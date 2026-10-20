import unittest
from src.audio_quality import parse_silences, evaluate_audio

class AudioQualityTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_silences('silence_start: 1\nsilence_end: 2 | silence_duration: 1',3),[{'start':1.,'end':2.}])
    def test_allowed_pause_and_truncation(self):
        report = evaluate_audio([{'start':1.,'end':2.}], 3, {'last_word_end':3.4,'coverage':1,'allowed_pauses':[{'start':.95,'end':2.05}]})
        self.assertTrue(report['intervals'][0]['intentional'])
        self.assertTrue(report['errors'])
        self.assertFalse(report['warnings'])
    def test_bad_output(self):
        with self.assertRaises(ValueError): parse_silences('silence_start: garbage',3)
    def test_missing_audio(self):
        from src.audio_quality import analyze_audio
        class Config:
            def get(self,k,d=None): return d
        with self.assertRaises(ValueError): analyze_audio(Config(),'/missing/audio.wav',{})
    def test_unbounded_pause(self):
        with self.assertRaises(ValueError): evaluate_audio([],1,{'allowed_pauses':[{'start':0,'end':2}]})
    def test_endpoints(self):
        from src.audio_quality import validate_endpoints
        self.assertEqual(validate_endpoints(3,2,3),['video ends before final narration'])
    def test_duplicate_silence_start_rejected(self):
        with self.assertRaises(ValueError): parse_silences('silence_start: 0\nsilence_start: 1',3)
    def test_nonfinite_endpoint_rejected(self):
        from src.audio_quality import validate_endpoints
        self.assertEqual(validate_endpoints(float('nan'),3,2),['audio ends before final narration'])
