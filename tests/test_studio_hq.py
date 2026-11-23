"""Offline regression tests for phase-zero safety and nonfatal instrumentation."""
from contextlib import closing, contextmanager
import hashlib
from importlib.util import find_spec
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from src import studio_events as events
from studio_hq.archive import (Rclone, archive_one, archive_tick, atomic_json, checked_manifest, digest,
                               discover, fingerprint, package, restore, worker_lock)
from studio_hq.__main__ import load, sync_events
from studio_hq.finished_cleanup import cleanup_candidate, cleanup_tick, eligible_video


class FakeDrive:
    def __init__(self):
        self.files = {}
        self.corrupt = False

    def copy(self, source, target):
        if str(source).startswith('test-drive:'):
            Path(target).write_bytes(self.files[str(source)])
        else:
            value = Path(source).read_bytes()
            if target in self.files and self.files[target] != value:
                raise ValueError('Immutable destination differs')
            self.files[target] = value

    def stat(self, remote):
        value = self.files[remote]
        return {'Size': len(value), 'ID': 'observed-drive-id',
                'Hashes': {'MD5': 'bad' if self.corrupt else hashlib.md5(value).hexdigest()}}


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clip = self.root / 'shotsnip'; self.clip.mkdir()
        self.config = {'roots': {'shotsnip': str(self.clip), 'histold': str(self.root/'histold'),
                                 'astrotold': str(self.root/'astrotold')},
                       'state_dir': str(self.root/'state'), 'remote': 'test-drive:StudioHQ',
                       'local_cleanup_enabled': False}
        self.video = self.clip/'work/scheduler/run/highlight-finished/finished-short.mp4'
        self.video.parent.mkdir(parents=True)
        self.video.write_bytes(b'final-video-content')
        atomic_json(self.video.parent/'metadata.json', {'title': 'A sample'})
        atomic_json(self.clip/'work/scheduler/state.json',
                    {'uploads': [{'file': str(self.video), 'status': 'uploaded'}], 'ready_clips': []})
        events.record_finished(self.video, 'shotsnip', 'job-one')
        self.marker = self.video.with_name(self.video.name+'.studio-hq.json')
        value = json.loads(self.marker.read_text())
        value['completed_at'] = '2020-01-01T00:00:00+00:00'
        atomic_json(self.marker, value)

    def candidate(self):
        return discover(self.config)[0]

    def test_old_confirmed_final_is_eligible_and_does_not_include_raw(self):
        (self.video.parent/'capture.mp4').write_bytes(b'raw')
        (self.video.parent/'segment-1.ts').write_bytes(b'raw')
        (self.video.parent/'token.json').write_text('secret')
        manifest = package(self.candidate(), self.clip)
        self.assertEqual({r['name'] for r in manifest['files']}, {'finished-short.mp4','metadata.json'})

    def test_missing_completion_record_is_not_guessed_from_old_mtime(self):
        self.marker.unlink()
        self.assertFalse(self.candidate()['eligible'])

    def test_changed_final_is_protected(self):
        self.video.write_bytes(b'changed')
        self.assertIn('finished_media_changed', self.candidate()['reasons'])

    def test_recent_or_future_completion_is_protected(self):
        events.record_finished(self.video, 'shotsnip', 'job-one')
        row = json.loads(self.marker.read_text()); row['completed_at']='2999-01-01T00:00:00+00:00'
        atomic_json(self.marker,row)
        self.assertFalse(self.candidate()['eligible'])

    def test_keep_flag_protects_whole_run(self):
        (self.video.parent.parent/'.studio-hq-keep').touch()
        self.assertIn('keep_flag', self.candidate()['reasons'])

    def test_pending_and_uncertain_publication_are_protected(self):
        for status in ('pending','uncertain','facebook_uncertain','quarantined'):
            atomic_json(self.clip/'work/scheduler/state.json', {'uploads':[{'file':str(self.video),'status':status}]})
            self.assertFalse(self.candidate()['eligible'],status)

    def test_ready_queue_and_missing_ledger_protect_files(self):
        p=self.clip/'work/scheduler/state.json'
        atomic_json(p,{'uploads':[{'file':str(self.video),'status':'uploaded'}], 'ready_clips':[{'file':str(self.video)}]})
        self.assertIn('pending_publication',self.candidate()['reasons'])
        p.write_text('{broken')
        self.assertFalse(self.candidate()['eligible'])

    def test_manual_queue_dependency_protects_external_referenced_final(self):
        folder=self.clip/'work/manual'; folder.mkdir(parents=True)
        with closing(sqlite3.connect(folder/'jobs.sqlite')) as db, db:
            db.execute('CREATE TABLE jobs(id TEXT,status TEXT,result TEXT)')
            db.execute('INSERT INTO jobs VALUES(?,?,?)',('manual-1','queued_publish',json.dumps({'file':str(self.video)})))
        self.assertIn('manual_job_needs_files',self.candidate()['reasons'])

    def test_wrong_channel_completion_marker_is_rejected(self):
        row=json.loads(self.marker.read_text());row['channel']='histold';atomic_json(self.marker,row)
        self.assertFalse(self.candidate()['eligible'])

    def test_no_delete_after_verified_archive_and_restore_preserves_age(self):
        drive=FakeDrive()
        value=archive_one(self.candidate(),self.config,drive)
        self.assertTrue(self.video.exists())
        self.assertEqual(value['status'],'verified_local_retained')
        restored=restore(value,self.config,drive)
        self.assertEqual(digest(Path(restored['path'])/self.video.name),digest(self.video))
        self.assertEqual(restored['original_completed_at'],'2020-01-01T00:00:00+00:00')
        self.assertFalse(restored['local_cleanup_enabled'])

    def test_batched_restore_uses_only_manifest_members_and_verifies_all(self):
        drive=FakeDrive(); value=archive_one(self.candidate(),self.config,drive)
        requests=[]
        def batch(source,target,names):
            requests.extend(names)
            for name in names:Path(target,name).write_bytes(drive.files[source+'/'+name])
        drive.restore_files=batch
        record=restore(value,self.config,drive)
        self.assertEqual(set(requests),{r['name'] for r in value['files']})
        self.assertTrue(Path(record['path'],self.video.name).exists())

    def test_retry_reuses_immutable_manifest(self):
        drive=FakeDrive(); first=archive_one(self.candidate(),self.config,drive)
        second=archive_one(self.candidate(),self.config,drive)
        self.assertEqual(first,second)

    def test_hourly_tick_retries_manifest_upload_then_skips_completed_package(self):
        drive = FakeDrive()
        real_copy = drive.copy
        def interrupt(source, target):
            if target.endswith('/archive-manifest.json'):
                raise RuntimeError('Transfer interrupted')
            real_copy(source, target)
        with patch.object(drive, 'copy', side_effect=interrupt):
            self.assertEqual(len(archive_tick(self.config, transfer=drive)['failed']), 1)
        self.assertEqual(len(archive_tick(self.config, transfer=drive)['archived']), 1)
        with patch.object(drive, 'copy', side_effect=AssertionError('Unexpected repeat upload')):
            self.assertEqual(archive_tick(self.config, transfer=drive)['archived'], [])
        self.assertTrue(self.video.is_file())

    def test_hourly_legacy_copy_requires_matching_published_bytes(self):
        self.marker.unlink()
        ledger = self.clip/'work/scheduler/state.json'
        upload = dict(file=str(self.video), status='uploaded', video_id='confirmed',
                      facebook={'status': 'published'}, finished_at='2020-01-01T00:00:00+00:00')
        atomic_json(ledger, {'uploads': [dict(upload, sha256='stale')]})
        drive = FakeDrive()
        self.assertEqual(archive_tick(self.config, transfer=drive)['archived'], [])
        atomic_json(ledger, {'uploads': [dict(upload, sha256=digest(self.video))]})
        self.assertEqual(len(archive_tick(self.config, transfer=drive)['archived']), 1)
        self.assertTrue(self.video.is_file())

    def test_finished_cleanup_requires_dual_publication_age_hash_and_no_dependencies(self):
        manifest = package(self.candidate(), self.clip)
        manifest['status'] = 'verified_local_retained'
        published = {'file': str(self.video), 'status': 'uploaded', 'video_id': 'confirmed',
                     'facebook': {'status': 'published'}, 'finished_at': '2020-01-01T00:00:00+00:00',
                     'sha256': digest(self.video)}
        ledger = {'uploads': [published], 'ready_clips': []}
        self.assertEqual(eligible_video(manifest, self.clip, ledger, [], time.time()), self.video)
        job = {'original_file': str(self.video)}
        self.assertIsNone(eligible_video(manifest, self.clip, ledger, [job], time.time()))
        published['finished_at'] = '2999-01-01T00:00:00+00:00'
        self.assertIsNone(eligible_video(manifest, self.clip, ledger, [], time.time()))
        published['finished_at'] = '2020-01-01T00:00:00+00:00'
        self.video.write_bytes(b'changed since archival')
        self.assertIsNone(eligible_video(manifest, self.clip, ledger, [], time.time()))

    def test_manual_cleanup_requires_completed_job_and_selected_destinations(self):
        video = self.clip/'work/manual/manual-1/reformatted/finished-short.mp4'
        video.parent.mkdir(parents=True)
        video.write_bytes(b'published manual render')
        manifest = package({'eligible': True, 'channel': 'shotsnip', 'path': str(video),
                            'completed_at': '2020-01-01T00:00:00+00:00', 'job_id': 'manual-1'}, self.clip)
        manifest['status'] = 'verified_local_retained'
        upload = {'file': str(video), 'status': 'uploaded', 'video_id': 'confirmed',
                  'finished_at': '2020-01-01T00:00:00+00:00', 'sha256': digest(video)}
        job = ('manual-1', 1577836800, {'platforms': ['youtube']},
               {'file': str(video), 'publication': {'youtube_id': 'confirmed'}})
        ledger = {'uploads': [upload], 'studio_hq_published_manual_jobs': [job]}
        self.assertEqual(eligible_video(manifest, self.clip, ledger, [], time.time()), video)
        job[2]['platforms'].append('facebook')
        self.assertIsNone(eligible_video(manifest, self.clip, ledger, [], time.time()))

    def test_both_story_channels_require_managed_published_stage_with_matching_marker(self):
        self.config['story_finished_cleanup_enabled'] = True
        for channel in ('histold', 'astrotold'):
            root = Path(self.config['roots'][channel])
            video = root/'output/story/video.mp4'
            video.parent.mkdir(parents=True)
            video.write_bytes(b'final story')
            events.record_finished(video, channel, 'story')
            marker_path = video.with_name('video.mp4.studio-hq.json')
            recorded = json.loads(marker_path.read_text(encoding='utf-8'))
            recorded['completed_at'] = '2020-01-01T00:00:00+00:00'
            atomic_json(marker_path, recorded)
            state = {'complete': True, 'no_upload': False, 'studio_hq_managed_output': True,
                     'youtube_id': 'confirmed', 'done': ['upload']}
            state_path = video.parent/'state.json'
            atomic_json(state_path, state)
            manifest = package({'eligible': True, 'channel': channel, 'path': str(video),
                                'completed_at': recorded['completed_at'], 'job_id': 'story'}, root)
            manifest['status'] = 'verified_local_retained'
            with cleanup_candidate(manifest, self.config) as candidate:
                self.assertEqual(candidate, video)
                self.assertTrue((video.parent/'.studio-hq-media-lock').is_dir())
            state['no_upload'] = True
            atomic_json(state_path, state)
            with cleanup_candidate(manifest, self.config) as candidate:
                self.assertIsNone(candidate)

    def test_hosted_story_cleanup_rejects_drafts_and_mismatched_extracted_video(self):
        self.config['story_finished_cleanup_enabled'] = True
        root = Path(self.config['state_dir'])/'github-artifacts'
        artifact = root/'42'
        video = artifact/'files/video.mp4'
        video.parent.mkdir(parents=True)
        video.write_bytes(b'hosted final')
        when = '2020-01-01T00:00:00+00:00'
        atomic_json(artifact/'source.json', {'id': 42, 'run_id': 123, 'channel': 'astrotold', 'created_at': when})
        manifest = package({'eligible': True, 'channel': 'astrotold', 'path': str(video),
                            'completed_at': when, 'job_id': 'github-artifact-42'}, root)
        manifest.update(status='verified_local_retained', github_artifact_id=42, github_run_id=123)
        for no_upload, media, eligible in ((True, b'hosted final', None), (False, b'tampered bytes', None),
                                           (False, b'hosted final', video)):
            with ZipFile(artifact/'artifact.zip', 'w') as bundle:
                bundle.writestr('video.mp4', media)
                bundle.writestr('state.json', json.dumps({'complete': True, 'no_upload': no_upload,
                                                          'youtube_id': 'confirmed', 'done': ['upload']}))
            with cleanup_candidate(manifest, self.config) as candidate:
                self.assertEqual(candidate, eligible)

    @unittest.skipUnless(find_spec('filelock'), 'requires the Shotsnip worker environment')
    def test_finished_cleanup_keeps_local_video_on_bad_restore_then_removes_only_verified_mp4(self):
        drive = FakeDrive()
        value = archive_one(self.candidate(), self.config, drive)
        atomic_json(self.clip/'work/scheduler/state.json', {'uploads': [{
            'file': str(self.video), 'status': 'uploaded', 'video_id': 'confirmed',
            'facebook': {'status': 'published'}, 'finished_at': value['completed_at'],
            'sha256': digest(self.video)}]})
        (self.clip/'work/dashboard').mkdir()
        queue = self.clip/'work/manual/jobs.sqlite'
        queue.parent.mkdir()
        with closing(sqlite3.connect(queue)) as db, db:
            db.execute('CREATE TABLE jobs(id TEXT, updated REAL, status TEXT, options TEXT, result TEXT)')
        self.config['cleanup_ffprobe'] = 'ffprobe'

        def restore_files(source, target, names):
            for name in names:
                (target/name).write_bytes(drive.files[source+'/'+name])

        with patch('studio_hq.finished_cleanup.Rclone') as transfer, patch('studio_hq.finished_cleanup.subprocess.run') as probe:
            probe.return_value = SimpleNamespace(returncode=0, stdout='codec_name=h264')
            transfer.return_value.restore_files.side_effect = lambda source, target, names: (target/names[0]).write_bytes(b'bad')
            self.assertEqual(cleanup_tick(self.config)['status'], 'failed')
            self.assertTrue(self.video.exists())
            transfer.return_value.restore_files.side_effect = restore_files
            self.assertEqual(cleanup_tick(self.config)['status'], 'removed')
        self.assertFalse(self.video.exists())
        # Verified on Drive: the whole Shotsnip run folder goes (raw recording, cuts, previews, metadata).
        self.assertFalse(self.video.parent.parent.exists())
        self.assertTrue((self.clip/'work/scheduler').is_dir())

    def test_rate_limit_defers_followup_without_another_provider_request(self):
        self.config.update(rclone='fake-rclone',rclone_config=str(self.root/'private/rclone.conf'))
        transfer=Rclone(self.config)
        response=SimpleNamespace(returncode=1,stderr='RATE_LIMIT_EXCEEDED',stdout='')
        with patch('studio_hq.archive.subprocess.run',return_value=response) as run:
            with self.assertRaises(RuntimeError):transfer.run('about','test-drive:','--json')
            with self.assertRaises(RuntimeError):transfer.run('about','test-drive:','--json')
            self.assertEqual(run.call_count,1)
        self.assertTrue(self.video.exists())

    def test_remote_corruption_never_marks_package_verified(self):
        drive=FakeDrive(); drive.corrupt=True
        with self.assertRaises(ValueError):archive_one(self.candidate(),self.config,drive)
        self.assertTrue(self.video.exists())
        self.assertFalse((self.root/'state/manifests').exists())

    def test_protection_rechecked_instead_of_trusting_old_plan(self):
        candidate=self.candidate()
        (self.video.parent/'.studio-hq-keep').touch()
        with self.assertRaises(ValueError):archive_one(candidate,self.config,FakeDrive())

    def test_source_change_during_upload_rejects_package(self):
        drive=FakeDrive(); original=drive.copy
        def mutate(source,target):
            original(source,target)
            if str(source)==str(self.video):self.video.write_bytes(b'new final')
        drive.copy=mutate
        with self.assertRaises(ValueError):archive_one(self.candidate(),self.config,drive)
        self.assertTrue(self.video.exists())

    def test_restore_rejects_traversal_and_non_media_files(self):
        value=package(self.candidate(),self.clip)
        for name in ('../escape','C:escape','token.json','folder/file','..\\escape'):
            value['files'][0]['name']=name
            with self.assertRaises(ValueError):checked_manifest(value)

    def test_lock_excludes_overlapping_archive_processes(self):
        with worker_lock(self.root/'state'):
            with self.assertRaises(OSError):
                with worker_lock(self.root/'state'):pass

    def test_config_refuses_cleanup_enabled(self):
        p=self.root/'config.json'; self.config['local_cleanup_enabled']=True
        atomic_json(p,self.config)
        with self.assertRaises(ValueError):load(p)

    def test_completion_timestamp_is_preserved_on_repeat(self):
        before=self.marker.read_bytes()
        self.assertTrue(events.record_finished(self.video,'shotsnip','job-one'))
        self.assertEqual(before,self.marker.read_bytes())


