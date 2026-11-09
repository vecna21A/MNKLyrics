"""User configs (saved overlay looks): save, rename, delete, duplicate, and share via code or file.

A config carries the creator's complete setup: the look, plus the overlay's size, its place on the
screen (stored as a spot on the screen, so it lands in the same place on any monitor size), and the
overlay options and player controls. Configs saved or shared before 1.7.1 only had the look; those
still only change the look.

Share codes look like ``MNK-4fK9x…``: a compact binary record (closest built-in look + only the
settings that differ from it) written with letters and digits only, so it stays short and survives
being pasted into Discord. Older ``MNK1-…`` codes are still accepted. Anything imported is treated
as untrusted: only known keys are kept, each value is type-checked and clamped.
"""

import base64
import datetime
import json
import re
import zlib

from PySide6.QtCore import QObject, Signal

from .config import CONFIG_KEYS, DEFAULTS, LAYOUT_KEYS, PRESET_KEYS, STYLE_PRESETS

CODE_PREFIX = "MNK-"       # compact v2 codes
LEGACY_PREFIX = "MNK1-"    # v1 codes (compressed JSON) — still imported
FILE_FORMAT = "mnklyrics-config"
FILE_EXT = ".mnkcfg"
MAX_CODE_CHARS = 20000
MAX_NAME = 40
MAX_AUTHOR = 32

RANGES = {
    "font_size": (12, 120), "font_weight": (100, 900), "letter_spacing": (-2.0, 12.0),
    "line_spacing": (1.0, 2.2), "context_scale": (0.4, 1.0), "lines_before": (0, 3), "lines_after": (0, 3),
    "context_opacity": (0.05, 1.0), "glow_strength": (0.0, 2.0), "glow_radius": (2, 48),
    "outline_width": (0.5, 6.0), "shadow_opacity": (0.0, 1.0), "shadow_offset": (0, 12),
    "bg_opacity": (0.0, 1.0), "bg_radius": (0, 48), "padding": (4, 48), "anim_speed": (0.3, 2.5),
    "width": (360, 2000), "margin": (0, 400), "opacity": (0.1, 1.0), "hover_opacity": (0.0, 1.0),
    "controls_size": (0.6, 1.8), "controls_opacity": (0.2, 1.0),
    "pos_fx": (-1, 1000), "pos_fy": (-1, 1000),
}
POS_KEYS = ("pos_fx", "pos_fy")  # a dragged position, as thousandths of the screen (-1 = not dragged)
EXTRA_DEFAULTS = {"pos_fx": -1, "pos_fy": -1}
FULL_KEYS = CONFIG_KEYS + POS_KEYS
CHOICES = {
    "align": ("left", "center", "right"),
    "highlight_style": ("karaoke", "color", "glow", "pill", "underline", "none"),
    "animation": ("slide", "bounce", "fade", "zoom", "none"),
    "entrance": ("pop", "rise", "typewriter", "none"),
    "position": ("top_left", "top_center", "top_right", "middle_left", "center", "middle_right",
                 "bottom_left", "bottom_center", "bottom_right", "custom"),
    "controls_position": ("below", "above", "left", "right"),
    "controls_visibility": ("always", "hover"),
    "controls_style": ("pill", "glass", "minimal"),
    "controls_playlist_visibility": ("always", "hover"),
}


def _default(key):
    return EXTRA_DEFAULTS[key] if key in EXTRA_DEFAULTS else DEFAULTS["overlay"][key]


def is_full(values) -> bool:
    """True for configs that carry the whole setup (made with 1.7.1+), not just the look."""
    return isinstance(values, dict) and "position" in values
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


class ConfigError(ValueError):
    pass


def clean_text(text, limit) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", "", str(text or "")).strip()
    return text[:limit]


