"""Persona_Tool: Aura's Expression layer, changed one field at a time with the user's approval."""

from __future__ import annotations

import json
from typing import Literal

from langchain_core.tools import BaseTool, tool

from aura.events import emit
from aura.identity import IdentityStore, PersonaError

Field = Literal["tone", "brevity", "formality", "style", "constraints", "backstory"]
Change = Literal["replace", "add", "remove"]


def identity_tools(store: IdentityStore) -> list[BaseTool]:
    @tool
    def view_persona() -> str:
        """Show your current expression settings (persona): name, tone, brevity, formality,
        style, constraints and backstory. Your constitution and core rules are permanent and not
        part of the persona."""
        return json.dumps(store.load().public(), ensure_ascii=False, indent=1)

    @tool
    def update_persona(field: Field, change: Change, value: str, reason: str) -> str:
        """Change ONE field of your persona, only when the user asks you directly. Text fields
        (tone, brevity, formality, backstory) use change='replace'. List fields (style,
        constraints) use 'add' or 'remove' for one item, or 'replace' to set a single item.
        Give a short reason in the user's terms. The user approves the change before it applies;
        your constitution, core rules and name cannot be changed."""
        try:
            persona = store.apply(field, change, value, reason, by="aura")
        except PersonaError as e:
            return f"Not changed: {e}."
        emit({"persona": {"name": persona.name}})
        return f"Persona updated: {field} is now {json.dumps(persona.public()[field], ensure_ascii=False)}."

    return [view_persona, update_persona]
