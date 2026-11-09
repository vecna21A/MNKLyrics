"""Playlists in the UI: the small song picker that opens from the overlay's playlist button, and the
"Playlists" page of the control center (paste links, see and manage saved playlists)."""

import time

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QGuiApplication, QIcon, QPainter, QPainterPath, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QVBoxLayout, QWidget)

from . import playlists
from .covers import covers
from .widgets import AA, Card, GlowButton, paint_glow, row, theme


def fmt_ms(ms):
    s = int((ms or 0) / 1000)
    return f"{s // 60}:{s % 60:02d}"


def _norm(t):
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in (t or "")).split())


def _list_style():
    return (f"QListWidget {{ background: transparent; border: none; outline: none; }}"
            f"QListWidget::item {{ padding: 7px 10px; border-radius: 8px; margin: 1px 0; }}"
            f"QListWidget::item:hover {{ background: {theme.c('bg3').name()}; }}"
            f"QListWidget::item:selected {{ background: {theme.c('bg3').name()}; color: {theme.c('accent').name()}; }}")


class SmoothList(QListWidget):
    """Song list with covers and a smooth, animated mouse-wheel scroll."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setIconSize(QSize(38, 38))
        self.setStyleSheet(_list_style())
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(280)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda v: self.verticalScrollBar().setValue(int(v)))
        self._target = None
        self._items = {}
        covers().ready.connect(self._cover_ready)

    def wheelEvent(self, e):
        if not e.pixelDelta().isNull():  # touchpads scroll smoothly by themselves
            super().wheelEvent(e)
            return
        bar = self.verticalScrollBar()
        running = self._anim.state() == QVariantAnimation.State.Running
        start = self._target if running and self._target is not None else bar.value()
        self._target = max(bar.minimum(), min(bar.maximum(), start - e.angleDelta().y() / 120 * 150))
        self._anim.stop()
        self._anim.setStartValue(float(bar.value()))
        self._anim.setEndValue(float(self._target))
        self._anim.start()
        e.accept()

    def _cover_ready(self, tid):
        item = self._items.get(tid)
        if item is not None:
            pm = covers().get(tid)
            if pm is not None:
                item.setIcon(QIcon(pm))


def _blank_cover():
    pm = QPixmap(76, 76)
    pm.fill(theme.c("bg3"))
    return QIcon(pm)


def fill_songs(widget: QListWidget, pl, query="", current=None):
    """Put a playlist's songs into a list (filtered by ``query``); the playing song gets a ♪. Keeps the
    scroll position, so names arriving in the background don't make the list jump."""
    bar = widget.verticalScrollBar()
    keep = bar.value()
    widget.clear()
    if isinstance(widget, SmoothList):
        widget._items = {}
    if not pl:
        return 0
    q = _norm(query)
    cur = _norm(current.title) if current is not None else None
    n = 0
    for i, t in enumerate(pl["tracks"]):
        name = t.get("name") or ""
        if q and q not in _norm(f"{name} {t.get('artists', '')}"):
            continue
        playing = bool(name) and cur is not None and _norm(name) == cur
        sub = "  •  ".join(x for x in (t.get("artists"), fmt_ms(t["duration_ms"]) if t.get("duration_ms") else "") if x)
        shown = name or f"Song {i + 1}  ·  name loading…"
        item = QListWidgetItem(f"{'♪  ' if playing else ''}{shown}" + (f"\n{sub}" if sub else ""))
        item.setData(Qt.ItemDataRole.UserRole, t["id"])
        if playing:
            item.setForeground(theme.c("accent"))
        pm = covers().get(t["id"], t.get("image", ""))
        item.setIcon(QIcon(pm) if pm is not None else _blank_cover())
        if isinstance(widget, SmoothList):
            widget._items[t["id"]] = item
        widget.addItem(item)
        n += 1
    bar.setValue(keep)
    return n


