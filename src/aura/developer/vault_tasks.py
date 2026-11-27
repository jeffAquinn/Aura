"""Vault tasks: Claude Code or Codex does a job in the user's vault, asked for from the chat.

1. Aura asks (ask_developer). Its approval card shows the task, the Developer, and exactly what
   leaves this Mac: the task and a copy of one vault folder (how many files, how large).
2. That copy, and nothing else, goes into a scratch workspace. The Developer works on it in the
   Developer container (container.py): it sees no other file on this Mac, and reaches only its
   own vendor.
3. Every file it created, changed or deleted is shown with its diff, each with its own Allow /
   Deny (default Deny). Only allowed changes reach the vault, and only where the vault's file is
   still exactly as it was copied: anything changed meanwhile is left alone.

The vault is the only place a task can change: never Aura's own code (the Integrity Rule).
"""

from __future__ import annotations

import difflib
import hashlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from aura.tools.vault import BUILD_OUTPUT, Vault, VaultError, human_size

MAX_FILES = 2_000  # files copied into a task's workspace
MAX_BYTES = 300 * 1024 * 1024  # ... and their total size (the user's choice, 2026-11-27)
MAX_CHANGES = 40  # changes one task may bring back for approval
MAX_FILE_BYTES = 10 * 1024 * 1024  # a single file it may write
DIFF_LINES = 60  # diff lines shown per change
SKIP = {".DS_Store"}  # Finder's files: never copied, never compared
# Build output and caches (tools/vault.BUILD_OUTPUT) are never copied, compared or written back, so
# they can't fill a task's limits or clutter the vault (a stale app/.build once made the Aura sandbox
# 397 MB).
GENERATED = BUILD_OUTPUT
# A task aimed at the whole Vault is for the user's content, not Aura's runtime machinery. A user
# can still explicitly choose `Aura/` when they want a Developer to work on the project sandbox.
SYSTEM_ROOTS = {"Aura", "Audit_Staging", "Audit_Temp", "Skills Inbox"}
TASK_LIMITS = (10, 8_000)  # characters


class VaultTaskError(ValueError):
    pass


@dataclass
class Change:
    path: str  # vault-relative, e.g. "The_Absolute_R/forms.md"
    kind: str  # "create" | "modify" | "delete" | "create_folder" | "delete_folder"
    source: str = ""  # workspace-relative file holding the new content
    before: str | None = None  # the vault file's sha256 when it was copied (None: it didn't exist)
    added: int = 0
    removed: int = 0
    size: int = 0
    binary: bool = False
    diff: list[str] = field(default_factory=list)

    def headline(self) -> str:
        what = {"create": "create", "modify": "change", "delete": "delete", "create_folder": "create folder",
                "delete_folder": "delete folder"}[self.kind]  # fmt: skip
        shown = self.path + ("/" if self.kind.endswith("folder") else "")
        if self.kind.endswith("folder"):
            detail = " (only if empty)" if self.kind == "delete_folder" else ""
        elif self.binary:
            detail = f" (binary, {human_size(self.size)})"
        elif self.kind == "delete":
            detail = f" ({human_size(self.size)}, −{self.removed} lines)"
        elif self.kind == "create":
            detail = f" ({human_size(self.size)}, +{self.added} lines)"
        else:
            detail = f" (+{self.added} −{self.removed})"
        return f"{what} '{shown}'{detail}"

    def describe(self) -> str:
        """The approval card's text: the headline, then the diff (the card colors + and −)."""
        return "\n".join([self.headline() + (":" if self.diff else ""), *(f"    │ {line}" for line in self.diff)])


@dataclass
class Copy:
    folder: str  # vault-relative ("." for the whole vault)
    files: dict[str, str]  # workspace-relative path -> sha256 at copy time
    folders: set[str]
    bytes: int


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _walk(root: Path) -> tuple[list[Path], list[Path]]:
    """Regular files and folders under root. Links are skipped, never followed."""
    files, folders = [], []
    for dirpath, dirnames, filenames in os.walk(root):  # followlinks=False: a linked folder isn't entered
        base = Path(dirpath)
        for d in list(dirnames):
            if (base / d).is_symlink() or d in GENERATED or (base == root and d in SYSTEM_ROOTS):
                dirnames.remove(d)
            else:
                folders.append(base / d)
        files += [base / f for f in filenames if f not in SKIP and not (base / f).is_symlink() and (base / f).is_file()]
    return sorted(files), sorted(folders)


def check_task(task: str) -> None:
    if not TASK_LIMITS[0] <= len(task.strip()) <= TASK_LIMITS[1]:
        raise VaultTaskError(f"the task must be {TASK_LIMITS[0]}-{TASK_LIMITS[1]} characters")


def source_folder(vault: Vault, folder: str) -> tuple[str, Path]:
    """The vault folder a task copies: (its vault-relative name, its real path)."""
    try:
        path = vault.resolve(folder or ".")
    except VaultError as e:
        raise VaultTaskError(str(e)) from e
    if not path.is_dir():
        raise VaultTaskError(f"'{folder}' is not a folder in the vault")
    rel = path.relative_to(vault.root).as_posix()
    return ("." if rel in ("", ".") else rel), path


