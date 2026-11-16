"""Actions the dashboard (you, or the local AI on your behalf) may take, and their checks.

The local AI can only PROPOSE one of these; the dashboard shows the proposal and runs it
only after you press Confirm, and `validate` runs again at that point. Nothing here can
change how many videos a day are made (growth.posts_per_day), config.yaml, or publish
anything. Changes are written to master through the GitHub API (GitHub CLI signed in on
the laptop), so they are atomic commits and never collide with the laptop's clone.
"""
from __future__ import annotations

import base64
import json
import math
import re
import subprocess
from datetime import datetime, timedelta, timezone

from .growth import PLAN, problem, settings
from .strategy import MAX_BOOST, OVERRIDES

DIMENSIONS = ('series', 'region', 'duration_variant')
MAX_PIN_DAYS = 30

ACTIONS = {
    'pin_hours': {
        'description': 'Pin the daily publish hours (UTC) so the growth agent stops changing them for some days. '
                       'The number of hours must equal posts_per_day.',
        'parameters': {'type': 'object', 'properties': {
            'hours': {'type': 'array', 'items': {'type': 'integer'}, 'description': 'UTC hours 0-23'},
            'days': {'type': 'integer', 'description': f'how many days to keep them pinned (1-{MAX_PIN_DAYS})'}},
            'required': ['hours']}},
    'unpin_hours': {
        'description': 'Remove a pin so the growth agent manages publish hours again.',
        'parameters': {'type': 'object', 'properties': {}}},
    'pause_option': {
        'description': 'Stop picking a series, region or video length for new videos.',
        'parameters': {'type': 'object', 'properties': {
            'dimension': {'type': 'string', 'enum': list(DIMENSIONS)}, 'option': {'type': 'string'}},
            'required': ['dimension', 'option']}},
    'boost_option': {
        'description': f'Pick a series, region or video length more often (factor 1.1-{MAX_BOOST:g}).',
        'parameters': {'type': 'object', 'properties': {
            'dimension': {'type': 'string', 'enum': list(DIMENSIONS)}, 'option': {'type': 'string'},
            'factor': {'type': 'number'}}, 'required': ['dimension', 'option', 'factor']}},
    'reset_option': {
        'description': 'Undo a pause or boost so the data-driven weight applies again.',
        'parameters': {'type': 'object', 'properties': {
            'dimension': {'type': 'string', 'enum': list(DIMENSIONS)}, 'option': {'type': 'string'}},
            'required': ['dimension', 'option']}},
    'refresh_analytics': {
        'description': 'Fetch fresh YouTube, Facebook and Instagram numbers now (runs the analytics workflow).',
        'parameters': {'type': 'object', 'properties': {}}},
}


def options(cfg) -> dict[str, list[str]]:
    return {'series': [s['id'] for s in cfg.get('editorial.series') or []],
            'region': [r['id'] for r in cfg.get('editorial.regions') or []],
            'duration_variant': [v['id'] for v in cfg.get('editorial.duration_variants') or []]}


def clauses(message: str) -> list[str]:
    return re.split(r'[.;]|\b(?:but|and|then)\b|,\s*(?=(?:boost|pause|focus|change|refresh|avoid)\b)',
                    message.casefold().replace('’', "'"))


def positive_clause(text: str) -> bool:
    return not re.search(r"\b(don't|do not|never|what if|hypothetically)\b", text)


def analysis_requested(message: str) -> bool:
    return any(positive_clause(c) and re.search(r'\b(analy[sz]e|reanaly[sz]e|re-analy[sz]e|recheck|re-check|research|review)\b', c)
               for c in clauses(message))


def timing_requested(message: str) -> bool:
    return any(positive_clause(c) and re.search(r'\b(publish|posting|post|upload|schedule)\b', c)
               and re.search(r'\b(timings?|times?|hours?|schedule)\b', c)
               and re.search(r'\b(change|adjust|move|optimi[sz]e|suggest|recommend|choose|best|better|set|pin|pick)\b', c)
               for c in clauses(message))


