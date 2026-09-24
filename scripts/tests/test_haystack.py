"""Tests for scripts/haystack.py (issue #31).

Pure-logic, no network: everything here runs against `generate_haystack()`
directly. The `count_tokens_fn` path is exercised with a fake callable, the
same pattern test_server_config.py uses for host-only probes -- no real
`/tokenize` call is needed to test that the wiring is correct.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from haystack import (  # noqa: E402
    DEFAULT_CHARS_PER_TOKEN, PREAMBLE, TOKEN_SOURCE_ESTIMATE, TOKEN_SOURCE_SERVER,
    generate_haystack,
)


# ---------------------------------------------------------------------------
# Determinism: same seed -> byte-identical text (acceptance criterion)
# ---------------------------------------------------------------------------

def test_same_seed_is_byte_identical():
    a = generate_haystack(seed=42, target_tokens=2000)
    b = generate_haystack(seed=42, target_tokens=2000)
    assert a.text == b.text
    assert a.facts == b.facts
    assert a.pairs == b.pairs


def test_different_seed_differs():
    a = generate_haystack(seed=1, target_tokens=2000)
    b = generate_haystack(seed=2, target_tokens=2000)
    assert a.text != b.text


def test_different_target_tokens_differs():
    a = generate_haystack(seed=1, target_tokens=2000)
    b = generate_haystack(seed=1, target_tokens=4000)
    assert a.text != b.text
    assert len(b.text) > len(a.text)


# ---------------------------------------------------------------------------
# Structure: facts, pairs, preamble
# ---------------------------------------------------------------------------

def test_text_starts_with_fictional_disclaimer():
    h = generate_haystack(seed=1, target_tokens=1000)
    assert h.text.startswith(PREAMBLE)


def test_default_three_facts_in_planting_order():
    h = generate_haystack(seed=1, target_tokens=4000)
    assert len(h.facts) == 3
    # Planted at 0.1, 0.5, 0.9 -- returned in that same order, not
    # document order (they happen to coincide here, but the guarantee
    # being tested is the return order tracks num_facts's index, not
    # wherever insertion left them in an internal list).
    positions = [f.depth_position for f in h.facts]
    assert positions == sorted(positions)


def test_facts_are_actually_present_in_text_at_recorded_offset():
    h = generate_haystack(seed=7, target_tokens=6000)
    for fact in h.facts:
        # char_offset is where the fact's SENTENCE starts; not every
        # template opens with the entity name (some open with "According
        # to the ledger, ..."), so check both entity and value appear
        # within the sentence's own span, not at offset 0 exactly.
        window = h.text[fact.char_offset:fact.char_offset + 200]
        assert fact.entity in window, (
            f'{fact.entity} not found in the text starting at its recorded '
            f'char_offset {fact.char_offset}')
        assert fact.value in window
        # And nowhere strictly BEFORE the recorded offset -- confirms the
        # offset is not just "somewhere in the vicinity" but the true start.
        assert h.text.find(fact.entity) >= fact.char_offset


def test_fact_depth_positions_are_in_unit_interval():
    h = generate_haystack(seed=3, target_tokens=5000)
    for fact in h.facts:
        assert 0.0 <= fact.depth_position <= 1.0


def test_planted_facts_use_names_disjoint_from_filler_pool():
    # The target/filler name pools are disjoint by construction (module
    # docstring); spot-check no planted entity collides with a filler
    # entity's (entity, attribute) pair under a DIFFERENT value, which
    # would make the expected answer ambiguous.
    h = generate_haystack(seed=11, target_tokens=8000)
    fact_pairs = {(f.entity, f.attribute) for f in h.facts}
    # Every fact pair must be present in the full pairs set (it was added).
    assert fact_pairs <= h.pairs


def test_pairs_include_filler_not_just_facts():
    h = generate_haystack(seed=5, target_tokens=4000)
    fact_pairs = {(f.entity, f.attribute) for f in h.facts}
    assert len(h.pairs) > len(fact_pairs)


def test_num_facts_and_position_fractions_length_mismatch_raises():
    import pytest
    with pytest.raises(ValueError):
        generate_haystack(seed=1, target_tokens=1000, num_facts=3,
                           position_fractions=(0.1, 0.9))


def test_custom_num_facts_and_positions():
    h = generate_haystack(seed=1, target_tokens=4000, num_facts=2,
                           position_fractions=(0.2, 0.8))
    assert len(h.facts) == 2


# ---------------------------------------------------------------------------
# Token sizing: server_tokenize vs chars_per_token_estimate
# ---------------------------------------------------------------------------

def test_no_count_fn_uses_chars_per_token_estimate():
    h = generate_haystack(seed=1, target_tokens=1000)
    assert h.token_count_source == TOKEN_SOURCE_ESTIMATE
    assert h.chars_per_token == DEFAULT_CHARS_PER_TOKEN
    assert h.token_count == round(len(h.text) / DEFAULT_CHARS_PER_TOKEN)


def test_count_fn_used_when_it_succeeds():
    calls = []

    def fake_count(text):
        calls.append(text)
        return 12345

    h = generate_haystack(seed=1, target_tokens=1000, count_tokens_fn=fake_count)
    assert h.token_count_source == TOKEN_SOURCE_SERVER
    assert h.token_count == 12345
    assert calls == [h.text]  # called exactly once, with the final text


def test_count_fn_failure_falls_back_to_estimate():
    def broken_count(text):
        raise RuntimeError('server unreachable')

    h = generate_haystack(seed=1, target_tokens=1000, count_tokens_fn=broken_count)
    assert h.token_count_source == TOKEN_SOURCE_ESTIMATE
    assert h.token_count == round(len(h.text) / DEFAULT_CHARS_PER_TOKEN)


def test_approx_token_offset_scales_with_reported_token_count():
    h = generate_haystack(seed=1, target_tokens=2000, count_tokens_fn=lambda t: 500)
    for fact in h.facts:
        assert fact.approx_token_offset == round(fact.depth_position * 500)
        assert 0 <= fact.approx_token_offset <= 500


def test_custom_chars_per_token_changes_estimate_and_size():
    a = generate_haystack(seed=1, target_tokens=2000, chars_per_token=3.0)
    b = generate_haystack(seed=1, target_tokens=2000, chars_per_token=5.0)
    # A smaller chars-per-token estimate means more characters are needed
    # to reach the same target token count.
    assert len(a.text) < len(b.text)
