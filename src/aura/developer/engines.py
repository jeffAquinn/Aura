"""The Developers: Claude Code and Codex, as zero-trust external agents.

Each engine runs only inside the Developer container (see container.py). That container sees the
build's workspace and nothing else on this Mac, and its only network route is an allowlist of the
engine's own vendor. Here: how to invoke each engine, which hosts it may reach, which credential it
gets, and how its event stream becomes status lines for the Workshop.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aura.secrets import Secrets

CODEX_VERSION = "0.171.0"
CLAUDE_VERSION = "2.1.330"
CODEX_HOME = Path("~/.codex").expanduser()


@dataclass
class EngineEvent:
    kind: str  # "status" (an action), "message" (the engine's own words), "result", "error"
    text: str = ""
    cost_usd: float | None = None  # on "result" (Claude Code)
    ok: bool = True


def _short(path: str) -> str:
    return path.split("/work/", 1)[-1] if "/work/" in path else path


class Engine:
    name: str
    label: str
    vendor: str
    hosts: tuple[str, ...]
    plan: str = "its own plan"  # how it's paid for, in the user's terms ("your Claude plan")
    caps_cost: bool  # whether a dollar budget is enforced by the engine itself

    def problem(self, secrets: Secrets) -> str | None:
        """Why this engine can't run right now (None: ready)."""
        raise NotImplementedError

    def command(self, prompt: str, budget_usd: float) -> list[str]:
        raise NotImplementedError

    def env_names(self) -> list[str]:
        """Variables passed by name into the container (values never appear in a command line)."""
        return []

    def env_values(self, secrets: Secrets) -> dict[str, str]:
        return {}

    def mounts(self, run_dir: Path) -> list[tuple[Path, str]]:
        return []

    def prepare(self, run_dir: Path) -> None:
        pass

    def finish(self, run_dir: Path) -> None:
        pass

    def parse(self, line: str) -> list[EngineEvent]:
        raise NotImplementedError