def requested(name: str, args: dict, message: str) -> bool:
    """Ground each proposal in its own clause, preserving independent positive commands."""
    verbs = {'pause_option': r'\b(pause|stop|disable|avoid|exclude|skip)\b',
             'boost_option': r'\b(boost|increase|more|prioriti[sz]e|focus|emphasi[sz]e)\b',
             'reset_option': r'\b(reset|unpause|resume|restore)\b',
             'pin_hours': r'\b(pin|set|change|schedule|move|adjust|suggest|recommend|choose|pick)\b',
             'unpin_hours': r'\b(unpin|agent decide|automatic|automatically)\b',
             'refresh_analytics': r'\b(refresh|fetch|update)\b'}
    for text in clauses(message):
        if not positive_clause(text) or not re.search(verbs.get(name, r'(?!)'), text):
            continue
        if name.endswith('_option'):
            option = str(args.get('option', '')).replace('_', ' ')
            aliases = {'duration_variant': {'concise': ['concise', 'shorter'], 'story': ['story', 'longer']},
                       'region': {'americas': ['americas', 'america', 'usa', 'united states'],
                                  'europe': ['europe', 'european'], 'japan': ['japan', 'japanese'],
                                  'russia': ['russia', 'russian'], 'india': ['india', 'indian'],
                                  'africa': ['africa', 'african'], 'china': ['china', 'chinese']}}
            terms = aliases.get(args.get('dimension'), {}).get(str(args.get('option')), [option])
            if any(re.search(r'\b' + re.escape(t) + r'\b', text) for t in terms if t):
                return True
        elif name != 'refresh_analytics' or 'analytic' in text or 'stats' in text or 'data' in text:
            return True
    return False


