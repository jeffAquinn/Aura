"""Aura's brain: the LLM, the persona, and conversation memory wired into one agent loop.

The loop is LangChain's `create_agent` (built on LangGraph). With no tools it is a plain chat
loop; in Sprint 2 tools are added to the same agent and it becomes a reason → act → observe loop
without any restructuring. Interfaces (terminal now, menu bar later) only talk to `Aura`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, SummarizationMiddleware, dynamic_prompt
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from aura.config import Settings
from aura.persona import system_prompt


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
    def __init__(self, settings: Settings | None = None, model: BaseChatModel | None = None):
        self.settings = settings or Settings.from_env()
        llm = model or build_model(self.settings)
        budget = self.settings.context_tokens

        @dynamic_prompt
        def persona(request: ModelRequest) -> str:
            # Rendered per call so a long-running Aura (e.g. resident in the menu bar) never has
            # a stale date. Date only, not time: the prompt prefix stays stable between turns,
            # so the server's prompt cache stays warm.
            return system_prompt(today=date.today(), engine=self.settings.model)

        self._agent = create_agent(
            model=llm,
            tools=[],  # Sprint 2: Aura's hands go here.
            middleware=[
                persona,
                # Short-term memory compaction: when the history nears the server's context
                # window, older turns are folded into a summary instead of silently falling off.
                SummarizationMiddleware(
                    model=llm,
                    trigger=("tokens", int(budget * 0.6)),
                    keep=("tokens", int(budget * 0.3)),
                ),
            ],
            # Conversation memory: every turn is checkpointed per thread_id and replayed to the
            # model on the next turn. In-process for now; swap for a SQLite saver to persist.
            checkpointer=InMemorySaver(),
        )
        self.new_conversation()

    def new_conversation(self) -> None:
        self.thread_id = uuid.uuid4().hex

    @property
    def _config(self) -> RunnableConfig:
        return {"configurable": {"thread_id": self.thread_id}}

    @staticmethod
    def _input(text: str) -> dict:
        return {"messages": [{"role": "user", "content": text}]}

    def chat(self, text: str) -> str:
        """Send a message and return Aura's full reply."""
        result = self._agent.invoke(self._input(text), self._config)
        return result["messages"][-1].text

    def stream(self, text: str) -> Iterator[str]:
        """Send a message and yield Aura's reply as it is generated."""
        for chunk, meta in self._agent.stream(
            self._input(text), self._config, stream_mode="messages"
        ):
            # Only the agent's own model node; skips e.g. the summarizer's internal LLM call.
            # Streaming backends emit AIMessageChunks; non-streaming ones emit one AIMessage.
            if meta.get("langgraph_node") == "model" and isinstance(chunk, AIMessage):
                if chunk.text:
                    yield chunk.text
