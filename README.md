# Aura

A local, agentic AI system for macOS, living on a Mac mini.
Architecture and reviews by Gemma; implementation by Claude Code.

## Run

```sh
uv run aura                 # chat in the terminal (/new = fresh conversation, /exit = quit)
uv run pytest               # all tests; live ones skip if Ollama / Docker / network are absent
```

### The menu bar app

```sh
scripts/build-app.sh        # builds app/build/Aura.app (needs Xcode 26+)
open app/build/Aura.app     # sparkle icon in the menu bar; ⌥Space opens the panel
```

The app launches its own local server (`uv run aura-server`, 127.0.0.1 only, per-launch token)
and stops it when it quits. `cd app && swift test` runs the client's unit tests.

**In the panel:** drag the header to move it. Drag over any message to select text; right-click
a message for **Copy Message** or **Copy Conversation** (copied as plain text), or use the copy
button that appears beside Aura's replies. Settings (the gear) → Appearance sets the font, the
text size and the colors of your bubbles and Aura's (violet by default).

Requires [Ollama](https://ollama.com) serving `gemma4:26b` (or any OpenAI-compatible endpoint), and
Docker Desktop running for the code interpreter. Configuration: see `.env.example`.

## What Aura can do

| Tool | Group | Approval |
|---|---|---|
| `list_vault`, `read_file` | File_System_Tool | — |
| `write_file`, `create_folder`, `delete_path` | File_System_Tool | asked first (`Allow? [y/N]`) |
| `web_search` (Tavily, DuckDuckGo fallback) | Web_Search_Tool | — |
| `run_python` (fresh offline Docker container per run) | Code_Interpreter_Tool | asked first, with the code |
| `list_voices`, `set_voice` | Voice_Tool | — (instant, reversible) |
| `view_persona`, `update_persona` (one field at a time) | Persona_Tool | asked first, with a diff |
| `check_library` (PyPI + OSV facts) | Governor | — |
| `list_skills`, `propose_skill` | Workshop | — (deploying is your decision in the Workshop) |
| `develop_skill` (Claude Code or Codex builds or improves a skill) | Developer | asked first: the Specification Approval card |
| `skill_<name>` (each installed skill) | Skill_Tool | — (approved once, at install) |

**Voice:** the app speaks replies sentence by sentence, offline: Kokoro-82M neural voices (Heart,
Bella, Emma and eight more, synthesized locally by the server) or any macOS system voice. It reads
approval requests aloud. Spoken replies are off until you turn them on (header speaker icon,
Settings, or "Aura, talk to me").

**Identity (Tri-Layer model):** the *Soul* (`src/aura/constitution.md` + core rules in
`persona.py`) is permanent; the *Expression* (`~/Library/Application Support/Aura/persona.json`)
changes one field at a time with your approval, is versioned, and can be reset.

File tools are confined to `~/Documents/Aura_Vault`. Everything consequential is recorded in
`~/Library/Logs/Aura/actions.jsonl`.

### Skills: how Aura grows (Sprint 5)

Aura gains abilities only as new folders under `skills/`; nothing can edit its core (the
constitution, the agent, the Governor or the tests). A skill is `skill.toml` (name, version,
purpose, inputs, pinned dependencies, permissions), `skill.py` (`run(args)`) and `test_skill.py`.

1. Put a skill folder in `~/Documents/Aura_Vault/Skills Inbox/` (an example:
   `examples/skills/pdf_text`), then click **Audit** in the Workshop (menu bar → Open Workshop…)
   or ask Aura to propose it.
2. The **Security Governor** stages it in a shadow project (a git worktree in `.aura-staging/`) and
   checks it by rules alone, never by asking the model: changes only inside `skills/<name>/`, no
   protected file touched, a valid manifest, a static scan (no network, system or dynamic-code
   use), and every dependency (direct and indirect) on PyPI with a ready-built package, no known
   vulnerabilities (OSV), and no lookalike name. It then builds the skill's sandbox image, runs the
   skill's tests inside it, and runs Aura's own suite in the shadow project.
3. The **Audit Card** shows a green / yellow / red risk badge, what the rules verified, what the
   skill merely claims, the test results and a side-by-side diff. Red, failing or missing tests
   block deployment. Return rejects; deploying takes a click on **Approve & Deploy**.
4. Deploying copies the folder into `skills/`, makes a local commit (never pushed), records the
   approval by content hash outside the repo, and loads the tool without a restart. Any later
   edit to the folder unloads it until it's audited again.

Skills always run in their own offline container, with the vault read-only if the manifest asks
for it. Network-enabled skills aren't accepted yet.

**Continuous Audit.** A package that was safe at install can be found vulnerable later, so the
server re-checks the exact packages in every installed skill's image: against OSV at each start,
and against every provenance rule (PyPI and OSV) once a day while Aura is idle. A skill that turns
red is switched off at once; the menu bar icon turns into a red warning and the Workshop asks
whether to uninstall it or keep it disabled. If the check can't reach PyPI or OSV, nothing changes.

### The Developer: Claude Code and Codex build skills (Sprint 6)

Aura can commission a skill instead of waiting for one to appear in the Inbox. Ask in chat ("have
Claude Code build a skill that…"), or click **New Skill** in the Workshop (right-click an installed
skill to **Improve** it).

1. **Specification.** Aura writes a Technical Specification with six sections (Goal, Inputs, Output,
   Steps, Acceptance tests, Constraints). The **Specification Approval card** shows the Developer,
   the hard limits, exactly what is sent and to whom, and the specification itself. You can edit the
   specification or switch the Developer, then **Approve & Build**. Return denies.
2. **Developer.** Claude Code (a dedicated Anthropic API key, `--bare`, $2 hard cap per build) or Codex
   (your ChatGPT sign-in) works in the **Developer container**:
   - **Files:** it sees the build's scratch workspace and nothing else on this Mac.
   - **Network:** its only route out is an allowlist of its own vendor's hosts.
   - **Feed:** its actions stream into the build's live feed in the Workshop, where **Stop** ends it at once.
3. **Security Governor.** Only the written `skill/` folder is taken and audited exactly as in Sprint 5:
   - the shadow project, the static scan and provenance;
   - the skill's tests in its sandbox, and Aura's full suite.

   A failing round is wiped and its findings go back to the Developer, for at most 2 repair rounds.
4. **Code Review.** Only a build that passed every check reaches the Audit Card, marked **Built by**
   (the Developer, the round, the cost) with its specification. Deploying is still your click. When a
   build is ready, the menu bar says so and, if spoken replies are on, Aura tells you.

**Keys** live only in the macOS Keychain: Settings (the gear in the chat header) → Keys. A key is
never shown again (only a masked hint), and never written to a file, a log or the chat. The
Anthropic key is passed to Claude Code's container only.

### Connections (Sprint 7, in progress)

Settings (the gear) → Connections, or **Connections…** in the menu bar menu, sets up Aura's links to
your accounts, one at a time. **Google** (Gmail, Calendar, Drive) is first:
1. **Create a Google OAuth client** in your own Google Cloud project. The window walks you through it:
   audience "Personal", type "Desktop app".
2. **Save its ID and secret** to the Keychain.
3. **Choose the services and sign in** in your browser. Sign-in is OAuth with PKCE, through a one-time
   listener on 127.0.0.1.

Aura asks Google only for the services you choose. Its access to Gmail is read plus drafts; it
never sends mail. Everything is stored in the Keychain: never in the project folder, a file, a log or
the chat. No tool of Aura's can reach a connection yet: that arrives with the Permissions screen.
Slack and Discord come next.

## Layout

| Path | Role |
|---|---|
| `src/aura/brain.py` | Agent loop (LangChain `create_agent`), memory, compaction, event API for interfaces |
| `src/aura/middleware.py` | Tool status lines, fallback notice, exact denial reply |
| `src/aura/persona.py` | Aura's identity and system prompt |
| `src/aura/config.py` | Settings from env / `.env` |
| `src/aura/audit.py` | JSONL audit log |
| `src/aura/tools/` | `vault.py` (whitelist + file tools), `web.py` (search), `sandbox.py` (Docker runner), `workshop.py` (skill tools) |
| `src/aura/identity.py`, `voice.py`, `speech.py` | Persona store, voice settings, Kokoro neural speech |
| `src/aura/skills.py` | Skill manifests, the registry and approvals, skill tools |
| `src/aura/governor/` | Security Governor: `scan.py` (static), `provenance.py` (PyPI/OSV), `workshop.py` (staging, audit, deploy, uninstall), `continuous.py` (re-checks of installed skills) |
| `src/aura/developer/` | The Developer: `engines.py` (Claude Code, Codex), `container.py` + `egress.py` (the isolated container and its egress allowlist), `workspace.py` (the scratch workspace), `workflow.py` (specification → build → audit → repair → review) |
| `src/aura/secrets.py` | Keys and connection credentials in the macOS Keychain |
| `src/aura/connections/` | Settings → Connections: `oauth.py` (browser sign-in, PKCE, loopback), `google.py` (Gmail, Calendar, Drive) |
| `src/aura/main.py` | Terminal interface |
| `src/aura/server.py` | Local HTTP + SSE server for the app (auth, streaming, Smart-Warm) |
| `app/` | SwiftUI menu bar app: `AuraKit` (client, SSE, state, speech) + `AuraUI` (chat, settings, Workshop) + `Aura` (app shell) + `aura-smoke` |
| `docs/sprint-2-design.md` | Sprint 2 design, as approved by Gemma |

## Roadmap

- [x] Sprint 1: Core engine (brain, persona, memory, compaction, streaming, error handling)
- [x] Sprint 2: Tools (vault with whitelist + approvals, Tavily search with DuckDuckGo fallback, Docker sandbox) and high-fidelity memory retention
- [x] Sprint 3: SwiftUI menu bar app + FastAPI local "Aura Server", visual polish
- [x] Sprint 4: Voice and identity (TTS, voice tool, Tri-Layer persona)
- [x] Sprint 5: Skill registry, Security Governor, Workshop and staging (+ Continuous Audit)
- [ ] Sprint 6: Claude Code and Codex as developer engines (the Developer-Agent Workflow)
- [ ] Sprint 7: Connections (MCP servers in the Workshop, and a Permissions screen: files, Google account, Slack, Discord)
- [ ] Sprint 8: The Librarian (knowledge ingestion, secret vault, index)

## License

MIT. See [LICENSE](LICENSE).