def sanitize(values, full=None) -> dict:
    """Safe set of config values: unknown keys dropped, bad values replaced by defaults. ``full`` (the
    default when the values contain a position) keeps the whole setup, otherwise only the look."""
    out = {}
    src = values if isinstance(values, dict) else {}
    if full is None:
        full = is_full(src)
    for key in (FULL_KEYS if full else PRESET_KEYS):
        default = _default(key)
        v = src.get(key, default)
        if isinstance(default, bool):
            v = v if isinstance(v, bool) else default
        elif isinstance(default, (int, float)):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:  # reject bools and NaN
                v = default
            lo, hi = RANGES.get(key, (float("-inf"), float("inf")))
            v = min(max(v, lo), hi)
            v = int(round(v)) if isinstance(default, int) else float(v)
        elif key in CHOICES:
            v = v if v in CHOICES[key] else default
        elif key.endswith("_color"):
            v = v.upper() if isinstance(v, str) and _COLOR.match(v) else default
        elif key == "font_family":
            v = clean_text(v, 64) or default
        else:
            v = default
        out[key] = v
    return out


# ---------------------------------------------------------------- compact v2 share codes
# Frozen orders: codes already shared depend on them. Only ever APPEND new entries.
CODE_KEYS = (
    "font_family", "font_size", "font_weight", "italic", "uppercase", "letter_spacing",
    "line_spacing", "context_scale", "lines_before", "lines_after", "align",
    "text_color", "context_opacity", "highlight_style", "highlight_color",
    "glow_enabled", "glow_color", "glow_strength", "glow_radius",
    "outline_enabled", "outline_color", "outline_width",
    "shadow_enabled", "shadow_color", "shadow_opacity", "shadow_offset",
    "bg_enabled", "bg_color", "bg_opacity", "bg_radius", "bg_border", "padding",
    "animation", "entrance", "anim_speed",
    # 1.7.1+: the rest of the setup (codes that use these start with version 3)
    "width", "position", "margin", "opacity", "show_track_info", "show_progress", "hide_when_paused",
    "hide_no_lyrics", "no_lyrics_card", "fade_on_hover", "hover_opacity",
    "controls_enabled", "controls_playpause", "controls_prevnext", "controls_volume", "controls_position",
    "controls_visibility", "controls_style", "controls_size", "controls_color", "controls_opacity",
    "pos_fx", "pos_fy",
    "controls_playlist", "controls_playlist_visibility",   # 2.4+
)
STYLE_CODE_KEYS = CODE_KEYS[:35]
# (index 1 was called "Spotify Classic" before 1.3.1 — renamed, same position, so old codes still decode)
CODE_BASES = (None, "Classic Green", "Neon Karaoke", "Cinema Subtitles", "Minimal Glass", "Synthwave",
              "Retro Terminal", "Sakura", "Gamer HUD")
_B62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
MAX_V2_CHARS = 1200


def _b62encode(data: bytes) -> str:
    n = int.from_bytes(b"\x01" + data, "big")  # sentinel byte keeps leading zeros
    out = []
    while n:
        n, r = divmod(n, 62)
        out.append(_B62[r])
    return "".join(reversed(out))


def _b62decode(text: str) -> bytes:
    n = 0
    for ch in text:
        i = _B62.find(ch)
        if i < 0:
            raise ConfigError("That share code has an invalid character. Make sure you copied all of it.")
        n = n * 62 + i
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    if not raw or raw[0] != 1:
        raise ConfigError("That share code is damaged. Make sure you copied all of it.")
    return raw[1:]


def _kind(key):
    d = _default(key)
    if isinstance(d, bool):
        return "bool"
    if key.endswith("_color"):
        return "color"
    if key in CHOICES:
        return "enum"
    if key == "font_family":
        return "str"
    return "int" if isinstance(d, int) else "float"


def _width(key):
    lo, hi = RANGES[key]
    span = (hi - lo) if _kind(key) == "int" else round((hi - lo) * 100)
    return 1 if span <= 255 else 2


def _pack(key, v) -> bytes:
    kind = _kind(key)
    if kind == "bool":
        return b""  # a listed bool simply means "the opposite of the base look"
    if kind == "color":
        return bytes.fromhex(v[1:])
    if kind == "enum":
        return bytes([CHOICES[key].index(v)])
    if kind == "str":
        data = v.encode("utf-8")[:64]
        return bytes([len(data)]) + data
    lo, _ = RANGES[key]
    n = (v - lo) if kind == "int" else round((v - lo) * 100)
    return int(max(0, n)).to_bytes(_width(key), "big")


