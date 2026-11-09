"""Discord status: shows "Listening to MNKLyrics" with the current song on the user's Discord profile.

MNKLyrics only talks to the Discord app running on this PC, through Discord's local Rich Presence pipe.
Nothing is sent to any server by MNKLyrics; Discord then shows the status to the user's friends.
"""

import json
import os
import queue
import re
import struct
import threading
import time
import uuid

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QImage

from .config import APP_NAME, DISCORD_CLIENT_ID, UPDATE_REPO

OP_HANDSHAKE, OP_FRAME, OP_CLOSE = 0, 1, 2
ACTIVITY_LISTENING = 2
MIN_GAP = 4.5          # Discord allows about 5 status updates per 20 seconds
RETRY_EVERY = 15.0     # look for Discord again this often while it isn't running
# The logo shown next to the status: the one on the GitHub page (Discord fetches it through its own proxy)
LOGO_URL = f"https://raw.githubusercontent.com/{UPDATE_REPO}/main/logo.png"


class _Pipe:
    """Discord's local IPC: frames of <opcode:uint32><length:uint32><json> over a named pipe."""

    def __init__(self):
        self.f = None

    def connect(self, client_id) -> bool:
        for i in range(10):
            try:
                self.f = open(rf"\\?\pipe\discord-ipc-{i}", "r+b", buffering=0)
            except OSError:
                continue
            try:
                self.send(OP_HANDSHAKE, {"v": 1, "client_id": client_id})
                op, data = self.recv()
                if op == OP_FRAME and data.get("evt") == "READY":
                    return True
            except (OSError, ValueError, struct.error):
                pass
            self.close()
        return False

    def send(self, op, payload):
        body = json.dumps(payload).encode("utf-8")
        self.f.write(struct.pack("<II", op, len(body)) + body)

    def recv(self):
        op, n = struct.unpack("<II", self._read(8))
        return op, json.loads(self._read(n).decode("utf-8") or "{}")

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.f.read(n - len(buf))
            if not chunk:
                raise OSError("Discord closed the connection")
            buf += chunk
        return buf

    def close(self):
        if self.f is not None:
            try:
                self.f.close()
            except OSError:
                pass
        self.f = None


class _Worker(threading.Thread):
    """Owns the pipe. Always sends the newest wanted status, spaced out to respect Discord's limits."""

    def __init__(self, client_id):
        super().__init__(daemon=True, name="discord-status")
        self.client_id = client_id
        self.wanted = queue.Queue()
        self.pipe = _Pipe()
        self.connected = False
        self.last_send = 0.0
        self.next_try = 0.0

    def run(self):
        current = None      # last status Discord accepted
        target = None       # newest status we want shown (None = nothing)
        while True:
            try:
                target = self.wanted.get(timeout=1.0)
                while True:  # only the newest request matters
                    target = self.wanted.get_nowait()
            except queue.Empty:
                pass
            if target == "quit":
                self._send(None)
                self.pipe.close()
                return
            if target == current:
                continue
            if not self.connected:
                if target is None or time.monotonic() < self.next_try:
                    if target is None:
                        current = None
                    continue
                self.connected = self.pipe.connect(self.client_id)
                if not self.connected:
                    self.next_try = time.monotonic() + RETRY_EVERY
                    continue
            wait = self.last_send + MIN_GAP - time.monotonic()
            if wait > 0:
                time.sleep(wait)
                try:  # something newer may have arrived meanwhile
                    while True:
                        target = self.wanted.get_nowait()
                except queue.Empty:
                    pass
                if target == "quit":
                    continue
            if self._send(target):
                current = target

    def _send(self, activity) -> bool:
        if not self.connected:
            return False
        try:
            self.pipe.send(OP_FRAME, {"cmd": "SET_ACTIVITY", "nonce": str(uuid.uuid4()),
                                      "args": {"pid": os.getpid(), "activity": activity}})
            self.pipe.recv()
            self.last_send = time.monotonic()
            return True
        except (OSError, ValueError, struct.error):
            self.pipe.close()
            self.connected = False
            self.next_try = time.monotonic() + 2.0
            return False


def _norm(s):
    """Lowercase, without brackets ("(Remastered)", "[Live]"), features and punctuation, for matching."""
    s = re.sub(r"[\(\[].*?[\)\]]", " ", (s or "").lower())
    s = re.split(r"\s+(?:feat\.?|ft\.?|featuring)\s+|\s+-\s+", s)[0]
    return re.sub(r"[^\w]+", " ", s).strip()


