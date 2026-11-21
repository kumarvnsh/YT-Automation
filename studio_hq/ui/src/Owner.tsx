import { useState } from 'react';
import { ArrowsClockwise as Refresh, Microphone, MagnifyingGlass, PushPin, PushPinSlash } from '@phosphor-icons/react';
import type { CommandEvent, Send } from './Controls';
import type { AssistantRecord } from './Planning';

type Time = (value: string | null) => string;

/** Inline two-step confirmation: the first tap explains, the second sends. */
function useConfirm() {
  const [pending, setPending] = useState<{ key: string; text: string; run: () => void } | null>(null);
  const ask = (key: string, text: string, run: () => void) => setPending({ key, text, run });
  const box = (key: string, busy: boolean) => pending?.key === key && <div className="cadence-confirm inline-confirm" role="group" aria-label="Confirm change">
    <p>{pending.text}</p>
    <button className="action" onClick={() => setPending(null)}>Back</button>
    <button className="action primary" disabled={busy} onClick={() => { pending.run(); setPending(null); }}>Confirm</button>
  </div>;
  return { ask, box, open: (key: string) => pending?.key === key };
}

export function CommentaryControl({ mode, busy, send, recent, time }: { mode: string; busy: boolean; send?: Send; recent?: CommandEvent; time: Time }) {
  const modes = [['off', 'Off', 'No spoken voice'], ['intro', 'Intro', 'One spoken opening line'], ['full', 'Full', 'Narrated breakdown']] as const;
  return <section className="panel control-form">
    <h2><Microphone size={18} weight="duotone"/>Spoken commentary</h2>
    <p className="control-note">Voice on automatic clips. Applies to the next clips that are rendered.</p>
    <div className="segmented" role="radiogroup" aria-label="Commentary">
      {modes.map(([id, label, note]) => <button key={id} role="radio" aria-checked={mode === id} disabled={!send || busy || mode === id}
        onClick={() => send?.({ action: 'shotsnip_commentary', channel: 'shotsnip', mode: id })}><strong>{label}</strong><small>{note}</small></button>)}
    </div>
    {recent && <p className="analytics-info" role="status">{recent.message} · {time(recent.at)} IST</p>}
  </section>;
}

export function WatchWorker({ worker, busy, send, time }: {
  worker: { status: string; checked_at: string | null; note: string; stop_requested: boolean }; busy: boolean; send?: Send; time: Time }) {
  const confirm = useConfirm();
  const stale = !worker.checked_at || Date.now() - Date.parse(worker.checked_at) > 30 * 60 * 1000;
  const label = worker.stop_requested ? 'Stopping' : stale ? 'Not running' : worker.status.replaceAll('_', ' ');
  return <section className="panel control-form">
    <h2>Local watch worker</h2>
    <p className="control-note">Watches enabled YouTube targets and saves clips locally. It never publishes.</p>
    <p className="worker-status"><span className={'status ' + (!stale && !worker.stop_requested ? 'good' : 'neutral')}>{label}</span>
      {worker.checked_at && <small>Last report {time(worker.checked_at)} IST</small>}</p>
    {worker.note && !stale && <p className="control-note">{worker.note}</p>}
    {send && <div className="live-buttons tight">
      <button className="action" disabled={busy} onClick={() => send({ action: 'shotsnip_watch_worker', channel: 'shotsnip', start: true })}>Start worker</button>
      <button className="action" disabled={busy || confirm.open('stop')} onClick={() => confirm.ask('stop',
        'Stop the watch worker? The active capture closes at its next check; a render in progress finishes first.',
        () => send({ action: 'shotsnip_watch_worker', channel: 'shotsnip', start: false }))}>Stop worker</button>
    </div>}
    {confirm.box('stop', busy)}
  </section>;
}

