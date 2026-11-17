#!/usr/bin/env python3
"""Histold dashboard server: the live dashboard as an installable app, with a local-AI chat.

Listens on 127.0.0.1 only. Reach it from other devices through Tailscale
(`tailscale serve --bg 8787`), never through an open router port. Every page needs the
access key in D:\\histold\\dashboard_key.txt (entered once per device). The local AI
(Ollama) answers from the dashboard data and can only PROPOSE the actions in
src/control.py; they run only when you press Confirm, after a second validation.
"""
from __future__ import annotations

import argparse
import copy
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import struct
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import zlib
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = ROOT
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import build_dashboard  # noqa: E402
from src import control  # noqa: E402
from src.config import load_config  # noqa: E402
from src.growth import PLAN, propose, problem, settings  # noqa: E402
from src.strategy import OVERRIDES, apply_overrides  # noqa: E402
from src.assistant_voice import LocalVoice  # noqa: E402
from src.ai_providers import Providers  # noqa: E402
from weekly_review import ensure_server, ollama  # noqa: E402

KEY_FILE = ROOT.parent / 'dashboard_key.txt'
LOG = ROOT.parent / 'strategist.log'
REFRESH_SECONDS = 300  # server-side re-read of exports/plan; the page polls /api/pulse every 15 s
MODEL = os.environ.get('REVIEW_MODEL', 'qwen3:8b')
OLLAMA = os.environ.get('OLLAMA_HOST_URL', 'http://127.0.0.1:11434')
VOICE = LocalVoice()
PROVIDERS = Providers(None)  # local-only until main() points it at the settings file
# Only messages that look like a change request go through the (local) action extractor.
CHANGE_RE = re.compile(r'\b(pause|boost|reset|pin|unpin|avoid|stop|focus|more|less|fewer|prioriti[sz]e|change|set|switch|move|schedule|resume|refresh|disable|enable|let the agent|no more|increase|decrease|drop)\b', re.I)


def server_config():
    return load_config(DATA_ROOT / 'config.yaml')


def gh_exe() -> str:
    for c in (os.environ.get('GH_EXE'), shutil.which('gh'), r'C:\Program Files\GitHub CLI\gh.exe'):
        if c and Path(c).exists():
            return c
    raise SystemExit('GitHub CLI (gh) not found')


class State:
    def __init__(self, github: control.GitHub):
        self.github = github
        self.lock = threading.Lock()
        self.refresh_lock = threading.Lock()
        self.chat_lock = threading.Lock()
        self.chat_waiting = 0  # chats queued for the lock; background jobs back off while > 0
        self.data: dict | None = None
        self.built = 0.0
        self.raw_data: dict | None = None
        self.research_status = {'running': False}

    def start_research(self):
        if not self.chat_lock.acquire(blocking=False):
            raise ValueError('AI is busy. Try again when its current answer finishes.')
        self.research_status = {'running': True, 'message': 'Qwen is reviewing the analytics…'}
        def run():
            try:
                self.analyze_now()
                self.research_status = {'running': False, 'message': 'Content analysis updated.'}
            except Exception as exc:
                self.research_status = {'running': False, 'error': str(exc)[:250]}
            finally:
                self.chat_lock.release()
        threading.Thread(target=run, daemon=True).start()

    def analyze_now(self):
        """Caller holds chat_lock. Rebuild evidence from the newest available exports."""
        self.refresh()
        with tempfile.TemporaryDirectory(prefix='histold-analysis-') as temp:
            snapshot = Path(temp) / 'snapshot.json'
            snapshot.write_text(json.dumps(self.get(), default=str), encoding='utf-8')
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/content_insights.py'),
                                    '--data-root', str(DATA_ROOT), '--snapshot', str(snapshot)],
                                    capture_output=True, text=True, encoding='utf-8', timeout=300)
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout)[-250:])
        with self.refresh_lock:
            if self.raw_data is not None:
                report_path = DATA_ROOT / 'data/private/content-insights.json'
                self.raw_data['ai_insights'] = json.loads(report_path.read_text(encoding='utf-8'))
            self._refresh(overlay_only=True)
        return self.get()

    def refresh(self, overlay_only: bool = False) -> None:
        with self.refresh_lock:
            self._refresh(overlay_only)

    def _refresh(self, overlay_only: bool = False) -> None:
        """Data from the laptop clone, with the plan and overrides read live from GitHub
        (dashboard changes land there first; the clone catches up on its hourly run)."""
        if not overlay_only or self.raw_data is None:
            exports = {}
            note = None
            try:
                for key, path in [('index', 'data/published_index.json'), ('analytics', 'data/analytics.json'),
                                  ('meta', 'data/meta_analytics.json')]:
                    value, _ = self.github.read_json(path)
                    if value is not None:
                        exports[key] = value
            except Exception as exc:
                note = f'Latest exports unavailable ({exc}); using cached laptop exports where needed.'
            self.raw_data = build_dashboard.collect(datetime.now(timezone.utc), LOG, DATA_ROOT, exports)
            if note:
                self.raw_data['live_note'] = note
        data = copy.deepcopy(self.raw_data)
        try:
            plan, _ = self.github.read_json(PLAN)
            overrides, _ = self.github.read_json(OVERRIDES)
            if plan:
                data['plan'] = plan
                from src.growth import hours_from_plan
                data['hours'], data['hours_source'] = hours_from_plan(plan, server_config())
            data['overrides'] = overrides or {}
            if overrides and data.get('strategy', {}).get('weights'):
                data['strategy'] = {**data['strategy'], 'weights': apply_overrides(data['strategy']['weights'], overrides)}
        except Exception as exc:  # noqa: BLE001 - offline: keep the clone's view
            data['live_note'] = f'GitHub not reachable ({exc}); showing the laptop copy.'
        with self.lock:
            self.data, self.built = data, time.time()

    def get(self) -> dict:
        if self.data is None:
            self.refresh()
        with self.lock:
            return copy.deepcopy(self.data)

    def name_videos(self) -> int:
        """Give each video a 2-4 word conversational nickname with the local model (cached; skipped while the AI is busy)."""
        path = DATA_ROOT / build_dashboard.NICKNAMES
        known = build_dashboard.read_json(path, {}) or {}
        titles = [v['title'] for v in (self.raw_data or {}).get('videos', []) if v.get('title') and v['title'] not in known]
        if not titles:
            return 0
        added = 0
        ensure_server(OLLAMA)
        for i in range(0, len(titles), 10):
            # Background work yields to the owner: one small batch per lock hold, and stop if a chat is waiting.
            if self.chat_waiting or not self.chat_lock.acquire(blocking=False):
                break
            try:
                batch = titles[i:i + 10]
                msg = ollama(OLLAMA, '/api/chat', {
                    'model': MODEL, 'stream': False, 'think': False, 'format': 'json',
                    'messages': [{'role': 'system', 'content': NICK_PROMPT},
                                 {'role': 'user', 'content': json.dumps(batch, ensure_ascii=False)}],
                    'options': {'temperature': 0.2, 'num_ctx': 4096, 'num_predict': 700}}, timeout=300).get('message', {})
            finally:
                self.chat_lock.release()
            try:
                names = json.loads(msg.get('content') or '{}').get('names') or {}
            except ValueError:
                continue
            for title in batch:
                name = names.get(title)
                if isinstance(name, str) and 1 <= len(name.split()) <= 5 and len(name) <= 40:
                    known[title] = name.strip().strip('"')
                    added += 1
        if added:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix('.tmp')
            tmp.write_text(json.dumps(known, ensure_ascii=False, indent=1), encoding='utf-8')
            tmp.replace(path)
            with self.refresh_lock:
                for v in (self.raw_data or {}).get('videos', []):
                    v['nick'] = known.get(v['title'], v.get('nick'))
                self._refresh(overlay_only=True)
        return added

    def loop(self):
        try:
            self.get()
            self.name_videos()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        while True:
            time.sleep(REFRESH_SECONDS)
            try:
                self.refresh_earnings_if_due()
                self.refresh()
                self.name_videos()
                report = (self.data or {}).get('ai_insights') or {}
                try:
                    old = datetime.now(timezone.utc) - datetime.fromisoformat(report['generated']) > timedelta(hours=24)
                except (ValueError, KeyError, TypeError):
                    old = True
                if old and not self.chat_lock.locked():
                    self.start_research()
            except Exception:  # noqa: BLE001
                traceback.print_exc()

    def refresh_earnings_if_due(self):
        """Hourly read-only financial collection; credentials and reports remain local."""
        status = DATA_ROOT / 'data/private/earnings-status.json'
        if status.exists() and time.time() - status.stat().st_mtime < 3600:
            return
        subprocess.run([sys.executable, str(ROOT / 'scripts/refresh_private_earnings.py'),
                        '--config', str(ROOT / 'config.yaml'), '--data-root', str(DATA_ROOT)],
                       capture_output=True, timeout=600)