def _score(title, artist, album, c_title, c_artist, c_album):
    """How well a search result matches the song; 0 when it's by another artist (a cover version,
    a karaoke track or a different song with the same name)."""
    t, a = _norm(title), _norm(artist)
    ct, ca = _norm(c_title), _norm(c_artist)
    if a and not (ca and (a in ca or ca in a or a.split(" ")[0] == ca.split(" ")[0])):
        return 0
    s = 2 if a else 0
    if ct == t:
        s += 3
    elif t and (t in ct or ct in t):
        s += 1
    if album and _norm(album) == _norm(c_album):
        s += 2
    return s


SIG = 16           # covers are compared as 16×16 colour thumbnails
MATCH_LIMIT = 30.0  # average colour difference (0–255) below which two covers count as the same picture


def image_signature(data: bytes):
    """A tiny colour fingerprint of an image, for telling which cover is Spotify's. None if unreadable."""
    img = QImage.fromData(data)
    if img.isNull():
        return None
    img = img.convertToFormat(QImage.Format.Format_RGB32).scaled(
        SIG, SIG, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    out = []
    for y in range(SIG):
        for x in range(SIG):
            c = img.pixelColor(x, y)
            out += (c.red(), c.green(), c.blue())
    return out


def signature_distance(s1, s2):
    return sum(abs(p - q) for p, q in zip(s1, s2)) / len(s1)


def find_cover_candidates(title, artist, album=""):
    """Possible covers for the song as [(text score, big image url, small image url)], best text match first.
    Searches Apple's iTunes Search and Deezer (free, no key). Sends only the title and artist."""
    import requests
    headers = {"User-Agent": f"{APP_NAME} (+https://github.com/{UPDATE_REPO})"}
    found = []
    try:
        r = requests.get("https://itunes.apple.com/search", headers=headers, timeout=4, params={
            "term": f"{artist} {title}", "media": "music", "entity": "song", "limit": 15})
        for it in r.json().get("results", []):
            sc = _score(title, artist, album, it.get("trackName"), it.get("artistName"), it.get("collectionName"))
            small = it.get("artworkUrl100")
            if sc >= 3 and small:
                found.append((sc, small.replace("100x100bb", "600x600bb"), small))
    except Exception:
        pass
    try:
        r = requests.get("https://api.deezer.com/search", headers=headers, timeout=4, params={
            "q": f"{artist} {title}", "limit": 10})
        for it in r.json().get("data", []):
            alb = it.get("album") or {}
            sc = _score(title, artist, album, it.get("title"), (it.get("artist") or {}).get("name"),
                        alb.get("title"))
            if sc >= 3 and alb.get("cover_xl") and alb.get("cover_medium"):
                found.append((sc, alb["cover_xl"], alb["cover_medium"]))
    except Exception:
        pass
    seen, out = set(), []
    for item in sorted(found, key=lambda f: -f[0]):
        if item[1] not in seen:
            seen.add(item[1])
            out.append(item)
    return out[:10]


def fetch_signatures(candidates):
    """[(text score, big url, fingerprint or None)] — downloads the small versions in parallel."""
    import requests
    from concurrent.futures import ThreadPoolExecutor

    def sig(item):
        try:
            r = requests.get(item[2], timeout=4)
            return item[0], item[1], image_signature(r.content) if r.ok else None
        except Exception:
            return item[0], item[1], None
    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(sig, candidates))


def pick_cover(candidates, artwork_sig):
    """The candidate that looks like Spotify's cover. Without Spotify's cover to compare with, the best text
    match. With it but no lookalike, "" — the MNKLyrics logo is better than the wrong picture."""
    if not candidates:
        return ""
    if artwork_sig is None:
        return candidates[0][1] if candidates[0][0] >= 5 else ""
    best, best_d = "", MATCH_LIMIT
    for _score_, url, s in candidates:
        if s is not None:
            d = signature_distance(s, artwork_sig)
            if d < best_d:
                best, best_d = url, d
    return best


