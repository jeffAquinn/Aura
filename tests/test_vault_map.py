"""The Codex of Emergence: the vault's layout, laid out once, never overwriting, known to Aura."""

from __future__ import annotations

from datetime import date

import pytest

from aura.audit import AuditLog
from aura.persona import system_prompt
from aura.tools import vault_map
from aura.tools.vault import Vault

DAY = date(2026, 11, 23)
PARTS = ["The_Absolute_R", "The_Interface_Many", "The_Witness_Relational"]
LIBRARIES = ["Knowledge", "Wisdom"]


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    return Vault(root, AuditLog(tmp_path / "audit.jsonl"))


def test_an_empty_vault_gets_the_three_parts_the_map_and_the_manifesto(vault):
    created = vault_map.seed(vault, DAY)
    assert created == [*(p + "/" for p in PARTS), "Conversations/", *(p + "/" for p in LIBRARIES),
                       "Manifesto_of_the_Lens.md", "Map_of_the_Ascent.md"]
    assert all((vault.root / p).is_dir() for p in PARTS)
    manifesto = (vault.root / "Manifesto_of_the_Lens.md").read_text()
    assert vault_map.REALIZATION in manifesto and "Monday, November 23, 2026" in manifesto
    the_map = (vault.root / "Map_of_the_Ascent.md").read_text()
    assert all(f"[{p}](<{p}/>)" in the_map for p in [*PARTS, *LIBRARIES]) and "(no notes yet)" in the_map
    assert "vault_map_laid_out" in vault.audit.path.read_text()


def test_existing_notes_are_kept_and_indexed_on_the_map(vault):
    (vault.root / "Conversations").mkdir()
    note = vault.root / "Conversations" / "The_Architecture_of_Emergence.md"
    note.write_text("# Ours\n")
    (vault.root / "Manifesto_of_the_Lens.md").write_text("the user's own words")
    created = vault_map.seed(vault, DAY)
    assert "Conversations/" not in created and "Manifesto_of_the_Lens.md" not in created  # never overwritten
    assert (vault.root / "Manifesto_of_the_Lens.md").read_text() == "the user's own words"
    assert note.read_text() == "# Ours\n"
    assert ("- [The Architecture of Emergence](<Conversations/The_Architecture_of_Emergence.md>)"
            in (vault.root / "Map_of_the_Ascent.md").read_text())  # fmt: skip


def test_it_is_laid_out_once_a_removed_part_stays_removed(vault):
    vault_map.seed(vault, DAY)
    (vault.root / PARTS[1]).rmdir()
    (vault.root / "Map_of_the_Ascent.md").unlink()
    assert vault_map.seed(vault, DAY) == []  # another part remains: the user's choice stands
    assert not (vault.root / PARTS[1]).exists() and not (vault.root / "Map_of_the_Ascent.md").exists()


def test_an_existing_vault_gets_libraries_once_without_recreating_removed_folders(vault):
    (vault.root / PARTS[0]).mkdir()
    assert vault_map.seed(vault) == ["Knowledge/", "Wisdom/"]
    (vault.root / "Knowledge").rmdir()
    assert vault_map.seed(vault) == []


def test_aura_knows_where_each_kind_of_note_belongs(tmp_path):
    prompt = system_prompt(DAY, "gemma4:26b", tmp_path)
    assert "Codex of Emergence" in prompt and "not the Codex developer" in prompt
    assert "[Source: Knowledge/path.md]" in prompt
    for folder in [*PARTS, *LIBRARIES, "Conversations", "Map_of_the_Ascent.md", "Manifesto_of_the_Lens.md"]:
        assert folder in prompt


def test_starting_aura_lays_out_the_vault(tmp_path, monkeypatch, capsys):
    import builtins

    from aura import main

    monkeypatch.setattr(builtins, "input", lambda prompt="": (_ for _ in ()).throw(EOFError))
    main.main()  # starts, then leaves at once (Ctrl-D)
    root = tmp_path / "vault"  # conftest points AURA_VAULT_DIR here
    assert all((root / p).is_dir() for p in PARTS) and (root / "Map_of_the_Ascent.md").is_file()
