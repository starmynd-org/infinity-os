"""Browser regression of the actual templates; synthetic read port, no database access.

Run with an unused evidence directory. Screenshots form labelled light/dark pairs.
No live acts, credentials, private reports or raw HTML are persisted.
"""
from dataclasses import replace
from pathlib import Path
import json
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from web.tests import test_attention_native_options as checks
checks.psycopg2.connect = checks.forbidden_connection
from web.views.tests.test_act_door import an_app
from web.views import QueueView
from web.views.navigation import inspector_url
from flask import render_template, request


class Scene:
    evidence_label = 'Synthetic fixture evidence'
    def __init__(self):
        fixture = checks.Admission()
        fixture.setUp()
        try:
            original = checks.model.attention_port().queue(limit=0)
        finally:
            fixture.stack.close()
        ids = ('0429', '0427', '0461', '0441', '0428', '0445', '0447', '0446')
        self.items = [replace(i, item_id='work_item:' + sid, title='Synthetic completed report ' + sid,
                              rank=rank) for rank, (i, sid) in enumerate(zip(
                              [i for i in original.items if i.kind == 'review'], ids), 1)]
        self.accepted = set()
        self.task_receipts = []
        self.refusals = original.refusal_items
        self.show_refusals = False
        self.app = an_app(self)
        self.theme = 'light'
        self.app.context_processor(lambda: {'theme': self.theme})
        self.app.jinja_env.globals.update(image_host=lambda: 'synthetic.invalid')
        self.app.testing = True
        self.app.add_url_rule('/task/<tid>', view_func=self.task)
        self.app.add_url_rule('/intake', view_func=self.intake)

    def queue(self, *, offset=0, limit=None, filter_key='queue', sort=None, descending=False):
        from web.views.queue import SORTS, FILTERS
        rows = [i for i in self.items if i.item_id not in self.accepted]
        all_rows = list(rows)
        rows = [i for i in rows if FILTERS[filter_key](i)]
        if sort:
            rows.sort(key=SORTS[sort], reverse=descending)
        limit = 7 if limit is None else limit
        offset = min(offset, len(rows))
        shown = rows[offset:offset + limit] if limit else rows[offset:]
        refused = self.refusals if self.show_refusals else ()
        return QueueView(items=tuple(shown), checked_at=checks.NOW, window=0,
                         total_open=len(all_rows) + len(refused), admitted=len(all_rows),
                         refused_of=len(all_rows) + len(refused), refused=len(refused), refusal_items=refused,
                         blocked=0, deferred=0, filtered_total=len(rows), offset=offset, limit=limit,
                         reordered=bool(sort), sort_key=sort, descending=descending,
                         total_by_tier={'shape': len(rows), 'decide': 0, 'judge': 0})

    def item(self, typed):
        return next((i for i in self.items if i.item_id == typed and typed not in self.accepted), None)

    def refusal(self, typed):
        return next((r for r in self.refusals if r.item_id == typed), None)

    def intake(self):
        waiting = [dict(id=i, name='Synthetic objective ' + str(i), origin='human', body='Original objective body ' + str(i),
                        first_line='Original objective body ' + str(i), lines=1, bytes=25, proposals=[]) for i in range(1, 59)]
        return render_template('intake.html', room='intake', intake=dict(read=True, waiting=waiting,
            waiting_n=58, machine_n=0, taken_in_total=58, accepted_n=0, proposals_n=0,
            open_recs=0, objective_recs=0, sources=[], recent_accepted=[]))

    def task(self, tid):
        rv = dict(item=dict(id=tid, title='Synthetic completed report ' + tid, state='done',
                  accepted_at=checks.NOW if 'work_item:' + tid in self.accepted else None,
                  accepted_by='test-human', result='Completion evidence sentinel ' + tid,
                  agent_claimable=True, attempts=1, max_attempts=3),
                  signals={}, analysis=dict(claims=[dict(naked=False, text='UnbrokenEvidenceReference' * 30,
                    how='Synthetic fixture', evidence='Recorded synthetic artifact')], naked=[], line='Synthetic evidence'),
                  dod=[], dod_note=None, brief='Synthetic work order', trail=[], thread=[],
                  artifacts=[dict(seq=1, path='synthetic/' + 'long-path-' * 20 + 'report.txt', kind='report', exists_now=True)],
                  img=None, caveat=None, fleet_paused=True, questions=[],
                  would_auto_accept=dict(enabled=False, eligible=False, reasons=[]))
        return render_template('detail_task.html', rv=rv, room='queue', crosstalk=[], receipts=self.task_receipts,
                               stopwatch=None, attention_return=inspector_url('work_item:' + tid, request.args)
                               if request.args.get('from_attention') == '1' else None)


