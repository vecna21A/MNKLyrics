"""Custom-painted, animated widgets with glow effects for the control center."""

import math
import time

from PySide6.QtCore import (QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation,
                            Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QKeySequence, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import (QScrollArea, QAbstractButton, QColorDialog, QFrame, QHBoxLayout, QLabel, QLayout,
                               QSizePolicy, QVBoxLayout, QWidget)

from .themes import ThemeManager

theme = ThemeManager()
AA = QPainter.RenderHint.Antialiasing
OUT = QEasingCurve.Type.OutCubic


def mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * t, a.greenF() + (b.greenF() - a.greenF()) * t,
                           a.blueF() + (b.blueF() - a.blueF()) * t, a.alphaF() + (b.alphaF() - a.alphaF()) * t)


def alpha(c: QColor, a: float) -> QColor:
    c = QColor(c)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def accent_gradient(r: QRectF, a: float = 1.0) -> QLinearGradient:
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, theme.c("accent", a))
    g.setColorAt(1, theme.c("accent2", a))
    return g


def paint_glow(p: QPainter, r: QRectF, radius: float, color: QColor, strength: float, spread: int = 8):
    """Soft glow around a rounded rect, built from layered translucent strokes."""
    if strength <= 0.01:
        return
    p.save()
    p.setBrush(Qt.BrushStyle.NoBrush)
    for k in range(1, spread + 1):
        a = strength * 0.16 * (1 - k / (spread + 1)) ** 2
        p.setPen(QPen(alpha(color, a), 2))
        p.drawRoundedRect(r.adjusted(-k, -k, k, k), radius + k, radius + k)
    p.restore()


class Anim:
    """Named float animations that repaint the owner widget on every step."""

    def __init__(self, widget):
        self.w = widget
        self.v = {}
        self._a = {}

    def get(self, key, default=0.0):
        return self.v.get(key, default)

    def set(self, key, value):
        a = self._a.get(key)
        if a is not None:
            a.stop()
        self.v[key] = float(value)
        self.w.update()

    def to(self, key, target, ms=220, curve=OUT):
        a = self._a.get(key)
        if a is None:
            a = QVariantAnimation(self.w)
            a.valueChanged.connect(lambda val, k=key: self._step(k, val))
            self._a[key] = a
        a.stop()
        start = self.v.get(key, 0.0)
        if abs(start - target) < 1e-4:
            self.v[key] = float(target)
            return
        a.setStartValue(float(start))
        a.setEndValue(float(target))
        a.setDuration(ms)
        a.setEasingCurve(curve)
        a.start()

    def _step(self, key, val):
        self.v[key] = float(val)
        self.w.update()


