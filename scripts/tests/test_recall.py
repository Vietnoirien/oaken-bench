"""Tests for scripts/recall.py (issue #31).

Two layers, matching test_toolbattery.py's and test_server_config.py's
conventions:

1. Pure-logic tests (scoring, URL/depth parsing, seed derivation) -- no
   network, must never regress.
2. A handful of end-to-end tests against a fake server: a real stdlib
   `http.server.HTTPServer` serving `/props`, `/tokenize`, `/v1/models`
   and `/v1/chat/completions` on a background thread, the same technique
   test_server_config.py uses for `/props`. No real llama-server is ever
   started -- AGENTS.md's "test against fakes" rule.
"""
import http.server
import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import abstain  # noqa: E402
import direct_env  # noqa: E402
import haystack as haystack_mod  # noqa: E402
import recall  # noqa: E402


# ---------------------------------------------------------------------------
# Pure logic: no network
# ---------------------------------------------------------------------------

def test_recall_uses_the_shared_server_root_url_helper():
    # recall.py used to carry its own private _root_url() -- issue #32
    # merged in #28's fix (direct_env.server_root_url()) and replaced it
    # with that shared helper, so there is exactly one implementation of
    # "strip a trailing /v1" in the codebase. test_direct_env.py owns the
    # behavior's own test coverage; this just pins that recall.py is
    # actually calling it (via run_battery, below) rather than having grown
    # a second private copy back.
    assert recall.server_root_url is direct_env.server_root_url
    assert recall.server_root_url('http://172.17.0.1:8080/v1') == 'http://172.17.0.1:8080'


def test_parse_depth_plain_int():
    assert recall.parse_depth('4096') == 4096


def test_parse_depth_k_suffix():
    assert recall.parse_depth('128k') == 131072
    assert recall.parse_depth('4K') == 4096


def test_depth_seed_differs_per_depth():
    a = recall._depth_seed(1, 4096)
    b = recall._depth_seed(1, 16384)
    assert a != b


def test_depth_seed_deterministic():
    assert recall._depth_seed(7, 4096) == recall._depth_seed(7, 4096)


def test_build_question_lists_every_fact():
    facts = (
        haystack_mod.PlantedFact('E1', 'custodian', 'V1', 0.1, 10, 2),
        haystack_mod.PlantedFact('E2', 'clearance code', 'V2', 0.9, 90, 18),
    )
    q = recall._build_question(facts)
    assert 'E1' in q and 'custodian' in q
    assert 'E2' in q and 'clearance code' in q


# ---------------------------------------------------------------------------
# _first_tool_call: dict args, JSON-string args, wrong tool, absent
# ---------------------------------------------------------------------------

def test_first_tool_call_dict_args():
    message = {'tool_calls': [{'function': {'name': 'report_recall', 'arguments': {'answers': []}}}]}
    assert recall._first_tool_call(message) == {'answers': []}


def test_first_tool_call_json_string_args():
    message = {'tool_calls': [{'function': {'name': 'report_recall',
                                             'arguments': json.dumps({'answers': [{'entity': 'e'}]})}}]}
    assert recall._first_tool_call(message) == {'answers': [{'entity': 'e'}]}


def test_first_tool_call_ignores_other_tools():
    message = {'tool_calls': [{'function': {'name': 'something_else', 'arguments': {}}}]}
    assert recall._first_tool_call(message) is None


def test_first_tool_call_none_when_absent():
    assert recall._first_tool_call({}) is None


def test_first_tool_call_malformed_json_is_none():
    message = {'tool_calls': [{'function': {'name': 'report_recall', 'arguments': 'not json'}}]}
    assert recall._first_tool_call(message) is None


# ---------------------------------------------------------------------------
# _score_recall
# ---------------------------------------------------------------------------

def _fact(entity, attribute, value, pos=0.5):
    return haystack_mod.PlantedFact(entity, attribute, value, pos, 100, 25)


def test_score_recall_all_correct():
    facts = [_fact('E1', 'custodian', 'Val-100'), _fact('E2', 'storage tier', 'Val-200')]
    tool_args = {'answers': [
        {'entity': 'E1', 'attribute': 'custodian', 'value': 'Val-100'},
        {'entity': 'E2', 'attribute': 'storage tier', 'value': 'Val-200'},
    ]}
    results = recall._score_recall(facts, tool_args)
    assert all(r['correct'] for r in results)


