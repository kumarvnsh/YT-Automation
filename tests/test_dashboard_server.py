"""Verify authentication and chat/refresh boundaries without platform writes."""
import importlib.util
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from src.config import Config

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/dashboard_server.py'
spec = importlib.util.spec_from_file_location('histold_dashboard_test', SCRIPT)
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)


class DashboardTests(unittest.TestCase):
    def test_repeated_live_refresh_does_not_compound_boost(self):
        gh = Mock()
        gh.read_json.side_effect = lambda path: (None, None) if path.endswith('growth_plan.json') else (
            {'region': {'boost': {'europe': 2}}}, 'sha')
        state = dashboard.State(gh)
        base = {'strategy': {'weights': {'region': {'europe': 0.5, 'americas': 0.5}}}}
        with patch.object(dashboard.build_dashboard, 'collect', return_value=base):
            state.refresh()
            first = state.get()['strategy']['weights']
            state.refresh(overlay_only=True)
            self.assertEqual(state.get()['strategy']['weights'], first)
            self.assertEqual(base['strategy']['weights']['region']['europe'], 0.5)

    def test_chat_discards_unrequested_boost_and_converts_ist_exactly(self):
        state = Mock()
        state.get.return_value = {'posts_per_day': 5, 'hours': [0, 12, 16, 18, 22]}
        cfg = Config({'growth': {'posts_per_day': 5, 'allowed_hours_utc': list(range(24)), 'min_gap_hours': 2},
                      'editorial': {'series': [{'id': 'unsolved'}, {'id': 'almost'}],
                                    'regions': [{'id': 'europe'}]}})
        calls = [{'function': {'name': 'pause_option', 'arguments': {'dimension': 'series', 'option': 'unsolved'}}},
                 {'function': {'name': 'boost_option', 'arguments': {'dimension': 'region', 'option': 'europe', 'factor': 2}}}]
        with patch.object(dashboard, 'server_config', return_value=cfg), \
             patch.object(dashboard, 'ensure_server'), patch.object(dashboard, 'assistant_context', return_value='{}'), \
             patch.object(dashboard, 'ollama', return_value={'message': {'tool_calls': calls}}):
            result = dashboard.chat(state, [{'role': 'user', 'content': 'Pause unsolved please'}])
        self.assertEqual([p['name'] for p in result['proposals']], ['pause_option'])
        calls = [{'function': {'name': 'pin_hours', 'arguments': {'hours': [1, 2, 3, 4, 5]}}}]
        with patch.object(dashboard, 'server_config', return_value=cfg), \
             patch.object(dashboard, 'ensure_server'), patch.object(dashboard, 'assistant_context', return_value='{}'), \
             patch.object(dashboard, 'ollama', return_value={'message': {'tool_calls': calls}}):
            result = dashboard.chat(state, [{'role': 'user', 'content':
                'Pin 05:30, 17:30, 21:30, 23:30, 03:30 IST'}])
        self.assertEqual(result['proposals'][0]['args']['hours'], [0, 12, 16, 18, 22])
        self.assertNotIn('error', result['proposals'][0])

    def test_compound_reanalysis_then_schedule_is_read_only_until_confirmed(self):
        cfg = Config({'growth': {'posts_per_day': 3, 'default_hours_utc': [12,18,23],
                                'allowed_hours_utc': [0,1,2,3,4]+list(range(11,24)), 'min_gap_hours': 2}})
        data = {'hours': [12,18,23], 'posts_per_day': 3, 'hour_stats': {'12': {'n':14,'shrunk':.46},
                '18': {'n':12,'shrunk':.67}}, 'content_evidence': {'mature_videos':112}}
        state = Mock()
        state.get.return_value = data
        state.analyze_now.return_value = data
        with patch.object(dashboard, 'server_config', return_value=cfg):
            result = dashboard.chat(state, [{'role':'user','content':'Analyse again and change the publish timings'}])
        state.analyze_now.assert_called_once()
        self.assertEqual(len(result['proposals'][0]['args']['hours']), 3)
        self.assertIn('Confirm', result['reply'])
        self.assertIn('experiment', result['reply'])
        state.github.update_json.assert_not_called()

    def test_failed_analysis_cannot_produce_a_schedule_from_old_data(self):
        state = Mock()
        state.analyze_now.side_effect = RuntimeError('model unavailable')
        with patch.object(dashboard, 'server_config', return_value=Config({})):
            result = dashboard.chat(state, [{'role':'user','content':'Analyze again and change the publish timings'}])
        self.assertEqual(result['proposals'], [])
        self.assertIn('failed', result['reply'])

    def test_followup_yes_reuses_validated_card_and_never_executes(self):
        cfg = Config({'growth': {'posts_per_day':3,'allowed_hours_utc':list(range(24)),'min_gap_hours':2}})
        state = Mock()
        card = {'name':'pin_hours','args':{'hours':[12,18,23],'days':14}}
        with patch.object(dashboard, 'server_config', return_value=cfg):
            result = dashboard.chat(state, [{'role':'user','content':'Suggest posting times'},
                {'role':'assistant','content':'Review this proposal','proposals':[card]},
                {'role':'user','content':'Yes, do that'}])
        self.assertEqual(result['proposals'][0]['args']['hours'],[12,18,23])
        state.github.update_json.assert_not_called()

    def test_api_needs_cookie_and_custom_header_and_rechecks_actions(self):
        state = dashboard.State(Mock())
        server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.make_handler(state, 'test-key', {}))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}/api/action'
        try:
            for headers, status, payload in [({}, 401, {}),
                    ({'Cookie': 'hk=test-key'}, 401, {}),
                    ({'Cookie': 'hk=test-key', 'X-Histold': '1'}, 400, {'name': 'publish', 'args': {}})]:
                req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(req)
                self.assertEqual(caught.exception.code, status)
            state.github.run_workflow.assert_not_called()
            state.github.update_json.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_shotsnip_api_is_isolated_and_rejects_histold_mutations(self):
        state = Mock()
        state.get.return_value = {'channel':'histold','videos':[]}
        with patch.object(dashboard.clipping_dashboard,'collect',return_value={'channel':'shotsnip','videos':[]}):
            server=ThreadingHTTPServer(('127.0.0.1',0),dashboard.make_handler(state,'test-key',{}))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                for path,label in [('/api/data?channel=shotsnip','shotsnip'),('/api/data','histold')]:
                    req=urllib.request.Request(base+path,headers={'Cookie':'hk=test-key'})
                    self.assertEqual(json.load(opener.open(req))['channel'],label)
                req=urllib.request.Request(base+'/api/action',data=json.dumps({'channel':'shotsnip','name':'pause_option'}).encode(),
                    headers={'Cookie':'hk=test-key','X-Histold':'1'})
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    opener.open(req)
                self.assertEqual(caught.exception.code,400)
                state.github.run_workflow.assert_not_called()
                state.github.update_json.assert_not_called()
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':
    unittest.main()
