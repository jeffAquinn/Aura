from pathlib import Path

from aura.citations import verify_citations, warning


def test_valid_knowledge_citation_passes(tmp_path):
    source = tmp_path / "Knowledge" / "orientation.md"
    source.parent.mkdir()
    source.write_text("local source")

    assert verify_citations("Grounded. [Source: Knowledge/orientation.md]", tmp_path) == []


def test_missing_and_non_knowledge_citations_are_reported(tmp_path):
    (tmp_path / "notes.md").write_text("not a source")
    findings = verify_citations(
        "[Source: Knowledge/missing.md] [Source: notes.md] [Source: ../../etc/passwd]", tmp_path,
    )

    assert [f.reason for f in findings] == ["not found", "not a Knowledge source", "outside the vault"]
    assert warning(findings) == "\n\n⚠️ Local citation check: 3 citations could not be verified in Knowledge/."
