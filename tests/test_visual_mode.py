import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import Config
from src import pipeline


class VisualModeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config({'video': {'mode': 'claude_motion', 'three_d': {'enabled': True}}})

    def test_new_stages_snapshot_mode_across_toggle_changes(self):
        with tempfile.TemporaryDirectory() as tmp, patch('src.pipeline._out_root', return_value=Path(tmp)), patch.dict(os.environ, {'VIDEO_3D': 'on', 'VIDEO_MODE': ''}):
            stage = pipeline.new_stage(self.cfg, 'short', no_upload=True, dry_run=True)
            state = json.loads((stage / 'state.json').read_text())
            self.assertEqual(state.get('visual_mode'), 'three_motion')
            os.environ['VIDEO_3D'] = 'off'
            self.assertEqual(pipeline._effective_mode(self.cfg, 'short', state), 'three_motion')

    def test_override_and_legacy_modes(self):
        with patch.dict(os.environ, {'VIDEO_3D': 'on', 'VIDEO_MODE': ''}):
            self.assertEqual(pipeline._effective_mode(self.cfg, 'short', {}), 'claude_motion')
            self.assertEqual(pipeline._effective_mode(self.cfg, 'long'), 'broll')
            self.assertEqual(pipeline._effective_mode(Config({'video': {'mode': 'broll'}}), 'short'), 'broll')
        with patch.dict(os.environ, {'VIDEO_3D': 'on', 'VIDEO_MODE': 'claude_motion'}):
            self.assertEqual(pipeline._effective_mode(self.cfg, 'short'), 'claude_motion')
        with patch.dict(os.environ, {'VIDEO_3D': 'off', 'VIDEO_MODE': ''}):
            self.assertEqual(pipeline._effective_mode(self.cfg, 'short'), 'claude_motion')

    def test_three_motion_skips_stock_assets(self):
        state = {'fmt': 'short', 'visual_mode': 'three_motion'}
        pipeline._step_assets(self.cfg, Path('.'), state)
        self.assertEqual(state['assets'], [])

    def test_toggle_sets_repository_variable(self):
        from src.visual_mode import main
        with patch('src.visual_mode.subprocess.run') as run:
            main(['off', '--repo', 'owner/repo'])
        run.assert_called_once_with(['gh', 'variable', 'set', 'HISTOLD_3D', '--body', 'off', '--repo', 'owner/repo'], check=True)

    def test_three_motion_blocks_failed_quality(self):
        report = {'passed': False, 'checks': {'render': 'fail'}, 'errors': ['bad render']}
        with tempfile.TemporaryDirectory() as tmp, patch('src.quality_gate.validate_stage', return_value=report):
            with self.assertRaisesRegex(RuntimeError, 'quality'):
                pipeline._step_quality(self.cfg, Path(tmp), {'fmt': 'short', 'visual_mode': 'three_motion'})

    def test_workflow_installs_pinned_renderer_and_exports_toggle(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/publish.yml').read_text()
        for text in ('vars.HISTOLD_3D', 'VIDEO_3D:', 'node-version: "22"', 'npm ci', 'HYPERFRAMES_CLI='):
            self.assertIn(text, workflow)

    def test_three_motion_render_dispatch(self):
        import sys
        import types
        from unittest.mock import Mock
        render = Mock()
        module = types.ModuleType('src.three_motion')
        module.render_motion_video = render
        state = {'fmt': 'short', 'visual_mode': 'three_motion', 'assets': [],
                 'voiceover': {'duration': 5, 'words': []}}
        with patch.dict(sys.modules, {'src.three_motion': module}):
            pipeline._step_render(self.cfg, Path('.'), state)
        render.assert_called_once_with(self.cfg, state, Path('.'), [], 5)

    def test_three_motion_failure_falls_back_to_claude_motion(self):
        import sys
        import types
        from unittest.mock import Mock
        three = types.ModuleType('src.three_motion')
        three.render_motion_video = Mock(side_effect=RuntimeError('blank scene'))
        claude = types.ModuleType('src.claude_motion')
        claude.render_motion_video = Mock()
        state = {'fmt': 'short', 'visual_mode': 'three_motion', 'assets': [],
                 'voiceover': {'duration': 5, 'words': []}}
        with patch.dict(sys.modules, {'src.three_motion': three, 'src.claude_motion': claude}):
            pipeline._step_render(self.cfg, Path('.'), state)
        claude.render_motion_video.assert_called_once()
        self.assertEqual(state['visual_mode'], 'claude_motion')
        self.assertIn('blank scene', state['render_fallback'])

    def test_upload_persists_each_meta_id_before_next_platform(self):
        cfg = Config({'meta': {'enabled': True, 'primary': True, 'post_to_facebook': True, 'page_id': '1'},
                      'youtube': {'enabled': False}})
        def fake_publish(*a, existing_results=None, on_progress=None):
            on_progress({'facebook': 'fb1'})
            raise RuntimeError('crash before instagram')
        with tempfile.TemporaryDirectory() as tmp, patch('src.meta_uploader.publish_reel', side_effect=fake_publish):
            state = {'fmt': 'short', 'script': {'title': 't', 'description': 'd', 'tags': []}}
            with self.assertRaises(RuntimeError):
                pipeline._step_upload(cfg, Path(tmp), state)
            saved = json.loads((Path(tmp) / 'state.json').read_text())
            self.assertEqual(saved['meta'], {'facebook': 'fb1'})

    def test_three_motion_captions_are_smaller_without_mutating_config(self):
        state = {'fmt': 'short', 'visual_mode': 'three_motion', 'voiceover': {'words': []}}
        with patch('src.captions.build_ass') as build:
            pipeline._step_captions(self.cfg, Path('.'), state)
        scoped = build.call_args.args[0]
        self.assertEqual(scoped.get('video.captions.fontsize_short'), 58)
        self.assertEqual(build.call_args.kwargs['position'], 'bottom')
        self.assertIsNone(self.cfg.get('video.captions.fontsize_short'))

    def test_legacy_failed_quality_retains_warning_behavior(self):
        report = {'passed': False, 'checks': {'render': 'fail'}, 'errors': ['bad render']}
        with tempfile.TemporaryDirectory() as tmp, patch('src.quality_gate.validate_stage', return_value=report):
            state = {'fmt': 'short'}
            pipeline._step_quality(self.cfg, Path(tmp), state)
            self.assertEqual(state['quality'], report)
