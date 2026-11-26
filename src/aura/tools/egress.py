"""The Egress Gatekeeper: nothing Aura searches for leaves this Mac without the user's approval.

Every web_search query gets its own approval card (brain.py), and nothing is remembered: the same
query asked twice gets two cards. When the user approves one, `Aura.resume` asks this gate for a
grant bound to that approval, that tool call and the exact query. `WebSearch.search` uses the
grant up before any backend runs, so a call that skipped the card, changed the query, or reused a
grant is refused (PermissionDenied) and opens no connection.

Grants live in memory only, keyed by the tool call they answer: a restart drops any that were
approved but not yet used, and the search fails closed. (No signature: in one process, a key would
sit beside the grants it signs and protect nothing. Gemma's review, 2026-11-26.)

A query with characters the user can't see on the card (control, format such as zero-width and
bidi, unassigned, private-use, surrogates, line or paragraph separators, spaces other than U+0020,
and the default-ignorable characters such as variation selectors) is refused before any card is
shown: what the user reads is then exactly what would be sent.
"""

from __future__ import annotations

import threading
import unicodedata
from dataclasses import dataclass

from aura.capabilities import CAPABILITIES

# Unicode general categories a reader can't see or can't trust on a card. Checked on the raw code
# points: normalizing first (NFKC) would turn U+00A0 into a space and let it through.
HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cn", "Co", "Cs", "Zl", "Zp"})
# Characters that draw nothing although their category looks harmless: Unicode's
# Default_Ignorable_Code_Point set (DerivedCoreProperties.txt; Python's unicodedata doesn't expose
# it), e.g. variation selectors (Mn), which can carry any bytes invisibly after an emoji, and the
# Hangul fillers (Lo). Plus U+2800 BRAILLE PATTERN BLANK (So), which draws as a space.
INVISIBLE_RANGES = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
    (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x2800, 0x2800),
    (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
)


def _invisible(ch: str) -> bool:
    code = ord(ch)
    return any(low <= code <= high for low, high in INVISIBLE_RANGES)


# Tool -> the argument that leaves the Mac. Only these get grants (from the Capability Registry:
# the rows whose floor is "egress"; Phase 1: web search).
GATED: dict[str, str] = {c.name: c.gated_arg for c in CAPABILITIES if c.floor == "egress" and c.gated_arg}

EGRESS_WARNING = "Warning: This action will send a query to the internet. This request leaves your local Mac mini."
QUERY_LINE = "    │ "  # the query follows exactly as it will be sent: not quoted, trimmed or wrapped


class PermissionDenied(PermissionError):
    """An egress the user did not approve, exactly as asked: nothing was sent."""


class HiddenCharacters(PermissionDenied):
    """The query has characters a card can't show (`codes`, e.g. ["U+200B"]): never sent."""

    def __init__(self, codes: list[str]):
        super().__init__(f"The query has characters the card can't show ({', '.join(codes)}).")
        self.codes = codes


def hidden_characters(text: str) -> list[str]:
    """The code points in `text` the user couldn't see on a card, as "U+200B" labels (in order,
    without repeats). Empty means every character is visible (or an ordinary space)."""
    found: list[str] = []
    for ch in text:
        category = unicodedata.category(ch)
        if category in HIDDEN_CATEGORIES or (category == "Zs" and ch != " ") or _invisible(ch):
            label = f"U+{ord(ch):04X}"
            if label not in found:
                found.append(label)
    return found


def visible(query: object) -> bool:
    """Can this query be shown on a card exactly as it would be sent?"""
    return isinstance(query, str) and not hidden_characters(query)


def describe_search(query: str, destinations: str, private: bool = False) -> str:
    """The Egress Gatekeeper's card: the warning, where it goes, and the exact query. (A query with
    characters a card can't show never reaches a card.)"""
    lines = [EGRESS_WARNING,
             f"search the web ({destinations}) for this exact query ({len(query)} characters):",
             QUERY_LINE + query]  # fmt: skip
    if private:
        lines.append("This turn read your private data: check the query contains none of it.")
    return "\n".join(lines)


@dataclass(frozen=True)
class Grant:
    approval_id: str
    tool_call_id: str
    query: str


class EgressGate:
    """Single-use grants for approved egress, one per approved tool call."""

    def __init__(self) -> None:
        self._grants: dict[str, Grant] = {}
        self._lock = threading.Lock()

    def grant(self, approval_id: str, tool_call_id: str, query: str) -> Grant:
        """Called only by `Aura.resume`, for a web_search the user has just approved."""
        if not (approval_id and tool_call_id):
            raise PermissionDenied("A grant needs the approval and the tool call it answers.")
        if not visible(query):
            raise PermissionDenied("That query has characters the card can't show.")
        made = Grant(approval_id, tool_call_id, query)
        with self._lock:
            self._grants[tool_call_id] = made
        return made

    def grant_for(self, tool: str, args: dict, approval_id: str | None, tool_call_id: str | None) -> None:
        """Called by `Aura.resume` for every action the user approved: only GATED tools get a grant."""
        if tool in GATED and approval_id and tool_call_id:
            self.grant(approval_id, tool_call_id, args.get(GATED[tool]))

    def revoke(self, tool_call_id: str) -> None:
        """Drop the grant of a tool call that will never run (a stopped or settled turn)."""
        with self._lock:
            self._grants.pop(tool_call_id, None)

    def consume(self, tool_call_id: str | None, query: str) -> Grant:
        """Use up the grant for this tool call, or raise PermissionDenied. The query must be the
        exact one approved (compared code point for code point) and fully visible."""
        if hidden := hidden_characters(query):  # grant() refuses these too: a second line of defence
            raise HiddenCharacters(hidden)
        with self._lock:
            held = self._grants.pop(tool_call_id, None) if tool_call_id else None
        if held is None:
            raise PermissionDenied("No approval for this search: the user hasn't approved it on a card.")
        if held.query != query:
            raise PermissionDenied("This query isn't the one the user approved.")
        return held

    def pending(self) -> int:
        """How many grants are approved but not yet used (the tests check none are left over)."""
        with self._lock:
            return len(self._grants)
