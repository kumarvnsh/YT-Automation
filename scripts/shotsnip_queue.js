// Shotsnip work queue: what is running, what runs next, and the controls that shape the plan.
let queueTab = 'active', queueJobsSignature = '', queueHistorySignature = '';
const Q_BUSY = ['scanning', 'capturing', 'analyzing', 'finishing', 'uploading'];
const Q_STEPS = [
  ['find', 'Find stream', 'Checks target channels, live GTA streams and recent replays'],
  ['record', 'Record', 'Captures the stream window'],
  ['pick', 'Pick moment', 'Clips Kitty scores the recording and picks 30 seconds'],
  ['check', 'Check footage', 'The local reviewer confirms a real GTA moment'],
  ['edit', 'Edit + intro', 'Layout, captions and the spoken intro'],
  ['publish', 'Publish', 'YouTube, then the Facebook Reel'],
];
const Q_ACTIVE_JOB = ['running', 'publishing'], Q_WAITING_JOB = ['queued', 'queued_publish'];
const Q_TABS = [
  ['active', 'Active', j => Q_ACTIVE_JOB.includes(j.status) || Q_WAITING_JOB.includes(j.status)],
  ['ready', 'Ready to publish', j => j.status === 'ready'],
  ['problems', 'Needs attention', j => ['failed', 'attention', 'partial'].includes(j.status)],
  ['done', 'Done', j => ['published', 'cancelled'].includes(j.status)],
];

function qClock(seconds) {
  if (seconds <= 0) return 'now';
  const h = Math.floor(seconds / 3600), m = Math.floor(seconds % 3600 / 60), s = Math.floor(seconds % 60);
  return h ? h + 'h ' + String(m).padStart(2, '0') + 'm' : m ? m + 'm ' + String(s).padStart(2, '0') + 's' : s + 's';
}
function qTime(epoch) {
  const d = new Date(epoch * 1000), today = new Date();
  const time = d.toLocaleTimeString(undefined, {hour: 'numeric', minute: '2-digit'});
  const day = offset => new Date(today.getTime() + offset * 864e5).toDateString();
  return d.toDateString() === day(0) ? 'Today ' + time : d.toDateString() === day(1) ? 'Tomorrow ' + time
    : d.toDateString() === day(-1) ? 'Yesterday ' + time
    : d.toLocaleDateString(undefined, {month: 'short', day: 'numeric'}) + ' ' + time;
}
function qAgo(epoch) { return epoch ? qClock(Date.now() / 1000 - epoch) + ' ago' : 'never'; }
function qVoiceMode(config) { const c = config.commentary || {}; return c.enabled ? (c.mode || 'intro') : 'off'; }
function qCycleBase(s) { return Math.max(s.last_cycle_at || 0, s.last_upload_at || 0); }

// Map the worker's coarse phase + note onto the six visible steps.
function qStepIndex(s) {
  const phase = s.phase, note = (s.note || '').toLowerCase();
  if (phase === 'scanning') return 0;
  if (phase === 'capturing') return 1;
  if (phase === 'analyzing') return /footage|gta moment|gameplay peak/.test(note) ? 3 : 2;
  if (phase === 'finishing') return 4;
  if (phase === 'uploading') return 5;
  if (phase === 'published') return 6;
  return -1;
}

function qBlockers() {
  const s = D.scheduler || {}, c = D.scheduler_config || {}, svc = D.scheduler_service || {}, now = Date.now() / 1000, out = [];
  const held = (s.uploads || []).filter(u => ['pending', 'uncertain'].includes(u.status));
  if (held.length) out.push(['bad', 'Publishing is held', 'YouTube did not confirm an upload started ' + date(held[0].started_at) +
    '. Check YouTube Studio for “' + (held[0].marker || 'the Shotsnip marker') + '” before anything else publishes.']);
  if (!c.enabled) out.push(['warn', 'Automatic clipping is paused', 'Manual jobs still run. Turn automatic clipping back on in Controls to resume the plan.']);
  if (!svc.checked_at || now - svc.checked_at > 90) out.push(['bad', 'The scheduler is not running', 'The dashboard dispatcher has not checked in ' + qAgo(svc.checked_at) + '. Restart the dashboard to resume work.']);
  else if (svc.status === 'retry_wait' && svc.retry_at > now) out.push(['warn', 'Waiting before a retry', 'The last run exited with an error. The worker retries in ' + qClock(svc.retry_at - now) + '.']);
  if (s.phase === 'error' && s.error) out.push(['warn', 'The last automatic run failed', s.error.slice(0, 220)]);
  if (Q_BUSY.includes(s.phase) && now - (s.heartbeat_at || 0) > 1800) out.push(['bad', 'The current run looks stuck', 'No progress for ' + qAgo(s.heartbeat_at) + ' while ' + s.phase + '.']);
  return out;
}