export function AskShotsnip({ records, busy, send, recent, time }: { records: AssistantRecord[]; busy: boolean; send?: Send;
  recent?: CommandEvent; time: Time }) {
  const [question, setQuestion] = useState('');
  const thinking = records.some(row => row.status === 'thinking');
  const prompts = ['What should we test next, based on the evidence?', 'Which creators have enough evidence to compare?', 'Summarize recent capture failures.'];
  return <section className="panel assistant-panel">
    <div className="panel-head"><div><h2>Ask Shotsnip</h2><p>Questions about performance, sources, or capture problems. It answers; it cannot publish or change settings.</p></div></div>
    {send ? <form className="assistant-form" onSubmit={event => {
      event.preventDefault();
      if (!question.trim() || busy) return;
      send({ action: 'assistant_ask', channel: 'shotsnip', scope: 'shotsnip', question: question.trim() });
      setQuestion('');
    }}>
      <div className="filters" role="group" aria-label="Suggested questions">
        {prompts.map(text => <button type="button" key={text} onClick={() => setQuestion(text)}>{text}</button>)}</div>
      <label className="planning-search">Question
        <textarea value={question} maxLength={600} rows={3} onChange={event => setQuestion(event.target.value)} placeholder="What is working, and what should we test next?" /></label>
      <button className="action primary" type="submit" disabled={busy || !question.trim() || thinking}>Ask</button>
      {thinking && <p className="trace-muted" role="status">Shotsnip is answering. This can take a few minutes.</p>}
    </form> : <p className="chart-note">Questions are available when Studio HQ controls are enabled.</p>}
    {recent && <p className="analytics-info" role="status">{recent.message} · {time(recent.at)} IST</p>}
    {records.map(record => <article key={record.id} className="assistant-record">
      <p className="assistant-question"><strong>{record.question}</strong><small>{time(record.asked_at)} IST</small></p>
      {record.status === 'thinking' ? <p className="trace-muted">Waiting for the answer…</p>
        : <p className={record.status === 'failed' ? 'trace-muted' : 'assistant-reply'}>{record.reply}</p>}
    </article>)}
  </section>;
}

export type StrategyControls = {
  options: Record<'series' | 'region' | 'duration_variant', { id: string; label: string }[]>;
  overrides: Record<'series' | 'region' | 'duration_variant', { paused: string[]; boost: Record<string, number> }>;
  plan: { hours_utc: number[]; pinned_until: string | null; status: string };
  rules: { posts_per_day: number; allowed_hours_utc: number[]; min_gap_hours: number; max_boost: number };
};

const ist = (hour: number) => {
  const minutes = (hour * 60 + 330) % 1440;
  return String(Math.floor(minutes / 60)).padStart(2, '0') + ':' + String(minutes % 60).padStart(2, '0');
};

