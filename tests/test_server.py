"""Aura Server: SSE wire format, security, turn control, approvals over HTTP, Smart-Warm."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time

import httpx
import openai
import pytest
from conftest import ScriptedModel, call, make_aura, tool_turn
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from aura.middleware import DENIAL
from aura.brain import Changed
from aura.server import ModelKeeper, create_app, encode

TOKEN = "t0ken-" + "x" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeKeeper:
    def __init__(self, loaded: bool | None = True):
        self._loaded, self.warms = loaded, 0

    def loaded(self) -> bool | None:
        return self._loaded

    def warm_soon(self) -> None:
        self.warms += 1


def server(settings, *script, loaded: bool | None = True):
    aura, model = make_aura(settings, *script)
    keeper = FakeKeeper(loaded)
    client = TestClient(create_app(aura, TOKEN, keeper), base_url="http://127.0.0.1")
    return client, aura, keeper


def test_hol_start_requires_a_current_snapshot_and_an_advertised_model(settings):
    """The server, not the native client, binds a HOL start to one availability snapshot."""
    settings.project_sandbox_dir.mkdir(parents=True)
    client, _, _ = server(settings)
    overview = client.get("/v1/hol", headers=AUTH)
    assert overview.status_code == 200
    current = overview.json()
    assert len(current["snapshot"]) == 64 and "catalog" in current

    changed_roles = dict(current["catalog"]["roles"])
    changed_roles["chat"] = {"provider": "ollama", "model": "new-hol-choice:latest"}
    assert client.put("/v1/models", json={"roles": changed_roles}, headers=AUTH).status_code == 200

    stale = post(client, "/v1/hol/aura_session_review/start", {
        "snapshot": current["snapshot"],
        "model": current["defaults"]["model"],
    })
    assert stale.status_code == 409 and "Refresh the HOL Terminal" in stale.json()["error"]

    current = client.get("/v1/hol", headers=AUTH).json()

    unavailable = post(client, "/v1/hol/aura_session_review/start", {
        "snapshot": current["snapshot"],
        "model": {"provider": "ollama", "model": "not-in-this-snapshot:latest"},
    })
    assert unavailable.status_code == 409 and "not available in this HOL snapshot" in unavailable.json()["error"]

    allowed = post(client, "/v1/hol/aura_session_review/start", {
        "snapshot": current["snapshot"],
        "model": current["defaults"]["model"],
    })
    assert allowed.status_code == 409 and "not approved" in allowed.json()["error"]


def test_hol_snapshot_ignores_non_start_metadata(settings):
    settings.project_sandbox_dir.mkdir(parents=True)
    client, _, _ = server(settings)
    before = client.get("/v1/hol", headers=AUTH).json()["snapshot"]

    saved = post(client, "/v1/hol/savepoints", {"label": "Unrelated display metadata"})
    assert saved.status_code == 201

    assert client.get("/v1/hol", headers=AUTH).json()["snapshot"] == before


def test_hol_snapshot_binds_connected_engine_readiness(settings, monkeypatch):
    settings.project_sandbox_dir.mkdir(parents=True)
    client, aura, _ = server(settings)
    current = client.get("/v1/hol", headers=AUTH).json()
    changed_engines = [dict(engine) for engine in current["developers"]]
    assert changed_engines
    changed_engines[0]["problem"] = ("Connection is unavailable."
                                     if changed_engines[0]["problem"] is None else None)
    monkeypatch.setattr(aura.toolbox.developer, "engines_status", lambda: changed_engines)

    stale = post(client, "/v1/hol/aura_session_review/start", {
        "snapshot": current["snapshot"],
        "model": current["defaults"]["model"],
        "engine": changed_engines[0]["name"],
    })
    assert stale.status_code == 409 and "HOL availability changed" in stale.json()["error"]


def post(client: TestClient, path: str, body: dict | None = None, **headers: str) -> httpx.Response:
    return client.post(path, json=body if body is not None else {}, headers={**AUTH, **headers})


def events(r: httpx.Response) -> list[tuple[str, dict]]:
    """Parse an SSE body, enforcing exactly one event line and one data line per event."""
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    out = []
    for block in r.text.split("\n\n"):
        if not block:
            continue
        lines = block.split("\n")
        assert len(lines) == 2 and lines[0].startswith("event: ") and lines[1].startswith("data: "), block
        out.append((lines[0][7:], json.loads(lines[1][6:])))
    return out


def kinds(evs: list[tuple[str, dict]]) -> list[str]:
    """Event kinds in order; consecutive text pieces count as one (a reply may arrive in any
    number of pieces: the secret masker holds back a token until it's whole)."""
    out: list[str] = []
    for k, _ in evs:
        if not (k == "text" and out and out[-1] == "text"):
            out.append(k)
    return out


def said(evs: list[tuple[str, dict]]) -> str:
    return "".join(d["text"] for k, d in evs if k == "text")


# ── the streaming protocol ──────────────────────────────────────────────────────────────────


def test_a_turn_streams_thinking_text_done(settings):
    client, _, _ = server(settings, AIMessage("Hello.\nSecond line."))
    evs = events(post(client, "/v1/chat", {"text": "Hi"}))
    assert kinds(evs) == ["model", "thinking", "text", "done"]
    assert said(evs) == "Hello.\nSecond line."  # newlines travel inside the one JSON data line


def test_an_automatic_chat_turn_has_one_model_call_and_one_matching_model_event(settings):
    aura, model = make_aura(settings, AIMessage("Hello."))
    client = TestClient(create_app(aura, TOKEN, FakeKeeper()), base_url="http://127.0.0.1")

    evs = events(post(client, "/v1/chat", {"text": "Hi"}))

    assert len(model.agent_calls()) == 1
    assert [data for kind, data in evs if kind == "model"] == [
        {"provider": "ollama", "model": settings.model},
    ]


def test_a_local_performance_event_preserves_measured_counts_and_duration():
    data = json.loads(encode(Changed("performance", {
        "total_duration": 57.810607375,
        "prompt_duration": 0.13364725,
        "prompt_tokens": 34,
        "prompt_tokens_per_second": 254.40,
        "generation_duration": 57.674586333,
        "output_tokens": 1657,
        "output_tokens_per_second": 28.73,
    })).split("\n")[1][6:])

    assert data == {
        "total_duration": 57.810607375,
        "prompt_duration": 0.13364725,
        "prompt_tokens": 34,
        "prompt_tokens_per_second": 254.40,
        "generation_duration": 57.674586333,
        "output_tokens": 1657,
        "output_tokens_per_second": 28.73,
    }


def test_a_cold_model_announces_waking(settings):
    client, _, _ = server(settings, AIMessage("Morning."), loaded=False)
    assert kinds(events(post(client, "/v1/chat", {"text": "Hi"}))) == ["model", "thinking", "waking", "text", "done"]


def test_stream_turn_appends_a_warning_for_an_unverified_knowledge_citation(settings):
    client, _, _ = server(settings, AIMessage("Claim. [Source: Knowledge/missing.md]"))
    reply = said(events(post(client, "/v1/chat", {"text": "Hi"})))

    assert "Claim. [Source: Knowledge/missing.md]" in reply
    assert "Local citation check: 1 citation could not be verified" in reply


def test_tool_status_then_approval_then_continuation(settings):
    client, aura, _ = server(
        settings, tool_turn(call("create_folder", path="Project Aura")), AIMessage("Created it.")
    )
    evs = events(post(client, "/v1/chat", {"text": "Create a folder named Project Aura."}))
    assert kinds(evs) == ["model", "thinking", "approval", "done"]
    (action,) = evs[2][1]["actions"]
    assert action == {
        "tool": "create_folder",
        "args": {"path": "Project Aura"},
        "description": "create folder 'Project Aura/'",
    }
    assert client.get("/v1/health", headers=AUTH).json()["awaiting_approval"] is True
    assert not (settings.vault_dir / "Project Aura").exists()

    evs = events(post(client, "/v1/approvals", {"approvals": [True]}))
    assert kinds(evs) == ["thinking", "status", "text", "done"]
    assert evs[1][1]["text"] == "[Tool: File_System_Tool] Creating folder 'Project Aura'..."
    assert (settings.vault_dir / "Project Aura").is_dir()


def test_denial_over_http_is_the_exact_line(settings):
    client, _, _ = server(settings, tool_turn(call("delete_path", path="keep.txt")))
    post(client, "/v1/chat", {"text": "Delete keep.txt"})
    evs = events(post(client, "/v1/approvals", {"approvals": [False]}))
    assert said(evs) == DENIAL


class Unreachable(ScriptedModel):
    def _generate(self, *a, **kw):
        raise openai.APIConnectionError(request=httpx.Request("POST", "http://localhost:11434"))


def test_model_unreachable_becomes_an_error_event(settings):
    aura, _ = make_aura(settings)
    aura2 = type(aura)(settings=aura.settings, model=Unreachable(), toolbox=aura.toolbox)
    client = TestClient(create_app(aura2, TOKEN, FakeKeeper()), base_url="http://127.0.0.1")
    evs = events(post(client, "/v1/chat", {"text": "Hi"}))
    assert kinds(evs) == ["model", "thinking", "error", "done"]
    assert "Ollama" in evs[2][1]["message"]
    assert client.get("/v1/health", headers=AUTH).json()["busy"] is False  # turn released


# ── security ────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 401),
        ({"Authorization": "Bearer wrong"}, 401),
        ({"Authorization": TOKEN}, 401),  # no "Bearer"
        ({**AUTH, "Host": "evil.example"}, 421),  # DNS rebinding
        ({**AUTH, "Host": "evil.example:8080"}, 421),
    ],
    ids=["no-token", "wrong-token", "no-bearer", "foreign-host", "foreign-host-port"],
)
def test_requests_are_refused_without_token_or_from_foreign_hosts(settings, headers, status):
    client, _, _ = server(settings, AIMessage("x"))
    assert client.get("/v1/health", headers=headers).status_code == status
    assert client.post("/v1/chat", json={"text": "hi"}, headers=headers).status_code == status


