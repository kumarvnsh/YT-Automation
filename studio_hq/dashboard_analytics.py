"""Normalize existing creator exports for the private dashboard."""
from datetime import date, datetime, timedelta, timezone
import math
import re
from statistics import median
from urllib.parse import urlsplit

from .archive import read_json


def load(path, fallback):
    try:
        data = read_json(path)
        return data if isinstance(data, type(fallback)) else fallback
    except (OSError, ValueError):
        return fallback


def rows(value):
    return (row for row in value if isinstance(row, dict)) if isinstance(value, list) else ()


def number(value):
    return value if type(value) in (int, float) and 0 <= value < math.inf else None


def day(value):
    try:
        return date.fromisoformat(value[:10]) if isinstance(value, str) else None
    except ValueError:
        return None


def public_url(value, platform, identity):
    if platform == 'YouTube':
        return 'https://www.youtube.com/watch?v=' + identity if re.fullmatch(r'[A-Za-z0-9_-]{11}', identity) else None
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        hosts = ('facebook.com', 'www.facebook.com') if platform == 'Facebook' else ('instagram.com', 'www.instagram.com')
        if url.scheme == 'https' and url.netloc in hosts and url.path != '/':
            return 'https://' + url.netloc + url.path
    except ValueError:
        pass
    return None


def videos(source, platform, metric='views', published_key='publishedAt', url_key='url'):
    seen = {}
    for row in rows(source):
        identity = row.get('id')
        if not isinstance(identity, str) or not identity or identity in seen:
            continue
        title = row.get('title')
        title = ' '.join(re.sub(r'https?://\S+', '', title).split()) if isinstance(title, str) else ''
        published = day(row.get(published_key))
        seen[identity] = {
            'id': identity, 'title': (title or 'Untitled video')[:160],
            'published': published.isoformat() if published else None,
            'value': number(row.get(metric)), 'likes': number(row.get('likes')),
            'watch_seconds': number(row.get('average_watch_time_seconds')),
            'url': public_url(row.get(url_key), platform, identity),
        }
    return list(seen.values())


def metric(label, value, detail):
    return {'label': label, 'value': value, 'detail': detail}


def catalog(identity, platform, label, updated, items, note):
    measured = [item for item in items if item['value'] is not None]
    values = [item['value'] for item in measured]
    return {
        'id': identity, 'platform': platform, 'label': label, 'updated': updated,
        'note': note, 'points': [], 'chart_label': '', 'videos': measured,
        'metrics': [
            metric('Lifetime ' + label.lower(), sum(values) if values else None, 'Sum across measured videos in this export'),
            metric('Median per video', median(values) if values else None, label + ' across measured videos'),
            metric('Measured videos', len(measured) if items else None, f'{len(items)} videos in this export'),
        ],
    }


def points(values, first=None, last=None):
    if not values and not (first and last):
        return []
    end = last or max(values)
    start = max(first or min(values), end - timedelta(days=30))
    return [
        {'day': current.isoformat(), 'value': values.get(current)}
        for current in (start + timedelta(days=offset) for offset in range((end - start).days + 1))
    ]


def daily_values(source, date_key, metric_key):
    clean = {}
    for row in rows(source):
        observed = day(row.get(date_key))
        value = number(row.get(metric_key))
        if observed and value is not None:
            clean[observed] = value
    return clean


def shotsnip_sources(root):
    source = load(root/'analytics.json', {})
    history = source.get('owner_analytics')
    latest = source.get('checked_at')
    result = []
    if isinstance(history, dict) and history.get('status') == 'connected':
        first, last = day(history.get('start')), day(history.get('end'))
        values = daily_values(history.get('daily'), 'day', 'views')
        values = {d: v for d, v in values.items() if first and last and first <= d <= last}
        if values:
            watch = daily_values(history.get('daily'), 'day', 'estimatedMinutesWatched')
            net = {}
            for row in rows(history.get('daily')):
                observed = day(row.get('day'))
                gained, lost = number(row.get('subscribersGained')), number(row.get('subscribersLost'))
                if observed and gained is not None and lost is not None:
                    net[observed] = gained - lost
            names = {row['id']: row for row in videos(source.get('videos'), 'YouTube', published_key='published_at')}
            items = []
            seen = set()
            for row in rows(history.get('videos')):
                identity = row.get('video')
                value = number(row.get('views'))
                if not isinstance(identity, str) or identity in seen or value is None:
                    continue
                seen.add(identity)
                item = names.get(identity, {'id': identity, 'title': 'YouTube video', 'published': None,
                                            'url': public_url(None, 'YouTube', identity), 'likes': None})
                items.append(dict(item, value=value, watch_seconds=number(row.get('averageViewDuration'))))
            selected_watch = [watch[d] for d in values if d in watch]
            selected_net = [net[d] for d in values if d in net]
            result.append({
                'id': 'youtube-daily', 'platform': 'YouTube', 'label': 'Views', 'updated': latest,
                'chart_label': 'Views by reporting day', 'points': points(values, first, last), 'videos': items,
                'note': 'YouTube owner analytics. Totals cover reported days. Missing days are left blank.',
                'metrics': [
                    metric('Reported views', sum(values.values()), f'{len(values)} reported days'),
                    metric('Watch time (hours)', round(sum(selected_watch)/60, 1) if selected_watch else None, 'Estimated time across reported days'),
                    metric('Net subscribers', sum(selected_net) if selected_net else None, 'Gains minus losses on reported days'),
                ],
            })
    yt_items = videos(source.get('videos'), 'YouTube', published_key='published_at')
    if yt_items:
        yt = catalog('youtube', 'YouTube', 'Views', latest, yt_items,
                     'Public lifetime counts for the listed uploads. The channel may include more videos.')
        subs = number(source.get('subscribers'))
        if subs is not None:
            yt['metrics'].append(metric('Subscribers', subs, 'Latest public channel count'))
        result.append(yt)
    facebook = load(root/'facebook-analytics.json', {})
    reel_plays = [row for row in rows(facebook.get('videos')) if row.get('views_metric') == 'fb_reels_total_plays']
    items = videos(reel_plays, 'Facebook', published_key='created_time', url_key='permalink_url')
    values = daily_values(facebook.get('daily'), 'end_time', 'value')
    if values:
        result.append({
            'id': 'facebook-daily', 'platform': 'Facebook', 'label': 'Page video views',
            'updated': facebook.get('checked_at'), 'chart_label': 'Page views by period end date',
            'points': points(values), 'videos': [],
            'note': 'Page views count plays of at least three seconds. Facebook supplies period end dates and may include the current partial period.',
            'metrics': [
                metric('Reported page views', sum(values.values()), f'{len(values)} daily periods'),
                metric('Followers', number(facebook.get('followers')), 'Latest page count'),
                metric('Page likes', number(facebook.get('page_likes')), 'Latest page count'),
            ],
        })
    if items:
        result.append(catalog('facebook', 'Facebook', 'Plays', facebook.get('checked_at'), items,
                              'Lifetime Reel plays include initial plays and replays for the listed Reels.'))
    return result


