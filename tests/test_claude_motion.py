"""Timing + parsing checks for claude_motion. No network, browser or ffmpeg."""
import unittest
from unittest.mock import patch

from src.claude_motion import _EXAMPLE, _SCENE_RE, _SYSTEM, _check_and_repair, inject_kit, segment_windows
from src.config import Config
from src.tts import WordTiming


class ClaudeMotionTest(unittest.TestCase):
    def test_one_based_scene_ids_are_normalized_without_losing_hook(self):
        from src.claude_motion import parse_scenes
        raw = '<scene index="1"><html>hook</html></scene><scene index="2"><html>end</html></scene>'
        self.assertEqual(parse_scenes(raw, 2), {0:'<html>hook</html>',1:'<html>end</html>'})

    def test_scene_parser_accepts_single_quoted_indices(self):
        from src.claude_motion import parse_scenes
        self.assertEqual(parse_scenes("<scene index='0'><html>hook</html></scene>",1),{0:'<html>hook</html>'})

    def test_house_recovery_uses_vetted_maps_and_narration_only(self):
        from src.claude_motion import house_scene
        from src.map_assets import load_map
        html = house_scene('This American town requires a border detour.',
                           [load_map('canada'),load_map('washington'),load_map('united_states')])
        self.assertIn('data-map-id="canada"',html)
        self.assertIn('data-map-id="washington"',html)
        self.assertNotIn('<histold-map',html)
        self.assertIn('This American town',html)

    def test_windows_follow_word_timings_and_cover_total(self):
        words = [WordTiming(f"w{i}", i * 0.5, i * 0.5 + 0.4) for i in range(10)]
        win = segment_windows(["a b c d", "e f", "g h i j"], words, 6.0)
        self.assertEqual(win[0][0], 0.0)
        self.assertEqual(win[1][0], 2.0)   # starts at word 4
        self.assertEqual(win[2][0], 3.0)   # starts at word 6
        self.assertEqual(win[-1][1], 6.0)
        for (_, e), (s, _) in zip(win, win[1:]):
            self.assertEqual(e, s)        # contiguous, no gaps

    def test_scene_parsing(self):
        text = '<scene index="0"><!doctype html><p>a</p></scene>\n<scene index="1">\n<html>b</html>\n</scene>'
        self.assertEqual(dict((int(i), h) for i, h in _SCENE_RE.findall(text)),
                         {0: "<!doctype html><p>a</p>", 1: "<html>b</html>"})


    def test_kit_goes_first_in_head_so_scene_css_wins(self):
        out = inject_kit('<!doctype html><html><HEAD lang="en"><style>.x{}</style></head></html>')
        self.assertLess(out.index(".k-stage"), out.index(".x{}"))
        self.assertEqual(out.count("<style>"), 2)

    def test_kit_added_when_scene_has_no_head(self):
        out = inject_kit("<!DOCTYPE html><body><p>a</p></body>")
        self.assertTrue(out.startswith("<!doctype html><html><head>"))
        self.assertEqual(out.lower().count("<!doctype"), 1)
        self.assertIn(".k-stage", out)

    def test_system_prompt_formats_with_example(self):
        text = _SYSTEM.format(w=540, h=960, cap_y=672, w_text=468, example=_EXAMPLE.strip())
        self.assertIn("k-count", text)
        self.assertIn("y > 672px", text)


    def test_repair_only_adopted_when_it_lints_cleaner(self):
        # Arrange: scene 0 clean, scene 1 and 2 flagged; repair fixes 1, worsens 2
        lints = {"ok": [], "bad1": ["a overlaps b", "c cut off"], "bad2": ["x overlaps y"],
                 "fix1": [], "fix2": ["x overlaps y", "z cut off"]}
        fake_lint = lambda _page, html: next(v for k, v in lints.items() if f">{k}<" in html)
        raw = {0: "<p>ok</p>", 1: "<p>bad1</p>", 2: "<p>bad2</p>"}
        with patch("src.claude_motion._lint", side_effect=fake_lint), \
             patch("src.claude_motion._repair_scenes",
                   return_value={1: "<p>fix1</p>", 2: "<p>fix2</p>"}) as repair:
            # Act
            out = _check_and_repair(Config({}), None, raw, 540, 960)
        # Assert
        self.assertEqual(sorted(repair.call_args.args[2]), [1, 2])   # only flagged scenes sent
        self.assertEqual(out, {0: "<p>ok</p>", 1: "<p>fix1</p>", 2: "<p>bad2</p>"})

    def test_repair_failure_keeps_original_scenes(self):
        raw = {0: "<p>bad</p>"}
        with patch("src.claude_motion._lint", return_value=["a overlaps b"]), \
             patch("src.claude_motion._repair_scenes", side_effect=RuntimeError("api down")):
            self.assertEqual(_check_and_repair(Config({}), None, raw, 540, 960), raw)


if __name__ == "__main__":
    unittest.main()
