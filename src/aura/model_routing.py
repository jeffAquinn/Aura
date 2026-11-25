"""User-selected model roles.

Model choices are ordinary, non-secret preferences in Aura's application-support directory.
Credentials never appear here: Anthropic's API key stays in the macOS Keychain, while Ollama
needs no credential.  Keeping this separate also prevents a Claude Code plan token from ever
being mistaken for a general-purpose Claude API credential.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from aura.config import Settings

Provider = Literal["ollama", "anthropic"]
ROLES = ("startup", "coding", "chat", "deep_research", "web_search")
ROLE_LABELS = {
    "startup": "Model on startup",
    "coding": "Complex work / coding",
    "chat": "Chat",
    "deep_research": "Deep Research",
    "web_search": "Web Search",
}
# A useful offline catalog. The Settings "Sync cloud models" control replaces this with the
# account-visible list returned by Anthropic's Models API. A saved model name remains valid even
# when it is no longer in this suggestion list, so updating the catalog never destroys a choice.
ANTHROPIC_MODELS = (
    "claude-fable-5-2",
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
)
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,199}$")


@dataclass(frozen=True)
class ModelChoice:
    provider: Provider
    model: str

    def validate(self) -> ModelChoice:
        if self.provider not in ("ollama", "anthropic"):
            raise ValueError("Choose Ollama or Anthropic.")
        if not _MODEL.fullmatch(self.model):
            raise ValueError("The model name contains unsupported characters.")
        return self

    def public(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SleepMode:
    """An opt-in local model that Ollama should keep resident until the user turns it off."""

    enabled: bool
    model: str

    def validate(self) -> SleepMode:
        ModelChoice("ollama", self.model).validate()
        return self

    def public(self) -> dict[str, str | bool]:
        return asdict(self)


class ModelRouting:
    """Load and atomically save the five model-role preferences."""

    def __init__(self, settings: Settings):
        self.path = settings.data_dir / "model_routing.json"
        fallback = ModelChoice("ollama", settings.model)
        self._choices: dict[str, ModelChoice] = {role: fallback for role in ROLES}
        self._sleep_mode = SleepMode(False, settings.model)
        self._anthropic_models: tuple[str, ...] = ANTHROPIC_MODELS
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
            choices = raw.get("roles", {})
            for role in ROLES:
                item = choices.get(role)
                if isinstance(item, dict):
                    self._choices[role] = ModelChoice(str(item["provider"]), str(item["model"])).validate()
            sleep = raw.get("sleep_mode")
            if isinstance(sleep, dict):
                self._sleep_mode = SleepMode(bool(sleep["enabled"]), str(sleep["model"])).validate()
            models = raw.get("anthropic_models")
            if isinstance(models, list):
                valid = tuple(sorted({ModelChoice("anthropic", str(model)).validate().model for model in models}))
                if valid:
                    self._anthropic_models = valid
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            # The safe fallback is local Ollama. A malformed hand-edited preference must never
            # cause Aura to contact a cloud provider.
            return

    def choice(self, role: str) -> ModelChoice:
        if role not in ROLES:
            raise ValueError("Unknown model role.")
        return self._choices[role]

    def replace(self, values: dict[str, ModelChoice]) -> None:
        if set(values) != set(ROLES):
            raise ValueError("Provide one model choice for every role.")
        checked = {role: choice.validate() for role, choice in values.items()}
        self._choices = checked
        self._save()

    def sleep_mode(self) -> SleepMode:
        return self._sleep_mode

    def replace_sleep_mode(self, value: SleepMode) -> None:
        self._sleep_mode = value.validate()
        self._save()

    def anthropic_models(self) -> tuple[str, ...]:
        return self._anthropic_models

    def replace_anthropic_models(self, values: list[str]) -> None:
        """Persist the API-confirmed cloud catalog without ever storing credentials."""
        checked = tuple(sorted({ModelChoice("anthropic", value).validate().model for value in values}))
        if not checked:
            raise ValueError("Anthropic did not return any usable cloud models.")
        self._anthropic_models = checked
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "roles": {role: choice.public() for role, choice in self._choices.items()},
            "sleep_mode": self._sleep_mode.public(),
            "anthropic_models": list(self._anthropic_models),
        }, indent=2) + "\n")
        tmp.replace(self.path)

    def public(self) -> dict[str, dict[str, str]]:
        return {role: self.choice(role).public() for role in ROLES}


def infer_role(text: str, developer: str | None = None) -> str:
    """A small, transparent routing heuristic; explicit task words win over general chat."""
    if developer is not None:
        return "coding"
    words = text.lower()
    if any(phrase in words for phrase in ("web search", "search the web", "look online", "latest", "current news")):
        return "web_search"
    if any(phrase in words for phrase in ("deep research", "research this", "investigate", "compare sources", "literature review")):
        return "deep_research"
    if any(phrase in words for phrase in ("code", "bug", "debug", "refactor", "implement", "test", "repository", "repo")):
        return "coding"
    return "chat"
