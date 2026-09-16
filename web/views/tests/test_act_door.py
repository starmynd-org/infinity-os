"""The Attention act door, at the surface: what renders, what posts where, and what is refused.

    BRAIN_PG_DB=ios_attention_tests_a1 ENGINE_SCRATCH_DB=ios_attention_tests_a1 \
        QUEUE_SCRATCH_DB=ios_attention_tests_a1 python3 -m web.views.tests.test_act_door

Packet ATT-1d, author A3, 2026-09-10. Each class names the expectation it executes from
`_system/attention/RECEIPT-EXPECTATIONS-2026-09-10.md`, which was published before the controls
existed. `TheDoneStripeDoesNotPromiseTheWindow` was added by packet ATT-1f, author A5, on the same
day, and executes E6 AS AMENDED at 19:10Z after T2 measured X1.
`NoStripeOnThisPagePromisesTheWindow` was added by packet ATT-1h, author A6, on 2026-09-10 (23:0xZ,
which is 2026-09-11 in the operator's local evening; the measured UTC day is what is written): it
clears the residue A5 declared in section 2 of `ATT-1f-x2.md` (`reopen` and `unaccept work` still
promised a reload would show the row) and widens A5's phrase scan from one sentence to the whole
page and the whole source of both templates. `TheInspectorNamesTheRowsKind` was added by packet
ATT-1i, author A9, on 2026-09-11: it executes the LAST CLAUSE of E6 as amended, "a tester proves E6
by following the link after a reload and reading the inspector's kind", which T3 measured at X3 and
filed as an open reading because the inspector named no kind at all.
`TheReceiptStripeIsAnnouncedAndFocused` was added by packet ATT-1l, authors A11 and A12, on
2026-09-11: it holds the independent reviewer's F5 on X4 -- the page announced its refusals and not
its successes -- and it is the one class here that reads TEMPLATE SOURCE rather than a rendered
page, because what it is about is a script function no request in this file executes.
`TheInspectorNamesCompletenessInWords` and `TheRefusalPathKeepsFocusOnTheControl` were added by
packet ATT-1m, author A14, on 2026-09-11: they hold T7's two readings on X6 (`93363cb`,
`ATT-9-and-X6-measurement-93363cb.md`) -- Q2c NOT MET at 0 of 7 because "complete" was carried by
the absence of a row, and the refusal path re-enabling the pressed button without giving the focus
back. Each amendment, its measurement and the scope of each scan are in the classes' own docstrings.

WHAT THIS FILE CAN AND CANNOT SEE, said first so no green below is read as more than it is:

  * IT RENDERS, IT DOES NOT RUN A BROWSER. The stripe, its order (E1), the absence of a timer
    (E2) and the 375px geometry (E12) are properties of a page being DRIVEN, and this file drives
    nothing. It asserts the markup and the script that produce them; the tester with a viewport
    is the one who can say they did.
  * ONE OF THE TWELVE CLASSES NEEDS A DATABASE. `TheWriteDoorRefuses` builds the real app, which
    reads the store at startup, so it needs the scratch database named above -- never `brain`,
    never `ios_mount_scratch`. The other nine need none. (A3's version of this line said "three
    of the five" and was already stale at X2, which had six; it is repaired here rather than
    carried, and re-counted at each amendment for the same reason -- ATT-1l's class was the
    tenth and ATT-1m's two are the eleventh and the twelfth.)
  * AND THE OBJECTIVE HALF IS NOT PROVEN HERE. `AnObjectiveRowOffersTriageAndNothingElse` renders
    D4's row from a stub port, so it says what the control and the sentences ARE; it cannot say
    that an accept reached `brain.objective` or that the store refused the second one. That is the
    integration run in `outputs/2026-09-10-ATTENTION-OS-admiral/ATT-8-objectives-act.md`, against
    a disposable store with the proposed migration applied, and it is the only place in this
    packet where the acts themselves were watched.
  * EVERY ZERO BELOW IS PRINTED WITH ITS DENOMINATOR, and the two scans carry a positive control:
    a scanner that finds nothing in `web/views/**` is only evidence if the same scanner finds the
    thing in the file that really has it.
"""

from __future__ import annotations

import ast
import os
import re
import unittest
from datetime import datetime, timedelta, timezone

from flask import Flask

from web import guard, rooms
from web.actions import MIN_REASON
from web.attention_options import NativeOption
from web.views import (COMPLETE, Completeness, Freshness, Impact, Provenance, ProvenanceStep,
                       QueueItem, QueueView)
from web.views.queue import KIND_NOW, kind_sentence
from web.views.blueprint import MIN_REASON_MIRROR
from web.views.blueprint import build as build_attention

UTC = timezone.utc
NOW = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)

WEB_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VIEWS_DIR = os.path.join(WEB_DIR, "views")

#: The two templates this lane owns, scanned as SOURCE (Jinja comments included) as well as
#: rendered. A phrase that is forbidden on the page and left sitting in a comment is a phrase the
#: next author copies back out of the comment.
TEMPLATE_DIR = os.path.join(WEB_DIR, "templates", "attention")
TEMPLATE_FILES = ("inbox.html", "controls.html")

#: The door. Every form on the page declares this and nothing else declares anything (E7).
DOOR = "/attention/act"


# --------------------------------------------------------------------------- the fixture rows
#
# FOUR KINDS AND A FIFTH ROW THAT IS NOT A KIND. `KIND_BY_ARM` (`web/model.py:83`) produces exactly
# four words -- review, human, question, recommendation -- and `readable=False` is orthogonal to
# all four: it is a row whose content was never indexed for this reader, which the port can hand
# back under any kind at all. Both branches of the template's refusal are therefore exercised.
#
# THE IDS CARRY THE COLON ON PURPOSE. An Attention row's `item_id` is `<source_type>:<source_id>`
# and the write door matches the BARE `source_id` against the open queue, so a control that posted
# `work_item:w-review` would be refused as a stale card on every row. The colon in the fixture is
# what makes the test able to see that.

def an_item(**over):
    base = dict(
        item_id="work_item:w-review",
        title="Agent finished the renewal comparison",
        kind="review",
        tier=1,
        rank=1,
        why="the store records a completed work result",
        impact=Impact.unknown("the store records no measured amount with a unit and basis"),
        freshness=Freshness.never_read("no source read interval is recorded on this queue row"),
        provenance=Provenance("ops", "UTC", "work_item:w-review",
                              (ProvenanceStep(NOW - timedelta(minutes=20),
                                              "Item recorded in the runtime store", "producer"),)),
    )
    base.update(over)
    # This read-port fixture represents an admitted task with its typed native act.
    # Explicit native_options=() remains available for an unavailable task specimen.
    if base['kind'] == 'human' and 'native_options' not in over:
        sid = base['item_id'].partition(':')[2]
        base['native_options'] = (NativeOption(
            'mark-done', 'mark_done', 'Mark done',
            'Record what you did and mark this task complete.',
            'work_item', sid, 'done', 'What you did, in one line', MIN_REASON,
            'reopen', 'Reopen returns this task to unfinished work.'),)
    return QueueItem(**base)


REVIEW = an_item()
HUMAN = an_item(item_id="work_item:w-human", kind="human", rank=2, tier=2,
                title="Renew the office insurance")
QUESTION = an_item(item_id="question:q-1", kind="question", rank=3, tier=1,
                   title="Which supplier should the renewal go to?")
RECOMMENDATION = an_item(item_id="recommendation:r-1", kind="recommendation", rank=4, tier=2,
                         title="Recommend accepting the Halden quote")
RESTRICTED = an_item(item_id="work_item:w-restricted", kind="review", rank=5, tier=4,
                     readable=False, title="Item in a workspace you cannot read")

ROWS = [REVIEW, HUMAN, QUESTION, RECOMMENDATION, RESTRICTED]

#: ARM 5, ANDREW'S D4, packet ATT-8. KEPT OUT OF `ROWS` DELIBERATELY: the classes above assert
#: exact page-wide counts (three forms, three tokens, one `Inspect` per row), and a sixth row in
#: the shared list would move those numbers as a side effect of this packet rather than as a
#: decision. `AnObjectiveRowOffersTriageAndNothingElse` renders its own page from `ROWS + [
#: OBJECTIVE]` and prints its own denominators.
#:
#: THE ID IS THE BARE INTEGER THE STORE REALLY USES (`brain.objective.id::text`) rather than a
#: slug, because that integer is the whole reason the write door resolves an objective by its
#: TYPED id: a bare `6` can equal a work item's or a recommendation's, and PROOF case 8 built that
#: collision on a disposable store to watch the keyed lookup survive it.
#: The `why` is the OPTION and never a completed result, because `has_option` is the only term in
#: the gate that can admit an objective at all: one carries no completed work result, no answer
#: and no recommendation decision. A fixture that said otherwise would render a row the store
#: cannot produce.
OBJECTIVE = an_item(item_id="objective:6", kind="objective", rank=7, tier=1,
                    why="the store records an option for this item",
                    title="[SYNTHETIC] A10: an intake item awaiting triage")


class StubAttentionPort:
    """The read port, and it has no write method to call by accident."""
    evidence_label = 'Fixture evidence'

    def __init__(self, items=None):
        self._items = list(ROWS if items is None else items)

    def queue(self):
        return QueueView.ranked(self._items, checked_at=NOW)

    def item(self, item_id):
        for candidate in self._items:
            if candidate.item_id == item_id:
                return candidate
        return None


def an_app(port=None):
    """A real Flask app and a real Jinja environment, with no console and no store.

    `guard.attach` is what supplies `csrf_for`, and it is the console's own implementation rather
    than a stub: a test that mints its own tokens could not tell a room-scoped token from a
    constant, which is the whole of E8.
    """
    app = Flask(__name__, template_folder=os.path.join(WEB_DIR, "templates"),
                static_folder=os.path.join(WEB_DIR, "static"))
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


ROW_RE = re.compile(r'<tr[^>]*data-queue-row="(?P<id>[^"]+)"(?P<rest>.*?)</tr>', re.S)
FORM_RE = re.compile(r'<form\b[^>]*>', re.S)
ACTION_RE = re.compile(r'action="([^"]*)"')
CSRF_RE = re.compile(r'name="csrf"\s+value="([^"]*)"')

#: R2's F6 on X7. A failing `assertIn(..., self.row)` prints the whole rendered `<form>` into the
#: runner's output, and one of that form's fields is the room's minted CSRF token. On a scratch
#: database with a test-client session the value is harmless -- which is why F6 is a note -- but a
#: failing assertion is the one path in this lane that writes a token into a log nobody scans,
#: and every browser tester here runs a token scanner over its transcripts precisely because that
#: shape matters. `setUpClass` in `TheWriteDoorRefuses` already models the habit: it prints "room
#: token scraped off the page (session bound, value not printed)".
#:
#: WHAT SURVIVES REDACTION IS WHAT THE ASSERTIONS ARE ABOUT: the field's prefix, which names the
#: room the token was minted for, and its LENGTH CLASS. Neither is the secret, and both are what a
#: person reading a failure needs -- "the token is there, it says `attention.`, it is 51 characters
#: long" answers every question the markup raises without answering the one it must not.
TOKEN_VALUE_RE = re.compile(r'(?P<head>name="csrf"\s+value=")(?P<value>[^"]*)(?P<tail>")')


def token_redacted(html):
    """The same HTML with every csrf value replaced by its prefix and its length class.

    Used for the copy of a row or a page that an ASSERTION MESSAGE may print. The raw copy is kept
    beside it for the one test that is actually about the token, which reads the prefix off the
    real value and reports a length rather than the value when it fails.
    """
    def _mask(found):
        value = found.group("value")
        prefix = value.split(".")[0] + "." if "." in value else ""
        return "%s%s<redacted, %d chars>%s" % (found.group("head"), prefix, len(value),
                                               found.group("tail"))
    return TOKEN_VALUE_RE.sub(_mask, html)


def length_class(value):
    """A token's shape without its value, for an assertion message. Never the value itself.

    THE DOTLESS CASE IS THE ONE THAT MATTERS. `value.split(".")[0]` is the room prefix on a real
    token and is the WHOLE VALUE on a token that carries no dot, so a naive prefix would print the
    secret in exactly the case where the assertion is failing. A token with no dot has no prefix
    to report and this says so.
    """
    return "%d chars, prefix %r" % (len(value),
                                    value.split(".")[0] + "." if "." in value else "<none>")


def rows_of(body):
    return {m.group("id"): m.group("rest") for m in ROW_RE.finditer(body)}


def forms_in(html):
    return FORM_RE.findall(html)


class WhatEachKindRenders(unittest.TestCase):
    """E7, E8, E10, E11 over the rendered page."""

    @classmethod
    def setUpClass(cls):
        cls.body = an_app().test_client().get("/attention/").get_data(as_text=True)
        cls.rows = rows_of(cls.body)
        print("\n  DENOMINATORS, rendered page")
        print("    rows rendered                 %d of %d supplied" % (len(cls.rows), len(ROWS)))
        print("    forms found on the page       %d" % len(forms_in(cls.body)))
        print("    distinct form actions         %s"
              % sorted(set(ACTION_RE.findall(" ".join(forms_in(cls.body))))))

    def test_the_page_renders_every_fixture_row(self):
        # The denominator before every claim below. A control assertion over four rows that were
        # never rendered would pass for the wrong reason.
        self.assertEqual(len(self.rows), len(ROWS), self.rows.keys())

    def test_a_review_row_carries_exactly_one_accept_work_form_on_the_bare_id(self):
        row = self.rows["work_item:w-review"]
        self.assertEqual(len(forms_in(row)), 1)
        self.assertIn('name="action" value="accept_work"', row)
        self.assertIn('name="id" value="w-review"', row)
        self.assertNotIn('value="work_item:w-review"', row.split("<form")[1])
        self.assertIn("Accept work", row)

    def test_a_human_row_carries_one_mark_done_form_with_a_summary_field(self):
        row = self.rows["work_item:w-human"]
        self.assertEqual(len(forms_in(row)), 1)
        self.assertIn('name="action" value="mark_done"', row)
        self.assertIn('name="id" value="w-human"', row)
        self.assertIn('name="text"', row)
        self.assertIn("Mark done", row)

    def test_the_done_button_starts_disabled_and_the_gate_is_the_servers_number(self):
        # E11. The gate exists so the common case never round-trips; the refusal for a short
        # summary is still the server's sentence.
        row = self.rows["work_item:w-human"]
        self.assertIn('data-att-min="%d"' % MIN_REASON, row)
        self.assertRegex(row, r'<button[^>]*disabled')

    def test_a_question_row_carries_one_answer_form_with_a_text_field(self):
        row = self.rows["question:q-1"]
        self.assertEqual(len(forms_in(row)), 1)
        self.assertIn('name="action" value="answer"', row)
        self.assertIn('name="id" value="q-1"', row)
        self.assertIn('name="text"', row)

    def test_a_recommendation_without_a_native_option_says_what_is_missing(self):
        # This specimen has no native option; an open proposal is tested separately.
        row = self.rows["recommendation:r-1"]
        self.assertEqual(len(forms_in(row)), 0)
        self.assertIn("no available proposal decision", row)

    def test_an_unreadable_row_carries_no_control_and_says_what_is_missing(self):
        row = self.rows["work_item:w-restricted"]
        self.assertEqual(len(forms_in(row)), 0)
        self.assertIn("No act here", row)
        self.assertIn("never indexed", row)

    def test_every_form_on_the_page_posts_to_the_one_write_door(self):
        # E7, and the denominator is printed above: a page with zero forms would satisfy a subset
        # assertion and prove nothing, so the count is asserted non-zero first.
        forms = forms_in(self.body)
        self.assertEqual(len(forms), 3, "three rows of five may act")
        actions = sorted(set(ACTION_RE.findall(" ".join(forms))))
        self.assertEqual(actions, [DOOR])

    def test_zero_forms_post_anywhere_else(self):
        forms = forms_in(self.body)
        elsewhere = [f for f in forms if ACTION_RE.search(f) and
                     ACTION_RE.search(f).group(1) != DOOR]
        self.assertEqual(elsewhere, [], "%d of %d forms post elsewhere" % (len(elsewhere),
                                                                          len(forms)))

    def test_every_form_carries_this_rooms_token(self):
        # E8. `guard.token_for` mints `<room>.<issued>.<mac>`, so the room is legible in the value
        # and a queue token on this page would be visible here rather than only at the door.
        tokens = CSRF_RE.findall(self.body)
        self.assertEqual(len(tokens), 3)
        for token in tokens:
            # The message is the token's LENGTH CLASS and prefix, never its value. R2's F6 on X7
            # named line 747's diff; this line was the same defect stated outright, and the guard
            # at the foot of this file is what found it.
            self.assertTrue(token.startswith("attention."), length_class(token))

    def test_the_word_dismiss_appears_nowhere_and_inspect_appears_once_per_row(self):
        # R36: `dismiss` is not granted by implication and is not a word on this page. The zero is
        # only evidence beside a positive control, and `Inspect` is it: the same instrument over
        # the same bytes finds one per row.
        self.assertEqual(self.body.lower().count("dismiss"), 0)
        self.assertEqual(self.body.count(">Inspect<"), len(ROWS))

    def test_the_table_declares_an_act_column(self):
        self.assertIn("<th>Act</th>", self.body)


#: The sentence X1 (`b3e1304`) rendered after a `done`, kept VERBATIM as the positive control for
#: the phrase scan below. It is a scratch string in this file and nothing renders it: its whole job
#: is to prove that `phrase_count` can find "reload to see" when it is there, so the zero the real
#: sentence scores is an absence rather than a broken instrument.
X1_DONE_SENTENCE = (
    "This row has not left your queue: it moves to Accept work, as finished work "
    "awaiting your acceptance. Reload to see it there. This page has not re-read the "
    "store -- the reload is the read."
)

#: The sentence X2 (`503f2dc`) rendered after a `done`, kept VERBATIM so that packet ATT-1h, which
#: recomposed it out of a shared caveat constant, has to prove it did not change a syllable of what
#: a person reads. Clearing residue is not a licence to re-word what was already measured.
X2_DONE_SENTENCE = (
    "Your done is written. This row is now finished work awaiting your acceptance in the store, "
    "not gone. This page shows a ranked window of the queue, so the row may sit outside it and "
    "not appear here on a reload; the row’s own inspector does not depend on the window."
)

#: The phrase E6-as-amended forbids any stripe on this page to carry, lowercased at the point of
#: use. ATT-1f scoped this to the `done` sentence; ATT-1h scans the whole page and both templates.
FORBIDDEN_PHRASE = "reload to see"

INSPECT_ATTR_RE = re.compile(r'data-att-inspect="(?P<url>[^"]*)"')
#: The RENDERED aside only, which is a narrower thing than the row: the row also carries
#: `data-att-inspect` and the template carries comments, and an assertion about what a person is
#: pointed at must read the sentence they see and not the markup around it. R2's F4.
ASIDE_RE = re.compile(r'<p class="att-aside">(?P<text>.*?)</p>', re.S)
UNDO_FORM_RE = re.compile(r"function undoForm\(undo\) \{(?P<body>.*?)\n  \}\n", re.S)
JS_LITERAL_RE = re.compile(r"'([^']*)'")


def phrase_count(text, phrase):
    """One instrument, used on the real sentences and on the control, so a zero means something."""
    return text.lower().count(phrase)


