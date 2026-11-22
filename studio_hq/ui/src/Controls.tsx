import { useState } from 'react';
import { DraftModal } from './Media';
import { ArrowUpRight, Archive, Check, CircleAlert, Clapperboard, Pause, Play, RotateCw, WandSparkles } from './icons';

export type ManualJob = {
  id: string; title: string; revision: number; status: string;
  destinations: string[]; privacy: string; preview: boolean; at: string | null;
  eta?: string | null; position?: number; not_before?: string | null;
};
export type ManualSchedule = { gap_minutes: number; custom: boolean; choices: number[]; last_upload: string | null; next_at: string | null };

const hours = (minutes: number) => minutes % 60 ? (minutes / 60).toFixed(1).replace('.0', '') + ' h' : minutes / 60 + ' h';
const localInput = (iso: string | null | undefined) => {
  const value = iso ? new Date(iso) : new Date(Date.now() + 3600_000);
  const offset = value.getTimezoneOffset() * 60_000;
  return new Date(value.getTime() - offset).toISOString().slice(0, 16);
};

/** How far apart manual and library-batch clips go out, shared with the automatic posting window. */
export function PublishSchedule({ schedule, waiting, busy, send, time }: { schedule: ManualSchedule; waiting: number; busy: boolean; send?: Send; time: Time }) {
  return <section className="panel control-form">
    <h2><Clapperboard size={17}/>Publishing schedule</h2>
    <p className="control-note">{waiting ? `${waiting} ${waiting === 1 ? 'clip is' : 'clips are'} waiting to publish. ` : 'No clips are waiting to publish. '}
      {schedule.next_at ? 'Next about ' + time(schedule.next_at) + ' IST.' : ''}{schedule.last_upload ? ' Last upload ' + time(schedule.last_upload) + ' IST.' : ''}</p>
    <p className="control-note">Time between manual clips{schedule.custom ? '' : ' (current default)'}:</p>
    <div className="gap-choices" role="radiogroup" aria-label="Time between manual clips">
      {schedule.choices.map(minutes => <button key={minutes} role="radio" aria-checked={schedule.gap_minutes === minutes}
        disabled={!send || busy || (schedule.custom && schedule.gap_minutes === minutes)}
        onClick={() => send?.({ action: 'manual_spacing', channel: 'shotsnip', minutes })}>{hours(minutes)}</button>)}
    </div>
    <p className="control-note">The gap counts from the last upload of any kind, so automatic posts also push manual clips back.</p>
  </section>;
}
export type Approval = { title: string; stage: string; run_id: string; url: string; at: string; preview?: import('./Media').DraftPreview | null };
export type CommandEvent = { id?: string; action: string; status: string; message: string; at: string };
export type Command = Record<string, string | number | boolean | string[]>;
export type Send = (command: Command) => void;
type Time = (value: string | null) => string;
export type Pending = { title: string; detail: string; command: Command; story?: boolean };

export const statuses: Record<string, string> = {
  queued: 'Queued', queued_publish: 'Queued to publish', running: 'Processing', ready: 'Ready for review',
  publishing: 'Publishing', partial: 'Needs reconciliation', attention: 'Needs reconciliation', failed: 'Processing failed',
};
const models: Record<string, string> = {
  archive_now:'Archive now', restore:'Restore archive', scheduler:'Shotsnip schedule', scheduler_cadence:'Shotsnip cadence',
  strategist:'Strategist', story_draft:'Histold draft', story_approve:'Histold approval', target_add:'Add target',
  target_toggle:'Target change', agent_review:'Agent review', agent_learning:'Agent learning',
  assistant_ask:'Assistant question', assistant_apply:'Assistant proposal', create_preview:'Shotsnip preview',
  publish:'Shotsnip release', cancel:'Cancel clip', retry:'Retry clip',
};

function destinationText(values: string[]) {
  return values.map(value => value === 'youtube' ? 'YouTube' : 'Facebook').join(' + ');
}

