"""Aura's own llama-server (aura.llama_server): started for a llama.cpp model, stopped when another
model is chosen, when idle, and when Aura quits. A stand-in program plays llama-server."""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from aura.audit import AuditLog
from aura.config import Settings
from aura.llama_server import API_KEY, LlamaServer, LocalModelError, gguf_models, keep_alive_seconds
from aura.model_routing import ModelChoice

# Answers /health once "loaded", and writes down how it was started (its argv and API key).
FAKE = '''#!{python}
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
argv = sys.argv[1:]
port = int(argv[argv.index("--port") + 1])
with open(os.environ["FAKE_RECORD"], "a") as f:
    f.write(json.dumps({{"argv": argv, "key": os.environ.get("LLAMA_API_KEY")}}) + "\\n")
if os.environ.get("FAKE_CRASH"):
    sys.exit(1)
time.sleep(float(os.environ.get("FAKE_LOAD", "0")))
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404); self.end_headers(); self.wfile.write(b"{{}}")
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", port), H).serve_forever()
'''


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    models = tmp_path / "models"
    (models / "bonsai").mkdir(parents=True)
    (models / "bonsai" / "Bonsai-27B-Q2_0.gguf").write_bytes(b"GGUF")
    (models / "Small-1B.gguf").write_bytes(b"GGUF")
    (models / "mmproj-Small-1B.gguf").write_bytes(b"GGUF")  # a vision projector: not a model
    (models / "Big-00002-of-00002.gguf").write_bytes(b"GGUF")
    (models / "Big-00001-of-00002.gguf").write_bytes(b"GGUF")
    binary = tmp_path / "bin" / "llama-server"
    binary.parent.mkdir()
    binary.write_text(FAKE.format(python=sys.executable))
    binary.chmod(0o755)
    record = tmp_path / "started.jsonl"
    monkeypatch.setenv("FAKE_RECORD", str(record))
    settings = Settings(data_dir=tmp_path / "data", audit_log=tmp_path / "logs" / "actions.jsonl",
                        llamacpp_url=f"http://127.0.0.1:{free_port()}/v1", llamacpp_models_dir=models,
                        llama_server=str(binary))
    servers: list[LlamaServer] = []

    def make(**changes) -> LlamaServer:
        server = LlamaServer(replace(settings, **changes), audit=AuditLog(tmp_path / "audit.jsonl"), watch_every=3600)
        servers.append(server)
        return server

    yield make, record
    for server in servers:
        server.close()


def last(server: LlamaServer) -> dict:
    """The newest entry in the server's audit log, without its timestamp."""
    entry = dict(server.audit.entries()[-1])
    entry.pop("ts")
    return entry


def starts(record: Path) -> list[dict]:
    return [json.loads(line) for line in record.read_text().splitlines()] if record.exists() else []


def test_models_are_the_gguf_files_by_name(setup):
    make, _ = setup
    assert make().models() == ["Big", "Bonsai-27B-Q2_0", "Small-1B"]
    assert gguf_models(make().settings)["Big"].name == "Big-00001-of-00002.gguf"


def test_no_program_means_no_models(setup, tmp_path):
    make, _ = setup
    assert make(llama_server=str(tmp_path / "missing")).models() == []


def test_a_turn_starts_the_server_for_its_model_with_a_secret_key_out_of_sight(setup):
    make, record = setup
    server = make()
    assert server.loaded("Small-1B") is False
    server.prepare(ModelChoice("llamacpp", "Small-1B"))
    assert server.loaded("Small-1B")
    [started] = starts(record)
    assert started["key"] == API_KEY and API_KEY not in " ".join(started["argv"])  # environment, not argv
    argv = started["argv"]
    assert argv[argv.index("--alias") + 1] == "Small-1B"
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert argv[argv.index("-c") + 1] == "32768"  # llamacpp_context_tokens
    assert {"--jinja", "-fa", "--cache-type-k", "--cors-origins"} <= set(argv)
    assert last(server)["event"] == "local_model_loaded"

    server.prepare(ModelChoice("llamacpp", "Small-1B"))  # already loaded: nothing restarts
    assert len(starts(record)) == 1


def test_choosing_another_model_stops_the_server(setup):
    make, record = setup
    server = make()
    server.prepare(ModelChoice("llamacpp", "Small-1B"))
    pid = server._proc.pid
    server.prepare(ModelChoice("llamacpp", "Bonsai-27B-Q2_0"))  # another llama.cpp model: restarted with it
    assert server.loaded("Bonsai-27B-Q2_0") and not server.loaded("Small-1B")
    assert len(starts(record)) == 2 and not alive(pid)

    pid = server._proc.pid
    server.prepare(ModelChoice("ollama", "qwen3.5:9b"))  # an Ollama model: llama-server stops
    assert server._proc is None and not alive(pid) and not server.pid_file.exists()
    assert last(server) == {"event": "local_model_unloaded", "model": "Bonsai-27B-Q2_0", "provider": "llamacpp",
                            "reason": "another model was chosen"}


