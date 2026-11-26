"""Model role preferences: local by default, cloud credentials separate from Claude Code."""

from __future__ import annotations

from fastapi.testclient import TestClient
import httpx
import pytest

from aura.brain import build_model
from aura.model_routing import ROLES, ModelChoice, ModelRouting, SleepMode, infer_role
from aura import server as server_module
from aura.server import create_app
from conftest import ScriptedModel, make_aura


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
    assert routing.coding_engine() == "claude"


def test_routing_persists_validated_role_choices(settings):
    routing = ModelRouting(settings)
    saved = choices()
    saved["deep_research"] = ModelChoice("anthropic", "claude-sonnet-4-6")
    routing.replace(saved)
    assert ModelRouting(settings).choice("deep_research") == saved["deep_research"]
    assert (settings.data_dir / "model_routing.json").is_file()


def test_routing_persists_the_account_backed_coding_engine_separately(settings):
    routing = ModelRouting(settings)
    routing.replace_coding_engine("codex")

    assert ModelRouting(settings).coding_engine() == "codex"


def test_sleep_mode_persists_separately_from_role_choices(settings):
    routing = ModelRouting(settings)
    routing.replace_sleep_mode(SleepMode(True, "qwen3:8b"))
    assert ModelRouting(settings).sleep_mode() == SleepMode(True, "qwen3:8b")


def test_task_words_choose_the_corresponding_model_role():
    assert infer_role("Find the latest weather with a web search") == "web_search"
    assert infer_role("Research this and compare sources") == "deep_research"
    assert infer_role("Refactor this code") == "coding"
    assert infer_role("Hello Aura") == "chat"


def test_automatic_model_falls_back_to_a_verified_general_local_choice(settings):
    routing = ModelRouting(settings)
    saved = choices("missing:latest")
    saved["chat"] = ModelChoice("ollama", "qwen3:8b")
    saved["startup"] = ModelChoice("ollama", "llama3.2:3b")
    routing.replace(saved)

    choice, notice = server_module.automatic_model_choice(routing, "coding", {"qwen3:8b"}, False)

    assert choice == ModelChoice("ollama", "qwen3:8b")
    assert notice == "Auto selected local model 'qwen3:8b' because the coding model 'missing:latest' is not installed."


def test_automatic_model_never_escalates_a_missing_local_role_to_cloud(settings):
    routing = ModelRouting(settings)
    saved = choices("missing:latest")
    saved["chat"] = ModelChoice("anthropic", "claude-sonnet-5-5")
    routing.replace(saved)

    with pytest.raises(ValueError, match="not installed"):
        server_module.automatic_model_choice(routing, "coding", set(), True)


def test_automatic_model_skips_a_cloud_model_with_a_recent_capacity_refusal(settings):
    class ExhaustedCloudModel(RuntimeError):
        status_code = 429

    routing = ModelRouting(settings)
    saved = choices("qwen3:8b")
    saved["chat"] = ModelChoice("anthropic", "claude-fable-5-2")
    routing.replace(saved)
    availability = server_module.CloudModelAvailability(settings)
    availability.record_capacity_refusal(saved["chat"], ExhaustedCloudModel("quota exhausted"))

    choice, notice = server_module.automatic_model_choice(
        routing, "chat", {"qwen3:8b"}, True, availability.unavailable_models(),
    )

    assert choice == ModelChoice("ollama", "qwen3:8b")
    assert notice == "Auto selected local model 'qwen3:8b' because the chat cloud model recently reached its account limit."
    # The cache survives a server restart but is deliberately temporary (it has an expiry).
    assert "claude-fable-5-2" in server_module.CloudModelAvailability(settings).unavailable_models()


def test_cloud_capacity_detection_only_marks_account_limit_responses(settings):
    class StatusError(RuntimeError):
        def __init__(self, status_code, message):
            super().__init__(message)
            self.status_code = status_code

    assert server_module.is_cloud_capacity_refusal(StatusError(429, "too many requests"))
    assert server_module.is_cloud_capacity_refusal(StatusError(402, "payment required"))
    assert server_module.is_cloud_capacity_refusal(RuntimeError("Your credit balance is too low."))
    assert not server_module.is_cloud_capacity_refusal(StatusError(400, "max_tokens must be positive"))


