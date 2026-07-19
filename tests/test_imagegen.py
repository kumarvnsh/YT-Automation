"""Per-segment AI illustration generation, and its fallback guarantees.

Two properties matter more than the feature itself:
  1. A channel with ai_images off must never reach the generator.
  2. A generation failure must fall back to stock, never break a run.
"""
from __future__ import annotations

import base64
import pathlib
import re
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from src.config import Config
from src import assets, imagegen
from src.script_generator import Segment

NARRATION = "The Titanic received six iceberg warnings the day it sank."
KEYWORDS = ["ocean liner", "iceberg", "north atlantic"]


def _cfg(**ai) -> Config:
    block = {"enabled": True, "style": "illustrative"}
    block.update(ai)
    return Config({"assets": {"ai_images": block, "per_segment_clips": 3}})


class EnabledFlagTests(unittest.TestCase):
    def test_off_by_default(self) -> None:
        self.assertFalse(imagegen.enabled(Config({})))

    def test_off_when_block_present_but_disabled(self) -> None:
        self.assertFalse(imagegen.enabled(_cfg(enabled=False)))

    def test_on_when_enabled(self) -> None:
        self.assertTrue(imagegen.enabled(_cfg()))


class BeatSelectionTests(unittest.TestCase):
    def test_no_beats_configured_means_every_segment(self) -> None:
        cfg = _cfg()
        for beat in ("hook", "setup", "pivot", "fact", "callback", ""):
            self.assertTrue(imagegen.wanted_for_beat(cfg, beat))

    def test_configured_beats_are_selected(self) -> None:
        cfg = _cfg(beats=["pivot", "fact"])
        self.assertTrue(imagegen.wanted_for_beat(cfg, "pivot"))
        self.assertTrue(imagegen.wanted_for_beat(cfg, "fact"))

    def test_unconfigured_beats_keep_stock_footage(self) -> None:
        cfg = _cfg(beats=["pivot", "fact"])
        for beat in ("hook", "setup", "callback"):
            self.assertFalse(imagegen.wanted_for_beat(cfg, beat))

    def test_untagged_segment_keeps_stock_when_beats_configured(self) -> None:
        self.assertFalse(imagegen.wanted_for_beat(_cfg(beats=["fact"]), ""))

    def test_beat_matching_ignores_case_and_padding(self) -> None:
        self.assertTrue(imagegen.wanted_for_beat(_cfg(beats=[" Fact "]), "fact"))


class SelectSegmentsTests(unittest.TestCase):
    BEATS = ["hook", "setup", "pivot", "fact", "fact", "fact", "fact", "callback"]

    def test_no_cap_selects_every_eligible_beat(self) -> None:
        cfg = _cfg(beats=["pivot", "fact"])
        self.assertEqual({2, 3, 4, 5, 6}, imagegen.select_segments(cfg, self.BEATS))

    def test_cap_of_two_keeps_pivot_and_final_fact(self) -> None:
        cfg = _cfg(beats=["pivot", "fact"], max_images=2)
        self.assertEqual({2, 6}, imagegen.select_segments(cfg, self.BEATS))

    def test_cap_of_one_keeps_the_pivot(self) -> None:
        cfg = _cfg(beats=["pivot", "fact"], max_images=1)
        self.assertEqual({2}, imagegen.select_segments(cfg, self.BEATS))

    def test_cap_spreads_rather_than_taking_the_front(self) -> None:
        """Three images should not all land in the first half of the short."""
        cfg = _cfg(beats=["pivot", "fact"], max_images=3)
        chosen = sorted(imagegen.select_segments(cfg, self.BEATS))
        self.assertEqual([2, 4, 6], chosen)

    def test_cap_larger_than_eligible_is_harmless(self) -> None:
        cfg = _cfg(beats=["pivot", "fact"], max_images=99)
        self.assertEqual({2, 3, 4, 5, 6}, imagegen.select_segments(cfg, self.BEATS))

    def test_zero_cap_means_unlimited(self) -> None:
        cfg = _cfg(beats=["pivot", "fact"], max_images=0)
        self.assertEqual({2, 3, 4, 5, 6}, imagegen.select_segments(cfg, self.BEATS))

    def test_shorter_fact_stack_still_caps_at_two(self) -> None:
        beats = ["hook", "setup", "pivot", "fact", "fact", "fact", "callback"]
        cfg = _cfg(beats=["pivot", "fact"], max_images=2)
        self.assertEqual({2, 5}, imagegen.select_segments(cfg, beats))

    def test_untagged_segments_select_nothing_when_beats_configured(self) -> None:
        cfg = _cfg(beats=["pivot", "fact"], max_images=2)
        self.assertEqual(set(), imagegen.select_segments(cfg, [""] * 6))


