"""Every screen's data, read from the store and never invented.

One rule governs this file: **a number on this surface is a number this module read, or it is
not on this surface.** Where the design guide asks for a figure the runtime does not measure,
the screen says so in the place the figure would have been. That is not a shortfall being
excused; it is the only behaviour consistent with a console whose pitch is double-entry
accountability. An illustrative number on a real screen is indistinguishable from a measured
one, and the operator has no way to tell which he is looking at.

Figures the design guide asks for that the store did not hold when this lane started, each
rendered as a stated absence rather than as a plausible integer -- and each revisited when a
migration made the absence false, because a stated absence is a claim about the store and it goes
stale exactly like a number does:

  * **the leverage ratio.** Agent hours are real (`run.started_at`..`ended_at`). Operator minutes
    were measured nowhere in migration 1, so the ratio had one real half and one imaginary one and
    was not rendered as a ratio. **MIGRATION 23 (`brain.time_entry`, task 0279) MADE THAT FALSE**
    and task 0290 is where this file stopped printing it: `brief()` now reads the operator's
    measured minutes over its OWN window and renders the ratio WITH ITS COVERAGE, never bare. The
    argument for the coverage is in `brief()` and it is the whole reason that change is not a
    one-line deletion -- a total over a subset is not a ratio unless it says how big the subset is.
  * **the operator's decaying bump** and **typed defer**: these had no home when this lane
    started and the controls rendered disabled with that reason on them. **D6b landed migration
    0007 at 16:04Z and both are live now**, through `queue bump` and `queue defer`. The disabled
    rendering is kept as the fallback for a store without the queue tables, because a control that
    silently does nothing is worse than one that says why.

**THE QUEUE IS RANKED AND TIERED IN ONE PLACE AND IT IS NOT THIS FILE.** `human_queue.reads.queue`
is that place. This module had its own tier assignment (a constant per row kind) and its own
`rank()` while D6b was unlanded, and both are gone: task 0127. Two classifiers do not disagree
loudly, they disagree quietly, and the measured cost was not a mis-sorted list. `queue demote`
records a calibration miss against the producer whose estimate was wrong, and a console that
showed a tier the ranking module had never computed was feeding that dataset a tier the operator
never saw. The rule this file keeps is the CARD: which verb a kind offers, what its button says,
what the receipt reads. The rule it no longer has is which lane the card is in or what order it
comes in.

Reads go through `store.read()`, which Postgres has put in READ ONLY mode, through `store.reads`,
which is D1's module, and through `human_queue.reads`, which is D6b's. Nothing here writes; writes
are `web/rooms.py:dispatch`.
"""

from __future__ import annotations

import datetime as _dt
import os

import store
from human_queue import options as QO
from human_queue import reads as Q
from human_queue import tiers as HQT
from store import reads as R
from store import schema

from . import claims as C
from . import images
from . import rooms

OPERATOR = os.environ.get("CONSOLE_OPERATOR", "operator")

# The tier NAMES and the act each one describes come from D6b: `tiers.TIERS` is the order and
# `tiers.TIER_ACT` is the sentence. The display name and the strapline are this lane's, because
# they are copy on a card rather than a fact about the queue.
TIER_NAME = {"decide": "Decide", "judge": "Judge", "shape": "Shape"}
TIER_STRAP = {"decide": "act from prepared context", "judge": "open one thing",
              "shape": "generate: scope, rethink"}
TIERS = tuple({"k": k, "n": TIER_NAME[k], "m": TIER_STRAP[k], "act": HQT.TIER_ACT[k]}
              for k in HQT.TIERS)


def _mins(a, b) -> float:
    if not a or not b:
        return 0.0
    return max(0.0, (b - a).total_seconds() / 60.0)


# --------------------------------------------------------------------------- the queue

# The kind is what decides which verb a card offers, so it is read off the ONE column of
# `brain.queue_open` that names the arm a row came from. `primary_verb` and not `item_class`:
# `item_class` is overridable per item by `brain.queue_item`, and a console that derived the
# button from an overridable column would offer `Accept work` on the operator's own task the
# first time a producer set `item_class` on it.
KIND_BY_ARM = {
    ("work_item", "Accept work"): "review",
    ("work_item", "Mark my task done"): "human",
    ("question", None): "question",
    ("recommendation", None): "recommendation",
    # Arm 5, the intake item awaiting human triage. BOTH KEYS ARE HERE ON PURPOSE and the second
    # one is a departure from the packet definition, stated rather than slipped in: the lookup
    # below falls back to `KIND_BY_ARM.get((source_type, None), "review")`, so an objective row
    # that ever arrived carrying a different verb label would render as a REVIEW card -- the
    # unknown-source fallback term-5's coupling map names as the thing that must not reinterpret
    # an objective as work. The `(objective, None)` entry makes an objective stay an objective
    # whatever the label says, which costs one line and closes that fallback.
    ("objective", "Accept objective"): "objective",
    ("objective", None): "objective",
}


def queue_view(window: int = Q.DEFAULT_WINDOW, include_deferred: bool = False) -> dict:
    """D6b's queue, rendered. One ranking, one tier classifier, and neither of them is here.

    Returns the console's shape: the flat ranked list, the same items grouped by tier, and the
    counts D6b reports beside them. `totals` is not decoration -- the queue is a WINDOW, so the
    number of cards on a tier is not the number of items in it, and rendering the card count as
    the tier count would be a backlog silently reported as a plan.
    """
    try:
        q = Q.queue(window=window, include_deferred=include_deferred, exclude_objectives=True)
    except Exception as exc:                                            # noqa: BLE001
        # THE QUEUE ROOM HAS NO FALLBACK ORDER AND MUST NOT GROW ONE. A console that quietly
        # sorted the rows itself when D6b's schema was absent would be the second classifier this
        # lane just deleted, and it would be one that appears exactly when nobody is watching.
        # Measured: without the queue schema this is `relation "brain.queue_open" does not exist`,
        # HTTP 500, and the sentence below is what the log carries in front of it.
        raise RuntimeError(
            "the human queue cannot be read: brain.queue_open and the tables under it are D6b's "
            "migration 0007, which lives at queue/schema/0007_queue.sql and is NOT applied by "
            "engine/bin/scratch-db.sh. Apply 0007, 0008 and 0009 to this store. This room does "
            f"not fall back to an order of its own. Underlying error: {exc}") from exc
    counters = _counterarguments()
    tiers, items = {}, []
    for k in HQT.TIERS:
        block = q["tiers"][k]
        tiers[k] = [_card(it, k, block["total"], q["now"], counters) for it in block["items"]]
        items.extend(tiers[k])
    _stamp_done_refusals(items)
    _stamp_review_facts(items)
    _stamp_project(items)
    return {
        "items": items, "tiers": tiers, "now": q["now"], "window": q["window"],
        "totals": q["totals"],
        "hidden": {k: q["tiers"][k]["hidden"] for k in HQT.TIERS},
        # Nothing is dropped silently: what the window did not reach, what is waiting on a
        # blocker, and what carries a live wake condition are counted and named rather than
        # subtracted. `cycles` is a data error in `depends_on` and is reported for the same reason.
        "blocked": q["blocked"], "deferred_items": q["deferred_items"], "cycles": q["cycles"],
        # THE WEIGHTS THAT ORDERED THIS LIST, carried out with it so the sentence above the
        # column header describes THESE rows and not a set of defaults that may not be in force.
        # Same construction as the intake badge seeding its own count: two reads of one number
        # that can disagree is the defect, and here the two would disagree exactly when the
        # operator had tuned something, which is the moment he is most likely to look.
        "weights": q.get("weights"),
    }


# --------------------------------------------------------------- the five queues, and the table
#
# HIS ASK 5 SHIPPED TO A CLI AND THE CONSOLE NEVER GREW A SURFACE FOR IT. Measured 2026-09-01:
# `grep -rn operator_queue web/` returned **0**, while `brain.operator_queue` had been serving five
# queues since ledger 52. So the queues existed, the reader existed, and the only room he actually
# opens could not show him any of them.
#
# NOTHING BELOW IS A SECOND IMPLEMENTATION OF ANYTHING, and that is the whole design constraint.
# `human_queue.reads.queues()` is the one reader of the five and it stays the one reader; the
# ranking stays `human_queue.rank`'s; the tiering stays `human_queue.tiers`'. This module joins
# two reads that already exist and sorts a list for display. It writes nothing and it ranks
# nothing.

#: His five, in the order he named them, taken from the reader rather than retyped here. A second
#: copy of this tuple is a second answer to "which queues are there", and the one that is wrong is
#: the one nobody edited.
QUEUES = Q.QUEUES

#: The columns the full table offers, and the ONLY keys `table_view` will sort on. An allowlist
#: rather than a passthrough because `sort` arrives from the URL: a sort key read straight off
#: `request.args` into a dict lookup is a way to ask this process about keys the operator was
#: never offered, and a way for a typo to render an unsorted page that LOOKS sorted.
#:
#: The value is the key actually sorted ON, which is not always the key rendered. `impact` prints
#: what he typed and sorts on `impact_magnitude`, because sorting the raw text puts `critical`
#: under `low` alphabetically and `9000` above `10000` lexically. That divergence is the reason
#: this is a table and not a dict comprehension over the card.
SORTABLE = {
    "id": "id", "title": "title", "state": "state_word", "lane": "lane",
    "impact": "impact_magnitude", "unblocks": "unblocks", "age": "age_seconds",
    "prio": "prio", "queue": "queue",
}


def operator_queues(limit: int = 500) -> dict:
    """His five queues, straight from `human_queue.reads.queues()`. No second reader.

    Passed through rather than reimplemented, INCLUDING its `available` flag. That flag is the
    difference between "this queue is empty" and "this store cannot tell you", and a console that
    flattened the second into the first would paint `nothing needs deciding` over a column that
    does not exist. `reads.queues` already refuses to do that; this function's only job is not to
    undo it.
    """
    return Q.queues(limit=limit)


def _raw_impacts(ids: list[str]) -> dict:
    """`brain.work_item.impact` AS HE TYPED IT, because `brain.queue_open` folds it to a band.

    THE DEFECT THIS EXISTS TO FIX, measured 2026-09-01 on a seeded scratch store. The money column
    ordered the board correctly and PRINTED `high`, `medium`, `low` in every cell. Cause, and it
    is one line of `migrations/0048_impact_is_continuous.sql:272`:

        brain.signal_level('impact', COALESCE(w.impact, w.stakes))  AS impact,
        brain.impact_magnitude(COALESCE(w.impact, w.stakes))        AS impact_magnitude

    So the view publishes the BAND and the MAGNITUDE and never the amount. That is right for every
    existing surface -- migration 48 says so in as many words, every surface that prints a signal
    prints a band -- and it is exactly wrong for a column whose whole purpose is to show him the
    money. `150000` and `10000` and `9000` all arrive as `high`, `high`, `medium`.

    WHY THIS IS A SECOND READ AND NOT A MIGRATION. Publishing the raw amount on `brain.queue_open`
    would be the tidier fix and it is NOT taken here: that view is read by the ranking, the stack,
    the console and the CLI, and widening it during a four-commander wave is the shared-path
    collision the sprint map is built to avoid. S3's ledger number 55 is therefore ANNOUNCED AND
    UNUSED, and the migration is written up as an option for the operator rather than taken.

    IT IS ONE QUERY FOR THE WHOLE TABLE, keyed by id, never one per row.
    """
    if not ids:
        return {}
    with store.read() as s:
        rows = s.query(
            "SELECT id, impact FROM brain.work_item WHERE id = ANY(%s) AND impact IS NOT NULL",
            (list(ids),))
    return {str(r["id"]): r["impact"] for r in rows}


def _is_amount(raw) -> bool:
    """Whether this impact is an AMOUNT rather than a BAND. `signal_ok` accepts either."""
    try:
        float(str(raw).strip())
        return True
    except (TypeError, ValueError):
        return False


def _impact_text(raw) -> str | None:
    """What the impact CELL prints: his money, formatted as money; his band, as the band.

    `brain.signal_ok` accepts either, and migration 48's whole argument is that money is
    continuous and a band is not, so a cell that folded `10000` down to `high` would throw away
    the precision he chose to type. It also refuses a bare number in (0, 100) as ambiguous
    between three dollars and three out of five, which is why anything numeric that reaches here
    is either 0 or at least 100 and can be rendered as an amount without guessing.

    THE CURRENCY SYMBOL IS DELIBERATELY NOT HERE. The store holds an amount and never a currency,
    so printing `$` would be this file asserting a fact the column does not carry. It renders a
    grouped number and lets the band words stay words.
    """
    if raw is None:
        return None
    t = str(raw).strip()
    if not t:
        return None
    try:
        n = float(t)
    except ValueError:
        return t
    return f"{int(n):,}" if n == int(n) else f"{n:,.2f}"


def _age_seconds(since, now) -> float | None:
    """Seconds between `since` and `now`, or None when the row carries no timestamp.

    NONE IS NOT ZERO AND IS NOT SORTED AS ZERO. A row with no `since` is a row whose age is
    unknown; ordering it as the newest thing on the board would be this file inventing a fact,
    which is the one rule `web/model.py` opens with.
    """
    if since is None:
        return None
    try:
        return (now - since).total_seconds()
    except Exception:                                                   # noqa: BLE001
        return None


def table_view(queue: str | None = None, sort: str | None = None, direction: str = "desc",
               limit: int = 500) -> dict:
    """The full board as one sortable table: every open row, every column, his five queues.

    THE DOOR BEHIND THE RANKED WINDOW, and it is a DIFFERENT QUESTION rather than a bigger answer
    to the same one. `/queue` asks *what should I do next*, which is a plan and is deliberately
    seven rows. This asks *what is on the board*, which is an inventory. His 2026-08-31 ruling was
    that both ship, and the reason they never conflicted is that the window protects the
    starvation guarantee only for the surface that claims to be a plan.

    NOTHING HERE IS WRITTEN AND NOTHING HERE IS RANKED. `sort` reorders rows for display through
    `SORTABLE` above; no verb is dispatched, `priority` is never touched, and the computed score
    stays the machine's. `MUST-NOT-BUILD` item 2's two prohibitions -- score editing and
    drag-to-reorder -- are honoured by construction here and the ranked window's own header is not
    touched at all. The open question about item 2's third clause is
    `outputs/2026-09-01-sprints/S3/01-ITEM-2-AND-THE-SORTABLE-TABLE.md` and is the operator's.

    TWO SOURCES, AND THE MEMBERSHIP ONE IS AUTHORITATIVE. `operator_queue` decides which rows are
    in which queue; `queue_view` supplies the columns. They do NOT cover the same rows and the
    difference is the whole reason this is a join rather than a filter:

      * `review` is `done AND accepted_at IS NULL`. `brain.queue_open` publishes OPEN work, so a
        done-and-unaccepted row has NO ranked card. Filtering the ranked list by queue membership
        would have rendered `review` as empty while rows sat in it -- the empty-set trap wearing a
        new coat, on the exact surface whose brief warns about it three times.
      * A row may be in TWO queues and that is correct: it is one row seen from two questions. So
        `queue` on a row here is a LIST, the counts do not sum to the board, and `across` is named
        `across` rather than `total` so nobody reads it as a board count.

    THE DENOMINATORS ARE RETURNED, NOT COMPUTED IN THE TEMPLATE. `n` is what the queue holds and
    `enriched` is how many of those the ranked read could describe. A table that showed 12 rows
    with 5 populated and said nothing would be a page reporting a coverage gap as data.
    """
    qs = operator_queues(limit=limit)
    now = _dt.datetime.now(_dt.timezone.utc)
    # The ranked read, wide open, purely as a COLUMN SOURCE. `window=10_000` is the same widening
    # `_queue_ctx` uses for `?window=all` and is not a second constant: this is an inventory and a
    # seven-row window over an inventory is the defect, not the feature.
    try:
        ranked = {(c["source_type"], str(c["id"])): c for c in queue_view(window=10_000)["items"]}
    except Exception:                                                   # noqa: BLE001
        # A store that cannot serve the ranked read can still serve the five queues: four of the
        # five have been readable since migration 1. Degrading to titles is worth more than a 500,
        # and `enriched` below reports the degradation rather than hiding it.
        ranked = {}
    rows, seen = [], {}
    for qname in QUEUES:
        blk = qs["queues"].get(qname) or {}
        for r in (blk.get("items") or []):
            key = (r["source_type"], str(r["source_id"]))
            if key in seen:
                # ONE ROW, TWO QUEUES. It is not duplicated into two table rows: it is one row
                # whose `queue` cell names both, which is what the view's own comment says the
                # right answer is. Duplicating would make the table's own length a lie.
                seen[key]["queue"].append(qname)
                continue
            card = ranked.get(key) or {}
            row = {
                "id": str(r["source_id"]), "source_type": r["source_type"],
                "title": r["title"] or card.get("title") or "",
                "queue": [qname],
                "since": r.get("since"),
                "age_seconds": _age_seconds(r.get("since"), now),
                # EVERY COLUMN BELOW IS `None` WHEN THE RANKED READ DID NOT COVER THIS ROW, and
                # the template prints an em-space-free explicit mark for it rather than a blank.
                # A blank cell in a spreadsheet reads as a zero, and this lane has already shipped
                # that defect once with the `unblocks` column reading 0 on every row.
                "lane": card.get("lane") or None,
                "state_word": card.get("tier") or None,
                "impact": card.get("impact"),
                "impact_text": _impact_text(card.get("impact")),
                "impact_magnitude": card.get("impact_magnitude"),
                "unblocks": card.get("unblocks"),
                "prio": card.get("prio"),
                "href": card.get("href") or "",
                "enriched": bool(card),
            }
            seen[key] = row
            rows.append(row)
    # THE AMOUNT HE TYPED, STAMPED OVER THE BAND THE VIEW HANDED BACK. One read for the board.
    # A row keeps its band when it has no amount of its own, and `impact_is_money` is what the
    # cell uses to tell the two apart on screen: `150000` and `high` are not the same KIND of
    # answer and a column that rendered them identically would be inviting him to compare them.
    raw = _raw_impacts([r["id"] for r in rows if r["source_type"] == "work_item"])
    for r in rows:
        amount = raw.get(r["id"])
        if amount is not None:
            r["impact"] = amount
            r["impact_text"] = _impact_text(amount)
        r["impact_is_money"] = amount is not None and _is_amount(amount)
    counts = {q: {"n": (qs["queues"].get(q) or {}).get("n"),
                  "available": (qs["queues"].get(q) or {}).get("available", True),
                  "why": (qs["queues"].get(q) or {}).get("why")} for q in QUEUES}
    shown = [r for r in rows if queue is None or queue in r["queue"]]
    key = SORTABLE.get(sort or "")
    if key:
        desc = str(direction).lower() != "asc"
        # NONE SORTS LAST IN BOTH DIRECTIONS, which is the one ordering rule that is not a
        # preference. A missing impact is not the smallest impact, and floating it to the top of
        # an ascending sort would put every uncovered row above every real one and read as a
        # finding. The primary key is "is this cell known", so unknown always sinks.
        def sk(r):
            v = r.get(key)
            return (v is None, _sort_value(v, desc))
        shown = sorted(shown, key=sk, reverse=False)
        if desc:
            known = [r for r in shown if r.get(key) is not None]
            unknown = [r for r in shown if r.get(key) is None]
            shown = list(reversed(known)) + unknown
    return {
        "rows": shown, "counts": counts, "across": qs.get("across"),
        "decisions_available": qs.get("decisions_available"),
        "queue": queue, "sort": (sort if key else None), "dir": ("asc" if str(direction).lower()
                                                                 == "asc" else "desc"),
        # THE TWO DENOMINATORS, both of them, always. `total` is how many distinct rows the five
        # queues hold; `enriched` is how many of those the ranked read could describe. They are
        # equal on a healthy store and their gap is a real coverage statement, never a blank cell.
        "total": len(rows), "shown": len(shown),
        "enriched": sum(1 for r in shown if r["enriched"]),
        "sortable": sorted(SORTABLE),
    }


