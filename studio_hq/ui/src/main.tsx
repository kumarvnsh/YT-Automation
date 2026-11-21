import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Activity, Archive, ArrowDownToLine, ArrowUpRight, BarChart3, Check, CircleAlert, Clock3,
  CalendarDays, Cloud, Database, Film, HardDrive, LayoutGrid, PanelsTopLeft, Radio, RefreshCw, ShieldCheck, SlidersHorizontal,
} from 'lucide-react';
import { Controls, type Approval, type Command, type CommandEvent, type ManualJob, type Send } from './Controls';
import { Analytics, ChannelCards, ReportingChart, VideoRanking, channelNames, type Pacing, type StudioAnalytics, type StudioChannel } from './Analytics';
import { Planning, type JobLink, type PlanningData, type PlanningView } from './Planning';
import { Workspaces, type ShotsnipWorkspace } from './Workspaces';
import './styles.css';

type ChannelId = StudioChannel;
type Channel = { id: ChannelId; name: string; archived: number; last_published: string | null; activity_count: number };
type Item = { channel: ChannelId; kind: string; title: string; status: string; at: string | null; url: string | null };
type ArchiveRow = { id: string; channel: ChannelId; title: string; archived_at: string | null; state: string; size: number | null; url: string | null };
type Snapshot = {
  generated_at: string;
  channels: Channel[];
  activity: Item[];
  archives: ArchiveRow[];
  analytics?: StudioAnalytics;
  pacing_experiment?: Pacing | null;
  phase3?: PlanningData;
  shotsnip_workspace?: ShotsnipWorkspace;
  controls_enabled: boolean;
  commands: CommandEvent[];
  approvals: Approval[];
  backup_at: string | null;
  health: { queue_available: boolean; waiting: number; attention: number; scheduler_status: string; scheduler_at: string | null;
    scheduler_enabled: boolean | null; scheduler_hold: boolean; manual_jobs: ManualJob[] };
  storage: { free: number; total: number; drive_only: number; archive_count: number };
  operations: {
    archive: { at: string | null; copied: number; errors: number };
    cleanup: { at: string | null; status: string; removed: number; bytes_removed: number; channel: ChannelId | null };
    raw: { removed: number | null; bytes_removed: number | null };
  };
};

const labels = channelNames;
const abbreviations = { histold: 'H', shotsnip: 'S' };
const formatTime = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit', hour12: false,
});
const formatDay = new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', weekday: 'long', day: 'numeric', month: 'long' });

function timestamp(value: string | null) {
  if (!value || !Number.isFinite(Date.parse(value))) return '—';
  return formatTime.format(new Date(value));
}

function bytes(value: number | null) {
  if (value === null || !Number.isFinite(value)) return '—';
  if (value === 0) return '0 B';
  const scale = Math.min(3, Math.max(0, Math.floor(Math.log(value) / Math.log(1024))));
  return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 1 }).format(value / 1024 ** scale)
    + ' ' + ['B', 'KiB', 'MiB', 'GiB'][scale];
}

function Status({ children }: { children: string }) {
  const tone = /failed|attention|error/i.test(children) ? 'warn'
    : /published|verified|drive/i.test(children) ? 'good' : 'neutral';
  return <span className={'status ' + tone}><span aria-hidden="true" />{children}</span>;
}

function ChannelMark({ id }: { id: ChannelId }) {
  return <span aria-hidden="true" className={'channel-mark ' + id}>{abbreviations[id]}</span>;
}

function RowLink({ href, title }: { href: string; title: string }) {
  return <a className="icon-link" href={href} target="_blank" rel="noreferrer" aria-label={title} title={title}>
    <ArrowUpRight size={17} />
  </a>;
}

