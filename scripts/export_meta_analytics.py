#!/usr/bin/env python3
"""Export Facebook Reels + Instagram Reels stats to data/meta_analytics.json.

Usage:
  python scripts/export_meta_analytics.py [--config <path>] [--limit 50]

Uses the same META_ACCESS_TOKEN system-user token as the cross-poster
(src/meta_uploader.py) and the page_id / ig_user_id from config meta:.
Failures are explicit: missing credentials, permissions, or API errors return a
non-zero exit code so the analytics workflow cannot silently publish stale or
zeroed data.

Output shape (mirrors data/analytics.json videos):
  {"facebook":  {"updated": iso, "videos": [{id,title,publishedAt,thumbnail,views,likes,comments,url}]},
   "instagram": {"updated": iso, "videos": [...]}}
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config, base_dir, env  # noqa: E402
from src.meta_uploader import GRAPH, _page_access_token, _raise_graph  # noqa: E402


# Graph error codes Meta documents as temporary (unknown / service / rate limits).
_TRANSIENT_CODES = {1, 2, 4, 17, 32, 341, 613}
FB_PAGE_SIZE = 25  # smaller pages keep the heavy video_insights expansion from timing out


def _is_transient(resp: requests.Response) -> bool:
    if resp.status_code in (429, 500, 502, 503, 504):
        return True
    try:
        body = json.loads(resp.text or "")
    except (TypeError, ValueError):
        return False
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return False
    return err.get("is_transient") is True or err.get("code") in _TRANSIENT_CODES


def _graph_get(url: str, *, params: dict, timeout: int = 60, attempts: int = 3,
               backoff: float = 10.0) -> requests.Response:
    """GET with retries for Meta's temporary failures; permanent errors return at once."""
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == attempts:
                raise
        else:
            if resp.ok or not _is_transient(resp) or attempt == attempts:
                return resp
            print(f"  transient Graph error [{resp.status_code}], retry {attempt}/{attempts - 1}")
        time.sleep(backoff * attempt)
    raise AssertionError("unreachable")


def _experiment_metrics(video: dict, platform: str) -> dict:
    """Keep unsupported per-Reel measures explicit instead of inventing zeroes."""
    video.setdefault("unavailable_metrics", {})
    metrics = ("shares", "average_watch_time_seconds", "followers_gained", "three_second_retention", "completion_rate")
    if platform == "instagram":
        metrics += ("skip_rate",)
    for metric in metrics:
        video.setdefault(metric, None)
        if video[metric] is None:
            video["unavailable_metrics"].setdefault(metric, (
                f"{platform} per-media metric not collected by this exporter; requires a supported "
                "Insights metric and account permissions. No account-level substitute used."
            ))
    return video


def _metric_values(node, edge, token, ver, metrics, batch=False):
    """Isolate metric failures; never expose a credential or replace missing data with zero."""
    values, errors = {}, {}
    if batch:
        try:
            response=requests.get(f'{GRAPH}/{ver}/{node}/{edge}',
                headers={'Authorization':f'Bearer {token}'},
                params={'metric':','.join(metrics),'period':'lifetime'},timeout=30)
            payload=response.json()
            if response.ok and not payload.get('error'):
                for item in payload.get('data',[]):
                    rows=item.get('values') or []
                    if item.get('name') in metrics and rows and rows[-1].get('value') is not None:
                        values[item['name']]=rows[-1]['value']
        except (requests.RequestException,ValueError):
            pass  # Older metric surfaces can reject a combined list.
    for metric in metrics:
        if metric in values: continue
        try:
            response = requests.get(f"{GRAPH}/{ver}/{node}/{edge}",
                                    headers={"Authorization": f"Bearer {token}"},
                                    params={"metric": metric}, timeout=30)
            payload = response.json()
            if not response.ok or payload.get("error"):
                error = payload.get("error") or {}
                code = error.get("code")
                status = "access_denied" if code in (10, 190, 200, 294) else "request_rejected"
                message = str(error.get("message") or "Metric request rejected").replace(token, "[redacted]")
                errors[metric] = f"{status}: code {code}; {message[:250]}"
                continue
            rows = [r for item in payload.get("data", []) if item.get("name") == metric
                    for r in item.get("values", [])]
            if rows and rows[-1].get("value") is not None:
                values[metric] = rows[-1]["value"]
            else:
                errors[metric] = "no_data: API returned no value; reporting delay or media eligibility may apply."
        except (requests.RequestException, ValueError):
            errors[metric] = "fetch_failed: metric request failed; retry on the next export."
    return values, errors


