"""Lyrics lookup (LRCLIB, free & keyless), LRC parsing and on-disk cache."""

import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import requests
from PySide6.QtCore import QObject, Signal

from .config import cache_dir

API = "https://lrclib.net/api"
HEADERS = {"User-Agent": "MNKLyrics/1.0 (desktop lyrics overlay)"}
NEGATIVE_CACHE_SECONDS = 10 * 60  # look again soon: someone may have just shared lyrics for the song


@dataclass
class LyricLine:
    time: float  # milliseconds
    text: str


@dataclass
class Lyrics:
    lines: list = field(default_factory=list)
    synced: bool = True
    instrumental: bool = False
    source: str = "LRCLIB"
    version: int = 0    # which candidate is shown
    versions: int = 1   # how many usable candidates exist for this song


_TS = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]")
_OFFSET = re.compile(r"\[offset:\s*([+-]?\d+)\]", re.I)


def parse_lrc(text: str) -> list:
    offset = 0
    m = _OFFSET.search(text)
    if m:
        offset = int(m.group(1))
    lines = []
    for raw in text.splitlines():
        stamps = list(_TS.finditer(raw))
        if not stamps:
            continue
        lyric = _TS.sub("", raw).strip()
        for s in stamps:
            seconds = float(s.group(2).replace(":", "."))
            ms = (int(s.group(1)) * 60 + seconds) * 1000.0 - offset
            lines.append(LyricLine(max(0.0, ms), lyric))
    lines.sort(key=lambda l: l.time)
    return lines


_EXTRA = (r"remaster(?:ed)?|version|ver\.?|edit|live|mono|stereo|mix|deluxe|bonus|sped\s*up|slowed|reverb|"
          r"nightcore|acoustic|radio|single|explicit|clean|instrumental|from\s|taylor'?s")


def clean_title(title: str) -> str:
    """The song's name without Spotify's extras: "(feat. X)", "- Remastered 2011", "(Sped Up)", "Twin Ver."."""
    t = re.sub(r"\s*[\(\[](feat|ft|with)\.?\s[^\)\]]*[\)\]]", "", title, flags=re.I)
    t = re.sub(r"\s+(feat|ft)\.?\s.*$", "", t, flags=re.I)
    t = re.sub(rf"\s+[-–—]\s+.*({_EXTRA}).*$", "", t, flags=re.I)
    t = re.sub(rf"\s*[\(\[][^\)\]]*({_EXTRA})[^\)\]]*[\)\]]", "", t, flags=re.I)
    return t.strip() or title


def artists(artist: str) -> list:
    """Every artist credited: "A, B & C feat. D" -> [A, B, C, D]."""
    parts = re.split(r",|&| x | and | feat\.? | ft\.? | with ", artist or "", flags=re.I)
    return [p.strip() for p in parts if p.strip()] or [artist]


def primary_artist(artist: str) -> str:
    return artists(artist)[0]


def _norm(text: str) -> str:
    """Comparable form of any script: lowercase, accents removed (ü→u, ş→s, ı→i), punctuation gone.
    Japanese, Korean, Cyrillic, Arabic... letters are kept, so those titles can match too."""
    t = unicodedata.normalize("NFKD", (text or "").replace("ı", "i").replace("İ", "I")).casefold()
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[\W_]+", " ", t).strip()


