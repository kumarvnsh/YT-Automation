"""Creative split tests: one short style instruction is tested on alternate Histold Shorts.

data/creative_guidance.json (committed; the Operator writes it, the pipeline reads it):
  active     the test currently being assigned: {id, instruction, hypothesis, start_slot, posts, started}
  measuring  tests whose posts are all assigned, waiting for their videos to mature
  learned    instructions that beat their control; every Short's script prompt gets them
  history    concluded tests with their measured result

Within a test window, even offsets get the instruction ("variant") and odd offsets do not
("control"), so both arms are posted under the same conditions. A losing instruction is simply
dropped: no code ever changes, so there is nothing to revert.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

PATH = 'data/creative_guidance.json'
POSTS = 20              # about one week; 10 per arm
MIN_PER_ARM = 6
PROMOTE_LIFT = 0.005    # minimum lift of 0.5 percentage points in mean like rate
MAX_LEARNED = 12
MAX_CHARS = 300
FORBIDDEN = re.compile(r'(https?://|www\.|ignore|disregard|invent|fabricat|exaggerat|made[- ]up|clickbait|lie\b|'
                       r'mislead|without (sources|evidence)|skip (the )?(research|sources|claim)|claim_ids|'
                       r'youtube\.com|subscribe|comment yes)', re.I)


def empty() -> dict:
    return {'active': None, 'measuring': [], 'learned': [], 'history': []}


def load(root: Path) -> dict:
    try:
        data = json.loads((Path(root) / PATH).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return empty()
    base = empty()
    base.update({k: v for k, v in data.items() if k in base})
    return base


def arm(guidance: dict, ordinal: int) -> dict | None:
    test = guidance.get('active')
    for candidate in [test] + list(guidance.get('measuring') or []):
        if candidate and candidate['start_slot'] <= ordinal < candidate['start_slot'] + candidate['posts']:
            variant = (ordinal - candidate['start_slot']) % 2 == 0
            return {'id': candidate['id'], 'arm': 'variant' if variant else 'control',
                    'instruction': candidate['instruction'] if variant else None}
    return None


def prompt_instructions(guidance: dict, assigned: dict | None) -> list[str]:
    lines = [item['instruction'] for item in guidance.get('learned') or []]
    if assigned and assigned.get('instruction'):
        lines.append(assigned['instruction'])
    return lines


def prompt_block(instructions: list[str]) -> str:
    if not instructions:
        return ''
    return ('\nCreative style guidance learned from this audience (style only; it never overrides accuracy, '
            'sourcing, structure or length rules above):\n- ' + '\n- '.join(instructions))


def validate_instruction(text: str, guidance: dict) -> list[str]:
    problems = []
    text = (text or '').strip()
    if not 20 <= len(text) <= MAX_CHARS:
        problems.append(f'instruction must be 20-{MAX_CHARS} characters')
    if '\n' in text:
        problems.append('instruction must be one line')
    if FORBIDDEN.search(text):
        problems.append('instruction touches accuracy, sourcing, links or engagement bait')
    seen = [t['instruction'].lower() for t in (guidance.get('learned') or []) + (guidance.get('history') or [])
            + (guidance.get('measuring') or []) + ([guidance['active']] if guidance.get('active') else [])]
    if text.lower() in seen:
        problems.append('instruction was already tested')
    return problems


def window_full(guidance: dict, latest_slot: int) -> bool:
    test = guidance.get('active')
    return test is None or latest_slot >= test['start_slot'] + test['posts'] - 1


def start_test(guidance: dict, instruction: str, hypothesis: str, start_slot: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    out = json.loads(json.dumps(guidance))
    if out.get('active'):
        out['measuring'].append(out['active'])
    out['active'] = {'id': 'ct-' + now.strftime('%Y%m%d-%H%M'), 'instruction': instruction.strip(),
                     'hypothesis': (hypothesis or '').strip()[:300], 'start_slot': start_slot, 'posts': POSTS,
                     'started': now.isoformat()}
    return out


def judge(variant: list[float], control: list[float]) -> tuple[str, float | None]:
    """Return the decision and the absolute difference in mean like rates."""
    if len(variant) < MIN_PER_ARM or len(control) < MIN_PER_ARM:
        return 'wait', None
    lift = sum(variant) / len(variant) - sum(control) / len(control)
    return ('promote' if lift >= PROMOTE_LIFT else 'drop'), round(lift, 3)


def conclude(guidance: dict, test_id: str, decision: str, stats: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    out = json.loads(json.dumps(guidance))
    test = next(t for t in out['measuring'] if t['id'] == test_id)
    out['measuring'] = [t for t in out['measuring'] if t['id'] != test_id]
    result = {**test, 'decision': decision, 'concluded': now.isoformat(), **stats}
    out['history'] = (out['history'] + [result])[-50:]
    if decision == 'promote':
        out['learned'] = (out['learned'] + [{'id': test['id'], 'instruction': test['instruction'],
                                             'lift': stats.get('lift'), 'promoted': now.isoformat()}])[-MAX_LEARNED:]
    return out
