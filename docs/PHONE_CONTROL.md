# Phone control: Telegram bot and admin runner

Telegram brings updates to your phone and lets you approve actions with a tap. The admin runner performs a small, fixed set of Administrator tasks after your approval.

## How it works

1. The Bot Office, the Operations monitor and the Operator send updates to Telegram (and to WhatsApp, as before).
2. A new problem arrives with a **Fix now** button. Pressing it approves the fix at once, so the Operator does not wait for a second confirming review. The Operator is started immediately, and it reports the outcome back in Telegram.
3. Commands give you status and controls at any time:

   | Command | What it does |
   | --- | --- |
   | `/status` | Open problems, Operator state, dashboard and admin runner health |
   | `/problems` | Each open problem with its fix status and a **Fix now** button |
   | `/review` | Run an Operations review for Histold or Shotsnip now |
   | `/operator` | Run the Operator now, or pause and resume it |
   | `/dashboard` | Restart the dashboard. A copy stuck as Administrator is stopped through the admin runner first. |
   | `/admin` | Allowlisted admin actions |

## Safety

- **Owner only.** Only the Telegram account paired with a one-time code from the laptop is obeyed. Other people, and group chats, are ignored silently.
- **Fixed actions only.** Your messages are never run as commands. Each button carries only an approval id; the action it runs was fixed by the laptop's code. Buttons work once and expire after an hour, and every decision is recorded in `D:\histold\control\approvals.sqlite`.
- **Restricted admin runner.** It runs only the scripts in `D:\histold\admin-runner\actions`, a folder only Administrators can change. Requests name one allowlisted action and carry no arguments, so text from any request cannot become a command. Changing the list means editing `admin\` in the repository and re-running the installer as Administrator.
- **Protected from the Operator.** The Operator may not edit these control files.

Current admin actions: `ping` (makes no changes), `stop-elevated-dashboard`, `stop-stale-monitors` and `restart-tailscale`.

## Setup (once)

1. In Telegram, open **@BotFather**, send `/newbot`, choose a name, and copy the token it gives you.
2. On the laptop, in a normal PowerShell, run the setup script. Paste the token when asked; it is not shown or logged, and it is stored readable by you only.

   ```powershell
   powershell -ExecutionPolicy Bypass -File D:\YT-Automation\scripts\setup_telegram.ps1
   ```

   The script installs the **Histold Telegram** task, which keeps the bot running, and prints `/pair 123456`.
3. Send that `/pair` message to your new bot within 10 minutes. The bot replies "Paired".
4. In an **Administrator** PowerShell, install the admin runner. It ends with a self-test that should report `elevated: True`.

   ```powershell
   powershell -ExecutionPolicy Bypass -File D:\YT-Automation\scripts\install_admin_runner.ps1
   ```

## Day to day

- **Logs:**
  - Telegram bot: `D:\histold\control\telegram-bot.log`
  - Admin runner: `D:\histold\admin-runner\runner.log`
- **Moving control to another Telegram account:** re-run `setup_telegram.ps1 -PairOnly`.
- **Stopping the Telegram bot:** `Disable-ScheduledTask -TaskName 'Histold Telegram'`.
- **Removing the admin runner:** in an Administrator PowerShell, run `Unregister-ScheduledTask -TaskName 'Histold Admin Runner' -Confirm:$false`.
