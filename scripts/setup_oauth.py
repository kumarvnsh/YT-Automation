#!/usr/bin/env python3
"""One-time YouTube OAuth setup.

Prereqs:
  1. Google Cloud Console -> create project -> enable "YouTube Data API v3".
  2. APIs & Services -> Credentials -> Create OAuth client ID -> type "Desktop".
  3. Download the JSON, save it as secrets/client_secret.json
     (or set YOUTUBE_CLIENT_SECRET_FILE in .env to its path).
  4. OAuth consent screen: add your Google account as a Test user.

Then run:  python scripts/setup_oauth.py
A browser window opens; approve access. A token is cached to secrets/token.json.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.youtube_uploader import SCOPES, get_credentials, token_file  # noqa: E402


def main() -> None:
    # Optional: pass a config path; defaults to the root Histold config.
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else None
    load_config(cfg_path)
    print("Launching Google OAuth consent flow in your browser...")
    get_credentials(interactive=True, scopes=SCOPES)
    print(f"Success. Token cached at: {token_file()}")
    print("You can now run the pipeline with YouTube upload enabled.")


if __name__ == "__main__":
    main()
