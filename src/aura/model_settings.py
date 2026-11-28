"""Per-model settings (docs/model-settings.md, step 5 of docs/models-window.md).

Settings belong to a model, not a role: they're keyed "<provider>:<model>" (split at the first
colon; provider names have none, model names may: "ollama:qwen3.5:9b") and kept in
model_settings.json beside model_routing.json. Every field is optional: one that isn't set isn't
sent, so the model's own default (or Aura's global value, for effort, context and reply length)
applies. The file holds no secrets.

A model with nothing saved uses its recommended values, if it has any (Bonsai's, measured on its
Q2_0 build); saving settings for it replaces them, and clearing them brings them back.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any

import httpx

from aura.config import Settings
from aura.model_routing import ModelChoice, local_root

# name: (lowest, highest, type)
RANGES: dict[str, tuple[float, float, type]] = {
    "temperature": (0, 2, float),
    "top_p": (0, 1, float),
    "min_p": (0, 1, float),
    "top_k": (1, 200, int),
    "presence_penalty": (-2, 2, float),
    "repeat_penalty": (0.5, 2, float),
    "context_budget": (4_096, 262_144, int),
    "max_reply_tokens": (1_024, 32_768, int),
}
@dataclass(frozen=True)
class Profile:
    """How one provider takes a model's settings (the spec's §3). Every provider-specific rule lives
    here, so a new provider is one more entry, not a branch in each function below."""

    sampling: tuple[str, ...]  # sent as the client's own fields
    extra: tuple[str, ...]  # sent in the request body (llama-server's own fields)
    efforts: tuple[str, ...]
    effort_as: str  # "reasoning_effort" (OpenAI-style, always sent) or "output_config" (only when set)
    budget_from: str  # the Settings field that holds the default context budget
    reply_capped: bool  # a local model's reply is at most half its window
    window: str  # who decides the server's window: "aura" (it starts the server), "server" (it reports it), "none"
    # The reply cap goes in the request body as max_tokens: langchain-openai renames its own max_tokens to
    # max_completion_tokens, which Ollama ignores (a looping reply then never stops).
    cap_in_body: bool = False


_COMMON = ("effort", "context_budget", "max_reply_tokens")
PROVIDERS: dict[str, Profile] = {
    # llama-server takes every sampling setting; Aura starts it, so its window is Aura's to set.
    "llamacpp": Profile(sampling=("temperature", "top_p", "presence_penalty"), extra=("top_k", "min_p", "repeat_penalty"),
                        efforts=("none", "low", "medium", "high"), effort_as="reasoning_effort",
                        budget_from="llamacpp_context_tokens", reply_capped=True, window="aura", cap_in_body=True),
    # Ollama's OpenAI endpoint doesn't pass top_k, min_p or repeat_penalty through; its window is its own.
    "ollama": Profile(sampling=("temperature", "top_p", "presence_penalty"), extra=(),
                      efforts=("none", "low", "medium", "high"), effort_as="reasoning_effort",
                      budget_from="context_tokens", reply_capped=True, window="server", cap_in_body=True),
    # Current Claude models reject temperature, top_p and top_k: effort and reply length only.
    "anthropic": Profile(sampling=(), extra=(), efforts=("low", "medium", "high", "max"), effort_as="output_config",
                         budget_from="context_tokens", reply_capped=False, window="none"),
}
EFFORTS = {name: profile.efforts for name, profile in PROVIDERS.items()}
ACCEPTED = {name: {*profile.sampling, *profile.extra, *_COMMON} for name, profile in PROVIDERS.items()}
# Recommended values, by model family (docs/model-settings.md §5 and §10: Bonsai's non-thinking
# values tied on accuracy and were 30% faster than its file's thinking-mode defaults).
RECOMMENDED: dict[str, dict[str, Any]] = {
    "bonsai": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0,
               "repeat_penalty": 1.0},
}


def key(choice: ModelChoice) -> str:
    return f"{choice.provider}:{choice.model}"


def recommended(choice: ModelChoice) -> dict[str, Any]:
    """A llama.cpp model's recommended values, matched by family name; {} for others."""
    if choice.provider != "llamacpp":
        return {}
    name = choice.model.lower()
    return next((dict(values) for family, values in RECOMMENDED.items() if family in name), {})


def validate(choice: ModelChoice, values: dict[str, Any]) -> dict[str, Any]:
    """The settings, checked: only what the provider accepts, each within its range. Raises
    ValueError naming the field. None or "" means "not set" and is dropped."""
    accepted = ACCEPTED.get(choice.provider, set())
    out: dict[str, Any] = {}
    for name, value in values.items():
        if value is None or value == "":
            continue
        if name not in accepted:
            raise ValueError(f"{name} isn't a setting {choice.provider} models accept.")
        if name == "effort":
            if value not in EFFORTS[choice.provider]:
                raise ValueError(f"effort must be one of: {', '.join(EFFORTS[choice.provider])}.")
            out[name] = value
            continue
        low, high, kind = RANGES[name]
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be a number.") from None
        if kind is int and number != int(number):
            raise ValueError(f"{name} must be a whole number.")  # never silently cut 20.7 to 20
        if isinstance(value, bool) or not low <= number <= high:
            raise ValueError(f"{name} must be between {low:g} and {high:g}.")
        out[name] = kind(number)
    return out