def test_score_recall_wrong_value():
    facts = [_fact('E1', 'custodian', 'Val-100')]
    tool_args = {'answers': [{'entity': 'E1', 'attribute': 'custodian', 'value': 'Val-999'}]}
    results = recall._score_recall(facts, tool_args)
    assert results[0]['correct'] is False
    assert results[0]['recalledValue'] == 'Val-999'


def test_score_recall_missing_answer():
    facts = [_fact('E1', 'custodian', 'Val-100')]
    results = recall._score_recall(facts, {'answers': []})
    assert results[0]['correct'] is False
    assert results[0]['recalledValue'] is None


def test_score_recall_no_tool_call_at_all():
    facts = [_fact('E1', 'custodian', 'Val-100')]
    results = recall._score_recall(facts, None)
    assert results[0]['correct'] is False


def test_score_recall_trims_whitespace_but_is_case_sensitive():
    facts = [_fact('E1', 'custodian', 'Val-100')]
    ok = recall._score_recall(facts, {'answers': [{'entity': 'E1', 'attribute': 'custodian',
                                                     'value': '  Val-100  '}]})
    assert ok[0]['correct'] is True
    bad = recall._score_recall(facts, {'answers': [{'entity': 'E1', 'attribute': 'custodian',
                                                      'value': 'val-100'}]})
    assert bad[0]['correct'] is False


def test_score_recall_wrong_attribute_does_not_match():
    # A near-miss answer (right entity, wrong attribute) must not satisfy
    # the fact -- this is exactly the distinction #32's near-miss
    # distractors will probe on the question side.
    facts = [_fact('E1', 'custodian', 'Val-100')]
    results = recall._score_recall(facts, {'answers': [{'entity': 'E1', 'attribute': 'storage tier',
                                                          'value': 'Val-100'}]})
    assert results[0]['correct'] is False


# ---------------------------------------------------------------------------
# _score_recall / _score_abstention: issue #32's three/five-way classification
# ---------------------------------------------------------------------------

def test_score_recall_classifies_correct_answer():
    facts = [_fact('E1', 'custodian', 'Val-100')]
    results = recall._score_recall(facts, {'answers': [{'entity': 'E1', 'attribute': 'custodian',
                                                          'value': 'Val-100'}]})
    assert results[0]['classification'] == recall.CORRECT_ANSWER


def test_score_recall_classifies_wrong_answer():
    # A present fact answered with a DIFFERENT, non-abstention value is a
    # wrong answer, not a false abstention -- the model attempted to
    # recall and got it wrong, which is a different failure than claiming
    # the fact was never there.
    facts = [_fact('E1', 'custodian', 'Val-100')]
    results = recall._score_recall(facts, {'answers': [{'entity': 'E1', 'attribute': 'custodian',
                                                          'value': 'Val-999'}]})
    assert results[0]['classification'] == recall.WRONG_ANSWER


def test_score_recall_classifies_explicit_abstention_on_a_present_fact_as_false_abstention():
    # Over-abstaining must be visible: a model that says "not in context"
    # for something that WAS in the context is wrong in the opposite
    # direction from an invented answer, and #32 asks for it to be its
    # own bucket, not silently folded into "wrong answer".
    facts = [_fact('E1', 'custodian', 'Val-100')]
    results = recall._score_recall(facts, {'answers': [{'entity': 'E1', 'attribute': 'custodian',
                                                          'value': 'not in context'}]})
    assert results[0]['classification'] == recall.FALSE_ABSTENTION


def test_score_recall_classifies_missing_answer_on_a_present_fact_as_false_abstention():
    facts = [_fact('E1', 'custodian', 'Val-100')]
    results = recall._score_recall(facts, {'answers': []})
    assert results[0]['classification'] == recall.FALSE_ABSTENTION


def _abstention_question(entity, attribute, kind=abstain.KIND_GUARANTEED_ABSENT):
    return abstain.AbstentionQuestion(entity, attribute, kind)


def test_score_abstention_classifies_correct_abstention():
    questions = [_abstention_question('Ghost-Vault-1', 'custodian')]
    results = recall._score_abstention(questions, {'answers': [
        {'entity': 'Ghost-Vault-1', 'attribute': 'custodian', 'value': 'not in context'}]})
    assert results[0]['classification'] == recall.CORRECT_ABSTENTION


