import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from src.bot_workspace import Workspace
from src import bot_findings,bot_decisions


def proposal():
    return {'key':'next-experiment','title':'Which experiment should we prepare?',
        'summary':'Choose a bounded test based on matched-age results.','evidence':'Two candidate directions have limited samples.',
        'success_metric':'Compare 72-hour YouTube views at the same publication age.',
        'options':[{'id':'a','label':'Test one topic','plan':'Prepare a limited topic experiment.','tradeoff':'Small sample'},
                   {'id':'b','label':'Collect more evidence','plan':'Wait for the existing cohort to reach 72 hours.','tradeoff':'Slower decision'}]}


def source(w,channel='histold',bot='jarvis'):
    identity=w.enqueue({'channel':channel,'bot':bot})
    w.update(identity,status='completed',result='A useful report with evidence.')
    return w.task(identity)


def decision(w,channel='histold'):
    task=source(w,channel);w.record_decisions(task,[proposal()])
    return w.decisions(channel)[0]


def test_model_options_are_bounded_data_not_executable_actions():
    p=proposal();p['options'][0]['command']='rm -rf anything';p['title']+='\x00'
    out=bot_decisions.validate([p,p,'junk'])
    assert len(out)==1 and 'command' not in out[0]['options'][0]
    assert '\x00' not in out[0]['title']
    assert not bot_decisions.validate([{**p,'options':42}])
    assert not bot_decisions.validate([{**p,'options':[p['options'][0]]}])
    _,parsed=bot_findings.parse('Report\nFINDINGS_JSON: '+json.dumps({'decisions':[p]}))
    assert parsed['decisions'][0]['key']=='next-experiment'


def test_choice_is_durable_channel_scoped_and_double_click_safe(tmp_path):
    w=Workspace(tmp_path);d=decision(w)
    with pytest.raises(ValueError):w.decide({'channel':'shotsnip','id':d['id'],'option':'a'})
    with pytest.raises(ValueError):w.decide({'channel':'histold','id':d['id'],'option':'arbitrary-command'})
    def choose(_):return w.decide({'action':'choose_decision','channel':'histold','id':d['id'],'option':'a'})
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(choose,range(2)))
    assert results[0]['followup']==results[1]['followup']
    assert len(w.snapshot('histold')['tasks'])==2
    assert not w.snapshot('shotsnip')['tasks']
    stored=Workspace(tmp_path).decisions('histold')[0]
    assert stored['status']=='chosen' and stored['selected']=='a'
    followup=w.task(stored['followup'])
    assert 'do not execute commands' in followup['instruction']
    assert 'next-experiment' in w.prompt(followup)


def test_same_question_is_not_duplicated_by_other_reports_or_bots(tmp_path):
    w=Workspace(tmp_path);d=decision(w)
    w.record_decisions(source(w,bot='histold'),[proposal()])
    assert len(w.decisions('histold'))==1
    w.decide({'action':'dismiss_decision','channel':'histold','id':d['id']})
    w.record_decisions(source(w),[proposal()])
    assert len(w.decisions('histold'))==1 and w.decisions('histold')[0]['status']=='dismissed'


def test_revisit_stops_only_unfinished_followup_and_preserves_report(tmp_path):
    w=Workspace(tmp_path);d=decision(w)
    result=w.decide({'channel':'histold','id':d['id'],'option':'a'})
    w.decide({'action':'reopen_decision','channel':'histold','id':d['id']})
    assert w.task(result['followup'])['status']=='cancelled'
    assert w.task(d['source_task'])['status']=='completed'
    assert w.decisions('histold')[0]['status']=='pending'
    result=w.decide({'channel':'histold','id':d['id'],'option':'b'})
    assert w.task(result['followup'])['status']=='queued'


def test_full_queue_does_not_lose_owner_choice(tmp_path):
    w=Workspace(tmp_path);d=decision(w)
    for _ in range(20):w.enqueue({'channel':'shotsnip','bot':'jarvis'})
    with pytest.raises(ValueError,match='queue is full'):w.decide({'channel':'histold','id':d['id'],'option':'a'})
    assert w.decisions('histold')[0]['status']=='pending'


