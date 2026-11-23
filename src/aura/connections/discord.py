"""The Discord connection: the user's own private bot (Gemma's decision D3).

Discord (docs.discord.com, read 2026-11-22):
- A bot authenticates with its token ("Authorization: Bot <token>"); there's no browser sign-in.
  The token is shown once, on the Developer Portal's Bot page ("Reset Token").
- Automating a normal user account ("self-bots") is against Discord's terms, so Aura accepts only a
  token whose user is a bot.
- Without the privileged Message Content intent, message text comes back empty "across the APIs",
  REST included. Apps in under 100 servers switch it on themselves (Bot page → Privileged Gateway
  Intents); the application's flags show it (1<<18, or 1<<19 for under 100 servers).
- Bots join servers through an invite link (OAuth2, scope "bot") with the permission bits asked for.
  Aura asks for View Channels, Send Messages and Read Message History only.
- Live messages (live.py) use the same token on the Gateway: nothing more to set up.

Gemma: the token is verified with Discord before it is saved. Discord has no API to revoke a bot
token: disconnecting forgets it, and the user resets it in the Developer Portal to kill it.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from aura.audit import AuditLog
from aura.connections import ConnectionsError
from aura.secrets import Secrets

API = "https://discord.com/api/v10/"
USER_AGENT = "DiscordBot (https://localhost/aura, 1.0)"  # Discord asks bots for this form; nothing identifying
MESSAGE_CONTENT = (1 << 18) | (1 << 19)  # the intent, for 100+ servers or fewer
PERMISSIONS = (1 << 10) | (1 << 11) | (1 << 16)  # View Channels, Send Messages, Read Message History = 68608
PORTAL = "https://discord.com/developers/applications"

SERVICES = {
    "read": ("Read messages", "Read the channels and messages in the servers you invite the bot to."),
    "send": ("Send messages", "Post as the bot. Aura always shows you the exact message and asks first."),
}


def invite_url(app_id: str) -> str:
    return f"https://discord.com/oauth2/authorize?client_id={app_id}&scope=bot&permissions={PERMISSIONS}"


class DiscordConnection:
    id = "discord"
    label = "Discord"

    def __init__(self, secrets: Secrets, audit: AuditLog, http: httpx.Client | None = None,
                 clock: Callable[[], float] = time.time):  # fmt: skip
        self.secrets = secrets
        self.audit = audit
        self.http = http or httpx.Client(timeout=20)
        self.clock = clock
        self._checks: dict[str, dict[str, Any]] = {}
        self._problem: str | None = None
        self._lock = threading.RLock()
        self.listener: Any = None  # live.DiscordGateway, when the server listens

    # ── what the app shows ──────────────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        profile = self._profile()
        connected = bool(profile and self.secrets.get("discord_bot_token"))
        return {
            "id": self.id, "label": self.label, "state": "connected" if connected else "not_set_up",
            "configured": connected, "account": profile.get("bot") if connected else None,
            "services": [{"id": k, "label": label, "purpose": purpose, "granted": connected and self._granted(k, profile)}
                         for k, (label, purpose) in SERVICES.items()],
            "credentials": self.secrets.listing(self.id), "problem": self._problem,
            "checks": list(self._checks.values()),
            "setup": {"invite_url": invite_url(profile["app_id"]) if connected else None,
                      "portal_url": f"{PORTAL}/{profile['app_id']}/bot" if connected else PORTAL},
            "live": {**(self.listener.status() if self.listener else {"state": "off", "detail": ""}),
                     "configured": connected, "credentials": []},
        }  # fmt: skip

    @staticmethod
    def _granted(service: str, profile: dict[str, Any]) -> bool:
        if service == "read":
            return bool(profile.get("flags", 0) & MESSAGE_CONTENT)
        return bool(profile.get("servers"))

    # ── the bot token: verified with Discord, then kept in the Keychain ─────────────────────

    def set_credentials(self, token: str, _unused: str = "") -> None:
        token = token.strip()
        if token.lower().startswith("bot "):
            token = token[4:].strip()  # pasted with the header's prefix
        self.secrets.spec_check("discord_bot_token", token)  # its shape, before anything is sent
        try:
            profile = self._verify(token)  # raises: nothing is saved unless Discord accepts it
        except httpx.HTTPError as e:
            raise ConnectionsError(f"Couldn't reach Discord to check the token ({type(e).__name__}). Try again.") from e
        with self._lock:
            self.secrets.set("discord_bot_token", token)
            self.secrets.store("discord_profile", json.dumps(profile))
            self._problem, self._checks = None, {}
        self.audit.record("connection_credentials_set", connection=self.id)
        self.audit.record("connection_connected", connection=self.id, account=profile["bot"],
                          servers=len(profile["servers"]))  # fmt: skip

    def _verify(self, token: str) -> dict[str, Any]:
        """Who the token belongs to, its application, and the servers it's in."""
        me = self._get(token, "users/@me")
        if me.status_code == 401:
            raise ConnectionsError("Discord says this token isn't valid. On the Bot page, use Reset Token and paste the new one.")
        if me.status_code != 200:
            raise ConnectionsError(f"Discord didn't accept the token (HTTP {me.status_code}). Try again in a minute.")
        user = me.json()
        if not user.get("bot"):
            raise ConnectionsError("That's a personal account's token, not a bot's. Discord forbids automating user "
                                   "accounts; create a bot (step 1) and use its token.")  # fmt: skip
        app = self._get(token, "oauth2/applications/@me")
        guilds = self._get(token, "users/@me/guilds")
        app_body = app.json() if app.status_code == 200 else {}
        return {
            "bot": str(user.get("username") or "your bot"), "bot_id": str(user.get("id")),
            "app_id": str(app_body.get("id") or user.get("id")), "flags": int(app_body.get("flags") or 0),
            "public": bool(app_body.get("bot_public")),
            "servers": [str(g.get("name")) for g in (guilds.json() if guilds.status_code == 200 else [])][:50],
        }  # fmt: skip

    def remove_credentials(self) -> None:
        self.disconnect()

    def begin(self, services: list[str]) -> str:
        raise ConnectionsError("Discord uses your bot's token: there's no sign-in. Paste the token to connect.")

    # ── using it ────────────────────────────────────────────────────────────────────────────

    def check(self) -> dict[str, Any]:
        token = self.secrets.get("discord_bot_token")
        if not token or not self._profile():
            raise ConnectionsError("Discord isn't connected.")
        try:
            profile = self._verify(token)
        except ConnectionsError as e:
            self._problem = str(e)
            return self.status()
        except httpx.HTTPError as e:
            self._problem = f"Couldn't reach Discord ({type(e).__name__})."
            return self.status()
        self.secrets.store("discord_profile", json.dumps(profile))
        bot_page = f"{PORTAL}/{profile['app_id']}/bot"
        servers = profile["servers"]
        checks = {
            "read": {"service": "read", "ok": self._granted("read", profile), "at": int(self.clock()),
                     "detail": "Message Content intent is on." if self._granted("read", profile) else
                               "Message Content intent is off, so Discord hides message text from the bot. Turn it on "
                               "under Privileged Gateway Intents, then check again.",
                     "fix_url": None if self._granted("read", profile) else bot_page,
                     "fix_label": "Open the Bot page"},
            "send": {"service": "send", "ok": bool(servers), "at": int(self.clock()),
                     "detail": (f"In {len(servers)} server{'s' if len(servers) != 1 else ''}: {', '.join(servers[:5])}. "
                                "Aura will ask before every message.") if servers else
                               "The bot isn't in any server yet. Invite it to yours, then check again.",
                     "fix_url": None if servers else invite_url(profile["app_id"]), "fix_label": "Invite the bot"},
        }  # fmt: skip
        if profile["public"]:
            self._problem = ("Your bot is Public: anyone with its invite link could add it to their server. "
                             "Turn off Public Bot on the Bot page.")  # fmt: skip
        else:
            self._problem = None
        self._checks = checks
        self.audit.record("connection_checked", connection=self.id, results={k: v["ok"] for k, v in checks.items()})
        return self.status()

    def disconnect(self) -> None:
        """Forget the token. (Discord has no API to revoke a bot token: Reset Token in the
        Developer Portal kills it.)"""
        had = self.secrets.delete("discord_bot_token")
        self.secrets.delete("discord_profile")
        self._checks = {}
        self._problem = ("Aura has forgotten the bot token. To make sure no one else can use it, use Reset Token "
                         "on the bot's page in the Developer Portal.") if had else None  # fmt: skip
        if had:
            self.audit.record("connection_disconnected", connection=self.id)

    def _get(self, token: str, path: str) -> httpx.Response:
        return self.http.get(API + path, headers={"Authorization": f"Bot {token}", "User-Agent": USER_AGENT})

    def _profile(self) -> dict[str, Any] | None:
        raw = self.secrets.get("discord_profile")
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None
