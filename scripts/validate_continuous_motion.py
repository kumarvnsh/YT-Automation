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
