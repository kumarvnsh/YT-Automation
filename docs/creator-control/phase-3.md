# Phase 3 — Planning and channel workspaces

Open [Studio HQ](https://vnsh.tail9bdcf7.ts.net/studio-hq/). The new **Planning** view includes the IST calendar, Histold topic history, experiment evidence, platform earnings, and API costs.

| View | Source and interpretation |
| --- | --- |
| Calendar | Histold forecasts use the existing dispatcher rules and saved growth plan. Shotsnip displays its scan and posting cadence. Confirmed cadence changes go to the existing Shotsnip scheduler and are locked during an active hold or pending cadence change. |
| Strategy | Histold reservations link to published videos and drafts by job ID. The six-Reel view compares observed YouTube video results at the same age. The Shotsnip pacing report compares the 20- and 30-second variants on each platform. |
| Earnings & costs | Histold platform estimates use the saved financial reports. Facebook estimates can overlap, and display separately. Local API usage tracks attempts, failures, and costs with a recorded pricing basis. |

Use **Workspaces → Shotsnip → Targeted channels → Manage in Studio HQ** to search the watchlist, pause or enable a creator, and add a YouTube channel or Twitch profile. Studio HQ checks the URL first, then queues the change. The original Shotsnip dashboard makes the write, and Studio HQ verifies the result. A pause or enable is rejected if the watchlist changed after you opened it. Unconfirmed changes stay in review.

Use **Workspaces → Shotsnip → Agent → Open in Studio HQ** to read the latest agent review: source priorities with their sample sizes, applied weights, recommendations and recent capture errors. Capture errors show only the exception type and a short message; paths and URLs are removed. **Run review** waits up to 10 seconds for the new report. **Refresh analytics and review** first syncs platform analytics and can take several minutes. **Pause** or **enable learning** is checked against the state you saw. All three go through the original Shotsnip dashboard, which rejects a request while another agent job is running.

Use **Workspaces → Shotsnip → View live status** for the current worker stage, manual waiting count, finished reserve, heartbeat, and recent events. Workspaces also links to the full Live operations tools, Targeted channels, Work queue, Bot workspace, Connections, Agent, and Performance. **Workspaces → Histold** opens the native earnings and strategy summaries as well as the original strategy tools, full earnings report, assistant, and Bot workspace. Original tools use the existing dashboard sign-in.

Use **Planning → Calendar → Shotsnip posting cadence** to review and confirm a change when editing is available. It changes the scan and posting intervals together, as the original control does. Histold’s calendar is a read-only forecast. Topic history currently shows the latest 40 reservations.

Use **Planning → Job usage** for per-job API attempts, failures and recorded cost, broken down by stage and provider. Open a single job from **Strategy → Histold topic history** or **Controls → Shotsnip manual queue** with the **Job usage** button. A cost appears only for attempts with a recorded USD charge and pricing basis; otherwise it reads Unavailable. Attempts without a job ID appear only in provider totals.

The authenticated Rust service reads the finance-enabled snapshot under `D:\StudioHQ\private`. A normal snapshot leaves earnings and API usage empty. The desktop scheduled task writes the private snapshot and refreshes a private local observation index.

## Usage telemetry coverage

| Source | How it reaches the index | Status |
| --- | --- | --- |
| Local Histold and Shotsnip workers | The 5-minute maintenance pass imports `data/private/studio-hq/events` into `D:\StudioHQ\private\observations.sqlite`. | Live. Worker progress only; no paid calls observed yet. |
| Hosted Histold runs (`publish.yml`, `approve.yml`) | Each run uploads a separate `studio-hq-events-<job>` artifact containing only that job's scrubbed events. The hourly Archive tick imports up to 10 new artifacts, maps each one to its exact run and job ID, and records them in `private/hosted-events.json`. Run it on demand with `python -m studio_hq sync-hosted-events`. | Takes effect after the workflow change reaches `master`. |
| Shotsnip commentary and GTA moments | Local Ollama, so there is no API charge. | Not instrumented by design. |

Every imported event is scrubbed to an allowlist (`src/studio_events.py: scrub`): identity fields, provider, model, status, error type, numeric token counts and timing. Prompts, responses, URLs and messages are dropped. Event IDs keep imports idempotent, so re-runs and retries are never counted twice.

Costs stay **Unavailable** by design. Workers record `cost_usd=None` and `pricing_basis=None` until a supported price list or actual billing records are available. Known gaps: API calls made outside a pipeline stage, such as topic strategy before a stage exists, are not emitted. The legacy Shotsnip `pipeline.py` metadata call uses a URL hash rather than a job ID, so its attempts count as unattributed.

