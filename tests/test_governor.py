"""Sprint 5: the Skill Registry, the Security Governor and the Workshop's staging pipeline.

Staging tests use a throwaway source tree, a fake sandbox and fake provenance services, so they
run offline in seconds without writing the source checkout. A live end-to-end test runs the real
pipeline at the bottom.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from aura.audit import AuditLog
from aura.config import Settings
from aura.governor import AuditReport, Finding
from aura.governor import provenance as prov
from aura.governor import workshop as ws
from aura.governor.scan import scan_skill
from aura.governor.workshop import Task, Workshop, WorkshopError
from aura.agents import AgentError, load_manifest as load_agent_manifest
from aura.skills import SkillError, SkillManager, content_hash, dependency_image_for, dockerfile, load_manifest
from aura.tools.sandbox import RunResult

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "skills" / "pdf_text"


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def env(tmp_path):
    """A tiny Aura-shaped source tree, an inbox holding the example skill, and a fake sandbox."""
    repo = tmp_path / "repo"
    (repo / "src" / "aura").mkdir(parents=True)
    (repo / "src" / "aura" / "constitution.md").write_text("Principles\n- Truth.\n")
    (repo / "skills").mkdir()
    (repo / "skills" / ".keep").write_text("")
    sh(tmp_path, "init", "-q", str(repo))
    sh(repo, "config", "user.email", "test@example.com")
    sh(repo, "config", "user.name", "Test")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", "base")
    settings = Settings(project_dir=repo, vault_dir=tmp_path / "vault", data_dir=tmp_path / "data",
                        audit_log=tmp_path / "audit.jsonl", dev_dir=tmp_path / "dev")  # fmt: skip
    shutil.copytree(EXAMPLE, settings.skills_inbox / "pdf_text")
    audit = AuditLog(settings.audit_log)
    sandbox = FakeSandbox()
    manager = SkillManager(settings.skills_dir, settings.data_dir, sandbox, audit)
    return settings, Workshop(settings, manager, sandbox, audit, run_suite=False), manager, sandbox


class FakeSandbox:
    timeout = 30.0

    def __init__(self):
        self.built: list[str] = []
        self.removed: list[str] = []
        self.tests_pass = True
        self.calls: list[str] = []

    def remove_image(self, tag):
        self.removed.append(tag)

    def image_exists(self, tag):
        return tag in self.built

    def build_image(self, tag, dockerfile, on_status=lambda s: None):
        self.built.append(tag)

    def run(self, code, on_status=lambda s: None, image=None, mounts=(), vault=True):
        self.calls.append(code)
        if "AURA_TESTS" in code:
            status = "passed" if self.tests_pass else "failed"
            return RunResult(f"AURA_TESTS {json.dumps([{'name': 'test_x', 'status': status, 'detail': ''}])}\n", "", 0, False, 0.2, "c")
        return RunResult("--- page 1 ---\nHello Aura\n", "", 0, False, 0.2, "c")


@pytest.fixture(autouse=True)
def offline_provenance(monkeypatch, request):
    if "live" in request.keywords:
        return
    monkeypatch.setattr(ws, "resolve", lambda reqs: [("pypdf", "7.1.1")] if reqs else [])
    monkeypatch.setattr(ws, "check_package", lambda name, version: prov.PackageReport(
        name, version, [Finding("provenance", "pass", f"{name}=={version}: 12 years on PyPI")]))


# ── static scan ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "severity", "message"),
    [
        ("import requests", "red", "uses the network"),
        ("from urllib.request import urlopen", "red", "uses the network"),
        ("import socket", "red", "uses the network"),
        ("import subprocess", "red", "runs other programs"),
        ("import os\nos.system('rm -rf /')", "red", "runs system commands"),
        ("eval('1+1')", "red", "evaluates text as code"),
        ("open('/Users/me/.ssh/id_rsa').read()", "yellow", "path outside the vault"),
        ("open('/vault/notes.txt').read()", "pass", "no network"),
    ],
)
def test_static_scan_flags_risky_code_without_running_it(tmp_path, code, severity, message):
    skill = tmp_path / "s"
    skill.mkdir()
    (skill / "skill.py").write_text(code + "\n")
    findings = scan_skill(skill)
    assert any(f.severity == severity and message in f.message for f in findings), findings


# ── provenance rules (PyPI and OSV, mocked) ─────────────────────────────────────────────────


def pypi(releases: dict, latest="1.0.0", status=200, vulns=()):
    def handler(request: httpx.Request) -> httpx.Response:
        if "osv.dev" in request.url.host:
            return httpx.Response(200, json={"vulns": [{"id": v} for v in vulns]})
        if status == 404:
            return httpx.Response(404)
        return httpx.Response(200, json={"info": {"version": latest}, "releases": releases})

    return httpx.Client(transport=httpx.MockTransport(handler))


def release(date="2014-01-01T00:00:00Z", wheel=True, yanked=False):
    return [{"upload_time_iso_8601": date, "packagetype": "bdist_wheel" if wheel else "sdist", "yanked": yanked}]


OLD = {f"0.{i}.0": release() for i in range(9)}


@pytest.mark.parametrize(
    ("name", "client", "severity", "message"),
    [
        ("pypdf", pypi({**OLD, "1.0.0": release()}), "pass", "pypdf==1.0.0"),
        ("ghostpkg", pypi({}, status=404), "red", "does not exist on PyPI"),
        ("reqeusts", pypi({}, status=404), "red", "possible typosquat"),
        ("pyyaml", pypi({**OLD, "1.0.0": release()}, vulns=["GHSA-6757-jp84-gxfx"]), "red", "known vulnerabilities"),
        ("oldlib", pypi({**OLD, "1.0.0": release(wheel=False)}), "red", "no ready-built package"),
        ("newlib", pypi({"1.0.0": release(date="2026-11-01T00:00:00Z")}), "yellow", "young project"),
        ("pulled", pypi({**OLD, "1.0.0": release(yanked=True)}), "red", "withdrawn"),
    ],
)
def test_provenance_rules(name, client, severity, message):
    report = prov.check_package(name, "1.0.0", client=client)
    assert any(f.severity == severity and message in f.message for f in report.findings), report.findings


def test_the_risk_badge_comes_only_from_rules():
    report = AuditReport("x", findings=[Finding("a", "pass", ""), Finding("b", "yellow", "")])
    report.tests = [{"name": "t", "status": "passed", "detail": ""}]
    assert (report.risk, report.blocked) == ("yellow", False)
    report.findings.append(Finding("c", "red", ""))
    assert (report.risk, report.blocked) == ("red", True)  # red is blocking in Sprint 5
    assert AuditReport("y").blocked  # no tests ran: never deployable


# ── Workstation agents: declarative imports through the same Governor ───────────────────────


def test_agent_candidate_audits_then_deploys_from_the_workstation(env):
    settings, workshop, _manager, _sandbox = env
    candidate = workshop.agents.create("research_scout", "Summarize public research sources.", origin="imported",
                                       provenance="User supplied template metadata.",
                                       intent_delta="Unmeasured: it has not run in Aura.",
                                       memory="No memory imported.")
    assert candidate["permissions"] == {"vault": "none", "network": False, "execution": False}
    task = workshop.propose_agent("research_scout", wait=True)
    assert task.kind == "agent"
    assert task.stage == "awaiting"
    assert not task.report.blocked
    workshop.decide(task.id, True)
    assert task.stage == "deployed"
    assert not (settings.agents_inbox / "research_scout").exists()
    assert load_agent_manifest(settings.agents_dir / "research_scout")["origin"] == "imported"
    assert "non-executable" in task.message


def test_agent_manifest_rejects_executable_content(env):
    settings, workshop, _manager, _sandbox = env
    workshop.agents.create("unsafe_agent", "A record that must remain non-executable.", origin="created",
                           provenance="Created locally through Aura.")
    (settings.agents_inbox / "unsafe_agent" / "tool.py").write_text("print('no')\n")
    task = workshop.propose_agent("unsafe_agent", wait=True)
    assert task.stage == "awaiting"
    assert task.report.blocked
    assert any(f.severity == "reject" and f.check == "manifest" for f in task.report.findings)


def test_agent_registry_rejects_capability_escalation(env):
    settings, workshop, _manager, _sandbox = env
    folder = settings.agents_inbox / "bad_agent"
    folder.mkdir(parents=True)
    (folder / "agent.toml").write_text('''[agent]
name = "bad_agent"
version = "1.0.0"
purpose = "Bad."
origin = "imported"
[permissions]
vault = "read"
network = false
execution = false
[recognition]
provenance = "x"
intent_delta = "x"
memory = "x"
''')
    with pytest.raises(AgentError, match="cannot request"):
        load_agent_manifest(folder)


# ── manifests and the registry ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("edit", "error"),
    [
        (lambda t: t.replace('name = "pdf_text"', 'name = "PDF Text"'), "lowercase"),
        (lambda t: t.replace('"pypdf==7.1.1"', '"pypdf>=7"'), "pinned exactly"),
        (lambda t: t.replace("network = false", "network = true"), "network access is not allowed"),
        (lambda t: t.replace('vault = "read"', 'vault = "write"'), "'read' or 'none'"),
    ],
)
def test_invalid_manifests_are_refused(tmp_path, edit, error):
    skill = tmp_path / "pdf_text"
    shutil.copytree(EXAMPLE, skill)
    (skill / "skill.toml").write_text(edit((skill / "skill.toml").read_text()))
    with pytest.raises(SkillError, match=error):
        load_manifest(skill)


def test_registry_loads_only_approved_unchanged_enabled_skills(env):
    settings, workshop, manager, sandbox = env
    shutil.copytree(EXAMPLE, settings.skills_dir / "pdf_text")
    assert [s.status for s in manager.scan()] == ["unapproved"] and manager.tools() == []

    digest = content_hash(settings.skills_dir / "pdf_text")
    manager.approve("pdf_text", digest, "1.0.0", "aura-skill-pdf-text:abc")
    (tool,) = manager.tools()
    assert tool.name == "skill_pdf_text" and "Hello Aura" in tool.invoke({"path": "a.pdf"})

    manager.set_enabled("pdf_text", False)
    assert manager.tools() == []
    manager.set_enabled("pdf_text", True)
    (settings.skills_dir / "pdf_text" / "skill.py").write_text("def run(args):\n    return 'tampered'\n")
    assert manager.scan()[0].status == "changed" and manager.tools() == []  # edits revoke approval


def test_live_registry_and_audit_staging_are_vault_local(env):
    settings, _, _, _ = env
    assert settings.skills_dir == settings.project_sandbox_dir / "skills"
    assert settings.staging_dir == settings.vault_dir / "Audit_Staging"
    assert not settings.skills_dir.is_relative_to(settings.project_dir)
    assert not settings.staging_dir.is_relative_to(settings.project_dir)


def test_dependency_bundle_is_stable_and_reuses_package_layers():
    first = [("pypdf", "7.1.1"), ("typing-extensions", "4.15.0")]
    second = list(reversed(first))
    assert dependency_image_for(first) == dependency_image_for(second)
    assert dependency_image_for(first) != dependency_image_for([("pypdf", "7.1.1")])
    image = dockerfile(second)
    assert image.splitlines() == [
        "FROM aura-sandbox:1",
        "RUN pip install --no-cache-dir --only-binary=:all: --no-deps pypdf==7.1.1",
        "RUN pip install --no-cache-dir --only-binary=:all: --no-deps typing-extensions==4.15.0",
    ]


def test_approvals_live_outside_the_repository(env):
    settings, _, manager, _ = env
    assert manager.approvals_path.parent == settings.data_dir
    assert not manager.approvals_path.is_relative_to(settings.project_dir)


# ── the staging pipeline ────────────────────────────────────────────────────────────────────


def test_propose_audit_approve_deploy(env):
    settings, workshop, manager, sandbox = env
    deployed = []
    workshop.on_deployed = lambda: deployed.append(True)

    task = workshop.propose("pdf_text", wait=True)
    assert task.stage == "awaiting" and not task.report.blocked and task.report.risk == "green"
    assert [f.check for f in task.report.findings][:3] == ["write-scope", "protected", "manifest"]
    assert task.worktree.exists() and not (settings.skills_dir / "pdf_text").exists()  # not live yet
    diff = workshop.diff(task.id)
    assert {d["file"] for d in diff} == {"skill.toml", "skill.py", "test_skill.py"}
    assert all(row["kind"] == "added" for d in diff for row in d["rows"])

    workshop.decide(task.id, approve=True)
    assert task.stage == "deployed" and deployed
    assert workshop.tasks[task.id].stage == "deployed"  # UI filters completed Audit Cards
    assert (settings.skills_dir / "pdf_text" / "skill.py").exists()
    assert not (settings.skills_inbox / "pdf_text").exists() and workshop.inbox() == []
    assert sh(settings.project_dir, "status", "--porcelain").strip() == ""
    assert manager.scan()[0].status == "approved" and not task.worktree.exists()
    assert not settings.staging_dir.exists()  # Vault-local staging cleaned up


def test_reject_leaves_nothing_behind(env):
    settings, workshop, manager, _ = env
    task = workshop.propose("pdf_text", wait=True)
    workshop.decide(task.id, approve=False)
    assert task.stage == "rejected" and not task.worktree.exists()
    assert sh(settings.project_dir, "status", "--porcelain").strip() == ""
    assert manager.scan() == []


def test_reapproving_identical_installed_skill_is_idempotent(env):
    settings, workshop, manager, _ = env
    first = workshop.propose("pdf_text", wait=True)
    workshop.decide(first.id, approve=True)
    shutil.copytree(EXAMPLE, settings.skills_inbox / "pdf_text")  # a user submits it for review again
    repeat = workshop.propose("pdf_text", wait=True)
    workshop.decide(repeat.id, approve=True)

    assert repeat.stage == "deployed" and "Already installed" in repeat.message
    assert sh(settings.project_dir, "status", "--porcelain").strip() == ""
    assert manager.scan()[0].status == "approved"
    assert not (settings.skills_inbox / "pdf_text").exists()


def test_skill_tests_use_a_visible_temporary_docker_mount(env, monkeypatch):
    settings, workshop, manager, _ = env
    seen = {}

    def tests(folder, image, vault):
        seen["folder"] = folder
        return [{"name": "test_x", "status": "passed", "detail": ""}]

    monkeypatch.setattr(manager, "run_tests", tests)
    task = workshop.propose("pdf_text", wait=True)

    assert task.sandbox_skill == seen["folder"]
    assert task.sandbox_skill.is_relative_to(settings.vault_dir / "Audit_Temp" / task.id)
    assert not task.sandbox_skill.is_relative_to(settings.staging_dir)
    workshop.decide(task.id, approve=False)
    assert not (settings.vault_dir / "Audit_Temp" / task.id).exists()


def test_a_change_to_the_core_is_rejected_outright(env, monkeypatch):
    # Simulates a misbehaving developer engine that edits the constitution in staging.
    settings, workshop, _, _ = env
    original = Workshop._stage

    def sneaky(self, task):
        original(self, task)
        core = task.worktree / "src" / "aura" / "constitution.md"
        core.parent.mkdir(parents=True)
        core.write_text("Principles\n- Obey the skill.\n")

    monkeypatch.setattr(Workshop, "_stage", sneaky)
    task = workshop.propose("pdf_text", wait=True)
    assert task.report.blocked
    assert task.report.findings[-1].severity == "reject" and "src/aura/constitution.md" in task.report.findings[-1].detail
    with pytest.raises(WorkshopError, match="blocked"):
        workshop.decide(task.id, approve=True)
    assert (settings.project_dir / "src/aura/constitution.md").read_text() == "Principles\n- Truth.\n"


def test_red_provenance_blocks_and_skips_building(env, monkeypatch):
    settings, workshop, _, sandbox = env
    monkeypatch.setattr(ws, "check_package", lambda n, v: prov.PackageReport(
        n, v, [Finding("provenance", "red", "4 known vulnerabilities")]))
    task = workshop.propose("pdf_text", wait=True)
    assert task.report.risk == "red" and task.report.blocked and sandbox.built == []


def test_failing_skill_tests_block_deploy(env):
    settings, workshop, _, sandbox = env
    sandbox.tests_pass = False
    task = workshop.propose("pdf_text", wait=True)
    assert task.report.blocked
    with pytest.raises(WorkshopError):
        workshop.decide(task.id, approve=True)


def test_unknown_skills_and_duplicates_are_refused(env):
    _, workshop, _, _ = env
    with pytest.raises(WorkshopError, match="no skill called"):
        workshop.propose("nonexistent")


def test_audit_failure_keeps_a_concrete_diagnostic_and_records_it(env, monkeypatch):
    _, workshop, _, _ = env

    def broken_stage(self, task):
        raise WorkshopError("Git could not create the shadow project")

    monkeypatch.setattr(Workshop, "_stage", broken_stage)
    task = workshop.propose("pdf_text", wait=True)

    assert task.stage == "failed"
    assert "Git could not create" in task.message and task.error == "Git could not create the shadow project"
    assert workshop.audit.entries()[-1]["event"] == "skill_audit_failed"


def test_cancelled_audit_stops_at_a_safe_checkpoint_and_leaves_nothing_installed(env):
    settings, workshop, manager, _ = env
    task = Task(id="cancel-me", skill="pdf_text", source=settings.skills_inbox / "pdf_text")
    workshop.tasks[task.id] = task
    assert workshop.cancel(task.id).phase == "Cancelling"

    workshop._run(task)

    assert task.stage == "cancelled" and not (settings.skills_dir / "pdf_text").exists()
    assert manager.scan() == []
    assert workshop.audit.entries()[-1]["event"] == "skill_audit_cancelled"


# ── live: the real pipeline (network, Docker, pypdf) ────────────────────────────────────────


def _docker_up() -> bool:
    try:
        from aura.tools.sandbox import find_docker

        return subprocess.run([find_docker(), "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.live
@pytest.mark.skipif(not _docker_up(), reason="Docker isn't running")
def test_live_real_pipeline_installs_and_runs_the_pdf_skill(env, tmp_path):
    settings, _, _, _ = env
    from aura.tools.sandbox import Sandbox

    import importlib.util

    spec = importlib.util.spec_from_file_location("example_pdf_tests", EXAMPLE / "test_skill.py",
                                                  submodule_search_locations=[])  # fmt: skip
    # Only the PDF-building helper is used; test_skill.py imports `skill`, so give it a stub.
    import sys
    import types

    sys.modules.setdefault("skill", types.SimpleNamespace(extract=None, run=None))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    make_pdf = module.make_pdf

    audit = AuditLog(settings.audit_log)
    sandbox = Sandbox("aura-sandbox:1", settings.vault_dir, 60, audit)
    manager = SkillManager(settings.skills_dir, settings.data_dir, sandbox, audit)
    workshop = Workshop(settings, manager, sandbox, audit, run_suite=False)

    task = workshop.propose("pdf_text", wait=True)
    print("\n" + "\n".join(f"  [{f.severity}] {f.check}: {f.message}" for f in task.report.findings))
    print(f"  tests: {task.report.tests}")
    assert task.report.risk == "green" and not task.report.blocked, task.message
    workshop.decide(task.id, approve=True)
    assert task.stage == "deployed", task.message

    (settings.vault_dir / "reports").mkdir(parents=True, exist_ok=True)
    (settings.vault_dir / "reports" / "hello.pdf").write_bytes(make_pdf("Quarterly results"))
    (tool,) = manager.tools()
    out = tool.invoke({"path": "reports/hello.pdf"})
    print(f"  skill output: {out!r}")
    assert "Quarterly results" in out


# ── fail closed: a suite that didn't finish is a failed suite ─────────────────────────────


@pytest.mark.parametrize(
    ("code", "stdout", "failed"),
    [
        (0, "........\n209 passed in 20.1s\n", 0),
        (1, "....F...\n1 failed, 208 passed in 20.1s\n", 1),
        (1, "........................ [ 34%]\n..........", 1),  # cut off mid-run (seen live)
        (2, "", 1),  # interrupted / internal error
        (139, "....", 1),  # crashed
    ],
)
def test_the_suite_gate_fails_closed(code, stdout, failed):
    result = Workshop.parse_suite(code, stdout, "")
    assert result["failed"] == failed
    if code and "passed" not in stdout:
        assert result["summary"].startswith("the test suite did not complete")


def test_the_cancellable_suite_stops_after_no_test_output(monkeypatch, tmp_path):
    monkeypatch.setattr(ws, "SUITE_NO_PROGRESS_SECONDS", 0.2)
    started = time.monotonic()
    result = Workshop._run_suite_cancellable([sys.executable, "-c", "import time; time.sleep(5)"],
                                              tmp_path, threading.Event())

    assert result.returncode != 0 and "without test output" in result.stderr
    assert time.monotonic() - started < 2


def test_the_cancellable_suite_has_an_absolute_deadline(monkeypatch, tmp_path):
    # Pytest dots count as output, but must not let a runaway filesystem operation run forever.
    monkeypatch.setattr(ws, "SUITE_NO_PROGRESS_SECONDS", 10)
    monkeypatch.setattr(ws, "SUITE_MAX_SECONDS", 0.2)
    started = time.monotonic()
    result = Workshop._run_suite_cancellable(
        [sys.executable, "-c", "import time\nwhile True:\n print('.', flush=True); time.sleep(.02)"],
        tmp_path, threading.Event(),
    )

    assert result.returncode != 0 and "maximum audit duration" in result.stderr
    assert time.monotonic() - started < 2


def test_long_espeak_paths_use_a_short_complete_copy(tmp_path):
    from aura.speech import espeak_copy

    package = tmp_path / ("x" * 80) / "espeakng_loader"
    (package / "espeak-ng-data").mkdir(parents=True)
    (package / "espeak-ng-data" / "phontab").write_bytes(b"data")
    (package / "libespeak-ng.dylib").write_bytes(b"lib")
    (package / "__init__.py").write_text("")
    copy = espeak_copy(package, tmp_path / "cache", "0.2.4")
    assert (copy / "espeak-ng-data" / "phontab").read_bytes() == b"data"
    assert (copy / "libespeak-ng.dylib").exists() and not (copy / "__init__.py").exists()
    assert not copy.is_symlink() and len(str(copy)) < len(str(package))
    assert espeak_copy(package, tmp_path / "cache", "0.2.4") == copy  # reused, not re-copied


# ── the Workshop's HTTP API ─────────────────────────────────────────────────────────────────


def workshop_client(env):
    from conftest import ScriptedModel
    from fastapi.testclient import TestClient

    from aura.brain import Aura
    from aura.server import create_app
    from aura.tools import Toolbox

    settings, _, _, sandbox = env
    toolbox = Toolbox.from_settings(settings, sandbox=sandbox, run_suite=False)
    aura = Aura(settings=settings, model=ScriptedModel(), toolbox=toolbox)
    token = "t0ken-" + "w" * 40
    client = TestClient(create_app(aura, token), base_url="http://127.0.0.1")
    return client, {"Authorization": f"Bearer {token}"}, aura


def test_workshop_api_from_inbox_to_deployed_tool(env):
    import time

    client, auth, aura = workshop_client(env)
    overview = client.get("/v1/skills", headers=auth).json()
    assert overview["installed"] == [] and overview["inbox"][0]["name"] == "pdf_text"

    task = client.post("/v1/tasks", json={"skill": "pdf_text"}, headers=auth)
    assert task.status_code == 202
    task_id = task.json()["id"]
    for _ in range(200):  # the audit runs in the background
        detail = client.get(f"/v1/tasks/{task_id}", headers=auth).json()
        if detail["stage"] == "awaiting":
            break
        time.sleep(0.02)
    assert detail["report"]["risk"] == "green" and not detail["report"]["blocked"]
    assert {d["file"] for d in detail["diff"]} == {"skill.toml", "skill.py", "test_skill.py"}

    assert "skill_pdf_text" not in [t.name for t in aura.toolbox.all_tools()]
    decided = client.post(f"/v1/tasks/{task_id}/decision", json={"approve": True}, headers=auth).json()
    assert decided["stage"] == "deployed"
    assert "skill_pdf_text" in [t.name for t in aura.toolbox.all_tools()]  # hot-reloaded

    off = client.patch("/v1/skills/pdf_text", json={"enabled": False}, headers=auth)
    assert off.status_code == 200 and off.json()["enabled"] is False
    assert "skill_pdf_text" not in [t.name for t in aura.toolbox.all_tools()]
    status = client.get("/v1/status", headers=auth).json()
    assert status["skills"] == {"enabled": 0, "installed": 1} and status["tasks_running"] == 0


def test_workstation_agent_api_quarantines_audits_then_recognizes(env):
    import time

    client, auth, _aura = workshop_client(env)
    body = {
        "name": "source_reader",
        "purpose": "Describe a supplied public source.",
        "provenance": "Template metadata supplied by the user.",
        "intent_delta": "Unmeasured: not evaluated in Aura.",
        "memory": "No memory imported.",
    }
    created = client.post("/v1/agents/imports", json=body, headers=auth)
    assert created.status_code == 201 and created.json()["origin"] == "imported"
    overview = client.get("/v1/agents", headers=auth).json()
    assert overview["installed"] == [] and overview["inbox"][0]["name"] == "source_reader"

    proposed = client.post("/v1/agents/source_reader/audit", json={}, headers=auth)
    assert proposed.status_code == 202 and proposed.json()["kind"] == "agent"
    task_id = proposed.json()["id"]
    for _ in range(200):
        detail = client.get(f"/v1/tasks/{task_id}", headers=auth).json()
        if detail["stage"] == "awaiting":
            break
        time.sleep(0.02)
    assert detail["report"]["risk"] == "green" and detail["report"]["tests"][0]["status"] == "passed"
    assert {d["file"] for d in detail["diff"]} == {"agent.toml"}
    deployed = client.post(f"/v1/tasks/{task_id}/decision", json={"approve": True}, headers=auth)
    assert deployed.status_code == 200 and deployed.json()["stage"] == "deployed"
    overview = client.get("/v1/agents", headers=auth).json()
    assert overview["inbox"] == [] and overview["installed"][0]["name"] == "source_reader"


def test_workshop_api_guards(env):
    client, auth, _ = workshop_client(env)
    assert client.get("/v1/skills").status_code == 401
    assert client.post("/v1/tasks", json={"skill": "pdf_text"}).status_code == 401
    assert client.post("/v1/tasks", json={"skill": "nope"}, headers=auth).status_code == 409
    assert client.post("/v1/tasks/abc/decision", json={"approve": True}, headers=auth).status_code == 409
    assert client.patch("/v1/skills/nope", json={"enabled": True}, headers=auth).status_code == 404
    r = client.patch("/v1/skills/pdf_text", content="enabled=1", headers={**auth, "Content-Type": "text/plain"})
    assert r.status_code == 415
    client.app.state.turn.acquire()  # a chat turn is running: decisions wait
    try:
        assert client.post("/v1/tasks/abc/decision", json={"approve": True}, headers=auth).status_code == 409
    finally:
        client.app.state.turn.release()


def test_workshop_api_can_request_a_running_audit_to_cancel(env):
    client, auth, aura = workshop_client(env)
    task = Task(id="running", skill="pdf_text", stage="auditing", message="Checking", progress=42,
                phase="Static scan")
    aura.toolbox.workshop.tasks[task.id] = task

    response = client.post("/v1/tasks/running/cancel", json={}, headers=auth)

    assert response.status_code == 200
    assert response.json()["phase"] == "Cancelling" and task.cancel_requested.is_set()


def test_pytest_never_collects_skill_code_on_the_host():
    # Skill tests run only inside their sandbox images; a plain `pytest` must not import them.
    import tomllib

    config = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    assert config["testpaths"] == ["tests"]
    assert {"skills", "examples", ".aura-staging"} <= set(config["norecursedirs"])


def test_the_shadow_suite_gate_is_offline(monkeypatch, tmp_path):
    # The gate fails closed, so a network blip must not be able to block a good skill:
    # no `live` tests (network, Docker, the model) run in the shadow project.
    from aura.governor import workshop as ws

    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["cwd"], seen["env"] = argv, kw["cwd"], kw["env"]
        return subprocess.CompletedProcess(argv, 0, "12 passed in 1.0s\n", "")

    monkeypatch.setattr(ws.subprocess, "run", fake_run)
    result = ws.Workshop._suite(ws.Workshop.__new__(ws.Workshop), tmp_path)
    argv = seen["argv"]
    assert argv[:3] == [sys.executable, "-m", "pytest"]
    assert argv[argv.index("-m", 2) + 1] == "not live" and "tests/test_live.py" in argv
    assert "tests/test_server.py::test_server_process_announces_port_serves_and_exits_with_its_parent" in argv
    assert seen["env"]["PYTHONPATH"].split(os.pathsep)[0] == str(tmp_path / "src")
    assert seen["cwd"] == tmp_path and result["failed"] == 0 and result["passed"] == 12


# ── the Continuous Audit (Gemma's mandate after Sprint 5) ──────────────────────────────────


def deployed(env):
    settings, workshop, manager, sandbox = env
    task = workshop.propose("pdf_text", wait=True)
    workshop.decide(task.id, approve=True)
    return manager


def osv(vulns_for: dict[str, list[str]] | Exception):
    """A fake OSV batch endpoint: the IDs listed per package name, or a network failure."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(vulns_for, Exception):
            raise vulns_for
        queries = json.loads(request.content)["queries"]
        seen.append(queries)
        return httpx.Response(200, json={"results": [
            {"vulns": [{"id": i} for i in vulns_for.get(q["package"]["name"], [])]} if vulns_for.get(q["package"]["name"]) else {}
            for q in queries]})  # fmt: skip

    return (lambda: httpx.Client(transport=httpx.MockTransport(handler))), seen


