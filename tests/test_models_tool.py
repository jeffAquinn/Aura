"""list_models: which models Aura can run, what each role uses and what is loaded (read-only)."""

from __future__ import annotations

import httpx

from aura.capabilities import BY_NAME, FAMILY_SUMMARY
from aura.model_routing import ModelChoice, ModelRouting, SleepMode
from aura.tools import models as models_module
from aura.tools.models import describe, model_tools


def ollama(tags: list[str], loaded: list[str]):
    def get(url, **kwargs):
        request = httpx.Request("GET", url)
        if url.endswith("/api/tags"):
            return httpx.Response(200, json={"models": [{"name": n, "size": 16_000_000_000} for n in tags]}, request=request)
        return httpx.Response(200, json={"models": [{"name": n} for n in loaded]}, request=request)
    return get


def test_it_reports_roles_local_models_what_is_loaded_and_sleep_mode(settings, monkeypatch):
    routing = ModelRouting(settings)
    routing.replace({role: ModelChoice("ollama", "gemma4:26b") for role in ("startup", "coding", "chat", "deep_research", "web_search")}
                    | {"startup": ModelChoice("llamacpp", "Bonsai-27B")})
    routing.replace_sleep_mode(SleepMode(True, "gemma4:26b"))
    settings.llamacpp_models_dir.mkdir(parents=True)
    (settings.llamacpp_models_dir / "Bonsai-27B.gguf").write_bytes(b"GGUF")
    program = settings.llama_server
    open(program, "w").write("#!/bin/sh\n")
    __import__("os").chmod(program, 0o755)
    monkeypatch.setattr(models_module.httpx, "get", ollama(["gemma4:26b", "qwen3.5:9b"], ["gemma4:26b"]))

    text = describe(settings, lambda: "Bonsai-27B", settings.vault_dir)
    assert "Model on startup: Bonsai-27B (llamacpp)" in text and "Chat: gemma4:26b (ollama)" in text
    assert "- gemma4:26b (Ollama, 16.0 GB), loaded now" in text and "- qwen3.5:9b (Ollama, 16.0 GB)\n" in text
    assert "- Bonsai-27B (llama.cpp, 0.0 GB), loaded now" in text
    assert "Sleep Mode: on; after that, gemma4:26b stays loaded." in text


def test_ollama_down_is_said_plainly_and_nothing_breaks(settings, monkeypatch):
    def down(url, **kwargs):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(models_module.httpx, "get", down)
    text = model_tools(settings, lambda: None)[0].invoke({})
    assert "Ollama: not answering right now" in text and "Sleep Mode: off; nothing stays loaded." in text


def test_it_is_a_read_only_models_capability():
    row = BY_NAME["list_models"]
    assert (row.family, row.floor, row.egress) == ("Models", "none", "none")
    assert "Models" in FAMILY_SUMMARY and row.matches("Which models can you run?")


def test_the_models_window_and_list_models_share_one_local_state(settings, monkeypatch):
    from fastapi.testclient import TestClient

    from aura.server import create_app
    from conftest import make_aura
    from test_model_routing import AUTH, TOKEN, NoopKeeper

    monkeypatch.setattr(models_module.httpx, "get", ollama(["gemma4:26b", "qwen3.5:9b"], ["gemma4:26b"]))
    aura, _ = make_aura(settings)
    client = TestClient(create_app(aura, TOKEN, NoopKeeper()), base_url="http://127.0.0.1")
    state = client.get("/v1/models/local", headers=AUTH).json()
    assert state["ollama"] == {"answering": True, "models": [{"name": "gemma4:26b", "gb": 16.0, "loaded": True},
                                                             {"name": "qwen3.5:9b", "gb": 16.0, "loaded": False}]}
    assert state["llamacpp"]["server"] == {"running": False, "model": None} and state["llamacpp"]["installed"] is False
    assert state["sleep_mode"] == {"enabled": False, "model": settings.model} and state["idle_unload"] == "10m"
    assert "- gemma4:26b (Ollama, 16.0 GB), loaded now" in describe(settings, lambda: None)
