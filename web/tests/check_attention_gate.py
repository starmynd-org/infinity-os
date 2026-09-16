"""F-02: the twelve-character rule is visible text, and a press on the gated control answers.

Actual templates and the page's own script in Chromium, synthetic read port, a synthetic write
door that only counts. In each of four width/theme configurations, for Mark done and for Reject:
press empty (a sentence appears, focus lands on the field, no request leaves the page), type five
characters (the visible hint still names the rule and the remaining count), press again (same),
type twelve (the control is no longer gated) and press (exactly one request). Screenshots form
labelled light/dark pairs of the answered short press.
"""
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


def main():
    from playwright.sync_api import sync_playwright
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=False)
    fixture = checks.Admission()
    fixture.setUp()
    try:
        view = checks.model.attention_port().queue(limit=0)
    finally:
        fixture.stack.close()
    items = [next(i for i in view.items if i.kind == 'human')] + [i for i in view.items if i.kind == 'recommendation'][:1]

    class Port:
        evidence_label = 'Synthetic fixture evidence'

        def queue(self, *, filter_key='queue', sort=None, descending=False, offset=0, limit=None):
            return QueueView(items=tuple(items), checked_at=checks.NOW, window=0, total_open=2, admitted=2,
                             refused_of=2, refused=0, blocked=0, deferred=0, filtered_total=2, offset=0, limit=0,
                             total_by_tier={'decide': 0, 'judge': 1, 'shape': 1})

        def item(self, typed):
            return next((i for i in items if i.item_id == typed), None)

    app = an_app(Port())
    client = app.test_client()
    posts = []

    def route(r):
        u = urlsplit(r.request.url)
        if r.request.method == 'POST' and u.path == '/attention/act':
            posts.append(r.request.post_data or '')
            r.fulfill(status=200, content_type='application/json', body=json.dumps(dict(
                ok=True, verb='done', receipt='synthetic receipt', undo=None,
                no_undo_reason='synthetic: no inverse in this instrument', subject_type='work_item')))
        elif r.request.method == 'GET' and u.path.startswith(('/attention/', '/static/')):
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
                for action, label in (('mark_done', 'Mark done'), ('reject', 'Reject')):
                    before = len(posts)
                    page.goto('http://attention.test/attention/')
                    page.locator('html').evaluate('(el, theme) => el.setAttribute("data-theme", theme)', theme)
                    form = page.locator(f'form:has(input[value="{action}"])').first
                    field = form.locator('input[name="text"]')
                    button = form.locator('button')
                    hint = form.locator('[data-att-hint]')
                    assert hint.is_visible(), 'the rule is not visible text before typing'
                    assert 'at least 12 characters' in hint.inner_text()
                    assert field.get_attribute('aria-describedby') == hint.get_attribute('id')
                    assert button.get_attribute('aria-disabled') == 'true' and button.get_attribute('disabled') is None
                    # Playwright reads aria-disabled as not enabled, so the DOM is asked directly:
                    # the control carries no disabled property, takes focus, and a click reaches it.
                    assert button.evaluate('b => !b.disabled && (b.focus(), document.activeElement === b)'), \
                        'a gated control must still be focusable and pressable'
                    # Press empty (a DOM click, the same event a pointer or Enter produces).
                    button.evaluate('b => b.click()')
                    alert = form.locator('..').locator('[role="alert"]').last
                    alert.wait_for()
                    assert 'at least 12 characters' in alert.inner_text() and '0' in alert.inner_text()
                    assert page.evaluate('document.activeElement && document.activeElement.name') == 'text'
                    assert len(posts) == before, 'a gated press made a request'
                    page.screenshot(path=str(out / f'{action}-{width}-{theme}.png'), full_page=True)
                    # Five characters: the placeholder is gone; the hint still names the rule.
                    field.fill('short')
                    assert hint.is_visible() and 'at least 12 characters' in hint.inner_text() and '7 more' in hint.inner_text()
                    assert button.get_attribute('aria-disabled') == 'true'
                    button.evaluate('b => b.click()')
                    alert = form.locator('..').locator('[role="alert"]').last
                    assert 'at least 12 characters' in alert.inner_text() and '5' in alert.inner_text()
                    assert len(posts) == before
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    # P3-04: the browser counts the way the server counts. Six emoji are twelve
                    # UTF-16 units and six code points: gated. Six emoji and six letters: open.
                    # Surrounding whitespace, including Python's NEL and NBSP, does not count.
                    for text, expect_open, remaining in (('\U0001F642' * 6, False, 6),
                                                         ('\U0001F642' * 6 + 'abcdef', True, 0),
                                                         ('\u00a0twelve char\u0085', False, 1),
                                                         ('\u0085 twelve chars \u00a0', True, 0)):
                        field.fill(text)
                        assert (button.get_attribute('aria-disabled') == 'false') == expect_open, text
                        if not expect_open:
                            assert '%d more' % remaining in hint.inner_text(), (text, hint.inner_text())
                            button.evaluate('b => b.click()')
                            assert len(posts) == before, 'a gated press made a request'
                    field.fill('twelve chars')
                    assert button.get_attribute('aria-disabled') == 'false'
                    assert 'met' in hint.inner_text()
                    button.click()
                    page.locator('[data-att-receipt]').first.wait_for()
                    assert len(posts) == before + 1, 'an open gate must send exactly one request'
                    assert 'name="action"' in posts[-1] and action in posts[-1], posts[-1][:200]
                    cells.append(dict(width=width, height=height, theme=theme, action=action,
                                      empty_press_refused=True, short_press_refused=True, open_gate_posts=1,
                                      hint_visible_while_typing=True, focus_on_field=True,
                                      emoji_only_gated=True, emoji_mixed_open=True, whitespace_uncounted=True))
        browser.close()
    result = dict(cells=cells, posts=len(posts), database_connections_permitted=0, transitions_executed=0)
    (out / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
