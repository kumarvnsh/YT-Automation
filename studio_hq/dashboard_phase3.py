"""Read models for the schedule, evidence, and private financial reports."""
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import argparse
import hashlib
import json
import re
import sqlite3
from statistics import median
import subprocess

from src.facebook_monetization import summarize_api
from src.growth import IST, scheduled_format
from .dashboard_analytics import day, load, number, public_url, rows
from .dashboard_controls import CADENCE_MINUTES, cadence_settings


PHASE_LABELS = {
    'scanning': 'Finding a stream', 'capturing': 'Capturing', 'analyzing': 'Choosing a highlight',
    'finishing': 'Finishing', 'uploading': 'Uploading', 'published': 'Published', 'queued': 'Queued',
    'ready': 'Ready', 'waiting': 'Waiting', 'paused': 'Paused', 'failed': 'Failed', 'skipped': 'Skipped',
    'review': 'Needs review', 'repairing': 'Repairing', 'cleanup': 'Cleaning up',
}


def instant(value):
    try:
        if type(value) in (float, int):
            return datetime.fromtimestamp(value, timezone.utc)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, OverflowError, OSError):
        pass
    return None


def text(value, length=160):
    return ' '.join(re.sub(r'https?://\S+', '', value).split())[:length] if isinstance(value, str) else ''


def slots(root, config, now):
    """Use the same slot and expiry rules as the Histold dispatcher."""
    start = now.replace(minute=0, second=0, microsecond=0)
    horizon = (now.astimezone(IST).date() + timedelta(days=7))
    result = []
    for offset in range(7*24 + 1):
        at = start + timedelta(hours=offset)
        if at.astimezone(IST).date() >= horizon:
            break
        if at < now:
            continue
        fmt, source = scheduled_format(root, config, at)
        if fmt:
            result.append({'at': at.isoformat(), 'channel': 'histold',
                           'title': 'Long-form dispatch' if fmt == 'long' else 'Shorts dispatch',
                           'state': 'Planned',
                           'source': 'Weekly schedule' if fmt == 'long' else
                                     'Default hours' if source.startswith('default') else 'Growth plan',
                           'url': None})
    return result


