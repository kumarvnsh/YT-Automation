#!/usr/bin/env python3
"""Rebuild present-day world establishing maps from pinned Natural Earth geometry.

Preserves existing North America assets. Run manually, never during publishing.
"""
import hashlib
import json
from pathlib import Path
from xml.sax.saxutils import escape
import requests

ROOT = Path(__file__).resolve().parent.parent / 'assets/maps'
SOURCE = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_110m_admin_0_countries.geojson'
MIDDLE_EAST = ['Bahrain', 'Cyprus', 'Egypt', 'Iran', 'Iraq', 'Israel', 'Jordan', 'Kuwait', 'Lebanon', 'Oman', 'Palestine', 'Qatar', 'Saudi Arabia', 'Syria', 'Turkey', 'United Arab Emirates', 'Yemen']
COUNTRIES = {'india': 'India', 'japan': 'Japan', 'russia': 'Russia', 'egypt': 'Egypt', 'kenya': 'Kenya', 'nigeria': 'Nigeria', 'south_africa': 'South Africa', 'ethiopia': 'Ethiopia', 'ghana': 'Ghana', 'iran': 'Iran', 'iraq': 'Iraq', 'saudi_arabia': 'Saudi Arabia', 'turkey': 'Turkey'}


def main():
    response = requests.get(SOURCE, timeout=60)
    response.raise_for_status()
    data = response.json()
    digest = hashlib.sha256(response.content).hexdigest()
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    selections = {key: [f for f in data['features'] if f['properties']['ADMIN'] == label] for key, label in COUNTRIES.items()}
    selections['africa'] = [f for f in data['features'] if f['properties']['CONTINENT'] == 'Africa']
    selections['middle_east'] = [f for f in data['features'] if f['properties']['ADMIN'] in MIDDLE_EAST]
    for key, features in selections.items():
        assert features, key
        rings = []
        for feature in features:
            geometry = feature['geometry']
            assert geometry['type'] in ('Polygon', 'MultiPolygon')
            polygons = [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
            for polygon in polygons:
                for ring in polygon:
                    # Shift Russia's far-eastern longitudes onto a continuous 0–360 sheet.
                    rings.append([(x + 360 if key == 'russia' and x < 0 else x, y) for x, y, *_ in ring])
        xs = [x for ring in rings for x, _ in ring]
        ys = [y for ring in rings for _, y in ring]
        left, bottom, right, top = min(xs)-1, min(ys)-1, max(xs)+1, max(ys)+1
        width, height = (right-left)*6, (top-bottom)*6
        paths = ['M'+'L'.join(f'{(x-left)*6:.2f},{(top-y)*6:.2f}' for x,y in ring)+'Z' for ring in rings]
        label = COUNTRIES.get(key, {'africa': 'Africa', 'middle_east': 'Middle East'}.get(key))
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.2f} {height:.2f}" role="img" aria-label="{escape(label)}"><title>{escape(label)} — present-day establishing geography</title><path fill="currentColor" fill-rule="evenodd" d="'+ ' '.join(paths)+'"/></svg>'
        (ROOT / f'{key}.svg').write_text(svg)
        manifest['assets'][key] = {
            'file': f'{key}.svg', 'label': label, 'source_url': SOURCE,
            'source_sha256': digest, 'feature_names': sorted(f['properties']['ADMIN'] for f in features),
            'license': 'Public domain', 'license_url': 'https://www.naturalearthdata.com/about/terms-of-use/',
            'version': '5.1.2', 'projection': 'Equirectangular EPSG:4326, scaled 6 px/degree' + ('; negative longitudes shifted +360 for dateline continuity' if key == 'russia' else ''),
            'extent': [left, bottom, right, top],
            'scope': 'Present-day establishing geography using Natural Earth v5.1.2 de facto boundary conventions; not historical, legal, sovereignty or survey evidence.',
            'sha256': hashlib.sha256(svg.encode()).hexdigest()}
        if key == 'middle_east':
            manifest['assets'][key]['regional_convention'] = 'Editorial selection: '+', '.join(MIDDLE_EAST)+'. Includes Egypt, Cyprus and Turkey; excludes North Africa west of Egypt and South Caucasus. No universally agreed regional boundary is implied.'
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')


if __name__ == '__main__':
    main()
