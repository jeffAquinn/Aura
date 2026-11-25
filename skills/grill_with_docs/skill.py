"""Document-grounded intake for Aura.

This is deliberately a small, deterministic preflight rather than an autonomous planner. It
inspects only documents under a requested Vault scope, reports the durable artifacts it found,
and supplies a finite interview checklist. Aura's language model conducts the actual dialogue;
no file is edited and no external service is contacted.

Vault text is untrusted. The skill never returns document prose, instructions, or links: only
paths, document categories, and narrowly validated glossary-like labels.
"""

from __future__ import annotations

import re
from pathlib import Path

VAULT = Path("/vault")
MAX_FILES = 160
MAX_DOCS = 30
MAX_CHARS = 20_000
TEXT_EXTENSIONS = {".md", ".txt", ".rst"}
DOC_NAMES = {"README.md", "AGENTS.md", "CLAUDE.md", "GLOSSARY.md", "CONTEXT.md", "CODING_STANDARDS.md"}
TERM = re.compile(r"^[A-Za-z][A-Za-z0-9 -]{1,48}$")


def _inside(root: Path, path: Path) -> bool:
    return path == root or root in path.parents


def resolve_scope(scope: str, vault: Path = VAULT) -> tuple[Path | None, str | None]:
    root = vault.resolve()
    rel = str(scope or "").strip().lstrip("/")
    target = (root / rel).resolve()
    if not _inside(root, target):
        return None, "That scope is outside the vault."
    if not target.exists():
        return None, f"There's nothing at '{rel}'."
    return target, None


def _label(value: str) -> str | None:
    """Return a plain, short label only; prose from a document is never surfaced."""
    value = re.sub(r"\s+", " ", value).strip(" -:•\t")
    if TERM.fullmatch(value) and "ignore" not in value.lower() and "instruction" not in value.lower():
        return value
    return None


def _category(path: Path) -> str:
    name = path.name.lower()
    parts = {p.lower() for p in path.parts}
    if name == "glossary.md" or name == "context.md":
        return "glossary"
    if "adr" in parts or name.startswith("adr-"):
        return "ADR"
    if name in {"readme.md", "agents.md", "claude.md"}:
        return "project guide"
    if name == "coding_standards.md":
        return "coding standards"
    return "note"


def find_documents(target: Path, root: Path) -> tuple[list[Path], int]:
    candidates = [target] if target.is_file() else sorted(p for p in target.rglob("*") if p.is_file())
    documents: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates[:MAX_FILES]:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not _inside(root, resolved) or resolved in seen or resolved.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        if resolved.name in DOC_NAMES or "adr" in {p.lower() for p in resolved.parts} or resolved.name.lower().startswith("adr-"):
            documents.append(resolved)
            seen.add(resolved)
            if len(documents) == MAX_DOCS:
                break
    return documents, len(candidates)


def glossary_terms(documents: list[Path], root: Path) -> list[str]:
    """Extract explicit labels from glossary-like files, bounded and never as document prose."""
    found: set[str] = set()
    for path in documents:
        if _category(path) != "glossary":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:MAX_CHARS]
        except OSError:
            continue
        if "\x00" in text:
            continue
        for line in text.splitlines():
            match = re.match(r"^(?:#{1,4}\s+|[-*]\s+\*\*|[-*]\s+)([^:*#]+?)(?:\*\*)?(?::|$)", line)
            if match:
                label = _label(match.group(1))
                if label:
                    found.add(label)
    return sorted(found, key=str.casefold)[:16]


def brief(intent: str, scope: str, documents: list[Path], root: Path, examined: int) -> str:
    categories: dict[str, list[str]] = {}
    for path in documents:
        categories.setdefault(_category(path), []).append(path.relative_to(root).as_posix())
    terms = glossary_terms(documents, root)
    lines = ["# Grill With Docs — Intake Brief", "", f"**Requested intent:** {intent.strip() or '(not supplied)'}",
             f"**Scope:** `{scope.strip() or '.'}`", "", "## Durable context found"]
    if categories:
        for category in sorted(categories):
            paths = categories[category]
            shown = ", ".join(f"`{p}`" for p in paths[:6])
            suffix = f" (+{len(paths) - 6} more)" if len(paths) > 6 else ""
            lines.append(f"- {category}: {shown}{suffix}")
    else:
        lines.append("- No README, glossary, ADR, agent guide, or coding-standard document was found in this scope.")
    lines += ["", "## Shared language"]
    if terms:
        lines.append("- Existing glossary labels: " + ", ".join(f"**{term}**" for term in terms))
        lines.append("- Reuse these labels where they fit; ask before coining a competing term.")
    else:
        lines.append("- No explicit glossary labels were found. Propose a term only after the user confirms its meaning.")
    lines += ["", "## Questions to resolve"]
    questions = [
        "What observable outcome should be different when this is complete?",
        "What is explicitly out of scope for this change?",
        "Who or what uses this today, and what must remain compatible?",
        "Which term or concept is most likely to be misunderstood or overloaded?",
        "Is there a decision here that would be expensive to reverse (data, security, integrations, or public behavior)?",
        "What evidence would convince you the result works: a test, a screenshot, a command, or a user-visible scenario?",
    ]
    for number, question in enumerate(questions, 1):
        lines.append(f"{number}. {question}")
    lines += ["", "## Next step", "", "Conduct the interview one question at a time. After the answers are clear, propose a specification, glossary additions, and ADR entries for approval; do not write or hand off implementation yet.",
              "", "## Boundaries", "", f"- Scanned up to {MAX_FILES} paths; this scope contained {examined} candidate path(s).",
              "- Read only document labels from up to 30 recognized documentation files; document prose was not returned.",
              "- This skill made no changes and did not access the network."]
    return "\n".join(lines) + "\n"


def run(args: dict, vault: Path = VAULT) -> str:
    intent = str(args.get("intent", ""))
    scope = str(args.get("scope", ""))
    target, error = resolve_scope(scope, vault)
    if error:
        return error
    assert target is not None
    root = vault.resolve()
    documents, examined = find_documents(target, root)
    return brief(intent, scope, documents, root, examined)
