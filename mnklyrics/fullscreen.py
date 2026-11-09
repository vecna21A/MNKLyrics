"""Full-screen Overlay: big, Spotify-style lyrics that fill a monitor, with a fully customizable look
(background from album art, a color, a gradient or your own image; fonts, colors, layout, controls)."""

import math
import os
import time

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QImage,
                           QLinearGradient, QPainter, QPainterPath, QPixmap, QRadialGradient)
from PySide6.QtWidgets import QWidget

from .config import DEFAULTS
from .controls import ControlsRenderer
from .renderer import LyricsRenderer
from .widgets import draw_icon

STYLE_KEYS = ("font_family", "font_size", "font_weight", "italic", "uppercase", "letter_spacing", "line_spacing",
              "context_scale", "lines_before", "lines_after", "align", "text_color", "context_opacity",
              "highlight_style", "highlight_color", "glow_enabled", "glow_color", "glow_strength", "glow_radius",
              "outline_enabled", "outline_color", "outline_width", "shadow_enabled", "shadow_color",
              "shadow_opacity", "shadow_offset", "animation", "entrance", "anim_speed")


def fmt_time(ms):
    s = max(0, int(ms / 1000))
    return f"{s // 60}:{s % 60:02d}"


def blur_image(img: QImage, amount: float) -> QImage:
    """Cheap, smooth blur: shrink then enlarge (amount 0..100)."""
    if amount <= 0 or img.isNull():
        return img
    factor = 1.5 + amount * 0.45
    small = img.scaled(max(4, int(img.width() / factor)), max(4, int(img.height() / factor)),
                       Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return small.scaled(img.width(), img.height(), Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


def cover(img: QImage, w: int, h: int) -> QImage:
    """Scale-and-crop an image to fill w×h."""
    s = img.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    return s.copy(int((s.width() - w) / 2), int((s.height() - h) / 2), w, h)


class FullscreenPainter:
    """Draws the full-screen view at any size; used by the real window and the settings preview."""

    def __init__(self, settings, renderer: LyricsRenderer):
        self.settings = settings
        self.renderer = renderer
        self.controls = ControlsRenderer()
        self.art = None
        self._art_id = 0
        self._bg = None
        self._img_cache = (None, None)
        self.layout = {}
        self._rkey = None

    @property
    def cfg(self):
        return self.settings.data["fullscreen"]

    def set_art(self, img):
        self.art = img if img is not None and not img.isNull() else None
        self._art_id += 1
        self._bg = None

    # ------------------------------------------------------------- layout
    def compute_layout(self, W, H):
        c = self.cfg
        mx, my = W * 0.06, H * 0.08
        show_title = c["show_title"]
        art_pos = c["art_position"]
        ctrl_h = 0.0
        self.controls.configure(playpause=True, prevnext=c["controls_prevnext"], volume=c["controls_volume"],
                                style="minimal", size=1.35 * H / 1080, color=c["text_color"], opacity=1.0)
        if c["show_controls"]:
            ctrl_h = self.controls.size()[1]
        bottom_h = (28 * H / 1080 if c["show_progress"] else 0) + (ctrl_h + 10 if ctrl_h else 0)
        top, bottom = my, H - my - bottom_h
        L = {"art": None, "title": None}
        lw = W * max(0.3, min(0.95, float(c["lyrics_width"])))
        if art_pos == "left":
            a = min(W * 0.24, H * 0.4)
            title_h = (a * 0.36) if show_title else 0
            ay = (top + bottom) / 2 - (a + title_h) / 2
            L["art"] = QRectF(mx, ay, a, a)
            if show_title:
                L["title"] = QRectF(mx, ay + a + a * 0.08, a, title_h)
            x0 = mx + a + W * 0.06
            lw = min(lw, W - mx - x0)
            lyr = QRectF(x0, top, lw, bottom - top)
        else:
            if art_pos == "top":
                a = H * 0.1
                L["art"] = QRectF(mx, top, a, a)
                if show_title:
                    L["title"] = QRectF(mx + a + W * 0.015, top, W * 0.6, a)
                top += a + H * 0.05
            elif show_title:
                L["title"] = QRectF(mx, top, W * 0.6, H * 0.08)
                top += H * 0.1
            lw = min(lw, W - 2 * mx)
            align = c["align"]
            x0 = mx if align == "left" else (W - mx - lw if align == "right" else (W - lw) / 2)
            lyr = QRectF(x0, top, lw, bottom - top)
        L["lyrics"] = lyr
        prog_w = (W - 2 * mx) if art_pos != "left" else (W - mx - lyr.left())
        prog_x = mx if art_pos != "left" else lyr.left()
        L["progress"] = QRectF(prog_x, H - my - bottom_h + 8, prog_w, 4) if c["show_progress"] else None
        if ctrl_h:
            cw, _ = self.controls.size()
            L["controls"] = QPointF(prog_x + prog_w / 2 - cw / 2, H - my - ctrl_h)
        self.layout = L
        return L

    def renderer_cfg(self, lyrics_w):
        cfg = dict(DEFAULTS["overlay"])
        for k in STYLE_KEYS:
            cfg[k] = self.cfg[k]
        cfg.update(bg_enabled=False, show_track_info=False, show_progress=False, padding=0, bg_border=False)
        probe = LyricsRenderer()
        probe.cfg = cfg
        cfg["width"] = int(lyrics_w + 2 * probe.outer_margin())
        return cfg

    def configure_renderer(self, lyrics_w, dpr):
        cfg = self.renderer_cfg(lyrics_w)
        key = (tuple(cfg.items()), round(dpr, 2))
        if key != self._rkey:
            self._rkey = key
            self.renderer.set_config(cfg, dpr)
            self.renderer.v_anchor = "center"
            self.renderer.card_enabled = False  # full screen already shows the cover, title and progress

    # ---------------------------------------------------------- background
    def _user_image(self, path):
        if self._img_cache[0] != path:
            img = QImage(path) if path and os.path.exists(path) else QImage()
            self._img_cache = (path, img)
        return self._img_cache[1]

    def background(self, w, h):
        """Background pixmap at device size w×h (cached until something about it changes)."""
        c = self.cfg
        key = (w, h, c["bg_mode"], c["bg_solid"], c["bg_grad1"], c["bg_grad2"], c["bg_grad_angle"], c["bg_image"],
               c["bg_blur"], c["bg_dim"], c["bg_vignette"], self._art_id)
        if self._bg is not None and self._bg[0] == key:
            return self._bg[1]
        img = QImage(max(1, w), max(1, h), QImage.Format.Format_RGB32)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        mode = c["bg_mode"]
        source = None
        if mode == "album" and self.art is not None:
            source = self.art
        elif mode == "image":
            src = self._user_image(c["bg_image"])
            source = src if not src.isNull() else None
        if source is not None:
            p.drawImage(0, 0, blur_image(cover(source, w, h), c["bg_blur"]))
        elif mode == "color":
            p.fillRect(0, 0, w, h, QColor(c["bg_solid"]))
        else:  # gradient (also the fallback while there's no album art / image yet)
            a = math.radians(float(c["bg_grad_angle"]))
            cx, cy, r = w / 2, h / 2, math.hypot(w, h) / 2
            g = QLinearGradient(cx - math.cos(a) * r, cy - math.sin(a) * r, cx + math.cos(a) * r, cy + math.sin(a) * r)
            g.setColorAt(0, QColor(c["bg_grad1"]))
            g.setColorAt(1, QColor(c["bg_grad2"]))
            p.fillRect(0, 0, w, h, QBrush(g))
        p.fillRect(0, 0, w, h, QColor(0, 0, 0, int(255 * max(0.0, min(0.95, float(c["bg_dim"]))))))
        if c["bg_vignette"]:
            v = QRadialGradient(QPointF(w / 2, h / 2), math.hypot(w, h) * 0.62)
            v.setColorAt(0.55, QColor(0, 0, 0, 0))
            v.setColorAt(1, QColor(0, 0, 0, 170))
            p.fillRect(0, 0, w, h, QBrush(v))
        p.end()
        pm = QPixmap.fromImage(img)
        self._bg = (key, pm)
        return pm

    # ---------------------------------------------------------- foreground
    def paint_foreground(self, p, W, H, position=0.0, duration=0.0, playing=False, volume=-1.0, dpr=1.0):
        c = self.cfg
        L = self.compute_layout(W, H)
        self.configure_renderer(L["lyrics"].width(), dpr)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = self.renderer
        if L["art"] is not None:
            ar = L["art"]
            p.setPen(Qt.PenStyle.NoPen)
            for k in range(1, 9):
                p.setBrush(QColor(0, 0, 0, int(28 * (1 - k / 9))))
                p.drawRoundedRect(ar.adjusted(-k, -k + 6, k, k + 6), 14 + k, 14 + k)
            clip = QPainterPath()
            clip.addRoundedRect(ar, ar.width() * 0.04, ar.width() * 0.04)
            p.save()
            p.setClipPath(clip)
            if self.art is not None:
                p.drawImage(ar, cover(self.art, int(ar.width() * dpr), int(ar.height() * dpr)))
            else:
                g = QLinearGradient(ar.topLeft(), ar.bottomRight())
                g.setColorAt(0, QColor(c["bg_grad1"]))
                g.setColorAt(1, QColor(c["bg_grad2"]))
                p.fillRect(ar, QBrush(g))
                s = ar.width() * 0.3
                draw_icon(p, "music", QRectF(ar.center().x() - s / 2, ar.center().y() - s / 2, s, s),
                          QColor(255, 255, 255, 170), max(1.5, s / 14))
            p.restore()
        if L["title"] is not None and r.title:
            tr = L["title"]
            big = L["art"] is None or c["art_position"] == "left"
            tf = QFont(c["font_family"])
            tf.setPixelSize(int(H * (0.032 if big else 0.03)))
            tf.setWeight(QFont.Weight.Bold)
            af = QFont(c["font_family"])
            af.setPixelSize(int(H * 0.021))
            fm, am = QFontMetricsF(tf), QFontMetricsF(af)
            y = tr.top() + fm.ascent() + (tr.height() - fm.height() - am.height() * 1.3) / 2 \
                if c["art_position"] == "top" else tr.top() + fm.ascent()
            p.setFont(tf)
            p.setPen(QColor(c["text_color"]))
            p.drawText(QPointF(tr.left(), y), fm.elidedText(r.title, Qt.TextElideMode.ElideRight, tr.width()))
            p.setFont(af)
            col = QColor(c["text_color"])
            col.setAlphaF(0.7)
            p.setPen(col)
            p.drawText(QPointF(tr.left(), y + am.height() * 1.35),
                       am.elidedText(r.artist, Qt.TextElideMode.ElideRight, tr.width()))
        r.paint(p, L["lyrics"], 1.0)
        if L["progress"] is not None:
            pr = L["progress"]
            frac = max(0.0, min(1.0, position / duration)) if duration > 0 else 0.0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 50))
            p.drawRoundedRect(pr, 2, 2)
            p.setBrush(QColor(c["highlight_color"]))
            p.drawRoundedRect(QRectF(pr.left(), pr.top(), pr.width() * frac, pr.height()), 2, 2)
            tf = QFont("Segoe UI")
            tf.setPixelSize(int(H * 0.0135))
            p.setFont(tf)
            col = QColor(c["text_color"])
            col.setAlphaF(0.65)
            p.setPen(col)
            p.drawText(QRectF(pr.left(), pr.bottom() + 4, 200, 24), Qt.AlignmentFlag.AlignLeft, fmt_time(position))
            p.drawText(QRectF(pr.right() - 200, pr.bottom() + 4, 200, 24), Qt.AlignmentFlag.AlignRight,
                       fmt_time(duration))
        if "controls" in L:
            self.controls.playing = playing
            self.controls.volume = volume
            self.controls.paint(p, L["controls"], 1.0, c["highlight_color"])
        else:
            self.controls.bar = QRectF()
            self.controls.rects = {}


