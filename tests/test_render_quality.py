import unittest
from src.claude_motion import sample_times, summarize_motion


class RenderQualityTests(unittest.TestCase):
    def test_samples_include_cues_and_actual_final_frame(self):
        samples = sample_times(5.0, 30, [{'time': 2.0}])
        self.assertIn(0.0, samples)
        self.assertIn(2.0, samples)
        self.assertAlmostEqual(max(samples), 149/30)

    def test_foreground_inactivity_is_warning(self):
        report = summarize_motion([0.0, 0.00001, 0.0], [0.0, 2.0, 4.0, 6.0])
        self.assertIn('static_foreground', report)