class SongDropdown(QWidget):
    """The overlay's song list: unfolds from the playlist button (5 songs tall), see-through, with covers.
    Click a song to play it; click anywhere else (or the button again) to close it."""

    ROW = 46.0
    ROWS = 5
    HEAD = 28.0
    PAD = 8.0
    WIDTH = 320
    closed = Signal()

    def __init__(self, store):
        super().__init__(None)
        self.store = store
        self.current_name = ""
        self.tracks = []
        self.t = 0.0             # unfold (0..1)
        self.scroll = 0.0        # rows scrolled (animated)
        self._target = 0.0
        self.hover = -1
        self.closed_at = 0.0
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.NoDropShadowWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFixedSize(self.WIDTH, int(self.HEAD + self.ROW * self.ROWS + 2 * self.PAD))
        self._open_anim = QVariantAnimation(self)
        self._open_anim.setDuration(320)
        self._open_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._open_anim.valueChanged.connect(lambda v: (setattr(self, "t", float(v)), self.update()))
        self._scroll_anim = QVariantAnimation(self)
        self._scroll_anim.setDuration(260)
        self._scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._scroll_anim.valueChanged.connect(lambda v: (setattr(self, "scroll", float(v)), self.update()))
        covers().ready.connect(lambda _tid: self.isVisible() and self.update())
        store.names_changed.connect(lambda: self.isVisible() and self._load())
        store.changed.connect(lambda: self.isVisible() and self._load())

    # ------------------------------------------------------------ data
    def _load(self):
        pl = self.store.active
        self.tracks = list(pl["tracks"]) if pl else []
        self.title = pl["name"] if pl else "No playlist yet"
        self.update()

    def _max_scroll(self):
        return max(0.0, len(self.tracks) - self.ROWS)

    # ------------------------------------------------------------ open / close
    def toggle_near(self, btn: QRect, current=None):
        if self.isVisible():
            self.close()
            return
        if time.monotonic() - self.closed_at < 0.35:
            return  # this click on the button is what just closed it
        self.current_name = current.title if current is not None else ""
        self._load()
        cur = self._current_index()
        self._target = self.scroll = float(min(max(0, cur - 1), self._max_scroll())) if cur >= 0 else 0.0
        screen = QGuiApplication.screenAt(btn.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = btn.left() - 6
        y = btn.bottom() + 6
        if y + self.height() > area.bottom():
            y = btn.top() - self.height() - 6
        x = max(area.left() + 4, min(x, area.right() - self.width() - 4))
        self.move(QPoint(x, y))
        self.t = 0.0
        self.show()
        self._open_anim.stop()
        self._open_anim.setStartValue(0.0)
        self._open_anim.setEndValue(1.0)
        self._open_anim.start()

    def hideEvent(self, e):
        self.closed_at = time.monotonic()
        self.closed.emit()
        super().hideEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close()

    # ------------------------------------------------------------ geometry
    def _list_rect(self):
        return QRectF(self.PAD, self.PAD + self.HEAD, self.width() - 2 * self.PAD, self.ROW * self.ROWS)

    def _current_index(self):
        if not self.current_name:
            return -1
        cur = _norm(self.current_name)
        return next((i for i, t in enumerate(self.tracks) if t.get("name") and _norm(t["name"]) == cur), -1)

    def _row_at(self, pos):
        lr = self._list_rect()
        if not lr.contains(pos):
            return -1
        i = int((pos.y() - lr.top()) / self.ROW + self.scroll)
        return i if 0 <= i < len(self.tracks) else -1

    # ------------------------------------------------------------ input
    def mouseMoveEvent(self, e):
        h = self._row_at(e.position())
        if h != self.hover:
            self.hover = h
            self.update()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        if not self.rect().contains(e.position().toPoint()):
            self.close()   # a click outside (Qt sends it here while the list is open)
            return
        i = self._row_at(e.position())
        if i >= 0:
            t = self.tracks[i]
            playlists.play(t["id"])
            self.current_name = t.get("name") or ""
            self.update()

    def wheelEvent(self, e):
        step = -e.angleDelta().y() / 120.0
        self._target = min(max(0.0, self._target + step * 1.5), self._max_scroll())
        self._scroll_anim.stop()
        self._scroll_anim.setStartValue(self.scroll)
        self._scroll_anim.setEndValue(self._target)
        self._scroll_anim.start()

    # ------------------------------------------------------------ paint
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        t = self.t
        full = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        shown = QRectF(full.left(), full.top(), full.width(), max(14.0, full.height() * t))  # unfolds downward
        path = QPainterPath()
        path.addRoundedRect(shown, 14, 14)
        p.setClipPath(path)
        bg = QColor(theme.c("bg0"))
        bg.setAlphaF(0.55)       # see-through
        p.fillPath(path, bg)
        p.setPen(QPen(theme.c("accent", 0.45), 1.0))
        p.drawPath(path)

        f = QFont("Segoe UI")
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(theme.c("accent"))
        head = QRectF(self.PAD + 6, self.PAD, self.width() - 2 * self.PAD - 12, self.HEAD - 4)
        p.drawText(head, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.title)
        p.setPen(theme.c("dim"))
        f2 = QFont("Segoe UI")
        f2.setPixelSize(11)
        p.setFont(f2)
        p.drawText(head, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{len(self.tracks)} songs")

        lr = self._list_rect()
        p.save()
        p.setClipRect(lr.intersected(shown))
        first = int(self.scroll)
        cur = self._current_index()
        name_f = QFont("Segoe UI")
        name_f.setPixelSize(13)
        name_f.setWeight(QFont.Weight.DemiBold)
        sub_f = QFont("Segoe UI")
        sub_f.setPixelSize(11)
        for i in range(first, min(len(self.tracks), first + self.ROWS + 1)):
            k = i - self.scroll
            y = lr.top() + k * self.ROW
            # rows appear one after another as the list unfolds
            ra = max(0.0, min(1.0, t * (self.ROWS + 2) - (i - first)))
            if ra <= 0:
                continue
            p.setOpacity(ra)
            row = QRectF(lr.left(), y + (1 - ra) * -6, lr.width(), self.ROW)
            tr = self.tracks[i]
            if i == self.hover or i == cur:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.c("accent", 0.22 if i == cur else 0.12))
                p.drawRoundedRect(row.adjusted(0, 3, 0, -3), 9, 9)
            cov = QRectF(row.left() + 6, row.center().y() - 16, 32, 32)
            pm = covers().get(tr["id"], tr.get("image", ""))
            cp = QPainterPath()
            cp.addRoundedRect(cov, 6, 6)
            p.save()
            p.setClipPath(cp, Qt.ClipOperation.IntersectClip)
            if pm is not None:
                p.drawPixmap(cov.toRect(), pm)
            else:
                p.fillRect(cov, theme.c("bg3"))
            p.restore()
            tx = cov.right() + 10
            tw = row.right() - tx - 8
            name = tr.get("name") or f"Song {i + 1}"
            sub = "  •  ".join(x for x in (tr.get("artists") or "",
                                           fmt_ms(tr["duration_ms"]) if tr.get("duration_ms") else "") if x)
            p.setFont(name_f)
            p.setPen(theme.c("accent") if i == cur else theme.c("text"))
            fm = QFontMetricsF(name_f)
            ny = row.center().y() - (2 if sub else -fm.ascent() / 2 + 2)
            p.drawText(QPointF(tx, ny if sub else row.center().y() + fm.ascent() / 2 - 2),
                       fm.elidedText(("♪ " if i == cur else "") + name, Qt.TextElideMode.ElideRight, tw))
            if sub:
                p.setFont(sub_f)
                p.setPen(theme.c("text", 0.8) if i == cur else theme.c("dim"))
                fm2 = QFontMetricsF(sub_f)
                p.drawText(QPointF(tx, row.center().y() + fm2.ascent() + 1),
                           fm2.elidedText(sub, Qt.TextElideMode.ElideRight, tw))
        p.restore()
        p.setOpacity(1.0)
        # slim scroll indicator
        if len(self.tracks) > self.ROWS and t > 0.6:
            span = lr.height() * self.ROWS / len(self.tracks)
            y = lr.top() + (lr.height() - span) * (self.scroll / self._max_scroll())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.c("accent", 0.55))
            p.drawRoundedRect(QRectF(self.width() - 5, y, 3, span), 1.5, 1.5)


