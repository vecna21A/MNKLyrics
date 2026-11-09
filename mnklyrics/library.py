"""Lyrics Library: lyrics you write or pick (stored on this PC), songs that had no lyrics, and sharing
your synced lyrics with the MNKLyrics community (see community.py)."""

import json
import os
import threading
import time
import uuid

import requests
from PySide6.QtCore import QObject, Signal

from .config import APP_NAME, UPDATE_REPO, app_dir
from .lyrics import API, LyricLine, Lyrics, _norm, _similar, artists, clean_title, parse_lrc

HEADERS = {"User-Agent": f"{APP_NAME} (https://github.com/{UPDATE_REPO})"}
MISSING_MAX = 60


def fmt_ms(ms) -> str:
    """12345 -> "00:12.34" (LRC time)."""
    ms = max(0, int(round(ms or 0)))
    return f"{ms // 60000:02d}:{(ms % 60000) / 1000:05.2f}"


def parse_time(text: str):
    """"1:02.5", "01:02.50", "62.5" -> milliseconds, or None."""
    text = (text or "").strip().strip("[]")
    try:
        if ":" in text:
            m, s = text.split(":", 1)
            return (int(m) * 60 + float(s)) * 1000.0
        return float(text) * 1000.0
    except ValueError:
        return None


def to_lrc(lines) -> str:
    return "\n".join(f"[{fmt_ms(t)}] {txt}".rstrip() for t, txt in lines if t is not None)


def to_plain(lines) -> str:
    return "\n".join(txt for _t, txt in lines)


def same_song(entry, title, artist, duration_s=None) -> bool:
    if _norm(clean_title(entry.get("title", ""))) != _norm(clean_title(title or "")):
        return False
    if max((_similar(a, b) for a in artists(entry.get("artist", "")) for b in artists(artist or "")),
           default=0.0) < 0.75:
        return False
    d1, d2 = entry.get("duration") or 0, duration_s or 0
    return not (d1 and d2 and abs(d1 - d2) > 4)


