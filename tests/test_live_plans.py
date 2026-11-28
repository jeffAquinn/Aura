"""Live: plans on the local model (docs/orchestration.md, ticket T9). Runs the real model on a scratch
vault and data folder (conftest); every card not named below is denied, so nothing is sent.

    uv run pytest -q -m live tests/test_live_plans.py -s --basetemp /private/tmp/aura-pytest-live-plans

1. "Write a research note on X" gives a valid plan (its plan card) within two make_plan tries, in at
   least 8 of 10 runs. The plan card is denied: no step runs.
2. A Developer job in a vault folder runs with a review step before its file card. Codex does the job
   in the Developer container (it counts against the user's ChatGPT plan); its file card is denied.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from aura.brain import ApprovalNeeded, Aura
from aura.config import Settings

_base_url = Settings().base_url


def _reachable() -> bool:
    try:
        httpx.get(f"{_base_url}/models", timeout=2).raise_for_status()
        return True
    except httpx.HTTPError:
        return False


pytestmark = [pytest.mark.live, pytest.mark.skipif(not _reachable(), reason=f"no LLM at {_base_url}")]

TOPICS = ["tides", "quantum error correction", "the history of the printing press", "coral reef bleaching",
          "how vaccines train the immune system", "the Roman aqueducts", "black holes", "sourdough fermentation",
          "the causes of the 2008 financial crisis", "honeybee navigation"]
THRESHOLD = 8  # of 10 (docs/orchestration.md §7)


def _memory() -> str:
    """Free memory and swap, as macOS reports them (for the log of a live run)."""
    import subprocess

    def run(*cmd: str) -> str:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False).stdout

    free = next((line.split(":")[1].strip() for line in run("memory_pressure").splitlines() if "free percentage" in line), "?")
    return f"RAM free {free}; swap {run('sysctl', '-n', 'vm.swapusage').strip()}"


@pytest.fixture
def aura() -> Aura:
    """Only one local model at a time: two models don't fit in this Mac's memory together, and Ollama
    then swaps them in and out (2026-11-28: Aura's qwen3.5:9b and the test's gemma4:26b stalled for minutes)."""
    import subprocess

    settings = dataclasses.replace(Settings.from_env(), tavily_api_key=None)  # scratch vault and data (conftest)
    root = _base_url.removesuffix("/v1")
    loaded = [m.get("name", "?") for m in httpx.get(f"{root}/api/ps", timeout=3).json().get("models", [])]
    print(f"\n{_memory()}; loaded: {loaded or 'none'}")
    others = [name for name in loaded if name != settings.model]
    if others:
        pytest.skip(f"another local model is loaded ({', '.join(others)}): quit Aura or unload it first")
    if subprocess.run(["pgrep", "-x", "llama-server"], capture_output=True, check=False).returncode == 0:
        pytest.skip("a llama-server is running (Aura's local model): quit Aura first")
    return Aura(settings)


def this_turn(aura: Aura) -> list:
    messages = aura._agent.get_state(aura._config).values["messages"]
    start = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
    return messages[start:]


def test_a_research_note_gets_a_valid_plan_within_two_tries(aura):
    results = []
    for topic in TOPICS:
        aura.new_conversation(end_plan=True)
        began = time.monotonic()
        events = list(aura.stream(f"Write a research note on {topic}."))
        card = events[-1] if events and isinstance(events[-1], ApprovalNeeded) else None
        turn = this_turn(aura)
        tries = sum(c["name"] == "make_plan" for m in turn if isinstance(m, AIMessage) for c in m.tool_calls)
        used = [c["name"] for m in turn if isinstance(m, AIMessage) for c in m.tool_calls]
        refused = [str(m.content)[:160] for m in turn if isinstance(m, ToolMessage) and m.name == "make_plan"]
        planned = card is not None and card.actions[0].tool == "make_plan"
        steps = [f"{s['tool']}" for s in (card.actions[0].plan or {}).get("steps", [])] if planned else []
        results.append((topic, planned and tries <= 2, tries, used, steps, refused, time.monotonic() - began))
        if card is not None:
            list(aura.resume([False] * len(card.actions)))  # deny: no step runs
    for topic, ok, tries, used, steps, refused, took in results:
        print(f"{'PASS' if ok else 'MISS'}  tries={tries} {took:5.0f}s used={used} steps={steps}  {topic}")
        for reason in refused:
            print(f"        refused: {reason}")
    passed = sum(r[1] for r in results)
    print(f"\n{passed}/{len(results)} gave a valid plan within two tries")
    assert passed >= THRESHOLD


def _codex_ready() -> bool:
    from test_developer import _docker_up

    return (Path("~/.codex/auth.json").expanduser()).is_file() and _docker_up()


@pytest.mark.skipif(not _codex_ready(), reason="Codex isn't signed in, or Docker isn't running")
def test_a_developer_job_in_a_vault_folder_runs_after_a_review_step(aura):
    """Approve the plan, Continue at the review, allow the hand-off; the run is over at the first file
    card (denied). Passes when a review card came before that file card."""
    ideas = aura.toolbox.vault.root / "Ideas"
    ideas.mkdir(parents=True, exist_ok=True)
    (ideas / "garden.md").write_text("# Garden\nPlant tomatoes in May.\n")
    prompt = ("Make a plan: first I review the plan's direction, then Codex adds a README.md to my Ideas "
              "folder that lists the notes in it. Put a review step before the Developer step.")
    seen: list[str] = []
    # Not the chat box's engine picker: with it, Aura hands the job straight to the engine (by design,
    # docs/sprint-6-design.md), so no plan is made. The prompt names the engine instead.
    events = list(aura.stream(prompt))
    for _ in range(12):
        card = events[-1] if events and isinstance(events[-1], ApprovalNeeded) else None
        if card is None:
            break
        tools = [a.tool for a in card.actions]
        seen.append("+".join(tools))
        print(f"card: {tools}  {card.actions[0].description.splitlines()[0][:120]}")
        if tools == ["ask_developer"]:
            print("  hand-off:", card.actions[0].args)
        if all(t == "apply_change" for t in tools):  # the Developer's per-file card
            list(aura.resume([False] * len(card.actions)))  # denied: nothing reaches the vault
            break
        answer = [t in ("make_plan", "review_step", "ask_developer") for t in tools]
        events = list(aura.resume(answer))
    said = "".join(e.text for e in events if hasattr(e, "text"))
    print("cards in order:", seen, "\nAura said:", said[-600:])
    review = next((i for i, c in enumerate(seen) if "review_step" in c), None)
    files = next((i for i, c in enumerate(seen) if "apply_change" in c), None)
    assert review is not None and files is not None and review < files, seen