def _sort_value(v, desc: bool):
    """A comparable for a mixed column, because a table sorts what the store had, not what it wished.

    `sorted` raises `TypeError` on `str` against `int` and a 500 on a column that happens to hold
    both is a page the operator cannot open. Numbers keep their order among numbers, and anything
    else is compared as a casefolded string, so a mixed column degrades to a stable, explicable
    order instead of to an exception.
    """
    if isinstance(v, bool):
        return (0, float(v))
    if isinstance(v, (int, float)):
        return (0, float(v))
    return (1, str(v).casefold())


def _stamp_done_refusals(cards: list[dict]) -> None:
    """`done_refused` on every `human` card the write door will refuse. Row `0399`.

    THE CARD DOES NOT DECIDE THIS AND MUST NOT. It asks `web/rooms.py::agent_work_on`, which is
    the guard's own predicate and the only implementation of it, for exactly the reason task 0427
    wrote into `_VERBS['human']` three lines below: a card that answers a question about a row on
    its own is a card asserting a fact the row was never asked for, and the last time this lane
    did that the guard ended up reading the card's answer back.

    WHY THE CARD IS TOUCHED AT ALL, when the refusal already lands server side. `Send back` runs
    `reopen`, which returns the row to `inbox` with `claimed_by` cleared, so `brain.queue_open`'s
    second arm hands an agent's work item straight back on the OPERATOR'S arm and the card is
    re-rendered under the same id with `Mark my task done` where `Accept work` was. That is row
    `0391`'s residual measured from the other end: the card is not lingering, the ROW is still
    open and is genuinely still in the queue, and `Nothing leaves the queue silently` says it must
    stay. What must not stay is the offer. `MUST-NOT-BUILD` item 2's own note is the rule this
    follows -- an affordance that cannot work is worse than one that is absent -- and the control
    therefore renders disabled with the reason on it, exactly as the pre-D6b bump control did.

    ONE QUERY FOR THE BOARD, not one per card, and only for the cards that can carry the verb.
    """
    ids = [c["task"] or c["id"] for c in cards if c.get("kind") == "human"]
    if not ids:
        return
    try:
        worked = rooms.agent_work_on(ids, acting_as=store.human_slug(OPERATOR))
    except Exception:                                                   # noqa: BLE001
        # A store below this read is a store the guard will refuse on anyway; a queue that will
        # not render is a worse failure than a button that is refused when pressed.
        return
    for c in cards:
        if c.get("kind") != "human":
            continue
        ev = worked.get(c["task"] or c["id"])
        if ev and ev["worked"]:
            c["done_refused"] = (
                f"an agent has worked this row ({ev['why']}), so marking it done here would file "
                f"that agent's report in your words. It was sent back and is waiting to be worked "
                f"again. The verb for finished agent work is Accept work.")


def _stamp_review_facts(cards: list[dict]) -> None:
    """Two facts a Judge card needs BEFORE its two verbs, and neither is in `brain.queue_open`.

    Rows `0416` and `0414`, and they are one query because they are one screen.

    `caveat`  what the agent's own report says against itself. Arm 1 of the view publishes
              `title` and not `result`, so the sentence the operator most needs was reachable
              only by expanding the card or opening `/task/<id>`. `web/claims.py::caveats`
              selects whole claim lines verbatim and invents nothing; the count of what it
              scanned rides along so the card can say it looked and found none, which is a
              different statement from a card that never looked.

    `send_back_to`  where `Send back` will actually put this row. `reopen` clears `claimed_by`
              and leaves `agent_claimable` EXACTLY as it was, so on a fleet-claimable row the
              item goes back to the FLEET and leaves the human queue entirely, and on a row the
              fleet may not claim it comes back on arm 2 as the operator's own. Measured either
              side of the operator's click on 2026-08-28: before, `agent_claimable` true and
              `brain.queue_open` returned 1 row for it; after, `agent_claimable` still true and
              the view returned 0. He read the disappearance as success.

    ONE QUERY FOR THE BOARD, the shape `_stamp_done_refusals` above already sets, and the fleet
    flag is one scalar for the whole render rather than one per card. `agent_claimable` is read
    from `brain.work_item` and never from the card, for the reason task 0427 wrote into
    `_VERBS['human']`: a card that answers a question about a row on its own is a card asserting
    a fact the row was never asked for.
    """
    ids = [c["task"] or c["id"] for c in cards if c.get("kind") == "review"]
    if not ids:
        return
    try:
        with store.read() as s:
            rows = s.query("SELECT id, agent_claimable, coalesce(result, '') AS result "
                           "FROM brain.work_item WHERE id = ANY(%s)", (ids,))
        paused = R.fleet_paused()
    except Exception:                                                   # noqa: BLE001
        # A store this read cannot reach is one the whole board is about to fail on anyway. A
        # card rendered without these two facts is the card that shipped yesterday; a queue that
        # will not render at all is worse.
        return
    by_id = {str(r["id"]): r for r in rows}
    for c in cards:
        if c.get("kind") != "review":
            continue
        row = by_id.get(c["task"] or c["id"])
        if row is None:
            continue
        c["caveat"] = C.caveats(row["result"])
        c["send_back_to"] = "fleet" if row["agent_claimable"] else "you"
        c["fleet_paused"] = bool(paused)


# --------------------------------------------------------------------------- row 0434: filtering
#
# HIS ASK 4, IN HIS WORDS: *"default order by urgency x importance, with Notion-style filtering by
# project, client, task, priority and custom fields."* This block is the half of it that is a READ.
# Nothing here writes, nothing here sorts, and nothing here is a second ranking: `human_queue.
# ranking.rank` is the one order and this file deleted its own copy of it at task 0127.
#
# WHY FILTERING IS IN SCOPE AND SORTING IS NOT. `MUST-NOT-BUILD` item 2 forbids score editing and
# drag-to-reorder and it is KEPT: no control writes `priority`, `set` is not on any room's
# allowlist, and row 0431 made the column header undeliverable to a click on purpose. A filter
# changes WHICH rows are on the screen and never the order they arrive in, so the ranked order is
# still the machine's and is still the thing the operator is looking at.
#
# THREE OF HIS FIVE FIELDS DO NOT EXIST IN THIS STORE AND THE ANSWER IS TO SAY SO RATHER THAN TO
# INVENT THEM. Measured 2026-08-29 against `migrations/` and `queue/schema/`:
#
#   project   REAL from migration 44. `brain.work_item.project` is a text column referencing
#             `brain.project(slug)`. It is NOT published by `brain.queue_open`, so it is read
#             here, and on a store below 44 the column does not exist at all -- which is live
#             `brain` today, at ledger 42.
#   priority  REAL and already published by `brain.queue_open` as `w.priority`, ascending-urgent
#             (0 is P0). `rank.band_value` reads the same column, so a filter on it filters on
#             the thing the ranking already used.
#   task      REAL: `work_item_id`, which is what `blocks <id>` links to today.
#   client    ABSENT. There is no client column, table or vocabulary anywhere in `brain`. The
#             word appears in this repo only in prose. A dropdown labelled "client" that filtered
#             on the lane would be a field wearing another field's name.
#   custom    ABSENT. There is no user-defined field mechanism. `brain.work_item_signals` is a
#             fixed set of seven declared signals and is not extensible by the operator.
#
# So the bar offers what is real, and the console says in one sentence which of his five are not.


def _projects() -> dict:
    """`work_item_id -> project slug`, or an empty map on a store that cannot hold one.

    ASKED OF THE CATALOGUE RATHER THAN CAUGHT AS AN EXCEPTION, which is `web/board.py::present`'s
    instrument and this is deliberately the same one: a store below migration 44 must report
    "this store cannot hold a project" and not "you have no projects", and it must not report it
    by letting an `UndefinedColumn` escape into a 500 on the room the operator opens first.
    """
    with store.read() as s:
        if not s.scalar("SELECT to_regclass('brain.work_item') IS NOT NULL"):
            return {}
        has = s.scalar("SELECT count(*) FROM information_schema.columns "
                       " WHERE table_schema = 'brain' AND table_name = 'work_item' "
                       "   AND column_name = 'project'")
        if not has:
            return {}
        rows = s.query("SELECT id, project FROM brain.work_item WHERE project IS NOT NULL")
    return {str(r["id"]): r["project"] for r in rows}


def _stamp_project(cards: list[dict]) -> None:
    """`project` on every card, and the empty string where the row carries none.

    ONE READ FOR THE WHOLE PAGE, never one per card: this runs on the three-second poll path with
    everything else `queue_view` does, and a per-card lookup would be twenty round trips a minute
    to fill a column that is NULL on essentially every row (migration 44's own comment).
    """
    by_task = _projects()
    if not by_task:
        for c in cards:
            c["project"] = ""
        return
    for c in cards:
        c["project"] = by_task.get(c.get("task") or "") or ""


def queue_filter_facts() -> dict:
    """What this store can be filtered BY, and what it cannot, said in the store's own terms.

    A CONSOLE THAT OFFERED A PROJECT FILTER OVER A STORE WITH NO PROJECT COLUMN would be item 2's
    own disabled-affordance failure arriving through a door nobody was watching, and live `brain`
    is exactly that store: ledger 42 on 2026-08-29, so migrations 44, 46 and 47 are committed and
    unapplied and `brain.project` does not exist on the board he actually opens.

    Three states and they are three different sentences, because collapsing them would report a
    schema fact as an operator fact:

        column absent    the store cannot hold a project. Migration 44 is not applied.
        column present, nothing filed    the store can, and nothing has one yet.
        column present, N filed          the filter is live over N rows.
    """
    facts = {"project_column": False, "project_table": False, "projects_used": 0,
             "projects": [], "ledger": None}
    with store.read() as s:
        facts["ledger"] = s.scalar("SELECT max(version) FROM brain.schema_migration")
        facts["project_table"] = bool(
            s.scalar("SELECT to_regclass('brain.project') IS NOT NULL"))
        facts["project_column"] = bool(s.scalar(
            "SELECT count(*) FROM information_schema.columns "
            " WHERE table_schema = 'brain' AND table_name = 'work_item' "
            "   AND column_name = 'project'"))
        if facts["project_column"]:
            rows = s.query("SELECT project, count(*) AS n FROM brain.work_item "
                           " WHERE project IS NOT NULL GROUP BY project ORDER BY project")
            facts["projects"] = [{"slug": r["project"], "n": int(r["n"])} for r in rows]
            facts["projects_used"] = sum(p["n"] for p in facts["projects"])
    if not facts["project_column"]:
        facts["project_note"] = (
            f"this store has no brain.work_item.project column, so nothing here has a project to "
            f"filter by. It arrives with migration 44 and this store is at ledger "
            f"{facts['ledger']}.")
    elif not facts["projects_used"]:
        facts["project_note"] = ("the column is here and no row carries a project yet. "
                                 "`swarm project add` then `swarm project attach` fills it.")
    else:
        facts["project_note"] = (f"{facts['projects_used']} row(s) across "
                                 f"{len(facts['projects'])} project(s).")
    # THE TWO FIELDS HE NAMED THAT DO NOT EXIST. Said once, on the surface, rather than left for
    # him to discover by looking for a dropdown that is not there.
    facts["absent"] = [
        {"field": "client",
         "why": "there is no client column, table or vocabulary in brain. Lane is the nearest "
                "real field and it is offered under its own name."},
        {"field": "custom fields",
         "why": "brain.work_item_signals is a fixed set of seven declared signals and nothing "
                "lets the operator add an eighth."},
    ]
    return facts


def order_terms(weights: dict | None = None) -> dict:
    """What the queue is ordered by, read out of the ranking module rather than restated.

    THE SENTENCE IS BUILT FROM THE RANKING'S OWN WEIGHTS AND NOT TYPED HERE, because a console
    that describes an ordering in prose is a second statement of it and two statements drift. If
    a weight is added, removed or renamed in `human_queue.rank`, this line changes with it.

    AND SINCE ROW 0434 IT IS FINALLY WHAT HE ASKED FOR. He asked for urgency x importance. Until
    2026-08-31 the queue computed an ADDITIVE sum in which urgency was one of six peers, there was
    no signal named importance, and `stakes` was the nearest thing to one, so this function said
    so plainly rather than using his words over arithmetic that did not match them. The maths has
    now changed underneath it: `rank.score` computes `urgency x impact` as a spine with the rest
    added on as modifiers, so the sentence says `urgency x impact` because that is now true. The
    check that used to assert this strip claims NO product is inverted in
    `test_the_order_and_the_filters.py`, deliberately and in the same change, because a check that
    was protecting against a false claim has to be re-pointed the moment the claim becomes true or
    it is just a check nobody may fix.

    THE WEIGHTS PASSED IN ARE THE ONES THAT ORDERED THE LIST, and passing them is how this
    sentence stops being able to disagree with the rows beneath it. Until this change the function
    read `DEFAULT_WEIGHTS` unconditionally, so an operator who had set `signals.w_charter_alignment
    2` was shown a sentence describing weights his own store no longer used. It falls back to the
    defaults when nobody passes any, which costs no database round trip and is the right answer on
    every page that is not the queue.

    THE WORDS ARE SHORT ON PURPOSE AND THE LENGTH WAS MEASURED. The first version of this
    sentence spelled every term out ("charter alignment", "what it unblocks") and wrapped to three
    lines, which put the strip at 79px and row one at y=432 on a page row 0431 had just spent a
    lane getting to y=329. One line is 20px. A sentence nobody can afford to keep on the screen is
    a sentence that gets deleted, so it is one line and the long form is one hover away in `title`.
    """
    from human_queue import rank as _rank
    w = _rank.resolve_weights(weights) if weights else dict(_rank.DEFAULT_WEIGHTS)
    label = {"w_core": "urgency x impact", "w_charter_alignment": "charter",
             "w_unblock": "unblocks", "w_age_per_day": "age"}
    long = {"urgency x impact": "urgency multiplied by impact, which is a band or an amount of money",
            "charter": "charter alignment", "unblocks": "what it unblocks",
            "age": "how long it has been waiting"}
    terms = [label[k] for k in _rank.DEFAULT_WEIGHTS if k in label]
    spine, mods = terms[0], terms[1:]
    return {
        "terms": terms,
        "weights": w,
        # `band` first, then score, then id: `rank.rank`'s own sort key, in the order it applies
        # it. The band exists for exactly one condition and it is named rather than numbered.
        "band": (f"an item with an agent parked on it for {_rank.LIVE_IDLE_MINUTES}+ minutes "
                 f"sorts above everything, because that one costs the fleet by the minute"),
        # THE SPINE READS AS A PRODUCT AND THE MODIFIERS READ AS ADDITIONS, because that is what
        # the arithmetic does. "plus" rather than "+" between them so the one "x" on the line is
        # unmistakably the multiplication and not part of a chain of symbols.
        "sentence": f"ordered by {spine}, plus " + ", ".join(mods) + " and your bumps",
        "long": ("highest first. The spine is " + long[spine]
                 + ". Added to it are " + ", ".join(long[t] for t in mods)
                 + ", plus the operator's decaying bumps. The spine is a PRODUCT, so neither "
                   "factor can be zero: urgency runs 1 to 3 and impact runs 0.25 to 4. Nothing "
                   "on this page can edit it."),
        "ties": "rows on the same score are ordered by id",
    }


