"""Regression: recovered scenes explained nothing and every one repeated the maps."""
import unittest

import re

from src.claude_motion import bind_maps, ensure_opening_maps, house_scene, key_facts, key_words, summarize_motion
from src.map_assets import load_map


class SceneExplanationTests(unittest.TestCase):
    def test_key_facts_extracts_dates_numbers_names_in_order(self):
        facts = key_facts('In four days in 1666, the Great Fire of London destroyed 13,200 houses.')
        self.assertEqual(facts, ['1666', 'Great Fire of London', '13,200'])

    def test_house_scene_without_maps_shows_fact_cards(self):
        html = house_scene('By 1945, Japan had lost 3.1 million people.', [], 4.0)
        self.assertIn('house-fact', html)
        self.assertIn('3.1 million', html)
        self.assertNotIn('data-map-id', html)

    def test_house_scene_without_facts_uses_key_words_not_dots(self):
        html = house_scene('People kept using them for a very long time before the story ends.', [], 5.0)
        self.assertEqual(key_words('And that is how the story ends.'), ['story'])
        self.assertIn('house-fact', html)
        self.assertNotIn('<circle', html)

    def test_house_scene_beats_never_leave_a_lone_word(self):
        html = house_scene('People kept using them for a very long time before the story ends.', [], 5.0)
        beats = re.findall(r'class="house-beat"[^>]*>([^<]*)', html)
        self.assertGreater(min(len(b.split()) for b in beats), 1)

    def test_bind_maps_drops_unvetted_map_instead_of_rejecting_scene(self):
        html = bind_maps('<div class="k-stage"><histold-map id="atlantis"></histold-map>1666</div>', [])
        self.assertEqual(html, '<div class="k-stage">1666</div>')

    def test_opening_maps_inserted_at_top_of_stage_when_model_forgets(self):
        maps = [load_map('canada')]
        html = ensure_opening_maps('<body><div class="k-stage k-x"><b>1846</b></div></body>', maps)
        self.assertIn('<div class="k-stage k-x"><histold-map id="canada"></histold-map><b>1846</b>', html)

    def test_brief_payoff_hold_passes_long_freeze_fails(self):
        times = [0, .75, 1.5, 2.25]
        self.assertEqual(summarize_motion([0, 0, 0], times, limit=3.0), [])
        self.assertIn('static_foreground', summarize_motion([0] * 5, [0, .75, 1.5, 2.25, 3, 3.75], limit=3.0))


if __name__ == '__main__':
    unittest.main()
