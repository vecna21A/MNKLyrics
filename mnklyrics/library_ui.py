"""The Lyrics Library pages of the control center: "Write Lyrics" (type, tap-to-sync, fix, share) and
"Lyrics Library" (the community feed, your lyrics, songs that are missing lyrics)."""

import os
import time
from urllib.parse import quote

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QTimer, QUrl, QVariantAnimation, Signal
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QFontMetricsF, QKeySequence, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from . import community
from .library import fmt_ms, lines_from_text, parse_time, same_song, to_lrc, to_plain
from .renderer import blur_image
from .widgets import AA, Card, GlowButton, ToggleSwitch, alpha, draw_icon, mix, paint_glow, row, theme

TAP_LAG = 120  # people tap a little after a line starts; this much is taken off each tap (ms)


def fmt_len(seconds) -> str:
    s = int(round(seconds or 0))
    return f"{s // 60}:{s % 60:02d}" if s else ""


class SyncStage(QWidget):
    """Big "now tap this line" display for syncing: previous, current and next line plus the clock."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.prev, self.cur, self.nxt = "", "", ""
        self.time_text = "0:00.0"
        self.done = False
        self.flash = 0.0
        self.setMinimumHeight(170)

    def set_lines(self, prev, cur, nxt, done=False):
        self.prev, self.cur, self.nxt, self.done = prev, cur, nxt, done
        self.update()

    def pulse(self):
        self.flash = 1.0
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("bg3"))
        p.drawRoundedRect(r, 14, 14)
        if self.flash > 0.01:
            paint_glow(p, r, 14, theme.c("accent"), 0.6 * self.flash, 8)
            self.flash *= 0.82
            QTimer.singleShot(16, self.update)
        f = QFont("Segoe UI")
        f.setPixelSize(13)
        p.setFont(f)
        p.setPen(theme.c("dim"))
        p.drawText(r.adjusted(16, 10, -16, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, self.prev)
        p.drawText(r.adjusted(16, 0, -16, -10), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, self.nxt)
        p.setPen(theme.c("accent"))
        mono = QFont("Cascadia Mono")
        mono.setPixelSize(13)
        p.setFont(mono)
        p.drawText(r.adjusted(16, 10, -16, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, self.time_text)
        big = QFont("Segoe UI")
        big.setPixelSize(24)
        big.setWeight(QFont.Weight.Bold)
        p.setFont(big)
        text = self.cur
        if self.done:
            p.setPen(theme.c("accent"))
            text = "All lines synced. Check them below, then save or share"
        else:
            p.setPen(theme.c("text"))
        fm = QFontMetricsF(big)
        text = fm.elidedText(text, Qt.TextElideMode.ElideRight, r.width() - 40)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)


class LibraryPages:
    """Mixed into MainWindow. Needs: self.library, self.lib_client, self.clock (set by the app),
    self.current_track, and the window's page helpers."""

    # ================================================================ Write
    def _build_write(self):
        lay = self._page("Write Lyrics", "No lyrics for a song? Add them in 4 steps: type the words, tap along "
                                         "while the song plays, check the timings, then save or share them with the "
                                         "community.", "pencil", section="Lyrics library")
        self._write_page_index = self._building
        self.ed_id = None
        self.ed_lines = []      # [[ms or None, text]]
        self.ed_tap = 0

        song = Card("Song", "Lyrics are matched to songs by title, artist and length.")
        grid = QHBoxLayout()
        self.ed_title, self.ed_artist, self.ed_album, self.ed_len = (QLineEdit() for _ in range(4))
        for w, ph in ((self.ed_title, "Song title"), (self.ed_artist, "Artist"), (self.ed_album, "Album"),
                      (self.ed_len, "Length (3:41)")):
            w.setPlaceholderText(ph)
            grid.addWidget(w, 3 if w is not self.ed_len else 1)
        song.body.addLayout(grid)
        use = GlowButton("Use the song playing now", icon="music", variant="ghost")
        use.clicked.connect(self._ed_use_playing)
        bar = QHBoxLayout()
        bar.addWidget(use)
        bar.addStretch(1)
        song.body.addLayout(bar)
        lay.addWidget(song)

        text = Card("1  ·  Lyrics", "One line per row. Leave an empty row for a music-only break.")
        self.ed_text = QPlainTextEdit()
        self.ed_text.setPlaceholderText("Type or paste the lyrics here…")
        self.ed_text.setMinimumHeight(230)
        self.ed_text.textChanged.connect(self._ed_text_changed)
        text.body.addWidget(self.ed_text)
        tb = QHBoxLayout()
        load = GlowButton("Find the words online", icon="download", variant="ghost")
        load.setToolTip("Some songs have their words online but no timings. Load them here and just sync them.")
        load.clicked.connect(self._ed_fetch_text)
        open_btn = GlowButton("Open .lrc / .txt", icon="folder", variant="ghost")
        open_btn.clicked.connect(self._ed_open_file)
        clear = GlowButton("Start over", icon="trash", variant="ghost")
        clear.clicked.connect(self._ed_clear)
        for b in (load, open_btn):
            tb.addWidget(b)
        tb.addStretch(1)
        tb.addWidget(clear)
        text.body.addLayout(tb)
        lay.addWidget(text)

        sync = Card("2  ·  Sync", "Play the song in Spotify and press Tap (or Space) the moment each line starts. "
                                  "Made a mistake? Undo (Backspace) steps back one line.")
        self.ed_stage = SyncStage()
        sync.body.addWidget(self.ed_stage)
        sb = QHBoxLayout()
        top = GlowButton("Start from the top", icon="refresh", variant="ghost")
        top.setToolTip("Restarts the song and clears the timings")
        top.clicked.connect(self._ed_from_top)
        pp = GlowButton("Play / pause", icon="play", variant="ghost")
        pp.clicked.connect(lambda: self.media_command.emit("toggle"))
        undo = GlowButton("Undo", icon="prev", variant="ghost")
        undo.clicked.connect(self._ed_undo)
        self.ed_tap_btn = GlowButton("Tap  (Space)", icon="tap")
        self.ed_tap_btn.setMinimumWidth(170)
        self.ed_tap_btn.clicked.connect(self._ed_do_tap)
        for b in (top, pp, undo):
            sb.addWidget(b)
        sb.addStretch(1)
        sb.addWidget(self.ed_tap_btn)
        sync.body.addLayout(sb)
        self.ed_progress = QLabel("")
        self.ed_progress.setObjectName("rowDesc")
        sync.body.addWidget(self.ed_progress)
        lay.addWidget(sync)

        check = Card("3  ·  Check & fix", "Double-click a time or a line to edit it. Select lines and nudge them if "
                                          "they show up early or late.")
        self.ed_table = QTableWidget(0, 2)
        self.ed_table.setHorizontalHeaderLabels(["Time", "Line"])
        self.ed_table.verticalHeader().setVisible(False)
        self.ed_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.ed_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.ed_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ed_table.setMinimumHeight(260)
        self.ed_table.itemChanged.connect(self._ed_table_edited)
        check.body.addWidget(self.ed_table)
        nb = QHBoxLayout()
        for label, delta in (("Earlier 0.1 s", -100), ("Later 0.1 s", 100)):
            b = GlowButton(label, variant="ghost")
            b.setToolTip("Moves the selected lines (or all lines if none are selected)")
            b.clicked.connect(lambda _=False, d=delta: self._ed_nudge(d))
            nb.addWidget(b)
        nb.addStretch(1)
        check.body.addLayout(nb)
        self.ed_preview = ToggleSwitch()
        self.ed_preview.toggled.connect(self._ed_preview_toggled)
        check.body.addWidget(row("Preview on my overlay", self.ed_preview,
                                 "Shows these lyrics on the real overlay while the song plays"))
        lay.addWidget(check)

        done = Card("4  ·  Save & share")
        db = QHBoxLayout()
        save = GlowButton("Save to my lyrics", icon="check")
        save.clicked.connect(self._ed_save)
        share = GlowButton("Share with the community…", icon="share", variant="ghost")
        share.clicked.connect(self._ed_share)
        db.addWidget(save)
        db.addWidget(share)
        db.addStretch(1)
        done.body.addLayout(db)
        self.ed_status = QLabel("Save: the lyrics show for you whenever this song plays. Share: other listeners "
                                "can find them in the community library and ★ favorite them.")
        self.ed_status.setObjectName("rowDesc")
        self.ed_status.setWordWrap(True)
        done.body.addWidget(self.ed_status)
        lay.addWidget(done)
        lay.addStretch(1)

        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=lambda: self._ed_key("tap"),
                  context=Qt.ShortcutContext.WindowShortcut)
        QShortcut(QKeySequence(Qt.Key.Key_Backspace), self, activated=lambda: self._ed_key("undo"),
                  context=Qt.ShortcutContext.WindowShortcut)
        self._ed_timer = QTimer(self)
        self._ed_timer.setInterval(100)
        self._ed_timer.timeout.connect(self._ed_tick)
        self._ed_timer.start()
        self._ed_refresh()

    # editor helpers --------------------------------------------------------
    def _ed_on_page(self):
        return self.isVisible() and self.stack.currentIndex() == self._write_page_index

    def _ed_key(self, what):
        # text boxes keep Space/Backspace for typing (Qt gives them the key before this shortcut)
        if not self._ed_on_page() or isinstance(self.focusWidget(), (QPlainTextEdit, QLineEdit, QTableWidget)):
            return
        self._ed_do_tap() if what == "tap" else self._ed_undo()

    def _ed_position(self):
        clock = getattr(self, "clock", None)
        return clock.position() if clock is not None else 0.0

    def _ed_tick(self):
        if self._ed_on_page():
            ms = self._ed_position()
            self.ed_stage.time_text = f"{int(ms // 60000)}:{(ms % 60000) / 1000:04.1f}"
            self.ed_stage.update()

    def _ed_song(self):
        return {"title": self.ed_title.text().strip(), "artist": self.ed_artist.text().strip(),
                "album": self.ed_album.text().strip(), "duration": (parse_time(self.ed_len.text()) or 0) / 1000}

    def _ed_set_song(self, title="", artist="", album="", duration=0):
        self.ed_title.setText(title or "")
        self.ed_artist.setText(artist or "")
        self.ed_album.setText(album or "")
        self.ed_len.setText(fmt_len(duration))

    def _ed_use_playing(self):
        t = getattr(self, "current_track", None)
        if t is None:
            self._ed_say("Play a song in Spotify first.", error=True)
            return
        self._ed_set_song(t.title, t.artist, t.album, t.duration_ms / 1000)

    def _ed_say(self, text, error=False):
        self.ed_status.setText(text)
        self.ed_status.setStyleSheet(f"color: {'#FF6B6B' if error else theme.c('accent').name()};")

    def _ed_text_changed(self):
        new = lines_from_text(self.ed_text.toPlainText())
        old = self.ed_lines
        for i, (t, txt) in enumerate(new):  # keep timings for lines that didn't change
            if t is None and i < len(old) and old[i][1] == txt:
                new[i][0] = old[i][0]
        self.ed_lines = new
        self.ed_tap = next((i for i, (t, _x) in enumerate(new) if t is None), len(new))
        self._ed_refresh()

    def _ed_load(self, song, lines, entry_id=None):
        """Put a song and its lines (timed or not) into the editor."""
        self.ed_id = entry_id
        self._ed_set_song(song.get("title"), song.get("artist"), song.get("album"), song.get("duration"))
        self.ed_text.blockSignals(True)
        self.ed_text.setPlainText("\n".join(txt for _t, txt in lines))
        self.ed_text.blockSignals(False)
        self.ed_lines = [[t, txt] for t, txt in lines]
        self.ed_tap = next((i for i, (t, _x) in enumerate(self.ed_lines) if t is None), len(self.ed_lines))
        self._ed_refresh()

    def _ed_clear(self):
        if self.ed_lines and QMessageBox.question(self, "Start over", "Clear the lyrics and timings in the editor?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.ed_id = None
        self._ed_load({}, [])
        self._ed_use_playing()

    def _ed_from_top(self):
        for l in self.ed_lines:
            l[0] = None
        self.ed_tap = 0
        self.media_command.emit("seek:0")
        self.media_command.emit("play")
        self._ed_refresh()

    def _ed_do_tap(self):
        if not self.ed_lines:
            self._ed_say("Type or paste the lyrics first (step 1).", error=True)
            return
        if self.ed_tap >= len(self.ed_lines):
            return
        ms = max(0.0, self._ed_position() - TAP_LAG)
        if self.ed_tap > 0 and self.ed_lines[self.ed_tap - 1][0] is not None:
            ms = max(ms, self.ed_lines[self.ed_tap - 1][0] + 50)
        self.ed_lines[self.ed_tap][0] = ms
        self.ed_tap += 1
        self.ed_stage.pulse()
        self._ed_refresh()

    def _ed_undo(self):
        if self.ed_tap > 0:
            self.ed_tap -= 1
            self.ed_lines[self.ed_tap][0] = None
            self._ed_refresh()

    def _ed_nudge(self, delta):
        rows = sorted({i.row() for i in self.ed_table.selectedIndexes()}) or range(len(self.ed_lines))
        for r in rows:
            if self.ed_lines[r][0] is not None:
                self.ed_lines[r][0] = max(0.0, self.ed_lines[r][0] + delta)
        self._ed_refresh(keep_selection=True)

    def _ed_table_edited(self, item):
        r = item.row()
        if r >= len(self.ed_lines):
            return
        if item.column() == 0:
            ms = parse_time(item.text())
            self.ed_lines[r][0] = ms
        else:
            self.ed_lines[r][1] = item.text().strip()
            self.ed_text.blockSignals(True)
            self.ed_text.setPlainText("\n".join(txt for _t, txt in self.ed_lines))
            self.ed_text.blockSignals(False)
        QTimer.singleShot(0, lambda: self._ed_refresh(keep_selection=True))

    def _ed_refresh(self, keep_selection=False):
        lines = self.ed_lines
        n = len(lines)
        timed = sum(1 for t, _x in lines if t is not None)
        i = self.ed_tap

        def label(k):
            if not 0 <= k < n:
                return ""
            return lines[k][1] or "♪  music-only break"
        self.ed_stage.set_lines(label(i - 1), label(i) or ("Type the lyrics in step 1" if not n else ""),
                                label(i + 1), done=bool(n) and i >= n)
        self.ed_progress.setText(f"{timed} of {n} lines synced" if n else "")
        sel = {x.row() for x in self.ed_table.selectedIndexes()} if keep_selection else set()
        self.ed_table.blockSignals(True)
        self.ed_table.setRowCount(n)
        for r, (t, txt) in enumerate(lines):
            ti = QTableWidgetItem(fmt_ms(t) if t is not None else "--:--")
            ti.setForeground(theme.c("accent") if t is not None else theme.c("dim"))
            self.ed_table.setItem(r, 0, ti)
            self.ed_table.setItem(r, 1, QTableWidgetItem(txt or "♪"))
        self.ed_table.blockSignals(False)
        for r in sel:
            self.ed_table.selectRow(r)
        if self.ed_preview.isChecked():
            self._ed_preview_toggled(True)

    def _ed_ready(self, for_sharing=False):
        """The entry to save, or None after explaining what's missing."""
        song = self._ed_song()
        lines = [l for l in self.ed_lines]
        if not song["title"] or not song["artist"]:
            self._ed_say("Fill in the song title and artist first (or use the song playing now).", error=True)
            return None
        if len([l for l in lines if l[1]]) < 2:
            self._ed_say("Add the lyrics first (step 1).", error=True)
            return None
        if for_sharing:
            if any(t is None for t, _x in lines):
                self._ed_say("Sync every line before sharing (step 2). The library only takes synced lyrics.",
                             error=True)
                return None
            if not song["duration"]:
                self._ed_say("Fill in the song's length (like 3:41) before sharing.", error=True)
                return None
            times = [t for t, _x in lines]
            if any(b < a for a, b in zip(times, times[1:])):
                self._ed_say("Some lines are out of order in time. Fix them in step 3.", error=True)
                return None
        entry = {"title": song["title"], "artist": song["artist"], "album": song["album"],
                 "duration": song["duration"], "lines": lines, "kind": "own"}
        if self.ed_id:
            entry["id"] = self.ed_id
            old = self.library.get(self.ed_id)
            if old and old.get("shared_at"):
                entry["shared_at"] = old["shared_at"]
        return entry

    def _ed_save(self):
        entry = self._ed_ready()
        if entry is None:
            return None
        if any(t is None for t, _x in entry["lines"]):
            entry["lines"] = [[t, txt] for t, txt in entry["lines"]]
        stored = self.library.put(entry)
        self.ed_id = stored["id"]
        self._ed_say("Saved to your lyrics. They'll show whenever this song plays.")
        self.lyrics_saved.emit(stored["id"])
        return stored

    def _ed_share(self):
        entry = self._ed_ready(for_sharing=True)
        if entry is None:
            return
        self._confirm_share(entry, from_editor=True)

    def _confirm_share(self, entry, from_editor=False):
        if not community.enabled():
            self._ed_say("The community library isn't open yet. Your lyrics are saved on this PC.", error=True)
            self._lib_say("The community library isn't open yet.", error=True)
            return
        if not self.account.signed_in:
            box = QMessageBox(self)
            box.setWindowTitle("Sign in to share")
            box.setText("Sharing lyrics needs a quick Discord sign-in, so every share shows a real, unique "
                        "name.\n\nYour browser opens Discord. Press Authorize, then come back here. Your Discord "
                        "username will be shown on lyrics you share. Browsing and ★ favoriting never need it.")
            go = box.addButton("Sign in with Discord", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Not now", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not go:
                return
            self._pending_share = (entry, from_editor)
            self._say_everywhere("Waiting for Discord in your browser…")
            self.account.sign_in()
            return
        msg = (f"Share your synced lyrics for “{entry['title']}” by {entry['artist']}?\n\n"
               f"They'll show as synced by @{self.account.username}. Other listeners can find them in the "
               "community library, test them and ★ favorite them.\n\nPlease only share lyrics that match the song.")
        if QMessageBox.question(self, "Share with the community", msg) != QMessageBox.StandardButton.Yes:
            return
        stored = self.library.put(entry) if from_editor or not entry.get("id") else entry
        if from_editor:
            self.ed_id = stored["id"]
            self.lyrics_saved.emit(stored["id"])
        self._sharing = stored["id"]
        self.lib_client.publish(stored, self.account)

    def _say_everywhere(self, text, error=False):
        self._ed_say(text, error)
        self._lib_say(text, error)

    def _on_signed_in(self, ok, message):
        self._say_everywhere(message, error=not ok)
        self._account_rebuild()
        pending, self._pending_share = getattr(self, "_pending_share", None), None
        if ok:
            self.overlay_toast.emit(message)
            self.raise_()
            self.activateWindow()
            if pending is not None:
                self._confirm_share(*pending)

    def _on_publish_progress(self, eid, text):
        if getattr(self, "_sharing", None) == eid:
            if self.ed_id == eid:
                self._ed_say(text)
            self._lib_say(text)

    def _on_publish_done(self, eid, ok, message):
        if ok and message.startswith("unshared|"):
            self.library.mark_unshared(eid)
            message = message.split("|", 1)[1]
        elif ok:
            head, message = message.split("|", 1)
            row_id = head[3:]
            self.library.mark_shared(eid, int(row_id) if row_id.isdigit() else None)
            self.overlay_toast.emit("Lyrics shared with the community ♪")
        else:
            self.overlay_toast.emit("That didn't work")
        if self.ed_id == eid:
            self._ed_say(message, error=not ok)
        self._lib_say(message, error=not ok)

    def _ed_open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open lyrics", "", "Lyrics (*.lrc *.txt)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                text = fh.read()
        except OSError as e:
            self._ed_say(f"Couldn't open the file: {e.strerror}", error=True)
            return
        lines = lines_from_text(text)
        song = self._ed_song()
        if not song["title"]:
            song["title"] = os.path.splitext(os.path.basename(path))[0]
        self._ed_load(song, lines, self.ed_id)
        self._ed_say(f"Loaded {len(lines)} lines.")

    def _ed_fetch_text(self):  # "Find the words online"
        song = self._ed_song()
        if not song["title"]:
            self._ed_use_playing()
            song = self._ed_song()
        if not song["title"]:
            return
        self._ed_say("Looking for the words…")
        self._ed_search_req = self.lib_client.search(f"{song['title']} {song['artist']}")

    def _ed_preview_toggled(self, on):
        if on and any(t is not None for t, _x in self.ed_lines):
            self.preview_lyrics.emit([[t, txt] for t, txt in self.ed_lines if t is not None])
        else:
            self.preview_lyrics.emit(None)

    # ============================================================== Library
    def _build_library(self):
        lay = self._page("Lyrics Library", "Your lyrics, your ★ favorites from the community, and songs still "
                                           "waiting for lyrics.", "book")
        self._library_page_index = self._building
        tile = CommunityTile()
        tile.clicked.connect(lambda _checked=False: self.open_community())
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(10, 4, 10, 8)
        wl.addWidget(tile)
        lay.addWidget(wrap)
        self.acc_row = QHBoxLayout()
        self.acc_row.setContentsMargins(16, 0, 16, 0)
        lay.addLayout(self.acc_row)
        self.lib_note = QLabel("")
        self.lib_note.setObjectName("rowDesc")
        self.lib_note.setWordWrap(True)
        self.lib_note.setContentsMargins(16, 0, 16, 0)
        lay.addWidget(self.lib_note)

        mine = Card("Your lyrics", "Lyrics you wrote. They're used whenever that song plays. Share them to let "
                                   "other listeners use them too.")
        self.lib_mine = QVBoxLayout()
        self.lib_mine.setSpacing(6)
        mine.body.addLayout(self.lib_mine)
        lay.addWidget(mine)

        favs = Card("★ Favorites", "Community lyrics you picked. Each one is used whenever its song plays.")
        self.lib_favs = QVBoxLayout()
        self.lib_favs.setSpacing(6)
        favs.body.addLayout(self.lib_favs)
        lay.addWidget(favs)

        miss = Card("Songs missing lyrics", "Songs you played that had no lyrics yet. Look for them in the community "
                                            "library, or write them yourself.")
        self.lib_missing = QVBoxLayout()
        self.lib_missing.setSpacing(6)
        miss.body.addLayout(self.lib_missing)
        lay.addWidget(miss)
        lay.addStretch(1)

        self.library.changed.connect(self._lib_rebuild)
        self.lib_client.search_done.connect(self._on_search_done)
        self.lib_client.publish_progress.connect(self._on_publish_progress)
        self.lib_client.publish_done.connect(self._on_publish_done)
        self._ed_search_req = -1
        self._sheet = None
        self._pending_share = None
        self.account.changed.connect(self._account_rebuild)
        self.account.changed.connect(self._lib_rebuild)
        self.account.sign_in_done.connect(self._on_signed_in)
        self._account_rebuild()
        self._lib_rebuild()

    def _account_rebuild(self):
        self._clear(self.acc_row)
        if not community.enabled():
            return
        if self.account.signed_in:
            text = f"Sharing as <b>{self.account.display}</b> (@{self.account.username}). Signed in with Discord"
            btn = GlowButton("Sign out", icon="close", variant="ghost")
            btn.clicked.connect(lambda _=False: self.account.sign_out())
        else:
            text = "Browsing and ★ favoriting need no account. To <b>share</b> lyrics, sign in with Discord."
            btn = GlowButton("Sign in with Discord", icon="share", variant="ghost")
            btn.clicked.connect(lambda _=False: (self._say_everywhere("Waiting for Discord in your browser…"),
                                                 self.account.sign_in()))
        lbl = QLabel(text)
        lbl.setObjectName("rowDesc")
        lbl.setWordWrap(True)
        self.acc_row.addWidget(lbl, 1)
        self.acc_row.addWidget(btn)

    def _lib_page_shown(self):
        pass

    def open_community(self, query=""):
        """The community library, in a panel over the (blurred) control center."""
        if self._sheet is not None:
            return
        if not isinstance(query, str):  # a button's "clicked" passes True/False — not a search
            query = ""
        self._sheet = CommunitySheet(self.root, self, query)
        self._sheet.closed.connect(self._community_closed)

    def _community_closed(self):
        self._sheet = None
        self.preview_lyrics.emit(None)  # stop any test on the overlay

    def favorite(self, row):
        """★ Use this community version whenever its song plays (replaces an earlier favorite for it)."""
        from .lyrics import parse_lrc
        own = next((e for e in self.library.entries if e.get("kind", "own") == "own"
                    and same_song(e, row.get("title"), row.get("artist"))), None)
        if own is not None and QMessageBox.question(
                self, "Favorite", f"You wrote your own lyrics for “{own['title']}”. Use this community version "
                                  "instead?") != QMessageBox.StandardButton.Yes:
            return None
        stored = self.library.put({"title": row.get("title"), "artist": row.get("artist"),
                                   "album": row.get("album") or "", "duration": row.get("duration") or 0,
                                   "lines": [[l.time, l.text] for l in parse_lrc(row.get("synced") or "")],
                                   "kind": "fav", "source_id": row.get("id"), "author": row.get("author") or ""})
        self.preview_lyrics.emit(None)
        self.lyrics_saved.emit(stored["id"])
        self.overlay_toast.emit("★ Favorite saved")
        return stored

    def unfavorite(self, eid):
        self.library.delete(eid)
        self.lyrics_saved.emit("")

    def _lib_say(self, text, error=False):
        self.lib_note.setText(text)
        self.lib_note.setStyleSheet(f"color: {'#FF6B6B' if error else theme.c('accent').name()};")

    @staticmethod
    def _clear(layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()  # gone from view right away, not just when Qt gets round to deleting it
                w.deleteLater()
            elif item.layout() is not None:
                LibraryPages._clear(item.layout())

    def _lib_row(self, title, sub, badge, buttons):
        """One list row: title and details on the left, a badge, then action buttons."""
        w = QWidget()
        w.setObjectName("libRow")
        w.setStyleSheet(f"QWidget#libRow {{ background: {theme.c('bg3').name()}; border-radius: 10px; }}")
        h = QHBoxLayout(w)
        h.setContentsMargins(14, 8, 8, 8)
        h.setSpacing(8)
        left = QVBoxLayout()
        left.setSpacing(1)
        t = QLabel(title)
        t.setStyleSheet("font-weight: 600;")
        left.addWidget(t)
        if sub:
            s = QLabel(sub)
            s.setObjectName("rowDesc")
            left.addWidget(s)
        h.addLayout(left, 1)
        if badge:
            b = QLabel(badge)
            b.setStyleSheet(f"color: {theme.c('accent').name()}; font-size: 11px; font-weight: 600;")
            h.addWidget(b)
        for text, icon, fn, variant in buttons:
            btn = GlowButton(text, icon=icon, variant=variant)
            btn.clicked.connect(fn)
            h.addWidget(btn)
        return w

    def _lib_rebuild(self):
        own = [e for e in self.library.entries if e.get("kind", "own") == "own"]
        favs = [e for e in self.library.entries if e.get("kind") == "fav"]
        self._clear(self.lib_mine)
        if not own:
            e = QLabel("Nothing yet. Write lyrics for a song on the Write Lyrics page.")
            e.setObjectName("rowDesc")
            e.setWordWrap(True)
            self.lib_mine.addWidget(e)
        for entry in own:
            synced = all(t is not None for t, _x in entry["lines"])
            n = len([1 for _t, x in entry["lines"] if x])
            badge = "SHARED ✓" if entry.get("shared_at") else "ONLY YOU"
            eid = entry["id"]
            buttons = [("Edit", "pencil", lambda _=False, i=eid: self._lib_edit(i), "ghost")]
            if synced and not entry.get("shared_at"):
                buttons.append(("Share", "share", lambda _=False, i=eid: self._lib_share(i), "ghost"))
            elif entry.get("community_id") and self.account.signed_in:
                buttons.append(("Unshare", "close", lambda _=False, i=eid: self._lib_unshare(i), "ghost"))
            buttons += [("Export", "download", lambda _=False, i=eid: self._lib_export(i), "ghost"),
                        ("", "trash", lambda _=False, i=eid: self._lib_delete(i), "danger")]
            self.lib_mine.addWidget(self._lib_row(
                f"{entry['title']}  ·  {entry['artist']}",
                f"{n} lines • {'synced' if synced else 'not synced yet'}", badge, buttons))

        self._clear(self.lib_favs)
        if not favs:
            e = QLabel("No favorites yet. Open the community library, test a version and press ★ Favorite.")
            e.setObjectName("rowDesc")
            e.setWordWrap(True)
            self.lib_favs.addWidget(e)
        for entry in favs:
            n = len([1 for _t, x in entry["lines"] if x])
            eid = entry["id"]
            buttons = [("Other versions", "search",
                        lambda _=False, e=entry: self.open_community(f"{e['title']}"), "ghost"),
                       ("Remove ★", "close", lambda _=False, i=eid: self.unfavorite(i), "ghost")]
            self.lib_favs.addWidget(self._lib_row(
                f"{entry['title']}  ·  {entry['artist']}",
                f"{n} lines • synced by {entry.get('author') or 'a listener'}", "★", buttons))

        self._clear(self.lib_missing)
        if not self.library.missing:
            e = QLabel("No songs here. When a song has no lyrics, it shows up here so you can add them.")
            e.setObjectName("rowDesc")
            e.setWordWrap(True)
            self.lib_missing.addWidget(e)
        for m in self.library.missing[:25]:
            buttons = [("Find in community", "search",
                        lambda _=False, s=m: self.open_community(s["title"]), "ghost"),
                       ("Write lyrics", "pencil", lambda _=False, s=m: self._lib_write_missing(s), "primary"),
                       ("", "close", lambda _=False, s=m: self.library.forget_missing(s["title"], s["artist"]),
                        "ghost")]
            self.lib_missing.addWidget(self._lib_row(
                m["title"], f"{m['artist']}{'  •  ' + fmt_len(m.get('duration')) if m.get('duration') else ''}",
                "", buttons))

    # finding the words for the editor ---------------------------------------
    def _on_search_done(self, req, results, error):
        if req == self._ed_search_req:
            self._ed_got_text(results, error)

    @staticmethod
    def _result_lines(res):
        from .lyrics import parse_lrc
        if res.get("syncedLyrics"):
            return [[l.time, l.text] for l in parse_lrc(res["syncedLyrics"])]
        return lines_from_text(res.get("plainLyrics") or "")

    def _show_text(self, title, text):
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(460, 560)
        v = QVBoxLayout(dlg)
        area = QScrollArea()
        area.setWidgetResizable(True)
        lbl = QLabel(text or "♪ Instrumental")
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setContentsMargins(10, 10, 10, 10)
        area.setWidget(lbl)
        v.addWidget(area)
        close = GlowButton("Close", variant="ghost")
        close.clicked.connect(dlg.accept)
        v.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        dlg.exec()

    def _ed_got_text(self, results, error):
        if results is None:
            self._ed_say(error, error=True)
            return
        song = self._ed_song()
        best = None
        for r in results:
            if same_song({"title": r.get("trackName"), "artist": r.get("artistName"), "duration": r.get("duration")},
                         song["title"], song["artist"], song["duration"] or None):
                if r.get("syncedLyrics"):
                    best = r
                    break
                best = best or r
        if best is None or best.get("instrumental"):
            self._ed_say("Couldn't find the words online. Type them in, you'll be the first!")
            return
        if self.ed_lines and QMessageBox.question(self, "Replace lyrics", "Replace the text in the editor with "
                                                  "the words found online?") != QMessageBox.StandardButton.Yes:
            return
        self._ed_load(song, self._result_lines(best), self.ed_id)
        self._ed_say("Words loaded. Now sync them in step 2." if not best.get("syncedLyrics")
                     else "Words and timings loaded. Check them in step 3.")

    # your lyrics -----------------------------------------------------------
    def _lib_edit(self, eid):
        e = self.library.get(eid)
        if e is not None:
            self._ed_load(e, e["lines"], eid if e.get("kind", "own") == "own" else None)
            self.go_to("Write Lyrics")

    def _lib_unshare(self, eid):
        e = self.library.get(eid)
        if e is None or not e.get("community_id"):
            return
        if QMessageBox.question(self, "Unshare", f"Remove your lyrics for “{e['title']}” from the community "
                                "library? (They stay on this PC.)") != QMessageBox.StandardButton.Yes:
            return
        self._lib_say("Removing…")
        self.lib_client.unpublish(e, self.account)

    def _lib_share(self, eid):
        e = self.library.get(eid)
        if e is None:
            return
        if not e.get("duration"):
            self._lib_say("This one has no song length. Open it with Edit, fill in the length, then share.",
                          error=True)
            return
        self._confirm_share(e)

    def _lib_export(self, eid):
        e = self.library.get(eid)
        if e is None:
            return
        safe = "".join(c for c in f"{e['artist']} - {e['title']}" if c not in '\\/:*?"<>|')
        synced = all(t is not None for t, _x in e["lines"])
        path, _ = QFileDialog.getSaveFileName(self, "Export lyrics", f"{safe}.{'lrc' if synced else 'txt'}",
                                              "Lyrics (*.lrc *.txt)")
        if not path:
            return
        head = f"[ti:{e['title']}]\n[ar:{e['artist']}]\n" + (f"[al:{e['album']}]\n" if e.get("album") else "")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(head + to_lrc(e["lines"]) if synced else to_plain(e["lines"]))
            self._lib_say(f"Exported to {os.path.basename(path)}")
        except OSError as err:
            self._lib_say(f"Couldn't save: {err.strerror}", error=True)

    def _lib_delete(self, eid):
        e = self.library.get(eid)
        if e is None:
            return
        if QMessageBox.question(self, "Delete lyrics", f"Delete your lyrics for “{e['title']}”? (Shared copies "
                                "stay in the community library.)") == QMessageBox.StandardButton.Yes:
            self.library.delete(eid)
            self.lyrics_saved.emit("")

    def _lib_write_missing(self, m):
        self._ed_load(m, [])
        self.go_to("Write Lyrics")
        self._ed_say("Tip: press “Find the words online”. Sometimes the words exist and only need syncing.")



class CommunityTile(QAbstractButton):
    """The big clickable "Community lyrics" card on the Lyrics Library page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(108)
        self.hover = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(220)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "hover", float(v)), self.update()))

    def _to(self, v):
        self._anim.stop()
        self._anim.setStartValue(self.hover)
        self._anim.setEndValue(v)
        self._anim.start()

    def enterEvent(self, e):
        self._to(1.0)

    def leaveEvent(self, e):
        self._to(0.0)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(AA)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        paint_glow(p, r, 16, theme.c("accent"), 0.3 + 0.4 * self.hover, 8)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0, mix(theme.c("bg2"), theme.c("accent"), 0.16 + 0.1 * self.hover))
        g.setColorAt(1, mix(theme.c("bg2"), theme.c("accent2"), 0.10 + 0.08 * self.hover))
        p.setPen(QPen(theme.c("accent", 0.45 + 0.4 * self.hover), 1.2))
        p.setBrush(QBrush(g))
        p.drawRoundedRect(r, 16, 16)
        icon = QRectF(r.left() + 24, r.center().y() - 24, 48, 48)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("accent", 0.18))
        p.drawRoundedRect(icon, 14, 14)
        draw_icon(p, "book", icon.adjusted(12, 12, -12, -12), theme.c("accent"), 2.0)
        f = QFont("Segoe UI")
        f.setPixelSize(18)
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(theme.c("text"))
        x = icon.right() + 18
        p.drawText(QRectF(x, r.top() + 20, r.width() - x - 60, 28), Qt.AlignmentFlag.AlignVCenter,
                   "Community lyrics")
        f2 = QFont("Segoe UI")
        f2.setPixelSize(12)
        p.setFont(f2)
        p.setPen(theme.c("dim"))
        p.drawText(QRectF(x, r.top() + 50, r.width() - x - 60, 40), Qt.TextFlag.TextWordWrap,
                   "Search a song, test the lyrics other listeners synced, and ★ favorite the best one. It's "
                   "used whenever that song plays.")
        arrow = QRectF(r.right() - 46, r.center().y() - 11, 22, 22)
        draw_icon(p, "next", arrow.translated(4 * self.hover, 0), theme.c("accent"), 2.0)


class CommunitySheet(QWidget):
    """The community library as a panel inside the app, over a blurred copy of the control center:
    search a song, open it to see every version listeners synced, test one on the overlay, ★ favorite it."""

    closed = Signal()

    def __init__(self, root, win, query=""):
        super().__init__(root)
        self.win = win
        self.t = 0.0
        self._closing = False
        self._req = -1
        self._song = None
        self._testing = None
        self.setGeometry(root.rect())
        shot = root.grab().toImage()
        small = shot.scaled(max(1, shot.width() // 5), max(1, shot.height() // 5),
                            Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.bg = QPixmap.fromImage(blur_image(small, 5))

        self.panel = Card(parent=self)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        t = QLabel("Community lyrics")
        t.setStyleSheet("font-size: 20px; font-weight: 700;")
        s = QLabel("Lyrics synced by MNKLyrics listeners. Test a version, then ★ favorite the one that fits.")
        s.setObjectName("cardSub")
        s.setWordWrap(True)
        titles.addWidget(t)
        titles.addWidget(s)
        head.addLayout(titles, 1)
        close = GlowButton("", icon="close", variant="ghost")
        close.setToolTip("Close (Esc)")
        close.clicked.connect(self.close_sheet)
        head.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self.panel.body.addLayout(head)

        sb = QHBoxLayout()
        self.query = QLineEdit(query)
        self.query.setPlaceholderText("Search a song or artist…")
        self.query.returnPressed.connect(self.search)
        self._typing = QTimer(self)
        self._typing.setSingleShot(True)
        self._typing.setInterval(400)
        self._typing.timeout.connect(self.search)
        self.query.textEdited.connect(lambda _t: self._typing.start())
        now = GlowButton("Song playing now", icon="music", variant="ghost")
        now.clicked.connect(self._search_playing)
        sb.addWidget(self.query, 1)
        sb.addWidget(now)
        self.panel.body.addLayout(sb)
        self.note = QLabel("")
        self.note.setObjectName("rowDesc")
        self.note.setWordWrap(True)
        self.panel.body.addWidget(self.note)

        self.pages = QStackedWidget()
        # page 0: songs
        self.song_list, songs_area = self._list_area()
        self.pages.addWidget(songs_area)
        # page 1: the versions of one song + a text preview
        vpage = QWidget()
        vl = QVBoxLayout(vpage)
        vl.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        back = GlowButton("All songs", icon="prev", variant="ghost")
        back.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        self.song_title = QLabel("")
        self.song_title.setStyleSheet("font-size: 15px; font-weight: 700;")
        play = GlowButton("Play in Spotify", icon="play", variant="ghost")
        play.clicked.connect(self._play_song)
        top.addWidget(back)
        top.addWidget(self.song_title, 1)
        top.addWidget(play)
        vl.addLayout(top)
        split = QHBoxLayout()
        self.version_list, varea = self._list_area()
        split.addWidget(varea, 1)
        self.preview = QLabel("Choose a version to read it here.")
        self.preview.setWordWrap(True)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.preview.setContentsMargins(14, 12, 14, 12)
        self.preview.setStyleSheet(f"background: {theme.c('bg3').name()}; border-radius: 10px;")
        parea = QScrollArea()
        parea.setWidgetResizable(True)
        parea.setWidget(self.preview)
        parea.setFixedWidth(270)
        split.addWidget(parea)
        vl.addLayout(split, 1)
        self.pages.addWidget(vpage)
        self.panel.body.addWidget(self.pages, 1)
        out = self.panel.outer  # let the lists take the panel's full height (Card ends with a stretch)
        out.takeAt(out.count() - 1)
        out.setStretchFactor(self.panel.body, 1)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.close_sheet)
        win.lib_client.call_done.connect(self._on_result)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._step)
        self._after = None
        self._anim.finished.connect(self._anim_finished)
        self._fade(1.0)
        self.show()
        self.raise_()
        self.query.setFocus()
        if not community.enabled():
            self.note.setText("The community library isn't open yet.")
        else:
            self.search()

    # ------------------------------------------------------------ frame
    @staticmethod
    def _list_area():
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(6)
        lay.addStretch(1)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(holder)
        return lay, area

    def _fade(self, to, done=None):
        self._anim.stop()
        self._anim.setStartValue(self.t)
        self._anim.setEndValue(to)
        self._after = done
        self._anim.start()

    def _anim_finished(self):
        if self._after is not None:
            fn, self._after = self._after, None
            fn()

    def _step(self, v):
        self.t = float(v)
        self._place()
        self.update()

    def _place(self):
        if not hasattr(self, "panel"):
            return  # still being built
        W, H = self.width(), self.height()
        w, h = min(900, W - 80), min(640, H - 70)
        y = (H - h) / 2 + (1 - self.t) * 26
        self.panel.setGeometry(int((W - w) / 2), int(y), int(w), int(h))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setOpacity(self.t)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 18, 18)
        p.setClipPath(clip)
        p.drawPixmap(self.rect(), self.bg)
        p.fillRect(self.rect(), QColor(0, 0, 0, 120))

    def mousePressEvent(self, e):
        if not self.panel.geometry().contains(e.position().toPoint()):
            self.close_sheet()

    def close_sheet(self):
        if self._closing:
            return
        self._closing = True
        try:
            self.win.lib_client.call_done.disconnect(self._on_result)
        except (RuntimeError, TypeError):
            pass

        def done():
            self.hide()
            self.closed.emit()
            self.deleteLater()
        self._fade(0.0, done)

    # ------------------------------------------------------------ data
    def _say(self, text, error=False):
        self.note.setText(text)
        self.note.setStyleSheet(f"color: {'#FF6B6B' if error else theme.c('accent').name()};")

    def _clear(self, lay):
        while lay.count() > 1:  # keep the stretch at the end
            item = lay.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()

    def _search_playing(self):
        t = getattr(self.win, "current_track", None)
        if t is None:
            self._say("Play a song in Spotify first.", error=True)
            return
        self.query.setText(t.title)
        self.search()

    def search(self):
        if not community.enabled():
            return
        self.pages.setCurrentIndex(0)
        self._say("Searching…")
        self._kind = "songs"
        self._req = self.win.lib_client.call(community.songs, self.query.text().strip())

    def open_song(self, song):
        self._song = song
        self.song_title.setText(f"{song['title']}  ·  {song['artist']}")
        self.preview.setText("Choose a version to read it here.")
        self._clear(self.version_list)
        self.pages.setCurrentIndex(1)
        self._say("Loading the versions…")
        self._kind = "versions"
        self._req = self.win.lib_client.call(community.versions, song["title"], song["artist"])

    def _on_result(self, req, result, error):
        if req != self._req:
            return
        if result is None:
            self._say(error, error=True)
            return
        if self._kind == "songs":
            self._show_songs(result)
        else:
            self._show_versions(result)

    def _show_songs(self, songs):
        self._clear(self.song_list)
        q = self.query.text().strip()
        if not songs:
            self._say(f"No community lyrics for “{q}” yet. You can write them on the Write Lyrics page!" if q
                      else "Nothing shared yet. Be the first. Write lyrics on the Write Lyrics page!")
            return
        self._say(f"{len(songs)} {'song' if len(songs) == 1 else 'songs'}"
                  + (f" for “{q}”" if q else ", newest first") + ". Click a song to see its versions.")
        for song in songs:
            fav = self.win.library.favorite_of(song["title"], song["artist"])
            n = song["count"]
            sub = "  •  ".join(x for x in (song["artist"], f"{n} {'version' if n == 1 else 'versions'}",
                                           community.ago(song.get("newest"))) if x)
            row = self.win._lib_row(song["title"] or "?", sub, "★ FAVORITE" if fav else "",
                                    [("Open", "next", lambda _=False, s=song: self.open_song(s), "ghost")])
            self.song_list.insertWidget(self.song_list.count() - 1, row)

    def _show_versions(self, rows):
        self._clear(self.version_list)
        if not rows:
            self._say("No versions found for this song.", error=True)
            return
        fav = self.win.library.favorite_of(self._song["title"], self._song["artist"])
        fav_id = fav.get("source_id") if fav else None
        t = getattr(self.win, "current_track", None)
        playing = t is not None and same_song({"title": self._song["title"], "artist": self._song["artist"]},
                                              t.title, t.artist)
        self._say(f"{len(rows)} {'version' if len(rows) == 1 else 'versions'}. Test one on your overlay"
                  + ("" if playing else " (play the song in Spotify to see it line up)") + ", then ★ favorite it.")
        for row in rows:
            from .lyrics import parse_lrc
            n = len([l for l in parse_lrc(row.get("synced") or "") if l.text])
            who = (row.get("author") or "").strip() or "a listener"
            sub = "  •  ".join(x for x in (community.ago(row.get("created_at")), f"{n} lines",
                                           fmt_len(row.get("duration"))) if x)
            is_fav = fav_id is not None and fav_id == row.get("id")
            buttons = [("", "eye", lambda _=False, r=row: self._read(r), "ghost"),
                       ("Test", "play", lambda _=False, r=row: self._test(r), "ghost"),
                       ("★ Favorited" if is_fav else "★ Favorite", "check" if is_fav else "plus",
                        lambda _=False, r=row: self._favorite(r), "primary" if not is_fav else "ghost")]
            w = self.win._lib_row(f"Version by {who}", sub, "", buttons)
            self.version_list.insertWidget(self.version_list.count() - 1, w)

    def _read(self, row):
        from .lyrics import parse_lrc
        self.preview.setText("\n".join(l.text for l in parse_lrc(row.get("synced") or "")) or "♪")

    def _test(self, row):
        from .lyrics import parse_lrc
        lines = [[l.time, l.text] for l in parse_lrc(row.get("synced") or "")]
        self._read(row)
        self._testing = row.get("id")
        self.win.preview_lyrics.emit(lines)
        t = getattr(self.win, "current_track", None)
        if t is not None and same_song({"title": row.get("title"), "artist": row.get("artist")}, t.title, t.artist):
            self._say("Testing on your overlay. Watch whether the lines appear on time. ★ Favorite it if they do.")
        else:
            self._say("Showing on your overlay. Press “Play in Spotify” and play this song to see if it lines up.")

    def _favorite(self, row):
        fav = self.win.library.favorite_of(row.get("title"), row.get("artist"))
        if fav is not None and fav.get("source_id") == row.get("id"):
            self.win.unfavorite(fav["id"])
            self._say("Removed from your favorites.")
        else:
            if self.win.favorite(row) is None:
                return
            self._testing = None
            self._say("★ Saved! This version is used whenever this song plays.")
        self.open_song(self._song)

    def _play_song(self):
        if self._song:
            QDesktopServices.openUrl(QUrl("spotify:search:" + quote(f"{self._song['title']} "
                                                                      f"{self._song['artist']}")))
