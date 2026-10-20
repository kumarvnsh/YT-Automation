"""Production recovery integration checks without network, Chromium, or FFmpeg."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from src.claude_motion import render_motion_video
from src.config import Config


class SceneRecoveryTests(unittest.TestCase):
    def test_missing_opening_recovers_with_verified_maps_without_stock(self):
        self._check_recovery({1: '<html><head></head><body>ending</body></html>'},
                             'Missing generated scene')

    def test_opening_without_required_maps_is_replaced_not_accepted(self):
        self._check_recovery({0: '<html><head></head><body>invented map</body></html>',
                              1: '<html><head></head><body>ending</body></html>'},
                             'Opening scene is missing required establishing maps')

    def test_malformed_repair_uses_local_recovery(self):
        generated = {0: '<html><head></head><body><histold-map id="canada"></histold-map>'
                        '<histold-map id="washington"></histold-map>bad-layout</body></html>',
                     1: '<html><head></head><body>ending</body></html>'}
        self._check_recovery(generated, 'bad layout', malformed_repair=True)

    def test_render_failure_refreshes_saved_html_and_report(self):
        generated = {0: '<html><head></head><body><histold-map id="canada"></histold-map>'
                        '<histold-map id="washington"></histold-map>render-failure</body></html>',
                     1: '<html><head></head><body>ending</body></html>'}
        self._check_recovery(generated, 'encoder failed', render_failure=True)

    def _check_recovery(self, generated, error, malformed_repair=False, render_failure=False):
        cfg = Config({'production': {'enabled': True}, 'video': {'branding': {'enabled': True}}})
        state = {
            'fmt': 'short',
            'script': {'title': 'Border detour', 'segments': [
                {'narration': 'This American town requires a border detour.'},
                {'narration': 'A border explains the unusual route.'},
            ]},
            'maps': [{'map_id': 'canada'}, {'map_id': 'washington'}],
            'storyboard': {'narration_hash': 'test-hash', 'scenes': [
                {'start': 0, 'end': 3, 'cues': []},
                {'start': 3, 'end': 6, 'cues': []},
            ]},
        }
        playwright = MagicMock()
        inspected = []
        rendered = []

        def inspect(_page, html, *_args):
            inspected.append(html)
            if 'bad-layout' in html:
                return {'errors': ['bad layout'], 'warnings': []}
            if 'malformed-repair' in html:
                raise ValueError('repair failed inspection')
            return {'errors': [], 'warnings': ['obsolete warning'] if 'render-failure' in html else []}

        def render(_page, html, _duration, _fps, destination, _preset):
            rendered.append(html)
            if 'render-failure' in html:
                raise RuntimeError('encoder failed')
            destination.touch()
            return destination

        with TemporaryDirectory() as tmp, \
             patch('playwright.sync_api.sync_playwright', return_value=playwright), \
             patch('src.claude_motion._request_scenes', return_value=generated), \
             patch('src.claude_motion.inspect_scene', side_effect=inspect), \
             patch('src.claude_motion._render_scene', side_effect=render), \
             patch('src.claude_motion._repair_scenes', return_value={0: '<html><histold-map id="canada"></histold-map><histold-map id="washington"></histold-map>malformed-repair</html>'}) as repair, \
             patch('src.assets.fetch_for_segments') as fetch, \
             patch('src.video_builder._dims', return_value=(1080, 1920, 30)), \
             patch('src.video_builder._encode_settings', return_value=('fast', 20)), \
             patch('src.video_builder._concat', side_effect=lambda _clips, path: path):
            stage = Path(tmp)
            self.assertEqual(render_motion_video(cfg, state, stage, [], 6), stage / 'silent.mp4')
            report = json.loads((stage / 'render_quality.json').read_text())
            saved = (stage / 'scenes/scene_00.html').read_text()

        fetch.assert_not_called()
        self.assertEqual(len(rendered), 3 if render_failure else 2)
        if malformed_repair:
            repair.assert_called_once()
        self.assertNotIn('render-failure', saved)
        self.assertNotIn('bad-layout', saved)
        self.assertEqual(report['scenes']['0'], {'errors': [], 'warnings': []})
        self.assertNotIn('obsolete warning', report['warnings'])
        self.assertTrue(any('data-map-id="canada"' in html for html in inspected))
        self.assertIn('data-map-id="washington"', saved)
        self.assertIn('This American town requires a border detour.', saved)
        self.assertNotIn('<img', saved)
        self.assertEqual(report['errors'], [])
        self.assertFalse(any('review required' in warning for warning in report['warnings']))
        self.assertEqual(report['recoveries'], [{
            'scene': 0, 'method': 'local_house_scene',
            'original_errors': [error], 'validated': True,
        }])

