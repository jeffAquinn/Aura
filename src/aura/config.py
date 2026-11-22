"""Runtime configuration, read from the environment (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    # Any OpenAI-compatible endpoint: Ollama, LM Studio, vLLM, a hosted API, ...
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"  # Ollama ignores the key, but the client requires one.
    model: str = "gemma4:26b"
    # "none" | "low" | "medium" | "high"; None omits the parameter for servers that reject it.
    reasoning_effort: str | None = "none"
    # The context window the *server* actually allocates (see `ollama ps`). Ollama silently
    # drops the oldest messages past this, so Aura compacts its memory before reaching it.
    context_tokens: int = 16384

    # The only folder Aura's file tools can touch. Created on startup if missing.
    vault_dir: Path = Path("~/Documents/Aura_Vault").expanduser()
    # Web search: Tavily when a key is set, otherwise (or on Tavily errors) DuckDuckGo.
    tavily_api_key: str | None = None
    # Code interpreter: one disposable Docker container per run.
    sandbox_image: str = "aura-sandbox:1"
    sandbox_timeout: float = 30.0
    # Append-only record of approvals, file changes, code runs and blocked paths. Kept
    # outside the vault so Aura's own tools cannot alter it.
    audit_log: Path = Path("~/Library/Logs/Aura/actions.jsonl").expanduser()

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        d = cls()
        env = os.getenv
        return cls(
            base_url=env("AURA_BASE_URL", d.base_url),
            api_key=env("AURA_API_KEY", d.api_key),
            model=env("AURA_MODEL", d.model),
            reasoning_effort=env("AURA_REASONING_EFFORT", d.reasoning_effort) or None,
            context_tokens=int(env("AURA_CONTEXT_TOKENS", d.context_tokens)),
            vault_dir=Path(env("AURA_VAULT_DIR", str(d.vault_dir))).expanduser(),
            tavily_api_key=env("TAVILY_API_KEY") or None,
            sandbox_image=env("AURA_SANDBOX_IMAGE", d.sandbox_image),
            sandbox_timeout=float(env("AURA_SANDBOX_TIMEOUT", d.sandbox_timeout)),
            audit_log=Path(env("AURA_AUDIT_LOG", str(d.audit_log))).expanduser(),
        )
