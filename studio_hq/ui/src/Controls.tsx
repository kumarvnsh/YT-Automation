import { useState } from 'react';
import { ArrowUpRight, Archive, Check, CircleAlert, Clapperboard, Pause, Play, RotateCw, WandSparkles } from 'lucide-react';

export type ManualJob = {
  id: string; title: string; revision: number; status: string;
  destinations: string[]; privacy: string; preview: boolean; at: string | null;
};
export type Approval = { title: string; stage: string; run_id: string; url: string; at: string };
export type CommandEvent = { action: string; status: string; message: string; at: string };
export type Command = Record<string, string | number | boolean | string[]>;
export type Send = (command: Command) => void;

const statuses: Record<string, string> = {
  queued: 'Queued', queued_publish: 'Queued to publish', running: 'Processing', ready: 'Ready for review',
  publishing: 'Publishing', partial: 'Needs reconciliation', attention: 'Needs reconciliation', failed: 'Processing failed',
};
const models: Record<string, string> = {
  archive_now:'Archive now', restore:'Restore archive', scheduler:'Shotsnip schedule', scheduler_cadence:'Shotsnip cadence',
  strategist:'Strategist', story_draft:'Story draft', story_approve:'Histold approval',
};

function destinationText(values: string[]) {
  return values.map(value => value === 'youtube' ? 'YouTube' : 'Facebook').join(' + ');
}

