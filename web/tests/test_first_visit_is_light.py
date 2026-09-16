"""A visitor who has chosen nothing gets light, and one who has chosen keeps what they chose.

W3-UX-INFINITY, 2026-09-15, on ALPHA-COMMANDER-2's R21. The locked Infinity style guide
(`the product factory repository`, `docs/style-infinity/ANSWER-FOR-ANDREW.md`, locked by Andrew
2026-09-15 at `0d026cc`) says in one line: "First visit: light, whatever the device says, with a
saved Light, Dark, System switch". The console booted DARK on a first visit.

WHY THIS WAS NOT A CONFLICT BETWEEN TWO DECISIONS. The `dark` fallback entered at `72a04c0`,
2026-08-16, a month before the lock. It was a product that had not caught up with a guide, not a
second live decision, which is why this is a defect and not an escalation.

WHY NO TOKEN CENSUS COULD HAVE CAUGHT IT, and this is the part worth carrying: a first-visit
default is a TERNARY IN A TEMPLATE, not a hex in a stylesheet. Infinity could carry 33 of 33 of the
locked tokens and still open on the wrong one of its two palettes. The census that reads
`console.css` for hex literals is looking in a file that cannot hold this answer.

WHAT THIS SUITE PINS. The resolved value of the pre-paint expression for every input a visitor can
arrive with, read out of the SERVED HTML rather than out of the template source, so a change that
edits one of the two copies and not the other is caught. It does not drive a browser: the
expression is three lines of inline JavaScript and is evaluated here directly, which is stated
rather than hidden. A browser walk over the rendered `data-theme` is the harness's job, and eight of
Infinity's browser harnesses do not yet read the rendered theme back at all.

WATCHED FAILING. Reverting `web/templates/base.html` and `web/templates/stack.html` to `e363020`
reddens `test_a_first_visit_resolves_light`, `test_stack_resolves_the_same_as_the_shell` and
`test_the_two_copies_of_the_boot_are_character_identical` by name. The control tests, which assert
that a stored choice is honoured in BOTH directions, stay green across the revert -- they were green
before this change and must stay green after it, because this change must move exactly one visitor.

Run:  python3 -m unittest web.tests.test_first_visit_is_light
"""

import os
import re
import unittest
from unittest import mock

os.environ["BRAIN_PG_DB"] = os.environ.get("W3_THEME_DB", "brain_w3ux_theme_no_such_db")
if os.environ["BRAIN_PG_DB"] == "brain":
    raise SystemExit("test_first_visit_is_light: refusing the live store; this suite needs no rows")

import web.model as model  # noqa: E402  (the database name must be set first)
from web.app import create_app  # noqa: E402

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(WEB_DIR, "templates", "base.html")
STACK = os.path.join(WEB_DIR, "templates", "stack.html")

BOOT_RE = re.compile(
    r"var t=(?P<server>[^;]*);.*?"
    r"localStorage\.getItem\('vd-theme'\).*?"
    r"setAttribute\('data-theme', *t==='(?P<pivot>\w+)'\?'(?P<hit>\w+)':'(?P<miss>\w+)'\)",
    re.S)


def boot_of(html):
    """The pre-paint expression as SERVED, as (server value, pivot, hit, miss)."""
    m = BOOT_RE.search(html)
    assert m is not None, "the pre-paint theme script is not in the served page"
    return m.group("server"), m.group("pivot"), m.group("hit"), m.group("miss")


def resolve(server, stored, boot):
    """Evaluate the served ternary for one visitor, in Python, exactly as the browser would.

    `server` is what the context processor handed the template; `stored` is localStorage. The
    script's own falsiness rule is `if(!t)`, so null and the empty string both fall through.
    """
    _, pivot, hit, miss = boot
    t = server
    if not t:
        t = stored
    return hit if t == pivot else miss


