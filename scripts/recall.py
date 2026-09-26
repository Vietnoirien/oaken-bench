#!/usr/bin/env python3
"""Recall-at-depth and abstention battery for direct mode (issue #31's
recall pass, issue #32's abstention pass, together T0.5 of #26's ladder).

  ./scripts/recall.py --model your-model.gguf [--base-url http://host:port/v1]

`ctxprobe.sh` measures whether a context of a given size *loads*. This
measures whether the model can find anything inside it once loaded, and --
just as important -- whether it can tell the difference between "found"
and "not there": a seeded, fictional-fact haystack (`haystack.py`) is built
at each of a few context depths, a handful of facts are planted in it at
controlled positions, and a handful of guaranteed-absent (entity,
attribute) pairs (`abstain.py`) are built against the SAME haystack. One
tool call per depth asks about both kinds of pair together, and each
answer is classified: a planted fact scores correct answer / wrong answer
/ false abstention (over-abstaining must be visible, or a model that
always says "not in context" would score perfectly on recall alone by
never being scored on it); an absent pair scores correct abstention /
invented answer. See `abstain.py`'s module docstring for the three kinds
of absent question and the accepted abstention phrasings.

## Why one pass, not two separate CLIs

Issue #32 leaves this as a choice, preferring "share the haystack and
prefill cost" if reasonable -- it is. Recall and abstention both need: a
haystack built at this depth, and one cold-prefill request against it
(`measure_depth`'s call A). Running abstention as its own script would mean
building a SECOND haystack per depth (or threading the first one across
process boundaries, which is worse) and paying prefill twice for a probe
whose whole point is asking two kinds of question about the same document
a model has already read once. Folding it into `run_depth` costs one
extra, cheap, local step (`build_abstention_questions`) and reuses
everything else: the haystack, the two-call cache-trap pair, and the
`report_recall` tool itself, which already reports one value per requested
pair regardless of whether that pair turns out to be present or absent.

Talks to the OpenAI-compatible endpoint DIRECTLY, no harness in between --
same rationale as `toolbattery.py` (issue #8): a harness's own context
management and retry logic sits between the model and the wire and would
mask exactly the long-context behaviour this battery exists to measure.

## The cache trap (read before touching the timing code)

llama.cpp keeps a request's prompt warm per slot: a second request whose
prompt is a byte-identical prefix of an earlier one can reuse the KV cache
computed for that prefix, so prefill on a repeat query against the SAME
haystack can look almost free. Two different, deliberate choices follow
from that, and they point in opposite directions on purpose:

  - ACROSS depths, an honest cold-prefill measurement is wanted every
    time, so every depth gets its OWN haystack: `_depth_seed()` folds the
    depth into the seed passed to `generate_haystack()`, so depth 16384's
    text is not a prefix (or any other substring relationship) of depth
    32768's -- there is nothing for the cache to reuse across depths.
  - WITHIN one depth, `measure_depth()` EXPLOITS the cache instead of
    fighting it, always, whether or not the server sends `timings`: call
    A (`max_tokens=1`) forces one full, uncached prefill against a fresh
    prompt; call B immediately after, the identical prompt with the real
    `max_tokens`, almost certainly hits the now-warm cache, so its own
    prefill is close to free and its wall time is close to pure decode.
    This only works because A and B share the identical prompt -- the
    opposite of the across-depths case, which is why the two are handled
    differently instead of both sending `cache_prompt: false` (which
    `direct.chat()` has no parameter for, deliberately -- see its module
    docstring on keeping that seam narrow; the two-call trick needed no
    change to it).

Two calls per depth are paid every time, not only when timings are
missing: call A only ever generates one token, which is not enough to
score, so call B (the real, scored request) is unavoidable regardless of
what A reports. Once both calls exist, using A's own prefill and B's own
decode is strictly better than trying to reuse a single "primary" call for
both jobs, because no single call can give a clean reading of both --
whichever call is cache-warm has a near-zero, uninformative prefill
number. Per metric, llama.cpp's own `timings` object
(`prompt_per_second` from A, `predicted_per_second` from B) is used when
the response carries one; wall-clock (the call's elapsed time against its
own `usage` token counts) is the fallback, independently, per metric --
so a server that reports one but not the other still gets a real number
for both.

## Plaintext-probe caveat

Like `toolbattery.py`, this battery's prompts (though not their planted
facts, which are fresh every seed) are committed in plaintext -- see
CANARY.md section 3, extended in section 3b for this module. The artefact
carries a `caveat` field pointing back at it; `print_report()` prints it
next to the overall score so it cannot be read without the number it
qualifies.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from abstain import ALL_KINDS, INSTRUCTED_PHRASE, build_abstention_questions, is_abstention  # noqa: E402
from direct import ServerError, api_key_from_env, chat, server_reachable  # noqa: E402
from direct_env import VramSampler, build_environment, server_root_url  # noqa: E402
from haystack import DEFAULT_CHARS_PER_TOKEN, generate_haystack  # noqa: E402
import server_config  # noqa: E402

SCHEMA_VERSION = 1

DEFAULT_BASE_URL = os.environ.get('OAKEN_RECALL_BASE_URL', 'http://172.17.0.1:8080/v1')

# Token depths, e.g. the issue's own "4k/16k/32k/64k/128k" example.
DEFAULT_DEPTHS_TOKENS = (4096, 16384, 32768, 65536, 131072)

# Near the start, the middle, and near the end -- the standard
# needle-in-a-haystack spread. Not 0.0/1.0 exactly: a fact literally at
# the first or last sentence is a different (easier) probe than one
# actually buried in the middle of the context.
POSITION_FRACTIONS = (0.1, 0.5, 0.9)

# Reserved out of the served context size for the question, the tool
# schema, and the response itself. `default_generation_settings.n_ctx`
# from /props is the TOTAL slot size; a haystack sized right up to it would
# leave no room for any of that and the request would fail for a reason
# that has nothing to do with recall.
CONTEXT_RESERVE_TOKENS = 1024

TEXT_PREVIEW_MAX = 400


def _auth_headers(api_key):
    return {'Authorization': f'Bearer {api_key}'} if api_key else {}


def count_tokens_via_server(root_url, text, timeout=30, api_key=None):
    """POST <root_url>/tokenize. Returns an int token count. Raises on any
    transport/parse failure -- the caller (haystack.generate_haystack)
    already treats any exception here as "fall back to the chars-per-token
    estimate", so this does not need its own fallback path."""
    payload = json.dumps({'content': text}).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    headers.update(_auth_headers(api_key))
    req = urllib.request.Request(root_url.rstrip('/') + '/tokenize', data=payload,
                                  headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    data = json.loads(body)
    tokens = data.get('tokens')
    if not isinstance(tokens, list):
        raise ValueError('tokenize response had no "tokens" list')
    return len(tokens)


def probe_tokenize(root_url, timeout=10, api_key=None):
    """One cheap call to decide whether `/tokenize` is usable at all,
    before committing to it for every depth's (potentially 128k-character)
    haystack. Returns (available, reason)."""
    try:
        count_tokens_via_server(root_url, 'hello world', timeout=timeout, api_key=api_key)
        return True, None
    except Exception as e:  # noqa: BLE001 -- any failure just means "use the estimate"
        return False, str(e)


def fetch_served_context_tokens(root_url, timeout=5):
    """`default_generation_settings.n_ctx` from /props -- the context size
    actually allocated (server_config.py's docstring: "declared vs
    served"). Returns (n_ctx_or_None, reason_when_None)."""
    props = server_config.fetch_props(root_url, timeout=timeout)
    if 'error' in props:
        return None, props['error']
    n_ctx = (props.get('default_generation_settings') or {}).get('n_ctx')
    if not isinstance(n_ctx, int):
        return None, 'props response had no default_generation_settings.n_ctx'
    return n_ctx, None


def _depth_seed(seed, depth_tokens):
    # Arithmetic combination, not hash(): hash() is salted per-process
    # (PYTHONHASHSEED) unless disabled, which would silently break the
    # "same seed -> byte-identical haystack" guarantee generate_haystack()
    # promises -- every depth needs its OWN deterministic seed (see the
    # module docstring's cache-trap section), not a shared one.
    return seed * 1_000_003 + depth_tokens


# ---------------------------------------------------------------------------
# The recall tool: one call reports every planted fact in a depth at once
# ---------------------------------------------------------------------------

RECALL_TOOL = {
    'type': 'function',
    'function': {
        'name': 'report_recall',
        'description': 'Report the value recalled for each requested (entity, attribute) pair.',
        'parameters': {
            'type': 'object',
            'properties': {
                'answers': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'entity': {'type': 'string'},
                            'attribute': {'type': 'string'},
                            'value': {'type': 'string',
                                      'description': ('The value recorded for this entity/attribute, '
                                                       'exactly as written in the ledger -- OR exactly '
                                                       f'{INSTRUCTED_PHRASE!r} if the ledger does not '
                                                       'state a value for this pair.')},
                        },
                        'required': ['entity', 'attribute', 'value'],
                    },
                },
            },
            'required': ['answers'],
        },
    },
}

