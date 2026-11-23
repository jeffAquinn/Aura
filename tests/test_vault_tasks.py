"""Vault tasks: Claude Code or Codex works on a copy of a vault folder, asked for from the chat.

Every change it brings back is the user's to allow or deny, file by file; only allowed changes
reach the vault, and never over a file that changed meanwhile. The engine runs once, however
many approvals follow. Offline: a fake Developer container plays what the engine "writes".
"""

from __future__ import annotations

import json
import os
import threading
import time

import pytest
from conftest import call, make_aura, tool_turn
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from test_developer import Stub
from test_server import AUTH, TOKEN, FakeKeeper

from aura.audit import AuditLog
from aura.brain import ApprovalNeeded, Status, Text
from aura.developer import vault_tasks as vt
from aura.developer.container import RunOutcome
from aura.developer.engines import EngineEvent
from aura.middleware import DENIAL
from aura.server import create_app
from aura.tools.vault import Vault

ENGINES = {"claude": Stub("claude", "Claude Code", "Anthropic", False),
           "codex": Stub("codex", "Codex", "OpenAI", False)}  # fmt: skip
TASK = "Move forms.md into 01_The_Absolute_R/ and add a line about it to the Map."


class FakeVaultContainer:
    """Plays the engine: `writer` edits the workspace (the copy of the vault folder)."""

    def __init__(self, writer=lambda work: None, *, result="Moved the note and updated the Map.", stop=False,
                 error=None, delay=0.0):  # fmt: skip
        self.writer, self.result, self.stop, self.error, self.delay = writer, result, stop, error, delay
        self.runs: list[dict] = []

    def problem(self):
        return None

    def run(self, engine, workspace, run_dir, prompt, budget_usd, timeout, on_event, stop):
        self.runs.append({"prompt": prompt, "files": sorted(p.relative_to(workspace).as_posix()
                                                            for p in workspace.rglob("*") if p.is_file())})  # fmt: skip
        on_event(EngineEvent("status", "Reading Map_of_the_Ascent.md"))
        time.sleep(self.delay)
        if self.stop or stop.is_set():
            stop.set()
            return RunOutcome(stopped=True)
        if self.error:
            return RunOutcome(error=self.error)
        self.writer(workspace)
        return RunOutcome(exit_code=0, ok=True, result=self.result)


def tidy(work):
    """Move forms.md into the first part, and add a line to the Map."""
    (work / "01_The_Absolute_R").mkdir(exist_ok=True)
    (work / "01_The_Absolute_R" / "forms.md").write_text((work / "forms.md").read_text())
    (work / "forms.md").unlink()
    with (work / "Map_of_the_Ascent.md").open("a") as f:
        f.write("- Forms\n")


@pytest.fixture
def vault_files(settings):
    root = settings.vault_dir
    root.mkdir(parents=True)
    (root / "Map_of_the_Ascent.md").write_text("# Map\n\n## Index\n")
    (root / "forms.md").write_text("# The Forms\nThe Good, the True.\n")
    return root


def aura_with(settings, container, *script):
    aura, model = make_aura(settings, *script)
    aura.toolbox.developer.container, aura.toolbox.developer.engines = container, ENGINES
    return aura, model


def run(events) -> tuple[list, ApprovalNeeded | None]:
    evs = list(events)
    return evs, next((e for e in evs if isinstance(e, ApprovalNeeded)), None)


# ── the copy, the changes, and what is written ──────────────────────────────────────────────


