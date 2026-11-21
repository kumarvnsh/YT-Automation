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


METRIC_LABELS = {
    'views': 'Views (YouTube Data API, lifetime)',
    'legacy_video_views': 'Video views (legacy Facebook definition)',
    'fb_reels_total_plays': 'Reel plays (includes replays)',
    'instagram_views': 'Views (Instagram Insights)',
}
GITHUB_RUN = re.compile(r'\d{6,20}')


def observation_index(source):
    """Latest cumulative snapshot per media ID, with when it was observed and the metric definition."""
    index = {}
    channel = load(source/'data/analytics.json', {}).get('channels', {})
    youtube = channel.get('histold') if isinstance(channel, dict) else None
    if isinstance(youtube, dict):
        for video in rows(youtube.get('videos')):
            index[('youtube', video.get('id'))] = (video, youtube.get('updated'), 'views')
    meta = load(source/'data/meta_analytics.json', {})
    for platform in ('facebook', 'instagram'):
        section = meta.get(platform) if isinstance(meta, dict) else None
        if isinstance(section, dict):
            for video in rows(section.get('videos')):
                metric = video.get('views_metric') if platform == 'facebook' else 'instagram_views'
                index[(platform, video.get('id'))] = (video, section.get('updated'), metric)
    return index


def observation(index, platform, identity):
    found = index.get((platform, identity)) if isinstance(identity, str) else None
    if not found:
        return None
    video, observed, metric = found
    value = number(video.get('plays') if metric == 'fb_reels_total_plays' else video.get('views'))
    published, seen = instant(video.get('publishedAt') or video.get('created_time')), instant(observed)
    age = round((seen - published).total_seconds() / 3600) if published and seen and seen >= published else None
    return {'platform': {'youtube': 'YouTube', 'facebook': 'Facebook', 'instagram': 'Instagram'}[platform],
            'value': value, 'metric': METRIC_LABELS.get(metric, 'Metric definition unavailable'),
            'likes': number(video.get('likes')), 'comments': number(video.get('comments')),
            'observed_at': seen.isoformat() if seen else None, 'age_hours': age}


def idea_record(entry):
    selection = entry.get('editorial_selection') if entry else None
    if not isinstance(selection, dict):
        return None
    candidates = list(rows(selection.get('candidates')))
    chosen = selection.get('selected_index')
    chosen = candidates[chosen] if isinstance(chosen, int) and 0 <= chosen < len(candidates) else None
    return {
        'premise': text((chosen or {}).get('premise'), 400),
        'viewer_reason': text((chosen or {}).get('viewer_reason'), 300),
        'selection_reason': text(selection.get('selection_reason'), 400),
        'rejected': [{'premise': text(row.get('premise'), 300), 'reason': text(row.get('rejection_reason'), 300)}
                     for row in candidates if row is not chosen and isinstance(row.get('rejection_reason'), str)][:8],
    }


def trace_detail(job, reservation, entries, draft, index, repo='kumarvnsh/YT-Automation'):
    """Idea -> reservation -> run -> publications -> observations; every missing link is named."""
    first = entries[0] if entries else None
    run = (first or {}).get('run_id') or (draft or {}).get('run_id')
    run = str(run) if run is not None and GITHUB_RUN.fullmatch(str(run)) else None
    production = {}
    for key, label in (('editorial_series', 'Series'), ('editorial_region', 'Region'), ('category', 'Category'),
                       ('design', 'Design'), ('duration_variant', 'Duration variant'), ('voice', 'Voice')):
        value = (first or {}).get(key)
        if isinstance(value, str) and value:
            production[label] = text(value.replace('_', ' '), 60)
    duration = number((first or {}).get('duration'))
    if duration:
        production['Duration'] = f'{duration:.0f} s'
    arms = []
    for key in ('ab_test', 'growth_trial'):
        value = (first or {}).get(key)
        if isinstance(value, dict) and isinstance(value.get('id'), str):
            details = ', '.join(f"{name.replace('_', ' ')} {text(str(item), 40)}" for name, item in value.items()
                                if name in ('title_style', 'voice', 'lane') and item is not None)
            arms.append(text(value['id'], 60) + (' · ' + details if details else ''))
    if isinstance((first or {}).get('experiment'), str) and not any(arm.startswith(first['experiment']) for arm in arms):
        arms.append(text(first['experiment'], 60))
    publications, observations = [], []
    for entry in entries:
        links = []
        for platform, key in (('youtube', 'video_id'), ('facebook', 'facebook_id'), ('instagram', 'instagram_id')):
            identity = entry.get(key)
            if not isinstance(identity, str) or not identity:
                continue
            seen = observation(index, platform, identity)
            links.append({'platform': platform, 'id': text(identity, 40), 'observed': seen is not None,
                          'url': public_url(None, 'YouTube', identity) if platform == 'youtube' else None})
            if seen:
                observations.append(dict(seen, media=text(identity, 40)))
        when = instant(entry.get('published_at'))
        publications.append({'at': when.isoformat() if when else None, 'links': links,
                             'workflow': text(entry.get('workflow'), 30),
                             'republished_from': text(entry.get('republished_from'), 40) or None,
                             'retitled_at': entry.get('retitled_at') if isinstance(entry.get('retitled_at'), str) else None})
    idea = idea_record(first)
    gaps = []
    if not idea:
        gaps.append('The idea and its alternatives were not recorded for this job.')
    if not run:
        gaps.append('No workflow run is linked to this job.')
    if not entries and not draft:
        gaps.append('No publication or draft is recorded for this reservation.')
    recorded = {entry.get('video_id') for entry in entries}
    for entry in entries:
        original = entry.get('republished_from')
        if isinstance(original, str) and original not in recorded:
            gaps.append(f'The publication record for {text(original, 40)} is missing; it was reposted as '
                        f"{text(entry.get('video_id'), 40)}.")
    names = {'youtube': 'YouTube', 'facebook': 'Facebook', 'instagram': 'Instagram'}
    for publication in publications:
        for link in publication['links']:
            if not link['observed']:
                gaps.append(f"No current {names[link['platform']]} observation for media {link['id']}.")
    return {'idea': idea, 'reservation': {'date': reservation.get('date'), 'slot': text(reservation.get('slot'), 20)},
            'run': {'id': run, 'url': f'https://github.com/{repo}/actions/runs/{run}'} if run else None,
            'production': production, 'arms': arms, 'hook': text((first or {}).get('hook'), 160),
            'publications': publications, 'observations': observations, 'gaps': list(dict.fromkeys(gaps))}