# Some of the pairs asked about are planted facts (issue #31); some are
# guaranteed absent (issue #32, abstain.py) -- the model is never told
# which pair is which, that distinction is the whole point of the probe.
# INSTRUCTED_PHRASE (not is_abstention()'s full lenient marker list, which
# stays server-side scoring logic the model is never shown) is the one
# phrase actually put in front of the model, so a compliant model has a
# single, unambiguous thing to say.
SYSTEM_PROMPT = (
    'You will be given a long document followed by a list of (entity, attribute) pairs. '
    'For EACH pair, report the value exactly as written in the document. Do not use '
    'outside knowledge, and do not guess. Some of the pairs listed are NOT stated '
    f'anywhere in the document -- for those, answer exactly {INSTRUCTED_PHRASE!r} as the '
    'value, nothing else. Call report_recall exactly once, with one answer per pair listed.'
)


def _build_question(facts, abstention_questions=()):
    lines = [f"- entity {f.entity!r}, attribute {f.attribute!r}" for f in facts]
    lines += [f"- entity {q.entity!r}, attribute {q.attribute!r}" for q in abstention_questions]
    return ('Using report_recall, report the value for each of the following '
            f'(or {INSTRUCTED_PHRASE!r} if a pair is not stated in the document):\n'
            + '\n'.join(lines))


