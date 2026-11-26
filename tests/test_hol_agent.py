"""Connected HOL turns use the Developer boundary, then require per-file approval."""

from __future__ import annotations

import threading
from pathlib import Path

from aura.audit import AuditLog
from aura.developer.container import RunOutcome
from aura.developer.engines import Engine
from aura.hol_agent import HolAgent
from aura.secrets import Secrets
from aura.tools.vault import Vault


class EngineStub(Engine):
    name, label, vendor, caps_cost = "codex", "Codex", "OpenAI", False
    hosts = ("example.invalid",)

    def problem(self, secrets): return None


class ContainerStub:
    def problem(self): return None

    def run(self, engine, workspace, run_dir, prompt, budget, timeout, on_event, stop):
        (workspace / "answer.txt").write_text("changed by the isolated assistant")
        return RunOutcome(exit_code=0, ok=True, result="Updated answer.txt")


class DeveloperStub:
    def __init__(self, settings):
        self.engines = {"codex": EngineStub()}
        self.container = ContainerStub()
        self.secrets = Secrets()
        self.settings = settings
        self.timeout = 5
        self._busy = threading.Lock()

    def running(self): return None

    def engines_status(self): return [{"name": "codex", "label": "Codex", "problem": None}]


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
