"""Text-to-speech voiceover generation.

Default provider is edge-tts (free, no API key) which also gives word-level
timing — ideal for animated captions. ElevenLabs / OpenAI are optional upgrades
(no word timing, so captions fall back to evenly distributed timing).

Returns a `Voiceover` with the audio file path, total duration, and a list of
WordTiming(word, start, end) in seconds.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from .config import Config, env


@dataclass
class WordTiming:
    word: str
    start: float  # seconds
    end: float    # seconds


@dataclass
class Voiceover:
    audio_path: Path
    duration: float
    words: list[WordTiming]


def _ffprobe_duration(path: Path) -> float:
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


# --------------------------------------------------------------------------- #
# edge-tts (free, with word boundaries)
# --------------------------------------------------------------------------- #
async def _edge_synthesize(text: str, voice: str, rate: str, out_path: Path) -> list[WordTiming]:
    import edge_tts

    # edge-tts >= 7 defaults to SentenceBoundary; force word-level for captions.
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, boundary="WordBoundary")
    words: list[WordTiming] = []
    with open(out_path, "wb") as fh:
        async for chunk in communicate.stream():
            ctype = chunk["type"]
            if ctype == "audio":
                fh.write(chunk["data"])
            elif ctype in ("WordBoundary", "SentenceBoundary"):
                start = chunk["offset"] / 1e7  # 100-ns ticks -> seconds
                dur = chunk["duration"] / 1e7
                words.append(WordTiming(chunk["text"], start, start + dur))
    return words


def _synthesize_edge(cfg: Config, text: str, out_path: Path) -> Voiceover:
    voice = cfg.get("tts.edge_voice", "en-US-GuyNeural")
    rate = cfg.get("tts.edge_rate", "+0%")
    words = asyncio.run(_edge_synthesize(text, voice, rate, out_path))
    duration = _ffprobe_duration(out_path)
    # If boundaries came back sentence-level (or empty), refine to word-level
    # by evenly distributing within the spoken span so captions stay readable.
    if len(words) < max(2, len(text.split()) // 6):
        words = _refine_to_words(text, words, duration)
    return Voiceover(out_path, duration, words)


def _refine_to_words(text: str, coarse: list[WordTiming], duration: float) -> list[WordTiming]:
    """Turn sparse/sentence boundaries into per-word timings via even spacing."""
    all_words = text.split()
    if not all_words:
        return coarse
    if not coarse:
        return _estimate_word_timings(text, duration)
    # Spread each coarse chunk's window across the words it likely contains.
    # Simpler robust approach: even split across the full duration.
    return _estimate_word_timings(text, duration)


# --------------------------------------------------------------------------- #
# ElevenLabs (premium, optional)
# --------------------------------------------------------------------------- #
def _synthesize_elevenlabs(cfg: Config, text: str, out_path: Path) -> Voiceover:
    import requests

    api_key = env("ELEVENLABS_API_KEY")
    voice_id = cfg.get("tts.elevenlabs_voice_id")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    resp = requests.post(
        url,
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json={"text": text, "model_id": "eleven_multilingual_v2"},
        timeout=120,
    )
    resp.raise_for_status()
    out_path.write_bytes(resp.content)
    duration = _ffprobe_duration(out_path)
    return Voiceover(out_path, duration, _estimate_word_timings(text, duration))


# --------------------------------------------------------------------------- #
# OpenAI TTS (optional)
# --------------------------------------------------------------------------- #
def _synthesize_openai(cfg: Config, text: str, out_path: Path) -> Voiceover:
    from openai import OpenAI

    client = OpenAI(api_key=env("OPENAI_API_KEY"))
    voice = cfg.get("tts.openai_voice", "onyx")
    with client.audio.speech.with_streaming_response.create(
        model="tts-1", voice=voice, input=text,
    ) as response:
        response.stream_to_file(str(out_path))
    duration = _ffprobe_duration(out_path)
    return Voiceover(out_path, duration, _estimate_word_timings(text, duration))


def _estimate_word_timings(text: str, duration: float) -> list[WordTiming]:
    """Even split fallback when the provider gives no word boundaries."""
    words = text.split()
    if not words:
        return []
    per = duration / len(words)
    return [WordTiming(w, i * per, (i + 1) * per) for i, w in enumerate(words)]


def synthesize(cfg: Config, text: str, out_path: Path) -> Voiceover:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    provider = cfg.get("tts.provider", "edge")
    if provider == "edge":
        return _synthesize_edge(cfg, text, out_path)
    if provider == "elevenlabs":
        return _synthesize_elevenlabs(cfg, text, out_path)
    if provider == "openai":
        return _synthesize_openai(cfg, text, out_path)
    raise ValueError(f"Unknown tts.provider: {provider}")
