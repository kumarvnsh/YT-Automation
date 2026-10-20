import unittest
from src.alignment import build_storyboard
from src.tts import WordTiming

class AlignmentTests(unittest.TestCase):
    def test_text_match_numbers_and_frames(self):
        segments = [{'narration': 'In 49, we stopped.', 'beat': 'hook'}, {'narration': "Don't stop!", 'beat': 'callback'}]
        words = [WordTiming(w, i*.3, (i+1)*.3) for i,w in enumerate(['In','forty','nine','we','stopped','do','not','stop'])]
        result = build_storyboard(segments, words, 2.6, 30, 'native')
        self.assertEqual(result['scenes'][0]['end_frame'], result['scenes'][1]['start_frame'])
        self.assertEqual(result['scenes'][-1]['end_frame'], 78)
        self.assertEqual(result['coverage'], 1)
    def test_missing_word_rejected(self):
        with self.assertRaises(ValueError):
            build_storyboard([{'narration':'one two'}], [WordTiming('one',0,.5)], 1,30,'native')
    def test_estimated_rejected(self):
        with self.assertRaises(ValueError):
            build_storyboard([{'narration':'one'}],[WordTiming('one',0,.5)],1,30,'estimated')
    def test_overlap_rejected(self):
        with self.assertRaises(ValueError):
            build_storyboard([{'narration':'one two'}],[WordTiming('one',0,.6),WordTiming('two',.5,.9)],1,30,'native')
    def test_repeated_words_monotonic(self):
        result=build_storyboard([{'narration':'go go'},{'narration':'go now'}],[WordTiming(w,i*.25,(i+1)*.25) for i,w in enumerate(['go','go','go','now'])],1.1,30,'native')
        self.assertEqual(result['scenes'][1]['start_frame'],15)
    def test_impossible_short_windows(self):
        with self.assertRaises(ValueError):
            build_storyboard([{'narration':'a'},{'narration':'b'}],[WordTiming('a',0,.001),WordTiming('b',.001,.002)],1,30,'native')
    def test_duration_and_empty_rejected(self):
        for words in ([],[WordTiming('hello',0,2)]):
            with self.assertRaises(ValueError): build_storyboard([{'narration':'hello'}],words,1,30,'native')
    def test_unexpected_spoken_word_rejected(self):
        with self.assertRaises(ValueError):
            build_storyboard([{'narration':'hello'}],[WordTiming('hello',0,.2),WordTiming('world',.2,.4)],1,30,'native')

class TimingProvenanceTests(unittest.TestCase):
    def test_legacy_voiceover_defaults_estimated(self):
        from pathlib import Path
        from src.tts import Voiceover
        self.assertEqual(Voiceover(Path('a.mp3'),1,[]).timing_source,'estimated')
    def test_strict_synthesis_rejects_estimates(self):
        from pathlib import Path
        from unittest.mock import patch
        from src.tts import Voiceover, synthesize
        class Config:
            def get(self,k,d=None): return {'production.strict_timing':True}.get(k,d)
        with patch('src.tts._synthesize_edge',return_value=Voiceover(Path('a.mp3'),1,[WordTiming('hello',0,.5)])):
            with self.assertRaisesRegex(ValueError,'measured|native'): synthesize(Config(),'hello',Path('/tmp/histold-test-a.mp3'))
    def test_strict_synthesis_accepts_native(self):
        from pathlib import Path
        from unittest.mock import patch
        from src.tts import Voiceover, synthesize
        class Config:
            def get(self,k,d=None): return {'production.strict_timing':True}.get(k,d)
        voice=Voiceover(Path('a.mp3'),1,[WordTiming('hello',0,.5)],'native')
        with patch('src.tts._synthesize_edge',return_value=voice):
            self.assertIs(synthesize(Config(),'hello',Path('/tmp/histold-test-a.mp3')),voice)
    def test_sentence_chunk_cannot_span_scenes(self):
        with self.assertRaisesRegex(ValueError,'chunk crosses'):
            build_storyboard([{'narration':'hello'},{'narration':'world'}],[WordTiming('hello world',0,.8)],1,30,'native')
    def test_sentence_events_do_not_masquerade_as_word_timings(self):
        import asyncio
        import tempfile
        import types
        from pathlib import Path
        from unittest.mock import patch
        from src.tts import _edge_synthesize
        class Communicate:
            def __init__(self,*a,**k): pass
            async def stream(self):
                yield {'type':'SentenceBoundary','text':'hello world','offset':0,'duration':10000000}
                yield {'type':'audio','data':b'audio'}
        with tempfile.TemporaryDirectory() as d, patch.dict('sys.modules',{'edge_tts':types.SimpleNamespace(Communicate=Communicate)}):
            self.assertEqual(asyncio.run(_edge_synthesize('hello world','voice','+0%',Path(d)/'a.mp3')),[])
