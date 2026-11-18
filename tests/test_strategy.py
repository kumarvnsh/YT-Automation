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
    def test_youtube_primary_is_independent_of_facebook_and_ignores_tiny_retention(self):
        pairs=[entry(i,'japan',100,50) for i in range(6)]+[entry(i+6,'india',100,50) for i in range(6)]
        index=[e for e,_ in pairs]
        for e in index:e['facebook_id']='fb-'+e['video_id']
        best={'yt':{e['video_id']:{**m,'engaged_views':60} for e,m in pairs},'ig':{},
              'fb':{'fb-'+e['video_id']:{'views':900 if e['editorial_region']=='india' else 30,
                                        'plays':900 if e['editorial_region']=='india' else 30} for e in index}}
        chosen=cfg();chosen.raw['editorial']['primary_platform']='youtube'
        plan=strategy.build(chosen,index,best,NOW)
        self.assertEqual(plan['weights']['region']['india'],plan['weights']['region']['japan'])
        self.assertEqual(plan['primary_platform'],'youtube')
        tiny=[{'yt_pct':1800,'yt_engaged_count':2,'yt_views':1,'yt_like_rate':None,'yt_engaged':1}]
        strategy.score(tiny,'youtube');self.assertIsNone(tiny[0]['yt_pct']);self.assertIsNone(tiny[0]['yt_like_rate'])

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

    def test_facebook_views_steer_weights_when_youtube_is_equal(self):
        pairs = [entry(i, 'japan', 100, 50) for i in range(6)] + [entry(i + 6, 'india', 100, 50) for i in range(6)]
        index = [e for e, _ in pairs]
        for e in index:
            e['facebook_id'] = 'fb-' + e['video_id']
        fb = {'fb-' + e['video_id']: {'views': 900 if e['editorial_region'] == 'india' else 30} for e in index}
        best = {'yt': {e['video_id']: m for e, m in pairs}, 'ig': {}, 'fb': fb}

        weights = strategy.build(cfg(), index, best, NOW)['weights']['region']

        self.assertGreater(weights['india'], weights['japan'])

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
        self.assertIn('Recent stories with stronger observed performance', text)
        self.assertNotIn('audience', brief(assignment(cfg(), 7), []).lower())

    def test_overrides_pause_and_boost_then_renormalize(self):
        w = {'series': {'a': 0.5, 'b': 0.3, 'c': 0.2}}
        out = strategy.apply_overrides(w, {'series': {'paused': ['a'], 'boost': {'c': 10}}})['series']
        self.assertEqual(out['a'], 0)
        self.assertAlmostEqual(sum(out.values()), 1, places=3)
        self.assertAlmostEqual(out['c'] / out['b'], 0.2 * strategy.MAX_BOOST / 0.3, places=2)
        everything = strategy.apply_overrides(w, {'series': {'paused': ['a', 'b', 'c']}})
        self.assertEqual(everything, w)

    def test_load_applies_overrides_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data').mkdir()
            fresh = {'updated': datetime.now(timezone.utc).isoformat(), 'videos_scored': 40,
                     'weights': {'region': {'india': 0.5, 'japan': 0.5}}}
            (root / 'data/strategy.json').write_text(json.dumps(fresh))
            (root / strategy.OVERRIDES).write_text(json.dumps({'region': {'paused': ['japan']}}))
            plan = strategy.load(root)
            self.assertEqual(plan['weights']['region'], {'india': 1.0, 'japan': 0.0})
            self.assertEqual(assignment(cfg(), 3, plan)['region']['id'], 'india')

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
