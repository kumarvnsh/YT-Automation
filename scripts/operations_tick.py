#!/usr/bin/env python3
"""Local monitoring, weekly advisory reports and owner-approved WhatsApp updates."""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.build_dashboard import collect, read_json
from src.operations import IST, health_alerts, snapshot, weekly_review, daily_briefing
from src.whatsapp_updates import send_update


def tick(root, now=None):
    now = now or datetime.now(timezone.utc)
    private = root/'data/private'
    private.mkdir(parents=True, exist_ok=True)
    audience = private/'audience.json'
    if not audience.exists() or now.timestamp() - audience.stat().st_mtime > 86400:
        subprocess.run([sys.executable, str(Path(__file__).with_name('export_audience.py')), '--data-root', str(root)],
                       timeout=180, capture_output=True)
    data = collect(now, root.parent/'strategist.log', root)
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
              'weekly_review': review, 'notification': notification, 'daily_briefing': briefing}
    (private/'operations.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    args = parser.parse_args()
    result = tick(args.data_root)
    print('Operations updated:', len(result['alerts']), 'alerts; WhatsApp:', result['notification']['status'])
