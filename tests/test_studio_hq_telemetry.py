"""Fixture coverage for Studio HQ usage telemetry: scrubbing, hosted import and job joins."""
from contextlib import closing
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import unittest
import uuid
import zipfile

from scripts.export_studio_events import export
from src.studio_events import call, scope, scrub
from studio_hq.__main__ import sync_events, sync_hosted_events
from studio_hq.dashboard_phase3 import observation_report

JOB = '20261120_080000_123456_short'
OTHER = '20261120_090000_654321_short'
NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)


def event(job=JOB, stage='script', kind='api_usage', at='2026-11-20T08:01:00+00:00', channel='histold', **fields):
    return dict(schema_version=1, event_id=uuid.uuid4().hex, observed_at=at, channel=channel,
                job_id=job, stage=stage, kind=kind, **fields)


def bundle(rows, extra=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for row in rows:
            archive.writestr(row['event_id'] + '.json', json.dumps(row))
        for name, body in (extra or {}).items():
            archive.writestr(name, body)
    return buffer.getvalue()


class FakeGitHub:
    """Stands in for `gh api`; records calls, never touches the network."""
    def __init__(self, artifacts, downloads):
        self.artifacts, self.downloads, self.calls = artifacts, downloads, []

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if '--paginate' in args:
            body = '\n'.join(json.dumps(item) for item in self.artifacts)
            return subprocess.CompletedProcess(args, 0, body, '')
        artifact = int(args[2].split('/')[-2])
        payload = self.downloads.get(artifact)
        return subprocess.CompletedProcess(args, 0 if payload is not None else 1, payload or b'', b'')


def index_rows(folder):
    with closing(sqlite3.connect(Path(folder) / 'observations.sqlite')) as db:
        return [json.loads(row[0]) for row in db.execute('SELECT payload FROM events ORDER BY observed_at')]


class ScrubTests(unittest.TestCase):
    def test_scrub_drops_free_text_and_keeps_usage_numbers(self):
        row = scrub(event(provider='anthropic', status='failed', error_type='APIError', cost_usd='0.01',
                          pricing_basis='fixture receipt', prompt='SECRET PROMPT', url='https://private.invalid',
                          message='traceback with token', elapsed_seconds=1.5, cancel=False,
                          usage={'input_tokens': 10, 'output_tokens': 4, 'raw': 'SECRET', 'bogus': 1}))
        text = json.dumps(row)
        for leaked in ('SECRET', 'private.invalid', 'traceback', 'bogus'):
            self.assertNotIn(leaked, text)
        self.assertEqual(row['usage'], {'input_tokens': 10, 'output_tokens': 4})
        self.assertEqual((row['elapsed_seconds'], row['cancel'], row['cost_usd']), (1.5, False, '0.01'))

    def test_scrub_rejects_rows_without_identity(self):
        self.assertIsNone(scrub(dict(event(), job_id=None)))
        self.assertIsNone(scrub(dict(event(), schema_version=2)))
        self.assertIsNone(scrub('not a row'))

    def test_local_sync_stores_only_scrubbed_payloads(self):
        with TemporaryDirectory() as local:
            root = Path(local) / 'histold'
            folder = root / 'data/private/studio-hq/events'
            folder.mkdir(parents=True)
            row = event(provider='openai', prompt='SECRET PROMPT')
            (folder / (row['event_id'] + '.json')).write_text(json.dumps(row), encoding='utf-8')
            mislabelled = event(channel='shotsnip', provider='openai')
            (folder / (mislabelled['event_id'] + '.json')).write_text(json.dumps(mislabelled), encoding='utf-8')
            state = Path(local) / 'index'
            self.assertEqual(sync_events({'state_dir': state, 'roots': {'histold': root}})['events_imported'], 1)
            self.assertEqual(sync_events({'state_dir': state, 'roots': {'histold': root}})['events_imported'], 0)
            stored, = index_rows(state)
            self.assertNotIn('SECRET', json.dumps(stored))


class EmitterTests(unittest.TestCase):
    def test_http_error_responses_count_as_failed_attempts(self):
        class Response:
            def __init__(self, status):
                self.status_code = status
        with TemporaryDirectory() as local:
            with scope(local, 'histold', JOB, 'voice'):
                call('elevenlabs', 'speech', 'fixture', lambda **_: Response(429))
                call('elevenlabs', 'speech', 'fixture', lambda **_: Response(200))
            rows = [json.loads(path.read_text(encoding='utf-8'))
                    for path in (Path(local) / 'data/private/studio-hq/events').glob('*.json')]
        self.assertEqual(sorted((row['status'], row.get('http_status')) for row in rows),
                         [('failed', 429), ('response_received', None)])


class HostedExportTests(unittest.TestCase):
    def test_export_keeps_only_this_job_and_scrubs(self):
        with TemporaryDirectory() as local:
            events, out = Path(local) / 'events', Path(local) / 'out'
            events.mkdir()
            rows = [event(provider='anthropic', prompt='SECRET'), event(job=OTHER, provider='anthropic'),
                    event(channel='astrotold', provider='anthropic')]
            for row in rows:
                (events / (row['event_id'] + '.json')).write_text(json.dumps(row), encoding='utf-8')
            (events / 'notes.json').write_text('{}', encoding='utf-8')
            self.assertEqual(export(events, out, JOB), 1)
            exported, = out.iterdir()
            self.assertEqual(exported.stem, rows[0]['event_id'])
            self.assertNotIn('SECRET', exported.read_text(encoding='utf-8'))

    def test_export_without_events_is_a_quiet_no_op(self):
        with TemporaryDirectory() as local:
            self.assertEqual(export(Path(local) / 'missing', Path(local) / 'out', JOB), 0)
            self.assertFalse((Path(local) / 'out').exists())


class HostedImportTests(unittest.TestCase):
    def test_hosted_events_map_by_exact_run_and_job(self):
        good = [event(provider='anthropic', status='response_received', usage={'input_tokens': 5}),
                event(provider='elevenlabs', stage='voice', status='failed', error_type='Timeout')]
        wrong_job = event(job=OTHER, provider='anthropic')
        github = FakeGitHub(
            [{'id': 11, 'name': 'studio-hq-events-' + JOB, 'size_in_bytes': 900, 'run_id': 501},
             {'id': 12, 'name': 'studio-hq-events-not-a-job', 'size_in_bytes': 10, 'run_id': 502},
             {'id': 13, 'name': 'studio-hq-events-' + OTHER, 'size_in_bytes': 10, 'run_id': 503}],
            {11: bundle(good + [wrong_job], {'../escape.json': '{}', 'readme.txt': 'x'})})
        with TemporaryDirectory() as local:
            report = sync_hosted_events({}, local, run=github)
            self.assertEqual(report, {'artifacts': 2, 'events_imported': 2, 'rejected': 3, 'skipped': 1})
            rows = index_rows(local)
            self.assertEqual({row['hosted_run_id'] for row in rows}, {501})
            self.assertEqual({row['job_id'] for row in rows}, {JOB})
            ledger = json.loads((Path(local) / 'hosted-events.json').read_text(encoding='utf-8'))
            self.assertNotIn('13', ledger)  # failed download is retried on the next pass

            github.downloads[13] = bundle([event(job=OTHER, provider='openai')])
            again = sync_hosted_events({}, local, run=github)
            self.assertEqual((again['artifacts'], again['events_imported']), (1, 1))
            downloads = [call for call in github.calls if call[-1].endswith('/zip')]
            self.assertEqual(sum(call[-1].endswith('/11/zip') for call in downloads), 1)

    def test_listing_failure_is_reported_without_touching_the_index(self):
        def failing(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, '', 'auth required')
        with TemporaryDirectory() as local:
            with self.assertRaises(RuntimeError):
                sync_hosted_events({}, local, run=failing)
            self.assertFalse((Path(local) / 'observations.sqlite').exists())


class UsageReportTests(unittest.TestCase):
    def write(self, folder, rows):
        with closing(sqlite3.connect(Path(folder) / 'observations.sqlite')) as db, db:
            db.execute('CREATE TABLE events(event_id TEXT PRIMARY KEY,channel,job_id,stage,kind,observed_at,payload)')
            for row in rows:
                db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)', (row['event_id'], row['channel'], row['job_id'],
                           row['stage'], row['kind'], row['observed_at'], json.dumps(row)))

    def test_retries_providers_pricing_and_unattributed_records(self):
        priced = dict(cost_usd='0.0100', pricing_basis='fixture receipt')
        rows = [
            event(provider='anthropic', status='failed', at='2026-11-20T08:01:00+00:00', **priced),
            event(provider='anthropic', status='failed', at='2026-11-20T08:02:00+00:00'),
            event(provider='anthropic', status='response_received', at='2026-11-20T08:03:00+00:00', **priced),
            event(provider='elevenlabs', stage='voice', status='response_received', at='2026-11-20T08:04:00+00:00',
                  cost_usd='0.0200'),  # a charge without a pricing basis stays unpriced
            event(provider='openai', stage='thumbnail', status='response_received', at='2026-11-20T08:05:00+00:00',
                  cost_usd='-1', pricing_basis='fixture receipt'),  # negative charges are ignored
            event(job='strategist', provider='anthropic', at='2026-11-20T08:06:00+00:00', **priced),
            event(kind='stage_succeeded', stage='upload', at='2026-11-20T08:07:00+00:00'),
            event(provider='anthropic', at='2026-10-01T00:00:00+00:00', **priced),  # outside the window
        ]
        with TemporaryDirectory() as local:
            self.write(local, rows)
            topics = [{'job': JOB, 'title': 'Fixture topic', 'state': 'Published', 'date': '2026-11-20', 'url': None}]
            report = observation_report(Path(local), NOW, topics=topics)
        providers = {row['provider']: row for row in report['providers']}
        self.assertEqual((providers['anthropic']['attempts'], providers['anthropic']['failed'],
                          providers['anthropic']['priced'], providers['anthropic']['cost']), (4, 2, 3, '0.0300'))
        self.assertEqual((providers['elevenlabs']['priced'], providers['elevenlabs']['cost']), (0, None))
        self.assertEqual(report['unattributed'], 1)
        job, = report['jobs']
        self.assertEqual((job['attempts'], job['failed'], job['priced'], job['cost']), (5, 2, 2, '0.0200'))
        self.assertEqual((job['stage'], job['title']), ('upload', 'Fixture topic'))
        stages = {(row['stage'], row['provider']): row for row in job['breakdown']}
        self.assertEqual(stages[('script', 'anthropic')]['attempts'], 3)
        self.assertIsNone(stages[('voice', 'elevenlabs')]['cost'])
        self.assertIsNone(stages[('thumbnail', 'openai')]['cost'])

    def test_missing_index_reports_unavailable(self):
        with TemporaryDirectory() as local:
            report = observation_report(Path(local), NOW)
        self.assertFalse(report['available'])
        self.assertEqual(report['providers'], [])


if __name__ == '__main__':
    unittest.main()
