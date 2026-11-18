import json
import time
from unittest.mock import patch
import pytest
from src.bot_workspace import Workspace


def test_channel_scope_persistence_and_notes(tmp_path):
    w=Workspace(tmp_path,evidence=lambda channel:{'channel':channel,'views':12})
    with pytest.raises(ValueError):w.enqueue({'bot':'histold','channel':'shotsnip'})
    identity=w.enqueue({'bot':'shotsnip','channel':'shotsnip','instruction':'Compare creators'})
    w.memory({'bot':'shotsnip','channel':'shotsnip','text':'Use matched upload ages'})
    task=w.claim();assert task['id']==identity
    assert 'Use matched upload ages' in w.prompt(task)
    assert Workspace(tmp_path).task(identity)['status']=='running'
    assert not w.snapshot('histold')['tasks']
    assert w.snapshot('shotsnip')['tasks'][0]['evidence_checked_at']
    with pytest.raises(ValueError):w.control({'id':identity,'channel':'histold','action':'cancel'})


def test_disconnected_superbot_does_not_block_native_jobs(tmp_path):
    w=Workspace(tmp_path)
    remote=w.enqueue({'bot':'jarvis','channel':'histold','engine':'superbot'})
    native=w.enqueue({'bot':'jarvis','channel':'histold','engine':'analyst'})
    assert w.claim()['id']==native
    assert w.task(remote)['status']=='queued'
    assert not w.runtime()['ready']


def test_cancellation_retry_and_restrictions(tmp_path):
    w=Workspace(tmp_path)
    identity=w.enqueue({'bot':'jarvis','channel':'histold'})
    w.control({'id':identity,'channel':'histold','action':'cancel'})
    assert w.task(identity)['status']=='cancelled'
    with pytest.raises(ValueError):w.control({'id':identity,'channel':'histold','action':'retry'})
    with pytest.raises(ValueError):w.routine({'bot':'jarvis','channel':'histold','interval':1})
    with pytest.raises(ValueError):w.enqueue({'bot':'jarvis','channel':'histold','engine':'shell'})


def test_routines_do_not_stack_unfinished_assignments(tmp_path):
    w=Workspace(tmp_path)
    w.routine({'bot':'jarvis','channel':'histold','interval':3600})
    for _ in range(2):
        with w.db() as db:db.execute('UPDATE routines SET next_at=0')
        w.scheduled()
    assert len(w.snapshot('histold')['tasks'])==1


def test_runtime_requires_loopback_and_reviewed_adapter(tmp_path):
    w=Workspace(tmp_path);config=tmp_path/'superbot.json'
    config.write_text(json.dumps({'base_url':'http://example.com','password':'secret'}))
    with pytest.raises(ValueError):w.config()
    config.write_text(json.dumps({'base_url':'http://127.0.0.1:3007','password':'secret'}))
    with pytest.raises(RuntimeError):w.remote('GET','/api/state')
    assert 'secret' not in json.dumps(w.snapshot('histold'))


def test_superbot_submission_and_report_are_reconciled_by_message_id(tmp_path):
    w=Workspace(tmp_path);identity=w.enqueue({'bot':'jarvis','channel':'histold','engine':'superbot'});task=w.task(identity)
    calls=[]
    def remote(method,path,payload=None):
        calls.append((method,path,payload))
        if path=='/api/bots':return {'id':'bridgebot'}
        if method=='POST':return {'id':10}
        return [{'id':10,'role':'user','status':'done'}, {'id':12,'role':'bot','text':'Second'}, {'id':11,'role':'bot','text':'First'}]
    w.remote=remote
    assert w.run_remote(task,'Evidence and assignment')=='First\n\nSecond'
    assert w.task(identity)['remote_message']==10
    assert len([c for c in calls if c[0]=='POST' and c[1].endswith('/messages')])==1


def test_native_worker_finishes_advisory_report_without_actions(tmp_path):
    w=Workspace(tmp_path,evidence=lambda channel:{'views':4},analyst=lambda prompt:'Findings: gather more observations. No actions applied.')
    identity=w.enqueue({'bot':'jarvis','channel':'histold'})
    w.start()
    deadline=time.monotonic()+3
    while time.monotonic()<deadline and w.task(identity)['status']!='completed':time.sleep(.02)
    task=w.task(identity)
    assert task['status']=='completed'
    assert 'No actions applied' in task['result']
