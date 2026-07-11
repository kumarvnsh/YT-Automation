"""Shorts scripts must land in the word budget at generation time,
not fail the quality gate after a full render."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from src.config import Config
from src.script_generator import generate_script


def _script(narration: str) -> str:
    return json.dumps(
        {
            "topic": "test topic",
            "title": "A Test History Hook",
            "description": "A short description.",
            "tags": ["history"],
            "segments": [{"narration": narration, "keywords": ["old book"]}],
        }
    )


# Target: 2s ≈ 5 words → bounds [3, 6] with the default 0.75/1.35 ratios.
GOOD = _script("One two three four five.")
LONG = _script("One two three four five six seven eight nine ten eleven twelve.")


def _cfg() -> Config:
    return Config({"script": {"provider": "anthropic", "shorts_target_seconds": 2}})


class ScriptLengthTests(unittest.TestCase):
    def test_over_budget_draft_is_regenerated(self) -> None:
        with patch(
            "src.script_generator._call_anthropic", side_effect=[LONG, LONG, GOOD]
        ) as call:
            script = generate_script(_cfg(), "short", topic_override="test topic")

        self.assertEqual(3, call.call_count)
        self.assertEqual(5, len(script.full_narration.split()))

    def test_retry_prompt_carries_word_budget_feedback(self) -> None:
        with patch(
            "src.script_generator._call_anthropic", side_effect=[LONG, GOOD]
        ) as call:
            generate_script(_cfg(), "short", topic_override="test topic")

        retry_prompt = call.call_args_list[1].args[1]
        self.assertIn("previous draft was 12 words", retry_prompt)
        self.assertIn("MUST be between 3 and 6 words", retry_prompt)

    def test_gives_up_with_clear_error_after_three_long_drafts(self) -> None:
        with patch("src.script_generator._call_anthropic", side_effect=[LONG] * 3):
            with self.assertRaisesRegex(RuntimeError, "out of bounds after 3 attempts"):
                generate_script(_cfg(), "short", topic_override="test topic")

    def test_long_form_is_not_word_budget_enforced(self) -> None:
        with patch("src.script_generator._call_anthropic", return_value=LONG) as call:
            script = generate_script(_cfg(), "long", topic_override="test topic")

        self.assertEqual(1, call.call_count)
        self.assertEqual(12, len(script.full_narration.split()))


if __name__ == "__main__":
    unittest.main()