def topic_trace(source, approvals, repo='kumarvnsh/YT-Automation'):
    published = {row['stage_dir_name']: row for row in rows(load(source/'data/published_index.json', []))
                 if row.get('channel', 'histold') == 'histold' and isinstance(row.get('stage_dir_name'), str)}
    history, seen_rows = defaultdict(list), set()
    for row in rows(load(source/'data/published_index.json', [])):
        if row.get('channel', 'histold') == 'histold' and isinstance(row.get('stage_dir_name'), str):
            key = (row['stage_dir_name'], row.get('video_id'), row.get('workflow'), row.get('run_id'), row.get('published_at'))
            if key not in seen_rows:  # The index can hold repeated repost rows; show each publication once.
                seen_rows.add(key)
                history[row['stage_dir_name']].append(row)
    index = observation_index(source)
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
            'trace': trace_detail(job, record, sorted(history.get(job, []), key=lambda row: str(row.get('published_at'))),
                                  draft, index, repo),
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


MIN_ARM_SAMPLE = 3
CHECKPOINT_HOURS = {'24h': (24, 3), '72h': (72, 6), '7d': (168, 12)}
TRIAL_METRICS = {'youtube': ('views', 'Views (YouTube Data API, lifetime)'),
                 'fb_reels_total_plays': ('plays', 'Reel plays (includes replays)')}


def spread(values):
    return {'n': len(values), 'median': median(values) if values else None,
            'low': min(values) if values else None, 'high': max(values) if values else None}


def trial_groups(videos, lanes):
    """Arms grouped only within one checkpoint age, platform and metric definition."""
    grouped = defaultdict(lambda: defaultdict(lambda: {'values': [], 'rates': []}))
    for video in videos:
        lane = (video.get('trial') or {}).get('lane')
        checkpoints = video.get('checkpoints')
        if lane not in lanes or not isinstance(checkpoints, dict):
            continue
        for checkpoint, platforms in checkpoints.items():
            if checkpoint not in CHECKPOINT_HOURS or not isinstance(platforms, dict):
                continue
            for platform, observed in platforms.items():
                metrics = observed.get('metrics') if isinstance(observed, dict) else None
                if not isinstance(metrics, dict):
                    continue
                definition = 'youtube' if platform == 'youtube' else metrics.get('views_metric')
                if definition not in TRIAL_METRICS:
                    continue  # An unknown or legacy definition never joins a comparison.
                field, label = TRIAL_METRICS[definition]
                cell = grouped[(checkpoint, platform, label)][lane]
                if (value := number(metrics.get(field))) is not None:
                    cell['values'].append(value)
                if (rate := number(metrics.get('like_rate'))) is not None:
                    cell['rates'].append(rate)
    groups = []
    for (checkpoint, platform, label), cells in sorted(grouped.items(), key=lambda item: (CHECKPOINT_HOURS[item[0][0]][0], item[0][1])):
        arms = [dict(spread(cells[lane]['values']), lane=lane, name=lanes[lane],
                     like_rate=median(cells[lane]['rates']) if cells[lane]['rates'] else None,
                     like_rate_n=len(cells[lane]['rates'])) for lane in lanes]
        comparable = all(arm['n'] >= MIN_ARM_SAMPLE for arm in arms)
        leader = max(arms, key=lambda arm: arm['median']) if comparable else None
        overlap = comparable and any(arm is not leader and arm['high'] >= leader['low'] for arm in arms)
        groups.append({'checkpoint': checkpoint, 'hours': CHECKPOINT_HOURS[checkpoint][0],
                       'platform': 'YouTube' if platform == 'youtube' else 'Facebook', 'metric': label,
                       'arms': arms, 'comparable': comparable, 'leader': leader['lane'] if leader else None,
                       'overlap': overlap})
    return groups


