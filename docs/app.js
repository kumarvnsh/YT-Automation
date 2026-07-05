/* ============================================================
   YT-Automation Control Deck — vanilla JS, no build step.
   Reads data/analytics.json + data/trends.json (written by
   .github/workflows/analytics.yml) via the GitHub Contents API,
   and dispatches/reads GitHub Actions via the REST API — both
   authenticated with a fine-grained PAT stored in localStorage.
   ============================================================ */

const LS_KEY = "ytauto_dashboard_settings";

function getSettings() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveSettings(s) {
  localStorage.setItem(LS_KEY, JSON.stringify(s));
}

function isConfigured(s) {
  return Boolean(s.owner && s.repo && s.pat);
}

function apiUrl(s, path) {
  return `https://api.github.com/repos/${s.owner}/${s.repo}${path}`;
}

function ghHeaders(s) {
  return {
    Authorization: `Bearer ${s.pat}`,
    Accept: "application/vnd.github+json",
  };
}

function rawContentHeaders(s) {
  // api.github.com supports authenticated CORS properly; raw.githubusercontent.com
  // does not (its preflight rejects the Authorization header), so file reads go
  // through the Contents API with Accept: raw instead of a raw.githubusercontent.com URL.
  return { ...ghHeaders(s), Accept: "application/vnd.github.raw" };
}

/* ---------------------------------------------------------- */
/* Settings modal                                              */
/* ---------------------------------------------------------- */
const modal = document.getElementById("settingsModal");

function openSettings() {
  const s = getSettings();
  document.getElementById("sOwner").value = s.owner || "";
  document.getElementById("sRepo").value = s.repo || "YT-Automation";
  document.getElementById("sBranch").value = s.branch || "master";
  document.getElementById("sPat").value = s.pat || "";
  modal.classList.add("open");
}

function closeSettings() {
  modal.classList.remove("open");
}

document.getElementById("btnSettings").addEventListener("click", openSettings);
document.getElementById("btnCloseSettings").addEventListener("click", closeSettings);
modal.addEventListener("click", (e) => { if (e.target === modal) closeSettings(); });

document.getElementById("btnSaveSettings").addEventListener("click", () => {
  const s = {
    owner: document.getElementById("sOwner").value.trim(),
    repo: document.getElementById("sRepo").value.trim() || "YT-Automation",
    branch: document.getElementById("sBranch").value.trim() || "master",
    pat: document.getElementById("sPat").value.trim(),
  };
  saveSettings(s);
  closeSettings();
  boot();
});

document.getElementById("btnClearSettings").addEventListener("click", () => {
  localStorage.removeItem(LS_KEY);
  openSettings();
  boot();
});

/* ---------------------------------------------------------- */
/* Connection status                                           */
/* ---------------------------------------------------------- */
function setConn(state, label) {
  const led = document.getElementById("connLed");
  led.className = "led " + state;
  document.getElementById("connLabel").textContent = label;
}

/* ---------------------------------------------------------- */
/* Analytics + trends (via the Contents API, not raw.githubusercontent.com) */
/* ---------------------------------------------------------- */
async function fetchRepoFile(s, path) {
  const res = await fetch(`${apiUrl(s, `/contents/${path}`)}?_=${Date.now()}`, {
    cache: "no-store",
    headers: rawContentHeaders(s),
  });
  if (!res.ok) throw new Error(`${path} ${res.status}`);
  return res.text();
}

async function loadAnalytics(s) {
  return JSON.parse(await fetchRepoFile(s, "data/analytics.json"));
}

async function loadTrends(s) {
  return JSON.parse(await fetchRepoFile(s, "data/trends.json"));
}

async function loadPendingApprovals(s) {
  try {
    return JSON.parse(await fetchRepoFile(s, "data/pending_approvals.json"));
  } catch {
    return [];
  }
}

