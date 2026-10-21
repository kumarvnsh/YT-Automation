"""Text-to-speech voiceover generation.

Histold uses CPU Kokoro with native token timing and Edge fallback.
ElevenLabs / OpenAI are optional providers.
Estimated timings remain available for legacy jobs; strict production requires
measured boundaries, optionally obtained through configured transcription.

Returns a `Voiceover` with the audio file path, total duration, and a list of
WordTiming(word, start, end) in seconds.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
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
    timing_source: str = "estimated"


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
            elif ctype == "WordBoundary":
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
    timing_source = "native"
    if len(words) < max(1, len(text.split()) // 6):
        timing_source = "estimated"
        words = _refine_to_words(text, words, duration)
    return Voiceover(out_path, duration, words, timing_source)


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


def _synthesize_kokoro(cfg: Config, text: str, out_path: Path) -> Voiceover:
    """Keep model dependencies and CPU work in a bounded, isolated worker."""
    with tempfile.TemporaryDirectory(prefix="histold-kokoro-") as scratch:
        request = Path(scratch) / "request.json"
        result = Path(scratch) / "result.json"
        request.write_text(json.dumps({
            "text": text, "output": str(out_path.resolve()),
            "voice": cfg.get("tts.kokoro_voice", "am_michael"),
            "speed": cfg.get("tts.kokoro_speed", 0.95),
        }), encoding="utf-8")
        subprocess.run([
            os.environ.get("KOKORO_PYTHON", sys.executable), "-m", "src.kokoro_voice",
            str(request), str(result),
        ], cwd=Path(__file__).resolve().parent.parent, check=True,
            timeout=float(cfg.get("tts.kokoro_timeout", 300)))
        data = json.loads(result.read_text(encoding="utf-8"))
    return Voiceover(out_path, _ffprobe_duration(out_path),
                     [WordTiming(**w) for w in data["words"]], "native")


def synthesize(cfg: Config, text: str, out_path: Path) -> Voiceover:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    provider = cfg.get("tts.provider", "edge")
    if provider == "kokoro":
        try:
            voiceover = _synthesize_kokoro(cfg, text, out_path)
            from .alignment import build_storyboard
            build_storyboard([{"narration": text}], voiceover.words,
                             voiceover.duration, 30, voiceover.timing_source, strict=True)
        except Exception as exc:
            print(f"  ! Kokoro unavailable or invalid ({type(exc).__name__}); using Edge voiceover.")
            voiceover = _synthesize_edge(cfg, text, out_path)
    elif provider == "edge":
        voiceover = _synthesize_edge(cfg, text, out_path)
    elif provider == "elevenlabs":
        voiceover = _synthesize_elevenlabs(cfg, text, out_path)
    elif provider == "openai":
        voiceover = _synthesize_openai(cfg, text, out_path)
    else:
        raise ValueError(f"Unknown tts.provider: {provider}")
    strict = cfg.get("production.strict_timing", False)
    if voiceover.timing_source == "estimated" and cfg.get("tts.alignment_provider", "") == "openai":
        voiceover = align_voiceover(voiceover)
    if strict:
        from .alignment import build_storyboard
        try:
            build_storyboard([{"narration": text}], voiceover.words, voiceover.duration,
                             30, voiceover.timing_source, strict=True)
        except ValueError:
            if cfg.get("tts.alignment_provider", "") != "openai" or voiceover.timing_source == "forced_aligned":
                raise
            voiceover = align_voiceover(voiceover)
            build_storyboard([{"narration": text}], voiceover.words, voiceover.duration,
                             30, voiceover.timing_source, strict=True)
    return voiceover


def align_voiceover(voiceover: Voiceover) -> Voiceover:
    """Obtain measured ASR word timestamps; text agreement is validated downstream.

    OpenAI documents word timestamps for whisper-1 + verbose_json:
    https://developers.openai.com/api/docs/guides/speech-to-text
    This is transcription alignment, not proof that every scripted word was spoken.
    """
    from openai import OpenAI
    client = OpenAI(api_key=env("OPENAI_API_KEY"))
    with voiceover.audio_path.open("rb") as audio:
        transcript = client.audio.transcriptions.create(
            model="whisper-1", file=audio, response_format="verbose_json",
            timestamp_granularities=["word"],
        )
    words = [WordTiming(w.word, w.start, w.end) for w in (transcript.words or [])]
    if not words:
        raise ValueError("OpenAI alignment returned no measured word timestamps")
    return Voiceover(voiceover.audio_path, voiceover.duration, words, "forced_aligned")
