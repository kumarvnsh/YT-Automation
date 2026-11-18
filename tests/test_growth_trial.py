import copy
import json
from datetime import datetime,timedelta,timezone
from src.config import Config
from src import growth_trial as trial,pipeline
from src.editorial import brief
from src.script_generator import _build_prompt
from src.research import evidence_for_model
import pytest


def config():
    return Config({'production':{'enabled':True},'output':{'dir':'output'},'editorial':{'enabled':True,
        'growth_trial':{'enabled':True,'id':'trial','start_slot':129,'posts':42}},
        'ab_test':{'enabled':True,'start_slot':129,'posts':48},'tts':{'provider':'kokoro'}})


def selection():
    return {'editorial_selection':{'selected_index':0,'selection_reason':'Clear mechanism and supported payoff',
        'candidates':[{'premise':'A documented ingenious solution','opening_visual':'Diagram of the mechanism',
                       'payoff':'How the mechanism solved the problem','viewer_reason':'A familiar problem'},
                      {'premise':'Alternative one','rejection_reason':'No credible evidence'},
                      {'premise':'Alternative two','rejection_reason':'Weak visual explanation'}]}}


def test_finite_balanced_trial_rotates_each_lane_across_all_daily_slots():
    cfg=config();rows=[trial.assignment(cfg,i) for i in range(129,171)]
    for lane in trial.LANES:
        assert sum(r['series']['id']==lane['id'] for r in rows)==14
        assert {i%3 for i,r in enumerate(rows) if r['series']['id']==lane['id']}=={0,1,2}
    assert trial.assignment(cfg,128) is None and trial.assignment(cfg,171) is None
    assert {r['target_seconds'] for r in rows}=={35}
    assert all(r['primary_platform']=='youtube' and not r['follow_cta'] for r in rows)
    assert 'THREE distinct' in brief(rows[0],[])


def test_new_stages_freeze_other_tests_and_manual_topics_remain_explicit(tmp_path,monkeypatch):
    monkeypatch.setattr(pipeline,'base_dir',lambda:tmp_path)
    (tmp_path/'data').mkdir();(tmp_path/'data/published_index.json').write_text(json.dumps([{'editorial_slot':128}]))
    stage=pipeline._new_stage(config(),'short',no_upload=True,dry_run=True)
    state=pipeline.load_state(stage)
    assert state['growth_trial']['index']==0 and 'ab_test' not in state and 'creative_test' not in state
    assert state['production_config']['script']['shorts_target_seconds']==35
    assert state['production_config']['script']['title_style']=='statement'
    manual=pipeline._new_stage(config(),'short',no_upload=True,dry_run=True,topic='Chosen manually')
    assert 'growth_trial' not in pipeline.load_state(manual)


def test_candidate_gate_and_script_only_receive_selected_candidate():
    packet=selection();trial.validate_selection(packet)
    context=evidence_for_model(packet)
    assert context['editorial_selection']['selected']['premise']=='A documented ingenious solution'
    assert 'Alternative one' not in json.dumps(context)
    for broken in [{}, {'editorial_selection':{'candidates':[],'selected_index':0}}]:
        with pytest.raises(ValueError):trial.validate_selection(broken)
    placeholder=selection();placeholder['editorial_selection']['candidates'][1]['premise']='Candidate 2 (not researched)'
    with pytest.raises(ValueError,match='placeholders'):trial.validate_selection(placeholder)


def test_research_preserves_candidate_selection_in_saved_packet(monkeypatch):
    from src import research
    packet={**selection(),'claims':[],'sources':[]}
    monkeypatch.setattr(research,'_retrieve',lambda *args,**kwargs:(packet,[]))
    monkeypatch.setattr(research,'validate_research',lambda *args:[])
    monkeypatch.setattr(research,'repair_evidence',lambda cfg,result:result)
    result=research.research_topic(config(),'Trial story')
    assert result['status']=='supported'
    assert trial.validate_selection(result)['selected_index']==0


