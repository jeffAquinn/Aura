"""Sprint 1 acceptance tests, grouped by Gemma's review checkpoints.

Uses a recording fake model, so these run offline in milliseconds. Live checks against the
real LLM are in tests/test_live.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from aura.brain import Aura
from aura.config import Settings


class RecordingModel(BaseChatModel):
    """Replies 'reply N' and records every message list it is shown.

    With report_usage=True it also reports token usage the way a real server does: counting
    the *whole* prompt, system prompt included.
    """

    calls: list[list[BaseMessage]] = Field(default_factory=list)
    report_usage: bool = False

    @property
    def _llm_type(self) -> str:
        return "recording"

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kw: Any):
        self.calls.append(list(messages))
        reply = AIMessage(content=f"reply {len(self.calls)}")
        if self.report_usage:
            used = count_tokens_approximately(messages)
            reply.usage_metadata = {"input_tokens": used, "output_tokens": 2, "total_tokens": used + 2}
            reply.response_metadata = {"model_provider": self._get_ls_params()["ls_provider"]}
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def agent_calls(self) -> list[list[BaseMessage]]:
        """Calls made by Aura's agent loop (as opposed to the memory summarizer)."""
        return [c for c in self.calls if isinstance(c[0], SystemMessage) and "You are Aura" in c[0].text]


def make_aura(*, report_usage: bool = False, **settings: Any) -> tuple[Aura, RecordingModel]:
    model = RecordingModel(report_usage=report_usage)
    return Aura(settings=Settings(**settings), model=model), model


def texts(messages: list[BaseMessage]) -> list[str]:
    return [m.text for m in messages]


def history(aura: Aura) -> list[BaseMessage]:
    return aura._agent.get_state(aura._config).values.get("messages", [])


def run_turns(aura: Aura, model: RecordingModel, n: int) -> list[tuple[int, bool]]:
    """Chat n turns; per turn return (estimated history tokens, whether compaction ran)."""
    turns = []
    for i in range(n):
        prompt = f"Fact {i}: " + "detail " * 20
        size = count_tokens_approximately([*history(aura), HumanMessage(prompt)])
        before = len(model.calls)
        aura.chat(prompt)
        turns.append((size, len(model.calls) - before > 1))  # 2 calls = summarizer + reply
    return turns


# ── Integrity: persona initialization prevents model identity leakage ──────────────────────


def test_persona_is_the_first_thing_the_model_sees():
    aura, model = make_aura()
    aura.chat("Hello")

    first = model.calls[0][0]
    assert isinstance(first, SystemMessage)
    assert "You are Aura" in first.text
    assert "Concise" in first.text and "Sophisticated" in first.text
    # Without an explicit identity rule, the engine answers "I am Gemma" (seen live, 6/6).
    assert "never as the engine" in first.text
    assert "gemma4:26b" in first.text  # ...but stays honest about what powers it, if asked.


# ── Robustness: .env configuration flows into the logic ─────────────────────────────────────


def test_settings_come_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AURA_BASE_URL", "http://example.local/v1")
    monkeypatch.setenv("AURA_API_KEY", "k")
    monkeypatch.setenv("AURA_MODEL", "some-model")
    monkeypatch.setenv("AURA_REASONING_EFFORT", "")  # empty -> omit the parameter
    monkeypatch.setenv("AURA_CONTEXT_TOKENS", "4096")

    assert Settings.from_env() == Settings(
        base_url="http://example.local/v1",
        api_key="k",
        model="some-model",
        reasoning_effort=None,
        context_tokens=4096,
    )


# ── Reliability: compaction triggers on token count, and the window is never exceeded ─────


def test_compaction_triggers_at_60_percent_of_context_budget():
    aura, model = make_aura(context_tokens=1000)  # -> compact at 600 tokens, keep 300
    turns = run_turns(aura, model, 25)

    for i, (size, compacted) in enumerate(turns):
        assert compacted == (size >= 600), f"turn {i}: {size} tokens, compacted={compacted}"
    assert sum(c for _, c in turns) >= 2  # fired, and fired again after re-growing
    for call in model.agent_calls():
        assert count_tokens_approximately(call[1:]) < 600  # history never reaches the trigger


def test_compaction_counts_tokens_the_local_estimate_cannot_see():
    # The local estimate counts only the conversation. The server's reported usage also counts
    # the system prompt (and, from Sprint 2, tool schemas), so compaction must start earlier.
    aura, model = make_aura(context_tokens=1000, report_usage=True)
    turns = run_turns(aura, model, 15)

    first = next(i for i, (_, compacted) in enumerate(turns) if compacted)
    assert turns[first][0] < 600  # the conversation-only estimate alone would not have fired
    for call in model.agent_calls():
        assert count_tokens_approximately(call) < 1000  # whole prompt, system prompt included


def test_compaction_folds_old_turns_into_a_summary():
    aura, model = make_aura(context_tokens=200)
    run_turns(aura, model, 8)

    last_prompt = " ".join(texts(model.agent_calls()[-1]))
    assert "Fact 7" in last_prompt  # recent turns kept verbatim
    assert "Fact 0" not in last_prompt  # oldest turns folded away...
    assert "summary" in last_prompt.lower()  # ...into a summary, rather than silently dropped


def test_streaming_shows_only_auras_reply_during_compaction():
    aura, model = make_aura(context_tokens=200)
    run_turns(aura, model, 4)
    before = len(model.calls)

    out = "".join(aura.stream("One more."))
    assert len(model.calls) - before == 2  # this turn ran the summarizer, then Aura
    assert out == f"reply {len(model.calls)}"  # and only Aura's reply reached the screen


# ── Memory: Aura remembers the conversation ─────────────────────────────────────────────────


def test_remembers_previous_turns():
    aura, model = make_aura()
    assert aura.chat("My name is Ada.") == "reply 1"
    assert aura.chat("What is my name?") == "reply 2"

    # The second call must include the full first exchange, in order.
    assert texts(model.calls[1][1:]) == ["My name is Ada.", "reply 1", "What is my name?"]


def test_streamed_replies_are_remembered_too():
    aura, model = make_aura()
    assert "".join(aura.stream("Remember: the code is 42.")) == "reply 1"
    aura.chat("What was the code?")

    assert texts(model.calls[1][1:3]) == ["Remember: the code is 42.", "reply 1"]


def test_new_conversation_starts_clean():
    aura, model = make_aura()
    aura.chat("My name is Ada.")
    aura.new_conversation()
    aura.chat("What is my name?")

    assert texts(model.calls[1][1:]) == ["What is my name?"]
