"""Custom thumbnail: two OpenAI-generated candidates, Claude picks one.

YouTube allows one custom thumbnail per Short and has no A/B Test & Compare
for Shorts, so two compositions are generated and a cheap Claude vision call
picks the stronger one. The loser is kept in the stage dir so it can be
swapped in by hand from Studio.

House style: cinematic golden-hour digital painting, a hero object up front,
and a huge two-line hook (white line + yellow punch line, heavy black outline).
The hook is written by Claude first so the image model only has to letter
2-4 exact words instead of inventing (or pasting) a whole title.
Every failure path returns None; a thumbnail must never block an upload.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

from . import imagegen
from .config import Config, env

_STYLE = (
    "Viral YouTube Shorts thumbnail, vertical 9:16. Hyper-detailed cinematic "
    "digital painting, dramatic golden-hour backlighting, warm amber and "
    "orange glow, rich saturated colour, high contrast, volumetric light and "
    "smoke, shallow depth of field. Clearly a painted illustration, not a photograph."
)
_TEXT = (
    'Huge bold condensed sans-serif headline in the upper-middle third, centred, '
    'spelled exactly as given, two lines: line 1 "{l1}" in white, line 2 "{l2}" '
    "in bright yellow. Both lines have a thick black outline and a strong drop "
    "shadow so they read instantly on a phone. No other text anywhere."
)
_CONCEPTS = {
    "a": (
        "Composition: the story's most iconic object as a large hero in the "
        "lower half foreground, ornate and glossy, with one bold red graphic "
        "symbol (prohibition sign, warning sign, or arrow — whichever fits the "
        "hook) overlapping it. Behind it, a split background showing two "
        "contrasting period scenes from the story."
    ),
    "b": (
        "Composition: one dramatic period scene from the story with a single "
        "central figure or event caught mid-action, strong silhouette against "
        "the light, the key object clearly visible in the lower half, one bold "
        "red graphic accent pointing at it."
    ),
}


def build_prompt(title: str, story: str, hook: tuple[str, str], concept: str) -> str:
    return (
        f"{_STYLE} {_CONCEPTS[concept]} Story: {title}. {story} "
        f"{_TEXT.format(l1=hook[0], l2=hook[1])} Keep headline and hero object "
        "inside the central square so a 16:9 crop still works, and keep the "
        "bottom 15% free of important detail. No logos, watermarks, borders, "
        "or YouTube UI."
    )


def _hook(cfg: Config, title: str, story: str) -> tuple[str, str]:
    """2-4 word curiosity hook split into (white line, yellow punch line)."""
    try:
        import anthropic

        msg = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY")).messages.create(
            model=cfg.get("thumbnails.judge_model", "claude-sonnet-5-5"),
            max_tokens=2000,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": (
                f"History Short titled \"{title}\". Story: {story}\n\n"
                "Write a 2-4 word ALL-CAPS thumbnail hook, a punchy true claim that "
                "creates curiosity (style: COFFEE WAS | BANNED!). Only claims the "
                "story supports. Reply with exactly: LINE1 | LINE2 (LINE2 = the "
                "punch word(s), may end with ! or ?)."
            )}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        l1, l2 = (x.strip().upper() for x in text.splitlines()[-1].split("|", 1))
        if l1 and l2 and len((l1 + " " + l2).split()) <= 5:
            return l1, l2
    except Exception as exc:  # noqa: BLE001
        print(f"  ! thumbnail hook failed ({exc}) — using title words.")
    words = title.upper().split()
    return " ".join(words[:2]), " ".join(words[2:4]) or "!"


MAX_BYTES = 2 * 1024 * 1024  # YouTube thumbnails.set hard limit


def is_valid_jpeg(path: Path) -> bool:
    """Fully decodes the file: catches truncated/corrupt output before YouTube does."""
    try:
        if not 0 < path.stat().st_size <= MAX_BYTES:
            return False
        with Image.open(path) as im:
            if im.format != "JPEG" or min(im.size) < 640:
                return False
            im.load()
        return True
    except Exception:  # noqa: BLE001
        return False


def _to_jpeg(src: Path, dest: Path) -> Path | None:
    """PNG from the image API -> validated JPEG under 2MB, or None if unusable."""
    try:
        with Image.open(src) as im:
            rgb = im.convert("RGB")
        for q in (88, 78, 65):
            rgb.save(dest, "JPEG", quality=q, optimize=True)
            if dest.stat().st_size <= MAX_BYTES:
                break
        src.unlink()  # ~2.5MB PNG; keeps the run artifact small
    except Exception as exc:  # noqa: BLE001 - a bad image must never block upload
        print(f"  ! thumbnail {src.name} unreadable ({exc}) — discarded.")
        return None
    return dest if is_valid_jpeg(dest) else None


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
    story = " ".join(x.get("narration", "") for x in segs[:2]).strip()
    hook = _hook(cfg, title, story)
    print(f"  thumbnail hook: {hook[0]} / {hook[1]}")
    size = "1024x1536" if st["fmt"] == "short" else "1536x1024"

    made: list[Path] = []
    for concept in _CONCEPTS:
        raw = imagegen.render_prompt(
            build_prompt(title, story, hook, concept), stage / f"thumb_{concept}.png",
            model=str(cfg.get("thumbnails.model", "gpt-image-1")), size=size,
            quality=str(cfg.get("thumbnails.quality", "medium")),
            timeout=float(cfg.get("assets.ai_images.timeout_seconds", 90)),
        )
        jpg = _to_jpeg(raw, stage / f"thumb_{concept}.jpg") if raw else None
        if jpg:
            made.append(jpg)
    if not made:
        return None
    best = made[_pick(cfg, title, made)] if len(made) > 1 else made[0]
    # Fixed name so the repost flow (scripts/republish.py) can find it.
    (stage / "thumbnail.jpg").write_bytes(best.read_bytes())
    print(f"  ✓ thumbnail: {best.name} chosen from {len(made)} candidate(s)")
    return "thumbnail.jpg"
