#!/usr/bin/env python3
"""Publish-hour gate: exit 0 if a scheduled publish may run this UTC hour, 3 if not.

cron-job.org fires publish.yml hourly; only the growth plan's hours get through (see
src/growth.py). Fails closed: any error falls back to the default hours, never to "run".
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GO, SKIP = 0, 3
LATE_MIN = 40  # dispatches land at :00; a run starting this late is a stray or a retry


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--hour', type=int, help='UTC hour to check (default: now)')
    ap.add_argument('--minute', type=int, help='minute past the hour (default: now)')
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    hour = now.hour if args.hour is None else args.hour
    minute = now.minute if args.minute is None else args.minute
    if minute >= LATE_MIN:
        print(f'publish-hour gate: {hour:02d}:{minute:02d} UTC -> skip; scheduled runs must start before :{LATE_MIN}')
        return SKIP
    try:
        from src.config import load_config
        from src.growth import active_hours
        hours, source = active_hours(ROOT, load_config())
    except Exception as exc:  # noqa: BLE001 - the gate must always reach a decision
        from src.growth import FALLBACK_HOURS
        hours, source = FALLBACK_HOURS, f'built-in fallback ({type(exc).__name__}: {exc})'
    verdict = 'publish' if hour in hours else 'skip'
    print(f'publish-hour gate: {hour:02d}:00 UTC -> {verdict}; hours {hours} from {source}')
    return GO if hour in hours else SKIP


if __name__ == '__main__':
    raise SystemExit(main())
