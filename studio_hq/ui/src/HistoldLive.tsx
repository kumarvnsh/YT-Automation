import { useState } from 'react';
import { ConfirmPanel, type CommandEvent, type Pending, type Send } from './Controls';
import { ArrowUpRight, Check, CircleAlert, Cloud, HardDrive, Play, RotateCw } from './icons';

type Time = (value: string | null) => string;
type PlanUse = { used_5h: number; used_week: number; per_5h: number; per_week: number; ready: boolean;
  resting_until: string | null; frees_at: string | null; last_error: string | null };
export type HistoldLiveData = {
  mode: { local: boolean; source: 'studio_hq' | 'config'; changed_at: string | null };
  run: null | { running: boolean; stage?: string; format?: string; title?: string | null; started?: string | null; updated?: string;
    done?: string[]; current?: string | null; current_label?: string | null; detail?: string | null; problem?: string | null;
    progress?: { step: number; measured: boolean; overall: number | null; counts: { scenes: number; written: number; rendered: number } | null } | null; kind?: string | null; complete?: boolean; dry_run?: boolean; preview?: boolean; error?: string | null;
    youtube_url?: string | null; usage?: VideoUsage | null };
  plans: { claude: PlanUse; codex: PlanUse; qwen: { ready: boolean; running: boolean } };
  waiting: null | { format: 'short' | 'long'; retry_at: string | null; reason: string; attempts: number };
  setup: { name: string; ok: boolean }[];
  events: { at: string; text: string }[];
  heartbeat: string | null;
};

type UsageRow = { calls: number; failed: number; input: number; cached: number; output: number; seconds: number };
export type VideoUsage = { steps: (UsageRow & { step: string; label: string; providers: string[] })[]; total: UsageRow };
const providerNames: Record<string, string> = { claude_plan: 'Claude', codex_plan: 'Codex', qwen_local: 'Qwen' };
const compactNumber = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 });
const minutes = (seconds: number) => seconds < 60 ? Math.round(seconds) + ' s' : (seconds / 60).toFixed(1) + ' min';

/** Calls, tokens and model time this video used from each plan, step by step. */
function UsageTable({ usage }: { usage: VideoUsage }) {
  const cell = (row: UsageRow) => <>
    <td data-label="Calls">{row.calls}{row.failed ? <small> · {row.failed} failed</small> : null}</td>
    <td data-label="Tokens in">{compactNumber.format(row.input)}<small>{row.cached ? ' + ' + compactNumber.format(row.cached) + ' cached' : ''}</small></td>
    <td data-label="Tokens out">{compactNumber.format(row.output)}</td><td data-label="Model time">{minutes(row.seconds)}</td></>;
  return <div className="video-usage">
    <h3>Plan usage for this video</h3>
    <div className="table-scroll"><table>
      <thead><tr><th scope="col">Step</th><th scope="col">Calls</th><th scope="col">Tokens in</th><th scope="col">Tokens out</th><th scope="col">Model time</th></tr></thead>
      <tbody>{usage.steps.map(row => <tr key={row.step}><th scope="row">{row.label}
        <small>{row.providers.map(name => providerNames[name] ?? name).join(', ')}</small></th>{cell(row)}</tr>)}</tbody>
      <tfoot><tr><th scope="row">Total</th>{cell(usage.total)}</tr></tfoot>
    </table></div>
    <p className="chart-note">Counted against Histold’s share of each plan. Cached tokens count for much less. Codex does not report tokens, only calls.</p>
  </div>;
}

function Bar({ label, value, note }: { label: string; value: number; note?: string }) {
  const percent = Math.round(value * 100);
  return <div className="progress-row">
    <div className="progress-label"><span>{label}</span><b>{percent}%</b></div>
    <div className="progress-track" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
      <i style={{ width: Math.max(percent, 2) + '%' }} /></div>
    {note && <small>{note}</small>}
  </div>;
}

/** Pipeline steps grouped into the stages a person thinks in. */
const STAGES: [string, string[]][] = [
  ['Research', ['research']], ['Script', ['script']], ['Voice', ['voiceover', 'align', 'audio_check', 'captions']],
  ['Visuals', ['assets']], ['Render', ['render', 'compose']], ['Checks', ['quality', 'thumbnail']], ['Upload', ['upload']],
];
const formatName = (format?: string) => format === 'long' ? 'long video' : 'Short';