@pytest.mark.parametrize(
    "content",
    [
        {"content": "text=hi", "headers": {"Content-Type": "application/x-www-form-urlencoded"}},
        {"content": '{"text": "hi"}', "headers": {"Content-Type": "text/plain"}},
        {"content": '{"text": "hi"}', "headers": {}},
    ],
    ids=["form", "text-plain", "no-content-type"],
)
def test_cross_site_style_posts_are_refused(settings, content):
    # The "simple" requests a web page can send without a CORS preflight.
    client, _, _ = server(settings, AIMessage("x"))
    r = client.post("/v1/chat", content=content["content"], headers={**AUTH, **content["headers"]})
    assert r.status_code == 415


def test_no_cors_headers_are_ever_sent(settings):
    client, _, _ = server(settings, AIMessage("x"))
    origin = {"Origin": "https://evil.example"}
    responses = [
        client.get("/v1/health", headers={**AUTH, **origin}),
        client.options("/v1/chat", headers={**origin, "Access-Control-Request-Method": "POST"}),
    ]
    for r in responses:
        assert not any(h.lower().startswith("access-control-") for h in r.headers)
    assert responses[1].status_code == 401  # preflights carry no token: always refused


def test_docs_and_schema_are_not_exposed(settings):
    client, _, _ = server(settings)
    for path in ["/docs", "/redoc", "/openapi.json"]:
        assert client.get(path, headers=AUTH).status_code == 404


