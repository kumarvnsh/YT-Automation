"""Shorts scripts must land in the word budget at generation time,
not fail the quality gate after a full render."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from src.config import Config
from src.script_generator import generate_script
from tests.test_script_structure import CALLBACK, FACTS, HOOK, PIVOT, SETUP


def _script(padding_words: int = 0) -> str:
    """A structurally valid short, optionally padded past the word ceiling."""
    padding = (" " + " ".join(["extra"] * padding_words)) if padding_words else ""
    segments = [
        {"beat": "hook", "narration": HOOK, "keywords": ["ancient alphabet"]},
        {"beat": "setup", "narration": SETUP, "keywords": ["mediterranean coast"]},
        {"beat": "pivot", "narration": PIVOT, "keywords": ["old papyrus"]},
    ]
    segments += [
        {"beat": "fact", "narration": fact + padding, "keywords": ["stone carving"]}
        for fact in FACTS
    ]
    segments.append(
        {"beat": "callback", "narration": CALLBACK, "keywords": ["handwriting"]}
    )
    return json.dumps(
        {
            "topic": "phoenician alphabet",
            "title": "They Invented Writing Then Vanished",
            "description": "A short description.",
            "tags": ["history"],
            "segments": segments,
        }
    )


def _words(raw: str) -> int:
    return len(
        " ".join(seg["narration"] for seg in json.loads(raw)["segments"]).split()
    )


# 45s ≈ 112 words → bounds [84, 151] with the default 0.75/1.35 ratios.
GOOD = _script()
LONG = _script(padding_words=20)
GOOD_WORDS = _words(GOOD)
LONG_WORDS = _words(LONG)


def _cfg() -> Config:
    return Config(
        {
            "script": {
                "provider": "anthropic",
                "shorts_target_seconds": 45,
                "enforce_beats": True,
            }
        }
    )


class ScriptLengthTests(unittest.TestCase):
    def test_fixtures_straddle_the_budget(self) -> None:
        self.assertTrue(84 <= GOOD_WORDS <= 151, GOOD_WORDS)
        self.assertGreater(LONG_WORDS, 151)

    def test_over_budget_draft_is_regenerated(self) -> None:
        with patch(
            "src.script_generator._call_anthropic", side_effect=[LONG, LONG, GOOD]
        ) as call:
            script = generate_script(_cfg(), "short", topic_override="test topic")

        self.assertEqual(3, call.call_count)
        self.assertEqual(GOOD_WORDS, len(script.full_narration.split()))

    def test_retry_prompt_carries_word_budget_feedback(self) -> None:
        with patch(
            "src.script_generator._call_anthropic", side_effect=[LONG, GOOD]
        ) as call:
            generate_script(_cfg(), "short", topic_override="test topic")

        retry_prompt = call.call_args_list[1].args[1]
        self.assertIn(f"previous draft was {LONG_WORDS} words", retry_prompt)
        self.assertIn("MUST be between 84 and 151 words", retry_prompt)

    def test_gives_up_with_clear_error_after_three_long_drafts(self) -> None:
        with patch("src.script_generator._call_anthropic", side_effect=[LONG] * 3):
            with self.assertRaisesRegex(RuntimeError, "out of bounds after 3 attempts"):
                generate_script(_cfg(), "short", topic_override="test topic")

    def test_long_form_is_not_word_budget_enforced(self) -> None:
        with patch("src.script_generator._call_anthropic", return_value=LONG) as call:
            script = generate_script(_cfg(), "long", topic_override="test topic")

        self.assertEqual(1, call.call_count)
        self.assertEqual(LONG_WORDS, len(script.full_narration.split()))


class ScriptStructureRetryTests(unittest.TestCase):
    """Structure failures retry in the same loop as word-count failures, so a
    broken script costs one LLM call instead of a full render."""

    def test_structurally_broken_draft_is_regenerated(self) -> None:
        broken = json.loads(GOOD)
        broken["segments"][2]["beat"] = "fact"  # drop the pivot
        with patch(
            "src.script_generator._call_anthropic",
            side_effect=[json.dumps(broken), GOOD],
        ) as call:
            script = generate_script(_cfg(), "short", topic_override="test topic")

        self.assertEqual(2, call.call_count)
        self.assertEqual("pivot", script.segments[2].beat)

    def test_retry_prompt_names_the_structural_failure(self) -> None:
        broken = json.loads(GOOD)
        broken["segments"][-1]["narration"] = "A memorable takeaway for everyone."
        with patch(
            "src.script_generator._call_anthropic",
            side_effect=[json.dumps(broken), GOOD],
        ) as call:
            generate_script(_cfg(), "short", topic_override="test topic")

        retry_prompt = call.call_args_list[1].args[1]
        self.assertIn("broke the required structure", retry_prompt)
        self.assertIn("shares no distinctive word", retry_prompt)

    def test_gives_up_with_clear_error_after_three_broken_drafts(self) -> None:
        broken = json.loads(GOOD)
        broken["segments"][2]["beat"] = "fact"
        with patch(
            "src.script_generator._call_anthropic",
            side_effect=[json.dumps(broken)] * 3,
        ):
            with self.assertRaisesRegex(RuntimeError, "structure stayed invalid"):
                generate_script(_cfg(), "short", topic_override="test topic")

    def test_beat_tags_survive_onto_the_script(self) -> None:
        with patch("src.script_generator._call_anthropic", return_value=GOOD):
            script = generate_script(_cfg(), "short", topic_override="test topic")

        self.assertEqual(
            ["hook", "setup", "pivot", "fact", "fact", "fact", "callback"],
            [segment.beat for segment in script.segments],
        )


class OptOutChannelTests(unittest.TestCase):
    """Channels that don't set script.enforce_beats are untouched by the
    structure work — e.g. astrotold, whose 28s budget cannot hold the beats."""

    @staticmethod
    def _astrotold_cfg() -> Config:
        return Config(
            {"script": {"provider": "anthropic", "shorts_target_seconds": 28}}
        )

    def test_untagged_script_is_accepted_when_beats_not_enforced(self) -> None:
        plain = json.dumps(
            {
                "topic": "number three",
                "title": "What Your Number Three Means",
                "description": "A short reading.",
                "tags": ["numerology"],
                "segments": [
                    {
                        "narration": " ".join(["word"] * 70),
                        "keywords": ["night sky"],
                    }
                ],
            }
        )
        with patch("src.script_generator._call_anthropic", return_value=plain) as call:
            script = generate_script(
                self._astrotold_cfg(), "short", topic_override="test topic"
            )

        self.assertEqual(1, call.call_count)
        self.assertEqual("", script.segments[0].beat)

    def test_beat_instructions_are_absent_from_the_prompt(self) -> None:
        from src.script_generator import _build_prompt

        prompt = _build_prompt(self._astrotold_cfg(), "short", topic_override="t")
        self.assertNotIn("fact stack", prompt)
        self.assertNotIn("But here's the twist", prompt)

    def test_segment_count_matches_the_short_word_budget(self) -> None:
        """Regression: the beat scaffold asks for 6-8 segments, which blows a
        28s budget. A non-enforcing channel must keep the original 3-5."""
        from src.script_generator import _build_prompt

        prompt = _build_prompt(self._astrotold_cfg(), "short", topic_override="t")
        self.assertIn("3 to 5 short segments", prompt)
        self.assertNotIn("6 to 8 segments", prompt)

    def test_legacy_hook_rule_is_preserved(self) -> None:
        from src.script_generator import _build_prompt

        prompt = _build_prompt(self._astrotold_cfg(), "short", topic_override="t")
        self.assertIn("hook and MUST be 8-15 words", prompt)

    def test_long_form_keeps_the_legacy_hook_rule(self) -> None:
        """Long-form is unaffected on every channel, opted in or not."""
        from src.script_generator import _build_prompt

        for cfg in (self._astrotold_cfg(), _cfg()):
            prompt = _build_prompt(cfg, "long", topic_override="t")
            self.assertIn("hook and MUST be 8-15 words", prompt)
            self.assertNotIn("fact stack", prompt)


if __name__ == "__main__":
    unittest.main()