def queue_items() -> list[dict]:
    """The rendered queue as one ranked list. The order is D6b's, tier by tier."""
    return queue_view()["items"]


def find_item(item_id: str) -> dict | None:
    """The item behind a write, looked up over the WHOLE queue and not over the window.

    The write door checks that the id it was handed is still a live queue item, and "still live"
    is not the same question as "on the screen". A deferred item is a real row with a real card at
    `/question/<id>`, and refusing an answer to it with *it is not in the queue any more* would be
    a lie told by a rendering limit. So this reads every open item, including what the window cut,
    what a blocker is holding, and what carries a wake condition.
    """
    v = queue_view(window=10_000, include_deferred=True)
    for i in v["items"]:
        if i["id"] == item_id:
            return i
    counters = _counterarguments()
    for raw in v["blocked"]:
        if str(raw["source_id"]) == item_id:
            card = _card(raw, raw["tier"], 0, v["now"], counters)
            _stamp_done_refusals([card])          # the blocked arm builds its own card, so it
            _stamp_review_facts([card])           # needs the same stamps the board got
            return card
    return None


def _options_fields(it: dict) -> dict:
    """HOOK card-options-fields, SHELL-0 (task 0156) -- V3 (multi-option card) alone edits this.

    The parsed option set of C section 1.2, merged onto the card dict. `_card` is not edited
    (0151's collision 2): everything V3 adds is under this function and the section below it.

    THE SET IS READ ONCE FOR THE WHOLE BOARD, not once per card. `store.read()` opens a fresh
    Postgres connection every call -- measured at 13.0ms on this host, five connections in
    64.8ms -- and a console that polls every three seconds over twenty cards would spend a
    quarter of a second per poll on connection setup for a component that mostly has nothing to
    show. `_option_sets()` below is the single-flight that makes one render one query, and its
    staleness bound is stated where it is defined.
    """
    key = (it["source_type"], str(it["source_id"]))
    memo = _option_sets()
    opts = memo["sets"].get(key)
    if opts:
        return option_card_fields(opts)
    # NO OPTIONS. Two different facts, and the card must not render them the same way. A failed
    # attempt is C section 1.6b: an amber finding, word first, with the legacy singular card
    # beneath it. NOBODY HAVING TRIED IS NOT A FINDING AT ALL -- it is every card in the console
    # today, and an amber line on all of them would spend the one colour that means "look at
    # this" on the ordinary case (must-not-build 7, and section 2.1's law that one thing competes
    # for the eye). So the untried card renders exactly as it does today: nothing added.
    att = memo["errors"].get(key)
    if att and att.get("options_error"):
        return {"options": [], "options_n": 0,
                "options_failed": att["options_error"], "options_tried_at": att["options_drafted_at"]}
    return {}


def _media_fields(it: dict) -> dict:
    """HOOK card-media-fields, SHELL-0 (task 0156) -- V5 (images in place) alone edits this.

    Filled by task 0167. Returns `{"img": ...}` for a subject that carries a renderable image and
    `{}` for one that does not, because empty means empty: an item that never had an image emits
    no markup at all rather than an empty block (the q0146 rule SHELL-0 cites).

    THE VERDICT IN `img["state"]` IS MEASURED HERE, NOT READ OFF THE ROW. `web/images.py::verify`
    re-stats and re-hashes the file at render time, so `missing` and `changed` are facts about
    now rather than about the moment the pointer was written. That is the whole lane: v1 found a
    path recorded as present that was not on disk, and a console rendering the stored state would
    have drawn that as a healthy image.

    The subject vocabulary needs no mapping: `brain.queue_open.source_type` is already
    `work_item` / `question` / `recommendation`, which are three of the five values migration
    29's `subject_type` CHECK admits.
    """
    img = images.for_card(it["source_type"], it["source_id"])
    return {"img": img} if img else {}


def _href(it: dict) -> str:
    """The detail page for THIS row, keyed on the queue's own `source_type`. Row 0412.

    One rule: a card opens the page for the row the card is, and the subject it is about gets a
    link that says what it is. The three arms of `brain.queue_open` already carry the vocabulary
    (`work_item` / `question` / `recommendation`) and every one of them has a route.

    The work item arm is unchanged by construction: on arms 1 and 2 the view selects `w.id` into
    both `source_id` and `work_item_id`, so the old expression and this one produce the same
    string. The recommendation arm is repaired in passing rather than left: `work_item_id` is
    NULL there for a recommendation whose subject is not a work item, and the old fallback then
    built `/question/<numeric id>`, which is a 404 by construction because question ids are
    `q[0-9]{4,}`.
    """
    src, sid = it["source_type"], str(it["source_id"])
    if src == "question":
        return f"/question/{sid}"
    if src == "recommendation":
        return f"/rec/{sid}"
    return f"/task/{it.get('work_item_id') or sid}"


def _card(it: dict, tier: str, of: int, now, counters: dict) -> dict:
    """One queue item as a card: the kind's verb, its label, and the reason it sits where it does.

    Everything about POSITION -- tier, rank, score, band -- is copied from D6b and not recomputed.
    Everything about the CARD is decided here, which is the split the two lanes agreed on.
    """
    kind = KIND_BY_ARM.get((it["source_type"], it.get("primary_verb")),
                           KIND_BY_ARM.get((it["source_type"], None), "review"))
    default = (it.get("default_text") or "").strip()
    card = {
        "kind": kind, "id": str(it["source_id"]), "title": it["title"],
        # D6b'S OWN ADDRESS FOR THE ROW, carried through rather than re-derived. `kind` is the
        # console's word and `source_type` is the queue's, and every verb that addresses a queue
        # item -- bump, demote, defer, and now `queue time start` -- is addressed in the queue's.
        # `actions._source` maps kind back to it for the four existing callers and agrees with
        # this value by construction (`KIND_BY_ARM` is keyed on it); the stopwatch control renders
        # from this one so a template is not a fifth place that mapping lives.
        "source_type": it["source_type"],
        "src": " · ".join(x for x in (it.get("producer"), it.get("source_lane")) if x),
        "tier": tier, "tier_reason": it.get("tier_reason") or "",
        "gates": it.get("gates") or [],
        # `agent waiting Nm` is D6b's band 0 and nothing else: an agent parked on this item for
        # at least `rank.LIVE_IDLE_MINUTES`. It is the one condition that costs fleet wall-clock
        # by the minute, and it is the ranking module's definition rather than a second one.
        "live": _idle_minutes(it, now),
        "unblocks": int(it.get("unblocks_directly") or 0),
        "unblock_weight": it.get("unblock_weight"),
        "default": default or None,
        "default_at": it.get("default_fired_at"),
        "task": it.get("work_item_id") or "",
        # THE CARD'S OWN ADDRESS, WHICH IS NOT THE ADDRESS OF WHAT IT IS ABOUT. Row 0412.
        #
        # `work_item_id` above answers "which work item is this row about", and on the question
        # arm of `brain.queue_open` that is the item the question BLOCKS, never the question. The
        # card template derived both its id link and its `Open` control from that one field, so
        # every question card on the board pointed at a task page, `/question/<id>` was reachable
        # from nowhere in the queue, and an operator who clicked `Open` on the thing he was being
        # asked landed on the task instead, with the word "question" appearing zero times on it.
        # Measured on the seeded demo store before this change: 7 cards on `/queue`, all of them
        # questions, 7 links, 0 of them to a question.
        #
        # It is derived here rather than in the template because the template had it twice and
        # the two copies are what let it drift; `task` stays exactly as it was, and the card
        # renders it as the labelled `blocks` link it already is on `/question/<id>`.
        "href": _href(it),
        "lane": it.get("source_lane") or "",
        # ROW 0434. `brain.queue_open` publishes `w.priority` on all four arms and `rank.
        # band_value` reads that same column, so a filter on it filters on a number the ranking
        # already used. It is ASCENDING-URGENT -- 0 is P0 -- which is why it is rendered as `P0`
        # and never as a bare integer a reader would take for a size. NOTHING WRITES IT: item 2
        # forbids score editing, `set` is not on any room's allowlist, and this key is read-only
        # all the way to the browser, where it becomes a `data-` attribute and a dropdown value.
        "prio": (None if it.get("priority") is None else int(it["priority"])),
        # THE VALUE HALF OF THE SPINE, WHICH THIS SURFACE HAS BEEN NAMING AND NEVER SHOWING.
        #
        # `order_terms()` prints the sentence `ordered by urgency x impact` above this very list,
        # and until this line `impact` appeared in `web/` in that SENTENCE and nowhere else:
        # measured 2026-09-01, five occurrences in this file, all of them the label, zero of them
        # a value. So the page told him what it ranked by and then declined to show him the
        # column, which is the one shape a spreadsheet may not have.
        #
        # NEITHER OF THESE IS NEW DATA AND NEITHER NEEDS A MIGRATION. `brain.queue_open` has
        # published both since ledger 48 (`migrations/0048_impact_is_continuous.sql`), and both
        # read `COALESCE(w.impact, w.stakes)`, so a row that has never been given an impact
        # answers with its stakes through the same fold rather than with a hole.
        #
        # TWO KEYS BECAUSE THEY ANSWER TWO QUESTIONS AND ONLY ONE OF THEM SORTS. `impact` is what
        # the operator TYPED -- a band, or an amount of money -- and is what a cell prints, because
        # rendering `10000` as `high` would throw away the number he chose to be precise about.
        # `impact_magnitude` is the continuous fold the ranking already multiplies by, and it is
        # the only honest sort key: sorting on the raw text would put `critical` under `low`
        # alphabetically and `9000` above `10000` lexically. NOTHING HERE IS WRITTEN. Both are
        # read-only to the browser exactly as `prio` above is, for the same item 2 reason.
        "impact": (it.get("impact") or None),
        "impact_magnitude": (None if it.get("impact_magnitude") is None
                             else float(it["impact_magnitude"])),
        # Stamped after the cards are built, by `_stamp_project`, because it is one read for the
        # whole page rather than one per card. Declared here so the key exists on every card even
        # on the path that does not stamp.
        "project": "",
        "rank": it.get("rank"), "of": of,
        "score": it.get("score"), "band": it.get("band"),
        "defers": int(it.get("defers") or 0),
        "undoable": bool(it.get("undoable")),
        "reserved_oldest": bool(it.get("reserved_oldest")),
        "rec": None, "counter": None,
    }
    card.update(_VERBS[kind])
    if kind == "question":
        card["label"] = "Go with default" if default else "Answer"
        card["needs_text"] = not default
    if kind == "recommendation":
        # The recommendation IS its own text, and the counterargument is the overlay's if a
        # producer wrote one there and `brain.recommendation.rationale` otherwise. Never blank
        # where a rationale exists: the card renders a missing counterargument as the finding it
        # is, and a finding raised by a column this view does not publish would be a false one.
        card["rec"] = it["title"]
        card["counter"] = it.get("counterargument") or counters.get(str(it["source_id"])) or None
    # SHELL-0 (task 0156): each V10 lane extends the card through its own helper above, so V3
    # and V5 never co-edit this function body. Both return {} until their lanes land.
    card.update(_options_fields(it))
    card.update(_media_fields(it))
    card["counter_missing"] = not card["counter"]
    card["why"] = _why(card, it)
    return card


# The per-kind verb and label mapping. This is the console's half of the contract: which verb the
# primary control runs, what it says, and whether it needs typed text before it will submit.
_VERBS = {
    "question": {"verb": "answer", "label": "Answer", "needs_text": True},
    "recommendation": {"verb": "recommend accept", "label": "Approve", "needs_text": False,
                       "reject_verb": "recommend reject", "reject_label": "Reject"},
    "review": {"verb": "accept work", "label": "Accept work", "needs_text": False,
               "reject_verb": "reopen", "reject_label": "Send back"},
    # NO `actor_type` HERE, AND ITS ABSENCE IS THE POINT (task 0427). This entry stamped
    # `actor_type: 'human'` onto every card of this kind, and `web/rooms.py::assert_allowed_on`
    # read that stamp to decide whether `done` was legitimate. `kind` comes from `KIND_BY_ARM` on
    # `(source_type, primary_verb)`, so the stamp said which ARM of `brain.queue_open` the row
    # arrived on and never what `brain.work_item.actor_type` held -- a card asserting a fact
    # about a row that the row was never asked for. The guard now reads the row itself, and the
    # stamp is deleted rather than left as a value nothing consumes: a card carrying a column
    # name it did not measure is how the next guard gets written against the card again.
    "human": {"verb": "done", "label": "Mark my task done", "needs_text": True},
}


def _idle_minutes(it: dict, now) -> int:
    if it.get("band") != 0 or not it.get("agent_idle_since"):
        return 0
    return int(max(0.0, (now - it["agent_idle_since"]).total_seconds()) // 60)


def _counterarguments() -> dict:
    """`rationale` per open recommendation. The one column of the queue's four arms that
    `brain.queue_open` does not publish, read here rather than asked of D6b's view."""
    with store.read() as s:
        rows = s.query("SELECT id, rationale FROM brain.recommendation WHERE state = 'open'")
    return {str(r["id"]): (r["rationale"] or "").strip() for r in rows}


def _why(card: dict, it: dict) -> str:
    """Why this position, decomposed. The number is a projection of this, not the other way round.

    `entities/rules/priority-model.md`: if the order cannot be explained from the signals and the
    rules, the order is wrong. So the tier's reason, the jump band, and every non-zero term of the
    sum are printed, and the terms are D6b's own rather than a restatement.
    """
    where = TIER_NAME.get(card["tier"], card["tier"])
    bits = [f"#{card['rank']} of {card['of']} in {where}" if card.get("rank") else
            f"{where}, unranked: it waits on "
            + (", ".join(it.get("blocked_on") or []) or "a blocker") + " and a blocked item is "
            "never dispatched"]
    if card["tier_reason"]:
        bits.append(card["tier_reason"])
    if it.get("reversibility_floor"):
        bits.append(f"reversibility floor held it out of Decide "
                    f"({it.get('reversibility_level') or 'unset'})")
    if it.get("band_reason"):
        bits.append(it["band_reason"])
    terms = {k: v for k, v in (it.get("terms") or {}).items() if v}
    bits.append(f"score {it.get('score', 0):+.2f}" + (
        " = " + " ".join(f"{k} {v:+.2f}" for k, v in terms.items()) if terms else
        ", every term zero"))
    for b in it.get("bumps") or []:
        bits.append(f"bumped {b['delta']:+g} by {b['by'] or 'the operator'} {b['age_hours']:.0f}h "
                    f"ago, worth {b['now_worth']:+.2f} now: {b['reason']}")
    if card["reserved_oldest"]:
        bits.append("rendered because it is the oldest item in this tier and the window would "
                    "otherwise have starved it")
    return " · ".join(bits)


def defer_options(item: dict) -> dict:
    """Every path out of the queue carries a wake condition. There is no untyped defer.

    D6b's `queue defer` registers all five kinds and migration 0007 gives them a home, so the
    time chips are LIVE. Before D6b landed at 16:04Z there was no wake-condition column anywhere
    and the chips rendered disabled with that reason on them; the fallback below is what runs if
    the queue tables are absent, and it says so rather than rendering a control that would fail.

    Two behaviours here are the verb's, not this function's, and are stated so nobody
    reimplements them in the UI:

      * **The third defer refuses to be a defer.** After two, the time chips go and the options
        are decline or move to Shape. Three deferrals means mis-scoped, not mis-timed.
      * **Deferring past a silence window is an interstitial.** The verb refuses until the caller
        acknowledges what silence will ship, so the console shows the sentence and passes through
        what the operator actually ticked. It never acknowledges on his behalf.
    """
    typed = "queue defer" in store.registered()
    # The count is D6b's, off the item it already built: `defers` counts the three kinds that ARE
    # postponements (until-time, until-event, until-question) and not `decline` or a move to
    # Shape, which are exits rather than deferrals. The console used to run its own `count(*)`
    # over every row in `queue_defer`, which reached three after two postponements and a decline
    # that had already left the queue.
    n = int(item.get("defers") or 0) if typed else 0
    live = [{"label": "what would make this decidable? →", "action": "defer_question",
             "hint": "posts an agent task for the missing input and wakes when it lands"},
            {"label": "decline", "action": "decline", "warn": True,
             "hint": "returned to the producer, re-raisable once and only with a new event"}]
    if item.get("default"):
        live.insert(0, {"label": "go with the default now", "action": "accept_default",
                        "hint": "recorded as a decision, not a snooze"})
    # THE CHIPS CARRY A SPEC AND NOT A TIMESTAMP, and the timestamp is computed at submit time by
    # `wake_at_from` below. `+2h` used to be rendered as an absolute time to the microsecond, so
    # the markup of every Defer panel was different on every poll, three seconds apart, whether or
    # not anything had changed. Measured: two /api/patch/queue payloads 3.5s apart differed by
    # ~8 characters of 5939, all of them this field. A wake condition is also more honest computed
    # from the moment the operator ticked it than from the moment a poll happened to render.
    chips = [] if (typed and n >= 2) else [
        {"label": "+2h", "wake_in": "+2h", "why": "no wake-condition column"},
        {"label": "this evening", "wake_in": "at:19", "why": "no wake-condition column"},
        {"label": "tomorrow AM", "wake_in": "at:9:tomorrow", "why": "no wake-condition column"},
    ]
    return {
        "live": live,
        "disabled": chips,
        "typed_defer": typed,
        "third_defer": typed and n >= 2,
        "acknowledged": False,
        "disabled_note": (
            "Time and event chips are disabled: the queue tables are not present in this store, "
            "so a timed defer would live only in this browser. A queue nothing else can see is a "
            "second queue."),
        "default_note": (
            f'Deferring past the window ships the default: "{item["default"]}". Extending the '
            f"window is a separate act, and this defer records that you were told."
            if item.get("default") else None),
    }


def wake_at_from(spec: str) -> str:
    """Resolve a defer chip's wake SPEC to an absolute time, at the moment the chip was clicked.

    Three specs, matching the three chips: `+<n>h`, `at:<hour>`, `at:<hour>:tomorrow`. An
    unrecognised spec returns the empty string rather than a guessed time, and the verb refuses a
    timed defer with no wake condition, which is the correct end for a spec nobody wrote.
    """
    spec = (spec or "").strip()
    if spec.startswith("+") and spec.endswith("h"):
        try:
            return _in_hours(int(spec[1:-1]))
        except ValueError:
            return ""
    if spec.startswith("at:"):
        bits = spec.split(":")
        try:
            return _at_hour(int(bits[1]), tomorrow=(len(bits) > 2 and bits[2] == "tomorrow"))
        except (IndexError, ValueError):
            return ""
    return ""


def _in_hours(h: int) -> str:
    return (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=h)).isoformat()


