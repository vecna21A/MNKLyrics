"""Now-playing detection through the Windows media session (SMTC).

Spotify publishes its current track, timeline and playback state to Windows,
so no Spotify login or API key is needed.
"""

import asyncio
import datetime
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as SessionManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
    )
    from winrt.windows.storage.streams import Buffer, DataReader, InputStreamOptions
    HAS_WINRT = True
except Exception:  # pragma: no cover - only on systems without winrt
    HAS_WINRT = False


@dataclass(frozen=True)
class TrackInfo:
    title: str
    artist: str
    album: str
    duration_ms: float
    app_id: str

    @property
    def key(self):
        return (self.app_id, self.title, self.artist)


class MediaWatcher(QObject):
    track_changed = Signal(object)            # TrackInfo or None
    playback_updated = Signal(float, bool, float)  # position ms, playing, perf_counter timestamp
    artwork_changed = Signal(bytes)
    status_changed = Signal(str)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self._loop = None
        self._session = None
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="MediaWatcher", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop = True

    def command(self, name: str):
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._command(name), self._loop)

    # ------------------------------------------------------------------ thread
    def _run(self):
        if not HAS_WINRT:
            self.status_changed.emit("Windows media packages missing (pip install -r requirements.txt)")
            return
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as exc:  # keep the app alive even if WinRT breaks
            self.status_changed.emit(f"Media watcher stopped: {exc}")

    async def _command(self, name: str):
        session = self._session
        if session is None:
            return
        try:
            if name == "toggle":
                await session.try_toggle_play_pause_async()
            elif name == "next":
                await session.try_skip_next_async()
            elif name == "previous":
                await session.try_skip_previous_async()
            elif name == "play":
                await session.try_play_async()
            elif name.startswith("seek:"):  # "seek:<milliseconds>" (used by the lyrics editor)
                ticks = int(float(name[5:]) * 10_000)  # Windows counts in 100-nanosecond steps
                await session.try_change_playback_position_async(max(0, ticks))
        except Exception:
            pass

    def _pick_session(self, manager):
        sessions = list(manager.get_sessions())
        if self.settings.get("general.player") == "spotify":
            for s in sessions:
                if "spotify" in (s.source_app_user_model_id or "").lower():
                    return s
            return None
        current = manager.get_current_session()
        if current is not None:
            return current
        return sessions[0] if sessions else None

    async def _read_thumbnail(self, props):
        try:
            ref = props.thumbnail
            if ref is None:
                return b""
            stream = await ref.open_read_async()
            size = int(stream.size)
            if size <= 0:
                return b""
            buf = Buffer(size)
            await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
            try:
                return bytes(memoryview(buf))
            except TypeError:
                reader = DataReader.from_buffer(buf)
                data = bytearray(buf.length)
                reader.read_bytes(data)
                return bytes(data)
        except Exception:
            return b""

    async def _main(self):
        manager = await SessionManager.request_async()
        last_key = None
        art_retry_at = 0.0
        art_tries = 0
        self.status_changed.emit("Waiting for Spotify…")
        while not self._stop:
            try:
                session = self._pick_session(manager)
                self._session = session
                if session is None:
                    if last_key is not None:
                        last_key = None
                        self.track_changed.emit(None)
                    await asyncio.sleep(0.6)
                    continue

                props = await session.try_get_media_properties_async()
                timeline = session.get_timeline_properties()
                playback = session.get_playback_info()

                title = (props.title or "").strip()
                artist = (props.artist or props.album_artist or "").strip()
                album = (props.album_title or "").strip()
                duration = (timeline.end_time - timeline.start_time).total_seconds() * 1000.0
                track = TrackInfo(title, artist, album, max(0.0, duration), session.source_app_user_model_id or "")

                if title and track.key != last_key:
                    last_key = track.key
                    self.track_changed.emit(track)
                    art_tries = 0
                    art_retry_at = 0.0

                # Spotify updates the artwork slightly after the title, so retry a few times.
                if last_key is not None and art_tries < 3 and time.perf_counter() >= art_retry_at:
                    art_tries += 1
                    art_retry_at = time.perf_counter() + 1.2
                    data = await self._read_thumbnail(props)
                    if data:
                        self.artwork_changed.emit(data)

                playing = playback.playback_status == PlaybackStatus.PLAYING
                position = timeline.position.total_seconds() * 1000.0
                if playing:
                    updated = timeline.last_updated_time
                    if updated is not None and updated.year > 2000:
                        now = datetime.datetime.now(datetime.timezone.utc)
                        elapsed = (now - updated).total_seconds() * 1000.0
                        if 0 <= elapsed < 6 * 3600 * 1000:
                            position += elapsed
                if duration > 0:
                    position = min(position, duration)
                self.playback_updated.emit(max(0.0, position), playing, time.perf_counter())
            except Exception:
                await asyncio.sleep(1.0)
            await asyncio.sleep(0.2)


class PlaybackClock:
    """Smoothly extrapolates the playback position between media-session updates."""

    def __init__(self):
        self.base_pos = 0.0
        self.base_t = time.perf_counter()
        self.playing = False

    def reset(self):
        self.base_pos = 0.0
        self.base_t = time.perf_counter()

    def sync(self, pos: float, playing: bool, stamp: float):
        predicted = self.base_pos + ((stamp - self.base_t) * 1000.0 if self.playing else 0.0)
        drift = pos - predicted
        if playing != self.playing or abs(drift) > 350:
            self.base_pos, self.base_t, self.playing = pos, stamp, playing
        elif playing:
            # absorb small jitter gradually instead of jumping
            self.base_pos = predicted + drift * 0.12
            self.base_t = stamp

    def position(self) -> float:
        if self.playing:
            return self.base_pos + (time.perf_counter() - self.base_t) * 1000.0
        return self.base_pos
