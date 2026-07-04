/* ============================================================
   YT-Automation Control Deck — vanilla JS, no build step.
   Reads data/analytics.json + data/trends.json (written by
   .github/workflows/analytics.yml) via raw.githubusercontent.com,
   and dispatches/reads GitHub Actions via the REST API using a
   fine-grained PAT stored in localStorage.
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

function rawUrl(s, path) {
  const branch = s.branch || "master";
  return `https://raw.githubusercontent.com/${s.owner}/${s.repo}/${branch}/${path}?_=${Date.now()}`;
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

function rawHeaders(s) {
  // raw.githubusercontent.com needs auth too since this is a private repo.
  return s.pat ? { Authorization: `Bearer ${s.pat}` } : {};
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
/* Analytics + trends (raw.githubusercontent.com)               */
/* ---------------------------------------------------------- */
async function loadAnalytics(s) {
  const res = await fetch(rawUrl(s, "data/analytics.json"), { cache: "no-store", headers: rawHeaders(s) });
  if (!res.ok) throw new Error(`analytics.json ${res.status}`);
  return res.json();
}

async function loadTrends(s) {
  const res = await fetch(rawUrl(s, "data/trends.json"), { cache: "no-store", headers: rawHeaders(s) });
  if (!res.ok) throw new Error(`trends.json ${res.status}`);
  return res.json();
}

async function loadPendingApprovals(s) {
  const res = await fetch(rawUrl(s, "data/pending_approvals.json"), { cache: "no-store", headers: rawHeaders(s) });
  if (!res.ok) return [];
  return res.json();
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

function renderKpis(analytics) {
  const channels = (analytics && analytics.channels) || {};
  let allVideos = [];
  for (const key of Object.keys(channels)) {
    const vids = (channels[key].videos || []).map((v) => ({ ...v, _channel: key }));
    allVideos = allVideos.concat(vids);
  }
  const totalViews = allVideos.reduce((sum, v) => sum + (v.views || 0), 0);
  const last7 = allVideos.filter((v) => daysAgo(v.publishedAt) <= 7).length;
  const last30 = allVideos.filter((v) => daysAgo(v.publishedAt) <= 30).length;

  document.getElementById("kpiTotalVideos").textContent = allVideos.length || "0";
  document.getElementById("kpiTotalViews").textContent = fmtNum(totalViews);
  document.getElementById("kpiCadence7").textContent = last7;
  document.getElementById("kpiCadence30").textContent = `last 30d: ${last30}`;

  return allVideos;
}

function renderVideoList(allVideos) {
  const el = document.getElementById("videoList");
  document.getElementById("videoCount").textContent = allVideos.length ? `${allVideos.length} videos` : "";
  if (!allVideos.length) {
    el.innerHTML = `<div class="empty-state"><strong>No videos yet</strong>Once a run uploads, it'll show up here.</div>`;
    return;
  }
  const sorted = [...allVideos].sort((a, b) => new Date(b.publishedAt) - new Date(a.publishedAt));
  el.innerHTML = sorted
    .slice(0, 25)
    .map((v) => {
      const chanClass = v._channel === "medimyth" ? "medimyth" : "";
      const date = v.publishedAt ? new Date(v.publishedAt).toLocaleDateString() : "";
      return `
      <a class="video-row" href="https://youtu.be/${v.id}" target="_blank" rel="noopener">
        <img class="video-row__thumb" src="${v.thumbnail || ""}" alt="" loading="lazy">
        <div class="video-row__meta">
          <p class="video-row__title">${escapeHtml(v.title || "(untitled)")}</p>
          <div class="video-row__stats">
            <span class="chan-tag ${chanClass}">${v._channel}</span>
            <span>${fmtNum(v.views || 0)} views</span>
            <span>${fmtNum(v.likes || 0)} likes</span>
            <span>${date}</span>
          </div>
        </div>
      </a>`;
    })
    .join("");
}

function renderTrends(trends) {
  const el = document.getElementById("trendsPanel");
  const channels = (trends && trends.channels) || {};
  const keys = Object.keys(channels);
  if (!keys.length) {
    el.innerHTML = `<div class="empty-state"><strong>No data yet</strong>Connect the repo in Settings to load trends.json</div>`;
    return;
  }
  el.innerHTML = keys
    .map((key) => {
      const c = channels[key];
      const otd = (c.on_this_day || []).slice(0, 5);
      return `
      <div class="trend-channel">
        <h3>${key} — ${escapeHtml(c.date || "")}</h3>
        <ul class="trend-list">
          ${otd.map((e) => `<li>${escapeHtml(e)}</li>`).join("") || "<li>no on-this-day data</li>"}
        </ul>
      </div>`;
    })
    .join("");
}

function renderTicker(trends) {
  const el = document.getElementById("tickerTrack");
  const channels = (trends && trends.channels) || {};
  const terms = [];
  for (const key of Object.keys(channels)) {
    (channels[key].trends || []).slice(0, 10).forEach((t) => terms.push(t));
  }
  if (!terms.length) {
    el.innerHTML = "<span>No trend signals yet — run the analytics workflow.</span>";
    return;
  }
  const doubled = terms.concat(terms); // seamless loop
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
          <div class="approval-row__meta">${item.channel} &middot; ${item.stage_dir_name} &middot; run #${item.run_id}</div>
        </div>
        <button class="btn btn--primary approve-btn"
          data-channel="${item.channel}"
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
    channel: document.getElementById("dChannel").value,
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
  try {
    await dispatchWorkflow(s, "approve.yml", {
      channel: data.channel,
      stage_dir_name: data.stage,
      source_run_id: data.run,
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

async function loadRuns(s) {
  const el = document.getElementById("runList");
  if (!isConfigured(s)) return;
  try {
    const res = await fetch(apiUrl(s, "/actions/runs?per_page=10"), { headers: ghHeaders(s) });
    if (!res.ok) throw new Error(`${res.status}`);
    const data = await res.json();
    const runs = data.workflow_runs || [];
    if (!runs.length) {
      el.innerHTML = `<div class="empty-state"><strong>No runs yet</strong>Trigger one from Dispatch Run.</div>`;
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
  } catch (err) {
    el.innerHTML = `<div class="empty-state"><strong>Couldn't load runs</strong>${escapeHtml(err.message)}</div>`;
  }
}

document.getElementById("btnRefreshRuns").addEventListener("click", () => loadRuns(getSettings()));

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
    }
    if (trends) {
      renderTrends(trends);
      renderTicker(trends);
    }
    renderApprovals(approvals || [], s);
    setConn("ok", `linked to ${s.owner}/${s.repo}`);
  } catch (err) {
    setConn("bad", "link error: " + err.message);
  }

  loadRuns(s);
}

boot();
