import { useState } from 'react';
import { Robot, Warning } from '@phosphor-icons/react';
import type { CommandEvent, Send } from './Controls';

type Time = (value: string | null) => string;
type Task = { id: string; bot: string; engine: string; status: string; stage: string; instruction: string; created: string | null;
  updated: string | null; error: string | null; has_report: boolean; report: string; events: { at: string | null; note: string }[] };
type Decision = { id: string; bot: string; status: string; selected: string | null; version: number; updated: string | null; title: string;
  summary: string; evidence: string; success_metric: string; options: { id: string; label: string; plan: string }[] };
type Problem = { key: string; title: string; plain: string; evidence: string; last_seen: string | null; fix: { state: string; label: string } };
export type BotChannel = { bots: { id: string; name: string; role: string; mission: string }[]; tasks: Task[]; notes: Record<string, string>;
  routines: { bot: string; engine: string; instruction: string; interval: number; next_at: string | null }[];
  decisions: Decision[]; problems: Problem[] };
export type BotWorkspace = { scope: string; channels: Record<'histold' | 'shotsnip', BotChannel> };

const intervals = [[0, 'Off'], [3600, 'Hourly'], [21600, 'Every 6 h'], [86400, 'Daily']] as const;
const tone = (status: string) => status === 'completed' ? 'good' : ['failed', 'interrupted'].includes(status) ? 'warn' : 'neutral';

function BotCard({ bot, channel, routine, note, busy, send }: { bot: BotChannel['bots'][number]; channel: string;
  routine?: BotChannel['routines'][number]; note: string; busy: boolean; send?: Send }) {
  const [instruction, setInstruction] = useState('');
  const [engine, setEngine] = useState('analyst');
  const [notes, setNotes] = useState(note);
  const act = (extra: Record<string, string | number>) => send?.({ action: 'bot_action', channel, bot: bot.id, ...extra });
  return <section className="panel control-form bot-card">
    <h2><Robot size={18} weight="duotone"/>{bot.name}<small>{bot.role}</small></h2>
    <p className="control-note">{bot.mission}</p>
    {send && <>
      <label>New assignment<textarea rows={2} maxLength={4000} value={instruction} placeholder={bot.mission}
        onChange={event => setInstruction(event.target.value)} /></label>
      <div className="bot-row">
        <label>Engine<select value={engine} onChange={event => setEngine(event.target.value)}>
          <option value="analyst">Analyst</option><option value="superbot">Superbot</option></select></label>
        <button className="action primary" disabled={busy} onClick={() => { act({ bot_action: 'assign', instruction, engine }); setInstruction(''); }}>Assign</button>
      </div>
      <label>Routine<select value={routine?.interval ?? 0} disabled={busy}
        onChange={event => act({ bot_action: 'routine', interval: Number(event.target.value), engine: routine?.engine ?? 'analyst',
          instruction: routine?.instruction ?? '' })}>
        {intervals.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      {routine && routine.interval > 0 && <p className="control-note">Next run {routine.next_at ? new Date(routine.next_at).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false }) + ' IST' : 'soon'} · {routine.engine}</p>}
      <label>Bot notes<textarea rows={2} maxLength={6000} value={notes} placeholder="Standing guidance this bot should remember"
        onChange={event => setNotes(event.target.value)} /></label>
      {notes !== note && <button className="action" disabled={busy} onClick={() => act({ bot_action: 'memory', text: notes })}>Save notes</button>}
    </>}
  </section>;
}

