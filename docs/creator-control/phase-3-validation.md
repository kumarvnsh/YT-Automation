# Phase 3 end-to-end validation — 21 November 2026

Live checks against production data on the Windows host, plus fixtures for anything that would change a live setting.

## Job-to-publication joins

| Check | Result |
| --- | --- |
| Histold reservations, index and analytics | 377 reservations and 359 published stages. All IDs are well-formed. Every publication has a YouTube ID. Of the last 50, 49 have YouTube analytics (one is outside the 50-video analytics window), and 44/44 Facebook and 44/44 Instagram posts match their observations. |
| Unpublished reservations | 22. These are runs that never published, and their traces say so. Four July publications predate reservations. |
| **Lost original records (fixed)** | `scripts/merge_json_state.py` identified index entries by `stage_dir_name`. A repost reuses its original's stage, so each merge overwrote the original publication record with the repost and left duplicate rows. 7 originals were missing, and 8 reposts appeared 2–3 times. The merge identity now includes `republished_from`, and exact repeats collapse. All 7 originals can be recovered from git history; that repair has not been applied. |
| Trace display | Repeated index rows now show once. A repost whose original record is missing is named as a provenance gap. |
| **Shotsnip live-capture clips (fixed)** | 4 scheduled clips published from `work/live/<run>/` had no job ID, because only `work/scheduler/` paths were parsed. Both are now parsed, and all 50 Shotsnip activity rows can be joined. |
| Shotsnip worker events | Automatic-worker progress without a `studio_job_id` is filed under `scheduler`. It cannot be attributed to a clip, and it carries no API usage. |

## Privacy and access

| Check | Result |
| --- | --- |
| Network exposure | The original dashboard (8799), the hub (8801) and Ollama listen on `127.0.0.1` only. Tailscale Serve is tailnet-only, with no Funnel. All four tailnet devices belong to the owner. |
| **Forwarded requests (fixed)** | The hub accepted a request with a local Host header and no identity. Tailscale Serve keeps the client's Host header and adds identity only for user-owned devices, so a tagged device could have reached the dashboard. The hub now refuses any forwarded request without the owner's identity. Checked live: direct local 200; forwarded without identity 403; tailnet host without identity 403; a different account 403; the owner's device 200. |
| Static web root | 28 static files only. No snapshots, data or keys. |
| Private data | `D:\StudioHQ\private` grants access to the owner and SYSTEM only. Earnings, API usage and assistant history appear only in the private snapshot (tested). |
| Dashboard key | `D:\histold\dashboard_key.txt` inherits read access for all local users and modify access for authenticated users. Owner action recommended: restrict it to the owner and SYSTEM. |

## Recovery and duplicate prevention

The **Studio HQ Dashboard** task was stopped, the binary replaced and the task restarted, with about 3 seconds of downtime. While it was down, one assistant question was queued three times: twice with the same request ID and once with a new ID. All three resolved to one command, which ran once after the restart and produced one answer. Snapshot refresh resumed within 30 seconds. An interrupted command stays in review and is never re-run (unit-tested). A real reboot and a mid-command kill were not exercised.

## Not covered here

- Cross-device use on the Android phone, tablet and Mac needs a person to check on those devices.
- The first real hosted usage artifact, target change, agent action and assistant-proposal apply will be verified on first use.
- `voice-title-2x2` has no matched-age recorder.
