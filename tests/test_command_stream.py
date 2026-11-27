"""The Unified Command Stream (docs/unified-command-stream.md): the owner's Slack commands run in
the Mac conversation, each turn keeps its origin and reply address, and Slack answers only the
cards its own turns raised.
"""

from __future__ import annotations

import sqlite3
import threading

from conftest import call, make_aura, tool_turn
from langchain_core.messages import AIMessage

from aura.lifecycle import Lifecycle
from aura.state import StateStore

SLACK_THREAD = {"channel": "D0OWNER", "thread_ts": None}


# ── T1: origins and reply addresses ─────────────────────────────────────────────────────────


def test_a_version_1_store_gains_origin_and_reply_to_without_losing_approvals(tmp_path):
    path = tmp_path / "state.sqlite"
    db = sqlite3.connect(path)
    from aura.state import MIGRATIONS

    for statement in MIGRATIONS[1]:
        db.execute(statement)
    db.execute("INSERT INTO approvals (id, conversation, interrupt_id, digest, tools, signature, state, created) "
               "VALUES ('a-old', 'conv', 'i', 'd', '[]', '{}', 'pending', 'now')")  # fmt: skip
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()
    store = StateStore(path)
    old = store.approval("a-old")
    assert old["state"] == "pending" and old["origin"] == "mac" and old["reply_to"] is None


def test_a_slack_turns_card_keeps_its_origin_and_reply_address_after_a_restart(settings):
    aura, _ = make_aura(settings, tool_turn(call("create_folder", path="Plans")))
    (ask,) = [e for e in aura.stream("Make a Plans folder.", origin="slack", reply_to=SLACK_THREAD)
              if hasattr(e, "approval_id")]  # fmt: skip
    again, _ = make_aura(settings, AIMessage("Created it."), fresh=False)
    assert again.approval_id == ask.approval_id
    assert again.pending()["origin"] == "slack"
    assert again.pending_origin() == ("slack", SLACK_THREAD)


def test_a_mac_turns_card_is_the_macs(settings):
    aura, _ = make_aura(settings, tool_turn(call("create_folder", path="Plans")))
    list(aura.stream("Make a Plans folder."))
    assert aura.pending()["origin"] == "mac" and aura.pending_origin() == ("mac", None)


def test_a_card_raised_after_answering_a_slack_card_is_still_slacks(settings):
    """The continuation belongs to the card's turn, wherever the answer came from."""
    aura, _ = make_aura(settings, tool_turn(call("create_folder", path="Plans")),
                        tool_turn(call("create_folder", path="Notes")))  # fmt: skip
    list(aura.stream("Make two folders.", origin="slack", reply_to=SLACK_THREAD))
    list(aura.resume([True], approval_id=aura.approval_id))  # answered on the Mac (the default origin)
    assert aura.awaiting_approval and aura.pending_origin() == ("slack", SLACK_THREAD)


# ── T3: the owner's Slack commands run in the Mac conversation ──────────────────────────────


def slack(aura, tmp_path, **more):
    from test_slack_chat import remote_chat

    return remote_chat(aura, None, tmp_path, **more)


def test_an_owner_command_joins_the_mac_conversation_and_is_answered_where_it_was_asked(settings, tmp_path):
    aura, model = make_aura(settings, AIMessage("Nice to meet you, Ada."), AIMessage("You're Ada."),
                            AIMessage("Still Ada."))  # fmt: skip
    aura.chat("My name is Ada.")  # typed on the Mac
    remote, posted, _ = slack(aura, tmp_path)
    from aura.connections.remote import Ask

    remote.handle(Ask(channel="C7", ts="5.1", thread_ts="5.0", text="What's my name?", place="#aura-chat"))
    assert posted == [("C7", "You're Ada.", "5.0")]  # its own channel and thread
    assert "My name is Ada." in str(model.agent_calls()[-1])  # the Mac's context
    assert [(m["role"], m.get("origin")) for m in aura.transcript()] == [
        ("user", None), ("aura", None), ("user", "slack"), ("aura", None)]
    aura.chat("And now?")  # back on the Mac: the same conversation, and nothing goes to Slack
    assert "What's my name?" in str(model.agent_calls()[-1]) and len(posted) == 1


