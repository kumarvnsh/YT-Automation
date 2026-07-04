"""Upload a rendered video to YouTube via the Data API v3.

Auth model: OAuth 2.0 "installed app" flow. You create an OAuth client in Google
Cloud Console (Desktop type), download client_secret.json into secrets/, then run
scripts/setup_oauth.py ONCE to mint secrets/token.json. After that, uploads are
fully unattended (the refresh token is reused).
"""
from __future__ import annotations

from pathlib import Path

from .config import Config, env, base_dir

# upload = post videos; readonly = let us confirm which channel the token targets.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def token_file() -> Path:
    """Per-channel cached OAuth token (under the active channel's secrets/)."""
    return base_dir() / "secrets" / "token.json"


def _client_secret_path() -> Path:
    rel = env("YOUTUBE_CLIENT_SECRET_FILE", "secrets/client_secret.json")
    p = Path(rel)
    return p if p.is_absolute() else base_dir() / p


def get_credentials(interactive: bool = False):
    """Load cached creds, refreshing or running the OAuth flow as needed.

    interactive=True is used by setup_oauth.py to launch the browser consent.
    During unattended runs we only refresh; we never block on a browser.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    tok = token_file()
    creds = None
    if tok.exists():
        creds = Credentials.from_authorized_user_file(str(tok), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save(creds)
        return creds

    if not interactive:
        raise RuntimeError(
            "No valid YouTube credentials. Run `python scripts/setup_oauth.py` once."
        )

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(_client_secret_path()), SCOPES)
    creds = flow.run_local_server(port=0)
    _save(creds)
    return creds


def _save(creds) -> None:
    tok = token_file()
    tok.parent.mkdir(parents=True, exist_ok=True)
    tok.write_text(creds.to_json(), encoding="utf-8")


def get_my_channel(youtube) -> tuple[str, str]:
    """Return (channel_id, channel_title) for the authorized account."""
    resp = youtube.channels().list(part="snippet", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("No YouTube channel found for the authorized credentials.")
    return items[0]["id"], items[0]["snippet"]["title"]


def upload_video(
    cfg: Config,
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    thumbnail_path: Path | None = None,
) -> str:
    """Upload and return the new video ID. Honors config privacy/category."""
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = get_credentials(interactive=False)
    youtube = build("youtube", "v3", credentials=creds)

    # Safety lock: never upload to the wrong channel.
    expected = (cfg.get("youtube.expected_channel_id") or "").strip()
    if expected:
        channel_id, channel_title = get_my_channel(youtube)
        if channel_id != expected:
            raise RuntimeError(
                f"Channel mismatch — refusing to upload. Authorized channel is "
                f"'{channel_title}' ({channel_id}), but config expects {expected}. "
                f"Delete secrets/token.json and re-run scripts/setup_oauth.py, "
                f"then select the correct channel."
            )
        print(f"  channel verified: {channel_title} ({channel_id})")

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:30],
            "categoryId": str(cfg.get("youtube.category_id", "27")),
        },
        "status": {
            "privacyStatus": cfg.get("youtube.privacy_status", "private"),
            "selfDeclaredMadeForKids": bool(cfg.get("youtube.made_for_kids", False)),
        },
    }

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/*")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"  upload progress: {int(status.progress() * 100)}%")

    video_id = response["id"]
    print(f"  uploaded: https://youtu.be/{video_id}")

    if thumbnail_path and thumbnail_path.exists():
        try:
            youtube.thumbnails().set(
                videoId=video_id, media_body=MediaFileUpload(str(thumbnail_path))
            ).execute()
        except Exception as exc:  # noqa: BLE001
            print(f"  ! thumbnail upload skipped: {exc}")

    return video_id
