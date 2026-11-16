"""Local neural voice for the dashboard assistant. Nothing leaves the laptop.

Engine: Kokoro-82M via kokoro-onnx (CPU, ~1.4 s load, faster than realtime). Model files live
outside the repo (default D:\\histold\\voice, override with HISTOLD_VOICE_DIR):
  kokoro-v1.0.onnx, voices-v1.0.bin  (github.com/thewh1teagle/kokoro-onnx, release model-files-v1.0)
Only synthetic Kokoro voices are offered; no voice cloning of real people.
"""
from __future__ import annotations

import io
import os
import re
import threading
import wave
from pathlib import Path

VOICES = {  # id -> label shown in settings
    'bm_george': 'George · British, deep and composed',
    'bm_lewis': 'Lewis · British, warm',
    'bm_daniel': 'Daniel · British, crisp',
    'bm_fable': 'Fable · British, lighter',
    'am_michael': 'Michael · American (narration voice)',
    'bf_emma': 'Emma · British female',
}
DEFAULT_VOICE = os.environ.get('HISTOLD_VOICE', 'bm_george')
MAX_CHARS = 600


def clean(text: str) -> str:
    """Speakable text: no markdown, IDs or URLs; units read naturally."""
    text = re.sub(r'https?://\S+', '', text or '')
    text = re.sub(r'[*_`#|>]+', ' ', text)
    text = re.sub(r'\b\d{12,}\b', '', text)  # platform post IDs
    text = re.sub(r'(\d)k\b', r'\1 thousand', text)
    text = text.replace('%', ' percent').replace('·', ',').replace('→', ' to ').replace('–', ' to ')
    return re.sub(r'\s+', ' ', text).strip()[:MAX_CHARS]


class LocalVoice:
    def __init__(self, model_dir: Path | None = None):
        self.dir = Path(model_dir or os.environ.get('HISTOLD_VOICE_DIR', r'D:\histold\voice'))
        self.lock = threading.Lock()
        self.model = None
        self.error: str | None = None

    def status(self) -> dict:
        ok = (self.dir / 'kokoro-v1.0.onnx').exists() and (self.dir / 'voices-v1.0.bin').exists()
        try:
            import kokoro_onnx  # noqa: F401
        except ImportError:
            ok, self.error = False, 'kokoro-onnx is not installed in the dashboard venv'
        if not ok and not self.error:
            self.error = f'model files missing in {self.dir}'
        return {'available': ok, 'engine': 'kokoro', 'voices': VOICES, 'default': DEFAULT_VOICE,
                'error': None if ok else self.error}

    def _load(self):
        if self.model is None:
            from kokoro_onnx import Kokoro
            self.model = Kokoro(str(self.dir / 'kokoro-v1.0.onnx'), str(self.dir / 'voices-v1.0.bin'))
        return self.model

    def synth(self, text: str, voice: str | None = None, speed: float = 1.0) -> bytes:
        text = clean(text)
        if not text:
            raise ValueError('nothing to say')
        voice = voice if voice in VOICES else DEFAULT_VOICE
        speed = min(max(float(speed or 1.0), 0.7), 1.4)
        lang = 'en-gb' if voice.startswith('b') else 'en-us'
        import numpy as np
        with self.lock:
            samples, rate = self._load().create(text, voice=voice, speed=speed, lang=lang)
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype('<i2')
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())
        return buf.getvalue()
