import unittest

from src import map_assets


class MapAssetTests(unittest.TestCase):
    def test_vetted_assets_have_provenance_and_safe_geometry(self):
        for name in ('united_states', 'canada', 'minnesota', 'washington'):
            asset = map_assets.load_map(name)
            self.assertIn('<path', asset['svg'])
            self.assertEqual(asset['license'], 'Public domain')
            self.assertIn('v5.1.2', asset['source_url'])

    def test_unknown_location_is_not_invented(self):
        with self.assertRaises(ValueError):
            map_assets.load_map('atlantis')

    def test_active_svg_is_rejected(self):
        for text in ('<svg><script>alert(1)</script></svg>',
                     '<svg><path onclick="run()"/></svg>',
                     '<svg><image href="https://example.com/x"/></svg>'):
            with self.assertRaises(ValueError):
                map_assets.sanitize_svg(text)

    def test_geographic_claims_require_assets(self):
        with self.assertRaises(ValueError):
            map_assets.required_maps({'locations': [{'map_id': 'missing'}]})
