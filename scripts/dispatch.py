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
from src.growth import active_hours  # noqa: E402

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
    hours, source = active_hours(ROOT, load_config(), now)
    print(f'dispatch: {now:%H:%M} UTC; publish hours {hours} from {source}')

    ok = True
    if now.hour in hours and now.minute >= PUBLISH_WINDOW_MIN:
        print(f'  publish: missed this hour (now :{now.minute:02d}, window is :00-:{PUBLISH_WINDOW_MIN - 1})')
    elif now.hour in hours:
        if state.get('publish') == slot:
            print('  publish: already dispatched this hour')
        elif run_workflow('publish.yml', {'format': 'short', 'scheduled': 'true'}, args.dry_run):
            state['publish'] = slot
        else:
            ok = False
    else:
        print('  publish: not a publish hour')
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
