"""Thumbnail prompt + failure-path checks. No network."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from src import thumbnail
from src.youtube_uploader import set_thumbnail


class _Cfg:
    def __init__(self, d):
        self._d = d

    def get(self, path, default=None):
        return self._d.get(path, default)


class ThumbnailTest(unittest.TestCase):
    def test_prompt_letters_exact_hook_and_differs_by_concept(self):
        a = thumbnail.build_prompt("The Train", "In 1964...", ("COFFEE WAS", "BANNED!"), "a")
        b = thumbnail.build_prompt("The Train", "In 1964...", ("COFFEE WAS", "BANNED!"), "b")
        self.assertIn('line 1 "COFFEE WAS" in white', a)
        self.assertIn('line 2 "BANNED!"', a)
        self.assertNotEqual(a, b)

    def test_all_generations_failing_returns_none(self):
        st = {"fmt": "short", "script": {"title": "T", "segments": [{"narration": "x"}]}}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(thumbnail, "_hook", return_value=("A", "B")), \
                mock.patch.object(thumbnail.imagegen, "render_prompt", return_value=None):
            self.assertIsNone(thumbnail.make_thumbnail(_Cfg({}), st, Path(d)))


    def test_jpeg_validation_rejects_truncated_png_and_tiny(self):
        with tempfile.TemporaryDirectory() as d:
            good, cut, png, tiny = (Path(d) / n for n in ("g.jpg", "c.jpg", "p.png", "t.jpg"))
            Image.new("RGB", (1024, 1536), "orange").save(good, "JPEG")
            cut.write_bytes(good.read_bytes()[:2000])
            Image.new("RGB", (1024, 1536)).save(png, "PNG")
            Image.new("RGB", (100, 100)).save(tiny, "JPEG")
            self.assertTrue(thumbnail.is_valid_jpeg(good))
            for bad in (cut, png, tiny, Path(d) / "missing.jpg"):
                self.assertFalse(thumbnail.is_valid_jpeg(bad), bad.name)

    def test_set_thumbnail_retries_then_succeeds_and_skips_corrupt(self):
        yt = mock.MagicMock()
        yt.thumbnails().set().execute.side_effect = [
            RuntimeError("video not ready"),
            {"items": [{"maxres": {"url": "https://i.ytimg.com/x.jpg"}}]},
        ]
        with tempfile.TemporaryDirectory() as d, mock.patch("time.sleep"):
            good, cut = Path(d) / "g.jpg", Path(d) / "c.jpg"
            Image.new("RGB", (1024, 1536), "orange").save(good, "JPEG")
            cut.write_bytes(good.read_bytes()[:2000])
            self.assertTrue(set_thumbnail(yt, "vid", good))
            calls = yt.thumbnails().set().execute.call_count
            self.assertFalse(set_thumbnail(yt, "vid", cut))
            self.assertEqual(yt.thumbnails().set().execute.call_count, calls)  # never sent


if __name__ == "__main__":
    unittest.main()
