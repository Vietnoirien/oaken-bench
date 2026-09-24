#!/usr/bin/env python3
"""Seeded synthetic-fact haystack generator, for the T0.5 recall battery
(issue #31) and the abstention battery built on top of it (issue #32,
`scripts/abstain.py` + `scripts/recall.py`'s abstention pass).

`ctxprobe.sh` checks whether a context of a given size *loads*. Nothing in
this repo checks whether the model can find anything inside it once loaded.
This module builds the fixture that lets `recall.py` (and, later, #32's
abstention battery) ask that question: a long document of fictional
"operations ledger" records, most of them filler, a handful planted at
controlled positions and recorded so the caller knows the ground truth.

## Why fictional, not `SPEC.md`

Two independent reasons, not one:

  - **Contamination.** Reusing `SPEC.md`, the seed task or the held-out
    suite here would publish those assets a second time in plaintext (the
    same mistake CANARY.md section 3 already documents for
    `toolbattery.py`'s probes) -- and this module is *less* protected than
    that, since a haystack has to be legible to score, not just present.
  - **Answering from memory instead of from context.** A model that has
    seen `SPEC.md` (in training data, or in a training-time exposure this
    repo cannot rule out) could answer a `SPEC.md`-derived recall question
    correctly without reading a single character of the haystack it was
    given, which would silently turn a long-context recall probe into a
    memorisation probe. Invented entities, invented attribute values and a
    fresh seed every run close that door: there is nowhere else the answer
    could have come from.

## Interfaces #32 (abstention) is written against

Per issue #31's design note, three things are deliberately first-class,
not incidental:

  - `Haystack.text` -- the document itself.
  - `Haystack.facts` -- the planted, QUERIED facts: `(entity, attribute,
    value, depth_position)` each, in the order they were planted.
  - `Haystack.pairs` -- every `(entity, attribute)` pair that appears
    ANYWHERE in the document, filler included. #32 needs this to build
    two kinds of question `facts` alone cannot support: a
    **guaranteed-absent** question (an (entity, attribute) pair that is
    NOT in `pairs` at all -- the correct answer is "not in context", and
    checking against `pairs` is how #32 proves the pair is truly absent
    rather than accidentally present in some filler sentence), and a
    **near-miss** distractor (an entity that exists with a DIFFERENT
    attribute, or an attribute that exists on a DIFFERENT entity --
    plausible-looking but still absent, which `pairs` also makes checkable).

## Determinism

`generate_haystack(seed, ...)` with no `count_tokens_fn` is fully
deterministic: one `random.Random(seed)` stream, no wall-clock, no set
iteration order (sets are used for membership only; the ordered pieces
-- filler sentences, planted facts -- are plain lists). Same seed, same
`target_tokens`, same byte-identical text, forever, on any machine.

Passing a `count_tokens_fn` (recall.py's `/tokenize`-backed one) keeps that
guarantee CONDITIONAL on the function itself being deterministic for a
given text -- true of any real tokenizer, but means two different servers'
tokenizers are not obliged to agree on the reported `token_count`, only on
the (identically generated) `text`. See `generate_haystack`'s docstring.

## Sizing: one `/tokenize` call, not a fit loop

`target_tokens` is hit approximately via `DEFAULT_CHARS_PER_TOKEN` (a
documented, not measured, ~4 characters/token estimate for English prose --
BPE tokenizers common to llama.cpp-served models land close to this for
plain text; it will be off for code, numbers-heavy text or non-English
text, neither of which this generator produces). When a `count_tokens_fn`
is supplied the ACTUAL count is measured once, after generation, and
reported alongside the estimate -- this module does not loop
generate-measure-adjust to converge on an exact token count, because that
would cost one `/tokenize` round trip per iteration on top of the one
`recall.py` already budgets per depth, for a benchmark whose acceptance
criterion is "depths ... stop cleanly at the served context size", not
"land on an exact token count". Which method actually sized this haystack
is always recorded in `token_count_source`, per issue #31's design note.
"""
import dataclasses
import random

