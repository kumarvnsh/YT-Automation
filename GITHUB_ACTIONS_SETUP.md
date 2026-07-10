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
| `YT_MEDIMYTH_CLIENT_SECRET_JSON_B64` | `channels/medimyth/secrets/client_secret.json` | `base64 -i channels/medimyth/secrets/client_secret.json \| pbcopy` |
| `YT_MEDIMYTH_TOKEN_JSON_B64` | `channels/medimyth/secrets/token.json` | `base64 -i channels/medimyth/secrets/token.json \| pbcopy` |
| `OPENAI_API_KEY` (optional) | `.env` | only if `script.provider: openai` for either channel |
| `ELEVENLABS_API_KEY` (optional) | `.env` | only if `tts.provider: elevenlabs` for either channel |

**MediMyth OAuth is not set up yet.** Before you can generate its secrets, run locally:

```bash
python scripts/setup_oauth.py channels/medimyth/config.yaml
python scripts/whoami_youtube.py channels/medimyth/config.yaml
```

The second command prints the channel ID — paste it into `channels/medimyth/config.yaml`'s
`youtube.expected_channel_id` (currently blank) so uploads are locked to the right channel,
then commit that one-line change.

**Not a repo secret:** the fine-grained PAT used by the dashboard (scope: this repo's
Actions read/write, Contents read) — generate at
`github.com/settings/tokens?type=beta`, paste it into the dashboard's Settings modal only
(stored in your browser's `localStorage`, never in GitHub).

**WhatsApp notifications will not fire from CI** unless `OPENWA_BASE_URL` points somewhere
publicly reachable (it's currently `localhost`, unreachable from a GitHub-hosted runner) —
this is expected; the pipeline degrades gracefully with WhatsApp env vars unset.

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
Current cadence (configured 2026-07-10): **2 shorts/day at 9:00 AM and 6:00 PM IST**.

1. Log into cron-job.org → Cronjobs.
2. Keep exactly **two** jobs: one at **9:00 AM IST**, one at **6:00 PM IST** (job
   timezone Asia/Kolkata; in UTC that is 03:30 and 12:30).
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

1. Locally: `rm secrets/token.json`, then `python scripts/setup_oauth.py` and approve the
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
