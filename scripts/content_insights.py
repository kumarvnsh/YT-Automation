#!/usr/bin/env python3
"""Ask local Qwen to prioritize measured content findings; save a private dashboard report."""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from build_dashboard import collect
from weekly_review import ensure_server, ollama
from src.insights import evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=ROOT)
    parser.add_argument('--if-stale', action='store_true')
    parser.add_argument('--snapshot', type=Path, help='server-collected fresh dashboard snapshot')
    args = parser.parse_args()
    target = args.data_root / 'data/private/content-insights.json'
    now = datetime.now(timezone.utc)
    if args.if_stale and target.exists():
        try:
            if now - datetime.fromisoformat(json.loads(target.read_text(encoding='utf-8'))['generated']) < timedelta(hours=24):
                print('content insights: report is fresh'); return 0
        except (ValueError, KeyError):
            pass
    data = json.loads(args.snapshot.read_text(encoding='utf-8')) if args.snapshot else collect(
        now, args.data_root.parent / 'strategist.log', args.data_root)
    facts = evidence(data['videos'], now, like_rate_only=True)
    if facts['fb_coverage'] < 10:
        raise RuntimeError('Need at least ten mature videos with Facebook data.')
    host = os.environ.get('OLLAMA_HOST_URL', 'http://127.0.0.1:11434')
    model = os.environ.get('REVIEW_MODEL', 'qwen3:8b')
    ensure_server(host)
    def ids(key):
        values = [g['id'] for g in facts[key]]
        return {'type': 'array', 'items': {'type': 'string', **({'enum': values} if values else {})},
                'maxItems': 3 if values else 0}
    schema = {'type': 'object', 'properties': {
        'working_ids': ids('working'), 'weak_ids': ids('not_working'),
        'title_hypothesis': {'type': 'string', 'maxLength': 280},
        'next_test': {'type': 'string', 'enum': ['Compare concrete objects vs broad topics',
            'Compare named-place questions vs general questions', 'Hold the current mix and gather more data']}},
        'required': ['working_ids', 'weak_ids', 'title_hypothesis', 'next_test']}
    prompt = ('Review Histold history Shorts for its owner. Judge changes by Facebook likes per view. '
              'Select up to three working and three weak evidence IDs from the supplied lists. Prefer useful patterns '
              'with adequate samples. Exact numbers are already computed. Do not invent numbers, revenue, causes, '
              'viewer locations, new historical facts or titles. Give one short title-pattern hypothesis from the '
              'best/weak titles; explicitly call it a hypothesis, not a causal finding. Choose a next experiment. '
              'Weak means below this channel median, not objectively bad. Evidence:\n' + json.dumps(facts, separators=(',', ':')))
    start = time.monotonic()
    response = ollama(host, '/api/generate', {'model': model, 'prompt': prompt, 'stream': False,
                      'think': False, 'format': schema,
                      'options': {'temperature': 0.1, 'num_ctx': 8192, 'num_predict': 700}}, timeout=240)
    selected = json.loads(response['response'])
    allowed = {g['id']: g for g in facts['groups']}
    for key, direction in [('working_ids', 1), ('weak_ids', -1)]:
        values = selected.get(key, [])
        if not isinstance(values, list) or len(values) > 3:
            raise ValueError('Invalid model selection')
        for value in values:
            group = allowed.get(value)
            if not group or not group['enough_data'] or direction * (group['fb_vs_baseline_pct'] or 0) <= 0:
                raise ValueError('Model cited evidence outside the validated findings')
    result = {'generated': now.isoformat(), 'model': model, 'seconds': round(time.monotonic() - start, 1),
              'evidence': facts, 'interpretation': selected}
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix('.tmp')
    tmp.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    tmp.replace(target)
    print(json.dumps({'report': str(target), 'model_seconds': result['seconds'],
                      'mature_videos': facts['mature_videos'], 'selection': selected}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
