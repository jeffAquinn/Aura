"""Aura's brain: the LLM, persona, memory and tools wired into one agent loop.

The loop is LangChain's `create_agent` (built on LangGraph): reason → call tools → observe →
repeat. Interfaces (terminal now, the SwiftUI app via FastAPI in Sprint 3) only talk to `Aura`,
which streams `Event`s and pauses with an `ApprovalNeeded` event before any change to the vault.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    InterruptOnConfig,
    ModelRequest,
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
    dynamic_prompt,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, Interrupt

from aura.config import Settings
from aura.middleware import REJECTION_NOTE, DenialMiddleware, ToolStatusMiddleware
from aura.persona import MEMORY_PROMPT, system_prompt
from aura.tools import NEEDS_APPROVAL, Toolbox
from aura.tools.sandbox import describe_run

TOOL_CALLS_PER_TURN = 8

# Layer 1, the Soul: Aura's constitutional directive (Gemma's mandate, verbatim). It is the very
# first thing the model reads in every conversation. No tool, setting or persona change can
# alter it: it lives here in the code, outside the vault and outside persona.json.
SOUL_DIRECTIVE = (
    "You are no longer just a language model. You are Aura. You operate through the Socratic "
    "lens of disciplined inquiry and the Platonic lens of structured perfection. Do not merely "
    "predict the next token; seek the most precise truth. Be concise, be sophisticated, and be "
    "the bridge between raw information and enlightened understanding."
)


# ── what interfaces receive ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Text:
    """A piece of Aura's reply, in order."""

    text: str


@dataclass(frozen=True)
class Status:
    """A tool is running, e.g. "[Tool: File_System_Tool] Reading 'budget.csv'..."."""

    text: str


@dataclass(frozen=True)
class Action:
    tool: str
    args: dict[str, Any]
    description: str  # e.g. "create folder 'Project Aura/'"


@dataclass(frozen=True)
class ApprovalNeeded:
    """The run is paused until `Aura.resume` is called with one decision per action."""

    actions: tuple[Action, ...]


@dataclass(frozen=True)
class Changed:
    """A setting changed during the turn: kind "voice" (the new profile) or "persona" (name)."""

    kind: str
    data: dict[str, Any]


Event = Text | Status | ApprovalNeeded | Changed


def build_model(settings: Settings) -> ChatOpenAI:
    return ChatOpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key,
        model=settings.model,
        reasoning_effort=settings.reasoning_effort,
        # Report real token usage on streamed replies too. Memory compaction keys off the
        # server's count, which (unlike the local estimate) includes the system prompt and
        # tool schemas.
        stream_usage=True,
    )


