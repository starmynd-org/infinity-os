"""The attention routes. Read only, and structurally unable to be otherwise.

R04. This lives in `web/views/` rather than in `web/blueprints/` because the attention paths this
lane owns are `web/views/**` and `web/templates/attention/**`; a `web/blueprints/attention/`
package would be a path nobody granted. The blueprint is three GET routes over a read port.

There is no POST here and there is no verb table entry for this room, so `rooms.assert_room_can_act`
refuses `attention` before a row is looked up. That is the mechanism, not a promise.

BUT NOT "THE SAME WAY IT REFUSES STUDY", WHICH IS WHAT THIS DOCSTRING SAID UNTIL NOW AND WAS WRONG
ABOUT. Measured by running both, `outputs/2026-09-09-IOS-term-2/probe-attention-refusal.py`:

    assert_room_can_act("attention")  ->  "no such room: 'attention'. One of: queue, brief, ..."
    assert_room_can_act("study")      ->  "the study room calls zero verbs. Nothing posted here
                                           can be permitted..."

Two different branches of `allowed()` with two different messages. **Study is a DECLARED empty
frozenset** and `web/tests/test_allowlist.py` enumerates every verb registered anywhere in the
runtime to prove Study refuses all of them. **`attention` is not a key in `ROOM_VERBS` at all**, so
it takes the `room not in ROOM_VERBS` branch and is refused as UNKNOWN.

THE DIFFERENCE IS NOT PEDANTIC AND IT CUTS AGAINST THIS SURFACE. A refusal that says "no such room"
is indistinguishable, from outside, from a room somebody forgot to list. Study's silence is a policy
with a test behind it; this room's silence is an ABSENCE, and no test can tell an absence from an
oversight. Both refuse today, so nothing is unsafe -- but only one of them is *stated*.

THAT IS NOW HISTORY, AND THE PARAGRAPH ABOVE DESCRIBES THE STATE BEFORE R18. Ruling R18 (Admiral,
`195859Z`), confirmed directly by Andrew, granted `ROOM_VERBS["attention"]` to this seat, and the
key is now in `web/rooms.py`. `assert_room_can_act("attention")` PASSES. See
`outputs/2026-09-09-IOS-term-2/R18-DECISION-PATH-PREPARATION.md`.

THE SET HAS MOVED TWICE SINCE AND IS FIVE: `answer`, `accept work`, `unaccept work`, `done`
(R36) and `reopen` (`_system/attention/RECEIPT-EXPECTATIONS-2026-09-10.md`, 2026-09-10). Counting
it here rather than naming it would age better; it is named because a reader of this docstring is
usually asking exactly which verbs, and `web/rooms.py` is the one place it is decided.

AND THE PAGE NOW RENDERS CONTROLS, WHICH IT DID NOT WHEN THE PARAGRAPHS ABOVE WERE WRITTEN.
Measured on the served `588b366` (`outputs/2026-09-10-ATTENTION-OS-admiral/ATT-1a-merge-and-serve.md`,
section 5): the Attention page carried 0 forms, 0 buttons and 0 mentions of `attention/act`, so the
room's grant was open in the allowlist and unreachable from the screen. Packet ATT-1d puts the
controls on it. They live in `web/templates/attention/controls.html` and post to the one write
door; NOTHING about that changes the sentence below.

STILL TRUE, AND WORTH NOT LOSING: there is no POST route in THIS blueprint and there does not need
to be one. The write door is `web/app.py`'s `@app.post("/<room>/act")`, which is generic over the
room and already matched `/attention/act` before the grant -- the allowlist was the only thing
refusing it. This blueprint remains three GET routes over a read port.
"""

from __future__ import annotations

import inspect
from typing import List, Optional, Protocol

from flask import Blueprint, abort, render_template, request

from .honesty import display_zone
from .queue import RANK_EXPLANATION, SORTS, QueueItem, QueueView

