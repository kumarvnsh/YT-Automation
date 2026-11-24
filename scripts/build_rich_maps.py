"""Build rich establishing-map assets from public-domain Natural Earth data (run once, offline).

For every vetted map in assets/maps/manifest.json this cuts, for a padded view around the map's extent:
- the Natural Earth II shaded-relief raster (terrain and land cover) as a compact JPEG;
- neighbouring countries, rivers, lakes and a graticule as SVG path data;
- the subject outline (country, continent, region or US state) and its focus point,
all in the same equirectangular projection the vetted outlines use (6 units per degree), so pins line up.

Sources (all public domain, https://www.naturalearthdata.com/about/terms-of-use/):
  NE2_50M_SR_W.tif (Natural Earth II, 1:50m, shaded relief + water)
  ne_50m_admin_0_countries / ne_50m_admin_1_states_provinces / ne_50m_rivers_lake_centerlines / ne_50m_lakes
  (natural-earth-vector v5.1.2 GeoJSON)

Usage: python scripts/build_rich_maps.py D:\\YT-Automation-env\\mapdata
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MAPS = ROOT / 'assets' / 'maps'
OUT = MAPS / 'rich'
UNITS = 6  # per degree, same as the vetted outlines
PX = 30    # raster pixels per degree in NE2_50M (1/30 degree cells)

SUBJECTS = {
    'united_states': {'iso': ['USA']}, 'canada': {'iso': ['CAN']}, 'india': {'iso': ['IND']},
    'japan': {'iso': ['JPN']}, 'russia': {'iso': ['RUS']}, 'egypt': {'iso': ['EGY']}, 'kenya': {'iso': ['KEN']},
    'nigeria': {'iso': ['NGA']}, 'south_africa': {'iso': ['ZAF']}, 'ethiopia': {'iso': ['ETH']},
    'ghana': {'iso': ['GHA']}, 'iran': {'iso': ['IRN']}, 'iraq': {'iso': ['IRQ']}, 'saudi_arabia': {'iso': ['SAU']},
    'turkey': {'iso': ['TUR']}, 'china': {'iso': ['CHN']}, 'africa': {'continent': 'Africa'},
    'middle_east': {'iso': ['EGY', 'ISR', 'PSE', 'JOR', 'LBN', 'SYR', 'IRQ', 'IRN', 'SAU', 'KWT', 'BHR', 'QAT',
                            'ARE', 'OMN', 'YEM', 'TUR', 'CYP']},
    'minnesota': {'state': 'Minnesota'}, 'washington': {'state': 'Washington'},
}


def _load(folder: Path, name: str) -> list[dict]:
    return json.loads((folder / 'vector' / f'{name}.geojson').read_text(encoding='utf-8'))['features']


def _rings(geometry: dict) -> list[list]:
    if not geometry:
        return []
    kind, coords = geometry['type'], geometry['coordinates']
    if kind == 'Polygon':
        return coords
    if kind == 'MultiPolygon':
        return [ring for poly in coords for ring in poly]
    if kind == 'LineString':
        return [coords]
    if kind == 'MultiLineString':
        return coords
    return []


class View:
    def __init__(self, extent):
        west, south, east, north = extent
        span = max(east - west, north - south)
        pad = max(2.5, span * .14)
        self.west, self.east = west - pad, east + pad
        self.south, self.north = max(-85.0, south - pad), min(85.0, north + pad)
        self.wrap = self.east > 180  # Russia's vetted extent runs past the antimeridian
        self.width = (self.east - self.west) * UNITS
        self.height = (self.north - self.south) * UNITS

    def lon(self, lon: float) -> float:
        return lon + 360 if self.wrap and lon < 0 else lon

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        return (self.lon(lon) - self.west) * UNITS, (self.north - lat) * UNITS

    def touches(self, ring: list) -> bool:
        lons = [self.lon(p[0]) for p in ring]
        lats = [p[1] for p in ring]
        return not (max(lons) < self.west or min(lons) > self.east or max(lats) < self.south or min(lats) > self.north)

    def path(self, rings: list[list], closed: bool = True, step: float = .7) -> str:
        parts = []
        for ring in rings:
            if len(ring) < 2 or not self.touches(ring):
                continue
            points, last = [], None
            for lon, lat, *_ in ring:
                x, y = self.xy(lon, lat)
                # Clip to just outside the frame: parts beyond it are never seen, and the outline's box stays on screen.
                x, y = min(max(x, -1.0), self.width + 1), min(max(y, -1.0), self.height + 1)
                if last is None or abs(x - last[0]) + abs(y - last[1]) >= step:
                    points.append((x, y))
                    last = (x, y)
            if len(points) < 2:
                continue
            body = 'L'.join(f'{x:.1f},{y:.1f}' for x, y in points)
            parts.append('M' + body + ('Z' if closed else ''))
        return ''.join(parts)


def _relief(image: Image.Image, view: View, dest: Path) -> None:
    def crop(west, east):
        box = (int((west + 180) * PX), int((90 - view.north) * PX), int((east + 180) * PX), int((90 - view.south) * PX))
        return image.crop(box)
    if view.wrap:
        left, right = crop(view.west, 180), crop(-180, view.east - 360)
        piece = Image.new('RGB', (left.width + right.width, left.height))
        piece.paste(left, (0, 0))
        piece.paste(right, (left.width, 0))
    else:
        piece = crop(max(-180, view.west), min(180, view.east))
    target = 1100
    if piece.width > target:
        piece = piece.resize((target, round(piece.height * target / piece.width)), Image.LANCZOS)
    piece.convert('RGB').save(dest, 'JPEG', quality=84, optimize=True)


def build(folder: Path) -> None:
    Image.MAX_IMAGE_PIXELS = None
    raster = Image.open(folder / 'ne2' / 'unz' / 'NE2_50M_SR_W' / 'NE2_50M_SR_W.tif').convert('RGB')
    countries = _load(folder, 'ne_50m_admin_0_countries')
    states = _load(folder, 'ne_50m_admin_1_states_provinces')
    rivers = _load(folder, 'ne_50m_rivers_lake_centerlines')
    lakes = _load(folder, 'ne_50m_lakes')
    manifest = json.loads((MAPS / 'manifest.json').read_text(encoding='utf-8'))['assets']
    OUT.mkdir(parents=True, exist_ok=True)
    index = {}
    for map_id, asset in sorted(manifest.items()):
        spec = SUBJECTS.get(map_id)
        if not spec:
            continue
        view = View(asset['extent'])
        span = view.east - view.west
        if 'state' in spec:
            subject = [f for f in states if f['properties'].get('name') == spec['state'] and f['properties'].get('adm0_a3') == 'USA']
        elif 'continent' in spec:
            subject = [f for f in countries if f['properties'].get('CONTINENT') == spec['continent']]
        else:
            subject = [f for f in countries if f['properties'].get('ADM0_A3') in spec['iso']]
        subject_rings = [r for f in subject for r in _rings(f['geometry'])]
        main = max((r for r in subject_rings if view.touches(r)), key=len)
        lons, lats = [view.lon(p[0]) for p in main], [p[1] for p in main]
        focus = view.xy((min(lons) + max(lons)) / 2 if not view.wrap else (min(lons) + max(lons)) / 2 - (360 if (min(lons) + max(lons)) / 2 > 180 else 0),
                        (min(lats) + max(lats)) / 2)
        others = [f for f in countries if f not in subject]
        river_rank = 4 if span > 60 else 6 if span > 25 else 9
        lake_rank = 3 if span > 60 else 6 if span > 25 else 9
        step = 10 if span < 30 else 15 if span < 80 else 20
        grid = []
        for lon in range(int(view.west // step * step), int(view.east) + 1, step):
            if view.west < lon < view.east:
                x, _ = view.xy(lon if lon <= 180 else lon - 360, 0)
                grid.append(f'M{x:.1f},0V{view.height:.1f}')
        for lat in range(int(view.south // step * step), int(view.north) + 1, step):
            if view.south < lat < view.north:
                _, y = view.xy(view.west, lat)
                grid.append(f'M0,{y:.1f}H{view.width:.1f}')
        image = OUT / f'{map_id}_relief.jpg'
        _relief(raster, view, image)
        record = {
            'map_id': map_id, 'width': round(view.width, 1), 'height': round(view.height, 1),
            'extent': [round(view.west, 4), round(view.south, 4), round(view.east, 4), round(view.north, 4)],
            'relief': image.name, 'relief_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
            'focus': [round(focus[0], 1), round(focus[1], 1)],
            'subject': view.path(subject_rings, step=.5),
            'neighbours': view.path([r for f in others for r in _rings(f['geometry'])]),
            'rivers': view.path([r for f in rivers if (f['properties'].get('scalerank') or 99) <= river_rank
                                 for r in _rings(f['geometry'])], closed=False),
            'lakes': view.path([r for f in lakes if (f['properties'].get('scalerank') or 99) <= lake_rank
                                for r in _rings(f['geometry'])]),
            'graticule': ''.join(grid),
            'source': 'Natural Earth (public domain): NE2_50M_SR_W, ne_50m admin 0/1, rivers, lakes; v5.1.2',
        }
        (OUT / f'{map_id}.json').write_text(json.dumps(record, separators=(',', ':')), encoding='utf-8')
        index[map_id] = {'json': f'{map_id}.json', 'json_sha256': hashlib.sha256((OUT / f'{map_id}.json').read_bytes()).hexdigest()}
        print(f'{map_id}: {record["width"]:.0f}x{record["height"]:.0f}, json {(OUT / f"{map_id}.json").stat().st_size // 1024} KB, '
              f'relief {image.stat().st_size // 1024} KB')
    (OUT / 'index.json').write_text(json.dumps(index, indent=1), encoding='utf-8')


if __name__ == '__main__':
    build(Path(sys.argv[1]))
