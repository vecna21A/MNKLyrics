"""Player controls (previous / play-pause / next / volume) shared by the overlay and Full-screen Overlay.

Volume changes the *player's own* volume (Spotify's slider in the Windows volume mixer), never the
whole PC. Every button can be switched off, so nobody is forced to use them.
"""

import os

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient, QBrush

from .widgets import draw_icon


def _qc(value, a=1.0):
    c = QColor(value)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


class VolumeController(QObject):
    """Reads/sets the volume of the player's audio session (Windows Core Audio, per-app)."""

    changed = Signal(float)  # 0..1, or -1 when the player isn't making sound / can't be found

    def __init__(self):
        super().__init__()
        self.process = "spotify.exe"
        self.level = -1.0
        self._meter = None
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def set_target(self, app_id: str):
        """Follow whichever app is playing (e.g. 'Spotify.exe'); defaults to Spotify."""
        name = os.path.basename((app_id or "").replace("\\", "/")).lower()
        self.process = name if name.endswith(".exe") else "spotify.exe"
        self.refresh()

    def _sessions(self):
        try:
            from pycaw.pycaw import AudioUtilities
            return [s for s in AudioUtilities.GetAllSessions()
                    if s.Process and s.Process.name().lower() == self.process]
        except Exception:
            return []

    def peak(self) -> float:
        """How loud the player is right now (0..1) — drives the sound wave on the song card."""
        if self._meter is None:
            return 0.0
        try:
            return float(self._meter.GetPeakValue())
        except Exception:
            self._meter = None
            return 0.0

    def refresh(self):
        sessions = self._sessions()
        level = -1.0
        self._meter = None
        if sessions:
            try:
                level = float(sessions[0].SimpleAudioVolume.GetMasterVolume())
            except Exception:
                level = -1.0
            try:
                from pycaw.pycaw import IAudioMeterInformation
                self._meter = sessions[0]._ctl.QueryInterface(IAudioMeterInformation)
            except Exception:
                self._meter = None
        if abs(level - self.level) > 0.004:
            self.level = level
            self.changed.emit(level)

    def set(self, level: float):
        level = max(0.0, min(1.0, float(level)))
        ok = False
        for s in self._sessions():
            try:
                s.SimpleAudioVolume.SetMasterVolume(level, None)
                ok = True
            except Exception:
                pass
        if ok:
            self.level = level
            self.changed.emit(level)

    def nudge(self, delta: float):
        if self.level < 0:
            self.refresh()
        if self.level >= 0:
            self.set(self.level + delta)


