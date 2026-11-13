"""Vetted illustration library (game-icons.net, CC BY 3.0) for motion scenes.

Scenes ask for an icon with <histold-icon name="crown"></histold-icon>; bind_icons swaps in
the pinned path data, so Claude spends tokens on composition instead of hand-drawn SVG.
Only a short candidate list, matched to the script, is sent to the model.
"""
from __future__ import annotations

import gzip
import json
import re
from functools import lru_cache
from html import escape
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / 'assets' / 'icons' / 'game-icons.json.gz'
CREDIT = 'Illustrations: game-icons.net (CC BY 3.0)'
# Always offered: neutral documentary staples that fit most history stories.
DEFAULTS = ('scroll-unfurled', 'crown', 'castle', 'sailboat', 'crossed-swords', 'two-coins',
            'hourglass', 'person', 'family-house', 'quill-ink', 'world', 'open-book')
# Fantasy/game art clashes with a factual explainer.
_FANTASY = set('evil spell magic aura demon dragon wizard witch zombie monster alien orc goblin troll ghost '
               'undead vampire fairy elf unicorn potion rune arcane cursed haunted tentacle cthulhu imp '
               'skull death bleeding blood gore robot golem bolt'.split())
# Names that match everyday words but draw something else ("private" is a rank chevron).
_MISLEADING = {'private', 'private-first-class', 'corporal', 'sergeant', 'rank-1', 'rank-2', 'rank-3',
               'desert-eagle'}
_STOP = set('the and for with from that this was were are his her their they them into over after '
            'before about which while when where what who how why than then also only very more most '
            'have had has not but its one two first last years year began made make each every'.split())
_TAG = re.compile(r'<histold-icon\b([^>]*?)\s*(?:/>|>\s*</histold-icon>)', re.I)


@lru_cache(maxsize=1)
def load_icons() -> dict[str, dict]:
    return json.loads(gzip.decompress(PATH.read_bytes()))


def _stem(word: str) -> str:
    for suffix, repl in (('ves', 'f'), ('ies', 'y'), ('es', ''), ('s', ''), ('ed', ''), ('ing', '')):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[:-len(suffix)] + repl
    return word


def candidate_icons(texts: list[str], limit: int = 30) -> list[str]:
    """Icon names whose words appear in the script, best matches first, plus defaults."""
    words = {_stem(w) for t in texts for w in re.findall(r'[a-z]+', t.lower())
             if len(w) >= 3 and w not in _STOP}
    scored = []
    for name in load_icons():
        parts = name.split('-')
        if _FANTASY & set(parts) or name in _MISLEADING:
            continue
        # Whole-word match, or a long word inside a compound part ("shelf" in "bookshelf").
        hits = sum(1 for p in parts if _stem(p) in words or any(len(w) >= 5 and w in p for w in words))
        if hits:
            scored.append((-hits / len(parts), len(parts), name))
    picked = [name for *_, name in sorted(scored)][:limit]
    return picked + [d for d in DEFAULTS if d not in picked and d in load_icons()]


def bind_icons(html: str) -> tuple[str, list[str]]:
    """Replace icon placeholders with inline SVG; unknown names are dropped. Returns used names."""
    icons, used = load_icons(), []

    def swap(m: re.Match) -> str:
        attrs = m.group(1)
        name = re.search(r'''\bname\s*=\s*['"]([a-z0-9-]+)['"]''', attrs)
        if not name or name.group(1) not in icons:
            return ''
        used.append(name.group(1))
        cls = re.search(r'''\bclass\s*=\s*['"]([^'"]*)['"]''', attrs)
        style = re.search(r'''\bstyle\s*=\s*['"]([^'"]*)['"]''', attrs)
        return (f'<svg class="k-icon {escape(cls.group(1)) if cls else ""}" viewBox="0 0 512 512" '
                f'aria-hidden="true" style="{escape(style.group(1)) if style else ""}">'
                f'<path fill="currentColor" d="{icons[name.group(1)]["d"]}"/></svg>')

    return _TAG.sub(swap, html), used


if __name__ == '__main__':
    names = candidate_icons(["Russia's Hermitage library began with books from a French philosopher's shelves."])
    assert 'bookshelf' in names and not any('evil' in n for n in names), names
    html, used = bind_icons('<histold-icon name="crown" class="k-pop"></histold-icon><histold-icon name="nope"/>')
    assert used == ['crown'] and 'k-icon k-pop' in html and 'histold-icon' not in html, html
    print('ok', names[:10])
