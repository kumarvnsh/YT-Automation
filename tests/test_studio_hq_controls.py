from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import time
import uuid

from studio_hq import dashboard_controls as controls


class DashboardControlsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = {
            'roots': {'shotsnip': str(self.root)},
            'state_dir': str(self.root),
            'dashboard_backup_dir': str(self.root/'private'),
        }

    def command(self, action, **options):
        return {'request_id': str(uuid.uuid4()), 'action': action, **options}

    def manual(self):
        repo = Path(__file__).resolve().parents[2]/'YT-Cliping-Automation'
        if not (repo/'manual_jobs.py').is_file():
            self.skipTest('Sibling Shotsnip checkout is unavailable')
        module = controls.manual_module(repo)
        patched = patch.object(controls, 'manual_module', return_value=module)
        patched.start()
        self.addCleanup(patched.stop)
        return module

    def preview(self):
        return self.command('create_preview', channel='shotsnip', mode='exact',
                            url='https://youtu.be/abcdefg1234', ranges='10 - 20\n30 - 40',
                            platforms=['youtube'])

    def test_overlapping_preview_requests_create_only_one_job_per_range(self):
        module = self.manual()
        first, second = self.preview(), self.preview()
        with ThreadPoolExecutor(max_workers=2) as pool:
            left, right = pool.map(lambda body: controls.submit(self.config, body), (first, second))
        self.assertEqual(left, right)
        rows = module.list_jobs(self.root)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['options']['publish'] is False for row in rows))
        self.assertEqual(controls.submit(self.config, first), left)
        subset = self.command('create_preview', channel='shotsnip', mode='exact',
                              url=first['url'], ranges='30 - 40', platforms=['youtube'])
        self.assertEqual(controls.submit(self.config, subset)['job_ids'], left['job_ids'][1:])
        with self.assertRaisesRegex(ValueError, 'different action'):
            controls.submit(self.config, dict(first, ranges='50 - 60'))

    def test_approval_checks_stale_preview_and_publication_ledger(self):
        module = self.manual()
        job_id = controls.submit(self.config, self.preview())['job_ids'][0]
        clip = self.root/'work/manual'/job_id/'clip.mp4'
        clip.parent.mkdir(parents=True)
        clip.touch()
        module.update(job_id, self.root, status='ready', result={'file': str(clip)})
        revision = module.get(job_id, self.root)['updated']
        body = self.command('publish', channel='shotsnip', job_id=job_id,
                            revision=revision, platforms=['youtube'])
        ledger = self.root/'work/scheduler/state.json'
        ledger.parent.mkdir()
        ledger.write_text(json.dumps({'uploads': [{'file': str(clip), 'status': 'pending'}]}))
        with self.assertRaisesRegex(ValueError, 'publication record'):
            controls.submit(self.config, body)
        ledger.write_text('{"uploads":[]}')
        with self.assertRaisesRegex(ValueError, 'job changed'):
            controls.submit(self.config, dict(body, revision=0))
        with self.assertRaisesRegex(ValueError, 'destinations changed'):
            controls.submit(self.config, dict(body, platforms=['facebook']))
        result = controls.submit(self.config, body)
        self.assertEqual(module.get(job_id, self.root)['status'], 'queued_publish')
        self.assertEqual(controls.submit(self.config, body), result)

    def test_interrupted_host_request_stays_in_review_and_alias_never_requeues(self):
        first = self.command('archive_now')
        duplicate = self.command('archive_now')
        self.assertEqual(controls.submit(self.config, first), controls.submit(self.config, duplicate))
        with closing(sqlite3.connect(self.root/'hub-commands.sqlite')) as db, db:
            db.execute("UPDATE commands SET status='running'")
        with patch.object(controls, 'backup_queue'), patch.object(controls, 'perform') as perform:
            controls.maintenance_tick(self.config)
            perform.assert_not_called()
        self.assertEqual(controls.overview(self.config)[0]['status'], 'review')
        with closing(sqlite3.connect(self.root/'hub-commands.sqlite')) as db, db:
            db.execute("UPDATE commands SET status='complete',message='Finished'")
        self.assertEqual(controls.submit(self.config, duplicate)['command_id'], first['request_id'])

    def test_queue_order_and_publish_time_controls(self):
        module = self.manual()
        ids = controls.submit(self.config, self.command('create_preview', channel='shotsnip', mode='exact',
                              url='https://youtu.be/abcdefg1234', ranges='10 - 20' + chr(10) + '30 - 40', platforms=['youtube']))['job_ids']
        for identity in ids:
            module.update(identity, self.root, status='queued_publish')
        # Queue order is creation time, ties broken by id, exactly as the worker claims.
        ids = sorted(ids, key=lambda identity: (module.get(identity, self.root)['created'], identity))
        second = module.get(ids[1], self.root)
        result = controls.submit(self.config, self.command('move_earlier', channel='shotsnip', job_id=ids[1], revision=second['updated']))
        self.assertIn('earlier', result['message'])
        self.assertEqual(module.claim(self.root)['id'], ids[1])
        first = module.get(ids[0], self.root)
        with self.assertRaisesRegex(ValueError, 'Choose a publish time'):
            controls.submit(self.config, self.command('schedule', channel='shotsnip', job_id=ids[0], revision=first['updated'], not_before='soon'))
        later = time.time() + 7200
        self.assertIn('waits until then', controls.submit(self.config, self.command('schedule', channel='shotsnip', job_id=ids[0],
                                                          revision=first['updated'], not_before=later))['message'])
        self.assertEqual(module.get(ids[0], self.root)['options']['not_before'], later)
        self.assertIsNone(module.claim(self.root))  # the only remaining clip is scheduled for later

    def test_backups_include_queue_and_command_receipts(self):
        self.manual()
        controls.submit(self.config, self.preview())
        controls.submit(self.config, self.command('archive_now'))
        controls.backup_queue(self.config)
        for file, table in (('manual-queue', 'studio_hq_requests'), ('hub-commands', 'commands')):
            with closing(sqlite3.connect(self.root/'private'/f'{file}.sqlite')) as db:
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 1)
        self.assertEqual(json.loads((self.root/'backups/status.json').read_text())['status'], 'verified')

    def test_cadence_checks_a_fresh_revision_and_preserves_scheduler_settings(self):
        filename = self.root/'scheduler.json'
        original = {'enabled': False, 'scan_minutes': 180, 'post_minutes': 180, 'max_ready_clips': 5}
        controls.atomic_json(filename, original)
        _, revision = controls.cadence_settings(filename)
        command = self.command('scheduler_cadence', channel='shotsnip', minutes=240, revision=revision)
        result = controls.submit(self.config, command)
        self.assertEqual(controls.submit(self.config, command), result)
        with self.assertRaisesRegex(ValueError, 'existing Shotsnip cadences'):
            controls.submit(self.config, dict(command, minutes=97))
        with patch.object(controls, 'call_scheduler') as send:
            controls.atomic_json(filename, dict(original, live_operations_batch_hold={'batch':'fixture'}))
            with self.assertRaisesRegex(ValueError, 'schedule changed'):
                controls.perform(self.config, command)
            _, held_revision = controls.cadence_settings(filename)
            with self.assertRaisesRegex(ValueError, 'active hold'):
                controls.perform(self.config, dict(command, revision=held_revision))
            send.assert_not_called()
            controls.atomic_json(filename, original)
            send.side_effect = lambda config, payload: controls.atomic_json(
                filename, dict(original, scan_minutes=payload['minutes'], post_minutes=payload['minutes']))
            self.assertIn('240 minutes', controls.perform(self.config, command))
            send.assert_called_once_with(self.config, {'name':'set_cadence','minutes':240})
        self.assertEqual(controls.read_json(filename), dict(original, scan_minutes=240, post_minutes=240))


