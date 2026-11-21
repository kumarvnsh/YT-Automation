from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from studio_hq.archive import atomic_json
from studio_hq.dashboard_phase3 import calendar, earnings, observation_report, shotsnip_workspace, topic_trace
from studio_hq.dashboard_snapshot import snapshot


class Phase3Tests(unittest.TestCase):
    def test_calendar_records_do_not_imply_a_future_shotsnip_slot_during_hold(self):
        with TemporaryDirectory() as local:
            root = Path(local)
            atomic_json(root/'scheduler.json', {'enabled': True, 'post_minutes': 180,
                                                'live_operations_batch_hold': {'batch': 'test'}})
            atomic_json(root/'work/scheduler/state.json', {'last_upload_at': 1795269600})
            time = datetime(2026, 11, 21, 20, tzinfo=timezone.utc)
            activity = [{'at': '2026-11-21T19:30:00+00:00', 'channel': 'shotsnip',
                         'title': 'Confirmed upload', 'status': 'Published', 'url': None, 'kind': 'Manual clip'}]
            result = calendar({}, root, root, activity, time)
            self.assertEqual(result['today'], '2026-11-22')
            self.assertEqual(result['shotsnip']['status'], 'On hold')
            self.assertEqual(result['shotsnip']['cadence_minutes'], 180)
            self.assertIsNone(result['shotsnip']['earliest'])
            self.assertEqual(result['shotsnip']['choices'], [])
            self.assertEqual([event['state'] for event in result['events']], ['Published'])

    def test_live_state_only_exports_allowlisted_stage_details(self):
        with TemporaryDirectory() as local:
            root = Path(local)
            atomic_json(root/'work/scheduler/state.json', {
                'phase': 'capturing', 'selected': {'channel': 'Fixture', 'url': 'https://private.invalid'},
                'uploads': [{'status': 'uncertain'}], 'events': [
                    {'phase': 'scanning', 'at': '2026-11-21T10:00:00+00:00', 'note': 'PRIVATE'},
                    {'phase': 'secret-file-name', 'at': '2026-11-21T10:01:00+00:00'},
                ],
            })
            live = shotsnip_workspace(root)['live']
            self.assertEqual(live['source'], 'Fixture')
            self.assertEqual(live['pending_publications'], 1)
            self.assertEqual(len(live['events']), 1)
            self.assertNotIn('PRIVATE', json.dumps(live))
            self.assertNotIn('private.invalid', json.dumps(live))

    def test_topic_link_uses_the_job_identity(self):
        with TemporaryDirectory() as local:
            root = Path(local)
            job = '20261120_080000_short'
            other = '20261120_090000_short'
            atomic_json(root/'data/topic_reservations.json', [
                {'job_id': job, 'title': 'The same title'}, {'job_id': other, 'title': 'The same title'},
            ])
            atomic_json(root/'data/published_index.json', [{'stage_dir_name': job, 'video_id': 'abcdefghijk'}])
            by_job = {item['job']: item for item in topic_trace(root, [])}
            self.assertEqual(by_job[job]['state'], 'Published')
            self.assertEqual(by_job[other]['state'], 'Reservation recorded')
            self.assertIsNone(by_job[other]['url'])

    def test_finance_requires_private_export_and_preserves_reporting_grain(self):
        with TemporaryDirectory() as local:
            state = Path(local)
            histold = state/'histold'
            config = {'state_dir': state, 'roots': {'histold': histold, 'shotsnip': state/'shotsnip'},
                      'dashboard_private_snapshot': str(state/'private/dashboard.json')}
            atomic_json(histold/'data/private/youtube-earnings.json', {
                'status': 'collected', 'currency': 'USD', 'requested_start': '2026-11-01',
                'data': {'columnHeaders': [{'name': 'day'}, {'name': 'estimatedRevenue'}],
                         'rows': [['2026-11-03', 1.2], ['2026-11-04', 0.4]]},
            })
            self.assertEqual(earnings(histold)[0]['value'], '1.6')
            self.assertEqual(earnings(histold)[0]['start'], '2026-11-03')
            self.assertEqual(snapshot(config)['phase3']['earnings'], [])
            self.assertEqual(snapshot(config, include_finance=True)['phase3']['earnings'][0]['currency'], 'USD')

    def test_failed_attempt_costs_are_counted_only_with_a_pricing_basis(self):
        with TemporaryDirectory() as local:
            state = Path(local)
            job = '20261120_080000_short'
            with closing(sqlite3.connect(state/'observations.sqlite')) as db, db:
                db.execute('CREATE TABLE events(channel,job_id,stage,observed_at,kind,payload)')
                for channel, identity, fields in [
                    ('histold', job, {'status': 'failed', 'cost_usd': '0.25', 'pricing_basis': 'fixture receipt'}),
                    ('histold', job, {'status': 'failed', 'cost_usd': '1.00'}),
                    ('histold', 'unassigned', {'status': 'response_received'}),
                    ('astrotold', job, {'status': 'failed', 'cost_usd': '2.00', 'pricing_basis': 'fixture receipt'}),
                ]:
                    db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',
                               (channel, identity, 'render', '2026-11-20T00:00:00+00:00', 'api_usage',
                                json.dumps(dict(fields, provider='fixture'))))
            topics = [{'job': job, 'title': 'Fixture topic', 'state': 'Published', 'date': '2026-11-20', 'url': None}]
            report = observation_report(state, datetime(2026, 11, 21, tzinfo=timezone.utc), topics=topics)
            self.assertEqual(report['providers'], [{'channel': 'histold', 'provider': 'fixture', 'attempts': 3,
                                                   'failed': 2, 'priced': 1, 'cost': '0.25'}])
            self.assertEqual(report['unattributed'], 1)
            linked, = report['jobs']
            self.assertEqual((linked['id'], linked['title'], linked['attempts'], linked['cost']), (job, 'Fixture topic', 2, '0.25'))
            self.assertEqual(linked['breakdown'][0]['stage'], 'render')