class TheFirstVisitIsLight(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.patchers = [
            mock.patch.object(model, "queue_view",
                              lambda **kw: {"totals": {"decide": 0}, "tiers": {"decide": []}}),
            mock.patch.object(model, "table_view", lambda **kw: {
                "rows": [], "counts": {q: {"n": 0, "available": True, "why": None}
                                       for q in model.QUEUES},
                "across": None, "decisions_available": True, "queue": None, "sort": None,
                "dir": "desc", "total": 0, "shown": 0, "enriched": 0,
                "sortable": sorted(model.SORTABLE)}),
        ]
        for patcher in cls.patchers:
            patcher.start()
        client = create_app().test_client()
        cls.plain = client.get("/queue/table").get_data(as_text=True)
        cls.asked_light = client.get("/queue/table?theme=light").get_data(as_text=True)
        cls.asked_dark = client.get("/queue/table?theme=dark").get_data(as_text=True)
        cls.boot = boot_of(cls.plain)

    @classmethod
    def tearDownClass(cls):
        for patcher in cls.patchers:
            patcher.stop()

    # ------------------------------------------------------------------------- the finding

    def test_a_first_visit_resolves_light(self):
        """No query, no cookie, no localStorage: the state every stranger arrives in."""
        server, _, _, _ = self.boot
        self.assertEqual(server, "null",
                         "the server claimed a theme on a bare request: %r" % server)
        self.assertEqual(resolve(None, None, self.boot), "light")

    def test_a_device_that_prefers_dark_still_gets_light(self):
        """'whatever the device says' -- and nothing here reads the device, which is the point.

        There is no `prefers-color-scheme` anywhere in Infinity's stylesheets (0 occurrences
        against 2 `[data-theme="dark"]` blocks), so the device cannot reach this decision at all.
        That is the guide's instruction implemented by construction rather than by a branch.
        """
        css = open(os.path.join(WEB_DIR, "static", "console.css"), encoding="utf-8").read()
        self.assertNotIn("prefers-color-scheme", css)
        self.assertIn('[data-theme="dark"]', css)

    def test_stack_resolves_the_same_as_the_shell(self):
        """The full-screen room extends nothing and boots from its own copy."""
        stack_src = open(STACK, encoding="utf-8").read()
        self.assertIn("t==='dark'?'dark':'light'", stack_src)

    def test_the_two_copies_of_the_boot_are_character_identical(self):
        """Two copies of one expression is how two surfaces come to disagree."""
        line = "document.documentElement.setAttribute('data-theme', t==='dark'?'dark':'light');"
        for path in (BASE, STACK):
            src = open(path, encoding="utf-8").read()
            self.assertIn(line, src, "%s does not carry the boot verbatim" % os.path.basename(path))

    # --------------------------------------------------- controls: what must NOT have moved

    def test_control_a_stored_dark_is_still_honoured(self):
        """The nonzero beside the zero. If this file only asserted 'light', inverting the
        expression to a constant `'light'` would pass it and would silently throw away every
        operator's saved choice."""
        self.assertEqual(resolve(None, "dark", self.boot), "dark")
        self.assertEqual(resolve("dark", None, self.boot), "dark")

    def test_control_a_stored_light_is_still_honoured(self):
        self.assertEqual(resolve(None, "light", self.boot), "light")
        self.assertEqual(resolve("light", None, self.boot), "light")

    def test_control_the_query_parameter_still_reaches_the_template(self):
        """Proves the server half of the path is live, so `null` above is a real null and not a
        parameter that never arrived."""
        self.assertEqual(boot_of(self.asked_light)[0], '"light"')
        self.assertEqual(boot_of(self.asked_dark)[0], '"dark"')

    def test_control_the_switch_still_writes_both_words_and_nothing_else(self):
        """The inversion is only safe because the stored value space is closed to two words.
        `console.js` is the only writer; if it ever stores a third, this assumption breaks and
        that visitor silently becomes light."""
        js = open(os.path.join(WEB_DIR, "static", "console.js"), encoding="utf-8").read()
        self.assertIn("localStorage.setItem('vd-theme', n)", js)
        self.assertIn("vd_theme=' + n", js)
        self.assertEqual(js.count("localStorage.setItem('vd-theme'"), 1,
                         "a second writer of the stored theme appeared")


if __name__ == "__main__":
    unittest.main()
