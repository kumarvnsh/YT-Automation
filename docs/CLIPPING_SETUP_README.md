# Clipping Channel Automation — Windows Setup Guide

The clipping channel runs on one Windows PC and uses no paid AI. Every hour it finds a live GTA stream and records 5 minutes. A local **Qwen 3 8B** model picks the best 30 seconds. The worker turns that into a captioned 9:16 Short, uploads it to YouTube, then (optionally) cross-posts it to a Facebook Page.

Everything runs locally:

| Job | Tool |
| --- | --- |
| Stream capture | yt-dlp + FFmpeg |
| Highlight scoring | Clips Kitty + Ollama (`qwen3:8b`) |
| Speech captions | faster-whisper (`small`, CPU) |
| Rendering | FFmpeg |
| Uploading | YouTube Data API v3 (OAuth) |

> **Copyright:** clipping other creators' streams carries copyright and reused-content risk. Every clip carries a source credit, but credit is not a licence.

---

## Folder layout

| Folder | What it is |
| --- | --- |
| `D:\YT-Cliping-Automation` | Clipping worker: discovery, capture, finishing, upload, analytics sync |
| `D:\Clips-Kitty` | Clips Kitty highlight engine (AGPL-3.0), local API on `127.0.0.1:8765` |
| `D:\Ollama` | Ollama runtime + `qwen3:8b` model (~5.2 GB) |
| `D:\YT-Automation` | Optional dashboard, autostart and watchdog scripts |

Keep all venvs, caches and models on D:, not C:.

---

## How it fits together

```
 1. Discover ──► 2. Capture ──► 3. Clips Kitty ──► 4. Cut & finish
 watchlist,      yt-dlp+FFmpeg   Whisper transcript  30 s, 1080x1920
 YouTube live,   300 s record    qwen3:8b scores     captions + hook
 Twitch GTA      chat spikes     best clip, 55+      source credit
     ▲                                                     │
     │                                                     ▼
 8. Source    ◄── 7. Analytics ◄── 6. Facebook Reel ◄── 5. YouTube upload
 weights          every 30 min      optional,            channel ID checked,
 0.5x–1.5x        views/day per     never undoes         ledger blocks dupes
 per creator      source creator    the YouTube upload
```

The dashboard dispatcher or Task Scheduler starts each tick. A file lock allows only one tick at a time. Only stage 3 uses the language model. A real chat spike in stage 2 can override Kitty's timing. Stages 7–8 only change which creators are preferred, never the template or schedule.

---

## Prerequisites

### Hardware

| Item | Minimum | Why |
| --- | --- | --- |
| RAM | 16 GB | qwen3:8b needs ~6 GB; Whisper and FFmpeg run alongside |
| CPU | 6+ cores | Whisper `small` runs on CPU (6 threads, int8) |
| GPU | Optional | Ollama and the Kitty renderer use NVIDIA/NVENC when present |
| Disk | 60 GB free on D: | Model, Whisper weights, 3 days of raw recordings; capture stops below 3 GB free |
| Power | Sleep disabled on AC | The scheduler only runs while the PC is awake |

### Software

- Git for Windows
- Python 3.11 (64-bit), one venv per project
- FFmpeg. The worker falls back to the copy bundled with `imageio-ffmpeg`. Clips Kitty fetches its own copy.
- Ollama for Windows
- A Chromium browser (Brave or Chrome) for the dashboard and the Google sign-in pages

### Accounts and credentials

Each person creates their own. **Never share token files.**

| Credential | Where it comes from | Saved as |
| --- | --- | --- |
| Google OAuth client (Desktop app) | Google Cloud Console; enable YouTube Data API v3 + YouTube Analytics API | `client_secret.json` |
| Upload token | `get_token.py` (scopes `youtube.upload`, `youtube.readonly`) | `token.json` |
| Analytics token | `connect_analytics.py` (scopes `youtube.readonly`, `yt-analytics.readonly`) | `analytics_token.json` |
| Twitch chat (optional) | Twitch developer app, device login via `twitch_auth.py`, scope `chat:read` only | `work\twitch\credentials\twitch.bin` (DPAPI-encrypted) |
| Facebook Page (optional) | Meta system-user token with `pages_read_engagement` + `pages_manage_posts` | User env var `SHOTSNIP_META_TOKEN` |

