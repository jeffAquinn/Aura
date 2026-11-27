# Plan: the Models window

Status: **plan only, not built.** Written by Claude Code on 2026-11-27 from Aura's planning request, which was refused for its size. It is refined against the code as it stands at commit a21f12f. Aura reviews it as architect before any code is written, because it adds cloud connections and changes where keys are entered.

## 1. Goal

All model settings, local and cloud, move out of Settings into a new **Models** window. It opens from the menu-bar menu and from the panel's ⋯ menu. In it the user can:
- see and choose the model for each role;
- connect cloud providers (keys and, later, more providers);
- install and manage local models;
- tune each model (sampling, thinking, context budget, reply length);
- find local models to download for llama.cpp and MLX.

Settings keeps everything else, plus one line pointing to the new window.

## 2. Revisions to the request, and why

| Request | Revised to | Why |
|---|---|---|
| "Download or connect to cloud LLM providers" | **Connect** only | Cloud models run on the provider's servers; there is nothing to download. Local downloads stay (Ollama pulls, llama.cpp files). |
| Per-model **system prompt** | **Not offered.** Optionally, later, a short "model notes" text added after the Core rules, never replacing them. Aura decides. | Aura's system prompt is the constitution, the persona and the Core rules. The Integrity Rule depends on them reaching every model unchanged. A per-model prompt that replaced them would let a setting remove Aura's safeguards. |
| Per-model temperature, max tokens and so on | **Use the approved per-model settings spec** (`docs/model-settings.md`) | That spec, approved by Aura with revisions, already defines the fields, ranges, storage and acceptance tests. The Models window is where its screen goes. "Max tokens" is added to it (the reply limit, today one global `AURA_MAX_REPLY_TOKENS`). |
| More cloud providers, with "endpoint configuration" | **Phase 1: Anthropic only, moved. Phase 2 (separate review): one "OpenAI-compatible cloud" provider type, HTTPS only, host shown on every turn's model label.** | Every new provider is a new place the conversation goes. A free-form endpoint could send conversations anywhere, so it needs its own security review, and the user must see the host. |
| MLX download links | **Links, plus a note on what runs them** | Aura has no MLX provider. Ollama already runs many models on Apple's MLX (its `ollama ps` shows the `mlx` runner), so MLX speed is available today through Ollama. A direct MLX provider (`mlx_lm.server`, which speaks the same API as llama-server) is a possible later phase. |
| "Plan written in the sandbox" | Written in `docs/` (repository) and copied to the vault's `Aura/docs/specs/` | Aura's sandbox task was refused for its size. It is a plan for the live code, so it sits beside the other specs. |

## 3. Where model settings live today

**App (Swift)**
- `app/Sources/AuraUI/SettingsView.swift`
  - `ModelsSection` (about lines 193 to 400): the role pickers (`ModelRoleRow`: Startup, Chat, Deep Research, Web Search), the Anthropic key warning, Sync cloud models, Coding (`DeveloperCodingRow`: Claude Code or Codex), the Startup-model problem, Sleep Mode with its confirmation dialog, and Install a local Ollama model with its confirmation dialog.
  - `KeysSection` / `KeyRow`: every key, including **Anthropic API key (Aura cloud models)**.
