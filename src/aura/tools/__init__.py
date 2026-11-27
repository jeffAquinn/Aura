"""Aura's hands: the toolbox the agent loop is given, and how each tool presents itself."""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.tools import BaseTool

from aura.capabilities import BY_NAME, CAPABILITIES, Capability, check_registered, skill_capability
from aura.connections import Connections
from aura.connections.actions import PRIVATE_READS, connection_tools
from aura.connections.discord import DiscordConnection
from aura.connections.google import GoogleConnection
from aura.connections.live import DiscordGateway, Inbox, LiveMessages, SlackSocket
from aura.connections.slack import SlackConnection
from aura.audit import AuditLog
from aura.config import Settings
from aura.events import emit
from aura.identity import IdentityStore
from aura.masking import Masker
from aura.state import StateStore
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

__all__ = ["NEEDS_APPROVAL", "PRIVATE_READS", "Toolbox", "emit", "status_line"]

# Derived from the Capability Registry (src/aura/capabilities.py), where each tool's floor is
# written once. NEEDS_APPROVAL: the tools whose every call shows an approval card (R1 floors: code
# runs, vault changes, persona changes, Developer work, sending and posting).
NEEDS_APPROVAL = tuple(c.name for c in CAPABILITIES if c.floor == "ask")
# Approvals that may be edited on the card before approving: tool -> the fields the user may change.
EDITABLE: dict[str, tuple[str, ...]] = {c.name: c.editable for c in CAPABILITIES if c.editable}
GROUP = {c.name: c.label for c in CAPABILITIES}


def status_line(tool_name: str, args: dict) -> str:
    """e.g. "[Tool: File_System_Tool] Reading 'budget.csv'..." (from the tool's Registry row; an
    installed skill's row is the one its manifest gives, as in skill_capability)."""
    row = BY_NAME.get(tool_name)
    if row is None and tool_name.startswith("skill_"):
        row = skill_capability(tool_name[len("skill_"):], {})
    if row is None:
        return f"[Tool: {tool_name}] Working..."
    doing = row.doing(args)
    return f"[Tool: {row.label}] {doing}" if doing else ""


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
    connections: Connections  # tools via connection_tools (Gemma's floors, taint and framing)
    tools: list[BaseTool] = field(default_factory=list)
    live: LiveMessages = field(default_factory=LiveMessages)  # live messages: started by the server only
    state: StateStore = field(default_factory=lambda: StateStore(None))  # turns, approvals, builds, vault tasks
    masker: Masker = field(default_factory=Masker)  # secrets out of tool output, audit entries and replies
    skill_rows: list[Capability] = field(default_factory=list)  # the loaded skills' rows, as of all_tools()
    skill_state: tuple[tuple[str, str], ...] = ()  # the loaded skills' (name, digest), as of all_tools()
    local_server: Any = None  # Aura's llama-server (aura.llama_server), set by the server only
    _checkpointer: Any = field(default=None, repr=False)

    def checkpointer(self) -> Any:
        """The durable conversation memory (LangGraph's SQLite checkpointer), shared by every Aura
        on this toolbox; each keeps its own conversation (thread) in it."""
        if self._checkpointer is None:
            import sqlite3

            from langgraph.checkpoint.sqlite import SqliteSaver

            path = self.state.path.with_name("conversations.sqlite") if self.state.path else None
            conn = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
            if path:
                conn.execute("PRAGMA journal_mode = WAL")
            saver = SqliteSaver(conn)
            saver.setup()  # creates or migrates its own tables (idempotent)
            self._checkpointer = saver
        return self._checkpointer

    def all_tools(self) -> list[BaseTool]:
        """The built-in tools, every approved and enabled skill, and the tools of whatever is
        connected (all re-read on each rebuild)."""
        loaded = self.skills.loaded()  # one scan of the skill folders for both
        tools = [*self.tools, *self.skills.tools(loaded=loaded), *connection_tools(self.connections, self.live.inbox)]
        self.skill_rows = self.skills.capabilities(loaded)
        self.skill_state = tuple((s.name, s.digest) for s in loaded)  # a re-approved skill changes its digest
        check_registered((t.name for t in tools), self.skill_rows)  # no row: no known floor
        return tools

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
        masker = audit.masker = Masker.for_secrets(secrets)
        state = StateStore(settings.data_dir / "state.sqlite")
        vault = Vault(settings.vault_dir, audit)
        # This is a one-time seed, never a sync.  From this point on all code edits target the
        # vault's copy; the running checkout and its Git repository remain read-only to Aura.
        vault.seed_project_copy(settings.project_dir)
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
        from aura.tools.developer import developer_tools
        from aura.tools.workshop import workshop_tools

        skills = SkillManager(settings.skills_dir, settings.data_dir, sandbox, audit)
        # Older Aura versions kept the live registry beside the source checkout. Bring only
        # previously approved, missing skills forward into the Vault; never overwrite a user's
        # Vault-local version or copy unaudited code out of the pending Inbox workflow.
        vault.migrate_skill_registry(settings.project_dir, approved=set(skills.approvals()))
        workshop = Workshop(settings, skills, sandbox, audit, run_suite=run_suite)
        continuous = ContinuousAudit(skills, audit, settings.data_dir / "continuous_audit.json")
        developer = Developer(settings, workshop, container or DevContainer(sandbox, secrets, audit), secrets, audit,
                              state=state, masker=masker)  # fmt: skip
        tools += workshop_tools(workshop, developer)
        tools += developer_tools(developer, vault)
        slack, discord = SlackConnection(secrets, audit), DiscordConnection(secrets, audit)
        connections = Connections(GoogleConnection(secrets, audit), slack, discord)
        inbox = Inbox()
        live = LiveMessages(inbox, SlackSocket(slack, inbox), DiscordGateway(discord, inbox))
        box = cls(audit, vault, search, sandbox, identity, voice, skills, workshop, continuous, secrets, developer,
                  connections, tools, live, state, masker)  # fmt: skip
        from aura.tools.models import model_tools

        # What llama-server has loaded: the server sets local_server after the toolbox is built.
        box.tools += model_tools(settings, lambda: getattr(box.local_server, "model", None), vault.root)
        return box
