---
name: unslop
description: Review prose for concrete AI-writing patterns and optionally revise it while preserving meaning and tone. Use when the user asks to unslop or tighten text, and proactively on substantial prose before it ships: README and docs/ changes, Gemma review packets, user-facing UI strings, and long commit messages or PR bodies.
---

# Unslop

Claude Code version of Aura's `unslop` skill. Aura's rule IDs are kept so findings can be cross-referenced. (Aura's version adapts concepts from Cursor's public `unslop` skill.) Findings are suggestions, not errors.

## Patterns (with Aura's rule IDs)

- R3: a trailing "-ing" phrase that adds no fact ("…, highlighting the need for…")
- R5: vague attribution ("experts say", "it is widely believed")
- R7: overused AI vocabulary (delve, crucial, pivotal, foster, landscape, testament, underscore, vibrant, additionally)
- R8/R31: indirect verbs ("serves as", "stands as", "boasts", "utilize", "leverage" used as a verb)
- R9: "not just X, but Y"
- R12: "from X to Y" with no real scale between X and Y
- R13: a dash used as sentence punctuation (not number ranges or compound words)
- R19: curly quotes in code, Markdown source, or strings
- R20/R22: chatbot closers ("I hope this helps") and praise openers ("Great question!")
- R23: filler ("in order to", "it is important to note that")
- R24: more than one hedge in a sentence
- R26: abstract metaphor nouns (substrate, north star, flywheel, paradigm…)
- R27: generic claims that need a mechanism or a number (seamless, robust, powerful)
- R30: an adverb that is weaker than a precise verb. Use judgment: don't flag "only", "early", "family", "reply", and the like.
- R33: arrows or shorthand used in place of sentences

## Modes

- **review** (default): list each finding as `R<id> — <excerpt> — <suggested edit>`. Fifteen findings at most, the most impactful first.
- **rewrite**: return the revised text, then the findings that remain. Keep facts, technical terms, code, and the author's tone. Fix capitalization after removing a lead-in phrase. Keep "leverage" where it's a noun. Don't change quotes inside code spans. Point out any edit that might change the meaning.

This is a style screen, not proof that the text is accurate or complete.
