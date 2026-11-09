"""MNKLyrics community lyrics: lyrics synced by MNKLyrics listeners, kept in MNKLyrics' own free
database (Supabase). Only lyrics people synced in MNKLyrics are here — no random uploads.

Community lyrics are never used on their own: in the Lyrics Library people search a song, test the
versions listeners synced, and ★ favorite one — from then on that song uses it (stored on their PC). The project URL and the public ("anon"/"publishable") key in
config.py are meant to be public: the database only allows reading and adding lyrics, never changing
or deleting them (see community/setup.sql).
"""

import datetime
import re

import requests

from .config import APP_NAME, COMMUNITY_KEY, COMMUNITY_URL, VERSION
from .lyrics import Lyrics, _norm, _similar, artists, clean_title, parse_lrc, primary_artist

TABLE = "community_lyrics"
FIELDS = "id,created_at,title,artist,album,duration,synced,author"


def enabled() -> bool:
    return bool(COMMUNITY_URL and COMMUNITY_KEY)


def _url():
    return f"{COMMUNITY_URL.rstrip('/')}/rest/v1/{TABLE}"


def _headers(extra=None):
    h = {"apikey": COMMUNITY_KEY, "User-Agent": f"{APP_NAME}/{VERSION}", "Accept": "application/json"}
    if COMMUNITY_KEY.startswith("eyJ"):  # older "anon" keys are JWTs and go in Authorization too
        h["Authorization"] = f"Bearer {COMMUNITY_KEY}"
    h.update(extra or {})
    return h


def song_keys(title, artist):
    """Search keys that ignore case, accents, "(feat. X)", "- Remastered" and extra artists."""
    return _norm(clean_title(title or ""))[:200], _norm(primary_artist(artist or ""))[:200]


def _same_artist(a, b):
    return max((_similar(x, y) for x in artists(a) for y in artists(b)), default=0.0) >= 0.7


def find(title, artist, duration_s=None):
    """The newest community lyrics for this song, or None. Raises requests errors when offline."""
    tk, _ak = song_keys(title, artist)
    if not tk:
        return None
    r = requests.get(_url(), headers=_headers(), timeout=6, params={
        "select": FIELDS, "title_key": f"eq.{tk}", "order": "created_at.desc", "limit": "25"})
    r.raise_for_status()
    for row in r.json() or []:
        if not _same_artist(artist, row.get("artist", "")):
            continue
        d = row.get("duration") or 0
        if duration_s and d and abs(float(d) - float(duration_s)) > 3:
            continue  # another version of the song (radio edit, live, sped up…)
        if parse_lrc(row.get("synced") or ""):
            return row
    return None


def versions(title, artist):
    """Every version listeners synced for this song (any length), newest first."""
    tk, _ak = song_keys(title, artist)
    if not tk:
        return []
    r = requests.get(_url(), headers=_headers(), timeout=10, params={
        "select": FIELDS, "title_key": f"eq.{tk}", "order": "created_at.desc", "limit": "100"})
    r.raise_for_status()
    return [row for row in r.json() or [] if _same_artist(artist, row.get("artist", ""))
            and parse_lrc(row.get("synced") or "")]


def songs(query="", limit=200):
    """Songs that have community lyrics, grouped: [{"title","artist","count","newest"}], newest first."""
    groups = {}
    for row in feed(query, limit):
        key = song_keys(row.get("title"), row.get("artist"))
        g = groups.get(key)
        if g is None:
            groups[key] = {"title": row.get("title"), "artist": row.get("artist"), "count": 1,
                           "newest": row.get("created_at")}
        else:
            g["count"] += 1
    return list(groups.values())


def to_lyrics(row) -> Lyrics:
    who = (row.get("author") or "").strip()
    return Lyrics(parse_lrc(row.get("synced") or ""), synced=True,
                  source="MNKLyrics community" + (f" · synced by {who}" if who else ""))


def feed(query="", limit=40):
    """Latest shared lyrics, newest first; ``query`` filters by song or artist."""
    params = {"select": FIELDS, "order": "created_at.desc", "limit": str(limit)}
    q = re.sub(r"[,()*%\\]", " ", query or "").strip()
    if q:
        params["or"] = f"(title.ilike.*{q}*,artist.ilike.*{q}*)"
    r = requests.get(_url(), headers=_headers(), params=params, timeout=10)
    r.raise_for_status()
    return r.json() or []


def publish(title, artist, album, duration_s, lrc, token):
    """Share synced lyrics as the signed-in Discord user (the database adds their username).
    Returns the new row id. Raises RuntimeError with a message people can read."""
    tk, ak = song_keys(title, artist)
    body = {"title": title[:200], "artist": artist[:200], "album": (album or "")[:200],
            "duration": round(float(duration_s), 1), "title_key": tk, "artist_key": ak,
            "synced": lrc, "app_version": VERSION[:16]}
    try:
        r = requests.post(_url(), json=body, timeout=15, params={"select": "id"},
                          headers=_headers({"Prefer": "return=representation", "Content-Type": "application/json",
                                            "Authorization": f"Bearer {token}"}))
    except requests.RequestException:
        raise RuntimeError("Couldn't reach the community library. Check your internet and try again.")
    if r.status_code == 409:
        raise RuntimeError("These exact lyrics are already in the community library.")
    if r.status_code in (401, 403):
        raise RuntimeError("Your account can't share right now. Try signing out and in again.")
    if r.status_code not in (200, 201):
        raise RuntimeError(f"The community library didn't accept them (error {r.status_code}). Try again later.")
    try:
        rows = r.json()
        return rows[0]["id"] if rows else None
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def unpublish(row_id, token):
    """Remove one of your own shares."""
    try:
        r = requests.delete(_url(), params={"id": f"eq.{int(row_id)}"}, timeout=15,
                            headers=_headers({"Authorization": f"Bearer {token}", "Prefer": "return=representation"}))
    except requests.RequestException:
        raise RuntimeError("Couldn't reach the community library. Check your internet and try again.")
    if not r.ok:
        raise RuntimeError(f"Couldn't remove it (error {r.status_code}).")
    try:
        if not r.json():
            raise RuntimeError("Only the person who shared it can remove it.")
    except ValueError:
        pass


def ago(iso) -> str:
    """"2026-10-21T10:00:00+00:00" -> "3 h ago"."""
    try:
        t = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        s = (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds()
    except ValueError:
        return ""
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    if s < 86400 * 30:
        d = int(s // 86400)
        return f"{d} day{'s' if d > 1 else ''} ago"
    return t.strftime("%d %b %Y")
