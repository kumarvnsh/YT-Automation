#!/usr/bin/env python3
"""Capture and report Facebook growth observations without publishing or paid API calls."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import base_dir
from src.experiments import capture_snapshots
from src.facebook_growth import build_report, EXPERIMENT


def read(path, fallback):
    return json.loads(path.read_text()) if path.exists() else fallback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=base_dir())
    parser.add_argument('--earnings', type=Path, help='Optional private earnings ledger; requires --output outside tracked experiment reports')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    data = args.root / 'data'
    if args.earnings and (not args.output or not args.output.resolve().is_relative_to((data / 'private').resolve())):
        parser.error('Earnings reports require --output under data/private to avoid exposing financial records')
    experiment = read(data / 'experiments' / f'{EXPERIMENT}.json', None)
    if experiment is None:
        parser.error('Facebook experiment manifest not found')
    posts = read(data / 'published_index.json', [])
    path = data / 'experiments' / f'{EXPERIMENT}-snapshots.json'
    snapshots = capture_snapshots(posts,experiment,{'facebook':read(data / 'meta_analytics.json',{}).get('facebook',{})},read(path,[]))
    earnings = read(args.earnings,{}).get('records',[]) if args.earnings else []
    report = build_report(posts,snapshots,earnings)
    output = args.output or data / 'experiments' / f'{EXPERIMENT}-report.json'
    for target, content in [(path,snapshots),(output,report)]:
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(content,indent=2)+'\n')
        temporary.replace(target)
    print(f'Wrote {output} ({len(report["observations"])} Facebook observations)')


if __name__ == '__main__':
    main()