def _first_tool_call(message):
    for tc in (message.get('tool_calls') or []):
        if not isinstance(tc, dict):
            continue
        fn = tc.get('function') or {}
        if fn.get('name') != 'report_recall':
            continue
        raw = fn.get('arguments')
        args = raw if isinstance(raw, dict) else None
        if args is None and isinstance(raw, str):
            try:
                decoded = json.loads(raw)
            except (ValueError, TypeError):
                decoded = None
            if isinstance(decoded, dict):
                args = decoded
        return args
    return None


def _answers_by_pair(tool_args):
    """Shared by `_score_recall` (present facts) and `_score_abstention`
    (absent pairs) -- both read the same `report_recall` call, so parsing
    its `answers` array into a `(entity, attribute) -> value` lookup lives
    once, not twice."""
    answers = []
    if isinstance(tool_args, dict):
        raw_answers = tool_args.get('answers')
        if isinstance(raw_answers, list):
            answers = [a for a in raw_answers if isinstance(a, dict)]
    by_pair = {}
    for a in answers:
        key = (a.get('entity'), a.get('attribute'))
        by_pair[key] = a.get('value')
    return by_pair


# Classification labels. Snake_case, matching this codebase's other
# enum-like string values (timing's 'server_timings'/'wall_clock',
# haystack's 'server_tokenize'/'chars_per_token_estimate'), not the
# camelCase used for JSON *field names* elsewhere in the artefact.
CORRECT_ANSWER = 'correct_answer'
WRONG_ANSWER = 'wrong_answer'
FALSE_ABSTENTION = 'false_abstention'
CORRECT_ABSTENTION = 'correct_abstention'
INVENTED_ANSWER = 'invented_answer'


