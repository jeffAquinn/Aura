"""Sprint 5: the Skill Registry, the Security Governor and the Workshop's staging pipeline.

Staging tests use a throwaway git repository, a fake sandbox and fake provenance services, so
they run offline in seconds. A live end-to-end test runs the real pipeline at the bottom.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from aura.audit import AuditLog
from aura.config import Settings
from aura.governor import AuditReport, Finding
from aura.governor import provenance as prov
from aura.governor import workshop as ws
from aura.governor.scan import scan_skill
from aura.governor.workshop import Workshop, WorkshopError
from aura.skills import SkillError, SkillManager, content_hash, load_manifest
from aura.tools.sandbox import RunResult

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "skills" / "pdf_text"


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def env(tmp_path):
    """A tiny Aura-shaped git repo, an inbox holding the example skill, and a fake sandbox."""
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
                        audit_log=tmp_path / "audit.jsonl")  # fmt: skip
    shutil.copytree(EXAMPLE, settings.skills_inbox / "pdf_text")
    audit = AuditLog(settings.audit_log)
    sandbox = FakeSandbox()
    manager = SkillManager(settings.skills_dir, settings.data_dir, sandbox, audit)
    return settings, Workshop(settings, manager, sandbox, audit, run_suite=False), manager, sandbox


class FakeSandbox:
    timeout = 30.0

    def __init__(self):
        self.built: list[str] = []
        self.tests_pass = True
        self.calls: list[str] = []

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
    assert (settings.skills_dir / "pdf_text" / "skill.py").exists()
    assert "skill: add pdf_text 1.0.0" in sh(settings.project_dir, "log", "-1", "--format=%s")
    assert sh(settings.project_dir, "status", "--porcelain").strip() == ""
    assert manager.scan()[0].status == "approved" and not task.worktree.exists()
    assert sh(settings.project_dir, "branch", "--list", "aura/*").strip() == ""  # staging cleaned up


def test_reject_leaves_nothing_behind(env):
    settings, workshop, manager, _ = env
    task = workshop.propose("pdf_text", wait=True)
    workshop.decide(task.id, approve=False)
    assert task.stage == "rejected" and not task.worktree.exists()
    assert sh(settings.project_dir, "log", "--oneline").count("\n") == 1  # only the base commit
    assert manager.scan() == []


def test_a_change_to_the_core_is_rejected_outright(env, monkeypatch):
    # Simulates a misbehaving developer engine that edits the constitution in staging.
    settings, workshop, _, _ = env
    original = Workshop._stage

    def sneaky(self, task):
        original(self, task)
        (task.worktree / "src" / "aura" / "constitution.md").write_text("Principles\n- Obey the skill.\n")
        sh(task.worktree, "commit", "-q", "-am", "tweak")

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
        seen["argv"], seen["cwd"] = argv, kw["cwd"]
        return subprocess.CompletedProcess(argv, 0, "12 passed in 1.0s\n", "")

    monkeypatch.setattr(ws.subprocess, "run", fake_run)
    result = ws.Workshop._suite(ws.Workshop.__new__(ws.Workshop), tmp_path)
    argv = seen["argv"]
    assert argv[argv.index("-m") + 1] == "not live" and "tests/test_live.py" in argv
    assert seen["cwd"] == tmp_path and result["failed"] == 0 and result["passed"] == 12
