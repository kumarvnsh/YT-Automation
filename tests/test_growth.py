import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src import growth
from src.config import Config

NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
ALLOWED = [0, 1, 2, 3, 4] + list(range(11, 24))


def cfg(**over):
    g = {'posts_per_day': 5, 'default_hours_utc': [4, 7, 12, 16, 18], 'allowed_hours_utc': ALLOWED,
         'min_gap_hours': 2, 'plan_max_age_days': 7, 'change_interval_days': 3, 'min_samples': 6}
    return Config({'growth': {**g, **over}})


def write_plan(root, **plan):
    (root / 'data').mkdir(exist_ok=True)
    (root / growth.PLAN).write_text(json.dumps({'updated': NOW.isoformat(), **plan}))


class GateTests(unittest.TestCase):
    def test_reducing_volume_rejects_old_plan_and_keeps_three_slot_fallback(self):
        reduced = cfg(posts_per_day=3, default_hours_utc=[12, 18, 23])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_plan(root, hours_utc=[0, 12, 16, 18, 22])
            hours, source = growth.active_hours(root, reduced, NOW)
            self.assertEqual(hours, [12, 18, 23])
            self.assertIn('rejected', source)
            write_plan(root, hours_utc=[12, 18, 23])
            hours, source = growth.active_hours(root, reduced, NOW)
            self.assertEqual(hours, [12, 18, 23])
            self.assertNotIn('default', source)

    def active(self, **plan):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            if plan:
                write_plan(root, **plan)
            return growth.active_hours(root, cfg(), NOW)

    def test_missing_plan_uses_default_hours(self):
        hours, source = self.active()
        self.assertEqual(hours, [4, 7, 12, 16, 18])
        self.assertIn('no growth plan', source)

    def test_valid_plan_is_used(self):
        hours, source = self.active(hours_utc=[0, 12, 16, 18, 22])
        self.assertEqual(hours, [0, 12, 16, 18, 22])
        self.assertIn('growth plan', source)

    def test_plan_cannot_raise_posts_per_day(self):
        hours, source = self.active(hours_utc=[0, 2, 12, 14, 16, 18, 20, 22])
        self.assertEqual(hours, [4, 7, 12, 16, 18])
        self.assertIn('posts_per_day', source)

    def test_plan_rejected_outside_audience_hours_or_too_close(self):
        self.assertIn('outside', self.active(hours_utc=[6, 12, 16, 18, 22])[1])
        self.assertIn('apart', self.active(hours_utc=[12, 13, 16, 18, 22])[1])
        self.assertIn('IST evening', self.active(hours_utc=[0, 2, 4, 20, 22])[1])

    def test_stale_or_waiting_plan_falls_back(self):
        old = (NOW - timedelta(days=8)).isoformat()
        self.assertIn('days old', self.active(hours_utc=[0, 12, 16, 18, 22], updated=old)[1])
        self.assertIn('not steering', self.active(hours_utc=None, status='waiting')[1])

    def test_gaps_wrap_around_midnight(self):
        self.assertFalse(growth.gaps_ok([23, 0, 12], 2))
        self.assertTrue(growth.gaps_ok([22, 0, 12], 2))


class DedupeTests(unittest.TestCase):
    def test_duplicate_index_records_merge_into_one_video(self):
        merged = growth.dedupe([
            {'video_id': 'a', 'title': 'T', 'category': 'erased', 'facebook_id': None},
            {'video_id': 'a', 'title': 'T', 'category': None, 'facebook_id': 'fb1'},
            {'video_id': 'b', 'title': 'U'}])
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]['category'], 'erased')
        self.assertEqual(merged[0]['facebook_id'], 'fb1')


class PublishGateScriptTests(unittest.TestCase):
    def run_gate(self, *args):
        import subprocess
        import sys
        return subprocess.run([sys.executable, 'scripts/publish_gate.py', *args],
                              capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent)

    def test_late_runs_are_skipped_even_in_a_publish_hour(self):
        hours, _ = growth.active_hours(Path(__file__).resolve().parent.parent,
                                       __import__('src.config', fromlist=['load_config']).load_config())
        self.assertEqual(self.run_gate('--hour', str(hours[0]), '--minute', '5').returncode, 0)
        self.assertEqual(self.run_gate('--hour', str(hours[0]), '--minute', '45').returncode, 3)
        off = next(h for h in range(24) if h not in hours)
        self.assertEqual(self.run_gate('--hour', str(off), '--minute', '0').returncode, 3)


class ProposeTests(unittest.TestCase):
    def setUp(self):
        self.s = growth.settings(cfg())

    def test_off_audience_hour_is_replaced_first(self):
        stats = {h: {'n': 20, 'mean': 0.5, 'shrunk': 0.5} for h in (4, 7, 12, 16, 18)}
        hours, change = growth.propose([4, 7, 12, 16, 18], stats, self.s)
        self.assertEqual(change['removed'], 7)
        self.assertIsNone(growth.problem(hours, self.s))

    def test_weak_proven_hour_swapped_for_promising_one(self):
        current = [0, 12, 16, 18, 22]
        stats = {h: {'n': 20, 'mean': 0.6, 'shrunk': 0.6} for h in current}
        stats[16] = {'n': 20, 'mean': 0.3, 'shrunk': 0.32}
        hours, change = growth.propose(current, stats, self.s)
        self.assertEqual(change['removed'], 16)
        self.assertNotIn(16, hours)
        self.assertIsNone(growth.problem(hours, self.s))

    def test_holds_without_enough_data_or_clear_winner(self):
        current = [0, 12, 16, 18, 22]
        thin = {h: {'n': 2, 'mean': 0.2, 'shrunk': 0.4} for h in current}
        self.assertIsNone(growth.propose(current, thin, self.s)[1])
        strong = {h: {'n': 40, 'mean': 0.9, 'shrunk': 0.88} for h in current}
        self.assertIsNone(growth.propose(current, strong, self.s)[1])

    def test_hour_stats_credit_dispatch_hour_and_favor_facebook_reach(self):
        index, fb = [], {}
        for i in range(8):
            for hour, views in ((12, 900), (16, 20)):
                at = NOW - timedelta(days=3 + i, hours=NOW.hour - hour) + timedelta(minutes=11)
                index.append({'scheduled': True, 'channel': 'histold', 'video_id': f'{hour}-{i}',
                              'facebook_id': f'fb{hour}-{i}', 'published_at': at.isoformat()})
                fb[f'fb{hour}-{i}'] = {'views': views}
        stats = growth.hour_stats(index, {'yt': {}, 'ig': {}, 'fb': fb}, NOW)
        self.assertEqual(sorted(stats), [12, 16])
        self.assertGreater(stats[12]['shrunk'], stats[16]['shrunk'])


if __name__ == '__main__':
    unittest.main()
