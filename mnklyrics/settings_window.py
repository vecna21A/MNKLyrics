"""The control center: a frameless, animated settings window with live preview."""

import html
import math
import os
import re
import shutil
import time

from PySide6.QtCore import (QEasingCurve, QEvent, QParallelAnimationGroup, QPoint, QPointF, QPropertyAnimation,
                            QRect, QRectF, QSize, Qt, QTimer, QUrl, QVariantAnimation, Signal)
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QFontMetricsF, QGuiApplication, QImage,
                           QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient)
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractButton, QApplication, QBoxLayout, QComboBox, QCompleter, QFileDialog,
                               QFontComboBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
                               QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from .branding import paint_logo, render_logo
from .config import APP_NAME, DEFAULTS, HOTKEY_LABELS, PRESET_KEYS, STYLE_PRESETS, VERSION
from .configs import ConfigError, decode, find_code, same_look, sanitize, to_overlay
from .controls import (ControlsRenderer, controls_length, controls_origin, overlay_controls_cfg, overlay_extent,
                       split_overlay_rect)
from .fullscreen import FullscreenPreview
from .account import DiscordAccount
from .library import LibraryClient, LyricsLibrary
from .library_ui import LibraryPages
from .playlists import PlaylistStore
from .playlists_ui import PlaylistPages
from .lyrics import LyricLine
from .renderer import LyricsRenderer, contrast_color
from .themes import THEMES
from .widgets import (AA, Anim, Card, ColorSwatch, FlashHighlight, FlowLayout, GlowButton, GlowSlider,
                      GradientLabel, KeyCaptureButton, PositionPicker, ProgressBar, Segmented, Sidebar, Tile,
                      TileGroup, ToggleSwitch, accent_gradient, alpha, draw_icon, mix, paint_glow, row, theme)

from .demo import DEMO_LINES, DEMO_TOTAL


def fmt_time(ms):
    s = max(0, int(ms / 1000))
    return f"{s // 60}:{s % 60:02d}"


