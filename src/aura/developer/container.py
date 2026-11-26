"""The Developer container: where Claude Code and Codex work, isolated from this Mac.

- Files: the build's workspace (mounted at /work) and nothing else of the host: no vault, no home
  folder, no Aura repository. The rest of the filesystem is the image's, read-only.
- Network: an internal Docker network with no route out. The engine's vendor hostnames resolve to
  the egress allowlist proxy (egress.py), which passes only those, still encrypted. Nothing else
  even resolves.
- Limits: no Linux capabilities, no privilege escalation, memory, CPU and process caps, a time
  limit, and a Stop that kills the container at once.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from aura.audit import AuditLog
from aura.developer.engines import CLAUDE_VERSION, CODEX_VERSION, Engine, EngineEvent
from aura.secrets import Secrets
from aura.tools.sandbox import DEFAULT_IMAGE, Sandbox, SandboxUnavailable, _daemon_down, find_docker

IMAGE = f"aura-dev:codex-{CODEX_VERSION}-claude-{CLAUDE_VERSION}"
DOCKERFILE = f"""\
FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates python3 \\
 && rm -rf /var/lib/apt/lists/*
RUN npm install -g @openai/codex@{CODEX_VERSION} @anthropic-ai/claude-code@{CLAUDE_VERSION} \\
 && npm cache clean --force
USER 1000
WORKDIR /work
"""
NETWORK, PROXY = "aura-dev", "aura-dev-egress"
SUBNETS = ("172.30.99.0/24", "172.31.199.0/24", "10.213.47.0/24", "192.168.213.0/24")  # first free one
EGRESS = Path(__file__).with_name("egress.py")
PROBLEM_TTL = 5.0  # seconds


@dataclass
class RunOutcome:
    exit_code: int | None = None
    cost_usd: float = 0.0
    ok: bool = False  # the engine reported success
    result: str = ""
    timed_out: bool = False
    stopped: bool = False
    error: str | None = None  # couldn't run at all (Docker down, image build failed, ...)
    egress: list[str] = field(default_factory=list)  # the proxy's ALLOW/DENY lines


class DevContainer:
    def __init__(self, sandbox: Sandbox, secrets: Secrets, audit: AuditLog):
        self.sandbox = sandbox
        self.secrets = secrets
        self.audit = audit
        self.proxy_ip = ""  # the allowlist's address on the Developer network (set by ensure)
        self._checked: tuple[float, str | None] = (-PROBLEM_TTL, None)  # (when, problem())

    # ── one-time setup ──────────────────────────────────────────────────────────────────────

    def problem(self) -> str | None:
        """Why no build can run right now (None: Docker is ready). Cached for a few seconds: the
        Workshop polls while a build runs."""
        if time.monotonic() - self._checked[0] < PROBLEM_TTL:
            return self._checked[1]
        try:
            docker = find_docker()
            probe = subprocess.run([docker, "info"], capture_output=True, text=True, timeout=15)
            problem = "Start Docker Desktop." if probe.returncode != 0 else None
        except SandboxUnavailable as e:
            problem = str(e)
        except (OSError, subprocess.SubprocessError):
            problem = "Docker isn't responding. Restart Docker Desktop."
        self._checked = (time.monotonic(), problem)
        return problem

    def ensure(self, on_status: Callable[[str], None]) -> str:
        docker = find_docker()
        self.sandbox.ensure_image(docker, on_status)  # the proxy runs on the base sandbox image
        probe = subprocess.run([docker, "image", "inspect", IMAGE], capture_output=True, text=True)
        if _daemon_down(probe.stderr):
            raise SandboxUnavailable("Docker isn't running. Start Docker Desktop and try again.")
        if probe.returncode != 0:
            on_status("Preparing the Developer container (one-time, a minute or two)…")
            build = subprocess.run([docker, "build", "-t", IMAGE, "-"], input=DOCKERFILE,
                                   capture_output=True, text=True, timeout=1200)  # fmt: skip
            if build.returncode != 0:
                raise SandboxUnavailable(f"couldn't build the Developer container: {build.stderr[-500:]}")
            self.audit.record("developer_image_built", image=IMAGE)
        self.proxy_ip = self._network(docker)
        return docker

    def _network(self, docker: str) -> str:
        """The internal Developer network (created once, on the first free subnet); returns the
        address the egress allowlist takes on it."""
        subnet = subprocess.run([docker, "network", "inspect", NETWORK, "--format", "{{(index .IPAM.Config 0).Subnet}}"],
                                capture_output=True, text=True).stdout.strip()  # fmt: skip
        if not subnet:
            errors = []
            for candidate in SUBNETS:
                made = subprocess.run([docker, "network", "create", "--internal", "--subnet", candidate, NETWORK],
                                      capture_output=True, text=True)  # fmt: skip
                if made.returncode == 0:
                    subnet = candidate
                    break
                errors.append(made.stderr.strip()[-120:])
            else:
                raise SandboxUnavailable(f"couldn't create the Developer network: {errors[-1]}")
        base = subnet.split("/")[0].rsplit(".", 1)[0]
        return f"{base}.2"

    # ── a run ───────────────────────────────────────────────────────────────────────────────

    def run(self, engine: Engine, workspace: Path, run_dir: Path, prompt: str, budget_usd: float,
            timeout: float, on_event: Callable[[EngineEvent], None], stop: threading.Event,
            workspace_read_only: bool = False) -> RunOutcome:  # fmt: skip
        outcome = RunOutcome()
        try:
            docker = self.ensure(lambda s: on_event(EngineEvent("status", s)))
        except (SandboxUnavailable, OSError, subprocess.SubprocessError) as e:
            outcome.error = str(e)
            return outcome
        name = f"aura-dev-{uuid.uuid4().hex[:8]}"
        engine.prepare(run_dir)
        try:
            self._start_proxy(docker, engine.hosts)
            outcome = self._run_engine(docker, name, engine, workspace, run_dir, prompt, budget_usd,
                                       timeout, on_event, stop, workspace_read_only)  # fmt: skip
        except (SandboxUnavailable, OSError, subprocess.SubprocessError) as e:
            outcome.error = str(e)
        finally:
            subprocess.run([docker, "rm", "-f", name], capture_output=True)
            outcome.egress = self._stop_proxy(docker)
            engine.finish(run_dir)
        denied = [line for line in outcome.egress if line.startswith("DENY")]
        self.audit.record("developer_run", engine=engine.name, exit_code=outcome.exit_code,
                          cost_usd=round(outcome.cost_usd, 4), timed_out=outcome.timed_out,
                          stopped=outcome.stopped, egress_denied=denied[:20])  # fmt: skip
        return outcome

    def _start_proxy(self, docker: str, hosts: tuple[str, ...]) -> None:
        subprocess.run([docker, "rm", "-f", PROXY], capture_output=True)
        started = subprocess.run(
            [docker, "run", "-d", "--name", PROXY, "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
             "--memory", "128m", "--pids-limit", "128", "--read-only",
             "-e", f"ALLOW_HOSTS={','.join(hosts)}", "-v", f"{EGRESS}:/egress.py:ro",
             DEFAULT_IMAGE, "python", "/egress.py"],
            capture_output=True, text=True,
        )  # fmt: skip
        if started.returncode != 0:
            raise SandboxUnavailable(f"couldn't start the egress allowlist: {started.stderr[-300:]}")
        joined = subprocess.run([docker, "network", "connect", "--ip", self.proxy_ip, NETWORK, PROXY],
                                capture_output=True, text=True)  # fmt: skip
        if joined.returncode != 0:
            raise SandboxUnavailable(f"couldn't connect the egress allowlist: {joined.stderr[-300:]}")
        for _ in range(50):
            logs = subprocess.run([docker, "logs", PROXY], capture_output=True, text=True).stdout
            if "READY" in logs:
                return
            time.sleep(0.1)
        raise SandboxUnavailable("the egress allowlist didn't start")

    def _stop_proxy(self, docker: str) -> list[str]:
        logs = subprocess.run([docker, "logs", PROXY], capture_output=True, text=True).stdout
        subprocess.run([docker, "rm", "-f", PROXY], capture_output=True)
        return [line for line in logs.splitlines() if line.startswith(("ALLOW", "DENY"))]

    def argv(self, docker: str, name: str, engine: Engine, workspace: Path, run_dir: Path,
             command: list[str], workspace_read_only: bool = False) -> list[str]:  # fmt: skip
        """The isolated `docker run` for an engine (the leak test runs plain commands in it too)."""
        return [docker, "run", "--rm", "--name", name, "--network", NETWORK,
                *[a for h in engine.hosts for a in ("--add-host", f"{h}:{self.proxy_ip}")],
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--memory", "4g", "--cpus", "4", "--pids-limit", "512",
                "--read-only", "--tmpfs", "/tmp:rw,size=512m", "--tmpfs", "/home/node:rw,size=512m,uid=1000,gid=1000",
                "-v", f"{workspace}:/work" + (":ro" if workspace_read_only else ""),
                *[a for host, inside in engine.mounts(run_dir) for a in ("-v", f"{host}:{inside}")],
                *[a for n in engine.env_names() for a in ("-e", n)],  # values come from the environment
                IMAGE, *command]  # fmt: skip

    def _run_engine(self, docker: str, name: str, engine: Engine, workspace: Path, run_dir: Path, prompt: str,
                    budget_usd: float, timeout: float, on_event: Callable[[EngineEvent], None],
                    stop: threading.Event, workspace_read_only: bool = False) -> RunOutcome:  # fmt: skip
        outcome = RunOutcome()
        argv = self.argv(docker, name, engine, workspace, run_dir, engine.command(prompt, budget_usd),
                         workspace_read_only)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", ""),
               **engine.env_values(self.secrets)}  # fmt: skip
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        stderr: list[str] = []
        drain = threading.Thread(target=lambda: stderr.extend(proc.stderr), daemon=True)  # type: ignore[arg-type]
        drain.start()
        deadline = time.monotonic() + timeout

        def watchdog() -> None:
            while proc.poll() is None:
                if stop.is_set() or time.monotonic() > deadline:
                    outcome.stopped, outcome.timed_out = stop.is_set(), not stop.is_set()
                    subprocess.run([docker, "kill", name], capture_output=True)
                    return
                time.sleep(0.25)

        threading.Thread(target=watchdog, daemon=True).start()
        for line in proc.stdout:  # type: ignore[union-attr]
            for event in engine.parse(line):
                if event.kind == "result":
                    outcome.ok, outcome.result = event.ok, event.text
                    outcome.cost_usd = event.cost_usd or outcome.cost_usd
                on_event(event)
        outcome.exit_code = proc.wait()
        drain.join(timeout=2)
        if outcome.exit_code not in (0, None) and not (outcome.stopped or outcome.timed_out) and not outcome.result:
            outcome.error = ("".join(stderr).strip()[-400:] or f"the engine exited with code {outcome.exit_code}")
        return outcome
