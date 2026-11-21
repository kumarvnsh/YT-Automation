import type { ReactNode } from 'react';
import { Archive, ArrowDownToLine, ArrowUpRight, Activity, Cloud, Database, HardDrive } from './icons';
import { channelNames, type Pacing, type StudioAnalytics, type StudioChannel } from './Analytics';
import { CommandHistory, HistoldActions, HistoldApprovals, HostActions, LiveControls, NewClipForm, PublishSchedule, ShotsnipQueue,
  type Approval, type CommandEvent, type ManualJob, type Send } from './Controls';
import { AssistantPanel, Calendar, ExperimentPanel, JobUsage, Money, Recommendations, ShotsnipCadence, SixReelExperiment,
  TopicList, TopicPage, type JobLink, type PlanningData } from './Planning';
import { ChannelInsights } from './Insights';
import { BotsPage } from './Bots';
import { ClipsPage } from './Media';
import { AiSettingsPanel, AskShotsnip, CommentaryControl, HistoldStrategyControls, WatchWorker } from './Owner';
import { AgentPanel, LiveOperations, OriginalTools, Targets, type ShotsnipWorkspace } from './Workspaces';

export type Time = (value: string | null) => string;
export type Item = { channel: StudioChannel; kind: string; title: string; status: string; at: string | null; url: string | null };
export type ArchiveRow = { id: string; channel: StudioChannel; title: string; archived_at: string | null; state: string; size: number | null; url: string | null };
export type Snapshot = {
  generated_at: string;
  channels: { id: StudioChannel; name: string; archived: number; last_published: string | null; activity_count: number }[];
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
    scheduler_enabled: boolean | null; scheduler_hold: boolean; manual_jobs: ManualJob[];
    manual_schedule?: import('./Controls').ManualSchedule | null };
  storage: { free: number; total: number; drive_only: number; archive_count: number };
  operations: {
    archive: { at: string | null; copied: number; errors: number };
    cleanup: { at: string | null; status: string; removed: number; bytes_removed: number; channel: StudioChannel | null };
    raw: { removed: number | null; bytes_removed: number | null };
  };
};
export type PageProps = { snapshot: Snapshot; time: Time; send?: Send; busy: boolean; sub: string; rest: string[] };

export function bytes(value: number | null) {
  if (value === null || !Number.isFinite(value)) return '—';
  if (value === 0) return '0 B';
  const scale = Math.min(3, Math.max(0, Math.floor(Math.log(value) / Math.log(1024))));
  return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 1 }).format(value / 1024 ** scale)
    + ' ' + ['B', 'KiB', 'MiB', 'GiB'][scale];
}

function Status({ children }: { children: string }) {
  const tone = /failed|attention|error|hold/i.test(children) ? 'warn'
    : /published|verified|drive|complete|active/i.test(children) ? 'good' : 'neutral';
  return <span className={'status ' + tone}><span aria-hidden="true" />{children}</span>;
}

function Waiting({ what }: { what: string }) {
  return <div className="panel empty" role="status">Waiting for the first {what} snapshot.</div>;
}

const latest = (commands: CommandEvent[], ...actions: string[]) => commands.find(event => actions.includes(event.action));
const shotsnipStatus = (snapshot: Snapshot) => snapshot.health.scheduler_hold ? 'On hold' : snapshot.health.scheduler_status;