# --------------------------------------------------------------------- icons
def draw_icon(p: QPainter, name: str, r: QRectF, color: QColor, width: float = 1.7):
    p.save()
    p.setRenderHint(AA)
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    x, y, w, h = r.x(), r.y(), r.width(), r.height()

    def P(fx, fy):
        return QPointF(x + fx * w, y + fy * h)

    def poly(points, fill=False):
        pg = QPolygonF([P(a, b) for a, b in points])
        if fill:
            p.setBrush(color)
        p.drawPolygon(pg)
        p.setBrush(Qt.BrushStyle.NoBrush)

    if name == "music":
        p.drawLine(P(.36, .72), P(.36, .2))
        p.drawLine(P(.36, .2), P(.8, .12))
        p.drawLine(P(.8, .12), P(.8, .62))
        p.setBrush(color)
        p.drawEllipse(P(.27, .74), w * .11, h * .09)
        p.drawEllipse(P(.71, .64), w * .11, h * .09)
    elif name == "type":
        f = QFont("Segoe UI", 1)
        f.setPixelSize(int(h * .62))
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, "Aa")
    elif name == "palette":
        p.drawEllipse(r.adjusted(w * .1, h * .1, -w * .1, -h * .1))
        p.setBrush(color)
        for fx, fy in ((.35, .38), (.55, .3), (.68, .5), (.42, .62)):
            p.drawEllipse(P(fx, fy), w * .055, h * .055)
    elif name == "bolt":
        poly([(.58, .06), (.24, .56), (.48, .56), (.4, .94), (.78, .42), (.53, .42), (.62, .06)])
    elif name == "layout":
        p.drawRoundedRect(QRectF(P(.1, .16), P(.9, .84)), 3, 3)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(P(.24, .6), P(.76, .72)), 2, 2)
    elif name == "presets":
        p.drawRoundedRect(QRectF(P(.26, .12), P(.9, .64)), 3, 3)
        p.drawRoundedRect(QRectF(P(.1, .36), P(.74, .88)), 3, 3)
    elif name == "theme":
        er = r.adjusted(w * .12, h * .12, -w * .12, -h * .12)
        p.drawEllipse(er)
        path = QPainterPath()
        path.moveTo(er.center())
        path.arcTo(er, 90, 180)
        path.closeSubpath()
        p.fillPath(path, color)
    elif name == "keyboard":
        p.drawRoundedRect(QRectF(P(.06, .24), P(.94, .78)), 3, 3)
        p.setBrush(color)
        for row, fy in enumerate((.4, .54)):
            for k in range(5):
                p.drawEllipse(P(.2 + k * .15, fy), w * .028, h * .028)
        p.drawLine(P(.32, .66), P(.68, .66))
    elif name == "gear":
        c = r.center()
        rad = min(w, h)
        p.drawEllipse(c, rad * .16, rad * .16)
        p.drawEllipse(c, rad * .3, rad * .3)
        for k in range(8):
            a = k * math.pi / 4
            p.drawLine(QPointF(c.x() + math.cos(a) * rad * .3, c.y() + math.sin(a) * rad * .3),
                       QPointF(c.x() + math.cos(a) * rad * .42, c.y() + math.sin(a) * rad * .42))
    elif name == "info":
        p.drawEllipse(r.adjusted(w * .1, h * .1, -w * .1, -h * .1))
        p.drawLine(P(.5, .46), P(.5, .7))
        p.setBrush(color)
        p.drawEllipse(P(.5, .32), w * .035, h * .035)
    elif name == "play":
        poly([(.3, .18), (.3, .82), (.82, .5)], fill=True)
    elif name == "pause":
        p.setBrush(color)
        p.drawRoundedRect(QRectF(P(.26, .18), P(.42, .82)), 2, 2)
        p.drawRoundedRect(QRectF(P(.58, .18), P(.74, .82)), 2, 2)
    elif name == "next":
        poly([(.2, .2), (.2, .8), (.66, .5)], fill=True)
        p.drawLine(P(.78, .2), P(.78, .8))
    elif name == "prev":
        poly([(.8, .2), (.8, .8), (.34, .5)], fill=True)
        p.drawLine(P(.22, .2), P(.22, .8))
    elif name == "close":
        p.drawLine(P(.28, .28), P(.72, .72))
        p.drawLine(P(.72, .28), P(.28, .72))
    elif name == "min":
        p.drawLine(P(.28, .5), P(.72, .5))
    elif name in ("lock", "unlock"):
        p.drawRoundedRect(QRectF(P(.22, .46), P(.78, .88)), 3, 3)
        path = QPainterPath()
        if name == "lock":
            path.moveTo(P(.34, .46))
            path.lineTo(P(.34, .32))
            path.arcTo(QRectF(P(.34, .12), P(.66, .52)), 180, -180)
            path.lineTo(P(.66, .46))
        else:
            path.moveTo(P(.34, .46))
            path.lineTo(P(.34, .32))
            path.arcTo(QRectF(P(.34, .12), P(.66, .52)), 180, -180)
            path.lineTo(P(.66, .36))
        p.drawPath(path)
    elif name == "eye":
        path = QPainterPath()
        path.moveTo(P(.06, .5))
        path.quadTo(P(.5, .08), P(.94, .5))
        path.quadTo(P(.5, .92), P(.06, .5))
        p.drawPath(path)
        p.setBrush(color)
        p.drawEllipse(r.center(), w * .11, h * .11)
    elif name == "refresh":
        p.drawArc(r.adjusted(w * .16, h * .16, -w * .16, -h * .16), 30 * 16, 290 * 16)
        poly([(.74, .1), (.86, .34), (.62, .34)], fill=True)
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(P(.08, .78))
        path.lineTo(P(.08, .22))
        path.lineTo(P(.38, .22))
        path.lineTo(P(.46, .32))
        path.lineTo(P(.92, .32))
        path.lineTo(P(.92, .78))
        path.closeSubpath()
        p.drawPath(path)
    elif name == "trash":
        p.drawLine(P(.18, .26), P(.82, .26))
        p.drawLine(P(.4, .14), P(.6, .14))
        p.drawRoundedRect(QRectF(P(.26, .26), P(.74, .88)), 2, 2)
    elif name == "plus":
        p.drawLine(P(.5, .2), P(.5, .8))
        p.drawLine(P(.2, .5), P(.8, .5))
    elif name == "check":
        p.drawPolyline(QPolygonF([P(.2, .52), P(.42, .72), P(.8, .3)]))
    elif name in ("volume", "mute"):
        spk = QPainterPath()
        spk.moveTo(P(.12, .38))
        spk.lineTo(P(.3, .38))
        spk.lineTo(P(.52, .18))
        spk.lineTo(P(.52, .82))
        spk.lineTo(P(.3, .62))
        spk.lineTo(P(.12, .62))
        spk.closeSubpath()
        p.setBrush(color)
        p.drawPath(spk)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if name == "mute":
            p.drawLine(P(.66, .36), P(.9, .64))
            p.drawLine(P(.9, .36), P(.66, .64))
        else:
            p.drawArc(QRectF(P(.42, .32), P(.78, .68)), -50 * 16, 100 * 16)
            p.drawArc(QRectF(P(.36, .18), P(.92, .82)), -50 * 16, 100 * 16)
    elif name == "maximize":
        for a, b, c in (((.14, .38), (.14, .14), (.38, .14)), ((.62, .14), (.86, .14), (.86, .38)),
                        ((.86, .62), (.86, .86), (.62, .86)), ((.38, .86), (.14, .86), (.14, .62))):
            p.drawPolyline(QPolygonF([P(*a), P(*b), P(*c)]))
    elif name == "image":
        p.drawRoundedRect(QRectF(P(.1, .18), P(.9, .82)), 3, 3)
        p.drawPolyline(QPolygonF([P(.16, .74), P(.4, .46), P(.58, .64), P(.7, .52), P(.86, .7)]))
        p.drawEllipse(P(.68, .36), w * .06, h * .06)
    elif name == "more":
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        for fx in (.22, .5, .78):
            p.drawEllipse(P(fx, .5), w * .075, h * .075)
    elif name == "share":
        p.drawLine(P(.5, .16), P(.5, .62))
        p.drawPolyline(QPolygonF([P(.32, .33), P(.5, .15), P(.68, .33)]))
        p.drawPolyline(QPolygonF([P(.24, .5), P(.24, .84), P(.76, .84), P(.76, .5)]))
    elif name == "download":
        p.drawLine(P(.5, .14), P(.5, .62))
        p.drawPolyline(QPolygonF([P(.3, .44), P(.5, .64), P(.7, .44)]))
        p.drawLine(P(.22, .84), P(.78, .84))
    elif name == "pencil":
        p.drawPolyline(QPolygonF([P(.2, .8), P(.24, .62), P(.66, .2), P(.82, .36), P(.4, .78), P(.2, .8)]))
        p.drawLine(P(.58, .28), P(.74, .44))
    elif name == "playlist":
        for ly, lx in ((.24, .78), (.44, .78), (.64, .46)):  # (not "y": that's the icon's own position)
            p.drawLine(P(.14, ly), P(lx, ly))
        p.drawEllipse(QRectF(P(.56, .7), P(.72, .86)))
        p.drawPolyline(QPolygonF([P(.72, .78), P(.72, .52), P(.88, .48)]))
    elif name == "book":
        p.drawPolyline(QPolygonF([P(.5, .26), P(.32, .18), P(.12, .2), P(.12, .78), P(.32, .76), P(.5, .84),
                                  P(.68, .76), P(.88, .78), P(.88, .2), P(.68, .18), P(.5, .26), P(.5, .84)]))
    elif name == "tap":
        p.drawEllipse(QRectF(P(.3, .3), P(.7, .7)))
        p.drawEllipse(QRectF(P(.14, .14), P(.86, .86)))
    elif name == "copy":
        p.drawRoundedRect(QRectF(P(.32, .14), P(.84, .68)), 2, 2)
        p.drawPolyline(QPolygonF([P(.2, .32), P(.16, .32), P(.16, .86), P(.66, .86), P(.66, .8)]))
    elif name == "search":
        p.drawEllipse(QRectF(P(.14, .14), P(.64, .64)))
        p.drawLine(P(.58, .58), P(.86, .86))
    elif name == "logo":
        path = QPainterPath()
        path.moveTo(P(.5, .08))
        for k in range(1, 8):
            a = -math.pi / 2 + k * math.pi / 4
            rr = .42 if k % 2 == 0 else .17
            path.lineTo(P(.5 + math.cos(a) * rr, .5 + math.sin(a) * rr))
        path.closeSubpath()
        p.fillPath(path, color)
    p.restore()


