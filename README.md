# Aura

A local AI agent living on a Mac mini. Architecture and reviews by Gemma; implementation by Claude Code.

## Run

```sh
uv run aura                 # chat in the terminal (/new = fresh conversation, /exit = quit)
uv run pytest               # offline tests + a live test (skipped if Ollama is down)
```

Requires [Ollama](https://ollama.com) serving `gemma4:26b` (or any OpenAI-compatible endpoint; see `.env.example`).

## Layout

| File | Layer |
|---|---|
| `src/aura/brain.py` | Agent loop (LangChain `create_agent`), conversation memory, memory compaction |
| `src/aura/persona.py` | Aura's identity and system prompt |
| `src/aura/config.py` | Settings from env / `.env` |
| `src/aura/main.py` | Terminal interface |

## Roadmap

- [x] Sprint 1: Core engine (brain, persona, memory, compaction, streaming, error handling)
- [ ] Sprint 2: Tools (file system confined to a whitelisted vault, web search, sandboxed Python)
- [ ] Sprint 3: SwiftUI menu bar app + FastAPI local "Aura Server"
- [ ] Sprint 4: UX refinement (Markdown rendering, persona polish)
