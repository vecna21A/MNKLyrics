"""Built-in updater backed by GitHub Releases.

Each release carries ``MNKLyrics.exe`` and a small ``latest.json`` manifest:
    {"version": "1.2.0", "url": ".../MNKLyrics.exe", "sha256": "...", "size": 123, "notes": "..."}
The app reads https://github.com/<repo>/releases/latest/download/latest.json, downloads the new
exe next to the running one, verifies its SHA-256, then swaps the files and restarts. A running
exe can't be overwritten on Windows but it *can* be renamed, so the old one is moved to
``MNKLyrics.exe.old`` and deleted on the next start.
"""

import hashlib
import os
import re
import subprocess
import sys
import threading
import time

import requests
from PySide6.QtCore import QObject, Signal

from .config import UPDATE_REPO, VERSION

ALLOWED_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
HEADERS = {"User-Agent": f"MNKLyrics/{VERSION} (updater)"}


def parse_version(text) -> tuple:
    nums = [int(n) for n in re.findall(r"\d+", str(text))[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _host_allowed(url) -> bool:
    m = re.match(r"^https://([^/:]+)/", url or "")
    return bool(m) and m.group(1).lower() in ALLOWED_HOSTS


def cleanup_old_exe(exe_path=None):
    """Delete the previous version left behind by an update (it may still be closing; retry briefly)."""
    exe = exe_path or sys.executable
    old = exe + ".old"
    for _ in range(20):
        if not os.path.exists(old):
            return
        try:
            os.remove(old)
            return
        except OSError:
            time.sleep(0.25)


class Updater(QObject):
    """States: not_configured, idle, checking, up_to_date, available, downloading, ready, error."""

    changed = Signal()

    def __init__(self, exe_path=None, manifest_url=None):
        super().__init__()
        self.exe_path = exe_path or sys.executable
        repo = UPDATE_REPO.strip()
        self.manifest_url = manifest_url or (
            f"https://github.com/{repo}/releases/latest/download/latest.json" if repo else "")
        self.state = "idle" if self.manifest_url else "not_configured"
        self.latest = None       # manifest dict when an update is available
        self.progress = 0.0
        self.error = ""
        self.last_check = 0.0
        self._download = None
        self._busy = False

    # ------------------------------------------------------------------ check
    @property
    def can_install(self) -> bool:
        return is_frozen() or self.exe_path != sys.executable

    def check(self, silent=False):
        if not self.manifest_url or self._busy or self.state in ("downloading", "ready"):
            return
        self._busy = True
        if not silent:
            self._set("checking")
        threading.Thread(target=self._check, args=(silent,), daemon=True).start()

    def _check(self, silent):
        try:
            r = requests.get(self.manifest_url, headers=HEADERS, timeout=10)
            if r.status_code == 404:
                raise RuntimeError("No release has been published yet.")
            r.raise_for_status()
            m = r.json()
            version = str(m.get("version", ""))
            url = str(m.get("url", ""))
            sha = str(m.get("sha256", "")).lower()
            if not re.fullmatch(r"\d+(\.\d+){0,2}", version) or not re.fullmatch(r"[0-9a-f]{64}", sha):
                raise RuntimeError("The update information is invalid.")
            if not _host_allowed(url) and not self._test_url(url):
                raise RuntimeError("The update download address is not trusted.")
            self.last_check = time.time()
            if parse_version(version) > parse_version(VERSION):
                self.latest = {"version": version, "url": url, "sha256": sha,
                               "size": int(m.get("size") or 0), "notes": str(m.get("notes", ""))[:4000]}
                self._set("available")
            else:
                self.latest = None
                self._set("up_to_date")
        except Exception as exc:
            if silent:
                self._busy = False
                return
            self.error = str(exc) if isinstance(exc, RuntimeError) else "Couldn't reach the update server."
            self._set("error")
        finally:
            self._busy = False

    def _test_url(self, url):
        # only when a local manifest override is used for development/testing
        return self.manifest_url.startswith("http://127.0.0.1") and url.startswith("http://127.0.0.1")

    # --------------------------------------------------------------- download
    def download(self):
        if self.state != "available" or not self.latest:
            return
        self.progress = 0.0
        self._set("downloading")
        threading.Thread(target=self._download_worker, daemon=True).start()

    def _download_worker(self):
        target = self.exe_path + ".update"
        try:
            with requests.get(self.latest["url"], headers=HEADERS, stream=True, timeout=20) as r:
                r.raise_for_status()
                if not (_host_allowed(r.url) or self._test_url(r.url)):
                    raise RuntimeError("The download was redirected to an untrusted address.")
                total = int(r.headers.get("Content-Length") or self.latest.get("size") or 0)
                h = hashlib.sha256()
                done = 0
                with open(target, "wb") as fh:
                    for chunk in r.iter_content(256 * 1024):
                        fh.write(chunk)
                        h.update(chunk)
                        done += len(chunk)
                        if total:
                            self.progress = min(1.0, done / total)
                            self.changed.emit()
            if h.hexdigest() != self.latest["sha256"]:
                os.remove(target)
                raise RuntimeError("The download was corrupted (checksum mismatch). Please try again.")
            self._download = target
            self.progress = 1.0
            self._set("ready")
        except PermissionError:
            self.error = ("MNKLyrics can't write to its own folder. Move MNKLyrics.exe to a folder you own "
                          "(e.g. Desktop or Documents) and try again.")
            self._set("error")
        except Exception as exc:
            try:
                os.remove(target)
            except OSError:
                pass
            self.error = str(exc) if isinstance(exc, RuntimeError) else "The download failed. Check your connection."
            self._set("error")

    # ----------------------------------------------------------------- install
    def install_and_restart(self) -> bool:
        """Swap in the downloaded exe and start it. Returns True if the app should now quit."""
        if self.state != "ready" or not self._download:
            return False
        exe, old = self.exe_path, self.exe_path + ".old"
        try:
            if os.path.exists(old):
                os.remove(old)
            os.replace(exe, old)          # renaming a running exe is allowed on Windows
            try:
                os.replace(self._download, exe)
            except OSError:
                os.replace(old, exe)      # put the original back
                raise
            flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            subprocess.Popen([exe, "--updated"], close_fds=True, creationflags=flags,
                             cwd=os.path.dirname(exe))
            return True
        except OSError as exc:
            self.error = f"Couldn't install the update: {exc.strerror or exc}"
            self._set("error")
            return False

    def _set(self, state):
        self.state = state
        self.changed.emit()