class TargetControlTests(unittest.TestCase):
    """Targets are changed only through the existing Shotsnip dashboard owner."""
    def setUp(self):
        from scripts import clipping_dashboard
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = {'roots': {'shotsnip': str(self.root)}, 'state_dir': str(self.root)}
        controls.atomic_json(self.root/'watchlist.json', {'creators': [
            {'id': 'fixture', 'name': 'Fixture Creator', 'enabled': True,
             'sources': [{'platform': 'twitch', 'url': 'https://www.twitch.tv/fixture', 'login': 'fixture'}]}]})
        patched = patch.object(clipping_dashboard, 'CLIP_ROOT', self.root)
        patched.start()
        self.addCleanup(patched.stop)
        self.real_call = controls.call_scheduler
        # Stand-in for the HTTP hop: the real owner handler runs against the fixture root.
        owner = patch.object(controls, 'call_scheduler',
                             side_effect=lambda config, payload, **_: clipping_dashboard.action(payload))
        self.owner = owner.start()
        self.addCleanup(owner.stop)

    def command(self, action, **options):
        return {'request_id': str(uuid.uuid4()), 'action': action, 'channel': 'shotsnip', **options}

    def revision(self):
        return controls.watchlist_state(self.root)[1]

    def test_toggle_requires_a_fresh_revision_and_is_verified(self):
        stale = self.command('target_toggle', id='fixture', enabled=False, revision='0' * 64)
        controls.submit(self.config, stale)
        with self.assertRaisesRegex(ValueError, 'target list changed'):
            controls.perform(self.config, stale)
        self.owner.assert_not_called()
        body = self.command('target_toggle', id='fixture', enabled=False, revision=self.revision())
        self.assertIn('paused', controls.perform(self.config, body))
        self.assertFalse(controls.watchlist_state(self.root)[0][0]['enabled'])
        again = dict(body, revision=self.revision())
        self.assertIn('already paused', controls.perform(self.config, again))
        self.assertEqual(self.owner.call_count, 1)

    def test_toggle_rejects_unknown_targets_and_bad_input(self):
        with self.assertRaisesRegex(ValueError, 'Choose a target'):
            controls.submit(self.config, self.command('target_toggle', id='../x', enabled=False, revision='0' * 64))
        with self.assertRaisesRegex(ValueError, 'This queue belongs to Shotsnip'):
            controls.submit(self.config, dict(self.command('target_add', url='https://www.twitch.tv/new'), channel='histold'))
        body = self.command('target_toggle', id='missing', enabled=False, revision=self.revision())
        with self.assertRaisesRegex(ValueError, 'no longer on the watchlist'):
            controls.perform(self.config, body)

    def test_add_validates_before_queueing_and_verifies_the_owner_write(self):
        with self.assertRaisesRegex(ValueError, 'Only YouTube channel and Twitch profile'):
            controls.submit(self.config, self.command('target_add', url='https://example.com/creator'))
        with self.assertRaisesRegex(ValueError, 'without /watch'):
            controls.submit(self.config, self.command('target_add', url='https://www.youtube.com/watch?v=abc'))
        body = self.command('target_add', url='https://twitch.tv/NewCreator/')
        controls.submit(self.config, body)
        queued = json.loads(controls_row(self.root)['body'])
        self.assertEqual(queued['url'], 'https://www.twitch.tv/newcreator')
        self.assertIn('Added newcreator', controls.perform(self.config, queued))
        self.assertEqual(len(controls.watchlist_state(self.root)[0]), 2)
        duplicate = self.command('target_add', url='https://www.twitch.tv/fixture')
        controls.submit(self.config, duplicate)
        self.assertIn('already on the watchlist', controls.perform(self.config, json.loads(controls_row(self.root, duplicate)['body'])))

    def test_unverified_owner_reply_goes_to_review(self):
        self.owner.side_effect = lambda config, payload, **_: 'Added it.'
        body = self.command('target_add', url='https://www.twitch.tv/ghost')
        controls.submit(self.config, body)
        with patch.object(controls, 'backup_queue'):
            controls.maintenance_tick(self.config)
        latest = controls.overview(self.config)[0]
        self.assertEqual((latest['action'], latest['status']), ('target_add', 'review'))

    def test_owner_validation_errors_surface_as_rejections(self):
        class Reply:
            status = 400
            def read(self, size):
                return json.dumps({'error': 'Unknown target'}).encode()
        class Connection:
            def __init__(self, *args, **kwargs): pass
            def request(self, *args): pass
            def getresponse(self): return Reply()
            def close(self): pass
        (self.root/'key.txt').write_text('fixture-key')
        with patch.object(controls.http.client, 'HTTPConnection', Connection):
            with self.assertRaisesRegex(ValueError, 'Unknown target'):
                self.real_call(dict(self.config, dashboard_control_key=str(self.root/'key.txt')), {'name': 'set_target'})


