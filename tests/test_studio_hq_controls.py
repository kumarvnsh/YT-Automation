from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
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
