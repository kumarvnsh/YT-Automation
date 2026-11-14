"""Preview style selection must stay unpublished and preserve source audio."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.cinematic_preview import main
from src.config import Config


class CinematicPreviewTests(unittest.TestCase):
    def test_woodblock_reuses_audio_and_never_calls_upload(self):
        source = {'script': {'title': 'History'}, 'voiceover': {'file': 'voiceover.mp3'},
                  'done': ['research', 'script', 'voiceover', 'render', 'upload'],
                  'production_config': {'video': {}}, 'youtube_url': 'old-url',
                  'video': 'video.mp4'}
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            (stage / 'state.json').write_text(json.dumps(source))
            (stage / 'video.mp4').write_bytes(b'old video')
            with patch('scripts.cinematic_preview.load_config', return_value=Config({'video': {}})), \
                 patch('src.pipeline._step_captions'), patch('src.pipeline._step_render') as render, \
                 patch('src.pipeline._step_compose'), patch('src.pipeline._step_quality'), \
                 patch('src.pipeline._step_upload') as upload:
                main(directory, 'woodblock')
            saved = json.loads((stage / 'state.json').read_text())
            self.assertEqual(render.call_args.args[0].get('video.claude_motion.art_style'), 'woodblock')
            self.assertEqual(saved['voiceover'], source['voiceover'])
            self.assertTrue(saved['no_upload'])
            self.assertEqual(saved['preview_style'], 'woodblock')
            self.assertNotIn('upload', saved['done'])
            self.assertNotIn('youtube_url', saved)
            self.assertFalse((stage / 'video.mp4').exists())
            upload.assert_not_called()

    def test_unknown_style_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown preview style'):
            main('unused', 'unknown')