def test_compact_snapshot_and_report_detail_do_not_leak_private_evidence(tmp_path):
    w=Workspace(tmp_path);t=source(w)
    w.update(t['id'],result='x'*10000)
    with w.db() as db:db.execute('UPDATE tasks SET evidence=?,remote_bot=? WHERE id=?',(json.dumps({'checked_at':123,'data':{'secret':'private'}}),'remote-private',t['id']))
    snap=w.snapshot('histold',include_reports=False)
    assert 'result' not in snap['tasks'][0] and len(snap['tasks'][0]['result_excerpt'])==350
    detail=w.report('histold',t['id'])
    assert len(detail['result'])==10000 and detail['evidence_checked_at']==123
    assert 'private' not in json.dumps(detail)
    with pytest.raises(ValueError):w.report('shotsnip',t['id'])


def test_old_report_can_request_real_options_without_applying_settings(tmp_path):
    w=Workspace(tmp_path);t=source(w)
    followup=w.report_options({'channel':'histold','id':t['id']})
    assert w.task(followup)['status']=='queued'
    assert 'structured decisions array' in w.task(followup)['instruction']
    with pytest.raises(ValueError):w.report_options({'channel':'shotsnip','id':t['id']})


def test_owner_can_request_guarded_operator_repair_from_a_real_open_failure(tmp_path):
    calls=[];w=Workspace(tmp_path/'bot-workspace',repair=lambda:calls.append('repair'))
    t=source(w,'shotsnip','operations')
    findings=bot_findings.validate({'active_failures':[{'key':'capture-stall','title':'Capture stalls','evidence':'Repeated current capture timeout'}]})
    w.share(t,findings)
    assert not calls
    with pytest.raises(ValueError):w.approve_problem({'channel':'histold','key':'capture-stall'})
    w.approve_problem({'channel':'shotsnip','key':'capture-stall'})
    assert calls==['repair']
    assert bot_findings.persistent_failures(w.directory,'shotsnip')[0]['key']=='capture-stall'

def test_pending_decision_refreshes_evidence_and_rejects_stale_click(tmp_path):
    w=Workspace(tmp_path);d=decision(w)
    updated=proposal();updated['evidence']='A newer snapshot changes the evidence behind this decision.'
    w.record_decisions(source(w),[updated])
    with pytest.raises(ValueError,match='changed'):w.decide({'channel':'histold','id':d['id'],'option':'a','version':d['updated']})
    current=w.decisions('histold')[0]
    assert current['proposal']['evidence']==updated['evidence']
    assert w.decide({'channel':'histold','id':d['id'],'option':'a','version':current['updated']})['followup']


def test_owner_verification_is_scoped_and_does_not_stack(tmp_path):
    w=Workspace(tmp_path/'bot-workspace');t=source(w,'shotsnip','operations')
    w.share(t,bot_findings.validate({'active_failures':[{'key':'capture','title':'Capture problem'}]}))
    with pytest.raises(ValueError):w.verify_problem({'channel':'histold','key':'capture'})
    def check(_):return w.verify_problem({'channel':'shotsnip','key':'capture'})['id']
    with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(check,range(2)))
    assert ids[0]==ids[1]
    assert 'every currently open problem' in w.task(ids[0])['instruction']

def test_existing_report_proposal_becomes_immediate_options_without_model_work(tmp_path):
    w=Workspace(tmp_path);t=source(w)
    w.update(t['id'],result='## Findings\nEvidence is limited to comparable recent uploads.\n## Next experiment\nTest two opening styles on eight clips while keeping every other editing choice unchanged.\n## Success metric\nCompare median 24-hour views and measured group sizes.\n## Limitations\nThis is a small pilot.')
    result=w.report_action_options({'channel':'histold','id':t['id']})
    assert result['mode']=='decision'
    d=w.decisions('histold')[0]
    assert d['proposal']['options'][0]['plan'].startswith('Test two opening styles')
    assert d['proposal']['options'][1]['id']=='keep-current'
    assert len(w.snapshot('histold')['tasks'])==1
    assert w.report_action_options({'channel':'histold','id':t['id']})['decision_id']==result['decision_id']
    with pytest.raises(ValueError):w.report_action_options({'channel':'shotsnip','id':t['id']})