def test_score_abstention_classifies_missing_answer_as_correct_abstention():
    # The mirror image of the present-fact case: this pair was NEVER in
    # the document, so silence and an explicit "not in context" withhold
    # the same (nonexistent) value equally correctly.
    questions = [_abstention_question('Ghost-Vault-1', 'custodian')]
    results = recall._score_abstention(questions, {'answers': []})
    assert results[0]['classification'] == recall.CORRECT_ABSTENTION


def test_score_abstention_classifies_invented_answer():
    questions = [_abstention_question('Ghost-Vault-1', 'custodian')]
    results = recall._score_abstention(questions, {'answers': [
        {'entity': 'Ghost-Vault-1', 'attribute': 'custodian', 'value': 'Bogus-999'}]})
    assert results[0]['classification'] == recall.INVENTED_ANSWER


def test_present_question_counts_tally_all_three_classifications():
    fact_results = [
        {'classification': recall.CORRECT_ANSWER}, {'classification': recall.CORRECT_ANSWER},
        {'classification': recall.WRONG_ANSWER},
        {'classification': recall.FALSE_ABSTENTION},
    ]
    counts = recall._present_question_counts(fact_results)
    assert counts == {recall.CORRECT_ANSWER: 2, recall.WRONG_ANSWER: 1,
                       recall.FALSE_ABSTENTION: 1, 'total': 4}


def test_abstention_counts_tally_overall_and_by_kind():
    results = [
        {'classification': recall.CORRECT_ABSTENTION, 'kind': abstain.KIND_GUARANTEED_ABSENT},
        {'classification': recall.INVENTED_ANSWER, 'kind': abstain.KIND_GUARANTEED_ABSENT},
        {'classification': recall.CORRECT_ABSTENTION, 'kind': abstain.KIND_NEAR_MISS_SAME_ENTITY},
    ]
    counts = recall._abstention_counts(results)
    assert counts['overall'] == {recall.CORRECT_ABSTENTION: 2, recall.INVENTED_ANSWER: 1, 'total': 3}
    assert counts['byKind'][abstain.KIND_GUARANTEED_ABSENT] == {
        recall.CORRECT_ABSTENTION: 1, recall.INVENTED_ANSWER: 1, 'total': 2}
    assert counts['byKind'][abstain.KIND_NEAR_MISS_SAME_ENTITY] == {
        recall.CORRECT_ABSTENTION: 1, recall.INVENTED_ANSWER: 0, 'total': 1}
    assert counts['byKind'][abstain.KIND_NEAR_MISS_SAME_ATTRIBUTE] == {
        recall.CORRECT_ABSTENTION: 0, recall.INVENTED_ANSWER: 0, 'total': 0}


def test_build_question_lists_abstention_questions_too():
    facts = (haystack_mod.PlantedFact('E1', 'custodian', 'V1', 0.1, 10, 2),)
    questions = [_abstention_question('Ghost-Vault-1', 'clearance code')]
    q = recall._build_question(facts, questions)
    assert 'E1' in q and 'custodian' in q
    assert 'Ghost-Vault-1' in q and 'clearance code' in q


# ---------------------------------------------------------------------------
# _prompt_per_second / _predicted_per_second
# ---------------------------------------------------------------------------

def test_prompt_per_second_present():
    resp = {'timings': {'prompt_per_second': 500.0, 'predicted_per_second': 40.0}}
    assert recall._prompt_per_second(resp) == 500.0
    assert recall._predicted_per_second(resp) == 40.0


def test_prompt_per_second_absent():
    assert recall._prompt_per_second({}) is None
    assert recall._predicted_per_second({'timings': {}}) is None


# ---------------------------------------------------------------------------
# End-to-end against a fake server (no real llama-server)
# ---------------------------------------------------------------------------

import re  # noqa: E402


def _parse_requested_pairs(user_content):
    # Mirrors recall._build_question's `entity {e!r}, attribute {a!r}`
    # line shape -- lets the fake server answer EVERY pair actually asked
    # about (facts AND abstention questions) without the test needing to
    # separately compute recall.py's abstention rng stream.
    return re.findall(r"entity '([^']*)', attribute '([^']*)'", user_content)