# ------------------------------------------------------------------- buttons
class GlowButton(QAbstractButton):
    """Gradient button with animated glow, hover lift and click ripple."""

    def __init__(self, text="", icon=None, variant="primary", parent=None):
        super().__init__(parent)
        self.setText(text)
        self.icon_name = icon
        self.variant = variant
        self.anim = Anim(self)
        self.ripple_pos = QPointF()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

    M = 6  # glow margin

    def sizeHint(self):
        if self.variant == "round":
            return QSize(56, 56)
        f = QFont(self.font())
        f.setWeight(QFont.Weight.DemiBold)
        tw = QFontMetricsF(f).horizontalAdvance(self.text())
        w = tw + 36 + (22 if self.icon_name else 0) + 2 * self.M
        if not self.text():
            w = 38 + 2 * self.M
        return QSize(int(w), 38 + 2 * self.M)

    def enterEvent(self, e):
        self.anim.to("hover", 1.0, 180)

    def leaveEvent(self, e):
        self.anim.to("hover", 0.0, 260)

    def mousePressEvent(self, e):
        self.ripple_pos = e.position()
        self.anim.set("ripple", 0.0)
        self.anim.to("ripple", 1.0, 520)
        self.anim.to("press", 1.0, 80)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self.anim.to("press", 0.0, 200)
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        hov, prs = self.anim.get("hover"), self.anim.get("press")
        r = QRectF(self.rect()).adjusted(self.M, self.M, -self.M, -self.M)
        r.translate(0, -1.2 * hov + prs)
        rad = r.height() / 2 if self.variant == "round" else 10
        enabled = self.isEnabled()
        if not enabled:
            p.setOpacity(0.4)
        if self.variant in ("primary", "round"):
            paint_glow(p, r, rad, theme.c("accent"), 0.55 + hov * 0.9)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(accent_gradient(r)))
            p.drawRoundedRect(r, rad, rad)
            sheen = QLinearGradient(r.topLeft(), r.bottomLeft())
            sheen.setColorAt(0, QColor(255, 255, 255, int(55 + 40 * hov)))
            sheen.setColorAt(0.5, QColor(255, 255, 255, 0))
            p.setBrush(QBrush(sheen))
            p.drawRoundedRect(r, rad, rad)
            fg = QColor("#0A0A12") if theme.dark else QColor("#FFFFFF")
        else:
            col = theme.c("danger") if self.variant == "danger" else theme.c("accent")
            paint_glow(p, r, rad, col, hov * 0.8)
            p.setPen(QPen(mix(theme.c("border"), col, hov), 1.2))
            p.setBrush(mix(theme.c("bg3"), theme.c("bg2"), 0.3))
            p.drawRoundedRect(r, rad, rad)
            fg = mix(theme.c("text"), col, hov)
        rp = self.anim.get("ripple", 1.0)
        if rp < 1.0:
            clip = QPainterPath()
            clip.addRoundedRect(r, rad, rad)
            p.save()
            p.setClipPath(clip)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, int(90 * (1 - rp))))
            rr = r.width() * 1.2 * rp
            p.drawEllipse(self.ripple_pos, rr, rr)
            p.restore()
        f = QFont(self.font())
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        if self.icon_name and not self.text():
            s = min(r.width(), r.height()) * (0.42 if self.variant == "round" else 0.5)
            draw_icon(p, self.icon_name, QRectF(r.center().x() - s / 2, r.center().y() - s / 2, s, s), fg, 1.9)
        elif self.icon_name:
            tw = QFontMetricsF(f).horizontalAdvance(self.text())
            x0 = r.center().x() - (tw + 22) / 2
            draw_icon(p, self.icon_name, QRectF(x0, r.center().y() - 8, 16, 16), fg)
            p.setPen(fg)
            p.drawText(QRectF(x0 + 22, r.top(), tw + 4, r.height()), Qt.AlignmentFlag.AlignVCenter, self.text())
        else:
            p.setPen(fg)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())


class ToggleSwitch(QAbstractButton):
    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.anim = Anim(self)
        self.anim.set("pos", 1.0 if checked else 0.0)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(62, 38)
        self.toggled.connect(lambda c: self.anim.to("pos", 1.0 if c else 0.0, 280, QEasingCurve.Type.OutBack))

    def set_silent(self, value):
        self.blockSignals(True)
        self.setChecked(bool(value))
        self.blockSignals(False)
        self.anim.to("pos", 1.0 if value else 0.0, 280, QEasingCurve.Type.OutBack)

    def enterEvent(self, e):
        self.anim.to("hover", 1.0, 150)

    def leaveEvent(self, e):
        self.anim.to("hover", 0.0, 250)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        t = max(0.0, min(1.0, self.anim.get("pos")))
        tp = self.anim.get("pos")
        hov = self.anim.get("hover")
        r = QRectF(8, 8, 46, 22)
        paint_glow(p, r, 11, theme.c("accent"), t * (0.7 + 0.5 * hov), 7)
        p.setPen(QPen(mix(theme.c("border"), theme.c("accent"), max(t, hov * 0.5)), 1))
        p.setBrush(theme.c("bg3"))
        p.drawRoundedRect(r, 11, 11)
        if t > 0:
            p.setOpacity(t)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(accent_gradient(r)))
            p.drawRoundedRect(r, 11, 11)
            p.setOpacity(1.0)
        cx = r.left() + 11 + tp * 24
        knob_r = 7.5 + hov * 1.2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawEllipse(QPointF(cx, r.center().y() + 1.5), knob_r, knob_r)
        p.setBrush(mix(theme.c("dim"), QColor("#FFFFFF"), t))
        p.drawEllipse(QPointF(cx, r.center().y()), knob_r, knob_r)


