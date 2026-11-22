"""Live checks against the configured LLM (Ollama by default). Skipped if it isn't reachable.

Run with: uv run pytest tests/test_live.py -s
"""

from __future__ import annotations

import dataclasses
import re

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from aura.brain import Aura, Status, Text
from aura.config import Settings
from aura.middleware import DENIAL
from aura.tools.web import FALLBACK_NOTICE

_base_url = Settings().base_url


def _reachable() -> bool:
    try:
        httpx.get(f"{_base_url}/models", timeout=2).raise_for_status()
        return True
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason=f"no LLM at {_base_url}")


@pytest.fixture
def settings() -> Settings:
    # From env like the real app, but conftest has pointed the vault and log at a temp dir.
    return Settings.from_env()


@pytest.fixture
def aura(settings) -> Aura:
    a = Aura(settings)
    v = a.toolbox.vault.root
    (v / "notes.txt").write_text("Buy ultramarine paint\nCall the framer\n")
    (v / "budget.csv").write_text("item,amount\nrent,1200\nfood,450\nfun,150\n")
    (v / "old.txt").write_text("keep me")
    return a


def tools_called(aura: Aura) -> list[str]:
    """Tool calls the model made since the user's last message."""
    msgs = aura._agent.get_state(aura._config).values["messages"]
    last_user = max(i for i, m in enumerate(msgs) if isinstance(m, HumanMessage))
    return [c["name"] for m in msgs[last_user:] if isinstance(m, AIMessage) for c in m.tool_calls]


# ── Sprint 1: identity and memory ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "question", ["Who are you, in one sentence?", "Introduce yourself briefly.", "What's your name?"]
)
def test_identity_is_aura_not_the_engine(aura, question):
    reply = aura.chat(question)
    print(f"\n{question!r} -> {reply}")
    assert "aura" in reply.lower()
    assert "gemma" not in reply.lower() and "google" not in reply.lower()


def test_engine_is_disclosed_when_asked(aura):
    reply = aura.chat("Specifically, which language model powers you?")
    print(f"\n{reply}")
    assert "gemma" in reply.lower()


def test_remembers_within_a_conversation(aura):
    aura.chat("My favorite color is ultramarine. Just acknowledge it.")
    recall = aura.chat("What's my favorite color? One word.")
    print(f"\n{recall}")
    assert "ultramarine" in recall.lower()


def test_streamed_replies_report_server_token_usage(aura):
    # Compaction relies on this; without stream_usage the server sends no counts when streaming.
    list(aura.stream("Say hello."))
    last = aura._agent.get_state(aura._config).values["messages"][-1]
    print(f"\nprompt tokens incl. system prompt + 7 tool schemas: {last.usage_metadata['input_tokens']}")
    assert last.usage_metadata and last.usage_metadata["total_tokens"] > 0


def test_remembers_facts_from_before_compaction(settings):
    # A small budget forces the real model to summarize; the early fact must survive it.
    aura = Aura(dataclasses.replace(settings, context_tokens=3000))
    aura.chat("Please remember this: my locker code is 7319. Just acknowledge.")
    for i in range(10):
        aura.chat(f"Note {i}, no need to respond beyond 'noted': " + "background filler " * 30)

    summary, *rest = aura._agent.get_state(aura._config).values["messages"]
    assert summary.additional_kwargs.get("lc_source") == "summarization"  # compaction ran...
    assert not any("7319" in m.text for m in rest)  # ...and the original message is gone...
    recall = aura.chat("What is my locker code? Digits only.")
    print(f"\nafter compaction: {recall}")
    assert "7319" in recall  # ...but the fact lives on in the summary


# ── Sprint 2: tool selection accuracy ───────────────────────────────────────────────────────

CASES = [
    ("What's the weather in London right now?", {"web_search"}, None),
    ("What files are in my vault?", {"list_vault"}, "budget.csv"),
    ("Read notes.txt and tell me its first line.", {"read_file"}, "ultramarine"),
    ("Create a folder named 'Project Aura'.", {"create_folder"}, None),
    ("Use Python to compute the 30th Fibonacci number.", {"run_python"}, "832040"),
    ("Using pandas, what's the average amount in budget.csv?", {"run_python"}, "600"),
    ("Hi! In one sentence, how are you?", set(), None),
]
GATED = {"write_file", "create_folder", "delete_path"}


