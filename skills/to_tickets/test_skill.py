from skill import run


def test_tickets_are_small_and_explicit_about_dependencies():
    out = run({"plan": "Add settings\nAdd tests", "scope": "Aura"})
    assert "T1" in out and "T2" in out and "Blocking edge" in out


def test_empty_plan_stays_a_draft():
    assert "not an authoritative work graph" in run({"plan": ""})
