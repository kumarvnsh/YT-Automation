import { useState } from 'react';
import { ArrowUpRight } from './icons';
import type { CommandEvent, Send } from './Controls';

export type JobLink = { channel: 'histold' | 'shotsnip'; id: string };
type JobCharge = { attempts: number; failed: number; priced: number; cost: string | null };
type CalendarEvent = { at: string; channel: 'histold' | 'shotsnip'; title: string; state: string; source: string; url: string | null };
type Observation = { platform: string; media: string; value: number | null; metric: string; likes: number | null;
  comments: number | null; observed_at: string | null; age_hours: number | null };
export type Trace = {
  idea: { premise: string; viewer_reason: string; selection_reason: string; rejected: { premise: string; reason: string }[] } | null;
  reservation: { date: string | null; slot: string };
  run: { id: string; url: string } | null;
  production: Record<string, string>; arms: string[]; hook: string;
  publications: { at: string | null; workflow: string; republished_from: string | null; retitled_at: string | null;
    links: { platform: 'youtube' | 'facebook' | 'instagram'; id: string; url: string | null; observed: boolean }[] }[];
  observations: Observation[]; gaps: string[];
};
type Arm = { lane: string; name: string; n: number; median: number | null; low: number | null; high: number | null;
  like_rate: number | null; like_rate_n: number };
type Recommendation = { kind: 'observe' | 'collect' | 'gap'; title: string; evidence: string[]; uncertainty: string; action: string };
export type Experiments = {
  min_sample: number;
  trials: { id: string; planned_posts: number | null; published: number; updated: string | null;
    lanes: { lane: string; name: string; published: number }[];
    groups: { checkpoint: string; hours: number; platform: string; metric: string; arms: Arm[];
      comparable: boolean; leader: string | null; overlap: boolean }[];
    missed: { checkpoint: string; platform: string; count: number }[] }[];
  untracked: { id: string; published: number }[];
  recommendations: Recommendation[];
};
export type AssistantRecord = { id: string; scope: 'strategy' | 'trace' | 'shotsnip' | 'videos'; job: string | null; question: string;
  status: 'thinking' | 'answered' | 'failed'; asked_at: string | null; answered_at: string | null; reply: string | null;
  proposals: { index: number; name: string; summary: string | null; error: string | null;
    applied: { at: string; message: string } | null }[] };
export type PlanningData = {
  calendar: { today: string; first: string; last: string; histold_available: boolean; events: CalendarEvent[];
    shotsnip: { status: string; cadence_minutes: number | null; earliest: string | null;
      scan_minutes?: number | null; revision?: string | null; choices?: number[]; cadence_pending?: boolean } };
  topics: {job: string; date: string | null; title: string; format: string; state: string; url: string | null; trace?: Trace}[];
  histold_experiment: {hours: number; category: string; median: number; observed: number}[];
  experiments?: Experiments;
  assistant?: AssistantRecord[];
  strategy_controls?: import('./Owner').StrategyControls | null;
  ai_settings?: import('./Owner').AiSettings | null;
  bots?: import('./Bots').BotWorkspace | null;
  clips?: import('./Media').ClipData | null;
  earnings: {platform: string; label: string; value: string; currency: string | null; start: string | null;
    end: string | null; checked: string | null; detail: string}[];
  api_usage: {channel: 'histold' | 'shotsnip'; provider: string; attempts: number; failed: number;
    priced: number; cost: string | null}[];
  job_usage?: { available: boolean; unattributed: number; jobs: (JobCharge & JobLink & {
    title: string; state: string; url: string | null; at: string; last_seen: string | null; stage: string | null;
    breakdown: (JobCharge & { stage: string; provider: string })[];
  })[] };
};

const channelNames = { histold: 'Histold', shotsnip: 'Shotsnip' };
const shortDay = new Intl.DateTimeFormat('en-IN', { weekday: 'short', day: 'numeric', timeZone: 'UTC' });
const longDay = new Intl.DateTimeFormat('en-IN', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' });
const reportDate = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const localDay = new Intl.DateTimeFormat('en-CA', { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'Asia/Kolkata' });
const localClock = new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' });
const value = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 1 });

function offset(day: string, days: number) {
  const current = new Date(day);
  current.setUTCDate(current.getUTCDate() + days);
  return current.toISOString().slice(0,10);
}

function money(amount: string | null, currency: string | null, digits = 2) {
  if (amount === null) return 'Unavailable';
  if (!currency || !/^[A-Z]{3}$/.test(currency)) return amount + ' · currency unavailable';
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency, currencyDisplay: 'code',
    maximumFractionDigits: digits }).format(Number(amount));
}

