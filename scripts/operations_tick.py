#!/usr/bin/env python3
"""Local monitoring, weekly advisory reports and owner-approved WhatsApp updates."""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.build_dashboard import collect, read_json
from src.operations import IST, health_alerts, snapshot, weekly_review, daily_briefing
from src.whatsapp_updates import send_update
from src.control import GitHub
from src.config import load_config
from src.recovery import latest_exports, recover, service_watchdog, shotsnip_keepalive


def tick(root, now=None):
    """Serialize dashboard and Task Scheduler callers to avoid duplicate recovery dispatches."""
    directory = root/'data/private'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory/'operations.lock').open('a+b') as lock:
        lock.seek(0)
        lock.write(b'0')
        lock.flush()
        lock.seek(0)
        try:
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            return {'alerts': [], 'notification': {'status': 'another_check_running'}}
        return _tick(root, now)


def _tick(root, now=None):
    now = now or datetime.now(timezone.utc)
    private = root/'data/private'
    private.mkdir(parents=True, exist_ok=True)
    audience = private/'audience.json'
    if not audience.exists() or now.timestamp() - audience.stat().st_mtime > 86400:
        subprocess.run([sys.executable, str(Path(__file__).with_name('export_audience.py')), '--data-root', str(root)],
                       timeout=180, capture_output=True)
    github = GitHub('gh', 'kumarvnsh/YT-Automation')
    exports, export_source = latest_exports(github, private/'live-exports.json', now)
    data = collect(now, root.parent/'strategist.log', root, exports)
    data['audience'] = read_json(audience, {})
    runs, run_status = [], 'unavailable'
    try:
        result = subprocess.run(['gh', 'run', 'list', '-R', 'kumarvnsh/YT-Automation', '--branch', 'master',
                                 '--limit', '30', '--json', 'databaseId,workflowName,status,conclusion,url,createdAt'],
                                capture_output=True, text=True, timeout=45)
        if result.returncode == 0:
            runs, run_status = json.loads(result.stdout), 'checked'
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    alerts = health_alerts(data, runs, now)
    service_repairs, service_alerts = service_watchdog(Path(__file__).resolve().parent.parent,
                                                     private/'service-recovery.json', now)
    if not any(a['id'] == 'service:dashboard' for a in service_alerts):  # worker starts via the dashboard
        clip_root = Path(os.environ.get('SHOTSNIP_ROOT', r'D:\YT-Cliping-Automation'))
        service_repairs += shotsnip_keepalive(Path(__file__).resolve().parent.parent, clip_root,
                                              private/'service-recovery.json', now)
    alerts.extend(service_alerts)
    config_values = load_config(root/'config.yaml')
    repairs = recover(alerts, runs, github, private/'recovery-state.json', now,
                      verified=export_source.startswith('verified') and run_status == 'checked',
                      auto_republish=bool(config_values.get('republish.auto', False)))
    repairs = service_repairs + repairs
    previous_report = read_json(private/'operations.json', {}) or {}
    recent = read_json(private/'recovery-history.json', []) or []
    active_ids = {a['id'] for a in alerts}
    for old in previous_report.get('alerts', []):
        if old['id'] not in active_ids:
            recent.append({'at': now.isoformat(), 'action': old['id'], 'status': 'resolved',
                           'message': 'Latest source/health check no longer reports this issue.'})
    recent.extend({'at': now.isoformat(), **a} for a in repairs
                  if a['status'] in ('started','failed_or_unknown','restart_requested','restart_failed'))
    recent = recent[-30:]
    (private/'recovery-history.json').write_text(json.dumps(recent, indent=2), encoding='utf-8')
    history = private/'history'
    history.mkdir(exist_ok=True)
    current = snapshot(data, now)
    day = now.astimezone(IST).date()
    daily_path = history/(str(day)+'.json')
    # Keep the first daily sample so intraday checks do not move the comparison baseline.
    if not daily_path.exists():
        daily_path.write_text(json.dumps(current), encoding='utf-8')
    target = day - timedelta(days=7)
    previous = read_json(history/(str(target)+'.json'))
    review = weekly_review(data, now, current, previous)
    review_path = private/('weekly-'+review['week']+'.json')
    review_path.write_text(json.dumps(review, indent=2, default=str), encoding='utf-8')
    briefing = daily_briefing(data, alerts, now)
    (private/'daily-briefing.txt').write_text(briefing, encoding='utf-8')
    config = read_json(root.parent/'secrets/whatsapp-updates.json', {}) or {}
    notification = {'status': 'disabled' if not config.get('enabled') else 'scheduled', 'daily_time': '09:00 Asia/Kolkata'}
    if config.get('enabled'):
        if now.astimezone(IST).hour >= 9:
            notification = send_update(config, briefing, 'daily:'+str(day), private/'notification-state.json')
        if config.get('send_alerts', True):
            for alert in alerts:
                if alert['severity'] == 'error':
                    send_update(config, 'Jarvis alert: '+alert['message'], 'alert:'+alert['id'], private/'notification-state.json')
    report = {'generated': now.isoformat(), 'alerts': alerts, 'workflow_check': run_status,
              'export_source': export_source, 'repairs': repairs, 'recent_repairs': recent,
              'weekly_review': review, 'notification': notification, 'daily_briefing': briefing}
    (private/'operations.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    args = parser.parse_args()
    result = tick(args.data_root)
    print('Operations updated:', len(result['alerts']), 'alerts; WhatsApp:', result['notification']['status'])
