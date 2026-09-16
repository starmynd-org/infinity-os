"""Deep work's page pair resolves to P2 Manila, painted through `body.deep`, in both schemes.

W4-INFINITY-FIX, 2026-09-16. Style-guide key `IX-SG-07`, which P14 (`W3-P14-REPORT.md`, run at
`e363020`) reported FAIL and W3-INFINITY-LAND re-measured UNCHANGED at `fece8c3`
(`W3-INFINITY-LAND.md` section 4): "the deep-work palette is untouched by this merge." This suite
is the first thing in this repo that watches that key.

THE PALETTE IS NOT THIS SEAT'S CHOICE. `the product factory repository` `docs/style-infinity/
INFINITY-STYLE-GUIDE.md` at `34dcc21` (branch `w3/t7-lock-state-correction`), section 3d, is
LOCKED 2026-09-15. Andrew's own words on the shape of it, QUOTED there: "maybe deep work is just
always a bit darker ... almost like a more softer brown hue." Four stocks were drawn and measured;
P2 Manila `#F3EEE4` was chosen over P1 Paper `#FCFBF8` (the value this repo shipped). A reader who
disagrees with the palette is disagreeing with the lock, not with this file.

FIVE ROLES, NOT ALL TWELVE OF SECTION 3D'S TABLE. P14 reads only what already exists as a declared
custom property at `:root` (`console.css`'s own "EVERY COLOUR ... IS A TOKEN BY NAME" comment lists
eight: `--deep-bg`, `--deep-bg-raised`, `--deep-grid`, `--deep-ink-read`, `--deep-ink-2`,
`--deep-ink-3`, `--deep-rule`, `--deep-accent`). Of those, `--deep-accent` already matched the
guide before this commit (`#663B72` / `#D2B4DA`) and `--deep-rule` is excluded from the
off-palette count the same way `IX-SG-06` excluded `--rule`/`--rule-strong`: it shipped as an
rgba ink-wash, which the guide's own section 3c calls "decorative" rather than a token comparison.
That leaves exactly five: `--deep-bg`, `--deep-bg-raised`, `--deep-ink-read`, `--deep-ink-2`,
`--deep-ink-3` -- which is P14's own arithmetic, "5 of 6 deep roles differ" against a built
`--deep-accent` that did not.

PAINTED, NOT ONLY DECLARED. P14's own stated limitation (`W3-P14-REPORT.md` section 7): `body.deep`
is absent on all 15 of its product routes, so its `IX-SG-07` reading is the declared custom
property at `:root`, never a computed value on a rendered deep surface. This suite goes one step
past that limit, in the direction the limitation itself points: `body.deep` is forced by script on
a real loaded page (the same class the server would add did the door ever open in this fixture),
and `body`'s background and colour are read AFTER the class is applied, off the shipped
`body.deep{background:var(--bg);color:var(--ink)}` rule at `console.css:1152` -- not injected by
this file. A page that declared the right hex under a selector nothing ever matched would still
fail this half.

WATCHED FAILING. `git stash` this file's sibling edit to `console.css` (or check out `fece8c3`'s
copy) and re-run: `test_the_five_roles_resolve_to_manila_in_light` and its dark twin fail by name,
quoting `built=#fcfbf8`/`#0f1117` against `locked=#f3eee4`/`#14120f` -- the exact pair
`W3-INFINITY-LAND.md` section 4 recorded as unmoved.

CONTROLS. `--deep-accent` must NOT move (section 3d's own row says it already matched); `--focus`
stays `--run`'s alias, unrelated to this palette but cheap to guard since a careless sweep of "the
deep tokens" could plausibly touch it. The two new roles the guide adds, `--deep-edge` and
`--deep-field-border`, are asserted DECLARED and nothing asserts either is spent -- no rule
consumes them yet, same as `--field-border`/`--selected-row` were left in `IX-SG-06`'s fix.

Run:  python3 -m unittest web.tests.test_deep_work_is_manila
"""

