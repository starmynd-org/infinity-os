"""F-01 / P3-03 geometry of actual Attention templates with long synthetic proposals.

Three recommendations rank first (1,100 to 1,230 character proposals, a long counterargument),
then four unfinished tasks, with the dated intake observation on: the shape Screening measured on
c6fc3cc. Synthetic read port, no database access, no act is posted. Screenshots form labelled
light/dark pairs. Measures, per width and theme: every first-page row's height, the first action
control's bottom edge, horizontal overflow, and that the full proposal is reachable on demand in
the row and on the inspector.
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
from web.views.queue import bounded_title, TITLE_BOUND

WORDS = ('Consolidate the renewal quotes for the three suppliers into one comparison, then '
         'schedule a fifteen minute call with the account owner to confirm which clauses changed '
         'since the last term and whether the indexation cap still applies. ')
COUNTER = ('The comparison may take longer than the call it is meant to shorten, and the account '
           'owner has already said the clauses did not change; the cost may exceed the benefit.')


def proposal(n, length):
    mark = ' END-%d' % n
    body = ('Proposal %d: ' % n) + (WORDS * 12)
    return body[:length - len(mark)].rstrip() + mark


def main():
    from playwright.sync_api import sync_playwright
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=False)
    # The long proposals go in as the queue row titles, so the store port's own branch bounds
    # them: RESULT.json then measures what production builds, not a title this file pre-bounded.
    fixture = checks.Admission()
    fixture.setUp()
    try:
        lengths = {'1': 1230, '2': 1160, '3': 1099}
        for row in fixture.rows:
            if row['source_type'] == 'recommendation':
                row['title'] = proposal(int(row['source_id']), lengths[row['source_id']])
                fixture.facts[('recommendation', row['source_id'])]['recommendation_counterargument'] = COUNTER
        view = checks.model.attention_port().queue(limit=0)
    finally:
        fixture.stack.close()
    recs = sorted([i for i in view.items if i.kind == 'recommendation'], key=lambda i: i.item_id)[:3]
    tasks = [i for i in view.items if i.kind == 'human'][:4]
    items = []
    for rank, rec in enumerate(recs, 1):
        assert 1099 <= len(rec.proposal) <= 1230, len(rec.proposal)
        assert rec.title == bounded_title(rec.proposal) and len(rec.title) <= TITLE_BOUND
        assert rec.counterargument == COUNTER
        items.append(replace(rec, rank=rank))
    for rank, task in enumerate(tasks, 4):
        items.append(replace(task, rank=rank, title='Renew the office insurance before the broker deadline ' + task.item_id))

    class Port:
        evidence_label = 'Synthetic fixture evidence'
        intake_observation = checks.model._StoreAttentionPort.intake_observation

        def queue(self, *, filter_key='queue', sort=None, descending=False, offset=0, limit=None):
            # The blueprint inspects this signature by name; a port that pages must accept the filter.
            return QueueView(items=tuple(items), checked_at=checks.NOW, window=0, total_open=7, admitted=7,
                             refused_of=7, refused=0, blocked=0, deferred=0, filtered_total=7, offset=0, limit=7,
                             total_by_tier={'decide': 0, 'judge': 3, 'shape': 4})

        def item(self, typed):
            return next((i for i in items if i.item_id == typed), None)

    port = Port()
    app = an_app(port)
    client = app.test_client()

    def route(r):
        u = urlsplit(r.request.url)
        if r.request.method == 'GET' and u.path.startswith(('/attention/', '/static/')):
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
                page.goto('http://attention.test/attention/')
                page.locator('html').evaluate('(el, theme) => el.setAttribute("data-theme", theme)', theme)
                rows = page.locator('[data-queue-row]')
                assert rows.count() == 7
                assert page.locator('[data-intake-observation]').count() == 1
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                heights = [round(rows.nth(i).bounding_box()['height'], 1) for i in range(7)]
                titles = [rows.nth(i).locator('td[data-label="Item"] b').inner_text() for i in range(3)]
                assert all(len(t) <= TITLE_BOUND for t in titles), titles
                for i in range(3):
                    summary = rows.nth(i).locator('[data-att-proposal] summary')
                    assert summary.is_visible() and summary.inner_text() == 'Full proposal and argument against'
                    assert not rows.nth(i).locator('[data-att-proposal] > div').is_visible()
                    # The argument and the no-inverse sentence are read before the first control.
                    counter = rows.nth(i).locator('[data-att-counter]')
                    inverse = rows.nth(i).locator('[data-att-inverse-note]')
                    button = rows.nth(i).locator('form[data-att-act] button').first
                    assert counter.is_visible() and inverse.is_visible()
                    assert 'cannot be undone' in inverse.inner_text()
                    assert counter.bounding_box()['y'] < inverse.bounding_box()['y'] < button.bounding_box()['y']
                    assert rows.nth(i).locator('[data-att-inverse-note]').count() == 1
                first_action = page.locator('[data-queue-row] form[data-att-act] button').first
                box = first_action.bounding_box()
                first_action_bottom = round(box['y'] + box['height'], 1)
                first_action_label = first_action.inner_text()
                page.screenshot(path=str(out / f'queue-{width}-{theme}.png'), full_page=True)
                # The full text is one press away in the row, and the row still ends in its acts.
                rows.first.locator('[data-att-proposal] summary').click()
                expanded = rows.first.locator('[data-att-proposal] > div')
                assert expanded.is_visible()
                assert 'END-1' in expanded.inner_text() and len(expanded.inner_text()) >= 1230
                assert 'Argument against, in full: ' + COUNTER in expanded.inner_text()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(out / f'expanded-{width}-{theme}.png'), full_page=True)
                # And on the inspector, in full, under its own heading.
                page.goto('http://attention.test/attention/' + items[0].item_id)
                page.locator('html').evaluate('(el, theme) => el.setAttribute("data-theme", theme)', theme)
                full = page.locator('dt:has-text("Proposal, in full") + dd')
                assert 'END-1' in full.inner_text() and len(full.inner_text()) >= 1230
                assert page.locator('h1').count() == 1 and len(page.locator('h1').inner_text()) <= 140
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(out / f'inspector-{width}-{theme}.png'), full_page=True)
                cells.append(dict(width=width, height=height, theme=theme, rows=7, row_heights=heights,
                                  max_row_height=max(heights), first_action=first_action_label,
                                  first_action_bottom=first_action_bottom,
                                  first_action_inside_viewport=first_action_bottom <= height,
                                  proposal_lengths=[len(i.proposal) for i in items[:3]],
                                  title_lengths=[len(t) for t in titles], no_overflow=True))
        browser.close()
    result = dict(cells=cells, database_connections_permitted=0, transitions_executed=0)
    (out / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    assert all(c['first_action_inside_viewport'] for c in cells if c['width'] == 375), 'first action below the phone viewport'


if __name__ == '__main__':
    main()
