from skill import run


def test_handoff_redacts_and_preserves_next_focus():
    out = run({"summary": "Fixed the parser. api_key=secret-value-12345", "next_focus": "Run the parser test.", "artifacts": "Aura/spec.md"})
    assert "[redacted]" in out and "secret-value" not in out
    assert "Run the parser test" in out and "`Aura/spec.md`" in out


def test_empty_summary_is_honest():
    assert "No session summary was supplied" in run({"summary": "", "next_focus": ""})
