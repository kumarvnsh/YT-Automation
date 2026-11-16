# YT-Automation — Daily "History / Did-You-Know" Video Bot

Fully scripted pipeline that, on each run, **writes a script → generates a voiceover → pulls matching stock footage → burns in captions → renders an MP4 → uploads it to YouTube**. Supports both vertical **Shorts** and horizontal **long-form** videos. Point a scheduler at it and it runs hands-off every day.

Histold's current automatic Shorts prioritize **Facebook and surprising world history**, with YouTube and Instagram distribution retained. New jobs interleave nine regional groups, three evergreen series and 30/50-second duration tests. Facebook is published first; a missing Reel ID fails the step, and known successful platform IDs survive retries. The workflow allows up to 10 scheduled uploads per IST day; cron-job.org controls the actual posting times. Manual topics and existing stage snapshots keep their original settings.

See the [content playbook and prepared Page copy](docs/facebook-content-playbook.md), [Facebook Insights import/report guide](docs/facebook-growth.md), and [world map/source provenance](docs/world-history-sources.md). Raw dashboard CSVs, demographic notes and financial baselines stay in ignored `data/private/`. No Page edits, comment replies or video uploads are triggered by these local import/report commands.

## How it works

```
topic (angle + dedupe)
   └─> script_generator   LLM writes title, description, tags, segmented narration
         └─> tts          CPU Kokoro voiceover (+ native timing, Edge fallback)
               └─> captions   word timings → animated ASS subtitles
               └─> assets     Pexels stock video/photo per segment (keywords)
                     └─> video_builder   ffmpeg: clips + Ken-Burns + music + captions
                           └─> youtube_uploader   Data API v3 upload (OAuth)
```

Each run writes everything to `output/<timestamp>_<format>_<slug>/` (script.json, voiceover.mp3, captions.ass, video.mp4, metadata.json).

## Histold production profile

New Histold Shorts use the versioned production profile in root `config.yaml`:

```
research → script → voiceover → align → audio_check
  → captions → assets → render → compose → quality → thumbnail → upload
```

Research uses Anthropic's runtime web search and retains citation excerpts from
approved institutions. The script uses that research directly; there is no separate
post-script factual verification stage. Unsupported research or unresolved material
disputes still stop the job before script generation.
Broaden `research.authoritative_domains` deliberately for new historical topics.
When search citations omit a core source, one bounded repair pass retrieves the
approved source pages directly (including checked redirects), then searches for
remaining evidence if necessary. Fetched text, response hashes, and both attempts
remain in `research.json`. Rejected claims explicitly classified as nonessential
(`material_to_core_story: false`) move to background findings and `excluded_claims`;
the script must not use those findings. Core disputes are never relabeled to pass.
If validation still fails, research retries once with the failed packet and errors.
Automatic runs can then try one different story in the same niche, with one retry
for that story (four research packets maximum). An explicit `--topic` stays fixed
and gets only the same-topic retry. Known material contradictions remain blocking
on same-topic retries. Each packet is saved as `research_attempt_N.json` in the run
artifact; `research.json` contains the final packet. Exhausted attempts still stop
the job before script generation. Recovery can add research API cost.
Workflow failures identify the affected claim and source IDs.

`storyboard.json` records measured word timing, frame boundaries and reveal cues.
Edge supplies native word boundaries. If these are absent/inconsistent,
`tts.alignment_provider: openai` enables measured Whisper transcription timestamps
and validates text agreement; estimated timing cannot pass strict production.
Web research and optional transcription add API cost.

Conservative silence diagnostics warn without trimming audio. Exact intentional
pause windows can be recorded in the storyboard's `allowed_pauses`; rerun the
audio check afterward. Required maps use pinned Natural Earth outlines; initially
United States, Canada, Minnesota and Washington are supported. Other essential
geography and historical boundary overlays stop the job until vetted assets are
added. These maps establish present-day context and do not resolve local roads.

Final composition adds HISTOLD and captions. Missing libass, stale evidence or
timing, changed media, narration truncation, and blocking frame defects prevent
upload. Static-scene detection and pauses remain warning heuristics. Missing or
invalid generated scenes use a local house template with supplied narration and
vetted maps, and must pass the same frame checks. This recovery needs no image
credits. Historical stock-fallback reports still require review or a corrected render.

Every new job snapshots its production settings; older jobs retain legacy
behavior, and Astrotold does not opt into the new profile. Set
`production.enabled: false` to use the legacy profile for future jobs. Existing
production jobs keep their recorded rules.

