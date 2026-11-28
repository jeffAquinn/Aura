"""llama.cpp provider: a local model in a .gguf file, served by Aura's own llama-server (aura.llama_server)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from aura.brain import build_model
from aura.llama_server import LocalModelError
from aura.model_routing import ROLES, ModelChoice, ModelRouting, llamacpp_root
from aura import server as server_module
from aura.server import ActiveModelKeeper, create_app
from conftest import make_aura
from test_model_routing import AUTH, TOKEN, NoopKeeper, choices


BONSAI = ModelChoice("llamacpp", "bonsai-2.5-27b")


def installed(settings, *files: str):
    """llama.cpp installed (a stand-in program), with these model files in the models folder."""
    settings.llamacpp_models_dir.mkdir(parents=True, exist_ok=True)
    for name in files:
        (settings.llamacpp_models_dir / name).write_bytes(b"GGUF")
    program = Path(settings.llama_server)
    program.write_text("#!/bin/sh\n")
    program.chmod(0o755)


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


def test_llamacpp_models_are_the_model_files_whether_or_not_llama_server_runs(settings):
    assert server_module.llamacpp_models(settings) == []  # llama.cpp isn't installed
    installed(settings, "bonsai-2.5-27b.gguf", "Other-7B-Q4_K_M.gguf")
    assert server_module.llamacpp_models(settings) == ["Other-7B-Q4_K_M", "bonsai-2.5-27b"]


def test_automatic_routing_uses_a_served_llamacpp_role(settings):
    routing = ModelRouting(settings)
    saved = choices()
    saved["chat"] = BONSAI
    routing.replace(saved)
    assert server_module.automatic_model_choice(routing, "chat", set(), False, llamacpp={"bonsai-2.5-27b"}) == (BONSAI, None)


def test_automatic_routing_falls_back_locally_when_a_llamacpp_model_is_missing(settings):
    routing = ModelRouting(settings)
    saved = {role: BONSAI for role in ROLES}
    saved["startup"] = ModelChoice("ollama", "qwen3:8b")
    routing.replace(saved)
    choice, notice = server_module.automatic_model_choice(routing, "chat", {"qwen3:8b"}, True)
    assert choice == ModelChoice("ollama", "qwen3:8b")
    assert "the chat llama.cpp model 'bonsai-2.5-27b' isn't available" in notice


def test_automatic_routing_never_escalates_a_missing_llamacpp_model_to_cloud(settings):
    routing = ModelRouting(settings)
    saved = {role: BONSAI for role in ROLES}
    saved["startup"] = ModelChoice("anthropic", "claude-sonnet-5-5")
    routing.replace(saved)
    with pytest.raises(ValueError, match="its file isn't in the llama.cpp models folder"):
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


class Served:
    """Stands in for Aura's LlamaServer: which model is loaded, and what was asked of it."""

    def __init__(self, models: list[str], loaded: str | None = None):
        self._models, self.model, self.touched = models, loaded, 0

    def models(self) -> list[str]:
        return self._models

    def loaded(self, model: str) -> bool:
        return self.model == model

    def touch(self) -> None:
        self.touched += 1

    def loading(self, model: str) -> bool:
        return self.model == "loading:" + model


def test_health_reports_whether_aura_s_llama_server_has_the_active_model_loaded(settings):
    aura, _ = make_aura(settings)
    aura.select_model(BONSAI)
    keeper = ActiveModelKeeper(aura)
    assert keeper.loaded() is None  # Aura can't run llama.cpp models here
    aura.toolbox.local_server = Served(["bonsai-2.5-27b"], loaded="bonsai-2.5-27b")
    assert keeper.loaded() is True
    aura.toolbox.local_server = Served(["bonsai-2.5-27b"])  # not loaded: it loads on the next turn
    assert keeper.loaded() is False
    aura.toolbox.local_server = Served([])  # the model file is gone
    assert keeper.loaded() is None


def test_a_finished_turn_restarts_the_llamacpp_idle_time(settings):
    aura, _ = make_aura(settings)
    aura.select_model(BONSAI)
    aura.toolbox.local_server = served = Served(["bonsai-2.5-27b"], loaded="bonsai-2.5-27b")
    ActiveModelKeeper(aura).warm_soon()
    assert served.touched == 1


def test_health_clears_the_startup_problem_once_llama_server_is_ready(settings, monkeypatch):
    ModelRouting(settings).replace({role: BONSAI for role in ROLES})
    monkeypatch.setattr(server_module, "llamacpp_models", lambda _settings: [])  # still loading at launch
    aura = server_module.Aura(settings=settings)
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    assert client.get("/v1/health", headers=AUTH).json()["runtime_health"]["state"] == "unavailable"

    monkeypatch.setattr(server_module, "llamacpp_models", lambda _settings: ["bonsai-2.5-27b"])
    health = client.get("/v1/health", headers=AUTH).json()["runtime_health"]
    assert "isn't available" not in health["detail"]
    assert client.get("/v1/models", headers=AUTH).json()["startup_model_problem"] is None


