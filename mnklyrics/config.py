"""Persistent settings, defaults and style presets."""

import copy
import json
import os

from PySide6.QtCore import QObject, QTimer, Signal

APP_NAME = "MNKLyrics"
APP_ID = "MNKLyrics"
VERSION = "2.5.1"
# GitHub repository ("owner/name") whose Releases deliver updates. Set once via tools/release.py.
UPDATE_REPO = "vecna21A/MNKLyrics"
_OLD_APP_ID = "LuminaLyrics"  # the app's previous name
# Application ID from discord.com/developers (public, not a secret). Its name is what Discord shows:
# "Listening to MNKLyrics". Empty = the Discord status feature stays off.
DISCORD_CLIENT_ID = "1562172197466931380"
# MNKLyrics community lyrics database (Supabase): project URL + public anon/publishable key. Both are
# public by design — the database only lets the app read and add lyrics. Empty = community features off.
COMMUNITY_URL = ""
COMMUNITY_KEY = ""


def app_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_ID)
    if not os.path.isdir(path):
        old = os.path.join(base, _OLD_APP_ID)
        if os.path.isdir(old):
            # carry settings, saved lyrics versions and cache over from the old name
            import shutil
            try:
                shutil.copytree(old, path)
            except OSError:
                pass
    os.makedirs(path, exist_ok=True)
    return path


def cache_dir() -> str:
    path = os.path.join(app_dir(), "cache")
    os.makedirs(path, exist_ok=True)
    return path


DEFAULTS = {
    "overlay": {
        # typography
        "font_family": "Segoe UI",
        "font_size": 32,
        "font_weight": 700,
        "italic": False,
        "uppercase": False,
        "letter_spacing": 0.0,
        "line_spacing": 1.3,
        "context_scale": 0.72,
        "lines_before": 1,
        "lines_after": 1,
        "align": "center",
        # colors
        "text_color": "#FFFFFF",
        "context_opacity": 0.5,
        "highlight_style": "karaoke",
        "highlight_color": "#1ED760",
        "glow_enabled": True,
        "glow_color": "#1ED760",
        "glow_strength": 0.75,
        "glow_radius": 16,
        "outline_enabled": True,
        "outline_color": "#000000",
        "outline_width": 1.5,
        "shadow_enabled": True,
        "shadow_color": "#000000",
        "shadow_opacity": 0.6,
        "shadow_offset": 3,
        "bg_enabled": True,
        "bg_color": "#08080F",
        "bg_opacity": 0.45,
        "bg_radius": 20,
        "bg_border": True,
        "padding": 20,
        # animation
        "animation": "slide",
        "entrance": "pop",
        "anim_speed": 1.0,
        # layout
        "width": 960,
        "position": "bottom_center",
        "margin": 70,
        "custom_x": -1,
        "custom_y": -1,
        "screen": 0,
        "opacity": 1.0,
        # behaviour
        "visible": True,
        "click_through": True,
        "show_track_info": True,
        "show_progress": True,
        "hide_when_paused": False,
        "hide_no_lyrics": False,
        "no_lyrics_card": True,
        "fade_on_hover": True,
        "hover_opacity": 0.25,
        # player controls on the overlay (all optional)
        "controls_enabled": False,
        "controls_playpause": True,
        "controls_prevnext": True,
        "controls_volume": True,
        "controls_position": "below",      # below / above / left / right
        "controls_visibility": "always",   # always / hover
        "controls_style": "pill",          # pill / glass / minimal
        "controls_size": 1.0,
        "controls_color": "#FFFFFF",
        "controls_opacity": 0.92,
        "controls_playlist": True,          # song picker button (shows once a playlist is saved)
        "controls_playlist_visibility": "always",   # always / hover
    },
    # Full-screen Overlay: its own complete look, independent from the small overlay
    "fullscreen": {
        "font_family": "Segoe UI",
        "font_size": 56,
        "font_weight": 800,
        "italic": False,
        "uppercase": False,
        "letter_spacing": 0.0,
        "line_spacing": 1.35,
        "context_scale": 0.86,
        "lines_before": 2,
        "lines_after": 3,
        "align": "left",
        "text_color": "#FFFFFF",
        "context_opacity": 0.38,
        "highlight_style": "color",
        "highlight_color": "#FFFFFF",
        "glow_enabled": False,
        "glow_color": "#FFFFFF",
        "glow_strength": 0.45,
        "glow_radius": 18,
        "outline_enabled": False,
        "outline_color": "#000000",
        "outline_width": 1.5,
        "shadow_enabled": True,
        "shadow_color": "#000000",
        "shadow_opacity": 0.35,
        "shadow_offset": 3,
        "animation": "slide",
        "entrance": "none",
        "anim_speed": 1.0,
        "bg_mode": "album",                # album / color / gradient / image
        "bg_solid": "#121220",
        "bg_grad1": "#3A1C71",
        "bg_grad2": "#0B0B1A",
        "bg_grad_angle": 135,
        "bg_image": "",
        "bg_blur": 60,
        "bg_dim": 0.45,
        "bg_vignette": True,
        "lyrics_width": 0.6,
        "art_position": "left",            # left / top / hidden
        "show_title": True,
        "show_progress": True,
        "show_controls": True,
        "controls_volume": True,
        "controls_prevnext": True,
        "screen": 0,
        "always_on_top": True,
        "hide_cursor": True,
    },
    "general": {
        "ui_theme": "Neon Night",
        "ui_effects": True,
        "sync_offset_ms": 0,
        "player": "spotify",
        "start_with_windows": False,
        "show_window_on_start": True,
        "show_in_recordings": True,
        "discord_status": True,
        "discord_show_song": True,
        "discord_cover": True,
        "discord_button": True,
        "auto_update": True,
        "author_name": "",
        "active_config": "",
        "active_playlist": "",
    },
    "hotkeys": {
        "toggle_overlay": "Ctrl+Alt+L",
        "toggle_lock": "Ctrl+Alt+K",
        "open_settings": "Ctrl+Alt+O",
        "font_bigger": "Ctrl+Alt+Up",
        "font_smaller": "Ctrl+Alt+Down",
        "offset_minus": "Ctrl+Alt+Left",
        "offset_plus": "Ctrl+Alt+Right",
        "next_preset": "Ctrl+Alt+P",
        "play_pause": "Ctrl+Alt+Home",
        "next_version": "Ctrl+Alt+V",
        "toggle_fullscreen": "Ctrl+Alt+F",
        "next_track": "Ctrl+Alt+End",
        "previous_track": "Ctrl+Alt+Insert",
        "volume_up": "Ctrl+Alt+PgUp",
        "volume_down": "Ctrl+Alt+PgDown",
    },
    "custom_presets": {},  # legacy (v1.0) — migrated into "configs" on start
    "configs": {},         # name -> {"values": {...}, "author": str, "created": iso date, "imported": bool}
}

