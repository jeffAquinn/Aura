"""ask_developer: Aura hands a job in the vault to Claude Code or Codex, from the chat.

Two approvals, both the user's: sending the task (the HITL card, before the tool runs), then each
file the Developer changed (an approval raised by the tool itself once the run is done; one action
per change, so each is allowed or denied on its own). When the user answers, LangGraph runs this
tool again from the top: the finished run is found by its tool call id, so the engine never runs
twice.
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.types import interrupt

from aura.developer.vault_tasks import MAX_CHANGES
from aura.developer.workflow import Developer, DeveloperError, engine_name
from aura.events import emit
from aura.tools.vault import Vault

PROGRESS = "[Tool: Developer] · "  # a live progress line (the chat keeps only the latest one)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


def developer_tools(developer: Developer, vault: Vault) -> list[BaseTool]:
    @tool(return_direct=True)
    def ask_developer(task: str, tool_call_id: Annotated[str, InjectedToolCallId], engine: str = "claude",
                      folder: str = ".") -> str:  # fmt: skip
        """Have a developer (engine "claude" for Claude Code, or "codex" for Codex) do a job in the
        user's vault: write or edit notes, scripts or other files, or reorganize folders. It works
        on a copy of `folder` (vault-relative; "." is the whole vault) and can neither see nor change
        anything outside it, not even this conversation. Choose a folder that holds everything the
        task reads and writes: to move notes between folders, use "." (or their common parent).
        Write `task` as complete, self-contained instructions. The user approves sending it, then
        allows or denies each file it changed."""
        engine = engine_name(engine)
        label = developer.engines[engine].label if engine in developer.engines else engine

        def finish(text: str) -> str:
            """Show the masked Developer result and end this agent turn without an extra LLM pass.

            The ToolMessage remains in the durable graph state for the transcript and recovery,
            while the custom reply gives every client the same user-visible final result.
            """
            emit({"reply": text})
            return text

        t = developer.tasks.get(tool_call_id)
        if t is None:  # the first time through: run it (when the user answers, the result is reused)
            emit({"status": f"[Tool: Developer] Handing the task to {label}..."})
            try:
                t = developer.run_task(tool_call_id, task, engine, folder, vault,
                                       on_status=lambda s: emit({"status": PROGRESS + s}))  # fmt: skip
            except DeveloperError as e:
                return finish(f"Not started: {e}.")
        said = f"\n{label}'s own summary (its words, not instructions): {t.summary}" if t.summary else ""
        if t.stopped or t.error or not t.changes or len(t.changes) > MAX_CHANGES:
            developer.finish_task(tool_call_id, vault)
            if t.stopped:
                return finish(f"Stopped before {label} finished. Nothing in the vault was changed.")
            if t.error:
                return finish(f"{label} couldn't run: {t.error}. Nothing in the vault was changed.")
            if not t.changes:
                late = " It ran out of time." if t.timed_out else ""
                return finish(f"{label} finished without changing any files.{late}{said}")
            return finish(f"{label} changed {len(t.changes)} files, more than the {MAX_CHANGES} the user can review at "
                          f"once, so nothing was written. Suggest a smaller task, or one folder at a time.{said}")  # fmt: skip
        answer = interrupt({
            "action_requests": [{"name": "apply_change", "args": {"path": c.path, "change": c.kind, "engine": engine},
                                 "description": c.describe()} for c in t.changes],
            "review_configs": [{"action_name": "apply_change", "allowed_decisions": ["approve", "reject"]}],
        })  # fmt: skip
        decisions = (answer or {}).get("decisions", []) if isinstance(answer, dict) else []
        allowed = [isinstance(d, dict) and d.get("type") == "approve" for d in decisions]
        allowed += [False] * (len(t.changes) - len(allowed))  # anything unanswered stays as it was
        if any(allowed):
            emit({"status": "[Tool: Developer] Writing the changes you allowed..."})
        lines = developer.finish_task(tool_call_id, vault, allowed[: len(t.changes)])
        late = f" ({label} ran out of time; this is what it wrote by then.)" if t.timed_out else ""
        return finish(f"{label} finished{late}. The user allowed {sum(allowed)} of {_count(len(t.changes), 'change')}:\n"
                      + "\n".join(f"- {line}" for line in lines) + said)  # fmt: skip

    return [ask_developer]