function ActivityList({ items }: { items: Item[] }) {
  if (!items.length) return <p className="empty">No activity for this channel.</p>;
  return <div className="data-list">
    {items.map((row, index) => <article className="data-row" key={row.channel + row.kind + row.at + index}>
      <ChannelMark id={row.channel} />
      <div className="row-main"><strong>{row.title}</strong><small>{labels[row.channel]} · {row.kind}</small></div>
      <div className="row-when"><Status>{row.status}</Status><small>{timestamp(row.at)}</small></div>
      {row.url ? <RowLink href={row.url} title={'Watch ' + row.title + ' on YouTube'} /> : <span className="link-space" />}
    </article>)}
  </div>;
}

function ArchiveList({ items, restore, busy }: { items: ArchiveRow[]; restore?: Send; busy: boolean }) {
  if (!items.length) return <p className="empty">No archived clips for this channel.</p>;
  return <div className="data-list">
    {items.map(row => <article className="data-row" key={row.id}>
      <ChannelMark id={row.channel} />
      <div className="row-main"><strong>{row.title}</strong><small>{labels[row.channel]} · {bytes(row.size)}</small>
        {restore && row.state !== 'Verification pending' && row.url &&
          <button className="restore-action" disabled={busy} onClick={() => restore({action: 'restore', archive_id: row.id})}>Restore to PC</button>}
      </div>
      <div className="row-when"><Status>{row.state}</Status><small>{timestamp(row.archived_at)}</small></div>
      {row.url ? <RowLink href={row.url} title={'Open ' + row.title + ' in Google Drive'} /> : <span className="link-space" />}
    </article>)}
  </div>;
}