def measure(vault: Vault, folder: str) -> tuple[str, int, int]:
    """(folder, files, bytes) of what a task would copy, refused beyond the limits."""
    name, path = source_folder(vault, folder)
    files, _ = _walk(path)
    size = sum(f.stat().st_size for f in files)
    if len(files) > MAX_FILES or size > MAX_BYTES:
        raise VaultTaskError(f"'{name}' is too large to hand over ({len(files)} files, {human_size(size)}; the limit is "
                             f"{MAX_FILES} files and {human_size(MAX_BYTES)}): choose a smaller folder")  # fmt: skip
    return name, len(files), size


def prepare(vault: Vault, folder: str, work: Path) -> Copy:
    """Copy the folder into a fresh workspace; remember exactly what was copied."""
    name, path = source_folder(vault, folder)
    measure(vault, folder)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    files, folders = _walk(path)
    copy = Copy(name, {}, set(), 0)
    for d in folders:
        rel = d.relative_to(path).as_posix()
        (work / rel).mkdir(parents=True, exist_ok=True)
        copy.folders.add(rel)
    for f in files:
        rel = f.relative_to(path).as_posix()
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, work / rel)
        copy.files[rel] = _sha(work / rel)
        copy.bytes += f.stat().st_size
    return copy


def baseline_drift(vault: Vault, copy: Copy) -> list[str]:
    """Bounded, synchronous differences between a session's copy and its Vault source.

    This is deliberately a just-in-time check, not a watcher.  HOL already bounds a copied
    project at session start, so checking that same bounded baseline before another connected
    turn prevents an external edit from silently becoming part of the agent's mental model.
    """
    try:
        _, origin = source_folder(vault, copy.folder)
    except VaultTaskError as error:
        return [str(error)]
    files, folders = _walk(origin)
    current = {path.relative_to(origin).as_posix(): path for path in files}
    current_folders = {path.relative_to(origin).as_posix() for path in folders}
    changed = [rel for rel in sorted(set(copy.files) & set(current)) if _sha(current[rel]) != copy.files[rel]]
    missing = sorted(set(copy.files) - set(current))
    added = sorted(set(current) - set(copy.files))
    missing_folders = sorted(copy.folders - current_folders)
    added_folders = sorted(current_folders - copy.folders)
    findings = ([f"changed file: {rel}" for rel in changed]
                + [f"missing file: {rel}" for rel in missing]
                + [f"new file: {rel}" for rel in added]
                + [f"missing folder: {rel}" for rel in missing_folders]
                + [f"new folder: {rel}" for rel in added_folders])
    return findings[:8]


def prompt(task: str, folder: str = ".") -> str:
    """The Developer's brief. `folder`: the vault folder copied as the current directory, so a
    vault path in the task ("The_Interface_Many/models/") lands where it belongs instead of one
    level too deep (seen 2026-11-27: The_Interface_Many/The_Interface_Many/models)."""
    where = ("It is the current directory, and it is the vault's top folder"
             if folder in ("", ".") else
             f"It is the current directory, and it is the vault folder '{folder}'. A path in the task that starts "
             f"with '{folder}/' means the same place here without that prefix: for '{folder}/notes/a.md', write "
             "'notes/a.md'. Never create a folder of that name inside this one")
    return (
        "You are working for Aura, the user's personal AI agent, on a copy of a folder from the user's notes "
        f"vault. {where}; nothing else on the user's computer is available to you.\n\n"
        "The task, written by Aura from the user's request:\n"
        f"{task.strip()}\n\n"
        "Put a temporary file (a plan, notes, a scratch script) in this folder's `_scratch/` folder: Aura "
        "clears it a week after it was last changed. Make the changes directly in the files here. Afterwards the user sees every file you created, changed "
        "or deleted, with its diff, and allows or denies each one, so change only what the task needs. Don't "
        "use the network.\n\n"
        "If AURA_ENGINEERING.md is present, this is an Aura project sandbox: read it before changing code and "
        "follow its strategic workflow. In particular, use the glossary and relevant ADRs, avoid inventing "
        "product decisions, verify behavior with the repository's checks, and record evidence and risks for "
        "non-trivial changes. This workflow does not authorize any access outside this workspace.\n\n"
        "When you're done, reply with a short summary of what you changed, the checks you ran, and any remaining "
        "risks or unanswered questions."
    )


def _text(path: Path) -> list[str] | None:
    data = path.read_bytes()
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None


def _trim(lines: list[str]) -> list[str]:
    lines = [line[:200] for line in lines]
    return lines if len(lines) <= DIFF_LINES else [*lines[:DIFF_LINES], f"… ({len(lines) - DIFF_LINES} more lines)"]


