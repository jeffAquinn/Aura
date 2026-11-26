"""The Egress Gatekeeper's approval card: the explicit warning, and the query exactly as it will be
sent (tools/egress.py describe_search; Slack rendering in connections/remote.py)."""

from __future__ import annotations

import json

import pytest
from conftest import Recorder, call, make_aura, tool_turn
from langchain_core.messages import AIMessage
from test_connections import connected_aura
from test_governor import env  # noqa: F401 (fixture)
from test_slack_chat import remote_chat

from aura.brain import ApprovalNeeded
from aura.connections.remote import slack_card
from aura.tools.egress import EGRESS_WARNING, QUERY_LINE, describe_search

WARNING = "Warning: This action will send a query to the internet. This request leaves your local Mac mini."
QUERIES = ["weather London", "  café Zürich 東京 — naïve 🌧  ", "a\\b \"quoted\" 'single' `tick` *star* _under_ <b>x</b>"]


def card_for(settings, query: str, tavily_key: str | None = "tvly-test"):
    backend = Recorder()
    aura, _ = make_aura(settings, tool_turn(call("web_search", query=query)), AIMessage("Done."),
                        tavily=backend if tavily_key else None, duckduckgo=backend, tavily_api_key=tavily_key)  # fmt: skip
    events = list(aura.stream("Search the web for it."))
    assert isinstance(events[-1], ApprovalNeeded)
    return aura, events[-1], backend.queries


def test_the_warning_is_the_dossiers_exact_words():
    assert EGRESS_WARNING == WARNING


@pytest.mark.parametrize("query", QUERIES)
def test_the_card_shows_the_warning_and_the_exact_query(settings, query):
    aura, card, sent = card_for(settings, query)
    (action,) = card.actions
    lines = action.description.split("\n")
    assert lines[0] == WARNING
    assert lines[1] == f"search the web (Tavily; DuckDuckGo if Tavily fails) for this exact query ({len(query)} characters):"
    assert lines[2] == QUERY_LINE + query  # bit for bit: not quoted, trimmed or escaped
    assert lines[2][len(QUERY_LINE):].encode() == query.encode()
    assert action.args["query"] == query and sent == []
    list(aura.resume([True]))
    assert sent == [query]  # what was sent is what the card showed


def test_the_card_names_duckduckgo_alone_when_there_is_no_tavily_key(settings):
    _, card, _ = card_for(settings, "weather London", tavily_key=None)
    assert card.actions[0].description.split("\n")[1].startswith("search the web (DuckDuckGo) for this exact query")


def test_the_card_text_survives_the_server_unchanged(settings):
    # The app receives the card as JSON (server.py) and shows it as plain Text, not Markdown.
    query = QUERIES[1]
    _, card, _ = card_for(settings, query)
    wire = json.loads(json.dumps({"description": card.actions[0].description}))
    assert QUERY_LINE + query in wire["description"].split("\n")


def test_describe_search_adds_the_private_data_note_only_when_asked():
    assert "private data" not in describe_search("q", "DuckDuckGo")
    assert describe_search("q", "DuckDuckGo", private=True).endswith(
        "This turn read your private data: check the query contains none of it.")


# ── The same card in Slack: the query in a code block, taken from the tool call ─────────────


def test_slack_shows_the_warning_and_the_query_in_a_code_block():
    query = QUERIES[2]  # Markdown characters that Slack would otherwise render
    text = slack_card("web_search", {"query": query}, describe_search(query, "DuckDuckGo", private=True))
    lines = text.split("\n")
    assert lines[0] == "• " + WARNING
    assert lines[1] == f"> search the web (DuckDuckGo) for this exact query ({len(query)} characters):"
    assert lines[2:5] == ["```", query, "```"]  # exact: not stripped, quoted or rendered
    assert lines[5] == "> This turn read your private data: check the query contains none of it."


def test_slack_keeps_surrounding_spaces_inside_the_code_block():
    query = QUERIES[1]
    assert f"```\n{query}\n```" in slack_card("web_search", {"query": query}, describe_search(query, "DuckDuckGo"))


def test_a_query_slack_cant_show_exactly_cant_be_approved_from_slack(env, tmp_path):  # noqa: F811
    query = "how to write ``` fences"
    aura, _, searched = connected_aura(env, tool_turn(call("web_search", query=query)), AIMessage("Done."))
    remote, posted, dm = remote_chat(aura, env, tmp_path)
    remote.handle(dm("Search the web for how to write fences."))
    card = posted[-1][1]
    assert WARNING in card and "can't be approved from Slack" in card and searched == []
    remote.handle(dm("allow"))
    assert "nothing in it can be approved here" in posted[-1][1] and searched == []  # still waiting, nothing sent
    remote.handle(dm("deny"))
    assert searched == []


def test_slack_approves_a_search_end_to_end(env, tmp_path):  # noqa: F811
    query = "weather London"
    aura, _, searched = connected_aura(env, tool_turn(call("web_search", query=query)), AIMessage("Sunny."))
    remote, posted, dm = remote_chat(aura, env, tmp_path)
    remote.handle(dm("Search the web for the weather in London."))
    assert f"```\n{query}\n```" in posted[-1][1] and searched == []
    remote.handle(dm("allow"))
    assert searched == [query] and posted[-1][1] == "Sunny."


def test_a_query_slack_would_mask_cant_be_approved_from_slack(env, tmp_path):  # noqa: F811
    # Slack replies are masked: a token in the query would show as "[secret hidden]" on the card
    # while the real token was sent. So Slack can't approve it.
    from aura.masking import Masker

    query = "is xoxb-" + "1234567890-abcdefghijkl leaked"
    aura, _, searched = connected_aura(env, tool_turn(call("web_search", query=query)), AIMessage("Done."))
    remote, posted, dm = remote_chat(aura, env, tmp_path)
    remote._masker = Masker(list)
    assert remote._masker.mask(query) != query  # the premise: Slack would hide part of it
    remote.handle(dm("Search the web for whether my token leaked."))
    remote.handle(dm("allow"))
    assert "nothing in it can be approved here" in posted[-1][1] and searched == []
    remote.handle(dm("deny"))
    assert searched == []
