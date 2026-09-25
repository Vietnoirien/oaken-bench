#!/usr/bin/env python3
"""A short tool-calling battery for screening small models (issue #8).

  ./scripts/toolbattery.py --model your-model.gguf [--base-url http://host:port/v1]

Talks to an OpenAI-compatible `/v1/chat/completions` endpoint DIRECTLY --
not through pi or dsh. That is a deliberate design choice, not an oversight:

  (a) the point is minutes per model, not half an hour; harness startup and
      a 30-minute task dominate the long-horizon benchmark's cost, and a
      screening tool that pays that cost has defeated its own purpose.
  (b) the battery wants each dimension to fail INDEPENDENTLY. A harness's
      own retry, compaction and context-management logic sits between the
      model and the wire and will mask or repair exactly the failures this
      battery exists to catch.
  (c) MODELS.md section 4 records that on Gemma 4 12B QAT, neither
      `--reasoning-format` setting serves both pi and dsh at once (pi reads
      `reasoning_content`; dsh does not strip the inline
      `<|channel>thought` markers `--reasoning-format none` produces, and
      chokes on them). Routing a *model* screening tool through a harness
      would confound the model's tool-calling behaviour with that
      harness/template interaction -- exactly the confound this tool is
      built to avoid. Screening the model means talking to the model.

Six dimensions, scored independently, matching the issue (plus issue #30):

  - schemaAdherence     required params present, types correct, no invented params
  - toolSelection       right tool out of a set that includes plausible decoys
  - multiStepDependency call 2 must use the value call 1's (simulated) result returned
  - errorRecovery       call 1 is made to fail; does call 2 repeat it verbatim or adapt
  - refusal             no tool applies; does the model call one anyway (false positive)
  - shortChains         3-5 dependent calls in a row; how deep does the model get before
                        the thread breaks (issue #30)

Reuse, not reinvention: error recovery's "repeated verbatim" check is built
on `events.call_metrics()` and its digest machinery (`_canonical_digest`,
`_safe_tool_name`) -- the same functions the harness benchmark uses to
detect a repeated call in a pi/dsh trace, so "repeated verbatim" means the
same thing here as it does in `events-summary.json`. This module does not
grow a second scoring path (see issue #8's Dependency note).

Stdlib only at runtime: `urllib.request`, not `requests` (README: no
runtime deps).

The HTTP client (`chat()`, `server_reachable()`, `ServerError`) lives in
`direct.py`, not here (issue #27) -- it is the seam later direct-mode
batteries (T0.5, issues #31/#32) import instead of copying. This module
owns everything specific to tool-call screening: the five dimensions,
their scoring, and the CLI.

On issue #8's Dependency note, which names #1, #2 *and* #4: #1/#2's metrics
are reused (`call_metrics`, `_canonical_digest`). #4's are deliberately not,
and the reason is structural rather than an oversight. `parseErrors` and the
`malformed_tool_calls` outcome are derived from `stderr.log` -- a file the
container's entrypoint produces by redirecting a *harness* process's stderr.
This battery runs no harness and no container, so there is no such stream to
scan. The equivalent signal here is already first-class instead: an
unparseable `arguments` string is recorded per call as `parse_error` by
parse_message() and scored under schemaAdherence, which is a strictly better
place for it than a log grep.

schemaAdherence's probes (#15): SCHEMA_CASES mixes flat argument objects
(`{"city": ...}`) with two compound-schema probes built on TOOL_EDIT_FILE --
a required top-level scalar (`path`) alongside a required nested
array-of-objects (`edits[]`), lifted from pi's real `edit` tool schema
rather than hand-written. This is the shape that let a Gemma run score
schemaAdherence 3/3 while failing 17/17 real `edit` calls, all by omitting
`path` while filling in the nested structure correctly -- a failure mode no
one-level-deep probe can express. `dimensions.schemaAdherence.byTool` reports
pass/fail per probed tool for the same reason `errorsByTool` exists in
events-summary.json: an aggregate score can hide one catastrophic tool.
**The four `toolbattery-results/*.json` artefacts committed before this
change were scored without these probes and without `byTool`** -- they
answer "passed the flat-schema battery", not "passes schema adherence" (the
Gemma one is the exact 3/3 the issue is about). Per-tool granularity beyond
schemaAdherence, and any SCHEMA_VERSION bump to mark this formally, is
issue #29's scope, not this change's.

On raw model output in the artefact: unlike `edit`/`write` arguments in the
real harness traces (agent-written solution code against a held-out spec --
see events.py's module docstring and CANARY.md), what these probes elicit
is a handful of short, generic values (a city name, an ISO timestamp, a
made-up customer id) answering fixed, publicly-inventable prompts about a
fictional weather/calendar/customer-support toolset. There is no held-out
asset here to leak, so the case records below keep the actual arguments
(needed to show the schema/selection/dependency verdicts are correct) as
well as the digest, rather than digest-only. Model free-text answers are
truncated for the same reason a large log is truncated -- volume, not
sensitivity.

Issue #29: per-call classification and pseudo-tool-call detection
-------------------------------------------------------------------
Before this change, a dimension recorded one pass/fail bit per case. That
bit cannot say WHERE a call went wrong -- a model that emits `<tool_call>`
XML instead of a structured call, one that calls the right tool with a
truncated argument string, and one that calls the wrong tool outright all
land on the same `passed: false`, indistinguishable in the artefact. Every
real tool call each case produces is now additionally classified with
`classify_call()` into four CUMULATIVE levels -- a call can only reach
level N having cleared every level below it -- plus a floor and a "no call
made" bucket:

  0. noCall          -- the model answered in text (or, for schemaAdherence/
                        toolSelection/multiStepDependency, silently skipped
                        the turn). Not a failure of any level; there is
                        nothing to classify.
  1. notWellFormed   -- a call WAS made, but `arguments` failed to decode
                        as a JSON object (`parse_message()`'s `parse_error`,
                        e.g. truncated or malformed JSON). This is the
                        `malformed_tool_calls` equivalent the module
                        docstring above promises: the model tried to call
                        something and the wire-level shape is broken.
  2. wellFormed      -- `arguments` decoded to a JSON object, but either the
                        named tool isn't one this case offered (schema
                        unknown, so it cannot be judged further) or the
                        object fails ITS OWN tool's schema (missing
                        required params, invented params, wrong types) --
                        evaluated against whichever tool was actually
                        called, not the one the case wanted.
  3. schemaValid     -- well-formed AND passes that schema check, but the
                        tool called is not the one the case expected (a
                        decoy was taken, or -- for refusal cases, where NO
                        tool is ever the right one -- any tool call at all
                        tops out here).
  4. rightTool       -- schema-valid AND the tool called matches what the
                        case expected, but the dimension-specific "right
                        args" check for that particular call did not pass.
  5. rightArgs       -- rightTool AND the call is substantively correct for
                        what the case is testing. What that means is
                        dimension-specific, spelled out on classify_call().

`_by_tool_classification()` folds every call from every dimension into one
per-tool table (issue #29 asks this to generalise past #15's
`schemaAdherence.byTool` alone) -- so ONE catastrophic tool across the
*whole* battery is visible, not just within one dimension.

`detect_pseudo_tool_calls()` covers the other half of #29: a model that
never emits a structured `tool_calls` entry at all, and instead writes the
call as text in `content` -- invisible to every scorer above, which only
ever look at `message['tool_calls']`. See that function's docstring for the
six shapes covered and why "prose that merely mentions a tool name" must
not be one of them.

v1 vs v2 (issue #29): the four `toolbattery-results/*.json` artefacts
committed before this change (`schemaVersion: 1`, or `1` before that, the
plain flat-schema battery from issue #8) have neither per-call
classification nor pseudo-tool-call detection -- they were never rescored,
their meaning is unchanged, and per house style (AGENTS.md) that is
documented here rather than silently redefined. Read a `schemaVersion: 1`
artefact as "passed the v1 battery" (dimension pass/fail, plus #15's
`schemaAdherence.byTool` for anything after that commit); read a
`schemaVersion: 2` artefact as "passed the v2 battery": v1's dimensions
PLUS a per-call classification level for every call made and a
pseudo-tool-call count for every case's free text.

Issue #30: short dependent chains of 3-5 calls
-------------------------------------------------------------------
`multiStepDependency` above chains exactly two calls. Whether a model keeps
threading a value through call 3, 4 and 5 is a different question -- local
models observed on this repo tend to lose the thread well before then --
and that is what `shortChains` (`CHAIN_SCENARIOS`, `run_short_chains()`)
measures: four scenarios of length 3, 3, 4 and 5, each a strict sequence
where call N+1 needs a value that ONLY call N's simulated result carries.

Unguessable values: every id/token/code threaded through a chain is built
by `_seeded_id()` from a fixed seed string, not a small hand-picked example
like multiStepDependency's `cus_48291`. A hand-picked id is memorable and
short enough that a model could plausibly echo something LOOKING right by
pattern-matching the domain ("a customer id starts with cus_"); a
16-hex-digit tail seeded off a private string cannot be produced any way
other than reading it back out of the simulated tool result this module
injects as a `role: tool` message -- so a correct call 3 is evidence the
model actually carried call 2's result forward, not evidence it guessed the
shape of a plausible-looking id.

Depth, not just pass/fail: each case record carries `chainLength`,
`depthReached` (how many links were correct before the first break, 0 if
the very first call already went wrong), `brokenAtStep` and
`brokenAtLevel` -- the `classify_call()` level (#29) of the call that broke
the chain, e.g. `schemaValid` for a decoy taken, `notWellFormed` for
truncated JSON. A chain that completes all N links has `brokenAtStep`/
`brokenAtLevel` both None. The dimension summary additionally carries a
`depth` block (`_chain_depth_summary()`): total links reached over total
links offered across all chains, and a count of which level each broken
chain died at -- `_summarize()`'s own pass/fail (did the WHOLE chain
complete) is still computed too, for the pass/total number every other
dimension reports, but it collapses exactly the information `depth` exists
to keep.

A step only counts as reached when its call reaches `classify_call()`'s
`rightArgs` ceiling -- not merely "right tool called". A well-formed,
right-tool call with a stale, invented or truncated dependency value does
not advance the chain; it IS the break, scored at whatever level it
actually reached (typically `rightTool`, since the tool name matched but
the value didn't).

Two decisions this module makes, both recorded here because they change
what "depth reached" means and nothing enforces them by construction:

  - **Parallel calls.** If a turn returns more than one `tool_calls` entry,
    only the FIRST is used to judge and advance the chain -- the others are
    still recorded (tagged `'parallel': True`) and still counted in
    `report['callClassification']`, but do not themselves advance depth or
    break the chain. A later entry in the SAME turn cannot legitimately
    carry a dependency value the model has not been handed back yet (the
    tool result for the call still being decided is not in the transcript
    when the model emits it), so crediting one would let a lucky/parallel
    guess at a later step count as depth reached without ever threading
    the intermediate value.
  - **Skipping ahead.** A model that calls a LATER step's tool directly
    (e.g. `activate_device` on turn 1, guessing at the device id and
    activation code) is judged against THIS step's `expected_tool_name`,
    which is the very next tool in the chain, not the one it guessed --
    `classify_call()` then caps that call at `schemaValid` at best (right
    tool never matches), so it cannot reach `rightArgs` and the chain
    breaks at depth 0. Because every dependency value is unguessable
    (`_seeded_id()`), a model also cannot luck into the right VALUE even if
    it happened to call the right tool early -- there is no path to credit
    for a call made before the value it needs has ever been revealed.
"""
import argparse
import json
import os
import random
import re
import sys
from collections import namedtuple
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from direct import ServerError, api_key_from_env, chat, server_reachable  # noqa: E402
from direct_env import VramSampler, build_environment  # noqa: E402
from events import Call, _canonical_digest, _safe_tool_name, call_metrics  # noqa: E402

