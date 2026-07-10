from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import Config
from src import pipeline


class PipelineArtifactTests(unittest.TestCase):
    def test_cleanup_preserves_uploaded_stage_for_workflow_artifact(self) -> None:
        cfg = Config({"output": {"delete_after_upload": True, "keep_days": 3}})
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "20260710_090000_short"
            stage.mkdir()
            (stage / "video.mp4").write_bytes(b"video")

            with patch("src.pipeline._out_root", return_value=Path(tmp)), patch(
                "src.pipeline.env",
                side_effect=lambda key, default=None: "true"
                if key == "PRESERVE_STAGE_ARTIFACT"
                else default,
            ):
                pipeline._cleanup(cfg, stage, {"youtube_id": "video-1"})

            self.assertTrue(stage.exists())
            self.assertTrue((stage / "video.mp4").exists())

    def test_published_index_can_reference_the_source_artifact_run(self) -> None:
        cfg = Config({})
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "20260710_090000_short"
            stage.mkdir()
            values = {
                "PUBLISH_ARTIFACT_RUN_ID": "source-run-123",
                "GITHUB_RUN_ID": "approval-run-456",
            }
            with patch("src.pipeline.base_dir", return_value=Path(tmp)), patch(
                "src.pipeline.env", side_effect=lambda key, default=None: values.get(key, default)
            ):
                pipeline._record_published(
                    cfg,
                    stage,
                    {"youtube_id": "video-1", "title": "Title"},
                )

            entries = json.loads((Path(tmp) / "data" / "published_index.json").read_text())
            self.assertEqual(entries[0]["run_id"], "source-run-123")


if __name__ == "__main__":
    unittest.main()