def template_source(name):
    """The template as it sits on disk, Jinja comments and all."""
    with open(os.path.join(TEMPLATE_DIR, name), "r", encoding="utf-8") as handle:
        return handle.read()


def const_source(body, name):
    """The SOURCE of one `var NAME = ...;` in the page's script. Not its evaluated value."""
    found = re.search(r"var %s\s*=\s*(?P<body>.*?);\s*\n" % name, body, re.S)
    assert found, ("the rendered page carries no `var %s = ...;`, so any scan of it would be "
                   "vacuous" % name)
    return found.group("body")


def resolved_sentence(body, name):
    """One moved-sentence's source with `WINDOW_CAVEAT` substituted in.

    The three moved-sentences share one clause BY CONSTRUCTION (packet ATT-1h), so scanning a
    single constant's source alone would miss the half of the sentence that carries the caveat --
    and would score a vacuous 0 for the forbidden phrase. This is textual substitution over source
    rather than evaluation of the page's JavaScript; `js_string_value` below asserts the shape that
    makes that sound rather than assuming it.
    """
    text = const_source(body, name)
    if "WINDOW_CAVEAT" in text:
        text = text.replace("WINDOW_CAVEAT", const_source(body, "WINDOW_CAVEAT"))
    return text


def js_string_value(source):
    """The text a person reads, out of a source expression that is string literals joined by `+`.

    Sound only because these constants are exactly that, and because the page writes every
    apostrophe inside them as U+2019 rather than as the quote character. Both are ASSERTED here --
    anything left over after the literals are removed, other than `+` and whitespace, means this
    reading is a guess and the caller gets an error instead of a plausible wrong string.
    """
    residue = JS_LITERAL_RE.sub("", source).replace("+", "").strip()
    assert residue == "", (
        "this constant is not a plain join of string literals, so reading it as one would be a "
        "guess; residue: %r" % residue)
    return "".join(JS_LITERAL_RE.findall(source))


class TheDoneStripeDoesNotPromiseTheWindow(unittest.TestCase):
    """E6 as amended 2026-09-10 19:10Z by `ATTENTION-OS-admiral`, after T2 measured X1.

    T2's evidence: `artefacts/2026-09-10/ATTENTION-OS-admiral/evidence/ATT-1e-measurement-b3e1304.md`
    (E6 row). After a `done` the store moved exactly as X1's stripe said -- `state=done`,
    `accepted_at` NULL, which is arm 1 / Accept work -- and the reloaded page STILL did not show
    the row, because the view is a ranked window (7 of 99 on that fixture) and the row ranked
    outside it. So "Reload to see it there" was falsified by the reload.

    WHAT THIS TEST CAN SEE. The string the person reads and the link the page hands them, both in
    the rendered bytes. It does not drive a browser, so it cannot say the stripe appeared or that
    the link resolves; that is the tester's. It CAN say the promise is gone and the link is there.

    THE SCOPE OF THIS CLASS'S SCAN IS STILL ONE SENTENCE, and that is now a division of labour
    rather than a gap. A5 wrote here that the scan was scoped to `done` because `reopen` and
    `unaccept work` still carried the phrase and a page-wide 0 would have been red for a reason
    this expectation is not about. Packet ATT-1h cleared those two, so the page-wide scan lives in
    `NoStripeOnThisPagePromisesTheWindow` below and this class keeps saying the narrower thing it
    was built to say: THIS sentence, and the link on THIS control.
    """

    @classmethod
    def setUpClass(cls):
        cls.body = an_app().test_client().get("/attention/").get_data(as_text=True)
        cls.sentence = resolved_sentence(cls.body, "DONE_MOVED_SENTENCE")
        cls.rows = rows_of(cls.body)
        print("\n  DENOMINATORS, the done stripe's sentence")
        print("    sentence source captured      %d characters" % len(cls.sentence))
        print("    '%s' in the sentence          %d" % (FORBIDDEN_PHRASE,
                                                        phrase_count(cls.sentence,
                                                                     FORBIDDEN_PHRASE)))
        print("    '%s' in the X1 control        %d" % (FORBIDDEN_PHRASE,
                                                        phrase_count(X1_DONE_SENTENCE,
                                                                     FORBIDDEN_PHRASE)))
        print("    data-att-inspect on the page  %d" % len(INSPECT_ATTR_RE.findall(cls.body)))

    def test_the_instrument_finds_the_phrase_where_it_really_is(self):
        # The positive control, FIRST, so the zero below is read after the scanner has been shown
        # to work. A phrase scan that can only ever return 0 is not evidence of anything.
        self.assertEqual(phrase_count(X1_DONE_SENTENCE, FORBIDDEN_PHRASE), 1,
                         "the phrase scanner cannot find the phrase in the sentence that "
                         "demonstrably carried it, so its zero below means nothing")

    def test_the_done_sentence_does_not_promise_a_reload_will_show_the_row(self):
        # The captured source is non-trivial first: a regex that matched an empty group would
        # score 0 for the wrong reason.
        self.assertGreater(len(self.sentence), 80, self.sentence)
        self.assertEqual(phrase_count(self.sentence, FORBIDDEN_PHRASE), 0, self.sentence)

    def test_the_done_sentence_is_still_the_one_x2_was_measured_on(self):
        # Packet ATT-1h recomposed this constant out of `WINDOW_CAVEAT`, which `reopen` and
        # `unaccept work` now share. That is a refactor, and a refactor that moved a syllable of
        # what the person reads would be a re-wording nobody authorised. Byte equality, not
        # "contains".
        self.assertEqual(js_string_value(self.sentence), X2_DONE_SENTENCE)

    def test_the_done_sentence_says_what_the_store_now_holds(self):
        # E6 as amended: the row is finished work awaiting acceptance, and the page says the
        # window is a window rather than the queue.
        self.assertIn("finished work awaiting your acceptance", self.sentence)
        self.assertIn("ranked window", self.sentence)

    def test_the_done_branch_renders_that_named_sentence_and_no_other(self):
        # The constant is only evidence if it is what the `done` branch actually returns. Without
        # this, a second sentence could sit in `moved()` and the scan above would never see it.
        self.assertRegex(self.body,
                         r"if \(verb === 'done'\) \{\s*return DONE_MOVED_SENTENCE;\s*\}")

    def test_the_done_control_carries_its_rows_own_inspector(self):
        # The link E6-as-amended asks for, rendered SERVER-SIDE with the full `item_id`, on the
        # one row kind that can be marked done.
        row = self.rows["work_item:w-human"]
        self.assertIn('data-att-inspect="/attention/work_item:w-human"', row)
        # And it is the done control that carries it, not some other element in the row.
        self.assertRegex(row, r'<form[^>]*data-att-verb="done"[^>]*data-att-inspect="/attention/')

    def test_every_act_control_carries_one_inspector_url_and_none_carries_a_window(self):
        # ATT-1f put the attribute on the `done` form alone, because that was the only stripe that
        # needed it. ATT-1h widened it to every act form so the `unaccept work` stripe -- whose
        # form this page BUILDS, out of a response with no item id in it -- can inherit one. The
        # count is asserted against the number of forms rather than against a literal, so a fourth
        # act control added later without an id is red here rather than linkless in a browser.
        urls = INSPECT_ATTR_RE.findall(self.body)
        forms = forms_in(self.body)
        self.assertEqual(len(urls), len(forms),
                         "%d inspector urls for %d act forms" % (len(urls), len(forms)))
        self.assertEqual(len(urls), 3, urls)
        for url in urls:
            self.assertTrue(url.startswith("/attention/"), url)
            self.assertIn(":", url, "the inspector path takes the full <source_type>:<source_id>")
            self.assertNotIn("?", url, "the link must not carry the window it is meant to escape")

    def test_the_stripe_reads_the_link_off_the_form_and_puts_it_in_an_anchor(self):
        # The wiring, in the script the page ships. Three statements, each of which would silently
        # drop the link if it went missing. `closest` rather than `getAttribute` is ATT-1h's one
        # word: a row's own form matches itself, an undo form matches the stripe it sits in.
        self.assertIn("form.closest('[data-att-inspect]')", self.body)
        self.assertIn("whereLink.setAttribute('href', inspectUrl)", self.body)
        self.assertIn("ROW_INSPECT_LABEL", self.body)


class NoStripeOnThisPagePromisesTheWindow(unittest.TestCase):
    """E6 as amended, PAGE-WIDE. Packet ATT-1h, author A6, 2026-09-10 23:0xZ.

    ATT-1f cleared `done` and DECLARED what it had not cleared (`ATT-1f-x2.md`, section 2): the
    stripes for `reopen` -- the undo of a person's own `done` -- and for `unaccept work` still said
    a reload would put the row in front of them. The ranked window falsifies that for exactly the
    reason it falsified it for `done`: a reopened row re-enters arm 2 of `queue/schema/0017` and an
    unaccepted row re-enters arm 1, and NEITHER is guaranteed to rank into the window (7 of 99 on
    T2's fixture, and `?filter=review` and `?filter=done` both read "0 of 0 shown").

    So the scan here is not scoped to a sentence. It counts the phrase over the WHOLE rendered page
    and over the WHOLE source of both templates, Jinja comments included -- which is why the two
    comments that quoted X1's wording had to be reworded rather than left as prose nobody renders.
    A scan a comment can turn red is a scan that stays honest when the next author quotes the
    phrase back into the file to explain why it is forbidden.

    THE ZERO IS ONLY EVIDENCE BESIDE THE ONE. `X1_DONE_SENTENCE` is A5's positive control, kept
    verbatim as a scratch string that nothing renders, and `phrase_count` -- the same instrument
    that scores the zeroes -- finds the phrase in it. That test is asserted first and its number is
    printed beside every zero.

    WHAT THIS CLASS CANNOT SEE: it drives no browser, so it cannot say a stripe appeared, that an
    undo posted from one inherited the id, or that the inspector resolved for the row. It reads the
    strings a person would be shown and the wiring that would show them.
    """

    @classmethod
    def setUpClass(cls):
        cls.body = an_app().test_client().get("/attention/").get_data(as_text=True)
        cls.templates = {name: template_source(name) for name in TEMPLATE_FILES}
        cls.reopen = resolved_sentence(cls.body, "REOPEN_MOVED_SENTENCE")
        cls.unaccept = resolved_sentence(cls.body, "UNACCEPT_MOVED_SENTENCE")
        print("\n  DENOMINATORS, the phrase page-wide")
        print("    rendered page                     %d characters" % len(cls.body))
        for name in TEMPLATE_FILES:
            print("    %-33s %d characters" % (name + " source", len(cls.templates[name])))
        print("    '%s' in the rendered page  %d"
              % (FORBIDDEN_PHRASE, phrase_count(cls.body, FORBIDDEN_PHRASE)))
        for name in TEMPLATE_FILES:
            print("    '%s' in %-19s %d"
                  % (FORBIDDEN_PHRASE, name, phrase_count(cls.templates[name],
                                                          FORBIDDEN_PHRASE)))
        print("    '%s' in the X1 control     %d"
              % (FORBIDDEN_PHRASE, phrase_count(X1_DONE_SENTENCE, FORBIDDEN_PHRASE)))
        print("    reopen sentence resolved          %d characters" % len(cls.reopen))
        print("    unaccept sentence resolved        %d characters" % len(cls.unaccept))
        print("    data-att-inspect on the page      %d"
              % len(INSPECT_ATTR_RE.findall(cls.body)))

    def test_the_instrument_still_finds_the_phrase_where_it_really_is(self):
        # First, and asserted rather than only printed: every zero below is read after the scanner
        # has been shown to return a one over the sentence that demonstrably carried the phrase.
        self.assertEqual(phrase_count(X1_DONE_SENTENCE, FORBIDDEN_PHRASE), 1,
                         "the phrase scanner cannot find the phrase in X1's own sentence, so its "
                         "zeroes below mean nothing")

    def test_there_is_something_to_scan(self):
        # A verdict over an empty set is not a pass. The page has to have rendered its script and
        # both templates have to have been read before their zeroes are worth anything.
        self.assertGreater(len(self.body), 5000, "the page barely rendered")
        self.assertIn("var DONE_MOVED_SENTENCE", self.body)
        self.assertIn("var REOPEN_MOVED_SENTENCE", self.body)
        self.assertIn("var UNACCEPT_MOVED_SENTENCE", self.body)
        for name in TEMPLATE_FILES:
            self.assertGreater(len(self.templates[name]), 1000, name)

    def test_the_phrase_appears_nowhere_on_this_page_or_in_the_templates_that_make_it(self):
        # ONE case over three surfaces, deliberately, and it is the only case in this file that
        # owns the phrase page-wide. Three separate cases would turn one planted clause into three
        # reds and make a reader work out which of them is the defect; one dict names the surface
        # in the failure message instead. The template SOURCES are scanned as well as the render
        # because a Jinja comment is stripped before a person sees it and is still the text the
        # next author copies.
        counts = {"rendered page": phrase_count(self.body, FORBIDDEN_PHRASE)}
        for name in TEMPLATE_FILES:
            counts[name] = phrase_count(self.templates[name], FORBIDDEN_PHRASE)
        self.assertEqual(counts, {key: 0 for key in counts}, counts)

    def test_the_reopen_sentence_says_where_the_row_now_is(self):
        # Arm 2: the person's own unfinished task again. The sentence names the store, not the
        # page, and it names the window as a window. It does NOT re-count the forbidden phrase:
        # the case above owns that over every surface at once, and an assertion duplicated across
        # cases only multiplies one defect into several reds.
        self.assertGreater(len(self.reopen), 80, self.reopen)
        text = js_string_value(self.reopen)
        self.assertIn("your own unfinished task again in the store", text)
        self.assertIn("ranked window", text)
        self.assertIn("inspector", text)

    def test_the_unaccept_sentence_says_where_the_row_now_is(self):
        # Arm 1: finished work awaiting acceptance again, and the withdrawal is on the thread
        # rather than erased -- which `web/actions.py::unaccept_work` is what makes true.
        self.assertGreater(len(self.unaccept), 80, self.unaccept)
        text = js_string_value(self.unaccept)
        self.assertIn("finished work awaiting your acceptance in the store again", text)
        self.assertIn("written on the item’s thread rather than erased", text)
        self.assertIn("ranked window", text)

    def test_those_branches_return_those_named_sentences_and_no_other(self):
        # A constant is only evidence if it is what the branch actually returns. Without this, a
        # second sentence could sit in `moved()` and the scans above would never see it.
        self.assertRegex(self.body,
                         r"if \(verb === 'reopen'\) \{\s*return REOPEN_MOVED_SENTENCE;\s*\}")
        self.assertRegex(
            self.body,
            r"if \(verb === 'unaccept work'\) \{\s*return UNACCEPT_MOVED_SENTENCE;\s*\}")

    def test_the_sentences_share_one_caveat_so_they_cannot_drift_apart(self):
        # The residue existed because three sentences said the same thing in three places and only
        # one of them was corrected. One constant is the structural fix, and this is the assertion
        # that it stays one.
        #
        # `OPTION_DISPATCHED_SENTENCE` (D4, packet ATT-8) joins the list because the row it is
        # about IS still in the queue afterwards, so the window caveat is true of it. The fifth
        # D4 sentence, `ACCEPTED_MOVED_SENTENCE`, is deliberately NOT here and is asserted to carry
        # no caveat by the objective class below: an accepted objective has left the arm, so there
        # is no window for a caveat to be about -- the same reason `accept work` carries none.
        for name in ("DONE_MOVED_SENTENCE", "REOPEN_MOVED_SENTENCE", "UNACCEPT_MOVED_SENTENCE",
                     "OPTION_DISPATCHED_SENTENCE"):
            self.assertIn("WINDOW_CAVEAT", const_source(self.body, name), name)
        caveat = js_string_value(const_source(self.body, "WINDOW_CAVEAT"))
        self.assertIn("ranked window", caveat)
        self.assertIn("does not depend on the window", caveat)

    def test_the_inspector_is_offered_after_exactly_the_verbs_the_row_survives(self):
        # `accept work` is absent on purpose: an accepted row leaves `brain.queue_open`, so a link
        # to its inspector would point at a row the inspector can no longer find. The list is the
        # one place that decision is made, and the guard is the one place it is read.
        # `accept` (D4's objective triage) is absent for the same reason as `accept work`;
        # `recommend accept` is present because dispatching an option writes nothing to the
        # objective, so that row is still in the queue and its inspector still resolves.
        self.assertIn(
            "var INSPECTABLE_AFTER = ['done', 'reopen', 'unaccept work', 'recommend accept']",
            self.body)
        self.assertNotIn("'accept work'", const_source(self.body, "INSPECTABLE_AFTER"))
        self.assertNotIn("'accept'", const_source(self.body, "INSPECTABLE_AFTER"))
        self.assertIn("if (inspectUrl && data.subject_type !== 'recommendation' && INSPECTABLE_AFTER.indexOf(data.verb) !== -1)", self.body)

    def test_the_stripe_carries_the_id_forward_and_the_undo_control_is_untouched(self):
        # How a `reopen` or an `unaccept work` gets an inspector at all: the stripe declares the
        # id, and `land()` reads the nearest ancestor that declares one. The undo form itself is
        # built exactly as it was before this packet -- asserted, not asserted-by-memory, because
        # "the undo control is untouched" was a constraint on this packet and a reader deserves to
        # see it checked.
        self.assertIn("if (inspectUrl) tr.setAttribute('data-att-inspect', inspectUrl);", self.body)
        undo = UNDO_FORM_RE.search(self.body)
        self.assertTrue(undo, "the page renders no `undoForm`, so this assertion is vacuous")
        self.assertGreater(len(undo.group("body")), 200)
        self.assertNotIn("data-att-inspect", undo.group("body"),
                         "the id belongs on the stripe, not on the undo control")


class TheClientGateMirrorsTheServer(unittest.TestCase):
    """E11's other half: the number the button uses is the number the verb enforces."""

    def test_the_mirror_equals_the_authority(self):
        self.assertEqual(MIN_REASON_MIRROR, MIN_REASON,
                         "web/views/blueprint.py's mirror and web/actions.py::MIN_REASON have "
                         "diverged; the server's number is the authority.")


class TheViewsPackageCannotWrite(unittest.TestCase):
    """E7's structural half: no write path is reachable from `web/views/**`.

    THE NEEDLES ARE BUILT BY CONCATENATION so that this file, which is itself inside the scanned
    tree, is not its own false positive. A test that had to be excluded from its own scan would
    leave the one directory a write could hide in unscanned.
    """

    NEEDLES = ("store." + "apply(", "rooms." + "dispatch(")

    def _scan(self, path):
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        return [n for n in self.NEEDLES if n in text]

    def _python_files(self):
        found = []
        for base, dirs, names in os.walk(VIEWS_DIR):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            found.extend(os.path.join(base, n) for n in sorted(names) if n.endswith(".py"))
        return sorted(found)

    def test_no_file_under_web_views_calls_a_store_write(self):
        files = self._python_files()
        print("\n    web/views/** python files scanned  %d" % len(files))
        self.assertGreater(len(files), 4, "the scan found nothing to scan")
        offenders = {os.path.relpath(p, WEB_DIR): hits
                     for p in files for hits in [self._scan(p)] if hits}
        self.assertEqual(offenders, {})

    def test_the_scanner_finds_the_write_path_where_it_really_is(self):
        # The positive control beside the zero above. `web/rooms.py` calls `store.apply` and
        # `web/actions.py` calls `rooms.dispatch`; a scanner that misses those proves nothing when
        # it misses them in `web/views/**`.
        self.assertIn(self.NEEDLES[0], self._scan(os.path.join(WEB_DIR, "rooms.py")))
        self.assertIn(self.NEEDLES[1], self._scan(os.path.join(WEB_DIR, "actions.py")))


