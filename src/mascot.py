"""Histold owl mascot: amplitude-driven talking animation from user art.

The user supplies 4 mouth states (1.png closed → 4.png widest open) as square
transparent PNGs in the mascot folder. We map the voiceover's per-frame loudness
to one of those 4 states to create a talking effect, composite the owl over a
branded background (bottom-right, slightly raised), and encode a silent video.
Captions + audio are added afterward by video_builder.finalize().
"""
from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .config import Config


# --------------------------------------------------------------------------- #
# background
# --------------------------------------------------------------------------- #
def background(width: int, height: int) -> Image.Image:
    """Vertical navy gradient with a soft gold glow (brand backdrop)."""
    top, bot = (32, 48, 90), (10, 16, 32)
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    for c in range(3):
        col = np.linspace(top[c], bot[c], height).astype(np.uint8)
        arr[:, :, c] = col[:, None]
    bg = Image.fromarray(arr, "RGB").convert("RGBA")
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gx, gy = width // 2, int(height * 0.5)
    for i, rad in enumerate(range(int(width * 0.6), 0, -50)):
        a = max(0, 22 - i * 2)
        gd.ellipse([gx - rad, gy - rad, gx + rad, gy + rad], fill=(232, 184, 75, a))
    bg.alpha_composite(glow)
    return bg


# --------------------------------------------------------------------------- #
# mascot art
# --------------------------------------------------------------------------- #
def render_background(cfg: Config, duration: float, fmt: str, out_path: Path) -> Path:
    """Render a branded gradient background video with a slow zoom (captions-only mode)."""
    import tempfile, os, subprocess

    node = "video.short" if fmt == "short" else "video.long"
    W = cfg.get(f"{node}.width")
    H = cfg.get(f"{node}.height")
    fps = cfg.get(f"{node}.fps", 30)

    fd, png = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    background(W, H).convert("RGB").save(png)
    frames = max(1, int(duration * fps) + 2)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Gentle Ken-Burns zoom so it isn't a dead-static frame.
    vf = (f"scale={W*2}:{H*2},"
          f"zoompan=z='min(zoom+0.00015,1.08)':d={frames}:s={W}x{H}:fps={fps},"
          f"setsar=1")
    subprocess.run(
        ["ffmpeg", "-y", "-loop", "1", "-i", png, "-t", f"{duration:.3f}",
         "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         "-r", str(fps), str(out_path.resolve())],
        check=True, capture_output=True,
    )
    Path(png).unlink(missing_ok=True)
    return out_path


def load_states(mascot_dir: Path) -> list[Image.Image]:
    """Load 1.png..4.png (closed→open) as RGBA. Raises if missing."""
    states = []
    for n in (1, 2, 3, 4):
        p = mascot_dir / f"{n}.png"
        if not p.exists():
            raise FileNotFoundError(f"Mascot state image not found: {p}")
        states.append(Image.open(p).convert("RGBA"))
    return states


def _placed(state_img: Image.Image, W: int, H: int, scale: float,
            margin_right: int, margin_bottom: int) -> tuple[int, int, Image.Image]:
    ow = int(W * scale)
    oh = int(state_img.height * ow / state_img.width)
    resized = state_img.resize((ow, oh), Image.LANCZOS)
    x = W - ow - margin_right
    y = H - oh - margin_bottom
    return x, y, resized


# --------------------------------------------------------------------------- #
# amplitude → mouth state
# --------------------------------------------------------------------------- #
def amplitude_states(audio_path: Path, fps: int, n_frames: int, levels: int = 4) -> list[int]:
    """Return a mouth-state index (0..levels-1) per frame from audio loudness."""
    import tempfile, os

    fd, wav_name = tempfile.mkstemp(suffix=".wav")  # system temp, avoids mounted-FS quirks
    os.close(fd)
    wav = Path(wav_name)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(audio_path), "-ac", "1", "-ar", "16000",
         "-f", "wav", str(wav)],
        check=True, capture_output=True,
    )
    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        raw = w.readframes(w.getnframes())
    wav.unlink(missing_ok=True)

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return [0] * n_frames
    per = max(1, sr // fps)
    rms = np.zeros(n_frames, dtype=np.float32)
    for i in range(n_frames):
        seg = samples[i * per:(i + 1) * per]
        if seg.size:
            rms[i] = np.sqrt(np.mean(seg * seg))
    # Normalize against a robust peak; map to levels with a silence floor.
    peak = np.percentile(rms[rms > 0], 92) if np.any(rms > 0) else 1.0
    peak = max(peak, 1.0)
    norm = np.clip(rms / peak, 0, 1)
    floor = 0.08
    states = []
    for v in norm:
        if v < floor:
            states.append(0)
        else:
            states.append(int(np.clip(round(v * (levels - 1)), 1, levels - 1)))
    return states


# --------------------------------------------------------------------------- #
# render silent talking video
# --------------------------------------------------------------------------- #
def render_talking_video(
    cfg: Config, voiceover_path: Path, duration: float, fmt: str,
    out_path: Path, mascot_dir: Path,
) -> Path:
    node = "video.short" if fmt == "short" else "video.long"
    W = cfg.get(f"{node}.width")
    H = cfg.get(f"{node}.height")
    fps = cfg.get(f"{node}.fps", 30)
    scale = cfg.get("video.mascot.scale", 0.48)
    mr = cfg.get("video.mascot.margin_right", 30)
    mb = cfg.get("video.mascot.margin_bottom", 230)

    bg = background(W, H)
    states_img = load_states(mascot_dir)

    # Precompute the 4 full composited frames as raw RGB bytes (only 4 unique frames).
    frame_bytes = []
    for st in states_img:
        x, y, owl = _placed(st, W, H, scale, mr, mb)
        frame = bg.copy()
        frame.alpha_composite(owl, (x, y))
        frame_bytes.append(frame.convert("RGB").tobytes())

    n_frames = max(1, int(duration * fps) + 2)
    seq = amplitude_states(voiceover_path, fps, n_frames)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-f", "rawvideo", "-pixel_format", "rgb24",
         "-video_size", f"{W}x{H}", "-framerate", str(fps), "-i", "-",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         "-an", str(out_path.resolve())],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    try:
        for s in seq:
            proc.stdin.write(frame_bytes[s])
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode(errors="ignore") if proc.stderr else ""
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError("mascot ffmpeg encode failed:\n" + err[-1500:])
    return out_path


