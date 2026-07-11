"""Mocked end-to-end run of the pipeline: no network, ffmpeg, or OAuth.

Every external boundary (LLM, TTS, Pexels, ffmpeg/ffprobe, YouTube) is patched;
the real pipeline runner, provider routing, topic reservation, and quality gate
execute against a temporary stage directory.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import Config
from src import pipeline
from src.tts import Voiceover, WordTiming


SCRIPT_JSON = {
    "topic": "test topic",
    "title": "A Test History Hook",
    "description": "A short description.\n\n#history #shorts #facts",
    "tags": ["history", "facts"],
    "segments": [
        {"narration": "One two three four five.", "keywords": ["old library"]},
    ],
}


def _cfg() -> Config:
    return Config(
        {
            "script": {"provider": "anthropic", "shorts_target_seconds": 2},
            "video": {
                "mode": "broll",
                "short": {"width": 1080, "height": 1920},
                "captions": {"enabled": True},
            },
            "quality": {
                "enabled": True,
                "min_duration_ratio": 0.75,
                "max_duration_ratio": 1.35,
                "banned_terms": ["graphic sexual", "extreme gore"],
            },
            "youtube": {"enabled": True, "privacy_status": "public"},
            "output": {"dir": "output", "delete_after_upload": False, "keep_days": 3},
        }
    )


def _fake_synthesize(cfg, narration, out_path):
    Path(out_path).write_bytes(b"audio")
    return Voiceover(
        audio_path=Path(out_path),
        duration=2.0,
        words=[WordTiming("One", 0.0, 0.4), WordTiming("two", 0.4, 0.8)],
    )


def _fake_build_ass(cfg, words, out_path, fmt, **kwargs):
    Path(out_path).write_text("captions", encoding="utf-8")


def _fake_fetch_for_segments(cfg, segs, stage, fmt):
    from src.assets import Asset

    (stage / "assets").mkdir(exist_ok=True)
    path = stage / "assets" / "seg_00.jpg"
    path.write_bytes(b"image")
    return [[Asset(path, False, [])] for _ in segs]


def _fake_build_broll_silent(cfg, assets, duration, words, fmt, stage, name):
    (stage / name).write_bytes(b"silent")


def _fake_finalize(cfg, silent, vo, ass, out, stage, root):
    Path(out).write_bytes(b"video")


FFPROBE_JSON = json.dumps(
    {
        "streams": [
            {"codec_type": "video", "width": 1080, "height": 1920},
            {"codec_type": "audio"},
        ],
        "format": {"duration": "2.0"},
    }
)


class AutomationSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        for target in ("src.pipeline.base_dir", "src.topics.base_dir"):
            patcher = patch(target, return_value=self.tmp)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, upload_video, extra_patches=()):
        cfg = _cfg()
        stage = pipeline.new_stage(cfg, "short", no_upload=False, dry_run=False,
                                   topic="test topic")
        patches = [
            patch("src.script_generator._call_anthropic",
                  return_value=json.dumps(SCRIPT_JSON)),
            patch("src.tts.synthesize", side_effect=_fake_synthesize),
            patch("src.captions.build_ass", side_effect=_fake_build_ass),
            patch("src.assets.fetch_for_segments", side_effect=_fake_fetch_for_segments),
            patch("src.video_builder.build_broll_silent",
                  side_effect=_fake_build_broll_silent),
            patch("src.video_builder.finalize", side_effect=_fake_finalize),
            patch("src.youtube_uploader.upload_video", upload_video),
            *extra_patches,
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return cfg, stage

    def test_full_run_reserves_topic_passes_quality_and_uploads(self) -> None:
        upload = unittest.mock.MagicMock(return_value="mock-video-id")
        ffprobe = patch("src.quality_gate.subprocess.run")
        cfg, stage = self._run(upload, extra_patches=())
        with ffprobe as ffprobe_run:
            ffprobe_run.return_value.stdout = FFPROBE_JSON
            state = pipeline.run_stage(cfg, stage, one_step=False)

        self.assertTrue(state["complete"])
        self.assertEqual("anthropic", state["script_provider"])
        self.assertFalse(state["script_fallback_used"])
        self.assertEqual("reserved", state["topic_reservation"]["status"])
        self.assertTrue(state["quality"]["passed"])
        self.assertEqual("mock-video-id", state["youtube_id"])
        upload.assert_called_once()

        published = json.loads(
            (self.tmp / "data" / "published_index.json").read_text(encoding="utf-8")
        )
        self.assertEqual("mock-video-id", published[0]["video_id"])
        self.assertIn("slot", published[0])
        self.assertTrue(
            (self.tmp / "data" / "topic_reservations.json").exists()
        )

    def test_failed_quality_blocks_upload(self) -> None:
        upload = unittest.mock.MagicMock(return_value="mock-video-id")
        failed = {
            "passed": False,
            "score": 0,
            "checks": {"audio": "fail"},
            "errors": ["voiceover missing"],
        }
        cfg, stage = self._run(
            upload,
            extra_patches=(
                patch("src.quality_gate.validate_stage", return_value=failed),
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "quality gate failed: audio"):
            pipeline.run_stage(cfg, stage, one_step=False)

        upload.assert_not_called()
        state = pipeline.load_state(stage)
        self.assertFalse(state["complete"])
        self.assertFalse(state["quality"]["passed"])


if __name__ == "__main__":
    unittest.main()
