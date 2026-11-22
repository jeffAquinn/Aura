"""Voice: settings, catalog matching, the Voice_Tool, and the HTTP endpoints."""

from __future__ import annotations

from dataclasses import asdict

import pytest
from conftest import call, make_aura, tool_turn
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from aura.audit import AuditLog
from aura.brain import ApprovalNeeded, Changed, Status
from aura.server import create_app
from aura.voice import VoiceError, VoiceProfile, VoiceStore

CATALOG = [
    {"id": "com.apple.voice.compact.en-US.Samantha", "name": "Samantha", "language": "en-US", "quality": "default", "gender": "female"},
    {"id": "com.apple.voice.enhanced.en-US.Voice6", "name": "Voice 6", "language": "en-US", "quality": "enhanced", "gender": "female"},
    {"id": "com.apple.voice.compact.en-GB.Daniel", "name": "Daniel", "language": "en-GB", "quality": "default", "gender": "male"},
    {"id": "com.apple.voice.premium.en-GB.Daniel", "name": "Daniel", "language": "en-GB", "quality": "premium", "gender": "male"},
    {"id": "com.apple.speech.synthesis.voice.Bells", "name": "Bells", "language": "en-US", "quality": "default", "novelty": True},
]


@pytest.fixture
def store(tmp_path) -> VoiceStore:
    s = VoiceStore(tmp_path / "data", AuditLog(tmp_path / "audit.jsonl"))
    s.register(CATALOG)
    return s


def test_speech_is_off_by_default(tmp_path):
    assert VoiceStore(tmp_path, AuditLog(tmp_path / "a.jsonl")).load() == VoiceProfile(speak=False)


def test_listing_hides_novelty_voices_and_puts_quality_first(store):
    names = [(v["name"], v["quality"]) for v in store.listing("en")]
    assert ("Bells", "default") not in names
    assert names[0] == ("Daniel", "premium") and names[1] == ("Voice 6", "enhanced")
    assert [v["language"] for v in store.listing("en-GB")] == ["en-GB", "en-GB"]


@pytest.mark.parametrize(
    ("wanted", "voice_id"),
    [
        ("Daniel", "com.apple.voice.premium.en-GB.Daniel"),  # best quality wins a name tie
        ("daniel", "com.apple.voice.premium.en-GB.Daniel"),
        ("com.apple.voice.compact.en-GB.Daniel", "com.apple.voice.compact.en-GB.Daniel"),  # exact id
        ("sam", "com.apple.voice.compact.en-US.Samantha"),
    ],
)
def test_voices_are_found_by_id_name_or_part_of_a_name(store, wanted, voice_id):
    assert store.find(wanted)["id"] == voice_id


def test_update_changes_only_the_fields_given_and_clamps(store):
    store.update(by="user", voice="Daniel", speed=1.3)
    p = store.update(by="aura", volume=5.0, pitch=0.1)
    assert p == VoiceProfile(voice="com.apple.voice.premium.en-GB.Daniel", speed=1.3, pitch=0.5, volume=1.0, speak=False)
    assert store.load() == p  # persisted
    assert [e["by"] for e in store.audit.entries()] == ["user", "aura"]


def test_unknown_voices_and_no_app_are_refused(store, tmp_path):
    with pytest.raises(VoiceError, match="no voice called 'Zarquon'"):
        store.update(by="aura", voice="Zarquon")
    lonely = VoiceStore(tmp_path / "x", AuditLog(tmp_path / "x.jsonl"))
    with pytest.raises(VoiceError, match="Aura app"):
        lonely.update(by="aura", voice="Daniel")
    assert lonely.update(by="aura", speak=True).speak is True  # settings still work without the app


# ── through the agent loop: no approval, instant event ─────────────────────────────────────


def test_set_voice_applies_instantly_without_approval_and_tells_the_app(settings):
    aura, _ = make_aura(settings, tool_turn(call("set_voice", volume=0.5)), AIMessage("Softer now."))
    aura.toolbox.voice.register(CATALOG)
    events = list(aura.stream("Aura, speak more softly."))
    assert not any(isinstance(e, ApprovalNeeded) for e in events)
    assert "[Tool: Voice_Tool] Adjusting my voice..." in [e.text for e in events if isinstance(e, Status)]
    (changed,) = [e for e in events if isinstance(e, Changed)]
    assert changed == Changed("voice", asdict(VoiceProfile(volume=0.5)))


def test_list_voices_without_the_app_says_so(settings):
    aura, _ = make_aura(settings, tool_turn(call("list_voices", language="en-GB")), AIMessage("ok"))
    list(aura.stream("What voices do you have?"))
    msgs = aura._agent.get_state(aura._config).values["messages"]
    assert "Speech is available in the Aura app" in [m.text for m in msgs if m.type == "tool"][0]


# ── HTTP: the user acting directly ──────────────────────────────────────────────────────────

TOKEN = "t0ken-" + "y" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Keeper:
    def loaded(self):
        return True

    def warm_soon(self):
        pass


def client_for(settings) -> TestClient:
    aura, _ = make_aura(settings)
    return TestClient(create_app(aura, TOKEN, Keeper()), base_url="http://127.0.0.1")


def test_settings_voice_and_persona_endpoints(settings):
    c = client_for(settings)
    assert c.post("/v1/voices", json={"voices": CATALOG}, headers=AUTH).json() == {"registered": 5}
    s = c.get("/v1/settings", headers=AUTH).json()
    assert s["voice"]["speak"] is False and s["persona"]["name"] == "Aura" and s["voices_registered"] == 5

    r = c.put("/v1/voice", json={"voice": "Daniel", "speak": True}, headers=AUTH)
    assert r.status_code == 200 and r.json()["voice"] == "com.apple.voice.premium.en-GB.Daniel"
    assert c.put("/v1/voice", json={"voice": "Zarquon"}, headers=AUTH).status_code == 422

    assert c.post("/v1/persona/reset", json={}, headers=AUTH).json()["history"] == 1


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/v1/settings"), ("put", "/v1/voice"), ("post", "/v1/voices"), ("get", "/v1/persona"), ("post", "/v1/persona/reset")],
)
def test_new_endpoints_need_the_token(settings, method, path):
    c = client_for(settings)
    kwargs = {"json": {}} if method != "get" else {}
    assert getattr(c, method)(path, **kwargs).status_code == 401


def test_put_must_be_json_too(settings):
    c = client_for(settings)
    r = c.put("/v1/voice", content="speak=true", headers={**AUTH, "Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415
