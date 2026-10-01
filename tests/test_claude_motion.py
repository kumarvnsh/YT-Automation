"""Timing + parsing checks for claude_motion. No network, browser or ffmpeg."""
import unittest

from src.claude_motion import _SCENE_RE, segment_windows
from src.tts import WordTiming


class ClaudeMotionTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
