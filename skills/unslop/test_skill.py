"""Acceptance tests for the offline, supplied-text-only Unslop adaptation."""

from skill import MAX_TEXT_CHARS, mechanically_rewrite, run


def test_review_reports_stable_rule_identifiers():
    output = run({"text": "Great question! Experts believe this pivotal tool serves as a game-changing flywheel."})
    for rule_id in (5, 7, 8, 22, 26, 27):
        assert f"R{rule_id}:" in output
    assert "Editorial boundary" in output


def test_rewrite_makes_only_mechanical_changes():
    source = "In order to utilize it, this serves as “proof.”"
    output = run({"text": source, "mode": "rewrite"})
    assert "to use it, this is \"proof.\"" in output
    assert "Applied 5 mechanical edit(s)." in output


def test_rewrite_leaves_meaning_dependent_patterns_for_review():
    output = run({"text": "This is not just fast, but reliable — and perhaps maybe useful.", "mode": "rewrite"})
    assert "R9:" in output
    assert "R13:" in output
    assert "R24:" in output


def test_invalid_input_is_rejected():
    try:
        run({"text": ""})
        assert False, "expected ValueError"
    except ValueError as error:
        assert "non-empty" in str(error)
    try:
        run({"text": "x", "mode": "delete"})
        assert False, "expected ValueError"
    except ValueError as error:
        assert "review' or 'rewrite" in str(error)


def test_input_limit_and_no_hidden_io_helpers():
    try:
        run({"text": "x" * (MAX_TEXT_CHARS + 1)})
        assert False, "expected ValueError"
    except ValueError as error:
        assert str(MAX_TEXT_CHARS) in str(error)
    revised, count = mechanically_rewrite("Plain text.")
    assert revised == "Plain text." and count == 0
