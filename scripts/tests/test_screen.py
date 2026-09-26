"""Screen contract tests use synthetic scores; they are not calibration data."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import screen  # noqa: E402
import recall  # noqa: E402


def thresholds(status='calibrated'):
    return {'revision': 'synthetic-test-1', 'status': status,
            'calibrationEvidence': ['synthetic fixture'] if status == 'calibrated' else None,
            'minimums': {key: 0.5 if status == 'calibrated' else None for key in screen.METRICS}}


def reports():
    t0 = {'dimensions': {
        name: {'score': 0.8, 'depth': {'score': 0.75}} for name in
        ('schemaAdherence', 'toolSelection', 'shortChains', 'refusal')}}
    t05 = {'overall': {'recallScore': 0.67,
                       'abstentionCounts': {'correct_abstention': 2, 'total': 3}}}
    return t0, t05


class ScreenTests(unittest.TestCase):
    def test_recall_output_limit_does_not_become_false_abstention(self):
        response = {'choices': [{'finish_reason': 'length', 'message': {
            'content': '', 'reasoning_content': 'unfinished reasoning'}}]}
        timing = {'completionTokens': 64}
        with patch.object(recall, 'measure_depth', return_value=(response, timing)):
            depth = recall.run_depth('http://localhost:8082/v1', 'http://localhost:8082',
                                     'test', 200, 1, max_tokens=64, timeout=10,
                                     api_key=None, chars_per_token=4, count_tokens_fn=None)
        self.assertTrue(depth['outputTruncated'])
        self.assertNotIn('recallScore', depth)
        self.assertNotIn('presentQuestionCounts', depth)

    def test_chain_output_limit_marks_screen_incomplete(self):
        with patch.object(screen.toolbattery, '_run_case', return_value={'finish_reason': 'length'}):
            case = screen.toolbattery._run_chain(
                'http://localhost:8082/v1', 'test', 8192, 10, [],
                screen.toolbattery.CHAIN_SCENARIOS[0])
        self.assertTrue(case['outputTruncated'])
        self.assertEqual(case['brokenAtLevel'], 'outputTruncated')

    def test_committed_thresholds_have_calibration_evidence(self):
        data = screen.load_thresholds(screen.DEFAULT_THRESHOLDS)
        self.assertEqual(data['status'], 'calibrated')
        self.assertTrue(data['calibrationEvidence'])
        self.assertTrue(all(0 <= v <= 1 for v in data['minimums'].values()))

    def test_revision_and_minimums_are_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'thresholds.json'
            data = thresholds()
            del data['minimums']['refusal']
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                screen.load_thresholds(path)
            data = thresholds()
            data['minimums']['refusal'] = None
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                screen.load_thresholds(path)

    def test_synthetic_go_no_go_and_incomplete(self):
        t0, t05 = reports()
        metrics = screen.extract_metrics(t0, t05)
        self.assertEqual(screen.judge(metrics, thresholds(), complete=True)['verdict'], 'go')
        metrics['recall'] = 0.4
        decision = screen.judge(metrics, thresholds(), complete=True)
        self.assertEqual(decision['verdict'], 'no_go')
        self.assertFalse(decision['checks']['recall']['passed'])
        self.assertEqual(screen.judge(metrics, thresholds(), complete=False)['verdict'], 'incomplete')
        self.assertEqual(screen.judge(metrics, thresholds('pending_calibration'),
                                      complete=True)['verdict'], 'unverified')
        self.assertEqual(screen.judge(metrics, thresholds('pending_calibration'),
                                      complete=False)['verdict'], 'incomplete')

    def test_missing_observation_cannot_pass(self):
        t0, t05 = reports()
        metrics = screen.extract_metrics(t0, t05)
        metrics['abstention'] = None
        self.assertEqual(screen.judge(metrics, thresholds(), complete=True)['verdict'], 'incomplete')

    def test_screen_uses_all_four_chains(self):
        fake_dim = {'score': 1, 'notAttempted': 0, 'depth': {'score': 1}, 'cases': []}
        fake_t05 = {'overall': {'recallScore': 1, 'depthsAttempted': 1,
                                'depthsSkipped': 0,
                                'abstentionCounts': {'correct_abstention': 3, 'total': 3}}}
        class Sampler:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def peak_mib(self):
                return None
        with patch.object(screen, 'VramSampler', return_value=Sampler()), \
             patch.object(screen, 'build_environment', return_value={}), \
             patch.object(screen.toolbattery, 'run_schema_adherence', return_value=fake_dim), \
             patch.object(screen.toolbattery, 'run_tool_selection', return_value=fake_dim), \
             patch.object(screen.toolbattery, 'run_refusal', return_value=fake_dim), \
             patch.object(screen.toolbattery, 'run_short_chains', return_value=fake_dim) as chains, \
             patch.object(screen.recall, 'run_battery', return_value=fake_t05) as t05:
            report = screen.run_screen('http://localhost:8082/v1', 'test', thresholds())
        self.assertEqual(len(chains.call_args.kwargs['scenarios']), 4)
        self.assertEqual(t05.call_args.args[2], [16384])
        self.assertEqual(t05.call_args.kwargs['max_tokens'], 16384)
        self.assertEqual(report['assessment']['verdict'], 'go')
        self.assertEqual(report['protocolVersion'], 4)
        self.assertEqual(report['thresholdRevision'], 'synthetic-test-1')

    def test_cli_writes_revision_and_returns_go_exit_code(self):
        report = {'label': 'screen-test-20260925T000000Z', 'model': 'test',
                  'elapsedSeconds': 1.0, 'thresholdRevision': 'screen-v4-2026-09-26',
                  'assessment': {'verdict': 'go'}}
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(screen, 'server_reachable', return_value=True), \
             patch.object(screen, 'run_screen', return_value=report):
            out = Path(tmp) / 'screen.json'
            code = screen.main(['--model', 'test', '--out', str(out)])
            saved = json.loads(out.read_text())
        self.assertEqual(code, 0)
        self.assertEqual(saved['thresholdRevision'], 'screen-v4-2026-09-26')


if __name__ == '__main__':
    unittest.main()