def controls_row(root, body=None):
    with closing(controls.controls_db({'state_dir': str(root)})) as db:
        if body:
            return db.execute('SELECT body FROM commands WHERE id=?', (body['request_id'],)).fetchone()
        return db.execute('SELECT body FROM commands ORDER BY updated DESC LIMIT 1').fetchone()


class AgentControlTests(unittest.TestCase):
    """Agent learning and reviews run through the existing Shotsnip dashboard owner."""
    def setUp(self):
        from scripts import clipping_dashboard
        self.owner_module = clipping_dashboard
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = {'roots': {'shotsnip': str(self.root)}, 'state_dir': str(self.root)}
        controls.atomic_json(self.root/'work/dashboard/agent-policy.json',
                             {'learning_enabled': True, 'source_priorities': {'Fixture': 1.2}, 'updated_at': None})
        patched = patch.object(clipping_dashboard, 'CLIP_ROOT', self.root)
        patched.start()
        self.addCleanup(patched.stop)
        owner = patch.object(controls, 'call_scheduler',
                             side_effect=lambda config, payload, **_: clipping_dashboard.action(payload))
        self.owner = owner.start()
        self.addCleanup(owner.stop)

    def command(self, action, **options):
        return {'request_id': str(uuid.uuid4()), 'action': action, 'channel': 'shotsnip', **options}

    def test_learning_toggle_checks_expected_state_and_verifies(self):
        with self.assertRaisesRegex(ValueError, 'Refresh the agent status'):
            controls.submit(self.config, self.command('agent_learning', enabled=False))
        stale = self.command('agent_learning', enabled=True, expected=False)
        self.assertIn('already enabled', controls.perform(self.config, stale))
        wrong = self.command('agent_learning', enabled=False, expected=False)
        with self.assertRaisesRegex(ValueError, 'learning changed'):
            controls.perform(self.config, wrong)
        self.owner.assert_not_called()
        body = self.command('agent_learning', enabled=False, expected=True)
        self.assertIn('paused', controls.perform(self.config, body))
        policy = controls.read_json(self.root/'work/dashboard/agent-policy.json')
        self.assertEqual((policy['learning_enabled'], policy['source_priorities']), (False, {'Fixture': 1.2}))

    def test_review_waits_for_a_new_report(self):
        with self.assertRaisesRegex(ValueError, 'Choose a review'):
            controls.submit(self.config, self.command('agent_review', mode='publish'))
        body = self.command('agent_review', mode='review')
        self.assertIn('finished', controls.agent_change(self.config, body, wait=10))
        report = controls.read_json(self.root/'work/dashboard/agent-review.json')
        self.assertIn('No publishing', report['automation_scope'])

    def test_busy_owner_rejects_instead_of_queueing_twice(self):
        self.owner_module.JOB['running'] = True
        self.addCleanup(self.owner_module.JOB.update, running=False)
        with self.assertRaisesRegex(ValueError, 'already running'):
            controls.agent_change(self.config, self.command('agent_review', mode='review'), wait=0)


