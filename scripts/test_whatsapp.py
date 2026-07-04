#!/usr/bin/env python3
"""Send a test WhatsApp message through OpenWA to verify your config.

Usage:
    python scripts/test_whatsapp.py
    python scripts/test_whatsapp.py "custom message"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.notify import Notifier  # noqa: E402


def main() -> int:
    cfg = load_config()
    notifier = Notifier(cfg)
    text = sys.argv[1] if len(sys.argv) > 1 else "✅ Histold WhatsApp test — notifications are working!"
    ok = notifier.send(text)
    if ok:
        print("Sent. Check your WhatsApp.")
        return 0
    print(
        "Not sent. Check that:\n"
        "  - OpenWA is running (http://localhost:2785)\n"
        "  - notifications.enabled: true in config.yaml\n"
        "  - OPENWA_API_KEY / OPENWA_SESSION_ID / WHATSAPP_CHAT_ID are set in .env\n"
        "  - the OpenWA session is connected (QR scanned)."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
