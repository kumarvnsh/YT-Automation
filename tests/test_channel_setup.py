from __future__ import annotations

from datetime import date
import unittest
from unittest.mock import patch

from src.config import Config
from src.script_generator import _build_prompt


class ChannelPromptTests(unittest.TestCase):
    def test_default_config_uses_history_did_you_know_niche(self) -> None:
        prompt = _build_prompt(Config({}), "short", topic_override="test topic")

        self.assertIn('in the "history / did-you-know" niche.', prompt)

    def test_channel_configuration_is_included_in_script_prompt(self) -> None:
        cfg = Config(
            {
                "channel": {
                    "niche": "astrology and numerology",
                    "persona": "Astrotold is an empathetic celestial guide.",
                    "language_instruction": "Write natural Hinglish in Roman script.",
                    "content_rules": "Use English stock-footage keywords.",
                    "safety_rules": "Frame every reading as entertainment; no guarantees.",
                }
            }
        )

        with patch("src.script_generator.date", create=True) as mocked_date:
            mocked_date.today.return_value = date(2026, 7, 18)
            prompt = _build_prompt(cfg, "short", topic_override="test topic")

        self.assertIn("Astrotold is an empathetic celestial guide.", prompt)
        self.assertIn('in the "astrology and numerology" niche.', prompt)
        self.assertIn("TODAY'S DATE: 18 July 2026", prompt)
        self.assertIn("Write natural Hinglish in Roman script.", prompt)
        self.assertIn("Use English stock-footage keywords.", prompt)
        self.assertIn("Frame every reading as entertainment; no guarantees.", prompt)


if __name__ == "__main__":
    unittest.main()
