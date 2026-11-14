import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import wave

from src.config import Config
from src.cinematic_motion import prepare_html, render_motion_video, system_prompt


class CinematicMotionTests(unittest.TestCase):
    def test_injection_is_offline_and_preserves_authored_animation(self):
        authored = '<style>@keyframes arch{to{opacity:1}}.arch{animation:arch 2s both}</style>'
        html = prepare_html('<html><head>' + authored + '</head><body><div class="k-stage"></div></body></html>', 0)
        self.assertIn(authored, html)
        self.assertLess(html.index('id="cinematic-2d"'), html.index(authored))
        self.assertIn('data:font/ttf;base64,', html)
        self.assertIn("font-family:'Libre Baskerville'", html)
        self.assertNotIn('Oswald', html)
        self.assertNotIn('#09192e', html)
        self.assertNotIn('https://', html)
        self.assertEqual(prepare_html(html, 0).count('id="cinematic-2d"'), 1)

    def test_themes_alternate(self):
        self.assertIn('--parch:#f1eadb', prepare_html('<head></head>', 0))
        self.assertIn('--parch:#153c3a', prepare_html('<head></head>', 1))

    def test_prompt_has_parser_and_safe_area_contract(self):
        prompt = system_prompt(540, 960)
        self.assertIn('540 x 960', prompt)
        self.assertIn('<scene index="0">', prompt)
        self.assertIn('bottom 180 CSS pixels', prompt)
        self.assertIn('CSS @keyframes ONLY', prompt)

    def test_wrapper_scopes_config_and_generates_quiet_effects(self):
        raw = {'video': {'claude_motion': {'max_tokens': 9000}}}
        original = copy.deepcopy(raw)
        state = {'storyboard': {'scenes': [{'start': 0}, {'start': .5}]}}
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            with patch('src.claude_motion.render_motion_video', return_value=stage / 'motion.mp4') as render:
                render_motion_video(Config(raw), state, stage, [], 1)
            self.assertEqual(raw, original)
            scoped = render.call_args.args[0]
            self.assertEqual(scoped.get('video.claude_motion.style'), 'cinematic_2d')
            self.assertEqual(scoped.get('video.claude_motion.max_tokens'), 9000)
            with wave.open(str(stage / state['cinematic_effects'])) as effect:
                self.assertEqual(effect.getframerate(), 24000)
                self.assertEqual(effect.getnframes(), 36000)

    def test_truncated_generation_requests_only_missing_scenes(self):
        from types import SimpleNamespace as NS
        from unittest.mock import MagicMock
        from src.claude_motion import _request_scenes
        first = NS(stop_reason='max_tokens', content=[NS(type='text', text='<scene index="0"><html>first</html></scene>')], usage=NS(input_tokens=10, output_tokens=20))
        recovered = NS(stop_reason='end_turn', content=[NS(type='text', text='<scene index="1"><html>last</html></scene>')], usage=NS(input_tokens=10, output_tokens=10))
        client = MagicMock()
        client.beta.messages.stream.return_value.__enter__.return_value.get_final_message.side_effect = [first, recovered]
        seg = NS(narration='Printing changed history', keywords=['printing'], beat='hook')
        with patch('anthropic.Anthropic', return_value=client):
            scenes = _request_scenes(Config({'video': {'claude_motion': {'style': 'cinematic_2d'}}}), 'Press', [seg, seg], [(0, 3), (3, 6)], 540, 960)
        self.assertEqual(scenes, {0: '<html>first</html>', 1: '<html>last</html>'})
        self.assertEqual(client.beta.messages.stream.call_count, 2)
        request = client.beta.messages.stream.call_args.kwargs['messages'][0]['content']
        self.assertIn('ONLY these missing zero-based scene IDs: [1]', request)


if __name__ == '__main__':
    unittest.main()
