"""Aura's identity, in three layers (Gemma's Tri-Layer Identity Model).

Layer 1, the Soul: `constitution.md` plus the core rules in persona.py. Shipped with the code and
immutable at runtime: no tool can change it, and it is in every system prompt.
Layer 2, the Expression: `persona.json` (this module). Aura may change it only one field at a time
and only with the user's approval; every change is versioned and can be reset.
Layer 3, the Hands: the tools section of the system prompt (persona.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aura.audit import AuditLog

# Editable fields: kind and maximum characters per value.
FIELDS: dict[str, tuple[str, int]] = {
    "name": ("text", 40),
    "tone": ("text", 400),
    "brevity": ("text", 300),
    "formality": ("text", 300),
    "backstory": ("text", 1500),
    "style": ("list", 300),
    "constraints": ("list", 300),
}
MAX_ITEMS = 12
MAX_TOTAL_CHARS = 3000

DEFAULT: dict[str, Any] = {
    "schema": 1,
    "name": "Aura",
    "tone": "Sophisticated: precise, well-informed, quietly confident. Dry wit is welcome; filler is not.",
    "brevity": "Concise: lead with the answer. Use as few words as the question deserves, and expand "
    "only when asked or when the topic genuinely needs depth.",
    "formality": "Natural and professional.",
    "backstory": "",
    "style": [
        "Helpful: anticipate the obvious next step, and offer it briefly.",
        "Plain text by default; Markdown only when structure (steps, code, tables) truly helps.",
    ],
    "constraints": [],
}


class PersonaError(ValueError):
    """A persona change or file that breaks the manifest's rules."""


@dataclass(frozen=True)
class Persona:
    data: dict[str, Any]

    @property
    def name(self) -> str:
        return self.data["name"]

    def render(self) -> str:
        """The Expression layer, as it appears in the system prompt."""
        d = self.data
        lines = [f"- Tone: {d['tone']}", f"- Brevity: {d['brevity']}", f"- Formality: {d['formality']}"]
        lines += [f"- Style: {s}" for s in d["style"]]
        lines += [f"- The user's standing preference: {c}" for c in d["constraints"]]
        if d["backstory"]:
            lines.append(f"- Backstory: {d['backstory']}")
        return "\n".join(lines)

    def public(self) -> dict[str, Any]:
        return {k: self.data[k] for k in ("name", *[f for f in FIELDS if f != "name"])}


def validate(raw: Any) -> dict[str, Any]:
    """Return a clean manifest, or raise PersonaError explaining what's wrong."""
    if not isinstance(raw, dict):
        raise PersonaError("the persona must be a JSON object")
    data: dict[str, Any] = {"schema": 1}
    for field, (kind, limit) in FIELDS.items():
        value = raw.get(field, DEFAULT[field])
        if kind == "text":
            if not isinstance(value, str):
                raise PersonaError(f"'{field}' must be text")
            value = value.strip()
            if field == "name" and not value:
                raise PersonaError("the name can't be empty")
            if len(value) > limit:
                raise PersonaError(f"'{field}' is longer than {limit} characters")
        else:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise PersonaError(f"'{field}' must be a list of text items")
            value = [v.strip() for v in value if v.strip()]
            if len(value) > MAX_ITEMS:
                raise PersonaError(f"'{field}' can have at most {MAX_ITEMS} items")
            if any(len(v) > limit for v in value):
                raise PersonaError(f"each '{field}' item must be at most {limit} characters")
        data[field] = value
    total = sum(len(v) if isinstance(v, str) else sum(map(len, v)) for k, v in data.items() if k != "schema")
    if total > MAX_TOTAL_CHARS:
        raise PersonaError(f"the persona would be {total} characters; the limit is {MAX_TOTAL_CHARS}")
    if "updated" in raw and isinstance(raw["updated"], dict):
        data["updated"] = raw["updated"]
    return data