function qActiveManual() { return (D.manual_jobs || []).find(j => Q_ACTIVE_JOB.includes(j.status)); }

function renderQueueNow() {
  const s = D.scheduler || {}, c = D.scheduler_config || {}, manual = qActiveManual(), now = Date.now() / 1000;
  let title, detail, index, source = s.selected, kind;
  if (manual) {
    kind = 'Manual job';
    title = manual.result?.title || manual.options?.hook || manual.result?.source?.channel || 'Manual clip';
    detail = manual.note || manual.stage;
    index = manual.status === 'publishing' ? 5 : qStepIndex({phase: manual.stage, note: manual.note});
    source = manual.result?.source || null;
  } else if (Q_BUSY.includes(s.phase)) {
    kind = 'Automatic cycle';
    title = source?.channel ? 'Clipping ' + source.channel : 'Looking for a stream';
    detail = s.note; index = qStepIndex(s);
  }
  const track = '<ol class="q-track">' + Q_STEPS.map(([, label, help], i) =>
    '<li class="' + (index > i ? 'done' : index === i ? 'active' : '') + '" title="' + esc(help) + '"><span>' + (index > i ? '✓' : i + 1) + '</span>' + esc(label) + '</li>').join('') + '</ol>';
  if (!title) {
    const next = qCycleBase(s) + (c.scan_minutes || 180) * 60;
    $('qNow').innerHTML = '<span class="eyebrow">NOW</span><div class="q-now-head"><div><h2>' +
      (c.enabled ? 'Idle until the next cycle' : 'Idle — automatic clipping paused') + '</h2><p class="shot-muted">' +
      esc(s.note || 'Waiting for the next scheduled check') + '</p></div>' +
      (c.enabled ? '<div class="q-countdown"><span>Next cycle</span><strong data-q-until="' + next + '">' + qClock(next - now) + '</strong></div>' : '') +
      '</div>' + track.replace(/class="(done|active)"/g, 'class=""') +
      (D.run_now_requested ? '<p class="q-flag">A run-now request is waiting for the worker. <button type="button" data-q-cancel-run>Withdraw</button></p>' : '');
    return;
  }
  const started = manual ? manual.updated : s.phase_started_at;
  $('qNow').innerHTML = '<span class="eyebrow">NOW · ' + esc(kind.toUpperCase()) + '</span><div class="q-now-head"><div><h2><span class="pulse"></span>' + esc(title) +
    '</h2><p>' + esc(detail || '') + '</p></div><div class="q-countdown"><span>In this stage</span><strong data-q-since="' + (started || now) + '">' +
    qClock(now - (started || now)) + '</strong></div></div>' + track +
    '<p class="shot-muted">' + (source?.channel ? 'Source: ' + esc(source.channel) + (source.viewers ? ' · ' + fmt(source.viewers) + (source.archive ? ' replay views' : ' watching live') : '') + ' · ' : '') +
    'Last progress ' + esc(qAgo(manual ? manual.updated : s.heartbeat_at)) + '</p>' +
    (!manual && (s.events || []).length ? '<details class="q-events"><summary>Stage log</summary>' + s.events.slice(-6).reverse().map(e =>
      '<p><time>' + esc(new Date(e.at * 1000).toLocaleTimeString()) + '</time> ' + esc(e.note) + '</p>').join('') + '</details>' : '');
}

