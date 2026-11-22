"""Aura's hands: the toolbox the agent loop is given, and how each tool presents itself."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from langchain_core.tools import BaseTool
from langgraph.config import get_stream_writer

from aura.audit import AuditLog
from aura.config import Settings
from aura.tools.sandbox import Sandbox, sandbox_tools
from aura.tools.vault import Vault, vault_tools
from aura.tools.web import WebSearch, web_tools

# Tools that change the vault: each pauses for the user's approval before it runs.
NEEDS_APPROVAL = ("write_file", "create_folder", "delete_path")

GROUP = {
    "list_vault": "File_System_Tool",
    "read_file": "File_System_Tool",
    "write_file": "File_System_Tool",
    "create_folder": "File_System_Tool",
    "delete_path": "File_System_Tool",
    "web_search": "Web_Search_Tool",
    "run_python": "Code_Interpreter_Tool",
}

_DOING: dict[str, Callable[[dict], str]] = {
    "list_vault": lambda a: f"Listing '{a.get('path') or '.'}'...",
    "read_file": lambda a: f"Reading '{a.get('path')}'...",
    "write_file": lambda a: f"Writing '{a.get('path')}'...",
    "create_folder": lambda a: f"Creating folder '{a.get('path')}'...",
    "delete_path": lambda a: f"Deleting '{a.get('path')}'...",
    "web_search": lambda a: f"Searching for '{a.get('query')}'...",
    "run_python": lambda a: "Running Python in the sandbox...",
}


def status_line(tool_name: str, args: dict) -> str:
    """e.g. "[Tool: File_System_Tool] Reading 'budget.csv'..." """
    doing = _DOING.get(tool_name, lambda a: "Working...")(args)
    return f"[Tool: {GROUP.get(tool_name, tool_name)}] {doing}"


def emit(event: dict) -> None:
    """Send a UI event (status line / notice) to whoever is streaming the agent, if anyone."""
    try:
        get_stream_writer()(event)
    except Exception:  # not inside a streamed run
        pass


@dataclass
class Toolbox:
    audit: AuditLog
    vault: Vault
    search: WebSearch
    sandbox: Sandbox
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
        tools = [
            *vault_tools(vault),
            *web_tools(search),
            *sandbox_tools(sandbox, emit_status=lambda s: emit({"status": f"[Tool: Code_Interpreter_Tool] {s}"})),
        ]
        return cls(audit, vault, search, sandbox, tools)
