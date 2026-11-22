"""Terminal interface for Aura (Sprint 1). Run with: uv run aura"""

from __future__ import annotations

import readline  # noqa: F401  (enables arrow-key editing and history in input())

import openai

from aura.brain import Aura


def main() -> None:
    aura = Aura()
    s = aura.settings
    print(f"Aura is awake  ·  {s.model} @ {s.base_url}")
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

        print("aura › ", end="", flush=True)
        try:
            for token in aura.stream(text):
                print(token, end="", flush=True)
        except KeyboardInterrupt:
            print(" [interrupted]", end="")
        except openai.APIConnectionError:
            print(f"[can't reach {s.base_url}. Is Ollama running?]", end="")
        except openai.APIError as e:
            print(f"[model error: {e}]", end="")
        print()


if __name__ == "__main__":
    main()
