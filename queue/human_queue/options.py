"""Drafted options on a queue item: two to four, each with its counterargument.

The operator asked for multiple choice per task -- spin up an AI to do it, spin up an AI to help,
a fast model or a deeper one with a bigger prompt, or scope it out as a full swarm sprint -- and
for an AI to draft them rather than a human writing them. This module is the producer half of
that: one verb that writes a whole option set in one transaction, and one read the console
renders from.

================================================================ THE ONE PROPERTY THIS FILE KEEPS

**An option that drafts itself is an agent proposing its own work, and a drafting verb is the
natural place for that to become an agent EXECUTING its own work.** It does not become one here,
and the reason is structural rather than careful: this file writes proposals and has no path to
executed work at all. Every option is a `brain.recommendation`, and the only thing in the runtime
that turns a recommendation into a `work_item` is `recommend accept`, which since task 0290
refuses any connection the database does not know as a human -- `brain.current_human()`, which is
NULL for `brain_runtime`, the login every agent surface connects as. It also refuses an empty
decider, a decider registered in `brain.agent`, and any process running under the fleet runner,
and `brain.recommendation_human_decider` refuses all four again at the table. The first of the
four is the gate; the `brain.agent` test used to be, and it failed open by construction -- it
refuses the names that ARE agents, so every name that is not one passed.

So there is no second route, and `queue/tests/test_no_self_execution.py` keeps covering the only
one. `web/tests/test_option_dispatch.py::test_a_drafted_option_cannot_dispatch_without_a_human`
is this lane's addition to that: it proves the option layer reaches executed work through that
verb and through nothing else.

================================================================ WHY AN OPTION IS A RECOMMENDATION

`brain.recommendation` already holds a proposal, its rationale, its template, the decider, the
decision time and the work it spawned, under a trigger that will not let it be born accepted. A
parallel `option` table with its own accept path would have every one of those columns again and
none of the guards. So the recommendation IS the option; `brain.queue_item_option` carries the
facets a recommendation has nowhere to put (kind, who, thinking, size, cost, time, lane,
recommended) and joins on `recommendation_id`.

`queue/schema/0014_queue_item_options.sql` states the storage argument in full.
"""

from __future__ import annotations

import argparse
import json
import sys

import store

from .transitions import (QueueError, _overlay, _source, _work_item_of, norm_task_id,
                          recommend as _recommend)

#: The four kinds the operator named, in his own order: do it, help me do it, think harder about
#: it, scope it as a sprint. The CHECK on the column is the same list. Extensible by migration,
#: never by a producer passing a new string.
KINDS = ("agent-does", "agent-helps", "agent-deep", "sprint")

#: Two is the floor and four is the ceiling. "Sometimes 2, sometimes 4" is the operator's own
#: range. ONE option is not a choice -- it is the singular `recommended_option` the console has
#: rendered since D7, and the card falls back to exactly that (C section 1.6a), so drafting a set
#: of one would build the multi-option component to show a single-option card. FIVE is a form.
MIN_OPTIONS, MAX_OPTIONS = 2, 4


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


