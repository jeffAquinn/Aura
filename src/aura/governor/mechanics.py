"""Pillar 2, Mechanics (docs/mechanics.md, approved by Aura 2026-11-26): a Developer build's
declared Files line, compared with what the audit sees.

The Technical Specification declares which of the skill's places it uses (the vault, read-only;
/tmp). The audit compares that with `skill.toml` and with the file uses the static scan recorded
(scan.FileUse). Each difference is a yellow finding, stated as a plain fact for the user to judge
on the Audit Card: the code is only read, so a path string is evidence of use, not proof, and
nothing here blocks a skill. The four lines that must say "none" were enforced before the build.
"""

from __future__ import annotations

import re
from dataclasses import replace

from aura.developer.workspace import declared_places, mechanics
from aura.governor import Finding
from aura.governor.scan import ALLOWED_ROOTS, FileUse

CHECK = "mechanics"
UNDECLARED = "; Mechanics' Files line doesn't declare it"


def _in_tests(use: FileUse) -> bool:
    """A use in the skill's own tests (test_*.py): Mechanics describes the skill at work, and its
    tests may write fixtures to /tmp."""
    name = use.where.rsplit(":", 1)[0].rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py")


def _names(files: str, path: str) -> bool:
    """Whether the Files line names this path as a whole ("/etc" isn't named by "/etc/hosts")."""
    return re.search(rf"(?<![\w/.]){re.escape(path)}(?![\w/])", files) is not None


def compare(specification: str, manifest: dict, scanned: list[Finding],
            uses: list[FileUse]) -> tuple[list[Finding], list[Finding]]:  # fmt: skip
    """(the scan's findings, with undeclared outside paths marked; the Mechanics findings)."""
    declared = mechanics(specification)
    if declared is None or "Files" not in declared:
        return scanned, [Finding(CHECK, "yellow", "the specification declares no Files line in Mechanics",
                                 "it was written before Mechanics was required")]  # fmt: skip
    files = declared["Files"]
    places = declared_places(files)
    found: list[Finding] = []
    if manifest["permissions"]["vault"] == "read" and "vault" not in places:
        found.append(Finding(CHECK, "yellow", "skill.toml asks to read the vault" + UNDECLARED,
                             f'vault = "read"; Files: {files[:120]}'))  # fmt: skip
    tmp = next((u for u in uses if not _in_tests(u) and (u.what in ("tempfile", "/tmp") or u.what.startswith("/tmp/"))),
               None)  # fmt: skip
    if tmp and "tmp" not in places:
        found.append(Finding(CHECK, "yellow", "the code uses /tmp" + UNDECLARED, f"{tmp.where}; Files: {files[:120]}"))
    # Paths outside the skill's places are already yellow in the scan: say whether Files names them.
    undeclared = {(u.where, u.what[:60]) for u in uses if not _in_tests(u) and u.what.startswith("/")
                  and not u.what.startswith(ALLOWED_ROOTS) and not _names(files, u.what)}  # fmt: skip
    marked = [replace(f, message=f.message + UNDECLARED)
              if f.check == "static-scan" and any(f.detail == where and f"({path})" in f.message for where, path in undeclared)
              else f for f in scanned]  # fmt: skip
    if not found and not undeclared:
        found.append(Finding(CHECK, "pass", "the static scan found no file use beyond Mechanics' Files line",
                             f"paths built while the skill runs aren't seen; Files: {files[:120]}"))  # fmt: skip
    return marked, found
