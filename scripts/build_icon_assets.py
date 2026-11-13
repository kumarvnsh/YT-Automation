#!/usr/bin/env python3
"""Rebuild assets/icons/game-icons.json.gz from an unpacked game-icons/icons checkout.

Usage: python scripts/build_icon_assets.py /path/to/game-icons-checkout
Never runs during publishing. Icons are CC BY 3.0 (a few CC0) by the game-icons.net
authors; only path data is kept, so no script, link or image can enter a scene.
"""
import gzip
import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / 'assets/icons/game-icons.json.gz'
# Prolific authors first, so their version wins a duplicate name.
PRIORITY = ('lorc', 'delapouite')
SAFE_D = re.compile(r'^[MmLlHhVvCcSsQqTtAaZz0-9.,\se+-]+$')


def main(src: Path) -> None:
    files = sorted(src.glob('*/*.svg'), key=lambda f: (f.parent.name not in PRIORITY,
                                                       PRIORITY.index(f.parent.name) if f.parent.name in PRIORITY else 0,
                                                       f.parent.name, f.name))
    icons = {}
    for f in files:
        name = f.stem
        svg = f.read_text(encoding='utf-8')
        if name in icons or 'viewBox="0 0 512 512"' not in svg:
            continue
        paths = re.findall(r'<path\b[^>]*\bd="([^"]+)"', svg)
        paths = [d for d in paths if d.strip() != 'M0 0h512v512H0z']  # drop the black tile
        if not paths or not all(SAFE_D.match(d) for d in paths):
            continue
        icons[name] = {'d': ' '.join(paths), 'by': f.parent.name}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(gzip.compress(json.dumps(icons, separators=(',', ':'), sort_keys=True).encode(), 9))
    print(f'{len(icons)} icons -> {OUT} ({OUT.stat().st_size // 1024} KB)')


if __name__ == '__main__':
    main(Path(sys.argv[1]))