The first six new Shorts reserve alternating categories from
`data/experiments/histold-six-reels.json`. Published records retain category,
hook, duration, profile and platform IDs. The analytics workflow captures
24/72/168-hour observations with actual age and late flags, and excludes these
posts from automatic reposting. YouTube rates use compatible Analytics report
views; unavailable Meta watch-time, retention, shares or attributed followers
remain null with reasons. Two examples per category are exploratory.

Local verification commands (no publishing or notifications):

```bash
python -m unittest discover -s tests -t . -v
python scripts/validate_histold_render.py  # Chromium + FFmpeg synthetic fixtures, no AI calls
python scripts/report_experiment.py      # Reads existing exports, no API calls
```

The render fixture uses synthetic tone audio and is a technical check, not a
finished historical Reel. To generate a real unpublished Short use
`python -m src.main --format short --no-upload`; this makes configured API calls
and may send configured notifications. Failed evidence stays in `research.json`;
inspect the saved attempts if automatic recovery also fails.

## Facebook Content Monetization and Stars tracking

Import per-Reel dashboard figures with `python scripts/import_facebook_monetization.py /path/to/export.csv`.
Use `assets/templates/facebook-monetization.csv` for the supported normalized headers;
this is a Histold import format, not an assumed native Meta export format. Dates are
inclusive `YYYY-MM-DD`, IDs must be Facebook Reel IDs, currency is the exported
three-letter code, and numbers use decimal points without grouping/currency symbols.
Leave unavailable values blank; a real reported zero can be entered as `0`.

For different dashboard headers, pass `--mapping /path/to/mapping.json`, a JSON object
mapping canonical names to actual headers, for example `{"media_id":"Post ID"}`.
Map earnings only when the column's meaning and reporting period match. Use one row
per Reel/period/currency. Reimporting the same key updates it; overlapping periods
are rejected to prevent double counting. Page-level earnings cannot be attributed
to individual Reels by this importer.

The ledger and report stay in git-ignored `data/private/`. The report joins known
Reel IDs to titles, categories and experiment IDs, keeps Stars separate, and derives
Content Monetization earnings per 1,000 qualified views only from the same row.
No earnings are inferred from total views or Stars counts. No API permissions or
automatic financial exports are assumed; importing new dashboard data is manual.

## One-time setup

1. **System dependency — ffmpeg** (required):
   - macOS: `brew install ffmpeg`
   - Ubuntu: `sudo apt-get install ffmpeg`

2. **Python deps** (Python 3.10+):
   ```bash
   cd ~/Documents/YT-Automation
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **API keys** — copy and fill:
   ```bash
   cp .env.example .env
   ```
   Minimum to render videos:
   - `ANTHROPIC_API_KEY` *(or set `script.provider: openai` + `OPENAI_API_KEY`)* — writes the script.
   - `PEXELS_API_KEY` — free at <https://www.pexels.com/api/> — stock footage.
   - Histold voiceover uses **Kokoro Michael** (`am_michael`, speed `0.95`) on CPU, with **Edge fallback**. GitHub Actions installs an isolated worker and caches model downloads. No TTS API key or running Mac is required. For local Kokoro use, install `torch==2.6.0` and `requirements-kokoro.txt` in a separate Python 3.11 environment and set `KOKORO_PYTHON` to its interpreter. Otherwise generation falls back to Edge. The manual **Voiceover CPU Smoke Test** workflow produces audio and timing artifacts without publishing a video.

4. **YouTube upload** (one-time OAuth):
   - Google Cloud Console → new project → enable **YouTube Data API v3**.
   - Credentials → **Create OAuth client ID** → type **Desktop** → download JSON → save as `secrets/client_secret.json`.
   - OAuth consent screen → add your Google account under **Test users**.
   - Run once: `python scripts/setup_oauth.py` (opens a browser, caches `secrets/token.json`).

## Run it

```bash
# Script only — no media, no cost beyond the LLM call. Great first test:
python -m src.main --format short --dry-run

# Render a Short but don't upload:
python -m src.main --format short --no-upload