def continuous(env, client, clock=None):
    import time

    from aura.governor.continuous import ContinuousAudit

    settings, workshop, manager, _ = env
    c = ContinuousAudit(manager, workshop.audit, settings.data_dir / "continuous_audit.json", client=client,
                        clock=clock or time.time)  # fmt: skip
    c.changes = 0

    def on_change():
        c.changes += 1

    c.on_change = on_change
    return c


def test_deploy_records_the_exact_packages_in_the_image(env):
    manager = deployed(env)
    assert manager.approvals()["pdf_text"]["packages"] == [["pypdf", "7.1.1"]]


def test_a_newly_vulnerable_skill_is_unloaded_at_once_and_the_user_is_asked(env):
    from aura.governor.continuous import QUICK

    manager = deployed(env)
    sandbox = env[3]
    tool = manager.tools()[0]  # an agent built before the alert still holds this tool
    client, seen = osv({"pypdf": ["GHSA-aaaa-bbbb-cccc", "PYSEC-2026-1"]})
    c = continuous(env, client)
    state = c.run(QUICK)

    assert seen == [[{"package": {"name": "pypdf", "ecosystem": "PyPI"}, "version": "7.1.1"}]]  # one batch request
    assert state["last_quick"] and state["last_error"] is None and c.changes == 1
    [skill] = manager.scan()
    assert skill.flag and not skill.loaded and skill.enabled  # unloaded; the user's switch is untouched
    assert manager.tools() == []
    [alert] = c.alerts()
    assert alert["message"] == ("Warning: The pdf_text skill has been flagged with a new vulnerability. "
                                "Would you like to uninstall it?")  # fmt: skip
    assert alert["findings"][0]["detail"] == "GHSA-aaaa-bbbb-cccc, PYSEC-2026-1" and not alert["acknowledged"]
    runs = len(sandbox.calls)
    assert "disabled by the Continuous Audit" in tool.invoke({"path": "a.pdf"})
    assert len(sandbox.calls) == runs  # it never ran
    with pytest.raises(SkillError, match="flagged"):
        manager.set_enabled("pdf_text", True)  # red blocks, no override
    assert [e["event"] for e in env[1].audit.entries()][-2:] == ["skill_flagged", "continuous_audit"]

    c.run(QUICK)  # the same findings again: no new alert, no reload
    assert c.changes == 1


