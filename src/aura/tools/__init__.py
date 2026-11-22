"""Aura's hands: the toolbox the agent loop is given, and how each tool presents itself."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from langchain_core.tools import BaseTool

from aura.audit import AuditLog
from aura.config import Settings
from aura.events import emit
from aura.identity import IdentityStore
from aura.tools.identity import identity_tools
from aura.tools.sandbox import Sandbox, sandbox_tools
from aura.tools.vault import Vault, vault_tools
from aura.tools.voice import voice_tools
from aura.tools.web import WebSearch, web_tools
from aura.voice import VoiceStore

__all__ = ["NEEDS_APPROVAL", "Toolbox", "emit", "status_line"]

# Tools that change the vault or Aura's persona: each pauses for the user's approval first.
NEEDS_APPROVAL = ("write_file", "create_folder", "delete_path", "update_persona")

GROUP = {
    "list_vault": "File_System_Tool",
    "read_file": "File_System_Tool",
    "write_file": "File_System_Tool",
    "create_folder": "File_System_Tool",
    "delete_path": "File_System_Tool",
    "web_search": "Web_Search_Tool",
    "run_python": "Code_Interpreter_Tool",
    "list_voices": "Voice_Tool",
    "set_voice": "Voice_Tool",
    "view_persona": "Persona_Tool",
    "update_persona": "Persona_Tool",
}

_DOING: dict[str, Callable[[dict], str]] = {
    "list_vault": lambda a: f"Listing '{a.get('path') or '.'}'...",
    "read_file": lambda a: f"Reading '{a.get('path')}'...",
    "write_file": lambda a: f"Writing '{a.get('path')}'...",
    "create_folder": lambda a: f"Creating folder '{a.get('path')}'...",
    "delete_path": lambda a: f"Deleting '{a.get('path')}'...",
    "web_search": lambda a: f"Searching for '{a.get('query')}'...",
    "run_python": lambda a: "Running Python in the sandbox...",
    "list_voices": lambda a: "Looking through my voices...",
    "set_voice": lambda a: "Adjusting my voice...",
    "view_persona": lambda a: "Reviewing my persona...",
    "update_persona": lambda a: f"Updating my {a.get('field', 'persona')}...",
}


def status_line(tool_name: str, args: dict) -> str:
    """e.g. "[Tool: File_System_Tool] Reading 'budget.csv'..." """
    doing = _DOING.get(tool_name, lambda a: "Working...")(args)
    return f"[Tool: {GROUP.get(tool_name, tool_name)}] {doing}"


@dataclass
class Toolbox:
    audit: AuditLog
    vault: Vault
    search: WebSearch
    sandbox: Sandbox
    identity: IdentityStore
    voice: VoiceStore
    tools: list[BaseTool] = field(default_factory=list)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        search: WebSearch | None = None,
        sandbox: Sandbox | None = None,
    ) -> Toolbox:
        audit = AuditLog(settings.audit_log)
        vault = Vault(settings.vault_dir, audit)
        search = search or WebSearch(settings.tavily_api_key, audit)
        sandbox = sandbox or Sandbox(
            settings.sandbox_image, settings.vault_dir, settings.sandbox_timeout, audit
        )
        identity = IdentityStore(settings.data_dir, audit)
        voice = VoiceStore(settings.data_dir, audit)
        tools = [
            *vault_tools(vault),
            *web_tools(search),
            *sandbox_tools(sandbox, emit_status=lambda s: emit({"status": f"[Tool: Code_Interpreter_Tool] {s}"})),
            *voice_tools(voice),
            *identity_tools(identity),
        ]
        return cls(audit, vault, search, sandbox, identity, voice, tools)