def _at_hour(hour: int, tomorrow: bool = False) -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    when = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if tomorrow or when <= now:
        when += _dt.timedelta(days=1)
    return when.isoformat()


# --------------------------------------------------------------- the operator's stopwatch
#
# Task 0284, over the ledger task 0279 built. Both functions here are THIN: they call
# `human_queue.reads`, which is the one implementation, and they add exactly two things the
# console needs and the CLI does not -- a `who` this surface can be trusted to keep, and a
# degraded shape for a store that has D6b's queue tables and not migration 23's ledger.
#
# **WHO.** `brain.time_entry` is keyed on `who`; `reads.time_status`, `reads.depth_and_clearance`
# and `reads.tier_ceilings` all default it to `'operator'`; and `web/actions.py` writes with
# `who = OPERATOR`. This module READS with the same name, so the console's own two halves cannot
# disagree about whose minutes it is showing. What CAN still disagree is the console and the CLI:
# set `CONSOLE_OPERATOR=andrew` and this surface measures `andrew` while `queue depth` with no
# `--who` reports on `operator` and shows nothing. That is a real trap and the honest place to put
# it is here, next to the constant, rather than in a release note.

STOPWATCH_DAYS = 7


def _no_ledger(exc: Exception) -> dict:
    """A store without migration 23. Say which migration, do not hide the control.

    `queue_view` RAISES on a missing `brain.queue_open`, because a queue room with no queue has
    nothing to render and no order of its own to fall back on. The stopwatch is different: the
    room is fine without it, and the console's rule for a thing the store cannot answer is to say
    so in the place the figure would have been. A strip that silently disappeared would be
    indistinguishable from a strip with no timer running, which is the one confusion this surface
    cannot afford -- it is the surface whose whole job is to say whether a timer is running.
    """
    return {"unavailable": (
        "the operator's stopwatch is `brain.time_entry`, migration 23, which lives at "
        "queue/schema/0012_operator_time_entry.sql and is not applied to this store. Nothing here "
        "is measured, and it is null rather than zero. Underlying error: " + str(exc))}


def stopwatch() -> dict:
    """The running-timer strip: what is running, for how long, and the warning BEFORE the stop.

    `running.warning` is the point of rendering this at all and it is written by `reads` rather
    than composed here. It fires while the entry is still open, because an operator who learns at
    stop time that his four hours were relabelled `abandoned` has already lost the chance to
    correct them into something true; one who is told while the timer runs can still stop it and
    `queue time correct` it. So the strip is a patched region and the warning is the loudest thing
    in it.

    Read on every three-second poll, which is affordable: one scalar and two indexed reads over
    one operator's entries for one day.
    """
    try:
        return Q.time_status(who=OPERATOR, days=1)
    except Exception as exc:                                            # noqa: BLE001
        return _no_ledger(exc)


def depth() -> dict:
    """Depth, clearance and the two ETAs, for the block the operator plans his morning from.

    NOTHING IS SUMMARISED AWAY HERE, and that is the whole reason this is a pass-through. The read
    reports coverage (`measured over N of M`), a null per tier with the reason attached, and an
    hours total that covers only the tiers that HAVE a measurement, with the others named beside
    it. A console that rendered the hours and dropped the coverage would recreate exactly the
    implication of totality the read was built to refuse -- the timed set is a subset, because a
    timer is never required and an untimed item is normal.

    Called from the template on a full render only; `web/app.py:_queue_ctx` says why.
    """
    try:
        return Q.depth_and_clearance(days=STOPWATCH_DAYS, who=OPERATOR)
    except Exception as exc:                                            # noqa: BLE001
        return _no_ledger(exc)


# --------------------------------------------------------------------------- the review view

def questions_on(item_id: str) -> list[dict]:
    """Every question raised against one work item, open and closed, oldest first. Row 0412.

    `/task/<id>` is where a blocked item is actually read, and it could not say what it was
    blocked ON. It rendered `state blocked` twice and the word "question" zero times, with no
    link to `/question/<id>` anywhere on the page, so an operator who reached the task from the
    queue had arrived at a dead end. The store has always had the edge: `work_item_id` on
    `brain.question`, indexed as `question_task_idx` since migration 1. Nothing read it back.

    ANSWERED AND WITHDRAWN QUESTIONS ARE RETURNED TOO, not just open ones. The open ones are why
    the item is stopped; the closed ones are the decisions the item was built on, and a screen
    that showed only what is still pending would drop the record the moment it stopped being
    urgent. `SELECT *` with `.get` on the migration-31 columns so a store below 31 renders the
    two states it has rather than raising.
    """
    with store.read() as s:
        rows = s.query("SELECT * FROM brain.question WHERE work_item_id = %s "
                       "ORDER BY asked_at", (item_id,))
    for r in rows:
        r["status"] = ("answered" if r.get("answer") is not None
                       else "withdrawn" if r.get("withdrawn_at") else "open")
    return rows


def review(item_id: str) -> dict | None:
    """The screen where Judge earns its tier: every claim with its evidence, and the trail."""
    w = R.work_item(item_id)
    if not w:
        return None
    arts = R.artifacts(item_id)
    th = R.thread(item_id)
    paths = [a["path"] for a in arts]
    analysis = C.analyse(w.get("result") or "", paths)

    # THE WORK ORDER, FROM THE COLUMN THAT HOLDS THE WORK ORDER. Task 0169, after migration 14.
    #
    # This screen used to read the brief out of `result`, and had to blank it on a finished item
    # (`"" if w["state"] in ("done", "blocked")`) because `post` and `done` wrote the same column
    # by turns and the second write erased the first. When that left no definition readable, it
    # then scanned the THREAD for the copy the Scope room duplicated there for exactly this
    # reason. Both branches were workarounds for a store defect, this screen found it, and
    # migration 14 fixed it: `brief` is write-once, no verb overwrites it, and `R.work_item` is
    # `SELECT *`, so it is correct in EVERY state with no read-layer change. `.get` rather than
    # `[...]` so a console pointed at a store below migration 14 renders an empty brief instead
    # of a KeyError -- and empty is then the true answer for that store, not a hidden one.
    brief_text = w.get("brief") or ""
    dod = C.definition_of_done(brief_text)
    dod_note = None
    if not dod:
        # Empty now means empty. Migration 14 recovered the posted body only for rows no finisher
        # had ever touched; for the rest the text is not in the database, and it says so rather
        # than reconstructing one from the summary. An invented work order is worse than an
        # absent one.
        dod_note = (
            "No definition of done is readable for this item. Either it was posted without one, "
            "or it predates the column that keeps one: until migration 14 the posted brief shared "
            "`work_item.result` with the agent's report and the report overwrote it, so for items "
            "already finished when that landed the text is not in this database at all. It is not "
            "reconstructed from the summary here, because a work order invented from a report is "
            "worse than an absent one. Anything posted since carries its brief unchanged.")

    # THIS IS AN ALLOWLIST, SO A KIND THE STORE GAINS AND THIS LIST DOES NOT IS DROPPED, not
    # rendered as unknown. `budget` (migration 13) is here for that reason: the budget stop line
    # used to be filed as `note` and was visible only by accident of that workaround. Anything
    # added to `brain.thread.kind` has to be added here in the same change. crosstalk() below
    # does not have this problem: it falls through .get(kind, "rep") and renders.
    trail = [t for t in th if t["kind"] in
             ("post", "claim", "done", "fail", "block", "reopen", "accept", "ask", "answer",
              "note", "msg", "set", "reap", "budget", "unaccept")]
    for t in trail:
        t["stamp"] = {"accept": "ACCEPT", "reopen": "REOPEN", "done": "DONE", "ask": "ASKS",
                      "fail": "FAIL", "block": "BLOCK", "unaccept": "UNACCEPT"}.get(t["kind"])
        # `unaccept` is deliberately given NO class, which is neither `acc` nor `rej`.
        #
        # Row 0413, migration 43. The verb withdraws the OPERATOR'S OWN DECISION and touches
        # neither `state` nor `result`, so the agent's report survives it intact. Painting it
        # `rej` would render a withdrawal as a verdict on the agent's work, which is the one
        # thing this verb was built not to be: `reopen` is how you send work back, and the
        # whole reason acceptance needed its own inverse is that `reopen` says something
        # different and louder. It is also the honest reading of item 7's open question about
        # `REOPEN` stamps being red: this lane does not add a fourth red that is not damage.
        t["cls"] = {"accept": "acc", "done": "acc", "reopen": "rej", "fail": "rej",
                    "block": "rej", "ask": "q"}.get(t["kind"], "")

    for a in arts:
        # `exists_at_record` is what was true when the claim was made. Re-statting is the reader's
        # job, and a path that was recorded and is now gone is a finding rather than a formatting
        # problem, so both facts are rendered rather than one.
        a["exists_now"] = os.path.exists(a["path"]) if a.get("path") else False
        a["drifted"] = bool(a.get("exists_at_record")) and not a["exists_now"]

    sig = R.signals(item_id) or {}
    return {
        "item": w, "signals": sig, "analysis": analysis, "dod": dod, "dod_note": dod_note,
        # `brief` is passed separately from `item` so the template does not have to know which
        # column the work order lives in, and so the panel that renders it cannot silently start
        # rendering `result` again the way this screen used to.
        "brief": brief_text,
        "trail": trail, "artifacts": arts, "thread": th,
        # V5 (task 0167). The item detail page is where a work item is actually READ, so an image
        # attached to it renders here as well as on its queue card -- "render it in the console
        # where the item is rendered", and this screen is one of the two places it is. Verified
        # at render time like every other caller: `img.state` is measured now, not stored.
        "img": images.for_card("work_item", item_id),
        # Row 0416. THE SAME SENTENCE THE QUEUE CARD CARRIES, from the same function, because
        # this page has the identical shape of the defect: `Accept work` renders above `The
        # report, in full`, so the report's own caveat is below the button here too.
        "caveat": C.caveats(w.get("result") or ""),
        # Row 0414. `Send back` renders on this page too, and where it sends the row is a fact
        # about `agent_claimable` (on `item`, since `R.work_item` is `SELECT *`) AND about
        # whether anything is running to take it. Read here rather than left to the template,
        # because a template that cannot read it renders an undefined as `the fleet will pick it
        # up`, which is the confident wrong destination this row is about.
        "fleet_paused": R.fleet_paused(),
        # Row 0412: the route out of this page and back to what stopped the item.
        "questions": questions_on(item_id),
        "would_auto_accept": _would_auto_accept(item_id),
    }


def _would_auto_accept(item_id: str) -> dict:
    from swarm_engine import accept as A
    try:
        return {"enabled": A.enabled(), **A.would_accept(item_id)}
    except Exception as exc:                                            # noqa: BLE001
        return {"enabled": False, "eligible": False, "reasons": [f"unavailable: {exc}"]}


# --------------------------------------------------------------------------- the brief

def brief() -> dict:
    """A read-then-act ramp, in the specified order, with every figure sourced."""
    with store.read() as s:
        window_hours = 12
        agent_minutes = float(s.scalar(
            "SELECT COALESCE(sum(EXTRACT(epoch FROM (COALESCE(ended_at, now()) - started_at))), 0)"
            " / 60.0 FROM brain.run WHERE started_at > now() - interval '12 hours'") or 0)
        shipped = s.query(
            "SELECT id, title, finished_at, result FROM brain.work_item "
            " WHERE state = 'done' AND finished_at > now() - interval '12 hours' "
            " ORDER BY finished_at DESC")
        gated = s.query(
            "SELECT w.id, w.title, s.external, s.canon_touching FROM brain.work_item w "
            "  JOIN brain.work_item_signals s ON s.id = w.id "
            " WHERE w.state = 'done' AND w.accepted_at IS NULL "
            "   AND (s.external OR s.canon_touching) ORDER BY w.finished_at")
        # DEFAULTED, computed: an answer whose text IS the stated default is what silence
        # decided. There is no `defaulted` flag in migration 1, and equality with
        # `default_if_unanswered` is the only computable definition; an operator who types the
        # default verbatim lands here too, which is a false positive worth having over a
        # DEFAULTED block that cannot be built at all.
        defaulted = s.query(
            "SELECT q.id, q.text, q.answer, q.answered_at, q.default_if_unanswered, "
            "       q.asked_by, q.work_item_id, w.external, w.state "
            "  FROM brain.question q LEFT JOIN brain.work_item w ON w.id = q.work_item_id "
            " WHERE q.answer IS NOT NULL AND q.answer = q.default_if_unanswered "
            "   AND q.answered_at > now() - interval '36 hours' ORDER BY q.answered_at DESC")
        overrides = s.query(
            "SELECT asked_by AS producer, count(*) AS n, "
            "  count(*) FILTER (WHERE answer <> default_if_unanswered) AS overridden "
            " FROM brain.question WHERE answer IS NOT NULL GROUP BY asked_by ORDER BY asked_by")
        # RED MEANS DAMAGE AND NOTHING ELSE, shell wide. A task blocked on an operator answer is
        # WAITING ON A HUMAN, which is amber: counting it red would make red the ordinary colour
        # of a working night and there would be no colour left for a task that is actually broken.
        # Red is a task that spent every attempt it had.
        red = s.scalar("SELECT count(*) FROM brain.work_item "
                       " WHERE state = 'blocked' AND attempts >= max_attempts")
        waiting = s.scalar("SELECT count(*) FROM brain.work_item w "
                           " WHERE w.state = 'blocked' AND w.attempts < w.max_attempts")
        because = _because_of_you(s)

    for d in defaulted:
        # An external default already consumed cannot be reopened, and the affordance says so
        # rather than being offered and failing.
        d["external_ran"] = bool(d.get("external"))
        d["reopenable"] = bool(d.get("work_item_id")) and not d["external_ran"]

    for o in overrides:
        o["rate"] = (100.0 * o["overridden"] / o["n"]) if o["n"] else 0.0
        o["hot"] = o["rate"] > 20.0

    lev = _leverage(window_hours, agent_minutes)

    return {
        "window_hours": window_hours,
        "agent_hours": round(agent_minutes / 60.0, 1),
        # BOTH HALVES ARE MEASURED NOW, over the same window, and the ratio carries its coverage.
        # `_leverage` is where the argument is; `operator_minutes` stays null rather than zero when
        # nothing was timed, because zero minutes is not what an untimed window means.
        "operator_minutes": lev["operator_minutes"],
        "leverage": lev,
        "ratio_note": lev["note"],
        "shipped": shipped, "gated": gated, "defaulted": defaulted,
        "overrides": overrides, "red": red, "waiting": waiting, "because": because,
        "auto": R.auto_accept_candidates(),
    }


