"""HOL keeps every mutable operation inside the user's Vault Aura copy."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from aura.audit import AuditLog
from aura.hol import BUILTIN_NAME, HolError, HolManager
from aura.skills import SkillManager, content_hash
from aura.tools.sandbox import Sandbox


def hol(settings) -> tuple[HolManager, SkillManager]:
    settings.project_sandbox_dir.mkdir(parents=True)
    (settings.project_sandbox_dir / "keep.txt").write_text("original")
    audit = AuditLog(settings.audit_log)
    manager = SkillManager(settings.skills_dir, settings.data_dir, Sandbox("unused", settings.vault_dir, 1, audit), audit)
    return HolManager(settings, manager, audit), manager


def approve_builtin(manager: HolManager, skills: SkillManager, settings) -> None:
    manager.ensure_builtin_candidate()
    source, live = settings.skills_inbox / BUILTIN_NAME, settings.skills_dir / BUILTIN_NAME
    live.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, live)
    skills.approve(BUILTIN_NAME, content_hash(live), "1.0.0", "")


def test_hol_session_is_offered_to_workshop_but_not_runnable_until_approved(settings):
    manager, skills = hol(settings)
    manager.ensure_builtin_candidate()
    assert (settings.skills_inbox / BUILTIN_NAME / "skill.toml").is_file()
    assert manager.sessions() == []

    approve_builtin(manager, skills, settings)
    session = manager.sessions()
    assert [s["name"] for s in session] == [BUILTIN_NAME]
    started = manager.start(BUILTIN_NAME, model={"provider": "ollama", "model": "test"}, effort="low")
    assert started["working_directory"] == str(settings.project_sandbox_dir)
    assert "disposable, no-network container" in "\n".join(started["output"])


def test_savepoints_restore_only_the_vault_copy_and_keep_three(settings):
    manager, _ = hol(settings)
    root = settings.project_sandbox_dir
    points = []
    for value in ("one", "two", "three", "four"):
        (root / "keep.txt").write_text(value)
        points.append(manager.create_savepoint(value))
    assert len(manager.public(model={"provider": "ollama", "model": "test"}, effort=None)["savepoints"]) == 3

    (root / "keep.txt").write_text("changed")
    manager.restore_savepoint(points[-1]["id"])
    assert (root / "keep.txt").read_text() == "four"
    with pytest.raises(HolError, match="No such"):
        manager.restore_savepoint(points[0]["id"])


def test_savepoint_index_cannot_escape_its_owned_vault_directory(settings, tmp_path):
    manager, _ = hol(settings)
    manager.create_savepoint("first")
    outside = tmp_path / "must-not-delete"
    outside.mkdir()
    (outside / "keep.txt").write_text("safe")
    manager.index_path.write_text(json.dumps([{"id": "../../must-not-delete", "created": "now", "label": "bad"}]))
    orphan = manager.savepoints_dir / "interrupted-copy"
    orphan.mkdir()

    manager.create_savepoint("second")
    assert (outside / "keep.txt").read_text() == "safe"
    assert not orphan.exists()
    assert len(manager.public(model={"provider": "ollama", "model": "test"}, effort=None)["savepoints"]) == 1


def test_restore_original_uses_clean_git_head_and_never_replaces_source(settings, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "readme.txt").write_text("clean")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "readme.txt"], cwd=source, check=True)
    subprocess.run(["git", "-c", "user.name=Aura", "-c", "user.email=aura@example.test", "commit", "-qm", "initial"], cwd=source, check=True)
    sandbox = settings.vault_dir / source.name
    sandbox.mkdir(parents=True)
    (sandbox / "readme.txt").write_text("edited")
    object.__setattr__(settings, "project_dir", source)
    manager = HolManager(settings, SkillManager(settings.skills_dir, settings.data_dir,
                         Sandbox("unused", settings.vault_dir, 1, AuditLog(settings.audit_log)), AuditLog(settings.audit_log)),
                         AuditLog(settings.audit_log))

    manager.restore_original()
    assert (sandbox / "readme.txt").read_text() == "clean"
    assert (source / "readme.txt").read_text() == "clean"
