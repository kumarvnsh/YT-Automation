"""Retitled videos leave the Underperformers pool for a 24h cooldown window."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import republish  # noqa: E402
import auto_republish  # noqa: E402


class RecordRetitleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        patcher = patch("republish.base_dir", return_value=self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _index(self) -> list[dict]:
        return json.loads((self.tmp / "data" / "published_index.json").read_text(encoding="utf-8"))

    def test_updates_existing_entry(self) -> None:
        (self.tmp / "data").mkdir()
        (self.tmp / "data" / "published_index.json").write_text(json.dumps([
            {"video_id": "vid-1", "run_id": "run-1", "stage_dir_name": "stage-1",
             "title": "Old", "retitled_at": None},
        ]), encoding="utf-8")

        republish._record_retitle("vid-1", "New Title")

        entry = self._index()[0]
        self.assertEqual("New Title", entry["title"])
        self.assertTrue(entry["retitled_at"])
        self.assertEqual("run-1", entry["run_id"])

    def test_appends_minimal_entry_for_pre_index_video(self) -> None:
        republish._record_retitle("legacy-vid", "Fresh Title")

        entries = self._index()
        self.assertEqual(1, len(entries))
        entry = entries[0]
        self.assertEqual("legacy-vid", entry["video_id"])
        self.assertEqual("Fresh Title", entry["title"])
        self.assertTrue(entry["retitled_at"])
        # No artifact mapping: repost (manual button and auto) must stay disabled.
        self.assertEqual("", entry["run_id"])
        self.assertEqual("", entry["stage_dir_name"])


class DashboardCooldownTests(unittest.TestCase):
    def test_dashboard_hides_fresh_retitles(self) -> None:
        app_js = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
        self.assertIn("RETITLE_COOLDOWN_HOURS = 24", app_js)
        self.assertIn(
            "hoursAgo(entry.retitled_at) < RETITLE_COOLDOWN_HOURS) return false;",
            app_js,
        )


class AutoRepublishCooldownTests(unittest.TestCase):
    def _run_auto(self, entry_extra: dict, mode: str = "repost") -> str:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "config.yaml").write_text(json.dumps({
                "republish": {
                    "auto": True, "mode": mode, "view_threshold": 100,
                    "min_age_hours": 30, "max_age_days": 13, "max_per_check": 1,
                },
            }), encoding="utf-8")
            (base / "data").mkdir()
            published = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
            (base / "data" / "analytics.json").write_text(json.dumps({
                "videos": [{"id": "vid-1", "title": "Stuck", "views": 5,
                            "publishedAt": published}],
            }), encoding="utf-8")
            (base / "data" / "published_index.json").write_text(json.dumps([
                {"video_id": "vid-1", "run_id": "run-1", "stage_dir_name": "stage-1",
                 "published_at": published, "retitled_at": None,
                 "republished_from": None, "republished_as": None,
                 **entry_extra},
            ]), encoding="utf-8")

            out = io.StringIO()
            argv = ["auto_republish.py", "--config", str(base / "config.yaml"), "--dry-run"]
            with patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
                auto_republish.main()
            return out.getvalue()

    def test_fresh_retitle_is_not_a_candidate(self) -> None:
        recent = datetime.now(timezone.utc).isoformat()
        output = self._run_auto({"retitled_at": recent})
        self.assertIn("0 candidate(s)", output)

    def test_old_retitle_is_eligible_again(self) -> None:
        stale = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        output = self._run_auto({"retitled_at": stale})
        self.assertIn("1 candidate(s)", output)

    def test_retitle_only_entry_is_never_reposted(self) -> None:
        stale = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        output = self._run_auto({"retitled_at": stale, "run_id": "", "stage_dir_name": ""})
        self.assertIn("0 candidate(s)", output)


if __name__ == "__main__":
    unittest.main()