class _State:
    def __init__(self):
        self.expected_by_text = {}
        self.requests = []
        self.send_timings = True
        self.tokenize_fails = False
        self.props_n_ctx = 1_000_000
        # How the fake server answers a requested pair that is NOT one of
        # the facts in expected_by_text (i.e. an abstention question):
        #   'correct'  -- answers with abstain.INSTRUCTED_PHRASE
        #   'invented' -- answers with a plausible-looking fabricated value
        #   'missing'  -- omits the pair from `answers` entirely
        self.abstention_answer_mode = 'correct'

    def tokenize_count(self, text):
        # A trivial, deterministic "tokenizer": 1 token per 4 chars,
        # rounded up. Its ONLY job is to be a pure function of `text` so
        # generate_haystack() produces the same result whether this test
        # calls it directly (to compute expected facts) or the fake HTTP
        # server calls it (when recall.py's own count_tokens_via_server
        # hits /tokenize).
        return max(1, (len(text) + 3) // 4)


def _make_handler(state):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _send_json(self, obj, status=200):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/props':
                self._send_json({'default_generation_settings': {'n_ctx': state.props_n_ctx}})
            elif self.path == '/v1/models':
                self._send_json({'data': []})
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length) if length else b''
            if self.path == '/tokenize':
                if state.tokenize_fails:
                    self.send_response(500)
                    self.end_headers()
                    return
                data = json.loads(body)
                n = state.tokenize_count(data.get('content', ''))
                self._send_json({'tokens': list(range(n))})
            elif self.path == '/v1/chat/completions':
                req = json.loads(body)
                state.requests.append(req)
                self._send_json(self._chat_response(req))
            else:
                self.send_response(404)
                self.end_headers()

        def _chat_response(self, req):
            user_content = req['messages'][1]['content']
            haystack_text = user_content.split('\n\nUsing report_recall')[0]
            facts = state.expected_by_text.get(haystack_text, [])
            fact_values = {(f.entity, f.attribute): f.value for f in facts}
            answers = []
            for entity, attribute in _parse_requested_pairs(user_content):
                key = (entity, attribute)
                if key in fact_values:
                    answers.append({'entity': entity, 'attribute': attribute, 'value': fact_values[key]})
                    continue
                # Not a planted fact -- this is one of recall.py's
                # abstention questions (haystack.py's PREAMBLE and filler
                # sentences never produce the "entity '...', attribute
                # '...'" line shape _parse_requested_pairs matches, only
                # _build_question's own question lines do).
                if state.abstention_answer_mode == 'missing':
                    continue
                value = ('not in context' if state.abstention_answer_mode == 'correct'
                         else 'Bogus-999')
                answers.append({'entity': entity, 'attribute': attribute, 'value': value})
            message = {
                'role': 'assistant', 'content': None,
                'tool_calls': [{'id': 'c1', 'type': 'function',
                                 'function': {'name': 'report_recall', 'arguments': json.dumps({'answers': answers})}}],
            }
            resp = {
                'choices': [{'message': message, 'finish_reason': 'tool_calls'}],
                'usage': {'prompt_tokens': 5000, 'completion_tokens': 50},
            }
            if state.send_timings:
                resp['timings'] = {'prompt_per_second': 321.0, 'predicted_per_second': 12.0}
            return resp

        def log_message(self, *a):
            pass
    return Handler


@pytest.fixture
def fake_env():
    state = _State()
    srv = http.server.HTTPServer(('127.0.0.1', 0), _make_handler(state))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base_url = f'http://127.0.0.1:{srv.server_port}/v1'
    yield base_url, state
    srv.shutdown()
    t.join(timeout=5)


def _precompute_expected(seed, depth_tokens, tokenize_fn):
    h = haystack_mod.generate_haystack(seed=recall._depth_seed(seed, depth_tokens),
                                        target_tokens=depth_tokens, count_tokens_fn=tokenize_fn)
    return h.text, h.facts


def test_run_battery_scores_full_recall_with_server_timings(fake_env):
    base_url, state = fake_env
    seed = 123
    depths = [200]
    text, facts = _precompute_expected(seed, 200, state.tokenize_count)
    state.expected_by_text[text] = facts

    report = recall.run_battery(base_url, 'test-model', depths, seed=seed, max_tokens=64, timeout=10)

    assert report['overall']['recallScore'] == 1.0
    timing = report['depths'][0]['timing']
    assert timing['prefillSource'] == 'server_timings'
    assert timing['decodeSource'] == 'server_timings'
    assert timing['prefillTokPerSec'] == 321.0
    assert timing['decodeTokPerSec'] == 12.0
    # Two calls every time -- call A (max_tokens=1, cold prefill) then
    # call B (the real, scored request) -- see measure_depth()'s docstring
    # on why call A can never be skipped even when the server reports
    # timings (it only ever produces one token, never enough to score).
    assert len(state.requests) == 2
    assert report['tokenizeAvailable'] is True
    assert report['depths'][0]['haystack']['tokenCountSource'] == haystack_mod.TOKEN_SOURCE_SERVER


