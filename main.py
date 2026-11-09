"""MNKLyrics — see Spotify lyrics on top of anything (games, work, chats)."""

import ctypes
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication


def main():
    if sys.platform == "win32":
        # own taskbar identity instead of python.exe
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MNKLyrics.App")
        except Exception:
            pass
        # Windows timers tick every ~15.6 ms by default, so a 60 fps (16 ms) animation alternates
        # between 15 and 31 ms frames and stutters. 1 ms resolution gives evenly spaced frames.
        # Since Windows 10 2004 this only affects this process.
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("MNKLyrics")
    app.setQuitOnLastWindowClosed(False)

    from mnklyrics.app import MNKLyricsApp, send_to_running_instance, wait_for_previous_instance
    from mnklyrics.updater import cleanup_old_exe, is_frozen

    if "--updated" in sys.argv:
        wait_for_previous_instance()  # the old version is still closing
    if is_frozen():
        cleanup_old_exe()  # remove the previous version left behind by an update

    # a .mnkcfg file opened with the app while it's already running gets imported there
    cfg_files = [a for a in sys.argv[1:] if a.lower().endswith(".mnkcfg")]
    if cfg_files:
        if send_to_running_instance(b"import:" + cfg_files[0].encode("utf-8")):
            return 0
    elif send_to_running_instance(b"show"):
        return 0
    mnk = MNKLyricsApp(app)  # noqa: F841 - keep a reference for the app's lifetime
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
