"""T1 of docs/orchestration.md: a plan's checks, its IDs, what runs next, and how failure spreads."""

import json
import sqlite3

import pytest

from aura.audit import AuditLog, category
from aura.plans import MAX_STEPS, Plan, PlanError, PlanStore, Ruling, make
from aura.state import StateStore

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
    with pytest.raises(PlanError, match="was stopped"):
        plan.start("s3")


def test_a_plan_round_trips_through_its_storage_shape():
    plan = five_tasks()
    plan.start("s1"); plan.finish("s1", "done", "ok")
    plan.rulings.append(Ruling("s3", "what", "why", "cost"))
    again = Plan.from_dict(plan.to_dict())
    assert again == plan and again.to_dict() == plan.to_dict()


# ── T2: plans kept in state.sqlite, every change in the audit log ─────────────────────────────



@pytest.fixture
def store(tmp_path):
    state = StateStore(tmp_path / "state.sqlite")
    audit = AuditLog(tmp_path / "actions.jsonl")
    yield PlanStore(state, audit), state, tmp_path / "actions.jsonl"
    state.close()


def events(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_a_plan_and_each_change_survive_a_restart(store, tmp_path):
    plans, state, _ = store
    plan = plans.create(five_tasks())
    plans.change(plan.id, lambda p: (p.start("s1"), p.finish("s1", "done", "intent is clear")))
    state.close()
    reopened = PlanStore(StateStore(tmp_path / "state.sqlite"), AuditLog(tmp_path / "actions.jsonl"))
    again = reopened.get(plan.id)
    assert again.steps["s1"].status == "done" and [s.id for s in again.ready()] == ["s2", "s3"]
    assert [p.id for p in reopened.active()] == [plan.id]


def test_a_stale_change_is_refused(store):
    plans, _, _ = store
    plan = plans.create(five_tasks())
    first, second = plans.get(plan.id), plans.get(plan.id)  # two readers of the same version
    first.start("s1")
    plans.save(first)
    second.start("s1")
    with pytest.raises(PlanError, match="changed since it was read"):
        plans.save(second)
    assert plans.get(plan.id).steps["s1"].attempts == 1  # the stale write changed nothing


def test_a_refused_transition_saves_nothing(store):
    plans, _, log = store
    plan = plans.create(five_tasks())
    before = len(events(log))
    with pytest.raises(PlanError, match="can't start"):
        plans.change(plan.id, lambda p: p.start("s4"))
    assert plans.get(plan.id).version == plan.version and len(events(log)) == before


def test_every_change_is_an_audit_event_the_observer_shows(store):
    plans, _, log = store
    plan = plans.create(make("Tidy Ideas", [step(tool="search_vault"), step(after=[1])], TOOLS))
    plans.change(plan.id, lambda p: p.start("s1"))
    plans.change(plan.id, lambda p: p.finish("s1", "failed", "no notes"))  # retryable, but…
    plans.change(plan.id, lambda p: p.stop())  # …Stop ends the plan anyway
    recorded = [(e["event"], e.get("step"), e.get("status")) for e in events(log)]
    assert recorded == [("plan_created", None, None), ("plan_step", "s1", "running"), ("plan_step", "s1", "failed"),
                        ("plan_step", "s2", "blocked"), ("plan_step", "s2", "skipped"), ("plan_ended", None, None)]
    assert {category(e["event"]) for e in events(log)} == {"actions"}
    created = events(log)[0]
    assert created["plan"] == plan.id and created["steps"] == 2 and created["goal"] == "Tidy Ideas"
    assert plans.active() == [] and plans.get(plan.id).ended


def test_results_are_kept_short(store):
    plans, _, _ = store
    plan = plans.create(make("x", [step()], TOOLS))
    plans.change(plan.id, lambda p: (p.start("s1"), p.finish("s1", "done", "word " * 1000)))
    assert len(plans.get(plan.id).steps["s1"].summary) <= 600


def test_plans_work_in_todays_schema_2_database_without_a_migration(tmp_path):
    path = tmp_path / "state.sqlite"
    StateStore(path).close()
    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == 2
    state = StateStore(path)
    plans = PlanStore(state, AuditLog(tmp_path / "a.jsonl"))
    assert plans.get(plans.create(five_tasks()).id).goal == "Ship the change"
    state.close()


def test_an_unknown_plan_is_reported(store):
    plans, _, _ = store
    with pytest.raises(PlanError, match="no plan 'p-nope'"):
        plans.get("p-nope")