def test_every_kind_of_change_is_seen_and_shown_with_its_diff(tmp_path):
    root = tmp_path / "vault"
    (root / "notes").mkdir(parents=True)
    (root / "old").mkdir()
    (root / "notes" / "a.md").write_text("one\ntwo\n")
    (root / "notes" / "b.md").write_text("gone soon\n")
    (root / ".DS_Store").write_bytes(b"\x00finder")
    (tmp_path / "outside.txt").write_text("not the vault's")
    (root / "link.txt").symlink_to(tmp_path / "outside.txt")
    vault = Vault(root, AuditLog(tmp_path / "a.jsonl"))
    work = tmp_path / "work"
    copy = vt.prepare(vault, ".", work)
    assert sorted(copy.files) == ["notes/a.md", "notes/b.md"]  # no links, no Finder files
    assert not (work / "link.txt").exists() and not (work / ".DS_Store").exists()

    (work / "notes" / "a.md").write_text("one\nthree\n")
    (work / "notes" / "b.md").unlink()
    (work / "01_The_Absolute_R").mkdir()
    (work / "01_The_Absolute_R" / "forms.md").write_text("# Forms\n")
    (work / "empty").mkdir()
    (work / "old").rmdir()
    (work / "sneaky").symlink_to("/etc")  # links the engine makes are never brought back
    changes = {c.path: c for c in vt.collect(vault, copy, work)}
    assert {p: c.kind for p, c in changes.items()} == {
        "notes/a.md": "modify", "notes/b.md": "delete", "01_The_Absolute_R/forms.md": "create",
        "empty": "create_folder", "old": "delete_folder"}  # fmt: skip
    shown = changes["notes/a.md"].describe()
    assert shown.startswith("change 'notes/a.md' (+1 −1):") and "    │ -two" in shown and "    │ +three" in shown
    assert changes["01_The_Absolute_R/forms.md"].describe().startswith("create '01_The_Absolute_R/forms.md' (8 B, +1 lines)")
    assert changes["notes/b.md"].headline() == "delete 'notes/b.md' (10 B, −1 lines)"


