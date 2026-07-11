from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import Config
from src.script_generator import generate_script, regenerate_title


SCRIPT_JSON = {
    "topic": "test topic",
    "title": "A Test History Hook",
    "description": "A short description.\n\n#history #shorts #facts",
    "tags": ["history", "facts"],
    "segments": [
        {"narration": "This is the hook.", "keywords": ["old library"]},
        {"narration": "This is the explanation.", "keywords": ["historic document"]},
    ],
}


class ScriptProviderRoutingTests(unittest.TestCase):
    def _generate(self, cfg: Config):
        return generate_script(cfg, "short", topic_override="test topic")

    def test_legacy_provider_calls_only_configured_provider(self) -> None:
        cfg = Config({"script": {"provider": "openai"}})

        with patch("src.script_generator._call_openai", return_value=json.dumps(SCRIPT_JSON)) as openai, patch(
            "src.script_generator._call_anthropic"
        ) as anthropic:
            script = self._generate(cfg)

        openai.assert_called_once()
        anthropic.assert_not_called()
        self.assertEqual("openai", script.provider)
        self.assertFalse(script.fallback_used)

    def test_fallback_uses_openai_after_anthropic_error(self) -> None:
        cfg = Config(
            {
                "script": {
                    "routing": "fallback",
                    "primary_provider": "anthropic",
                    "fallback_provider": "openai",
                }
            }
        )

        with patch("src.script_generator._call_anthropic", side_effect=RuntimeError("Claude down")), patch(
            "src.script_generator._call_openai", return_value=json.dumps(SCRIPT_JSON)
        ) as openai:
            script = self._generate(cfg)

        openai.assert_called_once()
        self.assertEqual("openai", script.provider)
        self.assertTrue(script.fallback_used)

    def test_invalid_primary_json_falls_back(self) -> None:
        cfg = Config(
            {
                "script": {
                    "routing": "fallback",
                    "primary_provider": "anthropic",
                    "fallback_provider": "openai",
                }
            }
        )

        with patch("src.script_generator._call_anthropic", return_value="not json"), patch(
            "src.script_generator._call_openai", return_value=json.dumps(SCRIPT_JSON)
        ):
            script = self._generate(cfg)

        self.assertEqual("openai", script.provider)
        self.assertTrue(script.fallback_used)

    def test_malformed_primary_fields_fall_back(self) -> None:
        cfg = Config(
            {
                "script": {
                    "routing": "fallback",
                    "primary_provider": "anthropic",
                    "fallback_provider": "openai",
                }
            }
        )
        malformed = {**SCRIPT_JSON, "title": True, "tags": None, "topic": None}

        with patch("src.script_generator._call_anthropic", return_value=json.dumps(malformed)), patch(
            "src.script_generator._call_openai", return_value=json.dumps(SCRIPT_JSON)
        ):
            script = self._generate(cfg)

        self.assertEqual("openai", script.provider)
        self.assertTrue(script.fallback_used)

    def test_provider_remains_primary_when_routing_is_enabled(self) -> None:
        cfg = Config(
            {
                "script": {
                    "provider": "openai",
                    "routing": "fallback",
                    "primary_provider": "anthropic",
                    "fallback_provider": "anthropic",
                }
            }
        )

        with patch("src.script_generator._call_openai", return_value=json.dumps(SCRIPT_JSON)) as openai, patch(
            "src.script_generator._call_anthropic"
        ) as anthropic:
            script = self._generate(cfg)

        openai.assert_called_once()
        anthropic.assert_not_called()
        self.assertEqual("openai", script.provider)

    def test_random_mode_calls_selected_provider(self) -> None:
        cfg = Config(
            {
                "script": {
                    "routing": "random",
                    "primary_provider": "anthropic",
                    "fallback_provider": "openai",
                }
            }
        )

        with patch("src.script_generator.random.choice", return_value="openai"), patch(
            "src.script_generator._call_openai", return_value=json.dumps(SCRIPT_JSON)
        ) as openai, patch("src.script_generator._call_anthropic") as anthropic:
            script = self._generate(cfg)

        openai.assert_called_once()
        anthropic.assert_not_called()
        self.assertEqual("openai", script.provider)
        self.assertFalse(script.fallback_used)

    def test_round_robin_alternates_providers(self) -> None:
        cfg = Config(
            {
                "script": {
                    "routing": "round_robin",
                    "primary_provider": "anthropic",
                    "fallback_provider": "openai",
                }
            }
        )

        with tempfile.TemporaryDirectory() as tmp, patch("src.script_generator.base_dir", return_value=Path(tmp)), patch(
            "src.script_generator._call_anthropic", return_value=json.dumps(SCRIPT_JSON)
        ) as anthropic, patch("src.script_generator._call_openai", return_value=json.dumps(SCRIPT_JSON)) as openai:
            first = self._generate(cfg)
            second = self._generate(cfg)

        self.assertEqual("anthropic", first.provider)
        self.assertEqual("openai", second.provider)
        anthropic.assert_called_once()
        openai.assert_called_once()

    def test_provider_failure_reports_all_errors(self) -> None:
        cfg = Config(
            {
                "script": {
                    "routing": "fallback",
                    "primary_provider": "anthropic",
                    "fallback_provider": "openai",
                }
            }
        )

        with patch("src.script_generator._call_anthropic", side_effect=RuntimeError("Claude down")), patch(
            "src.script_generator._call_openai", side_effect=RuntimeError("OpenAI down")
        ):
            with self.assertRaisesRegex(RuntimeError, "Claude down.*OpenAI down"):
                self._generate(cfg)

    def test_title_regeneration_falls_back_after_invalid_primary_json(self) -> None:
        cfg = Config(
            {
                "script": {
                    "routing": "fallback",
                    "primary_provider": "anthropic",
                    "fallback_provider": "openai",
                }
            }
        )

        with patch("src.script_generator._call_anthropic", return_value="not json"), patch(
            "src.script_generator._call_openai", return_value='{"title": "A Different Hook"}'
        ):
            title = regenerate_title(cfg, "Old Hook", "A factual context")

        self.assertEqual("A Different Hook", title)


if __name__ == "__main__":
    unittest.main()
