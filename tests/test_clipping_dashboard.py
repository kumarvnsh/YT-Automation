import importlib.util
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import pytest

SCRIPT = Path(__file__).resolve().parents[1]/'scripts/clipping_dashboard.py'
spec = importlib.util.spec_from_file_location('clipping_dashboard_test',SCRIPT)
clipping = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clipping)


def test_url_validation_rejects_non_channel_and_private_hosts():
    assert clipping.target_url('https://youtube.com/@Mythpat') == ('youtube','https://www.youtube.com/@Mythpat')
    assert clipping.target_url('https://twitch.tv/Pokimane') == ('twitch','https://www.twitch.tv/pokimane')
    for value in ['http://127.0.0.1','https://youtube.com.evil.test/@a',
                  'https://user@youtube.com/@a','https://youtube.com/watch?v=abcd',
                  'https://youtube.com:443/@a']:
        with pytest.raises(ValueError):
            clipping.target_url(value)


def test_missing_views_are_not_zero_and_zero_is_measured():
    report = clipping.evidence({'videos':[{'id':'a','title':'GTA 5','views':None},
                                          {'id':'b','title':'Roblox','views':0}]})
    assert report['videos_measured']==1 and report['listed_lifetime_views']==0
    assert report['weak'][0]['id']=='b'
    assert clipping.evidence({})['listed_lifetime_views'] is None


def test_targets_are_persisted_paused_and_deduplicated():
    with tempfile.TemporaryDirectory() as folder, patch.object(clipping,'CLIP_ROOT',Path(folder)):
        assert 'Added' in clipping.action({'name':'add_target','url':'https://twitch.tv/example_creator'})
        assert 'already' in clipping.action({'name':'add_target','url':'https://twitch.tv/example_creator'})
        clipping.action({'name':'set_target','id':'example_creator','enabled':False})
        data=clipping.read(Path(folder)/'watchlist.json')
        assert len(data['creators'])==1 and not data['creators'][0]['enabled']
        with pytest.raises(ValueError):
            clipping.action({'name':'boost_option','args':{'region':'europe'}})


def test_media_paths_stay_inside_work_and_secrets_are_never_collected():
    with tempfile.TemporaryDirectory() as folder, patch.object(clipping,'CLIP_ROOT',Path(folder)):
        root=Path(folder); (root/'work').mkdir()
        (root/'token.json').write_text('{"refresh_token":"DO_NOT_EXPOSE"}')
        (root/'work/test.mp4').write_bytes(b'test')
        assert clipping.media_file('test.mp4')==root/'work/test.mp4'
        for path in ['../token.json','../../secret.mp4','missing.mp4']:
            with pytest.raises(ValueError):
                clipping.media_file(path)
        assert 'DO_NOT_EXPOSE' not in json.dumps(clipping.collect())


def test_no_source_learning_from_unlinked_uploads_and_no_compounding():
    with tempfile.TemporaryDirectory() as folder, patch.object(clipping,'CLIP_ROOT',Path(folder)):
        root=Path(folder)
        clipping.write(root/'work/dashboard/analytics.json',{'videos':[
            {'id':'legacy','views':100000,'published_at':'2026-11-10T00:00:00Z'}]})
        clipping.write(root/'work/dashboard/agent-policy.json',{'learning_enabled':True,'source_priorities':{'old':1.5}})
        report=clipping.review()
        assert not report['findings']
        assert clipping.policy()['source_priorities']=={}
        clipping.review()
        assert clipping.policy()['source_priorities']=={}


def test_queue_plan_controls_write_only_the_bounded_settings(tmp_path):
    with patch.object(clipping,'CLIP_ROOT',tmp_path):
        (tmp_path/'scheduler.json').write_text(json.dumps({'enabled':True,'scan_minutes':180,'post_minutes':180,
            'commentary':{'enabled':True,'mode':'intro','voice':'chatterbox:0.7:0.4'}}))
        clipping.action({'name':'set_cadence','minutes':360})
        clipping.action({'name':'set_commentary','mode':'off'})
        config=json.loads((tmp_path/'scheduler.json').read_text())
        assert config['scan_minutes']==config['post_minutes']==360
        assert config['commentary']=={'enabled':False,'mode':'intro','voice':'chatterbox:0.7:0.4'}
        clipping.action({'name':'set_commentary','mode':'full'})
        assert json.loads((tmp_path/'scheduler.json').read_text())['commentary']['mode']=='full'
        for bad in ({'name':'set_cadence','minutes':5},{'name':'set_cadence','minutes':'60'},{'name':'set_commentary','mode':'loud'}):
            with pytest.raises(ValueError):clipping.action(bad)


def test_run_now_needs_auto_on_and_no_held_upload(tmp_path):
    with patch.object(clipping,'CLIP_ROOT',tmp_path):
        (tmp_path/'scheduler.json').write_text(json.dumps({'enabled':False}))
        with pytest.raises(ValueError,match='Resume'):clipping.action({'name':'run_auto_now'})
        (tmp_path/'scheduler.json').write_text(json.dumps({'enabled':True}))
        (tmp_path/'work/scheduler').mkdir(parents=True)
        (tmp_path/'work/scheduler/state.json').write_text(json.dumps({'uploads':[{'status':'uncertain'}]}))
        with pytest.raises(ValueError,match='held'):clipping.action({'name':'run_auto_now'})
        (tmp_path/'work/scheduler/state.json').write_text(json.dumps({'uploads':[{'status':'uploaded'}]}))
        clipping.action({'name':'run_auto_now'})
        assert (tmp_path/'work/dashboard/run-now').exists()
        clipping.action({'name':'cancel_run_now'})
        assert not (tmp_path/'work/dashboard/run-now').exists()


