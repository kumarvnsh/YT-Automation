import { useState } from 'react';
import { ArrowUpRight, BarChart3, Clapperboard, Eye, Globe2 } from 'lucide-react';

export const studioChannels = ['histold', 'shotsnip'] as const;
export type StudioChannel = typeof studioChannels[number];
export const channelNames = { histold: 'Histold', shotsnip: 'Shotsnip' };
export type Metric = { label: string; value: number | null; detail: string };
export type Series = {
  id: string; platform: string; label: string; updated: string | null; chart_label: string; note: string;
  metrics: Metric[];
  points: { day: string; value: number | null }[];
  videos: { id: string; title: string; value: number | null; published: string | null; likes: number | null;
    watch_seconds: number | null; url: string | null }[];
};
export type StudioAnalytics = Record<StudioChannel, Series[]>;
export type Pacing = { published: number; target: number; updated: string | null; groups: {
  platform: string; measure: string; variant: string; hours: number;
  median: number | null; eligible: number | null; observed: number | null;
}[] };

const format = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 1 });
const compact = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 });
const dateFormat = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', timeZone: 'UTC' });
const sourceName: Record<string, string> = {
  'youtube-daily': 'YouTube · daily', youtube: 'YouTube · lifetime',
  'facebook-daily': 'Facebook · daily', facebook: 'Facebook · Reel plays',
  'facebook-reels': 'Facebook · Reel plays', 'facebook-legacy': 'Facebook · legacy views',
  instagram: 'Instagram', publishing: 'Publishing',
};

function value(number: number | null) {
  return number === null ? '—' : format.format(number);
}

function dateLabel(day: string) {
  return dateFormat.format(new Date(day));
}

export function ChannelCards({ data, open }: { data: StudioAnalytics; open: (channel: StudioChannel) => void }) {
  const featured: Record<StudioChannel, string> = { histold: 'facebook-reels', shotsnip: 'youtube-daily' };
  const icons = { histold: Globe2, shotsnip: Clapperboard };
  const shortLabels = { histold: 'Lifetime Facebook Reel plays', shotsnip: 'Reported YouTube views' };
  return <section className="channel-cards" aria-label="Channel performance">
    {studioChannels.map(channel => {
      const source = data[channel].find(item => item.id === featured[channel]);
      const main = source?.metrics[0];
      const Icon = icons[channel];
      return <button type="button" className={`channel-card ${channel}`} key={channel} onClick={() => open(channel)}>
        <span className="card-top"><span className="performance-icon"><Icon size={18} aria-hidden="true" /></span>
          <strong>{channelNames[channel]}</strong><ArrowUpRight className="card-arrow" size={18} aria-hidden="true" /></span>
        <b>{value(main?.value ?? null)}</b>
        <span className="card-metric">{shortLabels[channel]}</span>
        <small>{main?.detail || 'Data unavailable'}</small>
      </button>;
    })}
  </section>;
}

export function ReportingChart({ series }: { series: Series }) {
  const [active, setActive] = useState<number | null>(null);
  if (!series.points.length) return null;
  const end = series.points.length - 1;
  const peak = Math.max(1, ...series.points.map(point => point.value ?? 0));
  const step = 10 ** Math.floor(Math.log10(peak));
  const top = Math.ceil(peak / step) * step;
  const current = active === null ? null : series.points[Math.min(active, end)];
  const tickValues = [top, top * .75, top * .5, top * .25, 0];
  return <section className="panel trend-panel" aria-label={series.chart_label}>
    <div className="panel-head chart-heading">
      <div><h2>{series.chart_label}</h2><p>{dateLabel(series.points[0].day)} – {dateLabel(series.points[end].day)}</p></div>
      <div className="chart-value" aria-live="polite">
        {current ? <>{dateLabel(current.day)} <strong>{current.value === null ? 'No report' : value(current.value)}</strong></>
          : <><Eye size={15} aria-hidden="true" /> {series.label}</>}
      </div>
    </div>
    <div className="chart-shell">
      <div className="chart-axis" aria-hidden="true">{tickValues.map(mark => <span key={mark}>{compact.format(mark)}</span>)}</div>
      <div className="chart-main">
        <div className="chart-guides" aria-hidden="true">{tickValues.map(mark => <span key={mark} />)}</div>
        <div className="chart-bars" aria-hidden="true" onPointerLeave={() => setActive(null)}>
          {series.points.map((point, index) => <div key={point.day} className={active === index ? 'chart-bar selected' : 'chart-bar'}
            onPointerEnter={() => setActive(index)} onPointerDown={() => setActive(index)}>
            {point.value !== null && <div className="chart-fill" style={{ height: Math.max(point.value / top * 100, .65) + '%' }} />}
          </div>)}
        </div>
        <input className="chart-input" type="range" min={0} max={end} value={active ?? end}
          aria-label="Explore the daily values" aria-valuetext={current ? dateLabel(current.day) + ' ' + (current.value ?? 'no report') : dateLabel(series.points[end].day)}
          onChange={event => setActive(Number(event.target.value))} onFocus={() => setActive(end)} onBlur={() => setActive(null)} />
      </div>
      <div className="chart-dates" aria-hidden="true"><span>{dateLabel(series.points[0].day)}</span>
        <span>{dateLabel(series.points[Math.floor(end / 2)].day)}</span><span>{dateLabel(series.points[end].day)}</span></div>
    </div>
    <p className="chart-note">{series.note}</p>
  </section>;
}