class LyricsLibrary(QObject):
    """Everything stored on this PC: your lyrics and the "missing lyrics" list."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.path = os.path.join(app_dir(), "library.json")
        self.entries = []    # {"id","title","artist","album","duration","lines":[[ms|None,text]],"kind",...}
        self.missing = []    # {"title","artist","album","duration","seen"}
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.entries = [e for e in data.get("entries", []) if isinstance(e, dict)]
            self.missing = [m for m in data.get("missing", []) if isinstance(m, dict)]
        except (OSError, ValueError):
            pass

    def save(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"entries": self.entries, "missing": self.missing}, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass
        self.changed.emit()

    # ------------------------------------------------------------- entries
    def get(self, entry_id):
        return next((e for e in self.entries if e["id"] == entry_id), None)

    def find(self, track):
        """Your lyrics for this song, if you wrote or picked some."""
        if track is None:
            return None
        dur = track.duration_ms / 1000 if track.duration_ms else None
        for e in self.entries:
            if same_song(e, track.title, track.artist, dur):
                return e
        return None

    def put(self, entry):
        """Add or update (by id). Returns the stored entry."""
        entry = dict(entry)
        entry.setdefault("id", uuid.uuid4().hex)
        entry.setdefault("created", time.time())
        entry["updated"] = time.time()
        for i, e in enumerate(self.entries):
            if e["id"] == entry["id"]:
                self.entries[i] = entry
                break
        else:
            # one set of lyrics per song: replace an older one for the same song
            self.entries = [e for e in self.entries
                            if not same_song(e, entry["title"], entry["artist"], entry.get("duration"))]
            self.entries.insert(0, entry)
        self.missing = [m for m in self.missing
                        if not same_song(m, entry["title"], entry["artist"], entry.get("duration"))]
        self.save()
        return entry

    def delete(self, entry_id):
        self.entries = [e for e in self.entries if e["id"] != entry_id]
        self.save()

    def mark_shared(self, entry_id, community_id=None):
        e = self.get(entry_id)
        if e is not None:
            e["shared_at"] = time.time()
            if community_id:
                e["community_id"] = community_id
            self.save()

    def mark_unshared(self, entry_id):
        e = self.get(entry_id)
        if e is not None:
            e.pop("shared_at", None)
            e.pop("community_id", None)
            self.save()

    @staticmethod
    def to_lyrics(entry) -> Lyrics:
        lines = [(t, txt) for t, txt in entry.get("lines", [])]
        synced = bool(lines) and all(t is not None for t, _ in lines)
        out = [LyricLine(float(t or 0.0), txt) for t, txt in lines]
        if entry.get("kind", "own") == "fav":
            who = entry.get("author") or "a listener"
            source = f"★ Community favorite · synced by {who}"
        else:
            source = "Your lyrics"
        return Lyrics(out, synced=synced, source=source)

    def favorite_of(self, title, artist):
        """The ★ favorite chosen for this song, if any."""
        return next((e for e in self.entries if e.get("kind") == "fav"
                     and same_song(e, title, artist)), None)

    # ------------------------------------------------------------- missing
    def note_missing(self, track):
        if track is None or not track.title:
            return
        dur = round(track.duration_ms / 1000) if track.duration_ms else 0
        self.missing = [m for m in self.missing if not same_song(m, track.title, track.artist, dur)]
        self.missing.insert(0, {"title": track.title, "artist": track.artist, "album": track.album,
                                "duration": dur, "seen": time.time()})
        del self.missing[MISSING_MAX:]
        self.save()

    def forget_missing(self, title, artist):
        self.missing = [m for m in self.missing if not same_song(m, title, artist)]
        self.save()


# ------------------------------------------------------------------ LRCLIB
def search_lrclib(query: str):
    r = requests.get(f"{API}/search", params={"q": query}, headers=HEADERS, timeout=15)
    r.raise_for_status()
    out = []
    for it in r.json() or []:
        if not (it.get("syncedLyrics") or it.get("plainLyrics") or it.get("instrumental")):
            continue
        out.append({k: it.get(k) for k in ("id", "trackName", "artistName", "albumName", "duration",
                                           "instrumental", "syncedLyrics", "plainLyrics")})
    return out


class LibraryClient(QObject):
    """Background work for the Lyrics Library: the community feed, sharing, and finding a song's words."""

    search_done = Signal(int, object, str)       # request id, LRCLIB results or None, error text
    feed_done = Signal(int, object, str)         # request id, community rows or None, error text
    call_done = Signal(int, object, str)         # request id, result or None, error text (see call())
    publish_progress = Signal(str, str)          # entry id, text
    publish_done = Signal(str, bool, str)        # entry id, ok, message

    def __init__(self):
        super().__init__()
        self._req = 0

    def _next(self):
        self._req += 1
        return self._req

    def search(self, query: str) -> int:
        """LRCLIB search — only used to pull a song's words into the editor so they can be synced."""
        req = self._next()

        def run():
            try:
                self.search_done.emit(req, search_lrclib(query), "")
            except (requests.RequestException, ValueError):
                self.search_done.emit(req, None, "Couldn't look the words up right now. Try again in a moment.")
        threading.Thread(target=run, daemon=True, name="library-search").start()
        return req

    def feed(self, query: str = "") -> int:
        req = self._next()

        def run():
            from . import community
            try:
                self.feed_done.emit(req, community.feed(query), "")
            except (requests.RequestException, ValueError):
                self.feed_done.emit(req, None, "Couldn't reach the community library. Check your internet.")
        threading.Thread(target=run, daemon=True, name="community-feed").start()
        return req

    def call(self, fn, *args) -> int:
        """Run a community lookup (e.g. community.songs) in the background; answers on call_done."""
        req = self._next()

        def run():
            try:
                self.call_done.emit(req, fn(*args), "")
            except (requests.RequestException, ValueError):
                self.call_done.emit(req, None, "Couldn't reach the community library. Check your internet.")
        threading.Thread(target=run, daemon=True, name="community-call").start()
        return req

    def publish(self, entry, account):
        """Share as the signed-in Discord account. publish_done's message carries the new row id on success
        as "id:<n>|<text>"."""
        eid = entry["id"]

        def run():
            from . import community
            self.publish_progress.emit(eid, "Sharing…")
            try:
                row_id = community.publish(entry["title"], entry["artist"], entry.get("album", ""),
                                           entry["duration"], to_lrc(entry["lines"]), account.access_token())
                self.publish_done.emit(eid, True, f"id:{row_id or ''}|Shared as @{account.username}! Listeners "
                                                  "can now find them in the community library.")
            except RuntimeError as e:
                self.publish_done.emit(eid, False, str(e))
        threading.Thread(target=run, daemon=True, name="community-publish").start()

    def unpublish(self, entry, account):
        eid = entry["id"]

        def run():
            from . import community
            try:
                community.unpublish(entry["community_id"], account.access_token())
                self.publish_done.emit(eid, True, "unshared|Removed from the community library.")
            except (RuntimeError, KeyError, ValueError) as e:
                self.publish_done.emit(eid, False, str(e) or "Couldn't remove it.")
        threading.Thread(target=run, daemon=True, name="community-unpublish").start()


def lines_from_text(text: str):
    """Editor text -> [[ms|None, text]]. Accepts plain lines or LRC ("[01:02.30] line")."""
    if "[" in text and parse_lrc(text):
        return [[l.time, l.text] for l in parse_lrc(text)]
    out = []
    for raw in text.splitlines():
        t = raw.strip()
        if not t and (not out or not out[-1][1]):
            continue  # one empty line marks a music-only break; more are ignored
        out.append([None, t])
    while out and not out[-1][1]:
        out.pop()
    return out
