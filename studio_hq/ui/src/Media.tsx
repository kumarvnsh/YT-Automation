import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowUpRight, Play, X } from '@phosphor-icons/react';
import type { Send } from './Controls';

type Time = (value: string | null) => string;
export const mediaUrl = (id: string) => new URL('api/media/' + id, window.location.href).href;

/** Native modal dialog: focus stays inside, Esc and the backdrop close it, focus returns afterwards. */
export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return <dialog ref={ref} className="modal" aria-label={title} onCancel={event => { event.preventDefault(); onClose(); }}
    onClick={event => { if (event.target === ref.current) onClose(); }}>
    <div className="modal-head"><h2>{title}</h2>
      <button className="icon-link" aria-label="Close" onClick={onClose}><X size={18}/></button></div>
    <div className="modal-body">{children}</div>
  </dialog>;
}

export type Clip = { media: string; title: string; creator: string; origin: string; run: string; at: string | null; intro: string;
  source_url: string | null; status: string | null; youtube: string | null; facebook: string | null; error: string | null };
export type Capture = { run: string; creator: string; worker: string; clips: number; chat_messages: number | null;
  elapsed_seconds: number | null; errors: string[]; source_url: string | null };
export type ClipData = { library: Clip[]; captures: Capture[] };

const origins = [['all', 'All'], ['Automatic cycle', 'Automatic'], ['Live chat-spike monitor', 'Live'], ['Manual job', 'Manual'], ['published', 'Published']] as const;

export function ClipsPage({ data, time }: { data: ClipData; time: Time }) {
  const [filter, setFilter] = useState<string>('all');
  const [search, setSearch] = useState('');
  const [limit, setLimit] = useState(15);
  const [playing, setPlaying] = useState<Clip | null>(null);
  const query = search.trim().toLowerCase();
  const clips = data.library.filter(clip => (filter === 'all' || (filter === 'published' ? !!(clip.youtube || clip.facebook) : clip.origin === filter))
    && (clip.title.toLowerCase().includes(query) || clip.creator.toLowerCase().includes(query)));
  return <div className="page-stack">
    <section className="panel">
      <div className="panel-head"><div><h2>Clip library</h2><p>Every finished clip, newest first, with where it came from and what happened to it.</p></div></div>
      <div className="filters" role="group" aria-label="Clip source">
        {origins.map(([id, label]) => <button key={id} aria-pressed={filter === id} onClick={() => { setFilter(id); setLimit(15); }}>{label}</button>)}
      </div>
      <label className="planning-search">Find a clip<input type="search" value={search} placeholder="Search title or streamer"
        onChange={event => { setSearch(event.target.value); setLimit(15); }} /></label>
      {clips.length === 0 && <p className="empty">No matching clips.</p>}
      <ul className="clip-list">{clips.slice(0, limit).map(clip => <li key={clip.media}>
        <button className="clip-play" onClick={() => setPlaying(clip)} aria-label={'Play ' + clip.title}><Play size={20} weight="fill"/></button>
        <span className="clip-main"><strong>{clip.title}</strong>
          <small>{clip.creator} · {clip.origin} · {time(clip.at)} IST</small>
          {clip.error && <small className="clip-error">{clip.error}</small>}</span>
        <span className="clip-links">
          {clip.youtube ? <a className="status good" href={'https://www.youtube.com/watch?v=' + clip.youtube} target="_blank" rel="noreferrer">YouTube</a>
            : <span className="status neutral">{clip.status ? clip.status.replaceAll('_', ' ') : 'Not published'}</span>}
          {clip.facebook && <span className="status good">Facebook</span>}
        </span>
      </li>)}</ul>
      {clips.length > limit && <div className="panel-foot"><button onClick={() => setLimit(value => value + 15)}>Show more clips</button></div>}
    </section>
    <section className="panel">
      <div className="panel-head"><div><h2>Captures</h2><p>Recent capture runs from the automatic and live workers.</p></div></div>
      <ul className="capture-list">{data.captures.map(run => <li key={run.run}>
        <span><strong>{run.creator || 'Unknown source'}</strong>
          <small>{run.worker === 'automatic' ? 'Automatic cycle' : 'Live monitor'} · {run.run.slice(0, 8)} {run.run.slice(9, 11)}:{run.run.slice(11, 13)}
            {run.chat_messages != null ? ' · ' + run.chat_messages + ' chat messages' : ''}{run.elapsed_seconds != null ? ' · ' + Math.round(run.elapsed_seconds / 60) + ' min' : ''}</small>
          {run.errors.map((error, index) => <small key={index} className="clip-error">{error}</small>)}</span>
        <span className={'status ' + (run.errors.length ? 'warn' : run.clips ? 'good' : 'neutral')}>{run.clips} {run.clips === 1 ? 'clip' : 'clips'}</span>
        {run.source_url ? <a className="icon-link" href={run.source_url} target="_blank" rel="noreferrer" aria-label="Open the source stream"><ArrowUpRight size={15}/></a> : <span/>}
      </li>)}</ul>
    </section>
    {playing && <Modal title={playing.title} onClose={() => setPlaying(null)}>
      <video className="modal-video vertical" src={mediaUrl(playing.media)} controls autoPlay playsInline preload="metadata" />
      <dl className="detail-list">
        <div><dt>Streamer</dt><dd>{playing.creator || '—'}</dd></div>
        <div><dt>Source</dt><dd>{playing.origin}</dd></div>
        <div><dt>Finished</dt><dd>{time(playing.at)} IST</dd></div>
        {playing.intro && <div><dt>Spoken intro</dt><dd>{playing.intro}</dd></div>}
        <div><dt>Published</dt><dd>{playing.youtube || playing.facebook ? [playing.youtube && 'YouTube', playing.facebook && 'Facebook'].filter(Boolean).join(' and ') : 'Not yet'}</dd></div>
      </dl>
      <div className="control-actions">
        {playing.youtube && <a className="action" href={'https://www.youtube.com/watch?v=' + playing.youtube} target="_blank" rel="noreferrer">Watch on YouTube <ArrowUpRight size={14}/></a>}
        {playing.source_url && <a className="action" href={playing.source_url} target="_blank" rel="noreferrer">Source stream <ArrowUpRight size={14}/></a>}
      </div>
    </Modal>}
  </div>;
}

