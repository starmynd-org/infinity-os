"""P3-02 round trip: inspector, task record, back to the inspector, back to the row.

Synthetic read port over the actual templates; no act is posted, no database is reached.
Screenshots form labelled light/dark pairs.
"""
from dataclasses import replace
from pathlib import Path
import json
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from web.tests.check_attention_ux import Scene, checks


def main():
    from playwright.sync_api import sync_playwright
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=False)
    scene = Scene()
    fixture = checks.Admission()
    fixture.setUp()
    try:
        original = checks.model.attention_port().queue(limit=0)
    finally:
        fixture.stack.close()
    human = next(i for i in original.items if i.kind == 'human')
    assert human.native_options, 'fixture human row offers no Mark done; no denominator'
    human = replace(human, item_id='work_item:0001', title='Renew the office insurance before the broker deadline', rank=9)
    scene.items.append(human)      # ninth row: page two, offset 7, so the context is really carried
    client = scene.app.test_client()

    def route(r):
        u = urlsplit(r.request.url)
        if r.request.method == 'GET' and u.path.startswith(('/attention/', '/task/', '/static/')):
            response = client.get(u.path + ('?' + u.query if u.query else ''))
            r.fulfill(status=response.status_code, content_type=response.content_type, body=response.data)
        else:
            r.abort()

    exe = sorted(Path.home().glob('.cache/ms-playwright/chromium-*/chrome-linux/chrome'))
    exe += sorted(Path.home().glob('.cache/ms-playwright/chromium-*/chrome-linux64/chrome'))
    cells = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, **({'executable_path': str(exe[-1])} if exe else {}))
        page = browser.new_page()
        page.route('**/*', route)
        for width, height in ((1440, 900), (375, 812)):
            page.set_viewport_size(dict(width=width, height=height))
            for theme in ('light', 'dark'):
                scene.theme = theme
                page.goto('http://attention.test/attention/?offset=7&limit=7')
                assert page.locator('[data-queue-row="work_item:0001"]').count() == 1
                page.locator('[data-queue-row="work_item:0001"]').get_by_role('link', name='Inspect', exact=True).click()
                assert page.locator('html').get_attribute('data-theme') == theme
                assert '/attention/work_item:0001' in page.url and 'offset=7' in page.url
                record = page.locator('[data-task-record]')
                assert record.count() == 1, 'no task record link on the unfinished task inspector'
                href = record.get_attribute('href')
                assert href.startswith('/task/0001?') and 'from_attention=1' in href and 'offset=7' in href and 'limit=7' in href
                assert '#' not in href.split('?')[0]
                assert page.locator('[data-completion-report]').count() == 0, 'an unfinished task must not offer a completed report'
                page.screenshot(path=str(out / f'inspector-{width}-{theme}.png'), full_page=True)
                record.click()
                assert page.locator('html').get_attribute('data-theme') == theme
                assert page.url.split('attention.test')[1].startswith('/task/0001?')
                assert page.get_by_text('The work order, as posted', exact=True).count() == 1
                assert page.get_by_text('Synthetic work order', exact=True).count() == 1
                assert page.locator('#completion-report').count() == 1
                back = page.get_by_role('link', name='Return to Attention review', exact=True)
                assert back.count() == 1
                assert 'offset=7' in back.get_attribute('href') and back.get_attribute('href').startswith('/attention/work_item:0001?')
                page.screenshot(path=str(out / f'task-{width}-{theme}.png'), full_page=True)
                back.click()
                assert '/attention/work_item:0001' in page.url and 'offset=7' in page.url and 'limit=7' in page.url
                assert page.locator('[data-task-record]').count() == 1
                page.get_by_role('link', name='Return to queue', exact=True).click()
                assert 'offset=7' in page.url and page.url.endswith('#attention-work_item:0001')
                row = page.locator('[data-queue-row="work_item:0001"]')
                assert row.count() == 1
                assert page.evaluate('document.activeElement && document.activeElement.dataset.queueRow') == 'work_item:0001'
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                # A question or recommendation inspector carries no task record link.
                rec = next(i for i in original.items if i.kind == 'recommendation')
                scene.items.append(replace(rec, item_id=rec.item_id, rank=10))
                page.goto('http://attention.test/attention/' + rec.item_id)
                assert page.get_by_text('Argument against', exact=True).count() == 1, 'the control must reach the rendered inspector'
                assert page.locator('[data-task-record]').count() == 0
                scene.items.pop()
                cells.append(dict(width=width, height=height, theme=theme, round_trip=True, context_preserved=True,
                                  row_focused=True, negative_control_recommendation=True))
        browser.close()
    result = dict(cells=cells, database_connections_permitted=0, transitions_executed=0)
    (out / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
