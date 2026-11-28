"""T4 of docs/orchestration.md: make_plan, plan_status and finish_step in real (scripted) turns."""

import json

from conftest import call, make_aura, tool_turn
from langchain_core.messages import AIMessage, ToolMessage

from aura.brain import ApprovalNeeded

NOTE = [{"does": "Find notes on the topic", "tool": "search_vault", "done_when": "matches listed"},
        {"does": "Draft the note", "tool": "think", "after": [1], "done_when": "a draft"},
        {"does": "Save it", "tool": "write_file", "after": [2], "done_when": "the note is written"}]


def plan_call(steps=NOTE, goal="Write a note on tides"):
    return call("make_plan", goal=goal, steps=steps)


def results(aura, name):
    messages = aura._agent.get_state(aura._config).values["messages"]
    return [str(m.content) for m in messages if isinstance(m, ToolMessage) and m.name == name]


def test_an_invalid_plan_gets_no_card_and_comes_back_with_every_reason(settings):
    bad = [{"does": "Fly", "tool": "teleport", "done_when": "there"}, {"does": "", "tool": "think", "done_when": "x"}]
    aura, _ = make_aura(settings, tool_turn(plan_call(bad)), AIMessage("I'll fix it."))
    events = list(aura.stream("Make a plan for the tides note."))
    assert not any(isinstance(e, ApprovalNeeded) for e in events)
    (answer,) = results(aura, "make_plan")
    assert answer.startswith("Plan not valid: ") and "no tool 'teleport'" in answer and "step 2 needs 'does'" in answer
    assert aura.toolbox.plans.active() == []


def test_a_third_invalid_plan_in_one_turn_is_refused(settings):
    bad = [{"does": "x", "tool": "teleport", "done_when": "x"}]
    aura, _ = make_aura(settings, *(tool_turn(plan_call(bad)) for _ in range(3)), AIMessage("Stopping."))
    list(aura.stream("Make a plan."))
    first, second, third = results(aura, "make_plan")
    assert "1 more try" in first and "Tell the user what you were trying" in second
    assert "2 plans were refused this turn, so no more are checked" in third


def test_a_valid_plan_waits_for_the_plan_card_and_deny_starts_nothing(settings):
    aura, _ = make_aura(settings, tool_turn(plan_call()), AIMessage("Understood."))
    events = list(aura.stream("Make a plan for a note on tides."))
    card = events[-1]
    assert isinstance(card, ApprovalNeeded) and card.actions[0].tool == "make_plan"
    text = card.actions[0].description
    assert text.startswith("Start this plan? Write a note on tides")
    assert "s3. Save it [write_file] (after s2). Done when: the note is written" in text
    assert "doesn't approve its actions" in text
    list(aura.resume([False]))
    assert aura.toolbox.plans.active() == []


def test_a_plan_runs_its_steps_with_evidence_and_each_step_keeps_its_own_card(settings):
    (settings.vault_dir).mkdir(parents=True, exist_ok=True)
    (settings.vault_dir / "tides.md").write_text("Tides follow the moon.\n")
    aura, _ = make_aura(
        settings,
        tool_turn(plan_call()),
        tool_turn(call("search_vault", query="moon")),
        tool_turn(call("finish_step", step="s1", outcome="done", summary="tides.md mentions the moon")),
        tool_turn(call("finish_step", step="s2", outcome="done", summary="drafted")),
        tool_turn(call("write_file", path="Knowledge/tides-note.md", content="Tides follow the moon.")),
        tool_turn(call("finish_step", step="s3", outcome="done", summary="written")),
        AIMessage("The note is written."),
    )
    events = list(aura.stream("Make a plan for a note on tides."))
    assert events[-1].actions[0].tool == "make_plan"
    events = list(aura.resume([True]))  # the plan card
    assert isinstance(events[-1], ApprovalNeeded) and events[-1].actions[0].tool == "write_file"  # its own card
    list(aura.resume([True]))
    (plan,) = [aura.toolbox.plans.get(p) for p in {aura.toolbox.plans.state.records("plan")[0][0]}]
    assert plan.complete and [s.outcome for s in plan.steps.values()] == ["done", "done", "done"]
    assert plan.steps["s1"].evidence and plan.steps["s3"].evidence and not plan.steps["s2"].evidence
    assert "Every step is done" in results(aura, "finish_step")[-1]
    copies = list((settings.vault_dir / "_scratch" / "Plans").iterdir())
    assert len(copies) == 1 and "| s3 | Save it | done | write_file |" in copies[0].read_text()