function renderQueuePlan() {
  const s = D.scheduler || {}, c = D.scheduler_config || {}, now = Date.now() / 1000, items = [];
  const waiting = (D.manual_jobs || []).filter(j => Q_WAITING_JOB.includes(j.status)).sort((a, b) => a.created - b.created);
  waiting.forEach((j, i) => items.push({kind: 'manual', when: i === 0 ? (qActiveManual() || Q_BUSY.includes(s.phase) ? 'After the current job' : 'Next') : 'Then',
    title: j.options?.hook || (j.status === 'queued_publish' ? 'Publish ready preview' : j.options?.mode === 'auto' ? 'Find a stream for me' : 'Clip from ' + (j.options?.url || 'link')),
    detail: (j.status === 'queued_publish' ? 'Publishes to ' : (j.options?.publish ? 'Clips and publishes to ' : 'Makes a preview for ')) + (j.options?.platforms || []).join(' + '),
    job: j, first: i === 0}));
  const voice = {off: 'no spoken intro', intro: 'spoken intro', full: 'narrated breakdown'}[qVoiceMode(c)];
  if (c.enabled) {
    const interval = (c.scan_minutes || 180) * 60;
    let next = qCycleBase(s) + interval;
    if (D.run_now_requested) items.push({kind: 'auto', when: 'As soon as the worker is free', title: 'Automatic cycle (requested now)', detail: 'Find stream → record → pick → check → ' + voice + ' → YouTube + Facebook'});
    for (let n = 0; n < 4 && next < now + 86400; n++, next += interval) {
      items.push({kind: 'auto', when: next <= now ? (waiting.length ? 'Due — after manual jobs' : 'Due now') : qTime(next), until: next,
        title: 'Automatic cycle', detail: 'Find stream → record → pick → check → ' + voice + ' → YouTube + Facebook'});
    }
  }
  $('qPlanNote').textContent = c.enabled ? 'Every ' + ((c.scan_minutes || 180) / 60) + 'h · times are estimates' : 'Automatic clipping paused';
  $('qPlan').innerHTML = items.map(it => '<li class="q-item ' + it.kind + '"><div class="q-when">' + esc(it.when) +
    (it.until && it.until > now ? '<span data-q-until="' + it.until + '">in ' + qClock(it.until - now) + '</span>' : '') + '</div><div class="q-what"><span class="q-kind">' +
    (it.kind === 'manual' ? 'Manual' : 'Auto') + '</span><b>' + esc(it.title) + '</b><p class="shot-muted">' + esc(it.detail) + '</p></div><div class="q-item-actions">' +
    (it.job ? (it.first ? '' : '<button type="button" data-job-id="' + esc(it.job.id) + '" data-job-action="prioritize">Move to front</button>') +
      '<button type="button" data-job-id="' + esc(it.job.id) + '" data-job-action="cancel">Cancel</button>' : '') + '</div></li>').join('') ||
    '<li class="q-empty"><b>Nothing is planned.</b><p class="shot-muted">Turn on automatic clipping or create a manual clip.</p></li>';
}

function qJobCard(j) {
  const o = j.options || {}, r = j.result || {}, p = r.publication || {}, fb = p.facebook, active = [...Q_WAITING_JOB, 'running'].includes(j.status);
  const button = (label, action, extra = '') => '<button type="button" ' + extra + ' data-job-id="' + esc(j.id) + '" data-job-action="' + action + '">' + label + '</button>';
  return '<article class="job-card"><div class="job-head"><div><span class="eyebrow">' + esc(o.mode) + ' · ' + esc(date(j.created * 1000)) + '</span><h3>' +
    esc(r.title || o.hook || r.source?.channel || 'Clipping job') + '</h3></div><span class="job-badge ' + esc(j.status) + '">' + esc(j.status.replaceAll('_', ' ')) + '</span></div>' +
    '<p>' + esc(j.note || j.stage) + '</p><p class="shot-muted">' + esc(o.url || 'Automatic source discovery') + (o.window ? ' · ' + o.window.join('–') + ' seconds' : '') +
    '<br>Destinations: ' + esc((o.platforms || []).join(' + ')) + ' · YouTube ' + esc(o.privacy) + '</p>' + (j.error ? '<p class="shot-error">' + esc(j.error) + '</p>' : '') +
    (r.file ? '<video controls preload="none" src="/api/shotsnip/media?file=' + encodeURIComponent(r.file) + '"></video>' : '') + '<div class="job-actions">' +
    (j.status === 'ready' ? button('Publish to selected platforms', 'publish', 'class="primary"') : '') + (j.status === 'failed' ? button('Retry processing', 'retry') : '') +
    (j.status === 'partial' && p.youtube_id && /failed \((400|401|403|404|422)\)/.test(fb?.error || '') ? button('Retry Facebook only', 'retry_facebook') : '') +
    (Q_WAITING_JOB.includes(j.status) ? button('Move to front', 'prioritize') : '') +
    (active ? button(j.status === 'running' ? 'Stop after this stage' : 'Cancel job', 'cancel') : '') +
    (p.youtube_id ? '<a class="button-link" target="_blank" rel="noopener" href="https://www.youtube.com/shorts/' + encodeURIComponent(p.youtube_id) + '">YouTube ↗</a>' : '') +
    (fb?.reel_id ? '<a class="button-link" target="_blank" rel="noopener" href="https://www.facebook.com/reel/' + encodeURIComponent(fb.reel_id) + '/">Facebook ↗</a>' : '') + '</div>' +
    (fb?.status === 'error' ? '<p class="shot-error">Facebook: ' + esc(fb.error) + '</p>' : '') + '</article>';
}

