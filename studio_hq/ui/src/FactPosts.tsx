import { useState } from 'react';
import { ConfirmPanel, type CommandEvent, type Pending, type Send } from './Controls';
import { ArrowUpRight, Pause, Play, RotateCw, Trash2, WandSparkles } from './icons';
import { mediaUrl } from './Media';

type Time = (value: string | null) => string;
export type FactPost = {
  id: string; status: string; label: string; headline: string | null; fact: string | null; image: string | null;
  sources: { publisher: string | null; url: string | null }[]; created: string | null; posted_at: string | null;
  error: string | null; facebook_url: string | null; instagram: boolean;
};
export type FactPostsData = {
  schedule: { enabled: boolean; hours_utc: number[]; approve_until: string | null; changed_at: string | null };
  posts: FactPost[]; posted_today: number; running: boolean;
};

const tone: Record<string, string> = { posted: 'good', awaiting_approval: 'warn', failed: 'warn', partly_posted: 'warn',
  waiting_plans: 'warn', skipped: 'neutral', making: 'neutral', approved: 'good', posting: 'good' };
const ist = (hour: number) => { const m = (hour * 60 + 330) % 1440; const h = Math.floor(m / 60);
  return `${(h % 12) || 12}:${String(m % 60).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`; };

/** Histold fact image posts: the schedule, approvals and every card made. */
export function FactPosts({ data, busy, send, recent, time }: { data: FactPostsData; busy: boolean; send?: Send;
  recent?: CommandEvent; time: Time }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [limit, setLimit] = useState(9);
  const waiting = data.posts.filter(post => post.status === 'awaiting_approval');
  const failed = data.posts.filter(post => post.status === 'failed').length;
  const approval = data.schedule.approve_until && new Date(data.schedule.approve_until + 'T23:59:59Z').getTime() >= Date.now();
  return <div className="page-stack">
    <section className="panel live-controls" aria-labelledby="fact-posts-heading">
      <div className="panel-head"><div><h2 id="fact-posts-heading">Fact image posts</h2>
        <p>Verified history facts, illustrated by Gemini (Codex as backup), posted to Facebook and Instagram.</p></div>
        <span className={'status ' + (data.schedule.enabled ? 'good' : 'neutral')}>{data.schedule.enabled ? 'On schedule' : 'Paused'}</span></div>
      <div className="live-metrics">
        <div><small>Posted today</small><strong>{data.posted_today} of {data.schedule.hours_utc.length || 3}</strong></div>
        <div><small>Waiting for your OK</small><strong>{waiting.length}</strong></div>
        <div><small>Daily times (IST)</small><strong className="small-strong">{data.schedule.hours_utc.map(ist).join(' · ') || '—'}</strong></div>
      </div>
      <div className="live-state">
        <p>{approval ? `Until ${new Date(data.schedule.approve_until + 'T00:00:00Z').toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}, each post waits for your OK here or in Telegram. After that, posts that pass every check go out on their own.`
          : 'Posts that pass every check go out on their own.'}</p>
        {data.running && <p className="control-note"><WandSparkles size={14}/>A fact post is being made right now.</p>}
      </div>
      {send && <div className="live-buttons">
        <button className="action primary" disabled={busy || data.running} onClick={() => send({ action: 'fact_post_make', channel: 'histold' })}>
          <WandSparkles size={16}/>Make one now</button>
        {data.schedule.enabled
          ? <button className="action" disabled={busy} onClick={() => setPending({ title: 'Pause scheduled fact posts?',
              detail: 'No fact posts are made at the daily times until you turn them back on. Waiting posts stay here.',
              command: { action: 'fact_post_schedule', channel: 'histold', enabled: false } })}><Pause size={16}/>Pause schedule</button>
          : <button className="action" disabled={busy} onClick={() => setPending({ title: 'Post three times a day?',
              detail: 'A fact post is made at each daily time on your Claude and Codex plans.',
              command: { action: 'fact_post_schedule', channel: 'histold', enabled: true } })}><Play size={16}/>Start schedule</button>}
        {failed > 0 && <button className="action" disabled={busy} onClick={() => setPending({
          title: `Remove ${failed === 1 ? 'the failed post' : `all ${failed} failed posts`} from the list?`,
          detail: 'They are hidden here. Posted and waiting posts are not touched.',
          command: { action: 'fact_post_remove', channel: 'histold', id: 'failed' } })}><Trash2 size={16}/>Remove failed ({failed})</button>}
      </div>}
      {recent && <p className="analytics-info" role="status">{recent.message} · {time(recent.at)} IST</p>}
      {pending && send && <ConfirmPanel pending={pending} busy={busy} send={send} close={() => setPending(null)} />}
    </section>
    {data.posts.length === 0 ? <section className="panel empty">No fact posts yet. Use “Make one now” to create the first.</section>
      : <section className="fact-grid" aria-label="Fact posts">
        {data.posts.slice(0, limit).map(post => <article key={post.id} className="panel fact-card">
          {post.image ? <img src={mediaUrl(post.image)} alt={post.headline ?? 'Fact post'} loading="lazy" />
            : <div className="fact-placeholder">{post.status === 'making' ? 'Being made…' : 'No card'}</div>}
          <div className="fact-body">
            <span className={'status ' + (tone[post.status] ?? 'neutral')}>{post.label}</span>
            <strong>{post.headline ?? 'Researching a fact…'}</strong>
            {post.fact && <p>{post.fact}</p>}
            {post.sources.length > 0 && <small>Sources: {post.sources.map((s, i) => <span key={i}>{i ? ', ' : ''}
              {s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.publisher ?? 'source'}</a> : s.publisher}</span>)}</small>}
            {post.error && post.status !== 'posted' && <small className="fact-error">{post.error}</small>}
            <small>{post.posted_at ? 'Posted ' + time(post.posted_at) + ' IST' : post.created ? 'Made ' + time(post.created) + ' IST' : ''}
              {post.instagram ? ' · Instagram ✓' : ''}</small>
            <div className="live-buttons">
              {send && post.status === 'awaiting_approval' && <>
                <button className="action primary" disabled={busy} onClick={() => setPending({ title: 'Post this to Facebook and Instagram?',
                  detail: post.headline ?? '', command: { action: 'fact_post_decide', channel: 'histold', id: post.id, choice: 'approve' } })}>Post it</button>
                <button className="action" disabled={busy} onClick={() => send({ action: 'fact_post_decide', channel: 'histold', id: post.id, choice: 'skip' })}>Skip</button>
              </>}
              {send && (post.status === 'failed' || post.status === 'waiting_plans') && <button className="action primary" disabled={busy || data.running}
                aria-label={`Retry fact post ${post.headline ?? ''}`.trim()}
                onClick={() => send({ action: 'fact_post_retry', channel: 'histold', id: post.id })}><RotateCw size={14}/>Retry</button>}
              {send && post.status === 'failed' && <button className="action" disabled={busy} aria-label={`Remove failed post ${post.headline ?? ''}`.trim()}
                onClick={() => send({ action: 'fact_post_remove', channel: 'histold', id: post.id })}><Trash2 size={14}/>Remove</button>}
              {post.facebook_url && <a className="action" href={post.facebook_url} target="_blank" rel="noreferrer">Facebook <ArrowUpRight size={14}/></a>}
            </div>
          </div>
        </article>)}
      </section>}
    {data.posts.length > limit && <div className="panel-foot"><button onClick={() => setLimit(n => n + 9)}>Show more posts</button></div>}
  </div>;
}
