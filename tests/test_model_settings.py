"""Per-model settings (docs/model-settings.md §8): saved, validated, sent only to providers that take
them, recommended values as defaults, the reply limit, and the context check."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aura import brain as brain_module
from aura.brain import build_model
from aura.model_routing import ModelChoice
from aura.model_settings import ModelSettingsStore, request_options, validate

BONSAI = ModelChoice("llamacpp", "Ternary-Bonsai-2.5-27B-Q2_0")
QWEN = ModelChoice("ollama", "qwen3.5:9b")
CLAUDE = ModelChoice("anthropic", "claude-sonnet-5-5")


def test_settings_are_saved_validated_and_kept_across_restarts(settings):
    store = ModelSettingsStore(settings)
    assert store.put(QWEN, {"temperature": 0.6, "top_p": "", "max_reply_tokens": 4096}) == {"temperature": 0.6, "max_reply_tokens": 4096}
    assert ModelSettingsStore(settings).get(QWEN) == {"temperature": 0.6, "max_reply_tokens": 4096}  # a new store: same file
    assert '"ollama:qwen3.5:9b"' in store.path.read_text()  # keyed provider:model
    assert list(store.all()) == ["ollama:qwen3.5:9b"]  # the model name keeps its own colon
    for bad, message in [({"temperature": 3}, "temperature must be between 0 and 2"),
                         ({"top_k": 20}, "top_k isn't a setting ollama models accept"),
                         ({"effort": "max"}, "effort must be one of"),
                         ({"max_reply_tokens": 100}, "max_reply_tokens must be between 1024 and 32768")]:
        with pytest.raises(ValueError, match=message):
            validate(QWEN, bad)
    with pytest.raises(ValueError, match="temperature isn't a setting anthropic"):
        validate(CLAUDE, {"temperature": 0.5})  # current Claude models reject it


def test_recommended_values_are_the_default_until_settings_are_saved(settings):
    store = ModelSettingsStore(settings)
    assert store.get(BONSAI)["top_k"] == 20 and store.get(QWEN) == {}
    store.put(BONSAI, {"temperature": 0.2})
    assert store.get(BONSAI) == {"temperature": 0.2}  # saved settings replace the recommended ones
    store.put(BONSAI, {})
    assert store.get(BONSAI)["temperature"] == 0.7  # cleared: recommended again


def test_each_provider_is_sent_only_its_own_settings(settings):
    values = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "repeat_penalty": 1.1, "effort": "low"}
    llama = request_options(settings, BONSAI, values, 32768)
    assert llama["extra_body"] == {"max_tokens": 8192, "top_k": 20, "min_p": 0.0, "repeat_penalty": 1.1}
    assert (llama["temperature"], llama["reasoning_effort"]) == (0.7, "low") and "max_tokens" not in llama
    ollama = request_options(settings, QWEN, {"temperature": 0.6}, 16384)
    assert ollama["extra_body"] == {"max_tokens": 8192} and ollama["reasoning_effort"] == settings.reasoning_effort
    claude = request_options(settings, CLAUDE, {"effort": "high", "max_reply_tokens": 20000}, 200000)
    assert claude == {"max_tokens": 20000, "output_config": {"effort": "high"}}
    assert request_options(settings, CLAUDE, {}, 200000) == {"max_tokens": 8192}  # nothing set: nothing sent


def test_a_local_reply_is_at_most_half_the_window(settings):
    assert request_options(settings, QWEN, {"max_reply_tokens": 16000}, 16384)["extra_body"]["max_tokens"] == 8192
    store = ModelSettingsStore(settings)
    store.put(QWEN, {"max_reply_tokens": 4096, "temperature": 0.5})
    model = build_model(settings, QWEN)
    assert (model.extra_body["max_tokens"], model.temperature) == (4096, 0.5)
    claude = build_model(settings, CLAUDE, api_key="sk-ant-api03-" + "a" * 40)
    assert claude.max_tokens == 8192 and not claude.output_config


def test_an_ollama_budget_beyond_its_window_is_capped_and_recorded_once(settings, monkeypatch):
    from conftest import make_aura

    ModelSettingsStore(settings).put(QWEN, {"context_budget": 32768})
    aura, _ = make_aura(settings)
    aura._injected_model = False  # the check is skipped for a test's stand-in model
    monkeypatch.setattr(brain_module, "ollama_window", lambda _s, model: 16384)
    assert aura._context_budget(QWEN) == 16384 and aura._context_budget(QWEN) == 16384
    capped = [e for e in aura.toolbox.audit.entries() if e["event"] == "context_budget_capped"]
    assert len(capped) == 1 and (capped[0]["budget"], capped[0]["window"]) == (32768, 16384)
    aura._windows.clear()
    monkeypatch.setattr(brain_module, "ollama_window", lambda _s, model: 65536)  # a bigger window: no cap
    assert aura._context_budget(QWEN) == 32768
    aura._windows.clear()
    monkeypatch.setattr(brain_module, "ollama_window", lambda _s, model: None)  # not loaded yet:
    assert aura._context_budget(QWEN) == settings.context_tokens  # its configured window is assumed
    aura._injected_model = True


def test_llama_server_is_started_with_the_model_s_own_window(settings):
    from aura.llama_server import LlamaServer

    server = LlamaServer(settings)
    assert server.window(BONSAI.model) == settings.llamacpp_context_tokens
    ModelSettingsStore(settings).put(BONSAI, {"context_budget": 24576})
    assert server.window(BONSAI.model) == 24576


def test_the_settings_endpoints(settings):
    from aura.server import create_app
    from conftest import make_aura
    from test_model_routing import AUTH, TOKEN, NoopKeeper

    aura, _ = make_aura(settings)
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    view = client.get("/v1/models/settings", params={"provider": "llamacpp", "model": BONSAI.model}, headers=AUTH).json()
    assert view["saved"] == {} and view["effective"]["top_k"] == 20 and view["recommended"]["top_p"] == 0.8
    assert "top_k" in view["accepted"] and view["ranges"]["temperature"] == [0, 2]
    saved = client.put("/v1/models/settings", json={"provider": "ollama", "model": "qwen3.5:9b",
                                                   "settings": {"temperature": 0.4}}, headers=AUTH)
    assert saved.status_code == 200 and saved.json()["saved"] == {"temperature": 0.4}
    bad = client.put("/v1/models/settings", json={"provider": "anthropic", "model": CLAUDE.model,
                                                 "settings": {"top_k": 5}}, headers=AUTH)
    assert bad.status_code == 422 and "top_k isn't a setting anthropic" in bad.json()["error"]


def test_whole_numbers_and_hand_edited_values(settings):
    with pytest.raises(ValueError, match="top_k must be a whole number"):
        validate(BONSAI, {"top_k": 20.7})
    assert validate(BONSAI, {"top_k": 20.0, "context_budget": "8192"}) == {"top_k": 20, "context_budget": 8192}
    store = ModelSettingsStore(settings)
    store.put(BONSAI, {"temperature": 0.7})
    store.path.write_text('{"llamacpp:Ternary-Bonsai-2.5-27B-Q2_0": {"temperature": 0.7, "top_k": 500}}')
    assert store.get(BONSAI) == {"temperature": 0.7}  # only the bad value is dropped


def test_ollama_names_match_with_or_without_latest(settings, monkeypatch):
    from aura import model_settings as ms

    monkeypatch.setattr(ms, "ollama_loaded", lambda _s: [{"name": "llama3:latest", "context_length": 8192}])
    assert ms.ollama_window(settings, "llama3") == 8192 and ms.ollama_window(settings, "llama3:8b") is None


def test_saving_the_model_in_use_during_a_turn_changes_nothing(settings):
    from aura.server import create_app
    from conftest import make_aura
    from test_model_routing import AUTH, TOKEN, NoopKeeper

    aura, _ = make_aura(settings)
    app = create_app(aura, TOKEN, NoopKeeper())
    client = TestClient(app, base_url="http://127.0.0.1")
    active = aura.active_model
    app.state.turn.acquire()  # a turn is running
    try:
        busy = client.put("/v1/models/settings", json={"provider": active.provider, "model": active.model,
                                                      "settings": {"temperature": 0.3}}, headers=AUTH)
    finally:
        app.state.turn.release()
    assert busy.status_code == 409 and ModelSettingsStore(settings).stored(active) is None