def histold_slots(root, python, now):
    if not isinstance(python, str):
        return None
    try:
        process = subprocess.run(
            [python, '-m', 'studio_hq.dashboard_phase3', str(root), now.isoformat()],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        return json.loads(process.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def calendar(config, source, shotsnip, activity, now):
    today = now.astimezone(IST).date()
    first, last = today - timedelta(days=6), today + timedelta(days=6)
    planned = histold_slots(source, config.get('dashboard_calendar_python'), now)
    events = planned or []
    for item in activity:
        at = instant(item['at'])
        if item['status'] == 'Published' and at and first <= at.astimezone(IST).date() <= today:
            events.append({'at': item['at'], 'channel': item['channel'], 'title': item['title'],
                           'state': 'Published', 'source': item['kind'], 'url': item['url']})
    try:
        schedule, revision = cadence_settings(shotsnip/'scheduler.json')
    except (OSError, ValueError):
        schedule, revision = {}, None
    if not isinstance(schedule, dict):
        schedule, revision = {}, None
    state = load(shotsnip/'work/scheduler/state.json', {})
    cadence = number(schedule.get('post_minutes'))
    last_upload = instant(state.get('last_upload_at'))
    early = number(schedule.get('early_publish_minutes')) or 0
    enabled = schedule.get('enabled')
    mode = 'Active' if enabled is True else 'Paused' if enabled is False else 'Unavailable'
    if schedule.get('live_operations_batch_hold'):
        mode = 'On hold'
    earliest = None
    if mode == 'Active' and cadence and last_upload:
        earliest = (last_upload + timedelta(minutes=max(0, cadence-early))).isoformat()
    return {
        'today': today.isoformat(), 'first': first.isoformat(), 'last': last.isoformat(),
        'histold_available': planned is not None,
        'shotsnip': {'status': mode, 'cadence_minutes': cadence, 'earliest': earliest,
                     'scan_minutes': number(schedule.get('scan_minutes')), 'revision': revision,
                     'choices': CADENCE_MINUTES if revision and isinstance(enabled, bool)
                     and not schedule.get('live_operations_batch_hold') and not schedule.get('pending_post_minutes') else [],
                     'cadence_pending': bool(schedule.get('pending_post_minutes'))},
        'events': sorted(events, key=lambda event: event['at']),
    }


def topic_trace(source, approvals):
    published = {row['stage_dir_name']: row for row in rows(load(source/'data/published_index.json', []))
                 if row.get('channel', 'histold') == 'histold' and isinstance(row.get('stage_dir_name'), str)}
    pending = {row['stage']: row for row in approvals}
    by_job = {}
    for record in rows(load(source/'data/topic_reservations.json', [])):
        job = record.get('job_id', '')
        if not isinstance(job, str) or not re.fullmatch(r'\d{8}_\d{6}(?:_\d{6})?_(?:short|long)', job):
            continue
        related = published.get(job)
        draft = pending.get(job)
        identity = related.get('video_id') if related else None
        url = public_url(None, 'YouTube', identity) if isinstance(identity, str) else None
        when = day(record.get('date'))
        by_job[job] = {
            'job': job, 'title': text(record.get('title')) or 'Untitled topic',
            'date': when.isoformat() if when else None,
            'format': 'Long-form' if job.endswith('_long') else 'Short',
            'state': 'Published' if related else 'Ready for review' if draft else 'Reservation recorded',
            'url': url or (draft['url'] if draft else None),
        }
    return sorted(by_job.values(), key=lambda row: row['job'], reverse=True)[:40]


def histold_experiment(source):
    report = load(source/'data/experiments/histold-six-reels-report.json', {})
    samples = defaultdict(dict)
    for row in rows(report.get('snapshots')):
        metric = row.get('metrics')
        views = metric.get('views') if isinstance(metric, dict) else None
        value = number(views.get('value')) if isinstance(views, dict) else None
        category, hours, identity = text(row.get('category')), row.get('target_age_hours'), row.get('media_id')
        # YouTube only. Older Facebook observations lack a stable metric definition.
        if (row.get('platform') != 'youtube' or row.get('late') is not False or value is None
                or hours not in (3, 24, 72, 168) or not isinstance(identity, str) or not category):
            continue
        samples[(hours, category)][identity] = value
    return [
        {'hours': hours, 'category': category.replace('_', ' ').capitalize(),
         'median': median(items.values()), 'observed': len(items)}
        for (hours, category), items in sorted(samples.items())
    ]


def amount(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and abs(result) < Decimal('1e18') else None
    except InvalidOperation:
        return None


def earnings(source):
    folder = source/'data/private'
    status = load(folder/'earnings-status.json', {})
    fb = load(folder/'facebook-api-live.json', {}) or load(folder/'facebook-api-monetization.json', {})
    result = []
    if fb:
        try:
            summary = summarize_api(fb, fb)
        except (TypeError, ValueError, KeyError, ArithmeticError, AttributeError):
            summary = {}
        if summary.get('available'):
            rolling = amount(summary.get('rolling_28_excluding_bonuses'))
            daily = amount(summary.get('total'))
            if rolling is not None:
                result.append({'platform': 'Facebook', 'label': 'Rolling 28-day Page estimate',
                               'value': str(rolling), 'currency': summary.get('currency'),
                               'start': None, 'end': summary.get('rolling_bucket_end'),
                               'detail': 'Excludes bonuses. Page-level estimate.',
                               'checked': summary.get('rolling_checked')})
            if daily is not None:
                result.append({'platform': 'Facebook', 'label': 'Daily earnings subtotal',
                               'value': str(daily), 'currency': summary.get('currency'),
                               'start': summary.get('first_bucket_end'), 'end': summary.get('last_bucket_end'),
                               'detail': 'Overlaps with the rolling estimate. Excludes bonuses.',
                               'checked': summary.get('checked')})
    youtube = load(folder/'youtube-earnings.json', {})
    report = youtube.get('data')
    if youtube.get('status') == 'collected' and isinstance(report, dict):
        headers = [col.get('name') for col in rows(report.get('columnHeaders'))]
        if 'day' in headers and 'estimatedRevenue' in headers and isinstance(report.get('rows'), list):
            date_index, value_index = headers.index('day'), headers.index('estimatedRevenue')
            values = {}
            for row in report['rows']:
                if not isinstance(row, list) or len(row) <= max(date_index, value_index):
                    continue
                observed, revenue = day(row[date_index]), amount(row[value_index])
                if observed and revenue is not None:
                    values[observed] = revenue
            if values:
                result.append({'platform': 'YouTube', 'label': 'Estimated channel revenue',
                               'value': str(sum(values.values())), 'currency': youtube.get('currency'),
                               'start': min(values).isoformat(), 'end': max(values).isoformat(),
                               'detail': f'{len(values)} reported days. Channel-level estimate.',
                               'checked': status.get('checked')})
    return result


def job_reference(channel, identity):
    if channel not in ('histold','shotsnip') or not isinstance(identity, str):
        return False
    pattern = (r'\d{8}_\d{6}(?:_\d{6})?_(?:short|long)' if channel == 'histold'
               else r'(?:[a-f0-9]{32}|library-[a-f0-9]{24}|\d{8}-\d{6}-[A-Za-z0-9_-]{1,64})')
    return bool(re.fullmatch(pattern, identity))


def usage_record():
    return {'attempts': 0, 'failed': 0, 'priced': 0, 'cost': Decimal(0)}


def count_attempt(group, event):
    group['attempts'] += 1
    group['failed'] += event.get('status') == 'failed'
    charge = amount(event.get('cost_usd'))
    if charge is not None and charge >= 0 and event.get('pricing_basis'):
        group['cost'] += charge
        group['priced'] += 1


def priced_record(group):
    return dict(group, cost=str(group['cost']) if group['priced'] else None)


def observation_report(state, now, activity=(), topics=()):
    """Summarize local observations at provider, job, and job-stage grain."""
    start = now - timedelta(days=30)
    path = state/'observations.sqlite'
    index = {}
    for row in activity:
        channel, identity = row['channel'], row.get('job')
        at = instant(row['at'])
        if job_reference(channel, identity) and at and start <= at <= now:
            index.setdefault((channel, identity), {
                'title': row['title'], 'state': row['status'], 'url': row['url'], 'at': at.isoformat(),
            })
    for topic in topics:
        observed = day(topic.get('date'))
        if observed and start.date() <= observed <= now.date() and job_reference('histold', topic['job']):
            index.setdefault(('histold', topic['job']), {
                'title': topic['title'], 'state': topic['state'], 'url': topic['url'], 'at': topic['date'],
            })
    jobs = {}
    grouped = {}
    unattributed = 0
    available = False
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
            queried = db.execute("""SELECT channel,job_id,stage,kind,observed_at,payload FROM events
                                    WHERE channel IN ('histold','shotsnip') AND observed_at>=?
                                    ORDER BY observed_at""", (start.isoformat(),))
            for channel, identity, stage, kind, timestamp, payload in queried:
                at = instant(timestamp)
                if not at or not start <= at <= now:
                    continue
                key = (channel, identity)
                job = None
                if job_reference(channel, identity):
                    job = jobs.setdefault(key, dict(usage_record(), details={}, last_seen=None, stage=None))
                    job['last_seen'] = at.isoformat()
                    job['stage'] = text(stage, 50) or 'Unknown stage'
                if kind != 'api_usage':
                    continue
                try:
                    event = json.loads(payload)
                except ValueError:
                    continue
                if not isinstance(event, dict) or not (provider := text(event.get('provider'), 60)):
                    continue
                group = grouped.setdefault((channel, provider), dict(usage_record(), channel=channel, provider=provider))
                count_attempt(group, event)
                if job is None:
                    unattributed += 1
                    continue
                count_attempt(job, event)
                step = job['stage']
                entry = job['details'].setdefault((step, provider), dict(usage_record(), stage=step, provider=provider))
                count_attempt(entry, event)
        available = True
    except sqlite3.Error:
        grouped, jobs, unattributed, available = {}, {}, 0, False
    for key in jobs:
        index.setdefault(key, {'title': 'Local worker record', 'state': 'Observed', 'url': None, 'at': jobs[key]['last_seen']})
    reports = []
    for (channel, identity), record in index.items():
        job = priced_record(jobs.get((channel, identity), dict(usage_record(), details={}, last_seen=None, stage=None)))
        steps = [priced_record(row) for _, row in sorted(job.pop('details').items())]
        reports.append(dict(record, **job, channel=channel, id=identity, breakdown=steps))
    reports.sort(key=lambda row: max(row['at'], row['last_seen'] or ''), reverse=True)
    return {'providers': [priced_record(group) for _, group in sorted(grouped.items())],
            'jobs': reports, 'unattributed': unattributed, 'available': available}


def phase3(config, source, shotsnip, activity, approvals, now, *, include_finance=False):
    topics = topic_trace(source, approvals)
    usage = observation_report(Path(config['dashboard_private_snapshot']).parent, now, activity, topics) if include_finance else {
        'providers': [], 'jobs': [], 'unattributed': 0, 'available': False,
    }
    return {
        'calendar': calendar(config, source, shotsnip, activity, now),
        'topics': topics,
        'histold_experiment': histold_experiment(source),
        'earnings': earnings(source) if include_finance else [],
        'api_usage': usage['providers'],
        'job_usage': {key: usage[key] for key in ('jobs', 'unattributed', 'available')},
    }


def target_label(source):
    if source.get('platform') == 'youtube':
        handle = source.get('handle')
        if isinstance(handle, str) and handle.strip('@'):
            return '@' + text(handle.strip('@'), 60)
        return text(source.get('channel_id'), 40) or 'YouTube channel'
    return text(source.get('login'), 40) or 'Twitch profile'


def target_list(root):
    """Watchlist entries for the targets editor, with the file revision the owner will check."""
    path = root/'watchlist.json'
    try:
        raw = path.read_bytes()
    except OSError:
        return None, None
    try:
        creators = json.loads(raw.decode('utf-8-sig')).get('creators')
    except (ValueError, AttributeError):
        return None, None
    entries = []
    for creator in rows(creators):
        identity = creator.get('id')
        if not isinstance(identity, str) or not identity:
            continue
        sources = [{'platform': source['platform'], 'label': target_label(source)}
                   for source in rows(creator.get('sources')) if source.get('platform') in ('youtube', 'twitch')]
        entries.append({'id': text(identity, 80), 'name': text(creator.get('name'), 80) or identity,
                        'enabled': creator.get('enabled') is True, 'sources': sources})
    return entries, hashlib.sha256(raw).hexdigest()


def shotsnip_workspace(root):
    dashboard = root/'work/dashboard'
    review = load(dashboard/'agent-review.json', {})
    policy = load(dashboard/'agent-policy.json', {})
    targets = load(root/'watchlist.json', {}).get('creators')
    analytics = load(dashboard/'analytics.json', {}).get('owner_analytics')
    upload_status = load(dashboard/'upload-identity.json', {}).get('status')
    twitch_status = load(dashboard/'twitch-identity.json', {}).get('status')
    fb_status = load(dashboard/'facebook-analytics.json', {}).get('status')
    live = load(root/'work/scheduler/state.json', {})
    schedule = load(root/'scheduler.json', {})
    dispatcher = load(dashboard/'dispatcher.json', {})
    selected = live.get('selected')
    phase = live.get('phase')
    phase = phase if isinstance(phase, str) and phase in PHASE_LABELS else ''
    events = []
    for event in rows(live.get('events')):
        at, state = instant(event.get('at')), event.get('phase')
        if at and isinstance(state, str) and state in PHASE_LABELS:
            events.append({'at': at.isoformat(), 'phase': PHASE_LABELS[state]})
    heartbeat = instant(live.get('heartbeat_at'))
    reserve = live.get('ready_clips')
    pending = sum(row.get('status') in ('pending', 'uncertain') for row in rows(live.get('uploads')))
    statuses = {
        'connected': 'Connected', 'verified': 'Verified', 'not_connected': 'Disconnected',
        'not_started': 'Unavailable', 'unavailable': 'Unavailable',
        'anonymous_capture_tested': 'Anonymous capture tested',
    }
    target_entries, targets_revision = target_list(root)
    return {
        'target_list': target_entries,
        'targets_revision': targets_revision,
        'targets': len(list(rows(targets))) if isinstance(targets, list) else None,
        'active_targets': sum(row.get('enabled') is True for row in rows(targets)) if isinstance(targets, list) else None,
        'agent_at': review.get('checked_at'),
        'learning': policy.get('learning_enabled') if isinstance(policy.get('learning_enabled'), bool) else None,
        'live': {
            'phase': phase,
            'label': PHASE_LABELS.get(phase, 'Waiting for a report'),
            'heartbeat': heartbeat.isoformat() if heartbeat else None,
            'dispatcher_at': as_iso(dispatcher.get('checked_at')),
            'source': text(selected.get('channel'), 80) if isinstance(selected, dict) else '',
            'replay': isinstance(selected, dict) and selected.get('archive') is True,
            'reserve': len(reserve) if isinstance(reserve, list) else None,
            'reserve_limit': number(schedule.get('max_ready_clips')),
            'pending_publications': pending,
            'events': sorted(events, key=lambda item: item['at'], reverse=True)[:8],
        },
        'connections': [
            {'name': name, 'status': statuses.get(status, 'Check connection') if isinstance(status, str) else 'Unavailable'}
            for name, status in (
                ('YouTube upload', upload_status if (root/'token.json').is_file() else 'not_connected'),
                ('YouTube Analytics', analytics.get('status') if isinstance(analytics, dict) else None),
                ('Facebook Analytics', fb_status),
                ('Twitch', twitch_status),
            )
        ],
    }


def as_iso(value):
    at = instant(value)
    return at.isoformat() if at else None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Forecast Histold dispatch slots from current owner config')
    parser.add_argument('root', type=Path)
    parser.add_argument('now', type=datetime.fromisoformat)
    args = parser.parse_args()
    import yaml
    from src.config import Config
    with (args.root/'config.yaml').open(encoding='utf-8') as stream:
        owner = Config(yaml.safe_load(stream) or {})
    print(json.dumps(slots(args.root, owner, args.now)))
