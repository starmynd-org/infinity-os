"""The visual vocabulary on the Attention room, and the served-commit element the shell has not got.

    BRAIN_PG_DB=ios_attention_tests_a1 ENGINE_SCRATCH_DB=ios_attention_tests_a1 \
        QUEUE_SCRATCH_DB=ios_attention_tests_a1 \
        python3 -m web.views.tests.test_visual_vocabulary_and_served_commit

Packet ATT-6, author A13, 2026-09-11. Two subjects, one file, because they are the two halves of
the same packet and a reader chasing either one should find the other.

WHAT THIS FILE CAN AND CANNOT SEE, said first so no green below is read as more than it is:

  * NO DATABASE. Every class here builds a Flask app over a stub read port, exactly as
    `test_act_door.py`'s `an_app` does, or over a temporary template directory. Nothing reads a
    store and nothing writes one.
  * IT RENDERS, IT DOES NOT RUN A BROWSER. It can say that a declaration names `--ws-causal` with
    a fallback and that the fallback resolves to a token `console.css` really defines. It CANNOT
    say what pixel a browser painted, and the "distinct hex values used by consumers classified
    causal" figure in the report is computed from the token values, not sampled from a screen.
  * THE FAMILY DOES NOT EXIST YET. `web/static/console.css` is not this seat's file and defines
    none of `--ws-causal`, `--ws-causal-text`, `--ws-causal-tint`, `--ws-on-causal`. That is
    asserted below as a FACT rather than assumed, because it is the whole reason every reference
    carries a fallback, and the day it stops being true the assertion is what says so.
  * EVERY ZERO CARRIES A POSITIVE CONTROL. A scanner that finds nothing is evidence only when the
    same scanner finds the thing in a string that really has it.

THE SECOND HALF IS A MEASUREMENT AND A PROPOSAL, NOT A CHANGE. R44 grants this seat ONE commit
element in `web/templates/base.html`, rendered "from what the app already knows". Measured here:
the app knows its commit in three places and exposes it to NO template, so the element cannot be
rendered honestly and `base.html` is not touched by this packet. `TheProposedShellHunkProducesEx
actlyOneElement` applies the proposed one-line hunk and the proposed element to a COPY of
`base.html` in a temporary directory and drives it, so the proposal in
`outputs/2026-09-10-ATTENTION-OS-admiral/ATT-6-vocabulary-and-commit-element.md` is falsifiable by
whoever lands it rather than an assertion about markup nobody ran.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest

from flask import Flask

from web import guard
from web.views.blueprint import build as build_attention
from web.views.tests.test_act_door import StubAttentionPort

WEB_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE_DIR = os.path.join(WEB_DIR, "templates", "attention")
STATIC_DIR = os.path.join(WEB_DIR, "static")
CONSOLE_CSS = os.path.join(STATIC_DIR, "console.css")
BASE_HTML = os.path.join(WEB_DIR, "templates", "base.html")
APP_PY = os.path.join(WEB_DIR, "app.py")

#: The two templates this seat owns. `controls.html` carries no colour at all and is scanned
#: anyway: "it has none" is a measurement that has to be re-taken, not a property to assume.
TEMPLATE_FILES = ("inbox.html", "controls.html")

#: The family, per `PROPOSAL-2026-09-10-INFINITY-EXPERIENCE-VISUAL-VOCABULARY.md` section 2 item 1,
#: with Andrew's PURPLE recorded in its section 4. The value each one falls back to today, and the
#: hex that fallback resolves to in `console.css`'s two palettes.
FAMILY = {
    "--ws-causal": ("--brand", "#774785", "#C59FD0"),
    "--ws-causal-text": ("--brand-text", "#663B72", "#D2B4DA"),
    "--ws-causal-tint": ("--tint-brand", "#F8F3F9", "#271E29"),
    "--ws-on-causal": ("--on-solid", "#FFFFFF", "#191919"),
}

#: A colour written as a number rather than as a name. The trailing `\b` is what keeps
#: `&#9656;` and `#evidence-rank` out of the count; the control class proves the regex bites.
HEX_RE = re.compile(r"#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3})?\b")

#: Any reference to the family, and the same reference WITH the fallback that keeps this page
#: working until `console.css` defines it. The two counts must be equal.
FAMILY_ANY_RE = re.compile(r"var\(\s*--ws-[a-z-]+")
FAMILY_WITH_FALLBACK_RE = re.compile(r"var\(\s*(--ws-[a-z-]+)\s*,\s*var\(\s*(--[a-z0-9-]+)\s*\)\s*\)")

#: An identity mark: the thing that says "you"/"assigned to you" rather than "because of you".
#: Section 2 item 2 of the proposal is the classification rule; this room is expected to have none.
IDENTITY_RE = re.compile(r"--you-fg|\.chip\.you\b|class=\"[^\"]*\byou\b|\bassignee\b")

#: The served-commit element, by its data attribute and by its words.
SERVED_ATTR_RE = re.compile(r'data-served-commit="([^"]*)"')
SERVED_TEXT_RE = re.compile(r"served at ([A-Za-z0-9]+)")


def source_of(name):
    with open(os.path.join(TEMPLATE_DIR, name), encoding="utf-8") as handle:
        return handle.read()


def file_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def an_app(port=None):
    """A real Flask app, a real Jinja environment, the real `base.html`, no console and no store.

    Copied in shape from `test_act_door.an_app` on purpose: the two files must render the same page
    the same way, or a green here and a green there would be about two different pages.
    """
    app = Flask(__name__, template_folder=os.path.join(WEB_DIR, "templates"),
                static_folder=STATIC_DIR)
    app.register_blueprint(build_attention(port or StubAttentionPort()))
    guard.attach(app)
    app.jinja_env.globals.update(
        intake_waiting=lambda: 0,
        deep_count=lambda: 0,
        dispatch_count=lambda: 0,
        dispatch_extra=lambda: {},
        dispatch_href=lambda: "/queue",
        mode_href=lambda **kw: "/queue",
    )

    @app.context_processor
    def _base_context():
        return {"theme": "dark", "room": "attention", "since": 0, "deep_on": False,
                "deep_since": None, "patch_only": False, "dispatch_on": False}

    return app


# --------------------------------------------------------------------- the vocabulary, in source


class NoColourIsWrittenAsANumberInThisRoom(unittest.TestCase):
    """Section 2's whole shape depends on colour arriving through tokens, so nothing may be typed.

    THE CONTROL IS `console.css`, which is where the numbers are supposed to live. A scanner that
    reported zero because it cannot see a hex colour at all would pass this file on any day.
    """

    def test_the_two_templates_carry_no_hex_colour(self):
        found = {name: HEX_RE.findall(source_of(name)) for name in TEMPLATE_FILES}
        print("\n  DENOMINATORS, hex colours written as numbers")
        for name in TEMPLATE_FILES:
            print("    %-14s %d, in %d bytes of source"
                  % (name, len(found[name]), len(source_of(name))))
        control = HEX_RE.findall(file_text(CONSOLE_CSS))
        print("    console.css    %d  (the positive control: the scanner bites)" % len(control))
        self.assertGreater(len(control), 20,
                           "the scanner found no hex in console.css, so its zeroes mean nothing")
        self.assertEqual(HEX_RE.findall("a{color:#774785;background:#FFF}"),
                         ["#774785", "#FFF"], "the scanner does not read a hex colour")
        for name in TEMPLATE_FILES:
            self.assertEqual(found[name], [], "%s writes a colour as a number" % name)


class EveryCausalConsumerNamesTheFamilyWithAFallback(unittest.TestCase):
    """Section 2 item 1's names, and the fallback that keeps the page whole until they exist.

    `console.css` is INFINITY-EXPERIENCE-admiral's under the proposal's section 3 and defines none
    of the four names today. That is asserted rather than assumed: it is the reason the fallbacks
    are there, and this is the assertion that will announce the day they become unnecessary.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = source_of("inbox.html")
        cls.css = file_text(CONSOLE_CSS)
        cls.any_refs = FAMILY_ANY_RE.findall(cls.source)
        cls.with_fallback = FAMILY_WITH_FALLBACK_RE.findall(cls.source)
        print("\n  DENOMINATORS, the causal family in inbox.html")
        print("    references to --ws-*          %d" % len(cls.any_refs))
        print("    of those, with a fallback     %d" % len(cls.with_fallback))
        for name, fallback in cls.with_fallback:
            print("      %-18s -> %s" % (name, fallback))

    def test_no_reference_is_written_without_a_fallback(self):
        self.assertEqual(len(self.any_refs), len(self.with_fallback),
                         "a --ws-* reference would render nothing until console.css defines it")
        self.assertGreater(len(self.with_fallback), 0,
                           "no causal consumer references the family at all")

    def test_all_four_family_names_are_spent_and_none_is_invented(self):
        used = {name for name, _ in self.with_fallback}
        print("    distinct family names used    %d of %d in the proposal" % (len(used), len(FAMILY)))
        self.assertEqual(used, set(FAMILY),
                         "the names used are not the four the proposal names")

    def test_each_fallback_is_a_token_console_css_really_defines(self):
        for name, fallback in self.with_fallback:
            self.assertIn("%s:" % FAMILY[name][0], self.css,
                          "%s is not defined in console.css" % FAMILY[name][0])
            self.assertEqual(fallback, FAMILY[name][0],
                             "%s falls back to %s, not to the token the proposal maps it to"
                             % (name, fallback))

    def test_the_family_itself_is_not_defined_yet_which_is_why_the_fallbacks_exist(self):
        for name in FAMILY:
            self.assertNotIn("%s:" % name, self.css,
                             "console.css now defines %s: the fallbacks are no longer load-bearing "
                             "and this packet's reasoning needs re-reading, not deleting" % name)


