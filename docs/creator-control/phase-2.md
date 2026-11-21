# Phase 2 — Studio controls

Open [Studio HQ](https://vnsh.tail9bdcf7.ts.net/studio-hq/) through your Tailscale account and select **Controls**. Windows remains the host for the Rust service and local workers.

| Action | Where the work runs |
| --- | --- |
| Queue a Shotsnip replay or timestamp preview | Existing Shotsnip manual queue and worker |
| Review and approve a Shotsnip preview | Existing Shotsnip publishing worker and posting rules |
| Cancel or retry Shotsnip processing | Existing manual queue. Running jobs stop at a safe stage. |
| Pause or resume automatic Shotsnip posting | Existing dashboard scheduler. An active batch hold or unresolved publication blocks resuming. |
| Render a Histold draft | Existing GitHub Actions workflow in preview mode |
| Approve a Histold draft | Existing Histold approval workflow after checking the upstream approval list and video artifact |
| Run the Strategist | Existing Windows scheduled task |
| Archive or restore a clip | Existing Drive archive integration. Archive now copies and verifies one eligible package. The scheduled cleanup owns local deletion. |

A restore rechecks the package hashes and writes files under `D:\StudioHQ\state\restores`. The cache has a 1 GiB limit and each restore needs an additional 1 GiB of free disk headroom. To watch on a different device, use the Drive link in **Archive**. The dashboard displays a disk warning below 10 GiB free.

## Recovery

The Windows service runs independently of an open browser. **Activity from this dashboard** records queued work and the last known outcome. A lost response can be retried with the same request ID. Shotsnip checks the exact job revision before changing it. An interrupted or uncertain publishing request stays in review for reconciliation through the existing dashboard or GitHub workflow.

Verified daily SQLite copies of the manual queue and host command journal live under `D:\StudioHQ\private\backups`. Each keeps the current copy and the previous daily copy. To recover, use the last verified copy with the corresponding service stopped, preserve the existing database for investigation, and reconcile any later platform results against the publisher's live ledger before restarting workers. These on-host backups cover queue records and command receipts. Drive media retention has its own verification records.

The Phase 1 binary, frontend, and settings are kept for rollback. To roll back, stop only the **Studio HQ Dashboard** task, restore the Phase 1 files from `D:\StudioHQ\dashboard\phase1-backup` and the private settings copy, set `dashboard_controls_enabled` to false in `D:\StudioHQ\config.json`, and restart the dashboard task.

Studio HQ covers Histold and Shotsnip. Astrotold uses its existing publishing automation and Drive retention outside the dashboard. Stage editing, new notification channels, and scheduler migration remain outside this rollout.

## Analytics

The overview uses the existing creator exports. **Analytics** gives each channel its own platform selector, source timestamps, and available daily reports or lifetime rankings. Histold Facebook Reel plays and legacy video views have separate sources. Shotsnip YouTube totals come from owner reporting days, and Facebook Page video views follow the reporting periods from Facebook. Missing daily observations are blank.

Phase 3 starts with the **Shotsnip pacing experiment** under **Analytics → Shotsnip**. The experiment displays eligible and observed clips by platform and at 24 or 72 hours. An unobserved median is blank.
