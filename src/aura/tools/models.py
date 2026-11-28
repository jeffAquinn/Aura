"""Models: which AI models Aura can run, what each role uses, and what is loaded now.

Read-only: it changes nothing (models are chosen in the Models window). It asks only this Mac's
Ollama and the llama.cpp models folder, never the network.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx
from langchain_core.tools import BaseTool, tool

from aura.config import Settings
from aura.llama_server import gguf_models, llamacpp_models
from aura.model_routing import ROLE_LABELS, ModelRouting, local_root
from aura.model_settings import ollama_loaded

CODEX = "The_Interface_Many/models/registry.md"  # the vault's Model Codex (profiles), if present
ROLE_ORDER = ("startup", "chat", "web_search", "deep_research")


def _ollama(settings: Settings) -> tuple[dict[str, float], set[str]] | None:
    """Installed Ollama models (name: size in GB) and the loaded ones; None if Ollama can't say."""
    try:
        root = local_root(settings.base_url)
        tags = httpx.get(f"{root}/api/tags", timeout=2).json().get("models", [])
    except (ValueError, httpx.HTTPError, AttributeError):
        return None
    loaded = ollama_loaded(settings)
    if loaded is None:
        return None
    installed = {str(m.get("name")): round(int(m.get("size") or 0) / 1e9, 1) for m in tags if m.get("name")}
    return installed, {str(m.get("name") or m.get("model")) for m in loaded}


def local_state(settings: Settings, llama_loaded: Callable[[], str | None]) -> dict[str, Any]:
    """The local models as data: Ollama's (with sizes and which is loaded), the llama.cpp files and
    llama-server's state, Sleep Mode and the idle time. One implementation for the Models window
    (GET /v1/models/local) and for Aura (list_models, which formats it as text)."""
    ollama = _ollama(settings)
    current = llama_loaded()
    files = gguf_models(settings)
    routing = ModelRouting(settings)
    sleep = routing.sleep_mode()
    return {
        "ollama": {"answering": ollama is not None,
                   "models": [{"name": name, "gb": size, "loaded": name in ollama[1]}
                              for name, size in sorted(ollama[0].items())] if ollama else []},
        "llamacpp": {"installed": os.access(settings.llama_server, os.X_OK),
                     "folder": str(settings.llamacpp_models_dir),
                     "models": [{"name": name, "gb": round(files[name].stat().st_size / 1e9, 1) if name in files else None,
                                 "loaded": name == current} for name in llamacpp_models(settings)],
                     "server": {"running": current is not None, "model": current}},
        "sleep_mode": sleep.public(),
        "idle_unload": settings.model_keep_alive,
    }


def describe(settings: Settings, llama_loaded: Callable[[], str | None], vault_root: Any = None) -> str:
    routing = ModelRouting(settings)
    state = local_state(settings, llama_loaded)
    lines = ["Roles (each kind of request uses its model; set in the Models window):"]
    for role in ROLE_ORDER:
        choice = routing.choice(role)
        lines.append(f"- {ROLE_LABELS.get(role, role)}: {choice.model} ({choice.provider})")
    lines.append(f"- Coding: the Developer ({'Claude Code' if routing.coding_engine() == 'claude' else 'Codex'})")

    lines.append("\nLocal models on this Mac:")
    if not state["ollama"]["answering"]:
        lines.append("- Ollama: not answering right now")
    else:
        lines += [f"- {m['name']} (Ollama, {m['gb']} GB){', loaded now' if m['loaded'] else ''}"
                  for m in state["ollama"]["models"]] or ["- Ollama: no models installed"]
    for m in state["llamacpp"]["models"]:
        lines.append(f"- {m['name']} (llama.cpp, {m['gb'] if m['gb'] is not None else '?'} GB)"
                     f"{', loaded now' if m['loaded'] else ''}")

    sleep = state["sleep_mode"]
    lines.append(f"\nAn idle model unloads after {state['idle_unload']}. Sleep Mode: "
                 + (f"on; after that, {sleep['model']} stays loaded." if sleep["enabled"] else "off; nothing stays loaded."))
    if vault_root is not None and (vault_root / CODEX).is_file():
        lines.append(f"Profiles of local models (strengths, sizes, benchmarks): the Model Codex, {CODEX}.")
    return "\n".join(lines)


def model_tools(settings: Settings, llama_loaded: Callable[[], str | None], vault_root: Any = None) -> list[BaseTool]:
    @tool
    def list_models() -> str:
        """See which AI models you can run on this Mac and which is loaded now: the model each role
        uses (Startup, Chat, Web Search, Deep Research), the local Ollama and llama.cpp models with
        their sizes, and Sleep Mode. Use it for any question about models; it changes nothing (the
        user switches models in the Models window)."""
        return describe(settings, llama_loaded, vault_root)

    return [list_models]
