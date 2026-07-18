# GitHub Actions + Dashboard Setup

Repo: **kumarvnsh/YT-Automation** (private, default branch `master`) — already created and pushed.

## 1. Repo Secrets

Settings → Secrets and variables → Actions → New repository secret. Add each of these:

| Secret name | Source | How to get the value (run locally, macOS) |
|---|---|---|
| `ANTHROPIC_API_KEY` | `.env` → `ANTHROPIC_API_KEY` | copy the value directly |
| `PEXELS_API_KEY` | `.env` → `PEXELS_API_KEY` | copy the value directly |
| `YT_CLIENT_SECRET_JSON_B64` | `secrets/client_secret.json` (Histold) | `base64 -i secrets/client_secret.json \| pbcopy` |
| `YT_TOKEN_JSON_B64` | `secrets/token.json` (Histold) | `base64 -i secrets/token.json \| pbcopy` |
| `META_ACCESS_TOKEN` | Meta Business system user | Non-expiring token assigned to the Histold Facebook Page and Instagram professional account |
| `OPENAI_API_KEY` (optional) | `.env` | only if `script.provider: openai` |
| `ELEVENLABS_API_KEY` (optional) | `.env` | only if `tts.provider: elevenlabs` |

**Not a repo secret:** the fine-grained PAT used by the dashboard (scope: this repo's
Actions read/write, Contents read) — generate at
`github.com/settings/tokens?type=beta`, paste it into the dashboard's Settings modal only
(stored in your browser's `localStorage`, never in GitHub).

**WhatsApp notifications will not fire from CI** unless `OPENWA_BASE_URL` points somewhere
publicly reachable (it's currently `localhost`, unreachable from a GitHub-hosted runner) —
this is expected; the pipeline degrades gracefully with WhatsApp env vars unset.

The `META_ACCESS_TOKEN` needs both publishing and analytics permissions. Include
`pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `instagram_basic`,
`instagram_content_publish`, and `instagram_manage_insights`, and assign the system user
the Page's content and insights tasks. The Meta analytics exporter now fails visibly when
these permissions are missing instead of showing zero or stale view counts.

## 2. Enable GitHub Pages

Settings → Pages → Source: **Deploy from a branch** → Branch: `master`, folder **`/docs`**.
You'll get a public URL like `https://kumarvnsh.github.io/YT-Automation/`.

## 3. Dashboard first-run

1. Open the Pages URL (or serve `docs/` locally with `python3 -m http.server 8000 --directory docs`).
2. Click **⚙ Settings**, fill in:
   - GitHub Owner: `kumarvnsh`
   - Repository: `YT-Automation`
   - Branch: `master`
   - PAT: the fine-grained token from step 1
3. Save. The status LED should turn green once `data/analytics.json` / `data/trends.json`
   exist (they're written by the `Export Analytics & Trends` workflow — trigger it once
   manually from the Actions tab if you don't want to wait for its 6-hour schedule).

## 4. Verification order (do this before trusting the daily schedule)

1. `workflow_dispatch` → **Publish Video** with `dry_run: true` — confirms secrets decode
   and the script-only path works.
2. `workflow_dispatch` with `no_upload: true` — confirms ffmpeg/render/asset-fetch on the
   runner; check the uploaded artifact and the Approval Queue panel on the dashboard.
3. `workflow_dispatch` with `privacy_status: private` for a real first live upload — confirm
   the channel-mismatch safety lock (`youtube.expected_channel_id`) passes.
4. Only after step 3 succeeds, trust the `schedule:` cron in `publish.yml` for unattended runs.

## 5. cron-job.org schedule (2 shorts/day)

Publishing is triggered externally by cron-job.org (GitHub's own cron proved unreliable).
Current cadence (updated 2026-07-18): **2 shorts/day at 9:30 AM and 9:30 PM IST**.
The evening slot moved from 6:30 PM on 2026-07-18: 13:00 UTC is a dead zone for
both the US and India, while 16:00 UTC lands at US midday.

A video goes live roughly **3 minutes after** its trigger fires (generate, render,
upload), so the two slots publish at ~04:03 and ~16:03 UTC. Aim the trigger, not
the publish time.

Keep the evening trigger before ~23:30 IST. The daily cap counts uploads per IST
day, so a publish that rolls past midnight IST desyncs the counter and would let a
third upload through.

1. Log into cron-job.org → Cronjobs.
2. Keep exactly **two** jobs: one at **9:30 AM IST**, one at **9:30 PM IST** (job
   timezone Asia/Kolkata; in UTC that is 04:00 and 16:00).
3. Both jobs use the identical request:
   - `POST https://api.github.com/repos/kumarvnsh/YT-Automation/actions/workflows/publish.yml/dispatches`
   - Body: `{"ref":"master","inputs":{"format":"short","scheduled":"true"}}`
   - Headers: existing `Authorization: Bearer <PAT>` + `Accept: application/vnd.github+json`.
4. "Test run" each job once and confirm a **Publish Video** run appears in the Actions tab
   (it will really upload — test near an intended slot, or temporarily add
   `"no_upload":"true"` to the body for the test).

The workflow additionally enforces a **hard cap of 2 scheduled uploads per IST day**
(counted from `data/published_index.json`), so a stray third cron job or a double-fire
self-cancels. Manual dashboard dispatches are exempt from the cap.

## 6. OAuth re-consent for retitle/republish (one-time)

The dashboard's Underperformers panel (retitle / repost buttons) needs the full
`youtube` manage scope, which the original token lacks. After pulling the code that adds
`MANAGE_SCOPE`:

1. Locally: remove `secrets/token.json`, then `python scripts/setup_oauth.py` and approve the
   consent screen (it now includes "Manage your YouTube account"). Pick the Histold channel.
2. `base64 -i secrets/token.json | pbcopy` → update the `YT_TOKEN_JSON_B64` repo secret.
3. Sanity: `python scripts/whoami_youtube.py`, then trigger the analytics workflow once.

Until this is done, retitle/repost runs fail with an explicit "token lacks the full
youtube scope" error; normal uploads are unaffected.

**Auto-republish** is also gated on this re-consent. With `republish.auto: true` in
`config.yaml`, every analytics export (~6h) checks for videos under
`republish.view_threshold` views after `republish.min_age_hours` and automatically
dispatches one `republish.yml` run (`republish.mode`: `repost` deletes + re-uploads with
a fresh title; `retitle` only swaps the title). Each video gets exactly ONE automatic
attempt, ever — after that only the dashboard's manual buttons apply. Set
`republish.auto: false` to go back to manual-only.
