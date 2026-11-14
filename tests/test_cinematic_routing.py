import os,tempfile,types,sys,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from src.config import Config,load_config
from src import pipeline

class CinematicRoutingTests(unittest.TestCase):
 def test_live_default_off_selects_cinematic_not_old_format(self):
  cfg=load_config(Path(__file__).resolve().parents[1]/'config.yaml')
  with patch.dict(os.environ,{'VIDEO_3D':'off','VIDEO_MODE':''}):
   self.assertEqual(pipeline._effective_mode(cfg,'short'),'cinematic_2d')
 def test_explicit_legacy_and_checkpoint_are_preserved(self):
  cfg=Config({'video':{'mode':'cinematic_2d','three_d':{'enabled':True}}})
  with patch.dict(os.environ,{'VIDEO_3D':'off','VIDEO_MODE':'claude_motion'}):
   self.assertEqual(pipeline._effective_mode(cfg,'short'),'claude_motion')
  with patch.dict(os.environ,{'VIDEO_3D':'on','VIDEO_MODE':''}):
   self.assertEqual(pipeline._effective_mode(cfg,'short',{'visual_mode':'cinematic_2d'}),'cinematic_2d')
 def test_cinematic_skips_stock_and_dispatches_new_style(self):
  cfg=Config({});state={'fmt':'short','visual_mode':'cinematic_2d','voiceover':{'duration':5,'words':[]}}
  pipeline._step_assets(cfg,Path('.'),state);self.assertEqual(state['assets'],[])
  module=types.ModuleType('src.cinematic_motion');module.render_motion_video=Mock()
  with patch.dict(sys.modules,{'src.cinematic_motion':module}):pipeline._step_render(cfg,Path('.'),state)
  module.render_motion_video.assert_called_once()
 def test_cinematic_has_clean_caption_size(self):
  cfg=Config({'video':{'captions':{'fontsize_short':96}}})
  with patch('src.captions.build_ass') as build:
   pipeline._step_captions(cfg,Path('.'),{'fmt':'short','visual_mode':'cinematic_2d','voiceover':{'words':[]}})
  self.assertEqual(build.call_args.args[0].get('video.captions.fontsize_short'),66)
  self.assertEqual(cfg.get('video.captions.fontsize_short'),96)
 def test_cinematic_visual_failure_does_not_publish_old_fallback(self):
  report={'passed':False,'checks':{'render_quality':'fail'},'errors':['blank scene']}
  with tempfile.TemporaryDirectory() as tmp,patch('src.quality_gate.validate_stage',return_value=report):
   with self.assertRaisesRegex(RuntimeError,'visual checks'):
    pipeline._step_quality(Config({}),Path(tmp),{'fmt':'short','visual_mode':'cinematic_2d'})

if __name__=='__main__':unittest.main()
