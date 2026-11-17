# Shotsnip in the local automation dashboard

The existing dashboard now has a top-left Histold / Shotsnip selector (also
available on mobile). Shotsnip is scoped to channel ID
`UCmOU4XeMIljEWNBd6NQUmdQ`, @shotsnipclips. Histold retains its existing pages,
actions, data clone and credentials. Shotsnip never executes Histold controls.

## Sources and access

- Runtime source: `D:\YT-Cliping-Automation` (override `SHOTSNIP_ROOT` locally).
- Public identity: `channel.json`; creator list: `watchlist.json`.
- Google Analytics sign-in: run `connect_analytics.py` in the clipping repo.
  It verifies the selected channel before saving `analytics_token.json`.
  This ignored file is separate from the existing uploader's `token.json`.
- `sync_analytics.py` writes `work/dashboard/analytics.json`: latest 50 listed
  public uploads at most, verified owner video statistics, and a 28-day YouTube
  Analytics report ending two days before the current UTC date.
- Refreshes are read-only and run every 30 minutes while this dashboard process
  is alive, or through Refresh analytics. Missing metrics remain unavailable.

The owner API can legitimately return no video rows or zero daily views when
old videos have no activity in the requested period. Lifetime totals are shown
separately. Title-derived game groups are exploratory and not causal claims.

## Targets and local worker

Target channels accepts HTTPS YouTube @handles/channel URLs and Twitch profile
URLs. YouTube IDs are resolved before saving. Invalid domains, watch URLs,
credentials in URLs, and explicit ports are refused. Existing targets are not
duplicated. Pause/Resume updates only Shotsnip's watchlist.

Start local clipping launches `watch_service.py` in the clipping repo. One
stream is captured at a time; up to two enabled YouTube targets are probed
concurrently. The worker respects the GTA V → GTA VI IST schedule, applies the
saved finishing template, and backs off a failed source for five minutes.
Only actual chat spikes trigger clips; it does not fabricate test triggers.
Twitch capture, automatic public uploads and launch-at-login are not enabled.
Stop requests are cooperative; a current render finishes before stopping.

The worker lock prevents duplicate workers. Preview MP4s are served only after
dashboard authentication, with range support and paths confined to `work/`.
Signed capture URLs, token files, cookies and refresh tokens are not exposed.

## Agent boundary

The Shotsnip agent reads only its own metrics. It can refresh reports, diagnose
saved errors and adjust bounded local source priorities (0.5–1.5). It does not
change account access, code, templates, schedule, or publishing.

Source learning requires explicit `uploaded_video_id` + `creator` provenance
in a finished clip's metadata, at least six eligible linked outputs overall,
and three per creator, aged 2–28 days. Its proxy is median lifetime views per
day since upload, not matched-age retention. Weights are recomputed, not
compounded. No linked evidence means no source preference is claimed.

## Future Facebook / Instagram access

Create a dedicated Shotsnip Facebook Page and Instagram professional account,
link them, and grant the Meta app the Page/Instagram publishing and insights
access supported for those assets. Connect them in a separate local OAuth
setup and verify the returned Page/IG IDs before enabling uploads. Do not copy
Histold's tokens or send passwords/tokens in chat. Meta may require app review
or business verification depending on the app's mode and who owns the assets.

## Current preview and deployment limitation

The updated dashboard was started on localhost port 8799 using the existing
dashboard key. The old 8787 process belongs to another task login and this
shell cannot terminate it. Updating the password-backed Strategist task was
also refused by Windows. That task was left unchanged; no password is needed
in chat. Restart/deploy the dashboard under the task's owning account before
replacing the existing 8787 app. Its hourly data clone resets tracked files,
so the UI source should remain in `D:\YT-Automation`, separate from that clone.

## Clips Kitty assessment

Reviewed upstream `ColinGPT9/clips-studio` (now Clips Kitty) on 17 Nov 2026.
It offers transcript/signal-based clip selection, game/webcam layouts,
editable word captions, local Ollama/Whisper processing and a documented
headless loopback API. A separate optional editing engine is a plausible
integration; it has not been installed or benchmarked here. Keep our live
buffer/chat detection, IST policy and channel routing, then compare its
selection/framing quality on the same captured footage. Do not assume its
VOD/chat-replay workflow replaces low-latency live clipping.

References:
- https://github.com/ColinGPT9/clips-studio
- https://github.com/ColinGPT9/clips-studio/blob/main/docs/API.md
- https://github.com/ColinGPT9/clips-studio/blob/main/docs/GAMING.md
- https://github.com/ColinGPT9/clips-studio/blob/main/LICENSE

The project declares AGPL-3.0. Preserve its license/attribution and review
license obligations before copying, modifying, distributing or hosting it.
Its API documentation explicitly requires loopback or an authenticated proxy.
