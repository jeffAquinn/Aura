"""Vault whitelist enforcement: the file tools cannot touch anything outside the vault."""

from __future__ import annotations

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


def test_read_truncates_long_files_and_refuses_binary(env):
    vault, tools, root, _, _ = env
    (root / "long.txt").write_text("x" * (READ_LIMIT + 50))
    (root / "img.bin").write_bytes(b"\x89PNG\x00\x00binary")

    long = use(tools, "read_file", path="long.txt")
    assert long.startswith("x" * READ_LIMIT) and "[truncated" in long
    assert "binary file" in use(tools, "read_file", path="img.bin")


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
