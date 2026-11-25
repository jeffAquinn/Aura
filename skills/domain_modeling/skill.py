"""Read explicit glossary labels without returning untrusted document prose or making edits."""

from __future__ import annotations

import re
from pathlib import Path

VAULT = Path("/vault")
MAX_FILES = 120
MAX_CHARS = 20_000
TERM = re.compile(r"^[A-Za-z][A-Za-z0-9 -]{1,48}$")


def inside(root: Path, candidate: Path) -> bool:
    return candidate == root or root in candidate.parents


def clean_label(value: str) -> str | None:
    value = re.sub(r"\s+", " ", value).strip(" -:•\t")
    if TERM.fullmatch(value) and "ignore" not in value.lower() and "instruction" not in value.lower():
        return value
    return None


def documents(target: Path, root: Path) -> list[Path]:
    candidates = [target] if target.is_file() else sorted(p for p in target.rglob("*") if p.is_file())
    out = []
    for path in candidates[:MAX_FILES]:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if inside(root, resolved) and resolved.suffix.lower() in {".md", ".txt"} and resolved.name.lower() in {"glossary.md", "context.md", "readme.md"}:
            out.append(resolved)
    return out


def labels(paths: list[Path]) -> list[str]:
    found: set[str] = set()
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:MAX_CHARS]
        except OSError:
            continue
        if "\x00" in text:
            continue
        for line in text.splitlines():
            match = re.match(r"^(?:#{1,4}\s+|[-*]\s+\*\*|[-*]\s+)([^:*#]+?)(?:\*\*)?(?::|$)", line)
            if match:
                label = clean_label(match.group(1))
                if label:
                    found.add(label)
    return sorted(found, key=str.casefold)[:20]


def run(args: dict, vault: Path = VAULT) -> str:
    root, scope = vault.resolve(), str(args.get("scope", "")).strip().lstrip("/")
    target = (root / scope).resolve()
    if not inside(root, target):
        return "That scope is outside the vault."
    if not target.exists():
        return f"There's nothing at '{scope}'."
    focus = re.sub(r"\s+", " ", str(args.get("focus", "") or "")).strip()[:120]
    docs = documents(target, root)
    terms = labels(docs)
    lines = ["# Domain Model Review", "", f"**Scope:** `{scope or '.'}`", f"**Focus:** {focus or 'all explicit terms'}", "", "## Evidence"]
    lines += [f"- `{path.relative_to(root).as_posix()}`" for path in docs[:20]] or ["- No README, GLOSSARY.md, or CONTEXT.md document was found."]
    lines += ["", "## Explicit terms"]
    lines += [f"- **{term}**" for term in terms] or ["- No safe, explicit labels were found; ask the user to define the important nouns before naming code or artifacts."]
    lines += ["", "## Stress-test questions", "1. Does each important term name one stable concept, rather than several related ones?", "2. What is the boundary case that would make this term ambiguous?", "3. Which existing term should be reused instead of coined again?", "4. Does a durable decision need an ADR before code or Vault changes?", "", "## Boundary", "This is a read-only review. Propose glossary or ADR edits for approval; do not write them automatically."]
    return "\n".join(lines) + "\n"
