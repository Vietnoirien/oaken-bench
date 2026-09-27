"""The follow-up cases must change inputs without breaking their tool dependencies."""
import sys
import unittest
from pathlib import Path

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


if __name__ == '__main__':
    unittest.main()