NICK_PROMPT = """You name history videos for a channel owner's dashboard. For each title in the JSON list, give a 2-4 word nickname the owner would use in conversation, naming the concrete subject (people, place, object, era), in the style of "Roman Aqueducts" or "Viking Sunstones". No question words, no punctuation.
Reply only with JSON: {"names": {"<exact title>": "<nickname>", ...}} covering every title."""


def vid(x: dict) -> dict:
    return {'video': x.get('nick') or x['title'], 'title': x['title'], 'series': x['series'], 'region': x['region'],
            'published': x['at'][:10], 'fb_views': x['fb'], 'ig_views': x['ig'], 'yt_views': x['yt'], 'score': x['score']}


def assistant_context(d: dict) -> str:
    v = d['videos']
    scored = sorted([x for x in v if x.get('score') is not None], key=lambda x: -x['score'])
    now = datetime.now(timezone.utc)

    def tot(k, days):
        return sum(x.get(k) or 0 for x in v if (now - datetime.fromisoformat(x['at'])).days < days)
    ctx = {
        'views_last_7d_by_publish_date': {p: tot(p, 7) for p in ('fb', 'ig', 'yt')},
        'views_last_28d': {p: tot(p, 28) for p in ('fb', 'ig', 'yt')},
        'videos_28d': len(v),
        'publish_hours_utc': d['hours'], 'posts_per_day_fixed': d['posts_per_day'],
        'allowed_hours_utc': d['allowed_hours'], 'growth_agent_status': (d.get('plan') or {}).get('status'),
        'reach_score_by_hour_utc': d['hour_stats'],
        'topic_weights': (d.get('strategy') or {}).get('weights'),
        'manual_overrides': d.get('overrides'),
        'options': d.get('options'),
        'best_recent': [vid(x) for x in scored[:8]],
        'worst_recent': [vid(x) for x in scored[-6:]],
        'latest_published': [vid(x) for x in v[:8]],
        'data_gaps': {p: list(m) for p, m in (d.get('gaps') or {}).items()},
        'monetization_data': d.get('monetization_api') or ('imported report' if d.get('monetization') else 'no earnings snapshot'),
        'measured_content_patterns': {k: (d.get('content_evidence') or {}).get(k) for k in
                                      ('fb_baseline_median', 'mature_videos', 'working', 'not_working', 'limitations')},
        'facebook_views_by_region': {region: {'views': sum(x.get('fb') or 0 for x in v if x.get('region') == region),
                                             'videos': sum(1 for x in v if x.get('region') == region)}
                                     for region in d.get('options', {}).get('region', [])},
    }
    return json.dumps(ctx, separators=(',', ':'), default=str)


PERSONA = """Voice and manner: you are the owner's composed, highly capable AI butler. Speak in measured British English, courteous and unhurried, with dry, understated wit used sparingly (never at the expense of accuracy, and never when the news is bad).
- Lead with the answer, phrased like a calm one-line status report built only from numbers in DATA.
- Keep replies short: two to four sentences unless the owner asks for detail. Use **bold** for the key numbers; use a short bullet list only when comparing three or more things.
- Refer to videos by the exact "video" nickname from DATA in double quotes, never by platform IDs, and only name videos that appear in DATA.
- End with one concrete recommendation when the data supports one.
- Anticipate: when DATA shows something the owner should know (an error, a stale export, a weak series), add one brief heads-up.
- Understate rather than exaggerate; when a group has few videos, say the evidence is limited.
- {address}
- Never claim to be a film character or quote films. Never invent numbers to sound confident."""

ADDRESS = {'sir': 'Address the owner as "sir", naturally and not in every sentence.',
           'maam': 'Address the owner as "ma\'am", naturally and not in every sentence.',
           'boss': 'Address the owner as "boss", naturally and not in every sentence.',
           'none': 'Do not use an honorific; address the owner directly.'}


def key_facts(d: dict) -> str:
    """Pre-computed plain-English facts, so a small model never has to rank or read signs itself."""
    now = datetime.now(timezone.utc)
    vids = d.get('videos') or []
    mature = [v for v in vids if v.get('fb') is not None and now - datetime.fromisoformat(v['at']) > timedelta(hours=48)]
    name = lambda v: f'"{v.get("nick") or v["title"]}" ({v["fb"]:,} FB views, {v.get("region") or "?"}, published {v["at"][:10]})'  # noqa: E731
    lines = ['KEY FACTS (computed exactly; use these for "best"/"worst" questions):']
    top = sorted(mature, key=lambda v: -v['fb'])[:3]
    low = sorted(mature, key=lambda v: v['fb'])[:3]
    if top:
        lines.append('Best videos by Facebook views (28 days): ' + '; '.join(name(v) for v in top) + '.')
    if low:
        lines.append('Lowest-reach videos (older than 48 h): ' + '; '.join(name(v) for v in low) + '.')
    week = [v for v in mature if now - datetime.fromisoformat(v['at']) < timedelta(days=7)]
    if week:
        lines.append(f'Best video published in the last 7 days: {name(max(week, key=lambda v: v["fb"]))}.')
    for g in (d.get('content_evidence') or {}).get('groups') or []:
        pct = g.get('fb_vs_baseline_pct')
        if pct is None or not g.get('enough_data'):
            continue
        side = 'ABOVE' if pct > 0 else 'BELOW' if pct < 0 else 'AT'
        lines.append(f'{g["dimension"]} "{g["option"].replace("_", " ")}": {abs(pct)}% {side} the channel median '
                     f'({g.get("fb_median")} median FB views over {g.get("fb_n")} videos).')
    return '\n'.join(lines[:30])


STYLE_LIMITS = """HARD LIMITS for this reply: at most 90 words unless the owner explicitly asks for detail. No headings, no emojis, no closing offers like "Let me know". At most one short bullet list.
Judging performance: compare regions, series and lengths by fb_vs_baseline_pct / median views per video (measured_content_patterns), never by total views, because totals mostly reflect how many videos were posted. Week-over-week claims need dates in DATA; otherwise say what the 28-day data shows."""


def persona(address: str | None, name: str | None = None) -> str:
    name = re.sub(r'[^\w .\'-]', '', (name or ''))[:40].strip()
    line = f'Address the owner as "{name}", naturally and not in every sentence.' if address == 'name' and name \
        else ADDRESS.get(address or 'sir', ADDRESS['sir'])  # owner asked to be addressed as "sir"
    return PERSONA.format(address=line)


SYSTEM = """You are the Histold growth assistant, running locally on the owner's laptop. Histold posts history Shorts to Facebook (the only monetized platform, the priority), Instagram and YouTube; the target audience is the USA and Europe. The owner is in India (IST = UTC+5:30).
Rules:
- Answer only from DATA below; if something is not in it, say so. Be brief and concrete; use numbers from DATA.
- You do not change settings yourself. If the owner asks for a change, a separate step turns the request into proposal cards shown under your answer; just say what the change would do and that it needs their Confirm. Never say a change is made or proposed by you.
- Changes that exist: pause, boost or reset a series/region/length; pin publish hours (UTC) or let the agent decide; refresh analytics.
- You cannot change how many videos are made per day, spending, captions or the videos themselves, and you cannot publish anything. Say so if asked.
- Publish hours are UTC; when you mention them also give IST and New York time.
- For earnings, quote rolling_28_excluding_bonuses as Meta's rolling estimate and total as the separate daily-detail subtotal. If reconciliation reports a difference, explain it. A missing bonus is unknown, not zero. Never call either metric an all-in total or add overlapping metrics.
DATA: """

EXTRACT = """You turn the owner's latest request into setting-change tool calls for a video channel, and nothing else.
- Read the conversation to resolve references, but only propose changes requested in the latest message.
- Call a tool only for a change the owner explicitly asks for in the latest message. Questions are not changes.
- Use these exact ids. series: {series}. region: {region}. duration_variant: {duration}.
- Publish hours are UTC integers; exactly {posts} hours. Current hours: {hours}. IST = UTC+5:30. Never round times.
- If the owner requests a number of videos per day or spending changes, call no tool.
- Never invent an action for a question, hypothetical example, or negated request.
- If no change is requested, reply with the single word NONE and call no tool."""


def analysis_summary(d: dict) -> str:
    report = d.get('ai_insights') or {}
    facts = report.get('evidence') or d.get('content_evidence') or {}
    parts = [f"Fresh local analysis complete: {facts.get('mature_videos', 0)} mature videos reviewed."]
    for label, key in [('Above the Facebook median', 'working'), ('Below the Facebook median', 'not_working')]:
        groups = facts.get(key) or []
        if groups:
            g = groups[0]
            parts.append(f"{label}: {g['option'].replace('_', ' ')} — {g['fb_median']:g} median views across {g['fb_n']} videos.")
    if d.get('live_note'):
        parts.append(d['live_note'])
    parts.append('This re-analyzes the latest available exported metrics; it does not request new measurements from the platforms.')
    return '\n'.join(parts)


