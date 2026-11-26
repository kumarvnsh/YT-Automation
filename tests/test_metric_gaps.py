import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src import bot_findings, metric_gaps

NOW = datetime(2026, 11, 22, 6, 0, tzinfo=timezone.utc)


def video(identity, days_old, views, pct):
    return {'yt_id': identity, 'title': 'Video ' + identity, 'at': (NOW - timedelta(days=days_old)).isoformat(), 'yt': views, 'yt_pct': pct}


NORMAL = [video('aaaaaaaa', 6, 52, 43.0), video('bbbbbbbb', 5, 0, None), video('cccccccc', 1, 12, None)]
FAILURE = {'key': 'youtube-viewed-percentage-gap', 'title': 'YouTube viewing percentages are still missing',
           'plain': 'Viewing percentages are missing for several uploads.', 'evidence': '8 of 14 lack the metric'}


def findings(*failures, suspects=()):
    return {'active_failures': list(failures), 'suspect_metrics': list(suspects), 'decisions': []}


class Classify(unittest.TestCase):
    def test_statuses(self):
        self.assertEqual([metric_gaps.yt_pct_status(v, NOW) for v in NORMAL], ['reported', 'no_views_yet', 'reporting_delay'])
        self.assertEqual(metric_gaps.yt_pct_status(video('dddddddd', 5, 30, None), NOW), 'missing')

    def test_video_analytics_counted_no_views_for_is_not_a_fault(self):
        # 2 public views, 8 days old, Analytics report returned rows for others but none for it.
        uncounted = {**video('ZE9tGMBB7Fg', 8, 2, None), 'yt_analytics': 'no_row'}
        self.assertEqual(metric_gaps.yt_pct_status(uncounted, NOW), 'no_views_yet')
        failed_report = {**video('eeeeeeee', 8, 2, None), 'yt_analytics': 'unavailable'}
        self.assertEqual(metric_gaps.yt_pct_status(failed_report, NOW), 'missing')
        kept, settled = metric_gaps.screen(findings(FAILURE), NORMAL + [uncounted], NOW)
        self.assertEqual(kept['active_failures'], [])
        self.assertEqual(settled[0]['key'], FAILURE['key'])

    def test_annotate_counts_and_names_real_faults(self):
        videos, counts = metric_gaps.annotate(NORMAL + [video('dddddddd', 5, 30, None)], NOW)
        self.assertEqual(videos[1]['yt_pct_status'], 'no_views_yet')
        self.assertEqual((counts['reported'], counts['no_views_yet'], counts['reporting_delay'], counts['missing']), (1, 1, 1, 1))
        self.assertEqual(counts['missing_titles'], ['Video dddddddd'])

    def test_topic_matches_varied_wording(self):
        for text in ('youtube-viewed-percentage-gap', 'YT % viewed missing', 'avg view percentage absent', 'yt_pct gap',
                     'Average view duration unavailable', 'Retention data missing'):
            self.assertTrue(metric_gaps.about_percentages(text), text)
        self.assertFalse(metric_gaps.about_percentages('Upload workflow failed'))


class Screen(unittest.TestCase):
    def test_normal_delays_are_settled_and_other_failures_kept(self):
        other = {'key': 'upload-failed', 'title': 'Upload failed', 'plain': 'x', 'evidence': 'y'}
        suspects = [{'video_id': 'cccccccc', 'platform': 'yt', 'reason': 'Viewing percentage missing'},
                    {'video_id': 'aaaaaaaa', 'platform': 'fb', 'reason': 'Facebook plays absent'}]
        kept, settled = metric_gaps.screen(findings(FAILURE, other, suspects=suspects), NORMAL, NOW)
        self.assertEqual([f['key'] for f in kept['active_failures']], ['upload-failed'])
        self.assertEqual([s['platform'] for s in kept['suspect_metrics']], ['fb'])
        self.assertEqual(settled[0]['key'], FAILURE['key'])
        self.assertIn('1 video(s) have no views yet and 1 are under 3 days old', settled[0]['reason'])

    def test_a_real_gap_keeps_the_report(self):
        kept, settled = metric_gaps.screen(findings(FAILURE), NORMAL + [video('dddddddd', 5, 30, None)], NOW)
        self.assertEqual(kept['active_failures'], [FAILURE])
        self.assertEqual(settled, [])


class Settled(unittest.TestCase):
    def test_settled_problem_leaves_the_list_and_repeats_are_ignored(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            bot_findings.record(folder, 'histold', 'operations', 't1', findings(FAILURE), NOW)
            self.assertEqual(len(bot_findings.problems(folder, folder/'operator', 'histold')), 1)
            bot_findings.settle(folder, 'histold', FAILURE['key'], 'Normal delays', 'data check', now=NOW)
            self.assertEqual(bot_findings.problems(folder, folder/'operator', 'histold'), [])
            new = bot_findings.record(folder, 'histold', 'operations', 't2', findings(FAILURE), NOW + timedelta(hours=1))
            self.assertEqual(new, [])
            self.assertEqual(bot_findings.problems(folder, folder/'operator', 'histold'), [])
            self.assertEqual(bot_findings.persistent_failures(folder, 'histold', NOW + timedelta(hours=1)), [])
            self.assertEqual(bot_findings.settled(folder, 'histold', NOW)[0]['repeats'], 1)
            self.assertIn('Settled as not a problem', bot_findings.known_keys(folder, 'histold', folder/'operator'))

    def test_settling_expires(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            bot_findings.settle(folder, 'histold', FAILURE['key'], 'Normal delays', 'owner', now=NOW)
            later = NOW + bot_findings.SETTLED_TTL + timedelta(hours=1)
            new = bot_findings.record(folder, 'histold', 'operations', 't3', findings(FAILURE), later)
            self.assertEqual([item['key'] for item in new], [FAILURE['key']])


class TelegramButton(unittest.TestCase):
    def test_dismiss_is_allowlisted_with_strict_arguments(self):
        from src import telegram_control
        telegram_control.validate('dismiss_problem', {'channel': 'histold', 'key': 'youtube-viewed-percentage-gap'})
        with self.assertRaises(ValueError):
            telegram_control.validate('dismiss_problem', {'channel': 'histold', 'key': '../../x'})


if __name__ == '__main__':
    unittest.main()
