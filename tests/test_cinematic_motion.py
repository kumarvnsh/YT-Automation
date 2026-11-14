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


if __name__ == '__main__':
    unittest.main()
