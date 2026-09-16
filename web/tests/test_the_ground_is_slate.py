"""The ten ground roles resolve to Ground B Slate, in both schemes, painted rather than declared.

W3-UX-INFINITY, 2026-09-16. Style-guide key `IX-SG-06`, which the P14 instrument reports as FAIL
on the deployed build: the locked guide's ground had not reached `console.css`.

THE GROUND IS NOT THIS SEAT'S CHOICE AND THIS SUITE IS NOT WHERE IT WAS MADE. Andrew locked the
Infinity style guide on 2026-09-15, QUOTED in its header: "I like it a lot. Let's go with your
suggestions and lock in this style guide." Its section 3 heading records the consequence for this
key, QUOTED: "LOCKED 2026-09-15: B Slate". The values below are that guide's section 3a, column B,
mapped onto these token names by its own section 3c. A reader who disagrees with the palette is
disagreeing with the lock, not with this file.

PAINTED, NOT READ OFF THE CASCADE, and the distinction has burned this seat before. A token can be
declared correctly in a block that never wins, and a declaration-level assertion goes green over
that. So every value here is read back out of `getComputedStyle` in chromium after the page has
loaded the real stylesheet, which is the cascade's own answer to "what is `--bg` here". Three
further checks go past the token to the paint: the body's background must BE `--bg` as an rgb
triple, a real `<input type="text">` must take its border from `--field-border` through the
shipped rule rather than from anything this file injects, and in light the page and the surface
must not be the same colour -- section 3c's whole complaint about what was here, QUOTED: "today
identical to --bg in light, so nothing lifts off the page".

WHAT THE CONTROLS ARE FOR. Fourteen state values across the two schemes must NOT have moved: the
guide's section 3b fixes them as laws rather than choices, and they are the strongest existing tie
between Infinity and the parent system. A change that swept the whole palette would redden them.
`--focus` must still be character-for-character `--run`; un-aliasing it is a separate locked
choice this key does not carry, and the guide contradicts itself about it (the lock table says
un-aliased, section 3c says "`--focus` stays an alias"), so it is pinned as it ships and routed.

WATCHED FAILING. Reverting `web/static/console.css` to d4341d2 reddens this suite by name --
see the seat report for the run, the file hashes either side and the denominator.

The `--selected-row` token is asserted as DECLARED and nothing asserts it is spent, because it is
deliberately unspent; `console.css` carries the reason at its declaration.

Run:  python3 -m unittest web.tests.test_the_ground_is_slate
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

# Guide section 3a, column B, through section 3c's mapping. Light, then dark.
GROUND = {
    "light": {
        "--bg": "#f6f8fb", "--bg-raised": "#ffffff", "--bg-sunk": "#e9ecf1",
        "--rule": "#d9dee6", "--rule-strong": "#bfc6d1", "--field-border": "#737c89",
        "--ink": "#171a20", "--ink-2": "#515965", "--ink-3": "#636b78",
        "--selected-row": "#eae6ef",
    },
    "dark": {
        "--bg": "#14171c", "--bg-raised": "#1b1f26", "--bg-sunk": "#101317",
        "--rule": "#2a2f38", "--rule-strong": "#39404c", "--field-border": "#78818f",
        "--ink": "#e8ebef", "--ink-2": "#afb5bf", "--ink-3": "#8a919c",
        "--selected-row": "#241e2b",
    },
}

# Guide section 3b: laws, not choices. These must survive a ground change untouched.
LAWS = {
    "light": {"--brand": "#774785", "--brand-text": "#663b72", "--run": "#3a6b9e",
              "--fin": "#1c7048", "--wait": "#7d5800", "--dmg": "#ac2318", "--rest": "#6b7280"},
    "dark": {"--brand": "#c59fd0", "--brand-text": "#d2b4da", "--run": "#7fb3e8",
             "--fin": "#63c89b", "--wait": "#e0b75a", "--dmg": "#ee8078", "--rest": "#8e8d8a"},
}

# The painted half. `--bg` as chromium reports a background, per scheme.
BODY_RGB = {"light": "rgb(246, 248, 251)", "dark": "rgb(20, 23, 28)"}
FIELD_RGB = {"light": "rgb(115, 124, 137)", "dark": "rgb(120, 129, 143)"}

READ = """(names) => {
  const root = document.documentElement;
  const css = getComputedStyle(root);
  const tokens = {};
  for (const n of names) tokens[n] = css.getPropertyValue(n).trim().toLowerCase();

  // Real house fields, inserted into the live document so the SHIPPED rules decide their
  // borders. Nothing here sets a colour: if the rule did not carry --field-border, this reads
  // whatever the user agent gives, which is not the expected triple.
  //
  // `.tinput` is the terminal pane's field and lives in `terminal.css`, which `base.html` links
  // only from the terminal template's head block -- so the suite loads that stylesheet itself
  // (see TERMINAL_CSS below) rather than pretend this page already had it. That is the point of
  // the probe: terminal.css spends a token declared in console.css, and this is the only thing
  // that checks the two files still agree.
  const read = el => {
    document.body.appendChild(el);
    const cs = getComputedStyle(el);
    const out = {colour: cs.borderTopColor, width: cs.borderTopWidth, style: cs.borderTopStyle};
    el.remove();
    return out;
  };
  const bare = document.createElement('input');
  bare.type = 'text';
  const tinput = document.createElement('input');
  tinput.type = 'text';
  tinput.className = 'tinput';

  return {
    tokens: tokens,
    body_bg: getComputedStyle(document.body).backgroundColor,
    field: read(bare),
    tinput: read(tinput),
    terminal_css_loaded: [...document.styleSheets].some(
      s => (s.href || '').indexOf('terminal.css') !== -1),
    theme: root.getAttribute('data-theme'),
  };
}"""

# The terminal pane's stylesheet is linked only from the terminal template's head block, so the
# page under test has to be given it before `.tinput` means anything. Awaited, not fired and
# hoped for: an unloaded stylesheet leaves the probe at the user agent's border, and the suite
# would then report a colour mismatch that is really an absent file.
TERMINAL_CSS = """() => new Promise((resolve, reject) => {
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = '/static/terminal.css';
  link.onload = () => resolve(true);
  link.onerror = () => reject(new Error('terminal.css did not load'));
  document.head.appendChild(link);
})"""


class TheGroundIsSlate(unittest.TestCase):

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

        names = sorted(set(list(GROUND["light"]) + list(LAWS["light"])
                           + ["--focus", "--on-solid", "--grid"]))
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
            page.evaluate(TERMINAL_CSS)
            for scheme in ("light", "dark"):
                page.evaluate("(s) => document.documentElement.setAttribute('data-theme', s)",
                              scheme)
                cls.read[scheme] = page.evaluate(READ, names)
            ctx.close()
            browser.close()
        cls.aborted = aborted
        with open(CONSOLE_CSS, encoding="utf-8") as handle:
            cls.source = handle.read()
        for scheme in ("light", "dark"):
            got = cls.read[scheme]
            print("\n  MEASURED %-5s data-theme=%s  body %s  field %s %s %s  .tinput %s  "
                  "terminal.css %s"
                  % (scheme, got["theme"], got["body_bg"], got["field"]["colour"],
                     got["field"]["width"], got["field"]["style"], got["tinput"]["colour"],
                     "loaded" if got["terminal_css_loaded"] else "ABSENT"))
            print("           %s" % {k: v for k, v in sorted(got["tokens"].items())})

    # ---------------------------------------------------------------- the key: IX-SG-06

    def test_the_ten_ground_roles_resolve_to_slate_in_light(self):
        self._assert_roles("light")

    def test_the_ten_ground_roles_resolve_to_slate_in_dark(self):
        self._assert_roles("dark")

    def _assert_roles(self, scheme):
        got = self.read[scheme]["tokens"]
        wrong = []
        for name, expected in sorted(GROUND[scheme].items()):
            if got.get(name, "") != expected:
                wrong.append("%s locked=%s built=%s" % (name, expected, got.get(name) or "ABSENT"))
        self.assertEqual(wrong, [], "%s: %d of %d ground roles are not Ground B Slate:\n    %s"
                         % (scheme, len(wrong), len(GROUND[scheme]), "\n    ".join(wrong)))

    def test_the_two_new_roles_are_declared_rather_than_missing(self):
        """`IX-SG-06` reports these two as NO BUILT TOKEN AT ALL, so their absence is the finding.

        Separated from the sweep above so a run that is only missing the new names says so by
        name instead of inside a list of ten.
        """
        for scheme in ("light", "dark"):
            for name in ("--field-border", "--selected-row"):
                self.assertTrue(self.read[scheme]["tokens"].get(name),
                                "%s has no value for %s" % (scheme, name))

    # ---------------------------------------------------------------- past the declaration

    def test_the_body_is_actually_painted_the_page_colour(self):
        for scheme in ("light", "dark"):
            self.assertEqual(self.read[scheme]["body_bg"], BODY_RGB[scheme],
                             "%s: the body paints %s, not --bg"
                             % (scheme, self.read[scheme]["body_bg"]))

    def test_a_real_field_takes_its_border_from_the_field_border_token(self):
        """The shipped `input[type=text]` rule, measured on an element it styles.

        The width and style assertions are the non-vacuity half: if the rule did not match the
        probe at all, the border would be the user agent's 2px inset and this would say so rather
        than reporting a colour mismatch that looks like a wrong hex.
        """
        self._assert_field("field", "a house field")

    def test_the_terminal_pane_field_spends_the_same_token(self):
        """`terminal.css`'s `.tinput`, which is a separate file reading console.css's `:root`.

        Two stylesheets agreeing on a token is the thing nothing else in this repo checks, and it
        is the half of the WCAG 1.4.11 fix that is easiest to leave behind.
        """
        for scheme in ("light", "dark"):
            self.assertTrue(self.read[scheme]["terminal_css_loaded"],
                            "terminal.css is not on the page, so .tinput measures nothing")
        self._assert_field("tinput", "the terminal pane's field")

    def _assert_field(self, key, what):
        for scheme in ("light", "dark"):
            field = self.read[scheme][key]
            self.assertEqual(field["width"], "1px",
                             "%s: the rule for %s did not match the probe at all (border-width %s)"
                             % (scheme, what, field["width"]))
            self.assertEqual(field["style"], "solid", (scheme, what, field))
            self.assertEqual(field["colour"], FIELD_RGB[scheme],
                             "%s: %s is bounded by %s, not --field-border"
                             % (scheme, what, field["colour"]))

    def test_in_light_the_surface_lifts_off_the_page(self):
        """Section 3c's complaint, as an assertion: --bg and --bg-raised were the same white."""
        tokens = self.read["light"]["tokens"]
        self.assertNotEqual(tokens["--bg"], tokens["--bg-raised"],
                            "the page and the surface are both %s, so nothing lifts off the page"
                            % tokens["--bg"])

    def test_the_rules_are_ground_relative_and_no_longer_an_ink_wash(self):
        """rgba rules composite against whatever they are drawn over; the guide authors hex."""
        for scheme in ("light", "dark"):
            for name in ("--rule", "--rule-strong"):
                value = self.read[scheme]["tokens"][name]
                self.assertNotIn("rgba", value, "%s %s is still a wash: %s" % (scheme, name, value))

    # ---------------------------------------------------------------- controls: what must NOT move

    def test_control_the_seven_state_values_did_not_move_in_either_scheme(self):
        """Section 3b. A change that swept the palette rather than the ground reddens this."""
        wrong = []
        for scheme in ("light", "dark"):
            got = self.read[scheme]["tokens"]
            for name, expected in sorted(LAWS[scheme].items()):
                if got.get(name, "") != expected:
                    wrong.append("%s %s law=%s built=%s"
                                 % (scheme, name, expected, got.get(name) or "ABSENT"))
        self.assertEqual(wrong, [], "state values moved:\n    " + "\n    ".join(wrong))

    def test_control_focus_is_still_the_same_value_as_run(self):
        for scheme in ("light", "dark"):
            got = self.read[scheme]["tokens"]
            self.assertEqual(got["--focus"], got["--run"],
                             "%s: --focus %s is no longer --run %s" % (scheme, got["--focus"],
                                                                       got["--run"]))

    def test_control_on_solid_did_not_follow_the_ground(self):
        """It was the old dark --bg's hex and it stays there; `console.css` carries the reason.

        Moving it to #14171C would raise the label-on-brand pair to 7.91 and contradict the 7.74
        the locked guide publishes for it. This is the assertion that would catch someone tidying
        that comment away.
        """
        self.assertEqual(self.read["dark"]["tokens"]["--on-solid"], "#191919")
        self.assertEqual(self.read["light"]["tokens"]["--on-solid"], "#ffffff")

    def test_control_no_stale_measurement_is_left_in_the_dark_brand_comment(self):
        """The comment asserted 9.44:1 for --brand-text on --bg, read off the OLD ground.

        Re-measured at #14171C it is 9.65. A number in a comment is a measurement, and a stale one
        is the trap this repo's own orientation names ("do not trust a commit message").
        """
        self.assertNotIn("9.44:1 on --bg", self.source,
                         "the dark palette still claims 9.44:1 against a ground that moved")
        self.assertIn("9.65:1 at #14171C", self.source)

    def test_control_nothing_offorigin_was_requested(self):
        self.assertEqual(self.aborted, [], "the page reached outside the harness: %s" % self.aborted)

    def test_control_the_grid_is_still_off_in_both_schemes(self):
        """Law 3 survives a ground change: --grid is transparent, not a faint ink.

        FIRST WRITTEN AS A COUNT OF `--grid:transparent` IN THE SOURCE AND IT WAS WRONG: it found
        three, because the comment at :194 quotes the declaration it is describing. A text count
        measures the file's vocabulary, not its declarations. The computed value is the property.
        """
        for scheme in ("light", "dark"):
            self.assertEqual(self.read[scheme]["tokens"]["--grid"], "transparent",
                             "%s: --grid is %s, so the 34px decorative grid can paint"
                             % (scheme, self.read[scheme]["tokens"]["--grid"]))


if __name__ == "__main__":
    unittest.main()
