#!/usr/bin/env python3
"""Import a normalized Facebook CSV, optionally mapping the export's column names."""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import base_dir
from src.facebook_monetization import parse_export, merge_records, build_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    parser.add_argument('--mapping', type=Path, help='JSON mapping canonical names to actual CSV headers')
    parser.add_argument('--root', type=Path, default=base_dir())
    args = parser.parse_args()
    mapping = json.loads(args.mapping.read_text()) if args.mapping else None
    target = args.root / 'data/private'
    ledger_path = target / 'facebook-monetization.json'
    existing = json.loads(ledger_path.read_text()) if ledger_path.exists() else {'schema_version': 1, 'records': []}
    with args.csv.open(encoding='utf-8-sig', newline='') as source:
        incoming = parse_export(source, mapping)
    provenance = {'source_file': args.csv.name, 'source_sha256': hashlib.sha256(args.csv.read_bytes()).hexdigest(),
                  'imported_at': datetime.now(timezone.utc).isoformat()}
    rows = merge_records(existing['records'], [{**row, **provenance} for row in incoming])
    index_path = args.root / 'data/published_index.json'
    posts = json.loads(index_path.read_text()) if index_path.exists() else []
    report = build_report(rows, posts)
    target.mkdir(parents=True, exist_ok=True)
    for path, content in [(ledger_path, {'schema_version': 1, 'records': rows}),
                          (target / 'facebook-monetization-report.json', report)]:
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(content, indent=2) + '\n')
        temporary.replace(path)
    print(f'Imported {len(incoming)} rows; {len(rows)} saved. Report: {target / "facebook-monetization-report.json"}')


if __name__ == '__main__':
    main()