class TheVerbSetIsExactlyEight(unittest.TestCase):
    """Andrew's 2026-09-12 native proposal integration adds recommend reject explicitly.

    The closed set retains D4's objective acts. Dismiss remains outside the grant.
    """

    RULED = {"answer", "accept work", "unaccept work", "done", "reopen",
             "accept", "recommend accept", "recommend reject"}

    def test_the_attention_room_holds_exactly_the_eight_ruled_verbs(self):
        self.assertEqual(set(rooms.ROOM_VERBS["attention"]), self.RULED)

    def test_dismiss_is_not_in_the_set(self):
        self.assertNotIn("dismiss", rooms.ROOM_VERBS["attention"])

    def test_native_proposal_rejection_is_in_the_explicit_set(self):
        self.assertIn("recommend reject", rooms.ROOM_VERBS["attention"])


class AnObjectiveRowOffersTriageAndNothingElse(unittest.TestCase):
    """D4's act half at the surface: E7, E8, E10 and E4 over a row of arm 5. Packet ATT-8, A10.

    THE FIXTURE IS ADDED BESIDE `ROWS` RATHER THAN INTO IT, and that is a decision about the other
    six classes rather than about this one. `WhatEachKindRenders` asserts exact page-wide counts --
    three forms, three tokens, one `Inspect` per row -- and folding a sixth row into the shared
    list would have moved those numbers as a side effect of this packet, which is how a suite stops
    being able to tell a deliberate change from a regression. This class renders its own page.

    WHAT IT CANNOT SEE, on the same terms as every class above: it drives no browser, so the stripe
    this row's receipt produces, its order, and the absence of a timer are read here as the strings
    and the wiring that would produce them. The acts themselves are proven against a store by the
    integration run in `outputs/2026-09-10-ATTENTION-OS-admiral/ATT-8-objectives-act.md`, which is
    where the receipt text and the refusal sentences were actually watched.
    """

    @classmethod
    def setUpClass(cls):
        # R2's F6. `cls.body` and `cls.row` are the copies every content assertion in this class
        # reads, and a failed assertion prints them -- so they are the REDACTED copies, carrying
        # the token's prefix and length and not its value. `cls.raw_row` is the page exactly as it
        # was served and is read by ONE test, the one whose subject is the token itself; redacting
        # the copy that test reads would have turned it into a test of `token_redacted`.
        served = an_app(StubAttentionPort(ROWS + [OBJECTIVE])).test_client().get(
            "/attention/").get_data(as_text=True)
        cls.raw_row = rows_of(served).get("objective:6", "")
        cls.body = token_redacted(served)
        cls.rows = rows_of(cls.body)
        cls.row = cls.rows.get("objective:6", "")
        print("\n  DENOMINATORS, the objective row")
        print("    rows rendered                 %d of %d supplied"
              % (len(cls.rows), len(ROWS) + 1))
        print("    forms on the page             %d" % len(forms_in(cls.body)))
        print("    forms on the objective row    %d" % len(forms_in(cls.row)))
        print("    '%s' page-wide           %d  (control, X1's own sentence: %d)"
              % (FORBIDDEN_PHRASE, phrase_count(cls.body, FORBIDDEN_PHRASE),
                 phrase_count(X1_DONE_SENTENCE, FORBIDDEN_PHRASE)))
        print("    'dismiss' page-wide           %d  (control, '>Inspect<': %d)"
              % (cls.body.lower().count("dismiss"), cls.body.count(">Inspect<")))

    def test_the_page_rendered_the_objective_row_at_all(self):
        # The denominator before every claim below: a page that dropped the row would make every
        # `assertNotIn` in this class pass for the wrong reason.
        self.assertEqual(len(self.rows), len(ROWS) + 1, sorted(self.rows))
        self.assertIn("objective:6", self.rows)

    def test_it_carries_exactly_one_accept_objective_form_on_the_bare_id(self):
        self.assertEqual(len(forms_in(self.row)), 1)
        self.assertIn('name="action" value="accept_objective"', self.row)
        self.assertIn('name="id" value="6"', self.row)
        self.assertIn('name="source_type" value="objective"', self.row)
        self.assertIn("Accept objective", self.row)

    def test_the_posted_id_is_the_bare_one_and_the_inspector_url_is_the_typed_one(self):
        # The two ids on one element, and they are deliberately different: the write door matches
        # the bare `source_id`, and `attention.inspect` resolves the typed `item_id` through
        # `port.item`. A row that posted the typed id would be refused as a stale card.
        self.assertNotIn('value="objective:6"', self.row.split("<form")[1])
        found = INSPECT_ATTR_RE.search(self.row)
        self.assertTrue(found, "the objective control carries no data-att-inspect")
        self.assertIn("objective:6", found.group("url"))

    def test_the_control_posts_to_the_one_door_with_this_rooms_token(self):
        # E7 and E8 on this row. The token names the room it was minted for, so a queue token
        # here would be visible in the value rather than only at the door.
        #
        # THE ONE TEST IN THIS CLASS THAT READS THE UNREDACTED ROW, and the only one that needs
        # to: its subject is the token's own prefix. F6's rule still holds inside it -- the
        # failure message reports the token's LENGTH CLASS and the prefix it really carries, and
        # never the value, which is why `length_class` exists and why `token.group(1)` is not
        # passed as the message the way it was at X7.
        form = forms_in(self.row)[0]
        self.assertEqual(ACTION_RE.search(form).group(1), DOOR)
        token = CSRF_RE.search(self.raw_row)
        self.assertTrue(token, "the objective control carries no token")
        self.assertTrue(token.group(1).startswith("attention."), length_class(token.group(1)))

    def test_the_row_says_its_options_are_recorded_and_not_choosable_here(self):
        # E10's rule applied to the half of D4 this view cannot render: the port hands it
        # `has_option` and never the option rows, so the page says the proposals are recorded and
        # not shown here rather than inventing labels. The sentence sits BESIDE a working control,
        # so it must not wear the class the "No act here" sentences wear.
        #
        # RENAMED AND REWRITTEN FOR R2's F4 ON X7 (packet ATT-1q, author A16). The old name was
        # `..._says_where_its_options_are_chosen_...` and it asserted the old sentence, "chosen one
        # at a time from the row's own inspector, not from here" -- a destination that renders no
        # option. A test that pins a false sentence is the thing that keeps it true, so the
        # assertion moved with the template rather than being left to pass over it.
        self.assertIn("att-aside", self.row)
        # The phrases are each kept inside one source line on purpose: the template wraps, and an
        # assertion that spans its line break would be a test of the indentation.
        self.assertIn("are not shown on this page", self.row)
        self.assertIn("this page is handed whether an option exists", self.row)
        self.assertIn("not an act", self.row)
        self.assertNotIn("No act here", self.row)

    def test_the_aside_names_no_page_that_cannot_render_an_option(self):
        # R2's F4, as a guard rather than as a wording preference: the aside must not send a
        # person anywhere, because nowhere on this console lists an objective's proposals today.
        # Native task actions now appear in the inspector; objective proposals still do not.
        aside = ASIDE_RE.search(self.row)
        self.assertTrue(aside, "the objective row renders no aside at all")
        text = aside.group("text")
        self.assertNotIn("inspector", text)
        self.assertNotIn("chosen one at a time from the", text)
        # TWO-WAY CONTROL ON BOTH READERS, because both claims above are zeros. The word is in
        # this template's own comments, so a reader that cannot find it there would report an
        # absence it is incapable of seeing; and the inspector really does render no option.
        self.assertIn("inspector", template_source("controls.html"))
        client = an_app(StubAttentionPort(ROWS + [OBJECTIVE])).test_client()
        objective = token_redacted(client.get('/attention/objective:6').get_data(as_text=True))
        human = token_redacted(client.get('/attention/work_item:w-human').get_data(as_text=True))
        self.assertIn('Result', objective)
        self.assertNotIn('Available actions', objective)
        self.assertNotIn('dispatch_option', objective)
        self.assertIn('Available actions', human)

    def test_the_whole_page_still_posts_to_one_door_and_nowhere_else(self):
        forms = forms_in(self.body)
        self.assertEqual(len(forms), 4, "four rows of six may act")
        self.assertEqual(sorted(set(ACTION_RE.findall(" ".join(forms)))), [DOOR])

    def test_the_forbidden_phrase_is_absent_page_wide_with_its_control(self):
        # E6 as amended, re-measured over the page THIS class renders rather than inherited from
        # the class next door: the objective row adds a sentence and a stripe branch, and a zero
        # measured before they existed says nothing about them.
        self.assertEqual(phrase_count(X1_DONE_SENTENCE, FORBIDDEN_PHRASE), 1,
                         "the instrument cannot find the phrase where it really is")
        self.assertEqual(phrase_count(self.body, FORBIDDEN_PHRASE), 0)

    def test_the_word_dismiss_is_absent_page_wide_with_its_control(self):
        # R36 and D4 both: `dismiss` is not granted by implication. The zero is only evidence
        # beside a positive control over the same bytes.
        self.assertEqual(self.body.count(">Inspect<"), len(ROWS) + 1)
        self.assertEqual(self.body.lower().count("dismiss"), 0)

    def test_the_accept_branch_returns_the_named_sentence_and_it_carries_no_window_caveat(self):
        # E5 for this verb, and the absence of the caveat is the claim: an accepted objective
        # leaves arm 5 outright, so there is no window for a caveat to be about. `accept work`
        # carries none for the same reason.
        self.assertRegex(self.body,
                         r"if \(verb === 'accept'\) \{\s*return ACCEPTED_MOVED_SENTENCE;\s*\}")
        source = const_source(self.body, "ACCEPTED_MOVED_SENTENCE")
        self.assertNotIn("WINDOW_CAVEAT", source)
        text = js_string_value(source)
        self.assertIn("accepted in the store", text)
        self.assertIn("has left the waiting arm", text)

    def test_the_option_dispatch_branch_says_the_row_did_not_move(self):
        # D4's structural sentence, which is the one a person cannot check for themselves:
        # `recommend accept` posts the task and writes nothing to `brain.objective`.
        self.assertRegex(
            self.body,
            r"if \(verb === 'recommend accept'\) \{\s*return OPTION_DISPATCHED_SENTENCE;\s*\}")
        # `resolved_sentence`, not `const_source`: this one ends in `+ WINDOW_CAVEAT`, which the
        # literal reader refuses to guess at. The caveat belongs on it -- the objective is still
        # in the queue after a dispatch -- so the substituted form is what a person reads.
        text = js_string_value(resolved_sentence(self.body, "OPTION_DISPATCHED_SENTENCE"))
        self.assertIn("has NOT moved", text)
        self.assertIn("not the same act as filing it", text)
        self.assertIn("ranked window", text)

    def test_the_room_holds_the_two_verbs_this_row_needs_and_not_the_word_it_does_not(self):
        # The surface above is only honest if the room really permits what the buttons name. The
        # set itself is pinned by `TheVerbSetIsExactlySeven`; this is the per-row half of it.
        self.assertIn("accept", rooms.ROOM_VERBS["attention"])
        self.assertIn("recommend accept", rooms.ROOM_VERBS["attention"])
        self.assertNotIn("dismiss", rooms.ROOM_VERBS["attention"])


class TheWriteDoorRefuses(unittest.TestCase):
    """E8 and E9 through the REAL app: the token rule and the stale card.

    This is the one class that needs a database, because `web/app.py::create_app` reads the store
    at startup to say which human this console is acting as.
    """

    @classmethod
    def setUpClass(cls):
        from web.app import create_app
        cls.app = create_app()
        cls.client = cls.app.test_client()

        # THE TOKEN IS SCRAPED OFF THE PAGE THE CONSOLE SERVED, not minted beside it. A token this
        # test manufactured would prove that `token_for` agrees with `check_token`, which is a fact
        # about `web/guard.py` and not about this surface. This way the value under test is the one
        # the browser would actually send, and the session it is bound to is the one the console
        # set on the same request (`guard.attach`'s `after_request` mints `vd_sid` on first sight).
        page = cls.client.get("/attention/")
        assert page.status_code == 200, "the attention page did not render: %s" % page.status_code
        body = page.get_data(as_text=True)
        found = re.search(r'data-att-csrf="([^"]+)"', body)
        assert found, "the attention page rendered no room token to post with"
        cls.attention_token = found.group(1)
        sid = re.search(r"%s=([^;]+);" % guard.SESSION_COOKIE,
                        page.headers.get("Set-Cookie") or "")
        assert sid, "the console set no session cookie, so no token can be bound to one"
        cls.sid = sid.group(1)

        # The other room's token, minted for THE SAME browser, which is what makes the refusal
        # below a fact about the ROOM rather than about the session.
        with cls.app.test_request_context(
                "/", headers={"Cookie": guard.SESSION_COOKIE + "=" + cls.sid}):
            cls.queue_token = guard.token_for("queue")
            same = guard.token_for("attention")
        assert same == cls.attention_token, (
            "the token minted for this session does not match the one the page rendered; the "
            "scrape and the mint disagree and neither can be trusted")
        print("\n    database under test               %s" % os.environ.get("BRAIN_PG_DB"))
        print("    room token scraped off the page   yes (session bound, value not printed)")

    def _post(self, form):
        # No Cookie header: the test client is carrying the console's own session cookie from the
        # GET in `setUpClass`, exactly as a browser would.
        return self.client.post(DOOR, data=form, headers={"Origin": "http://localhost"})

    def test_a_queue_token_at_the_attention_door_is_refused(self):
        # E8. The token names the room it was minted for, and the door reads that name rather than
        # a header the request chose.
        answer = self._post({"csrf": self.queue_token, "action": "accept_work", "id": "w-review"})
        self.assertEqual(answer.status_code, 403)
        body = answer.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["kind"], "write-refusal")
        self.assertIn("'queue'", body["error"])

    def test_the_rooms_own_token_gets_past_the_identity_guard(self):
        # The control for the refusal above: the SAME request with the attention token reaches a
        # different guard and answers with a different sentence. Without this, the 403 above is
        # evidence about CSRF in general and not about the room binding.
        answer = self._post({"csrf": self.attention_token, "action": "accept_work",
                             "id": "no-such-row-att-1d"})
        self.assertNotEqual(answer.status_code, 403)

    def test_an_unknown_id_is_refused_as_a_stale_card(self):
        # E9. 400 and the server's own sentence, which the page renders in place beside the
        # control that posted it.
        answer = self._post({"csrf": self.attention_token, "action": "accept_work",
                             "id": "no-such-row-att-1d"})
        self.assertEqual(answer.status_code, 400)
        body = answer.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["kind"], "refused")
        self.assertIn("is not in the queue any more", body["error"])

    def test_an_objective_that_is_not_on_this_queue_is_refused_in_words(self):
        # D4's door, through the real app. This case holds on BOTH stores and says something
        # different on each, which is why it is here rather than in the stub class:
        #
        #   * on a store WITHOUT the arm's migration there is no objective on this queue at all,
        #     so this is the refusal every objective gets;
        #   * on a store WITH it, this id is one no `brain.objective` row holds, and the same
        #     sentence is what a SECOND accept of an already-accepted row gets -- the row has left
        #     arm 5, so the keyed read comes back empty.
        #
        # The id is nine digits so no sequence on either store will ever mint it.
        answer = self._post({"csrf": self.attention_token, "action": "accept_objective",
                             "id": "999000777", "source_type": "objective"})
        self.assertEqual(answer.status_code, 400)
        body = answer.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["kind"], "refused")
        self.assertIn("there is no objective", body["error"])
        # And it names the typed id it actually looked up, so a reader can go and run the same
        # read rather than take the refusal's word for it.
        self.assertIn("objective:999000777", body["error"])

    def test_a_post_with_no_token_is_refused_in_words(self):
        answer = self.client.post(DOOR, data={"action": "accept_work", "id": "w-review"},
                                  headers={"Origin": "http://localhost"})
        self.assertEqual(answer.status_code, 403)
        self.assertIn("CSRF", answer.get_json()["error"])


#: `_find`'s own sentence, and the whole instrument of the class below. The two refusals come from
#: different code paths and say different things, so the sentence that comes back NAMES THE BRANCH
#: THAT RAN -- which is how R2 measured F1 without needing a colliding fixture row to exist.
FIND_SENTENCE = "is not in the queue any more"
#: Nine digits, so no sequence on either store will ever mint it. Every cell below is refused
#: before any verb dispatches and nothing is written.
UNMINTABLE_ID = "999000777"
APP_PY = os.path.join(WEB_DIR, "app.py")


