"""Playlists: save songs once, then pick any of them right from the overlay.

Since February 2026 Spotify doesn't let apps read the songs of other people's playlists (only their name),
so MNKLyrics works with song links instead: in Spotify, open a playlist, press Ctrl+A then Ctrl+C — that
copies a link for every song — and paste them into MNKLyrics. Each song's name and cover come from
Spotify's public link preview (no key, no login), which answers only so many lookups at a time, so
names fill in in the background. Choosing a song plays it in the Spotify app (see spotify_starter.py).
"""

import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests
from PySide6.QtCore import QObject, QTimer, Signal

from .config import APP_NAME, app_dir

_TRACK = re.compile(r"(?:open\.spotify\.com/(?:intl-[a-z-]+/)?track/|spotify:track:)([A-Za-z0-9]{22})")
_PLAYLIST = re.compile(r"(?:open\.spotify\.com/(?:intl-[a-z-]+/)?playlist/|spotify:playlist:)([A-Za-z0-9]{10,40})")
MAX_SONGS = 1000


def enabled() -> bool:
    return True


def track_ids(text: str):
    """Every Spotify song link in the pasted text, in order, without repeats."""
    seen, out = set(), []
    for tid in _TRACK.findall(text or ""):
        if tid not in seen:
            seen.add(tid)
            out.append(tid)
    return out[:MAX_SONGS]


def playlist_id(text: str):
    m = _PLAYLIST.search(text or "")
    return m.group(1) if m else None


def song_info(tid: str):
    """(info, status) from Spotify's public link preview. status: "ok", "limited" (asked too often —
    try later), "missing" (the song doesn't exist any more) or "error" (offline / other)."""
    try:
        r = requests.get("https://open.spotify.com/oembed", timeout=10, headers={"User-Agent": APP_NAME},
                         params={"url": f"https://open.spotify.com/track/{tid}"})
    except requests.RequestException:
        return None, "error"
    if r.status_code == 429:
        return None, "limited"
    if r.status_code in (400, 404):
        return None, "missing"
    if not r.ok:
        return None, "error"
    try:
        d = r.json()
    except ValueError:
        return None, "error"
    name = (d.get("title") or "").strip()
    if not name:
        return None, "missing"
    return {"name": name, "image": d.get("thumbnail_url") or ""}, "ok"


class NameLoader(QObject):
    """Looks song names up in the background. Spotify answers ~180 lookups and then refuses for a while,
    so it waits whenever that happens and carries on by itself — no song is skipped."""

    found = Signal(str, object)      # track id, {"name","image"} or None if the song no longer exists
    waiting = Signal(int)            # seconds until it tries again (0 = working)
    PAUSE = 300         # first wait after Spotify says "too many" (its pause lasts ~4.5 min)
    EXTRA_WAIT = 120    # if it still says no
    OFFLINE_WAIT = 20   # no internet / other hiccup

    def __init__(self):
        super().__init__()
        self._want = []
        self._lock = threading.Lock()
        self._thread = None
        self.backoff = self.PAUSE

    def want(self, ids):
        with self._lock:
            known = set(self._want)
            self._want += [i for i in ids if i not in known]
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True, name="song-names")
            self._thread.start()

    def _take(self, n):
        with self._lock:
            batch, self._want = self._want[:n], self._want[n:]
        return batch

    def _put_back(self, ids):
        with self._lock:
            self._want = ids + self._want

    def _run(self):
        while True:
            batch = self._take(4)
            if not batch:
                return
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(song_info, batch))
            retry = []
            for tid, (info, status) in zip(batch, results):
                if status == "ok":
                    self.found.emit(tid, info)
                elif status == "missing":
                    self.found.emit(tid, None)
                else:
                    retry.append(tid)
            if retry:
                self._put_back(retry)
                limited = any(s == "limited" for _i, s in results)
                # Spotify's pause lasts ~4.5 minutes: wait it out in one go, then a bit more if needed
                wait = self.backoff if limited else self.OFFLINE_WAIT
                self.waiting.emit(wait)
                time.sleep(wait)
                if limited:
                    self.backoff = self.EXTRA_WAIT
                self.waiting.emit(0)
            else:
                self.backoff = self.PAUSE
                time.sleep(0.15)  # gentle pace


def play(track_id: str):
    """Start the song in the Spotify app, in the background (see spotify_starter.py)."""
    from . import spotify_starter
    spotify_starter.play(track_id)


