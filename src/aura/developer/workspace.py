"""A build's scratch workspace, and what the Developer is told.

The workspace is all an engine ever sees: the approved specification, the skill format (the
contract the Security Governor enforces), one example skill, and skill/, the only folder whose
contents are taken. Nothing from Aura's repository, the vault or the conversation is in it.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from aura.skills import MAX_BYTES, TESTS

SECTIONS = ("Goal", "Inputs", "Output", "Steps", "Acceptance tests", "Constraints")
SPEC_LIMITS = (150, 12_000)  # characters

SKILL_FORMAT = f"""\
# How to write an Aura skill

An Aura skill is one folder, `skill/`, with exactly these files (others are allowed, total under
{MAX_BYTES // 1024} KB, no symbolic links):

## skill.toml
```toml
name = "csv_chart"            # lowercase letters, digits, underscores; 2-40 chars; matches the task
version = "1.0.0"             # x.y.z
purpose = "One sentence, 10-300 characters, saying what the skill does."
dependencies = ["pypdf==7.1.1"]   # every package pinned exactly with ==; [] if none

[inputs]                      # up to 8; every value Aura passes is a STRING
path = "Path of the file in the vault, e.g. 'Reports/q3.pdf'"

[permissions]
network = false               # always false: skills never use the network
vault = "read"                # "read" (vault mounted read-only at /vault) or "none"
```

## skill.py
Defines `run(args: dict) -> str`. `args` holds the inputs (strings). Return text for Aura to read;
keep it under 8,000 characters. Raise `ValueError` with a clear message for bad input.

## test_skill.py
Plain functions named `test_*` with `assert` (no pytest, no fixtures). They are imported and called
one by one. Build test data in code (e.g. write a small file under /tmp); do not rely on files in
the vault. Cover the acceptance tests in SPEC.md, plus bad input.

## The Security Governor rejects (and you must avoid)
- Any network use: socket, urllib, http, requests, httpx, aiohttp, ftplib, smtplib, ...
- Running programs or system calls: subprocess, os.system, os.popen, os.exec*, ctypes, multiprocessing, pty
- Dynamic code: eval, exec, compile, __import__
- Absolute paths outside /vault (read-only), /tmp and /skill
- Dependencies that aren't pinned with ==, have no ready-built wheel, have known vulnerabilities, are
  under 90 days old, or look like a misspelling of a popular package. Prefer the standard library.

## Where it runs
Each run: a fresh container with Python 3.12, numpy and pandas preinstalled, your pinned
dependencies, no network, the skill folder read-only at /skill and the vault read-only at /vault.
Here, in the build workspace, third-party packages are NOT installed and there is no network: you
can't pip install. `python3 run_tests.py` runs your tests the same way the Governor does (tests that
need a third-party package will fail here; that's expected, the Governor installs them).
"""

RUN_TESTS = TESTS.replace('sys.path.insert(0, "/skill")', 'sys.path.insert(0, "skill")')


def check_specification(spec: str) -> list[str]:
    """What's wrong with a Technical Specification (empty: fine)."""
    problems = []
    if not SPEC_LIMITS[0] <= len(spec.strip()) <= SPEC_LIMITS[1]:
        problems.append(f"it must be {SPEC_LIMITS[0]}-{SPEC_LIMITS[1]} characters")
    missing = [s for s in SECTIONS if not re.search(rf"(?im)^\s*(#+\s*)?{re.escape(s)}\b", spec)]
    if missing:
        problems.append("missing sections: " + ", ".join(missing))
    return problems


def prepare(workspace: Path, spec: str, example: Path, current: Path | None) -> None:
    """Lay out a fresh workspace. `current`: the installed skill, when improving one."""
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    (workspace / "SPEC.md").write_text(spec.strip() + "\n", encoding="utf-8")
    (workspace / "SKILL_FORMAT.md").write_text(SKILL_FORMAT, encoding="utf-8")
    (workspace / "run_tests.py").write_text(RUN_TESTS, encoding="utf-8")
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(example, workspace / "example" / example.name, ignore=ignore)
    if current is not None:
        shutil.copytree(current, workspace / "skill", ignore=ignore)
    else:
        (workspace / "skill").mkdir()


def first_prompt(name: str, improve: bool) -> str:
    task = (f"Improve the existing Aura skill in skill/ (named '{name}') as SPEC.md describes."
            if improve else f"Build a new Aura skill named '{name}' in skill/, as SPEC.md describes.")  # fmt: skip
    return (
        f"{task}\n\n"
        "Read SPEC.md and SKILL_FORMAT.md first, and use example/ as a model. Write only inside "
        "skill/ (skill.toml, skill.py, test_skill.py). Follow SKILL_FORMAT.md exactly: the Security "
        "Governor rejects anything else. Run `python3 run_tests.py` to check your work. Do not try to "
        "use the network or look outside this workspace. When done, reply with a short summary."
    )


def repair_prompt(name: str, round_no: int, findings: list[str]) -> str:
    listed = "\n".join(f"- {f}" for f in findings) or "- (no details)"
    return (
        f"The Security Governor rejected round {round_no} of the '{name}' skill in skill/. Its findings:\n"
        f"{listed}\n\n"
        "Fix every finding by editing the files in skill/ (SPEC.md and SKILL_FORMAT.md still apply). "
        "Run `python3 run_tests.py`, then reply with a short summary of what you changed."
    )


def collected(workspace: Path) -> Path:
    return workspace / "skill"


def has_skill(workspace: Path) -> list[str]:
    """Missing required files (checked before bothering the Governor)."""
    folder = collected(workspace)
    return [f for f in ("skill.toml", "skill.py", "test_skill.py") if not (folder / f).is_file()]
