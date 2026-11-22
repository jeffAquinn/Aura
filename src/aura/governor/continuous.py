"""The Continuous Audit (Gemma's mandate after Sprint 5): installed skills are re-checked against
what is known *today*, because a package that was safe at install can be found vulnerable tomorrow.

- Quick pass, at every server start: one OSV batch query covering every installed package.
- Deep pass, once a day while Aura is idle: every provenance rule again (PyPI and OSV).

The packages checked are the exact ones in each skill's image, recorded at deploy. A skill that
turns red is unloaded at once (red blocks, with no override) and the user is alerted; they then
uninstall it or keep it disabled. When the facts can't be fetched (offline, a service down), nothing
changes: an unknown never flags a skill and never clears one.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from aura.audit import AuditLog
from aura.governor import Finding
from aura.governor.provenance import check_package, normalize, resolve
from aura.skills import Skill, SkillManager

log = logging.getLogger(__name__)

QUICK, DEEP = "quick", "deep"
DAY = 24 * 3600
OSV_BATCH = "https://api.osv.dev/v1/querybatch"


def alert_message(skill: str, findings: list[dict[str, Any]]) -> str:
    """Gemma's alert wording for a new vulnerability; the same question for any other red finding."""
    if any("vulnerabilit" in f.get("message", "") for f in findings):
        return f"Warning: The {skill} skill has been flagged with a new vulnerability. Would you like to uninstall it?"
    return f"Warning: The {skill} skill has been flagged by the Continuous Audit. Would you like to uninstall it?"


def osv_batch(packages: list[tuple[str, str]], client: httpx.Client) -> list[list[str]]:
    """Known vulnerability IDs for each (name, version), in order, from one OSV request."""
    queries = [{"package": {"name": normalize(n), "ecosystem": "PyPI"}, "version": v} for n, v in packages]
    r = client.post(OSV_BATCH, json={"queries": queries})
    r.raise_for_status()
    results = r.json().get("results", [])
    if len(results) != len(packages):
        raise ValueError("OSV returned an incomplete answer")
    return [[v["id"] for v in res.get("vulns", [])] for res in results]


class ContinuousAudit:
    def __init__(
        self,
        manager: SkillManager,
        audit: AuditLog,
        state_path: Path,
        client: Callable[[], httpx.Client] = lambda: httpx.Client(timeout=20),
        clock: Callable[[], float] = time.time,
    ):
        self.manager = manager
        self.audit = audit
        self.state_path = state_path
        self.client = client
        self.clock = clock
        self.on_change: Callable[[], None] = lambda: None  # reload Aura's tools
        self._running = threading.Lock()  # one pass at a time

    # ── state (survives restarts, so "once a day" means once a day) ─────────────────────────

    def state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return {"last_quick": data.get("last_quick"), "last_deep": data.get("last_deep"),
                "last_error": data.get("last_error"), "running": self._running.locked()}  # fmt: skip

    def _save(self, **changes: Any) -> None:
        data = {k: v for k, v in self.state().items() if k != "running"} | changes
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_path)

    def deep_due(self) -> bool:
        last = self.state()["last_deep"]
        return last is None or self.clock() - datetime.fromisoformat(last).timestamp() >= DAY

    def alerts(self) -> list[dict[str, Any]]:
        return [{"skill": s.name, "message": alert_message(s.name, s.flag["findings"]),
                 "findings": s.flag["findings"], "since": s.flag["since"],
                 "acknowledged": s.flag.get("acknowledged", False)}
                for s in self.manager.scan() if s.flag]  # fmt: skip

    # ── a pass ──────────────────────────────────────────────────────────────────────────────

    def run(self, kind: str) -> dict[str, Any]:
        """Re-check every installed skill. Returns the state afterwards. Never raises."""
        if not self._running.acquire(blocking=False):
            return self.state()
        try:
            return self._run(kind)
        finally:
            self._running.release()

    def _run(self, kind: str) -> dict[str, Any]:
        installed = [s for s in self.manager.scan() if s.status == "approved"]
        now = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="seconds")
        try:
            packages = {s.name: self._packages(s) for s in installed}
            verdicts = self._quick(packages) if kind == QUICK else self._deep(packages)
        except Exception as e:  # noqa: BLE001: unknown changes nothing
            error = f"{type(e).__name__}: {str(e)[:200]}"
            log.warning("continuous audit (%s) couldn't finish: %s", kind, error)
            self.audit.record("continuous_audit_failed", kind=kind, error=error)
            self._save(last_error=error)
            return self.state()

        changed = False
        for skill in installed:
            red = [asdict(f) for f in verdicts[skill.name] if f.severity in ("red", "reject")]
            if red:
                if not skill.flag or skill.flag.get("findings") != red:
                    self.manager.flag(skill.name, kind, red)
                    self.audit.record("skill_flagged", skill=skill.name, kind=kind,
                                      findings=[f["message"] for f in red])  # fmt: skip
                    changed = True
            elif skill.flag and (kind == DEEP or skill.flag.get("kind") == QUICK):
                # A quick pass sees only OSV, so it can clear only what a quick pass raised.
                self.manager.clear_flag(skill.name)
                self.audit.record("skill_flag_cleared", skill=skill.name, kind=kind)
                changed = True
        self._save(**{f"last_{kind}": now, "last_error": None})
        self.audit.record("continuous_audit", kind=kind, skills=len(installed),
                          packages=sum(len(p) for p in packages.values()))  # fmt: skip
        if changed:
            self.on_change()
        return self.state()

    def _packages(self, skill: Skill) -> list[tuple[str, str]]:
        recorded = (self.manager.approvals().get(skill.name) or {}).get("packages")
        if recorded is not None:
            return [(n, v) for n, v in recorded]
        return resolve((skill.manifest or {}).get("dependencies", []))  # approved before packages were recorded

    def _quick(self, packages: dict[str, list[tuple[str, str]]]) -> dict[str, list[Finding]]:
        everything = sorted({p for pins in packages.values() for p in pins})
        if not everything:
            return {name: [] for name in packages}
        with self.client() as http:
            vulns = dict(zip(everything, osv_batch(everything, http), strict=True))
        return {name: [Finding("provenance", "red", f"{len(vulns[p])} known vulnerabilities in {p[0]} {p[1]}",
                               ", ".join(vulns[p][:5])) for p in pins if vulns[p]]
                for name, pins in packages.items()}  # fmt: skip

    def _deep(self, packages: dict[str, list[tuple[str, str]]]) -> dict[str, list[Finding]]:
        with self.client() as http:
            return {name: [f for n, v in pins for f in check_package(n, v, client=http).findings]
                    for name, pins in packages.items()}  # fmt: skip

    # ── the schedule ────────────────────────────────────────────────────────────────────────

    def start(self, idle: Callable[[], bool], every: float = 600) -> threading.Thread:
        """A quick pass now; then, checking every `every` seconds, a deep pass whenever one is due
        (24 hours since the last) and Aura is idle."""

        def loop() -> None:
            self.run(QUICK)
            while True:
                if self.deep_due() and idle():
                    self.run(DEEP)
                time.sleep(every)

        thread = threading.Thread(target=loop, name="continuous-audit", daemon=True)
        thread.start()
        return thread
