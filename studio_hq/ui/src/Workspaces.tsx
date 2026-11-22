import { useEffect, useState } from 'react';
import { ArrowUpRight } from './icons';
import type { CommandEvent, Send } from './Controls';

export type Target = { id: string; name: string; enabled: boolean; sources: { platform: 'youtube' | 'twitch'; label: string }[] };
export type AgentReport = {
  checked_at: string | null; learning: boolean | null; policy_updated_at: string | null;
  priorities: { creator: string; priority: number }[];
  findings: { creator: string; samples: number; priority: number | null; median_views: number }[];
  errors: { run: string; creator: string; error: string }[];
  actions: string[]; next_steps: string[]; sample_rule: string; comparison: string; scope: string;
};
export type ShotsnipWorkspace = {
  agent?: AgentReport;
  target_list?: Target[] | null; targets_revision?: string | null;
  targets: number | null; active_targets: number | null; agent_at: string | null; learning: boolean | null;
  connections: { name: string; status: string }[];
  live?: { phase: string; label: string; heartbeat: string | null; dispatcher_at: string | null;
    source: string; replay: boolean; reserve: number | null; reserve_limit: number | null;
    pending_publications: number; events: {at: string; phase: string}[];
    enabled?: boolean | null; run_now?: boolean; stop_after?: boolean;
    hold?: { batch: string; total: number; published: number | null; paused_at: string | null } | null;
    commentary?: string; watch_worker?: { status: string; checked_at: string | null; note: string; stop_requested: boolean };
    timers?: { combined: boolean; scan_minutes: number; post_minutes: number; next_cycle: string | null; next_post: string | null;
      stage_started: string | null; last_upload: string | null } };
};


const BUSY = ['scanning', 'capturing', 'analyzing', 'finishing', 'uploading'];
const clock = (seconds: number) => {
  const n = Math.max(0, Math.ceil(seconds));
  return [Math.floor(n / 3600), Math.floor(n % 3600 / 60), n % 60].map(part => String(part).padStart(2, '0')).join(':');
};

/** Countdown to the next live cycle (and post), ticking every second, like the original Shotsnip dashboard. */
export function LiveTimers({ live, time }: { live: NonNullable<ShotsnipWorkspace['live']>; time: (value: string | null) => string }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const id = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(id); }, []);
  const timers = live.timers;
  if (!timers) return null;
  const busy = BUSY.includes(live.phase);
  const left = (iso: string | null) => iso ? (Date.parse(iso) - now) / 1000 : null;
  const cycle = left(timers.next_cycle);
  const post = left(timers.next_post);
  const cycleText = live.enabled !== true ? 'Paused' : busy ? 'Running now' : cycle == null ? '—' : cycle <= 0 ? 'Due now' : clock(cycle);
  const postText = live.enabled !== true ? 'Paused' : timers.combined ? 'After processing' : live.phase === 'uploading' ? 'Uploading'
    : post == null ? '—' : post <= 0 ? 'Due now' : clock(post);
  const elapsed = busy && timers.stage_started ? Math.floor((now - Date.parse(timers.stage_started)) / 60000) : null;
  return <section className="panel live-timers-card" aria-labelledby="live-timers-heading">
    <div className="panel-head"><div><h2 id="live-timers-heading">Next live cycle</h2>
      <p>{timers.combined ? `Each cycle scans, captures and posts, every ${Math.round(timers.scan_minutes / 60 * 10) / 10} h after the last cycle or upload.`
        : `Scans every ${timers.scan_minutes} min; posts every ${timers.post_minutes} min.`}</p></div></div>
    <div className="live-timer-grid">
      <div><small>{timers.combined ? 'Next cycle in' : 'Next scan in'}</small><strong className="live-timer" aria-live="off">{cycleText}</strong>
        {timers.next_cycle && live.enabled === true && !busy && <small>at {time(timers.next_cycle)} IST</small>}</div>
      <div><small>Next post</small><strong className="live-timer">{postText}</strong>
        {timers.last_upload && <small>Last upload {time(timers.last_upload)} IST</small>}</div>
    </div>
    {elapsed != null && <p className="control-note">Current stage running for {elapsed} min.</p>}
  </section>;
}

