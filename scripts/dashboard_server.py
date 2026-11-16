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
from weekly_review import ensure_server, ollama  # noqa: E402

KEY_FILE = ROOT.parent / 'dashboard_key.txt'
LOG = ROOT.parent / 'strategist.log'
REFRESH_SECONDS = 600
MODEL = os.environ.get('REVIEW_MODEL', 'qwen3:8b')
OLLAMA = os.environ.get('OLLAMA_HOST_URL', 'http://127.0.0.1:11434')


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

    def loop(self):
        while True:
            time.sleep(REFRESH_SECONDS)
            try:
                self.refresh_earnings_if_due()
                self.refresh()
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
        'best_recent': [{'title': x['title'], 'series': x['series'], 'region': x['region'], 'fb_views': x['fb'], 'score': x['score']} for x in scored[:6]],
        'worst_recent': [{'title': x['title'], 'series': x['series'], 'region': x['region'], 'fb_views': x['fb'], 'score': x['score']} for x in scored[-6:]],
        'data_gaps': {p: list(m) for p, m in (d.get('gaps') or {}).items()},
        'monetization_data': d.get('monetization_api') or ('imported report' if d.get('monetization') else 'no earnings snapshot'),
        'measured_content_patterns': {k: (d.get('content_evidence') or {}).get(k) for k in
                                      ('fb_baseline_median', 'mature_videos', 'working', 'not_working', 'limitations')},
        'facebook_views_by_region': {region: {'views': sum(x.get('fb') or 0 for x in v if x.get('region') == region),
                                             'videos': sum(1 for x in v if x.get('region') == region)}
                                     for region in d.get('options', {}).get('region', [])},
    }
    return json.dumps(ctx, separators=(',', ':'), default=str)


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


def chat(state: State, messages: list[dict]) -> dict:
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
    ensure_server(OLLAMA)
    opts = control.options(cfg)
    # Resolve and validate actions before writing any acknowledgement.
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
    answer = ollama(OLLAMA, '/api/chat', {
        'model': MODEL, 'stream': False, 'think': False,
        'messages': [{'role': 'system', 'content': SYSTEM + '\nNo action cards were produced for this message. Do not claim any action was proposed or performed.\n' + assistant_context(d)}] + history,
        'options': {'temperature': 0.2, 'num_ctx': 8192, 'num_predict': 700}}, timeout=300).get('message', {})
    return {'reply': (answer.get('content') or '').strip() or 'Please clarify the change you want; no action was identified.', 'proposals': []}


# ---------- app shell: manifest, service worker, icons ----------

MANIFEST = {'name': 'Histold', 'short_name': 'Histold', 'start_url': '/', 'scope': '/', 'display': 'standalone',
            'background_color': '#141413', 'theme_color': '#5b4bd6', 'description': 'Histold growth dashboard',
            'icons': [{'src': '/icon-192.png', 'sizes': '192x192', 'type': 'image/png'},
                      {'src': '/icon-512.png', 'sizes': '512x512', 'type': 'image/png'},
                      {'src': '/icon-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'}]}