def test_a_tool_step_isnt_done_on_the_models_word_or_after_a_denied_card(settings):
    aura, _ = make_aura(
        settings,
        tool_turn(plan_call([{"does": "Save it", "tool": "write_file", "done_when": "written"}])),
        tool_turn(call("finish_step", step="s1", outcome="done", summary="I saved it")),  # no write at all
        tool_turn(call("write_file", path="a.md", content="x")),
        tool_turn(call("finish_step", step="s1", outcome="done", summary="saved now")),  # after a denied card
        AIMessage("It wasn't saved."),
    )
    list(aura.stream("Make a plan: save a note."))
    list(aura.resume([True]))  # the plan card
    list(aura.resume([False]))  # deny the write: the turn ends there, as for any denied card
    list(aura.stream("go on"))  # the next turn tries to finish the step anyway
    claim, after_deny = results(aura, "finish_step")
    assert claim.startswith("Not recorded: step s1 isn't done: write_file hasn't returned a result")
    assert after_deny.startswith("Not recorded:")
    plan = aura.toolbox.plans.active()[0]
    # T6: the denied card failed the step (and the claim before it saved nothing).
    assert plan.steps["s1"].status == "failed" and not (settings.vault_dir / "a.md").exists()


def test_a_failed_step_is_retried_once_with_a_ruling_and_a_new_result(settings):
    settings.vault_dir.mkdir(parents=True, exist_ok=True)
    aura, _ = make_aura(
        settings,
        tool_turn(plan_call([{"does": "Find tide notes", "tool": "search_vault", "done_when": "found"},
                             {"does": "Summarise", "tool": "think", "after": [1], "done_when": "a summary"}])),
        tool_turn(call("search_vault", query="tidal")),
        tool_turn(call("finish_step", step="s1", outcome="failed", summary="nothing matched")),
        tool_turn(call("finish_step", step="s1", outcome="done", summary="retry without searching")),  # no ruling
        tool_turn(call("finish_step", step="s1", outcome="done", summary="same old result",
                       ruling="search for tide — the word tidal matched nothing — may miss notes")),  # no new result
        tool_turn(call("search_vault", query="tide")),
        tool_turn(call("finish_step", step="s1", outcome="done", summary="no notes; going on",
                       ruling="search for tide — tidal matched nothing — may miss notes")),
        AIMessage("Done."),
    )
    list(aura.stream("Make a plan to summarise my tide notes."))
    list(aura.resume([True]))
    failed, no_ruling, stale, retried = results(aura, "finish_step")
    assert "s2 [blocked]" in failed
    assert "to retry it, pass ruling" in no_ruling
    assert stale.startswith("Not recorded: step s1 isn't done")  # the failed attempt's result was claimed
    assert "s1 [done]" in retried and "Ruling (s1): search for tide — tidal matched nothing — may miss notes" in retried
    assert "Next: s2" in retried


def test_a_running_plans_tools_are_offered_every_turn_without_cues(settings):
    aura, _ = make_aura(settings, tool_turn(plan_call()), AIMessage("Plan started."))
    list(aura.stream("Make a plan for a note on tides."))
    list(aura.resume([True]))
    list(aura.stream("ok, go on"))  # no cue for any of these
    assert {"plan_status", "finish_step", "search_vault", "write_file"} <= aura._visible_tool_names


def test_a_new_plan_says_it_replaces_the_running_one_and_stops_it(settings):
    other = [{"does": "Think", "tool": "think", "done_when": "thought"}]
    aura, _ = make_aura(settings, tool_turn(plan_call()), AIMessage("Started."),
                        tool_turn(plan_call(other, goal="Something else")), AIMessage("Switched."))
    list(aura.stream("Make a plan for a note on tides."))
    list(aura.resume([True]))
    first = aura.toolbox.plans.active()[0]
    card = list(aura.stream("Make a plan for something else instead."))[-1]
    assert f'It replaces the plan still running, "Write a note on tides" ({first.id}).' in card.actions[0].description
    list(aura.resume([True]))
    assert aura.toolbox.plans.get(first.id).stopped
    assert [p.goal for p in aura.toolbox.plans.active()] == ["Something else"]


def test_plan_status_without_a_plan(settings):
    aura, _ = make_aura(settings, tool_turn(call("plan_status")), AIMessage("None."))
    list(aura.stream("Is a multi-step plan running?"))
    assert results(aura, "plan_status") == ["No plan is running."]


