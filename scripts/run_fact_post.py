#!/usr/bin/env python3
"""Make or post one Histold fact image post on this laptop (see src/fact_posts.py).

  --make [--slot HOUR]   research, illustrate, render and check one post; during the approval period it is
                         sent to Telegram with Approve / Skip, otherwise it is posted straight away
  --post ID              post an approved card to Facebook and Instagram
  --retry                remake a post that waited for the Claude / Codex limits to reset

Started by scripts/dispatch.py at the fact-post hours, by Telegram buttons and by Studio HQ.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

HOME = Path(os.environ.get('HISTOLD_HOME', r'D:\histold'))
LOCK = HOME / 'control' / 'fact-post.lock'
LOG = HOME / 'control' / 'fact-posts.log'


def log(*parts):
    line = datetime.now().strftime('%Y-%m-%d %H:%M:%S ') + ' '.join(str(p) for p in parts)
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open('a', encoding='utf-8') as handle:
        handle.write(line + '\n')


def environment(cfg) -> None:
    """Same tools and sign-ins the local video runner uses (Meta token, hosting, Chromium, plans)."""
    from run_local_publish import tool_env
    env, _ = tool_env(cfg)
    os.environ.update({k: v for k, v in env.items() if isinstance(v, str)})


def notify_draft(entry: dict) -> None:
    from src import telegram_control
    text = (f"🖼 Sir, a Histold fact post is ready for your OK.\n\n{entry.get('headline')}\n\n{entry.get('fact')}\n\n"
            f"Sources: {', '.join(s.get('publisher') or '' for s in entry.get('sources') or [])}")
    status = telegram_control.notify_photo(Path(entry['card']), text, [
        ('fact_post', {'id': entry['id'], 'choice': 'approve'}, '✅ Post it'),
        ('fact_post', {'id': entry['id'], 'choice': 'skip'}, '⏭ Skip')])
    log('approval request', entry['id'], status)


def make(cfg, slot: str) -> int:
    from src import fact_posts, plan_ai, telegram_control
    try:
        entry = fact_posts.make_post(cfg, slot)
    except plan_ai.PlanUnavailable as exc:
        log('waiting for the plans:', exc)
        telegram_control.notify('⏳ Sir, your Claude and Codex limits are used up, so this Histold fact post will be made after they reset.')
        return 0
    except Exception as exc:  # noqa: BLE001
        log('fact post failed:', type(exc).__name__, exc)
        telegram_control.notify(f'❌ Sir, a Histold fact post could not be made ({str(exc)[:160]}). The next one runs as usual.')
        return 1
    log('made', entry['id'], entry['status'], '-', entry.get('headline'))
    if entry['status'] == 'awaiting_approval':
        notify_draft(entry)
        return 0
    return post(cfg, entry['id'])


def post(cfg, post_id: str) -> int:
    from src import fact_posts, telegram_control
    entry = fact_posts.publish(cfg, post_id)
    log('post', post_id, entry['status'], entry.get('error') or '')
    if entry['status'] == 'posted':
        return 0
    telegram_control.notify(f"⚠️ Sir, the fact post \"{entry.get('headline')}\" was {entry['status'].replace('_', ' ')}: {entry.get('error')}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument('--make', action='store_true')
    group.add_argument('--post')
    group.add_argument('--retry', action='store_true')
    ap.add_argument('--slot', default='')
    args = ap.parse_args()
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(60):  # a post being made blocks a second one; a quick approval waits its turn
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.time() - LOCK.stat().st_mtime > 3600:
                LOCK.unlink(missing_ok=True)
                continue
            if not args.post:
                log('another fact post is being made')
                return 0
            time.sleep(10)
    else:
        return 1
    try:
        from src.config import load_config
        cfg = load_config()
        environment(cfg)
        if args.post:
            return post(cfg, args.post)
        if args.retry:
            from src import fact_posts
            waiting = [r for r in fact_posts.load_queue() if r.get('status') == 'waiting_plans']
            for row in waiting:
                fact_posts.update(row['id'], status='replaced')
            return make(cfg, waiting[-1].get('slot') or '' if waiting else '')
        return make(cfg, args.slot)
    finally:
        os.close(fd)
        LOCK.unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