function CadenceEditor({ data, busy, send, recent, time }: {
  data: PlanningData['calendar']['shotsnip']; busy: boolean; send?: Send; recent?: CommandEvent;
  time: (value: string | null) => string;
}) {
  const [draft, setDraft] = useState<{ minutes: number; revision: string } | null>(null);
  const [confirm, setConfirm] = useState(false);
  const allowed = !!send && !!data.revision && !!data.choices?.length;
  const changed = draft && draft.revision !== data.revision;
  const different = draft && (draft.minutes !== data.cadence_minutes || draft.minutes !== data.scan_minutes);
  return <div className="cadence-edit">
    {allowed && <div className="cadence-form"><label className="source-select">Scan and posting intervals
      <select value={draft?.minutes ?? (data.choices?.includes(data.cadence_minutes ?? -1) ? data.cadence_minutes! : '')}
        disabled={busy || confirm} onChange={event => setDraft({ minutes: Number(event.target.value), revision: data.revision! })}>
        <option value="" disabled>Choose an interval</option>
        {data.choices!.map(minutes => <option value={minutes} key={minutes}>{minutes / 60} h</option>)}
      </select></label>
      <button className="action" disabled={busy || !different || !!changed || confirm} onClick={() => setConfirm(true)}>Review change</button>
    </div>}
    {changed && <p className="analytics-info">Scheduler settings changed. <button className="action" onClick={() => {setDraft(null);setConfirm(false);}}>Use latest settings</button></p>}
    {data.status === 'On hold' && <p className="analytics-info">Cadence editing is locked while the current hold is active.</p>}
    {data.cadence_pending && <p className="analytics-info">The scheduler already has a pending cadence change.</p>}
    {confirm && allowed && different && !changed && <div className="cadence-confirm" role="group" aria-label="Confirm cadence change">
      <p>Set both intervals to {draft.minutes / 60} h? This updates the existing scheduler.</p>
      <button className="action" onClick={() => setConfirm(false)}>Back</button>
      <button className="action primary" disabled={busy} onClick={() => {
        send({action:'scheduler_cadence',channel:'shotsnip',minutes:draft.minutes,revision:draft.revision});
        setConfirm(false);
        setDraft(null);
      }}>Confirm cadence</button>
    </div>}
    {recent && <p className="analytics-info">{recent.message} · {time(recent.at)} IST</p>}
  </div>;
}

const weekdayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const monthDay = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', timeZone: 'UTC' });
const kindLabel = (event: CalendarEvent) => event.state === 'Published'
  ? (event.source === 'Manual clip' ? 'Manual clip' : 'Published') : 'Planned slot';