# ── fixes from the code review (2026-11-27) ──────────────────────────────────────────────────


def test_a_valid_plan_after_two_invalid_ones_still_starts(settings):
    bad = [{"does": "x", "tool": "teleport", "done_when": "x"}]
    aura, _ = make_aura(settings, tool_turn(plan_call(bad)), tool_turn(plan_call(bad)), tool_turn(plan_call()),
                        AIMessage("Started."))
    events = list(aura.stream("Make a plan for a note on tides."))
    assert events[-1].actions[0].tool == "make_plan"  # the third, valid plan gets its card…
    list(aura.resume([True]))
    assert results(aura, "make_plan")[-1].startswith("Approved by the user and started.")  # …and starts
    assert len(aura.toolbox.plans.active()) == 1


def test_a_plans_step_tools_can_run_in_the_turn_it_was_approved(settings):
    steps = [{"does": "See what is loaded", "tool": "list_models", "done_when": "listed"}]
    aura, _ = make_aura(settings, tool_turn(call("make_plan", goal="Check the models", steps=steps)),
                        tool_turn(call("list_models")), tool_turn(call("finish_step", step="s1", outcome="done",
                                                                          summary="listed")), AIMessage("Done."))
    list(aura.stream("Make a plan: check things."))  # no cue shows list_models
    list(aura.resume([True]))
    assert not any("isn't available in this turn" in r for r in results(aura, "list_models"))
    assert aura.toolbox.plans.get(aura.toolbox.plans.state.records("plan")[0][0]).complete


def test_a_result_from_an_earlier_step_never_proves_a_later_one(settings):
    settings.vault_dir.mkdir(parents=True, exist_ok=True)
    steps = [{"does": "Search tides", "tool": "search_vault", "done_when": "searched"},
             {"does": "Search moon", "tool": "search_vault", "after": [1], "done_when": "searched"}]
    aura, _ = make_aura(settings, tool_turn(plan_call(steps)),
                        tool_turn(call("search_vault", query="tide")), tool_turn(call("search_vault", query="sea")),
                        tool_turn(call("finish_step", step="s1", outcome="done", summary="ok")),
                        tool_turn(call("finish_step", step="s2", outcome="done", summary="reused")),  # no new search
                        AIMessage("Stopping."))
    list(aura.stream("Make a plan for a note on tides."))
    list(aura.resume([True]))
    first, second = results(aura, "finish_step")
    assert "s1 [done]" in first and second.startswith("Not recorded: step s2 isn't done")


def test_a_retried_steps_failed_result_stays_claimed():
    from aura.plans import Plan, Ruling

    plan = Plan.from_dict({"id": "p-1", "goal": "x", "steps": [
        {"id": "s1", "does": "a", "tool": "web_search", "done_when": "x"}]})
    plan.start("s1"); plan.finish("s1", "failed", "bad", evidence="call-A")
    plan.retry("s1", Ruling("s1", "w", "y", "c")); plan.start("s1"); plan.finish("s1", "done", "ok", evidence="call-B")
    assert plan.claimed == {"call-A", "call-B"} and Plan.from_dict(plan.to_dict()).claimed == {"call-A", "call-B"}


def test_only_steps_that_can_still_run_keep_their_tools_shown():
    from aura.plans import Plan, Ruling
    from aura.tools.plans import steps_in_use

    class Store:
        def __init__(self, plan): self.plan = plan
        def active(self): return [self.plan]

    plan = Plan.from_dict({"id": "p-1", "goal": "x", "steps": [
        {"id": "s1", "does": "a", "tool": "write_file", "done_when": "x"},
        {"id": "s2", "does": "b", "tool": "slack_send", "done_when": "x", "after": ["s1"]}]})
    assert steps_in_use(Store(plan)) == {"plan_status", "finish_step", "write_file", "slack_send"}
    plan.skip("s2", Ruling("s2", "drop it", "the user said so", "nothing posted"))
    assert steps_in_use(Store(plan)) == {"plan_status", "finish_step", "write_file"}


def test_two_plans_in_one_message_are_both_refused(settings):
    aura, _ = make_aura(settings, tool_turn(plan_call(), plan_call(goal="Another")), AIMessage("One at a time."))
    list(aura.stream("Make a plan."))
    list(aura.resume([True, True]))
    assert results(aura, "make_plan") == ["Plan not valid: one plan at a time. Send a single make_plan."] * 2
    assert aura.toolbox.plans.active() == []


