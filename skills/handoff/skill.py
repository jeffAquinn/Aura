"""Make a compact, safe continuity brief from user-supplied session facts only."""

import re

SECRET = re.compile(r"(?i)(sk-[a-z0-9_-]{8,}|(?:api[_ -]?key|token|password)\s*[:=]\s*)[^\s,;]+")
MAX = 6_000


def clean(text: str) -> str:
    text = SECRET.sub("[redacted]", str(text or ""))
    return re.sub(r"\s+", " ", text).strip()[:MAX]


def bullets(text: str, fallback: str) -> list[str]:
    parts = [clean(p).lstrip("-• ") for p in re.split(r"\n+|(?<=[.!?])\s+", text) if clean(p)]
    return parts[:8] or [fallback]


def run(args: dict) -> str:
    summary = clean(args.get("summary", ""))
    focus = clean(args.get("next_focus", "")) or "Clarify the next smallest safe step."
    artifacts = [a.strip() for a in str(args.get("artifacts", "")).split(",") if a.strip()]
    lines = ["# Session Handoff", "", "## Current state"]
    lines += [f"- {item}" for item in bullets(summary, "No session summary was supplied; re-establish the goal before acting.")]
    lines += ["", "## Next focus", f"- {focus}", "", "## Durable artifacts"]
    lines += [f"- `{clean(path)}`" for path in artifacts[:12]] or ["- None supplied; do not recreate artifacts until their location is confirmed."]
    lines += ["", "## Open questions", "- What remains uncertain or unverified?", "- What must be approved before the next action?",
              "", "## Suggested next move", "- Read the referenced artifacts first, then confirm the smallest next action. Use the appropriate planning or review skill only after the context is restored.",
              "", "## Safety", "- This handoff is derived only from supplied text. Potential credentials were redacted; verify facts against the referenced artifacts."]
    return "\n".join(lines) + "\n"
