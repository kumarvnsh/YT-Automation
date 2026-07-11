#!/usr/bin/env python3
"""Show which YouTube channel the saved credentials will upload to.

Run this AFTER scripts/setup_oauth.py to confirm you authorized the right
channel (especially important if you use Brand Accounts).

    python scripts/whoami_youtube.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.youtube_uploader import get_credentials  # noqa: E402


def main() -> int:
    # Optional config path; defaults to the root Histold config.
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else None)
    from googleapiclient.discovery import build

    creds = get_credentials(interactive=False)
    youtube = build("youtube", "v3", credentials=creds)
    resp = youtube.channels().list(part="snippet,statistics", mine=True).execute()

    items = resp.get("items", [])
    if not items:
        print("No channel found for these credentials.")
        return 1

    ch = items[0]
    sn = ch["snippet"]
    st = ch.get("statistics", {})
    print("Uploads will go to this channel:")
    print(f"  Name:        {sn['title']}")
    print(f"  Channel ID:  {ch['id']}")
    print(f"  URL:         https://www.youtube.com/channel/{ch['id']}")
    print(f"  Subscribers: {st.get('subscriberCount', 'hidden')}")
    print(f"  Videos:      {st.get('videoCount', '0')}")

    expected = (cfg.get("youtube.expected_channel_id") or "").strip()
    if expected:
        if ch["id"] == expected:
            print("\n✅ MATCH — this is the locked channel in config.yaml. You're good to go.")
        else:
            print(f"\n❌ MISMATCH — config expects {expected}.")
            print("   Delete secrets/token.json and re-run scripts/setup_oauth.py, "
                  "then pick the correct channel at the consent screen.")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