# v2 (issue #29): per-call classification levels + pseudo-tool-call
# detection, on top of v1's per-dimension pass/fail and (post-#15)
# schemaAdherence.byTool. See the module docstring's "v1 vs v2" section --
# the four committed toolbattery-results/*.json artefacts predate this and
# are NOT rescored; they stay schemaVersion 1 and mean what they always
# meant.
SCHEMA_VERSION = 2

DEFAULT_BASE_URL = os.environ.get('OAKEN_TOOLBATTERY_BASE_URL', 'http://172.17.0.1:8080/v1')

# Gemma 4 12B QAT served with `--reasoning-format none` (MODELS.md section
# 4) inlines its reasoning in `content` as `<|channel>thought ... <channel|>`
# ahead of (or instead of, on a tool-call turn) the final answer text. Note
# the markers are NOT symmetric: opening is `<|channel>thought`, closing is
# `<channel|>` -- confirmed against a live response, not assumed from the
# docs. Strip it rather than being surprised by it, and rather than storing
# a wall of chain-of-thought in the artefact.
REASONING_PATTERN = re.compile(r'<\|channel>thought\n?(.*?)<channel\|>', re.DOTALL)

TEXT_PREVIEW_MAX = 400


def strip_reasoning(content):
    """Split an assistant `content` string into (reasoning, final_text).
    Returns (None, content) unchanged if no reasoning marker is present --
    covers both a model that never emits one and a server run without
    `--reasoning-format none`."""
    if not isinstance(content, str) or not content:
        return None, content
    m = REASONING_PATTERN.search(content)
    if not m:
        return None, content
    reasoning = m.group(1).strip()
    final = (content[:m.start()] + content[m.end():]).strip()
    return reasoning, final


def parse_message(message):
    """Normalize one OpenAI-shaped `message` into
    {reasoning_present, text, tool_calls: [{id, name, args, args_raw, parse_error}]}.
    `args` is a dict if the arguments parsed as a JSON object, else None --
    a parse failure is itself a schema-adherence finding, not something to
    hide by raising."""
    content = message.get('content')
    reasoning, final_text = strip_reasoning(content)
    calls = []
    for tc in (message.get('tool_calls') or []):
        if not isinstance(tc, dict):
            continue
        fn = tc.get('function') or {}
        name = fn.get('name')
        raw = fn.get('arguments')
        args, parse_error = None, None
        if isinstance(raw, dict):
            args = raw
        elif isinstance(raw, str):
            try:
                decoded = json.loads(raw)
            except (ValueError, TypeError) as e:
                parse_error = str(e)
            else:
                args = decoded if isinstance(decoded, dict) else None
                if args is None:
                    parse_error = 'arguments did not decode to a JSON object'
        calls.append({'id': tc.get('id'), 'name': name, 'args': args,
                       'args_raw': raw, 'parse_error': parse_error})
    return {'reasoning_present': reasoning is not None, 'text': final_text, 'tool_calls': calls}


def _to_call(parsed_call, turn):
    return Call(tool=_safe_tool_name(parsed_call['name']), call_id=parsed_call.get('id'),
                args=parsed_call['args'], args_digest=_canonical_digest(parsed_call['args']),
                ok=None, turn=turn, step=None, started_ms=None, ended_ms=None, error_text=None)


def _preview(text, n=TEXT_PREVIEW_MAX):
    if not isinstance(text, str):
        return text
    return text if len(text) <= n else text[:n] + '...'


# ---------------------------------------------------------------------------
# Pure scoring logic (no network) -- unit-testable in isolation
# ---------------------------------------------------------------------------

_JSON_TYPE_MAP = {'string': str, 'array': list, 'object': dict}


def _type_ok(value, expected_type):
    if expected_type == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == 'boolean':
        return isinstance(value, bool)
    py = _JSON_TYPE_MAP.get(expected_type)
    if py is None:
        return True  # unrecognised schema type: not this checker's job to fail on it
    return isinstance(value, py)


def _args_schema_valid(tool_schema, args):
    """required present, types correct, no invented -- schema-only check
    against ONE tool's schema, independent of which tool name was actually
    called. Split out from score_schema_adherence() (which also enforces
    the name match) because classify_call() needs to grade a call's
    arguments against whatever tool it named, even when that is not the
    tool the case expected."""
    fn = tool_schema['function']
    params = fn.get('parameters') or {}
    props = params.get('properties') or {}
    required = set(params.get('required') or [])
    if args is None:
        return False, ['arguments did not parse as a JSON object']
    reasons = []
    missing = sorted(required - set(args))
    if missing:
        reasons.append(f'missing required params: {missing}')
    invented = sorted(set(args) - set(props))
    if invented:
        reasons.append(f'invented params not in schema: {invented}')
    type_errors = [f'{k}: expected {props[k].get("type")}, got {type(v).__name__}'
                    for k, v in args.items() if k in props and not _type_ok(v, props[k].get('type'))]
    if type_errors:
        reasons.append('type mismatches: ' + '; '.join(type_errors))
    return (not reasons), reasons


def score_schema_adherence(tool_schema, parsed_call):
    """required params present, types correct, no invented parameters."""
    fn = tool_schema['function']
    if parsed_call is None:
        return False, ['no tool call made']
    if parsed_call['name'] != fn['name']:
        return False, [f"wrong tool called: {parsed_call['name']!r} (expected {fn['name']!r})"]
    args = parsed_call['args']
    if args is None:
        return False, [f"arguments did not parse as a JSON object: {parsed_call.get('parse_error')}"]
    return _args_schema_valid(tool_schema, args)


def score_tool_selection(expected_tool_name, parsed_call):
    """right tool selected out of a set with plausible decoys."""
    if parsed_call is None:
        return False, ['no tool call made']
    if parsed_call['name'] != expected_tool_name:
        return False, [f"called {parsed_call['name']!r} instead of {expected_tool_name!r} (decoy taken)"]
    return True, []


def score_dependency(step2_call, expected_key, expected_value):
    """call 2 must carry the value call 1's (simulated) tool result handed back,
    not an invented one."""
    if step2_call is None:
        return False, ['no second call made']
    args = step2_call['args'] or {}
    if expected_key not in args:
        return False, [f'second call missing {expected_key!r}: got keys {sorted(args)}']
    if args[expected_key] != expected_value:
        return False, [f'{expected_key} mismatch: got {args[expected_key]!r}, expected {expected_value!r} '
                        '(the value the simulated first result returned)']
    return True, []


def score_error_recovery(call1_parsed, call2_parsed):
    """First call fails; does the model repeat the exact same call, or adapt?
    Built directly on events.call_metrics()'s repeat-run detection, per
    issue #8's instruction to reuse the existing scoring path rather than
    grow a second one."""
    # A live run turned this up: at a tight max_tokens, the inline reasoning
    # channel (MODELS.md §4) can eat the budget and truncate the FIRST
    # call's own argument JSON before it ever reaches the injected error --
    # `args` comes back None with a parse error, not from anything the
    # error message did. A call with unparseable args digests differently
    # from any well-formed retry pretty much by construction, so this
    # dimension would score that as "adapted" for the wrong reason. Still
    # scored as a pass (parse errors are a schemaAdherence-shaped finding,
    # not this dimension's), but flagged so the case record does not imply
    # more than it showed.
    malformed_first = call1_parsed is not None and call1_parsed.get('args') is None

    if call2_parsed is None or call2_parsed.get('name') is None:
        # No retry at all. This used to score as a pass, on the reasoning
        # that not retrying is not repeating verbatim -- but that made a
        # dimension named errorRecovery report 100% on a live run where
        # neither case recovered from anything, which is precisely the kind
        # of number this repo keeps having to withdraw.
        #
        # None is a third outcome: not attempted. _summarize() leaves it out
        # of the denominator rather than counting it either way, the same
        # way call_metrics()'s errorRate excludes its `unknown` bucket. A
        # model that never retries now reports 0 attempted, not 100%.
        return None, ['model did not retry the failing call (stopped or '
                      'answered in text) -- recovery was never exercised']
    c1 = _to_call(call1_parsed, turn=1)
    c2 = _to_call(call2_parsed, turn=2)
    metrics = call_metrics([c1, c2])
    repeated = metrics['longestRepeatRun'] >= 2
    if repeated:
        return False, ['repeated the identical failing call verbatim (same tool + args digest)']
    reasons = ['adapted: different tool and/or different arguments on retry']
    if malformed_first:
        reasons.append(f"caveat: the first call's own arguments failed to parse "
                        f"({call1_parsed.get('parse_error')}), likely truncated by max_tokens -- "
                        "this pass may reflect a well-formed retry rather than genuine adaptation "
                        "to the injected error")
    return True, reasons


_CHAIN_SEED_NAMESPACE = 'oaken-toolbattery-shortChains-v1'


def _seeded_id(qualifier, prefix, length=12):
    """A deterministic but unguessable id (issue #30): `random.Random`
    seeded on a private, fixed string (`_CHAIN_SEED_NAMESPACE` + `qualifier`)
    always produces the same output run to run -- the artefact and this
    module's own tests agree -- but the string itself is a 12-hex-digit
    tail no model can shortcut to by pattern-matching the domain the way it
    could a short hand-picked example like multiStepDependency's
    `cus_48291`. The only way to produce it is to read it back out of the
    simulated tool result `run_short_chains()` hands back after the
    PREVIOUS call in the chain."""
    rng = random.Random(f'{_CHAIN_SEED_NAMESPACE}:{qualifier}')
    return f"{prefix}_{''.join(rng.choice('0123456789abcdef') for _ in range(length))}"