class TheAccentIsNeverSpentOnAStateOrAnAffordance(unittest.TestCase):
    """The coda's hard constraint, and `console.css:44`'s law, both read as assertions.

    `web/MUST-NOT-BUILD.md` at `dc9a99d`: the accent "must read as causal without reading as a
    fifth state colour", and "the five semantic states keep their hues and their meanings".
    `console.css:44`: "The brand accent is never spent on an affordance."
    """

    @classmethod
    def setUpClass(cls):
        cls.source = source_of("inbox.html")

    def test_the_four_state_chips_still_bind_their_state_tokens(self):
        expected = {".att-chip.wait{color:var(--wait)}",
                    ".att-chip.run{color:var(--run)}",
                    ".att-chip.rest{color:var(--rest)}",
                    ".att-chip.stale{color:var(--wait)}"}
        for rule in sorted(expected):
            self.assertIn(rule, self.source, "a state chip no longer carries its state token")
        self.assertIn(".att-err{margin-top:.3rem;font-size:.72rem;color:var(--dmg)", self.source,
                      "the refusal line no longer carries the damage token")

    def test_the_focus_ring_is_not_causal(self):
        self.assertIn(".att-receipt:focus{outline:2px solid var(--focus,var(--run))", self.source,
                      "the focus ring changed: an affordance may not carry the accent")

    def test_accent_is_not_referenced_at_all(self):
        """Section 2 item 3. `--accent` is links, focus and active nav, and is not this seat's."""
        hits = re.findall(r"var\(\s*--accent\b", self.source + source_of("controls.html"))
        print("\n    references to --accent        %d (expected 0)" % len(hits))
        self.assertEqual(hits, [])