def _score_recall(facts, tool_args):
    """Exact-match scoring for PRESENT (planted) facts, trimmed of
    surrounding whitespace (formatting noise, not a recall failure) but
    otherwise case-sensitive -- the planted values are short generated
    codes (e.g. "Kestrel-482") where a case change is itself a plausible
    failure mode worth catching, not something to paper over.

    Every result also carries a `classification`, issue #32's addition:
    a present fact answered wrong is `wrong_answer`; a present fact the
    model claims is absent (an explicit abstention phrase, OR no answer
    for that pair at all -- see the note below) is `false_abstention`,
    the over-abstaining direction #32 asks to make visible. Without this,
    a model that always says "not in context" would score 0% recall but
    nothing would flag it as different from a model that recalls nothing
    because it genuinely cannot find anything.

    A MISSING answer for a present fact is scored `false_abstention`, not
    a separate "no answer" bucket: this pair WAS in the document, so
    silence withholds the same value an explicit "not in context" would,
    and a caller cannot tell "forgot to answer" from "decided not to"
    from the tool call alone -- treating them alike is the conservative
    reading, not a guess at intent."""
    by_pair = _answers_by_pair(tool_args)

    results = []
    for fact in facts:
        key = (fact.entity, fact.attribute)
        recalled = by_pair.get(key)
        expected = fact.value
        correct = (isinstance(recalled, str) and recalled.strip() == expected.strip())
        if correct:
            classification = CORRECT_ANSWER
        elif recalled is None or is_abstention(recalled):
            classification = FALSE_ABSTENTION
        else:
            classification = WRONG_ANSWER
        results.append({
            'entity': fact.entity, 'attribute': fact.attribute, 'expectedValue': expected,
            'recalledValue': recalled, 'correct': correct, 'classification': classification,
            'depthPositionFraction': round(fact.depth_position, 4),
            'charOffset': fact.char_offset, 'approxTokenOffset': fact.approx_token_offset,
        })
    return results


def _score_abstention(questions, tool_args):
    """Classify each ABSENT (guaranteed-absent or near-miss) question,
    issue #32's other half. A missing answer scores `correct_abstention`
    here -- the mirror image of `_score_recall`'s choice above, and for
    the same reason stated differently: this pair was never in the
    document, so silence and an explicit "not in context" both withhold
    the SAME (nonexistent) value equally correctly. Only a non-abstention
    string is `invented_answer` -- the model produced a specific value for
    something that was never there to produce a value for."""
    by_pair = _answers_by_pair(tool_args)

    results = []
    for q in questions:
        key = (q.entity, q.attribute)
        recalled = by_pair.get(key)
        classification = (CORRECT_ABSTENTION if (recalled is None or is_abstention(recalled))
                           else INVENTED_ANSWER)
        results.append({
            'entity': q.entity, 'attribute': q.attribute, 'kind': q.kind,
            'recalledValue': recalled, 'classification': classification,
        })
    return results


def _count_classifications(results, labels):
    counts = {label: 0 for label in labels}
    for r in results:
        counts[r['classification']] += 1
    counts['total'] = len(results)
    return counts


def _present_question_counts(fact_results):
    return _count_classifications(fact_results, (CORRECT_ANSWER, WRONG_ANSWER, FALSE_ABSTENTION))


def _abstention_counts(abstention_results):
    overall = _count_classifications(abstention_results, (CORRECT_ABSTENTION, INVENTED_ANSWER))
    by_kind = {}
    for kind in ALL_KINDS:
        kind_results = [r for r in abstention_results if r['kind'] == kind]
        by_kind[kind] = _count_classifications(kind_results, (CORRECT_ABSTENTION, INVENTED_ANSWER))
    return {'overall': overall, 'byKind': by_kind}


