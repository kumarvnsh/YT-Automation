"""Reserve a Histold publication before any platform receives an upload."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.config import load_config
from src.growth import IST, scheduled_format

LEDGER = Path('data/histold_publish_claims.json')


def reserve(cfg, claims, index, now, fmt, key, dispatch_slot):
    if fmt not in ('short', 'long'):
        raise ValueError('Select short or long for a publication run')
    if dispatch_slot is not None:
        if dispatch_slot != now.strftime('%Y-%m-%dT%H') or now.minute >= 40:
            raise ValueError('Scheduled dispatch missed its original UTC hour or start window')
        if fmt != scheduled_format(ROOT, cfg, now)[0]:
            raise ValueError('This format is not scheduled at this hour')
    if any(row['key'] == key for row in claims):
        raise ValueError('Publication slot or draft already claimed')

    local = now.astimezone(IST)
    day = local.date().isoformat()
    week = local.strftime('%G-W%V')
    if fmt == 'short':
        used = sum(row['day'] == day and row['format'] == 'short' for row in claims)
        known = {row['key'] for row in claims}
        legacy = set()
        for entry in index:
            if (entry.get('channel', 'histold') != 'histold' or entry.get('publish_claim') in known
                    or not entry.get('published_at') or not (entry.get('video_id') or entry.get('facebook_id'))):
                continue
            entry_format = entry.get('format') or ('long' if str(entry.get('stage_dir_name', '')).endswith('_long') else 'short')
            stamp = datetime.fromisoformat(entry['published_at'].replace('Z', '+00:00'))
            if entry_format == 'short' and stamp.astimezone(IST).date() == local.date():
                legacy.add(entry.get('video_id') or entry['facebook_id'])
        if used + len(legacy) >= int(cfg.get('growth.daily_publish_cap', 3)):
            raise ValueError('Daily Histold Shorts cap reached')
    elif dispatch_slot is not None and any(row['week'] == week and row['format'] == 'long' for row in claims):
        raise ValueError('This week already has a long-form claim')
    return [row for row in claims if row['week'] >= local.replace(day=1).strftime('%G-W%V')] + [
        {'key': key, 'day': day, 'week': week, 'format': fmt}
    ]


def claim(fmt, key):
    scheduled = os.environ.get('PUBLISH_SCHEDULED') == 'true'
    claims = json.loads(LEDGER.read_text()) if LEDGER.exists() else []
    index = json.loads(Path('data/published_index.json').read_text(encoding='utf-8'))
    try:
        updated = reserve(load_config(), claims, index, datetime.now(timezone.utc), fmt, key,
                          os.environ.get('DISPATCH_SLOT', '') if scheduled else None)
    except ValueError as exc:
        print(f'Publish blocked: {exc}')
        return 3
    LEDGER.write_text(json.dumps(updated, indent=2) + '\n')
    return 0


def git(*args, check=True):
    return subprocess.run(('git', *args), check=check).returncode


def main():
    fmt, key = sys.argv[1:3]
    if os.environ.get('GITHUB_RUN_ATTEMPT') != '1':
        print('Use a fresh dispatch. Rerunning a publication can repeat an upload.')
        return 3
    git('config', 'user.name', 'github-actions[bot]')
    git('config', 'user.email', 'github-actions[bot]@users.noreply.github.com')
    for _ in range(3):
        git('fetch', 'origin', 'master')
        git('reset', '--hard', 'origin/master')
        code = claim(fmt, key)
        if code:
            return code
        git('add', str(LEDGER))
        git('commit', '-m', 'chore: reserve Histold publication [skip ci]')
        if git('push', 'origin', 'HEAD:master', check=False) == 0:
            with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8') as env_file:
                env_file.write(f'PUBLISH_CLAIM={key}\n')
            return 0
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