class TheAttentionDoorResolvesOnlyByTypedIdentity(unittest.TestCase):
    """R2's F1 and F2 on X7, held at the door: NO form on this room reaches `_find`.

    Packet ATT-1q, author A16, 2026-09-11, against `REVIEW-x7-40a6bfb.md` sections 7.5 and 8.

    WHAT WENT WRONG AND WHY A TEST HAD TO HOLD IT. A10's branch took `dispatch_option` only when
    the form declared `source_type=objective`, so the typed-lookup guarantee -- the thing four
    paragraphs of `_perform_objective`'s docstring exist to state -- was conditional on a field the
    CLIENT chose to send. R2 measured four cells through the real app: `source_type` absent reached
    `_find`, `source_type=work_item` reached `_find`, and only the two declaring `objective` (plus
    `accept_objective`, which is keyed on the action name) reached the typed read. No control on
    this surface posts `dispatch_option` today, so the exposure was future rather than live -- but
    it rested on ONE hidden field, whose deletion R2's own plant showed goes red in exactly one
    assertion in this file. A default that has to be re-sent on every future form is not a
    guarantee; the branch now takes every `dispatch_option` on this room and refuses in words.

    THE INSTRUMENT IS THE SENTENCE, NOT THE STATUS. Every cell here is a 400, so a status code
    cannot tell the branches apart. `_find`'s refusal says "is not in the queue any more" and the
    typed path says "there is no objective"; the branch that ran is therefore readable off the
    words a person would have seen, which is the same reading R2 took. `test_the_find_sentence_is
    _still_reachable_on_this_room` is the control that makes every absence below a verdict: an
    instrument that could never print `_find`'s sentence would report `_find` unreached on a door
    that reached it every time.

    NOTHING HERE NEEDS AN ARM. On a store without arm 5's migration every objective read comes
    back empty, which is precisely the refusal these cells assert; on a store with it, the id is
    one no `brain.objective` row holds and the sentence is the same. Both stores, one expectation.
    """

    @classmethod
    def setUpClass(cls):
        from web.app import create_app
        cls.app = create_app()
        cls.client = cls.app.test_client()
        page = cls.client.get("/attention/")
        assert page.status_code == 200, "the attention page did not render: %s" % page.status_code
        found = re.search(r'data-att-csrf="([^"]+)"', page.get_data(as_text=True))
        assert found, "the attention page rendered no room token to post with"
        # Scraped off the page the console served, for the reason `TheWriteDoorRefuses` gives.
        # Session bound, value not printed -- F6's habit applies to this class too.
        cls.attention_token = found.group(1)
        print("\n    database under test               %s" % os.environ.get("BRAIN_PG_DB"))
        print("    room token scraped off the page   yes (session bound, value not printed)")

    def _refusal(self, form):
        form = dict(form, csrf=self.attention_token)
        answer = self.client.post(DOOR, data=form, headers={"Origin": "http://localhost"})
        self.assertEqual(answer.status_code, 400, "this cell was expected to be refused, not run")
        body = answer.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["kind"], "refused")
        return body["error"]

    # ------------------------------------------------------------------ the control, read first
    def test_the_find_sentence_is_still_reachable_on_this_room(self):
        # THE POSITIVE CONTROL FOR EVERY `assertNotIn(FIND_SENTENCE, ...)` BELOW. `accept_work` is
        # not one of this branch's two actions, so it falls through to `_find` exactly as it
        # always did, and the reader prints its sentence. Without this, a typo in FIND_SENTENCE
        # would make every cell below pass while the door routed every post into `_find`.
        self.assertIn(FIND_SENTENCE,
                      self._refusal({"action": "accept_work", "id": "no-such-row-att-1q"}))

    # ------------------------------------------------------- R2's four cells, now all fail-closed
    def test_a_dispatch_option_with_no_source_type_is_refused_by_name(self):
        # R2's cell B, the finding itself. This POST reached `_find` at X7.
        error = self._refusal({"action": "dispatch_option", "id": UNMINTABLE_ID, "option": "1"})
        self.assertIn("source_type", error)
        self.assertIn("carries no", error)
        self.assertNotIn(FIND_SENTENCE, error)

    def test_a_dispatch_option_naming_another_arm_is_refused_by_name(self):
        # R2's cell D. A form that LIES about its source is refused for the source it named,
        # rather than being taken at its word in either direction.
        error = self._refusal({"action": "dispatch_option", "id": UNMINTABLE_ID,
                               "source_type": "work_item", "option": "1"})
        self.assertIn("holds no verb for a row of source", error)
        self.assertIn("work_item", error)
        self.assertNotIn(FIND_SENTENCE, error)

    def test_a_dispatch_option_declaring_objective_reaches_the_typed_read(self):
        # R2's cell A, and the control that the two refusals above are about the DECLARATION and
        # not about `dispatch_option` being refused wholesale on this room.
        error = self._refusal({"action": "dispatch_option", "id": UNMINTABLE_ID,
                               "source_type": "objective", "option": "1"})
        self.assertIn("there is no objective", error)
        self.assertIn("objective:" + UNMINTABLE_ID, error)
        self.assertNotIn(FIND_SENTENCE, error)

    def test_an_accept_objective_with_no_source_type_is_refused_by_name(self):
        # R2's cell C, which reached the typed read at X7 because it is keyed on the action name.
        # It is now refused for the missing field instead, and that is a deliberate tightening
        # rather than a side effect: the control this page renders declares `source_type` on the
        # accept form too, so the only posts this turns away are ones no page produced.
        error = self._refusal({"action": "accept_objective", "id": UNMINTABLE_ID})
        self.assertIn("source_type", error)
        self.assertNotIn(FIND_SENTENCE, error)

    def test_an_accept_objective_naming_another_arm_is_refused_by_name(self):
        error = self._refusal({"action": "accept_objective", "id": UNMINTABLE_ID,
                               "source_type": "question"})
        self.assertIn("holds no verb for a row of source", error)
        self.assertNotIn(FIND_SENTENCE, error)

    def test_both_refusals_land_before_the_port_is_read(self):
        # The refusals name the field, and they do NOT name a typed id -- which is how a reader
        # can tell from the sentence alone that no lookup was paid for. A refusal that quoted
        # `objective:<id>` would be the port's answer, not the gate's.
        for form in ({"action": "dispatch_option", "id": UNMINTABLE_ID},
                     {"action": "accept_objective", "id": UNMINTABLE_ID,
                      "source_type": "recommendation"}):
            error = self._refusal(form)
            self.assertNotIn("objective:" + UNMINTABLE_ID, error)

    # ------------------------------------------------------------------------------ R2's F2
    def test_the_typed_refusal_claims_no_work_item_collision(self):
        # F2. The sentence a person reads during a refusal asserted a collision the lane's own
        # proof measured as NOT CONSTRUCTIBLE: `schema-proposal/PROOF.md` case 8 records that work
        # item ids are zero padded (`0007`) and no integer's text carries a leading zero. The
        # recommendation half is true and is the whole justification; the work item half was not.
        error = self._refusal({"action": "accept_objective", "id": UNMINTABLE_ID,
                               "source_type": "objective"})
        self.assertNotIn("work item", error.lower())
        # And the true reason is stated rather than the false one merely deleted.
        self.assertIn("the intake arm's own key", error)
        self.assertIn("names no arm", error)
        # CONTROL: the reader is looking at a sentence that really is the typed refusal.
        self.assertIn("objective:" + UNMINTABLE_ID, error)

    def test_the_branchs_own_comment_carries_the_same_correction(self):
        # The correction has to reach the comment as well as the string, or the next author copies
        # the claim back out of the comment -- which is the rule this file already applies to the
        # templates it scans as source.
        with open(APP_PY, "r", encoding="utf-8") as handle:
            source = handle.read()
        # COUNTS AND NOT `assertIn`, and the reason is this file's size: an `assertNotIn` over
        # `web/app.py` prints the whole 132 KB module into the runner's output when it fails,
        # which is a log nobody reads and the neighbouring failure buried. Watched happening on
        # this very plant. A count fails as `1 != 0` with the sentence below beside it.
        self.assertEqual(source.count("can equal a work item's"), 0,
                         "the collision PROOF.md case 8 measured as NOT constructible is back in "
                         "web/app.py")
        # CONTROL, two-way: the reader is reading the right file and can find what IS there.
        self.assertGreater(source.count("zero padded"), 0,
                           "the reader cannot find the correction where it really is, so its "
                           "zero above is a fact about the reader")
        self.assertGreater(source.count("PROOF.md"), 0, "this is not web/app.py")


#: A ROW WHOSE KIND `KIND_BY_ARM` DOES NOT PRODUCE. Not a typo and not a future promise: it
#: is the only way to see what the inspector does when the store grows an arm before
#: `web/views/queue.py::KIND_NOW` grows a sentence. `QueueItem` does not validate `kind`, so the
#: port can hand this back today and the page has to answer for it today.
#:
#: IT WAS `objective` UNTIL PACKET ATT-8 (2026-09-10, author A10) AND THAT IS EXACTLY WHY IT MOVED.
#: A9 picked `objective` as the example of a word with no sentence; Andrew's D4 gave it one, so
#: leaving the fixture alone would have turned this class's witness into a row that renders a
#: sentence while the tests around it say it renders none -- the class would still have been green
#: and would have been testing nothing. A kind is only an unknown while nothing knows it, so the
#: example has to be a word `KIND_NOW` really does lack, and `sprint` is one: it is a queue OPTION
#: kind (`web/actions.py::dispatch_option` refuses one by name) and has never been a queue arm.
UNKNOWN_KIND = an_item(item_id="sprint:s-1", kind="sprint", rank=6, tier=4,
                       title="A kind this console has no sentence for")

#: WHAT EACH KIND MUST READ ON THE INSPECTOR, keyed by the row that carries it. Written out here
#: rather than imported from `KIND_NOW`, because a test that imports the mapping it is checking
#: asserts that a dict equals itself: swapping two values in `web/views/queue.py` would keep such
#: a test green, which is exactly the plant this class is watched under.
KIND_SENTENCE_BY_ROW = {
    "work_item:w-review": "Finished work awaiting your acceptance (Accept work)",
    "work_item:w-human": "Your own task, not yet marked done (Mark my task done)",
    "question:q-1": "A question waiting for your answer",
    "recommendation:r-1": "A proposal awaiting your approval or rejection",
    # D4's fifth, and it names the store's own `primary_verb` for the same reason the first two do.
    "objective:6": "An intake item awaiting your triage (Accept objective)",
}

#: The label the row is rendered under. Once per inspector page, on the readable branch.
KIND_LABEL = "What it is now"

#: `KIND_BY_ARM`'s four words, lifted from `web/model.py` AS TEXT rather than imported. The import
#: would pull `store` and the whole read stack into a class that otherwise needs no database, and
#: the point of the check is the vocabulary, not the object.
KIND_BY_ARM_BLOCK_RE = re.compile(r"KIND_BY_ARM = \{(?P<body>.*?)\n\}", re.S)
ARM_KIND_RE = re.compile(r'\):\s*"([a-z_]+)"')


class TheInspectorNamesTheRowsKind(unittest.TestCase):
    """E6 AS AMENDED, its last clause: "a tester proves E6 by ... reading the inspector's kind".

    THE MEASUREMENT THIS CLASS EXISTS FOR. T3's pass at X3 (`df13a55`, evidence
    `artefacts/2026-09-10/ATTENTION-OS-admiral/evidence/ATT-1g-measurement-df13a55.md`, sections 3
    and 4) followed the `done` stripe's "Open this row's inspector" link after a reload and read
    "Included because the store records an option for this item and a completed work result".
    Measured on that page: the words `Accept work` appear 0 times and the word `done` 0 times. T3
    filed it as an open reading rather than a defect (comparison point 4) and said the ruling was
    the Admiral's. The ruling is that the kind is named, and this is where that is held.

    WHAT THIS CLASS CAN AND CANNOT SEE, on the same terms as the rest of this file. It RENDERS the
    inspector through the real blueprint, the real Jinja environment and the real template; it does
    not follow a link from a stripe, because nothing here drives a browser. That the stripe's
    `data-att-inspect` url is the row's own is asserted by `TheDoneStripeDoesNotPromiseTheWindow`
    above, and that following it lands on this page is the tester's to say.
    """

    @classmethod
    def setUpClass(cls):
        supplied = ROWS + [UNKNOWN_KIND, OBJECTIVE]
        client = an_app(StubAttentionPort(supplied)).test_client()
        cls.pages = {}
        for row in supplied:
            answer = client.get("/attention/" + row.item_id)
            cls.pages[row.item_id] = (answer.status_code, answer.get_data(as_text=True))
        print("\n  DENOMINATORS, inspector pages")
        print("    inspector pages rendered      %d of %d rows supplied"
              % (sum(1 for s, _ in cls.pages.values() if s == 200), len(cls.pages)))
        print("    kinds with a sentence asserted %d, plus 1 kind with none"
              % len(KIND_SENTENCE_BY_ROW))
        print("    pages carrying %-14r %d of %d"
              % (KIND_LABEL, sum(1 for _, b in cls.pages.values() if KIND_LABEL in b),
                 len(cls.pages)))

    def body(self, item_id):
        status, body = self.pages[item_id]
        # The denominator for every assertion below. A 404 would make every `assertNotIn` pass.
        self.assertEqual(status, 200, "inspector for %s did not render" % item_id)
        return body

    def test_every_fixture_row_has_an_inspector_to_read(self):
        rendered = [i for i, (s, _) in self.pages.items() if s == 200]
        self.assertEqual(len(rendered), len(ROWS) + 2, sorted(self.pages))

    def test_a_review_row_reads_as_finished_work_awaiting_acceptance(self):
        # The row a `done` produces, and the one T3's link landed on. The verb `Accept work` is on
        # the page in the queue's own vocabulary, which it was not at `df13a55`.
        body = self.body("work_item:w-review")
        self.assertIn(KIND_SENTENCE_BY_ROW["work_item:w-review"], body)
        self.assertIn("Accept work", body)

    def test_a_human_row_reads_as_the_persons_own_unfinished_task(self):
        body = self.body("work_item:w-human")
        self.assertIn(KIND_SENTENCE_BY_ROW["work_item:w-human"], body)
        self.assertIn("Mark my task done", body)

    def test_a_question_row_reads_as_a_question_awaiting_an_answer(self):
        body = self.body("question:q-1")
        self.assertIn(KIND_SENTENCE_BY_ROW["question:q-1"], body)

    def test_a_recommendation_row_names_the_pending_proposal_decision(self):
        body = self.body("recommendation:r-1")
        self.assertIn(KIND_SENTENCE_BY_ROW["recommendation:r-1"], body)
        self.assertIn("approval or rejection", body)

    def test_no_inspector_carries_another_kinds_sentence(self):
        # THE ASSERTION THE PLANT IS WATCHED ON. Four `assertIn`s can all pass while two sentences
        # are swapped only if each row also carries the other's; this says it does not.
        for item_id, sentence in KIND_SENTENCE_BY_ROW.items():
            body = self.body(item_id)
            others = [s for i, s in KIND_SENTENCE_BY_ROW.items() if i != item_id]
            wrong = [s for s in others if s in body]
            self.assertEqual(wrong, [], "%s carries %d of %d other kinds' sentences: %s"
                             % (item_id, len(wrong), len(others), wrong))
            self.assertIn(sentence, body)

    def test_an_objective_row_reads_as_an_intake_item_awaiting_triage(self):
        # D4's arm, packet ATT-8. The store's own `primary_verb` on arm 5 is the text
        # `Accept objective`, and it is on the page in that vocabulary for the same reason
        # `Accept work` is: a person arriving here from a stripe has to read what to do next.
        body = self.body("objective:6")
        self.assertIn(KIND_SENTENCE_BY_ROW["objective:6"], body)
        self.assertIn("Accept objective", body)

    def test_an_unknown_kind_renders_its_raw_word_and_never_a_guess(self):
        # A kind with no sentence prints the store's own word. The known sentences are absent,
        # which is the half that matters: the nearest of them would be a lie a reader cannot see.
        body = self.body("sprint:s-1")
        self.assertIn(KIND_LABEL, body)
        self.assertIn("sprint", body)
        present = [s for s in KIND_SENTENCE_BY_ROW.values() if s in body]
        self.assertEqual(present, [], "an unknown kind guessed %d of %d known sentences: %s"
                         % (len(present), len(KIND_SENTENCE_BY_ROW), present))

    def test_the_label_appears_exactly_once_per_readable_inspector(self):
        for item_id in list(KIND_SENTENCE_BY_ROW) + ["sprint:s-1"]:
            self.assertEqual(self.body(item_id).count(KIND_LABEL), 1, item_id)

    def test_the_unreadable_branch_carries_no_kind_row_and_still_renders(self):
        # The control beside the five ones above. `readable=False` returns the refusal rather than
        # a stub, so there is no kind row on it -- and the page still renders, which is what stops
        # this zero being the zero of a page that 404ed.
        body = self.body("work_item:w-restricted")
        self.assertEqual(body.count(KIND_LABEL), 0)
        self.assertIn("nothing was indexed for your account", body)

    def test_what_it_is_now_sits_above_why_it_is_here(self):
        # The order is the point of the row. A person arriving from a receipt stripe is asking what
        # state the row is in; the reason it is in the queue at all is the second question.
        for item_id in KIND_SENTENCE_BY_ROW:
            body = self.body(item_id)
            self.assertLess(body.index(KIND_LABEL), body.index("Why it is here"), item_id)

    def test_every_arm_kind_has_a_sentence_and_the_extra_one_is_named(self):
        # The kind is READ OFF THE ARM, so the set of kinds that NEED a sentence is `KIND_BY_ARM`'s
        # and not this file's opinion. Lifted from `web/model.py` as text; the count is asserted
        # first so a regex that matched nothing cannot pass as agreement.
        #
        # A9 WROTE THIS AS AN EQUALITY AND PACKET ATT-8 MADE IT A CONTAINMENT, which is a weakening
        # and is therefore argued rather than just done. The two sides answer different questions:
        # an arm kind with NO sentence is a defect (the page would print a raw word where it
        # promises prose), while a sentence with no arm is INERT -- nothing produces that kind, so
        # nothing renders it. D4 puts `objective` in `KIND_NOW` on this branch while the two
        # `KIND_BY_ARM` entries that produce it are Platform's hunks, and that order is the safe
        # one (staging packet section 4: the hunks may land before the migration; the migration
        # must not land without the hunks). So the containment is asserted, AND the extra is named
        # here by hand: an unexplained extra key would be a typo nothing catches.
        with open(os.path.join(WEB_DIR, "model.py"), "r", encoding="utf-8") as handle:
            block = KIND_BY_ARM_BLOCK_RE.search(handle.read())
        self.assertIsNotNone(block, "KIND_BY_ARM was not found in web/model.py")
        arm_kinds = set(ARM_KIND_RE.findall(block.group("body")))
        print("\n    kinds parsed out of KIND_BY_ARM  %d: %s" % (len(arm_kinds),
                                                                 sorted(arm_kinds)))
        print("    kinds with a sentence            %d: %s" % (len(KIND_NOW),
                                                               sorted(KIND_NOW)))
        self.assertGreaterEqual(len(arm_kinds), 4, sorted(arm_kinds))
        missing = sorted(arm_kinds - set(KIND_NOW))
        self.assertEqual(missing, [], "web/model.py produces %d kind(s) web/views/queue.py::"
                                      "KIND_NOW has no sentence for: %s" % (len(missing), missing))
        # AHEAD_OF_THE_ARM is the whole of the permitted excess. A key that is neither an arm kind
        # nor named here is red.
        ahead_of_the_arm = {"objective"}
        unexplained = sorted(set(KIND_NOW) - arm_kinds - ahead_of_the_arm)
        self.assertEqual(unexplained, [], "KIND_NOW carries %d kind(s) no arm produces and this "
                                          "test does not name: %s" % (len(unexplained),
                                                                      unexplained))

    def test_an_unknown_kind_returns_its_own_word_from_the_one_mapping(self):
        # The function, under the page. `KIND_NOW` is the one place a kind is spelled out in
        # `web/views/**`; a second place is how two surfaces come to disagree about a row.
        #
        # THE EXAMPLE MOVED OFF `objective` WITH THE FIXTURE ABOVE, and both halves are asserted
        # here so the move is visible: a word the map does not hold comes back as itself, and
        # `objective` -- which it now holds -- comes back as D4's sentence. A9's version asserted
        # only the first of those, about a word that has since gained a sentence.
        self.assertEqual(kind_sentence("sprint"), "sprint")
        self.assertEqual(kind_sentence("review"), KIND_NOW["review"])
        self.assertEqual(kind_sentence("objective"), KIND_NOW["objective"])


# ----------------------------------------------------------- F5: the receipt stripe is announced
#
# THE SCAN IS OVER THE TEMPLATE SOURCE AND NOT THE RENDERED PAGE, which is a deliberate weakening
# and is said here rather than left for a reader to notice. What is under test is a BUILDER and a
# HANDLER -- `stripe()` and `land()` -- that no request in this file ever executes: the page ships
# them as script and a browser runs them after a press. The rendered page would carry the same
# bytes, so rendering would buy nothing and would put a Flask app between the assertion and the
# thing asserted. The source is the artefact.

#: The three function bodies this class reads, each captured up to its own two-space `}`. Written
#: as three regexes rather than one so that a failure names WHICH function went missing.
#: The signature gained `taskUrl` and `noHomeNote` in packet ATT-1o (F5's lifetime half). It is
#: still pinned EXACTLY rather than loosened to `\(.*?\)`, because this regex is what makes every
#: assertion in the class below a reading of the real builder: a signature that drifted would make
#: the capture fail loudly here instead of silently scanning some other function.
STRIPE_FN_RE = re.compile(
    r"\n  function stripe\(data, inspectUrl, taskUrl, noHomeNote\) \{(?P<body>.*?)\n  \}\n", re.S)
LAND_FN_RE = re.compile(r"\n  function land\(form, data\) \{(?P<body>.*?)\n  \}\n", re.S)
SHOW_ERROR_FN_RE = re.compile(
    r"\n  function showError\(form, text, status\) \{(?P<body>.*?)\n  \}\n", re.S)

