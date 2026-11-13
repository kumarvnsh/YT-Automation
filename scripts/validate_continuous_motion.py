"""Browser regression check; needs `playwright install chromium`, no API calls."""
from playwright.sync_api import sync_playwright
from src.claude_motion import apply_palette, house_scene, inspect_scene
from src.map_assets import load_map


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 540, 'height': 960})
        page.route('https://**/*', lambda route: route.abort())
        for ids in [('united_states',), ('russia', 'india'),
                    ('canada', 'washington', 'united_states'), ()]:
            html = apply_palette(house_scene(
                'Pueblo communities drove Spanish colonists out of Santa Fe in 1680.',
                [load_map(i) for i in ids], 5.6), 'navy_teal')
            report = inspect_scene(page, html, 5.6, 30, [], True)
            assert not report['errors'], (ids, report)
            print(f'PASS: animated recovery {ids or "diagram"}')
        # Regression: the reference example scene was rejected as static at real scene lengths.
        example = apply_palette(open('src/motion_example.html').read(), 'navy_teal')
        report = inspect_scene(page, example, 6.0, 30, [], False)
        assert not report['errors'], report
        print('PASS: reference example accepted at 6s')
        # Regression: a k-slam stamp was flagged as cut off while still entering at 2.4x scale.
        stamp = apply_palette('<html><head></head><body><div class="k-stage"><div class="k-title k-rise">Hermitage</div>'
                              '<div class="k-stamp k-slam" style="--d:1s;--r:-4deg">Philosopher\'s books</div>'
                              '</div></body></html>', 'navy_teal')
        report = inspect_scene(page, stamp, 3.0, 30, [{'time': 1.15}], False)
        assert not [e for e in report['errors'] if 'cut off' in e or 'overlaps' in e], report
        print('PASS: entering stamp judged at landed size')
        # Explainer layouts with library icons must lay out cleanly (no overlap or clipping).
        from src.icon_assets import bind_icons
        steps = ('<html><head></head><body><div class="k-stage"><div class="k-title k-wipe">Ice in the desert</div>'
                 '<div class="k-steps" style="--d:.3s">' + ''.join(
                     f'<div><histold-icon name="{n}"></histold-icon><div class="k-label">{n}</div></div>'
                     for n in ('ice-cube', 'mining', 'stone-wall')) +
                 '</div><div class="k-timeline" style="--d:2s"><div><b>1700</b><div class="k-label">Built</div></div>'
                 '<div><b>1900</b><div class="k-label">Fridges</div></div></div></div></body></html>')
        report = inspect_scene(page, apply_palette(bind_icons(steps)[0], 'navy_teal'), 4.0, 30, [], True)
        assert not [e for e in report['errors'] if 'overlap' in e or 'cut off' in e], report
        print('PASS: explainer layouts with icons')
        static = apply_palette('<html><head></head><body><div class="k-stage">'
                               '<div class="k-title">1680</div></div></body></html>', 'navy_teal')
        report = inspect_scene(page, static, 5.6, 30, [], False)
        assert 'static_opening' in report['errors'], report
        assert 'static_foreground' in report['errors'], report
        delayed = static.replace('class="k-title"', 'class="k-title k-rise" style="--d:1s"')
        assert 'static_opening' in inspect_scene(page, delayed, 5.6, 30, [], False)['errors']
        print('PASS: static and delayed openings rejected')
        browser.close()


if __name__ == '__main__':
    main()