def score_chain_step(call, dependency):
    """Does this chain step's call carry forward the exact value(s) the
    PREVIOUS step's simulated result handed back? `dependency` is None for
    a chain's first step (nothing to depend on yet -- passes automatically,
    same convention as schemaAdherence's `right_args_ok=None` collapse) or
    a dict of `{arg_key: expected_value}` every one of which THIS call's
    arguments must match exactly. More than one key covers a step like
    `activate_device`, which needs both the device id from step 1 AND the
    activation code from step 2 at once -- `score_dependency()` (the
    multiStepDependency dimension's single-key version) cannot express
    that, so this is a distinct function rather than a reuse."""
    if dependency is None:
        return True, []
    if call is None:
        return False, ['no call made']
    args = call.get('args') or {}
    missing = sorted(k for k in dependency if k not in args)
    if missing:
        return False, [f'missing dependency keys: {missing}']
    mismatched = {k: args[k] for k in dependency if args.get(k) != dependency[k]}
    if mismatched:
        return False, [f'dependency value mismatch: {mismatched} '
                        '(expected the value the simulated previous result returned)']
    return True, []


def score_refusal(parsed):
    """No tool applies -- the false-positive direction. Correct behaviour is
    zero tool calls."""
    made = [c['name'] for c in parsed['tool_calls']]
    if made:
        return False, [f'called {made} when no supplied tool applied to the request']
    return True, ['no tool called, as expected']


# ---------------------------------------------------------------------------
# Per-call classification (issue #29): well-formed / schema-valid /
# right tool / right args, cumulative, plus a "no call" floor.
# ---------------------------------------------------------------------------

CALL_LEVEL_NO_CALL = 'noCall'
CALL_LEVEL_NOT_WELL_FORMED = 'notWellFormed'
CALL_LEVEL_WELL_FORMED = 'wellFormed'
CALL_LEVEL_SCHEMA_VALID = 'schemaValid'
CALL_LEVEL_RIGHT_TOOL = 'rightTool'
CALL_LEVEL_RIGHT_ARGS = 'rightArgs'

# Ordered floor-to-ceiling. Used only to render a stable column order in
# print_report()/the artefact's counts -- classify_call() never iterates
# this, it returns one level directly.
CALL_LEVELS = (CALL_LEVEL_NO_CALL, CALL_LEVEL_NOT_WELL_FORMED, CALL_LEVEL_WELL_FORMED,
               CALL_LEVEL_SCHEMA_VALID, CALL_LEVEL_RIGHT_TOOL, CALL_LEVEL_RIGHT_ARGS)


def classify_call(tools_by_name, expected_tool_name, parsed_call, right_args_ok=None):
    """Classify one (attempted) tool call into the four cumulative levels
    issue #29 asks for, plus the `noCall` floor for a turn that made no
    call at all. A call can only reach level N having cleared every level
    below it:

      1. well-formed   -- `parsed_call['args']` is a dict, i.e. `arguments`
                          decoded as JSON to an object. `parse_message()`
                          already records a failure to do that as
                          `parse_error` with `args=None` -- that IS the
                          not-well-formed level, not a separate check here.
      2. schema-valid  -- well-formed AND the arguments satisfy the JSON
                          schema of the tool ACTUALLY named in the call
                          (required params present, correct types, no
                          invented params) -- looked up in `tools_by_name`
                          by the call's own tool name, not the case's
                          expected one. A tool name outside `tools_by_name`
                          (nothing offered by that shape) has no schema to
                          check against, so it caps at well-formed.
      3. right tool    -- schema-valid AND `parsed_call['name']` equals
                          `expected_tool_name`. `expected_tool_name=None`
                          means no tool is ever "right" for this case (the
                          refusal dimension: any call at all is a false
                          positive) -- such a call can reach schema-valid
                          at most.
      4. right args    -- right tool AND `right_args_ok` is not False. What
                          "right args" checks is dimension-specific, passed
                          in already computed by the caller:
                            - schemaAdherence / toolSelection: no further
                              check beyond the schema itself -- there is no
                              single expected VALUE for "book a room called
                              Falcon", only an expected shape. Pass
                              `right_args_ok=None` (the default): once
                              right-tool is reached, right-args follows
                              automatically, so these two levels collapse
                              for these dimensions.
                            - multiStepDependency: `right_args_ok` is
                              score_dependency()'s bool -- the second
                              call's argument at the dependency key equals
                              the value the simulated first result
                              returned, not an invented one.
                            - errorRecovery, the FIRST (failing) call of a
                              pair: no correct value exists yet to adapt
                              to, so pass `right_args_ok=None`, same as
                              schemaAdherence -- it collapses to whether
                              the retry target tool was even named right.
                              The SECOND (retry) call: `right_args_ok` is
                              score_error_recovery()'s bool -- the retry's
                              (tool, args) pair differs from the first
                              call's, i.e. adapted rather than repeated
                              verbatim. Note score_error_recovery() also
                              passes when the retry uses a DIFFERENT tool;
                              classify_call() still requires
                              `expected_tool_name` to match to reach
                              right-tool, so an adaptive-but-different-tool
                              retry is a real, valid pass at the dimension
                              level while capping at schema-valid here --
                              the two are answering different questions
                              (did it recover? vs. did it recover WITH the
                              tool this case is about?).
    """
    if parsed_call is None:
        return CALL_LEVEL_NO_CALL
    if parsed_call.get('args') is None:
        return CALL_LEVEL_NOT_WELL_FORMED
    name = parsed_call.get('name')
    schema = tools_by_name.get(name)
    if schema is None:
        return CALL_LEVEL_WELL_FORMED
    schema_ok, _ = _args_schema_valid(schema, parsed_call['args'])
    if not schema_ok:
        return CALL_LEVEL_WELL_FORMED
    if expected_tool_name is None or name != expected_tool_name:
        return CALL_LEVEL_SCHEMA_VALID
    if right_args_ok is False:
        return CALL_LEVEL_RIGHT_TOOL
    return CALL_LEVEL_RIGHT_ARGS


def _call_record(turn, expected_tool_name, parsed_call, right_args_ok=None, tools_by_name=None):
    """Build the `{turn, tool, expectedTool, level}` dict attached to a case
    record's `calls` list. `tools_by_name` defaults to the full catalog
    (TOOLS_BY_NAME, defined below) so callers in the run_* functions don't
    each have to thread it through."""
    reg = tools_by_name if tools_by_name is not None else TOOLS_BY_NAME
    level = classify_call(reg, expected_tool_name, parsed_call, right_args_ok=right_args_ok)
    return {'turn': turn, 'tool': parsed_call['name'] if parsed_call else None,
            'expectedTool': expected_tool_name, 'level': level}


# ---------------------------------------------------------------------------
# Tool catalog -- fresh, generic, unrelated to SPEC.md/seed/held-out (CANARY.md)
# ---------------------------------------------------------------------------
# A small fictional "ops assistant" surface: weather, currency, a customer
# lookup + order status pair, reminders, meeting rooms. None of it overlaps
# the auto-battler domain the seed task and held-out suite are written
# against, and none of these probe prompts is drawn from either.

