# WhatsApp Notifications + Daily Automation

Get a WhatsApp message when a video uploads, when a run fails, and a special alert when AI credits run out and the flow stops. Powered by the self-hosted **OpenWA** gateway.

---

## Part 1 — Run OpenWA (the WhatsApp gateway)

OpenWA runs locally in Docker and talks to WhatsApp via your own number.

**Prerequisite:** Docker Desktop installed and running.

```bash
# 1. Clone and start OpenWA (separate from this project)
git clone https://github.com/rmyndharis/OpenWA.git
cd OpenWA
docker compose -f docker-compose.dev.yml up -d
```

Then open the dashboard at **http://localhost:2785**.

1. **Create an API key** in the dashboard (API Keys section). Copy it.
2. **Create a session** (e.g. name it `histold`). Copy the **session id**.
3. **Start the session** and open its **QR code**.
4. On your phone: WhatsApp → **Settings → Linked Devices → Link a Device** → scan the QR.
5. Wait until the session status shows **connected**.

> ⚠️ This links your personal WhatsApp to OpenWA (same as WhatsApp Web). Use a number you control. Keep the API key private.

### Your `WHATSAPP_CHAT_ID`

This is the number that will *receive* alerts (your own number works great), formatted as:

```
<countrycode><number>@c.us      e.g. India +91 98765 43210  →  919876543210@c.us
```

No `+`, no spaces.

---

## Part 2 — Fill in `.env`

Open `.env` in this project and set:

```
OPENWA_BASE_URL=http://localhost:2785
OPENWA_API_KEY=<the key you copied>
OPENWA_SESSION_ID=<the session id you copied>
WHATSAPP_CHAT_ID=919876543210@c.us
```

Notification behavior is toggled in `config.yaml` under `notifications:` (all on by default except start ping).

### Test it

```bash
python scripts/test_whatsapp.py
```

You should receive a WhatsApp message within a few seconds. If not, the script prints exactly what to check.

---

## Part 3 — What you'll be notified about

| Event | Message |
|---|---|
| ✅ Upload succeeded | title + YouTube link |
| ✅ Rendered (when run with `--no-upload`) | title, marked "not uploaded" |
| ❌ A video failed | the format + error summary |
| ⛔ **Credits exhausted / flow stopped** | clear "top up at console.anthropic.com" alert |
| 💥 Whole run crashed | fatal error summary |

The notifier is **fail-safe**: if OpenWA is down or unconfigured, the video pipeline still runs — it just logs that the alert couldn't be sent.

---

## Part 4 — Make it run every day automatically

The daily entry point is `scripts/run_daily.sh` (defaults to one Short/day; edit it for `--format both`).

### Option A — launchd (recommended on macOS)

```bash
cp scripts/com.histold.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.histold.daily.plist

# test immediately:
launchctl start com.histold.daily
```

Runs at **09:00 daily**. Change the `Hour`/`Minute` in the plist, then unload + load again. Full commands are in comments inside the plist.

### Option B — cron

```bash
crontab -e
# add:
0 9 * * * /Users/vnshkumar/Documents/YT-Automation/scripts/run_daily.sh >> /Users/vnshkumar/Documents/YT-Automation/logs/cron.log 2>&1
```

> Both options require your Mac to be **awake** at run time. For truly hands-off (Mac off) you'd move the pipeline to a small cloud server — ask and I'll set that up.

---

## Recommended setup order

1. Render a video locally first: `python -m src.main --format short --no-upload`
2. Do the YouTube OAuth: `python scripts/setup_oauth.py`
3. Set up OpenWA + `python scripts/test_whatsapp.py`
4. One full manual run: `python -m src.main --format short`
5. Install the launchd agent. Done — it's now daily and you'll get WhatsApp alerts.
