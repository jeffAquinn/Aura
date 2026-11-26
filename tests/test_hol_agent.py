"""Connected HOL turns use the Developer boundary, then require per-file approval."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from aura.audit import AuditLog
from aura.developer.container import RunOutcome
from aura.developer.engines import Engine
from aura.developer import vault_tasks as vt
from aura.hol_agent import HolAgent, HolAgentError
from aura.secrets import Secrets
from aura.tools.vault import Vault


class EngineStub(Engine):
    name, label, vendor, caps_cost = "codex", "Codex", "OpenAI", False
    hosts = ("example.invalid",)

    def __init__(self):
        self.current_problem: str | None = None

    def problem(self, secrets): return self.current_problem


class ContainerStub:
    def __init__(self):
        self.calls = 0

    def problem(self): return None

    def run(self, engine, workspace, run_dir, prompt, budget, timeout, on_event, stop):
        self.calls += 1
        answer = "changed by the isolated assistant" if self.calls == 1 else "second isolated edit"
        (workspace / "answer.txt").write_text(answer)
        return RunOutcome(exit_code=0, ok=True, result="Updated answer.txt")


class DeveloperStub:
    def __init__(self, settings):
        self.engine = EngineStub()
        self.engines = {"codex": self.engine}
        self.container = ContainerStub()
        self.secrets = Secrets()
        self.settings = settings
        self.timeout = 5
        self._busy = threading.Lock()

    def running(self): return None

    def engines_status(self): return [{"name": "codex", "label": "Codex", "problem": self.engine.current_problem}]


def test_hol_agent_never_writes_until_each_change_is_allowed(settings):
    project = settings.vault_dir / "Aura"
    project.mkdir(parents=True)
    (project / "answer.txt").write_text("original")
    audit = AuditLog(settings.audit_log)
    agent = HolAgent(DeveloperStub(settings), Vault(settings.vault_dir, audit), audit)

    started = agent.start("terminal", "review", "Make the requested scoped change.", "codex", project)
    assert "credentials" in "\n".join(started)
    result = agent.send("terminal", "Change answer.txt.")
    assert result["changes"] and (project / "answer.txt").read_text() == "original"

    decided = agent.decide("terminal", [True])
    assert "Updated 'Aura/answer.txt'" in decided["output"]
    assert (project / "answer.txt").read_text() == "changed by the isolated assistant"
    agent.stop("terminal")


def test_hol_agent_refreshes_before_a_turn_when_the_vault_file_was_removed_between_turns(settings):
    project = settings.vault_dir / "Aura"
    project.mkdir(parents=True)
    (project / "answer.txt").write_text("original")
    audit = AuditLog(settings.audit_log)
    agent = HolAgent(DeveloperStub(settings), Vault(settings.vault_dir, audit), audit)

    agent.start("terminal", "review", "Make the requested scoped change.", "codex", project)
    agent.send("terminal", "Change answer.txt.")
    agent.decide("terminal", [True])
    (project / "answer.txt").unlink()  # A user or another local process changes the Vault after re-sync.

    with pytest.raises(HolAgentError, match="isolated workspace was refreshed"):
        agent.send("terminal", "Change answer.txt again.")
    assert agent.developer.container.calls == 1
    assert "Aura_Vault changed outside this HOL session" in agent._session("terminal").transcript[-1][1]

    result = agent.send("terminal", "Change answer.txt again.")
    assert result["changes"]
    assert not (project / "answer.txt").exists()
    agent.stop("terminal")


def test_hol_agent_verifies_the_approved_file_survived_the_write(settings, monkeypatch):
    project = settings.vault_dir / "Aura"
    project.mkdir(parents=True)
    (project / "answer.txt").write_text("original")
    audit = AuditLog(settings.audit_log)
    agent = HolAgent(DeveloperStub(settings), Vault(settings.vault_dir, audit), audit)
    agent.start("terminal", "review", "Make the requested scoped change.", "codex", project)
    agent.send("terminal", "Change answer.txt.")

    real_replace = Path.replace

    def replace_then_external_write(source, target):
        result = real_replace(source, target)
        if Path(target) == project / "answer.txt":
            Path(target).write_text("changed outside Aura")
        return result

    monkeypatch.setattr(Path, "replace", replace_then_external_write)
    decided = agent.decide("terminal", [True])

    assert "did not match the approved content after writing" in decided["output"][0]
    assert (project / "answer.txt").read_text() == "changed outside Aura"
    agent.stop("terminal")


def test_hol_agent_blocks_an_engine_that_becomes_degraded_before_a_turn(settings):
    project = settings.vault_dir / "Aura"
    project.mkdir(parents=True)
    (project / "answer.txt").write_text("original")
    audit = AuditLog(settings.audit_log)
    developer = DeveloperStub(settings)
    agent = HolAgent(developer, Vault(settings.vault_dir, audit), audit)
    agent.start("terminal", "review", "Make the requested scoped change.", "codex", project)

    developer.engine.current_problem = "The connected account session expired."
    with pytest.raises(HolAgentError, match="isn't ready: The connected account session expired"):
        agent.send("terminal", "Change answer.txt.")
    assert developer.container.calls == 0
    agent.stop("terminal")
