"""Agent-loop tests (offline, deterministic), grouped by Gemma's review checkpoints."""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path
from typing import Any

import pytest
from conftest import ScriptedModel, call, make_aura, tool_turn
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from aura.audit import AuditLog
from aura.tools import Toolbox
from aura.brain import ApprovalNeeded, Aura, Event, Status, Text
from aura.config import Settings
from aura.middleware import DENIAL, RETRY_LIMIT, ProviderThinkingSanitizer, without_provider_thinking
from aura.skills import content_hash
from aura.tools.web import FALLBACK_NOTICE, Hit, WebSearch


def said(events: list[Event]) -> str:
    return "".join(e.text for e in events if isinstance(e, Text))


def statuses(events: list[Event]) -> list[str]:
    return [e.text for e in events if isinstance(e, Status)]


def texts(messages: list[BaseMessage]) -> list[str]:
    return [m.text for m in messages]


def history(aura: Aura) -> list[BaseMessage]:
    return aura._agent.get_state(aura._config).values.get("messages", [])


# ── Sprint 1 · Integrity: persona initialization prevents model identity leakage ──────────


def test_persona_is_the_first_thing_the_model_sees(settings):
    aura, model = make_aura(settings)
    aura.chat("Hello")

    first = model.calls[0][0]
    assert isinstance(first, SystemMessage)
    assert "You are Aura" in first.text
    assert "Concise" in first.text and "Sophisticated" in first.text
    # Without an explicit identity rule, the engine answers "I am Gemma" (seen live, 6/6).
    assert "never as the engine" in first.text
    assert "gemma4:26b" in first.text  # ...but stays honest about what powers it, if asked.


def test_persona_describes_the_tools_and_their_limits(settings):
    aura, model = make_aura(settings)
    aura.chat("Hello")

    prompt = model.calls[0][0].text
    assert str(settings.vault_dir.name) in prompt
    assert "read-only at /vault" in prompt and "no internet" in prompt
    assert "Aura project sandbox" in prompt and "never update the root project or GitHub" in prompt
    assert "never ask for permission in chat" in prompt  # no double confirmation
    assert "Never say an action happened unless a tool result confirms it" in prompt
    assert "use English for every user-facing chat reply" in prompt


def test_live_skill_registry_prompt_overrides_stale_conversation_claims(settings):
    aura, model = make_aura(settings)
    example = Path(__file__).resolve().parents[1] / "examples" / "skills" / "pdf_text"
    live = aura.toolbox.skills.dir / "pdf_text"
    if not live.exists():
        shutil.copytree(example, live)
    aura.toolbox.skills.approve("pdf_text", content_hash(live), "1.0.0", "aura-deps:test")

    aura.chat("Use the installed PDF skill.")

    prompt = model.agent_calls()[0][0].text
    assert "Active and callable now: pdf_text" in prompt
    assert "Under audit: none" in prompt
    assert "overrides chat history" in prompt
    assert "Do not invent files" in prompt


def test_provider_thinking_blocks_are_removed_without_losing_text_or_tool_calls(settings):
    message = AIMessage(
        content=[
            {"type": "thinking", "thinking": "private reasoning", "signature": "signed-by-provider"},
            {"type": "reasoning", "reasoning": "normalised private reasoning", "extras": {"signature": "also-signed"}},
            {"type": "text", "text": "Visible answer."},
            {"type": "redacted_thinking", "data": "opaque"},
        ],
        tool_calls=[call("list_vault", path="")],
    )

    cleaned = without_provider_thinking(message)

    assert isinstance(cleaned, AIMessage)
    assert cleaned.content == [{"type": "text", "text": "Visible answer."}]
    assert cleaned.tool_calls == message.tool_calls


def test_provider_thinking_blocks_never_reach_a_durable_checkpoint(settings):
    reply = AIMessage(content=[
        {"type": "thinking", "thinking": "private reasoning", "signature": "provider-bound"},
        {"type": "text", "text": "Visible answer."},
    ])
    aura, _ = make_aura(settings, reply)

    aura.chat("Hello.")

    saved = next(message for message in history(aura) if isinstance(message, AIMessage))
    assert saved.content == [{"type": "text", "text": "Visible answer."}]