class NoIdentityMarkIsCarriedByThisRoom(unittest.TestCase):
    """Section 2 item 2 has nothing to reclassify here, and that is measured rather than assumed.

    An IDENTITY mark says "you"/"assigned to you"; a CAUSAL one says "this happened because of
    you". The static chrome's `--you-fg` and `.chip.you` live on the c3 lane, not in the runtime
    plane, so the expected count in these two templates is 0 -- and a 0 from an unproven scanner
    is worth nothing, so the scanner is shown biting on a string that really has one.
    """

    def test_the_scanner_bites(self):
        self.assertTrue(IDENTITY_RE.search('<span class="chip you">you</span>'))
        self.assertTrue(IDENTITY_RE.search("--you-fg:#7a4a13;"))

    def test_neither_template_carries_one(self):
        counts = {name: len(IDENTITY_RE.findall(source_of(name))) for name in TEMPLATE_FILES}
        print("\n  DENOMINATORS, identity marks")
        for name in TEMPLATE_FILES:
            print("    %-14s %d (expected 0)" % (name, counts[name]))
        self.assertEqual(sum(counts.values()), 0)

    def test_the_runtime_plane_defines_no_you_token_either(self):
        """So "most of the 19 go neutral" is the c3 packet's work and none of it is this seat's."""
        self.assertNotIn("--you-fg", file_text(CONSOLE_CSS))


# ------------------------------------------------------------------- the vocabulary, on the page


class TheFamilyReachesTheRenderedPage(unittest.TestCase):
    """Source is not a page. The style block is inline, so the served bytes are checkable."""

    @classmethod
    def setUpClass(cls):
        cls.body = an_app().test_client().get("/attention/").get_data(as_text=True)
        cls.refs = FAMILY_WITH_FALLBACK_RE.findall(cls.body)
        print("\n  DENOMINATORS, the rendered Attention page")
        print("    bytes served                  %d" % len(cls.body))
        print("    --ws-* references on the page %d" % len(cls.refs))
        print("    act buttons on the page       %d" % cls.body.count('class="att-btn"'))

    def test_the_page_was_actually_rendered(self):
        self.assertGreater(len(self.body), 2000, "nothing was rendered, so nothing was measured")
        self.assertIn("att-btn", self.body, "no act control rendered, so the causal consumer is "
                                            "not on this page and its count means nothing")

    def test_every_family_name_is_on_the_page_with_its_fallback(self):
        self.assertEqual({name for name, _ in self.refs}, set(FAMILY))


