"""Aura's brain: the LLM, persona, memory and tools wired into one agent loop.

The loop is LangChain's `create_agent` (built on LangGraph): reason → call tools → observe →
repeat. Interfaces (terminal now, the SwiftUI app via FastAPI in Sprint 3) only talk to `Aura`,
which streams `Event`s and pauses with an `ApprovalNeeded` event before any change to the vault.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
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
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.outputs import LLMResult
from langchain_core.runnables import RunnableConfig
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import Command, Interrupt

from aura.config import Settings
from aura.model_routing import ModelChoice
from aura.middleware import (
    REJECTION_NOTE,
    DenialMiddleware,
    ProviderThinkingSanitizer,
    RecursionCircuitBreaker,
    RecursionMiddleware,
    ToolStatusMiddleware,
)
from aura.persona import MEMORY_PROMPT, system_prompt
from aura.developer.workflow import describe_build, describe_task
from aura.connections.actions import OUTWARD, describe_outward, inbox_exchange, turn_is_tainted
from aura.tools import EDITABLE, NEEDS_APPROVAL, Toolbox
from aura.tools.sandbox import describe_run

TOOL_CALLS_PER_TURN = 8
CHECKPOINTS_KEPT = 8  # per conversation: only the latest is ever read (no time travel)
TRANSCRIPT_CHARS = 20_000  # of one message, when the app restores the conversation
# What the model reads for a tool call that never got its result (the turn was stopped, or Aura
# restarted). Its effects, if any, stay: this says so instead of pretending either way.
CUT_SHORT = ("This was cut short (the user stopped the turn, or Aura restarted) before it finished, so it may "
             "have partly run. Don't assume either way; check before relying on it, and don't repeat it unless "
             "the user asks.")  # fmt: skip
DIRECT_FOLDER = re.compile(r"(?im)^\s*folder:\s*(\.|[A-Za-z0-9][A-Za-z0-9._/-]{0,199})\s*$")


class UsageAwareSummarizationMiddleware(SummarizationMiddleware):
    """Retain the configured fraction of context when a provider reports prompt usage.

    LangChain uses reported usage to decide *when* to summarize, but its normal cutoff is based
    only on conversation messages. System prompts and tool schemas can make the provider's count
    reach the trigger while those messages still fit inside the local keep budget, yielding a
    zero-length summary. Reserve the provider-reported fixed overhead from the whole-prompt keep
    budget before choosing a local-message cutoff instead.
    """

    def _trigger_tokens(self) -> int | None:
        return next((int(clause["tokens"]) for clause in self._trigger_clauses if "tokens" in clause), None)

    @staticmethod
    def _reported_input_tokens(messages: Sequence[Any]) -> int | None:
        last_ai = next((message for message in reversed(messages) if isinstance(message, AIMessage)), None)
        reported = (last_ai.usage_metadata or {}).get("input_tokens") if last_ai else None
        return reported if isinstance(reported, int) and reported > 0 else None

    @staticmethod
    def _has_new_history(messages: Sequence[Any]) -> bool:
        """A reported-usage fallback may not summarize a previous summary by itself."""
        return any(
            not (
                isinstance(message, HumanMessage)
                and message.additional_kwargs.get("lc_source") == "summarization"
            )
            for message in messages
        )

    def _reported_cutoff(self, messages: Sequence[Any]) -> int:
        if self.keep[0] != "tokens":
            return 0
        reported = self._reported_input_tokens(messages)
        if reported is None:
            return 0
        local_total = self._partial_token_counter(messages)
        if local_total <= 0:
            return 0
        trigger = self._trigger_tokens()
        overhead = max(0, reported - local_total)
        # Compaction cannot solve a prompt whose fixed system/tool overhead already reaches the
        # trigger. Let the normal local-history path govern that case instead of re-summarizing
        # every turn from a provider count that no amount of history trimming can reduce.
        if trigger is None or overhead >= trigger:
            return 0
        # The configured keep budget is a whole-prompt budget. Reserve its fixed system/tool
        # portion first; scaling the whole local history would incorrectly assume that overhead
        # shrinks along with the conversation.
        local_keep = max(0, self.keep[1] - overhead)
        for index in range(1, len(messages)):
            if self._partial_token_counter(messages[index:]) <= local_keep:
                cutoff = self._find_safe_cutoff_point(messages, index)
                if cutoff > 0:
                    return cutoff
        if local_keep == 0 and len(messages) > 1:
            # Keep the current message (or its complete tool-call group) so it can still be
            # answered; the prior conversation is represented by the summary.
            return self._find_safe_cutoff_point(messages, len(messages) - 1)
        return 0

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        messages = state["messages"]
        # Preserve LangChain's normal path, including local-only compaction when usage is absent.
        if update := super().before_model(state, runtime):
            return update
        if not self._should_summarize(messages, self.token_counter(messages)):
            return None
        cutoff = self._reported_cutoff(messages)
        if cutoff <= 0:
            return None
        messages_to_summarize = messages[:cutoff]
        if not self._has_new_history(messages_to_summarize):
            return None
        summary = self._create_summary(messages_to_summarize)
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *self._build_new_messages(summary),
                *messages[cutoff:],
            ]
        }

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
    """The run is paused until `Aura.resume` is called with one decision per action. The answer
    names `approval_id`, which binds it to this turn, conversation, tools and arguments."""

    actions: tuple[Action, ...]
    approval_id: str = ""


class TurnCancelled(Exception):
    """Raised inside the model's token stream once the turn is stopped: ends the HTTP stream to the
    model at once, instead of waiting for it to finish generating."""


class _StopOnCancel(BaseCallbackHandler):
    raise_error = True  # a callback's exception normally is only logged
    run_inline = True

    def __init__(self, cancel: threading.Event):
        self.cancel = cancel

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        if self.cancel.is_set():
            raise TurnCancelled


class StaleApproval(RuntimeError):
    """The answer is for an approval that is no longer the one waiting (a new conversation, a
    newer request, an answer already given, or permissions changed since it was asked)."""


@dataclass(frozen=True)
class Changed:
    """Something the app shows changed during the turn: kind "voice" (the new profile), "persona"
    (the name) or "task" (an audit or a build started; see the Workshop)."""

    kind: str
    data: dict[str, Any]


Event = Text | Status | ApprovalNeeded | Changed


class _LocalPerformanceRecorder(BaseCallbackHandler):
    """Capture one local LLM call's real wall time and reported token counts.

    Aura uses Ollama's OpenAI-compatible endpoint for tool-calling. That endpoint reports exact
    token usage but, unlike Ollama's native ``--verbose`` endpoint, does not include its internal
    nanosecond duration fields. Measuring the request and its token stream here keeps the app
    honest about what it can observe without issuing a second inference request.
    """

    run_inline = True

    def __init__(self) -> None:
        self._starts: dict[uuid.UUID, float] = {}
        self._first_tokens: dict[uuid.UUID, float] = {}
        self._finished: list[dict[str, int | float]] = []
        self._lock = threading.Lock()

    def on_chat_model_start(self, serialized: dict, messages: list, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        self._start(run_id)

    def on_llm_start(self, serialized: dict, prompts: list[str], *, run_id: uuid.UUID, **kwargs: Any) -> None:
        self._start(run_id)

    def _start(self, run_id: uuid.UUID) -> None:
        with self._lock:
            self._starts.setdefault(run_id, time.perf_counter())

    def on_llm_new_token(self, token: str, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        with self._lock:
            if run_id in self._starts:
                self._first_tokens.setdefault(run_id, time.perf_counter())

    def on_llm_error(self, error: BaseException, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        with self._lock:
            self._starts.pop(run_id, None)
            self._first_tokens.pop(run_id, None)

    def on_llm_end(self, response: LLMResult, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        with self._lock:
            started = self._starts.pop(run_id, None)
            first_token = self._first_tokens.pop(run_id, None)
        if started is None or not response.generations or not response.generations[0]:
            return
        message = response.generations[0][0].message
        usage = getattr(message, "usage_metadata", None) or {}
        prompt_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
        if not isinstance(prompt_tokens, int) or not isinstance(output_tokens, int) or output_tokens <= 0:
            return
        finished = time.perf_counter()
        # The first generated token arrives after prompt evaluation. This is the closest
        # observable equivalent of Ollama's native eval rate on the OpenAI-compatible route.
        generation_seconds = max(0.001, finished - (first_token or started))
        with self._lock:
            self._finished.append({
                "total_duration": max(0.0, finished - started),
                "prompt_tokens": prompt_tokens,
                "output_tokens": output_tokens,
                "output_tokens_per_second": output_tokens / generation_seconds,
            })

    def drain(self) -> list[dict[str, int | float]]:
        with self._lock:
            finished, self._finished = self._finished, []
        return finished


def build_model(settings: Settings, choice: ModelChoice | None = None, api_key: str | None = None) -> BaseChatModel:
    """Build either the local OpenAI-compatible model or a first-party Claude API model.

    ``claude_oauth_token`` is intentionally never accepted here: it is a Claude Code credential,
    not Aura's Anthropic API credential.
    """
    choice = choice or ModelChoice("ollama", settings.model)
    if choice.provider == "anthropic":
        if not api_key:
            raise ValueError("Add an Anthropic API key in Settings → Keys before selecting a Claude cloud model.")
        return ChatAnthropic(model=choice.model, api_key=api_key, max_tokens=settings.max_reply_tokens)
    return ChatOpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key,
        model=choice.model,
        reasoning_effort=settings.reasoning_effort,
        max_tokens=settings.max_reply_tokens,  # a looping model stops instead of running forever
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
        primary: bool = True,
        role: str | None = None,
    ):
        """`primary`: the Mac panel's Aura. Another (Slack chat, remote.py) shares the toolbox and
        model but keeps its own conversation, and leaves the reload hooks and the live-message
        notices to the primary.

        `role` names the saved conversation this Aura resumes and owns (default: "mac" for the
        primary, "slack" otherwise). A second process on the same data folder, such as the
        terminal (`uv run aura`), must use its own role: a new conversation deletes the old one on
        disk, so sharing "mac" would erase the app's conversation from under it."""
        self.settings = settings or Settings.from_env()
        self.toolbox = toolbox or Toolbox.from_settings(self.settings)
        self.toolbox.vault.ensure_exists()
        self.primary = primary
        self.active_model = ModelChoice("ollama", self.settings.model)
        self._injected_model = model is not None
        self.llm = model or build_model(self.settings, self.active_model)
        # This guard is reset only for a new user turn. It survives an approval/resume within that
        # turn so an agent cannot evade the retry limit by splitting the same failed work in two.
        self._circuit_breaker = RecursionCircuitBreaker()
        budget = self.settings.context_tokens
        vault = self.toolbox.vault
        identity = self.toolbox.identity

        @dynamic_prompt
        def persona(request: ModelRequest) -> str:
            # Rendered per call so a long-running Aura (e.g. resident in the menu bar) never has
            # a stale date. Date only, not time: the prompt prefix stays stable between turns,
            # so the server's prompt cache stays warm.
            # The persona is re-read every turn, so an approved change applies on the next reply.
            return system_prompt(
                date.today(), self.active_model.model, vault.root, identity.load(), SOUL_DIRECTIVE,
                project_sandbox=self.settings.project_sandbox_dir,
            ) + "\n\n" + self._live_skill_context()

        def describe(tool_call: dict, state: Any, runtime: Any) -> str:
            args = tool_call["args"]
            if tool_call["name"] == "update_persona":
                return identity.describe(
                    str(args.get("field", "")), str(args.get("change", "")),
                    str(args.get("value", "")), str(args.get("reason", "")),
                )  # fmt: skip
            if tool_call["name"] == "run_python":
                return describe_run(str(args.get("code", "")), self.toolbox.sandbox.timeout)
            if tool_call["name"] == "develop_skill":
                return describe_build(args, self.toolbox.developer)
            if tool_call["name"] == "ask_developer":
                return describe_task(args, self.toolbox.developer, vault)
            if tool_call["name"] in (*OUTWARD, "web_search"):
                return describe_outward(tool_call["name"], args)
            return vault.describe_change(tool_call["name"], args)

        approval = InterruptOnConfig(allowed_decisions=["approve", "reject"], description=describe)
        # The Specification Approval card: the user may also edit the specification before approving.
        editable = InterruptOnConfig(allowed_decisions=["approve", "edit", "reject"], description=describe)
        # R2 (Gemma's taint rule): a web search sends its words off this Mac, so once a turn has read
        # private data (mail, calendar, Drive, Slack, Discord) the search waits for the user too.
        tainted = InterruptOnConfig(allowed_decisions=["approve", "reject"], description=describe,
                                    when=lambda request: turn_is_tainted(request.state["messages"]))  # fmt: skip
        # Conversation memory (and paused approvals): checkpointed per thread_id in SQLite, so it
        # survives agent rebuilds (deploying a skill) and restarts of the server.
        self._checkpointer = self.toolbox.checkpointer()
        self.role = role or ("mac" if primary else "slack")
        self.turn_id: str | None = None  # the server's correlation id for the turn running now
        self.recovery_note: str | None = None  # what a restart left unfinished (shown once by the app)

        def build(tools: list) -> Any:
            self._summarization = UsageAwareSummarizationMiddleware(
                model=self.llm,
                trigger=("tokens", int(budget * 0.6)),
                keep=("tokens", int(budget * 0.3)),
                summary_prompt=MEMORY_PROMPT,
                # LangChain's default trims what it summarizes to 4k tokens by dropping
                # the *oldest* messages: at a 16k budget that discards the earlier summary
                # (every fact so far) before re-summarizing. Size it to the budget so the
                # whole slice is summarized; it is ~30% of the budget in practice.
                trim_tokens_to_summarize=int(budget * 0.6),
            )
            return create_agent(
                model=self.llm,
                tools=tools,
                middleware=[
                    persona,
                    # A provider's signed extended-thinking blocks are valid only for the exact
                    # request that produced them. Sanitize before compaction or another model
                    # pass so restart recovery and durable history remain provider-neutral.
                    ProviderThinkingSanitizer(),
                    DenialMiddleware(),
                    # Short-term memory compaction: when the history nears the server's context
                    # window, older turns are folded into a summary instead of silently falling off.
                    self._summarization,
                    ToolCallLimitMiddleware(run_limit=TOOL_CALLS_PER_TURN),
                    ToolStatusMiddleware(self.toolbox.masker, self._circuit_breaker, self.toolbox.audit),
                    RecursionMiddleware(),
                    HumanInTheLoopMiddleware(interrupt_on={
                        **{name: editable if name in EDITABLE else approval for name in NEEDS_APPROVAL},
                        "web_search": tainted}),
                ],
                checkpointer=self._checkpointer,
            )

        self._build = build
        self._connected = self.toolbox.connections.signature()
        self._agent = build(self.toolbox.all_tools())
        if primary:
            self.toolbox.workshop.on_deployed = self.reload_tools
            self.toolbox.continuous.on_change = self.reload_tools  # the server wraps this in its turn lock
        self._pending: Interrupt | None = None
        self.approval_id: str | None = None
        saved = self.toolbox.state.get_meta(f"conversation:{self.role}")
        if saved:
            self.thread_id = saved  # the conversation from before the restart
            self._restore()
        else:
            self.new_conversation()

    def reload_tools(self) -> None:
        """Rebuild the agent with the current tools (e.g. a newly deployed skill). Memory is kept."""
        self._connected = self.toolbox.connections.signature()
        self._agent = self._build(self.toolbox.all_tools())

    def select_model(self, choice: ModelChoice) -> None:
        """Switch model roles only between turns, preserving the durable conversation state."""
        choice = choice.validate()
        if choice == self.active_model:
            return
        if self._injected_model:
            # Test and embedding callers deliberately supplied a model object. Do not replace it
            # with a network model behind their back; retain the visible choice for the prompt.
            self.active_model = choice
            self.reload_tools()
            return
        key = self.toolbox.secrets.get("anthropic_api_key") if choice.provider == "anthropic" else None
        self.llm = build_model(self.settings, choice, key)
        self.active_model = choice
        self.reload_tools()

    def new_conversation(self) -> None:
        old = getattr(self, "thread_id", None)
        if old:  # an approval card of the old conversation can no longer be answered
            if self._pending is not None:  # e.g. a vault review: its working copy is dropped
                self._settle("The user started a new conversation before answering; nothing was carried out.")
            self.toolbox.state.stale_approvals(old, "the conversation was reset")
            self._checkpointer.delete_thread(old)  # forgotten means forgotten, on disk too
        self.thread_id = uuid.uuid4().hex
        self._pending = None
        self.approval_id = None
        self.recovery_note = None
        self.toolbox.state.set_meta(f"conversation:{self.role}", self.thread_id)

    # ── durability: restart recovery and runs cut short ────────────────────────────────────

    def _restore(self) -> None:
        """After a restart: an approval that can still be answered safely is waiting again; any
        other unfinished run is settled, so nothing runs twice and the history stays valid."""
        state = self._agent.get_state(self._config)
        if state.interrupts:
            interrupt = state.interrupts[0]
            record = self.toolbox.state.approval_for(interrupt.id)
            problem = None
            if record and record["state"] == "submitted":
                problem = ("Aura restarted while carrying out an action you had approved. Whether it finished is "
                           "unknown, so it was not repeated.")  # fmt: skip
            elif record and record["state"] != "pending":
                problem = "An approval request went stale while Aura was restarting; it was not carried out."
            elif self._needs_lost_task(state):
                problem = ("Aura restarted, and the developer's working copy for the changes you were reviewing "
                           "couldn't be kept. Nothing was written to your vault.")  # fmt: skip
            if problem is None:
                self._pending = interrupt
                self.approval_id = record["id"] if record else self._register(interrupt, self._actions(interrupt))
                return
            if record:
                self.toolbox.state.settle_approval(record["id"], "unrecoverable", problem)
            self._settle(problem)
            self.recovery_note = problem
            self.toolbox.audit.record("approval_unrecoverable", conversation=self.thread_id, reason=problem)
        elif state.next:
            self._settle(CUT_SHORT)
            self.recovery_note = "Aura restarted while it was working on your last message; it may have partly run."

    def _needs_lost_task(self, state: Any) -> bool:
        """Is the pending approval a vault job's per-file review whose working copy is gone?"""
        names = {a.get("name") for a in state.interrupts[0].value.get("action_requests", [])}
        if "apply_change" not in names:
            return False
        return not any(tc["id"] in self.toolbox.developer.tasks for tc in self._dangling(state))

    @staticmethod
    def _dangling(state: Any) -> list[dict[str, Any]]:
        msgs = state.values.get("messages", [])
        answered = {m.tool_call_id for m in msgs if isinstance(m, ToolMessage)}
        last = next((m for m in reversed(msgs) if isinstance(m, AIMessage)), None)
        return [tc for tc in (last.tool_calls if last else []) if tc["id"] not in answered]

    def _settle(self, note: str = CUT_SHORT) -> bool:
        """End an unfinished run without running anything: every tool call that has no result yet
        gets `note` as its result, and a waiting approval is dropped. Returns whether anything
        needed settling. (No model call: this is safe at startup and before a new message.)"""
        state = self._agent.get_state(self._config)  # includes the writes of steps that finished
        # A run closed mid-step: a tool that finished has its result only as a pending write of a
        # step that was never committed, and a new message would discard it. Commit it.
        saved = self._checkpointer.get_tuple(self._config)
        committed = {m.id for m in (saved.checkpoint["channel_values"].get("messages", []) if saved else [])}
        uncommitted = [m for m in state.values.get("messages", []) if m.id not in committed]
        if not (state.interrupts or state.next or uncommitted):
            return False
        dangling = self._dangling(state)
        for tc in dangling:
            if tc["name"] == "ask_developer":  # its working copy: dropped, nothing written
                self.toolbox.developer.finish_task(tc["id"], self.toolbox.vault)
        self._agent.update_state(self._config, {"messages": [*uncommitted, *(
            ToolMessage(note, tool_call_id=tc["id"], name=tc["name"], status="error") for tc in dangling)]},
            as_node="tools")  # fmt: skip
        if self.approval_id:
            self.toolbox.state.settle_approval(self.approval_id, "stale", note)
        self._pending, self.approval_id = None, None
        return True

    def _permissions(self) -> str:
        """What Aura may do right now: an approval asked under other permissions goes stale."""
        try:
            skills = sorted(s.name for s in self.toolbox.skills.scan() if s.loaded)
        except Exception:  # noqa: BLE001
            skills = []
        return json.dumps({"connections": self.toolbox.connections.signature(), "skills": skills}, default=str)

    def _live_skill_context(self) -> str:
        """A compact, current skill fact base that takes precedence over restored chat text."""
        try:
            registry = self.toolbox.skills.scan()
            active = sorted(skill.name for skill in registry if skill.loaded)
            inactive = sorted(f"{skill.name} ({skill.status})" for skill in registry if not skill.loaded)
        except Exception:  # noqa: BLE001: an unavailable registry must not make chat unavailable
            active, inactive = [], []
        running = sorted(task.skill for task in self.toolbox.workshop.tasks.values()
                         if task.stage in ("staging", "auditing", "awaiting"))
        return "\n".join([
            "Live skills (authoritative; overrides chat history):",
            "- Active and callable now: " + (", ".join(active) if active else "none"),
            "- Unavailable: " + (", ".join(inactive) if inactive else "none"),
            "- Under audit: " + (", ".join(running) if running else "none"),
            "Never call an active skill pending or auditing. Use its skill_<name> tool with real user input; if "
            "input is missing, ask briefly. Do not invent files, tests, or results.",
        ])

    def _actions(self, interrupt: Interrupt) -> tuple[Action, ...]:
        return tuple(Action(a["name"], a["args"], a.get("description", "")) for a in interrupt.value["action_requests"])

    def _register(self, interrupt: Interrupt, actions: tuple[Action, ...]) -> str:
        """Bind a new approval to its turn, conversation, tools and argument digest."""
        approval_id = "a-" + uuid.uuid4().hex[:16]
        digest = hashlib.sha256(json.dumps([self.thread_id, interrupt.id, [(a.tool, a.args) for a in actions]],
                                           sort_keys=True, default=str).encode()).hexdigest()  # fmt: skip
        self.toolbox.state.add_approval(approval_id, self.turn_id, self.thread_id, interrupt.id, digest,
                                        [a.tool for a in actions], self._permissions())  # fmt: skip
        return approval_id

    def pending(self) -> dict[str, Any] | None:
        """The approval waiting now (for the app to show again after a restart), or None."""
        if self._pending is None:
            return None
        return {"approval_id": self.approval_id,
                "actions": [{"tool": a.tool, "args": a.args, "description": a.description}
                            for a in self._actions(self._pending)]}  # fmt: skip

    def transcript(self, limit: int = 200) -> list[dict[str, str]]:
        """The conversation as the user saw it: their messages and Aura's replies (no tool calls,
        no memory summaries). Long messages are cut with a marker."""
        out: list[dict[str, str]] = []
        for m in self._agent.get_state(self._config).values.get("messages", []):
            if isinstance(m, HumanMessage) and m.additional_kwargs.get("lc_source") != "summarization":
                role, text = "user", m.text.split("\n\n[I chose ", 1)[0]
            elif isinstance(m, AIMessage) and m.text:
                role, text = "aura", m.text
            else:
                continue
            if len(text) > TRANSCRIPT_CHARS:
                text = text[:TRANSCRIPT_CHARS] + f"\n… [cut: {len(text) - TRANSCRIPT_CHARS} more characters]"
            out.append({"role": role, "text": text})
        return out[-limit:]

    @property
    def _config(self) -> RunnableConfig:
        return {"configurable": {"thread_id": self.thread_id}}

    # ── the interface API ──────────────────────────────────────────────────────────────────

    @property
    def awaiting_approval(self) -> bool:
        return self._pending is not None

    # stream() and resume() validate eagerly, then return the run's event iterator, so a caller
    # (e.g. the HTTP server) learns about a bad request before it starts streaming a reply.

    def stream(self, text: str, developer: str | None = None, turn_id: str | None = None,
               cancel: threading.Event | None = None) -> Iterator[Event]:
        """Send a message; yield Aura's reply, tool status lines, and possibly a final
        ApprovalNeeded (answer it with `resume`). `developer` ("claude" or "codex"): the user chose
        that Developer in the chat box, so Aura hands the message to it (ask_developer)."""
        if self._pending:
            raise RuntimeError("An approval is pending; answer it with resume() first.")
        self.turn_id = turn_id
        self._circuit_breaker.reset()
        self._settle()  # the last turn was stopped mid-way: give its open tool calls a result
        if self.toolbox.connections.signature() != self._connected:  # an account was connected or removed
            self.reload_tools()
        self._turn_notices: set[str] = set()  # each notice is said once per turn
        if developer is not None:
            engine = self.toolbox.developer.engines.get(developer)
            if engine is None:
                raise ValueError(f"unknown developer '{developer}'")
            # A person who explicitly chooses Claude Code or Codex expects the Developer handoff,
            # not another model turn that might stall before presenting the approval card. Seed a
            # normal ``ask_developer`` tool call as the model node so the existing approval,
            # checkpoint, restart recovery and per-file review machinery remains authoritative.
            # An explicit "Aura project sandbox" request is the one safe case where the folder is
            # selected without a planning turn; every other direct handoff retains the vault-wide
            # default and its measured approval card.
            folder = "."
            task = text
            if match := DIRECT_FOLDER.search(text):
                folder = match.group(1)
                task = DIRECT_FOLDER.sub("", text, count=1).strip()
                if not task:
                    raise ValueError("Describe the work after the 'Folder:' line.")
            sandbox = self.settings.project_sandbox_dir
            if folder == "." and "aura project sandbox" in text.lower() and sandbox is not None:
                try:
                    folder = sandbox.resolve().relative_to(self.toolbox.vault.root.resolve()).as_posix()
                except ValueError:
                    pass
            call_id = "direct-developer-" + uuid.uuid4().hex
            direct = AIMessage(content="", tool_calls=[{
                "name": "ask_developer", "args": {"task": task, "engine": engine.name, "folder": folder},
                "id": call_id,
            }])
            self._agent.update_state(self._config, {"messages": [HumanMessage(content=task), direct]}, as_node="model")
            return self._run(None, cancel=cancel)
        messages: list[Any] = [{"role": "user", "content": text}]
        fresh = self.toolbox.live.inbox.unseen_by_model() if self.primary else []
        if fresh:  # live messages since the last turn: a private read (R2), framed as untrusted (R5)
            messages += inbox_exchange(fresh)
        return self._run({"messages": messages}, cancel=cancel)

    def resume(self, approvals: Sequence[bool | dict[str, Any]], approval_id: str | None = None,
               turn_id: str | None = None, cancel: threading.Event | None = None) -> Iterator[Event]:
        """Answer the pending ApprovalNeeded, one decision per action, and continue the run. A
        decision is True/False, or {"approve": true, "args": {...}} to approve with edited
        arguments (only the fields EDITABLE allows, e.g. a build's specification)."""
        pending = self._pending
        if pending is None:
            raise RuntimeError("Nothing is waiting for approval.")
        if approval_id is not None and approval_id != self.approval_id:
            raise StaleApproval("That approval card is out of date: it isn't the request Aura is waiting on.")
        actions = pending.value["action_requests"]
        if len(approvals) != len(actions):
            raise ValueError(f"Expected {len(actions)} decisions, got {len(approvals)}.")
        edits = [a.get("args") if isinstance(a, dict) else None for a in approvals]
        for action, edit in zip(actions, edits):
            if edit is not None and (not isinstance(edit, dict) or not set(edit) <= set(EDITABLE.get(action["name"], ()))):
                raise ValueError(f"{action['name']} can't be edited that way.")
        record = self.toolbox.state.approval(self.approval_id) if self.approval_id else None
        if record and record["signature"] != self._permissions():
            self._settle("The user's permissions changed before they answered, so this was not carried out. "
                         "Ask again if it's still wanted.")  # fmt: skip
            raise StaleApproval("Your permissions changed since Aura asked, so that request was cancelled. Ask again.")
        answering = self.approval_id
        if answering and not self.toolbox.state.claim_approval(answering):  # exactly one answer runs
            raise StaleApproval("That approval was already answered.")
        self.turn_id = turn_id
        self._pending, self.approval_id = None, None  # only once the answer is known to be valid
        decisions = []
        for action, answer, edit in zip(actions, approvals, edits):
            ok = bool(answer.get("approve")) if isinstance(answer, dict) else bool(answer)
            self.toolbox.audit.record(
                "approval",
                tool=action["name"],
                args=_brief({**action["args"], **(edit or {})}),
                decision=("approved with edits" if edit else "approved") if ok else "denied",
            )
            if ok and edit:
                decisions.append({"type": "edit", "edited_action": {"name": action["name"], "args": {**action["args"], **edit}}})
            else:
                decisions.append({"type": "approve"} if ok else {"type": "reject", "message": REJECTION_NOTE})
        return self._run(Command(resume={pending.id: {"decisions": decisions}}), answering=answering, cancel=cancel)

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

    def _run(self, payload: Any, answering: str | None = None, cancel: threading.Event | None = None) -> Iterator[Event]:
        """`cancel`: the turn's token. Checked at every chunk (a model writing a long tool call
        streams no text), so a stop ends the run at the next chunk; the step's open tool calls are
        settled before the next message (_settle), and no approval is raised by a stopped run."""
        performance = _LocalPerformanceRecorder() if self.active_model.provider == "ollama" else None
        callbacks: list[BaseCallbackHandler] = []
        if cancel is not None:
            callbacks.append(_StopOnCancel(cancel))
        if performance is not None:
            callbacks.append(performance)
        config = {**self._config, "callbacks": callbacks} if callbacks else self._config
        run = self._agent.stream(payload, config, stream_mode=["messages", "custom"])
        try:
            for mode, data in run:
                if cancel is not None and cancel.is_set():
                    return
                yield from self._event(mode, data)
                if performance is not None:
                    for measured in performance.drain():
                        yield Changed("performance", measured)
        except TurnCancelled:
            return
        finally:
            try:
                run.close()
            except TurnCancelled:
                pass
        if performance is not None:
            for measured in performance.drain():
                yield Changed("performance", measured)
        if cancel is not None and cancel.is_set():
            return
        if answering:  # the approved continuation ran to the end
            self.toolbox.state.settle_approval(answering, "done")
        prune_checkpoints(self._checkpointer, self.thread_id)
        interrupts = self._agent.get_state(self._config).interrupts
        if interrupts:
            self._pending = interrupts[0]
            actions = self._actions(self._pending)
            self.approval_id = self._register(self._pending, actions)
            yield ApprovalNeeded(actions, self.approval_id)

    def _event(self, mode: str, data: Any) -> Iterator[Event]:
        """One item of the agent's stream, as what interfaces receive (often nothing)."""
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
            elif "task" in data:  # an audit or a build started: the chat shows a card for it
                yield Changed("task", data["task"])
            return
        chunk, meta = data
        # Only the agent's own model node; skips e.g. the summarizer's internal LLM call.
        # Streaming backends emit AIMessageChunks; non-streaming ones emit one AIMessage.
        if meta.get("langgraph_node") == "model" and isinstance(chunk, AIMessage) and chunk.text:
            yield Text(chunk.text)


def prune_checkpoints(saver: Any, thread_id: str, keep: int = CHECKPOINTS_KEPT) -> None:
    """Bound the conversation store: drop all but the latest `keep` checkpoints of a thread (ids
    are time-ordered) and their pending writes. The latest, and any interrupt on it, are kept."""
    conn, lock = getattr(saver, "conn", None), getattr(saver, "lock", None)
    if conn is None or lock is None:
        return
    with lock:
        old = "SELECT checkpoint_id FROM checkpoints WHERE thread_id = ? ORDER BY checkpoint_id DESC LIMIT -1 OFFSET ?"
        conn.execute(f"DELETE FROM writes WHERE thread_id = ? AND checkpoint_id IN ({old})", (thread_id, thread_id, keep))
        conn.execute(f"DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_id IN ({old})", (thread_id, thread_id, keep))
        conn.commit()


def _brief(args: dict[str, Any]) -> dict[str, Any]:
    """Args for the audit log, with long values (file contents) shortened."""
    def short(v: Any) -> Any:
        return v[:200] + f"... ({len(v)} chars)" if isinstance(v, str) and len(v) > 200 else v

    return {k: short(v) for k, v in args.items()}
