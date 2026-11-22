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
        self.backend.set_password(SERVICE, name, value)
        self.record("secret_set", name=name)  # the name only, never the value

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
        if spec.prefix:
            return f"{spec.prefix}…{value[-4:]}"
        return f"{value[:6]}…{spec.suffix.lstrip('.')}" if spec.suffix else f"{value[:4]}…{value[-4:]}"

    @staticmethod
    def _spec(name: str) -> KeySpec:
        if name not in KEYS:
            raise SecretError(f"unknown key '{name}'")
        return KEYS[name]
