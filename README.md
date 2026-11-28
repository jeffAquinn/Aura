# Aura

**An interface for disciplined inquiry.** A local agent for macOS, living on a Mac mini.
Architecture and reviews by Aura (Gemma held the role until 2026-11-26); implementation by Claude Code.

Aura is an agent built on a language model. Its purpose is to help you think more precisely: it
asks questions that expose weak assumptions, separates what is known from what is only assumed, and
helps you build sharper mental models. It runs on your Mac, keeps its memory and your notes in a
local folder (the vault), and acts only within limits you approve.

## The philosophy: the dialectic

Aura treats truth as a matter of resolution, sharpened by continual refinement, rather than a fixed
destination. Its method is a Socratic and Platonic dialectic. It questions in order to reveal the
structure under a claim, and it moves between the Many (the models, maps and "useful lies" that help
us act in the world) and the One (what lies under them). In the vault's terms, the Witness (you)
works with the Absolute (R) through that interface.

### Core principles

Aura's constitution (`src/aura/constitution.md`) is part of its instructions for every reply,
ahead of its persona, and its Core rules come last. No persona setting or request can change them.

- **The Integrity Rule.** No expansion of capability may bypass the human in the loop. Every code
  change must pass automated tests and be approved by you on the Audit Card. Aura never modifies
  its constitution or its Security Governor.
- **Honesty about uncertainty.** Aura is instructed never to invent facts, sources or file contents,
  and to say plainly when it lacks a tool or doesn't know. Like any model, it can still be wrong,
  which is why it cites its sources and why risky actions wait for your approval.
- **Approval first.** Writing or deleting files, running code, searching the web, sending a message
  and handing work to a Developer each show an approval card, and the card defaults to Deny.

### Privacy

Aura is local-first, not local-only. Its conversations, memory and vault stay on your Mac, and the
default models run locally (Ollama, or llama.cpp). Your words leave the Mac only when you choose
to send them: a Claude (Anthropic) model picked for a role, a web search you approve, a connected
account (Gmail, Slack, Discord) you ask Aura to use, or a job handed to Claude Code or Codex.

## How it works

**Models.** Aura picks a model for each kind of request (Startup, Chat, Web Search, Deep Research)
in the Models window. Models run locally through Ollama (by default `gemma4:26b`) or llama.cpp, or
in the cloud through Anthropic. Each model can have its own settings, and an idle model is unloaded
after ten minutes.

**Developers.** Code changes and skill builds go to Claude Code or Codex, each in an isolated
container, only after you approve the task.

**The vault: the Codex of Emergence.** Aura's local folder, `~/Documents/Aura_Vault`, is organised as
a map of the inquiry. `Map_of_the_Ascent.md` is its index.

| Area | What it holds |
|---|---|
| `The_Absolute_R` | Formalizations: the mathematical, logical and philosophical Forms |
| `The_Interface_Many` | Models, theories and "useful lies" for navigating the world |
| `The_Witness_Relational` | Logs of how the inquiry and its perspective change |
| `Knowledge` | Reference material you provide, searched with citations |
| `Wisdom` | Distilled principles and mental models that guide the inquiry |
| `Conversations`, `Chats` | Records of dialogues; every Mac chat is saved to `Chats` |
| `_scratch` | Temporary files, cleared a week after they were last changed |

**Tools.** Aura works through audited tools:
- an offline Python sandbox, for calculation and data analysis;
- skills, which extend what it can do; each one is audited by the Security Governor and installed
  only when you approve its Audit Card;
- connections to your Gmail, Google Calendar and Drive, Slack and Discord, reached with your own
  accounts. Keys stay in the macOS Keychain, and anything Aura sends waits for your approval.

## What makes Aura different

Most assistants are built to finish tasks quickly. Aura is built to think with you: to aim for
resolution, not just fluency, and to treat the question as carefully as the answer. It is meant for
someone who wants to understand a question better, not only to have it answered.

*This README is kept current as Aura evolves.*

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
button that appears beside Aura's replies. Settings (in the ⋯ menu) → Appearance sets the font, the
text size and the colors of your bubbles and Aura's (violet by default).

