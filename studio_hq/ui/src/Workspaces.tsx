import { useState } from 'react';
import { Activity, ArrowUpRight, BarChart3, Bot, Cable, ClipboardList, Crosshair, FlaskConical, WalletCards } from 'lucide-react';
import type { PlanningView } from './Planning';
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
    pending_publications: number; events: {at: string; phase: string}[] };
};

const shotsnip = '/?channel=shotsnip';
const histold = '/?channel=histold';

function LiveOperations({ live, status, waiting, time, close }: {
  live: NonNullable<ShotsnipWorkspace['live']>; status: string; waiting: number | null;
  time: (value: string | null) => string; close: () => void;
}) {
  const stages = [['scanning','Find stream'],['capturing','Capture'],['analyzing','Pick highlight'],
    ['finishing','Finish clip'],['uploading','Upload']];
  const active = stages.findIndex(([phase]) => phase === live.phase);
  const stale = active >= 0 && (!live.heartbeat || Date.now() - Date.parse(live.heartbeat) > 30 * 60 * 1000);
  const flagged = stale || live.pending_publications > 0 || status === 'On hold';
  return <section className="panel live-operations" aria-labelledby="live-heading">
    <div className="panel-head planning-head">
      <div><h2 id="live-heading">Live operations</h2><p>Current stage and recent events from the Windows worker.</p></div>
      <div className="tool-links"><span className={'status ' + (flagged ? 'warn' : 'neutral')}>{status}</span>
        <button className="action" onClick={close}>Close live view</button></div>
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

function Targets({ list, revision, send, busy, recent, time, close }: {
  list: Target[]; revision: string | null; send?: Send; busy: boolean; recent?: CommandEvent;
  time: (value: string | null) => string; close: () => void;
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
      <div className="tool-links"><button className="action" onClick={close}>Close targets</button></div>
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

function AgentPanel({ data, send, busy, recent, time, close }: {
  data: AgentReport; send?: Send; busy: boolean; recent?: CommandEvent;
  time: (value: string | null) => string; close: () => void;
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
        <button className="action" onClick={close}>Close agent</button></div>
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

export function Workspaces({ data, status, waiting, time, openQueue, openPerformance, openPlanning, send, busy = false, commands = [] }: {
  data?: ShotsnipWorkspace; status: string; waiting: number | null; time: (value: string | null) => string;
  openQueue: () => void; openPerformance: () => void; openPlanning: (view: PlanningView) => void;
  send?: Send; busy?: boolean; commands?: CommandEvent[];
}) {
  const [channel, setChannel] = useState<'shotsnip' | 'histold'>('shotsnip');
  const [showLive, setShowLive] = useState(false);
  const [showTargets, setShowTargets] = useState(false);
  const [showAgent, setShowAgent] = useState(false);
  const tools = [
    { title: 'Live operations', icon: Activity, note: 'Active run, scheduler checks, and upload blockers.',
      state: status, href: shotsnip + '#overview', link: 'Full operations tools',
      local: data?.live ? { label: showLive ? 'Hide live status' : 'View live status', action: () => setShowLive(!showLive) } : undefined },
    { title: 'Targeted channels', icon: Crosshair, note: 'Manage the creators and streams Shotsnip monitors.',
      state: data?.targets != null ? data.active_targets + ' active of ' + data.targets : '',
      href: shotsnip + '#targets', link: 'Original targets tool',
      local: data?.target_list ? { label: showTargets ? 'Hide targets' : 'Manage in Studio HQ', action: () => setShowTargets(!showTargets) } : undefined },
    { title: 'Work queue', icon: ClipboardList, note: 'View automatic work, reprioritize jobs, and inspect failed stages.',
      state: waiting === null ? '' : waiting + ' waiting', href: shotsnip + '#jobs', link: 'Open full queue',
      local: { label: 'Studio HQ queue', action: openQueue } },
    { title: 'Bot workspace', icon: Bot, note: 'Assignments, assistant reports, and bot routines.',
      href: '/bots?channel=shotsnip', link: 'Open bot workspace' },
    { title: 'Connections', icon: Cable, note: 'Publishing, analytics, and capture connections.',
      href: shotsnip + '#connections', link: 'Manage connections', connections: data?.connections },
    { title: 'Agent', icon: FlaskConical, note: data?.agent_at ? 'Reviewed ' + time(data.agent_at) + ' IST' : 'Open source signals and capture review.',
      state: data?.learning == null ? '' : data.learning ? 'Learning enabled' : 'Learning paused',
      href: shotsnip + '#agent', link: 'Original agent tool',
      local: data?.agent ? { label: showAgent ? 'Hide agent' : 'Open in Studio HQ', action: () => setShowAgent(!showAgent) } : undefined },
    { title: 'Performance', icon: BarChart3, note: 'Platform trends, top clips, and matched-age observations.',
      href: shotsnip + '#performance', link: 'Full performance', local: { label: 'Studio HQ analytics', action: openPerformance } },
  ];
  const histoldTools = [
    { title: 'Earnings', icon: WalletCards, note: 'Facebook and YouTube reports with their original reporting periods.',
      href: histold + '#/earnings', link: 'Full earnings report', local: { label: 'Studio HQ earnings', action: () => openPlanning('money') } },
    { title: 'Strategy', icon: FlaskConical, note: 'Topic decisions, source evidence, and existing strategy controls.',
      href: histold + '#/strategy', link: 'Full strategy tools', local: { label: 'Studio HQ strategy', action: () => openPlanning('strategy') } },
    { title: 'Assistant', icon: Activity, note: 'Ask questions about current channel evidence and review action proposals.',
      href: histold + '#/ai', link: 'Open assistant' },
    { title: 'Bot workspace', icon: Bot, note: 'Assignments, assistant reports, and bot routines.',
      href: '/bots?channel=histold', link: 'Open bot workspace' },
  ];
  const entries: typeof tools = channel === 'shotsnip' ? tools : histoldTools;
  return <div className="planning-body">
    <div className="filters source-filters" role="group" aria-label="Workspace channel">
      <button aria-pressed={channel === 'shotsnip'} onClick={() => setChannel('shotsnip')}>Shotsnip</button>
      <button aria-pressed={channel === 'histold'} onClick={() => setChannel('histold')}>Histold</button>
    </div>
    <p className="analytics-info">Full tools open in the original dashboard and use its existing sign-in.</p>
    {channel === 'shotsnip' && showLive && data?.live &&
      <LiveOperations live={data.live} status={status} waiting={waiting} time={time} close={() => setShowLive(false)} />}
    {channel === 'shotsnip' && showTargets && data?.target_list &&
      <Targets list={data.target_list} revision={data.targets_revision ?? null} send={send} busy={busy}
        recent={commands.find(event => event.action === 'target_add' || event.action === 'target_toggle')}
        time={time} close={() => setShowTargets(false)} />}
    {channel === 'shotsnip' && showAgent && data?.agent &&
      <AgentPanel data={data.agent} send={send} busy={busy} time={time} close={() => setShowAgent(false)}
        recent={commands.find(event => event.action === 'agent_review' || event.action === 'agent_learning')} />}
    <div className="tools-grid">
      {entries.map(tool => <article className="panel tool-card" key={tool.title}>
        <div className="tool-heading"><span className="performance-icon"><tool.icon size={18}/></span>
          <h2>{tool.title}</h2></div>
        <p>{tool.note}</p>
        {tool.state && <small className="tool-state">{tool.state}</small>}
        {tool.connections && <dl>{tool.connections.map(row => <div key={row.name}><dt>{row.name}</dt><dd>{row.status}</dd></div>)}</dl>}
        <div className="tool-links">
          {tool.local && <button className="action" onClick={tool.local.action}>{tool.local.label}</button>}
          <a className="action" href={tool.href} target="_blank" rel="noreferrer">{tool.link} <ArrowUpRight size={15}/></a>
        </div>
      </article>)}
    </div>
  </div>;
}
