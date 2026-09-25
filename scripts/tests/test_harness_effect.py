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


def test_prompt_inlines_document_only_for_direct_mode():
    cases = harness_effect.build_case_set(4096, 5)
    text = cases['haystack'].text
    direct_prompt = harness_effect.build_prompt(cases)
    harness_prompt = harness_effect.build_prompt(cases, ledger_path='/work/ledger.txt')
    assert text in direct_prompt
    assert text not in harness_prompt
    assert '/work/ledger.txt' in harness_prompt


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


def _known_answers(cases):
    return {'answers': [
        {'entity': f.entity, 'attribute': f.attribute, 'value': f.value}
        for f in cases['haystack'].facts
    ] + [
        {'entity': q.entity, 'attribute': q.attribute, 'value': 'not in context'}
        for q in cases['absent']
    ]}


def test_item_ids_and_per_depth_deltas_use_common_scored_cases(monkeypatch):
    cases = harness_effect.build_case_set(4096, 73)
    monkeypatch.setattr(harness_effect, 'build_case_set', lambda depth, seed: cases)
    direct_answers = _known_answers(cases)
    pi_answers = json.loads(json.dumps(direct_answers))
    pi_answers['answers'][0]['value'] = 'incorrect-answer'
    pi_answers['answers'][len(cases['haystack'].facts)]['value'] = 'invented-answer'

    def fake_run(mode, *args, **kwargs):
        if mode == 'direct':
            return direct_answers, 1.0
        if mode == 'pi':
            return pi_answers, 1.0
        raise RuntimeError('fake dsh failure')

    monkeypatch.setattr(harness_effect, 'run_harness', fake_run)
    report = harness_effect.run_comparison(
        [4096], 73, 'fake-model', base_url='http://127.0.0.1:18081/v1')
    depth = report['depths'][0]
    items = depth['items']
    assert len({item['itemId'] for item in items}) == len(items)
    assert all(len(item['itemId']) == 64 for item in items)
    assert depth['comparisons']['pi_vs_direct']['presentRecallDeltaPercentagePoints'] == -33.33
    assert depth['comparisons']['pi_vs_direct']['absentAbstentionDeltaPercentagePoints'] == -16.67
    assert depth['comparisons']['pi_vs_direct']['presentRecallCommonItemCount'] == len(cases['haystack'].facts)
    assert depth['comparisons']['pi_vs_direct']['absentAbstentionCommonItemCount'] == len(cases['absent'])
    assert depth['comparisons']['dsh_vs_direct']['presentRecallDeltaPercentagePoints'] is None
    assert depth['comparisons']['dsh_vs_direct']['absentAbstentionDeltaPercentagePoints'] is None
    assert all(item['modes']['dsh']['status'] == 'error' for item in items)
    overall = report['harnessEffect']['dsh_vs_direct']
    assert overall['leftStatus'] == 'partial'
    assert overall['presentRecallDeltaPercentagePoints'] is None

    artifact = json.dumps(report)
    for fact in cases['haystack'].facts:
        assert fact.entity not in artifact
        assert fact.attribute not in artifact
        assert fact.value not in artifact
    for question in cases['absent']:
        assert question.entity not in artifact
        assert question.attribute not in artifact
    assert 'incorrect-answer' not in artifact
    assert 'invented-answer' not in artifact


def test_unparseable_mode_makes_per_depth_delta_null(monkeypatch):
    cases = harness_effect.build_case_set(4096, 91)
    monkeypatch.setattr(harness_effect, 'build_case_set', lambda depth, seed: cases)

    def fake_run(mode, *args, **kwargs):
        return (None if mode == 'pi' else _known_answers(cases)), 1.0

    monkeypatch.setattr(harness_effect, 'run_harness', fake_run)
    report = harness_effect.run_comparison(
        [4096], 91, 'fake-model', base_url='http://127.0.0.1:18081/v1',
        modes=('direct', 'pi'))
    comparison = report['depths'][0]['comparisons']['pi_vs_direct']
    assert comparison['leftStatus'] == 'unparseable'
    assert comparison['presentRecallDeltaPercentagePoints'] is None
    assert comparison['absentAbstentionDeltaPercentagePoints'] is None


def test_aggregate_delta_uses_only_depths_scored_by_both_modes(monkeypatch):
    original_builder = harness_effect.build_case_set
    cases_by_depth = {}

    def build(depth, seed):
        cases_by_depth[depth] = original_builder(depth, seed)
        return cases_by_depth[depth]

    monkeypatch.setattr(harness_effect, 'build_case_set', build)
    calls_by_mode = {}

    def fake_run(mode, prompt, **kwargs):
        calls_by_mode[mode] = calls_by_mode.get(mode, 0) + 1
        if mode == 'dsh':
            if calls_by_mode[mode] == 2:
                raise RuntimeError('fake failure at second depth')
        depth = (4096, 8192)[calls_by_mode[mode] - 1]
        cases = cases_by_depth[depth]
        return _known_answers(cases), 1.0

    monkeypatch.setattr(harness_effect, 'run_harness', fake_run)
    report = harness_effect.run_comparison(
        [4096, 8192], 101, 'fake-model', base_url='http://127.0.0.1:18081/v1')
    aggregate = report['harnessEffect']['dsh_vs_direct']
    assert aggregate['leftStatus'] == 'partial'
    assert aggregate['presentRecallCommonItemCount'] == len(cases_by_depth[4096]['haystack'].facts)
    assert aggregate['absentAbstentionCommonItemCount'] == len(cases_by_depth[4096]['absent'])
    assert report['depths'][1]['comparisons']['dsh_vs_direct']['presentRecallDeltaPercentagePoints'] is None


