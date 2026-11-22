"""Private per-video YouTube insights: retention curves, traffic sources and daily views.

Fetching needs Google's client libraries, so the hub's maintenance loop starts it in the
helper interpreter every few hours. It only reads, using each channel's existing
analytics sign-in, and checks the channel before reading anything. Summaries run inside
the snapshot with no network. Impressions and click-through rate are not offered by the
YouTube Analytics API, so they are not shown.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from .archive import atomic_json, read_json

REFRESH_SECONDS = 6 * 3600
RETRY_SECONDS = 30 * 60
MAX_VIDEOS = 40
MIN_AGE_DAYS, MAX_AGE_DAYS = 2, 45
COMPARE_DAY = 7
MIN_TYPICAL_VIEWS = 20
SOURCES = {
    'SHORTS': 'Shorts feed', 'YT_SEARCH': 'YouTube search', 'RELATED_VIDEO': 'Suggested videos',
    'SUBSCRIBER': 'Home and subscriptions', 'YT_CHANNEL': 'Channel pages', 'YT_OTHER_PAGE': 'Other YouTube pages',
    'EXT_URL': 'External sites and apps', 'NO_LINK_OTHER': 'Direct or unknown', 'NOTIFICATION': 'Notifications',
    'PLAYLIST': 'Playlists', 'YT_PLAYLIST_PAGE': 'Playlist pages', 'HASHTAGS': 'Hashtag pages', 'END_SCREEN': 'End screens',
    'ADVERTISING': 'Ads', 'SOUND_PAGE': 'Sound pages', 'LIVE_REDIRECT': 'Live redirects', 'ANNOTATION': 'Cards',
    'CAMPAIGN_CARD': 'Campaign cards', 'PRODUCT_PAGE': 'Product pages', 'VIDEO_REMIXES': 'Remixes', 'SHORTS_CONTENT_LINKS': 'Shorts links',
}
CHANNELS = ('histold', 'shotsnip')
ERRORS = {
    'PermissionError': 'The saved analytics sign-in belongs to a different channel.',
    'FileNotFoundError': 'There is no analytics sign-in for this channel on this computer.',
    'RefreshError': 'The analytics sign-in has expired. Sign in again to resume.',
}


def output_path(config):
    return Path(config['dashboard_private_snapshot']).with_name('video-insights.json')


def label(source):
    return SOURCES.get(source) or str(source).replace('_', ' ').capitalize()


# ---------------------------------------------------------------- fetching (helper interpreter)

def channel_sources(config):
    """Token file and a reader for the expected channel ID, per channel. IDs stay on this computer."""
    histold = Path(config.get('dashboard_histold_index_root') or config['roots']['histold'])
    shotsnip = Path(config['roots']['shotsnip'])

    def histold_id():
        import yaml
        with (histold/'config.yaml').open(encoding='utf-8') as stream:
            return yaml.safe_load(stream)['youtube']['expected_channel_id']

    def shotsnip_id():
        return json.loads((shotsnip/'channel.json').read_text(encoding='utf-8'))['channel_id']

    return {'histold': (histold/'secrets/earnings-token.json', histold_id),
            'shotsnip': (shotsnip/'analytics_token.json', shotsnip_id)}


def recent_uploads(rows, today):
    """Newest first: (published, id) for uploads old enough to have analytics and young enough to matter."""
    picked = []
    for published, identity in rows:
        try:
            age = (today - date.fromisoformat(published[:10])).days
        except (TypeError, ValueError):
            continue
        if MIN_AGE_DAYS <= age <= MAX_AGE_DAYS:
            picked.append((published, identity))
    picked.sort(reverse=True)
    return picked[:MAX_VIDEOS]


def table(response):
    headers = [column['name'] for column in response.get('columnHeaders', [])]
    return [dict(zip(headers, row)) for row in response.get('rows', [])]


def compact_retention(rows):
    """Keep every other point of YouTube's 100-point curve: [share of video, audience watch ratio, relative]."""
    points = sorted((r for r in rows if isinstance(r.get('elapsedVideoTimeRatio'), (int, float))),
                    key=lambda r: r['elapsedVideoTimeRatio'])
    return [[round(r['elapsedVideoTimeRatio'], 3), round(r.get('audienceWatchRatio') or 0, 4),
             None if r.get('relativeRetentionPerformance') is None else round(r['relativeRetentionPerformance'], 4)]
            for index, r in enumerate(points) if index % 2 == 1 or index == 0]


