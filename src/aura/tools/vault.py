"""File_System_Tool: Aura's hands inside the vault, and nowhere else.

Every path the model supplies goes through `Vault.resolve`, which collapses `..`, follows
symlinks, and refuses anything that does not land inside the vault root.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from aura.audit import AuditLog

READ_LIMIT = 12_000  # chars returned by read_file (~3k tokens of a 16k window)
LIST_LIMIT = 200  # entries returned by list_vault
PREVIEW_LINES = 3  # lines of new content shown in a write approval prompt


class VaultError(Exception):
    """A path the vault refuses to touch."""


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    raise AssertionError  # unreachable


class Vault:
    def __init__(self, root: Path, audit: AuditLog):
        self.root = root.expanduser().resolve()
        self.audit = audit

    def ensure_exists(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    # ── the whitelist ──────────────────────────────────────────────────────────────────────

    def _candidate(self, path: str) -> Path:
        raw = (path or "").strip() or "."
        if raw.startswith("~"):
            raise VaultError(
                f"'{path}' uses '~'. Vault paths are relative to the vault root, e.g. 'notes/todo.md'."
            )
        if "\x00" in raw:
            raise VaultError("Path contains a null byte.")
        p = Path(raw)
        return p if p.is_absolute() else self.root / p

    def _check_inside(self, resolved: Path, path: str) -> Path:
        if not resolved.is_relative_to(self.root):
            raise VaultError(f"'{path}' is outside the vault. Aura can only use {self.root}.")
        return resolved

    def resolve(self, path: str) -> Path:
        """Vault-relative (or absolute-inside-vault) path -> real path, or VaultError."""
        try:
            resolved = self._candidate(path).resolve()  # collapses '..', follows symlinks
        except (OSError, RuntimeError) as e:  # e.g. symlink loops
            raise VaultError(f"Can't resolve '{path}': {e}") from e
        return self._check_inside(resolved, path)

    def rel(self, p: Path) -> str:
        r = p.relative_to(self.root).as_posix()
        return "." if r == "." else r + ("/" if p.is_dir() else "")

    # ── operations ─────────────────────────────────────────────────────────────────────────

    def list(self, path: str = ".") -> str:
        d = self.resolve(path)
        if not d.is_dir():
            return f"'{path}' is not a folder." if d.exists() else f"'{path}' does not exist."
        entries = sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        if not entries:
            return f"'{self.rel(d)}' is empty."
        lines = []
        for p in entries[:LIST_LIMIT]:
            if p.is_dir():
                lines.append(f"  {p.name}/")
            else:
                lines.append(f"  {p.name}  ({human_size(p.stat().st_size)})")
        more = f"\n  ... and {len(entries) - LIST_LIMIT} more" if len(entries) > LIST_LIMIT else ""
        return f"Contents of '{self.rel(d)}' ({len(entries)} items):\n" + "\n".join(lines) + more

    def read(self, path: str) -> str:
        f = self.resolve(path)
        if not f.is_file():
            return f"'{path}' is a folder; use list_vault." if f.is_dir() else f"'{path}' does not exist."
        data = f.read_bytes()
        if b"\x00" in data[:8192]:
            return f"'{self.rel(f)}' is a binary file ({human_size(len(data))}); it can't be shown as text."
        text = data.decode("utf-8", errors="replace")
        if len(text) > READ_LIMIT:
            return text[:READ_LIMIT] + f"\n\n[truncated: showing the first {READ_LIMIT} of {len(text)} characters]"
        return text

    def write(self, path: str, content: str, overwrite: bool = False) -> str:
        f = self.resolve(path)
        if f == self.root or f.is_dir():
            return f"'{path}' is a folder; give a file path."
        existed = f.exists()
        if existed and not overwrite:
            return f"'{self.rel(f)}' already exists. To replace it, call write_file with overwrite=true."
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("w" if overwrite else "x", encoding="utf-8") as fh:  # "x": never clobber
            fh.write(content)
        size = len(content.encode("utf-8"))
        self.audit.record("file_written", path=self.rel(f), bytes=size, overwrote=existed)
        return f"{'Replaced' if existed else 'Created'} '{self.rel(f)}' ({human_size(size)})."

    def make_folder(self, path: str) -> str:
        d = self.resolve(path)
        if d.exists():
            return f"'{self.rel(d)}' already exists."
        d.mkdir(parents=True)
        self.audit.record("folder_created", path=self.rel(d))
        return f"Created folder '{self.rel(d)}'."

    def delete(self, path: str) -> str:
        candidate = self._candidate(path)
        if candidate.is_symlink() and candidate.name != "..":
            # Remove the link itself, never what it points to (which may be outside the vault).
            link = self._check_inside(candidate.parent.resolve() / candidate.name, path)
            link.unlink()
            self.audit.record("deleted", path=link.relative_to(self.root).as_posix(), kind="symlink")
            return f"Deleted link '{link.relative_to(self.root).as_posix()}'."
        target = self.resolve(path)
        if target == self.root:
            raise VaultError("Refusing to delete the vault itself.")
        if not target.exists():
            return f"'{path}' does not exist."
        rel = self.rel(target)
        if target.is_dir():
            if any(target.iterdir()):
                return f"'{rel}' is not empty. Aura only deletes empty folders; delete its contents first."
            target.rmdir()
            kind = "folder"
        else:
            target.unlink()
            kind = "file"
        self.audit.record("deleted", path=rel, kind=kind)
        return f"Deleted {kind} '{rel}'."

    # ── approval prompt text ───────────────────────────────────────────────────────────────

    def describe_change(self, tool_name: str, args: dict) -> str:
        """One honest line (plus preview) of what an approved call would do."""
        path = str(args.get("path", ""))
        try:
            target = self.resolve(path)
        except VaultError as e:
            return f"{tool_name.replace('_', ' ')} '{path}', which will be blocked: {e}"
        shown = target.relative_to(self.root).as_posix()
        if tool_name == "create_folder":
            return f"create folder '{shown}/'"
        if tool_name == "delete_path":
            if not target.exists():
                return f"delete '{shown}' (it doesn't exist)"
            if target.is_dir():
                return f"delete folder '{shown}/' (only if empty)"
            return f"delete file '{shown}' ({human_size(target.stat().st_size)})"
        if tool_name == "write_file":
            content = str(args.get("content", ""))
            size = human_size(len(content.encode("utf-8")))
            if target.is_file():
                head = f"overwrite '{shown}' ({human_size(target.stat().st_size)} → {size})"
                if not args.get("overwrite"):
                    head += ", which will be refused: overwrite was not requested"
            else:
                head = f"create file '{shown}' ({size})"
            lines = content.splitlines()
            preview = [f"    │ {line[:100]}" for line in lines[:PREVIEW_LINES]]
            if len(lines) > PREVIEW_LINES:
                preview.append(f"    │ ... ({len(lines) - PREVIEW_LINES} more lines)")
            return "\n".join([head + ":", *preview]) if preview else head
        return f"{tool_name} {args}"


def _guard(vault: Vault, tool_name: str, path: str, op: Callable[[], str]) -> str:
    """Run a vault operation, turning refusals and OS errors into messages for the model."""
    try:
        return op()
    except VaultError as e:
        vault.audit.record("path_blocked", tool=tool_name, path=path, reason=str(e))
        return f"Blocked: {e}"
    except OSError as e:
        return f"Error: {e.strerror or e} ('{path}')."


def vault_tools(vault: Vault) -> list[BaseTool]:
    @tool
    def list_vault(path: str = ".") -> str:
        """List the files and folders at `path` in the user's Aura Vault (default: the vault root).
        Paths are relative to the vault root."""
        return _guard(vault, "list_vault", path, lambda: vault.list(path))

    @tool
    def read_file(path: str) -> str:
        """Read a text file from the Aura Vault. `path` is relative to the vault root."""
        return _guard(vault, "read_file", path, lambda: vault.read(path))

    @tool
    def write_file(path: str, content: str, overwrite: bool = False) -> str:
        """Create a text file in the Aura Vault; missing parent folders are created. Set
        overwrite=true only to replace an existing file. The user approves this before it runs."""
        return _guard(vault, "write_file", path, lambda: vault.write(path, content, overwrite))

    @tool
    def create_folder(path: str) -> str:
        """Create a folder in the Aura Vault. The user approves this before it runs."""
        return _guard(vault, "create_folder", path, lambda: vault.make_folder(path))

    @tool
    def delete_path(path: str) -> str:
        """Delete a file or an empty folder from the Aura Vault (never recursive). The user
        approves this before it runs."""
        return _guard(vault, "delete_path", path, lambda: vault.delete(path))

    return [list_vault, read_file, write_file, create_folder, delete_path]