def _leverage(window_hours: int, agent_minutes: float) -> dict:
    """The leverage ratio: measured over measured, in ONE window, and NEVER without its coverage.

    Task 0290. Until migration 23 this was the file's first stated absence and the sentence beside
    it was true. It is not true any more, and a console that goes on printing *measured nowhere*
    for a figure its own store holds is telling the same kind of lie as an illustrative number --
    on the one screen whose pitch is double-entry accountability.

    **THE COVERAGE IS THE WHOLE PROBLEM AND IT IS WHY THIS FUNCTION IS NOT THREE LINES.** Agent
    hours are TOTAL: every run in the window is in `brain.run` and nothing asks the fleet to opt
    in. Operator minutes are a SUBSET: a timer is never required, an untimed item is normal and
    always will be (migration 23 states that as a policy, not a shortfall). A total divided by a
    subset is inflated by exactly however undisciplined he was about pressing start, and this is
    the single most quotable figure on the surface -- the one number that would end up in a
    sentence about what this system is worth. So the coverage travels INSIDE `note`, not beside it
    in a field a template could drop: whatever renders the number renders how much of his time it
    saw, or the number is not rendered at all.

    **ONE WINDOW, AND IT IS THE SCREEN'S OWN.** The Brief states everything over 12 hours and says
    so in its header. `reads.time_status` and `reads.depth_and_clearance` take `days`, so the
    choice was to state the ratio over a day or to measure the operator's half over 12 hours;
    `reads.operator_minutes` exists so it can be the second. A 24h denominator under a 12h header
    would be two windows on one screen, and the reader has no way to see which figure is which.

    **WHICH WAY IT IS WRONG, since it cannot be labelled a ceiling.** The denominator misses
    untimed work, abandoned entries, superseded ones and any timer still running -- all of which
    push the ratio UP, and the first of them without bound. Two smaller errors push it DOWN: an
    entry that started before the window is counted in full, and `agent_minutes` ignores runs that
    started before the window. The dominant error is coverage and it inflates, which is why
    coverage is the thing printed next to the number.

    THREE OUTCOMES, and two of them are still absences:
      * no ledger in this store  -> named absence, with the migration.
      * ledger, nothing timed    -> named absence, and NULL rather than a zero denominator.
      * ledger with measurement  -> the ratio, its window, and its coverage in one sentence.
    """
    try:
        om = Q.operator_minutes(hours=window_hours, who=OPERATOR)
    except Exception as exc:                                                # noqa: BLE001
        return {
            "ratio": None, "operator_minutes": None, "coverage": None, "excluded": [],
            "timed_items": 0, "window_hours": window_hours, "who": OPERATOR,
            "note": ("Operator minutes are not measured in THIS store: the ledger that holds them "
                     "is `brain.time_entry`, migration 23 at "
                     "queue/schema/0012_operator_time_entry.sql, and it is not applied here. So "
                     "the leverage ratio still has one real half, and the half that is real is on "
                     f"the left. Underlying error: {exc}")}
    mins = float(om["measured_minutes"] or 0.0)
    out = {"ratio": None, "operator_minutes": mins if om["timed_items"] else None,
           "coverage": om["coverage"], "excluded": om["excluded"],
           "timed_items": om["timed_items"], "window_hours": window_hours, "who": om["who"]}
    if not om["timed_items"] or mins <= 0:
        # NULL, NOT ZERO, and not an infinite ratio either. An untimed window means nothing was
        # measured; it does not mean he spent no minutes, and dividing by it would produce the most
        # flattering number on the screen out of the least evidence.
        out["note"] = (
            f"No operator minutes were measured in the last {window_hours}h, so the leverage "
            f"ratio still has one real half -- the ledger for the other half exists now "
            f"(`brain.time_entry`, migration 23) and has nothing in this window. It is null "
            f"rather than zero. Starting a timer on a card is what fills it in, and a timer is "
            f"never required: {om['coverage']['text']}.")
        return out
    # WHOLE NUMBERS FROM 10x UP, one decimal below it. Two reasons and neither is aesthetic: it is
    # how a leverage figure is actually quoted (`77x`, not `77.3x`), and this whole page is ONE
    # polled region, so every digit of precision is a digit that can change under a live run and
    # swap the markup of every form on the screen. A tenth of a decimal on a 3-figure ratio is
    # churn bought with no information.
    raw = agent_minutes / mins
    out["ratio"] = round(raw) if raw >= 10 else round(raw, 1)
    cov = om["coverage"]
    pct = f" ({cov['rate'] * 100:.0f}% of them)" if cov["rate"] is not None else ""
    out["note"] = (
        f"{out['ratio']:g}x is {round(agent_minutes / 60.0, 1):g}h of measured fleet time over "
        f"{mins:g} measured minutes of yours, both over the same {window_hours}h. READ THE SECOND "
        f"NUMBER BEFORE QUOTING THE FIRST: fleet time is total, your minutes are only the ones a "
        f"timer was running for, so this ratio is inflated by however much of your time went "
        f"unmeasured -- {cov['text']}{pct}"
        + ("; " + "; ".join(om["excluded"]) if om["excluded"] else "")
        + ". Coverage over ITEMS is also not coverage over minutes: time you spend on anything "
          "that is not a queue item cannot be recorded in that ledger at all.")
    return out


def _because_of_you(s) -> list[dict]:
    """Lineage walked from the operator's OWN actions, rejections included.

    A send-back that caught a real defect produces visible value and nothing else on the surface
    shows it, so `reopen` rows are first-class here rather than filtered out as negatives.
    """
    acts = s.query(
        "SELECT t.seq, t.ts, t.kind, t.work_item_id, t.text FROM brain.thread t "
        " WHERE t.from_agent = %s AND t.kind IN ('answer', 'reopen', 'accept', 'post') "
        "   AND t.ts > now() - interval '36 hours' ORDER BY t.ts DESC LIMIT 8", (OPERATOR,))
    out = []
    for a in acts:
        wid = a["work_item_id"]
        # TWO INDEPENDENT READS, NOT A JOIN. These are two unrelated counts that happen to share
        # a key, and joining them multiplied both: `brain.artifact FULL JOIN brain.run` on
        # work_item_id is a cartesian product WITHIN one item, so an item with 3 artifacts and 2
        # runs read as 6 artifacts with each run's duration summed three times. It also invented
        # artifacts outright -- with no artifacts and 2 runs the join still emits 2 run-side rows
        # and count(*) called them artifacts. It rendered as zeros only because a seeded
        # brain.run holds sub-second rows; the first night the fleet did real work, the ONE block
        # whose job is making operator leverage legible would have inflated it by the run count.
        #
        # NO JOIN OF ANY KIND FIXES THIS, which is worth stating because narrowing the FULL JOIN
        # to a plain one is the obvious repair and it is not a repair. Measured, not reasoned:
        # with `JOIN` the same 3-artifact 2-run item still reads 6 artifacts -- the product is
        # the join, not its outer-ness -- and it additionally drops the two cases the FULL JOIN
        # was written to serve, a failed attempt that burned agent minutes without writing
        # anything, and an artifact recorded outside any tracked run. It fails all three tests.
        # Subqueries are what keeps both halves without letting either touch the other.
        # web/tests/test_because_of_you.py seeds those three shapes and is the guard.
        after = s.one(
            "SELECT (SELECT count(*) FROM brain.artifact a"
            "         WHERE a.work_item_id = %s AND a.ts > %s) AS arts, "
            "       (SELECT COALESCE(sum(EXTRACT(epoch FROM"
            "                 (COALESCE(r.ended_at, now()) - r.started_at))), 0) / 60.0"
            "          FROM brain.run r"
            "         WHERE r.work_item_id = %s AND r.started_at > %s) AS minutes",
            (wid, a["ts"], wid, a["ts"])) or {}
        unblocked = s.scalar(
            "SELECT count(*) FROM brain.work_item WHERE depends_on LIKE %s", (f"%{wid}%",)) or 0
        out.append({
            "kind": a["kind"], "task": wid, "ts": a["ts"], "text": a["text"],
            "artifacts": int(after.get("arts") or 0),
            "minutes": round(float(after.get("minutes") or 0), 0),
            "unblocked": unblocked,
        })
    return out


# --------------------------------------------------------------------------- fleet

def fleet() -> dict:
    ags = R.agents()
    for a in ags:
        a["state"] = ("stopped" if a["stopped"] else
                      "dead" if (a["age_seconds"] or 0) > 900 and a["status"] == "working" else
                      a["status"] or "idle")
        a["colour"] = {"working": "run", "waiting": "wait", "dead": "dmg",
                       "stopped": "rest", "idle": "rest"}.get(a["state"], "rest")
    return {
        "agents": ags,
        "paused": R.fleet_paused(),
        # The queue appears in Fleet as a COUNT and never as cards. Fleet is a dispatch control
        # surface; two queues on one screen is two queues. The count is the TOTAL open queue and
        # not the number of cards the Queue room happens to be rendering: a window is a rendering
        # decision and this is a fact about the fleet's obligation.
        "queue_count": queue_view()["totals"]["open"],
        "lag": R.subscriber_lag(),
    }


# --------------------------------------------------------------------------- study

def study() -> dict:
    """Counters that are links. No charts, no percent-complete, no synthesised health score."""
    with store.read() as s:
        sessions = s.scalar("SELECT count(*) FROM brain.session")
        sessions_today = s.scalar(
            "SELECT count(*) FROM brain.session WHERE started_at > now() - interval '24 hours'")
        events = s.query(
            "SELECT type, count(*) AS n FROM brain.event GROUP BY type ORDER BY n DESC")
        recs = s.one(
            "SELECT count(*) AS n, count(*) FILTER (WHERE state <> 'pending') AS decided, "
            "       count(*) FILTER (WHERE state = 'accepted') AS accepted "
            "  FROM brain.recommendation")
        arts = s.scalar("SELECT count(*) FROM brain.artifact")
        transcripts = s.one(
            "SELECT count(*) AS n, COALESCE(sum(bytes), 0) AS bytes, "
            "       count(*) FILTER (WHERE verified_ok) AS verified FROM brain.transcript")
        agent_minutes = float(s.scalar(
            "SELECT COALESCE(sum(EXTRACT(epoch FROM (COALESCE(ended_at, now()) - started_at))), 0)"
            " / 60.0 FROM brain.run") or 0)
        runs = s.scalar("SELECT count(*) FROM brain.run")
        # One bar per day, no smoothing, no projection, no forecast segment.
        days = s.query(
            "SELECT date_trunc('day', occurred_at)::date AS day, count(*) AS n FROM brain.event "
            " WHERE occurred_at > now() - interval '14 days' GROUP BY 1 ORDER BY 1")
    acted = (100.0 * recs["accepted"] / recs["n"]) if recs and recs["n"] else None
    return {
        "sessions": sessions, "sessions_today": sessions_today, "events": events,
        "event_total": sum(e["n"] for e in events),
        "recs": recs, "acted_rate": acted, "artifacts": arts, "transcripts": transcripts,
        "agent_hours": round(agent_minutes / 60.0, 1), "runs": runs,
        "days": days, "day_max": max([d["n"] for d in days], default=0),
        "lag": R.subscriber_lag(),
        "health": store.health(),
    }


# --------------------------------------------------------------------------- crosstalk

# Speech versus noise. Dialogue renders in full; the machine's own bookkeeping collapses to a
# fold. Without this the interesting three lines drown in ninety.
SPEECH = ("post", "ask", "answer", "reanswer", "msg", "note", "done", "reopen", "accept",
          "fail", "block")
NOISE = ("claim", "set", "artifact", "reap", "heartbeat", "run")


def crosstalk(item_id: str | None = None, minutes: int = 1440) -> dict:
    """The agent conversation as a readable narrative, not a log dump.

    **The fold is labelled for what the store actually holds.** The mock folds `47 tool calls`;
    tool calls live in the transcript, not in `thread`, so folding them here and calling them
    tool calls would be a caption for data this screen does not have. It folds the runtime
    records it does have and says so.
    """
    with store.read() as s:
        if item_id:
            rows = s.query(
                "SELECT seq, ts, from_agent, to_agent, kind, text FROM brain.thread "
                " WHERE work_item_id = %s ORDER BY seq", (item_id,))
        else:
            rows = s.query(
                "SELECT seq, ts, from_agent, to_agent, kind, text FROM brain.thread "
                " WHERE ts > now() - make_interval(mins => %s) ORDER BY seq DESC LIMIT 200",
                (minutes,))
            rows.reverse()

    feed, fold = [], []

    def flush():
        if not fold:
            return
        span = _mins(fold[0]["ts"], fold[-1]["ts"])
        feed.append({"k": "fold", "n": len(fold), "m": f"{span:.0f}m",
                     "lines": [f"{f['kind']} · {f['from_agent'] or '?'} · "
                               f"{(f['text'] or '')[:110]}" for f in fold]})
        fold.clear()

    for r in rows:
        if r["kind"] in NOISE:
            fold.append(r)
            continue
        flush()
        k = {"accept": "acc", "done": "acc", "reopen": "rej", "fail": "rej", "block": "rej",
             "ask": "ask", "answer": "ans", "reanswer": "ans", "post": "cmd",
             "msg": "cmd"}.get(r["kind"], "rep")
        feed.append({
            "k": k, "from": r["from_agent"] or "?", "to": r["to_agent"] or "",
            "t": r["ts"].strftime("%H:%M") if r["ts"] else "",
            "b": r["text"] or "",
            "stamp": {"acc": "ACCEPT" if r["kind"] == "accept" else "DONE", "rej": "REOPEN",
                      "ask": "ASKS"}.get(k),
        })
    flush()
    who = sorted({e["from"] for e in feed if e.get("from")} |
                 {e["to"] for e in feed if e.get("to")})
    return {"feed": feed, "agents": who, "complete": True, "item": item_id}


# --------------------------------------------------------------------------- celebration

# Fires on VERIFIED events arriving from the system, never on inputs going into it. A click is
# an intention; confetti for an intention is a slot machine. Caps are per calendar day.
#
# ROW 0409 CHANGES WHO IS FILTERED OUT AND NOT WHAT COUNTS AS AN EVENT. The tiers, the caps and
# the sentence above are untouched. What changed is that "not mine" is now asked of
# `from_agent` rather than inferred from a cursor the client moved, and that tier 1 no longer
# reaches the particle canvas -- see `console.js`, which is where the decision about MOTION
# lives, because it is a decision about the surface rather than about the events.
CELEBRATION_CAPS = {1: 3, 2: 1}