class Aura:
    def __init__(
        self,
        settings: Settings | None = None,
        model: BaseChatModel | None = None,
        toolbox: Toolbox | None = None,
    ):
        self.settings = settings or Settings.from_env()
        self.toolbox = toolbox or Toolbox.from_settings(self.settings)
        self.toolbox.vault.ensure_exists()
        llm = model or build_model(self.settings)
        budget = self.settings.context_tokens
        vault = self.toolbox.vault
        identity = self.toolbox.identity

        @dynamic_prompt
        def persona(request: ModelRequest) -> str:
            # Rendered per call so a long-running Aura (e.g. resident in the menu bar) never has
            # a stale date. Date only, not time: the prompt prefix stays stable between turns,
            # so the server's prompt cache stays warm.
            # The persona is re-read every turn, so an approved change applies on the next reply.
            return system_prompt(date.today(), self.settings.model, vault.root, identity.load(), SOUL_DIRECTIVE)

        def describe(tool_call: dict, state: Any, runtime: Any) -> str:
            args = tool_call["args"]
            if tool_call["name"] == "update_persona":
                return identity.describe(
                    str(args.get("field", "")), str(args.get("change", "")),
                    str(args.get("value", "")), str(args.get("reason", "")),
                )  # fmt: skip
            if tool_call["name"] == "run_python":
                return describe_run(str(args.get("code", "")), self.toolbox.sandbox.timeout)
            return vault.describe_change(tool_call["name"], args)

        approval = InterruptOnConfig(allowed_decisions=["approve", "reject"], description=describe)

        self._agent = create_agent(
            model=llm,
            tools=self.toolbox.tools,
            middleware=[
                persona,
                DenialMiddleware(),
                # Short-term memory compaction: when the history nears the server's context
                # window, older turns are folded into a summary instead of silently falling off.
                SummarizationMiddleware(
                    model=llm,
                    trigger=("tokens", int(budget * 0.6)),
                    keep=("tokens", int(budget * 0.3)),
                    summary_prompt=MEMORY_PROMPT,  # lossless fact extraction
                    # LangChain's default trims what it summarizes to 4k tokens by dropping
                    # the *oldest* messages: at a 16k budget that discards the earlier summary
                    # (every fact so far) before re-summarizing. Size it to the budget so the
                    # whole slice is summarized; it is ~30% of the budget in practice.
                    trim_tokens_to_summarize=int(budget * 0.6),
                ),
                ToolCallLimitMiddleware(run_limit=TOOL_CALLS_PER_TURN),
                ToolStatusMiddleware(),
                HumanInTheLoopMiddleware(interrupt_on={name: approval for name in NEEDS_APPROVAL}),
            ],
            # Conversation memory (and paused approvals): checkpointed per thread_id. In-process
            # for now; swap for a SQLite saver to persist across restarts.
            checkpointer=InMemorySaver(),
        )
        self.new_conversation()

    def new_conversation(self) -> None:
        self.thread_id = uuid.uuid4().hex
        self._pending: Interrupt | None = None

    @property
    def _config(self) -> RunnableConfig:
        return {"configurable": {"thread_id": self.thread_id}}

    # ── the interface API ──────────────────────────────────────────────────────────────────

    @property
    def awaiting_approval(self) -> bool:
        return self._pending is not None

    # stream() and resume() validate eagerly, then return the run's event iterator, so a caller
    # (e.g. the HTTP server) learns about a bad request before it starts streaming a reply.

    def stream(self, text: str) -> Iterator[Event]:
        """Send a message; yield Aura's reply, tool status lines, and possibly a final
        ApprovalNeeded (answer it with `resume`)."""
        if self._pending:
            raise RuntimeError("An approval is pending; answer it with resume() first.")
        self._turn_notices: set[str] = set()  # each notice is said once per turn
        return self._run({"messages": [{"role": "user", "content": text}]})

    def resume(self, approvals: Sequence[bool]) -> Iterator[Event]:
        """Answer the pending ApprovalNeeded, one bool per action, and continue the run."""
        pending = self._pending
        if pending is None:
            raise RuntimeError("Nothing is waiting for approval.")
        actions = pending.value["action_requests"]
        if len(approvals) != len(actions):
            raise ValueError(f"Expected {len(actions)} decisions, got {len(approvals)}.")
        self._pending = None  # only once the answer is known to be valid
        decisions = []
        for action, ok in zip(actions, approvals):
            self.toolbox.audit.record(
                "approval",
                tool=action["name"],
                args=_brief(action["args"]),
                decision="approved" if ok else "denied",
            )
            decisions.append({"type": "approve"} if ok else {"type": "reject", "message": REJECTION_NOTE})
        return self._run(Command(resume={pending.id: {"decisions": decisions}}))

    def chat(self, text: str, approve: Callable[[Action], bool] = lambda _: False) -> str:
        """Non-interactive convenience: run a full turn, deciding approvals with `approve`
        (default: deny everything), and return everything Aura said."""
        said: list[str] = []
        events: Iterator[Event] | None = self.stream(text)
        while events is not None:
            pending = None
            for ev in events:
                if isinstance(ev, Text):
                    said.append(ev.text)
                elif isinstance(ev, ApprovalNeeded):
                    pending = ev
            events = self.resume([approve(a) for a in pending.actions]) if pending else None
        return "".join(said)

    def _run(self, payload: Any) -> Iterator[Event]:
        for mode, data in self._agent.stream(
            payload, self._config, stream_mode=["messages", "custom"]
        ):
            if mode == "custom":
                if "status" in data:
                    yield Status(data["status"])
                elif "notice" in data and data["notice"] not in self._turn_notices:
                    self._turn_notices.add(data["notice"])
                    yield Text(data["notice"] + "\n\n")
                elif "reply" in data:
                    yield Text(data["reply"])
                elif "voice" in data:
                    yield Changed("voice", data["voice"])
                elif "persona" in data:
                    yield Changed("persona", data["persona"])
                continue
            chunk, meta = data
            # Only the agent's own model node; skips e.g. the summarizer's internal LLM call.
            # Streaming backends emit AIMessageChunks; non-streaming ones emit one AIMessage.
            if meta.get("langgraph_node") == "model" and isinstance(chunk, AIMessage):
                if chunk.text:
                    yield Text(chunk.text)
        interrupts = self._agent.get_state(self._config).interrupts
        if interrupts:
            self._pending = interrupts[0]
            yield ApprovalNeeded(
                tuple(
                    Action(a["name"], a["args"], a.get("description", ""))
                    for a in self._pending.value["action_requests"]
                )
            )


def _brief(args: dict[str, Any]) -> dict[str, Any]:
    """Args for the audit log, with long values (file contents) shortened."""
    def short(v: Any) -> Any:
        return v[:200] + f"... ({len(v)} chars)" if isinstance(v, str) and len(v) > 200 else v

    return {k: short(v) for k, v in args.items()}