def test_run_battery_falls_back_to_wall_clock_pair_without_timings(fake_env):
    base_url, state = fake_env
    state.send_timings = False
    seed = 5
    depths = [200]
    text, facts = _precompute_expected(seed, 200, state.tokenize_count)
    state.expected_by_text[text] = facts

    report = recall.run_battery(base_url, 'test-model', depths, seed=seed, max_tokens=64, timeout=10)

    d = report['depths'][0]
    assert d['timing']['prefillSource'] == 'wall_clock'
    assert d['timing']['decodeSource'] == 'wall_clock'
    assert d['timing']['prefillTokPerSec'] is not None
    assert d['timing']['decodeTokPerSec'] is not None
    # Two calls: call A (max_tokens=1, discarded) then call B (scored).
    assert len(state.requests) == 2
    assert state.requests[0]['max_tokens'] == 1
    assert state.requests[1]['max_tokens'] == 64
    assert d['recallScore'] == 1.0


def test_run_battery_skips_depth_exceeding_served_context(fake_env):
    base_url, state = fake_env
    state.props_n_ctx = 4096  # smaller than the requested depth below
    seed = 9
    depths = [8192]

    report = recall.run_battery(base_url, 'test-model', depths, seed=seed, max_tokens=64, timeout=10)

    assert report['depths'] == []
    assert len(report['depthsSkipped']) == 1
    assert report['depthsSkipped'][0]['depthTokens'] == 8192
    assert report['servedContextTokens'] == 4096
    assert report['overall']['depthsSkipped'] == 1


def test_run_battery_falls_back_to_chars_per_token_when_tokenize_unavailable(fake_env):
    base_url, state = fake_env
    state.tokenize_fails = True
    seed = 3
    depths = [200]
    text, facts = _precompute_expected(seed, 200, tokenize_fn=None)
    state.expected_by_text[text] = facts

    report = recall.run_battery(base_url, 'test-model', depths, seed=seed, max_tokens=64, timeout=10)

    assert report['tokenizeAvailable'] is False
    assert report['tokenizeUnavailableReason'] is not None
    assert report['depths'][0]['haystack']['tokenCountSource'] == haystack_mod.TOKEN_SOURCE_ESTIMATE
    assert report['depths'][0]['recallScore'] == 1.0


def test_run_battery_partial_recall_scores_fraction(fake_env):
    base_url, state = fake_env
    seed = 17
    depths = [200]
    text, facts = _precompute_expected(seed, 200, state.tokenize_count)
    # Only expose the first two facts to the fake server's answer key --
    # the rest come back missing, forcing a fractional score.
    state.expected_by_text[text] = facts[:2]

    report = recall.run_battery(base_url, 'test-model', depths, seed=seed, max_tokens=64, timeout=10)

    d = report['depths'][0]
    assert 0.0 < d['recallScore'] < 1.0
    assert any(not f['correct'] for f in d['facts'])


# ---------------------------------------------------------------------------
# Abstention, end-to-end (issue #32) -- same fake server, same haystack
# call, per recall.py's "share the haystack and prefill cost" design note
# ---------------------------------------------------------------------------

def test_run_battery_scores_correct_abstention_against_a_compliant_model(fake_env):
    base_url, state = fake_env
    state.abstention_answer_mode = 'correct'  # fake server answers INSTRUCTED_PHRASE
    seed = 21
    depths = [200]
    text, facts = _precompute_expected(seed, 200, state.tokenize_count)
    state.expected_by_text[text] = facts

    report = recall.run_battery(base_url, 'test-model', depths, seed=seed, max_tokens=64, timeout=10)

    d = report['depths'][0]
    assert d['abstention']['counts']['overall']['total'] == 6  # 3 kinds x default num_each=2
    assert d['abstention']['counts']['overall'][recall.CORRECT_ABSTENTION] == 6
    assert d['abstention']['counts']['overall'][recall.INVENTED_ANSWER] == 0
    assert all(q['kind'] in abstain.ALL_KINDS for q in d['abstention']['questions'])


