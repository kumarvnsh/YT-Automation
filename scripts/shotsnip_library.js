// Shotsnip Clips + Target channels pages.
let libraryLimit = 12, librarySignature = '', targetsSignature = '';
const norm = s => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');

function viewsById() {
  const yt = new Map((D.analytics?.videos || []).map(v => [v.id, v.views]));
  const fb = new Map((D.facebook_analytics?.videos || []).filter(v => v.youtube_id).map(v => [v.youtube_id, v.plays ?? v.initial_plays]));
  return {yt, fb};
}

/* ---------- Clips ---------- */
function renderClipTiles(lib) {
  const day = Date.now() - DAY, recent = lib.filter(c => Date.parse(c.at) > day);
  const errors = (D.runs || []).filter(r => r.errors?.length && /^\d{8}-\d{6}/.test(r.run) &&
    new Date(r.run.slice(0, 4) + '-' + r.run.slice(4, 6) + '-' + r.run.slice(6, 8) + 'T' + r.run.slice(9, 11) + ':' + r.run.slice(11, 13)).getTime() > day).length;
  $('clTiles').innerHTML = [
    tile('Finished · 24h', n0(recent.length), 'clips from every source'),
    tile('Published · 24h', n0(recent.filter(c => c.published?.status === 'uploaded').length), 'YouTube + Facebook'),
    tile('Kept local · 24h', n0(recent.filter(c => !c.published).length), 'never uploaded'),
    tile('Capture errors · 24h', n0(errors), errors ? '<a href="#clips" data-open-runs>see runs</a>' : 'none recorded'),
  ].join('');
}

function workerCard(kind, state, title, lines) {
  return '<article class="cl-worker"><div class="cl-worker-head"><span class="live-status ' + state + '"><span class="live-dot" aria-hidden="true"></span><span class="live-state">' +
    {running: 'WORKING', idle: 'IDLE', paused: 'PAUSED', error: 'ERROR', off: 'STOPPED'}[state] + '</span></span><span class="eyebrow">' + esc(kind) + '</span></div><h3>' + esc(title) + '</h3>' +
    lines.map(l => '<p class="shot-muted">' + l + '</p>').join('') + '</article>';
}

function renderWorkers() {
  const s = D.scheduler || {}, c = D.scheduler_config || {}, busy = Q_BUSY.includes(s.phase);
  const auto = workerCard('Automatic pipeline', busy ? 'running' : !c.enabled ? 'paused' : s.phase === 'error' ? 'error' : 'idle',
    busy ? (Q_STEPS[qStepIndex(s)]?.[1] || 'Working') + (s.selected?.channel ? ' · ' + s.selected.channel : '') : !c.enabled ? 'Paused' : s.phase === 'error' ? 'Last run failed' : 'Waiting for the next cycle',
    [esc(s.note || '—'), 'Publishes to YouTube + Facebook every ' + ((c.scan_minutes || 180) / 60) + 'h · <a href="#jobs">Work queue →</a>']);
  const svc = D.service || {}, fresh = svc.checked_at && Date.now() - Date.parse(svc.checked_at) < 15 * 60e3, src = svc.source || {};
  const state = !fresh ? 'off' : /captur|record|watch/.test(svc.status || '') ? 'running' : svc.status === 'error' ? 'error' : 'idle';
  const monitor = workerCard('Live chat-spike monitor', state,
    !fresh ? 'Not running' : src.creator ? 'Watching ' + src.creator : String(svc.status || 'Idle').replaceAll('_', ' '),
    [fresh ? (src.watching ? n0(src.watching) + ' watching live · ' : '') + 'checked ' + esc(ageText(Date.parse(svc.checked_at))) : 'Last check-in ' + esc(svc.checked_at ? ageText(Date.parse(svc.checked_at)) : 'never'),
     'Makes local clips when chat spikes. ' + (svc.publishing ? 'Publishing is on.' : 'Never uploads.')]);
  $('clWorkers').innerHTML = auto + monitor;
}

function runTime(run) {
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(run || '');
  return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) : null;
}
function friendlyError(text) {
  const t = String(text || '');
  if (/PermissionError|being used by another process/i.test(t)) return 'Recording file was locked by the recorder';
  if (/getaddrinfo|NameResolution|resolve/i.test(t)) return 'Network lookup failed (DNS)';
  if (/not currently live|live event will begin/i.test(t)) return 'Stream was not live';
  if (/timed? ?out/i.test(t)) return 'Timed out';
  return t.split('\n')[0].slice(0, 120);
}
function renderRuns() {
  const rows = (D.runs || []).slice(0, 25);
  $('clRuns').innerHTML = '<table><thead><tr><th>When</th><th>Streamer</th><th>Worker</th><th>Result</th></tr></thead><tbody>' + rows.map(r => {
    const at = runTime(r.run), finished = (r.clips || []).filter(c => c.finished).length;
    const result = r.errors?.length ? '<span class="clip-badge attention">Error</span> <span class="shot-muted" title="' + esc(r.errors.join('\n')) + '">' + esc(friendlyError(r.errors[0])) + '</span>'
      : finished ? '<span class="clip-badge published">' + finished + ' clip' + (finished > 1 ? 's' : '') + '</span>' : '<span class="clip-badge local">No clip</span>';
    return '<tr><td>' + esc(at ? qTime(at.getTime() / 1000) : r.run) + '</td><td>' + esc(r.creator || '—') + '</td><td>' + (r.worker === 'automatic' ? 'Automatic' : 'Live monitor') + '</td><td>' + result + '</td></tr>';
  }).join('') + '</tbody></table>';
}

