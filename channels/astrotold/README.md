# Astrotold channel setup

Astrotold is an isolated Hinglish astrology-and-numerology channel. Its OAuth
credentials, token, and generated files belong under `channels/astrotold/`, not
the repository root or any Histold paths.

## First-time owner setup

Run these steps from the repository root while signed in to the intended
Astrotold YouTube account.

1. Create the channel-local credentials directory and environment file:

   ```bash
   mkdir -p channels/astrotold/secrets
   cp channels/astrotold/.env.example channels/astrotold/.env
   ```

   Add values for `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `PEXELS_API_KEY`
   to `channels/astrotold/.env`. Optional provider variables are documented in
   the template. When this configuration is loaded, the config loader reads
   only `channels/astrotold/.env`; it does not use the repository-root `.env`.

2. In Google Cloud Console, download the OAuth **Desktop** client JSON and put
   it at `channels/astrotold/secrets/client_secret.json`. Do not commit this
   file or reuse a Histold credential.

3. Authorize and verify the target channel:

```bash
python scripts/setup_oauth.py channels/astrotold/config.yaml
python scripts/whoami_youtube.py channels/astrotold/config.yaml
```

`setup_oauth.py` writes the authorization token to
`channels/astrotold/secrets/token.json`. Copy the verified channel ID printed
by `whoami_youtube.py` into `youtube.expected_channel_id` in
`channels/astrotold/config.yaml`, then set `youtube.enabled` to `true` while
leaving `youtube.privacy_status` set to `private`.

4. Run render-only checks (these do not upload):

```bash
python -m src.main --config channels/astrotold/config.yaml --format short --dry-run
python -m src.main --config channels/astrotold/config.yaml --format short --no-upload
```

The channel begins with uploads disabled as a safety lock. After enabling it
for the verified ID, make two private review uploads with:

```bash
python -m src.main --config channels/astrotold/config.yaml --format short
```

Review both uploads in YouTube Studio for accuracy, presentation, and policy
compliance before changing `youtube.privacy_status` to `public`.

The dry run and `--no-upload` run above create material only in
`channels/astrotold/output/`. They do not upload a video.

## Daily scheduling

After the private-upload review is complete, add these local-time entries with
`crontab -e` to publish one Astrotold Short at 09:00 and one at 18:00 each day:

```cron
0 9 * * * /Users/vnshkumar/Documents/YT-Automation/scripts/run_channel.sh /Users/vnshkumar/Documents/YT-Automation/channels/astrotold/config.yaml morning >> /Users/vnshkumar/Documents/YT-Automation/logs/astrotold.log 2>&1
0 18 * * * /Users/vnshkumar/Documents/YT-Automation/scripts/run_channel.sh /Users/vnshkumar/Documents/YT-Automation/channels/astrotold/config.yaml evening >> /Users/vnshkumar/Documents/YT-Automation/logs/astrotold.log 2>&1
```
