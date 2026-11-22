import { useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, ArrowUpRight } from './icons';
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { channelNames, PacingExperiment, type Pacing, type Series, type StudioAnalytics, type StudioChannel } from './Analytics';
import { AttentionPanel, InsightsStatus, TrafficPanel, VideoDetailModal, type ChannelVideoInsights } from './VideoInsights';

type Time = (value: string | null) => string;
type Platform = 'youtube' | 'facebook' | 'instagram';
type Video = Series['videos'][number] & { platform: Platform };

/* Validated categorical order (dataviz validator, dark surface #151d2b): all checks pass. */
const platformColor: Record<Platform, string> = { youtube: '#d9463f', facebook: '#3987e5', instagram: '#24a88a' };
const platformName: Record<Platform, string> = { youtube: 'YouTube', facebook: 'Facebook', instagram: 'Instagram' };
const compact = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 });
const whole = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
const percent = new Intl.NumberFormat('en-IN', { style: 'percent', maximumFractionDigits: 1 });
const dayLabel = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', timeZone: 'UTC' });
const axis = { fill: '#aebbd0', fontSize: 11 };
const tooltipStyle = { background: '#1b2637', border: '1px solid #41516b', borderRadius: 10, color: '#edf2fa', fontSize: 12 };

/** Per-video series that share a comparable "views" meaning; legacy Facebook views stay out of comparisons. */
function platformSeries(series: Series[]) {
  const pick = (ids: string[]) => series.find(item => ids.includes(item.id) && item.videos.length);
  return ([['youtube', ['youtube']], ['facebook', ['facebook-reels', 'facebook']], ['instagram', ['instagram']]] as const)
    .map(([platform, ids]) => ({ platform, series: pick([...ids]) }))
    .filter((row): row is { platform: Platform; series: Series } => !!row.series);
}

function median(values: number[]) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function lastDays(count: number, end: string) {
  const days: string[] = [];
  const last = new Date(end + 'T00:00:00Z');
  for (let i = count - 1; i >= 0; i--) days.push(new Date(last.getTime() - i * 86400000).toISOString().slice(0, 10));
  return days;
}

function ChartPanel({ title, note, children, wide }: { title: string; note: string; children: React.ReactNode; wide?: boolean }) {
  return <section className={'panel chart-panel' + (wide ? ' wide' : '')}>
    <div className="panel-head"><div><h2>{title}</h2><p>{note}</p></div></div>
    <div className="chart-body">{children}</div>
  </section>;
}

function Kpis({ series, platforms }: { series: Series[]; platforms: ReturnType<typeof platformSeries> }) {
  const tiles: { label: string; value: number | null; detail: string; color?: string }[] = [];
  for (const { platform, series: item } of platforms) {
    const first = item.metrics[0];
    tiles.push({ label: platformName[platform] + ' · ' + first.label.toLowerCase(), value: first.value,
      detail: item.metrics.slice(1).map(metric => metric.label + ' ' + (metric.value == null ? '—' : compact.format(metric.value))).join(' · '),
      color: platformColor[platform] });
  }
  for (const id of ['youtube-daily', 'facebook-daily']) {
    const item = series.find(row => row.id === id);
    item?.metrics.slice(0, 2).forEach(metric => tiles.push({ label: (id.startsWith('youtube') ? 'YouTube' : 'Facebook') + ' · ' + metric.label.toLowerCase(),
      value: metric.value, detail: metric.detail }));
  }
  const publishing = series.find(row => row.id === 'publishing');
  if (publishing) tiles.push({ label: 'Posts · last 28 days', value: publishing.metrics[0]?.value ?? null, detail: publishing.metrics[1]
    ? publishing.metrics[1].label + ' ' + (publishing.metrics[1].value ?? '—') : '' });
  return <section className="kpi-grid" aria-label="At a glance">
    {tiles.slice(0, 8).map(tile => <article className="kpi" key={tile.label}>
      <span className="kpi-label">{tile.color && <i style={{ background: tile.color }} aria-hidden="true" />}{tile.label}</span>
      <strong>{tile.value == null ? '—' : compact.format(tile.value)}</strong>
      <small>{tile.detail}</small>
    </article>)}
  </section>;
}

