#!/usr/bin/env python3
"""Merge N per-channel JSON exports into one file the dashboard fetches once.

Usage:
  python scripts/merge_channel_json.py --labels histold,medimyth --out data/analytics.json \\
      data/analytics.json channels/medimyth/data/analytics.json

Output shape: {"channels": {"<label>": <parsed input file>, ...}}
Standalone (no src/ imports) so it works with no env/config setup.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="Per-channel JSON files, in the same order as --labels.")
    parser.add_argument("--labels", required=True, help="Comma-separated labels, one per input file.")
    parser.add_argument("--out", required=True, help="Merged output path.")
    args = parser.parse_args()

    labels = [l.strip() for l in args.labels.split(",")]
    if len(labels) != len(args.inputs):
        print(f"ERROR: {len(labels)} labels but {len(args.inputs)} input files.", file=sys.stderr)
        return 2

    merged: dict[str, dict] = {}
    for label, path in zip(labels, args.inputs):
        p = Path(path)
        if not p.exists():
            print(f"  (skipping {label}: {p} not found)")
            continue
        merged[label] = json.loads(p.read_text(encoding="utf-8"))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"channels": merged}, indent=2), encoding="utf-8")
    print(f"Wrote {out_path} with channels: {', '.join(merged)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
