import json
import time
from datetime import datetime, timedelta, timezone

from src import bot_findings as bf
from src import operator_agent as op
from src.bot_workspace import Workspace

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)


def line(data):
    return bf.MARKER + ' ' + json.dumps(data)


def test_parse_strips_marker_keeps_provider_line_and_sanitizes():
    report = '# Report\nFindings here\n' + line({
        'active_failures': [{'key': 'Mythpat Segments!', 'title': 'Mythpat\x07 capture\nfails', 'evidence': 'x' * 900},
                            {'key': 'mythpat-segments', 'title': 'duplicate'}, 'junk'],
        'suspect_metrics': [{'video_id': 'abcDEF123_-', 'platform': 'FB', 'reason': 'near zero'},
                            {'video_id': 'bad id;rm', 'platform': 'fb'}, {'video_id': 'abcDEF123_-', 'platform': 'tiktok'}],
    }) + '\n\nAnalyst provider: claude'
    text, findings = bf.parse(report)
    assert bf.MARKER not in text and text.endswith('Analyst provider: claude') and 'Findings here' in text
    assert findings['active_failures'] == [{'key': 'mythpat-segments', 'title': 'Mythpat capture fails', 'evidence': 'x' * 400}]
    assert findings['suspect_metrics'] == [{'video_id': 'abcDEF123_-', 'platform': 'fb', 'reason': 'near zero'}]


def test_parse_without_or_with_broken_block():
    assert bf.parse('plain report') == ('plain report', None)
    assert bf.parse('body\n' + bf.MARKER + ' {not json') == ('body', None)


def test_record_tracks_streaks_and_resolution(tmp_path):
    fail = {'active_failures': [{'key': 'lock', 'title': 'Lock', 'evidence': ''}], 'suspect_metrics': []}
    assert [f['key'] for f in bf.record(tmp_path, 'shotsnip', 'operations', 't1', fail, NOW)] == ['lock']
    assert bf.persistent_failures(tmp_path, 'shotsnip', NOW) == []          # one report is not enough
    assert bf.record(tmp_path, 'shotsnip', 'operations', 't2', fail, NOW + timedelta(hours=1)) == []
    assert [f['streak'] for f in bf.persistent_failures(tmp_path, 'shotsnip', NOW + timedelta(hours=1))] == [2]
    assert bf.persistent_failures(tmp_path, 'shotsnip', NOW + timedelta(hours=5)) == []  # stale report
    # Other bots never change active failures; an Operations report without it resolves it.
    bf.record(tmp_path, 'shotsnip', 'jarvis', 't3', {'active_failures': [], 'suspect_metrics': []}, NOW)
    assert bf.persistent_failures(tmp_path, 'shotsnip', NOW + timedelta(hours=1))
    bf.record(tmp_path, 'shotsnip', 'operations', 't4', {'active_failures': [], 'suspect_metrics': []}, NOW + timedelta(hours=2))
    assert bf.persistent_failures(tmp_path, 'shotsnip', NOW + timedelta(hours=2)) == []


def test_masks_only_confirmed_anomalies(tmp_path):
    suspects = [{'video_id': f'video{i}0', 'platform': 'fb', 'reason': 'r'} for i in range(4)]
    bf.record(tmp_path, 'histold', 'histold', 't', {'active_failures': [], 'suspect_metrics': suspects}, NOW)
    index = [{'video_id': f'video{i}0', 'facebook_id': f'f{i}', 'instagram_id': f'i{i}'} for i in range(4)]
    best = {'yt': {'video00': {'views': 300}, 'video10': {'views': 20}, 'video30': {'views': 300}},
            'ig': {'i2': {'views': 200}}, 'fb': {'f0': {'views': 1}, 'f1': {'views': 2}, 'f2': {'views': 150}, 'f3': {'views': 40}}}
    out, masked = bf.mask_suspect_metrics(tmp_path, 'histold', index, best, NOW)
    # video0: 1 FB view vs 300 YouTube -> masked. video1: no real reach elsewhere. video2: FB is real. video3: 40 is not near zero.
    assert [m['video_id'] for m in masked] == ['video00']
    assert 'f0' not in out['fb'] and {'f1', 'f2', 'f3'} <= set(out['fb']) and 'f0' in best['fb']
    assert bf.mask_suspect_metrics(tmp_path, 'histold', index, best, NOW + bf.SUSPECT_TTL)[1] == []


def test_workspace_shares_findings_and_alerts_once_per_new_failure(tmp_path):
    sent = []
    report = 'Report\n' + line({'active_failures': [{'key': 'lock', 'title': 'Mythpat lock', 'evidence': 'PermissionError'}],
                                'suspect_metrics': []})
    w = Workspace(tmp_path, analyst=lambda prompt, **kw: report, notify=lambda text, key: sent.append((text, key)) or 'accepted')
    probe = Workspace(tmp_path / 'probe')
    assert bf.MARKER in probe.prompt(probe.task(probe.enqueue({'bot': 'operations', 'channel': 'shotsnip'})))
    w.start()
    for expected in ('Owner alerted', 'Findings shared'):  # first report alerts; the repeat does not
        identity = w.enqueue({'bot': 'operations', 'channel': 'shotsnip'})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not w.task(identity)['stage'].startswith(expected):
            time.sleep(.02)
        assert w.task(identity)['stage'].startswith(expected)
    assert len(sent) == 1 and 'Mythpat lock' in sent[0][0] and sent[0][1].startswith('bot-ops:')
    assert bf.MARKER not in w.task(identity)['result']
    assert bf.load(tmp_path)['channels']['shotsnip']['active_failures']['lock']['streak'] >= 2
    assert 'lock (Mythpat lock; Operator:' in w.prompt(w.task(w.enqueue({'bot': 'operations', 'channel': 'shotsnip'})))


def test_operator_turns_findings_into_guarded_issues():
    finding = {'key': 'lock', 'title': 'Mythpat lock', 'evidence': 'e', 'streak': 2,
               'first_seen': NOW.isoformat(), 'last_seen': NOW.isoformat()}
    [issue] = op.bot_finding_issues({'shotsnip': [finding], 'histold': []})
    assert issue['repo'] == 'shotsnip' and issue['kind'] == 'bot_finding' and issue['title'].endswith('Mythpat lock')
    entry = {'issue': issue['id'], 'deployed_at': (NOW - timedelta(hours=1)).isoformat()}
    assert op.fix_regressed(entry, [issue], [], NOW) is None                 # report predates deploy + 3h
    later = {**issue, 'finding': {**finding, 'last_seen': (NOW + timedelta(hours=3)).isoformat()}}
    assert 'still reports' in op.fix_regressed(entry, [later], [], NOW + timedelta(hours=3))
    assert 'src/bot_findings.py' in op.PROTECTED_FILES


def test_fix_status_follows_the_operator_ledger(tmp_path):
    work, operator = tmp_path / 'bot-workspace', tmp_path / 'operator'
    operator.mkdir()
    item = {'key': 'lock', 'title': 'Lock', 'streak': 1}
    assert bf.issue_id('shotsnip', 'lock') == op.fingerprint('shotsnip', 'bot', 'lock')
    assert bf.fix_status(operator, 'shotsnip', item)['state'] == 'confirming'
    assert bf.fix_status(operator, 'shotsnip', {**item, 'streak': 2})['state'] == 'queued'
    issue = bf.issue_id('shotsnip', 'lock')
    ledger = {'entries': [{'kind': 'fix_attempt', 'issue': issue, 'status': 'no_code_fix', 'owner_action': 'Close the open CSV',
                           'at': NOW.isoformat()}]}
    (operator / 'ledger.json').write_text(json.dumps(ledger))
    status = bf.fix_status(operator, 'shotsnip', item)
    assert status['state'] == 'no_code_fix' and status['detail'] == 'Close the open CSV'
    ledger['entries'].append({'kind': 'escalation', 'issue': issue})
    (operator / 'ledger.json').write_text(json.dumps(ledger))
    assert bf.fix_status(operator, 'shotsnip', item)['state'] == 'escalated'


def test_confirmed_problem_starts_operator_once_and_reaches_snapshot(tmp_path):
    started = []
    report = 'Report\n' + line({'active_failures': [{'key': 'lock', 'title': 'Mythpat lock', 'evidence': 'e'}], 'suspect_metrics': []})
    work = tmp_path / 'bot-workspace'
    w = Workspace(work, analyst=lambda prompt, **kw: report, repair=lambda: started.append(1))
    for expected in ('Findings shared', 'Operator started'):  # first report only confirms; the second triggers
        task = w.task(w.enqueue({'bot': 'operations', 'channel': 'shotsnip'}))
        w.claim()
        text, findings = bf.parse(w.run_assignment(task, 'Evidence'))
        w.update(task['id'], status='completed', result=text)
        w.share(task, findings)
        assert any(e['note'].startswith(expected) for e in w.snapshot('shotsnip')['tasks'][0]['events'])
    w.share(task, findings)   # throttled: no second start within REPAIR_GAP
    assert started == [1]
    [problem] = w.snapshot('shotsnip')['problems']
    assert problem['fix']['state'] == 'queued' and 'Operator: Confirmed' in w.prompt(task)
