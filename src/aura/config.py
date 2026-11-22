"""Runtime configuration, read from the environment (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass

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

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        d = cls()
        return cls(
            base_url=os.getenv("AURA_BASE_URL", d.base_url),
            api_key=os.getenv("AURA_API_KEY", d.api_key),
            model=os.getenv("AURA_MODEL", d.model),
            reasoning_effort=os.getenv("AURA_REASONING_EFFORT", d.reasoning_effort) or None,
            context_tokens=int(os.getenv("AURA_CONTEXT_TOKENS", d.context_tokens)),
        )