function renderQueueJobs() {
  const jobs = D.manual_jobs || [], counts = Object.fromEntries(Q_TABS.map(([id, , test]) => [id, jobs.filter(test).length]));
  if (queueTab === 'active' && !counts.active) queueTab = counts.ready ? 'ready' : counts.problems ? 'problems' : 'active';
  $('qTabs').innerHTML = Q_TABS.map(([id, label]) => '<button type="button" role="tab" aria-selected="' + (id === queueTab) + '" data-q-tab="' + id + '">' + label +
    ' <span>' + counts[id] + '</span></button>').join('');
  const [, , test] = Q_TABS.find(([id]) => id === queueTab), rows = jobs.filter(test)
    .sort((a, b) => Q_WAITING_JOB.includes(a.status) && Q_WAITING_JOB.includes(b.status) ? a.created - b.created : b.created - a.created);
  const signature = queueTab + JSON.stringify(rows);
  if (signature === queueJobsSignature) return;
  queueJobsSignature = signature;
  $('jobQueue').innerHTML = rows.map(qJobCard).join('') || '<div class="empty-state q-empty-jobs"><h3>' +
    ({active: 'No manual jobs waiting.', ready: 'No previews waiting for you.', problems: 'Nothing needs attention.', done: 'No finished manual jobs yet.'}[queueTab]) +
    '</h3><p class="shot-muted">' + (queueTab === 'active' ? '<a href="#studio">Create a clip</a> to add one ahead of the automatic plan.' : '') + '</p></div>';
}

function renderQueueHistory() {
  const rows = D.auto_history || [], signature = JSON.stringify(rows);
  if (signature === queueHistorySignature) return;
  queueHistorySignature = signature;
  const badge = (status) => '<span class="job-badge ' + ({uploaded: 'published', rejected: 'failed', uncertain: 'attention', pending: 'attention'}[status] || '') + '">' + esc(status || 'unknown') + '</span>';
  const fbCell = f => f?.reel_id ? '<a target="_blank" rel="noopener" href="https://www.facebook.com/reel/' + encodeURIComponent(f.reel_id) + '/">Reel ↗</a>'
    : f?.status === 'error' ? '<span class="shot-error" title="' + esc(f.error) + '">Failed</span>' : '<span class="shot-muted">—</span>';
  $('qHistory').innerHTML = rows.length ? '<div class="shot-table"><table><thead><tr><th>Clip</th><th>When</th><th>YouTube</th><th>Facebook</th></tr></thead><tbody>' + rows.map(r =>
    '<tr><td><b>' + esc(r.title || 'Untitled clip') + '</b><div class="shot-muted">' + esc(r.creator || 'Unknown source') + (r.intro ? ' · “' + esc(r.intro) + '”' : '') + '</div></td><td>' +
    esc(r.at ? qTime(Date.parse(r.at) / 1000) : '—') + '</td><td>' + badge(r.status) + (r.video_id ? ' <a target="_blank" rel="noopener" href="https://www.youtube.com/shorts/' + encodeURIComponent(r.video_id) + '">Watch ↗</a>' : '') +
    (r.rejection_reason ? '<div class="shot-error">' + esc(r.rejection_reason) + '</div>' : '') + '</td><td>' + fbCell(r.facebook) + '</td></tr>').join('') + '</tbody></table></div>'
    : '<p class="shot-muted">No automatic uploads yet.</p>';
}

