from __future__ import annotations

from datetime import date
from pathlib import Path
import unittest
from unittest.mock import patch

from src.config import Config, base_dir, load_config
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


class AstrotoldConfigTests(unittest.TestCase):
    def test_astrotold_configuration_is_isolated_and_safe(self) -> None:
        config_path = Path("channels/astrotold/config.yaml")

        cfg = load_config(config_path)

        self.assertEqual(cfg.get("channel.name"), "Astrotold")
        self.assertEqual(cfg.get("channel.niche"), "astrology and numerology")
        self.assertEqual(cfg.get("tts.edge_voice"), "hi-IN-MadhurNeural")
        self.assertEqual(cfg.get("youtube.privacy_status"), "private")
        self.assertEqual(cfg.get("youtube.expected_channel_id"), "")
        self.assertEqual(cfg.get("output.dir"), "output")
        self.assertEqual(
            base_dir() / cfg.get("output.dir"),
            config_path.parent.resolve() / "output",
        )


if __name__ == "__main__":
    unittest.main()
