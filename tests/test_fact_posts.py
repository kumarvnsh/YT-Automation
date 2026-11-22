import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from src import fact_posts as fp
from src.config import Config

CFG = Config({'fact_posts': {'approve_until': '2026-11-29'}, 'meta': {'hashtags': ['#WorldHistory', '#Histold']},
              'research': {'authoritative_domains': ['loc.gov', 'archives.gov', 'si.edu']}})
PAGE = ('The Smithsonian Institution was established in 1846 with funds from James Smithson, a British scientist who '
        'left his estate to the United States to found an establishment for the increase and diffusion of knowledge.')


class Queue(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(fp, 'POSTS', Path(self.folder.name)), mock.patch.object(fp, 'QUEUE', Path(self.folder.name) / 'queue.json')]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.folder.cleanup()

    def test_approval_period_then_automatic(self):
        self.assertTrue(fp.needs_approval(CFG, datetime(2026, 11, 29, 20, tzinfo=timezone.utc)))
        self.assertFalse(fp.needs_approval(CFG, datetime(2026, 11, 30, 1, tzinfo=timezone.utc)))
        self.assertTrue(fp.needs_approval(Config({'fact_posts': {'always_approve': True}})))

    def test_decide_approves_once_and_starts_posting(self):
        fp.save_queue([{'id': '20261122_124801_5b2615', 'status': 'awaiting_approval'}])
        started = []
        self.assertIn('Approved', fp.decide('20261122_124801_5b2615', 'approve', start=started.append))
        self.assertEqual(started, ['20261122_124801_5b2615'])
        self.assertIn('already approved', fp.decide('20261122_124801_5b2615', 'approve', start=started.append))
        self.assertEqual(len(started), 1)

    def test_skip(self):
        fp.save_queue([{'id': '20261122_124801_5b2615', 'status': 'awaiting_approval'}])
        fp.decide('20261122_124801_5b2615', 'skip', start=lambda _id: self.fail('must not post'))
        self.assertEqual(fp.load_queue()[0]['status'], 'skipped')


class Verification(unittest.TestCase):
    def test_quotes_must_be_on_two_institutions_pages(self):
        fact = {'sources': [
            {'url': 'https://www.si.edu/about/history', 'publisher': 'Smithsonian', 'excerpt': 'left his estate to the United States to found an establishment'},
            {'url': 'https://www.loc.gov/item/x', 'publisher': 'Library of Congress', 'excerpt': 'established in 1846 with funds from James Smithson, a British scientist'},
            {'url': 'https://example.com/blog', 'publisher': 'Blog', 'excerpt': 'established in 1846 with funds from James Smithson'}]}
        fetched = lambda cfg, url: {'url': url, 'cited_text': PAGE}
        with mock.patch('src.research._fetch_page', side_effect=fetched):
            verified = fp.verify_sources(CFG, fact)
        self.assertEqual({s['institution'] for s in verified}, {'si.edu', 'loc.gov'})

    def test_one_institution_or_invented_quotes_fail(self):
        fact = {'sources': [
            {'url': 'https://www.si.edu/a', 'excerpt': 'left his estate to the United States to found an establishment'},
            {'url': 'https://www.si.edu/b', 'excerpt': 'established in 1846 with funds from James Smithson'},
            {'url': 'https://www.loc.gov/c', 'excerpt': 'Smithson personally designed the castle building himself in 1840'}]}
        with mock.patch('src.research._fetch_page', side_effect=lambda cfg, url: {'url': url, 'cited_text': PAGE}):
            self.assertEqual(fp.verify_sources(CFG, fact), [])

    def test_caption_has_sources_and_tags(self):
        text = fp.caption(CFG, {'headline': 'H', 'caption': 'C', 'hashtags': ['Smithsonian', '#History']},
                          [{'publisher': 'Smithsonian', 'institution': 'si.edu'}, {'publisher': None, 'institution': 'loc.gov'}])
        self.assertIn('Sources: Smithsonian, loc.gov.', text)
        self.assertIn('#Smithsonian #History #WorldHistory #Histold', text)


class StudioView(unittest.TestCase):
    def test_view_registers_cards_and_validates_controls(self):
        from studio_hq import fact_posts_view as view
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            folder = root / 'private' / 'drafts' / 'fact-posts'
            (folder / '20261122_124801_5b2615').mkdir(parents=True)
            card = folder / '20261122_124801_5b2615' / 'card.jpg'
            card.write_bytes(b'jpg')
            (folder / 'queue.json').write_text(json.dumps([{'id': '20261122_124801_5b2615', 'status': 'awaiting_approval',
                'card': str(card), 'headline': 'H', 'sources': [{'publisher': 'Smithsonian', 'url': 'https://www.si.edu'}]}]))
            config = {'dashboard_private_snapshot': str(root / 'private' / 'snap.json'), 'dashboard_histold_index_root': str(root / 'clone')}
            media = {}
            data = view.snapshot(config, media)
            self.assertEqual(data['posts'][0]['label'], 'Waiting for your OK')
            self.assertEqual(media[data['posts'][0]['image']], str(card.resolve()))
            for body in ({'action': 'fact_post_decide', 'id': '../x', 'choice': 'approve'}, {'action': 'fact_post_schedule', 'enabled': 'yes'}):
                with self.assertRaises(ValueError):
                    view.check(body)
            self.assertIn('Skipped', view.perform(config, {'action': 'fact_post_decide', 'id': '20261122_124801_5b2615', 'choice': 'skip'}))


if __name__ == '__main__':
    unittest.main()
