# Aura

A local AI agent living on a Mac mini. Architecture and reviews by Gemma; implementation by Claude Code.

## Run

```sh
uv run aura                 # chat in the terminal (/new = fresh conversation, /exit = quit)
uv run pytest               # all tests; live ones skip if Ollama / Docker / network are absent
```

Requires [Ollama](https://ollama.com) serving `gemma4:26b` (or any OpenAI-compatible endpoint), and
Docker Desktop running for the code interpreter. Configuration: see `.env.example`.

## What Aura can do

| Tool | Group | Approval |
|---|---|---|
| `list_vault`, `read_file` | File_System_Tool | — |
| `write_file`, `create_folder`, `delete_path` | File_System_Tool | asked first (`Allow? [y/N]`) |
| `web_search` (Tavily, DuckDuckGo fallback) | Web_Search_Tool | — |
| `run_python` (fresh offline Docker container per run) | Code_Interpreter_Tool | — |

File tools are confined to `~/Documents/Aura_Vault`. Everything consequential is recorded in
`~/Library/Logs/Aura/actions.jsonl`.

## Layout

| Path | Role |
|---|---|
| `src/aura/brain.py` | Agent loop (LangChain `create_agent`), memory, compaction, event API for interfaces |
| `src/aura/middleware.py` | Tool status lines, fallback notice, exact denial reply |
| `src/aura/persona.py` | Aura's identity and system prompt |
| `src/aura/config.py` | Settings from env / `.env` |
| `src/aura/audit.py` | JSONL audit log |
| `src/aura/tools/` | `vault.py` (whitelist + file tools), `web.py` (search), `sandbox.py` (Docker runner) |
| `src/aura/main.py` | Terminal interface |
| `docs/sprint-2-design.md` | Sprint 2 design, as approved by Gemma |

## Roadmap

- [x] Sprint 1: Core engine (brain, persona, memory, compaction, streaming, error handling)
- [x] Sprint 2: Tools (vault with whitelist + approvals, Tavily search with DuckDuckGo fallback, Docker sandbox) and high-fidelity memory retention
- [ ] Sprint 3: SwiftUI menu bar app + FastAPI local "Aura Server"
- [ ] Sprint 4: UX refinement (Markdown rendering, persona polish)