_CACHE: dict[str, tuple[int, dict[str, dict[str, Any]]]] = {}  # path -> (mtime_ns, contents)
_WRITING = threading.Lock()  # one save at a time: two at once could otherwise lose one


@dataclass
class ModelSettingsStore:
    settings: Settings

    @property
    def path(self):
        return self.settings.data_dir / "model_settings.json"

    def all(self) -> dict[str, dict[str, Any]]:
        """The file's contents, read again only when it changes (it is consulted on every turn)."""
        try:
            stamp = self.path.stat().st_mtime_ns
        except OSError:
            return {}  # missing: no settings, today's behavior
        cached = _CACHE.get(str(self.path))
        if cached and cached[0] == stamp:
            return {k: dict(v) for k, v in cached[1].items()}
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}  # unreadable: no settings
        contents = {k: v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}
        _CACHE[str(self.path)] = (stamp, contents)
        return {k: dict(v) for k, v in contents.items()}

    def stored(self, choice: ModelChoice) -> dict[str, Any] | None:
        return self.all().get(key(choice))

    def get(self, choice: ModelChoice) -> dict[str, Any]:
        """The settings in effect: what's saved, or the model's recommended values."""
        saved = self.stored(choice)
        if saved is None:
            return recommended(choice)
        good: dict[str, Any] = {}
        for name, value in saved.items():  # a hand-edited bad value drops only itself
            try:
                good.update(validate(choice, {name: value}))
            except ValueError:
                continue
        return good

    def put(self, choice: ModelChoice, values: dict[str, Any]) -> dict[str, Any]:
        """Save the model's settings (validated). Empty: forget them, so recommended values (if
        any) apply again."""
        checked = validate(choice.validate(), values)
        with _WRITING:
            return self._write(choice, checked)

    def _write(self, choice: ModelChoice, checked: dict[str, Any]) -> dict[str, Any]:
        everything = self.all()
        if checked:
            everything[key(choice)] = checked
        else:
            everything.pop(key(choice), None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(everything, indent=2, sort_keys=True) + "\n")
        tmp.replace(self.path)
        _CACHE.pop(str(self.path), None)
        return checked


def budget(settings: Settings, choice: ModelChoice, values: dict[str, Any]) -> int:
    """The model's context budget (Aura's compaction limit): its own, or the provider's default."""
    if "context_budget" in values:
        return int(values["context_budget"])
    return int(getattr(settings, PROVIDERS[choice.provider].budget_from))


def reply_tokens(settings: Settings, choice: ModelChoice, values: dict[str, Any], context: int) -> int:
    """The reply limit: the model's own or the global one; a local model's is at most half its window."""
    limit = int(values.get("max_reply_tokens", settings.max_reply_tokens))
    return min(limit, context // 2) if PROVIDERS[choice.provider].reply_capped else limit


def ollama_loaded(settings: Settings) -> list[dict[str, Any]] | None:
    """Ollama's loaded models (`ollama ps`), each with its name and context_length; None if
    Ollama can't say. One call for the Models window, list_models and the context check."""
    try:
        listed = httpx.get(f"{local_root(settings.base_url)}/api/ps", timeout=2).json().get("models", [])
    except (ValueError, httpx.HTTPError, AttributeError):
        return None
    return [m for m in listed if isinstance(m, dict)]


def ollama_window(settings: Settings, model: str) -> int | None:
    """The context window Ollama has allocated for a loaded model; None if it isn't loaded or
    Ollama can't say. Aura can't set it: it's Ollama's own setting. "llama3" is "llama3:latest"."""
    names = {model, model if ":" in model else f"{model}:latest"}
    for m in ollama_loaded(settings) or []:
        if names & {m.get("name"), m.get("model")} and m.get("context_length"):
            return int(m["context_length"])
    return None


def request_options(settings: Settings, choice: ModelChoice, values: dict[str, Any], context: int) -> dict[str, Any]:
    """What a model's settings add to its client: each provider is sent only what it accepts."""
    profile = PROVIDERS[choice.provider]
    cap = reply_tokens(settings, choice, values, context)
    options: dict[str, Any] = {"extra_body": {"max_tokens": cap}} if profile.cap_in_body else {"max_tokens": cap}
    if profile.effort_as == "reasoning_effort":
        options["reasoning_effort"] = values.get("effort", settings.reasoning_effort)
    elif "effort" in values:
        options["output_config"] = {"effort": values["effort"]}
    options.update({name: values[name] for name in profile.sampling if name in values})
    if extra := {name: values[name] for name in profile.extra if name in values}:
        options["extra_body"] = {**options.get("extra_body", {}), **extra}
    return options