/** Two-week overview beside a day agenda: counts in the grid, full detail in the agenda. */
export function Calendar({ data }: { data: PlanningData['calendar']; time: (value: string | null) => string }) {
  const [selected, setSelected] = useState(data.today);
  const [filter, setFilter] = useState<'all' | 'histold' | 'shotsnip'>('all');
  const visible = data.events.filter(event => filter === 'all' || event.channel === filter)
    .sort((a, b) => a.at.localeCompare(b.at));
  const byDay = new Map<string, CalendarEvent[]>();
  for (const event of visible) {
    const key = localDay.format(new Date(event.at));
    byDay.set(key, [...(byDay.get(key) ?? []), event]);
  }
  const weekday = (day: string) => (new Date(day + 'T00:00:00Z').getUTCDay() + 6) % 7;
  const start = offset(data.first, -weekday(data.first));
  const end = offset(data.last, 6 - weekday(data.last));
  const days: string[] = [];
  for (let day = start; day <= end; day = offset(day, 1)) days.push(day);
  const inRange = (day: string) => day >= data.first && day <= data.last;
  const move = (step: number) => setSelected(day => {
    const next = offset(day, step);
    return inRange(next) ? next : day;
  });
  const chosen = byDay.get(selected) ?? [];
  const now = Date.now();
  const upcoming = visible.filter(event => Date.parse(event.at) > now);
  const next = upcoming[0];
  const todayItems = byDay.get(data.today) ?? [];
  const total = (channel: 'histold' | 'shotsnip') => visible.filter(event => event.channel === channel && event.state === 'Published').length;
  const nowIndex = selected === data.today ? chosen.findIndex(event => Date.parse(event.at) > now) : -1;

  return <div className="calendar-layout">
    <div className="calendar-summary">
      <div className="summary-tile accent"><span>Next up</span>
        <strong>{next ? localClock.format(new Date(next.at)) : '—'}</strong>
        <small>{next ? channelNames[next.channel] + ' · ' + next.title : 'Nothing planned'}</small></div>
      <div className="summary-tile"><span>Today</span><strong>{todayItems.length}</strong>
        <small>{todayItems.filter(e => e.state === 'Published').length} published · {todayItems.filter(e => e.state !== 'Published').length} planned</small></div>
      <div className="summary-tile"><span>Published in this period</span><strong>{total('histold') + total('shotsnip')}</strong>
        <small><i className="swatch histold"/>Histold {total('histold')} · <i className="swatch shotsnip"/>Shotsnip {total('shotsnip')}</small></div>
      <div className="summary-tile"><span>Planned ahead</span><strong>{upcoming.length}</strong>
        <small>Histold dispatch slots · Shotsnip posts {data.shotsnip.cadence_minutes ? 'every ' + data.shotsnip.cadence_minutes / 60 + ' h' : 'on schedule'}</small></div>
    </div>
    <div className="calendar-columns">
      <section className="panel calendar-panel">
        <div className="calendar-toolbar">
          <div><h2>{monthDay.format(new Date(start))} to {monthDay.format(new Date(end))}</h2>
            <p>Tap a day to see its posts · IST</p></div>
          <div className="filters" role="group" aria-label="Calendar channel">
            {(['all','histold','shotsnip'] as const).map(channel => <button key={channel} aria-pressed={channel === filter}
              onClick={() => setFilter(channel)}>{channel !== 'all' && <i className={'swatch ' + channel}/>}{channel === 'all' ? 'All' : channelNames[channel]}</button>)}
          </div>
        </div>
        <div className="calendar-grid" role="group" aria-label="Days" onKeyDown={event => {
          const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key];
          if (step) { event.preventDefault(); move(step); }
        }}>
          {weekdayNames.map(name => <div key={name} className="calendar-weekday" aria-hidden="true">{name}</div>)}
          {days.map(day => {
            const items = byDay.get(day) ?? [];
            const count = (channel: string) => items.filter(item => item.channel === channel).length;
            const planned = items.filter(item => item.state !== 'Published').length;
            return <button key={day} aria-pressed={day === selected} disabled={!inRange(day)} tabIndex={day === selected ? 0 : -1}
              ref={element => { if (element && day === selected && element.ownerDocument.activeElement?.closest('.calendar-grid')) element.focus(); }}
              className={'calendar-day' + (day === data.today ? ' today' : '') + (day < data.today ? ' past' : '')}
              onClick={() => setSelected(day)} aria-label={longDay.format(new Date(day)) + ': ' + items.length + ' posts' + (planned ? ', ' + planned + ' planned' : '')}>
              <span className="calendar-date">{Number(day.slice(8))}{day === data.today && <em>Today</em>}</span>
              {items.length > 0 && <span className="calendar-counts">
                {count('histold') > 0 && <span className="count histold" title="Histold">{count('histold')}</span>}
                {count('shotsnip') > 0 && <span className="count shotsnip" title="Shotsnip">{count('shotsnip')}</span>}
              </span>}
              {items.length > 0 && <span className="calendar-bar" aria-hidden="true">
                {count('histold') > 0 && <i className="histold" style={{ flexGrow: count('histold') }}/>}
                {count('shotsnip') > 0 && <i className="shotsnip" style={{ flexGrow: count('shotsnip') }}/>}
              </span>}
            </button>;
          })}
        </div>
        <div className="calendar-legend" aria-hidden="true">
          <span><i className="swatch histold"/>Histold</span><span><i className="swatch shotsnip"/>Shotsnip</span>
          <span>Arrow keys move between days</span></div>
        {!data.histold_available && <p className="chart-note">Histold planned slots are unavailable. Recorded posts are shown.</p>}
      </section>
      <section className="panel agenda-panel" aria-live="polite">
        <div className="agenda-head">
          <button className="action" aria-label="Previous day" disabled={!inRange(offset(selected, -1))} onClick={() => move(-1)}>‹</button>
          <div><h2>{longDay.format(new Date(selected))}</h2>
            <p>{chosen.length ? chosen.length + (chosen.length === 1 ? ' post' : ' posts') : 'No posts'}{selected === data.today ? ' · today' : ''}</p></div>
          <button className="action" aria-label="Next day" disabled={!inRange(offset(selected, 1))} onClick={() => move(1)}>›</button>
        </div>
        {selected !== data.today && <div className="agenda-today"><button className="action" onClick={() => setSelected(data.today)}>Back to today</button></div>}
        {chosen.length ? <ol className="agenda-list">{chosen.map((event, index) => <li key={event.at + event.channel + index}
          className={'agenda-item ' + event.channel + (Date.parse(event.at) < now ? ' done' : '') + (event.state === 'Published' ? '' : ' planned')}>
          {index === nowIndex && <span className="now-line" aria-label="Now"><b>Now</b></span>}
          <time dateTime={event.at}>{localClock.format(new Date(event.at))}</time>
          <span className="agenda-text"><strong>{event.title}</strong>
            <small><i className={'swatch ' + event.channel}/>{channelNames[event.channel]} · {kindLabel(event)}</small></span>
          {event.url ? <a className="icon-link" href={event.url} aria-label={'Watch ' + event.title} target="_blank" rel="noreferrer"><ArrowUpRight size={15}/></a> : <span />}
        </li>)}
          {nowIndex === -1 && selected === data.today && chosen.length > 0 && <li className="now-end"><span className="now-line"><b>Now</b></span></li>}
        </ol> : <p className="empty">Nothing published or planned on this day.</p>}
        <p className="chart-note"><a href="#/shotsnip/live">Change the Shotsnip cadence</a></p>
      </section>
    </div>
  </div>;
}