/** Where Histold videos are made, one-off runs, and the video waiting for the plans to reset. */
export function HistoldLiveControls({ data, busy, send, recent, time }: { data: HistoldLiveData; busy: boolean; send?: Send;
  recent?: CommandEvent; time: Time }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const local = data.mode.local;
  const missing = data.setup.filter(item => !item.ok);
  const running = !!data.run?.running;
  return <section className="panel live-controls" aria-labelledby="histold-controls-heading">
    <div className="panel-head"><div><h2 id="histold-controls-heading">Live controls</h2>
      <p>Where Histold videos are made, and one-off runs.</p></div>
      <span className={'status ' + (local ? 'good' : 'neutral')}>{local ? 'On this PC' : 'Paid cloud'}</span></div>
    <div className="live-state">
      {local ? <p><HardDrive size={15}/> Videos are made on this PC with your Claude and Codex plans. When both plans are used up,
        the video waits for them to reset; no paid credits are used. Other failures hand the slot to the paid cloud run.</p>
        : <p><Cloud size={15}/> Videos are made in the GitHub cloud workflow, which uses paid API credits.</p>}
      {data.mode.changed_at && <p className="control-note">Switched from Studio HQ {time(data.mode.changed_at)} IST.</p>}
      {missing.length > 0 && <p className="control-note"><CircleAlert size={14}/>Not ready on this PC: {missing.map(item => item.name).join(', ')}.</p>}
      {running && <p className="control-note"><Check size={14}/>A video is being made on this PC right now.</p>}
    </div>
    {data.waiting && <div className="histold-waiting">
      <div><strong>A {formatName(data.waiting.format)} is waiting for your plans</strong>
        <small>{data.waiting.retry_at ? 'Tries again after ' + time(data.waiting.retry_at) + ' IST' : 'Tries again at the next hourly check'}
          {data.waiting.attempts > 1 ? ' · attempt ' + data.waiting.attempts : ''}</small></div>
      {send && <div className="live-buttons">
        <button className="action" disabled={busy || running} onClick={() => send({ action: 'histold_local_retry', channel: 'histold', choice: 'now' })}>
          <RotateCw size={15}/>Try now</button>
        <button className="action" disabled={busy} onClick={() => setPending({ title: 'Drop the waiting video?',
          detail: 'It will not be made. The next scheduled slot runs as usual.',
          command: { action: 'histold_local_retry', channel: 'histold', choice: 'cancel' } })}>Drop it</button>
      </div>}
    </div>}
    {send && <div className="live-buttons">
      {local
        ? <button className="action" disabled={busy} onClick={() => setPending({ title: 'Switch to the paid cloud?',
            detail: 'From the next publish hour, Histold videos are made in the GitHub cloud workflow using paid API credits.',
            command: { action: 'histold_local_mode', channel: 'histold', local: false } })}><Cloud size={16}/>Use paid cloud</button>
        : <button className="action primary" disabled={busy || missing.length > 0} onClick={() => setPending({ title: 'Make videos on this PC?',
            detail: 'From the next publish hour, Histold videos are made here with your Claude and Codex plans. This PC must be awake at publish times.',
            command: { action: 'histold_local_mode', channel: 'histold', local: true } })}><HardDrive size={16}/>Make videos on this PC</button>}
      {local && <button className="action" disabled={busy || running || missing.length > 0} onClick={() => setPending({ title: 'Make a Short now?',
        detail: 'Research, write, render and publish one extra Short now on your plans. Today’s posting cap still applies.',
        command: { action: 'histold_local_run', channel: 'histold', format: 'short' } })}><Play size={16}/>Make a Short now</button>}
    </div>}
    {recent && <p className="analytics-info" role="status">{recent.message} · {time(recent.at)} IST</p>}
    {pending && send && <ConfirmPanel pending={pending} busy={busy} send={send} close={() => setPending(null)} />}
  </section>;
}

