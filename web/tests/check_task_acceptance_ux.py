"""Task recovery UI over actual templates and synthetic responses; no database acts."""
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
    client = scene.app.test_client()
    calls = []
    fail_read = False
    plant_task = False
    plant_normalization = False

    def route(r):
        nonlocal fail_read
        u = urlsplit(r.request.url)
        if plant_task and u.path == '/static/task-acceptance.js':
            r.fulfill(status=200, content_type='application/javascript', body='/* planted missing task refresh */')
            return
        if r.request.method == 'POST' and u.path == '/queue/act':
            with scene.app.test_request_context(u.path, method='POST', data=r.request.post_data_buffer,
                                               content_type=r.request.header_value('content-type')) as c:
                form = dict(c.request.form)
            calls.append(form['action'])
            typed = 'work_item:' + form['id']
            reason = ' '.join(form.get('text', '').split())
            if form['action'] == 'unaccept' and (len(reason) < 12 or typed not in scene.accepted):
                r.fulfill(status=400, content_type='application/json', body=json.dumps(dict(
                    ok=False, error='Use a meaningful reason of at least 12 characters.' if len(reason) < 12 else 'This acceptance is already withdrawn.')))
                return
            if form['action'] == 'unaccept':
                scene.accepted.remove(typed)
                verb, receipt = 'unaccept work', 'Synthetic withdrawal recorded'
            elif form['action'] == 'accept_work':
                scene.accepted.add(typed)
                verb, receipt = 'accept work', 'Synthetic acceptance recorded'
            else:
                raise AssertionError('unexpected synthetic action')
            r.fulfill(status=200, content_type='application/json', body=json.dumps(dict(ok=True, verb=verb, receipt=receipt)))
        elif r.request.method == 'GET' and u.path.startswith(('/task/', '/static/')):
            if fail_read and u.path.startswith('/task/'):
                fail_read = False
                r.fulfill(status=503, body='Synthetic read failure')
                return
            response = client.get(u.path + ('?' + u.query if u.query else ''))
            body = response.data
            if plant_normalization and u.path == '/static/console.js':
                body = body.replace(b"ta.dataset.normalize === 'words'", b'false')
            r.fulfill(status=response.status_code, content_type=response.content_type, body=body)
        else:
            r.abort()

    executables = sorted(Path.home().glob('.cache/ms-playwright/chromium-*/chrome-linux/chrome'))
    executables += sorted(Path.home().glob('.cache/ms-playwright/chromium-*/chrome-linux64/chrome'))
    cells = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, **({'executable_path': str(executables[-1])} if executables else {}))
        page = browser.new_page()
        page.route('**/*', route)
        plant_task = True
        scene.accepted = {'work_item:0429'}
        page.goto('http://attention.test/task/0429')
        page.locator('[data-toggle="un-0429"]').click()
        page.locator('form:has(input[value="unaccept"]) [data-gate]').fill('A valid reason for the planted omission')
        page.locator('form:has(input[value="unaccept"]) button[type="submit"]').click()
        page.get_by_text('Synthetic withdrawal recorded', exact=True).wait_for()
        assert not scene.accepted and page.locator('[data-task-acceptance-state]').count() == 0
        assert 'is accepted' in page.locator('[data-task-state]').inner_text(), 'old stale-state defect was not planted'
        plant_task = False
        plant_normalization = True
        scene.accepted = {'work_item:0429'}
        page.goto('http://attention.test/task/0429')
        page.locator('[data-toggle="un-0429"]').click()
        page.locator('form:has(input[value="unaccept"]) [data-gate]').fill('a' + ' ' * 10 + 'b')
        assert page.locator('form:has(input[value="unaccept"]) button[type="submit"]').is_enabled(), 'old normalization mismatch not planted'
        plant_normalization = False
        for width, height in ((1440, 900), (375, 812)):
            page.set_viewport_size(dict(width=width, height=height))
            for theme in ('light', 'dark'):
                scene.theme = theme
                scene.accepted = {'work_item:0429'}
                page.goto('http://attention.test/task/0429')
                assert page.locator('html').get_attribute('data-theme') == theme
                report = page.locator('#completion-report').inner_text()
                page.locator('[data-toggle="un-0429"]').click()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'task reason/report overflow'
                form = page.locator('form:has(input[name="action"][value="unaccept"])')
                field = form.locator('[data-gate]')
                for reason in ('', ' ' * 20, 'a' + ' ' * 10 + 'b', 'a' + '\u0085' * 10 + 'b', '🙂' * 6):
                    field.fill(reason)
                    assert form.locator('button[type="submit"]').is_disabled(), 'normalized-short reason enabled'
                # Exercise the server refusal even if a client bypasses the disabled button.
                # This route is a synthetic response sink, never the real write door.
                field.fill('too short')
                form.evaluate('el=>el.requestSubmit()')
                page.get_by_text('Use a meaningful reason of at least 12 characters.', exact=True).wait_for()
                assert field.input_value() == 'too short'
                assert field.evaluate('el=>el===document.activeElement')
                assert field.get_attribute('aria-describedby') == form.locator('[data-task-error]').get_attribute('id')
                assert form.locator('button[type="submit"]').is_disabled()
                assert scene.accepted == {'work_item:0429'}
                field.fill('A clear synthetic withdrawal reason')
                assert form.locator('button[type="submit"]').is_enabled()
                count = len(calls)
                form.locator('button[type="submit"]').click()
                try:
                    page.locator('[data-task-acceptance-state]').wait_for(timeout=5000)
                except Exception:
                    print(json.dumps(dict(instrument='task state did not refresh', calls=calls,
                        feedback=page.locator('[data-task-feedback]').inner_text(),
                        errors=page.locator('[data-task-error]').all_text_contents(),
                        state_hidden=page.locator('[data-task-state]').is_hidden(),
                        synthetic_accepted=sorted(scene.accepted))))
                    raise
                assert len(calls) == count + 1, 'one click made more than one POST'
                assert page.locator('form:has(input[value="accept_work"])').count() == 1
                assert page.locator('[data-toggle="un-0429"]').count() == 0
                assert 'is accepted' not in page.locator('[data-task-state]').inner_text()
                assert page.locator('[data-task-feedback]').evaluate('el=>el===document.activeElement')
                assert page.locator('[data-task-error]').count() == 0
                assert page.locator('#completion-report').inner_text() == report
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'restored task overflow'
                page.locator('[data-toggle="sb-0429"]').click()
                assert page.locator('#sb-0429').is_visible(), 'fresh Send back lost its handler'
                page.locator('[data-toggle="sb-0429"]').click()
                page.screenshot(path=str(out / f'task-{width}-{theme}.png'), full_page=True)
                page.reload()
                assert page.locator('[data-task-acceptance-state]').count() == 1
                assert page.locator('#completion-report').inner_text() == report

                # A stale inverse refuses, preserving the reason and usable focus.
                scene.accepted = {'work_item:0429'}
                page.reload()
                page.locator('[data-toggle="un-0429"]').click()
                field = page.locator('form:has(input[value="unaccept"]) [data-gate]')
                field.fill('A retained reason for the stale decision')
                scene.accepted.clear()
                page.locator('form:has(input[value="unaccept"]) button[type="submit"]').click()
                page.get_by_text('This acceptance is already withdrawn.', exact=True).wait_for()
                assert field.input_value() == 'A retained reason for the stale decision'
                assert field.evaluate('el=>el===document.activeElement')
                assert field.get_attribute('aria-describedby') == page.locator('[data-task-error]').get_attribute('id')
                assert page.get_by_label('Reason for withdrawing acceptance', exact=True).count() == 1
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'task refusal overflow'

                # The act succeeded but its read-back failed: hide stale controls.
                scene.accepted = {'work_item:0429'}
                scene.task_receipts = [dict(text='Synthetic previous acceptance', undo=dict(action='unaccept', id='0429', label='Unaccept'))]
                page.reload()
                page.locator('[data-toggle="un-0429"]').click()
                page.locator('[data-task-state] form:has(input[value="unaccept"]) [data-gate]').fill('A valid reason before a failed read')
                fail_read = True
                page.locator('[data-task-state] form:has(input[value="unaccept"]) button[type="submit"]').click()
                page.get_by_role('link', name='Reload task', exact=True).wait_for()
                assert page.locator('[data-task-state]').is_hidden()
                assert page.locator('[data-task-receipts] form:has(input[value="unaccept"])').count() == 1
                assert page.locator('[data-task-receipts]').is_hidden()
                assert page.locator('[data-task-feedback]').evaluate('el=>el===document.activeElement')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'task read failure overflow'
                assert not scene.accepted
                scene.task_receipts = []
                cells.append(dict(width=width, height=height, theme=theme, withdrawal=True,
                                  reload_agrees=True, short_refusal_focus=True,
                                  stale_refusal_focus=True, failed_read_recovery=True))
        browser.close()
    result = dict(cells=cells, synthetic_posts=len(calls), database_connections_permitted=0,
                  missing_refresh_plant_detected=True,
                  old_normalization_plant_detected=True,
                  actual_store_mutations=0, scope='actual templates and browser, synthetic response/state model')
    (out / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
