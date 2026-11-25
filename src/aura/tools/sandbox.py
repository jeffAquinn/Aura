"""Code_Interpreter_Tool: Python in a fresh, locked-down Docker container for every run.

No network, read-only root filesystem, unprivileged user, CPU/memory/process limits, the vault
mounted read-only at /vault, and a hard wall-clock timeout. The container is force-removed in a
`finally` block, so a timeout, crash or Ctrl-C can never leave one running.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from aura.audit import AuditLog

DEFAULT_IMAGE = "aura-sandbox:1"
# Built from stdin (no build context), so the image can be built from wherever Aura runs.
DOCKERFILE = """\
FROM python:3.12-slim
RUN pip install --no-cache-dir numpy==2.5.5 pandas==3.1.1
"""
OUTPUT_LIMIT = 8_000
DOCKER_CANDIDATES = ("~/.docker/bin/docker", "/usr/local/bin/docker", "/opt/homebrew/bin/docker")
AURA_CONTAINER_PREFIXES = ("aura-run-", "aura-dev-")
AURA_NETWORK = "aura-dev"


class SandboxUnavailable(Exception):
    """Docker is missing, not running, or the image can't be prepared."""


def cleanup_aura_docker(audit: AuditLog | None = None) -> None:
    """Stop only Docker resources that Aura owns when its server exits.

    This is namespace-scoped: it never runs Docker's global prune commands and never stops
    containers belonging to another app. Images remain cached because they are inert and needed
    for the next approved sandbox run; Aura's short-lived containers and internal network go away.
    """
    try:
        docker = find_docker()
        listed = subprocess.run([docker, "ps", "-a", "--format", "{{.ID}} {{.Names}}"],
                                capture_output=True, text=True, timeout=15)
        if listed.returncode != 0:
            return
        owned = []
        for line in listed.stdout.splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) == 2 and parts[1].startswith(AURA_CONTAINER_PREFIXES):
                owned.append(parts[0])
        for container in owned:
            subprocess.run([docker, "rm", "-f", container], capture_output=True, timeout=30)
        # The Developer's internal network has no user data or volumes and is recreated on demand.
        subprocess.run([docker, "network", "rm", AURA_NETWORK], capture_output=True, timeout=30)
        if audit is not None:
            audit.record("docker_cleanup", containers=len(owned), network=AURA_NETWORK)
    except (OSError, subprocess.SubprocessError, SandboxUnavailable):
        # Shutdown must never block Aura from quitting. The next server exit retries this cleanup.
        return


@dataclass(frozen=True)
class RunResult:
    stdout: str
    stderr: str
    exit_code: int | None  # None when timed out
    timed_out: bool
    seconds: float
    container: str


def find_docker() -> str:
    for c in (shutil.which("docker"), *DOCKER_CANDIDATES):
        if c and Path(c).expanduser().exists():
            return str(Path(c).expanduser())
    raise SandboxUnavailable("Docker isn't installed.")


def _daemon_down(stderr: str) -> bool:
    return "Cannot connect to the Docker daemon" in stderr or "error during connect" in stderr