# Full run (render + upload per config.yaml):
python -m src.main --format short
python -m src.main --format long
python -m src.main --format both
```

> ⚠️ `config.yaml` ships with `youtube.privacy_status: private` on purpose. Review a few generated videos, then switch to `unlisted` or `public` when you trust the output.

## Daily automation (decide later)

The pipeline is scheduler-agnostic. `scripts/run_daily.sh` is the entry point.

- **macOS cron** — `crontab -e`:
  ```
  0 9 * * * /Users/vnshkumar/Documents/YT-Automation/scripts/run_daily.sh >> /Users/vnshkumar/Documents/YT-Automation/logs/cron.log 2>&1
  ```
- **macOS launchd** — more reliable than cron on laptops; create a `LaunchAgent` plist calling `run_daily.sh`.
- **Cloud (hands-off, Mac can be off)** — GitHub Actions on a `schedule:` cron, with keys stored as repo **Secrets** and `secrets/token.json` committed as an encrypted secret. Ask and I'll generate the workflow file.

## Laptop dashboard app and local AI

Run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_dashboard.ps1`
on the Windows laptop. Open **http://localhost:8787** and sign in using the key in
`D:\histold\dashboard_key.txt`. The key, logs, and process metadata live outside the
repository. The server reads `D:\histold\YT-Automation`, refreshing its snapshot every
ten minutes. **Refresh dashboard** reloads sooner; platform exports arrive at the next
hourly laptop sync.

Local Ollama chat can propose pausing, boosting, or resetting a series/region/length,
pinning publish hours for 1–30 days, returning control to the agent, or refreshing
analytics. Press **Confirm** to apply a proposal. The server checks it again and writes
only `data/growth_overrides.json` or `data/growth_plan.json` through the GitHub CLI.
The daily post count stays fixed. Controls use UTC; chat also accepts explicit IST
times ending in `:30`, converting exactly to whole UTC hours. At least one option in
each dimension must remain active.

Chat understands **"Analyze again and change the publish timings"** as a sequence:
read the newest committed analytics exports, run local Qwen analysis, then propose a
validated posting-hour experiment. The card shows UTC/IST times, sample counts, and
the measurement period; only **Confirm** changes the schedule. Untried hours are
identified as experiments. If analysis fails, no schedule proposal is made. An
acknowledgement such as **"Yes, do that"** re-shows the previous validated card.
Independent clauses are checked separately, so **"Don't change the schedule; boost
Europe"** can still produce the requested Europe boost.

The **Histold Dashboard** login task starts the app. The hourly runner also invokes
`start_dashboard.ps1`, checking its process and restarting it when code changes.
For remote access, install Tailscale on both devices and sign in to the same account.
Run `tailscale serve --bg 8787` on the laptop and enable Serve at the URL it prints.
Open the resulting private HTTPS address on another device and enter the dashboard
key. The laptop must remain powered and connected for live access and AI chat.

Use **Install app** or Edge/Chrome's Install menu. On Mac, Safari offers **File → Add
to Dock**; on iPhone, Safari offers **Share → Add to Home Screen**. Offline mode shows
the last cached view; live data and controls need the laptop connection. **Sign out**
clears the app cache on that device.

Facebook activation/payout status is owner-reported. A read-only Graph API probe can
retrieve Page-level estimated earnings using the existing GitHub Actions secret.
`scripts/probe_facebook_monetization.py` encrypts its response to a laptop-held RSA
key using AES-GCM; only the encrypted artifact leaves the Actions runner. The local
report is `data/private/facebook-api-monetization.json`. The dashboard uses a single
numeric approximate-earnings stream with its response currency description; it never
adds overlapping metrics or API versions, nor guesses a `microAmount` conversion.
This is a separately fetched snapshot, not part of the hourly analytics exporter yet.
The bonus probe requests documented `earning_source` / `monetization_tool` breakdowns
and rolling `days_28` metrics. `extra_bonus` is shown only when returned, including an
explicit zero. Rolling observations are never summed: only the latest is shown.
If the rolling estimate and daily-detail subtotal disagree, both stay visible with a
reconciliation note; no all-in total is inferred. Other bonus programs may not be
represented by `extra_bonus`.
Per-Reel earnings/qualified views can require additional monetization-admin access.
Optional CSV reports can still be imported with `scripts/import_facebook_monetization.py`.
All raw financial reports and private keys stay outside tracked source files.

**Content insights:** `scripts/content_insights.py --data-root D:\histold\YT-Automation`
asks local `qwen3:8b` to review measured content patterns. The server and hourly runner
refresh stale reports once per day; **Analyze with Qwen** refreshes on demand. Reports
stay in ignored `data/private/content-insights.json`. The dashboard computes medians
and sample counts itself, considers videos aged 48 hours–28 days, and requires six
measured Facebook videos before surfacing a group finding. Qwen's title observations
are hypotheses, distinct from the measured rankings. Missing values are never treated
as zero. The view uses peak exported metrics rather than age-matched measurements;
it cannot establish causality, viewer locations, or earnings.

