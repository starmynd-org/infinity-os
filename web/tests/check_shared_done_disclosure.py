"""The shared card's Mark my task done discloses, before the act, what the note does.

The Queue room's card (`macros.html::card`, the only caller of `reason_box` with `mark_done`)
is rendered here through the real `reason_box` macro on a minimal page carrying the console's own
stylesheet and script, so the toggle and the panel behave as they do in the room. Chromium opens
the panel in four width/theme configurations and measures that the disclosure is visible above
the note field and reads exactly the P3-01 sentences. Synthetic item, no act is posted, no
database is reached. Screenshots form labelled light/dark pairs.

WHAT THIS DOES NOT PROVE: the room's card grid, the two-level expansion that gates the toggle in
the real Queue room, the toggle's real markup in `primary()`, or the room's chrome. The page is a
stand-in for the panel, and the pairs are pictures of the panel, not of the room.
"""
from pathlib import Path
import json
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import psycopg2


def forbidden(*args, **kwargs):
    raise AssertionError('shared disclosure check attempted a database connection')


psycopg2.connect = forbidden
from flask import Flask, render_template_string, request
from web.attention_options import DONE_DISCLOSURE, DONE_INVERSE_NOTE

PAGE = '''<!doctype html><html lang="en" data-theme="{{ theme }}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="/static/console.css"></head>
<body data-room="queue"><div class="shell"><div id="saybar"></div>
{% import "macros.html" as m %}
{# A plain card, not the room's `.qrow`/`.qgrid` grid: that grid needs the row's other columns
   to lay out and collapses without them. This page proves the panel's content and order, not
   the room's card geometry, which the scratch-database suites own. #}
<div class="card" id="card-{{ item.id }}" style="max-width:720px">
  <button class="sec" data-toggle="mine-{{ item.id }}">Mark my task done</button>
  {{ m.reason_box('queue', 'mark_done', item, 'mine-' ~ item.id, 'Mark my task done',
                  'What did you actually do? Numbers, paths, how you checked it.',
                  'Your own work: the guard reads the row, not this card.') }}
</div></div>
<canvas id="cf"></canvas><script src="/static/console.js"></script></body></html>'''


def main():
    from playwright.sync_api import sync_playwright
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=False)
    app = Flask(__name__, template_folder=str(ROOT / 'web' / 'templates'), static_folder=str(ROOT / 'web' / 'static'))
    app.jinja_env.globals['csrf_for'] = lambda room: 'synthetic-token'
    item = dict(id='0001', kind='human', title='Renew the office insurance', done_refused=False)

    @app.get('/')
    def page():
        return render_template_string(PAGE, item=item, theme=request.args.get('theme', 'light'))

    client = app.test_client()

    def route(r):
        u = urlsplit(r.request.url)
        if r.request.method == 'GET':
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
                page.goto('http://queue.test/?theme=' + theme, wait_until='networkidle')
                assert page.locator('html').get_attribute('data-theme') == theme
                panel = page.locator('#mine-0001')
                assert not panel.is_visible(), 'the panel starts closed'
                page.get_by_role('button', name='Mark my task done', exact=True).click()
                assert panel.is_visible(), 'the toggle did not open the panel'
                disclosure = panel.locator('[data-done-disclosure]')
                assert disclosure.count() == 1 and disclosure.is_visible()
                assert disclosure.inner_text() == DONE_DISCLOSURE + ' ' + DONE_INVERSE_NOTE
                field = panel.locator('textarea[name="text"]')
                assert disclosure.bounding_box()['y'] + disclosure.bounding_box()['height'] <= field.bounding_box()['y']
                assert panel.locator('button[type="submit"]').is_disabled(), 'the shared gate is unchanged'
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(out / f'card-{width}-{theme}.png'), full_page=True)
                cells.append(dict(width=width, height=height, theme=theme, disclosure_visible=True,
                                  disclosure_above_field=True, wording_matches_attention=True, no_overflow=True))
        browser.close()
    result = dict(cells=cells, database_connections_permitted=0, transitions_executed=0)
    (out / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
