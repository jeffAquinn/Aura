"""Connections (Sprint 7): Aura's links to the user's accounts (Google, Slack, Discord).

Credentials live in the Keychain and are set, signed in and checked from Settings → Connections.
Aura's tools (actions.py) use the connection objects, never the secrets, under Gemma's floors,
taint rule and framing. Live messages (live.py) bring new Slack and Discord messages in as they
arrive. The Developers (Claude Code, Codex) never get a connection.
"""

from __future__ import annotations

from typing import Any


class ConnectionsError(Exception):
    """Why a connection can't do what was asked (shown to the user as is)."""


class Connections:
    """The registry the server's Settings → Connections API uses."""

    def __init__(self, *connections: Any):
        self.all = {c.id: c for c in connections}

    def public(self) -> list[dict[str, Any]]:
        return [c.status() for c in self.all.values()]

    def signature(self) -> tuple:
        """What's connected and granted: when it changes, Aura's tools are rebuilt."""
        return tuple((c["id"], c["state"], tuple(s["id"] for s in c["services"] if s["granted"])) for c in self.public())

    def get(self, connection_id: str) -> Any:
        if connection_id not in self.all:
            raise ConnectionsError(f"no connection called '{connection_id}'")
        return self.all[connection_id]
