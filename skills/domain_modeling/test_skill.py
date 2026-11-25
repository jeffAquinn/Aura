import tempfile
from pathlib import Path

from skill import run


def make_vault():
    root = Path(tempfile.mkdtemp())
    (root / "Aura").mkdir()
    (root / "Aura" / "GLOSSARY.md").write_text("## Vault\n\n- **Skill Inbox**: pending skills\n")
    return root


def test_reads_explicit_domain_labels_only():
    out = run({"scope": "Aura", "focus": "skills"}, vault=make_vault())
    assert "**Vault**" in out and "**Skill Inbox**" in out and "Stress-test questions" in out


def test_scope_escape_is_rejected():
    assert run({"scope": "../nope"}, vault=make_vault()) == "That scope is outside the vault."
