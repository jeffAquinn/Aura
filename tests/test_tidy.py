"""Keeping the vault free of clutter: find_clutter lists it, tidy_vault (one approval card) moves it
to the Trash, where the user can still recover it."""

from __future__ import annotations

import os
import time

from aura.audit import AuditLog
from aura.tools.vault import STALE_DAYS, Vault


def vault_with_clutter(tmp_path):
    vault = Vault(tmp_path / "vault", AuditLog(tmp_path / "audit.jsonl"))
    vault.ensure_exists()
    root = vault.root
    (root / "Project/app/.build/debug").mkdir(parents=True)
    (root / "Project/app/.build/debug/a.o").write_bytes(b"x" * 2_000_000)
    (root / "Project/src/__pycache__").mkdir(parents=True)
    (root / "Project/src/__pycache__/m.pyc").write_bytes(b"x" * 10)
    (root / "Project/_scratch").mkdir()
    old = root / "Project/_scratch/plan.md"
    old.write_text("a plan for one task")
    week_ago = time.time() - (STALE_DAYS + 1) * 86400
    os.utime(old, (week_ago, week_ago))
    (root / "Project/_scratch/today.md").write_text("still in use")
    (root / "Project/notes.md").write_text("keep me")
    return vault


def test_clutter_is_build_output_and_stale_one_time_files_only(tmp_path):
    vault = vault_with_clutter(tmp_path)
    found = {item["path"]: item["why"] for item in vault.find_clutter()}
    assert found == {"Project/app/.build/": "build output or cache", "Project/src/__pycache__/": "build output or cache",
                     "Project/_scratch/plan.md": f"one-time file, unchanged for {STALE_DAYS + 1} days"}


def test_tidying_moves_to_the_trash_and_refuses_what_it_must_not_touch(tmp_path):
    vault = vault_with_clutter(tmp_path)
    trash = tmp_path / "Trash"
    trash.mkdir()
    result = vault.trash(["Project/app/.build", "Project/_scratch/plan.md", "../outside.txt", ".", "nope.md",
                          "Project/notes.md", "Project"], trash_dir=trash)
    assert sorted(p.name for p in trash.iterdir()) == [".build", "plan.md"]  # recoverable from the Trash
    assert not (vault.root / "Project/app/.build").exists() and (vault.root / "Project/notes.md").exists()
    assert "Moved 2 item(s)" in result and "../outside.txt" in result and "nope.md" in result
    assert "Project/notes.md (not clutter" in result and (vault.root / "Project").is_dir()  # never the user's work
    assert [e["path"] for e in vault.audit.entries() if e["event"] == "trashed"] == ["Project/app/.build/", "Project/_scratch/plan.md"]


def test_tidy_vault_asks_first_with_one_card_for_the_whole_list(settings):
    from conftest import call, make_aura, tool_turn

    aura, _ = make_aura(settings, tool_turn(call("tidy_vault", paths=["a/.build", "b/_scratch/plan.md"])))
    events = list(aura.stream("Tidy my vault."))
    card = events[-1]
    assert type(card).__name__ == "ApprovalNeeded" and [a.tool for a in card.actions] == ["tidy_vault"]