export function Controls({
  jobs, approvals, commands, scheduler, hold, busy, backupAt, time, send, usageJobs, openUsage,
}: {
  jobs: ManualJob[]; approvals: Approval[]; commands: CommandEvent[]; scheduler: boolean | null;
  hold: boolean; busy: boolean; backupAt: string | null;
  time: (value: string | null) => string; send: Send;
  usageJobs?: Set<string>; openUsage?: (id: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [mode, setMode] = useState('replay');
  const [url, setUrl] = useState('');
  const [ranges, setRanges] = useState('');
  const [platforms, setPlatforms] = useState(['youtube', 'facebook']);
  const [format, setFormat] = useState('short');
  const [confirm, setConfirm] = useState<{ title: string; detail: string; command: Command; story?: boolean } | null>(null);
  const [privacy, setPrivacy] = useState('private');
  const [jobCount, setJobCount] = useState(8);

  function jobAction(job: ManualJob, action: string) {
    const command = { channel:'shotsnip', action, job_id:job.id, revision:job.revision, platforms:job.destinations };
    const detail = action === 'publish' ? 'Publish this reviewed clip to ' + destinationText(job.destinations)
      + (job.destinations.includes('youtube') ? ' with ' + job.privacy + ' YouTube visibility.' : '.')
      : action === 'cancel' ? 'Queued work is cancelled. A running job stops at its next safe stage.'
      : 'Run the existing processing worker again for this failed job.';
    setConfirm({title:job.title, detail, command});
  }

  return <div className="workspace controls-workspace">
    <div className="control-stack">
      <section className="panel">
        <div className="panel-head"><div><h2>Shotsnip manual queue</h2><p>Review, approve, or stop jobs in the existing queue.</p></div></div>
        {jobs.length === 0 ? <p className="empty">No manual jobs awaiting action.</p> : jobs.slice(0, jobCount).map(job => (
          <article key={job.id} className="control-row">
            <div className="control-row-title"><strong>{job.title}</strong><small>{statuses[job.status] || job.status} · {time(job.at)}</small></div>
            <div className="control-actions">
              {job.preview && <button className="action" onClick={() => setOpen(open === job.id ? null : job.id)}>{open === job.id ? 'Close preview' : 'Preview'}</button>}
              {job.status === 'ready' && job.preview && job.destinations.length > 0 &&
                <button className="action primary" disabled={busy || open !== job.id} onClick={() => jobAction(job, 'publish')}>Approve release</button>}
              {['queued', 'queued_publish', 'running'].includes(job.status) && <button className="action" disabled={busy} onClick={() => jobAction(job,'cancel')}>Cancel</button>}
              {job.status === 'failed' && <button className="action" disabled={busy} onClick={() => jobAction(job,'retry')}>Retry processing</button>}
              {openUsage && usageJobs?.has(job.id) && <button className="action" onClick={() => openUsage(job.id)}>Job usage</button>}
            </div>
            {open === job.id && job.preview && <video key={job.revision} className="clip-preview" controls playsInline preload="metadata"
              src={new URL('api/preview/' + encodeURIComponent(job.id) + '?revision=' + encodeURIComponent(job.revision), window.location.href).href}
              aria-label={'Preview of ' + job.title}/>}
          </article>
        ))}
        {jobs.length > jobCount && <div className="panel-foot"><button onClick={() => setJobCount(value => value + 8)}>Show more jobs</button></div>}
      </section>
      <section className="panel">
        <div className="panel-head"><div><h2>Histold approvals</h2><p>Open the workflow artifact and review the draft before approving.</p></div></div>
        {approvals.length === 0 ? <p className="empty">No recent drafts in the approval queue.</p> : approvals.map(draft => (
          <article className="control-row" key={draft.stage}>
            <div className="control-row-title"><strong>{draft.title}</strong><small>{time(draft.at)}</small></div>
            <div className="control-actions">
              <a href={draft.url} target="_blank" rel="noreferrer" className="action">Open workflow <ArrowUpRight size={14}/></a>
              <button className="action primary" disabled={busy} onClick={() => {
                setPrivacy('private');
                setConfirm({ title:draft.title, detail:'Publish this Histold draft through its existing workflow. YouTube visibility does not set Facebook or Instagram visibility.', story:true,
                             command:{action:'story_approve', channel:'histold', stage:draft.stage, run_id:draft.run_id} });
              }}>Review approval</button>
            </div>
          </article>
        ))}
      </section>
      <section className="panel">
        <div className="panel-head"><div><h2>Activity from this dashboard</h2><p>Requests can finish after you close the page.</p></div></div>
        {commands.length === 0 ? <p className="empty">No host requests yet.</p> : commands.map((event, index) => (
          <div className="command-row" key={event.at + index}><strong>{models[event.action] || event.action}</strong>
            <small>{event.message} · {time(event.at)}</small></div>
        ))}
      </section>
    </div>
    <aside className="secondary" aria-label="Studio actions">
      <form className="panel control-form" onSubmit={event => {
        event.preventDefault(); send({action:'create_preview', channel:'shotsnip', mode, url, ranges, platforms});
      }}>
        <h2><Clapperboard size={17}/>New Shotsnip preview</h2>
        <label>Source URL<input type="url" required maxLength={550} value={url} onChange={event => setUrl(event.target.value)} placeholder="https://www.youtube.com/watch?v=…" /></label>
        <label>Clip selection<select value={mode} onChange={event => setMode(event.target.value)}><option value="replay">Select from replay</option><option value="exact">Use timestamps</option></select></label>
        {mode === 'exact' && <label>Time ranges<textarea value={ranges} required rows={2} maxLength={240} onChange={event => setRanges(event.target.value)} placeholder="01:20 - 01:50" /></label>}
        <fieldset><legend>Publishing destinations after approval</legend>
          {['youtube','facebook'].map(p => <label key={p}><input type="checkbox" checked={platforms.includes(p)}
            onChange={event => setPlatforms(value => event.target.checked ? [...value,p] : value.filter(item => item !== p))}/>{destinationText([p])}</label>)}
        </fieldset>
        <button className="action primary" type="submit" disabled={busy || platforms.length === 0}>Queue preview</button>
      </form>
      <form className="panel control-form" onSubmit={event => {
        event.preventDefault(); send({action:'story_draft', channel:'histold', format});
      }}>
        <h2><WandSparkles size={17}/>New Histold draft</h2>
        <label>Format<select value={format} onChange={event => setFormat(event.target.value)}><option value="short">Short</option><option value="long">Long-form</option></select></label>
        <button className="action primary" type="submit" disabled={busy}>Render draft</button>
      </form>
      <section className="panel control-form">
        <h2><Archive size={17}/>Windows host</h2>
        {hold ? <p className="control-note"><CircleAlert size={14}/>Shotsnip scheduling is on hold.</p> : null}
        <button className="action" disabled={busy || typeof scheduler !== 'boolean' || (hold && !scheduler)} onClick={() => {
          if (typeof scheduler !== 'boolean') return;
          setConfirm({title:'Shotsnip automatic posting', detail:scheduler ? 'Pause automatic posting. Manual jobs stay in their queue.' : 'Resume by the existing schedule.',
                      command:{action:'scheduler',channel:'shotsnip',enabled:!scheduler,expected:scheduler}});
        }}>{scheduler ? <Pause size={15}/> : <Play size={15}/>} {scheduler ? 'Pause automatic posting' : 'Resume automatic posting'}</button>
        <button className="action" disabled={busy} onClick={() => send({action:'archive_now'})}><Archive size={15}/>Archive one eligible clip</button>
        <button className="action" disabled={busy} onClick={() => send({action:'strategist'})}><RotateCw size={15}/>Run existing Strategist</button>
        {backupAt && <small className="backup-status"><Check size={15}/>Queue backup {time(backupAt)} IST</small>}
      </section>
    </aside>
    {confirm && <section className="confirm-panel" aria-label="Review command">
      <div><strong>{confirm.title}</strong><p>{confirm.detail}</p></div>
      {confirm.story && <label>YouTube visibility <select value={privacy} onChange={event => setPrivacy(event.target.value)}><option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select></label>}
      <div className="control-actions"><button className="action" onClick={() => setConfirm(null)}>Back</button>
        <button className="action primary" disabled={busy} onClick={() => {send(confirm.story ? {...confirm.command,privacy} : confirm.command);setConfirm(null);}}>Confirm</button></div>
    </section>}
  </div>;
}
