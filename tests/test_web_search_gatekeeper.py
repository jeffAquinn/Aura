"""The Egress Gatekeeper, Phase 1: no web search leaves this Mac without a fresh approval card for
that exact query (src/aura/tools/egress.py, web.py, brain.py)."""

from __future__ import annotations

import pytest
from conftest import Recorder, call, make_aura, tool_turn
from langchain_core.messages import AIMessage, ToolMessage

from aura.audit import AuditLog
from aura.brain import ApprovalNeeded
from aura.tools.egress import EgressGate, PermissionDenied, hidden_characters, visible
from aura.tools.web import WebSearch, duckduckgo_backend, web_tools


def gated_aura(settings, *script):
    tavily, ddg = Recorder(), Recorder()
    aura, _ = make_aura(settings, *script, tavily=tavily, duckduckgo=ddg, tavily_api_key="tvly-test")
    return aura, tavily, ddg


def results(aura) -> list[ToolMessage]:
    msgs = aura._agent.get_state(aura._config).values["messages"]
    return [m for m in msgs if isinstance(m, ToolMessage) and m.name == "web_search"]


# ── The gate: WebSearch.search refuses without a grant for the exact query ──────────────────


@pytest.fixture
def search(tmp_path):
    tavily, ddg = Recorder(), Recorder()
    return WebSearch("tvly-test", AuditLog(tmp_path / "audit.jsonl"), tavily=tavily, duckduckgo=ddg), tavily, ddg


def test_a_search_without_approval_raises_permission_denied(search):
    web, tavily, ddg = search
    with pytest.raises(PermissionDenied):
        web.search("weather London")
    with pytest.raises(PermissionDenied):
        web.search("weather London", tool_call_id="call_1")
    assert tavily.queries == ddg.queries == []


def test_a_grant_covers_only_the_exact_query_it_was_made_for(search):
    web, tavily, _ = search
    web.gate.grant("a-1", "call_1", "weather London")
    with pytest.raises(PermissionDenied):
        web.search("weather London ", tool_call_id="call_1")  # one trailing space differs
    assert tavily.queries == []
    web.gate.grant("a-1", "call_1", "weather London")
    with pytest.raises(PermissionDenied):
        web.search("weather London", tool_call_id="call_2")  # another tool call
    assert tavily.queries == []


def test_a_grant_is_used_once(search):
    web, tavily, _ = search
    web.gate.grant("a-1", "call_1", "weather London")
    web.search("weather London", tool_call_id="call_1")
    with pytest.raises(PermissionDenied):
        web.search("weather London", tool_call_id="call_1")
    assert tavily.queries == ["weather London"]
    assert web.gate.pending() == 0


def test_one_grant_covers_the_named_fallback(search):
    web, _, ddg = search

    def down(query, n):
        raise RuntimeError("Tavily is down")

    web._tavily = down
    web.gate.grant("a-1", "call_1", "weather London")
    assert web.search("weather London", tool_call_id="call_1").backend == "duckduckgo"
    assert ddg.queries == ["weather London"]


def test_grants_do_not_survive_a_new_gate():
    first, second = EgressGate(), EgressGate()
    first.grant("a-1", "call_1", "q")
    with pytest.raises(PermissionDenied):
        second.consume("call_1", "q")  # a restart: a new gate holds no grants


def test_the_tool_called_directly_refuses_and_sends_nothing(search):
    web, tavily, ddg = search
    (tool,) = web_tools(web)
    msg = tool.invoke({"type": "tool_call", "name": "web_search", "args": {"query": "my secret key"}, "id": "call_x"})
    assert msg.text.startswith("Not searched: No approval for this search")
    assert msg.artifact == {"backend": "none", "fallback": False}
    assert tavily.queries == ddg.queries == []
    assert web.audit.entries()[-1]["event"] == "egress_refused"


# ── Hidden characters: refused before any card, and again inside search() ───────────────────