function fmtNum(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

function daysAgo(iso) {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return Infinity;
  return (Date.now() - t) / 86400000;
}

function analyticsChannels(analytics) {
  if (analytics && analytics.channels) return analytics.channels;
  if (analytics && Array.isArray(analytics.videos)) {
    return { [analytics.channel || "channel"]: analytics };
  }
  return {};
}

function renderKpis(analytics) {
  const channels = analyticsChannels(analytics);
  let allVideos = [];
  for (const key of Object.keys(channels)) {
    const vids = (channels[key].videos || []).map((v) => ({ ...v, _channel: key }));
    allVideos = allVideos.concat(vids);
  }
  const totalViews = allVideos.reduce((sum, v) => sum + (v.views || 0), 0);
  const last7 = allVideos.filter((v) => daysAgo(v.publishedAt) <= 7).length;
  const last30 = allVideos.filter((v) => daysAgo(v.publishedAt) <= 30).length;
  const retentionVideos = allVideos.filter((v) => Number.isFinite(Number(v.avg_view_pct)));
  const avgRetention = retentionVideos.length
    ? retentionVideos.reduce((sum, v) => sum + Number(v.avg_view_pct), 0) / retentionVideos.length
    : null;

  document.getElementById("kpiTotalVideos").textContent = allVideos.length || "0";
  document.getElementById("kpiTotalViews").textContent = fmtNum(totalViews);
  document.getElementById("kpiCadence7").textContent = last7;
  document.getElementById("kpiCadence30").textContent = `last 30d: ${last30}`;
  const retentionEl = document.getElementById("kpiAvgRetention");
  const retentionSub = document.getElementById("kpiRetentionSub");
  if (avgRetention === null) {
    retentionEl.textContent = "N/A";
    retentionEl.classList.add("na");
    retentionSub.textContent = "needs YT Analytics API";
  } else {
    retentionEl.textContent = `${avgRetention.toFixed(1)}%`;
    retentionEl.classList.remove("na");
    retentionSub.textContent = `${retentionVideos.length} videos with retention`;
  }

  return allVideos;
}

const VIDEO_PAGE_SIZE = 10;
let videoPageItems = [];
let videoPage = 1;

function renderVideoList(allVideos) {
  const el = document.getElementById("videoList");
  document.getElementById("videoCount").textContent = allVideos.length ? `${allVideos.length} videos` : "";
  if (!allVideos.length) {
    el.innerHTML = `<div class="empty-state"><strong>No videos yet</strong>Once a run uploads, it'll show up here.</div>`;
    videoPageItems = [];
    document.getElementById("videoPagination").style.display = "none";
    return;
  }
  videoPageItems = [...allVideos].sort((a, b) => new Date(b.publishedAt) - new Date(a.publishedAt));
  videoPage = 1;
  renderVideoPage();
}

function renderVideoPage() {
  const el = document.getElementById("videoList");
  const totalPages = Math.max(1, Math.ceil(videoPageItems.length / VIDEO_PAGE_SIZE));
  videoPage = Math.min(Math.max(1, videoPage), totalPages);
  const start = (videoPage - 1) * VIDEO_PAGE_SIZE;
  const pageItems = videoPageItems.slice(start, start + VIDEO_PAGE_SIZE);

  el.innerHTML = pageItems
    .map((v) => {
      const date = v.publishedAt ? new Date(v.publishedAt).toLocaleDateString() : "";
      const retention = Number.isFinite(Number(v.avg_view_pct))
        ? `<span>${Number(v.avg_view_pct).toFixed(1)}% retention</span>`
        : "";
      const subs = Number.isFinite(Number(v.subs_gained))
        ? `<span>+${fmtNum(Number(v.subs_gained))} subs</span>`
        : "";
      return `
      <a class="video-row" href="https://youtu.be/${v.id}" target="_blank" rel="noopener">
        <img class="video-row__thumb" src="${v.thumbnail || ""}" alt="" loading="lazy">
        <div class="video-row__meta">
          <p class="video-row__title">${escapeHtml(v.title || "(untitled)")}</p>
          <div class="video-row__stats">
            <span>${fmtNum(v.views || 0)} views</span>
            <span>${fmtNum(v.likes || 0)} likes</span>
            ${retention}
            ${subs}
            <span>${date}</span>
          </div>
        </div>
      </a>`;
    })
    .join("");

  const pagination = document.getElementById("videoPagination");
  pagination.style.display = totalPages > 1 ? "flex" : "none";
  document.getElementById("videoPageLabel").textContent = `Page ${videoPage} of ${totalPages}`;
  document.getElementById("videoPrev").disabled = videoPage <= 1;
  document.getElementById("videoNext").disabled = videoPage >= totalPages;
}

document.getElementById("videoPrev").addEventListener("click", () => {
  videoPage -= 1;
  renderVideoPage();
});
document.getElementById("videoNext").addEventListener("click", () => {
  videoPage += 1;
  renderVideoPage();
});

/* Topics published from the trends panel are marked "used" in localStorage so
   the panel rotates the next fresh one in. Optimistic: marked on dispatch, not
   on upload completion. */
const USED_TRENDS_KEY = "ytauto_used_trends";
const TREND_DISPLAY_LIMIT = 5;
let lastTrends = null; // cached so a publish click can re-render

function getUsedTrends() {
  try {
    return JSON.parse(localStorage.getItem(USED_TRENDS_KEY) || "[]");
  } catch {
    return [];
  }
}

function markTrendUsed(topic, poolStillPresent) {
  const used = new Set(getUsedTrends());
  used.add(topic);
  // Prune entries no longer in the current pool so storage stays bounded.
  const pruned = [...used].filter((t) => poolStillPresent.has(t));
  localStorage.setItem(USED_TRENDS_KEY, JSON.stringify(pruned));
}

function renderTrends(trends) {
  lastTrends = trends;
  const el = document.getElementById("trendsPanel");
  const channels = (trends && trends.channels) || {};
  const keys = Object.keys(channels);
  if (!keys.length) {
    el.innerHTML = `<div class="empty-state"><strong>No data yet</strong>Connect the repo in Settings to load trends.json</div>`;
    return;
  }
  const used = new Set(getUsedTrends());

  el.innerHTML = keys
    .map((key) => {
      const c = channels[key];
      const pool = (c.on_this_day || []).filter((e) => !used.has(e));
      const shown = pool.slice(0, TREND_DISPLAY_LIMIT);
      const rows = shown.length
        ? shown
            .map(
              (e) => `
          <li class="trend-list__item">
            <span class="trend-list__text">${escapeHtml(e)}</span>
            <button class="btn btn--primary trend-publish" data-topic="${escapeHtml(e)}">▶ Publish</button>
          </li>`
            )
            .join("")
        : `<li class="trend-list__empty">All trends published — refreshes on the next analytics run.</li>`;
      return `
      <div class="trend-channel">
        <h3>${key} — ${escapeHtml(c.date || "")}</h3>
        <ul class="trend-list">${rows}</ul>
      </div>`;
    })
    .join("");

  el.querySelectorAll(".trend-publish").forEach((btn) => {
    btn.addEventListener("click", () => publishTrend(btn.dataset.topic, btn));
  });
}

async function publishTrend(topic, btn) {
  const s = getSettings();
  if (!isConfigured(s)) {
    alert("Configure Settings first (owner/repo/PAT).");
    return;
  }
  btn.disabled = true;
  btn.textContent = "Dispatching…";
  try {
    await dispatchWorkflow(s, "publish.yml", {
      format: "short",
      topic,
      privacy_status: "",
      dry_run: "false",
      no_upload: "false",
    });
    // Collect every topic currently in any channel's pool so pruning keeps
    // still-relevant used entries and drops stale ones.
    const poolStillPresent = new Set();
    const channels = (lastTrends && lastTrends.channels) || {};
    for (const key of Object.keys(channels)) {
      (channels[key].on_this_day || []).forEach((e) => poolStillPresent.add(e));
    }
    markTrendUsed(topic, poolStillPresent);
    renderTrends(lastTrends); // next pooled trend rotates in
    setTimeout(() => loadRuns(getSettings(), 1), 4000);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "▶ Publish";
    alert("Failed to dispatch: " + err.message);
  }
}

function renderStatsTicker(allVideos) {
  const el = document.getElementById("tickerTrack");
  if (!allVideos.length) {
    el.innerHTML = "<span>No stats yet — waiting on analytics.json.</span>";
    return;
  }
  const totalViews = allVideos.reduce((sum, v) => sum + (v.views || 0), 0);
  const totalLikes = allVideos.reduce((sum, v) => sum + (v.likes || 0), 0);
  const views48h = allVideos
    .filter((v) => daysAgo(v.publishedAt) <= 2)
    .reduce((sum, v) => sum + (v.views || 0), 0);
  const subsGained = allVideos.reduce((sum, v) => sum + (Number(v.subs_gained) || 0), 0);

  const items = [
    `TOTAL VIEWS ${fmtNum(totalViews)}`,
    `VIEWS (48H) ${fmtNum(views48h)}`,
    `LIKES ${fmtNum(totalLikes)}`,
    `SUBS GAINED ${fmtNum(subsGained)}`,
  ];
  const doubled = items.concat(items); // seamless loop
  el.innerHTML = doubled.map((t) => `<span>${escapeHtml(t)}</span>`).join("");
}

function renderApprovals(list, s) {
  const el = document.getElementById("approvalList");
  document.getElementById("approvalCount").textContent = list.length ? `${list.length} pending` : "";
  if (!list.length) {
    el.innerHTML = `<div class="empty-state"><strong>Queue empty</strong>Runs dispatched with "No upload" land here for review.</div>`;
    return;
  }
  el.innerHTML = list
    .map((item) => `
      <div class="approval-row">
        <div>
          <div class="approval-row__title">${escapeHtml(item.title || item.stage_dir_name)}</div>
          <div class="approval-row__meta">${item.stage_dir_name} &middot; run #${item.run_id}</div>
        </div>
        <button class="btn btn--primary approve-btn"
          data-stage="${item.stage_dir_name}"
          data-run="${item.run_id}">Approve &amp; Upload</button>
      </div>`)
    .join("");

  el.querySelectorAll(".approve-btn").forEach((btn) => {
    btn.addEventListener("click", () => dispatchApprove(s, btn.dataset));
  });
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ---------------------------------------------------------- */
/* Dispatch: trigger a new publish.yml run                     */
/* ---------------------------------------------------------- */
async function dispatchWorkflow(s, workflowFile, inputs) {
  const res = await fetch(apiUrl(s, `/actions/workflows/${workflowFile}/dispatches`), {
    method: "POST",
    headers: { ...ghHeaders(s), "Content-Type": "application/json" },
    body: JSON.stringify({ ref: s.branch || "master", inputs }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${text}`.trim());
  }
}

document.getElementById("btnDispatch").addEventListener("click", async () => {
  const s = getSettings();
  const msg = document.getElementById("dispatchMsg");
  if (!isConfigured(s)) {
    msg.textContent = "Configure Settings first (owner/repo/PAT).";
    msg.className = "dispatch-msg err";
    return;
  }
  const inputs = {
    format: document.getElementById("dFormat").value,
    topic: document.getElementById("dTopic").value.trim(),
    privacy_status: document.getElementById("dPrivacy").value,
    dry_run: document.getElementById("dDryRun").checked ? "true" : "false",
    no_upload: document.getElementById("dNoUpload").checked ? "true" : "false",
  };
  msg.textContent = "Dispatching...";
  msg.className = "dispatch-msg";
  try {
    await dispatchWorkflow(s, "publish.yml", inputs);
    msg.textContent = "Dispatched — check Run History shortly.";
    msg.className = "dispatch-msg ok";
    setTimeout(() => loadRuns(s), 4000);
  } catch (err) {
    msg.textContent = "Failed: " + err.message;
    msg.className = "dispatch-msg err";
  }
});

async function dispatchApprove(s, data) {
  if (!isConfigured(s)) return;
  const privacy = prompt(
    "Privacy for this upload — private / unlisted / public\n(leave blank to use whatever was set when this draft was generated):",
    "private"
  );
  if (privacy === null) return; // cancelled
  if (privacy && !["private", "unlisted", "public"].includes(privacy)) {
    alert("Must be private, unlisted, public, or blank.");
    return;
  }
  try {
    await dispatchWorkflow(s, "approve.yml", {
      stage_dir_name: data.stage,
      source_run_id: data.run,
      privacy_status: privacy || "",
    });
    alert("Approval dispatched — check Run History for approve.yml.");
  } catch (err) {
    alert("Failed to dispatch approval: " + err.message);
  }
}

/* ---------------------------------------------------------- */
/* Run history                                                 */
/* ---------------------------------------------------------- */
function statusPill(run) {
  if (run.status !== "completed") return `<span class="status-pill progress">${run.status}</span>`;
  if (run.conclusion === "success") return `<span class="status-pill success">success</span>`;
  if (run.conclusion === "failure") return `<span class="status-pill failure">failure</span>`;
  return `<span class="status-pill neutral">${run.conclusion || "unknown"}</span>`;
}

const RUN_PAGE_SIZE = 10;
let runPage = 1;
let runHasNext = false;

function parseLinkHeader(header) {
  const rels = {};
  if (!header) return rels;
  header.split(",").forEach((part) => {
    const match = part.match(/<([^>]+)>;\s*rel="([^"]+)"/);
    if (match) rels[match[2]] = match[1];
  });
  return rels;
}

async function loadRuns(s, page = 1) {
  const el = document.getElementById("runList");
  const pagination = document.getElementById("runPagination");
  if (!isConfigured(s)) return;
  try {
    const res = await fetch(apiUrl(s, `/actions/runs?per_page=${RUN_PAGE_SIZE}&page=${page}`), { headers: ghHeaders(s) });
    if (!res.ok) throw new Error(`${res.status}`);
    const data = await res.json();
    const runs = data.workflow_runs || [];
    runPage = page;
    runHasNext = Boolean(parseLinkHeader(res.headers.get("link")).next);
    if (!runs.length) {
      el.innerHTML = `<div class="empty-state"><strong>No runs yet</strong>Trigger one from Dispatch Run.</div>`;
      pagination.style.display = "none";
      return;
    }
    el.innerHTML = runs
      .map((r) => `
        <div class="run-row">
          ${statusPill(r)}
          <a href="${r.html_url}" target="_blank" rel="noopener">${escapeHtml(r.name || r.display_title || "run")}</a>
          <span class="run-row__meta">${new Date(r.created_at).toLocaleString()}</span>
          <span class="run-row__meta">#${r.run_number}</span>
        </div>`)
      .join("");

    pagination.style.display = runPage > 1 || runHasNext ? "flex" : "none";
    document.getElementById("runPageLabel").textContent = `Page ${runPage}`;
    document.getElementById("runPrev").disabled = runPage <= 1;
    document.getElementById("runNext").disabled = !runHasNext;
  } catch (err) {
    el.innerHTML = `<div class="empty-state"><strong>Couldn't load runs</strong>${escapeHtml(err.message)}</div>`;
    pagination.style.display = "none";
  }
}

document.getElementById("btnRefreshRuns").addEventListener("click", () => loadRuns(getSettings(), 1));
document.getElementById("runPrev").addEventListener("click", () => loadRuns(getSettings(), runPage - 1));
document.getElementById("runNext").addEventListener("click", () => loadRuns(getSettings(), runPage + 1));

/* ---------------------------------------------------------- */
/* Boot                                                         */
/* ---------------------------------------------------------- */
async function boot() {
  const s = getSettings();
  if (!isConfigured(s)) {
    setConn("warn", "not connected — open Settings");
    return;
  }
  setConn("warn", "connecting...");

  try {
    const [analytics, trends, approvals] = await Promise.all([
      loadAnalytics(s).catch(() => null),
      loadTrends(s).catch(() => null),
      loadPendingApprovals(s).catch(() => []),
    ]);

    if (analytics) {
      const allVideos = renderKpis(analytics);
      renderVideoList(allVideos);
      renderStatsTicker(allVideos);
    }
    if (trends) {
      renderTrends(trends);
    }
    renderApprovals(approvals || [], s);
    setConn("ok", `linked to ${s.owner}/${s.repo}`);
  } catch (err) {
    setConn("bad", "link error: " + err.message);
  }

  loadRuns(s);
}

boot();
