from skill import run


def test_map_has_explicit_blockers():
    out = run({"objective": "Build a Vault workflow", "scope": "Aura"})
    assert "D1" in out and "D5" in out and "Blocked by: D4" in out


def test_map_does_not_guess_an_approach():
    assert "not permission to guess" in run({"objective": "A large idea"})