def recommend_schedule(d: dict, cfg, days: int = 14) -> tuple[list[dict], str]:
    """Measured one-hour experiment, validated by the same rules as the publish gate."""
    s = settings(cfg)
    current = list(d['hours'])
    if why := problem(current, s):
        return [], f'I cannot suggest from an invalid current schedule: {why}.'
    stats = {int(h): st for h, st in d.get('hour_stats', {}).items()}
    hours, change = propose(current, stats, s)
    if not change:
        return [], 'The available data does not justify a different posting slot yet. I recommend keeping the current times and gathering more data.'
    args, summary = control.validate('pin_hours', {'hours': hours, 'days': days}, cfg)
    candidate = stats.get(change['added'], {})
    count = candidate.get('n', 0)
    local = ', '.join(f'{(h * 60 + 330) // 60 % 24:02d}:30' for h in hours)
    details = (f"Replace {change['removed']:02d}:00 with {change['added']:02d}:00 UTC: {change['reason']}. "
               f"The proposed hour has {count} measured videos; this is an experiment, not a guaranteed improvement. "
               f"IST times: {local}. Keeps {s['posts_per_day']} posts/day. Confirming starts a new {days}-day measurement period.")
    return [{'name': 'pin_hours', 'args': args, 'summary': summary, 'details': details}], details


FOLLOWUPS = {
    'region': ['Which region should I post more of?', 'How is Europe doing versus the Americas?', 'Show me the weakest region and why'],
    'series': ['Which series is working best right now?', 'Should I pause any series?', 'What new angle should I test next?'],
    'length': ['Do longer or shorter videos do better?', 'Which length works on Facebook?', 'What should the next video\'s length be?'],
    'hours': ['Analyze again and suggest better publish timings', 'When is my next post?', 'Which hour performs best?'],
    'trend': ['How did this week compare with last week?', 'What was my best video this week?', 'Where is growth coming from?'],
    'money': ['How much have I earned this month?', 'Which videos earn the most?', 'What is missing from the earnings data?'],
}


def attachments(d: dict, question: str, reply: str) -> dict:
    text = f'{question}\n{reply}'.lower()
    seen, refs = set(), []
    for v in d.get('videos', []):
        keys = [v.get('nick'), v.get('title'), v.get('yt_id'), v.get('fb_id'), v.get('ig_id')]
        if any(k and len(str(k)) >= 5 and str(k).lower() in text for k in keys) and v['title'] not in seen:
            seen.add(v['title'])
            refs.append(v.get('yt_id') or v.get('fb_id') or v['title'])
    q = question.lower()
    chart = ('money' if re.search(r'earn|money|revenue|paid|payout|monetiz', q) else
             'hours' if re.search(r'\bhour|timing|schedule|when .*post|publish time|posting time', q) else
             'region' if re.search(r'region|country|europe|asia|america|africa|where', q) else
             'series' if re.search(r'series|topic|theme|format|angle', q) else
             'length' if re.search(r'length|duration|longer|shorter|concise', q) else
             'trend' if re.search(r'view|grow|trend|perform|week|month|today|doing|best|worst|top', q) else None)
    return {'videos': refs[:6], 'chart': chart if chart != 'money' else None,
            'followups': FOLLOWUPS.get(chart or 'trend', FOLLOWUPS['trend'])}


def chat(state: State, messages: list[dict], address: str | None = None, name: str | None = None) -> dict:
    d = state.get()
    history = [{'role': m['role'], 'content': m['content']} for m in messages[-12:]
               if isinstance(m, dict) and m.get('role') in ('user', 'assistant') and isinstance(m.get('content'), str)]
    cfg = server_config()
    last = next((m['content'] for m in reversed(history) if m['role'] == 'user'), '')
    # Follow-up acknowledgement re-shows the previous concrete card; it never executes it.
    if re.fullmatch(r'\s*(?:yes|yes please|do (?:that|it)|yes[, ]+(?:do (?:that|it)|use those times)|use those times)[.! ]*', last, re.I):
        pending = next((m.get('proposals') for m in reversed(messages[:-1])
                        if isinstance(m, dict) and m.get('role') == 'assistant' and m.get('proposals')), [])
        cards = []
        for p in pending:
            if p.get('error'):
                continue
            norm, summary = control.validate(p.get('name'), p.get('args') or {}, cfg)
            cards.append({**p, 'args': norm, 'summary': summary})
        return {'reply': 'Use Confirm on the proposal below to apply it. Nothing has changed yet.' if cards else
                'There is no pending change to confirm. Tell me which setting you want to change.', 'proposals': cards}
    explicit_hours = bool(re.search(r'\b\d{1,2}:\d{2}|\b\d{1,2}\s*(?:am|pm)\b|\b\d{1,2}(?:\s*,\s*\d{1,2})+|\b(?:to|at)\s+\d{1,2}\b', last, re.I))
    auto_timing = control.timing_requested(last) and not explicit_hours
    analyzed = control.analysis_requested(last) or auto_timing
    if analyzed:
        try:
            d = state.analyze_now()
        except Exception as exc:
            return {'reply': f'Fresh analysis failed: {str(exc)[:220]}. I have not proposed or changed any posting times.', 'proposals': []}
    prefix = analysis_summary(d) + '\n\n' if analyzed else ''
    if auto_timing:
        duration = re.search(r'\bfor\s+(\d+)\s+days?\b', last, re.I)
        cards, explanation = recommend_schedule(d, cfg, int(duration[1]) if duration else 14)
        return {'reply': prefix + explanation + ('\nReview the card and press Confirm to apply it. Nothing has changed yet.' if cards else ''), 'proposals': cards}
    opts = control.options(cfg)
    # Resolve and validate actions before writing any acknowledgement.
    msg = {}
    if CHANGE_RE.search(last):  # questions skip the extractor and go straight to the answering provider
        ensure_server(OLLAMA)
        msg = ollama(OLLAMA, '/api/chat', {
        'model': MODEL, 'stream': False, 'think': False, 'tools': control.tools(),
        'messages': [{'role': 'system', 'content': EXTRACT.format(
            series=', '.join(opts['series']), region=', '.join(opts['region']), duration=', '.join(opts['duration_variant']),
            posts=d['posts_per_day'], hours=d['hours'])}] + history,
        'options': {'temperature': 0, 'num_ctx': 4096, 'num_predict': 300}}, timeout=300).get('message', {})
    proposals = []
    for call in msg.get('tool_calls') or []:
        fn = call.get('function', {})
        args = fn.get('arguments') or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        try:
            if not control.requested(fn.get('name'), args, last):
                continue
            if fn.get('name') == 'pin_hours':
                local = control.explicit_ist_hours(last)
                if local is not None:
                    args['hours'] = local
            norm, summary = control.validate(fn.get('name'), args, cfg)
            proposals.append({'name': fn.get('name'), 'args': norm, 'summary': summary})
        except ValueError as exc:
            proposals.append({'name': fn.get('name'), 'args': args, 'error': str(exc)})
    if proposals:
        return {'reply': prefix + 'I checked the requested changes. Review the cards below; nothing changes until you press Confirm.', 'proposals': proposals}
    if analyzed:
        return {'reply': prefix.strip() + '\nThe findings are updated in the dashboard. No settings were changed.', 'proposals': []}
    # Style rules go last: small local models follow the most recent instructions most reliably.
    system = (SYSTEM + '\nNo action cards were produced for this message. Do not claim any action was proposed or performed.\n'
              + assistant_context(d) + '\n\n' + key_facts(d) + '\n\n' + persona(address, name) + '\n' + STYLE_LIMITS)

    def local_answer() -> str:
        ensure_server(OLLAMA)
        return (ollama(OLLAMA, '/api/chat', {
            'model': MODEL, 'stream': False, 'think': False,
            'messages': [{'role': 'system', 'content': system}] + history,
            'options': {'temperature': 0.3, 'num_ctx': 8192, 'num_predict': 320}}, timeout=300).get('message', {}).get('content') or '')
    text, used, notes = PROVIDERS.answer(system, history, local_answer)
    return {'reply': text.strip() or 'Please clarify the change you want; no action was identified.', 'proposals': [],
            'provider': used, 'provider_notes': notes}


# ---------- app shell: manifest, service worker, icons ----------

MANIFEST = {'name': 'Histold', 'short_name': 'Histold', 'start_url': '/', 'scope': '/', 'display': 'standalone',
            'background_color': '#111827', 'theme_color': '#111827', 'description': 'Histold growth dashboard',
            'icons': [{'src': '/icon-192.png', 'sizes': '192x192', 'type': 'image/png'},
                      {'src': '/icon-512.png', 'sizes': '512x512', 'type': 'image/png'},
                      {'src': '/icon-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'}]}

