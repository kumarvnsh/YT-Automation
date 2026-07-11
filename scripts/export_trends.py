#!/usr/bin/env python3
"""Export today's trend + on-this-day signals to data/trends.json.

Usage:
  python scripts/export_trends.py [--config <path>] [--label histold]

Reuses src.trends.gather_signals(cfg) — the same trend-aware signals the
script generator already uses internally — just persists them for the
dashboard too.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config, base_dir  # noqa: E402
from src import trends  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--label", default="channel", help="Channel key for the merged JSON, e.g. histold.")
    args = parser.parse_args()

    cfg = load_config(args.config)
    signals = trends.gather_signals(cfg)
    out = {"channel": args.label, **signals}

    out_path = base_dir() / "data" / "trends.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
