"""The Slack connection: the user's own internal Slack app, acting as the user (a user token).

Slack's rules (docs.slack.dev, read 2026-11-22):
- A redirect must be HTTPS, except that an app with PKCE turned on may use http://localhost:<port>
  as a desktop app. It must match the app's registered Redirect URL, port included. So Aura listens
  on one fixed port and the manifest registers exactly that address.
- With PKCE, the token exchange carries no client secret. With token rotation off, the user token
  doesn't expire, so Aura never needs the app's client secret at all: only its Client ID.
- Slack's MCP server accepts only internal (or Marketplace) apps, so the user's app stays internal
  to their workspace.

The user creates the app from the manifest below, in one paste. Aura asks only for the scopes of
the services the user picks; sending a message will always ask first (Gemma's floors).
"""

from __future__ import annotations

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

AUTH_URL = "https://slack.com/oauth/v2/authorize"
API = "https://slack.com/api/"
PORT = 47615  # fixed: Slack matches the redirect exactly, port included
REDIRECT = f"http://localhost:{PORT}/slack/callback"


@dataclass(frozen=True)
class Service:
    id: str
    label: str
    purpose: str
    scopes: tuple[str, ...]


SERVICES = {
    s.id: s
    for s in (
        Service("read", "Read messages", "Read and search your channels, private channels and direct messages.",
                ("channels:read", "channels:history", "groups:read", "groups:history", "im:read", "im:history",
                 "mpim:read", "mpim:history", "users:read", "search:read")),
        Service("send", "Send messages", "Post messages as you. Aura always shows you the exact message and asks first.",
                ("chat:write",)),
    )
}  # fmt: skip


def manifest() -> str:
    """The Slack app the user creates (api.slack.com/apps → Create New App → From a manifest)."""
    return json.dumps({
        "display_information": {"name": "Aura", "description": "Aura, the assistant on your Mac.",
                                "background_color": "#7c4dff"},
        "oauth_config": {"redirect_urls": [REDIRECT], "pkce_enabled": True,
                         "scopes": {"user": [scope for s in SERVICES.values() for scope in s.scopes]}},
        "settings": {"org_deploy_enabled": False, "socket_mode_enabled": False, "token_rotation_enabled": False},
    }, indent=2)  # fmt: skip