import os
import unittest
from urllib.parse import urlsplit

from web.tests.test_routines_and_attention_render import an_app

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - reported as NOT RUN below
    sync_playwright = None

ORIGIN = "http://console.test"

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONSOLE_CSS = os.path.join(WEB_DIR, "static", "console.css")

# Guide section 3d, LOCKED 2026-09-15. Light, then dark. Five roles P14 can see move; --deep-rule
# and the two new roles are checked separately below, not folded into this sweep.
MANILA = {
    "light": {
        "--deep-bg": "#f3eee4", "--deep-bg-raised": "#f8f4ec",
        "--deep-ink-read": "#1d1a15", "--deep-ink-2": "#4d4a45", "--deep-ink-3": "#63605b",
    },
    "dark": {
        "--deep-bg": "#14120f", "--deep-bg-raised": "#1c1a16",
        "--deep-ink-read": "#e9e5dd", "--deep-ink-2": "#b2ada3", "--deep-ink-3": "#8e887c",
    },
}

RULE = {"light": "#dfd8c8", "dark": "#2a2721"}
NEW_ROLES = ("--deep-edge", "--deep-field-border")

# Section 3d's own row: this one already matched before this commit and must not move.
ACCENT_CONTROL = {"light": "#663b72", "dark": "#d2b4da"}

# The painted half: body.deep{background:var(--bg);color:var(--ink)} at console.css:1152, read
# after --bg/--ink are rebound to --deep-bg/--deep-ink-read inside that same rule.
PAINTED_BG = {"light": "rgb(243, 238, 228)", "dark": "rgb(20, 18, 15)"}
PAINTED_INK = {"light": "rgb(29, 26, 21)", "dark": "rgb(233, 229, 221)"}

READ = """(names) => {
  const root = document.documentElement;
  const css = getComputedStyle(root);
  const tokens = {};
  for (const n of names) tokens[n] = css.getPropertyValue(n).trim().toLowerCase();

  // Forced the same way test_the_ground_is_slate.py forces data-theme: nothing in this fixture's
  // 15 reachable routes ever adds this class (P14 measured that, section 7), so a real page load
  // is given the class the door would add, and the SHIPPED rule decides what paints.
  document.body.classList.add('deep');
  const painted = {
    bg: getComputedStyle(document.body).backgroundColor,
    ink: getComputedStyle(document.body).color,
  };
  document.body.classList.remove('deep');

  return {tokens: tokens, painted: painted, theme: root.getAttribute('data-theme')};
}"""


