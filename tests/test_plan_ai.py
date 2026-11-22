import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from src import plan_ai
from src.config import Config

CFG = Config({'local_ai': {'order': ['claude', 'codex', 'qwen'], 'qwen_for': ['assess', 'thumbnail', 'title'],
                           'claude': {'per_5h': 2, 'per_week': 10}}})


def done(stdout='', returncode=0, stderr=''):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class PlanAI(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.env = mock.patch.dict(os.environ, {'HISTOLD_AI_BACKEND': 'plan', 'HISTOLD_PLAN_AI_STATE': str(root / 'state.json'),
                                                'HISTOLD_PLAN_AI_SANDBOX': str(root / 'sandbox')})
        self.env.start()
        self.which = mock.patch.multiple(plan_ai, find_claude=lambda: 'claude.cmd', find_codex=lambda: 'codex.exe',
                                         _live_settings=lambda: {})
        self.which.start()

    def tearDown(self):
        self.which.stop()
        self.env.stop()
        self.folder.cleanup()

    def test_only_the_local_runner_turns_it_on(self):
        self.assertTrue(plan_ai.enabled())
        with mock.patch.dict(os.environ, {'HISTOLD_AI_BACKEND': ''}):
            self.assertFalse(plan_ai.enabled())

    def test_qwen_never_researches_or_writes_scene_code(self):
        self.assertEqual(plan_ai.order_for(CFG, 'research', True), ['claude', 'codex'])
        self.assertEqual(plan_ai.order_for(CFG, 'scenes', False), ['claude', 'codex'])
        self.assertEqual(plan_ai.order_for(CFG, 'assess', False), ['claude', 'codex', 'qwen'])

    def test_claude_answers_with_no_tools_and_usage_is_counted(self):
        reply = json.dumps({'result': '{"ok": true}', 'is_error': False, 'usage': {'input_tokens': 5, 'output_tokens': 7}})
        with mock.patch.object(plan_ai, '_run', return_value=done(reply)) as run:
            text, provider = plan_ai.complete(CFG, 'script', 'PROMPT', system='SYSTEM')
        self.assertEqual((text, provider), ('{"ok": true}', 'claude'))
        cmd, prompt = run.call_args.args[0], run.call_args.args[1]
        self.assertEqual(cmd[cmd.index('--tools') + 1], '')
        self.assertEqual(prompt, 'PROMPT')  # the job's rules replace Claude Code's own system prompt
        self.assertIn('--system-prompt-file', cmd)
        self.assertFalse(Path(cmd[cmd.index('--system-prompt-file') + 1]).exists())  # removed after the call
        self.assertEqual(plan_ai.status(CFG)['claude']['used_5h'], 1)

    def test_scene_jobs_use_their_own_claude_model(self):
        cfg = Config({'local_ai': {'claude_model': 'opus', 'claude_model_for': {'scenes': 'sonnet'}}})
        reply = json.dumps({'result': 'x', 'is_error': False})
        with mock.patch.object(plan_ai, '_run', return_value=done(reply)) as run:
            plan_ai.complete(cfg, 'scenes', 'P', json_reply=False)
            plan_ai.complete(cfg, 'script', 'P')
        models = [call.args[0][call.args[0].index('--model') + 1] for call in run.call_args_list]
        self.assertEqual(models, ['sonnet', 'opus'])

    def test_current_config_beats_the_videos_snapshot(self):
        snapshot = Config({'local_ai': {'claude_model': 'old'}})
        with mock.patch.object(plan_ai, '_live_settings', return_value={'claude_model_for': {'scenes': 'sonnet'}}):
            live = plan_ai._with_live(snapshot)
        self.assertEqual(live.get('local_ai.claude_model_for'), {'scenes': 'sonnet'})
        self.assertIsNone(live.get('local_ai.claude_model'))

    def test_research_may_only_search_and_fetch(self):
        reply = json.dumps({'result': '{}', 'is_error': False})
        with mock.patch.object(plan_ai, '_run', return_value=done(reply)) as run:
            plan_ai.complete(CFG, 'research', 'P', web=True)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index('--tools') + 1], 'WebSearch,WebFetch')
        self.assertEqual(cmd[cmd.index('--allowedTools') + 1], 'WebSearch,WebFetch')

    def test_plan_limit_rests_claude_and_codex_takes_over(self):
        limit = json.dumps({'result': 'Claude usage limit reached. Your limit resets 5pm', 'is_error': True})

        def run(cmd, prompt, timeout, env=None):
            if cmd[0] == 'claude.cmd':
                return done(limit, 1)
            Path(cmd[cmd.index('-o') + 1]).write_text('{"from": "codex"}', encoding='utf-8')
            return done('')

        with mock.patch.object(plan_ai, '_run', side_effect=run):
            text, provider = plan_ai.complete(CFG, 'script', 'P')
        self.assertEqual((text, provider), ('{"from": "codex"}', 'codex'))
        self.assertIsNotNone(plan_ai.status(CFG)['claude']['resting_until'])

    def test_caps_skip_a_provider_and_research_never_reaches_qwen(self):
        state = {'usage': {'claude': [time.time(), time.time()]}, 'cooldown': {'codex': time.time() + 600}, 'last_error': {}}
        Path(os.environ['HISTOLD_PLAN_AI_STATE']).write_text(json.dumps(state), encoding='utf-8')
        with mock.patch.object(plan_ai, '_run') as run, mock.patch.object(plan_ai, '_qwen') as qwen:
            with self.assertRaises(plan_ai.PlanUnavailable):
                plan_ai.complete(CFG, 'research', 'P', web=True)
            run.assert_not_called()
            qwen.assert_not_called()

    def test_qwen_is_the_last_resort_for_small_jobs(self):
        state = {'usage': {'claude': [time.time(), time.time()]}, 'cooldown': {'codex': time.time() + 600}, 'last_error': {}}
        Path(os.environ['HISTOLD_PLAN_AI_STATE']).write_text(json.dumps(state), encoding='utf-8')
        with mock.patch.object(plan_ai, '_qwen', return_value=('BIG | CLAIM!', {}, 'qwen3:8b')):
            self.assertEqual(plan_ai.complete(CFG, 'thumbnail', 'P', json_reply=False), ('BIG | CLAIM!', 'qwen'))


class Routing(unittest.TestCase):
    def test_script_uses_only_plans_when_enabled_and_api_otherwise(self):
        from src.script_generator import _provider_sequence
        cfg = Config({'script': {'routing': 'fallback', 'provider': 'anthropic', 'fallback_provider': 'anthropic_fallback'}})
        with mock.patch.dict(os.environ, {'HISTOLD_AI_BACKEND': 'plan'}):
            self.assertEqual(_provider_sequence(cfg), ['plan'])
        with mock.patch.dict(os.environ, {'HISTOLD_AI_BACKEND': ''}):
            self.assertEqual(_provider_sequence(cfg), ['anthropic', 'anthropic_fallback'])


if __name__ == '__main__':
    unittest.main()
