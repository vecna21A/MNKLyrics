"""Always-on-top, click-through desktop overlay that shows the lyrics."""

import ctypes
import sys
import time

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .controls import (ControlsRenderer, controls_length, controls_origin, overlay_controls_cfg, overlay_extent,
                       split_overlay_rect)
from .renderer import LyricsRenderer, clamp, qcolor

IS_WIN = sys.platform == "win32"
if IS_WIN:
    user32 = ctypes.windll.user32
    _GetLong = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    _SetLong = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    _GetLong.restype = ctypes.c_ssize_t
    _GetLong.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _SetLong.restype = ctypes.c_ssize_t
    _SetLong.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SetWindowDisplayAffinity.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user32.SetWindowDisplayAffinity.restype = ctypes.c_bool

WDA_NONE = 0x00                # visible to screen recorders, screenshots and streams
WDA_EXCLUDEFROMCAPTURE = 0x11  # Windows 10 2004+: on screen for you, invisible to every capture

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
WS_EX_TOOLWINDOW = 0x80
WS_EX_LAYERED = 0x80000
WS_EX_NOACTIVATE = 0x08000000


class OverlayWindow(QWidget):
    open_settings = Signal()
    control = Signal(str)  # "toggle" / "previous" / "next" / "playlist" from the player controls

    def __init__(self, settings, clock):
        super().__init__(None)
        self.settings = settings
        self.clock = clock
        self.renderer = LyricsRenderer()
        self.controls = ControlsRenderer()
        self.volume = None            # VolumeController, set by the app
        self.controls_alpha = 0.0     # animated (for "show on hover")
        self.playlist_alpha = 0.0     # the playlist button's own fade (it can be "show on hover" by itself)
        self.has_playlists = False    # set by the app: the playlist button only shows once one is saved
        self.suppressed = False
        self._interactive = False     # True while the cursor is over the controls of a locked overlay
        self._vol_drag = False
        self._unmute_level = 0.6
        self.demo = None          # demo renderer while "preview on screen" is on
        self._demo_t0 = 0.0
        self.playing = False
        self.has_lyrics = False
        self.alpha = 0.0          # animated visibility (auto-hide / show-hide)
        self.hover = 1.0          # animated hover fade
        self._hover_since = None  # when the cursor entered the panel (fade only after a short dwell)
        self._paused_since = None # when playback paused (hide-when-paused waits out short pauses)
        self._drag = None
        self._last = time.perf_counter()
        self._last_paint = 0.0
        self.dirty = True  # set whenever lyrics / status / track change outside the animation loop

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("MNKLyrics Overlay")

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self._tick)
        self.topmost_timer = QTimer(self)
        self.topmost_timer.setInterval(2000)
        self.topmost_timer.timeout.connect(self._assert_topmost)

        settings.changed.connect(self._on_setting)
        self.apply_config()
        self.timer.start()
        self.topmost_timer.start()

    # ------------------------------------------------------------- settings
    @property
    def cfg(self):
        return self.settings.overlay

    def _on_setting(self, key):
        if key == "*" or key.startswith("overlay."):
            self.apply_config()
        if key in ("*", "general.show_in_recordings"):
            self._apply_capture_visibility()

    @property
    def active_renderer(self):
        return self.demo if self.demo is not None else self.renderer

    def apply_config(self):
        cfg = self.cfg
        screen = self._screen()
        dpr = screen.devicePixelRatio() if screen else 1.0
        pos = cfg["position"]
        anchor = "bottom" if pos.startswith("bottom") or pos == "custom" else "top" if pos.startswith("top") \
            else "center"
        for r in (self.renderer, self.demo):
            if r is not None:
                r.set_config(cfg, dpr)
                r.v_anchor = anchor
        self.controls.configure(**overlay_controls_cfg(cfg))
        # the playlist button lives next to the song's progress track (not in the player controls)
        on = bool(cfg.get("controls_playlist", True)) and self.has_playlists
        for r in (self.renderer, self.demo):
            if r is not None:
                r.playlist_button = on
                r.playlist_alpha = self.playlist_alpha
        self.setMouseTracking(True)
        self._sync_geometry()
        self.setWindowOpacity(clamp(cfg["opacity"], 0.05, 1.0))
        if cfg["visible"] and not self.isVisible():
            self.show()
        self._apply_click_through()
        self.dirty = True

    def _sync_geometry(self):
        """Window is sized to the tallest layout the current song needs; the panel animates inside it."""
        if self._drag is not None:
            return  # the user is moving the overlay; don't snap it back to the saved spot mid-drag
        w, h = self.active_renderer.preferred_size()
        ew, eh = overlay_extent(self.cfg, self.controls)  # room for the player controls, if shown
        geo = self._target_geometry(int(w + ew), int(h + eh))
        if geo != self.geometry():
            self.setGeometry(geo)
            self.dirty = True

    def set_demo(self, on: bool):
        """Show looping sample lines on the real overlay so styles and position can be tuned live."""
        from .demo import DEMO_LINES, DEMO_TOTAL
        from .lyrics import LyricLine
        if on and self.demo is None:
            self.demo = LyricsRenderer()
            self.demo.set_lyrics([LyricLine(t, s) for t, s in DEMO_LINES], True)
            self.demo.set_track("Preview on screen", "MNKLyrics", DEMO_TOTAL)
            self._demo_t0 = time.perf_counter()
        elif not on:
            self.demo = None
        self.apply_config()

    def _screen(self):
        screens = QGuiApplication.screens()
        idx = int(self.cfg.get("screen", 0))
        return screens[idx] if 0 <= idx < len(screens) else QGuiApplication.primaryScreen()

    def _target_geometry(self, w, h) -> QRect:
        cfg = self.cfg
        area = self._screen().availableGeometry()
        pos = cfg["position"]
        if pos == "custom" and cfg["custom_x"] >= 0:
            # custom positions are stored as the overlay's bottom-centre so resizing grows upward
            x = int(cfg["custom_x"] - w / 2)
            y = int(cfg["custom_y"] - h)
            return QRect(x, y, w, h)
        m = int(cfg["margin"])
        vert, _, horiz = pos.partition("_") if pos != "center" else ("center", "", "center")
        if horiz == "left":
            x = area.left() + m
        elif horiz == "right":
            x = area.right() - w - m
        else:
            x = area.center().x() - w // 2
        if vert == "top":
            y = area.top() + m
        elif vert == "bottom":
            y = area.bottom() - h - m
        else:
            y = area.center().y() - h // 2
        return QRect(x, y, w, h)

    # ---------------------------------------------------------- win32 bits
    def _hwnd(self):
        return int(self.winId())

    def _apply_click_through(self):
        locked = bool(self.cfg["click_through"])
        if locked and self._drag is not None:
            self._finish_drag()  # locked mid-drag (e.g. Ctrl+Alt+K): keep where it was dropped
        if IS_WIN and self.isVisible():
            hwnd = self._hwnd()
            style = _GetLong(hwnd, GWL_EXSTYLE)
            style |= WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            # locked = clicks pass through, except while the cursor is over the player controls
            passthrough = locked and not self._interactive
            style = style | WS_EX_TRANSPARENT if passthrough else style & ~WS_EX_TRANSPARENT
            _SetLong(hwnd, GWL_EXSTYLE, style)
        self.setCursor(Qt.CursorShape.ArrowCursor if locked else Qt.CursorShape.SizeAllCursor)

    def _controls_on(self):
        return bool(self.cfg.get("controls_enabled")) and self.controls.size()[0] > 0

    def _set_interactive(self, on):
        if on != self._interactive:
            self._interactive = on
            self._apply_click_through()

    def _apply_capture_visibility(self):
        """Show the overlay in screen recordings (Medal desktop capture, OBS display capture, screenshots),
        or hide it from all of them while keeping it on your screen."""
        if not IS_WIN or not self.isVisible():
            return
        show = bool(self.settings.get("general.show_in_recordings", True))
        user32.SetWindowDisplayAffinity(self._hwnd(), WDA_NONE if show else WDA_EXCLUDEFROMCAPTURE)

    def _assert_topmost(self):
        if IS_WIN and self.isVisible():
            # games in borderless mode sometimes grab the top-most slot; take it back quietly
            user32.SetWindowPos(self._hwnd(), ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._apply_click_through)
        QTimer.singleShot(0, self._apply_capture_visibility)

    # ------------------------------------------------------------ animation
    def _target_alpha(self, now):
        cfg = self.cfg
        if self.demo is not None:
            return 1.0
        if not cfg["visible"] or self.suppressed:  # suppressed: the Full-screen Overlay is open
            return 0.0
        if self.playing:
            self._paused_since = None
        elif self._paused_since is None:
            self._paused_since = now
        # Spotify briefly reports "paused" while skipping/seeking; only hide after a real pause
        if (cfg["hide_when_paused"] and not self.playing and self.renderer.title
                and now - self._paused_since > 2.0):
            return 0.0
        if cfg["hide_no_lyrics"] and not self.renderer.lines:
            return 0.0
        return 1.0

    def _hover_target(self, now):
        """Fade only while the cursor actually rests on the visible panel (not the invisible margin)."""
        cfg = self.cfg
        if not (cfg["fade_on_hover"] and cfg["click_through"]) or self.alpha < 0.05:
            self._hover_since = None
            return 1.0
        panel = self.active_renderer.panel_rect
        top_left = self.mapToGlobal(panel.topLeft().toPoint())
        inside = QRect(top_left, panel.size().toSize()).contains(QCursor.pos())
        if not inside:
            self._hover_since = None
            return 1.0
        if self._hover_since is None:
            self._hover_since = now
        return clamp(cfg["hover_opacity"], 0.0, 1.0) if now - self._hover_since > 0.35 else 1.0

    def _tick(self):
        now = time.perf_counter()
        dt, self._last = now - self._last, now
        target = self._target_alpha(now)
        self.alpha += (target - self.alpha) * min(1.0, dt * 7)
        if target == 0.0 and self.alpha < 0.01:
            self.alpha = 0.0
            if self.isVisible():
                self.hide()
            return
        if target > 0 and not self.isVisible():
            self.show()

        hover_target = self._hover_target(now)
        self.hover += (hover_target - self.hover) * min(1.0, dt * 8)

        r = self.active_renderer
        if self.demo is not None:
            from .demo import DEMO_TOTAL
            r.update(dt, ((now - self._demo_t0) * 1000) % DEMO_TOTAL)
            playing = True
        else:
            r.update(dt, self.clock.position() + self.settings.get("general.sync_offset_ms", 0))
            playing = self.playing
        self._sync_geometry()

        # player controls: fade in/out ("show on hover"), hover highlight, and clickability
        ctrl_target = 0.0
        cursor = QCursor.pos()
        local = QPointF(self.mapFromGlobal(cursor))
        bar = self.controls.bar
        if self._controls_on():
            if self.cfg.get("controls_visibility", "always") == "always" or self._vol_drag:
                ctrl_target = 1.0
            else:
                zone = r.panel_rect.united(bar).adjusted(-40, -40, 40, 40) if not bar.isNull() \
                    else r.panel_rect.adjusted(-40, -40, 40, 40)
                ctrl_target = 1.0 if zone.contains(local) else 0.0
        self.controls_alpha += (ctrl_target - self.controls_alpha) * min(1.0, dt * 9)
        pl_target = 0.0
        if r.playlist_button:
            if self.cfg.get("controls_playlist_visibility", "always") == "always" or r.playlist_open:
                pl_target = 1.0
            else:
                pl_target = 1.0 if r.panel_rect.adjusted(-40, -40, 40, 40).contains(local) else 0.0
        if abs(pl_target - self.playlist_alpha) > 0.002:
            self.playlist_alpha += (pl_target - self.playlist_alpha) * min(1.0, dt * 9)
            r.playlist_alpha = self.playlist_alpha
            self.dirty = True
        over_pl = not r.playlist_rect.isNull() and r.playlist_rect.adjusted(-3, -3, 3, 3).contains(local)
        if over_pl != r.playlist_hover:
            r.playlist_hover = over_pl
            self.dirty = True
        over_bar = self._controls_on() and self.controls_alpha > 0.5 and bar.contains(local)
        self._set_interactive(over_bar or over_pl or self._vol_drag)
        hov = self.controls.hit(local) if over_bar else None
        if hov != self.controls.hover:
            self.controls.hover = hov
            self.dirty = True

        # Repaint only when something moves, so the overlay costs ~nothing while idle.
        fading = (abs(self.alpha - target) > 0.002 or abs(self.hover - hover_target) > 0.002
                  or abs(self.controls_alpha - ctrl_target) > 0.002)
        needs = (self.dirty or fading or not self.cfg["click_through"] or r.needs_frame(playing))
        if not needs and self.playing and self.cfg["show_progress"] and now - self._last_paint > 0.25:
            needs = True  # progress bar creeps forward a few times per second
        if needs:
            self.dirty = False
            self._last_paint = now
            self.update()
        # 60 fps for line changes, ~40 fps for the steady karaoke fill, 20 Hz polling when idle
        if r.in_transition() or fading or self._drag is not None:
            interval = 15
        elif needs:
            interval = 25
        else:
            interval = 50
        if self.timer.interval() != interval:
            self.timer.setInterval(interval)

    def toast(self, text):
        self.active_renderer.toast(text)
        self.dirty = True
        self.timer.setInterval(15)

    # ---------------------------------------------------------------- paint
    def paintEvent(self, event):
        p = QPainter(self)
        r = self.active_renderer
        lyrics_rect = split_overlay_rect(QRectF(self.rect()), self.cfg, self.controls)
        r.paint(p, lyrics_rect, self.alpha * self.hover)
        if self._controls_on() and self.controls_alpha > 0.01:
            self.controls.playing = True if self.demo is not None else self.playing
            self.controls.volume = self.volume.level if self.volume is not None else -1.0
            origin = controls_origin(r.panel_rect, self.cfg, self.controls)
            self.controls.paint(p, origin, self.alpha * self.controls_alpha, self.cfg["highlight_color"],
                                controls_length(r.panel_rect, self.cfg))
        else:
            self.controls.bar = QRectF()
        if not self.cfg["click_through"]:
            self._paint_edit_frame(p)
        p.end()

    def _paint_edit_frame(self, p):
        hl = self.cfg["highlight_color"]
        r = self.active_renderer.panel_rect.adjusted(-4, -4, 4, 4)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(1.0)
        pen = QPen(qcolor(hl, 0.9), 1.6, Qt.PenStyle.DashLine)
        pen.setDashOffset(-self.active_renderer.clock * 12)
        p.setPen(pen)
        p.setBrush(QColor(0, 0, 0, 30))
        p.drawRoundedRect(r, 14, 14)
        f = QFont("Segoe UI")
        f.setPixelSize(11)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(qcolor(hl))
        p.drawText(r.adjusted(0, 0, 0, -4), int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom),
                   "Drag to move  •  Scroll to resize  •  Double-click for settings  •  Ctrl+Alt+K to lock")

    # ----------------------------------------------------------- interaction
    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pl = self.active_renderer.playlist_rect
        if not pl.isNull() and pl.adjusted(-3, -3, 3, 3).contains(e.position()):
            self.control.emit("playlist")
            return
        hit = self.controls.hit(e.position()) if self._controls_on() and self.controls_alpha > 0.5 else None
        if hit:
            self._press_control(hit, e.position())
            return
        if not self.cfg["click_through"]:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _press_control(self, name, pos):
        if name == "play":
            self.control.emit("toggle")
        elif name == "playlist":
            self.control.emit("playlist")
        elif name in ("prev", "next"):
            self.control.emit("previous" if name == "prev" else "next")
        elif name == "mute" and self.volume is not None:
            if self.volume.level > 0:
                self._unmute_level = self.volume.level
                self.volume.set(0.0)
            else:
                self.volume.set(self._unmute_level or 0.6)
        elif name == "volume" and self.volume is not None:
            self._vol_drag = True
            v = self.controls.volume_at(pos)
            if v is not None:
                self.volume.set(v)
        self.dirty = True

    def mouseMoveEvent(self, e):
        if self._vol_drag and self.volume is not None:
            v = self.controls.volume_at(e.position())
            if v is not None:
                self.volume.set(v)
                self.dirty = True
        elif self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if self._vol_drag:
            self._vol_drag = False
            return
        self._finish_drag()

    def _finish_drag(self):
        """Save where the overlay was dropped — all at once, so it never jumps through in-between spots."""
        if self._drag is None:
            return
        self._drag = None
        g = self.geometry()
        values = {"custom_x": int(g.x() + g.width() / 2), "custom_y": int(g.y() + g.height()),
                  "position": "custom"}
        for i, s in enumerate(QGuiApplication.screens()):
            if s.geometry().contains(g.center()):
                values["screen"] = i
                break
        self.settings.set_many("overlay", values)

    def mouseDoubleClickEvent(self, e):
        if self._controls_on() and self.controls.bar.contains(e.position()):
            return  # fast clicks on the buttons aren't a request to open settings
        self.open_settings.emit()

    def wheelEvent(self, e):
        if self._controls_on() and self.controls.bar.contains(e.position()) and self.volume is not None:
            self.volume.nudge(0.05 if e.angleDelta().y() > 0 else -0.05)  # scroll on the controls = volume
            self.dirty = True
            return
        if self.cfg["click_through"]:
            return
        step = 2 if e.angleDelta().y() > 0 else -2
        size = int(clamp(self.cfg["font_size"] + step, 12, 120))
        self.settings.set("overlay.font_size", size)
        self.toast(f"Font size {size}px")