function ViewsByPublishDay({ videos, end }: { videos: Video[]; end: string }) {
  const present = (['youtube', 'facebook', 'instagram'] as const).filter(p => videos.some(v => v.platform === p));
  const data = lastDays(28, end).map(day => {
    const row: Record<string, string | number> = { day };
    for (const p of present) row[p] = videos.filter(v => v.platform === p && v.published === day).reduce((sum, v) => sum + (v.value ?? 0), 0);
    return row;
  });
  // Start at the first day with any views (at least a week), so a new channel's chart is not mostly empty.
  const first = data.findIndex(row => present.some(p => Number(row[p]) > 0));
  if (first > 0) data.splice(0, Math.min(first, data.length - 7));
  return <ResponsiveContainer width="100%" height={260}>
    <BarChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
      <CartesianGrid vertical={false} stroke="#2d394c" strokeDasharray="3 5" />
      <XAxis dataKey="day" tick={axis} tickFormatter={day => dayLabel.format(new Date(day))} interval="preserveStartEnd" minTickGap={18} axisLine={false} tickLine={false} />
      <YAxis tick={axis} tickFormatter={value => compact.format(value)} axisLine={false} tickLine={false} width={48} />
      <Tooltip cursor={{ fill: '#ffffff0d' }} contentStyle={tooltipStyle} labelFormatter={day => dayLabel.format(new Date(String(day)))}
        formatter={(value: number, name: string) => [whole.format(value), platformName[name as Platform]]} />
      <Legend formatter={name => <span style={{ color: '#c7d8f1' }}>{platformName[name as Platform]}</span>} iconType="square" iconSize={10} />
      {present.map((p, index) => <Bar key={p} dataKey={p} stackId="views" fill={platformColor[p]} stroke="#151d2b" strokeWidth={1}
        radius={index === present.length - 1 ? [4, 4, 0, 0] : 0} maxBarSize={22} />)}
    </BarChart>
  </ResponsiveContainer>;
}

function DailyArea({ series, color }: { series: Series; color: string }) {
  const data = series.points.map(point => ({ day: point.day, value: point.value }));
  const id = 'fill-' + series.id;
  return <ResponsiveContainer width="100%" height={220}>
    <AreaChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
      <defs><linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor={color} stopOpacity={0.45} /><stop offset="100%" stopColor={color} stopOpacity={0.02} /></linearGradient></defs>
      <CartesianGrid vertical={false} stroke="#2d394c" strokeDasharray="3 5" />
      <XAxis dataKey="day" tick={axis} tickFormatter={day => dayLabel.format(new Date(day))} interval="preserveStartEnd" minTickGap={24} axisLine={false} tickLine={false} />
      <YAxis tick={axis} tickFormatter={value => compact.format(value)} axisLine={false} tickLine={false} width={48} />
      <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: '#aebbd0', strokeDasharray: '3 3' }}
        labelFormatter={day => dayLabel.format(new Date(String(day)))} formatter={(value: number) => [value == null ? 'Not reported' : whole.format(value), series.chart_label || 'Value']} />
      <Area type="monotone" dataKey="value" stroke={color} strokeWidth={2} fill={`url(#${id})`} connectNulls={false}
        activeDot={{ r: 5, stroke: '#151d2b', strokeWidth: 2 }} />
    </AreaChart>
  </ResponsiveContainer>;
}

