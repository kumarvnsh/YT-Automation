"""Studio HQ view and controls for Histold fact image posts (src/fact_posts.py does the work).

The queue and cards live in Studio HQ's private drafts folder, so card previews use the existing
media route. Controls write the same queue and switch files the laptop runner and dispatcher use.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .archive import atomic_json, read_json
from .histold_live import clone, configured, control
from .media import media_id

ACTIONS = ('fact_post_decide', 'fact_post_make', 'fact_post_schedule', 'fact_post_remove', 'fact_post_retry')
POST_ID = re.compile(r'\d{8}_\d{6}_[0-9a-f]{6}')
STATUS = {'making': 'Being made', 'awaiting_approval': 'Waiting for your OK', 'approved': 'Ready for scheduled time', 'posting': 'Posting',
          'posted': 'Posted', 'partly_posted': 'Partly posted', 'skipped': 'Skipped', 'failed': 'Failed',
          'waiting_plans': 'Waiting for plan limits', 'replaced': 'Remade', 'removed': 'Removed'}
HIDDEN = ('replaced', 'removed')  # kept in the queue for history, never listed


def folder(config) -> Path:
    return Path(config['dashboard_private_snapshot']).parent / 'drafts' / 'fact-posts'


def queue(config) -> list[dict]:
    path = folder(config) / 'queue.json'
    rows = read_json(path) if path.is_file() else []
    return rows if isinstance(rows, list) else []


def _config_value(config, key: str):
    text = ''
    try:
        text = (clone(config) / 'config.yaml').read_text(encoding='utf-8')
    except OSError:
        pass
    section = re.search(r'^fact_posts:\s*\n((?:[ \t]+.*\n|\s*\n)+)', text + '\n', re.M)
    found = re.search(rf'^\s+{key}:\s*([^#\n]+)', section.group(1), re.M) if section else None
    return found.group(1).strip().strip('"\'') if found else None


def schedule(config) -> dict:
    switch = control(config) / 'fact-posts.json'
    data = read_json(switch) if switch.is_file() else None
    hours = [int(h) for h in re.findall(r'\d+', _config_value(config, 'hours_utc') or '')]
    enabled = data['enabled'] if isinstance(data, dict) and isinstance(data.get('enabled'), bool) else _config_value(config, 'enabled') == 'true'
    return {'enabled': enabled, 'hours_utc': hours, 'approve_until': _config_value(config, 'approve_until'),
            'changed_at': data.get('at') if isinstance(data, dict) else None}


def snapshot(config, media: dict | None) -> dict | None:
    if not config.get('dashboard_private_snapshot'):
        return None
    rows = []
    for row in queue(config)[-40:][::-1]:
        if not isinstance(row, dict) or not POST_ID.fullmatch(str(row.get('id'))) or row.get('status') in HIDDEN:
            continue
        card = Path(str(row.get('card') or ''))
        image = None
        if card.is_file() and card.resolve().is_relative_to(folder(config).resolve()) and media is not None:
            image = media_id(card)
            media[image] = str(card.resolve())
        facebook = row.get('facebook_id')
        rows.append({'id': row['id'], 'status': row.get('status'), 'label': STATUS.get(row.get('status'), row.get('status')),
                     'headline': row.get('headline'), 'fact': row.get('fact'), 'image': image,
                     'sources': [{'publisher': s.get('publisher'), 'url': s.get('url')} for s in row.get('sources') or [] if isinstance(s, dict)],
                     'created': row.get('created'), 'posted_at': row.get('posted_at'), 'error': row.get('error'),
                     'facebook_url': f'https://www.facebook.com/{facebook}' if isinstance(facebook, str) and re.fullmatch(r'[\d_]+', facebook) else None,
                     'instagram': bool(row.get('instagram_id')), 'repost': repostable(row)})
    today = datetime.now(timezone.utc).date().isoformat()
    return {'schedule': schedule(config), 'posts': rows,
            'posted_today': sum(1 for r in queue(config) if str(r.get('posted_at') or '').startswith(today)),
            'running': (control(config) / 'fact-post.lock').is_file()}


def repostable(row: dict) -> bool:
    """Same rule as src.fact_posts.can_repost: the card went out but missed a platform, so send it again unchanged."""
    status = row.get('status')
    return status == 'partly_posted' or (status == 'failed' and bool(row.get('publish_attempted'))
                                         and Path(str(row.get('card') or '')).is_file())


def check(body) -> str:
    action = body.get('action')
    if action == 'fact_post_decide':
        if not POST_ID.fullmatch(str(body.get('id'))) or body.get('choice') not in ('approve', 'skip'):
            raise ValueError('Choose a post and whether to post or skip it.')
        return body['id'] + ':' + body['choice']
    if action == 'fact_post_schedule':
        if type(body.get('enabled')) is not bool:
            raise ValueError('Choose whether fact posts run on schedule.')
        return str(body['enabled'])
    if action == 'fact_post_make':
        return 'now'
    if action == 'fact_post_retry':
        if not POST_ID.fullmatch(str(body.get('id'))):
            raise ValueError('Choose a failed post to retry.')
        return str(body['id'])
    if action == 'fact_post_remove':
        if body.get('id') != 'failed' and not POST_ID.fullmatch(str(body.get('id'))):
            raise ValueError('Choose a failed post to remove.')
        return str(body['id'])
    raise ValueError('Unknown fact post action.')


def _runner(config, args: list[str]) -> None:
    python = Path(configured(config)['python'])
    if not python.is_file():
        raise ValueError('The local production tools are not installed on this PC.')
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0
    subprocess.Popen([str(python), str(clone(config) / 'scripts' / 'run_fact_post.py'), *args], cwd=clone(config),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)


def perform(config, body) -> str:
    action = body['action']
    if action == 'fact_post_schedule':
        control(config).mkdir(parents=True, exist_ok=True)
        atomic_json(control(config) / 'fact-posts.json', {'enabled': body['enabled'], 'by': 'studio_hq',
                                                          'at': datetime.now(timezone.utc).isoformat()})
        return 'Fact posts now run three times a day.' if body['enabled'] else 'Scheduled fact posts are paused.'
    if action == 'fact_post_make':
        lock = control(config) / 'fact-post.lock'
        if lock.is_file() and time.time() - lock.stat().st_mtime < 3600:
            raise ValueError('A fact post is already being made.')
        _runner(config, ['--make'])
        return 'Making a fact post now. It appears here, and in Telegram for your OK, in about 5 minutes.'
    rows = queue(config)
    if action == 'fact_post_retry':
        entry = next((r for r in rows if isinstance(r, dict) and r.get('id') == body['id']), None)
        if not entry or entry.get('status') not in ('failed', 'waiting_plans', 'partly_posted'):
            raise ValueError('Only a failed, waiting or partly posted post can be retried.')
        lock = control(config) / 'fact-post.lock'
        if lock.is_file() and time.time() - lock.stat().st_mtime < 3600:
            raise ValueError('A fact post is being made right now; retry when it finishes.')
        if repostable(entry):
            _runner(config, ['--post', body['id']])
            return 'Posting the same card again to the platform that missed it. Check back in a minute.'
        _runner(config, ['--retry-post', body['id']])
        return 'Retrying it with the same fact. The new card appears here, and in Telegram for your OK, in about 5 minutes.'
    if action == 'fact_post_remove':
        # Only failed posts: a removed post is hidden from the list but stays in the queue file.
        targets = [r for r in rows if isinstance(r, dict) and r.get('status') == 'failed'
                   and body['id'] in ('failed', r.get('id'))]
        if not targets:
            raise ValueError('There is no failed post to remove.')
        stamp = datetime.now(timezone.utc).isoformat()
        for row in targets:
            row.update(status='removed', removed_at=stamp, updated=stamp)
        atomic_json(folder(config) / 'queue.json', rows)
        return 'Removed the failed post.' if len(targets) == 1 else f'Removed {len(targets)} failed posts.'
    entry = next((r for r in rows if r.get('id') == body['id']), None)
    if not entry:
        raise ValueError('That fact post no longer exists.')
    if entry.get('status') != 'awaiting_approval':
        raise ValueError('That post is no longer waiting for approval.')
    entry.update(status='skipped' if body['choice'] == 'skip' else 'approved', updated=datetime.now(timezone.utc).isoformat())
    atomic_json(folder(config) / 'queue.json', rows)
    if body['choice'] == 'skip':
        return 'Skipped. It will not be posted.'
    _runner(config, ['--post', body['id']])
    return 'Approved. Posting it to Facebook and Instagram now.'
