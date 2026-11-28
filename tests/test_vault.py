"""Vault whitelist enforcement: the file tools cannot touch anything outside the vault."""

from __future__ import annotations

import os
import threading

import pytest

from aura.audit import AuditLog
from aura.tools.vault import READ_LIMIT, Vault, vault_tools


@pytest.fixture
def env(tmp_path):
    """A vault, plus a 'secret' file just outside it that must stay unreachable."""
    root = tmp_path / "vault"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("TOP SECRET")
    audit = AuditLog(tmp_path / "audit.jsonl")
    vault = Vault(root, audit)
    tools = {t.name: t for t in vault_tools(vault)}
    return vault, tools, root, outside, audit


def use(tools, name, **args) -> str:
    return tools[name].invoke(args)


def test_create_write_list_read_delete_round_trip(env):
    vault, tools, root, _, _ = env
    assert use(tools, "create_folder", path="Project Aura") == "Created folder 'Project Aura/'."
    assert use(tools, "write_file", path="Project Aura/plan.md", content="# Plan\n") == (
        "Created 'Project Aura/plan.md' (7 B)."
    )
    assert "plan.md  (7 B)" in use(tools, "list_vault", path="Project Aura")
    assert use(tools, "read_file", path="Project Aura/plan.md") == "# Plan\n"
    assert use(tools, "delete_path", path="Project Aura/plan.md") == "Deleted file 'Project Aura/plan.md'."
    assert use(tools, "delete_path", path="Project Aura") == "Deleted folder 'Project Aura/'."
    assert list(root.iterdir()) == []


ESCAPES = [
    "../secret.txt",
    "notes/../../secret.txt",
    "~/secret.txt",
    "/etc/hosts",
    "/private/etc/hosts",
    "bad\x00name",
]


@pytest.mark.parametrize("path", ESCAPES)
@pytest.mark.parametrize("tool", ["read_file", "list_vault", "write_file", "create_folder", "delete_path"])
def test_paths_outside_the_vault_are_blocked_for_every_tool(env, tool, path):
    vault, tools, root, outside, audit = env
    args = {"path": path, "content": "pwned"} if tool == "write_file" else {"path": path}

    result = use(tools, tool, **args)

    assert result.startswith("Blocked:"), result
    assert outside.read_text() == "TOP SECRET"
    assert audit.entries()[-1]["event"] == "path_blocked"


def test_absolute_path_to_the_real_outside_file_is_blocked(env):
    vault, tools, root, outside, _ = env
    assert use(tools, "read_file", path=str(outside)).startswith("Blocked:")
    assert use(tools, "delete_path", path=str(outside)).startswith("Blocked:")
    assert outside.exists()


def test_absolute_path_inside_the_vault_is_allowed(env):
    vault, tools, root, _, _ = env
    (root / "a.txt").write_text("hi")
    assert use(tools, "read_file", path=str(root / "a.txt")) == "hi"


def test_symlink_pointing_outside_cannot_be_followed(env):
    vault, tools, root, outside, _ = env
    (root / "link.txt").symlink_to(outside)
    (root / "linkdir").symlink_to(outside.parent)

    assert use(tools, "read_file", path="link.txt").startswith("Blocked:")
    assert use(tools, "write_file", path="link.txt", content="x", overwrite=True).startswith("Blocked:")
    assert use(tools, "list_vault", path="linkdir").startswith("Blocked:")
    assert use(tools, "write_file", path="linkdir/new.txt", content="x").startswith("Blocked:")
    assert use(tools, "create_folder", path="linkdir/sub").startswith("Blocked:")
    assert outside.read_text() == "TOP SECRET"
    assert not (outside.parent / "new.txt").exists()


def test_deleting_a_symlink_removes_only_the_link(env):
    vault, tools, root, outside, _ = env
    (root / "link.txt").symlink_to(outside)

    assert use(tools, "delete_path", path="link.txt") == "Deleted link 'link.txt'."
    assert not (root / "link.txt").is_symlink()
    assert outside.read_text() == "TOP SECRET"  # the target outside the vault is untouched


