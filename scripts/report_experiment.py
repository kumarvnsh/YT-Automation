#!/usr/bin/env python3
"""Save immutable 24/72/168h observations from existing analytics exports."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import base_dir
from src.experiments import capture_snapshots


def read(path, fallback):
    return json.loads(path.read_text()) if path.exists() else fallback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=base_dir())
    parser.add_argument('--experiment', default='histold-six-reels')
    args = parser.parse_args()
    data = args.root / 'data'
    experiment = read(data / 'experiments' / f'{args.experiment}.json', None)
    if experiment is None:
        parser.error('Experiment manifest not found')
    yt = read(data / 'analytics.json', {})
    yt = yt.get('channels', {}).get('histold', yt)
    meta = read(data / 'meta_analytics.json', {})
    snapshots_path = data / 'experiments' / f'{args.experiment}-snapshots.json'
    snapshots = capture_snapshots(read(data / 'published_index.json', []), experiment,
                                 {'youtube':yt, **meta}, read(snapshots_path, []))
    snapshots_path.write_text(json.dumps(snapshots, indent=2) + '\n')
    report = {'experiment':experiment, 'snapshots':snapshots,
              'limitations':['Unavailable metrics are null, never zero.',
                              'Late snapshots are cumulative counts at actual age, not reconstructed historical counts.',
                              'No cross-platform pooling; two Reels per format are exploratory.']}
    path = data / 'experiments' / f'{args.experiment}-report.json'
    path.write_text(json.dumps(report, indent=2) + '\n')
    print(f'Wrote {path} ({len(snapshots)} observations)')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