class PlaylistPages:
    """Mixed into MainWindow: the "Playlists" page. Needs self.playlists (PlaylistStore)."""

    def _build_playlists(self):
        lay = self._page("Playlists", "Save songs from your Spotify playlists here, then pick any of them right "
                                      "from the overlay with its playlist button.", "playlist", section="Music")
        add = Card("Add songs from Spotify", "1. Open a playlist in Spotify and click any song.   2. Press Ctrl+A, "
                                             "then Ctrl+C (this copies every song).   3. Click the button below.")
        h = QHBoxLayout()
        go = GlowButton("Paste songs from Spotify", icon="download")
        go.clicked.connect(lambda _=False: self._pl_paste())
        h.addWidget(go)
        h.addStretch(1)
        add.body.addLayout(h)
        self.pl_note = QLabel("Works with any playlist: yours, a friend's, or Spotify's own mixes.")
        self.pl_note.setObjectName("rowDesc")
        self.pl_note.setWordWrap(True)
        add.body.addWidget(self.pl_note)
        lay.addWidget(add)

        saved = Card("Your playlists", "The one marked ON OVERLAY is what the overlay's playlist button shows.")
        self.pl_rows = QVBoxLayout()
        self.pl_rows.setSpacing(6)
        saved.body.addLayout(self.pl_rows)
        lay.addWidget(saved)

        songs = Card("Songs")
        self.pl_songs_title = songs.findChild(QLabel, "cardTitle")
        self.pl_search = QLineEdit()
        self.pl_search.setPlaceholderText("Search songs…")
        self.pl_search.textChanged.connect(lambda _t: self._pl_fill_songs())
        songs.body.addWidget(self.pl_search)
        self.pl_list = SmoothList()
        self.pl_list.setMinimumHeight(360)
        self.pl_list.itemDoubleClicked.connect(self._pl_play)
        songs.body.addWidget(self.pl_list)
        tip = QLabel("Double-click a song to play it in Spotify.")
        tip.setObjectName("rowDesc")
        songs.body.addWidget(tip)
        lay.addWidget(songs)
        lay.addStretch(1)

        self._pl_shown = None
        self.playlists.changed.connect(self._pl_rebuild)
        self.playlists.loaded.connect(self._pl_loaded)
        self.playlists.names_changed.connect(self._pl_names_arrived)
        self.playlists.waiting.connect(self._pl_waiting)
        self._pl_rebuild()

    def _pl_say(self, text, error=False):
        self.pl_note.setText(text)
        self.pl_note.setStyleSheet(f"color: {'#FF6B6B' if error else theme.c('accent').name()};")

    def _pl_paste(self):
        from PySide6.QtWidgets import QInputDialog
        text = QGuiApplication.clipboard().text()
        if not playlists.track_ids(text):
            self.playlists.add_songs(text, "")   # explains what to do
            return
        n = len(playlists.track_ids(text))
        name, ok = QInputDialog.getText(self, "Name this list", f"{n} songs copied. Name the list:",
                                        text="My playlist")
        if not ok:
            return
        self._pl_say(f"Loading {n} songs…")
        self.playlists.add_songs(text, name.strip() or "My playlist")

    def _pl_names_arrived(self):
        self._pl_fill_songs()
        known, total = self.playlists.progress()
        if known < total:
            self._pl_say(f"Loading song names… {known} of {total}. You can already play every song.")
        else:
            self._pl_say(f"All {total} song names loaded.")

    def _pl_waiting(self, seconds):
        known, total = self.playlists.progress()
        if seconds and known < total:
            mins = max(1, round(seconds / 60))
            self._pl_say(f"{known} of {total} names loaded. Spotify only answers so many at once. The rest "
                         f"load by themselves in about {mins} min. You can already play every song.")

    def _pl_loaded(self, ok, message):
        if ok:
            known, total = self.playlists.progress()
            more = "" if known >= total else " Song names are loading in the background."
            self._pl_say(f"Added {message}. Pick its songs from the overlay's playlist button.{more}")
        else:
            self._pl_say(message, error=True)
        if ok:
            self.overlay_toast.emit("Playlist ready ♪")

    def _pl_rebuild(self):
        while self.pl_rows.count():
            w = self.pl_rows.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        items = self.playlists.items
        if not items:
            e = QLabel("No playlists yet. Add songs above.")
            e.setObjectName("rowDesc")
            self.pl_rows.addWidget(e)
        act = self.playlists.active
        for pl in items:
            pid = pl["id"]
            on = act is not None and act["id"] == pid
            buttons = [("Songs", "eye", lambda _=False, i=pid: self._pl_show(i), "ghost")]
            if not on:
                buttons.append(("Use on overlay", "check", lambda _=False, i=pid: self.playlists.set_active(i), "ghost"))
            buttons += [("", "pencil", lambda _=False, i=pid: self._pl_rename(i), "ghost"),
                        ("", "trash", lambda _=False, i=pid: self.playlists.remove(i), "danger")]
            sub = "  •  ".join(x for x in (f"by {pl['owner']}" if pl.get("owner") else "",
                                           f"{len(pl['tracks'])} songs") if x)
            self.pl_rows.addWidget(self._lib_row(pl["name"], sub, "ON OVERLAY" if on else "", buttons))
        if self._pl_shown is None or self.playlists.get(self._pl_shown) is None:
            self._pl_shown = act["id"] if act else None
        self._pl_fill_songs()

    def _pl_rename(self, pid):
        from PySide6.QtWidgets import QInputDialog
        pl = self.playlists.get(pid)
        if pl is None:
            return
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=pl["name"])
        if ok:
            self.playlists.rename(pid, name)

    def _pl_show(self, pid):
        self._pl_shown = pid
        self._pl_fill_songs()

    def _pl_fill_songs(self):
        pl = self.playlists.get(self._pl_shown) if self._pl_shown else None
        if self.pl_songs_title is not None:
            self.pl_songs_title.setText(f"Songs in “{pl['name']}”" if pl else "Songs")
        fill_songs(self.pl_list, pl, self.pl_search.text(), getattr(self, "current_track", None))

    def _pl_play(self, item):
        tid = item.data(Qt.ItemDataRole.UserRole)
        if tid:
            playlists.play(tid)
            self.overlay_toast.emit("Playing ♪")