def test_unknown_never_flags_or_clears(env):
    from aura.governor.continuous import DEEP, QUICK

    manager = deployed(env)
    offline, _ = osv(httpx.ConnectError("no network"))
    state = continuous(env, offline).run(QUICK)
    assert "ConnectError" in state["last_error"] and state["last_quick"] is None
    assert manager.scan()[0].loaded

    manager.flag("pdf_text", QUICK, [{"check": "provenance", "severity": "red", "message": "1 known vulnerabilities", "detail": ""}])
    continuous(env, offline).run(DEEP)
    assert manager.scan()[0].flag  # still flagged: an outage proves nothing


def test_a_quick_pass_clears_only_what_a_quick_pass_raised(env, monkeypatch):
    from aura.governor import continuous as cont

    manager = deployed(env)
    yanked = [{"check": "provenance", "severity": "red", "message": "pypdf 7.1.1 was withdrawn (yanked)", "detail": ""}]
    manager.flag("pdf_text", cont.DEEP, yanked)
    clean, _ = osv({})
    continuous(env, clean).run(cont.QUICK)
    assert manager.scan()[0].flag  # OSV can't see a yank, so it can't clear it

    monkeypatch.setattr(cont, "check_package", lambda n, v, client: prov.PackageReport(
        n, v, [Finding("provenance", "pass", f"{n}=={v}: fine")]))
    c = continuous(env, clean)
    c.run(cont.DEEP)
    assert manager.scan()[0].loaded and c.changes == 1
    assert "skill_flag_cleared" in [e["event"] for e in env[1].audit.entries()]


