"""llama.cpp provider: a local model served by the user's own llama-server, never a remote one."""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient
import httpx
import pytest

from aura.brain import build_model
from aura.model_routing import ROLES, ModelChoice, ModelRouting, llamacpp_root
from aura import server as server_module
from aura.server import ActiveModelKeeper, create_app
from conftest import make_aura
from test_model_routing import AUTH, TOKEN, NoopKeeper, choices


BONSAI = ModelChoice("llamacpp", "bonsai-2.5-27b")


def served(*ids: str):
    """A fake llama-server /v1/models answer."""
    def get(url, **kwargs):
        assert url == "http://127.0.0.1:8080/v1/models"
        return httpx.Response(200, json={"object": "list", "data": [{"id": i} for i in ids]}, request=httpx.Request("GET", url))
    return get


def test_llamacpp_is_a_valid_local_provider_and_persists(settings):
    routing = ModelRouting(settings)
    saved = choices()
    saved["chat"] = BONSAI.validate()
    routing.replace(saved)
    assert ModelRouting(settings).choice("chat") == BONSAI


@pytest.mark.parametrize("url", ["https://127.0.0.1:8080/v1", "http://example.com:8080/v1", "http://10.0.0.5:8080/v1"])
def test_llamacpp_url_must_stay_on_this_mac(settings, url):
    with pytest.raises(ValueError, match="127.0.0.1 or localhost"):
        llamacpp_root(replace(settings, llamacpp_url=url))
    with pytest.raises(ValueError):
        build_model(replace(settings, llamacpp_url=url), BONSAI)


def test_build_model_points_a_llamacpp_choice_at_llama_server(settings):
    llm = build_model(settings, BONSAI)
    assert str(llm.openai_api_base) == "http://127.0.0.1:8080/v1"
    assert llm.model_name == "bonsai-2.5-27b"


def test_llamacpp_models_lists_served_aliases_and_drops_path_names(settings, monkeypatch):
    monkeypatch.setattr(server_module.httpx, "get", served("bonsai-2.5-27b", "/Users/x/models/a.gguf"))
    assert server_module.llamacpp_models(settings) == ["bonsai-2.5-27b"]


def test_llamacpp_models_is_empty_when_llama_server_is_down_or_loading(settings, monkeypatch):
    def down(url, **kwargs):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(server_module.httpx, "get", down)
    assert server_module.llamacpp_models(settings) == []
    loading = lambda url, **kwargs: httpx.Response(503, json={"error": "Loading model"}, request=httpx.Request("GET", url))
    monkeypatch.setattr(server_module.httpx, "get", loading)
    assert server_module.llamacpp_models(settings) == []


def test_automatic_routing_uses_a_served_llamacpp_role(settings):
    routing = ModelRouting(settings)
    saved = choices()
    saved["chat"] = BONSAI
    routing.replace(saved)
    assert server_module.automatic_model_choice(routing, "chat", set(), False, llamacpp={"bonsai-2.5-27b"}) == (BONSAI, None)


def test_automatic_routing_falls_back_locally_when_llama_server_is_down(settings):
    routing = ModelRouting(settings)
    saved = {role: BONSAI for role in ROLES}
    saved["startup"] = ModelChoice("ollama", "qwen3:8b")
    routing.replace(saved)
    choice, notice = server_module.automatic_model_choice(routing, "chat", {"qwen3:8b"}, True)
    assert choice == ModelChoice("ollama", "qwen3:8b")
    assert "llama-server is not serving the chat model 'bonsai-2.5-27b'" in notice


def test_automatic_routing_never_escalates_a_down_llama_server_to_cloud(settings):
    routing = ModelRouting(settings)
    saved = {role: BONSAI for role in ROLES}
    saved["startup"] = ModelChoice("anthropic", "claude-sonnet-5-5")
    routing.replace(saved)
    with pytest.raises(ValueError, match="llama-server is not serving it"):
        server_module.automatic_model_choice(routing, "chat", set(), True)


def test_models_api_saves_and_offers_a_llamacpp_role(settings):
    aura, _ = make_aura(settings)
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    roles = {role: {"provider": "ollama", "model": "qwen3:8b"} for role in ROLES} | {"chat": BONSAI.public()}
    saved = client.put("/v1/models", json={"roles": roles}, headers=AUTH)
    assert saved.status_code == 200 and saved.json()["roles"]["chat"] == BONSAI.public()
    assert saved.json()["llamacpp_models"] == ["bonsai-2.5-27b"]


def test_a_picked_llamacpp_model_runs_the_turn(settings):
    ModelRouting(settings).replace(choices() | {"chat": BONSAI})  # a test double's catalog is its saved roles
    aura, _ = make_aura(settings)
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    reply = client.post("/v1/chat", json={"text": "Hello", "model": BONSAI.public()}, headers=AUTH)
    assert reply.status_code == 200
    assert aura.active_model == BONSAI


def llama_server(health: int, *ids: str):
    """A fake llama-server answering /health and /v1/models."""
    def get(url, **kwargs):
        request = httpx.Request("GET", url)
        if url.endswith("/health"):
            return httpx.Response(health, request=request)
        if health != 200:
            return httpx.Response(health, request=request)
        return httpx.Response(200, json={"data": [{"id": i} for i in ids]}, request=request)
    return get


def test_health_reports_llama_server_readiness_for_the_active_model(settings, monkeypatch):
    aura, _ = make_aura(settings)
    aura.select_model(BONSAI)
    keeper = ActiveModelKeeper(aura)
    monkeypatch.setattr(server_module.httpx, "get", llama_server(200, "bonsai-2.5-27b"))
    assert keeper.loaded() is True
    monkeypatch.setattr(server_module.httpx, "get", llama_server(200, "qwen3-8b"))  # serving another model
    assert keeper.loaded() is None
    monkeypatch.setattr(server_module.httpx, "get", llama_server(503))  # still loading
    assert keeper.loaded() is None


def test_health_clears_the_startup_problem_once_llama_server_is_ready(settings, monkeypatch):
    ModelRouting(settings).replace({role: BONSAI for role in ROLES})
    monkeypatch.setattr(server_module, "llamacpp_models", lambda _settings: [])  # still loading at launch
    aura = server_module.Aura(settings=settings)
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    assert client.get("/v1/health", headers=AUTH).json()["runtime_health"]["state"] == "unavailable"

    monkeypatch.setattr(server_module, "llamacpp_models", lambda _settings: ["bonsai-2.5-27b"])
    health = client.get("/v1/health", headers=AUTH).json()["runtime_health"]
    assert "llama-server is not serving" not in health["detail"]
    assert client.get("/v1/models", headers=AUTH).json()["startup_model_problem"] is None