# ----------------------------------------------------------------- the commit element, and why not


class TheShellExposesNoCommitToAnyTemplate(unittest.TestCase):
    """R44's grant is conditional on there being an honest value to render, and there is not.

    `web/app.py` knows the commit in three places: `_serving_commit()`, the `serving_commit`
    constant closed over by `_say_what_is_being_served` (which publishes it as the
    `X-Console-Commit` header), and `_serving_report`'s `commit_now`/`commit_at_start` on
    `/api/health`. NONE of the three reaches a template, so an element added to `base.html` today
    could only ever render the word "unknown" on all 23 extending templates -- and R44 grants ONE
    element, which is not a thing to spend on a value that does not exist.
    """

    @classmethod
    def setUpClass(cls):
        cls.body = an_app().test_client().get("/attention/").get_data(as_text=True)
        cls.app_source = file_text(APP_PY)
        cls.base_source = file_text(BASE_HTML)

    def test_the_scanner_bites(self):
        self.assertEqual(SERVED_ATTR_RE.findall('<code data-served-commit="57da822">x</code>'),
                         ["57da822"])

    def test_the_rendered_page_carries_no_served_commit_element(self):
        found = SERVED_ATTR_RE.findall(self.body)
        print("\n  DENOMINATORS, the served-commit element")
        print("    bytes served                  %d" % len(self.body))
        print("    [data-served-commit] on page  %d (expected 0 today)" % len(found))
        print("    [data-served-commit] in base  %d (expected 0 today)"
              % len(SERVED_ATTR_RE.findall(self.base_source)))
        self.assertEqual(found, [])
        self.assertEqual(SERVED_ATTR_RE.findall(self.base_source), [])

    def test_app_py_knows_its_commit(self):
        """The denominator for the sentence below: the value exists, it just does not travel."""
        self.assertIn("def _serving_commit()", self.app_source)
        self.assertIn('"commit_now": commit_now', self.app_source)
        self.assertIn('response.headers["X-Console-Commit"] = serving_commit or "unknown"',
                      self.app_source)

    def test_and_exposes_it_to_no_template(self):
        processors = re.findall(r"@app\.context_processor\s+def (\w+)", self.app_source)
        globals_updates = re.findall(r"app\.jinja_env\.globals\.update\(([^)]*)\)", self.app_source)
        print("    context processors in app.py  %d %s" % (len(processors), processors))
        print("    jinja globals registered      %s"
              % [g.strip() for g in ",".join(globals_updates).split(",") if g.strip()])
        self.assertGreater(len(processors), 3,
                           "found almost no context processors: the scan is broken, not the app")
        for name in ("served_commit", "commit_now", "commit_at_start", "serving_commit="):
            self.assertNotIn('"%s"' % name, "".join(globals_updates))
        self.assertNotIn("served_commit", self.app_source,
                         "app.py now carries a served_commit: the ATT-6 proposal has landed and "
                         "base.html's one element is now renderable")