/** One review step before any change; the page never sends a command on the first tap. */
export function ConfirmPanel({ pending, busy, send, close }: { pending: Pending; busy: boolean; send: Send; close: () => void }) {
  const [privacy, setPrivacy] = useState('private');
  return <section className="confirm-panel" aria-label="Review request" role="dialog">
    <div><strong>{pending.title}</strong><p>{pending.detail}</p></div>
    {pending.story && <label>YouTube visibility <select value={privacy} onChange={event => setPrivacy(event.target.value)}>
      <option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select></label>}
    <div className="control-actions"><button className="action" onClick={close}>Back</button>
      <button className="action primary" disabled={busy} onClick={() => {
        send(pending.story ? {...pending.command, privacy} : pending.command); close();
      }}>Confirm</button></div>
  </section>;
}

export function ShotsnipQueue({ jobs, busy, time, send, usageJobs, openUsage }: {
  jobs: ManualJob[]; busy: boolean; time: Time; send?: Send; usageJobs?: Set<string>; openUsage?: (id: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [count, setCount] = useState(12);
  const [pending, setPending] = useState<Pending | null>(null);
  const [timing, setTiming] = useState<{ id: string; value: string } | null>(null);
  const ordered = [...jobs].sort((a, b) => (a.position ?? 999) - (b.position ?? 999));
  const queueAction = (job: ManualJob, action: string, extra: Record<string, number | null> = {}) =>
    send?.({ action, channel: 'shotsnip', job_id: job.id, revision: job.revision, ...extra } as Command);

  function jobAction(job: ManualJob, action: string) {
    const command = { channel:'shotsnip', action, job_id:job.id, revision:job.revision, platforms:job.destinations };
    const detail = action === 'publish' ? 'Publish this reviewed clip to ' + destinationText(job.destinations)
      + (job.destinations.includes('youtube') ? ' with ' + job.privacy + ' YouTube visibility.' : '.')
      : action === 'cancel' ? 'Queued work is cancelled. A running job stops at its next safe stage.'
      : 'Run the existing processing worker again for this failed job.';
    setPending({title:job.title, detail, command});
  }

  return <section className="panel">
    <div className="panel-head"><div><h2>Manual clips</h2><p>Preview, approve, or stop clips in the existing queue.</p></div></div>
    {jobs.length === 0 ? <p className="empty">No manual clips are waiting.</p> : ordered.slice(0, count).map(job => (
      <article key={job.id} className="control-row">
        {job.position && <span className="queue-position" aria-label={'Position ' + job.position}>{job.position}</span>}
        <div className="control-row-title"><strong>{job.title}</strong>
          <small>{statuses[job.status] || job.status}{job.eta ? ' · about ' + time(job.eta) + ' IST' : ' · ' + time(job.at)}</small>
          {job.not_before && <small className="queue-pinned">Not before {time(job.not_before)} IST</small>}</div>
        <div className="control-actions">
          {job.preview && <button className="action" onClick={() => setOpen(open === job.id ? null : job.id)}>{open === job.id ? 'Close preview' : 'Preview'}</button>}
          {send && job.status === 'ready' && job.preview && job.destinations.length > 0 &&
            <button className="action primary" disabled={busy || open !== job.id} title={open !== job.id ? 'Watch the preview first' : undefined}
              onClick={() => jobAction(job, 'publish')}>Approve release</button>}
          {send && ['queued', 'queued_publish', 'running'].includes(job.status) && <button className="action" disabled={busy} onClick={() => jobAction(job,'cancel')}>Cancel</button>}
          {send && job.status === 'failed' && <button className="action" disabled={busy} onClick={() => jobAction(job,'retry')}>Retry processing</button>}
          {openUsage && usageJobs?.has(job.id) && <button className="action" onClick={() => openUsage(job.id)}>Job usage</button>}
          {send && ['queued', 'queued_publish'].includes(job.status) && <span className="queue-tools">
            <button className="action" aria-label="Move earlier" title="Move earlier" disabled={busy || job.position === 1} onClick={() => queueAction(job, 'move_earlier')}>↑</button>
            <button className="action" aria-label="Move later" title="Move later" disabled={busy} onClick={() => queueAction(job, 'move_later')}>↓</button>
            <button className="action" disabled={busy || job.position === 1} onClick={() => queueAction(job, 'prioritize')}>First</button>
          </span>}
          {send && ['ready', 'queued', 'queued_publish'].includes(job.status) && <button className="action" disabled={busy}
            onClick={() => setTiming(timing?.id === job.id ? null : { id: job.id, value: localInput(job.not_before ?? job.eta) })}>{job.not_before ? 'Change time' : 'Set time'}</button>}
        </div>
        {timing?.id === job.id && send && <div className="queue-timing inline-confirm">
          <label>Publish no earlier than<input type="datetime-local" value={timing.value} onChange={event => setTiming({ id: job.id, value: event.target.value })} /></label>
          <button className="action primary" disabled={busy || !timing.value} onClick={() => {
            queueAction(job, 'schedule', { not_before: Math.round(new Date(timing.value).getTime() / 1000) }); setTiming(null);
          }}>Save time</button>
          {job.not_before && <button className="action" disabled={busy} onClick={() => { queueAction(job, 'schedule', { not_before: null }); setTiming(null); }}>Clear time</button>}
          <button className="action" onClick={() => setTiming(null)}>Cancel</button>
          <small>The clip waits until this time, then goes out in queue order with the usual gap.</small>
        </div>}
        {open === job.id && job.preview && <video key={job.revision} className="clip-preview" controls playsInline preload="metadata"
          src={new URL('api/preview/' + encodeURIComponent(job.id) + '?revision=' + encodeURIComponent(job.revision), window.location.href).href}
          aria-label={'Preview of ' + job.title}/>}
      </article>
    ))}
    {jobs.length > count && <div className="panel-foot"><button onClick={() => setCount(value => value + 12)}>Show more clips</button></div>}
    {pending && send && <ConfirmPanel pending={pending} busy={busy} send={send} close={() => setPending(null)} />}
  </section>;
}

export function NewClipForm({ busy, send }: { busy: boolean; send: Send }) {
  const [mode, setMode] = useState('replay');
  const [url, setUrl] = useState('');
  const [ranges, setRanges] = useState('');
  const [platforms, setPlatforms] = useState(['youtube', 'facebook']);
  return <form className="panel control-form" onSubmit={event => {
    event.preventDefault(); send({action:'create_preview', channel:'shotsnip', mode, url, ranges, platforms});
  }}>
    <h2><Clapperboard size={17}/>New clip preview</h2>
    <label>Source URL<input type="url" required maxLength={550} value={url} onChange={event => setUrl(event.target.value)} placeholder="https://www.youtube.com/watch?v=…" /></label>
    <label>Clip selection<select value={mode} onChange={event => setMode(event.target.value)}><option value="replay">Select from replay</option><option value="exact">Use timestamps</option></select></label>
    {mode === 'exact' && <label>Time ranges<textarea value={ranges} required rows={2} maxLength={240} onChange={event => setRanges(event.target.value)} placeholder="01:20 - 01:50" /></label>}
    <fieldset><legend>Publish to, after approval</legend>
      {['youtube','facebook'].map(p => <label key={p}><input type="checkbox" checked={platforms.includes(p)}
        onChange={event => setPlatforms(value => event.target.checked ? [...value,p] : value.filter(item => item !== p))}/>{destinationText([p])}</label>)}
    </fieldset>
    <button className="action primary" type="submit" disabled={busy || platforms.length === 0}>Queue preview</button>
  </form>;
}

export type LiveState = {
  phase: string; enabled?: boolean | null; run_now?: boolean; stop_after?: boolean; pending_publications: number;
  hold?: { batch: string; total: number; published: number | null; paused_at: string | null } | null;
};

/** Pause, resume, run-now and stop-after-stage for Shotsnip's automatic live operations. */
export function LiveControls({ live, busy, send, recent, time }: { live: LiveState; busy: boolean; send?: Send;
  recent?: CommandEvent; time: Time }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const enabled = live.enabled === true;
  const hold = live.hold;
  const running = ['capturing', 'analyzing', 'finishing'].includes(live.phase);
  const blocked = live.pending_publications > 0;
  const state = enabled ? 'Running' : hold ? 'On hold' : 'Paused';
  const progress = hold ? (hold.published ?? '?') + ' of ' + hold.total : '';
  return <section className="panel live-controls" aria-labelledby="live-controls-heading">
    <div className="panel-head"><div><h2 id="live-controls-heading">Live controls</h2>
      <p>Start, stop, or nudge automatic live clipping.</p></div>
      <span className={'status ' + (enabled ? 'good' : 'warn')}>{state}</span></div>
    <div className="live-state">
      {enabled ? <p>Shotsnip scans your targets and posts on its cadence. Manual and batch clips always go first.</p>
        : hold ? <p><strong>Library batch in progress: {progress} clips published.</strong> Live clipping resumes by itself once all {hold.total} are on YouTube and Facebook{hold.paused_at ? ' (on hold since ' + time(hold.paused_at) + ' IST)' : ''}.</p>
        : <p>Automatic live clipping is paused. Manual clips still run from the queue.</p>}
      {blocked && <p className="control-note"><CircleAlert size={14}/>A publication result is unresolved, so live operations can’t resume until it is checked.</p>}
      {live.stop_after && <p className="control-note"><CircleAlert size={14}/>The current live job will stop after this stage.</p>}
      {live.run_now && <p className="control-note"><Check size={14}/>A live cycle is requested and starts when the worker is free.</p>}
    </div>
    {send && <div className="live-buttons">
      {enabled ? <>
        <button className="action" disabled={busy} onClick={() => setPending({ title: 'Pause live operations',
          detail: 'Automatic scanning and posting stop. Manual and batch clips keep their own queue.',
          command: { action: 'scheduler', channel: 'shotsnip', enabled: false, expected: true } })}><Pause size={16}/>Pause live operations</button>
        {live.run_now
          ? <button className="action" disabled={busy} onClick={() => send({ action: 'live_run_now', channel: 'shotsnip', cancel: true })}>Cancel run request</button>
          : <button className="action" disabled={busy || blocked} onClick={() => send({ action: 'live_run_now', channel: 'shotsnip', cancel: false })}><RotateCw size={16}/>Run a cycle now</button>}
        {running && !live.stop_after && <button className="action" disabled={busy} onClick={() => setPending({ title: 'Stop after this stage',
          detail: 'The running live job finishes its current stage, then stops. Nothing is deleted.',
          command: { action: 'live_stop_after_stage', channel: 'shotsnip' } })}>Stop after this stage</button>}
      </> : <button className="action primary" disabled={busy || blocked} onClick={() => setPending({
          title: hold ? 'Resume live operations now?' : 'Resume live operations',
          detail: hold ? `This ends the library batch hold early (${progress} published). The remaining batch clips still publish on their own schedule, and live clipping resumes alongside them.`
            : 'Shotsnip resumes scanning and posting on its cadence.',
          command: { action: 'scheduler', channel: 'shotsnip', enabled: true, expected: false, end_hold: !!hold } })}><Play size={16}/>Resume live operations</button>}
    </div>}
    {recent && <p className="analytics-info" role="status">{recent.message} · {time(recent.at)} IST</p>}
    {pending && send && <ConfirmPanel pending={pending} busy={busy} send={send} close={() => setPending(null)} />}
  </section>;
}

export function SchedulerSwitch({ scheduler, hold, busy, send }: { scheduler: boolean | null; hold: boolean; busy: boolean; send?: Send }) {
  const [pending, setPending] = useState<Pending | null>(null);
  return <section className="panel control-form">
    <h2>{scheduler ? <Play size={17}/> : <Pause size={17}/>}Automatic posting</h2>
    <p className="control-note">{scheduler == null ? 'Scheduler status is unavailable.' : scheduler ? 'Shotsnip posts automatically on its schedule.' : 'Automatic posting is paused.'}</p>
    {hold && <p className="control-note"><CircleAlert size={14}/>Scheduling is on hold.</p>}
    {send && <button className="action" disabled={busy || typeof scheduler !== 'boolean' || (hold && !scheduler)} onClick={() => {
      if (typeof scheduler !== 'boolean') return;
      setPending({title:'Shotsnip automatic posting', detail:scheduler ? 'Pause automatic posting. Manual clips stay in their queue.' : 'Resume by the existing schedule.',
                  command:{action:'scheduler',channel:'shotsnip',enabled:!scheduler,expected:scheduler}});
    }}>{scheduler ? <Pause size={15}/> : <Play size={15}/>} {scheduler ? 'Pause automatic posting' : 'Resume automatic posting'}</button>}
    {pending && send && <ConfirmPanel pending={pending} busy={busy} send={send} close={() => setPending(null)} />}
  </section>;
}

export function HistoldApprovals({ approvals, busy, time, send, recent }: { approvals: Approval[]; busy: boolean; time: Time; send?: Send; recent?: CommandEvent }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [viewing, setViewing] = useState<Approval | null>(null);
  return <section className="panel">
    <div className="panel-head"><div><h2>Drafts waiting for approval</h2><p>Prepare a preview to watch the draft here with all its details, then approve it.</p></div></div>
    {recent && <p className="analytics-info" role="status">{recent.message} · {time(recent.at)} IST</p>}
    {approvals.length === 0 ? <p className="empty">No drafts are waiting.</p> : approvals.map(draft => (
      <article className="control-row" key={draft.stage}>
        <div className="control-row-title"><strong>{draft.title}</strong><small>{time(draft.at)}</small></div>
        <div className="control-actions">
          {draft.preview ? <button className="action primary" onClick={() => setViewing(draft)}><Play size={15}/>View draft</button>
            : send && <button className="action" disabled={busy} onClick={() => send({action:'draft_preview', channel:'histold', stage:draft.stage, run_id:draft.run_id})}>Prepare preview</button>}
          <a href={draft.url} target="_blank" rel="noreferrer" className="action">Workflow <ArrowUpRight size={14}/></a>
          {send && !draft.preview && <button className="action" disabled={busy} onClick={() => setPending({ title:draft.title, story:true,
            detail:'Publish this Histold draft through its existing workflow. YouTube visibility does not set Facebook or Instagram visibility.',
            command:{action:'story_approve', channel:'histold', stage:draft.stage, run_id:draft.run_id} })}>Review approval</button>}
        </div>
      </article>
    ))}
    {pending && send && <ConfirmPanel pending={pending} busy={busy} send={send} close={() => setPending(null)} />}
    {viewing?.preview && <DraftModal title={viewing.title} stage={viewing.stage} runId={viewing.run_id} url={viewing.url} at={viewing.at}
      preview={viewing.preview} busy={busy} send={send} time={time} onClose={() => setViewing(null)} />}
  </section>;
}

export function HistoldActions({ busy, send }: { busy: boolean; send: Send }) {
  const [format, setFormat] = useState('short');
  return <form className="panel control-form" onSubmit={event => { event.preventDefault(); send({action:'story_draft', channel:'histold', format}); }}>
    <h2><WandSparkles size={17}/>New draft</h2>
    <label>Format<select value={format} onChange={event => setFormat(event.target.value)}><option value="short">Short</option><option value="long">Long-form</option></select></label>
    <button className="action primary" type="submit" disabled={busy}>Render draft</button>
    <p className="control-note">Renders a preview without publishing.</p>
    <button className="action" type="button" disabled={busy} onClick={() => send({action:'strategist'})}><RotateCw size={15}/>Run the Strategist now</button>
  </form>;
}

export function HostActions({ busy, send, backupAt, time }: { busy: boolean; send: Send; backupAt: string | null; time: Time }) {
  return <section className="panel control-form">
    <h2><Archive size={17}/>Archive</h2>
    <p className="control-note">Copies one finished clip older than 48 hours to Drive and verifies it.</p>
    <button className="action" disabled={busy} onClick={() => send({action:'archive_now'})}><Archive size={15}/>Archive one eligible clip</button>
    {backupAt && <small className="backup-status"><Check size={15}/>Queue backup {time(backupAt)} IST</small>}
  </section>;
}

export function CommandHistory({ commands, time }: { commands: CommandEvent[]; time: Time }) {
  return <section className="panel">
    <div className="panel-head"><div><h2>Your recent requests</h2><p>Requests keep running after you close the page.</p></div></div>
    {commands.length === 0 ? <p className="empty">No requests yet.</p> : commands.map((event, index) => (
      <div className="command-row" key={event.at + index}><strong>{models[event.action] || event.action}</strong>
        <small>{event.message} · {time(event.at)}</small></div>
    ))}
  </section>;
}
