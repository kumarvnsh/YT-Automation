import os
import unittest
from unittest import mock

from src import cinematic_motion as kits


class SceneStyleTests(unittest.TestCase):
    def test_machine_stories_get_flatlit_and_events_get_papercut(self):
        machine = {'title': 'A 2,500-Pound Machine of Gears', 'segments': [
            {'narration': 'Each gear turned a shaft; the machine added every tide.'}]}
        event = {'title': 'Why India Walked to the Sea for Salt', 'segments': [
            {'narration': 'Thousands followed Gandhi to the coast in 1930.'}]}
        self.assertEqual(kits.choose_style(machine), 'flatlit')
        self.assertEqual(kits.choose_style(event), 'papercut')

    def test_auto_resolves_per_script_and_each_kit_falls_back_to_the_other_then_textured(self):
        with mock.patch.dict(os.environ, {'HISTOLD_STYLE': 'auto'}):
            self.assertEqual(kits.resolve_style({'title': 'Salt march'}), 'papercut')
        with mock.patch.dict(os.environ, {'HISTOLD_STYLE': 'flatlit'}):
            self.assertEqual(kits.resolve_style({'title': 'Salt march'}), 'flatlit')
        with mock.patch.dict(os.environ, {'HISTOLD_STYLE': ''}):
            self.assertEqual(kits.resolve_style({}), '')
        self.assertEqual(kits.style_chain('papercut'), ['papercut', 'flatlit', 'textured'])
        self.assertEqual(kits.style_chain('flatlit'), ['flatlit', 'papercut', 'textured'])
        self.assertEqual(kits.style_chain(''), [])

    def test_writing_style_switches_the_brief_and_restores_it(self):
        with mock.patch.dict(os.environ, {'HISTOLD_STYLE': 'papercut', 'HISTOLD_TEXTURE': '1'}):
            self.assertIn('CUT-PAPER', kits.system_prompt(540, 960))
            with kits.writing_style('flatlit'):
                self.assertIn('FLAT-LIT', kits.system_prompt(540, 960))
            with kits.writing_style('textured'):
                prompt = kits.system_prompt(540, 960)
                self.assertIn('TEXTURED ILLUSTRATION', prompt)
                self.assertNotIn('CUT-PAPER', prompt)
            self.assertEqual(os.environ['HISTOLD_STYLE'], 'papercut')

    def test_a_tagged_scene_is_prepared_with_its_own_kit(self):
        scene = '<!doctype html><html><head></head><body><div class="k-stage">Hi</div></body></html>'
        with mock.patch.dict(os.environ, {'HISTOLD_STYLE': 'papercut'}):
            self.assertIn('pc-torn', kits.prepare_html(scene, 0))
            flat = kits.prepare_html(kits.tag_style(scene, 'flatlit'), 0)
            self.assertIn('fl-pill', flat)
            self.assertNotIn('pc-torn', flat)
            textured = kits.prepare_html(kits.tag_style(scene, 'textured'), 0)
            self.assertIn('hx-ink', textured)
        self.assertEqual(kits.tagged_style(kits.tag_style(kits.tag_style(scene, 'papercut'), 'flatlit')), 'flatlit')


class EmptyPanelTests(unittest.TestCase):
    def test_a_flat_framed_box_is_empty_and_one_with_drawing_is_not(self):
        from PIL import Image, ImageDraw
        from src.claude_motion import _panel_is_empty
        shot = Image.new('L', (1080, 1920), 40)
        draw = ImageDraw.Draw(shot)
        draw.rectangle((100, 200, 900, 700), fill=90, outline=200, width=6)   # empty grey panel
        draw.rectangle((100, 900, 900, 1400), fill=90, outline=200, width=6)  # panel with a gear row
        for x in range(200, 800, 120):
            draw.ellipse((x, 1080, x + 80, 1160), outline=230, width=8)
        self.assertTrue(_panel_is_empty(shot, {'x': 50, 'y': 100, 'w': 400, 'h': 250}, 540))
        self.assertFalse(_panel_is_empty(shot, {'x': 50, 'y': 450, 'w': 400, 'h': 250}, 540))

    def test_the_scene_brief_forbids_empty_panels(self):
        from pathlib import Path
        self.assertIn('NEVER show an empty frame', Path('src/cinematic_2d_prompt.txt').read_text(encoding='utf-8'))

if __name__ == '__main__':
    unittest.main()
