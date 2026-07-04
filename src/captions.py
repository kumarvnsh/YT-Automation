"""Build an ASS subtitle file from word timings for burned-in captions.

We group words into short chunks (a few words at a time) so captions read like
the punchy, fast captions common on history/short-form channels.
"""
from __future__ import annotations

from pathlib import Path

from .config import Config
from .tts import WordTiming


def _fmt_ts(seconds: float) -> str:
    """ASS timestamp: H:MM:SS.cc (centiseconds)."""
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs == 100:
        cs = 0
        s += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _chunk(words: list[WordTiming], size: int) -> list[list[WordTiming]]:
    return [words[i : i + size] for i in range(0, len(words), size)]


def build_ass(
    cfg: Config,
    words: list[WordTiming],
    out_path: Path,
    fmt: str,
    words_per_caption: int = 4,
    position: str = "bottom",
) -> Path:
    """Write an .ass file with chunked captions. Returns the path.

    position: "bottom" (default) or "top" — used in mascot mode so captions sit
    in the upper area, clear of the owl in the bottom-right corner.
    """
    is_short = fmt == "short"
    node = "video.short" if is_short else "video.long"
    play_w = cfg.get(f"{node}.width", 1080 if is_short else 1920)
    play_h = cfg.get(f"{node}.height", 1920 if is_short else 1080)

    fontsize = (
        cfg.get("video.captions.fontsize_short", 96)
        if is_short
        else cfg.get("video.captions.fontsize_long", 56)
    )
    font = cfg.get("video.captions.font", "Arial")
    primary = cfg.get("video.captions.primary_color", "&H00FFFFFF")
    outline_col = cfg.get("video.captions.outline_color", "&H00000000")
    outline = cfg.get("video.captions.outline", 6)

    # Alignment: 2 = bottom-center, 8 = top-center, 5 = middle-center.
    # MarginV offsets from that edge, in real pixels (PlayResX/Y match the video).
    if position == "top":
        alignment = 8
        margin_v = cfg.get("video.captions.margin_top", 380)
    elif position == "center":
        alignment = 5
        margin_v = 0
    else:
        alignment = 2
        margin_v = cfg.get("video.captions.margin_bottom", 220)
    header = f"""[Script Info]
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {play_w}
PlayResY: {play_h}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{fontsize},{primary},{outline_col},&H00000000,-1,0,1,{outline},1,{alignment},40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = [header]
    if not words:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(header, encoding="utf-8")
        return out_path

    for group in _chunk(words, words_per_caption):
        start = group[0].start
        end = group[-1].end
        text = " ".join(w.word for w in group).replace("\n", " ").strip()
        # Escape ASS special chars minimally.
        text = text.replace("{", "(").replace("}", ")")
        lines.append(
            f"Dialogue: 0,{_fmt_ts(start)},{_fmt_ts(end)},Default,,0,0,0,,{text}\n"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(lines), encoding="utf-8")
    return out_path