#: E1's order and the focus call as ONE contiguous match. The index comparisons below say the four
#: statements are in order; this says nothing was interleaved between them.
E1_TAIL_RE = re.compile(r"origin\.parentNode\.insertBefore\(s, origin\);\s*"
                        r"origin\.parentNode\.removeChild\(origin\);\s*"
                        r"s\.focus\(\);")

#: Every place `land()` puts its stripe into the document, so "three paths, three focus calls" is a
#: ratio and not a literal `3` that stays green when a fourth path is added without a focus call.
INSERTS_THE_STRIPE_RE = re.compile(r"(?:appendChild|insertBefore)\(s[,)]")

#: A SCRATCH SNIPPET NOTHING RENDERS, and the positive control for every scanner below. Each of the
#: four instruments is run over it first: a `setAttribute` reader that cannot find an attribute in
#: a string that demonstrably has one, or a timer scanner that cannot find `setTimeout(` in a line
#: that is one, would score its zeroes and its counts over the real source for the wrong reason.
#: The values are nonsense words on purpose -- they can only come from here.
CONTROL_SNIPPET = (
    "  var probe = document.createElement('tr');\n"
    "  probe.setAttribute('role', 'CONTROL-ROLE');\n"
    "  probe.setAttribute('aria-live', 'CONTROL-LIVE');\n"
    "  probe.setAttribute('tabindex', 'CONTROL-TABINDEX');\n"
    "  window.setTimeout(function () { probe.remove(); }, 9000);\n"
)

#: The timers E2 forbids on this page. Scanned WITH the opening paren, because the word itself
#: appears in the comment at the top of the script that promises there is no timer, and a scanner
#: that went red on the promise would be a scanner nobody could keep.
TIMER_CALLS = ("setTimeout(", "setInterval(")


def attr_values(source, attr):
    """Every value assigned to `attr` through `.setAttribute('<attr>', '<value>')`, in order.

    Values, not a count, so a test can say WHICH value it found instead of only how many: a builder
    that set `role` to `alert` would otherwise be indistinguishable here from one that set `status`.
    """
    return re.findall(r"\.setAttribute\('%s', '([^']*)'\)" % re.escape(attr), source)


def attr_receivers(source, attr):
    """The variable each `setAttribute('<attr>', ...)` is called ON, in order.

    Separate from `attr_values` on purpose, and the two are asserted by separate cases: one owns
    whether the attribute is set and to what, the other owns which element receives it. A case that
    did both would turn one reverted line into two reds and make a reader work out which is the
    defect -- the failure mode this file already avoids in its phrase scans.
    """
    return re.findall(r"(\w+)\.setAttribute\('%s'," % re.escape(attr), source)


def function_body(source, pattern, name):
    """One script function's body out of the template source, or an error naming what is missing."""
    found = pattern.search(source.replace("\r\n", "\n"))
    assert found, ("`%s` was not found in the template source, so every assertion about it would "
                   "be vacuous" % name)
    return found.group("body")


class TheReceiptStripeIsAnnouncedAndFocused(unittest.TestCase):
    """F5, the independent reviewer on X4. Packet ATT-1l, authors A11 and A12, 2026-09-11.

    THE FINDING (`review/REVIEW-act-door-eb4c495.md` section 8, F5, "should fix"): the page
    announces its REFUSALS and not its SUCCESSES. `showError()` sets `role="alert"`; `stripe()` set
    `class`, `data-att-receipt`, `data-att-inspect`, `colSpan` and `data-label` and no role and no
    live region at all -- and `land()` then removed the row the person had just activated, which
    destroys the focused element and drops focus to `<body>` with nothing spoken. For a
    screen-reader user that made item 10's whole guarantee -- nothing vanishes before its receipt --
    unobservable: the row vanished and the receipt was silent. F5 is outside E1 to E12 as written,
    which is why it was "should fix" rather than a blocker; the checklist did not ask.

    THE FIX IS FOUR LINES AND THIS CLASS HOLDS ALL FOUR. Three attributes on the stripe `tr`
    (`role="status"`, `aria-live="polite"`, `tabindex="-1"`) and one `s.focus()` per insertion path
    in `land()`. The focus move is the load-bearing half and the live region is the belt: a live
    region inserted into the document already populated is not reliably announced, because the
    region has to be present before its content changes.

    WHAT THIS CLASS CAN AND CANNOT SEE, on the same terms as every class above and one step weaker.
    It reads the SOURCE of two script functions. It does not render, it does not drive a browser,
    and it therefore cannot say that a reader spoke the receipt or that `document.activeElement`
    moved. It can say that the attributes are set, that they are set on the row element rather than
    on the cell, that E9's `role="alert"` was not disturbed, and that the focus call sits after E1's
    order rather than inside it. The rest is the tester's, with Playwright's accessibility tree.

    E1 AND E2 ARE CONSTRAINTS ON THIS FIX, not things it changes. E1's two statements keep their
    order and the focus call is asserted to come after both; no timer is added, and the scanner that
    says so is run over a control that has one.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = template_source("inbox.html")
        cls.stripe = function_body(cls.source, STRIPE_FN_RE, "function stripe")
        cls.land = function_body(cls.source, LAND_FN_RE, "function land")
        cls.show_error = function_body(cls.source, SHOW_ERROR_FN_RE, "function showError")
        cls.inserts = INSERTS_THE_STRIPE_RE.findall(cls.land)
        cls.focuses = cls.land.count("s.focus();")
        print("\n  DENOMINATORS, the receipt stripe's announcement")
        print("    inbox.html source                 %d characters" % len(cls.source))
        print("    stripe() body                     %d characters" % len(cls.stripe))
        print("    land() body                       %d characters" % len(cls.land))
        print("    showError() body                  %d characters" % len(cls.show_error))
        for attr in ("role", "aria-live", "tabindex"):
            print("    %-13s set in stripe()      %s on %s  (control: %s)"
                  % (attr, attr_values(cls.stripe, attr), attr_receivers(cls.stripe, attr),
                     attr_values(CONTROL_SNIPPET, attr)))
        print("    data-label set in stripe()        %s on %s"
              % (attr_values(cls.stripe, "data-label"), attr_receivers(cls.stripe, "data-label")))
        print("    role set in showError()           %s" % attr_values(cls.show_error, "role"))
        print("    stripe insertions in land()       %d" % len(cls.inserts))
        print("    focus calls in land()             %d" % cls.focuses)
        print("    timer calls in stripe() + land()  %d  (control: %d)"
              % (sum(cls.stripe.count(c) + cls.land.count(c) for c in TIMER_CALLS),
                 sum(CONTROL_SNIPPET.count(c) for c in TIMER_CALLS)))

    def test_the_instruments_find_what_they_look_for_where_it_really_is(self):
        # THE POSITIVE CONTROL, FIRST, so every count and every zero below is read after the four
        # scanners have been shown to work. An attribute reader that can only ever return [] would
        # make `assertEqual(values, [])` the cheapest green in this file.
        self.assertEqual(attr_values(CONTROL_SNIPPET, "role"), ["CONTROL-ROLE"])
        self.assertEqual(attr_values(CONTROL_SNIPPET, "aria-live"), ["CONTROL-LIVE"])
        self.assertEqual(attr_values(CONTROL_SNIPPET, "tabindex"), ["CONTROL-TABINDEX"])
        self.assertEqual(sum(CONTROL_SNIPPET.count(c) for c in TIMER_CALLS), 1,
                         "the timer scanner cannot find a timer in a line that is one, so its zero "
                         "over the real source would mean nothing")

    def test_there_is_something_to_scan(self):
        # A verdict over an empty capture is not a pass. Each body has to be the real one before its
        # contents are worth asserting, and a regex that matched an empty group would satisfy every
        # `assertNotIn` below.
        self.assertGreater(len(self.stripe), 1000, "stripe() barely captured")
        self.assertGreater(len(self.land), 500, "land() barely captured")
        self.assertGreater(len(self.show_error), 100, "showError() barely captured")
        self.assertIn("var tr = el('tr', 'att-receipt');", self.stripe)
        self.assertIn("var s = stripe(data,", self.land)

    def test_the_stripe_is_built_with_exactly_the_three_announcing_attributes(self):
        # One case over the three, deliberately: they are one decision and one fix, and three cases
        # would turn a single reverted line into a reader's puzzle about which of three is the
        # defect. The values are asserted, not just the presence, because `role="alert"` here would
        # make a receipt interrupt whatever the reader was saying -- which is what `status` is not.
        found = {attr: attr_values(self.stripe, attr) for attr in ("role", "aria-live", "tabindex")}
        self.assertEqual(found, {"role": ["status"],
                                 "aria-live": ["polite"],
                                 "tabindex": ["-1"]}, found)

    def test_wherever_they_are_set_the_receiver_is_the_row_and_never_the_cell(self):
        # The element announced, the element focused and the element `land()` inserts have to be ONE
        # element. On the `td` they would drift apart at the next edit and nothing would be red.
        #
        # THIS CASE DOES NOT RE-ASSERT PRESENCE, and that is the whole reason it is a second case.
        # The one above owns whether the three are set and to what; this one owns the receiver of
        # whatever IS set. Measured: with `aria-live` removed from the builder, that split makes the
        # plant exactly one named red instead of two a reader has to reconcile.
        receivers = {attr: attr_receivers(self.stripe, attr)
                     for attr in ("role", "aria-live", "tabindex")}
        wrong = {attr: names for attr, names in receivers.items()
                 if [name for name in names if name != "tr"]}
        self.assertEqual(wrong, {}, receivers)
        # AND THE DENOMINATOR, so this is not a verdict over an empty set: the `td` in this builder
        # takes exactly one attribute, `data-label`, and it is the positive control that the
        # receiver reader can see a `td` when there is one to see.
        self.assertEqual(attr_receivers(self.stripe, "data-label"), ["td"])

    def test_the_refusal_is_still_an_alert_and_the_receipt_is_not(self):
        # E9 IS A CONSTRAINT ON THIS FIX. `showError()` was to be left alone, and "left alone" is
        # checked rather than remembered. The two roles are also asserted to differ: a fix that made
        # both `alert` would announce every receipt by interrupting, which is the opposite trade.
        self.assertEqual(attr_values(self.show_error, "role"), ["alert"])
        self.assertEqual(attr_values(self.stripe, "role"), ["status"])
        self.assertNotEqual(attr_values(self.show_error, "role"),
                            attr_values(self.stripe, "role"))

    def test_land_builds_then_inserts_then_removes_then_focuses_in_that_order(self):
        # E1 IS THE OTHER CONSTRAINT, and the order is item 10's actual test: nothing vanishes
        # before its receipt. Asserted by index rather than by a regex so that the failure message
        # names the four positions, and the last focus call is taken with `rindex` because the two
        # earlier paths have their own.
        build = self.land.index("var s = stripe(data,")
        insert = self.land.index("origin.parentNode.insertBefore(s, origin);")
        remove = self.land.index("origin.parentNode.removeChild(origin);")
        focus = self.land.rindex("s.focus();")
        order = [("build", build), ("insert", insert), ("remove", remove), ("focus", focus)]
        self.assertEqual(order, sorted(order, key=lambda pair: pair[1]), order)

    def test_nothing_was_interleaved_into_e1s_order(self):
        # The index test above would still pass if a statement were added BETWEEN the insert and the
        # remove. This one says the three are contiguous, and that there is exactly one such run:
        # a second copy of E1's tail elsewhere in the function would be a second act path nobody
        # declared.
        self.assertEqual(len(E1_TAIL_RE.findall(self.land)), 1,
                         "E1's order and its focus call are not one contiguous run: %r"
                         % self.land[-400:])

    def test_every_path_that_inserts_a_stripe_also_focuses_it(self):
        # THE DENOMINATOR IS THE NUMBER OF INSERTION PATHS, not a literal three. F5 named two --
        # the accept and the undo -- and the third, a form outside any row, is the fallback. A
        # fourth path added later without a focus call is red here rather than silent in a browser.
        self.assertEqual(len(self.inserts), 3, self.inserts)
        self.assertEqual(self.focuses, len(self.inserts),
                         "%d focus calls for %d stripe insertions" % (self.focuses,
                                                                      len(self.inserts)))

    def test_the_fix_added_no_timer(self):
        # E2. The stripe stays until the next act or the next page load, and a focus move is
        # synchronous, so nothing here needs a timer and nothing here has one. The zero is read
        # beside the control asserted at the top of this class.
        counts = {call: self.stripe.count(call) + self.land.count(call) for call in TIMER_CALLS}
        self.assertEqual(counts, {call: 0 for call in TIMER_CALLS}, counts)

    def test_the_success_path_never_calls_the_refusal_paths_focus_helper(self):
        # THE CONSTRAINT ON X7's SECOND CORRECTION, held where the success path is held. `refocus()`
        # is for the branch where the pressed button SURVIVES; on this branch the button has left
        # the DOM with its row and the stripe is what takes focus. A `refocus(btn)` that drifted in
        # here would move focus off the receipt and undo F5 without touching a line F5's cases read.
        self.assertEqual(self.land.count("refocus("), 0, self.land)

    def test_the_sighted_half_is_a_focus_outline_that_adds_no_width(self):
        # The other half of the same fix, for a person who can see the screen: a programmatic focus
        # after a pointer press does NOT match `:focus-visible`, so the rule is `:focus`. The offset
        # is negative so the ring is drawn inside the row and E12's 375px geometry is untouched --
        # which is a claim about layout that only the tester's viewport can finally confirm.
        self.assertIn(".att-receipt:focus{outline:2px solid var(--focus,var(--run));"
                      "outline-offset:-2px}", self.source)
        self.assertNotIn(".att-receipt:focus-visible{", self.source)


# -------------------------------------------------------- Q2c: completeness is named in words
#
# THE THREE STUB ROWS THIS CLASS RENDERS, kept out of `ROWS` for the reason `OBJECTIVE` is kept out
# of it: the classes above assert exact page-wide counts, and three more rows in the shared list
# would move those numbers as a side effect of this packet rather than as a decision.
#
# The unmeasured row is a row with `completeness=None`, which is what EVERY row on the loaded mount
# was when T7 measured Q2c at `93363cb`. It is written out here as its own named stub rather than
# borrowed from `ROWS`, so the case below reads as the state it is about; the four readable rows of
# `ROWS` carry the same absence and are counted in the same denominator.
COMPLETE_RESULT = an_item(item_id="work_item:w-complete", rank=8, tier=1,
                          title="Agent finished the renewal comparison, with every source read",
                          completeness=Completeness.complete())
PARTIAL_RESULT = an_item(item_id="work_item:w-partial", rank=9, tier=2,
                         title="Agent finished the renewal comparison with a source missing",
                         completeness=Completeness.partial(["the sales chat channel"],
                                                           "the connector was down all morning"))
UNMEASURED_RESULT = an_item(item_id="work_item:w-unstated", rank=10, tier=2,
                            title="Agent finished, and nobody recorded whether it covered everything")

#: The inspector's completeness term and the value cell that follows it. Values, not a count: a
#: template that rendered the row and left it empty, or rendered "partial" where the component says
#: "finished without ...", would be indistinguishable from the real thing under a counter.
RESULT_LABEL = "Result"
# A report link now owns Result on completed work. The separate assessment must
# still state complete, partial or unknown; the link is not that assessment.
RESULT_ROW_RE = re.compile(r"<dt>(?:Result|Completeness assessment)</dt>\s*<dd[^>]*>(?P<value>[^<]*)</dd>")

#: THE POSITIVE CONTROL AND ITS NEGATIVE, for the reader above. One markup fragment that has the row
#: and one that does not, so "no Result row on the unreadable branch" is a reading rather than the
#: output of a regex that can only ever return []. The values are nonsense words on purpose.
RESULT_CONTROL_WITH = ('<dl class="att-kv"><dt>Freshness</dt><dd>CONTROL-FRESHNESS</dd>'
                       '<dt>Result</dt>\n      <dd class="att-unknown">CONTROL-RESULT</dd>'
                       '<dt>Effect on you</dt><dd>CONTROL-IMPACT</dd></dl>')
RESULT_CONTROL_WITHOUT = ('<dl class="att-kv"><dt>Freshness</dt><dd>CONTROL-FRESHNESS</dd>'
                          '<dt>Effect on you</dt><dd>CONTROL-IMPACT</dd></dl>')

#: The word the template prints where the store holds no completeness at all. It is asserted here as
#: a literal and read off the rendered page, never imported from the template, for the reason
#: `KIND_SENTENCE_BY_ROW` is written out: a test that reads its expectation out of the artefact it
#: is checking asserts that a file equals itself.
NOT_STATED = "not stated"


def result_values(body):
    """Every value the inspector prints under its completeness term, in order."""
    return [found.group("value").strip() for found in RESULT_ROW_RE.finditer(body)]


class TheInspectorNamesCompletenessInWords(unittest.TestCase):
    """Q2c on `_system/attention/C5-RENDER-QUESTIONS-2026-09-11.md` (`3d5f23e`). Packet ATT-1m, X7.

    THE MEASUREMENT THIS CLASS EXISTS FOR. T7's pass at `93363cb` (evidence
    `ATT-9-and-X6-measurement-93363cb.md`, section 4) read every one of the 7 loaded rows' inspectors
    and reported Q2c NOT MET at **0 of 7**: `kind` 7 of 7, reversibility 7 of 7, completeness
    **0 of 7**. The cause was one guard. The Result term was `{% if shown.completeness %}`, and on
    the row itself the completeness chip is `{% if item.incomplete %}` -- so an item that finished
    COMPLETELY and an item nobody ever measured rendered exactly the same thing, which is nothing,
    and "complete" was carried by the absence of a chip beside the absence of a row. A reader cannot
    read an absence, and the "Not stated" signals sentence never named completeness either.

    THE CORRECTION IS IN THE TEMPLATE AND NOWHERE ELSE. `Completeness.render()` already says
    `complete` in the component's own vocabulary, so no state value was invented and `honesty.py` is
    untouched; `QueueItem.inspect()` still returns `None` for an item with no completeness, so the
    dict can still tell "complete" from "nobody said" and `web/tests/test_completeness.py` reads the
    same value it always did. The template supplies the display word for the absent case, beside
    "not read" on the unreadable branch, which is where this file already keeps that kind of literal.

    WHAT THIS CLASS CAN AND CANNOT SEE. It RENDERS the inspector through the real blueprint and the
    real template and reads the value cell out of the markup; it drives no browser, so it cannot say
    what Q2c's own instrument says -- 7 of 7 rows of the LOADED mount, read with `innerText`. That is
    the tester's, and the fixture here is three stubs plus the four readable rows of `ROWS`, not the
    mount. It is also completeness ONLY: `kind` is held by `TheInspectorNamesTheRowsKind` above, and
    reversibility rides on `Signals.silence_note()`, which these stubs carry none of (denominator 0).
    """

    @classmethod
    def setUpClass(cls):
        supplied = ROWS + [COMPLETE_RESULT, PARTIAL_RESULT, UNMEASURED_RESULT]
        client = an_app(StubAttentionPort(supplied)).test_client()
        cls.pages = {}
        for row in supplied:
            answer = client.get("/attention/" + row.item_id)
            cls.pages[row.item_id] = (answer.status_code, answer.get_data(as_text=True))
        cls.readable = [row.item_id for row in supplied if row.readable]
        cls.unmeasured = [row.item_id for row in supplied
                          if row.readable and row.completeness is None]
        cls.stated = [row.item_id for row in supplied
                      if row.readable and row.completeness is not None]
        print("\n  DENOMINATORS, completeness in words")
        print("    inspector pages rendered          %d of %d rows supplied"
              % (sum(1 for s, _ in cls.pages.values() if s == 200), len(cls.pages)))
        print("    readable inspectors               %d of %d" % (len(cls.readable), len(supplied)))
        print("    of those, no completeness held    %d" % len(cls.unmeasured))
        print("    of those, a completeness held     %d" % len(cls.stated))
        print("    pages carrying a %-8r term   %d of %d"
              % (RESULT_LABEL, sum(1 for _, b in cls.pages.values() if result_values(b)),
                 len(cls.pages)))
        print("    control reader, row present       %s  (absent: %s)"
              % (result_values(RESULT_CONTROL_WITH), result_values(RESULT_CONTROL_WITHOUT)))

    def body(self, item_id):
        status, body = self.pages[item_id]
        # The denominator for every assertion below. A 404 would make every `assertEqual([], ...)`
        # here pass for the wrong reason.
        self.assertEqual(status, 200, "inspector for %s did not render" % item_id)
        return body

    def test_the_reader_finds_the_row_where_it_really_is_and_not_where_it_is_not(self):
        # THE POSITIVE CONTROL AND ITS NEGATIVE, FIRST. Both halves matter: without the first, the
        # three value cases could be green over a regex that matched anything; without the second,
        # the unreadable-branch zero would be the zero of a reader that never finds anything.
        self.assertEqual(result_values(RESULT_CONTROL_WITH), ["CONTROL-RESULT"])
        self.assertEqual(result_values(RESULT_CONTROL_WITH.replace('<dt>Result</dt>',
                         '<dt>Completeness assessment</dt>')), ["CONTROL-RESULT"])
        self.assertEqual(result_values(RESULT_CONTROL_WITHOUT), [])

    def test_every_stub_row_has_an_inspector_to_read(self):
        rendered = [i for i, (s, _) in self.pages.items() if s == 200]
        self.assertEqual(len(rendered), len(ROWS) + 3, sorted(self.pages))

    def test_a_complete_item_says_the_word_complete(self):
        # The state that used to render NOTHING and now renders the component's own word. Compared
        # against `Completeness.complete().render()` as well as against the literal, so a template
        # that started spelling the state itself -- "done", "all sources read" -- is red here even
        # though it would still be a word on the page.
        values = result_values(self.body("work_item:w-complete"))
        self.assertEqual(values, [COMPLETE], values)
        self.assertEqual(values, [Completeness.complete().render()], values)

    def test_a_partial_item_says_what_is_missing(self):
        # `partial()` refuses to be built without its list, and the sentence it renders carries that
        # list; the inspector prints the sentence rather than the bare state word, which is the
        # difference between knowing the shape of the hole and knowing only that there is one.
        values = result_values(self.body("work_item:w-partial"))
        self.assertEqual(values, [PARTIAL_RESULT.completeness.render()], values)
        self.assertIn("the sales chat channel", values[0])
        self.assertNotIn(NOT_STATED, values[0])

    def test_an_unmeasured_item_says_not_stated(self):
        # THE CASE THE PLANT IS WATCHED ON, and the one that owns the guard T7's 0 of 7 came from.
        # Its denominator is every readable inspector whose row holds no completeness -- the named
        # stub and the four readable rows of `ROWS` -- so restoring the old `{% if %}` on that row
        # is red here by name and green everywhere else in this file.
        self.assertGreaterEqual(len(self.unmeasured), 5, self.unmeasured)
        read = {item_id: result_values(self.body(item_id)) for item_id in self.unmeasured}
        self.assertEqual(read, {item_id: [NOT_STATED] for item_id in self.unmeasured}, read)

    def test_the_term_is_rendered_once_wherever_it_is_rendered(self):
        # WHEREVER, NOT EVERYWHERE, and the difference was measured rather than reasoned. Written
        # first as "once on every readable inspector", this case re-asserted PRESENCE on its way to
        # asserting "once", so the plant -- the old `{% if %}` back on the Result row -- scored two
        # reds for one defect and made a reader work out which was the defect. That is the failure
        # mode `TheReceiptStripeIsAnnouncedAndFocused` was repaired for at `93363cb`, one packet
        # ago, on the same file. This case now owns only the count of whatever IS rendered; the
        # case above owns whether an unmeasured row renders anything at all.
        counts = {item_id: len(result_values(self.body(item_id)))
                  for item_id in self.readable}
        twice = {item_id: n for item_id, n in counts.items() if n > 1}
        self.assertEqual(twice, {}, counts)
        # AND THE DENOMINATOR, so this is not a verdict over a page with no such row on it: the two
        # stub rows that hold a completeness carry exactly one term each, and they are a different
        # set from the one the case above is watched on.
        self.assertEqual({item_id: counts[item_id] for item_id in self.stated},
                         {item_id: 1 for item_id in self.stated}, counts)

    def test_the_unreadable_branch_carries_no_result_row(self):
        # The control beside the count above, and a claim about the refusal rather than about the
        # value: `inspect()` returns no completeness key at all on that branch, so there is nothing
        # to print and nothing here invents a "not stated" for an item whose result was never the
        # reader's to see. The page still renders, which is what stops this zero being a 404's.
        body = self.body("work_item:w-restricted")
        self.assertEqual(result_values(body), [])
        self.assertEqual(body.count("<dt>%s</dt>" % RESULT_LABEL), 0)
        self.assertIn("nothing was indexed for your account", body)

    def test_the_dict_still_distinguishes_complete_from_nobody_said(self):
        # The half of this correction that is NOT on the page. "not stated" is a display word; if it
        # had been pushed into `QueueItem.inspect()` the two states would have collapsed into one
        # string for every other caller, and `web/tests/test_completeness.py` -- which this packet
        # may not edit -- asserts the `None` this case also asserts.
        self.assertIsNone(UNMEASURED_RESULT.inspect()["completeness"])
        self.assertEqual(COMPLETE_RESULT.inspect()["completeness"], COMPLETE)
        self.assertFalse(COMPLETE_RESULT.incomplete)
        self.assertTrue(PARTIAL_RESULT.incomplete)


# --------------------------------------------------- the refusal path keeps focus on the control
#
#: The two script functions this class reads, each captured up to its own two-space `}`, and written
#: as two regexes rather than one so a failure names WHICH function went missing.
SEND_FN_RE = re.compile(r"\n  function send\(form\) \{(?P<body>.*?)\n  \}\n", re.S)
REFOCUS_FN_RE = re.compile(r"\n  function refocus\(btn\) \{(?P<body>.*?)\n  \}\n", re.S)

#: The three statements of a refusal's tail, in the order they have to appear in.
SHOW_ERROR_CALL = "showError("
RE_ENABLE = "btn.disabled = false;"
REFOCUS_CALL = "refocus(btn);"
REFUSAL_TAIL = (SHOW_ERROR_CALL, RE_ENABLE, REFOCUS_CALL)

#: THE TWO-WAY CONTROL for the order reader below: one branch whose tail is in order and one whose
#: two last statements are swapped. A reader that called both of them ordered would call anything
#: ordered, and the whole of this class is one ordering claim.
BRANCH_CONTROL_IN_ORDER = ("        showError(form, 'CONTROL-TEXT', 0);\n"
                           "        if (btn) btn.disabled = false;\n"
                           "        gate(form);\n"
                           "        refocus(btn);\n")
BRANCH_CONTROL_SWAPPED = ("        showError(form, 'CONTROL-TEXT', 0);\n"
                          "        refocus(btn);\n"
                          "        gate(form);\n"
                          "        if (btn) btn.disabled = false;\n")


def tail_positions(branch):
    """Where each statement of the refusal's tail sits in one branch. `-1` means it is absent."""
    return [(step, branch.find(step)) for step in REFUSAL_TAIL]


