# Astrotold channel setup

Astrotold is an isolated Hinglish astrology-and-numerology channel. Its OAuth
credentials, token, and generated files belong under `channels/astrotold/`, not
the repository root or any Histold paths.

## First-time owner setup

Run these commands from the repository root while signed in to the intended
Astrotold YouTube account:

```bash
python scripts/setup_oauth.py channels/astrotold/config.yaml
python scripts/whoami_youtube.py channels/astrotold/config.yaml
python -m src.main --config channels/astrotold/config.yaml --format short --dry-run
python -m src.main --config channels/astrotold/config.yaml --format short --no-upload
```

`setup_oauth.py` stores the client credentials and authorization token in
`channels/astrotold/secrets/`. They must remain local and must not be copied
from Histold or committed. `whoami_youtube.py` verifies the connected channel;
copy the returned channel ID into `youtube.expected_channel_id` in
`channels/astrotold/config.yaml` before any real uploads.

The channel is deliberately configured for private uploads. Review two private
uploads in YouTube Studio for accuracy, presentation, and policy compliance
before changing `youtube.privacy_status` to `public`.

The dry run and `--no-upload` run above create material only in
`channels/astrotold/output/`. They do not upload a video.
