# Studio HQ handoff

Continue Phase 3 in `D:\YT-Automation`. The sibling Shotsnip repo is `D:\YT-Cliping-Automation`. The user wants one private dashboard on Windows, Android and Mac through [Studio HQ on Tailscale](https://vnsh.tail9bdcf7.ts.net/studio-hq/), using the existing Windows host and workers.

## Phases and task list

| Phase | Status | Task list |
| --- | --- | --- |
| **0 · Storage and baseline** | Operational | Hourly Google Drive archive. Finished Histold, Astrotold, and automatic and manual Shotsnip clips become eligible after 48 hours. Verify the Drive copy before removing any eligible local video. Preserve active jobs, keep flags and uncertain publications. Raw `.ts` segments use local 24-hour retention. |
| **1 · Cross-device dashboard** | Live | Rust/Axum service and responsive React UI over Tailscale. Overview, platform analytics, activity, health, and media archive. Histold and Shotsnip are the only channels in HQ. Astrotold continues independently. |
| **2 · Controls and reliability** | Live for core controls | Shotsnip previews, approvals, cancellation, processing retry, and scheduler pause/resume. Histold drafts and approvals. Strategist, archive now, verified restore, daily queue and command backups, and disk warnings. Stage edits and new notifications were deferred. |
| **3 · Calendar, strategy and money** | In progress | Live calendar, topic history, experiments, earnings, workplace links, and Shotsnip worker status. Shotsnip cadence editing uses the existing owner and respects the active hold. Per-job costs and usage are the current work in progress. |
| **4 · Native apps and distributed workers** | Planned and optional | Evaluate a Tauri shell, native notifications, and a Mac worker. Keep browser access. Add a worker only where it solves a problem. Test ownership, recovery and duplicate prevention before retiring old tools. |

## Resume here

1. **Job-level usage — deployed 2026-11-21.** Planning → Job usage is live, with drill-downs from Histold topic history and the Shotsnip manual queue. Verified on desktop and mobile against the live snapshot (197 jobs, no API attempts yet, costs Unavailable) and a fixture with priced, unpriced, failed and unattributed attempts. Previous index backed up under `D:\StudioHQ\dashboard\before-job-usage-*`.
2. **Telemetry coverage — built 2026-11-21, needs push.** Hosted runs now export a scrubbed `studio-hq-events-<job>` artifact, and the hourly Archive tick imports it by exact run and job ID. Local imports are scrubbed too. Nine fixture tests in `tests/test_studio_hq_telemetry.py` cover failures, retries, multiple providers, priced versus unpriced attempts, negative charges, unattributed records, path escapes and idempotent re-import. The workflow change is uncommitted. Costs remain unavailable until a supported pricing basis or billing source exists. See the coverage table in phase-3.md.
3. **Workspace tools — Targeted channels and Agent native 2026-11-21.** Studio HQ can list, pause, enable and add Shotsnip targets, and can read the agent review, run a review or analytics refresh, and toggle learning. Every write goes through the original dashboard's `/api/shotsnip/action` (the owner) and is verified or held for review. Still linked out: the full automatic Work queue, Bot workspace and Connections (status only).
4. **Phase 3 strategy — idea trace, experiment comparisons and recommendations done 2026-11-21.** Topics trace from idea to run, publications, labelled observations and cost, with explicit gaps. The growth trial is compared only within one age window, platform and metric, with a minimum of 3 posts per lane and an overlap check. Rule-based recommendations state their evidence, uncertainty and an advisory action. Remaining: an assistant with scope-limited evidence that proposes actions only through validated existing controls, and a matched-age recorder for `voice-title-2x2`. The Histold calendar is still a read-only forecast.
5. **Validate end to end.** Test real job-to-publication joins, recovery and privacy. Keep cross-device access on Tailscale. Only then assess the optional Phase 4 work.

## Live deployment and checks

- Rust source lives in `studio_hq/hub`, React in `studio_hq/ui`, and the Python snapshot and command adapters in `studio_hq`.
- Runtime frontend is `D:\StudioHQ\dashboard\web`. Build with `npm run build` from `studio_hq/ui`. Back up the live index and copy the new hashed assets before replacing `index.html`. Frontend-only releases need no service restart.
- **Studio HQ Dashboard** and **Studio HQ Archive** are independent Windows scheduled tasks. Finance snapshots and the observation index live under `D:\StudioHQ\private`. Keep those outside the static web root and verify that ordinary snapshots omit finance.
- Run `D:/YT-Cliping-Automation/.venv/Scripts/python.exe -m unittest tests.test_studio_hq_phase3 tests.test_studio_hq_dashboard tests.test_studio_hq_controls tests.test_studio_hq -q` in `D:\YT-Automation`. The handoff source passed all 44 tests and the frontend build.
- Preserve the existing automation and Shotsnip hold. Use fixtures for publishing and cadence changes. The workspace already has uncommitted changes. Review the diff before committing or pushing.

See the [merged plan](README.md), [storage notes](phase-0.md), [controls and recovery](phase-2.md), and [Phase 3 operating notes](phase-3.md).
