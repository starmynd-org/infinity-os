"""Actual task UI and receipt producer over synthetic transitions; no listener/store."""
from pathlib import Path
import json
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from web.tests.check_attention_ux import Scene
from web.tests.test_shared_receipts import receipt_functions, acceptance, withdrawal


def main():
    from playwright.sync_api import sync_playwright
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=False)
    scene = Scene()
    client = scene.app.test_client()
    state, _ = receipt_functions()
    posts = 0

    def route(r):
        nonlocal posts
        u = urlsplit(r.request.url)
        if r.request.method == 'POST' and u.path == '/queue/act':
            with scene.app.test_request_context(u.path, method='POST', data=r.request.post_data_buffer,
                                               content_type=r.request.header_value('content-type')) as context:
                form = dict(context.request.form)
            posts += 1
            typed = 'work_item:' + form['id']
            if form['action'] == 'unaccept':
                if typed not in scene.accepted:
                    r.fulfill(status=400, content_type='application/json', body=json.dumps(dict(
                        ok=False, kind='refused', error='Synthetic acceptance already withdrawn.')))
                    return
                scene.accepted.remove(typed)
                result = withdrawal(form['id'])
            elif form['action'] == 'accept_work':
                scene.accepted.add(typed)
                result = acceptance(form['id'])
            else:
                raise AssertionError('unexpected synthetic verb')
            state['_push_receipt'](result)
            r.fulfill(status=200, content_type='application/json', body=json.dumps(dict(ok=True, **result)))
        elif r.request.method == 'GET' and u.path.startswith(('/task/', '/static/')):
            scene.task_receipts = state['_receipts']()
            response = client.get(u.path)
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
                scene.accepted = {'work_item:0429'}
                state['_RECEIPTS'].clear()
                state['_push_receipt'](acceptance('0429'))
                page.goto('http://attention.test/task/0429')
                assert page.locator('html').get_attribute('data-theme') == theme
                report = page.locator('#completion-report').inner_text()
                shared = page.locator('[data-task-receipts]')
                assert shared.locator('form:has(input[value="unaccept"])').count() == 1
                shared.locator('form:has(input[value="unaccept"]) button').click()
                page.locator('[data-task-acceptance-state]').wait_for()
                assert shared.locator('form:has(input[value="unaccept"])').count() == 0
                assert 'Acceptance withdrawn; this inverse has been used.' in shared.inner_text()
                assert len(state['_RECEIPTS']) == 2
                assert state['_RECEIPTS'][0]['text'] == '0429 ACCEPTED by fixture-human'
                page.locator('[data-task-state] form:has(input[value="accept_work"]) button').click()
                page.locator('[data-toggle="un-0429"]').wait_for()
                assert shared.locator('form:has(input[value="unaccept"])').count() == 1
                assert len(state['_RECEIPTS']) == 3
                assert shared.locator('.receipt').count() == 3
                assert 'Acceptance withdrawn; this inverse has been used.' in shared.inner_text()
                assert page.locator('#completion-report').inner_text() == report
                assert page.locator('[data-task-feedback]').evaluate('el=>el===document.activeElement')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                shared.evaluate('el => window.scrollTo(0, window.scrollY + el.getBoundingClientRect().top - 200)')
                shared.screenshot(path=str(out / f'shared-{width}-{theme}.png'))
                # A refused stale inverse must not spend a display receipt as though it succeeded.
                scene.accepted.clear()
                shared.locator('form:has(input[value="unaccept"]) button').click()
                page.get_by_text('Synthetic acceptance already withdrawn.', exact=True).wait_for()
                assert len(state['_RECEIPTS']) == 3
                assert shared.locator('form:has(input[value="unaccept"])').count() == 1
                assert shared.locator('button').evaluate('el=>el===document.activeElement')
                cells.append(dict(width=width, height=height, theme=theme, history_preserved=True,
                    consumed_inverse_absent=True, later_acceptance_current_inverses=1,
                    refusal_not_consumed=True, no_overflow=True))
        browser.close()
    result = dict(cells=cells, synthetic_posts=posts, database_connections_permitted=0,
        actual_store_mutations=0, scope='actual templates and extracted actual receipt functions; synthetic transitions')
    (out / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
