import re
import unittest

from src.claude_motion import attach_pins, bind_maps
from src.map_assets import available_map_ids, load_map


class MapMotionTests(unittest.TestCase):
    def test_every_vetted_map_loads_whatever_the_system_encoding(self):
        # Titles carry an em dash; reading with the Windows default encoding broke the checksum.
        for map_id in available_map_ids():
            self.assertTrue(load_map(map_id)['svg'].startswith('<svg'))

    def test_pins_come_only_from_coordinates_inside_the_map(self):
        maps = [load_map('egypt')]
        attach_pins(maps, {'locations': [
            {'name': 'Giza, Egypt', 'map_id': 'egypt', 'lat': 29.98, 'lon': 31.13},
            {'name': 'Rome, Italy', 'map_id': None, 'lat': 41.9, 'lon': 12.5},       # outside the extent
            {'name': 'Cairo, Egypt', 'map_id': 'egypt', 'lat': None, 'lon': None},   # unknown: no pin
        ]})
        self.assertEqual([p['label'] for p in maps[0]['pins']], ['Giza'])
        html = bind_maps('<body><histold-map id="egypt"></histold-map></body>', maps)
        self.assertIn('class="histold-map hm-rich"', html)
        self.assertIn('class="hm-land" pathLength="1"', html)
        self.assertIn('>Giza</text>', html)
        self.assertIn('29.98° N · 31.13° E', html)  # coordinates card
        # Giza sits at (lon - west) * 6, (north - lat) * 6 in the rich map's padded view.
        from src.claude_motion import _rich
        west, _, _, north = _rich('egypt')['extent']
        self.assertIn(f'translate({(31.13 - west) * 6:.2f} {(north - 29.98) * 6:.2f})', html)

    def test_outline_maps_still_animate_when_a_rich_asset_is_missing(self):
        from unittest import mock
        maps = [load_map('egypt')]
        attach_pins(maps, {'locations': [{'name': 'Giza, Egypt', 'map_id': 'egypt', 'lat': 29.98, 'lon': 31.13}]})
        with mock.patch('src.claude_motion._rich', return_value=None):
            html = bind_maps('<body><histold-map id="egypt"></histold-map></body>', maps)
        self.assertNotIn('hm-rich"', html)
        self.assertIn('class="hm-cam"', html)
        west, _, _, north = maps[0]['extent']
        self.assertIn(f'translate({(31.13 - west) * 6:.2f} {(north - 29.98) * 6:.2f})', html)

    def test_rich_assets_match_their_recorded_hashes(self):
        from src.claude_motion import _rich
        for map_id in available_map_ids():
            self.assertIsNotNone(_rich(map_id), map_id)

    def test_us_maps_animate_and_states_draw_after_the_country(self):
        maps = [load_map('united_states'), load_map('minnesota')]
        attach_pins(maps, {'locations': [{'name': 'Brooklyn, New York', 'lat': 40.65, 'lon': -73.95}]})
        html = bind_maps('<body><histold-map id="united_states"></histold-map><histold-map id="minnesota"></histold-map></body>', maps)
        self.assertIn('data-map-id="minnesota" color="var(--gold)" style="--hd:1.2s"', html)
        self.assertIn('data-map-id="united_states"', html)
        self.assertEqual(html.count('>Brooklyn</text>'), 1)
        self.assertFalse(re.search(r'<histold-map', html))

    def test_no_pins_without_coordinates(self):
        maps = [load_map('united_states')]
        attach_pins(maps, {'locations': [{'name': 'United States', 'map_id': 'united_states'}]})
        self.assertNotIn('class="hm-drop"', bind_maps('<histold-map id="united_states"></histold-map>', maps))


if __name__ == '__main__':
    unittest.main()