class PromptTests(unittest.TestCase):
    def test_prompt_carries_subject_and_keywords(self) -> None:
        p = imagegen.build_prompt(_cfg(), NARRATION, KEYWORDS)
        self.assertIn("Titanic", p)
        self.assertIn("ocean liner", p)

    def test_default_style_is_explicitly_not_photographic(self) -> None:
        """The illustrative default is what keeps us outside YouTube's
        synthetic-content disclosure requirement."""
        p = imagegen.build_prompt(_cfg(), NARRATION, KEYWORDS)
        self.assertIn("not a photograph", p)

    def test_photoreal_style_is_opt_in(self) -> None:
        p = imagegen.build_prompt(_cfg(style="photoreal"), NARRATION, KEYWORDS)
        self.assertIn("photorealistic", p)
        self.assertNotIn("not a photograph", p)

    def test_unknown_style_falls_back_to_illustrative(self) -> None:
        p = imagegen.build_prompt(_cfg(style="nonsense"), NARRATION, KEYWORDS)
        self.assertIn("not a photograph", p)

    def test_spoken_filler_is_stripped(self) -> None:
        """'But here's the twist' is a narration device, not a drawable thing."""
        p = imagegen.build_prompt(
            _cfg(), "But here's the twist. The ship was secretly a warship.", []
        )
        self.assertNotIn("here's the twist", p.lower())
        self.assertIn("secretly a warship", p)

    def test_prompt_reserves_the_caption_area_and_bans_modern_text(self) -> None:
        p = imagegen.build_prompt(_cfg(), NARRATION, KEYWORDS)
        self.assertIn("lower third", p)
        self.assertIn("No modern text", p)

    def test_inscriptions_are_required_to_be_illegible(self) -> None:
        """A blanket text ban fails when the subject is writing itself; the
        model invents glyphs regardless, so require them to be unreadable."""
        p = imagegen.build_prompt(_cfg(), NARRATION, KEYWORDS)
        self.assertIn("illegible", p)

    def test_composition_rotates_across_segments(self) -> None:
        """Consecutive fact beats must not come back as the same shot."""
        prompts = [
            imagegen.build_prompt(_cfg(), NARRATION, KEYWORDS, variant=i)
            for i in range(5)
        ]
        self.assertEqual(5, len(set(prompts)), "each variant needs a distinct framing")

    def test_composition_wraps_past_the_preset_count(self) -> None:
        n = len(imagegen._COMPOSITIONS)
        first = imagegen.build_prompt(_cfg(), NARRATION, KEYWORDS, variant=0)
        wrapped = imagegen.build_prompt(_cfg(), NARRATION, KEYWORDS, variant=n)
        self.assertEqual(first, wrapped)

    def test_long_narration_is_truncated(self) -> None:
        # Count the exact token: the boilerplate itself contains "words".
        p = imagegen.build_prompt(_cfg(), " ".join(["zebra"] * 200), [])
        self.assertLessEqual(len(re.findall(r"\bzebra\b", p)), 40)


class GenerateFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = pathlib.Path(self.tmp.name) / "seg_00_ai.png"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_missing_api_key_returns_none(self) -> None:
        with patch("src.imagegen.env", return_value=""):
            self.assertIsNone(
                imagegen.generate(_cfg(), NARRATION, KEYWORDS, self.dest)
            )

    def _with_fake_openai(self, factory):
        """Install a stub `openai` module for the duration of a call.

        The real package is an optional dependency, so these paths have to be
        exercised without it being importable.
        """
        module = types.ModuleType("openai")
        module.OpenAI = factory
        return patch.dict(sys.modules, {"openai": module})

    def test_api_error_returns_none_instead_of_raising(self) -> None:
        def boom(api_key=None):
            raise RuntimeError("rate limited")

        with patch("src.imagegen.env", return_value="key"), self._with_fake_openai(boom):
            self.assertIsNone(
                imagegen.generate(_cfg(), NARRATION, KEYWORDS, self.dest)
            )

    def test_missing_openai_package_returns_none(self) -> None:
        with patch("src.imagegen.env", return_value="key"), patch.dict(
            sys.modules, {"openai": None}
        ):
            self.assertIsNone(
                imagegen.generate(_cfg(), NARRATION, KEYWORDS, self.dest)
            )

    def test_successful_generation_writes_the_file(self) -> None:
        class FakeClient:
            def __init__(self, api_key=None, timeout=None, max_retries=None):
                self.images = self

            def generate(self, **kwargs):
                payload = types.SimpleNamespace(
                    b64_json=base64.b64encode(b"fake-png-bytes").decode()
                )
                return types.SimpleNamespace(data=[payload])

        with patch("src.imagegen.env", return_value="key"), self._with_fake_openai(
            FakeClient
        ):
            out = imagegen.generate(_cfg(), NARRATION, KEYWORDS, self.dest)

        self.assertEqual(self.dest, out)
        self.assertEqual(b"fake-png-bytes", self.dest.read_bytes())

    def test_empty_response_returns_none(self) -> None:
        class EmptyClient:
            def __init__(self, api_key=None, timeout=None, max_retries=None):
                self.images = self

            def generate(self, **kwargs):
                return types.SimpleNamespace(
                    data=[types.SimpleNamespace(b64_json=None, url=None)]
                )

        with patch("src.imagegen.env", return_value="key"), self._with_fake_openai(
            EmptyClient
        ):
            self.assertIsNone(
                imagegen.generate(_cfg(), NARRATION, KEYWORDS, self.dest)
            )
        self.assertFalse(self.dest.exists())


