import json
import os
import time
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts import dispatch
from scripts import run_local_publish as runner
from src.config import Config


def proc(stdout='', returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr='')


class Dispatch(unittest.TestCase):
    def setUp(self):
        switch = mock.patch.object(dispatch, 'SWITCH_FILE', Path(tempfile.gettempdir()) / 'histold-no-switch.json')
        switch.start()
        self.addCleanup(switch.stop)

    def test_studio_hq_switch_overrides_config(self):
        with tempfile.TemporaryDirectory() as raw:
            switch = Path(raw) / 'switch.json'
            switch.write_text(json.dumps({'enabled': True}))
            with mock.patch.object(dispatch, 'SWITCH_FILE', switch):
                self.assertTrue(dispatch.local_enabled({'enabled': False}))
                switch.write_text(json.dumps({'enabled': False}))
                self.assertFalse(dispatch.local_enabled({'enabled': True}))
        self.assertTrue(dispatch.local_enabled({'enabled': True}))

    def test_cloud_when_local_production_is_off(self):
        with mock.patch.object(dispatch, 'load_config', return_value=Config({'local_production': {'enabled': False}})):
            self.assertFalse(dispatch.local_publish('short', '2026-11-22T11', False))

    def test_local_runner_started_with_the_slot(self):
        with tempfile.NamedTemporaryFile(suffix='.exe', delete=False) as python:
            cfg = Config({'local_production': {'enabled': True, 'python': python.name}})
        with mock.patch.object(dispatch, 'load_config', return_value=cfg), mock.patch.object(dispatch.subprocess, 'Popen') as popen:
            self.assertTrue(dispatch.local_publish('short', '2026-11-22T11', False))
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[0], python.name)
        self.assertEqual(cmd[-4:], ['--format', 'short', '--slot', '2026-11-22T11'])
        Path(python.name).unlink()

    def test_missing_local_python_uses_the_cloud(self):
        cfg = Config({'local_production': {'enabled': True, 'python': r'D:\nowhere\python.exe'}})
        with mock.patch.object(dispatch, 'load_config', return_value=cfg):
            self.assertFalse(dispatch.local_publish('short', '2026-11-22T11', False))


class Runner(unittest.TestCase):
    def args(self, **extra):
        return SimpleNamespace(format='short', slot='2026-11-22T11', dry_run=False, no_upload=False,
                               no_fallback=False, stamp='T', **extra)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.logs = mock.patch.object(runner, 'LOG_DIR', Path(self.folder.name))
        self.logs.start()
        self.notify = mock.patch.object(runner, 'notify')
        self.sent = self.notify.start()

    def tearDown(self):
        self.notify.stop()
        self.logs.stop()
        self.folder.cleanup()

    def test_fallback_releases_the_claim_then_starts_the_paid_cloud_run(self):
        calls = []
        with mock.patch.object(runner, 'run', side_effect=lambda cmd, **kw: calls.append(cmd) or proc()), \
                mock.patch.object(runner, 'newest_stage', return_value=None):
            self.assertEqual(runner.fall_back(self.args(), 'plans capped', 'slot-2026-11-22T11', {}), 0)
        self.assertEqual(calls[0][1:4], ['scripts/claim_histold_publish.py', '--release', 'slot-2026-11-22T11'])
        self.assertEqual(calls[1][1:3], ['workflow', 'run'])
        self.assertIn('format=short', calls[1])
        self.assertNotIn('scheduled=true', ' '.join(calls[1]))  # a late fallback must not hit the slot-hour gate
        self.assertIn('paid cloud run', self.sent.call_args.args[0])

    def test_previews_never_fall_back(self):
        with mock.patch.object(runner, 'run', return_value=proc()) as run, mock.patch.object(runner, 'newest_stage', return_value=None):
            preview = SimpleNamespace(**{**vars(self.args()), 'no_upload': True})
            self.assertEqual(runner.fall_back(preview, 'x'), 1)
        run.assert_not_called()

    def test_pre_upload_failure_is_retried_but_upload_is_not(self):
        outputs = iter([proc("  !! step 'render' failed: boom\nSTATUS: ERROR boom", 1), proc('STATUS: DONE  url')])
        with mock.patch.object(runner, 'stream', side_effect=lambda *a, **k: next(outputs)), mock.patch.object(runner.time, 'sleep'):
            self.assertEqual(runner.step_loop(self.args(), {})[0], 'DONE')
        outputs = iter([proc("  !! step 'upload' failed: boom\nSTATUS: ERROR boom", 1)])
        with mock.patch.object(runner, 'stream', side_effect=lambda *a, **k: next(outputs)), mock.patch.object(runner.time, 'sleep'):
            self.assertEqual(runner.step_loop(self.args(), {})[0], 'ERROR')

    def test_exhausted_plans_are_not_retried_locally(self):
        outputs = iter([proc("  !! step 'script' failed: No plan provider could do script\nSTATUS: ERROR x", 1)])
        with mock.patch.object(runner, 'stream', side_effect=lambda *a, **k: next(outputs)), mock.patch.object(runner.time, 'sleep') as sleep:
            self.assertEqual(runner.step_loop(self.args(), {})[0], 'ERROR')
        sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()


