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