def test_research_gate_stops_before_rendering_on_missing_selection(tmp_path,monkeypatch):
    from src import research
    monkeypatch.setattr(research,'begin_step',lambda *args:[])
    monkeypatch.setattr(research,'research_topic',lambda *args:{'status':'supported','claims':[]})
    state={'growth_trial':{'id':'trial'},'overrides':{'topic':'Manual test'}}
    with pytest.raises(ValueError,match='three candidate'):pipeline._step_research(config(),tmp_path,state)
    assert (tmp_path/'research.json').exists()


def test_checkpoints_do_not_backfill_or_overwrite_and_platforms_are_separate(tmp_path):
    at=datetime(2026,11,19,tzinfo=timezone.utc)
    index=[{'video_id':'v','facebook_id':'f','growth_trial':{'id':'trial','lane':'decisions'},'published_at':at.isoformat()}]
    def source(hours,**metrics):return {'updated':(at+timedelta(hours=hours)).isoformat(),'videos':[{'id':'v',**metrics}]}
    trial.record(tmp_path,index,source(72,views=100,engaged_views=50),{},at+timedelta(hours=72))
    path=tmp_path/'data/private/growth-trial-observations.json'
    old=json.loads(path.read_text())
    assert set(old['videos']['v']['checkpoints'])=={'72h'}
    report=trial.record(tmp_path,index,source(73,views=999),{},at+timedelta(hours=73))
    assert report['lanes'][0]['youtube']['median_72h']==100
    assert report['lanes'][0]['facebook']['median_72h'] is None
    trial.record(tmp_path,index,source(168,views=300),{},at+timedelta(hours=172))
    assert '7d' not in json.loads(path.read_text())['videos']['v']['checkpoints'] # stale export


def test_trial_script_keeps_opening_visual_and_payoff_without_legacy_feedback(monkeypatch):
    import src.script_generator as sg
    cfg=config();cfg.raw['production']['editorial']=trial.assignment(cfg,129)
    cfg.raw['script']={'story_profile':'evergreen_concise','shorts_target_seconds':35}
    monkeypatch.setattr(sg,'recent_titles',lambda:[])
    monkeypatch.setattr(sg,'performance_examples',lambda:pytest.fail('Legacy blended feedback must not enter trial'))
    prompt=_build_prompt(cfg,'short','Selected supported premise')
    assert 'YouTube Shorts is the primary audience' in prompt and 'First-scene keywords' in prompt
    assert 'Facebook is the primary audience' not in prompt and "TODAY'S DATE" not in prompt


def test_selected_opening_visual_reaches_cinematic_scene_request():
    from types import SimpleNamespace as NS
    from unittest.mock import MagicMock,patch
    from src.claude_motion import _request_scenes
    response=NS(stop_reason='end_turn',content=[NS(type='text',text='<scene index="0"><html>cutaway</html></scene>')],
                usage=NS(input_tokens=10,output_tokens=20))
    client=MagicMock();client.beta.messages.stream.return_value.__enter__.return_value.get_final_message.return_value=response
    cfg=config();cfg.raw['production']['editorial']=trial.assignment(cfg,129)
    cfg.raw['production']['editorial_opening_visual']='Cutaway of the ice compartment and airflow'
    cfg.raw['video']={'claude_motion':{'style':'cinematic_2d'}}
    segment=NS(narration='Ice cooled the food',keywords=['icebox'],beat='hook')
    with patch('anthropic.Anthropic',return_value=client):
        _request_scenes(cfg,'Icebox',[segment],[(0,3)],540,960)
    prompt=client.beta.messages.stream.call_args.kwargs['messages'][0]['content']
    assert 'Cutaway of the ice compartment and airflow' in prompt
    assert 'YouTube Shorts world-history' in prompt and 'Do not start with a series card' in prompt
