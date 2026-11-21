import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BookOpen, CalendarDays, CircleAlert, Home, IconContext, RefreshCw, Scissors, Server, ShieldCheck, StudioLogo } from './icons';
import type { Command, Send } from './Controls';
import { HistoldPage, HomePage, PlanPage, ShotsnipPage, SystemPage, bytes, type PageProps, type Snapshot } from './Pages';
import './styles.css';

const formatTime = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit', hour12: false,
});

function timestamp(value: string | null) {
  if (!value || !Number.isFinite(Date.parse(value))) return '—';
  return formatTime.format(new Date(value));
}

/** Five sections, each with at most one row of sub-pages. Every page has its own address. */
const sections = [
  { id: 'home', label: 'Home', icon: Home, intro: 'Channel performance across every platform.',
    subs: [['histold', 'Histold'], ['shotsnip', 'Shotsnip']] },
  { id: 'shotsnip', label: 'Shotsnip', icon: Scissors, intro: 'Live clipping, the clip queue, sources, and the agent.',
    subs: [['live', 'Live'], ['clips', 'Clips'], ['queue', 'Queue'], ['targets', 'Targets'], ['agent', 'Agent'], ['bots', 'Bots']] },
  { id: 'histold', label: 'Histold', icon: BookOpen, intro: 'Topics, experiments, the assistant, approvals, and strategy controls.',
    subs: [['topics', 'Topics'], ['experiments', 'Experiments'], ['assistant', 'Assistant'], ['approvals', 'Approvals'], ['controls', 'Controls'], ['bots', 'Bots']] },
  { id: 'plan', label: 'Plan', icon: CalendarDays, intro: 'The publishing calendar, earnings, and costs.',
    subs: [['calendar', 'Calendar'], ['money', 'Money'], ['jobs', 'Job usage']] },
  { id: 'system', label: 'System', icon: Server, intro: 'Storage, background jobs, AI settings, the archive, and original tools.',
    subs: [['health', 'Health'], ['ai', 'AI'], ['activity', 'Activity'], ['archive', 'Archive'], ['tools', 'Original tools']] },
] as const;
type SectionId = typeof sections[number]['id'];

function parseRoute() {
  const parts = window.location.hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
  const section = sections.find(item => item.id === parts[0]) ?? sections[0];
  const sub = section.subs.find(([id]) => id === parts[1])?.[0] ?? section.subs[0]?.[0] ?? '';
  return { section, sub, rest: section.subs.some(([id]) => id === parts[1]) ? parts.slice(2) : [] };
}

function useRoute() {
  const [route, setRoute] = useState(parseRoute);
  useEffect(() => {
    const update = () => { setRoute(parseRoute()); window.scrollTo(0, 0); };
    window.addEventListener('hashchange', update);
    return () => window.removeEventListener('hashchange', update);
  }, []);
  return route;
}

