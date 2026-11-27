"""Acceptance tests (docs/specs/vault-integrity.md): fixtures are built in a temp vault, and the
skill must stay read-only. Run by Aura inside the skill's container, never on the host."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from skill import MAX_FILES, run


def make_vault() -> Path:
    root = Path(tempfile.mkdtemp()) / "vault"
    (root / "notes").mkdir(parents=True)
    (root / "notes/plan.md").write_text("Draft plan.\n")
    (root / "notes/ideas.md").write_text("Ideas.\n")
    (root / "notes/todo.md").write_text("Todo.\n")
    (root / "notes/.DS_Store").write_text("hidden")
    (root / "notes/sub").mkdir()
    (root / "notes/sub/deep.md").write_text("Not in the folder's own level.\n")
    return root


def state(root: Path) -> dict:
    return {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(root.rglob("*")) if p.is_file()}


def call(vault: Path, **args) -> dict:
    full = {"path": "", "baseline": "", "expected_sha256": "", "expected_content": "", **args}
    return json.loads(run(full, vault=vault))


def test_1_fingerprint_lists_the_folder_and_changes_nothing():
    v = make_vault()
    before = state(v)
    out = call(v, path="notes/plan.md")
    assert out["status"] == "fingerprinted" and out["baseline"]["folder"] == "notes"
    files = out["baseline"]["files"]
    assert set(files) == {"plan.md", "ideas.md", "todo.md"}  # hidden files and subfolders left out
    assert files["plan.md"]["sha256"] == hashlib.sha256(b"Draft plan.\n").hexdigest()
    assert out["target"]["path"] == "notes/plan.md"
    assert state(v) == before  # read-only: no content or time changed


def test_2_a_safe_change_is_reported_safe():
    v = make_vault()
    baseline = json.dumps(call(v, path="notes/plan.md")["baseline"])
    (v / "notes/plan.md").write_text("Final plan.\n")
    expected = hashlib.sha256(b"Final plan.\n").hexdigest()
    out = call(v, path="notes/plan.md", baseline=baseline, expected_sha256=expected)
    assert (out["status"], out["integrity_check"], out["side_effects"]) == ("safe", True, [])
    by_content = call(v, path="notes/plan.md", baseline=baseline, expected_content="Final plan.\n")
    assert by_content["status"] == "safe" and by_content["integrity_check"] is True


def test_3_wrong_content_is_unsafe():
    v = make_vault()
    baseline = json.dumps(call(v, path="notes/plan.md")["baseline"])
    (v / "notes/plan.md").write_text("Something else.\n")
    out = call(v, path="notes/plan.md", baseline=baseline, expected_content="Final plan.\n")
    assert (out["status"], out["integrity_check"]) == ("unsafe", False)
    assert "does not hold the expected content" in out["message"]


def test_4_side_effects_are_listed():
    v = make_vault()
    baseline = json.dumps(call(v, path="notes/plan.md")["baseline"])
    (v / "notes/plan.md").write_text("Final plan.\n")
    (v / "notes/ideas.md").write_text("Changed behind our back.\n")
    (v / "notes/new.md").write_text("Appeared.\n")
    (v / "notes/todo.md").unlink()
    out = call(v, path="notes/plan.md", baseline=baseline, expected_content="Final plan.\n")
    assert out["status"] == "unsafe" and out["integrity_check"] is True
    assert out["side_effects"] == [{"file": "ideas.md", "change": "changed"}, {"file": "new.md", "change": "added"},
                                   {"file": "todo.md", "change": "removed"}]


def test_5_paths_outside_the_vault_and_links_are_refused():
    v = make_vault()
    secret = v.parent / "secret.txt"
    secret.write_text("outside")
    assert "outside the vault" in call(v, path="../secret.txt")["message"]
    os.symlink(secret, v / "notes/link.md")
    out = call(v, path="notes/link.md")
    assert out["status"] == "unsafe" and "symbolic link" in out["message"]
    os.symlink(v.parent, v / "escape")
    assert call(v, path="escape/secret.txt")["status"] == "unsafe"
    assert "folder" in call(v, path="notes")["message"]


def test_6_a_baseline_from_another_folder_is_refused():
    v = make_vault()
    other = json.dumps(call(v, path="notes/sub/deep.md")["baseline"])
    out = call(v, path="notes/plan.md", baseline=other)
    assert out["status"] == "unsafe" and "not 'notes'" in out["message"]
    assert "isn't a fingerprint" in call(v, path="notes/plan.md", baseline="{oops")["message"]


def test_a_new_file_can_be_fingerprinted_before_it_exists():
    v = make_vault()
    out = call(v, path="notes/fresh.md")
    assert out["target"] is None and "does not exist yet" in out["message"]
    baseline = json.dumps(out["baseline"])
    (v / "notes/fresh.md").write_text("New.\n")
    done = call(v, path="notes/fresh.md", baseline=baseline, expected_content="New.\n")
    assert done["status"] == "safe"


def test_large_folders_are_bounded():
    v = make_vault()
    for i in range(MAX_FILES + 5):
        (v / f"notes/n{i:04}.md").write_text(str(i))
    out = call(v, path="notes/plan.md")
    assert len(out["baseline"]["files"]) == MAX_FILES and f"first {MAX_FILES}" in out["message"]
