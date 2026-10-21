# YT-Automation — Daily "History / Did-You-Know" Video Bot

Fully scripted pipeline that, on each run, **writes a script → generates a voiceover → pulls matching stock footage → burns in captions → renders an MP4 → uploads it to YouTube**. Supports both vertical **Shorts** and horizontal **long-form** videos. Point a scheduler at it and it runs hands-off every day.

Histold's current automatic Shorts prioritize **Facebook and surprising world history**, with YouTube and Instagram distribution retained. New jobs interleave eight regional groups, three evergreen series and 30/50-second duration tests. Facebook is published first; a missing Reel ID fails the step, and known successful platform IDs survive retries. The existing two daily schedule is unchanged. Manual topics and existing stage snapshots keep their original settings.

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

## Automated content factory (scheduled publishing)

Production scheduling is driven by **cron-job.org**, which dispatches the
`publish.yml` GitHub Actions workflow twice a day (9:00am / 6:00pm IST). Each
dispatch is an **independent job slot**:

- The workflow derives `PUBLISH_SLOT=morning` (before 14:00 IST) or
  `PUBLISH_SLOT=evening` from the runner clock and passes it to the pipeline.
- Each new stage records a `job_id` and `slot`, and reserves its topic in
  `data/topic_reservations.json` right after script generation. A reservation
  is idempotent per job (safe to resume) and rejects near-duplicate topics
  across slots, so the morning and evening videos always cover distinct topics.
- The workflow commits `data/topic_reservations.json` back to the repo along
  with the other dedup state.

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
