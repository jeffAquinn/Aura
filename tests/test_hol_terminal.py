"""The interactive HOL shell is a Docker container with the Vault as its only host mount."""

from __future__ import annotations

from collections import deque
from pathlib import Path
import subprocess
import time

import pytest

from aura.audit import AuditLog
from aura.hol_terminal import HolTerminal, HolTerminalError, Terminal
from aura.masking import MASK, Masker
from aura.tools.sandbox import Sandbox
from aura.tools.sandbox import SandboxUnavailable, find_docker


class FakeProcess:
    def poll(self): return None


def terminal(tmp_path: Path) -> HolTerminal:
    vault = tmp_path / "Aura_Vault"
    (vault / "Aura").mkdir(parents=True)
    audit = AuditLog(tmp_path / "audit.jsonl")
    return HolTerminal(vault, Sandbox("aura-sandbox:1", vault, 30, audit), audit)


def test_container_argv_has_one_writable_host_mount_and_all_hardening_flags(tmp_path):
    manager = terminal(tmp_path)
    argv = manager.argv("docker", "aura-hol-test")
    command = " ".join(argv)
    for flag in [
        "--network none", "--read-only", "--memory 1g --memory-swap 1g", "--cpus 1",
        "--pids-limit 64", "--ulimit nofile=256:256", "--cap-drop ALL",
        "--security-opt no-new-privileges", "--workdir /vault/Aura",
        f"type=bind,source={manager.vault_dir},target=/vault",
    ]:
        assert flag in command, flag
    assert "docker.sock" not in command
    assert "--privileged" not in command
    assert "--network host" not in command
    assert "readonly" not in command.split("target=/vault", 1)[1].split()[:1]


def test_terminal_output_uses_absolute_cursor_when_the_bounded_buffer_evicts(tmp_path):
    manager = terminal(tmp_path)
    process = FakeProcess()
    state = Terminal("terminal", "aura-hol-terminal", "docker", process)  # type: ignore[arg-type]
    state.output = deque(["new-1", "new-2"], maxlen=2)
    state.output_start = 7
    manager._terminals[state.id] = state

    assert manager.status(state.id, 0) == {"id": state.id, "running": True, "output": ["new-1", "new-2"], "next": 9}
    assert manager.status(state.id, 8)["output"] == ["new-2"]
    assert manager.status(state.id, 9)["output"] == []


def test_terminal_masks_secret_shaped_and_stored_output_before_it_reaches_the_ui(tmp_path):
    manager = terminal(tmp_path)
    state = Terminal("terminal", "aura-hol-terminal", "docker", FakeProcess(), masker=Masker(lambda: ["literal-secret-12345"]))  # type: ignore[arg-type]
    manager._terminals[state.id] = state
    manager._append(state, "literal-secret-12345 sk-ant-abcdefghijklmnopqrstuvwxyz\n")
    assert manager.status(state.id, 0)["output"] == [f"{MASK} {MASK}"]


@pytest.mark.parametrize("command", ["", " ", "x\0y", "x" * 12_001])
def test_terminal_rejects_invalid_command_before_writing_to_a_container(tmp_path, command):
    manager = terminal(tmp_path)
    with pytest.raises(HolTerminalError):
        manager.send("missing", command)


def _docker_up() -> bool:
    try:
        return subprocess.run([find_docker(), "info"], capture_output=True, timeout=10).returncode == 0
    except (SandboxUnavailable, subprocess.SubprocessError, OSError):
        return False


@pytest.mark.live
@pytest.mark.skipif(not _docker_up(), reason="Docker isn't running")
def test_live_terminal_can_only_write_the_vault_mount_and_has_no_network(tmp_path):
    manager = terminal(tmp_path)
    state = manager.start("test")
    try:
        manager.send(state["id"], "touch /vault/Aura/inside.txt; test ! -e /Users && echo HOST_HIDDEN; command -v git || echo GIT_ABSENT; python -c \"import socket; socket.create_connection(('1.1.1.1', 53), 1)\" || echo NETWORK_BLOCKED")
        output = ""
        for _ in range(40):
            output = "\n".join(manager.status(state["id"], 0)["output"])
            if "HOST_HIDDEN" in output and "GIT_ABSENT" in output and "NETWORK_BLOCKED" in output:
                break
            time.sleep(0.1)
        assert (manager.vault_dir / "Aura" / "inside.txt").is_file()
        assert "HOST_HIDDEN" in output and "GIT_ABSENT" in output and "NETWORK_BLOCKED" in output
    finally:
        manager.stop(state["id"])
