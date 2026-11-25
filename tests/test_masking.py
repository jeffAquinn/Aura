"""Behavior-level tests for secrets redaction at Aura's output boundary."""

from __future__ import annotations

import itertools

import pytest

from aura.masking import MASK, MAX_HOLD, Masker


def test_mask_replaces_stored_literals_and_known_credential_shapes():
    literal = "demo-literal-secret-12345"
    anthropic = "sk-ant-" + "x" * 20
    masker = Masker(lambda: [literal])

    assert masker.mask(f"before {literal}; cloud {anthropic}; after") == f"before {MASK}; cloud {MASK}; after"


def test_mask_leaves_non_secrets_unchanged_and_is_idempotent():
    masker = Masker(lambda: ["demo-literal-secret-12345"])
    text = "Résumé: ordinary text stays unchanged."

    assert masker.mask(text) == text
    once = masker.mask("demo-literal-secret-12345")
    assert masker.mask(once) == once == MASK


def test_mask_value_recurses_through_json_values_without_changing_other_values():
    literal = "demo-literal-secret-12345"
    value = {
        "message": f"pasted {literal}",
        "nested": [literal, {"count": 2, "enabled": True}],
    }

    assert Masker(lambda: [literal]).mask_value(value) == {
        "message": f"pasted {MASK}",
        "nested": [MASK, {"count": 2, "enabled": True}],
    }


def test_refresh_discards_cached_literals_when_stored_secrets_change():
    current = ["first-demo-secret-12345"]
    masker = Masker(lambda: current)

    assert masker.mask(current[0]) == MASK
    current[:] = ["second-demo-secret-12345"]
    # This establishes that refresh(), not the normal cache window, reloads the changed source.
    assert masker.mask(current[0]) == current[0]  # the cache protects repeated Keychain reads

    masker.refresh()
    assert masker.mask(current[0]) == MASK


def test_stream_masks_a_literal_split_across_chunks_and_preserves_surrounding_text():
    literal = "demo-literal-secret-12345"
    stream = Masker(lambda: [literal]).stream()

    output = stream.feed("safe ") + stream.feed("demo-literal-") + stream.feed("secret-12345 done") + stream.flush()

    assert output == f"safe {MASK} done"


def test_stream_never_releases_part_of_a_stored_secret_when_max_hold_is_exceeded():
    literal = "demo-literal-secret-12345"
    stream = Masker(lambda: [literal]).stream()

    output = stream.feed(literal + "x" * (MAX_HOLD - 8)) + stream.flush()

    assert output == MASK


def test_stream_never_releases_a_suffix_of_a_shaped_credential_when_max_hold_is_exceeded():
    secret = "sk-ant-" + "A" * 16 + "Z" * 24
    stream = Masker().stream()

    output = stream.feed(secret + "D" * (MAX_HOLD - 20)) + stream.flush()

    assert "Z" not in output


def test_stream_discards_a_second_secret_that_straddles_an_overflow_boundary():
    first = "first-stored-secret-12345"
    second = "second-stored-secret-67890"
    prefix = first + "x" * (MAX_HOLD - len(first) - 4) + second[:5]
    stream = Masker(lambda: [first, second]).stream()

    output = stream.feed(prefix) + stream.feed(second[5:] + " safe") + stream.flush()

    assert output == f"{MASK} safe"
    assert second[:5] not in output
    assert second[5:] not in output


@pytest.mark.parametrize("separator", ["  ", "\t", " \t ", "\n"])
def test_mask_redacts_bearer_credentials_after_any_whitespace(separator):
    credential = "demo-bearer-token-12345"

    assert Masker().mask(f"Bearer{separator}{credential}") == f"Bearer{separator}{MASK}"


def test_refresh_failure_keeps_the_last_known_stored_secret_masked():
    literal = "demo-literal-secret-12345"
    values: list[str] | None = [literal]

    def source():
        if values is None:
            raise RuntimeError("Keychain unavailable")
        return values

    masker = Masker(source)
    assert masker.mask(literal) == MASK
    values = None
    masker.refresh()

    assert masker.mask(literal) == MASK


@pytest.mark.parametrize("literal", ["123456789012345678", "12345678901234567890.50", "1234567890123e5"])
def test_for_secrets_masks_the_original_long_json_scalar(literal):

    class FakeSecrets:
        def get(self, name):
            return literal if name == "anthropic_api_key" else None

    assert Masker.for_secrets(FakeSecrets()).mask(literal) == MASK


def test_mask_fully_redacts_overlapping_stored_secrets():
    first = "abcdefgh1234567890"
    second = "7890qrstuvwxyz12"
    suffix = "qrstuvwxyz12"

    assert suffix not in Masker(lambda: [first, second]).mask(first + suffix)


def stream_text(masker: Masker, text: str, sizes: list[int]) -> str:
    stream = masker.stream()
    chunks: list[str] = []
    position = 0
    for size in itertools.cycle(sizes):
        if position >= len(text):
            break
        chunks.append(stream.feed(text[position:position + size]))
        position += size
    return "".join(chunks) + stream.flush()


@pytest.mark.parametrize("sizes", [[1], [997], [3, 4096, 5]])
def test_overlong_shaped_credentials_never_leak_at_hold_boundaries(sizes):
    secret = "sk-ant-" + "A" * 16 + "Z" * 24
    for padding in range(MAX_HOLD - 60, MAX_HOLD + 60, 31):
        output = stream_text(Masker(), "x " + secret + "D" * padding, sizes)
        assert "Z" not in output and "AAAA" not in output


def test_literals_near_the_hold_boundary_never_leak_when_streamed_in_chunks():
    literal = "demo-literal-secret-12345"
    for lead in range(MAX_HOLD - 40, MAX_HOLD + 5, 11):
        output = stream_text(Masker(lambda: [literal]), "x" * lead + literal + "y" * 50, [97, 531])
        assert "demo-lit" not in output and "t-12345" not in output


def test_mask_merges_chains_and_contained_overlapping_literals():
    masker = Masker(lambda: ["aaaa11112222", "11112222bbbb", "2222bbbbcccc", "xaaaa11112222x"])

    output = masker.mask("xaaaa11112222bbbbccccx")

    assert all(fragment not in output for fragment in ["1111", "bbbb", "cccc"])


def test_stored_literal_inside_a_shaped_credential_does_not_expose_its_suffix():
    literal = "LITERALSECRET123"
    credential = "sk-ant-" + "A" * 16 + literal + "Z" * 24

    output = Masker(lambda: [literal]).mask(credential)

    assert "AAAA" not in output
    assert "Z" not in output