def _tool(name, description, properties, required):
    return {'type': 'function', 'function': {
        'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties, 'required': required}}}


TOOL_GET_WEATHER = _tool(
    'get_weather', 'Get the current weather for a city.',
    {'city': {'type': 'string', 'description': 'City name, e.g. "Paris"'},
     'units': {'type': 'string', 'enum': ['celsius', 'fahrenheit']}},
    ['city'])

TOOL_GET_WEATHER_ALERTS = _tool(  # decoy: same domain, wrong operation
    'get_weather_alerts', 'Get active severe-weather alerts for a region.',
    {'region': {'type': 'string'}}, ['region'])

TOOL_CONVERT_CURRENCY = _tool(
    'convert_currency', 'Convert an amount of money from one currency to another.',
    {'amount': {'type': 'number'}, 'from_currency': {'type': 'string'},
     'to_currency': {'type': 'string'}},
    ['amount', 'from_currency', 'to_currency'])

TOOL_GET_EXCHANGE_RATE = _tool(
    'get_exchange_rate', 'Look up the current exchange rate between two currencies.',
    {'from_currency': {'type': 'string'}, 'to_currency': {'type': 'string'}},
    ['from_currency', 'to_currency'])

TOOL_APPLY_RATE = _tool(
    'apply_exchange_rate', 'Apply a previously-looked-up exchange rate to an amount.',
    {'amount': {'type': 'number'}, 'rate': {'type': 'number'}}, ['amount', 'rate'])

TOOL_LOOKUP_CUSTOMER = _tool(
    'lookup_customer', 'Look up a customer record by email address, returning a customer id.',
    {'email': {'type': 'string'}}, ['email'])

TOOL_GET_ORDER_STATUS = _tool(
    'get_order_status', 'Get the status of a customer\'s most recent order by customer id.',
    {'customer_id': {'type': 'string'}}, ['customer_id'])

TOOL_CANCEL_SUBSCRIPTION = _tool(  # decoy: same domain (customer ops), wrong operation
    'cancel_subscription', 'Cancel a customer\'s subscription by customer id.',
    {'customer_id': {'type': 'string'}}, ['customer_id'])

TOOL_CREATE_REMINDER = _tool(
    'create_reminder', 'Create a reminder for the user.',
    {'text': {'type': 'string'}, 'remind_at': {'type': 'string', 'description': 'ISO-8601 datetime'}},
    ['text', 'remind_at'])

TOOL_BOOK_ROOM = _tool(
    'book_meeting_room',
    'Book a meeting room.',
    {'room_name': {'type': 'string'}, 'start_time': {'type': 'string', 'description': 'ISO-8601 datetime'},
     'duration_minutes': {'type': 'integer'}, 'attendees': {'type': 'array', 'items': {'type': 'string'}},
     'recurring': {'type': 'boolean'}},
    ['room_name', 'start_time', 'duration_minutes', 'attendees'])

TOOL_LIST_ROOMS = _tool(  # decoy: adjacent operation
    'list_available_rooms', 'List meeting rooms free in a given building.',
    {'building': {'type': 'string'}}, ['building'])

# Lifted, not hand-written (issue #15): the flat probes above ({"city": ...},
# {"room": ..., "start": ...}) are all one level deep, and every one of them
# missed the live failure that opened #15 -- Gemma 4 12B QAT omitted pi's
# required top-level `path` on 17/17 `edit` calls while filling in the nested
# `edits[]` array correctly. A flat probe cannot express "drops a sibling
# scalar while concentrating on a nested structure" because it has no
# sibling to drop. This schema is pi's real `edit` tool, field-for-field, as
# captured live in a `session`/`toolsAdded` event under
# `~/.cache/oaken-bench/schema-capture-pi/` (`message.toolsAdded[]` on the
# system message, EVENTS.md section 1) -- not reinvented, so the probe
# reproduces the actual shape that broke rather than a guess at one. Renamed
# `edit_file` here only to keep this module's tool names to its own fictional
# ops-assistant domain (CANARY.md); `path` + `edits[]` and both `required`
# lists are unchanged from the capture.
TOOL_EDIT_FILE = _tool(
    'edit_file',
    'Edit an existing text file by replacing literal text. Every edits[].oldText '
    'must match a unique, non-overlapping region of the original file.',
    {'path': {'type': 'string', 'description': 'Path to the file to edit (relative or absolute)'},
     'edits': {'type': 'array', 'description': 'One or more targeted replacements.',
               'items': {'type': 'object', 'required': ['oldText', 'newText'],
                         'properties': {
                             'oldText': {'type': 'string',
                                         'description': 'Exact text for one targeted replacement. Must be '
                                                         'unique in the original file.'},
                             'newText': {'type': 'string', 'description': 'Replacement text for this edit.'},
                         }}}},
    ['path', 'edits'])

# ---------------------------------------------------------------------------
# shortChains (issue #30): four small fictional workflows, each a strict
# sequence of 3-5 calls where call N+1 needs a value only call N's
# simulated result carries. Tools below are grouped by workflow; the
# correct-path tool for each step is paired with 1-2 decoys in
# CHAIN_SCENARIOS further down. None of this overlaps the auto-battler
# domain (CANARY.md), same as every other tool in this module.
# ---------------------------------------------------------------------------

# Workflow: ship an order, then track it.
TOOL_LOOKUP_ORDER_BY_EMAIL = _tool(
    'lookup_order_by_email', "Look up a customer's most recent order by email, returning an order id.",
    {'email': {'type': 'string'}}, ['email'])

TOOL_GET_SHIPPING_LABEL = _tool(
    'get_shipping_label', 'Generate a shipping label for an order, returning a tracking number.',
    {'order_id': {'type': 'string'}}, ['order_id'])

TOOL_GET_TRACKING_STATUS = _tool(
    'get_tracking_status', 'Get the current delivery status for a tracking number.',
    {'tracking_number': {'type': 'string'}}, ['tracking_number'])

TOOL_REROUTE_SHIPMENT = _tool(  # decoy: same arg shape as get_tracking_status, wrong operation
    'reroute_shipment', 'Reroute an in-transit shipment to a different address.',
    {'tracking_number': {'type': 'string'}, 'new_address': {'type': 'string'}},
    ['tracking_number', 'new_address'])

# Workflow: open a support ticket, assign it, get the agent's contact.
TOOL_OPEN_SUPPORT_TICKET = _tool(
    'open_support_ticket', 'Open a new support ticket for an issue, returning a ticket id.',
    {'issue': {'type': 'string'}}, ['issue'])

TOOL_ASSIGN_TICKET_AGENT = _tool(
    'assign_ticket_agent', 'Assign a support ticket to an agent, returning the agent id.',
    {'ticket_id': {'type': 'string'}}, ['ticket_id'])

TOOL_GET_AGENT_CONTACT = _tool(
    'get_agent_contact', "Get a support agent's contact email by agent id.",
    {'agent_id': {'type': 'string'}}, ['agent_id'])

TOOL_CLOSE_SUPPORT_TICKET = _tool(  # decoy: same domain, wrong operation -- this chain never closes the ticket
    'close_support_ticket', 'Close a support ticket by id.',
    {'ticket_id': {'type': 'string'}}, ['ticket_id'])

# Workflow: register a device, activate it (needs BOTH step 1's and step
# 2's results at once), check its status.
TOOL_REGISTER_DEVICE = _tool(
    'register_device', 'Register a new device by name, returning a device id.',
    {'device_name': {'type': 'string'}}, ['device_name'])

TOOL_GENERATE_ACTIVATION_CODE = _tool(
    'generate_activation_code', 'Generate a one-time activation code for a registered device.',
    {'device_id': {'type': 'string'}}, ['device_id'])

TOOL_ACTIVATE_DEVICE = _tool(
    'activate_device',
    'Activate a device using its id and a previously-generated activation code, returning a session token.',
    {'device_id': {'type': 'string'}, 'activation_code': {'type': 'string'}},
    ['device_id', 'activation_code'])

TOOL_GET_DEVICE_STATUS = _tool(
    'get_device_status', 'Get the current status of a device session by session token.',
    {'session_token': {'type': 'string'}}, ['session_token'])

TOOL_LIST_DEVICES = _tool(  # decoy: adjacent operation, never the correct next step
    'list_devices', 'List devices already registered to the account.', {}, [])

TOOL_DEREGISTER_DEVICE = _tool(  # decoy: opposite operation
    'deregister_device', 'Remove a device from the account by device id.',
    {'device_id': {'type': 'string'}}, ['device_id'])

# Workflow: submit an expense, route/notify/wait for approval, release payment.
TOOL_SUBMIT_EXPENSE = _tool(
    'submit_expense', 'Submit an expense for reimbursement, returning an expense id.',
    {'amount': {'type': 'number'}, 'description': {'type': 'string'}}, ['amount', 'description'])

TOOL_ROUTE_FOR_APPROVAL = _tool(
    'route_expense_for_approval', 'Route a submitted expense to an approver, returning the approver id.',
    {'expense_id': {'type': 'string'}}, ['expense_id'])

TOOL_NOTIFY_APPROVER = _tool(
    'notify_approver', 'Notify an approver that an expense is waiting on them, returning a notification id.',
    {'approver_id': {'type': 'string'}}, ['approver_id'])

TOOL_GET_APPROVAL_STATUS = _tool(
    'get_approval_status',
    'Get the approval status for a notification, returning an approval code once approved.',
    {'notification_id': {'type': 'string'}}, ['notification_id'])

TOOL_RELEASE_PAYMENT = _tool(
    'release_payment', "Release payment for an expense using its approval code.",
    {'approval_code': {'type': 'string'}}, ['approval_code'])

TOOL_LIST_PENDING_EXPENSES = _tool(  # decoy: adjacent operation
    'list_pending_expenses', 'List expenses awaiting approval.', {}, [])

TOOL_REJECT_EXPENSE = _tool(  # decoy: opposite outcome, never the correct next step
    'reject_expense', 'Reject a submitted expense by id.',
    {'expense_id': {'type': 'string'}, 'reason': {'type': 'string'}}, ['expense_id', 'reason'])

# Registry of every tool this module's cases can offer, by name -- used by
# classify_call() (via _call_record()) to look up the schema of whatever
# tool a call actually named, and by detect_pseudo_tool_calls() to decide
# whether a name found in free text is one of "ours" rather than
# coincidental text. Built once from the ALL-CAPS TOOL_* constants above
# rather than hand-listed, so a new tool can't be added up there and
# forgotten down here.
ALL_TOOLS = [v for k, v in list(globals().items())
             if k.startswith('TOOL_') and isinstance(v, dict) and 'function' in v]
TOOLS_BY_NAME = {t['function']['name']: t for t in ALL_TOOLS}

SYSTEM_PROMPT = (
    'You are an operations assistant with access to a fixed set of tools. '
    'Call a tool only when it is needed to fulfil the request; otherwise answer directly in text. '
    'Use only the parameters each tool declares.'
)


# ---------------------------------------------------------------------------
# Pseudo-tool-call detection (issue #29): a call written as TEXT instead of
# landing in the OpenAI-shaped `message['tool_calls']` array -- invisible to
# every scorer above, all of which only ever look at `tool_calls`. Six
# shapes, matching what real chat templates and servers are observed to
# leak when the parsing layer between them and this module's `chat()`
# doesn't turn the marker into a structured call:
# ---------------------------------------------------------------------------

PseudoToolCall = namedtuple('PseudoToolCall', ['format', 'tool', 'raw'])

# Qwen2.5/Nous-Hermes-2 style: <tool_call>\n{"name": ..., "arguments": ...}\n</tool_call>
_TOOL_CALL_TAG_RE = re.compile(r'<tool_call>\s*(.*?)\s*</tool_call>', re.DOTALL | re.IGNORECASE)

# A `<|tool_call|>` sentinel token wrapping JSON, seen when a chat
# template's special token leaks into `content` verbatim instead of being
# stripped and parsed server-side. Closing sentinel is optional -- a
# truncated completion can cut off before it.
_SENTINEL_TAG_RE = re.compile(r'<\|tool_call\|>\s*(.*?)\s*(?:<\|/tool_call\|>|$)', re.DOTALL)

# Hermes/Qwen XML function-call form: <function=name>{...}</function> or
# <function:name>{...}</function> (both separators are attested live).
_FUNCTION_XML_RE = re.compile(r'<function[=:]([\w.\-]+)>(.*?)</function>', re.DOTALL | re.IGNORECASE)

# Mistral's raw template: [TOOL_CALLS] [{"name": ..., "arguments": {...}}]
_MISTRAL_TOOL_CALLS_RE = re.compile(r'\[TOOL_CALLS\]\s*(\[.*\])', re.DOTALL)

# gpt-oss Harmony channel routing: `to=functions.<name>` addresses the
# commentary channel at a function. When the server fails to turn that into
# a structured call, it leaks into `content` as plain text with this marker
# still in it.
_HARMONY_TO_FUNCTIONS_RE = re.compile(r'to=functions\.([A-Za-z_][\w.\-]*)')

# A fenced code block of any kind (```json, ```python, bare ```...```).
_FENCED_CODE_RE = re.compile(r'```(\w*)\n(.*?)```', re.DOTALL)


def _tool_name_from_json_obj(obj):
    """Pull a tool name out of a decoded JSON object shaped like
    `{"name": ..., "arguments": ...}`, `{"tool": ...}`, or the OpenAI/
    Harmony-ish `{"function": {"name": ...}}`. Returns None if `obj` isn't
    a dict or none of those keys hold a string."""
    if not isinstance(obj, dict):
        return None
    fn = obj.get('function')
    if isinstance(fn, dict) and isinstance(fn.get('name'), str):
        return fn['name']
    for key in ('name', 'tool', 'tool_name'):
        v = obj.get(key)
        if isinstance(v, str):
            return v
    return None


def _scan_json_objects(s):
    """Yield (start, end, obj) for every brace-matched, JSON-decodable
    object substring of `s`, scanning left to right and skipping past each
    match found. Brace-matching (not a flat regex) because `arguments` is
    routinely a nested object itself -- `\\{[^{}]*\\}` cannot span that, and
    silently missing the exact case this exists to catch (a real,
    multi-field tool call) would defeat the point."""
    i, n = 0, len(s)
    while i < n:
        if s[i] != '{':
            i += 1
            continue
        depth, in_str, esc, j = 0, False, False, i
        while j < n:
            c = s[j]
            if in_str:
                if esc:
                    esc = False
                elif c == '\\':
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        if depth == 0 and j < n:
            snippet = s[i:j + 1]
            try:
                obj = json.loads(snippet)
            except (ValueError, TypeError):
                obj = None
            if isinstance(obj, dict):
                yield i, j + 1, obj
                i = j + 1
                continue
        i += 1


def _overlaps(start, end, claimed):
    return any(start < c_end and end > c_start for c_start, c_end in claimed)


def detect_pseudo_tool_calls(text, known_tool_names):
    """Scan a model's free-text answer for a tool call written as TEXT
    instead of a structured `tool_calls` entry. Six shapes, each tagged in
    the returned `PseudoToolCall.format` so the artefact says which leaked,
    not just that something did:

      jsonObject          a bare JSON object naming a tool ({"name": ...,
                           "arguments": {...}} or an OpenAI-shaped
                           {"function": {"name": ...}}), found anywhere via
                           brace-matched scanning (a flat regex cannot span
                           a nested `arguments` object -- see
                           _scan_json_objects()). Restricted to
                           `known_tool_names` so an unrelated JSON blob in
                           the answer text ("here's an example: {...}")
                           isn't misread as a call.
      toolCallTag         Qwen2.5/Nous-Hermes-2 `<tool_call>...</tool_call>`.
      sentinelTag         a `<|tool_call|>` special-token sentinel wrapping
                           JSON, leaked verbatim instead of parsed.
      functionXml         `<function=name>...</function>` /
                           `<function:name>...</function>`.
      mistralToolCalls     Mistral's raw `[TOOL_CALLS] [...]` marker.
      harmonyLeak         gpt-oss's Harmony `to=functions.<name>` channel
                           routing marker, leaked as plain text.
      fencedCode          a fenced ``` code block whose content is a JSON
                           object naming a known tool, or a
                           `known_tool_name(...)` call-syntax line.

    A tool name is counted ONLY inside one of these structured shapes --
    never for bare prose that merely mentions the name ("you could call
    get_weather here" is not a pseudo-tool-call; see the unit test named
    for exactly that). Matches are resolved in the order above, each
    claiming its text span, so one leaked call is not double-counted under
    two formats (e.g. the JSON inside a `<tool_call>` tag is not ALSO
    reported as a bare `jsonObject`)."""
    if not isinstance(text, str) or not text:
        return []
    known = set(known_tool_names)
    found = []
    claimed = []

    def _claim(start, end, fmt, tool, raw):
        claimed.append((start, end))
        found.append(PseudoToolCall(fmt, tool, _preview(raw)))

    for m in _TOOL_CALL_TAG_RE.finditer(text):
        try:
            obj = json.loads(m.group(1))
        except (ValueError, TypeError):
            obj = None
        tool = _tool_name_from_json_obj(obj)
        if tool:
            _claim(m.start(), m.end(), 'toolCallTag', tool, m.group(0))

    for m in _SENTINEL_TAG_RE.finditer(text):
        if _overlaps(m.start(), m.end(), claimed):
            continue
        try:
            obj = json.loads(m.group(1))
        except (ValueError, TypeError):
            obj = None
        tool = _tool_name_from_json_obj(obj)
        if tool:
            _claim(m.start(), m.end(), 'sentinelTag', tool, m.group(0))

    for m in _FUNCTION_XML_RE.finditer(text):
        if _overlaps(m.start(), m.end(), claimed):
            continue
        _claim(m.start(), m.end(), 'functionXml', m.group(1), m.group(0))

    for m in _MISTRAL_TOOL_CALLS_RE.finditer(text):
        if _overlaps(m.start(), m.end(), claimed):
            continue
        try:
            arr = json.loads(m.group(1))
        except (ValueError, TypeError):
            arr = None
        if isinstance(arr, list):
            for item in arr:
                tool = _tool_name_from_json_obj(item)
                if tool:
                    _claim(m.start(), m.end(), 'mistralToolCalls', tool, m.group(0))
                    break  # one claim per marker: the leak is what's counted, not each array entry

    for m in _HARMONY_TO_FUNCTIONS_RE.finditer(text):
        if _overlaps(m.start(), m.end(), claimed):
            continue
        _claim(m.start(), m.end(), 'harmonyLeak', m.group(1), m.group(0))

    for m in _FENCED_CODE_RE.finditer(text):
        if _overlaps(m.start(), m.end(), claimed):
            continue
        body = m.group(2)
        matched = False
        for _start, _end, obj in _scan_json_objects(body):
            tool = _tool_name_from_json_obj(obj)
            if tool and tool in known:
                _claim(m.start(), m.end(), 'fencedCode', tool, m.group(0))
                matched = True
                break
        if not matched:
            for name in known:
                if re.search(r'\b' + re.escape(name) + r'\s*\(', body):
                    _claim(m.start(), m.end(), 'fencedCode', name, m.group(0))
                    break

    for start, end, obj in _scan_json_objects(text):
        if _overlaps(start, end, claimed):
            continue
        tool = _tool_name_from_json_obj(obj)
        if tool and tool in known:
            _claim(start, end, 'jsonObject', tool, text[start:end])

    return found


def _msg(role, content):
    return {'role': role, 'content': content}


def _run_case(base_url, model, max_tokens, timeout, messages, tools, api_key=None):
    resp = chat(base_url, model, messages, tools=tools, max_tokens=max_tokens, timeout=timeout,
                api_key=api_key)
    choice = (resp.get('choices') or [{}])[0]
    message = choice.get('message') or {}
    parsed = parse_message(message)
    parsed['finish_reason'] = choice.get('finish_reason')
    return parsed


def _first_call(parsed):
    return parsed['tool_calls'][0] if parsed['tool_calls'] else None


def _case_record(case_id, description, passed, reasons, extra=None):
    rec = {'case': case_id, 'description': description, 'passed': passed, 'reasons': reasons}
    if extra:
        rec.update(extra)
    return rec


def _pseudo_calls_extra(known_tool_names, *turn_texts):
    """`turn_texts`: (turn_number, text) pairs, one per model response the
    case exercised. Returns the flat list of pseudo-tool-calls found across
    all of them, each turn-tagged -- suitable for a case record's
    `pseudoToolCalls` key. Empty list (not None) when nothing was found, so
    every case record has the same shape whether or not anything leaked."""
    found = []
    for turn, text in turn_texts:
        for p in detect_pseudo_tool_calls(text, known_tool_names):
            found.append({'turn': turn, 'format': p.format, 'tool': p.tool, 'raw': p.raw})
    return found


# ---------------------------------------------------------------------------
# Dimension A: schema adherence
# ---------------------------------------------------------------------------

SCHEMA_CASES = [
    ('room-basic',
     'Book Falcon for 30 minutes starting at 2026-10-01T14:00:00 for alice@example.com and bob@example.com.',
     TOOL_BOOK_ROOM),
    ('room-recurring',
     'Set up a recurring 60 minute meeting in room Heron at 2026-10-02T09:30:00 with just carol@example.com.',
     TOOL_BOOK_ROOM),
    ('weather-units',
     'What is the weather in Tokyo right now, in fahrenheit?',
     TOOL_GET_WEATHER),
    # Compound-schema probes (#15): a required top-level scalar (`path`)
    # alongside a required nested array-of-objects (`edits[]`), shaped like
    # pi's real `edit` tool -- see TOOL_EDIT_FILE above. Two cases, not one:
    # the failure this exists to catch (model fills in `edits[]` correctly
    # and drops the sibling `path`) is a tendency, not a certainty, and a
    # single prompt risks passing by chance on a model that would still fail
    # it most of the time -- the same reason room-basic/room-recurring
    # already probe TOOL_BOOK_ROOM twice.
    ('edit-single',
     'In config/ops.yaml, replace the line "retries: 3" with "retries: 5".',
     TOOL_EDIT_FILE),
    ('edit-multi',
     'In runbook.md, replace "Owner: TBD" with "Owner: SRE" and replace "Status: draft" with "Status: active".',
     TOOL_EDIT_FILE),
]


def run_schema_adherence(base_url, model, max_tokens, timeout, errors, api_key=None):
    cases = []
    for case_id, prompt, tool in SCHEMA_CASES:
        messages = [_msg('system', SYSTEM_PROMPT), _msg('user', prompt)]
        try:
            parsed = _run_case(base_url, model, max_tokens, timeout, messages, [tool], api_key=api_key)
        except ServerError as e:
            errors.append(f'schemaAdherence/{case_id}: {e}')
            cases.append(_case_record(case_id, prompt, False, [f'request failed: {e}'],
                                       {'expectedTool': tool['function']['name'], 'calls': [],
                                        'pseudoToolCalls': []}))
            continue
        call = _first_call(parsed)
        passed, reasons = score_schema_adherence(tool, call)
        # right_args_ok=None (#29): schemaAdherence has no expected VALUE,
        # only an expected shape -- right-tool and right-args collapse.
        tool_name = tool['function']['name']
        cases.append(_case_record(case_id, prompt, passed, reasons, {
            'expectedTool': tool_name,
            'toolCalled': call['name'] if call else None,
            'arguments': call['args'] if call else None,
            'calls': [_call_record(1, tool_name, call)],
            'pseudoToolCalls': _pseudo_calls_extra([tool_name], (1, parsed.get('text'))),
        }))
    summary = _summarize('schemaAdherence', cases)
    # Per-tool breakdown (#15): "schemaAdherence 3/3" hid that all three
    # flat probes happened to be tools the model handles fine, while the one
    # tool that matters most in a real harness trace (a compound-schema
    # mutating call) was never asked. `byTool` makes a single catastrophic
    # tool visible in the dimension total, the way `errorsByTool` does in
    # events-summary.json -- granularity the full per-tool report (#29) will
    # build on, not duplicate.
    summary['byTool'] = _schema_adherence_by_tool(cases)
    return summary


def _schema_adherence_by_tool(cases):
    by_tool = {}
    for c in cases:
        tool_name = c.get('expectedTool')
        if tool_name is None:
            continue
        bucket = by_tool.setdefault(tool_name, {'passed': 0, 'total': 0, 'notAttempted': 0})
        if c['passed'] is None:
            bucket['notAttempted'] += 1
        else:
            bucket['total'] += 1
            if c['passed']:
                bucket['passed'] += 1
    for bucket in by_tool.values():
        bucket['score'] = round(bucket['passed'] / bucket['total'], 4) if bucket['total'] else None
    return by_tool


# ---------------------------------------------------------------------------
# Dimension B: correct tool selection among decoys
# ---------------------------------------------------------------------------

SELECTION_CASES = [
    ('pick-weather',
     'How hot is it in Lisbon today?',
     [TOOL_GET_WEATHER, TOOL_GET_WEATHER_ALERTS, TOOL_CONVERT_CURRENCY], 'get_weather'),
    ('pick-alerts',
     'Are there any severe weather alerts active for the Gulf Coast region right now?',
     [TOOL_GET_WEATHER, TOOL_GET_WEATHER_ALERTS, TOOL_CREATE_REMINDER], 'get_weather_alerts'),
    ('pick-order-status',
     "What's the status of the most recent order for the customer with id cus_77120?",
     [TOOL_GET_ORDER_STATUS, TOOL_CANCEL_SUBSCRIPTION, TOOL_LOOKUP_CUSTOMER], 'get_order_status'),
    ('pick-cancel',
     'Please cancel the subscription belonging to customer cus_31004.',
     [TOOL_GET_ORDER_STATUS, TOOL_CANCEL_SUBSCRIPTION, TOOL_LOOKUP_CUSTOMER], 'cancel_subscription'),
    ('pick-rooms-list',
     'What meeting rooms are free right now in the Riverside building?',
     [TOOL_BOOK_ROOM, TOOL_LIST_ROOMS], 'list_available_rooms'),
]


def run_tool_selection(base_url, model, max_tokens, timeout, errors, api_key=None):
    cases = []
    for case_id, prompt, tools, expected in SELECTION_CASES:
        messages = [_msg('system', SYSTEM_PROMPT), _msg('user', prompt)]
        try:
            parsed = _run_case(base_url, model, max_tokens, timeout, messages, tools, api_key=api_key)
        except ServerError as e:
            errors.append(f'toolSelection/{case_id}: {e}')
            cases.append(_case_record(case_id, prompt, False, [f'request failed: {e}'],
                                       {'calls': [], 'pseudoToolCalls': []}))
            continue
        call = _first_call(parsed)
        passed, reasons = score_tool_selection(expected, call)
        offered = [t['function']['name'] for t in tools]
        cases.append(_case_record(case_id, prompt, passed, reasons, {
            'expectedTool': expected, 'toolCalled': call['name'] if call else None,
            'calls': [_call_record(1, expected, call)],
            'pseudoToolCalls': _pseudo_calls_extra(offered, (1, parsed.get('text'))),
        }))
    return _summarize('toolSelection', cases)


# ---------------------------------------------------------------------------
# Dimension C: multi-step dependency
# ---------------------------------------------------------------------------

def run_multi_step_dependency(base_url, model, max_tokens, timeout, errors, api_key=None):
    cases = []

    # Scenario 1: lookup_customer -> get_order_status(customer_id=<looked-up id>)
    case_id = 'customer-then-order'
    prompt = "What is the order status for the customer whose email is dana@example.com?"
    tools = [TOOL_LOOKUP_CUSTOMER, TOOL_GET_ORDER_STATUS]
    fake_customer_id = 'cus_48291'
    messages = [_msg('system', SYSTEM_PROMPT), _msg('user', prompt)]
    try:
        offered = [t['function']['name'] for t in tools]
        p1 = _run_case(base_url, model, max_tokens, timeout, messages, tools, api_key=api_key)
        c1 = _first_call(p1)
        if c1 is None or c1['name'] != 'lookup_customer':
            cases.append(_case_record(case_id, prompt, False,
                                       [f"expected first call lookup_customer, got {c1['name'] if c1 else None}"],
                                       {'firstCall': c1,
                                        'calls': [_call_record(1, 'lookup_customer', c1)],
                                        'pseudoToolCalls': _pseudo_calls_extra(offered, (1, p1.get('text')))}))
        else:
            messages.append({'role': 'assistant', 'content': p1.get('text'),
                              'tool_calls': [{'id': c1['id'], 'type': 'function',
                                              'function': {'name': c1['name'], 'arguments': json.dumps(c1['args'])}}]})
            messages.append({'role': 'tool', 'tool_call_id': c1['id'],
                              'content': json.dumps({'customer_id': fake_customer_id})})
            p2 = _run_case(base_url, model, max_tokens, timeout, messages, tools, api_key=api_key)
            c2 = _first_call(p2)
            passed, reasons = score_dependency(c2, 'customer_id', fake_customer_id)
            cases.append(_case_record(case_id, prompt, passed, reasons, {
                'firstCall': c1, 'secondCall': c2, 'simulatedFirstResult': {'customer_id': fake_customer_id},
                'calls': [_call_record(1, 'lookup_customer', c1),
                          _call_record(2, 'get_order_status', c2, right_args_ok=passed)],
                'pseudoToolCalls': _pseudo_calls_extra(offered, (1, p1.get('text')), (2, p2.get('text'))),
            }))
    except ServerError as e:
        errors.append(f'multiStepDependency/{case_id}: {e}')
        cases.append(_case_record(case_id, prompt, False, [f'request failed: {e}'],
                                   {'calls': [], 'pseudoToolCalls': []}))

    # Scenario 2: get_exchange_rate -> apply_exchange_rate(rate=<looked-up rate>)
    case_id = 'rate-then-apply'
    prompt = 'Convert 250 USD to EUR.'
    tools = [TOOL_GET_EXCHANGE_RATE, TOOL_APPLY_RATE]
    fake_rate = 0.9137
    messages = [_msg('system', SYSTEM_PROMPT), _msg('user', prompt)]
    try:
        offered = [t['function']['name'] for t in tools]
        p1 = _run_case(base_url, model, max_tokens, timeout, messages, tools, api_key=api_key)
        c1 = _first_call(p1)
        if c1 is None or c1['name'] != 'get_exchange_rate':
            cases.append(_case_record(case_id, prompt, False,
                                       [f"expected first call get_exchange_rate, got {c1['name'] if c1 else None}"],
                                       {'firstCall': c1,
                                        'calls': [_call_record(1, 'get_exchange_rate', c1)],
                                        'pseudoToolCalls': _pseudo_calls_extra(offered, (1, p1.get('text')))}))
        else:
            messages.append({'role': 'assistant', 'content': p1.get('text'),
                              'tool_calls': [{'id': c1['id'], 'type': 'function',
                                              'function': {'name': c1['name'], 'arguments': json.dumps(c1['args'])}}]})
            messages.append({'role': 'tool', 'tool_call_id': c1['id'],
                              'content': json.dumps({'rate': fake_rate})})
            p2 = _run_case(base_url, model, max_tokens, timeout, messages, tools, api_key=api_key)
            c2 = _first_call(p2)
            passed, reasons = score_dependency(c2, 'rate', fake_rate)
            cases.append(_case_record(case_id, prompt, passed, reasons, {
                'firstCall': c1, 'secondCall': c2, 'simulatedFirstResult': {'rate': fake_rate},
                'calls': [_call_record(1, 'get_exchange_rate', c1),
                          _call_record(2, 'apply_exchange_rate', c2, right_args_ok=passed)],
                'pseudoToolCalls': _pseudo_calls_extra(offered, (1, p1.get('text')), (2, p2.get('text'))),
            }))
    except ServerError as e:
        errors.append(f'multiStepDependency/{case_id}: {e}')
        cases.append(_case_record(case_id, prompt, False, [f'request failed: {e}'],
                                   {'calls': [], 'pseudoToolCalls': []}))

    return _summarize('multiStepDependency', cases)


# ---------------------------------------------------------------------------
# Dimension D: error recovery
# ---------------------------------------------------------------------------

def run_error_recovery(base_url, model, max_tokens, timeout, errors, api_key=None):
    cases = []
    scenarios = [
        ('reminder-bad-format',
         'Remind me to call the vendor next Tuesday at 3pm.',
         [TOOL_CREATE_REMINDER],
         'create_reminder',
         # Deliberately does not presuppose which value the model chose for
         # remind_at (a live run showed the model can resolve "next Tuesday"
         # to an absolute ISO-8601 timestamp on its own, which an error
         # message hardcoded to say "you sent a relative phrase" would then
         # misdescribe). "Rejected, try a different value" applies whatever
         # form the first call took, so the test still isolates whether the
         # model adapts *something* rather than repeating the exact call.
         {'error': 'create_reminder failed: remind_at was rejected by the calendar service. '
                   'Provide a different value.'}),
        ('room-conflict',
         'Book room Falcon for 30 minutes starting 2026-10-01T14:00:00 for alice@example.com.',
         [TOOL_BOOK_ROOM],
         'book_meeting_room',
         {'error': 'room Falcon is already booked for that time slot'}),
    ]
    for case_id, prompt, tools, expected_tool, error_result in scenarios:
        messages = [_msg('system', SYSTEM_PROMPT), _msg('user', prompt)]
        offered = [t['function']['name'] for t in tools]
        try:
            p1 = _run_case(base_url, model, max_tokens, timeout, messages, tools, api_key=api_key)
            c1 = _first_call(p1)
            if c1 is None:
                cases.append(_case_record(case_id, prompt, False,
                                           ['no tool call made on the first turn; cannot probe recovery'],
                                           {'firstCall': None,
                                            'calls': [_call_record(1, expected_tool, None)],
                                            'pseudoToolCalls': _pseudo_calls_extra(offered, (1, p1.get('text')))}))
                continue
            messages.append({'role': 'assistant', 'content': p1.get('text'),
                              'tool_calls': [{'id': c1['id'], 'type': 'function',
                                              'function': {'name': c1['name'], 'arguments': json.dumps(c1['args'])}}]})
            messages.append({'role': 'tool', 'tool_call_id': c1['id'], 'content': json.dumps(error_result)})
            p2 = _run_case(base_url, model, max_tokens, timeout, messages, tools, api_key=api_key)
            c2 = _first_call(p2)
            passed, reasons = score_error_recovery(c1, c2)
            # First call: no expected VALUE exists yet, so right_args_ok=None
            # (collapses to schemaAdherence's rule). Second (retry) call:
            # right_args_ok=passed -- score_error_recovery()'s bool, i.e.
            # "adapted rather than repeated verbatim". See classify_call()'s
            # docstring for why a retry that adapts with a DIFFERENT tool
            # still caps at schemaValid here despite being a dimension pass.
            cases.append(_case_record(case_id, prompt, passed, reasons, {
                'firstCall': c1, 'simulatedError': error_result, 'secondCall': c2,
                'secondCallText': _preview(p2.get('text')),
                'calls': [_call_record(1, expected_tool, c1),
                          _call_record(2, expected_tool, c2, right_args_ok=passed)],
                'pseudoToolCalls': _pseudo_calls_extra(offered, (1, p1.get('text')), (2, p2.get('text'))),
            }))
        except ServerError as e:
            errors.append(f'errorRecovery/{case_id}: {e}')
            cases.append(_case_record(case_id, prompt, False, [f'request failed: {e}'],
                                       {'calls': [], 'pseudoToolCalls': []}))
    return _summarize('errorRecovery', cases)


# ---------------------------------------------------------------------------
# Dimension E: refusal to call when no tool applies (false-positive direction)
# ---------------------------------------------------------------------------

REFUSAL_TOOLS = [TOOL_GET_WEATHER, TOOL_CONVERT_CURRENCY, TOOL_LOOKUP_CUSTOMER]

REFUSAL_CASES = [
    ('trivia', 'What is the capital of France?'),
    ('arithmetic', "What's 17 times 23?"),
    ('opinion', 'What is a good name for a pet cat?'),
    ('unit-conversion-trap',  # decoy word overlap: "convert" is offered, but for currency, not units
     'Convert 10 miles to kilometers.'),
]


def run_refusal(base_url, model, max_tokens, timeout, errors, api_key=None):
    cases = []
    for case_id, prompt in REFUSAL_CASES:
        messages = [_msg('system', SYSTEM_PROMPT), _msg('user', prompt)]
        try:
            parsed = _run_case(base_url, model, max_tokens, timeout, messages, REFUSAL_TOOLS, api_key=api_key)
        except ServerError as e:
            errors.append(f'refusal/{case_id}: {e}')
            cases.append(_case_record(case_id, prompt, False, [f'request failed: {e}'],
                                       {'calls': [], 'pseudoToolCalls': []}))
            continue
        passed, reasons = score_refusal(parsed)
        # expected_tool_name=None: no tool is ever "right" here, so any call
        # made caps at schemaValid (see classify_call()'s docstring, point
        # 3). A refusal case that behaves correctly makes zero calls, hence
        # an empty `calls` list -- there is nothing to classify, not a
        # noCall entry for each of the offered tools that weren't picked.
        cases.append(_case_record(case_id, prompt, passed, reasons, {
            'toolsCalled': [c['name'] for c in parsed['tool_calls']],
            'answerText': _preview(parsed.get('text')),
            'calls': [_call_record(i + 1, None, c) for i, c in enumerate(parsed['tool_calls'])],
            'pseudoToolCalls': _pseudo_calls_extra(
                [t['function']['name'] for t in REFUSAL_TOOLS], (1, parsed.get('text'))),
        }))
    return _summarize('refusal', cases)


# ---------------------------------------------------------------------------
# Dimension F: short dependent chains of 3-5 calls (issue #30)
# ---------------------------------------------------------------------------
# See the module docstring's "Issue #30" section for the full rationale
# (unguessable values, what "depth reached" means, and the parallel-call /
# skip-ahead decisions). ChainStep.dependency mirrors the PREVIOUS step's
# `result` dict (or, for a step needing more than one prior value, the
# union of several previous results' dicts) so a generic runner can walk
# any chain without per-scenario special-casing.

ChainStep = namedtuple('ChainStep', ['tool', 'decoys', 'dependency', 'result'])
ChainScenario = namedtuple('ChainScenario', ['chain_id', 'prompt', 'steps'])

_ORDER_ID = _seeded_id('shipping/order_id', 'ord')
_TRACKING_NUMBER = _seeded_id('shipping/tracking_number', 'trk')

_TICKET_ID = _seeded_id('ticket/ticket_id', 'tick')
_AGENT_ID = _seeded_id('ticket/agent_id', 'agt')

_DEVICE_ID = _seeded_id('device/device_id', 'dev')
_ACTIVATION_CODE = _seeded_id('device/activation_code', 'act')
_SESSION_TOKEN = _seeded_id('device/session_token', 'sess')

_EXPENSE_ID = _seeded_id('expense/expense_id', 'exp')
_APPROVER_ID = _seeded_id('expense/approver_id', 'appr')
_NOTIFICATION_ID = _seeded_id('expense/notification_id', 'notif')
_APPROVAL_CODE = _seeded_id('expense/approval_code', 'apc')

CHAIN_SCENARIOS = [
    ChainScenario(
        'order-ship-track',
        'Ship the most recent order for the customer with email morgan@example.com, '
        'then tell me its current delivery status.',
        [
            ChainStep(TOOL_LOOKUP_ORDER_BY_EMAIL, [TOOL_GET_ORDER_STATUS, TOOL_CANCEL_SUBSCRIPTION],
                      None, {'order_id': _ORDER_ID}),
            ChainStep(TOOL_GET_SHIPPING_LABEL, [TOOL_GET_ORDER_STATUS, TOOL_REROUTE_SHIPMENT],
                      {'order_id': _ORDER_ID}, {'tracking_number': _TRACKING_NUMBER}),
            ChainStep(TOOL_GET_TRACKING_STATUS, [TOOL_REROUTE_SHIPMENT, TOOL_GET_ORDER_STATUS],
                      {'tracking_number': _TRACKING_NUMBER}, {'status': 'in_transit'}),
        ]),
    ChainScenario(
        'ticket-assign-contact',
        "Open a support ticket for \"VPN keeps disconnecting\", assign it to an agent, "
        "and give me that agent's contact email.",
        [
            ChainStep(TOOL_OPEN_SUPPORT_TICKET, [TOOL_LOOKUP_CUSTOMER, TOOL_CLOSE_SUPPORT_TICKET],
                      None, {'ticket_id': _TICKET_ID}),
            ChainStep(TOOL_ASSIGN_TICKET_AGENT, [TOOL_CLOSE_SUPPORT_TICKET, TOOL_CREATE_REMINDER],
                      {'ticket_id': _TICKET_ID}, {'agent_id': _AGENT_ID}),
            ChainStep(TOOL_GET_AGENT_CONTACT, [TOOL_CLOSE_SUPPORT_TICKET, TOOL_LOOKUP_CUSTOMER],
                      {'agent_id': _AGENT_ID}, {'contact_email': 'agent@example.com'}),
        ]),
    ChainScenario(
        'device-register-activate',
        'Register a new device named "kiosk-07", activate it, and tell me its status.',
        [
            ChainStep(TOOL_REGISTER_DEVICE, [TOOL_LIST_DEVICES, TOOL_DEREGISTER_DEVICE],
                      None, {'device_id': _DEVICE_ID}),
            ChainStep(TOOL_GENERATE_ACTIVATION_CODE, [TOOL_DEREGISTER_DEVICE, TOOL_LIST_DEVICES],
                      {'device_id': _DEVICE_ID}, {'activation_code': _ACTIVATION_CODE}),
            # Needs BOTH the device id (step 1) and the activation code
            # (step 2) at once -- score_chain_step()'s reason for existing
            # instead of reusing score_dependency().
            ChainStep(TOOL_ACTIVATE_DEVICE, [TOOL_DEREGISTER_DEVICE, TOOL_LIST_DEVICES],
                      {'device_id': _DEVICE_ID, 'activation_code': _ACTIVATION_CODE},
                      {'session_token': _SESSION_TOKEN}),
            ChainStep(TOOL_GET_DEVICE_STATUS, [TOOL_LIST_DEVICES, TOOL_DEREGISTER_DEVICE],
                      {'session_token': _SESSION_TOKEN}, {'status': 'active'}),
        ]),
    ChainScenario(
        'expense-submit-release',
        'Submit a $42.50 expense for "client dinner", route it for approval, notify the approver, '
        "and release payment once it's approved.",
        [
            ChainStep(TOOL_SUBMIT_EXPENSE, [TOOL_LIST_PENDING_EXPENSES, TOOL_REJECT_EXPENSE],
                      None, {'expense_id': _EXPENSE_ID}),
            ChainStep(TOOL_ROUTE_FOR_APPROVAL, [TOOL_REJECT_EXPENSE, TOOL_LIST_PENDING_EXPENSES],
                      {'expense_id': _EXPENSE_ID}, {'approver_id': _APPROVER_ID}),
            ChainStep(TOOL_NOTIFY_APPROVER, [TOOL_REJECT_EXPENSE, TOOL_LIST_PENDING_EXPENSES],
                      {'approver_id': _APPROVER_ID}, {'notification_id': _NOTIFICATION_ID}),
            ChainStep(TOOL_GET_APPROVAL_STATUS, [TOOL_LIST_PENDING_EXPENSES, TOOL_REJECT_EXPENSE],
                      {'notification_id': _NOTIFICATION_ID}, {'approval_code': _APPROVAL_CODE}),
            ChainStep(TOOL_RELEASE_PAYMENT, [TOOL_REJECT_EXPENSE, TOOL_LIST_PENDING_EXPENSES],
                      {'approval_code': _APPROVAL_CODE}, {'confirmation': 'paid'}),
        ]),
]


def _run_chain(base_url, model, max_tokens, timeout, errors, scenario, api_key=None):
    chain_id, prompt, steps = scenario.chain_id, scenario.prompt, scenario.steps
    chain_len = len(steps)
    messages = [_msg('system', SYSTEM_PROMPT), _msg('user', prompt)]
    calls_records = []
    pseudo = []
    depth_reached = 0
    broken_at_step = None
    broken_at_level = None
    break_reasons = None

    for i, step in enumerate(steps):
        step_no = i + 1
        tools_here = [step.tool] + list(step.decoys)
        offered = [t['function']['name'] for t in tools_here]
        expected_name = step.tool['function']['name']
        try:
            parsed = _run_case(base_url, model, max_tokens, timeout, messages, tools_here, api_key=api_key)
        except ServerError as e:
            errors.append(f'shortChains/{chain_id}: {e}')
            broken_at_step = step_no
            break_reasons = [f'request failed: {e}']
            break

        raw_calls = parsed['tool_calls']
        primary = raw_calls[0] if raw_calls else None
        extra_calls = raw_calls[1:]
        dep_ok, dep_reasons = score_chain_step(primary, step.dependency)
        # right_args_ok=None on step 1 (no dependency exists yet) collapses
        # right-tool/right-args the same way schemaAdherence does; every
        # later step's right_args_ok is score_chain_step()'s own verdict.
        right_args_ok = dep_ok if step.dependency is not None else None
        rec = _call_record(step_no, expected_name, primary, right_args_ok=right_args_ok)
        calls_records.append(rec)
        # Parallel calls (see module docstring): recorded and still folded
        # into report['callClassification'] via calls_records, but never
        # used to advance or judge the chain -- only `primary` is.
        for extra in extra_calls:
            extra_rec = _call_record(step_no, expected_name, extra, right_args_ok=right_args_ok)
            extra_rec['parallel'] = True
            calls_records.append(extra_rec)
        pseudo.extend(_pseudo_calls_extra(offered, (step_no, parsed.get('text'))))

        # A step counts as reached only at the classify_call() ceiling
        # (rightArgs) -- "right tool, stale/invented value" is the break,
        # not a pass, scored at whatever level it actually landed on.
        if rec['level'] != CALL_LEVEL_RIGHT_ARGS:
            broken_at_step = step_no
            broken_at_level = rec['level']
            if primary is None:
                break_reasons = ['no call made on this turn -- step skipped, or the model answered in text']
            elif primary['name'] != expected_name:
                break_reasons = [f"expected {expected_name!r}, got {primary['name']!r} "
                                  '(decoy taken, or a later step guessed ahead of its dependency)']
            else:
                break_reasons = dep_reasons
            break

        depth_reached = step_no
        messages.append({'role': 'assistant', 'content': parsed.get('text'),
                          'tool_calls': [{'id': primary['id'], 'type': 'function',
                                          'function': {'name': primary['name'],
                                                       'arguments': json.dumps(primary['args'])}}]})
        messages.append({'role': 'tool', 'tool_call_id': primary['id'], 'content': json.dumps(step.result)})

    passed = depth_reached == chain_len
    reasons = [f'completed all {chain_len} steps'] if passed else (break_reasons or ['chain not completed'])
    return _case_record(chain_id, prompt, passed, reasons, {
        'chainLength': chain_len, 'depthReached': depth_reached,
        'brokenAtStep': broken_at_step, 'brokenAtLevel': broken_at_level,
        'calls': calls_records, 'pseudoToolCalls': pseudo,
    })


def _chain_depth_summary(cases):
    """Depth-based view alongside `_summarize()`'s pass/fail: total links
    reached across every chain versus every link offered, plus a count of
    which classification level each broken chain died at. Issue #30 asks
    for depth to be reported, not collapsed into one pass/fail bit per
    chain -- this is that number; `_summarize()`'s passed/total is "how
    many chains went the full distance", a coarser, still-useful sibling."""
    total_depth = sum(c['depthReached'] for c in cases)
    total_length = sum(c['chainLength'] for c in cases)
    broken_at_level = {}
    for c in cases:
        lvl = c.get('brokenAtLevel')
        if lvl is not None:
            broken_at_level[lvl] = broken_at_level.get(lvl, 0) + 1
    return {'totalDepthReached': total_depth, 'totalPossibleDepth': total_length,
            'score': round(total_depth / total_length, 4) if total_length else None,
            'brokenAtLevel': broken_at_level}


def run_short_chains(base_url, model, max_tokens, timeout, errors, api_key=None, scenarios=None):
    if scenarios is None:
        scenarios = CHAIN_SCENARIOS
    cases = [_run_chain(base_url, model, max_tokens, timeout, errors, scenario, api_key=api_key)
             for scenario in scenarios]
    summary = _summarize('shortChains', cases)
    summary['depth'] = _chain_depth_summary(cases)
    return summary


# ---------------------------------------------------------------------------
# Aggregation + artefact
# ---------------------------------------------------------------------------

def _summarize(name, cases):
    """`passed is None` means the case never exercised the thing it tests,
    so it is excluded from the denominator instead of being scored either
    way -- see score_error_recovery(). `score` is None, not 0.0, when
    nothing was attempted: a dimension that got no evidence has no score,
    and 0.0 would read as "failed everything"."""
    attempted = [c for c in cases if c['passed'] is not None]
    not_attempted = len(cases) - len(attempted)
    total = len(attempted)
    passed = sum(1 for c in attempted if c['passed'])
    return {'dimension': name, 'passed': passed, 'total': total,
            'notAttempted': not_attempted, 'cases': cases,
            'score': round(passed / total, 4) if total else None}


def _iter_calls(dimensions):
    """Yield every per-call classification dict (`{turn, tool, expectedTool,
    level}`) recorded across every case in every dimension -- the flat
    stream both classification summaries below fold over."""
    for d in dimensions.values():
        for case in d['cases']:
            for c in case.get('calls') or []:
                yield c


def _call_level_counts(calls):
    counts = {lvl: 0 for lvl in CALL_LEVELS}
    for c in calls:
        counts[c['level']] = counts.get(c['level'], 0) + 1
    return counts


def _by_tool_classification(calls):
    """Generalises #15's schemaAdherence-only `byTool` across every
    dimension (issue #29): for each tool a call actually NAMED anywhere in
    the battery, how many of those calls reached each classification
    level. `tool` is None for a `noCall` entry (nothing was named), so
    those are skipped -- there is nothing to attribute to a tool. One
    catastrophic tool used across several dimensions is visible here even
    if it never looks bad within any single dimension's own byTool."""
    by_tool = {}
    levels_without_no_call = [lvl for lvl in CALL_LEVELS if lvl != CALL_LEVEL_NO_CALL]
    for c in calls:
        tool_name = c.get('tool')
        if tool_name is None:
            continue
        bucket = by_tool.setdefault(tool_name, {lvl: 0 for lvl in levels_without_no_call})
        bucket[c['level']] = bucket.get(c['level'], 0) + 1
    return by_tool


def _pseudo_summary(dimensions):
    """Every pseudo-tool-call found across every case (issue #29), plus a
    per-format count, so "how many leaked as text" and "which shape leaked"
    are both answerable from the artefact without walking every case."""
    found = []
    by_format = {}
    for d in dimensions.values():
        for case in d['cases']:
            for p in case.get('pseudoToolCalls') or []:
                found.append({'dimension': d['dimension'], 'case': case['case'], **p})
                by_format[p['format']] = by_format.get(p['format'], 0) + 1
    return {'total': len(found), 'byFormat': by_format, 'found': found}


def run_battery(base_url, model, max_tokens=512, timeout=60, api_key=None):
    errors = []
    dimensions = {
        'schemaAdherence': run_schema_adherence(base_url, model, max_tokens, timeout, errors, api_key=api_key),
        'toolSelection': run_tool_selection(base_url, model, max_tokens, timeout, errors, api_key=api_key),
        'multiStepDependency': run_multi_step_dependency(base_url, model, max_tokens, timeout, errors, api_key=api_key),
        'errorRecovery': run_error_recovery(base_url, model, max_tokens, timeout, errors, api_key=api_key),
        'refusal': run_refusal(base_url, model, max_tokens, timeout, errors, api_key=api_key),
        'shortChains': run_short_chains(base_url, model, max_tokens, timeout, errors, api_key=api_key),
    }
    # Denominators already exclude not-attempted cases (see _summarize), so
    # the overall figure can never be inflated by a dimension that got no
    # evidence. notAttempted is carried up so the headline cannot be read
    # without it.
    total = sum(d['total'] for d in dimensions.values())
    passed = sum(d['passed'] for d in dimensions.values())
    not_attempted = sum(d['notAttempted'] for d in dimensions.values())
    all_calls = list(_iter_calls(dimensions))
    return {
        'schemaVersion': SCHEMA_VERSION,
        'label': f"toolbattery-{_slug(model)}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        'model': model,
        'baseUrl': base_url,
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'dimensions': dimensions,
        'overall': {'passed': passed, 'total': total,
                    'notAttempted': not_attempted,
                    'score': round(passed / total, 4) if total else None},
        # #29: per-call classification counts (well-formed / schema-valid /
        # right tool / right args, cumulative) folded across every call in
        # every dimension, plus the per-tool breakdown generalised past
        # #15's schemaAdherence-only one.
        'callClassification': {
            'counts': _call_level_counts(all_calls),
            'byTool': _by_tool_classification(all_calls),
        },
        # #29: tool calls the model wrote as free text instead of a
        # structured tool_calls entry.
        'pseudoToolCalls': _pseudo_summary(dimensions),
        'transportErrors': errors,
    }


def _slug(s):
    return re.sub(r'[^A-Za-z0-9._-]+', '-', s).strip('-') or 'model'


def print_report(report):
    print(f"=== toolbattery: {report['model']} @ {report['baseUrl']} ===")
    for name, d in report['dimensions'].items():
        na = f"  [{d['notAttempted']} not attempted]" if d.get('notAttempted') else ''
        pct = '  --' if d['score'] is None else f"  ({d['score'] * 100:.0f}%)"
        print(f"  {name:22s} {d['passed']}/{d['total']}{pct}{na}")
        if d.get('byTool'):
            for tool_name, b in d['byTool'].items():
                tpct = '  --' if b['score'] is None else f"  ({b['score'] * 100:.0f}%)"
                print(f"    by tool: {tool_name:18s} {b['passed']}/{b['total']}{tpct}")
        if d.get('depth'):  # shortChains (#30): depth reached, not just pass/fail per chain
            dep = d['depth']
            dpct = '  --' if dep['score'] is None else f"  ({dep['score'] * 100:.0f}%)"
            print(f"    depth reached: {dep['totalDepthReached']}/{dep['totalPossibleDepth']}{dpct}")
            if dep['brokenAtLevel']:
                print(f"    broken at level: {dep['brokenAtLevel']}")
        for c in d['cases']:
            mark = {True: 'PASS', False: 'FAIL', None: 'N/A '}[c['passed']]
            extra = ''
            if 'depthReached' in c:
                extra = f" [depth {c['depthReached']}/{c['chainLength']}, broke at step " \
                        f"{c['brokenAtStep']} level {c['brokenAtLevel']}]" if c['brokenAtStep'] else \
                        f" [depth {c['depthReached']}/{c['chainLength']}]"
            print(f"    [{mark}] {c['case']}{extra}: {'; '.join(c['reasons']) if c['reasons'] else 'ok'}")
    o = report['overall']
    if o.get('notAttempted'):
        print(f"  {'':22s} {o['notAttempted']} case(s) not attempted -- "
              f"excluded from the denominator, not scored as passes")
    print(f"  {'OVERALL':22s} {report['overall']['passed']}/{report['overall']['total']}"
          f"  ({report['overall']['score']*100:.0f}%)")
    cc = report.get('callClassification')
    if cc:
        counts = cc['counts']
        print('  call classification (cumulative):')
        for lvl in CALL_LEVELS:
            if counts.get(lvl):
                print(f"    {lvl:16s} {counts[lvl]}")
        if cc.get('byTool'):
            print('  call classification by tool:')
            for tool_name, bucket in cc['byTool'].items():
                nonzero = {lvl: n for lvl, n in bucket.items() if n}
                print(f"    {tool_name:20s} {nonzero}")
    pt = report.get('pseudoToolCalls')
    if pt and pt['total']:
        print(f"  pseudo-tool-calls written as text: {pt['total']} {pt['byFormat']}")
        for p in pt['found']:
            print(f"    [{p['format']}] {p['dimension']}/{p['case']} turn {p['turn']}: {p['tool']!r}")
    if report['transportErrors']:
        print('  transport errors:')
        for e in report['transportErrors']:
            print(f'    - {e}')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--base-url', default=DEFAULT_BASE_URL,
                    help=f'OpenAI-compatible base URL (default: {DEFAULT_BASE_URL})')
    p.add_argument('--model', required=True, help='model id, as advertised by /v1/models')
    p.add_argument('--max-tokens', type=int, default=512)
    p.add_argument('--timeout', type=int, default=60, help='per-request timeout, seconds')
    p.add_argument('--out', default=None,
                    help='output path for the JSON artefact (default: toolbattery-results/<label>.json)')
    args = p.parse_args(argv)

    # Bearer key, if this base URL wants one: env var only, per direct.py's
    # module docstring -- never a --api-key flag, which `ps` and shell
    # history would both expose.
    api_key = api_key_from_env()

    if not server_reachable(args.base_url, api_key=api_key):
        print(f'ERROR: no server reachable at {args.base_url} -- refusing to run', file=sys.stderr)
        return 2

    # Sampled around the whole battery, not just around individual calls --
    # peak VRAM is a property of the run, and #28 wants it caught wherever
    # in the run it lands, not just at the edges.
    with VramSampler() as sampler:
        report = run_battery(args.base_url, args.model, max_tokens=args.max_tokens, timeout=args.timeout,
                              api_key=api_key)
    report['environment'] = build_environment(args.base_url, vram_peak=sampler.peak_mib())
    print_report(report)

    out_path = args.out
    if out_path is None:
        out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'toolbattery-results')
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
