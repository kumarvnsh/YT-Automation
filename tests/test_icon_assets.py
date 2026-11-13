import unittest

from src.claude_motion import house_scene
from src.icon_assets import CREDIT, DEFAULTS, bind_icons, candidate_icons, load_icons


class IconAssetTests(unittest.TestCase):
    def test_icon_library_paths_are_sanitized_path_data_only(self):
        icons = load_icons()
        self.assertGreater(len(icons), 4000)
        self.assertFalse([n for n, i in icons.items() if '<' in i['d'] or '"' in i['d']])

    def test_candidate_icons_match_script_words_and_skip_fantasy(self):
        names = candidate_icons(["Russia's Hermitage library began with books from a philosopher's shelves."])
        self.assertIn('bookshelf', names)
        self.assertIn('philosopher-bust', names)
        self.assertFalse([n for n in names if 'evil' in n or 'spell' in n])
        self.assertTrue(set(DEFAULTS) <= set(names))

    def test_bind_icons_inlines_known_keeps_class_style_and_drops_unknown(self):
        html, used = bind_icons('<histold-icon name="crown" class="k-pop" style="--d:.4s"></histold-icon>'
                                '<histold-icon name="not-real"/>')
        self.assertEqual(used, ['crown'])
        self.assertIn('class="k-icon k-pop"', html)
        self.assertIn('style="--d:.4s"', html)
        self.assertNotIn('histold-icon', html)

    def test_house_scene_adds_matched_icon_above_facts(self):
        html = house_scene('Persians stored winter ice in 1700 inside a huge clay dome.', [], 5.0)
        self.assertIn('<histold-icon name="', html)

    def test_credit_names_license(self):
        self.assertIn('CC BY 3.0', CREDIT)


if __name__ == '__main__':
    unittest.main()
