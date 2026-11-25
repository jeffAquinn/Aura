"""Agent-loop hooks that make Aura's tool use visible and its refusals exact."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import threading
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, ToolCallRequest, hook_config
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.runtime import Runtime

from aura.events import emit
from aura.masking import PATTERNS_ONLY, Masker
from aura.tools import status_line
from aura.tools.web import FALLBACK_NOTICE

DENIAL = "[Action Denied by User]. I will not perform that action. How else can I help?"
# What the model reads in place of the declined tool's result (visible in later turns).
REJECTION_NOTE = "The user declined this action. Do not retry it unless they ask."
MAX_TOOL_OUTPUT = 24_000  # characters of one tool result the model reads (about 6k tokens)
RETRY_LIMIT = 3
RECURSION_MARKER = "aura_recursion_breaker"


def bound(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    """Cut a tool result to `limit`, keeping its start and end, with an explicit marker."""
    if len(text) <= limit:
        return text
    head, tail = text[: limit * 3 // 4], text[-(limit // 4):]
    return f"{head}\n… [cut by Aura: {len(text) - len(head) - len(tail)} characters of this tool's output omitted] …\n{tail}"


def _normalise(value: Any, limit: int = 800) -> str:
    """Make equivalent tool calls/results comparable without retaining an unbounded transcript."""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return " ".join(text.split())[:limit]


@dataclass(frozen=True)
class CircuitBreak:
    """A safe, user-facing record of the repeated operation Aura stopped."""

    tool: str
    command: str
    result: str
    attempts: tuple[str, ...]
    limit: int = RETRY_LIMIT

    @property
    def message(self) -> str:
        attempts = "\n".join(f"{i}. {attempt}" for i, attempt in enumerate(self.attempts, start=1))
        return (
            "HALT: RECURSION DETECTED\n\n"
            f"Aura stopped after {self.limit} consecutive identical tool attempts.\n"
            f"Tool / command: {self.command}\n"
            f"Repeated result or state: {self.result or '(empty result)'}\n\n"
            f"Last {self.limit} attempts:\n{attempts}\n\n"
            "No further automated retries will run in this turn. Please change the approach or provide guidance."
        )


class RecursionCircuitBreaker:
    """Per-turn circuit breaker for a tool call that keeps returning the same state.

    It intentionally keys on the canonical tool arguments *and* its completed result. Three
    reads with changing results are not a loop; three identical failures (or no-progress reads)
    are. The lock also makes this safe if LangGraph executes a batch of tool calls concurrently.
    """

    def __init__(self, limit: int = RETRY_LIMIT):
        if limit < 1:
            raise ValueError("retry limit must be positive")
        self.limit = limit
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self._signature: tuple[str, str, str, str] | None = None
            self._attempts: list[str] = []
            self._tripped = False

    def observe(self, tool: str, args: dict[str, Any], result: ToolMessage) -> CircuitBreak | None:
        """Record one completed tool call and return a halt record on the retry limit."""
        command_args = _normalise(args)
        command = f"{tool}({command_args})" if command_args else f"{tool}()"
        state = _normalise(result.content)
        signature = (tool, command_args, result.status or "success", state)
        attempt = f"{command} → {state or '(empty result)'}"
        with self._lock:
            if self._tripped:
                return None
            if signature != self._signature:
                self._signature, self._attempts = signature, []
            self._attempts.append(attempt)
            if len(self._attempts) < self.limit:
                return None
            self._tripped = True
            return CircuitBreak(tool, command, state, tuple(self._attempts[-self.limit:]), self.limit)


def _is_rejection(m: BaseMessage) -> bool:
    return (
        isinstance(m, ToolMessage)
        and m.additional_kwargs.get("lc_source") == "hitl_interrupt_decision"
        and m.additional_kwargs.get("lc_data", {}).get("decision") == "reject"
    )


class DenialMiddleware(AgentMiddleware):
    """When the user declines every action the model just requested, end the turn with the
    fixed denial line instead of letting the model improvise (no apologies, no retries)."""

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        msgs = state["messages"]
        i = len(msgs)
        while i and isinstance(msgs[i - 1], ToolMessage):
            i -= 1
        results = msgs[i:]  # the tool results answering the latest tool calls
        if results and all(_is_rejection(m) for m in results):
            emit({"reply": DENIAL})
            return {"messages": [AIMessage(DENIAL)], "jump_to": "end"}
        return None


class RecursionMiddleware(AgentMiddleware):
    """End an agent turn as soon as ToolStatusMiddleware marks a recursion circuit break."""

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        for message in reversed(state["messages"]):
            if not isinstance(message, ToolMessage):
                break
            stopped = message.additional_kwargs.get(RECURSION_MARKER)
            if stopped:
                # A custom event makes the exact halt report visible to every interface. Jumping
                # to end prevents another model pass from rationalising its way into a fourth try.
                emit({"reply": stopped})
                return {"messages": [AIMessage(stopped)], "jump_to": "end"}
        return None


class ToolStatusMiddleware(AgentMiddleware):
    """Announce each tool as it actually runs (after any approval), and flag fallback search.
    Every result the model reads is masked (no credential reaches the model, the conversation
    store or a reply) and bounded (a huge output can't flood the context)."""

    def __init__(self, masker: Masker = PATTERNS_ONLY, circuit: RecursionCircuitBreaker | None = None,
                 audit: Any = None):
        super().__init__()
        self.masker = masker
        self.circuit = circuit
        self.audit = audit

    def wrap_tool_call(self, request: ToolCallRequest, handler: Callable) -> Any:
        call = request.tool_call
        if line := status_line(call["name"], call["args"]):  # "" for a tool that announces itself
            emit({"status": line})
        result = handler(request)
        if isinstance(result, ToolMessage):
            if isinstance(result.content, str):
                result.content = bound(self.masker.mask(result.content))
            if isinstance(result.artifact, dict) and result.artifact.get("fallback"):
                emit({"notice": FALLBACK_NOTICE})
            if self.circuit and (stopped := self.circuit.observe(call["name"], call["args"], result)):
                result.additional_kwargs[RECURSION_MARKER] = stopped.message
                if self.audit:
                    self.audit.record("recursion_halted", tool=stopped.tool, command=stopped.command,
                                      result=stopped.result, attempts=list(stopped.attempts))
        return result