def tail_is_in_order(branch):
    """True when all three statements are present and in the one order that serves a keyboard."""
    found = tail_positions(branch)
    if any(at < 0 for _, at in found):
        return False
    return found == sorted(found, key=lambda pair: pair[1])


class TheRefusalPathKeepsFocusOnTheControl(unittest.TestCase):
    """The F5 twin, on the branch F5 did not cover. Packet ATT-1m, X7, from T7's reading at X6.

    THE MEASUREMENT THIS CLASS EXISTS FOR. T7's two-tab stale press at `93363cb` (evidence
    `ATT-9-and-X6-measurement-93363cb.md`, section 2, A4) found E9 MET -- 400, the server's sentence
    in place, computed role `alert`, `live: assertive`, the button re-enabled, no stripe, the row
    untouched -- and then read `document.activeElement` on the refusing page: **`BODY`**, computed
    role `none`, `ignored: true`. `send()` disables the pressed button, which blurs it, and the
    refusal path handed the button back without handing the focus back. A screen-reader user is
    served either way, because an assertive live region speaks without focus; a KEYBOARD user is
    dropped to the top of the document and has to tab back to the control they just pressed. T7
    filed it as evidence and not as a verdict, and said it was the Admiral's to rule on.

    IT IS ONE HELPER AND TWO CALLS, AND BOTH ERROR BRANCHES GET IT. `send()` has two: the door's own
    refusal, and the `catch` where the page could not reach the door at all. T7 measured the first.
    The second re-enables the same button in the same way and loses focus in the same way, so it is
    fixed with it rather than left as the one branch where the page still drops a keyboard user --
    and this class takes its denominator from the number of re-enable sites rather than from a
    literal 2, so a third branch added later without a focus call is red here rather than silent.

    WHAT THIS CLASS CAN AND CANNOT SEE, on the same terms as `TheReceiptStripeIsAnnouncedAndFocused`
    above and one step weaker than the tester. It reads the SOURCE of two script functions. It does
    not render, it does not drive a browser, and it therefore cannot say that `document.activeElement`
    is the button after a refusal -- that is the reading a tester takes with Playwright, and it is
    the one that closes this. It can say that the call exists, that it is in both error branches,
    that it comes after the re-enable rather than before it, that it cannot fire on a control the
    gate has just re-disabled, and that the success path did not acquire one.

    E2 AND E9 ARE CONSTRAINTS ON THIS FIX. No timer is added, and the scanner that says so runs over
    a control that has one; `showError()` is untouched, which the class above asserts by its role.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = template_source("inbox.html")
        cls.send = function_body(cls.source, SEND_FN_RE, "function send")
        cls.refocus = function_body(cls.source, REFOCUS_FN_RE, "function refocus")
        cls.land = function_body(cls.source, LAND_FN_RE, "function land")
        # The two error branches, split at the `catch` that begins the second one. Both are tails of
        # the same promise chain, so the split is a substring index and not a parser.
        split = cls.send.index(".catch(function (e)")
        cls.branches = {"the door refused": cls.send[:split],
                        "the door was unreachable": cls.send[split:]}
        cls.re_enables = cls.send.count(RE_ENABLE)
        cls.refocus_calls = cls.send.count(REFOCUS_CALL)
        print("\n  DENOMINATORS, the refusal path's focus")
        print("    send() body                       %d characters" % len(cls.send))
        print("    refocus() body                    %d characters" % len(cls.refocus))
        print("    re-enable sites in send()         %d" % cls.re_enables)
        print("    refocus calls in send()           %d" % cls.refocus_calls)
        print("    refocus calls in land()           %d  (the success path keeps the stripe)"
              % cls.land.count("refocus("))
        for name, branch in cls.branches.items():
            print("    %-33s %s" % (name, tail_positions(branch)))
        print("    order reader, in order / swapped  %s / %s"
              % (tail_is_in_order(BRANCH_CONTROL_IN_ORDER),
                 tail_is_in_order(BRANCH_CONTROL_SWAPPED)))
        print("    timer calls in send() + refocus()  %d  (control: %d)"
              % (sum(cls.send.count(c) + cls.refocus.count(c) for c in TIMER_CALLS),
                 sum(CONTROL_SNIPPET.count(c) for c in TIMER_CALLS)))

    def test_the_order_reader_is_two_way(self):
        # THE POSITIVE CONTROL AND ITS NEGATIVE, FIRST, because every case below is this one reader
        # applied to the real source. A reader that returned True for anything would make the
        # ordering claim the cheapest green in this file.
        self.assertTrue(tail_is_in_order(BRANCH_CONTROL_IN_ORDER))
        self.assertFalse(tail_is_in_order(BRANCH_CONTROL_SWAPPED))
        self.assertFalse(tail_is_in_order("        gate(form);\n"))

    def test_there_is_something_to_scan(self):
        # A verdict over an empty capture is not a pass: a regex that matched an empty group would
        # satisfy every count below and report two branches of nothing.
        self.assertGreater(len(self.send), 800, "send() barely captured")
        self.assertGreater(len(self.refocus), 20, "refocus() barely captured")
        self.assertIn("fetch('/attention/act'", self.send)
        for name, branch in self.branches.items():
            self.assertIn(SHOW_ERROR_CALL, branch, name)

    def test_every_re_enable_of_the_button_is_followed_by_a_refocus(self):
        # THE DENOMINATOR IS THE NUMBER OF RE-ENABLE SITES, not a literal 2. Every place that hands
        # the button back is a place that took focus away by disabling it, so the ratio is the
        # claim: a third error branch added without a focus call is red here.
        self.assertEqual(self.re_enables, 2, self.send)
        self.assertEqual(self.refocus_calls, self.re_enables,
                         "%d focus calls for %d re-enables" % (self.refocus_calls,
                                                               self.re_enables))

    def test_both_error_branches_show_then_re_enable_then_refocus(self):
        # The order is the whole of it. Focus moved BEFORE the re-enable would land on a disabled
        # control and go nowhere, which is the same `<body>` T7 measured with a line that looks like
        # a fix. Reported per branch so the failure names which one.
        wrong = {name: tail_positions(branch) for name, branch in self.branches.items()
                 if not tail_is_in_order(branch)}
        self.assertEqual(wrong, {}, wrong)

    def test_the_focus_lands_on_the_pressed_button_and_only_when_it_can_be_pressed(self):
        # THE HELPER'S OWN BODY, and the case a removed `btn.focus()` is watched on. The guard is
        # asserted with it: `gate()` may re-disable the button on a form whose field is too short,
        # and a `focus()` on a disabled control is a silent no-op that would leave this class green
        # over the very behaviour it exists to prevent.
        self.assertIn("btn.focus();", self.refocus)
        self.assertEqual(self.refocus.count("focus()"), 1, self.refocus)
        self.assertIn("!btn.disabled", self.refocus)
        self.assertEqual(self.source.count("function refocus("), 1)

    def test_the_gate_still_runs_before_the_focus_move(self):
        # `gate()` is what decides whether the button may be pressed at all, so it has to have run
        # before the guard above reads `btn.disabled`. Asserted per branch by index rather than by
        # a regex, so the failure names the branch and the two positions.
        for name, branch in self.branches.items():
            self.assertLess(branch.index("gate(form);"), branch.index(REFOCUS_CALL), name)

    def test_the_fix_added_no_timer(self):
        # E2, and the same scanner the F5 class runs, read beside the same control. Nothing here
        # needs a timer: a focus move is synchronous.
        counts = {call: self.send.count(call) + self.refocus.count(call) for call in TIMER_CALLS}
        self.assertEqual(counts, {call: 0 for call in TIMER_CALLS}, counts)


# ------------------------------------------- F5: the inverse that outlives the page it was shown on
#
# CLOSURE'S PANE PASS, `INFINITY-CLOSURE-admiral/evidence/P14-RESULT-attention-actdoor-eb4c495.md`,
# finding F5. E3's inverse is REAL -- the tester posted `undo_done` and the store took it, HTTP 200,
# `verb: "reopen"` -- and its only affordance was session-transient: after the act and ONE reload,
# controls matching `undo|reopen|unaccept` measured **0** on `?filter=queue`, **0** on the 99-row
# page, and **0 forms at all** on the row's own inspector, which is read-only by design.
#
# The repair is a sentence and a link, not a second write door: `/task/<bare source_id>` already
# carries `Unaccept` and `Send back`, each behind a typed reason, and it is not a window of the
# queue, so it survives the act that removes the row from every window.

#: The route that page is, as `web/app.py` declares it. Written out here as literals rather than
#: imported, for the reason `NOT_STATED` is: a test that reads its expectation out of the artefact
#: it checks asserts that a file equals itself.
TASK_ROUTE_DECORATOR = '@app.get("/task/<tid>")'
TASK_ROUTE_FUNCTION = "def task(tid):"

#: A route that demonstrably exists in the same file, and one that demonstrably does not. The first
#: proves the scanner can find a route declaration; the second proves it can fail to.
CONTROL_ROUTE_PRESENT = '@app.get("/question/<qid>")'
CONTROL_ROUTE_ABSENT = '@app.get("/CONTROL-NO-SUCH-ROUTE/<x>")'

#: ATT-1s, A18R: the durable clause follows the complete E2 sentence and names the
#: button on the destination page. Expectations stay literal, independent of source.
F5_DONE_CLAUSE = (
    " After that, send it back from the row’s own page "
    "(the Send back button runs the same reopen).")
F5_ACCEPT_CLAUSE = " After that, unaccept from the row’s own page."
E2_SENTENCE = (
    "This receipt stays until you leave this page. The record is on the item’s own thread.")

#: The fragment the objective row hands the stripe, in the verb's own terms. It is the SERVER's
#: words, not the page's: the third sentence names a kind, and this page cannot see one.
OBJECTIVE_NO_HOME = "an accepted objective has no page with an inverse yet"
QUESTION_NO_HOME = "an answered question has no page with an inverse yet"

#: Every act form declares exactly one of these two and never both.
TASK_ATTR_RE = re.compile(r'data-att-task="([^"]*)"')
NO_HOME_ATTR_RE = re.compile(r'data-att-no-home="([^"]*)"')


class TheRowsOwnPageIsTheRouteTheAppDeclares(unittest.TestCase):
    """The link is a route this console really serves, and not a path this template invented.

    `url_for('task', tid=...)` is NOT used, and the reason is measured rather than preferred: both
    render harnesses that exercise these templates -- `an_app` in this file and `an_app` in
    `web/tests/test_routines_and_attention_render.py` -- register the attention blueprint ALONE, so
    `url_for` would raise `BuildError` on every render of the queue. The literal path is also the
    console's own convention: 14 links across 9 templates write it and none calls `url_for('task')`.

    THIS CLASS IS WHAT `url_for` WOULD OTHERWISE HAVE GIVEN. It reads `web/app.py` -- which this
    packet does not edit -- and goes red the day the route is renamed or moved, which is the only
    guarantee the endpoint name was buying.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(WEB_DIR, "app.py"), encoding="utf-8") as handle:
            cls.app_py = handle.read()
        cls.hrefs = sorted(set(TASK_ATTR_RE.findall(
            an_app().test_client().get("/attention/").get_data(as_text=True))))
        print("\n  DENOMINATORS, the row's own page")
        print("    web/app.py                    %d characters" % len(cls.app_py))
        print("    the route declaration         %d  (control present: %d, control absent: %d)"
              % (cls.app_py.count(TASK_ROUTE_DECORATOR),
                 cls.app_py.count(CONTROL_ROUTE_PRESENT),
                 cls.app_py.count(CONTROL_ROUTE_ABSENT)))
        print("    data-att-task on the page     %s" % cls.hrefs)

    def test_the_scanner_can_find_a_route_and_can_fail_to(self):
        # The positive control and its negative, FIRST. A scanner that finds every string would make
        # the assertion below free, and one that finds none would make it impossible.
        self.assertEqual(self.app_py.count(CONTROL_ROUTE_PRESENT), 1)
        self.assertEqual(self.app_py.count(CONTROL_ROUTE_ABSENT), 0)

    def test_web_app_declares_the_route_this_template_links_to(self):
        self.assertEqual(self.app_py.count(TASK_ROUTE_DECORATOR), 1, TASK_ROUTE_DECORATOR)
        self.assertIn(TASK_ROUTE_FUNCTION, self.app_py)

    def test_every_rendered_link_is_that_route_with_the_bare_id_on_it(self):
        # The BARE `source_id`, like the id the write door is posted: `/task/<tid>` resolves through
        # `model.review(tid)`, which matches `brain.work_item.id`. A prefixed `work_item:w-human`
        # would 404 on a page whose whole job is to still be there after the queue has moved on.
        self.assertEqual(self.hrefs, ["/task/w-human", "/task/w-review"], self.hrefs)


