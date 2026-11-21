# Phase 0 — Implementation and operating notes

Status: hourly Drive archiving, guarded Shotsnip raw cleanup, and finished-clip cleanup for Shotsnip, Histold, and Astrotold are active.

## What is implemented

- `python -m studio_hq` supplies inventory, candidate planning, event indexing, Drive quota, bounded GitHub artifact collection, archive verification and isolated restore commands.
- Additive telemetry in both repos records stage/job observations and available provider usage without recording prompts, credentials, responses, signed URLs or exception messages.
- Each event has its own atomic JSON file. The observation index imports by unique event ID into a separate SQLite database and never writes existing publishing ledgers.
- Future finished renders receive completion markers tied to the actual filename, size and modification timestamp. Reobserving an unchanged render preserves its original completion time.
- Existing media without reliable completion records remains ineligible for automatic age-based archiving. It needs a provenance-aware migration; modification time alone is insufficient.
- The archive worker rechecks current eligibility, packages only approved final/supporting artifacts, verifies remote size and MD5 and records Drive file IDs. SHA-256 verifies restored contents.
- Content-derived archive identities and immutable uploads prevent accidental replacement. Retrying the same package reuses its manifest.
- The worker has a cross-process lock. Local deletion is deliberately absent from this initial implementation and configuration must keep `local_cleanup_enabled=false`.

## Live ownership audit

| Component | Existing owner / boundary |
| --- | --- |
| Dashboard | `Histold Dashboard` task; current Tailscale Serve forwards to loopback port 8799 |
| Operations and recovery | `Histold Jarvis Operations`; uses existing locks and can manage worker health |
| Strategist | Task refers to `D:\histold\YT-Automation`, a separate data checkout; do not replace or reset it |
| Operator | Existing `Histold Operator` task and its existing policy |
| Telegram | Existing task; an alternate client, not independent Windows-host failover |
| Histold/Astrotold publication | Existing GitHub publish workflows and slot/claim controls; relevant render artifacts expire after 14 days |
| Shotsnip local processing | Shared byte-range worker lock and existing scheduler/manual queue |
| Shotsnip publication | `work/scheduler/state.json`, operation markers, confirmed platform IDs and reconciliation |
| Shotsnip Mac execution | Existing workflows target `[self-hosted, macOS, yt-shorts]`; no new agent is installed |
| Studio HQ | Separate inventory/transfer tools; no publisher or existing scheduler ownership |
| Studio HQ Archive | Separate hourly Windows task, limited to one new package per run. Runs while the task's Windows user is signed in. |

At initial inspection, local Shotsnip state contained queued publication jobs and attention states. Preserve these dependencies; do not erase them to force retries. Other work may legitimately update this queue while Phase 0 is developed.

No existing task, publishing schedule, privacy setting, credential file, worker pause setting or Tailscale route is changed by these tools. New observations take effect when an existing process next imports the modified code; running processes are not restarted here.

## Private runtime files

| Location | Purpose |
| --- | --- |
| `D:\StudioHQ\config.json` | Local root mapping and archive settings; no OAuth tokens |
| `D:\StudioHQ\private\rclone.conf` | Drive OAuth configuration; restricted Windows ACL, never commit |
| `D:\StudioHQ\tools\rclone\rclone.exe` | Official release binary verified against release SHA-256 sums |
| `D:\StudioHQ\state\inventory.json` | Local disk inventory |
| `D:\StudioHQ\state\archive-plan.json` | Eligibility and protection reasons |
| `D:\StudioHQ\state\observations.sqlite` | Separate observation index |
| `D:\StudioHQ\state\manifests` | Verified archive manifests |
| `D:\StudioHQ\state\archive-receipts` | Records successful remote manifest uploads for skipping identical completed packages |
| `D:\StudioHQ\state\last-archive-tick.json` | Most recent hourly archive result |
| `D:\StudioHQ\state\restore-checks` | Restore proof records |
| `D:\StudioHQ\state\github-artifacts` | Bounded local copies of retained Actions artifacts |
| `<channel-root>\data\private\studio-hq\events` | Atomic event spool, ignored by Git |

