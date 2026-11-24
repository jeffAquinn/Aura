"""Masking secrets out of text that leaves a tool: tool output, audit entries, streamed replies,
Developer feeds and Slack answers.

Two layers:
- the exact values in the Keychain (Settings → Keys and connection tokens, including the strings
  inside a stored sign-in's JSON), cached briefly;
- the shapes of well-known credentials (Slack, Anthropic, OpenAI, Tavily, GitHub, Google, Discord,
  JWTs, "Bearer …"), for a secret Aura never stored.

A streamed reply arrives in chunks, and a secret may be split between two of them. StreamMasker
holds back the trailing run of token characters (letters, digits, -_.+/=) until the run ends, so
a token is always judged whole before any of it is sent.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

MASK = "[secret hidden]"
MIN_LITERAL = 12  # shorter stored values (e.g. a user id) aren't treated as secrets
CACHE_SECONDS = 30.0
MAX_HOLD = 4_096  # a token run longer than this is masked and released in pieces

PATTERNS = [
    re.compile(p)
    for p in (
        r"xox[abpeorst]-[A-Za-z0-9-]{8,}",  # Slack bot/user/refresh tokens
        r"xoxe\.xox[bp]-[A-Za-z0-9-]{8,}",  # Slack rotating tokens
        r"xapp-[A-Za-z0-9-]{8,}",  # Slack app-level tokens
        r"sk-ant-[A-Za-z0-9_-]{16,}",  # Anthropic
        r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}",  # OpenAI
        r"tvly-[A-Za-z0-9_-]{16,}",  # Tavily
        r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}",  # GitHub
        r"AIza[0-9A-Za-z_-]{30,}",  # Google API keys
        r"ya29\.[0-9A-Za-z_-]{20,}",  # Google OAuth access tokens
        r"1//[0-9A-Za-z_-]{30,}",  # Google refresh tokens
        r"[MNO][A-Za-z0-9_-]{23,27}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,40}",  # Discord bot tokens
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",  # JWTs
        r"(?i)(?<=bearer )[A-Za-z0-9._~+/=-]{16,}",  # an Authorization header's credential
    )
]
TOKEN_CHARS = re.compile(r"[A-Za-z0-9_\-.+/=~]+$")
BEARER_BEFORE = re.compile(r"(?i)bearer\s+$")  # "Bearer " right before a held token stays with it


def _strings(value: str) -> Iterable[str]:
    """A stored secret, or every string inside it when it's a sign-in's JSON."""
    try:
        data = json.loads(value)
    except ValueError:
        yield value
        return
    stack: list[Any] = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            stack += list(item.values())
        elif isinstance(item, list):
            stack += item


class Masker:
    """`values`: returns the secrets currently stored (called at most every CACHE_SECONDS)."""

    def __init__(self, values: Callable[[], Iterable[str]] = lambda: ()):
        self._values = values
        self._lock = threading.Lock()
        self._literals: list[str] = []
        self._loaded = -CACHE_SECONDS

    @classmethod
    def for_secrets(cls, secrets: Any) -> Masker:
        from aura.secrets import KEYS

        def values() -> Iterable[str]:
            for name in KEYS:
                value = secrets.get(name)
                if value:
                    yield from _strings(value)

        return cls(values)

    def refresh(self) -> None:
        with self._lock:
            self._loaded = -CACHE_SECONDS

    def literals(self) -> list[str]:
        with self._lock:
            if time.monotonic() - self._loaded >= CACHE_SECONDS:
                try:
                    found = {v for v in self._values() if isinstance(v, str) and len(v) >= MIN_LITERAL}
                except Exception:  # noqa: BLE001: a locked Keychain still leaves the patterns
                    found = set()
                self._literals = sorted(found, key=len, reverse=True)  # longest first: no partial leftovers
                self._loaded = time.monotonic()
            return self._literals

    def mask(self, text: str) -> str:
        if not text:
            return text
        for literal in self.literals():
            if literal in text:
                text = text.replace(literal, MASK)
        for pattern in PATTERNS:
            text = pattern.sub(MASK, text)
        return text

    def mask_value(self, value: Any) -> Any:
        """Masks every string inside a JSON-like value (an audit entry's fields)."""
        if isinstance(value, str):
            return self.mask(value)
        if isinstance(value, dict):
            return {k: self.mask_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.mask_value(v) for v in value]
        return value

    def stream(self) -> StreamMasker:
        return StreamMasker(self)


PATTERNS_ONLY = Masker()


class StreamMasker:
    """Masks a text that arrives in chunks. feed() returns what is safe to send now; flush() the rest."""

    def __init__(self, masker: Masker):
        self.masker = masker
        self._held = ""

    def feed(self, chunk: str) -> str:
        text = self._held + chunk
        tail = TOKEN_CHARS.search(text)
        cut = tail.start() if tail else len(text)
        if before := BEARER_BEFORE.search(text[:cut]):  # "Bearer " waits for its credential
            cut = before.start()
        # A literal secret may contain characters outside TOKEN_CHARS: never cut inside one, and
        # hold back any suffix that could still grow into one.
        for literal in self.masker.literals():
            start = text.find(literal)
            while start != -1:
                if start < cut < start + len(literal):
                    cut = start
                start = text.find(literal, start + 1)
            for k in range(min(len(literal) - 1, len(text)), 0, -1):
                if literal.startswith(text[-k:]):
                    cut = min(cut, len(text) - k)
                    break
        if len(text) - cut > MAX_HOLD:  # an endless run: release all but the last MAX_HOLD, masked
            cut = len(text) - MAX_HOLD
        ready, self._held = text[:cut], text[cut:]
        return self.masker.mask(ready) if ready else ""

    def flush(self) -> str:
        ready, self._held = self._held, ""
        return self.masker.mask(ready) if ready else ""
