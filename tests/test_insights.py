import unittest
from datetime import datetime, timedelta, timezone

from src.insights import evidence

NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)


def video(n, region='europe', fb=200, age=72):
    return {'title': f'Video {n}', 'region': region, 'series': 'decisions', 'length': 'concise',
            'fb': fb, 'yt_pct': None, 'ig_skip': None, 'score': 0.5,
            'at': (NOW-timedelta(hours=age)).isoformat()}


class InsightsTests(unittest.TestCase):
    def test_recent_uploads_missing_values_and_zero_views_stay_distinct(self):
        report = evidence([video(1, fb=0), video(2, fb=None), video(3, fb=500, age=2)], NOW)
        self.assertEqual(report['mature_videos'], 2)
        self.assertEqual(report['fb_coverage'], 1)
        self.assertEqual(report['fb_baseline_median'], 0)
        self.assertEqual(report['weak_videos'][0]['fb'], 0)
        self.assertEqual(report['working'], [])

    def test_small_samples_cannot_be_declared_winners(self):
        videos = [video(n) for n in range(12)] + [video(20, region='japan', fb=5000)]
        report = evidence(videos, NOW)
        japan = next(g for g in report['groups'] if g['id']=='region:japan')
        self.assertFalse(japan['enough_data'])
        self.assertNotIn('region:japan', [g['id'] for g in report['working']])

    def test_group_medians_are_per_video_not_totals(self):
        videos = [video(n, fb=100) for n in range(6)] + [video(n+10, region='japan', fb=200) for n in range(6)]
        report = evidence(videos, NOW)
        self.assertEqual(report['fb_baseline_median'], 150)
        self.assertEqual(report['working'][0]['id'], 'region:japan')
        self.assertEqual(report['working'][0]['fb_median'], 200)
        self.assertEqual(report['not_working'][0]['id'], 'region:europe')


if __name__ == '__main__':
    unittest.main()
