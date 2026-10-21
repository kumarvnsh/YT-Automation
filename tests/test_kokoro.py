import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from src.config import Config
from src.tts import Voiceover, WordTiming, synthesize

class KokoroTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'voice.mp3'
        self.cfg = Config({'tts': {'provider': 'kokoro'}, 'production': {'strict_timing': True}})

    def test_selected_provider(self):
        voice = Voiceover(self.path, 1, [WordTiming('hello', .1, .6)], 'native')
        with patch('src.tts._synthesize_kokoro', return_value=voice), patch('src.tts._synthesize_edge') as edge:
            self.assertIs(synthesize(self.cfg, 'hello', self.path), voice)
            edge.assert_not_called()

    def test_worker_failure_falls_back_to_edge(self):
        voice = Voiceover(self.path, 1, [WordTiming('hello', .1, .6)], 'native')
        with patch('src.tts._synthesize_kokoro', side_effect=TimeoutError('worker timeout')), patch('src.tts._synthesize_edge', return_value=voice):
            self.assertIs(synthesize(self.cfg, 'hello', self.path), voice)

    def test_bad_native_timing_falls_back(self):
        bad = Voiceover(self.path, 1, [WordTiming('wrong', .1, .6)], 'native')
        good = Voiceover(self.path, 1, [WordTiming('hello', .1, .6)], 'native')
        with patch('src.tts._synthesize_kokoro', return_value=bad), patch('src.tts._synthesize_edge', return_value=good):
            self.assertIs(synthesize(self.cfg, 'hello', self.path), good)

    def test_chunk_offset_and_punctuation(self):
        from types import SimpleNamespace as Token
        from src.kokoro_voice import token_timings
        words = token_timings([Token(text='Hello', start_ts=.1, end_ts=.4), Token(text='!',start_ts=None,end_ts=None)], 2.0)
        self.assertEqual(words, [{'word': 'Hello', 'start': 2.1, 'end': 2.4}])

    def test_untimed_spoken_token_is_rejected(self):
        from types import SimpleNamespace as Token
        from src.kokoro_voice import token_timings
        with self.assertRaises(ValueError):
            token_timings([Token(text='hello', start_ts=None, end_ts=None)], 0)
