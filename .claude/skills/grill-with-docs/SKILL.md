---
name: grill-with-docs
description: Document-grounded intake interview before planning or coding an Aura change: reads the durable docs, asks the user clarifying questions one at a time, and proposes a spec for approval. Use proactively when a request is a new feature, a sprint idea, or ambiguous about outcome, scope or acceptance evidence. Skip it for small, well-specified fixes.
---

# Grill with docs

Claude Code version of Aura's `grill_with_docs` skill. Don't edit code or hand off implementation during this skill. The output is an agreed specification.

## 1. Read the durable context (silently)

Read whatever exists in scope: `README.md`, `docs/sprint-*-design.md` (the latest design shows the current architecture and the architect's decisions: Gemma's through 2026-11-26, Aura's since), ADRs, glossaries, coding standards, and the module docstrings of the code involved. Note the existing terms so you reuse them rather than coin competing ones.

## 2. Brief the user

Briefly list the documents you found and the terms that apply to this change.

## 3. Interview, one question at a time

Ask these in order. Skip one when the docs or the user's request already answer it, and say which doc answered it:

1. What observable outcome should be different when this is complete?
2. What is explicitly out of scope?
3. Who or what uses this today, and what must stay compatible (app, Slack/Discord remote, vault layout, approvals)?
4. Which term or concept is most likely to be misunderstood or overloaded?
5. Is any decision here expensive to reverse (data, security, the Integrity Rule, the Security Governor, integrations, public behavior)?
6. What evidence would convince the user it works: a test, a screenshot (not via `screencapture`), a command, or a user-visible scenario?

Wait for each answer before asking the next question. Use AskUserQuestion when the answer is a choice between concrete options.

## 4. Propose

When the answers are clear, propose a short specification: outcome, scope, non-goals, constraints, acceptance evidence, and any glossary or design-doc additions. Ask for approval. If the change touches Aura's security model or architecture, point out that Aura, the project architect, reviews it first (see the sprint workflow in memory). Once the spec is approved, continue with `to-tickets` or `implement-spec`.
