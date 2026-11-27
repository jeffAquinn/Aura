# Unified Command Stream: one conversation for the Mac and Slack (Gap A)

Status: **approved by Aura (architect), 2026-11-26. Implemented (tickets T1 to T7), 2026-11-26.** The live check from the phone (section 6) is still to be run.

## 1. Outcome

When the owner talks to Aura from Slack, the message joins the **Mac conversation**: the conversation, memory and context are the same ones the app uses. Aura's answer goes back to Slack, and the Mac panel shows the turn live, as it runs. Anyone else's messages stay passive (R5, R2), as they are today.

**Today:** `RemoteChat` (`connections/remote.py`) runs a second Aura (`primary=False`, `role="slack"`) with its own saved conversation. The Mac sees only the small cards the inbox adds for Slack commands and replies.

## 2. Scope and non-goals

In scope: Slack, for the owner's verified commands only (the existing `_ask` check in `live.py`).

Not in scope:
- Discord commands (Gap B). Section 4 leaves room for them.
- When passive messages reach the model (Gap C).
- Starting a new conversation from Slack.
- The Developer picker (`</>`) from Slack.
- Posting replies to turns started on the Mac into Slack.

## 3. Unchanged

- **Identity:** a message is a command only if the sender's Slack user ID is the signed-in account's ID, the "Talk to Aura from Slack" switch is on, and the message is a DM, an @mention or in the chat channel. The switch stays off by default.
- **Everyone else:** messages go to the inbox and reach the model as the synthetic `new_messages` result, labelled R5 and counted as an R2 private read.
- **Approvals:** cards default to Deny. The Egress Gatekeeper's card applies to every search. A search Slack can't show exactly can only be denied in Slack. The Integrity Rule and the R1 floors are unchanged.
- **Masking:** everything posted to Slack still passes through the masker.

## 4. Design

### 4.1 The Instance Problem: one Aura, many origins

The server already holds one primary `aura`, and every Mac turn uses it. The fix is to remove the second Aura instead of trying to route events to it:

