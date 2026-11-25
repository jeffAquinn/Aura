from skill import run


def test_handoff_requires_approval_and_evidence():
    out = run({"spec": "Add a safe model picker", "scope": "Aura/app"})
    assert "Allowed scope: Aura/app" in out and "Evidence required" in out and "present this handoff for approval" in out


def test_missing_scope_is_not_silently_allowed():
    assert "Not specified — do not hand off yet" in run({"spec": "Do a thing", "scope": ""})