class ControlsRenderer:
    """Lays out, paints and hit-tests a control bar. ``cfg`` keys: playpause, prevnext, volume,
    style, size, color, opacity, vertical, and for a docked vertical strip: dock_color, dock_opacity,
    dock_radius, dock_border (all optional)."""

    BTN = 34.0
    SLIDER = 112.0
    MIN_SLIDER = 36.0

    def __init__(self):
        self.cfg = {}
        self.rects = {}           # name -> QRectF in the painter's coordinates (after paint)
        self.bar = QRectF()
        self.hover = None
        self.volume = -1.0
        self.playing = False

    def configure(self, **cfg):
        self.cfg = cfg

    @property
    def vertical(self):
        return bool(self.cfg.get("vertical"))

    def _scale(self):
        return max(0.5, min(2.0, float(self.cfg.get("size", 1.0))))

    def _buttons(self):
        out = []
        if self.cfg.get("prevnext", True):
            out.append(("prev", 1.0))
        if self.cfg.get("playpause", True):
            out.append(("play", 1.15))
        if self.cfg.get("prevnext", True):
            out.append(("next", 1.0))
        return out

    def _length(self, s, slider):
        """Main-axis length at scale ``s`` with a slider of ``slider`` px."""
        b, gap, pad = self.BTN * s, 8 * s, 10 * s
        btns = self._buttons()
        n = len(btns) + (1 if self.cfg.get("volume", True) else 0)
        if n == 0:
            return 0.0
        total = sum(b * m for _, m in btns) + 2 * pad
        if self.cfg.get("volume", True):
            total += b * 0.8 + slider
        total += gap * (n - 1) + (gap * 1.5 if self.cfg.get("volume", True) and n > 1 else 0)
        return total

    def size(self):
        """(width, height). A vertical strip reports its natural length; it stretches to fit when docked."""
        s = self._scale()
        length = self._length(s, self.SLIDER * s)
        if length <= 0:
            return 0.0, 0.0
        thickness = self.BTN * s + 2 * 10 * s * 0.6
        return (thickness, length) if self.vertical else (length, thickness)

    def paint(self, p: QPainter, top_left: QPointF, alpha=1.0, accent="#1ED760", length=None):
        """Paint at ``top_left``. A vertical strip is stretched (or shrunk) to ``length`` when given."""
        w, h = self.size()
        self.rects = {}
        if w <= 0 or alpha <= 0.01:
            self.bar = QRectF()
            return
        s = self._scale()
        vertical = self.vertical
        slider = self.SLIDER * s
        if vertical and length:
            vol = self.cfg.get("volume", True)
            need = self._length(s, self.MIN_SLIDER * s if vol else 0)
            if need > length:  # lyrics box is short: shrink the buttons so the strip still matches it
                s *= max(0.45, length / need)
            slider = max(self.MIN_SLIDER * s, length - self._length(s, 0)) if vol else 0
            h = max(length, self._length(s, slider))
        thick = w if vertical else h
        b, gap, pad = self.BTN * s, 8 * s, 10 * s
        bar = QRectF(top_left.x(), top_left.y(), thick if vertical else w, h)
        self.bar = bar
        style = self.cfg.get("style", "pill")
        icon = QColor(self.cfg.get("color", "#FFFFFF"))
        acc = QColor(accent)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(alpha * max(0.15, min(1.0, float(self.cfg.get("opacity", 0.92)))))
        rad = min(bar.width(), bar.height()) / 2
        if vertical and self.cfg.get("dock_color") and style == "pill":
            # docked next to the lyrics: wear the same background as the lyrics box
            r = min(rad, float(self.cfg.get("dock_radius", rad)))
            if self.cfg.get("dock_border"):
                p.setPen(QPen(_qc(accent, 0.35), 1.2))
            else:
                p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_qc(self.cfg["dock_color"], float(self.cfg.get("dock_opacity", 0.7))))
            p.drawRoundedRect(bar.adjusted(0.5, 0.5, -0.5, -0.5), r, r)
        elif style == "pill":
            p.setPen(QPen(_qc(accent, 0.45), 1.2))
            p.setBrush(QColor(8, 8, 14, 175))
            p.drawRoundedRect(bar, rad, rad)
        elif style == "glass":
            p.setPen(QPen(QColor(255, 255, 255, 70), 1.0))
            p.setBrush(QColor(255, 255, 255, 34))
            p.drawRoundedRect(bar, 12 * s, 12 * s)
        cross = bar.center().x() if vertical else bar.center().y()
        cursor = (bar.top() if vertical else bar.left()) + pad

        def place(size):
            """Rect of ``size`` at the cursor on the main axis, centered on the cross axis."""
            if vertical:
                return QRectF(cross - size / 2, cursor, size, size)
            return QRectF(cursor, cross - size / 2, size, size)

        def button(name, glyph, size_mul=1.0, filled=False):
            nonlocal cursor
            bw = b * size_mul
            r = place(bw)
            self.rects[name] = r
            hov = self.hover == name
            if filled:
                g = QRadialGradient(r.center(), bw * 0.8)
                g.setColorAt(0, _qc(accent, 0.45 if hov else 0.25))
                g.setColorAt(1, _qc(accent, 0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(g))
                p.drawEllipse(r.center(), bw * 0.8, bw * 0.8)
                p.setBrush(acc)
                p.drawEllipse(r.adjusted(bw * .08, bw * .08, -bw * .08, -bw * .08))
                col = QColor("#0A0A12") if acc.lightnessF() > 0.55 else QColor("#FFFFFF")
            else:
                if hov:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(255, 255, 255, 30))
                    p.drawEllipse(r.center(), bw / 2, bw / 2)
                col = acc if hov else icon
            if style == "minimal" and not filled:  # readable on any background
                draw_icon(p, glyph, r.adjusted(bw * .27, bw * .27, -bw * .27, -bw * .27).translated(1, 1),
                          QColor(0, 0, 0, 150), 2.0 * s)
            m = bw * (0.3 if filled else 0.27)
            draw_icon(p, glyph, r.adjusted(m, m, -m, -m), col, 2.0 * s)
            cursor += bw + gap

        for name, mul in self._buttons():
            if name == "play":
                button("play", "pause" if self.playing else "play", mul, filled=True)
            else:
                button(name, name, mul)
        if self.cfg.get("volume", True):
            if len(self.rects):
                cursor += gap * 0.5
            vi = place(b * 0.8)
            self.rects["mute"] = vi
            muted = self.volume == 0
            draw_icon(p, "mute" if muted else "volume", vi.adjusted(b * .12, b * .12, -b * .12, -b * .12),
                      acc if self.hover == "mute" else icon, 1.8 * s)
            cursor += b * 0.8 + gap * 0.5
            if vertical:
                end = bar.bottom() - pad
                track = QRectF(cross - 2.5 * s, cursor, 5 * s, max(4.0, end - cursor))
                self.rects["volume"] = track.adjusted(-12 * s, -6 * s, 12 * s, 6 * s)
            else:
                track = QRectF(cursor, cross - 2.5 * s, slider - gap * 0.5, 5 * s)
                self.rects["volume"] = track.adjusted(-6 * s, -12 * s, 6 * s, 12 * s)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 50))
            p.drawRoundedRect(track, 2.5 * s, 2.5 * s)
            if self.volume >= 0:
                if vertical:  # fills from the bottom up
                    fh = track.height() * self.volume
                    fill = QRectF(track.left(), track.bottom() - fh, track.width(), fh)
                    knob = QPointF(track.center().x(), fill.top())
                else:
                    fill = QRectF(track.left(), track.top(), track.width() * self.volume, track.height())
                    knob = QPointF(fill.right(), track.center().y())
                p.setBrush(acc)
                p.drawRoundedRect(fill, 2.5 * s, 2.5 * s)
                p.setBrush(QColor("#FFFFFF"))
                kr = (7 if self.hover == "volume" else 5.5) * s
                p.drawEllipse(knob, kr, kr)
        p.restore()

    def hit(self, pos: QPointF):
        for name, r in self.rects.items():
            if r.contains(pos):
                return name
        return None

    def volume_at(self, pos: QPointF):
        r = self.rects.get("volume")
        if not r:
            return None
        if self.vertical:
            inner = r.adjusted(0, 6, 0, -6)
            return max(0.0, min(1.0, (inner.bottom() - pos.y()) / max(1.0, inner.height())))
        inner = r.adjusted(6, 0, -6, 0)
        return max(0.0, min(1.0, (pos.x() - inner.left()) / max(1.0, inner.width())))


