import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import Config
from src import pipeline


class HistoldPipelineTests(unittest.TestCase):
    def test_corrupt_old_stage_does_not_block_experiment_and_slots_are_distinct(self):
        cfg = Config({'production': {'enabled': True, 'experiment': True}})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'data/experiments').mkdir(parents=True)
            (root/'data/experiments/histold-six-reels.json').write_text(json.dumps({'id':'test','slots':[
                {'slot':1,'category':'geography','hook':'first'}, {'slot':2,'category':'history','hook':'next'}]}))
            output = root/'output'
            (output/'broken').mkdir(parents=True)
            (output/'broken/state.json').write_text('{')
            with patch.object(pipeline, 'base_dir', return_value=root):
                first = pipeline.new_stage(cfg, 'short', no_upload=True, dry_run=False)
                second = pipeline.new_stage(cfg, 'short', no_upload=True, dry_run=False)
            self.assertNotEqual(first,second)
            self.assertEqual(pipeline.load_state(first)['experiment_slot'],1)
            self.assertEqual(pipeline.load_state(second)['experiment_slot'],2)

    def test_new_profile_has_verified_and_aligned_steps_and_snapshot(self):
        cfg = Config({'production': {'enabled': True}, 'script': {'story_profile': 'consequence_first'}})
        with tempfile.TemporaryDirectory() as tmp, patch.object(pipeline, '_out_root', return_value=Path(tmp)):
            stage = pipeline.new_stage(cfg, 'short', no_upload=True, dry_run=False)
            state = pipeline.load_state(stage)
        steps = state['target_steps']
        self.assertLess(steps.index('research'), steps.index('script'))
        self.assertLess(steps.index('verify'), steps.index('voiceover'))
        self.assertLess(steps.index('align'), steps.index('captions'))
        self.assertIn('production_config', state)

    def test_legacy_stage_does_not_acquire_strict_settings(self):
        cfg = Config({'production': {'enabled': True}})
        effective = pipeline.stage_config(cfg, {'target_steps': ['script']})
        self.assertFalse(effective.get('production.enabled'))

    def test_unverified_script_stops_before_voice_generation(self):
        cfg = Config({'production': {'enabled': True}})
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            state = {'script': {'segments': [{'narration': 'A claim.'}]}, 'research': {}}
            with patch('src.research.verify_script', return_value={'status': 'unsupported'}):
                with self.assertRaisesRegex(RuntimeError, 'verif'):
                    pipeline._step_verify(cfg, stage, state)
            self.assertTrue((stage/'verification.json').exists())

    def test_upload_rechecks_strict_artifacts_even_without_quality_step(self):
        cfg = Config({'production': {'enabled': True}})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, 'production|verification|quality'):
                pipeline._step_upload(cfg, Path(tmp), {'production_config': cfg.raw, 'script': {}})
