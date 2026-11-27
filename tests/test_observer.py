"""Pillar 3, the Observer (docs/observer.md, approved by the user and Aura 2026-11-26): a live,
read-only view of Aura's own activity, from the audit log it already keeps."""

from __future__ import annotations

from conftest import make_aura

from aura.audit import AuditLog
from aura.masking import Masker

TOKEN_TEXT = "xoxb-" + "1234567890-abcdefghijkl"  # split: GitHub push protection


# ── T1: audit entries on the live feed ──────────────────────────────────────────────────────


def test_a_listener_gets_each_entry_masked_as_written(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl", Masker())
    heard = []
    audit.listen(heard.append)
    audit.record("search_fallback", query=f"is {TOKEN_TEXT} leaked")
    (written,) = audit.entries()
    assert heard == [written] and TOKEN_TEXT not in str(heard)


def test_a_failing_listener_never_stops_the_record(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")

    def broken(entry):
        raise RuntimeError("the window went away")

    audit.listen(broken)
    audit.record("skill_run", skill="pdf_text")
    assert [e["event"] for e in audit.entries()] == ["skill_run"]


def test_activity_reaches_the_live_feed_masked(settings):
    from test_command_stream import feed, server

    aura, _ = make_aura(settings)
    client = server(aura)
    aura.toolbox.audit.record("search_fallback", query=f"is {TOKEN_TEXT} leaked")
    (frame,) = [d for k, d in feed(client) if k == "activity"]
    assert frame["entry"]["event"] == "search_fallback" and frame["origin"] == "aura" and frame["turn_id"] is None
    assert TOKEN_TEXT not in str(frame)


# ── T2: the history endpoint ────────────────────────────────────────────────────────────────


def test_history_is_the_newest_entries_oldest_first_with_the_feed_number(settings):
    from test_command_stream import server
    from test_server import AUTH

    aura, _ = make_aura(settings)
    client = server(aura)
    for i in range(5):
        aura.toolbox.audit.record("skill_run", n=i)
    body = client.get("/v1/activity?limit=3", headers=AUTH).json()
    assert [e["n"] for e in body["entries"] if e["event"] == "skill_run"] == [2, 3, 4]
    assert body["latest"] == client.app.state.broadcast.latest


def test_history_needs_the_token_and_tolerates_a_damaged_line(settings):
    from test_command_stream import server
    from test_server import AUTH

    aura, _ = make_aura(settings)
    client = server(aura)
    assert client.get("/v1/activity").status_code == 401
    aura.toolbox.audit.record("skill_run", n=1)
    with aura.toolbox.audit.path.open("a") as f:
        f.write("{not json\n")
    aura.toolbox.audit.record("skill_run", n=2)
    entries = client.get("/v1/activity", headers=AUTH).json()["entries"]
    assert [e["n"] for e in entries if e["event"] == "skill_run"] == [1, 2]


# ── T3: the containers Aura starts ──────────────────────────────────────────────────────────


def _sandbox(tmp_path, monkeypatch, on_run):
    from test_sandbox import FakeDocker, make

    from aura.tools import sandbox as sb

    monkeypatch.setattr(sb.subprocess, "run", FakeDocker(on_run))
    s = make(tmp_path)
    return s, s.audit


def test_a_sandbox_run_records_its_limits_and_how_it_ended(tmp_path, monkeypatch):
    import subprocess

    s, audit = _sandbox(tmp_path, monkeypatch, lambda args, kw: subprocess.CompletedProcess(args, 0, "ok\n", ""))
    s.run("print('ok')")
    started, finished = [e for e in audit.entries() if e["event"].startswith("container_")]
    assert started["event"] == "container_started" and started["kind"] == "sandbox"
    assert (started["network"], started["memory"], started["cpus"], started["processes"]) == ("none", "512m", "1", "64")
    assert started["vault"] == "read-only"
    assert finished["event"] == "container_finished" and finished["container"] == started["container"]
    assert finished["exit_code"] == 0 and not finished["timed_out"]


def test_an_interrupted_sandbox_run_still_ends_in_the_log(tmp_path, monkeypatch):
    import pytest

    def crash(args, kw):
        raise KeyboardInterrupt

    s, audit = _sandbox(tmp_path, monkeypatch, crash)
    with pytest.raises(KeyboardInterrupt):
        s.run("x")
    assert [e["event"] for e in audit.entries() if e["event"].startswith("container_")] == [
        "container_started", "container_finished"]  # fmt: skip
    assert audit.entries()[-1]["interrupted"] is True


def test_the_limits_are_read_from_the_command_itself():
    from aura.tools.sandbox import container_limits

    assert container_limits(["docker", "run", "--network", "x", "--memory", "4g", "--cpus", "4", "img"]) == {
        "network": "x", "memory": "4g", "cpus": "4"}  # fmt: skip
    assert container_limits(["docker", "run", "img", "--network"]) == {}  # a flag with no value isn't a limit


def test_a_sandbox_run_with_docker_down_says_no_container_ran(tmp_path, monkeypatch):
    import subprocess

    import pytest

    from aura.tools.sandbox import SandboxUnavailable

    down = lambda args, kw: subprocess.CompletedProcess(args, 125, "", "Cannot connect to the Docker daemon")
    s, audit = _sandbox(tmp_path, monkeypatch, down)
    with pytest.raises(SandboxUnavailable):
        s.run("x")
    assert audit.entries()[-1]["event"] == "container_finished" and audit.entries()[-1]["never_started"] is True


def _developer(tmp_path, monkeypatch, run_engine, start_proxy=lambda docker, hosts: None):
    """Offline: Docker, the egress proxy and the engine itself are stood in for; the records are real."""
    import subprocess
    import threading

    from aura.developer import container as dc
    from aura.developer.container import DevContainer
    from aura.developer.engines import ClaudeCode
    from aura.secrets import Secrets
    from aura.tools.sandbox import Sandbox

    audit = AuditLog(tmp_path / "audit.jsonl")
    box = DevContainer(Sandbox("aura-sandbox:1", tmp_path / "vault", 30, audit), Secrets(), audit)
    monkeypatch.setattr(box, "ensure", lambda status: "docker")
    monkeypatch.setattr(box, "_start_proxy", start_proxy)
    monkeypatch.setattr(box, "_stop_proxy", lambda docker: [])
    monkeypatch.setattr(box, "_run_engine", run_engine)
    monkeypatch.setattr(dc.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    (tmp_path / "work").mkdir()
    run = lambda: box.run(ClaudeCode(), tmp_path / "work", tmp_path, "build it", 1.0, 60, lambda e: None,
                          threading.Event())  # fmt: skip
    return run, audit


def _containers(audit):
    return [e for e in audit.entries() if e["event"].startswith("container_")]


def test_a_developer_run_records_its_container(tmp_path, monkeypatch):
    from aura.developer import container as dc
    from aura.developer.container import RunOutcome

    run, audit = _developer(tmp_path, monkeypatch, lambda *a: RunOutcome(exit_code=0))
    run()
    started, finished = [e for e in audit.entries() if e["event"].startswith("container_")]
    assert started["kind"] == "developer" and started["engine"] == "claude"
    assert (started["memory"], started["cpus"], started["processes"]) == ("4g", "4", "512")
    assert started["network"] == dc.NETWORK and started["allowed_hosts"]  # only its vendor, through the allowlist
    assert finished["container"] == started["container"] and finished["exit_code"] == 0
    assert finished["interrupted"] is False


def test_a_developer_container_whose_proxy_never_started_is_not_recorded(tmp_path, monkeypatch):
    from aura.tools.sandbox import SandboxUnavailable

    def no_proxy(docker, hosts):
        raise SandboxUnavailable("the egress proxy didn't start")

    run, audit = _developer(tmp_path, monkeypatch, lambda *a: None, start_proxy=no_proxy)
    assert run().error == "the egress proxy didn't start"
    assert _containers(audit) == []


def test_a_developer_container_that_raises_still_ends_in_the_log(tmp_path, monkeypatch):
    import pytest

    def broken(*a):
        raise ValueError("unparseable engine output")

    run, audit = _developer(tmp_path, monkeypatch, broken)
    with pytest.raises(ValueError):
        run()
    assert [e["event"] for e in _containers(audit)] == ["container_started", "container_finished"]
    assert _containers(audit)[-1]["interrupted"] is True


def test_history_reads_only_the_end_of_a_long_log(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    for i in range(3000):
        audit.record("skill_run", n=i, note="x" * 40)
    newest = audit._tail(5, block=256)  # several small blocks, each cutting a line part-way
    assert [e["n"] for e in newest] == [2995, 2996, 2997, 2998, 2999]
    assert [e["n"] for e in audit.recent(2)] == [2998, 2999]
    assert len(audit.recent(5000)) == 3000


def test_the_history_and_the_feed_position_are_taken_together(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    seen = []
    audit.listen(seen.append)
    audit.record("skill_run")
    entries, mark = audit.snapshot(10, lambda: len(seen))
    assert len(entries) == mark == 1  # every entry in the history is already on the feed


# ── T4: egress grants ───────────────────────────────────────────────────────────────────────


def test_an_approved_search_records_its_grant_then_its_use(settings):
    from conftest import call, tool_turn
    from langchain_core.messages import AIMessage

    aura, _ = make_aura(settings, tool_turn(call("web_search", query="weather London")), AIMessage("Sunny."))
    list(aura.stream("Weather in London?"))
    list(aura.resume([True], approval_id=aura.approval_id))
    egress = [(e["event"], e.get("query")) for e in aura.toolbox.audit.entries() if e["event"].startswith("egress_")]
    assert egress == [("egress_granted", "weather London"), ("egress_used", "weather London")]


def test_a_refused_search_is_recorded_once_and_only_why(settings):
    from aura.tools.web import web_tools

    aura, _ = make_aura(settings)
    (search,) = [t for t in web_tools(aura.toolbox.search) if t.name == "web_search"]
    search.invoke({"name": "web_search", "args": {"query": "an unapproved query"}, "id": "call-1", "type": "tool_call"})
    refused = [e for e in aura.toolbox.audit.entries() if e["event"].startswith("egress_")]
    assert [e["event"] for e in refused] == ["egress_refused"]  # once: no grant was ever made
    assert "unapproved" not in str(refused)  # never shown on a card, so never logged


# ── after review: the server names each entry's kind ────────────────────────────────────────


def test_every_event_aura_records_has_a_kind():
    """The app shows the server's kind, so a new event can't drift into "other" unnoticed."""
    import re
    from pathlib import Path

    from aura.audit import category

    src = Path(__file__).resolve().parent.parent / "src" / "aura"
    names = {m for f in src.rglob("*.py") for m in re.findall(r'\.record\(\s*"([a-z_]+)"', f.read_text())}
    names |= {"skill_audited", "agent_audited"}  # workshop.py records these by variable
    assert len(names) > 80
    assert {n for n in names if category(n) == "other"} == set()
    assert category("agent_audit_failed") == "alerts" and category("hol_agent_vault_drift") == "alerts"


def test_the_feed_and_the_history_carry_the_kind_but_the_log_doesnt(settings):
    from test_command_stream import feed, server
    from test_server import AUTH

    aura, _ = make_aura(settings)
    client = server(aura)
    aura.toolbox.audit.record("container_started", kind="sandbox")
    (frame,) = [d for k, d in feed(client) if k == "activity"]
    assert frame["entry"]["category"] == "containers"
    (entry,) = [e for e in client.get("/v1/activity", headers=AUTH).json()["entries"] if e["event"] == "container_started"]
    assert entry["category"] == "containers" and "category" not in aura.toolbox.audit.entries()[-1]
