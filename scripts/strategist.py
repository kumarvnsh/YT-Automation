#!/usr/bin/env python3
"""Recompute data/strategy.json from the last 4 weeks of cross-platform metrics and print a summary."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import bot_findings  # noqa: E402
from src.config import load_config  # noqa: E402
from src.strategy import WINDOW_DAYS, build, peaks  # noqa: E402


def main() -> int:
    cfg = load_config()
    since = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS + 3)).date().isoformat()
    index = json.loads((ROOT / 'data/published_index.json').read_text())
    # Bot Office findings: drop per-platform numbers the data confirms are pipeline faults.
    best, masked = bot_findings.mask_suspect_metrics(ROOT.parent / 'bot-workspace', 'histold', index, peaks(ROOT, since))
    strategy = build(cfg, index, best)
    strategy['masked_metrics'] = masked
    (ROOT / 'data/strategy.json').write_text(json.dumps(strategy, indent=2) + '\n')
    print(f"strategy: {strategy['videos_scored']} videos scored")
    if masked:
        print(f'  ignored {len(masked)} faulty metric(s) flagged by Bot Office: ' + ', '.join(f"{m['video_id']}/{m['platform']}" for m in masked))
    for dim, weights in strategy['weights'].items():
        top = sorted(weights.items(), key=lambda kv: -kv[1])
        print(f'  {dim}: ' + ', '.join(f'{k} {v:.0%}' for k, v in top[:4]) + (' ...' if len(top) > 4 else ''))
    print('  best:  ' + ' | '.join(strategy['winners'][:3]))
    if strategy['losers']:
        print('  worst: ' + ' | '.join(strategy['losers'][-3:]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
