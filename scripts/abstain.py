#!/usr/bin/env python3
"""Abstention questions and phrasing acceptance for the T0.5 abstention
battery (issue #32), built on `haystack.py`'s exposed `facts` and
`(entity, attribute)` `pairs` -- see haystack.py's module docstring,
"Interfaces #32 is written against". `recall.py` runs this battery inline,
sharing one haystack (and one prefill) per depth with the recall battery
from issue #31; see recall.py's module docstring for why.

## What is asked

`build_abstention_questions()` returns `AbstentionQuestion`s in three
kinds, each guaranteed absent from the haystack BY CONSTRUCTION -- checked
against `Haystack.pairs` before being returned, not merely assumed from how
it was generated (`test_abstain.py`'s `test_*_is_guaranteed_absent` tests
are issue #32's "absence is guaranteed by construction, and a test checks
it" acceptance criterion):

  - `guaranteed_absent` -- an entity that appears NOWHERE in the document,
    paired with a random attribute. The baseline: nothing about this pair
    should look familiar.
  - `near_miss_same_entity` -- an entity that DOES appear in the document
    (with some OTHER attribute recorded), paired with an attribute it does
    not have. Probes whether a model that half-recognises "I've seen this
    entity" fills in a plausible-looking value instead of noticing that
    THIS pair was never stated.
  - `near_miss_same_attribute` -- an attribute that DOES appear in the
    document (recorded for some OTHER entity), paired with a fresh entity
    that appears nowhere. The mirror image: "I've seen this attribute
    mentioned" is not evidence for THIS entity.

Every abstention question issue #32 asks for is one of these three kinds --
there is no question that is absent for no describable reason, which is
also what makes `byKind` breakdowns in the artefact meaningful instead of
just a raw pass/fail count.

## Accepted abstention phrasings

`recall.py`'s SYSTEM_PROMPT instructs the model to answer exactly
"not in context" for a pair the document does not state. Real models
paraphrase instructions even when following them in good faith, so scoring
matches leniently: `is_abstention()` lowercases and strips punctuation
(`_normalize`), then checks the result for any of `ABSTENTION_MARKERS` as a
SUBSTRING -- "Not in the context; no such record exists for this entity."
matches on "not in the context" alone, without needing an exact match.

Lenient is not the same as silent. The accepted markers are a fixed,
documented, unit-tested list (`test_abstain.py::test_is_abstention_*`), not
an LLM-judged fuzzy call -- a score from this function is exactly as
reproducible as the exact-match recall scoring it sits next to. Extending
the list means adding a marker here AND a case to
`test_abstain.py::test_is_abstention_accepts_documented_phrasings` in the
same change; a marker with no test is not "documented and unit-tested" per
issue #32's acceptance criterion.

The list stays deliberately narrow:

  - Bare "no" is excluded: too likely to appear inside a real hedge
    ("no, it was last serviced...") or a real value, and adding it would
    make a wrong-but-confident answer that happens to start with "no"
    silently score as a correct abstention instead of a wrong answer.
  - "I don't know" / "I do not know" IS included, on the reasoning that
    the system prompt tells the model to answer only from the document, so
    there is nowhere else a truthful answer could come from other than
    "I looked and it is not there" -- in THIS probe's framing, "I don't
    know" and "not in the document" are the same claim. That reasoning is
    specific to this probe's prompt and would not transfer to a
    general-purpose abstention detector.
"""
import dataclasses
import random

from haystack import ATTRIBUTES, entity_name

# The exact phrase recall.py instructs the model to use. Scoring itself
# never requires this exact string back (is_abstention() is lenient, per
# the module docstring) -- it is exported so the prompt text and this
# module's own documentation cannot drift apart from each other.
INSTRUCTED_PHRASE = 'not in context'

KIND_GUARANTEED_ABSENT = 'guaranteed_absent'
KIND_NEAR_MISS_SAME_ENTITY = 'near_miss_same_entity'
KIND_NEAR_MISS_SAME_ATTRIBUTE = 'near_miss_same_attribute'

ALL_KINDS = (KIND_GUARANTEED_ABSENT, KIND_NEAR_MISS_SAME_ENTITY, KIND_NEAR_MISS_SAME_ATTRIBUTE)

# Every marker is matched as a normalized SUBSTRING of the model's answer
# (see _normalize) -- keep in sync with test_abstain.py's phrasing test;
# see the module docstring for what was deliberately left out and why.
ABSTENTION_MARKERS = (
    'not in context',
    'not in the context',
    'not stated',
    'not mentioned',
    'not provided',
    'not given',
    'no information',
    'not found',
    'not specified',
    'not present',
    'no record',
    'no such record',
    'no such entry',
    'does not appear',
    'does not mention',
    'cannot find',
    'cant find',
    'unknown',
    'n a',  # normalized form of "n/a" / "N.A." -- see _normalize
    'i dont know',  # normalized form of "I don't know"
    'i do not know',
)


