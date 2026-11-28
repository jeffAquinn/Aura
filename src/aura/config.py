"""Runtime configuration, read from the environment (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parents[2]  # the repository: src/aura/config.py -> ../..
# Where Homebrew puts llama.cpp's server (Apple silicon first). The app's PATH has neither.
LLAMA_SERVER = next((p for p in ("/opt/homebrew/bin/llama-server", "/usr/local/bin/llama-server")
                     if Path(p).is_file()), "/opt/homebrew/bin/llama-server")


@dataclass(frozen=True)
class Settings:
    # Any OpenAI-compatible endpoint: Ollama, LM Studio, vLLM, a hosted API, ...
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"  # Ollama ignores the key, but the client requires one.
    model: str = "gemma4:26b"
    # llama.cpp's llama-server, which Aura starts for a llama.cpp model and stops again
    # (aura.llama_server). Only on this Mac: the URL must be a 127.0.0.1/localhost one.
    llamacpp_url: str = "http://127.0.0.1:8080/v1"
    # The llama.cpp models: every .gguf file in this folder (and below), named by its file name.
    llamacpp_models_dir: Path = Path("~/models").expanduser()
    llama_server: str = LLAMA_SERVER  # the program Aura runs; never one a model or skill names
    # "none" | "low" | "medium" | "high"; None omits the parameter for servers that reject it.
    reasoning_effort: str | None = "none"
    # The context window the *server* actually allocates (see `ollama ps`). Ollama silently
    # drops the oldest messages past this, so Aura compacts its memory before reaching it.
    context_tokens: int = 16384
    # llama-server's window, which Aura sets itself (-c); Aura's memory budget for llama.cpp models.
    # 32k: about 4 GB of q8_0 KV cache for a 27B model, which fits beside it in 24 GB.
    llamacpp_context_tokens: int = 32768
    # How long a local model (Ollama's, or llama-server's) stays loaded after a turn. Aura does
    # not preload models simply because its panel is visible.
    model_keep_alive: str = "10m"
    # The most one model call may generate. Without a cap, a model stuck in a loop never stops:
    # Ollama shifts its context and keeps going (seen in a live test: one call ran 23 minutes).
    # Half the window leaves room for the longest memory summary.
    max_reply_tokens: int = 8192

    # The only folder Aura's file tools can touch. Created on startup if missing.
    vault_dir: Path = Path("~/Documents/Aura_Vault").expanduser()
    # Web search: Tavily when a key is set, otherwise (or on Tavily errors) DuckDuckGo. The key
    # belongs in the Keychain (Settings → Keys), which wins; this is only a legacy fallback.
    tavily_api_key: str | None = None
    # Code interpreter: one disposable Docker container per run.
    sandbox_image: str = "aura-sandbox:1"
    sandbox_timeout: float = 30.0
    # Aura's own settings: persona.json (+ history) and voice.json.
    data_dir: Path = Path("~/Library/Application Support/Aura").expanduser()
    # The read-only source checkout Aura runs from.  Its source is used to start the server and
    # run regressions, but Aura never deploys skills or writes Git state here.
    project_dir: Path = PROJECT_DIR
    # The Developer's scratch workspaces: outside the repository, so an engine never sees Aura's code.
    dev_dir: Path = Path("~/Library/Caches/Aura/dev").expanduser()
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
            llamacpp_url=env("AURA_LLAMACPP_URL", d.llamacpp_url),
            llamacpp_models_dir=Path(env("AURA_LLAMACPP_MODELS_DIR", str(d.llamacpp_models_dir))).expanduser(),
            llama_server=env("AURA_LLAMA_SERVER", d.llama_server),
            reasoning_effort=env("AURA_REASONING_EFFORT", d.reasoning_effort) or None,
            context_tokens=int(env("AURA_CONTEXT_TOKENS", d.context_tokens)),
            llamacpp_context_tokens=int(env("AURA_LLAMACPP_CONTEXT_TOKENS", d.llamacpp_context_tokens)),
            model_keep_alive=env("AURA_MODEL_KEEP_ALIVE", d.model_keep_alive),
            max_reply_tokens=int(env("AURA_MAX_REPLY_TOKENS", d.max_reply_tokens)),
            vault_dir=Path(env("AURA_VAULT_DIR", str(d.vault_dir))).expanduser(),
            tavily_api_key=env("TAVILY_API_KEY") or None,
            sandbox_image=env("AURA_SANDBOX_IMAGE", d.sandbox_image),
            sandbox_timeout=float(env("AURA_SANDBOX_TIMEOUT", d.sandbox_timeout)),
            data_dir=Path(env("AURA_DATA_DIR", str(d.data_dir))).expanduser(),
            project_dir=Path(env("AURA_PROJECT_DIR", str(d.project_dir))).expanduser(),
            dev_dir=Path(env("AURA_DEV_DIR", str(d.dev_dir))).expanduser(),
            audit_log=Path(env("AURA_AUDIT_LOG", str(d.audit_log))).expanduser(),
        )

    @property
    def skills_dir(self) -> Path:
        """The live skill registry, owned by the user's Vault sandbox.

        Keeping executable skill content here means every install, update and uninstall is local
        to this user.  The source checkout remains a read-only application source and is never
        modified or committed by Aura.
        """
        return self.project_sandbox_dir / "skills"

    @property
    def skills_inbox(self) -> Path:
        """Where new skills wait to be proposed: inside the vault, easy to drop into with Finder."""
        return self.vault_dir / "Skills Inbox"

    @property
    def workstation_dir(self) -> Path:
        """User-owned workspace for non-executable agent candidates and recognized manifests."""
        return self.vault_dir / "Workstation"

    @property
    def agents_dir(self) -> Path:
        return self.workstation_dir / "Agents"

    @property
    def agents_inbox(self) -> Path:
        return self.workstation_dir / "Agent Inbox"

    @property
    def retired_agents_dir(self) -> Path:
        """Read-only archive location for retired recognition records."""
        return self.workstation_dir / "Retired Agents"

    @property
    def agent_lifecycle_log(self) -> Path:
        """Append-only lifecycle evidence; never a place for credentials or free-form secrets."""
        return self.workstation_dir / "Agent_Lifecycle.jsonl"

    @property
    def project_sandbox_dir(self) -> Path:
        """The one-time, editable project copy inside the user's vault.

        It deliberately has the same name as the source root (normally ``Aura``), but it is not a
        worktree and has no Git metadata.  Nothing in the app writes from this path back to
        ``project_dir``.
        """
        return self.vault_dir / self.project_dir.expanduser().resolve().name

    @property
    def staging_dir(self) -> Path:
        """Short-lived audit staging, also contained entirely in the Vault."""
        return self.vault_dir / "Audit_Staging"
