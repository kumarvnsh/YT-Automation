"""What the Telegram bot shows: read-only summaries for /today, /stats and /posts, in Telegram HTML.

Everything here only reads files the channels already write; nothing changes. Missing or broken
files show as "not available" rather than failing the command.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

HOME = Path(os.environ.get('HISTOLD_HOME', r'D:\histold'))
HISTOLD_DATA = HOME / 'YT-Automation' / 'data'  # the runner's clone pulls fresh analytics every hour
SHOTSNIP = Path(os.environ.get('SHOTSNIP_HOME', r'D:\YT-Cliping-Automation'))
IST = timezone(timedelta(hours=5, minutes=30))
RULE = '━━━━━━━━━━━━━━'


def _json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _when(value) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).astimezone(IST)


def _num(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return '–'
    return f'{n / 1000:.1f}k'.replace('.0k', 'k') if n >= 1000 else str(n)


def header(icon: str, title: str, note: str = '') -> str:
    return f'{icon} <b>{escape(title)}</b>' + (f'\n<i>{escape(note)}</i>' if note else '') + '\n' + RULE


def histold_videos() -> list[dict]:
    channel = _json(HISTOLD_DATA / 'analytics.json', {}).get('channels', {}).get('histold', {})
    views = {v.get('id'): v for v in channel.get('videos', [])}
    rows = []
    for item in _json(HISTOLD_DATA / 'published_index.json', []):
        if item.get('channel', 'histold') != 'histold' or not item.get('video_id'):
            continue
        seen = views.get(item['video_id'], {})
        rows.append({'id': item['video_id'], 'title': item.get('title') or seen.get('title') or 'Untitled',
                     'at': _when(item.get('published_at')), 'views': seen.get('views'), 'likes': seen.get('likes')})
    return sorted((r for r in rows if r['at']), key=lambda r: r['at'], reverse=True)


def shotsnip_videos() -> list[dict]:
    listed = _json(SHOTSNIP / 'work' / 'dashboard' / 'analytics.json', {})
    seen = {v.get('id'): v for v in listed.get('videos', [])}
    rows = []
    for row in _json(SHOTSNIP / 'work' / 'scheduler' / 'state.json', {}).get('uploads', []):
        if row.get('status') != 'uploaded' or not row.get('video_id'):
            continue
        info = seen.get(row['video_id'], {})
        rows.append({'id': row['video_id'], 'title': row.get('title') or info.get('title') or 'Untitled clip',
                     'streamer': (row.get('source') or {}).get('channel') or '',
                     'at': _when(row.get('finished_at') or row.get('started_at')), 'views': info.get('views')})
    return sorted((r for r in rows if r['at']), key=lambda r: r['at'], reverse=True)


def _line(video: dict, dated: bool = False) -> str:
    title = video['title'] if len(video['title']) <= 70 else video['title'][:68].rstrip() + '…'
    meta = video['at'].strftime('%d %b %H:%M' if dated else '%H:%M')
    if video.get('views') is not None:
        meta += f" · 👁 {_num(video['views'])}"
    return f"• <a href=\"https://youtu.be/{escape(video['id'])}\">{escape(title, quote=False)}</a>\n   <i>{meta}</i>"


def today(now: datetime | None = None) -> str:
    day = (now or datetime.now(IST)).astimezone(IST).date()
    parts = [header('📅', 'Today', day.strftime('%A %d %B'))]
    for icon, name, videos in (('🏛', 'Histold', histold_videos()), ('🎮', 'Shotsnip', shotsnip_videos())):
        todays = [v for v in videos if v['at'].date() == day]
        parts.append(f'\n{icon} <b>{name}</b> · {len(todays)} upload{"s" if len(todays) != 1 else ""}')
        if todays:
            parts += [_line(v) for v in todays[:6]]
        elif videos:
            parts.append(f"<i>Nothing yet today. Last upload {videos[0]['at'].strftime('%d %b %H:%M')}.</i>")
        else:
            parts.append('<i>No uploads found.</i>')
    posts = [p for p in _fact_posts() if (_when(p.get('posted_at')) or datetime.min.replace(tzinfo=IST)).date() == day]
    waiting = [p for p in _fact_posts() if p.get('status') == 'awaiting_approval']
    parts.append(f'\n🖼 <b>Fact posts</b> · {len(posts)} posted' + (f', {len(waiting)} waiting for you (/posts)' if waiting else ''))
    parts += [f"• {escape(p.get('headline') or '')}" for p in posts[:4]]
    return '\n'.join(parts)


def stats(now: datetime | None = None) -> str:
    now = (now or datetime.now(IST)).astimezone(IST)
    parts = [header('📈', 'Channel stats')]
    snap = _json(SHOTSNIP / 'work' / 'dashboard' / 'analytics.json', {})
    for icon, name, videos, extra in (
            ('🏛', 'Histold', histold_videos(), ''),
            ('🎮', 'Shotsnip', shotsnip_videos(),
             f" · 👥 {_num(snap.get('subscribers'))} subs · 👁 {_num(snap.get('total_views'))} total" if snap else '')):
        week = [v for v in videos if now - v['at'] < timedelta(days=7)]
        views = sum(v['views'] or 0 for v in week)
        parts.append(f'\n{icon} <b>{name}</b>{extra}')
        parts.append(f'Last 7 days: {len(week)} uploads · 👁 {_num(views)} views')
        best = sorted((v for v in week if v.get('views') is not None), key=lambda v: v['views'], reverse=True)[:3]
        if best:
            parts.append('<b>Top this week</b>')
            parts += [_line(v, dated=True) for v in best]
    parts.append('\n<i>Views refresh about every hour.</i>')
    return '\n'.join(parts)


def _fact_posts() -> list[dict]:
    try:
        from src import fact_posts
        return fact_posts.load_queue()
    except Exception:  # noqa: BLE001 - a view must never break the bot
        return []


def waiting_posts() -> list[dict]:
    return [p for p in _fact_posts() if p.get('status') == 'awaiting_approval']


def post_caption(entry: dict) -> str:
    sources = ', '.join(s.get('publisher') or '' for s in entry.get('sources') or [])
    return (f"🖼 <b>Fact post ready for your OK</b>\n{RULE}\n<b>{escape(entry.get('headline') or '')}</b>\n\n"
            f"{escape(entry.get('fact') or '')}\n\n<i>Sources: {escape(sources)}</i>")
