"""Signing in to a service in the user's own browser (OAuth 2.0 for native apps, RFC 8252).

Aura never sees the user's password. The flow:
1. A one-shot listener starts on 127.0.0.1, on a random free port, for this sign-in only.
2. The user's browser opens the service's sign-in page. The request carries a random `state` and
   a PKCE challenge (RFC 7636, S256), so only this sign-in, on this Mac, can redeem the code.
3. The service redirects the browser back to the listener with a one-time code. The listener
   accepts exactly one well-formed answer (the right `state`, and the right issuer when the
   service names one: RFC 9207), hands the code to the connection, and shuts down. Anything
   else is refused. It also shuts down after `timeout` seconds.

Nothing here logs a URL, a code or a token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CALLBACK = "/callback"


class OAuthError(Exception):
    pass


def pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge)."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class LoopbackSignIn:
    """One browser sign-in. `on_code(code, verifier, redirect_uri)` redeems the code; whatever it
    raises becomes this sign-in's error (shown to the user, never the code itself)."""

    def __init__(self, on_code: Callable[[str, str, str], None], issuer: str | None = None, timeout: float = 300):
        self.on_code = on_code
        self.issuer = issuer
        self.timeout = timeout
        self.state = secrets.token_urlsafe(32)
        self.verifier, self.challenge = pkce_pair()
        self.done = threading.Event()
        self.error: str | None = None
        self.redirect_uri = ""
        self._server: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()
        self._answered = False

    def start(self) -> str:
        """Listen on 127.0.0.1 (never another interface); returns the redirect URI."""
        flow = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 (http.server's naming)
                status, title, text = flow._answer(self.path)
                body = _PAGE.format(title=html.escape(title), text=html.escape(text)).encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:  # the request line holds the code: never log it
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.redirect_uri = f"http://127.0.0.1:{self._server.server_port}{CALLBACK}"
        threading.Thread(target=self._server.serve_forever, daemon=True, name="oauth-loopback").start()
        threading.Thread(target=self._expire, daemon=True, name="oauth-timeout").start()
        return self.redirect_uri

    def cancel(self, reason: str = "Sign-in cancelled.") -> None:
        with self._lock:
            if not self._answered:
                self._answered = True
                self.error = reason
        self._close()

    def _expire(self) -> None:
        if not self.done.wait(self.timeout):
            self.cancel(f"Sign-in timed out after {int(self.timeout // 60)} minutes. Try again.")

    def _answer(self, path: str) -> tuple[int, str, str]:
        url = urlparse(path)
        if url.path != CALLBACK:
            return 404, "Not found", "This address only accepts Aura's sign-in."
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        with self._lock:
            if self._answered:
                return 410, "Already used", "This sign-in link has already been used. Start again from Aura."
            if not hmac.compare_digest(query.get("state", ""), self.state):
                # A stray or forged request: refuse it, but keep waiting for the real answer.
                return 400, "Not this sign-in", "This request doesn't match the sign-in Aura started."
            self._answered = True
        try:
            if "error" in query:
                raise OAuthError("You declined, so nothing was connected." if query["error"] == "access_denied"
                                 else f"The sign-in failed ({query['error']}).")  # fmt: skip
            if self.issuer and "iss" in query and query["iss"] != self.issuer:
                raise OAuthError("The answer came from an unexpected issuer, so it was refused.")
            if not query.get("code"):
                raise OAuthError("The answer had no sign-in code.")
            self.on_code(query["code"], self.verifier, self.redirect_uri)
            return 200, "Aura is connected", "You can close this tab and return to Aura."
        except Exception as e:  # noqa: BLE001: any failure is shown, never the code
            self.error = str(e) if isinstance(e, OAuthError) else f"The sign-in couldn't finish: {type(e).__name__}"
            return 400, "Not connected", self.error
        finally:
            threading.Thread(target=self._close, daemon=True).start()  # after this response is sent

    def _close(self) -> None:
        self.done.set()
        server, self._server = self._server, None
        if server is not None:
            time.sleep(0.2)  # let the last response go out
            server.shutdown()
            server.server_close()


_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Aura: {title}</title>
<style>body{{font:16px -apple-system,system-ui,sans-serif;display:grid;place-items:center;height:90vh;
color:#222;background:#f4f2fb}}main{{text-align:center;max-width:28em}}h1{{color:#7C4DFF}}</style></head>
<body><main><h1>{title}</h1><p>{text}</p></main></body></html>"""