def test_a_slack_turn_is_recorded_with_its_origin(settings, tmp_path):
    aura, _ = make_aura(settings, AIMessage("Hi."))
    lifecycle = Lifecycle(aura.toolbox.state, threading.Lock())
    remote, _, dm = slack(aura, tmp_path, lifecycle=lifecycle)
    begun = []
    real = lifecycle.begin
    lifecycle.begin = lambda *a, **k: begun.append(t := real(*a, **k)) or t
    remote.handle(dm("hello"))
    (turn,) = begun
    assert turn.origin == "slack" and aura.toolbox.state.turn(turn.id)["origin"] == "slack" and turn.state == "done"


def test_slack_never_answers_a_card_raised_at_the_mac(settings, tmp_path):
    from aura.connections.remote import MAC_CARD

    aura, _ = make_aura(settings, tool_turn(call("create_folder", path="Plans")), AIMessage("Created it."))
    list(aura.stream("Make a Plans folder."))  # the card is raised on the Mac
    remote, posted, dm = slack(aura, tmp_path)
    for word in ("allow", "deny", "make it anyway"):
        remote.handle(dm(word))
        assert posted[-1][1] == MAC_CARD
    assert aura.awaiting_approval and not (settings.vault_dir / "Plans").exists()  # untouched, still the Mac's
    list(aura.resume([True], approval_id=aura.approval_id))  # the Mac answers it
    assert (settings.vault_dir / "Plans").is_dir()


def test_slack_answers_its_own_card(settings, tmp_path):
    aura, _ = make_aura(settings, tool_turn(call("create_folder", path="Plans")), AIMessage("Created it."))
    remote, posted, dm = slack(aura, tmp_path)
    remote.handle(dm("Make a Plans folder."))
    assert posted[-1][1].endswith("Reply **allow** or **deny**.") and aura.pending()["origin"] == "slack"
    remote.handle(dm("allow"))
    assert (settings.vault_dir / "Plans").is_dir() and posted[-1][1] == "Created it."


def test_a_slack_turn_uses_the_macs_model_choice(settings, tmp_path):
    aura, _ = make_aura(settings, AIMessage("Hi."))
    asked = []
    remote, posted, dm = slack(aura, tmp_path, choose_model=lambda text: asked.append(text))
    remote.handle(dm("hello"))
    assert asked == ["hello"] and posted[-1][1] == "Hi."


def test_a_model_that_isnt_ready_is_explained_in_slack(settings, tmp_path):
    aura, _ = make_aura(settings)

    def not_ready(text):
        raise ValueError("Download the local model before using it: big:70b.")

    remote, posted, dm = slack(aura, tmp_path, choose_model=not_ready)
    remote.handle(dm("hello"))
    assert posted[-1][1] == "Download the local model before using it: big:70b." and not aura.transcript()


def test_the_queue_holds_five_and_turns_the_rest_away(settings, tmp_path):
    import time

    from aura.connections.remote import BUSY, QUEUE_LIMIT

    aura, _ = make_aura(settings, *[AIMessage(f"Reply {i}.") for i in range(QUEUE_LIMIT + 1)])
    lock = threading.Lock()
    remote, posted, dm = slack(aura, tmp_path, turn=lock)
    lock.acquire()  # a Mac turn is running
    remote.submit(dm("first"))
    deadline = time.monotonic() + 5
    while remote._queue.qsize() and time.monotonic() < deadline:  # the worker takes it and waits
        time.sleep(0.01)
    for i in range(QUEUE_LIMIT):
        remote.submit(dm(f"queued {i}"))
    remote.submit(dm("one too many"))
    assert [text for _, text, _ in posted] == [BUSY]
    lock.release()
    while len(posted) < QUEUE_LIMIT + 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert [text for _, text, _ in posted[1:]] == [f"Reply {i}." for i in range(QUEUE_LIMIT + 1)]  # in order