HOTKEY_LABELS = {
    "toggle_overlay": "Show / hide overlay",
    "toggle_lock": "Lock / unlock overlay (click-through)",
    "open_settings": "Open control center",
    "font_bigger": "Increase font size",
    "font_smaller": "Decrease font size",
    "offset_minus": "Lyrics later (-100 ms)",
    "offset_plus": "Lyrics earlier (+100 ms)",
    "next_preset": "Cycle style presets",
    "play_pause": "Play / pause Spotify",
    "next_version": "Try another lyrics version",
    "toggle_fullscreen": "Open / close Full-screen Overlay",
    "next_track": "Next song",
    "previous_track": "Previous song",
    "volume_up": "Spotify volume up",
    "volume_down": "Spotify volume down",
}

# Keys a style preset is allowed to change (never position / behaviour).
PRESET_KEYS = (
    "font_family", "font_size", "font_weight", "italic", "uppercase", "letter_spacing",
    "line_spacing", "context_scale", "lines_before", "lines_after", "align",
    "text_color", "context_opacity", "highlight_style", "highlight_color",
    "glow_enabled", "glow_color", "glow_strength", "glow_radius",
    "outline_enabled", "outline_color", "outline_width",
    "shadow_enabled", "shadow_color", "shadow_opacity", "shadow_offset",
    "bg_enabled", "bg_color", "bg_opacity", "bg_radius", "bg_border", "padding",
    "animation", "entrance", "anim_speed",
)

