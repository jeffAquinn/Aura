"""Connections (Sprint 7): Aura's links to the user's accounts (Google; Slack and Discord next).

So far: the credentials (in the Keychain), the sign-in and a live check, all from Settings →
Connections. No tool of Aura's (the agent) can reach a connection or its secrets; tools arrive
later, behind the Permissions screen (Gemma's floors and taint rule). The Developers (Claude
Code, Codex) never get a connection.
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
