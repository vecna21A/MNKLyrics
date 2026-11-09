"""MNKLyrics logo: drawn in code so it is crisp at every size (tray, taskbar, .exe icon)."""

import struct

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QFontMetricsF, QIcon, QImage, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap)

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def _alpha(c: QColor, a: float) -> QColor:
    c = QColor(c)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


TILE_TOP, TILE_BOTTOM = QColor("#24262B"), QColor("#0F1013")
INK = QColor("#FFFFFF")
ACCENT = QColor("#E5484D")  # the red "playing line"


def _wordmark_font(px: int) -> QFont:
    family = "Bahnschrift" if "Bahnschrift" in QFontDatabase.families() else "Segoe UI"
    f = QFont(family)
    f.setWeight(QFont.Weight.Bold)
    f.setStretch(QFont.Stretch.Condensed)
    f.setPixelSize(max(1, px))
    return f


def paint_logo(p: QPainter, r: QRectF, detail: bool = True):
    """MNK wordmark: bold condensed white type on a charcoal tile with a red playing-line underline.
    The same full design is used at every size."""
    detail = True
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    w = r.width()

    # tile: soft charcoal gradient, gentle top sheen, hairline edge
    rad = w * 0.225
    shape = QPainterPath()
    shape.addRoundedRect(r, rad, rad)
    bg = QLinearGradient(r.topLeft(), r.bottomRight())
    bg.setColorAt(0, TILE_TOP)
    bg.setColorAt(1, TILE_BOTTOM)
    p.fillPath(shape, QBrush(bg))
    sheen = QLinearGradient(r.topLeft(), QPointF(r.left(), r.top() + w * 0.55))
    sheen.setColorAt(0, QColor(255, 255, 255, 40))
    sheen.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillPath(shape, QBrush(sheen))
    edge = max(1.0, w * 0.008)
    p.setPen(QPen(QColor(255, 255, 255, 30), edge))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(shape)

    # wordmark
    text = "MNK" if detail else "M"
    f = _wordmark_font(int(w * (0.36 if detail else 0.66)))
    tw = QFontMetricsF(f).horizontalAdvance(text)
    path = QPainterPath()
    path.addText(r.left() + (w - tw) / 2, r.top() + w * (0.6 if detail else 0.76), f, text)
    path.setFillRule(Qt.FillRule.WindingFill)  # the font's overlapping strokes must not cut holes
    p.fillPath(path, INK)

    # red underline, like the line currently being sung
    if detail:
        bar = QRectF(r.left() + w * 0.33, r.top() + w * 0.69, w * 0.34, w * 0.045)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ACCENT)
        p.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)
    p.restore()


def render_logo(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    margin = max(0.0, size * 0.03) if size >= 32 else 0.0
    paint_logo(p, QRectF(margin, margin, size - 2 * margin, size - 2 * margin))
    p.end()
    return img


def render_publisher_logo(size: int) -> QImage:
    """The MNK logo with a red 'upload' badge — the Publisher tool's icon."""
    img = render_logo(size)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    d = size * 0.44
    badge = QRectF(size - d - size * 0.01, size - d - size * 0.01, d, d)
    p.setPen(QPen(QColor("#0F1013"), max(1.0, size * 0.035)))
    p.setBrush(ACCENT)
    p.drawEllipse(badge)
    c = badge.center()
    a = d * 0.24
    pen = QPen(QColor("#FFFFFF"), max(1.2, d * 0.13), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
               Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawLine(QPointF(c.x(), c.y() + a), QPointF(c.x(), c.y() - a))
    p.drawLine(QPointF(c.x() - a * 0.8, c.y() - a * 0.15), QPointF(c.x(), c.y() - a))
    p.drawLine(QPointF(c.x() + a * 0.8, c.y() - a * 0.15), QPointF(c.x(), c.y() - a))
    p.end()
    return img


def app_icon(renderer=None) -> QIcon:
    renderer = renderer or render_logo
    icon = QIcon()
    for s in ICO_SIZES:
        icon.addPixmap(QPixmap.fromImage(renderer(s)))
    return icon


def write_ico(path: str, sizes=ICO_SIZES, renderer=None):
    """Multi-resolution .ico with PNG-compressed entries (supported since Windows Vista)."""
    renderer = renderer or render_logo
    blobs = []
    for s in sizes:
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        renderer(s).save(buf, "PNG")
        buf.close()
        blobs.append((s, bytes(data)))
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = len(header) + 16 * len(blobs)
    entries, payload = b"", b""
    for s, png in blobs:
        dim = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(payload))
        payload += png
    with open(path, "wb") as fh:
        fh.write(header + entries + payload)