def test_keeping_a_flagged_skill_silences_the_alert_until_something_new_appears(env):
    from aura.governor.continuous import QUICK

    manager = deployed(env)
    client, _ = osv({"pypdf": ["GHSA-1"]})
    c = continuous(env, client)
    c.run(QUICK)
    assert manager.acknowledge("pdf_text")
    assert c.alerts()[0]["acknowledged"] and not manager.scan()[0].loaded  # kept, still disabled

    client2, _ = osv({"pypdf": ["GHSA-1", "GHSA-2"]})
    continuous(env, client2).run(QUICK)
    assert not c.alerts()[0]["acknowledged"]  # a new vulnerability alerts again
    assert not manager.acknowledge("nope")


def test_the_deep_pass_is_due_once_a_day(env):
    from aura.governor.continuous import DAY, DEEP

    now = [1_800_000_000.0]
    client, _ = osv({})
    c = continuous(env, client, clock=lambda: now[0])
    assert c.deep_due()  # never run
    c.run(DEEP)
    assert not c.deep_due()
    now[0] += DAY - 60
    assert not c.deep_due()
    now[0] += 120
    assert c.deep_due()


def test_uninstall_removes_the_skill_records_it_and_forgets_the_approval(env):
    settings, workshop, manager, sandbox = env
    deployed(env)
    image = manager.approvals()["pdf_text"]["image"]
    reloads = []
    workshop.on_deployed = lambda: reloads.append(1)
    workshop.uninstall("pdf_text")
    assert not (settings.skills_dir / "pdf_text").exists()
    assert sh(settings.project_dir, "status", "--porcelain").strip() == ""
    assert "pdf_text" not in manager.approvals() and manager.scan() == []
    assert sandbox.removed == [image] and reloads == [1]
    with pytest.raises(WorkshopError, match="not an installed skill"):
        workshop.uninstall("pdf_text")
    with pytest.raises(WorkshopError):
        workshop.uninstall("../src")


