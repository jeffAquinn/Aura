---
name: diagnosing-bugs
description: Evidence-first diagnosis of a reported bug, flaky test, or performance regression in the Aura repo, with gated phases from reproduction to regression proof. Use when the user reports something broken, slow, or flaky and before proposing a fix.
---

# Diagnosing bugs

Claude Code version of Aura's `diagnosing_bugs` skill. A plausible explanation is not a diagnosis: do not edit code to fix the bug until the reproduction and discriminating evidence exist.

Work through the phases in order. At each gate, write down the evidence (command, output, file:line) before moving on.

1. **Reproduce.** Find the smallest repeatable input, environment, and expected vs. actual result. Run it yourself; don't rely on the report alone.
2. **Minimise.** Remove unrelated inputs until the failure still occurs at the smallest useful seam (one test, one function, one request).
3. **Hypothesise.** List competing causes. For each, state what evidence would falsify it.
4. **Instrument.** Add or inspect the narrowest safe observation that separates the hypotheses: a focused test, a log line, `get_state` vs. `checkpointer.get_tuple`, and so on. Remove temporary instrumentation afterwards.
5. **Fix.** Make the smallest change the evidence justifies. Don't broaden scope.
6. **Regression proof.** Show that the original reproduction now passes and that nearby behavior still works. Prefer adding a test that failed before the fix.
7. **Retro.** If discovery or proof was hard, propose one guardrail (a test, a lint, a pointer) for the user to approve. See the `retro` skill.

## Aura specifics

- Run Python with `uv run`. `/usr/bin/python3` is blocked by the unaccepted Xcode license.
- Put pytest state in a named `/private/tmp/aura-pytest-*` directory (`--basetemp`) and remove that exact directory afterwards.
- Real-model tests are marked `live` and cost money or time. Don't run them without asking.
- Known flakes include parallel tool-order assertions (for example, `tests/test_connections.py::test_sending_shows_the_exact_message_and_waits_for_the_user`). Check that a "new" failure isn't one of these before diagnosing it.
- Never use `screencapture`: it triggers a Screen Recording prompt. To inspect windows, use CGWindowList.
- Circuit breaker: after three attempts that end in the same error or the same lack of progress, stop and report the command and evidence to the user.
