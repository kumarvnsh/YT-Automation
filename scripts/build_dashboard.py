#!/usr/bin/env python3
"""Build the laptop dashboard: one self-contained HTML file from the repo's data.

Reads committed exports (YouTube, Facebook, Instagram), the strategist, growth plan and
weekly reviews, plus local-only files (data/private earnings import, laptop logs). No
server and no network: open the output file in a browser. Rebuilt hourly by the runner.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.config import load_config  # noqa: E402
from src.growth import active_hours, dedupe, hour_stats, settings  # noqa: E402
from src.strategy import WINDOW_DAYS, peaks, rows, score  # noqa: E402
from src.insights import evidence  # noqa: E402
from src.facebook_monetization import summarize_api  # noqa: E402

DEFAULT_OUT = ROOT.parent / 'dashboard' / 'index.html'
DEFAULT_LOG = ROOT.parent / 'strategist.log'


def num(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def md_to_html(text: str) -> str:
    """Just enough Markdown for the weekly review: headings, bullets, bold, italics, tables."""
    out, in_list, table = [], 0, []

    def inline(s):
        s = html.escape(s)
        s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
        s = re.sub(r'\*(.+?)\*', r'<em>\1</em>', s)
        return re.sub(r'`(.+?)`', r'<code>\1</code>', s)

    def flush_table():
        if table:
            head, *body = [r for r in table if not re.fullmatch(r'\|?[-| :]+\|?', r)]
            cells = lambda r: [c.strip() for c in r.strip('|').split('|')]  # noqa: E731
            out.append('<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{inline(c)}</th>' for c in cells(head))
                       + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{inline(c)}</td>' for c in cells(r)) + '</tr>'
                                                          for r in body) + '</tbody></table></div>')
            table.clear()

    for line in text.splitlines():
        if line.startswith('|'):
            table.append(line)
            continue
        flush_table()
        m = re.match(r'^(\s*)- (.*)', line)
        depth = len(m.group(1)) // 2 + 1 if m else 0
        while in_list > depth:
            out.append('</ul>')
            in_list -= 1
        while in_list < depth:
            out.append('<ul>')
            in_list += 1
        if m:
            out.append(f'<li>{inline(m.group(2))}</li>')
        elif line.startswith('#'):
            level = min(len(line) - len(line.lstrip('#')) + 2, 5)
            out.append(f'<h{level}>{inline(line.lstrip("#").strip())}</h{level}>')
        elif line.strip():
            out.append(f'<p>{inline(line)}</p>')
    flush_table()
    out.extend('</ul>' for _ in range(in_list))
    return '\n'.join(out)


def log_health(log: Path, now: datetime) -> dict:
    try:
        lines = log.read_text(encoding='utf-8', errors='replace').splitlines()[-4000:]
    except OSError:
        return {'last_run': None, 'problems': ['no laptop log found']}
    last_run, problems = None, []
    for line in lines:
        try:
            at = datetime.strptime(line[:19], '%Y-%m-%d %H:%M:%S').astimezone(timezone.utc)
        except ValueError:
            continue
        if '== strategist run start' in line:
            last_run = at
        if now - at < timedelta(hours=24) and ('!!' in line or 'failed' in line.lower() or 'fatal' in line.lower()):
            problems.append(line[21:200])
    return {'last_run': last_run.isoformat() if last_run else None, 'problems': problems[-8:]}


def collect(now: datetime, log: Path, root: Path | None = None, exports: dict | None = None) -> dict:
    root = root or ROOT
    exports = exports or {}
    cfg = load_config(root / 'config.yaml')
    index = exports.get('index', read_json(root / 'data/published_index.json', []))
    best = peaks(root, (now - timedelta(days=WINDOW_DAYS + 3)).date().isoformat())
    analytics = exports.get('analytics', read_json(root / 'data/analytics.json', {}))
    meta = exports.get('meta', read_json(root / 'data/meta_analytics.json', {}))
    for platform, records in [('yt', analytics.get('channels', {}).get('histold', {}).get('videos', [])),
                              ('fb', meta.get('facebook', {}).get('videos', [])),
                              ('ig', meta.get('instagram', {}).get('videos', []))]:
        for record in records:
            prior = best[platform].get(str(record['id']), {})
            if (record.get('views') or 0) >= (prior.get('views') or 0):
                best[platform][str(record['id'])] = record
    scored = rows(index, best, now)
    score(scored)
    scores = {v['title']: round(v['score'], 3) for v in scored}

    videos = []
    for e in dedupe(index):
        if e.get('channel', 'histold') != 'histold' or not e.get('published_at'):
            continue
        at = datetime.fromisoformat(e['published_at'].replace('Z', '+00:00'))
        if now - at > timedelta(days=WINDOW_DAYS):
            continue
        yt = best['yt'].get(e.get('video_id'), {})
        fb = best['fb'].get(str(e.get('facebook_id')), {})
        ig = best['ig'].get(str(e.get('instagram_id')), {})
        videos.append({
            'at': at.isoformat(), 'title': e.get('title', ''), 'yt_id': e.get('video_id'),
            'series': e.get('category') or '', 'region': e.get('editorial_region') or '',
            'length': e.get('duration_variant') or '', 'scheduled': bool(e.get('scheduled')),
            'fb': num(fb.get('views')), 'fb_likes': num(fb.get('likes')),
            'ig': num(ig.get('views')), 'ig_likes': num(ig.get('likes')), 'ig_skip': ig.get('skip_rate'),
            'yt': num(yt.get('views')), 'yt_likes': num(yt.get('likes')), 'yt_pct': yt.get('avg_view_pct'),
            'score': scores.get(e.get('title', '')),
        })
    videos.sort(key=lambda v: v['at'], reverse=True)

    gaps = {}
    for platform, items in (('YouTube', analytics.get('channels', {}).get('histold', {}).get('videos', [])),
                            ('Facebook', meta.get('facebook', {}).get('videos', [])),
                            ('Instagram', meta.get('instagram', {}).get('videos', []))):
        missing = {}
        for item in items:
            um = item.get('unavailable_metrics') or []
            for key in (um.keys() if isinstance(um, dict) else um):
                missing[key] = um[key] if isinstance(um, dict) else 'not returned by the API'
        if missing:
            gaps[platform] = missing

    reviews = sorted((root / 'data/reviews').glob('*.md')) if (root / 'data/reviews').exists() else []
    s = settings(cfg)
    hours, hours_source = active_hours(root, cfg, now)
    return {
        'built': now.isoformat(),
        'videos': videos,
        'content_evidence': evidence(videos, now),
        'ai_insights': read_json(root / 'data/private/content-insights.json'),
        'freshness': {
            'YouTube export': analytics.get('channels', {}).get('histold', {}).get('updated'),
            'Facebook/Instagram export': meta.get('facebook', {}).get('updated'),
            'Topic strategy': (read_json(root / 'data/strategy.json', {}) or {}).get('updated'),
            'Growth plan': (read_json(root / 'data/growth_plan.json', {}) or {}).get('updated'),
            'Laptop dispatcher': (read_json(root.parent / 'dispatch_state.json', {}) or {}).get('last_run'),
        },
        'health': log_health(log, now),
        'strategy': read_json(root / 'data/strategy.json', {}),
        'plan': read_json(root / 'data/growth_plan.json', {}) or {},
        'hours': hours, 'hours_source': hours_source,
        'allowed_hours': s['allowed_hours_utc'], 'posts_per_day': s['posts_per_day'],
        'hour_stats': {str(h): v for h, v in hour_stats(index, best, now).items()},
        'review': {'name': reviews[-1].stem, 'html': md_to_html(reviews[-1].read_text(encoding='utf-8'))} if reviews else None,
        'monetization': read_json(root / 'data/private/facebook-monetization-report.json'),
        'monetization_api': summarize_api(read_json(root / 'data/private/facebook-api-monetization.json'),
                                         read_json(root / 'data/private/facebook-api-bonus-probe.json')),
        'overrides': read_json(root / 'data/growth_overrides.json', {}),
        'options': {'series': [x['id'] for x in cfg.get('editorial.series') or []],
                    'region': [x['id'] for x in cfg.get('editorial.regions') or []],
                    'duration_variant': [x['id'] for x in cfg.get('editorial.duration_variants') or []]},
        'gaps': gaps,
    }


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Histold Dashboard</title>
<style>
:root{--bg:#f6f5f2;--panel:#fff;--ink:#1d1d1b;--muted:#6b6a65;--line:#e4e2dc;--fb:#1877f2;--ig:#c13584;--yt:#e62117;
--good:#1a7f37;--warn:#b26a00;--bad:#c62828;--accent:#5b4bd6;--soft:#efedf9}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141413;--panel:#1e1e1c;--ink:#ecebe6;--muted:#a09e96;
--line:#33322e;--soft:#2a2840;--accent:#9d93f0;--fb:#4a95f5;--ig:#e05aa6;--yt:#ff5148;--good:#4cc36a;--warn:#e0a03a;--bad:#ff6b6b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;padding:16px clamp(16px,3vw,32px);border-bottom:1px solid var(--line);background:var(--panel)}
h1{font-size:20px;margin:0}h2{font-size:15px;margin:0 0 12px}h3,h4,h5{margin:14px 0 6px}
main{padding:16px clamp(16px,3vw,32px) 48px;display:grid;gap:16px;grid-template-columns:repeat(12,1fr)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;grid-column:span 12;min-width:0}
@media(min-width:1000px){.half{grid-column:span 6}.third{grid-column:span 4}.twothird{grid-column:span 8}}
.muted{color:var(--muted)}.small{font-size:12px}
.pills{display:flex;flex-wrap:wrap;gap:6px}.pill{border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:12px;white-space:nowrap}
.pill b{font-weight:600}.ok{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.kpis{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));grid-column:span 12}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
.kpi .v{font-size:24px;font-weight:650;margin-top:2px;font-variant-numeric:tabular-nums}.kpi .l{font-size:12px;color:var(--muted)}
.kpi .s{font-size:12px;color:var(--muted);margin-top:2px}
.legend{display:flex;gap:14px;font-size:12px;color:var(--muted);margin-bottom:8px}.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
svg{display:block;width:100%;height:auto}svg text{fill:var(--muted);font-size:10px}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}td.t{white-space:normal;min-width:260px}
th{font-size:12px;color:var(--muted);font-weight:600;cursor:pointer;user-select:none;position:sticky;top:0;background:var(--panel)}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}tr:hover td{background:var(--soft)}
.toolbar{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px}input,select{font:inherit;padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
.bar{height:8px;border-radius:4px;background:var(--accent)}.wrow{display:grid;grid-template-columns:120px 1fr 44px;gap:8px;align-items:center;font-size:12px;margin:3px 0}
.hours{display:flex;flex-wrap:wrap;gap:8px}.hour{border:1px solid var(--line);border-radius:10px;padding:8px 10px;min-width:118px}
.hour b{font-size:16px}.review ul{padding-left:20px}.review p{margin:6px 0}.empty{color:var(--muted);font-style:italic}
a{color:var(--accent)}code{font-size:12px}
.tag{font-size:10px;border-radius:4px;padding:1px 5px;margin-left:4px;background:var(--soft);color:var(--accent)}
</style>
<style>
:root{--bg:#f3f5f9;--panel:#fff;--ink:#182539;--muted:#65738a;--line:#e4e9f1;--accent:#315de5;--soft:#edf2ff;--good:#138463;--warn:#a86a14;--bad:#bd4555}
html{scroll-behavior:smooth;scroll-padding-top:92px}body{font-size:14px;letter-spacing:-.1px}code{overflow-wrap:anywhere}
.sidebar{position:fixed;inset:0 auto 0 0;width:218px;background:#101c31;color:#fff;padding:30px 18px;z-index:20;display:flex;flex-direction:column}
.brand{display:flex;align-items:center;gap:12px;font-size:22px;font-weight:750;padding:0 12px 30px}
.brandmark{display:grid;place-items:center;background:#5778ed;color:#fff;border-radius:12px;width:36px;height:36px;font-size:21px;font-weight:800}
.sidebar nav{display:grid;gap:7px}.sidebar nav a{display:block;padding:13px 15px;color:#afbed5;text-decoration:none;border-radius:9px;font-size:14px;font-weight:550}
.sidebar nav a:hover,.sidebar nav a:first-child{background:#233650;color:#fff}.sidebar footer{margin-top:auto;padding:18px 12px;color:#aabbd2;font-size:12px;line-height:1.7}
header{margin-left:218px;position:sticky;top:0;z-index:10;padding:18px 30px;background:var(--panel)}header>div:first-child{flex:1;min-width:0}
header h1{font-size:20px;font-weight:700}header .pills{display:none}
main{margin-left:218px;padding:24px 30px 50px;gap:20px;max-width:1700px}
.panel,.kpi{border-radius:16px;box-shadow:0 3px 16px #18253904;border:1px solid var(--line)}
.panel{padding:22px}h2{font-size:17px;font-weight:680;letter-spacing:-.35px}h4{font-size:13px}
.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:1.2px;font-weight:750;color:var(--muted)}
.intro{grid-column:1/-1;display:flex;justify-content:space-between;align-items:center;gap:12px}.intro h2{font-size:28px;letter-spacing:-1px;margin:5px 0}.intro p{margin:0;color:var(--muted)}
.kpis{grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.kpi{padding:20px}.kpi .l{font-size:11px;font-weight:650;text-transform:uppercase;letter-spacing:.6px}.kpi .v{font-size:29px;letter-spacing:-1px;margin:8px 0}.kpi .s{font-size:11px}
.insight-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.finding-list{display:grid;gap:10px}
.finding{background:var(--bg);padding:14px 16px;border-radius:12px;border-left:3px solid var(--good)}.finding.weak{border-color:var(--warn)}
.finding b{display:block;font-size:14px;margin:0 0 5px}.finding p{font-size:12px;margin:4px 0;color:var(--muted)}.finding .delta{font-weight:750;color:var(--good)}.finding.weak .delta{color:var(--warn)}
.ai-note{padding:15px;background:var(--soft);border-radius:12px;margin-top:16px;font-size:13px;line-height:1.65}.ai-note p{margin:5px 0}
.section-row{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px}.section-row h2{margin:0}
.segmented{display:flex;gap:5px;background:var(--bg);padding:4px;border-radius:10px}.segmented button{border:0;background:transparent;min-height:36px}.segmented button[aria-pressed="true"]{background:var(--panel);box-shadow:0 1px 5px #18253912;color:var(--accent)}
.compare-row{display:grid;grid-template-columns:135px 1fr 90px;align-items:center;gap:15px;margin:14px 0;font-size:12px}.compare-row .track{height:10px;background:var(--bg);border-radius:9px}.compare-row .fill{height:100%;border-radius:9px;background:var(--accent)}.compare-row .sample{font-size:11px;color:var(--muted)}
.video-picks{display:grid;gap:10px}.pick{border:1px solid var(--line);border-radius:12px;padding:14px;display:flex;gap:12px;align-items:start}.rank{flex-shrink:0;width:27px;height:27px;border-radius:8px;display:grid;place-items:center;background:var(--soft);color:var(--accent);font-size:12px;font-weight:700}.pick-title{font-weight:600;font-size:13px;line-height:1.5}.pick .small{margin-top:6px}
.mobile-videos{display:none}.mobile-nav{display:none}.health-details{grid-column:1/-1}.health-details summary{cursor:pointer;font-weight:600}.health-details .pills{margin-top:15px;gap:9px}
input,select,button{min-height:44px;font:inherit}input,select{max-width:100%;border-radius:9px}button{border:1px solid var(--line);border-radius:9px;background:var(--panel);color:var(--ink);padding:8px 13px;cursor:pointer;transition:background .15s,transform .15s}button:active{transform:translateY(1px)}button[hidden]{display:none}
button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #7897ff;outline-offset:3px}
.toolbar input{flex:1;min-width:140px}.toolbar select{max-width:190px}.table-caption{font-size:12px;color:var(--muted);margin:12px 0}.hour{padding:12px 14px;background:var(--bg);border:0;flex:1}.wrow{grid-template-columns:135px 1fr 40px}
#daily,#hours{overflow-x:auto}#daily svg{min-width:500px}#hours svg{min-width:700px}
.view-nav{display:flex;gap:8px;align-items:center;flex-shrink:0}.status-dot{width:8px;height:8px;border-radius:50%;background:var(--good);display:inline-block;margin-right:6px}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0e1727;--panel:#172338;--ink:#e9eff8;--muted:#9aaec8;--line:#293850;--accent:#91acff;--soft:#243550;--good:#5bd1aa;--warn:#e1b15e;--bad:#ff8a9b}.sidebar{background:#0a1220}.kpi,.panel{box-shadow:none}}
@media(min-width:1450px){main{gap:24px;padding:28px 38px}header{padding:20px 38px}}
@media(max-width:1099px){.sidebar{width:176px;padding:22px 10px}.sidebar .brand{font-size:19px;padding:0 10px 25px}header,main{margin-left:176px}main{padding:20px;gap:16px}.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:699px){.sidebar{display:none}header{margin:0;padding:13px 16px}header h1{font-size:18px}header #built{font-size:10px;max-width:240px}
main{margin:0;padding:18px 14px calc(95px + env(safe-area-inset-bottom));gap:14px;display:block}main>.panel,main>.kpis,main>.intro,main>.health-details{margin-bottom:16px}
.intro h2{font-size:24px}.intro p{font-size:12px}.intro{align-items:start}.panel{padding:16px;border-radius:14px}h2{font-size:16px}.kpis{display:grid;gap:10px}.kpi{padding:14px;border-radius:14px}.kpi .v{font-size:25px}.kpi:last-child .v{font-size:22px}.kpi .l{font-size:10px}.kpi .s{line-height:1.5}
.insight-grid{grid-template-columns:1fr;gap:17px}.finding{padding:12px}.compare-row{grid-template-columns:92px 1fr 65px;gap:10px;font-size:11px}.segmented{width:100%}.segmented button{flex:1;font-size:12px}
.toolbar{gap:8px}.toolbar input{flex-basis:100%;font-size:16px}.toolbar select{flex:1 1 calc(50% - 4px);min-width:0;max-width:none;font-size:16px}#videoSort{flex-basis:100%}.desktop-videos{display:none}.mobile-videos{display:grid;gap:10px}
.video-card{border:1px solid var(--line);border-radius:12px;padding:14px}.video-card .title{font-size:14px;font-weight:650;line-height:1.5;margin:8px 0 10px}.video-card .meta{font-size:10px;color:var(--muted);display:flex;justify-content:space-between;gap:8px}.video-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;background:var(--bg);border-radius:9px;padding:10px}.video-metrics b{display:block;font-size:16px}.video-metrics span{font-size:10px;color:var(--muted)}
.mobile-nav{display:flex;position:fixed;bottom:0;left:0;right:0;background:var(--panel);border-top:1px solid var(--line);padding:8px 6px calc(8px + env(safe-area-inset-bottom));z-index:30;box-shadow:0 -3px 15px #101c3108}.mobile-nav a{flex:1;text-align:center;text-decoration:none;color:var(--muted);font-size:10px;font-weight:650;min-height:42px;display:grid;align-content:center;gap:4px}.mobile-nav a span{font-size:17px;color:var(--accent)}
.view-nav{gap:6px}.view-nav span,.view-nav a{display:none}.row{gap:8px}.chat{max-height:340px!important}.msg{max-width:95%!important;font-size:13px}#chatInput{font-size:16px;min-width:0}#chatForm{flex-wrap:nowrap}.wrow{grid-template-columns:117px 1fr 33px;gap:7px}.hour{min-width:105px}.pick{padding:12px}.health-details .pill{white-space:normal}}
</style>__HEAD__</head><body>
<aside class="sidebar"><div class="brand"><span class="brandmark">H</span>Histold</div><nav aria-label="Main navigation"><a href="#overview">◈ &nbsp; Overview</a><a href="#insights">✦ &nbsp; Content insights</a><a href="#videos">▤ &nbsp; Videos</a><a href="#ai">◎ &nbsp; AI assistant</a><a href="#controls">⚙ &nbsp; Controls</a><a href="#strategy-section">↗ &nbsp; Topic strategy</a><a href="#money-section">◉ &nbsp; Monetization</a></nav><footer><span class="status-dot"></span>Local AI workspace<br>Facebook · Instagram · YouTube</footer></aside>
<header><div><h1>Channel overview</h1><div class="muted small" id="built"></div></div><div class="view-nav"><span class="small muted"><i class="status-dot"></i>Histold</span><a href="#ai" class="small">Ask AI ↗</a><button id="topRefresh" aria-label="Refresh dashboard" title="Refresh dashboard" type="button">↻ <span>Refresh</span></button></div></header>
<main id="overview">
<div class="intro"><div><div class="eyebrow">Histold analytics</div><h2>Your channel, in focus.</h2><p id="cadenceSummary">Performance, content decisions and your next three posts.</p></div></div>
<div class="kpis" id="kpis"></div>
<section class="panel" id="insights"><div class="section-row"><h2>What’s working</h2><span id="insightsStamp" class="small muted"></span></div><p class="small muted" id="insightsScope"></p><div class="insight-grid"><div><h4 class="ok">Keep exploring</h4><div id="workingFindings" class="finding-list"></div></div><div><h4 class="warn">Needs attention</h4><div id="weakFindings" class="finding-list"></div></div></div><div id="aiTakeaway"></div></section>
<section class="panel half"><div class="section-row"><h2>Content comparison</h2><div class="segmented" aria-label="Compare content"><button data-dim="region" aria-pressed="true">Regions</button><button data-dim="length" aria-pressed="false">Length</button><button data-dim="series" aria-pressed="false">Series</button></div></div><p class="small muted">Median Facebook views per video · minimum 6 videos for a finding</p><div id="contentCompare"></div></section>
<section class="panel half"><h2>Videos to learn from</h2><div class="segmented" style="margin-bottom:14px"><button id="bestPicks" aria-pressed="true">Top performers</button><button id="weakPicks" aria-pressed="false">Lower reach</button></div><div id="videoPicks" class="video-picks"></div></section>
__PANELS__
<section class="panel twothird"><h2>Views by publish day (last 28 days)</h2>
<p class="small muted">Peak cumulative views on videos published that day, rather than views earned that day.</p>
<div class="legend"><span><i class="sw" style="background:var(--fb)"></i>Facebook</span><span><i class="sw" style="background:var(--ig)"></i>Instagram</span><span><i class="sw" style="background:var(--yt)"></i>YouTube</span></div>
<div id="daily"></div></section>
<section class="panel third"><h2>Publish schedule</h2><div id="schedule"></div></section>
<section class="panel"><h2>Reach by publish hour</h2><p class="muted small">Facebook views (double weight) + total views, as a 0-1 percentile score per hour the video was triggered. Filled bars are current publish hours; faded hours are outside the US/Europe window.</p><div id="hours"></div></section>
<section class="panel" id="videos"><h2>Video library</h2><div class="toolbar"><input id="q" aria-label="Search video titles" placeholder="Search videos…" size="28"><select id="fSeries" aria-label="Filter series"></select><select id="fRegion" aria-label="Filter region"></select><select id="videoSort" aria-label="Sort videos"><option value="at">Latest first</option><option value="fb">Facebook views</option><option value="score">Overall score</option></select></div><div id="videoCount" class="table-caption"></div>
<div class="scroll desktop-videos" style="max-height:620px"><table id="vt"></table></div><div class="mobile-videos" id="videoCards"></div><button id="moreVideos" class="mobile-videos" style="width:100%;margin-top:12px">Show more videos</button></section>
<section class="panel half" id="strategy-section"><h2>Topic strategy</h2><div id="strategy"></div></section>
<section class="panel half" id="money-section"><h2>Monetization</h2><div id="money"></div></section>
<section class="panel half review"><details><summary id="reviewTitle" style="font-weight:650;cursor:pointer">Weekly notes archive</summary><p class="small muted">Earlier model-written narrative. Use the measured content findings above for current decisions.</p><div id="review"></div></details></section>
<section class="panel half"><h2>Data gaps</h2><div id="gaps"></div></section>
<details class="panel health-details"><summary>System health &amp; data freshness</summary><div class="pills" id="pills"></div></details>
</main>
<nav class="mobile-nav" aria-label="Mobile navigation"><a href="#overview"><span>◈</span>Overview</a><a href="#videos"><span>▤</span>Videos</a><a href="#ai"><span>◎</span>Ask AI</a><a href="#controls"><span>⚙</span>Controls</a></nav>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const $=id=>document.getElementById(id), esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt=n=>n==null?'–':n>=1e4?(n/1e3).toFixed(1)+'k':n.toLocaleString();
const ago=iso=>{if(!iso)return null;const h=(Date.now()-new Date(iso))/36e5;return h<1?Math.round(h*60)+' min':h<48?Math.round(h)+' h':Math.round(h/24)+' d'};
const local=(iso,o)=>new Date(iso).toLocaleString([], o||{dateStyle:'medium',timeStyle:'short'});
const tz=(h,zone)=>{const d=new Date();d.setUTCHours(h,0,0,0);return d.toLocaleTimeString([], {hour:'numeric',minute:'2-digit',timeZone:zone})};
$('built').textContent='Built '+local(D.built)+' · data from the last 28 days';
$('topRefresh').onclick=()=>location.reload();

// health pills
const limits={'YouTube export':3,'Facebook/Instagram export':3,'Topic strategy':6,'Growth plan':30,'Laptop dispatcher':2};
let pills=Object.entries(D.freshness).map(([k,v])=>{const h=v?(Date.now()-new Date(v))/36e5:1e9;const c=h<=limits[k]?'ok':h<=limits[k]*4?'warn':'bad';
 return `<span class="pill"><b class="${c}">●</b> ${esc(k)}: ${v?ago(v)+' ago':'never'}</span>`});
const p=D.health.problems;pills.push(`<span class="pill" title="${esc(p.join('\n'))}"><b class="${p.length?'bad':'ok'}">●</b> Laptop errors (24h): ${p.length}</span>`);
$('pills').innerHTML=pills.join('');

// Local-AI selections use only evidence IDs validated against computed statistics.
const pretty=s=>String(s||'').replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());
const E=D.content_evidence||{}, AI=D.ai_insights, AE=AI?.evidence||E, choice=AI?.interpretation;
$('insightsStamp').textContent=AI?`${AI.model} · ${ago(AI.generated)} ago`:'Measured analytics';
$('insightsScope').textContent=`${AE.mature_videos||0} videos aged 48 hours–28 days · channel median ${fmt(AE.fb_baseline_median)} Facebook views. Findings require ${AE.min_sample||6} videos; small gaps remain exploratory.`;
function findings(id,list,weak){$(id).innerHTML=list.length?list.slice(0,3).map(g=>`<article class="finding ${weak?'weak':''}"><b>${esc(pretty(g.option))} <span class="muted small">${esc(g.dimension)}</span></b><span class="delta">${g.fb_vs_baseline_pct>0?'+':''}${g.fb_vs_baseline_pct}%</span> <span class="small">vs channel median</span><p>${fmt(g.fb_median)} median FB views · ${g.fb_n} measured videos</p></article>`).join(''):'<p class="empty small">No supported finding yet. Keep gathering data.</p>'}
findings('workingFindings',AE.working||[],false);findings('weakFindings',AE.not_working||[],true);
$('aiTakeaway').innerHTML=(choice?`<div class="ai-note"><div class="eyebrow">Qwen’s interpretation · hypothesis</div><p>${esc(choice.title_hypothesis)}</p><b>Next test:</b> ${esc(choice.next_test)}</div>`:'')+`<details class="small muted" style="margin-top:13px"><summary>How to read these findings</summary><p>${(AE.limitations||[]).map(esc).join('</p><p>')}</p><p>New series with fewer than six measured videos are exploratory; do not pause them on this evidence alone.</p></details>`;
function comparison(dim){const groups=(E.groups||[]).filter(g=>g.dimension===dim).sort((a,b)=>(b.fb_median??-1)-(a.fb_median??-1)),max=Math.max(1,...groups.map(g=>g.fb_median||0));
 $('contentCompare').innerHTML=groups.map(g=>`<div class="compare-row"><div>${esc(pretty(g.option))}<div class="sample">${g.fb_n} measured${g.enough_data?'':' · early data'}</div></div><div class="track"><div class="fill" style="width:${(g.fb_median||0)/max*100}%;opacity:${g.enough_data?1:.3}"></div></div><div style="text-align:right"><b>${fmt(g.fb_median)}</b><div class="sample">median views</div></div></div>`).join('')||'<p class="empty">No comparison data yet.</p>';
 document.querySelectorAll('[data-dim]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.dim===dim)))}
document.querySelectorAll('[data-dim]').forEach(b=>b.onclick=()=>comparison(b.dataset.dim));comparison('region');
function picks(weak=false){const rows=(weak?E.weak_videos:E.best_videos)||[];$('videoPicks').innerHTML=rows.slice(0,3).map((v,i)=>`<article class="pick"><span class="rank">${i+1}</span><div><div class="pick-title">${v.yt_id?`<a href="https://youtube.com/shorts/${esc(v.yt_id)}" target="_blank" rel="noopener">${esc(v.title)}</a>`:esc(v.title)}</div><div class="small muted"><b>${fmt(v.fb)} Facebook views</b> · ${esc(pretty(v.region))} · ${esc(v.length)}</div></div></article>`).join('')+(weak?'<p class="small muted">Zero views may reflect distribution or reporting; check before judging content quality.</p>':'');$('bestPicks').setAttribute('aria-pressed',String(!weak));$('weakPicks').setAttribute('aria-pressed',String(weak))}
$('bestPicks').onclick=()=>picks();$('weakPicks').onclick=()=>picks(true);picks();

// KPIs
const V=D.videos, now=Date.now(), in7=V.filter(v=>now-new Date(v.at)<7*864e5);
const sum=(a,k)=>a.reduce((s,v)=>s+(v[k]||0),0), avg=(a,k)=>{const x=a.filter(v=>v[k]!=null);return x.length?sum(x,k)/x.length:null};
const next=(()=>{for(let i=0;i<48;i++){const d=new Date();d.setUTCMinutes(0,0,0);d.setUTCHours(d.getUTCHours()+i+1);if(D.hours.includes(d.getUTCHours()))return d}})();
const kpis=[['Facebook views',fmt(sum(V,'fb')),V.filter(v=>v.fb!=null).length+' measured videos · monetization reported active'],
 ['Instagram views',fmt(sum(V,'ig')),V.filter(v=>v.ig!=null).length+' measured videos'],
 ['YouTube views',fmt(sum(V,'yt')),V.filter(v=>v.yt!=null).length+' measured videos'],
 ['Next publish',next?local(next.toISOString(),{hour:'numeric',minute:'2-digit'}):'–',(next?local(next.toISOString(),{weekday:'short'})+' · ':'')+D.posts_per_day+' posts per day']];
$('kpis').innerHTML=kpis.map(([l,v,s])=>`<div class="kpi"><div class="l">${l}</div><div class="v">${esc(v)}</div><div class="s">${esc(s)}</div></div>`).join('');
$('cadenceSummary').textContent=`${D.posts_per_day} scheduled posts daily · ${in7.length} videos published in 7 days · ${V.length} in this 28-day view`;

// daily stacked bars
(()=>{const days=[];for(let i=27;i>=0;i--){const d=new Date(now-i*864e5);days.push(d.toISOString().slice(0,10))}
 const by=Object.fromEntries(days.map(d=>[d,{fb:0,ig:0,yt:0,n:0}]));
 V.forEach(v=>{const d=by[new Date(v.at).toISOString().slice(0,10)];if(d){d.fb+=v.fb||0;d.ig+=v.ig||0;d.yt+=v.yt||0;d.n++}});
 const max=Math.max(1,...days.map(d=>by[d].fb+by[d].ig+by[d].yt)),W=760,H=200,bw=W/days.length;
 let s=`<svg viewBox="0 0 ${W} ${H+28}" role="img" aria-label="Views by publish day">`;
 [0,.5,1].forEach(f=>{const y=H-f*H;s+=`<line x1="0" x2="${W}" y1="${y}" y2="${y}" stroke="var(--line)"/><text x="2" y="${y-3}">${fmt(Math.round(max*f))}</text>`});
 days.forEach((d,i)=>{let y=H;const x=i*bw+3,w=bw-6,b=by[d];
  [['fb','var(--fb)'],['ig','var(--ig)'],['yt','var(--yt)']].forEach(([k,c])=>{const h=b[k]/max*H;y-=h;s+=`<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${c}" rx="2"><title>${d}: ${k.toUpperCase()} ${b[k].toLocaleString()} (${b.n} videos)</title></rect>`});
  if(i%4==0)s+=`<text x="${x}" y="${H+16}">${d.slice(5)}</text>`});
 $('daily').innerHTML=s+'</svg>'})();

// schedule
(()=>{const pl=D.plan||{};let s='<div class="hours">'+D.hours.map(h=>`<div class="hour"><b>${tz(h)}</b><div class="small muted">${String(h).padStart(2,'0')}:00 UTC</div><div class="small">NY ${tz(h,'America/New_York')} · Paris ${tz(h,'Europe/Paris')}</div></div>`).join('')+'</div>';
 s+=`<p class="small muted">Source: ${esc(D.hours_source)}</p>`;
 if(pl.status)s+=`<p class="small"><b>Agent:</b> ${esc(pl.status)}</p>`;
 const hist=(pl.history||[]).slice(-5).reverse();
 const what=c=>c.pinned?'pinned '+c.pinned.map(h=>String(h).padStart(2,'0')).join(', ')+' UTC':c.removed!=null?`${String(c.removed).padStart(2,'0')}→${String(c.added).padStart(2,'0')} UTC`:'';
 s+=hist.length?'<h4>Recent changes</h4><ul class="small">'+hist.map(c=>`<li>${local(c.at,{dateStyle:'medium'})}: ${what(c)} — ${esc(c.reason)}</li>`).join('')+'</ul>':'<p class="empty small">No hour changes yet.</p>';
 $('schedule').innerHTML=s})();

// reach by hour
(()=>{const W=760,H=150,bw=W/24;let s=`<svg viewBox="0 0 ${W} ${H+44}" role="img" aria-label="Reach by publish hour">`;
 for(let h=0;h<24;h++){const st=D.hour_stats[h],x=h*bw+3,w=bw-6,on=D.hours.includes(h),ok=D.allowed_hours.includes(h);
  if(st){const bh=st.shrunk*H;s+=`<rect x="${x}" y="${H-bh}" width="${w}" height="${bh}" rx="3" fill="var(--accent)" opacity="${on?1:ok?.45:.18}"><title>${h}:00 UTC · score ${st.shrunk} · ${st.n} videos</title></rect><text x="${x+w/2}" y="${H-bh-4}" text-anchor="middle">${st.n}</text>`}
  else s+=`<rect x="${x}" y="${H-4}" width="${w}" height="4" fill="var(--line)"><title>${h}:00 UTC · no data</title></rect>`;
  if(on)s+=`<circle cx="${x+w/2}" cy="${H+8}" r="3" fill="var(--accent)"/>`;
  s+=`<text x="${x+w/2}" y="${H+24}" text-anchor="middle">${h}</text>`;
  if(h%3==0)s+=`<text x="${x+w/2}" y="${H+38}" text-anchor="middle">${tz(h)}</text>`}
 s+=`<line x1="0" x2="${W}" y1="${H-.5*H}" y2="${H-.5*H}" stroke="var(--line)" stroke-dasharray="4 4"/></svg>`;
 $('hours').innerHTML=s+'<p class="small muted">Row 1: UTC hour (numbers above bars = videos). Row 2: your local time.</p>'})();

// video table
const cols=[['at','Published'],['title','Title'],['series','Series'],['region','Region'],['length','Length'],['fb','FB views','n'],['ig','IG views','n'],['yt','YT views','n'],['yt_pct','YT % viewed','n'],['score','Score','n']];
let sortK='at',sortD=-1,videoLimit=12;
const opts=(id,k,label)=>{$(id).innerHTML=`<option value="">${label}</option>`+[...new Set(V.map(v=>v[k]).filter(Boolean))].sort().map(x=>`<option>${esc(x)}</option>`).join('')};
opts('fSeries','series','All series');opts('fRegion','region','All regions');
function table(){const q=$('q').value.toLowerCase(),fs=$('fSeries').value,fr=$('fRegion').value;
 const rows=V.filter(v=>(!q||v.title.toLowerCase().includes(q))&&(!fs||v.series==fs)&&(!fr||v.region==fr))
  .sort((a,b)=>{const x=a[sortK],y=b[sortK];return x==null?1:y==null?-1:(x>y?1:x<y?-1:0)*sortD});
 $('vt').innerHTML='<thead><tr>'+cols.map(([k,l,c])=>`<th class="${c||''}" data-k="${k}">${l}${sortK==k?(sortD>0?' ▲':' ▼'):''}</th>`).join('')+'</tr></thead><tbody>'+
 rows.map(v=>`<tr><td>${local(v.at,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}</td><td class="t">${v.yt_id?`<a href="https://youtube.com/shorts/${esc(v.yt_id)}" target="_blank" rel="noopener">${esc(v.title)}</a>`:esc(v.title)}</td><td>${esc(v.series)}</td><td>${esc(v.region)}</td><td>${esc(v.length)}</td><td class="n">${fmt(v.fb)}</td><td class="n">${fmt(v.ig)}</td><td class="n">${fmt(v.yt)}</td><td class="n">${v.yt_pct==null?'–':Math.round(v.yt_pct)+'%'}</td><td class="n">${v.score==null?'<span class="muted" title="under 48h old or not an editorial Short">–</span>':v.score.toFixed(2)}</td></tr>`).join('')+'</tbody>';
 $('videoCount').textContent=rows.length+' matching videos · views include the highest exported snapshot';
 $('videoCards').innerHTML=rows.slice(0,videoLimit).map(v=>`<article class="video-card"><div class="meta"><span>${local(v.at,{month:'short',day:'numeric'})}</span><span>${esc(pretty(v.region))} · ${esc(v.length)}</span></div><div class="title">${v.yt_id?`<a href="https://youtube.com/shorts/${esc(v.yt_id)}" target="_blank" rel="noopener">${esc(v.title)}</a>`:esc(v.title)}</div><div class="video-metrics"><div><b>${fmt(v.fb)}</b><span>Facebook</span></div><div><b>${fmt(v.ig)}</b><span>Instagram</span></div><div><b>${fmt(v.yt)}</b><span>YouTube</span></div></div><div class="small muted" style="margin-top:9px">${esc(pretty(v.series))}${v.yt_pct!=null?' · YT viewed '+Math.round(v.yt_pct)+'%':''}</div></article>`).join('')||'<p class="empty">No videos match these filters.</p>';
 $('moreVideos').hidden=rows.length<=videoLimit;
 document.querySelectorAll('#vt th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;sortD=sortK==k?-sortD:-1;sortK=k;table()})}
['q','fSeries','fRegion'].forEach(id=>$(id).oninput=()=>{videoLimit=12;table()});$('videoSort').onchange=()=>{sortK=$('videoSort').value;sortD=-1;table()};$('moreVideos').onclick=()=>{videoLimit+=12;table()};table();

// strategy
(()=>{const st=D.strategy||{};if(!st.weights){$('strategy').innerHTML='<p class="empty">No strategy yet.</p>';return}
 let s=`<p class="small muted">${st.videos_scored} videos scored · updated ${ago(st.updated)} ago. Weights decide how often each option is picked for new videos.</p>`;
 const ov=D.overrides||{};
 for(const [dim,w] of Object.entries(st.weights)){const max=Math.max(...Object.values(w)),o=ov[dim]||{};s+=`<h4>${esc(dim.replace('_',' '))}</h4>`+Object.entries(w).sort((a,b)=>b[1]-a[1]).map(([k,v])=>{const tag=(o.paused||[]).includes(k)?'<span class="tag">paused</span>':(o.boost||{})[k]?`<span class="tag">×${o.boost[k]}</span>`:'';return `<div class="wrow"><span>${esc(k)}${tag}</span><div class="bar" style="width:${v/max*100}%"></div><span class="n">${Math.round(v*100)}%</span></div>`}).join('')}
 if(Object.keys(ov).filter(k=>k!='updated').length)s+='<p class="small muted">Tags show your manual pauses and boosts; new videos apply them on top of these computed weights.</p>';
 if(st.winners)s+='<h4>Best recent</h4><ul class="small">'+st.winners.slice(0,5).map(t=>`<li>${esc(t)}</li>`).join('')+'</ul>';
 $('strategy').innerHTML=s})();

// monetization
(()=>{const m=D.monetization,a=D.monetization_api;if(a?.available){const money=v=>a.currency?new Intl.NumberFormat(undefined,{style:'currency',currency:a.currency,maximumFractionDigits:2}).format(Number(v)):v+' (unit not verified)';
 const hasRolling=a.rolling_28_excluding_bonuses!=null,amount=money(hasRolling?a.rolling_28_excluding_bonuses:a.total);
 $('money').innerHTML=`<div class="eyebrow">Facebook · ${hasRolling?'Meta rolling 28-day estimate':'Daily-detail subtotal'} excluding bonuses</div><div style="font-size:36px;font-weight:750;margin:12px 0">${esc(amount)}</div><p>Through ${local(a.rolling_bucket_end||a.last_bucket_end,{dateStyle:'medium'})} · estimated, not a payout</p>
 <div class="finding ${a.bonus_earnings!=null?'':'weak'}"><b>Extra Bonus: ${a.bonus_earnings!=null?esc(money(a.bonus_earnings)):'unavailable'}</b><p>${a.bonus_earnings!=null?a.bonus_daily_buckets+' daily buckets returned by Meta. This covers the Extra Bonus field, not every possible bonus program.':esc(a.bonus_status||'Unavailable does not mean zero.')}</p></div>
 <p class="small">Daily-detail subtotal: <b>${esc(money(a.total))}</b> across ${a.daily_buckets} buckets.</p>${a.reconciliation?'<p class="small warn">'+esc(a.reconciliation)+'</p>':''}<p class="small"><b>All-in total including every bonus: not verified.</b></p>
 <p class="small muted">Fetched ${ago(a.rolling_checked||a.checked)} ago directly from Meta · ${esc(a.version)}. This separate snapshot is not refreshed by the hourly analytics export.</p><p class="small">Meta rejected the per-Reel earnings and qualified-view requests despite the account’s monetization-insights task. Availability remains unresolved; no revenue-per-view estimate is calculated.</p><details class="small muted"><summary>Source &amp; access details</summary><p>${esc(a.metric)} · ${esc(a.currency_source)}</p><p>Daily bucket end times: ${local(a.first_bucket_end)} – ${local(a.last_bucket_end)}.</p>${(a.limitations||[]).map(t=>'<p>'+esc(t)+'</p>').join('')}${(a.per_video_errors||[]).map(t=>'<p>'+esc(t)+'</p>').join('')}</details>`;if(m)$('money').innerHTML+='<details><summary>Imported Reel-level report</summary><pre class="small" style="white-space:pre-wrap">'+esc(JSON.stringify(m,null,2))+'</pre></details>';return}
 if(!m){$('money').innerHTML=`<p><b class="warn">No earnings data.</b> No earnings snapshot or CSV report is available on this laptop.</p>
<ul class="small"><li><b>Now:</b> export your earnings CSV in Meta Business Suite (Monetization → Earnings) and run<br><code>.venv\\Scripts\\python scripts\\import_facebook_monetization.py earnings.csv</code><br>in <code>D:\\histold\\YT-Automation</code>. It stays on this laptop (<code>data/private/</code>, never committed).</li>
<li><b>Status:</b> you reported Facebook monetization active. This app cannot independently verify eligibility or payout status. YouTube and Instagram monetization status is not recorded.</li>
<li>Automatic earnings depend on the reporting access Meta makes available to this app; granting a permission alone does not guarantee it.</li></ul>`;return}
 $('money').innerHTML='<pre class="small" style="white-space:pre-wrap">'+esc(JSON.stringify(m,null,2)).slice(0,4000)+'</pre>'})();

// review & gaps
if(D.review){$('reviewTitle').textContent='Weekly notes archive · '+D.review.name;$('review').innerHTML=D.review.html}else $('review').innerHTML='<p class="empty">The first review is written on the laptop\'s next weekly run.</p>';
(()=>{const g=Object.entries(D.gaps);$('gaps').innerHTML=g.length?'<p class="small muted">Metrics the platform APIs did not return for at least one recent video:</p>'+g.map(([p,m])=>`<h4>${esc(p)}</h4><ul class="small">`+Object.entries(m).map(([k,why])=>`<li><code>${esc(k)}</code> — ${esc(why)}</li>`).join('')+'</ul>').join(''):'<p class="ok">All metrics available.</p>'})();
</script>__LIVE_JS__</body></html>
"""


def render(data: dict, head: str = '', panels: str = '', live_js: str = '') -> str:
    blob = json.dumps(data, separators=(',', ':'), default=str).replace('</', '<\\/')
    return (PAGE.replace('__HEAD__', head).replace('__PANELS__', panels)
            .replace('__LIVE_JS__', live_js).replace('__DATA__', blob))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--out', type=Path, default=DEFAULT_OUT)
    ap.add_argument('--log', type=Path, default=DEFAULT_LOG)
    args = ap.parse_args()
    data = collect(datetime.now(timezone.utc), args.log)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix('.tmp')
    tmp.write_text(render(data), encoding='utf-8')
    tmp.replace(args.out)
    print(f"dashboard: {len(data['videos'])} videos -> {args.out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