class TheStripeSaysWhereTheInverseLivesAfterThisPage(unittest.TestCase):
    """F5. Packet ATT-1o, author A15, 2026-09-11.

    WHAT THIS CLASS CAN SEE: the attribute the server renders on each act form, and the sentence and
    the link the page's own script builds out of it. It drives no browser, so it cannot say the
    stripe appeared or that the link resolves -- Closure's tester owns both. It CAN say that the
    clause is there, that it is on exactly the stripes that carry an inverse, and that the page it
    names is the route `web/app.py` declares.
    """

    @classmethod
    def setUpClass(cls):
        cls.body = an_app().test_client().get("/attention/").get_data(as_text=True)
        cls.rows = rows_of(cls.body)
        cls.forms = forms_in(cls.body)
        cls.source = template_source("inbox.html")
        cls.stripe = function_body(cls.source, STRIPE_FN_RE, "function stripe")
        cls.land = function_body(cls.source, LAND_FN_RE, "function land")
        cls.with_page = TASK_ATTR_RE.findall(cls.body)
        cls.without = NO_HOME_ATTR_RE.findall(cls.body)
        cls.sentences = {name: js_string_value(const_source(cls.body, name))
                         for name in ("STAY", "AFTER_DONE_CLAUSE", "AFTER_ACCEPT_CLAUSE",
                                      "STAY_NO_PAGE_HEAD", "STAY_NO_PAGE_TAIL")}
        print("\n  DENOMINATORS, F5's clause")
        print("    act forms on the page         %d" % len(cls.forms))
        print("    forms naming a durable page   %d %s" % (len(cls.with_page), cls.with_page))
        print("    forms naming none             %d %s" % (len(cls.without), cls.without))
        for name, text in sorted(cls.sentences.items()):
            print("    %-26s %r" % (name, text))

    def test_every_act_form_declares_exactly_one_of_the_two_and_never_both(self):
        # The denominator is the number of act forms, not a literal: a fifth control added later
        # without either attribute would render a stripe whose sentence is chosen by a fallback, and
        # that is red here rather than vague in a browser.
        self.assertEqual(len(self.forms), 3, self.forms)
        self.assertEqual(len(self.with_page) + len(self.without), len(self.forms),
                         "%d + %d for %d forms" % (len(self.with_page), len(self.without),
                                                   len(self.forms)))
        both = [f for f in self.forms if TASK_ATTR_RE.search(f) and NO_HOME_ATTR_RE.search(f)]
        self.assertEqual(both, [])

    def test_the_two_work_item_rows_name_their_own_page_and_the_question_names_none(self):
        # The gate is the SOURCE TYPE and never the kind: `KIND_BY_ARM`'s `review` is emitted by
        # arms 3 and 4 as well, which need not be work items, so a kind test would hand a question
        # row a `/task/` link that 404s.
        self.assertIn('data-att-task="/task/w-review"', self.rows["work_item:w-review"])
        self.assertIn('data-att-task="/task/w-human"', self.rows["work_item:w-human"])
        self.assertNotIn("data-att-task", self.rows["question:q-1"])
        self.assertIn(QUESTION_NO_HOME, self.rows["question:q-1"])

    def test_the_objective_row_says_what_the_no_undo_reason_already_says(self):
        # D4's row, rendered on its own page for the reason `AnObjectiveRowOffersTriageAndNothingElse`
        # renders one: a sixth row in `ROWS` would move the page-wide counts above as a side effect.
        body = an_app(StubAttentionPort(ROWS + [OBJECTIVE])).test_client()\
            .get("/attention/").get_data(as_text=True)
        row = rows_of(body)["objective:6"]
        self.assertIn('data-att-no-home="%s"' % OBJECTIVE_NO_HOME, row)
        self.assertNotIn("data-att-task", row)

    def test_the_clause_is_the_one_the_packet_named_and_e2s_own_clause_survives_it(self):
        self.assertEqual(self.sentences["AFTER_DONE_CLAUSE"], F5_DONE_CLAUSE)
        self.assertEqual(self.sentences["AFTER_ACCEPT_CLAUSE"], F5_ACCEPT_CLAUSE)
        self.assertEqual(self.sentences["STAY"], E2_SENTENCE)

    def test_the_unchanged_sentence_is_unchanged(self):
        # The packet's own constraint: where the stripe carries no inverse, nothing moves. Byte
        # equality against the string X7 shipped, not "contains".
        self.assertEqual(self.sentences["STAY"],
                         "This receipt stays until you leave this page. "
                         "The record is on the item’s own thread.")

    def test_the_sentence_is_chosen_by_whether_the_server_said_an_inverse_exists(self):
        # One decision in one place, on the SAME term E3 and E4 are decided on (`data.undo`), so the
        # sentence cannot disagree with the control rendered two lines below it.
        chooser = function_body(
            self.source,
            re.compile(r"\n  function stayLine\(data, taskUrl, noHomeNote\) \{(?P<body>.*?)\n  \}\n",
                       re.S),
            "function stayLine")
        self.assertIn("if (!data.undo) return STAY;", chooser)
        self.assertIn("if (taskUrl) return STAY + afterPage(data.verb).clause;", chooser)
        self.assertIn("return STAY + STAY_NO_PAGE_HEAD + (noHomeNote || NO_HOME_FALLBACK) + STAY_NO_PAGE_TAIL",
                      chooser)
        self.assertEqual(self.source.count("function stayLine("), 1)

    def test_the_link_is_rendered_only_where_both_terms_hold(self):
        # An inverse AND a page that carries one. A link under either of the other two sentences
        # would contradict the words beside it.
        self.assertIn("if (data.undo && taskUrl) {", self.stripe)
        self.assertIn("homeLink.setAttribute('href', taskUrl);", self.stripe)
        self.assertIn("afterPage(data.verb).label", self.stripe)
        self.assertIn("el('div', 'att-stay', stayLine(data, taskUrl, noHomeNote))", self.stripe)

    def test_the_stripe_carries_both_carriers_onward_so_an_undo_inherits_them(self):
        # `land()` reads the nearest ancestor that declares one, so a form this page BUILT -- which
        # the server never saw -- inherits the row's durable page from the stripe it sits in.
        self.assertIn("tr.setAttribute('data-att-task', taskUrl);", self.stripe)
        self.assertIn("tr.setAttribute('data-att-no-home', noHomeNote);", self.stripe)
        self.assertIn("form.closest('[data-att-task]')", self.land)
        self.assertIn("form.closest('[data-att-no-home]')", self.land)

    def test_the_target_the_link_carries_is_a_control_sized_anchor(self):
        # F2's rule, worn by the one anchor on this page that no server renders. The class is
        # asserted here rather than in the CSS class below because this is the only place the
        # script decides it.
        self.assertIn("el('a', 'att-link att-task', afterPage(data.verb).label)", self.stripe)


# ------------------------------------------------- F8: one name per control, and never per column
#
# Closure's P14 pane pass, C5-8 row S4: "no name shared by controls acting on distinct rows",
# UNMET. MEASURED on the loaded mount, denominator 7 rows: distinct act-button names **2** across
# **7** buttons on **7** distinct rows -- `Mark done` naming 5 of them and `Accept work` 2 -- and
# distinct summary-field names **1** across **5** fields on **5** distinct rows. S2 was 65 of 65
# named on the same page, so the gap is in exactly one place, and it is the place where the writes
# happen.

#: FOUR ROWS PER KIND, which is the shape the finding is about: a name is only ambiguous beside
#: another control that shares it. Four rather than two so that a template which appended, say, the
#: rank instead of the title would still be visibly wrong, and so the denominator is not so small
#: that "distinct" and "present" become the same assertion.
def a_family(kind, prefix, count=4):
    return [an_item(item_id="work_item:%s-%d" % (prefix, n) if kind != "question"
                    else "question:%s-%d" % (prefix, n),
                    kind=kind, rank=n, tier=1,
                    title="Row %d, %s work nobody else is doing" % (n, prefix))
            for n in range(1, count + 1)]


#: The four control kinds this file's own template names, each with the label pattern it wears and
#: the visible text it must keep. The visible text is the POSITIVE CONTROL for every distinctness
#: count below: it is the thing that really is repeated N times, so an instrument that cannot see a
#: collision in it could not be trusted to report an absence of one in the names.
NAME_PATTERNS = {
    "review": [("button", "Accept work: %s", "Accept work")],
    "human": [("button", "Mark done: %s", "Mark done"),
              ("field", "What you did, in one line for %s", None)],
    "question": [("button", "Answer: %s", "Answer")],
}

BUTTON_RE = re.compile(r"<button\b[^>]*>", re.S)
FIELD_RE = re.compile(r'<input\b[^>]*class="att-field"[^>]*>', re.S)
ARIA_LABEL_RE = re.compile(r'aria-label="([^"]*)"')


def names_of(tags):
    """Every accessible name these tags carry, in order, and `None` where a tag carries none."""
    out = []
    for tag in tags:
        found = ARIA_LABEL_RE.search(tag)
        out.append(found.group(1) if found else None)
    return out


def act_controls_text(body):
    """The rendered ROWS only, joined -- never the whole page.

    Measured while writing this class, and it is the reason the helper exists: a scan of the whole
    body finds a fifth unnamed `button` that belongs to `base.html`'s chrome (the theme control),
    which is not this seat's file and is not what S4 is about. Scoping to the rows is what makes
    every count below a count of THIS room's act controls; the chrome's own naming is reported to
    the Admiral rather than quietly folded into a green.
    """
    return "".join(rows_of(body).values())


class EveryActControlIsNamedForItsOwnRow(unittest.TestCase):
    """F8, S4. Packet ATT-1o, author A15, 2026-09-11.

    RENDERED, not scanned: what is under test is the name a person HEARS, and that is a property of
    the bytes the server sends rather than of a line in a macro. This file cannot compute an
    accessible name the way a browser does -- it reads `aria-label`, which is what overrides
    everything else in the accname order -- so what it proves is that each control carries a
    distinct label, and the tester with a real reader is the one who can say what was spoken.
    """

    @classmethod
    def setUpClass(cls):
        cls.pages = {}
        for kind, prefix in (("review", "review"), ("human", "human"), ("question", "question")):
            rows = a_family(kind, prefix)
            body = an_app(StubAttentionPort(rows)).test_client()\
                .get("/attention/").get_data(as_text=True)
            cls.pages[kind] = (rows, act_controls_text(body), rows_of(body))
        print("\n  DENOMINATORS, one name per control")
        for kind, (rows, body, rendered) in sorted(cls.pages.items()):
            buttons = names_of(BUTTON_RE.findall(body))
            fields = names_of(FIELD_RE.findall(body))
            print("    %-9s rows %d rendered %d | buttons %d, distinct names %d | fields %d, "
                  "distinct names %d"
                  % (kind, len(rows), len(rendered), len(buttons), len(set(buttons)),
                     len(fields), len(set(fields))))

    def test_the_fixture_rendered_four_rows_of_each_kind(self):
        # The denominator before every claim below. Four distinct names over one rendered row would
        # be impossible, and four over four rows that did not render would be vacuous.
        for kind, (rows, body, rendered) in sorted(self.pages.items()):
            self.assertEqual(len(rendered), 4, kind)
            self.assertEqual(len(forms_in(body)), 4, kind)

    def test_the_instrument_can_see_a_collision_where_one_really_is(self):
        # THE POSITIVE CONTROL. The VISIBLE text of these buttons is genuinely repeated four times,
        # and that is what the finding was about before the labels existed: if the reader below
        # cannot score 1 distinct over four identical strings, its 4 over the names means nothing.
        for kind, (rows, body, rendered) in sorted(self.pages.items()):
            visible = [text for _, _, text in NAME_PATTERNS[kind] if text]
            for text in visible:
                self.assertEqual(body.count(">%s<" % text), 4, (kind, text))
                self.assertEqual(len({text}), 1)

    def test_n_rows_yield_n_distinct_names_for_every_control_kind(self):
        # S4 itself, as a ratio and never as a literal: distinct names EQUALS controls, per kind, so
        # a fifth row added later without a label is red here rather than a second voice saying the
        # same words.
        for kind, (rows, body, rendered) in sorted(self.pages.items()):
            for control, pattern, _ in NAME_PATTERNS[kind]:
                tags = (BUTTON_RE if control == "button" else FIELD_RE).findall(body)
                found = names_of(tags)
                self.assertEqual(len(found), len(rows), (kind, control, found))
                self.assertNotIn(None, found, (kind, control, "a control carries no name at all"))
                self.assertEqual(len(set(found)), len(rows), (kind, control, found))
                self.assertEqual(sorted(found), sorted(pattern % row.title for row in rows),
                                 (kind, control))

    def test_the_name_leads_with_the_verb_and_the_visible_text_did_not_move(self):
        # The visible words are a PREFIX of the spoken ones rather than something else entirely,
        # which is what keeps "press the button that says X" true for a person using both channels.
        for kind, (rows, body, rendered) in sorted(self.pages.items()):
            for control, pattern, text in NAME_PATTERNS[kind]:
                if not text:
                    continue
                for row in rows:
                    self.assertTrue((pattern % row.title).startswith(text), (kind, row.title))
                self.assertEqual(body.count(">%s<" % text), len(rows), (kind, text))

    def test_the_objective_control_is_named_for_its_row_too(self):
        # D4's verb is not in `NAME_PATTERNS` above because only one objective can be rendered from
        # this stub, so it cannot carry a distinctness ratio. It is asserted for presence on the
        # same rule: every control on this page names the row it writes to.
        body = an_app(StubAttentionPort(ROWS + [OBJECTIVE])).test_client()\
            .get("/attention/").get_data(as_text=True)
        row = rows_of(body)["objective:6"]
        self.assertIn('aria-label="Accept objective: %s"' % OBJECTIVE.title, row)

    def test_no_control_on_the_default_page_shares_a_name_with_another(self):
        # And the whole page, over the five-row fixture the rest of this file uses: three act
        # controls on three distinct rows, three distinct names, plus the two fields.
        rows = act_controls_text(an_app().test_client().get("/attention/").get_data(as_text=True))
        labelled = names_of(BUTTON_RE.findall(rows) + FIELD_RE.findall(rows))
        self.assertEqual(len(labelled), 5, labelled)
        self.assertEqual(len(set(labelled)), 5, labelled)


# ------------------------------ F2 and F3: a control you can hit, and a sort you can reach at 375
#
# WHAT THIS FILE CANNOT SAY, first and plainly: **F2 and F3 are geometry, and geometry is measured
# in a viewport.** Nothing below reports a pixel. A `min-height` in a stylesheet is a rule, not a
# rendered box: a parent with a fixed height, a `line-height` on an ancestor or a later rule in
# `console.css` could all defeat it, and only the tester with a real 375 px pane can say whether the
# 16 of 65 (1440) and 22 of 65 (375) that Closure MEASURED under 24 px are now 0. What these cases
# own is the half that CAN be pinned in source: that every control on this page is in the family
# that carries the rule, and that a control which is invisible at one width has another way in.
#
# Closure's list of the under-size 16 at 1440 is entirely this template's: 7 rank chips, 7 `Inspect`
# links, 2 sort links. At 375 the 22 are 7 rank chips, 7 metadata chips, 7 `Inspect` -- 21 of this
# template's -- plus **1 nav link**, which belongs to `base.html`'s shared chrome and is not this
# seat's file. So the reachable ceiling here is 16 of 16 and 21 of 22.

#: The rule every sized control wears, asserted as the exact declaration rather than as three
#: separate substrings: a `min-height` without `box-sizing` measures differently once the padding is
#: counted, and a `min-height` on an inline box does nothing at all.
SIZE_RULE = "box-sizing:border-box;min-height:24px;min-width:24px"

#: The narrow-width block, and the rule inside it that causes F3.
NARROW_BLOCK_RE = re.compile(r"@media\(max-width:760px\)\{(?P<body>(?:[^{}]|\{[^{}]*\})*)\}", re.S)
THEAD_HIDDEN = ".att-table thead{display:none}"

#: A declaration that is demonstrably in this stylesheet and one that demonstrably is not, so a
#: scanner that reports "the rule is there" has been shown able to report that it is not.
CONTROL_RULE_PRESENT = ".att-sort{display:none}"
CONTROL_RULE_ABSENT = ".att-sort{display:CONTROL-NO-SUCH-VALUE}"

#: The narrow-width sort control, by the name a reader meets it under.
SORT_NAV_RE = re.compile(r'<nav class="att-sort" aria-label="Sort this view">(?P<body>.*?)</nav>',
                         re.S)
TABS_NAV_RE = re.compile(r'<nav class="att-tabs".*?</nav>', re.S)
HREF_RE = re.compile(r'href="([^"]*)"')
ANCHOR_RE = re.compile(r"<a\b[^>]*>", re.S)
SECTION_RE = re.compile(r"<section data-attention-queue.*?</section>", re.S)