class SlackConnection:
    id = "slack"
    label = "Slack"

    def __init__(self, secrets: Secrets, audit: AuditLog, http: httpx.Client | None = None,
                 clock: Callable[[], float] = time.time, timeout: float = 300, port: int = PORT):  # fmt: skip
        self.secrets = secrets
        self.audit = audit
        self.http = http or httpx.Client(timeout=20)
        self.clock = clock
        self.timeout = timeout
        self.port = port
        self._signin: LoopbackSignIn | None = None
        self._wanted: tuple[str, ...] = ()
        self._checks: dict[str, dict[str, Any]] = {}
        self._problem: str | None = None
        self._lock = threading.RLock()

    # ── what the app shows ──────────────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        tokens = self._tokens()
        signing_in = self._signin is not None and not self._signin.done.is_set()
        state = ("signing_in" if signing_in else "connected" if tokens else "ready" if self.configured
                 else "not_set_up")  # fmt: skip
        granted = set((tokens or {}).get("scopes", "").split(","))
        return {
            "id": self.id, "label": self.label, "state": state, "configured": self.configured,
            "account": (tokens or {}).get("account"),
            "services": [{"id": s.id, "label": s.label, "purpose": s.purpose,
                          "granted": bool(tokens) and set(s.scopes) <= granted} for s in SERVICES.values()],
            "credentials": self.secrets.listing(self.id), "problem": self._current_problem(),
            "checks": list(self._checks.values()),
            "setup": {"redirect_url": f"http://localhost:{self.port}/slack/callback", "manifest": manifest()},
        }  # fmt: skip

    @property
    def configured(self) -> bool:
        return bool(self.secrets.get("slack_client_id"))

    def _current_problem(self) -> str | None:
        signin = self._signin
        if signin is not None and signin.done.is_set() and signin.error:
            return signin.error
        return self._problem

    # ── credentials: the app's Client ID only (PKCE replaces the secret) ────────────────────

    def set_credentials(self, client_id: str, client_secret: str = "") -> None:
        self.secrets.set("slack_client_id", client_id)  # validated: raises SecretError
        self._problem = None
        self.audit.record("connection_credentials_set", connection=self.id)

    def remove_credentials(self) -> None:
        self.disconnect()
        self.secrets.delete("slack_client_id")
        self.audit.record("connection_credentials_removed", connection=self.id)

    # ── sign-in ─────────────────────────────────────────────────────────────────────────────

    def begin(self, services: list[str]) -> str:
        if not self.configured:
            raise ConnectionsError("Add your Slack app's Client ID first.")
        wanted = tuple(s for s in SERVICES if s in services)
        if not wanted or len(wanted) != len(set(services)):
            raise ConnectionsError(f"Choose at least one of: {', '.join(SERVICES)}.")
        with self._lock:
            if self._signin is not None:
                self._signin.cancel("Replaced by a new sign-in.")
            self._wanted, self._problem = wanted, None
            self._signin = LoopbackSignIn(self._redeem, timeout=self.timeout, port=self.port, host="localhost",
                                          path="/slack/callback")  # fmt: skip
            try:
                redirect = self._signin.start()
            except OAuthError as e:
                self._signin = None
                raise ConnectionsError(str(e)) from e
            threading.Thread(target=self._watch, args=(self._signin,), daemon=True).start()
        query = {
            "client_id": self.secrets.get("slack_client_id"), "scope": "",
            "user_scope": ",".join(scope for s in wanted for scope in SERVICES[s].scopes),
            "redirect_uri": redirect, "state": self._signin.state,
            "code_challenge": self._signin.challenge, "code_challenge_method": "S256",
        }  # fmt: skip
        self.audit.record("connection_sign_in_started", connection=self.id, services=list(wanted))
        return f"{AUTH_URL}?{urlencode(query)}"

    def _watch(self, signin: LoopbackSignIn) -> None:
        signin.done.wait()
        if signin.error:
            self.audit.record("connection_sign_in_failed", connection=self.id, reason=signin.error)

    def _redeem(self, code: str, verifier: str, redirect_uri: str) -> None:
        """Code → user token, with the PKCE verifier and no client secret (Slack: "should not
        include client_secret")."""
        r = self.http.post(API + "oauth.v2.access", data={
            "client_id": self.secrets.get("slack_client_id"), "code": code, "code_verifier": verifier,
            "redirect_uri": redirect_uri,
        })  # fmt: skip
        body = _json(r)
        if not body.get("ok"):
            raise OAuthError(f"Slack refused the sign-in: {_explain(body.get('error'))}")
        user = body.get("authed_user") or {}
        token = user.get("access_token") or (body.get("access_token") if body.get("token_type") == "user" else None)
        if not token:
            raise OAuthError("Slack didn't return a user token. Check the app has user scopes, then try again.")
        granted = set(str(user.get("scope") or body.get("scope") or "").split(","))
        who = self._identify(token)
        tokens = {"access_token": token, "scopes": ",".join(sorted(granted)), "user_id": user.get("id"),
                  "team_id": (body.get("team") or {}).get("id"), "account": who}  # fmt: skip
        self.secrets.store("slack_tokens", json.dumps(tokens))
        self._checks = {}
        missing = [SERVICES[s].label for s in self._wanted if not set(SERVICES[s].scopes) <= granted]
        self.audit.record("connection_connected", connection=self.id, account=who,
                          services=[s for s in self._wanted if SERVICES[s].label not in missing])  # fmt: skip
        if missing:
            self._problem = (f"Slack didn't grant {', '.join(missing)}. Add the missing scopes to your app "
                             "(paste Aura's manifest again), then sign in again.")  # fmt: skip

    def _identify(self, token: str) -> str | None:
        """'you in Workspace', from auth.test (needs no scope)."""
        try:
            body = _json(self.http.post(API + "auth.test", headers={"Authorization": f"Bearer {token}"}))
        except httpx.HTTPError:
            return None
        if not body.get("ok"):
            return None
        return f"{body.get('user')} in {body.get('team')}" if body.get("team") else body.get("user")

    # ── using it ────────────────────────────────────────────────────────────────────────────

    def token(self) -> str:
        tokens = self._tokens()
        if not tokens:
            raise ConnectionsError("Slack isn't connected.")
        return str(tokens["access_token"])

    def check(self) -> dict[str, Any]:
        """auth.test for the token, then one harmless read per granted service. Sending can't be
        tested without sending, so it's only confirmed as granted."""
        status = self.status()
        if status["state"] != "connected":
            raise ConnectionsError("Slack isn't connected.")
        token = self.token()
        auth = {"Authorization": f"Bearer {token}"}
        checks: dict[str, dict[str, Any]] = {}
        try:
            alive = _json(self.http.post(API + "auth.test", headers=auth))
        except httpx.HTTPError as e:
            self._problem = f"Couldn't reach Slack ({type(e).__name__})."
            return self.status()
        if not alive.get("ok"):
            if alive.get("error") in ("invalid_auth", "token_revoked", "account_inactive", "not_authed"):
                self.secrets.delete("slack_tokens")
                self.audit.record("connection_expired", connection=self.id)
            self._problem = f"Slack: {_explain(alive.get('error'))}"
            return self.status()
        for s in status["services"]:
            if not s["granted"]:
                continue
            if s["id"] == "read":
                try:
                    body = _json(self.http.get(API + "conversations.list", params={"limit": 1, "types": "public_channel"},
                                               headers=auth))  # fmt: skip
                    ok, detail = bool(body.get("ok")), "Working." if body.get("ok") else _explain(body.get("error"))
                except httpx.HTTPError as e:
                    ok, detail = False, f"Couldn't reach Slack ({type(e).__name__})."
            else:
                ok, detail = True, "Granted. Aura will show you each message and ask before sending."
            checks[s["id"]] = {"service": s["id"], "ok": ok, "detail": detail, "fix_url": None, "at": int(self.clock())}
        self._checks, self._problem = checks, None
        self.audit.record("connection_checked", connection=self.id, results={k: v["ok"] for k, v in checks.items()})
        return self.status()

    def disconnect(self) -> None:
        """Revoke the token at Slack (auth.revoke), then forget it."""
        tokens = self._tokens()
        with self._lock:
            if self._signin is not None:
                self._signin.cancel()
                self._signin = None
        if tokens:
            try:
                self.http.post(API + "auth.revoke", headers={"Authorization": f"Bearer {tokens['access_token']}"})
            except httpx.HTTPError:
                pass  # offline: the local copy is still deleted; the app can be removed in Slack too
            self.secrets.delete("slack_tokens")
            self.audit.record("connection_disconnected", connection=self.id)
        self._checks, self._problem = {}, None

    def _tokens(self) -> dict[str, Any] | None:
        raw = self.secrets.get("slack_tokens")
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None


def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        body = r.json()
        return body if isinstance(body, dict) else {}
    except ValueError:
        return {"ok": False, "error": f"HTTP {r.status_code}"}


_EXPLAINED = {
    "bad_redirect_uri": "the return address doesn't match the app's Redirect URL. Paste Aura's manifest into "
                        "the app again (it registers http://localhost:47615/slack/callback).",
    "invalid_client_id": "that Client ID isn't a Slack app. Copy it from the app's Basic Information page.",
    "invalid_code": "the sign-in code expired. Sign in again.",
    "invalid_scope": "the app doesn't have one of the scopes Aura asked for. Paste Aura's manifest into the app again.",
    "missing_scope": "the app is missing a scope this needs. Paste Aura's manifest into the app again, then sign in again.",
    "invalid_auth": "the sign-in is no longer valid. Sign in again.",
    "token_revoked": "Aura's access was revoked. Sign in again.",
}


def _explain(error: Any) -> str:
    code = str(error or "unknown error")
    return _EXPLAINED.get(code, code)
