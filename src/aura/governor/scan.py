"""Static scan of skill code (AST only: the code is never imported or run on the host)."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from aura.governor import Finding

NETWORK = {"socket", "http", "urllib", "urllib3", "requests", "httpx", "aiohttp", "ftplib", "smtplib",
           "telnetlib", "websocket", "websockets", "paramiko", "xmlrpc"}  # fmt: skip
SYSTEM = {"subprocess": "runs other programs", "ctypes": "calls native code directly",
          "multiprocessing": "starts other processes", "pty": "opens terminals"}  # fmt: skip
SYSTEM_CALLS = {"system", "popen", "execv", "execve", "execl", "execlp", "spawnv", "spawnl", "fork", "kill"}
DYNAMIC = {"eval": "evaluates text as code", "exec": "runs text as code", "__import__": "imports by name at runtime"}
ALLOWED_ROOTS = ("/vault", "/tmp", "/skill")


@dataclass(frozen=True)
class FileUse:
    """A file use the scan saw in the code: an absolute path it names (in full), or "tempfile"."""

    what: str
    where: str  # "pdf_text/skill.py:12", as on the scan's findings


def scan_file(path: Path, rel: str, uses: list[FileUse] | None = None) -> list[Finding]:
    """The file's findings. `uses`, when given, also collects its file uses (for Mechanics)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except SyntaxError as e:
        return [Finding("static-scan", "red", f"{rel} is not valid Python", f"line {e.lineno}: {e.msg}")]
    findings: list[Finding] = []

    def flag(severity: str, message: str, node: ast.AST) -> None:
        findings.append(Finding("static-scan", severity, message, f"{rel}:{getattr(node, 'lineno', '?')}"))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                root = name.split(".")[0]
                if root == "tempfile" and uses is not None:
                    uses.append(FileUse("tempfile", f"{rel}:{node.lineno}"))
                if root in NETWORK:
                    flag("red", f"uses the network ({name})", node)
                elif root in SYSTEM:
                    flag("red", f"{SYSTEM[root]} ({name})", node)
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "os" and fn.attr in SYSTEM_CALLS:
                flag("red", f"runs system commands (os.{fn.attr})", node)
            elif isinstance(fn, ast.Name) and fn.id in DYNAMIC:
                flag("red", f"{DYNAMIC[fn.id]} ({fn.id})", node)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if v.startswith("/") and len(v) > 1 and " " not in v:
                if uses is not None:
                    uses.append(FileUse(v, f"{rel}:{node.lineno}"))
                if not v.startswith(ALLOWED_ROOTS):
                    flag("yellow", f"refers to a path outside the vault ({v[:60]})", node)
    return findings


def scan_skill(skill_dir: Path, uses: list[FileUse] | None = None) -> list[Finding]:
    findings = []
    for path in sorted(skill_dir.rglob("*.py")):
        if "__pycache__" not in path.parts:
            findings += scan_file(path, path.relative_to(skill_dir.parent).as_posix(), uses)
    if not [f for f in findings if f.severity in ("yellow", "red")]:
        findings.append(Finding("static-scan", "pass", "no network, system or dynamic-code use found"))
    return findings
