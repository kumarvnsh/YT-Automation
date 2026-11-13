import unittest
from pathlib import Path
from unittest.mock import patch

from src import meta_uploader as mu
from src.config import Config


def _cfg(stories=True):
    return Config({'meta': {'stories': stories, 'page_id': 'P', 'ig_user_id': 'IG', 'primary': True}})


class MetaStoriesTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(mu, 'env', return_value='tok'),
                        patch.object(mu, '_host_video', return_value=('https://x/v.mp4', lambda: None)),
                        patch.object(mu, '_publish_facebook_reel', return_value='fb1'),
                        patch.object(mu, '_publish_facebook_story', return_value='fbs1')]
        for p in self.patches:
            p.start()
        self.ig = patch.object(mu, '_publish_instagram', side_effect=lambda *a, media_type='REELS', **k:
                               'igs1' if media_type == 'STORIES' else 'ig1').start()

    def tearDown(self):
        patch.stopall()

    def test_meta_stories_enabled_posts_reel_then_both_stories(self):
        results = mu.publish_reel(_cfg(), Path('v.mp4'), 'T', 'D')
        self.assertEqual(results, {'facebook': 'fb1', 'instagram': 'ig1',
                                   'instagram_story': 'igs1', 'facebook_story': 'fbs1'})

    def test_meta_stories_disabled_posts_no_story(self):
        results = mu.publish_reel(_cfg(stories=False), Path('v.mp4'), 'T', 'D')
        self.assertNotIn('instagram_story', results)
        self.assertNotIn('facebook_story', results)

    def test_meta_story_failure_keeps_reel_ids(self):
        mu._publish_facebook_story.side_effect = RuntimeError('boom')
        results = mu.publish_reel(_cfg(), Path('v.mp4'), 'T', 'D')
        self.assertEqual(results['facebook'], 'fb1')
        self.assertEqual(results['facebook_story_error'], 'boom')

    def test_progress_saves_every_result_before_next_publication(self):
        snapshots = []
        mu.publish_reel(_cfg(), Path('v.mp4'), 'T', 'D',
                        on_progress=lambda result: snapshots.append(result))
        self.assertEqual(snapshots[0], {'facebook': 'fb1'})
        self.assertEqual(len(snapshots), 4)
        self.assertIn('facebook_story', snapshots[-1])

    def test_instagram_failure_preserves_facebook_and_reports_error(self):
        self.ig.side_effect = RuntimeError('IG unavailable')
        snapshots = []
        result = mu.publish_reel(_cfg(stories=False), Path('v.mp4'), 'T', 'D',
                                on_progress=lambda result: snapshots.append(result))
        self.assertEqual(result['facebook'], 'fb1')
        self.assertEqual(result['instagram_error'], 'IG unavailable')
        self.assertEqual(snapshots[-1], result)

    def test_persistence_failure_stops_before_next_platform(self):
        with self.assertRaisesRegex(RuntimeError, 'disk full'):
            mu.publish_reel(_cfg(), Path('v.mp4'), 'T', 'D',
                            on_progress=lambda result: (_ for _ in ()).throw(RuntimeError('disk full')))
        self.ig.assert_not_called()

    def test_retry_facebook_success_instagram_failure_only_posts_missing(self):
        result = mu.publish_reel(_cfg(stories=False), Path('v.mp4'), 'T', 'D',
                                 existing_results={'facebook': 'fb1', 'instagram_error': 'offline'})
        mu._publish_facebook_reel.assert_not_called()
        self.assertEqual(result, {'facebook': 'fb1', 'instagram': 'ig1'})

    def test_meta_retry_posts_only_missing_story(self):
        done = {'facebook': 'fb1', 'instagram': 'ig1', 'instagram_story': 'igs1'}
        results = mu.publish_reel(_cfg(), Path('v.mp4'), 'T', 'D', existing_results=done)
        self.assertEqual(results['facebook_story'], 'fbs1')
        self.ig.assert_not_called()


if __name__ == '__main__':
    unittest.main()
