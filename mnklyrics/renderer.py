"""Lyrics renderer shared by the desktop overlay and the live preview.

Every lyric line is pre-rendered once into cached images (shadow, blurred glow,
outline, fill), so each animation frame only composites a few images. That
keeps the overlay light enough to run next to a game.
"""

import bisect
import math
import random
from collections import OrderedDict

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene

from .lyrics import LyricLine

STYLE_KEYS = (
    "font_family", "font_size", "font_weight", "italic", "uppercase", "letter_spacing", "line_spacing",
    "align", "text_color", "highlight_style", "highlight_color", "glow_enabled", "glow_color",
    "glow_strength", "glow_radius", "outline_enabled", "outline_color", "outline_width",
    "shadow_enabled", "shadow_color", "shadow_opacity", "shadow_offset", "width", "padding",
)

ARGB = QImage.Format.Format_ARGB32_Premultiplied
WAVE_BARS = 46
HINTS = (QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
         | QPainter.RenderHint.TextAntialiasing)


def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def lerp(a, b, t):
    return a + (b - a) * t


def ease_out_cubic(t):
    return 1 - (1 - t) ** 3


def ease_out_back(t, s=1.9):
    t -= 1
    return t * t * ((s + 1) * t + s) + 1


def qcolor(value, alpha=1.0) -> QColor:
    c = QColor(value)
    c.setAlphaF(clamp(alpha))
    return c


def contrast_color(hex_color) -> QColor:
    c = QColor(hex_color)
    lum = 0.299 * c.redF() + 0.587 * c.greenF() + 0.114 * c.blueF()
    return QColor("#0B0B10") if lum > 0.6 else QColor("#FFFFFF")


def blur_image(img: QImage, radius: float) -> QImage:
    """Gaussian-ish blur via QGraphicsBlurEffect (only used when building cached sprites)."""
    if radius <= 0.5:
        return img
    dpr = img.devicePixelRatio()
    src = QImage(img)
    src.setDevicePixelRatio(1.0)
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(src))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius * dpr)
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)
    out = QImage(src.size(), ARGB)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    area = QRectF(0, 0, src.width(), src.height())
    scene.render(p, area, area)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


class Sprite:
    __slots__ = ("base", "active", "w", "h", "m", "subs", "box_h")


