# Pillar 3: the Observer

Status: **approved by the user and by Aura (architect), 2026-11-26. Built 2026-11-27** (see §8); awaiting Aura's review before tagging.

Aura 2.0's third pillar (`docs/aura-2-plan.md` item 3, D1, D7, D9): "Aura reports on its own activity: the containers it starts (resources, duration, network mode), turns and approvals, vault changes made during a task, and egress grants. It's shown in the app."

## 1. Outcome

An **Observer** window in the Mac app shows a live, colour-coded log of Aura's own activity. It opens with the recent history and then follows new events as they happen. You can pause it to read an entry, resume, and filter by type. It is read-only: nothing in it acts on Aura, and nothing in it goes back to the model.

## 2. What it shows

| Type | Examples | Source |
|---|---|---|
| Turns | a message from the Mac or Slack; done, stopped or failed; tools used | the live feed (`/v1/turns/live`), already masked |
| Actions | a tool's status line; an approval asked, allowed or denied | the feed and the audit log |
| Vault | a file written, a change applied or denied, a vault task | the audit log |
| Containers | a sandbox run, a Developer run, an audit's sandbox: start, end, duration, exit, network mode, memory and CPU limits | **new** audit records |
| Egress | a web search query granted and used, or refused | **new** audit records (the Egress Gatekeeper) |
| Connections | signing in, tokens renewed or expired, Slack chat switched | the audit log |
| Alerts | Continuous Audit alerts, skills flagged, recursion halted | the audit log |

The audit log (`actions.jsonl`) already records about 60 kinds of Aura activity, masked as it's written. The Observer is mostly a live view of it.

## 3. Design

- **One source:** `AuditLog.record()` gains a listener. Every entry, already masked, is also published on the existing turn broadcast as an `activity` frame (the Unified Command Stream's feed). There's no second logging service.
- **History:** `GET /v1/activity?limit=200` returns the newest audit entries, so the window has context when it opens. The feed then supplies live events, with the same numbered catch-up and `reset` as the mirror.
- **New records:**
  - `container_started` / `container_finished`: kind, network mode, memory and CPU limits, duration, exit code. Recorded where Aura runs `docker run` (`tools/sandbox.py`, `developer/container.py`).
  - `egress_granted` / `egress_used` / `egress_refused`, recorded in `tools/egress.py`.
  - Neither record holds a query's text beyond what the approval card already showed.
- **The app:**
  - A new **Observer** window, opened from the menu bar and the panel's header, like the Workshop.
  - It shows one row per event: time, type (colour and symbol), a one-line summary, and details on click.
  - Pause/Resume holds the view while events keep arriving, and the newest events come in when you resume.
  - A type filter.
  - The chat ignores `activity` frames.
- **Never blocks:** publishing is an append to the bounded broadcast buffer, so a full buffer drops the oldest frames. Aura never waits for a viewer.

## 4. Scope and non-goals

- No model reasoning ("THOUGHT"): the user's choice, 2026-11-26.
- No tool for Aura to read the log: the user's choice. Log text never re-enters the model, so there's no new prompt-injection path.
- Nothing beyond Aura's own activity (D7).
- No live container sampling (`docker stats`). Containers show their configured limits and duration.
- No change to what the audit log records today, apart from the new container and egress entries.
- `aura-session-review` reports reading the same log is a follow-up.

## 5. Terms

- **Observer:** the window, and its live view of Aura's activity.
- **Audit log:** `actions.jsonl`, the masked record the Observer shows.
- **Activity frame:** one audit entry on the live feed.
- **Live feed:** `/v1/turns/live`, the Unified Command Stream's mirror feed, which now also carries activity frames.

## 6. Unchanged

The Integrity Rule, approval cards defaulting to Deny, R1/R2/R5, the Egress Gatekeeper's behavior, and masking (entries are masked once, when recorded).

## 7. Acceptance evidence

- Offline tests:
  - An audit entry appears on the feed as an `activity` frame, masked.
  - `/v1/activity` returns the newest entries and needs the token.
  - Sandbox and Developer runs record start and finish with their limits and network mode.
  - Egress grants and uses are recorded.
  - The chat ignores activity frames.
  - A slow or absent viewer never delays a turn.
- Swift tests: the Observer store merges history and live frames, pauses and resumes without losing events, and filters by type.
- App: `scripts/build-app.sh`, and the window rendered offscreen with sample events.
- Live, by the user: open the Observer, ask Aura to search the web or run Python, and watch the approval, the container and the egress events appear.

## 8. As built (2026-11-27)

Built as designed, with these changes and clarifications:

- **The server names each entry's type.** `aura.audit.category()` maps every event name to its type, and the feed and `/v1/activity` carry that type. The log file itself doesn't. A test runs every event name in the source through the mapping, so the app keeps no list of its own. HOL Terminal start and stop count as Containers, and their records now include the limits.
- **Turn rows appear only live.** Turns, approvals, status lines and turn errors come from the feed's turn frames, not the audit log. They aren't in the history the window opens with, and a `reset` reload brings back only the audit entries. If the view is paused during a reset, the live-only rows it's holding are kept for Resume.
- **History and the feed never overlap or leave gaps.** `AuditLog.record()` runs its listeners while it holds the log's lock. `/v1/activity` takes the entries and the feed's position together (`AuditLog.snapshot`), so each entry is either in the history or arrives afterwards on the feed, never both. Reading history goes back from the end of `actions.jsonl` only as far as it needs to, so a long log doesn't hold up recording.
- **Container records match what actually ran.**
  - A Developer container is recorded as started only after its egress proxy is up.
  - Its finish is recorded even when an error escapes the run, marked `interrupted`.
  - A sandbox run that finds Docker down records `never_started`.
  - The Developer's egress proxy container isn't recorded separately.
- **The window stops following when it closes.** Reopening it loads the history again. A start that's still waiting for its history when the window closes, or when a second start replaces it, gives up.
- **Turn frames and activity frames share the feed's buffer.** The buffer was doubled to 4,000 frames. A very large burst of audit entries can still push a reconnecting panel into a `reset`.
- **Evidence:**
  - 699 offline Python tests, including `tests/test_observer.py`.
  - 142 Swift tests, including the Observer store and the window rendered offscreen.
  - `code-review` and `security-review`: no vulnerabilities found. The review fixes are listed above.
  - The live check by the user is still to come.