export function ShotsnipCadence({ data, time, send, busy, cadenceEvent }: { data: PlanningData['calendar']['shotsnip'];
  time: (value: string | null) => string; send?: Send; busy: boolean; cadenceEvent?: CommandEvent;
}) {
  return <section className="panel">
    <div className="panel-head"><div><h2>Posting cadence</h2><p>Clips are published when eligible under the existing scheduler.</p></div>
      <span className={'status ' + (data.status === 'Active' ? 'good' : 'warn')}>{data.status}</span></div>
    <div className="planning-detail">
      {data.cadence_minutes && <span>{data.cadence_minutes / 60} h between posting windows</span>}
      {data.scan_minutes != null && <span>Scans every {data.scan_minutes / 60} h</span>}
      {data.earliest && <span>Next eligible {time(data.earliest)} IST</span>}
    </div>
    <CadenceEditor data={data} busy={busy} send={send} recent={cadenceEvent} time={time} />
  </section>;
}

const platformLabel = { youtube: 'YouTube', facebook: 'Facebook', instagram: 'Instagram' };

export function TraceView({ trace, time, usage, openUsage, ask }: { trace: Trace; time: (value: string | null) => string;
  usage: boolean; openUsage: () => void; ask?: () => void }) {
  const production = Object.entries(trace.production);
  return <div className="trace">
    <ol className="trace-steps">
      <li><h4>Idea</h4>
        {trace.idea ? <>
          <p>{trace.idea.premise || 'Premise unavailable'}</p>
          {trace.idea.selection_reason && <p className="trace-muted">Why chosen: {trace.idea.selection_reason}</p>}
          {trace.idea.rejected.length > 0 && <details><summary>{trace.idea.rejected.length} alternatives excluded</summary>
            <ul>{trace.idea.rejected.map((row, index) => <li key={index}><strong>{row.premise}</strong><span>{row.reason}</span></li>)}</ul></details>}
        </> : <p className="trace-muted">Not recorded for this job.</p>}
      </li>
      <li><h4>Reservation and run</h4>
        <p>Reserved {trace.reservation.date || 'date unavailable'}{trace.reservation.slot ? ' · ' + trace.reservation.slot + ' slot' : ''}</p>
        {trace.run ? <a className="action" href={trace.run.url} target="_blank" rel="noreferrer">Workflow run {trace.run.id} <ArrowUpRight size={14}/></a>
          : <p className="trace-muted">No workflow run linked.</p>}
      </li>
      <li><h4>Production</h4>
        {production.length ? <dl className="trace-facts">{production.map(([key, item]) => <div key={key}><dt>{key}</dt><dd>{item}</dd></div>)}</dl>
          : <p className="trace-muted">No production details recorded.</p>}
        {trace.hook && <p className="trace-muted">Hook: “{trace.hook}”</p>}
        {trace.arms.length > 0 && <p className="trace-muted">Experiment arms: {trace.arms.join(' · ')}</p>}
      </li>
      <li><h4>Publication</h4>
        {trace.publications.length ? trace.publications.map((row, index) => <div key={index} className="trace-publication">
          <p>{row.at ? time(row.at) + ' IST' : 'Time unavailable'} · {row.workflow || 'workflow unknown'}
            {row.republished_from ? ' · republished from ' + row.republished_from : ''}{row.retitled_at ? ' · retitled' : ''}</p>
          <p className="trace-links">{row.links.map(link => link.url
            ? <a key={link.platform + link.id} href={link.url} target="_blank" rel="noreferrer">{platformLabel[link.platform]} <ArrowUpRight size={12}/></a>
            : <span key={link.platform + link.id}>{platformLabel[link.platform]} {link.id}</span>)}</p>
        </div>) : <p className="trace-muted">Not published.</p>}
      </li>
      <li><h4>Observations</h4>
        {trace.observations.length ? <table><thead><tr><th scope="col">Platform</th><th scope="col">Value</th><th scope="col">Age</th></tr></thead>
          <tbody>{trace.observations.map(row => <tr key={row.platform + row.media}>
            <th scope="row">{row.platform}<small>{row.metric}</small></th>
            <td>{row.value != null ? value.format(row.value) : 'Unavailable'}{row.likes != null ? <small>{row.likes} likes</small> : null}</td>
            <td>{row.age_hours != null ? row.age_hours + ' h' : '—'}<small>{row.observed_at ? time(row.observed_at) : ''}</small></td></tr>)}</tbody></table>
          : <p className="trace-muted">No observations yet.</p>}
        <p className="trace-muted">Cumulative snapshots. Compare only the same platform and metric at a similar age.</p>
      </li>
      <li><h4>Cost</h4>
        {usage ? <button className="action" onClick={openUsage}>Open job usage</button>
          : <p className="trace-muted">No usage records for this job in the last 30 days.</p>}
      </li>
    </ol>
    {trace.gaps.length > 0 && <div className="trace-gaps" role="note"><strong>Provenance gaps</strong>
      <ul>{trace.gaps.map(gap => <li key={gap}>{gap}</li>)}</ul></div>}
    {ask && <button className="action" onClick={ask}>Ask about this topic</button>}
  </div>;
}

