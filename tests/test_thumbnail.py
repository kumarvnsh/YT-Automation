"""Thumbnail prompt + failure-path checks. No network."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import thumbnail


class _Cfg:
    def __init__(self, d):
        self._d = d

    def get(self, path, default=None):
        return self._d.get(path, default)


class ThumbnailTest(unittest.TestCase):
    def test_prompts_use_channel_style_and_differ_by_concept(self):
        cfg = _Cfg({"assets.ai_images.style": "histold_hybrid"})
        a = thumbnail.build_prompt(cfg, "The Train", "In 1964...", "a")
        b = thumbnail.build_prompt(cfg, "The Train", "In 1964...", "b")
        self.assertIn("propaganda", a)
        self.assertIn("Text-led", a)
        self.assertIn("no text at all", b)

    def test_all_generations_failing_returns_none(self):
        st = {"fmt": "short", "script": {"title": "T", "segments": [{"narration": "x"}]}}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(thumbnail.imagegen, "render_prompt", return_value=None):
            self.assertIsNone(thumbnail.make_thumbnail(_Cfg({}), st, Path(d)))


if __name__ == "__main__":
    unittest.main()
