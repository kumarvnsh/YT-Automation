# Superbot dashboard bridge

Upstream: https://github.com/davidondrej/superbot
Reviewed revision: `652e100fc3412239fd5a07ca7bb8566213c5b6fa` (MIT; license included).

## What runs now

`/bots?channel=histold` and `/bots?channel=shotsnip` share a dashboard workspace.
Jarvis coordinates; channel analysts review performance; Operations diagnoses
reliability. Assignments, activity, results, owner notes, evidence snapshots and
routines persist in SQLite under the laptop's private `bot-workspace` directory.
Notes and the last two completed reports are supplied to subsequent assignments.

The **Dashboard analyst** engine uses the existing capped, read-only AI provider
chain. It works without WSL. **Superbot** assignments wait until the Linux runtime
is connected; they are never silently substituted with another engine.

This release is advisory. Model output is never interpreted as shell commands,
account settings, or publishing actions. The existing upload worker remains the
only automatic publisher. Review an experiment's evidence before applying a change.

## Linux installation

1. Run `scripts/setup_superbot_wsl.ps1` in Administrator PowerShell. Restart Windows.
2. Run `scripts/complete_superbot_setup.ps1` in the normal Windows user account.
   It installs Ubuntu 24.04 if needed, creates a dedicated Linux user, builds the
   pinned Superbot revision, and applies `codex-read-only.patch`.
3. Complete Linux Codex login using the command printed by the installer.
4. Run `scripts/install_superbot_task.ps1` to register the ordinary-user login task
   and start the service. Use `scripts/start_superbot.ps1` for manual startup.

Windows WSL prerequisites were enabled on 18 November 2026; reboot, Linux build
and Linux account authentication remain necessary. Do not label Superbot connected
until its authenticated API is reachable and the Linux CLI is installed/logged in.

## Runtime boundaries

- The Codex adapter creates fresh ephemeral **read-only** sessions. The upstream
  approval/sandbox bypass flag is removed. No other harness is installed by setup.
- Bot screens are disabled. Windows drive automount and Windows executable interop
  are disabled for this dedicated distribution on its next restart.
- The local API listens on loopback port 3007 with a generated password. The
  dashboard proxies only typed bot operations; it never exposes that password.
- No YouTube, Facebook or WhatsApp publishing credentials are copied into Linux.
- One dashboard assignment runs at a time, protected by a shared worker lock.
  Scheduled assignments do not accumulate behind unfinished work for the same bot.
- Interrupted runs require review/retry; submissions with unknown outcomes are not
  automatically repeated. Stopping a native analyst takes effect after its response.
- Routines are off by default. Cadences are hourly, every six hours, or daily from
  the time they are enabled. They schedule analysis, not extra uploads.

## Verification

`tests/test_bot_workspace.py` covers channel isolation, durable queues and notes,
offline Superbot behavior, schedule deduplication, loopback restrictions, remote
message reconciliation, and completed native analysis. A real Shotsnip assignment
was completed through the dashboard using the configured Codex provider.

Linux installation is pending the Windows reboot. Shell syntax and the patch
were checked locally; `cargo test` and the actual Superbot end-to-end assignment
must pass after Linux setup before treating that engine as production ready.
