from datetime import datetime, timedelta, timezone

from src import operator_agent as op

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)


def diff_for(path, added='x = 1', removed=None):
    lines = [f'diff --git a/{path} b/{path}', f'--- a/{path}', f'+++ b/{path}', '@@ -1 +1 @@']
    if removed:
        lines.append('-' + removed)
    lines.append('+' + added)
    return '\n'.join(lines) + '\n'


def test_small_code_fix_is_allowed():
    assert op.check_diff(diff_for('src/captions.py', 'return words[:limit]')) == []


def test_protected_files_are_rejected():
    for path in ('.github/workflows/publish.yml', 'scripts/publish_gate.py', 'src/quality_gate.py',
                 'requirements.txt', 'client_secret.json', 'src/operator_agent.py', 'token.json'):
        assert op.check_diff(diff_for(path)), path


def test_protected_settings_and_secrets_are_rejected():
    assert op.check_diff(diff_for('config.yaml', '  max_images: 40', '  max_images: 8'))
    assert op.check_diff(diff_for('config.yaml', '  privacy_status: public'))
    assert op.check_diff(diff_for('src/x.py', 'KEY = "sk-abcdefghijklmnopqrstuvwxyz"'))
    assert op.check_diff(diff_for('config.yaml', '  hook_style: "question"')) == []


def test_skipping_tests_and_oversized_diffs_are_rejected():
    assert op.check_diff(diff_for('tests/test_a.py', '@pytest.mark.skip'))
    big = ''.join(diff_for(f'src/f{i}.py') for i in range(op.MAX_FILES + 1))
    assert any('files' in p for p in op.check_diff(big))


def test_failed_workflows_use_latest_completed_run():
    runs = [{'workflowName': 'Publish', 'status': 'completed', 'conclusion': 'failure', 'createdAt': '2026-11-18T01:00:00Z', 'databaseId': 1},
            {'workflowName': 'Publish', 'status': 'completed', 'conclusion': 'success', 'createdAt': '2026-11-18T02:00:00Z', 'databaseId': 2},
            {'workflowName': 'Tests', 'status': 'completed', 'conclusion': 'failure', 'createdAt': '2026-11-18T03:00:00Z', 'databaseId': 3},
            {'workflowName': 'Tests', 'status': 'completed', 'conclusion': 'cancelled', 'createdAt': '2026-11-18T04:00:00Z', 'databaseId': 4}]
    issues = op.failed_workflows('histold', runs)
    assert [i['workflow'] for i in issues] == ['Tests'] and issues[0]['run_id'] == 3


def test_alerts_must_persist_before_code_changes(tmp_path):
    ledger = op.Ledger(tmp_path / 'l.json')
    alert = [{'id': 'service:dashboard', 'severity': 'error', 'title': 'Dashboard down'}]
    assert op.persistent_alerts(ledger, alert, NOW) == []
    assert len(op.persistent_alerts(ledger, alert, NOW + timedelta(hours=3))) == 1
    assert op.persistent_alerts(ledger, [], NOW + timedelta(hours=4)) == []
    assert ledger.data['alerts_seen'] == {}


def test_attempt_limits_and_escalation(tmp_path):
    ledger = op.Ledger(tmp_path / 'l.json')
    issue = {'id': 'abc', 'repo': 'histold', 'title': 't'}
    assert op.choose_issue(ledger, [issue], NOW)[0] == issue
    for _ in range(op.MAX_ATTEMPTS_PER_ISSUE):
        ledger.add(kind='fix_attempt', issue='abc', status='no_code_fix', at=NOW.isoformat())
    chosen, escalate = op.choose_issue(ledger, [issue], NOW)
    assert chosen is None and escalate == [issue]
    ledger.add(kind='escalation', issue='abc', at=NOW.isoformat())
    assert op.choose_issue(ledger, [issue], NOW) == (None, [])


def test_daily_cap(tmp_path):
    ledger = op.Ledger(tmp_path / 'l.json')
    for i in range(op.MAX_FIXES_PER_DAY):
        ledger.add(kind='fix_attempt', issue=str(i), status='no_code_fix', at=NOW.isoformat())
    assert op.choose_issue(ledger, [{'id': 'new'}], NOW) == (None, [])


def test_deployed_fix_reverts_when_problem_returns():
    entry = {'issue': 'abc', 'deployed_at': NOW.isoformat()}
    again = [{'id': 'abc', 'kind': 'workflow', 'created': (NOW + timedelta(hours=1)).isoformat()}]
    old = [{'id': 'abc', 'kind': 'workflow', 'created': (NOW - timedelta(hours=1)).isoformat()}]
    assert op.fix_regressed(entry, again, [], NOW + timedelta(hours=2))
    assert op.fix_regressed(entry, old, [], NOW + timedelta(hours=2)) is None
    ci_fail = [{'status': 'completed', 'conclusion': 'failure', 'workflowName': 'Tests'}]
    assert op.fix_regressed(entry, [], ci_fail, NOW)


def test_verdict_parsing():
    text = 'Fixed it.\n{"status": "fixed", "root_cause": "r", "summary": "s", "owner_action": ""}'
    assert op.parse_verdict(text)['status'] == 'fixed'
    assert op.parse_verdict('no json here')['status'] == 'no_code_fix'


def test_only_new_test_failures_block_a_deploy():
    out = 'FAILED tests/a.py::t1 - boom\nERROR tests/b.py::t2\n2 failed'
    assert op.failed_tests(out) == {'tests/a.py::t1', 'tests/b.py::t2'}
    assert op.new_failures({'tests/a.py::t1'}, {'tests/a.py::t1'}) == set()
    assert op.new_failures(set(), {'tests/a.py::t1'}) == {'tests/a.py::t1'}
