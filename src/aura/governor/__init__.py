"""The Security Governor: deterministic checks that decide whether a skill may join Aura.

The language model never sets a risk score here (an AI's opinion of its own safety is circular).
This package is protected: the staging pipeline rejects any change that touches it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

SEVERITY = ("pass", "info", "yellow", "red", "reject")  # least to most serious


@dataclass(frozen=True)
class Finding:
    check: str  # "write-scope" | "protected" | "manifest" | "static-scan" | "provenance" | "tests"
    severity: str  # one of SEVERITY
    message: str
    detail: str = ""


@dataclass
class AuditReport:
    skill: str
    version: str = ""
    purpose: str = ""  # stated by the skill's manifest: shown as unverified
    findings: list[Finding] = field(default_factory=list)
    tests: list[dict] = field(default_factory=list)  # the skill's own tests, run in the sandbox
    suite: dict = field(default_factory=dict)  # Aura's existing test suite, run in staging
    image: str = ""

    @property
    def risk(self) -> str:
        worst = max((SEVERITY.index(f.severity) for f in self.findings), default=0)
        return "red" if worst >= SEVERITY.index("red") else "yellow" if worst == SEVERITY.index("yellow") else "green"

    @property
    def blocked(self) -> bool:
        """Red (Sprint 5: no override), an automatic reject, or any failing test."""
        tests_failed = any(t["status"] != "passed" for t in self.tests) or self.suite.get("failed", 0) > 0
        return self.risk == "red" or tests_failed or not self.tests

    def to_dict(self) -> dict:
        return {**asdict(self), "risk": self.risk, "blocked": self.blocked}