class TheProposedShellHunkProducesExactlyOneElement(unittest.TestCase):
    """The proposal, driven. Neither `web/app.py` nor `web/templates/base.html` is edited.

    A COPY of `base.html` is patched in a temporary directory with the proposed element and served
    by a Flask app carrying the proposed one-line global, so the report's hunk is falsifiable by
    whoever lands it. Both arms of the honesty rule are driven: a commit present renders the sha,
    a commit absent renders the word "unknown" and never an invented value.
    """

    #: The one line `web/app.py` would gain, beside `_say_what_is_being_served` at :493 where the
    #: same constant is already spent. A GLOBAL and not a context processor calling
    #: `_serving_commit()`: that helper shells out to `git rev-parse`, and a context processor runs
    #: on every render including the three-second poll, which is the cost `_say_what_is_being_served`
    #: refuses in its own docstring.
    HUNK = 'app.jinja_env.globals["served_commit"] = serving_commit or "unknown"'

    #: The one element `base.html` would gain, as the last child of `<div class="hrow">`. No class,
    #: no style, no second element: R44 grants the element and no adjacent style or layout change.
    ELEMENT = ('<code data-served-commit="{{ served_commit }}">served at '
               '{{ served_commit }}</code>')

    #: Where it goes. `.hrow` closes once, immediately before the threshold-line comment, and it is
    #: ONE insertion point serving both the deep and the ordinary header rather than one per branch.
    ANCHOR = "</div>\n{# THE THRESHOLD LINE"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="att6-base-")
        base = file_text(BASE_HTML)
        assert base.count(cls.ANCHOR) == 1, "the .hrow anchor is not unique; the proposal moved"
        patched = base.replace(cls.ANCHOR, "  " + cls.ELEMENT + "\n" + cls.ANCHOR)
        with open(os.path.join(cls.tmp, "base.html"), "w", encoding="utf-8") as handle:
            handle.write(patched)
        with open(os.path.join(cls.tmp, "child.html"), "w", encoding="utf-8") as handle:
            handle.write('{% extends "base.html" %}{% block body %}<p>a child</p>{% endblock %}')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def a_page(self, serving_commit):
        app = Flask(__name__, template_folder=self.tmp, static_folder=STATIC_DIR)
        app.jinja_env.globals.update(
            intake_waiting=lambda: 0, deep_count=lambda: 0, dispatch_count=lambda: 0,
            dispatch_extra=lambda: {}, dispatch_href=lambda: "/queue",
            mode_href=lambda **kw: "/queue")
        # ---- THE PROPOSED HUNK, VERBATIM, evaluated rather than quoted.
        app.jinja_env.globals["served_commit"] = serving_commit or "unknown"

        @app.context_processor
        def _base_context():
            return {"theme": "dark", "room": "attention", "since": 0, "deep_on": False,
                    "deep_since": None, "deep_here": False, "patch_only": False,
                    "dispatch_on": False}

        @app.get("/")
        def _child():
            from flask import render_template
            return render_template("child.html")

        return app.test_client().get("/").get_data(as_text=True)

    def test_the_hunk_in_the_report_is_the_hunk_driven_here(self):
        """So the report cannot describe one line while this class proves another.

        THE FIRST VERSION OF THIS ASSERTION WAS TAUTOLOGICAL and is recorded here rather than
        silently replaced: it looked for `HUNK` in THIS file, which contains `HUNK`, so it could
        not fail for any edit anybody could make. It now reads the report, which is the document
        whoever lands the hunk will copy from, and the executable line below is counted so that a
        second copy cannot drift in unnoticed.
        """
        report = os.path.join(os.path.dirname(WEB_DIR), "outputs",
                              "2026-09-10-ATTENTION-OS-admiral",
                              "ATT-6-vocabulary-and-commit-element.md")
        self.assertTrue(os.path.exists(report), "the ATT-6 report is not where this test expects "
                                                "it; the hunk it proposes is now unverified")
        self.assertIn(self.HUNK, file_text(report),
                      "the report proposes a different line from the one driven here")
        executable = [line.strip() for line in file_text(os.path.abspath(__file__)).splitlines()
                      if line.strip() == self.HUNK]
        print("\n    executable copies of the hunk %d (expected exactly 1)" % len(executable))
        self.assertEqual(len(executable), 1)

    def test_a_known_commit_renders_exactly_one_element_carrying_the_sha(self):
        body = self.a_page("57da822")
        attrs = SERVED_ATTR_RE.findall(body)
        words = SERVED_TEXT_RE.findall(body)
        print("\n  DENOMINATORS, the proposed element under a known commit")
        print("    bytes rendered                %d" % len(body))
        print("    [data-served-commit]          %d (expected exactly 1)" % len(attrs))
        print("    'served at <sha>' occurrences %d %s" % (len(words), words))
        self.assertGreater(len(body), 2000, "the shell did not render, so nothing was measured")
        self.assertEqual(attrs, ["57da822"])
        self.assertEqual(words, ["57da822"])

    def test_an_unknown_commit_renders_the_word_and_never_an_invented_value(self):
        body = self.a_page(None)
        print("\n  DENOMINATORS, the control: the commit is absent")
        print("    [data-served-commit]          %s" % SERVED_ATTR_RE.findall(body))
        print("    'served at <word>'            %s" % SERVED_TEXT_RE.findall(body))
        self.assertEqual(SERVED_ATTR_RE.findall(body), ["unknown"])
        self.assertEqual(SERVED_TEXT_RE.findall(body), ["unknown"])
        self.assertNotIn("57da822", body, "a sha leaked from the other arm of this class")


if __name__ == "__main__":
    unittest.main(verbosity=2)
