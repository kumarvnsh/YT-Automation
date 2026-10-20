import json
from unittest.mock import Mock, patch
from scripts import auto_republish


def test_experiment_posts_are_never_automatically_republished(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    entries = [{'video_id':name,'experiment':experiment,'run_id':'r','stage_dir_name':'s',
                'published_at':'2026-10-01T00:00:00Z'} for name,experiment in [('experiment','histold-six-reels'),('control',None)]]
    (data / 'published_index.json').write_text(json.dumps(entries))
    (data / 'analytics.json').write_text(json.dumps({'videos':[
        {'id':name,'views':1,'publishedAt':'2026-10-01T00:00:00Z'} for name in ['experiment','control']]}))
    cfg=Mock()
    cfg.get.side_effect=lambda key,default=None: {'republish.auto':True,'republish.max_age_days':100}.get(key,default)
    with patch.object(auto_republish,'base_dir',return_value=tmp_path), patch.object(auto_republish,'load_config',return_value=cfg), patch.object(auto_republish,'_dispatch') as dispatch, patch('sys.argv',['auto_republish.py']):
        auto_republish.main()
    assert dispatch.call_count==1
    assert dispatch.call_args.args[1]=='control'

def test_unavailable_views_do_not_trigger_republishing(tmp_path):
    data=tmp_path / 'data'
    data.mkdir()
    (data / 'published_index.json').write_text(json.dumps([{'video_id':'unknown','run_id':'r','stage_dir_name':'s','published_at':'2026-10-01T00:00:00Z'}]))
    (data / 'analytics.json').write_text(json.dumps({'videos':[{'id':'unknown','views':None,'publishedAt':'2026-10-01T00:00:00Z'}]}))
    cfg=Mock()
    cfg.get.side_effect=lambda key,default=None: {'republish.auto':True,'republish.max_age_days':100}.get(key,default)
    with patch.object(auto_republish,'base_dir',return_value=tmp_path), patch.object(auto_republish,'load_config',return_value=cfg), patch.object(auto_republish,'_dispatch') as dispatch, patch('sys.argv',['auto_republish.py']):
        auto_republish.main()
    dispatch.assert_not_called()
