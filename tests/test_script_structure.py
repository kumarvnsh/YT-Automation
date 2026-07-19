"""The four-beat looping structure enforced on every short.

Derived from the only videos on this channel that exceeded 100% average view
percentage: hook with an unresolved gap, setup, an explicit pivot a third of
the way in, a stack of standalone facts, and a callback that reopens the hook.
"""
from __future__ import annotations

import unittest

from src.script_generator import validate_structure

HOOK = (
    "What if the alphabet you are reading right now was invented by a "
    "civilization most people have completely forgotten?"
)
SETUP = (
    "The Phoenicians lived along the coast of modern Lebanon around 1500 BCE "
    "and traded across the ancient world."
)
PIVOT = (
    "But here is the twist. They recorded their history on perishable papyrus "
    "instead of stone, so almost nothing survived."
)
FACTS = [
    "They invented a twenty two letter writing system that inspired Greek, "
    "Latin, and Arabic.",
    "They may have sailed around Africa two thousand years before Vasco da "
    "Gama did.",
    "Their rivals in Rome wrote the surviving histories and painted them as "
    "mere traders.",
]
CALLBACK = (
    "Every time you write a single letter you carry forward a gift from a "
    "civilization history tried to forget."
)


def valid_script() -> tuple[list[str], list[str]]:
    """A structurally valid short: hook, setup, pivot, 3 facts, callback."""
    beats = ["hook", "setup", "pivot"] + ["fact"] * len(FACTS) + ["callback"]
    narrations = [HOOK, SETUP, PIVOT, *FACTS, CALLBACK]
    return beats, narrations


class ValidScriptTests(unittest.TestCase):
    def test_the_reference_structure_passes(self) -> None:
        self.assertIsNone(validate_structure(*valid_script()))


class BeatTagTests(unittest.TestCase):
    def test_untagged_segments_are_rejected(self) -> None:
        beats, narrations = valid_script()
        beats[2] = ""
        self.assertIn("missing beat tags", validate_structure(beats, narrations))

    def test_unknown_beat_tag_is_rejected(self) -> None:
        beats, narrations = valid_script()
        beats[2] = "outro"
        self.assertIn("unknown beat tags: outro", validate_structure(beats, narrations))

    def test_empty_script_is_rejected(self) -> None:
        self.assertIsNotNone(validate_structure([], []))


class BeatCountTests(unittest.TestCase):
    def test_missing_pivot_is_rejected(self) -> None:
        """The regression that mattered: no mid-script turn, viewers drop."""
        beats, narrations = valid_script()
        beats[2] = "fact"
        self.assertIn("one 'pivot' segment", validate_structure(beats, narrations))

    def test_two_pivots_are_rejected(self) -> None:
        beats, narrations = valid_script()
        beats[3] = "pivot"
        self.assertIn("one 'pivot' segment", validate_structure(beats, narrations))

    def test_thin_fact_stack_is_rejected(self) -> None:
        beats = ["hook", "setup", "pivot", "fact", "fact", "callback"]
        narrations = [HOOK, SETUP, PIVOT, FACTS[0], FACTS[1], CALLBACK]
        self.assertIn("need 3-5", validate_structure(beats, narrations))

    def test_oversized_fact_stack_is_rejected(self) -> None:
        beats = ["hook", "setup", "pivot"] + ["fact"] * 6 + ["callback"]
        narrations = [HOOK, SETUP, PIVOT, *(FACTS * 2), CALLBACK]
        self.assertIn("need 3-5", validate_structure(beats, narrations))


class BeatOrderTests(unittest.TestCase):
    def test_hook_must_come_first(self) -> None:
        beats, narrations = valid_script()
        beats[0], beats[1] = "setup", "hook"
        narrations[0], narrations[1] = SETUP, HOOK
        self.assertIn("must be first", validate_structure(beats, narrations))

    def test_callback_must_come_last(self) -> None:
        beats, narrations = valid_script()
        beats[-1], beats[-2] = "fact", "callback"
        narrations[-1], narrations[-2] = FACTS[0], CALLBACK
        self.assertIn("must be last", validate_structure(beats, narrations))

    def test_pivot_too_late_is_rejected(self) -> None:
        """A pivot after the fact stack cannot hold the middle."""
        beats = ["hook", "setup"] + ["fact"] * 5 + ["pivot", "callback"]
        narrations = [HOOK, SETUP, *(FACTS + FACTS[:2]), PIVOT, CALLBACK]
        self.assertIn("one third in", validate_structure(beats, narrations))


class HookTests(unittest.TestCase):
    def test_hook_below_word_floor_is_rejected(self) -> None:
        """Nine words cannot hold a gap — this was the shipped anti-pattern."""
        beats, narrations = valid_script()
        narrations[0] = "She cured leprosy. Then a man stole her credit."
        error = validate_structure(beats, narrations)
        self.assertIn("hook is 9 words", error)

    def test_hook_above_word_ceiling_is_rejected(self) -> None:
        beats, narrations = valid_script()
        narrations[0] = " ".join(["word"] * 30)
        self.assertIn("hook is 30 words", validate_structure(beats, narrations))


class CallbackLoopTests(unittest.TestCase):
    def test_generic_closer_is_rejected(self) -> None:
        """The platitude that shipped on the 40%-retention video."""
        beats, narrations = valid_script()
        narrations[-1] = (
            "One election, one seat, a permanently changed map of who gets to "
            "govern."
        )
        self.assertIn(
            "shares no distinctive word", validate_structure(beats, narrations)
        )

    def test_closer_reusing_a_hook_noun_passes(self) -> None:
        beats, narrations = valid_script()
        narrations[-1] = "That forgotten civilization is still in your hands today."
        self.assertIsNone(validate_structure(beats, narrations))

    def test_shared_stopword_does_not_count_as_a_loop(self) -> None:
        beats, narrations = valid_script()
        narrations[-1] = "There were three things about those years, every time."
        self.assertIn(
            "shares no distinctive word", validate_structure(beats, narrations)
        )


if __name__ == "__main__":
    unittest.main()