class PlaylistStore(QObject):
    """Saved playlists (kept on this PC) and which one the overlay's song picker shows."""

    changed = Signal()
    loaded = Signal(bool, str)        # ok, message (after adding songs)
    waiting = Signal(int)             # song-name lookups paused for this many seconds (0 = going again)
    names_changed = Signal()          # more song names arrived (lists keep their place, just relabel)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.path = os.path.join(app_dir(), "playlists.json")
        self.names_path = os.path.join(app_dir(), "song_names.json")
        self.names = self._load_names()       # track id -> {"name","image"}, shared by all lists
        self.loader = NameLoader()
        self.loader.found.connect(self._on_name)
        self.loader.waiting.connect(self.waiting)
        self._save_soon = QTimer(self)
        self._save_soon.setSingleShot(True)
        self._save_soon.setInterval(800)
        self._save_soon.timeout.connect(self._flush_names)
        self.items = []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                self.items = [p for p in json.load(fh) if isinstance(p, dict) and p.get("tracks")]
        except (OSError, ValueError):
            pass
        QTimer.singleShot(1500, self._fetch_missing)   # finish names that were still loading last time

    def save(self, emit=True):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.items, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass
        if emit:
            self.changed.emit()

    def get(self, pid):
        return next((p for p in self.items if p["id"] == pid), None)

    @property
    def active(self):
        """The playlist the overlay's song picker shows (the chosen one, else the first)."""
        return self.get(self.settings.get("general.active_playlist")) or (self.items[0] if self.items else None)

    def set_active(self, pid):
        self.settings.set("general.active_playlist", pid)
        self.changed.emit()

    def add_songs(self, text, name) -> bool:
        """Save a new list from pasted song links (Ctrl+A, Ctrl+C in a Spotify playlist). The list is ready
        right away; song names fill in in the background (songs can be played before their name shows)."""
        ids = track_ids(text)
        if not ids:
            if playlist_id(text):
                msg = ("Spotify no longer lets apps read playlist links. Open the playlist in Spotify, click a "
                       "song, press Ctrl+A then Ctrl+C, and paste again.")
            else:
                msg = "No Spotify songs in what you pasted. In Spotify, select songs and press Ctrl+C first."
            self.loaded.emit(False, msg)
            return False
        tracks = []
        for tid in ids:
            known = self.names.get(tid) or {}
            tracks.append({"id": tid, "name": known.get("name", ""), "artists": known.get("artists", ""),
                           "duration_ms": known.get("duration_ms", 0), "image": known.get("image", "")})
        data = {"id": "list-" + uuid.uuid4().hex[:10], "name": (name or "My songs")[:60], "owner": "",
                "image": next((t["image"] for t in tracks if t["image"]), ""), "tracks": tracks,
                "updated": time.time()}
        self.items.insert(0, data)
        if not self.settings.get("general.active_playlist") or self.active is None:
            self.settings.set("general.active_playlist", data["id"])
        self.save()
        self._fetch_missing()
        self.loaded.emit(True, f"“{data['name']}”: {len(tracks)} songs")
        return True

    # ------------------------------------------------------------- song names
    def _load_names(self):
        try:
            with open(self.names_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_names(self):
        tmp = self.names_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.names, fh, ensure_ascii=False)
            os.replace(tmp, self.names_path)
        except OSError:
            pass

    def _fetch_missing(self):
        missing = [t["id"] for p in self.items for t in p["tracks"] if not t.get("name")]
        if missing:
            self.loader.want(missing)

    def _on_name(self, tid, info):
        self.names[tid] = info or {"name": "Song not available", "image": ""}
        for p in self.items:
            for t in p["tracks"]:
                if t["id"] == tid:
                    t["name"] = self.names[tid]["name"]
                    t["image"] = self.names[tid].get("image", "")
                    if not p.get("image"):
                        p["image"] = t["image"]
        self._save_soon.start()

    def _flush_names(self):
        self._save_names()
        self.save(emit=False)
        self.names_changed.emit()

    def learn(self, track):
        """When a saved song plays, Spotify tells us its artist and length — remember them."""
        if track is None or not track.title:
            return
        key = " ".join(re.sub(r"[^\w]+", " ", track.title.lower()).split())
        changed = False
        for p in self.items:
            for t in p["tracks"]:
                name = " ".join(re.sub(r"[^\w]+", " ", (t.get("name") or "").lower()).split())
                if name and name == key and not t.get("artists"):
                    t["artists"] = track.artist or ""
                    t["duration_ms"] = int(track.duration_ms or 0)
                    known = self.names.setdefault(t["id"], {"name": t["name"], "image": t.get("image", "")})
                    known["artists"], known["duration_ms"] = t["artists"], t["duration_ms"]
                    changed = True
        if changed:
            self._save_soon.start()

    def progress(self):
        """(names known, songs) over all saved lists."""
        tracks = [t for p in self.items for t in p["tracks"]]
        return sum(1 for t in tracks if t.get("name")), len(tracks)

    def rename(self, pid, name):
        pl = self.get(pid)
        if pl is not None and name.strip():
            pl["name"] = name.strip()[:60]
            self.save()

    def remove(self, pid):
        self.items = [p for p in self.items if p["id"] != pid]
        if self.settings.get("general.active_playlist") == pid:
            self.settings.set("general.active_playlist", self.items[0]["id"] if self.items else "")
        self.save()
