import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from studio_hq import histold_live as live

CONFIG_YAML = """growth:
  daily_publish_cap: 3

local_production:
  enabled: false
  python: 'PYTHON_PATH'

local_ai:
  order: [claude, codex, qwen]
  claude: {per_5h: 2, per_week: 9}
  codex: {per_5h: 60, per_week: 600}

notifications:
  enabled: false
"""


class HistoldLive(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.home = Path(self.folder.name)
        self.clone = self.home / 'YT-Automation'
        (self.clone / 'output').mkdir(parents=True)
        (self.home / 'control').mkdir()
        self.python = self.home / 'python.exe'
        self.python.write_text('')
        (self.clone / 'config.yaml').write_text(CONFIG_YAML.replace('PYTHON_PATH', str(self.python)), encoding='utf-8')
        self.config = {'dashboard_histold_index_root': str(self.clone), 'roots': {'histold': str(self.clone)}}
        self.ollama = mock.patch('urllib.request.urlopen', side_effect=OSError('off'))
        self.ollama.start()

    def tearDown(self):
        self.ollama.stop()
        self.folder.cleanup()

    def test_reads_config_without_yaml(self):
        settings = live.configured(self.config)
        self.assertFalse(settings['enabled'])
        self.assertEqual(settings['caps']['claude'], {'per_5h': 2, 'per_week': 9})
        self.assertEqual(Path(settings['python']), self.python)

    def test_studio_hq_switch_wins_over_config(self):
        self.assertEqual(live.mode(self.config)['local'], False)
        live.perform(self.config, {'action': 'histold_local_mode', 'local': True})
        state = live.mode(self.config)
        self.assertEqual((state['local'], state['source']), (True, 'studio_hq'))

    def test_plan_use_and_when_a_capped_share_frees_up(self):
        now = time.time()
        (self.home / 'control' / 'plan-ai.json').write_text(json.dumps(
            {'usage': {'claude': [now - 600, now - 60]}, 'cooldown': {'codex': now + 900}, 'last_error': {}}))
        plans = live.plans(self.config, now)
        self.assertEqual((plans['claude']['used_5h'], plans['claude']['ready']), (2, False))
        self.assertIsNotNone(plans['claude']['frees_at'])
        self.assertIsNotNone(plans['codex']['resting_until'])
        self.assertEqual(plans['qwen'], {'ready': False, 'running': False})

    def test_current_run_reports_stage_progress(self):
        stage = self.clone / 'output' / '20261122_105811_039795_short'
        stage.mkdir()
        (stage / 'state.json').write_text(json.dumps({'fmt': 'short', 'done': ['research', 'script'],
            'target_steps': ['research', 'script', 'voiceover', 'upload'], 'script': {'title': 'A Title'}}))
        (self.home / 'control' / 'local-publish.lock').write_text('')
        run = live.current_run(self.config, time.time())
        self.assertEqual((run['running'], run['current'], run['title'], run['format']), (True, 'voiceover', 'A Title', 'short'))
        self.assertTrue(run['started'].startswith('2026-11-22T10:58:11'))

    def test_events_are_newest_first_and_drop_links(self):
        log = self.home / 'control' / 'local-publish' / 'runner.log'
        log.parent.mkdir()
        log.write_text('2026-11-22 10:58:10 local run short slot - start\n'
                       '2026-11-22 11:03:22 cited page could not be fetched: https://example.org/x\n', encoding='utf-8')
        events = live.snapshot(self.config)['events']
        self.assertEqual(events[0]['text'], 'cited page could not be fetched:')
        self.assertEqual(events[1]['at'], '2026-11-22T10:58:10+05:30')

    def test_controls_validate_and_handle_the_waiting_video(self):
        for body in ({'action': 'histold_local_mode', 'local': 'yes'}, {'action': 'histold_local_run', 'format': 'reel'},
                     {'action': 'histold_local_retry', 'choice': 'later'}):
            with self.assertRaises(ValueError):
                live.check(body)
        with self.assertRaises(ValueError):
            live.perform(self.config, {'action': 'histold_local_retry', 'choice': 'now'})  # nothing waiting
        retry = self.home / 'control' / 'local-publish-retry.json'
        retry.write_text(json.dumps({'format': 'short', 'retry_at': time.time() + 3600}))
        self.assertEqual(live.snapshot(self.config)['waiting']['format'], 'short')
        live.perform(self.config, {'action': 'histold_local_retry', 'choice': 'cancel'})
        self.assertFalse(retry.exists())

    def test_make_now_needs_local_mode_and_no_run_in_progress(self):
        with self.assertRaisesRegex(ValueError, 'local production'):
            live.perform(self.config, {'action': 'histold_local_run', 'format': 'short'})
        live.perform(self.config, {'action': 'histold_local_mode', 'local': True})
        (self.home / 'control' / 'local-publish.lock').write_text('')
        with self.assertRaisesRegex(ValueError, 'already being made'):
            live.perform(self.config, {'action': 'histold_local_run', 'format': 'short'})
        (self.home / 'control' / 'local-publish.lock').unlink()
        with mock.patch.object(live.subprocess, 'Popen') as popen:
            live.perform(self.config, {'action': 'histold_local_run', 'format': 'short'})
        self.assertEqual(popen.call_args.args[0][-2:], ['--format', 'short'])

    def test_per_video_plan_usage_and_live_step(self):
        stage = self.clone / 'output' / '20261122_105811_039795_short'
        stage.mkdir()
        (stage / 'state.json').write_text(json.dumps({'fmt': 'short', 'done': ['research', 'script'],
            'target_steps': ['research', 'script', 'voiceover', 'render']}))
        events = self.clone / 'data' / 'private' / 'studio-hq' / 'events'
        events.mkdir(parents=True)
        for n, (step, out) in enumerate((('research', 100), ('render', 50), ('render', 25))):
            (events / f'{n}.json').write_text(json.dumps({'kind': 'api_usage', 'job_id': stage.name, 'stage': step,
                'provider': 'claude_plan', 'status': 'ok', 'elapsed_seconds': 60,
                'usage': {'input_tokens': 2, 'cache_creation_input_tokens': 8, 'cache_read_input_tokens': 5, 'output_tokens': out}}))
        (events / 'other.json').write_text(json.dumps({'kind': 'api_usage', 'job_id': 'another', 'stage': 'render'}))
        log = self.home / 'control' / 'local-publish' / 'run.log'
        log.parent.mkdir()
        log.write_text('  → step: render\n  (whatsapp notify skipped)\n  plan scenes: 2/3 requested scenes returned\n',
                       encoding='utf-8')
        (self.home / 'control' / 'local-publish-status.json').write_text(json.dumps({'root': str(self.clone), 'log': str(log)}))
        (self.home / 'control' / 'local-publish.lock').write_text('')
        run = live.current_run(self.config, time.time())
        self.assertEqual((run['current'], run['current_label']), ('render', 'Rendering scenes'))
        self.assertEqual(run['detail'], 'plan scenes: 2/3 requested scenes returned')
        usage = run['usage']
        self.assertEqual([row['step'] for row in usage['steps']], ['research', 'render'])
        self.assertEqual(usage['total'], {'calls': 3, 'failed': 0, 'input': 30, 'cached': 15, 'output': 175, 'seconds': 180.0})

    def test_progress_counts_scenes_and_weights_the_whole_video(self):
        fraction, measured = live.step_fraction('render', {'written': 5, 'rendered': 3, 'repaired': True}, 5, 0)
        self.assertTrue(measured)
        self.assertAlmostEqual(fraction, 0.6 + 0.1 + 0.18)
        self.assertEqual(live.step_fraction('script', {}, 5, 30), (0.5, False))
        self.assertEqual(live.step_fraction('script', {}, 5, 999)[0], 0.95)  # never claims done before it is
        stage = self.clone / 'output' / '20261122_105811_039795_short'
        stage.mkdir()
        (stage / 'state.json').write_text(json.dumps({'fmt': 'short', 'done': ['research', 'script'],
            'target_steps': ['research', 'script', 'render'], 'script': {'segments': [{}, {}, {}, {}]}}))
        log = self.home / 'control' / 'local-publish' / 'run.log'
        log.parent.mkdir()
        log.write_text('  → step: render\n  plan scenes: 2/2 requested scenes returned\n  plan scenes: 2/2 requested scenes returned\n',
                       encoding='utf-8')
        (self.home / 'control' / 'local-publish-status.json').write_text(json.dumps({'root': str(self.clone), 'log': str(log)}))
        (self.home / 'control' / 'local-publish.lock').write_text('')
        progress = live.current_run(self.config, time.time())['progress']
        self.assertEqual(progress['counts'], {'scenes': 4, 'written': 4, 'rendered': 0})
        self.assertEqual(progress['step'], 0.6)
        self.assertAlmostEqual(progress['overall'], (120 + 60 + 330 * 0.6) / (120 + 60 + 330), places=3)

    def test_setup_survives_a_tool_path_from_another_system(self):
        with mock.patch.object(live, 'configured', return_value={'python': 'python.exe', 'enabled': False, 'caps': {}}):
            self.assertEqual(len(live.setup(self.config)), 4)

    def test_a_live_failure_never_breaks_the_snapshot(self):
        from studio_hq import dashboard_snapshot
        with mock.patch.object(dashboard_snapshot, 'histold_live_snapshot', side_effect=RuntimeError('boom')):
            self.assertIsNone(dashboard_snapshot._histold_live({}))


if __name__ == '__main__':
    unittest.main()