def test_auto_history_uses_finished_titles_newest_first(tmp_path):
    folder=tmp_path/'run/highlight-finished';folder.mkdir(parents=True)
    (folder/'metadata.json').write_text(json.dumps({'title':'Own title','commentary':{'script':{'hook':'Wait, what?'}}}))
    state={'uploads':[{'status':'uploaded','video_id':'old','file':str(folder/'x.mp4'),'source':{'channel':'A'}},
                      {'status':'uploaded','video_id':'new','file':str(folder/'commentary-short.mp4'),'source':{'channel':'B'},
                       'facebook':{'status':'published','reel_id':'9'}}]}
    rows=clipping.auto_history(state)
    assert [r['video_id'] for r in rows]==['new','old']
    assert rows[0]['title']=='Own title' and rows[0]['intro']=='Wait, what?' and rows[0]['facebook']['reel_id']=='9'


def test_latest_clip_is_newest_file_from_any_source_with_honest_publish_state(tmp_path):
    import os
    work=tmp_path/'work'
    sched=work/'scheduler/20261119-205514-x/highlight-finished';sched.mkdir(parents=True)
    (sched/'finished-short.mp4').write_bytes(b'0');(sched/'commentary-short.mp4').write_bytes(b'0')
    (sched/'metadata.json').write_text(json.dumps({'title':'Scheduled one'}))
    live=work/'live/20261119-230001-y/clip-chat-spike-1-finished';live.mkdir(parents=True)
    (live/'finished-short.mp4').write_bytes(b'0');(live/'metadata.json').write_text(json.dumps({'title':'Live one'}))
    os.utime(sched/'commentary-short.mp4',(1000,1000));os.utime(sched/'finished-short.mp4',(900,900));os.utime(live/'finished-short.mp4',(2000,2000))
    state={'uploads':[{'status':'uploaded','video_id':'yt1','file':str(sched/'commentary-short.mp4'),'facebook':{'reel_id':'fb1'}}]}
    with patch.object(clipping,'CLIP_ROOT',tmp_path):
        newest=clipping.latest_clip(state,[])
        assert newest['title']=='Live one' and newest['origin']=='Live chat-spike monitor' and newest['published'] is None
        os.utime(live/'finished-short.mp4',(10,10))
        newest=clipping.latest_clip(state,[])
        assert newest['file'].endswith('commentary-short.mp4')
        assert newest['published']=={'status':'uploaded','video_id':'yt1','reel_id':'fb1'}


def test_clip_library_merges_sources_newest_first_with_publish_state(tmp_path):
    import os
    work=tmp_path/'work'
    auto=work/'scheduler/20261119-205514-x/highlight-finished';auto.mkdir(parents=True)
    (auto/'commentary-short.mp4').write_bytes(b'0');os.utime(auto/'commentary-short.mp4',(100,100))
    live=work/'live/20261119-230001-y/clip-1-finished';live.mkdir(parents=True)
    (live/'finished-short.mp4').write_bytes(b'0');os.utime(live/'finished-short.mp4',(200,200))
    runs=[{'run':'20261119-230001-y','creator':'Speed','worker':'live','clips':[{'file':'live/20261119-230001-y/clip-1-finished/finished-short.mp4','finished':True,'title':'Live one'}]},
          {'run':'20261119-205514-x','creator':'Auron','worker':'automatic','clips':[{'file':'scheduler/20261119-205514-x/highlight-finished/commentary-short.mp4','finished':True,'title':'Auto one'}]}]
    state={'uploads':[{'status':'uploaded','video_id':'yt9','file':str(auto/'commentary-short.mp4')}]}
    with patch.object(clipping,'CLIP_ROOT',tmp_path):
        rows=clipping.clip_library(runs,state,[])
    assert [r['title'] for r in rows]==['Live one','Auto one']
    assert rows[0]['origin']=='Live chat-spike monitor' and rows[0]['published'] is None
    assert rows[1]['origin']=='Automatic cycle' and rows[1]['published']['video_id']=='yt9'



def test_manual_module_resolves_siblings_and_retries_failed_loads(tmp_path, monkeypatch):
    import sys
    (tmp_path/'manual_jobs.py').write_text('from fixture_sibling import VALUE\ndef pending(root=None):\n    return VALUE\n')
    monkeypatch.setattr(clipping, 'CLIP_ROOT', tmp_path)
    monkeypatch.setattr(clipping, 'MANUAL', None)
    monkeypatch.setattr(sys, 'path', list(sys.path))
    try:
        with pytest.raises(ModuleNotFoundError):
            clipping.manual_module()
        assert clipping.MANUAL is None  # a half-loaded module is never cached
        (tmp_path/'fixture_sibling.py').write_text('VALUE = True\n')
        assert clipping.manual_module().pending() is True
    finally:
        sys.modules.pop('fixture_sibling', None)
