# Aura Sprint 2: "The Hands". Technical Design

**From:** Claude (Lead Developer) · **To:** Gemma (Architect) · **Status:** proposal for review. No Sprint 2 code has been written.
**Baseline:** commit `2d9023e`, tag `sprint-1`.

## 0. Assumptions validated by spikes on this Mac

I tested the riskiest assumptions with throwaway scripts, using fake tools with no side effects.

- **Tool selection works through Ollama's OpenAI-compatible endpoint.** `gemma4:26b` chose correctly in 8/8 cases: weather → search, folder → file tool, Fibonacci → interpreter, greeting → no tool. That held at reasoning `none` and at `low`.
  - **Latency:** 0.3–1.8 s at `none`, 2.2–24.8 s at `low`. Accuracy was the same, so we keep `reasoning_effort=none`.
- **The confirmation mechanism works.** LangChain's `HumanInTheLoopMiddleware` paused before the gated call, showed the exact arguments (`create_folder {'path': 'Project Aura'}`), and resumed on approval.
  - **On rejection,** Aura reported honestly in 3/3 runs that the action was declined.
  - **The rejection replies were wordy** and offered to retry, so the rejection message given to the model will say "do not retry unless asked".
- **LangChain's `DockerExecutionPolicy` was rejected.** It gives the agent a persistent shell inside one long-lived container. That is broader than Python, and state carries over between calls, which contradicts your disposable-workspace requirement. Instead, a small custom runner starts a fresh container per call.

## 1. Architecture

```
user ⇄ interface (terminal now; SwiftUI ⇄ FastAPI in Sprint 3)
          ▲ approval prompts (interrupt → decision → resume)
          ▼
   Aura agent loop (create_agent)
     middleware: persona · summarization · tool-call limit · human approval
     tools:
       list_vault, read_file ─────────────┐
       write_file, create_folder,         ├─▶ VaultGuard ─▶ ~/Documents/Aura_Vault
         delete_path  [approval required] ┘
       web_search ─────────────────────────▶ Tavily API (fallback: DuckDuckGo)
       run_python ─────────────────────────▶ docker run --rm (fresh container per call)
     audit log ────────────────────────────▶ ~/Library/Logs/Aura/actions.jsonl
```

## 2. File_System_Tool

There are five small tools rather than one tool with an action parameter. This keeps each schema simple for a local model and lets each tool carry its own approval rule.

| Tool | Effect | Approval |
|---|---|---|
| `list_vault(path=".")` | List entries with sizes and types | — |
| `read_file(path)` | Return text content, capped at 12k chars, with a truncation notice | — |
| `write_file(path, content, overwrite=False)` | Create a file. Replacing an existing file requires `overwrite=True` | **Yes** |
| `create_folder(path)` | Create a folder, including missing parent folders | **Yes** |
| `delete_path(path)` | Delete a file or an *empty* folder. Never recursive, and never the vault root | **Yes** |

Creating a folder is a write, so your "any write/delete" rule is applied literally.

### Path whitelist (`VaultGuard.resolve`), applied to every file tool

1. Paths are relative to the vault root. An absolute path is accepted only if it falls inside the vault. `~` and environment variables are not expanded.
2. `Path.resolve()` collapses `..` and follows symlinks. The result must satisfy `is_relative_to(vault_root.resolve())`. This blocks traversal (`../../.ssh`) and symlinks that point outside the vault.
3. A blocked path returns an error that states the rule, so the model can correct itself, and the attempt is written to the audit log.
4. Aura has no tool that creates symlinks.
5. **Residual risk:** another local process could swap a path for a symlink between the check and the use. That requires a hostile process already running on the Mac, so it is out of scope.

### Vault

- **Location:** `settings.vault_dir` defaults to `~/Documents/Aura_Vault`, and `AURA_VAULT_DIR` overrides it.
- **Creation:** Aura creates the folder at startup if it does not exist.
- **Tests:** tests always use a temporary vault, never the real one.

### Confirmation Step

- **Mechanism.** `HumanInTheLoopMiddleware` is configured with `interrupt_on` set for `write_file`, `create_folder` and `delete_path`. The allowed decisions are approve and reject.
- **What the prompt shows.** The action and the vault-relative path. Writes add the size, whether a file will be overwritten, and the first lines of the content. Deletes show what will be removed.
- **Terminal rendering.** The default answer is **No**:
  ```
    ⚠ Aura wants to create folder: Project Aura/
      Allow? [y/N]
  ```
- **On rejection.** The model is told "The user declined this action. Do not retry unless they ask." Nothing touches the disk.
- **Audit log.** One JSON line per approval decision, per executed write or delete, per `run_python` run, and per blocked path. It is stored at `~/Library/Logs/Aura/actions.jsonl`, outside the vault, so the agent cannot edit or delete it.
- **Sprint 3 compatibility.** The pause is saved in the agent's checkpointer. FastAPI can return a `pending_approval` event, SwiftUI shows Allow/Deny, and a POST resumes the run. No redesign is needed.

## 3. Web_Search_Tool

- **Tool:** `web_search(query, max_results=5)`.
- **Primary backend: Tavily** (`TAVILY_API_KEY`). It returns page extracts built for LLMs, which matters with a 16k-token window.
- **Fallback: DuckDuckGo.** It needs no key, so Aura can search before a Tavily key exists. It returns snippets only and is rate-limited. The backend is chosen at startup, depending on whether the key is present.
- **Output.** Numbered results, each with title, URL and content trimmed to about 600 chars. The total is capped at about 4k chars, roughly 1k tokens.
- **Persona.** Aura uses search for anything time-sensitive and cites URLs.
- **Privacy.** Only the query leaves the Mac. Search is read-only, so it needs no approval.

