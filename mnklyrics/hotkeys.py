"""System-wide hotkeys through the Win32 RegisterHotKey API (works while gaming)."""

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT,
        "win": MOD_WIN, "meta": MOD_WIN}
VK = {"space": 0x20, "pgup": 0x21, "pageup": 0x21, "pgdown": 0x22, "pagedown": 0x22, "end": 0x23,
      "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "ins": 0x2D, "insert": 0x2D,
      "del": 0x2E, "delete": 0x2E, "pause": 0x13, "tab": 0x09,
      ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, ";": 0xBA, "=": 0xBB, "[": 0xDB, "]": 0xDD, "'": 0xDE}
VK.update({f"f{i}": 0x6F + i for i in range(1, 25)})
VK.update({chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)})
VK.update({str(d): 0x30 + d for d in range(10)})


def parse_hotkey(text: str):
    """'Ctrl+Alt+L' -> (modifiers, virtual key) or None."""
    if not text:
        return None
    parts = [p.strip().lower() for p in text.replace(" ", "").split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    for p in parts[:-1]:
        if p not in MODS:
            return None
        mods |= MODS[p]
    vk = VK.get(parts[-1])
    if vk is None:
        return None
    return mods, vk


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager

    def nativeEventFilter(self, event_type, message):
        try:
            msg = wintypes.MSG.from_address(int(message))
        except (TypeError, ValueError):
            return False, 0
        if msg.message == WM_HOTKEY:
            self.manager._fire(int(msg.wParam))
            return True, 0
        return False, 0


class HotkeyManager(QObject):
    triggered = Signal(str)

    def __init__(self, app):
        super().__init__()
        self.enabled = sys.platform == "win32"
        self._ids = {}
        self.failed = []
        if self.enabled:
            self._user32 = ctypes.windll.user32
            self._filter = _Filter(self)
            app.installNativeEventFilter(self._filter)

    def register_all(self, mapping: dict):
        self.unregister_all()
        self.failed = []
        if not self.enabled:
            return
        for i, (name, combo) in enumerate(mapping.items(), start=1):
            parsed = parse_hotkey(combo)
            if not parsed:
                continue
            mods, vk = parsed
            if self._user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                self._ids[i] = name
            else:
                self.failed.append(name)

    def unregister_all(self):
        if not self.enabled:
            return
        for i in list(self._ids):
            self._user32.UnregisterHotKey(None, i)
        self._ids.clear()

    def _fire(self, hotkey_id):
        name = self._ids.get(hotkey_id)
        if name:
            self.triggered.emit(name)
