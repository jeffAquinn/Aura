# Per-model settings

Status: **approved by the user, 2026-11-27. Aura approved it with revisions the same day (§10). The user then asked for the changes in §11, which reverse two earlier decisions and need Aura's review. Not built yet.**

## 1. Why

Aura sends no sampling settings today. Each model runs on whatever its server defaults to.

Measured on this Mac (2026-11-27), with Ternary Bonsai 2.5 27B served by llama-server:
- The GGUF carries its own defaults (`general.sampling.*`): temperature 1.0, top_p 0.95, top_k 20, min_p 0.05. These are thinking-mode values.
- Aura sends `reasoning_effort: "none"`, and Bonsai then doesn't think at all (zero reasoning tokens).
- So Bonsai's non-thinking turns run at thinking-mode temperatures.

The context budget (`AURA_CONTEXT_TOKENS`, 16,384) is global too, so a model served with a larger or smaller window can't be told apart.

## 2. Outcome

In Settings → **Models** → **Model settings**, you pick any model Aura knows and set its settings. Aura sends them on every call to that model: Mac turns, the chat box's one-turn pick, and Slack turns. A blank field means "don't send it", so the server's or the model's own default applies.

## 3. Settings, by provider

A field a provider doesn't accept is hidden for that provider.

| Setting | llama.cpp | Ollama | Anthropic |
|---|---|---|---|
| temperature, top_p, presence_penalty | yes | yes | no: current Claude models reject them with a 400 |
| top_k, min_p, repeat_penalty | yes | only if its OpenAI endpoint passes them through (checked during the build; hidden otherwise) | no |
| Thinking / effort | `reasoning_effort` none, low, medium or high | `reasoning_effort` | `output_config.effort`, low to max |
| Context budget (Aura's compaction limit) | yes, checked against llama-server's `/props` `n_ctx` | yes, checked against `/api/ps` `context_length` | yes |
| Reply length (`max_reply_tokens`, added 2026-11-27) | `max_tokens` on the request (llama-server's `n_predict`) | `max_tokens` on its OpenAI endpoint (Ollama's `num_predict`) | `max_tokens` (required by the API) |

The Anthropic column follows the `claude-api` reference. Fable 5.x, Opus 5.5, 5, 4.8 and 4.7, and Sonnet 5 reject `temperature`, `top_p` and `top_k`. Sonnet 5.5 and Haiku 5.5 reject any non-default value. API version `2026-10-21` rejects them on every model. Thinking can't be turned off on Opus 5.5: effort is its only control, and its default is `medium`.

## 4. Rules

- **Settings belong to a model, not a role.** They're keyed by provider plus model name and stored in `model_settings.json`, next to `model_routing.json`. Two roles that use the same model share its settings. The file holds no secrets and never a credential.
- **Values are range-checked:**
  - temperature 0 to 2
  - top_p and min_p 0 to 1
  - top_k 1 to 200
  - presence_penalty -2 to 2
  - repeat_penalty 0.5 to 2
  - context budget at least 4,096
  - reply length 1,024 to 32,768 tokens, and for a local model at most half its context budget (a longer value is capped at that, with the same warning as the context check)
- **No setting means today's behavior:** the global `reasoning_effort=none`, `AURA_CONTEXT_TOKENS`, `AURA_MAX_REPLY_TOKENS` (8,192), and no sampling values sent.
- **Reply length is an override:** a model's `max_reply_tokens` replaces the global `AURA_MAX_REPLY_TOKENS` for that model only. `build_model` applies it, as it applies the global value today; nothing else in a turn changes.
- **HOL:** a HOL session's own effort choice still wins over the model's setting.
- **Context check:** Aura's context budget is a compaction limit. The server's context window is what Ollama or llama-server allocates, which Aura can't set. If the budget is larger than the window, Settings warns, and Aura uses the smaller of the two, so the server never drops or refuses part of a conversation.

## 5. Bonsai

- `scripts/llama-server-bonsai.sh` gains flash attention (`-fa on`) and a q8_0 KV cache (`--cache-type-k q8_0 --cache-type-v q8_0`), each checked against this llama.cpp build.
- Bonsai's settings **start filled in with the recommended values** measured on this Q2_0 build (§10): temperature 0.7, top_p 0.8, top_k 20, min_p 0, presence_penalty 0, repeat_penalty 1.0. The user confirmed them on 2026-11-27. A **Use recommended** button puts them back after an edit.

## 6. Out of scope

- Starting or stopping Ollama, or changing Ollama's context window, from Aura. (llama-server is now started by Aura: §11.)
- `sysctl iogpu.wired_limit_mb` and other system changes.
- Thinking-mode sampling presets.
- Benchmarks, or switching which model a role uses.

## 7. Unchanged

The Integrity Rule, approval cards defaulting to Deny, R1/R2/R5, the Egress Gatekeeper, the llama.cpp localhost rule, and automatic routing's rule that a local request never becomes a cloud one.

## 8. Acceptance evidence

- Offline tests:
  - Settings are saved, validated and kept across restarts.
  - Each provider is sent only its own parameters, and a blank field is left out.
  - A HOL session's effort wins.
  - The context check warns and uses the smaller of the two values.
- A live check: a real Bonsai turn through Aura. llama-server's `/slots` shows the temperature and top_k that Aura sent, and clearing a field brings back the GGUF default.

## 9. Terms

- **Model settings:** the sampling, effort and context-budget values Aura sends to one model.
- **Context budget:** Aura's own limit, which triggers memory compaction. It isn't the server's context window.

## 10. Aura's review (2026-11-27) and the measurement

Aura approved the spec with these revisions:
- **The context check runs when Aura builds each turn,** not only in Settings. The warning shows in Settings, and an Observer entry is recorded when Aura caps the budget. (The budget is Aura's compaction limit, not a request parameter. The server's window comes from llama-server's `/props` and Ollama's `/api/ps`.)
- **Claude models:** the temperature, top_p and top_k fields are greyed out with a short "not accepted by this model" note, not hidden.
- **Script flags:** the evidence is llama-server's KV-cache size in its log, before and after.
- **Recommended values must be tested on this Q2_0 build.** Aura also proposed untested values (0.7 / 0.9 / top_k 40). The user chose to measure them first.

**Measurement (2026-11-27).** The run used the 20 prompts and the family scoring of `tests/test_live_capabilities.py`. Each prompt went through Aura's real agent with Bonsai, twice per profile, with every approval card denied. First, llama-server's `/slots` confirmed that the values Aura sends arrive, and that a blank field falls back to the GGUF's own defaults.

| Profile | temp / top_p / top_k / min_p | Right tool family | Median turn |
|---|---|---|---|
| GGUF defaults (nothing sent) | 1.0 / 0.95 / 20 / 0.05 | 34/40 (85%) | 23.8 s |
| Document, non-thinking | 0.7 / 0.8 / 20 / 0 | 34/40 (85%) | 16.9 s |
| Aura's proposal | 0.7 / 0.9 / 40 / 0.05 | 34/40 (85%) | 17.1 s |

- **The misses come from the prompts, not the sampling.** All three profiles missed "Change the way you sound." (6 of 6 runs) and "What add-ons have you got installed?" (5 of 6). Bonsai answered both without calling a tool.
- **Speed:** both 0.7 profiles are about 30% faster at the median.
- **Looping:** once, Aura's profile called `list_vault` 14 times instead of handing a job to the Developer. The document's profile did the same with 6 calls. This happened once per profile, so it's weak evidence.
- **Recommendation:** the document's non-thinking values. They tie on accuracy, have the fastest median, and keep the model's own top_k.

## 11. Revision (2026-11-27): Aura runs llama-server, and the Startup model is the one in use

The user asked for four changes after the measurement.

1. **Recommended is the default.** Bonsai's settings start with the §5 values instead of blank.
2. **Aura starts and stops llama-server, and it loads and unloads models like Ollama.**
   - Aura launches one llama-server in **router mode** when Aura starts, and stops it when Aura quits. Router mode starts with no model loaded, lists every GGUF in the models folder, and loads a model on its first request.
   - The models folder is `AURA_LLAMACPP_MODELS_DIR`, default `~/models`. A model's name is its file name without `.gguf` (for example `Ternary-Bonsai-2.5-27B-Q2_0`), as router mode reports it.
   - Aura writes a presets file (`--models-preset`) with each model's server flags: `--jinja`, the context size, `-fa on` and the q8_0 KV cache (§5). At most one model is loaded at a time (`--models-max 1`), because of the Mac's 24 GB.
   - The idle timer is `--sleep-idle-seconds`, the same value as Ollama's keep-alive (item 3). "Loaded" in health comes from router mode's per-model `status`, as Ollama's comes from `/api/ps`.
   - Sleep Mode can keep a llama.cpp model loaded, as it does for Ollama.
   - The saved role choice `bonsai-2.5-27b` is moved to the new name once, when Aura first finds the file.
   - `scripts/llama-server-bonsai.sh` stays, for running Bonsai by hand on another port.
3. **The idle unload time goes from 5 to 10 minutes** (`AURA_MODEL_KEEP_ALIVE` default `10m`), for Ollama and llama.cpp alike.
4. **At launch, the chat box's model picker starts on the Startup model,** not on "Automatic". It stays there until the user changes it, and "Automatic (Chat setting)" stays in the menu. The Startup model is loaded at launch whether it's an Ollama or a llama.cpp model.

**Security (for Aura's review):**
- llama-server listens on 127.0.0.1 only, on `AURA_LLAMACPP_PORT` (default 8081, so it doesn't clash with a hand-run server on 8080).
- Aura gives it a random API key for each launch, passed in the environment (`LLAMA_API_KEY`, not on the command line, where `ps` would show it), and sends that key on every call. Router mode warns that, by default, it has no key and allows every CORS origin. With the key, another local program or a web page can't use it. CORS is also limited with `--cors-origins localhost`.
- Aura runs only the `llama-server` program it finds at a fixed place (`AURA_LLAMA_SERVER`, default the Homebrew path), never one named by a model or a skill. It serves only files in the models folder.
- The localhost-only URL rule (§7) and automatic routing's rule that a local request never becomes a cloud one are unchanged.

**Acceptance evidence, added to §8:**
- Offline tests: the server's command line and presets file, the key passed in the environment, health read from router status, the name migration, the picker starting on the Startup model, and the 10-minute default.
- Live: Aura launches, llama-server is running with nothing loaded until the Startup model loads; a turn runs; after 10 idle minutes the model reports unloaded; quitting Aura stops llama-server.

### As built (2026-11-27)

The user asked for this to be built before Aura's review ("easy and seamless"); Aura reviews it before the tag.
- `src/aura/llama_server.py` runs **one llama-server per model**, not router mode: Aura starts it with `-m <file>` for the model a turn needs and stops the process to unload it. This keeps the load, unload and idle timing in Aura's hands, the way Ollama's keep-alive works.
- It starts on every turn's first step (Mac and Slack alike), when the chat box picks a model (`POST /v1/models/prepare`), and at launch for a llama.cpp Startup model. A turn on any other model stops it. After 10 idle minutes it stops, never during a turn. Quitting Aura stops it, and a llama-server left by an Aura that crashed is stopped at the next launch (pid file, checked against the command line).
- Port: kept at 8080 (`AURA_LLAMACPP_URL`), not 8081. If something else holds the port, Aura says so instead of using it.
- Names: a model's name is its file name, so no saved choice needed moving (no role used `bonsai-2.5-27b`).
- Not done: Sleep Mode for llama.cpp models, and the KV-cache size evidence (llama-server's default log level doesn't print it). The per-model sampling settings (§1 to §10, with Bonsai's recommended values as defaults) are still to build.
- Live check: Bonsai loaded in 11.8 s with `-fa on`, the q8_0 cache and `--cors-origins localhost`; a request without the key got 401; another web origin got no CORS permission; `n_ctx` was 16,384; switching to an Ollama model stopped the process.
- Bonsai's recommended values (§5) are now sent by default (`brain.RECOMMENDED_SAMPLING`, matched by the family name "bonsai" for llama.cpp models). llama-server's `/slots` confirmed 0.7 / 0.8 / 20 / min_p 0 / presence 0 / repeat 1.0 on a live turn. The Settings screen to edit them is still to build.
- The prompt now names the provider with the model ("gemma4:26b (Ollama, on this Mac)") and says Aura sets it for every message, after gemma doubted the name it was given.
