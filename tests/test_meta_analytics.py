from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import export_meta_analytics


class InstagramInsightsTests(unittest.TestCase):
    @patch("scripts.export_meta_analytics.requests.get")
    def test_media_views_raises_when_all_metric_requests_fail(self, get: Mock) -> None:
        first = Mock(ok=False, status_code=400, text="views is not supported")
        second = Mock(ok=False, status_code=403, text="missing instagram_manage_insights")
        get.side_effect = [first, second]

        with self.assertRaisesRegex(RuntimeError, "instagram_manage_insights"):
            export_meta_analytics._ig_media_views("media-1", "token", "v21.0", "views")

    @patch("scripts.export_meta_analytics.requests.get")
    def test_media_views_raises_when_both_metrics_return_empty_data(self, get: Mock) -> None:
        empty = Mock(ok=True)
        empty.json.return_value = {"data": []}
        get.side_effect = [empty, empty]

        with self.assertRaisesRegex(RuntimeError, "empty insights data"):
            export_meta_analytics._ig_media_views("media-1", "token", "v21.0", "views")

    @patch("scripts.export_meta_analytics.requests.get")
    def test_media_list_error_reports_body_without_exposing_token(self, get: Mock) -> None:
        response = Mock(
            ok=False,
            status_code=403,
            text='{"error":{"message":"missing instagram_basic"}}',
        )
        response.raise_for_status.side_effect = RuntimeError(
            "403 for https://graph.facebook.com/media?access_token=secret-token"
        )
        get.return_value = response

        with self.assertRaises(RuntimeError) as caught:
            export_meta_analytics.fetch_instagram("secret-token", "ig-1", "v21.0", 50)

        self.assertIn("missing instagram_basic", str(caught.exception))
        self.assertNotIn("secret-token", str(caught.exception))


class FacebookInsightsTests(unittest.TestCase):
    def test_missing_video_insights_is_not_reported_as_zero(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no recognizable view metric"):
            export_meta_analytics._insight_views({})

    def test_explicit_zero_video_insights_remains_zero(self) -> None:
        insights = {
            "data": [
                {"name": "total_video_views", "values": [{"value": 0}]},
            ]
        }
        self.assertEqual(export_meta_analytics._insight_views(insights), 0)


class ExportCommandTests(unittest.TestCase):
    @patch("scripts.export_meta_analytics.env", return_value=None)
    def test_missing_token_returns_failure(self, _env: Mock) -> None:
        with patch.object(sys, "argv", ["export_meta_analytics.py"]):
            self.assertEqual(export_meta_analytics.main(), 1)

    @patch("scripts.export_meta_analytics.fetch_instagram", return_value=[])
    @patch("scripts.export_meta_analytics.fetch_facebook", side_effect=RuntimeError("denied"))
    @patch("scripts.export_meta_analytics.env", return_value="token")
    def test_any_configured_platform_failure_returns_failure(
        self, _env: Mock, _facebook: Mock, _instagram: Mock
    ) -> None:
        cfg = Mock()
        values = {
            "meta.api_version": "v21.0",
            "meta.page_id": "page-1",
            "meta.ig_user_id": "ig-1",
        }
        cfg.get.side_effect = lambda key, default=None: values.get(key, default)
        with tempfile.TemporaryDirectory() as tmp, patch(
            "scripts.export_meta_analytics.load_config", return_value=cfg
        ), patch("scripts.export_meta_analytics.base_dir", return_value=Path(tmp)), patch.object(
            sys, "argv", ["export_meta_analytics.py"]
        ):
            self.assertEqual(export_meta_analytics.main(), 1)
            self.assertFalse((Path(tmp) / "data" / "meta_analytics.json").exists())


if __name__ == "__main__":
    unittest.main()