# Two DISJOINT name pools -- filler entities are drawn from one, planted
# (queried) facts from the other -- so a planted fact's entity can never
# collide with a filler sentence's entity by construction. That matters:
# a collision would let a filler sentence silently restate (or contradict)
# a planted fact's value, which would make the "expected" value ambiguous
# without either side's generation code ever seeing the other's choice.
_FILLER_PREFIXES = [
    'Sable', 'Thorne', 'Amber', 'Coral', 'Quartz', 'Ember', 'Basalt', 'Cobalt',
    'Flint', 'Ivory', 'Umbra', 'Violet', 'Onyx', 'Ash', 'Birch', 'Cedar',
    'Dusk', 'Elm', 'Granite', 'Hollow',
]
_FILLER_SUFFIXES = [
    'Vault', 'Node', 'Cache', 'Relay', 'Beacon', 'Anchor', 'Warren', 'Spire',
    'Ledger', 'Cistern', 'Foundry', 'Archive', 'Bastion', 'Outpost', 'Conduit',
    'Chamber', 'Yard', 'Depot', 'Wharf', 'Silo',
]
_TARGET_PREFIXES = [
    'Meridian', 'Kestrel', 'Marrow', 'Nimbus', 'Petrel', 'Talon', 'Ferro',
    'Grove', 'Quill', 'Basil',
]
_TARGET_SUFFIXES = [
    'Reach', 'Hollowmere', 'Keep', 'Crucible', 'Waypoint', 'Sanctum',
    'Enclave', 'Threshold', 'Annex', 'Terminus',
]

# Abstract, fictional "record" attributes -- ops-log flavoured so the
# sentences read naturally, none of it tied to any real-world scheme.
_ATTRIBUTES = [
    'custodian', 'clearance code', 'activation phrase', 'storage tier',
    'maintenance interval', 'power source', 'backup frequency',
    'access tier', 'serial designation', 'failsafe contact',
]

_VALUE_WORDS = [
    'Kestrel', 'Marlin', 'Opal', 'Basalt', 'Quill', 'Ferro', 'Nimbus',
    'Talon', 'Grove', 'Petrel', 'Cobalt', 'Ember', 'Warble', 'Ashen',
    'Umbral', 'Coral', 'Sable', 'Thorne', 'Briar', 'Flax',
]

_TEMPLATES = [
    "The {entity} logs its {attribute} as {value}.",
    "According to the ledger, {entity}'s {attribute} is {value}.",
    "Field notes record that {entity} has a {attribute} of {value}.",
    "{entity} was registered with a {attribute} reading {value}.",
    "An inspector confirmed {entity}'s {attribute}: {value}.",
    "Maintenance records list {entity}'s {attribute} as {value}.",
]

# A fixed, plainly-worded disclaimer -- part of the generated text itself
# (every haystack starts with it), not just documentation: it tells the
# model in-context that this is invented material, which reduces the
# chance a model treats a coincidentally real-sounding name as a cue to
# answer from outside knowledge instead of from what is actually written.
PREAMBLE = (
    'The following is a fictional operations ledger, generated for a '
    'context-recall test. Every entity name, code and value in it is '
    'invented and does not refer to any real person, place, organisation '
    'or system. Answer only from what is written below.'
)

# Documented, not measured (see module docstring): a common rough estimate
# for English prose under a BPE tokenizer. recall.py's --chars-per-token
# flag overrides this; whichever value was actually used is recorded in
# the returned Haystack so a reader never has to guess.
DEFAULT_CHARS_PER_TOKEN = 4.0

TOKEN_SOURCE_SERVER = 'server_tokenize'
TOKEN_SOURCE_ESTIMATE = 'chars_per_token_estimate'

# Public re-export -- abstain.py (issue #32) needs the attribute vocabulary
# to pick a plausible-but-absent attribute for a near-miss question, without
# reaching into a name prefixed `_` (this module's own convention for
# "generation internals, not part of the interface").
ATTRIBUTES = tuple(_ATTRIBUTES)