class ClaudeCode(Engine):
    """Claude Code on the user's Claude plan (the user's choice, 2026-11-23; it replaced Gemma's
    dedicated API key). It signs in with a long-lived token from `claude setup-token`, kept in the
    Keychain and passed only into the Developer container. No --bare: that mode accepts only an API
    key. The container gives the isolation --bare gave (no hooks, plugins, CLAUDE.md or memory of
    the user's: its home folder is empty and thrown away), and an API key is never passed, so
    nothing is billed per use."""

    name, label, vendor, plan = "claude", "Claude Code", "Anthropic", "your Claude plan"
    hosts = ("api.anthropic.com",)
    caps_cost = False  # a plan, not a bill: the limits are time and rounds, as for Codex

    def problem(self, secrets: Secrets) -> str | None:
        if not secrets.get("claude_oauth_token"):
            return "Sign in with your Claude plan in Settings → Keys (run `claude setup-token` in Terminal)."
        return None

    def command(self, prompt: str, budget_usd: float) -> list[str]:
        return [
            "claude", "-p", prompt, "--no-session-persistence",
            "--output-format", "stream-json", "--verbose",
            "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--allowedTools", "Read", "Glob", "Grep", "Edit", "Write", "Bash",
            "--disallowedTools", "WebFetch", "WebSearch", "Agent",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers": {}}',
        ]  # fmt: skip

    def env_names(self) -> list[str]:
        return ["CLAUDE_CODE_OAUTH_TOKEN", "DISABLE_TELEMETRY", "DISABLE_ERROR_REPORTING",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "DISABLE_AUTOUPDATER"]  # fmt: skip

    def env_values(self, secrets: Secrets) -> dict[str, str]:
        return {"CLAUDE_CODE_OAUTH_TOKEN": secrets.get("claude_oauth_token") or "", "DISABLE_TELEMETRY": "1",
                "DISABLE_ERROR_REPORTING": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_AUTOUPDATER": "1"}  # fmt: skip

    def parse(self, line: str) -> list[EngineEvent]:
        try:
            e = json.loads(line)
        except ValueError:
            return []
        if e.get("type") == "assistant":
            out = []
            for part in (e.get("message") or {}).get("content", []):
                if part.get("type") == "tool_use":
                    out.append(EngineEvent("status", _describe_tool(part.get("name", ""), part.get("input") or {})))
                elif part.get("type") == "text" and part.get("text", "").strip():
                    out.append(EngineEvent("message", part["text"].strip()))
            return out
        if e.get("type") == "result":
            ok = not e.get("is_error") and e.get("subtype") == "success"
            return [EngineEvent("result", str(e.get("result") or e.get("subtype") or "")[:2000],
                                cost_usd=float(e.get("total_cost_usd") or 0.0), ok=ok)]  # fmt: skip
        return []


def _describe_tool(name: str, args: dict[str, Any]) -> str:
    path = _short(str(args.get("file_path") or args.get("path") or ""))
    if name == "Write":
        return f"Writing {path}"
    if name in ("Edit", "MultiEdit"):
        return f"Editing {path}"
    if name == "Read":
        return f"Reading {path}"
    if name == "Bash":
        return f"Running: {str(args.get('command', ''))[:120]}"
    if name in ("Glob", "Grep"):
        return f"Searching the workspace ({args.get('pattern', '')})"
    return f"Using {name}"


class Codex(Engine):
    """Codex with the user's ChatGPT sign-in. Its own macOS sandbox can read the whole disk (Aura's
    leak test showed it), so it runs only in the Developer container, with its sign-in copied in for
    the run. ChatGPT-side extras (apps, plugins, browser and computer use) are switched off."""

    name, label, vendor, plan = "codex", "Codex", "OpenAI", "your ChatGPT plan"
    hosts = ("chatgpt.com", "auth.openai.com", "api.openai.com")
    caps_cost = False
    OFF = ("apps", "plugins", "remote_plugin", "browser_use", "browser_use_external", "computer_use",
           "image_generation", "in_app_browser", "hooks")  # fmt: skip

    def __init__(self, home: Path = CODEX_HOME):
        self.home = home

    def problem(self, secrets: Secrets) -> str | None:
        if not (self.home / "auth.json").is_file():
            return "Sign in to Codex first (run `codex login` in Terminal)."
        return None

    def command(self, prompt: str, budget_usd: float) -> list[str]:
        disable = [arg for feature in self.OFF for arg in ("--disable", feature)]
        return ["codex", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
                "--json", "-C", "/work", "--sandbox", "danger-full-access", *disable, prompt]  # fmt: skip
        # danger-full-access applies *inside* the container, which is the sandbox: Codex's own Linux
        # sandbox can't start there, and the container already confines files and network.

    def env_names(self) -> list[str]:
        return ["CODEX_HOME"]

    def env_values(self, secrets: Secrets) -> dict[str, str]:
        return {"CODEX_HOME": "/codex"}

    def mounts(self, run_dir: Path) -> list[tuple[Path, str]]:
        return [(run_dir / "codex", "/codex")]

    def prepare(self, run_dir: Path) -> None:
        """A private copy of the sign-in, for this run only."""
        home = run_dir / "codex"
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copy2(self.home / "auth.json", home / "auth.json")
        (home / "auth.json").chmod(0o600)
        (run_dir / "codex.sum").write_text(_sha(self.home / "auth.json"))

    def finish(self, run_dir: Path) -> None:
        """If Codex refreshed its sign-in during the run, keep the refreshed one (only if the user's
        own copy hasn't changed meanwhile), then delete the run's copy."""
        copy, original = run_dir / "codex" / "auth.json", self.home / "auth.json"
        try:
            if copy.is_file() and _sha(copy) != (run_dir / "codex.sum").read_text() \
                    and _sha(original) == (run_dir / "codex.sum").read_text():  # fmt: skip
                tmp = original.with_suffix(".aura-tmp")
                shutil.copy2(copy, tmp)
                tmp.chmod(0o600)
                os.replace(tmp, original)
        finally:
            shutil.rmtree(run_dir / "codex", ignore_errors=True)
            (run_dir / "codex.sum").unlink(missing_ok=True)

    def parse(self, line: str) -> list[EngineEvent]:
        try:
            e = json.loads(line)
        except ValueError:
            return []
        kind, item = e.get("type"), e.get("item") or {}
        if kind == "item.completed":
            t = item.get("type")
            if t == "command_execution":
                return [EngineEvent("status", f"Ran: {str(item.get('command', ''))[:120]} (exit {item.get('exit_code')})")]
            if t == "file_change":
                return [EngineEvent("status", f"{c.get('kind', 'changed').capitalize()} {_short(str(c.get('path', '')))}")
                        for c in item.get("changes", [])]  # fmt: skip
            if t == "agent_message" and item.get("text", "").strip():
                return [EngineEvent("message", item["text"].strip())]
        if kind == "turn.completed":
            return [EngineEvent("result", "done", ok=True)]
        if kind == "turn.failed":
            return [EngineEvent("result", str((e.get("error") or {}).get("message", "failed"))[:500], ok=False)]
        return []


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


ENGINES: dict[str, Engine] = {"claude": ClaudeCode(), "codex": Codex()}