def test_an_idle_model_unloads_after_the_keep_alive_but_never_during_a_turn(setup):
    make, _ = setup
    server = make(model_keep_alive="10m")
    assert server.idle_after == 600
    server.prepare(ModelChoice("llamacpp", "Small-1B"))
    assert server.check_idle() is False  # just used
    server._last_used -= 601
    busy = True
    server.busy = lambda: busy
    assert server.check_idle() is False and server.loaded("Small-1B")  # a turn is running
    busy = False
    assert server.check_idle() is True and server._proc is None
    assert last(server)["reason"] == "idle for 10m"


def test_quitting_stops_the_server(setup):
    make, _ = setup
    server = make()
    server.prepare(ModelChoice("llamacpp", "Small-1B"))
    pid = server._proc.pid
    server.close()
    assert not alive(pid)


def test_a_server_left_by_a_crashed_aura_is_stopped_at_the_next_start(setup):
    make, _ = setup
    first = make()
    first.prepare(ModelChoice("llamacpp", "Small-1B"))
    pid = first._proc.pid
    first._proc = None  # Aura "crashed": its child is still running, its pid file is left
    make()
    deadline = time.monotonic() + 5
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(pid)


def test_a_pid_file_naming_another_program_is_left_alone(setup):
    make, _ = setup
    server = make()
    server.pid_file.parent.mkdir(parents=True, exist_ok=True)
    # this test process: not a llama-server, and its owner is gone
    server.pid_file.write_text(json.dumps({"pid": os.getpid(), "owner": 2**22 - 1}))
    make()
    assert alive(os.getpid()) and not server.pid_file.exists()


def test_problems_say_what_to_do(setup, monkeypatch, tmp_path):
    make, _ = setup
    with pytest.raises(LocalModelError, match="can't find"):
        make().ensure("Nope")
    with pytest.raises(LocalModelError, match="brew install llama.cpp"):
        make(llama_server=str(tmp_path / "missing")).ensure("Small-1B")
    monkeypatch.setenv("FAKE_CRASH", "1")
    server = make()
    with pytest.raises(LocalModelError, match="stopped while loading"):
        server.ensure("Small-1B")
    assert server._proc is None


def test_a_port_already_in_use_is_reported_not_taken_over(setup):
    make, _ = setup
    server = make()
    with socket.socket() as other:
        other.bind(("127.0.0.1", server.port))
        other.listen()
        with pytest.raises(LocalModelError, match="Another program is using"):
            server.ensure("Small-1B")


def test_keep_alive_forms():
    assert keep_alive_seconds("10m") == 600
    assert keep_alive_seconds("30s") == 30
    assert keep_alive_seconds("1h") == 3600
    assert keep_alive_seconds("1h30m") == 5400
    assert keep_alive_seconds("300ms") == 0.3
    assert keep_alive_seconds(300) == 300
    assert keep_alive_seconds("-1") is None and keep_alive_seconds("-1m") is None
    assert keep_alive_seconds("soon") == 600 and keep_alive_seconds("") == 600  # never stops Aura starting


def test_a_second_aura_on_the_same_data_folder_leaves_the_first_one_s_server_alone(setup):
    make, _ = setup
    first = make()
    first.prepare(ModelChoice("llamacpp", "Small-1B"))
    saved = json.loads(first.pid_file.read_text())
    saved["owner"] = os.getppid()  # another Aura, still running
    first.pid_file.write_text(json.dumps(saved))
    make()
    assert first.loaded("Small-1B")


def test_a_load_gives_way_to_a_later_pick_and_to_stop(setup, monkeypatch):
    import threading

    make, _ = setup
    monkeypatch.setenv("FAKE_LOAD", "30")  # a slow load
    server = make()
    errors: list[str] = []

    def load() -> None:
        try:
            server.prepare(ModelChoice("llamacpp", "Small-1B"))
        except LocalModelError as e:
            errors.append(str(e))

    loading = threading.Thread(target=load)
    loading.start()
    time.sleep(0.5)
    began = time.monotonic()
    server.prepare(ModelChoice("ollama", "qwen3.5:9b"))  # picked another model meanwhile
    loading.join(10)
    assert time.monotonic() - began < 10 and errors == ["Loading 'Small-1B' was stopped."]
    assert server._proc is None

    cancel = threading.Event()
    cancel.set()  # Stop pressed
    with pytest.raises(LocalModelError, match="was stopped"):
        server.prepare(ModelChoice("llamacpp", "Small-1B"), cancel)


def test_a_server_that_exits_by_itself_is_recorded_as_such(setup):
    make, _ = setup
    server = make()
    server.prepare(ModelChoice("llamacpp", "Small-1B"))
    server._proc.kill()
    server._proc.wait()
    server.prepare(ModelChoice("llamacpp", "Small-1B"))  # started again
    unloaded = [e for e in server.audit.entries() if e["event"] == "local_model_unloaded"]
    assert unloaded[-1]["reason"] == "llama-server exited by itself (code -9)"
    assert server.loaded("Small-1B")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a stopped child of this process is a zombie until reaped: not running
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return True
