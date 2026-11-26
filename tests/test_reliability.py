"""Reliability: the runtime state store, durable conversations and approvals, turn lifecycle and
Stop, Developer and vault-task recovery after a restart, bounded feeds, and secret masking.

A "restart" here is a second Aura (or Developer) built on the same data folder: exactly what the
server does when it starts again.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from conftest import call, make_aura, tool_turn
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from test_developer import ENGINES as BUILD_ENGINES
from test_developer import SPEC, FakeContainer
from test_governor import env, offline_provenance  # noqa: F401 (fixtures)
from test_server import AUTH, TOKEN, FakeKeeper, events, kinds, post
from test_vault_tasks import ENGINES, TASK, FakeVaultContainer, aura_with, run, tidy, vault_files  # noqa: F401

from aura.audit import AuditLog
from aura.brain import CUT_SHORT, StaleApproval, Text
from aura.developer.container import RunOutcome
from aura.developer.workflow import Build, Developer, DeveloperError
from aura.lifecycle import Lifecycle
from aura.masking import MASK, Masker
from aura.middleware import MAX_TOOL_OUTPUT, bound
from aura.secrets import Secrets
from aura.server import create_app, stream_turn
from aura.state import FEED_KEEP, SCHEMA_VERSION, StateError, StateStore


def assert_paired(messages) -> None:
    """Every tool call the model is shown has its result (OpenAI-compatible servers insist)."""
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    for m in messages:
        for tc in getattr(m, "tool_calls", None) or []:
            assert tc["id"] in answered, f"tool call {tc['name']} has no result"


# ── the state store ─────────────────────────────────────────────────────────────────────────


def test_the_store_uses_wal_and_a_schema_version_and_keeps_data_across_reopen(tmp_path):
    path = tmp_path / "state.sqlite"
    store = StateStore(path)
    assert store.version == SCHEMA_VERSION
    assert sqlite3.connect(path).execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    store.set_meta("conversation:mac", "abc")
    store.put_record("build", "b-1", "queued", {"skill": "x"})
    store.close()
    again = StateStore(path)  # migrations are idempotent: nothing is recreated or lost
    assert again.get_meta("conversation:mac") == "abc"
    assert again.record("build", "b-1") == ("queued", {"skill": "x"})


def test_a_store_from_a_newer_aura_is_refused(tmp_path):
    path = tmp_path / "state.sqlite"
    StateStore(path).close()
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version = 99")
    db.close()
    with pytest.raises(StateError, match="newer Aura"):
        StateStore(path)


def test_a_failed_migration_leaves_nothing_half_done(tmp_path, monkeypatch):
    from aura import state as state_module

    broken = {1: [*state_module.MIGRATIONS[1], "CREATE TABLE meta (oops TEXT)"]}  # fails on the last statement
    monkeypatch.setattr(state_module, "MIGRATIONS", broken)
    with pytest.raises(sqlite3.OperationalError):
        StateStore(tmp_path / "state.sqlite")
    db = sqlite3.connect(tmp_path / "state.sqlite")
    assert db.execute("PRAGMA user_version").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] == 0


def test_transitions_are_atomic_under_concurrency(tmp_path):
    store = StateStore(tmp_path / "state.sqlite")
    store.add_approval("a-1", "t-1", "conv", "int-1", "digest", ["create_folder"], "{}")
    store.put_record("vault_task", "k", "awaiting", {})
    wins, moves = [], []
    barrier = threading.Barrier(16)

    def race():
        barrier.wait()
        wins.append(store.claim_approval("a-1"))
        moves.append(store.transition("vault_task", "k", ("awaiting",), "finishing"))

    threads = [threading.Thread(target=race) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert wins.count(True) == 1 and moves.count(True) == 1
    assert store.approval("a-1")["state"] == "submitted"


def test_feeds_and_records_are_bounded(tmp_path):
    store = StateStore(tmp_path / "state.sqlite")
    for i in range(FEED_KEEP + 200):
        store.add_event("build", "b-1", f"line {i}")
    feed = store.events("build", "b-1")
    assert len(feed) == FEED_KEEP and feed[-1]["text"] == f"line {FEED_KEEP + 199}"
    store.add_event("build", "b-1", "x" * 5000)
    assert store.events("build", "b-1")[-1]["text"].endswith("more characters]")
    for i in range(30):
        store.put_record("build", f"b-{i}", "failed", {})
    store.put_record("build", "b-live", "developing", {})
    assert store.prune_records("build", keep_states=("developing",), keep=10) == 20
    assert store.record("build", "b-live") is not None


def test_unfinished_turns_are_marked_interrupted_at_start(tmp_path):
    store = StateStore(tmp_path / "state.sqlite")
    store.begin_turn("t-1", "chat", "mac", "conv")
    lifecycle = Lifecycle(StateStore(tmp_path / "state.sqlite"), threading.Lock())
    assert lifecycle.interrupted_at_start == 1
    assert store.turn("t-1")["state"] == "interrupted"


# ── conversations and approvals survive a restart ───────────────────────────────────────────


def test_the_conversation_survives_a_restart(settings):
    aura, _ = make_aura(settings, AIMessage("Nice to meet you, Ada."))
    aura.chat("My name is Ada.")
    again, model = make_aura(settings, AIMessage("You're Ada."), fresh=False)
    assert again.thread_id == aura.thread_id
    again.chat("What's my name?")
    assert "My name is Ada." in str(model.agent_calls()[-1])
    assert [m["role"] for m in again.transcript()] == ["user", "aura", "user", "aura"]


def _terminal_aura(settings, model=None):
    """A second Aura process on the same data folder, as `uv run aura` builds it."""
    from conftest import ScriptedModel

    from aura.brain import Aura
    from aura.main import TERMINAL_ROLE
    from aura.tools import Toolbox

    return Aura(settings=settings, model=model or ScriptedModel(script=[]), toolbox=Toolbox.from_settings(settings),
                role=TERMINAL_ROLE)  # fmt: skip


def test_a_terminal_reset_does_not_erase_the_apps_conversation(settings):
    app, _ = make_aura(settings, AIMessage("Nice to meet you, Ada."))
    app.chat("My name is Ada.")
    terminal = _terminal_aura(settings)
    assert terminal.thread_id != app.thread_id  # its own conversation, not the app's
    terminal.new_conversation()  # what /new in the terminal does
    restarted, _ = make_aura(settings, fresh=False)
    assert restarted.thread_id == app.thread_id
    assert [m["role"] for m in restarted.transcript()] == ["user", "aura"]


def test_the_terminal_resumes_its_own_conversation(settings):
    from conftest import ScriptedModel

    first = _terminal_aura(settings, ScriptedModel(script=[AIMessage("Hello from the terminal.")]))
    first.chat("Hi.")
    again = _terminal_aura(settings)
    assert again.thread_id == first.thread_id
    app, _ = make_aura(settings, fresh=False)
    assert app.thread_id != first.thread_id


def test_the_terminal_entry_point_uses_its_own_role(settings, monkeypatch):
    import builtins

    import aura.main as terminal_main

    built = []

    def fake_aura(**kwargs):
        built.append(kwargs)
        return _terminal_aura(settings)

    monkeypatch.setattr(terminal_main, "Aura", fake_aura)
    monkeypatch.setattr(builtins, "input", lambda prompt="": (_ for _ in ()).throw(EOFError))
    terminal_main.main()
    assert built == [{"role": terminal_main.TERMINAL_ROLE, "publish_capabilities": True}]


def test_the_conversation_store_is_bounded_and_a_reset_forgets_on_disk(settings):
    from aura.brain import CHECKPOINTS_KEPT

    aura, model = make_aura(settings, *[AIMessage(f"Reply {i}.") for i in range(12)])
    for i in range(12):
        aura.chat(f"Message {i}.")
    conn = aura.toolbox.checkpointer().conn
    count = lambda thread: conn.execute("SELECT count(*) FROM checkpoints WHERE thread_id = ?", (thread,)).fetchone()[0]  # noqa: E731
    assert count(aura.thread_id) == CHECKPOINTS_KEPT
    assert len(aura.transcript()) == 24  # the history is all still there
    old = aura.thread_id
    aura.new_conversation()
    assert count(old) == 0


def test_a_pending_approval_survives_a_restart_and_can_be_answered(settings):
    aura, _ = make_aura(settings, tool_turn(call("create_folder", path="Plans")))
    (ask,) = [e for e in aura.stream("Make a Plans folder.") if hasattr(e, "approval_id")]
    again, _ = make_aura(settings, AIMessage("Created it."), fresh=False)
    assert again.awaiting_approval and again.approval_id == ask.approval_id
    assert again.pending()["actions"][0]["tool"] == "create_folder"
    list(again.resume([True], approval_id=ask.approval_id))
    assert (settings.vault_dir / "Plans").is_dir()
    assert again.toolbox.state.approval(ask.approval_id)["state"] == "done"


def test_an_approval_answered_before_a_crash_is_never_run_again(settings):
    aura, _ = make_aura(settings, tool_turn(call("create_folder", path="Plans")))
    list(aura.stream("Make a Plans folder."))
    assert aura.toolbox.state.claim_approval(aura.approval_id)  # answered; the server died before it ran
    again, model = make_aura(settings, AIMessage("Okay."), fresh=False)
    assert not again.awaiting_approval
    assert "not repeated" in again.recovery_note
    assert again.toolbox.state.approval(aura.approval_id)["state"] == "unrecoverable"
    again.chat("Hello again.")
    assert not (settings.vault_dir / "Plans").exists()
    assert_paired(model.agent_calls()[-1])


def test_a_duplicate_approval_runs_the_action_at_most_once(settings):
    aura, _ = make_aura(settings, tool_turn(call("create_folder", path="Plans")), AIMessage("Done."))
    client = TestClient(create_app(aura, TOKEN, FakeKeeper()), base_url="http://127.0.0.1")
    evs = events(post(client, "/v1/chat", {"text": "Make a Plans folder."}))
    approval_id = next(d for k, d in evs if k == "approval")["approval_id"]
    first = post(client, "/v1/approvals", {"approvals": [True], "approval_id": approval_id})
    assert "status" in kinds(events(first))
    second = post(client, "/v1/approvals", {"approvals": [True], "approval_id": approval_id})
    assert second.status_code == 409
    created = [e for e in AuditLog(settings.audit_log).entries() if e["event"] == "approval" and e["tool"] == "create_folder"]
    assert len(created) == 1


def test_stale_approvals_are_refused(settings):
    aura, _ = make_aura(settings, tool_turn(call("create_folder", path="Old")), tool_turn(call("create_folder", path="New")))
    list(aura.stream("Make Old."))
    old = aura.approval_id
    aura.new_conversation()  # a reset: the old card can't be answered
    assert aura.toolbox.state.approval(old)["state"] == "stale"
    with pytest.raises(RuntimeError):
        list(aura.resume([True], approval_id=old))
    list(aura.stream("Make New."))
    with pytest.raises(StaleApproval, match="out of date"):  # an old card can't approve a newer request
        aura.resume([True], approval_id=old)
    assert aura.awaiting_approval and aura.pending()["actions"][0]["args"] == {"path": "New"}
    assert not (settings.vault_dir / "Old").exists() and not (settings.vault_dir / "New").exists()


def test_an_approval_goes_stale_when_permissions_change(settings, monkeypatch):
    aura, model = make_aura(settings, tool_turn(call("create_folder", path="Plans")), AIMessage("Hi."))
    list(aura.stream("Make a Plans folder."))
    monkeypatch.setattr(aura, "_permissions", lambda: '{"skills": ["something new"]}')
    with pytest.raises(StaleApproval, match="permissions changed"):
        aura.resume([True])
    assert not aura.awaiting_approval and not (settings.vault_dir / "Plans").exists()
    aura.chat("Hello.")
    assert_paired(model.agent_calls()[-1])


# ── turn lifecycle: stop, disconnect, failure ───────────────────────────────────────────────


class BlockingContainer(FakeVaultContainer):
    """An engine that works until it is stopped."""

    def __init__(self):
        super().__init__(tidy)
        self.started = threading.Event()

    def run(self, engine, workspace, run_dir, prompt, budget_usd, timeout, on_event, stop):
        self.started.set()
        stop.wait(10)
        return RunOutcome(stopped=stop.is_set())


def test_stop_ends_the_turn_after_the_container_is_cleaned_up(settings, vault_files):
    container = BlockingContainer()
    aura, model = aura_with(settings, container, tool_turn(call("ask_developer", task=TASK)), AIMessage("Hi."))
    client = TestClient(create_app(aura, TOKEN, FakeKeeper()), base_url="http://127.0.0.1")
    evs = events(post(client, "/v1/chat", {"text": "Tidy my vault."}))
    approval_id = next(d for k, d in evs if k == "approval")["approval_id"]
    out: dict = {}
    worker = threading.Thread(target=lambda: out.update(r=post(client, "/v1/approvals",
                                                               {"approvals": [True], "approval_id": approval_id})))  # fmt: skip
    worker.start()
    assert container.started.wait(5)
    turn_id = client.get("/v1/health", headers=AUTH).json()["active_turn"]["id"]
    stopped = post(client, "/v1/stop", {"turn_id": turn_id}).json()
    assert stopped["stopped"] is True  # returned only once the turn was released
    worker.join(5)
    evs = events(out["r"])
    assert kinds(evs)[-2:] == ["stopped", "done"] and evs[0][1]["turn_id"] == turn_id
    health = client.get("/v1/health", headers=AUTH).json()
    assert health["busy"] is False and health["active_turn"] is None
    assert aura.toolbox.state.turn(turn_id)["state"] == "stopped"
    assert post(client, "/v1/stop", {"turn_id": turn_id}).json()["stopped"] is False  # idempotent
    assert (vault_files / "forms.md").exists()  # nothing was written
    assert kinds(events(post(client, "/v1/chat", {"text": "Hello."})))[-1] == "done"
    assert_paired(model.agent_calls()[-1])


def test_stopping_nothing_changes_nothing(settings):
    aura, _ = make_aura(settings)
    client = TestClient(create_app(aura, TOKEN, FakeKeeper()), base_url="http://127.0.0.1")
    assert post(client, "/v1/stop", {}).json() == {"stopped": False, "turn": None}


def test_a_client_disconnect_releases_the_turn_without_leaking_its_worker(settings):
    aura, _ = make_aura(settings)
    lifecycle = Lifecycle(aura.toolbox.state, threading.Lock())
    cancelled, finished = [], []

    def agent_run():
        try:
            yield Text("first ")
            time.sleep(0.3)  # a step in progress when the client leaves
            finished.append(time.monotonic())
            yield Text("second")
        finally:
            finished.append("closed")

    lifecycle.on_cancel = lambda: cancelled.append(True)
    turn = lifecycle.begin("chat", "mac", "conv")
    response = stream_turn(agent_run(), lifecycle, turn, FakeKeeper(), check_cold=False)

    async def leave_early():
        body = response.body_iterator
        assert "thinking" in await body.__anext__()
        await asyncio.wait_for(body.__anext__(), 0.1)  # start the slow step, then leave mid-step
        await body.aclose()

    try:
        asyncio.run(leave_early())
    except TimeoutError:
        pass
    assert turn.released.wait(2)
    assert cancelled == [True]
    assert "closed" in finished  # the run was closed, after its step ended: no worker left behind
    assert aura.toolbox.state.turn(turn.id)["state"] in ("abandoned", "stopped")
    assert not lifecycle.lock.locked()


def test_a_model_failure_releases_the_turn_and_is_reported(settings):
    from test_server import Unreachable

    aura, _ = make_aura(settings)
    broken = type(aura)(settings=aura.settings, model=Unreachable(), toolbox=aura.toolbox)
    client = TestClient(create_app(broken, TOKEN, FakeKeeper()), base_url="http://127.0.0.1")
    assert "error" in kinds(events(post(client, "/v1/chat", {"text": "Hi"})))
    health = client.get("/v1/health", headers=AUTH).json()
    assert health["busy"] is False and "unreachable" in health["last_failure"]


# ── Developer builds and vault tasks after a restart ────────────────────────────────────────


def build_record(stage: str, **extra) -> dict:
    b = Build(id=f"b-{stage[:6]}", skill="pdf_text", engine="claude", specification=SPEC, improve=False,
              budget_usd=2.0, max_rounds=3, stage=stage, **extra)  # fmt: skip
    return b.record()


@pytest.mark.parametrize("stage", ["queued", "developing", "auditing", "repairing"])
def test_a_running_build_is_recovered_as_interrupted(env, tmp_path, stage):  # noqa: F811
    settings, workshop, _, _ = env
    store = StateStore(tmp_path / "state.sqlite")
    store.put_record("build", f"b-{stage[:6]}", stage, build_record(stage))
    store.add_event("build", f"b-{stage[:6]}", "Writing skill/skill.py (round 1)")
    d = Developer(settings, workshop, FakeContainer(), Secrets(), workshop.audit, engines=BUILD_ENGINES, state=store)
    (build,) = d.builds.values()
    assert build.stage == "interrupted" and build.recoverable and stage in build.message
    assert build.feed[-1]["text"] == "Writing skill/skill.py (round 1)"  # the log is kept
    assert d.recovery()[0]["actions"] == ["retry", "dismiss"]
    assert store.record("build", build.id)[0] == "interrupted"


def test_finished_builds_keep_their_outcome_and_a_lost_audit_card_is_interrupted(env, tmp_path):  # noqa: F811
    settings, workshop, _, _ = env
    store = StateStore(tmp_path / "state.sqlite")
    for stage, extra in (("failed", {}), ("stopped", {}), ("ready", {"task_id": "gone"}),
                         ("ready", {"task_id": "kept", "decision": "deployed"})):  # fmt: skip
        record = build_record(stage, **extra) | {"id": f"b-{stage}-{extra.get('task_id', '')}"}
        store.put_record("build", record["id"], stage, record)
    d = Developer(settings, workshop, FakeContainer(), Secrets(), workshop.audit, engines=BUILD_ENGINES, state=store)
    stages = {b.id: b.public(workshop)["stage"] for b in d.builds.values()}
    assert stages == {"b-failed-": "failed", "b-stopped-": "stopped", "b-ready-gone": "interrupted",
                      "b-ready-kept": "deployed"}  # fmt: skip
    assert [i["id"] for i in d.recovery()] == ["b-ready-gone"]


def test_retry_and_dismiss_are_the_users_clicks(env, tmp_path):  # noqa: F811
    settings, workshop, _, _ = env
    store = StateStore(tmp_path / "state.sqlite")
    store.put_record("build", "b-develo", "developing", build_record("developing"))
    d = Developer(settings, workshop, FakeContainer(error="Docker isn't running"), Secrets(), workshop.audit,
                  engines=BUILD_ENGINES, state=store)  # fmt: skip
    new = d.retry("b-develo")
    assert new.id != "b-develo" and new.specification == SPEC.strip()
    assert not d.builds["b-develo"].recoverable and d.recovery() == []
    with pytest.raises(DeveloperError):
        d.retry("b-develo")
    d.dismiss("b-develo")  # idempotent


def test_stop_and_finish_are_idempotent(env):  # noqa: F811
    settings, workshop, _, _ = env
    d = Developer(settings, workshop, FakeContainer(error="x"), Secrets(), workshop.audit, engines=BUILD_ENGINES)
    build = d.start("pdf_text", SPEC, "claude", wait=True)
    feed = len(build.feed)
    d.stop(build.id)
    d.stop(build.id)
    assert len(build.feed) == feed  # stopping a finished build changes nothing
    assert d.finish_task("no-such-call", None) == []


def test_failed_scratch_is_kept_for_diagnosis_then_swept_without_credentials(env, tmp_path):  # noqa: F811
    settings, workshop, _, _ = env
    store = StateStore(tmp_path / "state.sqlite")
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
    for name, finished in (("b-old", old), ("b-recent", datetime.now(timezone.utc).isoformat(timespec="seconds"))):
        folder = settings.dev_dir / name
        (folder / "codex").mkdir(parents=True)
        (folder / "codex" / "auth.json").write_text("{}")
        (folder / "work.log").write_text("diagnosis")
        record = build_record("failed") | {"id": name, "scratch": str(folder), "finished": finished}
        store.put_record("build", name, "failed", record)
    (settings.dev_dir / "v-orphan").mkdir()
    Developer(settings, workshop, FakeContainer(), Secrets(), workshop.audit, engines=BUILD_ENGINES, state=store)
    assert not (settings.dev_dir / "b-old").exists() and not (settings.dev_dir / "v-orphan").exists()
    assert (settings.dev_dir / "b-recent" / "work.log").exists()
    assert not (settings.dev_dir / "b-recent" / "codex").exists()  # never kept: a sign-in copy


def test_a_vault_review_survives_a_restart_and_still_guards_each_write(settings, vault_files):
    aura, _ = aura_with(settings, FakeVaultContainer(tidy), tool_turn(call("ask_developer", task=TASK)))
    run(aura.stream("Tidy my vault."))
    _, review = run(aura.resume([True]))
    assert {a.tool for a in review.actions} == {"apply_change"}
    (vault_files / "Map_of_the_Ascent.md").write_text("# Map, edited by hand meanwhile\n")
    again, model = make_aura(settings, AIMessage("Done."), fresh=False)  # the restart
    assert again.awaiting_approval and again.approval_id == review.approval_id
    events = list(again.resume([True] * len(review.actions), approval_id=review.approval_id))
    assert (vault_files / "The_Absolute_R" / "forms.md").exists()
    assert (vault_files / "Map_of_the_Ascent.md").read_text() == "# Map, edited by hand meanwhile\n"
    assert "changed after the task started" in "".join(e.text for e in events if isinstance(e, Text))
    assert model.agent_calls() == []
    assert list(settings.dev_dir.iterdir()) == []


def test_a_vault_review_whose_copy_is_gone_is_settled_not_rerun(settings, vault_files):
    import shutil

    container = FakeVaultContainer(tidy)
    aura, _ = aura_with(settings, container, tool_turn(call("ask_developer", task=TASK)))
    run(aura.stream("Tidy my vault."))
    run(aura.resume([True]))
    for folder in settings.dev_dir.iterdir():
        shutil.rmtree(folder)
    again, model = make_aura(settings, AIMessage("Okay."), fresh=False)
    assert not again.awaiting_approval and "Nothing was written" in again.recovery_note
    again.chat("Hello.")
    assert len(container.runs) == 1 and (vault_files / "forms.md").exists()
    assert_paired(model.agent_calls()[-1])


def test_a_new_conversation_drops_a_waiting_vault_review(settings, vault_files):
    aura, _ = aura_with(settings, FakeVaultContainer(tidy), tool_turn(call("ask_developer", task=TASK)))
    run(aura.stream("Tidy my vault."))
    run(aura.resume([True]))
    assert aura.toolbox.developer.tasks
    aura.new_conversation()
    assert not aura.toolbox.developer.tasks and list(settings.dev_dir.iterdir()) == []
    assert (vault_files / "forms.md").exists()


def test_a_vault_task_running_at_a_restart_is_interrupted_and_never_rerun(settings, vault_files):
    aura, _ = make_aura(settings)
    aura.toolbox.state.put_record("vault_task", "call_x", "running", {"id": "v-dead", "engine": "claude", "task": TASK})
    again, _ = make_aura(settings, fresh=False)
    developer = again.toolbox.developer
    assert developer.state.record("vault_task", "call_x")[0] == "interrupted"
    (item,) = developer.recovery()
    assert item["kind"] == "vault_task" and item["actions"] == ["dismiss"]
    with pytest.raises(DeveloperError, match="isn't run again"):
        developer.run_task("call_x", TASK, "claude", ".", again.toolbox.vault)
    developer.dismiss("call_x")
    assert developer.recovery() == []


def test_the_recovery_section_over_http(env, tmp_path, settings):  # noqa: F811
    aura, _ = make_aura(settings)
    aura.toolbox.state.put_record("build", "b-develo", "developing", build_record("developing"))
    again, _ = make_aura(settings, fresh=False)
    client = TestClient(create_app(again, TOKEN, FakeKeeper()), base_url="http://127.0.0.1")
    (item,) = client.get("/v1/recovery", headers=AUTH).json()["items"]
    assert item["id"] == "b-develo"
    assert client.get("/v1/health", headers=AUTH).json()["recoverable"] == 1
    assert post(client, "/v1/recovery/b-develo/dismiss").json() == {"items": []}
    assert post(client, "/v1/recovery/nothing/dismiss").status_code == 404


# ── bounded output and truncation markers ───────────────────────────────────────────────────


def test_long_tool_output_is_bounded_with_a_marker():
    text = "start " + "x" * 200_000 + " end"
    cut = bound(text)
    assert len(cut) < MAX_TOOL_OUTPUT + 200 and "cut by Aura" in cut
    assert cut.startswith("start ") and cut.endswith(" end")
    assert bound("short") == "short"


def test_a_builds_feed_is_bounded_and_marks_long_lines(env, tmp_path):  # noqa: F811
    settings, workshop, _, _ = env
    store = StateStore(tmp_path / "state.sqlite")
    d = Developer(settings, workshop, FakeContainer(error="x"), Secrets(), workshop.audit, engines=BUILD_ENGINES, state=store)
    build = d.start("pdf_text", SPEC, "claude", wait=True)
    for i in range(1000):
        build.say(f"line {i}")
    build.say("y" * 2000)
    assert len(build.feed) == 300 and len(store.events("build", build.id)) == FEED_KEEP
    assert build.feed[-1]["text"].endswith("more characters]")


def test_a_long_reply_is_cut_with_a_marker_when_restored(settings):
    aura, _ = make_aura(settings, AIMessage("z" * 30_000))
    aura.chat("Write a lot.")
    assert aura.transcript()[-1]["text"].endswith("more characters]")


def test_the_conversation_endpoint_restores_the_chat(settings):
    aura, _ = make_aura(settings, AIMessage("Hello, Ada."))
    aura.chat("I'm Ada.")
    again, _ = make_aura(settings, fresh=False)
    client = TestClient(create_app(again, TOKEN, FakeKeeper()), base_url="http://127.0.0.1")
    body = client.get("/v1/conversation", headers=AUTH).json()
    assert body["messages"] == [{"role": "user", "text": "I'm Ada."}, {"role": "aura", "text": "Hello, Ada."}]
    assert body["pending"] is None


# ── secret masking ──────────────────────────────────────────────────────────────────────────

LITERAL = "q7Zr" + "Lm2#Wp9!Xs4%Tn8&Vb3"  # has characters a token pattern wouldn't treat as one word
SLACK = "xoxb-" + "1234567890-" + "abcdefghijKLMNOP"


@pytest.mark.parametrize("secret", [LITERAL, SLACK])
def test_a_secret_split_across_chunks_is_still_masked(secret):
    masker = Masker(lambda: [LITERAL])
    text = f"The token is {secret} and that's it."
    for i in range(1, len(text)):
        for j in range(i, len(text), 7):
            stream = masker.stream()
            out = stream.feed(text[:i]) + stream.feed(text[i:j]) + stream.feed(text[j:]) + stream.flush()
            assert secret not in out and MASK in out, (i, j, out)
            assert out.startswith("The token is ") and out.endswith(" and that's it.")


def test_a_bearer_credential_split_from_its_header_is_masked():
    masker = Masker()
    token = "abcdefghij" + "0123456789XYZ"
    stream = masker.stream()
    out = stream.feed("Authorization: Bearer ") + stream.feed(token[:8]) + stream.feed(token[8:] + "\n") + stream.flush()
    assert token not in out and MASK in out


def test_masking_large_repetitive_output_is_fast_and_complete():
    masker = Masker(lambda: [LITERAL])
    chunk = "lorem ipsum dolor " * 2000
    text = (chunk + LITERAL + " " + SLACK + " ") * 20  # ~720 KB
    started = time.monotonic()
    masked = masker.mask(text)
    stream = masker.stream()
    streamed = "".join(stream.feed(text[i:i + 997]) for i in range(0, len(text), 997)) + stream.flush()
    assert time.monotonic() - started < 10
    for out in (masked, streamed):
        assert LITERAL not in out and SLACK not in out and out.count(MASK) == 40


def test_audit_entries_and_tool_results_are_masked(settings, tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl", Masker(lambda: [LITERAL]))
    log.record("note", detail=f"pasted {LITERAL}", nested={"list": [SLACK]})
    raw = (tmp_path / "audit.jsonl").read_text()
    assert LITERAL not in raw and SLACK not in raw and json.loads(raw)["detail"] == f"pasted {MASK}"


def test_keychain_values_are_masked_including_inside_a_sign_in(settings):
    aura, _ = make_aura(settings)
    secrets = aura.toolbox.secrets
    secrets.backend.set_password("local.aura", "google_tokens", json.dumps({"access": "goog" + "le-access-0123456789"}))
    aura.toolbox.masker.refresh()
    assert aura.toolbox.masker.mask("token google-access-0123456789 here") == f"token {MASK} here"
