"""Pinned, provenance-tracked present-day establishing geography."""
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / 'assets' / 'maps'


def sanitize_svg(text: str) -> str:
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('SVG declarations are not allowed')
    root = ET.fromstring(text)
    for element in root.iter():
        if element.tag.split('}')[-1] not in {'svg', 'g', 'path', 'title', 'desc'}:
            raise ValueError('SVG contains an unsupported element')
        for key, value in element.attrib.items():
            if key.split('}')[-1].lower().startswith('on') or key.split('}')[-1] in {'href', 'style'} or 'url(' in value.lower():
                raise ValueError('SVG contains active or external content')
    return text


def load_map(map_id: str) -> dict:
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    asset = manifest['assets'].get(map_id)
    if not asset:
        raise ValueError(f'No vetted geographic asset: {map_id}')
    text = (ROOT / asset['file']).read_text()
    if hashlib.sha256(text.encode()).hexdigest() != asset['sha256']:
        raise ValueError(f'Map checksum mismatch: {map_id}')
    return {**asset, 'map_id': map_id, 'svg': sanitize_svg(text)}


def required_maps(research: dict) -> list[dict]:
    ids = []
    for location in research.get('locations', []):
        if isinstance(location, dict) and location.get('map_id'):
            ids.append(location['map_id'])
    if any(name in ids for name in ('minnesota', 'washington')) and 'united_states' not in ids:
        ids.insert(0, 'united_states')
    return [load_map(map_id) for map_id in dict.fromkeys(ids)]
