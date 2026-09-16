"""Chromium checks of actual Attention templates with a synthetic, unwritable store.

Native dispatch reaches the real room guard, with store.apply replaced by an in-memory
receipt. This is browser integration evidence, not a database mutation proof.
"""
from pathlib import Path
import json
import os
import sys
from urllib.parse import parse_qs, urlsplit
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from web.tests import test_attention_native_options as checks
checks.psycopg2.connect = checks.forbidden_connection
from web.views.tests.test_act_door import an_app
from web.views import QueueView
from web.attention_native_door import perform
from web import actions
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=False)
fixture = checks.Admission()
fixture.setUp()
view = checks.model.attention_port().queue(limit=0)
fixture.stack.close()
items = [next(i for i in view.items if i.kind == 'human')] + [i for i in view.items if i.kind == 'recommendation'][:2]
class Port:
    def queue(self, **kwargs):
        return QueueView(items=tuple(items), checked_at=checks.NOW, window=0,
                         total_open=3, admitted=3, refused_of=3, refused=0, blocked=0, deferred=0,
                         filtered_total=3, offset=0, limit=0,
                         total_by_tier={'decide': 0, 'judge': 2, 'shape': 1})
    def item(self, typed):
        return next((i for i in items if i.item_id == typed), None)
port = Port()
app = an_app(port)
client = app.test_client()
calls = []
def applied(verb, **kwargs):
    calls.append((verb, kwargs.get('id')))
    return {'id': kwargs.get('id')}
def route(request_route):
    request = request_route.request
    path = urlsplit(request.url).path
    if request.method == 'POST' and path == '/attention/act':
        with app.test_request_context(path, method='POST', data=request.post_data_buffer,
                                      content_type=request.header_value('content-type')) as context:
            form = dict(context.request.form)
        with patch.object(checks.store, 'apply', applied), patch.object(checks.store, 'registered', return_value={
                'done': {}, 'reopen': {}, 'recommend accept': {}, 'recommend reject': {}}), \
                patch.object(checks.rooms, '_row_behind', return_value=dict(id='0001', agent_claimable=False, claimed_by=None, state='inbox')), \
                patch.object(checks.rooms, 'agent_work_on', return_value={}):
            if form['action'] == 'undo_done':
                out = actions.undo_done('attention', item={'id': form['id'], 'kind': 'review'}, operator='operator')
            else:
                out = perform(form['action'], form['id'], form.get('text', ''),
                              source_type=form.get('source_type'), port=port, operator='operator')
        response = {k: out.get(k) for k in ('verb', 'receipt', 'undo', 'no_undo_reason', 'subject_type')}
        request_route.fulfill(status=200, content_type='application/json', body=json.dumps(dict(ok=True, **response)))
    elif request.method == 'GET' and path.startswith(('/attention/', '/static/')):
        response = client.get(path)
        request_route.fulfill(status=response.status_code, content_type=response.content_type, body=response.data)
    else:
        request_route.abort()