function libraryCard(c, views) {
  const pub = c.published, yt = pub?.video_id ? views.yt.get(pub.video_id) : null, fb = pub?.video_id ? views.fb.get(pub.video_id) : null;
  const badge = pub?.status === 'uploaded' ? '<span class="clip-badge published">✓ Published</span>' : pub ? '<span class="clip-badge attention">Upload ' + esc(pub.status) + '</span>' : '<span class="clip-badge local">Local only</span>';
  return '<article class="cl-card"><video controls preload="metadata" src="/api/shotsnip/media?file=' + encodeURIComponent(c.file) + '#t=1"></video><div class="cl-body"><p class="clip-origin">' + badge +
    '<span>' + esc(c.origin) + '</span></p><h3>' + esc(c.title) + '</h3><p class="shot-muted">' + esc(c.creator || 'Unknown source') + ' · ' + esc(ageText(Date.parse(c.at))) + '</p>' +
    (c.intro ? '<p class="ov-intro">“' + esc(c.intro) + '”</p>' : '') + (c.error ? '<p class="shot-error">' + esc(c.error) + '</p>' : '') +
    (pub?.video_id ? '<div class="cl-stats"><span><i class="sw yt"></i>' + n0(yt) + ' views</span><span><i class="sw fb"></i>' + n0(fb) + ' plays</span></div>' : '') +
    '<div class="ov-card-links">' + (pub?.video_id ? '<a href="https://www.youtube.com/shorts/' + encodeURIComponent(pub.video_id) + '" target="_blank" rel="noopener">YouTube ↗</a>' : '') +
    (pub?.reel_id ? '<a href="https://www.facebook.com/reel/' + encodeURIComponent(pub.reel_id) + '/" target="_blank" rel="noopener">Facebook ↗</a>' : '') +
    (c.source_url ? '<a href="' + esc(c.source_url) + '" target="_blank" rel="noopener">Source ↗</a>' : '') + '</div></div></article>';
}

function renderLibrary() {
  const lib = D.clip_library || [], status = document.querySelector('input[name=clStatus]:checked')?.value || 'all', origin = $('clOrigin').value, q = ($('clSearch').value || '').toLowerCase();
  const rows = lib.filter(c => (status === 'all' || (status === 'published' ? c.published?.status === 'uploaded' : !c.published)) &&
    (origin === 'all' || c.origin === origin) && [c.title, c.creator, c.intro].join(' ').toLowerCase().includes(q));
  const signature = JSON.stringify([rows.map(r => r.file + (r.published?.video_id || '')), libraryLimit, D.analytics?.checked_at]);
  $('clCount').textContent = rows.length + ' of ' + lib.length + ' clips';
  if (signature === librarySignature) return;
  librarySignature = signature;
  const views = viewsById();
  $('clGrid').innerHTML = rows.slice(0, libraryLimit).map(c => libraryCard(c, views)).join('') || '<p class="shot-muted">No clips match these filters.</p>';
  $('clMore').hidden = rows.length <= libraryLimit;
  $('clMore').textContent = 'Show ' + Math.min(12, rows.length - libraryLimit) + ' more';
}

/* ---------- Targets ---------- */
function targetRows() {
  // Replays carry total replay views in `viewers`; only real live streams count as live.
  const streams = (D.scheduler?.streams || []).filter(s => !s.archive && s.metric !== 'replay_views' && !s.recent_replay), history = D.scheduler?.capture_history || [], cooldown = (D.scheduler_config?.streamer_cooldown_hours || 6) * 3600;
  const clips = typeof clipRows === 'function' ? clipRows() : [], now = Date.now() / 1000;
  return (D.targets || []).map(t => {
    const keys = [norm(t.name), norm(t.id), ...(t.sources || []).map(s => norm(s.handle))].filter(Boolean);
    const match = name => { const n = norm(name); return n && keys.some(k => n === k || n.includes(k) || k.includes(n)); };
    const live = streams.filter(s => match(s.channel)).sort((a, b) => (b.viewers || 0) - (a.viewers || 0))[0];
    const last = history.filter(h => match(h.source?.channel)).reduce((m, h) => Math.max(m, h.at || 0), 0);
    const mine = clips.filter(c => match(c.creator));
    return {t, live, last, coolLeft: last ? Math.max(0, last + cooldown - now) : 0, clips: mine.length,
      yt: median(mine.map(c => c.yt)), fb: median(mine.filter(c => c.fb != null).map(c => c.fb)), priority: D.policy?.source_priorities?.[t.name]};
  }).sort((a, b) => (b.t.enabled - a.t.enabled) || (!!b.live - !!a.live) || (b.clips - a.clips) || a.t.name.localeCompare(b.t.name));
}

