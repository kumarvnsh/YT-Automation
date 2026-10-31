import unittest
from src.claude_motion import sample_times, summarize_motion


class ContinuousMotionTests(unittest.TestCase):
    def test_opening_is_sampled_before_first_second(self):
        times = sample_times(6, 30, [])
        self.assertIn(.15, times)
        self.assertIn(.3, times)
        self.assertIn(.75, times)

    def test_two_second_hold_is_not_accepted_as_motion(self):
        self.assertIn('static_foreground', summarize_motion([0, 0, 0], [0, .75, 1.5, 2.25]))

    def test_continuously_changing_foreground_passes(self):
        self.assertEqual(summarize_motion([.01] * 4, [0, .75, 1.5, 2.25, 3]), [])
