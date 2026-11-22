"""Code interpreter sandbox: isolation, limits, and container cleanup.

Offline tests check the hardening flags and the try/finally cleanup with Docker mocked.
Live tests attack a real container and are skipped when Docker isn't running.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aura.audit import AuditLog
from aura.tools import sandbox as sb
from aura.tools.sandbox import Sandbox, SandboxUnavailable, find_docker, sandbox_tools


def make(tmp_path: Path, timeout: float = 30.0) -> Sandbox:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    return Sandbox(sb.DEFAULT_IMAGE, vault, timeout, AuditLog(tmp_path / "audit.jsonl"))


# ── offline ─────────────────────────────────────────────────────────────────────────────────


def test_command_carries_every_hardening_flag(tmp_path):
    s = make(tmp_path)
    cmd = " ".join(s.command("docker", "aura-run-x"))
    for flag in [
        "--rm",
        "--network none",
        "--read-only",
        "--memory 512m --memory-swap 512m",
        "--cpus 1",
        "--pids-limit 64",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--user 65534:65534",
        f"--mount type=bind,source={s.vault_dir},target=/vault,readonly",
    ]:
        assert flag in cmd, flag
    assert "-v " not in cmd and "--privileged" not in cmd and "--network host" not in cmd


class FakeDocker:
    """Stands in for subprocess.run: image present; `docker run` behaves as told."""

    def __init__(self, on_run):
        self.on_run, self.calls = on_run, []

    def __call__(self, args, **kw):
        self.calls.append(args)
        if args[1] == "run":
            return self.on_run(args, kw)
        return subprocess.CompletedProcess(args, 0, "", "")

    def removed(self) -> list[str]:
        return [a[3] for a in self.calls if a[1:3] == ["rm", "-f"]]

    def started(self) -> list[str]:
        return [a[a.index("--name") + 1] for a in self.calls if a[1] == "run"]


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.TimeoutExpired(cmd="docker", timeout=30, output=b"partial"),
        KeyboardInterrupt(),
        OSError("docker crashed"),
    ],
    ids=["timeout", "ctrl-c", "crash"],
)
def test_container_is_force_removed_in_finally(tmp_path, monkeypatch, failure):
    def on_run(args, kw):
        raise failure

    fake = FakeDocker(on_run)
    monkeypatch.setattr(sb.subprocess, "run", fake)
    s = make(tmp_path)

    if isinstance(failure, subprocess.TimeoutExpired):
        result = s.run("while True: pass")
        assert result.timed_out and result.stdout == "partial"
    else:
        with pytest.raises(type(failure)):
            s.run("x")
    assert fake.removed() == fake.started()  # the exact container that was started


def test_container_is_removed_after_a_normal_run_too(tmp_path, monkeypatch):
    fake = FakeDocker(lambda args, kw: subprocess.CompletedProcess(args, 0, "ok\n", ""))
    monkeypatch.setattr(sb.subprocess, "run", fake)
    assert make(tmp_path).run("print('ok')").stdout == "ok\n"
    assert fake.removed() == fake.started()


def test_docker_not_running_is_explained_to_the_model(tmp_path, monkeypatch):
    def run(args, **kw):
        return subprocess.CompletedProcess(args, 1, "", "Cannot connect to the Docker daemon at unix://...")

    monkeypatch.setattr(sb.subprocess, "run", run)
    (tool,) = sandbox_tools(make(tmp_path), emit_status=lambda s: None)
    assert tool.invoke({"code": "print(1)"}) == (
        "The code sandbox is unavailable: Docker isn't running. Start Docker Desktop and try again."
    )


# ── live: try to break out of a real container ──────────────────────────────────────────────


def _docker_up() -> bool:
    try:
        return subprocess.run([find_docker(), "info"], capture_output=True, timeout=10).returncode == 0
    except (SandboxUnavailable, subprocess.SubprocessError, OSError):
        return False


live = pytest.mark.skipif(not _docker_up(), reason="Docker isn't running")


@pytest.fixture
def box(tmp_path) -> Sandbox:
    s = make(tmp_path)
    (s.vault_dir / "budget.csv").write_text("item,amount\nrent,1200\nfood,450\nfun,150\n")
    return s


def py(box: Sandbox, code: str):
    r = box.run(code)
    assert not r.timed_out, r
    return r


@live
def test_runs_python_with_numpy_and_pandas_reading_the_vault(box):
    r = py(box, "import numpy as np, pandas as pd\nprint(pd.read_csv('/vault/budget.csv').amount.mean())")
    assert r.exit_code == 0 and r.stdout.strip() == "600.0", r


@live
def test_no_network_access(box):
    r = py(box, """
import socket
for target in [("1.1.1.1", 53), ("host.docker.internal", 80)]:
    try:
        socket.create_connection(target, timeout=3); print("CONNECTED", target)
    except OSError as e:
        print("blocked", type(e).__name__)
try:
    socket.getaddrinfo("example.com", 443); print("DNS OK")
except OSError:
    print("dns blocked")
""")
    assert "CONNECTED" not in r.stdout and "DNS OK" not in r.stdout, r.stdout
    assert r.stdout.count("blocked") == 3


@live
def test_vault_is_readable_but_read_only(box):
    r = py(box, """
print(open('/vault/budget.csv').readline().strip())
for p in ['/vault/budget.csv', '/vault/new.txt']:
    try:
        open(p, 'a').write('x'); print('WROTE', p)
    except OSError as e:
        print('refused', e.errno)
""")
    assert r.stdout.splitlines()[0] == "item,amount"
    assert "WROTE" not in r.stdout and r.stdout.count("refused") == 2
    assert sorted(p.name for p in box.vault_dir.iterdir()) == ["budget.csv"]
    assert (box.vault_dir / "budget.csv").read_text().endswith("fun,150\n")


@live
def test_root_filesystem_is_read_only_and_host_is_invisible(box):
    r = py(box, """
import os
for p in ['/etc/evil', '/usr/local/lib/evil.py', '/evil']:
    try:
        open(p, 'w').write('x'); print('WROTE', p)
    except OSError:
        print('refused')
open('/tmp/scratch', 'w').write('ok'); print('tmp ok')
print('host visible' if os.path.exists('/Users') else 'host hidden')
""")
    assert "WROTE" not in r.stdout and r.stdout.count("refused") == 3
    assert "tmp ok" in r.stdout and "host hidden" in r.stdout


@live
def test_runs_unprivileged_with_no_capabilities(box):
    r = py(box, """
import os
print(os.getuid(), os.getgid())
print([l for l in open('/proc/self/status') if l.startswith('CapEff')][0].split()[1])
""")
    uid_gid, cap_eff = r.stdout.splitlines()
    assert uid_gid == "65534 65534" and int(cap_eff, 16) == 0


@live
def test_memory_limit_is_enforced(box):
    r = py(box, "x = bytearray(1024 ** 3)\nprint('allocated 1 GB')")
    assert r.exit_code != 0 and "allocated" not in r.stdout


@live
def test_each_run_gets_a_fresh_disposable_container(box):
    py(box, "open('/tmp/state', 'w').write('left behind')")
    r = py(box, "import os; print(os.path.exists('/tmp/state'))")
    assert r.stdout.strip() == "False"


@live
def test_timeout_kills_the_container_and_leaves_nothing_behind(tmp_path):
    box = make(tmp_path, timeout=3)
    r = box.run("while True: pass")
    assert r.timed_out and r.seconds < 6
    leftover = subprocess.run(
        [find_docker(), "ps", "-aq", "--filter", f"name={r.container}"], capture_output=True, text=True
    )
    assert leftover.stdout.strip() == ""