def test_a_ruling_splits_on_dashes_never_on_a_hyphen():
    from aura.tools.plans import _ruling

    r = _ruling("s1", "use page 2 - not page 1 — the first was empty — one extra search")
    assert (r.what, r.why, r.cost) == ("use page 2 - not page 1", "the first was empty", "one extra search")


def test_asking_for_an_explanation_step_by_step_doesnt_offer_plans():
    from aura.capabilities import BY_NAME

    assert not BY_NAME["make_plan"].matches("Explain step by step how DNS works")
    assert BY_NAME["make_plan"].matches("Make a plan for the move")


def test_a_denied_plan_never_starts_even_if_its_card_condition_changes_before_the_resume(settings):
    # LangChain re-runs `when` on resume and, if it is now False, runs the tool despite the Deny. Here a
    # step's tool disappears while the card is open (as when an account is disconnected): on resume
    # the card's condition is False, the tool runs anyway, and make_plan's own check refuses the plan.
    aura, _ = make_aura(settings, tool_turn(plan_call()), AIMessage("Understood."))
    card = list(aura.stream("Make a plan for a note on tides."))[-1]
    assert card.actions[0].tool == "make_plan"
    aura.toolbox.tools = [tool for tool in aura.toolbox.tools if tool.name != "write_file"]
    list(aura.resume([False]))
    (answer,) = results(aura, "make_plan")
    assert answer.startswith("Plan not valid") and "no tool 'write_file'" in answer  # the dangerous path ran…
    assert aura.toolbox.plans.state.records("plan") == []  # …and still started nothing


# ── T5: what the app's plan card and progress line receive ───────────────────────────────────


def test_the_plan_card_carries_the_checked_plan_with_its_real_step_ids(settings):
    named = [{"id": "find", "does": "Find notes", "tool": "search_vault", "done_when": "listed"},
             {"id": "draft", "does": "Draft", "tool": "think", "after": ["find"], "done_when": "a draft"}]
    aura, _ = make_aura(settings, tool_turn(plan_call(named)), AIMessage("ok"))
    card = list(aura.stream("Make a plan for a note on tides."))[-1]
    assert card.actions[0].plan == {
        "goal": "Write a note on tides", "replaces": None,
        "steps": [{"id": "s1", "does": "Find notes", "tool": "search_vault", "after": [], "done_when": "listed"},
                  {"id": "s2", "does": "Draft", "tool": "think", "after": ["s1"], "done_when": "a draft"}]}
    assert aura.pending()["actions"][0]["plan"] == card.actions[0].plan  # the card shown again after a restart


def test_other_cards_carry_no_plan(settings):
    aura, _ = make_aura(settings, tool_turn(call("create_folder", path="Receipts")), AIMessage("ok"))
    card = list(aura.stream("Create a folder named Receipts."))[-1]
    assert card.actions[0].plan is None


def test_the_server_streams_the_plan_card_and_reports_the_running_plan(settings):
    from fastapi.testclient import TestClient
    from test_server import AUTH, TOKEN, FakeKeeper, events, post

    from aura.server import create_app

    aura, _ = make_aura(settings, tool_turn(plan_call()), AIMessage("Started."))
    client = TestClient(create_app(aura, TOKEN, FakeKeeper()), base_url="http://127.0.0.1")
    assert client.get("/v1/plans/active", headers=AUTH).json() == {"plan": None}
    evs = events(post(client, "/v1/chat", {"text": "Make a plan for a note on tides."}))
    (approval,) = [data for kind, data in evs if kind == "approval"]
    assert [s["id"] for s in approval["actions"][0]["plan"]["steps"]] == ["s1", "s2", "s3"]
    events(post(client, "/v1/approvals", {"approval_id": approval["approval_id"], "approvals": [True]}))
    running = client.get("/v1/plans/active", headers=AUTH).json()["plan"]
    assert running["goal"] == "Write a note on tides" and (running["done"], running["total"]) == (0, 3)
    assert [s["status"] for s in running["steps"]] == ["pending"] * 3


# ── T6: cards raised during a plan, and what Allow and Deny do to its step ───────────────────

WRITE_PLAN = [{"does": "Find notes", "tool": "search_vault", "done_when": "listed"},
              {"does": "Save the note", "tool": "write_file", "after": [1], "done_when": "written"},
              {"does": "Tell the user", "tool": "think", "after": [2], "done_when": "told"}]