class AgentExportTests(unittest.TestCase):
    def test_capture_errors_keep_type_and_drop_paths(self):
        from studio_hq.dashboard_phase3 import capture_error
        self.assertEqual(capture_error(["PermissionError: [Errno 13] Permission denied: 'D:\\x\\file.ts'"]), 'PermissionError')
        self.assertEqual(capture_error("['RuntimeError: Live video capture stalled for 40 seconds']"),
                         'RuntimeError: Live video capture stalled for 40 seconds')
        self.assertEqual(capture_error(['ValueError: see https://example.invalid/x']), 'ValueError')
        self.assertEqual(capture_error(None), 'Capture error')


class LiveControlTests(unittest.TestCase):
    """Pause, resume, run-now and stop-after-stage run through the existing Shotsnip dashboard."""
    def setUp(self):
        from scripts import clipping_dashboard
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = {'roots': {'shotsnip': str(self.root)}, 'state_dir': str(self.root)}
        self.hold = {'batch': 'fixture-batch', 'job_ids': ['a'], 'paused_at': '2026-11-21T09:00:00+00:00'}
        controls.atomic_json(self.root/'scheduler.json', {'enabled': False, 'scan_minutes': 180, 'post_minutes': 180,
                                                          'live_operations_batch_hold': self.hold})
        controls.atomic_json(self.root/'work/scheduler/state.json', {'phase': 'queued', 'uploads': [{'status': 'uploaded'}]})
        for target in (patch.object(clipping_dashboard, 'CLIP_ROOT', self.root),
                       patch.object(controls, 'call_scheduler', side_effect=lambda config, payload, **_: clipping_dashboard.action(payload))):
            target.start()
            self.addCleanup(target.stop)

    def command(self, action, **options):
        return {'request_id': str(uuid.uuid4()), 'action': action, 'channel': 'shotsnip', **options}

    def test_resume_during_a_batch_hold_needs_explicit_confirmation(self):
        resume = self.command('scheduler', enabled=True, expected=False)
        with self.assertRaisesRegex(ValueError, 'Confirm ending the hold'):
            controls.perform(self.config, resume)
        self.assertIn('hold ended early', controls.perform(self.config, dict(resume, end_hold=True)))
        settings = controls.read_json(self.root/'scheduler.json')
        self.assertTrue(settings['enabled'])
        self.assertNotIn('live_operations_batch_hold', settings)
        self.assertEqual(settings['ended_batch_hold']['batch'], 'fixture-batch')
        pause = self.command('scheduler', enabled=False, expected=True)
        self.assertIn('paused', controls.perform(self.config, pause))
        self.assertFalse(controls.read_json(self.root/'scheduler.json')['enabled'])

    def test_unresolved_publication_blocks_resume(self):
        controls.atomic_json(self.root/'work/scheduler/state.json', {'phase': 'queued', 'uploads': [{'status': 'pending'}]})
        with self.assertRaisesRegex(ValueError, 'duplicate post'):
            controls.perform(self.config, self.command('scheduler', enabled=True, expected=False, end_hold=True))
        self.assertIn('live_operations_batch_hold', controls.read_json(self.root/'scheduler.json'))

    def test_run_now_and_cancel_are_verified(self):
        with self.assertRaisesRegex(ValueError, 'Resume automatic clipping'):
            controls.perform(self.config, self.command('live_run_now', cancel=False))
        controls.atomic_json(self.root/'scheduler.json', {'enabled': True})
        self.assertIn('requested', controls.perform(self.config, self.command('live_run_now', cancel=False)))
        self.assertTrue((self.root/'work/dashboard/run-now').exists())
        self.assertIn('withdrawn', controls.perform(self.config, self.command('live_run_now', cancel=True)))
        self.assertFalse((self.root/'work/dashboard/run-now').exists())
        with self.assertRaisesRegex(ValueError, 'Refresh the live status'):
            controls.submit(self.config, self.command('live_run_now'))

    def test_stop_after_stage_only_while_a_stage_runs(self):
        with self.assertRaisesRegex(ValueError, 'No live stage'):
            controls.perform(self.config, self.command('live_stop_after_stage'))
        controls.atomic_json(self.root/'work/scheduler/state.json', {'phase': 'capturing', 'uploads': []})
        self.assertIn('stop after this stage', controls.perform(self.config, self.command('live_stop_after_stage')))
        self.assertTrue((self.root/'work/scheduler/stop-after-stage').exists())


