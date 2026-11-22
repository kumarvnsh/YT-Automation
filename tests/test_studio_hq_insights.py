import json
import tempfile
import time
import unittest
from unittest import mock
from datetime import date, timedelta
from pathlib import Path

from studio_hq import assistant, video_insights as vi


def daily(start, values):
    first = date.fromisoformat(start)
    return [[str(first + timedelta(days=i)), v] for i, v in enumerate(values) if v]  # YouTube omits zero days


def video(identity, start, values, *, pct=50.0, retention=None, traffic=None, seconds=40):
    return {'id': identity, 'title': 'Video ' + identity, 'published': start + 'T10:00:00Z', 'duration_seconds': seconds,
            'totals': {'views': sum(values), 'averageViewPercentage': pct},
            'retention': retention if retention is not None else [[0.0, 1.0, 0.5], [0.2, 0.8, 0.5], [1.0, 0.4, 0.5]],
            'traffic': traffic if traffic is not None else [{'insightTrafficSourceType': 'SHORTS', 'views': 90},
                                                             {'insightTrafficSourceType': 'YT_SEARCH', 'views': 10}],
            'daily': daily(start, values)}


class Helpers(unittest.TestCase):
    def test_recent_uploads_keeps_reportable_ages_newest_first(self):
        today = date(2026, 11, 22)
        rows = [('2026-11-21T00:00:00Z', 'new'), ('2026-11-19T00:00:00Z', 'ok1'), ('2026-10-01T00:00:00Z', 'old'),
                ('2026-11-10T00:00:00Z', 'ok2'), (None, 'private')]
        self.assertEqual([i for _, i in vi.recent_uploads(rows, today)], ['ok1', 'ok2'])

    def test_retention_is_halved_and_sorted(self):
        rows = [{'elapsedVideoTimeRatio': r / 100, 'audienceWatchRatio': 1 - r / 200, 'relativeRetentionPerformance': 0.5}
                for r in range(100, 0, -1)]
        curve = vi.compact_retention(rows)
        self.assertEqual(len(curve), 51)
        self.assertEqual(curve[0][0], 0.01)
        self.assertEqual(curve[-1][0], 1.0)

    def test_duration(self):
        self.assertEqual(vi.duration_seconds('PT1M5S'), 65)
        self.assertEqual(vi.duration_seconds('PT45S'), 45)
        self.assertIsNone(vi.duration_seconds('bad'))

    def test_missing_days_count_as_zero_until_last_reported_day(self):
        filled = vi.filled_daily({'published': '2026-11-01', 'daily': [['2026-11-03', 5]]}, date(2026, 11, 4))
        self.assertEqual([v for _, v in filled], [0, 0, 5, 0])


class Summary(unittest.TestCase):
    def report(self, slow):
        videos = [video(f'v{i}', '2026-11-01', [40, 30, 20, 10, 5]) for i in range(6)] + [slow]
        return {'status': 'connected', 'checked_at': 'x', 'window_end': '2026-11-21', 'videos': videos,
                'traffic': [{'insightTrafficSourceType': 'SHORTS', 'views': 80}, {'insightTrafficSourceType': 'NEW_THING', 'views': 20}]}

    def test_slow_starter_gets_specific_reasons(self):
        slow = video('slow', '2026-11-01', [5, 3, 2, 1, 1], pct=20.0,
                     retention=[[0.0, 1.0, 0.2], [0.2, 0.3, 0.2], [1.0, 0.1, 0.2]],
                     traffic=[{'insightTrafficSourceType': 'SHORTS', 'views': 2}, {'insightTrafficSourceType': 'YT_SEARCH', 'views': 8}])
        result = vi.summarize_channel(self.report(slow))
        self.assertEqual([row['id'] for row in result['attention']], ['slow'])
        reasons = ' '.join(result['attention'][0]['reasons'])
        for phrase in ('watched 20%', 'first fifth', 'similar length', 'Shorts feed'):
            self.assertIn(phrase, reasons)
        self.assertEqual(result['traffic'][1]['label'], 'New thing')
        self.assertEqual(len(result['typical_retention']), 3)

    def test_no_data_does_not_claim_retention_is_normal(self):
        slow = video('slow', '2026-11-01', [1, 0, 0, 0, 0], retention=[], traffic=[])
        reasons = vi.summarize_channel(self.report(slow))['attention'][0]['reasons']
        self.assertEqual(reasons, ['YouTube has too little viewing data on it yet to explain why.'])

    def test_tiny_channels_are_not_flagged(self):
        report = {'status': 'connected', 'videos': [video(f'v{i}', '2026-11-01', [3, 2, 1, 1]) for i in range(6)]
                  + [video('slow', '2026-11-01', [0, 1, 0, 0])]}
        self.assertEqual(vi.summarize_channel(report)['attention'], [])

    def test_error_report_is_summarised_without_data(self):
        self.assertEqual(vi.summarize_channel({'status': 'error', 'error': 'Expired'}), {'status': 'error', 'error': 'Expired'})


