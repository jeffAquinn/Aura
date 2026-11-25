"""Model role preferences: local by default, cloud credentials separate from Claude Code."""

from __future__ import annotations

from fastapi.testclient import TestClient
import httpx
import pytest

from aura.brain import build_model
from aura.model_routing import ROLES, ModelChoice, ModelRouting, infer_role
from aura import server as server_module
from aura.server import create_app
from conftest import make_aura


TOKEN = "t0ken-" + "x" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class NoopKeeper:
    def loaded(self): return None
    def warm_soon(self): pass


def choices(model: str = "qwen3:8b") -> dict[str, ModelChoice]:
    return {role: ModelChoice("ollama", model) for role in ROLES}


def test_routing_defaults_every_role_to_the_configured_local_model(settings):
    routing = ModelRouting(settings)
    assert routing.public() == {role: {"provider": "ollama", "model": settings.model} for role in ROLES}


def test_routing_persists_validated_role_choices(settings):
    routing = ModelRouting(settings)
    saved = choices()
    saved["deep_research"] = ModelChoice("anthropic", "claude-sonnet-4-6")
    routing.replace(saved)
    assert ModelRouting(settings).choice("deep_research") == saved["deep_research"]
    assert (settings.data_dir / "model_routing.json").is_file()


def test_task_words_choose_the_corresponding_model_role():
    assert infer_role("Find the latest weather with a web search") == "web_search"
    assert infer_role("Research this and compare sources") == "deep_research"
    assert infer_role("Refactor this code") == "coding"
    assert infer_role("Hello Aura") == "chat"


def test_models_api_saves_all_roles_and_switches_before_a_turn(settings):
    aura, _ = make_aura(settings)
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    body = {"roles": {role: {"provider": "ollama", "model": "qwen3:8b"} for role in ROLES}}
    updated = client.put("/v1/models", json=body, headers=AUTH)
    assert updated.status_code == 200 and updated.json()["roles"] == body["roles"]
    reply = client.post("/v1/chat", json={"text": "Please refactor this code."}, headers=AUTH)
    assert reply.status_code == 200
    assert aura.active_model == ModelChoice("ollama", "qwen3:8b")


def test_selecting_claude_without_an_api_key_refuses_before_the_turn(settings):
    with pytest.raises(ValueError, match="Anthropic API key"):
        build_model(settings, ModelChoice("anthropic", "claude-sonnet-4-6"))


def test_user_confirmed_ollama_pull_refreshes_models_and_can_be_selected(settings, monkeypatch):
    installed = ["qwen3:8b"]
    requests = []

    def get(url, **kwargs):
        assert url == "http://localhost:11434/api/tags"
        return httpx.Response(200, json={"models": [{"name": name} for name in installed]})

    def post(url, **kwargs):
        requests.append((url, kwargs))
        assert url == "http://localhost:11434/api/pull"
        assert kwargs["json"] == {"name": "phi4:latest", "stream": False}
        installed.append("phi4:latest")
        return httpx.Response(200, json={"status": "success"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(server_module.httpx, "get", get)
    monkeypatch.setattr(server_module.httpx, "post", post)
    aura, _ = make_aura(settings)
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")

    pulled = client.post("/v1/models/pull", json={"model": "phi4:latest"}, headers=AUTH)
    assert pulled.status_code == 200 and "phi4:latest" in pulled.json()["ollama_models"]
    assert len(requests) == 1

    body = {"roles": {role: {"provider": "ollama", "model": "phi4:latest"} for role in ROLES}}
    assert client.put("/v1/models", json=body, headers=AUTH).status_code == 200
    reply = client.post("/v1/chat", json={"text": "Hello", "model": {"provider": "ollama", "model": "phi4:latest"}}, headers=AUTH)
    assert reply.status_code == 200 and aura.active_model == ModelChoice("ollama", "phi4:latest")


def test_local_model_pull_rejects_unsafe_names_without_contacting_ollama(settings, monkeypatch):
    monkeypatch.setattr(server_module.httpx, "post", lambda *args, **kwargs: pytest.fail("must not contact Ollama"))
    aura, _ = make_aura(settings)
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    response = client.post("/v1/models/pull", json={"model": "../../not-a-model"}, headers=AUTH)
    assert response.status_code == 422 and "unsupported characters" in response.json()["error"]