# ------------------------------------------------------------------ preview
class PreviewPanel(QWidget):
    HEIGHT = 250

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.renderer = LyricsRenderer()
        self.renderer.set_config(settings.overlay)
        self.renderer.set_lyrics([LyricLine(t, s) for t, s in DEMO_LINES], True)
        self.renderer.set_track("Midnight Signal", "MNKLyrics Demo", DEMO_TOTAL)
        self.t0 = time.perf_counter()
        self._last = self.t0
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.controls = ControlsRenderer()
        self.controls.configure(**overlay_controls_cfg(settings.overlay))
        settings.changed.connect(self._cfg)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.setMinimumHeight(0)

    def _cfg(self, key):
        if key == "*" or key.startswith("overlay."):
            self.renderer.set_config(self.settings.overlay, self.devicePixelRatioF())
            self.controls.configure(**overlay_controls_cfg(self.settings.overlay))

    def _tick(self):
        if not self.isVisible() or self.height() < 10:
            return
        now = time.perf_counter()
        dt, self._last = now - self._last, now
        self.renderer.update(dt, ((now - self.t0) * 1000) % DEMO_TOTAL)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        r = QRectF(self.rect()).adjusted(10, 6, -10, -8)
        if r.height() < 20:
            return
        clip = QPainterPath()
        clip.addRoundedRect(r, 16, 16)
        paint_glow(p, r, 16, theme.c("accent"), 0.35, 6)
        p.save()
        p.setClipPath(clip)
        sky = QLinearGradient(r.topLeft(), r.bottomLeft())
        sky.setColorAt(0, QColor("#0B1026"))
        sky.setColorAt(0.6, mix(QColor("#1B1036"), theme.c("accent2"), 0.25))
        sky.setColorAt(1, QColor("#05060C"))
        p.fillRect(r, QBrush(sky))
        t = time.perf_counter()
        sun = QPointF(r.center().x() + 180, r.top() + r.height() * 0.55)
        g = QRadialGradient(sun, 90)
        g.setColorAt(0, mix(theme.c("accent"), QColor("#FFFFFF"), 0.2))
        g.setColorAt(0.35, theme.c("accent", 0.45))
        g.setColorAt(1, theme.c("accent", 0))
        p.fillRect(r, QBrush(g))
        p.setPen(Qt.PenStyle.NoPen)
        for k, (col, amp, base) in enumerate(((QColor(20, 18, 48), 40, 0.62), (QColor(12, 10, 30), 55, 0.74))):
            poly = [QPointF(r.left(), r.bottom())]
            for i in range(0, 41):
                x = r.left() + r.width() * i / 40
                y = r.top() + r.height() * base - abs(math.sin(i * 0.7 + k * 2.1)) * amp * (0.5 + 0.5 * math.sin(i * 0.23 + k))
                poly.append(QPointF(x, y))
            poly.append(QPointF(r.right(), r.bottom()))
            p.setBrush(col)
            p.drawPolygon(QPolygonF(poly))
        p.setPen(QPen(theme.c("accent", 0.18), 1))
        horizon = r.top() + r.height() * 0.8
        for i in range(8):
            y = horizon + ((i + (t * 0.6) % 1) ** 2) * 3
            if y < r.bottom():
                p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        cfg = self.settings.overlay
        pw, ph = self.renderer.preferred_size()
        ew, eh = overlay_extent(cfg, self.controls)
        vw, vh = pw + ew, ph + eh
        k = min(1.0, (r.width() - 20) / vw, (r.height() - 30) / vh)
        p.translate(r.center().x() - vw * k / 2, r.center().y() - vh * k / 2 + 10)
        p.scale(k, k)
        full = QRectF(0, 0, vw, vh)
        self.renderer.paint(p, split_overlay_rect(full, cfg, self.controls), 1.0)
        if cfg.get("controls_enabled") and self.controls.size()[0] > 0:
            self.controls.playing, self.controls.volume = True, 0.6
            panel = self.renderer.panel_rect
            self.controls.paint(p, controls_origin(panel, cfg, self.controls), 1.0, cfg["highlight_color"],
                                controls_length(panel, cfg))
        p.restore()
        chip = QRectF(r.left() + 14, r.top() + 12, 104, 22)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 140))
        p.drawRoundedRect(chip, 11, 11)
        pulse = 0.5 + 0.5 * math.sin(t * 4)
        p.setBrush(QColor(255, 70, 90, int(150 + 105 * pulse)))
        p.drawEllipse(QPointF(chip.left() + 13, chip.center().y()), 4, 4)
        f = QFont("Segoe UI")
        f.setPixelSize(10)
        f.setWeight(QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        p.setFont(f)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(chip.adjusted(24, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, "LIVE PREVIEW")


# -------------------------------------------------------------- now playing
class NowPlayingCard(QWidget):
    command = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(270)
        self.anim = Anim(self)
        self.art = None
        self.blur = None
        self.dominant = None
        self.title, self.artist, self.album = "Nothing playing", "Start a song in Spotify", ""
        self.pos, self.dur, self.playing = 0.0, 0.0, False
        self.prev_btn = GlowButton(icon="prev", variant="ghost", parent=self)
        self.play_btn = GlowButton(icon="play", variant="round", parent=self)
        self.next_btn = GlowButton(icon="next", variant="ghost", parent=self)
        self.prev_btn.clicked.connect(lambda: self.command.emit("previous"))
        self.play_btn.clicked.connect(lambda: self.command.emit("toggle"))
        self.next_btn.clicked.connect(lambda: self.command.emit("next"))
        for b in (self.prev_btn, self.play_btn, self.next_btn):
            b.resize(b.sizeHint())
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.anim.set("art", 1.0)

    def _tick(self):
        # equalizer + breathing glow only animate while music plays
        if self.playing and self.isVisible():
            self.update()

    def set_track(self, track):
        if track is None:
            self.title, self.artist, self.album = "Nothing playing", "Start a song in Spotify", ""
            self.art = self.blur = self.dominant = None
        else:
            self.title, self.artist, self.album = track.title, track.artist or "Unknown artist", track.album
        self.anim.set("text", 0.0)
        self.anim.to("text", 1.0, 600)
        self.update()

    def set_art(self, data: bytes):
        img = QImage.fromData(data)
        if img.isNull():
            return
        self.art = img
        self.blur = img.scaled(18, 18, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        c = img.scaled(1, 1, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation).pixelColor(0, 0)
        h, s, v, _ = c.getHsv()
        self.dominant = QColor.fromHsv(max(0, h), max(s, 150), max(v, 210))
        self.anim.set("art", 0.0)
        self.anim.to("art", 1.0, 700)

    def set_progress(self, pos, dur, playing):
        changed = abs(pos - self.pos) > 400 or dur != self.dur
        self.pos, self.dur = pos, dur
        if playing != self.playing:
            self.playing = playing
            self.play_btn.icon_name = "pause" if playing else "play"
            self.play_btn.update()
            changed = True
        if changed and not playing:
            self.update()

    def resizeEvent(self, e):
        x0 = 24 + 190 + 28
        w = self.width() - x0 - 28
        y = self.height() - 76
        cx = x0 + w / 2
        self.play_btn.move(int(cx - self.play_btn.width() / 2), int(y - 4))
        self.prev_btn.move(int(cx - self.play_btn.width() / 2 - self.prev_btn.width() - 10), int(y + 5))
        self.next_btn.move(int(cx + self.play_btn.width() / 2 + 10), int(y + 5))

    def _static_layer(self, r, ar, dom):
        """Card background + artwork glow, cached until size, art, color or theme change."""
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, id(self.blur), theme.name, dom.name(),
               round(self.anim.get("art", 1.0), 2))
        if getattr(self, "_layer_key", None) != key:
            pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(AA)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            self._draw_background(p, r, dom)
            p.end()
            glow = QPixmap(pm.size())
            glow.setDevicePixelRatio(dpr)
            glow.fill(Qt.GlobalColor.transparent)
            p = QPainter(glow)
            p.setRenderHint(AA)
            paint_glow(p, ar, 16, dom, 1.7, 12)
            p.end()
            self._layer_key, self._layer, self._glow = key, pm, glow
        return self._layer, self._glow

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        dom = self.dominant or theme.c("accent")
        ar = QRectF(r.left() + 22, r.top() + 22, 190, 190)
        layer, glow = self._static_layer(r, ar, dom)
        p.drawPixmap(0, 0, layer)
        t = time.perf_counter()
        breathe = 0.5 + 0.5 * math.sin(t * 2.2) if self.playing else 0.3
        p.setOpacity(0.55 + 0.45 * breathe)
        p.drawPixmap(0, 0, glow)
        p.setOpacity(1.0)
        self._draw_dynamic(p, r, ar, dom, t)

    def _draw_background(self, p, r, dom):
        paint_glow(p, r, 20, dom, 0.6, 10)
        clip = QPainterPath()
        clip.addRoundedRect(r, 20, 20)
        p.save()
        p.setClipPath(clip)
        p.fillRect(r, theme.c("bg2"))
        if self.blur is not None:
            p.setOpacity(0.85 * self.anim.get("art", 1.0))
            side = max(r.width(), r.height()) * 1.2
            p.drawImage(QRectF(r.center().x() - side / 2, r.center().y() - side / 2, side, side), self.blur)
            p.setOpacity(1.0)
        else:
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, theme.c("accent", 0.25))
            g.setColorAt(1, theme.c("accent2", 0.18))
            p.fillRect(r, QBrush(g))
        shade = QLinearGradient(r.topLeft(), r.topRight())
        shade.setColorAt(0, alpha(theme.c("bg0"), 0.35))
        shade.setColorAt(1, alpha(theme.c("bg0"), 0.82))
        p.fillRect(r, QBrush(shade))
        p.restore()
        p.setPen(QPen(alpha(dom, 0.45), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 20, 20)

    def _draw_dynamic(self, p, r, ar, dom, t):
        # artwork
        aclip = QPainterPath()
        aclip.addRoundedRect(ar, 16, 16)
        p.save()
        p.setClipPath(aclip)
        if self.art is not None:
            p.setOpacity(self.anim.get("art", 1.0))
            p.drawImage(ar, self.art)
            p.setOpacity(1.0)
        else:
            g = QLinearGradient(ar.topLeft(), ar.bottomRight())
            g.setColorAt(0, theme.c("accent"))
            g.setColorAt(1, theme.c("accent2"))
            p.fillRect(ar, QBrush(g))
            draw_icon(p, "music", ar.adjusted(55, 55, -55, -55), QColor(10, 10, 18, 200), 5)
        p.restore()

        # text block
        x0 = ar.right() + 28
        tw = r.right() - x0 - 24
        ta = self.anim.get("text", 1.0)
        y = r.top() + 30
        f = QFont("Segoe UI")
        f.setPixelSize(11)
        f.setWeight(QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2)
        p.setFont(f)
        p.setPen(dom)
        p.drawText(QPointF(x0, y), "NOW PLAYING" if self.playing else ("PAUSED" if self.dur else "IDLE"))
        # equalizer bars
        bx = x0 + QFontMetricsF(f).horizontalAdvance("NOW PLAYING") + 18
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dom)
        for i in range(5):
            hgt = 3 + (abs(math.sin(t * (5 + i * 1.7) + i)) * 11 if self.playing else 2)
            p.drawRoundedRect(QRectF(bx + i * 6, y - hgt, 3.5, hgt), 1.5, 1.5)
        p.setOpacity(ta)
        off = (1 - ta) * 14
        tf = QFont("Segoe UI Variable Display")
        tf.setPixelSize(28)
        tf.setWeight(QFont.Weight.Bold)
        p.setFont(tf)
        p.setPen(QColor("#FFFFFF") if theme.dark else theme.c("text"))
        title = QFontMetricsF(tf).elidedText(self.title, Qt.TextElideMode.ElideRight, tw)
        p.drawText(QPointF(x0 + off, y + 42), title)
        af = QFont("Segoe UI")
        af.setPixelSize(16)
        p.setFont(af)
        p.setPen(mix(theme.c("text"), dom, 0.35))
        p.drawText(QPointF(x0 + off * 1.5, y + 70),
                   QFontMetricsF(af).elidedText(self.artist, Qt.TextElideMode.ElideRight, tw))
        if self.album:
            bf = QFont("Segoe UI")
            bf.setPixelSize(12)
            p.setFont(bf)
            p.setPen(theme.c("dim"))
            p.drawText(QPointF(x0 + off * 2, y + 92),
                       QFontMetricsF(bf).elidedText(self.album, Qt.TextElideMode.ElideRight, tw))
        p.setOpacity(1.0)

        # progress
        py = r.bottom() - 104
        bar = QRectF(x0, py, tw, 5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRoundedRect(bar, 2.5, 2.5)
        frac = min(1.0, self.pos / self.dur) if self.dur > 0 else 0.0
        if frac > 0:
            fill = QRectF(bar.left(), bar.top(), bar.width() * frac, bar.height())
            p.setBrush(alpha(dom, 0.3))
            p.drawRoundedRect(fill.adjusted(-2, -3, 2, 3), 5, 5)
            g = QLinearGradient(fill.topLeft(), fill.topRight())
            g.setColorAt(0, theme.c("accent"))
            g.setColorAt(1, dom)
            p.setBrush(QBrush(g))
            p.drawRoundedRect(fill, 2.5, 2.5)
            c = QPointF(fill.right(), fill.center().y())
            rg = QRadialGradient(c, 12)
            rg.setColorAt(0, alpha(dom, 0.8))
            rg.setColorAt(1, alpha(dom, 0))
            p.setBrush(QBrush(rg))
            p.drawEllipse(c, 12, 12)
            p.setBrush(QColor("#FFFFFF"))
            p.drawEllipse(c, 4.5, 4.5)
        tf2 = QFont("Cascadia Mono")
        tf2.setPixelSize(11)
        p.setFont(tf2)
        p.setPen(theme.c("dim"))
        p.drawText(QRectF(bar.left(), bar.bottom() + 6, 80, 16), Qt.AlignmentFlag.AlignLeft, fmt_time(self.pos))
        p.drawText(QRectF(bar.right() - 80, bar.bottom() + 6, 80, 16), Qt.AlignmentFlag.AlignRight,
                   fmt_time(self.dur))


def paint_shadow(p, r, radius, depth):
    """Soft, neutral drop shadow under a rounded rect (used beneath the logo)."""
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    for k in range(depth, 0, -1):
        p.setBrush(QColor(0, 0, 0, int(70 * (1 - k / (depth + 1)) ** 2 / 2)))
        p.drawRoundedRect(r.adjusted(-k * 0.5, -k * 0.2 + depth * 0.25, k * 0.5, k * 0.7 + depth * 0.25),
                          radius + k * 0.5, radius + k * 0.5)
    p.restore()


# ----------------------------------------------------------------- credits
DEV_NAME = "Vladimir"
DEV_DISCORD = "vladimir.hm"
DISCORD_SERVER = "discord.gg/hG48dMH8Pm"


class CreditsHero(QWidget):
    """Banner with the glowing logo, app name and author."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(230)
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(lambda: self.isVisible() and self.update())
        self.timer.start()
        self._logo = None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        r = QRectF(self.rect()).adjusted(10, 8, -10, -8)
        t = time.perf_counter()
        paint_glow(p, r, 20, QColor("#7B5CFF"), 0.7, 10)
        clip = QPainterPath()
        clip.addRoundedRect(r, 20, 20)
        p.save()
        p.setClipPath(clip)
        bg = QLinearGradient(r.topLeft(), r.bottomRight())
        bg.setColorAt(0, QColor("#150F35"))
        bg.setColorAt(1, QColor("#07080F"))
        p.fillRect(r, QBrush(bg))
        for fx, col, a in ((0.15 + 0.03 * math.sin(t * 0.7), "#00E5FF", 0.22), (0.85, "#FF2E97", 0.2),
                           (0.5 + 0.1 * math.cos(t * 0.5), "#7B5CFF", 0.28)):
            g = QRadialGradient(QPointF(r.left() + r.width() * fx, r.top() + r.height() * 0.3), r.width() * 0.45)
            g.setColorAt(0, alpha(QColor(col), a))
            g.setColorAt(1, alpha(QColor(col), 0))
            p.fillRect(r, QBrush(g))
        p.restore()
        p.setPen(QPen(alpha(QColor("#7B5CFF"), 0.5), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 20, 20)

        size = 132
        tile = QRectF(r.left() + 44, r.center().y() - size / 2, size, size)
        paint_shadow(p, tile, size * 0.225, 14)
        dpr = self.devicePixelRatioF()
        if self._logo is None or self._logo.devicePixelRatio() != dpr:
            self._logo = render_logo(int(size * dpr))
            self._logo.setDevicePixelRatio(dpr)
        p.drawImage(tile.topLeft(), self._logo)

        x = tile.right() + 40
        f = QFont("Segoe UI Variable Display")
        f.setPixelSize(46)
        f.setWeight(QFont.Weight.Black)
        path = QPainterPath()
        fm = QFontMetricsF(f)
        path.addText(x, r.center().y() - 6, f, APP_NAME)
        br = path.boundingRect()
        for wdt, a in ((12, 0.05), (7, 0.09), (3, 0.14)):
            p.strokePath(path, QPen(alpha(QColor("#7B5CFF"), a), wdt, Qt.PenStyle.SolidLine,
                                    Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        g = QLinearGradient(br.topLeft(), br.topRight())
        g.setColorAt(0, QColor("#FFFFFF"))
        g.setColorAt(0.45, QColor("#00E5FF"))
        g.setColorAt(0.75, QColor("#7B5CFF"))
        g.setColorAt(1, QColor("#FF2E97"))
        p.fillPath(path, QBrush(g))

        sf = QFont("Segoe UI")
        sf.setPixelSize(13)
        p.setFont(sf)
        p.setPen(QColor(255, 255, 255, 150))
        p.drawText(QPointF(x + 2, r.center().y() + 22), f"Version {VERSION}  •  Real-time lyrics overlay  •  Works with Spotify")

        mf = QFont("Segoe UI")
        mf.setPixelSize(17)
        mf.setWeight(QFont.Weight.DemiBold)
        p.setFont(mf)
        made = "Made by "
        p.setPen(QColor(255, 255, 255, 220))
        y = r.center().y() + 56
        p.drawText(QPointF(x + 2, y), made)
        nx = x + 2 + QFontMetricsF(mf).horizontalAdvance(made)
        npath = QPainterPath()
        bf = QFont(mf)
        bf.setWeight(QFont.Weight.Bold)
        npath.addText(nx, y, bf, DEV_NAME)
        nb = npath.boundingRect()
        ng = QLinearGradient(nb.topLeft(), nb.topRight())
        ng.setColorAt(0, QColor("#00E5FF"))
        ng.setColorAt(1, QColor("#FF2E97"))
        p.strokePath(npath, QPen(alpha(QColor("#FF2E97"), 0.25), 4, Qt.PenStyle.SolidLine,
                                 Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.fillPath(npath, QBrush(ng))


# ---------------------------------------------------------------- chrome
class WindowButton(GlowButton):
    def __init__(self, icon, danger=False, parent=None):
        super().__init__(icon=icon, variant="danger" if danger else "ghost", parent=parent)
        self.setFixedSize(40, 34)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        hov = self.anim.get("hover")
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        col = theme.c("danger") if self.variant == "danger" else theme.c("accent")
        if hov > 0.01:
            paint_glow(p, r, 8, col, hov * 0.8, 5)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(alpha(col, 0.85 * hov) if self.variant == "danger" else alpha(col, 0.15 * hov))
            p.drawRoundedRect(r, 8, 8)
        fg = mix(theme.c("dim"), QColor("#FFFFFF") if self.variant == "danger" else theme.c("accent"), hov)
        draw_icon(p, self.icon_name, QRectF(r.center().x() - 7, r.center().y() - 7, 14, 14), fg, 1.6)


class TitleBar(QWidget):
    minimize = Signal()
    close = Signal()

    def __init__(self, parent=None, title=APP_NAME, subtitle="Real-time lyrics overlay · Works with Spotify",
                 logo=None):
        super().__init__(parent)
        self.logo_renderer = logo or render_logo
        self.setFixedHeight(58)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 0, 12, 0)
        lay.addSpacing(44)
        col = QVBoxLayout()
        col.setSpacing(0)
        t = QLabel(title)
        t.setStyleSheet("font-size: 15px; font-weight: 700;")
        s = QLabel(subtitle)
        s.setObjectName("dim")
        s.setStyleSheet("font-size: 11px;")
        col.addStretch(1)
        col.addWidget(t)
        col.addWidget(s)
        col.addStretch(1)
        lay.addLayout(col)
        lay.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("dim")
        lay.addWidget(self.status)
        lay.addSpacing(10)
        self.update_btn = GlowButton("Update available", icon="download")
        self.update_btn.hide()
        lay.addWidget(self.update_btn)
        lay.addSpacing(6)
        mn = WindowButton("min", parent=self)
        cl = WindowButton("close", danger=True, parent=self)
        mn.clicked.connect(self.minimize)
        cl.clicked.connect(self.close)
        lay.addWidget(mn)
        lay.addWidget(cl)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        t = time.perf_counter()
        c = QPointF(40, self.height() / 2)
        rr = 18
        tile = QRectF(c.x() - rr, c.y() - rr, rr * 2, rr * 2)
        paint_shadow(p, tile, rr * 2 * 0.225, 6)
        dpr = self.devicePixelRatioF()
        if getattr(self, "_logo_dpr", None) != dpr:
            self._logo = self.logo_renderer(int(rr * 2 * dpr))
            self._logo.setDevicePixelRatio(dpr)
            self._logo_dpr = dpr
        p.drawImage(tile.topLeft(), self._logo)
        p.setPen(QPen(theme.c("border"), 1))
        p.drawLine(QPointF(0, self.height() - 0.5), QPointF(self.width(), self.height() - 0.5))


class AmbientRoot(QWidget):
    """Rounded window body with a slowly drifting aurora behind the UI."""

    SIDEBAR = 232

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.t0 = time.perf_counter()
        self.timer = QTimer(self)
        self.timer.setInterval(90)  # the aurora drifts slowly; ~11 fps is plenty
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._blobs = None
        theme.changed.connect(self._invalidate)

    def _invalidate(self):
        self._blobs = None
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._blobs = None

    def _tick(self):
        if self.isVisible() and not self.window().isMinimized() and self.settings.get("general.ui_effects"):
            self._blobs = self._render_blobs()
            self.update(0, 0, self.width(), 60)
            self.update(0, 0, self.SIDEBAR, self.height())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, 18, 18)
        p.setClipPath(path)
        p.fillRect(r, theme.c("bg0"))
        side = QRectF(0, 0, self.SIDEBAR, r.height())
        p.fillRect(side, theme.c("bg1"))
        if self._blobs is None:
            self._blobs = self._render_blobs()
        p.drawImage(r, self._blobs)
        p.setPen(QPen(theme.c("border"), 1))
        p.drawLine(QPointF(self.SIDEBAR - 0.5, 58), QPointF(self.SIDEBAR - 0.5, r.height()))

    def _render_blobs(self):
        """Low-resolution aurora layer, scaled up when drawn (cheap and naturally soft)."""
        r = QRectF(self.rect())
        t = time.perf_counter() - self.t0 if self.settings.get("general.ui_effects") else 0.0
        img = QImage(max(1, int(r.width() / 6)), max(1, int(r.height() / 6)), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        ip = QPainter(img)
        ip.setRenderHint(AA)
        ip.setPen(Qt.PenStyle.NoPen)
        w, h = img.width(), img.height()
        blobs = ((theme.c("accent"), 0.12, 0.15, 0.55, 0.13), (theme.c("accent2"), 0.85, 0.1, 0.5, 0.17),
                 (theme.c("accent"), 0.6, 0.95, 0.6, 0.11))
        strength = 0.22 if theme.dark else 0.12
        for i, (col, fx, fy, fr, sp) in enumerate(blobs):
            cx = w * (fx + 0.08 * math.sin(t * sp * 2 + i))
            cy = h * (fy + 0.08 * math.cos(t * sp * 1.6 + i * 2))
            rad = max(w, h) * fr
            g = QRadialGradient(QPointF(cx, cy), rad)
            g.setColorAt(0, alpha(col, strength))
            g.setColorAt(1, alpha(col, 0))
            ip.setBrush(QBrush(g))
            ip.drawEllipse(QPointF(cx, cy), rad, rad)
        ip.end()
        return img


class ThemeReveal(QWidget):
    """Circular reveal: the old theme is drawn on top with a growing hole."""

    def __init__(self, parent, pixmap, origin: QPointF):
        super().__init__(parent)
        self.pix = pixmap
        self.origin = origin
        self.radius = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(parent.rect())
        corners = [QPointF(0, 0), QPointF(self.width(), 0), QPointF(0, self.height()), QPointF(self.width(), self.height())]
        self.max_r = max(math.hypot(c.x() - origin.x(), c.y() - origin.y()) for c in corners)
        self.a = QVariantAnimation(self)
        self.a.setStartValue(0.0)
        self.a.setEndValue(1.0)
        self.a.setDuration(750)
        self.a.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.a.valueChanged.connect(self._step)
        self.a.finished.connect(self.deleteLater)
        self.show()
        self.raise_()
        self.a.start()

    def _step(self, v):
        self.radius = float(v) * self.max_r
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        outside = QPainterPath()
        outside.addRect(QRectF(self.rect()))
        hole = QPainterPath()
        hole.addEllipse(self.origin, self.radius, self.radius)
        p.setClipPath(outside.subtracted(hole))
        p.drawPixmap(0, 0, self.pix)
        p.setClipping(False)
        for k, a in ((14, 0.08), (8, 0.15), (3, 0.5)):
            p.setPen(QPen(theme.c("accent", a), k))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(self.origin, self.radius, self.radius)


class SizeGrip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)

    def mousePressEvent(self, e):
        handle = self.window().windowHandle()
        if handle:
            handle.startSystemResize(Qt.Edge.BottomEdge | Qt.Edge.RightEdge)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        p.setPen(QPen(theme.c("dim", 0.7), 1.4))
        for k in (5, 9, 13):
            p.drawLine(QPointF(16, 16 - k), QPointF(16 - k, 16))


class PageTransition(QWidget):
    """Draws the old and new page snapshots sliding/fading over the real content, then removes itself."""

    done = Signal()

    def __init__(self, parent, rect, old, new, direction, axis="y"):
        super().__init__(parent)
        self.old, self.new, self.dir, self.axis = old, new, direction, axis
        self.t = 0.0
        self.setGeometry(rect)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)  # Qt can skip painting what's underneath
        self.anim = QVariantAnimation(self)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setDuration(300)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(self._step)
        self.anim.finished.connect(self.finish)
        self.show()
        self.raise_()
        self.anim.start()

    def _step(self, v):
        self.t = float(v)
        self.repaint()  # paint immediately on each animation tick — no coalescing, no skipped frames

    def finish(self):
        if self.anim.state() == QVariantAnimation.State.Running:
            self.anim.stop()
        if self.isVisible():
            self.hide()
            self.deleteLater()
            self.done.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        t = self.t
        shift = (34 if self.axis == "x" else 26) * self.dir

        def at(d):
            return QPointF(d, 0) if self.axis == "x" else QPointF(0, d)
        p.drawPixmap(at(-shift * t * 0.6), self.old)
        p.setOpacity(t)
        p.drawPixmap(at(shift * (1 - t)), self.new)


class PreviewArea(QWidget):
    """The live preview above the style pages. It slides open on pages that change the look and shows
    the overlay preview, or the Full-screen preview on the Full screen page."""

    HEIGHT = 250

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.overlay = PreviewPanel(settings)
        self.full = FullscreenPreview(settings)
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.overlay)
        self.stack.addWidget(self.full)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.stack)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(0)
        self.setMaximumHeight(self.HEIGHT)

    def sizeHint(self):
        # Fixed vertical policy + animated maximumHeight = smooth expand/collapse
        return QSize(600, self.maximumHeight())

    def set_mode(self, mode):
        self.stack.setCurrentIndex(1 if mode == "fullscreen" else 0)


class ClipboardBanner(QWidget):
    """Offers a one-click import when a share code someone sent you is on the clipboard."""

    import_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.code = ""
        self.dismissed = set()
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 10, 10, 10)
        h.setSpacing(10)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        h.addWidget(self.label, 1)
        go = GlowButton("Import", icon="download")
        go.clicked.connect(lambda: (self.dismissed.add(self.code), self.hide(), self.import_clicked.emit(self.code)))
        no = GlowButton(icon="close", variant="ghost")
        no.setToolTip("Dismiss")
        no.clicked.connect(lambda: (self.dismissed.add(self.code), self.hide()))
        h.addWidget(go)
        h.addWidget(no)
        self.hide()

    def offer(self, code, name, author):
        if code in self.dismissed:
            return
        self.code = code
        by = f" by <b>{html.escape(author)}</b>" if author else ""  # names come from other people: escape
        self.label.setText(f"Share code on your clipboard: <b>“{html.escape(name)}”</b>{by}. Import it?")
        self.show()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -7)
        p.setPen(QPen(theme.c("accent", 0.8), 1.2))
        p.setBrush(mix(theme.c("bg2"), theme.c("accent"), 0.12))
        p.drawRoundedRect(r, 12, 12)


class ConfigCard(QAbstractButton):
    """Gallery card for a config: a large preview, its name, and Share / ⋯ buttons that appear on hover.
    Clicking the card applies the look."""

    W, H, PH = 244, 212, 136  # card size and preview height

    def __init__(self, name, values, meta, active, window, builtin=False):
        super().__init__()
        self.name, self.values, self.meta, self.active = name, values, meta, active
        self.anim = Anim(self)
        self._preview = None
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to apply this look")
        self.clicked.connect(lambda: window.apply_preset(name))
        self.share = GlowButton(icon="share", variant="ghost", parent=self)
        self.more = GlowButton(icon="more", variant="ghost", parent=self)
        self.share.setToolTip("Share code or file")
        self.more.setToolTip("Save a copy" if builtin else "Rename, update, duplicate or delete")
        for i, b in enumerate((self.more, self.share)):
            b.setFixedSize(40, 40)
            b.move(self.W - 14 - 40 - i * 40, 12)
            b.hide()
        if builtin:
            self.share.clicked.connect(lambda: window._builtin_share(name))
            self.more.clicked.connect(lambda: window._builtin_more_menu(name, self.more))
        else:
            self.share.clicked.connect(lambda: window._config_share_menu(name, self.share))
            self.more.clicked.connect(lambda: window._config_more_menu(name, self.more))

    def enterEvent(self, e):
        self.anim.to("hover", 1.0, 200)
        self.share.show()
        self.more.show()

    def leaveEvent(self, e):
        self.anim.to("hover", 0.0, 320)
        self.share.hide()
        self.more.hide()

    def _preview_pixmap(self, w, h):
        dpr = self.devicePixelRatioF()
        if self._preview is None or self._preview[0] != (w, h, dpr):
            pm = QPixmap(int(w * dpr), int(h * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pm)
            paint_look(painter, QRectF(0, 0, w, h), self.values, two_lines=True)
            painter.end()
            self._preview = ((w, h, dpr), pm)
        return self._preview[1]

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        hov = self.anim.get("hover")
        r = QRectF(self.rect()).adjusted(6, 6 - 3 * hov, -6, -6 - 3 * hov)
        ring = 1.0 if self.active else 0.0
        paint_glow(p, r, 16, theme.c("accent"), 0.25 * hov + 0.9 * ring, 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, int(40 + 50 * hov)))
        p.drawRoundedRect(r.translated(0, 4 + 3 * hov), 16, 16)
        p.setBrush(theme.c("bg2"))
        p.drawRoundedRect(r, 16, 16)
        # preview (rounded top)
        prev = QRectF(r.left(), r.top(), r.width(), self.PH)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(prev.left(), prev.top(), prev.width(), prev.height() + 16), 16, 16)
        p.save()
        p.setClipPath(clip)
        p.setClipRect(prev, Qt.ClipOperation.IntersectClip)
        p.drawPixmap(prev.topLeft(), self._preview_pixmap(prev.width(), prev.height()))
        p.restore()
        if self.active:
            f = QFont("Segoe UI")
            f.setPixelSize(10)
            f.setWeight(QFont.Weight.Bold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1)
            chip = QRectF(prev.left() + 12, prev.top() + 12, 58, 20)
            p.setBrush(QBrush(accent_gradient(chip)))
            p.drawRoundedRect(chip, 10, 10)
            p.setFont(f)
            p.setPen(QColor("#0A0A12") if theme.dark else QColor("#FFFFFF"))
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, "IN USE")
        # name + meta
        nf = QFont("Segoe UI")
        nf.setPixelSize(15)
        nf.setWeight(QFont.Weight.DemiBold)
        p.setFont(nf)
        p.setPen(mix(theme.c("text"), theme.c("accent"), max(hov * 0.5, ring * 0.6)))
        tr = QRectF(r.left() + 16, prev.bottom() + 12, r.width() - 32, 22)
        p.drawText(tr, Qt.AlignmentFlag.AlignVCenter,
                   QFontMetricsF(nf).elidedText(self.name, Qt.TextElideMode.ElideRight, tr.width()))
        mf = QFont("Segoe UI")
        mf.setPixelSize(11)
        p.setFont(mf)
        p.setPen(theme.c("dim"))
        p.drawText(QRectF(tr.left(), tr.bottom() + 2, tr.width(), 18), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetricsF(mf).elidedText(self.meta, Qt.TextElideMode.ElideRight, tr.width()))
        # border
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(mix(theme.c("border"), theme.c("accent"), max(hov * 0.6, ring)), 1.2 + ring))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 16, 16)


class AddTile(QAbstractButton):
    """Dashed 'action' tile that sits in the gallery (new config / import)."""

    def __init__(self, icon, title, subtitle, parent=None):
        super().__init__(parent)
        self.icon_name, self.title, self.subtitle = icon, title, subtitle
        self.anim = Anim(self)
        self.setFixedSize(ConfigCard.W, ConfigCard.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, e):
        self.anim.to("hover", 1.0, 200)

    def leaveEvent(self, e):
        self.anim.to("hover", 0.0, 320)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        hov = self.anim.get("hover")
        r = QRectF(self.rect()).adjusted(6, 6 - 3 * hov, -6, -6 - 3 * hov)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mix(theme.c("bg2"), theme.c("accent"), 0.05 + 0.06 * hov))
        p.drawRoundedRect(r, 16, 16)
        pen = QPen(mix(theme.c("border"), theme.c("accent"), 0.3 + 0.7 * hov), 1.6, Qt.PenStyle.DashLine)
        pen.setDashPattern([5, 4])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 15, 15)
        c = QPointF(r.center().x(), r.top() + r.height() * 0.38)
        rad = 26 + 3 * hov
        paint_glow(p, QRectF(c.x() - rad, c.y() - rad, rad * 2, rad * 2), rad, theme.c("accent"), 0.5 * hov, 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(accent_gradient(QRectF(c.x() - rad, c.y() - rad, rad * 2, rad * 2), 0.2 + 0.8 * hov)))
        p.drawEllipse(c, rad, rad)
        draw_icon(p, self.icon_name, QRectF(c.x() - 11, c.y() - 11, 22, 22),
                  mix(theme.c("accent"), QColor("#0A0A12") if theme.dark else QColor("#FFFFFF"), hov), 2.2)
        tf = QFont("Segoe UI")
        tf.setPixelSize(14)
        tf.setWeight(QFont.Weight.DemiBold)
        p.setFont(tf)
        p.setPen(theme.c("text"))
        p.drawText(QRectF(r.left(), c.y() + rad + 14, r.width(), 22), Qt.AlignmentFlag.AlignCenter, self.title)
        sf = QFont("Segoe UI")
        sf.setPixelSize(11)
        p.setFont(sf)
        p.setPen(theme.c("dim"))
        p.drawText(QRectF(r.left() + 14, c.y() + rad + 36, r.width() - 28, 34),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                   self.subtitle)


def section_header(title, subtitle=""):
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(6, 18, 6, 6)
    v.setSpacing(2)
    t = QLabel(title)
    t.setStyleSheet("font-size: 19px; font-weight: 700;")
    v.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("dim")
        s.setWordWrap(True)
        v.addWidget(s)
    w.title_label = t
    return w


class SearchBox(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("search")
        self.setClearButtonEnabled(True)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        draw_icon(p, "search", QRectF(24, self.height() / 2 - 7, 14, 14),
                  theme.c("accent") if self.hasFocus() else theme.c("dim"), 1.6)


# ---------------------------------------------------------------- tile painters
def paint_look(p, r, cfg, two_lines=False):
    """Draw a miniature of how a config makes the lyrics look (sample text is placeholder)."""
    full = dict(DEFAULTS["overlay"])
    full.update(cfg)
    p.save()
    p.setRenderHint(AA)
    sky = QLinearGradient(r.topLeft(), r.bottomLeft())
    sky.setColorAt(0, QColor("#141830"))
    sky.setColorAt(1, QColor("#07080F"))
    p.fillRect(r, QBrush(sky))
    if two_lines:
        box = r.adjusted(16, 18, -16, -18)
    else:
        box = r.adjusted(12, 26, -12, -30)
    if full["bg_enabled"]:
        bgc = QColor(full["bg_color"])
        bgc.setAlphaF(min(1.0, full["bg_opacity"] + 0.1))
        p.setPen(QPen(alpha(QColor(full["highlight_color"]), 0.45), 1) if full["bg_border"] else Qt.PenStyle.NoPen)
        p.setBrush(bgc)
        rad = min(full["bg_radius"], 14)
        p.drawRoundedRect(box, rad, rad)

    def font(px):
        f = QFont(full["font_family"])
        f.setPixelSize(px)
        f.setWeight(QFont.Weight(int(round(full["font_weight"] / 100) * 100)))
        f.setItalic(full["italic"])
        return f

    def x_for(width, fm_w):
        if full["align"] == "left":
            return box.left() + 12
        if full["align"] == "right":
            return box.right() - 12 - fm_w
        return box.center().x() - fm_w / 2

    size = 19 if two_lines else 15
    f = font(size)
    fm = QFontMetricsF(f)
    text = "Glow tonight"
    if full["uppercase"]:
        text = text.upper()
    cy = box.center().y() + (fm.ascent() / 2 - 2) + (8 if two_lines else 0)
    if two_lines:  # a dimmer "other line" above, like the real overlay
        cf = font(max(9, int(size * full["context_scale"])))
        cfm = QFontMetricsF(cf)
        ctext = "Every heartbeat" if not full["uppercase"] else "EVERY HEARTBEAT"
        cpath = QPainterPath()
        cpath.addText(x_for(box.width(), cfm.horizontalAdvance(ctext)), cy - fm.height() * 0.95, cf, ctext)
        p.fillPath(cpath, alpha(QColor(full["text_color"]), full["context_opacity"]))
    path = QPainterPath()
    path.addText(x_for(box.width(), fm.horizontalAdvance(text)), cy, f, text)
    _paint_sample_line(p, path, full)
    p.restore()


def _paint_sample_line(p, path, full):
    hl = QColor(full["highlight_color"])
    style = full["highlight_style"]
    if style == "pill":
        br = path.boundingRect().adjusted(-8, -4, 8, 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(hl)
        p.drawRoundedRect(br, br.height() / 2, br.height() / 2)
    if full["glow_enabled"] or style == "glow":
        gc = hl if style == "glow" else QColor(full["glow_color"])
        for w, a in ((9, 0.07), (5, 0.12), (2.5, 0.2)):
            p.strokePath(path, QPen(alpha(gc, a * (1.4 if style == "glow" else 1)), w, Qt.PenStyle.SolidLine,
                                    Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if full["outline_enabled"]:
        p.strokePath(path, QPen(QColor(full["outline_color"]), full["outline_width"] * 1.5))
    if style == "pill":
        fill = contrast_color(full["highlight_color"])
    elif style in ("color", "underline", "karaoke"):
        fill = hl
    else:
        fill = QColor(full["text_color"])
    if style == "karaoke":
        p.fillPath(path, QColor(full["text_color"]))
        p.save()
        br = path.boundingRect()
        p.setClipRect(QRectF(br.left(), br.top() - 4, br.width() * 0.6, br.height() + 8))
        p.fillPath(path, hl)
        p.restore()
    else:
        p.fillPath(path, fill)
    if style == "underline":
        br = path.boundingRect()
        p.fillRect(QRectF(br.left(), br.bottom() + 3, br.width() * 0.7, 2.5), hl)


def preset_painter(cfg):
    """Tile painter: a mini preview plus the tile's label underneath."""
    def paint(p, r, tile):
        paint_look(p, r, cfg)
        sel = tile.anim.get("sel")
        lf = QFont("Segoe UI")
        lf.setPixelSize(12)
        lf.setWeight(QFont.Weight.DemiBold)
        p.setFont(lf)
        p.setPen(mix(QColor("#FFFFFF"), theme.c("accent"), max(sel, tile.anim.get("hover"))))
        p.drawText(QRectF(r.left(), r.bottom() - 26, r.width(), 22), Qt.AlignmentFlag.AlignCenter, tile.label)
    return paint


def theme_painter(name):
    t = THEMES[name]

    def paint(p, r, tile):
        p.fillRect(r, QColor(t["bg0"]))
        g = QRadialGradient(QPointF(r.right() - 20, r.top() + 10), r.width() * 0.7)
        g.setColorAt(0, alpha(QColor(t["accent2"]), 0.35))
        g.setColorAt(1, alpha(QColor(t["accent2"]), 0))
        p.fillRect(r, QBrush(g))
        side = QRectF(r.left(), r.top(), r.width() * 0.26, r.height())
        p.fillRect(side, QColor(t["bg1"]))
        p.setPen(Qt.PenStyle.NoPen)
        for k in range(4):
            p.setBrush(QColor(t["accent"]) if k == 1 else QColor(t["border"]))
            p.drawRoundedRect(QRectF(side.left() + 8, side.top() + 14 + k * 13, side.width() - 16, 6), 3, 3)
        card = QRectF(side.right() + 10, r.top() + 14, r.right() - side.right() - 20, r.height() * 0.42)
        p.setBrush(QColor(t["bg2"]))
        p.setPen(QPen(QColor(t["border"]), 1))
        p.drawRoundedRect(card, 6, 6)
        bar = QRectF(card.left() + 8, card.top() + 10, card.width() * 0.7, 6)
        gg = QLinearGradient(bar.topLeft(), bar.topRight())
        gg.setColorAt(0, QColor(t["accent"]))
        gg.setColorAt(1, QColor(t["accent2"]))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(gg))
        p.drawRoundedRect(bar, 3, 3)
        p.setBrush(QColor(t["dim"]))
        p.drawRoundedRect(QRectF(card.left() + 8, card.top() + 24, card.width() * 0.45, 4), 2, 2)
        sel = tile.anim.get("sel")
        lf = QFont("Segoe UI")
        lf.setPixelSize(12)
        lf.setWeight(QFont.Weight.DemiBold)
        p.setFont(lf)
        p.setPen(mix(QColor(t["text"]), QColor(t["accent"]), max(sel, tile.anim.get("hover"))))
        p.drawText(QRectF(side.right(), r.bottom() - 30, r.width() - side.width(), 24), Qt.AlignmentFlag.AlignCenter,
                   name)
    return paint


# ------------------------------------------------------------------ window
class MainWindow(QWidget, LibraryPages, PlaylistPages):
    media_command = Signal(str)
    preview_lyrics = Signal(object)   # draft lines from the lyrics editor, or None to stop previewing
    lyrics_saved = Signal(str)        # an entry in "Your lyrics" changed: show it if that song is playing
    reload_lyrics = Signal()
    next_version = Signal()
    hotkey_capture = Signal(bool)
    overlay_toast = Signal(str)
    quit_requested = Signal()
    demo_toggled = Signal(bool)
    hidden = Signal()
    update_check = Signal()
    update_download = Signal()
    update_install = Signal()
    open_fullscreen = Signal()

    M = 16  # outer shadow margin

    def __init__(self, settings, configs=None, updater=None, library=None, playlist_store=None):
        super().__init__(None)
        self.settings = settings
        self.configs = configs
        self.updater = updater
        self.playlists = playlist_store if playlist_store is not None else PlaylistStore(settings)
        self.library = library if library is not None else LyricsLibrary()
        self.lib_client = LibraryClient()
        self.account = DiscordAccount()   # only used to share lyrics with the community
        self.clock = None            # the app's playback clock (used to time taps when syncing lyrics)
        self.current_track = None
        self._setters = {}
        self._search = []          # (label, (section, page), widget) for settings search
        self._page_keys = {}       # (section, page) -> setting keys shown on that page (for "Reset page")
        self._building = (0, 0)
        self.pages = {}            # page title -> (section, page)
        self.sections = []         # {"title", "header", "pages": [{"label", "widget", "name"}]}
        self._pending = {}
        self._flush = QTimer(self)
        self._flush.setSingleShot(True)
        self._flush.setInterval(45)
        self._flush.timeout.connect(self._flush_pending)
        self._closing = False
        self.quitting = False

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 820)
        self.setMinimumSize(900, 620)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(self.M, self.M, self.M, self.M)
        self.root = AmbientRoot(settings, self)
        outer.addWidget(self.root)
        rl = QVBoxLayout(self.root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        self.titlebar = TitleBar(self.root)
        self.titlebar.minimize.connect(self.showMinimized)
        self.titlebar.close.connect(self.hide_animated)
        self.titlebar.update_btn.clicked.connect(lambda: self.go_to("Updates"))
        rl.addWidget(self.titlebar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        rl.addLayout(body, 1)
        self.sidebar = Sidebar(self.root)
        body.addWidget(self.sidebar)
        self.search = SearchBox()
        self.search.setPlaceholderText("Search settings…   Ctrl+F")
        self.sidebar.lay.addSpacing(4)
        self.sidebar.lay.addWidget(self.search)
        QShortcut(QKeySequence.StandardKey.Find, self, activated=lambda: (self.search.setFocus(),
                                                                         self.search.selectAll()))
        self.setAcceptDrops(True)

        self.right_col = QWidget(self.root)
        right = QVBoxLayout(self.right_col)
        right.setContentsMargins(18, 10, 14, 6)
        right.setSpacing(0)
        body.addWidget(self.right_col, 1)
        self.clip_banner = ClipboardBanner(self.right_col)
        self.clip_banner.import_clicked.connect(lambda code: (self.go_to("Configs"), self._import_code(code)))
        right.addWidget(self.clip_banner)
        self.preview_area = PreviewArea(settings, self.right_col)
        self.preview = self.preview_area.overlay
        right.addWidget(self.preview_area)
        self.stack = QStackedWidget(self.right_col)
        right.addWidget(self.stack, 1)
        self._transition = None
        grip_row = QHBoxLayout()
        grip_row.addStretch(1)
        grip_row.addWidget(SizeGrip(self.right_col))
        right.addLayout(grip_row)

        self.preview_pages = {}    # page index -> "overlay" / "fullscreen"
        self._rows = []
        self._build_pages()
        self._build_search()
        self._tame_wheel()
        self.sidebar.changed.connect(self._go)
        self.sidebar.finish()
        self._set_preview(None, animate=False)

        settings.changed.connect(self._on_setting)
        theme.changed.connect(self._repaint_all)
        QGuiApplication.clipboard().dataChanged.connect(self._check_clipboard)

    # ------------------------------------------------------- clipboard
    def _check_clipboard(self):
        """If a share code someone sent is on the clipboard, offer to import it (once)."""
        if self.configs is None or not self.isVisible():
            return
        text = QGuiApplication.clipboard().text()
        if "MNK" not in text or len(text) > 5000:
            return
        code = find_code(text)
        if not code:
            return
        try:
            data = decode(code)
        except ConfigError:
            return
        known = [e["values"] for e in self.configs.items.values()] + [
            sanitize({**DEFAULTS["overlay"], **v}) for v in STYLE_PRESETS.values()]
        if any(same_look(data["values"], v) for v in known):
            return  # already have this look (e.g. you just copied your own code to share it)
        self.clip_banner.offer(code, data["name"], data["author"])

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            QTimer.singleShot(150, self._check_clipboard)

    # ------------------------------------------------------------ chrome
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        r = QRectF(self.rect()).adjusted(self.M, self.M, -self.M, -self.M)
        p.setPen(Qt.PenStyle.NoPen)
        for k in range(1, self.M + 1):
            p.setBrush(QColor(0, 0, 0, int(70 * (1 - k / (self.M + 1)) ** 2)))
            p.drawRoundedRect(r.adjusted(-k, -k + 4, k, k + 4), 18 + k, 18 + k)
        paint_glow(p, r, 18, theme.c("accent"), 0.5, 8)
        p.setPen(QPen(theme.c("border"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(-0.5, -0.5, 0.5, 0.5), 18, 18)

    def show_animated(self):
        if self.isMinimized():
            self.showNormal()
        if self.isVisible():
            self.raise_()
            self.activateWindow()
            return
        screen = QGuiApplication.primaryScreen().availableGeometry()
        if not getattr(self, "_placed", False):
            self.move(screen.center().x() - self.width() // 2, screen.center().y() - self.height() // 2)
            self._placed = True
        end = self.pos()
        self.setWindowOpacity(0.0)
        self.move(end + QPoint(0, 24))
        self.show()
        self.raise_()
        self.activateWindow()
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setDuration(320)
        move = QPropertyAnimation(self, b"pos", self)
        move.setStartValue(end + QPoint(0, 24))
        move.setEndValue(end)
        move.setDuration(420)
        move.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._open_anim = QParallelAnimationGroup(self)
        self._open_anim.addAnimation(fade)
        self._open_anim.addAnimation(move)
        self._open_anim.start()

    def hide_animated(self):
        if self._closing:
            return
        self._closing = True
        start = self.pos()
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setStartValue(self.windowOpacity())
        fade.setEndValue(0.0)
        fade.setDuration(220)
        move = QPropertyAnimation(self, b"pos", self)
        move.setStartValue(start)
        move.setEndValue(start + QPoint(0, 18))
        move.setDuration(220)
        move.setEasingCurve(QEasingCurve.Type.InCubic)
        group = QParallelAnimationGroup(self)
        group.addAnimation(fade)
        group.addAnimation(move)

        def done():
            self.hide()
            self.move(start)
            self.setWindowOpacity(1.0)
            self._closing = False
            self.hidden.emit()
        group.finished.connect(done)
        group.start()
        self._close_anim = group

    def closeEvent(self, e):
        if self.quitting:
            e.accept()
            return
        e.ignore()
        self.hide_animated()

    def _repaint_all(self):
        for w in self.findChildren(QWidget):
            w.update()
        self.update()

    def _go(self, i):
        """Switch pages with a snapshot cross-slide: two cached images animate instead of live widgets,
        so the transition runs at a steady 60 fps no matter how heavy the page is."""
        if self._transition is not None:
            self._transition.finish()
        old_index = self.stack.currentIndex()
        if old_index == i:
            return
        animate = self.isVisible() and not self.isMinimized()
        rect = self.right_col.geometry()
        old = self.root.grab(rect) if animate else None
        self.stack.setCurrentIndex(i)
        self._set_preview(self.preview_pages.get(i), animate=False)
        if i == getattr(self, "_library_page_index", -1):
            self._lib_page_shown()
        if not animate:
            return
        self.right_col.layout().activate()
        QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        new = self.root.grab(rect)
        self.preview.timer.stop()  # the live preview is hidden under the transition; let it rest
        self._transition = PageTransition(self.root, rect, old, new, 1 if i > old_index else -1)
        self._transition.done.connect(self._transition_done)

    def _transition_done(self):
        self._transition = None
        self.preview.timer.start()

    def _set_preview(self, mode, animate=True):
        """Show the preview area in ``mode`` ("overlay" / "fullscreen"), or hide it for None."""
        area = self.preview_area
        if mode:
            area.set_mode(mode)
        target = PreviewArea.HEIGHT if mode else 0
        if not animate:
            area.setMaximumHeight(target)
            area.setVisible(bool(mode))
            return
        area.setVisible(True)
        a = QPropertyAnimation(area, b"maximumHeight", self)
        a.setStartValue(area.maximumHeight())
        a.setEndValue(target)
        a.setDuration(420)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        if not mode:
            a.finished.connect(lambda: area.setVisible(area.maximumHeight() > 0))
        a.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._preview_anim = a

    def _tame_wheel(self):
        """Dropdowns and sliders only take the mouse wheel once clicked; otherwise the wheel scrolls the
        page, so scrolling past a setting never changes it by accident."""
        for i in range(self.stack.count()):
            page = self.stack.widget(i)
            for w in page.findChildren(QComboBox) + page.findChildren(GlowSlider):
                w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                w.installEventFilter(self)

    def eventFilter(self, obj, e):
        if (e.type() == QEvent.Type.Wheel and isinstance(obj, (QComboBox, GlowSlider))
                and not obj.hasFocus()):
            w = obj.parentWidget()
            while w is not None and not isinstance(w, QScrollArea):
                w = w.parentWidget()
            if w is not None:
                QApplication.sendEvent(w.verticalScrollBar(), e)
            return True
        return super().eventFilter(obj, e)

    def change_theme(self, name, origin=None):
        if name == theme.name:
            return
        pix = self.root.grab()
        if origin is None:
            origin = QPointF(self.root.width() / 2, self.root.height() / 2)
        theme.set_theme(name)
        QApplication.instance().setStyleSheet(theme.stylesheet())
        ThemeReveal(self.root, pix, origin)

    # ---------------------------------------------------------- binding
    def _register(self, key, setter):
        self._setters.setdefault(key, []).append(setter)
        self._page_keys.setdefault(self._building, set()).add(key)
        setter(self.settings.get(key))

    def _index(self, label, widget):
        self._search.append((label, self._building, widget))

    def _add_row(self, card, label, control, desc=None, value=None):
        w = row(label, control, desc, value)
        card.body.addWidget(w)
        self._index(label, w)
        return w

    def _on_setting(self, key):
        keys = list(self._setters) if key == "*" else [key]
        for k in keys:
            for s in self._setters.get(k, []):
                s(self.settings.get(k))
        if key in ("general.ui_theme", "*"):
            name = self.settings.get("general.ui_theme")
            if name != theme.name:
                self.change_theme(name, self.root.mapFromGlobal(self.cursor().pos()).toPointF())

    def _set_later(self, key, value):
        self._pending[key] = value
        if not self._flush.isActive():
            self._flush.start()

    def _flush_pending(self):
        pending, self._pending = self._pending, {}
        for k, v in pending.items():
            self.settings.set(k, v)

    def slider(self, card, label, key, lo, hi, step, fmt="{:.0f}", desc=None, mult=1.0):
        s = GlowSlider(lo, hi, step)
        val = QLabel()
        val.setObjectName("value")
        is_int = isinstance(DEFAULTS[key.split(".")[0]][key.split(".")[1]], int) and not isinstance(
            DEFAULTS[key.split(".")[0]][key.split(".")[1]], bool)

        def show(v):
            val.setText(fmt.format(v * mult))

        def setter(v):
            s.setValue(v)
            show(s.value())
        s.valueChanged.connect(lambda v: (show(v), self._set_later(key, int(round(v)) if is_int else float(v))))
        self._register(key, setter)
        self._add_row(card, label, s, desc, val)
        return s

    def toggle(self, card, label, key, desc=None):
        t = ToggleSwitch()
        t.toggled.connect(lambda c: self.settings.set(key, bool(c)))
        self._register(key, t.set_silent)
        self._add_row(card, label, t, desc)
        return t

    def color(self, card, label, key, desc=None):
        c = ColorSwatch()
        c.colorChanged.connect(lambda v: self._set_later(key, v))
        self._register(key, c.setColor)
        self._add_row(card, label, c, desc)
        return c

    def segmented(self, card, label, key, options, desc=None):
        s = Segmented(options)
        s.changed.connect(lambda v: self.settings.set(key, v))
        self._register(key, lambda v: s.setValue(v))
        self._add_row(card, label, s, desc)
        return s

    def tiles(self, card, key, options, columns=6, size=(128, 96)):
        g = TileGroup(columns)
        for value, label, icon in options:
            g.add(Tile(value, label, icon=icon, size=size))
        g.changed.connect(lambda v: self.settings.set(key, v))
        self._register(key, g.setValue)
        card.body.addWidget(g)
        return g

    # ------------------------------------------------------------ pages
    def _page(self, title, subtitle, icon, preview=None, section=None, reset=False):
        """Add a sidebar page. ``preview`` = "overlay" / "fullscreen" shows that live preview above it."""
        if section:
            self.sidebar.add_section(section)
        idx = self.sidebar.add(title, icon)
        self._building = idx
        self.pages[title] = idx
        if preview:
            self.preview_pages[idx] = preview
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 4, 8, 20)
        lay.setSpacing(4)
        head = QHBoxLayout()
        head.addWidget(GradientLabel(title, 28), 1)
        if reset:
            rb = GlowButton("Reset page", icon="refresh", variant="ghost")
            rb.setToolTip("Put every setting on this page back to its default")
            rb.clicked.connect(lambda _=False, i=idx, t=title: self._reset_page(i, t))
            head.addWidget(rb, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(head)
        self._index(title, inner)
        sub = QLabel(subtitle)
        sub.setObjectName("dim")
        sub.setWordWrap(True)
        sub.setContentsMargins(6, 0, 0, 8)
        lay.addWidget(sub)
        area.setWidget(inner)
        self.stack.addWidget(area)
        return lay

    def _cards_row(self, lay, *cards):
        """Cards side by side on wide windows, stacked when the window is narrow."""
        box = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        box.setSpacing(0)
        for c in cards:
            box.addWidget(c, 1)
        lay.addLayout(box)
        self._rows.append(box)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "_sheet", None) is not None:
            self._sheet.setGeometry(self.root.rect())
        wide = self.width() - 2 * self.M - self.sidebar.width() >= 900
        direction = QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        for box in self._rows:
            if box.direction() != direction:
                box.setDirection(direction)

    # ----------------------------------------------------------- search
    def _build_search(self):
        # cards are searchable by their titles too
        for i in range(self.stack.count()):
            for card in self.stack.widget(i).findChildren(Card):
                if card.title_text:
                    self._search.append((card.title_text, i, card))
        names = {i: self.sidebar.items[i].text() for i in range(len(self.sidebar.items))}
        self._search_lookup = {}
        entries = []
        for label, page, widget in self._search:
            text = label if label == names[page] else f"{label}   ·   {names[page]}"
            if text not in self._search_lookup:
                self._search_lookup[text] = (page, widget)
                entries.append(text)
        completer = QCompleter(entries, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setMaxVisibleItems(10)
        completer.popup().setObjectName("searchPopup")
        completer.activated.connect(self._jump)
        self.search.setCompleter(completer)
        self.search.returnPressed.connect(lambda: completer.popup().isVisible() or self._jump_first())

    def _jump_first(self):
        q = self.search.text().strip().lower()
        for text in self._search_lookup:
            if q and q in text.lower():
                self._jump(text)
                return

    def _jump(self, text):
        hit = self._search_lookup.get(text)
        if not hit:
            return
        page, widget = hit
        self.sidebar.select(page, emit=True)
        QTimer.singleShot(0, self.search.clear)

        def reveal():
            area = self.stack.widget(page)
            if isinstance(area, QScrollArea) and widget is not area.widget():
                area.ensureWidgetVisible(widget, 0, 140)
                FlashHighlight(widget)
        QTimer.singleShot(480, reveal)

    def go_to(self, page_name):
        if page_name in self.pages:
            self.sidebar.select(self.pages[page_name], emit=True)

    # ------------------------------------------------------------ reset
    def _reset_page(self, idx, title):
        keys = sorted(self._page_keys.get(idx, ()))
        if not keys:
            return
        answer = QMessageBox.question(self, "Reset page", f"Put every setting on the “{title}” page back to "
                                                          "its default?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        overlay = {k.split(".", 1)[1]: DEFAULTS["overlay"][k.split(".", 1)[1]]
                   for k in keys if k.startswith("overlay.")}
        if overlay:
            self.settings.update_section("overlay", overlay)
        for k in keys:
            section, name = k.split(".", 1)
            if section != "overlay":
                self.settings.set(k, DEFAULTS[section][name])
        self.overlay_toast.emit(f"{title} reset to defaults")

    # ------------------------------------------------------- drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith((".mnkcfg", ".json", ".txt"))
                                          for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            path = u.toLocalFile()
            if path:
                self._import_config_file(path)

    def _build_pages(self):
        self._build_dashboard()
        self._build_configs()
        self._build_typography()
        self._build_colors()
        self._build_animation()
        self._build_layout()
        self._build_player_controls()
        self._build_fullscreen()
        self._build_playlists()
        self._build_write()
        self._build_library()
        self._build_themes()
        self._build_hotkeys()
        self._build_recording()
        self._build_updates()
        self._build_about()
        self._build_legal()
        self._build_credits()

    def _build_legal(self):
        lay = self._page("Privacy & Legal", "What MNKLyrics does with your data, and the fine print.", "lock")

        def text_card(title, html_text):
            card = Card(title)
            lbl = QLabel(html_text)
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setOpenExternalLinks(True)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("font-size: 13px; line-height: 150%;")
            card.body.addWidget(lbl)
            lay.addWidget(card)
            return card

        a = theme.t["accent"]
        text_card("Privacy: MNKLyrics doesn't collect your data", (
            "No account, no analytics, no tracking, no ads. To work, the app only connects to:"
            "<ul style='margin-left:-20px'>"
            "<li><b>lrclib.net</b>: to look up lyrics. It sends the song's title, artist, album and length.</li>"
            "<li><b>github.com</b>: to check for and download MNKLyrics updates.</li></ul>"
            "If <b>Discord status</b> is on (General page), MNKLyrics tells the Discord app on this PC what song is "
            "playing, and Discord shows it on your profile. To show the song's cover there, it looks up the cover "
            "on <b>itunes.apple.com</b> (or <b>deezer.com</b>), sending the song's title and artist. You can turn "
            "it off anytime. Browsing the community lyrics needs no account. To <b>share</b> lyrics you sign in "
            "with Discord (MNKLyrics never sees your password); the song's title, artist, album, length, your "
            "lyrics and your Discord username are then published to the MNKLyrics community library for anyone "
            "to see. The sign-in is kept encrypted on this PC, and you can sign out on the Lyrics Library page. "
            "Like any website, those services can see your IP address when the app connects. Your settings, "
            "configs and lyrics cache stay on this PC in <b>%APPDATA%\\MNKLyrics</b>. Share codes only contain "
            "style settings and the creator name you chose."))
        text_card("Lyrics", (
            f"Lyrics are provided by <a style='color:{a}' href='https://lrclib.net'>LRCLIB</a>, a free community "
            "lyrics database, and are shown for your personal use while you listen. All lyrics belong to their "
            "songwriters and publishers. MNKLyrics doesn't host or share lyrics. It only keeps a small cache on "
            "your PC so songs load faster."))
        text_card("Trademarks", (
            "MNKLyrics is a free, independent project. It isn't affiliated with, endorsed by, or sponsored by "
            "Spotify, LRCLIB, Medal, OBS, Discord, Apple, Deezer or any other company mentioned in the app. Spotify is a trademark of "
            "Spotify AB. All other product names and trademarks belong to their respective owners and are used "
            "only to describe what MNKLyrics works with."))
        lay.addStretch(1)

    def _build_recording(self):
        lay = self._page("Recording & Clips", "Choose whether the lyrics appear in your clips, recordings and "
                                              "streams.", "eye")
        card = Card("Show in recordings")
        self.toggle(card, "Show lyrics in recordings & clips", "general.show_in_recordings",
                    "On: screen recorders, clip apps, screenshots and streams capture the lyrics.  "
                    "Off: the lyrics stay on your screen but are invisible to every recorder.")
        lay.addWidget(card)

        medal = Card("Recording the lyrics with Medal",
                     "Medal's game recording captures the game's own window, so separate overlay windows like "
                     "MNKLyrics don't show up in those clips. Medal's Desktop Capture records the whole screen, "
                     "lyrics included:")
        steps = QLabel(
            "<ol style='margin-left:-20px'>"
            "<li>Make sure <b>Show lyrics in recordings &amp; clips</b> above is <b>on</b>.</li>"
            "<li>In Medal, click <b>Waiting for game</b> in the top-left corner.</li>"
            "<li>Click the <b>Waiting for game to be detected</b> banner, choose <b>Desktop</b> and press "
            "<b>Switch</b>.</li>"
            "<li>Pick the monitor the lyrics are on and click <b>Start Recording</b>.</li>"
            "<li>Run your game in <b>borderless / windowed fullscreen</b> (exclusive fullscreen hides the "
            "overlay from your screen and from recordings).</li>"
            "</ol>")
        steps.setTextFormat(Qt.TextFormat.RichText)
        steps.setWordWrap(True)
        steps.setStyleSheet(f"font-size: 13px; line-height: 150%;")
        medal.body.addWidget(steps)
        link = QHBoxLayout()
        guide = GlowButton("Medal's Desktop Capture guide", icon="info", variant="ghost")
        guide.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(
            "https://support.medal.tv/support/solutions/articles/48001263799-how-to-screen-record-desktop-capture-")))
        link.addWidget(guide)
        link.addStretch(1)
        medal.body.addLayout(link)
        note = QLabel("These steps follow Medal's own help article; Medal may rename things in newer versions, "
                      "the guide button always has the current steps.")
        note.setObjectName("rowDesc")
        note.setWordWrap(True)
        medal.body.addWidget(note)
        lay.addWidget(medal)

        other = Card("Other recorders")
        txt = QLabel("The same rule applies to most recorders: recording the whole screen includes the lyrics, "
                     "while recording only the game usually doesn't.\n"
                     "• OBS: use a Display Capture source (Game Capture records only the game).\n"
                     "• Apps that record a monitor or the full desktop include the lyrics.")
        txt.setObjectName("rowDesc")
        txt.setWordWrap(True)
        other.body.addWidget(txt)
        lay.addWidget(other)
        tm = QLabel("Medal and OBS are trademarks of their respective owners. MNKLyrics is an independent app and "
                    "isn't affiliated with or endorsed by them.")
        tm.setObjectName("rowDesc")
        tm.setWordWrap(True)
        tm.setContentsMargins(8, 4, 8, 0)
        lay.addWidget(tm)
        lay.addStretch(1)

    def _build_credits(self):
        lay = self._page("Credits", "The people behind MNKLyrics.", "info")
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(CreditsHero())
        lay.addWidget(wrap)

        card = Card("Connect", "Questions, ideas or bug reports? Reach out on Discord.")
        card.body.addWidget(row("Made by", self._value_label(DEV_NAME)))

        dev = QWidget()
        dh = QHBoxLayout(dev)
        dh.setContentsMargins(0, 0, 0, 0)
        dh.addWidget(self._value_label(DEV_DISCORD))
        copy_btn = GlowButton("Copy", icon="presets", variant="ghost")

        def copy():
            QGuiApplication.clipboard().setText(DEV_DISCORD)
            copy_btn.setText("Copied!")
            copy_btn.icon_name = "check"
            copy_btn.update()
            QTimer.singleShot(1600, lambda: (copy_btn.setText("Copy"), setattr(copy_btn, "icon_name", "presets"),
                                             copy_btn.update()))
        copy_btn.clicked.connect(copy)
        dh.addWidget(copy_btn)
        card.body.addWidget(row("Developer's Discord", dev, "Add me as a friend on Discord"))

        srv = QWidget()
        sh = QHBoxLayout(srv)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.addWidget(self._value_label(DISCORD_SERVER))
        join = GlowButton("Join server", icon="play")
        join.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://" + DISCORD_SERVER)))
        sh.addWidget(join)
        card.body.addWidget(row("Discord Server", srv, "Community, updates and support"))
        lay.addWidget(card)

        powered = Card("Powered by")
        txt = QLabel("Synced lyrics: LRCLIB (lrclib.net) community database\n"
                     "Now playing: Windows media session\n"
                     "Interface: Qt for Python (PySide6)")
        txt.setObjectName("rowDesc")
        powered.body.addWidget(txt)
        lay.addWidget(powered)
        lay.addStretch(1)

    @staticmethod
    def _value_label(text):
        l = QLabel(text)
        l.setObjectName("value")
        l.setStyleSheet("font-size: 14px;")
        l.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return l

    def _build_dashboard(self):
        lay = self._page("Now Playing", "Your live session. Lyrics follow Spotify automatically. No login needed.",
                         "music", section="Overview")
        self.now_playing = NowPlayingCard()
        self.now_playing.command.connect(self.media_command)
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(10, 6, 10, 6)
        wl.addWidget(self.now_playing)
        lay.addWidget(wrap)

        qc = Card("Quick configs", "Switch your look in one click. Share and manage them on the Configs page.")
        self.quick_group = TileGroup()
        self.quick_group.changed.connect(self.apply_preset)
        qc.body.addWidget(self.quick_group)
        qbar = QHBoxLayout()
        save = GlowButton("Save current look", icon="plus", variant="ghost")
        save.clicked.connect(self._quick_save)
        paste = GlowButton("Paste share code", icon="download", variant="ghost")
        paste.clicked.connect(self._quick_paste)
        manage = GlowButton("All configs", icon="presets")
        manage.clicked.connect(lambda: self.go_to("Configs"))
        for b in (save, paste):
            qbar.addWidget(b)
        qbar.addStretch(1)
        qbar.addWidget(manage)
        qc.body.addLayout(qbar)
        lay.addWidget(qc)
        if self.configs is not None:
            self.configs.changed.connect(self._rebuild_quick)
        self._register("general.active_config", lambda _v: self._rebuild_quick())

        quick = Card("Quick controls", "Everything here also has a global hotkey.")
        self.toggle(quick, "Show overlay", "overlay.visible", "Ctrl+Alt+L")
        self.toggle(quick, "Lock overlay (click-through)", "overlay.click_through",
                    "Unlock to drag it anywhere on screen • Ctrl+Alt+K")
        self.toggle(quick, "Fade when the mouse hovers", "overlay.fade_on_hover")
        self.demo_switch = ToggleSwitch()
        self.demo_switch.toggled.connect(self.demo_toggled)
        self._add_row(quick, "Preview on screen", self.demo_switch,
                      "Shows sample lyrics on the real overlay so you can style and place it without music")
        self.toggle(quick, "Player controls on the overlay", "overlay.controls_enabled",
                    "Volume, previous / next and play / pause. Pick which on the Player Controls page")
        fs_btn = GlowButton("Open full screen", icon="maximize", variant="ghost")
        fs_btn.clicked.connect(self.open_fullscreen)
        self._add_row(quick, "Full-screen Overlay", fs_btn, "Big lyrics that fill your screen • Ctrl+Alt+F")

        sync = Card("Lyrics & sync", "Nudge timing if lyrics run early or late for a song.")
        self.lyrics_status = QLabel("Waiting for Spotify…")
        self.lyrics_status.setObjectName("value")
        self.lyrics_status.setWordWrap(True)
        sync.body.addWidget(self.lyrics_status)
        self.slider(sync, "Sync offset", "general.sync_offset_ms", -3000, 3000, 50, "{:+.0f} ms",
                    "Positive = lyrics appear earlier")
        btns = QHBoxLayout()
        for text, delta in (("−250 ms", -250), ("Reset", None), ("+250 ms", 250)):
            b = GlowButton(text, variant="ghost")
            b.clicked.connect(lambda _=False, d=delta: self.settings.set(
                "general.sync_offset_ms", 0 if d is None else int(self.settings.get("general.sync_offset_ms") + d)))
            btns.addWidget(b)
        btns.addStretch(1)
        sync.body.addLayout(btns)
        fix = QHBoxLayout()
        self.version_btn = GlowButton("Other version", icon="presets", variant="ghost")
        self.version_btn.setToolTip("Lyrics wrong or full of typos? Switch to another uploaded version "
                                    "of this song (Ctrl+Alt+V). Your pick is remembered.")
        self.version_btn.clicked.connect(self.next_version)
        self.version_btn.setEnabled(False)
        fix.addWidget(self.version_btn)
        fix.addStretch(1)
        reload_btn = GlowButton("Reload lyrics", icon="refresh")
        reload_btn.clicked.connect(self.reload_lyrics)
        fix.addWidget(reload_btn)
        sync.body.addLayout(fix)
        self._cards_row(lay, quick, sync)
        lay.addStretch(1)

    def _build_typography(self):
        lay = self._page("Typography", "Font, size and spacing of the lyrics. Changes apply instantly.", "type",
                         preview="overlay", section="Lyrics style", reset=True)
        font = Card("Font")
        combo = QFontComboBox()
        combo.setMinimumWidth(260)
        combo.setMaxVisibleItems(14)
        combo.currentFontChanged.connect(lambda f: self.settings.set("overlay.font_family", f.family()))

        def set_font(v):
            combo.blockSignals(True)
            combo.setCurrentFont(QFont(v))
            combo.blockSignals(False)
        self._register("overlay.font_family", set_font)
        self._add_row(font, "Font family", combo)
        self.slider(font, "Size", "overlay.font_size", 12, 96, 1, "{:.0f} px")
        self.slider(font, "Weight", "overlay.font_weight", 100, 900, 100, "{:.0f}")
        self.toggle(font, "Italic", "overlay.italic")
        self.toggle(font, "ALL CAPS", "overlay.uppercase")

        spacing = Card("Spacing & lines")
        self.slider(spacing, "Letter spacing", "overlay.letter_spacing", -2, 12, 0.5, "{:.1f} px")
        self.slider(spacing, "Line height", "overlay.line_spacing", 1.0, 2.2, 0.05, "{:.2f}×")
        self.slider(spacing, "Other lines size", "overlay.context_scale", 0.4, 1.0, 0.02, "{:.0f}%", mult=100)
        self.segmented(spacing, "Lines before", "overlay.lines_before", [(i, str(i)) for i in range(4)])
        self.segmented(spacing, "Lines after", "overlay.lines_after", [(i, str(i)) for i in range(4)])
        self.segmented(spacing, "Alignment", "overlay.align", [("left", "Left"), ("center", "Center"), ("right", "Right")])
        self._cards_row(lay, font, spacing)
        lay.addStretch(1)

    def _build_colors(self):
        lay = self._page("Colors & Glow", "Colors, highlight style, glow, outline and the backdrop.", "palette",
                         preview="overlay", reset=True)
        hl = Card("Highlight style", "How the line being sung stands out.")
        self.tiles(hl, "overlay.highlight_style", [
            ("karaoke", "Karaoke", "bolt"), ("color", "Color", "palette"), ("glow", "Glow", "theme"),
            ("pill", "Pill", "layout"), ("underline", "Underline", "min"), ("none", "None", "close")])
        lay.addWidget(hl)

        text = Card("Text")
        self.color(text, "Text color", "overlay.text_color")
        self.color(text, "Highlight color", "overlay.highlight_color")
        self.slider(text, "Other lines opacity", "overlay.context_opacity", 0.05, 1.0, 0.01, "{:.0f}%", mult=100)
        glow = Card("Glow")
        self.toggle(glow, "Glow", "overlay.glow_enabled")
        self.color(glow, "Glow color", "overlay.glow_color")
        self.slider(glow, "Strength", "overlay.glow_strength", 0.0, 2.0, 0.05, "{:.0f}%", mult=100)
        self.slider(glow, "Radius", "overlay.glow_radius", 2, 48, 1, "{:.0f} px")
        self._cards_row(lay, text, glow)

        outline = Card("Outline & shadow", "Keeps lyrics readable over bright game scenes.")
        self.toggle(outline, "Outline", "overlay.outline_enabled")
        self.color(outline, "Outline color", "overlay.outline_color")
        self.slider(outline, "Outline width", "overlay.outline_width", 0.5, 6, 0.25, "{:.2f} px")
        self.toggle(outline, "Drop shadow", "overlay.shadow_enabled")
        self.color(outline, "Shadow color", "overlay.shadow_color")
        self.slider(outline, "Shadow opacity", "overlay.shadow_opacity", 0, 1, 0.01, "{:.0f}%", mult=100)
        self.slider(outline, "Shadow distance", "overlay.shadow_offset", 0, 12, 1, "{:.0f} px")
        bg = Card("Background panel")
        self.toggle(bg, "Show background", "overlay.bg_enabled")
        self.color(bg, "Background color", "overlay.bg_color")
        self.slider(bg, "Transparency", "overlay.bg_opacity", 0, 1, 0.01, "{:.0f}%", mult=100)
        self.slider(bg, "Corner radius", "overlay.bg_radius", 0, 48, 1, "{:.0f} px")
        self.slider(bg, "Padding", "overlay.padding", 4, 48, 1, "{:.0f} px")
        self.toggle(bg, "Glowing border", "overlay.bg_border")
        self._cards_row(lay, outline, bg)
        lay.addStretch(1)

    def _build_animation(self):
        lay = self._page("Animation", "How lyrics move when the song advances.", "bolt", preview="overlay",
                         reset=True)
        tr = Card("Line transition")
        self.tiles(tr, "overlay.animation", [
            ("slide", "Smooth slide", "layout"), ("bounce", "Bounce", "bolt"), ("fade", "Crossfade", "theme"),
            ("zoom", "Zoom", "search"), ("none", "Instant", "close")])
        lay.addWidget(tr)
        en = Card("Active line entrance")
        self.tiles(en, "overlay.entrance", [
            ("pop", "Pop", "plus"), ("rise", "Rise", "play"), ("typewriter", "Typewriter", "keyboard"),
            ("none", "None", "close")])
        lay.addWidget(en)
        sp = Card("Timing")
        self.slider(sp, "Animation speed", "overlay.anim_speed", 0.3, 2.5, 0.05, "{:.2f}×")
        lay.addWidget(sp)
        lay.addStretch(1)

    def _build_layout(self):
        lay = self._page("Position & Layout", "Where the overlay sits and what it shows.", "layout", reset=True)
        pos = Card("Screen position", "Click an anchor, or unlock the overlay and drag it.")
        picker = PositionPicker()
        picker.changed.connect(lambda v: self.settings.set("overlay.position", v))
        self._register("overlay.position", picker.setValue)
        prow = QHBoxLayout()
        prow.addWidget(picker)
        right = QVBoxLayout()
        screens = QComboBox()
        for i, s in enumerate(QGuiApplication.screens()):
            g = s.geometry()
            screens.addItem(f"Display {i + 1}  ({g.width()}×{g.height()})", i)
        screens.currentIndexChanged.connect(lambda i: self.settings.set("overlay.screen", i))

        def set_screen(v):
            screens.blockSignals(True)
            screens.setCurrentIndex(max(0, min(screens.count() - 1, int(v))))
            screens.blockSignals(False)
        self._register("overlay.screen", set_screen)
        right.addWidget(QLabel("Monitor"))
        right.addWidget(screens)
        right.addSpacing(10)
        reset = GlowButton("Reset position", icon="refresh", variant="ghost")
        reset.clicked.connect(lambda: self.settings.set("overlay.position", "bottom_center"))
        right.addWidget(reset)
        right.addStretch(1)
        prow.addSpacing(20)
        prow.addLayout(right, 1)
        pos.body.addLayout(prow)
        self.slider(pos, "Edge margin", "overlay.margin", 0, 400, 2, "{:.0f} px")

        size = Card("Size & visibility")
        self.slider(size, "Overlay width", "overlay.width", 360, 2000, 10, "{:.0f} px")
        self.slider(size, "Overall opacity", "overlay.opacity", 0.1, 1.0, 0.01, "{:.0f}%", mult=100)
        self.toggle(size, "Show song title", "overlay.show_track_info")
        self.toggle(size, "Show progress bar", "overlay.show_progress")
        self.toggle(size, "Lock (click-through)", "overlay.click_through")
        self._cards_row(lay, pos, size)

        beh = Card("Visibility", "When the overlay steps out of the way.")
        self.toggle(beh, "Hide while paused", "overlay.hide_when_paused",
                    "Waits 2 seconds first, so skipping or seeking never makes it flicker")
        self.toggle(beh, "Song card when there are no lyrics", "overlay.no_lyrics_card",
                    "Cover, song name and a live sound wave instead of an empty overlay")
        self.toggle(beh, "Hide when no lyrics are found", "overlay.hide_no_lyrics")
        self.toggle(beh, "Fade when the mouse rests on it", "overlay.fade_on_hover",
                    "Only while the overlay is locked and the cursor stays on the lyrics box")
        self.slider(beh, "Opacity while hovered", "overlay.hover_opacity", 0.0, 1.0, 0.05, "{:.0f}%", mult=100)
        lay.addWidget(beh)
        lay.addStretch(1)

    # ------------------------------------------------------ player controls
    def _build_player_controls(self):
        lay = self._page("Player Controls", "Control the music right from the overlay. Turn on only the buttons "
                                            "you want.", "volume", preview="overlay", reset=True)
        main = Card("Buttons", "Every button is optional.")
        self.toggle(main, "Show player controls", "overlay.controls_enabled",
                    "Adds a small control bar next to the lyrics")
        self.toggle(main, "Play / pause", "overlay.controls_playpause")
        self.toggle(main, "Previous and next song", "overlay.controls_prevnext")
        self.toggle(main, "Volume slider", "overlay.controls_volume",
                    "Changes Spotify's own volume (like the Windows volume mixer), not your whole PC")
        place = Card("Placement")
        self.segmented(place, "Position", "overlay.controls_position",
                       [("below", "Below"), ("above", "Above"), ("left", "Left"), ("right", "Right")])
        self.segmented(place, "Show", "overlay.controls_visibility", [("always", "Always"), ("hover", "On hover")],
                       "On hover: the bar appears when your cursor comes near the lyrics")
        self._cards_row(lay, main, place)
        look = Card("Look")
        self.tiles(look, "overlay.controls_style", [
            ("pill", "Pill", "layout"), ("glass", "Glass", "theme"), ("minimal", "Minimal", "min")])
        self.slider(look, "Size", "overlay.controls_size", 0.6, 1.8, 0.05, "{:.0f}%", mult=100)
        self.color(look, "Icon color", "overlay.controls_color")
        self.slider(look, "Opacity", "overlay.controls_opacity", 0.2, 1.0, 0.01, "{:.0f}%", mult=100)
        lay.addWidget(look)
        plc = Card("Playlist button", "A small button on the left of the song's progress bar opens a list of "
                                      "your saved songs. Click one to play it. It shows once you've added a "
                                      "playlist, and works even with the player controls off.")
        self.toggle(plc, "Playlist button on the overlay", "overlay.controls_playlist")
        self.segmented(plc, "Show it", "overlay.controls_playlist_visibility",
                       [("always", "Always"), ("hover", "On hover")],
                       "On hover: it fades in when your cursor comes near the lyrics")
        manage = GlowButton("Manage playlists", icon="playlist", variant="ghost")
        manage.clicked.connect(lambda _=False: self.go_to("Playlists"))
        plc.body.addWidget(manage, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(plc)
        tip = QLabel("While the overlay is locked, the control bar becomes clickable when your cursor is over it. "
                     "Everywhere else, clicks still go straight to your game. Scroll on the bar to change volume.")
        tip.setObjectName("rowDesc")
        tip.setWordWrap(True)
        tip.setContentsMargins(8, 4, 8, 0)
        lay.addWidget(tip)
        lay.addStretch(1)

    # ----------------------------------------------------------- full screen
    def font_picker(self, card, label, key):
        combo = QFontComboBox()
        combo.setMinimumWidth(240)
        combo.setMaxVisibleItems(14)
        combo.currentFontChanged.connect(lambda f: self.settings.set(key, f.family()))

        def set_font(v):
            combo.blockSignals(True)
            combo.setCurrentFont(QFont(v))
            combo.blockSignals(False)
        self._register(key, set_font)
        self._add_row(card, label, combo)

    def _build_fullscreen(self):
        f = "fullscreen."
        lay = self._page("Full Screen", "Big, Spotify-style lyrics that fill your screen. Every detail is yours "
                                        "to design.", "maximize", preview="fullscreen", section="Full-screen Overlay",
                         reset=True)
        go = Card("Open it", "Or press Ctrl+Alt+F anywhere.")
        open_btn = GlowButton("Open full screen", icon="maximize")
        open_btn.clicked.connect(self.open_fullscreen)
        go.body.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignLeft)
        txt = QLabel("While it's open: Esc or Ctrl+Alt+F closes it • Space plays / pauses • ← → previous / next "
                     "• ↑ ↓ or the mouse wheel change the volume")
        txt.setObjectName("rowDesc")
        txt.setWordWrap(True)
        go.body.addWidget(txt)
        beh = Card("Behavior")
        screens = QComboBox()
        for i, s in enumerate(QGuiApplication.screens()):
            g = s.geometry()
            screens.addItem(f"Display {i + 1}  ({g.width()}×{g.height()})", i)
        screens.currentIndexChanged.connect(lambda i: self.settings.set(f + "screen", i))
        self._register(f + "screen", lambda v: (screens.blockSignals(True),
                                                screens.setCurrentIndex(max(0, min(screens.count() - 1, int(v)))),
                                                screens.blockSignals(False)))
        self._add_row(beh, "Monitor", screens)
        self.toggle(beh, "Keep on top of other windows", f + "always_on_top")
        self.toggle(beh, "Hide the mouse cursor when idle", f + "hide_cursor")
        self._cards_row(lay, go, beh)

        mode = Card("Background", "Album art, a color, a gradient, or any picture you like.")
        self.tiles(mode, f + "bg_mode", [("album", "Album art", "music"), ("color", "Color", "palette"),
                                         ("gradient", "Gradient", "theme"), ("image", "My image", "image")])
        lay.addWidget(mode)
        img = Card("My background image", "Pick any picture on your PC. MNKLyrics keeps its own copy, so moving the original "
                               "later is fine.")
        pick_row = QHBoxLayout()
        pick = GlowButton("Choose image…", icon="image")
        pick.clicked.connect(self._choose_background)
        clear = GlowButton("Remove", icon="trash", variant="ghost")
        clear.clicked.connect(lambda: self.settings.set(f + "bg_image", ""))
        self.bg_name = QLabel("")
        self.bg_name.setObjectName("rowDesc")
        self._register(f + "bg_image", lambda v: self.bg_name.setText(os.path.basename(v) if v else "No image chosen"))
        pick_row.addWidget(pick)
        pick_row.addWidget(clear)
        pick_row.addWidget(self.bg_name, 1)
        img.body.addLayout(pick_row)
        lay.addWidget(img)
        colors = Card("Background colors")
        self.color(colors, "Solid color", f + "bg_solid")
        self.color(colors, "Gradient from", f + "bg_grad1")
        self.color(colors, "Gradient to", f + "bg_grad2")
        self.slider(colors, "Gradient angle", f + "bg_grad_angle", 0, 360, 5, "{:.0f}°")
        fx = Card("Background effects")
        self.slider(fx, "Blur", f + "bg_blur", 0, 100, 1, "{:.0f}%", desc="For album art and your image")
        self.slider(fx, "Darken", f + "bg_dim", 0.0, 0.9, 0.01, "{:.0f}%", mult=100,
                    desc="Keeps the lyrics readable on bright pictures")
        self.toggle(fx, "Vignette", f + "bg_vignette", "Darker edges that draw the eye to the lyrics")
        self._cards_row(lay, colors, fx)

        font = Card("Lyrics text")
        self.font_picker(font, "Font family", f + "font_family")
        self.slider(font, "Size", f + "font_size", 24, 140, 1, "{:.0f} px")
        self.slider(font, "Weight", f + "font_weight", 100, 900, 100, "{:.0f}")
        self.toggle(font, "Italic", f + "italic")
        self.toggle(font, "ALL CAPS", f + "uppercase")
        self.slider(font, "Letter spacing", f + "letter_spacing", -2, 12, 0.5, "{:.1f} px")
        self.slider(font, "Line height", f + "line_spacing", 1.0, 2.2, 0.05, "{:.2f}×")
        lines = Card("Lyric lines")
        self.segmented(lines, "Lines before", f + "lines_before", [(i, str(i)) for i in range(0, 7)])
        self.segmented(lines, "Lines after", f + "lines_after", [(i, str(i)) for i in range(0, 7)])
        self.slider(lines, "Other lines size", f + "context_scale", 0.4, 1.0, 0.02, "{:.0f}%", mult=100)
        self.segmented(lines, "Alignment", f + "align", [("left", "Left"), ("center", "Center"), ("right", "Right")])
        self._cards_row(lay, font, lines)
        col = Card("Lyrics colors & highlight")
        self.tiles(col, f + "highlight_style", [
            ("color", "Color", "palette"), ("karaoke", "Karaoke", "bolt"), ("glow", "Glow", "theme"),
            ("pill", "Pill", "layout"), ("underline", "Underline", "min"), ("none", "None", "close")])
        self.color(col, "Text color", f + "text_color")
        self.color(col, "Highlight color", f + "highlight_color")
        self.slider(col, "Other lines opacity", f + "context_opacity", 0.05, 1.0, 0.01, "{:.0f}%", mult=100)
        lay.addWidget(col)
        glow = Card("Glow & shadow")
        self.toggle(glow, "Glow", f + "glow_enabled")
        self.color(glow, "Glow color", f + "glow_color")
        self.slider(glow, "Glow strength", f + "glow_strength", 0.0, 2.0, 0.05, "{:.0f}%", mult=100)
        self.slider(glow, "Glow radius", f + "glow_radius", 2, 60, 1, "{:.0f} px")
        self.toggle(glow, "Drop shadow", f + "shadow_enabled")
        self.color(glow, "Shadow color", f + "shadow_color")
        self.slider(glow, "Shadow opacity", f + "shadow_opacity", 0, 1, 0.01, "{:.0f}%", mult=100)
        self.slider(glow, "Shadow distance", f + "shadow_offset", 0, 16, 1, "{:.0f} px")
        self.toggle(glow, "Outline", f + "outline_enabled")
        self.color(glow, "Outline color", f + "outline_color")
        self.slider(glow, "Outline width", f + "outline_width", 0.5, 6, 0.25, "{:.2f} px")
        motion = Card("Lyrics motion")
        self.segmented(motion, "Transition", f + "animation", [("slide", "Slide"), ("bounce", "Bounce"),
                                                                ("fade", "Fade"), ("zoom", "Zoom"),
                                                                ("none", "Instant")])
        self.segmented(motion, "New line", f + "entrance", [("none", "None"), ("pop", "Pop"), ("rise", "Rise"),
                                                             ("typewriter", "Type")])
        self.slider(motion, "Speed", f + "anim_speed", 0.3, 2.5, 0.05, "{:.2f}×")
        self._cards_row(lay, glow, motion)

        art = Card("Album art & song info")
        self.segmented(art, "Album art", f + "art_position", [("left", "Left"), ("top", "Top"), ("hidden", "Hidden")])
        self.toggle(art, "Song title and artist", f + "show_title")
        self.slider(art, "Lyrics width", f + "lyrics_width", 0.3, 0.95, 0.01, "{:.0f}%", mult=100)
        ctl = Card("Progress & controls")
        self.toggle(ctl, "Progress bar", f + "show_progress")
        self.toggle(ctl, "Player controls", f + "show_controls")
        self.toggle(ctl, "Previous and next song", f + "controls_prevnext")
        self.toggle(ctl, "Volume slider", f + "controls_volume",
                    "Changes Spotify's own volume, not your whole PC")
        self._cards_row(lay, art, ctl)
        lay.addStretch(1)

    def _choose_background(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a background picture", "",
                                              "Pictures (*.png *.jpg *.jpeg *.webp *.bmp *.gif)")
        if not path:
            return
        from .config import app_dir
        if QImage(path).isNull():
            QMessageBox.warning(self, "Background", "That file couldn't be opened as a picture.")
            return
        folder = os.path.join(app_dir(), "backgrounds")
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, "background" + os.path.splitext(path)[1].lower())
        try:
            for old in os.listdir(folder):
                os.remove(os.path.join(folder, old))
            shutil.copyfile(path, target)
        except OSError as exc:
            QMessageBox.warning(self, "Background", f"Couldn't copy the picture: {exc.strerror}")
            return
        self.settings.set("fullscreen.bg_image", "")  # force a refresh even if the name is the same
        self.settings.set("fullscreen.bg_image", target)
        self.settings.set("fullscreen.bg_mode", "image")

    # ---------------------------------------------------------- configs
    def _build_configs(self):
        lay = self._page("Configs", "Your looks, ready to switch, share and swap with friends.", "presets")
        lay.setSpacing(8)
        self.config_msg = QLabel("")
        self.config_msg.setWordWrap(True)
        self.config_msg.setContentsMargins(6, 0, 6, 0)
        self.config_msg.hide()
        lay.addWidget(self.config_msg)

        self.mine_header = section_header("My configs", "Click a look to apply it. Hover for Share and more.")
        lay.addWidget(self.mine_header)
        mine = QWidget()
        self.config_grid = FlowLayout(mine, spacing=14)
        lay.addWidget(mine)
        self._index("My configs", mine)
        self._index("Save current look", mine)

        lay.addWidget(section_header("Built-in looks", "Hand-made starting points. Save a copy to tweak one."))
        builtin = QWidget()
        self.builtin_grid = FlowLayout(builtin, spacing=14)
        lay.addWidget(builtin)
        self._index("Built-in looks", builtin)

        lay.addSpacing(10)
        community = Card("Community", "Share your configs and grab ones other people made. "
                                      "A share code can only change how the lyrics look.")
        paste = QHBoxLayout()
        self.code_field = QLineEdit()
        self.code_field.setPlaceholderText("Paste a share code (MNK-…)")
        self.code_field.returnPressed.connect(lambda: self._import_code(self.code_field.text()))
        go = GlowButton("Import code", icon="download")
        go.clicked.connect(lambda: self._import_code(self.code_field.text()))
        paste.addWidget(self.code_field, 1)
        paste.addWidget(go)
        community.body.addLayout(paste)
        self._index("Paste a share code", community)
        author = QLineEdit()
        author.setPlaceholderText("Your name")
        author.setMaxLength(32)
        author.setFixedWidth(240)
        author.editingFinished.connect(lambda: self.settings.set("general.author_name", author.text().strip()))
        self._register("general.author_name", lambda v: author.hasFocus() or author.setText(v or ""))
        self._add_row(community, "Creator name", author, "Shown as “by …” on configs you share")
        discord = GlowButton("Browse configs on Discord", icon="share", variant="ghost")
        discord.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://" + DISCORD_SERVER)))
        dl = QHBoxLayout()
        dl.addWidget(discord)
        dl.addStretch(1)
        community.body.addLayout(dl)
        lay.addWidget(community)
        lay.addStretch(1)
        if self.configs is not None:
            self.configs.changed.connect(self._rebuild_configs)
        self._register("general.active_config", lambda _v: self._rebuild_configs())

    @staticmethod
    def _clear_flow(flow):
        while flow.count():
            item = flow.takeAt(0)
            w = item.widget() if item else None
            if w:
                w.setParent(None)
                w.deleteLater()

    def _rebuild_configs(self):
        if self.configs is None:
            return
        active = self.settings.get("general.active_config")
        items = self.configs.items
        self.mine_header.title_label.setText(f"My configs  ·  {len(items)}" if items else "My configs")
        self._clear_flow(self.config_grid)
        new = AddTile("plus", "Save current look", "Keep how the lyrics look right now as a new config")
        new.clicked.connect(self._quick_save)
        imp = AddTile("download", "Import", "Paste a share code or open a .mnkcfg file")
        imp.clicked.connect(lambda: self._import_menu(imp))
        self.config_grid.addWidget(new)
        self.config_grid.addWidget(imp)
        for name, entry in reversed(list(items.items())):  # newest first
            bits = [f"by {entry['author']}" if entry.get("author") else "by you"]
            if entry.get("imported"):
                bits.append("imported")
            if entry.get("created"):
                bits.append(entry["created"])
            self.config_grid.addWidget(ConfigCard(name, entry.get("values", {}), "  •  ".join(bits),
                                                  name == active, self))
        self._clear_flow(self.builtin_grid)
        for name, values in STYLE_PRESETS.items():
            self.builtin_grid.addWidget(ConfigCard(name, values, "built-in look", name == active, self,
                                                   builtin=True))

    def _rebuild_quick(self):
        """Dashboard strip: your configs (newest first), or the built-in looks until you save one."""
        if self.configs is None or not hasattr(self, "quick_group"):
            return
        self.quick_group.clear()
        mine = list(self.configs.items.items())[::-1][:7]
        looks = [(n, e["values"]) for n, e in mine] or list(STYLE_PRESETS.items())[:7]
        for name, values in looks:
            self.quick_group.add(Tile(name, name, painter=preset_painter(values), size=(150, 96)))
        self.quick_group.setValue(self.settings.get("general.active_config"))

    def _quick_save(self):
        name, ok = QInputDialog.getText(self, "Save current look", "Name:",
                                        text=f"My config {len(self.configs.items) + 1}")
        if ok:
            saved = self.configs.save_current(name or "My config")
            self.settings.set("general.active_config", saved)
            self.overlay_toast.emit(f"Saved config: {saved}")

    def _quick_paste(self):
        code = find_code(QGuiApplication.clipboard().text())
        if code:
            self.go_to("Configs")
            self._import_code(code)
        else:
            self.go_to("Configs")
            self.code_field.setFocus()
            self._config_message("Copy a share code (MNK-…) first, or paste it into the box below.")

    def _config_message(self, text, error=False):
        color = theme.t["danger"] if error else theme.t["accent"]
        self.config_msg.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.config_msg.setText(text)
        self.config_msg.show()

    def apply_preset(self, name):
        cfg = self.settings.all_presets().get(name)
        if not cfg:
            return
        if name in STYLE_PRESETS and not (self.configs and name in self.configs.items):
            # built-in looks only restyle the lyrics (they keep your font size, size and position)
            values = {k: DEFAULTS["overlay"][k] for k in PRESET_KEYS if k != "font_size"}
            values.update({k: v for k, v in cfg.items() if k in PRESET_KEYS})
        else:
            # a config is exactly what its creator set up: look, size, place on screen, options
            values = to_overlay(cfg, int(self.settings.get("overlay.screen") or 0))
        self.settings.update_section("overlay", values)
        self.settings.set("general.active_config", name)
        self.overlay_toast.emit(f"Config: {name}")

    def _config_share_menu(self, name, anchor):
        menu = QMenu(self)
        a_code = menu.addAction("Copy share code (paste it in Discord)")
        a_file = menu.addAction("Export as file…")
        act = menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))
        if act == a_code:
            QGuiApplication.clipboard().setText(self.configs.share_code(name))
            self._config_message(f"Share code for “{name}” copied. Paste it anywhere to share.")
            self.overlay_toast.emit("Share code copied")
        elif act == a_file:
            safe = "".join(c for c in name if c not in '<>:"/\\|?*').strip() or "config"
            path, _ = QFileDialog.getSaveFileName(self, "Export config", f"{safe}.mnkcfg",
                                                  "MNKLyrics config (*.mnkcfg)")
            if path:
                try:
                    self.configs.export_file(name, path)
                    self._config_message(f"Exported “{name}”. Send the file to anyone. They can drag it "
                                         "onto MNKLyrics to import it.")
                except OSError as exc:
                    self._config_message(f"Couldn't save the file: {exc.strerror}", error=True)

    def _config_more_menu(self, name, anchor):
        menu = QMenu(self)
        a_rename = menu.addAction("Rename…")
        a_update = menu.addAction("Update with current look")
        a_dup = menu.addAction("Duplicate")
        menu.addSeparator()
        a_del = menu.addAction("Delete…")
        act = menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))
        if act == a_rename:
            new, ok = QInputDialog.getText(self, "Rename config", "New name:", text=name)
            if ok:
                try:
                    final = self.configs.rename(name, new)
                    self._config_message(f"Renamed to “{final}”.")
                except ValueError as exc:
                    self._config_message(str(exc), error=True)
        elif act == a_update:
            self.configs.update_from_current(name)
            self._config_message(f"“{name}” now matches your current look.")
        elif act == a_dup:
            self._config_message(f"Created “{self.configs.duplicate(name)}”.")
        elif act == a_del:
            if QMessageBox.question(self, "Delete config", f"Delete “{name}”? This can't be undone.") \
                    == QMessageBox.StandardButton.Yes:
                self.configs.delete(name)
                self._config_message(f"Deleted “{name}”.")

    def _builtin_share(self, name):
        QGuiApplication.clipboard().setText(self.configs.share_code(name))
        self._config_message(f"Share code for “{name}” copied.")

    def _builtin_more_menu(self, name, anchor):
        menu = QMenu(self)
        a_copy = menu.addAction("Save an editable copy to My configs")
        a_code = menu.addAction("Copy share code")
        act = menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))
        if act == a_code:
            self._builtin_share(name)
        elif act == a_copy:
            self._config_message(f"Created “{self.configs.duplicate(name)}” in My configs.")

    def _import_menu(self, anchor):
        menu = QMenu(self)
        a_code = menu.addAction("Paste a share code…")
        a_file = menu.addAction("Open a config file…")
        act = menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))
        if act == a_code:
            clip = QGuiApplication.clipboard().text().strip()
            code, ok = QInputDialog.getText(self, "Import config", "Paste the share code:",
                                            text=clip if clip.startswith("MNK1-") else "")
            if ok:
                self._import_code(code)
        elif act == a_file:
            path, _ = QFileDialog.getOpenFileName(self, "Import config", "",
                                                  "MNKLyrics config (*.mnkcfg *.json *.txt)")
            if path:
                self._import_config_file(path)

    def _import_code(self, code):
        if self.configs is None:
            return
        try:
            name = self.configs.import_code(code)
        except ValueError as exc:
            self._config_message(str(exc), error=True)
            return
        self.code_field.clear()
        author = self.configs.items[name].get("author")
        self._config_message(f"Imported “{name}”{f' by {author}' if author else ''}. Click Apply to use it.")
        self.overlay_toast.emit(f"Imported config: {name}")

    def _import_config_file(self, path):
        if self.configs is None:
            return
        self.show_animated()
        self.go_to("Configs")
        try:
            name = self.configs.import_file(path)
        except (OSError, ValueError) as exc:
            self._config_message(str(exc) if isinstance(exc, ValueError) else "Couldn't read that file.",
                                 error=True)
            return
        author = self.configs.items[name].get("author")
        self._config_message(f"Imported “{name}”{f' by {author}' if author else ''}. Click Apply to use it.")

    # ---------------------------------------------------------- updates
    def _build_updates(self):
        lay = self._page("Updates", "Get new features and fixes with one click.", "download")
        card = Card("MNKLyrics updates")
        self.upd_title = QLabel(f"You have version {VERSION}")
        self.upd_title.setStyleSheet("font-size: 16px; font-weight: 600;")
        card.body.addWidget(self.upd_title)
        self.upd_status = QLabel("")
        self.upd_status.setObjectName("rowDesc")
        self.upd_status.setWordWrap(True)
        card.body.addWidget(self.upd_status)
        self.upd_notes = QLabel("")
        self.upd_notes.setWordWrap(True)
        self.upd_notes.setStyleSheet(f"background: {theme.t['bg3']}; border-radius: 10px; padding: 10px;")
        self.upd_notes.hide()
        card.body.addWidget(self.upd_notes)
        self.upd_bar = ProgressBar()
        self.upd_bar.hide()
        card.body.addWidget(self.upd_bar)
        btns = QHBoxLayout()
        self.upd_check = GlowButton("Check for updates", icon="refresh", variant="ghost")
        self.upd_get = GlowButton("Download update", icon="download")
        self.upd_restart = GlowButton("Restart & install", icon="refresh")
        self.upd_check.clicked.connect(self.update_check)
        self.upd_get.clicked.connect(self.update_download)
        self.upd_restart.clicked.connect(self.update_install)
        for b in (self.upd_check, self.upd_get, self.upd_restart):
            btns.addWidget(b)
        btns.addStretch(1)
        card.body.addLayout(btns)
        self.toggle(card, "Check for updates automatically", "general.auto_update",
                    "Checks when the app starts and every few hours; never installs without asking")
        lay.addWidget(card)
        lay.addStretch(1)
        if self.updater is not None:
            self.updater.changed.connect(self._refresh_updates)
        self._refresh_updates()

    def _refresh_updates(self):
        u = self.updater
        state = u.state if u else "not_configured"
        latest = (u.latest or {}) if u else {}
        texts = {
            "not_configured": "Updates aren't set up in this build yet.",
            "idle": "Click “Check for updates” to look for a newer version.",
            "checking": "Checking for updates…",
            "up_to_date": "You're on the latest version.",
            "available": f"Version {latest.get('version', '')} is available.",
            "downloading": f"Downloading… {int((u.progress if u else 0) * 100)}%",
            "ready": "Downloaded and verified. Restart to finish installing. It takes a few seconds.",
            "error": (u.error if u else "") or "Something went wrong.",
        }
        text = texts.get(state, "")
        if state == "available" and u and not u.can_install:
            text += " (You're running from source, so get it from the release page.)"
        self.upd_status.setText(text)
        self.upd_status.setStyleSheet(f"color: {theme.t['danger']};" if state == "error" else
                                      f"color: {theme.t['accent']}; font-weight: 600;" if state in
                                      ("available", "ready") else "")
        notes = latest.get("notes", "") if state in ("available", "downloading", "ready") else ""
        self.upd_notes.setText(f"What's new:\n{notes}" if notes else "")
        self.upd_notes.setVisible(bool(notes))
        self.upd_bar.setVisible(state in ("downloading", "ready"))
        self.upd_bar.set_value(u.progress if u else 0)
        self.upd_check.setVisible(state in ("idle", "up_to_date", "error", "checking"))
        self.upd_check.setEnabled(state != "checking")
        self.upd_get.setVisible(state == "available" and bool(u and u.can_install))
        self.upd_restart.setVisible(state == "ready")
        self.titlebar.update_btn.setVisible(state in ("available", "downloading", "ready"))
        self.titlebar.update_btn.setText("Restart to update" if state == "ready" else "Update available")
        self.titlebar.update_btn.resize(self.titlebar.update_btn.sizeHint())

    def _build_themes(self):
        lay = self._page("Appearance", "Theme of this control center. Switches live with a reveal animation.",
                         "theme", section="App")
        card = Card("Themes")
        g = TileGroup(4)
        for name in THEMES:
            g.add(Tile(name, name, painter=theme_painter(name), size=(220, 130)))
        g.changed.connect(lambda v: self.settings.set("general.ui_theme", v))
        self._register("general.ui_theme", g.setValue)
        card.body.addWidget(g)
        lay.addWidget(card)
        fx = Card("Effects")
        self.toggle(fx, "Animated ambient background", "general.ui_effects", "Subtle moving aurora behind the menu")
        lay.addWidget(fx)
        lay.addStretch(1)

    def _build_hotkeys(self):
        lay = self._page("Hotkeys", "Global shortcuts that work while you game. Click a shortcut to rebind it; "
                                    "Backspace clears, Esc cancels.", "keyboard")
        card = Card("Shortcuts")
        self.hotkey_buttons = {}
        for key, label in HOTKEY_LABELS.items():
            b = KeyCaptureButton()
            b.capturing.connect(self.hotkey_capture)
            b.captured.connect(lambda combo, k=key: self.settings.set(f"hotkeys.{k}", combo))
            self._register(f"hotkeys.{key}", lambda v, b=b: b.set_combo(v))
            self._add_row(card, label, b)
            self.hotkey_buttons[key] = b
        self.hotkey_note = QLabel("")
        self.hotkey_note.setObjectName("rowDesc")
        card.body.addWidget(self.hotkey_note)
        reset = GlowButton("Reset to defaults", icon="refresh", variant="ghost")
        reset.clicked.connect(lambda: self.settings.reset_section("hotkeys"))
        card.body.addWidget(reset)
        lay.addWidget(card)
        lay.addStretch(1)

    def set_hotkey_failures(self, names):
        if names:
            self.hotkey_note.setText("Already used by another app: " + ", ".join(HOTKEY_LABELS[n] for n in names))
        else:
            self.hotkey_note.setText("")

    def _build_about(self):
        lay = self._page("General", "Startup, player source and storage.", "gear")
        gen = Card("General")
        self.segmented(gen, "Follow", "general.player", [("spotify", "Spotify only"), ("any", "Any player")],
                       "“Any player” also follows browsers, YouTube Music, etc.")
        self.toggle(gen, "Start with Windows", "general.start_with_windows")
        self.toggle(gen, "Open this window on startup", "general.show_window_on_start",
                    "Off = start quietly in the system tray")
        lay.addWidget(gen)
        dc = Card("Discord status", "Shows “Listening to MNKLyrics” on your Discord profile while music plays. "
                                    "Needs the Discord app open on this PC. Not showing? In Discord, open "
                                    "User Settings › Activity Privacy and turn on sharing your activity.")
        self.toggle(dc, "Show on my Discord profile", "general.discord_status")
        lay.addWidget(dc)
        data = Card("Storage")
        h = QHBoxLayout()
        b1 = GlowButton("Open settings folder", icon="folder", variant="ghost")
        b2 = GlowButton("Clear lyrics cache", icon="trash", variant="danger")
        b1.clicked.connect(self._open_folder)
        b2.clicked.connect(self._clear_cache)
        h.addWidget(b1)
        h.addWidget(b2)
        h.addStretch(1)
        data.body.addLayout(h)
        lay.addWidget(data)
        about = Card(f"About {APP_NAME}")
        txt = QLabel("Now-playing info comes from the Windows media session, so Spotify needs no login.\n"
                     "Synced lyrics are provided by LRCLIB (lrclib.net), a free community lyrics database.\n"
                     "Tip: for games, use borderless / windowed fullscreen so the overlay can draw on top.")
        txt.setObjectName("rowDesc")
        txt.setWordWrap(True)
        about.body.addWidget(txt)
        quit_btn = GlowButton(f"Quit {APP_NAME}", icon="close", variant="danger")
        quit_btn.clicked.connect(self.quit_requested)
        about.body.addWidget(quit_btn)
        lay.addWidget(about)
        lay.addStretch(1)

    def _open_folder(self):
        import os
        from .config import app_dir
        os.startfile(app_dir())

    def _clear_cache(self):
        import glob
        import os
        from .config import cache_dir
        n = 0
        for f in glob.glob(os.path.join(cache_dir(), "*.json")):
            try:
                os.remove(f)
                n += 1
            except OSError:
                pass
        self.overlay_toast.emit(f"Cleared {n} cached lyrics")

    # ------------------------------------------------------------ updates
    def set_track(self, track):
        self.current_track = track
        self.now_playing.set_track(track)
        self.titlebar.status.setText(f"♪ {track.title}" if track else "")

    def set_lyrics_status(self, text):
        self.lyrics_status.setText(text)

    def set_versions(self, current, total):
        self.version_btn.setEnabled(total > 1)
        self.version_btn.setText(f"Other version ({current + 1}/{total})" if total > 1 else "Other version")
        self.version_btn.resize(self.version_btn.sizeHint())
        self.version_btn.updateGeometry()
