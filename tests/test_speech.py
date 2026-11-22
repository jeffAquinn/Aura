"""Aura's neural voices (Kokoro): catalog, selection, the speech endpoint, real synthesis."""

from __future__ import annotations

import io
import time

import pytest
from conftest import make_aura
from fastapi.testclient import TestClient

from aura import speech
from aura.audit import AuditLog
from aura.server import create_app
from aura.speech import Kokoro
from aura.voice import VoiceStore

pytestmark = pytest.mark.skipif(not speech.available(), reason="neural voices not installed")

TOKEN = "t0ken-" + "z" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_the_user_s_picks_come_first_then_more_female_voices():
    names = [v["name"] for v in speech.catalog()]
    assert names[:3] == ["Heart", "Bella", "Emma"]
    assert len(names) == 11 and all(v["gender"] == "female" for v in speech.catalog())
    assert all(v["id"].startswith("kokoro:") and v["quality"] == "neural" for v in speech.catalog())


def test_neural_voices_rank_above_system_voices_and_are_found_by_name(tmp_path):
    store = VoiceStore(tmp_path, AuditLog(tmp_path / "a.jsonl"), neural=speech.catalog())
    store.register([{"id": "com.apple.voice.enhanced.en-US.Voice6", "name": "Voice 6", "language": "en-US", "quality": "enhanced"}])
    listing = [v["name"] for v in store.listing("en")]
    assert listing[:3] == ["Heart", "Bella", "Emma"] and "Voice 6" in listing
    assert store.find("bella")["id"] == "kokoro:af_bella"
    assert store.listing("en-GB")[0]["name"] == "Emma"  # "a British voice" -> Emma first
    assert store.update(by="user", voice="Heart").voice == "kokoro:af_heart"


class FakeKokoro(Kokoro):
    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, str, float]] = []

    def synthesize(self, text, voice_id, speed=1.0):
        self.calls.append((text, voice_id, speed))
        return b"RIFF....WAVEfake"


def client(settings, kokoro=None) -> TestClient:
    aura, _ = make_aura(settings)
    return TestClient(create_app(aura, TOKEN, voices=kokoro or FakeKokoro()), base_url="http://127.0.0.1")


def test_speech_endpoint_returns_wav_for_one_sentence(settings):
    fake = FakeKokoro()
    c = client(settings, fake)
    r = c.post("/v1/speech", json={"text": "Hello there.", "voice": "kokoro:af_heart", "speed": 1.1}, headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content.startswith(b"RIFF")
    assert fake.calls == [("Hello there.", "kokoro:af_heart", 1.1)]


@pytest.mark.parametrize(
    "body",
    [
        {"text": "hi", "voice": "com.apple.voice.compact.en-US.Samantha"},  # not a neural voice
        {"text": "hi", "voice": "kokoro:am_adam"},  # not in Aura's set
        {"text": "", "voice": "kokoro:af_heart"},
        {"text": "x" * 1001, "voice": "kokoro:af_heart"},
        {"text": "hi", "voice": "kokoro:af_heart", "speed": 9},
    ],
)
def test_speech_endpoint_validates(settings, body):
    assert client(settings).post("/v1/speech", json=body, headers=AUTH).status_code == 422


def test_speech_endpoint_needs_the_token(settings):
    assert client(settings).post("/v1/speech", json={"text": "hi", "voice": "kokoro:af_heart"}).status_code == 401


def test_voices_endpoint_lists_neural_then_system(settings):
    c = client(settings)
    c.post("/v1/voices", json={"voices": [{"id": "sam", "name": "Samantha", "language": "en-US"}]}, headers=AUTH)
    names = [v["name"] for v in c.get("/v1/voices", headers=AUTH).json()]
    assert names[:3] == ["Heart", "Bella", "Emma"] and names[-1] == "Samantha"


def test_real_kokoro_speaks_a_sentence():
    import soundfile as sf

    kokoro = Kokoro()
    start = time.monotonic()
    wav = kokoro.synthesize("It's 5.3 degrees and overcast in London.", "kokoro:af_heart")
    audio, rate = sf.read(io.BytesIO(wav))
    print(f"\n{len(audio) / rate:.1f}s of speech in {time.monotonic() - start:.1f}s (incl. model load)")
    assert rate == 24000 and 1.5 < len(audio) / rate < 8
    faster = kokoro.synthesize("It's 5.3 degrees and overcast in London.", "kokoro:af_heart", speed=1.6)
    assert len(sf.read(io.BytesIO(faster))[0]) < len(audio)  # speed really changes the speech
