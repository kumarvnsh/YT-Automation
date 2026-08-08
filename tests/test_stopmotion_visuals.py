"""Checks for the animated/hybrid visual feature: style presets, prompt wiring,
and crossfade offset math. No ffmpeg or network — pure logic only."""
import unittest

from src import imagegen
from src.video_builder import _xfade_offsets


class _Cfg:
    def __init__(self, d):
        self._d = d

    def get(self, path, default=None):
        return self._d.get(path, default)


class VisualStylesTest(unittest.TestCase):
    def test_new_style_presets_exist(self):
        for name in ("histold_hybrid", "whiteboard_vox", "animated", "vox",
                     "claymation", "graphic_novel", "propaganda_poster",
                     "woodcut", "watercolor", "noir"):
            self.assertIn(name, imagegen.STYLE_PRESETS, name)

    def test_build_prompt_uses_selected_style(self):
        cfg = _Cfg({"assets.ai_images.style": "histold_hybrid"})
        prompt = imagegen.build_prompt(
            cfg, "A fast ocean liner crosses the sea.", ["ocean liner"], variant=0
        ).lower()
        self.assertIn("graphic novel", prompt)
        self.assertIn("propaganda", prompt)


class XfadeOffsetTest(unittest.TestCase):
    def test_offsets_accumulate_and_shorten(self):
        offs = _xfade_offsets([5.0, 5.0, 5.0], 0.4)
        self.assertEqual(offs, [4.6, 9.2])  # sum(d[0..j-1]) - j*fade
        # Final length = last offset + fade + tail = total - (n-1)*fade.
        self.assertAlmostEqual(offs[-1] + 0.4 + (5.0 - 0.4), 15.0 - 2 * 0.4)

    def test_single_clip_has_no_offsets(self):
        self.assertEqual(_xfade_offsets([5.0], 0.4), [])


if __name__ == "__main__":
    unittest.main()