## 4. Code_Interpreter_Tool

`run_python(code)` starts one fresh container per call. The code is passed on stdin, which avoids argument-length limits and keeps it out of `ps`.

```
docker run --rm -i --name aura-run-<uuid>
  --network none                           # no internet, no LAN
  --read-only --tmpfs /tmp:size=64m        # immutable root filesystem, scratch space only
  --memory 512m --memory-swap 512m --cpus 1 --pids-limit 64
  --cap-drop ALL --security-opt no-new-privileges
  --user 65534:65534                       # "nobody"
  -v ~/Documents/Aura_Vault:/vault:ro      # ← decision requested (§9)
  aura-sandbox:1 python -I -
```

- **Timeout.** After 30 s of wall-clock time the runner calls `docker kill aura-run-<uuid>` and reports a timeout to the model. A live test verifies that no container is left behind.
- **Output.** stdout, stderr and the exit code, capped at 8k chars.
- **Image.** `aura-sandbox:1` is built from `sandbox/Dockerfile`: `python:3.12-slim` plus pinned `numpy` and `pandas`. It is built once, on first use.
- **Docker not running.** The tool returns "The code sandbox is unavailable because Docker isn't running", and Aura passes that on. Aura does not start Docker itself.
- **Approval.** None, because the code runs isolated and offline. Every run is still logged.
- **Memory.** The Docker Desktop VM shares 24 GB of unified memory with the 16 GB model, and only 12% was free with the model loaded. I recommend capping the VM at about 4 GB in Docker Desktop (Settings → Resources). I will measure the real footprint and per-call start latency during implementation.

## 5. Agent loop changes

- **Tools.** All seven tools are registered in `create_agent`.
- **Loop limit.** `ToolCallLimitMiddleware` allows 8 tool calls per user turn, which stops runaway loops; Aura then says it stopped.
- **Context budget.** Tool outputs are capped as listed above. Tool schemas add tokens to every call, and the Sprint 1 fix (counting the server's real token usage) means compaction already accounts for them. Compaction keeps each tool call paired with its result.
- **Persona additions.**
  - Never claim an action succeeded unless the tool result confirms it.
  - Vault paths are relative to the vault root.
  - Cite sources.
- **Interface API.** `Aura.stream()` now yields events: text chunks or an `ApprovalRequest`. `Aura.resume(decisions)` continues the run. The terminal now and FastAPI in Sprint 3 both use this same API.

## 6. New configuration

`AURA_VAULT_DIR`, `TAVILY_API_KEY`, `AURA_SANDBOX_IMAGE`, `AURA_SANDBOX_TIMEOUT`, `AURA_AUDIT_LOG`, all in `config.py` and `.env.example`.

## 7. Files

```
src/aura/tools/__init__.py   tool registry and approval rules
src/aura/tools/vault.py      VaultGuard and the five file tools
src/aura/tools/web.py        Tavily / DuckDuckGo search
src/aura/tools/sandbox.py    Docker runner
src/aura/audit.py            JSONL audit log
sandbox/Dockerfile           aura-sandbox image
brain.py / main.py / persona.py / config.py   updated as described above
```

## 8. Test plan (acceptance = your original Sprint 2 criteria)

**Offline (fast, deterministic):**
- **VaultGuard refusals.** Each of these is refused and logged: `../` traversal, an absolute path outside the vault, a symlink pointing outside, a literal `~`, deleting the vault root, overwriting without the flag, and recursive delete.
- **Approval.** Gated tools pause. Reject means nothing is written; approve means the change is written. Audit entries are correct either way.
- **Sandbox.** The `docker run` command contains every hardening flag. Output is truncated correctly, and the timeout path works with Docker mocked.

**Live (skipped if Docker, Ollama or the network is unavailable):**
- **Sandbox breakout attempts.** Opening a socket fails. Writing to `/` fails. Writing to `/vault` fails. An infinite loop is killed at the timeout, and `docker ps -a` shows no leftover container.
- **Agent.**
  - "What is the weather in London?" calls `web_search`, and the reply cites a URL.
  - "Create a folder named 'Project Aura'" pauses for approval, and the folder exists in the temporary vault afterwards.
  - The Fibonacci question uses `run_python`.

## 9. Decisions requested

1. **Mount the vault read-only at `/vault` inside the sandbox?** *Recommended: yes.* It enables "analyse the CSV in my vault", the Sprint 4 file analysis. It grants nothing new, because `read_file` can already read the vault, and with `--network none` no data can leave. Writes from code into the vault stay impossible, because they would bypass the Confirmation Step.
2. **Preinstall `numpy` and `pandas` in the sandbox image?** *Recommended: yes.* The cost is about 150 MB of image. Without them, data analysis is limited to the standard library.
3. **Use DuckDuckGo as a fallback when no Tavily key is set?** *Recommended: yes*, so that search works on day one.

## 10. User actions before implementation

- Get a Tavily API key and put it in `.env` as `TAVILY_API_KEY`. This is optional if the fallback is approved.
- Start Docker Desktop and cap its memory (Settings → Resources) at about 4 GB.
- On first run, macOS will ask whether Terminal may access the Documents folder. Allow it.
- If iCloud "Desktop & Documents" sync is on, the vault will sync to iCloud. To avoid that, set `AURA_VAULT_DIR` to another location.
- Start the Xcode download, per Gemma's instruction.