# THE CLIENT GATE'S MINIMUM, MIRRORED FROM `web/actions.py::MIN_REASON`, AND THE MIRROR IS
# DELIBERATE RATHER THAN LAZY.
#
# The authority is `web/actions.py::MIN_REASON` (12 at the time of writing) and it is the SERVER
# that enforces it: `mark_my_task_done` calls `_need(summary, ...)`, which refuses anything
# shorter, in its own words. The number here only decides when a button stops being greyed out, so
# that the common case never round-trips; a person who defeats it gets the server's refusal shown
# in place (E9 of `_system/attention/RECEIPT-EXPECTATIONS-2026-09-10.md`).
#
# WHY NOT `from ..actions import MIN_REASON`: `web/actions.py` imports `rooms`, which imports
# `store`, so that one line would put the console's whole write path inside `web/views/**` --
# `actions.rooms.dispatch` would be reachable from this package by attribute access. E7 is the
# expectation that this package cannot write, and this package's own `__init__` says "nothing here
# reads a store, and nothing here writes". A mirrored integer with a test on it is cheaper than
# that boundary.
#
# THE MIRROR IS PINNED BY TEST, so a divergence is red rather than silent:
# `web/views/tests/test_act_door.py::TheClientGateMirrorsTheServer` imports the real constant and
# asserts equality. If that test is red, this number moved and `web/actions.py` did not, or the
# other way round: change this line, never the server's.
MIN_REASON_MIRROR = 12


class AttentionReadPort(Protocol):
    """What R01 must supply. Read only; there is no write method to call by accident.

    Contract: `outputs/2026-09-09-IOS-term-2/VIEW-CONTRACT-2026-09-09.md`.

    KEYWORD-ONLY, EVERY ARGUMENT. A positional call site that drifts by one argument silently
    swaps a filter for a sort, and this repo has paid for that class of thing before.

    EVERY ARGUMENT OPTIONAL, AND EVERY DEFAULT IS TODAY'S BEHAVIOUR, so the port may land one
    argument at a time and the route keeps working the whole way.

    `limit` HAS THREE STATES AND ONLY TWO OF THEM ARE NUMBERS. `None` means the port chooses its
    own window -- that is the default and what an ordinary request sends. `0` means UNWINDOWED and
    is what a test asks for deliberately. Any positive integer is a page cap.

    Collapsing `None` into `0` is not a tidy-up, it is the page cap being switched off on every
    ordinary request: `term-4` measured that `GET /attention/` was passing an explicit `limit=0`
    and therefore instructing the port to return the entire admitted population.

    The port applies FILTER, then SORT, then SLICE, over the full candidate population. The route
    does not slice and, when the port accepts these arguments, does not filter or sort either.
    """

    def queue(self, *, filter_key: str = "queue", sort: Optional[str] = None,
              descending: bool = False, offset: int = 0,
              limit: Optional[int] = None) -> QueueView: ...

    def item(self, item_id: str) -> Optional[QueueItem]: ...


