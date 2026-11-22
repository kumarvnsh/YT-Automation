#!/usr/bin/env python3
"""Histold Telegram bot: long-polls Telegram and handles the owner's commands and approval buttons.

Runs as the owner (no admin rights) from the "Histold Telegram" scheduled task, which restarts it
every 5 minutes if it is not running. It exits when its own code changes so updates load.
Usage:
    python scripts/telegram_bot.py           run the bot
    python scripts/telegram_bot.py --pair    print a one-time pairing code (10 minutes)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
from src import telegram_control as tc  # noqa: E402

WATCHED = ['scripts/telegram_bot.py', 'src/telegram_control.py', 'src/telegram_views.py', 'src/bot_findings.py']
LOG = tc.CONTROL / 'telegram-bot.log'
OFFSET = tc.CONTROL / 'telegram-offset.json'


def log(*parts):
    tc.CONTROL.mkdir(parents=True, exist_ok=True)
    line = datetime.now().strftime('%Y-%m-%d %H:%M:%S') + ' ' + ' '.join(str(p) for p in parts)
    with LOG.open('a', encoding='utf-8') as handle:
        handle.write(line + '\n')
    if LOG.stat().st_size > 2_000_000:
        LOG.replace(LOG.with_suffix('.old.log'))


def digest() -> str:
    return hashlib.sha256(b''.join((CODE / f).read_bytes() for f in WATCHED)).hexdigest()


def single_instance():
    """Hold a lock for the life of the process; returns None if another bot is running."""
    import msvcrt
    tc.CONTROL.mkdir(parents=True, exist_ok=True)
    handle = (tc.CONTROL / 'telegram-bot.lock').open('a+b')
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def pair() -> int:
    config = tc.load_config()
    if not config.get('token'):
        print('No bot token yet. Run scripts\\setup_telegram.ps1 first.')
        return 1
    code = f'{secrets.randbelow(10**6):06d}'
    config.update(pair_code=code, pair_expires=time.time() + 600)
    config.pop('owner_id', None)
    tc.save_config(config)
    print(f'In Telegram, open your bot and send:  /pair {code}\nThe code works once and expires in 10 minutes.')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--pair', action='store_true')
    args = ap.parse_args()
    if args.pair:
        return pair()
    lock = single_instance()
    if lock is None:
        return 0
    started, last_check, backoff = digest(), time.monotonic(), 5
    log('bot started')
    try:  # command menu and start-screen text; refreshed whenever new code loads
        tc.Telegram(tc.load_config().get('token', '')).setup_profile()
    except Exception as exc:  # cosmetic: never stop the bot
        log('profile setup failed', type(exc).__name__, str(exc)[:200])
    try:
        offset = json.loads(OFFSET.read_text()).get('offset', 0)
    except (OSError, ValueError):
        offset = 0
    while True:
        if time.monotonic() - last_check > 600:
            last_check = time.monotonic()
            if digest() != started:
                log('code changed; exiting so the scheduled task restarts the new version')
                return 0
        config = tc.load_config()
        if not config.get('token'):
            log('no token configured; waiting')
            time.sleep(60)
            continue
        try:
            telegram = tc.Telegram(config['token'])
            controller = tc.Controller(telegram, config)
            for update in telegram.updates(offset):
                # Advance and persist the offset first: a crash must never replay a button press.
                offset = max(offset, int(update['update_id']) + 1)
                OFFSET.write_text(json.dumps({'offset': offset}))
                try:
                    controller.handle(update)
                except Exception as exc:
                    log('update failed', type(exc).__name__, str(exc)[:300])
            backoff = 5
        except Exception as exc:
            log('poll failed', type(exc).__name__, str(exc)[:300])
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)


if __name__ == '__main__':
    raise SystemExit(main())