- `RemoteChat` keeps its transport work: the queue, the allow/deny words and posting to Slack. It is given the primary `aura` instead of `make_aura`. The second Aura and the `conversation:slack` thread are no longer used.
- Each turn already has an `origin` (`Turn.origin`, stored by `Lifecycle.begin`). A Slack command starts its turn with `origin="slack"` and a **reply address**: the channel and thread to answer in.
- The automatic model choice in `/v1/chat` (the route, the cloud availability check, the model notice) moves into a helper that Mac and Slack turns both call. A Slack turn chooses its model the same way as a Mac turn where the user hasn't picked one.
- Passive messages that arrived since the last turn (other people's, R5 and R2) are added to **Mac turns only**. *Changed after code review, 2026-11-26; this needs Aura's ruling:* the draft added them to Slack turns too. But a Slack reply is posted without a card, and it can go into a channel thread when the owner @mentions Aura there, so someone's private DM could be summarised into a public channel. They wait for the next Mac turn instead.

### 4.2 The Feedback Loop: replies follow the turn's origin

A turn's events are passed through a small **turn broadcast**. It delivers each event to the turn's origin and to a mirror feed:

- **The origin:** for a Mac turn, the SSE response as today. For a Slack turn, a collector that gathers the reply and posts it once, at the end of the turn, to the reply address, as `RemoteChat._finish` does today.
- **The mirror feed:** `GET /v1/turns/live` (SSE, with the per-launch bearer token). The app subscribes once and draws any turn it didn't start itself: the owner's Slack message as a user bubble with a Slack badge, then Aura's reply as it streams in. Masking applies on the feed as it does on `/v1/chat`.

Replies are addressed by the turn's own reply address. There is never a global "last Slack channel". A Mac turn has no reply address, so nothing from it is posted to Slack.

The inbox stops adding `command` and `reply` cards, because those messages are now in the conversation itself. The audit records each turn's origin.

### 4.3 Concurrency

One lock, as today (`Lifecycle`):
- A Mac message while a Slack turn runs gets the existing busy response. The app shows "Answering from Slack…" with a Stop button, which stops either kind of turn.
- A Slack message while a Mac turn runs waits in `RemoteChat`'s queue. Queued messages run in order, one turn each.
- The queue holds at most 5 messages. Beyond that, Slack gets "I'm busy, try again in a moment," and that message is dropped.
- While a card is waiting, no new turn starts from either place. This is today's rule on the Mac.

### 4.4 Approvals across origins (the user's decision, 2026-11-26)

- **The Mac can answer any card**, whichever place started the turn.
- **Slack can answer only cards raised by a Slack turn.** For a Mac turn's card, Slack replies "A request from the Mac is waiting there. Answer it on the Mac," and nothing is approved.
- A card's origin and reply address are stored with its durable approval record, so they survive a restart.
- When the Mac answers a Slack card, Aura still posts the rest of the turn's reply in Slack.
- If the Mac and Slack both answer one card, the first answer wins. The existing `StaleApproval` check refuses the second.

### 4.5 Migration

The old Slack conversation is left on disk, unused. Nothing is deleted. Starting a new conversation on the Mac starts it for both places.

## 5. What this changes about trust

The owner's Slack messages become user messages in the Mac conversation. A person who took over the owner's Slack account could now see what's in the Mac conversation; before, they saw only the separate Slack conversation. They already had the same tools and the same cards on Slack turns. They don't gain power over cards raised at the Mac (4.4). The mitigations are the off-by-default switch, the identity check, and the per-turn origin in the audit. **Aura accepted this residual risk (2026-11-26)** as a credential risk, not a risk to the system's integrity.

## 6. Acceptance evidence

Offline tests:
1. An owner command runs on the primary Aura's thread. No second Aura is built.
2. A non-owner message never starts a turn and reaches the model only as R5 `new_messages`.
3. A Slack turn's reply is posted to its own channel and thread. A Mac turn posts nothing to Slack.
4. The mirror feed shows a Slack turn's message and reply, masked.
5. A Mac message during a Slack turn gets busy. A Slack message during a Mac turn waits, then runs. The sixth queued message is refused.
6. Slack can't approve a Mac card. The Mac can approve a Slack card, and the rest of the turn is posted to Slack. After a restart, a Slack card is still Slack's.
7. The existing `test_slack_chat.py` and `test_live_messages.py` cases still pass, or are updated where the single conversation changes them.

App: `scripts/build-app.sh` succeeds, and the Mac panel draws a Slack turn from the mirror feed. This is checked against the scratch vault with CGWindowList, not `screencapture`.

Live, run by the user: from the phone, ask something that depends on an earlier Mac message. Then watch the turn appear on the Mac, and approve a search card from Slack.

## 7. Aura's rulings (2026-11-26)

1. The residual risk in section 5 is accepted.
2. A queue limit of 5 is right: room for a few follow-ups from a phone, and backpressure against a flooded channel.
3. Post the Slack reply once, at the end of the turn, as drafted. A reply posted all at once avoids rate limits and half-written messages after a dropped connection.

## 8. As built (2026-11-26)

Where each part lives:

- **Origins and reply addresses:** `Turn.origin` (`lifecycle.py`) says where a turn began. `Aura.origin` and `Aura.reply_to` (`brain.py`) hold the running turn's origin and Slack reply address. `state.sqlite` schema 2 stores both with each card (`approvals.origin`, `approvals.reply_to`). `Aura.pending_origin()` reads them, and a resumed card keeps the origin of the turn that raised it. Slack messages are stored with `origin: "slack"`, and the transcript returns it.
- **One Aura:** `RemoteChat` (`connections/remote.py`) runs on the primary Aura. It uses `choose_turn_model` from `server.py`, the same automatic model choice as the Mac. It holds a queue of 5 (`QUEUE_LIMIT`). For a Mac card it replies `MAC_CARD` and approves nothing.
- **The mirror feed:** `TurnBroadcast` (`broadcast.py`) and `GET /v1/turns/live?after=N&hold=S`.
  - Each frame's data carries `seq`, `turn_id` and `origin`.
  - The feed sends `ready` first. `after=-1` starts from now. A gap the buffer can't fill gives `reset`, and so does a number beyond the newest. Numbers start again when the server restarts, so a client follows a new server from -1, as the app does each time it connects to a new server.
  - `stream_turn` publishes exactly what it sends to the Mac, after masking. Slack turns publish through the same stream masker.
- **A Slack card answered on the Mac:** `/v1/approvals` reads the card's origin before resuming. When the continuation ends, `RemoteChat.deliver` posts the rest once, before the turn's lock is released, so replies in the thread stay in order. The post starts with "(Answered on the Mac.)", a prefix Aura approved, and says "Done." when Aura had nothing more to say. A failed post is logged, the Mac shows "Couldn't answer in Slack", and the turn ends either way.
- **Answering a card from Slack:** the feed gets an `answered` frame only when Slack actually answers a card. The app marks the Mac's copy "Answered in Slack" only then. Any other Slack message, such as "answer it on the Mac", leaves the card waiting on the Mac.
- **The citation warning** is part of the reply, so a continuation posted to Slack carries it too. Slack's own turns aren't checked yet (open item).
- **The app:** `ChatStore.followMirror` reconnects quietly from the last `seq` seen, waiting 0.5 s at first and doubling to at most 5 s. On `reset` it reloads the conversation.
  - Slack turns show a "From Slack" label, the header "Answering from Slack…", and a Stop that names the turn's id.
  - A card Slack answered itself shows "Answered in Slack".
  - Slack turns are not read aloud on the Mac.
  - The Mac notification for Aura's Slack answer now comes from the feed.
- **Cleanup:** the inbox no longer gets `command` or `reply` cards for the owner's Slack chat, since the conversation shows them. The one exception is the failed-post notice, which the model never sees.