def _code_keys(full):
    return CODE_KEYS if full else STYLE_CODE_KEYS


def _unpack(key, data, pos, base_value):
    kind = _kind(key)

    def take(n):
        if pos + n > len(data):
            raise ConfigError("That share code is damaged. Make sure you copied all of it.")
        return data[pos:pos + n]
    if kind == "bool":
        return (not base_value), pos
    if kind == "color":
        return "#" + take(3).hex().upper(), pos + 3
    if kind == "enum":
        i = take(1)[0]
        return (CHOICES[key][i] if i < len(CHOICES[key]) else base_value), pos + 1
    if kind == "str":
        n = take(1)[0]
        pos += 1
        return bytes(take(n)).decode("utf-8", "ignore"), pos + n
    w = _width(key)
    n = int.from_bytes(take(w), "big")
    lo, _ = RANGES[key]
    return (lo + n if kind == "int" else round(lo + n / 100, 4)), pos + w


def _base_values(index, full=False):
    vals = {k: _default(k) for k in (FULL_KEYS if full else PRESET_KEYS)}
    name = CODE_BASES[index]
    if name:
        vals.update(STYLE_PRESETS.get(name, {}))
    return sanitize(vals, full)


def _same(key, a, b):
    return a == b if _kind(key) == "bool" else _pack(key, a) == _pack(key, b)


def same_look(a, b) -> bool:
    """True if two configs are the same (share codes store decimals to 0.01)."""
    keys = CODE_KEYS if is_full(a) and is_full(b) else STYLE_CODE_KEYS
    return all(_same(k, a.get(k, _default(k)), b.get(k, _default(k))) for k in keys)


def find_code(text: str):
    """The first share code inside any text (e.g. a pasted Discord message), or None."""
    m = re.search(r"MNK1-[A-Za-z0-9_-]{8,}|MNK-[0-9A-Za-z]{4,}", text or "")
    return m.group(0) if m else None


def _text_bytes(text, limit):
    data = clean_text(text, limit).encode("utf-8")[:255]
    return bytes([len(data)]) + data


def encode(name, author, values) -> str:
    """Compact share code: closest built-in look + only the settings that differ from it.
    Version 3 codes carry the whole setup; version 2 (configs from before 1.7.1) only the look."""
    full = is_full(values)
    vals = sanitize(values, full)
    keys = _code_keys(full)
    best = None
    for b in range(len(CODE_BASES)):
        base = _base_values(b, full)
        fields = b"".join(bytes([i]) + _pack(k, vals[k]) for i, k in enumerate(keys)
                          if not _same(k, vals[k], base[k]))
        if best is None or len(fields) < len(best[1]):
            best = (b, fields)
    body = bytes([best[0]]) + _text_bytes(name, MAX_NAME) + _text_bytes(author, MAX_AUTHOR) + best[1]
    packed = zlib.compressobj(9, zlib.DEFLATED, -15)
    deflated = packed.compress(body) + packed.flush()
    ver = 3 if full else 2
    payload = (bytes([0x80 | ver]) + deflated) if len(deflated) < len(body) else (bytes([ver]) + body)
    return CODE_PREFIX + _b62encode(payload)


def _decode_v2(body62: str) -> dict:
    if len(body62) > MAX_V2_CHARS:
        raise ConfigError("That share code is too long.")
    raw = _b62decode(body62)
    if not raw or raw[0] & 0x7F not in (2, 3):
        raise ConfigError("That share code is from a newer MNKLyrics. Update the app to use it.")
    full = raw[0] & 0x7F == 3
    data = raw[1:]
    if raw[0] & 0x80:
        try:
            d = zlib.decompressobj(-15)
            data = d.decompress(data, 4096)
        except zlib.error:
            raise ConfigError("That share code is damaged. Make sure you copied all of it.")
    if len(data) < 3 or data[0] >= len(CODE_BASES):
        raise ConfigError("That share code is damaged. Make sure you copied all of it.")
    vals = _base_values(data[0], full)
    keys = _code_keys(full)
    pos = 1
    texts = []
    for _ in range(2):
        if pos >= len(data):
            raise ConfigError("That share code is damaged. Make sure you copied all of it.")
        n = data[pos]
        texts.append(bytes(data[pos + 1:pos + 1 + n]).decode("utf-8", "ignore"))
        pos += 1 + n
        if pos > len(data):
            raise ConfigError("That share code is damaged. Make sure you copied all of it.")
    while pos < len(data):
        i = data[pos]
        if i >= len(keys):
            raise ConfigError("That share code is from a newer MNKLyrics. Update the app to use it.")
        key = keys[i]
        vals[key], pos = _unpack(key, data, pos + 1, vals[key])
    return {"name": clean_text(texts[0], MAX_NAME) or "Shared config",
            "author": clean_text(texts[1], MAX_AUTHOR), "values": sanitize(vals, full)}


