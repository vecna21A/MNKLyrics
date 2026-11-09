"""Start a song in the Spotify app without Spotify taking over the screen.

Opening ``spotify:track:<id>?autoplay=true`` makes the Spotify app switch to the song and play it, but
Spotify also jumps to the front. So right after, its window is put back the way it was (minimized, or
hidden in the tray) and the focus is handed back to the window that had it — usually the song picker,
which leaves the game in front once the picker is closed. Spotify shows for a split second at most.
"""

import ctypes
import os
import time
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtGui import QDesktopServices

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
SW_HIDE, SW_MINIMIZE, SW_SHOWMINNOACTIVE = 0, 6, 7
VK_MENU, KEYUP = 0x12, 0x0002


def _process(hwnd) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        return os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(h)


def _title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def spotify_window():
    """Spotify's main window (not its hidden helper windows), or None if Spotify isn't running."""
    found = []

    def cb(hwnd, _):
        if user32.GetWindow(hwnd, 4) == 0 and _process(hwnd) == "spotify.exe":  # top-level, unowned
            t = _title(hwnd)
            if t and not t.startswith(("GDI+", "Default IME", "MSCTFIME")):
                found.append(hwnd)
        return True
    user32.EnumWindows(_EnumProc(cb), 0)
    return found[0] if found else None


class SpotifyStarter(QObject):
    def __init__(self):
        super().__init__()
        self.timer = QTimer(self)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self._tick)

    def play(self, track_id: str):
        self.sp = spotify_window()
        self.running = self.sp is not None
        self.was_min = bool(user32.IsIconic(self.sp)) if self.running else True
        self.was_hidden = not bool(user32.IsWindowVisible(self.sp)) if self.running else False
        self.prev = user32.GetForegroundWindow()
        self.t0 = time.monotonic()
        self.limit = 3.0 if self.running else 15.0   # a cold start takes a few seconds
        QDesktopServices.openUrl(QUrl(f"spotify:track:{track_id}?autoplay=true"))
        self.timer.start()

    def _tick(self):
        if time.monotonic() - self.t0 > self.limit:
            self.timer.stop()
            self._tidy()
            return
        fg = user32.GetForegroundWindow()
        if not fg or _process(fg) != "spotify.exe":
            return
        sp = self.sp or spotify_window() or fg
        self.sp = sp
        # minimizing / hiding the window in front makes Windows move on; then hand the focus back
        user32.ShowWindow(sp, SW_HIDE if self.was_hidden else SW_MINIMIZE)
        if self.prev and user32.IsWindow(self.prev) and self.prev != sp:
            user32.keybd_event(VK_MENU, 0, 0, 0)       # a tap of Alt allows this app to set the focus
            user32.keybd_event(VK_MENU, 0, KEYUP, 0)
            user32.SetForegroundWindow(self.prev)
        if not self.running:
            self.limit = min(self.limit, time.monotonic() - self.t0 + 2.0)  # it's up now; watch a bit longer

    def _tidy(self):
        """Leave Spotify's window the way it was: hidden in the tray, or minimized."""
        sp = self.sp
        if not sp or not user32.IsWindow(sp) or _process(user32.GetForegroundWindow()) == "spotify.exe":
            return
        if self.was_hidden:
            user32.ShowWindow(sp, SW_HIDE)
        elif not user32.IsIconic(sp):
            user32.ShowWindow(sp, SW_SHOWMINNOACTIVE)


_starter = None


def play(track_id: str):
    global _starter
    if _starter is None:
        _starter = SpotifyStarter()
    _starter.play(track_id)
