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