class OwnerControlTests(unittest.TestCase):
    """Remaining old-dashboard controls, each handed to its existing owner."""
    def setUp(self):
        from scripts import clipping_dashboard
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = {'roots': {'shotsnip': str(self.root)}, 'state_dir': str(self.root)}
        controls.atomic_json(self.root/'scheduler.json', {'enabled': True, 'commentary': {'enabled': True, 'mode': 'intro'}})
        for target in (patch.object(clipping_dashboard, 'CLIP_ROOT', self.root),
                       patch.object(controls, 'call_scheduler', side_effect=lambda config, payload, **_: clipping_dashboard.action(payload))):
            target.start()
            self.addCleanup(target.stop)

    def command(self, action, **options):
        return {'request_id': str(uuid.uuid4()), 'action': action, **options}

    def test_commentary_mode_is_set_and_verified(self):
        with self.assertRaisesRegex(ValueError, 'off, intro, or full'):
            controls.submit(self.config, self.command('shotsnip_commentary', channel='shotsnip', mode='loud'))
        self.assertIn('turned off', controls.perform(self.config, self.command('shotsnip_commentary', channel='shotsnip', mode='off')))
        self.assertFalse(controls.read_json(self.root/'scheduler.json')['commentary']['enabled'])
        self.assertIn('full narrated', controls.perform(self.config, self.command('shotsnip_commentary', channel='shotsnip', mode='full')))

    def test_posting_rules_are_validated_saved_and_verified(self):
        import shutil
        shutil.copy(r'D:\YT-Cliping-Automation\owner_settings.py', self.root/'owner_settings.py')
        revision = controls.cadence_settings(self.root/'scheduler.json')[1]
        with self.assertRaisesRegex(ValueError, 'Uploads a day must be from 1 to 12'):
            controls.submit(self.config, self.command('shotsnip_settings', channel='shotsnip', revision=revision,
                                                      changes={'youtube_rules.max_per_day': 40}))
        with self.assertRaisesRegex(ValueError, 'not a Shotsnip setting'):
            controls.submit(self.config, self.command('shotsnip_settings', channel='shotsnip', revision=revision,
                                                      changes={'enabled': False}))
        body = self.command('shotsnip_settings', channel='shotsnip', revision=revision,
                            changes={'youtube_rules.max_per_day': 4, 'privacy': 'unlisted', 'never_posted_cleanup_hours': 12})
        self.assertIn('Saved: Uploads a day', controls.perform(self.config, body))
        saved = controls.read_json(self.root/'scheduler.json')
        self.assertEqual((saved['youtube_rules']['max_per_day'], saved['privacy'], saved['never_posted_cleanup_hours']),
                         (4, 'unlisted', 12))
        self.assertTrue(saved['enabled'])  # other settings untouched
        with self.assertRaisesRegex(ValueError, 'settings changed'):  # stale revision
            controls.perform(self.config, body)

    def test_watch_worker_stop_is_verified(self):
        self.assertIn('Stop requested', controls.perform(self.config, self.command('shotsnip_watch_worker', channel='shotsnip', start=False)))
        self.assertTrue((self.root/'work/dashboard/stop-worker').exists())

    def test_histold_strategy_is_validated_before_queueing_and_sent_to_the_owner(self):
        with self.assertRaisesRegex(ValueError, 'Choose a Histold strategy control'):
            controls.submit(self.config, self.command('histold_strategy', channel='histold', name='delete_everything', args={}))
        with self.assertRaisesRegex(ValueError, 'unknown series'):
            controls.submit(self.config, self.command('histold_strategy', channel='histold', name='pause_option',
                                                      args={'dimension': 'series', 'option': 'no-such-series'}))
        with self.assertRaisesRegex(ValueError, 'belongs to Histold'):
            controls.submit(self.config, self.command('histold_strategy', channel='shotsnip', name='refresh_analytics', args={}))
        import yaml
        series = yaml.safe_load((Path(__file__).resolve().parents[1]/'config.yaml').read_text(encoding='utf-8'))['editorial']['series'][0]['id']
        body = self.command('histold_strategy', channel='histold', name='pause_option', args={'dimension': 'series', 'option': series})
        controls.submit(self.config, body)
        with patch.object(controls, 'owner_request', return_value={'message': 'Pause series — saved.'}) as owner:
            self.assertIn('saved', controls.perform(self.config, body))
            owner.assert_called_once_with(self.config, '/api/action', {'name': 'pause_option', 'args': {'dimension': 'series', 'option': series}}, timeout=60)
        with patch.object(controls, 'owner_request', return_value={'running': True, 'message': 'Qwen is reviewing the analytics…'}):
            self.assertIn('reviewing', controls.perform(self.config, self.command('histold_research', channel='histold')))