def _similar(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _artist_match(wanted: str, found: str) -> float:
    """Best match between any credited artist on either side."""
    return max((_similar(w, f) for w in artists(wanted) for f in artists(found)), default=0.0)


def _words(item: dict) -> list:
    text = item.get("syncedLyrics") or item.get("plainLyrics") or ""
    return re.findall(r"\w+(?:'\w+)?", _TS.sub(" ", text).casefold())


def _to_lyrics(item: dict):
    if not item:
        return None
    if item.get("instrumental"):
        return Lyrics([], synced=True, instrumental=True)
    synced = item.get("syncedLyrics")
    if synced:
        lines = parse_lrc(synced)
        if lines:
            return Lyrics(lines, synced=True)
    plain = item.get("plainLyrics")
    if plain:
        lines = [LyricLine(0.0, l.strip()) for l in plain.splitlines()]
        # collapse repeated blank lines
        out = []
        for l in lines:
            if not l.text and (not out or not out[-1].text):
                continue
            out.append(l)
        return Lyrics(out, synced=False)
    return None


class LyricsFetcher(QObject):
    finished = Signal(int, object)  # request id, Lyrics or None

    def __init__(self):
        super().__init__()
        self._req = 0
        self._lock = threading.Lock()

    def request(self, track, force: bool = False, version=None) -> int:
        """Fetch lyrics for ``track``. ``force`` re-downloads; ``version`` picks another candidate."""
        with self._lock:
            self._req += 1
            req = self._req
        threading.Thread(target=self._work, args=(req, track, force, version), daemon=True).start()
        return req

    # ------------------------------------------------------------- internals
    def _cache_path(self, track) -> str:
        key = f"v3|{track.artist.lower()}|{track.title.lower()}|{round(track.duration_ms / 1000)}"
        return os.path.join(cache_dir(), hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")

    def _work(self, req, track, force, version):
        # (community lyrics are only used once someone ★ favorites a version — see library.py)
        path = self._cache_path(track)
        cached = None
        if not force:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    cached = json.load(fh)
                if not cached.get("items") and time.time() - cached.get("ts", 0) >= NEGATIVE_CACHE_SECONDS:
                    cached = None  # retry songs that had no lyrics a while ago
            except (OSError, ValueError):
                cached = None
        if cached is None:
            try:
                cached = {"ts": time.time(), "items": self._candidates(track), "choice": 0}
            except requests.RequestException:
                self.finished.emit(req, None)
                return
        items = cached["items"]
        if not items:
            self._save(path, cached)
            self.finished.emit(req, None)
            return
        if version is not None:
            cached["choice"] = version % len(items)  # remembered for next time this song plays
        choice = min(cached.get("choice", 0), len(items) - 1)
        self._save(path, cached)
        lyrics = _to_lyrics(items[choice])
        if lyrics is not None:
            lyrics.version, lyrics.versions = choice, len(items)
        self.finished.emit(req, lyrics)

    @staticmethod
    def _save(path, data):
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except OSError:
            pass

    def _get(self, endpoint, params):
        """One LRCLIB request. Busy-server answers are retried; other failures just count as "no result",
        so one bad request never hides lyrics the other searches found."""
        for attempt in range(3):
            try:
                r = requests.get(f"{API}/{endpoint}", params=params, headers=HEADERS, timeout=12)
            except requests.RequestException:
                self._failed += 1
                return None
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(0.6 * (attempt + 1))
                continue
            if r.status_code == 404:
                self._answered += 1
                return None
            if not r.ok:
                self._failed += 1
                return None
            self._answered += 1
            try:
                return r.json()
            except ValueError:
                return None
        self._failed += 1
        return None

    def _candidates(self, track) -> list:
        """All usable LRCLIB uploads for this song, best first.

        LRCLIB is community-written, so one song often has several uploads with different
        typos and line breaks. Wrong songs, remixes and live cuts are filtered out by title,
        artist and length, then the remaining versions are ranked by how much their text agrees
        with each other: one-off transcription mistakes lose to the consensus.
        """
        from concurrent.futures import ThreadPoolExecutor
        duration = round(track.duration_ms / 1000) if track.duration_ms else None
        found = {}
        self._failed = self._answered = 0

        def add(item):
            if isinstance(item, dict) and (item.get("syncedLyrics") or item.get("plainLyrics")
                                           or item.get("instrumental")):
                found[item.get("id") or id(item)] = item

        title, artist = clean_title(track.title), primary_artist(track.artist)
        exact = {"track_name": track.title, "artist_name": track.artist}
        if track.album:
            exact["album_name"] = track.album
        if duration:
            exact["duration"] = duration
        requests_ = [("get-cached", exact)]
        if title != track.title or artist != track.artist:
            requests_.append(("get-cached", {**exact, "track_name": title, "artist_name": artist}))
        searches = [
            {"track_name": track.title, "artist_name": track.artist},
            {"track_name": title, "artist_name": artist},
            {"q": f"{title} {artist}"},
            {"track_name": title},  # artist written differently (another alphabet, "The ...")
        ]
        seen = set()
        for q in searches:
            sig = tuple(sorted(q.items()))
            if sig not in seen:
                seen.add(sig)
                requests_.append(("search", q))
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda r: self._get(*r), requests_))
        for (endpoint, _q), res in zip(requests_, results):
            if endpoint == "search":
                for cand in res or []:
                    add(cand)
            else:
                add(res)
        if not found and self._answered == 0 and self._failed:
            raise requests.ConnectionError("LRCLIB could not be reached")  # don't remember "no lyrics"

        def plausible(c):
            if _similar(clean_title(c.get("trackName", "")), title) < 0.75:
                return False
            if _artist_match(track.artist, c.get("artistName", "")) >= 0.6:
                return True
            # same title and the very same length: the artist is probably just spelled another way
            return bool(duration and c.get("duration") and abs(float(c["duration"]) - duration) <= 2
                        and _norm(clean_title(c.get("trackName", ""))) == _norm(title))

        title, artist = clean_title(track.title), primary_artist(track.artist)
        cands = [c for c in found.values() if plausible(c)]
        if duration:
            close = [c for c in cands if not c.get("duration") or abs(float(c["duration"]) - duration) <= 8]
            cands = close or cands
        if not cands:
            return []

        texts = {id(c): _words(c) for c in cands}
        # Identical re-uploads are copies, not independent votes: keep only the newest of each text.
        by_text = {}
        for c in sorted(cands, key=lambda c: c.get("id") or 0, reverse=True):
            by_text.setdefault((" ".join(texts[id(c)]), bool(c.get("syncedLyrics"))), c)
        cands = list(by_text.values())
        synced = [c for c in cands if c.get("syncedLyrics")]

        def consensus(c):
            others = [o for o in synced if o is not c]
            if not others or not texts[id(c)]:
                return 0.0
            return sum(SequenceMatcher(None, texts[id(c)], texts[id(o)], autojunk=False).ratio()
                       for o in others) / len(others)

        def score(c):
            s = 3.0 if c.get("syncedLyrics") else (1.0 if c.get("plainLyrics") else 0.5)
            if duration and c.get("duration"):
                diff = abs(float(c["duration"]) - duration)
                s += 2 if diff <= 2 else 1 if diff <= 5 else 0
            if _norm(clean_title(c.get("trackName", ""))) == _norm(title):
                s += 0.5
            return s + 4 * consensus(c)

        # LRCLIB uploads can't be edited, so fixes arrive as newer uploads (higher id):
        # when two versions score about the same, prefer the newer one.
        ranked = sorted(cands, key=lambda c: (round(score(c), 1), c.get("id") or 0), reverse=True)
        return [{k: c.get(k) for k in ("id", "trackName", "artistName", "duration", "instrumental",
                                        "syncedLyrics", "plainLyrics")} for c in ranked[:8]]
