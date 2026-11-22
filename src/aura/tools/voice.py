"""Voice_Tool: how Aura sounds. No approval: changes are instant, audible and reversible."""

from __future__ import annotations

from dataclasses import asdict

from langchain_core.tools import BaseTool, tool

from aura.events import emit
from aura.voice import VoiceError, VoiceStore

NO_APP = "Speech is available in the Aura app; no voices have been reported yet."


def voice_tools(store: VoiceStore) -> list[BaseTool]:
    @tool
    def list_voices(language: str = "") -> str:
        """List the voices you can speak with, best quality first. Optionally filter by language
        code, e.g. 'en-GB' for British English or 'en' for all English voices."""
        voices = store.listing(language)
        if not store.catalog:
            return NO_APP
        if not voices:
            return f"No voices for '{language}'."
        shown = [f"{v['name']} ({v['language']}, {v.get('gender', 'unknown')}, {v.get('quality', 'default')})"
                 for v in voices[:25]]
        more = f"\n…and {len(voices) - 25} more" if len(voices) > 25 else ""
        return "\n".join(shown) + more

    @tool
    def set_voice(voice: str = "", speed: float = 0.0, pitch: float = 0.0, volume: float = 0.0,
                  speak: bool | None = None) -> str:
        """Change how you sound. Set only what the user asked for and leave the rest out.
        voice: a voice name from list_voices (only when the user wants a different voice).
        speed and pitch: multipliers around 1.0 (0.5-2.0); volume: 0.1-1.0.
        speak: true/false to turn spoken replies on or off."""
        try:
            profile = store.update(
                by="aura", voice=voice or None, speed=speed or None, pitch=pitch or None,
                volume=volume or None, speak=speak,
            )
        except VoiceError as e:
            return f"Not changed: {e}."
        emit({"voice": asdict(profile)})
        return f"Voice updated: {store.describe(profile)}."

    return [list_voices, set_voice]