function renderTargets() {
  const rows = targetRows(), q = norm($('tgSearch').value);
  $('tgTiles').innerHTML = [
    tile('Targets', n0(rows.length), 'streamers on the list'),
    tile('Enabled', n0(rows.filter(r => r.t.enabled).length), n0(rows.filter(r => !r.t.enabled).length) + ' paused'),
    tile('Live right now', n0(rows.filter(r => r.live && r.t.enabled).length), 'from the last stream scan'),
    tile('Resting', n0(rows.filter(r => r.coolLeft > 0).length), (D.scheduler_config?.streamer_cooldown_hours || 6) + 'h cooldown after a capture'),
  ].join('');
  const shown = rows.filter(r => !q || norm(r.t.name).includes(q));
  const signature = JSON.stringify(shown.map(r => [r.t.id, r.t.enabled, r.live?.viewers, Math.ceil(r.coolLeft / 600), r.clips, r.yt, r.fb, r.priority]));
  if (signature === targetsSignature) return;
  targetsSignature = signature;
  $('targetsList').innerHTML = '<div class="tg-head" aria-hidden="true"><span>Streamer</span><span>Status</span><span class="num">Clips</span><span class="num">YouTube median</span><span class="num">Facebook median</span><span>Last clipped</span><span>Active</span></div>' +
    shown.map(({t, live, last, coolLeft, clips, yt, fb, priority}) => {
      const status = !t.enabled ? '<span class="clip-badge local">Paused</span>'
        : live ? '<span class="clip-badge live">● Live</span> <span class="shot-muted">' + n0(live.viewers) + ' watching' + (live.platform ? ' · ' + esc(live.platform) : '') + '</span>'
        : coolLeft > 0 ? '<span class="clip-badge attention-soft">Resting</span> <span class="shot-muted">' + esc(qClock(coolLeft)) + ' left</span>' : '<span class="clip-badge">Offline</span>';
      return '<div class="tg-row' + (t.enabled ? '' : ' off') + '"><div class="tg-name"><b>' + esc(t.name) + '</b><span>' + (t.sources || []).map(s => '<a href="' + esc(s.url) + '" target="_blank" rel="noopener">' + esc(s.platform) + ' ↗</a>').join('') +
        (priority && priority !== 1 ? '<span class="pf-tag">' + n0(priority) + '× priority</span>' : '') + '</span></div><div class="tg-status">' + status + '</div><div class="num" data-label="Clips">' + n0(clips) +
        '</div><div class="num" data-label="YouTube median">' + n0(yt) + '</div><div class="num" data-label="Facebook median">' + n0(fb) + '</div><div class="tg-last" data-label="Last clipped">' + (last ? esc(ageText(last * 1000)) : '—') +
        '</div><div><button type="button" class="tg-switch" role="switch" aria-checked="' + t.enabled + '" aria-label="' + esc((t.enabled ? 'Pause ' : 'Resume ') + t.name) + '" data-target="' + esc(t.id) + '" data-enabled="' + !t.enabled + '"><span></span></button></div></div>';
    }).join('') + (shown.length ? '' : '<p class="shot-muted">No streamers match.</p>');
}

function renderLibraryPages() {
  if (!$('clGrid')) return;
  const lib = D.clip_library || [];
  renderClipTiles(lib); renderWorkers(); renderRuns(); renderLibrary(); renderTargets();
}

function initLibraryPages() {
  document.querySelectorAll('input[name=clStatus]').forEach(r => { r.onchange = () => { libraryLimit = 12; renderLibrary(); }; });
  $('clOrigin').onchange = () => { libraryLimit = 12; renderLibrary(); };
  $('clSearch').oninput = () => { libraryLimit = 12; renderLibrary(); };
  $('clMore').onclick = () => { libraryLimit += 12; renderLibrary(); };
  $('tgSearch').oninput = () => { targetsSignature = ''; renderTargets(); };
  $('clTiles').addEventListener('click', e => { if (e.target.closest('[data-open-runs]')) { e.preventDefault(); const d = document.querySelector('.cl-runs'); d.open = true; d.scrollIntoView({behavior: 'smooth'}); } });
}
initLibraryPages();