export function BotsPage({ data, channel, busy, send, recent, time }: { data: BotWorkspace; channel: 'histold' | 'shotsnip'; busy: boolean;
  send?: Send; recent?: CommandEvent; time: Time }) {
  const view = data.channels[channel];
  const [limit, setLimit] = useState(8);
  const act = (extra: Record<string, string | number>) => send?.({ action: 'bot_action', channel, ...extra });
  const name = (id: string) => view.bots.find(bot => bot.id === id)?.name ?? id;
  const pending = view.decisions.filter(item => item.status === 'pending');
  const resolved = view.decisions.filter(item => item.status !== 'pending');
  return <div className="page-stack">
    <p className="insights-updated">{data.scope}</p>
    {recent && <p className="analytics-info" role="status">{recent.message} · {time(recent.at)} IST</p>}
    {view.problems.length > 0 && <section className="panel">
      <div className="panel-head"><div><h2>Open problems</h2><p>Found by the operations bot. Repairs keep the Operator’s normal checks.</p></div></div>
      {view.problems.map(problem => <article className="control-row" key={problem.key}>
        <div className="control-row-title"><strong><Warning size={15} weight="fill" className="warn-icon"/> {problem.title}</strong>
          <small>{problem.plain}</small><small>{problem.fix.label}{problem.last_seen ? ' · seen ' + time(problem.last_seen) : ''}</small></div>
        {send && <div className="control-actions">
          <button className="action" disabled={busy} onClick={() => act({ bot_action: 'verify_problem', key: problem.key })}>Check again</button>
          <button className="action primary" disabled={busy} onClick={() => act({ bot_action: 'approve_problem', key: problem.key })}>Approve repair</button>
        </div>}
      </article>)}
    </section>}
    {pending.length > 0 && <section className="panel">
      <div className="panel-head"><div><h2>Decisions for you</h2><p>Choosing records your direction and queues a detailed brief. It does not change production settings.</p></div></div>
      {pending.map(item => <article className="decision" key={item.id}>
        <h3>{item.title}</h3><p>{item.summary}</p>
        {item.success_metric && <p className="trace-muted">Success measure: {item.success_metric}</p>}
        {item.evidence && <details><summary>Evidence</summary><p className="assistant-reply">{item.evidence}</p></details>}
        {send && <div className="decision-options">{item.options.map(option => <button key={option.id} className="decision-option" disabled={busy}
          onClick={() => act({ bot_action: 'choose_decision', id: item.id, option: option.id, version: item.version })}>
          <strong>{option.label}</strong><small>{option.plan}</small></button>)}
          <button className="action" disabled={busy} onClick={() => act({ bot_action: 'dismiss_decision', id: item.id, version: item.version })}>Dismiss</button>
        </div>}
      </article>)}
    </section>}
    <div className="bot-grid">
      {view.bots.map(bot => <BotCard key={bot.id + JSON.stringify(view.notes[bot.id] ?? '')} bot={bot} channel={channel} busy={busy} send={send}
        routine={view.routines.find(row => row.bot === bot.id)} note={view.notes[bot.id] ?? ''} />)}
    </div>
    <section className="panel">
      <div className="panel-head"><div><h2>Assignments</h2><p>Newest first. Open one to read its report.</p></div></div>
      {view.tasks.length === 0 && <p className="empty">No assignments yet.</p>}
      <ul className="task-list">{view.tasks.slice(0, limit).map(task => <li key={task.id}>
        <details>
          <summary>
            <span><strong>{name(task.bot)}</strong><small>{task.instruction.slice(0, 140)}</small><small>{time(task.created)} IST · {task.engine} · {task.stage}</small></span>
            <span className={'status ' + tone(task.status)}>{task.status}</span>
          </summary>
          <div className="task-body">
            {task.error && <p className="control-note">{task.error}</p>}
            {task.has_report ? <p className="assistant-reply">{task.report}</p> : <p className="trace-muted">No report yet.</p>}
            {send && <div className="control-actions">
              {['queued', 'running'].includes(task.status) && <button className="action" disabled={busy} onClick={() => act({ bot_action: 'cancel', id: task.id })}>Stop</button>}
              {['failed', 'interrupted'].includes(task.status) && <button className="action" disabled={busy} onClick={() => act({ bot_action: 'retry', id: task.id })}>Retry</button>}
              {task.status === 'completed' && task.has_report && <button className="action" disabled={busy}
                onClick={() => act({ bot_action: 'report_options', id: task.id })}>Turn into options</button>}
            </div>}
          </div>
        </details>
      </li>)}</ul>
      {view.tasks.length > limit && <div className="panel-foot"><button onClick={() => setLimit(value => value + 8)}>Show more assignments</button></div>}
    </section>
    {resolved.length > 0 && <section className="panel">
      <div className="panel-head"><div><h2>Past decisions</h2><p>Reopen one to choose again.</p></div></div>
      {resolved.map(item => <article className="control-row" key={item.id}>
        <div className="control-row-title"><strong>{item.title}</strong>
          <small>{item.status === 'chosen' ? 'Chose: ' + (item.options.find(o => o.id === item.selected)?.label ?? item.selected) : 'Dismissed'} · {time(item.updated)}</small></div>
        {send && <div className="control-actions"><button className="action" disabled={busy} onClick={() => act({ bot_action: 'reopen_decision', id: item.id })}>Reopen</button></div>}
      </article>)}
    </section>}
  </div>;
}