class AiSettingsTests(unittest.TestCase):
    def test_ai_settings_are_validated_and_sent_to_the_owner(self):
        with tempfile.TemporaryDirectory() as local:
            config = {'state_dir': local, 'roots': {}}
            body = lambda **options: {'request_id': str(uuid.uuid4()), 'action': 'ai_settings', 'channel': 'histold', **options}
            with self.assertRaisesRegex(ValueError, 'Choose which AI'):
                controls.submit(config, body(mode='gpt'))
            with self.assertRaisesRegex(ValueError, 'whole numbers'):
                controls.submit(config, body(provider='claude', per_5h=-1))
            with self.assertRaisesRegex(ValueError, 'Choose an AI setting'):
                controls.submit(config, body(provider='local', per_5h=5))
            with patch.object(controls, 'owner_request', return_value={'mode': 'local'}) as owner:
                self.assertIn('local', controls.perform(config, body(mode='local')))
                owner.assert_called_once_with(config, '/api/ai', {'mode': 'local'}, timeout=20)
            with patch.object(controls, 'owner_request', return_value={'mode': 'auto'}) as owner:
                controls.perform(config, body(provider='codex', per_5h=10, per_week=50, clear_cooldown=True))
                owner.assert_called_once_with(config, '/api/ai', {'codex': {'per_5h': 10, 'per_week': 50, 'clear_cooldown': True}}, timeout=20)