def _vault_path(copy: Copy, rel: str) -> str:
    return rel if copy.folder == "." else str(PurePosixPath(copy.folder) / rel)


def collect(vault: Vault, copy: Copy, work: Path) -> list[Change]:
    """What the Developer changed in the workspace, compared with what was copied."""
    _, origin = source_folder(vault, copy.folder)
    files, folders = _walk(work)
    now = {f.relative_to(work).as_posix(): f for f in files}
    changes: list[Change] = []
    for rel, f in now.items():
        old_sha = copy.files.get(rel)
        if old_sha is not None and _sha(f) == old_sha:
            continue
        new = _text(f)
        old = _text(origin / rel) if old_sha is not None and (origin / rel).is_file() else []
        c = Change(_vault_path(copy, rel), "modify" if old_sha else "create", source=rel, before=old_sha,
                   size=f.stat().st_size)  # fmt: skip
        if new is None or old is None:
            c.binary = True
        else:
            body = list(difflib.unified_diff(old, new, lineterm="", n=2))[2:] if old_sha else [f"+{x}" for x in new]
            c.added = sum(1 for x in body if x.startswith("+"))
            c.removed = sum(1 for x in body if x.startswith("-"))
            c.diff = _trim(body)
        changes.append(c)
    for rel, sha in copy.files.items():
        if rel not in now:
            old = _text(origin / rel) if (origin / rel).is_file() else None
            c = Change(_vault_path(copy, rel), "delete", before=sha,
                       size=(origin / rel).stat().st_size if (origin / rel).is_file() else 0)  # fmt: skip
            c.binary = old is None
            c.removed = len(old or [])
            changes.append(c)
    have = {d.relative_to(work).as_posix() for d in folders}
    for rel in sorted(have - copy.folders):
        if not any(k.startswith(rel + "/") for k in now):  # a new folder with files appears with them
            changes.append(Change(_vault_path(copy, rel), "create_folder"))
    for rel in sorted(copy.folders - have, key=lambda r: -r.count("/")):  # deepest first
        changes.append(Change(_vault_path(copy, rel), "delete_folder"))
    return changes


ORDER = {"modify": 0, "create": 0, "create_folder": 1, "delete": 2, "delete_folder": 3}


def apply(vault: Vault, copy: Copy, work: Path, changes: list[Change], allowed: list[bool]) -> list[str]:
    """Write the allowed changes to the vault. Returns one line per change, for the model."""
    lines: list[str] = []
    for change, ok in sorted(zip(changes, allowed), key=lambda pair: ORDER[pair[0].kind]):
        if not ok:
            lines.append(f"Denied (left as it was): {change.headline()}")
            vault.audit.record("vault_change_denied", path=change.path, kind=change.kind)
            continue
        try:
            done = _apply_one(vault, work, change)
        except (VaultError, OSError) as e:
            done = f"Not applied: {change.headline()}: {e}"
        lines.append(done)
        if not done.startswith("Not applied"):
            vault.audit.record("vault_change_applied", path=change.path, kind=change.kind)
    return lines


def _apply_one(vault: Vault, work: Path, c: Change) -> str:
    target = vault.writable(c.path)  # generated files (Capabilities.md) are never changed
    if target == vault.root:
        raise VaultError("that is the vault itself")
    if c.kind in ("create", "modify"):
        src = work / c.source
        if src.is_symlink() or not src.is_file():
            return f"Not applied: {c.headline()}: the new file is missing"
        if src.stat().st_size > MAX_FILE_BYTES:
            return f"Not applied: {c.headline()}: larger than {human_size(MAX_FILE_BYTES)}"
        current = _sha(target) if target.is_file() else None
        if target.exists() and not target.is_file():
            return f"Not applied: {c.headline()}: a folder is in the way"
        if current != c.before:
            return f"Not applied: {c.headline()}: the vault's copy changed after the task started"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".aura-new")
        shutil.copyfile(src, tmp)
        tmp.replace(target)
        if not target.is_file() or _sha(target) != _sha(src):
            # Do not try to restore a value after a concurrent writer won the race: that would
            # overwrite an external change.  The caller refreshes its workspace before another turn.
            return f"Not applied: {c.headline()}: the vault's copy did not match the approved content after writing"
        return ("Created" if c.kind == "create" else "Updated") + f" '{c.path}'"
    if c.kind == "delete":
        if not target.is_file():
            return f"Not applied: {c.headline()}: it's already gone"
        if _sha(target) != c.before:
            return f"Not applied: {c.headline()}: the vault's copy changed after the task started"
        target.unlink()
        return f"Deleted '{c.path}'"
    if c.kind == "create_folder":
        target.mkdir(parents=True, exist_ok=True)
        return f"Created folder '{c.path}/'"
    if not target.is_dir():
        return f"Not applied: {c.headline()}: it's already gone"
    if any(target.iterdir()):
        return f"Not applied: {c.headline()}: it isn't empty"
    target.rmdir()
    return f"Deleted folder '{c.path}/'"