SW = """const C='histold-v4';
self.addEventListener('install',e=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil((async()=>{
 await Promise.all((await caches.keys()).filter(k=>k.startsWith('histold-')&&k!==C).map(k=>caches.delete(k)));
 await clients.claim();
 for(const c of await clients.matchAll({type:'window'}))c.postMessage({type:'dashboard-updated'});
})()));
self.addEventListener('fetch',e=>{const r=e.request;if(r.method!=='GET'||new URL(r.url).origin!==location.origin)return;
 if(new URL(r.url).pathname.startsWith('/api/')){e.respondWith(fetch(r,{cache:'no-store'}));return}
 e.respondWith(fetch(r,{cache:'no-store'}).then(res=>{if(res.ok&&!res.redirected){const c=res.clone();caches.open(C).then(x=>x.put(r,c))}return res})
 .catch(()=>caches.match(r)))});"""


def png_icon(size: int) -> bytes:
    """Charcoal rounded tile with a slate-blue 'H', drawn without an image library."""
    bg, fg = (17, 24, 39), (167, 187, 213)
    r, rows = size * 0.2, []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            cx, cy = min(x, size - 1 - x), min(y, size - 1 - y)
            inside = not (cx < r and cy < r and (r - cx) ** 2 + (r - cy) ** 2 > r * r)
            u, w = x / size, y / size
            h = 0.28 <= w <= 0.72 and (0.3 <= u <= 0.4 or 0.6 <= u <= 0.7 or (0.4 < u < 0.6 and 0.46 <= w <= 0.55))
            row += bytes(fg if (inside and h) else bg) + bytes([255 if inside else 0])
        rows.append(bytes(row))

    def chunk(t, b):
        return struct.pack('>I', len(b)) + t + b + struct.pack('>I', zlib.crc32(t + b) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b''.join(rows), 9)) + chunk(b'IEND', b''))


HEAD = """<link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#111827">
<link rel="apple-touch-icon" href="/icon-192.png"><meta name="apple-mobile-web-app-capable" content="yes">
<style>.chat{display:flex;flex-direction:column;gap:8px;max-height:420px;overflow:auto;padding:4px 2px;margin-bottom:10px}
.msg{padding:9px 12px;border-radius:12px;max-width:88%;white-space:pre-wrap}.me{align-self:flex-end;background:var(--accent);color:#fff}
.ai{align-self:flex-start;background:var(--soft)}.prop{border:1px solid var(--line);border-radius:10px;padding:10px;margin-top:6px;background:var(--panel)}
.prop.err{border-color:var(--bad)}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.grow{flex:1;min-width:180px}
button{font:inherit;padding:7px 14px;border-radius:8px;border:1px solid var(--line);background:var(--panel);color:var(--ink);cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}button:disabled{opacity:.5;cursor:default}
.hint{font-size:12px;color:var(--muted);margin:6px 0}.status{font-size:12px;margin-top:8px}</style>"""

PANELS = """<section class="panel twothird" id="ai" data-page="ai"><div class="section-row"><h2>Histold AI <span class="muted small" id="aiBrain">· auto</span></h2><span class="small muted"><i class="status-dot"></i>Reading your live data</span></div>
<div class="chat" id="chat"><div class="msg ai" id="aiWelcome">Ready when you are.</div></div>
<form class="row" id="chatForm"><input class="grow" id="chatInput" placeholder="Ask or instruct…" autocomplete="off"><button type="button" id="chatMic" title="Speak your question" aria-label="Speak your question" hidden>🎙 Talk</button><button class="primary" id="chatSend">Send</button></form>
<p class="hint">Changes are only proposed; nothing happens until you press Confirm, including requests made by voice. Videos per day can't be changed here.</p></section>
<section class="panel third" data-page="ai"><h2>Ask me about</h2><div class="suggest">
<button type="button" data-ask="How did this week go?">📈 How did this week go?</button>
<button type="button" data-ask="Which videos are underperforming and why?">🔍 Which videos are underperforming, and why?</button>
<button type="button" data-ask="Which region should I double down on?">🌍 Which region should I double down on?</button>
<button type="button" data-ask="What should my next few videos be about?">💡 What should my next videos be about?</button>
<button type="button" data-ask="Analyze again and suggest better publish timings">⏱ Re-analyze and suggest better publish times</button>
<button type="button" data-ask="How much have I earned and what is missing from the earnings data?">💰 How much have I earned?</button>
</div><h4>Briefing</h4><button type="button" id="aiBrief" style="width:100%">▶ Give me today's briefing</button></section>
<section class="panel" id="brainSettings" data-page="settings"><div class="section-row"><h2>AI brain</h2><select id="aiMode" aria-label="Which AI answers"><option value="auto">Auto · Claude → Codex → local</option><option value="claude">Claude only (falls back to local)</option><option value="codex">Codex only (falls back to local)</option><option value="local">Local qwen3:8b only</option></select></div><div class="brain-grid" id="brainCards"></div><p class="hint">Claude and Codex answer through their CLIs on this laptop using your own plans, with no tools and no file access; setting changes still need your Confirm. The dashboard cannot see your plan's real remaining quota, so the caps below limit how much of it the dashboard uses (rolling 5 hours and 7 days). If a plan reports its limit, that provider rests until the reset time it gives and the next one answers.</p></section>
<section class="panel half" id="assistantSettings" data-page="settings"><h2>Assistant &amp; voice</h2>
<h4>How the AI addresses you</h4><div class="row"><select id="addressAs" aria-label="How the AI addresses you"><option value="sir" selected>Sir</option><option value="none">No honorific</option><option value="maam">Ma'am</option><option value="boss">Boss</option><option value="name">My name</option></select><input id="addressName" placeholder="Your name" size="12" aria-label="Your name" hidden></div>
<h4>Spoken replies</h4><div class="row"><button id="voiceToggle" type="button" aria-pressed="false">Voice replies: off</button><select id="voiceEngine" aria-label="Voice engine"><option value="local">Local neural voice</option><option value="browser">Browser voice</option></select><button id="voiceTest" type="button">▶ Test voice</button></div>
<p class="hint" id="voiceInfo">Spoken questions always get a spoken answer. The local neural voice runs on this laptop; if it is unavailable the browser voice is used. Speech-to-text uses your browser (Chrome/Edge may send audio to their speech service).</p></section>
<section class="panel half" id="controls" data-page="settings"><h2>Channel controls</h2>
<h4>Steer topics</h4><div class="row"><select id="cDim" aria-label="Topic dimension"><option value="series">series</option><option value="region">region</option><option value="duration_variant">length</option></select>
<select id="cOpt" aria-label="Topic option"></select></div><div class="row" style="margin-top:6px"><select id="cAct" aria-label="Topic action"><option value="pause_option">Pause</option><option value="boost_option:1.5">Boost ×1.5</option>
<option value="boost_option:2">Boost ×2</option><option value="reset_option">Reset</option></select><button id="cApply">Apply</button></div>
<h4>Publish hours (UTC)</h4><div class="row"><input id="cHours" size="16" aria-label="Publish hours in UTC"><select id="cDays" aria-label="Pin duration in days"><option>3</option><option selected>7</option><option>14</option><option>30</option></select><span class="small muted">days</span></div>
<div class="row" style="margin-top:6px"><button id="cPin">Pin</button><button id="cUnpin">Let agent decide</button></div>
<h4>Data &amp; analysis</h4><div class="row"><button id="cRefresh">Refresh analytics</button><button id="cResearch">Analyze with Qwen</button></div><div class="status" id="cStatus"></div></section>
<section class="panel half" data-page="settings"><h2>App &amp; devices</h2><div class="row"><button id="installApp" hidden>Install app</button><button id="reloadData">Refresh dashboard</button><button id="logout">Sign out</button><span class="small muted" id="connectionStatus"></span></div>
<details class="hint"><summary>Use on another device</summary>Connect that device to your Tailscale account, open this laptop's HTTPS dashboard address and sign in with the dashboard key.
Windows: Edge/Chrome menu → Install app. Mac: Safari → File → Add to Dock, or Chrome → Install. iPhone: Safari → Share → Add to Home Screen.</details></section>"""

