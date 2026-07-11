"""Bulk playlist sorting survives unreadable playlists and caches lookups."""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Import via the same package path playlist_sort uses internally, so
# patch.object targets the exact module object under test.
from scripts import export_analytics, playlist_sort  # noqa: E402


class FakeNotFound(Exception):
    """Duck-typed googleapiclient HttpError with a 404 response."""

    def __init__(self):
        super().__init__("playlistNotFound")
        self.resp = types.SimpleNamespace(status=404)


OWNED = [
    {"id": "PLbroken", "title": "Ancient Mysteries", "video_count": 3},
    {"id": "PLgood", "title": "Lost Cities", "video_count": 1},
]

VIDEOS = [
    # → Ancient Mysteries (unreadable playlist)
    {"id": "vid-1", "title": "The Ancient Mystery of the Unknown Secret", "is_unsorted": True},
    # → Lost Cities (works)
    {"id": "vid-2", "title": "The Lost City ruins of Petra in the desert", "is_unsorted": True},
    # → no rule match → default title → playlist must be created
    {"id": "vid-3", "title": "A plain video", "is_unsorted": True},
    # already sorted: skipped entirely
    {"id": "vid-4", "title": "Sorted already", "is_unsorted": False},
]


def _members_side_effect(youtube, playlist_id):
    if playlist_id == "PLbroken":
        raise FakeNotFound()
    if playlist_id == "PLgood":
        return ["vid-existing"]
    raise AssertionError(f"unexpected playlistItems.list for {playlist_id}")


class BulkSortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.youtube = MagicMock()
        self.youtube.playlists().insert().execute.return_value = {
            "id": "PLnew",
            "snippet": {"title": playlist_sort.DEFAULT_PLAYLIST_TITLE},
        }
        self.youtube.playlistItems().insert().execute.return_value = {"id": "pli-1"}
        patches = [
            patch.object(export_analytics, "_uploads_playlist_id", return_value="UUuploads"),
            patch.object(export_analytics, "_owned_playlists", return_value=[dict(p) for p in OWNED]),
            patch.object(export_analytics, "_playlist_video_ids", side_effect=_members_side_effect),
        ]
        for p in patches:
            self.mocks = p.start()
            self.addCleanup(p.stop)

    def test_unreadable_playlist_fails_one_video_not_the_run(self) -> None:
        results = playlist_sort.bulk_sort_videos(self.youtube, VIDEOS)

        by_id = {r["video_id"]: r for r in results}
        self.assertEqual("error", by_id["vid-1"]["status"])
        self.assertIn("playlistNotFound", by_id["vid-1"]["error"])
        self.assertEqual("inserted", by_id["vid-2"]["status"])
        self.assertEqual("PLgood", by_id["vid-2"]["playlist_id"])
        self.assertEqual("inserted", by_id["vid-3"]["status"])
        self.assertEqual("PLnew", by_id["vid-3"]["playlist_id"])
        self.assertNotIn("vid-4", by_id)

    def test_created_playlist_is_never_listed(self) -> None:
        # Listing a freshly created playlist can 404 before it propagates; the
        # membership cache must treat it as known-empty instead of calling out.
        playlist_sort.bulk_sort_videos(self.youtube, VIDEOS)
        listed = [c.args[1] for c in export_analytics._playlist_video_ids.call_args_list]
        self.assertNotIn("PLnew", listed)

    def test_bulk_fetches_playlists_once(self) -> None:
        playlist_sort.bulk_sort_videos(self.youtube, VIDEOS)
        export_analytics._owned_playlists.assert_called_once()
        export_analytics._uploads_playlist_id.assert_called_once()

    def test_already_present_video_is_not_reinserted(self) -> None:
        videos = [{"id": "vid-existing", "title": "Lost City ruins", "is_unsorted": True}]
        results = playlist_sort.bulk_sort_videos(self.youtube, videos)
        self.assertEqual("already-present", results[0]["status"])

    def test_uploads_playlist_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "uploads playlist"):
            playlist_sort.add_video_to_playlist(
                self.youtube, "vid-1", playlist_id="UUuploads"
            )


class ExportMembershipTests(unittest.TestCase):
    def test_memberships_skip_unreadable_playlists(self) -> None:
        youtube = MagicMock()
        with patch.object(
            export_analytics, "_playlist_video_ids", side_effect=_members_side_effect
        ):
            memberships = export_analytics._playlist_memberships(
                youtube, [dict(p) for p in OWNED]
            )
        self.assertEqual({"PLgood": ["vid-existing"]}, memberships)

    def test_non_404_errors_still_raise(self) -> None:
        youtube = MagicMock()
        with patch.object(
            export_analytics, "_playlist_video_ids", side_effect=RuntimeError("quota")
        ):
            with self.assertRaisesRegex(RuntimeError, "quota"):
                export_analytics._playlist_memberships(youtube, [dict(OWNED[1])])


if __name__ == "__main__":
    unittest.main()
