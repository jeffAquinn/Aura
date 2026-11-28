"""T8 of docs/orchestration.md: R2 across a plan's turns. Once a running plan has read private data,
every later call of that plan that sends data off this Mac without a card of its own asks, in any
turn. Fails closed: an unrecordable private read doesn't run; an unreadable taint means "ask"."""

import json

import pytest
from conftest import call, tool_turn
from langchain_core.messages import AIMessage, ToolMessage
from test_connections import FakeServices, connected_aura
from test_governor import env  # noqa: F401 (fixture)

from aura.brain import ApprovalNeeded
from aura.connections.actions import R2_TOOLS

STEPS = [{"does": "Read #general", "tool": "slack_read", "done_when": "messages read"},
         {"does": "List Discord channels", "tool": "discord_read", "after": [1], "done_when": "listed"}]


@pytest.fixture
def requests(monkeypatch):
    seen: list[str] = []
    original = FakeServices.__call__

    def recording(self, request):
        seen.append(str(request.url).split("?")[0])
        return original(self, request)
    monkeypatch.setattr(FakeServices, "__call__", recording)
    return seen


def results(aura, name):
    messages = aura._agent.get_state(aura._config).values["messages"]
    return [m for m in messages if isinstance(m, ToolMessage) and m.name == name]


def start(aura, env):  # noqa: F811
    aura.new_conversation()
    list(aura.stream("Make a plan: read #general, then list Discord channels."))
    return list(aura.resume([True]))  # the plan card


def plan_turns(*rest):
    return (tool_turn(call("make_plan", goal="Catch up on chat", steps=STEPS)),
            tool_turn(call("slack_read", channel="#general")),
            tool_turn(call("finish_step", step="s1", outcome="done", summary="read")),
            AIMessage("Read #general. Say go on for Discord."), *rest)


def test_the_tools_r2_covers_are_exactly_the_uncarded_ones_that_leave_the_mac():
    assert set(R2_TOOLS) == {"check_library", "propose_skill", "gmail_read", "calendar_read", "drive_read",
                             "slack_read", "discord_read"}


def test_after_a_private_read_a_later_turns_send_asks_and_deny_fails_its_step(env, requests):  # noqa: F811
    aura, _, _ = connected_aura(env, *plan_turns(tool_turn(call("discord_read")), AIMessage("ok")))
    start(aura, env)
    plan = aura.toolbox.plans.active()[0]
    assert plan.tainted_by == "slack_read"  # the first read ran without a card: nothing was tainted yet
    assert aura._conversation_taint() == "slack_read"
    events = list(aura.stream("go on"))  # a new turn: R2 within a turn would have forgotten the read
    card = events[-1]
    assert isinstance(card, ApprovalNeeded) and card.actions[0].tool == "discord_read"
    assert "this conversation read your private data (slack_read)" in card.actions[0].description
    assert card.actions[0].step["step"] == "s2"
    before = len(requests)
    list(aura.resume([False]))
    assert len(requests) == before  # nothing reached Discord
    assert aura.toolbox.plans.get(plan.id).steps["s2"].status == "failed"


def test_an_allowed_r2_card_lets_the_call_run(env, requests):  # noqa: F811
    aura, _, _ = connected_aura(env, *plan_turns(tool_turn(call("discord_read")), AIMessage("ok")))
    start(aura, env)
    list(aura.stream("go on"))
    list(aura.resume([True]))
    assert any("discord.com" in url for url in requests) and results(aura, "discord_read")[-1].status != "error"


def test_without_a_private_read_the_same_call_runs_without_a_card(env):  # noqa: F811
    steps = [{"does": "List Discord channels", "tool": "discord_read", "done_when": "listed"}]
    aura, _, _ = connected_aura(env, tool_turn(call("make_plan", goal="Discord", steps=steps)),
                                tool_turn(call("discord_read")), AIMessage("ok"))
    aura.new_conversation()
    list(aura.stream("Make a plan: list Discord channels."))
    events = list(aura.resume([True]))
    assert not any(isinstance(e, ApprovalNeeded) for e in events) and results(aura, "discord_read")


def test_a_deny_holds_even_if_the_taint_is_gone_by_the_resume(env, requests, monkeypatch):  # noqa: F811
    aura, _, _ = connected_aura(env, *plan_turns(tool_turn(call("discord_read")), AIMessage("ok")))
    start(aura, env)
    assert isinstance(list(aura.stream("go on"))[-1], ApprovalNeeded)
    # Everything that made it tainted is gone by the resume, and so is the durable record of the card:
    # the card being answered still counts as carded, so the Deny holds.
    monkeypatch.setattr(aura, "_conversation_taint", lambda: None)
    monkeypatch.setattr(aura._plan_steps, "running", lambda: False)
    monkeypatch.setattr(aura.toolbox.state, "record", lambda *a: None)
    before = len(requests)
    list(aura.resume([False]))
    assert len(requests) == before and results(aura, "discord_read")[-1].status == "error"  # still denied