export function ExperimentPanel({ data, time }: { data: Experiments; time: (value: string | null) => string }) {
  const [choice, setChoice] = useState(0);
  const trial = data.trials[0];
  if (!trial) return <section className="panel"><div className="panel-head"><div><h2>Experiments</h2>
    <p>No matched-age trial observations are recorded.</p></div></div></section>;
  const group = trial.groups[Math.min(choice, trial.groups.length - 1)];
  const rate = new Intl.NumberFormat('en-IN', { style: 'percent', maximumFractionDigits: 1 });
  return <section className="panel experiment-panel">
    <div className="panel-head"><div><h2>Experiment {trial.id}</h2>
      <p>{trial.published} of {trial.planned_posts ?? '?'} planned posts · first observation in each age window
        {trial.updated ? ' · recorded ' + time(trial.updated) + ' IST' : ''}</p></div></div>
    {trial.groups.length > 0 ? <>
      <div className="filters" role="group" aria-label="Comparison">
        {trial.groups.map((item, index) => <button key={item.checkpoint + item.platform} aria-pressed={item === group}
          onClick={() => setChoice(index)}>{item.hours} h · {item.platform}</button>)}
      </div>
      <table><thead><tr><th scope="col">Lane</th><th scope="col">Posts</th><th scope="col">Median</th><th scope="col">Range</th></tr></thead>
        <tbody>{group.arms.map(arm => <tr key={arm.lane}>
          <th scope="row">{arm.name}{arm.lane === group.leader ? <small>Highest median</small> : null}</th>
          <td>{arm.n}</td>
          <td>{arm.median != null ? value.format(arm.median) : '—'}{arm.like_rate != null ? <small>{rate.format(arm.like_rate)} likes</small> : null}</td>
          <td>{arm.low != null ? value.format(arm.low) + '–' + value.format(arm.high!) : '—'}</td></tr>)}</tbody></table>
      <p className="chart-note">{group.metric} at {group.hours} h. {group.comparable
        ? (group.overlap ? 'Every lane has enough posts, but the ranges overlap.' : 'Every lane has enough posts and the leader’s range is separate.')
        : `Not comparable yet: each lane needs at least ${data.min_sample} posts observed in this window.`}</p>
    </> : <p className="empty">No matched-age observations yet.</p>}
  </section>;
}

export function Recommendations({ items }: { items: Recommendation[] }) {
  const labels = { observe: 'Observation', collect: 'Keep collecting', gap: 'Evidence gap' };
  return <section className="panel recommendations">
    <div className="panel-head"><div><h2>Recommendations</h2><p>Advisory. Each one states its evidence and how uncertain it is.</p></div></div>
    {items.length ? items.map(item => <article key={item.title} className="recommendation">
      <span className={'status ' + (item.kind === 'gap' ? 'warn' : 'neutral')}>{labels[item.kind]}</span>
      <h3>{item.title}</h3>
      <ul>{item.evidence.map(line => <li key={line}>{line}</li>)}</ul>
      <p><strong>Uncertainty:</strong> {item.uncertainty}</p>
      <p className="trace-muted">{item.action}</p>
    </article>) : <p className="empty">No recommendations yet.</p>}
  </section>;
}