/** Things that need the owner, each linking straight to the page where it is handled. */
export function attention(snapshot: Snapshot) {
  const items: { tone: 'warn' | 'info'; text: string; href: string; section: string }[] = [];
  const ready = snapshot.health.manual_jobs.filter(job => job.status === 'ready').length;
  const failed = snapshot.health.manual_jobs.filter(job => ['failed', 'partial', 'attention'].includes(job.status)).length;
  if (snapshot.approvals.length) items.push({ tone: 'info', section: 'histold', href: '#/histold/approvals',
    text: `${snapshot.approvals.length} Histold ${snapshot.approvals.length === 1 ? 'draft is' : 'drafts are'} waiting for approval` });
  if (ready) items.push({ tone: 'info', section: 'shotsnip', href: '#/shotsnip/queue',
    text: `${ready} Shotsnip ${ready === 1 ? 'clip is' : 'clips are'} ready for review` });
  if (failed) items.push({ tone: 'warn', section: 'shotsnip', href: '#/shotsnip/queue',
    text: `${failed} Shotsnip ${failed === 1 ? 'clip needs' : 'clips need'} attention` });
  if (snapshot.health.scheduler_hold) items.push({ tone: 'warn', section: 'shotsnip', href: '#/shotsnip/live',
    text: 'Shotsnip automatic posting is on hold' });
  if (snapshot.storage.free < 10 * 1024 ** 3) items.push({ tone: 'warn', section: 'system', href: '#/system/health',
    text: `Storage is low: ${bytes(snapshot.storage.free)} free on this PC` });
  if (snapshot.operations.archive.errors) items.push({ tone: 'warn', section: 'system', href: '#/system/health',
    text: 'The last archive run needs attention' });
  const gaps = snapshot.phase3?.experiments?.recommendations.filter(item => item.kind === 'gap') ?? [];
  if (gaps.length) items.push({ tone: 'info', section: 'histold', href: '#/histold/experiments',
    text: `${gaps.length} experiment evidence ${gaps.length === 1 ? 'gap' : 'gaps'} to review` });
  return items;
}

function Feed({ items, time, limit }: { items: Item[]; time: Time; limit: number }) {
  if (!items.length) return <p className="empty">No activity yet.</p>;
  return <ul className="feed-list">{items.slice(0, limit).map((row, index) => <li key={row.channel + row.at + index}>
    <span className={'channel-mark ' + row.channel} aria-hidden="true">{row.channel === 'histold' ? 'H' : 'S'}</span>
    <span className="feed-main"><strong>{row.title}</strong><small>{channelNames[row.channel]} · {row.kind} · {time(row.at)}</small></span>
    <Status>{row.status}</Status>
    {row.url && <a className="icon-link" href={row.url} target="_blank" rel="noreferrer" aria-label={'Open ' + row.title}><ArrowUpRight size={16}/></a>}
  </li>)}</ul>;
}

export function HomePage({ snapshot, time, sub }: PageProps) {
  const channel: StudioChannel = sub === 'shotsnip' ? 'shotsnip' : 'histold';
  if (!snapshot.analytics) return <Waiting what="analytics" />;
  return <div className={'scope-' + channel}>
    <ChannelInsights key={channel} data={snapshot.analytics} channel={channel} time={time}
      pacing={channel === 'shotsnip' ? snapshot.pacing_experiment : null} />
  </div>;
}

export function ShotsnipPage({ snapshot, time, send, busy, sub }: PageProps) {
  const workspace = snapshot.shotsnip_workspace;
  const phase3 = snapshot.phase3;
  if (sub === 'queue') return <div className="page-split">
    <ShotsnipQueue jobs={snapshot.health.manual_jobs} busy={busy} time={time} send={send}
      usageJobs={new Set((phase3?.job_usage?.jobs ?? []).filter(job => job.channel === 'shotsnip').map(job => job.id))}
      openUsage={id => { window.location.hash = '#/plan/jobs/shotsnip/' + id; }} />
    <div className="page-stack">
      {snapshot.health.manual_schedule && <PublishSchedule schedule={snapshot.health.manual_schedule} busy={busy} send={send} time={time}
        waiting={snapshot.health.manual_jobs.filter(job => job.status === 'queued_publish').length} />}
      {send && <NewClipForm busy={busy} send={send} />}
    </div>
  </div>;
  if (sub === 'clips') return phase3?.clips ? <ClipsPage data={phase3.clips} time={time} /> : <Waiting what="clip library" />;
  if (sub === 'bots') return phase3?.bots
    ? <BotsPage data={phase3.bots} channel="shotsnip" busy={busy} send={send} time={time} recent={latest(snapshot.commands, 'bot_action')} />
    : <Waiting what="bot workspace" />;
  if (sub === 'targets') return workspace?.target_list
    ? <Targets list={workspace.target_list} revision={workspace.targets_revision ?? null} send={send} busy={busy} time={time}
        recent={latest(snapshot.commands, 'target_add', 'target_toggle')} />
    : <Waiting what="targets" />;
  if (sub === 'agent') return <div className="page-stack">
    {workspace?.agent ? <AgentPanel data={workspace.agent} send={send} busy={busy} time={time}
      recent={latest(snapshot.commands, 'agent_review', 'agent_learning')} /> : <Waiting what="agent" />}
    <AskShotsnip records={(phase3?.assistant ?? []).filter(row => row.scope === 'shotsnip')} busy={busy} send={send} time={time}
      recent={latest(snapshot.commands, 'assistant_ask')} />
  </div>;
  return <div className="page-stack">
    {workspace?.live && <LiveControls live={workspace.live} busy={busy} send={send} time={time}
      recent={latest(snapshot.commands, 'scheduler', 'live_run_now', 'live_stop_after_stage')} />}
    {workspace?.live && <LiveOperations live={workspace.live} status={shotsnipStatus(snapshot)}
      waiting={snapshot.health.queue_available ? snapshot.health.waiting : null} time={time} />}
    {phase3 ? <ShotsnipCadence data={phase3.calendar.shotsnip} time={time} send={send} busy={busy}
      cadenceEvent={latest(snapshot.commands, 'scheduler_cadence')} /> : <Waiting what="schedule" />}
    {workspace?.live && <div className="page-split even">
      <CommentaryControl mode={workspace.live.commentary ?? 'off'} busy={busy} send={send} time={time}
        recent={latest(snapshot.commands, 'shotsnip_commentary')} />
      {workspace.live.watch_worker && <WatchWorker worker={workspace.live.watch_worker} busy={busy} send={send} time={time} />}
    </div>}
  </div>;
}