@dataclasses.dataclass(frozen=True)
class PlantedFact:
    """One queried fact. `depth_position` is a fraction in [0, 1] of the
    way through `Haystack.text` -- position, not just presence, is the
    point of a recall-at-depth probe. `char_offset` is the same position
    in characters; `approx_token_offset` scales it into the haystack's own
    token_count, so it inherits that count's source (server or estimate)
    rather than assuming its own separate chars-per-token conversion."""
    entity: str
    attribute: str
    value: str
    depth_position: float
    char_offset: int
    approx_token_offset: int


@dataclasses.dataclass(frozen=True)
class Haystack:
    text: str
    facts: tuple  # tuple[PlantedFact, ...], in planting order
    pairs: frozenset  # frozenset[(entity, attribute)] -- every pair in the text
    seed: int
    target_tokens: int
    token_count: int
    token_count_source: str
    chars_per_token: float


def _entity_name(rng, prefixes, suffixes):
    # A 4-digit suffix over a 20x10 (or 10x10) prefix/suffix grid gives
    # each pool roughly 10^5-10^6 distinct names -- collisions within one
    # haystack are possible but harmless (see _build_filler's dedupe) since
    # this is drawing from ONE rng stream per haystack, not across seeds.
    return f'{rng.choice(prefixes)}-{rng.choice(suffixes)}-{rng.randrange(1000, 9999)}'


def entity_name(rng, pool='target'):
    """Public wrapper around `_entity_name`, for abstain.py (issue #32):
    it needs FRESH entity names in the same dashed-prefix-suffix-digits
    shape the haystack text uses, drawn from a caller-supplied `rng` --
    never from a haystack's own generation stream. Sharing that stream
    would make the haystack TEXT depend on how many abstention questions
    were later requested, breaking `generate_haystack`'s "same seed ->
    byte-identical text" guarantee (see the module docstring)."""
    prefixes, suffixes = ((_TARGET_PREFIXES, _TARGET_SUFFIXES) if pool == 'target'
                           else (_FILLER_PREFIXES, _FILLER_SUFFIXES))
    return _entity_name(rng, prefixes, suffixes)


def _value(rng):
    return f'{rng.choice(_VALUE_WORDS)}-{rng.randrange(100, 999)}'


def _sentence(rng, entity, attribute, value):
    return _TEMPLATES[rng.randrange(len(_TEMPLATES))].format(
        entity=entity, attribute=attribute, value=value)


def _build_filler(rng, target_chars, preamble_len):
    """Generate filler (entity, attribute, value, sentence) records until
    the running character count reaches target_chars. Returns
    (records, pairs) where pairs is the set of every (entity, attribute)
    seen, filler included -- the set #32 needs to build guaranteed-absent
    and near-miss questions."""
    records = []
    pairs = set()
    running = preamble_len
    while running < target_chars:
        entity = _entity_name(rng, _FILLER_PREFIXES, _FILLER_SUFFIXES)
        attribute = _ATTRIBUTES[rng.randrange(len(_ATTRIBUTES))]
        value = _value(rng)
        sentence = _sentence(rng, entity, attribute, value)
        records.append({'entity': entity, 'attribute': attribute, 'value': value,
                         'sentence': sentence, 'is_fact': False})
        pairs.add((entity, attribute))
        running += len(sentence) + 1  # +1 for the separator that will join it
    return records, pairs


def _build_target_facts(rng, num_facts):
    """Planted, queried facts -- drawn from the DISJOINT target name pool
    (see module docstring). Distinct entities within one haystack: two
    planted facts sharing an entity would be a legitimate scenario in
    principle, but would also make `pairs` alone insufficient to tell
    #32 apart's "near miss" (same entity, different attribute) from a
    second genuine planted fact, so this generator keeps them distinct."""
    used = set()
    facts = []
    for _ in range(num_facts):
        while True:
            entity = _entity_name(rng, _TARGET_PREFIXES, _TARGET_SUFFIXES)
            if entity not in used:
                used.add(entity)
                break
        attribute = _ATTRIBUTES[rng.randrange(len(_ATTRIBUTES))]
        value = _value(rng)
        facts.append({'entity': entity, 'attribute': attribute, 'value': value})
    return facts


