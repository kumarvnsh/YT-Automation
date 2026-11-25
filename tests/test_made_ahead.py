"""Made-ahead Histold videos: rendered before the slot, claimed and uploaded when the hour starts."""
import json
import os
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts import dispatch
from scripts import run_local_publish as runner
from src.config import Config


def proc(stdout='', returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr='')


class Folder(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.patches = [mock.patch.object(runner, 'LOG_DIR', self.root / 'logs'),
                        mock.patch.object(runner, 'AHEAD_DIR', self.root / 'ahead'),
                        mock.patch.object(dispatch, 'AHEAD_DIR', self.root / 'ahead'),
                        mock.patch.object(dispatch, 'LOCAL_LOCK', self.root / 'lock'),
                        mock.patch.object(dispatch, 'SWITCH_FILE', self.root / 'no-switch.json'),
                        mock.patch.object(runner, 'ROOT', self.root / 'repo'),
                        mock.patch.object(runner, 'notify')]
        self.sent = [p.start() for p in self.patches][-1]
        (self.root / 'logs').mkdir()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.folder.cleanup()

    def stage(self, name, **state):
        path = self.root / 'repo' / 'output' / name
        path.mkdir(parents=True)
        (path / 'state.json').write_text(json.dumps(state))
        return path / 'state.json'


class Dispatcher(Folder):
    def cfg(self, hours=2):
        python = self.root / 'python.exe'
        python.write_text('')
        return Config({'local_production': {'enabled': True, 'python': str(python), 'make_ahead_hours': hours}})

    def test_starts_the_slot_two_hours_ahead_once(self):
        now = datetime(2026, 11, 25, 10, 3, tzinfo=timezone.utc)
        state = {}
        with mock.patch.object(dispatch, 'load_config', return_value=self.cfg()), \
                mock.patch.object(dispatch, 'scheduled_format', return_value=('short', 'plan')) as fmt, \
                mock.patch.object(dispatch.subprocess, 'Popen') as popen:
            self.assertTrue(dispatch.make_ahead(now, state, False))
            self.assertFalse(dispatch.make_ahead(now, state, False))  # same slot is not started twice
        self.assertEqual(fmt.call_args.args[2], datetime(2026, 11, 25, 12, tzinfo=timezone.utc))
        self.assertEqual(popen.call_args.args[0][-5:], ['--format', 'short', '--slot', '2026-11-25T12', '--ahead'])
        self.assertEqual(state['ahead'], '2026-11-25T12')

    def test_off_when_zero_or_not_a_publish_hour(self):
        now = datetime(2026, 11, 25, 10, 3, tzinfo=timezone.utc)
        with mock.patch.object(dispatch, 'load_config', return_value=self.cfg(0)), \
                mock.patch.object(dispatch.subprocess, 'Popen') as popen:
            self.assertFalse(dispatch.make_ahead(now, {}, False))
        with mock.patch.object(dispatch, 'load_config', return_value=self.cfg()), \
                mock.patch.object(dispatch, 'scheduled_format', return_value=(None, 'plan')):
            self.assertFalse(dispatch.make_ahead(now, {}, False))
        popen.assert_not_called()

    def write(self, state):
        (self.root / 'ahead').mkdir(exist_ok=True)
        (self.root / 'ahead' / 'S.json').write_text(json.dumps({'state': state}))

    def test_slot_hour_leaves_a_live_made_ahead_run_alone(self):
        self.assertFalse(dispatch.ahead_owns('S'))  # nothing made ahead: the usual run
        self.write('waiting')
        self.assertFalse(dispatch.ahead_owns('S'))  # its process is gone (no lock): the usual run
        (self.root / 'lock').write_text('')
        self.assertTrue(dispatch.ahead_owns('S'))
        self.assertFalse(dispatch.ahead_owns('S', now=time.time() + 6 * 3600))  # stale lock
        self.write('gave_up')
        self.assertFalse(dispatch.ahead_owns('S'))
        (self.root / 'lock').unlink()
        self.write('published')
        self.assertTrue(dispatch.ahead_owns('S'))


class Runner(Folder):
    def args(self, minutes=120):
        slot = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H')
        return SimpleNamespace(format='short', slot=slot, dry_run=False, no_upload=False, no_fallback=False,
                               stamp='T', ahead=True, stage=None)

    def ahead(self, args):
        return json.loads((self.root / 'ahead' / f'{args.slot}.json').read_text())

    def test_new_stage_is_made_without_upload_and_never_resumes_another(self):
        args = self.args()
        outputs = iter([proc('Created stage: 20261125_100000_000001_short\nSTATUS: MORE'), proc('STATUS: DONE')])
        with mock.patch.object(runner, 'stream', side_effect=lambda *a, **k: next(outputs)) as stream:
            status, _, stage = runner.produce_ahead(args, {})
        self.assertEqual((status, stage), ('DONE', '20261125_100000_000001_short'))
        first, second = (call.args[0] for call in stream.call_args_list)
        self.assertIn('--fresh', first)
        self.assertIn('--no-upload', first)
        self.assertEqual(second[-3:], ['--stage', '20261125_100000_000001_short', '--step'])
        self.assertNotIn('--force-upload', second)

    def run_ahead(self, args, produced, claimed, upload='STATUS: DONE'):
        class Watcher:
            def __init__(self, *a, **k):
                self.ok, self.key, self.stopped = claimed, 'slot-' + args.slot if claimed else None, False

            def start(self):
                pass

            def is_alive(self):
                return False

            def join(self):
                pass

            def stop(self):
                self.stopped = True
        with mock.patch.object(runner, 'claim_worktree', return_value=self.root), \
                mock.patch.object(runner, 'SlotClaim', Watcher), \
                mock.patch.object(runner, 'produce_ahead', return_value=produced), \
                mock.patch.object(runner, 'stream', return_value=proc(upload)) as stream, \
                mock.patch.object(runner, 'record_state', return_value='recorded'), \
                mock.patch.object(runner, 'fall_back', return_value=0) as fall_back:
            code = runner.make_ahead(args, {})
        return code, stream, fall_back

    def test_ready_video_uploads_that_stage_after_the_claim(self):
        args = self.args()
        code, stream, _ = self.run_ahead(args, ('DONE', '', 'STAGE'), claimed=True)
        self.assertEqual(code, 0)
        self.assertEqual(stream.call_args.args[0][-4:], ['--stage', 'STAGE', '--force-upload', '--step'])
        self.assertEqual(self.ahead(args)['state'], 'published')

    def test_unclaimed_slot_keeps_the_video_as_a_preview(self):
        args = self.args()
        code, stream, _ = self.run_ahead(args, ('DONE', '', 'STAGE'), claimed=False)
        self.assertEqual(code, 0)
        stream.assert_not_called()
        self.assertEqual(self.ahead(args)['state'], 'kept_as_preview')
        self.assertIn('preview', self.sent.call_args.args[0])

    def test_failure_before_the_slot_hands_the_stage_to_the_usual_run(self):
        args = self.args()
        path = self.stage('STAGE', no_upload=True, complete=False)
        code, _, fall_back = self.run_ahead(args, ('ERROR', "!! step 'render' failed: boom", 'STAGE'), claimed=None)
        self.assertEqual(code, 0)
        fall_back.assert_not_called()  # no paid cloud run before the slot
        self.assertEqual(self.ahead(args)['state'], 'gave_up')
        self.assertFalse(json.loads(path.read_text())['no_upload'])  # the slot run resumes and uploads it

    def test_failure_after_the_claim_falls_back_like_any_run(self):
        args = self.args()
        self.stage('STAGE', no_upload=True, complete=False)
        code, _, fall_back = self.run_ahead(args, ('ERROR', "!! step 'render' failed: boom", 'STAGE'), claimed=True)
        self.assertEqual(code, 0)
        self.assertEqual(fall_back.call_args.args[2], 'slot-' + args.slot)
        self.assertEqual(self.ahead(args)['state'], 'failed')

    def test_slot_run_takes_over_an_unfinished_made_ahead_video(self):
        path = self.stage('STAGE', no_upload=True, complete=False)
        done = self.stage('DONE', no_upload=True, complete=True)
        (self.root / 'ahead').mkdir()
        (self.root / 'ahead' / 'S.json').write_text(json.dumps({'state': 'making', 'stage': 'STAGE'}))
        runner.own_ahead_stage('S')
        self.assertFalse(json.loads(path.read_text())['no_upload'])
        runner.hand_back('DONE')
        self.assertTrue(json.loads(done.read_text())['no_upload'])  # a finished preview is never reopened


class ClaimWatcher(Folder):
    def test_claims_when_the_hour_starts_and_can_be_cancelled_before(self):
        soon = SimpleNamespace(format='short', slot=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H'))
        with mock.patch.object(runner, 'claim', return_value=(True, 'slot-x')) as claim:
            watcher = runner.SlotClaim(soon, {}, self.root, margin=0)
            watcher.start()
            watcher.join(5)
        self.assertEqual((watcher.ok, watcher.key), (True, 'slot-x'))
        self.assertEqual(claim.call_args.kwargs['cwd'], self.root)  # never in the checkout making the video
        later = SimpleNamespace(format='short', slot=(datetime.now(timezone.utc) + timedelta(hours=2)).strftime('%Y-%m-%dT%H'))
        with mock.patch.object(runner, 'claim') as claim:
            watcher = runner.SlotClaim(later, {}, self.root)
            watcher.start()
            watcher.stop()
        claim.assert_not_called()
        self.assertIsNone(watcher.ok)


class Lock(Folder):
    def test_made_ahead_run_waits_for_the_previous_upload(self):
        lock = self.root / 'lock'
        with mock.patch.object(runner, 'LOCK', lock):
            fd = runner.take_lock()
            self.assertIsNotNone(fd)
            self.assertIsNone(runner.take_lock())  # the usual run gives up at once
            calls = []

            def released(seconds):
                calls.append(seconds)
                os.close(fd)
                lock.unlink()
            with mock.patch.object(runner.time, 'sleep', side_effect=released):
                second = runner.take_lock(time.time() + 3600)
            self.assertIsNotNone(second)
            self.assertEqual(calls, [30])
            os.close(second)


class RenderWorkers(unittest.TestCase):
    def test_laptop_setting_reaches_the_renderer_and_cloud_stays_at_one(self):
        from src import claude_motion
        clean = {k: v for k, v in os.environ.items() if k != 'HISTOLD_RENDER_WORKERS'}
        with mock.patch.dict(os.environ, clean, clear=True):
            self.assertEqual(claude_motion.render_workers(), 1)
            with mock.patch.object(runner, 'run', return_value=proc('tok')), \
                    mock.patch.object(runner, 'secret_text', return_value='x'):
                env, _ = runner.tool_env(Config({'local_production': {'render_workers': 3}}))
            self.assertEqual(env['HISTOLD_RENDER_WORKERS'], '3')
            with mock.patch.dict(os.environ, {'HISTOLD_RENDER_WORKERS': '3'}):
                self.assertEqual(claude_motion.render_workers(), 3)
            with mock.patch.dict(os.environ, {'HISTOLD_RENDER_WORKERS': 'x'}):
                self.assertEqual(claude_motion.render_workers(), 1)


if __name__ == '__main__':
    unittest.main()


class ParallelRender(unittest.TestCase):
    HTML = ('<!doctype html><html><head><style>body{margin:0;background:#123}'
            '#b{width:80px;height:80px;background:#fc3;animation:m 1s linear both}'
            '@keyframes m{from{transform:translateX(0)}to{transform:translateX(300px)}}</style></head>'
            '<body><div id="b"></div></body></html>')

    def setUp(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest('playwright not installed')
        import shutil
        if not shutil.which('ffmpeg'):
            self.skipTest('ffmpeg not installed')

    def test_frames_match_a_one_at_a_time_render_and_failures_come_back(self):
        from playwright.sync_api import sync_playwright
        from src import claude_motion as cm

        def frames(path):
            return subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'framemd5', '-'],
                                  capture_output=True, text=True).stdout.splitlines()[-30:]
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={'width': 270, 'height': 480}, device_scale_factor=2)
                alone = cm._render_scene(page, self.HTML, 1.0, 30, folder / 'alone.mp4', 'ultrafast')
                browser.close()
            jobs = [(i, self.HTML, 1.0, folder / f'p{i}.mp4') for i in range(3)]
            together = cm._render_parallel(jobs, 30, 'ultrafast', (270, 480), 2)
            self.assertEqual(sorted(together), [0, 1, 2])
            for i in range(3):
                self.assertEqual(frames(together[i]), frames(alone))
            broken = cm._render_parallel([(0, '<html><body>no animation</body></html>', 1.0, folder / 'b.mp4'),
                                          (1, self.HTML, 1.0, folder / 'ok.mp4')], 30, 'ultrafast', (270, 480), 2)
            self.assertIsInstance(broken[0], Exception)
            self.assertIsInstance(broken[1], Path)