LIVE_JS = """<script>
if('serviceWorker' in navigator){
 let updating=false;const updateView=()=>{if(!updating){updating=true;location.reload()}};
 navigator.serviceWorker.addEventListener('message',e=>{if(e.data?.type==='dashboard-updated')updateView()});
 navigator.serviceWorker.register('/sw.js',{updateViaCache:'none'}).then(reg=>{
  const check=()=>reg.update().catch(()=>{});check();
  addEventListener('focus',check);document.addEventListener('visibilitychange',()=>{if(!document.hidden)check()});
 }).catch(()=>{});
}
let installPrompt;addEventListener('beforeinstallprompt',e=>{e.preventDefault();installPrompt=e;$('installApp').hidden=false});
$('installApp').onclick=async()=>{if(installPrompt){await installPrompt.prompt();installPrompt=null;$('installApp').hidden=true}};
function connectivity(){$('connectionStatus').textContent=navigator.onLine?'Connected to laptop':'Offline · showing saved dashboard';$('chatSend').disabled=!navigator.onLine}
addEventListener('online',connectivity);addEventListener('offline',connectivity);connectivity();
$('reloadData').onclick=async()=>{try{await api('/api/refresh',{});await pulse()}catch(e){$('connectionStatus').textContent=e.message}};
$('topRefresh').onclick=async()=>{const b=$('topRefresh');b.disabled=true;b.setAttribute('aria-busy','true');b.innerHTML='↻ <span>Refreshing…</span>';
 try{await api('/api/refresh',{});await pulse();b.disabled=false;b.removeAttribute('aria-busy');b.innerHTML='↻ <span>Refresh</span>'}catch(e){$('connectionStatus').textContent='Refresh failed: '+e.message;b.disabled=false;b.removeAttribute('aria-busy');b.innerHTML='↻ <span>Retry</span>'}};
$('logout').onclick=async()=>{await api('/api/logout',{});if('caches' in window){for(const k of await caches.keys())await caches.delete(k)}location.replace('/login')};
if(D.live_note){const p=document.createElement('span');p.className='pill';p.innerHTML='<b class="warn">●</b> '+esc(D.live_note);$('pills').appendChild(p)}
async function api(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Histold':'1'},body:JSON.stringify(body)});
 const j=await r.json().catch(()=>({error:'bad response'}));if(!r.ok)throw new Error(j.error||r.statusText);return j}
const msgs=[];
function bubble(cls,html){const d=document.createElement('div');d.className='msg '+cls;d.innerHTML=html;$('chat').appendChild(d);$('chat').scrollTop=1e9;return d}
async function act(name,args,btn,out){btn&&(btn.disabled=true);try{const j=await api('/api/action',{name,args});out.innerHTML='<b class="ok">✓</b> '+esc(j.message);setTimeout(()=>location.reload(),2500)}
 catch(e){out.innerHTML='<b class="bad">✗</b> '+esc(e.message);btn&&(btn.disabled=false)}}
// ---- rich answers: markdown, video chips, attachments, live working steps ----
function md(src){const out=[];let list=null,table=[];
 const inline=t=>HUD.linkVideos(esc(t).replace(/\\*\\*(.+?)\\*\\*/g,'<strong>$1</strong>').replace(/(^|[^*])\\*([^*\\s][^*]*?)\\*/g,'$1<em>$2</em>').replace(/`([^`]+)`/g,'<code>$1</code>'));
 const flushList=()=>{if(list){out.push('<'+list.t+'>'+list.items.map(i=>'<li>'+inline(i)+'</li>').join('')+'</'+list.t+'>');list=null}};
 const flushTable=()=>{if(table.length){const rows=table.filter(r=>!/^\\|?[\\s:|-]+\\|?$/.test(r)).map(r=>r.replace(/^\\||\\|$/g,'').split('|').map(c=>c.trim()));
  if(rows.length)out.push('<div class="scroll"><table><thead><tr>'+rows[0].map(c=>'<th>'+inline(c)+'</th>').join('')+'</tr></thead><tbody>'+rows.slice(1).map(r=>'<tr>'+r.map(c=>'<td>'+inline(c)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>');table=[]}};
 for(const line of String(src||'').split('\\n')){const t=line.trim();
  if(t.startsWith('|')){flushList();table.push(t);continue}flushTable();
  const m=t.match(/^[-*•]\\s+(.*)/),n=t.match(/^\\d+[.)]\\s+(.*)/);
  if(m||n){const type=m?'ul':'ol';if(!list||list.t!==type){flushList();list={t:type,items:[]}}list.items.push((m||n)[1]);continue}
  flushList();if(!t)continue;const h=t.match(/^#{1,6}\\s+(.*)/);out.push(h?'<h5>'+inline(h[1])+'</h5>':'<p>'+inline(t)+'</p>')}
 flushList();flushTable();return out.join('')}
function working(el,t){const deep=/analy[sz]e|recheck|research|timings|posting times/i.test(t);
 const steps=deep?['Re-reading the latest platform exports','Recomputing medians by region, series and length','Running the local content analysis','Testing publish hours against reach scores','Preparing a recommendation']
  :['Scanning '+D.videos.length+' videos from the last 28 days','Comparing Facebook, Instagram and YouTube reach','Checking topic weights and publish hours','Composing the answer'];
 let i=0;const draw=()=>{el.innerHTML='<ul class="steps">'+steps.slice(0,i+1).map((x,k)=>'<li class="'+(k<i?'done':'')+'">'+esc(x)+(k===i?'…':'')+'</li>').join('')+'</ul>';$('chat').scrollTop=1e9};draw();
 const timer=setInterval(()=>{if(i<steps.length-1){i++;draw()}},deep?9000:2600);return()=>clearInterval(timer)}
function miniChart(kind){
 if(kind==='hours'){const W=300,H=70,bw=W/24;let s='<svg viewBox="0 0 '+W+' '+(H+14)+'" role="img" aria-label="Reach score by publish hour">';
  for(let h=0;h<24;h++){const st=D.hour_stats[h],bh=st?st.shrunk*H:2,on=D.hours.includes(h);
   s+='<rect x="'+(h*bw+1)+'" y="'+(H-bh)+'" width="'+(bw-2)+'" height="'+bh+'" fill="var(--accent)" opacity="'+(on?1:.3)+'"><title>'+h+':00 UTC'+(st?' · score '+st.shrunk+' · '+st.n+' videos':' · no data')+'</title></rect>';
   if(h%6==0)s+='<text x="'+(h*bw)+'" y="'+(H+12)+'">'+h+'h</text>'}return s+'</svg><p class="small muted">Bright bars are your current publish hours.</p>'}
 if(kind==='trend'){const n=14,now=Date.now(),W=300,H=80,ser={fb:Array(n).fill(0),ig:Array(n).fill(0),yt:Array(n).fill(0)};
  V.forEach(v=>{const i=n-1-Math.floor((now-new Date(v.at))/864e5);if(i>=0&&i<n)for(const k in ser)ser[k][i]+=v[k]||0});
  const max=Math.max(1,...Object.values(ser).flat());let s='<svg viewBox="0 0 '+W+' '+(H+14)+'" role="img" aria-label="Views by publish day">';
  for(const [k,c] of [['fb','var(--fb)'],['ig','var(--ig)'],['yt','var(--yt)']])s+='<polyline fill="none" stroke="'+c+'" stroke-width="2" points="'+ser[k].map((y,i)=>(i/(n-1)*W).toFixed(1)+','+(H-y/max*(H-4)).toFixed(1)).join(' ')+'"/>';
  return s+'<text x="0" y="'+(H+12)+'">14 days ago</text><text x="'+W+'" y="'+(H+12)+'" text-anchor="end">today</text></svg><div class="legend"><span><i class="sw" style="background:var(--fb)"></i>Facebook</span><span><i class="sw" style="background:var(--ig)"></i>Instagram</span><span><i class="sw" style="background:var(--yt)"></i>YouTube</span></div>'}
 const groups=(E.groups||[]).filter(g=>g.dimension===kind).sort((a,b)=>(b.fb_median??-1)-(a.fb_median??-1)).slice(0,8),max=Math.max(1,...groups.map(g=>g.fb_median||0));
 return groups.map(g=>'<div class="compare-row"><div>'+esc(pretty(g.option))+'<div class="sample">'+g.fb_n+' videos</div></div><div class="track"><div class="fill" style="width:'+((g.fb_median||0)/max*100)+'%;opacity:'+(g.enough_data?1:.35)+'"></div></div><div style="text-align:right"><b>'+fmt(g.fb_median)+'</b></div></div>').join('')||'<p class="empty small">No comparison data yet.</p>'}
const CHART_TITLE={region:'Median Facebook views by region',series:'Median Facebook views by series',length:'Median Facebook views by length',hours:'Reach score by publish hour (UTC)',trend:'Views by publish day · last 14 days'};
function attach(el,a){if(!a)return;let h='';
 if(a.chart)h+='<div class="att"><h5>'+CHART_TITLE[a.chart]+'</h5>'+miniChart(a.chart)+'</div>';
 const vids=(a.videos||[]).map(HUD.findVideo).filter(Boolean);
 if(vids.length)h+='<div class="att"><h5>Videos mentioned</h5><div class="vcards">'+vids.map(v=>'<button type="button" class="vcard" data-video="'+esc(vidKey(v))+'">'+(thumb(v)?'<img src="'+thumb(v)+'" alt="" loading="lazy">':'')+'<b>'+esc(nick(v))+'</b><span>FB '+fmt(v.fb)+' · IG '+fmt(v.ig)+' · YT '+fmt(v.yt)+'</span></button>').join('')+'</div></div>';
 if(a.followups&&a.followups.length)h+='<div class="followups">'+a.followups.map(f=>'<button type="button" data-ask="'+esc(f)+'">'+esc(f)+'</button>').join('')+'</div>';
 el.insertAdjacentHTML('beforeend',h)}
HUD.ask=t=>{if(location.hash!=='#/ai')location.hash='#/ai';$('chatInput').value=t;$('chatForm').requestSubmit()};
document.addEventListener('click',e=>{const b=e.target.closest('[data-ask]');if(b&&!$('chatSend').disabled)HUD.ask(b.dataset.ask)});
$('aiWelcome').innerHTML=md((()=>{const h=new Date().getHours();return (h<12?'Good morning':h<18?'Good afternoon':'Good evening')+HUD.address()+'. I am watching **'+D.videos.length+'** videos across Facebook, Instagram and YouTube. Ask me anything, pick a suggestion, or tap the core to talk. If you ask for a change, I will show you exactly what needs your Confirm.'})());
$('aiBrief').onclick=()=>{if(location.hash!=='#/ai')location.hash='#/ai';bubble('ai',$('briefing').innerHTML);speak(briefText)};
$('chatForm').onsubmit=async e=>{e.preventDefault();const t=$('chatInput').value.trim();if(!t)return;$('chatInput').value='';const spoken=viaVoice;viaVoice=false;coreState('thinking','processing');HUD.log('Query: '+t.slice(0,60));
 msgs.push({role:'user',content:t});bubble('me',esc(t));const wait=bubble('ai','');const stop=working(wait,t);$('chatSend').disabled=true;
 try{const j=await api('/api/chat',{messages:msgs,address:$('addressAs').value,name:$('addressName').value});stop();msgs.push({role:'assistant',content:j.reply,proposals:j.proposals});
  wait.innerHTML=md(j.reply);attach(wait,j.attachments);if(j.provider)wait.insertAdjacentHTML('beforeend','<div class="via">via '+esc(BRAIN[j.provider]||j.provider)+((j.provider_notes||[]).length?' · '+esc(j.provider_notes.join('; ')):'')+'</div>');if(j.provider&&j.provider!=='local')loadBrain();
  HUD.log('AI answered'+(j.proposals.length?' · '+j.proposals.length+' proposal(s) awaiting Confirm':''));
  if(spoken||voiceOn)speak(j.reply+(j.proposals.length?' Review the proposal card and press Confirm if you want it applied.':''));else coreState('','standby');
  j.proposals.forEach(p=>{const c=document.createElement('div');c.className='prop'+(p.error?' err':'');
   if(p.error){c.innerHTML='<b class="bad">Can’t do:</b> '+esc(p.name)+' — '+esc(p.error)}
   else{c.innerHTML='<div><b>Proposed:</b> '+esc(p.summary)+'</div>'+(p.details?'<p class="small">'+esc(p.details)+'</p>':'')+'<div class="row" style="margin-top:6px"><button class="primary">Confirm</button><button>Dismiss</button></div><div class="status"></div>';
    const [ok,no]=c.querySelectorAll('button'),st=c.querySelector('.status');ok.onclick=()=>act(p.name,p.args,ok,st);no.onclick=()=>c.remove()}
   wait.appendChild(c)});$('chat').scrollTop=1e9}
 catch(e){stop();wait.innerHTML='<b class="bad">Error:</b> '+esc(e.message);coreState('','standby')}finally{$('chatSend').disabled=false}};
// ---- live telemetry: cheap /api/pulse poll; full data only when the server rebuilt it ----
let version=null,lastSync=Date.now(),online=true;
function uplink(ok){online=ok;const m=Math.round((Date.now()-lastSync)/6e4);
 $('uplink').innerHTML='<i class="status-dot'+(ok?'':' off')+'"></i>'+(ok?'Uplink live · synced '+(m<1?'just now':m+'m ago'):'Uplink lost · retrying')}
async function pulse(){try{const r=await fetch('/api/pulse',{cache:'no-store'});if(!r.ok||r.redirected)throw new Error('signed out');const j=await r.json();
  if(!online)HUD.log('Uplink restored');
  if(version!==null&&j.version!==version){const d=await (await fetch('/api/data',{cache:'no-store'})).json();
   for(const k of Object.keys(D))delete D[k];Object.assign(D,d);HUD.rerender();lastSync=Date.now();
   HUD.log('Data stream updated · FB '+fmt(sum(V,'fb'))+' · IG '+fmt(sum(V,'ig'))+' · YT '+fmt(sum(V,'yt')))}
  if(version===null)lastSync=Date.now();version=j.version;uplink(true)}
 catch(e){if(online)HUD.log('Uplink lost: '+e.message);uplink(false)}}
pulse();setInterval(pulse,15000);setInterval(()=>uplink(online),30000);

// ---- optional voice: speech in (browser recognition) and speech out (speechSynthesis) ----
const SR=window.SpeechRecognition||window.webkitSpeechRecognition;let viaVoice=false,rec=null;
let voiceOn=false;try{voiceOn=localStorage.getItem('hud-voice')==='1'}catch(e){}
function coreState(cls,label){$('core').className='core'+(cls?' '+cls:'');$('coreState').textContent='AI core · '+label}
function setVoice(on){voiceOn=on;try{localStorage.setItem('hud-voice',on?'1':'0')}catch(e){}
 $('voiceToggle').textContent='Voice replies: '+(on?'on':'off');$('voiceToggle').setAttribute('aria-pressed',String(on));$('voiceToggle').classList.toggle('on',on);if(!on)stopAudio()}
$('voiceToggle').onclick=()=>setVoice(!voiceOn);
try{$('addressAs').value=localStorage.getItem('hud-address')||'sir';$('addressName').value=localStorage.getItem('hud-name')||''}catch(e){}
const addrUI=()=>{$('addressName').hidden=$('addressAs').value!=='name'};addrUI();
$('addressAs').onchange=()=>{addrUI();try{localStorage.setItem('hud-address',$('addressAs').value)}catch(e){}};
$('addressName').oninput=()=>{try{localStorage.setItem('hud-name',$('addressName').value)}catch(e){}};

// ---- speech out: local neural voice (sentence by sentence, light HUD audio treatment), browser voice as fallback ----
let voiceEngine='local',ttsVoice='',localVoice=null,playing=null,actx=null,speakGen=0;
try{voiceEngine=localStorage.getItem('hud-engine')||'local';ttsVoice=localStorage.getItem('hud-tts-voice')||''}catch(e){}
$('voiceEngine').value=voiceEngine;
$('voiceEngine').onchange=()=>{voiceEngine=$('voiceEngine').value;try{localStorage.setItem('hud-engine',voiceEngine)}catch(e){}voiceUI()};
const voiceSel=document.createElement('select');voiceSel.id='voiceName';voiceSel.setAttribute('aria-label','Neural voice');$('voiceEngine').after(voiceSel);
voiceSel.onchange=()=>{ttsVoice=voiceSel.value;try{localStorage.setItem('hud-tts-voice',ttsVoice)}catch(e){}};
function voiceUI(){voiceSel.hidden=voiceEngine!=='local'||!localVoice?.available;
 $('voiceInfo').textContent=(voiceEngine==='local'?(localVoice?.available?'Local neural voice (Kokoro) runs on this laptop; nothing is sent anywhere. ':'Local neural voice unavailable ('+(localVoice?.error||'checking')+'); using the browser voice. '):'Browser voice selected. ')+'Spoken questions always get a spoken answer. Speech-to-text uses your browser (Chrome/Edge may send audio to their speech service).'}
fetch('/api/voice',{cache:'no-store'}).then(r=>r.json()).then(j=>{localVoice=j;voiceSel.innerHTML=Object.entries(j.voices||{}).map(([k,l])=>'<option value="'+esc(k)+'">'+esc(l)+'</option>').join('');
 voiceSel.value=ttsVoice&&j.voices?.[ttsVoice]?ttsVoice:j.default;ttsVoice=voiceSel.value;voiceUI()}).catch(()=>{localVoice={available:false,error:'server unreachable'};voiceUI()});voiceUI();
$('voiceTest').onclick=()=>speak((()=>{const h=new Date().getHours();return (h<12?'Good morning':h<18?'Good afternoon':'Good evening')+HUD.address()+'. All systems are online. Facebook is carrying the week, and your next video goes out on schedule.'})());
function stopAudio(){speakGen++;if(playing){playing.pause();playing=null}if(window.speechSynthesis)speechSynthesis.cancel()}
function hudChain(el){if(!window.AudioContext)return;actx=actx||new AudioContext();if(actx.state==='suspended')actx.resume();
 const src=actx.createMediaElementSource(el),hp=actx.createBiquadFilter(),pk=actx.createBiquadFilter(),comp=actx.createDynamicsCompressor(),dl=actx.createDelay(),wet=actx.createGain();
 hp.type='highpass';hp.frequency.value=110;pk.type='peaking';pk.frequency.value=2800;pk.gain.value=3;pk.Q.value=.9;dl.delayTime.value=.028;wet.gain.value=.14;
 src.connect(hp);hp.connect(pk);pk.connect(comp);comp.connect(actx.destination);comp.connect(dl);dl.connect(wet);wet.connect(actx.destination)}
function sentences(t){const parts=String(t).replace(/[*_`#|]/g,' ').split(/(?<=[.!?])\\s+/),out=[];let cur='';
 for(const x of parts){if((cur+' '+x).length>220&&cur){out.push(cur);cur=x}else cur=(cur?cur+' ':'')+x}if(cur.trim())out.push(cur);return out.slice(0,8)}
const tts=t=>fetch('/api/tts',{method:'POST',headers:{'Content-Type':'application/json','X-Histold':'1'},body:JSON.stringify({text:t,voice:ttsVoice})}).then(r=>{if(!r.ok)throw new Error('voice '+r.status);return r.blob()});
const play=blob=>new Promise(res=>{const a=new Audio(URL.createObjectURL(blob));playing=a;try{hudChain(a)}catch(e){}a.onended=a.onerror=()=>{URL.revokeObjectURL(a.src);res()};a.play().catch(res)});
async function speakLocal(text,gen){const parts=sentences(text);if(!parts.length)return;let next=tts(parts[0]);
 for(let i=0;i<parts.length;i++){const blob=await next;if(gen!==speakGen)return;if(i+1<parts.length)next=tts(parts[i+1]).catch(()=>null);if(blob)await play(blob);if(gen!==speakGen)return}}
function pickVoice(){const vs=speechSynthesis.getVoices().filter(v=>/^en/i.test(v.lang));
 return vs.find(v=>/Google UK English Male|Ryan|Guy|Daniel|Brian/i.test(v.name))||vs.find(v=>/Natural|Online/i.test(v.name))||vs.find(v=>/en-GB/i.test(v.lang))||vs[0]}
function speakBrowser(text,done){if(!('speechSynthesis' in window))return done();
 const clean=String(text).replace(/[*_`#|]/g,' ').replace(/\\s+/g,' ').trim(),u=new SpeechSynthesisUtterance(clean.length>700?clean.slice(0,700)+'… The full answer is on screen.':clean);
 const v=pickVoice();if(v)u.voice=v;u.rate=1.03;u.pitch=.92;u.onend=u.onerror=done;speechSynthesis.speak(u)}
function speak(text){stopAudio();const gen=speakGen;coreState('speaking','speaking');const done=()=>{if(gen===speakGen)coreState('','standby')};
 if(voiceEngine==='local'&&localVoice?.available)speakLocal(text,gen).then(done,()=>{if(gen===speakGen)speakBrowser(text,done)});else speakBrowser(text,done)}
setVoice(voiceOn);
function listen(){if(!SR){location.hash='#/ai';$('chatInput').focus();return}
 if(rec){rec.stop();return}stopAudio();
 rec=new SR();rec.lang=navigator.language||'en-US';rec.interimResults=true;rec.maxAlternatives=1;
 let finalText='';coreState('listening','listening');$('chatMic').classList.add('on');$('chatMic').textContent='■ Stop';
 rec.onresult=e=>{let t='';for(const r of e.results){t+=r[0].transcript;if(r.isFinal)finalText=t}$('chatInput').value=t};
 rec.onerror=e=>{HUD.log('Voice input: '+e.error)};
 rec.onend=()=>{rec=null;$('chatMic').classList.remove('on');$('chatMic').textContent='🎙 Talk';
  const t=(finalText||$('chatInput').value).trim();if(t&&navigator.onLine){$('chatInput').value=t;viaVoice=true;$('chatForm').requestSubmit()}else coreState('','standby')};
 rec.start()}
if(SR)$('chatMic').hidden=false;$('chatMic').onclick=listen;
$('core').onclick=()=>{if(location.hash!=='#/ai')location.hash='#/ai';listen()};HUD.say=t=>speak(t);
// ---- AI brain: provider status, usage meters and caps ----
const BRAIN={claude:'Claude',codex:'Codex',local:'Local qwen3:8b'};
function meter(u,c){const pct=c?Math.min(100,u/c*100):100;return '<div class="track"><div class="fill" style="width:'+pct+'%;'+(pct>=100?'background:var(--bad)!important':pct>=75?'background:var(--warn)!important':'')+'"></div></div>'}
function renderBrain(j){$('aiMode').value=j.mode;$('aiBrain').textContent='· '+(j.mode==='auto'?'auto':BRAIN[j.mode]);
 $('brainCards').innerHTML=Object.entries(j.providers).map(([k,p])=>{
  const state=!p.installed?'<b class="bad">Not installed</b>':p.resting_until?'<b class="warn">Resting until '+new Date(p.resting_until*1000).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})+'</b>':p.ready?'<b class="ok">Ready</b>':'<b class="warn">Cap reached</b>';
  let h='<div class="brain"><div class="section-row" style="margin:0"><h4 style="margin:0">'+esc(p.label)+'</h4><span class="small">'+state+'</span></div>';
  if(k==='local')return h+'<p class="small muted">Always available on this laptop (GPU). Used for setting-change detection and as the final fallback.</p></div>';
  h+='<div class="small" style="margin-top:8px">Last 5 h: <b>'+p.used_5h+'</b> / <input type="number" min="0" max="1000" value="'+p.per_5h+'" data-cap="'+k+'.per_5h" aria-label="'+esc(p.label)+' 5 hour cap" style="width:70px;min-height:30px;padding:2px 6px"></div>'+meter(p.used_5h,p.per_5h)
   +'<div class="small" style="margin-top:8px">Last 7 days: <b>'+p.used_week+'</b> / <input type="number" min="0" max="1000" value="'+p.per_week+'" data-cap="'+k+'.per_week" aria-label="'+esc(p.label)+' weekly cap" style="width:70px;min-height:30px;padding:2px 6px"></div>'+meter(p.used_week,p.per_week);
  if(!p.installed&&k==='claude')h+='<p class="small">Install the Claude Code CLI once in a terminal: <code>npm install -g @anthropic-ai/claude-code</code>, then run <code>claude</code> and sign in with your Claude plan.</p>';
  if(!p.installed&&k==='codex')h+='<p class="small">Install the Codex desktop app or CLI and sign in.</p>';
  if(p.last_error)h+='<p class="small muted">'+esc(p.last_error)+'</p>';
  if(p.resting_until)h+='<button type="button" data-unrest="'+k+'" style="min-height:32px">Try it again now</button>';
  return h+'</div>'}).join('')}
async function loadBrain(){try{const r=await fetch('/api/ai',{cache:'no-store'});if(r.ok)renderBrain(await r.json())}catch(e){}}
$('aiMode').onchange=async()=>renderBrain(await api('/api/ai',{mode:$('aiMode').value}));
$('brainCards').addEventListener('change',async e=>{const c=e.target.dataset.cap;if(!c)return;const [prov,k]=c.split('.');renderBrain(await api('/api/ai',{[prov]:{[k]:+e.target.value}}))});
$('brainCards').addEventListener('click',async e=>{const k=e.target.dataset.unrest;if(k)renderBrain(await api('/api/ai',{[k]:{clear_cooldown:true}}))});
loadBrain();setInterval(loadBrain,60000);addEventListener('hud-page',()=>{if(HUD.page==='settings')loadBrain()});
function fillOpts(){$('cOpt').innerHTML=(D.options[$('cDim').value]||[]).map(o=>`<option>${esc(o)}</option>`).join('')}
$('cDim').onchange=fillOpts;fillOpts();$('cHours').value=D.hours.join(', ');
$('cApply').onclick=()=>{const [name,f]=$('cAct').value.split(':'),args={dimension:$('cDim').value,option:$('cOpt').value};if(f)args.factor=+f;
 if(confirm($('cAct').selectedOptions[0].text+' '+args.dimension+' "'+args.option+'"?'))act(name,args,$('cApply'),$('cStatus'))};
$('cPin').onclick=()=>{const hours=$('cHours').value.split(/[ ,]+/).filter(Boolean).map(Number),days=+$('cDays').value;
 if(confirm('Pin publish hours '+hours.join(', ')+' UTC for '+days+' days?'))act('pin_hours',{hours,days},$('cPin'),$('cStatus'))};
$('cUnpin').onclick=()=>{if(confirm('Let the growth agent manage publish hours again?'))act('unpin_hours',{},$('cUnpin'),$('cStatus'))};
$('cRefresh').onclick=()=>act('refresh_analytics',{},$('cRefresh'),$('cStatus'));
$('cResearch').onclick=async()=>{const b=$('cResearch'),s=$('cStatus');b.disabled=true;s.textContent='Qwen is reviewing your analytics…';
 try{await api('/api/research',{});const poll=setInterval(async()=>{try{const r=await fetch('/api/research');const j=await r.json();if(!r.ok)throw new Error(j.error||'Sign in again');if(!j.running){clearInterval(poll);b.disabled=false;s.textContent=j.error||j.message;if(!j.error)setTimeout(()=>location.reload(),1000)}}catch(e){clearInterval(poll);b.disabled=false;s.textContent=e.message}},2000)}
 catch(e){b.disabled=false;s.textContent=e.message}};
</script>"""

