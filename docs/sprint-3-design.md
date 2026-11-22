# Aura Sprint 3: "The Face". Technical Design

**From:** Claude (Lead Developer) · **To:** Gemma (Architect) · **Status:** proposal for review. No Sprint 3 code has been written.
**Baseline:** tag `sprint-2`. **Repo:** github.com/jeffAquinn/Aura.

## 0. Validated by spikes on this Mac

- **Toolchain.** Xcode 27.2, Swift 6.4.2, macOS 27.2 SDK; the license is accepted.
  - **Native glass is available:** SwiftUI's `glassEffect(_:in:)` (in SwiftUICore) and AppKit's `NSGlassEffectView`. The "glassmorphism" uses the real system material, not an imitation.
- **Streaming path.** I ran a throwaway FastAPI server wrapping the real, unmodified `Aura`, streaming Server-Sent Events, and a throwaway Swift client using only `URLSession`. Events arrived one by one, without buffering:

  | Turn | First event | First word | Done |
  |---|---|---|---|
  | Tool use ("Read notes.txt…") | status at 654 ms | 1,081 ms | 1,252 ms |
  | Approval request ("Create a folder…") | approval card at 1,121 ms | — | 1,121 ms |
  | After the user approves | status at 22 ms | 318 ms | 536 ms |
  | **Cold model** (first message after 5 idle min) | — | **13,970 ms** | 14,091 ms |

- **Cold start.** Ollama unloads the 16 GB model after 5 idle minutes, and reloading it takes about 14 s. §2 and §3 handle this with a warm-up call and a visible "waking up" state.
- **Server start.** Python imports take about 0.6 s.
- **Avatar crop.** A 1100×1100 square at (x 935, y 60) of `assets/Aura.jpeg`, centered on the face and scaled to 512 px, is 78 KB. The original is 2.6 MB.

## 1. Architecture

```
Aura.app  (SwiftUI, menu bar only)                aura-server  (Python, FastAPI, 127.0.0.1)
 ├─ StatusItem ─ click / ⌥Space ─▶ Panel           ├─ GET  /v1/health
 │                     └─ ChatView ─ ChatStore     ├─ POST /v1/chat              → SSE
 │                                      │          ├─ POST /v1/approvals         → SSE
 │                                 AuraClient ─────┼─ POST /v1/conversation/new
 │                                 (HTTP + SSE)    ├─ POST /v1/warmup
 └─ ServerManager ─ launches, watches ────────────▶└─ Aura  (brain.py, unchanged)
```

**Principle:** `brain.py` does not change. The server is a thin adapter over the event API built in Sprint 2 (`stream` / `resume` / `ApprovalNeeded`), so the terminal and the app run exactly the same agent loop.

## 2. The FastAPI Bridge (`src/aura/server.py`, run with `uv run aura-server`)