function PlatformMedians({ platforms }: { platforms: ReturnType<typeof platformSeries> }) {
  const data = platforms.map(({ platform, series }) => ({ platform,
    median: median(series.videos.map(v => v.value).filter((v): v is number => v != null)) ?? 0, count: series.videos.length }));
  return <ResponsiveContainer width="100%" height={Math.max(120, data.length * 56)}>
    <BarChart data={data} layout="vertical" margin={{ top: 4, right: 40, left: 8, bottom: 0 }}>
      <CartesianGrid horizontal={false} stroke="#2d394c" strokeDasharray="3 5" />
      <XAxis type="number" tick={axis} tickFormatter={value => compact.format(value)} axisLine={false} tickLine={false} />
      <YAxis type="category" dataKey="platform" tick={{ ...axis, fill: '#c7d8f1' }} tickFormatter={p => platformName[p as Platform]} axisLine={false} tickLine={false} width={76} />
      <Tooltip cursor={{ fill: '#ffffff0d' }} contentStyle={tooltipStyle}
        formatter={(value: number, _name, item) => [whole.format(value) + ' per video', (item.payload as { count: number }).count + ' videos measured']}
        labelFormatter={p => platformName[p as Platform]} />
      <Bar dataKey="median" radius={[0, 4, 4, 0]} maxBarSize={26} label={{ position: 'right', fill: '#edf2fa', fontSize: 12, formatter: (value: unknown) => compact.format(Number(value)) }}>
        {data.map(row => <Cell key={row.platform} fill={platformColor[row.platform]} />)}
      </Bar>
    </BarChart>
  </ResponsiveContainer>;
}

function Rhythm({ series, color }: { series: Series; color: string }) {
  return <ResponsiveContainer width="100%" height={200}>
    <BarChart data={series.points} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
      <CartesianGrid vertical={false} stroke="#2d394c" strokeDasharray="3 5" />
      <XAxis dataKey="day" tick={axis} tickFormatter={day => dayLabel.format(new Date(day))} interval="preserveStartEnd" minTickGap={24} axisLine={false} tickLine={false} />
      <YAxis tick={axis} allowDecimals={false} axisLine={false} tickLine={false} width={40} />
      <Tooltip cursor={{ fill: '#ffffff0d' }} contentStyle={tooltipStyle} labelFormatter={day => dayLabel.format(new Date(String(day)))}
        formatter={(value: number) => [whole.format(value), 'Posts']} />
      <Bar dataKey="value" fill={color} radius={[4, 4, 0, 0]} maxBarSize={18} />
    </BarChart>
  </ResponsiveContainer>;
}

function rate(video: Video) {
  return video.value && video.likes != null ? video.likes / video.value : null;
}

type Opener = { canOpen: (video: Video) => boolean; open: (video: Video) => void };

function VideoTitle({ video, opener, strong }: { video: Video; opener?: Opener; strong?: boolean }) {
  const text = strong ? <strong>{video.title}</strong> : video.title;
  if (opener?.canOpen(video)) return <button className="link-button" onClick={() => opener.open(video)}>{text}</button>;
  if (!strong && video.url) return <a href={video.url} target="_blank" rel="noreferrer">{video.title}</a>;
  return text;
}

function VideoList({ title, note, videos, score, opener }: { title: string; note: string; videos: Video[]; score: (video: Video) => number; opener?: Opener }) {
  return <section className="panel">
    <div className="panel-head"><div><h2>{title}</h2><p>{note}</p></div></div>
    {videos.length ? <ol className="video-list">{videos.map(video => <li key={video.platform + video.id}>
      <span className="platform-dot" style={{ background: platformColor[video.platform] }} aria-hidden="true" />
      <span className="video-main"><VideoTitle video={video} opener={opener} strong />
        <small>{platformName[video.platform]} · {video.published ? dayLabel.format(new Date(video.published)) : 'date unknown'}{rate(video) != null ? ' · ' + percent.format(rate(video)!) + ' likes' : ''}</small></span>
      <span className="video-score"><b>{video.value == null ? '—' : compact.format(video.value)}</b><small>{score(video).toFixed(1)}× typical</small></span>
      {video.url ? <a className="icon-link" href={video.url} target="_blank" rel="noreferrer" aria-label={'Open ' + video.title}><ArrowUpRight size={15}/></a> : <span />}
    </li>)}</ol> : <p className="empty">Not enough measured videos yet.</p>}
  </section>;
}

