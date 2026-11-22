"""Aura's neural voices: Kokoro-82M on Apple Silicon (MLX), entirely on this Mac.

The app asks the server for one sentence of audio at a time (POST /v1/speech) and plays it while
the next is prepared. The model loads on first use (about 0.5 GB) and stays loaded; synthesis
runs on one dedicated worker thread, in request order.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

PREFIX = "kokoro:"
REPO = "mlx-community/Kokoro-82M-bf16"

# English female voices: the user's picks first, then Kokoro's best-graded others (VOICES.md).
VOICES: list[tuple[str, str, str, str]] = [
    ("af_heart", "Heart", "en-US", "A"),
    ("af_bella", "Bella", "en-US", "A-"),
    ("bf_emma", "Emma", "en-GB", "B-"),
    ("af_nicole", "Nicole", "en-US", "B-"),
    ("af_sarah", "Sarah", "en-US", "C+"),
    ("af_aoede", "Aoede", "en-US", "C+"),
    ("af_kore", "Kore", "en-US", "C+"),
    ("bf_isabella", "Isabella", "en-GB", "C"),
    ("af_nova", "Nova", "en-US", "C"),
    ("af_alloy", "Alloy", "en-US", "C"),
    ("af_sky", "Sky", "en-US", "C-"),
]
DEFAULT_VOICE = PREFIX + "af_heart"


class SpeechUnavailable(RuntimeError):
    pass


def available() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("mlx_audio", "misaki", "soundfile"))


def catalog() -> list[dict[str, Any]]:
    """The neural voices, in display order (empty if Kokoro isn't installed)."""
    if not available():
        return []
    return [
        {"id": PREFIX + vid, "name": name, "language": lang, "quality": "neural", "gender": "female",
         "novelty": False, "grade": grade}
        for vid, name, lang, grade in VOICES
    ]


class Kokoro:
    def __init__(self, repo: str = REPO):
        self.repo = repo
        self._model: Any = None
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kokoro")  # FIFO, one at a time
        self._load_lock = threading.Lock()

    @staticmethod
    def knows(voice_id: str) -> bool:
        return voice_id.startswith(PREFIX) and voice_id[len(PREFIX):] in {v[0] for v in VOICES}

    def synthesize(self, text: str, voice_id: str, speed: float = 1.0) -> bytes:
        """One sentence -> 24 kHz mono 16-bit WAV."""
        if not available():
            raise SpeechUnavailable("neural voices aren't installed")
        import numpy as np
        import soundfile as sf

        voice = voice_id.removeprefix(PREFIX)
        model = self._load()
        chunks, rate = [], 24000
        for result in model.generate(text=text, voice=voice, speed=speed, lang_code=voice[0]):
            chunks.append(np.asarray(result.audio, dtype=np.float32).reshape(-1))
            rate = getattr(result, "sample_rate", None) or rate
        audio = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
        buffer = io.BytesIO()
        sf.write(buffer, audio, rate, format="WAV", subtype="PCM_16")
        return buffer.getvalue()

    def warm(self, voice_id: str) -> None:
        """Load the model and voice in the background so the first sentence isn't late."""
        if available() and self.knows(voice_id):
            self._worker.submit(self.synthesize, "Hello.", voice_id)

    async def synthesize_async(self, text: str, voice_id: str, speed: float = 1.0) -> bytes:
        future = self._worker.submit(self.synthesize, text, voice_id, speed)
        return await asyncio.wrap_future(future)

    def _load(self) -> Any:
        with self._load_lock:
            if self._model is None:
                from mlx_audio.tts.utils import load_model

                self._model = load_model(self.repo)
            return self._model