def missed_checkpoints(videos, observed_at):
    """Published long enough ago that a checkpoint window closed without an observation."""
    missed = defaultdict(int)
    for video in videos:
        published = instant(video.get('published_at'))
        checkpoints = video.get('checkpoints') if isinstance(video.get('checkpoints'), dict) else {}
        if not published or not observed_at:
            continue
        age = (observed_at - published).total_seconds() / 3600
        for name, (hours, tolerance) in CHECKPOINT_HOURS.items():
            if age > hours + tolerance:
                for platform in ('youtube', 'facebook'):
                    if platform not in (checkpoints.get(name) or {}):
                        missed[(name, platform)] += 1
    return [{'checkpoint': name, 'platform': 'YouTube' if platform == 'youtube' else 'Facebook', 'count': count}
            for (name, platform), count in sorted(missed.items())]


def experiment_comparisons(source, now):
    from src.growth_trial import LANES
    lanes = {lane['id']: lane['name'] for lane in LANES}
    data = load(source/'data/private/growth-trial-observations.json', {})
    videos = [video for video in (data.get('videos') or {}).values() if isinstance(video, dict)] \
        if isinstance(data.get('videos'), dict) else []
    updated = instant(data.get('updated'))
    trials, recommendations = [], []
    for identity in sorted({(video.get('trial') or {}).get('id') for video in videos} - {None}):
        members = [video for video in videos if (video.get('trial') or {}).get('id') == identity]
        planned = max((number((video.get('trial') or {}).get('planned_posts')) or 0 for video in members), default=0)
        groups = trial_groups(members, lanes)
        missed = missed_checkpoints(members, updated)
        published = {lane: sum((video.get('trial') or {}).get('lane') == lane for video in members) for lane in lanes}
        trials.append({'id': text(identity, 60), 'planned_posts': int(planned) or None, 'published': len(members),
                       'lanes': [{'lane': lane, 'name': lanes[lane], 'published': published[lane]} for lane in lanes],
                       'groups': groups, 'missed': missed, 'updated': updated.isoformat() if updated else None})
        ready = [group for group in groups if group['comparable']]
        for group in ready:
            leader = next(arm for arm in group['arms'] if arm['lane'] == group['leader'])
            recommendations.append({
                'kind': 'observe', 'title': f"{leader['name']} leads {group['platform']} at {group['hours']} h",
                'evidence': [f"{arm['name']}: median {arm['median']:g} from {arm['n']} posts (range {arm['low']:g}–{arm['high']:g})"
                             for arm in group['arms']] + [f"Metric: {group['metric']}."],
                'uncertainty': ('Ranges overlap, so this is not a reliable difference. ' if group['overlap'] else
                                'Ranges separate in this sample. ') +
                               'Lanes rotate by day but are not randomized, and topics differ within each lane.',
                'action': 'Advisory only. Decide after the planned posts finish; nothing changes automatically.'})
        if not ready:
            thinnest = [f"{group['hours']} h {group['platform']}: " + ', '.join(f"{arm['name']} {arm['n']}" for arm in group['arms'])
                        for group in groups if group['hours'] <= 72]
            recommendations.append({
                'kind': 'collect', 'title': f'Keep collecting {identity}',
                'evidence': [f"{len(members)} of {int(planned) if planned else '?'} planned posts published."] +
                            (thinnest or ['No matched-age observations yet.']),
                'uncertainty': f'Each lane needs at least {MIN_ARM_SAMPLE} observations at the same age, platform and '
                               'metric before a comparison means anything. Current differences are noise.',
                'action': 'No change recommended.'})
        lost = [row for row in missed if row['count']]
        if lost:
            recommendations.append({
                'kind': 'gap', 'title': f'Checkpoint windows were missed in {identity}',
                'evidence': [f"{row['count']} × {row['checkpoint']} {row['platform']}" for row in lost],
                'uncertainty': 'A missed window is unknown, not zero. It shrinks the sample and can bias lanes if misses are uneven.',
                'action': 'Check that the observation recorder runs at least every 3 hours.'})
    if updated and (now - updated).total_seconds() > 6 * 3600:
        recommendations.append({'kind': 'gap', 'title': 'Trial observations are stale',
                                'evidence': [f'Last recorded {updated.isoformat()}.'],
                                'uncertainty': 'The 24 h window is only 3 hours wide, so a stale recorder loses checkpoints.',
                                'action': 'Check the analytics refresh that records trial observations.'})
    tracked = {(video.get('trial') or {}).get('id') for video in videos}
    tests = defaultdict(int)
    for row in rows(load(source/'data/published_index.json', [])):
        test = row.get('ab_test')
        if isinstance(test, dict) and isinstance(test.get('id'), str) and test['id'] not in tracked:
            tests[test['id']] += 1
    untracked = [{'id': text(identity, 60), 'published': count} for identity, count in sorted(tests.items())]
    for test in untracked:
        recommendations.append({'kind': 'gap', 'title': f"{test['id']} has no matched-age observations",
                                'evidence': [f"{test['published']} published posts carry this test's arms."],
                                'uncertainty': 'Only lifetime snapshots at different ages exist, so its arms cannot be compared fairly.',
                                'action': 'Add the test to a matched-age recorder before reading results.'})
    return {'trials': trials, 'untracked': untracked, 'recommendations': recommendations, 'min_sample': MIN_ARM_SAMPLE}


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


