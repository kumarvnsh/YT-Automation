from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import Config
from src.script_generator import generate_script, regenerate_title
from tests.test_script_structure import valid_script


SCRIPT_JSON = {
    "topic": "test topic",
    "title": "A Test History Hook",
    "description": "A short description.\n\n#history #shorts #facts",
    "tags": ["history", "facts"],
    "segments": [
        {"beat": beat, "narration": narration, "keywords": ["old library"]}
        for beat, narration in zip(*valid_script())
    ],
}


class ScriptProviderRoutingTests(unittest.TestCase):
    def _generate(self, cfg: Config):
        # The fixture is the four-beat reference script at 117 words; a 45s
        # target puts it inside the enforced word budget and satisfies the
        # structure check, so routing behavior is what's under test here.
        cfg.raw.setdefault("script", {}).setdefault("shorts_target_seconds", 45)
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



class AnthropicCallTests(unittest.TestCase):
    def _call(self, cfg):
        from types import SimpleNamespace
        from src.script_generator import _call_anthropic
        msg = SimpleNamespace(content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text="{}")],
                              stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        with patch("anthropic.Anthropic") as client:
            client.return_value.messages.create.return_value = msg
            client.return_value.beta.messages.create.return_value = msg
            self.assertEqual(_call_anthropic(cfg, "p"), "{}")
        return client.return_value

    def test_effort_set_uses_thinking_with_that_effort(self):
        c = self._call(Config({"script": {"anthropic_model": "claude-sonnet-5-5", "anthropic_effort": "low"}}))
        kw = c.beta.messages.create.call_args.kwargs
        self.assertEqual(kw["output_config"], {"effort": "low"})
        self.assertEqual(kw["thinking"], {"type": "adaptive"})
        c.messages.create.assert_not_called()

    def test_no_effort_keeps_plain_call(self):
        c = self._call(Config({"script": {"anthropic_model": "claude-sonnet-4-6"}}))
        self.assertNotIn("thinking", c.messages.create.call_args.kwargs)
        c.beta.messages.create.assert_not_called()

    def test_plain_call_has_room_for_long_form_scripts(self):
        c = self._call(Config({"script": {"anthropic_model": "claude-haiku-5-5"}}))
        self.assertEqual(c.messages.create.call_args.kwargs["max_tokens"], 12000)

    def test_cut_off_reply_says_so(self):
        from types import SimpleNamespace
        from src.script_generator import _call_anthropic
        msg = SimpleNamespace(content=[SimpleNamespace(type="text", text="Let me plan the chapters")],
                              stop_reason="max_tokens", usage=SimpleNamespace(input_tokens=1, output_tokens=12000))
        with patch("anthropic.Anthropic") as client:
            client.return_value.messages.create.return_value = msg
            with self.assertRaisesRegex(RuntimeError, "12000-token limit"):
                _call_anthropic(Config({"script": {"anthropic_model": "claude-haiku-5-5"}}), "p")


class RecoveryTests(unittest.TestCase):
    """The 22 Nov long-form failure: two unreadable replies, then a dead OpenAI fallback."""

    def test_retry_asks_for_json_only_and_falls_back_to_a_stronger_claude_model(self):
        from src.script_generator import _call_with_routing, _parse_script_response
        cfg = Config({"script": {"routing": "fallback", "provider": "anthropic", "fallback_provider": "anthropic_fallback",
                                 "anthropic_model": "claude-haiku-5-5", "anthropic_fallback_model": "claude-sonnet-5-5"}})
        calls = []

        def reply(cfg, prompt, model=None):
            calls.append((model, prompt))
            return "# Planning the chapters" if model is None else json.dumps(SCRIPT_JSON)

        with patch("src.script_generator._call_anthropic", side_effect=reply):
            raw, provider, fallback = _call_with_routing(cfg, "PROMPT", _parse_script_response)
        self.assertEqual((provider, fallback), ("anthropic_fallback", True))
        self.assertEqual([model for model, _ in calls], [None, None, "claude-sonnet-5-5"])
        self.assertNotIn("could not be read", calls[0][1])
        self.assertIn("could not be read as the required JSON object", calls[1][1])
        self.assertEqual(calls[2][1], "PROMPT")  # the fallback model gets the clean prompt first

    def test_live_config_no_longer_falls_back_to_openai(self):
        from src.config import load_config
        cfg = load_config()
        self.assertEqual(cfg.get("script.fallback_provider"), "anthropic_fallback")
        self.assertGreaterEqual(int(cfg.get("script.max_output_tokens")), 12000)


if __name__ == "__main__":
    unittest.main()