@pytest.mark.parametrize(("prompt", "expected", "answer"), CASES, ids=[c[0][:40] for c in CASES])
def test_tool_selection(aura, prompt, expected, answer):
    reply = aura.chat(prompt, approve=lambda action: True)
    called = tools_called(aura)
    print(f"\n{prompt!r}\n  tools: {called}\n  reply: {reply[:300]}")

    assert expected <= set(called), f"expected {expected}, model called {called}"
    if not expected:
        assert called == []
    assert not (set(called) & GATED) - expected  # never an unrequested change to the vault
    if answer:
        assert answer in reply.replace(",", "")
    if "Project Aura" in prompt:
        assert (aura.toolbox.vault.root / "Project Aura").is_dir()


# ── Sprint 2: protocols, end to end ─────────────────────────────────────────────────────────


def test_status_lines_show_what_aura_is_doing(aura):
    events = list(aura.stream("Read notes.txt and tell me its first line."))
    statuses = [e.text for e in events if isinstance(e, Status)]
    print(f"\n{statuses}")
    assert "[Tool: File_System_Tool] Reading 'notes.txt'..." in statuses


def test_denied_delete_gets_the_exact_denial_and_keeps_the_file(aura):
    reply = aura.chat("Delete old.txt from my vault.", approve=lambda action: False)
    print(f"\n{reply}")
    assert "delete_path" in tools_called(aura)
    assert reply == DENIAL
    assert (aura.toolbox.vault.root / "old.txt").read_text() == "keep me"


@pytest.mark.skipif(bool(Settings.from_env().tavily_api_key), reason="Tavily key set: no fallback")
def test_fallback_search_is_announced(aura):
    events = list(aura.stream("What's the weather in London right now?"))
    reply = "".join(e.text for e in events if isinstance(e, Text))
    print(f"\n{reply}")
    assert reply.startswith(FALLBACK_NOTICE)
    assert "http" in reply  # cites a source


@pytest.mark.skipif(not Settings.from_env().tavily_api_key, reason="no TAVILY_API_KEY")
def test_primary_search_bypasses_the_fallback(aura):
    events = list(aura.stream("What's the weather in London right now?"))
    reply = "".join(e.text for e in events if isinstance(e, Text))
    msgs = aura._agent.get_state(aura._config).values["messages"]
    searches = [m for m in msgs if isinstance(m, ToolMessage) and m.name == "web_search"]
    print(f"\n{reply}\n  backends: {[m.artifact for m in searches]}")

    assert searches and all(m.artifact == {"backend": "tavily", "fallback": False} for m in searches)
    assert FALLBACK_NOTICE not in reply
    assert not any(e["event"] == "search_fallback" for e in aura.toolbox.audit.entries())
    assert "http" in reply  # cites a source
    # Grounded: every temperature Aura states appears in what Tavily returned. Compare the
    # numbers alone, since results may be structured data ("temp_c": 5.3) without a degree sign.
    temps = re.findall(r"(-?\d+(?:\.\d+)?)\s?°", reply)
    results = " ".join(m.text for m in searches)
    assert temps, "no temperature in the reply"
    assert all(re.search(rf"(?<![\d.]){re.escape(t)}(?![\d])", results) for t in temps), temps


def test_many_facts_survive_repeated_compaction_at_full_budget(settings):
    # The real 16k window, three compactions. Sprint 2 as first submitted (LangChain's default
    # prompt + 4k pre-trim) recalled 0/7 of these here; lossless prompt + budget-sized trim: 7/7.
    aura = Aura(settings)
    facts = [
        "Please remember: my locker code is 7319.",
        "Our project is called 'Bluebird Atlas'.",
        "My flight is on March 14 at 06:45, from gate B22.",
        "The trip budget cap is $2,375.50.",
        "Constraint: never schedule anything for me before 9am on weekdays.",
    ]
    filler = iter(f"Note {n}, reply only 'noted': " + "background filler " * 150 for n in range(40))
    for fact in facts:
        aura.chat(fact + " Just acknowledge.")
        aura.chat(next(filler))
        aura.chat(next(filler))
    for _ in range(22):
        aura.chat(next(filler))

    msgs = aura._agent.get_state(aura._config).values["messages"]
    assert msgs[0].additional_kwargs.get("lc_source") == "summarization"
    assert not any("7319" in m.text for m in msgs[1:])  # originals long gone from context
    reply = aura.chat(
        "Without using any tools, list: my locker code; the project name; my flight date, time "
        "and gate; the trip budget cap; and my scheduling constraint."
    )
    print(f"\n{reply}")
    low = reply.lower()
    for expected in ["7319", "bluebird atlas", "march 14", "6:45", "b22", "2,375.50", "weekday"]:
        assert expected in low, expected
    assert re.search(r"\b9\s?(:00\s?)?(am|a\.m\.)", low)
