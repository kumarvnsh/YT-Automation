"""Local forced alignment of known narration text (no ASR, no API).

Uses torchaudio's English wav2vec2 CTC model (WAV2VEC2_ASR_BASE_960H, the model
WhisperX uses for English) to place every scripted word on the measured audio.
Because the transcript is the script itself, coverage cannot drop on a misheard
name; low per-word confidence is reported and blocks the result instead.

Runs as an isolated worker under the Kokoro interpreter (torch + torchaudio):
    python -m src.forced_align request.json result.json
Pure text/grouping helpers below import nothing heavy so tests run without torch.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

from .alignment import _number

SAMPLE_RATE = 16000
PAD_SECONDS = 0.5
# Calibrated on real narration: correct text has a median per-word CTC posterior
# near 0.98, wrong text near 0.4; words that were never spoken score ~0.
MIN_WORD_SCORE = 0.05
MIN_MEDIAN_SCORE = 0.6
MAX_WEAK_WORDS = 1          # or 5% of words, whichever is larger
_ORDINAL = re.compile(r"st|nd|rd|th")


def _year(n: int) -> list[str] | None:
    """Year-style reading ("nineteen eighteen", "nineteen oh five", "eighteen hundred")."""
    if not (1100 <= n <= 2099) or 2000 <= n <= 2009:
        return None
    hi, lo = divmod(n, 100)
    if lo == 0:
        return _number(hi) + ["hundred"]
    return _number(hi) + (["oh"] + _number(lo) if lo < 10 else _number(lo))


def _number_options(token: str, grouped: bool) -> list[list[str]]:
    dollar, digits, decimals, suffix = re.fullmatch(r"(\$?)(\d+)(?:\.(\d+))?(%|[a-z]*)", token).groups()
    n = int(digits)
    readings = [] if grouped or decimals or dollar else [r for r in [_year(n)] if r]
    readings.append(_number(n))
    options = []
    for reading in readings:
        parts = list(reading)
        if decimals:
            parts += ["point"] + [_number(int(d))[0] for d in decimals]
        if suffix == "%":
            parts.append("percent")
        elif suffix:
            # 1920s -> "twentys", 19th -> "nineteenth": letters stay on the number.
            parts[-1] += "th" if _ORDINAL.fullmatch(suffix) else suffix
        if dollar:
            parts.append("dollars")
        options.append(parts)
    return options


def word_options(word: str) -> list[list[str]]:
    """Alternative letter sequences the narrator may have spoken for one display word.

    Accents are stripped and only a-z / inner apostrophes kept. Numbers get every
    plausible reading (year-style first, then cardinal); the audio picks one.
    """
    text = unicodedata.normalize("NFKD", word.replace("’", "'"))
    text = text.encode("ascii", "ignore").decode().lower()
    grouped = bool(re.search(r"\d,\d", text))
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    options = [[]]
    for token in re.findall(r"\$?\d+(?:\.\d+)?(?:%|[a-z]+)?|[a-z]+(?:'[a-z]+)*", text):
        choices = _number_options(token, grouped) if re.search(r"\d", token) else [[token]]
        options = [o + c for o in options for c in choices]
    return [o for o in options if o]


def alignment_words(text: str) -> list[tuple[int, str, list[list[str]]]]:
    """(display_index, display_word, reading options) for every audible word."""
    result = []
    for index, word in enumerate(text.split()):
        options = word_options(word)
        if options:
            result.append((index, word, options))
    return result


def alignment_units(text: str, choice: dict | None = None, words=None) -> list[tuple[int, str, str]]:
    """(display_index, display_word, letters) per spoken part, using reading
    `choice[display_index]` (default: the first option)."""
    units = []
    for index, word, options in words or alignment_words(text):
        for part in options[(choice or {}).get(index, 0)]:
            units.append((index, word, part))
    return units


def group_words(units, spans, seconds_per_frame, offset=0.0):
    """Merge per-unit token spans back into display words and check confidence.

    `spans[k]` is the list of (start_frame, end_frame, score) for unit k's letters;
    `offset` is leading padding (seconds) to remove from every timestamp.
    """
    words, weak = [], 0
    for index in dict.fromkeys(u[0] for u in units):
        members = [k for k, u in enumerate(units) if u[0] == index]
        letters = [s for k in members for s in spans[k]]
        start = max(0.0, letters[0][0] * seconds_per_frame - offset)
        end = letters[-1][1] * seconds_per_frame - offset
        score = sum(s[2] for s in letters) / len(letters)
        if not (math.isfinite(start) and math.isfinite(end) and end > start):
            raise ValueError(f"Invalid aligned span for {units[members[0]][1]!r}")
        weak += score < MIN_WORD_SCORE
        words.append({"word": units[members[0]][1], "start": round(start, 3),
                      "end": round(end, 3), "score": round(score, 3)})
    if not words:
        raise ValueError("No alignable words in narration")
    median = sorted(w["score"] for w in words)[len(words) // 2]
    if median < MIN_MEDIAN_SCORE:
        raise ValueError(f"Narration audio does not match the script (median word score {median:.2f})")
    if weak > max(MAX_WEAK_WORDS, 0.05 * len(words)):
        missing = ", ".join(w["word"] for w in words if w["score"] < MIN_WORD_SCORE)
        raise ValueError(f"{weak} scripted words were not heard in the audio: {missing}")
    return words


def _load_audio(path: str):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
                          "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    audio = np.frombuffer(raw, dtype=np.float32)
    if not audio.size or not np.isfinite(audio).all():
        raise ValueError("Empty or invalid narration audio")
    return audio


def align(request: dict) -> dict:
    import numpy as np
    import torch
    import torchaudio

    torch.set_num_threads(int(request.get("threads", 2)))
    words = alignment_words(request["text"])
    if not words:
        raise ValueError("No alignable words in narration")
    bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
    vocab = {c: i for i, c in enumerate(bundle.get_labels())}

    # Silence padding gives the CTC model context for the first and last words,
    # which otherwise get crushed into a few low-confidence frames.
    pad = np.zeros(int(PAD_SECONDS * SAMPLE_RATE), dtype=np.float32)
    waveform = torch.from_numpy(np.concatenate([pad, _load_audio(request["audio"]), pad])).unsqueeze(0)
    model = bundle.get_model().eval()
    with torch.inference_mode():
        emission, _ = model(waveform)
        emission = torch.log_softmax(emission, dim=-1)

    def run(choice):
        units = alignment_units("", choice, words)
        targets, owners = [], []
        for k, (_, _, letters) in enumerate(units):
            if k:
                targets.append(vocab["|"])
                owners.append(None)
            for char in letters.upper():
                targets.append(vocab[char])
                owners.append(k)
        if emission.size(1) < len(targets):
            raise ValueError("Narration audio is too short for its text")
        aligned, scores = torchaudio.functional.forced_align(
            emission, torch.tensor([targets], dtype=torch.int32), blank=0)
        token_spans = torchaudio.functional.merge_tokens(aligned[0], scores[0].exp())
        if len(token_spans) != len(targets):
            raise ValueError("Forced alignment lost tokens")
        spans = [[] for _ in units]
        for owner, span in zip(owners, token_spans):
            if owner is not None:
                spans[owner].append((span.start, span.end, float(span.score)))
        return units, spans, float(scores.sum())  # whole-path log-likelihood

    # Pick each ambiguous number's reading by which one the audio supports best.
    choice = {}
    units, spans, best = run(choice)
    for index, _, options in words:
        for option in range(1, len(options)):
            trial = {**choice, index: option}
            result = run(trial)
            if result[2] > best:
                choice, (units, spans, best) = trial, result
    seconds_per_frame = waveform.size(1) / emission.size(1) / SAMPLE_RATE
    return {"words": group_words(units, spans, seconds_per_frame, offset=PAD_SECONDS),
            "model": "torchaudio.WAV2VEC2_ASR_BASE_960H",
            "readings": {w: " ".join(o[choice.get(i, 0)]) for i, w, o in words if len(o) > 1}}


if __name__ == "__main__":
    result = align(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    Path(sys.argv[2]).write_text(json.dumps(result), encoding="utf-8")
