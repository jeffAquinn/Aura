"""T1 of docs/orchestration.md: a plan's checks, its IDs, what runs next, and how failure spreads."""

import pytest

from aura.plans import MAX_STEPS, Plan, PlanError, Ruling, make

TOOLS = {"search_vault", "read_file", "write_file", "web_search", "run_python", "ask_developer"}


def step(does="Do it", tool="think", done_when="it's done", **extra):
    return {"does": does, "tool": tool, "done_when": done_when, **extra}


def reasons(goal, steps, available=TOOLS):
    with pytest.raises(PlanError) as caught:
        make(goal, steps, available)
    return caught.value.reasons


def five_tasks() -> Plan:
    """Aura's own example: A; B and C after A; D after B and C; E after B."""
    return make("Ship the change", [
        step("Analyze intent", id="A"),
        step("Generate plan", after=["A"], id="B"),
        step("Fetch resources", tool="search_vault", after=["A"], id="C"),
        step("Implement", tool="write_file", after=["B", "C"], id="D"),
        step("Document", after=["B"], id="E"),
    ], TOOLS, plan_id="p-test")


def test_a_valid_plan_gets_ids_assigned_by_the_code_in_order():
    plan = five_tasks()
    assert plan.id == "p-test" and list(plan.steps) == ["s1", "s2", "s3", "s4", "s5"]
    assert plan.steps["s4"].after == ("s2", "s3") and plan.steps["s5"].after == ("s2",)
    assert [s.id for s in plan.ready()] == ["s1"]
    assert make("x", [step()], TOOLS).id.startswith("p-")


def test_positions_work_as_names_and_a_steps_own_name_wins():
    plan = make("x", [step(), step(after=[1]), step(after=["1", "2"])], TOOLS)
    assert plan.steps["s3"].after == ("s1", "s2")
    named = make("x", [step(id="2"), step(id="b", after=["2"])], TOOLS)  # "2" is step 1's own name
    assert named.steps["s2"].after == ("s1",)


def test_every_problem_is_reported_at_once():
    found = reasons("", [
        step(does=""),
        step(tool="teleport"),
        step(done_when=""),
        step(after=["nope"]),
        step(tool="make_plan"),
        step(after=["6"], id="6"),
        step(id="6"),
    ])
    assert "the plan needs a goal" in found
    assert any("step 1 needs 'does'" in r for r in found)
    assert any("no tool 'teleport'" in r for r in found)
    assert any("step 3 needs 'done_when': one checkable result" in r for r in found)
    assert any("waits for 'nope'" in r for r in found)
    assert any("make_plan can't be a step" in r for r in found)
    assert any("step 6 can't wait for itself" in r for r in found)
    assert any("'6' is already used by step 6" in r for r in found)


def test_a_tool_that_isnt_available_now_is_refused_but_think_and_review_always_work():
    assert any("no tool 'web_search'" in r for r in reasons("x", [step(tool="web_search")], {"read_file"}))
    assert make("x", [step(tool="think"), step(tool="review")], set()).steps["s2"].tool == "review"


def test_cycles_are_refused_with_their_path():
    found = reasons("x", [step(after=["3"]), step(after=["1"]), step(after=["2"])])
    assert len(found) == 1 and found[0].startswith("steps wait for each other in a circle: step")


def test_limits():
    assert any(f"at most {MAX_STEPS}" in r for r in reasons("x", [step() for _ in range(MAX_STEPS + 1)]))
    assert any("one line" in r for r in reasons("x", [step(does="x" * 301)]))
    assert any("keep it under 500" in r for r in reasons("g" * 501, [step()]))
    assert reasons("x", []) == ["the plan needs a list of steps"]
    assert reasons("x", "not a list") == ["the plan needs a list of steps"]
    assert any("isn't an object" in r for r in reasons("x", ["just text"]))


