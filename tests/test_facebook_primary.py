import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import Config
from src import pipeline, meta_uploader


class FacebookPrimaryTests(unittest.TestCase):
    def cfg(self):
        return Config({'meta': {'enabled': True, 'primary': True, 'post_to_facebook': True,
                              'post_to_instagram': False, 'page_id': 'page',
                              'facebook_caption': {'enabled': True, 'hashtags': ['#WorldHistory', '#Histold']}},
                       'youtube': {'enabled': True}})

    def state(self):
        return {'fmt': 'short', 'script': {'title': 'A surprising origin', 'description': 'An explanation.', 'tags': []}}

    def test_failed_facebook_stops_youtube_and_does_not_claim_success(self):
        state = self.state()
        with patch('src.meta_uploader.publish_reel', return_value={'facebook_error': 'offline'}), \
             patch('src.youtube_uploader.upload_video') as youtube:
            with self.assertRaisesRegex(RuntimeError, 'Facebook'):
                pipeline._step_upload(self.cfg(), Path('/unused'), state)
        youtube.assert_not_called()

    def test_facebook_publishes_first_and_successful_ids_are_reused_on_retry(self):
        state = self.state()
        order = []
        def facebook(*args, existing_results=None, **kwargs):
            if (existing_results or {}).get('facebook'):  # real publish_reel skips done platforms
                return existing_results
            order.append('facebook')
            return {'facebook': 'fb-id'}
        def youtube(*args, **kwargs):
            order.append('youtube')
            if len(order) == 2:
                raise RuntimeError('temporary youtube failure')
            return 'yt-id'
        with tempfile.TemporaryDirectory() as tmp, patch.object(pipeline, 'base_dir', return_value=Path(tmp)), \
             patch('src.meta_uploader.publish_reel', side_effect=facebook) as fb, \
             patch('src.youtube_uploader.upload_video', side_effect=youtube):
            stage = Path(tmp) / 'stage'
            stage.mkdir()
            with self.assertRaisesRegex(RuntimeError, 'temporary youtube'):
                pipeline._step_upload(self.cfg(), stage, state)
            self.assertEqual(state['meta']['facebook'], 'fb-id')
            pipeline._step_upload(self.cfg(), stage, state)
            pipeline._record_published(self.cfg(), stage, state)
            import json
            index = json.loads((Path(tmp) / 'data/published_index.json').read_text())
            self.assertEqual(len(index), 1)
            self.assertEqual(index[0]['video_id'], 'yt-id')
            self.assertEqual(index[0]['facebook_id'], 'fb-id')
        self.assertEqual(order, ['facebook', 'youtube', 'youtube'])

    def test_facebook_caption_is_short_relevant_and_has_no_youtube_promotion(self):
        cfg = self.cfg()
        caption = meta_uploader._build_caption(cfg, 'An everyday origin',
            'A sourced explanation.\n\nSubscribe on YouTube: https://youtube.com/x\n#viral #trending', platform='facebook')
        self.assertIn('An everyday origin', caption)
        self.assertIn('A sourced explanation.', caption)
        self.assertNotIn('YouTube', caption)
        self.assertNotIn('#viral', caption)
        self.assertLess(len(caption), 650)

    def test_instagram_caption_drops_youtube_promo_dedupes_tags_and_asks(self):
        cfg = Config({'meta': {'hashtags': ['#WorldHistory', '#Histold']}})
        caption = meta_uploader._build_caption(cfg, 'Noodles are older than you think',
            'Found under a bowl in China. #FoodHistory #worldhistory #A #B #C\n'
            'Subscribe on YouTube: https://youtube.com/x\n\n'
            'Portrait of X: Y, CC BY-SA 4.0, via Wikimedia Commons',
            question='What would you have eaten instead?')
        self.assertTrue(caption.startswith('Noodles are older than you think'))
        self.assertNotIn('YouTube', caption)
        self.assertIn('via Wikimedia Commons', caption)  # licence credit kept
        self.assertEqual(caption.split('\n\n')[-2], 'What would you have eaten instead?')
        tags = caption.split('\n\n')[-1].split()
        self.assertEqual(tags, ['#WorldHistory', '#Histold', '#FoodHistory', '#A', '#B'])
        self.assertNotIn('#', caption.rsplit('\n\n', 1)[0])

    def test_closing_question_is_last_question_of_callback(self):
        from src.pipeline import _closing_question
        sc = {'segments': [{'narration': 'Hook.'}, {'narration': 'So it began. Would you have tried it? Think.'}]}
        self.assertEqual(_closing_question(sc), 'Would you have tried it?')
        self.assertEqual(_closing_question({'segments': [{'narration': 'No question here.'}]}), '')

    def test_meta_retry_does_not_republish_successful_facebook(self):
        cfg = self.cfg()
        with patch.object(meta_uploader, 'env', return_value='token'), \
             patch.object(meta_uploader, '_host_video') as host, \
             patch.object(meta_uploader, '_publish_facebook_reel') as facebook:
            result = meta_uploader.publish_reel(cfg, Path('/unused'), 'Title', 'Text', existing_results={'facebook': 'already'})
        self.assertEqual(result['facebook'], 'already')
        host.assert_not_called()
        facebook.assert_not_called()