class DeepWorkIsManila(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise unittest.SkipTest("NOT RUN: playwright is not importable in this interpreter")
        client = an_app().test_client()
        aborted = []

        def answer(route):
            request = route.request
            if request.method != "GET" or not request.url.startswith(ORIGIN + "/"):
                aborted.append((request.method, request.url))
                return route.abort()
            parts = urlsplit(request.url)
            path = parts.path + ("?" + parts.query if parts.query else "")
            response = client.get(path)
            headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
            return route.fulfill(status=response.status_code, headers=headers,
                                 body=response.get_data())

        names = sorted(set(list(MANILA["light"]) + list(NEW_ROLES)
                           + ["--deep-rule", "--deep-accent", "--focus", "--run"]))
        cls.read = {}
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:  # pragma: no cover - reported as NOT RUN
                raise unittest.SkipTest("NOT RUN: chromium did not launch: %s" % exc)
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            ctx.route("**/*", answer)
            page = ctx.new_page()
            response = page.goto(ORIGIN + "/attention/", wait_until="load")
            assert response.status == 200, response.status
            for scheme in ("light", "dark"):
                page.evaluate("(s) => document.documentElement.setAttribute('data-theme', s)",
                              scheme)
                cls.read[scheme] = page.evaluate(READ, names)
            ctx.close()
            browser.close()
        cls.aborted = aborted
        for scheme in ("light", "dark"):
            got = cls.read[scheme]
            print("\n  MEASURED %-5s data-theme=%s  body.deep painted bg=%s ink=%s"
                  % (scheme, got["theme"], got["painted"]["bg"], got["painted"]["ink"]))
            print("           %s" % {k: v for k, v in sorted(got["tokens"].items())})

    # ---------------------------------------------------------------- the key: IX-SG-07

    def test_the_five_roles_resolve_to_manila_in_light(self):
        self._assert_roles("light")

    def test_the_five_roles_resolve_to_manila_in_dark(self):
        self._assert_roles("dark")

    def _assert_roles(self, scheme):
        got = self.read[scheme]["tokens"]
        wrong = []
        for name, expected in sorted(MANILA[scheme].items()):
            if got.get(name, "") != expected:
                wrong.append("%s locked=%s built=%s" % (name, expected, got.get(name) or "ABSENT"))
        self.assertEqual(wrong, [], "%s: %d of %d deep roles are not P2 Manila:\n    %s"
                         % (scheme, len(wrong), len(MANILA[scheme]), "\n    ".join(wrong)))

    def test_the_body_is_actually_painted_the_deep_page_colour(self):
        """Past the declaration: `body.deep`'s own rule, not this file's assertion of one."""
        for scheme in ("light", "dark"):
            painted = self.read[scheme]["painted"]
            self.assertEqual(painted["bg"], PAINTED_BG[scheme],
                             "%s: body.deep paints %s, not --deep-bg" % (scheme, painted["bg"]))
            self.assertEqual(painted["ink"], PAINTED_INK[scheme],
                             "%s: body.deep paints ink %s, not --deep-ink-read"
                             % (scheme, painted["ink"]))

    def test_the_rule_is_ground_relative_and_no_longer_an_ink_wash(self):
        """Section 3d authors `rule` as a hex, same reason `IX-SG-06` gave for --rule/--rule-strong.

        rgba rules composite against whatever they are drawn over; excluded from the five-role
        off-palette count above for the same reason, but asserted here so the conversion is not
        silently reverted.
        """
        for scheme in ("light", "dark"):
            value = self.read[scheme]["tokens"]["--deep-rule"]
            self.assertNotIn("rgba", value, "%s --deep-rule is still a wash: %s" % (scheme, value))
            self.assertEqual(value, RULE[scheme])

    def test_the_two_new_roles_are_declared_rather_than_missing(self):
        """Section 3d calls `edge` and `field border` new. Declared and deliberately unspent."""
        for scheme in ("light", "dark"):
            for name in NEW_ROLES:
                self.assertTrue(self.read[scheme]["tokens"].get(name),
                                "%s has no value for %s" % (scheme, name))

    # ---------------------------------------------------------------- controls: what must NOT move

    def test_control_the_accent_already_matched_and_did_not_move(self):
        """Section 3d's own row: --deep-accent shipped correct. A sweep of "the deep tokens" that
        touched it anyway would be moving a value the guide never asked to change."""
        for scheme in ("light", "dark"):
            got = self.read[scheme]["tokens"]["--deep-accent"]
            self.assertEqual(got, ACCENT_CONTROL[scheme],
                             "%s: --deep-accent moved to %s, unasked" % (scheme, got))

    def test_control_focus_is_still_the_same_value_as_run(self):
        for scheme in ("light", "dark"):
            got = self.read[scheme]["tokens"]
            self.assertEqual(got["--focus"], got["--run"],
                             "%s: --focus %s is no longer --run %s" % (scheme, got["--focus"],
                                                                       got["--run"]))

    def test_control_nothing_offorigin_was_requested(self):
        self.assertEqual(self.aborted, [], "the page reached outside the harness: %s" % self.aborted)


if __name__ == "__main__":
    unittest.main()
