import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import antigravity_ai as ag
from src import plan_ai
from src.config import Config

MODELS = {'clientModelConfigs': [
    {'modelId': 'claude-sonnet-5-5-high', 'modelOrAlias': {'model': 'MODEL_PLACEHOLDER_M405'},
     'quotaInfo': {'remainingFraction': 0.8, 'resetTime': 'x'}},
    {'modelId': 'gemini-4-neon-high', 'modelOrAlias': {'model': 'MODEL_PLACEHOLDER_M532'},
     'quotaInfo': {'remainingFraction': 0.0, 'resetTime': 'later'}},
    {'modelId': 'gemini-4-carbon', 'modelOrAlias': {'model': 'M670'}, 'disabled': True, 'quotaInfo': {}}]}


class Antigravity(unittest.TestCase):
    def setUp(self):
        ag._CACHE.update(at=0, models=None)
        self.calls = []

        def fake(method, body, timeout=20):
            self.calls.append((method, body))
            return MODELS if method == 'GetCascadeModelConfigData' else {'response': ' hello '}
        patcher = mock.patch.object(ag, '_call', side_effect=fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_text_is_a_plain_call_with_the_models_internal_code(self):
        self.assertEqual(ag.text('hi', 'claude-sonnet-5-5-high'), 'hello')
        self.assertEqual(self.calls[-1], ('GetModelResponse', {'prompt': 'hi', 'model': 'MODEL_PLACEHOLDER_M405'}))

    def test_quota_and_plan_limits_are_respected(self):
        self.assertTrue(ag.ready('claude-sonnet-5-5-high'))
        self.assertFalse(ag.ready('gemini-4-neon-high'))   # used up
        self.assertFalse(ag.ready('gemini-4-carbon'))      # not on this plan
        with self.assertRaises(ag.Unavailable):
            ag.text('hi', 'gemini-4-neon-high')

    def test_plan_router_uses_antigravity_after_claude_and_codex_but_never_for_research(self):
        cfg = Config({'local_ai': {'order': ['claude', 'codex', 'antigravity', 'qwen']}})
        self.assertIn('antigravity', plan_ai.order_for(cfg, 'scenes', web=False))
        self.assertNotIn('antigravity', plan_ai.order_for(cfg, 'research', web=True))
        text, usage, model = plan_ai._antigravity(Config({'local_ai': {'antigravity_models': ['gemini-4-neon-high', 'claude-sonnet-5-5-high']}}), 'p', 60)
        self.assertEqual((text, model), ('hello', 'claude-sonnet-5-5-high'))  # Gemini out of quota: next model


class ImageOrder(unittest.TestCase):
    def setUp(self):  # never read the laptop's real plan state (Gemini may really be resting)
        state = {'usage': {}, 'cooldown': {}, 'last_error': {}}
        for name, value in (('_load', state), ('_save', None)):
            patcher = mock.patch.object(plan_ai, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_gemini_first_then_codex_backup(self):
        cfg = Config({'local_ai': {'image_order': ['antigravity', 'codex']}})
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'a.png'
            with mock.patch.object(ag, 'image', side_effect=lambda *a, **k: out) as drawn, \
                    mock.patch.object(plan_ai, 'codex_image') as codex:
                self.assertEqual(plan_ai.image(cfg, 'p', out), out)
                codex.assert_not_called()
                drawn.assert_called_once()
            with mock.patch.object(ag, 'image', side_effect=RuntimeError('no draw')), \
                    mock.patch.object(plan_ai, 'codex_image', return_value=out) as codex:
                self.assertEqual(plan_ai.image(cfg, 'p', out), out)
                codex.assert_called_once()

    def test_all_providers_out_of_quota_means_wait_for_plans(self):
        cfg = Config({'local_ai': {'image_order': ['antigravity', 'codex']}})
        with mock.patch.object(ag, 'image', side_effect=ag.Unavailable('closed')), \
                mock.patch.object(plan_ai, 'codex_image', side_effect=plan_ai.PlanUnavailable('capped')):
            with self.assertRaises(plan_ai.PlanUnavailable):
                plan_ai.image(cfg, 'p', Path('x.png'))


class Drawing(unittest.TestCase):
    def run_image(self, rows, image=True):
        with tempfile.TemporaryDirectory() as home:
            home = Path(home)
            cid, helper = 'a' * 8 + '-0000-0000-0000-' + 'b' * 12, 'c' * 8 + '-0000-0000-0000-' + 'd' * 12
            logs = home / 'brain' / cid / '.system_generated' / 'logs'
            logs.mkdir(parents=True)
            rows = [dict(r, content=r['content'].replace('HELPER', helper)) for r in rows]
            (logs / 'transcript_full.jsonl').write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
            if image:
                from PIL import Image
                Image.new('RGB', (8, 12), 'red').save(home / 'brain' / cid / 'art.jpg')
            forgotten = []
            with mock.patch.object(ag, 'AG', home), mock.patch.object(ag, 'ready', return_value=True), \
                    mock.patch.object(ag, '_agentapi', return_value={'response': {'newConversation': {'conversationId': cid}}}), \
                    mock.patch.object(ag, 'forget', side_effect=forgotten.append), mock.patch('time.sleep'), \
                    mock.patch.object(ag.time, 'time', side_effect=__import__('itertools').count(int(__import__('time').time()), 2)):
                try:
                    return ag.image('p', home / 'out.png', timeout=5), sorted(forgotten), cid, helper
                except Exception as exc:  # noqa: BLE001
                    return exc, sorted(forgotten), cid, helper

    def test_image_is_taken_and_both_conversations_are_removed(self):
        rows = [{'type': 'GENERIC', 'content': 'Created the following subagent:\n{\n  "conversationId": "HELPER",'}]
        result, forgotten, cid, helper = self.run_image(rows)
        self.assertTrue(str(result).endswith('out.png'))
        self.assertEqual(forgotten, sorted([cid, helper]))

    def test_quota_error_reports_its_reset_time(self):
        rows = [{'type': 'SYSTEM_MESSAGE', 'content': '429 RESOURCE_EXHAUSTED ... Quota resets at `2026-11-23T09:39:26Z`'}]
        result, forgotten, cid, _ = self.run_image(rows, image=False)
        self.assertIsInstance(result, ag.Unavailable)
        self.assertEqual(result.reset, 1795426766.0)
        self.assertIn(cid, forgotten)

    def test_used_up_image_quota_rests_instead_of_opening_conversations(self):
        cfg = Config({'local_ai': {'image_order': ['antigravity']}})
        state = {'usage': {}, 'cooldown': {}, 'last_error': {}}
        with mock.patch.object(plan_ai, '_load', return_value=state), mock.patch.object(plan_ai, '_save'), \
                mock.patch.object(ag, 'image', side_effect=ag.Unavailable('Antigravity image quota used up', 4102444800.0)) as drawn:
            with self.assertRaises(plan_ai.PlanUnavailable):
                plan_ai.image(cfg, 'p', Path('x.png'))
            self.assertEqual(state['cooldown']['antigravity_image'], 4102444800.0)
            with self.assertRaises(plan_ai.PlanUnavailable):
                plan_ai.image(cfg, 'p', Path('x.png'))
            self.assertEqual(drawn.call_count, 1)  # second call never opened a conversation