# ── T4: the turn broadcast and the mirror feed ──────────────────────────────────────────────

TOKEN_TEXT = "xoxb-" + "1234567890-abcdefghijkl"  # split: GitHub push protection


class _Turn:
    def __init__(self, id="t-1", origin="slack"):
        self.id, self.origin = id, origin


def test_the_broadcast_gives_what_was_missed_or_a_reset():
    from aura.broadcast import TurnBroadcast

    b = TurnBroadcast(keep=3)
    for i in range(3):
        b.publish(_Turn(), "text", {"text": str(i)})
    assert [f["data"]["text"] for f in b.wait(1, 0)] == ["1", "2"]
    assert b.wait(3, 0) == []
    assert b.wait(99, 5)[0]["event"] == "reset"  # numbered before a restart: at once, no wait
    b.publish(_Turn(), "text", {"text": "3"})  # frame 1 is gone now
    assert b.wait(0, 0)[0]["event"] == "reset"  # frame 1 was missed and can't be given
    assert [f["seq"] for f in b.wait(1, 0)] == [2, 3, 4]


def feed(client, after=0, hold=0.3):
    import httpx
    from test_server import AUTH, events

    r = client.get(f"/v1/turns/live?after={after}&hold={hold}", headers=AUTH)
    kept = "".join(block + "\n\n" for block in r.text.split("\n\n") if block and not block.startswith(":"))
    return events(httpx.Response(r.status_code, headers=r.headers, text=kept))  # keepalive comments dropped


def server(aura):
    from fastapi.testclient import TestClient
    from test_server import TOKEN, FakeKeeper

    from aura.server import create_app

    return TestClient(create_app(aura, TOKEN, FakeKeeper()), base_url="http://127.0.0.1")


def test_a_mac_turn_is_mirrored_exactly_as_sent_and_masked(settings):
    from test_server import events, post
    from test_server import said as texts

    aura, _ = make_aura(settings, AIMessage(f"Your token is {TOKEN_TEXT}."))
    client = server(aura)
    sent = events(post(client, "/v1/chat", {"text": f"is {TOKEN_TEXT} safe?"}))
    mirrored = feed(client)
    assert mirrored[0] == ("ready", {"seq": 0})
    (start,) = [d for k, d in mirrored if k == "turn"]
    assert start["origin"] == "mac" and TOKEN_TEXT not in start["text"] and start["text"].startswith("is ")
    assert texts(mirrored) == texts(sent) and TOKEN_TEXT not in texts(mirrored)
    assert [k for k, _ in mirrored][-1] == "done" and {d["turn_id"] for k, d in mirrored if k != "ready"} == {start["turn_id"]}


def test_a_slack_turn_is_mirrored_with_its_message_and_reply(settings, tmp_path):
    from aura.broadcast import TurnBroadcast
    from aura.masking import Masker

    aura, _ = make_aura(settings, AIMessage(f"Here: {TOKEN_TEXT}"))
    broadcast = TurnBroadcast()
    lifecycle = Lifecycle(aura.toolbox.state, threading.Lock())
    remote, posted, dm = slack(aura, tmp_path, lifecycle=lifecycle, broadcast=broadcast, masker=Masker())
    remote.handle(dm("what's my token?"))
    frames = broadcast.wait(0, 0)
    assert frames[0]["event"] == "turn" and frames[-1]["event"] == "done"
    assert frames[0]["origin"] == "slack" and frames[0]["data"]["text"] == "what's my token?"
    shown = "".join(f["data"]["text"] for f in frames if f["event"] == "text")
    assert shown.startswith("Here: ") and TOKEN_TEXT not in shown and TOKEN_TEXT not in posted[-1][1]
    assert frames[-1]["data"] == {"state": "done"}


