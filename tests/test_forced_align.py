import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.alignment import build_storyboard
from src.config import Config
from src.forced_align import alignment_units, group_words
from src.tts import Voiceover, WordTiming, align_voiceover, synthesize


class UnitTests(unittest.TestCase):
    def test_numbers_accents_and_contractions(self):
        units = alignment_units("In 1,918 the café didn’t — open.")
        self.assertEqual([u[2] for u in units],
                         ['in', 'one', 'thousand', 'nine', 'hundred', 'eighteen', 'the', 'cafe', "didn't", 'open'])
        self.assertEqual({u[1] for u in units if u[0] == 1}, {'1,918'})
        self.assertNotIn(5, {u[0] for u in units})  # dash has nothing to align

    def test_grouping_keeps_display_words_and_feeds_strict_storyboard(self):
        text = 'Built in 1918. Gone by 1920.'
        units = alignment_units(text)
        spans, frame = [], 0
        for _, _, letters in units:
            spans.append([(frame + i, frame + i + 1, 0.9) for i in range(len(letters))])
            frame += len(letters) + 1
        words = group_words(units, spans, 0.02)
        self.assertEqual([w['word'] for w in words], ['Built', 'in', '1918.', 'Gone', 'by', '1920.'])
        self.assertLess(words[2]['start'], words[2]['end'])
        timings = [WordTiming(w['word'], w['start'], w['end']) for w in words]
        board = build_storyboard([{'narration': 'Built in 1918.'}, {'narration': 'Gone by 1920.'}],
                                 timings, frame * 0.02, 30, 'forced_aligned', strict=True)
        self.assertEqual(board['coverage'], 1)

    def test_number_readings(self):
        from src.forced_align import word_options
        self.assertEqual(word_options('1918,'), [['nineteen', 'eighteen'], ['one', 'thousand', 'nine', 'hundred', 'eighteen']])
        self.assertEqual(word_options('1905')[0], ['nineteen', 'oh', 'five'])
        self.assertEqual(word_options('1800')[0], ['eighteen', 'hundred'])
        self.assertEqual(word_options('2005'), [['two', 'thousand', 'five']])
        self.assertEqual(word_options('1,918'), [['one', 'thousand', 'nine', 'hundred', 'eighteen']])
        self.assertEqual(word_options('75%'), [['seventy', 'five', 'percent']])
        self.assertEqual(word_options('$300'), [['three', 'hundred', 'dollars']])
        self.assertEqual(word_options('1920s')[0], ['nineteen', 'twentys'])
        self.assertEqual(word_options('19th-century'), [['nineteenth', 'century']])
        self.assertEqual(word_options('—'), [])

    def test_wrong_text_fails_on_median(self):
        units = alignment_units('one two three')
        spans = [[(i * 4, i * 4 + 2, 0.3)] for i in range(3)]
        with self.assertRaisesRegex(ValueError, 'does not match'):
            group_words(units, spans, 0.02)

    def test_unspoken_words_fail_but_one_is_tolerated(self):
        units = alignment_units(' '.join(['word'] * 20))
        good = [[(i * 4, i * 4 + 2, 0.95)] for i in range(20)]
        one_bad = good[:5] + [[(20, 22, 0.0)]] + good[6:]
        self.assertEqual(len(group_words(units, one_bad, 0.02)), 20)
        two_bad = one_bad[:9] + [[(36, 38, 0.0)]] + one_bad[10:]
        with self.assertRaisesRegex(ValueError, 'not heard'):
            group_words(units, two_bad, 0.02)

    def test_padding_offset_is_removed(self):
        units = alignment_units('hello')
        words = group_words(units, [[(30, 40, 0.9)]], 0.02, offset=0.5)
        self.assertEqual((words[0]['start'], words[0]['end']), (0.1, 0.3))


class ProviderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'voice.mp3'
        self.estimated = Voiceover(self.path, 1, [WordTiming('hello', 0, 1)], 'estimated')
        self.aligned = Voiceover(self.path, 1, [WordTiming('hello', .1, .6)], 'forced_aligned')

    def cfg(self, aligner):
        return Config({'tts': {'provider': 'openai', 'alignment_provider': aligner},
                       'production': {'strict_timing': True}})

    def test_local_aligner_used_for_estimated_timing(self):
        with patch('src.tts._synthesize_openai', return_value=self.estimated), \
                patch('src.tts._align_local', return_value=self.aligned) as local, \
                patch('src.tts._align_openai') as remote:
            self.assertIs(synthesize(self.cfg('local'), 'hello', self.path), self.aligned)
            local.assert_called_once()
            self.assertEqual(local.call_args.args[2], 'hello')
            remote.assert_not_called()

    def test_local_failure_uses_whisper_only_with_key(self):
        cfg = self.cfg('local')
        with patch('src.tts._align_local', side_effect=RuntimeError('no torch')), \
                patch('src.tts._align_openai', return_value=self.aligned) as remote:
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'test'}):
                self.assertIs(align_voiceover(self.estimated, cfg, 'hello'), self.aligned)
            remote.assert_called_once()
            with patch.dict(os.environ, {'OPENAI_API_KEY': ''}), self.assertRaises(RuntimeError):
                align_voiceover(self.estimated, cfg, 'hello')

    def test_openai_provider_skips_local(self):
        with patch('src.tts._align_local') as local, patch('src.tts._align_openai', return_value=self.aligned):
            self.assertIs(align_voiceover(self.estimated, self.cfg('openai'), 'hello'), self.aligned)
            local.assert_not_called()


class PipelineRealignTests(unittest.TestCase):
    def test_scene_failure_is_force_aligned_before_approximating(self):
        from src import pipeline
        segments = [{'narration': 'Hello there.'}, {'narration': 'General Kenobi.'}]
        # One native chunk spans the scene boundary, which strict timing rejects.
        st = {'fmt': 'shorts', 'script': {'segments': segments},
              'voiceover': {'path': 'voiceover.mp3', 'duration': 2.0, 'timing_source': 'native',
                            'words': [['Hello', 0.0, 0.4], ['there. General', 0.5, 1.2], ['Kenobi.', 1.3, 1.9]]}}
        fixed = Voiceover(Path('voiceover.mp3'), 2.0, [WordTiming('Hello', 0, .4), WordTiming('there.', .5, .8),
                                                       WordTiming('General', .9, 1.2), WordTiming('Kenobi.', 1.3, 1.9)],
                          'forced_aligned')
        cfg = Config({'tts': {'alignment_provider': 'local'}})
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            (stage / 'voiceover.mp3').write_bytes(b'audio')
            with patch('src.tts.align_voiceover', return_value=fixed) as align:
                pipeline._step_align(cfg, stage, st)
            self.assertEqual(align.call_args.args[2], 'Hello there. General Kenobi.')
        self.assertEqual(st['storyboard']['timing_source'], 'forced_aligned')
        self.assertEqual(st['storyboard']['coverage'], 1)
        self.assertEqual(st['voiceover']['timing_source'], 'forced_aligned')


if __name__ == '__main__':
    unittest.main()