def test_short_tokens_are_rejected(settings):
    aura, _ = make_aura(settings)
    with pytest.raises(ValueError):
        create_app(aura, "short")


# ── turn control ────────────────────────────────────────────────────────────────────────────


def test_one_turn_at_a_time(settings):
    client, _, _ = server(settings, AIMessage("x"))
    client.app.state.turn.acquire()
    try:
        assert post(client, "/v1/chat", {"text": "hi"}).status_code == 409
        assert post(client, "/v1/approvals", {"approvals": [True]}).status_code == 409
        assert post(client, "/v1/conversation/new").status_code == 409
        assert client.get("/v1/health", headers=AUTH).json()["busy"] is True
    finally:
        client.app.state.turn.release()


def test_a_pending_approval_must_be_answered_before_a_new_message(settings):
    client, _, _ = server(settings, tool_turn(call("create_folder", path="X")))
    post(client, "/v1/chat", {"text": "Create X"})
    r = post(client, "/v1/chat", {"text": "Something else"})
    assert r.status_code == 409 and "approval is pending" in r.json()["error"]


def test_approvals_need_something_pending_and_the_right_count(settings):
    client, _, _ = server(settings, tool_turn(call("create_folder", path="X")))
    assert post(client, "/v1/approvals", {"approvals": [True]}).status_code == 409

    post(client, "/v1/chat", {"text": "Create X"})
    assert post(client, "/v1/approvals", {"approvals": [True, False]}).status_code == 422
    # A malformed answer must not lose the pending approval.
    assert client.get("/v1/health", headers=AUTH).json()["awaiting_approval"] is True
    assert kinds(events(post(client, "/v1/approvals", {"approvals": [True]})))[-1] == "done"


@pytest.mark.parametrize("body", [{"text": ""}, {"text": "x" * 20_001}, {}, {"text": 5}])
def test_invalid_messages_are_rejected(settings, body):
    client, _, _ = server(settings)
    assert post(client, "/v1/chat", body).status_code == 422


def test_new_conversation_forgets(settings):
    client, aura, _ = server(settings, AIMessage("a"), AIMessage("b"))
    events(post(client, "/v1/chat", {"text": "My name is Ada."}))
    thread = aura.thread_id
    assert post(client, "/v1/conversation/new").status_code == 204
    assert aura.thread_id != thread


