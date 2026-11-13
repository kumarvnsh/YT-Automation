import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from src import portrait_assets as pa
from src.claude_motion import house_scene


def _entity(died=True, human=True, image=True):
    claims = {'P31': [{'mainsnak': {'datavalue': {'value': {'id': 'Q5' if human else 'Q515'}}}}],
              'P569': [{'mainsnak': {'datavalue': {'value': {'time': '+1713-10-05T00:00:00Z'}}}}]}
    if died:
        claims['P570'] = [{'mainsnak': {'datavalue': {'value': {'time': '+1784-07-31T00:00:00Z'}}}}]
    if image:
        claims['P18'] = [{'mainsnak': {'datavalue': {'value': 'Diderot.jpg'}}}]
    return {'entities': {'Q1': {'claims': claims}}}


def _api(license_name='Public domain', **entity):
    def get(url, **params):
        if 'summary' in url:
            return {'type': 'standard', 'wikibase_item': 'Q1'}
        if 'wikidata' in url:
            return _entity(**entity)
        return {'query': {'pages': {'1': {'imageinfo': [{'thumburl': 'https://up/x.jpg', 'extmetadata': {
            'LicenseShortName': {'value': license_name}, 'Artist': {'value': '<a href="#">Alix</a>'}}}]}}}}
    return get


class PortraitAssetTests(unittest.TestCase):
    person = {'name': 'Denis Diderot', 'wikipedia_title': 'Denis Diderot'}

    def _fetch(self, **kw):
        image = MagicMock(content=b'jpg', headers={'content-type': 'image/jpeg'})
        with TemporaryDirectory() as tmp, patch.object(pa, '_get', side_effect=_api(**kw)), \
                patch.object(pa.requests, 'get', return_value=image):
            return pa.fetch_portrait(self.person, Path(tmp))

    def test_portrait_deceased_public_domain_accepted_with_credit(self):
        p = self._fetch()
        self.assertEqual((p['id'], p['years']), ('denis_diderot', '1713–1784'))
        self.assertEqual(p['credit'], 'Portrait of Denis Diderot: Alix, Public domain, via Wikimedia Commons')

    def test_portrait_living_person_rejected(self):
        self.assertIsNone(self._fetch(died=False))

    def test_portrait_non_human_rejected(self):
        self.assertIsNone(self._fetch(human=False))

    def test_portrait_non_free_license_rejected(self):
        self.assertIsNone(self._fetch(license_name='Fair use'))
        self.assertIsNotNone(self._fetch(license_name='CC BY-SA 4.0'))

    def test_mentioned_matches_surname_not_title_words(self):
        self.assertTrue(pa.mentioned(self.person, 'Diderot sold his books.'))
        self.assertFalse(pa.mentioned({'name': 'Catherine the Great'}, 'A great library.'))

    def test_bind_photos_embeds_image_and_drops_unknown_ids(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp, 'd.jpg'); f.write_bytes(b'jpg')
            portrait = {'id': 'denis_diderot', 'name': 'Denis Diderot', 'years': '1713–1784', 'file': str(f), 'mime': 'image/jpeg'}
            html, used = pa.bind_photos('<histold-photo id="denis_diderot"></histold-photo><histold-photo id="x"/>', [portrait])
            fallback = house_scene('Diderot needed money in 1765.', [], 5.0, [portrait])
        self.assertEqual(used, ['denis_diderot'])
        self.assertIn('src="data:image/jpeg;base64,', html)
        self.assertNotIn('histold-photo', html)
        self.assertIn('<histold-photo id="denis_diderot"', fallback)


if __name__ == '__main__':
    unittest.main()