def _facebook_reel_insights(media_id, token, ver):
    metrics = ("post_video_avg_time_watched", "post_video_retention_graph",
               "fb_reels_total_plays", "blue_reels_play_count", "fb_reels_replay_count")
    values, errors = _metric_values(media_id, "video_insights", token, ver, metrics,batch=True)
    for metric,legacy in zip(metrics[:2],('total_video_avg_time_watched','total_video_retention_graph')):
        if metric not in values:
            fallback,_=_metric_values(media_id,'video_insights',token,ver,(legacy,))
            if legacy in fallback:
                values[metric]=fallback[legacy];errors.pop(metric,None)
    watch = values.get(metrics[0])
    result = {"average_watch_time_seconds": watch / 1000 if isinstance(watch, (int, float)) and not isinstance(watch, bool) else None,
              "retention_graph": values.get(metrics[1]),
              "unavailable_metrics": {("average_watch_time_seconds" if k == metrics[0] else "retention_graph"): v
                                      for k, v in errors.items()}}
    for field,metric in [('plays','fb_reels_total_plays'),('initial_plays','blue_reels_play_count'),('replays','fb_reels_replay_count')]:
        value=values.get(metric)
        result[field]=int(value) if isinstance(value,(int,float)) and not isinstance(value,bool) and value>=0 else None
    # Retention graph samples are not a unique-view completion rate or a 3-second start rate.
    return result


def facebook_view_fields(insights, reel):
    """Keep Reel starts/replays separate from legacy three-second video views."""
    result=dict(reel)
    try: legacy=_insight_views(insights)
    except RuntimeError: legacy=None
    metric=next((key for key in ('plays','initial_plays') if reel.get(key) is not None),None)
    result.update(views=reel.get(metric) if metric else legacy,
                  views_metric={'plays':'fb_reels_total_plays','initial_plays':'blue_reels_play_count'}.get(metric,'legacy_video_views' if legacy is not None else None),
                  three_second_views=next((v.get('values',[{}])[-1].get('value') for v in (insights or {}).get('data',[])
                                           if v.get('name')=='total_video_views' and v.get('values')),None))
    result['insight_status']='available' if result['views'] is not None else 'unavailable'
    if result['views'] is None:
        result.setdefault('unavailable_metrics',{})['views']='Meta returned no supported per-video count; not treated as zero.'
    return result


def _first_line(text: str) -> str:
    return (text or "").strip().splitlines()[0][:120] if (text or "").strip() else "(untitled)"


def _insight_views(insights: dict) -> int:
    """Prefer explicitly recognized video views; never mistake ad views for video views."""
    by_name = {item.get('name'): item for item in (insights or {}).get('data', [])}
    for metric in ("fb_reels_total_plays", "blue_reels_play_count", "total_video_plays", "total_video_views"):
        item = by_name.get(metric)
        if item:
            values = item.get("values", [])
            if values:
                v = values[0].get("value")
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                    return int(v)
    raise RuntimeError(
        "Facebook video_insights contained no recognizable view metric; verify "
        "the token and system user have Page insights access."
    )


def fetch_facebook(token: str, page_id: str, ver: str, limit: int) -> list[dict]:
    page_token = _page_access_token(token, page_id, ver)
    rows: list[dict] = []
    params = {
        "fields": "id,title,description,created_time,permalink_url,picture,"
                  "likes.summary(true),comments.summary(true),video_insights",
        "limit": min(limit, FB_PAGE_SIZE),
        "access_token": page_token,
    }
    while len(rows) < limit:
        r = _graph_get(f"{GRAPH}/{ver}/{page_id}/videos", params=params)
        _raise_graph(r, "Facebook video analytics")
        body = r.json()
        rows.extend(body.get("data", []))
        after = ((body.get("paging") or {}).get("cursors") or {}).get("after")
        if not after or not (body.get("paging") or {}).get("next") or not body.get("data"):
            break
        params = {**params, "after": after}
    def build_video(v):
        permalink = v.get("permalink_url", "")
        return _experiment_metrics({
            "id": v["id"],
            "title": (v.get("title") or "").strip() or _first_line(v.get("description", "")),
            "publishedAt": v.get("created_time", ""),
            "thumbnail": v.get("picture", ""),
            "likes": ((v.get("likes") or {}).get("summary") or {}).get("total_count", 0),
            "comments": ((v.get("comments") or {}).get("summary") or {}).get("total_count", 0),
            "url": f"https://www.facebook.com{permalink}" if permalink.startswith("/") else permalink,
            **facebook_view_fields(v.get('video_insights'),_facebook_reel_insights(v["id"], page_token, ver)),
        }, "facebook")
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(build_video,rows[:limit]))


def _ig_media_views(media_id: str, token: str, ver: str, metric: str) -> tuple[int, str]:
    """Try the given insights metric, falling back across API renames (views/plays)."""
    errors = []
    for m in (metric, "views" if metric == "plays" else "plays"):
        r = requests.get(f"{GRAPH}/{ver}/{media_id}/insights",
                         params={"metric": m, "access_token": token}, timeout=30)
        if not r.ok:
            errors.append(f"{m} [{r.status_code}]: {r.text}")
            continue
        data = r.json().get("data", [])
        if data and data[0].get("values"):
            value = data[0]["values"][0].get("value")
            if isinstance(value, (int, float)):
                return int(value), m
        errors.append(f"{m}: empty insights data")
    raise RuntimeError(
        f"Instagram insights failed for media {media_id}; verify the token has "
        f"instagram_manage_insights and pages_read_engagement. {'; '.join(errors)}"
    )


