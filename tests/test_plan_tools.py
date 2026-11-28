"""T4 of docs/orchestration.md: make_plan, plan_status and finish_step in real (scripted) turns."""

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
    # A refused finish saves nothing, not even the start that came with it (T6 marks a step running).
    assert plan.steps["s1"].status == "pending" and not (settings.vault_dir / "a.md").exists()


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