def main():
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=False)
    scene = Scene()
    client = scene.app.test_client()
    browser_acts = []
    fail_next_read = False
    plant_progress = False
    concurrent_removal = False
    hold_next_read = False
    held_read = None

    def route(r):
        nonlocal fail_next_read, hold_next_read, held_read
        u = urlsplit(r.request.url)
        if plant_progress and u.path == '/static/attention-progress.js':
            r.fulfill(status=200, content_type='application/javascript', body='/* planted missing refresh */')
            return
        if r.request.method == 'POST' and u.path == '/attention/act':
            with scene.app.test_request_context(u.path, method='POST', data=r.request.post_data_buffer,
                                               content_type=r.request.header_value('content-type')) as c:
                form = dict(c.request.form)
            assert form['action'] in ('accept_work', 'unaccept')
            if form['action'] == 'accept_work':
                scene.accepted.add('work_item:' + form['id'])
                if concurrent_removal:
                    scene.accepted.add('work_item:0427')
            else:
                scene.accepted.discard('work_item:' + form['id'])
            browser_acts.append(form['action'])
            r.fulfill(status=200, content_type='application/json', body=json.dumps(dict(ok=True,
                      verb='accept work' if form['action'] == 'accept_work' else 'unaccept work',
                      receipt='Synthetic acceptance recorded' if form['action'] == 'accept_work' else 'Synthetic withdrawal recorded',
                      undo=dict(action='unaccept', id=form['id'], label='Unaccept') if form['action'] == 'accept_work' else None)))
        elif r.request.method == 'GET' and u.path.startswith(('/attention/', '/task/', '/static/', '/intake')):
            if hold_next_read and u.path == '/attention/':
                hold_next_read = False
                held_read = r
                return
            if fail_next_read and u.path == '/attention/':
                fail_next_read = False
                r.fulfill(status=503, body='Synthetic read failure')
                return
            response = client.get(u.path + ('?' + u.query if u.query else ''))
            r.fulfill(status=response.status_code, content_type=response.content_type, body=response.data)
        else:
            r.abort()

    executables = sorted(Path.home().glob('.cache/ms-playwright/chromium-*/chrome-linux/chrome'))
    executables += sorted(Path.home().glob('.cache/ms-playwright/chromium-*/chrome-linux64/chrome'))
    cells = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, **({'executable_path': str(executables[-1])} if executables else {}))
        page = browser.new_page()
        page.route('**/*', route)
        plant_progress = True
        page.goto('http://attention.test/attention/')
        page.locator('form:has(input[value="accept_work"]) button').first.click()
        page.locator('[data-att-receipt]').wait_for()
        detected = False
        try:
            page.get_by_text('6 of 7 pending shown · 1 receipt', exact=True).wait_for(timeout=1500)
        except BrowserTimeout:
            detected = True
        assert detected, 'missing-refresh plant was not detected'
        plant_progress = False
        scene.accepted.clear()
        for width, height in ((1440, 900), (375, 812)):
            page.set_viewport_size(dict(width=width, height=height))
            for theme in ('light', 'dark'):
                scene.theme = theme
                for item in scene.items:
                    page.goto('http://attention.test/attention/' + item.item_id + '?offset=7&limit=7')
                    assert page.locator('html').get_attribute('data-theme') == theme
                    assert page.locator('[data-completion-report]').count() == 1, 'missing report link'
                    assert 'not stated' not in page.locator('dt:has-text("Result") + dd').inner_text()
                page.screenshot(path=str(out / f'inspector-{width}-{theme}.png'), full_page=True)
                page.locator('[data-completion-report]').click()
                assert page.locator('html').get_attribute('data-theme') == theme
                assert page.locator('#completion-report').get_by_text('Completion evidence sentinel 0446', exact=True).count() == 1
                assert page.locator('#completion-report a[href="/artifact/1"]').count() == 1
                page.get_by_role('link', name='Return to Attention review', exact=True).click()
                page.get_by_role('link', name='Return to queue', exact=True).click()
                assert page.locator('html').get_attribute('data-theme') == theme
                assert page.locator('[data-queue-row="work_item:0446"]').count() == 1, 'lost eighth row'
                assert 'offset=7' in page.url and 'limit=7' in page.url
                page.goto('http://attention.test/attention/?filter=review&limit=0')
                assert page.locator('[data-queue-row]').count() == 8, 'pending acceptances missing from review'
                page.goto('http://attention.test/attention/?filter=stale')
                assert 'No rows match Stale.' in page.locator('[data-attention-queue]').inner_text()
                assert 'Nothing needs you' not in page.locator('[data-attention-queue]').inner_text()
                scene.show_refusals = True
                page.goto('http://attention.test/attention/')
                page.locator('[data-attention-preparation] summary').click()
                assert page.locator('[data-refused-item]').count() == 58
                page.locator('[data-refused-item="objective:58"] a').click()
                assert page.locator('#preparation-handoff').input_value().startswith('Attention: prepare objective:58')
                page.screenshot(path=str(out / f'preparation-{width}-{theme}.png'), full_page=True)
                page.locator('[data-preparation-source]').click()
                assert page.locator('#objective-58').get_by_text('Original objective body 58', exact=True).count() == 2
                assert page.locator('html').get_attribute('data-theme') == theme
                scene.show_refusals = False
                for item in scene.items[:7]:
                    scene.accepted.clear()
                    page.goto('http://attention.test/attention/')
                    page.locator('[data-queue-row="' + item.item_id + '"] form:has(input[value="accept_work"]) button').click()
                    page.get_by_text('6 of 7 pending shown · 1 receipt', exact=True).wait_for()
                    assert 'offset=6' in page.locator('[data-att-pages] a[rel="next"]').get_attribute('href')
                    page.locator('[data-att-pages] a[rel="next"]').click()
                    assert page.locator('[data-queue-row="work_item:0446"]').count() == 1
                scene.accepted.clear()
                page.goto('http://attention.test/attention/')
                fail_next_read = True
                page.locator('form:has(input[value="accept_work"]) button').first.click()
                page.get_by_text('The action is recorded; remaining counts could not be refreshed.', exact=True).wait_for()
                assert page.locator('[data-att-pages] a[rel="next"]').count() == 0
                assert page.locator('[data-att-receipt]').count() == 1
                scene.accepted.clear()
                page.goto('http://attention.test/attention/')
                page.locator('form:has(input[value="accept_work"]) button').first.click()
                page.get_by_text('6 of 7 pending shown · 1 receipt', exact=True).wait_for()
                receipt = page.locator('[data-att-receipt]')
                assert receipt.evaluate('el=>el===document.activeElement'), 'success advanced away from receipt'
                assert not receipt.locator('.att-receipt-details').get_attribute('open')
                assert receipt.locator('button').is_visible(), 'inverse hidden inside details'
                assert receipt.locator('.att-task').is_visible(), 'durable recovery hidden'
                assert receipt.bounding_box()['height'] <= 320, 'receipt still consumes excessive vertical space'
                receipt.get_by_role('link', name='Next pending row on this page', exact=True).click()
                assert page.locator('[data-queue-row]').first.evaluate('el=>el===document.activeElement')
                receipt.locator('summary').click()
                assert receipt.locator('.att-receipt-details .att-moved').is_visible()
                receipt.locator('summary').click()
                receipt.focus()
                page.screenshot(path=str(out / f'receipt-{width}-{theme}.png'), full_page=True)
                page.locator('[data-att-receipt] form:has(input[value="unaccept"]) button').click()
                page.get_by_text('The action is recorded. Reload to read the changed queue and continue.', exact=True).wait_for()
                withdrawal = page.locator('[data-att-receipt]').nth(1)
                assert withdrawal.evaluate('el=>el===document.activeElement')
                assert withdrawal.get_by_role('link', name='Reload queue from the start', exact=True).is_visible()
                assert withdrawal.bounding_box()['height'] <= 320
                page.screenshot(path=str(out / f'withdrawal-{width}-{theme}.png'), full_page=True)
                assert page.locator('[data-att-pages] a[rel="next"]').count() == 0
                assert not scene.accepted
                page.goto('http://attention.test/attention/')
                concurrent_removal = True
                page.locator('form:has(input[value="accept_work"]) button').first.click()
                page.get_by_text('The action is recorded; remaining counts could not be refreshed.', exact=True).wait_for()
                assert page.locator('[data-att-pages] a[rel="next"]').count() == 0
                concurrent_removal = False
                scene.accepted.clear()
                # Complete later rows first, then move focus while a read is held.
                # Continuation must stay on the receipt for the action just taken.
                page.goto('http://attention.test/attention/')
                page.locator('[data-queue-row]').last.locator('form:has(input[value="accept_work"]) button').click()
                page.get_by_text('6 of 7 pending shown · 1 receipt', exact=True).wait_for()
                hold_next_read = True
                page.locator('[data-queue-row]').first.locator('form:has(input[value="accept_work"]) button').click()
                page.wait_for_function('document.querySelectorAll("[data-att-receipt]").length === 2')
                page.locator('[data-att-receipt]').first.locator('button').focus()
                page.wait_for_timeout(50)
                assert held_read is not None
                response = client.get('/attention/')
                held_read.fulfill(status=response.status_code, content_type=response.content_type, body=response.data)
                held_read = None
                page.get_by_text('5 of 6 pending shown · 2 receipts', exact=True).wait_for()
                assert page.locator('[data-att-receipt]').first.locator('[data-att-next]').count() == 1
                assert page.locator('[data-att-receipt]').last.locator('[data-att-next]').count() == 0
                assert page.locator('[data-att-receipt]').first.locator('button').evaluate('el=>el===document.activeElement')
                page.get_by_role('link', name='Next pending row on this page', exact=True).click()
                assert page.locator('[data-queue-row]').first.evaluate('el=>el===document.activeElement')
                scene.accepted.clear()
                for label in ('Recorded on this install', 'Synthetic fixture evidence'):
                    scene.evidence_label = label
                    for path in ('/attention/', '/attention/work_item:0429'):
                        page.goto('http://attention.test' + path)
                        assert page.locator('[data-evidence-origin]').inner_text() == label
                        assert page.locator('html').get_attribute('data-theme') == theme
                page.screenshot(path=str(out / f'origin-{width}-{theme}.png'), full_page=True)
                for query in ('', '?filter=review&sort=title&dir=desc&limit=3'):
                    page.goto('http://attention.test/attention/' + query)
                    page.locator('[data-att-pages] a[rel="next"]').click()
                    target = page.locator('[data-queue-row]').first.get_attribute('data-queue-row')
                    page.locator('[data-queue-row]').first.get_by_role('link', name='Inspect', exact=True).click()
                    page.get_by_role('link', name='Return to queue', exact=True).click()
                    returned = page.locator('[data-queue-row="' + target + '"]')
                    page.wait_for_function('id => document.activeElement?.dataset.queueRow === id', arg=target, timeout=3000)
                    assert returned.evaluate('el=>el===document.activeElement'), 'return did not focus its row'
                    title = returned.locator('b').first.bounding_box()
                    header_bottom = page.locator('header').bounding_box()['height']
                    assert header_bottom <= title['y'] < height
                    assert returned.evaluate('el=>{const r=el.querySelector("b").getBoundingClientRect();return document.elementFromPoint(r.left+3,r.top+3).closest("[data-queue-row]")===el;}'), 'returned title is occluded'
                    if query:
                        assert all(part in page.url for part in ('filter=review', 'sort=title', 'dir=desc', 'limit=3', 'offset=3'))
                for typed in ('objective:10', 'work_item:unavailable'):
                    response = page.goto('http://attention.test/attention/' + typed + '?filter=review&limit=3')
                    assert response.status == 404
                    assert page.locator('html').get_attribute('data-theme') == theme
                    assert page.get_by_role('heading', level=1, name='Item unavailable in this view').is_visible()
                    assert page.locator('form').count() == 0
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    if typed.startswith('objective:'):
                        page.get_by_role('link', name='Read the preparation or handling reason', exact=True).click()
                        assert page.locator('[data-preparation-source]').count() == 1
                        page.go_back()
                    else:
                        assert 'does not establish whether the source exists' in page.locator('[data-attention-unavailable]').inner_text()
                        page.screenshot(path=str(out / f'unavailable-{width}-{theme}.png'), full_page=True)
                    page.get_by_role('link', name='Return to Attention', exact=True).click()
                    assert 'filter=review' in page.url and 'limit=3' in page.url
                    assert page.locator('[data-queue-row]').count() == 3
                cells.append(dict(width=width, height=height, theme=theme, reports=8, return_to_eighth=True))
        restricted = replace(scene.items[0], readable=False, title='Restricted item')
        scene.items[0] = restricted
        page.goto('http://attention.test/attention/' + restricted.item_id)
        assert page.locator('[data-completion-report]').count() == 0, 'restricted report link leaked'
        browser.close()
    result = dict(cells=cells, database_connections_permitted=0, transitions_executed=0,
                  synthetic_browser_act_responses=len(browser_acts),
                  scope='actual Flask templates; synthetic completion reports and read port')
    (out / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
