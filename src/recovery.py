"""Bounded operational recovery. Never publishes videos, modifies code or changes credentials."""
import json
import os
import subprocess
import requests
from datetime import datetime, timedelta, timezone

EXPORTS = {'index': 'data/published_index.json', 'analytics': 'data/analytics.json', 'meta': 'data/meta_analytics.json'}
ANALYTICS = 'Export Analytics & Trends'


def latest_exports(github, path, now):
    """Read the same GitHub exports the dashboard sees, without resetting the publishing clone."""
    try:
        cache = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        cache = {}
    try:
        fresh = (now - datetime.fromisoformat(cache['checked'])).total_seconds() < 180
    except (KeyError, ValueError, TypeError):
        fresh = False
    if fresh:
        return cache.get('exports', {}), 'verified GitHub exports (cached for up to 3 minutes)'
    exports = {}
    try:
        for key, filename in EXPORTS.items():
            value, _ = github.read_json(filename)
            if value is not None:
                exports[key] = value
        if not all(k in exports for k in EXPORTS):
            raise ValueError('Incomplete export response')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'checked': now.isoformat(), 'exports': exports}), encoding='utf-8')
        return exports, 'verified GitHub exports'
    except Exception:
        return cache.get('exports', {}), 'GitHub unavailable; using the last cached or laptop exports'


def recover(alerts, runs, github, state_path, now, verified=False, auto_republish=False):
    """At most two analytics refreshes/hour, 15 minutes apart; record before dispatch."""
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {'attempts': []}
    needed = any(a['id'] in ('stale:YouTube export', 'stale:Facebook/Instagram export') for a in alerts)
    actions = []
    if not needed:
        return actions
    if not verified:
        return [{'action': 'refresh_analytics', 'status': 'waiting_for_github',
                 'message': 'Cannot verify the latest exports; retry source connectivity before dispatching work.'}]
    if auto_republish:
        return [{'action': 'refresh_analytics', 'status': 'needs_review',
                 'message': 'Analytics has auto-republish enabled; recovery will not trigger a potentially publishing workflow.'}]
    if any(r.get('workflowName') == ANALYTICS and r.get('status') in ('queued','in_progress','waiting','pending','requested') for r in runs):
        return [{'action': 'refresh_analytics', 'status': 'in_progress',
                 'message': 'An analytics export is already active; waiting for its output.'}]
    attempts = []
    for item in state.get('attempts', []):
        try:
            at = datetime.fromisoformat(item['at'])
            if now - at < timedelta(hours=1):
                attempts.append(item)
        except (ValueError, KeyError, TypeError):
            continue
    if attempts and (len(attempts) >= 2 or now - datetime.fromisoformat(attempts[-1]['at']) < timedelta(minutes=15)):
        return [{'action': 'refresh_analytics', 'status': 'cooldown',
                 'message': 'Recovery is bounded to two exports/hour, at least 15 minutes apart. A fresh export timestamp must confirm resolution.'}]
    attempt = {'at': now.isoformat(), 'status': 'outcome_unknown'}
    attempts.append(attempt)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({'attempts': attempts}), encoding='utf-8')
    try:
        github.run_workflow('analytics.yml')
        attempt['status'] = 'started'
        message = 'Started analytics refresh; the alert stays open until fresh exports are observed.'
    except Exception:
        attempt['status'] = 'failed_or_unknown'
        message = 'Dispatch did not confirm success. The attempt is retained to prevent a retry storm.'
    state_path.write_text(json.dumps({'attempts': attempts}), encoding='utf-8')
    actions.append({'action': 'refresh_analytics', 'status': attempt['status'], 'message': message})
    return actions


def _lock_held(path):
    """True when another process holds the worker's FileLock (Windows byte lock)."""
    if not path.exists():
        return False
    try:
        import msvcrt
    except ImportError:
        return True  # cannot probe off Windows; never risk a duplicate start
    try:
        fd = os.open(str(path), os.O_RDWR)
    except OSError:
        return True
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def shotsnip_keepalive(repo, clip_root, state_path, now):
    """Restart the Shotsnip clipping worker if it died (e.g. a dashboard restart killed
    it), unless the owner pressed Stop. Bounded to one attempt per 10 minutes."""
    work = clip_root/'work'/'dashboard'
    if not (clip_root/'watch_service.py').exists() or (work/'stop-worker').exists() or _lock_held(work/'worker.lock'):
        return []
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
        if now - datetime.fromisoformat(state['shotsnip worker']) < timedelta(minutes=10):
            return []
    except (OSError, ValueError, KeyError, TypeError):
        state = {}
    state['shotsnip worker'] = now.isoformat()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state), encoding='utf-8')
    try:
        result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                 '-File', str(repo/'scripts'/'start_shotsnip_worker.ps1')], capture_output=True, timeout=90)
        status = 'restart_requested' if result.returncode == 0 else 'restart_failed'
    except (OSError, subprocess.TimeoutExpired):
        status = 'restart_failed'
    return [{'action': 'restart shotsnip worker', 'status': status,
             'message': 'Shotsnip clipping worker was not running; clips stay local.'}]


def service_watchdog(repo, state_path, now):
    """Restart only known local services after a failed HTTP probe, with a 15-minute cooldown."""
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    actions, alerts = [], []
    for service, url, script in [('dashboard', 'http://127.0.0.1:8799/login', 'start_dashboard.ps1'),
                                 ('WhatsApp gateway', 'http://127.0.0.1:2785/', 'start_openwa.ps1')]:
        try:
            response = requests.get(url, timeout=4)
            if response.status_code < 500:
                continue
        except requests.RequestException:
            pass
        alert = {'id': 'service:' + service, 'severity': 'error', 'message': service + ' is not responding.'}
        alerts.append(alert)
        try:
            cooling = now - datetime.fromisoformat(state[service]) < timedelta(minutes=15)
        except (KeyError, TypeError, ValueError):
            cooling = False
        if cooling:
            actions.append({'action': 'restart ' + service, 'status': 'cooldown', 'message': 'Waiting before another restart; service health is not yet confirmed.'})
            continue
        state[service] = now.isoformat()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state), encoding='utf-8')
        try:
            result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                     '-File', str(repo/'scripts'/script)], capture_output=True, timeout=45)
            status = 'restart_requested' if result.returncode == 0 else 'restart_failed'
        except (OSError, subprocess.TimeoutExpired):
            status = 'restart_failed'
        actions.append({'action': 'restart ' + service, 'status': status,
                        'message': 'The next health probe must confirm recovery; no publish command was run.'})
    return actions, alerts
