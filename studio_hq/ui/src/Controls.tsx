import { useState } from 'react';
import { ArrowUpRight, Archive, Check, CircleAlert, Clapperboard, Pause, Play, RotateCw, WandSparkles } from './icons';

export type ManualJob = {
  id: string; title: string; revision: number; status: string;
  destinations: string[]; privacy: string; preview: boolean; at: string | null;
};
export type Approval = { title: string; stage: string; run_id: string; url: string; at: string };
export type CommandEvent = { action: string; status: string; message: string; at: string };
export type Command = Record<string, string | number | boolean | string[]>;
export type Send = (command: Command) => void;
type Time = (value: string | null) => string;
type Pending = { title: string; detail: string; command: Command; story?: boolean };

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
function ConfirmPanel({ pending, busy, send, close }: { pending: Pending; busy: boolean; send: Send; close: () => void }) {
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
  const [count, setCount] = useState(8);
  const [pending, setPending] = useState<Pending | null>(null);

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
    {jobs.length === 0 ? <p className="empty">No manual clips are waiting.</p> : jobs.slice(0, count).map(job => (
      <article key={job.id} className="control-row">
        <div className="control-row-title"><strong>{job.title}</strong><small>{statuses[job.status] || job.status} · {time(job.at)}</small></div>
        <div className="control-actions">
          {job.preview && <button className="action" onClick={() => setOpen(open === job.id ? null : job.id)}>{open === job.id ? 'Close preview' : 'Preview'}</button>}
          {send && job.status === 'ready' && job.preview && job.destinations.length > 0 &&
            <button className="action primary" disabled={busy || open !== job.id} title={open !== job.id ? 'Watch the preview first' : undefined}
              onClick={() => jobAction(job, 'publish')}>Approve release</button>}
          {send && ['queued', 'queued_publish', 'running'].includes(job.status) && <button className="action" disabled={busy} onClick={() => jobAction(job,'cancel')}>Cancel</button>}
          {send && job.status === 'failed' && <button className="action" disabled={busy} onClick={() => jobAction(job,'retry')}>Retry processing</button>}
          {openUsage && usageJobs?.has(job.id) && <button className="action" onClick={() => openUsage(job.id)}>Job usage</button>}
        </div>
        {open === job.id && job.preview && <video key={job.revision} className="clip-preview" controls playsInline preload="metadata"
          src={new URL('api/preview/' + encodeURIComponent(job.id) + '?revision=' + encodeURIComponent(job.revision), window.location.href).href}
          aria-label={'Preview of ' + job.title}/>}
      </article>
    ))}
    {jobs.length > count && <div className="panel-foot"><button onClick={() => setCount(value => value + 8)}>Show more clips</button></div>}
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

export function HistoldApprovals({ approvals, busy, time, send }: { approvals: Approval[]; busy: boolean; time: Time; send?: Send }) {
  const [pending, setPending] = useState<Pending | null>(null);
  return <section className="panel">
    <div className="panel-head"><div><h2>Drafts waiting for approval</h2><p>Open the workflow, review the draft, then approve it.</p></div></div>
    {approvals.length === 0 ? <p className="empty">No drafts are waiting.</p> : approvals.map(draft => (
      <article className="control-row" key={draft.stage}>
        <div className="control-row-title"><strong>{draft.title}</strong><small>{time(draft.at)}</small></div>
        <div className="control-actions">
          <a href={draft.url} target="_blank" rel="noreferrer" className="action">Open workflow <ArrowUpRight size={14}/></a>
          {send && <button className="action primary" disabled={busy} onClick={() => setPending({ title:draft.title, story:true,
            detail:'Publish this Histold draft through its existing workflow. YouTube visibility does not set Facebook or Instagram visibility.',
            command:{action:'story_approve', channel:'histold', stage:draft.stage, run_id:draft.run_id} })}>Review approval</button>}
        </div>
      </article>
    ))}
    {pending && send && <ConfirmPanel pending={pending} busy={busy} send={send} close={() => setPending(null)} />}
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
