import json
import threading
import time
from datetime import datetime, timezone

import pytest

from src import bot_findings as bf
from src import telegram_control as tc

OWNER = 111222333
TOKEN = '123456789:' + 'A' * 35


class FakeTelegram:
    def __init__(self):
        self.sent, self.answers, self.edits = [], [], []

    def send(self, chat_id, text, buttons=None):
        self.sent.append((chat_id, text, buttons))

    def answer(self, callback_id, text=''):
        self.answers.append(text)

    def edit(self, chat_id, message_id, text):
        self.edits.append(text)


def controller(tmp_path, owner=OWNER, **config):
    paths = {k: tmp_path / k for k in ('control', 'admin', 'operator', 'workspace')}
    cfg = {'token': TOKEN, **({'owner_id': owner} if owner else {}), **config}
    (tmp_path / 'telegram.json').write_text(json.dumps(cfg))
    c = tc.Controller(FakeTelegram(), cfg, config_path=tmp_path / 'telegram.json', control=paths['control'],
                      admin=paths['admin'], operator=paths['operator'], workspace=paths['workspace'])
    c.ran = []
    c.run = lambda cmd, timeout=180: c.ran.append(cmd) or (0, 'ok')
    c.sleep = lambda s: None
    return c


def message(text, sender=OWNER, chat_type='private'):
    return {'update_id': 1, 'message': {'text': text, 'from': {'id': sender}, 'chat': {'id': sender, 'type': chat_type}}}


def press(data, sender=OWNER):
    return {'update_id': 2, 'callback_query': {'id': 'cb', 'data': data, 'from': {'id': sender},
                                               'message': {'message_id': 5, 'chat': {'id': sender}, 'text': 'menu'}}}


def buttons(c):
    return [d for _, _, rows in c.telegram.sent if rows for row in rows for _, d in row]


def test_pairing_needs_the_one_time_code(tmp_path):
    c = controller(tmp_path, owner=None, pair_code='424242', pair_expires=time.time() + 600)
    c.handle(message('/pair 000000', sender=5))
    c.handle(message('/status', sender=5))
    assert not c.telegram.sent and 'owner_id' not in c.config
    c.handle(message('/pair 424242', sender=OWNER))
    saved = json.loads((tmp_path / 'telegram.json').read_text())
    assert saved['owner_id'] == OWNER and 'pair_code' not in saved and 'Paired' in c.telegram.sent[0][1]


def test_expired_pairing_code_is_ignored(tmp_path):
    c = controller(tmp_path, owner=None, pair_code='424242', pair_expires=time.time() - 1)
    c.handle(message('/pair 424242'))
    assert 'owner_id' not in c.config


def test_strangers_and_groups_are_ignored(tmp_path):
    c = controller(tmp_path)
    c.handle(message('/operator', sender=999))
    c.handle(message('/operator', chat_type='group'))
    assert not c.telegram.sent
    c.handle(message('/operator'))
    data = buttons(c)[0]
    c.handle(press(data, sender=999))          # someone else pressing the owner's button
    assert not c.ran and not c.telegram.answers


def test_buttons_run_fixed_actions_once(tmp_path):
    c = controller(tmp_path)
    c.handle(message('/operator'))
    run_button = buttons(c)[0]
    c.handle(press(run_button))
    assert c.ran == [['schtasks', '/Run', '/TN', 'Histold Operator']]
    assert c.telegram.sent[-1][1].startswith('Done:')
    c.handle(press(run_button))               # replay
    assert len(c.ran) == 1 and c.telegram.answers[-1] == 'This button was already used'
    c.handle(press('a:' + 'f' * 16 + ':y'))   # forged id
    assert len(c.ran) == 1 and c.telegram.answers[-1] == 'Unknown request'


def test_pause_blocks_operator_and_resume_restores(tmp_path):
    c = controller(tmp_path)
    assert c.execute('operator_pause', {})[0] and (tmp_path / 'operator' / 'PAUSE').exists()
    ok, text = c.execute('operator_run', {})
    assert not ok and 'paused' in text and not c.ran
    c.execute('operator_resume', {})
    assert not (tmp_path / 'operator' / 'PAUSE').exists()


