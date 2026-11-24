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


if __name__ == '__main__':
    unittest.main()