export function LiveOperations({ live, status, waiting, time, close }: {
  live: NonNullable<ShotsnipWorkspace['live']>; status: string; waiting: number | null;
  time: (value: string | null) => string; close?: () => void;
}) {
  const stages = [['scanning','Find stream'],['capturing','Capture'],['analyzing','Pick highlight'],
    ['finishing','Finish clip'],['uploading','Upload']];
  const active = stages.findIndex(([phase]) => phase === live.phase);
  const stale = active >= 0 && (!live.heartbeat || Date.now() - Date.parse(live.heartbeat) > 30 * 60 * 1000);
  const flagged = stale || live.pending_publications > 0 || status === 'On hold';
  return <section className="panel live-operations" aria-labelledby="live-heading">
    <div className="panel-head planning-head">
      <div><h2 id="live-heading">Shotsnip live operations</h2><p>Current stage and recent events from the Windows worker.</p></div>
      <div className="tool-links"><span className={'status ' + (flagged ? 'warn' : 'neutral')}>{status}</span>
        {close && <button className="action" onClick={close}>Close live view</button>}</div>
    </div>
    <div className="live-metrics">
      <div><small>Worker stage</small><strong>{live.label}</strong></div>
      <div><small>Manual jobs waiting</small><strong>{waiting ?? '—'}</strong></div>
      <div><small>Finished reserve</small><strong>{live.reserve ?? '—'}{live.reserve_limit != null ? ' / ' + live.reserve_limit : ''}</strong></div>
    </div>
    <ol className="live-stages" aria-label="Automatic clipping stages">
      {stages.map(([phase,label],index) => <li key={phase} aria-current={index === active ? 'step' : undefined}>{label}</li>)}
    </ol>
    <div className="planning-detail">
      {live.source && <span>{live.replay ? 'Replay' : 'Selected source'} · {live.source}</span>}
      {live.heartbeat && <span>Worker heartbeat {time(live.heartbeat)} IST</span>}
      {live.dispatcher_at && <span>Dispatcher checked {time(live.dispatcher_at)} IST</span>}
    </div>
    {stale && <p className="chart-note">The active stage’s worker heartbeat is stale.</p>}
    {live.pending_publications > 0 && <p className="chart-note">{live.pending_publications} publication outcomes need a review in the full queue.</p>}
    <details className="live-history"><summary>Recent worker events</summary>
      {live.events.length ? <ol>{live.events.map((item,index) =>
        <li key={item.at + index}><time dateTime={item.at}>{time(item.at)} IST</time><span>{item.phase}</span></li>)}</ol>
        : <p className="chart-note">No recent events reported.</p>}
    </details>
  </section>;
}

const platformNames = { youtube: 'YouTube', twitch: 'Twitch' };
const targetPattern = /^https:\/\/(?:www\.)?(?:youtube\.com\/(?:@[\w.-]+|channel\/UC[\w-]{22})|twitch\.tv\/\w{3,25})\/?$/i;

