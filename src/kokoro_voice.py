"""CPU-only Kokoro worker; install requirements-kokoro.txt separately."""
from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile


def token_timings(tokens, offset):
    words = []
    for token in tokens or []:
        if not any(c.isalnum() for c in token.text):
            continue
        start, end = token.start_ts, token.end_ts
        if start is None or end is None or not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end:
            raise ValueError(f"Missing or invalid Kokoro timing for {token.text!r}")
        words.append({"word": token.text, "start": offset + start, "end": offset + end})
    return words


def generate(request, result_path):
    import numpy as np
    import soundfile as sf
    import torch
    from kokoro import KPipeline

    torch.set_num_threads(2)
    pipeline = KPipeline(lang_code="a", device="cpu", repo_id="hexgrad/Kokoro-82M")
    chunks, words, samples = [], [], 0
    for chunk in pipeline(request["text"], voice=request["voice"], speed=float(request["speed"])):
        audio = chunk.audio.detach().cpu().numpy()
        if not audio.size or not np.isfinite(audio).all():
            raise ValueError("Kokoro returned empty or invalid audio")
        words.extend(token_timings(chunk.tokens, samples / 24000))
        chunks.append(audio)
        samples += audio.size
    if not chunks or not words:
        raise ValueError("Kokoro returned no timed narration")
    output = Path(request["output"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="histold-kokoro-audio-") as scratch:
        wav = Path(scratch) / "voice.wav"
        sf.write(wav, np.concatenate(chunks), 24000)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
                        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "24000", str(output)], check=True)
    Path(result_path).write_text(json.dumps({"words": words}), encoding="utf-8")


if __name__ == "__main__":
    generate(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")), sys.argv[2])
