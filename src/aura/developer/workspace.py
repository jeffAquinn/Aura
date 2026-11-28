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

SECTIONS = ("Goal", "Inputs", "Output", "Steps", "Mechanics", "Acceptance tests", "Constraints")
# Pillar 2 (docs/mechanics.md, approved by Aura 2026-11-26): what a skill relies on, one line each.
# The first four must say "none": the Security Governor already blocks any skill that does them.
MUST_BE_NONE = {
    "Network": "skills never use the network",
    "Processes": "skills can't start other programs (subprocess, os.system)",
    "Native code": "skills can't call native code (ctypes)",
    "Dynamic code": "skills can't run text as code (eval, exec)",
}
MECHANICS = (*MUST_BE_NONE, "Files")
_LABEL = re.compile(rf"(?i)^\s*[-*•]?\s*({'|'.join(map(re.escape, MECHANICS))})\s*:\s*(.*)$")
_CANON = {m.casefold(): m for m in MECHANICS}
# Mechanics' fixed "…: none" lines, left out of the length limits so a blank template stays too short.
BOILERPLATE = re.compile(rf"(?im)^\s*(#+\s*)?Mechanics\s*$|^\s*[-*•]?\s*({'|'.join(map(re.escape, MUST_BE_NONE))})"
                         r"\s*:\s*none\.?\s*$")  # fmt: skip
SPEC_LIMITS = (150, 30_000)  # characters

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


def _heading(section: str) -> str:
    return rf"(?im)^\s*(#+\s*)?{re.escape(section)}\b"


def _title(names: str) -> str:
    """A line that starts a section: a Markdown heading, or a section name alone on its line."""
    return rf"(?im)^\s*(#+\s*({names})\b.*|({names})\s*:?\s*)$"


def mechanics(spec: str) -> dict[str, str] | None:
    """The Mechanics section's lines (label: answer, e.g. "Files": "reads the vault file…"), or
    None when there's no Mechanics section. An answer may wrap onto the lines below it."""
    start = re.search(_title("Mechanics"), spec)
    if start is None:
        return None
    body = spec[start.end():]
    others = "|".join(re.escape(s) for s in SECTIONS if s != "Mechanics")
    end = re.search(rf"(?im)^\s*(#+\s*\S.*|({others})\s*:?\s*)$", body)  # any heading, or a section name alone
    found: dict[str, str] = {}
    current = None
    for line in body[: end.start() if end else None].splitlines():
        if (m := _LABEL.match(line)) and (label := _CANON.get(m.group(1).casefold())):
            current = label
            found[current] = m.group(2).strip()
        elif current and line.strip():
            found[current] = f"{found[current]} {line.strip()}".strip()
    return found


def _counted(spec: str) -> str:
    """The text the length limits apply to: without Mechanics' fixed "…: none" lines, so a blank
    template stays too short to send."""
    return BOILERPLATE.sub("", spec).strip()


def says_none(answer: str) -> bool:
    """Just "none", with at most a full stop or a short note in brackets: "none, except it fetches…"
    is not none."""
    return re.fullmatch(r"(?i)none\.?(\s*\([^()]{0,120}\))?\.?", answer.strip()) is not None


def check_specification(spec: str, require_mechanics: bool = True) -> list[str]:
    """What's wrong with a Technical Specification (empty: fine). `require_mechanics` False: one
    approved before Mechanics existed (a retry), checked as it was then."""
    problems = []
    if not SPEC_LIMITS[0] <= len(_counted(spec)) <= SPEC_LIMITS[1]:
        problems.append(f"it must be {SPEC_LIMITS[0]}-{SPEC_LIMITS[1]} characters")
    missing = [s for s in SECTIONS if not re.search(_heading(s), spec) and (require_mechanics or s != "Mechanics")]
    if missing:
        problems.append("missing sections: " + ", ".join(missing))
    declared = mechanics(spec)
    if declared is not None:
        if absent := [m for m in MECHANICS if m not in declared]:
            problems.append("Mechanics is missing: " + ", ".join(absent))
        for label, why in MUST_BE_NONE.items():
            if label in declared and not says_none(declared[label]):
                problems.append(f"Mechanics: {label} must be 'none': {why}, and the Security Governor blocks any "
                                "skill that does")  # fmt: skip
        if "Files" in declared and not declared["Files"]:
            problems.append("Mechanics: Files needs an answer: the vault, /tmp, or none, and why")
    return problems


def declared_places(files: str) -> set[str]:
    """Which of a skill's places a Files answer mentions: "vault" (/vault) and "tmp" (/tmp)."""
    words = {"vault": r"\bvault\b", "tmp": r"\btmp\b|\bscratch\b"}
    return {place for place, rx in words.items() if re.search("(?i)" + rx, files)}


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
