#!/usr/bin/env python3
"""Laptop dispatcher (hourly): start publish.yml at the growth plan's hours, analytics.yml every hour.

Replaces cron-job.org. Uses the GitHub CLI signed in on this laptop (`gh auth login`);
API keys stay in GitHub's secret store. Each target fires at most once per UTC hour
(state file outside the repo), and the workflow's publish-hour gate re-checks the plan,
so a double or stray dispatch cannot publish an extra video.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.config import load_config  # noqa: E402
from src.growth import scheduled_format  # noqa: E402

DEFAULT_STATE = ROOT.parent / 'dispatch_state.json'
PUBLISH_WINDOW_MIN = 20   # a late catch-up run (e.g. after sleep) must not publish mid-hour


def gh_exe() -> str:
    found = os.environ.get('GH_EXE') or shutil.which('gh')
    if found:
        return found
    for candidate in (r'C:\Program Files\GitHub CLI\gh.exe', '/usr/local/bin/gh', '/opt/homebrew/bin/gh'):
        if Path(candidate).exists():
            return candidate
    raise SystemExit('dispatch: GitHub CLI (gh) not found')


def repo_slug() -> str:
    url = subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=ROOT, capture_output=True, text=True).stdout
    m = re.search(r'github\.com[:/]([^/]+/[^/.\s]+)', url)
    if not m:
        raise SystemExit(f'dispatch: cannot read the GitHub repo from origin ({url.strip()!r})')
    return m.group(1)


def run_workflow(workflow: str, fields: dict, dry_run: bool) -> bool:
    cmd = [gh_exe(), 'workflow', 'run', workflow, '-R', repo_slug(), '--ref', 'master']
    for key, value in fields.items():
        cmd += ['-f', f'{key}={value}']
    if dry_run:
        print('  would run: ' + ' '.join(cmd[1:]))
        return True
    out = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, 'GH_PROMPT_DISABLED': '1'})
    print(f"  {workflow}: {'dispatched' if out.returncode == 0 else 'FAILED'} {(out.stdout + out.stderr).strip()}")
    return out.returncode == 0


SWITCH_FILE = Path(os.environ.get('HISTOLD_HOME', r'D:\histold')) / 'control' / 'local-production.json'


def local_enabled(local: dict) -> bool:
    """Studio HQ's switch (Histold > Live) wins over config.yaml, so it applies without a git push."""
    try:
        switch = json.loads(SWITCH_FILE.read_text(encoding='utf-8'))
        if isinstance(switch, dict) and isinstance(switch.get('enabled'), bool):
            return switch['enabled']
    except (OSError, ValueError):
        pass
    return bool(local.get('enabled'))


def local_publish(fmt: str, slot: str, dry_run: bool) -> bool:
    """Start scripts/run_local_publish.py when local production is on; False means use the cloud."""
    local = load_config().get('local_production', {}) or {}
    if not local_enabled(local):
        return False
    python = Path(local.get('python') or r'D:\YT-Automation-env\histold-local\Scripts\python.exe')
    if not python.is_file():
        print(f'  publish: local production is on but {python} is missing; using the cloud')
        return False
    cmd = [str(python), str(ROOT / 'scripts' / 'run_local_publish.py'), '--format', fmt, '--slot', slot]
    if dry_run:
        print('  would run locally: ' + ' '.join(cmd))
        return True
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0
    subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags)
    print(f'  publish: started locally ({fmt}, slot {slot})')
    return True


RETRY_FILE = Path(os.environ.get('HISTOLD_HOME', r'D:\histold')) / 'control' / 'local-publish-retry.json'