def generate_haystack(seed, target_tokens, *, num_facts=3,
                       position_fractions=(0.1, 0.5, 0.9),
                       chars_per_token=DEFAULT_CHARS_PER_TOKEN,
                       count_tokens_fn=None):
    """Build one haystack. `position_fractions` says where in the document
    (as a fraction of sentence count, not yet of final character count --
    the actual `depth_position` recorded per fact is measured AFTER
    assembly, from real character offsets) each of `num_facts` planted
    facts lands; must be the same length as `num_facts`.

    `count_tokens_fn`, when given, is called ONCE with the final assembled
    text and must return an int token count (see module docstring on why
    this is a single measurement, not a fit loop). Any exception it raises
    is treated the same as not passing one: fall back to the
    `chars_per_token` estimate, `token_count_source` records which
    happened. A tokenizer failing must not be fatal to generating a usable
    haystack -- the caller (recall.py) still has a document and a
    documented estimate of its size.
    """
    if len(position_fractions) != num_facts:
        raise ValueError(
            f'position_fractions has {len(position_fractions)} entries, '
            f'need {num_facts} to match num_facts')

    rng = random.Random(seed)
    target_chars = int(target_tokens * chars_per_token)
    preamble_len = len(PREAMBLE)

    filler_records, pairs = _build_filler(rng, target_chars, preamble_len)
    target_facts = _build_target_facts(rng, num_facts)
    for fact in target_facts:
        pairs.add((fact['entity'], fact['attribute']))

    # Insert planted-fact sentences into the filler sequence at their
    # fractional position, largest fraction first: inserting front-to-back
    # would shift every later fraction's intended index by however many
    # facts were already inserted ahead of it.
    order = sorted(range(num_facts), key=lambda i: position_fractions[i], reverse=True)
    for i in order:
        fact = target_facts[i]
        idx = min(int(position_fractions[i] * len(filler_records)), len(filler_records))
        record = {'entity': fact['entity'], 'attribute': fact['attribute'],
                   'value': fact['value'],
                   'sentence': _sentence(rng, fact['entity'], fact['attribute'], fact['value']),
                   'is_fact': True, 'fact_index': i}
        filler_records.insert(idx, record)

    # Assemble the real text and capture each fact's TRUE character offset
    # as we go -- computing it from a formula (sentence lengths + assumed
    # separator widths) would be one more place to get the paragraph-break
    # arithmetic wrong; walking the actual join is exact by construction.
    chunks = [PREAMBLE]
    offset = len(PREAMBLE)
    fact_offsets = {}
    for i, record in enumerate(filler_records):
        sep = '\n\n' if (i % 6 == 0) else ' '
        chunks.append(sep)
        offset += len(sep)
        chunks.append(record['sentence'])
        if record['is_fact']:
            fact_offsets[record['fact_index']] = offset
        offset += len(record['sentence'])
    text = ''.join(chunks)
    total_chars = len(text)

    if count_tokens_fn is not None:
        try:
            token_count = int(count_tokens_fn(text))
            token_count_source = TOKEN_SOURCE_SERVER
        except Exception:  # noqa: BLE001 -- a tokenizer failure falls back, never raises
            token_count = round(total_chars / chars_per_token)
            token_count_source = TOKEN_SOURCE_ESTIMATE
    else:
        token_count = round(total_chars / chars_per_token)
        token_count_source = TOKEN_SOURCE_ESTIMATE

    facts = []
    for i in range(num_facts):
        fact = target_facts[i]
        char_offset = fact_offsets[i]
        depth_position = char_offset / total_chars if total_chars else 0.0
        approx_token_offset = round(depth_position * token_count)
        facts.append(PlantedFact(
            entity=fact['entity'], attribute=fact['attribute'], value=fact['value'],
            depth_position=depth_position, char_offset=char_offset,
            approx_token_offset=approx_token_offset))

    return Haystack(
        text=text, facts=tuple(facts), pairs=frozenset(pairs), seed=seed,
        target_tokens=target_tokens, token_count=token_count,
        token_count_source=token_count_source, chars_per_token=chars_per_token)