def decode(code: str) -> dict:
    code = re.sub(r"\s+", "", code or "")
    start = code.find(LEGACY_PREFIX)
    if start < 0:
        m = re.search(r"MNK-([0-9A-Za-z]+)", code)
        if m:
            return _decode_v2(m.group(1))
        raise ConfigError("That isn't an MNKLyrics share code (it should start with MNK-).")
    body = code[start + len(LEGACY_PREFIX):]
    if not body or len(body) > MAX_CODE_CHARS:
        raise ConfigError("That share code is empty or too long.")
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        d = zlib.decompressobj()
        data = d.decompress(raw, 200_000)
        if d.unconsumed_tail:
            raise ConfigError("That share code is too large.")
        payload = json.loads(data.decode("utf-8"))
    except ConfigError:
        raise
    except Exception:
        raise ConfigError("That share code is damaged. Make sure you copied all of it.")
    if not isinstance(payload, dict) or payload.get("f") != 1:
        raise ConfigError("That share code is from an unsupported version.")
    return {"name": clean_text(payload.get("n"), MAX_NAME) or "Shared config",
            "author": clean_text(payload.get("a"), MAX_AUTHOR),
            "values": sanitize(payload.get("v"), False)}


def read_file(path) -> dict:
    with open(path, "r", encoding="utf-8-sig") as fh:
        text = fh.read(MAX_CODE_CHARS * 4)
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except ValueError:
            raise ConfigError("That file isn't a valid MNKLyrics config.")
        if not isinstance(data, dict) or data.get("format") != FILE_FORMAT:
            raise ConfigError("That file isn't an MNKLyrics config.")
        return {"name": clean_text(data.get("name"), MAX_NAME) or "Imported config",
                "author": clean_text(data.get("author"), MAX_AUTHOR),
                "values": sanitize(data.get("values"))}
    return decode(stripped)


