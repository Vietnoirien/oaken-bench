"""The follow-up cases must change inputs without breaking their tool dependencies."""
import sys
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import screen_followup  # noqa: E402


class FollowupCasesTests(unittest.TestCase):
    def test_seeds_change_prompts_and_dependency_values(self):
        first, second = (screen_followup.chain_cases(seed) for seed in screen_followup.SEEDS)
        self.assertEqual(len(first), 4)
        for original, a, b in zip(screen_followup.toolbattery.CHAIN_SCENARIOS, first, second):
            self.assertNotEqual(a.prompt, original.prompt)
            self.assertNotEqual(a.prompt, b.prompt)
            self.assertEqual(len(a.steps), len(original.steps))
            self.assertEqual([s.tool for s in a.steps], [s.tool for s in original.steps])
            prior_a = {}
            prior_b = {}
            for step_a, step_b in zip(a.steps, b.steps):
                for key, value in (step_a.dependency or {}).items():
                    self.assertEqual(value, prior_a[key])
                    self.assertNotEqual(value, step_b.dependency[key])
                    self.assertEqual(step_b.dependency[key], prior_b[key])
                prior_a.update(step_a.result)
                prior_b.update(step_b.result)

    def test_undeclared_seed_is_rejected(self):
        with self.assertRaises(ValueError):
            screen_followup.chain_cases(42)

    def test_disconnect_writes_metadata_only_partial_and_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'partial.json'
            with (patch.object(screen_followup, 'server_reachable', return_value=True),
                  patch.object(screen_followup, 'VramSampler'),
                  patch.object(screen_followup, 'build_environment', return_value={'backend': 'unknown'}),
                  patch.object(screen_followup.toolbattery, 'run_short_chains',
                               side_effect=ConnectionError('raw model reply SECRET')),
                  patch.object(screen_followup.recall, 'run_battery') as recall):
                status = screen_followup.main([
                    '--model', 'fixture', '--seed', '20260927', '--out', str(out)])
            self.assertEqual(status, 2)
            recall.assert_not_called()
            report = json.loads(out.read_text())
            self.assertTrue(report['incomplete'])
            self.assertEqual(report['runs'][0]['seed'], 20260927)
            self.assertEqual(report['runs'][0]['phase'], 'chains')
            self.assertEqual(report['runs'][0]['completedChains'], 0)
            self.assertIn('elapsedSeconds', report['runs'][0])
            self.assertNotIn('SECRET', out.read_text())


if __name__ == '__main__':
    unittest.main()
