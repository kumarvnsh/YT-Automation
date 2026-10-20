import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import Config
from src import video_builder as vb


class HistoldCompositionTests(unittest.TestCase):
    def test_strict_captions_cannot_silently_disappear(self):
        cfg = Config({'production': {'enabled': True}, 'video': {'captions': {'enabled': True}}})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(vb, '_has_subtitles_filter', return_value=False), patch.object(vb, '_run'):
                with self.assertRaisesRegex(RuntimeError, 'caption'):
                    vb.finalize(cfg, root/'silent.mp4', root/'voice.mp3', root/'captions.ass',
                                root/'out.mp4', root, root)

    def test_branding_and_caption_application_are_recorded(self):
        cfg = Config({'production': {'enabled': True}, 'video': {'branding': {'enabled': True}}})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(vb, '_has_subtitles_filter', return_value=True), patch.object(vb, '_run') as run:
                vb.finalize(cfg, root/'silent.mp4', root/'voice.mp3', root/'captions.ass',
                            root/'out.mp4', root, root)
            filters = run.call_args_list[0].args[0]
            graph = filters[filters.index('-filter_complex')+1]
            self.assertIn('colorchannelmixer=aa=0.72', graph)
            self.assertIn('overlay=x=main_w*0.05:y=main_h*0.05', graph)
            self.assertIn('scale=108:-1', graph)
            self.assertTrue(any(str(arg).endswith('histold-logo.png') for arg in filters))
            self.assertNotIn('drawtext=', graph)
            self.assertTrue((root/'composition.json').exists())
