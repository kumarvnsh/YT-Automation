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
