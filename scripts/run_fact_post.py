#!/usr/bin/env python3
"""Make or post one Histold fact image post on this laptop (see src/fact_posts.py).

  --make [--slot HOUR]   research, illustrate, render and check one post; during the approval period it is
                         sent to Telegram with Approve / Skip, otherwise it is posted straight away
  --post ID              post an approved card to Facebook and Instagram
  --retry                remake the post that waited for the plan limits to reset (its fact is kept)
  --retry-post ID        remake one failed or waiting post, keeping its verified fact (Telegram / Studio HQ button)

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


def retry_button(post_id: str) -> list:
    return [('fact_post_retry', {'id': post_id}, '🔁 Retry')]


def notify_draft(entry: dict) -> None:
    from src import telegram_control, telegram_views
    status = telegram_control.notify_photo(Path(entry['card']), telegram_views.post_caption(entry), [
        ('fact_post', {'id': entry['id'], 'choice': 'approve'}, '✅ Post it'),
        ('fact_post', {'id': entry['id'], 'choice': 'skip'}, '⏭ Skip')], html=True)
    log('approval request', entry['id'], status)


def _latest(fact_posts) -> str | None:
    rows = [r for r in fact_posts.load_queue() if r.get('status') in ('failed', 'waiting_plans')]
    return rows[-1]['id'] if rows else None


def make(cfg, slot: str, resume: str | None = None) -> int:
    from src import fact_posts, plan_ai, telegram_control
    try:
        entry = fact_posts.make_post(cfg, slot, resume=resume)
    except plan_ai.PlanUnavailable as exc:
        log('waiting for the plans:', exc)
        what = 'image makers (Gemini and Codex)' if 'image provider' in str(exc) else 'Claude and Codex limits'
        post = _latest(fact_posts)
        telegram_control.notify(f'⏳ Sir, the {what} are used up, so this Histold fact post waits for them to reset. '
                                'Tap Retry to try again sooner.', retry_button(post) if post else None)
        return 0
    except Exception as exc:  # noqa: BLE001
        log('fact post failed:', type(exc).__name__, exc)
        post = _latest(fact_posts)
        telegram_control.notify(f'❌ Sir, a Histold fact post could not be made ({str(exc)[:160]}). '
                                'Tap Retry to make it again; the next one also runs as usual.', retry_button(post) if post else None)
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
    group.add_argument('--retry-post')
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
            if not (args.post or args.retry_post):
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
        if args.retry_post:
            return make(cfg, '', resume=args.retry_post)
        if args.retry:
            from src import fact_posts
            waiting = [r for r in fact_posts.load_queue() if r.get('status') == 'waiting_plans']
            if not waiting:
                return make(cfg, '')
            code = 0
            for row in waiting:  # oldest first, each keeping its own verified fact
                code = make(cfg, row.get('slot') or '', resume=row['id'])
                still = next((r for r in fact_posts.load_queue() if r['id'] == row['id']), {})
                if still.get('status') == 'waiting_plans':
                    break  # the image makers ran out again: the rest keep waiting for the next retry
            return code
        return make(cfg, args.slot)
    finally:
        os.close(fd)
        LOCK.unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