def overlay_controls_cfg(cfg: dict) -> dict:
    """Map overlay settings to ControlsRenderer settings. On the left or right the controls become a
    vertical strip docked to the lyrics box, wearing the box's own background."""
    side = cfg.get("controls_position", "below") in ("left", "right")
    out = {"playpause": cfg.get("controls_playpause", True), "prevnext": cfg.get("controls_prevnext", True),
           "volume": cfg.get("controls_volume", True), "style": cfg.get("controls_style", "pill"),
           "size": cfg.get("controls_size", 1.0), "color": cfg.get("controls_color", "#FFFFFF"),
           "opacity": cfg.get("controls_opacity", 0.92), "vertical": side}
    if side and cfg.get("bg_enabled", True):
        out.update(dock_color=cfg.get("bg_color", "#0A0A12"), dock_opacity=cfg.get("bg_opacity", 0.6),
                   dock_radius=cfg.get("bg_radius", 18), dock_border=cfg.get("bg_border", False))
    return out


CONTROLS_GAP = 10.0
DOCK_GAP = 6.0  # a docked side strip sits this close to the lyrics box


def _gap(cfg):
    return DOCK_GAP if cfg.get("controls_position", "below") in ("left", "right") else CONTROLS_GAP


def overlay_extent(cfg, controls: ControlsRenderer):
    """Extra (width, height) the overlay window needs for its control bar, or (0, 0)."""
    if not cfg.get("controls_enabled"):
        return 0.0, 0.0
    cw, ch = controls.size()
    if cw <= 0:
        return 0.0, 0.0
    pos = cfg.get("controls_position", "below")
    if pos in ("left", "right"):
        return cw + _gap(cfg), 0.0
    return 0.0, ch + _gap(cfg)


def split_overlay_rect(rect: QRectF, cfg, controls: ControlsRenderer) -> QRectF:
    """The part of the overlay window where the lyrics panel lives (the rest is for the controls)."""
    ew, eh = overlay_extent(cfg, controls)
    pos = cfg.get("controls_position", "below")
    if ew:
        return rect.adjusted(ew, 0, 0, 0) if pos == "left" else rect.adjusted(0, 0, -ew, 0)
    if eh:
        return rect.adjusted(0, eh, 0, 0) if pos == "above" else rect.adjusted(0, 0, 0, -eh)
    return rect


def controls_origin(panel: QRectF, cfg, controls: ControlsRenderer) -> QPointF:
    """Top-left of the control bar, placed next to the (animated) lyrics panel. Docked side strips
    start at the panel's top edge and are painted with ``length=panel.height()``."""
    cw, ch = controls.size()
    pos = cfg.get("controls_position", "below")
    gap = _gap(cfg)
    if pos == "above":
        return QPointF(panel.center().x() - cw / 2, panel.top() - gap - ch)
    if pos == "left":
        return QPointF(panel.left() - gap - cw, panel.top())
    if pos == "right":
        return QPointF(panel.right() + gap, panel.top())
    return QPointF(panel.center().x() - cw / 2, panel.bottom() + gap)


def controls_length(panel: QRectF, cfg):
    """Length to stretch a docked side strip to (None for bars above/below)."""
    return panel.height() if cfg.get("controls_position", "below") in ("left", "right") else None