class WaitForReset(unittest.TestCase):
    """Owner's rule (22 Nov): when Claude and Codex are out of limits, wait for the reset, never paid credits."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.patches = [mock.patch.object(runner, 'LOG_DIR', root), mock.patch.object(runner, 'RETRY', root / 'retry.json'),
                        mock.patch.object(dispatch, 'RETRY_FILE', root / 'retry.json'),
                        mock.patch.object(dispatch, 'SWITCH_FILE', root / 'no-switch.json'), mock.patch.object(runner, 'notify')]
        self.sent = [p.start() for p in self.patches][-1]

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.folder.cleanup()

    def test_used_up_plans_release_the_slot_and_wait_without_paid_credits(self):
        args = SimpleNamespace(format='short', slot='2026-11-22T11', dry_run=False, no_upload=False, no_fallback=False, stamp='T')
        calls = []
        with mock.patch.object(runner, 'run', side_effect=lambda cmd, **kw: calls.append(cmd) or proc()), \
                mock.patch.object(runner, 'newest_stage', return_value=None), \
                mock.patch('src.plan_ai.next_ready_at', return_value=1_900_000_000.0):
            self.assertEqual(runner.defer(args, 'plans capped', 'slot-2026-11-22T11', {}), 0)
        self.assertEqual(calls[0][1:3], ['scripts/claim_histold_publish.py', '--release'])
        self.assertFalse(any('workflow' in cmd for cmd in calls))  # no paid cloud run
        waiting = json.loads(runner.RETRY.read_text())
        self.assertEqual((waiting['format'], waiting['retry_at'], waiting['attempts']), ('short', 1_900_000_000.0, 1))
        self.assertIn('No paid credits', self.sent.call_args.args[0])

    def test_dispatcher_retries_only_after_the_reset(self):
        runner.RETRY.write_text(json.dumps({'format': 'short', 'retry_at': 2000}))
        with tempfile.NamedTemporaryFile(suffix='.exe', delete=False) as python:
            cfg = Config({'local_production': {'enabled': True, 'python': python.name}})
        with mock.patch.object(dispatch, 'load_config', return_value=cfg), mock.patch.object(dispatch.subprocess, 'Popen') as popen:
            self.assertFalse(dispatch.retry_waiting_video(False, now=1000))
            self.assertTrue(dispatch.retry_waiting_video(False, now=3000))
        self.assertIn('--retry', popen.call_args.args[0])
        Path(python.name).unlink()


class ResetTime(unittest.TestCase):
    def test_reset_is_the_earlier_of_claude_and_codex(self):
        from src import plan_ai
        now = 100_000.0
        state = {'usage': {'claude': [now - 100, now - 50]}, 'cooldown': {'codex': now + 900}, 'last_error': {}}
        cfg = Config({'local_ai': {'claude': {'per_5h': 2, 'per_week': 100}}})
        with mock.patch.object(plan_ai, '_load', return_value=state):
            self.assertEqual(plan_ai.next_ready_at(cfg, now), now + 900)          # Codex rests 15 min
            state['cooldown'] = {'codex': now + 99_999}
            self.assertEqual(plan_ai.next_ready_at(cfg, now), now - 100 + 5 * 3600)  # Claude's oldest call ages out


class StaleLocks(unittest.TestCase):
    def test_only_old_locks_on_unfinished_videos_are_cleared(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name, complete in (('old_unfinished', False), ('old_finished', True), ('fresh', False)):
                stage = root / 'output' / name
                (stage / '.studio-hq-media-lock').mkdir(parents=True)
                (stage / 'state.json').write_text(json.dumps({'complete': complete}))
            old = time.time() - 3600
            for name in ('old_unfinished', 'old_finished'):
                os.utime(root / 'output' / name / '.studio-hq-media-lock', (old, old))
            with mock.patch.object(runner, 'ROOT', root), mock.patch.object(runner, 'log'):
                runner.clear_stale_locks()
            self.assertFalse((root / 'output' / 'old_unfinished' / '.studio-hq-media-lock').exists())
            self.assertTrue((root / 'output' / 'old_finished' / '.studio-hq-media-lock').exists())
            self.assertTrue((root / 'output' / 'fresh' / '.studio-hq-media-lock').exists())