export function AssistantPanel({ records, topics, focus, setFocus, send, busy, recent, time }: {
  records: AssistantRecord[]; topics: PlanningData['topics']; focus: string | null; setFocus: (job: string | null) => void;
  send?: Send; busy: boolean; recent?: CommandEvent; time: (value: string | null) => string;
}) {
  const [question, setQuestion] = useState('');
  const [confirm, setConfirm] = useState<string | null>(null);
  const [videos, setVideos] = useState(false);
  const topic = topics.find(row => row.job === focus);
  const scopeLabel = (record: AssistantRecord) => record.scope === 'trace' ? 'Topic ' + record.job
    : record.scope === 'videos' ? 'Video performance' : 'Recommendations and experiments';
  const thinking = records.some(row => row.status === 'thinking');
  return <section className="panel assistant-panel" aria-labelledby="assistant-heading">
    <div className="panel-head"><div><h2 id="assistant-heading">Ask the Histold assistant</h2>
      <p>It sees only the evidence you choose. Proposals change nothing until you apply them.</p></div></div>
    {send ? <form className="assistant-form" onSubmit={event => {
      event.preventDefault();
      if (!question.trim() || busy) return;
      send(focus ? {action:'assistant_ask',channel:'histold',scope:'trace',job:focus,question:question.trim()}
        : {action:'assistant_ask',channel:'histold',scope:videos ? 'videos' : 'strategy',question:question.trim()});
      setQuestion('');
    }}>
      <div className="filters" role="group" aria-label="Evidence">
        <button type="button" aria-pressed={!focus && !videos} onClick={() => { setFocus(null); setVideos(false); }}>Recommendations and experiments</button>
        <button type="button" aria-pressed={!focus && videos} onClick={() => { setFocus(null); setVideos(true); }}>Video performance</button>
        {topic && <button type="button" aria-pressed>Topic: {topic.title}</button>}
      </div>
      {!topic && <p className="trace-muted">To ask about one topic, open it under Topics and choose Ask the assistant.</p>}
      <label className="planning-search">Question
        <textarea value={question} maxLength={600} rows={3} onChange={event => setQuestion(event.target.value)}
          placeholder={topic ? 'Why did this topic underperform on YouTube?' : videos ? 'Why did my recent videos start slowly?' : 'Which lane is ahead, and how sure can we be?'} /></label>
      <button className="action primary" type="submit" disabled={busy || !question.trim() || thinking}>Ask</button>
      {thinking && <p className="trace-muted" role="status">The assistant is answering. This can take a few minutes.</p>}
    </form> : <p className="chart-note">Questions are available when Studio HQ controls are enabled.</p>}
    {recent && <p className="analytics-info" role="status">{recent.message} · {time(recent.at)} IST</p>}
    {records.map(record => <article key={record.id} className="assistant-record">
      <p className="assistant-question"><strong>{record.question}</strong>
        <small>{scopeLabel(record)} · {time(record.asked_at)} IST</small></p>
      {record.status === 'thinking' ? <p className="trace-muted">Waiting for the answer…</p>
        : <p className={record.status === 'failed' ? 'trace-muted' : 'assistant-reply'}>{record.reply}</p>}
      {record.proposals.map(item => {
        const key = record.id + ':' + item.index;
        return <div key={key} className="assistant-proposal">
          <span>{item.error ? 'Not valid: ' + item.error : item.summary || item.name}</span>
          {item.applied ? <span className="status good">Applied</span>
            : !item.error && send && (confirm === key
              ? <span className="tool-links"><button className="action" onClick={() => setConfirm(null)}>Back</button>
                <button className="action primary" disabled={busy} onClick={() => {
                  send({action:'assistant_apply',channel:'histold',answer_id:record.id,index:item.index}); setConfirm(null);
                }}>Confirm apply</button></span>
              : <button className="action" disabled={busy} onClick={() => setConfirm(key)}>Review and apply</button>)}
          {item.applied && <small>{item.applied.message}</small>}
          {confirm === key && <small>This changes the live Histold settings through the original dashboard, which checks it again.</small>}
        </div>;
      })}
    </article>)}
  </section>;
}