def test_thinking_sanitizer_repairs_an_old_checkpoint_before_a_model_reads_it(settings):
    stale = AIMessage(content=[
        {"type": "thinking", "thinking": "private", "signature": "no-longer-valid"},
        {"type": "text", "text": "Previous visible answer."},
    ])
    aura, model = make_aura(settings, AIMessage("Fresh answer."))
    # This represents a checkpoint written before ProviderThinkingSanitizer was introduced.
    aura._agent.update_state(aura._config, {"messages": [stale]}, as_node="model")

    assert ProviderThinkingSanitizer().before_model(aura._agent.get_state(aura._config).values, None)
    aura.chat("Continue safely.")

    seen = model.agent_calls()[0]
    previous = next(message for message in seen if isinstance(message, AIMessage) and message.text == "Previous visible answer.")
    assert previous.content == [{"type": "text", "text": "Previous visible answer."}]


def test_repeated_identical_tool_result_halts_the_turn_after_three_attempts(settings):
    repeated = [tool_turn(call("list_vault", path="")) for _ in range(RETRY_LIMIT)]
    aura, model = make_aura(settings, *repeated, AIMessage("This fourth model reply must not run."))

    reply = aura.chat("List my vault until it works.")

    assert reply.startswith("HALT: RECURSION DETECTED")
    assert "list_vault" in reply
    assert f"Last {RETRY_LIMIT} attempts" in reply
    # The guard ends the graph before it can ask the model to issue another command.
    assert len(model.agent_calls()) == RETRY_LIMIT
    assert aura.toolbox.audit.entries()[-1]["event"] == "recursion_halted"


# ── Sprint 1 · Robustness: .env configuration flows into the logic ─────────────────────────


def test_every_model_call_has_a_length_cap():
    from aura.brain import build_model

    assert build_model(Settings()).max_tokens == 8192  # a looping model stops instead of running forever
    assert build_model(Settings(max_reply_tokens=2048)).max_tokens == 2048