function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [view, setView] = useState<'overview' | 'analytics' | 'planning' | 'workspaces' | 'archive' | 'controls'>('overview');
  const [planningView, setPlanningView] = useState<PlanningView>('calendar');
  const [jobFocus, setJobFocus] = useState<JobLink | null>(null);
  const [analyticsChannel, setAnalyticsChannel] = useState<ChannelId>('histold');
  const [channel, setChannel] = useState<ChannelId | 'all'>('all');
  const [issue, setIssue] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [request, setRequest] = useState(0);
  const [pageSize, setPageSize] = useState(12);
  const [sending, setSending] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [retry, setRetry] = useState<Command | null>(null);

  async function sendRequest(body: Command) {
    setSending(true);
    setFeedback('Sending request…');
    try {
      const result = await fetch(new URL('api/command', window.location.href), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Studio-HQ': '1' },
        body: JSON.stringify(body),
      });
      if (result.status >= 500) throw new Error('Request unavailable');
      const data = await result.json();
      setRetry(null);
      setFeedback(data.message || (result.ok ? 'Request received.' : 'Request denied.'));
      setRequest(value => value + 1);
    } catch {
      setRetry(body);
      setFeedback('The outcome is unknown. Check the activity or retry the same request.');
    } finally {
      setSending(false);
    }
  }
  const send: Send = body => { void sendRequest({ ...body, request_id: crypto.randomUUID() }); };
  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    async function load() {
      if (busy) return;
      busy = true;
      setRefreshing(true);
      try {
        const res = await fetch(new URL('api/snapshot', window.location.href), { signal: controller.signal, cache: 'no-store' });
        if (!res.ok) throw new Error('Snapshot unavailable');
        const data: Snapshot = await res.json();
        if (!controller.signal.aborted) { setSnapshot(data); setIssue(false); }
      } catch {
        if (!controller.signal.aborted) setIssue(true);
      } finally {
        busy = false;
        if (!controller.signal.aborted) setRefreshing(false);
      }
    }
    void load();
    const interval = setInterval(load, 30_000);
    return () => { controller.abort(); clearInterval(interval); };
  }, [request]);

  const archive = snapshot?.operations.archive;
  const cleanup = snapshot?.operations.cleanup;
  const stale = snapshot !== null && Date.now() - Date.parse(snapshot.generated_at) > 90_000;
  const current = issue || stale ? 'Snapshot delayed' : snapshot ? 'Connected' : 'Connecting';
  const visible = (row: { channel: ChannelId }) => row.channel in labels && (channel === 'all' || row.channel === channel);
  const rows = (snapshot?.activity || []).filter(visible);
  const files = (snapshot?.archives || []).filter(visible);
  const chooseView = (next: typeof view) => { setView(next); setPageSize(12); window.scrollTo(0, 0); };
  const chooseChannel = (next: typeof channel) => { setChannel(next); setPageSize(12); };
  const count = view === 'overview' ? rows.length : files.length;
  const performance = snapshot?.analytics;
  const trend = performance?.shotsnip.find(item => item.id === 'youtube-daily');
  const topHistold = performance?.histold.find(item => item.id === 'facebook-reels');
  function openAnalytics(next: ChannelId) {
    setAnalyticsChannel(next);
    chooseView('analytics');
  }
  function openPlanning(next: PlanningView) {
    setJobFocus(null);
    setPlanningView(next);
    chooseView('planning');
  }
  function openJob(job: JobLink) {
    setJobFocus(job);
    setPlanningView('jobs');
    chooseView('planning');
  }

  return <div className="shell">
    <a className="skip" href="#content">Skip to content</a>
    <aside className="rail">
      <div className="brand"><div className="brand-mark"><Film size={21}/></div><div><strong>Studio HQ</strong><span>Creator workspace</span></div></div>
      <nav aria-label="Studio navigation">
        <button className={view === 'overview' ? 'nav-active' : ''} aria-current={view === 'overview' ? 'page' : undefined} onClick={() => chooseView('overview')}><LayoutGrid size={18}/>Overview</button>
        <button className={view === 'analytics' ? 'nav-active' : ''} aria-current={view === 'analytics' ? 'page' : undefined}
          onClick={() => chooseView('analytics')}><BarChart3 size={18}/>Analytics</button>
        <button className={view === 'planning' ? 'nav-active' : ''} aria-current={view === 'planning' ? 'page' : undefined}
          onClick={() => chooseView('planning')}><CalendarDays size={18}/>Planning</button>
        <button className={view === 'workspaces' ? 'nav-active' : ''} aria-current={view === 'workspaces' ? 'page' : undefined}
          onClick={() => chooseView('workspaces')}><PanelsTopLeft size={18}/>Workspaces</button>
        <button className={view === 'archive' ? 'nav-active' : ''} aria-current={view === 'archive' ? 'page' : undefined} onClick={() => chooseView('archive')}><Archive size={18}/>Archive</button>
        {snapshot?.controls_enabled && <button className={view === 'controls' ? 'nav-active' : ''} aria-current={view === 'controls' ? 'page' : undefined}
          onClick={() => chooseView('controls')}><SlidersHorizontal size={18}/>Controls</button>}
      </nav>
      <div className="rail-foot"><a href="/" target="_blank" rel="noreferrer">Original dashboard <ArrowUpRight size={14}/></a>
        <span><ShieldCheck size={15}/>Private access</span></div>
    </aside>
    <main id="content">
      <header className="topline">
        <span className="today">{formatDay.format(new Date())} <span className="tz">IST</span></span>
        <div className="top-actions"><span className={'connection ' + (issue || stale ? 'is-delayed' : '')}><i />{current}</span>
          <button className="refresh" onClick={() => setRequest(value => value + 1)} disabled={refreshing} aria-label="Refresh dashboard" title="Refresh dashboard"><RefreshCw size={17}/></button>
        </div>
      </header>
      <section className="intro">
        <h1>{{ overview: 'Studio overview', analytics: 'Channel analytics', planning: 'Studio planning',
          workspaces: 'Channel workspaces', archive: 'Media archive', controls: 'Studio controls' }[view]}</h1>
        <p>{{ overview: 'Channel performance and publishing operations for Histold and Shotsnip.',
          analytics: 'Explore performance by channel, platform, and reporting period.',
          planning: 'Publishing calendar, experiment evidence, and financial reports.',
          workspaces: 'Find the operating tools for Shotsnip and Histold.',
          archive: 'Find finished videos and open verified copies in Drive.',
          controls: 'Review clips, request drafts, and manage work on Windows.' }[view]}</p>
      </section>
      {issue || stale ? <div className="notice" role="status"><CircleAlert size={17}/><span>{snapshot ? 'Showing the last available snapshot.' : 'The studio snapshot is unavailable. Try refreshing.'}</span></div> : null}
      {feedback && <div className="command-feedback" role="status"><span>{feedback}</span>
        {retry && <button className="action" disabled={sending} onClick={() => void sendRequest(retry)}>Retry request</button>}
        {!sending && <button className="action" aria-label="Dismiss request message" onClick={() => { setFeedback(''); setRetry(null); }}>Dismiss</button>}
      </div>}
      {snapshot ? <>
        {snapshot.storage.free < 10 * 1024 ** 3 && <div className="notice" role="status"><CircleAlert size={17}/>
          <span>Windows storage is low. {bytes(snapshot.storage.free)} is available.</span></div>}
        {view === 'analytics' ? performance
          ? <Analytics key={analyticsChannel} data={performance} channel={analyticsChannel} choose={setAnalyticsChannel}
            time={timestamp} pacing={snapshot.pacing_experiment} />
          : <div className="panel empty" role="status">Waiting for the first analytics snapshot.</div>
          : view === 'planning' ? snapshot.phase3
            ? <Planning data={snapshot.phase3} view={planningView} choose={openPlanning} time={timestamp}
              openPacing={() => openAnalytics('shotsnip')} send={snapshot.controls_enabled ? send : undefined}
              busy={sending || stale || issue} cadenceEvent={snapshot.commands.find(event => event.action === 'scheduler_cadence')}
              openJob={openJob} jobFocus={jobFocus} commands={snapshot.commands} />
            : <div className="panel empty" role="status">Waiting for the first planning snapshot.</div>
          : view === 'workspaces' ? <Workspaces data={snapshot.shotsnip_workspace}
            status={snapshot.health.scheduler_hold ? 'On hold' : snapshot.health.scheduler_status}
            waiting={snapshot.health.queue_available ? snapshot.health.waiting : null} time={timestamp}
            openQueue={() => chooseView('controls')} openPerformance={() => openAnalytics('shotsnip')} openPlanning={openPlanning}
            send={snapshot.controls_enabled ? send : undefined} busy={sending || stale || issue} commands={snapshot.commands} />
          : view === 'controls' && snapshot.controls_enabled ? <Controls jobs={snapshot.health.manual_jobs} approvals={snapshot.approvals}
          commands={snapshot.commands} scheduler={snapshot.health.scheduler_enabled} hold={snapshot.health.scheduler_hold}
          busy={sending || stale || issue} backupAt={snapshot.backup_at} time={timestamp} send={send}
          usageJobs={new Set((snapshot.phase3?.job_usage?.jobs ?? []).filter(job => job.channel === 'shotsnip').map(job => job.id))}
          openUsage={id => openJob({channel:'shotsnip', id})} /> : <>
        {view === 'overview' && performance && <>
          <ChannelCards data={performance} open={openAnalytics} />
          {(trend || topHistold) && <div className="overview-performance">
            {trend && <ReportingChart series={{ ...trend, chart_label: 'Shotsnip · YouTube views' }} />}
            {topHistold && <VideoRanking series={topHistold} limit={4} />}
          </div>}
          <div className="section-title"><h2>Publishing operations</h2>{snapshot.controls_enabled && <button className="action" onClick={() => chooseView('controls')}>Open controls <ArrowUpRight size={15}/></button>}</div>
        </>}
        <section className="stats" aria-label="Storage and queue summary">
          <div className="stat"><div><span>Verified archives</span><b>{snapshot.storage.archive_count}</b><small>{snapshot.storage.drive_only} Drive only</small></div><div className="stat-icon"><Cloud size={19}/></div></div>
          <div className="stat"><div><span>Free on this PC</span><b>{bytes(snapshot.storage.free)}</b><small>of {bytes(snapshot.storage.total)}</small></div><div className="stat-icon"><HardDrive size={19}/></div></div>
          <div className="stat"><div><span>Shotsnip waiting</span><b>{snapshot.health.queue_available ? snapshot.health.waiting : '—'}</b><small>{snapshot.health.queue_available ? snapshot.health.attention + ' need attention' : 'Queue unavailable'}</small></div><div className="stat-icon"><Clock3 size={19}/></div></div>
        </section>
        <div className="workspace">
          <section className="panel feed" aria-labelledby="feed-heading">
            <div className="panel-head"><div><h2 id="feed-heading">{view === 'overview' ? 'Recent activity' : 'Archived videos'}</h2><p>{count} {view === 'overview' ? 'records' : 'videos'} · times in IST</p></div></div>
            <div className="filters" role="group" aria-label="Filter by channel">
              <button aria-pressed={channel === 'all'} onClick={() => chooseChannel('all')}>All channels</button>
              {snapshot.channels.filter(item => item.id in labels).map(item => <button key={item.id} aria-pressed={channel === item.id} onClick={() => chooseChannel(item.id)}>{item.name}</button>)}
            </div>
            {view === 'overview' ? <ActivityList items={rows.slice(0, pageSize)} /> : <ArchiveList items={files.slice(0, pageSize)}
              restore={snapshot.controls_enabled ? send : undefined} busy={sending || stale || issue} />}
            {count > pageSize ? <div className="panel-foot"><button onClick={() => setPageSize(value => value + 12)}>Show more</button></div> : null}
          </section>
          <aside className="secondary" aria-label="Studio health">
            <section className="panel side-panel"><h2><Radio size={17}/>Operations</h2>
              <div className="operation"><Archive size={17}/><div><strong>Archive run</strong><small>{archive?.at ? timestamp(archive.at) + ' · ' + archive.copied + ' copied' : 'Awaiting report'}</small></div><Status>{archive?.errors ? 'Needs attention' : archive?.at ? 'Complete' : 'Waiting'}</Status></div>
              <div className="operation"><ArrowDownToLine size={17}/><div><strong>Finished cleanup</strong><small>{cleanup?.at ? timestamp(cleanup.at) + ' · ' + bytes(cleanup.bytes_removed) : 'Awaiting report'}</small></div>
                {cleanup?.at ? <Status>{cleanup.status}</Status> : null}</div>
              <div className="operation"><Activity size={17}/><div><strong>Shotsnip scheduler</strong><small>{timestamp(snapshot.health.scheduler_at)}</small></div><Status>{snapshot.health.scheduler_status}</Status></div>
              <div className="operation"><Database size={17}/><div><strong>Raw segments</strong><small>{snapshot.operations.raw.removed === null ? 'Awaiting scheduler report' : snapshot.operations.raw.removed + ' removed · ' + bytes(snapshot.operations.raw.bytes_removed)}</small></div></div>
            </section>
            <section className="panel side-panel"><h2><Check size={17}/>Channels</h2>
              {snapshot.channels.filter(item => item.id in labels).map(item => <button className="channel-line" key={item.id} onClick={() => chooseChannel(item.id)}><ChannelMark id={item.id}/><span><strong>{item.name}</strong><small>{item.last_published ? 'Published ' + timestamp(item.last_published) : 'No recorded publication'}</small></span><span className="archive-number">{item.archived}</span></button>)}
            </section>
          </aside>
        </div>
        </>}
        <footer>Snapshot {timestamp(snapshot.generated_at)} IST</footer>
      </> : <div className="loading" role="status">{issue ? 'Use refresh to try again.' : 'Loading channel activity…'}</div>}
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App />);
