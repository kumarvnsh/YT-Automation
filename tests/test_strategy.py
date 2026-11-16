import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src import strategy
from src.config import Config
from src.editorial import assignment, brief

NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)


def cfg():
    return Config({'editorial': {
        'regions': [{'id': 'india', 'label': 'India'}, {'id': 'japan', 'label': 'Japan'}],
        'series': [{'id': 'mistakes', 'name': 'Mistakes'}, {'id': 'unsolved', 'name': 'Unsolved'}],
        'duration_variants': [{'id': 'concise', 'seconds': 30}, {'id': 'story', 'seconds': 50}]}})


def entry(i, region, views, pct):
    return {'video_id': f'v{i}', 'title': f'{region} {i}', 'editorial_slot': i, 'category': 'mistakes',
            'editorial_region': region, 'duration_variant': 'concise',
            'published_at': (NOW - timedelta(days=3)).isoformat()}, {'views': views, 'likes': views // 50, 'avg_view_pct': pct}


class StrategyTests(unittest.TestCase):
    def build(self):
        pairs = [entry(i, 'japan', 400, 70) for i in range(6)] + [entry(i + 6, 'india', 20, 25) for i in range(6)]
        best = {'yt': {e['video_id']: m for e, m in pairs}, 'ig': {}, 'fb': {}}
        return strategy.build(cfg(), [e for e, _ in pairs], best, NOW)

    def test_strategy_build_favors_region_that_performs(self):
        result = self.build()

        self.assertEqual(result['videos_scored'], 12)
        self.assertGreater(result['weights']['region']['japan'], result['weights']['region']['india'])
        self.assertTrue(result['winners'][0].startswith('japan'))
        self.assertTrue(result['losers'][-1].startswith('india'))

    def test_strategy_weights_keep_exploring_every_option(self):
        weights = self.build()['weights']

        for dim in weights.values():
            self.assertAlmostEqual(sum(dim.values()), 1, places=2)
            self.assertGreaterEqual(min(dim.values()), strategy.EXPLORE / len(dim))

    def test_strategy_skips_young_and_non_editorial_videos(self):
        young, m = entry(1, 'japan', 10, 50)
        young['published_at'] = (NOW - timedelta(hours=5)).isoformat()
        legacy, m2 = entry(2, 'japan', 10, 50)
        legacy['editorial_slot'] = None

        rows = strategy.rows([young, legacy], {'yt': {'v1': m, 'v2': m2}, 'ig': {}, 'fb': {}}, NOW)

        self.assertEqual(rows, [])

    def test_assignment_with_strategy_is_deterministic_and_briefs_audience(self):
        plan = self.build()

        first, again = assignment(cfg(), 7, plan), assignment(cfg(), 7, plan)
        text = brief(first, [])

        self.assertEqual(first, again)
        self.assertIn('Our audience watched and liked', text)
        self.assertNotIn('audience', brief(assignment(cfg(), 7), []).lower())

    def test_load_ignores_stale_or_thin_strategy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data').mkdir()
            path = root / 'data/strategy.json'
            fresh = {'updated': datetime.now(timezone.utc).isoformat(), 'videos_scored': 40, 'weights': {}}
            path.write_text(json.dumps(fresh))
            self.assertIsNotNone(strategy.load(root))
            path.write_text(json.dumps({**fresh, 'updated': (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()}))
            self.assertIsNone(strategy.load(root))
            path.write_text(json.dumps({**fresh, 'videos_scored': 3}))
            self.assertIsNone(strategy.load(root))


if __name__ == '__main__':
    unittest.main()
