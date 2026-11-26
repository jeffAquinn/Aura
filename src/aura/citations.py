"""Deterministic, local verification for Knowledge citation markers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


SOURCE = re.compile(r"\[Source:\s*([^\]\r\n]*?)\s*\]")


@dataclass(frozen=True)
class CitationFinding:
    path: str
    reason: str


def verify_citations(text: str, vault_root: Path) -> list[CitationFinding]:
    """Return unverified source markers without opening any cited file.

    A model response is untrusted input. Canonicalize it before containment
    checks, then use the resolved path that was checked; never reconstruct a
    path from the original string. Citations are valid only for existing,
    regular files inside the local Knowledge library.
    """
    root = vault_root.expanduser().resolve()
    findings: list[CitationFinding] = []
    for match in SOURCE.finditer(text):
        raw = match.group(1).strip()
        if not raw:
            findings.append(CitationFinding(raw, "empty"))
            continue
        try:
            target = (root / Path(raw)).resolve()
            relative = target.relative_to(root)
        except (OSError, ValueError):
            findings.append(CitationFinding(raw, "outside the vault"))
            continue
        if not relative.parts or relative.parts[0] != "Knowledge":
            findings.append(CitationFinding(raw, "not a Knowledge source"))
        elif not target.is_file():
            findings.append(CitationFinding(raw, "not found"))
    return findings


def warning(findings: list[CitationFinding]) -> str:
    """A compact, user-visible settlement note for an otherwise streamed reply."""
    count = len(findings)
    noun = "citation" if count == 1 else "citations"
    return f"\n\n⚠️ Local citation check: {count} {noun} could not be verified in Knowledge/."