class ConfigStore(QObject):
    changed = Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        legacy = settings.data.get("custom_presets") or {}
        if legacy:  # v1.0 "custom presets" become configs
            for name, values in legacy.items():
                self._put(name, values, author=self.author, imported=False)
            settings.data["custom_presets"] = {}
            settings.save()

    # ---------------------------------------------------------------- access
    @property
    def items(self) -> dict:
        return self.settings.data.setdefault("configs", {})

    @property
    def author(self) -> str:
        return clean_text(self.settings.get("general.author_name"), MAX_AUTHOR)

    def names(self):
        return list(self.items)

    def get(self, name):
        return self.items.get(name)

    def unique_name(self, base, ignore=None):
        base = clean_text(base, MAX_NAME) or "My config"
        taken = set(self.items) | set(STYLE_PRESETS)
        taken.discard(ignore)
        if base not in taken:
            return base
        n = 2
        while True:
            candidate = f"{base[:MAX_NAME - 5]} ({n})"
            if candidate not in taken:
                return candidate
            n += 1

    # --------------------------------------------------------------- changes
    def _put(self, name, values, author="", imported=False):
        name = self.unique_name(name)
        self.items[name] = {"values": sanitize(values), "author": clean_text(author, MAX_AUTHOR),
                            "created": datetime.date.today().isoformat(), "imported": bool(imported)}
        return name

    def _commit(self):
        self.settings.save()
        self.changed.emit()

    def save_current(self, name) -> str:
        name = self._put(name, capture(self.settings.overlay), author=self.author)
        self._commit()
        return name

    def update_from_current(self, name):
        entry = self.items[name]
        entry["values"] = sanitize(capture(self.settings.overlay), True)
        self._commit()

    def rename(self, old, new) -> str:
        new = clean_text(new, MAX_NAME)
        if not new:
            raise ConfigError("The name can't be empty.")
        if new == old:
            return old
        new = self.unique_name(new, ignore=old)
        # rebuild to keep the list order
        self.settings.data["configs"] = {(new if k == old else k): v for k, v in self.items.items()}
        if self.settings.get("general.active_config") == old:
            self.settings.set("general.active_config", new)
        self._commit()
        return new

    def delete(self, name):
        self.items.pop(name, None)
        if self.settings.get("general.active_config") == name:
            self.settings.set("general.active_config", "")
        self._commit()

    def duplicate(self, name) -> str:
        values = self.items[name]["values"] if name in self.items else STYLE_PRESETS[name]
        new = self._put(f"{name} copy", values, author=self.author)
        self._commit()
        return new

    def values_of(self, name) -> dict:
        if name in self.items:
            return self.items[name]["values"]
        return STYLE_PRESETS[name]

    # ---------------------------------------------------------------- share
    def share_code(self, name) -> str:
        entry = self.items.get(name)
        author = entry["author"] if entry else "MNKLyrics"
        return encode(name, author or self.author, self.values_of(name))

    def import_code(self, code) -> str:
        data = decode(code)
        name = self._put(data["name"], data["values"], author=data["author"], imported=True)
        self._commit()
        return name

    def import_file(self, path) -> str:
        data = read_file(path)
        name = self._put(data["name"], data["values"], author=data["author"], imported=True)
        self._commit()
        return name

    def export_file(self, name, path):
        entry = self.items.get(name) or {}
        data = {"format": FILE_FORMAT, "version": 1, "name": name,
                "author": entry.get("author") or self.author, "values": sanitize(self.values_of(name))}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)


# ---------------------------------------------------------------- whole-setup helpers
def _screen_rect_for(x, y, screen_index):
    """Available area of the screen holding point (x, y), else of the chosen monitor."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QGuiApplication
    s = QGuiApplication.screenAt(QPoint(int(x), int(y))) if x >= 0 else None
    if s is None:
        screens = QGuiApplication.screens()
        s = screens[screen_index] if 0 <= screen_index < len(screens) else QGuiApplication.primaryScreen()
    return s.availableGeometry() if s is not None else None


def capture(overlay: dict) -> dict:
    """The current overlay as a full config. A dragged position becomes a spot on the screen."""
    vals = dict(overlay)
    vals["pos_fx"] = vals["pos_fy"] = -1
    if vals.get("position") == "custom" and vals.get("custom_x", -1) >= 0:
        area = _screen_rect_for(vals["custom_x"], vals["custom_y"], int(vals.get("screen", 0)))
        if area is not None and area.width() > 0 and area.height() > 0:
            vals["pos_fx"] = round(min(max((vals["custom_x"] - area.x()) / area.width(), 0.0), 1.0) * 1000)
            vals["pos_fy"] = round(min(max((vals["custom_y"] - area.y()) / area.height(), 0.0), 1.0) * 1000)
    return sanitize(vals, True)


def to_overlay(values: dict, screen_index: int = 0) -> dict:
    """Overlay settings to apply for a config. Look-only configs (older ones and the built-in looks)
    leave size, position and options alone."""
    vals = sanitize(values)
    out = {k: vals[k] for k in PRESET_KEYS}
    if not is_full(values):
        return out
    out.update({k: vals[k] for k in LAYOUT_KEYS})
    if vals["position"] == "custom":
        area = _screen_rect_for(-1, -1, screen_index)
        if vals["pos_fx"] >= 0 and area is not None:
            out["custom_x"] = int(area.x() + vals["pos_fx"] / 1000 * area.width())
            out["custom_y"] = int(area.y() + vals["pos_fy"] / 1000 * area.height())
        else:
            out["position"] = "bottom_center"
    return out