The phone layout uses four bottom navigation targets and paged video cards; the desktop
layout keeps the sidebar and sortable table. Filters apply to both presentations.

## Automated content factory (scheduled publishing)

Production scheduling is driven by the **always-on Windows laptop**. Its hourly task
runs `scripts/run_strategist.ps1`, which uses `scripts/dispatch.py` (GitHub CLI) to
dispatch `publish.yml` (`scheduled=true`) at the hours in `data/growth_plan.json`, and
`analytics.yml` every hour (GitHub's own cron for analytics is a 6-hourly backup). In
the workflow, the **publish-hour gate** (`scripts/publish_gate.py`) re-checks every
scheduled run against the same plan and cancels anything outside it or starting after
:40. How many videos a day stays fixed by `growth.posts_per_day` in `config.yaml`; a
missing, stale or invalid plan falls back to `growth.default_hours_utc`. The cap of 10
successful scheduled uploads per IST day and the 45-minute duplicate guard remain.

The **growth agent** (`scripts/growth_agent.py`, daily) scores each publish hour on
reach (Facebook views double-weighted, plus total views) and swaps at most one hour
every `growth.change_interval_days`, within `growth.allowed_hours_utc` (the US + Europe
audience window). Once per ISO week the runner writes `data/reviews/<week>.md`, a
"what's working" review drafted by a local model (Ollama, `qwen3:8b`) over numbers
computed in code; its topic ideas are unverified and advisory only. Every run also
rebuilds the **laptop dashboard** (`scripts/build_dashboard.py` →
`D:\histold\dashboard\index.html`, a self-contained file: no server, works with a
private repo).

Each dispatch that passes the gate is an **independent job slot**:

- The workflow derives `PUBLISH_SLOT=morning` (before 14:00 IST) or
  `PUBLISH_SLOT=evening` from the runner clock and passes it to the pipeline.
- Each new stage records a `job_id` and `slot`, and reserves its topic in
  `data/topic_reservations.json` right after script generation. A reservation
  is idempotent per job (safe to resume) and rejects near-duplicate topics
  across slots, so the morning and evening videos always cover distinct topics.
- The workflow commits `data/topic_reservations.json` back to the repo along
  with the other dedup state.

### Continuous motion

Future Histold scenes are checked at 0.15s and 0.3s, then throughout the narration. Static openings and foreground holds of 1.5 seconds trigger repair or animated local recovery. Recovery scenes use vetted map outlines or animated diagrams with timed short labels; a static map introduction is no longer the recovery template. Run the browser check with `PYTHONPATH=. python scripts/validate_continuous_motion.py` after installing Chromium for Playwright.

### Script provider routing

Script generation defaults to **Claude with automatic OpenAI fallback**: if the
Anthropic call fails or returns invalid JSON, the same prompt is retried on
OpenAI, and the stage records `script_provider` / `script_fallback_used`.
Configure in `config.yaml`:

```yaml
script:
  provider: "anthropic"     # primary provider
  routing: "fallback"       # fallback | random | round_robin (optional modes)
  fallback_provider: "openai"
```

`random` picks a provider per run; `round_robin` alternates runs (state in
`data/provider_rotation.json`). Removing `routing` restores the legacy
single-provider behavior.

### Quality gate

Every **new** job runs a deterministic, local `quality` step between compose
and upload — no API calls, just state/file checks and one ffprobe call:
script structure, upload metadata, topic reservation, narration length, audio,
captions, assets, video presence, resolution, duration ratio, and configured
banned terms. The report is written to `<stage>/quality.json`; a failed check
**blocks the upload** and fails the run. Tune thresholds under `quality:` in
`config.yaml`. Stages created before this feature keep their original step
list and remain resumable/approvable as before.

> Scope note: this phase does **not** change the Pexels asset flow and does
> **not** add AI image generation. Analytics-based topic weighting and
> provider performance selection are deferred.

## Configuration

All non-secret behavior lives in **`config.yaml`** — niche persona, target durations, TTS voice, resolutions, caption styling, privacy status, default tags, provider routing, and quality thresholds. No code changes needed to retune the channel.

## Cost notes

- **edge-tts**: free. **Pexels**: free. **LLM**: a few cents per script. **YouTube API**: free (default quota allows ~6 uploads/day).
- Switching TTS to ElevenLabs or video to an AI-video model adds cost — both are config flags.

## Important / legal

- Use only **royalty-free** music (`assets/music/` README lists sources). Pexels stock is free to use.
- Keep content factually accurate and advertiser-friendly; the script prompt enforces this but **spot-check** outputs, especially before going public.
- Respect YouTube's policies on automated/repetitious content — varied, genuinely informative videos fare best.

## Project layout

```
config.yaml            channel + pipeline settings (edit this)
.env                   secrets (you create from .env.example)
requirements.txt
src/
  config.py            config + env loader
  topics.py            angle picker + dedupe (data/used_topics.json)
  script_generator.py  LLM → title/description/tags/segments
  tts.py               Kokoro / edge-tts / elevenlabs / openai voiceover
  kokoro_voice.py      isolated CPU worker with native token timestamps
  captions.py          word timings → ASS subtitles
  assets.py            Pexels stock fetch per segment
  video_builder.py     ffmpeg assembly
  youtube_uploader.py  Data API v3 upload
  main.py              orchestrator / CLI
scripts/
  setup_oauth.py       one-time YouTube auth
  run_daily.sh         scheduler entry point
assets/music/          drop royalty-free tracks here (optional)
output/                generated videos + metadata
```


### Daily Shorts 3D switch

The switch selects 3D or cinematic 2D for new Histold Shorts. From an authenticated `gh`
checkout, persist the setting for daily and manual GitHub runs:

```sh
python -m src.visual_mode off  # cinematic 2D illustrations, diagrams and subtle sound effects
python -m src.visual_mode on   # 3D renderer
```

The command sets repository variable `HISTOLD_3D`; it does not edit local config.
Use `--repo OWNER/REPO` to target another checkout explicitly. For a local run,
use `VIDEO_3D=off python -m src.main --format short` (or `VIDEO_3D=on`).
An explicit `VIDEO_MODE=claude_motion` (legacy), `cinematic_2d`, or `three_motion` takes priority;
clear repository variable `VIDEO_MODE` to let the persistent switch control runs.
Each new stage records its renderer so changing the switch cannot change an
in-flight render. Older stages keep their original mode behavior. Long videos
continue using b-roll.

Local 3D setup requires Node 22 and `npm ci` at the repository root, alongside
Python dependencies, ffmpeg, and `python -m playwright install chromium`.
HyperFrames and Three.js use the pinned local `node_modules` packages.
# Local earnings collection and data gaps

The dashboard attempts read-only earnings collection hourly. Reports and diagnostics
stay under `D:\histold\YT-Automation\data\private`; they are never committed.
Missing credentials or rejected requests retain the previous successful report.
Dashboard Refresh reloads available exports; it does not authorize a new account.

In PowerShell, from `D:\YT-Automation`:

```powershell
# Enter your existing Meta token privately at the hidden terminal prompt.
powershell -ExecutionPolicy Bypass -File scripts\setup_private_earnings.ps1 -Platform Meta

# First place your Google Desktop OAuth client JSON at:
# D:\histold\YT-Automation\secrets\client_secret.json
# Enable YouTube Data API v3 and YouTube Analytics API in that Google project.
# Then approve the separate read-only earnings login in your browser:
powershell -ExecutionPolicy Bypass -File scripts\setup_private_earnings.ps1 -Platform YouTube
```

Meta's token file is restricted to your Windows user and SYSTEM. It is stored outside
both clones at `D:\histold\secrets\meta-earnings-token.txt`. No additional Meta
permission has been shown to resolve the bonus discrepancy. Existing Page access
can be reused; never paste tokens into chat. The YouTube login requests monetary
analytics and channel-read access, and saves `secrets/earnings-token.json` separately
from the upload token. Use the Histold channel account; the configured channel ID is checked.

Facebook collection requests explicit watch time and retains raw retention graphs;
these are not assumed to equal a completion rate. Instagram metric failures are
isolated and include redacted API reasons. Data gaps show affected-video counts.
Per-post earnings use Page attachment IDs to map videos to posts; unmatched IDs
remain unavailable. Ordinary earnings exclude bonuses, and a returned bonus zero
is not treated as proof of no bonus income. Reported currency and reporting periods
must be verified before combining amounts. The public API may not expose every bonus.

These exporter changes must be deployed to GitHub before hourly Actions collect the
new engagement fields. Local commits alone do not update the Actions runner.