@pytest.mark.parametrize('url', ['http://127.0.0.1:8080/v1', 'http://172.17.0.1:8080/v1'])
def test_reserved_8080_endpoint_is_rejected(url):
    with pytest.raises(ValueError, match='port 8080'):
        harness_effect._checked_base_url(url)


def test_missing_endpoint_is_rejected():
    with pytest.raises(ValueError, match='explicit --base-url'):
        harness_effect._checked_base_url(None)


@pytest.mark.parametrize('mode,entrypoint', [('pi', 'pi'), ('dsh', 'dsh')])
def test_default_harness_command_uses_benchmark_container_and_host_gateway(
        monkeypatch, mode, entrypoint):
    monkeypatch.delenv('OAKEN_IMAGE', raising=False)
    command = harness_effect._docker_command(
        mode, None, 'read /work/ledger.txt', 'fake-model', '/tmp/workspace', '/tmp/config')
    assert command[:3] == ['docker', 'run', '--rm']
    assert '--add-host=llama:host-gateway' in command
    assert command[command.index('--entrypoint') + 1] == entrypoint
    assert 'oaken-bench:1.0' in command
    assert command[-1] == 'read /work/ledger.txt'


def test_default_harness_command_passes_prompt_once(monkeypatch, tmp_path):
    prompt = 'read /work/ledger.txt'
    command = harness_effect._docker_command(
        'pi', None, prompt, 'fake-model', '/tmp/workspace', '/tmp/config')
    observed = {}

    def fake_run(argv, **kwargs):
        observed['argv'] = argv
        return subprocess.CompletedProcess(argv, 0, '', '')

    monkeypatch.setattr(harness_effect.subprocess, 'run', fake_run)
    harness_effect._run_command(command, prompt, 2, str(tmp_path), {})
    assert observed['argv'].count(prompt) == 1


def test_direct_mode_allows_reasoning_before_json(monkeypatch):
    observed = {}

    def fake_chat(*args, **kwargs):
        observed['max_tokens'] = kwargs['max_tokens']
        return {'choices': [{'message': {'content': '{"answers":[]}'}}]}

    monkeypatch.setattr(harness_effect.direct, 'chat', fake_chat)
    answers, _ = harness_effect.run_harness(
        'direct', 'question', model='fake-model',
        base_url='http://127.0.0.1:18081/v1', timeout=2)
    assert answers == {'answers': []}
    assert observed['max_tokens'] >= 4096


def test_over_context_depth_is_skipped_before_any_mode_runs(monkeypatch):
    def fail_if_run(*args, **kwargs):
        raise AssertionError('over-context case reached a model')

    monkeypatch.setattr(harness_effect, 'run_harness', fail_if_run)
    report = harness_effect.run_comparison(
        [4096], 7, 'fake-model', base_url='http://127.0.0.1:18081/v1',
        count_tokens_fn=lambda text: 6000, served_context=8192)
    depth = report['depths'][0]
    assert depth['validForComparison'] is False
    assert depth['directPromptTokenCount'] == 6000
    assert depth['modes']['direct']['status'] == 'skipped'
    assert report['byMode']['direct']['depthsScored'] == 0


@pytest.mark.parametrize('mode,config_path', [
    ('pi', '.pi/agent/models.json'), ('dsh', '.dsh/settings.yaml'),
])
def test_harness_adapter_rewrites_endpoint_in_temporary_config(monkeypatch, mode, config_path):
    endpoint = 'http://172.17.0.1:18081/v1'
    observed = {}

    def fake_run(argv, **kwargs):
        config = os.path.join(kwargs['env']['HOME'], config_path)
        with open(config, encoding='utf-8') as stream:
            contents = stream.read()
        observed['contents'] = contents
        observed['argv'] = argv
        with open(os.path.join(kwargs['cwd'], 'ledger.txt'), encoding='utf-8') as stream:
            observed['ledger'] = stream.read()
        return subprocess.CompletedProcess(argv, 0, '{"answers":[]}', '')

    monkeypatch.setattr(harness_effect.subprocess, 'run', fake_run)
    answers, elapsed = harness_effect.run_harness(
        mode, 'common prompt', model='fake-model', base_url=endpoint,
        timeout=2, command=['fake-harness'], ledger_text='private ledger text')
    assert answers == {'answers': []}
    assert elapsed >= 0
    assert endpoint in observed['contents']
    assert '8080' not in observed['contents']
    assert observed['ledger'] == 'private ledger text'


def test_dsh_qwen_config_does_not_duplicate_registered_model(monkeypatch):
    observed = {}

    def fake_run(argv, **kwargs):
        path = os.path.join(kwargs['env']['HOME'], '.dsh', 'settings.yaml')
        with open(path, encoding='utf-8') as stream:
            observed['settings'] = stream.read()
        return subprocess.CompletedProcess(argv, 0, '{"answers":[]}', '')

    monkeypatch.setattr(harness_effect.subprocess, 'run', fake_run)
    model = 'Qwen3.6-35B-A3B-UD-Q4_K_S.gguf'
    harness_effect.run_harness(
        'dsh', 'question', model=model,
        base_url='http://172.17.0.1:18081/v1', timeout=2,
        command=['fake-harness'], ledger_text='ledger')
    assert observed['settings'].count(f'- id: {model}') == 1
    assert f'  model: {model}' in observed['settings']