def _merge_counts(dicts, labels):
    """Sum a list of `_count_classifications`-shaped dicts, field by
    field -- used to roll depth-level counts up into the battery's
    `overall` block without re-deriving them from raw results."""
    merged = {label: 0 for label in labels}
    merged['total'] = 0
    for d in dicts:
        for label in labels:
            merged[label] += d.get(label, 0)
        merged['total'] += d.get('total', 0)
    return merged


def _preview(text, n=TEXT_PREVIEW_MAX):
    if not isinstance(text, str):
        return text
    return text if len(text) <= n else text[:n] + '...'


# ---------------------------------------------------------------------------
# Timing: always a cache-exploiting pair (see module docstring); per-metric
# server `timings` when the response carries one, wall clock otherwise
# ---------------------------------------------------------------------------

def _prompt_per_second(resp):
    timings = resp.get('timings')
    return timings.get('prompt_per_second') if isinstance(timings, dict) else None


def _predicted_per_second(resp):
    timings = resp.get('timings')
    return timings.get('predicted_per_second') if isinstance(timings, dict) else None


def measure_depth(base_url, model, messages, max_tokens, timeout, api_key):
    """Call A (`max_tokens=1`, cold prefill) then call B (the real, scored
    request, warm cache) against the IDENTICAL prompt -- see the module
    docstring's "cache trap" section for why this pairing, and why it is
    safe only WITHIN one depth. Returns (response_to_score, timing_dict).
    `response_to_score` is call B's -- call A only ever produces one token,
    never enough to score."""
    t0 = time.monotonic()
    resp_a = chat(base_url, model, messages, tools=[RECALL_TOOL], max_tokens=1,
                  timeout=timeout, api_key=api_key)
    prefill_wall_s = time.monotonic() - t0

    t1 = time.monotonic()
    resp_b = chat(base_url, model, messages, tools=[RECALL_TOOL], max_tokens=max_tokens,
                  timeout=timeout, api_key=api_key)
    decode_wall_s = time.monotonic() - t1

    usage_a = resp_a.get('usage') or {}
    usage_b = resp_b.get('usage') or {}
    prompt_tokens = usage_a.get('prompt_tokens') or usage_b.get('prompt_tokens')
    completion_tokens = usage_b.get('completion_tokens')

    server_prefill = _prompt_per_second(resp_a)
    if server_prefill is not None:
        prefill_tps, prefill_source = server_prefill, 'server_timings'
    else:
        prefill_tps = (prompt_tokens / prefill_wall_s) if prompt_tokens and prefill_wall_s > 0 else None
        prefill_source = 'wall_clock'

    server_decode = _predicted_per_second(resp_b)
    if server_decode is not None:
        decode_tps, decode_source = server_decode, 'server_timings'
    else:
        decode_tps = (completion_tokens / decode_wall_s) if completion_tokens and decode_wall_s > 0 else None
        decode_source = 'wall_clock'

    timing = {
        'prefillTokPerSec': prefill_tps, 'prefillSource': prefill_source,
        'decodeTokPerSec': decode_tps, 'decodeSource': decode_source,
        'promptTokens': prompt_tokens, 'completionTokens': completion_tokens,
    }
    return resp_b, timing


# ---------------------------------------------------------------------------
# One depth
# ---------------------------------------------------------------------------

