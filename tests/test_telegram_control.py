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
        self.sent, self.answers, self.edits, self.cleared, self.photos = [], [], [], [], []

    def send(self, chat_id, text, buttons=None, html=False, keyboard=None):
        self.sent.append((chat_id, text, buttons))

    def send_photo(self, chat_id, path, caption, buttons=None, html=False):
        self.photos.append((path, caption, buttons))

    def clear_buttons(self, chat_id, message_id):
        self.cleared.append(message_id)

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
    assert saved['owner_id'] == OWNER and 'pair_code' not in saved and 'Connected' in c.telegram.sent[0][1]


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
    assert c.telegram.sent[-1][1].startswith('✅ <b>Done.</b>')
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
    assert c.telegram.sent[-1][1].startswith('✅ <b>Done.</b>')


def test_ipv4_only_is_scoped():
    import socket
    import urllib3.util.connection as connection
    before = connection.allowed_gai_family
    with tc.ipv4_only():
        assert connection.allowed_gai_family() == socket.AF_INET
    assert connection.allowed_gai_family is before


def test_messages_use_the_plain_explanation(tmp_path):
    c = controller(tmp_path)
    bf.record(tmp_path / 'workspace', 'shotsnip', 'operations', 't',
              {'active_failures': [{'key': 'lock', 'title': 'PermissionError on segments.csv',
                                    'plain': 'Recording of one streamer keeps stopping, so fewer clips get made.', 'evidence': ''}],
               'suspect_metrics': []})
    c.handle(message('/status'))
    c.handle(message('/problems'))
    status, problem = c.telegram.sent[0][1], c.telegram.sent[1][1]
    assert 'Recording of one streamer keeps stopping' in status and 'PermissionError' not in status
    assert 'problem</b>' in problem and 'Recording of one streamer' in problem and 'Spotted once' in problem


def test_run_returns_while_a_started_program_keeps_running(tmp_path):
    # 2026-11-22: start_dashboard.ps1 left the dashboard holding the output pipe and the bot hung for good.
    import sys
    child = tmp_path / 'child.py'
    child.write_text('import subprocess, sys\nsubprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])\nprint("started")\n')
    began = time.monotonic()
    code, out = tc.run([sys.executable, str(child)], timeout=20)
    assert (code, out) == (0, 'started')
    assert time.monotonic() - began < 15


def test_fact_post_choices_share_one_row_without_dismiss(tmp_path):
    rows = tc.offer_rows(tc.Approvals(tmp_path), [
        ('fact_post', {'id': '20261123_013913_38b899', 'choice': 'approve'}, '✅ Post it'),
        ('fact_post', {'id': '20261123_013913_38b899', 'choice': 'skip'}, '⏭ Skip'),
        ('operator_run', {}, 'Run')])
    assert [[label for label, _ in row] for row in rows] == [['✅ Post it', '⏭ Skip'], ['Run', 'Dismiss']]


def test_menu_keyboard_taps_run_their_commands_and_used_buttons_disappear(tmp_path, monkeypatch):
    from src import telegram_views as tv
    c = controller(tmp_path)
    monkeypatch.setattr(tv, 'today', lambda: 'TODAY')
    monkeypatch.setattr(tv, 'stats', lambda: 'STATS')
    for label, reply in (('📅 Today', 'TODAY'), ('📈 Stats', 'STATS'), ('⚙️ More', '/review')):
        c.handle(message(label))
        assert reply in c.telegram.sent[-1][1]
    c.handle(message('/operator'))
    c.handle(press(buttons(c)[0]))
    assert c.telegram.cleared == [5]  # the tapped message lost its buttons


def test_posts_sends_waiting_cards_with_post_and_skip(tmp_path, monkeypatch):
    from src import telegram_views as tv
    entry = {'id': '20261123_013913_38b899', 'status': 'awaiting_approval', 'headline': 'Flags <cut> & kept',
             'fact': 'A fact.', 'sources': [{'publisher': 'NPS'}], 'card': str(tmp_path / 'card.png')}
    monkeypatch.setattr(tv, '_fact_posts', lambda: [entry])
    c = controller(tmp_path)
    c.handle(message('/posts'))
    path, caption, rows = c.telegram.photos[-1]
    assert [label for label, _ in rows[0]] == ['✅ Post it', '⏭ Skip'] and len(rows) == 1
    assert 'Flags &lt;cut&gt; &amp; kept' in caption  # data is escaped for Telegram HTML


def test_today_and_stats_read_both_channels(tmp_path, monkeypatch):
    from datetime import timedelta
    from src import telegram_views as tv
    now = datetime(2026, 11, 23, 12, 0, tzinfo=tv.IST)
    data, clips = tmp_path / 'data', tmp_path / 'clips'
    (data).mkdir(); (clips / 'work' / 'dashboard').mkdir(parents=True); (clips / 'work' / 'scheduler').mkdir(parents=True)
    at = (now - timedelta(hours=2)).isoformat()
    (data / 'published_index.json').write_text(json.dumps([{'video_id': 'h1', 'title': 'Wolves <return>', 'published_at': at, 'channel': 'histold'}]))
    (data / 'analytics.json').write_text(json.dumps({'channels': {'histold': {'videos': [{'id': 'h1', 'views': 1500}]}}}))
    (clips / 'work' / 'scheduler' / 'state.json').write_text(json.dumps({'uploads': [
        {'status': 'uploaded', 'video_id': 's1', 'finished_at': (now - timedelta(days=2)).isoformat(), 'source': {'channel': 'Kai'}}]}))
    (clips / 'work' / 'dashboard' / 'analytics.json').write_text(json.dumps({'subscribers': 16, 'total_views': 28066,
                                                                            'videos': [{'id': 's1', 'title': 'Kai jumps', 'views': 40}]}))
    monkeypatch.setattr(tv, 'HISTOLD_DATA', data)
    monkeypatch.setattr(tv, 'SHOTSNIP', clips)
    monkeypatch.setattr(tv, '_fact_posts', lambda: [])
    today = tv.today(now)
    assert '1 upload' in today and 'Wolves &lt;return&gt;' in today and '👁 1.5k' in today
    assert 'Nothing yet today. Last upload 21 Nov' in today
    stats = tv.stats(now)
    assert '👥 16 subs' in stats and '👁 28.1k total' in stats and 'Kai jumps' in stats
