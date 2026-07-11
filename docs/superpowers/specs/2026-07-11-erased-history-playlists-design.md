# Erased From History Niche and Playlist Dashboard Design

## Goal

Move Histold into one clear 30-day lane so YouTube can classify the channel:
ancient mysteries, vanished civilizations, lost cities, erased records, and
forgotten discoveries. The system should make each upload feel like part of the
same subscribable series, then give the dashboard a way to sort unsorted videos
into YouTube playlists through GitHub Actions.

## Scope

This change covers the primary Histold config, script generation prompt,
YouTube upload post-actions, analytics export, dashboard UI, and a new playlist
workflow. It does not redesign the renderer, scheduler, Meta cross-posting, or
unrelated channel expansion work.

## Channel Positioning

Histold becomes a 30-day "Erased From History" series. Topic generation should
prefer:

- vanished civilizations and lost cities
- ancient mysteries with plausible historical grounding
- forgotten technologies, records, maps, libraries, and discoveries
- people, places, and evidence erased, buried, miscredited, or ignored by later
  history
- titles and hooks that frame the topic as forgotten, vanished, hidden, or
  erased without claiming conspiracy unless the facts support it

The generated narration must end with a spoken CTA:
"Follow for more forgotten history."

The description must preserve the current 2-3 sentence shape, include series
language, and end with relevant hashtags. Tags should reinforce the lane:
`forgotten history`, `ancient mysteries`, `lost civilizations`, `erased from
history`, `history shorts`, and adjacent terms.

## Comments CTA

Each upload should receive one top-level comment with a question that invites
replies, for example:

> Which forgotten civilization should we uncover next?

The comment text should be configurable. The workflow will store the created
comment id in stage state and in the published index when available.

YouTube Data API currently documents top-level comment creation through
`commentThreads.insert`, but the public comment methods do not document a
pin-comment operation. The automation will therefore create the intended pinned
comment text and record it. Actual pinning remains a manual YouTube Studio step
unless a supported API endpoint becomes available.

## Playlist Dashboard

Analytics export should include enough playlist data for the dashboard to know:

- the channel's owned playlists, excluding the uploads playlist from selectable
  organization playlists
- which recent videos belong to at least one owned playlist
- which recent videos are not in any selectable playlist

The dashboard will add an "Unsorted Videos" panel. Each row shows the video
title, current stats, and a playlist dropdown. The user can choose a playlist
and click Add. The dashboard dispatches a new GitHub Actions workflow with:

- `video_id`
- `playlist_id`
- `channel`, defaulting to `histold`

The dashboard does not talk to YouTube directly and does not handle YouTube
OAuth tokens in the browser.

## GitHub Actions Workflow

Add a `playlist.yml` workflow. It reconstructs the same YouTube credentials used
by analytics and republish jobs, loads the selected channel config, verifies the
authorized channel, and calls a small Python entrypoint to insert the video into
the requested playlist.

The entrypoint must:

- list or fetch owned playlists to verify the target playlist belongs to the
  authorized channel
- reject attempts to add to the uploads playlist
- check existing playlist membership first so repeat dashboard clicks are
  idempotent
- insert via `playlistItems.insert` only when needed
- update `data/published_index.json` with the playlist assignment when the video
  is tracked there

## Data Flow

1. `scripts/export_analytics.py` fetches recent uploads and owned playlists.
2. It scans playlist membership for those recent video ids.
3. It writes playlist metadata into `data/analytics.json`.
4. `docs/app.js` renders unsorted YouTube videos from that data.
5. A dashboard click dispatches `playlist.yml`.
6. The workflow adds the video to the selected playlist and commits any durable
   index update.
7. The next analytics export removes that video from the unsorted list.

## Error Handling

Dashboard dispatch failures should show the GitHub API error. Workflow failures
should fail clearly for missing OAuth credentials, wrong channel, playlist not
found, video not found, or insufficient YouTube scope. Duplicate additions should
finish successfully with a "already in playlist" message.

Comment creation must be best-effort after upload. A comment failure should not
fail the video upload because the primary deliverable is the published video.

## Testing

Unit tests should cover:

- script prompt contains the Erased From History lane and required CTA
- upload post-action builds a top-level comment request when enabled
- playlist insertion is idempotent when membership already exists
- playlist insertion rejects the uploads playlist
- analytics export marks videos with and without organization playlists

Dashboard changes can be verified with static sample data and syntax checks,
because the app is vanilla JS without a build step.

## Rollout

The first rollout uses the existing Histold channel only. After 30 days, the
same configuration shape can support broader series lanes without changing the
workflow or dashboard mechanics.