def explicit_ist_hours(message: str) -> list[int] | None:
    """Convert explicit HH:MM IST hours exactly; never silently round a schedule."""
    if not re.search(r'\b(ist|india|indian|local time)\b', message, re.I):
        return None
    matches = re.findall(r'\b([0-2]?\d):([0-5]\d)\s*(am|pm)?\b', message, re.I)
    if not matches:
        raise ValueError('For IST schedules, give all times as HH:30 (24-hour format).')
    hours = []
    for h, m, ap in matches:
        hour = int(h)
        if ap:
            if not 1 <= hour <= 12:
                raise ValueError('Invalid 12-hour time.')
            hour = hour % 12 + (12 if ap.lower() == 'pm' else 0)
        if hour > 23:
            raise ValueError('Invalid hour.')
        minute = (hour * 60 + int(m) - 330) % 1440
        if minute % 60:
            raise ValueError('Publish slots use whole UTC hours; choose times ending in :30 IST.')
        hours.append(minute // 60)
    return hours


def validate(name: str, args: dict, cfg) -> tuple[dict, str]:
    """Normalized arguments and a plain-English summary; ValueError if not allowed."""
    if name not in ACTIONS:
        raise ValueError(f'unknown action {name!r}')
    if not isinstance(args, dict):
        raise ValueError('arguments must be an object')
    args = dict(args)
    allowed = ACTIONS[name]['parameters']['properties']
    if set(args) - set(allowed):
        raise ValueError('unsupported action arguments')
    if name == 'pin_hours':
        raw_hours = args.get('hours')
        if not isinstance(raw_hours, list) or any(type(h) is not int for h in raw_hours):
            raise ValueError('hours must be whole numbers 0-23')
        if 'days' in args and type(args['days']) is not int:
            raise ValueError('days must be a whole number')
        try:
            hours = sorted(raw_hours)
            days = args.get('days', 7)
        except (TypeError, ValueError):
            raise ValueError('hours must be whole numbers 0-23') from None
        if not 1 <= days <= MAX_PIN_DAYS:
            raise ValueError(f'days must be 1-{MAX_PIN_DAYS}')
        if why := problem(hours, settings(cfg)):
            raise ValueError(why)
        return {'hours': hours, 'days': days}, f"Pin publish hours to {', '.join(f'{h:02d}:00' for h in hours)} UTC for {days} days"
    if name in ('unpin_hours', 'refresh_analytics'):
        return {}, {'unpin_hours': 'Let the growth agent manage publish hours again',
                    'refresh_analytics': 'Refresh analytics from all platforms now'}[name]
    dim, opt = args.get('dimension'), str(args.get('option') or '').strip()
    known = options(cfg)
    if dim not in known:
        raise ValueError(f'dimension must be one of {", ".join(DIMENSIONS)}')
    match = {o.casefold(): o for o in known[dim]}.get(opt.casefold().replace(' ', '_'))
    if not match:
        raise ValueError(f'unknown {dim} {opt!r}; choose from {", ".join(known[dim])}')
    if name == 'boost_option':
        try:
            factor = round(float(args.get('factor')), 2)
        except (TypeError, ValueError):
            raise ValueError('factor must be a number') from None
        if isinstance(args.get('factor'), bool) or not math.isfinite(factor) or not 1.1 <= factor <= MAX_BOOST:
            raise ValueError(f'factor must be between 1.1 and {MAX_BOOST:g}')
        return {'dimension': dim, 'option': match, 'factor': factor}, f'Pick {dim} "{match}" {factor:g}x as often'
    verb = {'pause_option': 'Pause', 'reset_option': 'Reset to data-driven weight:'}[name]
    return {'dimension': dim, 'option': match}, f'{verb} {dim} "{match}"'


class GitHub:
    """Minimal GitHub access through the signed-in GitHub CLI."""

    def __init__(self, gh: str, repo: str, branch: str = 'master'):
        self.gh, self.repo, self.branch = gh, repo, branch

    def _api(self, *args, payload=None) -> dict:
        out = subprocess.run([self.gh, 'api', *args] + (['--input', '-'] if payload is not None else []),
                             input=json.dumps(payload) if payload is not None else None,
                             capture_output=True, text=True, timeout=60)
        if out.returncode:
            raise RuntimeError((out.stderr or out.stdout).strip()[:300])
        return json.loads(out.stdout or '{}')

    def read_json(self, path: str) -> tuple[dict | None, str | None]:
        try:
            meta = self._api(f'repos/{self.repo}/contents/{path}?ref={self.branch}')
        except RuntimeError as exc:
            if 'Not Found' in str(exc) or '404' in str(exc):
                return None, None
            raise
        return json.loads(base64.b64decode(meta['content'])), meta['sha']

    def update_json(self, path: str, change, message: str) -> None:
        """Read-modify-write with the file's sha; retried if someone else pushed meanwhile."""
        for attempt in range(3):
            data, sha = self.read_json(path)
            new = change(data or {})
            body = {'message': message, 'branch': self.branch,
                    'content': base64.b64encode((json.dumps(new, indent=2) + '\n').encode()).decode()}
            if sha:
                body['sha'] = sha
            try:
                self._api('-X', 'PUT', f'repos/{self.repo}/contents/{path}', payload=body)
                return
            except RuntimeError as exc:
                if attempt == 2 or not re.search(r'409|does not match|sha', str(exc)):
                    raise

    def run_workflow(self, workflow: str) -> None:
        out = subprocess.run([self.gh, 'workflow', 'run', workflow, '-R', self.repo, '--ref', self.branch],
                             capture_output=True, text=True, timeout=60)
        if out.returncode:
            raise RuntimeError((out.stderr or out.stdout).strip()[:300])


def execute(name: str, args: dict, cfg, github: GitHub, now: datetime | None = None) -> str:
    args, summary = validate(name, args, cfg)
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    msg = f'chore: dashboard: {summary} [skip ci]'
    if name == 'refresh_analytics':
        github.run_workflow('analytics.yml')
        return 'Analytics refresh started. The dashboard picks up its results on the next hourly laptop sync.'
    if name in ('pin_hours', 'unpin_hours'):
        def change(plan):
            plan = dict(plan)
            history = plan.get('history', [])
            if name == 'pin_hours':
                until = (now + timedelta(days=args['days'])).isoformat()
                plan.update(hours_utc=args['hours'], pinned_until=until, changed_at=stamp, updated=stamp,
                            status=f'pinned by you until {until[:16]} UTC')
                history.append({'at': stamp, 'pinned': args['hours'], 'reason': f"pinned by you for {args['days']} days"})
            else:
                # Back-date "updated" so the agent re-plans on the laptop's next hourly run.
                plan.update(pinned_until=None, status='unpinned by you; agent resumes',
                            updated=(now - timedelta(hours=21)).isoformat())
                history.append({'at': stamp, 'reason': 'unpinned by you'})
            plan['history'] = history[-30:]
            return plan
        github.update_json(PLAN, change, msg)
        return summary + ' — saved. The workflow gate uses it from the next publish hour.'

    def change(ov):
        ov = dict(ov)
        dim = ov.setdefault(args['dimension'], {})
        paused = [o for o in dim.get('paused', []) if o != args['option']]
        boost = {k: v for k, v in (dim.get('boost') or {}).items() if k != args['option']}
        if name == 'pause_option':
            paused.append(args['option'])
            if set(paused) >= set(options(cfg)[args['dimension']]):
                raise ValueError('keep at least one option active in each dimension')
        elif name == 'boost_option':
            boost[args['option']] = args['factor']
        dim.update(paused=paused, boost=boost)
        ov['updated'] = stamp
        return ov
    github.update_json(OVERRIDES, change, msg)
    return summary + ' — saved. New videos use it from the next publish.'


def tools() -> list[dict]:
    """The actions in Ollama's tool-calling format."""
    return [{'type': 'function', 'function': {'name': n, **spec}} for n, spec in ACTIONS.items()]
