"""Copy one hosted run's scrubbed Studio HQ observations into an upload folder.

Usage: export_studio_events.py EVENTS_DIR OUT_DIR JOB_ID

Only events for exactly JOB_ID are exported, and only allowlisted fields. This
never fails the workflow: telemetry is best effort and never owns publishing.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.studio_events import scrub  # noqa: E402

EVENT_NAME = re.compile(r'[0-9a-f]{32}\.json')
MAX_EVENT_BYTES = 64 * 1024
MAX_EVENTS = 2000


def export(events: Path, out: Path, job_id: str) -> int:
    written = 0
    if not events.is_dir() or not job_id:
        return 0
    for path in sorted(events.glob('*.json')):
        if written >= MAX_EVENTS:
            break
        if not EVENT_NAME.fullmatch(path.name) or path.stat().st_size > MAX_EVENT_BYTES:
            continue
        try:
            row = scrub(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            continue
        if not row or row['event_id'] != path.stem or row['job_id'] != job_id or row['channel'] != 'histold':
            continue
        out.mkdir(parents=True, exist_ok=True)
        (out / path.name).write_text(json.dumps(row, ensure_ascii=False), encoding='utf-8')
        written += 1
    return written


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__.strip().splitlines()[2])
        return 0
    try:
        count = export(Path(argv[1]), Path(argv[2]), argv[3])
    except Exception as exc:  # noqa: BLE001 - never fail the publishing workflow
        print(f'Studio HQ telemetry export skipped ({type(exc).__name__}).')
        return 0
    print(f'Studio HQ telemetry: exported {count} events.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