class LyricsRenderer:
    def __init__(self):
        self.cfg = {}
        self.dpr = 1.0
        self._sig = None
        self._cache = OrderedDict()
        self._bg_cache = None
        self._rows = {}            # text -> wrapped row count (cheap; no sprite needed)
        self._reserve = None       # tallest content any line window of this song needs
        self.font = QFont()
        self.small_font = QFont()
        self.v_anchor = "center"   # which edge the panel hugs inside the window: top / bottom / center
        self.lay = None            # animated layout: before / active / content heights
        self.panel_rect = QRectF() # last painted panel, in widget coordinates (hover detection)

        self.lines = []
        self.times = []
        self.synced = True
        self.active = 0
        self.scroll = 0.0
        self.vel = 0.0
        self.emph = {}
        self.trans = None
        self.entrance = 1.0
        self.entrance_dur = 0.45
        self.kp = 0.0

        self.status = "Waiting for Spotify…"
        self.busy = False
        self.title = ""
        self.artist = ""
        self.badge = ""
        self.position = 0.0
        self.duration = 0.0
        self.clock = 0.0
        self.toast_text = ""
        self.toast_t = 99.0

        # song card shown when a song has no lyrics: cover, title, artist and a live sound-wave timeline
        self.card = ""              # "" = no card, else the small note under the artist ("No lyrics found")
        self.card_enabled = True    # the Full-screen Overlay draws its own song info, so it turns this off
        self.art = None             # QImage of the cover
        self._art_px = None         # (size, dpr, pixmap) rounded cover, cached
        self.bars = [0.0] * WAVE_BARS   # live visualizer bar heights (0..1)
        self._rng = random.Random(7)
        self.live = 0.0             # smoothed loudness right now
        self._peak = 0.05           # recent loudest level, so quiet and loud songs both fill the bars
        self._low = 0.0             # recent quietest level

        # playlist button on the left of the song's progress track (opens the song list)
        self.playlist_button = False
        self.playlist_alpha = 1.0   # it can be "show on hover"
        self.playlist_hover = False
        self.playlist_open = False
        self.playlist_rect = QRectF()   # where it was drawn (widget coordinates), for clicks

    # ----------------------------------------------------------- configuration
    def set_config(self, cfg: dict, dpr: float = 1.0):
        self.cfg = dict(cfg)
        self._reserve = None
        sig = tuple(cfg.get(k) for k in STYLE_KEYS) + (round(dpr, 2),)
        if sig != self._sig:
            self._sig = sig
            self.dpr = dpr
            self._cache.clear()
            self._rows.clear()
            f = QFont(cfg["font_family"])
            f.setPixelSize(max(6, int(cfg["font_size"])))
            f.setWeight(QFont.Weight(int(clamp(round(cfg["font_weight"] / 100) * 100, 100, 900))))
            f.setItalic(bool(cfg["italic"]))
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, float(cfg["letter_spacing"]))
            f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
            f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
            self.font = f
            sf = QFont(cfg["font_family"])
            sf.setPixelSize(max(11, int(cfg["font_size"] * 0.4)))
            sf.setWeight(QFont.Weight.DemiBold)
            self.small_font = sf

    # metrics ---------------------------------------------------------------
    def lh(self):
        return self.cfg["font_size"] * self.cfg["line_spacing"]

    def outer_margin(self):
        return int(max(10, self.cfg["glow_radius"] * 0.9 + 8))

    def header_h(self):
        if self.card_on():
            return 0.0
        return self.small_font.pixelSize() * 1.9 if self.cfg["show_track_info"] else 0.0

    def progress_h(self):
        if self.card_on():
            return 0.0
        if self.playlist_button:
            return 26.0  # room for the playlist button next to the track
        return 14.0 if self.cfg["show_progress"] else 0.0

    PL_BTN = 22.0

    def _paint_playlist_button(self, p, x, cy):
        """The round playlist button; returns the width it takes (0 when it's off)."""
        if not self.playlist_button:
            self.playlist_rect = QRectF()
            return 0.0
        d = self.PL_BTN
        r = QRectF(x, cy - d / 2, d, d)
        a = clamp(self.playlist_alpha)
        self.playlist_rect = r if a > 0.4 else QRectF()
        if a <= 0.01:
            return d + 10
        hl = self.cfg["highlight_color"]
        p.save()
        p.setOpacity(p.opacity() * a)
        p.setPen(QPen(qcolor(hl, 0.9 if (self.playlist_hover or self.playlist_open) else 0.55), 1.3))
        p.setBrush(qcolor(hl, 0.35 if self.playlist_open else 0.22 if self.playlist_hover else 0.1))
        p.drawEllipse(r)
        from .widgets import draw_icon
        draw_icon(p, "playlist", r.adjusted(5, 5, -5, -5),
                  qcolor(hl) if (self.playlist_hover or self.playlist_open) else qcolor(self.cfg["text_color"], 0.9),
                  1.6)
        p.restore()
        return d + 10

    # song card --------------------------------------------------------------
    def card_on(self):
        return bool(self.card) and self.card_enabled and not self.lines and self.cfg.get("no_lyrics_card", True)

    def _card_metrics(self):
        fs = self.cfg["font_size"]
        art = clamp(fs * 2.3, 56.0, 132.0)
        wave = clamp(fs * 1.05, 22.0, 52.0)
        gap = clamp(fs * 0.35, 8.0, 16.0)
        return art, wave, gap

    def _card_h(self):
        art, wave, gap = self._card_metrics()
        return art + gap + wave + 12  # visualizer, then the song's progress track under it

    def set_art(self, img):
        self.art = img if img is not None and not img.isNull() else None
        self._art_px = None

    def push_level(self, level: float):
        """Feed the player's loudness (0..1, ~30 times a second) while the card is shown."""
        level = max(0.0, float(level))
        self._peak = max(level, self._peak * 0.997, 0.05)
        # follow the dips quickly and creep up slowly: bars show the music's rise and fall, not its volume
        self._low += (level - self._low) * (0.35 if level < self._low else 0.004)
        v = clamp((level - self._low) / max(0.05, self._peak - self._low)) ** 0.8
        self.live += (v - self.live) * (0.6 if v > self.live else 0.3)
        # an equalizer-style bounce: every bar follows the live loudness with its own wobble, tallest
        # in the middle, and falls back smoothly between beats (see update())
        n = WAVE_BARS
        for i in range(n):
            env = 0.45 + 0.55 * math.sin(math.pi * (i + 0.5) / n) ** 0.7
            target = self.live * env * (0.45 + 0.55 * self._rng.random())
            if target > self.bars[i]:
                self.bars[i] += (target - self.bars[i]) * 0.7

    def text_width(self):
        return max(80.0, self.cfg["width"] - 2 * self.outer_margin() - 2 * self.cfg["padding"])

    def rows(self, text):
        """How many rows a lyric wraps to at the configured width."""
        if not text:
            return 1
        n = self._rows.get(text)
        if n is None:
            shown = text.upper() if self.cfg["uppercase"] else text
            n = len(self._wrap(shown, QFontMetricsF(self.font), self.text_width()))
            self._rows[text] = n
        return n

    def _layout_for(self, i):
        """(before, active, total) content heights when line ``i`` is the one being sung.
        Missing neighbours (start/end of song) still reserve one row so the panel stays steady."""
        cfg = self.cfg
        lh, cs = self.lh(), cfg["context_scale"]
        n = len(self.lines)

        def h(j, scale):
            rows = self.rows(self.lines[j].text) if 0 <= j < n else 1
            return rows * lh * scale

        before = sum(h(j, cs) for j in range(i - cfg["lines_before"], i))
        active = max(h(i, 1.0), lh * 1.15)
        after = sum(h(j, cs) for j in range(i + 1, i + cfg["lines_after"] + 1))
        return before, active, before + active + after

    def reserve_content_h(self):
        """Height the window must reserve so every line window of this song fits without clipping."""
        if self.card_on():
            return self._card_h()
        if self._reserve is None:
            best = self._layout_for(0)[2] if not self.lines else max(
                self._layout_for(i)[2] for i in range(len(self.lines)))
            self._reserve = best
        return self._reserve

    def panel_h(self, content_h):
        return 2 * self.cfg["padding"] + self.header_h() + content_h + self.progress_h()

    def preferred_size(self):
        m = self.outer_margin()
        h = 2 * m + self.panel_h(self.reserve_content_h())
        return int(self.cfg["width"]), int(math.ceil(h))

    def _target_lay(self):
        if self.card_on():
            h = self._card_h()
            return 0.0, h, h
        return self._layout_for(self.active)

    # ----------------------------------------------------------------- state
    def set_track(self, title, artist, duration):
        if (title, artist) != (self.title, self.artist):
            self.bars = [0.0] * WAVE_BARS
            self.live = 0.0
            self.set_art(None)  # the new cover arrives a moment later
        self.title, self.artist, self.duration = title, artist, duration

    def set_status(self, text, busy=False, card=""):
        """Show a message instead of lyrics. ``card`` (e.g. "No lyrics found") shows the song card instead."""
        self.lines, self.times = [], []
        self.status, self.busy, self.badge = text, busy, ""
        self.card = card
        self.active = 0
        self._reserve = None
        self.lay = None

    def set_lyrics(self, lines, synced=True, duration=0.0):
        lines = [l for l in lines]
        if synced and lines and lines[0].time > 3500 and lines[0].text:
            lines.insert(0, LyricLine(0.0, ""))  # intro interlude dots
        if not synced:
            dur = duration or self.duration or 180000.0
            n = max(1, len(lines))
            start, end = dur * 0.06, dur * 0.94
            lines = [LyricLine(start + (end - start) * i / n, l.text) for i, l in enumerate(lines)]
        self.lines = lines
        self.times = [l.time for l in lines]
        self.synced = synced
        self.badge = "" if synced else "UNSYNCED"
        self.active = 0
        self.scroll = 0.0
        self.vel = 0.0
        self.emph = {0: 1.0}
        self.trans = None
        self.entrance = 1.0
        self.kp = 0.0
        self.status = ""
        self.busy = False
        self.card = ""
        self._reserve = None
        self.lay = None  # snap to the new song's layout instead of animating from the old one

    def toast(self, text):
        self.toast_text = text
        self.toast_t = 0.0

    # ---------------------------------------------------------------- update
    def update(self, dt: float, pos: float):
        dt = clamp(dt, 0.0, 0.1)
        cfg = self.cfg
        spd = max(0.1, cfg["anim_speed"])
        self.clock += dt
        self.position = pos
        if self.card:
            fall = dt * 1.8
            self.bars = [b - fall if b > fall else 0.0 for b in self.bars]
        self.toast_t += dt
        n = len(self.lines)
        if n:
            idx = int(clamp(bisect.bisect_right(self.times, pos) - 1, 0, n - 1))
            if idx != self.active:
                self._change(idx)
            start = self.times[idx]
            end = self.times[idx + 1] if idx + 1 < n else start + 6000
            gap = max(1.0, end - start)
            text = self.lines[idx].text
            if text and gap > 7000:
                est = min(gap, max(1400.0, len(text) * 85 + 600))
            else:
                est = gap * 0.95
            lp = clamp((pos - start) / est)
            if lp < self.kp - 0.15:
                self.kp = lp
            self.kp += (lp - self.kp) * min(1.0, dt * 18)

        anim = cfg["animation"]
        if anim in ("slide", "bounce"):
            k = 140.0 * spd * spd
            c = 2 * math.sqrt(k) * (1.0 if anim == "slide" else 0.36)
            steps = int(dt / 0.008) + 1
            h = dt / steps
            for _ in range(steps):
                a = k * (self.active - self.scroll) - c * self.vel
                self.vel += a * h
                self.scroll += self.vel * h
        else:
            self.scroll, self.vel = float(self.active), 0.0

        if abs(self.active - self.scroll) < 0.0005 and abs(self.vel) < 0.0005:
            self.scroll, self.vel = float(self.active), 0.0

        rate = min(1.0, dt * 9.0 * spd)
        for i in list(self.emph):
            if i != self.active:
                self.emph[i] += (0.0 - self.emph[i]) * (1.0 if anim == "none" else rate)
                if self.emph[i] < 0.004:
                    del self.emph[i]
        e = self.emph.get(self.active, 0.0)
        e = 1.0 if anim == "none" else e + (1.0 - e) * rate
        self.emph[self.active] = 1.0 if e > 0.998 else e

        if self.trans is not None:
            self.trans["t"] += dt * spd / 0.4
            if self.trans["t"] >= 1.0:
                self.trans = None
        self.entrance = min(1.0, self.entrance + dt * spd / self.entrance_dur)

        # panel height follows the lines it has to show (e.g. a lyric wrapping onto two rows)
        target = self._target_lay()
        if self.lay is None or anim == "none":
            self.lay = list(target)
        else:
            k = min(1.0, dt * 10.0 * spd)
            for j in range(3):
                d = target[j] - self.lay[j]
                self.lay[j] = target[j] if abs(d) < 0.3 else self.lay[j] + d * k

    def in_transition(self) -> bool:
        """True during short, fast animations (line change, toast) that deserve full frame rate."""
        if self.toast_t < 2.3 or self.trans is not None or self.entrance < 1.0:
            return True
        if self.scroll != self.active or self.vel != 0.0:
            return True
        for i, e in self.emph.items():
            if (i == self.active and e < 1.0) or i != self.active:
                return True
        return False

    def needs_frame(self, playing: bool) -> bool:
        """True while anything on screen is moving; lets the overlay skip idle repaints."""
        if self.busy or self.in_transition():
            return True
        if playing and self.card_on():
            return True  # the sound wave moves with the music
        if self.lay is not None and list(self._target_lay()) != self.lay:
            return True  # panel still resizing
        if playing and self.lines:
            if not self.lines[self.active].text:
                return True  # pulsing interlude dots
            if self.synced and self.cfg["highlight_style"] in ("karaoke", "underline") and self.kp < 0.999:
                return True
        return False

    def _change(self, idx):
        prev = self.active
        self.active = idx
        self.kp = 0.0
        self.entrance = 0.0
        text = self.lines[idx].text
        self.entrance_dur = clamp(len(text) * 0.03, 0.35, 1.0) if self.cfg["entrance"] == "typewriter" else 0.45
        if abs(idx - self.scroll) > 4:
            self.scroll = idx - math.copysign(0.8, idx - prev)
            self.vel = 0.0
            self.emph = {}
        if self.cfg["animation"] in ("fade", "zoom"):
            self.trans = {"prev": prev, "t": 0.0}

    # --------------------------------------------------------------- sprites
    def _wrap(self, text, fm, max_w):
        words = text.split(" ")
        out, cur = [], ""
        for w in words:
            trial = w if not cur else cur + " " + w
            if fm.horizontalAdvance(trial) <= max_w or not cur:
                cur = trial
            else:
                out.append(cur)
                cur = w
        out.append(cur)
        return [(s, fm.horizontalAdvance(s)) for s in out]

    def _new_img(self, w, h):
        img = QImage(max(1, int(math.ceil(w * self.dpr))), max(1, int(math.ceil(h * self.dpr))), ARGB)
        img.setDevicePixelRatio(self.dpr)
        img.fill(Qt.GlobalColor.transparent)
        return img

    def _mask(self, path, w, h, color, stroke=0.0):
        img = self._new_img(w, h)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if stroke > 0:
            p.strokePath(path, QPen(color, stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                    Qt.PenJoinStyle.RoundJoin))
        p.fillPath(path, color)
        p.end()
        return img

    def _layer(self, path, w, h, fill, glow_color=None, glow_strength=0.0, outline=True):
        cfg = self.cfg
        img = self._new_img(w, h)
        p = QPainter(img)
        p.setRenderHints(HINTS)
        if cfg["shadow_enabled"] and cfg["shadow_opacity"] > 0:
            off = cfg["shadow_offset"]
            sh = blur_image(self._mask(path, w, h, qcolor(cfg["shadow_color"]), 1.0), 3.0)
            p.setOpacity(clamp(cfg["shadow_opacity"]))
            p.drawImage(QPointF(off, off), sh)
            p.setOpacity(1.0)
        if glow_color is not None and glow_strength > 0:
            r = max(2.0, float(cfg["glow_radius"]))
            mask = self._mask(path, w, h, qcolor(glow_color), r * 0.18)
            wide = blur_image(mask, r)
            tight = blur_image(mask, r * 0.35)
            p.setOpacity(clamp(glow_strength))
            p.drawImage(QPointF(0, 0), wide)
            if glow_strength > 1.0:
                p.setOpacity(clamp(glow_strength - 1.0))
                p.drawImage(QPointF(0, 0), wide)
            p.setOpacity(clamp(glow_strength * 0.75))
            p.drawImage(QPointF(0, 0), tight)
            p.setOpacity(1.0)
        if outline and cfg["outline_enabled"] and cfg["outline_width"] > 0:
            p.strokePath(path, QPen(qcolor(cfg["outline_color"]), cfg["outline_width"] * 2,
                                    Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.fillPath(path, fill)
        p.end()
        return img

    def sprite(self, text):
        sp = self._cache.get(text)
        if sp is not None:
            self._cache.move_to_end(text)
            return sp
        cfg = self.cfg
        shown = text.upper() if cfg["uppercase"] else text
        fm = QFontMetricsF(self.font)
        max_w = self.text_width()
        subs = self._wrap(shown, fm, max_w)
        lh = self.lh()
        m = int(cfg["glow_radius"] * 1.3 + cfg["outline_width"] * 2 + cfg["shadow_offset"] + 10)
        w, h = max_w + 2 * m, lh * len(subs) + 2 * m
        asc, desc = fm.ascent(), fm.descent()
        path = QPainterPath()
        rects = []
        for j, (s, sw) in enumerate(subs):
            x = m + self._align_off(sw, max_w)
            top = m + j * lh
            path.addText(x, top + (lh - asc - desc) / 2 + asc, self.font, s)
            rects.append(QRectF(x, top, sw, lh))

        style = cfg["highlight_style"]
        hl, txt = cfg["highlight_color"], cfg["text_color"]
        glow = cfg["glow_color"] if cfg["glow_enabled"] else None
        strength = cfg["glow_strength"] if cfg["glow_enabled"] else 0.0
        sp = Sprite()
        sp.base = self._layer(path, w, h, qcolor(txt))
        if style in ("karaoke", "color", "underline"):
            sp.active = self._layer(path, w, h, qcolor(hl), glow, strength)
        elif style == "glow":
            sp.active = self._layer(path, w, h, qcolor(txt), hl, max(0.8, strength) * 1.35)
        elif style == "pill":
            sp.active = self._layer(path, w, h, contrast_color(hl), None, 0.0, outline=False)
        else:
            sp.active = self._layer(path, w, h, qcolor(txt), glow, strength)
        sp.w, sp.h, sp.m, sp.subs, sp.box_h = w, h, m, rects, lh * len(subs)
        self._cache[text] = sp
        while len(self._cache) > 48:
            self._cache.popitem(last=False)
        return sp

    def _align_off(self, sw, max_w):
        a = self.cfg["align"]
        if a == "left":
            return 0.0
        if a == "right":
            return max_w - sw
        return (max_w - sw) / 2

    # ----------------------------------------------------------------- paint
    def paint(self, p: QPainter, rect: QRectF, alpha: float = 1.0):
        cfg = self.cfg
        p.save()
        p.setRenderHints(HINTS)
        p.setOpacity(alpha)
        m = self.outer_margin()
        if self.lay is None:
            self.lay = list(self._target_lay())
        area = rect.adjusted(m, m, -m, -m)
        ph = min(area.height(), self.panel_h(self.lay[2]))
        if self.v_anchor == "top":
            top = area.top()
        elif self.v_anchor == "bottom":
            top = area.bottom() - ph
        else:
            top = area.center().y() - ph / 2
        bg = QRectF(area.left(), top, area.width(), ph)
        self.panel_rect = bg
        self._paint_bg(p, bg)
        pad = cfg["padding"]
        inner = bg.adjusted(pad, pad, -pad, -pad)
        hh = self.header_h()
        if hh:
            self._paint_header(p, QRectF(inner.left(), inner.top(), inner.width(), hh))
        content = QRectF(inner.left(), inner.top() + hh, inner.width(), self.lay[2])
        if not self.card_on():  # the song card has its own timeline
            ph = self.progress_h()
            cy = content.bottom() + 6 + (ph - 6) / 2 if self.playlist_button else content.bottom() + 8
            used = self._paint_playlist_button(p, inner.left(), cy)
            if cfg["show_progress"]:
                self._paint_progress(p, QRectF(inner.left() + used, cy - 2, inner.width() - used, 4))

        p.save()
        p.setClipRect(QRectF(rect.left(), bg.top(), rect.width(), bg.height()))
        if self.lines:
            if self.trans is not None:
                t = ease_out_cubic(clamp(self.trans["t"]))
                prev = self.trans["prev"]
                zoom = cfg["animation"] == "zoom"
                self._paint_pass(p, content, float(prev), lambda i: 1.0 if i == prev else 0.0,
                                 alpha * (1 - t), lerp(1.0, 1.08, t) if zoom else 1.0, False)
                self._paint_pass(p, content, float(self.active), lambda i: 1.0 if i == self.active else 0.0,
                                 alpha * t, lerp(0.9, 1.0, t) if zoom else 1.0, True)
            else:
                self._paint_pass(p, content, self.scroll, lambda i: self.emph.get(i, 0.0), alpha, 1.0, True)
        elif self.card_on():
            self._paint_card(p, content, alpha)
        else:
            self._paint_status(p, content, alpha)
        p.restore()
        self._paint_toast(p, bg, alpha)
        p.restore()

    def _paint_bg(self, p, bg):
        """Draw the background panel from a cached image (rebuilt only when its look or size changes)."""
        cfg = self.cfg
        if not cfg["bg_enabled"]:
            return
        # Cached at a fixed height, then drawn as three slices (top cap, stretched middle, bottom cap)
        # so the panel can grow/shrink smoothly without re-rendering its glow every frame.
        pad = 10
        cap = cfg["bg_radius"] + pad + 14
        cached_h = max(bg.height(), 2 * cap + 8)
        key = (round(bg.width(), 1), self.dpr, cfg["bg_color"], cfg["bg_opacity"], cfg["bg_radius"],
               cfg["bg_border"], cfg["highlight_color"])
        if self._bg_cache is None or self._bg_cache[0] != key or self._bg_cache[3] < cached_h - 0.5:
            cached_h = max(cached_h, self._bg_cache[3] if self._bg_cache and self._bg_cache[0] == key else 0)
            img = self._new_img(bg.width() + 2 * pad, cached_h + 2 * pad)
            ip = QPainter(img)
            ip.setRenderHints(HINTS)
            self._draw_bg(ip, QRectF(pad, pad, bg.width(), cached_h))
            ip.end()
            self._bg_cache = (key, img, pad, cached_h)
        _, img, pad, ch = self._bg_cache
        if abs(ch - bg.height()) < 0.5:
            p.drawImage(QPointF(bg.left() - pad, bg.top() - pad), img)
            return
        W = bg.width() + 2 * pad
        full_h = ch + 2 * pad
        cap = min(cap, (bg.height() + 2 * pad) / 2)
        d = self.dpr
        x = bg.left() - pad
        top_y, bottom_y = bg.top() - pad, bg.bottom() + pad
        p.drawImage(QRectF(x, top_y, W, cap), img, QRectF(0, 0, W * d, cap * d))
        p.drawImage(QRectF(x, bottom_y - cap, W, cap), img, QRectF(0, (full_h - cap) * d, W * d, cap * d))
        mid = bottom_y - cap - (top_y + cap)
        if mid > 0:
            p.drawImage(QRectF(x, top_y + cap, W, mid), img,
                        QRectF(0, (full_h / 2 - 1) * d, W * d, 2 * d))

    def _draw_bg(self, p, bg):
        cfg = self.cfg
        r = cfg["bg_radius"]
        path = QPainterPath()
        path.addRoundedRect(bg, r, r)
        if cfg["bg_border"]:
            for k, a in ((6, 0.04), (3.5, 0.07), (1.5, 0.12)):
                p.setPen(QPen(qcolor(cfg["highlight_color"], a * 1.4), k * 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(bg, r, r)
        p.fillPath(path, qcolor(cfg["bg_color"], cfg["bg_opacity"]))
        sheen = QLinearGradient(bg.topLeft(), QPointF(bg.left(), bg.top() + bg.height() * 0.6))
        sheen.setColorAt(0, QColor(255, 255, 255, int(16 * min(1.0, cfg["bg_opacity"] * 2.2))))
        sheen.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillPath(path, QBrush(sheen))
        if cfg["bg_border"]:
            p.setPen(QPen(qcolor(cfg["highlight_color"], 0.35), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(bg.adjusted(0.5, 0.5, -0.5, -0.5), r, r)

    def _paint_header(self, p, r):
        cfg = self.cfg
        if not self.title:
            return
        p.setFont(self.small_font)
        fm = QFontMetricsF(self.small_font)
        note = "♪  "
        text = self.title + (f"  •  {self.artist}" if self.artist else "")
        if self.badge:
            text += f"   [{self.badge}]"
        nw = fm.horizontalAdvance(note)
        text = fm.elidedText(text, Qt.TextElideMode.ElideRight, r.width() - nw)
        tw = nw + fm.horizontalAdvance(text)
        a = cfg["align"]
        x = r.left() if a == "left" else r.right() - tw if a == "right" else r.center().x() - tw / 2
        y = r.top() + fm.ascent()
        p.setPen(QColor(0, 0, 0, 140))
        p.drawText(QPointF(x + nw + 1, y + 1), text)
        p.setPen(qcolor(cfg["highlight_color"], 0.95))
        p.drawText(QPointF(x, y), note)
        p.setPen(qcolor(cfg["text_color"], 0.72))
        p.drawText(QPointF(x + nw, y), text)

    def _paint_progress(self, p, r):
        cfg = self.cfg
        frac = clamp(self.position / self.duration) if self.duration > 0 else 0.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(cfg["text_color"], 0.14))
        p.drawRoundedRect(r, 2, 2)
        if frac <= 0:
            return
        fill = QRectF(r.left(), r.top(), r.width() * frac, r.height())
        hl = cfg["highlight_color"]
        p.setBrush(qcolor(hl, 0.18))
        p.drawRoundedRect(fill.adjusted(-2, -3, 2, 3), 5, 5)
        p.setBrush(qcolor(hl))
        p.drawRoundedRect(fill, 2, 2)
        g = QRadialGradient(QPointF(fill.right(), fill.center().y()), 9)
        g.setColorAt(0, qcolor(hl, 0.9))
        g.setColorAt(1, qcolor(hl, 0.0))
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(fill.right(), fill.center().y()), 9, 9)

    def _paint_pass(self, p, content, scroll, emph_fn, alpha, zoom, is_current):
        cfg = self.cfg
        n = len(self.lines)
        cs = cfg["context_scale"]
        before, after = cfg["lines_before"], cfg["lines_after"]
        lh = self.lh()
        base = int(clamp(math.floor(scroll), 0, n - 1))
        frac = scroll - base
        lo, hi = max(0, base - before - 2), min(n - 1, base + after + 3)

        scale, height, ys = {}, {}, {}
        for i in range(lo, hi + 1):
            s = lerp(cs, 1.0, emph_fn(i))
            scale[i] = s
            text = self.lines[i].text
            height[i] = (self.sprite(text).box_h if text else lh) * s
        ys[base] = 0.0
        for i in range(base + 1, hi + 1):
            ys[i] = ys[i - 1] + height[i - 1] / 2 + height[i] / 2
        for i in range(base - 1, lo - 1, -1):
            ys[i] = ys[i + 1] - height[i + 1] / 2 - height[i] / 2
        nxt = ys.get(base + 1, ys[base] + height[base])
        prv = ys.get(base - 1, ys[base] - height[base])
        cam = ys[base] + frac * ((nxt - ys[base]) if frac >= 0 else (ys[base] - prv))
        anchor_y = content.top() + self.lay[0] + self.lay[1] / 2

        if zoom != 1.0:
            p.save()
            p.translate(content.center().x(), anchor_y)
            p.scale(zoom, zoom)
            p.translate(-content.center().x(), -anchor_y)

        for i in range(lo, hi + 1):
            d = i - scroll
            fade = clamp(before + 1 + d) if d < 0 else clamp(after + 1 - d)
            cy = anchor_y + ys[i] - cam
            if i != self.active:
                # neighbours scrolling out of the panel fade softly; the sung line never does
                top, bottom = cy - height[i] / 2, cy + height[i] / 2
                soft = lh * cs
                if top < content.top() - 4:
                    fade *= clamp(1 - (content.top() - 4 - top) / soft)
                if bottom > content.bottom() + 4:
                    fade *= clamp(1 - (bottom - content.bottom() - 4) / soft)
            if fade <= 0.01:
                continue
            e = emph_fn(i)
            entrance = self.entrance if (is_current and i == self.active) else 1.0
            self._paint_line(p, i, content, cy, scale[i], e, fade * alpha, entrance)

        if zoom != 1.0:
            p.restore()

    def _paint_line(self, p, i, content, cy, s, e, alpha, entrance):
        cfg = self.cfg
        text = self.lines[i].text
        is_active = i == self.active
        if not text:
            self._paint_dots(p, content, cy, s, e, alpha, is_active)
            return
        ent = cfg["entrance"]
        if entrance < 1.0 and is_active:
            if ent == "pop":
                s *= lerp(0.86, 1.0, ease_out_back(entrance))
            elif ent == "rise":
                cy += (1 - ease_out_cubic(entrance)) * self.lh() * 0.55
                alpha *= ease_out_cubic(entrance)
        sp = self.sprite(text)
        a = cfg["align"]
        ax_local = 0.0 if a == "left" else self.text_width() if a == "right" else self.text_width() / 2
        ax = content.left() + ax_local
        x = ax - (ax_local + sp.m) * s
        y = cy - sp.h * s / 2
        target = QRectF(x, y, sp.w * s, sp.h * s)
        style = cfg["highlight_style"]
        if not self.synced and style == "karaoke":
            style = "color"
        ctx_a = lerp(cfg["context_opacity"], 1.0, e)

        reveal = None
        if is_active and ent == "typewriter" and entrance < 1.0:
            reveal = entrance
        if reveal is not None:
            p.save()
            p.setClipPath(self._progress_clip(sp, x, y, s, reveal), Qt.ClipOperation.IntersectClip)

        if style == "pill" and e > 0.01:
            self._paint_pill(p, sp, x, y, s, e, alpha)

        if style == "karaoke":
            p.setOpacity(alpha * ctx_a)
            p.drawImage(target, sp.base)
            if e > 0.01:
                prog = self.kp if is_active else (1.0 if i < self.active else 0.0)
                if prog > 0:
                    p.save()
                    p.setClipPath(self._progress_clip(sp, x, y, s, prog), Qt.ClipOperation.IntersectClip)
                    p.setOpacity(alpha * e)
                    p.drawImage(target, sp.active)
                    p.restore()
                    self._paint_soft_edge(p, sp, target, x, y, s, prog, alpha * e)
        else:
            p.setOpacity(alpha * ctx_a * (1 - e * e))
            p.drawImage(target, sp.base)
            if e > 0.01:
                p.setOpacity(alpha * e)
                p.drawImage(target, sp.active)
            if style == "underline" and e > 0.01:
                self._paint_underline(p, sp, x, y, s, e, alpha)

        if reveal is not None:
            p.restore()
            self._paint_caret(p, sp, x, y, s, reveal, alpha)
        p.setOpacity(1.0)

    def _fill_geometry(self, sp, prog):
        """Yield (sub_rect, filled_width) for a 0..1 progress across wrapped sub-lines."""
        total = sum(r.width() for r in sp.subs) or 1.0
        left = prog * total
        for r in sp.subs:
            wfill = clamp(left, 0, r.width())
            left -= r.width()
            yield r, wfill

    def _progress_clip(self, sp, x, y, s, prog):
        path = QPainterPath()
        n = len(sp.subs)
        for j, (r, wfill) in enumerate(self._fill_geometry(sp, prog)):
            if wfill <= 0:
                break
            top = 0 if j == 0 else r.top()
            bottom = sp.h if j == n - 1 else r.bottom()
            right = sp.w if wfill >= r.width() - 0.01 else r.left() + wfill
            path.addRect(QRectF(x, y + top * s, right * s, (bottom - top) * s))
        return path

    def _paint_soft_edge(self, p, sp, target, x, y, s, prog, alpha):
        if prog >= 0.999:
            return
        n = len(sp.subs)
        for j, (r, wfill) in enumerate(self._fill_geometry(sp, prog)):
            if 0 < wfill < r.width():
                top = 0 if j == 0 else r.top()
                bottom = sp.h if j == n - 1 else r.bottom()
                edge = r.left() + wfill
                for k, a in enumerate((0.7, 0.45, 0.25, 0.1)):
                    p.save()
                    p.setClipRect(QRectF(x + (edge + k * 4) * s, y + top * s, 4 * s, (bottom - top) * s),
                                  Qt.ClipOperation.IntersectClip)
                    p.setOpacity(alpha * a)
                    p.drawImage(target, sp.active)
                    p.restore()
                break

    def _paint_caret(self, p, sp, x, y, s, prog, alpha):
        for r, wfill in self._fill_geometry(sp, prog):
            if 0 < wfill < r.width():
                cx = x + (r.left() + wfill + 2) * s
                bar = QRectF(cx, y + (r.top() + r.height() * 0.2) * s, 3 * s, r.height() * 0.6 * s)
                p.setOpacity(alpha)
                p.fillRect(bar, qcolor(self.cfg["highlight_color"]))
                return

    def _paint_pill(self, p, sp, x, y, s, e, alpha):
        hl = self.cfg["highlight_color"]
        p.setPen(Qt.PenStyle.NoPen)
        for r in sp.subs:
            pr = QRectF(x + (r.left() - 16) * s, y + (r.top() + r.height() * 0.08) * s,
                        (r.width() + 32) * s, r.height() * 0.84 * s)
            rad = pr.height() / 2
            p.setOpacity(alpha * e * 0.35)
            p.setBrush(qcolor(hl, 0.5))
            p.drawRoundedRect(pr.adjusted(-4, -4, 4, 4), rad + 4, rad + 4)
            p.setOpacity(alpha * e)
            p.setBrush(qcolor(hl, 0.92))
            p.drawRoundedRect(pr, rad, rad)
        p.setOpacity(1.0)

    def _paint_underline(self, p, sp, x, y, s, e, alpha):
        hl = self.cfg["highlight_color"]
        p.setPen(Qt.PenStyle.NoPen)
        for r, wfill in self._fill_geometry(sp, self.kp if self.synced else 1.0):
            if wfill <= 0:
                break
            bar = QRectF(x + r.left() * s, y + (r.bottom() - r.height() * 0.08) * s, wfill * s, max(2.0, 3 * s))
            p.setOpacity(alpha * e * 0.3)
            p.setBrush(qcolor(hl))
            p.drawRoundedRect(bar.adjusted(-2, -3, 2, 3), 4, 4)
            p.setOpacity(alpha * e)
            p.drawRoundedRect(bar, 1.5, 1.5)
        p.setOpacity(1.0)

    def _paint_dots(self, p, content, cy, s, e, alpha, active):
        cfg = self.cfg
        fs = cfg["font_size"] * s
        r = max(3.0, fs * 0.13)
        gap = fs * 0.62
        a = cfg["align"]
        if a == "left":
            cx0 = content.left() + r
        elif a == "right":
            cx0 = content.right() - r - gap * 2
        else:
            cx0 = content.center().x() - gap
        hl, txt = QColor(cfg["highlight_color"]), QColor(cfg["text_color"])
        p.setPen(Qt.PenStyle.NoPen)
        for k in range(3):
            lit = clamp(self.kp * 3 - k) if active else 0.0
            pulse = 1 + 0.18 * math.sin(self.clock * 4.2 - k * 0.9) * e
            rr = r * pulse
            c = QPointF(cx0 + k * gap, cy)
            if lit > 0:
                g = QRadialGradient(c, rr * 3.2)
                g.setColorAt(0, qcolor(hl, 0.55 * lit))
                g.setColorAt(1, qcolor(hl, 0.0))
                p.setOpacity(alpha)
                p.setBrush(QBrush(g))
                p.drawEllipse(c, rr * 3.2, rr * 3.2)
            col = QColor.fromRgbF(lerp(txt.redF(), hl.redF(), lit), lerp(txt.greenF(), hl.greenF(), lit),
                                  lerp(txt.blueF(), hl.blueF(), lit))
            p.setOpacity(alpha * lerp(cfg["context_opacity"], 1.0, max(lit, e * 0.6)))
            p.setBrush(col)
            p.drawEllipse(c, rr, rr)
        p.setOpacity(1.0)

    def _paint_status(self, p, content, alpha):
        cfg = self.cfg
        if not self.status:
            return
        f = QFont(self.font)
        f.setPixelSize(max(12, int(cfg["font_size"] * 0.62)))
        fm = QFontMetricsF(f)
        text = self.status
        if self.busy:
            stem = text.rstrip("…. ")
            w = fm.horizontalAdvance(stem + "...")
            text = stem + "." * (1 + int(self.clock * 3) % 3)
        else:
            text = fm.elidedText(text, Qt.TextElideMode.ElideRight, content.width())
            w = fm.horizontalAdvance(text)
        a = cfg["align"]
        x = content.left() if a == "left" else content.right() - w if a == "right" else content.center().x() - w / 2
        cy = content.top() + self.lay[0] + self.lay[1] / 2
        path = QPainterPath()
        path.addText(x, cy + (fm.ascent() - fm.descent()) / 2, f, text)
        p.setOpacity(alpha * (0.75 + 0.25 * math.sin(self.clock * 3)) if self.busy else alpha * 0.9)
        if cfg["outline_enabled"]:
            p.strokePath(path, QPen(qcolor(cfg["outline_color"], 0.8), cfg["outline_width"] * 2,
                                    Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.fillPath(path, qcolor(cfg["text_color"], 0.85))
        p.setOpacity(1.0)

    def _art_pixmap(self, size):
        d = self.dpr
        key = (round(size), round(d, 2), id(self.art))
        if self._art_px is None or self._art_px[0] != key:
            px = QPixmap(max(1, int(size * d)), max(1, int(size * d)))
            px.fill(Qt.GlobalColor.transparent)
            ip = QPainter(px)
            ip.setRenderHints(HINTS)
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(0, 0, px.width(), px.height()), px.width() * 0.12, px.width() * 0.12)
            ip.setClipPath(clip)
            src = self.art
            side = min(src.width(), src.height())
            ip.drawImage(QRectF(0, 0, px.width(), px.height()), src,
                         QRectF((src.width() - side) / 2, (src.height() - side) / 2, side, side))
            ip.end()
            px.setDevicePixelRatio(d)
            self._art_px = (key, px)
        return self._art_px[1]

    def _paint_card(self, p, content, alpha):
        """Cover on the left; title, "By artist" and the note beside it; a sound-wave timeline below."""
        cfg = self.cfg
        A, wave_h, gap = self._card_metrics()
        text_c, hl = QColor(cfg["text_color"]), cfg["highlight_color"]
        art = QRectF(content.left(), content.top(), A, A)
        if self.art is not None:
            p.drawPixmap(art.topLeft(), self._art_pixmap(A))
        else:
            p.setPen(QPen(qcolor(cfg["text_color"], 0.55), max(1.5, A * 0.03)))
            p.setBrush(qcolor(cfg["text_color"], 0.06))
            p.drawRoundedRect(art.adjusted(1, 1, -1, -1), A * 0.12, A * 0.12)
            nf = QFont(self.font)
            nf.setPixelSize(int(A * 0.36))
            p.setFont(nf)
            p.setPen(qcolor(hl, 0.8))
            p.drawText(art, Qt.AlignmentFlag.AlignCenter, "♪")
        p.setPen(QPen(qcolor(cfg["text_color"], 0.18), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(art, A * 0.12, A * 0.12)

        # text beside the cover
        x = art.right() + gap * 1.3
        w = content.right() - x
        fs = cfg["font_size"]
        tf = QFont(self.font)
        tf.setPixelSize(int(clamp(fs * 0.62, 14, 34)))
        tf.setWeight(QFont.Weight.Bold)
        af = QFont(self.font)
        af.setPixelSize(int(clamp(fs * 0.44, 11, 24)))
        af.setWeight(QFont.Weight.DemiBold)
        nf = QFont(self.font)
        nf.setPixelSize(int(clamp(fs * 0.32, 10, 17)))
        nf.setWeight(QFont.Weight.Normal)
        nf.setItalic(False)
        rows = [(tf, self.title or "Unknown song", text_c, 1.0),
                (af, f"By {self.artist}" if self.artist else "", text_c, 0.78),
                (nf, self.card, QColor(hl), 0.95)]
        heights = [QFontMetricsF(f).height() for f, t, _c, _a in rows if t]
        total = sum(heights) + 3 * (len(heights) - 1)
        y = art.center().y() - total / 2
        for f, text, col, a in rows:
            if not text:
                continue
            fm = QFontMetricsF(f)
            shown = fm.elidedText(text, Qt.TextElideMode.ElideRight, max(10.0, w))
            path = QPainterPath()
            path.addText(x, y + fm.ascent(), f, shown)
            if cfg["shadow_enabled"]:
                p.fillPath(path.translated(1, 1.5), qcolor(cfg["shadow_color"], 0.5 * cfg["shadow_opacity"]))
            if cfg["outline_enabled"]:
                p.strokePath(path, QPen(qcolor(cfg["outline_color"], 0.8), cfg["outline_width"] * 1.5,
                                        Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            c = QColor(col)
            c.setAlphaF(a)
            p.fillPath(path, c)
            y += fm.height() + 3

        # live visualizer, sitting on the song's progress track (playlist button on its left)
        top = art.bottom() + gap
        track_y = top + wave_h + 6
        left = content.left() + self._paint_playlist_button(p, content.left(), track_y)
        right = content.right()
        n = WAVE_BARS
        step = (right - left) / n
        bw = max(2.0, step * 0.55)
        for i, v in enumerate(self.bars):
            h = 2.0 + v * (wave_h - 2.0)
            x = left + i * step + (step - bw) / 2
            bar = QRectF(x, track_y - 4 - h, bw, h)
            g = QLinearGradient(bar.bottomLeft(), bar.topLeft())
            g.setColorAt(0, qcolor(hl, 0.95))
            g.setColorAt(1, qcolor(hl, 0.35 + 0.5 * v))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawRoundedRect(bar, bw / 2, bw / 2)
        # progress track: played part, a knob where the song is, a dot at the end
        frac = clamp(self.position / self.duration) if self.duration > 0 else 0.0
        end = right - 5
        px = left + (end - left) * frac
        p.setPen(QPen(qcolor(cfg["text_color"], 0.22), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(px, track_y), QPointF(end, track_y))
        p.setPen(QPen(qcolor(hl, 0.95), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(left, track_y), QPointF(px, track_y))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(cfg["text_color"], 0.9))
        p.drawEllipse(QPointF(end, track_y), 4.5, 4.5)
        p.setBrush(qcolor(hl, 0.3))
        p.drawEllipse(QPointF(px, track_y), 8, 8)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QPointF(px, track_y), 5, 5)

    def _paint_toast(self, p, bg, alpha):
        t = self.toast_t
        if t > 2.2 or not self.toast_text:
            return
        a = ease_out_cubic(clamp(t / 0.2)) * (1 - clamp((t - 1.8) / 0.4))
        f = QFont("Segoe UI")
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.Bold)
        fm = QFontMetricsF(f)
        w = fm.horizontalAdvance(self.toast_text) + 28
        hl = self.cfg["highlight_color"]
        r = QRectF(bg.center().x() - w / 2, bg.top() + 6 - (1 - a) * 10, w, 24)
        p.setOpacity(alpha * a)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(hl, 0.25))
        p.drawRoundedRect(r.adjusted(-4, -4, 4, 4), 16, 16)
        p.setBrush(qcolor(hl, 0.95))
        p.drawRoundedRect(r, 12, 12)
        p.setFont(f)
        p.setPen(contrast_color(hl))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.toast_text)
        p.setOpacity(1.0)
