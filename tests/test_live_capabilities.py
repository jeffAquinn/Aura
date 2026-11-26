"""Live: does the local model reach the right tools through find_capabilities when no cue matched?
(Aura 2.0, Pillar 1, T6.) Runs the real model; every approval card is denied, so nothing is sent
or changed. Passes when the right family's tool is called for at least 85% of the prompts.

    uv run pytest -q -m live tests/test_live_capabilities.py -s --basetemp /private/tmp/aura-pytest-live-cap
"""

from __future__ import annotations

import dataclasses

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from aura.brain import Aura
from aura.capabilities import BY_NAME, select
from aura.config import Settings

_base_url = Settings().base_url


def _reachable() -> bool:
    try:
        httpx.get(f"{_base_url}/models", timeout=2).raise_for_status()
        return True
    except httpx.HTTPError:
        return False


pytestmark = [pytest.mark.live, pytest.mark.skipif(not _reachable(), reason=f"no LLM at {_base_url}")]

# Each needs a tool, and none contains a cue (checked below), so only find_capabilities can help.
PROMPTS = [
    ("Which accents do you have?", "Voice"),
    ("Change the way you sound.", "Voice"),
    ("What's in Map_of_the_Ascent.md?", "Files"),
    ("Show me what's inside the Conversations area.", "Files"),
    ("Create a new area called Ideas.", "Files"),
    ("Throw away old.txt.", "Files"),
    ("How hot is it in Tokyo right now?", "Web"),
    ("Who won the football final last night?", "Web"),
    ("What's the exchange rate of euros to yen?", "Web"),
    ("What's 2 to the power of 64 exactly? Run it to be sure.", "Code"),
    ("Draw a graph of y = x squared.", "Code"),
    ("Make your replies shorter from now on.", "Persona"),
    ("Be more formal with me.", "Persona"),
    ("What's your personality like? Show me your settings.", "Persona"),
    ("What add-ons have you got installed?", "Workshop"),
    ("What agents are registered on the workstation?", "Workshop"),
    ("Which accounts are linked to you?", "Connections"),
    ("What do my study materials say about Plato?", "Knowledge"),
    ("Give the Developer this job: tidy the Ideas area.", "Developer"),
    ("Sound like a British woman, please.", "Voice"),
]
THRESHOLD = 0.85  # Aura's bar, kept (2026-11-26). Baseline: 70% (14/20); see docs/aura-2-plan.md, T6.


@pytest.fixture
def aura() -> Aura:
    a = Aura(dataclasses.replace(Settings.from_env(), tavily_api_key=None))  # the vault is a temp dir (conftest)
    (a.toolbox.vault.root / "old.txt").write_text("keep me")
    return a


def turn_calls(aura: Aura) -> list[dict]:
    msgs = aura._agent.get_state(aura._config).values["messages"]
    last_user = max(i for i, m in enumerate(msgs) if isinstance(m, HumanMessage))
    return [c for m in msgs[last_user:] if isinstance(m, AIMessage) for c in m.tool_calls]


def test_the_prompts_really_match_no_cue():
    assert [p for p, _ in PROMPTS if select(BY_NAME, p)] == []


def test_find_capabilities_reaches_the_right_family(aura):
    results = []
    for prompt, family in PROMPTS:
        aura.new_conversation()
        aura.chat(prompt)  # every approval card is denied: nothing is sent or changed
        calls = turn_calls(aura)
        loaded = [c["args"].get("family", "") for c in calls if c["name"] == "find_capabilities"]
        used = [c["name"] for c in calls if c["name"] != "find_capabilities"]
        hit = any(BY_NAME.get(name) is not None and BY_NAME[name].family == family for name in used)
        results.append((prompt, family, loaded, used, hit))
    for prompt, family, loaded, used, hit in results:
        print(f"{'PASS' if hit else 'MISS'}  {family:<12} loaded={loaded} used={used}  {prompt}")
    rate = sum(r[-1] for r in results) / len(results)
    print(f"\n{rate:.0%} reached the right family ({sum(r[-1] for r in results)}/{len(results)})")
    assert rate >= THRESHOLD
