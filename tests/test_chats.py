"""Saved chats (vault Chats/): kept after every turn, listed newest first, opened again to carry on."""

from __future__ import annotations

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from aura.chats import ChatArchive, title_of
from aura.server import create_app
from conftest import make_aura
from test_model_routing import AUTH, TOKEN, NoopKeeper


def test_a_chat_round_trips_through_a_readable_markdown_file(tmp_path):
    archive = ChatArchive(tmp_path, mask=lambda t: t.replace("sk-" + "secret", "[redacted]"))
    messages = [{"role": "user", "text": "Plan my week.\nThanks!"},
                {"role": "aura", "text": "## Monday\n- gym\n\n<!-- aura:you --> not a real marker"},
                {"role": "user", "text": "My key is sk-" + "secret"}]
    path = archive.save("c1", messages)
    text = path.read_text()
    assert path.parent.name == "Chats" and "Plan my week" in path.name
    assert text.startswith("---\naura-chat: c1\ntitle: Plan my week.\n") and "**You**" in text and "**Aura**" in text
    assert "[redacted]" in text and "sk-" + "secret" not in text
    loaded = archive.load("c1")
    assert [m["role"] for m in loaded] == ["user", "aura", "user"]
    assert loaded[0]["text"] == "Plan my week.\nThanks!" and loaded[1]["text"].startswith("## Monday\n- gym")
    assert "<!-- aura :you -->" in loaded[1]["text"]  # a message can't fake a marker


def test_saving_again_updates_the_same_file_and_the_list_is_newest_first(tmp_path):
    archive = ChatArchive(tmp_path)
    first = archive.save("a", [{"role": "user", "text": "First chat"}])
    archive.save("b", [{"role": "user", "text": "Second chat"}])
    again = archive.save("a", [{"role": "user", "text": "First chat"}, {"role": "aura", "text": "Reply"}])
    assert again == first and len(list((tmp_path / "Chats").glob("*.md"))) == 2
    assert [c["id"] for c in archive.list()] == ["a", "b"]
    assert archive.save("empty", [{"role": "aura", "text": "Hello"}]) is None  # nothing from the user: not saved
    assert archive.load("missing") is None


def test_titles_are_the_first_line_shortened():
    assert title_of([{"role": "user", "text": "  Hello   there\nmore"}]) == "Hello there"
    assert len(title_of([{"role": "user", "text": "x" * 200}])) == 60


def test_new_chat_keeps_the_old_one_and_opening_it_carries_on(settings):
    aura, model = make_aura(settings, AIMessage("Sunny in London."), AIMessage("Noted."), AIMessage("It was sunny."))
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    assert client.post("/v1/chat", json={"text": "Weather in London?"}, headers=AUTH).status_code == 200
    listed = client.get("/v1/chats", headers=AUTH).json()
    [weather] = listed["chats"]
    assert weather["title"] == "Weather in London?" and listed["current"] == weather["id"]

    assert client.post("/v1/conversation/new", json={}, headers=AUTH).status_code == 204
    client.post("/v1/chat", json={"text": "Remember milk."}, headers=AUTH)
    titles = [c["title"] for c in client.get("/v1/chats", headers=AUTH).json()["chats"]]
    assert titles == ["Remember milk.", "Weather in London?"]  # the old chat wasn't forgotten

    opened = client.post("/v1/chats/open", json={"id": weather["id"]}, headers=AUTH)
    assert opened.status_code == 200
    assert [m["text"] for m in opened.json()["messages"]] == ["Weather in London?", "Sunny in London."]
    client.post("/v1/chat", json={"text": "What did you say about it?"}, headers=AUTH)
    seen = [m.content for m in model.calls[-1] if hasattr(m, "content")]
    assert "Weather in London?" in seen and "Sunny in London." in seen  # the model carries on from it
    assert client.get("/v1/chats", headers=AUTH).json()["current"] == weather["id"]
    assert client.post("/v1/chats/open", json={"id": "nope"}, headers=AUTH).status_code == 404
    assert client.post("/v1/chats/open", json={"id": "../x"}, headers=AUTH).status_code == 422