def duration_seconds(value):
    import re
    match = re.fullmatch(r'P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', value or '')
    if not match:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def fetch_channel(token, expected, today):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    if not token.is_file():
        raise FileNotFoundError(token.name)
    credentials = Credentials.from_authorized_user_file(str(token))
    if not credentials.valid:
        credentials.refresh(Request())  # In memory only; the saved sign-in is never rewritten.
    youtube = build('youtube', 'v3', credentials=credentials, cache_discovery=False)
    items = youtube.channels().list(part='id,contentDetails', mine=True).execute().get('items', [])
    if len(items) != 1 or items[0]['id'] != expected:
        raise PermissionError('channel mismatch')
    uploads, rows, page = items[0]['contentDetails']['relatedPlaylists']['uploads'], [], None
    for _ in range(6):
        result = youtube.playlistItems().list(part='contentDetails', playlistId=uploads, maxResults=50, pageToken=page).execute()
        batch = [(i['contentDetails'].get('videoPublishedAt'), i['contentDetails']['videoId']) for i in result.get('items', [])]
        rows.extend(row for row in batch if row[0])
        page = result.get('nextPageToken')
        oldest = min((row[0] for row in batch if row[0]), default=None)
        if not page or (oldest and (today - date.fromisoformat(oldest[:10])).days > MAX_AGE_DAYS + 7):
            break
    picked = recent_uploads(rows, today)
    details = {}
    for offset in range(0, len(picked), 50):
        ids = ','.join(identity for _, identity in picked[offset:offset + 50])
        for item in youtube.videos().list(part='snippet,contentDetails,status', id=ids).execute().get('items', []):
            if item.get('status', {}).get('privacyStatus') == 'public':
                details[item['id']] = item
    picked = [(published, identity) for published, identity in picked if identity in details]
    end = today - timedelta(days=1)
    analytics = build('youtubeAnalytics', 'v2', credentials=credentials, cache_discovery=False)

    def query(**options):
        for attempt in range(3):
            try:
                return table(analytics.reports().query(ids='channel==MINE', endDate=str(end), **options).execute())
            except Exception:  # noqa: BLE001 - brief rate limits are common; retry, then let the caller decide
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))

    channel_start = str(end - timedelta(days=27))
    report = {'status': 'connected', 'checked_at': datetime.now(timezone.utc).isoformat(), 'window_end': str(end),
              'traffic_start': channel_start,
              'traffic': query(startDate=channel_start, metrics='views', dimensions='insightTrafficSourceType', sort='-views'),
              'content_types': query(startDate=channel_start, metrics='views,averageViewPercentage', dimensions='creatorContentType'),
              'videos': [], 'missing': 0}
    if not picked:
        return report
    first = min(published[:10] for published, _ in picked)
    totals = {row['video']: row for row in query(
        startDate=first, dimensions='video', maxResults=len(picked),
        metrics='views,engagedViews,averageViewPercentage,averageViewDuration,likes,shares,comments,subscribersGained',
        filters='video==' + ','.join(identity for _, identity in picked))}
    for published, identity in picked:
        item, start = details[identity], published[:10]
        video = {'id': identity, 'title': item['snippet'].get('title', '')[:160], 'published': published,
                 'duration_seconds': duration_seconds(item.get('contentDetails', {}).get('duration')),
                 'totals': {k: v for k, v in totals.get(identity, {}).items() if k != 'video'}}
        try:
            only = 'video==' + identity
            video['retention'] = compact_retention(query(startDate=start, filters=only, dimensions='elapsedVideoTimeRatio',
                                                         metrics='audienceWatchRatio,relativeRetentionPerformance'))
            video['traffic'] = query(startDate=start, filters=only, dimensions='insightTrafficSourceType', metrics='views', sort='-views')
            video['daily'] = [[row['day'], row['views']] for row in query(startDate=start, filters=only, dimensions='day',
                                                                          metrics='views', sort='day')]
        except Exception:  # noqa: BLE001 - one video's report must not hide the others
            report['missing'] += 1
        report['videos'].append(video)
    return report


def refresh(config, today=None, fetch=fetch_channel):
    """Fetch every channel; a failing channel keeps its last good report and says why."""
    path = output_path(config)
    previous = read_json(path) if path.is_file() else {}
    previous = previous if isinstance(previous, dict) else {}
    result = {'generated': datetime.now(timezone.utc).isoformat(), 'channels': {}}
    for channel, (token, expected) in channel_sources(config).items():
        try:
            result['channels'][channel] = fetch(token, expected(), today or date.today())
        except Exception as exc:  # noqa: BLE001 - report the reason, never the token or IDs
            reason = ERRORS.get(type(exc).__name__, 'YouTube Analytics could not be read this time.')
            old = (previous.get('channels') or {}).get(channel)
            if isinstance(old, dict) and old.get('status') == 'connected':
                result['channels'][channel] = dict(old, error=reason, error_at=result['generated'])
            else:
                result['channels'][channel] = {'status': 'error', 'error': reason, 'error_at': result['generated']}
            result['channels'][channel]['error_kind'] = type(exc).__name__
    atomic_json(path, result)
    return result