class GlowSlider(QWidget):
    valueChanged = Signal(float)

    def __init__(self, lo, hi, step=1.0, value=0.0, parent=None):
        super().__init__(parent)
        self.lo, self.hi, self.step = float(lo), float(hi), float(step)
        self._v = float(value)
        self.anim = Anim(self)
        self._drag = False
        self.setMinimumHeight(30)
        self.setMinimumWidth(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def value(self):
        return self._v

    def setValue(self, v, emit=False):
        v = max(self.lo, min(self.hi, float(v)))
        if self.step:
            v = round((v - self.lo) / self.step) * self.step + self.lo
            v = round(v, 6)
        if v == self._v:
            return
        self._v = v
        self.update()
        if emit:
            self.valueChanged.emit(v)

    def _track(self):
        return QRectF(12, self.height() / 2 - 2.5, self.width() - 24, 5)

    def _frac(self):
        return 0.0 if self.hi == self.lo else (self._v - self.lo) / (self.hi - self.lo)

    def _set_from_x(self, x):
        t = self._track()
        frac = max(0.0, min(1.0, (x - t.left()) / t.width()))
        self.setValue(self.lo + frac * (self.hi - self.lo), emit=True)

    def mousePressEvent(self, e):
        self._drag = True
        self.anim.to("press", 1.0, 120)
        self._set_from_x(e.position().x())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._set_from_x(e.position().x())

    def mouseReleaseEvent(self, e):
        self._drag = False
        self.anim.to("press", 0.0, 220)

    def wheelEvent(self, e):
        if not self.hasFocus():  # scrolling the page past a slider must not change it; click it first
            e.ignore()
            return
        d = 1 if e.angleDelta().y() > 0 else -1
        self.setValue(self._v + d * (self.step or (self.hi - self.lo) / 100), emit=True)

    def keyPressEvent(self, e):
        st = self.step or (self.hi - self.lo) / 100
        if e.key() in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            self.setValue(self._v + st, emit=True)
        elif e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            self.setValue(self._v - st, emit=True)
        else:
            super().keyPressEvent(e)

    def enterEvent(self, e):
        self.anim.to("hover", 1.0, 160)

    def leaveEvent(self, e):
        self.anim.to("hover", 0.0, 260)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        t = self._track()
        hov, prs = self.anim.get("hover"), self.anim.get("press")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("bg3"))
        p.drawRoundedRect(t, 2.5, 2.5)
        p.setPen(QPen(theme.c("border"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(t, 2.5, 2.5)
        fx = t.left() + t.width() * self._frac()
        fill = QRectF(t.left(), t.top(), fx - t.left(), t.height())
        if fill.width() > 0:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(accent_gradient(t, 0.25 + 0.15 * hov)))
            p.drawRoundedRect(fill.adjusted(-2, -3, 2, 3), 5, 5)
            p.setBrush(QBrush(accent_gradient(t)))
            p.drawRoundedRect(fill, 2.5, 2.5)
        c = QPointF(fx, t.center().y())
        g = QRadialGradient(c, 18)
        g.setColorAt(0, theme.c("accent2" if prs > 0.5 else "accent", 0.35 + 0.35 * hov + 0.2 * prs))
        g.setColorAt(1, theme.c("accent", 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(c, 18, 18)
        rr = 7 + 2 * hov - 1.5 * prs
        p.setBrush(QColor("#FFFFFF"))
        p.setPen(QPen(theme.c("accent"), 2.2))
        p.drawEllipse(c, rr, rr)


class ColorSwatch(QAbstractButton):
    colorChanged = Signal(str)

    def __init__(self, color="#FFFFFF", parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self.anim = Anim(self)
        self.setFixedSize(116, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._pick)

    def color(self):
        return self._color.name().upper()

    def setColor(self, c):
        self._color = QColor(c)
        self.update()

    def _pick(self):
        original = QColor(self._color)
        dlg = QColorDialog(original, self.window())
        dlg.setWindowTitle("Pick a color")
        dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        dlg.currentColorChanged.connect(lambda c: (self.setColor(c), self.colorChanged.emit(c.name().upper())))
        if dlg.exec():
            self.setColor(dlg.selectedColor())
        else:
            self.setColor(original)
        self.colorChanged.emit(self.color())

    def enterEvent(self, e):
        self.anim.to("hover", 1.0, 160)

    def leaveEvent(self, e):
        self.anim.to("hover", 0.0, 260)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        hov = self.anim.get("hover")
        r = QRectF(5, 5, self.width() - 10, self.height() - 10)
        paint_glow(p, r, 9, self._color, 0.5 + hov, 6)
        p.setPen(QPen(mix(theme.c("border"), theme.c("accent"), hov), 1))
        p.setBrush(theme.c("bg3"))
        p.drawRoundedRect(r, 9, 9)
        sw = QRectF(r.left() + 4, r.top() + 4, r.height() - 8, r.height() - 8)
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.setBrush(self._color)
        p.drawRoundedRect(sw, 6, 6)
        f = QFont("Cascadia Mono")
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(theme.c("text"))
        p.drawText(QRectF(sw.right() + 8, r.top(), r.width(), r.height()), Qt.AlignmentFlag.AlignVCenter,
                   self.color())


class Segmented(QWidget):
    changed = Signal(object)

    def __init__(self, options, value=None, parent=None):
        super().__init__(parent)
        self.options = list(options)  # [(value, label)]
        self.anim = Anim(self)
        self._hover = -1
        self._value = value if value is not None else self.options[0][0]
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(38)
        f = QFont(self.font())
        fm = QFontMetricsF(f)
        self._widths = [max(44.0, fm.horizontalAdvance(l) + 28) for _, l in self.options]
        self.setFixedWidth(int(sum(self._widths) + 8))
        i = self._index(self._value)
        self.anim.set("x", self._x(i))
        self.anim.set("w", self._widths[i])

    def _index(self, v):
        for i, (val, _) in enumerate(self.options):
            if val == v:
                return i
        return 0

    def _x(self, i):
        return 4 + sum(self._widths[:i])

    def value(self):
        return self._value

    def setValue(self, v, animate=True):
        self._value = v
        i = self._index(v)
        if animate:
            self.anim.to("x", self._x(i), 320, QEasingCurve.Type.OutBack)
            self.anim.to("w", self._widths[i], 320, QEasingCurve.Type.OutCubic)
        else:
            self.anim.set("x", self._x(i))
            self.anim.set("w", self._widths[i])
        self.update()

    def _hit(self, x):
        acc = 4
        for i, w in enumerate(self._widths):
            if acc <= x < acc + w:
                return i
            acc += w
        return -1

    def mouseMoveEvent(self, e):
        h = self._hit(e.position().x())
        if h != self._hover:
            self._hover = h
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._hit(e.position().x())
        if i >= 0 and self.options[i][0] != self._value:
            self.setValue(self.options[i][0])
            self.changed.emit(self._value)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(theme.c("border"), 1))
        p.setBrush(theme.c("bg3"))
        p.drawRoundedRect(r, 10, 10)
        pill = QRectF(self.anim.get("x"), 4, self.anim.get("w"), r.height() - 7)
        paint_glow(p, pill, 7, theme.c("accent"), 0.6, 5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(accent_gradient(pill)))
        p.drawRoundedRect(pill, 7, 7)
        sel = self._index(self._value)
        for i, (_, label) in enumerate(self.options):
            cell = QRectF(self._x(i), 0, self._widths[i], r.height())
            if i == sel:
                p.setPen(QColor("#0A0A12") if theme.dark else QColor("#FFFFFF"))
            elif i == self._hover:
                p.setPen(theme.c("accent"))
            else:
                p.setPen(theme.c("dim"))
            f = QFont(self.font())
            f.setWeight(QFont.Weight.DemiBold if i == sel else QFont.Weight.Normal)
            p.setFont(f)
            p.drawText(cell, Qt.AlignmentFlag.AlignCenter, label)


class Tile(QAbstractButton):
    """Selectable tile; contents drawn by ``painter(p, rect, tile)``."""

    def __init__(self, value, label, painter=None, icon=None, size=(132, 96), parent=None):
        super().__init__(parent)
        self.value, self.label, self.painter, self.icon_name = value, label, painter, icon
        self.setCheckable(True)
        self.anim = Anim(self)
        self.setFixedSize(*size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(lambda c: self.anim.to("sel", 1.0 if c else 0.0, 300))

    def enterEvent(self, e):
        self.anim.to("hover", 1.0, 160)

    def leaveEvent(self, e):
        self.anim.to("hover", 0.0, 280)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        hov, sel = self.anim.get("hover"), self.anim.get("sel")
        r = QRectF(self.rect()).adjusted(7, 7 - 2 * hov, -7, -7 - 2 * hov)
        paint_glow(p, r, 12, theme.c("accent"), sel * 1.1 + hov * 0.4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, int(40 + 40 * hov)))
        p.drawRoundedRect(r.translated(0, 3 + 2 * hov), 12, 12)
        p.setBrush(mix(theme.c("bg3"), theme.c("bg2"), 0.4))
        p.drawRoundedRect(r, 12, 12)
        if self.painter:
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(r, 12, 12)
            p.setClipPath(clip)
            self.painter(p, r, self)
            p.restore()
        else:
            ir = QRectF(r.center().x() - 15, r.top() + r.height() * 0.2, 30, 30)
            draw_icon(p, self.icon_name or "music", ir, mix(theme.c("dim"), theme.c("accent"), max(sel, hov)), 2)
            p.setPen(mix(theme.c("text"), theme.c("accent"), sel))
            f = QFont(self.font())
            f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
            p.drawText(QRectF(r.left(), r.bottom() - 34, r.width(), 26), Qt.AlignmentFlag.AlignCenter, self.label)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(mix(theme.c("border"), theme.c("accent"), max(sel, hov * 0.6)), 1.2 + sel))
        p.drawRoundedRect(r, 12, 12)
        if sel > 0.01:
            b = QRectF(r.right() - 24, r.top() + 6, 18, 18)
            p.setOpacity(sel)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(accent_gradient(b)))
            p.drawEllipse(b)
            draw_icon(p, "check", b.adjusted(3, 3, -3, -3), QColor("#0A0A12"), 2)
            p.setOpacity(1.0)


class FlowLayout(QLayout):
    """Lays fixed-size children left-to-right, wrapping to new rows as needed."""

    def __init__(self, parent=None, spacing=2):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        return size

    def _do_layout(self, rect, test):
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() + 1 and line_h > 0:
                x = rect.x()
                y += line_h + self._spacing
                line_h = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class TileGroup(QWidget):
    changed = Signal(object)

    def __init__(self, columns=5, parent=None):
        super().__init__(parent)
        self.flow = FlowLayout(self)
        self.tiles = []

    def add(self, tile: Tile):
        self.tiles.append(tile)
        self.flow.addWidget(tile)
        tile.clicked.connect(lambda _=False, t=tile: self._clicked(t))
        return tile

    def clear(self):
        for t in self.tiles:
            t.setParent(None)
            t.deleteLater()
        self.tiles = []

    def _clicked(self, tile):
        self.setValue(tile.value)
        self.changed.emit(tile.value)

    def setValue(self, value):
        for t in self.tiles:
            t.blockSignals(True)
            t.setChecked(t.value == value)
            t.blockSignals(False)
            t.anim.to("sel", 1.0 if t.value == value else 0.0, 300)


# --------------------------------------------------------------------- cards
class Card(QFrame):
    S = 10  # shadow margin

    def __init__(self, title=None, subtitle=None, parent=None):
        super().__init__(parent)
        self.anim = Anim(self)
        self.title_text = title or ""
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(self.S + 22, self.S + 18, self.S + 22, self.S + 20)
        self.outer.setSpacing(12)
        if title:
            head = QVBoxLayout()
            head.setSpacing(2)
            t = QLabel(title)
            t.setObjectName("cardTitle")
            head.addWidget(t)
            if subtitle:
                s = QLabel(subtitle)
                s.setObjectName("cardSub")
                s.setWordWrap(True)
                head.addWidget(s)
            self.outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        self.outer.addLayout(self.body)
        self.outer.addStretch(1)

    def enterEvent(self, e):
        self.anim.to("hover", 1.0, 260)

    def leaveEvent(self, e):
        self.anim.to("hover", 0.0, 380)

    def paintEvent(self, e):
        # The resting and hovered backgrounds are rendered once and cross-faded, so hover animations
        # cost two image draws per frame instead of re-drawing shadows, glow and gradients.
        p = QPainter(self)
        hov = self.anim.get("hover")
        p.drawPixmap(0, 0, self._layer(0))
        if hov > 0.01:
            p.setOpacity(hov)
            p.drawPixmap(0, 0, self._layer(1))

    def _layer(self, hov):
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, theme.name, hov)
        cache = self.__dict__.setdefault("_layers", {})
        pm = cache.get(hov)
        if pm is None or pm[0] != key:
            img = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
            img.setDevicePixelRatio(dpr)
            img.fill(Qt.GlobalColor.transparent)
            painter = QPainter(img)
            self._draw_background(painter, float(hov))
            painter.end()
            pm = cache[hov] = (key, img)
        return pm[1]

    def _draw_background(self, p, hov):
        p.setRenderHint(AA)
        S = self.S
        r = QRectF(self.rect()).adjusted(S, S, -S, -S)
        shadow = 0.22 if theme.dark else 0.07
        p.setPen(Qt.PenStyle.NoPen)
        for k in range(1, S + 1):
            p.setBrush(QColor(0, 0, 0, int(255 * shadow * (1 - k / (S + 1)) ** 2 * (0.6 + hov * 0.6) / 3)))
            p.drawRoundedRect(r.adjusted(-k * 0.6, -k * 0.3 + 2, k * 0.6, k * 0.8 + 2), 16 + k * 0.6, 16 + k * 0.6)
        paint_glow(p, r, 16, theme.c("accent"), hov * 0.35, 6)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, mix(theme.c("bg2"), theme.c("bg3"), 0.55))
        g.setColorAt(1, theme.c("bg2"))
        p.setBrush(QBrush(g))
        p.drawRoundedRect(r, 16, 16)
        bg = QLinearGradient(r.topLeft(), r.bottomRight())
        bg.setColorAt(0, mix(theme.c("border"), theme.c("accent"), 0.25 + hov * 0.5))
        bg.setColorAt(0.5, theme.c("border"))
        bg.setColorAt(1, mix(theme.c("border"), theme.c("accent2"), hov * 0.5))
        p.setPen(QPen(QBrush(bg), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 16, 16)


class GradientLabel(QWidget):
    def __init__(self, text, size=28, parent=None):
        super().__init__(parent)
        self.text = text
        self.f = QFont("Segoe UI Variable Display")
        self.f.setPixelSize(size)
        self.f.setWeight(QFont.Weight.Bold)
        self.setFixedHeight(int(size * 1.6))
        self.setMinimumWidth(200)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        fm = QFontMetricsF(self.f)
        path = QPainterPath()
        path.addText(4, (self.height() + fm.ascent() - fm.descent()) / 2, self.f, self.text)
        br = path.boundingRect()
        for w, a in ((10, 0.05), (6, 0.08), (3, 0.12)):
            p.strokePath(path, QPen(theme.c("accent", a), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                    Qt.PenJoinStyle.RoundJoin))
        g = QLinearGradient(br.topLeft(), br.topRight())
        g.setColorAt(0, theme.c("text"))
        g.setColorAt(0.55, theme.c("accent"))
        g.setColorAt(1, theme.c("accent2"))
        p.fillPath(path, QBrush(g))


class KeyCaptureButton(QAbstractButton):
    captured = Signal(str)
    capturing = Signal(bool)

    def __init__(self, combo="", parent=None):
        super().__init__(parent)
        self.combo = combo
        self.active = False
        self.partial = ""
        self.anim = Anim(self)
        self.setFixedSize(200, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self._start)
        self._pulse = QTimer(self)
        self._pulse.setInterval(30)
        self._pulse.timeout.connect(self.update)

    def set_combo(self, combo):
        self.combo = combo
        self.update()

    def _start(self):
        if self.active:
            return
        self.active = True
        self.partial = ""
        self.capturing.emit(True)
        self.grabKeyboard()
        self._pulse.start()
        self.anim.to("act", 1.0, 200)

    def _stop(self):
        self.active = False
        self.releaseKeyboard()
        self._pulse.stop()
        self.anim.to("act", 0.0, 260)
        self.capturing.emit(False)

    def focusOutEvent(self, e):
        if self.active:
            self._stop()
        super().focusOutEvent(e)

    def keyPressEvent(self, e):
        if not self.active:
            return super().keyPressEvent(e)
        key = e.key()
        mods = e.modifiers()
        names = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            names.append("Ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:
            names.append("Alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            names.append("Shift")
        if mods & Qt.KeyboardModifier.MetaModifier:
            names.append("Win")
        if key == Qt.Key.Key_Escape:
            self._stop()
            return
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and not names:
            self.combo = ""
            self._stop()
            self.captured.emit("")
            return
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta, Qt.Key.Key_AltGr):
            self.partial = "+".join(names) + "+…"
            self.update()
            return
        keyname = QKeySequence(key).toString()
        if not keyname:
            return
        self.combo = "+".join(names + [keyname])
        self._stop()
        self.captured.emit(self.combo)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        act = self.anim.get("act")
        r = QRectF(self.rect()).adjusted(5, 5, -5, -5)
        pulse = 0.55 + 0.45 * math.sin(time.perf_counter() * 6) if self.active else 0.6
        paint_glow(p, r, 9, theme.c("accent"), act * pulse * 1.3, 6)
        p.setPen(QPen(mix(theme.c("border"), theme.c("accent"), act), 1.2))
        p.setBrush(theme.c("bg3"))
        p.drawRoundedRect(r, 9, 9)
        f = QFont("Segoe UI")
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        if self.active:
            p.setPen(theme.c("accent"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.partial or "Press a key combo…")
            return
        if not self.combo:
            p.setPen(theme.c("dim"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "Not set")
            return
        # key caps
        parts = self.combo.split("+")
        fm = QFontMetricsF(f)
        widths = [fm.horizontalAdvance(s) + 16 for s in parts]
        total = sum(widths) + 8 * (len(parts) - 1)
        x = r.center().x() - total / 2
        for s, w in zip(parts, widths):
            cap = QRectF(x, r.top() + 6, w, r.height() - 12)
            p.setPen(QPen(theme.c("border"), 1))
            p.setBrush(theme.c("bg2"))
            p.drawRoundedRect(cap, 5, 5)
            p.setPen(theme.c("text"))
            p.drawText(cap, Qt.AlignmentFlag.AlignCenter, s)
            x += w + 8


POSITIONS = [["top_left", "top_center", "top_right"],
             ["middle_left", "center", "middle_right"],
             ["bottom_left", "bottom_center", "bottom_right"]]


class PositionPicker(QWidget):
    changed = Signal(str)

    def __init__(self, value="bottom_center", parent=None):
        super().__init__(parent)
        self._value = value
        self.anim = Anim(self)
        self._hover = None
        self.setFixedSize(300, 200)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._place(animate=False)

    def _screen_rect(self):
        return QRectF(20, 10, 260, 150)

    def _anchor(self, name):
        s = self._screen_rect().adjusted(26, 22, -26, -22)
        for row, names in enumerate(POSITIONS):
            for col, n in enumerate(names):
                if n == name:
                    return QPointF(s.left() + s.width() * col / 2, s.top() + s.height() * row / 2)
        return None

    def _place(self, animate=True):
        a = self._anchor(self._value)
        if a is None:
            self.anim.set("vis", 0.0)
            return
        if animate:
            self.anim.to("x", a.x(), 380, QEasingCurve.Type.OutBack)
            self.anim.to("y", a.y(), 380, QEasingCurve.Type.OutBack)
            self.anim.to("vis", 1.0, 250)
        else:
            self.anim.set("x", a.x())
            self.anim.set("y", a.y())
            self.anim.set("vis", 1.0)

    def setValue(self, v):
        if v != self._value:
            self._value = v
            self._place()

    def _nearest(self, pos):
        best, bd = None, 1e9
        for names in POSITIONS:
            for n in names:
                a = self._anchor(n)
                d = (a.x() - pos.x()) ** 2 + (a.y() - pos.y()) ** 2
                if d < bd:
                    best, bd = n, d
        return best if bd < 60 ** 2 else None

    def mouseMoveEvent(self, e):
        h = self._nearest(e.position())
        if h != self._hover:
            self._hover = h
            self.update()

    def leaveEvent(self, e):
        self._hover = None
        self.update()

    def mousePressEvent(self, e):
        n = self._nearest(e.position())
        if n:
            self.setValue(n)
            self.changed.emit(n)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        s = self._screen_rect()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("border"))
        p.drawRoundedRect(QRectF(s.center().x() - 8, s.bottom(), 16, 18), 3, 3)
        p.drawRoundedRect(QRectF(s.center().x() - 46, s.bottom() + 16, 92, 8), 4, 4)
        paint_glow(p, s, 12, theme.c("accent"), 0.35, 6)
        g = QLinearGradient(s.topLeft(), s.bottomRight())
        g.setColorAt(0, mix(theme.c("bg3"), theme.c("accent"), 0.12))
        g.setColorAt(1, mix(theme.c("bg1"), theme.c("accent2"), 0.15))
        p.setPen(QPen(theme.c("border"), 2))
        p.setBrush(QBrush(g))
        p.drawRoundedRect(s, 12, 12)
        for names in POSITIONS:
            for n in names:
                a = self._anchor(n)
                hov = n == self._hover
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.c("accent", 0.8) if hov else theme.c("dim", 0.45))
                p.drawEllipse(a, 4 if hov else 3, 4 if hov else 3)
        vis = self.anim.get("vis")
        if vis > 0.01:
            c = QPointF(self.anim.get("x"), self.anim.get("y"))
            bar = QRectF(c.x() - 34, c.y() - 9, 68, 18)
            p.setOpacity(vis)
            paint_glow(p, bar, 9, theme.c("accent"), 1.2, 7)
            p.setBrush(QBrush(accent_gradient(bar)))
            p.drawRoundedRect(bar, 9, 9)
            p.setBrush(QColor(10, 10, 18, 170))
            p.drawRoundedRect(QRectF(bar.left() + 10, bar.center().y() - 2, 48, 4), 2, 2)
            p.setOpacity(1.0)
        else:
            p.setPen(theme.c("dim"))
            p.drawText(s, Qt.AlignmentFlag.AlignCenter, "Custom position (dragged)")


# ------------------------------------------------------------------- sidebar
class NavItem(QAbstractButton):
    def __init__(self, text, icon, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.icon_name = icon
        self.setCheckable(True)
        self.anim = Anim(self)
        self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(lambda c: self.anim.to("sel", 1.0 if c else 0.0, 260))

    def enterEvent(self, e):
        self.anim.to("hover", 1.0, 150)

    def leaveEvent(self, e):
        self.anim.to("hover", 0.0, 260)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        hov, sel = self.anim.get("hover"), self.anim.get("sel")
        r = QRectF(self.rect()).adjusted(10, 3, -10, -3)
        if hov > 0.01 and sel < 0.99:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.c("text", 0.05 * hov))
            p.drawRoundedRect(r, 10, 10)
        col = mix(mix(theme.c("dim"), theme.c("text"), hov), theme.c("accent"), sel)
        x = r.left() + 14 + hov * 3
        draw_icon(p, self.icon_name, QRectF(x, r.center().y() - 9, 18, 18), col, 1.7)
        f = QFont(self.font())
        f.setPixelSize(13)
        f.setWeight(QFont.Weight.DemiBold if sel > 0.5 else QFont.Weight.Normal)
        p.setFont(f)
        p.setPen(mix(mix(theme.c("dim"), theme.c("text"), hov), theme.c("text"), sel))
        p.drawText(QRectF(x + 30, r.top(), r.width() - 50, r.height()), Qt.AlignmentFlag.AlignVCenter, self.text())


class _NavList(QWidget):
    """The scrollable list of sidebar items; paints the glowing pill behind the selected one."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.anim = Anim(self)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 2, 0, 10)
        self.lay.setSpacing(3)
        self.current_item = None

    def paintEvent(self, e):
        if self.current_item is None:
            return
        p = QPainter(self)
        p.setRenderHint(AA)
        y, h = self.anim.get("y"), self.anim.get("h", 44)
        r = QRectF(10, y + 3, self.width() - 20, h - 6)
        paint_glow(p, r, 10, theme.c("accent"), 0.45, 6)
        g = QLinearGradient(r.topLeft(), r.topRight())
        g.setColorAt(0, theme.c("accent", 0.22))
        g.setColorAt(1, theme.c("accent2", 0.05))
        p.setPen(QPen(theme.c("accent", 0.35), 1))
        p.setBrush(QBrush(g))
        p.drawRoundedRect(r, 10, 10)
        bar = QRectF(r.left() + 2, r.top() + 10, 3, r.height() - 20)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(accent_gradient(bar)))
        p.drawRoundedRect(bar, 1.5, 1.5)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.current_item is not None:
            g = self.current_item.geometry()
            self.anim.set("y", g.y())
            self.anim.set("h", g.height())


class Sidebar(QWidget):
    """Search on top, then the page list. The list keeps roomy, full-size items and scrolls when it's
    taller than the window."""

    changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(232)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 10)
        outer.setSpacing(6)
        self.lay = QVBoxLayout()          # top area (the search box goes here)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(2)
        outer.addLayout(self.lay)
        self.nav = _NavList()
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.nav)
        outer.addWidget(self.scroll, 1)
        self.footer = QVBoxLayout()
        outer.addLayout(self.footer)
        self.items = []
        self.current = 0

    @property
    def anim(self):
        return self.nav.anim

    def add_section(self, text):
        l = QLabel(text.upper())
        l.setObjectName("dim")
        l.setStyleSheet("font-size: 10px; font-weight: 700; letter-spacing: 1.5px; padding: 16px 0 6px 26px;")
        self.nav.lay.addWidget(l)

    def add(self, text, icon):
        item = NavItem(text, icon, self.nav)
        idx = len(self.items)
        self.items.append(item)
        self.nav.lay.addWidget(item)
        item.clicked.connect(lambda _=False, i=idx: self.select(i, emit=True))
        return idx

    def finish(self):
        self.nav.lay.addStretch(1)
        QTimer.singleShot(0, lambda: self.select(self.current, animate=False))

    def select(self, i, emit=False, animate=True):
        self.current = i
        for k, it in enumerate(self.items):
            it.blockSignals(True)
            it.setChecked(k == i)
            it.blockSignals(False)
            it.anim.to("sel", 1.0 if k == i else 0.0, 260)
        item = self.items[i]
        self.nav.current_item = item
        g = item.geometry()
        if animate:
            self.nav.anim.to("y", g.y(), 420, QEasingCurve.Type.OutBack)
        else:
            self.nav.anim.set("y", g.y())
        self.nav.anim.set("h", g.height())
        self.scroll.ensureWidgetVisible(item, 0, 24)
        if emit:
            self.changed.emit(i)


class ProgressBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.setFixedHeight(14)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, v):
        self.value = max(0.0, min(1.0, float(v)))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        t = QRectF(4, self.height() / 2 - 3, self.width() - 8, 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("bg3"))
        p.drawRoundedRect(t, 3, 3)
        if self.value > 0:
            f = QRectF(t.left(), t.top(), t.width() * self.value, t.height())
            p.setBrush(QBrush(accent_gradient(t, 0.3)))
            p.drawRoundedRect(f.adjusted(-2, -3, 2, 3), 5, 5)
            p.setBrush(QBrush(accent_gradient(t)))
            p.drawRoundedRect(f, 3, 3)


class FlashHighlight(QWidget):
    """Briefly glows around a widget (used when settings search jumps to a setting)."""

    def __init__(self, target: QWidget):
        super().__init__(target)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(target.rect())
        self.a = QVariantAnimation(self)
        self.a.setStartValue(1.0)
        self.a.setEndValue(0.0)
        self.a.setDuration(1800)
        self.a.setEasingCurve(QEasingCurve.Type.InQuad)
        self.a.valueChanged.connect(lambda _v: self.update())
        self.a.finished.connect(self.deleteLater)
        self.show()
        self.raise_()
        self.a.start()

    def paintEvent(self, e):
        v = float(self.a.currentValue() or 0.0)
        p = QPainter(self)
        p.setRenderHint(AA)
        r = QRectF(self.rect()).adjusted(3, 2, -3, -2)
        p.setPen(QPen(theme.c("accent", 0.9 * v), 2))
        p.setBrush(theme.c("accent", 0.12 * v))
        p.drawRoundedRect(r, 10, 10)


def row(label, control, desc=None, value_label=None):
    """A settings row: label (+description) on the left, the control on the right."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 2, 0, 2)
    h.setSpacing(14)
    left = QVBoxLayout()
    left.setSpacing(1)
    l = QLabel(label)
    l.setObjectName("rowLabel")
    left.addWidget(l)
    if desc:
        d = QLabel(desc)
        d.setObjectName("rowDesc")
        d.setWordWrap(True)
        left.addWidget(d)
    h.addLayout(left, 0 if isinstance(control, GlowSlider) else 1)
    if isinstance(control, GlowSlider):
        l.setMinimumWidth(128)
        h.addWidget(control, 1)
    else:
        h.addWidget(control, 0, Qt.AlignmentFlag.AlignRight)
    if value_label is not None:
        value_label.setMinimumWidth(58)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(value_label)
    return w