@pytest.mark.parametrize("path", [".", "", "notes/..", "VAULT"])
def test_the_vault_itself_cannot_be_deleted(env, path):
    vault, tools, root, _, _ = env
    (root / "notes").mkdir()
    path = str(root) if path == "VAULT" else path
    assert use(tools, "delete_path", path=path) == "Blocked: Refusing to delete the vault itself."
    assert root.is_dir()


def test_delete_is_never_recursive(env):
    vault, tools, root, _, _ = env
    (root / "full").mkdir()
    (root / "full" / "keep.txt").write_text("x")

    assert "not empty" in use(tools, "delete_path", path="full")
    assert (root / "full" / "keep.txt").exists()


def test_overwrite_requires_the_flag(env):
    vault, tools, root, _, _ = env
    (root / "a.txt").write_text("original")

    assert "already exists" in use(tools, "write_file", path="a.txt", content="new")
    assert (root / "a.txt").read_text() == "original"
    assert use(tools, "write_file", path="a.txt", content="new", overwrite=True).startswith("Replaced")
    assert (root / "a.txt").read_text() == "new"


def test_approved_overwrite_rejects_a_file_changed_after_its_card(env):
    vault, tools, root, _, _ = env
    target = root / "a.txt"
    target.write_text("approved version")
    args = {"path": "a.txt", "content": "Aura's replacement", "overwrite": True}

    vault.describe_change("write_file", args)
    target.write_text("external edit")

    result = use(tools, "write_file", **args)
    assert result.startswith("Blocked: The file changed after approval")
    assert target.read_text() == "external edit"


def test_two_approved_overwrites_allow_only_the_first_version(env):
    vault, tools, root, _, _ = env
    target = root / "a.txt"
    target.write_text("original")
    first = {"path": "a.txt", "content": "first", "overwrite": True}
    second = {"path": "a.txt", "content": "second", "overwrite": True}
    vault.describe_change("write_file", first)
    vault.describe_change("write_file", second)

    assert use(tools, "write_file", **first).startswith("Replaced")
    assert use(tools, "write_file", **second).startswith("Blocked: The file changed after approval")
    assert target.read_text() == "first"


def test_concurrent_approved_overwrites_allow_only_one_writer(env):
    vault, tools, root, _, _ = env
    target = root / "a.txt"
    target.write_text("original")
    first = {"path": "a.txt", "content": "first", "overwrite": True}
    second = {"path": "a.txt", "content": "second", "overwrite": True}
    vault.describe_change("write_file", first)
    vault.describe_change("write_file", second)
    barrier = threading.Barrier(3)
    results: list[str] = []

    def attempt(args):
        barrier.wait()
        results.append(use(tools, "write_file", **args))

    writers = [threading.Thread(target=attempt, args=(args,)) for args in (first, second)]
    for writer in writers:
        writer.start()
    barrier.wait()
    for writer in writers:
        writer.join()

    assert sum(result.startswith("Replaced") for result in results) == 1
    assert sum(result.startswith("Blocked: The file changed after approval") for result in results) == 1
    assert target.read_text() in {"first", "second"}


def test_failed_atomic_overwrite_keeps_original_and_cleans_temporary_file(env, monkeypatch):
    _, tools, root, _, _ = env
    target = root / "a.txt"
    target.write_text("original")

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    result = use(tools, "write_file", path="a.txt", content="replacement", overwrite=True)

    assert result.startswith("Error: simulated replace failure")
    assert target.read_text() == "original"
    assert list(root.glob(".a.txt.*.tmp")) == []


def test_read_truncates_long_files_and_refuses_binary(env):
    vault, tools, root, _, _ = env
    (root / "long.txt").write_text("x" * (READ_LIMIT + 50))
    (root / "img.bin").write_bytes(b"\x89PNG\x00\x00binary")

    long = use(tools, "read_file", path="long.txt")
    assert long.startswith("x" * READ_LIMIT) and "The rest: read_file(" in long
    assert "binary file" in use(tools, "read_file", path="img.bin")


