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


def collect(now: datetime, log: Path) -> dict:
    cfg = load_config()
    index = read_json(ROOT / 'data/published_index.json', [])
    best = peaks(ROOT, (now - timedelta(days=WINDOW_DAYS + 3)).date().isoformat())
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

    analytics = read_json(ROOT / 'data/analytics.json', {})
    meta = read_json(ROOT / 'data/meta_analytics.json', {})
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

    reviews = sorted((ROOT / 'data/reviews').glob('*.md')) if (ROOT / 'data/reviews').exists() else []
    s = settings(cfg)
    hours, hours_source = active_hours(ROOT, cfg, now)
    return {
        'built': now.isoformat(),
        'videos': videos,
        'freshness': {
            'YouTube export': analytics.get('channels', {}).get('histold', {}).get('updated'),
            'Facebook/Instagram export': meta.get('facebook', {}).get('updated'),
            'Topic strategy': (read_json(ROOT / 'data/strategy.json', {}) or {}).get('updated'),
            'Growth plan': (read_json(ROOT / 'data/growth_plan.json', {}) or {}).get('updated'),
            'Laptop dispatcher': (read_json(ROOT.parent / 'dispatch_state.json', {}) or {}).get('last_run'),
        },
        'health': log_health(log, now),
        'strategy': read_json(ROOT / 'data/strategy.json', {}),
        'plan': read_json(ROOT / 'data/growth_plan.json', {}) or {},
        'hours': hours, 'hours_source': hours_source,
        'allowed_hours': s['allowed_hours_utc'], 'posts_per_day': s['posts_per_day'],
        'hour_stats': {str(h): v for h, v in hour_stats(index, best, now).items()},
        'review': {'name': reviews[-1].stem, 'html': md_to_html(reviews[-1].read_text(encoding='utf-8'))} if reviews else None,
        'monetization': read_json(ROOT / 'data/private/facebook-monetization-report.json'),
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
</style></head><body>
<header><div><h1>Histold</h1><div class="muted small" id="built"></div></div><div class="pills" id="pills"></div></header>
<main>
<div class="kpis" id="kpis"></div>
<section class="panel twothird"><h2>Views by publish day (last 28 days)</h2>
<div class="legend"><span><i class="sw" style="background:var(--fb)"></i>Facebook</span><span><i class="sw" style="background:var(--ig)"></i>Instagram</span><span><i class="sw" style="background:var(--yt)"></i>YouTube</span></div>
<div id="daily"></div></section>
<section class="panel third"><h2>Publish schedule</h2><div id="schedule"></div></section>
<section class="panel"><h2>Reach by publish hour</h2><p class="muted small">Facebook views (double weight) + total views, as a 0-1 percentile score per hour the video was triggered. Filled bars are current publish hours; faded hours are outside the US/Europe window.</p><div id="hours"></div></section>
<section class="panel"><h2>Videos</h2><div class="toolbar"><input id="q" placeholder="Search titles" size="28"><select id="fSeries"></select><select id="fRegion"></select></div>
<div class="scroll" style="max-height:620px"><table id="vt"></table></div></section>
<section class="panel half"><h2>Topic strategy</h2><div id="strategy"></div></section>
<section class="panel half"><h2>Monetization</h2><div id="money"></div></section>
<section class="panel half review"><h2 id="reviewTitle">Weekly review</h2><div id="review"></div></section>
<section class="panel half"><h2>Data gaps</h2><div id="gaps"></div></section>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const $=id=>document.getElementById(id), esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt=n=>n==null?'–':n>=1e4?(n/1e3).toFixed(1)+'k':n.toLocaleString();
const ago=iso=>{if(!iso)return null;const h=(Date.now()-new Date(iso))/36e5;return h<1?Math.round(h*60)+' min':h<48?Math.round(h)+' h':Math.round(h/24)+' d'};
const local=(iso,o)=>new Date(iso).toLocaleString([], o||{dateStyle:'medium',timeStyle:'short'});
const tz=(h,zone)=>{const d=new Date();d.setUTCHours(h,0,0,0);return d.toLocaleTimeString([], {hour:'numeric',minute:'2-digit',timeZone:zone})};
$('built').textContent='Built '+local(D.built)+' · data from the last 28 days';

// health pills
const limits={'YouTube export':3,'Facebook/Instagram export':3,'Topic strategy':6,'Growth plan':30,'Laptop dispatcher':2};
let pills=Object.entries(D.freshness).map(([k,v])=>{const h=v?(Date.now()-new Date(v))/36e5:1e9;const c=h<=limits[k]?'ok':h<=limits[k]*4?'warn':'bad';
 return `<span class="pill"><b class="${c}">●</b> ${esc(k)}: ${v?ago(v)+' ago':'never'}</span>`});
const p=D.health.problems;pills.push(`<span class="pill" title="${esc(p.join('\n'))}"><b class="${p.length?'bad':'ok'}">●</b> Laptop errors (24h): ${p.length}</span>`);
$('pills').innerHTML=pills.join('');

// KPIs
const V=D.videos, now=Date.now(), in7=V.filter(v=>now-new Date(v.at)<7*864e5);
const sum=(a,k)=>a.reduce((s,v)=>s+(v[k]||0),0), avg=(a,k)=>{const x=a.filter(v=>v[k]!=null);return x.length?sum(x,k)/x.length:null};
const next=(()=>{for(let i=0;i<48;i++){const d=new Date();d.setUTCMinutes(0,0,0);d.setUTCHours(d.getUTCHours()+i+1);if(D.hours.includes(d.getUTCHours()))return d}})();
const kpis=[['Videos published',in7.length+' / 7d',V.length+' in 28 days · '+in7.filter(v=>v.scheduled).length+' scheduled this week'],
 ['Facebook views',fmt(sum(V,'fb')),'avg '+fmt(Math.round(avg(V,'fb')||0))+' per Reel · monetized'],
 ['Instagram views',fmt(sum(V,'ig')),'avg '+fmt(Math.round(avg(V,'ig')||0))+' per Reel'],
 ['YouTube views',fmt(sum(V,'yt')),'avg '+fmt(Math.round(avg(V,'yt')||0))+' per Short'],
 ['Facebook likes',fmt(sum(V,'fb_likes')),'IG '+fmt(sum(V,'ig_likes'))+' · YT '+fmt(sum(V,'yt_likes'))],
 ['Next publish',next?local(next.toISOString(),{weekday:'short',hour:'numeric',minute:'2-digit'}):'–',D.posts_per_day+' per day · '+D.hours.length+' slots']];
$('kpis').innerHTML=kpis.map(([l,v,s])=>`<div class="kpi"><div class="l">${l}</div><div class="v">${esc(v)}</div><div class="s">${esc(s)}</div></div>`).join('');

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
 s+=hist.length?'<h4>Recent changes</h4><ul class="small">'+hist.map(c=>`<li>${local(c.at,{dateStyle:'medium'})}: ${String(c.removed).padStart(2,'0')}→${String(c.added).padStart(2,'0')} UTC — ${esc(c.reason)}</li>`).join('')+'</ul>':'<p class="empty small">No hour changes yet.</p>';
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
let sortK='at',sortD=-1;
const opts=(id,k,label)=>{$(id).innerHTML=`<option value="">${label}</option>`+[...new Set(V.map(v=>v[k]).filter(Boolean))].sort().map(x=>`<option>${esc(x)}</option>`).join('')};
opts('fSeries','series','All series');opts('fRegion','region','All regions');
function table(){const q=$('q').value.toLowerCase(),fs=$('fSeries').value,fr=$('fRegion').value;
 const rows=V.filter(v=>(!q||v.title.toLowerCase().includes(q))&&(!fs||v.series==fs)&&(!fr||v.region==fr))
  .sort((a,b)=>{const x=a[sortK],y=b[sortK];return x==null?1:y==null?-1:(x>y?1:x<y?-1:0)*sortD});
 $('vt').innerHTML='<thead><tr>'+cols.map(([k,l,c])=>`<th class="${c||''}" data-k="${k}">${l}${sortK==k?(sortD>0?' ▲':' ▼'):''}</th>`).join('')+'</tr></thead><tbody>'+
 rows.map(v=>`<tr><td>${local(v.at,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}</td><td class="t">${v.yt_id?`<a href="https://youtube.com/shorts/${esc(v.yt_id)}" target="_blank" rel="noopener">${esc(v.title)}</a>`:esc(v.title)}</td><td>${esc(v.series)}</td><td>${esc(v.region)}</td><td>${esc(v.length)}</td><td class="n">${fmt(v.fb)}</td><td class="n">${fmt(v.ig)}</td><td class="n">${fmt(v.yt)}</td><td class="n">${v.yt_pct==null?'–':Math.round(v.yt_pct)+'%'}</td><td class="n">${v.score==null?'<span class="muted" title="under 48h old or not an editorial Short">–</span>':v.score.toFixed(2)}</td></tr>`).join('')+'</tbody>';
 document.querySelectorAll('#vt th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;sortD=sortK==k?-sortD:-1;sortK=k;table()})}
['q','fSeries','fRegion'].forEach(id=>$(id).oninput=table);table();

// strategy
(()=>{const st=D.strategy||{};if(!st.weights){$('strategy').innerHTML='<p class="empty">No strategy yet.</p>';return}
 let s=`<p class="small muted">${st.videos_scored} videos scored · updated ${ago(st.updated)} ago. Weights decide how often each option is picked for new videos.</p>`;
 for(const [dim,w] of Object.entries(st.weights)){const max=Math.max(...Object.values(w));s+=`<h4>${esc(dim.replace('_',' '))}</h4>`+Object.entries(w).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`<div class="wrow"><span>${esc(k)}</span><div class="bar" style="width:${v/max*100}%"></div><span class="n">${Math.round(v*100)}%</span></div>`).join('')}
 if(st.winners)s+='<h4>Best recent</h4><ul class="small">'+st.winners.slice(0,5).map(t=>`<li>${esc(t)}</li>`).join('')+'</ul>';
 $('strategy').innerHTML=s})();

// monetization
(()=>{const m=D.monetization;if(!m){$('money').innerHTML=`<p><b class="warn">No earnings data.</b> Facebook's API gives this app only views, likes and comments, so earnings can't be read automatically yet.</p>
<ul class="small"><li><b>Now:</b> export your earnings CSV in Meta Business Suite (Monetization → Earnings) and run<br><code>.venv\\Scripts\\python scripts\\import_facebook_monetization.py earnings.csv</code><br>in <code>D:\\histold\\YT-Automation</code>. It stays on this laptop (<code>data/private/</code>, never committed).</li>
<li><b>Automatic later:</b> give the Meta app the <code>read_insights</code> permission and Page monetization access, then the hourly export can fetch it.</li></ul>`;return}
 $('money').innerHTML='<pre class="small" style="white-space:pre-wrap">'+esc(JSON.stringify(m,null,2)).slice(0,4000)+'</pre>'})();

// review & gaps
if(D.review){$('reviewTitle').textContent='Weekly review · '+D.review.name;$('review').innerHTML=D.review.html}else $('review').innerHTML='<p class="empty">The first review is written on the laptop\'s next weekly run.</p>';
(()=>{const g=Object.entries(D.gaps);$('gaps').innerHTML=g.length?'<p class="small muted">Metrics the platform APIs did not return for at least one recent video:</p>'+g.map(([p,m])=>`<h4>${esc(p)}</h4><ul class="small">`+Object.entries(m).map(([k,why])=>`<li><code>${esc(k)}</code> — ${esc(why)}</li>`).join('')+'</ul>').join(''):'<p class="ok">All metrics available.</p>'})();
</script></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--out', type=Path, default=DEFAULT_OUT)
    ap.add_argument('--log', type=Path, default=DEFAULT_LOG)
    args = ap.parse_args()
    data = collect(datetime.now(timezone.utc), args.log)
    blob = json.dumps(data, separators=(',', ':'), default=str).replace('</', '<\\/')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix('.tmp')
    tmp.write_text(PAGE.replace('__DATA__', blob), encoding='utf-8')
    tmp.replace(args.out)
    print(f"dashboard: {len(data['videos'])} videos -> {args.out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
