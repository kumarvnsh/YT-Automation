import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from src import painted_scenes, plan_ai
from src.ai_providers import reset_time
from src.cinematic_motion import prepare_html, system_prompt
from src.config import Config

SCENE = '<!doctype html><html><head><style>body{background:var(--parch)}</style></head><body><div class="k-stage">Hi</div></body></html>'


class Painted(unittest.TestCase):
    def test_only_the_laptop_flag_turns_it_on_and_adds_the_prompt_note(self):
        with mock.patch.dict(os.environ, {'HISTOLD_PAINTED': ''}):
            self.assertFalse(painted_scenes.enabled())
            self.assertNotIn('PAINTED BACKDROP', system_prompt(540, 960))
        with mock.patch.dict(os.environ, {'HISTOLD_PAINTED': '1'}):
            self.assertIn('PAINTED BACKDROP', system_prompt(540, 960))

    def test_backdrop_sits_behind_the_scene_and_its_motion_is_ambient(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / 'p.jpg'
            image.write_bytes(b'\xff\xd8fake')
            html = painted_scenes.apply(prepare_html(SCENE, 0, painted=True), image, 4.0, 1)
        self.assertIn('<div class="hp-paint"', html.split('<body>')[1])
        self.assertIn('animation:kit-fx-paint 4.00s', html)  # kit-fx-: motion checks ignore it
        self.assertIn('background:transparent!important', html)

    def test_light_palettes_swap_to_light_text_over_paintings(self):
        plain = prepare_html(SCENE, 0)
        painted = prepare_html(SCENE, 0, painted=True)
        self.assertIn('--parch:#f1eadb;--ink:#152f30', plain)
        self.assertIn('--parch:#152f30;--ink:#f1eadb', painted)
        self.assertIn('--parch:#153c3a', prepare_html(SCENE, 1, painted=True))  # already dark: unchanged

    def test_painter_shares_paintings_across_extra_scenes_and_survives_failures(self):
        drawn = []

        def fake(cfg, prompt, out, timeout=0):
            drawn.append(prompt)
            if len(drawn) == 2:
                raise RuntimeError('boom')
            Path(out).write_bytes(b'png')
            return out
        cfg = Config({'local_production': {'painted_scenes': {'max_images': 3, 'parallel': 1}}})
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(plan_ai, 'codex_image', side_effect=fake), \
                mock.patch.object(painted_scenes, '_fit', side_effect=lambda src, dest, w, h: dest.write_bytes(b'j') or dest):
            ready = painted_scenes.Painter(cfg, 'T', [f'line {i}' for i in range(6)], Path(folder), 1080, 1920).collect(30)
        self.assertEqual(len(drawn), 3)
        self.assertIn('No text', drawn[0])
        self.assertEqual(sorted(ready), [0, 1, 4, 5])  # painting 1 (scenes 2-3) failed; those keep the plain look

    def test_plan_limit_stops_further_paintings(self):
        calls = []

        def capped(cfg, prompt, out, timeout=0):
            calls.append(1)
            raise plan_ai.PlanUnavailable('capped')
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(plan_ai, 'codex_image', side_effect=capped):
            ready = painted_scenes.Painter(Config({'local_production': {'painted_scenes': {'parallel': 1}}}),
                                           'T', ['a', 'b', 'c'], Path(folder), 1080, 1920).collect(30)
        self.assertEqual((ready, len(calls)), ({}, 1))


class ResetTime(unittest.TestCase):
    def test_codex_dated_reset_is_understood(self):
        text = "You've hit your usage limit. ... or try again at Nov 25th, 2026 10:47 PM."
        self.assertEqual(datetime.fromtimestamp(reset_time(text)), datetime(2026, 11, 25, 22, 47))
        self.assertEqual(datetime.fromtimestamp(reset_time('try again at 3:35 AM', datetime(2026, 1, 1, 9))),
                         datetime(2026, 1, 2, 3, 35))