export function HistoldPage({ snapshot, time, send, busy, sub, rest }: PageProps) {
  const phase3 = snapshot.phase3;
  if (sub === 'approvals') return <div className="page-split">
    <HistoldApprovals approvals={snapshot.approvals} busy={busy} time={time} send={send} recent={latest(snapshot.commands, 'draft_preview', 'story_approve')} />
    {send && <HistoldActions busy={busy} send={send} />}
  </div>;
  if (!phase3) return <Waiting what="strategy" />;
  if (sub === 'experiments') return <div className="page-stack">
    {phase3.experiments && <Recommendations items={phase3.experiments.recommendations} />}
    <div className="page-split">
      {phase3.experiments && <ExperimentPanel data={phase3.experiments} time={time} />}
      <SixReelExperiment data={phase3} />
    </div>
  </div>;
  if (sub === 'bots') return phase3.bots
    ? <BotsPage data={phase3.bots} channel="histold" busy={busy} send={send} time={time} recent={latest(snapshot.commands, 'bot_action')} />
    : <Waiting what="bot workspace" />;
  if (sub === 'controls') return phase3.strategy_controls
    ? <HistoldStrategyControls data={phase3.strategy_controls} busy={busy} send={send} time={time}
        recent={latest(snapshot.commands, 'histold_strategy', 'histold_research')} />
    : <Waiting what="strategy controls" />;
  if (sub === 'assistant') return phase3.assistant
    ? <AssistantPanel records={phase3.assistant.filter(row => row.scope !== 'shotsnip')} topics={phase3.topics} focus={rest[0] ?? null}
        setFocus={job => { window.location.hash = '#/histold/assistant' + (job ? '/' + job : ''); }}
        send={send} busy={busy} time={time} recent={latest(snapshot.commands, 'assistant_ask', 'assistant_apply')} />
    : <Waiting what="assistant" />;
  if (rest[0]) return <TopicPage data={phase3} job={rest[0]} time={time} send={send} />;
  return <TopicList data={phase3} />;
}

export function PlanPage({ snapshot, time, sub, rest }: PageProps) {
  const phase3 = snapshot.phase3;
  if (!phase3) return <Waiting what="planning" />;
  if (sub === 'money') return <Money data={phase3} time={time} />;
  if (sub === 'jobs') {
    const focus: JobLink | null = (rest[0] === 'histold' || rest[0] === 'shotsnip') && rest[1] ? { channel: rest[0], id: rest[1] } : null;
    return phase3.job_usage
      ? <JobUsage key={focus ? focus.channel + focus.id : 'all'} data={phase3.job_usage} focus={focus} time={time} />
      : <Waiting what="job usage" />;
  }
  return <Calendar data={phase3.calendar} time={time} />;
}