All of these files are git-ignored. Keep them on the PC that uses them.

---

## Install on Windows

Install the pieces in this order. Run every command in PowerShell.

### 1. Clipping worker

Ask the owner for access to the repository and clone it into `D:\YT-Cliping-Automation`. Then:

```powershell
cd D:\YT-Cliping-Automation
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-live.txt
```

Set **your own** channel in `channel.json`:

```json
{ "label": "mychannel", "name": "My Clips", "url": "https://www.youtube.com/@myhandle", "channel_id": "UC..." }
```

Every upload is refused unless the signed-in channel matches this ID.

### 2. Clips Kitty (highlight engine)

Use the upstream [ColinGPT9/clips-studio](https://github.com/ColinGPT9/clips-studio) repo at tested commit `56a571faadd244926d1abdae5843d413af8665f9`:

```powershell
git clone https://github.com/ColinGPT9/clips-studio D:\Clips-Kitty
cd D:\Clips-Kitty
git checkout 56a571faadd244926d1abdae5843d413af8665f9
git apply D:\YT-Cliping-Automation\integrations\clips-kitty\ffmpeg-9.patch
copy D:\YT-Cliping-Automation\integrations\clips-kitty\shotsnip.yaml config\shotsnip.yaml
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt "av<19"
.\.venv\Scripts\python.exe scripts\fetch_ffmpeg.py
```

- The patch replaces FFmpeg's removed `-vsync` flag with `-fps_mode`. Re-apply it after any upstream update.
- In `config\shotsnip.yaml`, set `channel:` to your own handle.

### 3. Ollama + Qwen

See [Local AI](#local-ai-ollama--qwen) below.

### 4. Google sign-in

See [Uploading](#uploading-to-youtube-and-facebook) below.

### 5. Test by hand before automating

```powershell
cd D:\YT-Cliping-Automation
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe live_clipper.py discover --query "GTA V roleplay live"
```

### 6. Schedule it

See [Running it every day](#running-it-every-day).

---

## Local AI: Ollama + Qwen

The only language model is `qwen3:8b`, served by Ollama on `http://localhost:11434`. Clips Kitty uses it to read each recording's transcript and score candidate moments. The dashboard uses the same model for its assistant and daily review. You don't need a cloud AI key.

1. Install Ollama for Windows into `D:\Ollama\app`.
2. Keep models off C:, then restart Ollama:

   ```powershell
   [Environment]::SetEnvironmentVariable('OLLAMA_MODELS', 'D:\Ollama\models', 'User')
   ```

3. Pull the model (~5.2 GB) and check that it answers:

   ```powershell
   D:\Ollama\app\ollama.exe pull qwen3:8b
   D:\Ollama\app\ollama.exe run qwen3:8b "Say ready"
   ```

### Scoring settings (`D:\Clips-Kitty\config\shotsnip.yaml`)

| Setting | Value | Meaning |
| --- | --- | --- |
| `model` | `qwen3:8b` | Model that judges moments |
| `llm.ollama_host` | `http://localhost:11434` | Local Ollama server |
| `llm.temperature` / `num_ctx` | 0.4 / 8192 | Fairly consistent scoring, 8k context |
| `whisper.model` / `device` | `small` / `cpu` | Transcript that Kitty scores against |
| `clips.min_score` | 55 | Below this, no clip is made that hour |
| `clips.max_clips_per_video` | 1 | One clip per recording |
| `clips.min_duration` / `max_duration` | 30 / 30 s | Fixed 30-second highlight |
| `scoring.weights` | text 0.3, visual 0.2, reaction 0.2, audio 0.2, engagement 0.1 | How the final score is blended |
| `auto_upload` | `false` | Kitty never uploads; the worker does |

The worker starts Kitty itself when needed (`main.py --config config/shotsnip.yaml serve --host 127.0.0.1 --port 8765`) and runs its `/health/preflight` check first. Each job asks for one clip with this brief: *a funny or surprising GTA moment with a clear payoff, no loading screens or dead air.* If a job takes more than 35 minutes, it's abandoned and nothing is uploaded.

---

## The clipping pipeline

`scheduled_clips.py --tick` runs one cycle:

1. **Pick the game.** `game_schedule.json` chooses GTA V or GTA VI by date. Titles that name the wrong game, no game, or both games are rejected. This is a title check, not visual verification.
2. **Scan for sources** (`stream_sources.py`). It checks the creators in `watchlist.json`, YouTube live search and the public Twitch directory for that game, then ranks streams by live viewer count. A creator clipped in the last 6 hours is skipped. If nothing suitable is live, `archive_streams.py` falls back to yesterday's stream replays, ranked by views.
3. **Capture** 300 seconds with yt-dlp + FFmpeg into `work\scheduler\<timestamp>-<id>\`. Chat is counted as unique chatters per 5-second bin: YouTube through its unofficial web endpoint, Twitch through read-only IRC. No names or messages are stored.
4. **Score with Clips Kitty.** Kitty transcribes the recording with Whisper and asks Qwen to score moments. The best clip scoring 55 or more wins. If none reaches 55, that hour is skipped.
5. **Choose the exact window.** A real chat spike (≥ 8 unique chatters and 3× the median of the previous 12 bins) overrides Kitty. The clip is then the 30 seconds ending 5 seconds before the spike. `media_safety.py` checks that all 30 seconds have audio.
6. **Cut** the highlight from the original full-frame recording, then fully decode it to prove it isn't corrupt.
7. **Finish** with `finish_video.py`, using template `templates\gaming-short-v1.json` + `.ass`. The Short is 1080×1920 at 30 fps, with a blurred background and the full gameplay frame. It adds a bold hook, word-highlighted captions, a category label and a bottom message. A small source credit sits at y=1760. Hindi speech keeps Hindi captions. Other languages are translated to English. Silence gets no captions.
8. **Upload** to YouTube, then cross-post to Facebook.

The hook is Kitty's suggested title when that title is plain English and under 100 characters. Otherwise it's `<creator>'s GTA live moment`. Raw recordings older than 3 days are deleted at the start of each tick.

### Manual clips

The dashboard's **Create clip** form queues a job (`manual_jobs.py`, stored in `work\manual\jobs.sqlite`). The source can be automatic discovery, a live link, a replay link, or up to 5 exact time ranges of 5–60 seconds each. The default outcome is **Preview** only; publishing is a separate click. To process queued jobs from the command line, run `.\.venv\Scripts\python.exe manual_worker.py`.

---

## Uploading to YouTube and Facebook

### One-time Google setup

1. In Google Cloud Console, create a project. Enable **YouTube Data API v3** and **YouTube Analytics API**.
2. Configure the OAuth consent screen and add your Google account as a test user.
3. Create an OAuth client of type **Desktop app**. Download the JSON and save it as `D:\YT-Cliping-Automation\client_secret.json`.
4. Sign in for uploads. When asked, choose the **clipping channel**, not your personal one:

   ```powershell
   cd D:\YT-Cliping-Automation
   .\.venv\Scripts\python.exe get_token.py
   ```

   If the channel doesn't match `channel.json`, it won't save `token.json`.
5. Sign in again for analytics:

   ```powershell
   .\.venv\Scripts\python.exe connect_analytics.py
   ```

### What each upload does

- Re-checks the signed-in channel (`channels.list(mine=True)`) against `channel.json`, the game schedule and the pause switch.
- Refuses a clip whose SHA-256 hash was already uploaded.
- Writes a `pending` row to `work\scheduler\state.json` with a unique marker in the description. Then it calls `videos.insert`: category 20 (Gaming), privacy `public`, not made for kids.
- What it records depends on the result:
  - **Success:** the video ID.
  - **4xx error:** marked `rejected`.
  - **Anything else:** marked `uncertain`, and **all publishing is held** until you check the channel. Never delete a ledger row to force a retry.

### Pacing and quota

`scheduler.json` keeps successful uploads at least `post_minutes` apart. Each upload costs about 1,600 of the default 10,000 daily API units. Unless Google raises your quota, about **6 uploads a day** is the safe limit. Set `post_minutes` to about 240 to stay under it.

### Facebook Page (optional)

1. Create a Facebook Page for the channel. Create a Meta app with a system user assigned to that Page.
2. Generate a non-expiring system-user token with `pages_read_engagement` and `pages_manage_posts`.
3. Save the token to your Windows user environment. The script prompts with hidden input, so you never type it into a file:

   ```powershell
   powershell -File D:\YT-Cliping-Automation\setup_shotsnip_facebook.ps1
   ```

4. Put your Page settings in `facebook.json`:

   ```json
   { "enabled": true, "page_id": "<your page id>", "api_version": "<Graph API version>" }
   ```

The Reel uploads straight from the local MP4. A Facebook failure never blocks or reverses the YouTube upload.

---

## Analytics and the learning loop

Analytics refresh every 30 minutes while the dashboard runs, and they're read-only. They feed one bounded lever: how much each source creator is preferred (0.5–1.5×).

**What's collected** (`sync_analytics.py` → `work\dashboard\analytics.json`):

- A public snapshot of the latest 50 uploads (views, likes, comments, duration, publish time), each checked against your channel ID.
- Owner statistics from the YouTube API, using `analytics_token.json`.
- A 28-day YouTube Analytics report ending two days before today, because Google's data lags.
- Facebook Page and Reel figures (`sync_facebook.py`), when Facebook is enabled.

**How it changes future clips:**

- Each finished clip records `uploaded_video_id` and `creator` in its metadata.
- Clips count when they are 2–28 days old. Learning starts only once there are 6 linked clips overall and 3 per creator.
- Each creator's score is the median of lifetime views per day since upload. Their weight is that median ÷ the overall median, clamped to 0.5–1.5. Weights are recomputed from scratch each time, never compounded.
- Weights are saved to `work\dashboard\agent-policy.json` (`source_priorities`) and used when ranking live sources.

The analytics agent can't change code, templates, the schedule, account access or publishing.

---

## Running it every day

The scheduler tick runs often and decides for itself whether a cycle is due.

### Schedule settings (`scheduler.json`)

| Key | Example | Effect |
| --- | --- | --- |
| `enabled` | `true` | Master on/off |
| `mode` | `scan_capture_upload` | Each cycle scans, captures, scores and uploads in one go |
| `scan_minutes` | 60 | Minimum gap between cycles |
| `post_minutes` | 60 | Minimum gap between successful uploads (see quota note above) |
| `capture_seconds` | 300 | Length of each recording |
| `max_ready_clips` | 5 | Reserve of finished clips per day; unused ones expire at midnight IST |
| `streamer_cooldown_hours` | 6 | Wait before clipping the same creator again |
| `privacy` | `public` | Requested YouTube privacy |
| `raw_retention_days` | 3 | Raw recordings older than this are deleted |

Cadence is a minimum, not a promise. If no source is live, or no clip scores 55+, that hour is skipped.

### Option A — Task Scheduler only (recommended for new PCs)

Run this once to tick every 30 minutes while you're logged in:

```powershell
$Py = 'D:\YT-Cliping-Automation\.venv\Scripts\python.exe'
$Action = New-ScheduledTaskAction -Execute $Py -Argument 'scheduled_clips.py --tick' -WorkingDirectory 'D:\YT-Cliping-Automation'
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30)
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName 'Clipping Tick' -Action $Action -Trigger $Trigger -Settings $Settings
```

### Option B — Dashboard

`D:\YT-Automation\scripts\dashboard_server.py` provides a web dashboard with a clipping-channel page:

- Status and the clip queue.
- Pause/Start buttons, Run now, and manual clips.
- Analytics and clip previews, behind a login key.

Its background dispatcher checks every 10 seconds. When a cycle is due, it launches `scheduled_clips.py --tick`, or `manual_worker.py` if manual jobs are queued. After a failure, it retries 5 minutes later.

| Piece | What it does |
| --- | --- |
| `scripts\start_dashboard.ps1` | Starts the dashboard (port 8799) and restarts it when its code changes |
| Task at logon → `scripts\autostart.ps1` | Starts the dashboard and the clipping worker, then opens the app |
| Task every minute → `scripts\run_operations.ps1` | Watchdog: restarts a dead dashboard and the worker, at most once per 10 minutes unless Stop was pressed |

Option B currently assumes the owner's folder layout (`D:\histold` data clone and `dashboard_key.txt`). On a new PC, start with Option A and add the dashboard later.

### Keep the PC able to run

- Disable sleep on AC power and stay logged in.
- Keep at least 3 GB free on D:.
- Leave Ollama running.

---

## Troubleshooting

Errors appear as `phase: error` in `work\scheduler\state.json` and on the dashboard.

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| "Unresolved upload in ledger; publishing held" | An upload ended `pending`/`uncertain` | Check YouTube Studio for a video with that row's marker; record the outcome. Never delete the row |
| "Upload blocked: connected YouTube channel is not …" | `token.json` belongs to another channel | Re-run `get_token.py` and choose the clipping channel |
| "Clips Kitty could not start" | Kitty venv broken or port 8765 busy | Read `work\scheduler\kitty-service.log`; start Kitty by hand to see the error |
| "Clips Kitty preflight: …" | Ollama not running, model missing, or Whisper weights missing | Start Ollama; `ollama list` should show `qwen3:8b` |
| `no_quality_clip` every hour | Nothing scored 55+ | Normal for quiet streams; lower `min_score` cautiously |
| "No fresh streamer available" | All live creators are on cooldown | Add creators to `watchlist.json` or lower the cooldown |
| `quotaExceeded` rejection | Over 10,000 API units for the day | Wait for the Pacific-midnight reset; space uploads further apart |
| Capture stops early | Less than 3 GB free on D: | Free up space |
| FFmpeg `-vsync` error in Kitty | Patch lost after an update | Re-apply `ffmpeg-9.patch` |
| Worker doesn't restart after reboot | Stop was pressed (`work\dashboard\stop-worker` exists) | Press Start in the dashboard or delete that file |

**Logs:**

| Log | What it records |
| --- | --- |
| `work\dashboard\scheduler.log` | Each tick |
| `work\scheduler\kitty-service.log` | Kitty service startup |
| `D:\Clips-Kitty\data\logs` | Kitty jobs |

---

## Quick reference

Run these from `D:\YT-Cliping-Automation`:

| Task | Command |
| --- | --- |
| Run one cycle now | `.\.venv\Scripts\python.exe scheduled_clips.py --tick` |
| Process queued manual jobs | `.\.venv\Scripts\python.exe manual_worker.py` |
| Refresh analytics | `.\.venv\Scripts\python.exe sync_analytics.py` |
| Finish a clip by hand | `.\.venv\Scripts\python.exe finish_video.py --input CLIP.mp4 --hook "..." --creator "..." --source-url URL` |
| Find live streams | `.\.venv\Scripts\python.exe live_clipper.py discover --query "GTA V roleplay live"` |
| Run tests | `.\.venv\Scripts\python.exe -m pytest -q` |
| Pause automation | Set `"enabled": false` in `scheduler.json` |

| File | Purpose |
| --- | --- |
| `channel.json` | Your channel identity; uploads must match it |
| `watchlist.json` | Creators to watch (YouTube handles, Twitch profiles) |
| `scheduler.json` | Cadence, capture length, cooldown, privacy |
| `game_schedule.json` | Which game is active and when it switches |
| `templates\gaming-short-v1.*` | Short layout, fonts and colours |
| `facebook.json` | Facebook cross-post on/off and Page ID |
| `work\scheduler\state.json` | Upload ledger and live status. Back it up; never edit it by hand |
| `D:\Clips-Kitty\config\shotsnip.yaml` | Qwen model and highlight scoring |
