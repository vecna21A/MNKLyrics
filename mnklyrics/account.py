"""Sign in with Discord — only needed to share lyrics with the community.

Uses the community database's login (Supabase Auth) with Discord as the provider, in the standard way for
desktop apps (OAuth "PKCE"): the browser opens Discord's own "Authorize" page, Discord sends the browser
back to a one-time page on this PC (127.0.0.1), and the app swaps the one-time code for a session. The
app never sees a password. The session is stored encrypted for this Windows user (DPAPI).

Every shared lyric is stamped with the sharer's Discord username by the database itself, so names are
unique and can't be faked.
"""

import base64
import ctypes
import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from PySide6.QtCore import QObject, Signal

from .config import APP_NAME, COMMUNITY_KEY, COMMUNITY_URL, app_dir

PORTS = (47823, 47824, 47825)  # must be listed in Supabase > Authentication > URL Configuration
SIGN_IN_TIMEOUT = 180
_ENTROPY = b"MNKLyrics-community-session-v1"


# ------------------------------------------------------------------ DPAPI
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _crypt(data: bytes, protect: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    ebuf = ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
    entropy = _Blob(len(_ENTROPY), ctypes.cast(ebuf, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, ctypes.byref(entropy), None, None, 0x01, ctypes.byref(out)):
        raise OSError("Windows couldn't protect the sign-in.")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


# ------------------------------------------------------------------ helpers
def _auth_url(path):
    return f"{COMMUNITY_URL.rstrip('/')}/auth/v1/{path}"


def _headers():
    h = {"apikey": COMMUNITY_KEY, "Content-Type": "application/json", "User-Agent": APP_NAME}
    if COMMUNITY_KEY.startswith("eyJ"):
        h["Authorization"] = f"Bearer {COMMUNITY_KEY}"
    return h


def discord_names(user: dict):
    """(unique username, display name) from the login's Discord profile."""
    meta = (user or {}).get("user_metadata") or {}
    username = (meta.get("full_name") or (meta.get("name") or "").split("#")[0] or "").strip()
    display = ((meta.get("custom_claims") or {}).get("global_name") or "").strip() or username
    return username, display


_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>MNKLyrics</title><style>
body{{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#0d0d14;
color:#eee;font-family:Segoe UI,Arial,sans-serif}} .c{{text-align:center;padding:40px 48px;border-radius:18px;
background:#171723;border:1px solid #2b2b3d}} h1{{margin:0 0 8px;font-size:22px;color:{color}}}
p{{margin:0;color:#aaa}}</style></head><body><div class="c"><h1>{title}</h1><p>{text}</p></div></body></html>"""


class _Callback(BaseHTTPRequestHandler):
    result = None

    def log_message(self, *a):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        if url.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        q = parse_qs(url.query)
        type(self).result = {k: v[0] for k, v in q.items()}
        ok = "code" in q
        page = _PAGE.format(color="#3ddc84" if ok else "#ff6b6b",
                            title="You're signed in ✓" if ok else "Sign-in didn't work",
                            text="You can close this tab and go back to MNKLyrics." if ok
                            else "Close this tab and try again in MNKLyrics.")
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class DiscordAccount(QObject):
    changed = Signal()
    sign_in_done = Signal(bool, str)   # ok, message

    def __init__(self):
        super().__init__()
        self.path = os.path.join(app_dir(), "community_session.bin")
        self.user = None
        self.refresh_token = ""
        self._access = ""
        self._expires = 0.0
        self._lock = threading.Lock()
        self._busy = False
        try:
            with open(self.path, "rb") as fh:
                data = json.loads(_crypt(fh.read(), False).decode("utf-8"))
            self.user = data.get("user")
            self.refresh_token = data.get("refresh_token", "")
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------- state
    @property
    def signed_in(self) -> bool:
        return bool(self.refresh_token and self.user)

    @property
    def username(self) -> str:
        return discord_names(self.user)[0]

    @property
    def display(self) -> str:
        return discord_names(self.user)[1]

    @property
    def user_id(self) -> str:
        return (self.user or {}).get("id", "")

    def _store(self, session):
        self.user = session.get("user") or self.user
        self.refresh_token = session.get("refresh_token") or self.refresh_token
        self._access = session.get("access_token", "")
        self._expires = time.time() + float(session.get("expires_in") or 3600) - 60
        try:
            blob = _crypt(json.dumps({"user": self.user, "refresh_token": self.refresh_token}).encode("utf-8"), True)
            with open(self.path, "wb") as fh:
                fh.write(blob)
        except OSError:
            pass

    def sign_out(self):
        token = self._access
        self.user, self.refresh_token, self._access, self._expires = None, "", "", 0.0
        try:
            os.remove(self.path)
        except OSError:
            pass
        if token:
            threading.Thread(target=lambda: requests.post(_auth_url("logout"), timeout=8, headers={
                **_headers(), "Authorization": f"Bearer {token}"}), daemon=True).start()
        self.changed.emit()

    # ------------------------------------------------------------- sign in
    def sign_in(self):
        """Open Discord's authorize page in the browser; answers on sign_in_done."""
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._sign_in, daemon=True, name="discord-sign-in").start()

    def _sign_in(self):
        try:
            session = self._run_browser_flow()
            self._store(session)
            self.sign_in_done.emit(True, f"Signed in as @{self.username}")
            self.changed.emit()
        except RuntimeError as e:
            self.sign_in_done.emit(False, str(e))
        finally:
            self._busy = False

    def _run_browser_flow(self):
        server = None
        for port in PORTS:
            try:
                server = HTTPServer(("127.0.0.1", port), _Callback)
                break
            except OSError:
                continue
        if server is None:
            raise RuntimeError("Couldn't start the sign-in (ports are busy). Restart MNKLyrics and try again.")
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        redirect = f"http://127.0.0.1:{server.server_port}/callback"
        url = _auth_url("authorize") + "?" + urlencode({
            "provider": "discord", "redirect_to": redirect, "code_challenge": challenge,
            "code_challenge_method": "s256", "scopes": "identify"})
        _Callback.result = None
        server.timeout = 1.0
        try:
            webbrowser.open(url)
            end = time.time() + SIGN_IN_TIMEOUT
            while _Callback.result is None and time.time() < end:
                server.handle_request()
        finally:
            server.server_close()
        res = _Callback.result
        if res is None:
            raise RuntimeError("Sign-in timed out. Try again and press Authorize in the browser.")
        if "code" not in res:
            msg = res.get("error_description") or res.get("error") or "Sign-in was cancelled."
            raise RuntimeError(f"Sign-in didn't work: {msg.replace('+', ' ')}")
        try:
            r = requests.post(_auth_url("token") + "?grant_type=pkce", headers=_headers(), timeout=15,
                              json={"auth_code": res["code"], "code_verifier": verifier})
        except requests.RequestException:
            raise RuntimeError("Couldn't finish signing in. Check your internet and try again.")
        if not r.ok:
            raise RuntimeError(f"Couldn't finish signing in (error {r.status_code}). Try again.")
        return r.json()

    # ------------------------------------------------------------- tokens
    def access_token(self) -> str:
        """A fresh token for the database (refreshed when needed). Raises RuntimeError if signed out."""
        with self._lock:
            if self._access and time.time() < self._expires:
                return self._access
            if not self.refresh_token:
                raise RuntimeError("Sign in with Discord to share lyrics.")
            try:
                r = requests.post(_auth_url("token") + "?grant_type=refresh_token", headers=_headers(), timeout=15,
                                  json={"refresh_token": self.refresh_token})
            except requests.RequestException:
                raise RuntimeError("Couldn't reach the community library. Check your internet.")
            if r.status_code in (400, 401, 403):
                self.user, self.refresh_token = None, ""
                try:
                    os.remove(self.path)
                except OSError:
                    pass
                self.changed.emit()
                raise RuntimeError("Your Discord sign-in expired. Sign in again to share.")
            if not r.ok:
                raise RuntimeError(f"Couldn't check your sign-in (error {r.status_code}). Try again later.")
            self._store(r.json())
            return self._access