function ArchiveList({ items, time, send, busy }: { items: ArchiveRow[]; time: Time; send?: Send; busy: boolean }) {
  if (!items.length) return <p className="empty">No archived videos yet.</p>;
  return <ul className="feed-list">{items.map(row => <li key={row.id}>
    <span className={'channel-mark ' + row.channel} aria-hidden="true">{row.channel === 'histold' ? 'H' : 'S'}</span>
    <span className="feed-main"><strong>{row.title}</strong><small>{channelNames[row.channel]} · {bytes(row.size)} · {time(row.archived_at)}</small>
      {send && row.state !== 'Verification pending' && row.url &&
        <button className="restore-action" disabled={busy} onClick={() => send({action: 'restore', archive_id: row.id})}>Restore to PC</button>}</span>
    <Status>{row.state}</Status>
    {row.url && <a className="icon-link" href={row.url} target="_blank" rel="noreferrer" aria-label={'Open ' + row.title + ' in Google Drive'}><ArrowUpRight size={16}/></a>}
  </li>)}</ul>;
}

function Stat({ label, value, note, icon }: { label: string; value: string | number; note: string; icon: ReactNode }) {
  return <div className="stat"><div><span>{label}</span><b>{value}</b><small>{note}</small></div><div className="stat-icon">{icon}</div></div>;
}

export function SystemPage({ snapshot, time, send, busy, sub }: PageProps) {
  if (sub === 'activity') return <section className="panel"><div className="panel-head"><div><h2>All activity</h2>
    <p>{snapshot.activity.length} records · IST</p></div></div>
    <Feed items={snapshot.activity.filter(row => row.channel in channelNames)} time={time} limit={80} /></section>;
  if (sub === 'archive') return <section className="panel"><div className="panel-head"><div><h2>Archived videos</h2>
    <p>Verified copies in Google Drive. Restore copies a video back to this PC.</p></div></div>
    <ArchiveList items={snapshot.archives.filter(row => row.channel in channelNames).slice(0, 80)} time={time} send={send} busy={busy} /></section>;
  if (sub === 'tools') return <OriginalTools connections={snapshot.shotsnip_workspace?.connections} />;
  if (sub === 'ai') return snapshot.phase3?.ai_settings
    ? <AiSettingsPanel key={JSON.stringify(snapshot.phase3.ai_settings.providers)} data={snapshot.phase3.ai_settings} busy={busy} send={send} time={time}
        recent={latest(snapshot.commands, 'ai_settings')} />
    : <Waiting what="AI settings" />;
  const { archive, cleanup, raw } = snapshot.operations;
  return <div className="page-stack">
    <section className="stats" aria-label="Storage and archive">
      <Stat label="Free on this PC" value={bytes(snapshot.storage.free)} note={'of ' + bytes(snapshot.storage.total)} icon={<HardDrive size={19}/>} />
      <Stat label="Verified archives" value={snapshot.storage.archive_count} note={snapshot.storage.drive_only + ' in Drive only'} icon={<Cloud size={19}/>} />
    </section>
    <div className="page-split">
      <section className="panel side-panel"><h2>Background jobs</h2>
        <div className="operation"><Archive size={17}/><div><strong>Archive run</strong><small>{archive.at ? time(archive.at) + ' · ' + archive.copied + ' copied' : 'Awaiting report'}</small></div>
          <Status>{archive.errors ? 'Needs attention' : archive.at ? 'Complete' : 'Waiting'}</Status></div>
        <div className="operation"><ArrowDownToLine size={17}/><div><strong>Finished cleanup</strong><small>{cleanup.at ? time(cleanup.at) + ' · ' + bytes(cleanup.bytes_removed) : 'Awaiting report'}</small></div>
          {cleanup.at && <Status>{cleanup.status}</Status>}</div>
        <div className="operation"><Activity size={17}/><div><strong>Shotsnip scheduler</strong><small>{time(snapshot.health.scheduler_at)}</small></div>
          <Status>{shotsnipStatus(snapshot)}</Status></div>
        <div className="operation"><Database size={17}/><div><strong>Raw segments</strong><small>{raw.removed === null ? 'Awaiting scheduler report' : raw.removed + ' removed · ' + bytes(raw.bytes_removed)}</small></div></div>
      </section>
      {send && <HostActions busy={busy} send={send} backupAt={snapshot.backup_at} time={time} />}
    </div>
    <CommandHistory commands={snapshot.commands} time={time} />
  </div>;
}