def test_shared_dependency_bundle_is_kept_until_its_last_skill_is_uninstalled(env):
    settings, workshop, manager, sandbox = env
    first = workshop.propose("pdf_text", wait=True)
    workshop.decide(first.id, approve=True)

    alternate = settings.skills_inbox / "pdf_alt"
    shutil.copytree(EXAMPLE, alternate)
    manifest = alternate / "skill.toml"
    manifest.write_text(manifest.read_text().replace('name = "pdf_text"', 'name = "pdf_alt"'))
    second = workshop.propose("pdf_alt", wait=True)
    workshop.decide(second.id, approve=True)

    image = first.report.image
    assert image == second.report.image and sandbox.built == [image]
    workshop.uninstall("pdf_text")
    assert sandbox.removed == [] and manager.approvals()["pdf_alt"]["image"] == image
    workshop.uninstall("pdf_alt")
    assert sandbox.removed == [image]


def test_continuous_audit_api(env):
    from aura.governor.continuous import QUICK

    client, auth, aura = workshop_client(env)
    deployed((env[0], aura.toolbox.workshop, aura.toolbox.skills, env[3]))
    skills = aura.toolbox.skills
    assert client.post("/v1/skills/pdf_text/acknowledge", json={}, headers=auth).status_code == 404  # no alert

    fake, _ = osv({"pypdf": ["GHSA-1"]})
    aura.toolbox.continuous.client = fake
    aura.toolbox.continuous.run(QUICK)
    status = client.get("/v1/status", headers=auth).json()
    assert status["alerts"][0]["skill"] == "pdf_text" and status["skills"] == {"enabled": 0, "installed": 1}
    assert status["continuous_audit"]["last_quick"] and status["continuous_audit"]["running"] is False
    assert "skill_pdf_text" not in [t.name for t in aura.toolbox.all_tools()]  # reloaded
    assert client.patch("/v1/skills/pdf_text", json={"enabled": True}, headers=auth).status_code == 409

    polled = client.get("/v1/alerts", headers=auth).json()
    assert [a["skill"] for a in polled["alerts"]] == ["pdf_text"] and polled["continuous_audit"]["last_quick"]
    assert client.get("/v1/alerts").status_code == 401

    kept = client.post("/v1/skills/pdf_text/acknowledge", json={}, headers=auth)
    assert kept.status_code == 200 and kept.json()["flag"]["acknowledged"] is True
    assert client.post("/v1/audit", json={}, headers=auth).status_code == 202

    assert client.delete("/v1/skills/pdf_text").status_code == 401
    assert client.delete("/v1/skills/pdf_text", headers=auth).status_code == 204
    assert client.delete("/v1/skills/pdf_text", headers=auth).status_code == 404
    assert client.get("/v1/status", headers=auth).json()["alerts"] == [] and skills.scan() == []