def test_run_battery_scores_invented_answer_when_model_fabricates_absent_values(fake_env):
    base_url, state = fake_env
    state.abstention_answer_mode = 'invented'  # fake server fabricates a value instead
    seed = 23
    depths = [200]
    text, facts = _precompute_expected(seed, 200, state.tokenize_count)
    state.expected_by_text[text] = facts

    report = recall.run_battery(base_url, 'test-model', depths, seed=seed, max_tokens=64, timeout=10)

    counts = report['depths'][0]['abstention']['counts']['overall']
    assert counts[recall.INVENTED_ANSWER] == 6
    assert counts[recall.CORRECT_ABSTENTION] == 0


def test_run_battery_treats_a_missing_abstention_answer_as_correct_abstention(fake_env):
    base_url, state = fake_env
    state.abstention_answer_mode = 'missing'  # fake server omits absent pairs entirely
    seed = 29
    depths = [200]
    text, facts = _precompute_expected(seed, 200, state.tokenize_count)
    state.expected_by_text[text] = facts

    report = recall.run_battery(base_url, 'test-model', depths, seed=seed, max_tokens=64, timeout=10)

    counts = report['depths'][0]['abstention']['counts']['overall']
    assert counts[recall.CORRECT_ABSTENTION] == 6


def test_run_battery_flags_over_abstaining_on_present_facts(fake_env):
    # The acceptance criterion this exists for: a model that answers
    # EVERY pair (present or absent) with the abstention phrase must show
    # up as false-abstaining on the present facts, not score a clean
    # recall failure indistinguishable from "genuinely could not find it".
    base_url, state = fake_env
    seed = 31
    depths = [200]
    text, facts = _precompute_expected(seed, 200, state.tokenize_count)
    state.expected_by_text[text] = []  # server answers nothing correctly -- always abstains

    report = recall.run_battery(base_url, 'test-model', depths, seed=seed, max_tokens=64, timeout=10)

    present_counts = report['depths'][0]['presentQuestionCounts']
    assert present_counts[recall.FALSE_ABSTENTION] == len(facts)
    assert present_counts[recall.CORRECT_ANSWER] == 0
    assert report['depths'][0]['recallScore'] == 0.0
    # ... but abstention on the genuinely absent pairs is still correct --
    # this run's abstention half looks perfect even though recall is 0%,
    # which is exactly why the two halves are reported separately.
    assert report['depths'][0]['abstention']['counts']['overall'][recall.CORRECT_ABSTENTION] == 6


def test_run_battery_rolls_up_present_and_abstention_counts_into_overall(fake_env):
    base_url, state = fake_env
    seed = 37
    depths = [200]
    text, facts = _precompute_expected(seed, 200, state.tokenize_count)
    state.expected_by_text[text] = facts

    report = recall.run_battery(base_url, 'test-model', depths, seed=seed, max_tokens=64, timeout=10)

    o = report['overall']
    d = report['depths'][0]
    assert o['presentQuestionCounts'] == d['presentQuestionCounts']
    assert o['abstentionCounts'] == d['abstention']['counts']['overall']


def test_abstention_per_kind_cli_option_changes_question_count(fake_env):
    base_url, state = fake_env
    seed = 41
    depths = [200]
    text, facts = _precompute_expected(seed, 200, state.tokenize_count)
    state.expected_by_text[text] = facts

    report = recall.run_battery(base_url, 'test-model', depths, seed=seed, max_tokens=64,
                                 timeout=10, num_abstention_each=1)

    assert report['depths'][0]['abstention']['counts']['overall']['total'] == 3  # 3 kinds x 1


def test_report_includes_canary_caveat(fake_env):
    base_url, state = fake_env
    seed = 1
    depths = [200]
    text, facts = _precompute_expected(seed, 200, state.tokenize_count)
    state.expected_by_text[text] = facts

    report = recall.run_battery(base_url, 'test-model', depths, seed=seed, max_tokens=64, timeout=10)
    assert 'CANARY.md' in report['caveat']


def test_print_report_does_not_raise(fake_env, capsys):
    base_url, state = fake_env
    seed = 1
    depths = [200]
    text, facts = _precompute_expected(seed, 200, state.tokenize_count)
    state.expected_by_text[text] = facts

    report = recall.run_battery(base_url, 'test-model', depths, seed=seed, max_tokens=64, timeout=10)
    recall.print_report(report)
    out = capsys.readouterr().out
    assert 'OVERALL recall' in out
    assert 'caveat' in out
    assert 'OVERALL abstention' in out
    assert 'abstention:' in out  # per-depth line
