import { useState } from 'react';
import { ArrowLeft, ArrowRight, ArrowUpRight, CalendarDays, FlaskConical, ListChecks, WalletCards } from 'lucide-react';
import type { CommandEvent, Send } from './Controls';

export type PlanningView = 'calendar' | 'strategy' | 'money' | 'jobs';
export type JobLink = { channel: 'histold' | 'shotsnip'; id: string };
type JobCharge = { attempts: number; failed: number; priced: number; cost: string | null };
type CalendarEvent = { at: string; channel: 'histold' | 'shotsnip'; title: string; state: string; source: string; url: string | null };
export type PlanningData = {
  calendar: { today: string; first: string; last: string; histold_available: boolean; events: CalendarEvent[];
    shotsnip: { status: string; cadence_minutes: number | null; earliest: string | null;
      scan_minutes?: number | null; revision?: string | null; choices?: number[]; cadence_pending?: boolean } };
  topics: {job: string; date: string | null; title: string; format: string; state: string; url: string | null}[];
  histold_experiment: {hours: number; category: string; median: number; observed: number}[];
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

function Calendar({ data, time, send, busy, cadenceEvent }: { data: PlanningData['calendar'];
  time: (value: string | null) => string; send?: Send; busy: boolean; cadenceEvent?: CommandEvent;
}) {
  const [selected, setSelected] = useState(data.today);
  const [filter, setFilter] = useState<'all' | 'histold' | 'shotsnip'>('all');
  const events = data.events.filter(event => localDay.format(new Date(event.at)) === selected &&
    (filter === 'all' || event.channel === filter));
  const start = selected < data.today ? data.first : data.today;
  const dates = Array.from({length: selected < data.today ? 6 : 7}, (_, i) => offset(start, i));
  return <div className="planning-body">
    <section className="panel">
      <div className="panel-head planning-head"><div><h2>Publishing calendar</h2><p>Dispatch slots and recorded posts · IST</p></div>
        <div className="calendar-paging">
          <button className="action" aria-label="Show past days" disabled={selected < data.today} onClick={() => setSelected(offset(data.today,-1))}><ArrowLeft size={16}/></button>
          <button className="action" onClick={() => setSelected(data.today)}>Today</button>
          <button className="action" aria-label="Show upcoming days" disabled={selected >= data.today} onClick={() => setSelected(data.today)}><ArrowRight size={16}/></button>
        </div>
      </div>
      <div className="calendar-days" role="group" aria-label="Select day">
        {dates.map(day => <button className="action" key={day} aria-pressed={day === selected}
          onClick={() => setSelected(day)}>{shortDay.format(new Date(day))}</button>)}
      </div>
      <div className="calendar-filter">
        <strong>{longDay.format(new Date(selected))}</strong>
        <div className="filters source-filters" role="group" aria-label="Calendar channel">
          {(['all','histold','shotsnip'] as const).map(channel => <button key={channel} aria-pressed={channel === filter}
            onClick={() => setFilter(channel)}>{channel === 'all' ? 'All' : channelNames[channel]}</button>)}
        </div>
      </div>
      {!data.histold_available && <p className="chart-note">Histold’s dispatch preview is unavailable. Recorded posts are shown below.</p>}
      {!events.length ? <p className="empty">No recorded posts or planned dispatches for this selection.</p>
        : events.map((event, index) => <article key={event.at + event.channel + index} className="agenda-row">
          <time dateTime={event.at}>{localClock.format(new Date(event.at))}</time>
          <div><strong>{event.title}</strong><small>{channelNames[event.channel]} · {event.source}</small></div>
          <span className={'status ' + (event.state === 'Published' ? 'good' : 'neutral')}>{event.state}</span>
          {event.url && <a className="rank-link" href={event.url} aria-label={'Watch ' + event.title} target="_blank" rel="noreferrer"><ArrowUpRight size={16}/></a>}
        </article>)}
    </section>
    <section className="panel">
      <div className="panel-head"><div><h2>Shotsnip posting cadence</h2><p>Clips are published when eligible under the existing scheduler.</p></div>
        <span className={'status ' + (data.shotsnip.status === 'Active' ? 'good' : 'warn')}>{data.shotsnip.status}</span></div>
      <div className="planning-detail">
        {data.shotsnip.cadence_minutes && <span>{data.shotsnip.cadence_minutes} minutes between posting windows</span>}
        {data.shotsnip.scan_minutes != null && <span>Scan every {data.shotsnip.scan_minutes} minutes</span>}
        {data.shotsnip.earliest && <span>Cadence threshold {time(data.shotsnip.earliest)} IST</span>}
        <a className="action" href="/?channel=shotsnip#jobs" target="_blank" rel="noreferrer">Open work queue <ArrowUpRight size={15}/></a>
      </div>
      <CadenceEditor data={data.shotsnip} busy={busy} send={send} recent={cadenceEvent} time={time} />
    </section>
  </div>;
}

function Strategy({ data, openPacing, openJob }: { data: PlanningData; openPacing: () => void; openJob: (job: JobLink) => void }) {
  const [search, setSearch] = useState('');
  const [limit, setLimit] = useState(8);
  const [age, setAge] = useState(24);
  const ages = [...new Set(data.histold_experiment.map(item => item.hours))];
  const topics = data.topics.filter(row => row.title.toLowerCase().includes(search.trim().toLowerCase()) || row.job.includes(search.trim()));
  return <div className="planning-body">
    <div className="planning-split">
      <section className="panel">
        <div className="panel-head planning-head"><div><h2>Histold topic history</h2><p>Reservations linked to drafts and published videos by job ID.</p></div>
          <a className="action" href="/?channel=histold#/strategy" target="_blank" rel="noreferrer">Strategy tools <ArrowUpRight size={15}/></a></div>
        <label className="planning-search">Find a topic<input type="search" value={search}
          onChange={event => { setSearch(event.target.value); setLimit(8); }} placeholder="Search title or job ID" /></label>
        {topics.length ? topics.slice(0,limit).map(row => <article className="topic-row" key={row.job}>
          <div><strong>{row.title}</strong><small>{row.format} · {row.date || 'Date unavailable'} · {row.job}</small></div>
          <span className={'status ' + (row.state === 'Published' ? 'good' : 'neutral')}>{row.state}</span>
          <div className="topic-links">
            {data.job_usage?.jobs.some(job => job.channel === 'histold' && job.id === row.job) &&
              <button className="action" onClick={() => openJob({channel:'histold',id:row.job})}>Job usage</button>}
            {row.url && <a className="rank-link" href={row.url} aria-label={'Open ' + row.title} target="_blank" rel="noreferrer"><ArrowUpRight size={16}/></a>}
          </div>
        </article>) : <p className="empty">No matching topic records.</p>}
        {topics.length > limit && <div className="panel-foot"><button onClick={() => setLimit(value => value + 8)}>Show more topics</button></div>}
      </section>
      <div className="planning-body">
        <section className="panel">
          <div className="panel-head"><div><h2>Histold six-Reel experiment</h2><p>YouTube observations within the experiment’s age window.</p></div></div>
          {ages.length > 0 && <div className="filters" role="group" aria-label="Observation age">
            {ages.map(hour => <button key={hour} aria-pressed={hour === (ages.includes(age) ? age : ages[0])}
              onClick={() => setAge(hour)}>{hour}h</button>)}</div>}
          {data.histold_experiment.filter(item => item.hours === (ages.includes(age) ? age : ages[0])).map(item =>
            <div className="topic-row" key={item.category}><div><strong>{item.category}</strong>
              <small>{item.observed} observed {item.observed === 1 ? 'video' : 'videos'}</small></div><span>{value.format(item.median)} median views</span></div>)}
          <p className="chart-note">{ages.length ? 'Exploratory sample. Compare categories only at the same observation age.' : 'No matching YouTube observations are available.'}</p>
        </section>
        <section className="panel"><div className="panel-head"><div><h2>Shotsnip pacing</h2><p>Compare 20-second and 30-second clips at matched ages.</p></div></div>
          <div className="planning-detail"><button className="action" onClick={openPacing}>Open pacing experiment <ArrowUpRight size={15}/></button></div></section>
      </div>
    </div>
  </div>;
}

function Money({ data, time, openJobs }: { data: PlanningData; time: (value: string | null) => string; openJobs: () => void }) {
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
        <button className="action" onClick={openJobs}>View by job <ArrowUpRight size={15}/></button></div>
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

function JobUsage({ data, focus, time }: {
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

export function Planning({ data, view, choose, time, openPacing, send, busy, cadenceEvent, openJob, jobFocus }: {data: PlanningData; view: PlanningView;
  choose: (view: PlanningView) => void; time: (value: string | null) => string; openPacing: () => void;
  send?: Send; busy: boolean; cadenceEvent?: CommandEvent; openJob: (job: JobLink) => void; jobFocus: JobLink | null }) {
  const tabs = [{id:'calendar',label:'Calendar',icon:CalendarDays}, {id:'strategy',label:'Strategy',icon:FlaskConical},
    {id:'money',label:'Earnings & costs',icon:WalletCards}, {id:'jobs',label:'Job usage',icon:ListChecks}] as const;
  return <div className="planning-body">
    <div className="filters source-filters planning-tabs" role="group" aria-label="Planning sections">
      {tabs.map(tab => <button key={tab.id} aria-pressed={view === tab.id} onClick={() => choose(tab.id)}><tab.icon size={16}/>{tab.label}</button>)}
    </div>
    {view === 'calendar' ? <Calendar data={data.calendar} time={time} send={send} busy={busy} cadenceEvent={cadenceEvent} />
      : view === 'strategy' ? <Strategy data={data} openPacing={openPacing} openJob={openJob} />
      : view === 'money' ? <Money data={data} time={time} openJobs={() => choose('jobs')} />
      : data.job_usage ? <JobUsage key={jobFocus ? jobFocus.channel + jobFocus.id : 'all'} data={data.job_usage} focus={jobFocus} time={time} />
      : <p className="panel empty">Waiting for the first job usage snapshot.</p>}
  </div>;
}