class Refresh(unittest.TestCase):
    def config(self, folder):
        return {'dashboard_private_snapshot': str(folder/'snap.json'), 'state_dir': str(folder),
                'roots': {'histold': str(folder), 'shotsnip': str(folder)}, 'dashboard_calendar_python': str(folder/'python.exe')}

    def test_failed_channel_keeps_last_good_report(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            config = self.config(folder)
            (folder/'video-insights.json').write_text(json.dumps({'channels': {'histold': {'status': 'connected', 'videos': [1]}}}))
            sources = {'histold': (folder, lambda: 'id'), 'shotsnip': (folder, lambda: 'id')}

            def fail(*_):
                raise PermissionError('mismatch')
            with mock.patch.object(vi, 'channel_sources', return_value=sources):
                result = vi.refresh(config, fetch=fail)
            self.assertEqual(result['channels']['histold']['videos'], [1])
            self.assertIn('different channel', result['channels']['histold']['error'])
            self.assertEqual(result['channels']['shotsnip']['status'], 'error')
            self.assertNotIn('mismatch', json.dumps(result))

    def test_starts_only_when_stale_and_not_recently_tried(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            config = self.config(folder)
            (folder/'python.exe').write_text('')
            started = []
            self.assertTrue(vi.maybe_start(config, folder/'c.json', spawn=started.append))
            self.assertEqual(started[0][1:3], ['-m', 'studio_hq.video_insights'])
            self.assertFalse(vi.maybe_start(config, folder/'c.json', spawn=started.append))  # tried moments ago
            self.assertFalse(vi.maybe_start(config, folder/'c.json', spawn=started.append, now=time.time() + 10))
            (folder/'video-insights.json').write_text('{}')
            self.assertFalse(vi.maybe_start(config, folder/'c.json', spawn=started.append, now=time.time() + vi.RETRY_SECONDS + 5))
            self.assertEqual(len(started), 1)


class Assistant(unittest.TestCase):
    def test_video_scope_sends_compact_evidence(self):
        summary = vi.summarize_channel(Summary().report(video('slow', '2026-11-01', [5, 3, 2, 1, 1])))
        facts = assistant.evidence({'video_insights': {'histold': summary}}, 'videos', None)
        self.assertEqual(facts['slow_starters'][0]['title'], 'Video slow')
        self.assertEqual(len(facts['recent_videos']), 7)
        self.assertLess(len(json.dumps(facts)), 12000)
        self.assertEqual(assistant.check_request({'scope': 'videos', 'question': 'Why?'}), ('videos', None))

    def test_video_scope_needs_insights(self):
        with self.assertRaises(ValueError):
            assistant.evidence({'video_insights': {'histold': {'status': 'error'}}}, 'videos', None)


if __name__ == '__main__':
    unittest.main()