export function HistoldStrategyControls({ data, busy, send, recent, time }: { data: StrategyControls; busy: boolean; send?: Send;
  recent?: CommandEvent; time: Time }) {
  const [hours, setHours] = useState<number[]>(data.plan.hours_utc);
  const [days, setDays] = useState(7);
  const [dimension, setDimension] = useState<'series' | 'region' | 'duration_variant'>('series');
  const [factor, setFactor] = useState(1.5);
  const confirm = useConfirm();
  const strategy = (name: string, args: Record<string, string | number | number[]>) =>
    send?.({ action: 'histold_strategy', channel: 'histold', name, args } as never);
  const toggle = (hour: number) => setHours(current => current.includes(hour) ? current.filter(h => h !== hour) : [...current, hour].sort((a, b) => a - b));
  const sorted = [...hours].sort((a, b) => a - b);
  const gapOk = sorted.every((hour, index) => index === 0 || hour - sorted[index - 1] >= data.rules.min_gap_hours);
  const hoursOk = sorted.length === data.rules.posts_per_day && gapOk;
  const override = data.overrides[dimension];
  const boosts = [1.5, 2, 3].filter(value => value <= data.rules.max_boost);
  const dims = [['series', 'Series'], ['region', 'Regions'], ['duration_variant', 'Duration']] as const;

  return <div className="page-stack">
    {recent && <p className="analytics-info" role="status">{recent.message} · {time(recent.at)} IST</p>}
    <div className="page-split">
      <section className="panel control-form">
        <h2><PushPin size={18} weight="duotone"/>Publish hours</h2>
        <p className="control-note">{data.plan.pinned_until ? 'Pinned by you until ' + time(data.plan.pinned_until) + ' IST.' : 'The growth agent chooses the hours.'}
          {' '}Now: {data.plan.hours_utc.map(ist).join(', ')} IST.</p>
        <p className="control-note">Choose {data.rules.posts_per_day} hours, at least {data.rules.min_gap_hours} h apart, including one in the IST evening slot.</p>
        <div className="hour-grid" role="group" aria-label="Publish hours (IST)">
          {data.rules.allowed_hours_utc.map(hour => <button key={hour} type="button" aria-pressed={hours.includes(hour)} onClick={() => toggle(hour)}>{ist(hour)}</button>)}
        </div>
        <label>Keep pinned for<select value={days} onChange={event => setDays(Number(event.target.value))}>
          {[3, 7, 14, 30].map(value => <option key={value} value={value}>{value} days</option>)}</select></label>
        {!hoursOk && <p className="control-note">{sorted.length !== data.rules.posts_per_day ? `Pick exactly ${data.rules.posts_per_day} hours.` : `Hours must be ${data.rules.min_gap_hours} h apart.`}</p>}
        {send && <div className="live-buttons tight">
          <button className="action primary" type="button" disabled={busy || !hoursOk || confirm.open('pin')} onClick={() => confirm.ask('pin',
            `Pin publishing to ${sorted.map(ist).join(', ')} IST for ${days} days? The workflow uses it from the next publish hour.`,
            () => strategy('pin_hours', { hours: sorted, days }))}><PushPin size={16}/>Pin these hours</button>
          {data.plan.pinned_until && <button className="action" type="button" disabled={busy || confirm.open('unpin')} onClick={() => confirm.ask('unpin',
            'Let the growth agent choose publish hours again?', () => strategy('unpin_hours', {}))}><PushPinSlash size={16}/>Let the agent choose</button>}
        </div>}
        {confirm.box('pin', busy)}{confirm.box('unpin', busy)}
      </section>
      <section className="panel control-form">
        <h2><Refresh size={18} weight="duotone"/>Data and research</h2>
        <p className="control-note">Refresh pulls the latest analytics from every platform. Research re-runs the content analysis on the current data.</p>
        {send && <div className="live-buttons tight">
          <button className="action" disabled={busy} onClick={() => strategy('refresh_analytics', {})}><Refresh size={16}/>Refresh analytics</button>
          <button className="action" disabled={busy} onClick={() => send({ action: 'histold_research', channel: 'histold' })}><MagnifyingGlass size={16}/>Run content research</button>
        </div>}
      </section>
    </div>
    <section className="panel">
      <div className="panel-head"><div><h2>What the channel makes</h2>
        <p>Pause an option, pick it more often, or return it to the data-driven weight. New videos use the change from the next publish.</p></div></div>
      <div className="filters" role="group" aria-label="Option type">
        {dims.map(([id, label]) => <button key={id} aria-pressed={dimension === id} onClick={() => setDimension(id)}>{label}</button>)}
        <label className="boost-select">Boost by<select value={factor} onChange={event => setFactor(Number(event.target.value))}>
          {boosts.map(value => <option key={value} value={value}>×{value}</option>)}</select></label>
      </div>
      <ul className="option-list">{data.options[dimension].map(option => {
        const paused = override.paused.includes(option.id);
        const boost = override.boost[option.id];
        const key = dimension + option.id;
        return <li key={option.id}>
          <span><strong>{option.label}</strong>
            <small>{paused ? 'Paused' : boost ? 'Picked ×' + boost + ' as often' : 'Data-driven weight'}</small></span>
          {send && <span className="control-actions">
            {!paused && <button className="action" disabled={busy} onClick={() => confirm.ask(key, `Pause “${option.label}”? New videos stop using it.`,
              () => strategy('pause_option', { dimension, option: option.id }))}>Pause</button>}
            {!paused && <button className="action" disabled={busy} onClick={() => confirm.ask(key, `Pick “${option.label}” ×${factor} as often?`,
              () => strategy('boost_option', { dimension, option: option.id, factor }))}>Boost</button>}
            {(paused || boost) && <button className="action" disabled={busy} onClick={() => confirm.ask(key, `Return “${option.label}” to its data-driven weight?`,
              () => strategy('reset_option', { dimension, option: option.id }))}>Reset</button>}
          </span>}
          {confirm.box(key, busy)}
        </li>;
      })}</ul>
    </section>
  </div>;
}