def maybe_start(config, config_path, *, spawn=None, now=None):
    """Called by the maintenance loop: start a background refresh when the report is stale."""
    if not config.get('dashboard_private_snapshot'):
        return False
    now = now or time.time()
    path, marker = output_path(config), Path(config['state_dir'])/'video-insights-attempt.json'
    if path.is_file() and now - path.stat().st_mtime < REFRESH_SECONDS:
        return False
    if marker.is_file() and now - marker.stat().st_mtime < RETRY_SECONDS:
        return False
    python = config.get('dashboard_calendar_python')
    if not python or not Path(python).is_file():
        return False
    atomic_json(marker, {'at': datetime.now(timezone.utc).isoformat()})
    command = [python, '-m', 'studio_hq.video_insights', str(config_path)]
    if spawn:
        spawn(command)
    else:
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0
        subprocess.Popen(command, cwd=Path(__file__).resolve().parent.parent, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    return True


# ---------------------------------------------------------------- summarising (snapshot, no network)

def number(value):
    return value if type(value) in (int, float) and value == value else None


def filled_daily(video, data_end):
    """Views for each day from publication to the last reported day. YouTube omits zero-view days."""
    try:  # YouTube Analytics days run on US Pacific time; standard time is close enough to place the first day.
        published = str(video.get('published'))
        start = date.fromisoformat(published) if len(published) == 10 else \
            (datetime.fromisoformat(published.replace('Z', '+00:00')) - timedelta(hours=8)).date()
    except ValueError:
        return []
    if not data_end or data_end < start:
        return []
    seen = {row[0]: number(row[1]) or 0 for row in video.get('daily') or [] if isinstance(row, list) and len(row) == 2}
    return [[str(start + timedelta(days=offset)), seen.get(str(start + timedelta(days=offset)), 0)]
            for offset in range((data_end - start).days + 1)]


def cumulative(daily, days):
    return sum(row[1] for row in daily[:days]) if len(daily) >= days else None


def watch_at(curve, share):
    points = [p for p in curve if isinstance(p, list) and len(p) == 3 and number(p[0]) is not None and number(p[1]) is not None]
    if not points:
        return None
    return min(points, key=lambda p: abs(p[0] - share))[1]


def shares(rows):
    total = sum(number(row.get('views')) or 0 for row in rows if isinstance(row, dict))
    return {row.get('insightTrafficSourceType'): (number(row.get('views')) or 0) / total
            for row in rows if isinstance(row, dict)} if total else {}


def is_short(video):
    seconds = number(video.get('duration_seconds'))
    return seconds is not None and seconds <= 180


def typical_curve(videos):
    """Median audience watch ratio at each point of the video, across this channel's recent uploads."""
    curves = [v['retention'] for v in videos if v.get('retention')]
    if len(curves) < 3:
        return []
    marks = sorted({p[0] for curve in curves for p in curve})
    result = []
    for mark in marks:
        values = [watch_at(curve, mark) for curve in curves]
        values = [v for v in values if v is not None]
        if len(values) >= 3:
            result.append([mark, round(median(values), 4)])
    return result


def summarize_channel(report):
    if not isinstance(report, dict) or report.get('status') != 'connected':
        return {'status': 'error', 'error': (report or {}).get('error') or 'No insights have been collected yet.'} \
            if isinstance(report, dict) else None
    videos = [v for v in report.get('videos', []) if isinstance(v, dict) and isinstance(v.get('id'), str)]
    reported = [row[0] for v in videos for row in v.get('daily') or [] if isinstance(row, list) and len(row) == 2]
    try:
        data_end = date.fromisoformat(max(reported)) if reported else None
    except ValueError:
        data_end = None
    filled = {v['id']: filled_daily(v, data_end) for v in videos}
    curve = typical_curve(videos)
    avg_pct = [number(v.get('totals', {}).get('averageViewPercentage')) for v in videos]
    typical_pct = median([v for v in avg_pct if v is not None]) if any(v is not None for v in avg_pct) else None
    feed_shares = [shares(v.get('traffic') or []).get('SHORTS') for v in videos if is_short(v) and v.get('traffic')]
    typical_feed = median([s for s in feed_shares if s is not None]) if any(s is not None for s in feed_shares) else None
    opening = [watch_at(v.get('retention') or [], 0.2) for v in videos]
    typical_opening = median([o for o in opening if o is not None]) if any(o is not None for o in opening) else None
    attention, details = [], {}
    for video in videos:
        totals = video.get('totals') or {}
        daily = filled[video['id']]
        traffic = shares(video.get('traffic') or [])
        details[video['id']] = {
            'title': video.get('title') or 'YouTube video', 'published': video.get('published'),
            'duration_seconds': number(video.get('duration_seconds')), 'short': is_short(video),
            'totals': {k: number(v) for k, v in totals.items()},
            'retention': [[p[0], p[1]] for p in video.get('retention') or []],
            'relative_retention': median([p[2] for p in video.get('retention') or [] if number(p[2]) is not None])
            if any(number(p[2]) is not None for p in video.get('retention') or []) else None,
            'traffic': [{'source': row.get('insightTrafficSourceType'), 'label': label(row.get('insightTrafficSourceType')),
                         'views': number(row.get('views')), 'share': round(traffic.get(row.get('insightTrafficSourceType'), 0), 4)}
                        for row in (video.get('traffic') or [])[:8] if isinstance(row, dict)],
            'daily': [row for row in daily[:30] if isinstance(row, list) and len(row) == 2],
        }
        day = min(len(daily), COMPARE_DAY)
        if day < 3:
            continue
        mine = cumulative(daily, day)
        others = [cumulative(filled[other['id']], day) for other in videos if other is not video]
        others = [value for value in others if value is not None]
        if mine is None or len(others) < 5 or median(others) < MIN_TYPICAL_VIEWS:
            continue
        typical = median(others)
        if mine >= 0.5 * typical:
            continue
        reasons = []
        pct = number(totals.get('averageViewPercentage'))
        if pct is not None and typical_pct and pct < 0.8 * typical_pct:
            reasons.append(f'Viewers watched {pct:.0f}% of it on average, against {typical_pct:.0f}% for a typical recent upload.')
        start = watch_at(video.get('retention') or [], 0.2)
        if start is not None and typical_opening and start < 0.8 * typical_opening:
            reasons.append('More viewers left in the first fifth of the video than usual.')
        relative = details[video['id']]['relative_retention']
        if relative is not None and relative < 0.35:
            reasons.append('Retention is below most YouTube videos of a similar length.')
        feed = traffic.get('SHORTS')
        if is_short(video) and feed is not None and typical_feed and feed < 0.6 * typical_feed:
            reasons.append(f'The Shorts feed brought {feed:.0%} of its views, against {typical_feed:.0%} usually.')
        if not reasons and not video.get('retention') and not video.get('traffic'):
            reasons.append('YouTube has too little viewing data on it yet to explain why.')
        elif not reasons:
            reasons.append('Retention and traffic look normal, so fewer people chose to watch it. The topic, title or thumbnail is the likely difference.')
        attention.append({'id': video['id'], 'title': details[video['id']]['title'], 'published': video.get('published'),
                          'day': day, 'views': mine, 'typical': typical, 'ratio': round(mine / typical, 3), 'reasons': reasons})
    attention.sort(key=lambda row: row['ratio'])
    traffic = shares(report.get('traffic') or [])
    return {
        'status': 'connected', 'checked_at': report.get('checked_at'), 'window_end': report.get('window_end'),
        'traffic_start': report.get('traffic_start'), 'error': report.get('error'), 'missing': report.get('missing', 0),
        'traffic': [{'source': row.get('insightTrafficSourceType'), 'label': label(row.get('insightTrafficSourceType')),
                     'views': number(row.get('views')), 'share': round(traffic.get(row.get('insightTrafficSourceType'), 0), 4)}
                    for row in (report.get('traffic') or [])[:10] if isinstance(row, dict)],
        'typical_retention': curve, 'typical_view_percentage': typical_pct, 'compare_day': COMPARE_DAY,
        'attention': attention[:8], 'videos': details, 'video_count': len(details),
        'data_end': str(data_end) if data_end else None,
        'retention_count': sum(bool(v.get('retention')) for v in videos),
    }


def summary(config):
    if not config.get('dashboard_private_snapshot'):
        return None
    path = output_path(config)
    data = read_json(path) if path.is_file() else None
    channels = (data or {}).get('channels') if isinstance(data, dict) else None
    if not isinstance(channels, dict):
        return {channel: {'status': 'pending', 'error': 'The first insights refresh has not finished yet.'} for channel in CHANNELS}
    return {channel: summarize_channel(channels.get(channel)) for channel in CHANNELS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    config = read_json(parser.parse_args().config)
    lock = Path(config['state_dir'])/'video-insights.lock'
    try:
        if lock.is_file() and time.time() - lock.stat().st_mtime > 3600:
            lock.unlink()
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return 0
    try:
        refresh(config)
        try:
            from .dashboard_controls import refresh_snapshot
            refresh_snapshot(config)
        except Exception:  # noqa: BLE001 - the 30-second cycle picks it up anyway
            pass
    finally:
        os.close(handle)
        lock.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