def assistant_history(config):
    from .assistant import history
    try:
        return history(config)
    except (OSError, KeyError):
        return []


def phase3(config, source, shotsnip, activity, approvals, now, *, include_finance=False):
    topics = topic_trace(source, approvals, config.get('github_repo', 'kumarvnsh/YT-Automation'))
    usage = observation_report(Path(config['dashboard_private_snapshot']).parent, now, activity, topics) if include_finance else {
        'providers': [], 'jobs': [], 'unattributed': 0, 'available': False,
    }
    return {
        'calendar': calendar(config, source, shotsnip, activity, now),
        'topics': topics,
        'histold_experiment': histold_experiment(source),
        'experiments': experiment_comparisons(source, now),
        'assistant': assistant_history(config) if include_finance else [],
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


ERROR_SHAPE = re.compile(r"[\[\"' ]*([A-Z][A-Za-z]{2,40}(?:Error|Exception|Timeout))\b:?\s*(.*)", re.S)
UNSAFE_DETAIL = re.compile(r'[\\/:]|https?|www\.|@')


def capture_error(value):
    """Exception type plus a short message; drop any detail that carries a path, URL or handle."""
    raw = value[0] if isinstance(value, list) and value else value
    match = ERROR_SHAPE.match(raw) if isinstance(raw, str) else None
    if not match:
        return 'Capture error'
    kind, detail = match[1], match[2].strip(" '\"]")
    detail = text(detail, 90) if detail and not UNSAFE_DETAIL.search(detail) else ''
    return kind + (': ' + detail if detail else '')


def agent_summary(dashboard):
    review = load(dashboard/'agent-review.json', {})
    policy = load(dashboard/'agent-policy.json', {})
    learning = policy.get('learning_enabled') if isinstance(policy.get('learning_enabled'), bool) else None
    weights = policy.get('source_priorities')
    priorities = sorted(({'creator': text(name, 80), 'priority': value}
                         for name, value in (weights.items() if isinstance(weights, dict) else ())
                         if isinstance(value, (int, float)) and not isinstance(value, bool)),
                        key=lambda row: (-row['priority'], row['creator']))
    findings = []
    for row in rows(review.get('findings')):
        median_views, n = row.get('median_views_near_24_hours'), row.get('n')
        if isinstance(row.get('creator'), str) and isinstance(n, int) and isinstance(median_views, (int, float)):
            findings.append({'creator': text(row['creator'], 80), 'samples': n,
                             'priority': row.get('priority') if isinstance(row.get('priority'), (int, float)) else None,
                             'median_views': median_views})
    errors = [{'run': text(row.get('run'), 60), 'creator': text(row.get('creator'), 80),
               'error': capture_error(row.get('errors'))} for row in rows(review.get('errors'))][:10]
    notes = lambda key: [text(item, 200) for item in review.get(key, []) if isinstance(item, str)][:5] \
        if isinstance(review.get(key), list) else []
    checked = instant(review.get('checked_at'))
    updated = instant(policy.get('updated_at'))
    return {'checked_at': checked.isoformat() if checked else None, 'learning': learning,
            'policy_updated_at': updated.isoformat() if updated else None,
            'priorities': priorities[:30], 'findings': findings[:30], 'errors': errors,
            'actions': notes('actions'), 'next_steps': notes('next_steps'),
            'sample_rule': text(review.get('sample_rule'), 200), 'comparison': text(review.get('comparison'), 200),
            'scope': text(review.get('automation_scope'), 240)}


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
        'agent': agent_summary(dashboard),
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