class FullscreenWindow(QWidget):
    control = Signal(str)
    closed = Signal()

    def __init__(self, settings, clock):
        super().__init__(None)
        self.settings = settings
        self.clock = clock
        self.renderer = LyricsRenderer()
        self.painter = FullscreenPainter(settings, self.renderer)
        self.playing = False
        self.duration = 0.0
        self.volume = None
        self._last = time.perf_counter()
        self._moved = time.perf_counter()
        self._vol_drag = False
        self.close_hover = False
        self._closing = False
        self._anim = None
        self.setWindowTitle("MNKLyrics: Full screen")
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self._tick)
        settings.changed.connect(self._on_setting)

    def _on_setting(self, key):
        if key == "*" or key.startswith("fullscreen."):
            self.painter._rkey = None
            if self.isVisible():
                self.update()

    def set_art(self, data: bytes):
        self.painter.set_art(QImage.fromData(data) if data else None)
        self.update()

    def open(self):
        c = self.settings.data["fullscreen"]
        screens = QGuiApplication.screens()
        idx = int(c.get("screen", 0))
        screen = screens[idx] if 0 <= idx < len(screens) else QGuiApplication.primaryScreen()
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        if c.get("always_on_top", True):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setGeometry(screen.geometry())
        self._closing = False
        self.setWindowOpacity(0.0)
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self._apply_capture_visibility()
        self._moved = time.perf_counter()
        self._last = time.perf_counter()
        self.timer.start()
        self._fade(1.0, 380, QEasingCurve.Type.OutCubic)

    def _fade(self, end, ms, curve, done=None):
        """Fade the whole window in or out."""
        if self._anim is not None:
            self._anim.stop()
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(end)
        self._anim.setDuration(ms)
        self._anim.setEasingCurve(curve)
        if done is not None:
            self._anim.finished.connect(done)
        self._anim.start()

    def _apply_capture_visibility(self):
        try:
            import ctypes
            show = bool(self.settings.get("general.show_in_recordings", True))
            ctypes.windll.user32.SetWindowDisplayAffinity(ctypes.c_void_p(int(self.winId())), 0x00 if show else 0x11)
        except Exception:
            pass

    def close_view(self):
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        self.unsetCursor()

        def done():
            self.timer.stop()
            self.hide()
            self.setWindowOpacity(1.0)
            self._closing = False
            self.closed.emit()
        self._fade(0.0, 280, QEasingCurve.Type.InCubic, done)

    def toggle(self):
        self.close_view() if self.isVisible() and not self._closing else self.open()

    def closeEvent(self, e):  # Alt+F4
        e.ignore()
        self.close_view()

    # ----------------------------------------------------------------- loop
    def _tick(self):
        now = time.perf_counter()
        dt, self._last = now - self._last, now
        self.renderer.update(dt, self.clock.position() + self.settings.get("general.sync_offset_ms", 0))
        idle = now - self._moved > 2.5
        if self.settings.data["fullscreen"].get("hide_cursor", True) and idle and not self._vol_drag:
            self.setCursor(Qt.CursorShape.BlankCursor)
        self.update()

    def _close_rect(self):
        return QRectF(self.width() - 64, 20, 44, 44)

    def paintEvent(self, e):
        p = QPainter(self)
        dpr = self.devicePixelRatioF()
        p.drawPixmap(self.rect(), self.painter.background(int(self.width() * dpr), int(self.height() * dpr)))
        pos = min(self.clock.position(), self.duration) if self.duration else self.clock.position()
        level = self.volume.level if self.volume is not None else -1.0
        zoom = 1.0 + 0.035 * (1.0 - self.windowOpacity())  # lyrics settle in as the window fades in
        p.save()
        if zoom > 1.0005:
            p.translate(self.width() / 2, self.height() / 2)
            p.scale(zoom, zoom)
            p.translate(-self.width() / 2, -self.height() / 2)
        self.painter.paint_foreground(p, self.width(), self.height(), pos, self.duration, self.playing, level, dpr)
        p.restore()
        if time.perf_counter() - self._moved < 2.5:  # the close button shows while the mouse moves
            r = self._close_rect()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 60 if self.close_hover else 28))
            p.drawEllipse(r)
            draw_icon(p, "close", r.adjusted(14, 14, -14, -14), QColor("#FFFFFF"), 2.0)
        p.end()

    # ----------------------------------------------------------- interaction
    def mouseMoveEvent(self, e):
        self._moved = time.perf_counter()
        self.unsetCursor()
        pos = e.position()
        self.close_hover = self._close_rect().contains(pos)
        self.painter.controls.hover = self.painter.controls.hit(pos)
        if self._vol_drag and self.volume is not None:
            v = self.painter.controls.volume_at(pos)
            if v is not None:
                self.volume.set(v)

    def mousePressEvent(self, e):
        pos = e.position()
        if self._close_rect().contains(pos):
            self.close_view()
            return
        hit = self.painter.controls.hit(pos)
        if hit == "play":
            self.control.emit("toggle")
        elif hit in ("prev", "next"):
            self.control.emit("previous" if hit == "prev" else "next")
        elif hit == "mute" and self.volume is not None:
            self.volume.set(0.0 if self.volume.level > 0 else 0.6)
        elif hit == "volume" and self.volume is not None:
            self._vol_drag = True
            v = self.painter.controls.volume_at(pos)
            if v is not None:
                self.volume.set(v)

    def mouseReleaseEvent(self, e):
        self._vol_drag = False

    def wheelEvent(self, e):
        if self.volume is not None:
            self.volume.nudge(0.05 if e.angleDelta().y() > 0 else -0.05)

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key.Key_Escape, Qt.Key.Key_F11):
            self.close_view()
        elif k == Qt.Key.Key_Space:
            self.control.emit("toggle")
        elif k == Qt.Key.Key_Right:
            self.control.emit("next")
        elif k == Qt.Key.Key_Left:
            self.control.emit("previous")
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.volume is not None:
            self.volume.nudge(0.05 if k == Qt.Key.Key_Up else -0.05)
        else:
            super().keyPressEvent(e)