def render_overlay(
    cfg: Config, voiceover_path: Path, duration: float, fmt: str,
    out_path: Path, mascot_dir: Path,
) -> Path:
    """Render the talking owl as a TRANSPARENT overlay video (qtrle .mov) for
    compositing on top of b-roll. Same lip-sync, transparent everywhere but the owl.
    """
    node = "video.short" if fmt == "short" else "video.long"
    W = cfg.get(f"{node}.width")
    H = cfg.get(f"{node}.height")
    fps = cfg.get(f"{node}.fps", 30)
    scale = cfg.get("video.mascot.scale", 0.48)
    mr = cfg.get("video.mascot.margin_right", 30)
    mb = cfg.get("video.mascot.margin_bottom", 230)

    states_img = load_states(mascot_dir)
    frame_bytes = []
    for st in states_img:
        x, y, owl = _placed(st, W, H, scale, mr, mb)
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        canvas.alpha_composite(owl, (x, y))
        frame_bytes.append(canvas.tobytes())  # RGBA

    n_frames = max(1, int(duration * fps) + 2)
    seq = amplitude_states(voiceover_path, fps, n_frames)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-f", "rawvideo", "-pixel_format", "rgba",
         "-video_size", f"{W}x{H}", "-framerate", str(fps), "-i", "-",
         "-c:v", "qtrle", "-an", str(out_path.resolve())],  # qtrle keeps alpha
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    try:
        for s in seq:
            proc.stdin.write(frame_bytes[s])
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode(errors="ignore") if proc.stderr else ""
    if proc.wait() != 0:
        raise RuntimeError("mascot overlay encode failed:\n" + err[-1500:])
    return out_path
