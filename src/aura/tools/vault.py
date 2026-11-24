"""File_System_Tool: Aura's hands inside the vault, and nowhere else.

Every path the model supplies goes through `Vault.resolve`, which collapses `..`, follows
symlinks, and refuses anything that does not land inside the vault root.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import tempfile

from langchain_core.tools import BaseTool, tool

from aura.audit import AuditLog

READ_LIMIT = 12_000  # chars returned by read_file (~3k tokens of a 16k window)
LIST_LIMIT = 200  # entries returned by list_vault
PREVIEW_LINES = 3  # lines of new content shown in a write approval prompt


class VaultError(Exception):
    """A path the vault refuses to touch."""


# These are repository implementation details, rather than application files.  In particular,
# copying .git would leave an editable Git configuration (and its remotes) in the user's vault.
# The sandbox is deliberately a project *copy*, not another checkout.
PROJECT_COPY_SKIP = {".git", ".aura-staging"}

# These files give the coding engines a compact, persistent strategic memory without copying a
# third-party agent plugin into the vault.  They deliberately mirror only the parts of the
# engineering-skills workflow that work inside Aura's local, approval-gated sandbox.
ENGINEERING_FILES = {
    "AURA_ENGINEERING.md": """# Aura Master Programmer Workflow

This is an isolated project sandbox. Changes made here never modify the live Aura checkout or
GitHub. There is no Git history, remote, deployment, or issue tracker in this copy.

## The operating loop

1. **Orient.** Before changing code, read this file, `GLOSSARY.md`, the relevant ADRs in
   `docs/adr/`, and the nearby tests and code. Reuse the project's established terms.
2. **Align.** Do not invent product decisions. State focused unanswered questions or reasonable,
   reversible assumptions before implementation. Keep the task's scope explicit.
3. **Design.** For one small, clear vertical slice, work directly. For a multi-module, risky, or
   open-ended change, create or update a spec in `docs/specs/` before implementation. Prefer a
   small public interface with substantial behavior behind it; avoid speculative abstractions.
4. **Build.** Work one vertical slice at a time. Add behavior-level tests at agreed public seams;
   do not couple tests to private implementation details. Make the smallest change that satisfies
   the current slice, then refactor only when the behavior is protected.
5. **Verify.** Run the repository's relevant formatter, lint/type checks, and tests. Treat a
   check that was not run as unverified, never as passing. Capture concrete evidence in the review.
6. **Review.** Compare the result separately against the spec and `CODING_STANDARDS.md`. Record
   evidence, remaining risks, whether this is a one-way or two-way door, and its blast radius in
   `docs/reviews/` when the change is more than trivial.
7. **Learn.** If a mistake could be prevented next time, propose the cheapest durable guardrail:
   a test, linter/type rule, navigation pointer, or a concise standards rule. Put hard-to-reverse,
   surprising trade-offs in an ADR; do not create ADRs for routine implementation details.

## Working agreement

- Preserve the sandbox boundary. Never try to reach the source checkout, GitHub, or the network.
- Prefer existing patterns and test seams over new frameworks or broad rewrites.
- Keep `GLOSSARY.md` about domain language, not implementation details.
- Keep ADRs short: context, decision, alternatives, consequences, and date.
- Keep work reviewable. A large change should be divided into independently verifiable slices.
""",
    "GLOSSARY.md": """# Aura Sandbox Glossary

Use these names consistently in user-facing text, code, tests, specs, and reviews. Add a term only
when its meaning has been resolved; do not turn this file into a design notebook.

| Term | Meaning | Avoid |
| --- | --- | --- |
| Aura project sandbox | The editable project copy under `Aura_Vault/Aura/`. | live checkout, repository |
| Source checkout | The original Aura project folder used only to seed the sandbox. | sandbox |
| Vault | The user's `Aura_Vault` folder, which contains the project sandbox and their own files. | repository |
| Approval | The user's explicit allow/deny decision before Aura writes a proposed change. | automatic merge |
| Developer | The isolated Claude Code or Codex run that edits a temporary copy for review. | direct filesystem access |
| Skill | An audited, sandboxed Aura capability; it is not an arbitrary coding-agent plugin. | script |
| Specification | A durable statement of problem, scope, decisions, test seams, and acceptance evidence. | implementation notes |
""",
    "CODING_STANDARDS.md": """# Coding Standards

These are review criteria, not a substitute for automated checks. A deterministic rule belongs in
a formatter, linter, type checker, test, or CI command when the project supports one.

## Change design

- Name concepts with `GLOSSARY.md` vocabulary and keep related behavior together.
- Prefer a small, stable public interface over exposing implementation details.
- Do not add abstraction, configuration, or extensibility without a present requirement.
- Keep a logical change local. If it requires scattered edits, consider a deeper module or a
  narrow adapter at the boundary.

## Tests and evidence