@pytest.mark.parametrize("query", [
    "weather\u200bLondon",  # zero-width space (Cf)
    "weather \u202eLondon",  # right-to-left override (Cf)
    "weather\u00a0London",  # no-break space (Zs, not U+0020)
    "weather\x07London",  # bell (Cc)
    "weather\nLondon",  # newline (Cc)
    "weather\u2028London",  # line separator (Zl)
    "weather\ue000London",  # private use (Co)
    "weather\U000e0041London",  # tag character (Cf)
    "weather London \U0001f327" + "".join(chr(0xE0100 + b) for b in b"hunter2"),  # variation selectors (Mn) carrying bytes
    "weather\ufe0fLondon",  # variation selector-16 (Mn)
    "weather\u034fLondon",  # combining grapheme joiner (Mn)
    "weather\u3164London",  # Hangul filler (Lo)
    "weather\u2800London",  # braille pattern blank (So)
    "weather\u00adLondon",  # soft hyphen (Cf)
])
def test_hidden_characters_are_found(query):
    assert hidden_characters(query) and not visible(query)


def test_visible_text_passes_including_ordinary_unicode():
    for query in ["weather London", "  spaced  ", "café Zürich", "東京 天気", "naïve—dash", "emoji 🌧"]:
        assert visible(query), query
    assert not visible(None) and not visible(3)


def test_hidden_characters_are_refused_even_with_a_grant(search):
    web, tavily, ddg = search
    from aura.tools.egress import Grant

    hidden = "weather\u200bLondon"
    with pytest.raises(PermissionDenied):
        web.gate.grant("a-1", "call_1", hidden)  # grant() refuses it...
    # ...and search() refuses it even if a grant for it existed (defence in depth).
    web.gate._grants["call_1"] = Grant("a-1", "call_1", hidden)
    with pytest.raises(PermissionDenied):
        web.search("weather\u200bLondon", tool_call_id="call_1")
    assert tavily.queries == ddg.queries == []


def test_a_query_with_hidden_characters_gets_no_card_and_is_not_sent(settings):
    aura, tavily, ddg = gated_aura(settings, tool_turn(call("web_search", query="weather\u200bLondon")), AIMessage("Sorry."))
    events = list(aura.stream("Weather in London?"))
    assert not any(isinstance(e, ApprovalNeeded) for e in events)
    (refused,) = results(aura)
    assert refused.text.startswith("Not searched: the query has characters that can't be shown") and "U+200B" in refused.text
    assert tavily.queries == ddg.queries == []


# ── End to end: every search waits for its own card ─────────────────────────────────────────


def test_every_search_waits_for_a_card_even_without_private_data(settings):
    aura, tavily, _ = gated_aura(settings, tool_turn(call("web_search", query="weather London")), AIMessage("Sunny."))
    events = list(aura.stream("Weather in London?"))
    assert isinstance(events[-1], ApprovalNeeded) and tavily.queries == []
    list(aura.resume([True]))
    assert tavily.queries == ["weather London"]
    assert aura.toolbox.search.gate.pending() == 0


def test_a_denied_search_sends_nothing_and_leaves_no_grant(settings):
    aura, tavily, ddg = gated_aura(settings, tool_turn(call("web_search", query="my secret key is hunter2")), AIMessage("OK."))
    list(aura.stream("Search for my secret key."))
    list(aura.resume([False]))
    assert tavily.queries == ddg.queries == []
    assert aura.toolbox.search.gate.pending() == 0


def test_the_same_query_twice_gets_two_cards(settings):
    aura, tavily, _ = gated_aura(settings,
                                 tool_turn(call("web_search", query="weather London")), AIMessage("Sunny."),
                                 tool_turn(call("web_search", query="weather London")), AIMessage("Still sunny."))  # fmt: skip
    assert isinstance(list(aura.stream("Weather in London?"))[-1], ApprovalNeeded)
    list(aura.resume([True]))
    assert isinstance(list(aura.stream("And now?"))[-1], ApprovalNeeded)  # no remembered consent
    assert tavily.queries == ["weather London"]
    list(aura.resume([True]))
    assert tavily.queries == ["weather London", "weather London"]


def test_each_search_in_one_step_is_decided_on_its_own(settings):
    aura, tavily, _ = gated_aura(settings,
                                 tool_turn(call("web_search", query="weather London"), call("web_search", query="my password")),
                                 AIMessage("Done."))  # fmt: skip
    card = list(aura.stream("Two searches."))[-1]
    assert isinstance(card, ApprovalNeeded) and len(card.actions) == 2
    list(aura.resume([True, False]))
    assert tavily.queries == ["weather London"]


