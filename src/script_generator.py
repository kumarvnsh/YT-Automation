"""Generate the video script + YouTube metadata via an LLM.

Output (a `Script` dataclass) contains everything downstream needs:
  - title           : YouTube title
  - description     : YouTube description
  - tags            : list[str]
  - segments        : ordered narration beats; each has `narration` text and
                      `keywords` used to fetch matching stock footage.

The narration of all segments concatenated IS the voiceover script.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict

from .config import Config, env
from .topics import recent_titles, pick_angle


@dataclass
class Segment:
    narration: str
    keywords: list[str] = field(default_factory=list)


@dataclass
class Script:
    title: str
    description: str
    tags: list[str]
    segments: list[Segment]
    topic: str

    @property
    def full_narration(self) -> str:
        return " ".join(s.narration.strip() for s in self.segments).strip()

    def to_dict(self) -> dict:
        return asdict(self)


_SYSTEM = (
    "You are a professional short-form and long-form video scriptwriter for a "
    "YouTube channel. You return ONLY valid JSON, no markdown, no commentary."
)


def _build_prompt(cfg: Config, fmt: str, topic_override: str | None = None) -> str:
    persona = cfg.get("channel.persona", "").strip()
    if fmt == "short":
        secs = cfg.get("script.shorts_target_seconds", 20)
        seg_hint = "3 to 5 short segments" if secs <= 30 else "5 to 7 short segments"
    else:
        secs = cfg.get("script.longform_target_seconds", 420)
        seg_hint = "10 to 16 segments"
    words = int(secs * 150 / 60)  # ~150 wpm
    lo, hi = int(words * 0.9), int(words * 1.1)
    avoid = recent_titles()
    avoid_block = "\n".join(f"- {t}" for t in avoid) if avoid else "(none yet)"

    # Topic direction: an explicit override wins; otherwise trend-aware
    # signals, or the curated angle bank.
    if topic_override:
        direction = f"Today's REQUIRED topic (do not deviate): {topic_override}"
    else:
        source = cfg.get("channel.topic_source", "angles")
        if source in ("trends", "on_this_day", "blend"):
            direction = _trend_direction(cfg, source)
        else:
            direction = f"Today's creative angle to explore: {pick_angle(cfg)}"

    return f"""{persona}

TASK: Write ONE {fmt}-form YouTube video script in the "history / did-you-know" niche.
{direction}

Constraints:
- Total narration MUST be {lo}-{hi} words. Count your words before answering and
  comply exactly; this is a hard limit, not a suggestion (target ~{words} words).
- Break the narration into {seg_hint}. Each segment is one or two spoken sentences.
- For EACH segment provide 2-4 visual search keywords describing concrete,
  filmable imagery (e.g. "ancient roman ruins", "old library books", "stormy ocean").
  Avoid abstract keywords. These drive stock-footage search.
- The FIRST segment is the hook and MUST be 8-15 words. It must NOT open with a
  dry date/setup ("In 1915, Alice Ball was a chemist who..."). Instead lead with
  the consequence, twist, or stakes, e.g. "She cured leprosy. Then a man stole
  her credit." or "This law could send you to prison for reading it." Bury the
  date/setup (if any) in segment 2, never segment 1.