def running_plan(aura):
    return aura.toolbox.plans.get(aura.toolbox.plans.state.records("plan")[0][0])


def test_a_card_during_a_plan_names_its_step_and_the_step_waits(settings):
    settings.vault_dir.mkdir(parents=True, exist_ok=True)
    aura, _ = make_aura(settings, tool_turn(plan_call(WRITE_PLAN)),
                        tool_turn(call("search_vault", query="tide")),
                        tool_turn(call("finish_step", step="s1", outcome="done", summary="none found")),
                        tool_turn(call("write_file", path="Knowledge/tides.md", content="Tides.")),
                        AIMessage("ok"))
    list(aura.stream("Make a plan for a note on tides."))
    card = list(aura.resume([True]))[-1]  # the plan card, then the write's own card
    assert card.actions[0].tool == "write_file"
    assert card.actions[0].step == {"plan": running_plan(aura).id, "goal": "Write a note on tides", "step": "s2",
                                    "index": 2, "total": 3, "does": "Save the note"}
    assert card.actions[0].payload()["step"]["index"] == 2 and aura.pending()["actions"][0]["step"]["step"] == "s2"
    assert running_plan(aura).steps["s2"].status == "waiting"


def test_deny_fails_the_step_and_blocks_what_depends_on_it(settings):
    settings.vault_dir.mkdir(parents=True, exist_ok=True)
    aura, _ = make_aura(settings, tool_turn(plan_call(WRITE_PLAN)),
                        tool_turn(call("search_vault", query="tide")),
                        tool_turn(call("finish_step", step="s1", outcome="done", summary="none found")),
                        tool_turn(call("write_file", path="Knowledge/tides.md", content="Tides.")),
                        AIMessage("ok"))
    list(aura.stream("Make a plan for a note on tides."))
    list(aura.resume([True]))
    list(aura.resume([False]))  # deny the write
    plan = running_plan(aura)
    assert plan.steps["s2"].status == "failed" and plan.steps["s2"].summary == "You denied write_file on its card."
    assert plan.steps["s3"].status == "blocked" and not (settings.vault_dir / "Knowledge" / "tides.md").exists()


def test_allow_runs_the_step_and_its_tool_running_marks_it_running(settings):
    settings.vault_dir.mkdir(parents=True, exist_ok=True)
    aura, _ = make_aura(settings, tool_turn(plan_call(WRITE_PLAN)),
                        tool_turn(call("search_vault", query="tide")),
                        tool_turn(call("plan_status")),  # s1 is running before it is finished
                        tool_turn(call("finish_step", step="s1", outcome="done", summary="none found")),
                        tool_turn(call("write_file", path="Knowledge/tides.md", content="Tides.")),
                        tool_turn(call("finish_step", step="s2", outcome="done", summary="written")),
                        AIMessage("ok"))
    list(aura.stream("Make a plan for a note on tides."))
    list(aura.resume([True]))
    list(aura.resume([True]))  # allow the write
    assert "- s1 [running] Find notes" in results(aura, "plan_status")[0]
    plan = running_plan(aura)
    assert plan.steps["s2"].status == "done" and (settings.vault_dir / "Knowledge" / "tides.md").exists()
    statuses = [(e["step"], e["status"]) for e in map(json.loads, settings.audit_log.read_text().splitlines())
                if e["event"] == "plan_step"]
    assert statuses[:5] == [("s1", "running"), ("s1", "done"), ("s2", "waiting"), ("s2", "running"), ("s2", "done")]


def test_cards_outside_a_plan_are_unchanged(settings):
    aura, _ = make_aura(settings, tool_turn(call("create_folder", path="Receipts")), AIMessage("ok"))
    card = list(aura.stream("Create a folder named Receipts."))[-1]
    assert card.actions[0].step is None and set(card.actions[0].payload()) == {"tool", "args", "description"}


def test_plan_bookkeeping_that_fails_never_breaks_a_tool_call(settings, monkeypatch):
    settings.vault_dir.mkdir(parents=True, exist_ok=True)
    aura, _ = make_aura(settings, tool_turn(plan_call(WRITE_PLAN)), tool_turn(call("search_vault", query="tide")),
                        AIMessage("ok"))
    list(aura.stream("Make a plan for a note on tides."))

    def broken(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(aura.toolbox.plans, "change", broken)
    list(aura.resume([True]))  # the plan card; then search_vault runs although its step can't be recorded
    assert results(aura, "search_vault") and not results(aura, "search_vault")[0].startswith("Error")
