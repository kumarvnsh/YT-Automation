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

    def test_world_maps_are_manifest_discoverable_and_nonempty(self):
        names = map_assets.available_map_ids()
        for name in ('india', 'japan', 'russia', 'africa', 'middle_east', 'egypt', 'kenya', 'nigeria', 'south_africa', 'iran', 'iraq', 'saudi_arabia', 'turkey'):
            self.assertIn(name, names)
            asset = map_assets.load_map(name)
            self.assertIn('Present-day', asset['scope'])
            self.assertTrue(asset['source_sha256'])
            self.assertTrue(asset['feature_names'])
            import re
            coordinates = re.findall(r'([\d.-]+),([\d.-]+)', asset['svg'])
            self.assertGreater(len(coordinates), 10)
            import xml.etree.ElementTree as ET
            width, height = map(float, ET.fromstring(asset['svg']).attrib['viewBox'].split()[2:])
            for x, y in coordinates:
                self.assertTrue(0 <= float(x) <= width)
                self.assertTrue(0 <= float(y) <= height)

    def test_checksum_tampering_is_rejected(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'manifest.json').write_text(json.dumps({'assets': {'test': {'file': 'test.svg', 'sha256': 'wrong'}}}))
            (root / 'test.svg').write_text('<svg><path d="M0,0L1,1Z"/></svg>')
            with patch.object(map_assets, 'ROOT', root):
                self.assertEqual(map_assets.available_map_ids(), ['test'])
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    map_assets.load_map('test')

    def test_original_builder_preserves_world_manifest_entries(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock, patch
        from scripts import build_map_assets
        features = [{'properties': {'ADMIN': label, 'name': label},
                     'geometry': {'type': 'Polygon', 'coordinates': [[[-110, 30], [-100, 30], [-100, 40], [-110, 30]]]}}
                    for label in ('United States of America', 'Canada', 'Minnesota', 'Washington')]
        response = Mock()
        response.json.return_value = {'features': features}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            world = {'file': 'india.svg', 'sha256': 'existing-reviewed-checksum'}
            (root / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'assets': {'india': world}}))
            with patch.object(build_map_assets, 'ROOT', root), patch.object(build_map_assets.requests, 'get', return_value=response):
                build_map_assets.main()
            manifest = json.loads((root / 'manifest.json').read_text())
            self.assertEqual(manifest['assets']['india'], world)
            self.assertIn('canada', manifest['assets'])