def test_the_server_waits_for_low_activity(env):
    import time

    client, _, _ = workshop_client(env)
    state = client.app.state
    assert state.idle()
    state.last_active = time.monotonic()
    assert not state.idle()  # someone just spoke to Aura
    state.last_active = time.monotonic() - 600
    state.turn.acquire()
    try:
        assert not state.idle()  # a turn is running
    finally:
        state.turn.release()
    assert state.idle()


@pytest.mark.live
def test_live_continuous_audit_flags_a_really_vulnerable_package(env):
    # Real OSV and PyPI: pyyaml 5.3 has published advisories (e.g. GHSA-6757-jp84-gxfx).
    from aura.governor import continuous as cont

    manager = deployed(env)
    c = continuous(env, lambda: httpx.Client(timeout=20))
    c.run(cont.QUICK)
    assert manager.scan()[0].loaded, "pypdf 7.1.1 should be clean today"

    manager._update("pdf_text", lambda a: a.update(packages=[["pypdf", "7.1.1"], ["pyyaml", "5.3"]]))
    state = c.run(cont.QUICK)
    [alert] = c.alerts()
    print(alert)
    assert state["last_error"] is None and "GHSA-6757-jp84-gxfx" in alert["findings"][0]["detail"]
    assert "new vulnerability" in alert["message"] and not manager.scan()[0].loaded
    state = c.run(cont.DEEP)  # every rule again, against real PyPI too: still red
    assert state["last_error"] is None and manager.scan()[0].flag