export function TopicList({ data }: { data: PlanningData }) {
  const [search, setSearch] = useState('');
  const [limit, setLimit] = useState(12);
  const query = search.trim().toLowerCase();
  const topics = data.topics.filter(row => row.title.toLowerCase().includes(query) || row.job.includes(query));
  return <section className="panel">
    <div className="panel-head"><div><h2>Topics</h2><p>Recent ideas. Open one to see its full journey from idea to results.</p></div></div>
    <label className="planning-search">Find a topic<input type="search" value={search}
      onChange={event => { setSearch(event.target.value); setLimit(12); }} placeholder="Search title or job ID" /></label>
    {topics.length ? <ul className="topic-list">{topics.slice(0, limit).map(row => <li key={row.job}>
      <a href={'#/histold/topics/' + row.job}>
        <span><strong>{row.title}</strong><small>{row.format} · {row.date || 'Date unavailable'}{row.trace?.gaps.length ? ' · ' + row.trace.gaps.length + ' gaps' : ''}</small></span>
        <span className={'status ' + (row.state === 'Published' ? 'good' : 'neutral')}>{row.state}</span>
      </a></li>)}</ul> : <p className="empty">No matching topics.</p>}
    {topics.length > limit && <div className="panel-foot"><button onClick={() => setLimit(value => value + 12)}>Show more topics</button></div>}
  </section>;
}

export function TopicPage({ data, job, time, send }: { data: PlanningData; job: string; time: (value: string | null) => string; send?: Send }) {
  const topic = data.topics.find(row => row.job === job);
  if (!topic) return <section className="panel empty">This topic is no longer in the recent history. <a href="#/histold/topics">Back to topics</a></section>;
  const usage = data.job_usage?.jobs.find(row => row.channel === 'histold' && row.id === job);
  return <div className="planning-body">
    <a className="back-link" href="#/histold/topics">← All topics</a>
    <section className="panel topic-page">
      <div className="panel-head"><div><h2>{topic.title}</h2><p>{topic.format} · {topic.date || 'Date unavailable'} · {topic.job}</p></div>
        <span className={'status ' + (topic.state === 'Published' ? 'good' : 'neutral')}>{topic.state}</span></div>
      <div className="topic-actions">
        {topic.url && <a className="action" href={topic.url} target="_blank" rel="noreferrer">Watch <ArrowUpRight size={15}/></a>}
        {usage && <a className="action" href={'#/plan/jobs/histold/' + job}>Job usage</a>}
        {send && <a className="action" href={'#/histold/assistant/' + job}>Ask the assistant</a>}
      </div>
      {topic.trace ? <TraceView trace={topic.trace} time={time} usage={!!usage && usage.attempts > 0}
        openUsage={() => { window.location.hash = '#/plan/jobs/histold/' + job; }} />
        : <p className="empty">No trace is recorded for this topic.</p>}
    </section>
  </div>;
}

export function SixReelExperiment({ data }: { data: PlanningData }) {
  const [age, setAge] = useState(24);
  const ages = [...new Set(data.histold_experiment.map(item => item.hours))];
  const chosen = ages.includes(age) ? age : ages[0];
  return <section className="panel">
    <div className="panel-head"><div><h2>Six-Reel experiment</h2><p>YouTube observations within the experiment’s age window.</p></div></div>
    {ages.length > 0 && <div className="filters" role="group" aria-label="Observation age">
      {ages.map(hour => <button key={hour} aria-pressed={hour === chosen} onClick={() => setAge(hour)}>{hour}h</button>)}</div>}
    {data.histold_experiment.filter(item => item.hours === chosen).map(item =>
      <div className="topic-row" key={item.category}><div><strong>{item.category}</strong>
        <small>{item.observed} observed {item.observed === 1 ? 'video' : 'videos'}</small></div><span>{value.format(item.median)} median views</span></div>)}
    <p className="chart-note">{ages.length ? 'Exploratory sample. Compare categories only at the same observation age.' : 'No matching YouTube observations are available.'}</p>
  </section>;
}