def test_a_failure_blocks_only_what_depends_on_it():
    plan = five_tasks()
    plan.start("s1"); plan.finish("s1", "done", "intent is clear")
    assert [s.id for s in plan.ready()] == ["s2", "s3"]  # independent: either may go first
    plan.start("s2"); plan.finish("s2", "done", "plan written")
    plan.start("s3"); plan.finish("s3", "failed", "the vault search found nothing")
    assert plan.steps["s4"].status == "blocked"  # D: waits for the failed C
    assert [s.id for s in plan.ready()] == ["s5"]  # E: only waits for B
    plan.start("s5"); plan.finish("s5", "done", "documented")
    assert not plan.complete and not plan.ended  # C may still be retried once


def test_failure_spreads_to_every_descendant_and_a_retry_clears_it():
    plan = make("x", [step(tool="search_vault"), step(after=[1]), step(after=[2])], TOOLS)
    plan.start("s1"); plan.finish("s1", "failed", "no index")
    assert [plan.steps[s].status for s in ("s2", "s3")] == ["blocked", "blocked"]
    plan.retry("s1", Ruling("s1", "search by title instead", "the index is missing", "slower"))
    assert [plan.steps[s].status for s in ("s1", "s2", "s3")] == ["pending", "pending", "pending"]
    assert plan.rulings[0].line() == "Ruling (s1): search by title instead — the index is missing — slower"
    plan.start("s1"); plan.finish("s1", "failed", "still nothing")
    with pytest.raises(PlanError, match="failed 2 times: ask the user"):
        plan.retry("s1", Ruling("s1", "again", "why not", "time"))
    assert plan.ended and not plan.complete


def test_a_tool_step_is_done_only_with_evidence():
    plan = make("x", [step(tool="write_file"), step()], TOOLS)
    plan.start("s1")
    with pytest.raises(PlanError, match="write_file hasn't returned a result"):
        plan.finish("s1", "done", "I wrote it")  # a claim alone
    plan.finish("s1", "done_with_concerns", "written; the title may be wrong", evidence=True)
    assert plan.steps["s1"].status == "done" and plan.steps["s1"].outcome == "done_with_concerns"
    plan.start("s2"); plan.finish("s2", "done", "thought it through")  # think: no tool, no evidence needed
    assert plan.complete and plan.ended


def test_transitions_that_dont_fit_are_refused():
    plan = make("x", [step(), step(after=[1])], TOOLS)
    with pytest.raises(PlanError, match="s2 can't start: it waits for s1"):
        plan.start("s2")
    with pytest.raises(PlanError, match="no step 's9'"):
        plan.start("s9")
    with pytest.raises(PlanError, match="s1 is pending, not running or waiting"):
        plan.finish("s1", "done", "")
    plan.start("s1")
    with pytest.raises(PlanError, match="s1 can't start: it is running"):
        plan.start("s1")
    with pytest.raises(PlanError, match="outcome must be one of"):
        plan.finish("s1", "great", "")


def test_waiting_needs_input_skip_and_stop():
    plan = make("x", [step(tool="write_file"), step(after=[1]), step()], TOOLS)
    plan.start("s1"); plan.wait("s1")  # an approval card is open
    plan.resume("s1"); plan.finish("s1", "needs_input", "which folder?")
    assert plan.steps["s1"].status == "waiting" and plan.ready()[0].id == "s3"
    plan.skip("s1", Ruling("s1", "leave it out", "the user said so", "no note saved"))
    assert plan.steps["s2"].status == "blocked"
    assert plan.stop() == ["s2", "s3"] and plan.ended


def test_a_plan_round_trips_through_its_storage_shape():
    plan = five_tasks()
    plan.start("s1"); plan.finish("s1", "done", "ok")
    plan.rulings.append(Ruling("s3", "what", "why", "cost"))
    again = Plan.from_dict(plan.to_dict())
    assert again == plan and again.to_dict() == plan.to_dict()
