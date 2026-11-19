"""Cross-post a rendered short to Facebook Reels + Instagram Reels via the Graph API.

Auth: a single non-expiring **System User token** (Meta Business) stored in the
env var META_ACCESS_TOKEN, with pages_manage_posts + instagram_content_publish
and the Histold Page/IG assigned. Page ID and IG user ID come from config.

Hosting note: Instagram's Reels endpoint does not accept a raw file upload — it
fetches the video from a public URL. The rendered mp4 only exists on the CI
runner, so we publish it as a **GitHub Release asset** (public repo => public
URL), hand that URL to both platforms, then delete the asset. Requires
GITHUB_TOKEN + GITHUB_REPOSITORY in the environment (both present in Actions).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .config import Config, env

GRAPH = "https://graph.facebook.com"
RUPLOAD = "https://rupload.facebook.com/video-upload"

# GitHub Release used purely as an ephemeral public host for the mp4.
_MEDIA_TAG = "auto-media"


# --------------------------------------------------------------------------- #
# GitHub Release hosting (ephemeral public URL for the mp4)
# --------------------------------------------------------------------------- #
def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _ensure_media_release(repo: str, token: str) -> int:
    """Return the release id for the rolling media tag, creating it if absent."""
    r = requests.get(f"https://api.github.com/repos/{repo}/releases/tags/{_MEDIA_TAG}",
                     headers=_gh_headers(token), timeout=30)
    if r.status_code == 200:
        return r.json()["id"]
    r = requests.post(
        f"https://api.github.com/repos/{repo}/releases",
        headers=_gh_headers(token),
        json={
            "tag_name": _MEDIA_TAG,
            "name": "Auto media host",
            "body": "Ephemeral video hosting for Meta cross-posting. Assets are "
                    "created and deleted per run; safe to ignore.",
            "prerelease": True,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["id"]


def _host_video(video_path: Path) -> tuple[str, callable]:
    """Upload the mp4 as a release asset. Returns (public_url, cleanup_fn)."""
    repo = env("GITHUB_REPOSITORY")
    token = env("GITHUB_TOKEN")
    if not repo or not token:
        raise RuntimeError(
            "Meta cross-post needs a public video URL, hosted via a GitHub Release, "
            "which requires GITHUB_REPOSITORY and GITHUB_TOKEN in the environment "
            "(present in GitHub Actions). Set meta.enabled=false to skip cross-posting."
        )
    release_id = _ensure_media_release(repo, token)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    asset_name = f"reel_{stamp}.mp4"
    data = video_path.read_bytes()
    up = requests.post(
        f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets?name={asset_name}",
        headers={**_gh_headers(token), "Content-Type": "video/mp4"},
        data=data,
        timeout=300,
    )
    up.raise_for_status()
    asset = up.json()
    asset_id = asset["id"]
    url = asset["browser_download_url"]

    def cleanup() -> None:
        try:
            requests.delete(
                f"https://api.github.com/repos/{repo}/releases/assets/{asset_id}",
                headers=_gh_headers(token), timeout=30,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ! could not delete hosted media asset: {exc}")

    print(f"  hosted mp4 for Meta pull: {url}")
    return url, cleanup


# --------------------------------------------------------------------------- #
# Instagram Reels
# --------------------------------------------------------------------------- #
class InstagramPublishUnconfirmed(RuntimeError):
    """The publish request may have succeeded even if no media ID was returned."""


def _publish_instagram(token: str, ig_user_id: str, video_url: str, caption: str,
                       ver: str, timeout_s: int = 300, media_type: str = "REELS",
                       trial: bool = False) -> str:
    base = f"{GRAPH}/{ver}/{ig_user_id}"
    data = {"media_type": media_type, "video_url": video_url, "access_token": token}
    if media_type == "REELS":  # Stories take no caption
        data["caption"] = caption
    if trial:  # shown to non-followers first; Instagram shares it with followers if it performs
        data["trial_params"] = json.dumps({"graduation_strategy": "SS_PERFORMANCE"})
    r = requests.post(f"{base}/media", data=data, timeout=60)
    _raise_graph(r, "IG media container")
    creation_id = r.json()["id"]

    # Meta downloads + transcodes asynchronously; wait until FINISHED.
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        s = requests.get(f"{GRAPH}/{ver}/{creation_id}",
                         params={"fields": "status_code,status", "access_token": token},
                         timeout=30)
        _raise_graph(s, "IG container status")
        code = s.json().get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"IG container failed: {s.json()}")
        time.sleep(5)
    else:
        raise RuntimeError("IG container did not finish processing in time.")

    try:
        pub = requests.post(f"{base}/media_publish", data={
            "creation_id": creation_id,
            "access_token": token,
        }, timeout=60)
        _raise_graph(pub, "IG media_publish")
        media_id = pub.json()["id"]
    except (requests.RequestException, RuntimeError, ValueError, KeyError) as exc:
        raise InstagramPublishUnconfirmed(f"Instagram publication unconfirmed for container {creation_id}") from exc
    print(f"  ✓ Instagram {'Trial ' if trial else ''}{media_type.title()} published: {media_id}")
    return media_id


# --------------------------------------------------------------------------- #
# Facebook Reels
# --------------------------------------------------------------------------- #
def _page_access_token(user_token: str, page_id: str, ver: str) -> str:
    """Page publishing needs a Page token, not the user/system-user token.

    Derive it from the system-user token (which must have a role on the Page +
    pages_show_list/pages_read_engagement). A Page token from a non-expiring
    system-user token is itself non-expiring.
    """
    r = requests.get(f"{GRAPH}/{ver}/{page_id}",
                     params={"fields": "access_token", "access_token": user_token}, timeout=30)
    _raise_graph(r, "FB page-token fetch")
    tok = r.json().get("access_token")
    if not tok:
        raise RuntimeError(
            "No Page access token returned — the token's System User likely isn't "
            "assigned to the Histold Page with content permissions in Business settings."
        )
    return tok


def _publish_facebook_reel(token: str, page_id: str, video_url: str, description: str,
                           ver: str, timeout_s: int = 300, *, title: str = "") -> str:
    token = _page_access_token(token, page_id, ver)  # switch to the Page token

    # 1. start — reserve a video id + upload url
    start = requests.post(f"{GRAPH}/{ver}/{page_id}/video_reels",
                          data={"upload_phase": "start", "access_token": token}, timeout=60)
    _raise_graph(start, "FB reel start")
    video_id = start.json()["video_id"]

    # 2. hosted transfer — Meta pulls the file from our public URL
    transfer = requests.post(f"{RUPLOAD}/{ver}/{video_id}",
                             headers={"Authorization": f"OAuth {token}", "file_url": video_url},
                             timeout=120)
    _raise_graph(transfer, "FB reel transfer")

    # 3. wait until the upload phase reports complete before finishing
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        s = requests.get(f"{GRAPH}/{ver}/{video_id}",
                         params={"fields": "status", "access_token": token}, timeout=30)
        _raise_graph(s, "FB reel status")
        status = s.json().get("status", {})
        up = (status.get("uploading_phase") or {}).get("status")
        if up == "complete":
            break
        if up == "error":
            raise RuntimeError(f"FB reel upload failed: {status}")
        time.sleep(5)
    else:
        raise RuntimeError("Facebook Reel transfer did not finish in time")

    # 4. finish + publish
    finish = requests.post(f"{GRAPH}/{ver}/{page_id}/video_reels", data={
        "upload_phase": "finish",
        "video_id": video_id,
        "video_state": "PUBLISHED",
        "title": title.strip(),
        "description": description.strip(),
        "access_token": token,
    }, timeout=60)
    _raise_graph(finish, "FB reel finish")
    if finish.json().get("success") is not True:
        raise RuntimeError("Facebook did not confirm Reel publication")
    print(f"  ✓ Facebook Reel published: {video_id}")
    return video_id


def _publish_facebook_story(token: str, page_id: str, video_url: str, ver: str) -> str:
    """Page video Story: same start → hosted transfer → finish flow as Reels."""
    token = _page_access_token(token, page_id, ver)
    start = requests.post(f"{GRAPH}/{ver}/{page_id}/video_stories",
                          data={"upload_phase": "start", "access_token": token}, timeout=60)
    _raise_graph(start, "FB story start")
    video_id = start.json()["video_id"]
    transfer = requests.post(f"{RUPLOAD}/{ver}/{video_id}",
                             headers={"Authorization": f"OAuth {token}", "file_url": video_url},
                             timeout=120)
    _raise_graph(transfer, "FB story transfer")
    finish = requests.post(f"{GRAPH}/{ver}/{page_id}/video_stories", data={
        "upload_phase": "finish", "video_id": video_id, "access_token": token,
    }, timeout=60)
    _raise_graph(finish, "FB story finish")
    story_id = finish.json().get("post_id") or video_id
    print(f"  ✓ Facebook Story published: {story_id}")
    return story_id


def _publish_stories(cfg: Config, token: str, page_id: str, ig_user_id: str, url: str,
                     ver: str, results: dict, on_progress=None) -> None:
    """Share the just-posted Reel's video to Stories too. Best-effort and idempotent."""
    if not cfg.get('meta.stories', False):
        return
    jobs = []
    # A trial reel is meant to reach non-followers first; a Story would show it to followers.
    if results.get('instagram') and not results.get('instagram_story') and not results.get('instagram_trial'):
        jobs.append(('instagram_story', lambda: _publish_instagram(token, ig_user_id, url, '', ver,
                                                                   media_type='STORIES')))
    if results.get('facebook') and not results.get('facebook_story'):
        jobs.append(('facebook_story', lambda: _publish_facebook_story(token, page_id, url, ver)))
    for key, job in jobs:
        try:
            results[key] = job()
            results.pop(f'{key}_error', None)
        except Exception as exc:  # noqa: BLE001 - a Story must never fail the Reel
            print(f"  ! {key} failed: {exc}")
            results[f'{key}_error'] = str(exc)
        if on_progress is not None:
            on_progress(dict(results))


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
# trial_params arrived in Graph API v24.0; older versions may ignore it.
_TRIAL_VER = "v24.0"


