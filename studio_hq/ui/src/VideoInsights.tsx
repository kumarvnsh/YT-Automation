import { Area, Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { ArrowUpRight } from './icons';
import { Modal } from './Media';

export type TrafficRow = { source: string; label: string; views: number | null; share: number };
export type VideoDetail = {
  title: string; published: string | null; duration_seconds: number | null; short: boolean;
  totals: Partial<Record<'views' | 'engagedViews' | 'averageViewPercentage' | 'averageViewDuration' | 'likes' | 'shares' | 'comments' | 'subscribersGained', number | null>>;
  retention: [number, number][]; relative_retention: number | null; traffic: TrafficRow[]; daily: [string, number][];
};
export type Attention = { id: string; title: string; published: string | null; day: number; views: number; typical: number; ratio: number; reasons: string[] };
export type ChannelVideoInsights = {
  status: 'connected' | 'error' | 'pending'; error?: string | null; checked_at?: string | null; data_end?: string | null;
  traffic_start?: string | null; traffic?: TrafficRow[]; typical_retention?: [number, number][]; typical_view_percentage?: number | null;
  compare_day?: number; attention?: Attention[]; videos?: Record<string, VideoDetail>; video_count?: number; retention_count?: number;
};

type Time = (value: string | null) => string;
const whole = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 });
const pct = new Intl.NumberFormat('en-IN', { style: 'percent', maximumFractionDigits: 0 });
const dayLabel = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', timeZone: 'UTC' });
const axis = { fill: '#aebbd0', fontSize: 11 };
const tooltipStyle = { background: '#1b2637', border: '1px solid #41516b', borderRadius: 10, color: '#edf2fa', fontSize: 12 };
const typicalColor = '#8b98ad';

function duration(seconds: number | null | undefined) {
  if (seconds == null) return '—';
  const m = Math.floor(seconds / 60), s = Math.round(seconds % 60);
  return m ? `${m}m ${s.toString().padStart(2, '0')}s` : `${s}s`;
}

export function TrafficBars({ rows, color }: { rows: TrafficRow[]; color: string }) {
  if (!rows.length) return <p className="empty">YouTube has not reported where the views came from yet.</p>;
  const main = rows.filter(row => row.share >= 0.01).slice(0, 6);
  const rest = rows.filter(row => !main.includes(row));
  const shown = rest.length ? [...main, { source: 'OTHER', label: rest.length === 1 ? rest[0].label : 'Other sources',
    share: rest.reduce((sum, row) => sum + row.share, 0), views: rest.reduce((sum, row) => sum + (row.views ?? 0), 0) }] : main;
  return <ul className="traffic-bars">
    {shown.map(row => <li key={row.source}>
      <span className="traffic-name">{row.label}</span>
      <span className="traffic-track" aria-hidden="true"><i style={{ width: Math.max(row.share * 100, 1) + '%', background: color }} /></span>
      <span className="traffic-value">{pct.format(row.share)}<small>{row.views == null ? '' : compact.format(row.views)}</small></span>
    </li>)}
  </ul>;
}

function RetentionChart({ video, typical, color }: { video: VideoDetail; typical: [number, number][]; color: string }) {
  const marks = new Map<number, { share: number; video?: number; typical?: number }>();
  video.retention.forEach(([share, value]) => marks.set(share, { share, video: value }));
  typical.forEach(([share, value]) => marks.set(share, { ...(marks.get(share) ?? { share }), typical: value }));
  const data = [...marks.values()].sort((a, b) => a.share - b.share);
  return <ResponsiveContainer width="100%" height={230}>
    <ComposedChart data={data} margin={{ top: 8, right: 8, left: -10, bottom: 0 }}>
      <CartesianGrid vertical={false} stroke="#2d394c" strokeDasharray="3 5" />
      <XAxis dataKey="share" type="number" domain={[0, 1]} ticks={[0, .25, .5, .75, 1]} tick={axis} tickFormatter={v => pct.format(v)} axisLine={false} tickLine={false} />
      <YAxis tick={axis} tickFormatter={v => pct.format(v)} axisLine={false} tickLine={false} width={48} />
      <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: '#aebbd0', strokeDasharray: '3 3' }}
        labelFormatter={v => pct.format(Number(v)) + ' into the video'}
        formatter={(value: number, name: string) => [pct.format(value) + ' still watching', name === 'video' ? 'This video' : 'Typical recent upload']} />
      <Legend formatter={name => <span style={{ color: '#c7d8f1' }}>{name === 'video' ? 'This video' : 'Typical recent upload'}</span>} iconType="plainline" />
      <Area type="monotone" dataKey="video" stroke={color} strokeWidth={2} fill={color} fillOpacity={0.14} connectNulls dot={false} />
      {typical.length > 0 && <Line type="monotone" dataKey="typical" stroke={typicalColor} strokeWidth={2} strokeDasharray="5 4" dot={false} connectNulls />}
    </ComposedChart>
  </ResponsiveContainer>;
}

function DailyViews({ daily, color }: { daily: [string, number][]; color: string }) {
  return <ResponsiveContainer width="100%" height={180}>
    <BarChart data={daily.map(([day, views], index) => ({ day, views, label: 'Day ' + (index + 1) }))} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
      <CartesianGrid vertical={false} stroke="#2d394c" strokeDasharray="3 5" />
      <XAxis dataKey="day" tick={axis} tickFormatter={day => dayLabel.format(new Date(day))} interval="preserveStartEnd" minTickGap={18} axisLine={false} tickLine={false} />
      <YAxis tick={axis} tickFormatter={v => compact.format(v)} axisLine={false} tickLine={false} width={44} allowDecimals={false} />
      <Tooltip cursor={{ fill: '#ffffff0d' }} contentStyle={tooltipStyle}
        labelFormatter={(day, items) => (items?.[0]?.payload?.label ?? '') + ' · ' + dayLabel.format(new Date(String(day)))}
        formatter={(value: number) => [whole.format(value), 'Views']} />
      <Bar dataKey="views" fill={color} radius={[4, 4, 0, 0]} maxBarSize={18} />
    </BarChart>
  </ResponsiveContainer>;
}

