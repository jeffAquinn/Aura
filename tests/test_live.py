"""Live checks against the configured LLM (Ollama by default). Skipped if it isn't reachable.

Run with: uv run pytest tests/test_live.py -s
"""

from __future__ import annotations

import dataclasses

import httpx
import pytest

from aura.brain import Aura
from aura.config import Settings

settings = Settings.from_env()


def _reachable() -> bool:
    try:
        httpx.get(f"{settings.base_url}/models", timeout=2).raise_for_status()
        return True
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason=f"no LLM at {settings.base_url}")


@pytest.mark.parametrize(
    "question", ["Who are you, in one sentence?", "Introduce yourself briefly.", "What's your name?"]
)
def test_identity_is_aura_not_the_engine(question: str):
    reply = Aura(settings).chat(question)
    print(f"\n{question!r} -> {reply}")
    assert "aura" in reply.lower()
    assert "gemma" not in reply.lower() and "google" not in reply.lower()


def test_engine_is_disclosed_when_asked():
    reply = Aura(settings).chat("Specifically, which language model powers you?")
    print(f"\n{reply}")
    assert "gemma" in reply.lower()


def test_remembers_within_a_conversation():
    aura = Aura(settings)
    aura.chat("My favorite color is ultramarine. Just acknowledge it.")
    recall = aura.chat("What's my favorite color? One word.")
    print(f"\n{recall}")
    assert "ultramarine" in recall.lower()


def test_streamed_replies_report_server_token_usage():
    # Compaction relies on this; without stream_usage the server sends no counts when streaming.
    aura = Aura(settings)
    "".join(aura.stream("Say hello."))
    last = aura._agent.get_state(aura._config).values["messages"][-1]
    assert last.usage_metadata and last.usage_metadata["total_tokens"] > 0


def test_remembers_facts_from_before_compaction():
    # A small budget forces the real model to summarize; the early fact must survive it.
    aura = Aura(dataclasses.replace(settings, context_tokens=1500))
    aura.chat("Please remember this: my locker code is 7319. Just acknowledge.")
    for i in range(8):
        aura.chat(f"Note {i}, no need to respond beyond 'noted': " + "background filler " * 30)

    summary, *rest = aura._agent.get_state(aura._config).values["messages"]
    assert summary.additional_kwargs.get("lc_source") == "summarization"  # compaction ran...
    assert not any("7319" in m.text for m in rest)  # ...and the original message is gone...
    recall = aura.chat("What is my locker code? Digits only.")
    print(f"\nafter compaction: {recall}")
    assert "7319" in recall  # ...but the fact lives on in the summary