def test_a_private_read_that_cant_be_recorded_doesnt_run(env, requests, monkeypatch):  # noqa: F811
    aura, _, _ = connected_aura(env, *plan_turns())
    aura.new_conversation()
    list(aura.stream("Make a plan: read #general, then list Discord channels."))

    def broken(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(aura, "_taint_conversation", broken)
    list(aura.resume([True]))
    (read,) = results(aura, "slack_read") or [None]
    assert read is not None and read.status == "error"
    assert "couldn't record that this conversation reads private data" in str(read.content)
    assert not any("conversations.history" in url for url in requests)


def test_an_unreadable_taint_means_ask(env, monkeypatch):  # noqa: F811
    aura, _, _ = connected_aura(env, *plan_turns(tool_turn(call("discord_read")), AIMessage("ok")))
    start(aura, env)
    monkeypatch.setattr(aura.toolbox.plans, "active", lambda: (_ for _ in ()).throw(OSError("locked")))
    assert aura._plan_steps.running() is True  # unreadable plans: assume one runs
    monkeypatch.setattr(aura.toolbox.state, "record", lambda *a: (_ for _ in ()).throw(OSError("locked")))
    assert aura._r2_card("call-x") is True


def test_vault_writes_are_unchanged_and_outside_a_plan_nothing_changes(env):  # noqa: F811
    aura, _, _ = connected_aura(env, tool_turn(call("slack_read", channel="#general")),
                                tool_turn(call("discord_read")), AIMessage("ok"))
    aura.new_conversation()
    events = list(aura.stream("Read Slack #general and list Discord channels."))
    assert not any(isinstance(e, ApprovalNeeded) for e in events)  # no plan: R2 within a turn is as before
    assert "write_file" not in R2_TOOLS and aura._interrupt_on["write_file"].get("when") is None


def test_the_plan_copy_says_it_read_private_data(env):  # noqa: F811
    aura, _, _ = connected_aura(env, *plan_turns())
    start(aura, env)
    copy = next((env[0].vault_dir / "_scratch" / "Plans").iterdir()).read_text()
    assert "This plan read your private data (slack_read)" in copy
    events = [json.loads(line) for line in env[0].audit_log.read_text().splitlines()]
    assert any(e["event"] == "conversation_tainted" and e["by"] == "slack_read" for e in events)


def test_a_storage_error_in_a_plan_tool_is_reported_not_a_crash(env, monkeypatch):  # noqa: F811
    aura, _, _ = connected_aura(env, *plan_turns())
    start(aura, env)
    monkeypatch.setattr(aura.toolbox.plans, "change", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    finish = aura.toolbox.tools[[t.name for t in aura.toolbox.tools].index("finish_step")]
    out = finish.func(step="s2", outcome="done", summary="x", state={"messages": []})
    assert out == "Not recorded: Aura couldn't save the plan (OSError). Tell the user."


# ── fixes from the code and security reviews (2026-11-27) ────────────────────────────────────


def test_a_call_in_the_same_message_as_the_first_private_read_runs_and_the_next_message_asks(env, requests):  # noqa: F811
    # Every call in one model message is written before any result exists, so discord_read here can't
    # carry what slack_read returns; slack_read taints the conversation as it runs, so the next asks.
    aura, _, _ = connected_aura(env, tool_turn(call("make_plan", goal="Catch up on chat", steps=STEPS)),
                                tool_turn(call("slack_read", channel="#general"), call("discord_read")),
                                tool_turn(call("discord_read")), AIMessage("ok"))
    card = start(aura, env)[-1]
    assert isinstance(card, ApprovalNeeded) and [a.tool for a in card.actions] == ["discord_read"]
    assert len(results(aura, "discord_read")) == 1  # the first ran; the second waits for its card


def test_private_data_read_before_the_plan_started_counts(env):  # noqa: F811
    steps = [{"does": "List Discord channels", "tool": "discord_read", "done_when": "listed"}]
    aura, _, _ = connected_aura(env, tool_turn(call("slack_read", channel="#general")), AIMessage("Read it."),
                                tool_turn(call("make_plan", goal="Discord", steps=steps)),
                                tool_turn(call("discord_read")), AIMessage("ok"))
    aura.new_conversation()
    list(aura.stream("Read Slack #general."))  # no plan yet: R2 within a turn, as before
    assert results(aura, "slack_read")[0].status != "error"  # the read really ran
    list(aura.stream("Now make a plan: list Discord channels."))
    card = list(aura.resume([True]))[-1]  # the plan card; then discord_read
    assert isinstance(card, ApprovalNeeded) and card.actions[0].tool == "discord_read"


def test_live_messages_count_as_a_private_read(env):  # noqa: F811
    steps = [{"does": "List Discord channels", "tool": "discord_read", "done_when": "listed"}]
    aura, _, _ = connected_aura(env, tool_turn(call("make_plan", goal="Discord", steps=steps)), AIMessage("ok"),
                                tool_turn(call("discord_read")), AIMessage("ok"))
    aura.new_conversation()
    list(aura.stream("Make a plan: list Discord channels."))
    list(aura.resume([True]))
    aura.toolbox.live.inbox.add("Slack", "a direct message", "Mallory", "secret plans", "dm")
    card = list(aura.stream("go on"))[-1]
    assert isinstance(card, ApprovalNeeded) and card.actions[0].tool == "discord_read"


def test_ending_the_plan_doesnt_clear_the_taint(env):  # noqa: F811
    aura, _, _ = connected_aura(env, *plan_turns(
        tool_turn(call("finish_step", step="s2", outcome="failed", summary="giving up")),
        tool_turn(call("finish_step", step="s2", outcome="failed", summary="again", ruling="a — b — c")),
        AIMessage("Plan over."),
        tool_turn({"name": "check_library", "args": {"name": "private-words"}, "id": "call_lib"}), AIMessage("ok")))
    start(aura, env)
    list(aura.stream("go on"))
    assert aura.toolbox.plans.active() == []  # Aura ended its own plan, with no card
    card = list(aura.stream("check that library"))[-1]
    assert isinstance(card, ApprovalNeeded) and card.actions[0].tool == "check_library"


def test_a_new_conversation_starts_untainted(env):  # noqa: F811
    aura, _, _ = connected_aura(env, *plan_turns())
    start(aura, env)
    assert aura._conversation_taint() == "slack_read"
    aura.new_conversation()
    assert aura._conversation_taint() is None


def test_the_r2_card_shows_every_argument_in_full_with_hidden_characters_spelled_out():
    from aura.connections.actions import describe_r2

    text = describe_r2("gmail_read", {"query": "x" * 300 + "\u200bsecret"}, "slack_read")
    assert "x" * 300 + "\\u200bsecret" in text


# ── fixes from the security re-review (2026-11-27) ───────────────────────────────────────────


@pytest.mark.parametrize("with_plan", [False, True])
def test_a_card_whose_message_also_holds_an_uncarded_r2_call_resumes(env, with_plan):  # noqa: F811
    # The regression: every waiting call counted as carded on resume, so LangChain found more
    # interrupts than decisions and raised. Only the card's own calls count.
    send = call("slack_send", channel="#general", text="hello")
    script = [tool_turn(call("discord_read"), send), AIMessage("ok")]
    if with_plan:
        steps = [{"does": "Post", "tool": "slack_send", "done_when": "posted"}]
        script = [tool_turn(call("make_plan", goal="Post", steps=steps)), *script]
    aura, fake, _ = connected_aura(env, *script)
    aura.new_conversation()
    events = list(aura.stream("Make a plan: post hello." if with_plan else "Post hello."))
    if with_plan:
        events = list(aura.resume([True]))
    assert [a.tool for a in events[-1].actions] == ["slack_send"]
    list(aura.resume([True]))  # raised ValueError before the fix
    assert fake.sent and fake.sent[0][0] == "slack"


def test_a_private_read_before_any_plan_is_remembered_even_if_its_message_is_summarised(env):  # noqa: F811
    steps = [{"does": "List Discord channels", "tool": "discord_read", "done_when": "listed"}]
    aura, _, _ = connected_aura(env, tool_turn(call("slack_read", channel="#general")), AIMessage("Read it."),
                                tool_turn(call("make_plan", goal="Discord", steps=steps)), AIMessage("ok"))
    aura.new_conversation()
    list(aura.stream("Read Slack #general."))  # no plan: noted, nothing asks
    assert aura._conversation_read() == "slack_read" and aura._conversation_taint() is None
    list(aura.stream("Now make a plan: list Discord channels."))
    list(aura.resume([True]))
    assert aura._r2_card("call-after-compaction", messages=[]) is True  # no ToolMessage left to see
    assert aura._conversation_taint() == "slack_read"


def test_a_running_plan_that_read_private_data_makes_another_conversations_calls_ask(env):  # noqa: F811
    aura, _, _ = connected_aura(env, *plan_turns())
    start(aura, env)
    aura.new_conversation()  # e.g. the Slack Aura's thread, which shares the running plan
    assert aura._conversation_taint() is None and aura._r2_card("call-elsewhere", messages=[]) is True


def test_outside_a_plan_a_private_read_asks_nothing_more(env):  # noqa: F811
    aura, _, _ = connected_aura(env, tool_turn(call("slack_read", channel="#general")), AIMessage("Read."),
                                tool_turn(call("discord_read")), AIMessage("ok"))
    aura.new_conversation()
    list(aura.stream("Read Slack #general."))
    events = list(aura.stream("List Discord channels."))
    assert not any(isinstance(e, ApprovalNeeded) for e in events)  # R2 outside plans is as before