Selected Drive destination: [user-supplied archive folder](https://drive.google.com/drive/folders/YOUR_DRIVE_FOLDER_ID).

The rclone connection uses the app-created-files scope and an explicit root folder ID. Archive channels are created inside this folder. Existing unrelated files are not an input to cleanup.

## Current validation results

- The selected folder accepted a small test upload; remote checksums and a downloaded restore matched.
- Three retained GitHub artifacts were collected locally. One Histold package containing a final video and six supporting files was uploaded and all seven remote files verified.
- The first real package restore hit Google's shared rclone OAuth project rate limit. A batched retry restored all seven files. Each restored file matched the SHA-256 checksum recorded in the archive manifest.
- Provider rate limits now persist a three-minute transfer backoff. Errors stay in the ACL-protected private directory; public output does not include signed URLs or tokens.
- New archives use the existing desktop OAuth client from Google Cloud project `yt-clipping-automation` with a separate Drive token. A real Histold package passed upload verification and checksum verification after all seven files were restored.
- New archives go under `StudioHQ/` inside the selected Drive folder. The earlier verified archive uses the original connection. Restore it with `python -m studio_hq --config D:\StudioHQ\config-legacy-drive.json restore D:\StudioHQ\state\manifests\LEGACY_ARCHIVE_ID.json`.
- 23 new phase-zero safety tests pass. The broader Windows-compatible Histold selection passed 183 tests before the final added backoff test; nine lock/artifact tests also passed in an isolated Linux copy. Shotsnip passed 102 selected tests; two POSIX signal failures were reproduced using unchanged HEAD code on Windows.
- Histold's existing `new_stage` imports `fcntl`; moving execution from its existing POSIX workers to native Windows needs a separate, tested lock compatibility change. No execution migration is performed here.

Machine-readable local progress is recorded in `D:\StudioHQ\state\phase-0-status.json`. These results describe this run rather than permanent service health.

## Commands

Run from `D:\YT-Automation` using Python 3.10 or newer. The phase-zero CLI uses only the standard library.

```powershell
python -m studio_hq inventory
python -m studio_hq plan
python -m studio_hq sync-events
python -m studio_hq drive-status

# List retained run artifacts, without downloading.
python -m studio_hq github-artifacts

# Collect a bounded batch, prioritizing the earliest expiry in the listed page.
python -m studio_hq github-artifacts --collect --limit 3

# Copy and verify eligible local completed jobs. Local media stays in place.
python -m studio_hq archive --limit 1

# Same bounded copy-only operation used by the hourly task.
python -m studio_hq archive-tick --limit 1

# Preserve collected final videos even when source completion age is uncertain.
# Such archives explicitly record artifact creation as an upper bound; no cleanup.
python -m studio_hq archive-collected --limit 1

# Restore into an isolated new directory and verify SHA-256.
python -m studio_hq restore D:\StudioHQ\state\manifests\ARCHIVE_ID.json
```

Commands writing archive/collection/restore state are serialized by the Studio HQ lock. They do not acquire publisher ownership or execute downloaded artifacts. Artifact listing currently covers the first 100 items; pagination must be added before calling it a complete historical inventory. Download/extraction budgets and free-space checks bound artifact collection.

## Contracts, version 1

- **Job reference:** `(channel, source job ID)`, preserving existing IDs. A worker progress observation without a job uses the scheduler identity; it is not treated as a new publication job.
- **Event:** unique `event_id`, UTC `observed_at`, channel, job ID, stage, kind and narrowly scoped observations.
- **API usage:** provider, operation, model, observed token/search units when returned, response/failure state and elapsed time. `cost_usd` and pricing basis are null until a versioned price basis is configured. SDK-internal retries are not individually observable and are explicitly labeled as such.
- **Finished marker:** schema version, channel, job ID, filename, completion time, file size and modification timestamp. Not a publication receipt.
- **Archive manifest:** content-derived archive ID, original job/time provenance, explicit approved members, local hashes, observed remote IDs and verification records. Not authorization to delete.
- **Publication:** existing ledger/operation marker remains authoritative. Neither an event nor a successful archive can declare a platform upload successful.

These observations currently cover pipeline-scoped paid calls and Shotsnip metadata generation. CLI-subscription/local-model work is not assigned fabricated per-call charges. Telemetry failures must never fail a production call.

## Retention activation still required

The agreed future behavior remains: finished packages at 48 hours, raw `.ts` segments at 24 hours, dependency/Keep protections, and no local removal before verified remote storage where archiving is required.

The hourly task copies only. Older Shotsnip clips without finish markers qualify for copying if both platforms confirmed publication at least 48 hours ago, the exact bytes still match the publishing ledger, and the regular protection checks pass. That publication timestamp is an upper bound on completion and is recorded as such.

The 21 November 2026 read-only audit found 44,212 raw segments (26.7 GiB) that passed the age and closed-capture checks. It excluded 627 segments in runs referenced by pending work. Both archived Shotsnip videos matched their local checksums, but neither qualified for removal because the original completion records were missing. The report is at `D:\StudioHQ\state\cleanup-dry-run.json`. These are point-in-time results. Recheck live dependencies under coordinated locks before deletion.

Shotsnip's scheduler routes raw segments through `raw_retention.py`. The guard uses the scheduler's held lock, acquires the live worker lock, and reserves the manual queue for writes before inspecting dependencies. It defers if the live worker or a queue writer is active and processes up to 250 eligible segments per scheduler pass. The raw `.ts` retention is 24 hours. Other media covered by the existing cleanup uses the separate three-day setting. The first guarded batch removed 250 segments and freed 246,614,264 bytes. The report is at `D:\StudioHQ\state\raw-retention-first-run.json`.

The one-clip finished-media pilot restored and verified all nine files before removing a single Shotsnip MP4. Its publication record showed both platforms had confirmed upload more than 48 hours earlier and matched the exact video checksum. The pilot rechecked local hashes, Keep markers, and job references under both worker locks and a manual queue write reservation. The restore and supporting files remain available. See `D:\StudioHQ\state\finished-clip-cleanup-pilot.json`.

The hourly Windows task runs `archive-tick --limit 1 --cleanup-shotsnip` in the Shotsnip Python environment. Each run removes at most one eligible finished MP4 across all channels. A receipt, full-package download and checksum validation, readable restored video, and a fresh eligibility check are required. Supporting files remain available locally.

- Scheduled Shotsnip clips require confirmed publication to both platforms. Manual Shotsnip clips require a published job, confirmation for its selected destinations, and a match to the publishing ledger hash. Publication time provides a conservative 48-hour age bound when the original finish marker is missing.
- Locally produced Histold and Astrotold clips use their recorded completion time, completed publishing state, and a shared stage lock. Unmanaged legacy local stages stay protected until their provenance is established.
- The worker also collects at most one older retained GitHub Actions artifact per hourly run. For hosted story outputs, the artifact creation time is the 48-hour age bound. The archived video must match the workflow ZIP and the recorded publishing stage must have finished. Cleanup removes the extracted local MP4 and retains the original ZIP for provenance. GitHub continues to manage its original artifact retention.

Read the latest result in `D:\StudioHQ\state\last-finished-cleanup-tick.json`. The `shotsnip_finished_cleanup_enabled` setting is the existing cleanup switch. `story_finished_cleanup_enabled` allows the story channels within that step. `archive_hosted_stories` controls hosted artifact intake.

Remaining retention work:

1. Review legacy local files without reliable production records before making them eligible.
2. Track the hourly backfill and check for hosted artifacts that expire before intake reaches them.
3. Coordinate any other local checkout that produces story renders. This checkout's preservation settings apply when it runs the local story pipeline. Hosted runs continue to use their existing GitHub artifact policy.

## Validation

`tests/test_studio_hq.py` tests failed/corrupt uploads, restored content, changed sources, stale plans, protected jobs, Keep flags, uncertain publication, manifest path validation, idempotent archive retries, locking, idempotent event imports, timestamp preservation, redaction and nonfatal telemetry failures.

Broader pipeline tests should run in an isolated test environment. The existing Histold suite contains POSIX-only `fcntl` assumptions, and the legacy clipping heatmap tests contain POSIX-only signal assumptions; report those separately from introduced regressions. Do not alter working runtime dependencies merely to run tests.