def test_auto_learns_a_cloud_capacity_refusal_before_the_next_turn(settings):
    class QuotaError(RuntimeError):
        status_code = 429

    class QuotaLimitedModel(ScriptedModel):
        def _generate(self, *args, **kwargs):
            raise QuotaError("You have reached your limit for this model.")

    aura, _ = make_aura(settings)
    aura.toolbox.secrets.set("anthropic_api_key", "sk-ant-api03-" + "a" * 40)
    broken = type(aura)(settings=aura.settings, model=QuotaLimitedModel(), toolbox=aura.toolbox)
    client = TestClient(create_app(broken, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    saved = choices("qwen3:8b")
    saved["chat"] = ModelChoice("anthropic", "claude-fable-5-2")
    assert client.put("/v1/models", json={"roles": {role: choice.public() for role, choice in saved.items()}}, headers=AUTH).status_code == 200

    first = client.post("/v1/chat", json={"text": "Hello"}, headers=AUTH)
    assert first.status_code == 200 and "no account capacity right now" in first.text

    # No second provider probe or failed cloud turn: the selected model is the local fallback.
    second = client.post("/v1/chat", json={"text": "Hello again"}, headers=AUTH)
    assert second.status_code == 200
    assert '"provider": "ollama"' in second.text
    assert "recently reached its account limit" in second.text


def test_usable_ollama_models_requires_the_runtime_readiness_probe(settings, monkeypatch):
    def ready(url, **kwargs):
        if url.endswith("/api/tags"):
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]}, request=httpx.Request("GET", url))
        assert url.endswith("/api/ps") and kwargs["timeout"] == 2
        return httpx.Response(200, json={"models": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(server_module.httpx, "get", ready)
    assert server_module.usable_ollama_models(settings) == {"qwen3:8b"}

    monkeypatch.setattr(server_module.httpx, "get", lambda url, **kwargs: httpx.Response(
        503 if url.endswith("/api/ps") else 200,
        json={"models": [{"name": "qwen3:8b"}]}, request=httpx.Request("GET", url)))
    assert server_module.usable_ollama_models(settings) == set()


def test_smallest_usable_ollama_model_uses_downloaded_size_and_requires_a_ready_runtime(settings, monkeypatch):
    def ready(url, **kwargs):
        if url.endswith("/api/tags"):
            return httpx.Response(200, json={"models": [
                {"name": "large:latest", "size": 9_000},
                {"name": "small:latest", "size": 1_000},
                {"name": "unknown:latest"},
            ]}, request=httpx.Request("GET", url))
        assert url.endswith("/api/ps") and kwargs["timeout"] == 2
        return httpx.Response(200, json={"models": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(server_module.httpx, "get", ready)
    sizes = server_module.usable_ollama_model_sizes(settings)

    assert sizes == {"large:latest": 9_000, "small:latest": 1_000, "unknown:latest": 2**63 - 1}
    assert server_module.smallest_usable_ollama_model(sizes) == ModelChoice("ollama", "small:latest")


def test_developer_handoff_preloads_the_smallest_ready_local_model(settings, monkeypatch):
    aura, _ = make_aura(settings)
    # The direct handoff cannot need an LLM before its send approval card.  Treat this test's
    # server as production routing so it exercises the real Ollama-size selection branch.
    warm = []
    monkeypatch.setattr(server_module, "usable_ollama_model_sizes", lambda _settings: {
        "small:latest": 1_000, "large:latest": 9_000,
    })
    monkeypatch.setattr(server_module.ModelKeeper, "warm_soon", lambda self: warm.append((self.model, self.keep_alive)))
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    aura._injected_model = False

    reply = client.post("/v1/chat", json={"text": "Inspect this code without editing it.", "developer": "claude"}, headers=AUTH)

    assert reply.status_code == 200
    assert aura.active_model == ModelChoice("ollama", "small:latest")
    assert warm == [("small:latest", settings.model_keep_alive)]
    assert "preload the smallest available local model 'small:latest' while Claude Code works" in reply.text


def test_automatic_local_chat_does_not_preload_a_competing_empty_generation(settings, monkeypatch):
    aura, _ = make_aura(settings)
    warm = []
    monkeypatch.setattr(server_module, "usable_ollama_models", lambda _settings: {settings.model})
    monkeypatch.setattr(server_module.ModelKeeper, "warm_soon", lambda self: warm.append((self.model, self.keep_alive)))
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")

    # Exercise production automatic routing while retaining the scripted test model for the
    # actual reply. The only production-only branch here is the native Ollama prewarm.
    aura._injected_model = False
    aura.select_model = lambda choice: setattr(aura, "active_model", choice)  # type: ignore[method-assign]
    reply = client.post("/v1/chat", json={"text": "Hello"}, headers=AUTH)

    assert reply.status_code == 200
    assert aura.active_model == ModelChoice("ollama", settings.model)
    assert warm == []


def test_developer_handoff_refuses_before_sending_when_no_local_model_is_usable(settings, monkeypatch):
    aura, _ = make_aura(settings)
    monkeypatch.setattr(server_module, "usable_ollama_model_sizes", lambda _settings: {})
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    aura._injected_model = False

    reply = client.post("/v1/chat", json={"text": "Inspect this code without editing it.", "developer": "codex"}, headers=AUTH)

    assert reply.status_code == 409
    assert "need a usable local Ollama model" in reply.json()["error"]


def test_automatic_model_refuses_an_unusable_role_before_opening_a_turn(settings, monkeypatch):
    routing = ModelRouting(settings)
    routing.replace(choices("missing:latest"))
    monkeypatch.setattr(server_module, "installed_ollama_models", lambda _settings: [])
    aura = server_module.Aura(settings=settings)
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")

    response = client.post("/v1/chat", json={"text": "Hello"}, headers=AUTH)

    assert response.status_code == 409
    assert "Auto mode cannot use the chat model 'missing:latest' because it is not installed" in response.json()["error"]


def test_models_api_saves_all_roles_and_switches_before_a_turn(settings):
    aura, _ = make_aura(settings)
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    body = {"roles": {role: {"provider": "ollama", "model": "qwen3:8b"} for role in ROLES}}
    updated = client.put("/v1/models", json=body, headers=AUTH)
    assert updated.status_code == 200 and updated.json()["roles"] == body["roles"]
    reply = client.post("/v1/chat", json={"text": "Please refactor this code."}, headers=AUTH)
    assert reply.status_code == 200
    assert aura.active_model == ModelChoice("ollama", "qwen3:8b")


def test_models_api_defaults_coding_to_claude_code_and_persists_a_codex_choice(settings):
    aura, _ = make_aura(settings)
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")

    catalog = client.get("/v1/models", headers=AUTH)
    assert catalog.status_code == 200 and catalog.json()["coding_engine"] == "claude"
    updated = client.put("/v1/models/coding-engine", json={"engine": "codex"}, headers=AUTH)
    assert updated.status_code == 200 and updated.json()["coding_engine"] == "codex"

    restarted, _ = make_aura(settings)
    reloaded = TestClient(create_app(restarted, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    assert reloaded.get("/v1/models", headers=AUTH).json()["coding_engine"] == "codex"


def test_coding_requests_use_the_chat_model_for_planning_not_the_legacy_cloud_coding_model(settings):
    aura, _ = make_aura(settings)
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    saved = choices("qwen3:8b")
    saved["coding"] = ModelChoice("anthropic", "claude-fable-5-2")
    assert client.put("/v1/models", json={"roles": {role: choice.public() for role, choice in saved.items()}}, headers=AUTH).status_code == 200

    # The Developer may later be unavailable, but Auto must never select this stale cloud coding role.
    reply = client.post("/v1/chat", json={"text": "Please refactor this code."}, headers=AUTH)
    assert reply.status_code == 200
    assert aura.active_model == ModelChoice("ollama", "qwen3:8b")


def test_models_api_restores_every_role_after_a_server_restart(settings):
    aura, _ = make_aura(settings)
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    roles = choices("qwen3:8b")
    roles["coding"] = ModelChoice("ollama", "phi4:latest")
    roles["deep_research"] = ModelChoice("ollama", "gemma3:12b")
    body = {"roles": {role: choice.public() for role, choice in roles.items()}}
    assert client.put("/v1/models", json=body, headers=AUTH).status_code == 200

    restarted, _ = make_aura(settings)
    reloaded = TestClient(create_app(restarted, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")

    assert reloaded.get("/v1/models", headers=AUTH).json()["roles"] == body["roles"]
    assert restarted.active_model == roles["startup"]


def test_selecting_claude_without_an_api_key_refuses_before_the_turn(settings):
    with pytest.raises(ValueError, match="Anthropic API key"):
        build_model(settings, ModelChoice("anthropic", "claude-sonnet-4-6"))


def test_cloud_catalog_includes_current_opus_5_5_when_offline(settings):
    assert "claude-opus-5-5" in ModelRouting(settings).anthropic_models()


def test_opus_5_5_is_a_usable_anthropic_model_choice(settings):
    model = build_model(settings, ModelChoice("anthropic", "claude-opus-5-5"), api_key="sk-ant-api03-" + "a" * 40)
    assert model.model == "claude-opus-5-5"


def test_cloud_model_sync_uses_the_anthropic_catalog_and_persists_only_model_ids(settings, monkeypatch):
    calls = []

    def get(url, **kwargs):
        if url == "http://localhost:11434/api/tags":
            return httpx.Response(200, json={"models": []}, request=httpx.Request("GET", url))
        calls.append((url, kwargs))
        assert url == "https://api.anthropic.com/v1/models"
        assert kwargs["headers"]["anthropic-version"] == "2023-06-01"
        assert kwargs["headers"]["x-api-key"].startswith("sk-ant-")
        return httpx.Response(200, json={"data": [
            {"id": "claude-opus-5-5"}, {"id": "claude-sonnet-5-5"},
        ], "has_more": False}, request=httpx.Request("GET", url))

    monkeypatch.setattr(server_module.httpx, "get", get)
    aura, _ = make_aura(settings)
    aura.toolbox.secrets.set("anthropic_api_key", "sk-ant-api03-" + "a" * 40)
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")

    response = client.post("/v1/models/sync-cloud", json={}, headers=AUTH)

    assert response.status_code == 200
    assert response.json()["anthropic_models"] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert calls and "sk-ant-" not in response.text
    assert ModelRouting(settings).anthropic_models() == ("claude-opus-5-5", "claude-sonnet-5-5")


def test_cloud_model_sync_requires_an_anthropic_api_key(settings):
    aura, _ = make_aura(settings)
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    response = client.post("/v1/models/sync-cloud", json={}, headers=AUTH)
    assert response.status_code == 422 and "API key" in response.json()["error"]


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


def test_sleep_mode_keeps_only_the_chosen_local_model_resident(settings, monkeypatch):
    calls = []
    monkeypatch.setattr(server_module, "installed_ollama_models", lambda _settings: ["qwen3:8b"])
    monkeypatch.setattr(server_module.ModelKeeper, "warm_soon", lambda self: calls.append((self.model, self.keep_alive)))
    aura, _ = make_aura(settings)
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")

    enabled = client.put("/v1/models/sleep", json={"enabled": True, "model": "qwen3:8b"}, headers=AUTH)
    assert enabled.status_code == 200
    assert enabled.json()["sleep_mode"] == {"enabled": True, "model": "qwen3:8b"}
    assert calls == [("qwen3:8b", -1)]

    disabled = client.put("/v1/models/sleep", json={"enabled": False, "model": "qwen3:8b"}, headers=AUTH)
    assert disabled.status_code == 200 and calls[-1] == ("qwen3:8b", settings.model_keep_alive)
