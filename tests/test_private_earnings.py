import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.refresh_private_earnings import collect_meta, collect_youtube, graph_get
from src.config import Config


class PrivateEarningsTests(unittest.TestCase):
    def test_missing_credentials_do_not_start_auth_or_change_upload_token(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp) / 'repo'
            root.mkdir()
            upload = root / 'secrets/token.json'
            upload.parent.mkdir()
            upload.write_text('original')
            self.assertEqual(collect_meta(Config({}), root)['status'], 'credentials_missing')
            self.assertEqual(collect_youtube(Config({}), root)['status'], 'authorization_required')
            self.assertEqual(upload.read_text(), 'original')

    @patch('requests.get')
    def test_api_results_never_retain_token_in_errors_or_paging_urls(self, get):
        response = Mock(ok=False)
        response.json.return_value = {'error': {'code': 200, 'message': 'denied TOKEN'}}
        get.return_value = response
        result = graph_get('1/insights', 'TOKEN', 'v21.0')
        self.assertNotIn('TOKEN', str(result))
        response.ok = True
        response.json.return_value = {'data': [], 'paging': {'next': 'https://example.com/?access_token=TOKEN'}}
        self.assertNotIn('TOKEN', str(graph_get('1/insights', 'TOKEN', 'v21.0')))

    @patch('src.meta_uploader._page_access_token', return_value='page-token')
    @patch('scripts.refresh_private_earnings.graph_get')
    def test_post_earnings_use_confirmed_attachment_mapping(self, get, _page):
        def response(node, *args, **kwargs):
            if node.endswith('/published_posts'):
                return {'status': 'ok', 'data': [{'id': 'PAGE_POST', 'attachments': {'data': [{'target': {'id': 'VIDEO'}}]}}]}
            return {'status': 'ok', 'data': [{'values': [{'value': 0.4}]}]}
        get.side_effect = response
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'META_ACCESS_TOKEN': 't' * 60}, clear=True):
            root = Path(tmp)
            (root / 'data').mkdir()
            (root / 'data/published_index.json').write_text(json.dumps([{'facebook_id': 'VIDEO', 'published_at': '2099-01-01T00:00:00+00:00'}]))
            result = collect_meta(Config({'meta': {'page_id': 'PAGE'}}), root)
        checks = [c for c in result['report']['checks'] if c['scope'] == 'post']
        self.assertEqual(len(checks), 3)
        self.assertTrue(all(c['post_id'] == 'PAGE_POST' for c in checks))
        self.assertFalse(any(call.args[0] == 'VIDEO/insights' for call in get.call_args_list))


if __name__ == '__main__':
    unittest.main()