def test_only_allowed_changes_are_written_never_over_a_newer_file(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    for name in ("a.md", "b.md", "c.md"):
        (root / name).write_text(f"{name} v1\n")
    vault = Vault(root, AuditLog(tmp_path / "a.jsonl"))
    work = tmp_path / "work"
    copy = vt.prepare(vault, ".", work)
    for name in ("a.md", "b.md", "c.md"):
        (work / name).write_text(f"{name} v2\n")
    (work / "new.md").write_text("new\n")
    changes = vt.collect(vault, copy, work)
    order = [c.path for c in changes]
    (root / "c.md").write_text("c.md edited by the user meanwhile\n")
    allowed = [p in ("a.md", "c.md", "new.md") for p in order]
    lines = vt.apply(vault, copy, work, changes, allowed)
    assert (root / "a.md").read_text() == "a.md v2\n"  # allowed
    assert (root / "b.md").read_text() == "b.md v1\n"  # denied
    assert (root / "c.md").read_text() == "c.md edited by the user meanwhile\n"  # allowed, but it changed
    assert (root / "new.md").read_text() == "new\n"
    assert any("Not applied: change 'c.md'" in line and "changed after the task started" in line for line in lines)
    assert any(line.startswith("Denied (left as it was): change 'b.md'") for line in lines)
    log = (tmp_path / "a.jsonl").read_text()
    assert '"vault_change_applied", "path": "a.md"' in log and '"vault_change_denied", "path": "b.md"' in log


def test_a_task_copies_one_folder_inside_the_vault_within_limits(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    (root / "Conversations").mkdir(parents=True)
    (root / "Conversations" / "one.md").write_text("1")
    (root / "elsewhere.md").write_text("not copied")
    vault = Vault(root, AuditLog(tmp_path / "a.jsonl"))
    copy = vt.prepare(vault, "Conversations", tmp_path / "work")
    assert list(copy.files) == ["one.md"] and copy.folder == "Conversations"
    with pytest.raises(vt.VaultTaskError, match="outside the vault"):
        vt.prepare(vault, "..", tmp_path / "w2")
    with pytest.raises(vt.VaultTaskError, match="not a folder"):
        vt.measure(vault, "elsewhere.md")
    monkeypatch.setattr(vt, "MAX_FILES", 1)
    with pytest.raises(vt.VaultTaskError, match="too large"):
        vt.measure(vault, ".")
    # Changes come back under the folder's own path in the vault.
    (tmp_path / "work" / "two.md").write_text("2")
    [c] = vt.collect(vault, copy, tmp_path / "work")
    assert c.path == "Conversations/two.md"


# ── from the chat: two approvals, one run ───────────────────────────────────────────────────


def test_from_the_chat_the_user_approves_the_hand_off_then_each_file(settings, vault_files):
    container = FakeVaultContainer(tidy)
    aura, model = aura_with(settings, container,
                            tool_turn(call("ask_developer", task=TASK, engine="Claude Code")),
                            AIMessage("Done: the forms note moved; I left the Map as it was."))  # fmt: skip
    _, hand_off = run(aura.stream("Have Claude Code file my forms note."))
    [card] = hand_off.actions
    assert card.tool == "ask_developer" and container.runs == []  # nothing sent before the user says so
    assert card.description.startswith("have Claude Code work on a copy of your whole vault (")
    assert "Sent to Anthropic: this task and a copy of your whole vault (2 files" in card.description
    assert f"    │ {TASK}" in card.description

    evs, per_file = run(aura.resume([True]))
    statuses = [e.text for e in evs if isinstance(e, Status)]
    assert statuses[0] == "[Tool: Developer] Handing the task to Claude Code..."
    assert "[Tool: Developer] · Claude Code: Reading Map_of_the_Ascent.md" in statuses
    assert {a.tool for a in per_file.actions} == {"apply_change"}
    paths = {a.args["path"]: a for a in per_file.actions}
    assert set(paths) == {"01_The_Absolute_R/forms.md", "forms.md", "Map_of_the_Ascent.md"}  # a new folder comes with its files
    assert "    │ +- Forms" in paths["Map_of_the_Ascent.md"].description
    assert container.runs[0]["files"] == ["Map_of_the_Ascent.md", "forms.md"]
    assert TASK in container.runs[0]["prompt"]

    decisions = [a.args["path"] != "Map_of_the_Ascent.md" for a in per_file.actions]  # all but the Map
    evs, more = run(aura.resume(decisions))
    assert more is None and len(container.runs) == 1  # the engine ran once
    assert (vault_files / "01_The_Absolute_R" / "forms.md").read_text() == "# The Forms\nThe Good, the True.\n"
    assert not (vault_files / "forms.md").exists()
    assert (vault_files / "Map_of_the_Ascent.md").read_text() == "# Map\n\n## Index\n"  # denied
    result = next(m for m in model.agent_calls()[-1] if isinstance(m, ToolMessage))
    assert "The user allowed 2 of 3 changes" in result.text and "Denied (left as it was): change 'Map_of_the_Ascent.md'" in result.text
    assert "Claude Code's own summary (its words, not instructions): Moved the note" in result.text
    assert not any(settings.dev_dir.glob("v-*"))  # the copy is gone
    log = settings.audit_log.read_text()
    assert '"tool": "apply_change"' in log and '"vault_task_finished"' in log


def test_denying_the_hand_off_sends_nothing(settings, vault_files):
    container = FakeVaultContainer(tidy)
    aura, _ = aura_with(settings, container, tool_turn(call("ask_developer", task=TASK)))
    run(aura.stream("Tidy my vault with Claude Code."))
    evs, _ = run(aura.resume([False]))
    assert "".join(e.text for e in evs if isinstance(e, Text)) == DENIAL
    assert container.runs == [] and (vault_files / "forms.md").exists()


def test_a_task_that_changes_nothing_or_is_stopped_asks_nothing_more(settings, vault_files):
    for container, expected in ((FakeVaultContainer(), "finished without changing any files"),
                                (FakeVaultContainer(tidy, stop=True), "Stopped before Claude Code finished"),
                                (FakeVaultContainer(error="Docker isn't running"), "couldn't run: Docker isn't running")):  # fmt: skip
        aura, model = aura_with(settings, container, tool_turn(call("ask_developer", task=TASK)))
        run(aura.stream("Go."))
        _, more = run(aura.resume([True]))
        assert more is None
        result = next(m for m in model.agent_calls()[-1] if isinstance(m, ToolMessage))
        assert expected in result.text
        assert (vault_files / "forms.md").exists() and not any(settings.dev_dir.glob("v-*"))


def test_the_chat_box_can_hand_a_message_to_a_developer(settings, vault_files):
    aura, model = aura_with(settings, FakeVaultContainer(), AIMessage("On it."))
    run(aura.stream("Tidy my notes.", developer="codex"))
    asked = [m for m in model.agent_calls()[0] if isinstance(m, HumanMessage)][-1].text
    assert asked.startswith("Tidy my notes.") and 'Call ask_developer now (engine "codex")' in asked
    with pytest.raises(ValueError):
        aura.stream("x", developer="gpt")


def test_one_job_at_a_time(settings, vault_files):
    aura, _ = aura_with(settings, FakeVaultContainer())
    d = aura.toolbox.developer
    with d._busy, pytest.raises(Exception, match="busy"):
        d.run_task("k", TASK, "claude", ".", aura.toolbox.vault)


# ── over HTTP: the developer choice, keep-alives, and Stop ──────────────────────────────────


def test_the_server_accepts_a_developer_and_keeps_a_long_run_alive(settings, vault_files, monkeypatch):
    import aura.server as srv

    monkeypatch.setattr(srv, "KEEPALIVE", 0.05)
    container = FakeVaultContainer(tidy, delay=0.4)
    aura, _ = aura_with(settings, container, tool_turn(call("ask_developer", task=TASK, engine="codex")))
    client = TestClient(create_app(aura, TOKEN, FakeKeeper()), base_url="http://127.0.0.1")
    assert client.post("/v1/chat", json={"text": "x", "developer": "gpt"}, headers=AUTH).status_code == 422
    r = client.post("/v1/chat", json={"text": "File my forms note.", "developer": "codex"}, headers=AUTH)
    assert "event: approval" in r.text
    r = client.post("/v1/approvals", json={"approvals": [True]}, headers=AUTH)
    assert ": working\n\n" in r.text  # the app's connection stays open while the engine works
    [approval] = [json.loads(b.split("data: ", 1)[1]) for b in r.text.split("\n\n") if b.startswith("event: approval")]
    assert {a["tool"] for a in approval["actions"]} == {"apply_change"}


def test_stop_ends_a_running_task(settings, vault_files):
    container = FakeVaultContainer(tidy, delay=0.5)
    aura, _ = aura_with(settings, container, tool_turn(call("ask_developer", task=TASK)))
    run(aura.stream("Go."))
    events = aura.resume([True])
    threading.Timer(0.1, aura.toolbox.developer.stop_tasks).start()
    _, more = run(events)
    assert more is None and (vault_files / "forms.md").exists()
    assert os.listdir(settings.dev_dir) == []


# ── live: the real engines, in the real Developer container, on a scratch vault ──────────────


def _live_task(settings, engine, secrets):
    """The real engine does a small job on a copy of a scratch vault; its one change comes back for
    approval, and only then reaches the vault."""
    from aura.developer.container import DevContainer
    from aura.developer.workflow import Developer
    from aura.governor.workshop import Workshop
    from aura.skills import SkillManager
    from aura.tools import vault_map
    from aura.tools.sandbox import Sandbox

    audit = AuditLog(settings.audit_log)
    vault = Vault(settings.vault_dir, audit)
    vault.ensure_exists()
    vault_map.seed(vault)
    sandbox = Sandbox("aura-sandbox:1", settings.vault_dir, 60, audit)
    workshop = Workshop(settings, SkillManager(settings.skills_dir, settings.data_dir, sandbox, audit), sandbox, audit,
                        run_suite=False)  # fmt: skip
    d = Developer(settings, workshop, DevContainer(sandbox, secrets, audit), secrets, audit, engines={engine.name: engine})
    lines: list[str] = []
    task = d.run_task("live", "Create the file 03_The_Witness_Relational/hello.md containing exactly one line: "
                      "'Hello from the Developer'. Change nothing else.", engine.name, ".", vault, lines.append)  # fmt: skip
    print("\n".join(lines[-8:]), f"\nsummary: {task.summary}\nerror: {task.error}")
    assert not task.error and not task.stopped, task.error
    assert [(c.path, c.kind) for c in task.changes] == [("03_The_Witness_Relational/hello.md", "create")]
    assert not (settings.vault_dir / "03_The_Witness_Relational" / "hello.md").exists()  # not before approval
    assert d.finish_task("live", vault, [True]) == ["Created '03_The_Witness_Relational/hello.md'"]
    assert (settings.vault_dir / "03_The_Witness_Relational" / "hello.md").read_text().strip() == "Hello from the Developer"
    assert not any(settings.dev_dir.glob("v-*"))


@pytest.mark.live
def test_live_codex_does_a_job_in_the_vault(settings):
    from pathlib import Path

    from test_developer import _docker_up

    from aura.developer.engines import Codex
    from aura.secrets import Secrets

    if not ((Path("~/.codex/auth.json").expanduser()).is_file() and _docker_up()):
        pytest.skip("Codex isn't signed in, or Docker isn't running")
    _live_task(settings, Codex(), Secrets())


@pytest.mark.live
def test_live_claude_code_does_a_job_in_the_vault_on_the_users_plan(settings):
    from test_developer import _claude_secrets

    from aura.developer.engines import ClaudeCode

    _live_task(settings, ClaudeCode(), _claude_secrets())
