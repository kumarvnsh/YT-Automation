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

    @staticmethod
    def _insights(**values) -> Mock:
        response = Mock(ok=True)
        response.json.return_value = {"data": [{"name": k, "values": [{"value": v}]} for k, v in values.items()]}
        return response

    @patch("scripts.export_meta_analytics.requests.get")
    def test_reel_insights_converts_watch_time_ms_and_keeps_skip_rate(self, get: Mock) -> None:
        get.side_effect = [self._insights(ig_reels_avg_watch_time=12450), self._insights(reels_skip_rate=41.5), self._insights(shares=3)]

        result = export_meta_analytics._ig_reel_insights("media-1", "token", "v21.0")

        self.assertEqual(result, {"average_watch_time_seconds": 12.45, "skip_rate": 41.5, "shares": 3, "unavailable_metrics": {}})

    @patch("scripts.export_meta_analytics.requests.get")
    def test_reel_insights_retries_singly_when_batch_rejected(self, get: Mock) -> None:
        rejected = Mock(ok=False, status_code=400, text="reels_skip_rate is not supported")
        rejected.json.return_value = {"error": {"code": 100, "message": "unsupported metric"}}
        get.side_effect = [self._insights(ig_reels_avg_watch_time=9000), rejected,
                           self._insights(shares=0)]

        result = export_meta_analytics._ig_reel_insights("media-1", "token", "v21.0")

        self.assertEqual(result['average_watch_time_seconds'], 9.0)
        self.assertIsNone(result['skip_rate'])
        self.assertEqual(result['shares'], 0)
        self.assertIn('code 100', result['unavailable_metrics']['skip_rate'])

    @patch("scripts.export_meta_analytics.requests.get")
    def test_failure_is_redacted_and_survives_experiment_annotation(self, get):
        response = Mock(ok=False)
        response.json.return_value = {'error': {'code': 200, 'message': 'denied secret-token'}}
        get.return_value = response
        result = export_meta_analytics._ig_reel_insights('1', 'secret-token', 'v21.0')
        result = export_meta_analytics._experiment_metrics(result, 'instagram')
        self.assertIn('access_denied', result['unavailable_metrics']['shares'])
        self.assertNotIn('secret-token', str(result))

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

    @staticmethod
    def _media_list(timestamp: str) -> Mock:
        response = Mock(ok=True)
        response.json.return_value = {"data": [{"id": "reel-1", "media_type": "VIDEO",
                                                "timestamp": timestamp, "caption": "New reel"}]}
        return response

    @patch("scripts.export_meta_analytics._ig_reel_insights",
           return_value={"shares": None, "unavailable_metrics": {}})
    @patch("scripts.export_meta_analytics.requests.get")
    @patch("scripts.export_meta_analytics._graph_get")
    def test_brand_new_reel_without_insights_does_not_fail_export(self, graph_get, get, _ins) -> None:
        from datetime import datetime, timedelta, timezone
        fresh = (datetime.now(timezone.utc) - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%S+0000")
        graph_get.return_value = self._media_list(fresh)
        empty = Mock(ok=True)
        empty.json.return_value = {"data": []}
        get.return_value = empty

        videos = export_meta_analytics.fetch_instagram("token", "ig-1", "v21.0", 50)

        self.assertIsNone(videos[0]["views"])
        self.assertIn("insights_pending", videos[0]["unavailable_metrics"]["views"])

    @patch("scripts.export_meta_analytics._ig_reel_insights",
           return_value={"shares": None, "unavailable_metrics": {}})
    @patch("scripts.export_meta_analytics.requests.get")
    @patch("scripts.export_meta_analytics._graph_get")
    def test_older_reel_without_insights_still_fails_export(self, graph_get, get, _ins) -> None:
        graph_get.return_value = self._media_list("2026-01-01T10:00:00+0000")
        get.return_value = Mock(ok=False, status_code=403, text="missing instagram_manage_insights")

        with self.assertRaisesRegex(RuntimeError, "instagram_manage_insights"):
            export_meta_analytics.fetch_instagram("token", "ig-1", "v21.0", 50)


class FacebookInsightsTests(unittest.TestCase):
    def test_ad_views_cannot_be_mistaken_for_video_views(self):
        self.assertEqual(export_meta_analytics._insight_views({'data': [
            {'name': 'total_video_ad_views', 'values': [{'value': 900}]},
            {'name': 'total_video_views', 'values': [{'value': 20}]}]}), 20)
    @patch("scripts.export_meta_analytics.requests.get")
    def test_watch_time_ms_and_raw_retention_graph(self, get):
        get.side_effect = [InstagramInsightsTests._insights(post_video_avg_time_watched=12340),
                           InstagramInsightsTests._insights(post_video_retention_graph={'0': 100, '5': 70}),
                           InstagramInsightsTests._insights(fb_reels_total_plays=120),
                           InstagramInsightsTests._insights(blue_reels_play_count=100),
                           InstagramInsightsTests._insights(fb_reels_replay_count=20)]
        result = export_meta_analytics._facebook_reel_insights('1', 'token', 'v21.0')
        self.assertEqual(result['average_watch_time_seconds'], 12.34)
        self.assertEqual(result['retention_graph']['5'], 70)
        self.assertNotIn('completion_rate', result)
        self.assertEqual((result['plays'],result['initial_plays'],result['replays']),(120,100,20))

    def test_reel_plays_override_legacy_zero_without_inventing_missing_values(self):
        legacy={'data':[{'name':'total_video_views','values':[{'value':0}]}]}
        result=export_meta_analytics.facebook_view_fields(legacy,{'plays':120,'initial_plays':100,'replays':20})
        self.assertEqual(result['views'],120)
        self.assertEqual(result['views_metric'],'fb_reels_total_plays')
        self.assertEqual(result['three_second_views'],0)
        result=export_meta_analytics.facebook_view_fields({}, {'plays':None,'initial_plays':None,'replays':None})
        self.assertIsNone(result['views'])
        self.assertEqual(result['insight_status'],'unavailable')
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


def _resp(status: int, body: dict) -> Mock:
    import json
    r = Mock(ok=200 <= status < 300, status_code=status, text=json.dumps(body))
    r.json.return_value = body
    return r


@patch("scripts.export_meta_analytics.time.sleep")
class GraphRetryTests(unittest.TestCase):
    @patch("scripts.export_meta_analytics.requests.get")
    def test_transient_500_is_retried_then_succeeds(self, get: Mock, sleep: Mock) -> None:
        get.side_effect = [_resp(500, {"error": {"code": 2, "is_transient": True}}), _resp(200, {"data": []})]
        r = export_meta_analytics._graph_get("https://x", params={})
        self.assertTrue(r.ok)
        self.assertEqual(get.call_count, 2)

    @patch("scripts.export_meta_analytics.requests.get")
    def test_permission_error_is_not_retried(self, get: Mock, sleep: Mock) -> None:
        get.return_value = _resp(403, {"error": {"code": 200, "message": "denied"}})
        r = export_meta_analytics._graph_get("https://x", params={})
        self.assertFalse(r.ok)
        self.assertEqual(get.call_count, 1)
        sleep.assert_not_called()

    @patch("scripts.export_meta_analytics.requests.get")
    def test_gives_up_after_three_transient_failures(self, get: Mock, sleep: Mock) -> None:
        get.return_value = _resp(500, {"error": {"code": 2, "is_transient": True}})
        self.assertFalse(export_meta_analytics._graph_get("https://x", params={}).ok)
        self.assertEqual(get.call_count, 3)

    @patch("scripts.export_meta_analytics._facebook_reel_insights", return_value={})
    @patch("scripts.export_meta_analytics._page_access_token", return_value="page")
    @patch("scripts.export_meta_analytics.requests.get")
    def test_facebook_videos_are_fetched_in_pages(self, get: Mock, _tok, _ins, sleep: Mock) -> None:
        views = {"data": [{"name": "total_video_views", "values": [{"value": 5}]}]}
        page = lambda ids, after=None: _resp(200, {  # noqa: E731
            "data": [{"id": i, "video_insights": views} for i in ids],
            "paging": {"cursors": {"after": after}, "next": "more" if after else None}})
        get.side_effect = [page(["a", "b"], after="c1"), page(["c"])]
        vids = export_meta_analytics.fetch_facebook("token", "pg", "v21.0", 100)
        self.assertEqual([v["id"] for v in vids], ["a", "b", "c"])
        self.assertEqual(get.call_args_list[1].kwargs["params"]["after"], "c1")
        self.assertEqual(get.call_args_list[0].kwargs["params"]["limit"], export_meta_analytics.FB_PAGE_SIZE)


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
