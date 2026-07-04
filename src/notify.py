"""WhatsApp notifications via a self-hosted OpenWA gateway.

OpenWA (https://github.com/rmyndharis/OpenWA) exposes a REST API. We send text
with:
    POST {base}/api/sessions/{session}/messages/send-text
    headers: X-API-Key: <key>
    body:    {"chatId": "<number>@c.us", "text": "..."}

The notifier is intentionally fail-safe: if it is disabled, unconfigured, or the
gateway is unreachable, it logs and returns False instead of raising — a failed
notification must never take down the video pipeline.
"""
from __future__ import annotations

from datetime import datetime

import requests

from .config import Config, env


# Substrings that indicate the LLM provider is out of credit / quota.
_CREDIT_MARKERS = (
    "credit balance is too low",
    "plans & billing",
    "insufficient_quota",
    "insufficient quota",
    "billing",
    "exceeded your current quota",
)


def classify_error(message: str) -> str:
    """Return 'credits' if the error looks like an exhausted-balance error, else 'failure'."""
    low = (message or "").lower()
    return "credits" if any(m in low for m in _CREDIT_MARKERS) else "failure"


class Notifier:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.enabled = bool(cfg.get("notifications.enabled", False))
        self.base = (env("OPENWA_BASE_URL") or "http://localhost:2785").rstrip("/")
        self.api_key = env("OPENWA_API_KEY")
        self.session = env("OPENWA_SESSION_ID")
        self.chat_id = env("WHATSAPP_CHAT_ID")

    # -- low level -----------------------------------------------------------
    def _configured(self) -> bool:
        return all([self.enabled, self.api_key, self.session, self.chat_id])

    def send(self, text: str) -> bool:
        """Send a raw WhatsApp message. Never raises."""
        if not self._configured():
            missing = [
                n for n, v in (
                    ("notifications.enabled", self.enabled),
                    ("OPENWA_API_KEY", self.api_key),
                    ("OPENWA_SESSION_ID", self.session),
                    ("WHATSAPP_CHAT_ID", self.chat_id),
                ) if not v
            ]
            print(f"  (whatsapp notify skipped — not configured: {', '.join(missing)})")
            return False
        url = f"{self.base}/api/sessions/{self.session}/messages/send-text"
        try:
            r = requests.post(
                url,
                headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
                json={"chatId": self.chat_id, "text": text},
                timeout=20,
            )
            r.raise_for_status()
            print("  (whatsapp notification sent)")
            return True
        except Exception as exc:  # noqa: BLE001 - notifications must never crash the run
            print(f"  ! whatsapp notify failed: {exc}")
            return False

    # -- event helpers -------------------------------------------------------
    @staticmethod
    def _ts() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M")

    def event_enabled(self, key: str, default: bool = True) -> bool:
        return bool(self.cfg.get(f"notifications.{key}", default))

    def notify_start(self, formats: list[str]) -> None:
        if self.event_enabled("notify_on_start", False):
            self.send(f"🎬 Histold: daily run started ({', '.join(formats)}) — {self._ts()}")

    def notify_success(self, fmt: str, title: str, url: str | None) -> None:
        if not self.event_enabled("notify_on_success"):
            return
        if url:
            self.send(f"✅ Histold: {fmt} uploaded\n“{title}”\n{url}\n{self._ts()}")
        else:
            self.send(f"✅ Histold: {fmt} rendered (not uploaded)\n“{title}”\n{self._ts()}")

    def notify_failure(self, fmt: str, error: str) -> None:
        kind = classify_error(error)
        short = (error or "").strip().replace("\n", " ")[:300]
        if kind == "credits":
            if self.event_enabled("notify_on_credits"):
                self.send(
                    "⛔ Histold: FLOW STOPPED — AI credits exhausted.\n"
                    "Top up at console.anthropic.com → Plans & Billing.\n"
                    f"({fmt}) {self._ts()}"
                )
        else:
            if self.event_enabled("notify_on_failure"):
                self.send(f"❌ Histold: {fmt} FAILED\n{short}\n{self._ts()}")

    def notify_fatal(self, error: str) -> None:
        """Catastrophic error before/around the per-video loop."""
        kind = classify_error(error)
        short = (error or "").strip().replace("\n", " ")[:300]
        if kind == "credits" and self.event_enabled("notify_on_credits"):
            self.send(
                "⛔ Histold: FLOW STOPPED — AI credits exhausted.\n"
                "Top up at console.anthropic.com → Plans & Billing.\n"
                f"{self._ts()}"
            )
        elif self.event_enabled("notify_on_failure"):
            self.send(f"💥 Histold: daily run CRASHED\n{short}\n{self._ts()}")