class Sandbox:
    def __init__(self, image: str, vault_dir: Path, timeout: float, audit: AuditLog):
        self.image = image
        self.vault_dir = vault_dir.expanduser().resolve()
        self.timeout = timeout
        self.audit = audit
        self._image_ready = False

    def command(
        self, docker: str, name: str, image: str | None = None,
        mounts: tuple[tuple[Path, str], ...] = (), vault: bool = True,
    ) -> list[str]:
        extra: list[str] = []
        if vault:
            extra += ["--mount", f"type=bind,source={self.vault_dir},target=/vault,readonly"]
        for source, target in mounts:  # always read-only
            extra += ["--mount", f"type=bind,source={source},target={target},readonly"]
        return [
            docker, "run", "--rm", "-i", "--name", name,
            "--network", "none",                              # no internet, no LAN
            "--read-only", "--tmpfs", "/tmp:rw,size=64m,mode=1777",  # scratch space only
            "--memory", "512m", "--memory-swap", "512m",      # no swap beyond the cap
            "--cpus", "1", "--pids-limit", "64",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "65534:65534",                          # nobody
            "--workdir", "/tmp", "--env", "HOME=/tmp",
            *extra,
            image or self.image, "python", "-I", "-",         # code arrives on stdin
        ]  # fmt: skip

    def ensure_image(self, docker: str, on_status: Callable[[str], None] = lambda _: None) -> None:
        if self._image_ready:
            return
        probe = subprocess.run([docker, "image", "inspect", self.image], capture_output=True, text=True)
        if _daemon_down(probe.stderr):
            raise SandboxUnavailable("Docker isn't running. Start Docker Desktop and try again.")
        if probe.returncode != 0:
            if self.image != DEFAULT_IMAGE:
                raise SandboxUnavailable(f"Sandbox image '{self.image}' not found.")
            on_status("Preparing the sandbox image (one-time, about a minute)...")
            build = subprocess.run(
                [docker, "build", "-t", self.image, "-"],
                input=DOCKERFILE, capture_output=True, text=True, timeout=900,
            )
            if build.returncode != 0:
                raise SandboxUnavailable(f"Couldn't build the sandbox image: {build.stderr[-500:]}")
            self.audit.record("sandbox_image_built", image=self.image)
        self._image_ready = True

    def image_exists(self, tag: str) -> bool:
        probe = subprocess.run([find_docker(), "image", "inspect", tag], capture_output=True, text=True)
        if _daemon_down(probe.stderr):
            raise SandboxUnavailable("Docker isn't running. Start Docker Desktop and try again.")
        return probe.returncode == 0

    def build_image(self, tag: str, dockerfile: str, on_status: Callable[[str], None] = lambda _: None) -> None:
        """Build an image from a Dockerfile given as text (no build context)."""
        docker = find_docker()
        self.ensure_image(docker, on_status)  # skill images build on top of the base sandbox
        on_status(f"Building {tag}...")
        build = subprocess.run([docker, "build", "-t", tag, "-"], input=dockerfile,
                               capture_output=True, text=True, timeout=900)  # fmt: skip
        if build.returncode != 0:
            raise SandboxUnavailable(f"couldn't build {tag}: {build.stderr[-600:]}")

    def remove_image(self, tag: str) -> None:
        """Best effort: delete one orphaned Aura-owned image, never a user image.

        Docker Desktop itself remains under the user's control.  The Workshop calls this only
        after checking that no approved skill still references the image.
        """
        if not (tag.startswith("aura-skill-") or tag.startswith("aura-deps:")):
            return
        try:
            subprocess.run([find_docker(), "image", "rm", tag], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError, SandboxUnavailable):
            pass  # Docker is down: the image is unused and harmless; it can be pruned later

    def run(
        self, code: str, on_status: Callable[[str], None] = lambda _: None, image: str | None = None,
        mounts: tuple[tuple[Path, str], ...] = (), vault: bool = True,
    ) -> RunResult:
        docker = find_docker()
        if image is None:
            self.ensure_image(docker, on_status)
        name = f"aura-run-{uuid.uuid4().hex[:12]}"
        start = time.monotonic()
        try:
            proc = subprocess.run(
                self.command(docker, name, image, mounts, vault),
                input=code, capture_output=True, text=True, errors="replace",
                timeout=self.timeout,
            )
            result = RunResult(
                proc.stdout, proc.stderr, proc.returncode, False, time.monotonic() - start, name
            )
        except subprocess.TimeoutExpired as e:
            result = RunResult(
                _text(e.stdout), _text(e.stderr), None, True, time.monotonic() - start, name
            )
        finally:
            # Always force-remove: kills the container if it is still running (timeout, Ctrl-C,
            # crash) and is a harmless no-op when --rm already cleaned it up.
            try:
                subprocess.run([docker, "rm", "-f", name], capture_output=True, timeout=30)
            except (OSError, subprocess.SubprocessError) as e:
                self.audit.record("sandbox_cleanup_failed", container=name, error=str(e))
        if result.exit_code == 125 and _daemon_down(result.stderr):
            self._image_ready = False
            raise SandboxUnavailable("Docker isn't running. Start Docker Desktop and try again.")
        self.audit.record(
            "code_run", container=name, exit_code=result.exit_code, timed_out=result.timed_out,
            seconds=round(result.seconds, 2), code=code[:2000],
        )
        return result


def describe_run(code: str, timeout: float, preview_lines: int = 8) -> str:
    """Approval-card text for a code run: what will run, and the sandbox it runs in."""
    lines = code.strip().splitlines() or ["(empty)"]
    shown = [f"    │ {line[:100]}" for line in lines[:preview_lines]]
    if len(lines) > preview_lines:
        shown.append(f"    │ ... ({len(lines) - preview_lines} more lines)")
    head = f"run Python in the sandbox (no internet, vault read-only, {timeout:.0f}-second limit):"
    return "\n".join([head, *shown])


def _text(b: bytes | str | None) -> str:
    if b is None:
        return ""
    return b if isinstance(b, str) else b.decode("utf-8", errors="replace")


def format_result(r: RunResult, timeout: float) -> str:
    out, err = r.stdout.strip(), r.stderr.strip()
    if r.timed_out:
        head = f"Timed out after {timeout:.0f}s; the sandbox was stopped."
    else:
        head = f"Exit code {r.exit_code} ({r.seconds:.1f}s)."
        if r.exit_code == 137:
            head += " The process was killed, most likely for exceeding the 512 MB memory limit."
    body = ""
    if out:
        body += f"\nstdout:\n{out}"
    if err:
        body += f"\nstderr:\n{err}"
    if not body and not r.timed_out:
        body = "\n(no output; use print() to show results)"
    text = head + body
    if len(text) > OUTPUT_LIMIT:
        text = text[:OUTPUT_LIMIT] + f"\n[output truncated at {OUTPUT_LIMIT} characters]"
    return text


def sandbox_tools(sandbox: Sandbox, emit_status: Callable[[str], None]) -> list[BaseTool]:
    @tool
    def run_python(code: str) -> str:
        """Run Python 3.12 code in an isolated sandbox and return its output; use print() to show
        results. numpy and pandas are installed. The user's vault is mounted read-only at /vault
        (e.g. pd.read_csv('/vault/budget.csv')). No internet; nothing persists between runs;
        30-second limit."""
        try:
            return format_result(sandbox.run(code, on_status=emit_status), sandbox.timeout)
        except SandboxUnavailable as e:
            return f"The code sandbox is unavailable: {e}"

    return [run_python]