class EveryControlOnThisPageIsInTheFamilyThatCarriesTheSizeRule(unittest.TestCase):
    """F2. Packet ATT-1o, author A15, 2026-09-11. SOURCE, and it is a weaker claim than the finding.

    Closure MEASURED, denominator 65 real focusables: 16 under 24 px in at least one dimension at
    1440 and 22 at 375, worst cases the per-row `Inspect` link at 40 x **16** on a phone and the
    `Freshness` sort link at 62 x **14**. `Inspect` is the only route from a row to its evidence.
    The act door itself was clean at both widths (0 of 7 buttons, 0 of 5 fields), so this is a
    reachability defect on the READING path rather than on the writing one.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = template_source("inbox.html")
        body = an_app().test_client().get("/attention/").get_data(as_text=True)
        cls.section = SECTION_RE.search(body).group(0)
        cls.outside_tabs = TABS_NAV_RE.sub("", cls.section)
        cls.anchors = ANCHOR_RE.findall(cls.outside_tabs)
        cls.unsized = [a for a in cls.anchors
                       if "att-link" not in a and "att-chip" not in a]
        print("\n  DENOMINATORS, the 24 px family")
        print("    the size rule in the stylesheet   %d  (control present: %d, absent: %d)"
              % (cls.source.count(SIZE_RULE), cls.source.count(CONTROL_RULE_PRESENT),
                 cls.source.count(CONTROL_RULE_ABSENT)))
        print("    anchors in the queue section      %d  (filter strip excluded: %d)"
              % (len(cls.anchors), len(ANCHOR_RE.findall(cls.section)) - len(cls.anchors)))
        print("    of those carrying no size class   %d %s" % (len(cls.unsized), cls.unsized))

    def test_the_scanner_can_report_a_rule_that_is_not_there(self):
        # The control, first. `assertIn` over a stylesheet is the cheapest green in this file unless
        # the same reader has been watched failing to find something.
        self.assertEqual(self.source.count(CONTROL_RULE_PRESENT), 1)
        self.assertEqual(self.source.count(CONTROL_RULE_ABSENT), 0)

    def test_the_three_families_of_control_carry_the_rule(self):
        # `.att-link` is every anchor that is a control, `.att-chip` is the evidence chips, and the
        # filter strip's own anchors are the third. Asserted as one dict so a failure names WHICH.
        families = {".att-link": ".att-link{display:inline-block;" + SIZE_RULE,
                    ".att-chip": ".att-chip{display:inline-flex;align-items:center;" + SIZE_RULE,
                    ".att-tabs a": SIZE_RULE}
        missing = {name: rule for name, rule in families.items() if rule not in self.source}
        self.assertEqual(missing, {}, missing)
        # `.att-tabs a` shares the bare rule with the other two, so it is pinned by its own selector
        # as well rather than by a substring that another rule could be satisfying for it.
        self.assertRegex(self.source, r"\.att-tabs a\{[^}]*" + re.escape(SIZE_RULE))

    def test_every_control_anchor_the_server_renders_is_in_one_of_those_families(self):
        # THE RATIO, so a link added later with no size is red here rather than 16 px tall in a
        # browser. The filter strip is excluded by subtraction and its count is printed above, so
        # this is a scoped denominator and not a quiet one.
        self.assertGreaterEqual(len(self.anchors), 5 * 5, len(self.anchors))
        self.assertEqual(self.unsized, [], self.unsized)

    def test_the_act_door_itself_was_already_clean_and_is_not_touched(self):
        # Closure measured 0 of 7 buttons and 0 of 5 fields under 24 px. A "fix" that restyled the
        # buttons would be changing what was already met, so `.att-btn`'s own padding is asserted
        # unchanged.
        self.assertIn(".att-act .att-btn{font:inherit;font-size:.72rem;padding:.22rem .5rem;",
                      self.source)


class TheSortIsReachableAtNarrowWidths(unittest.TestCase):
    """F3, and C1-g with it. Packet ATT-1o, author A15, 2026-09-11.

    MEASURED at 375 by Closure: the two sort links have a bounding rect of width 0 and height 0,
    because `.att-table thead{display:none}` is what makes the card layout, and sorting -- which
    this page's own intro sentence offers -- has no reachable control at that width at all.

    THE CHOICE IS THE LEAST LAYOUT OF THE TWO. Showing the `thead` as a strip would mean undoing
    `display:block` on the table, the tbody, the rows and the cells, which ARE the card layout, and
    would then show nine header cells of which two sort. This is one `nav` of two links, hidden
    above 760 px so no reader meets the same two controls twice.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = template_source("inbox.html")
        cls.narrow = "\n".join(match.group("body") for match in NARROW_BLOCK_RE.finditer(cls.source))
        cls.body = an_app().test_client().get("/attention/").get_data(as_text=True)
        cls.nav = SORT_NAV_RE.search(cls.body)
        cls.nav_hrefs = HREF_RE.findall(cls.nav.group("body")) if cls.nav else []
        head = re.search(r"<thead>.*?</thead>", cls.body, re.S)
        cls.head_hrefs = HREF_RE.findall(head.group(0)) if head else []
        print("\n  DENOMINATORS, the sort at 375")
        print("    narrow-width block captured       %d characters" % len(cls.narrow))
        print("    the rule that causes F3           %d  (control: %d)"
              % (cls.narrow.count(THEAD_HIDDEN), cls.narrow.count(CONTROL_RULE_ABSENT)))
        print("    sort links in the header          %d %s" % (len(cls.head_hrefs),
                                                               cls.head_hrefs))
        print("    sort links in the narrow nav      %d %s" % (len(cls.nav_hrefs), cls.nav_hrefs))

    def test_the_cause_is_still_the_cause_and_the_reader_can_see_it(self):
        # The narrow block really was captured and really does hide the header. Without this the
        # assertions below would be about a stylesheet nobody has read.
        self.assertGreater(len(self.narrow), 200, self.narrow)
        self.assertEqual(self.narrow.count(THEAD_HIDDEN), 1, self.narrow)
        self.assertEqual(self.narrow.count(CONTROL_RULE_ABSENT), 0)

    def test_the_control_is_hidden_at_wide_widths_and_shown_at_narrow_ones(self):
        # BOTH halves. Only the second is F3's repair; the first is what stops it from being two
        # sort controls in the tab order at 1440, which would be F3's own defect in the other
        # direction. `display:none` removes an element from the tab order as well as from the paint.
        self.assertIn(CONTROL_RULE_PRESENT, self.source)
        self.assertIn(".att-sort{display:flex;", self.narrow)

    def test_the_narrow_control_renders_two_links_named_for_what_they_sort(self):
        self.assertIsNotNone(self.nav, "no sort nav rendered, so every claim below is vacuous")
        self.assertEqual(len(self.nav_hrefs), 2, self.nav_hrefs)
        self.assertIn("Sort by", self.nav.group("body"))
        for word in ("Title", "Freshness"):
            self.assertIn(word, self.nav.group("body"))
        # And it is a control, so it wears F2's rule like the rest.
        self.assertEqual(self.nav.group("body").count('class="att-link"'), 2)

    def test_the_two_widths_sort_by_exactly_the_same_urls(self):
        # THE DRIFT GUARD, and the reason the urls are two template variables rather than four
        # expressions. A narrow control that sorted by a different direction toggle than the header
        # would be a second sort wearing the first one's words.
        self.assertEqual(len(self.head_hrefs), 2, self.head_hrefs)
        self.assertEqual(self.nav_hrefs, self.head_hrefs)
        self.assertEqual(self.source.count("sort_title_url"), 3, "one set, two reads")
        self.assertEqual(self.source.count("sort_freshness_url"), 3, "one set, two reads")

    def test_the_control_follows_the_table_it_sorts_and_never_appears_without_one(self):
        # An empty view renders no table, so a sort control on it would offer to reorder nothing.
        empty = an_app(StubAttentionPort([])).test_client().get("/attention/").get_data(as_text=True)
        self.assertIsNone(SORT_NAV_RE.search(empty))
        # The positive control on that zero: the same reader finds it on the loaded page above.
        self.assertIsNotNone(self.nav)


# ------------------------------------------------- F9: the page the receipt sends people to, named
#
# Closure MEASURED on `/attention/work_item:<id>`: `h1` count **0** on a page of 80 elements whose
# own `title` is `Where this came from`. C5-8's S6 asks for exactly one. The queue page satisfies
# it; the inspector, which the stripe links to and which is the ONLY place an acted row can be read
# (F5), had no heading at all.
#
# F9's OTHER HALF IS NOT TAKEN HERE and the omission is deliberate: the missing `main` landmark on
# the queue is page skeleton, `base.html` is not this seat's file, and one room quietly declaring a
# landmark inside a shared shell is how the chrome collision happened the first time.

H1_RE = re.compile(r"<h1\b[^>]*>(?P<text>.*?)</h1>", re.S)
BOLD_RE = re.compile(r"<b\b[^>]*>(?P<text>.*?)</b>", re.S)

#: One fragment with two headings and one with none, so "exactly one" is a reading rather than the
#: output of a regex that can only return what the assertion wants.
H1_CONTROL_WITH = "<h1 class=\"CONTROL\">CONTROL-ONE</h1><h1>CONTROL-TWO</h1>"
H1_CONTROL_WITHOUT = "<b>CONTROL-ONE</b><div>CONTROL-TWO</div>"

#: The rule that makes this a semantics repair and not a redesign. `console.css` authors no `h1`
#: rule at all, so a bare `h1` would take the browser's own 2em.
TITLE_RULE = ".att-title{font:inherit;font-weight:700;margin:0}"


class TheInspectorCarriesExactlyOneHeadingAndItIsTheItem(unittest.TestCase):
    """F9. Packet ATT-1o, author A15, 2026-09-11. RENDERED, on both of the page's two branches.

    The unreadable branch is not a degraded version of the readable one -- there is no content to
    degrade -- but it is a page a person can arrive at, so it carries its heading too. Both are
    asserted because they are two `<b>`s in the template and a repair that moved one would leave
    the other reading as body text in a card.
    """

    @classmethod
    def setUpClass(cls):
        client = an_app().test_client()
        cls.pages = {
            "readable": (REVIEW, client.get("/attention/work_item:w-review")
                         .get_data(as_text=True)),
            "unreadable": (RESTRICTED, client.get("/attention/work_item:w-restricted")
                           .get_data(as_text=True)),
        }
        cls.source = template_source("inspect.html")
        print("\n  DENOMINATORS, the inspector's heading")
        print("    control: two headings / none      %d / %d"
              % (len(H1_RE.findall(H1_CONTROL_WITH)), len(H1_RE.findall(H1_CONTROL_WITHOUT))))
        for branch, (item, body) in sorted(cls.pages.items()):
            print("    %-11s page %6d bytes   h1 %d %s   (b elements on it: %d)"
                  % (branch, len(body), len(H1_RE.findall(body)), H1_RE.findall(body),
                     len(BOLD_RE.findall(body))))

    def test_the_counter_can_see_two_and_can_see_none(self):
        # The control, first and both ways. A reader that scored 1 on everything would make every
        # assertion below free.
        self.assertEqual(len(H1_RE.findall(H1_CONTROL_WITH)), 2)
        self.assertEqual(len(H1_RE.findall(H1_CONTROL_WITHOUT)), 0)

    def test_both_branches_rendered_before_anything_is_counted_on_them(self):
        # A verdict over a 404 is not a pass. Each page is the inspector and says so in its own
        # words, and each still carries the two `<b>` section headings that were NOT promoted --
        # which is the positive control that the tag readers reached the document.
        for branch, (item, body) in sorted(self.pages.items()):
            self.assertIn('data-evidence-origin>Fixture evidence</span>', body, branch)
            self.assertIn('inspector &middot; read only.', body, branch)
            self.assertGreaterEqual(len(BOLD_RE.findall(body)), 2, branch)

    def test_exactly_one_h1_on_each_branch_and_it_is_the_items_title(self):
        # S6, and the heading is the ITEM rather than the room: a person following a receipt
        # stripe's link is arriving at one row, and `Where this came from` is already the page title.
        for branch, (item, body) in sorted(self.pages.items()):
            found = H1_RE.findall(body)
            self.assertEqual(len(found), 1, (branch, found))
            self.assertEqual(found[0].strip(), item.title, branch)

    def test_the_heading_renders_as_the_bold_did(self):
        # The look is pinned, because this repair was licensed to change the semantics and not the
        # screen. `console.css` authors no `h1` rule, so without this the browser's own 2em lands on
        # a page nobody asked to re-baseline.
        self.assertIn(TITLE_RULE, self.source)
        self.assertEqual(self.source.count('<h1 class="att-title">{{ shown.title }}</h1>'), 2,
                         "one per branch, and the two branches are exclusive")

    def test_the_queue_page_still_has_its_own_single_heading(self):
        # S6 was already MET there and this packet does not touch it. Asserted rather than assumed:
        # the two templates are read by the same reader, and a change to one that moved the other
        # would be invisible otherwise.
        queue = an_app().test_client().get("/attention/").get_data(as_text=True)
        self.assertEqual(H1_RE.findall(queue), ["Attention"])

    def test_no_main_landmark_was_declared_by_this_room(self):
        # F9's other half, NOT taken, and pinned as not-taken so a later packet has to decide it
        # rather than drift into it. `base.html` is not this seat's file.
        self.assertEqual(self.source.count("<main"), 0)
        self.assertEqual(template_source("inbox.html").count("<main"), 0)


#: WHERE `msg` SITS IN EACH ASSERTION THIS FILE IS ALLOWED TO USE. Written out rather than
#: derived, and the scanner below FAILS on any assertion name that is missing from it: a guard
#: that silently skipped what it did not recognise would go quiet exactly when a new author
#: reached for a new assertion, which is the moment it is for.
ASSERT_MSG_INDEX = {
    "assertEqual": 2, "assertNotEqual": 2, "assertIn": 2, "assertNotIn": 2,
    "assertIs": 2, "assertIsNot": 2, "assertIsInstance": 2, "assertNotIsInstance": 2,
    "assertGreater": 2, "assertGreaterEqual": 2, "assertLess": 2, "assertLessEqual": 2,
    "assertRegex": 2, "assertNotRegex": 2, "assertCountEqual": 2, "assertListEqual": 2,
    "assertDictEqual": 2, "assertSetEqual": 2, "assertTupleEqual": 2, "assertMultiLineEqual": 2,
    "assertTrue": 1, "assertFalse": 1, "assertIsNone": 1, "assertIsNotNone": 1,
    "assertAlmostEqual": 3, "assertNotAlmostEqual": 3,
}
#: The context-manager assertions. They take no `msg` positionally, so there is no message to
#: scan; named here so they are registered rather than unrecognised.
ASSERT_NO_MSG = frozenset({"assertRaises", "assertRaisesRegex", "assertWarns", "assertWarnsRegex",
                           "assertLogs", "assertNoLogs"})
#: What a token is CALLED. The scan is on the name of the thing interpolated, not on its value --
#: a test file cannot know a secret by looking at it, but it can see that it is about to print one.
TOKENISH = re.compile(r"csrf|token", re.I)
#: The ONLY two functions allowed to stand between a token and an assertion message. Both are at
#: the top of this file and neither can emit a value: `length_class` reports a length and a prefix,
#: `token_redacted` replaces every value with its length class in place. Naming them here is what
#: keeps the guard a guard rather than a ban on saying the word.
REDACTORS = frozenset({"length_class", "token_redacted"})

OWN_SOURCE = os.path.abspath(__file__)


def assertion_messages(source):
    """Every assertion `msg` expression in `source`, as (lineno, method, expression node).

    Parsed rather than grepped: `msg` is a positional argument at a different index for each
    assertion and a keyword for some calls, and a regular expression over the text would have to
    guess at both. Returns the messages and the set of assertion names it did not recognise, so
    the caller can fail on the second rather than quietly under-report the first.
    """
    messages, unknown = [], set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        name = node.func.attr
        if not name.startswith("assert") or name in ASSERT_NO_MSG:
            continue
        if name not in ASSERT_MSG_INDEX:
            unknown.add(name)
            continue
        message = None
        index = ASSERT_MSG_INDEX[name]
        if len(node.args) > index:
            message = node.args[index]
        for keyword in node.keywords:
            if keyword.arg == "msg":
                message = keyword.value
        if message is not None:
            messages.append((node.lineno, name, message))
    return messages, unknown


def names_in(expression):
    """Every identifier an expression reads, bare names and attribute names alike.

    `token.group(1)` reads `token` and `group`; `self.csrf_value` reads `csrf_value`. Both shapes
    are how a token has actually reached an assertion message in this lane, so both are read.

    EXCEPT THROUGH THE TWO FUNCTIONS THAT EXIST TO MASK IT, and this is the difference between a
    guard and a ban. F6 is about a token's VALUE reaching a log; `length_class(token)` is a token's
    length and prefix and is exactly what a person debugging the failure needs. The subtree under a
    sanctioned call is therefore pruned rather than read, so the one safe way to mention a token in
    a message stays available and everything else stays visible.
    """
    found = []

    def walk(node):
        if isinstance(node, ast.Call):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if called in REDACTORS:
                return
        if isinstance(node, ast.Name):
            found.append(node.id)
        elif isinstance(node, ast.Attribute):
            found.append(node.attr)
        for child in ast.iter_child_nodes(node):
            walk(child)

    walk(expression)
    return found


class NoAssertionMessageInThisFilePrintsATokenValue(unittest.TestCase):
    """R2's F6 on X7, as a guard over this whole file rather than a repair of one line.

    Packet ATT-1q, author A16, 2026-09-11.

    WHAT F6 FOUND. `test_it_carries_exactly_one_accept_objective_form_on_the_bare_id` asserted
    against `self.row`, the rendered row's HTML, so `unittest`'s diff printed the whole `<form>` --
    including the room token minted for the session. R2 saw the value in its own plant run and did
    not reproduce it. On a scratch database with a test-client session it is harmless, which is
    why F6 is a note; the reason it is worth a guard is that every browser tester in this lane runs
    a token scanner over its transcripts, and a FAILING assertion is the one path that writes a
    token into a log nobody scanned. The repair is `token_redacted` at the top of this file. This
    class is the half that keeps it repaired.

    WHAT IT SCANS AND WHAT IT CANNOT SEE, said plainly. It reads the NAMES an assertion message
    interpolates, not values: a message built from a variable called `token` or `csrf` is a finding
    whatever that variable holds, and a message built from a variable called `x` that happens to
    hold a token is invisible to it. It also says nothing about the standard assertion diff -- the
    row and page copies this file asserts against are redacted at their source instead, which is
    the only place that problem can be fixed. What this guard catches is the shape that actually
    occurred: a token handed to an assertion AS its message.
    """

    def test_the_scanner_catches_the_shape_it_is_looking_for(self):
        # THE POSITIVE CONTROL, READ BEFORE THE ZERO. Three planted messages: two carry a token by
        # name in the two shapes this lane has produced, one is a plain sentence. A scanner that
        # returned an empty list for everything would pass the test below and mean nothing.
        planted = (
            "class Planted:\n"
            "    def t(self):\n"
            "        self.assertTrue(ok, token.group(1))\n"
            "        self.assertIn('a', b, msg=self.csrf_value)\n"
            "        self.assertEqual(1, 1, 'a message with no secret in it')\n"
            "        self.assertTrue(ok, length_class(token.group(1)))\n"
            "        self.assertIn('a', b)\n")
        messages, unknown = assertion_messages(planted)
        self.assertEqual(unknown, set())
        self.assertEqual(len(messages), 4, "the reader found the wrong number of messages")
        flagged = sorted(name for _line, _method, expr in messages
                         for name in names_in(expr) if TOKENISH.search(name))
        # The two raw shapes are caught; the masked one is not, and that is the pruning in
        # `names_in` being exercised rather than asserted. A `flagged` of three would mean the
        # guard had become a ban on the safe form, and a `flagged` of one or zero would mean it
        # had stopped seeing the unsafe ones.
        self.assertEqual(flagged, ["csrf_value", "token"])

    def test_no_assertion_message_in_this_file_interpolates_a_token(self):
        with open(OWN_SOURCE, "r", encoding="utf-8") as handle:
            source = handle.read()
        messages, unknown = assertion_messages(source)
        offenders = [(line, method, name) for line, method, expr in messages
                     for name in names_in(expr) if TOKENISH.search(name)]
        print("\n  DENOMINATORS, assertion messages in this file")
        print("    assertion calls carrying a message   %d" % len(messages))
        print("    unrecognised assertion names         %d" % len(unknown))
        print("    messages naming a token or csrf      %d" % len(offenders))
        self.assertEqual(sorted(unknown), [],
                         "an assertion this scanner does not know: add it to ASSERT_MSG_INDEX "
                         "with the index its msg takes, or to ASSERT_NO_MSG if it takes none")
        self.assertGreater(len(messages), 0,
                           "the reader found no assertion messages at all, so its zero above is "
                           "a fact about the reader and not about this file")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
