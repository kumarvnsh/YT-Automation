from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import Config
from src import pipeline
from src.quality_gate import validate_stage


def _quality_cfg() -> Config:
    return Config(
        {
            # 2s at ~150 words/min → the 5-word fixture narration is exactly on target.
            "script": {"shorts_target_seconds": 2},
            "video": {
                "short": {"width": 1080, "height": 1920},
                "captions": {"enabled": True},
            },
            "quality": {
                "enabled": True,
                "min_duration_ratio": 0.75,
                "max_duration_ratio": 1.35,
                "banned_terms": ["graphic sexual", "extreme gore"],
            },
        }
    )


def _valid_state() -> dict:
    return {
        "fmt": "short",
        "title": "A Test History Hook",
        "script": {
            "title": "A Test History Hook",
            "description": "Test description",
            "tags": ["history"],
            "segments": [
                {"narration": "One two three four five.", "keywords": ["old book"]}
            ],
        },
        "voiceover": {
            "path": "voiceover.mp3",
            "duration": 20.0,
            "words": [["One", 0.0, 0.2]],
        },
        "captions": "captions.ass",
        "assets": [[["assets/seg_00.jpg", False]]],
        "video": "video.mp4",
        "topic_reservation": {"status": "reserved"},
    }


def _ffprobe_json(width: int = 1080, height: int = 1920, duration: str = "20.0") -> str:
    return json.dumps(
        {
            "streams": [
                {"codec_type": "video", "width": width, "height": height},
                {"codec_type": "audio"},
            ],
            "format": {"duration": duration},
        }
    )


class QualityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.stage = Path(self._tmp.name)
        (self.stage / "voiceover.mp3").write_bytes(b"audio")
        (self.stage / "captions.ass").write_text("captions", encoding="utf-8")
        (self.stage / "assets").mkdir()
        (self.stage / "assets" / "seg_00.jpg").write_bytes(b"image")
        (self.stage / "video.mp4").write_bytes(b"video")

    def test_valid_stage_passes_every_check(self) -> None:
        with patch("src.quality_gate.subprocess.run") as ffprobe_run:
            ffprobe_run.return_value.stdout = _ffprobe_json()
            report = validate_stage(_quality_cfg(), self.stage, _valid_state())

        self.assertTrue(report["passed"])
        self.assertEqual(100, report["score"])
        self.assertEqual("pass", report["checks"]["video"])
        self.assertEqual("pass", report["checks"]["safety"])
        self.assertEqual([], report["errors"])

    def test_missing_voiceover_fails_audio_check(self) -> None:
        (self.stage / "voiceover.mp3").unlink()

        with patch("src.quality_gate.subprocess.run") as ffprobe_run:
            ffprobe_run.return_value.stdout = _ffprobe_json()
            report = validate_stage(_quality_cfg(), self.stage, _valid_state())

        self.assertFalse(report["passed"])
        self.assertEqual("fail", report["checks"]["audio"])

    def test_wrong_dimensions_fail(self) -> None:
        with patch("src.quality_gate.subprocess.run") as ffprobe_run:
            ffprobe_run.return_value.stdout = _ffprobe_json(width=1920, height=1080)
            report = validate_stage(_quality_cfg(), self.stage, _valid_state())

        self.assertFalse(report["passed"])
        self.assertEqual("fail", report["checks"]["dimensions"])

    def test_duration_far_from_voiceover_fails(self) -> None:
        with patch("src.quality_gate.subprocess.run") as ffprobe_run:
            ffprobe_run.return_value.stdout = _ffprobe_json(duration="40.0")
            report = validate_stage(_quality_cfg(), self.stage, _valid_state())

        self.assertFalse(report["passed"])
        self.assertEqual("fail", report["checks"]["duration"])

    def test_banned_term_fails_safety(self) -> None:
        state = _valid_state()
        state["script"]["description"] = "Contains extreme gore for shock value"

        with patch("src.quality_gate.subprocess.run") as ffprobe_run:
            ffprobe_run.return_value.stdout = _ffprobe_json()
            report = validate_stage(_quality_cfg(), self.stage, state)

        self.assertFalse(report["passed"])
        self.assertEqual("fail", report["checks"]["safety"])

    def test_missing_reservation_fails_topic_check(self) -> None:
        state = _valid_state()
        del state["topic_reservation"]

        with patch("src.quality_gate.subprocess.run") as ffprobe_run:
            ffprobe_run.return_value.stdout = _ffprobe_json()
            report = validate_stage(_quality_cfg(), self.stage, state)

        self.assertFalse(report["passed"])
        self.assertEqual("fail", report["checks"]["topic"])

    def test_report_uses_exact_check_names(self) -> None:
        with patch("src.quality_gate.subprocess.run") as ffprobe_run:
            ffprobe_run.return_value.stdout = _ffprobe_json()
            report = validate_stage(_quality_cfg(), self.stage, _valid_state())

        self.assertEqual(
            {
                "script", "topic", "narration", "audio", "captions", "assets",
                "video", "dimensions", "duration", "safety", "metadata",
            },
            set(report["checks"]),
        )


class QualityStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.stage = Path(self._tmp.name)

    def test_quality_step_runs_before_upload(self) -> None:
        self.assertIn("quality", pipeline.STEP_ORDER)
        self.assertLess(
            pipeline.STEP_ORDER.index("quality"),
            pipeline.STEP_ORDER.index("upload"),
        )
        self.assertIn("quality", pipeline.STEP_FUNCS)

    def test_failed_report_raises_and_persists(self) -> None:
        state = _valid_state()
        with patch(
            "src.quality_gate.validate_stage",
            return_value={
                "passed": False,
                "checks": {"audio": "fail"},
                "errors": ["voiceover missing"],
                "score": 0,
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "quality gate failed: audio"):
                pipeline._step_quality(_quality_cfg(), self.stage, state)

        self.assertFalse(state["quality"]["passed"])
        report = json.loads((self.stage / "quality.json").read_text(encoding="utf-8"))
        self.assertFalse(report["passed"])

    def test_passing_report_is_stored(self) -> None:
        state = _valid_state()
        good = {"passed": True, "checks": {"video": "pass"}, "errors": [], "score": 100}
        with patch("src.quality_gate.validate_stage", return_value=good):
            pipeline._step_quality(_quality_cfg(), self.stage, state)

        self.assertTrue(state["quality"]["passed"])

    def test_upload_blocked_when_quality_failed(self) -> None:
        state = _valid_state()
        state["quality"] = {"passed": False, "checks": {"audio": "fail"}}

        with self.assertRaisesRegex(RuntimeError, "upload blocked"):
            pipeline._step_upload(_quality_cfg(), self.stage, state)

    def test_legacy_state_without_quality_still_uploads(self) -> None:
        cfg = Config({"youtube": {"enabled": False}})
        state = _valid_state()

        pipeline._step_upload(cfg, self.stage, state)

        self.assertEqual("youtube.enabled=false", state["upload_skipped"])


if __name__ == "__main__":
    unittest.main()
