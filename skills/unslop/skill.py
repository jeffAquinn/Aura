"""A deterministic, opt-in prose review skill.

The rule identifiers and editorial concepts are adapted from Cursor's public
``pstack/skills/unslop/SKILL.md``.  This is not a copy of Cursor's skill:
Aura keeps it opt-in, offline, and limited to supplied text.  A report flags
possible patterns; it never presents an editorial preference as a factual error.
"""

import re

MAX_TEXT_CHARS = 4_500
MAX_FINDINGS = 15

# Kept as stable identifiers so a user or a future Aura skill can refer to a
# particular editorial concern without depending on prose wording.
RULES = {
    3: "Trailing -ing phrase that may add no fact",
    5: "Vague attribution without a named source",
    7: "Overused AI-style vocabulary",
    8: "Indirect verb where 'is' or 'has' may be clearer",
    9: "'Not just X, but Y' construction",
    12: "'From X to Y' construction without an evident scale",
    13: "Dash used as sentence punctuation",
    19: "Curly quotation marks",
    20: "Chatbot closing phrase",
    22: "Praise or agreement opener",
    23: "Filler phrase",
    24: "More than one hedge in a sentence",
    26: "Abstract metaphor noun",
    27: "Generic claim that needs a mechanism or number",
    30: "Adverb that may be weaker than a precise verb",
    31: "Needlessly indirect verb",
    33: "Arrow or shorthand instead of a complete sentence",
}

PATTERNS = (
    (3, re.compile(r"\b(?:highlighting|ensuring|showcasing|demonstrating|underscoring)\b[^.!?]{0,100}[.!?]", re.I)),
    (5, re.compile(r"\b(?:experts|sources|people)\s+(?:believe|say|think|agree)\b|\bit is widely believed\b", re.I)),
    (7, re.compile(r"\b(?:additionally|crucial|delve|enhance|foster|landscape|pivotal|showcase|testament|underscore|vibrant)\b", re.I)),
    (8, re.compile(r"\b(?:serves as|stands as|boasts|features)\b", re.I)),
    (9, re.compile(r"\bnot just\b[^.!?]{0,160}\bbut\b", re.I)),
    (12, re.compile(r"\bfrom\s+[^,.!?;]{1,60}\s+to\s+[^,.!?;]{1,60}", re.I)),
    (13, re.compile(r"[—–]")),
    (19, re.compile(r"[“”‘’]")),
    (20, re.compile(r"\b(?:i hope this helps|let me know if|happy to help)\b", re.I)),
    (22, re.compile(r"^\s*(?:great question|great point|absolutely|you're right|i agree)[!,.\s]", re.I)),
    (23, re.compile(r"\b(?:in order to|it is important to note that|at this point in time)\b", re.I)),
    (26, re.compile(r"\b(?:substrate|wedge|vector|nexus|primitive|harness|surface|scaffolding|paradigm|ratchet|north star|flywheel)\b", re.I)),
    (27, re.compile(r"\b(?:seamless|robust|powerful|game-changing|best-in-class)\b", re.I)),
    (30, re.compile(r"\b\w+ly\b", re.I)),
    (31, re.compile(r"\b(?:utilize|leverage)\b", re.I)),
    (33, re.compile(r"(?:->|→|⇒)")),
)

SAFE_REPLACEMENTS = (
    (re.compile(r"[“”]"), '"'),
    (re.compile(r"[‘’]"), "'"),
    (re.compile(r"\bin order to\b", re.I), "to"),
    (re.compile(r"\bit is important to note that\s*", re.I), ""),
    (re.compile(r"\bserves as\b", re.I), "is"),
    (re.compile(r"\bstands as\b", re.I), "is"),
    (re.compile(r"\butilize\b", re.I), "use"),
    (re.compile(r"\bleverage\b", re.I), "use"),
)


def _snippet(text: str, start: int, end: int) -> str:
    """Return a single-line excerpt, bounded so untrusted input cannot flood the report."""
    left = max(0, start - 45)
    right = min(len(text), end + 75)
    excerpt = re.sub(r"\s+", " ", text[left:right]).strip()
    if left:
        excerpt = "…" + excerpt
    if right < len(text):
        excerpt += "…"
    return excerpt.replace("`", "'")


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def findings(text: str) -> list[tuple[int, str]]:
    """Find possible style patterns.  Results are deliberately conservative suggestions."""
    found: list[tuple[int, str]] = []
    for rule_id, pattern in PATTERNS:
        for match in pattern.finditer(text):
            found.append((rule_id, _snippet(text, match.start(), match.end())))
            if len(found) >= MAX_FINDINGS:
                return found
    for sentence in _sentences(text):
        hedges = re.findall(r"\b(?:may|might|could|perhaps|possibly|maybe)\b", sentence, re.I)
        if len(hedges) > 1:
            start = text.find(sentence)
            found.append((24, _snippet(text, start, start + len(sentence))))
            if len(found) >= MAX_FINDINGS:
                break
    return found


def mechanically_rewrite(text: str) -> tuple[str, int]:
    """Apply only substitutions that do not require understanding the subject matter."""
    revised = text
    changes = 0
    for pattern, replacement in SAFE_REPLACEMENTS:
        revised, count = pattern.subn(replacement, revised)
        changes += count
    return revised, changes


def _review(text: str) -> str:
    found = findings(text)
    lines = ["# Unslop review", "", f"Checked {len(text)} characters. Possible style patterns: {len(found)}.", ""]
    if not found:
        lines += ["No listed patterns were found. This is a style screen, not proof that the prose is accurate or complete."]
        return "\n".join(lines)
    lines += ["## Possible edits", ""]
    for rule_id, excerpt in found:
        lines.append(f"- R{rule_id}: {RULES[rule_id]}. Near: \"{excerpt}\"")
    lines += ["", "## Editorial boundary", "",
              "These are suggestions, not errors. Confirm facts, preserve the author's intended tone, and make meaning-changing edits yourself."]
    return "\n".join(lines)


def run(args: dict) -> str:
    """Return a review, or safe mechanical revision plus a review of what remains."""
    text = args.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("'text' must be a non-empty string.")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"'text' must be at most {MAX_TEXT_CHARS} characters.")
    mode = str(args.get("mode", "review") or "review").strip().lower()
    if mode not in {"review", "rewrite"}:
        raise ValueError("'mode' must be 'review' or 'rewrite'.")
    if mode == "review":
        return _review(text)
    revised, changes = mechanically_rewrite(text)
    return f"# Safe mechanical rewrite\n\nApplied {changes} mechanical edit(s). Review the result before using it.\n\n{revised}\n\n{_review(revised)}"