# Everything else a saved/shared config carries: the creator's size, place on screen and overlay options.
# (Not shared: whether the overlay is shown/locked and which monitor — those stay the receiver's own.)
LAYOUT_KEYS = (
    "width", "position", "margin", "opacity", "show_track_info", "show_progress", "hide_when_paused",
    "hide_no_lyrics", "no_lyrics_card", "fade_on_hover", "hover_opacity",
    "controls_enabled", "controls_playpause", "controls_prevnext", "controls_volume", "controls_position",
    "controls_visibility", "controls_style", "controls_size", "controls_color", "controls_opacity",
    "controls_playlist", "controls_playlist_visibility",
)
CONFIG_KEYS = PRESET_KEYS + LAYOUT_KEYS

STYLE_PRESETS = {
    "Classic Green": {
        "font_family": "Segoe UI", "font_weight": 700, "italic": False, "uppercase": False,
        "letter_spacing": 0.0, "text_color": "#FFFFFF", "context_opacity": 0.5,
        "highlight_style": "karaoke", "highlight_color": "#1ED760",
        "glow_enabled": True, "glow_color": "#1ED760", "glow_strength": 0.75, "glow_radius": 16,
        "outline_enabled": True, "outline_color": "#000000", "outline_width": 1.5,
        "shadow_enabled": True, "bg_enabled": True, "bg_color": "#08080F", "bg_opacity": 0.45,
        "bg_radius": 20, "bg_border": True, "animation": "slide", "entrance": "pop", "align": "center",
    },
    "Neon Karaoke": {
        "font_family": "Bahnschrift", "font_weight": 700, "italic": False, "uppercase": False,
        "letter_spacing": 1.0, "text_color": "#E6FBFF", "context_opacity": 0.4,
        "highlight_style": "glow", "highlight_color": "#00E5FF",
        "glow_enabled": True, "glow_color": "#00E5FF", "glow_strength": 1.1, "glow_radius": 24,
        "outline_enabled": False, "shadow_enabled": False,
        "bg_enabled": False, "animation": "bounce", "entrance": "pop", "align": "center",
    },
    "Cinema Subtitles": {
        "font_family": "Segoe UI", "font_weight": 600, "italic": False, "uppercase": False,
        "letter_spacing": 0.0, "text_color": "#FFFFFF", "context_opacity": 0.55,
        "highlight_style": "color", "highlight_color": "#FFD54F",
        "glow_enabled": False, "outline_enabled": True, "outline_color": "#000000", "outline_width": 2.5,
        "shadow_enabled": True, "shadow_opacity": 0.8, "bg_enabled": False,
        "lines_before": 0, "lines_after": 0, "animation": "fade", "entrance": "none", "align": "center",
    },
    "Minimal Glass": {
        "font_family": "Segoe UI", "font_weight": 400, "italic": False, "uppercase": False,
        "letter_spacing": 0.5, "text_color": "#FFFFFF", "context_opacity": 0.32,
        "highlight_style": "color", "highlight_color": "#FFFFFF",
        "glow_enabled": True, "glow_color": "#FFFFFF", "glow_strength": 0.35, "glow_radius": 14,
        "outline_enabled": False, "shadow_enabled": True, "shadow_opacity": 0.5,
        "bg_enabled": True, "bg_color": "#FFFFFF", "bg_opacity": 0.08, "bg_radius": 28, "bg_border": True,
        "animation": "slide", "entrance": "rise", "align": "center",
    },
    "Synthwave": {
        "font_family": "Bahnschrift", "font_weight": 700, "italic": True, "uppercase": True,
        "letter_spacing": 2.0, "text_color": "#FFD6F0", "context_opacity": 0.45,
        "highlight_style": "karaoke", "highlight_color": "#FF2E97",
        "glow_enabled": True, "glow_color": "#FF2E97", "glow_strength": 1.0, "glow_radius": 22,
        "outline_enabled": True, "outline_color": "#1A0526", "outline_width": 2.0,
        "shadow_enabled": False, "bg_enabled": True, "bg_color": "#1A0526", "bg_opacity": 0.55,
        "bg_radius": 14, "bg_border": True, "animation": "bounce", "entrance": "rise", "align": "center",
    },
    "Retro Terminal": {
        "font_family": "Consolas", "font_weight": 700, "italic": False, "uppercase": True,
        "letter_spacing": 1.0, "text_color": "#33FF66", "context_opacity": 0.4,
        "highlight_style": "color", "highlight_color": "#B7FFC8",
        "glow_enabled": True, "glow_color": "#33FF66", "glow_strength": 0.7, "glow_radius": 14,
        "outline_enabled": False, "shadow_enabled": False,
        "bg_enabled": True, "bg_color": "#000000", "bg_opacity": 0.75, "bg_radius": 6, "bg_border": True,
        "animation": "slide", "entrance": "typewriter", "align": "left",
    },
    "Sakura": {
        "font_family": "Georgia", "font_weight": 400, "italic": True, "uppercase": False,
        "letter_spacing": 0.5, "text_color": "#FFF0F5", "context_opacity": 0.45,
        "highlight_style": "pill", "highlight_color": "#FF8FB1",
        "glow_enabled": True, "glow_color": "#FF8FB1", "glow_strength": 0.5, "glow_radius": 18,
        "outline_enabled": False, "shadow_enabled": True, "shadow_opacity": 0.4,
        "bg_enabled": True, "bg_color": "#2B0F1A", "bg_opacity": 0.4, "bg_radius": 26, "bg_border": True,
        "animation": "zoom", "entrance": "pop", "align": "center",
    },
    "Gamer HUD": {
        "font_family": "Bahnschrift", "font_weight": 700, "italic": False, "uppercase": True,
        "letter_spacing": 2.5, "text_color": "#F2F2F2", "context_opacity": 0.45,
        "highlight_style": "underline", "highlight_color": "#FF4655",
        "glow_enabled": True, "glow_color": "#FF4655", "glow_strength": 0.6, "glow_radius": 14,
        "outline_enabled": True, "outline_color": "#000000", "outline_width": 2.0,
        "shadow_enabled": True, "bg_enabled": False,
        "lines_before": 0, "lines_after": 1, "animation": "slide", "entrance": "rise", "align": "center",
    },
}


