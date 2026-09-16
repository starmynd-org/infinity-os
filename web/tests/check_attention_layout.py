"""Phone/desktop geometry of actual Attention templates, using long synthetic titles."""
from dataclasses import replace
from pathlib import Path
import json
import sys
from urllib.parse import urlsplit
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from web.tests.check_attention_ux import Scene


def main():
    from playwright.sync_api import sync_playwright
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=False)
    scene = Scene()
    if '--intake-observation' in sys.argv[2:]:
        from web.tests.check_attention_ux import checks
        scene.intake_observation = checks.model._StoreAttentionPort.intake_observation
    scene.items = [replace(i, title='Review the completed integration and its supporting evidence across the shared workspace ' + i.item_id) for i in scene.items]
    client = scene.app.test_client()
    def route(r):
        u = urlsplit(r.request.url)
        if r.request.method == 'GET' and u.path.startswith(('/attention/', '/static/')):
            response = client.get(u.path + ('?' + u.query if u.query else ''))
            r.fulfill(status=response.status_code, content_type=response.content_type, body=response.data)
        else:
            r.abort()
    exe = sorted(Path.home().glob('.cache/ms-playwright/chromium-*/chrome-linux/chrome')) + sorted(Path.home().glob('.cache/ms-playwright/chromium-*/chrome-linux64/chrome'))
    cells = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, **({'executable_path': str(exe[-1])} if exe else {}))
        page = browser.new_page()
        page.route('**/*', route)
        for width, height in ((1440, 900), (375, 812)):
            page.set_viewport_size(dict(width=width, height=height))
            for theme in ('light', 'dark'):
                scene.theme = theme
                page.goto('http://attention.test/attention/')
                assert page.locator('html').get_attribute('data-theme') == theme
                assert page.locator('[data-queue-row]').count() == 7
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                inspect = page.locator('[data-queue-row]').first.get_by_role('link', name='Inspect', exact=True)
                rect = inspect.bounding_box()
                if width == 375:
                    assert page.locator('[data-queue-row]').first.locator('.att-compact-state').first.is_visible()
                    assert rect['y'] >= 0 and rect['y'] + rect['height'] <= height, 'first inspection is below initial viewport'
                else:
                    wrapped = page.locator('.att-table th, .att-table td[data-label="Inspector"] a, .att-act button').evaluate_all('''elements => elements.flatMap(el => {
                      const failures = []; const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
                      while (walker.nextNode()) { const node = walker.currentNode;
                        for (const match of node.textContent.matchAll(/[A-Za-z]+/g)) {
                          const r = document.createRange(); r.setStart(node, match.index); r.setEnd(node, match.index + match[0].length);
                          if (r.getClientRects().length > 1) failures.push(match[0]);
                        }
                      } return failures;
                    })''')
                    assert not wrapped, 'desktop words split across lines: ' + ', '.join(wrapped)
                page.screenshot(path=str(out / f'layout-{width}-{theme}.png'), full_page=True)
                if getattr(scene, 'intake_observation', None):
                    page.locator('[data-intake-observation] summary').click()
                    assert page.get_by_text('This is a recorded observation.', exact=False).is_visible()
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.screenshot(path=str(out / f'intake-{width}-{theme}.png'), full_page=True)
                # Both visible sort controls name the field they actually order.
                original_ranks = {i.item_id: i.rank for i in scene.items}
                for descending in (False, True):
                    title_link = page.locator('.att-sort a' if width == 375 else '.att-table th a').filter(has_text='Title')
                    title_link.click()
                    assert 'sort=title' in page.url
                    assert ('dir=desc' if descending else 'dir=asc') in page.url
                    assert 'by title' in page.locator('section[data-attention-queue]').inner_text()
                    expected = sorted(scene.items, key=lambda i: i.title.casefold(), reverse=descending)[:7]
                    assert page.locator('[data-queue-row]').evaluate_all('els=>els.map(el=>el.dataset.queueRow)') == [i.item_id for i in expected]
                    for item in expected:
                        row = page.locator('[data-queue-row="' + item.item_id + '"]')
                        assert '#' + str(original_ranks[item.item_id]) + ' ' in row.locator('[data-label="Rank / status"]').inner_text()
                    assert page.get_by_role('link', name='Source / type', exact=True).count() == 0
                cells.append(dict(width=width, height=height, theme=theme, shown=7, first_inspect_bottom=round(rect['y'] + rect['height'], 1), no_overflow=True))
        browser.close()
    result = dict(cells=cells, database_connections_permitted=0, transitions_executed=0)
    (out / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
