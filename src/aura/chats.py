"""Saved chats: every Mac conversation, kept as a readable Markdown file in the vault's Chats folder.

A chat is saved after each turn, and before Aura starts a new one or opens an old one, so New
Chat no longer forgets the last conversation: it stays in Chats/ until the user deletes the file.
Opening a chat makes it the current conversation again (its messages are seeded back into Aura's
memory), and later turns keep updating the same file.

The file is a transcript a person can read: front matter, a title, then "You" and "Aura" in
turn. Each message starts with an HTML comment marker (invisible in Markdown viewers) so Aura can
load it back exactly. Text that looks like a secret is masked before it is written.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FOLDER = "Chats"
MARK = "<!-- aura:{} -->"
_MARK = re.compile(r"^<!-- aura:(you|aura) -->$", re.M)
_LABEL = {"user": ("you", "**You**"), "aura": ("aura", "**Aura**")}
_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
TITLE_CHARS = 60


def title_of(messages: list[dict[str, str]]) -> str:
    """A chat's title: its first message's first line, shortened (as Claude names a new chat)."""
    first = next((m["text"] for m in messages if m["role"] == "user" and m["text"].strip()), "New chat")
    line = " ".join(first.strip().splitlines()[0].split())
    return line if len(line) <= TITLE_CHARS else line[: TITLE_CHARS - 1].rstrip() + "…"


def _slug(title: str) -> str:
    return re.sub(r"[^\w\- ]+", "", title).strip()[:40].rstrip() or "Chat"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _front(text: str) -> dict[str, str]:
    match = _FRONT.match(text)
    if not match:
        return {}
    pairs = (line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
    return {k.strip(): v.strip() for k, v in pairs}


class ChatArchive:
    def __init__(self, vault_root: Path, mask: Any = None):
        self.root = vault_root / FOLDER
        self.mask = mask or (lambda text: text)

    def _files(self) -> list[Path]:
        return sorted(self.root.glob("*.md")) if self.root.is_dir() else []

    def _find(self, chat_id: str) -> Path | None:
        for path in self._files():
            if _front(path.read_text(encoding="utf-8", errors="replace")[:2000]).get("aura-chat") == chat_id:
                return path
        return None

    def save(self, chat_id: str, messages: list[dict[str, str]]) -> Path | None:
        """Write (or rewrite) the chat's file. A chat with nothing from the user isn't saved."""
        messages = [m for m in messages if m.get("role") in _LABEL and m.get("text", "").strip()]
        if not any(m["role"] == "user" for m in messages):
            return None
        path = self._find(chat_id)
        created = _front(path.read_text(encoding="utf-8")).get("created") if path else None
        title = title_of(messages)
        if path is None:
            self.root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d %H%M")
            path = self.root / f"{stamp} {_slug(title)}.md"
            n = 2
            while path.exists():
                path = self.root / f"{stamp} {_slug(title)} {n}.md"
                n += 1
        body = [f"---\naura-chat: {chat_id}\ntitle: {title}\ncreated: {created or _now()}\nupdated: {_now()}\n---\n",
                f"# {title}\n"]
        for m in messages:
            marker, label = _LABEL[m["role"]]
            text = self.mask(m["text"]).replace("<!-- aura:", "<!-- aura :")  # a message can't fake a marker
            body.append(f"{MARK.format(marker)}\n{label}\n\n{text.strip()}\n")
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(body), encoding="utf-8")
        os.replace(tmp, path)
        return path

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        """The saved chats, most recently updated first."""
        chats = []
        for path in self._files():
            meta = _front(path.read_text(encoding="utf-8", errors="replace")[:2000])
            if meta.get("aura-chat"):
                chats.append({"id": meta["aura-chat"], "title": meta.get("title") or path.stem,
                              "updated": meta.get("updated", ""), "file": f"{FOLDER}/{path.name}"})
        return sorted(chats, key=lambda c: c["updated"], reverse=True)[:limit]

    def load(self, chat_id: str) -> list[dict[str, str]] | None:
        """A saved chat's messages, in order; None if there is no such chat."""
        path = self._find(chat_id)
        if path is None:
            return None
        text = _FRONT.sub("", path.read_text(encoding="utf-8"), count=1)
        parts = _MARK.split(text)  # [header, kind, body, kind, body, ...]
        messages = []
        for kind, body in zip(parts[1::2], parts[2::2]):
            body = body.strip()
            label = _LABEL["user" if kind == "you" else "aura"][1]
            if body.startswith(label):
                body = body[len(label):].strip()
            messages.append({"role": "user" if kind == "you" else "aura", "text": body})
        return messages