type SortKey = 'published' | 'value' | 'likes' | 'rate';

function VideoTable({ videos, opener }: { videos: Video[]; opener?: Opener }) {
  const [platform, setPlatform] = useState<'all' | Platform>('all');
  const [sort, setSort] = useState<{ key: SortKey; down: boolean }>({ key: 'published', down: true });
  const [limit, setLimit] = useState(15);
  const present = (['youtube', 'facebook', 'instagram'] as const).filter(p => videos.some(v => v.platform === p));
  const value = (video: Video) => sort.key === 'rate' ? rate(video) : sort.key === 'published' ? video.published : video[sort.key];
  const rows = videos.filter(video => platform === 'all' || video.platform === platform).sort((a, b) => {
    const left = value(a), right = value(b);
    if (left == null) return 1;
    if (right == null) return -1;
    return (left < right ? -1 : left > right ? 1 : 0) * (sort.down ? -1 : 1);
  });
  const header = (key: SortKey, label: string) => <th scope="col" aria-sort={sort.key === key ? (sort.down ? 'descending' : 'ascending') : 'none'}>
    <button className="sort" onClick={() => setSort(current => ({ key, down: current.key === key ? !current.down : true }))}>
      {label}{sort.key === key && (sort.down ? <ArrowDown size={13}/> : <ArrowUp size={13}/>)}</button></th>;
  return <section className="panel experiment-panel video-table">
    <div className="panel-head"><div><h2>All videos</h2><p>Lifetime totals from each platform’s latest export. Tap a column to sort.</p></div></div>
    <div className="filters" role="group" aria-label="Platform">
      <button aria-pressed={platform === 'all'} onClick={() => { setPlatform('all'); setLimit(15); }}>All platforms</button>
      {present.map(p => <button key={p} aria-pressed={platform === p} onClick={() => { setPlatform(p); setLimit(15); }}>
        <span className="platform-dot" style={{ background: platformColor[p] }} aria-hidden="true" />{platformName[p]}</button>)}
    </div>
    <div className="table-scroll"><table>
      <thead><tr><th scope="col">Video</th>{header('published', 'Published')}{header('value', 'Views')}{header('likes', 'Likes')}{header('rate', 'Like rate')}</tr></thead>
      <tbody>{rows.slice(0, limit).map(video => <tr key={video.platform + video.id}>
        <th scope="row"><span className="table-title"><span className="platform-dot" style={{ background: platformColor[video.platform] }} aria-hidden="true" />
          <VideoTitle video={video} opener={opener} /></span><small>{platformName[video.platform]}{opener?.canOpen(video) ? ' · details' : ''}</small></th>
        <td>{video.published ? dayLabel.format(new Date(video.published)) : '—'}</td>
        <td>{video.value == null ? '—' : whole.format(video.value)}</td>
        <td>{video.likes == null ? '—' : whole.format(video.likes)}</td>
        <td>{rate(video) == null ? '—' : percent.format(rate(video)!)}</td></tr>)}</tbody>
    </table></div>
    {rows.length > limit && <div className="panel-foot"><button onClick={() => setLimit(count => count + 15)}>Show more videos</button></div>}
  </section>;
}