export function Targets({ list, revision, send, busy, recent, time, close }: {
  list: Target[]; revision: string | null; send?: Send; busy: boolean; recent?: CommandEvent;
  time: (value: string | null) => string; close?: () => void;
}) {
  const [filter, setFilter] = useState<'all' | 'active' | 'paused'>('all');
  const [search, setSearch] = useState('');
  const [limit, setLimit] = useState(12);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [url, setUrl] = useState('');
  const query = search.trim().toLowerCase();
  const shown = list.filter(item => (filter === 'all' || item.enabled === (filter === 'active')) &&
    (item.name.toLowerCase().includes(query) || item.sources.some(source => source.label.toLowerCase().includes(query))));
  const active = list.filter(item => item.enabled).length;
  const validUrl = targetPattern.test(url.trim());
  const editable = !!send && !!revision;
  return <section className="panel targets-panel" aria-labelledby="targets-heading">
    <div className="panel-head planning-head">
      <div><h2 id="targets-heading">Targeted channels</h2><p>{active} active of {list.length} creators Shotsnip monitors.</p></div>
      {close && <div className="tool-links"><button className="action" onClick={close}>Close targets</button></div>}
    </div>
    {editable && <form className="target-add" onSubmit={event => {
      event.preventDefault();
      if (!validUrl || busy) return;
      send({action:'target_add',channel:'shotsnip',url:url.trim()});
      setUrl('');
    }}>
      <label className="planning-search">Add a YouTube channel or Twitch profile
        <input type="url" inputMode="url" value={url} onChange={event => setUrl(event.target.value)}
          placeholder="https://www.youtube.com/@handle" autoComplete="off" spellCheck={false} /></label>
      <button className="action primary" type="submit" disabled={busy || !validUrl}>Add target</button>
      {url.trim() && !validUrl && <p className="chart-note">Use a YouTube @handle or /channel/ URL, or a Twitch profile URL.</p>}
    </form>}
    {recent && <p className="analytics-info" role="status">{recent.message} · {time(recent.at)} IST</p>}
    <div className="calendar-filter">
      <div className="filters" role="group" aria-label="Target status">
        {(['all','active','paused'] as const).map(item => <button key={item} aria-pressed={filter === item}
          onClick={() => {setFilter(item);setLimit(12);}}>{item === 'all' ? 'All' : item === 'active' ? 'Active' : 'Paused'}</button>)}
      </div>
    </div>
    <label className="planning-search">Find a creator<input type="search" value={search} placeholder="Search name or handle"
      onChange={event => {setSearch(event.target.value);setLimit(12);}} /></label>
    {shown.length === 0 && <p className="empty">No matching targets.</p>}
    {shown.slice(0, limit).map(item => <article className="control-row target-row" key={item.id}>
      <div className="control-row-title"><strong>{item.name}</strong>
        <small>{item.sources.map(source => platformNames[source.platform] + ' ' + source.label).join(' · ') || 'No supported source'}</small></div>
      <div className="control-actions">
        <span className={'status ' + (item.enabled ? 'good' : 'neutral')}>{item.enabled ? 'Active' : 'Paused'}</span>
        {editable && confirm !== item.id && <button className="action" disabled={busy}
          onClick={() => setConfirm(item.id)}>{item.enabled ? 'Pause' : 'Enable'}</button>}
      </div>
      {editable && confirm === item.id && <div className="cadence-confirm" role="group" aria-label={'Confirm change for ' + item.name}>
        <p>{item.enabled ? 'Pause ' + item.name + '? Shotsnip stops scanning these sources from its next scan.'
          : 'Enable ' + item.name + '? Shotsnip scans these sources again from its next scan.'}</p>
        <button className="action" onClick={() => setConfirm(null)}>Back</button>
        <button className="action primary" disabled={busy} onClick={() => {
          send!({action:'target_toggle',channel:'shotsnip',id:item.id,enabled:!item.enabled,revision:revision!});
          setConfirm(null);
        }}>{item.enabled ? 'Confirm pause' : 'Confirm enable'}</button>
      </div>}
    </article>)}
    {shown.length > limit && <div className="panel-foot"><button onClick={() => setLimit(value => value + 12)}>Show more targets</button></div>}
    {!editable && <p className="chart-note">Target changes are available when Studio HQ controls are enabled.</p>}
  </section>;
}

const count = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });

