from skill import run


def test_retro_identifies_a_loop_guard():
    out = run({"session_summary": "The audit got stuck in a retry loop after a failed test.", "scope": "Workshop"})
    assert "Control flow" in out and "Automated check" in out and "Workshop" in out


def test_retro_does_not_claim_to_change_anything():
    assert "review and approve" in run({"session_summary": "A slow task", "scope": "Aura"})