executables = sorted(Path.home().glob('.cache/ms-playwright/chromium-*/chrome-linux/chrome'))
executables += sorted(Path.home().glob('.cache/ms-playwright/chromium-*/chrome-linux64/chrome'))
verdicts = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, **({'executable_path': str(executables[-1])} if executables else {}))
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    page.route('**/*', route)
    for width in (1440, 375):
        page.set_viewport_size({'width': width, 'height': 1000})
        for theme in ('light', 'dark'):
            page.goto('http://attention.test/attention/', wait_until='networkidle')
            page.locator('html').evaluate('(el, theme) => el.setAttribute("data-theme", theme)', theme)
            assert page.locator('[data-queue-row]').count() == 3
            assert page.locator('form[data-att-act]').count() == 5
            # F-02: gated with aria-disabled (focusable, answerable), never the bare disabled attribute.
            assert page.locator('form:has(input[value="mark_done"]) button').get_attribute('aria-disabled') == 'true'
            assert page.locator('form:has(input[value="mark_done"]) button').evaluate('b => !b.disabled')
            assert page.locator('form:has(input[value="reject"]) button').first.get_attribute('aria-disabled') == 'true'
            assert page.locator('form:has(input[value="approve"]) button').count() == 2
            assert not page.locator('input[name="action"][value="dispatch_option"]').count()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            # P3-01: the replacement truth is visible in the row BEFORE the note field, in every
            # configuration, and again on the inspector's Available actions row.
            done_form = page.locator('form:has(input[value="mark_done"])')
            aside = done_form.locator('.att-aside').first
            assert aside.is_visible() and 'replaces whatever result it held before' in aside.inner_text()
            assert 'the earlier result text is not restored' in aside.inner_text()
            assert aside.bounding_box()['y'] < done_form.locator('input[name="text"]').bounding_box()['y']
            page.screenshot(path=str(OUT / f'queue-{width}-{theme}.png'), full_page=True)
            page.goto('http://attention.test/attention/' + items[0].item_id, wait_until='networkidle')
            page.locator('html').evaluate('(el, theme) => el.setAttribute("data-theme", theme)', theme)
            actions_row = page.locator('dt:has-text("Available actions") + dd')
            assert 'replaces whatever result it held before' in actions_row.inner_text()
            page.screenshot(path=str(OUT / f'inspector-{width}-{theme}.png'), full_page=True)
            # Done, then undo: the reopen receipt states in words that the note stays as the result.
            page.goto('http://attention.test/attention/', wait_until='networkidle')
            page.locator('html').evaluate('(el, theme) => el.setAttribute("data-theme", theme)', theme)
            form = page.locator('form:has(input[value="mark_done"])')
            form.locator('input[name="text"]').fill('Completed the synthetic task proof')
            assert form.locator('button').get_attribute('aria-disabled') == 'false'
            form.locator('button').click()
            page.locator('[data-att-receipt]').wait_for()
            # The response reports completion, not current acceptance state (Assembly 78ab795).
            assert page.locator('[data-att-receipt] > td > .att-moved').filter(has_text='Finished;').inner_text() == 'Finished; the completion was recorded.'
            assert page.locator('form:has(input[value="undo_done"])').count() == 1
            page.locator('form:has(input[value="undo_done"]) button').click()
            reopen = page.locator('[data-att-receipt]').filter(has_text='0001 back in your queue')
            reopen.wait_for()
            assert 'no undo' in reopen.inner_text()
            assert 'stays as its stored result text' in reopen.inner_text()
            assert 'the server sent no reason' not in reopen.inner_text()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            reopen.scroll_into_view_if_needed()
            page.screenshot(path=str(OUT / f'receipt-{width}-{theme}.png'), full_page=True)
            verdicts.append({'theme': theme, 'width': width, 'rows': 3, 'forms': 5, 'no_horizontal_overflow': True,
                             'disclosure_before_field': True, 'inspector_disclosure': True,
                             'reopen_receipt_states_retained_note': True})
    page.set_viewport_size({'width': 1440, 'height': 1000})
    for action in ('approve', 'reject'):
        page.goto('http://attention.test/attention/', wait_until='networkidle')
        form = page.locator(f'form:has(input[value="{action}"])').first
        if action == 'reject':
            form.locator('input[name="text"]').fill('The synthetic proposal costs too much')
        form.locator('button').click()
        stripe = page.locator('[data-att-receipt]')
        stripe.wait_for()
        assert stripe.get_by_text('This proposal has left the open queue.', exact=False).count() == 1
        assert stripe.locator('a[href*="/attention/recommendation:"]').count() == 0
        assert stripe.get_by_text('no undo', exact=False).count() > 0
    browser.close()
assert [c[0] for c in calls] == ['done', 'reopen'] * 4 + ['recommend accept', 'recommend reject']
receipt = {'viewport_theme_checks': verdicts, 'actions': [c[0] for c in calls],
           'actions_checked': '4/4 verbs; done and reopen in each of four configurations',
           'actual_store_mutations': 0,
           'scope': 'actual templates and room dispatch; synthetic in-memory transition receipts'}
(OUT / 'RESULT.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt, indent=2))