def celebrations(since_seq: int, mine: str | None = None) -> dict:
    """What the SYSTEM has confirmed since the browser last looked, MINUS the viewer's own acts.

    Tier 2 is reserved for a wager verdict passing, the only exogenous confirmation of value in
    the system. `disposition.wager_ref` is where that lands, and until a wager is scored this
    returns tier 1 only -- an empty tier 2 rather than a lowered bar for it.

    ----------------------------------------------------------------------------------------
    `mine` REPLACES A CURSOR JUMP WITH AN IDENTITY, AND THAT IS THE WHOLE OF ROW 0409.
    ----------------------------------------------------------------------------------------
    The rule "a click never celebrates itself" is right and is kept verbatim. It was enforced by
    the CLIENT moving its cursor to the store's head on every write it made (`console.js`, the
    line that read "the cursor jumps past this write"), and a cursor is the wrong instrument for
    it, because one cursor was answering two different questions:

        "what have I already seen"   -- a position, correctly a cursor
        "which of these are MINE"    -- an identity, and `brain.thread.from_agent` holds it

    Measured on `brain_c0409` 2026-08-28, all three consequences of the substitution:

      1. the operator's own `accept work` was never celebrated -- intended, and correct;
      2. every other actor's `done` fired a full-viewport particle burst at him on the next
         three-second poll whatever he was doing -- confetti at +2.12s from a CLI `done` in
         another terminal while he sat reading an opened panel;
      3. AND, undocumented anywhere until this row: his own click ALSO destroyed other actors'
         un-celebrated events. `celebrate_from` is `max(seq)` over the whole thread, so a `done`
         that landed 200ms before he pressed Accept was stepped over and never shown. Reproduced
         12s of silence where the same `done` fires reliably at ~2s with no click.

    Asking `from_agent <> mine` in SQL keeps (1) by construction -- an identity cannot go stale
    the way a position can -- and removes (2)'s randomness and (3) entirely. The client no longer
    touches its cursor on a write.

    `mine` is the console's `OPERATOR`, which is `store.human_slug()`, which is the same string
    `accept work` writes into `ctx.actor` and therefore into `brain.thread.from_agent` (verified:
    seq 145, `from_agent = 'operator'`, from a real console POST). `None` means "filter nothing",
    which is what a caller with no viewer identity gets, and is the old behaviour.
    """
    with store.read() as s:
        rows = s.query(
            "SELECT t.seq, t.ts, t.from_agent, t.kind, t.work_item_id, w.title "
            "  FROM brain.thread t "
            "  LEFT JOIN brain.work_item w ON w.id = t.work_item_id "
            " WHERE t.seq > %s AND t.kind IN ('done', 'accept') "
            "   AND (%s::text IS NULL OR t.from_agent IS DISTINCT FROM %s::text) "
            " ORDER BY t.seq", (since_seq, mine, mine))
        head = s.scalar("SELECT COALESCE(max(seq), 0) FROM brain.thread")
        wagers = s.query(
            "SELECT id, verdict, rationale, decided_at FROM brain.disposition "
            " WHERE wager_ref IS NOT NULL AND decided_at > now() - interval '24 hours' "
            " ORDER BY decided_at DESC LIMIT 3")
    out = []
    for w in wagers:
        out.append({"tier": 2, "who": None, "item": None,
                    "text": f"wager {w['id']} {w['verdict']} · "
                            f"{w['rationale'] or 'exogenous result confirmed'}"})
    for r in rows:
        # THE LINE NAMES WHAT IT IS ABOUT. It read `0013 done by T9`: an id, a verb and an agent
        # name, which is why the operator's word for the whole thing was "randomly" -- a burst
        # whose only referent is a four-digit id is a burst with no referent. The title comes
        # from `brain.work_item` and is AGENT-WRITTEN TEXT, so `console.js` renders these fields
        # with textContent and never by concatenating them into innerHTML. `say()` already made
        # exactly that choice for the same reason.
        who = r["from_agent"] or "somebody"
        verb = "finished" if r["kind"] == "done" else "accepted"
        title = (r["title"] or "").strip()
        out.append({"tier": 1, "who": who, "item": r["work_item_id"],
                    "text": f"{who} {verb} {r['work_item_id']}"
                            + (f" · {title}" if title else "")})
    return {"head": head, "events": out[: sum(CELEBRATION_CAPS.values())]}


def queue_zero_note() -> dict:
    """Queue zero gets calm and a statement of what the fleet is doing without him.

    Rewarding an empty queue rewards clearing items to feel good, which is the Goodhart failure
    the points doctrine names. So: no celebration here, ever.
    """
    f = fleet()
    working = [a for a in f["agents"] if a["state"] == "working"]
    return {
        "working": len(working),
        "red": len([a for a in f["agents"] if a["state"] == "dead"]),
        "agents": f["agents"],
        "paused": f["paused"],
    }


# =========================================================================================
# V3: THE MULTI-OPTION CARD (task 0165). Two to four drafted options per item, each with its
# counterargument, and choosing one dispatches real work.
#
# Everything below is read and rendering. The only verb an option reaches is `recommend accept`,
# which the Queue's allowlist already held, and it is reached through `web/actions.py`. Storage
# and the argument for it: `queue/schema/0014_queue_item_options.sql`.
# =========================================================================================

#: kind -> what the console's control says and does. THE VERB NAME IS THE CONSOLE'S, NEVER THE
#: PRODUCER'S. C section 1.2 lists a `verb` field per option; storing one would mean a drafting
#: agent names the transition a button runs, and the whole shape of `web/rooms.py` is that a
#: room's verbs come from a frozen set the producer cannot reach. So a producer proposes a KIND
#: and this table is the one place kinds become verbs.
#:
#: `Dispatch it` rather than `Dispatch`: DESIGN-SYSTEM.md section 7.3 amendment b, the name
#: collision with the Dispatch MODE.
#:
#: `sprint` IS NOT A VERB AND HAS NO ACTION. C section 1.5: "a sprint is never spawned from this
#: card; scoping is its own surface with its own demolition step". The control is a link into the
#: Scope room carrying the option's own words, so what comes out the other end is a definition of
#: done the operator demolished -- a scoped brief, not a one-line task.
OPTION_VERB = {
    "agent-does":  {"label": "Dispatch it",         "action": "dispatch_option"},
    "agent-deep":  {"label": "Dispatch it",         "action": "dispatch_option"},
    "agent-helps": {"label": "Start it together",   "action": "dispatch_option"},
    "sprint":      {"label": "Open sprint scoping", "action": None},
}

#: How long one read of the option sets is reused. THIS IS A SINGLE-FLIGHT, NOT A CACHE LAYER
#: (must-not-build 11 is about Redis and memcached, and this is neither): one render of the queue
#: calls `_card` once per item and every one of those calls must not open its own connection. The
#: bound is shorter than the 3s poll, so every poll reads the store fresh; and the one writer that
#: could see its own stale row -- a dispatch, which re-renders immediately -- calls
#: `invalidate_options()` in the same breath, so that window is closed rather than tolerated.
_OPTIONS_TTL_S = 1.0
_options_memo: dict = {"at": None, "sets": {}, "errors": {}}


def invalidate_options() -> None:
    """Drop the single-flight. Called by `web/actions.py` after a dispatch, so the receipt and
    the re-render that follows it read the row the write just made."""
    _options_memo["at"] = None


def _option_sets() -> dict:
    import time as _time
    now = _time.monotonic()
    at = _options_memo["at"]
    if at is None or (now - at) > _OPTIONS_TTL_S:
        _options_memo["sets"] = QO.option_sets()
        _options_memo["errors"] = QO.drafting_errors()
        _options_memo["at"] = now
    return _options_memo


def lane_engine(lane: str, cfg: dict | None = None) -> dict:
    """What will ACTUALLY run a task posted into this lane: engine, model, effort, and who.

    THE FAST-VERSUS-DEEP CHOICE IS THIS FUNCTION AND NOT A STORED STRING. `model`, `effort` and
    `engine` are per-agent config (`~/.brain-runtime/config.json`); `swarm claim` filters
    candidates on `lane`, and `engine/bin/swarm-run` reads `CFG_MODEL` and `CFG_EFFORT` off the
    claiming agent's profile. So an option carries a LANE, and the model and the effort are read
    back from the config the runner obeys. A model name stored beside the option would be a
    second copy of a config value, free to drift from the one that runs, and the card would then
    state a model no run ever used -- the confidently-wrong number this system exists to prevent.

    An honest absence where the fleet has no claimant for the lane: `agents` empty and `why` says
    so. A task posted there is real and it waits, and the card says that rather than implying a
    run that nothing will start.
    """
    try:
        from swarm_engine import config as _cfg
        cfg = cfg if cfg is not None else _cfg.config()
    except Exception as exc:                                            # noqa: BLE001
        return {"agents": [], "engine": None, "model": None, "effort": None,
                "why": f"the fleet config could not be read ({exc.__class__.__name__}), so the "
                       f"model and effort this lane would run under are unknown"}
    want = (lane or "").strip()
    claimants = []
    for a in cfg.get("agents", []):
        merged = _cfg.agent_config(a.get("name", ""), cfg)
        if _cfg.is_planner(merged.get("role", "")):
            continue                       # a planner never claims: D00 contract rule 5
        lanes = merged.get("lanes") or []
        if want and ("*" in lanes or want in lanes):
            claimants.append(merged)
    if not claimants:
        return {"agents": [], "engine": None, "model": None, "effort": None,
                "why": (f"no agent in the fleet claims lane {want!r}, so a task posted there "
                        f"waits until one does" if want else
                        "this option states no lane, so which profile would claim it is unknown")}
    models = sorted({str(c.get("model") or "") for c in claimants})
    efforts = sorted({str(c.get("effort") or "") for c in claimants})
    engines = sorted({str(c.get("engine") or "") for c in claimants})
    one = len(models) == 1 and len(efforts) == 1
    return {
        "agents": [c.get("name") for c in claimants],
        "engine": engines[0] if len(engines) == 1 else " or ".join(engines),
        "model": models[0] if len(models) == 1 else " or ".join(models),
        # An empty string in config means "the engine's own default", and printing it as blank
        # would read as a missing value rather than as the deliberate one it is.
        "effort": (efforts[0] or "engine default") if len(efforts) == 1
                  else " or ".join(e or "engine default" for e in efforts),
        "why": "" if one else
               (f"{len(claimants)} agent profiles claim this lane and they do not agree on a "
                f"model or an effort, so which one runs depends on which claims first"),
    }


def _money(v) -> str | None:
    if v is None:
        return None
    v = float(v)
    return f"~${v:,.2f}" if v < 10 else f"~${v:,.0f}"


def option_card_fields(rows: list) -> dict:
    """One drafted set as the card renders it. Pure: it reads nothing and invents nothing."""
    dispatched = next((r for r in rows if r.get("spawned_work_item")), None)
    opts = []
    for r in rows:
        kind = r["kind"]
        verb = OPTION_VERB.get(kind, {"label": "Open it", "action": None})
        counter = (r.get("counterargument") or "").strip()
        eng = lane_engine(r.get("lane") or "")
        cost = _money(r.get("cost_est"))
        time_est = (r.get("time_est") or "").strip()
        opts.append({
            "n": r["n"], "rid": r["recommendation_id"], "kind": kind,
            "label": r["label"], "who": r["who"], "thinking": r["thinking"],
            "size": r["size"], "lane": r["lane"],
            "cost": cost, "cost_missing": cost is None,
            "time": time_est or None, "time_missing": not time_est,
            "recommended": bool(r["recommended"]),
            "plan": (r.get("plan") or "").strip(),
            # NEVER AN OPTION WITHOUT ITS COUNTERARGUMENT. Where the drafter wrote none, the slot
            # carries the console's shipped finding instead (C section 1.6c) and the verb stays
            # enabled: blocking dispatch here would teach drafting agents to write filler, and
            # filler in that slot is worse than a finding because it reads like an argument.
            "counter": counter or None, "counter_missing": not counter,
            "state": r.get("state"), "drafted_by": r.get("drafted_by") or "",
            "title": r.get("proposed_action") or r["label"],
            "template_id": r.get("template_id"),
            "spawned": r.get("spawned_work_item"), "decided_by": r.get("decided_by"),
            "cost_actual": _money(r.get("cost_actual")), "minutes_actual": r.get("minutes_actual"),
            "verb_label": verb["label"], "action": verb["action"],
            "engine": eng,
            # THE COMMITMENT LINE, C section 1.4 item 3: cost restated on the path to dispatch,
            # because an option that hides its price is a trap and one glance-past is cheap. It
            # names the model and the effort READ FROM CONFIG, so what it promises is what the
            # runner will do.
            "commitment": _commitment(cost, time_est, eng, r),
        })
    # THE DRAFTER SAYS FAST OR DEEP; THE FLEET CONFIG DECIDES. When two options claim different
    # thinking and resolve to the SAME engine, model and effort, the choice the card is offering
    # does not exist -- the operator would pay a different price for the same run. That is exactly
    # the confidently-wrong figure this console is built against, so it is stated ON the card
    # rather than left for him to discover in the run. MEASURED 2026-08-18 on the live fleet:
    # every lane resolved to claude/opus/engine-default, because every profile carries
    # `lanes: ["*"]`, so this finding fires on a real four-option set today.
    seen: dict = {}
    for o in opts:
        e = o["engine"]
        if not e.get("model"):
            continue
        seen.setdefault((e["engine"], e["model"], e["effort"]), set()).add(o["thinking"])
    collapsed = sorted({t for k, v in seen.items() if len(v) > 1 for t in v})
    return {
        "options": opts,
        "options_n": len(opts),
        "options_same_engine": (
            f"{' and '.join(repr(c) for c in collapsed)} resolve to the same run on this fleet "
            f"({', '.join(k[1] + ', effort ' + k[2] for k, v in seen.items() if len(v) > 1)}). "
            f"The lanes these options post into are claimed by the same profiles, so the "
            f"thinking each one claims is the drafter's word and not a difference the runner "
            f"will make." if collapsed else None),
        # The set is resolved once any option has spawned work: the choice was made, and the rail
        # then states what was chosen rather than offering the choice again.
        "option_dispatched": ({"n": dispatched["n"], "task": dispatched["spawned_work_item"],
                               "by": dispatched.get("decided_by"),
                               "label": dispatched["label"]} if dispatched else None),
        "options_no_counterargument": [o["n"] for o in opts if o["counter_missing"]],
    }


def _commitment(cost, time_est, eng, row) -> str:
    who = row.get("drafted_by") or "the drafting agent"
    bits = []
    if cost and time_est:
        bits.append(f"{cost} and {time_est}")
    elif cost:
        bits.append(f"{cost}, no time estimated")
    elif time_est:
        bits.append(f"{time_est}, no cost estimated")
    else:
        bits.append("no cost and no time estimated")
    if eng.get("model"):
        bits.append(f"{eng['engine']} {eng['model']}, effort {eng['effort']}, "
                    f"lane {row.get('lane')} ({', '.join(eng['agents'])})")
    else:
        bits.append(eng["why"])
    bits.append(f"estimates are {who}'s; measured cost and time land beside them after the run")
    return ". ".join(b for b in bits if b) + "."


def option_for(source_type: str, source_id: str, n) -> dict | None:
    """One drafted option, read fresh. The row a dispatch is about, never the rendered card.

    NOT taken off the card the browser posted from, and that is the same argument
    `web/rooms.py::assert_allowed_on` makes for reading `brain.work_item` rather than the card:
    the card is markup, and a dispatch decided from markup is a dispatch decided by whatever the
    page last happened to render. The single-flight is deliberately bypassed here -- one extra
    connection on a write is cheap, and a dispatch reading a row up to a second old is not.
    """
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    for r in (QO.option_sets().get((source_type, str(source_id))) or []):
        if r["n"] == n:
            return r
    return None


# --------------------------------------------------------------------------- intake
#
# ROW 0438 (his ask 7) THROUGH ROW 0440, WHICH BUILT THE OTHER HALF. Lane D1, 2026-08-30.
#
# WHAT THIS IS THE SECOND HALF OF. `swarm intake --source <dir>` takes .md files from a folder
# and lands ONE row in `brain.objective`, state `inbox`, unclassified. Lane C1 supervised the
# workflow runtime, installed `brain-intake-sweep.timer` and proved the chain unattended: a file
# dropped into the folder became a row with nobody at a terminal. Then it measured the hole this
# block closes: **10 of 10 console GET routes rendered zero mention of the row that landed**, and
# `web/app.py` contained the word `objective` zero times. An item could arrive on his store and
# he could not see it.
#
# THE DOOR DOES NOT CLASSIFY AND NEITHER DOES THIS. `0438`'s superseding note is explicit about
# why: *"a path that landed a typed row would have to CLASSIFY spoken English, and a wrong
# classification is the same defect class as a fabricated transcription. It will be believed
# later."* So nothing here scores, ranks, guesses a priority, or decides what an item is about.
# It reads the rows in the order they arrived and prints what the store holds.
#
# THE PROPOSALS ARE READ, NEVER INVENTED. His ask asks intake to show AI-proposed actions for
# approval. The shape `0438` fixes is: an agent reads an objective and raises a
# `brain.recommendation` carrying a rationale AND a counterargument, `requires_human`, executing
# nothing. Measured on live `brain` 2026-08-30: **3 open recommendations, 0 of them naming an
# objective, and 0 recommendations of any state with `subject_type = 'objective'`.** So the
# surface renders the absence WITH that denominator rather than an empty affordance that pretends
# a loop exists. MUST-NOT-BUILD item 2's own rule: an affordance that cannot work is worse than
# one that is absent. The query below is real, so the day an agent raises one, the surface
# renders it without anybody editing this file.


