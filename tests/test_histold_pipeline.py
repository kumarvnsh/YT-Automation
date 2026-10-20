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

    def test_new_profile_skips_verification_and_has_aligned_steps(self):
        cfg = Config({'production': {'enabled': True}, 'script': {'story_profile': 'consequence_first'}})
        with tempfile.TemporaryDirectory() as tmp, patch.object(pipeline, '_out_root', return_value=Path(tmp)):
            stage = pipeline.new_stage(cfg, 'short', no_upload=True, dry_run=False)
            state = pipeline.load_state(stage)
        steps = state['target_steps']
        self.assertLess(steps.index('research'), steps.index('script'))
        self.assertNotIn('verify', steps)
        self.assertLess(steps.index('script'), steps.index('voiceover'))
        self.assertLess(steps.index('align'), steps.index('captions'))
        self.assertIn('production_config', state)

    def test_legacy_stage_does_not_acquire_strict_settings(self):
        cfg = Config({'production': {'enabled': True}})
        effective = pipeline.stage_config(cfg, {'target_steps': ['script']})
        self.assertFalse(effective.get('production.enabled'))

    def test_old_failed_verification_stage_resumes_at_voiceover(self):
        state = {'target_steps':['research','script','verify','voiceover'], 'done':['research','script']}
        self.assertEqual(pipeline._pending_steps(state), ['voiceover'])

    def test_resumed_job_migrates_removed_step_and_keeps_map_metadata(self):
        state = {'production_config': {'production': {'enabled': True}}, 'fmt':'short',
                 'target_steps':['research','script','verify','voiceover','captions'],
                 'done':['research','script'], 'research': {'status':'supported','locations':[]},
                 'complete':False, 'error':'Historical verification failed'}
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            pipeline.save_state(stage, state)
            with patch.dict(pipeline.STEP_FUNCS, {'voiceover': lambda *_: None}):
                result = pipeline.run_stage(Config({}), stage, one_step=True)
            self.assertNotIn('verify', result['target_steps'])
            self.assertIn('voiceover', result['done'])
            self.assertEqual(result['maps'], [])
            self.assertIsNone(result['error'])

    def test_upload_rechecks_strict_artifacts_even_without_quality_step(self):
        cfg = Config({'production': {'enabled': True}})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, 'production|verification|quality'):
                pipeline._step_upload(cfg, Path(tmp), {'production_config': cfg.raw, 'script': {}})