SW = """const C='histold-v2';
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
    """Purple rounded tile with a white 'H', drawn without an image library."""
    bg, fg = (91, 75, 214), (255, 255, 255)
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


HEAD = """<link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#5b4bd6">
<link rel="apple-touch-icon" href="/icon-192.png"><meta name="apple-mobile-web-app-capable" content="yes">
<style>.chat{display:flex;flex-direction:column;gap:8px;max-height:420px;overflow:auto;padding:4px 2px;margin-bottom:10px}
.msg{padding:9px 12px;border-radius:12px;max-width:88%;white-space:pre-wrap}.me{align-self:flex-end;background:var(--accent);color:#fff}
.ai{align-self:flex-start;background:var(--soft)}.prop{border:1px solid var(--line);border-radius:10px;padding:10px;margin-top:6px;background:var(--panel)}
.prop.err{border-color:var(--bad)}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.grow{flex:1;min-width:180px}
button{font:inherit;padding:7px 14px;border-radius:8px;border:1px solid var(--line);background:var(--panel);color:var(--ink);cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}button:disabled{opacity:.5;cursor:default}
.hint{font-size:12px;color:var(--muted);margin:6px 0}.status{font-size:12px;margin-top:8px}</style>"""

PANELS = """<section class="panel"><div class="row"><b>Histold app</b><button id="installApp" hidden>Install app</button><button id="reloadData">Refresh dashboard</button><button id="logout">Sign out</button><span class="small muted" id="connectionStatus"></span></div>
<details class="hint"><summary>Use on another device</summary>Connect that device to your Tailscale account, open this laptop's HTTPS dashboard address and sign in with the dashboard key.
Windows: Edge/Chrome menu → Install app. Mac: Safari → File → Add to Dock, or Chrome → Install. iPhone: Safari → Share → Add to Home Screen.</details></section>
<section class="panel twothird" id="ai"><h2>Ask Histold AI <span class="muted small">· local qwen3:8b</span></h2>
<div class="chat" id="chat"><div class="msg ai">Ask about performance or tell me what to change. Try "Analyze again and suggest better publish timings", "Avoid unsolved stories", "Focus on European history", or "Pin 12, 18, 23 UTC". I will check requested changes and show you exactly what needs confirmation.</div></div>
<form class="row" id="chatForm"><input class="grow" id="chatInput" placeholder="Ask or instruct…" autocomplete="off"><button class="primary" id="chatSend">Send</button></form>
<p class="hint">Changes are only proposed; nothing happens until you press Confirm. Videos per day can't be changed here.</p></section>
<section class="panel third" id="controls"><h2>Controls</h2>
<h4>Steer topics</h4><div class="row"><select id="cDim" aria-label="Topic dimension"><option value="series">series</option><option value="region">region</option><option value="duration_variant">length</option></select>
<select id="cOpt" aria-label="Topic option"></select></div><div class="row" style="margin-top:6px"><select id="cAct" aria-label="Topic action"><option value="pause_option">Pause</option><option value="boost_option:1.5">Boost ×1.5</option>
<option value="boost_option:2">Boost ×2</option><option value="reset_option">Reset</option></select><button id="cApply">Apply</button></div>
<h4>Publish hours (UTC)</h4><div class="row"><input id="cHours" size="16" aria-label="Publish hours in UTC"><select id="cDays" aria-label="Pin duration in days"><option>3</option><option selected>7</option><option>14</option><option>30</option></select><span class="small muted">days</span></div>
<div class="row" style="margin-top:6px"><button id="cPin">Pin</button><button id="cUnpin">Let agent decide</button></div>
<h4>Data &amp; analysis</h4><div class="row"><button id="cRefresh">Refresh analytics</button><button id="cResearch">Analyze with Qwen</button></div><div class="status" id="cStatus"></div></section>"""

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
$('reloadData').onclick=async()=>{try{await api('/api/refresh',{});location.reload()}catch(e){$('connectionStatus').textContent=e.message}};
$('topRefresh').onclick=async()=>{const b=$('topRefresh');b.disabled=true;b.setAttribute('aria-busy','true');b.innerHTML='↻ <span>Refreshing…</span>';
 try{await api('/api/refresh',{});location.reload()}catch(e){$('connectionStatus').textContent='Refresh failed: '+e.message;b.disabled=false;b.removeAttribute('aria-busy');b.innerHTML='↻ <span>Retry</span>'}};
$('logout').onclick=async()=>{await api('/api/logout',{});if('caches' in window){for(const k of await caches.keys())await caches.delete(k)}location.replace('/login')};
if(D.live_note){const p=document.createElement('span');p.className='pill';p.innerHTML='<b class="warn">●</b> '+esc(D.live_note);$('pills').appendChild(p)}
async function api(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Histold':'1'},body:JSON.stringify(body)});
 const j=await r.json().catch(()=>({error:'bad response'}));if(!r.ok)throw new Error(j.error||r.statusText);return j}
const msgs=[];
function bubble(cls,html){const d=document.createElement('div');d.className='msg '+cls;d.innerHTML=html;$('chat').appendChild(d);$('chat').scrollTop=1e9;return d}
async function act(name,args,btn,out){btn&&(btn.disabled=true);try{const j=await api('/api/action',{name,args});out.innerHTML='<b class="ok">✓</b> '+esc(j.message);setTimeout(()=>location.reload(),2500)}
 catch(e){out.innerHTML='<b class="bad">✗</b> '+esc(e.message);btn&&(btn.disabled=false)}}
$('chatForm').onsubmit=async e=>{e.preventDefault();const t=$('chatInput').value.trim();if(!t)return;$('chatInput').value='';
 msgs.push({role:'user',content:t});bubble('me',esc(t));const wait=bubble('ai','<span class="muted">'+(/analy[sz]e|recheck|research|timings|posting times/i.test(t)?'Rechecking analytics and running local analysis. This can take about a minute…':'Checking your request… (first answer can take ~40 s while the model loads)')+'</span>');$('chatSend').disabled=true;
 try{const j=await api('/api/chat',{messages:msgs});msgs.push({role:'assistant',content:j.reply,proposals:j.proposals});wait.innerHTML=esc(j.reply);
  j.proposals.forEach(p=>{const c=document.createElement('div');c.className='prop'+(p.error?' err':'');
   if(p.error){c.innerHTML='<b class="bad">Can\\'t do:</b> '+esc(p.name)+' — '+esc(p.error)}
   else{c.innerHTML='<div><b>Proposed:</b> '+esc(p.summary)+'</div>'+(p.details?'<p class="small">'+esc(p.details)+'</p>':'')+'<div class="row" style="margin-top:6px"><button class="primary">Confirm</button><button>Dismiss</button></div><div class="status"></div>';
    const [ok,no]=c.querySelectorAll('button'),st=c.querySelector('.status');ok.onclick=()=>act(p.name,p.args,ok,st);no.onclick=()=>c.remove()}
   wait.appendChild(c)});$('chat').scrollTop=1e9}
 catch(e){wait.innerHTML='<b class="bad">Error:</b> '+esc(e.message)}finally{$('chatSend').disabled=false}};
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
            if path == '/api/data':
                return self.send(200, json.dumps(state.get(), default=str), 'application/json')
            if path == '/api/research':
                return self.send(200, json.dumps(state.research_status), 'application/json')
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
                if path == '/api/research':
                    state.start_research()
                    return self.send(202, json.dumps(state.research_status), 'application/json')
                if path == '/api/chat':
                    if not state.chat_lock.acquire(blocking=False):
                        return self.send(429, json.dumps({'error': 'AI is answering another request. Try again shortly.'}), 'application/json')
                    try:
                        return self.send(200, json.dumps(chat(state, body.get('messages') or [])), 'application/json')
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
    state = State(control.GitHub(gh_exe(), repo_slug()))
    threading.Thread(target=state.loop, daemon=True).start()
    icons = {'/icon-192.png': png_icon(192), '/icon-512.png': png_icon(512)}
    server = ThreadingHTTPServer((args.host, args.port), make_handler(state, key, icons))
    print(f'Histold dashboard on http://{args.host}:{args.port} (key in {args.key_file})', flush=True)
    server.serve_forever()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