def test_search_knowledge_returns_bounded_local_text_sources_only(env):
    vault, tools, root, outside, _ = env
    library = root / "Knowledge"
    library.mkdir()
    (library / "release.md").write_text("# Release\nAura uses an atomic write guard for safe overwrites.")
    (library / "ignore.csv").write_text("atomic,write,guard")
    (library / "outside.md").symlink_to(outside)

    result = use(tools, "search_knowledge", query="atomic write")

    assert "Knowledge/release.md" in result
    assert "[Source: Knowledge/release.md]" in result
    assert "safe overwrites" in result
    assert "TOP SECRET" not in result and "ignore.csv" not in result


def test_search_knowledge_reports_no_match_or_library(env):
    _, tools, root, _, _ = env
    assert "has not been created" in use(tools, "search_knowledge", query="anything")
    (root / "Knowledge").mkdir()
    assert "No Knowledge sources matched" in use(tools, "search_knowledge", query="anything")
    assert "[Source:" not in use(tools, "search_knowledge", query="anything")


def test_writes_and_deletes_are_audited(env):
    vault, tools, root, _, audit = env
    use(tools, "write_file", path="a.txt", content="hi")
    use(tools, "delete_path", path="a.txt")
    assert [(e["event"], e["path"]) for e in audit.entries()] == [
        ("file_written", "a.txt"),
        ("deleted", "a.txt"),
    ]


def test_project_copy_is_one_way_and_cannot_carry_git_or_links(env):
    vault, _, root, outside, audit = env
    source = root.parent / "Aura"
    (source / "src" / "aura").mkdir(parents=True)
    (source / "src" / "aura" / "main.py").write_text("version = 1\n")
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("[remote]\n")
    (source / "outside-link").symlink_to(outside)

    seeded = vault.seed_project_copy(source)

    assert seeded and seeded.copied
    copy = root / "Aura"
    assert (copy / "src" / "aura" / "main.py").read_text() == "version = 1\n"
    assert not (copy / ".git").exists()
    assert not (copy / "outside-link").exists()
    assert "Orient." in (copy / "AURA_ENGINEERING.md").read_text()
    assert "## Change design" in (copy / "CODING_STANDARDS.md").read_text()
    assert (copy / "docs" / "adr" / "README.md").is_file()
    (copy / "src" / "aura" / "main.py").write_text("version = 2\n")
    assert (source / "src" / "aura" / "main.py").read_text() == "version = 1\n"
    (copy / "GLOSSARY.md").write_text("# My vocabulary\n")
    again = vault.seed_project_copy(source)
    assert again and again.path == copy and not again.copied
    assert (copy / "GLOSSARY.md").read_text() == "# My vocabulary\n"  # docs are never overwritten
    assert audit.entries()[-1]["event"] == "project_sandbox_seeded"


def test_missing_legacy_skills_are_copied_to_the_vault_registry_without_overwriting(env):
    vault, _, root, _, audit = env
    source = root.parent / "Aura"
    legacy = source / "skills"
    legacy.mkdir(parents=True)
    (legacy / "from_root").mkdir()
    (legacy / "from_root" / "skill.py").write_text("root version\n")
    (root / "Aura" / "skills").mkdir(parents=True)
    (root / "Aura" / "skills" / "already_local").mkdir()
    (root / "Aura" / "skills" / "already_local" / "skill.py").write_text("vault version\n")
    (legacy / "already_local").mkdir()
    (legacy / "already_local" / "skill.py").write_text("root version\n")

    migrated = vault.migrate_skill_registry(source)

    assert migrated == ["from_root"]
    assert (root / "Aura" / "skills" / "from_root" / "skill.py").read_text() == "root version\n"
    assert (root / "Aura" / "skills" / "already_local" / "skill.py").read_text() == "vault version\n"
    assert audit.entries()[-1]["event"] == "skill_registry_migrated"


