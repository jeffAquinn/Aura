"""Shared fixtures. Every test gets a throwaway vault and audit log; nothing touches the real ones."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from aura.audit import AuditLog
from aura.brain import Aura
from aura.config import Settings
from aura.tools import Toolbox
from aura.tools.web import Hit, WebSearch


class MemoryKeyring:
    """Stands in for the macOS Keychain: tests never read or write the real one."""

    def __init__(self):
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service, name):
        return self.items.get((service, name))

    def set_password(self, service, name, value):
        self.items[(service, name)] = value

    def delete_password(self, service, name):
        if (service, name) not in self.items:
            raise KeyError(name)
        del self.items[(service, name)]


def real_key(name: str) -> str | None:
    """A key from the real macOS Keychain, where Aura's Settings → Keys stores it: for live tests
    only. Call it inside a test or fixture, never at import, so offline runs and the Governor's
    suite gate never touch the Keychain."""
    try:
        from keyring.backends.macOS import Keyring

        return Keyring().get_password("local.aura", name) or None
    except Exception:  # noqa: BLE001: no Keychain, locked, or not on macOS
        return None


@pytest.fixture(autouse=True)
def _isolate_from_real_keychain(monkeypatch):
    import keyring

    monkeypatch.setattr(keyring, "get_keyring", lambda: MemoryKeyring())


@pytest.fixture(autouse=True)
def _isolate_from_real_vault(tmp_path, monkeypatch):
    # Settings.from_env() (used by live tests) must never resolve to the real vault or log.
    monkeypatch.setenv("AURA_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("AURA_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AURA_DEV_DIR", str(tmp_path / "dev"))


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(vault_dir=tmp_path / "vault", audit_log=tmp_path / "audit.jsonl", data_dir=tmp_path / "data",
                    dev_dir=tmp_path / "dev")


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


def fake_ddg(query: str, n: int) -> list[Hit]:
    return [Hit("London weather", "https://example.com/london", "12°C and light rain.")]


def make_aura(
    settings: Settings, *script: AIMessage, report_usage: bool = False, tavily=None, fresh: bool = True,
    **overrides: Any,
) -> tuple[Aura, ScriptedModel]:
    """`fresh`: start a new conversation even if the data folder has one saved (a test that builds
    several Auras in one folder wants each to start clean; a restart test passes False)."""
    s = dataclasses.replace(settings, **overrides)
    search = WebSearch(s.tavily_api_key, AuditLog(s.audit_log), tavily=tavily, duckduckgo=fake_ddg)
    model = ScriptedModel(script=list(script), report_usage=report_usage)
    aura = Aura(settings=s, model=model, toolbox=Toolbox.from_settings(s, search=search))
    if fresh:
        aura.new_conversation()
    return aura, model


def tool_turn(*calls: dict) -> AIMessage:
    return AIMessage(content="", tool_calls=list(calls))
