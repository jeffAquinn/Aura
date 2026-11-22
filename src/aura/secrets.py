"""API keys, kept in the macOS Keychain (the first piece of the Librarian's Secret Vault).

A key is never written to .env, a log, git, the vault or a conversation. The server reads it only
when it starts the process that needs it, and hands it to that process alone. Only the names
below can be stored: this is not a general-purpose password store.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

SERVICE = "local.aura"


@dataclass(frozen=True)
class KeySpec:
    name: str
    label: str
    prefix: str  # what a valid key starts with: catches pasting the wrong thing
    min_length: int = 20


KEYS = {
    k.name: k
    for k in (
        KeySpec("anthropic_api_key", "Anthropic API key (Claude Code, the Developer)", "sk-ant-"),
        KeySpec("tavily_api_key", "Tavily API key (web search)", "tvly-"),
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
        spec = self._spec(name)
        value = value.strip()
        if not value.startswith(spec.prefix) or len(value) < spec.min_length or any(c.isspace() for c in value):
            what = spec.label.split(" (")[0]
            article = "an" if what[0].lower() in "aeiou" else "a"
            raise SecretError(f"That doesn't look like {article} {what} (it should start with '{spec.prefix}').")
        self.backend.set_password(SERVICE, name, value)
        self.record("secret_set", name=name)  # the name only, never the value

    def delete(self, name: str) -> bool:
        self._spec(name)
        try:
            self.backend.delete_password(SERVICE, name)
        except Exception:  # noqa: BLE001: nothing to delete
            return False
        self.record("secret_deleted", name=name)
        return True

    def listing(self) -> list[dict[str, Any]]:
        """For Settings: which keys are set, with a masked hint. Never the value."""
        out = []
        for spec in KEYS.values():
            value = self.get(spec.name)
            out.append({"name": spec.name, "label": spec.label, "set": value is not None,
                        "hint": f"{spec.prefix}…{value[-4:]}" if value else None})  # fmt: skip
        return out

    @staticmethod
    def _spec(name: str) -> KeySpec:
        if name not in KEYS:
            raise SecretError(f"unknown key '{name}'")
        return KEYS[name]
