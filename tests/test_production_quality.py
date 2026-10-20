"""Regression checks for narration-bound production upload requirements."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.alignment import build_storyboard
from src.config import Config
from src.quality_gate import file_hash, production_checks, validate_stage
from src.research import script_hash
from src.tts import WordTiming


class ProductionQualityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.stage = Path(temporary.name)
        self.cfg = Config({'production': {'enabled': True}, 'video': {'short': {'fps': 30}, 'branding': {'enabled': True}}})
        self.script = {'segments': [{'narration': 'Hello world.', 'beat': 'hook'}, {'narration': 'This resolves it.', 'beat': 'callback'}]}
        words = [WordTiming(w, i*.3, (i+1)*.3) for i,w in enumerate(['Hello','world','This','resolves','it'])]
        (self.stage/'voiceover.mp3').write_bytes(b'original narration')
        board = build_storyboard(self.script['segments'], words, 1.7, 30, 'native')
        board['audio_sha256'] = file_hash(self.stage/'voiceover.mp3')
        self.state = {
            'fmt': 'short', 'script': self.script,
            'verification': {'status': 'supported', 'script_hash': script_hash(self.script)},
            'research': {'status': 'supported', 'locations': []}, 'maps': [],
            'voiceover': {'path': 'voiceover.mp3', 'duration': 1.7, 'timing_source': 'native', 'words': [[w.word,w.start,w.end] for w in words]},
            'storyboard': board,
            'audio_quality': {'status': 'pass', 'errors': [], 'narration_hash': board['narration_hash'], 'audio_sha256': board['audio_sha256']},
            'render_quality': {'errors': [], 'warnings': [], 'narration_hash': board['narration_hash']},
        }
        (self.stage/'video.mp4').write_bytes(b'final video')
        (self.stage/'captions.ass').write_text('captions')
        self.composition = {'captions_applied': True, 'branding_applied': True,
                            'video_sha256': file_hash(self.stage/'video.mp4'),
                            'audio_sha256': file_hash(self.stage/'voiceover.mp3'),
                            'captions_sha256': file_hash(self.stage/'captions.ass')}
        (self.stage/'composition.json').write_text(json.dumps(self.composition))
        self.evidence = patch('src.research.validate_research', return_value=[])
        self.evidence.start()
        self.addCleanup(self.evidence.stop)

    def checks(self):
        return production_checks(self.cfg, self.stage, self.state)

    def test_valid_current_artifacts_pass(self):
        checks, errors, _ = self.checks()
        self.assertTrue(all(v == 'pass' for v in checks.values()), errors)
        self.assertEqual(errors, [])

    def test_changed_audio_invalidates_board(self):
        (self.stage/'voiceover.mp3').write_bytes(b'regenerated different delivery')
        checks, _, _ = self.checks()
        self.assertEqual(checks['timing_binding'], 'fail')

    def test_changed_final_video_invalidates_composition(self):
        (self.stage/'video.mp4').write_bytes(b'replaced or truncated video')
        self.assertEqual(self.checks()[0]['composition_binding'], 'fail')

    def test_changed_caption_input_invalidates_composition(self):
        (self.stage/'captions.ass').write_text('new captions')
        self.assertEqual(self.checks()[0]['composition_binding'], 'fail')

    def test_changed_word_timing_invalidates_board(self):
        self.state['voiceover']['words'][2][1] += .05
        checks, _, _ = self.checks()
        self.assertEqual(checks['timing_binding'], 'fail')

    def test_changed_scene_boundary_invalidates_board(self):
        self.state['storyboard']['scenes'][0]['end_frame'] += 1
        self.assertEqual(self.checks()[0]['timing_binding'], 'fail')

    def test_estimates_cannot_pass_as_measured(self):
        self.state['voiceover']['timing_source'] = 'estimated'
        self.assertEqual(self.checks()[0]['timing_binding'], 'fail')

    def test_audio_diagnostic_for_another_file_fails(self):
        self.state['audio_quality']['audio_sha256'] = 'old-audio'
        self.assertEqual(self.checks()[0]['audio_diagnostics'], 'fail')

    def test_changed_narration_invalidates_verification_and_alignment(self):
        self.script['segments'][0]['narration'] = 'Different words.'
        checks, _, _ = self.checks()
        self.assertEqual(checks['verification'], 'fail')
        self.assertEqual(checks['alignment'], 'fail')

    def test_unsupported_verification_fails(self):
        self.state['verification']['status'] = 'unsupported'
        self.assertEqual(self.checks()[0]['verification'], 'fail')

    def test_missing_evidence_fails(self):
        with patch('src.research.validate_research', return_value=['Missing directly cited support']):
            checks, errors, _ = self.checks()
        self.assertEqual(checks['research_evidence'], 'fail')
        self.assertIn('Missing directly cited support', errors)

    def test_missing_composition_fails_captions_and_brand(self):
        (self.stage/'composition.json').unlink()
        checks, _, _ = self.checks()
        self.assertEqual(checks['caption_composition'], 'fail')
        self.assertEqual(checks['branding'], 'fail')

    def test_fallback_needs_explicit_review(self):
        self.state['render_quality']['warnings'] = ['Scene 0 used stock fallback; visual review required']
        self.assertEqual(self.checks()[0]['fallback_review'], 'fail')
        self.state['visual_reviewed'] = True
        self.assertEqual(self.checks()[0]['fallback_review'], 'pass')

    def test_audio_errors_are_blocking(self):
        self.state['audio_quality']['status'] = 'fail'
        self.state['audio_quality']['errors'] = ['Narration is entirely silent']
        self.assertEqual(self.checks()[0]['audio_diagnostics'], 'fail')

    def test_warning_only_silence_is_not_blocking(self):
        self.state['audio_quality']['status'] = 'warn'
        self.state['audio_quality']['warnings'] = ['Unexpected internal silence']
        checks, errors, warnings = self.checks()
        self.assertEqual(checks['audio_diagnostics'], 'pass')
        self.assertEqual(errors, [])
        self.assertIn('Unexpected internal silence', warnings)

    def test_short_video_stream_cannot_hide_behind_long_container(self):
        from tests.test_quality_gate import _quality_cfg, _valid_state
        cfg = _quality_cfg()
        cfg.raw['production'] = {'enabled': True}
        state = _valid_state()
        state['storyboard'] = {'last_word_end': 19.9}
        (self.stage/'video.mp4').write_bytes(b'video')
        (self.stage/'captions.ass').write_text('captions')
        (self.stage/'assets').mkdir()
        (self.stage/'assets'/'seg_00.jpg').write_bytes(b'image')
        probe = {'streams': [{'codec_type':'video','width':1080,'height':1920,'duration':'10','start_time':'0'}, {'codec_type':'audio','duration':'20','start_time':'0'}], 'format': {'duration':'20'}}
        with patch('src.quality_gate._ffprobe', return_value=probe), patch('src.quality_gate.production_checks', return_value=({},[],[])):
            report = validate_stage(cfg,self.stage,state)
        self.assertEqual(report['checks']['narration_endpoint'],'fail')
        self.assertTrue(any('video' in error and 'narration' in error for error in report['errors']))