- Test observable behavior through public seams, not private helpers or incidental call order.
- Use an independent expected result from the specification or a known-good example.
- Add regression coverage for fixed defects when a practical seam exists.
- Report the exact checks run and their result; unrun checks are risks, not passes.

## Safety

- Do not weaken validation, approval, path containment, masking, or sandboxing to make a task pass.
- Do not add network access, credentials, destructive migration behavior, or external side effects
  without an explicit user decision and a documented risk.
""",
    "docs/adr/README.md": """# Architecture Decision Records

Create an ADR only for a decision that is hard to reverse, surprising without context, and chosen
after a real trade-off. Name files `NNNN-short-decision.md`.

```markdown
# NNNN: Decision title

Date: YYYY-MM-DD

## Context
## Decision
## Alternatives considered
## Consequences
```
""",
    "docs/specs/README.md": """# Specifications

Use a spec for work that is larger than one clear, independently verifiable slice. Keep it about
outcomes and durable decisions, not unstable file paths or code snippets.

```markdown
# Feature title

## Problem
## Scope
## Out of scope
## User-visible behavior and acceptance evidence
## Design decisions and affected module boundaries
## Test seams and verification plan
## Risks and reversibility
```
""",
    "docs/reviews/README.md": """# Change Reviews

For non-trivial work, record review results using the domain language and keep specification and
standards findings separate.

```markdown
# Review: change title

## Summary
## Evidence
- Checks run:
- Behavior observed:

## Specification review
## Standards review
## Merge danger
- Door: one-way | two-way
- Blast radius: low | medium | high
- Remaining risks:
```
""",
    "docs/retros/README.md": """# Engineering Retrospectives

Use a retrospective after a meaningful implementation or review finding. Improve the environment,
not just this one patch: navigation, automated checks, standards, or information access. Prefer a
deterministic guardrail over a prose rule whenever possible.
""",
}


@dataclass(frozen=True)
class ProjectCopy:
    """The result of seeding Aura's editable project sandbox."""

    path: Path
    copied: bool
    files: int = 0
    folders: int = 0


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

    @staticmethod
    def _seed_engineering_files(root: Path) -> list[str]:
        """Add missing strategic-workflow references without overwriting sandbox work."""
        created: list[str] = []
        root = root.resolve()
        for relative, content in ENGINEERING_FILES.items():
            target = root / relative
            if target.exists() or target.is_symlink():
                continue
            parent = target.parent
            try:
                if not parent.resolve().is_relative_to(root):
                    continue  # a user-created link must never turn a seed into an external write
                parent.mkdir(parents=True, exist_ok=True)
                if not parent.resolve().is_relative_to(root):
                    continue
                with target.open("x", encoding="utf-8") as fh:
                    fh.write(content)
            except FileExistsError:  # another Aura start seeded it first
                continue
            created.append(relative)
        return created

    def seed_project_copy(self, source: Path) -> ProjectCopy | None:
        """Copy Aura's source tree into this vault once, without ever updating it again.

        The source checkout is only read.  A temporary sibling is atomically renamed into place,
        so an interrupted seed cannot leave a partial ``Aura/`` sandbox that later looks valid.
        Git data and links are excluded: neither is application source, and either could make the
        isolated copy reach back out to the user's checkout or its remote configuration.
        """
        source = source.expanduser().resolve()
        if not source.is_dir():
            return None
        self.ensure_exists()
        target = self.root / source.name
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            return ProjectCopy(target, copied=False)
        if target.exists():
            if created := self._seed_engineering_files(target):
                self.audit.record("project_sandbox_workflow_seeded", path=target.relative_to(self.root).as_posix(),
                                  created=created)  # fmt: skip
            return ProjectCopy(target, copied=False)
        if source.is_relative_to(self.root):
            raise VaultError("The Aura project source cannot be inside its own vault.")

        staging = Path(tempfile.mkdtemp(prefix=f".{source.name}.seed-", dir=self.root))
        files, folders = 0, 0
        try:
            for directory, names, filenames in os.walk(source, followlinks=False):
                current = Path(directory)
                relative = current.relative_to(source)
                destination = staging / relative
                destination.mkdir(exist_ok=True)
                for name in list(names):
                    entry = current / name
                    if name in PROJECT_COPY_SKIP or entry.is_symlink():
                        names.remove(name)
                        continue
                    folders += 1
                for name in filenames:
                    entry = current / name
                    if name in PROJECT_COPY_SKIP or entry.is_symlink() or not entry.is_file():
                        continue
                    shutil.copy2(entry, destination / name)
                    files += 1
            staging.replace(target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        workflow = self._seed_engineering_files(target)
        self.audit.record(
            "project_sandbox_seeded", source=str(source), path=target.relative_to(self.root).as_posix(),
            files=files, folders=folders, skipped=sorted(PROJECT_COPY_SKIP), workflow=workflow,
        )
        return ProjectCopy(target, copied=True, files=files, folders=folders)

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