def histold_sources(root):
    source = load(root/'data/analytics.json', {})
    channels = source.get('channels', {})
    youtube = channels.get('histold', {}) if isinstance(channels, dict) else {}
    result = []
    if isinstance(youtube, dict):
        items = videos(youtube.get('videos'), 'YouTube')
        if items:
            result.append(catalog('youtube', 'YouTube', 'Views', youtube.get('updated'), items,
                                  'Public lifetime video counts for the listed uploads.'))
    meta = load(root/'data/meta_analytics.json', {})
    facebook = meta.get('facebook')
    if isinstance(facebook, dict):
        for key, identity, label, note in (
            ('fb_reels_total_plays', 'facebook-reels', 'Plays', 'Lifetime Reel plays include initial plays and replays.'),
            ('legacy_video_views', 'facebook-legacy', 'Legacy views', 'Lifetime counts from the legacy Facebook views metric.'),
        ):
            items = videos([r for r in rows(facebook.get('videos')) if r.get('views_metric') == key], 'Facebook')
            if items:
                result.append(catalog(identity, 'Facebook', label, facebook.get('updated'), items, note))
    instagram = meta.get('instagram')
    if isinstance(instagram, dict):
        items = videos(instagram.get('videos'), 'Instagram')
        if items:
            result.append(catalog('instagram', 'Instagram', 'Views', instagram.get('updated'), items,
                                  'Lifetime Instagram views for the listed posts.'))
    return result


def publishing(members):
    local = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(local).date()
    # Use completed days, so the current partial day never reads as a decline.
    first, last = today - timedelta(days=28), today - timedelta(days=1)
    counts = {}
    latest = None
    for row in members:
        if row['status'] != 'Published' or not row['at']:
            continue
        latest = max(latest or row['at'], row['at'])
        observed = datetime.fromisoformat(row['at']).astimezone(local).date()
        if first <= observed <= last:
            counts[observed] = counts.get(observed, 0) + 1
    if latest is None:
        return None
    return {
        'id': 'publishing', 'platform': 'All recorded', 'label': 'Publications', 'updated': latest,
        'chart_label': 'Recorded publications by day · IST', 'points': points(counts, first, last),
        'videos': [], 'metrics': [
            metric('Recorded publications', sum(counts.values()), 'Last 28 completed days · IST'),
            metric('Days with a publication', len(counts), 'From available publishing records'),
        ],
        'note': 'Based on available publishing records. A blank day has no publication in the records.',
    }


def analytics(roots, source, activity):
    result = {
        'histold': histold_sources(source),
        'shotsnip': shotsnip_sources(roots['shotsnip']/'work/dashboard'),
    }
    for channel, sources in result.items():
        recorded = publishing([row for row in activity if row['channel'] == channel])
        if recorded:
            sources.append(recorded)
    return result


def pacing_experiment(root):
    report = load(root/'work/dashboard/growth-experiment.json', {})
    published, target = number(report.get('published')), number(report.get('target_clips'))
    if published is None or not target:
        return None
    checkpoints = report.get('checkpoints')
    if not isinstance(checkpoints, dict):
        return None
    groups = []
    for key, platform, measure in (('youtube', 'YouTube', 'Views'), ('facebook', 'Facebook', 'Reel plays')):
        arms = checkpoints.get(key)
        if not isinstance(arms, dict):
            continue
        for arm, label in (('tight_20s', 'Tight · 20s'), ('context_30s', 'Context · 30s')):
            ages = arms.get(arm)
            if not isinstance(ages, dict):
                continue
            for hours in (24, 72):
                item = ages.get(str(hours))
                if isinstance(item, dict):
                    groups.append({
                        'platform': platform, 'measure': measure, 'variant': label, 'hours': hours,
                        'eligible': number(item.get('eligible')), 'observed': number(item.get('measured')),
                        'median': number(item.get('median')),
                    })
    return {'published': published, 'target': target, 'updated': report.get('checked_at'), 'groups': groups}
