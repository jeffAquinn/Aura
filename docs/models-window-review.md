# Review packet: the Models window, as built

For: Aura, as architect. From: Claude Code, 2026-11-27. The plan you reviewed (`Aura/docs/specs/models-window.md`, with your four fixes applied) is built and pushed in steps 1 to 6, then reviewed in step 7. The user decides on the tag after your verdict.

## What was built

| Step | What | Commit |
|---|---|---|
| 1 | The "Aura Models" window and its menu items (menu-bar menu, the panel's ⋯ menu) | 7cf538f |
| 2 | Roles page: each role's model and the Developer for coding, moved unchanged from Settings | 0e5b093 |
| 3 | Local page: Ollama and llama.cpp models, what's loaded, memory, Sleep Mode, Install a model, links for GGUF, MLX and Ollama models; `local_state` shared by `GET /v1/models/local` and `list_models` | db67cc3 |
| 4 | Cloud page: the Anthropic key row (same Keychain item) and Sync cloud models; the key no longer appears in Settings → Keys, per your ruling | 4b80ac8 |
| 5 | Per-model settings on the server: `model_settings.json` keyed `"provider:model"`, `GET/PUT /v1/models/settings`, `build_model` per provider, `max_reply_tokens`, the context check | 59a6879 |
| 6 | Model settings page: the editor (fields a provider rejects are greyed out with a note, as you asked), Use recommended, Reset; "Model settings…" in the chat box's model menu | 517a191 |
| 7 | Reviews and their fixes (below) | this commit |

No Phase 2: Anthropic stays the only cloud provider (the user's decision).

## Two choices made while building (please confirm)

1. **Bonsai's recommended values were not migrated into the file.** They apply to a model with nothing saved, so today's behavior carries over without writing anything; saving settings replaces them, and Reset brings them back.
2. **A llama.cpp model's context budget also sets llama-server's window** (`-c`), because Aura starts that server. llama-server restarts if the budget changes. For Ollama, whose window Aura can't set, the budget is capped at the window `ollama ps` reports. Until Ollama has loaded the model, the configured 16k is assumed, and the check runs again at the start of each turn. A cap is recorded once as `context_budget_capped` (Observer).

## Reviews (step 7)

- **security-review:** no findings. Settings can't reach argv, a shell, a URL host or a file path; unknown keys are refused; the API key appears nowhere new; the new endpoints sit behind the token and host check.
- **code-review:** 3 important findings and 6 smaller ones, all fixed:
  - The Ollama cap rarely applied, because it was checked only when the model was built, before Ollama loaded it. It's now re-checked at the start of each turn, and an unloaded model assumes the configured window. Names match with or without `:latest`.
  - Saving the model in use during a turn could leave settings on disk that the running model didn't have. Now it saves only when no turn is running, and rebuilds the model in the same step.
  - A save finishing after another model was picked showed the old model's values. Fixed.
  - The smaller ones:
    - Whole-number fields reject fractions instead of silently cutting them.
    - A bad hand-edited value drops only itself.
    - Saves are serialized.
    - A window change is logged as such.
    - A failed load shows a Retry.
    - "Model settings…" always opens at the model asked for.
    - Two misplaced doc comments are moved back.
- **simplify:**
  - Removed the scaffolding left from step 1.
  - One provider list for every picker.
  - One warning label.
  - The settings file is re-read only when it changes (it's consulted every turn).
  - One `/api/ps` helper.
  - llama-server's window is computed once per load.
  - The window loads only the model catalog.
- **Not done (larger refactors, for later):**
  - One "provider profile" table, to replace the provider checks in `model_settings.py`.
  - Serving the settings schema once with the catalog.
- **Tests:** 749 Python and 160 Swift pass, including snapshots of all four pages.
- **Live checks:**
  - **Settings reach the model:** saved temperature 0.5, top_k 30, reply 2,048 and a 24,576-token window reached Bonsai, as llama-server's `/slots` and `/props` reported.
  - **Routing regression:** 19 of 20 on the 20 routing prompts. The one miss is the Developer hand-over prompt, the known weak one; earlier runs scored 38/40 and 20/20.

## Unchanged

- The Integrity Rule.
- Approval cards defaulting to Deny.
- R1, R2 and R5.
- The Egress Gatekeeper.
- The localhost-only llama.cpp rule.
- A local request never becoming a cloud one.
- No per-model prompt text.

## Questions for you

1. Do you accept the two choices above (recommended values not migrated; a llama.cpp budget sets llama-server's window)?
2. Should "unload any Ollama model for another role before loading a llama.cpp model" be added? One live run failed once with a llama-server "Compute error" while `gemma4:26b` (16 GB) was loaded. Five reruns didn't reproduce it, but on a 24 GB Mac it is the likely cause.

## Aura's verdict (2026-11-27) and what followed

Aura accepted the build and both choices, and recommended the tag. Its three observations:

1. **Is Ollama's reported window really enforced?** Yes. `ollama ps` reports the `context_length`
   Ollama allocated for the loaded model, and Ollama enforces it by dropping the oldest input that
   doesn't fit. Aura's cap makes Aura summarise its memory before that point, so it is a real
   limit on what Aura sends, not only a display.
2. **The "Compute error":** Aura recommended a warning rather than a hard unload. The user chose the
   unload: just before a llama.cpp model loads, Aura unloads Ollama's models (each unload is
   recorded for the Observer), and the Local page says so. A failure to unload never blocks the
   load. Live check: with `gemma4:26b` (16 GB) loaded, loading Bonsai unloaded it first and Bonsai
   answered.
3. **Reset's wording:** the button now says "Reset to recommended" for a model with recommended
   values (Bonsai), and "Reset to defaults" otherwise.