class DiscordPresence(QObject):
    """Keeps the Discord status in step with what's playing. Off when the setting is off, when nothing is
    playing, or when Discord isn't running (it reconnects by itself once Discord opens)."""

    _candidates_found = Signal(object, object)  # track, [(text score, url, fingerprint)]
    COVER_WAIT = 5.0  # wait this long for the cover before showing the status without it

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.track = None
        self.playing = False
        self.position = 0.0
        self.covers = {}           # track key -> chosen cover url ("" = none)
        self._cands = {}           # track key -> candidates with fingerprints
        self._art = {}             # track key -> fingerprint of Spotify's own cover
        self._looking = {}         # track key -> when the lookup started
        self._track_at = 0.0
        self._candidates_found.connect(self._on_candidates)
        self.worker = None
        if DISCORD_CLIENT_ID:
            self.worker = _Worker(DISCORD_CLIENT_ID)
            self.worker.start()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(800)  # skipping through songs sends one update, not many
        self._debounce.timeout.connect(self._push)
        settings.changed.connect(self._on_setting)

    @property
    def available(self):
        return self.worker is not None

    def _on_setting(self, key):
        if key == "*" or key.startswith("general.discord_"):
            if self.track is not None:
                self._lookup_cover()
            self._debounce.start()

    def update(self, track, playing, position_ms):
        """Call on song change, play/pause and seeks."""
        changed = (track != self.track or playing != self.playing
                   or abs(position_ms - self.position) > 3000)
        if track != self.track:
            self._track_at = time.monotonic()
        self.track, self.playing, self.position = track, playing, position_ms
        self._position_at = time.time()
        if changed:
            self._lookup_cover()
            self._debounce.start()

    def set_artwork(self, data: bytes):
        """Spotify's own cover for the current song (from Windows' media info)."""
        if self.track is None or not data:
            return
        sig = image_signature(data)
        if sig is not None:
            self._art[self.track.key] = sig
            self._choose(self.track.key)

    # ------------------------------------------------------------ cover art
    def _cover_wanted(self):
        g = self.settings.data["general"]
        return (self.worker is not None and g.get("discord_status", True)
                and self.track is not None and bool(self.track.title))

    def _lookup_cover(self):
        t = self.track
        if not self._cover_wanted() or t.key in self._cands or t.key in self._looking:
            return
        self._looking[t.key] = time.monotonic()

        def run():
            self._candidates_found.emit(t, fetch_signatures(find_cover_candidates(t.title, t.artist, t.album)))
        threading.Thread(target=run, daemon=True, name="cover-lookup").start()

    def _on_candidates(self, track, cands):
        self._looking.pop(track.key, None)
        self._cands[track.key] = cands
        for d in (self._cands, self._art, self.covers):  # keep memory small on very long sessions
            while len(d) > 200:
                d.pop(next(iter(d)))
        self._choose(track.key)

    def _choose(self, key):
        """(Re)pick the cover once candidates and/or Spotify's cover are in; update Discord if it changed."""
        if key not in self._cands:
            return
        url = pick_cover(self._cands[key], self._art.get(key))
        if self.covers.get(key) != url:
            self.covers[key] = url
            if self.track is not None and key == self.track.key:
                self._debounce.start()

    def activity(self):
        g = self.settings.data["general"]
        if not g.get("discord_status", True) or self.track is None or not self.playing:
            return None
        t = self.track
        act = {"type": ACTIVITY_LISTENING, "status_display_type": 0,
               "assets": {"large_image": LOGO_URL, "large_text": f"{APP_NAME} · real-time lyrics"}}
        if t.title:
            act["details"] = t.title[:128]
            if t.artist:
                act["state"] = f"by {t.artist}"[:128]
            cover = self.covers.get(t.key)
            if cover:  # the song's cover, with the MNKLyrics logo as the round badge on its corner
                act["assets"] = {"large_image": cover, "large_text": (t.album or t.title)[:128],
                                 "small_image": LOGO_URL, "small_text": APP_NAME}
            elif t.album:
                act["assets"]["large_text"] = t.album[:128]
            if t.duration_ms > 0:
                start = int(getattr(self, "_position_at", time.time()) * 1000 - self.position)
                act["timestamps"] = {"start": start, "end": start + int(t.duration_ms)}
        else:
            act["details"] = "Real-time lyrics"
        if UPDATE_REPO:
            act["buttons"] = [{"label": "Get MNKLyrics",
                               "url": f"https://github.com/{UPDATE_REPO}/releases/latest"}]
        return act

    def _push(self):
        if self.worker is None:
            return
        t = self.track
        if t is not None and self._cover_wanted():
            waiting = t.key in self._looking or t.key not in self._art  # candidates or Spotify's cover
            if waiting and time.monotonic() - self._track_at < self.COVER_WAIT:
                self._debounce.start(300)  # the cover is on its way; show the status once, with it
                return
        self.worker.wanted.put(self.activity())

    def shutdown(self):
        if self.worker is not None:
            self.worker.wanted.put("quit")
            self.worker.join(timeout=1.5)
