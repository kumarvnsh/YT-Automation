import { useState } from 'react';
import { Activity, ArrowUpRight, BarChart3, Bot, Cable, ClipboardList, Crosshair, FlaskConical, WalletCards } from 'lucide-react';
import type { PlanningView } from './Planning';

export type ShotsnipWorkspace = {
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

export function Workspaces({ data, status, waiting, time, openQueue, openPerformance, openPlanning }: {
  data?: ShotsnipWorkspace; status: string; waiting: number | null; time: (value: string | null) => string;
  openQueue: () => void; openPerformance: () => void; openPlanning: (view: PlanningView) => void;
}) {
  const [channel, setChannel] = useState<'shotsnip' | 'histold'>('shotsnip');
  const [showLive, setShowLive] = useState(false);
  const tools = [
    { title: 'Live operations', icon: Activity, note: 'Active run, scheduler checks, and upload blockers.',
      state: status, href: shotsnip + '#overview', link: 'Full operations tools',
      local: data?.live ? { label: showLive ? 'Hide live status' : 'View live status', action: () => setShowLive(!showLive) } : undefined },
    { title: 'Targeted channels', icon: Crosshair, note: 'Manage the creators and streams Shotsnip monitors.',
      state: data?.targets != null ? data.active_targets + ' active of ' + data.targets : '',
      href: shotsnip + '#targets', link: 'Manage targets' },
    { title: 'Work queue', icon: ClipboardList, note: 'View automatic work, reprioritize jobs, and inspect failed stages.',
      state: waiting === null ? '' : waiting + ' waiting', href: shotsnip + '#jobs', link: 'Open full queue',
      local: { label: 'Studio HQ queue', action: openQueue } },
    { title: 'Bot workspace', icon: Bot, note: 'Assignments, assistant reports, and bot routines.',
      href: '/bots?channel=shotsnip', link: 'Open bot workspace' },
    { title: 'Connections', icon: Cable, note: 'Publishing, analytics, and capture connections.',
      href: shotsnip + '#connections', link: 'Manage connections', connections: data?.connections },
    { title: 'Agent', icon: FlaskConical, note: data?.agent_at ? 'Reviewed ' + time(data.agent_at) + ' IST' : 'Open source signals and capture review.',
      state: data?.learning == null ? '' : data.learning ? 'Learning enabled' : 'Learning paused',
      href: shotsnip + '#agent', link: 'Open agent' },
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
