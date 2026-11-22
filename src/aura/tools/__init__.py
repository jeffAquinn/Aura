"""Aura's hands: the toolbox the agent loop is given, and how each tool presents itself."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from langchain_core.tools import BaseTool

from aura.connections import Connections
from aura.connections.google import GoogleConnection
from aura.audit import AuditLog
from aura.config import Settings
from aura.events import emit
from aura.identity import IdentityStore
from aura.tools.identity import identity_tools
from aura.tools.sandbox import Sandbox, sandbox_tools
from aura.tools.vault import Vault, vault_tools
from aura.tools.voice import voice_tools
from aura.tools.web import WebSearch, web_tools
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # imported lazily in from_settings (they import this package themselves)
    from aura.developer.workflow import Developer
    from aura.governor.continuous import ContinuousAudit
    from aura.governor.workshop import Workshop
    from aura.secrets import Secrets
    from aura.skills import SkillManager
from aura import speech
from aura.voice import VoiceStore

__all__ = ["NEEDS_APPROVAL", "Toolbox", "emit", "status_line"]

# Tools that run code, change the vault, or change Aura's persona: each pauses for the user's
# approval first (code execution was added by Gemma's Sprint 4 review).
NEEDS_APPROVAL = ("write_file", "create_folder", "delete_path", "update_persona", "run_python", "develop_skill")
# Approvals that may be edited on the card before approving: tool -> the fields the user may change.
EDITABLE: dict[str, tuple[str, ...]] = {"develop_skill": ("specification", "engine")}

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
    "check_library": "Governor",
    "list_skills": "Workshop",
    "propose_skill": "Workshop",
    "develop_skill": "Developer",
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
    "check_library": lambda a: f"Checking '{a.get('name')}' on PyPI and OSV...",
    "list_skills": lambda a: "Looking through my skills...",
    "propose_skill": lambda a: f"Staging the '{a.get('name')}' skill for audit...",
    "develop_skill": lambda a: f"Starting {'Codex' if a.get('engine') == 'codex' else 'Claude Code'} on the '{a.get('name')}' skill...",
}


def status_line(tool_name: str, args: dict) -> str:
    """e.g. "[Tool: File_System_Tool] Reading 'budget.csv'..." """
    if tool_name.startswith("skill_"):  # installed skills: Gemma's spoken status line
        return f"[Tool: Skill_Tool] Running the {tool_name[len('skill_'):]} skill..."
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
    skills: SkillManager
    workshop: Workshop
    continuous: ContinuousAudit
    secrets: Secrets
    developer: Developer
    connections: Connections  # Settings → Connections only: no tool reaches these (yet: Sprint 7)
    tools: list[BaseTool] = field(default_factory=list)

    def all_tools(self) -> list[BaseTool]:
        """The built-in tools plus every approved, enabled skill (re-read on each rebuild)."""
        return [*self.tools, *self.skills.tools()]

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        search: WebSearch | None = None,
        sandbox: Sandbox | None = None,
        run_suite: bool = True,
        secrets: Secrets | None = None,
        container: Any = None,
    ) -> Toolbox:
        from aura.secrets import Secrets

        audit = AuditLog(settings.audit_log)
        secrets = secrets or Secrets(audit_record=audit.record)
        vault = Vault(settings.vault_dir, audit)
        # Tavily's key: the Keychain's (Settings → Keys) wins, so a key replaced there stays replaced
        # after a restart; .env's is only a fallback from before Sprint 6.
        search = search or WebSearch(secrets.get("tavily_api_key") or settings.tavily_api_key, audit)
        sandbox = sandbox or Sandbox(
            settings.sandbox_image, settings.vault_dir, settings.sandbox_timeout, audit
        )
        identity = IdentityStore(settings.data_dir, audit)
        voice = VoiceStore(settings.data_dir, audit, neural=speech.catalog())
        tools = [
            *vault_tools(vault),
            *web_tools(search),
            *sandbox_tools(sandbox, emit_status=lambda s: emit({"status": f"[Tool: Code_Interpreter_Tool] {s}"})),
            *voice_tools(voice),
            *identity_tools(identity),
        ]
        from aura.governor.continuous import ContinuousAudit
        from aura.developer.container import DevContainer
        from aura.developer.workflow import Developer
        from aura.governor.workshop import Workshop
        from aura.skills import SkillManager
        from aura.tools.workshop import workshop_tools

        skills = SkillManager(settings.skills_dir, settings.data_dir, sandbox, audit)
        workshop = Workshop(settings, skills, sandbox, audit, run_suite=run_suite)
        continuous = ContinuousAudit(skills, audit, settings.data_dir / "continuous_audit.json")
        developer = Developer(settings, workshop, container or DevContainer(sandbox, secrets, audit), secrets, audit)
        tools += workshop_tools(workshop, developer)
        connections = Connections(GoogleConnection(secrets, audit))
        return cls(audit, vault, search, sandbox, identity, voice, skills, workshop, continuous, secrets, developer,
                   connections, tools)  # fmt: skip