def run_depth(base_url, root_url, model, depth_tokens, seed, *, max_tokens, timeout,
              api_key, chars_per_token, count_tokens_fn, num_facts=3, num_abstention_each=2):
    haystack = generate_haystack(
        seed=_depth_seed(seed, depth_tokens), target_tokens=depth_tokens,
        num_facts=num_facts, position_fractions=POSITION_FRACTIONS[:num_facts],
        chars_per_token=chars_per_token, count_tokens_fn=count_tokens_fn)

    # +1 offsets the abstention rng from the haystack's own seed stream --
    # see abstain.py's build_abstention_questions docstring on why this
    # must be a SEPARATE stream, not a shared one.
    abstention_questions = build_abstention_questions(
        haystack, seed=_depth_seed(seed, depth_tokens) + 1, num_each=num_abstention_each)

    question = _build_question(haystack.facts, abstention_questions)
    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': haystack.text + '\n\n' + question},
    ]

    record = {
        'depthTokens': depth_tokens,
        'haystack': {
            'seed': haystack.seed, 'tokenCount': haystack.token_count,
            'tokenCountSource': haystack.token_count_source,
            'charsPerToken': haystack.chars_per_token, 'textChars': len(haystack.text),
        },
    }

    try:
        resp, timing = measure_depth(base_url, model, messages, max_tokens, timeout, api_key)
    except ServerError as e:
        record['transportError'] = str(e)
        return record

    choice = (resp.get('choices') or [{}])[0]
    message = choice.get('message') or {}
    record['finishReason'] = choice.get('finish_reason')
    record['timing'] = timing
    record['rawAnswerPreview'] = _preview(message.get('content'))
    # A reasoning model can spend the entire output budget before calling the
    # reporting tool. Counting its empty response as six abstentions turns a
    # budget limit into a model failure, and even a truncated tool call may
    # omit answers. Leave this depth out of the score.
    if record['finishReason'] == 'length':
        record['outputTruncated'] = True
        return record
    tool_args = _first_tool_call(message)
    results = _score_recall(haystack.facts, tool_args)
    correct = sum(1 for r in results if r['correct'])
    abstention_results = _score_abstention(abstention_questions, tool_args)

    record['facts'] = results
    record['recallScore'] = round(correct / len(results), 4) if results else None
    record['recalled'] = correct
    record['total'] = len(results)
    record['presentQuestionCounts'] = _present_question_counts(results)
    record['abstention'] = {
        'questions': abstention_results,
        'counts': _abstention_counts(abstention_results),
    }
    return record


# ---------------------------------------------------------------------------
# Full battery
# ---------------------------------------------------------------------------

def run_battery(base_url, model, depths_tokens, *, seed, max_tokens=1024, timeout=120,
                 api_key=None, chars_per_token=None, use_tokenize=True, num_abstention_each=2):
    root_url = server_root_url(base_url)

    effective_chars_per_token = chars_per_token or DEFAULT_CHARS_PER_TOKEN

    served_ctx, ctx_reason = fetch_served_context_tokens(root_url)
    if served_ctx is not None:
        max_haystack_tokens = served_ctx - CONTEXT_RESERVE_TOKENS
    else:
        max_haystack_tokens = None

    if use_tokenize:
        tokenize_available, tokenize_reason = probe_tokenize(root_url, api_key=api_key)
    else:
        tokenize_available, tokenize_reason = False, 'disabled via --no-tokenize'
    count_tokens_fn = (
        (lambda text: count_tokens_via_server(root_url, text, api_key=api_key))
        if tokenize_available else None)

    depths_run = []
    depths_skipped = []
    for depth_tokens in depths_tokens:
        if max_haystack_tokens is not None and depth_tokens > max_haystack_tokens:
            depths_skipped.append({
                'depthTokens': depth_tokens,
                'reason': (f'exceeds served context size ({served_ctx} tokens) minus '
                           f'{CONTEXT_RESERVE_TOKENS}-token reserve for the question/response'),
            })
            continue
        record = run_depth(base_url, root_url, model, depth_tokens, seed, max_tokens=max_tokens,
                            timeout=timeout, api_key=api_key,
                            chars_per_token=effective_chars_per_token,
                            count_tokens_fn=count_tokens_fn,
                            num_abstention_each=num_abstention_each)
        depths_run.append(record)

    attempted = [d for d in depths_run if d.get('recallScore') is not None]
    overall_score = (round(sum(d['recallScore'] for d in attempted) / len(attempted), 4)
                      if attempted else None)
    # Roll each depth's own present/abstention counts up into one battery
    # total -- issue #32's "counts per depth in the artefact" is met by
    # each depth record above; this is the convenience sum next to it, not
    # a replacement for the per-depth breakdown.
    overall_present_counts = _merge_counts(
        [d['presentQuestionCounts'] for d in attempted], (CORRECT_ANSWER, WRONG_ANSWER, FALSE_ABSTENTION))
    overall_abstention_counts = _merge_counts(
        [d['abstention']['counts']['overall'] for d in attempted], (CORRECT_ABSTENTION, INVENTED_ANSWER))

    return {
        'schemaVersion': SCHEMA_VERSION,
        'label': f"recall-{_slug(model)}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        'model': model,
        'baseUrl': base_url,
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'seed': seed,
        'servedContextTokens': served_ctx,
        'servedContextSource': 'props' if served_ctx is not None else 'unknown',
        'servedContextUnknownReason': ctx_reason,
        'tokenizeAvailable': tokenize_available,
        'tokenizeUnavailableReason': None if tokenize_available else tokenize_reason,
        'depths': depths_run,
        'depthsSkipped': depths_skipped,
        'overall': {
            'recallScore': overall_score,
            'depthsAttempted': len(attempted),
            'depthsRequested': len(depths_tokens),
            'depthsSkipped': len(depths_skipped),
            'depthsFailed': len(depths_run) - len(attempted),
            'presentQuestionCounts': overall_present_counts,
            'abstentionCounts': overall_abstention_counts,
        },
        'caveat': ('This battery\'s prompts and haystack/abstention generators are committed '
                   'in plaintext, like toolbattery.py -- see CANARY.md section 3 (extended '
                   'in 3b for this module). Do not treat a rising score over time as proof '
                   'of improving recall or abstention without ruling out this exact battery '
                   'having been scraped; the fresh-seed-per-run design limits, but does not '
                   'eliminate, that risk for the probe SHAPE even though the planted facts '
                   'and absent pairs themselves change every run.'),
    }