class IdeaTraceTests(unittest.TestCase):
    def write(self, root, name, value):
        atomic_json(root/'data'/name, value)

    def test_idea_traces_to_run_publications_and_labelled_observations(self):
        with TemporaryDirectory() as local:
            root = Path(local)
            job, failed = '20261120_080000_123456_short', '20261120_090000_654321_short'
            self.write(root, 'topic_reservations.json', [
                {'job_id': job, 'title': 'Canary', 'date': '2026-11-20', 'slot': 'morning'},
                {'job_id': failed, 'title': 'Never rendered', 'date': '2026-11-20', 'slot': 'evening'}])
            selection = {'selected_index': 1, 'selection_reason': 'Fits the lane.', 'candidates': [
                {'premise': 'Icebox', 'rejection_reason': 'Recently covered.'},
                {'premise': 'Canary in the mine', 'viewer_reason': 'Simple mechanism.', 'rejection_reason': None}]}
            self.write(root, 'published_index.json', [
                {'stage_dir_name': job, 'video_id': 'abcdefghijk', 'facebook_id': '111', 'instagram_id': '222',
                 'run_id': '44900498335', 'published_at': '2026-11-20T09:00:00+00:00', 'workflow': 'publish',
                 'editorial_selection': selection, 'editorial_series': 'ingenious_solutions', 'duration': 30.2,
                 'ab_test': {'id': 'voice-title-2x2', 'title_style': 'question', 'voice': 'edge'}},
                {'stage_dir_name': job, 'video_id': 'zyxwvutsrqp', 'run_id': '44900498399',
                 'published_at': '2026-11-21T09:00:00+00:00', 'workflow': 'republish', 'republished_from': 'abcdefghijk'}])
            self.write(root, 'analytics.json', {'channels': {'histold': {'updated': '2026-11-21T09:00:00+00:00', 'videos': [
                {'id': 'abcdefghijk', 'publishedAt': '2026-11-20T09:00:00Z', 'views': 120, 'likes': 4}]}}})
            self.write(root, 'meta_analytics.json', {
                'facebook': {'updated': '2026-11-20T12:00:00+00:00', 'videos': [
                    {'id': '111', 'publishedAt': '2026-11-20T09:00:00+0000', 'plays': 50, 'views': 3,
                     'views_metric': 'fb_reels_total_plays'}]},
                'instagram': {'updated': '2026-11-20T12:00:00+00:00', 'videos': []}})
            rows = {row['job']: row['trace'] for row in topic_trace(root, [], 'owner/repo')}
            trace = rows[job]
            self.assertEqual(trace['idea']['premise'], 'Canary in the mine')
            self.assertEqual(trace['idea']['rejected'], [{'premise': 'Icebox', 'reason': 'Recently covered.'}])
            self.assertEqual(trace['run']['url'], 'https://github.com/owner/repo/actions/runs/44900498335')
            self.assertEqual(len(trace['publications']), 2)
            self.assertFalse(any('publication record for' in gap for gap in trace['gaps']))
            self.assertEqual(trace['publications'][1]['republished_from'], 'abcdefghijk')
            seen = {row['platform']: row for row in trace['observations']}
            self.assertEqual((seen['YouTube']['value'], seen['YouTube']['age_hours']), (120, 24))
            self.assertEqual((seen['Facebook']['value'], seen['Facebook']['metric']), (50, 'Reel plays (includes replays)'))
            self.assertIn('No current Instagram observation for media 222.', trace['gaps'])
            self.assertIn('No current YouTube observation for media zyxwvutsrqp.', trace['gaps'])  # republished copy
            self.assertNotIn('No current YouTube observation for media abcdefghijk.', trace['gaps'])
            self.assertEqual(trace['production']['Duration'], '30 s')
            self.assertTrue(trace['arms'][0].startswith('voice-title-2x2'))
            missing = rows[failed]
            self.assertEqual(missing['gaps'], ['The idea and its alternatives were not recorded for this job.',
                                               'No workflow run is linked to this job.',
                                               'No publication or draft is recorded for this reservation.'])