class Preparing:
    """Records what each turn asked Aura's llama-server to prepare; can fail like a real load."""

    def __init__(self, fail: str | None = None):
        self.prepared: list[ModelChoice] = []
        self.fail = fail

    def prepare(self, choice: ModelChoice, cancel=None) -> None:
        self.prepared.append(choice)
        if self.fail:
            raise LocalModelError(self.fail)


def test_every_turn_prepares_its_model_and_a_load_problem_is_shown(settings):
    from test_server import events

    ModelRouting(settings).replace(choices() | {"startup": BONSAI})  # Chat stays qwen3:8b (Ollama)
    aura, _ = make_aura(settings)
    aura.toolbox.local_server = preparing = Preparing()
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    assert client.post("/v1/chat", json={"text": "Hello", "model": BONSAI.public()}, headers=AUTH).status_code == 200
    qwen = ModelChoice("ollama", "qwen3:8b")
    assert client.post("/v1/chat", json={"text": "Hi"}, headers=AUTH).status_code == 200  # Automatic: Chat
    assert preparing.prepared == [BONSAI, qwen]  # an Ollama turn too: llama-server's model is unloaded

    preparing.fail = "llama.cpp isn't installed. Install it with `brew install llama.cpp`, then try again."
    shown = events(client.post("/v1/chat", json={"text": "Again", "model": BONSAI.public()}, headers=AUTH))
    assert ("error", {"message": preparing.fail}) in shown


def test_picking_a_model_in_the_chat_box_prepares_it(settings):
    aura, _ = make_aura(settings)
    aura.toolbox.local_server = preparing = Preparing()
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    aura._injected_model = False  # a real model's Aura: the pick loads it (here, in the stand-in)
    try:
        r = client.post("/v1/models/prepare", json=BONSAI.public(), headers=AUTH)
        assert r.status_code == 202 and r.json() == {"status": "preparing"}
        deadline = time.monotonic() + 5
        while not preparing.prepared and time.monotonic() < deadline:
            time.sleep(0.01)
        assert preparing.prepared == [BONSAI]
    finally:
        aura._injected_model = True
    assert client.post("/v1/models/prepare", json={"provider": "x", "model": "y"}, headers=AUTH).status_code == 422
    assert client.post("/v1/models/prepare", json=BONSAI.public()).status_code == 401


def test_the_status_follows_the_chat_box_pick_and_shows_a_model_loading(settings):
    ModelRouting(settings).replace(choices() | {"startup": BONSAI})
    aura, _ = make_aura(settings)
    aura.toolbox.local_server = served = Served(["bonsai-2.5-27b"], loaded="loading:bonsai-2.5-27b")
    client = TestClient(create_app(aura, TOKEN, ActiveModelKeeper(aura)), base_url="http://127.0.0.1")
    assert client.post("/v1/models/prepare", json=BONSAI.public(), headers=AUTH).status_code == 202
    runtime = client.get("/v1/health", headers=AUTH).json()["runtime_health"]
    assert (runtime["model"], runtime["state"], runtime["detail"]) == ("bonsai-2.5-27b", "loading", "Loading the model.")
    served.model = "bonsai-2.5-27b"  # loaded
    assert client.get("/v1/health", headers=AUTH).json()["runtime_health"]["state"] == "loaded"



def test_bonsai_gets_its_recommended_sampling_and_other_models_their_own_defaults(settings):
    from aura.model_settings import recommended as recommended_sampling

    bonsai = build_model(settings, ModelChoice("llamacpp", "Ternary-Bonsai-2.5-27B-Q2_0"))
    assert (bonsai.temperature, bonsai.top_p, bonsai.presence_penalty) == (0.7, 0.8, 0.0)
    assert bonsai.extra_body == {"max_tokens": 8192, "top_k": 20, "min_p": 0.0, "repeat_penalty": 1.0}
    other = build_model(settings, ModelChoice("llamacpp", "Other-7B-Q4_K_M"))
    assert other.top_p is None and other.extra_body == {"max_tokens": 8192}  # only the cap: the model file's defaults apply
    assert recommended_sampling(ModelChoice("ollama", "bonsai-lookalike")) == {}


def test_every_message_tells_the_model_which_model_answers_it(settings):
    from aura.brain import engine_name

    aura, model = make_aura(settings)
    aura.select_model(BONSAI)
    aura.chat("Which model are you?")
    assert f"say you currently run on {engine_name(BONSAI)}" in model.calls[-1][0].content
    assert engine_name(BONSAI) == "bonsai-2.5-27b (llama.cpp, on this Mac)"