LOGIN = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Histold sign in</title><link rel="manifest" href="/manifest.webmanifest"><style>body{font:15px system-ui;background:#141413;color:#ecebe6;display:grid;place-items:center;min-height:100vh;margin:0}
form{background:#1e1e1c;border:1px solid #33322e;border-radius:14px;padding:28px;width:min(360px,90vw)}input,button{font:inherit;width:100%;padding:10px;border-radius:8px;margin-top:10px;box-sizing:border-box}
input{border:1px solid #33322e;background:#141413;color:inherit}button{background:#5b4bd6;color:#fff;border:0}p{color:#a09e96;font-size:13px}</style></head>
<body><form method="post" action="/login"><h2 style="margin:0">Histold</h2><p>Enter the access key from <code>D:\\histold\\dashboard_key.txt</code> on the laptop. You only do this once per device.</p>
<input name="key" type="password" autofocus placeholder="Access key">__ERR__<button>Sign in</button></form></body></html>"""


def make_handler(state: State, key: str, icons: dict):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'Histold'

        def log_message(self, fmt, *args):  # quiet; errors still print
            pass

        def send(self, code, body, ctype='text/html; charset=utf-8', headers=None):
            if isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "frame-ancestors 'none'; base-uri 'self'; object-src 'none'")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if not getattr(self, '_head_only', False):
                self.wfile.write(body)

        def authed(self) -> bool:
            cookies = dict(p.strip().split('=', 1) for p in (self.headers.get('Cookie') or '').split(';') if '=' in p)
            return hmac.compare_digest(cookies.get('hk', ''), key)

        def do_HEAD(self):
            self._head_only = True
            self.do_GET()

        def body_json(self) -> dict:
            n = int(self.headers.get('Content-Length') or 0)
            if not 0 <= n <= 200_000:
                raise ValueError('request too large')
            value = json.loads(self.rfile.read(n) or b'{}')
            if not isinstance(value, dict):
                raise ValueError('request must be an object')
            return value

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path == '/manifest.webmanifest':
                return self.send(200, json.dumps(MANIFEST), 'application/manifest+json')
            if path == '/sw.js':
                return self.send(200, SW, 'text/javascript')
            if path in icons:
                return self.send(200, icons[path], 'image/png', {'Cache-Control': 'max-age=86400'})
            if path == '/login':
                return self.send(200, LOGIN.replace('__ERR__', ''))
            if not self.authed():
                return self.send(302, '', headers={'Location': '/login'})
            if path == '/':
                return self.send(200, build_dashboard.render(state.get(), HEAD, PANELS, LIVE_JS))
            if path == '/api/pulse':
                return self.send(200, json.dumps({'version': state.built, 'built': (state.data or {}).get('built'),
                                                  'refresh_seconds': REFRESH_SECONDS, 'research': state.research_status,
                                                  'live_note': (state.data or {}).get('live_note')}), 'application/json')
            if path == '/api/data':
                return self.send(200, json.dumps(state.get(), default=str), 'application/json')
            if path == '/api/research':
                return self.send(200, json.dumps(state.research_status), 'application/json')
            if path == '/api/ai':
                return self.send(200, json.dumps(PROVIDERS.status()), 'application/json')
            if path == '/api/voice':
                return self.send(200, json.dumps(VOICE.status()), 'application/json')
            self.send(404, 'not found', 'text/plain')

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            if path == '/login':
                n = int(self.headers.get('Content-Length') or 0)
                form = urllib.parse.parse_qs(self.rfile.read(min(n, 4096)).decode())
                if hmac.compare_digest((form.get('key') or [''])[0].strip(), key):
                    secure = '; Secure' if self.headers.get('X-Forwarded-Proto') == 'https' else ''
                    return self.send(302, '', headers={'Location': '/', 'Set-Cookie':
                                     f'hk={key}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000{secure}'})
                time.sleep(1)
                return self.send(401, LOGIN.replace('__ERR__', '<p style="color:#ff6b6b">Wrong key.</p>'))
            # API: cookie + custom header (a cross-site form cannot set it)
            if not self.authed() or self.headers.get('X-Histold') != '1':
                return self.send(401, json.dumps({'error': 'sign in again'}), 'application/json')
            try:
                body = self.body_json()
                if path == '/api/logout':
                    return self.send(200, '{}', 'application/json', {'Set-Cookie': 'hk=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'})
                if path == '/api/refresh':
                    state.refresh()
                    return self.send(200, '{}', 'application/json')
                if path == '/api/ai':
                    return self.send(200, json.dumps(PROVIDERS.update_settings(body)), 'application/json')
                if path == '/api/tts':
                    if not VOICE.status()['available']:
                        return self.send(503, json.dumps({'error': VOICE.error}), 'application/json')
                    audio = VOICE.synth(str(body.get('text') or ''), body.get('voice'), body.get('speed') or 1.0)
                    return self.send(200, audio, 'audio/wav')
                if path == '/api/research':
                    state.start_research()
                    return self.send(202, json.dumps(state.research_status), 'application/json')
                if path == '/api/chat':
                    state.chat_waiting += 1  # background jobs see this and stop after their current small batch
                    try:
                        got = state.chat_lock.acquire(timeout=240)
                    finally:
                        state.chat_waiting -= 1
                    if not got:
                        return self.send(429, json.dumps({'error': 'The AI is still finishing a long analysis. Try again in a minute.'}), 'application/json')
                    try:
                        result = chat(state, body.get('messages') or [], body.get('address'), body.get('name'))
                        last = next((m.get('content') for m in reversed(body.get('messages') or [])
                                     if isinstance(m, dict) and m.get('role') == 'user'), '') or ''
                        result['attachments'] = attachments(state.get(), str(last), result.get('reply') or '')
                        return self.send(200, json.dumps(result), 'application/json')
                    finally:
                        state.chat_lock.release()
                if path == '/api/action':
                    message = control.execute(body.get('name'), body.get('args') or {}, server_config(), state.github)
                    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} action {body.get('name')} {body.get('args')}: {message}", flush=True)
                    threading.Thread(target=state.refresh, kwargs={'overlay_only': True}, daemon=True).start()
                    return self.send(200, json.dumps({'message': message}), 'application/json')
            except ValueError as exc:
                return self.send(400, json.dumps({'error': str(exc)}), 'application/json')
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                return self.send(500, json.dumps({'error': f'{type(exc).__name__}: {exc}'[:300]}), 'application/json')
            self.send(404, json.dumps({'error': 'not found'}), 'application/json')
    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8787)
    ap.add_argument('--key-file', type=Path, default=KEY_FILE)
    ap.add_argument('--log', type=Path, default=ROOT.parent / 'strategist.log', help='laptop runner log (health)')
    ap.add_argument('--data-root', type=Path, default=ROOT, help='dedicated laptop clone containing current analytics')
    args = ap.parse_args()
    if args.host != '127.0.0.1':
        ap.error('Keep the server on 127.0.0.1; use Tailscale Serve for remote access.')
    global LOG, DATA_ROOT
    LOG = args.log
    DATA_ROOT = args.data_root.resolve()
    if not args.key_file.exists():
        args.key_file.write_text(secrets.token_urlsafe(18) + '\n')
    key = args.key_file.read_text().strip()
    from dispatch import repo_slug
    global PROVIDERS
    PROVIDERS = Providers(args.key_file.parent / 'ai_settings.json', sandbox=args.key_file.parent)
    state = State(control.GitHub(gh_exe(), repo_slug()))
    threading.Thread(target=state.loop, daemon=True).start()
    icons = {'/icon-192.png': png_icon(192), '/icon-512.png': png_icon(512)}
    server = ThreadingHTTPServer((args.host, args.port), make_handler(state, key, icons))
    print(f'Histold dashboard on http://{args.host}:{args.port} (key in {args.key_file})', flush=True)
    server.serve_forever()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