export function VideoDetailModal({ id, video, insights, attention, color, time, onClose }: {
  id: string; video: VideoDetail; insights: ChannelVideoInsights; attention?: Attention; color: string; time: Time; onClose: () => void;
}) {
  const t = video.totals;
  const typicalPct = insights.typical_view_percentage;
  const facts: [string, string, string?][] = [
    ['Views', t.views == null ? '—' : whole.format(t.views)],
    ['Engaged views', t.engagedViews == null ? '—' : whole.format(t.engagedViews)],
    ['Average viewed', t.averageViewPercentage == null ? '—' : t.averageViewPercentage.toFixed(0) + '%',
      typicalPct != null ? 'Typical ' + typicalPct.toFixed(0) + '%' : undefined],
    ['Average watch time', duration(t.averageViewDuration), video.duration_seconds ? 'of ' + duration(video.duration_seconds) : undefined],
    ['Likes', t.likes == null ? '—' : whole.format(t.likes)],
    ['Shares', t.shares == null ? '—' : whole.format(t.shares)],
    ['Comments', t.comments == null ? '—' : whole.format(t.comments)],
    ['Subscribers gained', t.subscribersGained == null ? '—' : whole.format(t.subscribersGained)],
  ];
  return <Modal title={video.title} onClose={onClose}>
    <p className="trace-muted">{video.short ? 'Short' : 'Video'} · published {time(video.published)} IST · YouTube reported through {insights.data_end ? dayLabel.format(new Date(insights.data_end)) : '—'}
      {' '}<a className="inline-link" href={'https://www.youtube.com/watch?v=' + id} target="_blank" rel="noreferrer">Open on YouTube <ArrowUpRight size={13}/></a></p>
    {attention && <section className="attention-note" aria-label="Why this video needs a look">
      <strong>{whole.format(attention.views)} views by day {attention.day}, against {whole.format(attention.typical)} for a typical recent upload</strong>
      <ul>{attention.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>
    </section>}
    <dl className="insight-facts">{facts.map(([name, value, note]) => <div key={name}><dt>{name}</dt><dd>{value}{note && <small>{note}</small>}</dd></div>)}</dl>
    <section>
      <h3 className="modal-section">Audience retention</h3>
      {video.retention.length ? <>
        <RetentionChart video={video} typical={insights.typical_retention ?? []} color={color} />
        <p className="chart-note">Share of viewers still watching at each point. Above 100% means people replayed that part.
          {video.relative_retention != null && ' Compared with YouTube videos of a similar length, its retention ranks around the '
            + Math.round(video.relative_retention * 100) + 'th percentile.'}</p>
      </> : <p className="empty">YouTube reports retention only once a video has enough viewers. This one does not have a curve yet.</p>}
    </section>
    <div className="page-split even">
      <section><h3 className="modal-section">Where its views came from</h3><TrafficBars rows={video.traffic} color={color} /></section>
      <section><h3 className="modal-section">Views per day since publishing</h3>
        {video.daily.length ? <><DailyViews daily={video.daily} color={color} />
          <p className="chart-note">Days follow YouTube’s reporting day, which runs on US Pacific time.</p></>
          : <p className="empty">No daily views reported yet.</p>}</section>
    </div>
    <p className="chart-note">Impressions and click-through rate are only in YouTube Studio; the YouTube Analytics API does not provide them.</p>
  </Modal>;
}

export function AttentionPanel({ insights, open }: { insights: ChannelVideoInsights; open: (id: string) => void }) {
  const rows = insights.attention ?? [];
  return <section className="panel">
    <div className="panel-head"><div><h2>Slow starters</h2>
      <p>Recent uploads with under half the views a typical upload had at the same age (up to day {insights.compare_day ?? 7}).</p></div></div>
    {rows.length ? <ol className="video-list attention-list">{rows.map(row => <li key={row.id}>
      <span className="video-main"><button className="link-button" onClick={() => open(row.id)}><strong>{row.title}</strong></button>
        <small>{row.reasons[0]}</small></span>
      <span className="video-score"><b>{whole.format(row.views)}</b><small>vs {whole.format(row.typical)} by day {row.day}</small></span>
    </li>)}</ol> : <p className="empty">No recent upload is far behind the typical one.</p>}
  </section>;
}

export function TrafficPanel({ insights, color }: { insights: ChannelVideoInsights; color: string }) {
  return <section className="panel">
    <div className="panel-head"><div><h2>Where YouTube views come from</h2>
      <p>Channel views by traffic source{insights.traffic_start ? ' since ' + dayLabel.format(new Date(insights.traffic_start)) : ''} · last 28 reported days</p></div></div>
    <TrafficBars rows={insights.traffic ?? []} color={color} />
  </section>;
}

export function InsightsStatus({ insights, time }: { insights?: ChannelVideoInsights | null; time: Time }) {
  if (!insights) return null;
  if (insights.status !== 'connected') return <p className="analytics-info" role="status">Video insights: {insights.error}</p>;
  return <p className="insights-updated">Video insights {time(insights.checked_at ?? null)} IST · {insights.video_count} recent uploads ·
    {' '}{insights.retention_count} with retention curves · tap a YouTube video for details{insights.error ? ' · last refresh: ' + insights.error : ''}</p>;
}
