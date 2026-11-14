import unittest
from pathlib import Path
from unittest.mock import patch

from src import meta_uploader as mu
from src.config import Config


def _cfg(every=4):
    return Config({'meta': {'trial_reel_every': every, 'page_id': 'P', 'ig_user_id': 'IG',
                            'primary': True, 'stories': True}})


class TrialReelTests(unittest.TestCase):
    def setUp(self):
        for p in [patch.object(mu, 'env', return_value='tok'),
                  patch.object(mu, '_host_video', return_value=('https://x/v.mp4', lambda: None)),
                  patch.object(mu, '_publish_facebook_reel', return_value='fb1'),
                  patch.object(mu, '_publish_facebook_story', return_value='fbs1')]:
            p.start()
        self.ig = patch.object(mu, '_publish_instagram', side_effect=lambda *a, media_type='REELS', trial=False, **k:
                               'igs1' if media_type == 'STORIES' else 'igt' if trial else 'ig1').start()

    def tearDown(self):
        patch.stopall()

    def test_one_in_four_consecutive_slots_is_trial(self):
        slots = [mu._trial_slot(_cfg(), n * 43200 + 100) for n in range(8)]
        self.assertEqual(sum(slots), 2)
        self.assertFalse(any(mu._trial_slot(_cfg(0), n * 43200) for n in range(8)))

    def test_trial_slot_posts_trial_and_skips_ig_story(self):
        with patch.object(mu, '_trial_slot', return_value=True):
            r = mu.publish_reel(_cfg(), Path('v.mp4'), 'T', 'D')
        self.assertEqual((r['instagram'], r['instagram_trial']), ('igt', True))
        self.assertNotIn('instagram_story', r)
        self.assertEqual(r['facebook_story'], 'fbs1')

    def test_trial_rejected_falls_back_to_normal_reel(self):
        def ig(*a, media_type='REELS', trial=False, **k):
            if trial:
                raise RuntimeError('not eligible')
            return 'igs1' if media_type == 'STORIES' else 'ig1'
        self.ig.side_effect = ig
        with patch.object(mu, '_trial_slot', return_value=True):
            r = mu.publish_reel(_cfg(), Path('v.mp4'), 'T', 'D')
        self.assertEqual(r['instagram'], 'ig1')
        self.assertNotIn('instagram_trial', r)
        self.assertEqual(r['instagram_story'], 'igs1')

    def test_trial_param_sent_to_graph(self):
        patch.stopall()
        class R:
            def __init__(s, j): s.j = j; s.ok = True; s.status_code = 200
            def json(s): return s.j
        sent = {}
        def post(url, data=None, **k):
            sent.setdefault('urls', []).append(url); sent.setdefault('data', []).append(data)
            return R({'id': 'c1'})
        with patch.object(mu.requests, 'post', side_effect=post), \
             patch.object(mu.requests, 'get', return_value=R({'status_code': 'FINISHED'})), \
             patch.object(mu, '_raise_graph'):
            mu._publish_instagram('tok', 'IG', 'u', 'cap', 'v24.0', trial=True)
        self.assertIn('trial_params', sent['data'][0])
        self.assertIn('SS_PERFORMANCE', sent['data'][0]['trial_params'])


if __name__ == '__main__':
    unittest.main()
