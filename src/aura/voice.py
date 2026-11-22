"""Aura's voice settings (voice.json) and the catalog of voices the app can speak with.

Speech itself happens in the macOS app (AVFoundation). The server owns the settings because the
agent's tools change them; every change is pushed to the app as a `voice` event.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from aura.audit import AuditLog

RANGES = {"speed": (0.5, 2.0), "pitch": (0.5, 2.0), "volume": (0.1, 1.0)}
QUALITY_RANK = {"premium": 0, "enhanced": 1, "default": 2}


@dataclass(frozen=True)
class VoiceProfile:
    voice: str | None = None  # a catalog id; None = the system default voice
    speed: float = 1.0
    pitch: float = 1.0
    volume: float = 0.9
    speak: bool = False  # Gemma's mandate: spoken replies are off until the user turns them on


class VoiceError(ValueError):
    pass


class VoiceStore:
    def __init__(self, data_dir: Path, audit: AuditLog):
        self.path = data_dir / "voice.json"
        self.audit = audit
        self.catalog: list[dict[str, Any]] = []  # registered by the app when it connects

    def load(self) -> VoiceProfile:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return _clamped(VoiceProfile(**{k: raw[k] for k in asdict(VoiceProfile()) if k in raw}))
        except (OSError, ValueError, TypeError):
            return VoiceProfile()

    def register(self, voices: list[dict[str, Any]]) -> int:
        self.catalog = [v for v in voices if v.get("id") and v.get("name")]
        return len(self.catalog)

    def listing(self, language: str = "") -> list[dict[str, Any]]:
        """Speakable voices, best quality first; novelty voices (Bells, Bubbles…) left out."""
        voices = [v for v in self.catalog if not v.get("novelty") and v.get("language", "").startswith(language)]
        return sorted(voices, key=lambda v: (QUALITY_RANK.get(v.get("quality", "default"), 3), v["name"]))

    def find(self, wanted: str) -> dict[str, Any] | None:
        """A voice by id, then exact name, then partial name; best quality wins ties."""
        w = wanted.strip().casefold()
        for match in (
            lambda v: v["id"].casefold() == w,
            lambda v: v["name"].casefold() == w,
            lambda v: w in v["name"].casefold() or w in v["id"].casefold(),
        ):
            hits = [v for v in self.listing() if match(v)] or [v for v in self.catalog if match(v)]
            if hits:
                return hits[0]
        return None

    def update(self, by: str, voice: str | None = None, speed: float | None = None,
               pitch: float | None = None, volume: float | None = None, speak: bool | None = None) -> VoiceProfile:
        """Change only the fields given."""
        current = self.load()
        changes: dict[str, Any] = {}
        if voice:
            if not self.catalog:
                raise VoiceError("no voices are available yet: speech happens in the Aura app")
            found = self.find(voice)
            if found is None:
                raise VoiceError(f"there is no voice called '{voice}'; use list_voices to see them")
            changes["voice"] = found["id"]
        for key, value in (("speed", speed), ("pitch", pitch), ("volume", volume)):
            if value is not None:
                changes[key] = value
        if speak is not None:
            changes["speak"] = speak
        new = _clamped(replace(current, **changes))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(new), indent=2) + "\n", encoding="utf-8")
        self.audit.record("voice_changed", by=by, **{k: getattr(new, k) for k in changes})
        return new

    def describe(self, profile: VoiceProfile) -> str:
        name = next((v["name"] for v in self.catalog if v["id"] == profile.voice), profile.voice or "the system voice")
        return (f"voice {name}, speed {profile.speed:g}, pitch {profile.pitch:g}, "
                f"volume {profile.volume:g}, spoken replies {'on' if profile.speak else 'off'}")


def _clamped(p: VoiceProfile) -> VoiceProfile:
    return replace(p, **{k: min(max(float(getattr(p, k)), lo), hi) for k, (lo, hi) in RANGES.items()})
