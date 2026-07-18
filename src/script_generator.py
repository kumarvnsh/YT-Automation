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
import random
import re
from dataclasses import dataclass, field, asdict
from datetime import date

from .config import Config, base_dir, env
from .topics import recent_titles, pick_angle, performance_examples, series_turn


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
    provider: str | None = None
    fallback_used: bool = False

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
    niche = (cfg.get("channel.niche") or "history / did-you-know").strip()
    channel_rules = "\n".join(
        rule
        for rule in (
            (cfg.get("channel.language_instruction") or "").strip(),
            (cfg.get("channel.content_rules") or "").strip(),
            (cfg.get("channel.safety_rules") or "").strip(),
        )
        if rule
    )
    today = date.today()
    task_context = f"TODAY'S DATE: {today.day} {today:%B %Y}"
    if channel_rules:
        task_context += f"\n{channel_rules}"
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

    # Topic direction: an explicit override wins, then a series episode when
    # one is due, then trend-aware signals or the curated angle bank.
    series = None if topic_override else series_turn(cfg)
    if topic_override:
        direction = f"Today's REQUIRED topic (do not deviate): {topic_override}"
    elif series:
        direction = _series_direction(cfg, series)
    else:
        source = cfg.get("channel.topic_source", "angles")
        if source in ("trends", "on_this_day", "blend"):
            direction = _trend_direction(cfg, source)
        else:
            direction = f"Today's creative angle to explore: {pick_angle(cfg)}"

    # A series episode carries its name in the title so the run is recognisable.
    series_title_rule = (
        f'\n- This is a "{series}" episode: the title MUST end with " | {series}", '
        "and the whole thing (episode title + suffix) must still fit in 80 characters."
        if series else ""
    )

    # Like-rate feedback: what this channel's audience actually engaged with.
    winners, losers = performance_examples()
    perf_block = ""
    if winners:
        perf_block = ("\n\nThese past videos scored HIGHEST with this audience — favour "
                      "topics and structures like them:\n- " + "\n- ".join(winners))
        if losers:
            perf_block += ("\nThese scored LOWEST — avoid this kind of topic:\n- "
                           + "\n- ".join(losers))

    return f"""{persona}

TASK: Write ONE {fmt}-form YouTube video script in the "{niche}" niche.
{task_context}
{direction}{perf_block}

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
  NOT contain hashtags (#).{series_title_rule}
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


def _series_direction(cfg: Config, series: str) -> str:
    """Build the topic-direction block for a recurring series episode.

    Deliberately ignores trending searches: the trend-bridged videos on this
    channel pull views but the worst like-rates, and a series episode is meant
    to earn the subscribe, not the impression.
    """
    custom = (cfg.get("series.direction") or "").strip()
    if custom:
        return f'This video is an episode of the recurring series "{series}".\n\n{custom}'
    return (
        f'This video is an episode of the recurring series "{series}".\n\n'
        "Pick a real historical moment that ALMOST went the other way — a decision, "
        "accident, vote, weather turn, or missed message where one small change would "
        "have redirected history. Make the near-miss itself the hook.\n"
        "- The counterfactual must be genuine: something contemporaries or historians "
        "actually recognised as a close call. Never invent a hinge that wasn't one.\n"
        "- State plainly what nearly happened and what actually happened. Do not "
        "speculate wildly about the alternate timeline — name the stakes and stop.\n"
        "- End on the open question, so viewers argue about it in the comments."
    )


def _trend_direction(cfg: Config, source: str) -> str:
    """Build a topic-direction block from live trend / on-this-day signals."""
    from . import trends

    sig = trends.gather_signals(cfg)
    trend_terms = sig["trends"] if source in ("trends", "blend") else []
    otd = sig["on_this_day"] if source in ("on_this_day", "blend") else []

    parts = [f"TODAY IS {sig['date']}. Make the topic feel timely and on-niche "
             "(a past event, myth, or tradition)."]
    if trend_terms:
        parts.append("Trending searches right now, biggest first, with news context "
                     "(may be unrelated to history):\n- "
                     + "\n- ".join(trend_terms))
    if otd:
        parts.append("On this day in history:\n- " + "\n- ".join(otd))

    parts.append(
        "Pick the STRONGEST idea for a history / did-you-know short:\n"
        "1) BEST: a trending search you can genuinely and ACCURATELY bridge to a real "
        "past event, myth, or tradition — open with that hook so it rides the trend. "
        "Work down the trend list IN ORDER and seriously attempt a bridge for each "
        "before moving on; several queries about the same event (a tournament, an "
        "election, a film) mean it is huge — a sports event bridges to the history of "
        "the sport, the host city, past finals, famous upsets, or the trophy itself.\n"
        "2) Only if NO trend above bridges honestly: a fascinating 'on this day' "
        "anniversary above.\n"
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


def _provider_call(cfg: Config, provider: str, prompt: str) -> str:
    if provider == "anthropic":
        return _call_anthropic(cfg, prompt)
    if provider == "openai":
        return _call_openai(cfg, prompt)
    raise RuntimeError(f"Unsupported script provider: {provider}")


def _next_round_robin_provider(cfg: Config) -> str:
    providers = [
        cfg.get("script.provider") or cfg.get("script.primary_provider", "anthropic"),
        cfg.get("script.fallback_provider", "openai"),
    ]
    providers = list(dict.fromkeys(providers))
    path = base_dir() / "data" / "provider_rotation.json"
    try:
        index = int(json.loads(path.read_text(encoding="utf-8")).get("next_index", 0))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        index = 0
    selected = providers[index % len(providers)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"next_index": (index + 1) % len(providers)}), encoding="utf-8"
    )
    return selected


def _provider_sequence(cfg: Config) -> list[str]:
    legacy = cfg.get("script.provider", "anthropic")
    routing = cfg.get("script.routing")
    if not routing:
        return [legacy]

    primary = cfg.get("script.provider") or cfg.get("script.primary_provider", legacy)
    fallback = cfg.get("script.fallback_provider", "openai")
    if routing == "random":
        primary = random.choice([primary, fallback])
    elif routing == "round_robin":
        primary = _next_round_robin_provider(cfg)
    elif routing != "fallback":
        raise RuntimeError(f"Unsupported script routing mode: {routing}")

    return list(dict.fromkeys([primary, fallback]))


def _call_with_routing(cfg: Config, prompt: str, validator=None) -> tuple[str, str, bool]:
    errors = []
    for index, provider in enumerate(_provider_sequence(cfg)):
        try:
            raw = _provider_call(cfg, provider, prompt)
            if validator is not None:
                validator(raw)
            return raw, provider, index > 0
        except Exception as exc:  # noqa: BLE001 - try configured fallback provider
            errors.append(f"{provider}: {exc}")
    raise RuntimeError("All script providers failed: " + " | ".join(errors))


def _parse_script_response(raw: str) -> tuple[dict, list[Segment]]:
    data = _extract_json(raw)
    if not isinstance(data, dict):
        raise ValueError("script response must be an object")
    for key in ("title", "description"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"script response missing or invalid: {key}")
    if not isinstance(data.get("segments"), list):
        raise ValueError("script response missing or invalid: segments")
    if "tags" in data and (
        not isinstance(data["tags"], list)
        or not all(isinstance(tag, str) for tag in data["tags"])
    ):
        raise ValueError("script response has invalid tags")
    if "topic" in data and (
        not isinstance(data["topic"], str) or not data["topic"].strip()
    ):
        raise ValueError("script response has invalid topic")

    segments = []
    for item in data["segments"]:
        if not isinstance(item, dict) or not isinstance(item.get("narration"), str):
            raise ValueError("script response has invalid segment narration")
        narration = item["narration"].strip()
        keywords = item.get("keywords", [])
        if not narration or not isinstance(keywords, list) or not all(
            isinstance(keyword, str) for keyword in keywords
        ):
            raise ValueError("script response has invalid segment")
        segments.append(Segment(narration, keywords))
    if not segments:
        raise ValueError("script response has no non-empty segments")
    return data, segments


def _parse_title_response(raw: str) -> str:
    title = _extract_json(raw).get("title", "").replace("#", "").strip()[:100]
    if not title:
        raise ValueError("title response missing: title")
    return title


def _dedupe_tags(tags: list[str], cfg: Config) -> list[str]:
    seen, unique = set(), []
    for tag in tags + (cfg.get("youtube.default_tags", []) or []):
        normalized = tag.lower().strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return unique[:30]


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

    for attempt in range(2):
        raw, _provider, _fallback_used = _call_with_routing(
            cfg, prompt, _parse_title_response
        )
        title = _parse_title_response(raw)
        if title and title.lower() != old_title.lower().strip():
            return title
    raise RuntimeError(f"Could not generate a title distinct from: {old_title!r}")


def generate_script(cfg: Config, fmt: str, topic_override: str | None = None) -> Script:
    """Generate a Script for fmt in {'short', 'long'}.

    Shorts enforce the narration word budget HERE, where a retry costs one
    LLM call — an over-length script that slips through renders a full video
    only for the quality gate to block its upload at the very end. Bounds
    reuse the quality gate's duration ratios so the two stay in agreement.
    """
    prompt = _build_prompt(cfg, fmt, topic_override=topic_override)
    target = int(cfg.get("script.shorts_target_seconds", 20) * 150 / 60)
    lo = int(target * float(cfg.get("quality.min_duration_ratio", 0.75)))
    hi = int(target * float(cfg.get("quality.max_duration_ratio", 1.35)))

    word_count = None
    for _attempt in range(3):
        attempt_prompt = prompt
        if word_count is not None:
            print(f"  ! narration was {word_count} words (need {lo}-{hi}) — regenerating.")
            attempt_prompt += (
                f"\n\nIMPORTANT: your previous draft was {word_count} words. "
                f"The total narration across all segments MUST be between {lo} "
                f"and {hi} words. Rewrite the script to fit that budget."
            )
        raw, provider, fallback_used = _call_with_routing(
            cfg, attempt_prompt, _parse_script_response
        )
        data, segments = _parse_script_response(raw)
        word_count = len(" ".join(s.narration for s in segments).split())
        if fmt == "short" and not (lo <= word_count <= hi):
            continue
        return Script(
            title=data["title"].strip()[:100],
            description=data["description"].strip(),
            tags=_dedupe_tags(data.get("tags", []), cfg),
            segments=segments,
            topic=data.get("topic", data["title"]).strip(),
            provider=provider,
            fallback_used=fallback_used,
        )

    raise RuntimeError(
        f"script narration stayed out of bounds after 3 attempts: last draft was "
        f"{word_count} words (need {lo}-{hi} for a "
        f"{cfg.get('script.shorts_target_seconds', 20)}s short)"
    )