@store.transition("queue draft options")
def queue_draft_options(ctx, *, source_type, source_id, options=None, error="", drafted_by="",
                        by=""):
    """Draft a whole option set onto one queue item. All of it, or none of it.

    ONE TRANSACTION FOR THE WHOLE SET, and that is not tidiness. A half-written set is a card
    showing two options where the drafter produced four, and the operator has no way to tell a
    short set from a truncated one -- he would be choosing between the options that happened to
    commit. `store.apply` wraps this whole function in one transaction, so the set lands complete
    or leaves no trace.

    **This verb is on no room's allowlist and the console never calls it.** Drafting is a producer
    act, like `queue classify`; the console's only verb on an option is `recommend accept`, which
    it already held. That separation is the reason the design system's "zero new transitions" rule
    survives a lane that adds one: the rule is about what a SURFACE may call.

    A SECOND DRAFT OVER A LIVE SET IS REFUSED rather than merged or overwritten. The rows carry
    the operator's pending decision; silently replacing them would change what he is choosing
    between while he reads it, and `queue_item_option`'s UNIQUE (source, n) would otherwise decide
    which half of two drafts he sees by insertion order.
    """
    st, sid = str(source_type), str(source_id)
    row = _source(ctx, st, sid)
    _overlay(ctx, st, sid)

    # THE FAILED DRAFT. C section 1.6b is a normative card state and a state with nowhere to be
    # written is decoration; a drafter whose attempt died says so here, in its own words, and the
    # card renders that rather than a generic sentence. `options_drafted_at` is what separates
    # "nobody tried" from "somebody tried and could not", and the operator is owed the difference.
    if error:
        if options:
            raise QueueError(
                "a draft cannot both fail and produce options. Record one or the other: a card "
                "showing a failure notice above a set of options tells the operator nothing "
                "about which of the two he is looking at.")
        ctx.execute(
            "UPDATE brain.queue_item SET options_error = %s, options_drafted_at = now() "
            "WHERE source_type = %s AND source_id = %s", (_clean(error), st, sid))
        return {"source_type": st, "source_id": sid, "options": [],
                "without_counterargument": [], "error": _clean(error)}

    if not isinstance(options, (list, tuple)):
        raise QueueError("options must be a list of option dicts")
    if not (MIN_OPTIONS <= len(options) <= MAX_OPTIONS):
        raise QueueError(
            f"{len(options)} option(s) drafted for {sid}. The operator asked for "
            f"{MIN_OPTIONS} to {MAX_OPTIONS}. One option is not a choice -- it is the singular "
            f"`recommended_option` `queue classify` already writes, and the card renders that "
            f"without any of this. More than {MAX_OPTIONS} is a form, not a decision.")

    live = ctx.execute(
        """SELECT o.n, o.recommendation_id, r.state
             FROM brain.queue_item_option o
             JOIN brain.recommendation r ON r.id = o.recommendation_id
            WHERE o.source_type = %s AND o.source_id = %s""", (st, sid))
    if live:
        open_now = [str(o["n"]) for o in live if o["state"] == "open"]
        raise QueueError(
            f"{sid} already carries {len(live)} drafted option(s)"
            + (f", {len(open_now)} of them still open (n={', '.join(open_now)})" if open_now
               else ", all of them already decided")
            + ". A second draft is refused rather than merged: these rows are what the operator "
              "is choosing between, and replacing them under him changes the question while he "
              "reads it. Decide or reject the open ones first (`queue reject <r-id> --reason ...`)"
              ", then draft again.", code=5)

    recommended = [i for i, o in enumerate(options) if o.get("recommended")]
    if len(recommended) > 1:
        raise QueueError(
            f"{len(recommended)} options are marked recommended. At most one may be: a set with "
            f"two recommendations has recommended nothing, and the word is the only thing on the "
            f"rail that is not symmetric across the rows.")

    # The subject the proposals are ABOUT, and the row the dispatched task inherits its hard flags
    # and its workdir from. `recommend accept` passes it to `post` as the parent, so `external`
    # and `canon_touching` inherit by OR exactly as they do for any other child -- which is the
    # same argument `--parent` carries on the bus, and the reason an option on a flagged item
    # cannot produce an unflagged task.
    subject = norm_task_id(str(_work_item_of(st, sid, row) or ""))

    written = []
    for i, o in enumerate(options, start=1):
        kind = _clean(o.get("kind"))
        if kind not in KINDS:
            raise QueueError(f"option {i}: {kind!r} is not a kind this queue drafts. "
                             f"One of: {', '.join(KINDS)}.")
        plan = _clean(o.get("plan"))
        if not plan:
            raise QueueError(f"option {i} states no plan. An option with no plan is a label.")
        title = _clean(o.get("title")) or _clean(o.get("label"))
        if not title:
            raise QueueError(f"option {i} states no title, so the task it would post has no name.")
        # THE COUNTERARGUMENT IS NOT REFUSED WHEN IT IS MISSING, and that is a decision rather
        # than a gap. The console renders an option with no counterargument as a FINDING in the
        # counterargument's own slot and leaves the verb enabled (C section 1.6c): refusing the
        # draft would teach drafting agents to write filler to get past this line, and filler in
        # that slot is worse than a finding, because it reads like an argument. The finding is
        # loud and it is attributed. `queue doctor` can count them.
        counter = _clean(o.get("counterargument"))
        # The registered `recommend` function, called directly with this transaction's ctx --
        # the same shape `recommend accept` uses to call `engine.post`. NOT a second INSERT into
        # `brain.recommendation`: a proposal is created in exactly one place, and a copy of that
        # INSERT here would be the drift the narrow waist exists to prevent.
        rec = _recommend(
            ctx,
            text=plan,
            rationale=counter,
            subject_type="work_item" if subject else "",
            subject_id=subject or "",
            template_id=o.get("template_id"),
            proposed_action=title,
            requires_human=True,
            produced_by=drafted_by or by or None,
            by=by or drafted_by or "queue")
        cost = o.get("cost_est")
        ctx.execute(
            """INSERT INTO brain.queue_item_option
                 (source_type, source_id, n, recommendation_id, kind, label, who, thinking,
                  size, lane, cost_est, time_est, recommended, drafted_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (st, sid, i, rec["id"], kind, _clean(o.get("label")) or title,
             _clean(o.get("who")), _clean(o.get("thinking")), _clean(o.get("size")),
             _clean(o.get("lane")), None if cost in (None, "") else float(cost),
             _clean(o.get("time_est")), bool(o.get("recommended")),
             drafted_by or by or ""))
        written.append({"n": i, "recommendation": rec["id"], "kind": kind, "label": title,
                        "counterargument": bool(counter)})

    # A SUCCESSFUL DRAFT CLEARS A PREVIOUS FAILURE NOTICE, because the notice is a statement
    # about the current state of the card and leaving it beside four live options would be the
    # card contradicting itself. The attempt is not erased from history: the thread carries both.
    ctx.execute(
        "UPDATE brain.queue_item SET options_error = NULL, options_drafted_at = now() "
        "WHERE source_type = %s AND source_id = %s", (st, sid))

    if subject:
        ctx.actor = drafted_by or by or "queue"
        ctx.thread(subject, "note",
                   f"{len(written)} options drafted on {st} {sid} by "
                   f"{drafted_by or by or 'a producer'}: "
                   + "; ".join(f"r{w['recommendation']} {w['label']}" for w in written)
                   + ". Each is a recommendation and each needs a human decider.")
    return {"source_type": st, "source_id": sid, "options": written,
            "without_counterargument": [w["n"] for w in written if not w["counterargument"]]}


# ------------------------------------------------------------------ the read the console renders

#: Every column the card needs, in one query for the whole board. ONE QUERY AND NOT ONE PER CARD:
#: `store.read()` opens a fresh Postgres connection every call -- measured at 13ms on this host --
#: and a console that polls every three seconds over twenty cards would spend a quarter of a
#: second per poll on connection setup alone.
_SETS = """
SELECT o.source_type, o.source_id, o.n, o.recommendation_id, o.kind, o.label, o.who,
       o.thinking, o.size, o.lane, o.cost_est, o.time_est, o.recommended, o.drafted_by,
       o.cost_actual, o.minutes_actual,
       r.text AS plan, r.rationale AS counterargument, r.state, r.template_id,
       r.proposed_action, r.spawned_work_item, r.decided_by, r.decided_at
  FROM brain.queue_item_option o
  JOIN brain.recommendation r ON r.id = o.recommendation_id
 ORDER BY o.source_type, o.source_id, o.n
"""


_ERRORS = """
SELECT source_type, source_id, options_error, options_drafted_at
  FROM brain.queue_item
 WHERE options_drafted_at IS NOT NULL
"""


def drafting_errors() -> dict:
    """Items where a drafting attempt was recorded, keyed `(source_type, source_id)`.

    Returns `{}` on a store without queue schema 0014, for the same reason `option_sets` does.
    """
    try:
        with store.read() as s:
            rows = s.query(_ERRORS)
    except Exception as exc:                                            # noqa: BLE001
        if "options_error" in str(exc) or "options_drafted_at" in str(exc):
            return {}
        raise
    return {(r["source_type"], str(r["source_id"])): dict(r) for r in rows}


def option_sets() -> dict:
    """Every drafted option set, keyed `(source_type, source_id)`. Reads only.

    Returns `{}` -- not an error -- when `brain.queue_item_option` does not exist, because a store
    without queue schema 0014 is the legacy state the card already renders (C section 1.6a: the
    frozen single-option card, zero new chrome). A console that raised here would take the whole
    queue down over a component that has nothing to show.
    """
    try:
        with store.read() as s:
            rows = s.query(_SETS)
    except Exception as exc:                                            # noqa: BLE001
        if "queue_item_option" in str(exc):
            return {}
        raise
    out: dict = {}
    for r in rows:
        out.setdefault((r["source_type"], str(r["source_id"])), []).append(dict(r))
    return out


# ------------------------------------------------------------------ the drafting agent's CLI


def main(argv=None) -> int:
    """`queue-options draft` and `queue-options show`. The drafting agent's surface.

    Kept out of `queue/bin/queue` on purpose while three lanes build against this tree at once:
    a new file collides with nobody, and folding these two subcommands into the queue CLI is a
    later edit to one function.
    """
    p = argparse.ArgumentParser(prog="queue-options", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("draft", help="write a 2-4 option set onto one queue item")
    d.add_argument("source_id", help="the queue item's id: a task id, a question id, or r<id>")
    d.add_argument("--source-type", default="work_item",
                   choices=("work_item", "question", "recommendation"))
    d.add_argument("--by", required=True, help="the drafting agent")
    d.add_argument("--failed", default="",
                   help="record that the drafting attempt FAILED, in one line. Writes no "
                        "options; the card renders the amber finding and falls back to the "
                        "singular recommended_option beneath it.")
    d.add_argument("-f", "--file", default="-",
                   help="JSON list of options, or - for stdin. Keys: kind, label, title, plan, "
                        "counterargument, who, thinking, size, lane, cost_est, time_est, "
                        "template_id, recommended")

    s = sub.add_parser("show", help="print the drafted set for one queue item")
    s.add_argument("source_id")
    s.add_argument("--source-type", default="work_item")

    args = p.parse_args(argv)
    if args.cmd == "draft":
        if args.failed:
            try:
                store.apply("queue draft options", actor=args.by,
                            source_type=args.source_type, source_id=args.source_id,
                            error=args.failed, drafted_by=args.by, by=args.by)
            except QueueError as exc:
                print(f"queue-options: {exc}", file=sys.stderr)
                return 5
            print(f"drafting failure recorded on {args.source_type} {args.source_id}: "
                  f"{args.failed}")
            return 0
        raw = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        try:
            options = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"queue-options: the option set is not valid JSON: {exc}", file=sys.stderr)
            return 2
        try:
            res = store.apply("queue draft options", actor=args.by,
                              source_type=args.source_type, source_id=args.source_id,
                              options=options, drafted_by=args.by, by=args.by)
        except QueueError as exc:
            print(f"queue-options: {exc}", file=sys.stderr)
            return 5
        print(f"{len(res['options'])} options drafted on {res['source_type']} "
              f"{res['source_id']}:")
        for o in res["options"]:
            print(f"  {o['n']}. r{o['recommendation']}  {o['kind']:<12} {o['label']}")
        if res["without_counterargument"]:
            # LOUD, AND NOT A REFUSAL. The card renders the finding; this line makes sure the
            # drafting agent reads it too rather than discovering it on the operator's screen.
            print(f"  WARNING: option(s) {res['without_counterargument']} state no "
                  f"counterargument. Each renders on the card as a finding: an option with "
                  f"nothing against it has not been argued, it has been advertised.",
                  file=sys.stderr)
        return 0

    sets = option_sets().get((args.source_type, str(args.source_id))) or []
    if not sets:
        print(f"no drafted options on {args.source_type} {args.source_id}")
        return 0
    for o in sets:
        cost = "no cost stated" if o["cost_est"] is None else f"~${o['cost_est']:g}"
        print(f"{o['n']}. r{o['recommendation_id']} [{o['state']}] {o['label']}\n"
              f"    {o['who']} · {o['thinking']} · {o['size']} · lane {o['lane'] or '-'} · "
              f"{cost} · {o['time_est'] or 'no time stated'}"
              + ("  RECOMMENDED" if o["recommended"] else "") + "\n"
              f"    plan:    {o['plan']}\n"
              f"    against: {o['counterargument'] or '** none stated: a finding, not filler **'}"
              + (f"\n    dispatched: {o['spawned_work_item']} by {o['decided_by']}"
                 if o["spawned_work_item"] else ""))
    return 0


# NO `if __name__ == "__main__"` AND NO `python3 -m human_queue.options`, and the reason is the
# narrow waist doing its job. `human_queue/__init__.py` imports this module to register the verb,
# so `-m` executes the file a SECOND time under the name `__main__` and `store.transition` raises
# `DuplicateTransition` -- correctly: two module objects would each hold a copy of the function.
# `queue/bin/queue-options` imports the package and calls `main()`, which is one import and one
# registration.