def test_fix_now_approves_the_open_problem_for_the_operator(tmp_path):
    c = controller(tmp_path)
    now = datetime.now(timezone.utc)
    bf.record(tmp_path / 'workspace', 'shotsnip', 'operations', 't',
              {'active_failures': [{'key': 'lock', 'title': 'Mythpat lock', 'evidence': ''}], 'suspect_metrics': []}, now)
    assert bf.persistent_failures(tmp_path / 'workspace', 'shotsnip', now) == []
    c.handle(message('/problems'))
    c.handle(press(buttons(c)[0]))
    assert [f['key'] for f in bf.persistent_failures(tmp_path / 'workspace', 'shotsnip')] == ['lock']
    assert c.ran == [['schtasks', '/Run', '/TN', 'Histold Operator']]
    # A later report keeps the approval.
    bf.record(tmp_path / 'workspace', 'shotsnip', 'operations', 't2',
              {'active_failures': [{'key': 'lock', 'title': 'Mythpat lock', 'evidence': ''}], 'suspect_metrics': []})
    assert bf.persistent_failures(tmp_path / 'workspace', 'shotsnip')
    with pytest.raises(ValueError):
        c.execute('fix_problem', {'channel': 'shotsnip', 'key': 'gone'})


def test_only_allowlisted_actions_and_arguments(tmp_path):
    a = tc.Approvals(tmp_path)
    for action, args in (('shell', {}), ('admin', {'name': 'format-c'}), ('review', {'channel': 'x; rm'}),
                         ('fix_problem', {'channel': 'shotsnip', 'key': 'a', 'extra': 1})):
        with pytest.raises(ValueError):
            a.create(action, args, 'x')
    identity = a.create('operator_run', {}, 'x', ttl=-1)
    with pytest.raises(ValueError, match='expired'):
        a.decide(identity, True)


def test_admin_request_round_trip_and_missing_runner(tmp_path):
    c = controller(tmp_path)
    assert not c.admin_request('ping')[0]                   # not installed
    (tmp_path / 'admin' / 'requests').mkdir(parents=True)
    (tmp_path / 'admin' / 'results').mkdir()
    (tmp_path / 'admin' / 'runner.ps1').write_text('#')

    def fake_runner():
        for _ in range(200):
            files = list((tmp_path / 'admin' / 'requests').glob('*.json'))
            if files:
                request = json.loads(files[0].read_text())
                assert set(request) == {'id', 'action', 'created'} and request['action'] == 'ping'
                (tmp_path / 'admin' / 'results' / files[0].name).write_text(json.dumps({'status': 'ok', 'output': 'elevated: True'}))
                return
            time.sleep(.01)
    thread = threading.Thread(target=fake_runner)
    thread.start()
    c.sleep = lambda s: time.sleep(.01)
    assert c.admin_request('ping') == (True, 'elevated: True')
    thread.join()
    with pytest.raises(ValueError):
        c.admin_request('del-everything')


def test_notify_without_pairing_and_token_never_in_errors(tmp_path):
    assert tc.notify('hi', config_path=tmp_path / 'missing.json') == 'not_configured'

    class Session:
        def post(self, url, json=None, timeout=None):
            class R:
                status_code = 401
                def json(self):
                    return {'ok': False, 'description': 'Unauthorized'}
            return R()
    with pytest.raises(RuntimeError) as err:
        tc.Telegram(TOKEN, Session()).send(1, 'x')
    assert TOKEN not in str(err.value)
    with pytest.raises(ValueError):
        tc.Telegram('not-a-token')


def test_late_button_answer_still_runs_the_action(tmp_path):
    c = controller(tmp_path)
    c.handle(message('/operator'))
    def too_late(callback_id, text=''):
        raise RuntimeError('Telegram answerCallbackQuery failed: query is too old')
    c.telegram.answer = too_late
    c.handle(press(buttons(c)[0]))
    assert c.ran == [['schtasks', '/Run', '/TN', 'Histold Operator']]
    assert c.telegram.sent[-1][1].startswith('Done:')


def test_ipv4_only_is_scoped():
    import socket
    import urllib3.util.connection as connection
    before = connection.allowed_gai_family
    with tc.ipv4_only():
        assert connection.allowed_gai_family() == socket.AF_INET
    assert connection.allowed_gai_family is before
