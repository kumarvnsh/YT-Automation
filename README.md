# YT-Automation — Daily "History / Did-You-Know" Video Bot

Fully scripted pipeline that, on each run, **writes a script → generates a voiceover → pulls matching stock footage → burns in captions → renders an MP4 → uploads it to YouTube**. Supports both vertical **Shorts** and horizontal **long-form** videos. Point a scheduler at it and it runs hands-off every day.

## How it works

```
topic (angle + dedupe)
   └─> script_generator   LLM writes title, description, tags, segmented narration
         └─> tts          edge-tts voiceover (+ word-level timing, free)
               └─> captions   word timings → animated ASS subtitles
               └─> assets     Pexels stock video/photo per segment (keywords)
                     └─> video_builder   ffmpeg: clips + Ken-Burns + music + captions
                           └─> youtube_uploader   Data API v3 upload (OAuth)
```

Each run writes everything to `output/<timestamp>_<format>_<slug>/` (script.json, voiceover.mp3, captions.ass, video.mp4, metadata.json).

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
   - Voiceover uses **edge-tts** which is **free, no key**. (Optional: ElevenLabs/OpenAI for premium voices.)

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

## Configuration

All non-secret behavior lives in **`config.yaml`** — niche persona, target durations, TTS voice, resolutions, caption styling, privacy status, default tags. No code changes needed to retune the channel.

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
  tts.py               edge-tts / elevenlabs / openai voiceover
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