class BotWorkspaceTests(unittest.TestCase):
    def body(self, **options):
        return {'request_id': str(uuid.uuid4()), 'action': 'bot_action', **options}

    def test_bot_actions_are_checked_then_sent_to_the_bot_workspace(self):
        from studio_hq import bots
        with tempfile.TemporaryDirectory() as local:
            config = {'state_dir': local, 'roots': {}}
            for options, message in (({'bot_action': 'delete', 'channel': 'histold'}, 'Choose a bot action'),
                                     ({'bot_action': 'assign', 'channel': 'histold', 'bot': 'shotsnip'}, None),
                                     ({'bot_action': 'assign', 'channel': 'histold', 'bot': 'ghost'}, 'Choose a bot'),
                                     ({'bot_action': 'routine', 'channel': 'histold', 'bot': 'histold', 'interval': 5}, 'Choose off, hourly'),
                                     ({'bot_action': 'cancel', 'channel': 'histold', 'id': '../x'}, 'Choose an item'),
                                     ({'bot_action': 'approve_problem', 'channel': 'histold', 'key': 'Bad Key!'}, 'Choose a problem')):
                if message:
                    with self.assertRaisesRegex(ValueError, message):
                        controls.submit(config, self.body(**options))
            # Bot scope per channel is the owner's check; Studio HQ only checks the bot exists.
            self.assertEqual(bots.check({'bot_action': 'routine', 'channel': 'histold', 'bot': 'histold', 'interval': 3600, 'instruction': ' x '}),
                             {'action': 'routine', 'channel': 'histold', 'bot': 'histold', 'instruction': 'x', 'engine': 'analyst', 'interval': 3600})
            body = self.body(bot_action='choose_decision', channel='shotsnip', id='a' * 32, option='b', version=12.5)
            controls.submit(config, body)
            with patch.object(controls, 'owner_request', return_value={'message': 'Choice saved'}) as owner:
                self.assertEqual(controls.perform(config, body), 'Choice saved')
                owner.assert_called_once_with(config, '/api/bots/action', {'action': 'choose_decision', 'channel': 'shotsnip',
                                              'id': 'a' * 32, 'option': 'b', 'version': 12.5}, timeout=30)

    def test_snapshot_reads_the_bot_office_read_only(self):
        from studio_hq import bots
        with tempfile.TemporaryDirectory() as local:
            root = Path(local)
            (root/'bot-workspace').mkdir()
            with closing(sqlite3.connect(root/'bot-workspace/tasks.sqlite')) as db, db:
                db.executescript('''CREATE TABLE tasks(id,bot,channel,instruction,engine,status,stage,created,updated,result,error,remote_bot,remote_message,cancel,evidence);
                  CREATE TABLE notes(bot,channel,text); CREATE TABLE routines(bot,channel,engine,instruction,interval,next_at);
                  CREATE TABLE decisions(id,channel,bot,source_task,decision_key,payload,status,created,updated,selected,followup);
                  CREATE TABLE events(task,at,note);''')
                db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', ('t' * 32, 'histold', 'histold', 'Find one idea', 'analyst',
                           'completed', 'Done', 1.0, 2.0, 'Report body', None, None, None, 0, None))
                db.execute('INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?,?)', ('d' * 32, 'histold', 'histold', 't' * 32, 'k',
                           json.dumps({'title': 'Test it?', 'options': [{'id': 'yes', 'label': 'Yes', 'plan': 'Run it'}]}), 'pending', 1.0, 3.0, None, None))
            (root/'key.txt').write_text('k')
            view = bots.snapshot({'dashboard_control_key': str(root/'key.txt')})
            histold = view['channels']['histold']
            self.assertEqual(histold['tasks'][0]['report'], 'Report body')
            self.assertEqual(histold['decisions'][0]['options'][0]['label'], 'Yes')
            self.assertEqual({bot['id'] for bot in histold['bots']}, {'jarvis', 'histold', 'operations'})
