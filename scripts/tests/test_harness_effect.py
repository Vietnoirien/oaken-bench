"""Fake-only tests for the shared T0.5 harness comparison."""
import json
import os
import sys
import subprocess

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import harness_effect  # noqa: E402
import recall  # noqa: E402


def test_case_set_is_reproducible_and_contains_guaranteed_absence():
    a = harness_effect.build_case_set(4096, 42)
    b = harness_effect.build_case_set(4096, 42)
    assert a['digest'] == b['digest']
    assert a['haystack'].text == b['haystack'].text
    pairs = {(f.entity, f.attribute) for f in a['haystack'].facts}
    assert all((q.entity, q.attribute) not in a['haystack'].pairs for q in a['absent'])
    assert pairs.isdisjoint({(q.entity, q.attribute) for q in a['absent']})


def test_parse_answers_accepts_fenced_or_surrounded_json():
    expected = {'answers': [{'entity': 'E1', 'attribute': 'custodian', 'value': 'Kestrel-482'}]}
    assert harness_effect.parse_answers(json.dumps(expected)) == expected
    assert harness_effect.parse_answers('answer:\n```json\n' + json.dumps(expected) + '\n```') == expected
    assert harness_effect.parse_answers('no structured answer') is None


def test_scoring_discards_answer_values():
    cases = harness_effect.build_case_set(4096, 2)
    facts = cases['haystack'].facts
    answers = {'answers': [
        {'entity': f.entity, 'attribute': f.attribute, 'value': f.value}
        for f in facts
    ] + [
        {'entity': q.entity, 'attribute': q.attribute, 'value': 'not in context'}
        for q in cases['absent']
    ]}
    scored = harness_effect.score_answers(cases, answers)
    serialized = json.dumps(scored)
    assert facts[0].value not in serialized
    assert scored['present'][recall.CORRECT_ANSWER] == len(facts)
    assert scored['absent'][recall.CORRECT_ABSTENTION] == len(cases['absent'])


@pytest.mark.parametrize('url', ['http://127.0.0.1:8080/v1', 'http://172.17.0.1:8080/v1'])
def test_reserved_8080_endpoint_is_rejected(url):
    with pytest.raises(ValueError, match='port 8080'):
        harness_effect._checked_base_url(url)


def test_missing_endpoint_is_rejected():
    with pytest.raises(ValueError, match='explicit --base-url'):
        harness_effect._checked_base_url(None)


@pytest.mark.parametrize('mode,config_path', [
    ('pi', '.pi/agent/models.json'), ('dsh', '.dsh/settings.yaml'),
])
def test_harness_adapter_rewrites_endpoint_in_temporary_config(monkeypatch, mode, config_path):
    endpoint = 'http://127.0.0.1:18081/v1'
    observed = {}

    def fake_run(argv, **kwargs):
        config = os.path.join(kwargs['env']['HOME'], config_path)
        with open(config, encoding='utf-8') as stream:
            contents = stream.read()
        observed['contents'] = contents
        observed['argv'] = argv
        return subprocess.CompletedProcess(argv, 0, '{"answers":[]}', '')

    monkeypatch.setattr(harness_effect.subprocess, 'run', fake_run)
    answers, elapsed = harness_effect.run_harness(
        mode, 'common prompt', model='fake-model', base_url=endpoint,
        timeout=2, command=['fake-harness'])
    assert answers == {'answers': []}
    assert elapsed >= 0
    assert endpoint in observed['contents']
    assert '8080' not in observed['contents']
