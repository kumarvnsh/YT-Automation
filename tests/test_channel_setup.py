from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from src.config import Config, base_dir, load_config
from src.script_generator import _build_prompt
from src.youtube_uploader import token_file


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
    def tearDown(self) -> None:
        load_config()

    def test_astrotold_configuration_is_isolated_and_safe(self) -> None:
        config_path = Path("channels/astrotold/config.yaml")

        cfg = load_config(config_path)

        self.assertEqual(cfg.get("channel.name"), "Astrotold")
        self.assertEqual(cfg.get("channel.niche"), "astrology and numerology")
        self.assertEqual(cfg.get("channel.language"), "hinglish")
        self.assertIn("Roman script", cfg.get("channel.language_instruction"))
        self.assertIn("concrete English asset-search keywords", cfg.get("channel.content_rules"))
        self.assertIn("Strictly for entertainment only", cfg.get("channel.safety_rules"))
        self.assertIn("medical, legal, financial, or emergency advice", cfg.get("channel.safety_rules"))
        self.assertEqual(cfg.get("tts.edge_voice"), "hi-IN-MadhurNeural")
        self.assertEqual(cfg.get("tts.edge_rate"), "+4%")
        self.assertEqual(cfg.get("script.shorts_target_seconds"), 28)
        self.assertFalse(cfg.get("youtube.enabled"))
        self.assertEqual(cfg.get("youtube.privacy_status"), "private")
        self.assertEqual(cfg.get("youtube.expected_channel_id"), "")
        self.assertEqual(cfg.get("output.dir"), "output")
        self.assertEqual(
            base_dir() / cfg.get("output.dir"),
            config_path.parent.resolve() / "output",
        )
        self.assertEqual(token_file(), config_path.parent.resolve() / "secrets/token.json")
        self.assertFalse(cfg.get("output.delete_after_upload"))
        self.assertEqual(cfg.get("output.keep_days"), 7)
        self.assertFalse(cfg.get("meta.enabled"))

        env_example = config_path.parent / ".env.example"
        self.assertTrue(env_example.is_file())
        env_example_text = env_example.read_text(encoding="utf-8")
        for variable in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "PEXELS_API_KEY"):
            self.assertIn(f"{variable}=", env_example_text)


class ChannelRunnerTests(unittest.TestCase):
    def run_runner(self, *args: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["BASH_FUNC_python%%"] = (
            '() { echo "PIPELINE_INVOKED" >&2; exit 99; }'
        )
        return subprocess.run(
            ["scripts/run_channel.sh", *args],
            capture_output=True,
            check=False,
            cwd=Path.cwd(),
            env=environment,
            text=True,
        )

    def test_channel_runner_requires_config_and_publish_slot(self) -> None:
        runner_path = Path("scripts/run_channel.sh")

        self.assertTrue(runner_path.is_file())
        runner = runner_path.read_text(encoding="utf-8")

        self.assertIn('CONFIG_PATH="${1:-}"', runner)
        self.assertIn('PUBLISH_SLOT="${2:-}"', runner)
        self.assertRegex(
            runner,
            r'case "\$PUBLISH_SLOT" in[\s\S]*morning\|evening\)',
        )
        self.assertIn("export PUBLISH_SLOT", runner)
        self.assertIn(
            'python -m src.main --config "$CONFIG_PATH" --format short',
            runner,
        )

    def test_channel_runner_rejects_an_empty_config_before_the_pipeline(self) -> None:
        result = self.run_runner("", "morning")

        self.assertEqual(result.returncode, 2)
        self.assertIn("ERROR: config path is required.", result.stderr)
        self.assertNotIn("PIPELINE_INVOKED", result.stderr)

    def test_channel_runner_rejects_an_invalid_slot_before_the_pipeline(self) -> None:
        result = self.run_runner("channels/astrotold/config.yaml", "midday")

        self.assertEqual(result.returncode, 2)
        self.assertIn("ERROR: publish slot must be 'morning' or 'evening'.", result.stderr)
        self.assertNotIn("PIPELINE_INVOKED", result.stderr)


if __name__ == "__main__":
    unittest.main()
