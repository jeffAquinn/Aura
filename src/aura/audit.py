"""Append-only JSONL audit log of everything Aura does that matters."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aura.masking import PATTERNS_ONLY, Masker


class AuditLog:
    def __init__(self, path: Path, masker: Masker = PATTERNS_ONLY):
        self.path = path
        self.masker = masker  # the Toolbox swaps in one that also knows the Keychain's values
        self._lock = threading.Lock()  # tool calls may run in parallel threads

    def record(self, event: str, **fields: Any) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event}
        entry.update(self.masker.mask_value(fields))  # never a credential, even one pasted into a message
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]
