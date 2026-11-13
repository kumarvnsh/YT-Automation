"""Licence-checked portraits of historical people from Wikipedia / Wikimedia Commons.

Research names the people in the story with exact English Wikipedia titles. For each one
that the narration actually mentions we accept a portrait only when Wikidata says it is a
human who has died (no living people's likeness), and Commons says the image is public
domain, CC0 or CC BY(-SA). Scenes request it with <histold-photo id="...">; anything that
fails a check is simply skipped and the scene uses an illustration instead.
"""
from __future__ import annotations

import base64
import re
from html import escape, unescape
from pathlib import Path
from urllib.parse import quote

import requests

UA = {'User-Agent': 'HistoldVideoBot/1.0 (automated history explainer; Wikimedia API client)'}
_OK_LICENSE = re.compile(r'^(public domain|pd\b|cc0|cc[ -]by(-sa)?[ -]\d)', re.I)


def slug(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')[:40]


def _get(url: str, **params) -> dict:
    r = requests.get(url, params=params or None, headers=UA, timeout=20)
    r.raise_for_status()
    return r.json()


def _year(claim) -> int | None:
    try:
        t = claim['mainsnak']['datavalue']['value']['time']  # "+1729-05-02T00:00:00Z"
        return int(t[:t.index('-', 1)])
    except (KeyError, TypeError, ValueError):
        return None


def mentioned(person: dict, narration: str) -> bool:
    """The narration names this person (full name or surname/regnal name)."""
    text = narration.lower()
    name = person.get('name', '').lower()
    words = [w for w in re.findall(r'[a-z]+', name) if len(w) >= 4 and w not in {'great', 'king', 'queen', 'emperor', 'saint'}]
    return bool(name) and (name in text or any(re.search(rf'\b{w}\b', text) for w in words))


def fetch_portrait(person: dict, dest_dir: Path) -> dict | None:
    """Download one vetted portrait; returns metadata or None when any check fails."""
    title = (person.get('wikipedia_title') or '').strip()
    if not title:
        return None
    summary = _get(f'https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(" ", "_"), safe="")}')
    if summary.get('type') == 'disambiguation' or not summary.get('wikibase_item'):
        return None
    qid = summary['wikibase_item']
    entity = _get(f'https://www.wikidata.org/wiki/Special:EntityData/{qid}.json')['entities'][qid]
    claims = entity.get('claims', {})
    human = any(c.get('mainsnak', {}).get('datavalue', {}).get('value', {}).get('id') == 'Q5' for c in claims.get('P31', []))
    died = _year((claims.get('P570') or [None])[0])
    if not human or died is None or not claims.get('P18'):
        return None  # only deceased humans with a curated portrait image
    born = _year((claims.get('P569') or [None])[0])
    filename = claims['P18'][0]['mainsnak']['datavalue']['value']
    pages = _get('https://commons.wikimedia.org/w/api.php', action='query', format='json',
                 titles=f'File:{filename}', prop='imageinfo', iiprop='url|extmetadata', iiurlwidth=600)
    info = next(iter(pages['query']['pages'].values()))['imageinfo'][0]
    meta = info.get('extmetadata', {})
    license_name = (meta.get('LicenseShortName') or {}).get('value', '')
    if not _OK_LICENSE.match(license_name.strip()):
        return None
    artist = re.sub(r'<[^>]+>', '', unescape((meta.get('Artist') or {}).get('value', 'Unknown'))).strip()[:80]
    image = requests.get(info['thumburl'], headers=UA, timeout=30)
    image.raise_for_status()
    if not image.headers.get('content-type', '').startswith('image/'):
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    pid = slug(person['name'])
    path = dest_dir / f'{pid}{Path(info["thumburl"]).suffix.lower() or ".jpg"}'
    path.write_bytes(image.content)
    era = lambda y: f'{-y} BC' if y < 0 else str(y)  # noqa: E731
    years = f'{era(born)}–{era(died)}' if born is not None else f'd. {era(died)}'
    return {'id': pid, 'name': person['name'], 'years': years,
            'file': str(path), 'mime': image.headers['content-type'].split(';')[0],
            'credit': f'Portrait of {person["name"]}: {artist or "Unknown"}, {license_name}, via Wikimedia Commons'}


def fetch_portraits(people: list[dict], narration: str, dest_dir: Path, limit: int = 3) -> list[dict]:
    """Portraits for people the narration mentions. Network or data failures skip that person."""
    found = []
    for person in people:
        if len(found) >= limit or not isinstance(person, dict) or not mentioned(person, narration):
            continue
        try:
            portrait = fetch_portrait(person, dest_dir)
        except Exception as exc:  # noqa: BLE001 - a portrait must never fail a render
            print(f'  ! portrait skipped for {person.get("name")}: {exc}')
            continue
        if portrait and portrait['id'] not in {p['id'] for p in found}:
            found.append(portrait)
            print(f'  ✓ portrait: {portrait["name"]} ({portrait["credit"]})')
    return found


def bind_photos(html: str, portraits: list[dict]) -> tuple[str, list[str]]:
    """Replace <histold-photo id> placeholders with an embedded framed portrait."""
    by_id, used = {p['id']: p for p in portraits}, []

    def swap(m: re.Match) -> str:
        attrs = m.group(1)
        pid = re.search(r'''\bid\s*=\s*['"]([a-z0-9_]+)['"]''', attrs)
        p = by_id.get(pid.group(1)) if pid else None
        if not p:
            return ''
        used.append(p['id'])
        cls = re.search(r'''\bclass\s*=\s*['"]([^'"]*)['"]''', attrs)
        style = re.search(r'''\bstyle\s*=\s*['"]([^'"]*)['"]''', attrs)
        data = base64.b64encode(Path(p['file']).read_bytes()).decode()
        return (f'<figure class="k-photo {escape(cls.group(1)) if cls else ""}" data-photo-id="{p["id"]}" '
                f'style="{escape(style.group(1)) if style else ""}"><div class="k-photo-frame">'
                f'<img alt="{escape(p["name"])}" src="data:{p["mime"]};base64,{data}"></div>'
                f'<figcaption>{escape(p["name"])}<small>{escape(p["years"])}</small></figcaption></figure>')

    return re.sub(r'<histold-photo\b([^>]*?)\s*(?:/>|>\s*</histold-photo>)', swap, html, flags=re.I), used
