"""Wires everything together: media watcher → lyrics → overlay, plus tray and hotkeys."""

import os
import sys

from PySide6.QtCore import QObject, QRect, QTimer
from PySide6.QtGui import QAction, QImage
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .branding import app_icon
from .config import APP_ID, APP_NAME, STYLE_PRESETS, VERSION, Settings
from .configs import ConfigStore
from .updater import Updater
from .hotkeys import HotkeyManager
from .lyrics import LyricsFetcher
from .media import MediaWatcher, PlaybackClock
from .controls import VolumeController
from .discord_rpc import DiscordPresence
from .library import LyricsLibrary
from .playlists import PlaylistStore
from .playlists_ui import SongDropdown
from .lyrics import LyricLine
from .fullscreen import FullscreenWindow
from .overlay import OverlayWindow
from .settings_window import MainWindow
from .widgets import theme

INSTANCE_KEY = f"{APP_ID}-single-instance"


def send_to_running_instance(message: bytes = b"show") -> bool:
    """Send a message to an already running instance ("show", "ping" or "import:<path>").
    Only a live instance that replies counts, so a crashed or hung process can never block startup."""
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_KEY)
    if not sock.waitForConnected(300):
        return False
    sock.write(message)
    sock.waitForBytesWritten(300)
    alive = sock.waitForReadyRead(800) and sock.readAll().data() == b"ok"
    sock.abort()
    return alive


def wait_for_previous_instance(timeout=12.0):
    """After an update the old version is still shutting down; wait for it before starting."""
    import time
    end = time.time() + timeout
    while time.time() < end and send_to_running_instance(b"ping"):
        time.sleep(0.3)