def test_a_slack_card_and_a_refusal_are_mirrored(settings, tmp_path):
    from aura.broadcast import TurnBroadcast
    from aura.connections.remote import MAC_CARD

    aura, _ = make_aura(settings, tool_turn(call("create_folder", path="Plans")))
    broadcast = TurnBroadcast()
    lifecycle = Lifecycle(aura.toolbox.state, threading.Lock())
    remote, _, dm = slack(aura, tmp_path, lifecycle=lifecycle, broadcast=broadcast)
    remote.handle(dm("Make a Plans folder."))
    assert "approval" in [f["event"] for f in broadcast.wait(0, 0)]
    aura.toolbox.state.add_approval("a-mac", None, aura.thread_id, "i", "d", [], "{}")  # pretend the Mac's card
    aura.approval_id = "a-mac"
    seen = broadcast.latest
    remote.handle(dm("allow"))
    assert [(f["event"], f["data"].get("text")) for f in broadcast.wait(seen, 0)][1] == ("text", MAC_CARD)


def test_the_feed_says_reset_to_a_client_from_before_a_restart(settings):
    aura, _ = make_aura(settings)
    mirrored = feed(server(aura), after=500, hold=0.2)
    assert [k for k, _ in mirrored] == ["ready", "reset"]


def test_the_feed_needs_the_token(settings):
    client = server(make_aura(settings)[0])
    assert client.get("/v1/turns/live?hold=0").status_code == 401


# ── T5: a Slack card answered on the Mac: the rest goes back to Slack ───────────────────────


def mac_and_slack(settings, *script):
    """The server, with its Slack chat posting into a list: (aura, client, remote, posted, dm)."""
    from test_server import server as served

    from aura.connections.remote import Ask

    client, aura, _ = served(settings, *script)
    remote, posted = client.app.state.remote, []
    remote._post = lambda *a: posted.append(a)
    return aura, client, remote, posted, lambda text: Ask(channel="C7", ts="5.1", thread_ts="5.0", text=text,
                                                          place="#aura-chat")  # fmt: skip


def wait_for(condition, seconds=5.0):
    import time

    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


def test_a_slack_card_answered_on_the_mac_finishes_in_slack_once(settings):
    from test_server import AUTH, events, post

    _, client, remote, posted, ask = mac_and_slack(settings, tool_turn(call("create_folder", path="Plans")),
                                                      AIMessage("Created it."))  # fmt: skip
    remote.handle(ask("Make a Plans folder."))
    assert posted[-1][1].endswith("Reply **allow** or **deny**.")
    pending = client.get("/v1/approvals/pending", headers=AUTH).json()["pending"]
    assert pending["origin"] == "slack"
    mac = events(post(client, "/v1/approvals", {"approvals": [True], "approval_id": pending["approval_id"]}))
    assert "".join(d["text"] for k, d in mac if k == "text") == "Created it."  # the Mac's own stream, as usual
    assert wait_for(lambda: len(posted) == 2)
    assert posted[-1] == ("C7", "(Answered on the Mac.)\n\nCreated it.", "5.0")  # the thread it came from
    assert not wait_for(lambda: len(posted) > 2, 0.3)  # once
    assert (settings.vault_dir / "Plans").is_dir()


def test_a_further_card_goes_to_slack_and_stays_slacks(settings):
    from test_server import post

    aura, client, remote, posted, ask = mac_and_slack(settings, tool_turn(call("create_folder", path="Plans")),
                                                      tool_turn(call("create_folder", path="Notes")))  # fmt: skip
    remote.handle(ask("Make two folders."))
    post(client, "/v1/approvals", {"approvals": [True], "approval_id": aura.approval_id})
    assert wait_for(lambda: len(posted) == 2) and "Notes" in posted[-1][1]
    assert posted[-1][1].endswith("Reply **allow** or **deny**.") and aura.pending_origin()[0] == "slack"


def test_a_mac_card_answered_on_the_mac_sends_nothing_to_slack(settings):
    from test_server import events, post

    _, client, _, posted, _ = mac_and_slack(settings, tool_turn(call("create_folder", path="Plans")),
                                                    AIMessage("Created it."))  # fmt: skip
    approval_id = next(d for k, d in events(post(client, "/v1/chat", {"text": "Make a Plans folder."}))
                       if k == "approval")["approval_id"]  # fmt: skip
    events(post(client, "/v1/approvals", {"approvals": [True], "approval_id": approval_id}))
    assert not wait_for(lambda: posted, 0.3)


