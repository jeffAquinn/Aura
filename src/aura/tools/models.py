"""Models: which AI models Aura can run, what each role uses, and what is loaded now.

Read-only: it changes nothing (models are chosen in Settings → Models). It asks only this Mac's
Ollama and the llama.cpp models folder, never the network.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from langchain_core.tools import BaseTool, tool

from aura.config import Settings
from aura.llama_server import gguf_models, llamacpp_models
from aura.model_routing import ROLE_LABELS, ModelRouting, local_root

CODEX = "The_Interface_Many/models/registry.md"  # the vault's Model Codex (profiles), if present
ROLE_ORDER = ("startup", "chat", "web_search", "deep_research")


def _ollama(settings: Settings) -> tuple[dict[str, float], set[str]] | None:
    """Installed Ollama models (name: size in GB) and the loaded ones; None if Ollama can't say."""
    try:
        root = local_root(settings.base_url)
        tags = httpx.get(f"{root}/api/tags", timeout=2).json().get("models", [])
        ps = httpx.get(f"{root}/api/ps", timeout=2).json().get("models", [])
    except (ValueError, httpx.HTTPError, AttributeError):
        return None
    installed = {str(m.get("name")): round(int(m.get("size") or 0) / 1e9, 1) for m in tags if m.get("name")}
    return installed, {str(m.get("name") or m.get("model")) for m in ps}


def describe(settings: Settings, llama_loaded: Callable[[], str | None], vault_root: Any = None) -> str:
    routing = ModelRouting(settings)
    lines = ["Roles (each kind of request uses its model; set in Settings → Models):"]
    for role in ROLE_ORDER:
        choice = routing.choice(role)
        lines.append(f"- {ROLE_LABELS.get(role, role)}: {choice.model} ({choice.provider})")
    lines.append(f"- Coding: the Developer ({'Claude Code' if routing.coding_engine() == 'claude' else 'Codex'})")

    lines.append("\nLocal models on this Mac:")
    ollama = _ollama(settings)
    if ollama is None:
        lines.append("- Ollama: not answering right now")
    else:
        installed, loaded = ollama
        lines += [f"- {name} (Ollama, {size} GB){', loaded now' if name in loaded else ''}"
                  for name, size in sorted(installed.items())] or ["- Ollama: no models installed"]
    current = llama_loaded()
    files = gguf_models(settings)
    for name in llamacpp_models(settings):
        size = round(files[name].stat().st_size / 1e9, 1) if name in files else "?"
        lines.append(f"- {name} (llama.cpp, {size} GB){', loaded now' if name == current else ''}")

    sleep = routing.sleep_mode()
    lines.append(f"\nAn idle model unloads after {settings.model_keep_alive}. Sleep Mode: "
                 + (f"on; after that, {sleep.model} stays loaded." if sleep.enabled else "off; nothing stays loaded."))
    if vault_root is not None and (vault_root / CODEX).is_file():
        lines.append(f"Profiles of local models (strengths, sizes, benchmarks): the Model Codex, {CODEX}.")
    return "\n".join(lines)


def model_tools(settings: Settings, llama_loaded: Callable[[], str | None], vault_root: Any = None) -> list[BaseTool]:
    @tool
    def list_models() -> str:
        """See which AI models you can run on this Mac and which is loaded now: the model each role
        uses (Startup, Chat, Web Search, Deep Research), the local Ollama and llama.cpp models with
        their sizes, and Sleep Mode. Use it for any question about models; it changes nothing (the
        user switches models in Settings → Models)."""
        return describe(settings, llama_loaded, vault_root)

    return [list_models]