export function VideoRanking({ series, limit = 8 }: { series: Series; limit?: number }) {
  const items = series.videos.slice().sort((left, right) => (right.value ?? -1) - (left.value ?? -1)).slice(0, limit);
  if (!items.length) return null;
  return <section className="panel ranking">
    <div className="panel-head"><div><h2>Top videos</h2><p>{series.platform} · {series.label.toLowerCase()}{series.points.length ? ' in this report' : ' since publication'}</p></div></div>
    {items.map((item, index) => <article className="rank-row" key={item.id}>
      <span className="rank-index">{String(index + 1).padStart(2, '0')}</span>
      <div className="rank-title">
        <strong>{item.title}</strong><small>
          {item.published ? 'Published ' + dateLabel(item.published) : 'Publication date unavailable'}
          {item.watch_seconds !== null && ' · ' + value(item.watch_seconds) + 's avg watch'}
        </small>
      </div>
      <strong className="rank-count">{value(item.value)}</strong>
      {item.url ? <a className="rank-link" href={item.url} target="_blank" rel="noreferrer" aria-label={'Open ' + item.title}><ArrowUpRight size={17} /></a>
        : <span className="rank-link" />}
    </article>)}
  </section>;
}

function PacingExperiment({ experiment, time }: { experiment: Pacing; time: (value: string | null) => string }) {
  const [platform, setPlatform] = useState('YouTube');
  const [hours, setHours] = useState(24);
  const groups = experiment.groups.filter(group => group.platform === platform && group.hours === hours);
  return <section className="panel experiment-panel" aria-labelledby="experiment-heading">
    <div className="panel-head"><div><h2 id="experiment-heading">Shotsnip pacing experiment</h2>
      <p>{experiment.published} of {experiment.target} clips published · Checked {time(experiment.updated)} IST</p></div></div>
    <div className="experiment-tools">
      <label className="source-select">Platform<select value={platform} onChange={event => setPlatform(event.target.value)}>
        <option>YouTube</option><option>Facebook</option></select></label>
      <label className="source-select">Observation age<select value={hours} onChange={event => setHours(Number(event.target.value))}>
        <option value={24}>24 hours</option><option value={72}>72 hours</option></select></label>
    </div>
    {!!groups.length && <table>
      <thead><tr><th scope="col">Variant</th><th scope="col">Observed / eligible</th><th scope="col">Median {groups[0].measure.toLowerCase()}</th></tr></thead>
      <tbody>{groups.map(group => <tr key={group.variant}><th scope="row">{group.variant}</th>
        <td>{value(group.observed)} / {value(group.eligible)}</td><td>{value(group.median)}</td></tr>)}</tbody>
    </table>}
    <p className="chart-note">{groups.some(group => (group.observed || 0) > 0)
      ? 'Observations are grouped by platform and age. Small samples can vary substantially.'
      : 'No eligible observations have been measured for this platform and age yet.'}</p>
  </section>;
}

export function Analytics({ data, channel, choose, time, pacing }: {
  data: StudioAnalytics; channel: StudioChannel; choose: (channel: StudioChannel) => void;
  time: (value: string | null) => string;
  pacing?: Pacing | null;
}) {
  const [source, setSource] = useState(channel === 'histold' ? 'facebook-reels' : 'youtube-daily');
  const series = data[channel].find(item => item.id === source) || data[channel][0];
  return <div className="analytics-workspace">
    <div className="analytics-tools">
      <div className="filters source-filters" role="group" aria-label="Analytics channel">
        {studioChannels.map(item => <button key={item} aria-pressed={channel === item}
          onClick={() => choose(item)}>{channelNames[item]}</button>)}
      </div>
      {data[channel].length > 1 && <label className="source-select">Data source
        <select value={series.id} onChange={event => setSource(event.target.value)}>
          {data[channel].map(item => <option key={item.id} value={item.id}>{sourceName[item.id] || item.platform}</option>)}
        </select>
      </label>}
    </div>
    {series ? <>
      <div className="analytics-source"><span><BarChart3 size={16}/>{sourceName[series.id] || series.platform}</span>
        <small>{series.id === 'publishing' ? 'Latest recorded publication' : 'Exported'} {time(series.updated)} IST</small></div>
      {series.id === 'publishing' && !data[channel].some(item => item.id !== 'publishing') &&
        <p className="analytics-info">Audience metrics are unavailable for {channelNames[channel]}. Publishing activity is available below.</p>}
      <section className="metric-cards" aria-label="Source totals">
        {series.metrics.map(metric => <div className="stat" key={metric.label}><div>
          <span>{metric.label}</span><b>{value(metric.value)}</b><small>{metric.detail}</small>
        </div></div>)}
      </section>
      <div className={series.points.length && series.videos.length ? 'analytics-split' : 'analytics-flow'}>
        <ReportingChart key={channel + series.id} series={series} />
        <VideoRanking series={series} />
      </div>
      {!series.points.length && <p className="analytics-info">{series.note}</p>}
    </> : <div className="panel empty">There is no analytics export or publishing data for {channelNames[channel]}.</div>}
    {channel === 'shotsnip' && pacing && <PacingExperiment experiment={pacing} time={time} />}
  </div>;
}
