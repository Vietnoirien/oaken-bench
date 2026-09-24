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

Five dimensions, scored independently, matching the issue:

  - schemaAdherence     required params present, types correct, no invented params
  - toolSelection       right tool out of a set that includes plausible decoys
  - multiStepDependency call 2 must use the value call 1's (simulated) result returned
  - errorRecovery       call 1 is made to fail; does call 2 repeat it verbatim or adapt
  - refusal             no tool applies; does the model call one anyway (false positive)

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
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from direct import ServerError, api_key_from_env, chat, server_reachable  # noqa: E402
from events import Call, _canonical_digest, _safe_tool_name, call_metrics  # noqa: E402

SCHEMA_VERSION = 1

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


def score_schema_adherence(tool_schema, parsed_call):
    """required params present, types correct, no invented parameters."""
    fn = tool_schema['function']
    params = fn.get('parameters') or {}
    props = params.get('properties') or {}
    required = set(params.get('required') or [])
    if parsed_call is None:
        return False, ['no tool call made']
    reasons = []
    if parsed_call['name'] != fn['name']:
        return False, [f"wrong tool called: {parsed_call['name']!r} (expected {fn['name']!r})"]
    args = parsed_call['args']
    if args is None:
        return False, [f"arguments did not parse as a JSON object: {parsed_call.get('parse_error')}"]
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


def score_refusal(parsed):
    """No tool applies -- the false-positive direction. Correct behaviour is
    zero tool calls."""
    made = [c['name'] for c in parsed['tool_calls']]
    if made:
        return False, [f'called {made} when no supplied tool applied to the request']
    return True, ['no tool called, as expected']


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

SYSTEM_PROMPT = (
    'You are an operations assistant with access to a fixed set of tools. '
    'Call a tool only when it is needed to fulfil the request; otherwise answer directly in text. '
    'Use only the parameters each tool declares.'
)


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
                                       {'expectedTool': tool['function']['name']}))
            continue
        call = _first_call(parsed)
        passed, reasons = score_schema_adherence(tool, call)
        cases.append(_case_record(case_id, prompt, passed, reasons, {
            'expectedTool': tool['function']['name'],
            'toolCalled': call['name'] if call else None,
            'arguments': call['args'] if call else None,
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
            cases.append(_case_record(case_id, prompt, False, [f'request failed: {e}']))
            continue
        call = _first_call(parsed)
        passed, reasons = score_tool_selection(expected, call)
        cases.append(_case_record(case_id, prompt, passed, reasons, {
            'expectedTool': expected, 'toolCalled': call['name'] if call else None,
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
        p1 = _run_case(base_url, model, max_tokens, timeout, messages, tools, api_key=api_key)
        c1 = _first_call(p1)
        if c1 is None or c1['name'] != 'lookup_customer':
            cases.append(_case_record(case_id, prompt, False,
                                       [f"expected first call lookup_customer, got {c1['name'] if c1 else None}"],
                                       {'firstCall': c1}))
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
            }))
    except ServerError as e:
        errors.append(f'multiStepDependency/{case_id}: {e}')
        cases.append(_case_record(case_id, prompt, False, [f'request failed: {e}']))

    # Scenario 2: get_exchange_rate -> apply_exchange_rate(rate=<looked-up rate>)
    case_id = 'rate-then-apply'
    prompt = 'Convert 250 USD to EUR.'
    tools = [TOOL_GET_EXCHANGE_RATE, TOOL_APPLY_RATE]
    fake_rate = 0.9137
    messages = [_msg('system', SYSTEM_PROMPT), _msg('user', prompt)]
    try:
        p1 = _run_case(base_url, model, max_tokens, timeout, messages, tools, api_key=api_key)
        c1 = _first_call(p1)
        if c1 is None or c1['name'] != 'get_exchange_rate':
            cases.append(_case_record(case_id, prompt, False,
                                       [f"expected first call get_exchange_rate, got {c1['name'] if c1 else None}"],
                                       {'firstCall': c1}))
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
            }))
    except ServerError as e:
        errors.append(f'multiStepDependency/{case_id}: {e}')
        cases.append(_case_record(case_id, prompt, False, [f'request failed: {e}']))

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
        try:
            p1 = _run_case(base_url, model, max_tokens, timeout, messages, tools, api_key=api_key)
            c1 = _first_call(p1)
            if c1 is None:
                cases.append(_case_record(case_id, prompt, False,
                                           ['no tool call made on the first turn; cannot probe recovery'],
                                           {'firstCall': None}))
                continue
            messages.append({'role': 'assistant', 'content': p1.get('text'),
                              'tool_calls': [{'id': c1['id'], 'type': 'function',
                                              'function': {'name': c1['name'], 'arguments': json.dumps(c1['args'])}}]})
            messages.append({'role': 'tool', 'tool_call_id': c1['id'], 'content': json.dumps(error_result)})
            p2 = _run_case(base_url, model, max_tokens, timeout, messages, tools, api_key=api_key)
            c2 = _first_call(p2)
            passed, reasons = score_error_recovery(c1, c2)
            cases.append(_case_record(case_id, prompt, passed, reasons, {
                'firstCall': c1, 'simulatedError': error_result, 'secondCall': c2,
                'secondCallText': _preview(p2.get('text')),
            }))
        except ServerError as e:
            errors.append(f'errorRecovery/{case_id}: {e}')
            cases.append(_case_record(case_id, prompt, False, [f'request failed: {e}']))
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
            cases.append(_case_record(case_id, prompt, False, [f'request failed: {e}']))
            continue
        passed, reasons = score_refusal(parsed)
        cases.append(_case_record(case_id, prompt, passed, reasons, {
            'toolsCalled': [c['name'] for c in parsed['tool_calls']],
            'answerText': _preview(parsed.get('text')),
        }))
    return _summarize('refusal', cases)


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


def run_battery(base_url, model, max_tokens=512, timeout=60, api_key=None):
    errors = []
    dimensions = {
        'schemaAdherence': run_schema_adherence(base_url, model, max_tokens, timeout, errors, api_key=api_key),
        'toolSelection': run_tool_selection(base_url, model, max_tokens, timeout, errors, api_key=api_key),
        'multiStepDependency': run_multi_step_dependency(base_url, model, max_tokens, timeout, errors, api_key=api_key),
        'errorRecovery': run_error_recovery(base_url, model, max_tokens, timeout, errors, api_key=api_key),
        'refusal': run_refusal(base_url, model, max_tokens, timeout, errors, api_key=api_key),
    }
    # Denominators already exclude not-attempted cases (see _summarize), so
    # the overall figure can never be inflated by a dimension that got no
    # evidence. notAttempted is carried up so the headline cannot be read
    # without it.
    total = sum(d['total'] for d in dimensions.values())
    passed = sum(d['passed'] for d in dimensions.values())
    not_attempted = sum(d['notAttempted'] for d in dimensions.values())
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
        for c in d['cases']:
            mark = {True: 'PASS', False: 'FAIL', None: 'N/A '}[c['passed']]
            print(f"    [{mark}] {c['case']}: {'; '.join(c['reasons']) if c['reasons'] else 'ok'}")
    o = report['overall']
    if o.get('notAttempted'):
        print(f"  {'':22s} {o['notAttempted']} case(s) not attempted -- "
              f"excluded from the denominator, not scored as passes")
    print(f"  {'OVERALL':22s} {report['overall']['passed']}/{report['overall']['total']}"
          f"  ({report['overall']['score']*100:.0f}%)")
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

    report = run_battery(args.base_url, args.model, max_tokens=args.max_tokens, timeout=args.timeout,
                          api_key=api_key)
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
