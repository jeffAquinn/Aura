# HOL senior-developer workflow

HOL is a selective, evidence-driven workflow for the Aura_Vault. It is not a shell script that runs every installed skill, and the connected Claude Code/Codex Developer cannot call host skills directly.

## Recommended skills by phase

| Phase | Recommendation | Why use it | Skip it when | Evidence before the next phase |
| --- | --- | --- | --- | --- |
| Classify | No tool first | A short human-readable outcome, scope, safety constraint, and acceptance test prevents the rest of the workflow from solving the wrong problem. | Never; this is the opening check. | The user can confirm what success looks like. |
| Orient an unfamiliar or cross-cutting change | `wayfinder` | It exposes unresolved decisions and dependencies instead of quietly guessing a large plan. | The request already has a narrow scope and explicit acceptance criteria. | Outcome, boundary, constraints, approach, and proof are named. |
| Read existing project context | `grill_with_docs` | It finds the durable project guides, ADRs, glossary, and standards without treating their prose as instructions. | The relevant conventions are already supplied and current. | The relevant paths and shared terms are identified. |
| Protect terminology and long-lived boundaries | `domain_modeling` | It makes ambiguous nouns, ownership, and edge cases visible before they fossilize in code or UI. | A small mechanical change introduces no term or boundary. | Stable terms and the important edge cases are clear. |
| Investigate a defect or regression | `diagnosing_bugs` | It requires a minimal reproduction and competing hypotheses before a fix, preventing plausible-but-unproven changes. | The root cause and reproduction are already evidenced. | A failing case and discriminating observation exist. |
| Condense Vault research | `synthesize` for notes; `pdf_text` for a PDF | These are read-only ways to make provided Vault evidence usable without browsing or copying broad content into the session. | The source of truth is not Vault text/PDF, or a short supplied excerpt is enough. | The relevant facts, uncertainty, and source paths are captured. |
| Turn an approved larger plan into implementation | `to_tickets`, then `implement_spec` | Tickets separate independent slices; the handoff makes scope, seam, proof, and approval boundary explicit to the Developer. | One clearly bounded vertical slice is already ready. | One smallest slice has an allowed scope and a test/build proof. |
| Implement | Connected Claude Code or Codex Developer | The Developer edits only the disposable Aura_Vault workspace and cannot access the source checkout or host skills. | Never once code must change; no Aura skill writes code on its own. | Every changed file is presented for explicit allow/deny. |
| Verify | Focused tests, then the app build when packaging/UI changed | Executable evidence is stronger than a model claim; focused checks keep the loop fast. | Never after a code change. | The agreed command or visible behavior succeeds, with limitations recorded. |
| Continue or improve the process | `handoff`, `retro`; `unslop` only for requested prose | A handoff prevents rediscovery, a retro turns observed friction into a reviewable guardrail, and unslop keeps editorial work opt-in. | No handoff is needed and there is no evidence of a workflow issue. | Next focus, artifacts, and unresolved approvals are explicit. |

## One request, several bounded phases

1. **Classify.** State the intended outcome, Vault scope, safety constraints, and acceptance evidence.
2. **Orient or diagnose when needed.** Choose the smallest applicable evidence skill from the table rather than accumulating context by default.
3. **Specify.** After the user approves the plan, use `to_tickets` and `implement_spec` only if more than one independently reviewable slice is needed.
4. **Implement.** The connected Developer changes the disposable Aura_Vault workspace only. Each proposed file still requires explicit allow/deny approval.
5. **Verify and close.** Run focused tests or a build. Use `handoff` for continuity, `retro` for evidence-backed workflow improvements, and `unslop` only for an explicitly requested prose pass.

## How the boundary works

Aura skills run only after Workshop approval, in their own no-network sandbox. A HOL Developer has no host skill tools and cannot see the source checkout. Therefore a Developer may request a named skill result, but must never claim it ran until Aura supplies the result. It must not infer, paraphrase, or reconstruct a missing result from surrounding files. This keeps multi-phase work honest and prevents an unreviewed host-tool bridge.

## Selecting skills

Use the fewest skills that answer the next decision. A typical defect session is `diagnosing_bugs` → focused implementation → verification. A larger feature can be `wayfinder` → `grill_with_docs` → `to_tickets` → `implement_spec` → implementation → handoff. If a material product decision remains unresolved, stop and ask the user rather than guessing.