def _port_accepts(fn, names) -> bool:
    """Does this port's `queue` take the contract's keywords? Inspected, never tried-and-caught.

    A `try/except TypeError` around the call would also catch a TypeError raised INSIDE the port
    and fall back to the unfiltered path, turning a genuine failure into a plausible page. The
    signature is a fact about the callable and reading it cannot mask anything.
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return all(n in params for n in names)


def _page_arg(name: str, absent):
    """A non-negative integer from the query string, or 400. `absent` is what a missing one means.

    400 rather than a silent 0: `?offset=abc` quietly becoming page one is a page that answers a
    question nobody asked, and the reader has no way to tell.

    ABSENT IS NOT ZERO FOR `limit`, AND CONFLATING THEM DISABLED THE PAGE CAP. `term-4` caught it:
    this returned 0 for a missing argument, and the route passed `limit=0` on every ordinary
    request -- but `limit=0` MEANS UNWINDOWED in this contract. So `GET /attention/` was explicitly
    instructing the port to return the entire admitted population, overriding the port's own
    bounded default, which is the exact opposite of what the contract exists to do. `None` means
    "the port chooses"; `0` stays the unwindowed contract a test asks for on purpose.

    `offset` genuinely does default to 0, because there is no third meaning for it.
    """
    raw = request.args.get(name)
    if raw is None or raw == "":
        return absent
    try:
        value = int(raw)
    except ValueError:
        abort(400)
    if value < 0:
        abort(400)
    return value


def _token_when_no_guard_is_attached(room: str) -> str:
    """`csrf_for` for a harness that is not a console. NEVER reached in the real console.

    It returns three dot-separated segments, so it has the SHAPE of a token and none of its
    substance, and the middle segment is not an integer -- which is the branch of
    `guard.check_token` that answers "malformed CSRF token." A placeholder that could be mistaken
    for a live token would be worse than a crash.

    `web/app.py::create_app` calls `guard.attach(app)` before any route is declared, and `attach`
    ASSIGNS `csrf_for` rather than defaulting it, so the real token wins wherever a guard exists.

    This exists because the act-door controls made `csrf_for` a hard requirement of this template,
    and `web/tests/test_routines_and_attention_render.py` builds a Flask app with the two view
    blueprints and no guard at all: it is a render test, it has no write door registered, and
    before this fallback every attention render in it became a 500 (`'csrf_for' is undefined`),
    16 of its 44 tests, MEASURED 2026-09-10. That suite is not this packet's to edit.

    The alternative was `{{ csrf_for(...) if csrf_for is defined }}` in the template, which would
    have made the one field E8 is about conditional in the markup -- and a form that sometimes
    carries no token at all is exactly the thing a reader could not tell from one that always
    does. A token-shaped string that the door refuses in words is the honest failure.
    """
    return "%s.unattached.this-app-has-no-console-guard" % room


def build(port: AttentionReadPort) -> Blueprint:
    from .navigation import FILTERS as NAV_FILTERS, canonical_filter, context, report_url, task_url
    from .queue import FILTERS, FILTER_LABELS, empty_message
    bp = Blueprint("attention", __name__, url_prefix="/attention")

    @bp.record_once
    def _csrf_for_is_required_by_this_surface(state):
        # `setdefault`, so a console that attached its guard keeps the guard's function. See
        # `_token_when_no_guard_is_attached` for why the fallback exists and what it is not.
        state.app.jinja_env.globals.setdefault("csrf_for", _token_when_no_guard_is_attached)

    @bp.context_processor
    def evidence_origin():
        return {'attention_evidence_label': getattr(port, 'evidence_label', 'Evidence source not declared'),
                'attention_intake_observation': getattr(port, 'intake_observation', None)}

    @bp.get("/")
    def inbox():
        predicates = FILTERS
        # VALIDATE BEFORE THE PORT IS CALLED, so the port may assume its arguments are valid and
        # a bad request never reaches the store. Both of these were already 400s; they have moved
        # ahead of the call rather than changed meaning.
        filter_key = canonical_filter(request.args.get("filter", "queue"))
        if filter_key not in predicates:
            abort(400)
        sort = request.args.get("sort")
        if sort is not None and sort not in SORTS:
            abort(400)
        descending = request.args.get("dir") == "desc"
        offset = _page_arg("offset", 0)
        limit = _page_arg("limit", None)

        # THE DEFECT THIS REPLACES. Until now this route called `port.queue()` with no arguments
        # and then applied the filter and the sort to WHATEVER CAME BACK. That is harmless only
        # while the port returns everything -- `web/model.py:2261-2266` returns an empty view
        # unconditionally, so the predicates have been running over an empty tuple. The moment a
        # windowing port lands, filtering the returned page rather than the population makes the
        # `review` tab show a fraction of the review rows AND LOOK COMPLETE: no exception, no
        # text on the page saying otherwise. Filtering a page loses rows; term-4 named it before
        # I found it in my own route.
        #
        # So the filter and the sort go INTO the call, and the slice happens last, inside the
        # port, over the full candidate population.
        #
        # The signature is inspected rather than tried-and-caught. A `try: port.queue(**kw)
        # except TypeError:` would also swallow a TypeError raised INSIDE the port and silently
        # fall back to the unfiltered path -- turning a real failure into a plausible page.
        accepts = _port_accepts(port.queue, ("filter_key", "sort", "descending", "offset", "limit"))
        if accepts:
            view = port.queue(filter_key=filter_key, sort=sort, descending=descending,
                              offset=offset, limit=limit)
        else:
            view = port.queue()

        # THE COMBINATION THAT MUST NEVER BE SERVED. A port that windows or pages but does not
        # accept the filter is exactly the silent-wrong-answer case above. It cannot happen by
        # accident today, and that is precisely why the guard is written now: the day it becomes
        # possible is the day nobody is looking for it. Refuse loudly rather than render.
        if not accepts and (view.window or view.limit or view.offset):
            raise RuntimeError(
                "the port returned a windowed or paged view (window=%r limit=%r offset=%r) but "
                "does not accept filter_key. Filtering a page loses rows and the page would look "
                "complete. Refusing to render." % (view.window, view.limit, view.offset))

        if not accepts:
            # The unwindowed legacy path, unchanged in behaviour and now unreachable from a
            # windowing port. Filtering the whole population here is correct BECAUSE it is the
            # whole population, and the guard above is what keeps that "because" true.
            if filter_key != "queue":
                view = QueueView.ranked(
                    [item for item in view.items if predicates[filter_key](item)],
                    checked_at=view.checked_at,
                )
            if sort:
                view = view.sorted_by(sort, descending=descending)
        getter = getattr(port, "capability_note", None)
        return render_template(
            "attention/inbox.html",
            view=view,
            filter_key=filter_key,
            filter_labels=FILTER_LABELS,
            empty_message=empty_message(view, filter_key) if view.empty else '',
            rank_explanation=RANK_EXPLANATION,
            note=getter() if callable(getter) else None,
            room="attention",
            display_zone=display_zone,
            # The `done` control's client gate. See MIN_REASON_MIRROR above for why this is a
            # mirror and what makes a divergence red.
            min_reason=MIN_REASON_MIRROR,
        )

    def unavailable(item_id, has_refusal=False):
        return render_template('attention/unavailable.html', room='attention',
                               item_id=item_id, has_refusal=has_refusal,
                               return_context=context(request.args)), 404

    @bp.get('/preparation/<item_id>')
    def preparation(item_id):
        lookup = getattr(port, 'refusal', None)
        refusal = lookup(item_id) if callable(lookup) else None
        if refusal is None:
            return unavailable(item_id)
        return render_template('attention/preparation.html', refusal=refusal, room='attention',
                               return_context=context(request.args))

    @bp.get("/<item_id>")
    def inspect(item_id):
        item = port.item(item_id)
        if item is None:
            # Only consult the same refusal metadata already exposed by this view.
            # Never bypass suppression/readability through a source-table fallback.
            lookup = getattr(port, 'refusal', None)
            refusal = lookup(item_id) if callable(lookup) else None
            return unavailable(item_id, refusal is not None)
        filter_key = canonical_filter(request.args.get("filter", "queue"))
        if filter_key not in NAV_FILTERS:
            filter_key = "queue"
        sort = request.args.get("sort")
        if sort not in (None, "title", "kind", "freshness", "tier"):
            sort = None
        direction = request.args.get("dir") if sort else None
        return render_template("attention/inspect.html", shown=item.inspect(),
                               display_zone=display_zone, room="attention",
                               filter_key=filter_key, sort=sort, direction=direction,
                               return_context=context(request.args),
                               report_url=report_url(item.item_id, request.args)
                               if item.readable and item.kind == 'review' else None,
                               # P3-02: every readable work item, whatever its kind, links to its
                               # own record; a restricted row links nowhere its content lives.
                               task_url=task_url(item.item_id, request.args) if item.readable else None)

    return bp