export function AgentPanel({ data, send, busy, recent, time, close }: {
  data: AgentReport; send?: Send; busy: boolean; recent?: CommandEvent;
  time: (value: string | null) => string; close?: () => void;
}) {
  const [confirm, setConfirm] = useState<'learning' | 'refresh' | null>(null);
  const [errorCount, setErrorCount] = useState(5);
  const learning = data.learning;
  return <section className="panel experiment-panel agent-panel" aria-labelledby="agent-heading">
    <div className="panel-head planning-head">
      <div><h2 id="agent-heading">Shotsnip agent</h2>
        <p>{data.checked_at ? 'Last review ' + time(data.checked_at) + ' IST' : 'No review has been recorded.'}</p></div>
      <div className="tool-links">
        {learning != null && <span className={'status ' + (learning ? 'good' : 'neutral')}>{learning ? 'Learning enabled' : 'Learning paused'}</span>}
        {close && <button className="action" onClick={close}>Close agent</button>}</div>
    </div>
    {send && <div className="planning-detail agent-actions">
      <button className="action" disabled={busy} onClick={() => send({action:'agent_review',channel:'shotsnip',mode:'review'})}>Run review</button>
      <button className="action" disabled={busy || confirm !== null} onClick={() => setConfirm('refresh')}>Refresh analytics and review</button>
      {learning != null && <button className="action" disabled={busy || confirm !== null}
        onClick={() => setConfirm('learning')}>{learning ? 'Pause learning' : 'Enable learning'}</button>}
    </div>}
    {send && confirm && <div className="cadence-confirm" role="group" aria-label="Confirm agent change">
      <p>{confirm === 'refresh' ? 'Sync Shotsnip analytics from the platforms, then run a review? This can take a few minutes.'
        : learning ? 'Pause learning? Current source priorities stay as they are until learning is enabled again.'
        : 'Enable learning? The next review recomputes source priorities from measured results (0.5–1.5).'}</p>
      <button className="action" onClick={() => setConfirm(null)}>Back</button>
      <button className="action primary" disabled={busy} onClick={() => {
        if (confirm === 'refresh') send({action:'agent_review',channel:'shotsnip',mode:'refresh'});
        else send({action:'agent_learning',channel:'shotsnip',enabled:!learning,expected:!!learning});
        setConfirm(null);
      }}>{confirm === 'refresh' ? 'Confirm refresh' : learning ? 'Confirm pause' : 'Confirm enable'}</button>
    </div>}
    {recent && <p className="analytics-info" role="status">{recent.message} · {time(recent.at)} IST</p>}
    <h3 className="agent-subhead">Source priorities</h3>
    {data.findings.length ? <table><thead><tr><th scope="col">Creator</th><th scope="col">Views near 24 h</th>
      <th scope="col">Clips</th><th scope="col">Weight</th></tr></thead>
      <tbody>{data.findings.map(row => <tr key={row.creator}><th scope="row">{row.creator}</th>
        <td>{count.format(row.median_views)}</td><td>{row.samples}</td>
        <td>{row.priority != null ? '×' + row.priority.toFixed(2) : '—'}</td></tr>)}</tbody></table>
      : <p className="empty">No creator has enough measured clips yet.</p>}
    {data.priorities.length > 0 && <p className="chart-note">Applied weights: {data.priorities.map(row => row.creator + ' ×' + row.priority.toFixed(2)).join(' · ')}
      {data.policy_updated_at ? ' · updated ' + time(data.policy_updated_at) + ' IST' : ''}</p>}
    {(data.actions.length > 0 || data.next_steps.length > 0) && <ul className="agent-notes">
      {data.actions.map(item => <li key={'a' + item}>{item}</li>)}
      {data.next_steps.map(item => <li key={'n' + item}>{item}</li>)}</ul>}
    <h3 className="agent-subhead">Capture errors</h3>
    {data.errors.length ? <ol className="agent-errors">{data.errors.slice(0, errorCount).map(row => <li key={row.run}>
      <strong>{row.creator || 'Unknown creator'}</strong><span>{row.error}</span><small>{row.run}</small></li>)}</ol>
      : <p className="empty">No capture errors in recent runs.</p>}
    {data.errors.length > errorCount && <div className="panel-foot"><button onClick={() => setErrorCount(data.errors.length)}>Show all errors</button></div>}
    <p className="chart-note">{[data.sample_rule, data.comparison, data.scope].filter(Boolean).join(' ')}</p>
  </section>;
}

const original = { shotsnip: '/?channel=shotsnip', histold: '/?channel=histold' };
const originalTools = {
  shotsnip: [
    ['Work queue', 'Automatic work, priorities, and failed stages', original.shotsnip + '#jobs'],
    ['Connections', 'Publishing, analytics, and capture accounts', original.shotsnip + '#connections'],
    ['Bot workspace', 'Assignments, reports, and routines', '/bots?channel=shotsnip'],
  ],
  histold: [
    ['Strategy tools', 'Topic decisions and strategy controls', original.histold + '#/strategy'],
    ['Earnings report', 'Full platform earnings detail', original.histold + '#/earnings'],
    ['Assistant chat', 'Full conversation view', original.histold + '#/ai'],
    ['Bot workspace', 'Assignments, reports, and routines', '/bots?channel=histold'],
  ],
} as const;

/** Tools that still live in the original dashboard; they open in a browser tab with its own sign-in. */
export function OriginalTools({ connections }: { connections?: { name: string; status: string }[] }) {
  return <div className="planning-body">
    {(['shotsnip', 'histold'] as const).map(channel => <section className="panel" key={channel}>
      <div className="panel-head"><div><h2>{channel === 'shotsnip' ? 'Shotsnip' : 'Histold'}</h2>
        <p>Opens the original dashboard in a new tab.</p></div></div>
      <ul className="link-list">{originalTools[channel].map(([title, note, href]) => <li key={title}>
        <a href={href} target="_blank" rel="noreferrer"><span><strong>{title}</strong><small>{note}</small></span><ArrowUpRight size={16}/></a>
      </li>)}</ul>
      {channel === 'shotsnip' && connections && <dl className="connection-list">{connections.map(row =>
        <div key={row.name}><dt>{row.name}</dt><dd>{row.status}</dd></div>)}</dl>}
    </section>)}
  </div>;
}