def test_health_reports_state(settings):
    client, _, _ = server(settings, loaded=False)
    h = client.get("/v1/health", headers=AUTH).json()
    assert h == {
        "status": "ok",
        "model": "gemma4:26b",
        "model_loaded": False,
        "search_mode": "DuckDuckGo (fallback mode: no Tavily key)",
        "vault": str(settings.vault_dir.resolve()),
        "busy": False,
        "awaiting_approval": False,
        "active_turn": None,
        "last_failure": None,
        "interrupted_at_start": 0,
        "recoverable": 0,
        "recovery_note": None,
        "runtime_health": {"state": "idle", "provider": "ollama", "model": "gemma4:26b",
                           "detail": "Model will load on the next turn."},
        "governance_health": {"state": "healthy", "agent": {"state": "healthy",
                               "agents": {"active": 0, "suspended": 0, "expired": 0, "due": 0},
                               "detail": "No agent lifecycle action is due."},
                              "optional_connection_attention": []},
    }


# ── Smart-Warm ──────────────────────────────────────────────────────────────────────────────


def test_every_turn_and_warmup_restart_the_keep_alive_timer(settings):
    client, _, keeper = server(settings, AIMessage("x"))
    events(post(client, "/v1/chat", {"text": "hi"}))
    assert keeper.warms == 1
    assert post(client, "/v1/warmup").status_code == 202
    assert keeper.warms == 2


def test_keeper_talks_to_ollamas_native_api():
    k = ModelKeeper("http://localhost:11434/v1/", "gemma4:26b")
    assert (k.url, k.keep_alive) == ("http://localhost:11434", "5m")
    assert ModelKeeper("http://127.0.0.1:9/v1", "m").loaded() is None  # unreachable: unknown


def test_keeper_coalesces_overlapping_native_warmups(monkeypatch):
    keeper = ModelKeeper("http://localhost:11434/v1", "gemma4:26b")
    started, release = threading.Event(), threading.Event()
    calls = []

    def warm() -> bool:
        calls.append(True)
        started.set()
        assert release.wait(1)
        return True

    monkeypatch.setattr(keeper, "warm", warm)
    keeper.warm_soon()
    assert started.wait(1)
    keeper.warm_soon()
    assert calls == [True]

    release.set()
    deadline = time.monotonic() + 1
    key = (keeper.url, keeper.model, keeper.keep_alive)
    while key in ModelKeeper._warming and time.monotonic() < deadline:
        time.sleep(0.01)
    assert key not in ModelKeeper._warming


def test_keeper_does_not_coalesce_different_residency_policies(monkeypatch):
    normal = ModelKeeper("http://localhost:11434/v1", "gemma4:26b", "5m")
    resident = ModelKeeper("http://localhost:11434/v1", "gemma4:26b", -1)
    normal_started, resident_started, release = threading.Event(), threading.Event(), threading.Event()

    def warm(started: threading.Event) -> bool:
        started.set()
        assert release.wait(1)
        return True

    monkeypatch.setattr(normal, "warm", lambda: warm(normal_started))
    monkeypatch.setattr(resident, "warm", lambda: warm(resident_started))
    normal.warm_soon()
    assert normal_started.wait(1)
    resident.warm_soon()
    assert resident_started.wait(1)
    release.set()


def _ollama_up() -> bool:
    try:
        return httpx.get("http://localhost:11434/api/ps", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.mark.skipif(not _ollama_up(), reason="Ollama isn't running")
def test_live_warm_sets_a_five_minute_keep_alive():
    k = ModelKeeper("http://localhost:11434/v1", "gemma4:26b")
    assert k.warm() is True and k.loaded() is True
    out = subprocess.run(["ollama", "ps"], capture_output=True, text=True).stdout
    line = next(row for row in out.splitlines() if row.startswith("gemma4:26b"))
    assert any(f"{m} minutes from now" in line for m in (4, 5)), line


# ── the real process ────────────────────────────────────────────────────────────────────────


@pytest.mark.live
def test_server_process_announces_port_serves_and_exits_with_its_parent(tmp_path):
    """A real ``uv`` child process is an integration check, not an offline audit check.

    Running it from an audit shadow project makes uv create a second virtual environment in that
    worktree.  It is intentionally kept out of the bounded, offline Governor suite; developers
    can still run it explicitly with the rest of the live integration checks.
    """
    proc = subprocess.Popen(
        ["uv", "run", "aura-server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={k: v for k, v in os.environ.items() if k != "AURA_SERVER_TOKEN"},
    )  # fmt: skip
    try:
        proc.stdin.write(TOKEN + "\n")
        proc.stdin.flush()
        ready = proc.stdout.readline().strip()
        assert ready.startswith("AURA_READY port="), (ready, proc.stderr.read() if proc.poll() else "")
        base = f"http://127.0.0.1:{int(ready.split('=')[1])}"

        assert httpx.get(f"{base}/v1/health", headers=AUTH).json()["status"] == "ok"
        assert httpx.get(f"{base}/v1/health").status_code == 401

        proc.stdin.close()  # the app quits or crashes
        start = time.monotonic()
        proc.wait(timeout=15)
        assert time.monotonic() - start < 10
    finally:
        if proc.poll() is None:
            proc.kill()