export function ChannelInsights({ data, channel, time, pacing, insights }: { data: StudioAnalytics; channel: StudioChannel; time: Time;
  pacing?: Pacing | null; insights?: ChannelVideoInsights | null }) {
  const series = data[channel];
  const [openId, setOpenId] = useState<string | null>(null);
  const details = insights?.status === 'connected' ? insights.videos ?? {} : {};
  const opener: Opener = { canOpen: video => video.platform === 'youtube' && !!details[video.id], open: video => setOpenId(video.id) };
  const platforms = useMemo(() => platformSeries(series), [series]);
  const videos: Video[] = useMemo(() => platforms.flatMap(({ platform, series: item }) =>
    item.videos.map(video => ({ ...video, platform }))), [platforms]);
  const updated = series.map(item => item.updated).filter(Boolean).sort().at(-1) ?? null;
  const end = (updated ?? new Date().toISOString()).slice(0, 10);
  const mature = videos.filter(video => video.published && video.value != null
    && Date.parse(end) - Date.parse(video.published) >= 3 * 86400000);
  // Rank against the typical video on the same platform; raw counts are not comparable across platforms.
  const typical = Object.fromEntries(platforms.map(({ platform, series: item }) =>
    [platform, median(item.videos.map(v => v.value).filter((v): v is number => v != null)) ?? 0]));
  const score = (video: Video) => typical[video.platform] ? video.value! / typical[video.platform] : 0;
  const top = videos.filter(video => video.value != null && typical[video.platform]).sort((a, b) => score(b) - score(a)).slice(0, 5);
  const learn = mature.filter(video => typical[video.platform]).sort((a, b) => score(a) - score(b)).slice(0, 5);
  const brand = channel === 'histold' ? '#7fb0ff' : '#ffcf35';
  const youtubeDaily = series.find(item => item.id === 'youtube-daily' && item.points.length);
  const facebookDaily = series.find(item => item.id === 'facebook-daily' && item.points.length);
  const publishing = series.find(item => item.id === 'publishing' && item.points.length);
  if (!series.length) return <div className="panel empty">There is no analytics export for {channelNames[channel]} yet.</div>;
  return <div className="page-stack insights">
    <p className="insights-updated">Latest export {time(updated)} IST · {videos.length} measured videos across {platforms.length} platforms</p>
    <InsightsStatus insights={insights} time={time} />
    <Kpis series={series} platforms={platforms} />
    <div className="chart-grid">
      <ChartPanel wide title="Views by publish day" note="Lifetime views of videos published each day, by platform · last 28 days">
        <ViewsByPublishDay videos={videos} end={end} />
      </ChartPanel>
      {youtubeDaily && <ChartPanel title="YouTube views per day" note="Channel-wide views reported by YouTube Analytics">
        <DailyArea series={youtubeDaily} color={platformColor.youtube} /></ChartPanel>}
      {facebookDaily && <ChartPanel title="Facebook page views per day" note="Page views reported by Meta">
        <DailyArea series={facebookDaily} color={platformColor.facebook} /></ChartPanel>}
      {platforms.length > 1 && <ChartPanel title="Typical video by platform" note="Median lifetime views per measured video">
        <PlatformMedians platforms={platforms} /></ChartPanel>}
      {publishing && <ChartPanel title="Publishing rhythm" note="Posts recorded per day · last 28 days">
        <Rhythm series={publishing} color={brand} /></ChartPanel>}
    </div>
    {insights?.status === 'connected' && <div className="page-split even">
      <AttentionPanel insights={insights} open={setOpenId} />
      <TrafficPanel insights={insights} color={platformColor.youtube} />
    </div>}
    <div className="page-split even">
      <VideoList title="Top performers" note="Furthest above the typical video on their platform" videos={top} score={score} opener={opener} />
      <VideoList title="Videos to learn from" note="Furthest below typical, among videos at least 3 days old" videos={learn} score={score} opener={opener} />
    </div>
    {videos.length > 0 && <VideoTable videos={videos} opener={opener} />}
    {openId && details[openId] && insights && <VideoDetailModal id={openId} video={details[openId]} insights={insights} color={brand}
      attention={insights.attention?.find(row => row.id === openId)} time={time} onClose={() => setOpenId(null)} />}
    {channel === 'shotsnip' && pacing && <PacingExperiment experiment={pacing} time={time} />}
  </div>;
}