def test_settings_come_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    env = {
        "AURA_BASE_URL": "http://example.local/v1",
        "AURA_API_KEY": "k",
        "AURA_MODEL": "some-model",
        "AURA_REASONING_EFFORT": "",  # empty -> omit the parameter
        "AURA_CONTEXT_TOKENS": "4096",
        "AURA_MODEL_KEEP_ALIVE": "45s",
        "AURA_MAX_REPLY_TOKENS": "2048",
        "AURA_VAULT_DIR": str(tmp_path / "v"),
        "TAVILY_API_KEY": "tvly-x",
        "AURA_SANDBOX_IMAGE": "img:2",
        "AURA_SANDBOX_TIMEOUT": "12",
        "AURA_AUDIT_LOG": str(tmp_path / "a.jsonl"),
        "AURA_DATA_DIR": str(tmp_path / "d"),
        "AURA_DEV_DIR": str(tmp_path / "dev"),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    assert Settings.from_env() == Settings(
        base_url="http://example.local/v1",
        api_key="k",
        model="some-model",
        reasoning_effort=None,
        context_tokens=4096,
        model_keep_alive="45s",
        max_reply_tokens=2048,
        vault_dir=tmp_path / "v",
        tavily_api_key="tvly-x",
        sandbox_image="img:2",
        sandbox_timeout=12.0,
        audit_log=tmp_path / "a.jsonl",
        data_dir=tmp_path / "d",
        dev_dir=tmp_path / "dev",
    )


def test_vault_is_created_on_startup(settings):
    assert not settings.vault_dir.exists()
    make_aura(settings)
    assert settings.vault_dir.is_dir()


def test_project_sandbox_is_seeded_in_the_vault_on_startup(settings, tmp_path):
    source = tmp_path / "Aura"
    (source / "src").mkdir(parents=True)
    (source / "src" / "feature.py").write_text("enabled = False\n")
    aura, _ = make_aura(dataclasses.replace(settings, project_dir=source))

    sandbox = aura.settings.project_sandbox_dir
    assert sandbox == settings.vault_dir / "Aura"
    assert (sandbox / "src" / "feature.py").read_text() == "enabled = False\n"
    assert (sandbox / "AURA_ENGINEERING.md").is_file()
    assert (sandbox / "docs" / "reviews" / "README.md").is_file()
    (sandbox / "src" / "feature.py").write_text("enabled = True\n")
    assert (source / "src" / "feature.py").read_text() == "enabled = False\n"


# ── Sprint 1 · Reliability: compaction triggers on token count ─────────────────────────────


def run_turns(aura: Aura, model: ScriptedModel, n: int) -> list[tuple[int, bool]]:
    """Chat n turns; per turn return (estimated history tokens, whether compaction ran)."""
    turns = []
    for i in range(n):
        prompt = f"Fact {i}: " + "detail " * 20
        size = count_tokens_approximately([*history(aura), HumanMessage(prompt)])
        before = len(model.calls)
        aura.chat(prompt)
        turns.append((size, len(model.calls) - before > 1))  # 2 calls = summarizer + reply
    return turns


def test_compaction_triggers_at_60_percent_of_context_budget(settings):
    aura, model = make_aura(settings, context_tokens=1000)  # -> compact at 600 tokens, keep 300
    turns = run_turns(aura, model, 40)

    for i, (size, compacted) in enumerate(turns):
        assert compacted == (size >= 600), f"turn {i}: {size} tokens, compacted={compacted}"
    assert sum(c for _, c in turns) >= 2  # fired, and fired again after re-growing
    for c in model.agent_calls():
        assert count_tokens_approximately(c[1:]) < 600  # history never reaches the trigger


def test_compaction_counts_tokens_the_local_estimate_cannot_see(settings):
    # The local estimate counts only the conversation. The server's reported usage also counts
    # the system prompt and tool schemas, so compaction must start earlier. The budget is twice the
    # system prompt (at least 2000), so the trigger (60%) sits just above the system prompt alone.
    probe, probe_model = make_aura(settings)
    probe.chat("hi")
    system = count_tokens_approximately(probe_model.agent_calls()[0][:1])
    budget = max(2000, 2 * system)
    aura, model = make_aura(dataclasses.replace(settings, data_dir=settings.data_dir / "2"),
                            context_tokens=budget, report_usage=True)  # fmt: skip
    turns = run_turns(aura, model, 25)

    first = next(i for i, (_, compacted) in enumerate(turns) if compacted)
    assert turns[first][0] < budget * 0.6  # the conversation-only estimate alone would not have fired
    assert count_tokens_approximately(model.agent_calls()[first]) < budget * 0.6
    for c in model.agent_calls():
        assert count_tokens_approximately(c) < budget  # whole prompt, system prompt included


def test_reported_usage_does_not_recompact_on_the_next_turn(settings):
    probe, probe_model = make_aura(settings)
    probe.chat("hi")
    budget = max(2000, 2 * count_tokens_approximately(probe_model.agent_calls()[0][:1]))
    aura, model = make_aura(dataclasses.replace(settings, data_dir=settings.data_dir / "2"),
                            context_tokens=budget, report_usage=True)  # fmt: skip

    turns = run_turns(aura, model, 15)
    first = next(i for i, (_, compacted) in enumerate(turns) if compacted)

    assert not any(compacted for _, compacted in turns[first + 1:])  # no stale usage re-compacts this window


def test_compaction_folds_old_turns_into_a_summary(settings):
    aura, model = make_aura(settings, context_tokens=200)
    run_turns(aura, model, 8)

    last_prompt = " ".join(texts(model.agent_calls()[-1]))
    assert "Fact 7" in last_prompt  # recent turns kept verbatim
    assert "Fact 0" not in last_prompt  # oldest turns folded away...
    assert "summary" in last_prompt.lower()  # ...into a summary, rather than silently dropped


def test_streaming_shows_only_auras_reply_during_compaction(settings):
    aura, model = make_aura(settings, context_tokens=200)
    run_turns(aura, model, 4)
    before = len(model.calls)

    out = said(list(aura.stream("One more.")))
    assert len(model.calls) - before == 2  # this turn ran the summarizer, then Aura
    assert out == f"reply {len(model.calls)}"  # and only Aura's reply reached the screen


def summarizer_calls(model: ScriptedModel) -> list[str]:
    """The prompt text of each memory-compaction call (i.e. not Aura's own agent calls)."""
    return [c[-1].text for c in model.calls if not isinstance(c[0], SystemMessage)]


def test_compaction_uses_auras_lossless_fact_prompt(settings):
    aura, model = make_aura(settings, context_tokens=200)
    aura.chat("My locker code is 7319.")
    run_turns(aura, model, 4)

    prompt = summarizer_calls(model)[0]
    assert "Perform lossless fact extraction" in prompt
    assert "Never round, reformat, convert or paraphrase a number" in prompt
    assert "carry every fact in it forward unchanged" in prompt
    assert "My locker code is 7319." in prompt


def test_compaction_at_full_budget_never_drops_what_it_summarizes(settings):
    # LangChain's default pre-trim (4k tokens, oldest dropped first) made every compaction at
    # the real 16k budget miss the earliest message and, later, the previous summary: the
    # locker code was gone before the summarizer ever saw it (seen: 4/4 compactions).
    aura, model = make_aura(settings)  # default context_tokens=16384
    aura.chat("Remember: my locker code is 7319.")
    for i in range(40):
        aura.chat(f"Note {i}: " + "detail " * 400)

    first, *later = summarizer_calls(model)
    assert len(later) >= 2  # compacted repeatedly
    assert "my locker code is 7319" in first
    for prompt in later:  # each re-summarization sees the summary that carries the facts
        assert "Here is a summary of the conversation to date" in prompt


# ── Sprint 1 · Memory ───────────────────────────────────────────────────────────────────────


def test_remembers_previous_turns(settings):
    aura, model = make_aura(settings)
    assert aura.chat("My name is Ada.") == "reply 1"
    assert aura.chat("What is my name?") == "reply 2"

    assert texts(model.calls[1][1:]) == ["My name is Ada.", "reply 1", "What is my name?"]


def test_streamed_replies_are_remembered_too(settings):
    aura, model = make_aura(settings)
    assert said(list(aura.stream("Remember: the code is 42."))) == "reply 1"
    aura.chat("What was the code?")

    assert texts(model.calls[1][1:3]) == ["Remember: the code is 42.", "reply 1"]


def test_new_conversation_starts_clean(settings):
    aura, model = make_aura(settings)
    aura.chat("My name is Ada.")
    aura.new_conversation()
    aura.chat("What is my name?")

    assert texts(model.calls[1][1:]) == ["What is my name?"]


# ── Sprint 2 · Confirmation Step ────────────────────────────────────────────────────────────


def test_vault_change_pauses_for_approval_and_runs_once_approved(settings):
    aura, model = make_aura(
        settings, tool_turn(call("create_folder", path="Project Aura")), AIMessage("Created it.")
    )

    events = list(aura.stream("Create a folder named 'Project Aura'."))
    assert isinstance(events[-1], ApprovalNeeded)
    (action,) = events[-1].actions
    assert action.tool == "create_folder"
    assert action.description == "create folder 'Project Aura/'"
    assert not (settings.vault_dir / "Project Aura").exists()  # nothing happens before approval

    events = list(aura.resume([True]))
    assert statuses(events) == ["[Tool: File_System_Tool] Creating folder 'Project Aura'..."]
    assert said(events) == "Created it."
    assert (settings.vault_dir / "Project Aura").is_dir()

    log = [(e["event"], e.get("decision")) for e in aura.toolbox.audit.entries()]
    assert [event for event in log if event[0] != "project_sandbox_seeded"] == [
        ("approval", "approved"), ("folder_created", None)
    ]


@pytest.mark.parametrize(
    "request_call",
    [
        call("write_file", path="notes.txt", content="hello"),
        call("create_folder", path="Project Aura"),
        call("delete_path", path="keep.txt"),
    ],
    ids=["write", "create_folder", "delete"],
)
def test_denied_action_gets_the_exact_denial_and_nothing_happens(settings, request_call):
    aura, model = make_aura(settings, tool_turn(request_call))
    (settings.vault_dir / "keep.txt").write_text("precious")

    events = list(aura.stream("Do the thing."))
    assert isinstance(events[-1], ApprovalNeeded)
    events = list(aura.resume([False]))

    assert said(events) == DENIAL  # exact wording, no apology or retry
    assert statuses(events) == []  # the tool never ran
    assert len(model.agent_calls()) == 1  # the model wasn't asked to improvise a reply
    # Aura's isolated project copy is seeded at startup; the denied action adds nothing else.
    assert sorted(p.name for p in settings.vault_dir.iterdir()) == ["Aura", "keep.txt"]
    assert (settings.vault_dir / "keep.txt").read_text() == "precious"
    assert aura.toolbox.audit.entries()[-1]["decision"] == "denied"


def test_after_a_denial_the_conversation_continues_and_remembers_it(settings):
    aura, model = make_aura(settings, tool_turn(call("create_folder", path="X")))
    aura.chat("Create folder X.")  # chat() denies by default
    aura.chat("OK, never mind.")

    seen = model.agent_calls()[-1]
    assert any(isinstance(m, ToolMessage) and "declined" in m.text for m in seen)
    assert any(isinstance(m, AIMessage) and m.text == DENIAL for m in seen)


def test_partly_approved_batch_lets_aura_report_both_outcomes(settings):
    aura, model = make_aura(
        settings,
        tool_turn(call("create_folder", path="A"), call("create_folder", path="B")),
        AIMessage("Made A; you declined B."),
    )
    events = list(aura.stream("Make folders A and B."))
    assert [a.description for a in events[-1].actions] == ["create folder 'A/'", "create folder 'B/'"]

    events = list(aura.resume([True, False]))
    assert said(events) == "Made A; you declined B."  # not the blanket denial
    assert (settings.vault_dir / "A").is_dir() and not (settings.vault_dir / "B").exists()


def test_reads_and_lists_need_no_approval(settings):
    (settings.vault_dir).mkdir()
    (settings.vault_dir / "budget.csv").write_text("item,amount\nrent,1200\n")
    aura, model = make_aura(
        settings,
        tool_turn(call("list_vault"), call("read_file", path="budget.csv")),
        AIMessage("Rent is 1200."),
    )

    events = list(aura.stream("What's in budget.csv?"))
    assert not any(isinstance(e, ApprovalNeeded) for e in events)
    # The two calls run concurrently, so their status lines can arrive in either order.
    assert sorted(statuses(events)) == [
        "[Tool: File_System_Tool] Listing '.'...",
        "[Tool: File_System_Tool] Reading 'budget.csv'...",
    ]
    results = {m.name: m.text for m in history(aura) if isinstance(m, ToolMessage)}
    assert "budget.csv" in results["list_vault"] and "rent,1200" in results["read_file"]


def test_runaway_tool_loops_are_capped(settings):
    loop = [tool_turn(call("list_vault")) for _ in range(12)]
    aura, model = make_aura(settings, *loop, AIMessage("Stopping."))

    events = list(aura.stream("List forever."))
    assert len(statuses(events)) == RETRY_LIMIT
    assert said(events).startswith("HALT: RECURSION DETECTED")


# ── Sprint 2 · Search fallback is announced ────────────────────────────────────────────────


def test_fallback_search_tells_the_user(settings):
    aura, model = make_aura(
        settings, tool_turn(call("web_search", query="weather London")), AIMessage("12°C, rain.")
    )

    events = list(aura.stream("Weather in London?"))
    assert statuses(events) == ["[Tool: Web_Search_Tool] Searching for 'weather London'..."]
    assert said(events) == FALLBACK_NOTICE + "\n\n" + "12°C, rain."


def test_fallback_notice_is_said_once_per_turn_even_after_several_searches(settings):
    aura, model = make_aura(
        settings,
        tool_turn(call("web_search", query="weather London")),
        tool_turn(call("web_search", query="London temperature now")),
        AIMessage("12°C."),
    )
    events = list(aura.stream("Weather in London?"))
    assert len(statuses(events)) == 2
    assert said(events) == FALLBACK_NOTICE + "\n\n" + "12°C."


def test_primary_search_has_no_fallback_notice(settings):
    tavily = lambda q, n: [Hit("Met Office", "https://metoffice.example", "11°C")]  # noqa: E731
    aura, model = make_aura(
        settings,
        tool_turn(call("web_search", query="weather London")),
        AIMessage("11°C."),
        tavily=tavily,
        tavily_api_key="tvly-test",
    )

    assert said(list(aura.stream("Weather in London?"))) == "11°C."


# ── Gemma's Sprint 4 mandate: code execution needs a formal approval card ──────────────────


class FakeSandbox:
    timeout = 30.0

    def __init__(self):
        self.runs: list[str] = []

    def run(self, code: str, on_status=lambda s: None):
        from aura.tools.sandbox import RunResult

        self.runs.append(code)
        return RunResult("832040\n", "", 0, False, 0.4, "aura-run-fake")


def make_code_aura(settings, *script):
    from conftest import fake_ddg

    sandbox = FakeSandbox()
    search = WebSearch(None, AuditLog(settings.audit_log), duckduckgo=fake_ddg)
    toolbox = Toolbox.from_settings(settings, search=search, sandbox=sandbox)
    return Aura(settings=settings, model=ScriptedModel(script=list(script)), toolbox=toolbox), sandbox


FIB = "a, b = 0, 1\nfor _ in range(30):\n    a, b = b, a + b\nprint(a)"


def test_code_runs_wait_for_an_approval_card_showing_the_code(settings):
    aura, sandbox = make_code_aura(settings, tool_turn(call("run_python", code=FIB)), AIMessage("832040."))
    events = list(aura.stream("Compute the 30th Fibonacci number with Python."))
    assert isinstance(events[-1], ApprovalNeeded)
    card = events[-1].actions[0].description
    assert card.startswith("run Python in the sandbox (no internet, vault read-only, 30-second limit):")
    assert "    │ for _ in range(30):" in card
    assert sandbox.runs == []  # nothing runs before Allow

    events = list(aura.resume([True]))
    assert sandbox.runs == [FIB]
    assert "[Tool: Code_Interpreter_Tool] Running Python in the sandbox..." in statuses(events)
    assert said(events) == "832040."


def test_denied_code_never_runs(settings):
    aura, sandbox = make_code_aura(settings, tool_turn(call("run_python", code="print(1)")))
    list(aura.stream("Run some code."))
    events = list(aura.resume([False]))
    assert said(events) == DENIAL and sandbox.runs == []