def _trial_slot(cfg: Config, now: float | None = None) -> bool:
    """True for 1 in `meta.trial_reel_every` posts.

    Posts go out every 12h (9:30 IST am/pm), so the 12h slot number rotates through
    the cycle; retries inside the same slot get the same answer.
    """
    every = int(cfg.get('meta.trial_reel_every', 0) or 0)
    return every > 0 and int((time.time() if now is None else now) // 43200) % every == 0


def _raise_graph(resp: requests.Response, what: str) -> None:
    if not resp.ok:
        raise RuntimeError(f"{what} failed [{resp.status_code}]: {resp.text}")


def _build_caption(cfg: Config, title: str, description: str, *, platform='instagram', question='') -> str:
    if platform == 'facebook' and cfg.get('meta.facebook_caption.enabled', False):
        import re
        lines = [re.sub(r'https?://\S+|#\w+', '', line).strip() for line in description.splitlines()
                 if not re.match(r'^\s*(?:subscribe|watch on youtube)\b', line, re.I)]
        body = ' '.join(filter(None, lines))
        summary = body if len(body) <= 320 else body[:319].rsplit(' ', 1)[0].rstrip('.,;:') + '…'
        parts = [title.strip(), summary]
        editorial = cfg.get('production.editorial', {})
        if editorial:
            parts.append(editorial['series']['name'])
            if editorial.get('follow_cta'):
                parts.append('Follow Histold for surprising world history.')
        tags = cfg.get('meta.facebook_caption.hashtags', []) or []
        parts.append(' '.join(tags[:3]))
        return '\n\n'.join(part for part in parts if part)[:650]
    import re
    found, kept = [], []
    for line in description.splitlines():
        if re.search(r'youtube|youtu\.be|subscribe', line, re.I):
            continue
        found += re.findall(r'#\w+', line)
        kept.append(re.sub(r'\s*#\w+', '', line).strip())  # credit lines survive: CC BY attribution
    body = re.sub(r'\n{3,}', '\n\n', '\n'.join(kept)).strip()
    tags, seen = [], set()
    for tag in (cfg.get("meta.hashtags", []) or []) + found:  # brand tags first, IG caps at 5
        if tag.lower() not in seen and len(tags) < 5:
            seen.add(tag.lower())
            tags.append(tag)
    parts = [title.strip(), body, question.strip(), " ".join(tags)]
    return "\n\n".join(p for p in parts if p)[:2200]  # IG caption hard limit


# --------------------------------------------------------------------------- #
# public entrypoint
# --------------------------------------------------------------------------- #
def publish_reel(cfg: Config, video_path: Path, title: str, description: str, *, existing_results=None, on_progress=None,
                 question: str = '') -> dict:
    """Cross-post the rendered short to FB and/or IG. Returns per-platform ids.

    on_progress receives a snapshot after each platform attempt, before the next.
    Callback failures abort publishing so unpersisted success is never ignored.

    Best-effort: raises on hard config/host errors, but the caller treats a
    per-platform failure as non-fatal so a YouTube upload is never blocked.
    """
    token = env("META_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("META_ACCESS_TOKEN not set — cannot cross-post.")

    ver = cfg.get("meta.api_version", "v21.0")
    page_id = str(cfg.get("meta.page_id", "")).strip()
    ig_user_id = str(cfg.get("meta.ig_user_id", "")).strip()
    results: dict = dict(existing_results or {})
    need_facebook = cfg.get('meta.post_to_facebook', True) and page_id and not results.get('facebook')
    need_instagram = (cfg.get('meta.post_to_instagram', True) and ig_user_id
                      and not results.get('instagram') and not results.get('instagram_publish_unconfirmed'))
    need_stories = cfg.get('meta.stories', False) and (
        (results.get('instagram') and not results.get('instagram_story')) or
        (results.get('facebook') and not results.get('facebook_story')))
    if not need_facebook and not need_instagram and not need_stories:
        return results
    url, cleanup = _host_video(video_path)
    try:
        if need_facebook and cfg.get('meta.primary', False):
            try:
                caption = _build_caption(cfg, title, description, platform='facebook')
                results['facebook'] = _publish_facebook_reel(token, page_id, url, caption, ver, title=title)
                results.pop('facebook_error', None)
            except Exception as exc:
                results['facebook_error'] = str(exc)
                if on_progress is not None:
                    on_progress(dict(results))
                return results
            if on_progress is not None:
                on_progress(dict(results))
        if need_instagram:
            try:
                caption = _build_caption(cfg, title, description, question=question)
                if _trial_slot(cfg):
                    try:
                        results["instagram"] = _publish_instagram(token, ig_user_id, url, caption,
                                                                  _TRIAL_VER, trial=True)
                        results["instagram_trial"] = True
                    except InstagramPublishUnconfirmed:
                        raise
                    except Exception as exc:  # noqa: BLE001 - rejected before publishing
                        print(f"  ! Instagram trial reel failed ({exc}) — posting a normal Reel")
                if not results.get("instagram"):
                    results["instagram"] = _publish_instagram(token, ig_user_id, url, caption, ver)
                results.pop('instagram_error', None)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! Instagram cross-post failed: {exc}")
                results["instagram_error"] = str(exc)
                if isinstance(exc, InstagramPublishUnconfirmed):
                    results["instagram_publish_unconfirmed"] = True
            if on_progress is not None:
                on_progress(dict(results))
        if need_facebook and not cfg.get('meta.primary', False):
            try:
                caption = _build_caption(cfg, title, description, platform='facebook')
                results["facebook"] = _publish_facebook_reel(token, page_id, url, caption, ver, title=title)
                results.pop('facebook_error', None)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! Facebook cross-post failed: {exc}")
                results["facebook_error"] = str(exc)
            if on_progress is not None:
                on_progress(dict(results))
        _publish_stories(cfg, token, page_id, ig_user_id, url, ver, results, on_progress)
    finally:
        cleanup()
    return results