def _slug(s):
    return re.sub(r'[^A-Za-z0-9._-]+', '-', s).strip('-') or 'model'


def parse_depth(s):
    """Accepts plain integers or a `k` suffix (e.g. "128k" -> 131072)."""
    s = s.strip()
    if s[-1:].lower() == 'k':
        return int(s[:-1]) * 1024
    return int(s)


def print_report(report):
    print(f"=== recall: {report['model']} @ {report['baseUrl']} (seed={report['seed']}) ===")
    print(f"  served context: {report['servedContextTokens']} tokens "
          f"({report['servedContextSource']})"
          + ('' if report['servedContextTokens'] is not None
             else f" -- {report['servedContextUnknownReason']}"))
    print(f"  token sizing: {'server /tokenize' if report['tokenizeAvailable'] else 'chars-per-token estimate'}"
          + ('' if report['tokenizeAvailable'] else f" ({report['tokenizeUnavailableReason']})"))
    for d in report['depths']:
        if 'transportError' in d:
            print(f"  depth {d['depthTokens']:>7}  ERROR: {d['transportError']}")
            continue
        if d.get('outputTruncated'):
            print(f"  depth {d['depthTokens']:>7}  INCOMPLETE: output hit the token limit")
            continue
        pct = '  --' if d['recallScore'] is None else f"  ({d['recallScore'] * 100:.0f}%)"
        timing = d['timing']
        prefill = timing.get('prefillTokPerSec')
        decode = timing.get('decodeTokPerSec')
        prefill_s = f"{prefill:.1f}" if isinstance(prefill, (int, float)) else 'n/a'
        decode_s = f"{decode:.1f}" if isinstance(decode, (int, float)) else 'n/a'
        print(f"  depth {d['depthTokens']:>7}  {d['recalled']}/{d['total']}{pct}"
              f"  prefill={prefill_s}tok/s [{timing.get('prefillSource')}]"
              f"  decode={decode_s}tok/s [{timing.get('decodeSource')}]")
        for f in d['facts']:
            mark = 'PASS' if f['correct'] else 'FAIL'
            print(f"    [{mark}] {f['entity']} / {f['attribute']} @ depth {f['depthPositionFraction']:.2f}"
                  f": expected {f['expectedValue']!r}, got {f['recalledValue']!r} [{f['classification']}]")
        abst = d.get('abstention')
        if abst is not None:
            ac = abst['counts']['overall']
            print(f"    abstention: {ac[CORRECT_ABSTENTION]}/{ac['total']} correct abstention, "
                  f"{ac[INVENTED_ANSWER]} invented")
            for q in abst['questions']:
                mark = 'PASS' if q['classification'] == CORRECT_ABSTENTION else 'FAIL'
                print(f"    [{mark}] {q['entity']} / {q['attribute']} ({q['kind']})"
                      f": got {q['recalledValue']!r} [{q['classification']}]")
    for skip in report['depthsSkipped']:
        print(f"  depth {skip['depthTokens']:>7}  SKIPPED: {skip['reason']}")
    o = report['overall']
    score_s = '--' if o['recallScore'] is None else f"{o['recallScore'] * 100:.0f}%"
    print(f"  OVERALL recall {score_s}  ({o['depthsAttempted']}/{o['depthsRequested']} depths attempted, "
          f"{o['depthsSkipped']} skipped, {o['depthsFailed']} failed)")
    pc = o['presentQuestionCounts']
    ac = o['abstentionCounts']
    print(f"  OVERALL present-question answers: {pc[CORRECT_ANSWER]} correct, {pc[WRONG_ANSWER]} wrong, "
          f"{pc[FALSE_ABSTENTION]} false abstention (of {pc['total']})")
    print(f"  OVERALL abstention: {ac[CORRECT_ABSTENTION]} correct abstention, "
          f"{ac[INVENTED_ANSWER]} invented answer (of {ac['total']})")
    print(f"  caveat: {report['caveat']}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--base-url', default=DEFAULT_BASE_URL,
                    help=f'OpenAI-compatible base URL (default: {DEFAULT_BASE_URL})')
    p.add_argument('--model', required=True, help='model id, as advertised by /v1/models')
    p.add_argument('--depths', default=None,
                    help='comma-separated token depths, e.g. "4k,16k,32k,64k,128k" '
                         f'(default: {",".join(str(d) for d in DEFAULT_DEPTHS_TOKENS)})')
    p.add_argument('--seed', type=int, default=20260924,
                    help='haystack seed -- same seed + depths reproduces the same facts')
    p.add_argument('--max-tokens', type=int, default=1024)
    p.add_argument('--timeout', type=int, default=180, help='per-request timeout, seconds')
    p.add_argument('--chars-per-token', type=float, default=None,
                    help='override the fallback chars-per-token estimate (default: haystack.py\'s)')
    p.add_argument('--no-tokenize', action='store_true',
                    help='skip the /tokenize probe and always use the chars-per-token estimate')
    p.add_argument('--abstention-per-kind', type=int, default=2,
                    help='number of abstention questions per kind (guaranteed_absent, '
                         'near_miss_same_entity, near_miss_same_attribute) at each depth (default: 2)')
    p.add_argument('--out', default=None,
                    help='output path for the JSON artefact (default: recall-results/<label>.json)')
    args = p.parse_args(argv)

    depths = ([parse_depth(d) for d in args.depths.split(',')] if args.depths
              else list(DEFAULT_DEPTHS_TOKENS))

    api_key = api_key_from_env()

    if not server_reachable(args.base_url, api_key=api_key):
        print(f'ERROR: no server reachable at {args.base_url} -- refusing to run', file=sys.stderr)
        return 2

    with VramSampler() as sampler:
        report = run_battery(args.base_url, args.model, depths, seed=args.seed,
                              max_tokens=args.max_tokens, timeout=args.timeout, api_key=api_key,
                              chars_per_token=args.chars_per_token, use_tokenize=not args.no_tokenize,
                              num_abstention_each=args.abstention_per_kind)
    report['environment'] = build_environment(args.base_url, vram_peak=sampler.peak_mib())
    print_report(report)

    out_path = args.out
    if out_path is None:
        out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'recall-results')
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, report['label'] + '.json')
    else:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or '.', exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f'\nwrote {out_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