def _intake_ago(when) -> str:
    """`3 h ago`, from a timestamptz. Coarse on purpose: this is arrival, not a stopwatch."""
    if not when:
        return ""
    secs = (_dt.datetime.now(_dt.timezone.utc) - when).total_seconds()
    if secs < 0:
        return "just now"
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{int(secs // 60)} min ago"
    if secs < 172800:
        return f"{int(secs // 3600)} h ago"
    return f"{int(secs // 86400)} d ago"


def _intake_first_line(body: str) -> str:
    """The first line with words in it, with a markdown heading's hashes taken off.

    It is a READ of the file, not a summary of it: no truncation of meaning, no rewording, and
    the whole body is on the surface one expansion away. A generated one-line summary is exactly
    the classification the intake transition refuses at the door.
    """
    for line in (body or "").splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text
    return ""


def _intake_sources() -> tuple[list[dict], str]:
    """The drop folders the sweep actually watches, parsed from the sweep itself.

    READ FROM THE SCRIPT AND NOT RETYPED HERE. A path written twice is a path that will disagree
    with itself; `systemd/brain-intake-sweep` holds the `SOURCES` array and this reads that line.
    If the file is gone or the line is not the shape this understands, it returns the REASON and
    no path, because naming a folder he might drop a file into that nothing sweeps is worse than
    naming none.

    The `.md` count beside each folder is the other end of the denominator: how many files are
    sitting in the door against how many rows came through it.
    """
    import re
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(repo, "systemd", "brain-intake-sweep")
    if not os.path.exists(path):
        return [], f"no sweep script at {path}, so no folder is named here"
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:                                              # noqa: BLE001
        return [], f"the sweep script could not be read: {exc}"
    m = re.search(r"^SOURCES=\((.*)\)\s*$", text, re.M)
    if not m:
        return [], (f"{path} carries no single-line SOURCES=(...) array, so this surface will "
                    f"not guess which folder is swept")
    out = []
    for raw in re.findall(r'"([^"]+)"|\'([^\']+)\'', m.group(1)):
        p = (raw[0] or raw[1]).replace("$REPO", repo).replace("${REPO}", repo)
        try:
            md = len([f for f in os.listdir(p) if f.endswith(".md")])
            exists = True
        except OSError:
            md, exists = 0, False
        out.append({"path": p, "md": md, "exists": exists})
    if not out:
        return [], f"{path} declares a SOURCES array with no folder in it"
    return out, ""


def intake_waiting_count() -> int | None:
    """How many intake items are waiting on the OPERATOR, or `None` if the read failed.

    `state = 'inbox'` WAS THE NARROWEST NUMBER THIS TABLE COULD PRODUCE AND IT WAS STILL TOO WIDE.
    He granted the badge on the condition that it counts what is genuinely waiting on him, and on
    2026-08-31 it read 3 when the answer was 1: two of the three inbox rows were the runtime's own
    daily heartbeat, landing on his board to prove the push path is alive. A badge that is two
    thirds noise on its third day stops being read, which is the incident MUST-NOT-BUILD item 7
    was written against, reached by a different route.

    SO IT COUNTS `brain.objective_waiting` (migration 50), which is `state = 'inbox'` AND a
    resolved origin of `human`. The origin is DECLARED at the intake door by whatever wrote the
    row, never inferred here from what the row is called: a lane refused to hardcode a name match
    into this layer and was right to, and the fix was to give the fact somewhere to live rather
    than to guess it further downstream.

    THE VIEW HOLDS THE PREDICATE, NOT THIS FUNCTION. The badge and `/intake`'s list must not be
    able to disagree about what "waiting" means, and two copies of a WHERE clause is how they
    would come to.

    IT FALLS BACK TO THE OLD COUNT BELOW LEDGER 50 AND KEEPS SERVING. `docs/SCHEMA-TOLERANCE.md`
    rules 1 and 5: a store that has not been given the view yet gets the number it has always had,
    which is correct and merely wide, rather than a 500 on every room in the console. The fallback
    is a fact and not a guess: a store with nowhere to record an origin has no machine-origin rows
    in it to exclude.

    `None` RATHER THAN 0 ON A FAILED READ, and the difference is the whole reason this function
    catches. This count is rendered in the shell, on every room, so an exception here would 500
    the WHOLE console over a table nine other screens do not use -- and the store dropped
    connections twice on this host last night (lane C3). But a failed read is not an empty inbox:
    returning 0 would paint "nothing is waiting on you" over an unknown, which is the fabricated
    number this file's opening rule exists against. `None` renders as a badge that says it does
    not know, and `/intake` says why.
    """
    try:
        with store.read() as s:
            if schema.has_column("objective", "origin"):
                return int(s.scalar("SELECT count(*) FROM brain.objective_waiting"))
            schema.warn_once(
                "intake_waiting_count",
                "this store has no brain.objective.origin (ledger 50), so the intake badge counts "
                "every inbox objective including the runtime's own heartbeat. Apply "
                "migrations/0050_intake_declares_its_origin.sql to narrow it.")
            return int(s.scalar("SELECT count(*) FROM brain.objective WHERE state = 'inbox'"))
    except Exception:                                                   # noqa: BLE001
        return None


def intake() -> dict:
    """Everything the intake room renders: what is waiting, what came through, what proposes.

    Read only. `web/rooms.py` has no `intake` room in `ROOM_VERBS`, so the write door refuses
    this surface before it looks a row up -- the same construction Study and Sessions use, and it
    needed nothing added to get it.
    """
    try:
        with store.read() as s:
            # THE ORIGIN COMES BACK WITH THE ROWS SO THE LIST CAN SAY WHAT THE BADGE COUNTED.
            # Migration 50, row 0438. The heartbeat is still LISTED, and only excluded from the
            # count: it is not hidden, it is labelled. Hiding it would remove the one thing that
            # makes it useful, which is that a human can see at a glance that today's arrived, and
            # `Nothing leaves the queue silently` is the same rule one surface over.
            #
            # Below ledger 50 the column is absent and every row is reported as `human`, which is
            # exactly what the fold would say about a NULL anyway, so the surface reads the same.
            origin_col = (", brain.objective_origin(origin) AS origin_resolved"
                          if schema.has_column("objective", "origin")
                          else ", 'human'::text AS origin_resolved")
            rows = s.query(
                "SELECT id, name, state, body, bytes, source_name, source_signature, "
                "       taken_in_at, accepted_at, intake_format, transcription_status"
                + origin_col +
                "  FROM brain.objective WHERE state = 'inbox' "
                " ORDER BY taken_in_at DESC, id DESC")
            total = int(s.scalar("SELECT count(*) FROM brain.objective"))
            accepted = int(s.scalar(
                "SELECT count(*) FROM brain.objective WHERE state = 'accepted'"))
            recent = s.query(
                "SELECT name, accepted_at FROM brain.objective WHERE state = 'accepted' "
                " ORDER BY accepted_at DESC NULLS LAST LIMIT 3")
            # THE PROPOSALS, AND THE TWO DENOMINATORS THAT MAKE THEIR ABSENCE A MEASUREMENT.
            # `open_recs` is every open recommendation in the store; `objective_recs` is every
            # recommendation of any state that names an objective at all. The sentence on the
            # surface is "none of the N open ones names an objective", which is a fact that
            # stops being true on its own the moment an agent raises one.
            #
            # TWO SUBJECT TYPES, AND THE SECOND ONE IS WHY THIS SURFACE CAN EVER SHOW ANYTHING.
            # Measured 2026-08-30 by watching the refusal happen:
            # `store.apply("recommend", subject_type="objective", ...)` raises
            # `QueueError: unknown subject type 'objective'`. The registered verb accepts only
            # work_item, session, event and entity (`queue/human_queue/transitions.py::recommend`),
            # so the loop `0438` describes -- an agent reads an intake item and raises a
            # recommendation about it -- CANNOT BE RAISED THROUGH THE VERB TODAY. Reading only
            # `subject_type = 'objective'` would therefore be a query that can never return a row
            # until an engine lane widens that tuple, which is not this lane's file.
            #
            # So `entity` is read too, and matched ON THE NAME ONLY. The name is unique on
            # `brain.objective` and is a distinctive string; the numeric id is not, and an entity
            # recommendation whose subject_id is "4" is about some other 4. Matching the id under
            # the `entity` type is exactly how this surface would invent a proposal that was never
            # about an intake item, so it does not.
            props = s.query(
                "SELECT id, subject_type, subject_id, text, rationale, requires_human, created_at "
                "  FROM brain.recommendation "
                " WHERE state = 'open' AND subject_type IN ('objective', 'entity') ORDER BY id")
            open_recs = int(s.scalar(
                "SELECT count(*) FROM brain.recommendation WHERE state = 'open'"))
            objective_recs = int(s.scalar(
                "SELECT count(*) FROM brain.recommendation WHERE subject_type = 'objective'"))
    except Exception as exc:                                            # noqa: BLE001
        # THE ROOM SAYS THE READ FAILED. It does not render an empty inbox, for the reason
        # `intake_waiting_count` gives: zero and unknown are different answers and only one of
        # them is true.
        return {"read": False, "why": str(exc), "waiting": [], "waiting_n": 0,
                # The two counts row 0438 added, present on BOTH arms so a template cannot reach
                # for a key that exists only when the read worked. The route already refuses to
                # seed the badge from this arm (`if data["read"]`), so these are shape and not a
                # claim: a failed read reports nothing waiting because it reports nothing at all.
                "inbox_n": 0, "machine_n": 0,
                "taken_in_total": 0, "accepted_n": 0, "recent_accepted": [],
                "proposals_n": 0, "open_recs": 0, "objective_recs": 0,
                "sources": [], "sources_why": ""}

    # Keyed by what the recommendation names. An `objective`-typed row may name either the id or
    # the name; an `entity`-typed one is taken on the NAME only, for the reason in the query's
    # comment. `by_name` and `by_id` are kept apart so that rule survives a later edit.
    by_name: dict[str, list] = {}
    by_id: dict[str, list] = {}
    for p in props:
        card = {"id": p["id"], "text": p["text"], "rationale": p["rationale"],
                "requires_human": p["requires_human"], "subject_type": p["subject_type"],
                "created": p["created_at"].strftime("%m-%d %H:%MZ") if p["created_at"] else ""}
        by_name.setdefault(str(p["subject_id"]), []).append(card)
        if p["subject_type"] == "objective":
            by_id.setdefault(str(p["subject_id"]), []).append(card)

    waiting = []
    for r in rows:
        body = r["body"] or ""
        # An `objective`-typed recommendation may name its subject by id or by name and both are
        # legitimate: the id is stable and the name is what a human types. An `entity`-typed one
        # is taken on the name alone. De-duplicated by recommendation id, because a row typed
        # `objective` whose subject_id happens to equal the name is in both maps.
        mine, seen = [], set()
        for card in by_id.get(str(r["id"]), []) + by_name.get(str(r["name"]), []):
            if card["id"] not in seen:
                seen.add(card["id"])
                mine.append(card)
        waiting.append({
            "id": r["id"],
            "name": r["name"],
            "bytes": r["bytes"],
            "lines": len(body.splitlines()),
            "source_name": r["source_name"],
            "source_signature": r["source_signature"],
            "arrived": r["taken_in_at"].strftime("%m-%d %H:%MZ") if r["taken_in_at"] else "",
            "ago": _intake_ago(r["taken_in_at"]),
            "format": r["intake_format"],
            "transcription": r["transcription_status"],
            "first_line": _intake_first_line(body),
            "body": body,
            "proposals": mine,
            # `machine` is the badge's own reason for not counting this row, carried onto the card
            # so the list can say so where he is looking rather than in a footnote.
            "origin": r["origin_resolved"],
        })

    sources, sources_why = _intake_sources()
    # THE BADGE'S NUMBER, AND IT IS NOT `len(waiting)` ANY MORE. Row 0438's condition: the count is
    # what is genuinely waiting on a human. The LIST is still everything in the inbox, because the
    # heartbeat's presence is worth seeing and its absence is the signal that the push path died.
    # Two numbers, both reported, neither standing in for the other.
    human_n = len([w for w in waiting if w["origin"] == "human"])
    return {
        "read": True, "why": "",
        "waiting": waiting, "waiting_n": human_n,
        "inbox_n": len(waiting), "machine_n": len(waiting) - human_n,
        "taken_in_total": total, "accepted_n": accepted,
        "recent_accepted": [{"name": a["name"],
                             "at": a["accepted_at"].strftime("%m-%d %H:%MZ")
                             if a["accepted_at"] else ""} for a in recent],
        "proposals_n": sum(len(w["proposals"]) for w in waiting),
        "open_recs": open_recs, "objective_recs": objective_recs,
        "sources": sources, "sources_why": sources_why,
    }

# ---------------------------------------------------------------------------------------------
# R03 / R04 READ PORTS. Task CAP07, 2026-09-06.
#
# The two surfaces under `web/blueprints/routines/` and `web/views/` take a read port rather than
# importing this module, so they can be rendered in a test with a stub and so this file stays the
# only place that knows what the store holds. These two functions are that seam.
#
# THEY REPORT AN ABSENCE RATHER THAN AN EMPTY LIST, and that is the whole point of them existing
# before R01 lands. A store with no routine tables and a user with no routines are different
# facts, and `[]` renders identically for both: "you have no routines yet", painted over "this
# console cannot see routines at all". That is the fabricated-number failure this file's opening
# rule is written against, one type up: a fabricated ABSENCE. `capability_note()` returns the
# sentence the surface prints instead, and `docs/SCHEMA-TOLERANCE.md` rules 1 and 5 are the
# pattern: probe, warn once, keep serving, say what is missing.
#
# THE TABLE NAMES BELOW ARE PROBES, NOT AN ALLOCATION. `migrations/**` and the schema ledger
# belong to R01 (T04 holds that claim), so this lane does not name a table into existence. The
# probe is tolerant either way: whatever R01 calls them, a store that does not have them yet gets
# an honest sentence, and when R01 lands, the queries go in here and nothing above this line or in
# either template changes.


ROUTINE_TABLE = "routine"
ATTENTION_TABLE = "work_item"


class _StoreRoutinePort:
    """`RoutineReadPort` over this store. Today it can only report that the store has no routines."""

    def capability_note(self):
        try:
            if schema.has_column(ROUTINE_TABLE, "id"):
                return None
            schema.warn_once(
                "routine_port",
                "this store has no brain.%s table, so the Routines surface can show nothing. "
                "It is not empty; it is not yet built. R01 owns that migration." % ROUTINE_TABLE)
            return ("This console cannot see routines yet: the store it reads has nowhere to keep "
                    "them. This is not an empty list, and nothing you created has been lost.")
        except Exception:                                               # noqa: BLE001
            return ("This console could not reach its store just now, so it does not know whether "
                    "you have routines. It is not saying you have none.")

    def list_routines(self):
        return []

    def get_routine(self, routine_id):
        return None

    def history(self, routine_id):
        return []

    def host_for(self, routine_id):
        from web.blueprints.routines import HostStatus
        return HostStatus(
            name="unknown", kind="not recorded", reachable=False,
            detail="No host is recorded for this routine, which is not the same as a host that is down.")


class _StoreAttentionPort:
    """`AttentionReadPort` over this store, with the same absence rule."""
    evidence_label = 'Runtime store evidence'
    # Supplied observation, not a health probe. Source: Attention Design's
    # EVIDENCE-2026-09-12.md section 4; coordinator filed C5 at 17:58Z.
    # Keep the historical time until a separately authorized measurement replaces it.
    intake_observation = {
        'observed_at': '2026-09-12T16:33:22Z',
        'when': '12 Sep 2026, 16:33 UTC',
        'source': 'Attention Design',
        'summary': 'Mail, meetings and Slack intake stopped at the last check',
        'detail': 'Mail, meetings and Slack had landed no new items since 3 September. '
                  'Mail was failing its intake-door check; the meeting and Slack sweep '
                  'timer was not installed. Drop-folder intake was still running.',
    }

    def capability_note(self):
        try:
            if schema.has_column(ATTENTION_TABLE, "id"):
                return None
            schema.warn_once(
                "attention_port",
                "this store has no brain.%s table, so the Attention surface can show nothing. "
                "R01 owns that migration." % ATTENTION_TABLE)
            return ("This console cannot see your attention queue yet: the store it reads has "
                    "nowhere to keep it. An empty screen here would be a claim it is not entitled "
                    "to make.")
        except Exception:                                               # noqa: BLE001
            return ("This console could not reach its store just now, so it does not know what is "
                    "waiting on you. It is not saying nothing is.")

    def queue(self, *, offset=0, limit=None, filter_key="queue",
              sort=None, descending=False, _item_id=None):
        """Filter, sort and page the canonical order using the view-owned axis.

        The full ranking is still paid here; bounding HTML does not bound that
        cost. No guessed LIMIT may make a later item unreachable. Counts name the
        gate, suppressed rows and the page separately. The view owner consumes
        this additive page shape and passes route arguments before the slice.
        """
        from web.views import QueueView, QueueItem, Provenance, ProvenanceStep
        from web.views.honesty import Freshness, Impact
        from web.views import signals as VS
        from web.views.queue import FILTERS, AdmissionRefusal, bounded_title
        from web.attention_options import for_row as native_options_for

        if limit is None:
            limit = Q.DEFAULT_WINDOW
        if (type(offset) is not int or offset < 0 or type(limit) is not int or limit < 0):
            raise ValueError("offset and limit must be nonnegative integers")
        from web.views.navigation import FILTERS as NAV_FILTERS, canonical_filter
        filter_key = canonical_filter(filter_key)
        if filter_key not in NAV_FILTERS:
            raise ValueError("unknown Attention filter")
        if sort not in (None, "title", "kind", "freshness", "tier"):
            raise ValueError("unknown Attention sort")

        now = _dt.datetime.now(_dt.timezone.utc)
        blocked = deferred = 0
        if _item_id is None:
            # sys.maxsize is an uncapped inventory request to the ONE ranker, not
            # a second rendering constant such as the old 10,000-row ceiling.
            import sys
            ranked = Q.queue(window=sys.maxsize)
            raw = [row for tier in HQT.TIERS for row in ranked["tiers"][tier]["items"]]
            now = ranked["now"]
            blocked = ranked["totals"]["blocked"]
            deferred = ranked["totals"]["deferred"]
            # The ranker's "open" excludes suppression; this contract's outer
            # population includes those two disjoint groups.
            total_open = ranked["totals"]["open"] + blocked + deferred
        else:
            # Inspector lookup is keyed, never a ranked pass through all items.
            source, separator, sid = str(_item_id).partition(":")
            # `objective` joins the allowlist WITH the fifth arm of brain.queue_open and never
            # before it: on a store that has not had the arm's migration, the keyed SELECT below
            # simply returns no row and the inspector refuses exactly as it does today. The
            # allowlist stays a closed set -- an unknown source is still refused before any SQL --
            # because it is the one place a crafted item_id is stopped from reaching a query.
            if (not separator or not sid
                    or source not in ("work_item", "question", "recommendation", "objective")):
                return QueueView(items=(), checked_at=now, window=0, refused_of=0,
                                 total_open=0, blocked=0, deferred=0, admitted=0, refused=0,
                                 filtered_total=0, offset=0, limit=limit,
                                 total_by_tier={t: 0 for t in HQT.TIERS})
            with store.read("runtime") as s:
                raw = s.query("""SELECT * FROM brain.queue_open
                                  WHERE source_type = %s AND source_id = %s""", (source, sid))
                if raw:
                    overlays = s.query("""SELECT * FROM brain.queue_item
                                          WHERE source_type = %s AND source_id = %s""", (source, sid))
                    defaults = s.query("""SELECT default_text FROM brain.queue_pending_default
                                          WHERE question_id = %s""", (sid,)) if source == "question" else []
                    raw[0].update(HQT.tier_inputs(raw[0], overlays[0] if overlays else {},
                                  defaults[0]["default_text"] if defaults else None))
                    tier = HQT.tier_of(raw[0], Q.signal_level("reversibility", raw[0].get("reversibility")))
                    raw[0].update(tier=tier["tier"], tier_reason=tier["reason"])
            total_open = len(raw)

        metadata = {}
        if raw:
            # One batch for the gate and original signal vocabulary. queue_open's
            # low/medium/high folds are ordering inputs, never producer claims.
            with store.read("runtime") as s:
                rows = s.query("""WITH keys AS (
                      SELECT * FROM unnest(%s::text[], %s::text[], %s::text[])
                           AS k(source_type, source_id, work_item_id)),
                    evidence AS (
                    SELECT k.source_type, k.source_id,
                           w.stakes, w.reversibility, w.urgency, w.effort,
                           w.state AS work_state,
                           r.text AS recommendation_text,
                           r.rationale AS recommendation_counterargument,
                           w.confidence, w.charter_alignment,
                           EXISTS (SELECT 1 FROM brain.queue_item_option o
                                    WHERE o.source_type = k.source_type AND o.source_id = k.source_id)
                             AS has_option,
                           (k.source_type = 'work_item' AND w.finished_at IS NOT NULL
                            AND btrim(w.result) <> '') AS has_completed_result,
                           (k.source_type = 'question' AND qu.answer IS NOT NULL) AS has_answer,
                           (k.source_type = 'recommendation' AND r.state IN ('accepted','rejected'))
                             AS has_recommendation_decision,
                           -- THE TYPED IDENTITY OF AN OBJECTIVE, and the reason it is read at all:
                           -- an objective carries no completed result, no answer and no
                           -- recommendation decision, so `has_option` is the ONLY term that can
                           -- admit one, and `has_option` is keyed on (source_type, source_id)
                           -- alone. A stray option row naming an objective that is gone would
                           -- therefore admit a card with nothing behind it. This says the row
                           -- exists, read from brain.objective itself, and the gate stays
                           -- fail-closed.
                           (k.source_type = 'objective' AND ob.id IS NOT NULL) AS objective_exists,
                           r.state AS recommendation_state
                      FROM keys k
                      LEFT JOIN brain.work_item w ON w.id = k.work_item_id
                      LEFT JOIN brain.question qu ON k.source_type = 'question' AND qu.id = k.source_id
                      LEFT JOIN brain.recommendation r ON k.source_type = 'recommendation'
                                                       AND r.id::text = k.source_id
                      -- `ob.id::text = k.source_id` and never `k.source_id::bigint`: the keys
                      -- carry question ids like `q0011`, and casting the KEY would raise on them.
                      -- Same shape as the recommendation join above, for the same reason.
                      LEFT JOIN brain.objective ob ON k.source_type = 'objective'
                                                   AND ob.id::text = k.source_id)
                    SELECT *, ((has_option AND (source_type <> 'objective' OR objective_exists))
                               OR has_completed_result OR has_answer
                               OR has_recommendation_decision) AS decidable
                      FROM evidence""",
                    ([r["source_type"] for r in raw], [str(r["source_id"]) for r in raw],
                     [r.get("work_item_id") for r in raw]))
            metadata = {(r["source_type"], r["source_id"]): r for r in rows}

        # Andrew, 2026-09-12: represent existing guarded task and proposal acts.
        # Drafted options retain the original gate; native options use typed
        # source facts and the same guard as the write door. No options are stored.
        native_eligible = {(r['source_type'], str(r['source_id'])) for r in raw}
        if _item_id is not None and any(r['source_type'] in ('work_item', 'recommendation') for r in raw):
            # A direct inspector/POST must not restore an action suppressed by
            # dependencies or a human defer. Native eligibility pays for the
            # canonical ranker here too; the displayed inspector position stays unset.
            current = Q.queue(window=__import__('sys').maxsize)
            native_eligible = {(r['source_type'], str(r['source_id']))
                               for tier in HQT.TIERS for r in current['tiers'][tier]['items']}
        native = {(r['source_type'], str(r['source_id'])):
                  (native_options_for(r, metadata.get((r['source_type'], str(r['source_id'])), {}),
                                      operator=OPERATOR)
                   if (r['source_type'], str(r['source_id'])) in native_eligible else ()) for r in raw}

        def kind(row):
            return KIND_BY_ARM.get((row["source_type"], row.get("primary_verb")),
                                   KIND_BY_ARM.get((row["source_type"], None), "review"))

        admitted = [(position, row) for position, row in enumerate(raw, 1)
                    if (metadata.get((row["source_type"], str(row["source_id"])), {}).get("decidable")
                        or native[(row['source_type'], str(row['source_id']))])]
        refused = len(raw) - len(admitted)
        admitted_keys = {(r['source_type'], str(r['source_id'])) for _, r in admitted}
        refusal_items = []
        for row in raw:
            key = (row['source_type'], str(row['source_id']))
            if key in admitted_keys:
                continue
            objective = key[0] == 'objective'
            facts = metadata.get(key)
            needs_preparation = bool(objective and facts and facts.get('objective_exists') and not facts.get('has_option'))
            if not facts:
                reason = 'Admission evidence could not be read for this row. Refresh its current source before deciding.'
            elif objective and not facts.get('objective_exists'):
                reason = 'The original objective is no longer available in this read. Refresh Attention to reconcile its current state.'
            elif needs_preparation:
                reason = ('No action or judgment option has been prepared for this objective. '
                          'The admission rule requires one; the preparation connection is missing.')
            else:
                reason = 'No currently available action or review evidence is represented for this row.'
            from urllib.parse import quote
            source_url = {'work_item': '/task/', 'question': '/question/', 'recommendation': '/rec/',
                          'objective': '/intake#objective-'}.get(key[0])
            source_url = source_url + quote(key[1], safe='') if source_url else None
            if objective and not (facts and facts.get('objective_exists')):
                source_url = None
            refusal_items.append(AdmissionRefusal(
                item_id=key[0] + ':' + key[1], title=row['title'],
                reason=reason, needs_preparation=needs_preparation, source_url=source_url))
        items = []
        for position, row in admitted:
            key = (row["source_type"], str(row["source_id"]))
            facts = metadata[key]
            declared = {}
            for name, vocabulary in (("stakes", VS.STAKES), ("reversibility", VS.REVERSIBILITY),
                                     ("urgency", VS.URGENCY), ("effort", VS.EFFORT),
                                     ("charter_alignment", VS.ALIGNMENT)):
                value = facts.get(name)
                if value in vocabulary:
                    declared[name] = value
            confidence = facts.get("confidence")
            # The legacy column carries low/medium/high text, not a probability.
            # Never invent a numeric confidence by translating those words.
            if type(confidence) in (int, float) and 0 <= confidence <= 1:
                declared["confidence"] = confidence
            signal = VS.Signals.from_contract(declared)
            at = row.get("surfaced_at") or row.get("created")
            # The timestamp is a stored event, not the time this process happened
            # to read it. A missing event timestamp cannot become invented evidence.
            if at is None:
                raise RuntimeError("Attention row has no recorded creation or surfacing timestamp")
            # Admission and preparation tier are different facts. An option can
            # admit an item even when the preparation overlay is absent.
            reasons = []
            if native[key]:
                reasons.append('an existing action: ' + ' or '.join(o.label for o in native[key]))
            if facts.get("has_option"):
                reasons.append("an option for this item")
            if facts.get("has_completed_result"):
                reasons.append("a completed work result")
            if facts.get("has_answer"):
                reasons.append("an answer to this question")
            if facts.get("has_recommendation_decision"):
                reasons.append("an accepted recommendation" if facts["recommendation_state"] == "accepted"
                               else "a rejected recommendation")
            why = ("Included because the store records " + " and ".join(reasons) + ". "
                   "The producer's reason for requesting attention is not available here.")
            if _item_id is not None:
                why += " Working position is not recalculated in this inspector."
            # F-01 / P3-03: a recommendation's row title is the proposal's bounded form, decided in
            # the view layer (`web/views/queue.py::bounded_title`); the whole text rides `proposal`.
            proposal = (row["title"] or "") if key[0] == "recommendation" else ""
            items.append(QueueItem(
                item_id=key[0] + ":" + key[1],
                title=bounded_title(proposal) if proposal else row["title"], kind=kind(row),
                native_options=native[key], proposal=proposal,
                counterargument=facts.get('recommendation_counterargument') or '',
                store_tier=row["tier"], rank=position if _item_id is None else 0, why=why,
                impact=Impact.unknown("the store records no measured amount with a unit and basis"),
                freshness=Freshness.never_read("no source read interval is recorded on this queue row"),
                provenance=Provenance(source_label=row.get("source_lane") or key[0], zone="UTC",
                    reference=key[0] + ":" + key[1], steps=(ProvenanceStep(
                        at=at, what="Item recorded in the runtime store",
                        by=row.get("producer") or "producer not recorded"),)), signals=signal))
        filtered = [item for item in items if FILTERS[filter_key](item)]
        if sort:
            from web.views.queue import SORTS
            filtered.sort(key=SORTS[sort], reverse=descending)
        # A queue may shrink between following a next link and this read. Clamp
        # stale offsets to the end; never return negative hidden counts.
        offset = min(offset, len(filtered))
        shown = filtered[offset:] if limit == 0 else filtered[offset:offset + limit]
        return QueueView(items=tuple(shown), checked_at=now, reordered=bool(sort), sort_key=sort,
                         descending=bool(descending), window=0, refused_of=len(raw),
                         total_open=total_open, blocked=blocked, deferred=deferred, admitted=len(admitted),
                         refused=refused, filtered_total=len(filtered), offset=offset, limit=limit,
                         refusal_items=tuple(refusal_items),
                         total_by_tier={t: sum(item.store_tier == t for item in filtered) for t in HQT.TIERS})

    def item(self, item_id):
        page = self.queue(_item_id=item_id, limit=1)
        return page.items[0] if page.items else None

    def refusal(self, item_id):
        # Recovery may explain a refusal, but never bypasses admission or suppression.
        return next((r for r in self.queue(limit=0).refusal_items if r.item_id == item_id), None)


def routine_port():
    return _StoreRoutinePort()


def attention_port():
    return _StoreAttentionPort()


# --------------------------------------------------------------- identity, term-5
#
# NEW FUNCTIONS ONLY, at the end of the file, in the one block 2026-09-09-IOS-term-5 owns.
# `web/model.py` is shared with 2026-09-09-IOS-term-4, which owns the query and window paths
# above; nothing above this comment is edited by term-5, and the integrator's gate refuses a
# merge where both seats touched one function. Announced to term-4 on the bus before this landed.
#
# Three reads, no writes, each tolerant of a store below ledger 69 per docs/SCHEMA-TOLERANCE.md
# rule 1: the store a console opens is whichever database BRAIN_PG_DB names, and on 2026-08-19
# eleven of 130 were at the tip. Absence returns an honest empty with a sentence, never a claim.
#
# What each answers, and the ruling it restates (ARCHITECTURE-2026-09-09, closed):
#   identity_view()          who this process IS, per the database (IDENTITY-POLICY rule A2), and
#                            which declared workspaces that human reaches. Both halves of
#                            "identity is GitHub AND Postgres" in one dict a surface can render.
#   workspaces_visible()     per-user visibility DERIVED from repo access (ruling A), which is
#                            brain.workspaces_visible_to() over the newest fresh receipts.
#   workspace_access(ws)     the AND for one workspace, brain.human_reaches().


def _identity_store_state() -> tuple[bool, str | None]:
    """(present, note). Present means migrations 69 and 70 are on the store this process reads."""
    try:
        if schema.has_relation("workspace") and schema.has_relation("repo_access_receipt"):
            return True, None
        schema.warn_once(
            "identity_term5",
            "this store has no brain.workspace or brain.repo_access_receipt, so per-user "
            "visibility cannot be derived here. Migrations 69 and 70 own that schema.")
        return False, ("This console cannot say which workspaces you reach: the store it reads "
                       "has no workspace declarations and no repo-host receipts yet. That is not "
                       "an empty list of workspaces.")
    except Exception:                                                   # noqa: BLE001
        return False, ("This console could not reach its store just now, so it does not know "
                       "which workspaces you reach. It is not saying none.")


def workspaces_visible(human: str | None = None) -> dict:
    """The declared workspaces `human` reaches, per the newest fresh repo-host receipt.

    Derived, never maintained: this is brain.workspaces_visible_to(), which composes
    brain.human_reaches(). With one workspace and one human it returns one or none, and this
    function does not know which case it is in. `count` sits beside `workspaces` so a surface
    prints the denominator rather than an adjective.
    """
    who = human or store.human_slug(OPERATOR)
    present, note = _identity_store_state()
    out = {"human": who, "workspaces": [], "count": 0, "declared": 0, "note": note}
    if not present:
        return out
    try:
        with store.read("runtime") as s:
            rows = s.query("SELECT * FROM brain.workspaces_visible_to(%s) AS w(workspace)", (who,))
            declared = s.scalar("SELECT count(*) FROM brain.workspace")
        out["workspaces"] = [r["workspace"] for r in rows]
        out["count"] = len(out["workspaces"])
        out["declared"] = int(declared or 0)
    except Exception as exc:                                            # noqa: BLE001
        out["note"] = f"could not read workspace reach: {exc.__class__.__name__}: {exc}"
    return out


def workspace_access(workspace: str, human: str | None = None) -> dict:
    """The AND for one workspace: does `human` reach it, per Postgres AND the repo host.

    Returns every term separately (brain.human_reach_report) so a refusal can say WHICH half
    failed, which is what the operator needs when a colleague was removed from GitHub but not
    from Postgres. `reach` is the conjunction and the only key a gate should decide on.
    """
    who = human or store.human_slug(OPERATOR)
    present, note = _identity_store_state()
    out = {"human": who, "workspace": workspace, "reach": False, "report": None, "note": note}
    if not present:
        return out
    try:
        has_set_binding = schema.has_column("repo_access_receipt", "repo_set")
        statement = (
            "SELECT r.*, brain.repo_access_receipt_covers_current_set(r.receipt_seq) "
            "AS repo_set_current FROM brain.human_reach_report(%s, %s) r"
            if has_set_binding else
            "SELECT * FROM brain.human_reach_report(%s, %s)"
        )
        with store.read("runtime") as s:
            rep = s.one(statement, (who, workspace))
        out["report"] = rep
        out["reach"] = bool(rep and rep.get("reach"))
        if rep and rep.get("receipt_seq") is not None and rep.get("repo_set_current") is False:
            out["note"] = (
                "The latest repo-host receipt has no recorded repo set or names a different "
                "declared set. Obtain a fresh reading for the current set."
            )
    except Exception as exc:                                            # noqa: BLE001
        out["note"] = f"could not read workspace reach: {exc.__class__.__name__}: {exc}"
    return out


def identity_view() -> dict:
    """Who this process is, per the database, and what it reaches. Rule A2's read plus ruling A's.

    `store.whoami()` is the Postgres half: which login this process holds and whether the store
    agrees about who that is. `workspaces_visible()` is the GitHub half as the store last heard
    it. Neither is asserted from configuration; a surface renders what comes back.
    """
    me = store.whoami()
    who = me.get("human") if me.get("reachable") and me.get("agrees") else None
    vis = workspaces_visible(who) if who else {"human": None, "workspaces": [], "count": 0,
                                               "declared": 0, "note": None}
    return {"whoami": me, "human": who, "workspaces": vis}
