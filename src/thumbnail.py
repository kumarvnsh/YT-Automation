"""Custom thumbnail: two OpenAI-generated candidates, Claude picks one.

YouTube allows one custom thumbnail per Short and has no A/B Test & Compare
for Shorts, so two concepts are generated (text-led and image-led) and a cheap
Claude vision call picks the stronger one. The loser is kept in the stage dir
so it can be swapped in by hand from Studio.

Style comes from assets.ai_images.style, so thumbnails stay illustrative
(no synthetic-media disclosure obligation, same as the in-video stills).
Every failure path returns None; a thumbnail must never block an upload.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

from . import imagegen
from .config import Config, env

_CONCEPTS = {
    "a": (
        "Text-led YouTube thumbnail. One huge, bold, high-contrast hook of 2-4 "
        "words (written in clean, correctly spelled English capitals) derived "
        "from the title, plus one iconic subject. Text fills the upper half."
    ),
    "b": (
        "Image-led YouTube thumbnail. A dramatic, tightly framed close-up of "
        "the single most intriguing subject from the story, strong silhouette, "
        "one accent colour, no text at all."
    ),
}


def build_prompt(cfg: Config, title: str, hook: str, concept: str) -> str:
    style_name = str(cfg.get("assets.ai_images.style", imagegen.DEFAULT_STYLE)).lower()
    style = imagegen.STYLE_PRESETS.get(style_name, imagegen.STYLE_PRESETS[imagegen.DEFAULT_STYLE])
    return (
        f"{_CONCEPTS[concept]} Video title: \"{title}\". Story hook: {hook} "
        f"Rendered as {style}. Readable at phone-feed size: one focal point, "
        "big shapes, no clutter, keep the subject centred so a square or 16:9 "
        "crop still works. No logos, watermarks, borders, or YouTube UI."
    )


def _to_jpeg(src: Path, dest: Path) -> Path:
    """YouTube rejects thumbnails over 2MB; the API returns PNGs larger than that."""
    Image.open(src).convert("RGB").save(dest, "JPEG", quality=88, optimize=True)
    src.unlink()  # ~2.5MB PNG; keeps the run artifact small
    return dest


def _pick(cfg: Config, title: str, paths: list[Path]) -> int:
    """Index of the stronger candidate per Claude; 0 if the call fails."""
    try:
        import anthropic

        content = []
        for label, p in zip("AB", paths):
            im = Image.open(p)
            im.thumbnail((512, 512))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
            content += [
                {"type": "text", "text": f"Thumbnail {label}:"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.standard_b64encode(buf.getvalue()).decode()}},
            ]
        content.append({"type": "text", "text": (
            f"These are candidate thumbnails for a history YouTube Short titled \"{title}\". "
            "Which gets more clicks in a phone feed: clearer focal point, readable at small "
            "size, curiosity, correctly spelled text? Answer with exactly one letter: A or B."
        )})
        msg = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY")).messages.create(
            model=cfg.get("thumbnails.judge_model", "claude-sonnet-5-5"),
            max_tokens=2000,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": content}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip().upper()
        return 1 if text.startswith("B") else 0
    except Exception as exc:  # noqa: BLE001
        print(f"  ! thumbnail judge failed ({exc}) — using candidate A.")
        return 0


def make_thumbnail(cfg: Config, st: dict, stage: Path) -> str | None:
    """Generate both candidates, pick one, return its stage-relative path."""
    title = st["script"]["title"]
    segs = st["script"].get("segments") or [{}]
    hook = segs[0].get("narration", "").strip()
    size = "1024x1536" if st["fmt"] == "short" else "1536x1024"

    made: list[Path] = []
    for concept in _CONCEPTS:
        raw = imagegen.render_prompt(
            build_prompt(cfg, title, hook, concept), stage / f"thumb_{concept}.png",
            model=str(cfg.get("thumbnails.model", "gpt-image-1")), size=size,
            quality=str(cfg.get("thumbnails.quality", "medium")),
            timeout=float(cfg.get("assets.ai_images.timeout_seconds", 90)),
        )
        if raw:
            made.append(_to_jpeg(raw, stage / f"thumb_{concept}.jpg"))
    if not made:
        return None
    best = made[_pick(cfg, title, made)] if len(made) > 1 else made[0]
    # Fixed name so the repost flow (scripts/republish.py) can find it.
    (stage / "thumbnail.jpg").write_bytes(best.read_bytes())
    print(f"  ✓ thumbnail: {best.name} chosen from {len(made)} candidate(s)")
    return "thumbnail.jpg"