class ExperimentComparisonTests(unittest.TestCase):
    def video(self, lane, published, **checkpoints):
        return {'trial': {'id': 'trial-v1', 'lane': lane, 'planned_posts': 42}, 'published_at': published,
                'checkpoints': checkpoints}

    def run_report(self, videos, index=(), updated='2026-11-21T00:00:00+00:00', now=None):
        from studio_hq.dashboard_phase3 import experiment_comparisons
        with TemporaryDirectory() as local:
            root = Path(local)
            atomic_json(root/'data/private/growth-trial-observations.json',
                        {'updated': updated, 'videos': {str(i): video for i, video in enumerate(videos)}})
            atomic_json(root/'data/published_index.json', list(index))
            return experiment_comparisons(root, now or datetime(2026, 11, 21, 1, tzinfo=timezone.utc))

    def test_arms_compare_only_within_age_platform_and_metric(self):
        lanes = ('ingenious_solutions', 'decisions', 'everyday_origins')
        videos = []
        for lane, plays in zip(lanes, ((100, 110, 120), (300, 310, 320), (90, 95, 105))):
            for value in plays:
                videos.append(self.video(lane, '2026-11-19T00:00:00+00:00', **{
                    '24h': {'facebook': {'metrics': {'plays': value, 'views_metric': 'fb_reels_total_plays', 'like_rate': 0.01}},
                            'youtube': {'metrics': {'views': value // 10}}}}))
        # A legacy Facebook definition must never join the Reel-plays comparison.
        videos.append(self.video('decisions', '2026-11-19T00:00:00+00:00', **{
            '24h': {'facebook': {'metrics': {'plays': 9999, 'views_metric': 'legacy_video_views'}}}}))
        report = self.run_report(videos)
        groups = {(g['checkpoint'], g['platform']): g for g in report['trials'][0]['groups']}
        facebook = groups[('24h', 'Facebook')]
        self.assertTrue(facebook['comparable'])
        self.assertEqual(facebook['leader'], 'decisions')
        self.assertFalse(facebook['overlap'])
        self.assertEqual(next(a for a in facebook['arms'] if a['lane'] == 'decisions')['high'], 320)
        self.assertFalse(groups[('24h', 'YouTube')]['overlap'])  # no other arm reaches the leader's 30-32 range
        # One high post in another arm overlaps the leader, so the lead is not reliable.
        videos.append(self.video('everyday_origins', '2026-11-19T00:00:00+00:00', **{
            '24h': {'facebook': {'metrics': {'plays': 305, 'views_metric': 'fb_reels_total_plays'}}}}))
        overlapping = self.run_report(videos)
        facebook = next(g for g in overlapping['trials'][0]['groups'] if g['platform'] == 'Facebook')
        self.assertTrue(facebook['overlap'])
        self.assertIn('not a reliable difference',
                      next(r for r in overlapping['recommendations'] if r['kind'] == 'observe' and 'Facebook' in r['title'])['uncertainty'])
        report = self.run_report(videos[:-1])
        titles = [row['title'] for row in report['recommendations']]
        self.assertIn('Decisions With Consequences leads Facebook at 24 h', titles)
        lead = next(row for row in report['recommendations'] if row['title'].endswith('Facebook at 24 h'))
        self.assertIn('Ranges separate', lead['uncertainty'])
        self.assertIn('nothing changes automatically', lead['action'])

    def test_thin_samples_missed_windows_stale_data_and_untracked_tests(self):
        videos = [self.video('decisions', '2026-11-18T00:00:00+00:00', **{
                      '24h': {'youtube': {'metrics': {'views': 4}}}}),
                  self.video('everyday_origins', '2026-11-20T23:00:00+00:00')]
        index = [{'ab_test': {'id': 'voice-title-2x2'}}, {'ab_test': {'id': 'voice-title-2x2'}}]
        report = self.run_report(videos, index, now=datetime(2026, 11, 21, 12, tzinfo=timezone.utc))
        kinds = {row['title']: row for row in report['recommendations']}
        self.assertIn('Keep collecting trial-v1', kinds)
        self.assertFalse(report['trials'][0]['groups'][0]['comparable'])
        missed = {(row['checkpoint'], row['platform']): row['count'] for row in report['trials'][0]['missed']}
        self.assertEqual(missed[('24h', 'Facebook')], 1)  # the 3-day-old post has no Facebook 24 h checkpoint
        self.assertNotIn(('24h', 'YouTube'), missed)
        self.assertIn('Trial observations are stale', kinds)
        self.assertEqual(report['untracked'], [{'id': 'voice-title-2x2', 'published': 2}])


class RepostGapTests(unittest.TestCase):
    def test_repeated_repost_rows_show_once_and_a_lost_original_is_named(self):
        with TemporaryDirectory() as local:
            root = Path(local)
            job = '20261115_070306_414133_short'
            atomic_json(root/'data/topic_reservations.json', [{'job_id': job, 'title': 'Tin couriers', 'date': '2026-11-15'}])
            repost = {'stage_dir_name': job, 'video_id': 'bLKPvWMmM4g', 'workflow': 'republish', 'run_id': '1',
                      'published_at': '2026-11-16T15:13:00+00:00', 'republished_from': 'IeZApCwr2NE'}
            atomic_json(root/'data/published_index.json', [repost, dict(repost), dict(repost)])
            trace = topic_trace(root, [])[0]['trace']
            self.assertEqual(len(trace['publications']), 1)
            self.assertIn('The publication record for IeZApCwr2NE is missing; it was reposted as bLKPvWMmM4g.', trace['gaps'])