type Provider = { label: string; installed: boolean; ready: boolean; per_5h?: number; per_week?: number; used_5h?: number;
  used_week?: number; resting_until?: string | null; last_error?: string | null };
export type AiSettings = { mode: string; enabled: boolean; providers: Record<string, Provider> };

/** Which AI answers in the assistants, and how much of each paid plan they may use. */
export function AiSettingsPanel({ data, busy, send, recent, time }: { data: AiSettings; busy: boolean; send?: Send; recent?: CommandEvent; time: Time }) {
  const [caps, setCaps] = useState<Record<string, { per_5h: number; per_week: number }>>(() => Object.fromEntries(
    Object.entries(data.providers).filter(([, p]) => p.per_5h != null).map(([id, p]) => [id, { per_5h: p.per_5h!, per_week: p.per_week! }])));
  const modes = [['auto', 'Auto', 'Codex, then Claude, then local'], ['claude', 'Claude', 'Falls back to local'],
    ['codex', 'Codex', 'Falls back to local'], ['local', 'Local only', 'qwen3:8b on this PC']] as const;
  return <div className="page-stack">
    {recent && <p className="analytics-info" role="status">{recent.message} · {time(recent.at)} IST</p>}
    <section className="panel control-form">
      <h2>Which AI answers</h2>
      <p className="control-note">Used by the Histold assistant, Ask Shotsnip and content research.</p>
      <div className="segmented four" role="radiogroup" aria-label="AI answering">
        {modes.map(([id, label, note]) => <button key={id} role="radio" aria-checked={data.mode === id} disabled={!send || busy || data.mode === id}
          onClick={() => send?.({ action: 'ai_settings', channel: 'histold', mode: id })}><strong>{label}</strong><small>{note}</small></button>)}
      </div>
    </section>
    <div className="page-split even">
      {Object.entries(data.providers).map(([id, provider]) => <section className="panel control-form" key={id}>
        <h2>{provider.label}</h2>
        <p className="worker-status"><span className={'status ' + (provider.ready ? 'good' : 'warn')}>
          {!provider.installed ? 'Not installed' : provider.resting_until ? 'Resting' : provider.ready ? 'Ready' : 'Limit reached'}</span>
          {provider.resting_until && <small>until {time(provider.resting_until)} IST</small>}</p>
        {provider.used_5h != null && <div className="usage-bars">
          {(['5h', 'week'] as const).map(window => {
            const used = window === '5h' ? provider.used_5h! : provider.used_week!;
            const cap = window === '5h' ? provider.per_5h! : provider.per_week!;
            return <div key={window}><span>{window === '5h' ? 'Last 5 hours' : 'Last 7 days'}<b>{used} of {cap}</b></span>
              <i><em style={{ width: Math.min(100, cap ? used / cap * 100 : 100) + '%' }} /></i></div>;
          })}</div>}
        {provider.last_error && <p className="control-note">Last problem: {provider.last_error}</p>}
        {send && caps[id] && <form className="cap-form" onSubmit={event => {
          event.preventDefault(); send({ action: 'ai_settings', channel: 'histold', provider: id, ...caps[id] });
        }}>
          <label>Per 5 hours<input type="number" min={0} max={1000} value={caps[id].per_5h}
            onChange={event => setCaps(all => ({ ...all, [id]: { ...all[id], per_5h: Number(event.target.value) } }))} /></label>
          <label>Per 7 days<input type="number" min={0} max={1000} value={caps[id].per_week}
            onChange={event => setCaps(all => ({ ...all, [id]: { ...all[id], per_week: Number(event.target.value) } }))} /></label>
          <button className="action" type="submit" disabled={busy}>Save caps</button>
          {provider.resting_until && <button className="action" type="button" disabled={busy}
            onClick={() => send({ action: 'ai_settings', channel: 'histold', provider: id, clear_cooldown: true })}>End rest now</button>}
        </form>}
      </section>)}
    </div>
  </div>;
}