class AssetCascadeTests(unittest.TestCase):
    """fetch_for_segments must route correctly for both channels."""

    segs = [Segment(NARRATION, KEYWORDS, "hook")]

    def _run(self, cfg, gen):
        with tempfile.TemporaryDirectory() as d:
            with patch("src.imagegen.generate", side_effect=gen) as g, patch(
                "src.assets._fetch_one", return_value=[]
            ) as pex, patch(
                "src.assets._solid_clip",
                side_effect=lambda p, s, o: (
                    p.parent.mkdir(parents=True, exist_ok=True),
                    p.write_text("x"),
                    p,
                )[-1],
            ):
                out = assets.fetch_for_segments(cfg, self.segs, pathlib.Path(d), "short")
            return g.call_count, pex.call_count, out

    @staticmethod
    def _ok(cfg, narration, keywords, dest, variant=0):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"png")
        return dest

    def test_disabled_channel_never_calls_the_generator(self) -> None:
        gen_calls, pexels_calls, _ = self._run(
            Config({"assets": {"per_segment_clips": 3}}), self._ok
        )
        self.assertEqual(0, gen_calls)
        self.assertEqual(1, pexels_calls)

    def test_enabled_channel_uses_one_image_and_skips_stock(self) -> None:
        gen_calls, pexels_calls, out = self._run(_cfg(), self._ok)
        self.assertEqual(1, gen_calls)
        self.assertEqual(0, pexels_calls)
        self.assertEqual(1, len(out[0]))
        self.assertFalse(out[0][0].is_video, "generated asset must be a still")

    def test_generation_failure_falls_back_to_stock(self) -> None:
        gen_calls, pexels_calls, out = self._run(_cfg(), lambda *a, **k: None)
        self.assertEqual(1, gen_calls)
        self.assertEqual(1, pexels_calls)
        self.assertTrue(out[0], "segment must still end up with an asset")

    def test_full_short_mixes_stills_and_stock_by_beat(self) -> None:
        """End-to-end shape of a real four-beat short: motion on hook/setup/
        callback, generated stills on pivot and the fact stack."""
        beats = ["hook", "setup", "pivot", "fact", "fact", "fact", "callback"]
        segs = [Segment(f"{b} narration text here.", ["old map"], b) for b in beats]
        cfg = _cfg(beats=["pivot", "fact"])

        with tempfile.TemporaryDirectory() as d:
            with patch("src.imagegen.generate", side_effect=self._ok) as gen, patch(
                "src.assets._fetch_one", return_value=[]
            ) as pex, patch(
                "src.assets._solid_clip",
                side_effect=lambda p, s, o: (
                    p.parent.mkdir(parents=True, exist_ok=True),
                    p.write_text("x"),
                    p,
                )[-1],
            ):
                out = assets.fetch_for_segments(cfg, segs, pathlib.Path(d), "short")

        self.assertEqual(4, gen.call_count, "pivot + 3 facts get generated stills")
        self.assertEqual(3, pex.call_count, "hook, setup, callback stay on stock")
        self.assertEqual(len(beats), len(out))
        generated = [i for i, b in enumerate(beats) if b in ("pivot", "fact")]
        for i in generated:
            self.assertEqual(1, len(out[i]), f"beat {beats[i]} should be one still")

    def test_capped_short_generates_exactly_two_spread_images(self) -> None:
        """The shipped config: 2 images, at the pivot and the last fact."""
        beats = ["hook", "setup", "pivot", "fact", "fact", "fact", "callback"]
        segs = [Segment(f"{b} narration text here.", ["old map"], b) for b in beats]
        cfg = _cfg(beats=["pivot", "fact"], max_images=2)

        with tempfile.TemporaryDirectory() as d:
            with patch("src.imagegen.generate", side_effect=self._ok) as gen, patch(
                "src.assets._fetch_one", return_value=[]
            ) as pex, patch(
                "src.assets._solid_clip",
                side_effect=lambda p, s, o: (
                    p.parent.mkdir(parents=True, exist_ok=True),
                    p.write_text("x"),
                    p,
                )[-1],
            ):
                out = assets.fetch_for_segments(cfg, segs, pathlib.Path(d), "short")

        self.assertEqual(2, gen.call_count)
        self.assertEqual(5, pex.call_count, "the other five beats stay on stock")
        stills = [i for i, a in enumerate(out) if a[0].path.suffix == ".png"]
        self.assertEqual([2, 5], stills, "pivot and final fact")

    def test_beat_survives_the_state_json_round_trip(self) -> None:
        """Regression: the assets step rebuilds Segments from state.json. If
        the beat tag is dropped there, every segment silently falls back to
        stock and no image is ever generated."""
        from src.pipeline import _rebuild_segments

        state = {
            "script": {
                "segments": [
                    {"beat": "pivot", "narration": NARRATION, "keywords": KEYWORDS}
                ]
            }
        }
        rebuilt = _rebuild_segments(state)
        self.assertEqual("pivot", rebuilt[0].beat)

    def test_long_form_never_uses_generated_images(self) -> None:
        cfg = _cfg()
        with tempfile.TemporaryDirectory() as d:
            with patch("src.imagegen.generate", side_effect=self._ok) as g, patch(
                "src.assets._fetch_one", return_value=[]
            ), patch(
                "src.assets._solid_clip",
                side_effect=lambda p, s, o: (
                    p.parent.mkdir(parents=True, exist_ok=True),
                    p.write_text("x"),
                    p,
                )[-1],
            ):
                assets.fetch_for_segments(cfg, self.segs, pathlib.Path(d), "long")
        self.assertEqual(0, g.call_count)


if __name__ == "__main__":
    unittest.main()