def demo_art(size=400) -> QImage:
    """An original abstract cover, used only for previews."""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor("#2A0B5E"))
    g.setColorAt(1, QColor("#0B3A5E"))
    p.fillRect(0, 0, size, size, QBrush(g))
    for i, (col, rad) in enumerate((("#FF4FA3", 0.38), ("#33D1FF", 0.26), ("#FFD166", 0.15))):
        c = QPointF(size * (0.5 + i * 0.045), size * (0.52 - i * 0.035))
        rg = QRadialGradient(c, size * rad)
        rg.setColorAt(0, QColor(col))
        rg.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(rg))
        p.drawEllipse(c, size * rad, size * rad)
    p.end()
    return img


class FullscreenPreview(QWidget):
    """Live miniature of the Full-screen Overlay for the settings page (sample lines only)."""

    VIRTUAL = (1920, 1080)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        from .demo import DEMO_LINES, DEMO_TOTAL
        from .lyrics import LyricLine
        self.total = DEMO_TOTAL
        self.renderer = LyricsRenderer()
        self.renderer.set_lyrics([LyricLine(t, s) for t, s in DEMO_LINES], True, DEMO_TOTAL)
        self.renderer.set_track("Midnight Signal", "MNKLyrics Demo", DEMO_TOTAL)
        self.painter = FullscreenPainter(settings, self.renderer)
        self.painter.set_art(demo_art())
        self.t0 = time.perf_counter()
        self._last = self.t0
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        settings.changed.connect(lambda k: (k == "*" or k.startswith("fullscreen.")) and self.update())

    def _tick(self):
        if not self.isVisible():
            return
        now = time.perf_counter()
        dt, self._last = now - self._last, now
        self.renderer.update(dt, ((now - self.t0) * 1000) % self.total)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        VW, VH = self.VIRTUAL
        k = min(self.width() / VW, self.height() / VH)
        w, h = VW * k, VH * k
        target = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        clip = QPainterPath()
        clip.addRoundedRect(target, 12, 12)
        p.setClipPath(clip)
        dpr = self.devicePixelRatioF()
        p.drawPixmap(target.toRect(), self.painter.background(int(w * dpr), int(h * dpr)))
        p.translate(target.topLeft())
        p.scale(k, k)
        pos = ((time.perf_counter() - self.t0) * 1000) % self.total
        self.painter.paint_foreground(p, VW, VH, pos, self.total, True, 0.6, dpr * k)