def _normalize(s):
    """Lowercase, drop apostrophes outright (so "can't"/"don't" collapse to
    "cant"/"dont" instead of splitting into two words), replace every other
    non-alphanumeric run with a single space, strip. Punctuation-insensitive
    on purpose: "n/a", "N.A.", and "n a" must all normalize to the same
    "n a" that ABSTENTION_MARKERS carries pre-normalized, or the substring
    check below would need to special-case punctuation itself instead of
    just comparing normalized text."""
    out = []
    prev_space = False
    for ch in s.lower():
        if ch == "'":
            continue
        if ch.isalnum():
            out.append(ch)
            prev_space = False
        elif not prev_space:
            out.append(' ')
            prev_space = True
    return ''.join(out).strip()


def is_abstention(value):
    """True if `value` reads as "the document does not state this", per
    the fixed, documented ABSTENTION_MARKERS list above. A non-string
    (including None, for "no answer given at all") is never an abstention
    by this function -- callers that want to treat a missing answer the
    same as an explicit one make that choice themselves, deliberately,
    because the right choice differs between a present-fact question
    (missing = false_abstention, see recall.py) and an absent-fact question
    (missing = correct_abstention)."""
    if not isinstance(value, str):
        return False
    normalized = _normalize(value)
    return any(marker in normalized for marker in ABSTENTION_MARKERS)


@dataclasses.dataclass(frozen=True)
class AbstentionQuestion:
    entity: str
    attribute: str
    kind: str  # one of ALL_KINDS


def _fresh_entity(rng, entities_in_doc, max_attempts=1000):
    # The name pools give roughly 10^5-10^6 distinct names each (see
    # haystack.py's _entity_name docstring), so colliding with the modest
    # number of entities in one haystack is astronomically unlikely -- but
    # "unlikely" is not "impossible", and a silent infinite loop on the
    # unlucky run is worse than a loud, immediate failure on it.
    for _ in range(max_attempts):
        pool = 'target' if rng.random() < 0.5 else 'filler'
        entity = entity_name(rng, pool)
        if entity not in entities_in_doc:
            return entity
    raise AssertionError(
        f'could not find a name outside the haystack in {max_attempts} attempts -- '
        'name pool exhausted, or entities_in_doc is unexpectedly large')


def build_abstention_questions(haystack, seed, num_each=2):
    """Build `3 * num_each` abstention questions against `haystack` (a
    `haystack.Haystack`), `num_each` of each kind in `ALL_KINDS`. Every
    returned question is checked against `haystack.pairs` before being
    returned (asserted, not assumed) -- that check, and `test_abstain.py`'s
    tests of it, are what makes "absence is guaranteed by construction, and
    a test checks it" true rather than aspirational.

    `seed` drives a RNG kept separate from the one that built `haystack`:
    `generate_haystack` does not expose its internal rng, and should not --
    consuming more of it here would make the haystack TEXT depend on how
    many abstention questions were requested, breaking its own "same seed
    -> byte-identical text" guarantee. Callers that want reproducible
    questions pass a seed derived from the same depth seed as the haystack
    (recall.py offsets `_depth_seed`'s result so the two streams, though
    both deterministic, are not identical to each other)."""
    rng = random.Random(seed)
    entities_in_doc = {e for e, _a in haystack.pairs}
    attrs_in_doc = sorted({a for _e, a in haystack.pairs})
    attrs_by_entity = {}
    for e, a in haystack.pairs:
        attrs_by_entity.setdefault(e, set()).add(a)
    entities_with_spare_attribute = [
        e for e in sorted(entities_in_doc) if len(attrs_by_entity[e]) < len(ATTRIBUTES)]

    questions = []

    for _ in range(num_each):
        entity = _fresh_entity(rng, entities_in_doc)
        attribute = ATTRIBUTES[rng.randrange(len(ATTRIBUTES))]
        assert (entity, attribute) not in haystack.pairs, (
            f'{(entity, attribute)!r} unexpectedly present -- guaranteed_absent broken')
        questions.append(AbstentionQuestion(entity, attribute, KIND_GUARANTEED_ABSENT))

    for _ in range(num_each):
        if not entities_with_spare_attribute:
            break  # every entity in this (tiny) haystack already has every attribute
        entity = rng.choice(entities_with_spare_attribute)
        candidates = [a for a in ATTRIBUTES if a not in attrs_by_entity[entity]]
        attribute = rng.choice(candidates)
        assert (entity, attribute) not in haystack.pairs, (
            f'{(entity, attribute)!r} unexpectedly present -- near_miss_same_entity broken')
        questions.append(AbstentionQuestion(entity, attribute, KIND_NEAR_MISS_SAME_ENTITY))

    for _ in range(num_each):
        attribute = (rng.choice(attrs_in_doc) if attrs_in_doc
                     else ATTRIBUTES[rng.randrange(len(ATTRIBUTES))])
        entity = _fresh_entity(rng, entities_in_doc)
        assert (entity, attribute) not in haystack.pairs, (
            f'{(entity, attribute)!r} unexpectedly present -- near_miss_same_attribute broken')
        questions.append(AbstentionQuestion(entity, attribute, KIND_NEAR_MISS_SAME_ATTRIBUTE))

    return questions