- `app/Sources/AuraUI/ChatView.swift`: `ChatModelPicker` (the chat box's model menu). It stays where it is.
- `app/Sources/AuraKit/ChatStore.swift`: `modelCatalog`, `adopt(_:)`, `updateModels`, `updateCodingEngine`, `syncCloudModels`, `updateSleepMode`, `pullLocalModel`, `pickChatModel`.
- `app/Sources/AuraKit/Protocol.swift`: `ModelCatalog`, `ModelChoice`, `SleepMode`, `ModelRoutingUpdate`.
- `app/Sources/AuraKit/AuraClient.swift`: the `/v1/models*` and `/v1/secrets*` calls.

**Server (Python)**
- `src/aura/model_routing.py`: `ModelRouting` (roles, coding engine, Sleep Mode, the Anthropic model list), stored in `model_routing.json` in Aura's data folder; `ModelChoice`; the providers (`ollama`, `llamacpp`, `anthropic`).
- `src/aura/server.py`: `GET/PUT /v1/models`, `POST /v1/models/pull`, `POST /v1/models/sync-cloud`, `PUT /v1/models/coding-engine`, `PUT /v1/models/sleep`, `POST /v1/models/prepare`; `IdleSleep`, `ActiveModelKeeper`, health's model state.
- `src/aura/secrets.py`: `KEYS` (Keychain), including `anthropic_api_key`.
- `src/aura/brain.py`: `build_model`, `RECOMMENDED_SAMPLING` (Bonsai's defaults), `engine_name`.
- `src/aura/llama_server.py`: llama.cpp models (`~/models`), starting and stopping llama-server.
- `src/aura/tools/models.py`: `list_models`, Aura's read-only view of all this.
- `src/aura/config.py`: the global settings: `reasoning_effort`, `context_tokens`, `max_reply_tokens`, `model_keep_alive`, `llamacpp_models_dir`.

## 4. The Models window

A new `NSWindow`, made the way the Workshop, Workstation and Observer windows are (`AuraApp.swift`): title "Aura Models", about 900 × 640 points, size saved as `AuraModels`, one instance re-shown when opened again.

**Layout: a sidebar of four sections** (a `NavigationSplitView`, like the Workshop):

1. **Roles.** The model for each role (Startup, Chat, Web Search, Deep Research), the Coding developer (Claude Code or Codex), and the Startup-model problem when there is one. These are moved from `ModelsSection` unchanged. Each row shows the model's provider and whether it is loaded now.
2. **Local.**
   - **Ollama:** installed models with size and "loaded now", and Install a model (the existing confirmed download).
   - **llama.cpp:** the `.gguf` files found in the models folder with sizes, the folder path with Show in Finder, and whether llama-server is running and which model it holds.
   - **Memory:** the Mac's memory in use (already measured for the header) next to each loaded model's size.
   - **Sleep Mode and the idle time:** moved here, with today's wording ("after 10 idle minutes…").
   - **Find models:** the download links (§9).
3. **Cloud.** One card per provider. Phase 1 has just Anthropic:
   - the key's status (set or not set, never shown) with Set, Replace and Remove, the same `KeyRow` behavior moved;
   - Sync cloud models;
   - the models the account can use;
   - a model that recently hit its account limit (`CloudModelAvailability`).
   Phase 2 adds "Add an OpenAI-compatible provider" (§6).
4. **Model settings.** Pick any model Aura knows; edit its settings (`docs/model-settings.md` §3 and §4, plus reply length); Use recommended; Reset to the model's own defaults. Fields a provider doesn't accept are greyed out with a note, as Aura asked in its review. The context check warning appears here.

**State and data flow**
- The window takes the existing `ChatStore`, as `SettingsView` does today. `ChatStore` stays the single owner of `modelCatalog`, so the chat box's model menu and the window can never disagree. New store methods: `loadModelSettings()`, `updateModelSettings(_:)`, `loadLocalModels()`.
- All changes go through the server, as today. The window never writes files or the Keychain itself.
- **Refreshing:** the window reloads when it opens, after each change, and every 5 seconds while it is in front (loaded-model state changes on its own).

## 5. Menus and navigation

- **Menu-bar menu** (`AuraApp.makeMenu`): add "Models…" after "Connections…".
- **Panel ⋯ menu** (`HeaderView.controls`): add "Models…" after "Settings…".
- **Settings:** `ModelsSection` is replaced by one row: "Models moved to their own window" with an Open Models… button. The Anthropic key leaves `KeysSection` (the Keychain item itself doesn't change; see §7). Keys keeps a one-line pointer for a release.
- **Elsewhere:** the Startup-model warning in Settings and the "No llama.cpp models found" hint move with their sections. The chat box's model menu gains a last item, "Model settings…", which opens the window at that model.
- New `AuraApp` method: `openModels(selecting:)`, passed to the panel as an environment action, like `openWorkshop`.

## 6. Data model

**Per-model settings** (from `docs/model-settings.md`, unchanged except for one field):
- `ModelSettings`: provider + model name (the key), `temperature`, `top_p`, `top_k`, `min_p`, `presence_penalty`, `repeat_penalty`, `effort` (none, low, medium, high, max), `context_budget`, and **new: `max_reply_tokens`** (1,024 to 32,768; blank means the global `AURA_MAX_REPLY_TOKENS`).
- Every field is optional. Blank means "don't send it", so the model's own default applies.
- The ranges are those of the spec's §4.
- Bonsai's recommended values move from `brain.RECOMMENDED_SAMPLING` into this data as its defaults, so the window shows them and the user can change them.

**Cloud providers**
- Phase 1: no new data. Anthropic stays a built-in provider; its key stays `anthropic_api_key` in the Keychain.
- Phase 2: `CloudProvider`:
  - `id`, `label`, `kind` (`openai-compatible`), `base_url` (HTTPS only, no credentials in the URL), `key_name` (a Keychain item `provider_<id>_api_key`), `models` (synced from the provider's `/models`).
  - Stored in `providers.json`, without the key.
  - A model choice's `provider` field then names the provider id. The existing `ModelChoice.validate` rules (name characters, known providers) extend to the configured ids.

## 7. Storage and migration

- **Unchanged:** `model_routing.json` (roles, coding engine, Sleep Mode, the Anthropic model list). Only the screen moves, not the storage.
- **New:** `model_settings.json` (per-model settings; no secrets). Missing or unreadable means "no settings": today's behavior, as the spec's §4 requires.
- **New in Phase 2:** `providers.json` (no secrets).
- **Keys:** the Anthropic key stays the same Keychain item. Moving its row between windows needs no data migration, and a user's saved key keeps working.
- **One migration:** Bonsai's built-in recommended values become stored defaults the first time `model_settings.json` is created. The code stops treating them as a special case.
- **Rollback:** restoring the previous build restores the old screens. `model_routing.json` was never changed, and an older build ignores `model_settings.json`.

## 8. Server and service changes

- `GET /v1/models/settings` and `PUT /v1/models/settings`: read, or validate and save, one model's settings. A model that isn't installed can still be configured.
- `GET /v1/models/local`: installed Ollama models with sizes, which are loaded, the llama.cpp files, llama-server's state, and memory in use. This is the same facts `list_models` gives Aura, as structured data, so the window and Aura share one implementation.
- `brain.build_model`: applies the stored settings for each provider (spec §3): `extra_body` for llama.cpp's `top_k`, `min_p` and `repeat_penalty`; Ollama only what its OpenAI endpoint accepts, checked during the build; Anthropic effort only.
- **Turn building:** the context check runs here, as Aura's revision asked, using the smaller of the budget and the server's window, and records an Observer entry when it caps.
- **Unchanged:** the existing `/v1/models*` and `/v1/secrets*` endpoints, which the window calls as Settings does now.
- **Phase 2:**
  - `POST /v1/models/providers` (add or edit), `DELETE` (remove), and `sync-cloud` per provider.
  - The Egress Gatekeeper is unaffected: model calls aren't web egress, but every cloud call is recorded in the audit log, as Anthropic calls are.
- **Not changed:**
  - the Integrity Rule;
  - approval cards defaulting to Deny;
  - R1, R2 and R5;
  - automatic routing's rule that a local request never becomes a cloud one;
  - the localhost-only llama.cpp rule.

## 9. Download links for local models

On the **Local** page, under "Find models", each with one sentence on what it is and how Aura uses it. They open in the browser; nothing downloads by itself.
- **llama.cpp (GGUF):** Hugging Face's GGUF models, `https://huggingface.co/models?library=gguf`. "Put the `.gguf` file in ~/models; Aura lists it and starts llama-server for it when you pick it."
- **MLX:** the MLX Community models, `https://huggingface.co/mlx-community`. "MLX models run on Apple silicon's GPU. Ollama already uses MLX for many models. Aura doesn't run MLX files directly yet."
- **Ollama** (already in Settings): `https://ollama.com/library?sort=popular`, next to Install a model.

## 10. Testing

- **Python, offline:**
  - per-model settings saved, validated and kept across restarts;
  - each provider is sent only its own parameters, and a blank field is left out;
  - a HOL session's effort wins over the model's setting;
  - the context check uses the smaller value and records the Observer entry (spec §8);
  - `max_reply_tokens` per model;
  - `GET /v1/models/local` matches `list_models`;
  - Bonsai's defaults migrate once;
  - the Phase 2 HTTPS-only rule and that keys never land in `providers.json`.
- **Swift:**
  - store tests for loading and saving settings;
  - snapshot tests of each of the four pages, light and dark, including a long model name and a model with no settings;
  - the menu items exist;
  - Settings shows the pointer and no longer the model rows;
  - the chat box's "Model settings…" opens the window at that model.
- **Live, with the user's go-ahead:**
  - a Bonsai turn whose llama-server `/slots` shows the saved temperature and `top_k`, and clearing a field brings back the file's default;
  - the routing eval (`tests/test_live_capabilities.py` and the 15 everyday prompts) shows no drop.

## 11. Risks and rollback

| Risk | Mitigation |
|---|---|
| Two places show model choices (the window and the chat box) and drift | One owner, `ChatStore.modelCatalog`; both read it, and the server is the only writer. |
| A per-model setting weakens safety (for example a "system prompt") | Not offered (§2). Settings are sampling, effort, budget and reply length only. |
| A setting makes a model misbehave (temperature 2, a tiny reply limit) | Range checks; blank = model default; Reset per model; Use recommended. |
| Context budget larger than the server's window | The check at turn build uses the smaller value; Settings warns (spec §4, Aura's revision). |
| Phase 2 endpoint sends conversations somewhere unexpected | HTTPS only; the host shown on the model label; a separate security review before Phase 2 ships. |
| Moving the Anthropic key row confuses a user | The Keychain item is unchanged; Keys keeps a pointer for a release. |
| Window refresh polling costs | Only while the window is in front, every 5 seconds, one local request. |

**Rollback.** Every slice is a two-way door except the Bonsai-defaults migration, which is harmless to roll back: an older build ignores `model_settings.json`. Phase 2 adds a file and Keychain items that an older build simply doesn't read.

## 12. Implementation order

Each step is a vertical slice that ships and is tested on its own.

1. **The window, empty, and its menu items.** Snapshot of the four pages with placeholders. Nothing moves yet.
2. **Roles page.** Move the role pickers, Coding and the Startup problem from `ModelsSection`. Settings shows the pointer. Store and snapshot tests.
3. **Local page.** Move Sleep Mode and Install a model. Add `GET /v1/models/local` (shared with `list_models`), llama.cpp files, memory, and the download links.
4. **Cloud page.** Move the Anthropic key row and Sync cloud models; Keys keeps a pointer.
5. **Per-model settings, server.** `model_settings.json`, `GET/PUT /v1/models/settings`, `build_model` per provider, Bonsai's defaults migrated, the context check at turn build (the approved spec, `docs/model-settings.md`).
6. **Model settings page.** The editor, greyed-out fields per provider, Use recommended, Reset, and the chat box's "Model settings…".
7. **Reviews:** `simplify`, `code-review`, `security-review`, the live Bonsai check, and the routing eval. Then Aura's review and a tag.
8. **Phase 2, after its own review:** OpenAI-compatible cloud providers.

## 13. Questions for Aura

1. Should a per-model "model notes" text, added after the Core rules, ever exist? Or is any per-model prompt text out?
2. Is Phase 2 (OpenAI-compatible cloud providers) wanted, and should a provider's host appear on every reply's model label?
3. Should the Anthropic key stay listed in Settings → Keys as well, or only in the Models window?