/** The video in progress on this PC, as a stage track with recent runner events. */
export function HistoldLiveOperations({ data, nextSlot, time }: { data: HistoldLiveData; nextSlot: string | null; time: Time }) {
  const run = data.run;
  const done = new Set(run?.done ?? []);
  const reached = STAGES.map(([, steps]) => steps.every(step => done.has(step)));
  // No stage folder yet means the runner is still preparing (syncing, checking the plans, claiming the slot).
  const live = run?.running && run.current ? STAGES.findIndex(([, steps]) => steps.includes(run.current!)) : -1;
  const active = live >= 0 ? live : run?.running && run.stage ? reached.findIndex(value => !value) : -1;
  const failed = !!run?.error && !run.running;
  const state = run?.running ? 'Working' : failed ? 'Stopped' : run?.complete ? 'Finished' : 'Idle';
  return <section className="panel live-operations" aria-labelledby="histold-live-heading">
    <div className="panel-head planning-head">
      <div><h2 id="histold-live-heading">Histold live operations</h2><p>The video being made on this PC, stage by stage.</p></div>
      <div className="tool-links"><span className={'status ' + (failed ? 'warn' : run?.running ? 'good' : 'neutral')}>{state}</span></div>
    </div>
    <div className="live-metrics">
      <div><small>{run?.running ? 'Making now' : 'Last video'}</small><strong>{run?.title || (run?.running ? 'Starting…' : '—')}</strong></div>
      <div><small>Stage</small><strong>{active >= 0 ? STAGES[active][0] : run?.running ? 'Getting ready' : run?.complete ? 'Done' : failed ? 'Stopped' : '—'}</strong></div>
      <div><small>Next publish</small><strong>{nextSlot ? time(nextSlot) : '—'}</strong></div>
    </div>
    <ol className="live-stages" aria-label="Video stages">
      {STAGES.map(([label], index) => <li key={label} aria-current={index === active ? 'step' : undefined}
        className={reached[index] ? 'done' : undefined}>{label}</li>)}
    </ol>
    {run?.running && run.problem && <p className="live-now problem" role="status">
      <CircleAlert size={18} aria-hidden="true" /><span><b>{run.current_label ?? 'This step'} hit a problem, retrying automatically</b>
      <small>{run.problem}</small></span></p>}
    {run?.running && !run.problem && (run.current_label || run.detail) && <p className="live-now" role="status">
      <span className="spinner" aria-hidden="true" /><span><b>{run.current_label ?? 'Working'}</b>
      {run.detail && <small>{run.detail}</small>}</span></p>}
    {run?.running && run.progress && <div className="live-progress">
      <Bar label={(run.current_label ?? 'This step')} value={run.progress.step}
        note={run.progress.counts ? `${run.progress.counts.written} of ${run.progress.counts.scenes} scenes written · ${run.progress.counts.rendered} of ${run.progress.counts.scenes} rendered`
          : run.progress.measured ? undefined : 'Estimated from how long this step usually takes'} />
      {run.progress.overall != null && <Bar label="Whole video" value={run.progress.overall} />}
    </div>}
    <div className="planning-detail">
      {run?.format && <span>{run.dry_run ? 'Script test' : run.preview ? 'Preview, not uploaded' : formatName(run.format)}</span>}
      {run?.started && <span>Started {time(run.started)} IST</span>}
      {run?.updated && <span>Last progress {time(run.updated)} IST</span>}
      {data.heartbeat && <span>Runner log {time(data.heartbeat)} IST</span>}
      {run?.youtube_url && <a className="inline-link" href={run.youtube_url} target="_blank" rel="noreferrer">Watch on YouTube <ArrowUpRight size={13}/></a>}
    </div>
    {failed && <p className="chart-note">Stopped: {run?.error}</p>}
    {run?.usage && <UsageTable usage={run.usage} />}
    <details className="live-history"><summary>Recent runner events</summary>
      {data.events.length ? <ol>{data.events.map((item, index) =>
        <li key={item.at + index}><time dateTime={item.at}>{time(item.at)} IST</time><span>{item.text}</span></li>)}</ol>
        : <p className="chart-note">No local runs yet.</p>}
    </details>
  </section>;
}

function Meter({ label, used, cap }: { label: string; used: number; cap: number }) {
  const share = cap ? Math.min(1, used / cap) : 0;
  return <div className="plan-meter">
    <span>{label}</span>
    <span className="traffic-track" role="meter" aria-valuemin={0} aria-valuemax={cap} aria-valuenow={used} aria-label={label}>
      <i style={{ width: Math.max(share * 100, used ? 2 : 0) + '%' }} className={share >= 1 ? 'full' : share >= 0.8 ? 'high' : undefined} /></span>
    <b>{used} / {cap}</b>
  </div>;
}

/** Histold's share of each plan, so you can see how close a video is to waiting. */
export function PlanLimits({ data, time }: { data: HistoldLiveData; time: Time }) {
  const rows: [string, PlanUse][] = [['Claude', data.plans.claude], ['Codex', data.plans.codex]];
  return <section className="panel" aria-labelledby="plan-limits-heading">
    <div className="panel-head"><div><h2 id="plan-limits-heading">Plan limits</h2>
      <p>Histold’s share of each plan. Calls are counted per rolling window.</p></div></div>
    <div className="plan-list">
      {rows.map(([name, plan]) => <article key={name} className="plan-card">
        <div className="plan-title"><strong>{name}</strong>
          <span className={'status ' + (plan.ready ? 'good' : 'warn')}>{plan.ready ? 'Available' : 'Resting'}</span></div>
        <Meter label="Last 5 hours" used={plan.used_5h} cap={plan.per_5h} />
        <Meter label="Last 7 days" used={plan.used_week} cap={plan.per_week} />
        {plan.resting_until && <p className="control-note">Plan limit reached · available again after {time(plan.resting_until)} IST</p>}
        {!plan.resting_until && plan.frees_at && <p className="control-note">Histold’s 5-hour share is used · frees up after {time(plan.frees_at)} IST</p>}
        {plan.last_error && !plan.resting_until && <p className="chart-note">Last problem: {plan.last_error}</p>}
      </article>)}
      <article className="plan-card">
        <div className="plan-title"><strong>Qwen on this PC</strong>
          <span className={'status ' + (data.plans.qwen.ready ? 'good' : 'neutral')}>{data.plans.qwen.ready ? 'Available' : data.plans.qwen.running ? 'Model missing' : 'Off'}</span></div>
        <p className="control-note">Last resort for fact checks and thumbnail text only. It never researches or writes scenes.</p>
      </article>
    </div>
  </section>;
}
