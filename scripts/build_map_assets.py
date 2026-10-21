#!/usr/bin/env python3
"""Rebuild pinned public-domain establishing outlines; never runs during publishing."""
import hashlib
import json
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent.parent / 'assets/maps'
BASE = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/'


def clip(ring):
    points = [(x, y) for x, y, *_ in ring]
    for axis, boundary, sign in [(0, -130, 1), (0, -50, -1), (1, 24, 1), (1, 85, -1)]:
        output = []
        if not points:
            break
        a = points[-1]
        for b in points:
            ain, bin = sign*(a[axis]-boundary) >= 0, sign*(b[axis]-boundary) >= 0
            if ain != bin:
                fraction = (boundary-a[axis])/(b[axis]-a[axis])
                output.append(tuple(a[i]+fraction*(b[i]-a[i]) for i in range(2)))
            if bin:
                output.append(b)
            a = b
        points = output
    return points


def main():
    ROOT.mkdir(exist_ok=True)
    layers = {}
    for kind, filename in [('countries', 'ne_110m_admin_0_countries'), ('states', 'ne_50m_admin_1_states_provinces')]:
        response = requests.get(BASE + filename + '.geojson', timeout=60)
        response.raise_for_status()
        layers[kind] = (filename, response.json())
    manifest_path = ROOT / 'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {'schema_version': 1, 'assets': {}}
    for name, label, kind, key, value in [
        ('united_states', 'United States', 'countries', 'ADMIN', 'United States of America'),
        ('canada', 'Canada', 'countries', 'ADMIN', 'Canada'),
        ('minnesota', 'Minnesota', 'states', 'name', 'Minnesota'),
        ('washington', 'Washington', 'states', 'name', 'Washington')]:
        filename, data = layers[kind]
        feature = next(f for f in data['features'] if f['properties'].get(key) == value)
        polygons = feature['geometry']['coordinates']
        if feature['geometry']['type'] == 'Polygon':
            polygons = [polygons]
        paths = []
        for polygon in polygons:
            # Skip dateline-crossing offshore rings; clip continental geometry to the display.
            if any(abs(a[0]-b[0]) > 180 for a, b in zip(polygon[0], polygon[0][1:])):
                continue
            ring = clip(polygon[0])
            if len(ring) >= 3:
                paths.append('M'+'L'.join(f'{(x+130)*6:.2f},{(85-y)*6:.2f}' for x, y in ring)+'Z')
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 366" role="img" aria-label="{label}"><path fill="currentColor" fill-rule="evenodd" d="'+' '.join(paths)+'"/></svg>'
        (ROOT / f'{name}.svg').write_text(svg)
        manifest['assets'][name] = {
            'file': f'{name}.svg', 'label': label, 'source_url': BASE + filename + '.geojson',
            'license': 'Public domain', 'license_url': 'https://www.naturalearthdata.com/about/terms-of-use/',
            'version': '5.1.2', 'projection': 'Equirectangular EPSG:4326, scaled 6 px/degree',
            'extent': [-130, 24, -50, 85],
            'scope': 'Present-day continental establishing outline; clipped at display extent; not survey or historical boundary data',
            'sha256': hashlib.sha256(svg.encode()).hexdigest()}
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')


if __name__ == '__main__':
    main()
