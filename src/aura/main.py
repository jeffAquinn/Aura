"""Terminal interface for Aura. Run with: uv run aura"""

from __future__ import annotations

import readline  # noqa: F401  (enables arrow-key editing and history in input())
import sys
from collections.abc import Iterator

import openai

from aura.brain import ApprovalNeeded, Aura, Event, Status, Text
from aura.tools import vault_map

_TTY = sys.stdout.isatty()


def _dim(s: str) -> str:
    return f"\033[2m{s}\033[0m" if _TTY else s


def _render(events: Iterator[Event]) -> ApprovalNeeded | None:
    """Print a run's events; return the approval request it paused on, if any."""
    replying = False
    try:
        for ev in events:
            if isinstance(ev, Status):
                if replying:
                    print()
                    replying = False
                print(_dim(f"  {ev.text}"))
            elif isinstance(ev, Text):
                if not replying:
                    print("aura › ", end="")
                    replying = True
                print(ev.text, end="", flush=True)
            elif isinstance(ev, ApprovalNeeded):
                if replying:
                    print()
                return ev
    finally:
        if replying:
            print()
    return None


def _ask(request: ApprovalNeeded) -> list[bool]:
    decisions = []
    for action in request.actions:
        first, *rest = action.description.splitlines()
        print(f"  ⚠ Aura wants to {first}")
        for line in rest:
            print(_dim(line))
        try:
            answer = input("    Allow? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            answer = ""
        decisions.append(answer in ("y", "yes"))
    return decisions


def main() -> None:
    try:
        aura = Aura()
        vault_map.seed(aura.toolbox.vault)  # the Codex of Emergence, laid out once
    except OSError as e:
        sys.exit(f"Aura couldn't start: {e}")
    s, tb = aura.settings, aura.toolbox
    print(f"Aura is awake  ·  {s.model} @ {s.base_url}")
    print(_dim(f"vault: {tb.vault.root}  ·  search: {tb.search.mode}  ·  audit log: {tb.audit.path}"))
    print("/new for a fresh conversation, /exit or Ctrl-D to leave.")

    while True:
        try:
            text = input("\nyou › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/exit", "/quit"):
            break
        if text == "/new":
            aura.new_conversation()
            print("(fresh conversation)")
            continue

        try:
            pending = _render(aura.stream(text))
            while pending:
                pending = _render(aura.resume(_ask(pending)))
        except KeyboardInterrupt:
            print(" [interrupted]")
        except openai.APIConnectionError:
            print(f"[can't reach {s.base_url}. Is Ollama running?]")
        except openai.APIError as e:
            print(f"[model error: {e}]")


if __name__ == "__main__":
    main()