class TelemetryTests(unittest.TestCase):
    def test_failed_stream_is_observed_without_recording_secret(self):
        with TemporaryDirectory() as folder:
            @contextmanager
            def failure(**kwargs):
                raise RuntimeError('SECRET')
                yield
            with events.scope(folder,'histold','job-1','render'):
                with self.assertRaises(RuntimeError):
                    with events.stream_call('anthropic','motion',None,failure,model='test-model'):
                        pass
            text=next((Path(folder)/'data/private/studio-hq/events').glob('*.json')).read_text()
            self.assertNotIn('SECRET',text)
            self.assertEqual(json.loads(text)['cost_status'],'unknown_failed_stream')

    def test_usage_never_logs_arguments_secrets_or_response_content(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            response=SimpleNamespace(usage=SimpleNamespace(input_tokens=12,output_tokens=3),text='SECRET_RESPONSE')
            with events.scope(root,'histold','job-1','script'):
                returned=events.call('anthropic','script',None,lambda **kw:response,
                                     model='example-model',prompt='SECRET_PROMPT',token='SECRET_TOKEN')
            self.assertIs(returned,response)
            rows=list((root/'data/private/studio-hq/events').glob('*.json'))
            text=rows[0].read_text(); data=json.loads(text)
            self.assertNotIn('SECRET',text); self.assertIsNone(data['cost_usd'])
            self.assertEqual(data['usage']['input_tokens'],12)
            config={'roots':{'histold':str(root)},'state_dir':str(root/'index')}
            self.assertEqual(sync_events(config)['events_imported'],1)
            self.assertEqual(sync_events(config)['events_imported'],0)

    def test_failure_logs_type_and_preserves_original_exception(self):
        with TemporaryDirectory() as folder:
            failure=ValueError('SECRET')
            def fail(**kwargs):raise failure
            with events.scope(folder,'astrotold','job-1','research'):
                with self.assertRaises(ValueError) as caught:events.call('anthropic','research','model',fail)
            self.assertIs(caught.exception,failure)
            row=next((Path(folder)/'data/private/studio-hq/events').glob('*.json'))
            self.assertNotIn('SECRET',row.read_text())

    def test_telemetry_io_failure_does_not_fail_provider_call(self):
        with events.scope('missing','histold','j','script'), patch.object(Path,'mkdir',side_effect=OSError('disk full')):
            self.assertEqual(events.call('openai','script','model',lambda:42),42)


if __name__=='__main__':unittest.main()


def test_spent_cloud_bundles_are_dropped_only_after_their_video_was_removed(tmp_path):
    import json
    from studio_hq.finished_cleanup import drop_spent_bundles
    state = tmp_path
    for run, removed in (('111', True), ('222', False), ('333', True)):
        folder = state/'github-artifacts'/run
        (folder/'files').mkdir(parents=True)
        (folder/'artifact.zip').write_bytes(b'zip' * 10)
        video = folder/'files'/'video.mp4'
        if run == '333':
            video.write_bytes(b'v')  # receipt says removed but the video is still here: keep the evidence
        receipts = state/'finished-cleanup-receipts'
        receipts.mkdir(exist_ok=True)
        (receipts/f'{run}.json').write_text(json.dumps({'status': 'removed' if removed else 'verified_pending_removal',
                                                        'original': str(video)}))
    assert drop_spent_bundles(state) == {'removed': 1, 'bytes_removed': 30}
    assert not (state/'github-artifacts'/'111'/'artifact.zip').exists()
    assert (state/'github-artifacts'/'222'/'artifact.zip').exists() and (state/'github-artifacts'/'333'/'artifact.zip').exists()
