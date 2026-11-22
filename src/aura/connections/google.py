"""The Google connection: Gmail, Calendar and Drive, with the user's own OAuth client.

Google's official Workspace MCP servers need membership in the Workspace Developer Preview, which
needs a Workspace account; this user has a personal Gmail account. So (Gemma's decision D2) Aura
uses Google's generally available REST APIs, with the user's own "Desktop app" OAuth client in a
Google Cloud project whose audience is "Personal" (only the creator's account can sign in; no
app verification, no 7-day expiry).

The user chooses the services; Aura asks Google only for those scopes. Gmail is read plus drafts:
Aura has no tool that sends mail (D6), even though Google's compose scope would allow it.

This sprint step: credentials, sign-in and a live check. Aura's tools come later, behind the
Permissions screen; until then nothing the agent does can reach Google.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from aura.audit import AuditLog
from aura.connections import ConnectionsError
from aura.connections.oauth import LoopbackSignIn, OAuthError
from aura.secrets import Secrets

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
ISSUER = "https://accounts.google.com"
SCOPE = "https://www.googleapis.com/auth/"
IDENTITY = ("openid", "email")


@dataclass(frozen=True)
class Service:
    id: str
    label: str
    purpose: str
    scopes: tuple[str, ...]
    check_url: str  # a harmless read that proves access


SERVICES = {
    s.id: s
    for s in (
        Service("gmail", "Gmail", "Read and search your mail, and write drafts. Aura never sends mail.",
                (SCOPE + "gmail.readonly", SCOPE + "gmail.compose"),
                "https://gmail.googleapis.com/gmail/v1/users/me/profile"),
        Service("calendar", "Calendar", "Read your calendars and events, and add or change events.",
                (SCOPE + "calendar.calendarlist.readonly", SCOPE + "calendar.events"),
                "https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=1"),
        Service("drive", "Drive", "Search and read your files, and create new ones.",
                (SCOPE + "drive.readonly", SCOPE + "drive.file"),
                "https://www.googleapis.com/drive/v3/about?fields=user"),
    )
}  # fmt: skip


class GoogleConnection:
    id = "google"
    label = "Google"

    def __init__(self, secrets: Secrets, audit: AuditLog, http: httpx.Client | None = None,
                 clock: Callable[[], float] = time.time, timeout: float = 300):  # fmt: skip
        self.secrets = secrets
        self.audit = audit
        self.http = http or httpx.Client(timeout=20)
        self.clock = clock
        self.timeout = timeout
        self._signin: LoopbackSignIn | None = None
        self._wanted: tuple[str, ...] = ()
        self._checks: dict[str, dict[str, Any]] = {}
        self._problem: str | None = None
        self._lock = threading.RLock()

    # ── what the app shows ──────────────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        tokens = self._tokens()
        signing_in = self._signin is not None and not self._signin.done.is_set()
        if signing_in:
            state = "signing_in"
        elif tokens:
            state = "connected"
        elif self.configured:
            state = "ready"
        else:
            state = "not_set_up"
        granted = set((tokens or {}).get("scopes", "").split())
        return {
            "id": self.id, "label": self.label, "state": state, "configured": self.configured,
            "account": (tokens or {}).get("account"),
            "services": [{"id": s.id, "label": s.label, "purpose": s.purpose,
                          "granted": bool(tokens) and set(s.scopes) <= granted} for s in SERVICES.values()],
            "credentials": self.secrets.listing(self.id),
            "problem": self._current_problem(), "checks": list(self._checks.values()),
        }  # fmt: skip

    def _current_problem(self) -> str | None:
        """A failed sign-in's reason is read from the sign-in itself, the moment it ends."""
        signin = self._signin
        if signin is not None and signin.done.is_set() and signin.error:
            return signin.error
        return self._problem

    @property
    def configured(self) -> bool:
        return bool(self.secrets.get("google_client_id") and self.secrets.get("google_client_secret"))

    # ── credentials (the user's OAuth client) ───────────────────────────────────────────────

    def set_credentials(self, client_id: str, client_secret: str) -> None:
        self.secrets.set("google_client_id", client_id)  # validated: raises SecretError
        self.secrets.set("google_client_secret", client_secret)
        self._problem = None
        self.audit.record("connection_credentials_set", connection=self.id)

    def remove_credentials(self) -> None:
        self.disconnect()
        self.secrets.delete("google_client_id")
        self.secrets.delete("google_client_secret")
        self.audit.record("connection_credentials_removed", connection=self.id)

    # ── sign-in ─────────────────────────────────────────────────────────────────────────────

    def begin(self, services: list[str]) -> str:
        """Start a browser sign-in for these services; returns the URL to open."""
        if not self.configured:
            raise ConnectionsError("Add your Google OAuth client ID and secret first.")
        wanted = tuple(s for s in SERVICES if s in services)
        if not wanted or len(wanted) != len(set(services)):
            raise ConnectionsError(f"Choose at least one of: {', '.join(SERVICES)}.")
        with self._lock:
            if self._signin is not None:
                self._signin.cancel("Replaced by a new sign-in.")
            self._wanted, self._problem = wanted, None
            self._signin = LoopbackSignIn(self._redeem, issuer=ISSUER, timeout=self.timeout)
            redirect = self._signin.start()
            threading.Thread(target=self._watch, args=(self._signin,), daemon=True).start()
        scopes = [*IDENTITY, *(scope for s in wanted for scope in SERVICES[s].scopes)]
        query = {
            "client_id": self.secrets.get("google_client_id"), "redirect_uri": redirect, "response_type": "code",
            "scope": " ".join(scopes), "state": self._signin.state, "code_challenge": self._signin.challenge,
            "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent",
        }  # fmt: skip
        self.audit.record("connection_sign_in_started", connection=self.id, services=list(wanted))
        return f"{AUTH_URL}?{urlencode(query)}"

    def _watch(self, signin: LoopbackSignIn) -> None:
        signin.done.wait()
        if signin.error:
            self.audit.record("connection_sign_in_failed", connection=self.id, reason=signin.error)

    def _redeem(self, code: str, verifier: str, redirect_uri: str) -> None:
        """The browser came back with a code: exchange it (with the PKCE verifier) for tokens."""
        r = self.http.post(TOKEN_URL, data={
            "code": code, "code_verifier": verifier, "redirect_uri": redirect_uri, "grant_type": "authorization_code",
            "client_id": self.secrets.get("google_client_id"), "client_secret": self.secrets.get("google_client_secret"),
        })  # fmt: skip
        if r.status_code != 200:
            raise OAuthError(f"Google refused the sign-in: {_error(r)}")
        body = r.json()
        if not body.get("refresh_token"):
            raise OAuthError("Google didn't grant lasting access (no refresh token). Try again.")
        granted = set(str(body.get("scope", "")).split())
        missing = [SERVICES[s].label for s in self._wanted if not set(SERVICES[s].scopes) <= granted]
        tokens = {
            "refresh_token": body["refresh_token"], "access_token": body.get("access_token", ""),
            "expires_at": self.clock() + float(body.get("expires_in", 0)) - 60, "scopes": " ".join(sorted(granted)),
            "account": _email(body.get("id_token", "")),
        }  # fmt: skip
        self.secrets.store("google_tokens", json.dumps(tokens))
        self._checks = {}
        self.audit.record("connection_connected", connection=self.id, account=tokens["account"],
                          services=[s for s in self._wanted if SERVICES[s].label not in missing])  # fmt: skip
        if missing:  # the user unticked some permissions on Google's consent screen
            self._problem = f"Google didn't grant access to {', '.join(missing)} (unticked on its consent screen)."

    # ── using the connection ────────────────────────────────────────────────────────────────

    def access_token(self) -> str:
        """A current access token, refreshed when it's about to expire."""
        with self._lock:
            tokens = self._tokens()
            if not tokens:
                raise ConnectionsError("Google isn't connected.")
            if tokens.get("access_token") and tokens.get("expires_at", 0) > self.clock():
                return str(tokens["access_token"])
            r = self.http.post(TOKEN_URL, data={
                "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                "client_id": self.secrets.get("google_client_id"), "client_secret": self.secrets.get("google_client_secret"),
            })  # fmt: skip
            if r.status_code != 200:
                if "invalid_grant" in r.text:  # revoked, expired, or the password changed
                    self.secrets.delete("google_tokens")
                    self._problem = "Google's sign-in has expired or was revoked. Sign in again."
                    self.audit.record("connection_expired", connection=self.id)
                raise ConnectionsError(f"Couldn't refresh Google's sign-in: {_error(r)}")
            body = r.json()
            tokens["access_token"] = body["access_token"]
            tokens["expires_at"] = self.clock() + float(body.get("expires_in", 0)) - 60
            self.secrets.store("google_tokens", json.dumps(tokens))
            return str(tokens["access_token"])

    def check(self) -> dict[str, Any]:
        """One harmless read per granted service, to prove the connection works."""
        status = self.status()
        if status["state"] != "connected":
            raise ConnectionsError("Google isn't connected.")
        checks: dict[str, dict[str, Any]] = {}
        try:
            token = self.access_token()
        except ConnectionsError as e:
            self._problem = str(e)
            return self.status()
        for s in status["services"]:
            if not s["granted"]:
                continue
            try:
                r = self.http.get(SERVICES[s["id"]].check_url, headers={"Authorization": f"Bearer {token}"})
                ok = r.status_code == 200
                detail = "Working." if ok else _error(r)
            except httpx.HTTPError as e:
                ok, detail = False, f"Couldn't reach Google ({type(e).__name__})."
            checks[s["id"]] = {"service": s["id"], "ok": ok, "detail": detail, "at": int(self.clock())}
        self._checks, self._problem = checks, None
        self.audit.record("connection_checked", connection=self.id, results={k: v["ok"] for k, v in checks.items()})
        return self.status()

    def disconnect(self) -> None:
        """Revoke Aura's access at Google, then forget the tokens."""
        tokens = self._tokens()
        with self._lock:
            if self._signin is not None:
                self._signin.cancel()
                self._signin = None
        if tokens:
            try:
                self.http.post(REVOKE_URL, data={"token": tokens["refresh_token"]})
            except httpx.HTTPError:
                pass  # offline: the local copy is still deleted; the user can revoke at myaccount.google.com
            self.secrets.delete("google_tokens")
            self.audit.record("connection_disconnected", connection=self.id)
        self._checks, self._problem = {}, None

    def _tokens(self) -> dict[str, Any] | None:
        raw = self.secrets.get("google_tokens")
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None


def _email(id_token: str) -> str | None:
    """The account's address, from the ID token Google's token endpoint just returned over TLS
    (Google: tokens received directly from it needn't be signature-checked)."""
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return str(claims["email"]) if claims.get("iss") in (ISSUER, "accounts.google.com") else None
    except (IndexError, ValueError, KeyError):
        return None


def _error(r: httpx.Response) -> str:
    try:
        body = r.json()
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("status") or r.status_code)[:200]
        return str(body.get("error_description") or err or r.status_code)[:200]
    except ValueError:
        return f"HTTP {r.status_code}"