class MNKLyricsApp(QObject):
    def __init__(self, qapp: QApplication):
        super().__init__()
        self.qapp = qapp
        self.settings = Settings()
        theme.set_theme(self.settings.get("general.ui_theme"))
        qapp.setStyleSheet(theme.stylesheet())
        self.icon = app_icon()
        qapp.setWindowIcon(self.icon)

        self.track = None
        self.req = 0
        self.version, self.versions = 0, 0
        self.switching = False
        self.playing = False
        self._preset_i = -1

        self.clock = PlaybackClock()
        self.volume = VolumeController()
        self.discord = DiscordPresence(self.settings)
        self.overlay = OverlayWindow(self.settings, self.clock)
        self.overlay.open_settings.connect(self.show_window)
        self.overlay.volume = self.volume
        self.overlay.control.connect(self._media_command)
        self.fullscreen = FullscreenWindow(self.settings, self.clock)
        self.fullscreen.volume = self.volume
        self.fullscreen.control.connect(self._media_command)
        self.fullscreen.closed.connect(self._fullscreen_closed)
        self.volume.changed.connect(self._on_volume)
        self._renderers = (self.overlay.renderer, self.fullscreen.renderer)
        self._status("Waiting for Spotify…")

        self.configs = ConfigStore(self.settings)
        self.updater = Updater()
        self.updater.changed.connect(self._on_update_state)
        self._announced = None

        self.library = LyricsLibrary()
        self._previewing = False
        self.playlists = PlaylistStore(self.settings)
        self.picker = SongDropdown(self.playlists)
        self.picker.closed.connect(self._picker_closed)
        self.playlists.changed.connect(self._playlists_changed)
        self._playlists_changed()
        self.window = MainWindow(self.settings, self.configs, self.updater, self.library, self.playlists)
        self.window.clock = self.clock
        self.window.lyrics_saved.connect(self._on_library_saved)
        self.window.preview_lyrics.connect(self._on_preview)
        self.window.demo_toggled.connect(self.overlay.set_demo)
        self.window.hidden.connect(lambda: self.window.demo_switch.setChecked(False))
        self.window.update_check.connect(lambda: self.updater.check())
        self.window.update_download.connect(self.updater.download)
        self.window.update_install.connect(self.install_update)
        self.window.media_command.connect(self._media_command)
        self.window.reload_lyrics.connect(self.reload_lyrics)
        self.window.next_version.connect(self.next_version)
        self.window.hotkey_capture.connect(self._hotkey_capture)
        self.window.overlay_toast.connect(self.overlay.toast)
        self.window.quit_requested.connect(self.quit)
        self.window.open_fullscreen.connect(self.toggle_fullscreen)

        self.fetcher = LyricsFetcher()
        self.fetcher.finished.connect(self._on_lyrics)

        self.media = MediaWatcher(self.settings)
        self.media.track_changed.connect(self._on_track)
        self.media.playback_updated.connect(self._on_playback)
        self.media.artwork_changed.connect(self.window.now_playing.set_art)
        self.media.artwork_changed.connect(self.fullscreen.set_art)
        self.media.artwork_changed.connect(self.discord.set_artwork)
        self.media.artwork_changed.connect(self._on_artwork)
        self.wave_timer = QTimer(self)
        self.wave_timer.setInterval(33)
        self.wave_timer.timeout.connect(self._feed_wave)
        self.wave_timer.start()
        self.media.status_changed.connect(self._on_media_status)
        self.media.start()

        self.hotkeys = HotkeyManager(qapp)
        self.hotkeys.triggered.connect(self._on_hotkey)
        self._register_hotkeys()

        self.settings.changed.connect(self._on_setting)
        self._setup_tray()
        self._setup_single_instance()

        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(100)
        self.progress_timer.timeout.connect(self._push_progress)
        self.progress_timer.start()

        # updates: quietly check shortly after start and every 6 hours (only if enabled)
        self.update_timer = QTimer(self)
        self.update_timer.setInterval(6 * 3600 * 1000)
        self.update_timer.timeout.connect(self._auto_check)
        self.update_timer.start()
        QTimer.singleShot(8000, self._auto_check)

        if "--updated" in sys.argv:
            QTimer.singleShot(1500, lambda: self.tray.showMessage(
                APP_NAME, f"Updated to version {VERSION}. Enjoy the new features!", self.icon, 6000))
        if self.settings.get("general.show_window_on_start") or "--updated" in sys.argv:
            QTimer.singleShot(150, self.show_window)
        for arg in sys.argv[1:]:
            if arg.lower().endswith(".mnkcfg"):
                QTimer.singleShot(600, lambda a=arg: self.import_file(a))

    # -------------------------------------------------------------- updates
    def _auto_check(self):
        if self.settings.get("general.auto_update"):
            self.updater.check(silent=True)

    def _on_update_state(self):
        u = self.updater
        if u.state == "available" and u.latest and self._announced != u.latest["version"]:
            self._announced = u.latest["version"]
            self.tray.showMessage(APP_NAME, f"Version {u.latest['version']} is available. Open MNKLyrics "
                                            "› Updates to install it.", self.icon, 8000)

    def install_update(self):
        if self.updater.install_and_restart():
            self.quit()

    # ------------------------------------------------------------ plumbing
    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        self.act_show = QAction("Open control center", menu)
        act_full = QAction("Full-screen lyrics", menu)
        act_full.triggered.connect(self.toggle_fullscreen)
        self.act_overlay = QAction("Show overlay", menu, checkable=True)
        self.act_lock = QAction("Lock overlay (click-through)", menu, checkable=True)
        act_quit = QAction("Quit", menu)
        self.act_show.triggered.connect(self.show_window)
        self.act_overlay.triggered.connect(lambda c: self.settings.set("overlay.visible", c))
        self.act_lock.triggered.connect(lambda c: self.settings.set("overlay.click_through", c))
        act_quit.triggered.connect(self.quit)
        self.configs_menu = QMenu("Configs", menu)
        self.configs_menu.aboutToShow.connect(self._fill_configs_menu)
        for a in (self.act_show, act_full, self.configs_menu, None, self.act_overlay, self.act_lock, None, act_quit):
            if a is None:
                menu.addSeparator()
            elif isinstance(a, QMenu):
                menu.addMenu(a)
            else:
                menu.addAction(a)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self._sync_tray()
        self.tray.show()

    def _fill_configs_menu(self):
        """Switch looks straight from the tray, even mid-game."""
        m = self.configs_menu
        m.clear()
        active = self.settings.get("general.active_config")
        groups = [("My configs", list(self.configs.items)), ("Built-in looks", list(STYLE_PRESETS))]
        for title, names in groups:
            if not names:
                continue
            m.addSection(title)
            for name in names:
                act = m.addAction(name)
                act.setCheckable(True)
                act.setChecked(name == active)
                act.triggered.connect(lambda _=False, n=name: self.window.apply_preset(n))
        m.addSeparator()
        m.addAction("Manage configs…").triggered.connect(lambda: (self.show_window(),
                                                                  self.window.go_to("Configs")))

    def _sync_tray(self):
        self.act_overlay.setChecked(bool(self.settings.get("overlay.visible")))
        self.act_lock.setChecked(bool(self.settings.get("overlay.click_through")))

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def _setup_single_instance(self):
        QLocalServer.removeServer(INSTANCE_KEY)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_instance_ping)
        self.server.listen(INSTANCE_KEY)

    def _on_instance_ping(self):
        conn = self.server.nextPendingConnection()
        if conn:
            conn.write(b"ok")
            conn.flush()
            conn.readyRead.connect(lambda c=conn: self._on_instance_message(bytes(c.readAll().data())))
            conn.disconnected.connect(conn.deleteLater)

    def _on_instance_message(self, data: bytes):
        if data.startswith(b"import:"):
            self.import_file(data[7:].decode("utf-8", "replace"))
        elif data.startswith(b"show"):
            self.show_window()

    def import_file(self, path):
        if path and os.path.isfile(path):
            self.window._import_config_file(path)

    def show_window(self):
        self.window.show_animated()

    def toggle_fullscreen(self):
        if self.fullscreen.isVisible():
            self.fullscreen.close_view()
            return
        # the menu steps aside while full screen is open and comes back when it closes
        self._menu_was_open = self.window.isVisible() and not self.window.isMinimized()
        if self._menu_was_open:
            self.window.hide_animated()
        self.overlay.suppressed = True
        self.fullscreen.playing = self.playing
        self.fullscreen.duration = self.track.duration_ms if self.track else 0.0
        self.volume.refresh()
        self.fullscreen.open()

    def _fullscreen_closed(self):
        self.overlay.suppressed = False
        self.overlay.dirty = True
        if getattr(self, "_menu_was_open", False):
            self._menu_was_open = False
            self.show_window()

    def _on_volume(self, _level):
        self.overlay.dirty = True

    def _status(self, text, busy=False, card=""):
        for r in self._renderers:
            r.set_status(text, busy=busy, card=card)

    def _on_artwork(self, data: bytes):
        img = QImage.fromData(data) if data else None
        self.overlay.renderer.set_art(img)
        self.overlay.dirty = True

    def _feed_wave(self):
        """While the song card is up, feed it Spotify's loudness so its sound wave moves with the music."""
        r = self.overlay.renderer
        if self.playing and r.card_on():
            r.push_level(self.volume.peak())

    def quit(self):
        self.fullscreen.timer.stop()
        self.discord.shutdown()
        self.media.stop()
        self.hotkeys.unregister_all()
        self.settings.save()
        self.tray.hide()
        self.window.quitting = True
        self.qapp.exit(0)

    # ------------------------------------------------------------ settings
    def _on_setting(self, key):
        if key == "*" or key.startswith("hotkeys."):
            self._register_hotkeys()
        if key in ("*", "overlay.visible", "overlay.click_through"):
            self._sync_tray()
        if key == "overlay.click_through":
            locked = self.settings.get("overlay.click_through")
            self.overlay.toast("Locked. Clicks pass through" if locked else "Unlocked. Drag me anywhere")
        if key == "general.start_with_windows":
            self._apply_autostart(self.settings.get(key))

    def _register_hotkeys(self):
        self.hotkeys.register_all(self.settings.data["hotkeys"])
        self.window.set_hotkey_failures(self.hotkeys.failed)

    def _hotkey_capture(self, active):
        if active:
            self.hotkeys.unregister_all()
        else:
            self._register_hotkeys()

    def _apply_autostart(self, enabled):
        if sys.platform != "win32":
            return
        import winreg
        run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE) as k:
                try:
                    winreg.DeleteValue(k, "LuminaLyrics")  # entry from the app's previous name
                except FileNotFoundError:
                    pass
                if enabled:
                    if getattr(sys, "frozen", False):  # running as MNKLyrics.exe
                        command = f'"{sys.executable}"'
                    else:
                        exe = sys.executable
                        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
                        if os.path.exists(pyw):
                            exe = pyw
                        main = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
                        command = f'"{exe}" "{main}"'
                    winreg.SetValueEx(k, APP_ID, 0, winreg.REG_SZ, command)
                else:
                    try:
                        winreg.DeleteValue(k, APP_ID)
                    except FileNotFoundError:
                        pass
        except OSError:
            pass

    # --------------------------------------------------------------- media
    def _media_command(self, name):
        if name == "playlist":  # the playlist button next to the song's progress track: open/close the list
            r = self.overlay.active_renderer
            btn = r.playlist_rect
            rect = QRect(self.overlay.mapToGlobal(btn.topLeft().toPoint()), btn.size().toSize()) \
                if not btn.isNull() else self.overlay.frameGeometry()
            self.picker.toggle_near(rect, self.track)
            r.playlist_open = self.picker.isVisible()
            self.overlay.dirty = True
            return
        self.media.command(name)

    def _picker_closed(self):
        self.overlay.active_renderer.playlist_open = False
        self.overlay.dirty = True

    def _playlists_changed(self):
        has = bool(self.playlists.items)
        if has != self.overlay.has_playlists:
            self.overlay.has_playlists = has
            self.overlay.apply_config()
        if not has:
            self.picker.hide()

    def _on_media_status(self, text):
        if self.track is None:
            self._status(text)
            self.overlay.dirty = True
            self.window.set_lyrics_status(text)

    def _on_track(self, track):
        self.track = track
        self.clock.reset()
        self.overlay.dirty = True
        self.switching = False
        self.window.set_versions(0, 0)
        self.window.set_track(track)
        self.discord.update(track, self.playing, 0.0)
        self.playlists.learn(track)   # saved songs pick up their artist and length as they play
        self.fullscreen.duration = track.duration_ms if track else 0.0
        if track is None:
            for r in self._renderers:
                r.set_track("", "", 0)
            self._status("Waiting for Spotify…")
            self.window.set_lyrics_status("Waiting for Spotify…")
            return
        self.volume.set_target(getattr(track, "app_id", ""))
        for r in self._renderers:
            r.set_track(track.title, track.artist, track.duration_ms)
        self._previewing = False
        if self._show_own():
            return
        self._status("Searching lyrics…", busy=True)
        self.window.set_lyrics_status(f"Searching lyrics for “{track.title}”…")
        self.req = self.fetcher.request(track)

    def _show_own(self) -> bool:
        """Lyrics you wrote or picked in the Lyrics Library always win over the online search."""
        own = self.library.find(self.track)
        if own is None or not own.get("lines"):
            return False
        self.req = -1  # ignore a search that's still running
        self._show_lyrics(self.library.to_lyrics(own))
        return True

    def reload_lyrics(self):
        if self.track is not None:
            if self._show_own():
                return
            self._status("Searching lyrics…", busy=True)
            self.window.set_lyrics_status("Reloading lyrics…")
            self.req = self.fetcher.request(self.track, force=True)

    def _on_library_saved(self, _entry_id):
        if self.track is not None and not self._previewing:
            if not self._show_own():
                self.reload_lyrics()

    def _on_preview(self, lines):
        """The lyrics editor's "Preview on my overlay": show the draft on the real overlay."""
        if lines:
            self._previewing = True
            self.overlay.renderer.set_lyrics([LyricLine(float(t), txt) for t, txt in lines], True,
                                             self.track.duration_ms if self.track else 0.0)
            self.overlay.dirty = True
        elif self._previewing:
            self._previewing = False
            self.reload_lyrics()

    def next_version(self):
        """Switch to the next LRCLIB upload of this song (remembered for next time)."""
        if self.track is None or self.versions < 2:
            self.overlay.toast("No other version of these lyrics")
            return
        self.switching = True
        self.req = self.fetcher.request(self.track, version=self.version + 1)

    def _on_lyrics(self, req, lyrics):
        if req != self.req or self.track is None:
            return
        if lyrics is None:
            self.library.note_missing(self.track)  # listed in the Lyrics Library so it can be written
        self._show_lyrics(lyrics)

    def _show_lyrics(self, lyrics):
        self.overlay.dirty = True
        self.version, self.versions = (lyrics.version, lyrics.versions) if lyrics else (0, 0)
        self.window.set_versions(self.version, self.versions)
        if lyrics is None:
            self._status("No lyrics found for this song", card="No lyrics found")
            self.window.set_lyrics_status("No lyrics found for this song. You can write them in the Lyrics "
                                          "Library.")
        elif lyrics.instrumental or not lyrics.lines:
            self._status("♪  Instrumental  ♪", card="Instrumental")
            self.window.set_lyrics_status("Instrumental track.")
        else:
            for r in self._renderers:
                r.set_lyrics(lyrics.lines, lyrics.synced, self.track.duration_ms)
            kind = "time-synced" if lyrics.synced else "unsynced (auto-scrolling)"
            ver = f" • version {lyrics.version + 1} of {lyrics.versions}" if lyrics.versions > 1 else ""
            self.window.set_lyrics_status(f"{len(lyrics.lines)} lines • {kind} • {lyrics.source}{ver}")
            if self.switching:
                self.overlay.toast(f"Lyrics version {lyrics.version + 1} of {lyrics.versions}")
        self.switching = False

    def _on_playback(self, pos, playing, stamp):
        before = self.clock.position()
        self.clock.sync(pos, playing, stamp)
        if playing != self.playing or abs(self.clock.position() - before) > 1000:
            self.overlay.dirty = True  # play/pause or seek
        self.playing = playing
        self.overlay.playing = playing
        self.fullscreen.playing = playing
        self.discord.update(self.track, playing, self.clock.position())

    def _push_progress(self):
        dur = self.track.duration_ms if self.track else 0.0
        pos = min(self.clock.position(), dur) if dur else 0.0
        self.window.now_playing.set_progress(pos, dur, self.playing)

    # -------------------------------------------------------------- hotkeys
    def _on_hotkey(self, name):
        s = self.settings
        if name == "toggle_overlay":
            s.set("overlay.visible", not s.get("overlay.visible"))
        elif name == "toggle_lock":
            s.set("overlay.click_through", not s.get("overlay.click_through"))
        elif name == "open_settings":
            if self.window.isVisible() and self.window.isActiveWindow():
                self.window.hide_animated()
            else:
                self.show_window()
        elif name in ("font_bigger", "font_smaller"):
            size = s.get("overlay.font_size") + (2 if name == "font_bigger" else -2)
            size = max(12, min(120, size))
            s.set("overlay.font_size", size)
            self.overlay.toast(f"Font size {size}px")
        elif name in ("offset_plus", "offset_minus"):
            off = s.get("general.sync_offset_ms") + (100 if name == "offset_plus" else -100)
            s.set("general.sync_offset_ms", off)
            self.overlay.toast(f"Sync offset {off:+d} ms")
        elif name == "next_preset":
            names = list(s.all_presets())
            self._preset_i = (self._preset_i + 1) % len(names)
            self.window.apply_preset(names[self._preset_i])
        elif name == "play_pause":
            self.media.command("toggle")
        elif name == "next_version":
            self.next_version()
        elif name == "toggle_fullscreen":
            self.toggle_fullscreen()
        elif name == "next_track":
            self.media.command("next")
        elif name == "previous_track":
            self.media.command("previous")
        elif name in ("volume_up", "volume_down"):
            self.volume.nudge(0.05 if name == "volume_up" else -0.05)
            if self.volume.level >= 0:
                self.overlay.toast(f"Spotify volume {round(self.volume.level * 100)}%")
            else:
                self.overlay.toast("Spotify isn't playing sound right now")
