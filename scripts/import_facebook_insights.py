#!/usr/bin/env python3
"""Import normalized manual Facebook Reel observations (no network calls)."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import base_dir
from src.facebook_growth import parse_insights, capture_manual, parse_native_export, native_baseline, EXPERIMENT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    parser.add_argument('--root', type=Path, default=base_dir())
    parser.add_argument('--native-export', action='store_true', help='Native Meta content CSV: private lifetime baseline only, no age snapshots')
    parser.add_argument('--observed-at', help='Explicit known export observation timestamp with timezone; otherwise unknown')
    args = parser.parse_args()
    data = args.root / 'data'
    if args.native_export:
        import shutil
        with args.csv.open(encoding='utf-8-sig', newline='') as stream:
            rows = parse_native_export(stream)
        report = native_baseline(rows,args.observed_at)
        target = data / 'private/facebook-baseline'
        target.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(args.csv,target / 'source.csv')
        report['source_sha256'] = hashlib.sha256(args.csv.read_bytes()).hexdigest()
        (target / 'records.json').write_text(json.dumps(rows,indent=2)+'\n')
        (target / 'report.json').write_text(json.dumps(report,indent=2)+'\n')
        (target / 'report.md').write_text(native_markdown(report))
        print(f'Imported {len(rows)} native rows; private baseline: {target}. No snapshots created.')
        return
    manifest = json.loads((data / 'experiments' / f'{EXPERIMENT}.json').read_text())
    posts = json.loads((data / 'published_index.json').read_text())
    with args.csv.open(encoding='utf-8-sig', newline='') as stream:
        rows = parse_insights(stream)
    for row in rows:
        row['source_sha256'] = hashlib.sha256(args.csv.read_bytes()).hexdigest()
    ledger = data / 'private/facebook-insights.json'
    old = json.loads(ledger.read_text()) if ledger.exists() else []
    merged = {(row['media_id'],row['observed_at']):row for row in old}
    for row in rows:
        key = (row['media_id'],row['observed_at'])
        if key in merged and any(merged[key].get(field) != value for field, value in row.items() if field != 'source_sha256'):
            raise ValueError('Observation already imported with different values; immutable records cannot be overwritten')
        merged.setdefault(key, row)
    path = data / 'experiments' / f'{EXPERIMENT}-snapshots.json'
    existing = json.loads(path.read_text()) if path.exists() else []
    snapshots = capture_manual(posts, manifest, rows, existing)
    for target, content in [(ledger,list(merged.values())),(path,snapshots)]:
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(content,indent=2)+'\n')
        temporary.replace(target)
    print(f'Imported {len(rows)} observations; captured {len(snapshots)-len(existing)} new age snapshots. Period-only or unmatched rows remain in the private ledger.')


def native_markdown(report):
    totals = report['totals']
    lines = ['# Facebook native export baseline', '',
             f"Metric scopes: {', '.join(report['metric_scopes'])}. Capture timestamp: {report['observed_at'] or 'unknown'}. Publish timezone: unknown.",
             f"Selected publish times: {report['selected_publish_local_range'][0]} through {report['selected_publish_local_range'][1]}.", '',
             f"Reels: {totals['sample_size']}; lifetime views: {totals['views']}; shares: {totals['shares']}; net follows: {totals['net_follows']}.",
             f"Watch seconds: {totals['seconds_viewed']}; median per-Reel average watch seconds: {totals['median_average_seconds_viewed']}.",
             f"Approximate earnings USD: {totals['approximate_earnings_usd']} (estimated, not payouts).", '',
             '| Duration seconds | Reels | Views | Median views | Shares | Net follows | Shares/1000 views |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for row in report['duration_groups']:
        rate = f"{row['shares_per_1000_views']:.2f}" if row['shares_per_1000_views'] is not None else 'unavailable'
        lines.append(f"| {row['duration_bucket']} | {row['sample_size']} | {row['views']} | {row['median_views']} | {row['shares']} | {row['net_follows']} | {rate} |")
    lines += ['', '## Highest-view Reels', '', '| Title | Views | Duration | Average watch seconds | Shares | Net follows |', '|---|---:|---:|---:|---:|---:|']
    for row in report['top_reels_by_views']:
        title = row['title'].replace('|',' / ')
        lines.append(f"| {title} | {row['views']} | {row['duration_seconds']} | {row['average_seconds_viewed']} | {row['shares']} | {row['net_follows']} |")
    lines += ['', '## Limits', ''] + ['- '+item for item in report['limitations']]
    return '\n'.join(lines)+'\n'


if __name__ == '__main__':
    main()
