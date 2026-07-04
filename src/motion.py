"""Programmatic 'motion graphics' background for captions-first channels.

No stock footage, no mascot, no AI cost. A branded gradient with slowly drifting,
gently swaying cartoon medical motifs (crosses, pills, rings) — an explainer /
kinetic-typography backdrop for big centered captions.

Rendered at half resolution and upscaled by ffmpeg for speed; deterministic seed
so the look is consistent across every video.
"""
from __future__ import annotations

import math
import random
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .config import Config


def _gradient(w: int, h: int, top, bot) -> Image.Image:
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for c in range(3):
        arr[:, :, c] = np.linspace(top[c], bot[c], h).astype(np.uint8)[:, None]
    return Image.fromarray(arr, "RGB").convert("RGBA")


def _sprite(kind: str, size: int, color) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    s = size
    if kind == "cross":  # medical plus
        t = s * 0.30
        d.rounded_rectangle([s/2 - t/2, s*0.14, s/2 + t/2, s*0.86], radius=t*0.3, fill=color)
        d.rounded_rectangle([s*0.14, s/2 - t/2, s*0.86, s/2 + t/2], radius=t*0.3, fill=color)
    elif kind == "pill":
        d.rounded_rectangle([s*0.08, s*0.36, s*0.92, s*0.64], radius=s*0.16, fill=color)
        d.line([(s*0.5, s*0.36), (s*0.5, s*0.64)], fill=(255, 255, 255, color[3]), width=max(2, int(s*0.03)))
    else:  # ring
        d.ellipse([s*0.12, s*0.12, s*0.88, s*0.88], outline=color, width=max(3, int(s*0.09)))
    return im


def render_motion_video(cfg: Config, duration: float, fmt: str, out_path: Path) -> Path:
    node = "video.short" if fmt == "short" else "video.long"
    W = cfg.get(f"{node}.width")
    H = cfg.get(f"{node}.height")
    fps = cfg.get(f"{node}.fps", 30)
    top = cfg.get("video.motion.bg_top", [14, 74, 92])       # teal (medical)
    bot = cfg.get("video.motion.bg_bottom", [6, 20, 32])

    rw, rh = W // 2, H // 2  # render at half-res, upscale in ffmpeg
    base = _gradient(rw, rh, top, bot)

    cfg_accents = cfg.get("video.motion.accents")
    if cfg_accents:
        accents = [tuple(a) for a in cfg_accents]
    else:
        accents = [(255, 255, 255, 55), (120, 220, 200, 70),
                   (230, 110, 110, 60), (110, 165, 235, 70)]
    kinds = ["cross", "ring", "pill", "cross", "ring"]
    rnd = random.Random(7)  # deterministic → consistent brand look
    elems = []
    for _ in range(16):
        size = rnd.randint(int(rw * 0.10), int(rw * 0.26))
        spr = _sprite(rnd.choice(kinds), size, rnd.choice(accents))
        elems.append({
            "spr": spr, "size": size,
            "x0": rnd.uniform(0, rw), "y0": rnd.uniform(0, rh),
            "vy": rnd.uniform(rh * 0.02, rh * 0.06),
            "A": rnd.uniform(6, 28), "f": rnd.uniform(0.04, 0.16),
            "ph": rnd.uniform(0, 6.28),
        })

    frames = max(1, int(duration * fps) + 2)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-f", "rawvideo", "-pixel_format", "rgb24",
         "-video_size", f"{rw}x{rh}", "-framerate", str(fps), "-i", "-",
         "-vf", f"scale={W}:{H}:flags=bilinear",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         "-an", str(out_path.resolve())],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    span = rh + int(rw * 0.3)
    try:
        for fidx in range(frames):
            t = fidx / fps
            fr = base.copy()
            for e in elems:
                y = (e["y0"] - e["vy"] * t) % span - e["size"] / 2
                x = e["x0"] + e["A"] * math.sin(2 * math.pi * e["f"] * t + e["ph"])
                fr.alpha_composite(e["spr"], (int(x - e["size"] / 2), int(y)))
            proc.stdin.write(fr.convert("RGB").tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode(errors="ignore") if proc.stderr else ""
    if proc.wait() != 0:
        raise RuntimeError("motion-graphics encode failed:\n" + err[-1500:])
    return out_path