function App() {
  const route = useRoute();
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [issue, setIssue] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [request, setRequest] = useState(0);
  const [sending, setSending] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [retry, setRetry] = useState<Command | null>(null);
  // A queued command is followed until Windows finishes it, then its result and the new data show by themselves.
  const [tracking, setTracking] = useState<{ id: string; since: number } | null>(null);

  async function sendRequest(body: Command) {
    setSending(true);
    setFeedback('Sending request…');
    try {
      const result = await fetch(new URL('api/command', window.location.href), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Studio-HQ': '1' },
        body: JSON.stringify(body),
      });
      if (result.status >= 500) throw new Error('Request unavailable');
      const data = await result.json();
      setRetry(null);
      if (result.ok && typeof data.command_id === 'string') {
        setTracking({ id: data.command_id, since: Date.now() });
        setFeedback('Working on it on Windows…');
      } else {
        setFeedback(data.message || (result.ok ? 'Done.' : 'Request denied.'));
      }
      setRequest(value => value + 1);
    } catch {
      setRetry(body);
      setFeedback('The outcome is unknown. Check your recent requests or retry the same request.');
    } finally {
      setSending(false);
    }
  }
  const send: Send = body => { void sendRequest({ ...body, request_id: crypto.randomUUID() }); };

  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    async function load() {
      if (busy) return;
      busy = true;
      setRefreshing(true);
      try {
        const res = await fetch(new URL('api/snapshot', window.location.href), { signal: controller.signal, cache: 'no-store' });
        if (!res.ok) throw new Error('Snapshot unavailable');
        const data: Snapshot = await res.json();
        if (!controller.signal.aborted) { setSnapshot(data); setIssue(false); }
      } catch {
        if (!controller.signal.aborted) setIssue(true);
      } finally {
        busy = false;
        if (!controller.signal.aborted) setRefreshing(false);
      }
    }
    void load();
    const interval = setInterval(load, tracking ? 2_000 : 30_000);
    return () => { controller.abort(); clearInterval(interval); };
  }, [request, tracking?.id]);

  useEffect(() => {
    if (!tracking || !snapshot) return;
    const event = snapshot.commands.find(item => item.id === tracking.id);
    if (event && !['queued', 'running'].includes(event.status)) {
      setFeedback(event.message);
      setTracking(null);
    } else if (Date.now() - tracking.since > 180_000) {
      setFeedback('Still running on Windows. The result will appear under System → Health when it finishes.');
      setTracking(null);
    } else if (event?.status === 'running') {
      setFeedback('Running on Windows…');
    }
  }, [snapshot, tracking]);

  const stale = snapshot !== null && Date.now() - Date.parse(snapshot.generated_at) > 90_000;
  const status = issue || stale ? 'Delayed' : snapshot ? 'Live' : 'Connecting';
  const { section, sub, rest } = route;
  const subnav = useRef<HTMLElement>(null);
  function markScroll() {
    const el = subnav.current;
    if (!el) return;
    el.classList.toggle('scrolls', el.scrollWidth > el.clientWidth + 2);
    el.classList.toggle('at-end', el.scrollLeft + el.clientWidth >= el.scrollWidth - 2);
  }
  useEffect(() => {
    // Keep the current sub-page visible in the scrollable row on phones.
    subnav.current?.querySelector('[aria-current=page]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    markScroll();
  }, [section.id, sub]);
  const props: PageProps | null = snapshot && {
    snapshot, time: timestamp, send: snapshot.controls_enabled ? send : undefined,
    busy: sending || !!tracking || stale || issue, sub, rest,
  };
  const Page = { home: HomePage, shotsnip: ShotsnipPage, histold: HistoldPage, plan: PlanPage, system: SystemPage }[section.id];

  const nav = (className: string) => <nav className={className} aria-label="Sections">
    {sections.map(item => <a key={item.id} href={'#/' + item.id} aria-current={item.id === section.id ? 'page' : undefined}>
      <span className="nav-icon"><item.icon size={22} weight={item.id === section.id ? 'fill' : 'duotone'} aria-hidden="true"/></span>
      <span>{item.label}</span>
    </a>)}
  </nav>;

  return <div className="shell">
    <a className="skip" href="#content">Skip to content</a>
    <aside className="rail">
      <div className="brand"><StudioLogo size={42}/><div><strong>Studio HQ</strong><span>Histold · Shotsnip</span></div></div>
      {nav('rail-nav')}
      <div className="rail-foot"><span><ShieldCheck size={15}/>Private access</span></div>
    </aside>
    <main id="content">
      <header className="topline">
        <div className="mobile-brand"><StudioLogo size={32}/><strong>Studio HQ</strong></div>
        <div className="top-actions">
          <span className={'connection ' + (issue || stale ? 'is-delayed' : '')} title={snapshot ? 'Updated ' + timestamp(snapshot.generated_at) + ' IST' : undefined}><i />{status}</span>
          <button className="refresh" onClick={() => setRequest(value => value + 1)} disabled={refreshing} aria-label="Refresh" title="Refresh"><RefreshCw size={17}/></button>
        </div>
      </header>
      <section className="intro">
        <span className="eyebrow">{section.subs.find(([id]) => id === sub)?.[1] ?? 'Studio HQ'}</span>
        <h1>{section.label}</h1>
        <p>{section.intro}</p>
      </section>
      {section.subs.length > 0 && <nav className="subnav" ref={subnav} onScroll={markScroll} aria-label={section.label + ' pages'}>
        {section.subs.map(([id, label]) => <a key={id} href={'#/' + section.id + '/' + id} aria-current={id === sub ? 'page' : undefined}>
          {label}
        </a>)}
      </nav>}
      {(issue || stale) && <div className="notice" role="status"><CircleAlert size={17}/><span>{snapshot ? 'Showing the last available data.' : 'Studio HQ is unavailable. Try refreshing.'}</span></div>}
      {feedback && <div className={'command-feedback' + (sending || tracking ? ' working' : '')} role="status">
        {(sending || tracking) && <span className="spinner" aria-hidden="true" />}<span>{feedback}</span>
        {retry && <button className="action" disabled={sending} onClick={() => void sendRequest(retry)}>Retry</button>}
        {!sending && !tracking && <button className="action" aria-label="Dismiss message" onClick={() => { setFeedback(''); setRetry(null); }}>Dismiss</button>}
      </div>}
      {props ? <Page {...props} /> : <div className="loading" role="status">{issue ? 'Use refresh to try again.' : 'Loading…'}</div>}
      {snapshot && <footer>Updated {timestamp(snapshot.generated_at)} IST · {bytes(snapshot.storage.free)} free</footer>}
    </main>
    {nav('bottom-nav')}
  </div>;
}

createRoot(document.getElementById('root')!).render(<IconContext.Provider value={{ weight: 'bold' }}><App /></IconContext.Provider>);