# ── Zero egress: three guards, because DuckDuckGo's client (primp) networks in Rust ─────────


class Egress(Exception):
    """Raised by a guard: a connection attempt that was not meant to happen."""


@pytest.fixture
def no_egress(monkeypatch):
    """Records (and blocks) every network attempt the search backends could make:
    Python sockets (Tavily via requests; httpx) and primp, ddgs's native HTTP client, which opens
    its own sockets and resolves DNS outside Python's socket module."""
    import socket

    import primp

    attempts: list[str] = []

    def blocked(kind):
        def guard(*args, **kwargs):
            attempts.append(kind)
            raise Egress(kind)
        return guard

    monkeypatch.setattr(socket, "getaddrinfo", blocked("dns"))
    monkeypatch.setattr(socket, "create_connection", blocked("tcp"))
    monkeypatch.setattr(socket.socket, "connect", blocked("tcp"))
    monkeypatch.setattr(socket.socket, "connect_ex", blocked("tcp"))
    monkeypatch.setattr(primp, "Client", blocked("primp"))
    if hasattr(primp, "AsyncClient"):
        monkeypatch.setattr(primp, "AsyncClient", blocked("primp"))
    return attempts


def real_backends_aura(settings, *script):
    """An Aura with the real Tavily and DuckDuckGo clients (a fake key): only the guards stop them."""
    aura, _ = make_aura(settings, *script, duckduckgo=duckduckgo_backend, tavily_api_key="tvly-" + "not-a-real-key")
    return aura


def test_the_guards_catch_each_backend(settings, no_egress):
    # The control: with the card approved, each real backend tries to connect and a guard sees
    # it. Without this, an empty `attempts` below could just mean a guard that never fires.
    aura = real_backends_aura(settings, tool_turn(call("web_search", query="weather London")), AIMessage("Sorry."))
    list(aura.stream("Weather in London?"))
    list(aura.resume([True]))
    assert "dns" in no_egress or "tcp" in no_egress  # Tavily, through Python's socket module
    assert "primp" in no_egress  # the DuckDuckGo fallback, through primp


def test_zero_egress_while_the_card_waits_and_after_a_deny(settings, no_egress):
    aura = real_backends_aura(settings, tool_turn(call("web_search", query="my secret key is sk-" + "abc123")),
                              AIMessage("I didn't search."))  # fmt: skip
    events = list(aura.stream("Search for my secret key."))
    assert isinstance(events[-1], ApprovalNeeded)
    assert no_egress == []  # halted at the card: nothing tried to connect
    list(aura.resume([False]))
    assert no_egress == []  # denied: still nothing
    assert not any(e["event"] == "search_fallback" for e in aura.toolbox.audit.entries())


def test_zero_egress_for_a_direct_call_without_a_card(settings, no_egress):
    aura = real_backends_aura(settings)
    (tool,) = [t for t in aura.toolbox.all_tools() if t.name == "web_search"]
    msg = tool.invoke({"type": "tool_call", "name": "web_search", "args": {"query": "my secret key"}, "id": "call_x"})
    assert msg.text.startswith("Not searched") and no_egress == []


def test_only_gated_tools_get_grants():
    gate = EgressGate()
    gate.grant_for("slack_send", {"text": "hi"}, "a-1", "call_1")  # R1 card, not an egress grant
    gate.grant_for("web_search", {"query": "q"}, None, "call_2")  # no approval: nothing
    assert gate.pending() == 0
    gate.grant_for("web_search", {"query": "q"}, "a-1", "call_3")
    assert gate.consume("call_3", "q").approval_id == "a-1"


def test_a_settled_turn_leaves_no_grant(settings):
    # Approved, then stopped before the search ran: _settle closes the call and drops its grant.
    aura, tavily, _ = gated_aura(settings, tool_turn(call("web_search", query="weather London")), AIMessage("Sunny."))
    list(aura.stream("Weather in London?"))
    (tool_call,) = aura._dangling(aura._agent.get_state(aura._config))
    aura.toolbox.search.gate.grant("a-1", tool_call["id"], "weather London")  # as resume() would
    aura._settle()
    assert aura.toolbox.search.gate.pending() == 0 and tavily.queries == []
