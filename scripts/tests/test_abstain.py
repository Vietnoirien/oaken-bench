"""Tests for scripts/abstain.py (issue #32). Pure logic, no network -- the
end-to-end abstention path (through recall.py against a fake server) is
covered by test_recall.py instead, matching that file's own two-layer
convention.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import abstain  # noqa: E402
import haystack as haystack_mod  # noqa: E402


# ---------------------------------------------------------------------------
# is_abstention: the documented, unit-tested phrasing list
# ---------------------------------------------------------------------------

def test_is_abstention_accepts_documented_phrasings():
    # Every case here must correspond to an entry (or a normalized form of
    # one) in abstain.ABSTENTION_MARKERS -- this test IS the "documented
    # and unit-tested" acceptance criterion, not just a smoke check of it.
    accepted = [
        'not in context',
        'Not in context.',
        'Not in the context.',
        'NOT IN CONTEXT',
        'This is not stated in the document.',
        'The document does not mention this.',
        'No information provided for this pair.',
        'Not found in the ledger.',
        'Not specified.',
        'That value is not present in the text.',
        'No record of this entity.',
        'There is no such record for this attribute.',
        'This does not appear in the document.',
        "I cannot find this in the text.",
        "I can't find it.",
        'Unknown.',
        'N/A',
        'n/a',
        'N.A.',
        "I don't know.",
        'I do not know.',
    ]
    for value in accepted:
        assert abstain.is_abstention(value), f'{value!r} should be accepted as abstention'


def test_is_abstention_rejects_a_real_looking_value():
    # A generated fictional value ("Kestrel-482"-shaped) or an ordinary
    # sentence must NOT be treated as an abstention -- otherwise a model
    # that invents plausible answers would be scored as correctly
    # abstaining, which is exactly the failure mode #32 exists to catch.
    for value in ('Kestrel-482', 'Marlin-731', 'The custodian is Basalt-119.',
                  'Its clearance code is Opal-204.'):
        assert not abstain.is_abstention(value)


def test_is_abstention_rejects_bare_no():
    # Deliberately excluded -- see the module docstring: "no" is too
    # likely to appear inside a real hedge or a real value to accept as an
    # abstention marker on its own.
    assert not abstain.is_abstention('No, that is Basalt-119.')
    assert not abstain.is_abstention('no')


def test_is_abstention_rejects_non_string():
    assert not abstain.is_abstention(None)
    assert not abstain.is_abstention(42)
    assert not abstain.is_abstention(['not in context'])


def test_instructed_phrase_is_itself_accepted():
    # The exact phrase recall.py puts in front of the model must itself
    # pass -- a model that follows the instruction to the letter has to
    # score as correctly abstaining, not fall through some normalization
    # edge case.
    assert abstain.is_abstention(abstain.INSTRUCTED_PHRASE)


# ---------------------------------------------------------------------------
# build_abstention_questions: guaranteed absence, near-miss kinds
# ---------------------------------------------------------------------------

def _haystack(seed=1, target_tokens=4000):
    return haystack_mod.generate_haystack(seed=seed, target_tokens=target_tokens)


def test_build_abstention_questions_returns_three_of_each_kind():
    h = _haystack()
    qs = abstain.build_abstention_questions(h, seed=99, num_each=2)
    by_kind = {}
    for q in qs:
        by_kind.setdefault(q.kind, []).append(q)
    assert set(by_kind) == set(abstain.ALL_KINDS)
    for kind in abstain.ALL_KINDS:
        assert len(by_kind[kind]) == 2


def test_build_abstention_questions_all_guaranteed_absent_by_construction():
    # This IS issue #32's "absence is guaranteed by construction, and a
    # test checks it" acceptance criterion -- checked against the
    # haystack's own `pairs`, the ground truth of what is actually in the
    # generated text, not re-derived from how the question was built.
    h = _haystack()
    qs = abstain.build_abstention_questions(h, seed=7, num_each=3)
    for q in qs:
        assert (q.entity, q.attribute) not in h.pairs, (
            f'{q.kind} question {(q.entity, q.attribute)!r} is actually present')


def test_near_miss_same_entity_reuses_an_entity_from_the_document():
    h = _haystack()
    qs = abstain.build_abstention_questions(h, seed=11, num_each=2)
    entities_in_doc = {e for e, _a in h.pairs}
    same_entity = [q for q in qs if q.kind == abstain.KIND_NEAR_MISS_SAME_ENTITY]
    assert same_entity
    for q in same_entity:
        assert q.entity in entities_in_doc
        # The near-miss is on the ATTRIBUTE: this entity has some other
        # attribute recorded, just not this one.
        assert any(e == q.entity and a != q.attribute for e, a in h.pairs)


def test_near_miss_same_attribute_reuses_an_attribute_from_the_document():
    h = _haystack()
    qs = abstain.build_abstention_questions(h, seed=13, num_each=2)
    attrs_in_doc = {a for _e, a in h.pairs}
    entities_in_doc = {e for e, _a in h.pairs}
    same_attribute = [q for q in qs if q.kind == abstain.KIND_NEAR_MISS_SAME_ATTRIBUTE]
    assert same_attribute
    for q in same_attribute:
        assert q.attribute in attrs_in_doc
        # The near-miss is on the ENTITY: this attribute is recorded for
        # some other entity, but this specific entity is not in the
        # document at all.
        assert q.entity not in entities_in_doc


def test_guaranteed_absent_entity_is_not_in_the_document_at_all():
    h = _haystack()
    qs = abstain.build_abstention_questions(h, seed=17, num_each=2)
    entities_in_doc = {e for e, _a in h.pairs}
    baseline = [q for q in qs if q.kind == abstain.KIND_GUARANTEED_ABSENT]
    assert baseline
    for q in baseline:
        assert q.entity not in entities_in_doc


def test_build_abstention_questions_is_deterministic_per_seed():
    h = _haystack()
    a = abstain.build_abstention_questions(h, seed=42, num_each=2)
    b = abstain.build_abstention_questions(h, seed=42, num_each=2)
    assert a == b


def test_build_abstention_questions_seed_does_not_mutate_haystack_text():
    # Building abstention questions must never consume the haystack's own
    # rng stream (there isn't one exposed) -- regenerating the SAME
    # haystack seed must keep producing the same text regardless of how
    # many abstention questions were built against a previous instance.
    h1 = _haystack(seed=5)
    abstain.build_abstention_questions(h1, seed=1, num_each=5)
    h2 = _haystack(seed=5)
    assert h1.text == h2.text
