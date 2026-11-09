"""Song covers for playlists: downloaded once in the background, kept small on this PC (covers folder),
and handed out as ready-made pixmaps."""

import os
import queue
import threading

import requests
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage, QPixmap

from .config import APP_NAME, app_dir

SIZE = 96  # stored size (px); shown at 30–40 px, so this stays sharp on high-DPI screens


class CoverCache(QObject):
    ready = Signal(str)   # track id whose cover just arrived

    def __init__(self):
        super().__init__()
        self.folder = os.path.join(app_dir(), "covers")
        os.makedirs(self.folder, exist_ok=True)
        self._pix = {}
        self._queue = queue.Queue()
        self._asked = set()
        self._done_signal.connect(self._on_done)
        for _ in range(4):
            threading.Thread(target=self._work, daemon=True, name="covers").start()

    _done_signal = Signal(str)

    def path(self, tid):
        return os.path.join(self.folder, f"{tid}.jpg")

    def get(self, tid, url=""):
        """The cover as a QPixmap, or None for now (it's fetched in the background; ``ready`` fires)."""
        pm = self._pix.get(tid)
        if pm is not None:
            return pm
        p = self.path(tid)
        if os.path.exists(p):
            pm = QPixmap(p)
            if not pm.isNull():
                self._pix[tid] = pm
                return pm
        if url and tid not in self._asked:
            self._asked.add(tid)
            self._queue.put((tid, url))
        return None

    def _work(self):
        while True:
            tid, url = self._queue.get()
            try:
                r = requests.get(url, timeout=15, headers={"User-Agent": APP_NAME})
                if r.ok:
                    img = QImage.fromData(r.content)
                    if not img.isNull():
                        img = img.scaled(SIZE, SIZE, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                         Qt.TransformationMode.SmoothTransformation)
                        if img.save(self.path(tid), "JPG", 88):
                            self._done_signal.emit(tid)
            except (requests.RequestException, OSError):
                self._asked.discard(tid)  # try again next time it's needed

    def _on_done(self, tid):
        self._pix.pop(tid, None)
        self.ready.emit(tid)


_cache = None


def covers() -> CoverCache:
    global _cache
    if _cache is None:
        _cache = CoverCache()
    return _cache
