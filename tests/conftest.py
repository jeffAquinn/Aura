"""Shared fixtures. Every test gets a throwaway vault and audit log; nothing touches the real ones."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from aura.config import Settings


@pytest.fixture(autouse=True)
def _isolate_from_real_vault(tmp_path, monkeypatch):
    # Settings.from_env() (used by live tests) must never resolve to the real vault or log.
    monkeypatch.setenv("AURA_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("AURA_AUDIT_LOG", str(tmp_path / "audit.jsonl"))


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(vault_dir=tmp_path / "vault", audit_log=tmp_path / "audit.jsonl")


def call(name: str, **args: Any) -> dict:
    return {"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:8]}"}


class ScriptedModel(BaseChatModel):
    """Plays back scripted replies (e.g. tool calls), then answers 'reply N'. Records every
    message list it is shown. With report_usage=True it reports token usage the way a real
    server does: counting the *whole* prompt, system prompt included."""

    script: list[AIMessage] = Field(default_factory=list)
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    report_usage: bool = False

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedModel:
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kw: Any):
        self.calls.append(list(messages))
        is_agent = isinstance(messages[0], SystemMessage) and "You are Aura" in messages[0].text
        if self.script and is_agent:
            reply = self.script.pop(0)
        else:
            reply = AIMessage(content=f"reply {len(self.calls)}")
        if self.report_usage:
            used = count_tokens_approximately(messages)
            reply.usage_metadata = {"input_tokens": used, "output_tokens": 2, "total_tokens": used + 2}
            reply.response_metadata = {"model_provider": self._get_ls_params()["ls_provider"]}
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def agent_calls(self) -> list[list[BaseMessage]]:
        """Calls made by Aura's agent loop (as opposed to the memory summarizer)."""
        return [c for c in self.calls if isinstance(c[0], SystemMessage) and "You are Aura" in c[0].text]