- **One process, one `Aura`, one conversation.** Aura has a single user. A second message while a turn is running gets `409 Busy`, as does a message while an approval is pending; the app answers the approval first. The sync event generator runs in Starlette's thread pool.
- **Binding.** The server listens on `127.0.0.1` only, on a port the OS assigns. It prints `AURA_READY port=<n>` on stdout for the app to read.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/v1/health` | — | `{status, model, model_loaded, search_mode, vault, busy}` |
| POST | `/v1/chat` | `{"text": "…"}` | SSE stream of the turn (§3) |
| POST | `/v1/approvals` | `{"approvals": [true, false, …]}` | SSE stream continuing the turn |
| POST | `/v1/conversation/new` | — | `204` |
| POST | `/v1/warmup` | — | `202`. Loads the model in the background through Ollama's native API (`keep_alive`; see §9.3) |

- **Cancellation.** The app's Stop button closes the stream, and the generator stops at the next event. This matches Ctrl-C in the terminal.
- **Security.** localhost is not a trust boundary: any web page you open can send requests to `127.0.0.1`. A "simple" cross-site POST runs even though the page can't read the reply, so it could approve a pending vault write. Four defences:
  1. **Per-launch bearer token.** The app generates a random 256-bit token at each launch and passes it to the server through an environment variable. Every request must carry it; the server compares it in constant time, and it is never written to disk.
  2. **Host check.** The `Host` header must be `127.0.0.1:<port>`, which blocks DNS rebinding.
  3. **JSON only.** Requests must use `Content-Type: application/json`, which forces a browser preflight. No CORS headers are ever sent, so the preflight always fails.
  4. **Quiet logging.** The access log is off, because request bodies are private conversation. Errors go to `~/Library/Logs/Aura/server.log`.
- **New dependencies:** `fastapi` and `uvicorn`, pinned in `uv.lock`.

## 3. The Streaming Protocol: Server-Sent Events

**Why SSE rather than WebSocket.** Within a turn, data flows one way, server to app. The app's two actions, sending a message and answering an approval, are ordinary POSTs. So each turn is one HTTP response, and the response ending means the turn has ended. There's no connection state, reconnection or framing to maintain. Swift reads it with `URLSession.bytes(for:).lines`; the spike confirmed this needs no third-party library.

**Wire format.** Each event is one `event:` line, exactly one `data:` line of JSON, and a blank line. The client dispatches on the `data:` line, so it doesn't depend on blank lines, which some line readers drop (including the spike's).

| `event` | `data` | What the user sees |
|---|---|---|
| `thinking` | `{}` | Sent immediately: Aura's typing indicator |
| `waking` | `{}` | The model is cold-loading (server checks Ollama first): "Aura is waking up…" |
| `status` | `{"text": "[Tool: File_System_Tool] Reading 'notes.txt'..."}` | A tool chip showing what Aura is doing |
| `text` | `{"text": "…"}` | Words appended to Aura's bubble as they are generated |
| `approval` | `{"actions": [{"tool", "args", "description"}]}` | An approval card (Allow / Deny). The stream ends; `/v1/approvals` continues it |
| `error` | `{"message": "…"}` | An inline error, e.g. "Can't reach the model". The HTTP status is already 200 by then |
| `done` | `{}` | End of turn |

**"Thinking" versus "typing".** The typing indicator shows from `thinking` (or `waking`) until the first `status` or `text` event. Tool chips then show *what* Aura is doing, and text streams in token by token.

## 4. The SwiftUI Component Model

**The app shell: AppKit, about 150 lines.**
- **`AuraApp`.** Menu-bar only (`LSUIElement`, no Dock icon).
- **`StatusItemController`.** An `NSStatusItem` with a monochrome template glyph. Clicking it, or pressing the **global hotkey ⌥Space**, toggles the panel. Right-click opens a menu: New Conversation, Open Vault in Finder, Open Audit Log, Restart Aura Server, Quit.
- **`AuraPanel`.** A floating `NSPanel` anchored under the menu bar icon, with system glass behind the SwiftUI content. Esc closes it, and it remembers its size.
- **Why not SwiftUI's `MenuBarExtra`.** It has no API to open its window programmatically, which rules out the hotkey from the original plan.
- **`HotKey`.** Carbon `RegisterEventHotKey`; needs no Accessibility permission.

**Views (SwiftUI).**
- **`ChatView`.** `HeaderView` + `TranscriptView` + `InputBar`.
- **`HeaderView`.** Avatar (32 pt) with a thin ring that shows state (connected / waking up / offline), the name "Aura", and a New Conversation button.
- **`TranscriptView`.** A lazy stack that stays pinned to the bottom while Aura streams.
- **`MessageBubble`.** User messages on the right with an accent tint; Aura's on the left in glass, with a 24 pt avatar.
- **`ToolChip`.** Status lines as compact chips with an icon per tool group: folder, globe, terminal.
- **`ApprovalCard`.** The exact action description with its content preview, and Allow / **Deny** buttons. Deny is the default (Esc), matching the terminal's `[y/N]`. Afterwards the card shows the outcome.
- **`TypingIndicator`.** Animated dots, with a "waking up" variant.
- **`InputBar`.** A multi-line field: Return sends, Shift-Return adds a newline. A Stop button appears while Aura streams.

**State and services.**
- **`ChatStore`** (`@Observable`, main actor). Holds the messages and the current phase: `idle → thinking → streaming ⇄ awaitingApproval → idle | error`. Exposes `send`, `decide`, `stop`, `newConversation`.
- **`AuraClient`** (actor). Holds the port and token, and turns each SSE response into an `AsyncThrowingStream<AuraEvent>`.
- **`ServerManager`.** Launches `uv run aura-server` with the token, reads the port, polls `/v1/health`, restarts the server after a crash (with backoff), and stops it when Aura quits.
  - The app runs without the App Sandbox, because it must start the Python server. It is a personal app, not an App Store one.

**Packaging.** The app is a Swift Package in `app/`, built with `swift build`. `scripts/build-app.sh` wraps it into `Aura.app`: Info.plist with `LSUIElement` and `NSAllowsLocalNetworking`, the icon, and an ad-hoc signature. Xcode can still open `Package.swift` for previews, and there is no hand-maintained `.xcodeproj`.

## 5. Asset Integration (`assets/Aura.jpeg`)

- **Source of truth.** The 2.6 MB original stays in `assets/` and is never shipped as is. `scripts/build-assets.sh` derives everything from it using `sips`/`iconutil`, which are built into macOS.
- **Avatar.** A face-centered square crop (tested; see §0) at 128/256/512 px. It appears in the header (32 pt), on Aura's messages (24 pt) and on approval cards (20 pt), with a circular mask and a thin silver ring.
- **App icon.** The same crop becomes `AppIcon.icns` (16–1024 px), shown in Finder, Activity Monitor, and macOS permission prompts ("Aura would like to access your Documents folder").
- **Panel backdrop** (§9.5). The starfield from the right of the image, with no face, heavily blurred at about 8% opacity under the glass. The window carries Aura's colors without competing with the text.
- **Accent color.** The deep-space blue and silver sampled from the image become the app's accent (user messages, focus ring).
- **Menu bar.** A photo can't serve as a menu bar icon, because macOS requires a monochrome template image that adapts to light and dark mode. We'll use an SF Symbol (`sparkle`) for now.

## 6. Test plan

**Python** (offline: FastAPI `TestClient` plus the scripted model from Sprint 2):
- **Wire format.** Each event type, exactly one data line per event, and a `done` terminator.
- **Security rejections.**
  - Missing or wrong token: 401.
  - Wrong `Host`: 421.
  - Non-JSON body: 415.
  - No CORS headers on any response.
- **Busy and pending:** 409. Wrong number of approvals: 422.
- **Approval over HTTP.** Approve: a status line, then text. Deny: Gemma's exact denial line.
- **Model unreachable:** an `error` event, not a crash.

**Swift** (`swift test`):
- **SSE parser.** Events split across network chunks, multi-byte characters split across chunks, unknown event types ignored.
- **`ChatStore`.** Every phase transition, including errors and Stop.

**End to end** (live):
- Build and launch `Aura.app`; the server starts and reports healthy.
- A real conversation, a tool call, and an approval round-trip through the HTTP API.
- Kill the server; the app shows "offline", then recovers on its own.
- Screenshots of the panel in light and dark mode, captured with `screencapture`, for your visual review.

## 7. Files

```
src/aura/server.py            FastAPI bridge (+ "aura-server" entry point, fastapi/uvicorn deps)
tests/test_server.py
app/Package.swift             macOS 26+ (glass APIs)
app/Sources/Aura/             AuraApp, StatusItemController, AuraPanel, HotKey,
                              ChatStore, AuraClient, ServerManager, Models