function renderQueueControls() {
  const c = D.scheduler_config || {}, s = D.scheduler || {}, manual = qActiveManual(), active = document.activeElement;
  if (active !== $('qAuto')) $('qAuto').checked = !!c.enabled;
  $('qAutoNote').textContent = c.enabled ? 'On — new clips are found, edited and published on the schedule below.' : 'Off — the plan stops after the current job. Manual jobs still run.';
  if (active !== $('qCadence')) $('qCadence').value = String(c.scan_minutes || 180);
  const perDay = Math.floor(1440 / (c.scan_minutes || 180));
  $('qCadenceNote').textContent = 'Up to ' + perDay + ' automatic clip' + (perDay === 1 ? '' : 's') + ' a day. Cycles without a good moment post nothing.';
  const mode = qVoiceMode(c);
  document.querySelectorAll('input[name=qVoice]').forEach(r => { r.checked = r.value === mode; });
  $('qVoiceNote').textContent = {off: 'Clips post with only the streamer’s audio.', intro: 'One short spoken line over the opening frame, then the clip plays untouched.',
    full: 'Intro, two freeze-frame explanations and a closing question.'}[mode] + (c.commentary?.voice ? ' Voice: ' + (c.commentary.voice.startsWith('chatterbox') ? 'Chatterbox (local)' : c.commentary.voice) + '.' : '');
  $('qStop').disabled = !Q_BUSY.includes(s.phase) || ['scanning', 'uploading'].includes(s.phase) || !!manual;
  $('qStop').textContent = manual ? 'Stop manual job from its card' : 'Stop after this stage';
  $('qRunNow').disabled = !c.enabled || !!D.run_now_requested;
  $('qRunNow').title = !c.enabled ? 'Turn automatic clipping on first' : D.run_now_requested ? 'Already requested' : 'Find a stream and publish one clip without waiting for the schedule';
}

function renderQueue() {
  const blockers = qBlockers();
  $('qBlockers').innerHTML = blockers.map(([tone, title, text]) => '<div class="q-blocker ' + tone + '"><b>' + esc(title) + '</b><p>' + esc(text) + '</p></div>').join('');
  renderQueueNow(); renderQueuePlan(); renderQueueJobs(); renderQueueHistory(); renderQueueControls();
}

function qTick() {
  const now = Date.now() / 1000;
  document.querySelectorAll('[data-q-until]').forEach(el => { const t = Number(el.dataset.qUntil) - now; el.textContent = (el.closest('.q-when') ? 'in ' : '') + qClock(t); });
  document.querySelectorAll('[data-q-since]').forEach(el => { el.textContent = qClock(now - Number(el.dataset.qSince)); });
}

function initQueue() {
  const run = async (button, fn) => { button.disabled = true; try { await fn(); } catch (err) { fail(err); } finally { button.disabled = false; } };
  $('qRunNow').onclick = e => {
    if (!confirm('Run one automatic cycle now? It finds a stream and publishes the clip publicly to YouTube and Facebook.')) return;
    run(e.currentTarget, () => act('run_auto_now'));
  };
  $('qAuto').onchange = e => act('set_scheduler', {enabled: e.target.checked}).catch(err => { e.target.checked = !e.target.checked; fail(err); });
  $('qCadence').onchange = e => act('set_cadence', {minutes: Number(e.target.value)}).catch(fail);
  document.querySelectorAll('input[name=qVoice]').forEach(r => { r.onchange = () => act('set_commentary', {mode: r.value}).catch(fail); });
  $('qStop').onclick = e => run(e.currentTarget, () => act('stop_auto_after_stage'));
  $('qTabs').onclick = e => { const b = e.target.closest('[data-q-tab]'); if (b) { queueTab = b.dataset.qTab; queueJobsSignature = ''; renderQueueJobs(); } };
  $('jobs').addEventListener('click', async e => {
    const cancelRun = e.target.closest('[data-q-cancel-run]');
    if (cancelRun) return run(cancelRun, () => act('cancel_run_now'));
    const b = e.target.closest('[data-job-action]');
    if (!b) return;
    if (b.dataset.jobAction === 'publish' && !confirm('Publish this preview now to its selected platforms?')) return;
    run(b, () => act('manual_control', {id: b.dataset.jobId, action: b.dataset.jobAction}));
  });
  setInterval(qTick, 1000);
}
initQueue();