_IG_REEL_METRICS = ("ig_reels_avg_watch_time", "reels_skip_rate", "shares")


def _ig_reel_insights(media_id: str, token: str, ver: str) -> dict:
    """Watch time, skip rate and shares for one Reel. Best-effort: a missing metric stays None.

    Request metrics individually so unsupported fields do not hide available values.
    """
    values, errors = _metric_values(media_id, "insights", token, ver, _IG_REEL_METRICS)
    number = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else None  # noqa: E731
    watch_ms = number(values.get("ig_reels_avg_watch_time"))
    return {
        "average_watch_time_seconds": round(watch_ms / 1000, 2) if watch_ms is not None else None,  # API returns ms
        "skip_rate": number(values.get("reels_skip_rate")),  # share leaving in the first 3s, as Meta reports it
        "shares": number(values.get("shares")),
        "unavailable_metrics": { {"ig_reels_avg_watch_time": "average_watch_time_seconds", "reels_skip_rate": "skip_rate"}.get(k, k): v
                                 for k, v in errors.items()},
    }


IG_INSIGHTS_GRACE_HOURS = 24  # Meta can return no/erroring insights for brand-new media


def _is_fresh(timestamp: str, now: datetime | None = None) -> bool:
    try:
        posted = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S%z")
    except (TypeError, ValueError):
        return False
    age = (now or datetime.now(timezone.utc)) - posted
    return age.total_seconds() < IG_INSIGHTS_GRACE_HOURS * 3600


def fetch_instagram(token: str, ig_user_id: str, ver: str, limit: int) -> list[dict]:
    r = _graph_get(
        f"{GRAPH}/{ver}/{ig_user_id}/media",
        params={
            "fields": "id,caption,media_type,timestamp,permalink,thumbnail_url,"
                      "media_url,like_count,comments_count",
            "limit": limit,
            "access_token": token,
        },
    )
    _raise_graph(r, "Instagram media list")
    videos = []
    metric = "views"  # v22+ name; falls back to "plays" on older API versions
    for m in r.json().get("data", []):
        if m.get("media_type") not in ("VIDEO", "REELS"):
            continue
        try:
            views, metric = _ig_media_views(m["id"], token, ver, metric)
        except RuntimeError as exc:
            # Insights for a Reel posted minutes ago are not ready yet; only that
            # case is tolerated. Older Reels failing still fails the export.
            if not _is_fresh(m.get("timestamp", "")):
                raise
            print(f"  instagram media {m['id']} is too new for insights; views left unavailable")
            views = None
            pending = {"views": f"insights_pending: Reel younger than {IG_INSIGHTS_GRACE_HOURS}h; {exc}"[:300]}
        else:
            pending = {}
        videos.append({
            "id": m["id"],
            "title": _first_line(m.get("caption", "")),
            "publishedAt": m.get("timestamp", ""),
            "thumbnail": m.get("thumbnail_url") or m.get("media_url", ""),
            "views": views,
            "likes": m.get("like_count", 0),
            "comments": m.get("comments_count", 0),
            "url": m.get("permalink", ""),
            **_ig_reel_insights(m["id"], token, ver),
        })
        videos[-1]["unavailable_metrics"].update(pending)
    return [_experiment_metrics(video, "instagram") for video in videos]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()

    token = env("META_ACCESS_TOKEN")
    if not token:
        print("META_ACCESS_TOKEN not set — Meta analytics export cannot run.", file=sys.stderr)
        return 1

    cfg = load_config(args.config)
    ver = cfg.get("meta.api_version", "v21.0")
    page_id = str(cfg.get("meta.page_id", "")).strip()
    ig_user_id = str(cfg.get("meta.ig_user_id", "")).strip()
    now = datetime.now(timezone.utc).isoformat()

    out: dict = {}
    failures: list[str] = []
    if page_id:
        try:
            vids = fetch_facebook(token, page_id, ver, args.limit)
            vids.sort(key=lambda v: v.get("publishedAt", ""), reverse=True)
            out["facebook"] = {"updated": now, "videos": vids}
            print(f"facebook: {len(vids)} videos")
        except Exception as exc:  # noqa: BLE001
            print(f"! facebook export failed: {exc}")
            failures.append(f"facebook: {exc}")
    if ig_user_id:
        try:
            vids = fetch_instagram(token, ig_user_id, ver, args.limit)
            vids.sort(key=lambda v: v.get("publishedAt", ""), reverse=True)
            out["instagram"] = {"updated": now, "videos": vids}
            print(f"instagram: {len(vids)} videos")
        except Exception as exc:  # noqa: BLE001
            print(f"! instagram export failed: {exc}")
            failures.append(f"instagram: {exc}")

    if failures:
        print(
            "Meta analytics export was incomplete: " + " | ".join(failures),
            file=sys.stderr,
        )
        return 1
    if not out:
        print("nothing exported — leaving data/meta_analytics.json untouched.", file=sys.stderr)
        return 1
    path = base_dir() / "data" / "meta_analytics.json"
    # Merge with existing file so one failed platform doesn't wipe the other's data.
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            existing.update(out)
            out = existing
        except json.JSONDecodeError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