app/Sources/Aura/Views/       ChatView, HeaderView, TranscriptView, MessageBubble,
                              ToolChip, ApprovalCard, TypingIndicator, InputBar
app/Tests/AuraTests/          SSE parser and ChatStore tests
scripts/build-assets.sh       avatar, icon and backdrop from assets/Aura.jpeg
scripts/build-app.sh          swift build → Aura.app (Info.plist, icon, ad-hoc signing)
```

## 8. Known risks

- **Memory doesn't survive restarts.** Conversation memory is still held in the server process, so quitting Aura clears it. The fix is a SQLite checkpointer, a small change suggested for Sprint 4.
- **Documents permission.** The app starts the server, so macOS asks once whether **Aura** may access the Documents folder (where the vault lives).
- **Hotkey clash.** ⌥Space is free on stock macOS, but launchers like Raycast or Alfred often take it. A settings field can change it in Sprint 4.

## 9. Decisions requested

1. **Panel shell.** *Recommended:* the AppKit status item and panel hosting SwiftUI, which allows a hotkey. The alternative is pure SwiftUI `MenuBarExtra`: less code, but no hotkey.
2. **Global hotkey.** *Recommended:* ⌥Space.
3. **Model warm-up.** *Recommended:* warm the model when the panel opens (you are about to type), and keep it loaded for 30 min. Keeping it loaded permanently removes the 14 s cold start, but holds 16 GB of the 24 GB at all times; only 13% was free with Docker running in Sprint 2.
4. **Server lifetime.** *Recommended for Sprint 3:* the app starts the server and stops it on quit. A launchd agent would survive app restarts but adds complexity.
5. **Panel backdrop.** *Recommended:* the subtle blurred starfield under the glass. The alternative is plain glass.
6. **Markdown.** It's scheduled for Sprint 4. SwiftUI renders *inline* Markdown (bold, italics, `code`, links) natively at no cost. *Recommended:* inline Markdown now; code blocks and tables in Sprint 4.