def test_only_approved_legacy_skills_migrate_when_an_approval_set_is_supplied(env):
    vault, _, root, _, _ = env
    source = root.parent / "Aura"
    legacy = source / "skills"
    for name in ("approved", "pending"):
        (legacy / name).mkdir(parents=True, exist_ok=True)
        (legacy / name / "skill.py").write_text(name)

    migrated = vault.migrate_skill_registry(source, approved={"approved"})

    assert migrated == ["approved"]
    assert (root / "Aura" / "skills" / "approved").is_dir()
    assert not (root / "Aura" / "skills" / "pending").exists()


# ── approval prompt text: honest about what will happen ────────────────────────────────────


def test_approval_descriptions(env):
    vault, tools, root, outside, _ = env
    (root / "old.txt").write_text("12345")
    d = vault.describe_change

    assert d("create_folder", {"path": "Project Aura"}) == "create folder 'Project Aura/'"
    assert d("delete_path", {"path": "old.txt"}) == "delete file 'old.txt' (5 B)"
    assert d("write_file", {"path": "new.md", "content": "a\nb"}) == (
        "create file 'new.md' (3 B):\n    │ a\n    │ b"
    )
    assert d("write_file", {"path": "old.txt", "content": "x", "overwrite": True}) == (
        "overwrite 'old.txt' (5 B → 1 B):\n    │ x"
    )
    assert "will be refused" in d("write_file", {"path": "old.txt", "content": "x"})
    assert "will be blocked" in d("delete_path", {"path": "../secret.txt"})


def test_a_long_file_is_read_in_parts_each_saying_how_to_read_the_next(tmp_path):
    # 2026-11-27: a 16k-character spec came back cut at 12k with no way on, and Aura asked for the
    # same part three times until its loop guard stopped it.
    from aura.audit import AuditLog
    from aura.tools.vault import READ_LIMIT, Vault

    vault = Vault(tmp_path / "v", AuditLog(tmp_path / "a.jsonl"))
    vault.ensure_exists()
    text = "".join(f"{i:05d}\n" for i in range(5000))  # 30,000 characters
    (vault.root / "spec.md").write_text(text)
    first = vault.read("spec.md")
    assert first.startswith(text[:READ_LIMIT]) and f"read_file(path='spec.md', offset={READ_LIMIT})" in first
    second = vault.read("spec.md", READ_LIMIT)
    assert second.startswith(text[READ_LIMIT:]) and "the end of the file" in second
    assert "past its end" in vault.read("spec.md", 99_999)
    (vault.root / "short.md").write_text("short")
    assert vault.read("short.md") == "short"  # a short file: no note


def test_search_vault_finds_lines_across_the_vault_like_grep(tmp_path):
    from aura.audit import AuditLog
    from aura.tools.vault import Vault

    vault = Vault(tmp_path / "v", AuditLog(tmp_path / "a.jsonl"))
    vault.ensure_exists()
    root = vault.root
    (root / "Aura/docs/specs").mkdir(parents=True)
    (root / "Aura/docs/specs/models-window.md").write_text("# Plan\nThe Models window opens from the menu.\n")
    (root / "Notes.md").write_text("Buy milk\nmodels are fun\n")
    (root / "Aura/app/.build").mkdir(parents=True)
    (root / "Aura/app/.build/x.md").write_text("Models window in build output\n")
    (root / "pic.png").write_bytes(b"\x89PNG\x00Models window")

    out = vault.search("models window")
    assert "Aura/docs/specs/models-window.md:2: The Models window opens from the menu." in out
    assert ".build" not in out and "pic.png" not in out and "in 1 file(s)" in out
    assert "Notes.md:2" in vault.search("fun models")  # every word, any order, when the phrase isn't there
    assert "contains 'nothing here'" in vault.search("nothing here")
    assert "Notes.md" not in vault.search("models", folder="Aura")  # only the folder asked for
