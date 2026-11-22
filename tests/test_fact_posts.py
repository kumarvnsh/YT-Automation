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
        fetched = lambda cfg, url, **kw: {'url': url, 'cited_text': PAGE}
        with mock.patch('src.research._fetch_page', side_effect=fetched):
            verified = fp.verify_sources(CFG, fact)
        self.assertEqual({s['institution'] for s in verified}, {'si.edu', 'loc.gov'})

    def test_one_institution_or_invented_quotes_fail(self):
        fact = {'sources': [
            {'url': 'https://www.si.edu/a', 'excerpt': 'left his estate to the United States to found an establishment'},
            {'url': 'https://www.si.edu/b', 'excerpt': 'established in 1846 with funds from James Smithson'},
            {'url': 'https://www.loc.gov/c', 'excerpt': 'Smithson personally designed the castle building himself in 1840'}]}
        reasons = []
        with mock.patch('src.research._fetch_page', side_effect=lambda cfg, url, **kw: {'url': url, 'cited_text': PAGE}):
            self.assertEqual(fp.verify_sources(CFG, fact, reasons), [])
        self.assertIn('www.loc.gov: quote not found on the page', reasons)

    def test_failure_says_why_each_source_was_rejected(self):
        fact = {'headline': 'Smithson', 'sources': [
            {'url': 'https://www.si.edu/a', 'excerpt': 'left his estate to the United States to found an establishment'},
            {'url': 'https://www.loc.gov/b', 'excerpt': 'established in 1846 with funds from James Smithson'}]}

        def fetch(cfg, url, **kw):
            if 'loc.gov' in url:
                raise ConnectionError('reset')
            return {'url': url, 'cited_text': PAGE}
        with mock.patch.object(fp, 'research_fact', return_value=fact), \
                mock.patch('src.research._fetch_page', side_effect=fetch):
            with self.assertRaises(RuntimeError) as caught:
                fp.find_fact(CFG, [], attempts=1)
        self.assertIn('www.loc.gov: page not fetched (ConnectionError)', str(caught.exception))

    def test_overstated_fact_is_reworded_before_being_dropped(self):
        fact = {'headline': 'The richest man funded it all', 'fact': 'Smithson gave everything.', 'sources': [
            {'url': 'https://www.si.edu/a', 'publisher': 'Smithsonian', 'excerpt': 'left his estate to the United States to found an establishment'},
            {'url': 'https://www.loc.gov/b', 'publisher': 'LOC', 'excerpt': 'established in 1846 with funds from James Smithson'}]}
        reworded = json.dumps({'headline': 'A British scientist funded the Smithsonian',
                               'fact': 'James Smithson left his estate to the United States.', 'caption': 'C'})
        checks = iter([['"richest" is not in the excerpts'], []])
        with mock.patch.object(fp, 'research_fact', return_value=fact), \
                mock.patch('src.research._fetch_page', side_effect=lambda cfg, url, **kw: {'url': url, 'cited_text': PAGE}), \
                mock.patch.object(fp, 'check_claims', side_effect=lambda *a: next(checks)), \
                mock.patch.object(fp.plan_ai, 'complete', return_value=(reworded, 'claude')) as complete:
            found, sources = fp.find_fact(CFG, [], attempts=1)
        self.assertEqual(found['headline'], 'A British scientist funded the Smithsonian')
        self.assertEqual(found['fact'], 'James Smithson left his estate to the United States.')
        self.assertEqual(len(sources), 2)
        self.assertIn('richest', complete.call_args[0][2])

    def test_quote_is_checked_against_the_whole_page(self):
        long_page = 'filler words here. ' * 3000 + PAGE
        fetched = []
        fact = {'sources': [{'url': 'https://www.si.edu/a', 'excerpt': 'left his estate to the United States to found an establishment'}]}
        with mock.patch('src.research._fetch_page', side_effect=lambda cfg, url, **kw: fetched.append(kw) or {'url': url, 'cited_text': long_page}):
            reasons = []
            fp.verify_sources(CFG, fact, reasons)
        self.assertEqual(fetched, [{'limit': None}])
        self.assertNotIn('www.si.edu: quote not found on the page', reasons)

    def test_source_fetch_uses_ipv4_and_retries_a_dropped_connection(self):
        import socket
        import requests
        import urllib3.util.connection as connection
        from src import research
        seen = []

        def get(url, **kwargs):
            seen.append(connection.allowed_gai_family())
            if len(seen) == 1:
                raise requests.ConnectionError('reset by peer')
            return 'page'
        with mock.patch('requests.get', side_effect=get), mock.patch.object(research.time, 'sleep'):
            self.assertEqual(research._ipv4_get('https://www.nps.gov/x'), 'page')
        self.assertEqual(seen, [socket.AF_INET, socket.AF_INET])
        self.assertNotEqual(connection.allowed_gai_family(), None)

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

    def test_failed_posts_can_be_removed_from_the_list(self):
        from studio_hq import fact_posts_view as view
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            folder = root / 'private' / 'drafts' / 'fact-posts'
            folder.mkdir(parents=True)
            ids = ['20261122_100000_aaaaaa', '20261122_110000_bbbbbb', '20261122_120000_cccccc']
            (folder / 'queue.json').write_text(json.dumps([
                {'id': ids[0], 'status': 'failed'}, {'id': ids[1], 'status': 'failed'}, {'id': ids[2], 'status': 'posted'}]))
            config = {'dashboard_private_snapshot': str(root / 'private' / 'snap.json'), 'dashboard_histold_index_root': str(root / 'clone')}
            with self.assertRaises(ValueError):
                view.check({'action': 'fact_post_remove', 'id': '../x'})
            self.assertEqual(view.check({'action': 'fact_post_remove', 'id': 'failed'}), 'failed')
            with self.assertRaises(ValueError):  # a posted card is never removed
                view.perform(config, {'action': 'fact_post_remove', 'id': ids[2]})
            self.assertIn('Removed the failed post', view.perform(config, {'action': 'fact_post_remove', 'id': ids[0]}))
            self.assertEqual([p['id'] for p in view.snapshot(config, {})['posts']], [ids[2], ids[1]])
            self.assertIn('Removed the failed post', view.perform(config, {'action': 'fact_post_remove', 'id': 'failed'}))
            self.assertEqual([p['id'] for p in view.snapshot(config, {})['posts']], [ids[2]])
            stored = json.loads((folder / 'queue.json').read_text())
            self.assertEqual([r['status'] for r in stored], ['removed', 'removed', 'posted'])


if __name__ == '__main__':
    unittest.main()