- Be factually accurate. Do NOT invent dates, names, or statistics.
- Avoid graphic, violent, or sensitive detail (keep it advertiser-friendly).
- The title must be specific and curiosity-driven, <= 80 characters, and must
  NOT contain hashtags (#).
- Provide 8-15 lowercase tags.
- The description: 2-3 sentences + 3 relevant hashtags on the last line.

DO NOT reuse any of these previously-used titles/topics:
{avoid_block}

Return JSON with EXACTLY this shape:
{{
  "topic": "short internal label for this topic",
  "title": "...",
  "description": "...",
  "tags": ["...", "..."],
  "segments": [
    {{"narration": "...", "keywords": ["...", "..."]}}
  ]
}}"""


def _trend_direction(cfg: Config, source: str) -> str:
    """Build a topic-direction block from live trend / on-this-day signals."""
    from . import trends

    sig = trends.gather_signals(cfg)
    trend_terms = sig["trends"] if source in ("trends", "blend") else []
    otd = sig["on_this_day"] if source in ("on_this_day", "blend") else []

    parts = [f"TODAY IS {sig['date']}. Make the topic feel timely and on-niche "
             "(a past event, myth, or tradition)."]
    if trend_terms:
        parts.append("Trending searches right now (may be unrelated to history):\n- "
                     + "\n- ".join(trend_terms))
    if otd:
        parts.append("On this day in history:\n- " + "\n- ".join(otd))

    parts.append(
        "Pick the STRONGEST idea for a history / did-you-know short:\n"
        "1) BEST: a trending search you can genuinely and ACCURATELY bridge to a real "
        "past event, myth, or tradition — open with that hook so it rides the trend.\n"
        "2) Otherwise: a fascinating 'on this day' anniversary above.\n"
        "3) Otherwise: your own strong idea in the niche.\n"
        "Never force a tie that isn't real, and stay strictly factual."
    )
    return "\n\n".join(parts)


def _extract_json(text: str) -> dict:
    """Best-effort extraction of a JSON object from a model response."""
    text = text.strip()
    # Strip code fences if present.
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Grab the outermost {...} block.
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start : end + 1])
        raise


def _call_anthropic(cfg: Config, prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
    model = cfg.get("script.anthropic_model", "claude-sonnet-4-6")
    msg = client.messages.create(
        model=model,
        max_tokens=4000,
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in msg.content if block.type == "text")


def _call_openai(cfg: Config, prompt: str) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=env("OPENAI_API_KEY"))
    model = cfg.get("script.openai_model", "gpt-4o-mini")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or ""


def regenerate_title(cfg: Config, old_title: str, context_text: str) -> str:
    """Produce a fresh title for an existing video (retitle/republish flow).

    Uses the video's script/description as factual context so the new title
    stays accurate while taking a clearly different angle from the old one.
    """
    prompt = f"""A YouTube short is underperforming and needs a NEW title.

OLD title (do NOT paraphrase it — take a different hook/angle):
{old_title}

Video content (the new title must stay factually consistent with this):
{context_text[:2000]}

Requirements:
- ONE new title, specific and curiosity-driven, <= 80 characters.
- NO hashtags (#).
- Clearly distinct from the old title: different hook, angle, or framing.
- Do not invent facts not present in the content above.

Return JSON with EXACTLY this shape:
{{"title": "..."}}"""

    provider = cfg.get("script.provider", "anthropic")
    for attempt in range(2):
        raw = _call_anthropic(cfg, prompt) if provider == "anthropic" else _call_openai(cfg, prompt)
        title = _extract_json(raw).get("title", "").replace("#", "").strip()[:100]
        if title and title.lower() != old_title.lower().strip():
            return title
    raise RuntimeError(f"Could not generate a title distinct from: {old_title!r}")


def generate_script(cfg: Config, fmt: str, topic_override: str | None = None) -> Script:
    """Generate a Script for fmt in {'short', 'long'}."""
    prompt = _build_prompt(cfg, fmt, topic_override=topic_override)
    provider = cfg.get("script.provider", "anthropic")
    raw = _call_anthropic(cfg, prompt) if provider == "anthropic" else _call_openai(cfg, prompt)
    data = _extract_json(raw)

    segments = [
        Segment(narration=s["narration"], keywords=s.get("keywords", []))
        for s in data["segments"]
        if s.get("narration", "").strip()
    ]

    word_count = len(" ".join(s.narration for s in segments).split())
    if fmt == "short":
        target = int(cfg.get("script.shorts_target_seconds", 20) * 150 / 60)
        lo, hi = int(target * 0.75), int(target * 1.35)
        if not (lo <= word_count <= hi):
            print(f"  ! WARNING: narration is {word_count} words (target ~{target}): "
                  f"prompt compliance was off for this generation.")

    tags = data.get("tags", []) + cfg.get("youtube.default_tags", [])
    # De-dup tags preserving order.
    seen, uniq = set(), []
    for t in tags:
        tl = t.lower().strip()
        if tl and tl not in seen:
            seen.add(tl)
            uniq.append(tl)

    return Script(
        title=data["title"].strip()[:100],
        description=data["description"].strip(),
        tags=uniq[:30],
        segments=segments,
        topic=data.get("topic", data["title"]).strip(),
    )