export type DraftPreview = { video: string; thumbnail: string | null; size: number; title: string; description: string; tags: string[];
  privacy: string; narration: string[]; quality: { passed: boolean | null; score: number | null; errors: string[]; warnings: string[] } };

export function DraftModal({ title, stage, runId, url, at, preview, busy, send, time, onClose }: { title: string; stage: string; runId: string;
  url: string; at: string; preview: DraftPreview; busy: boolean; send?: Send; time: Time; onClose: () => void }) {
  const [privacy, setPrivacy] = useState('private');
  const [confirm, setConfirm] = useState(false);
  return <Modal title={preview.title || title} onClose={onClose}>
    <div className="draft-layout">
      <video className="modal-video vertical" src={mediaUrl(preview.video)} poster={preview.thumbnail ? mediaUrl(preview.thumbnail) : undefined}
        controls playsInline preload="metadata" />
      <div className="draft-details">
        <p className="trace-muted">Rendered {time(at)} IST · {stage} · {(preview.size / 1024 ** 2).toFixed(1)} MB</p>
        {preview.quality.passed != null && <p><span className={'status ' + (preview.quality.passed ? 'good' : 'warn')}>
          {preview.quality.passed ? 'Quality checks passed' : 'Quality checks raised issues'}{preview.quality.score != null ? ' · score ' + preview.quality.score : ''}</span></p>}
        {preview.quality.errors.length > 0 && <ul className="draft-issues">{preview.quality.errors.map(item => <li key={item}>{item}</li>)}</ul>}
        {preview.description && <><h3>Description</h3><p className="assistant-reply">{preview.description}</p></>}
        {preview.tags.length > 0 && <p className="tag-row">{preview.tags.map(tag => <span key={tag}>{tag}</span>)}</p>}
        {preview.thumbnail && <><h3>Thumbnail</h3><img className="draft-thumb" src={mediaUrl(preview.thumbnail)} alt={'Thumbnail for ' + preview.title} /></>}
        {preview.narration.length > 0 && <details><summary>Narration ({preview.narration.length} parts)</summary>
          <ol className="narration">{preview.narration.map((line, index) => <li key={index}>{line}</li>)}</ol></details>}
        <div className="control-actions">
          <a className="action" href={url} target="_blank" rel="noreferrer">Workflow run {runId} <ArrowUpRight size={14}/></a>
        </div>
        {send && <div className="draft-approve">
          <label>YouTube visibility<select value={privacy} onChange={event => setPrivacy(event.target.value)}>
            <option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select></label>
          {!confirm ? <button className="action primary" disabled={busy} onClick={() => setConfirm(true)}>Approve and publish</button>
            : <div className="inline-confirm cadence-confirm"><p>Publish this draft through its existing workflow with {privacy} YouTube visibility? Facebook and Instagram follow their own settings.</p>
              <button className="action" onClick={() => setConfirm(false)}>Back</button>
              <button className="action primary" disabled={busy} onClick={() => {
                send({ action: 'story_approve', channel: 'histold', stage, run_id: runId, privacy }); setConfirm(false); onClose();
              }}>Confirm</button></div>}
        </div>}
      </div>
    </div>
  </Modal>;
}