def _merge(dst: dict, src: dict) -> None:
    for key, value in src.items():
        if key not in dst:
            continue
        if isinstance(dst[key], dict) and isinstance(value, dict) and dst[key]:
            _merge(dst[key], value)
        elif isinstance(dst[key], dict) and isinstance(value, dict):
            dst[key] = value  # free-form dicts such as custom_presets
        elif type(dst[key]) is type(value) or (isinstance(dst[key], (int, float)) and isinstance(value, (int, float))):
            dst[key] = value


class Settings(QObject):
    """Dotted-key settings store. Emits ``changed(key)``; ``"*"`` means many keys changed."""

    changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.path = os.path.join(app_dir(), "settings.json")
        self.data = copy.deepcopy(DEFAULTS)
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                _merge(self.data, json.load(fh))
        except (OSError, ValueError):
            pass
        # names renamed in 1.3.1 so they no longer use another company's brand
        g = self.data["general"]
        g["ui_theme"] = {"Spotify": "Emerald"}.get(g["ui_theme"], g["ui_theme"])
        g["active_config"] = {"Spotify Classic": "Classic Green"}.get(g["active_config"], g["active_config"])
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self.save)

    @property
    def overlay(self) -> dict:
        return self.data["overlay"]

    def get(self, key: str, default=None):
        section, _, name = key.partition(".")
        return self.data.get(section, {}).get(name, default)

    def set(self, key: str, value) -> None:
        section, _, name = key.partition(".")
        current = self.data[section].get(name)
        if isinstance(current, float) and isinstance(value, int):
            value = float(value)
        if current == value:
            return
        self.data[section][name] = value
        self.changed.emit(key)
        self._save_timer.start()

    def set_many(self, section: str, values: dict) -> None:
        """Change several keys, then notify — listeners never see a half-applied combination."""
        changed = [k for k, v in values.items() if self.data[section].get(k) != v]
        self.data[section].update(values)
        for k in changed:
            self.changed.emit(f"{section}.{k}")
        if changed:
            self._save_timer.start()

    def update_section(self, section: str, values: dict) -> None:
        self.data[section].update(values)
        self.changed.emit("*")
        self._save_timer.start()

    def reset_section(self, section: str) -> None:
        self.data[section] = copy.deepcopy(DEFAULTS[section])
        self.changed.emit("*")
        self._save_timer.start()

    def all_presets(self) -> dict:
        """Built-in looks plus the user's own configs, as name -> overlay values."""
        presets = dict(STYLE_PRESETS)
        for name, entry in self.data.get("configs", {}).items():
            presets[name] = entry.get("values", {})
        return presets

    def save(self) -> None:
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass
