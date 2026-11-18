#!/usr/bin/env python3
"""Operator tick: find production problems in both channels, fix them in code with Claude Code,
test, deploy, watch and auto-revert; run creative split tests (about every 3 days) for Histold.

Safety rules live in src/operator_agent.py. Create D:\\histold\\operator\\PAUSE to stop everything.
Usage: python scripts/operator_tick.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
from src import operator_agent as op  # noqa: E402
from src.whatsapp_updates import send_update  # noqa: E402
from src import bot_findings  # noqa: E402
from src import telegram_control  # noqa: E402

HOME = Path(os.environ.get('HISTOLD_HOME', r'D:\histold'))
STATE = HOME / 'operator'
DATA_ROOT = HOME / 'YT-Automation'
REPOS = {
    'histold': {'slug': 'kumarvnsh/YT-Automation', 'label': 'Histold (history Shorts)',
                'venv': HOME / 'ci-tests'},  # the test env (pytest + deps); the runtime .venv has no pytest
    'shotsnip': {'slug': 'kumarvnsh/YT-Cliping-Automation', 'label': 'Shotsnip (gaming clip Shorts)',
                 'venv': Path(os.environ.get('SHOTSNIP_ROOT', r'D:\YT-Cliping-Automation')) / '.venv'},
}
CLAUDE_TIMEOUT = 45 * 60
TEST_TIMEOUT = 25 * 60


def log(*parts):
    print(datetime.now().strftime('%Y-%m-%d %H:%M:%S'), *parts, flush=True)


def run(cmd, cwd=None, timeout=300, env=None, stdin=None):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=timeout, env=env, input=stdin)
        return r.returncode, (r.stdout or '') + (r.stderr or '')
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 124, f'{type(exc).__name__}: {exc}'


def tool_env(repo):
    env = dict(os.environ, PYTHONUTF8='1', GIT_TERMINAL_PROMPT='0')
    for key in ('TMP', 'TEMP'):
        env[key] = str(HOME / 'tmp')
    (HOME / 'tmp').mkdir(parents=True, exist_ok=True)
    scripts = REPOS[repo]['venv'] / 'Scripts'
    env['PATH'] = str(scripts) + os.pathsep + env.get('PATH', '')
    env['VIRTUAL_ENV'] = str(REPOS[repo]['venv'])
    return env


def notify(text, key):
    log('telegram', key, telegram_control.notify(text))
    try:
        config = json.loads((HOME / 'secrets/whatsapp-updates.json').read_text(encoding='utf-8'))
        result = send_update(config, text, 'operator:' + key, STATE / 'notify-state.json')
        log('whatsapp', key, result.get('status'))
    except Exception as exc:  # a notification failure must never stop repairs
        log('whatsapp failed', key, type(exc).__name__, exc)


# ---------------------------------------------------------------- GitHub

def gh_json(args, timeout=60):
    code, out = run(['gh', *args], timeout=timeout)
    if code != 0:
        raise RuntimeError(out.strip()[-300:])
    return json.loads(out)


def recent_runs(slug, commit=None):
    args = ['run', 'list', '-R', slug, '--limit', '40', '--json',
            'databaseId,workflowName,status,conclusion,url,createdAt,headSha,event']
    if commit:
        args += ['--commit', commit]
    else:
        args += ['--branch', 'master']
    return gh_json(args)


def detect_issues(ledger, now):
    issues, errors = [], []
    for repo, info in REPOS.items():
        try:
            runs = [r for r in recent_runs(info['slug'])
                    if now - op.parse_time(r['createdAt']) < timedelta(days=3)]
            issues += op.failed_workflows(repo, runs)
        except Exception as exc:
            errors.append(f'{repo}: GitHub check failed ({exc})')
    report_path = DATA_ROOT / 'data/private/operations.json'
    try:
        report = json.loads(report_path.read_text(encoding='utf-8'))
        if now - op.parse_time(report['generated']) < timedelta(minutes=30):
            issues += op.persistent_alerts(ledger, report.get('alerts', []), now)
    except (OSError, ValueError, KeyError):
        errors.append('Jarvis operations report unavailable')
    try:
        issues += op.bot_finding_issues({repo: bot_findings.persistent_failures(HOME / 'bot-workspace', repo, now)
                                         for repo in REPOS})
    except Exception as exc:  # findings are an extra signal; never block the other checks
        errors.append(f'Bot Office findings unavailable ({type(exc).__name__})')
    return issues, errors


def evidence_for(issue):
    if issue['kind'] == 'workflow':
        slug = REPOS[issue['repo']]['slug']
        _, out = run(['gh', 'run', 'view', str(issue['run_id']), '-R', slug, '--log-failed'], timeout=120)
        return f"Workflow: {issue['workflow']}\nRun: {issue.get('url')}\nCommit: {issue.get('sha')}\n\nFailed step logs:\n{out[-12000:]}"
    if issue['kind'] == 'bot_finding':
        f = issue['finding']
        return ("Lead from the Bot Office Operations monitor, an AI-written report over the dashboard snapshot. "
                "It is unverified: confirm it against this repository and real logs before changing anything, "
                "and report no_code_fix if it is not a code problem.\n"
                f"Finding: {f['title']}\nWhat the report cited: {f.get('evidence', '')}\n"
                f"Seen in {f.get('streak')} consecutive reports since {f.get('first_seen')}.")
    tail = ''
    try:
        tail = (HOME / 'operations.log').read_text(encoding='utf-8', errors='replace')[-4000:]
    except OSError:
        pass
    return f"Local health alert (from the Jarvis operations check):\n{json.dumps(issue['alert'], indent=1)}\n\noperations.log tail:\n{tail}"


# ---------------------------------------------------------------- clone, Claude, test, deploy

def clone(repo):
    path = STATE / 'repos' / repo
    slug = REPOS[repo]['slug']
    if not (path / '.git').exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        code, out = run(['git', 'clone', f'https://github.com/{slug}.git', str(path)], timeout=600)
        if code:
            raise RuntimeError('clone failed: ' + out[-300:])
        run(['git', 'config', 'credential.helper', 'manager'], cwd=path)
    for cmd in (['git', 'fetch', 'origin', 'master'], ['git', 'checkout', '-f', 'master'],
                ['git', 'reset', '--hard', 'origin/master'], ['git', 'clean', '-fd']):
        code, out = run(cmd, cwd=path, timeout=300)
        if code:
            raise RuntimeError(' '.join(cmd) + ' failed: ' + out[-300:])
    return path


def claude_exe():
    native = Path(os.environ.get('APPDATA', '')) / 'npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe'
    return str(native) if native.exists() else (shutil.which('claude') or 'claude')


def ask_claude(prompt, path, repo):
    cmd = [claude_exe(), '-p', '--output-format', 'json', '--permission-mode', 'acceptEdits',
           '--no-session-persistence',
           '--allowedTools', 'Read', 'Edit', 'Write', 'Glob', 'Grep', 'Bash(python -m pytest:*)',
           'Bash(git diff:*)', 'Bash(git status:*)', 'Bash(git log:*)', 'Bash(git show:*)',
           '--disallowedTools', 'Bash(git push:*)', 'Bash(git commit:*)', 'Bash(git reset:*)', 'WebFetch', 'WebSearch']
    try:
        proc = subprocess.run(cmd, cwd=path, capture_output=True, text=True, encoding='utf-8', errors='replace',
                              timeout=CLAUDE_TIMEOUT, env=tool_env(repo), input=prompt)
        code, out, err = proc.returncode, proc.stdout or '', proc.stderr or ''
    except (OSError, subprocess.TimeoutExpired) as exc:
        code, out, err = 124, '', f'{type(exc).__name__}: {exc}'
    runs = STATE / 'runs'
    runs.mkdir(parents=True, exist_ok=True)
    (runs / (datetime.now().strftime('%Y%m%d-%H%M%S') + f'-{repo}.json')).write_text(out + '\n' + err, encoding='utf-8')
    out = out or err
    try:
        payload = json.loads(out[out.index('{'):out.rindex('}') + 1])
        return op.parse_verdict(payload.get('result', '')), payload.get('total_cost_usd')
    except ValueError:
        return {'status': 'no_code_fix', 'root_cause': f'Claude Code did not run (exit {code}): {out.strip()[-200:]}',
                'summary': '', 'owner_action': 'Check that Claude Code is signed in on this PC.'}, None


def run_tests(path, repo):
    """(usable, failing test ids, output tail). Exit 0/1 means the suite ran; anything else
    (collection error, crash, timeout) is unusable and blocks a deploy."""
    python = str(REPOS[repo]['venv'] / 'Scripts' / 'python.exe')  # Windows ignores env PATH when resolving
    code, out = run([python, '-m', 'pytest', '-q', '-rfE', '-p', 'no:cacheprovider'], cwd=path,
                    timeout=TEST_TIMEOUT, env=tool_env(repo))
    ran = re.search(r'\d+ (passed|failed)', out) is not None
    return code in (0, 1) and ran, op.failed_tests(out), out[-1500:]


def tests_ok(path, repo, baseline):
    usable, failed, tail = run_tests(path, repo)
    new = op.new_failures(baseline, failed)
    if not usable or new:
        return False, ('new failures: ' + ', '.join(sorted(new)) + '\n' if new else '') + tail
    return True, tail


def deploy(path, repo, message, baseline):
    """Commit and push to master; on a race, rebase once and retest."""
    run(['git', 'add', '-A'], cwd=path)
    code, out = run(['git', 'commit', '-m', message], cwd=path)
    if code:
        return None, 'commit failed: ' + out[-200:]
    for attempt in range(2):
        code, out = run(['git', 'push', 'origin', 'HEAD:master'], cwd=path, timeout=180, env=tool_env(repo))
        if code == 0:
            _, sha = run(['git', 'rev-parse', 'HEAD'], cwd=path)
            return sha.strip(), None
        if attempt == 0:
            code, rebase = run(['git', 'pull', '--rebase', 'origin', 'master'], cwd=path, timeout=180, env=tool_env(repo))
            if code:
                run(['git', 'rebase', '--abort'], cwd=path)
                return None, 'master moved and the change no longer applies cleanly'
            ok, tail = tests_ok(path, repo, baseline)
            if not ok:
                return None, 'tests failed after rebasing onto the new master:\n' + tail
    return None, 'push failed: ' + out[-300:]


def attempt_change(repo, prompt, allowed, dry_run):
    """Shared path for fixes and content experiments. Returns a ledger-ready dict."""
    if dry_run:
        return {'status': 'dry_run'}
    path = clone(repo)
    usable, baseline, tail = run_tests(path, repo)
    if not usable:
        return {'status': 'no_code_fix', 'root_cause': 'The test suite cannot run on this PC, so nothing can be verified.',
                'summary': '', 'owner_action': 'Check the test environment: ' + tail[-200:]}
    if baseline:
        prompt += '\n\nTests already failing before your change (ignore these):\n' + '\n'.join(sorted(baseline))
    verdict, cost = ask_claude(prompt, path, repo)
    result = {**verdict, 'cost_usd': cost}
    run(['git', 'add', '-A'], cwd=path)
    _, diff = run(['git', 'diff', '--cached'], cwd=path)
    if verdict['status'] != 'fixed' or not diff.strip():
        return {**result, 'status': 'no_code_fix'}
    problems = op.check_diff(diff, allowed)
    if problems:
        return {**result, 'status': 'rejected', 'problems': problems}
    ok, tail = tests_ok(path, repo, baseline)
    if not ok:
        return {**result, 'status': 'tests_failed', 'tests': tail}
    files = op.changed_files(diff)
    message = (f"operator: {verdict['summary'] or 'automatic fix'}\n\nRoot cause / hypothesis: {verdict['root_cause']}\n"
               f"Files: {', '.join(files)}\nTests: passed locally before deploy. Auto-reverts if the problem returns.\n\n"
               "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
    sha, error = deploy(path, repo, message, baseline)
    if not sha:
        return {**result, 'status': 'deploy_failed', 'error': error}
    return {**result, 'status': 'deployed', 'commit': sha, 'files': files, 'deployed_at': op.now_utc().isoformat()}


def revert(entry, reason):
    repo = entry['repo']
    path = clone(repo)
    code, out = run(['git', 'revert', '--no-edit', entry['commit']], cwd=path)
    if code:
        run(['git', 'revert', '--abort'], cwd=path)
        return False, 'revert conflicts with newer changes: ' + out[-200:]
    run(['git', 'commit', '--amend', '-m',
         f"operator: revert {entry['commit'][:8]} ({reason})\n\n{entry.get('summary', '')}\n\n"
         "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"], cwd=path)
    code, out = run(['git', 'push', 'origin', 'HEAD:master'], cwd=path, timeout=180, env=tool_env(repo))
    return code == 0, out[-200:]


# ---------------------------------------------------------------- watch deployed changes

def watch_fixes(ledger, issues, now, dry_run):
    for entry in ledger.entries:
        if entry.get('kind') != 'fix_attempt' or entry.get('status') != 'deployed':
            continue
        try:
            ci = recent_runs(REPOS[entry['repo']]['slug'], entry['commit'])
        except Exception:
            continue
        reason = op.fix_regressed(entry, issues, ci, now)
        if reason and not dry_run:
            ok, detail = revert(entry, reason)
            entry['status'] = 'reverted' if ok else 'revert_failed'
            entry['revert_reason'] = reason if ok else detail
            notify(f"Sir, I rolled back my {entry['repo']} fix ({entry.get('summary')}) because {reason}."
                   if ok else f"Sir, my {entry['repo']} fix needs rolling back ({reason}) but the revert failed: {detail}. "
                   "Please take a look.", 'revert:' + entry['commit'][:12])
        elif not reason and now - op.parse_time(entry['deployed_at']) > op.FIX_WATCH:
            entry['status'] = 'verified'


def creative_scores(test, now):
    """Percentile scores for a creative test's variant and control Shorts, ranked against all
    recent editorial Shorts in one pass. Returns (variant, control, assigned, still_maturing)."""
    from src.strategy import peaks, rows, score
    index = json.loads((DATA_ROOT / 'data/published_index.json').read_text(encoding='utf-8'))
    best = peaks(DATA_ROOT, (now - timedelta(days=40)).strftime('%Y-%m-%d'))
    window = [e for e in index if (e.get('creative_test') or {}).get('id') == test['id']]
    variant = rows([e for e in window if e['creative_test'].get('arm') == 'variant'], best, now)
    control = rows([e for e in window if e['creative_test'].get('arm') == 'control'], best, now)
    others = rows([e for e in index if e not in window], best, now)
    score(variant + control + others)
    maturing = sum(1 for e in window if now - op.parse_time(e['published_at']) < timedelta(hours=48))
    return [v['score'] for v in variant], [v['score'] for v in control], len(window), maturing


def propose_instruction(guidance, strategy):
    """Ask Claude (no tools) for one new style instruction. Returns (instruction, hypothesis) or None."""
    cmd = [claude_exe(), '-p', '--output-format', 'json', '--no-session-persistence',
           '--disallowedTools', 'Bash', 'Edit', 'Write', 'Read', 'Glob', 'Grep', 'WebFetch', 'WebSearch']
    code, out = run(cmd, cwd=STATE, timeout=600, env=tool_env('histold'), stdin=op.creative_prompt(strategy, guidance))
    try:
        text = json.loads(out[out.index('{'):out.rindex('}') + 1]).get('result', '')
        for line in reversed(text.strip().splitlines()):
            line = line.strip().strip('`')
            if line.startswith('{') and line.endswith('}'):
                reply = json.loads(line)
                return str(reply.get('instruction', '')).strip(), str(reply.get('hypothesis', '')).strip()
    except ValueError:
        pass
    log('creative proposal unusable (exit', code, ')', out.strip()[-200:])
    return None


def push_data(path, message):
    """Commit and push a data-only change (no code), rebasing once if CI committed meanwhile."""
    run(['git', 'add', '-A'], cwd=path)
    code, out = run(['git', 'commit', '-m', message], cwd=path)
    if code:
        return False, out[-200:]
    for attempt in range(2):
        code, out = run(['git', 'push', 'origin', 'HEAD:master'], cwd=path, timeout=180, env=tool_env('histold'))
        if code == 0:
            return True, ''
        if attempt == 0 and run(['git', 'pull', '--rebase', 'origin', 'master'], cwd=path, timeout=180,
                                env=tool_env('histold'))[0]:
            run(['git', 'rebase', '--abort'], cwd=path)
            return False, 'rebase failed'
    return False, out[-200:]


def run_creative(ledger, now, dry_run):
    """Conclude finished creative tests, then start the next one when the current window is full."""
    from src import creative
    peek = creative.load(DATA_ROOT)
    index = json.loads((DATA_ROOT / 'data/published_index.json').read_text(encoding='utf-8'))
    latest = max((e.get('editorial_slot') or 0 for e in index), default=0)
    if not peek['measuring'] and not creative.window_full(peek, latest):
        return None
    path = DATA_ROOT if dry_run else clone('histold')
    guidance = creative.load(path)
    original = json.loads(json.dumps(guidance))
    notes, did = [], None

    if guidance['active'] and creative.window_full(guidance, latest):
        guidance['measuring'].append(guidance['active'])
        guidance['active'] = None
    for test in list(guidance['measuring']):
        try:
            variant, control, assigned, maturing = creative_scores(test, now)
        except Exception as exc:
            log('creative evaluation failed', exc)
            continue
        decision, lift = creative.judge(variant, control)
        age = now - op.parse_time(test['started'])
        if maturing or (decision == 'wait' and age < timedelta(days=12)):
            continue
        if decision == 'wait':
            decision = 'drop'  # too few comparable videos after 12 days: inconclusive, do not adopt
        stats = {'lift': lift, 'n_variant': len(variant), 'n_control': len(control), 'assigned': assigned}
        guidance = creative.conclude(guidance, test['id'], decision, stats, now)
        if not dry_run:
            did = ledger.add(kind='creative_test', status=decision, test=test['id'], summary=test['instruction'], **stats)
        notes.append(f"Sir, creative test result on Histold: '{test['instruction']}' "
                     + (f"beat the control by {lift:+.2f} and is now a permanent rule." if decision == 'promote'
                        else f"did not beat the control (lift {lift if lift is not None else 'n/a, too few videos'}); dropped."))

    if guidance['active'] is None:
        if manual_ab_test_running():
            log('creative test not started: the owner A/B test in config.yaml is still collecting posts')
        elif dry_run:
            log('would propose a new creative test starting at slot', latest + 3)
        else:
            try:
                strategy = json.loads((DATA_ROOT / 'data/strategy.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                strategy = {}
            proposal = propose_instruction(guidance, strategy) if strategy.get('videos_scored', 0) >= 20 else None
            problems = creative.validate_instruction(proposal[0], guidance) if proposal else ['no proposal']
            if problems:
                ledger.add(kind='creative_test', status='proposal_rejected', problems=problems,
                           summary=proposal[0] if proposal else '')
            else:
                guidance = creative.start_test(guidance, proposal[0], proposal[1], latest + 3, now)
                did = ledger.add(kind='creative_test', status='started', test=guidance['active']['id'],
                                 summary=proposal[0], hypothesis=proposal[1])
                notes.append(f"Sir, new creative test on Histold: '{proposal[0]}'. Half of the next "
                             f"{creative.POSTS} Shorts get it and half are the control; the winner becomes a permanent rule.")

    if dry_run:
        log('creative dry run:', notes or 'no conclusions', json.dumps({k: guidance[k] for k in ('active', 'measuring')}, default=str)[:300])
        return None
    if guidance != original:
        (path / creative.PATH).parent.mkdir(parents=True, exist_ok=True)
        (path / creative.PATH).write_text(json.dumps(guidance, indent=2), encoding='utf-8')
        ok, detail = push_data(path, 'operator: update creative split tests\n\n'
                               'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')
        if not ok:
            log('creative push failed', detail)
            return ledger.add(kind='creative_test', status='push_failed', error=detail)
        for i, note in enumerate(notes):
            notify(note, 'creative:' + now.strftime('%Y%m%d%H%M') + str(i))
    return did


def manual_ab_test_running():
    """True while config.yaml's ab_test window is open: prompt edits would contaminate it."""
    try:
        from src.config import load_config
        test = load_config(DATA_ROOT / 'config.yaml').get('ab_test') or {}
        if not test.get('enabled') or not isinstance(test.get('start_slot'), int):
            return False
        index = json.loads((DATA_ROOT / 'data/published_index.json').read_text(encoding='utf-8'))
        latest = max((e.get('editorial_slot') or 0 for e in index), default=0)
        return latest < test['start_slot'] + test.get('posts', 48)
    except Exception:
        return True  # unsure -> do not experiment


# ---------------------------------------------------------------- main

def tick(dry_run=False):
    now = op.now_utc()
    if (STATE / 'PAUSE').exists():
        log('paused by owner (PAUSE file present)')
        return {'status': 'paused'}
    ledger = op.Ledger(STATE / 'ledger.json')
    issues, errors = detect_issues(ledger, now)
    for error in errors:
        log(error)
    log(f'{len(issues)} open issue(s):', '; '.join(f"{i['repo']}: {i['title']}" for i in issues) or 'none')
    watch_fixes(ledger, issues, now, dry_run)

    issue, escalate = op.choose_issue(ledger, issues, now)
    for item in escalate:
        last = ledger.recent(now - op.ISSUE_COOLDOWN, kind='fix_attempt', issue=item['id'])[-1:]
        hint = (last[0].get('owner_action') or last[0].get('root_cause') or '') if last else ''
        notify(f"Sir, I could not fix '{item['title']}' ({item['repo']}) after {op.MAX_ATTEMPTS_PER_ISSUE} attempts. "
               f"{hint}".strip(), 'escalate:' + item['id'])
        if not dry_run:
            ledger.add(kind='escalation', issue=item['id'], repo=item['repo'], title=item['title'])

    did = None
    if issue:
        log('working on', issue['repo'], issue['title'], '(dry run)' if dry_run else '')
        evidence = evidence_for(issue)
        outcome = attempt_change(issue['repo'], op.fix_prompt(REPOS[issue['repo']]['label'], issue, evidence),
                                 None, dry_run)
        if not dry_run:
            did = ledger.add(kind='fix_attempt', issue=issue['id'], repo=issue['repo'], title=issue['title'], **outcome)
            if outcome['status'] == 'deployed':
                notify(f"Sir, {issue['repo']} had a problem ({issue['title']}). Fixed and deployed: {outcome['summary']} "
                       f"Tests passed; I'll roll it back automatically if it recurs.", 'fix:' + outcome['commit'][:12])
            elif outcome.get('owner_action'):
                notify(f"Sir, '{issue['title']}' ({issue['repo']}) is not a code problem: {outcome['owner_action']}",
                       'owner:' + issue['id'] + now.strftime('%Y%m%d'))
    if not issue:  # repairs come first; creative tests run on quiet ticks
        try:
            did = run_creative(ledger, now, dry_run) or did
        except Exception as exc:
            log('creative step failed', type(exc).__name__, exc)

    if not dry_run:
        ledger.save()
    status = {'checked': now.isoformat(), 'dry_run': dry_run, 'issues': issues, 'errors': errors,
              'action': did, 'recent': ledger.entries[-15:]}
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / ('status-dry-run.json' if dry_run else 'status.json')).write_text(
        json.dumps(status, indent=2, default=str), encoding='utf-8')
    log('action:', (did or {}).get('status', 'none'), (did or {}).get('summary', ''))
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='detect and plan only; no Claude, no pushes')
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    lock_path = STATE / 'operator.lock'
    with lock_path.open('a+b') as lock:
        try:
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except (ImportError, OSError):
            if sys.platform == 'win32':
                log('another operator tick is running')
                return 0
        tick(args.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
