from __future__ import annotations

import unittest

from scripts import export_analytics


class _Execute:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _Channels:
    def list(self, **_kwargs):
        return _Execute(
            {
                "items": [
                    {
                        "id": "channel-1",
                        "contentDetails": {
                            "relatedPlaylists": {"uploads": "uploads-playlist"}
                        },
                    }
                ]
            }
        )


class _Playlists:
    def __init__(self):
        self.items = [
            {"id": "uploads-playlist", "snippet": {"title": "Uploads"}, "contentDetails": {"itemCount": 2}},
            {"id": "playlist-lost", "snippet": {"title": "Lost Cities"}, "contentDetails": {"itemCount": 1}},
            {"id": "playlist-ancient", "snippet": {"title": "Ancient Mysteries"}, "contentDetails": {"itemCount": 0}},
        ]
        self.inserts = []

    def list(self, **_kwargs):
        return _Execute({"items": self.items})

    def insert(self, **kwargs):
        self.inserts.append(kwargs["body"])
        title = kwargs["body"]["snippet"]["title"]
        playlist = {
            "id": "playlist-created",
            "snippet": {"title": title},
            "contentDetails": {"itemCount": 0},
        }
        self.items.append(playlist)
        return _Execute(playlist)


class _PlaylistItems:
    def __init__(self):
        self.members = {
            "playlist-lost": ["video-sorted"],
            "playlist-ancient": [],
            "uploads-playlist": ["video-sorted", "video-unsorted"],
        }
        self.inserts = []

    def list(self, **kwargs):
        playlist_id = kwargs["playlistId"]
        return _Execute(
            {
                "items": [
                    {"contentDetails": {"videoId": video_id}}
                    for video_id in self.members.get(playlist_id, [])
                ]
            }
        )

    def insert(self, **kwargs):
        self.inserts.append(kwargs["body"])
        return _Execute({"id": "playlist-item-1"})


class _YouTube:
    def __init__(self):
        self.playlist_items = _PlaylistItems()
        self.playlists_resource = _Playlists()

    def channels(self):
        return _Channels()

    def playlists(self):
        return self.playlists_resource

    def playlistItems(self):
        return self.playlist_items


class AnalyticsPlaylistExportTests(unittest.TestCase):
    def test_marks_recent_videos_without_organization_playlist_as_unsorted(self) -> None:
        youtube = _YouTube()
        playlists = export_analytics._owned_playlists(youtube, "uploads-playlist")
        memberships = export_analytics._playlist_memberships(youtube, playlists)
        videos = [
            {"id": "video-sorted", "title": "Sorted"},
            {"id": "video-unsorted", "title": "Unsorted"},
        ]

        export_analytics._annotate_playlist_memberships(videos, playlists, memberships)

        self.assertFalse(videos[0]["is_unsorted"])
        self.assertEqual(["playlist-lost"], videos[0]["playlist_ids"])
        self.assertEqual(["Lost Cities"], videos[0]["playlists"])
        self.assertTrue(videos[1]["is_unsorted"])
        self.assertEqual([], videos[1]["playlist_ids"])


class PlaylistSortCommandTests(unittest.TestCase):
    def test_add_video_to_playlist_rejects_uploads_playlist(self) -> None:
        from scripts import playlist_sort

        youtube = _YouTube()

        with self.assertRaisesRegex(ValueError, "uploads playlist"):
            playlist_sort.add_video_to_playlist(
                youtube, "video-unsorted", "uploads-playlist"
            )

    def test_add_video_to_playlist_skips_existing_membership(self) -> None:
        from scripts import playlist_sort

        youtube = _YouTube()

        result = playlist_sort.add_video_to_playlist(
            youtube, "video-sorted", "playlist-lost"
        )

        self.assertEqual("already-present", result["status"])
        self.assertEqual([], youtube.playlist_items.inserts)

    def test_add_video_to_playlist_inserts_when_missing(self) -> None:
        from scripts import playlist_sort

        youtube = _YouTube()

        result = playlist_sort.add_video_to_playlist(
            youtube, "video-unsorted", "playlist-ancient"
        )

        self.assertEqual("inserted", result["status"])
        self.assertEqual(
            {
                "playlistId": "playlist-ancient",
                "resourceId": {"kind": "youtube#video", "videoId": "video-unsorted"},
            },
            youtube.playlist_items.inserts[0]["snippet"],
        )

    def test_add_video_to_playlist_uses_existing_playlist_title_when_id_missing(self) -> None:
        from scripts import playlist_sort

        youtube = _YouTube()

        result = playlist_sort.add_video_to_playlist(
            youtube, "video-unsorted", playlist_title="Ancient Mysteries"
        )

        self.assertEqual("inserted", result["status"])
        self.assertEqual("playlist-ancient", result["playlist_id"])
        self.assertEqual([], youtube.playlists_resource.inserts)

    def test_add_video_to_playlist_creates_default_playlist_when_missing(self) -> None:
        from scripts import playlist_sort

        youtube = _YouTube()

        result = playlist_sort.add_video_to_playlist(
            youtube, "video-unsorted", playlist_title="Erased From History"
        )

        self.assertEqual("inserted", result["status"])
        self.assertEqual("playlist-created", result["playlist_id"])
        self.assertEqual(
            {
                "snippet": {
                    "title": "Erased From History",
                    "description": "Automatically created by the Histold dashboard.",
                },
                "status": {"privacyStatus": "public"},
            },
            youtube.playlists_resource.inserts[0],
        )


if __name__ == "__main__":
    unittest.main()