def retry_waiting_video(dry_run: bool, now: float | None = None) -> bool:
    """A video that waited for the Claude/Codex limits to reset: start it once the reset has passed."""
    try:
        waiting = json.loads(RETRY_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    if not isinstance(waiting, dict) or waiting.get('format') not in ('short', 'long'):
        return False
    if (now or datetime.now(timezone.utc).timestamp()) < float(waiting.get('retry_at') or 0):
        print(f"  waiting video: {waiting['format']} retries after "
              f"{datetime.fromtimestamp(float(waiting['retry_at'])):%H:%M} (plans resetting)")
        return False
    local = load_config().get('local_production', {}) or {}
    python = Path(local.get('python') or r'D:\YT-Automation-env\histold-local\Scripts\python.exe')
    if not local_enabled(local) or not python.is_file():
        return False
    cmd = [str(python), str(ROOT / 'scripts' / 'run_local_publish.py'), '--format', waiting['format'], '--retry']
    if dry_run:
        print('  would retry locally: ' + ' '.join(cmd))
        return True
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0
    subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags)
    print(f"  waiting video: {waiting['format']} started on the plans again")
    return True


def fact_post(now: datetime, slot: str, state: dict, dry_run: bool) -> bool:
    """Start one Histold fact image post at its hours, or remake one that waited for the plans."""
    cfg = load_config()
    posts = cfg.get('fact_posts', {}) or {}
    local = cfg.get('local_production', {}) or {}
    python = Path(local.get('python') or r'D:\YT-Automation-env\histold-local\Scripts\python.exe')
    try:  # Studio HQ's Posts switch wins over config.yaml
        switch = json.loads((SWITCH_FILE.parent / 'fact-posts.json').read_text(encoding='utf-8'))
        enabled = switch['enabled'] if isinstance(switch.get('enabled'), bool) else bool(posts.get('enabled'))
    except (OSError, ValueError, AttributeError):
        enabled = bool(posts.get('enabled'))
    if not enabled or not python.is_file():
        return False
    mode = None
    if now.hour in (posts.get('hours_utc') or []) and now.minute < PUBLISH_WINDOW_MIN and state.get('fact_post') != slot:
        mode = ['--make', '--slot', slot]
    else:
        try:
            sys.path.insert(0, str(ROOT))
            from src.fact_posts import load_queue
            if any(r.get('status') == 'waiting_plans' and float(r.get('retry_at') or 0) <= now.timestamp() for r in load_queue()):
                mode = ['--retry']
        except Exception:  # noqa: BLE001
            mode = None
    if not mode:
        return False
    cmd = [str(python), str(ROOT / 'scripts' / 'run_fact_post.py'), *mode]
    if dry_run:
        print('  would make a fact post: ' + ' '.join(cmd))
        return True
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0
    subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    if mode[0] == '--make':
        state['fact_post'] = slot
    print('  fact post: started ' + ' '.join(mode))
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--dry-run', action='store_true', help='print what would be dispatched')
    ap.add_argument('--state', type=Path, default=DEFAULT_STATE)
    ap.add_argument('--no-analytics', action='store_true')
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    slot = now.strftime('%Y-%m-%dT%H')
    try:
        state = json.loads(args.state.read_text())
    except (OSError, ValueError):
        state = {}
    fmt, source = scheduled_format(ROOT, load_config(), now)
    print(f'dispatch: {now:%H:%M} UTC; format {fmt or "none"} from {source}')

    ok = True
    if fmt and now.minute >= PUBLISH_WINDOW_MIN:
        print(f'  publish: missed this hour (now :{now.minute:02d}, window is :00-:{PUBLISH_WINDOW_MIN - 1})')
    elif fmt:
        if state.get('publish') == slot:
            print('  publish: already dispatched this hour')
        elif local_publish(fmt, slot, args.dry_run):
            # Made on this laptop with the owner's plans; it falls back to the paid cloud run itself.
            state['publish'] = slot
        elif run_workflow('publish.yml', {'format': fmt, 'scheduled': 'true', 'dispatch_slot': slot}, args.dry_run):
            state['publish'] = slot
        else:
            ok = False
    else:
        print('  publish: not a publish hour')
    retry_waiting_video(args.dry_run)
    fact_post(now, slot, state, args.dry_run)
    if not args.no_analytics:
        if state.get('analytics') == slot:
            print('  analytics: already dispatched this hour')
        elif run_workflow('analytics.yml', {}, args.dry_run):
            state['analytics'] = slot
        else:
            ok = False
    if not args.dry_run:
        state['last_run'] = now.isoformat()
        args.state.write_text(json.dumps(state, indent=2) + '\n')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
