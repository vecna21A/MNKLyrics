<p align="center">
  <img src="screenshots/logo.png" width="120" alt="MNKLyrics logo">
</p>

<h1 align="center">MNKLyrics</h1>

<p align="center">
  <b>Real-time lyrics overlay for Windows · Works with Spotify</b><br>
  Follow along with the song you're playing, right on top of your game, your work or your chats.
</p>

<p align="center">
  <a href="https://github.com/vecna21A/MNKLyrics/releases/latest"><img src="https://img.shields.io/github/v/release/vecna21A/MNKLyrics?label=Download&style=for-the-badge&color=E5484D" alt="Download"></a>
  <a href="https://github.com/vecna21A/MNKLyrics/releases"><img src="https://img.shields.io/github/downloads/vecna21A/MNKLyrics/total?style=for-the-badge&color=24262B" alt="Downloads"></a>
  <a href="https://discord.gg/hG48dMH8Pm"><img src="https://img.shields.io/badge/Discord-Join-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord"></a>
</p>

<p align="center">
  <img src="screenshots/hero.png" alt="MNKLyrics overlay floating over a game scene">
</p>

---

## ✨ Features

- **Lyrics on top of everything.** A transparent, always-on-top overlay. Clicks pass straight through it to your game or app.
- **Time-synced karaoke.** Each line fills in as it's sung, and long lines wrap neatly without hiding the next one.
- **Make it yours.** Change the font, size, colors, glow, outline, shadow, background, animations, highlight style and position, and see every change live.
- **Configs you can share.** Save looks and share them with a short code like `MNK-rt1OAZ…`. Paste a friend's code to try their look instantly.
- **Made for gaming.** Global hotkeys, fade-on-hover, auto-hide when paused, and it stays light on your PC.
- **Shows up in your clips.** It's visible to whole-screen recorders, like Medal's Desktop Capture or OBS's Display Capture. You can also hide it from every recorder with one switch.
- **Automatic updates.** The app tells you when a new version is out and installs it in two clicks.
- **No login needed.** It works with the Spotify desktop app through Windows' media controls.

## 🖼️ Screenshots

<p align="center">
  <img src="screenshots/styles.png" alt="Four overlay looks: Neon Karaoke, Synthwave, Sakura and Gamer HUD"><br>
  <sub>A few of the built-in looks: Neon Karaoke, Synthwave, Sakura and Gamer HUD</sub>
</p>

<p align="center">
  <img src="screenshots/control-center.png" width="49%" alt="The MNKLyrics control center">
  <img src="screenshots/customize.png" width="49%" alt="Customizing colors and glow with a live preview"><br>
  <sub>The control center · Customizing with a live preview</sub>
</p>

<p align="center">
  <img src="screenshots/configs.png" width="75%" alt="The configs gallery"><br>
  <sub>Save, apply and share your looks</sub>
</p>

<sub>The lyrics in these screenshots are sample text written for MNKLyrics, not from a real song.</sub>

## 📥 Download & install

1. Download **`MNKLyrics.exe`** from the [latest release](https://github.com/vecna21A/MNKLyrics/releases/latest).
2. Double-click it. There's nothing to install and you don't need Python.
3. Play a song in the **Spotify desktop app**, and the lyrics appear.

You'll need Windows 10 or 11 and the Spotify desktop app.

> **"Windows protected your PC"?** It shows up for new apps from independent developers. Click **More info → Run anyway**. After that, MNKLyrics updates itself.

> **For games:** use **borderless or windowed fullscreen**. Exclusive fullscreen covers every other window, including overlays.

## ⌨️ Hotkeys

| Action | Keys |
|---|---|
| Show / hide overlay | `Ctrl+Alt+L` |
| Lock / unlock (click-through) | `Ctrl+Alt+K` |
| Open control center | `Ctrl+Alt+O` |
| Font size + / − | `Ctrl+Alt+Up` / `Ctrl+Alt+Down` |
| Lyrics earlier / later | `Ctrl+Alt+Right` / `Ctrl+Alt+Left` |
| Try another lyrics version | `Ctrl+Alt+V` |
| Next look | `Ctrl+Alt+P` |
| Play / pause | `Ctrl+Alt+Home` |

You can rebind every hotkey in the app. Unlock the overlay to drag it anywhere, scroll over it to resize the text, and double-click it to open the control center.

## 🎨 Sharing configs

1. Open **Configs** and click **Save current look**.
2. Hover your config and click **Share → Copy share code**.
3. Paste the code in our [Discord](https://discord.gg/hG48dMH8Pm).

When someone copies your code, MNKLyrics offers to import it in one click. A share code can only change how the lyrics look, nothing else.

## ❓ FAQ

**The lyrics have a typo or don't match the song.** Lyrics come from LRCLIB, a community database, and some songs have more than one version. Press `Ctrl+Alt+V` to switch versions; the app remembers your choice for that song.

**The lyrics are a bit early or late.** Use `Ctrl+Alt+Left` / `Ctrl+Alt+Right`, or the sync slider on the Now Playing page.

**The lyrics don't show up in my clips.** Your recorder is probably capturing only the game window. Switch it to full-screen or display capture; the app's *Recording & Clips* page has step-by-step help.

**My antivirus flagged it.** Small apps packaged like this one are sometimes flagged by mistake. MNKLyrics is safe; if you're unsure, scan it on [VirusTotal](https://www.virustotal.com).

## 🔒 Privacy

MNKLyrics **doesn't collect your data**: there's no account, no analytics, no tracking and no ads. It only connects to:

- **lrclib.net**, to look up lyrics (it sends the song's title, artist, album and length)
- **github.com**, to check for and download updates

Your settings, configs and lyrics cache stay on your PC in `%APPDATA%\MNKLyrics`.

## 💬 Community & credits

Made by **Vladimir**. Join the [Discord server](https://discord.gg/hG48dMH8Pm) to share configs, get help and hear about updates first. You can also add me on Discord: `vladimir.hm`.

- Synced lyrics are provided by [LRCLIB](https://lrclib.net), a free community lyrics database.
- Built with Python, Qt for Python (PySide6, LGPL v3), PyWinRT and requests. The full list of open-source components and their licenses is in the app under **Privacy & Legal**.

## ⚖️ Legal

MNKLyrics is a free, independent project. It isn't affiliated with, endorsed by, or sponsored by Spotify, LRCLIB, Medal, OBS or any other company mentioned here. Spotify is a trademark of Spotify AB, and all other trademarks belong to their respective owners.

Lyrics are shown for personal use while you listen, and they belong to their songwriters and publishers. MNKLyrics doesn't host or share lyrics.
