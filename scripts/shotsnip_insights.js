// Shotsnip Overview + Performance: one joined row per clip (YouTube + Facebook + checkpoints + intro).
let perfRange = '30', insightsSignature = '';
const DAY = 864e5;

function median(values) {
  const v = values.filter(x => typeof x === 'number' && isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : Math.round((v[m - 1] + v[m]) / 2);
}
function n0(v) { return v == null ? '—' : Number(v).toLocaleString(undefined, {maximumFractionDigits: 1}); }
function shortDay(t) { return new Date(t).toLocaleDateString(undefined, {month: 'short', day: 'numeric'}); }
function ageText(t) {
  const h = (Date.now() - t) / 36e5;
  return h < 1 ? Math.max(1, Math.round(h * 60)) + 'm ago' : h < 48 ? Math.round(h) + 'h ago' : Math.round(h / 24) + 'd ago';
}
function gameOf(title, hint) {
  if (hint === 'gta6' || /\bgta\s*(6|vi)\b/i.test(title || '')) return 'GTA 6';
  if (hint === 'gta5' || /\bgta\s*(5|v)\b/i.test(title || '')) return 'GTA 5';
  return /\bgta\b/i.test(title || '') || /^gta/.test(hint || '') ? 'GTA' : 'Other';
}

// Join every listed YouTube Short with its Facebook Reel, age checkpoints and upload record.
function clipRows() {
  const fb = new Map((D.facebook_analytics?.videos || []).filter(v => v.youtube_id).map(v => [v.youtube_id, v]));
  const ages = new Map((D.age_performance?.videos || []).map(v => [v.id, v.checkpoints || {}]));
  const history = new Map((D.auto_history || []).filter(h => h.video_id).map(h => [h.video_id, h]));
  return (D.analytics?.videos || []).map(v => {
    const f = fb.get(v.id), h = history.get(v.id), checks = ages.get(v.id) || {};
    const at = Date.parse(v.published_at) || 0;
    return {id: v.id, title: v.title || 'Untitled clip', url: v.url, at, yt: v.views ?? null, likes: v.likes ?? null,
      comments: v.comments ?? null, fb: f ? (f.plays ?? f.initial_plays ?? null) : null, fbReplays: f?.replays ?? null,
      fbId: f?.id, fbUrl: f ? facebookUrl(f) : null, creator: h?.creator || f?.creator || null, intro: h?.intro || null,
      game: gameOf(v.title, h?.game), h1: checks['1']?.views ?? null, h24: checks['24']?.views ?? null};
  }).sort((a, b) => b.at - a.at);
}

function tile(label, value, note, delta) {
  const arrow = delta == null ? '' : delta > 0 ? '<span class="delta up">▲ ' + n0(delta) + '</span>' : delta < 0 ? '<span class="delta down">▼ ' + n0(-delta) + '</span>' : '<span class="delta">no change</span>';
  return '<article class="ov-tile"><span class="eyebrow">' + esc(label) + '</span><strong>' + esc(value) + '</strong><span class="shot-muted">' + note + ' ' + arrow + '</span></article>';
}

function windowSum(rows, from, to, key) { return rows.filter(r => r.at >= from && r.at < to).reduce((n, r) => n + (r[key] || 0), 0); }

function renderOverviewTiles(rows) {
  const now = Date.now(), wk = now - 7 * DAY, prev = now - 14 * DAY;
  const cur = rows.filter(r => r.at >= wk), before = rows.filter(r => r.at >= prev && r.at < wk);
  $('ovRange').textContent = shortDay(wk) + ' – ' + shortDay(now) + ' · compared with the 7 days before';
  $('ovTiles').innerHTML = [
    tile('Clips published', n0(cur.length), 'vs prior week', cur.length - before.length),
    tile('YouTube views', n0(windowSum(rows, wk, now, 'yt')), 'on these clips', windowSum(rows, wk, now, 'yt') - windowSum(rows, prev, wk, 'yt')),
    tile('Facebook plays', n0(windowSum(rows, wk, now, 'fb')), 'includes replays', windowSum(rows, wk, now, 'fb') - windowSum(rows, prev, wk, 'fb')),
    tile('Subscribers', n0(D.analytics?.subscribers), 'YouTube, current'),
  ].join('');
}

function niceMax(v) { if (v <= 10) return 10; const p = 10 ** Math.floor(Math.log10(v)), m = v / p; return (m <= 2 ? 2 : m <= 5 ? 5 : 10) * p; }

function renderReach(rows) {
  const days = [], today = new Date(); today.setHours(0, 0, 0, 0);
  const first = rows.filter(r => r.at > today.getTime() - 13 * DAY).reduce((m, r) => Math.min(m, r.at), Infinity);
  const span = isFinite(first) ? Math.max(6, Math.ceil((today.getTime() - new Date(first).setHours(0, 0, 0, 0)) / DAY)) : 13;
  for (let i = Math.min(13, span); i >= 0; i--) {
    const start = today.getTime() - i * DAY, list = rows.filter(r => r.at >= start && r.at < start + DAY);
    days.push({start, clips: list.length, yt: list.reduce((n, r) => n + (r.yt || 0), 0), fb: list.reduce((n, r) => n + (r.fb || 0), 0)});
  }
  const top = niceMax(Math.max(1, ...days.map(d => Math.max(d.yt, d.fb))));
  const ticks = [0, top / 2, top];
  $('ovReach').innerHTML = '<div class="reach-plot"><div class="reach-axis">' + ticks.slice().reverse().map(t => '<span>' + n0(t) + '</span>').join('') +
    '</div><div class="reach-bars" style="grid-template-columns:repeat(' + days.length + ',minmax(0,1fr))">' + ticks.map(t => '<i class="grid" style="bottom:' + (t / top * 100) + '%"></i>').join('') + days.map(d =>
      '<button type="button" class="reach-day" data-tip="' + esc(shortDay(d.start) + ' · ' + d.clips + ' clip' + (d.clips === 1 ? '' : 's') + '\nYouTube: ' + n0(d.yt) + ' views\nFacebook: ' + n0(d.fb) + ' plays') +
      '" aria-label="' + esc(shortDay(d.start) + ': ' + d.clips + ' clips, ' + d.yt + ' YouTube views, ' + d.fb + ' Facebook plays') + '"><span class="bar yt" style="height:' + (d.yt / top * 100) +
      '%"></span><span class="bar fb" style="height:' + (d.fb / top * 100) + '%"></span><em>' + esc(shortDay(d.start)) + '</em></button>').join('') + '</div></div>';
  $('ovReachTable').innerHTML = '<table><thead><tr><th>Upload day</th><th>Clips</th><th>YouTube views</th><th>Facebook plays</th></tr></thead><tbody>' +
    days.slice().reverse().map(d => '<tr><td>' + shortDay(d.start) + '</td><td>' + d.clips + '</td><td>' + n0(d.yt) + '</td><td>' + n0(d.fb) + '</td></tr>').join('') + '</tbody></table>';
}

function signal(tone, title, text) {
  const icon = {good: '✓', warn: '!', bad: '✕', info: 'i'}[tone], label = {good: 'Good', warn: 'Watch', bad: 'Problem', info: 'Note'}[tone];
  return '<div class="sig ' + tone + '"><span class="sig-icon" aria-hidden="true">' + icon + '</span><div><span class="sig-label">' + label + '</span><b>' + esc(title) + '</b><p>' + esc(text) + '</p></div></div>';
}

function renderSignals(rows) {
  const out = [], recent = rows.filter(r => r.at > Date.now() - 30 * DAY).slice(0, 10);
  if (!recent.length) { $('ovSignals').innerHTML = signal('info', 'No recent clips', 'Signals appear once clips have been published in the last 30 days.'); return; }
  const mYt = median(recent.map(r => r.yt)), mFb = median(recent.filter(r => r.fb != null).map(r => r.fb));
  if (mFb != null && mYt != null) {
    out.push(mFb > mYt * 2 && mFb >= 10 ? signal('info', 'Facebook is carrying reach', 'Last ' + recent.length + ' clips: median ' + n0(mFb) + ' Facebook plays vs ' + n0(mYt) + ' YouTube views per clip.')
      : mYt > mFb * 2 && mYt >= 10 ? signal('good', 'YouTube is leading', 'Last ' + recent.length + ' clips: median ' + n0(mYt) + ' YouTube views vs ' + n0(mFb) + ' Facebook plays per clip.')
      : signal('info', 'Platforms are level', 'Last ' + recent.length + ' clips: median ' + n0(mYt) + ' YouTube views and ' + n0(mFb) + ' Facebook plays per clip.'));
  }
  const settled = recent.filter(r => Date.now() - r.at > 6 * 36e5), low = settled.filter(r => (r.yt || 0) < 5).length;
  if (settled.length >= 4 && low / settled.length >= .6) out.push(signal('bad', 'YouTube is barely showing recent Shorts', low + ' of the last ' + settled.length + ' clips older than 6 hours have under 5 YouTube views. YouTube limits reach for re-uploaded clips without the uploader’s own contribution.'));
  else if (settled.length >= 4 && low === 0) out.push(signal('good', 'Every recent Short is being shown', 'All of the last ' + settled.length + ' settled clips passed 5 YouTube views.'));
  const week = rows.filter(r => r.at > Date.now() - 7 * DAY), best = week.slice().sort((a, b) => ((b.yt || 0) + (b.fb || 0)) - ((a.yt || 0) + (a.fb || 0)))[0];
  if (best && (best.yt || best.fb)) out.push(signal('good', 'Best clip this week', '“' + best.title + '” — ' + n0(best.yt) + ' YouTube views, ' + n0(best.fb) + ' Facebook plays (' + ageText(best.at) + ').'));
  const withIntro = rows.filter(r => r.intro && Date.now() - r.at > 6 * 36e5), without = rows.filter(r => !r.intro && r.at > Date.now() - 30 * DAY && Date.now() - r.at > 6 * 36e5);
  if (withIntro.length < 5) out.push(signal('info', 'Spoken intro: too early to judge', withIntro.length + ' clip' + (withIntro.length === 1 ? '' : 's') + ' with an intro are older than 6 hours. A fair comparison needs at least 5.' +
    (withIntro.length ? ' So far: median ' + n0(median(withIntro.map(r => r.yt))) + ' YouTube views vs ' + n0(median(without.map(r => r.yt))) + ' without.' : '')));
  else {
    const a = median(withIntro.map(r => r.yt)), b = median(without.map(r => r.yt));
    out.push(signal(a > b ? 'good' : 'warn', a > b ? 'Spoken intro clips are ahead on YouTube' : 'Spoken intro is not helping yet', 'Median ' + n0(a) + ' YouTube views with an intro (n=' + withIntro.length + ') vs ' + n0(b) + ' without (n=' + without.length + ').'));
  }
  $('ovSignals').innerHTML = out.join('');
}

function clipCard(r) {
  return '<article class="ov-card"><span class="eyebrow">' + esc(r.game) + ' · ' + esc(ageText(r.at)) + '</span><h3>' + esc(r.title) + '</h3><p class="shot-muted">' + esc(r.creator || 'Unknown source') + '</p>' +
    (r.intro ? '<p class="ov-intro">“' + esc(r.intro) + '”</p>' : '') + '<div class="ov-card-stats"><div><b>' + n0(r.yt) + '</b><span><i class="sw yt"></i>YouTube views</span></div><div><b>' + n0(r.fb) +
    '</b><span><i class="sw fb"></i>Facebook plays</span></div></div><div class="ov-card-links"><a href="' + esc(r.url) + '" target="_blank" rel="noopener">YouTube ↗</a>' + (r.fbUrl ? '<a href="' + esc(r.fbUrl) + '" target="_blank" rel="noopener">Facebook ↗</a>' : '') + '</div></article>';
}

function renderOverview() {
  const rows = clipRows();
  renderOverviewTiles(rows); renderReach(rows); renderSignals(rows);
  $('ovLatest').innerHTML = rows.slice(0, 6).map(clipCard).join('') || '<p class="shot-muted">No clips published yet.</p>';
}

function filteredRows() {
  const q = ($('videoSearch').value || '').toLowerCase(), sort = $('videoSort').value, since = perfRange === 'all' ? 0 : Date.now() - Number(perfRange) * DAY;
  const rows = clipRows().filter(r => r.at >= since && [r.title, r.creator, r.intro].join(' ').toLowerCase().includes(q));
  const by = {recent: (a, b) => b.at - a.at, views: (a, b) => (b.yt ?? -1) - (a.yt ?? -1), plays: (a, b) => (b.fb ?? -1) - (a.fb ?? -1), low: (a, b) => (a.yt ?? Infinity) - (b.yt ?? Infinity)}[sort] || ((a, b) => b.at - a.at);
  return rows.sort(by);
}

function renderPerfTiles(rows) {
  const zero = rows.filter(r => (r.yt || 0) === 0 && Date.now() - r.at > 6 * 36e5).length, settled = rows.filter(r => Date.now() - r.at > 6 * 36e5).length;
  $('pfTiles').innerHTML = [
    tile('Clips', n0(rows.length), perfRange === 'all' ? 'all listed clips' : 'last ' + perfRange + ' days'),
    tile('Median YouTube views', n0(median(rows.map(r => r.yt))), 'per clip'),
    tile('Median Facebook plays', n0(median(rows.filter(r => r.fb != null).map(r => r.fb))), 'per clip, includes replays'),
    tile('Zero YouTube views', settled ? Math.round(zero / settled * 100) + '%' : '—', zero + ' of ' + settled + ' clips older than 6h'),
  ].join('');
}

function renderPerfChart(rows) {
  const list = rows.slice(0, 20), top = niceMax(Math.max(1, ...list.map(r => Math.max(r.yt || 0, r.fb || 0))));
  $('pfChartNote').textContent = list.length ? 'Showing ' + list.length + ' of ' + rows.length + ' clips in the current order. Scale 0–' + n0(top) + '.' : '';
  $('pfChart').innerHTML = list.map(r => '<div class="pf-row" tabindex="0" data-tip="' + esc(r.title + '\n' + (r.creator ? r.creator + ' · ' : '') + ageText(r.at) + '\nYouTube: ' + n0(r.yt) + ' views\nFacebook: ' + n0(r.fb) + ' plays') + '">' +
    '<span class="pf-label">' + esc(r.title) + '</span><span class="pf-bars"><span class="hbar yt" style="width:' + ((r.yt || 0) / top * 100) + '%"></span><span class="hbar fb" style="width:' + ((r.fb || 0) / top * 100) + '%"></span></span></div>').join('') ||
    '<p class="shot-muted">No clips match these filters.</p>';
}

function renderPerfTable(rows) {
  $('videoTable').innerHTML = '<table><thead><tr><th>Clip</th><th>Published</th><th class="num">YouTube views</th><th class="num">Likes</th><th class="num">At 1h / 24h</th><th class="num">Facebook plays</th><th class="num">Replays</th><th>Links</th></tr></thead><tbody>' +
    rows.map(r => '<tr><td><b>' + esc(r.title) + '</b><div class="shot-muted">' + esc([r.creator, r.game].filter(Boolean).join(' · ')) + (r.intro ? ' · <span class="pf-tag">Intro</span>' : '') + '</div></td><td>' + esc(shortDay(r.at)) +
      '<div class="shot-muted">' + esc(ageText(r.at)) + '</div></td><td class="num">' + n0(r.yt) + '</td><td class="num">' + n0(r.likes) + '</td><td class="num">' + n0(r.h1) + ' / ' + n0(r.h24) + '</td><td class="num">' + n0(r.fb) +
      '</td><td class="num">' + n0(r.fbReplays) + '</td><td><a href="' + esc(r.url) + '" target="_blank" rel="noopener">YT ↗</a>' + (r.fbUrl ? ' <a href="' + esc(r.fbUrl) + '" target="_blank" rel="noopener">FB ↗</a>' : '') + '</td></tr>').join('') +
    '</tbody></table>' + (rows.length ? '' : '<p class="shot-muted">No clips match these filters.</p>');
  const a = D.analytics || {};
  $('coverage').textContent = rows.length + ' clips shown · YouTube counts checked ' + date(a.checked_at) + ' · Facebook checked ' + date(D.facebook_analytics?.checked_at) + '. “—” means not measured, not zero.';
}

function renderCompare(rows) {
  const settled = rows.filter(r => Date.now() - r.at > 6 * 36e5), groups = [];
  const add = (section, label, list) => { if (list.length) groups.push([section, label, list]); };
  add('Spoken intro', 'With intro', settled.filter(r => r.intro)); add('Spoken intro', 'Without intro', settled.filter(r => !r.intro));
  ['GTA 6', 'GTA 5'].forEach(g => add('Game', g, settled.filter(r => r.game === g)));
  const creators = {};
  settled.forEach(r => { if (r.creator) (creators[r.creator] ||= []).push(r); });
  Object.entries(creators).filter(([, l]) => l.length >= 2).sort((a, b) => b[1].length - a[1].length).slice(0, 5).forEach(([c, l]) => add('Streamer', c, l));
  let last = '';
  $('pfCompare').innerHTML = groups.length ? '<div class="shot-table"><table><thead><tr><th>Group</th><th class="num">Clips</th><th class="num">Median YouTube</th><th class="num">Median Facebook</th></tr></thead><tbody>' + groups.map(([section, label, list]) =>
    (section !== last ? (last = section, '<tr class="pf-section"><td colspan="4">' + esc(section) + '</td></tr>') : '') + '<tr><td>' + esc(label) + (list.length < 5 ? ' <span class="pf-tag low">small sample</span>' : '') + '</td><td class="num">' + list.length +
      '</td><td class="num">' + n0(median(list.map(r => r.yt))) + '</td><td class="num">' + n0(median(list.filter(r => r.fb != null).map(r => r.fb))) + '</td></tr>').join('') + '</tbody></table></div>'
    : '<p class="shot-muted">Comparisons appear once clips in this range are older than 6 hours.</p>';
}

// The page's original videos() is replaced: one joined view drives tiles, chart, table and comparisons.
function videos() { const rows = filteredRows(); renderPerfTiles(rows); renderPerfChart(rows); renderPerfTable(rows); renderCompare(rows); }

// Live operations card at the top of Overview: status, stage track, plan chips and the controls.
function renderLiveOps() {
  const box = $('ovLiveMain');
  if (!box || typeof Q_STEPS === 'undefined') return;
  const s = D.scheduler || {}, c = D.scheduler_config || {}, now = Date.now() / 1000, manual = qActiveManual();
  const busy = Q_BUSY.includes(s.phase), index = manual ? qStepIndex({phase: manual.stage, note: manual.note}) : busy ? qStepIndex(s) : -1;
  const source = manual?.result?.source || s.selected, next = qCycleBase(s) + (c.scan_minutes || 180) * 60;
  const stepLabel = index >= 0 && index < Q_STEPS.length ? Q_STEPS[index][1] : null;
  let state, title, sub;
  if (manual || busy) {
    state = 'running';
    title = (manual ? 'Manual job · ' : '') + (stepLabel || 'Working') + (source?.channel ? ' · ' + source.channel : '');
    sub = (manual ? manual.note : s.note) || '';
  } else if (!c.enabled) {
    state = 'paused'; title = 'Automatic clipping is paused'; sub = 'Manual jobs still run. Resume to continue the schedule.';
  } else if (s.phase === 'error') {
    state = 'error'; title = 'Last run failed'; sub = (s.error || '').slice(0, 180);
  } else {
    state = 'idle'; title = 'Idle — next cycle in <span data-q-until="' + next + '">' + qClock(next - now) + '</span>'; sub = s.note || 'Waiting for the next scheduled check';
  }
  const last = (D.auto_history || []).find(h => h.status === 'uploaded'), voice = {off: 'Off', intro: 'Intro line', full: 'Full breakdown'}[qVoiceMode(c)];
  const chip = (label, value, tone = '') => '<span class="live-chip ' + tone + '"><em>' + label + '</em>' + value + '</span>';
  const blockers = qBlockers().filter(([, t]) => t !== 'Automatic clipping is paused');
  const track = '<ol class="q-track live-track">' + Q_STEPS.map(([, label], i) => '<li class="' + (index > i ? 'done' : index === i ? 'active' : '') + '"><span>' +
    (index > i ? '✓' : i + 1) + '</span>' + esc(label) + '</li>').join('') + '</ol>';
  box.innerHTML = '<div class="live-status ' + state + '"><span class="live-dot" aria-hidden="true"></span><span class="live-state">' + {running: 'LIVE', idle: 'IDLE', paused: 'PAUSED', error: 'ERROR'}[state] +
    '</span></div><h2 class="live-title">' + (state === 'idle' ? title.replace(/^([^<]+)/, m => esc(m)) : esc(title)) + '</h2><p class="live-sub">' + esc(sub) + '</p>' + track +
    '<div class="live-chips">' + chip('Automatic', c.enabled ? 'On' : 'Paused', c.enabled ? 'on' : 'off') + chip('Cadence', 'Every ' + ((c.scan_minutes || 180) / 60) + 'h') + chip('Intro', voice) +
    chip('Next cycle', c.enabled ? esc(qTime(next)) : '—') + chip('Last upload', last ? '<a href="https://www.youtube.com/shorts/' + encodeURIComponent(last.video_id) + '" target="_blank" rel="noopener">' +
      esc(last.at ? ageText(Date.parse(last.at)) : 'recent') + ' ↗</a>' : '—') + '</div>' +
    blockers.map(([tone, t, text]) => '<div class="q-blocker ' + tone + ' live-blocker"><b>' + esc(t) + '</b><p>' + esc(text) + '</p></div>').join('');
  // The newest finished clip from any source (picked server-side by file time), labelled with what happened to it.
  const clip = D.latest_clip, pub = clip?.published, lastPub = (D.auto_history || []).find(h => h.status === 'uploaded');
  const key = JSON.stringify([clip?.file, pub?.video_id, lastPub?.video_id]), pv = $('ovLivePreview');
  if (pv && pv.dataset.key !== key) {
    pv.dataset.key = key;
    const ytLink = id => '<a href="https://www.youtube.com/shorts/' + encodeURIComponent(id) + '" target="_blank" rel="noopener">YouTube ↗</a>';
    const fbLink = id => ' <a href="https://www.facebook.com/reel/' + encodeURIComponent(id) + '/" target="_blank" rel="noopener">Facebook ↗</a>';
    const badge = pub?.status === 'uploaded' ? '<span class="clip-badge published">✓ Published</span>'
      : pub ? '<span class="clip-badge attention">Upload ' + esc(pub.status) + '</span>' : '<span class="clip-badge local">Local only · not uploaded</span>';
    pv.innerHTML = clip ? '<span class="eyebrow">LATEST FINISHED CLIP</span><h3>' + esc(clip.title) + '</h3><p class="clip-origin">' + badge + '<span>' + esc(clip.origin) +
      (clip.creator ? ' · ' + esc(clip.creator) : '') + ' · finished ' + esc(ageText(Date.parse(clip.finished_at))) + '</span></p>' +
      (clip.intro ? '<p class="ov-intro">“' + esc(clip.intro) + '”</p>' : '') +
      '<video controls preload="metadata" src="/api/shotsnip/media?file=' + encodeURIComponent(clip.file) + '"></video>' +
      (pub?.video_id ? '<div class="live-preview-meta"><span id="ovLiveViews"></span><span>' + ytLink(pub.video_id) + (pub.reel_id ? fbLink(pub.reel_id) : '') + '</span></div>'
        : lastPub ? '<p class="live-last-pub">Last published: <b>' + esc(lastPub.title || 'clip') + '</b> · ' + ytLink(lastPub.video_id) + (lastPub.facebook?.reel_id ? fbLink(lastPub.facebook.reel_id) : '') + '</p>' : '')
      : '<span class="eyebrow">LATEST FINISHED CLIP</span><div class="empty-state"><p>Your next finished clip appears here.</p></div>';
  }
  if (pub?.video_id && $('ovLiveViews')) {
    const row = (D.analytics?.videos || []).find(v => v.id === pub.video_id), fbRow = (D.facebook_analytics?.videos || []).find(v => v.youtube_id === pub.video_id);
    $('ovLiveViews').innerHTML = '<i class="sw yt"></i>' + n0(row?.views) + ' views <i class="sw fb"></i>' + n0(fbRow?.plays) + ' plays';
  }
  // Reuse the existing control buttons (and their handlers) inside the card.
  const actions = $('ovLiveActions');
  for (const id of ['autoPause', 'autoStop']) { const b = $(id); if (b && b.parentElement !== actions) actions.insertBefore(b, actions.lastElementChild); }
}

function renderInsights() {
  renderLiveOps();
  const signature = JSON.stringify([D.analytics?.checked_at, D.facebook_analytics?.checked_at, (D.auto_history || []).length, D.analytics?.videos?.length]);
  if (signature === insightsSignature) return;
  insightsSignature = signature;
  renderOverview(); videos();
}

function initInsights() {
  const activity = $('publicActivity')?.closest('section');
  if (activity) activity.hidden = true;  // its content now lives in Overview's tiles and newest clips
  // Live operations sits directly under the header; the old ops cards are replaced by it.
  const hero = document.querySelector('#overview .control-hero'), ops = document.querySelector('#overview .ops-layout');
  if (hero) {
    const live = document.createElement('section');
    live.className = 'panel ov-live'; live.setAttribute('aria-labelledby', 'ovLiveHeading'); live.setAttribute('aria-live', 'polite');
    live.innerHTML = '<div class="ov-live-main"><div class="ov-live-head"><span class="eyebrow" id="ovLiveHeading">LIVE OPERATIONS</span></div><div id="ovLiveMain"></div>' +
      '<div class="ov-live-actions" id="ovLiveActions"><a class="button-link" href="#jobs">Open work queue →</a></div></div><div class="ov-live-preview" id="ovLivePreview"></div>';
    hero.after(live);
    if (ops) ops.hidden = true;  // replaced by this card (status, track, controls, latest published clip)
  }
  document.querySelectorAll('input[name=pfRange]').forEach(r => { r.onchange = () => { perfRange = r.value; videos(); }; });
  const tip = document.createElement('div');
  tip.className = 'viz-tip'; tip.setAttribute('role', 'tooltip'); tip.hidden = true; document.body.appendChild(tip);
  const show = (el, x, y) => { tip.textContent = el.dataset.tip; tip.hidden = false; const w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.min(innerWidth - w - 8, Math.max(8, x - w / 2)) + 'px'; tip.style.top = Math.max(8, y - h - 12) + 'px'; };
  const hide = () => { tip.hidden = true; };
  for (const box of [$('ovReach'), $('pfChart')]) {
    box.addEventListener('pointermove', e => { const el = e.target.closest('[data-tip]'); el ? show(el, e.clientX, e.clientY) : hide(); });
    box.addEventListener('pointerleave', hide);
    box.addEventListener('focusin', e => { const el = e.target.closest('[data-tip]'); if (el) { const r = el.getBoundingClientRect(); show(el, r.left + r.width / 2, r.top); } });
    box.addEventListener('focusout', hide);
  }
  addEventListener('scroll', hide, {passive: true});
}
initInsights();
