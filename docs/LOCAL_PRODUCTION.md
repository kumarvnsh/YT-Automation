# Local Histold production (owner's plans instead of paid credits)

Histold videos can be made on the laptop with the owner's **Claude** and **Codex** plans (and local
**Qwen** as a last resort) instead of paid API credits. The paid cloud path is unchanged and stays
the automatic fallback.

## How it works

1. `scripts/dispatch.py` (task *Histold Strategist*, hourly, in the dedicated clone `D:\histold\YT-Automation`)
   reaches a publish hour. With `local_production.enabled: true` it starts
   `scripts/run_local_publish.py` instead of the cloud workflow.
2. The runner does what `.github/workflows/publish.yml` does: claims the slot, runs the pipeline
   step loop (failed pre-upload steps retried twice), records the publication state on `master`.
3. Every AI call goes through `src/plan_ai.py` (`HISTOLD_AI_BACKEND=plan`):

   | Job | Order |
   |---|---|
   | Research (web search + fetch only) | Claude plan → Codex plan |
   | Script, 2D/3D scene visuals, scene repair | Claude plan → Codex plan |
   | Fact assessment, thumbnail hook | Claude plan → Codex plan → Qwen (`qwen3:8b`) |

   The CLIs run in an empty sandbox with no file or shell tools. Each plan has its own Histold
   budget (`local_ai.claude` / `local_ai.codex`, per 5 hours and per week); a plan that reports
   its limit rests until the reset. Usage shows in Studio HQ as `claude_plan` / `codex_plan` /
   `qwen_local` at zero cost.
4. **Plans used up → wait, no paid credits:** if Claude and Codex are both out of limits (before
   the run or at any step before upload), the runner releases the claim, records
   `D:\histold\control\local-publish-retry.json` with the earliest reset time, and Telegram says
   when it will try again. The hourly dispatcher starts `run_local_publish.py --retry` once that
   time has passed; it resumes the unfinished stage. The daily posting cap still applies.
5. **Other failures → paid cloud run:** if local setup is incomplete or a step fails before upload
   for another reason, the runner releases the claim and dispatches the paid cloud workflow for the
   same format, and Telegram says so. An upload failure is never retried (no double posts).

6. **Made ahead (`local_production.make_ahead_hours`, default 2):** the dispatcher starts each video
   that many hours before its slot (`run_local_publish.py --ahead`). It renders as a preview without
   claiming anything; a helper claims the slot from a second checkout (`D:\YT-Automation-env\claim-worktree`)
   when the hour starts, and the finished video uploads straight away, so it goes out on the hour instead of
   30 to 60 minutes late. Progress is kept in `D:\histold\control\made-ahead\<slot>.json`. If the video
   fails before its slot, it is handed back and the usual run at the hour resumes it (research and script kept);
   if it cannot take its slot (cap reached, hours changed) it stays as a preview in Studio HQ. Set 0 to make
   videos at the hour as before.
7. **Parallel scene rendering (`local_production.render_workers`, default 3):** scenes are captured by that many
   Chromium workers at once. Frames are identical to a one-at-a-time render; a scene that fails in a worker
   goes through the usual fallbacks. Set 1 to render one scene at a time. Cloud runs always use 1.

To go back to paid cloud runs entirely, set `local_production.enabled: false`.

## One-time setup (owner)

Tools are installed under `D:\YT-Automation-env` (`histold-local`, `histold-kokoro`, `ms-playwright`).
The laptop needs its own publishing sign-ins, which today exist only in GitHub's secret store:

1. **YouTube upload sign-in** — in PowerShell:
   `cd D:\histold\YT-Automation; D:\YT-Automation-env\histold-local\Scripts\python.exe scripts\setup_oauth.py`
   and approve in the browser with the Histold channel account. This writes `secrets\token.json`
   in the clone (git-ignored).
2. **Meta posting token** — nothing to do: the runner uses `D:\histold\secrets\meta-earnings-token.txt`,
   a non-expiring Meta *system user* token that already has `pages_manage_posts` and
   `instagram_content_publish` (checked 22 Nov 2026). To replace it, generate a new token in
   Meta Business Suite → Settings → Users → **System users** → your system user → **Generate token**
   (Histold app, never expires, permissions listed in GITHUB_ACTIONS_SETUP.md) and save it as
   `D:\histold\secrets\meta-publish-token.txt`, which takes priority.
3. *(Optional)* **Pexels key** — `D:\histold\secrets\pexels-api-key.txt`, only for the stock-footage fallback.
4. Set `local_production.enabled: true` in `config.yaml` and push.

The laptop must be awake at publish hours (the dispatcher already depends on that).
