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
def _publish_instagram(token: str, ig_user_id: str, video_url: str, caption: str,
                       ver: str, timeout_s: int = 300) -> str:
    base = f"{GRAPH}/{ver}/{ig_user_id}"
    r = requests.post(f"{base}/media", data={
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "access_token": token,
    }, timeout=60)
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

    pub = requests.post(f"{base}/media_publish", data={
        "creation_id": creation_id,
        "access_token": token,
    }, timeout=60)
    _raise_graph(pub, "IG media_publish")
    media_id = pub.json()["id"]
    print(f"  ✓ Instagram Reel published: {media_id}")
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
                           ver: str, timeout_s: int = 300) -> str:
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

    # 4. finish + publish
    finish = requests.post(f"{GRAPH}/{ver}/{page_id}/video_reels", data={
        "upload_phase": "finish",
        "video_id": video_id,
        "video_state": "PUBLISHED",
        "description": description,
        "access_token": token,
    }, timeout=60)
    _raise_graph(finish, "FB reel finish")
    print(f"  ✓ Facebook Reel published: {video_id}")
    return video_id


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _raise_graph(resp: requests.Response, what: str) -> None:
    if not resp.ok:
        raise RuntimeError(f"{what} failed [{resp.status_code}]: {resp.text}")


def _build_caption(cfg: Config, title: str, description: str) -> str:
    tags = cfg.get("meta.hashtags", []) or []
    parts = [title.strip()]
    if description.strip():
        parts.append(description.strip())
    if tags:
        parts.append(" ".join(tags))
    return "\n\n".join(parts)[:2200]  # IG caption hard limit


# --------------------------------------------------------------------------- #
# public entrypoint
# --------------------------------------------------------------------------- #
def publish_reel(cfg: Config, video_path: Path, title: str, description: str) -> dict:
    """Cross-post the rendered short to FB and/or IG. Returns per-platform ids.

    Best-effort: raises on hard config/host errors, but the caller treats a
    per-platform failure as non-fatal so a YouTube upload is never blocked.
    """
    token = env("META_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("META_ACCESS_TOKEN not set — cannot cross-post.")

    ver = cfg.get("meta.api_version", "v21.0")
    page_id = str(cfg.get("meta.page_id", "")).strip()
    ig_user_id = str(cfg.get("meta.ig_user_id", "")).strip()
    caption = _build_caption(cfg, title, description)

    results: dict = {}
    url, cleanup = _host_video(video_path)
    try:
        if cfg.get("meta.post_to_instagram", True) and ig_user_id:
            try:
                results["instagram"] = _publish_instagram(token, ig_user_id, url, caption, ver)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! Instagram cross-post failed: {exc}")
                results["instagram_error"] = str(exc)
        if cfg.get("meta.post_to_facebook", True) and page_id:
            try:
                results["facebook"] = _publish_facebook_reel(token, page_id, url, caption, ver)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! Facebook cross-post failed: {exc}")
                results["facebook_error"] = str(exc)
    finally:
        cleanup()
    return results
