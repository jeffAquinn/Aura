"""Agent-loop hooks that make Aura's tool use visible and its refusals exact."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, ToolCallRequest, hook_config
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.runtime import Runtime

from aura.events import emit
from aura.tools import status_line
from aura.tools.web import FALLBACK_NOTICE

DENIAL = "[Action Denied by User]. I will not perform that action. How else can I help?"
# What the model reads in place of the declined tool's result (visible in later turns).
REJECTION_NOTE = "The user declined this action. Do not retry it unless they ask."


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


class ToolStatusMiddleware(AgentMiddleware):
    """Announce each tool as it actually runs (after any approval), and flag fallback search."""

    def wrap_tool_call(self, request: ToolCallRequest, handler: Callable) -> Any:
        call = request.tool_call
        emit({"status": status_line(call["name"], call["args"])})
        result = handler(request)
        if isinstance(result, ToolMessage) and isinstance(result.artifact, dict):
            if result.artifact.get("fallback"):
                emit({"notice": FALLBACK_NOTICE})
        return result
