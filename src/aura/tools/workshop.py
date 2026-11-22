"""Evolution tools: inquire about libraries, see skills, and propose a skill for audit.

None of these changes Aura: proposing only starts an audit in a shadow project. Installing needs
the user's Approve & Deploy on the Audit Card in the Workshop.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from aura.events import emit
from aura.governor.provenance import check_package
from aura.governor.workshop import Workshop, WorkshopError


def workshop_tools(workshop: Workshop) -> list[BaseTool]:
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

    return [check_library, list_skills, propose_skill]