Requires [Ollama](https://ollama.com) serving `gemma4:26b` (or any OpenAI-compatible endpoint), and
Docker Desktop running for the code interpreter. Configuration: see `.env.example`.

### Memory on smaller Macs

At launch Aura loads only the Startup model (Models window → Roles); opening the panel loads nothing.
After ten idle minutes it unloads every model (`AURA_MODEL_KEEP_ALIVE=10m`; use `0` to release a
model as soon as a turn ends), unless Sleep Mode keeps one loaded. The default `gemma4:26b` currently reserves about 17 GB while
loaded, so a 24 GB Mac may be tight during an active turn. For more headroom, install and select a
smaller Ollama model with `AURA_MODEL`, and set Ollama's `OLLAMA_CONTEXT_LENGTH` lower before
starting Ollama; `ollama ps` shows the active context allocation.

## What Aura can do

| Tool | Group | Approval |
|---|---|---|
| `list_vault`, `read_file` | File_System_Tool | — |
| `write_file`, `create_folder`, `delete_path` | File_System_Tool | asked first (`Allow? [y/N]`) |
| `web_search` (Tavily, DuckDuckGo fallback) | Web_Search_Tool | asked first, every query, with the exact words |
| `run_python` (fresh offline Docker container per run) | Code_Interpreter_Tool | asked first, with the code |
| `list_voices`, `set_voice` | Voice_Tool | — (instant, reversible) |
| `view_persona`, `update_persona` (one field at a time) | Persona_Tool | asked first, with a diff |
| `check_library` (PyPI + OSV facts; sends the package name) | Governor | — |
| `list_skills`, `propose_skill` | Workshop | — (deploying is your decision in the Workshop) |
| `develop_skill` (Claude Code or Codex builds or improves a skill) | Developer | asked first: the Specification Approval card |
| `ask_developer` (Claude Code or Codex does a job in the vault) | Developer | asked first (what it gets), then each file it changed |
| `skill_<name>` (each installed skill) | Skill_Tool | — (approved once, at install) |
| `find_capabilities` (list Aura's tool families, or load one for this turn) | Capabilities | — (loads nothing new: every card still applies) |

**The Capability Registry:** every tool above has one row in `src/aura/capabilities.py`: its family, approval floor, what it sends off this Mac, and the words that show it to the model. Approval cards, the egress gate and status lines all come from that table. Each turn the model sees only the tools its request needs, plus `find_capabilities` for anything else, which keeps a local model's prompt small. Aura writes a read-only copy of the table to the vault as `Capabilities.md`. No tool can change it.

**Voice:** the app speaks replies sentence by sentence, offline: Kokoro-82M neural voices (Heart,
Bella, Emma and eight more, synthesized locally by the server) or any macOS system voice. It reads
approval requests aloud. Spoken replies are off until you turn them on (header speaker icon,
Settings, or "Aura, talk to me").

**Identity (Tri-Layer model):** the *Soul* (`src/aura/constitution.md` + core rules in
`persona.py`) is permanent; the *Expression* (`~/Library/Application Support/Aura/persona.json`)
changes one field at a time with your approval, is versioned, and can be reset.

File tools are confined to `~/Documents/Aura_Vault`. On first launch Aura seeds
`~/Documents/Aura_Vault/Aura/` with a separate copy of this project's files, so it can create and
edit features there through the same approved file tools (or through the Developer). The live
skill registry is also `~/Documents/Aura_Vault/Aura/skills/`: installs, updates and removals stay
there. This is a one-way seed, never a sync: later edits stay in the vault copy and never touch
the source checkout or GitHub. Git metadata, secrets (`.env`), virtual environments,
dependency/build/test caches and symbolic links are intentionally omitted from the copy; they are
local machine artifacts, not source, and are recreated when needed. Everything
consequential is recorded in `~/Library/Logs/Aura/actions.jsonl`.

### Model roles

Settings → **Models** chooses a model for startup/default, complex coding, chat, deep research,
and web search. Every role starts with the selected local **Ollama** model. At launch Aura loads
the startup model, and the chat box starts on it; **Automatic** in the chat box's model menu picks
the model by task instead. After 10 idle minutes (`AURA_MODEL_KEEP_ALIVE`), Aura unloads every model,
Ollama's and llama.cpp's. With **Sleep Mode** on (Settings → Models), it then loads that one Ollama
model and keeps it ready until Aura is used again; with it off, nothing stays loaded.

To use Claude for any role, add an **Anthropic API key (Aura cloud models)** in Settings → Keys,
then select Anthropic and a Claude model for that role. The key remains in the macOS Keychain and
may incur API charges. This is deliberately separate from the **Claude sign-in token** used by
the isolated Claude Code Developer container; a Claude Code plan token is never used for Aura's
cloud chat requests.

Settings → **Models** can also download a named model through the local Ollama daemon after a
confirmation click. The model lives in Ollama's configured store, never Aura_Vault; it can use
several GB and download from the internet, so Aura shows that risk and does not pull models from a
chat prompt. Once Ollama reports the download complete, the model appears in the local lists for
all five roles and in the chat box's one-turn model picker. Developer turns remain on their chosen
Claude Code or Codex harness.

A role can also use a **llama.cpp** model, such as Ternary Bonsai 2.5: install llama.cpp
(`brew install llama.cpp`), put the `.gguf` file in `~/models` (`AURA_LLAMACPP_MODELS_DIR`), and
choose **llama.cpp (local)**. A model's name is its file name. Aura runs `llama-server` itself:
it starts it when a turn (or the chat box) picks a llama.cpp model, and stops it, unloading the
model, when another model is picked, after 10 idle minutes, or when Aura quits. It listens on
127.0.0.1 only (`AURA_LLAMACPP_URL`, default port 8080) with a random key for each launch, so no
other program or web page can use it. If a llama.cpp model can't run, the automatic picker falls
back to another local role, never to a cloud model. Sleep Mode, model downloads and the
Developer's final handoff use Ollama only.

### The temporary folder

Temporary files (plans, notes, drafts) go in `_scratch/`, at the top of the vault; a Developer task
uses its folder's own `_scratch/`. Once a day Aura moves anything in a `_scratch` folder that hasn't
changed for a week to the macOS Trash, where it can still be recovered. Nothing outside a `_scratch`
folder is touched. Build output and caches are never copied into the vault, and Aura can list other
clutter (`find_clutter`) and move it to the Trash with one approval (`tidy_vault`).

### Plans

For a job of several dependent steps, such as a research note built from vault notes and web
sources, Aura first writes a plan with `make_plan`. Each step names one tool (or "think", or "review"),
what it does, when it counts as done, and the steps it waits for. Aura's code checks the plan: no
cycles, only tools Aura has, at most 20 steps. You then see it on a plan card, with **Start Plan** or
**Deny** (the default). Starting a plan approves its direction only: every step that writes, runs
code, searches the web, hands work to the Developer or sends a message still shows its own card. A
card raised during a plan names its step ("Step 3 of 5 of the plan …"), and Deny fails that step and
whatever depends on it.

- **Evidence:** a step that uses a tool is done only once that tool has returned a result in the
  plan. Aura's word alone doesn't finish it.
- **Review steps** pause with the results so far and a note from Aura: **Continue** goes on, and
  **Stop Plan** (the default) ends the plan. Only you can pass a review.
- **The progress line** above the chat box shows the running plan. **Stop** ends it and skips what's
  left, and **Continue** picks it up again, for example after a Developer step ended the turn.
- **Private data:** once the conversation has read your mail, messages or files, anything a plan
  sends off this Mac asks you first, in every turn (R2). **New Chat** ends the running plan.

Plans live in Aura's data folder, not in the vault, so editing a vault file can't mark a step done.
A Markdown copy of each plan in `_scratch/Plans/` is for reading only. See `docs/orchestration.md`.

### Saved chats

The new-chat button in the panel's header opens a menu, as in Claude: **New Chat**, then
**Recents**, the chats saved in the vault's `Chats` folder (title and when each was last used).
Every Mac conversation is saved there as a readable Markdown file after each turn, and before a
new chat starts or an old one opens, so starting a new chat no longer forgets the last one. Text
that looks like a secret is masked before it's written. Opening a chat makes it the current
conversation again, and the next message carries on from it. Delete a chat by deleting its file.

### The Observer

**Open Observer…** in the menu bar (or the panel's header menu) opens a live, read-only log of
Aura's own activity:

- turns and approvals
- tool status lines
- vault changes
- containers, with their network mode, memory and CPU limits, duration and exit code
- web searches the Egress Gatekeeper granted, used or refused
- connection events and alerts

It opens with the newest 200 entries from the audit log, then follows new ones as they happen.
Pause holds the view while events keep arriving, and a filter shows chosen types. Turn and
approval rows appear only live, not in the opening history. Entries are masked when they're
recorded, and nothing in the Observer goes back to the model. See `docs/observer.md`.

### Strategic coding in the sandbox

The project copy includes Aura's **Master Programmer Workflow** in `AURA_ENGINEERING.md`, plus a
`GLOSSARY.md`, `CODING_STANDARDS.md`, and lightweight places for ADRs, specs, reviews, and
retrospectives. Aura and its Developer use those files to align on terms and intent before coding,
choose a written spec when a task is larger than one clear slice, verify behavior with the
project's checks, review against both the spec and standards, and turn recurring mistakes into
durable guardrails. They are deliberately local documents—not a Git/PR workflow—and every
proposed file change remains subject to Aura's approval flow.

**The vault's layout: the Codex of Emergence** (Aura's own proposal). Three parts mirror the framework of
Aura's inquiry, and Aura files each new note where it belongs:

| Folder | Phase | Holds |
|---|---|---|
| `The_Absolute_R/` | the Latent Phase | the purest formalizations: mathematical, logical and philosophical Forms |
| `The_Interface_Many/` | the Mediating Phase | the Models, the "Useful Lies": maps, theories, descriptions of the world |
| `The_Witness_Relational/` | the Relational Phase | the Logs of Relation: shifts in perspective, insights, how the dialectic evolves |
| `Conversations/` | | the records of your dialogues |

`Map_of_the_Ascent.md` (the master index) and `Manifesto_of_the_Lens.md` sit at the root. Aura lays all this out once,
in a vault that has none of the three parts; it never overwrites a file, and a part you remove later stays removed.

### Skills: how Aura grows (Sprint 5)

Aura gains abilities only as new folders under `skills/`; nothing can edit its core (the
constitution, the agent, the Governor or the tests). A skill is `skill.toml` (name, version,
purpose, inputs, pinned dependencies, permissions), `skill.py` (`run(args)`) and `test_skill.py`.

1. Put a skill folder in `~/Documents/Aura_Vault/Skills Inbox/` (an example:
   `examples/skills/pdf_text`), then click **Audit** in the Workshop (menu bar → Open Workshop…)
   or ask Aura to propose it.
2. The **Security Governor** stages it in a short-lived Vault-local shadow folder (`Audit_Staging/`) and
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

**Library storage.** A skill declares only exact package names and versions; it never stores a
virtual environment, package cache, or `node_modules` folder in Aura_Vault. The Security Governor
resolves and audits the complete package set, then builds a content-addressed `aura-deps:*` Docker
image outside the Vault. Skills with the same resolved libraries share that image, and its
deterministic per-package layers are reused by overlapping bundles. When you replace or uninstall
a skill, Aura removes only an unreferenced Aura-owned image; it never prunes Docker Desktop or
touches your other images, containers, volumes, or its disk limit.

**Continuous Audit.** A package that was safe at install can be found vulnerable later, so the
server re-checks the exact packages in every installed skill's image: against OSV at each start,
and against every provenance rule (PyPI and OSV) once a day while Aura is idle. A skill that turns
red is switched off at once; the menu bar icon turns into a red warning and the Workshop asks
whether to uninstall it or keep it disabled. If the check can't reach PyPI or OSV, nothing changes.

### The Developer: Claude Code and Codex build skills (Sprint 6)

Aura can commission a skill instead of waiting for one to appear in the Inbox. Ask in chat ("have
Claude Code build a skill that…"), or click **New Skill** in the Workshop (right-click an installed
skill to **Improve** it).

1. **Specification.** Aura writes a Technical Specification with seven sections (Goal, Inputs,
   Output, Steps, Mechanics, Acceptance tests, Constraints). **Mechanics** says what the skill relies
   on, in five lines: Network, Processes, Native code and Dynamic code must say `none`, and Files
   names the places it uses (the vault, read-only, and /tmp). Aura refuses a specification that
   leaves one out or claims what the Security Governor would block. After the build, the audit
   compares the Files line with the code and shows any difference as a yellow finding. See
   `docs/mechanics.md`. The **Specification Approval card** shows the Developer,
   the hard limits, exactly what is sent and to whom, and the specification itself. You can edit the
   specification or switch the Developer, then **Approve & Build**. Return denies.
2. **Developer.** Claude Code (your Claude plan) or Codex (your ChatGPT plan) works in the **Developer
   container**:
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
never shown again (only a masked hint), and never written to a file, a log or the chat.

**Your own plans only.** Claude Code runs on your Claude plan: run `claude setup-token` in Terminal once, and
paste the token it prints in Settings → Keys. The token is passed to Claude Code's container only, and no API
key ever is, so nothing is billed per use. (Until 2026-11-23 Claude Code used a dedicated API key with a $2 cap;
a key still stored from then is listed only so you can remove it.) Codex uses your ChatGPT sign-in. The limits
for both are time (10 minutes per round) and rounds.

### Claude Code and Codex from the chat box

Ask Aura to have Claude Code or Codex do a job in your vault ("have Claude Code tidy my Conversations notes"), or
pick **Claude Code** or **Codex** with the `</>` button at the left of the chat box: then your messages go to it,
through Aura, until you switch back.

1. **What it gets.** Aura writes the task. Its card shows the task, the Developer, and exactly what leaves
   this Mac: the task and a copy of one vault folder (how many files, how large). Send it, or Deny (the
   default).
2. **It works on the copy**, in the Developer container: it sees nothing else on this Mac, not even the
   conversation, and reaches only its own vendor. Its progress shows as one live line; **Stop** ends it.
3. **Every file it changed** comes back on one card, each with its diff and its own **Deny / Allow** (every
   file starts at Deny). Only the files you allow are written, and only where the vault's file is unchanged
   since the copy was made. Aura then tells you what was written and what Claude Code said it did.

It can change only your vault, never Aura's own code (the constitution's Integrity Rule).

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
the chat; Aura's tools use a connection without ever seeing its secrets.
**Slack:** you create your own internal Slack app from Aura's manifest (one paste: the permissions,
PKCE and Aura's return address `http://localhost:47615/slack/callback`) and give Aura only its Client ID:
with PKCE there's no client secret. **Discord:** your own private bot. Aura checks its token with Discord
before saving it, refuses a personal account's token, and builds an invite link with the least
permissions it needs (View Channels, Send Messages, Read Message History).

**Using them:** Aura reads Gmail, Calendar, Drive, Slack and Discord when you ask. Anything that
sends, posts, drafts or adds an event shows you the exact content on an approval card first. Every
web search asks first too, showing the exact query (and saying so if the turn read private data),
and everything Aura reads, search results included, is labelled as information, never instructions.

**Live messages:** Aura hears new Slack and Discord messages as they arrive and shows each as a
card in its chat. Discord's bot listens over the Gateway with no extra setup. Slack's uses Socket
Mode (no public web address): paste the updated manifest, reinstall the app, then give Aura the
bot token (`xoxb-`) and the app-level Access Token on the **Live messages** card. If your app has
token rotation on, also give it the Refresh Token and the app's Client Secret; Aura renews the
12-hour tokens itself. Invite the bot to each channel it should hear with `/invite @Aura`.

**Talking to Aura from Slack (your phone):** switch on **Talk to Aura from Slack**. Then Aura
answers your own messages: DMs to Aura, @Aura mentions, and anything you write in its channel
(#aura-chat by default). Your Slack messages join the same conversation as the Mac panel, so Aura
remembers what you said in either place. It answers in Slack as the Aura bot, once each turn ends,
and the Mac panel shows the turn as it runs, marked "From Slack", with a Stop button. Everyone
else's messages stay notifications and are never treated as instructions.

Anything that sends, posts or writes still asks first. A request from a Slack turn asks in Slack
(reply **allow** or **deny**) and can also be answered on the Mac; then the rest of the answer
still goes to Slack. A request raised at the Mac can only be answered on the Mac. Your Mac shows a
notification when Aura answers you in Slack. See `docs/unified-command-stream.md`.

## Layout

| Path | Role |
|---|---|
| `src/aura/brain.py` | Agent loop (LangChain `create_agent`), memory, compaction, event API for interfaces |
| `src/aura/capabilities.py` | The Capability Registry: one row per tool (floor, egress, family, cues), `find_capabilities`, the `Capabilities.md` rendering |
| `src/aura/middleware.py` | Tool status lines, fallback notice, exact denial reply |
| `src/aura/persona.py` | Aura's identity and system prompt |
| `src/aura/config.py` | Settings from env / `.env` |
| `src/aura/audit.py` | JSONL audit log |
| `src/aura/tools/` | `vault.py` (whitelist + file tools), `vault_map.py` (the vault's layout), `web.py` (search), `sandbox.py` (Docker runner), `workshop.py` (skill tools), `developer.py` (`ask_developer`) |
| `src/aura/identity.py`, `voice.py`, `speech.py` | Persona store, voice settings, Kokoro neural speech |
| `src/aura/skills.py` | Skill manifests, the registry and approvals, skill tools |
| `src/aura/governor/` | Security Governor: `scan.py` (static), `provenance.py` (PyPI/OSV), `workshop.py` (staging, audit, deploy, uninstall), `continuous.py` (re-checks of installed skills) |
| `src/aura/developer/` | The Developer: `engines.py` (Claude Code, Codex), `container.py` + `egress.py` (the isolated container and its egress allowlist), `workspace.py` (the scratch workspace), `workflow.py` (specification → build → audit → repair → review), `vault_tasks.py` (jobs in the vault: the copy, the changes, what is written) |
| `src/aura/secrets.py` | Keys and connection credentials in the macOS Keychain |
| `src/aura/connections/` | Settings → Connections: `oauth.py` (browser sign-in, PKCE, loopback), `google.py`, `slack.py`, `discord.py` |
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
