from skill import run


def test_diagnosis_is_evidence_gated():
    out = run({"symptom": "The audit stops at 92%", "scope": "Workshop"})
    assert "Reproduce" in out and "Regression proof" in out and "record evidence" in out


def test_diagnosis_does_not_claim_a_cause():
    assert "plausible explanation is not a diagnosis" in run({"symptom": "It broke"})
