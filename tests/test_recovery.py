import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from src.recovery import latest_exports, recover, service_watchdog, ANALYTICS


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 17, 15, tzinfo=timezone.utc)
        self.alerts = [{'id': 'stale:YouTube export'}]

    def test_latest_exports_use_github_not_stale_clone_and_cache_requests(self):
        github = Mock()
        github.read_json.side_effect = [([{'video_id': 'new'}], 'a'), ({'updated': 'fresh'}, 'b'), ({'facebook': {}}, 'c')]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'cache.json'
            exports, source = latest_exports(github, path, self.now)
            self.assertEqual(exports['analytics']['updated'], 'fresh')
            self.assertTrue(source.startswith('verified'))
            latest_exports(github, path, self.now + timedelta(minutes=1))
            self.assertEqual(github.read_json.call_count, 3)

    def test_network_failure_never_claims_cached_exports_are_current(self):
        github = Mock()
        github.read_json.side_effect = RuntimeError('offline')
        with tempfile.TemporaryDirectory() as tmp:
            _, source = latest_exports(github, Path(tmp)/'cache.json', self.now)
            self.assertIn('unavailable', source)

    def test_stale_exports_start_one_refresh_and_cool_down(self):
        github = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'state.json'
            result = recover(self.alerts, [], github, path, self.now, verified=True)
            self.assertEqual(result[0]['status'], 'started')
            again = recover(self.alerts, [], github, path, self.now + timedelta(minutes=1), verified=True)
            self.assertEqual(again[0]['status'], 'cooldown')
            github.run_workflow.assert_called_once_with('analytics.yml')

    def test_inflight_export_or_unverified_data_never_starts_another(self):
        github = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'state.json'
            result = recover(self.alerts, [{'workflowName': ANALYTICS, 'status': 'in_progress'}], github, path, self.now, verified=True)
            self.assertEqual(result[0]['status'], 'in_progress')
            result = recover(self.alerts, [], github, path, self.now, verified=False)
            self.assertEqual(result[0]['status'], 'waiting_for_github')
            github.run_workflow.assert_not_called()

    def test_recovery_cannot_trigger_automatic_republishing(self):
        github = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            result = recover(self.alerts, [], github, Path(tmp)/'state.json', self.now, verified=True, auto_republish=True)
            self.assertEqual(result[0]['status'], 'needs_review')
            github.run_workflow.assert_not_called()

    def test_unknown_dispatch_outcome_is_persisted_and_not_blindly_retried(self):
        github = Mock()
        github.run_workflow.side_effect = TimeoutError()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'state.json'
            result = recover(self.alerts, [], github, path, self.now, verified=True)
            self.assertEqual(result[0]['status'], 'failed_or_unknown')
            recover(self.alerts, [], github, path, self.now, verified=True)
            self.assertEqual(github.run_workflow.call_count, 1)

    def test_recovery_is_limited_to_two_exports_per_hour(self):
        github = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'state.json'
            for minutes in (0, 20, 40):
                recover(self.alerts, [], github, path, self.now + timedelta(minutes=minutes), verified=True)
            self.assertEqual(github.run_workflow.call_count, 2)

    @patch('requests.get')
    @patch('src.recovery.subprocess.run')
    def test_failed_health_check_restarts_only_known_services_and_waits_for_verification(self, run, get):
        get.return_value.status_code = 503
        run.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actions, alerts = service_watchdog(root, root/'state.json', self.now)
            self.assertEqual(len(alerts), 2)
            self.assertTrue(all(a['status'] == 'restart_requested' for a in actions))
            self.assertEqual({Path(c.args[0][-1]).name for c in run.call_args_list}, {'start_dashboard.ps1','start_openwa.ps1'})
            again, _ = service_watchdog(root, root/'state.json', self.now + timedelta(minutes=1))
            self.assertTrue(all(a['status'] == 'cooldown' for a in again))
            self.assertEqual(run.call_count, 2)

    @patch('requests.get')
    @patch('src.recovery.subprocess.run', create=True)
    def test_healthy_services_do_not_restart(self, run, get):
        get.return_value.status_code = 200
        with tempfile.TemporaryDirectory() as tmp:
            actions, alerts = service_watchdog(Path(tmp), Path(tmp)/'state.json', self.now)
            self.assertFalse(actions)
            self.assertFalse(alerts)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()


class ShotsnipKeepaliveTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'clip'
        (self.root / 'work' / 'dashboard').mkdir(parents=True)
        (self.root / 'watch_service.py').write_text('', encoding='utf-8')
        self.state = Path(tmp.name) / 'state.json'
        self.now = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)

    def run_tick(self, now=None):
        from unittest.mock import MagicMock, patch
        from src.recovery import shotsnip_keepalive
        with patch('src.recovery.subprocess.run', return_value=MagicMock(returncode=0)) as run:
            return shotsnip_keepalive(Path('repo'), self.root, self.state, now or self.now), run

    def test_restarts_dead_worker_with_cooldown(self):
        actions, run = self.run_tick()
        self.assertEqual(actions[0]['status'], 'restart_requested')
        self.assertIn('start_shotsnip_worker.ps1', run.call_args.args[0][-1])
        self.assertEqual(self.run_tick(self.now + timedelta(minutes=5))[0], [])
        self.assertEqual(len(self.run_tick(self.now + timedelta(minutes=11))[0]), 1)

    def test_respects_owner_stop(self):
        (self.root / 'work' / 'dashboard' / 'stop-worker').write_text('stop', encoding='utf-8')
        actions, run = self.run_tick()
        self.assertEqual(actions, [])
        run.assert_not_called()

    def test_running_worker_is_left_alone(self):
        from unittest.mock import patch
        with patch('src.recovery._lock_held', return_value=True):
            actions, run = self.run_tick()
        self.assertEqual(actions, [])
        run.assert_not_called()