export function Money({ data, time }: { data: PlanningData; time: (value: string | null) => string }) {
  const formatDay = (day: string) => reportDate.format(new Date(day.slice(0,10)));
  return <div className="planning-body">
    <section>
      <div className="planning-subhead"><h2>Histold earnings</h2>
        <a className="action" href="/?channel=histold#/earnings" target="_blank" rel="noreferrer">Full earnings report <ArrowUpRight size={15}/></a></div>
      <div className="money-grid">{data.earnings.map(row => <article className="panel earnings-card" key={row.label}>
        <span className="earnings-platform">{row.platform}</span><h3>{row.label}</h3>
        <strong>{money(row.value,row.currency)}</strong>
        {row.end && <small>{row.start ? formatDay(row.start) + ' – ' : 'Period end '}{formatDay(row.end)}</small>}
        <p>{row.detail}</p>{row.checked && <small>Checked {time(row.checked)} IST</small>}
      </article>)}</div>
      {!data.earnings.length && <p className="empty panel">No earnings reports are available.</p>}
      <p className="chart-note">Figures are platform estimates. Shotsnip earnings are unavailable.</p>
    </section>
    <section className="panel experiment-panel">
      <div className="panel-head planning-head"><div><h2>API costs and usage</h2><p>Last 30 days of locally recorded API attempts.</p></div>
        <a className="action" href="#/plan/jobs">View by job</a></div>
      {data.api_usage.length ? <table><thead><tr><th scope="col">Channel and provider</th><th scope="col">Attempts</th>
        <th scope="col">Failed</th><th scope="col">Recorded cost</th></tr></thead>
        <tbody>{data.api_usage.map(row => <tr key={row.channel + row.provider}><th scope="row">{channelNames[row.channel]} · {row.provider}</th>
          <td>{row.attempts}</td><td>{row.failed}</td><td>{money(row.cost,'USD',4)}<br/>
          <small>{row.priced} of {row.attempts} priced</small></td></tr>)}</tbody></table>
        : <p className="empty">No API attempts are recorded in this window. Cost is unavailable.</p>}
      <p className="chart-note">Totals include only attempts with a recorded USD charge and a pricing basis.</p>
    </section>
  </div>;
}

export function JobUsage({ data, focus, time }: {
  data: NonNullable<PlanningData['job_usage']>; focus: JobLink | null; time: (value: string | null) => string;
}) {
  const [channel, setChannel] = useState<'all' | JobLink['channel']>(focus?.channel ?? 'all');
  const [search, setSearch] = useState(focus?.id ?? '');
  const [limit, setLimit] = useState(12);
  const query = search.trim().toLowerCase();
  const filtered = data.jobs.filter(job => (channel === 'all' || job.channel === channel) &&
    (job.title.toLowerCase().includes(query) || job.id.toLowerCase().includes(query)));

  return <section className="panel job-usage">
    <div className="panel-head"><div><h2>Job usage and costs</h2><p>Production records and local observations from the last 30 days.</p></div></div>
    <div className="filters" role="group" aria-label="Job channel">
      {(['all','histold','shotsnip'] as const).map(item => <button key={item} aria-pressed={channel === item}
        onClick={() => {setChannel(item);setLimit(12);}}>{item === 'all' ? 'All channels' : channelNames[item]}</button>)}
    </div>
    <label className="planning-search">Find a job<input type="search" value={search} placeholder="Search title or job ID"
      onChange={event => {setSearch(event.target.value);setLimit(12);}} /></label>
    {!data.available && <p className="chart-note">The local observation index is unavailable.</p>}
    {data.unattributed > 0 && <p className="chart-note">{data.unattributed} API attempts have no job ID and appear only in provider totals.</p>}
    {filtered.length === 0 && <p className="empty">No matching jobs in this window.</p>}
    {filtered.slice(0,limit).map(job => <details key={job.channel + job.id} className="job-usage-row" open={focus?.id === job.id && focus.channel === job.channel}>
      <summary>
        <span><strong>{job.title}</strong><small>{channelNames[job.channel]} · {job.state} · {job.id}</small></span>
        <span className="job-usage-cost">{money(job.cost,'USD',4)}<small>{job.attempts ? job.priced + ' of ' + job.attempts + ' attempts priced' : 'No recorded API attempts'}</small></span>
      </summary>
      <div className="job-usage-detail">
        {job.last_seen && <p>Last worker observation {time(job.last_seen)} IST{job.stage ? ' · ' + job.stage.replaceAll('_',' ') : ''}</p>}
        {job.attempts > 0 && <ul>{job.breakdown.map(item => <li key={item.stage + '|' + item.provider}>
          <strong>{item.stage.replaceAll('_',' ')} · {item.provider}</strong>
          <span>{item.attempts} attempts · {item.failed} failed · {money(item.cost,'USD',4)}</span></li>)}</ul>}
        {!job.attempts && <p>There are no local API attempts recorded for this job in the reporting window.</p>}
        {job.url && <a className="action" href={job.url} target="_blank" rel="noreferrer">Open publication or draft <ArrowUpRight size={15}/></a>}
      </div>
    </details>)}
    {filtered.length > limit && <div className="panel-foot"><button onClick={() => setLimit(value => value + 12)}>Show more jobs</button></div>}
    <p className="chart-note">Hosted workflow usage appears only when its telemetry is available to this host. Unpriced attempts remain unavailable.</p>
  </section>;
}