def test_a_failed_slack_post_still_ends_the_macs_turn(settings):
    from test_server import AUTH, events, post

    aura, client, remote, _, ask = mac_and_slack(settings, tool_turn(call("create_folder", path="Plans")),
                                                      AIMessage("Created it."))  # fmt: skip
    remote.handle(ask("Make a Plans folder."))

    def down(*a):
        raise ConnectionError("Slack is unreachable")

    remote._post = down
    mac = events(post(client, "/v1/approvals", {"approvals": [True], "approval_id": aura.approval_id}))
    assert [k for k, _ in mac][-1] == "done"
    health = client.get("/v1/health", headers=AUTH).json()
    assert "active_turn" in health and health["active_turn"] is None  # released: nothing hangs
    assert wait_for(lambda: any("Couldn't answer in Slack" in m.text for m in aura.toolbox.live.inbox.recent(5)))


# ── after review: privacy, card answers, order ──────────────────────────────────────────────


def test_a_slack_turn_never_carries_other_peoples_messages_and_the_mac_still_gets_them(settings, tmp_path):
    """R2: a Slack reply is posted without a card (maybe in a channel thread), so other people's
    live messages join only Mac turns."""
    aura, model = make_aura(settings, AIMessage("Hi from Slack."), AIMessage("Hi on the Mac."))
    aura.toolbox.live.inbox.add("Slack", "a direct message", "Sam", "my salary is confidential", "dm")
    remote, _, dm = slack(aura, tmp_path)
    remote.handle(dm("what's new?"))
    assert "my salary is confidential" not in str(model.agent_calls()[-1])
    aura.chat("Anything new?")  # the Mac's next turn: there it is, framed as untrusted
    assert "my salary is confidential" in str(model.agent_calls()[-1])


def test_the_feed_says_answered_only_when_slack_answers_a_card(settings, tmp_path):
    from aura.broadcast import TurnBroadcast

    aura, _ = make_aura(settings, tool_turn(call("create_folder", path="Plans")), AIMessage("Created it."))
    broadcast = TurnBroadcast()
    lifecycle = Lifecycle(aura.toolbox.state, threading.Lock())
    remote, _, dm = slack(aura, tmp_path, lifecycle=lifecycle, broadcast=broadcast)
    remote.handle(dm("Make a Plans folder."))
    remote.handle(dm("what's the weather?"))  # not an answer: "still waiting"
    assert "answered" not in [f["event"] for f in broadcast.wait(0, 0)]
    seen = broadcast.latest
    remote.handle(dm("allow"))
    assert [f["event"] for f in broadcast.wait(seen, 0)][:2] == ["turn", "answered"]


def test_a_silent_continuation_tells_slack_it_is_done(settings):
    from test_server import events, post

    aura, client, remote, posted, ask = mac_and_slack(settings, tool_turn(call("create_folder", path="Plans")),
                                                      AIMessage(""))  # fmt: skip
    remote.handle(ask("Make a Plans folder."))
    events(post(client, "/v1/approvals", {"approvals": [True], "approval_id": aura.approval_id}))  # Aura says nothing more
    assert posted[-1][1] == "(Answered on the Mac.)\n\nDone."


def test_the_rest_is_posted_to_slack_before_the_macs_turn_ends(settings):
    """So a queued Slack message can't answer in the thread first."""
    from test_server import events, post

    aura, client, remote, posted, ask = mac_and_slack(settings, tool_turn(call("create_folder", path="Plans")),
                                                      AIMessage("Created it."))  # fmt: skip
    remote.handle(ask("Make a Plans folder."))
    events(post(client, "/v1/approvals", {"approvals": [True], "approval_id": aura.approval_id}))
    assert len(posted) == 2  # already there when the Mac's stream ended: no waiting
