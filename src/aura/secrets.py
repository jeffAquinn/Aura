"""API keys and connection credentials, kept in the macOS Keychain.

A secret is never written to .env, a log, git, the vault or a conversation, and no tool of Aura's
(the agent) can read one: only the server's own code fetches a specific secret, at the moment it
needs it. Only the names below can be stored: this is not a general-purpose password store.

- "keys" (Settings → Keys): API keys the user pastes.
- a connection's credentials (Settings → Connections): the user pastes these too.
- a connection's sign-in tokens: written only by Aura's server, after the user signs in in their
  browser; never accepted from the app, never returned to it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

SERVICE = "local.aura"


@dataclass(frozen=True)
class KeySpec:
    name: str
    label: str
    prefix: str = ""  # what a valid value starts with: catches pasting the wrong thing
    min_length: int = 20
    suffix: str = ""  # ... or ends with
    group: str = "keys"  # "keys" (Settings → Keys) or a connection's id
    user_settable: bool = True  # False: written only by the server (sign-in tokens)
    pattern: str = ""  # a full-match regex, when the shape is known exactly
    example: str = ""  # what it looks like, for the error message
    last4_only: bool = False  # its hint shows only the last 4 characters (no fixed prefix to show)


KEYS = {
    k.name: k
    for k in (
        KeySpec("anthropic_api_key", "Anthropic API key (Claude Code, the Developer)", "sk-ant-"),
        KeySpec("tavily_api_key", "Tavily API key (web search)", "tvly-"),
        KeySpec("google_client_id", "Google OAuth client ID", suffix=".apps.googleusercontent.com", min_length=40,
                group="google"),
        KeySpec("google_client_secret", "Google OAuth client secret", "GOCSPX-", group="google"),
        KeySpec("google_tokens", "Google sign-in", group="google", user_settable=False),
        KeySpec("slack_client_id", "Slack app Client ID", min_length=10, group="slack", pattern=r"\d{6,20}\.\d{6,20}",
                example="1234567890123.1234567890123"),
        KeySpec("slack_tokens", "Slack sign-in", group="slack", user_settable=False),
        KeySpec("slack_app", "Slack app's token rotation", group="slack", user_settable=False),  # not secret: kept with its app
        # Live messages (Socket Mode): the app's Aura bot user, and the app-level token that opens
        # the socket. Their own group: they're set on the Live messages card, not with the Client ID.
        KeySpec("slack_bot_token", "Slack bot token", "xoxb-", min_length=40, group="slack_live"),
        KeySpec("slack_app_token", "Slack app-level token", min_length=40, group="slack_live",
                pattern=r"(xoxe\.)?xapp-[A-Za-z0-9-]+", example="xapp-1-… (or xoxe.xapp-1-… when token rotation is on)"),
        # With app-level token rotation on (it can't be turned off again), the token lasts 12 hours and
        # is renewed with its single-use refresh token and the app's Client Secret.
        KeySpec("slack_app_refresh", "Slack app-level refresh token", min_length=20, group="slack_live",
                pattern=r"xoxe-[A-Za-z0-9-]+", example="xoxe-1-…", last4_only=True),
        KeySpec("slack_client_secret", "Slack app Client Secret", min_length=20, group="slack_live",
                pattern=r"[A-Za-z0-9]{20,64}", example="the Client Secret under App Credentials (letters and digits)",
                last4_only=True),
        KeySpec("slack_app_rotation", "Slack app-level token renewal", group="slack_live", user_settable=False),
        KeySpec("slack_bot", "Slack bot details", group="slack_live", user_settable=False),
        KeySpec("discord_bot_token", "Discord bot token", min_length=50, group="discord",
                pattern=r"[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{20,}",
                example="three parts joined by dots (MTA1…·Gh7…·kX9…)"),
        KeySpec("discord_profile", "Discord bot details", group="discord", user_settable=False),
    )
}


class SecretError(ValueError):
    pass


class Secrets:
    """A thin, testable wrapper over a keyring backend (the macOS Keychain in production)."""

    def __init__(self, backend: Any = None, audit_record: Callable[..., None] = lambda *a, **k: None):
        if backend is None:
            import keyring

            backend = keyring.get_keyring()
        self.backend = backend
        self.record = audit_record

    def get(self, name: str) -> str | None:
        self._spec(name)
        try:
            return self.backend.get_password(SERVICE, name) or None
        except Exception:  # noqa: BLE001: a locked or unavailable Keychain means "not set"
            return None

    def set(self, name: str, value: str) -> None:
        """A value the user pasted (an API key, a connection's credential): checked first."""
        value = self.spec_check(name, value)
        self.backend.set_password(SERVICE, name, value)
        self.record("secret_set", name=name)  # the name only, never the value

    def spec_check(self, name: str, value: str) -> str:
        """Whether a pasted value has the right shape (without storing it); returns it trimmed."""
        spec = self._spec(name)
        if not spec.user_settable:
            raise SecretError(f"{spec.label} is written only by Aura's sign-in, not by hand.")
        value = value.strip()
        if (not value.startswith(spec.prefix) or not value.endswith(spec.suffix) or len(value) < spec.min_length
                or any(c.isspace() for c in value) or (spec.pattern and not re.fullmatch(spec.pattern, value))):  # fmt: skip
            what = spec.label.split(" (")[0]
            article = "an" if what[0].lower() in "aeiou" else "a"
            should = (f"look like {spec.example}" if spec.example else f"start with '{spec.prefix}'" if spec.prefix
                      else f"end with '{spec.suffix}'")  # fmt: skip
            raise SecretError(f"That doesn't look like {article} {what} (it should {should}).")
        return value

    def store(self, name: str, value: str) -> None:
        """A value only the server produces (sign-in tokens, as JSON)."""
        spec = self._spec(name)
        if spec.user_settable:
            raise SecretError(f"use set() for {spec.label}")
        self.backend.set_password(SERVICE, name, value)
        self.record("secret_stored", name=name)

    def delete(self, name: str) -> bool:
        self._spec(name)
        try:
            self.backend.delete_password(SERVICE, name)
        except Exception:  # noqa: BLE001: nothing to delete
            return False
        self.record("secret_deleted", name=name)
        return True

    def listing(self, group: str = "keys") -> list[dict[str, Any]]:
        """For Settings: which of a group's secrets are set, with a masked hint. Never the value,
        and never a hint of a sign-in token."""
        out = []
        for spec in KEYS.values():
            if spec.group != group or not spec.user_settable:
                continue
            value = self.get(spec.name)
            out.append({"name": spec.name, "label": spec.label, "set": value is not None,
                        "hint": self.hint(spec, value) if value else None})  # fmt: skip
        return out

    @staticmethod
    def hint(spec: KeySpec, value: str) -> str:
        if spec.last4_only:
            return f"…{value[-4:]}"
        if spec.prefix:
            return f"{spec.prefix}…{value[-4:]}"
        return f"{value[:6]}…{spec.suffix.lstrip('.')}" if spec.suffix else f"{value[:4]}…{value[-4:]}"

    @staticmethod
    def _spec(name: str) -> KeySpec:
        if name not in KEYS:
            raise SecretError(f"unknown key '{name}'")
        return KEYS[name]
