"""Generate a topical illustration per script segment via the OpenAI image API.

Stock footage is generic by construction: a segment about the SS United States
gets an interchangeable ocean liner, and a segment about Photo 51 gets a generic
laboratory. This module generates an image for what the narration actually says.

STYLE IS A COMPLIANCE DECISION, NOT A TASTE ONE.
YouTube requires disclosure for synthetic media a viewer "could easily mistake
for a real person, place, scene, or event", and does not require it for clearly
illustrative or obviously unrealistic visuals. The default `illustrative` style
therefore renders engraved/painterly plates rather than photographs: on-topic,
no disclosure obligation, and no risk of presenting a fabricated image as
documentary evidence of a real person or event. Switching to `photoreal` gives
up both protections — see config.

Every failure path returns None so the caller falls back to stock footage; image
generation must never be able to break a run.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

from .config import Config, env

# Rendered plate styles. Each must read as an illustration, never as a photo.
STYLE_PRESETS = {
    "illustrative": (
        "a detailed editorial illustration in the style of a vintage engraved "
        "plate: visible ink hatching and linework, muted parchment and sepia "
        "palette, painterly texture, clearly a hand-made illustration and "
        "obviously not a photograph"
    ),
    "painterly": (
        "a museum-quality painted illustration: visible brushwork and canvas "
        "texture, dramatic but soft lighting, rich muted colour, clearly a "
        "painting and obviously not a photograph"
    ),
    # Realistic output. Requires ticking YouTube's "altered or synthetic
    # content" box on every upload, and depicts real people/events that were
    # never photographed this way. Not the default, deliberately.
    "photoreal": "a photorealistic cinematic still, shallow depth of field",
}
DEFAULT_STYLE = "illustrative"

# Stripped from narration before it becomes an image prompt: these are spoken
# framing devices, not things that can be drawn.
_FILLER = re.compile(
    r"\b(but here'?s the twist|what most people don'?t know|what if|did you know"
    r"|here'?s the thing|and that'?s why|imagine)\b[,.:]?\s*",
    re.IGNORECASE,
)

# Rotated per segment so consecutive stills do not read as the same picture.
# Without this the model returns a centred object among ruins every time, and
# four near-identical fact images blur into a single beat.
_COMPOSITIONS = (
    "Wide establishing view with a deep horizon and small figures for scale.",
    "Tight close-up on the single most important object, filling the frame.",
    "Low three-quarter angle looking up at the subject, dramatic perspective.",
    "Overhead flat-lay study of the objects arranged on a surface.",
    "Mid-distance view framed through an arch, doorway, or foreground opening.",
)


def enabled(cfg: Config) -> bool:
    """Whether this channel generates images instead of pulling stock footage."""
    return bool(cfg.get("assets.ai_images.enabled", False))


def select_segments(cfg: Config, beats: list[str]) -> set[int]:
    """Indices of the segments that get a generated still.

    Two stages: filter to the configured beats, then cap the count. When the
    cap bites, the survivors are spread evenly across the eligible beats rather
    than taken from the front — two images back-to-back mid-video leaves the
    rest of the short on unbroken stock, which defeats the point.

    The first eligible beat (the pivot) is always kept: the cut from motion to
    a held illustration is what makes the turn land.
    """
    eligible = [i for i, b in enumerate(beats) if wanted_for_beat(cfg, b)]
    cap = cfg.get("assets.ai_images.max_images", 0) or 0
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        cap = 0
    if cap <= 0 or len(eligible) <= cap:
        return set(eligible)
    if cap == 1:
        return {eligible[0]}
    # Evenly spaced picks including both ends of the eligible run.
    step = (len(eligible) - 1) / (cap - 1)
    return {eligible[round(i * step)] for i in range(cap)}


def wanted_for_beat(cfg: Config, beat: str) -> bool:
    """Whether this segment should get a generated still rather than stock.

    A short built entirely from stills reads as a slideshow, and stillness costs
    attention. Generated images are spent where specificity pays — the pivot and
    the fact stack, where each beat makes its own concrete claim — while the
    hook, setup and callback keep moving footage.

    An empty `beats` list means every segment gets an image.
    """
    beats = cfg.get("assets.ai_images.beats", None)
    if not beats:
        return True
    return str(beat).strip().lower() in {str(b).strip().lower() for b in beats}


def build_prompt(
    cfg: Config, narration: str, keywords: list[str], variant: int = 0
) -> str:
    """Turn one segment into an image prompt.

    Derived in Python from fields the script already emits, so enabling this
    needs no change to the LLM prompt or JSON schema on any channel.

    `variant` is the segment index; it rotates the camera composition so a
    run of fact beats does not come back as five versions of the same shot.
    """
    style_name = str(cfg.get("assets.ai_images.style", DEFAULT_STYLE)).lower()
    style = STYLE_PRESETS.get(style_name, STYLE_PRESETS[DEFAULT_STYLE])

    subject = _FILLER.sub("", narration).strip()
    # Keep it short: long prompts drift toward collage and lose the subject.
    subject = " ".join(subject.split()[:40])
    hint = ", ".join(keywords[:3])

    parts = [f"{style}."]
    if subject:
        parts.append(f"Subject: {subject}")
    if hint:
        parts.append(f"Visual focus: {hint}.")
    parts.append(_COMPOSITIONS[variant % len(_COMPOSITIONS)])
    parts.append(
        "Vertical framing, subject clear of the lower third where captions sit. "
        # Banning text outright fails when the subject IS writing: the model
        # returns invented glyphs anyway. Allow inscriptions as texture, but
        # keep them illegible so no fabricated wording is ever readable.
        "Any inscriptions must be weathered and illegible, never readable words. "
        "No modern text, captions, watermarks, logos, or borders."
    )
    return " ".join(parts)


def generate(
    cfg: Config,
    narration: str,
    keywords: list[str],
    dest: Path,
    variant: int = 0,
) -> Path | None:
    """Generate one image for a segment. Returns None on any failure."""
    key = env("OPENAI_API_KEY")
    if not key:
        print("  ! ai images: OPENAI_API_KEY missing — falling back to stock.")
        return None

    prompt = build_prompt(cfg, narration, keywords, variant)
    model = str(cfg.get("assets.ai_images.model", "gpt-image-1"))
    size = str(cfg.get("assets.ai_images.size", "1024x1536"))
    quality = str(cfg.get("assets.ai_images.quality", "medium"))

    # Hard timeout: image requests occasionally hang, and an unbounded call
    # would stall the whole time-budgeted pipeline run rather than falling
    # back to stock footage.
    timeout = float(cfg.get("assets.ai_images.timeout_seconds", 90))

    try:
        from openai import OpenAI

        client = OpenAI(api_key=key, timeout=timeout, max_retries=1)
        resp = client.images.generate(
            model=model, prompt=prompt, size=size, quality=quality, n=1
        )
        payload = resp.data[0]
        raw = getattr(payload, "b64_json", None)
        if raw:
            data = base64.b64decode(raw)
        else:
            import requests

            url = getattr(payload, "url", None)
            if not url:
                raise RuntimeError("image response carried neither b64_json nor url")
            with requests.get(url, timeout=90) as r:
                r.raise_for_status()
                data = r.content

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        if dest.stat().st_size <= 0:
            raise RuntimeError("wrote an empty file")
        return dest
    except Exception as exc:  # noqa: BLE001 - never break a run over an image
        print(f"  ! ai image generation failed ({exc}) — falling back to stock.")
        return None
