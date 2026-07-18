from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from src import topics


class TopicReservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        patcher = patch("src.topics.base_dir", return_value=self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reservation_persists_slot_and_fingerprint(self) -> None:
        reservation = topics.reserve_topic(
            "The Forgotten Library of Alexandria", "short", "morning", "job-a"
        )

        self.assertEqual("job-a", reservation["job_id"])
        self.assertEqual("morning", reservation["slot"])
        self.assertTrue(reservation["fingerprint"])
        self.assertEqual(date.today().isoformat(), reservation["date"])
        self.assertEqual("reserved", reservation["status"])
        saved = json.loads((self.tmp / "data" / "topic_reservations.json").read_text())
        self.assertEqual([reservation], saved)

    def test_near_duplicate_topic_is_rejected(self) -> None:
        topics.reserve_topic(
            "The Forgotten Library of Alexandria", "short", "morning", "job-a"
        )

        with self.assertRaisesRegex(ValueError, "duplicate topic rejected"):
            topics.reserve_topic(
                "Forgotten Library Alexandria", "short", "evening", "job-b"
            )

    def test_reservation_is_idempotent_per_job(self) -> None:
        first = topics.reserve_topic("A Strange Roman Law", "short", "morning", "job-c")
        second = topics.reserve_topic("A Strange Roman Law", "short", "morning", "job-c")

        self.assertEqual(first, second)
        # The retry must return the original record before duplicate matching
        # runs, so an identical title never trips the duplicate rejection.
        entries = topics._load_reservations()
        self.assertEqual(1, len(entries))

    def test_distinct_topics_can_reserve_both_slots(self) -> None:
        morning = topics.reserve_topic(
            "The Forgotten Library of Alexandria", "short", "morning", "job-a"
        )
        evening = topics.reserve_topic(
            "How Rome Built Its Roads", "short", "evening", "job-d"
        )

        self.assertEqual("morning", morning["slot"])
        self.assertEqual("evening", evening["slot"])

    def test_duplicate_of_recorded_topic_title_is_rejected(self) -> None:
        topics.record_topic("The Forgotten Library of Alexandria", "short")

        with self.assertRaisesRegex(ValueError, "duplicate topic rejected"):
            topics.reserve_topic(
                "The Forgotten Library of Alexandria", "short", "evening", "job-e"
            )

    def test_fingerprint_normalizes_stopwords_and_order(self) -> None:
        self.assertEqual(
            topics.topic_fingerprint("The Forgotten Library of Alexandria"),
            topics.topic_fingerprint("Forgotten Library Alexandria"),
        )


if __name__ == "__main__":
    unittest.main()
