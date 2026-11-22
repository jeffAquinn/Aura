"""Evolution tools: inquire about libraries, see skills, propose a skill for audit, and have a
Developer (Claude Code or Codex) build or improve one.

None of these changes Aura: proposing only starts an audit in a shadow project, and a build only
produces a candidate that must pass the Security Governor. Installing needs the user's Approve &
Deploy on the Audit Card in the Workshop.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from aura.developer.workflow import Developer, DeveloperError
from aura.events import emit
from aura.governor.provenance import check_package
from aura.governor.workshop import Workshop, WorkshopError


def workshop_tools(workshop: Workshop, developer: Developer | None = None) -> list[BaseTool]:
    @tool
    def check_library(name: str, version: str = "") -> str:
        """Check a Python package's provenance with fixed rules (PyPI history, known
        vulnerabilities from OSV, typosquatting, ready-built packages). Facts only, never opinion."""
        try:
            report = check_package(name, version or None)
        except Exception as e:  # noqa: BLE001
            return f"Couldn't check {name}: {e}. Treat it as unverified."
        lines = [report.summary()] + [f"- [{f.severity}] {f.message}" for f in report.findings if f.severity != "pass"]
        return "\n".join(lines)

    @tool
    def list_skills() -> str:
        """List installed skills and the ones waiting in the Skills Inbox."""
        installed = [s.public() for s in workshop.manager.scan()]
        waiting = workshop.inbox()
        lines = ["Installed:"] + [f"- {s['name']} {s['version']} ({s['status']}, {'on' if s['enabled'] else 'off'}): {s['purpose']}"
                                   for s in installed] or ["- none"]  # fmt: skip
        lines += ["In the Skills Inbox (can be proposed):"]
        lines += [f"- {s['name']} {s['version']}: {s['purpose'] or 'invalid: ' + str(s['error'])}" for s in waiting] or ["- none"]
        return "\n".join(lines)

    @tool
    def propose_skill(name: str) -> str:
        """Propose a skill from the Skills Inbox for installation. This only starts the Security
        Governor's audit in a shadow project; the user decides on the Audit Card in the Workshop."""
        try:
            task = workshop.propose(name)
        except WorkshopError as e:
            return f"Not proposed: {e}."
        emit({"task": task.public()})
        return (f"The '{name}' skill is being audited in a shadow project (task {task.id}). "
                "The user will review the results and decide in the Workshop.")

    @tool
    def develop_skill(name: str, specification: str, engine: str = "claude", improve: bool = False) -> str:
        """Have a Developer (engine "claude" for Claude Code, or "codex" for Codex) build a new skill,
        or improve an installed one (improve=true), from a Technical Specification you write. The
        specification must have these sections: Goal, Inputs, Output, Steps, Acceptance tests,
        Constraints. The user sees and approves it first; it is all the Developer receives. The
        result must pass the Security Governor, and the user reviews it in the Workshop."""
        if developer is None:
            return "The Developer isn't available."
        try:
            build = developer.start(name, specification, engine, improve)
        except DeveloperError as e:
            return f"Not started: {e}."
        emit({"task": {"id": build.id, "skill": name, "stage": build.stage, "kind": "build"}})
        return (f"{developer.engines[engine].label} is now building '{name}' (build {build.id}). It will be "
                "audited by the Security Governor, and the user reviews the result in the Workshop.")

    tools = [check_library, list_skills, propose_skill]
    return tools + ([develop_skill] if developer is not None else [])