class IdentityStore:
    """persona.json, its history, and field-by-field changes."""

    def __init__(self, data_dir: Path, audit: AuditLog):
        self.path = data_dir / "persona.json"
        self.history_path = data_dir / "persona.history.jsonl"
        self.audit = audit
        self.error: str | None = None  # set when the file on disk is invalid
        self._last_good = Persona(validate(DEFAULT))

    def load(self) -> Persona:
        """The current persona. A missing file is created from the default; an invalid one is
        reported and the last valid persona is kept."""
        if not self.path.exists():
            self._write(dict(DEFAULT, updated=_stamp("default", "")))
        try:
            persona = Persona(validate(json.loads(self.path.read_text(encoding="utf-8"))))
        except (OSError, ValueError) as e:  # JSONDecodeError and PersonaError are ValueErrors
            self.error = f"persona.json is invalid ({e}); using the last valid persona"
            return self._last_good
        self.error = None
        self._last_good = persona
        return persona

    # ── changes ─────────────────────────────────────────────────────────────────────────────

    def proposed(self, field: str, change: str, value: str) -> dict[str, Any]:
        """The manifest as it would be after the change; raises PersonaError if not allowed."""
        if field not in FIELDS:
            raise PersonaError(f"'{field}' is not an editable field ({', '.join(FIELDS)})")
        kind = FIELDS[field][0]
        current = self.load().data
        new = dict(current)
        value = value.strip()
        if kind == "text":
            if change != "replace":
                raise PersonaError(f"'{field}' is text: use change='replace'")
            new[field] = value
        elif change == "add":
            if any(v.casefold() == value.casefold() for v in current[field]):
                raise PersonaError(f"that is already in '{field}'")
            new[field] = [*current[field], value]
        elif change == "remove":
            match = [v for v in current[field] if value.casefold() in v.casefold()]
            if len(match) != 1:
                raise PersonaError(f"name exactly one existing '{field}' item to remove")
            new[field] = [v for v in current[field] if v != match[0]]
        elif change == "replace":
            new[field] = [value] if value else []
        else:
            raise PersonaError("change must be 'replace', 'add' or 'remove'")
        return validate(new)

    def describe(self, field: str, change: str, value: str, reason: str) -> str:
        """Approval-card text: exactly what would change, as a diff."""
        current = self.load().data
        try:
            new = self.proposed(field, change, value)
        except PersonaError as e:
            return f"change {current['name']}'s {field}, which will be refused: {e}"
        if field == "name":
            head = f"rename {current['name']} → {new['name']}"
            body: list[str] = []
        else:
            head = f"change {current['name']}'s {field}:"
            old_items = current[field] if isinstance(current[field], list) else [current[field]]
            new_items = new[field] if isinstance(new[field], list) else [new[field]]
            body = [f"    − {v}" for v in old_items if v and v not in new_items]
            body += [f"    + {v}" for v in new_items if v and v not in old_items]
        if reason.strip():
            body.append(f"    reason: {reason.strip()}")
        return "\n".join([head, *body])

    def apply(self, field: str, change: str, value: str, reason: str, by: str = "aura") -> Persona:
        before = self.load().data
        new = self.proposed(field, change, value)
        self._append_history(before)
        self._write(dict(new, updated=_stamp(by, reason)))
        self.audit.record("persona_updated", field=field, change=change, value=value[:300], by=by, reason=reason)
        return self.load()

    def reset(self, by: str = "user") -> Persona:
        self._append_history(self.load().data)
        self._write(dict(DEFAULT, updated=_stamp(by, "reset to default")))
        self.audit.record("persona_reset", by=by)
        return self.load()

    def history_count(self) -> int:
        if not self.history_path.exists():
            return 0
        return sum(1 for line in self.history_path.read_text(encoding="utf-8").splitlines() if line)

    def _append_history(self, data: dict[str, Any]) -> None:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)  # atomic: a crash never leaves a half-written persona


def _stamp(by: str, reason: str) -> dict[str, str]:
    return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "by": by, "reason": reason}
