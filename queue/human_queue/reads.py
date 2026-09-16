"""What the queue looks like. Reads only -- every function here runs on a READ ONLY session.

D7 renders what this module ranks. The contract between the two lanes is the dict shape of
`queue()`, published to D-CROSSTALK so the console can build against it without reading this
tree.

Two properties this module holds and a rewrite tends to lose:

- **Render a window, not a backlog.** `queue()` takes a limit per tier and reports the total
  beside it. The default view is a plan for the next hour, not an inventory of obligation.
- **Never silently drop or auto-downgrade.** Everything filtered out is counted and named:
  blocked items, deferred items, and anything below the window. Back-pressure operates on
  aggregation and producer discipline, never on suppression.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import store
from store import schema
from swarm_engine.signals import signal_level

from . import rank as ranking
from . import tiers

DEFAULT_WINDOW = 7          # top N per tier. A window, not a backlog.
DEAD_WAKE_DAYS = 14         # an until-event defer whose event has not fired in this long
SECOND_QUEUE_BUMPS = 3      # beyond this many live bumps, say plainly what has happened


def _now():
    return datetime.now(timezone.utc)


def weights() -> dict:
    """The ranking weights ACTUALLY IN FORCE, which until task 0434 nothing in this lane read.

    THE DEFECT THIS FUNCTION CLOSES, and it had been live long enough that both halves of it were
    separately believed. `queue()` called `ranking.rank(...)` with no `weights=` argument at all,
    so `rank` fell through to `DEFAULT_WEIGHTS` on every call, and `swarm admin config set
    signals.w_urgency 5` reached the fleet's claim ordering and NOTHING ELSE. Watched on this host:
    the engine read `urgency +5` while the console, for the same row in the same second, read
    `urgency +2`. The operator had a lever with a wire attached to one of the two things it was
    labelled to move.

    ONE CONFIG SOURCE, NOT A SECOND READER. `config.effective()` is the file with the store's
    override layer folded on top, and it is the same call `claim` makes. Reading the file directly
    here, or reading `brain.config_setting` here, would be a second resolution of one question and
    the two would drift the first time an override landed.

    FAILING SOFT IS REQUIRED AND IS NOT LAZINESS. This runs inside every console render. A config
    resolution that raised because Postgres dropped a connection would take the queue page down
    over a tuning value, and the defaults are a complete, correct answer: the queue that results is
    the queue every store served before anyone set an override.
    """
    try:
        from swarm_engine.config import effective
        return ranking.resolve_weights((effective() or {}).get("signals") or {})
    except Exception:                                                   # noqa: BLE001
        return dict(ranking.DEFAULT_WEIGHTS)


def _rows(s, sql, params=None):
    return s.query(sql, params)


def _dag(s) -> ranking.Dag:
    # Keep all live dependents, plus terminal subjects still represented in the
    # human queue. An isolated accepted/cancelled history row cannot unblock work.
    rows = _rows(s, """SELECT w.id, w.state, w.priority, w.depends_on
                         FROM brain.work_item w
                        WHERE w.state NOT IN %s
                           OR (w.state = 'done' AND w.accepted_at IS NULL)
                           OR EXISTS (SELECT 1 FROM brain.question q
                                       WHERE q.work_item_id = w.id AND q.answer IS NULL
                                         AND to_jsonb(q)->>'withdrawn_at' IS NULL)
                           OR EXISTS (SELECT 1 FROM brain.recommendation r
                                       WHERE r.subject_type = 'work_item'
                                         AND r.subject_id = w.id AND r.state = 'open')""",
                 (tuple(ranking.TERMINAL_STATES),))
    # A missing dependency means unresolved to Dag. Preserve referenced terminal
    # rows as witnesses, or omitting a finished blocker would block its child.
    present = {r["id"] for r in rows}
    referenced = {dep for r in rows for dep in ranking.parse_deps(r.get("depends_on"))}
    missing = sorted(referenced - present)
    if missing:
        rows.extend(_rows(s, """SELECT id, state, priority, depends_on
                                 FROM brain.work_item WHERE id = ANY(%s)""", (missing,)))
    return ranking.Dag(rows)


def _overlays(s) -> dict:
    # A conservative live-source superset, not another queue classifier. Include
    # agent-claimable live work and all open recommendations too; queue_open still
    # determines membership. Closed history cannot enter any arm of that view.
    return {(r["source_type"], r["source_id"]): r
            for r in _rows(s, """SELECT o.* FROM brain.queue_item o
                WHERE (o.source_type = 'work_item' AND EXISTS (
                    SELECT 1 FROM brain.work_item w WHERE w.id = o.source_id
                     AND (w.state NOT IN %s OR (w.state = 'done' AND w.accepted_at IS NULL))))
                   OR (o.source_type = 'question' AND EXISTS (
                    SELECT 1 FROM brain.question q WHERE q.id = o.source_id
                     AND q.answer IS NULL AND to_jsonb(q)->>'withdrawn_at' IS NULL))
                   OR (o.source_type = 'recommendation' AND EXISTS (
                    SELECT 1 FROM brain.recommendation r WHERE r.id::text = o.source_id
                     AND r.state = 'open'))""", (tuple(ranking.TERMINAL_STATES),))}


def _bumps(s) -> dict:
    out = {}
    # Keep every bump for a live source, including tiny decayed contributions.
    # A time cutoff would silently change the score's exponential decay.
    for r in _rows(s, """SELECT b.* FROM brain.queue_bump b
                WHERE (b.source_type = 'work_item' AND EXISTS (
                    SELECT 1 FROM brain.work_item w WHERE w.id = b.source_id
                     AND (w.state NOT IN %s OR (w.state = 'done' AND w.accepted_at IS NULL))))
                   OR (b.source_type = 'question' AND EXISTS (
                    SELECT 1 FROM brain.question q WHERE q.id = b.source_id
                     AND q.answer IS NULL AND to_jsonb(q)->>'withdrawn_at' IS NULL))
                   OR (b.source_type = 'recommendation' AND EXISTS (
                    SELECT 1 FROM brain.recommendation r WHERE r.id::text = b.source_id
                     AND r.state = 'open'))
                ORDER BY b.created_at""", (tuple(ranking.TERMINAL_STATES),)):
        out.setdefault((r["source_type"], r["source_id"]), []).append(r)
    return out


def _defers(s) -> dict:
    """Every defer on a live source, carrying whether its condition still HOLDS it out.

    TWO QUESTIONS, ONE ROW SET, AND CONFLATING THEM WAS THE BUG (task 0158). This used to select
    `woke_at IS NULL` and serve both answers off it, and it was wrong in both directions:

      * **as suppression** it never looked at `wake_at`, so a `+2h` defer whose two hours had
        passed stayed out of the tier forever. Nothing in this repo calls `queue wake` except a
        human typing it at the CLI -- there is no scheduler -- so an unhonoured wake condition is
        never honoured by anything, and the receipt the console prints ("it wakes when the
        condition is met") was false in the strong sense. Measured 2026-08-16: defer +2h dropped
        the open count 1 -> 0 correctly and moving `wake_at` into the past left it at 0.
      * **as a count** it dropped woken rows, while `queue defer` refuses the third by counting
        every prior row of a postponing kind, woken or not. So once a defer elapsed the card
        offered the time chips back and the verb refused the tap. A control that is offered and
        then refused is worse than one that was never offered.

    So the row set retains every defer for each live source, including woken rows,
    and `condition_unmet` is computed per kind:

      * `until-time`      the store holds the answer: `wake_at > now()`.
      * `until-question`  the store holds the answer too, and this is the kind no ordinary task
                          manager can have: the wake task landing is what makes the item CHEAPER
                          rather than older, so a read that ignores it discards the whole point.
      * `until-event`     the store does NOT hold the answer. No table here records that a named
                          event fired, and inventing a rule would be worse than waiting, so these
                          hold until `queue wake` closes them and `doctor()` reports one that has
                          not fired in DEAD_WAKE_DAYS. Stated rather than quietly guessed.
      * `decline` / `accept-default` are exits, not postponements. They hold, and they are not
        counted as defers by either half.
    """
    out = {}
    for r in _rows(s, """SELECT d.*,
                                CASE d.kind
                                  WHEN 'until-time'     THEN d.wake_at > now()
                                  WHEN 'until-question' THEN coalesce(w.state, '') <> 'done'
                                  ELSE true
                                END AS condition_unmet
                           FROM brain.queue_defer d
                           LEFT JOIN brain.work_item w ON w.id = d.wake_task
                          WHERE (d.source_type = 'work_item' AND EXISTS (
                              SELECT 1 FROM brain.work_item src WHERE src.id = d.source_id
                               AND (src.state NOT IN %s
                                 OR (src.state = 'done' AND src.accepted_at IS NULL))))
                             OR (d.source_type = 'question' AND EXISTS (
                              SELECT 1 FROM brain.question q WHERE q.id = d.source_id
                               AND q.answer IS NULL AND to_jsonb(q)->>'withdrawn_at' IS NULL))
                             OR (d.source_type = 'recommendation' AND EXISTS (
                              SELECT 1 FROM brain.recommendation r WHERE r.id::text = d.source_id
                               AND r.state = 'open'))
                          ORDER BY d.created_at DESC""", (tuple(ranking.TERMINAL_STATES),)):
        out.setdefault((r["source_type"], r["source_id"]), []).append(r)
    return out


# The three kinds that are POSTPONEMENTS. `decline` and `accept-default` are exits: an item that
# left toward a decision has not been deferred, and counting them toward the third-defer refusal
# would spend the operator's two postponements on acts that were never postponements. This tuple
# is the read's half of that definition; `queue defer` holds the write's half by excluding the
# same two kinds from its `prior` count, and the two must not drift apart.
POSTPONING = ("until-time", "until-event", "until-question")


def _holding(defers: list[dict]) -> list[dict]:
    """The defers that are keeping this item out of its tier right now."""
    return [d for d in defers if d["woke_at"] is None and d["condition_unmet"]]


def _idle_agents(s) -> dict:
    """An agent parked on an item, waiting. The only condition that costs wall-clock by the minute."""
    rows = _rows(s, """SELECT a.name, a.work_item_id, a.updated, a.status
                         FROM brain.agent a
                        WHERE a.work_item_id <> '' AND a.status IN ('blocked', 'waiting', 'idle')""")
    return {r["work_item_id"]: r for r in rows}


def _pending_defaults(s) -> dict:
    return {r["question_id"]: r for r in _rows(s, "SELECT * FROM brain.queue_pending_default")}


def queue(tier: str | None = None, window: int = DEFAULT_WINDOW, include_deferred=False,
          *, exclude_objectives: bool = False) -> dict:
    """The human queue, ranked, with everything the console needs and nothing it must compute.

    The returned item dict is the published interface. Adding a key is safe; renaming one is a
    crosstalk post.

    The legacy console opts out of objective cards before composition and windowing. Other
    callers keep the complete source population and the same ranking implementation.
    """
    now = _now()
    with store.read("runtime") as s:
        s.query("SELECT set_config('jit', 'off', true)")
        raw = _rows(s, "SELECT * FROM brain.queue_open")
        if exclude_objectives:
            raw = [r for r in raw if r["source_type"] != "objective"]
        dag = _dag(s)
        overlays = _overlays(s)
        bumps = _bumps(s)
        defers = _defers(s)
        idle = _idle_agents(s)
        pending = _pending_defaults(s)

    items, blocked, deferred = [], [], []
    for r in raw:
        key = (r["source_type"], str(r["source_id"]))
        ov = overlays.get(key) or {}
        wid = r.get("work_item_id")
        item = dict(r)
        item["source_id"] = str(r["source_id"])
        item["counterargument"] = ov.get("counterargument")
        item["added"] = ov.get("added") or r.get("surfaced_at")
        # EVERY DEFER EVER MADE, not the open ones: this is the number the third-defer fork is
        # drawn from, and `queue defer` refuses the third by counting exactly these rows. The two
        # halves disagreeing is how the card came to offer a chip the verb then refused.
        item["defers"] = len([d for d in defers.get(key, []) if d["kind"] in POSTPONING])
        item["gates"] = [n.upper() for n, v in (("external", r["external"]),
                                                ("canon", r["canon_touching"])) if v]
        if r["source_type"] == "question":
            pd = pending.get(str(r["source_id"]))
            item["default_text"] = pd["default_text"] if pd else None
            item["default_null_branch"] = pd["null_branch"] if pd else None
            item["default_fired_at"] = pd["fired_at"] if pd else None
        # THE ONE COMPOSITION. `queue demote` calls the same function on the same two rows, so
        # the tier the operator is looking at and the tier the demote demotes FROM cannot drift.
        item.update(tiers.tier_inputs(r, ov, item.get("default_text")))
        rev = signal_level("reversibility", r.get("reversibility"))
        t = tiers.tier_of(item, rev)
        item.update({"tier": t["tier"], "tier_reason": t["reason"],
                     "reversibility_floor": t["floor_applied"], "reversibility_level": rev})
        agent = idle.get(wid) if wid else None
        item["agent_idle_since"] = agent["updated"] if agent else None
        item["idle_agent"] = agent["name"] if agent else None
        # `undo` is a rendering fact the console must not have to derive: where no un-verb
        # exists the stripe says `no undo -- external` rather than pretending.
        item["undoable"] = not r["external"]

        # A DEFER SUPPRESSES ONLY WHILE ITS CONDITION IS UNMET. An elapsed `+2h` is a defer that
        # has done its job, and the item belongs back on the tier where the operator will see it.
        holding = _holding(defers.get(key, []))
        if holding:
            item["deferred"] = [dict(d) for d in holding]
            if not include_deferred:
                deferred.append(item)
                continue
        if wid and dag.blocked(wid):
            item["blocked_on"] = dag.unresolved_blockers(wid)
            blocked.append(item)
            continue
        items.append(item)

    # THE WEIGHTS, RESOLVED ONCE FOR THE WHOLE READ. Once, and not per tier, because three tiers
    # ranked under three separately resolved weight sets could disagree if an override landed
    # between them, and one queue ordered by two weightings is the drift this argument exists to
    # end. Published on the returned dict so the console can state what it ordered by without
    # resolving them a second time.
    w = weights()
    out = {"tiers": {}, "totals": {}, "now": now, "cycles": [], "window": window, "weights": w}
    for t in tiers.TIERS:
        pool = [i for i in items if i["tier"] == t]
        ordered = ranking.rank(pool, dag, bumps_by_key=bumps, weights=w, now=now)
        shown = ordered[:window]
        # The reserved slot: each tier's list always includes its single oldest item, marked.
        # Bounded cost, and it converts starvation from a statistic into something the operator
        # must consciously decline.
        if len(ordered) > window:
            reserved = max(ordered, key=lambda r: r["age_days"])
            if reserved not in shown:
                reserved["reserved_oldest"] = True
                shown = shown + [reserved]
        out["tiers"][t] = {"items": shown, "total": len(ordered),
                           "hidden": max(0, len(ordered) - len(shown)),
                           "act": tiers.TIER_ACT[t]}
    out["cycles"] = dag.cycles
    out["totals"] = {"open": len(items), "blocked": len(blocked), "deferred": len(deferred),
                     "decide": out["tiers"]["decide"]["total"],
                     "judge": out["tiers"]["judge"]["total"],
                     "shape": out["tiers"]["shape"]["total"]}
    out["blocked"] = blocked
    out["deferred_items"] = deferred
    if tier:
        out["tiers"] = {tier: out["tiers"][tier]}
    return out


#: HIS FIVE QUEUES, IN THE ORDER HE NAMED THEM. The order is not alphabetical and is not a
#: preference: it runs from the cheapest thing he can clear to the most expensive. Questions have a
#: stated default and often take seconds; decisions are the ones only he can make.
QUEUES = ("questions", "review", "dependencies", "work", "decisions")


def queues(limit: int = 20) -> dict:
    """His five queues, his ask 5, with the counts and the top of each.

    FOUR OF THE FIVE NEEDED NOTHING BUILT, which is why this ask turned out to be much smaller than
    it was described as. `brain.question` is already its own table, review is `done` and not
    accepted, dependencies are derivable from `depends_on` in both directions, and work is the
    inbox. Only `decisions` had no marker, and migration 52 is that one column.

    A ROW MAY APPEAR IN TWO QUEUES AND THAT IS CORRECT. A done-and-unaccepted row that also blocks
    something is in `review` and in `dependencies`: one row, two questions. So `total` is the sum of
    the five counts and NOT the number of distinct rows, and it is named `across` rather than
    `total` so nobody reads it as a board count. Deduplicating would mean choosing which question
    matters, which is his job and not this function's.

    IT FALLS BACK BELOW LEDGER 52 AND KEEPS SERVING, with `decisions` reported as UNAVAILABLE rather
    than as empty. Zero and unknown are different answers, and only one of them is true: a store
    with nowhere to record a kind has no decisions in it to find, and saying `0` would paint
    "nothing needs deciding" over a column that does not exist.
    """
    have_kind = schema.has_column("work_item", "kind")
    if not have_kind:
        schema.warn_once(
            "operator_queues",
            "this store has no brain.work_item.kind (ledger 52), so the decisions queue cannot be "
            "read. Apply migrations/0052_a_decision_is_declared.sql.")
    out = {"queues": {}, "across": 0, "now": _now(), "decisions_available": have_kind}
    with store.read("runtime") as s:
        if have_kind:
            counts = {r["queue"]: r["n"] for r in _rows(s, "SELECT * FROM brain.operator_queue_count")}
            for q in QUEUES:
                items = _rows(s, "SELECT * FROM brain.operator_queue WHERE queue = %s "
                                 " ORDER BY since DESC NULLS LAST LIMIT %s", (q, limit))
                out["queues"][q] = {"n": int(counts.get(q, 0)), "items": items, "available": True}
        else:
            # THE FOUR THAT DO NOT NEED THE COLUMN STILL ANSWER. A surface that went blank on all
            # five over one missing column would be the outage `docs/SCHEMA-TOLERANCE.md` rule 1
            # exists against, and four of these five have been readable since migration 1.
            sql = {
                "questions": "SELECT count(*) FROM brain.question WHERE answer IS NULL",
                "review": "SELECT count(*) FROM brain.work_item "
                          " WHERE state = 'done' AND accepted_at IS NULL",
                "dependencies": "SELECT count(*) FROM brain.work_item "
                                " WHERE state IN ('inbox','active') "
                                "   AND COALESCE(btrim(depends_on), '') <> ''",
                "work": "SELECT count(*) FROM brain.work_item WHERE state = 'inbox'",
            }
            for q in QUEUES:
                if q == "decisions":
                    out["queues"][q] = {"n": None, "items": [], "available": False,
                                        "why": "this store is below ledger 52 and has no "
                                               "brain.work_item.kind, so nothing marks a decision"}
                    continue
                out["queues"][q] = {"n": int(s.scalar(sql[q])), "items": [], "available": True}
    out["across"] = sum(v["n"] for v in out["queues"].values() if v["n"] is not None)
    return out


def why(source_type: str, source_id: str) -> dict:
    """The additive decomposition for one item, including the bump and its decay state.

    This is what makes the ordering falsifiable. `entities/rules/priority-model.md`: if it
    cannot explain the order from the signals and the rules, the order is wrong.
    """
    q = queue(window=10_000, include_deferred=True)
    found = None
    for t, block in q["tiers"].items():
        for it in block["items"]:
            if it["source_type"] == source_type and str(it["source_id"]) == str(source_id):
                found = {**it, "tier": t, "of": block["total"]}
    if not found:
        for it in q["blocked"] + q["deferred_items"]:
            if it["source_type"] == source_type and str(it["source_id"]) == str(source_id):
                found = {**it, "of": None}
    if not found:
        return {"found": False, "source_type": source_type, "source_id": source_id}

    with store.read("runtime") as s:
        dag = _dag(s)
        bumps = _bumps(s).get((source_type, str(source_id)), [])
    wid = found.get("work_item_id")
    detail = dag.explain(wid) if wid else []
    _, live = ranking.bump_contribution(bumps, now=q["now"])
    return {"found": True, **found, "unblock_detail": detail, "bump_detail": live,
            "declared_unblocking": found.get("dependency_unblocking"),
            "declared_vs_computed": _calibration_note(found.get("dependency_unblocking"),
                                                      found.get("unblock_weight", 0.0))}


def _calibration_note(declared, computed) -> str | None:
    """Say when the producer's declared unblocking and the measured DAG disagree.

    Reported, never silently substituted. A producer declaring `high` on an item that blocks
    nothing is a measurable miscalibration, and overwriting the declaration would destroy the
    evidence that it happened.
    """
    level = signal_level("dependency_unblocking", declared)
    if level is None:
        return None
    if level == "high" and computed < 0.5:
        return (f"the producer declared dependency_unblocking={declared} and the DAG says this "
                f"item releases {computed:.2f}. That is a calibration miss worth a look.")
    if level == "low" and computed >= 2.0:
        return (f"the producer declared dependency_unblocking={declared} but this item releases "
                f"{computed:.2f} of downstream value. The computed term is what ranks it.")
    return None


# ------------------------------------------------------------------ the operator's stopwatch
#
# Everything below reads `brain.time_entry` through the two views migration 23 installs, and
# NEVER off the base table. `time_entry_effective` is the row set a mean may count -- stopped, not
# abandoned, not superseded by a correction -- and the whole argument for these functions is that
# the difference between its size and the number of items actually cleared is a fact that has to
# be REPORTED, not averaged away. See `queue/schema/0012_operator_time_entry.sql`.

def _entry_rows(s, since, who: str) -> list[dict]:
    """Effective entries that STOPPED inside the window. One row per entry, not per item."""
    return _rows(s, """SELECT id, source_type, source_id, tier_at_start, started_at, stopped_at,
                              seconds
                         FROM brain.time_entry_effective
                        WHERE who = %s AND stopped_at >= %s
                        ORDER BY started_at""", (who, since))


def _per_item(entries: list[dict]) -> dict:
    """Fold entries into ONE DATA POINT PER ITEM, which is what "minutes per item" means.

    An item worked in three sittings cost the operator the sum of the three, and counting each
    sitting as its own observation would report the median SITTING and label it the median item --
    a number that is smaller than the truth by however disciplined he was about pausing.

    The tier is the tier of the EARLIEST entry, and an item whose entries span two tiers is
    counted once, in that first tier, and also counted in `mixed_tier` so the caller can say so.
    Splitting its minutes across two tiers would put a fraction of one observation into each mean,
    and a mean over fractional observations is not a mean over items.
    """
    by_item: dict = {}
    for e in entries:
        key = (e["source_type"], e["source_id"])
        cur = by_item.setdefault(key, {"seconds": 0.0, "tier": e["tier_at_start"],
                                       "entries": 0, "tiers": set()})
        cur["seconds"] += float(e["seconds"] or 0.0)
        cur["entries"] += 1
        cur["tiers"].add(e["tier_at_start"])
    return by_item


def _stat(seconds: list[float]) -> dict:
    """n, mean, median. Median beside mean because ONE ambush moves a mean and not a median.

    The fast lane's whole asset is trust, and the question "is Decide really a two-minute lane"
    is answered differently by the two: a mean of 4 minutes over ten items can be nine
    ninety-second items and one twenty-five-minute one, which is a lane that works with a bug in
    it, not a lane that is slow.
    """
    if not seconds:
        return {"n": 0, "mean_minutes": None, "median_minutes": None, "total_minutes": 0.0}
    ordered = sorted(seconds)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return {"n": len(ordered),
            "mean_minutes": round(sum(ordered) / len(ordered) / 60.0, 2),
            "median_minutes": round(median / 60.0, 2),
            "total_minutes": round(sum(ordered) / 60.0, 2)}


def time_status(who: str = "operator", days: int = 1) -> dict:
    """What the stopwatch is doing right now, and what it measured recently. Reads only.

    NOT A TRANSITION and deliberately not registered as one: it changes nothing, and
    `store.registered()` is the audit surface for what CAN change state in this system. A read in
    that list is a name a reviewer has to check and then discover is inert.
    """
    since = _now() - timedelta(days=days)
    with store.read("runtime") as s:
        cap = float(s.one("SELECT EXTRACT(EPOCH FROM brain.time_entry_cap()) AS c")["c"])
        running = s.one("""SELECT * FROM brain.time_entry_annotated
                            WHERE who = %s AND running""", (who,))
        recent = _rows(s, """SELECT id, source_type, source_id, tier_at_start, started_at,
                                    stopped_at, seconds, ended_how, superseded, note, corrects
                               FROM brain.time_entry_annotated
                              WHERE who = %s AND NOT running AND stopped_at >= %s
                              ORDER BY started_at DESC""", (who, since))
    out = {"who": who, "now": _now(), "cap_seconds": cap, "days": days,
           "running": None, "recent": recent}
    if running:
        elapsed = float(running["elapsed_seconds"])
        out["running"] = {
            "id": running["id"], "source_type": running["source_type"],
            "source_id": running["source_id"], "tier_at_start": running["tier_at_start"],
            "started_at": running["started_at"], "elapsed_seconds": elapsed,
            "elapsed_minutes": round(elapsed / 60.0, 2), "over_cap": bool(running["over_cap"]),
            # Said BEFORE the stop, not after it. An operator who learns at stop time that his
            # entry has been relabelled has already lost the chance to correct it into something
            # true; one who is told while it is still running can.
            "warning": ("this timer is past the cap and WILL be recorded as abandoned, out of "
                        "every mean, whatever you pass to `stop`. If you know what the interval "
                        "actually was, stop it and then `queue time correct` it."
                        if running["over_cap"] else None)}
    measured = [r for r in recent if r["ended_how"] == "stopped" and not r["superseded"]]
    out["today"] = _stat([float(r["seconds"]) for r in measured])
    out["abandoned"] = len([r for r in recent if r["ended_how"] == "abandoned"])
    out["superseded"] = len([r for r in recent if r["superseded"]])
    return out


def tier_ceilings(days: int = 30, who: str = "operator") -> dict:
    """Is `Decide` really a two-minute lane? Measured, with the coverage of the measurement.

    `tiers.TIER_CEILING_SECONDS` holds the only ceiling anyone has claimed and `None` for the two
    nobody has, and this function reports `unclaimed` for those rather than inventing a number to
    compare against. A tier with no measured entry reports `unmeasured` and NOT a zero: zero
    breaches out of zero observations reads as a lane in perfect health and is a statement about
    nothing.

    `queue demote` already writes a calibration miss against the producer when the operator taps
    `not fast`. Until this function had rows to read, that miss recorded an opinion; the breach
    rate here is the evidence beside it.
    """
    since = _now() - timedelta(days=days)
    with store.read("runtime") as s:
        entries = _entry_rows(s, since, who)
    by_item = _per_item(entries)
    out = {"days_measured": days, "who": who, "tiers": {},
           "items_measured": len(by_item),
           "mixed_tier_items": len([v for v in by_item.values() if len(v["tiers"]) > 1])}
    for t in tiers.TIERS:
        claimed = tiers.TIER_CEILING_SECONDS.get(t)
        secs = [v["seconds"] for v in by_item.values() if v["tier"] == t]
        block = {"claimed_seconds": claimed, **_stat(secs)}
        if claimed is None:
            block["verdict"] = (f"no ceiling has been claimed for {t}. Nothing to measure "
                                f"against, and inventing one would manufacture the claim.")
        elif not secs:
            block["verdict"] = (f"UNMEASURED: no timed item started in {t} in the last {days}d. "
                                f"The {claimed / 60:.0f}-minute ceiling is still an assertion.")
        else:
            over = [x for x in secs if x > claimed]
            block["over_ceiling"] = len(over)
            block["over_ceiling_rate"] = round(len(over) / len(secs), 3)
            block["verdict"] = (
                f"measured over {len(secs)} timed item(s): median "
                f"{block['median_minutes']:.1f}min against a claimed "
                f"{claimed / 60:.0f}min ceiling, {len(over)} of {len(secs)} over it. "
                f"This is what {len(secs)} stopwatch readings say, not what the lane claims.")
        out["tiers"][t] = block
    return out


# ------------------------------------------------------- what left the queue, and what covered it
#
# ONE DEFINITION OF "CLEARED" AND ONE OF "COVERAGE", because two callers now need both and the
# second caller is on a surface, where a divergence would be invisible. `depth_and_clearance`
# held these inline; `operator_minutes` needs the same denominator over a shorter window, and
# a console that computed its own would be a second definition of the one figure whose whole job
# is to stop a measurement implying it saw everything.


def _cleared(s, since) -> dict:
    """Everything that left the operator's queue since `since`, in the two shapes callers need.

    `rows` is a COUNT WITH DUPLICATES and `keys` is a SET, and they differ on purpose. Item
    throughput asks how many dispositions happened, and an item both accepted and disposed is two
    acts. Coverage asks how many distinct items a timer could have covered, and that is one item.
    """
    disposed = _rows(s, """SELECT source_type, source_id, disposed_at
                             FROM brain.queue_item
                            WHERE disposed_at IS NOT NULL AND disposed_at >= %s""", (since,))
    accepted = _rows(s, """SELECT id, accepted_at FROM brain.work_item
                            WHERE accepted_at IS NOT NULL AND accepted_at >= %s""", (since,))
    answered = _rows(s, """SELECT id, answered_at FROM brain.question
                            WHERE answered_at IS NOT NULL AND answered_at >= %s""", (since,))
    return {
        "rows": len(disposed) + len(accepted) + len(answered),
        "keys": ({("work_item", str(r["id"])) for r in accepted}
                 | {("question", str(r["id"])) for r in answered}
                 | {(r["source_type"], str(r["source_id"])) for r in disposed}),
    }


def _ledger_counts(s, since, who: str) -> dict:
    """The four classes of entry in the window. Counted from the ANNOTATED view, not the effective
    one: the entries a mean may not count are exactly the ones a reader needs to see, and a surface
    that reported only the clean rows would hide the forgotten timers that motivated the cap."""
    row = s.one("""SELECT count(*) FILTER (WHERE ended_how = 'stopped'
                                             AND NOT superseded) AS measured,
                          count(*) FILTER (WHERE ended_how = 'abandoned') AS abandoned,
                          count(*) FILTER (WHERE superseded)        AS superseded,
                          count(*) FILTER (WHERE running)           AS running
                     FROM brain.time_entry_annotated
                    WHERE who = %s AND (stopped_at >= %s OR running)""", (who, since))
    return {k: int(row[k]) for k in ("measured", "abandoned", "superseded", "running")}


def _coverage(cleared_keys: set, by_item: dict, window: str) -> dict:
    """The "measured over N of M item(s)" sentence every stopwatch figure has to carry.

    `window` is a LABEL and not a number, because the two callers measure different spans -- 7d for
    the depth block, the Brief's own 12h for the leverage ratio -- and a coverage figure whose
    window is not stated beside it is the same omission this function exists to prevent, one level
    up.
    """
    covered = cleared_keys & set(by_item)
    return {
        "measured": len(covered), "cleared": len(cleared_keys),
        "rate": round(len(covered) / len(cleared_keys), 3) if cleared_keys else None,
        "text": (f"measured over {len(covered)} of {len(cleared_keys)} item(s) cleared in "
                 f"{window}" if cleared_keys else
                 f"nothing was cleared in {window}, so there is nothing to have covered"),
    }


# --------------------------------------------------------- the operator's half of one ratio

def operator_minutes(hours: float = 12.0, who: str = "operator") -> dict:
    """Measured operator minutes over an HOURS window, with the coverage of the measurement.

    Task 0290, for the Brief's leverage ratio. It exists rather than the ratio being computed off
    `time_status` for two reasons, and both are about the caller being a screen:

      * **THE WINDOW.** The Brief states everything over its own 12 hours. `time_status` and
        `depth_and_clearance` take `days`, and a ratio whose two halves cover different spans is
        not a ratio. So this one takes hours, and the caller passes the window it is already
        printing in its own header.
      * **THE COST.** `depth_and_clearance` is the only existing read that reports coverage, and it
        reads every open queue item to do it (`web/app.py:_queue_ctx` keeps it off the console's
        three-second poll for exactly that reason). The Brief IS re-rendered on that poll. Coverage
        needs the cleared set and not the depth, so this pays four small windowed reads and no
        `queue()` call.

    **WHAT THE NUMBERS ARE, AND WHICH WAY EACH ERROR PUSHES A RATIO BUILT ON THEM.** A caller that
    divides something by `measured_minutes` is dividing by a SUBSET of the operator's minutes, and
    every field below exists so it can say by how much rather than implying by none:

      * `measured_minutes` counts `time_entry_effective` entries that STOPPED inside the window.
        Untimed work is not in it -- a timer is never required and an untimed item is normal -- so
        the subset is smaller than the truth and a ratio over it reads HIGH.
      * `entries.abandoned` and `entries.superseded` are minutes the ledger holds and no mean may
        count. Same direction: they make the denominator smaller than his real time.
      * `running_minutes` is a live timer's elapsed, reported and NOT summed in. An unstopped
        interval is not a measurement (the schema's own rule), and the whole point of stating it
        is that on a 12h window it can be a large excluded quantity.
      * `coverage` is over ITEMS CLEARED, never over minutes. It is the closest measurable proxy
        and it is not the same question: minutes he spent on anything that is not a work item, a
        question or a recommendation cannot be recorded in this ledger at all, so no coverage
        figure here bounds them.
      * The one error in the other direction, stated so a caller does not claim a ceiling it does
        not have: an entry that started before the window and stopped inside it is counted in
        full, so up to one `brain.time_entry_cap()` of pre-window minutes can land in the
        denominator. That is why a caller must not label the ratio a ceiling; it is a measurement
        with a named dominant error, not a bound.
    """
    since = _now() - timedelta(hours=hours)
    with store.read("runtime") as s:
        cl = _cleared(s, since)
        entries = _entry_rows(s, since, who)
        ledger = _ledger_counts(s, since, who)
        running = s.one("""SELECT id, source_type, source_id, started_at, elapsed_seconds
                             FROM brain.time_entry_annotated
                            WHERE who = %s AND running""", (who,))
        # THE CAP OUT OF THE DATABASE, never a Python copy of it. `brain.time_entry_cap()` is a
        # function precisely so this constant exists once, and `queue/README.md` names a Python
        # constant shadowing a SQL one as the drift to avoid. It is here only to be printed in the
        # sentence about abandoned entries; nothing in this module enforces it.
        cap = s.one("SELECT EXTRACT(EPOCH FROM brain.time_entry_cap()) AS c")
        cap_hours = float(cap["c"]) / 3600.0
    by_item = _per_item(entries)
    stat = _stat([v["seconds"] for v in by_item.values()])
    window = f"{hours:g}h"
    out = {
        "who": who, "hours": hours, "since": since, "window": window,
        "measured_minutes": stat["total_minutes"], "timed_items": stat["n"],
        "entries": ledger,
        "running_minutes": (round(float(running["elapsed_seconds"]) / 60.0, 1)
                            if running else None),
        "coverage": _coverage(cl["keys"], by_item, window),
    }
    # The excluded classes, named in one sentence a caller can print whole. Nothing is subtracted
    # silently: if the ledger holds an abandoned entry, an abandoned entry is what the surface says
    # it holds, and the reader can see how much of his time the measured figure is missing.
    missing = []
    if ledger["running"]:
        # NO ELAPSED FIGURE IN THIS SENTENCE, and `running_minutes` above is where a caller that
        # wants one gets it. MEASURED, not reasoned: with the elapsed interpolated here, two
        # renders of `/brief` seven seconds apart differed -- and the Brief is ONE polled region
        # covering the whole page, so a figure that moves on the clock swaps every form on the
        # screen twenty times a minute. That is the defect `defer_options` already fixed once by
        # rendering a wake SPEC instead of a timestamp, and the Queue room's ticking strip is a
        # region of its own for the same reason. A sentence a surface prints should be stable
        # while the fact it states is unchanged.
        missing.append("a timer is running now and is not counted until it stops")
    if ledger["abandoned"]:
        missing.append(f"{ledger['abandoned']} abandoned entr"
                       f"{'y' if ledger['abandoned'] == 1 else 'ies'} (forgotten or over the "
                       f"{cap_hours:g}h cap) are in the ledger and out of every mean")
    if ledger["superseded"]:
        missing.append(f"{ledger['superseded']} superseded by a correction")
    out["excluded"] = missing
    out["basis"] = (
        f"{stat['total_minutes']:g} minute(s) over {stat['n']} timed item(s) in the last "
        f"{window}, from brain.time_entry_effective. A timer is never required, so this is a "
        f"SUBSET of his "
        f"minutes and any ratio over it reads high: {out['coverage']['text']}."
        if stat["n"] else
        f"NO OPERATOR MINUTES WERE MEASURED in the last {window}. It is null rather than zero, "
        f"and nothing derived from it is rendered as a number.")
    return out


# ------------------------------------------------------------------ WIP: depth and clearance

def depth_and_clearance(days: int = 7, who: str = "operator") -> dict:
    """Depth, clearance, and the ETA that divides them -- in TWO CURRENCIES, each labelled.

    Published as a readable so producers can see it. Agents generate at machine speed and the
    operator clears maybe 30 to 60 minutes a day; the arrival rate is the thing that decides
    whether this layer survives contact, so it is measured from day one rather than assumed.

    TWO CURRENCIES, AND THE WHOLE POINT IS THAT THEY ARE NOT MIXED (task 0279):

      * `eta_days` divides depth by ITEM THROUGHPUT -- how many things left the queue per day
        over the window. It is a real measurement and it was always one, but it cannot answer
        "how many hours of your time is this backlog", and it averages over days he never opened
        the queue at all.
      * `stopwatch.eta_hours` multiplies depth by MEASURED OPERATOR MINUTES from
        `brain.time_entry`. That is the number the whole program is about, and before migration 23
        there was no column anywhere that could produce it.

    A null ETA when nothing has ever been cleared is deliberate, and so is a null `eta_hours` for
    a tier with no measured entry. Dividing by an assumed clearance rate would produce a confident
    number from no measurement, which is the exact failure this program treats as a finding --
    and quietly substituting another tier's mean, or a global one, would be the same failure
    wearing a per-tier label. THERE IS NO FALLBACK IN THIS FUNCTION. Where a figure cannot be
    measured it is `None` and `stopwatch.unmeasured_tiers` names the tier and its depth, so the
    caller reports a gap rather than a number.

    COVERAGE IS REPORTED BECAUSE A TIMER IS NEVER REQUIRED. An untimed item is normal. Averaging
    over the timed subset and printing it as the queue's cost implies the measurement saw
    everything; `stopwatch.coverage` says "measured over N of M" instead.
    """
    q = queue(window=10_000)
    since = _now() - timedelta(days=days)
    with store.read("runtime") as s:
        cl = _cleared(s, since)
        entries = _entry_rows(s, since, who)
        ledger = _ledger_counts(s, since, who)
    cleared = cl["rows"]
    per_day = cleared / days if days else 0.0
    out = {"days_measured": days, "cleared": cleared, "per_day": round(per_day, 2),
           "depth": q["totals"], "eta_days": {}, "measured": cleared > 0}
    for t in tiers.TIERS:
        n = q["tiers"][t]["total"]
        out["eta_days"][t] = round(n / per_day, 2) if per_day else None
    total = q["totals"]["open"]
    out["eta_days"]["all"] = round(total / per_day, 2) if per_day else None
    # At roughly 7x daily clearance, triage mode unlocks: a reviewable batch-decline list, not
    # a red number and not a silent drop.
    out["triage_mode"] = bool(per_day and total >= 7 * per_day)
    out["eta_days_basis"] = (f"item throughput: {cleared} item(s) left the queue in {days}d. It "
                             f"contains no minutes and it averages over days the queue was never "
                             f"opened.")

    # ---------------------------------------------------------------- the stopwatch half
    by_item = _per_item(entries)
    sw = {
        "who": who,
        "entries": ledger,
        "coverage": _coverage(cl["keys"], by_item, f"{days}d"),
        "per_item": {}, "eta_hours": {}, "unmeasured_tiers": [],
        "untiered_items": len([v for v in by_item.values() if v["tier"] is None]),
        "mixed_tier_items": len([v for v in by_item.values() if len(v["tiers"]) > 1]),
    }
    sw["overall"] = _stat([v["seconds"] for v in by_item.values()])
    measured_hours = 0.0
    for t in tiers.TIERS:
        secs = [v["seconds"] for v in by_item.values() if v["tier"] == t]
        st = _stat(secs)
        sw["per_item"][t] = st
        depth_t = q["tiers"][t]["total"]
        if st["n"]:
            hours = round(depth_t * st["median_minutes"] / 60.0, 2)
            sw["eta_hours"][t] = hours
            measured_hours += hours
        else:
            sw["eta_hours"][t] = None
            sw["unmeasured_tiers"].append({"tier": t, "depth": depth_t,
                                           "why": "no measured time entry started in this tier"})
    # NOT an "all" figure. It is the sum of the tiers that HAVE a measurement, and the tiers left
    # out are named beside it. An "all" that quietly used the overall median for the unmeasured
    # tiers would be one number covering three, two of which were assumed.
    sw["eta_hours_measured_tiers_only"] = (
        round(measured_hours, 2) if len(sw["unmeasured_tiers"]) < len(tiers.TIERS) else None)
    sw["basis"] = (
        f"depth x the MEDIAN measured minutes per item in each tier, from "
        f"{sw['overall']['n']} timed item(s) in the last {days}d. Median and not mean: one "
        f"forgotten-looking outlier should not move the estimate the operator plans his morning "
        f"from. Tiers with no measured entry are null and named in `unmeasured_tiers`; nothing "
        f"here falls back to another tier's number."
        if sw["overall"]["n"] else
        f"NO OPERATOR TIME HAS BEEN MEASURED in the last {days}d, so every hours figure is null "
        f"rather than zero. `queue time start` / `queue time stop` is what fills this in; until "
        f"it does, the two-minute Decide ceiling and the cost of this backlog are both "
        f"assertions.")
    out["stopwatch"] = sw
    return out


# ------------------------------------------------------------------ whose queue is whose


def mine(who: str) -> tuple:
    """The open queue narrowed to one human, AND the total. Lane E, row 0384.

    Returns `(rows, total)` rather than rows alone, deliberately, because the caller must be able
    to print a denominator. An empty personal queue and an empty QUEUE are different facts and
    they render identically if only the narrowed number reaches the surface. That is rule 2 of
    the shared context in the shape it bites a read rather than a verdict.

    THIS IS A FILTER, NOT A BOUNDARY. `brain.queue_open` is unchanged and still shows every open
    item to every caller; this function narrows it on request, for any human, from any login. See
    `outputs/2026-08-27-E-0384-multi-user/IDENTITY-POLICY.md` part 3 for why per-human visibility
    is deliberately not built.
    """
    with store.read("runtime") as s:
        rows = _rows(s, "SELECT * FROM brain.queue_open_for(%s) ORDER BY priority DESC, "
                        "surfaced_at NULLS LAST", (who,))
        total = int(s.scalar("SELECT count(*) FROM brain.queue_open") or 0)
    return rows, total


# ------------------------------------------------------------------ the falsifier

def acted_on(min_raised: int = 1) -> dict:
    """The acted-on rate per template. Reported even when it is bad, especially when it is bad.

    Under roughly 30 percent this whole layer is cut back to events plus paging, which is stated
    in PLAN.md and is this lane's falsifier. Per template, because pruning should target a dead
    playbook rather than condemn the lane.

    ------------------------------------------------------------------------------------------
    THE DECIDED GUARD. Task 0382, from a defect lane A found while using the layer on 2026-08-27
    and the commander reproduced in one command.
    ------------------------------------------------------------------------------------------
    This function guarded `raised == 0` and stopped there, so the instant a recommendation existed
    and nobody had decided it, the verb printed a DEFINITE verdict from nothing:

        recommendations raised 3, accepted 0, decided 0
        acted-on rate: 0.0  (falsifier: below 30% this layer is cut back to events plus paging)
          BELOW THE FALSIFIER: cut this layer back to events plus paging

    Every number there is correct and the sentence is false. **0 decided out of 3 raised is not a
    0 percent acted-on rate. It is an absent measurement.** Three recommendations sat open in the
    operator's queue, waiting for the one act that is his alone, and the layer read that as
    evidence against itself.

    THIS WAS STRICTLY WORSE THAN THE ZERO IT REPLACED. Before any recommendation existed the
    falsifier could not fire in either direction and everybody knew it -- the 2026-08-16 UX review
    recorded exactly that, "the layer correctly refuses to render 0 %". The moment lane A raised
    three, the same code began printing a confident recommendation to CUT THE LAYER, and an agent
    reading that output would have been right to act on it.

    WHAT THE GUARD IS AND WHAT IT IS NOT. It is a denominator guard on `decided`, printing NOT
    MEASURABLE with the reason. It is NOT a widened threshold (0.30 is untouched, and it is
    PLAN.md's number), and it is NOT a suppressed line -- the row still prints, with what it
    compared beside it.

    WHAT IS DELIBERATELY LEFT OPEN, because it is not a lane's to settle. `acted_on_rate` stays
    `accepted / RAISED`, which is what PLAN.md's falsifier has always been read against, and this
    change does not touch it. But `accepted / raised` and `accepted / DECIDED` diverge whenever
    the queue is deep: 3 accepted of 5 decided of 100 raised reads 3 percent one way and 60
    percent the other, and only one of those is a statement about whether the playbooks are any
    good. Both numbers are returned here and both are printed, so the gap is visible rather than
    resolved by whoever reads it first. Which one the falsifier means is a question for the
    operator and is filed as its own row.
    """
    with store.read("runtime") as s:
        rows = _rows(s, "SELECT * FROM brain.queue_acted_on ORDER BY raised DESC")
        total = s.one("""SELECT count(*) AS raised,
                                count(*) FILTER (WHERE state = 'accepted') AS accepted,
                                count(*) FILTER (WHERE state IN ('accepted','rejected')) AS decided
                           FROM brain.recommendation""")
    raised = int(total["raised"] or 0)
    accepted = int(total["accepted"] or 0)
    decided = int(total["decided"] or 0)
    rate = (accepted / raised) if raised else None
    # The second reading, over the decisions that actually happened. Reported, never substituted.
    decided_rate = (accepted / decided) if decided else None
    measurable = raised > 0 and decided > 0

    if not raised:
        verdict = ("no data yet: nothing has been recommended, so the rate is undefined "
                   "rather than zero")
    elif not decided:
        verdict = (f"NOT MEASURABLE: {raised} recommendation(s) raised and 0 decided, so 0 "
                   f"comparisons were made. This is NOT a 0 percent acted-on rate and NOT the "
                   f"last number you saw -- an undecided recommendation is not a refused one. "
                   f"The falsifier cannot fire in either direction until the operator decides "
                   f"something; do not cut this layer against this output.")
    elif rate is not None and rate < 0.30:
        verdict = ("BELOW THE FALSIFIER: cut this layer back to events plus paging")
    else:
        verdict = "above the falsifier threshold"

    return {"per_template": [r for r in rows if int(r["raised"]) >= min_raised],
            "raised": raised, "accepted": accepted, "decided": decided,
            "still_open": raised - decided,
            "acted_on_rate": round(rate, 4) if rate is not None else None,
            "decided_rate": round(decided_rate, 4) if decided_rate is not None else None,
            "measurable": measurable,
            "falsifier_threshold": 0.30,
            "verdict": verdict}


def override_after_default() -> dict:
    """Per producer: how often the operator reversed what silence shipped.

    It needs no new mechanism. `reanswer` sets `question.amended_at`, so an amended answer on a
    question whose default fired IS the override. If a producer's defaults are bad, this says so
    before trust erodes quietly.
    """
    with store.read("runtime") as s:
        rows = _rows(s, "SELECT * FROM brain.queue_default_override ORDER BY defaults_fired DESC")
        fired = s.scalar("SELECT count(*) FROM brain.queue_default_event WHERE fired_at IS NOT NULL")
        # The property is about GATED items. An unflagged task's default is allowed to act --
        # that is what "silence is a usable answer" means for reversible work. Reporting every
        # act-shaped default as a breach would make the check cry wolf on the normal case and
        # nobody would read it by the second week.
        # THE BREACH IS NOT READ OFF THE CLASSIFIER'S VERDICT (task 0145). `brain.queue_default_
        # breach` is `null_branch = false` OR an independent scan of the shipped text finding an
        # act verb, so a wrong classifier no longer hides a breach from the metric that exists to
        # catch it. On 2026-08-16 the old query asked only `null_branch = false`, the classifier
        # had said `true` for a default reading *email the client the quote*, and this number
        # came back clean while an external act had shipped. A metric that cannot disagree with
        # the thing it measures is not a metric.
        risky = _rows(s, """SELECT question_id, work_item_id, producer, default_text,
                                   external, canon_touching, null_branch, shipped_act_verbs,
                                   self_certifying
                              FROM brain.queue_default_breach""")
        acting = int(s.scalar("""SELECT count(*) FROM brain.queue_default_event
                                  WHERE fired_at IS NOT NULL AND null_branch = false""") or 0)
        # Should always be 0: the CHECK on queue_default_event makes it unwritable. Reported
        # anyway, because "it cannot happen" is the sentence this lane keeps being wrong about,
        # and a zero that is measured is worth more than a zero that is assumed.
        lying = int(s.scalar("""SELECT count(*) FROM brain.queue_default_event
                                 WHERE fired_at IS NOT NULL AND null_branch
                                   AND cardinality(shipped_act_verbs) > 0""") or 0)
    unflagged_acting = max(0, acting - sum(1 for r in risky if not r["null_branch"]))
    return {"per_producer": rows, "fired": int(fired or 0),
            "non_null_branch_fired": risky,
            "self_certifying_null_branches": lying,
            "acting_defaults_on_unflagged_items": unflagged_acting,
            # THE CLAIM IS BOUNDED BY WHAT THE CHECK CAN SEE, and saying so is the point of the
            # whole task. Reading it as "no act shipped" is the same mistake in a different place:
            # the breach view catches a classifier that disagrees with the act vocabulary, and it
            # cannot catch a gap IN that vocabulary.
            #
            # THE WORD "BOUNDED" IS LOAD-BEARING AND REPLACED "BY CONSTRUCTION" ON PURPOSE (task
            # 0154). Migration 0011 closed the six phrasings task 0145 measured ("hold; get the
            # cheque to Mick") by refusing every word it does not recognise, and the brief that
            # proposed it claimed that made the property true BY CONSTRUCTION. It does not: the
            # same task then broke the shape with twenty strings built only from allowed words,
            # and one of them, `no action; all of it`, is still accepted today. So the bound is
            # stated in the same breath as the claim. This system is allowed to say what it has
            # measured and not allowed to say what it wishes were true, and the sentence that
            # read as proof while an external act had shipped is the reason that rule exists.
            "claim": (f"on flagged items, no default that fired named an act verb or a word this "
                      f"gate does not recognise, checked against the shipped text and not only "
                      f"against the classifier's verdict ({unflagged_acting} acting default(s) "
                      f"fired on unflagged items, which is allowed). This is bounded, not true "
                      f"by construction: one phrasing is measured as still accepted, `no action; "
                      f"all of it`, and this check cannot see a gap in its own word lists"
                      if not risky else
                      f"{len(risky)} default(s) that fired on FLAGGED items were not a null "
                      f"branch"
                      + (f", {lying} of them recorded as null branches while naming an act "
                         f"(the record certifying its own breach)" if lying else "")
                      + f". Investigate: this is the property the gate exists to hold.")}


# ------------------------------------------------------------------ points, read only

def points(ledger: str | None = None) -> dict:
    """`metrics/effectiveness-points.md`, read only, sliced human/ai/hybrid, framed neutral.

    NO SECOND SCORE, no streak, no leaderboard. The operator's own doctrine
    (`concepts/points-orientation-currency.md`) states plainly that points are "explicitly not a
    value target", and `polarity` on the metric node is `neutral`, not `higher-better`. A rising
    count not matched by passing wager verdicts is busywork, and a second score invented here
    would be the Goodhart failure the doctrine names, shipped by the lane that quoted it.

    QUEUE ZERO DOES NOT CELEBRATE. It is not in this function because it is not in this system.
    """
    root = Path(os.environ.get("BRAIN_ROOT")
                or "/mnt/c/Users/you/repos/your-brain")
    path = Path(ledger) if ledger else root / "departments/chief-of-staff/points-ledger/earn-events.jsonl"
    if not path.exists():
        return {"ledger": str(path), "exists": False, "events": 0, "note":
                "no ledger on disk. A null is not a zero: this reports absence, not a score of 0."}
    events, bad = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            bad += 1
    by_actor = {}
    for e in events:
        a = e.get("actor_type") or "unrecorded"
        slot = by_actor.setdefault(a, {"events": 0, "points": 0})
        slot["events"] += 1
        slot["points"] += int(e.get("points_awarded") or 0)
    by_dept = {}
    for e in events:
        d = e.get("owning_department_id") or "unrecorded"
        by_dept[d] = by_dept.get(d, 0) + int(e.get("points_awarded") or 0)
    last = max((e.get("earned_at") or "" for e in events), default="")
    return {
        "ledger": str(path), "exists": True, "events": len(events), "unparseable": bad,
        "points": sum(int(e.get("points_awarded") or 0) for e in events),
        "by_actor_type": by_actor, "by_department": by_dept, "last_earned_at": last,
        "polarity": "neutral",
        "framing": ("Diagnostic, not a target. More points is not better; a rising count not "
                    "matched by passing wager verdicts is busywork."),
        "history_warning": (
            f"the ledger holds {len(events)} events, total, ever, last {last[:10] or 'never'}. "
            f"Do not build a surface that implies a rich history exists."),
    }


# ------------------------------------------------------------------ doctor

def doctor() -> dict:
    """Findings, not decoration. Every check here exists because its absence hides something.

    The anti-graveyard is the theme: a queue's real failure is not a bad ranking, it is items
    that leave the count and never come back.
    """
    now = _now()
    findings = []
    with store.read("runtime") as s:
        dead = _rows(s, """SELECT d.*, now() - d.created_at AS age
                             FROM brain.queue_defer d
                            WHERE d.woke_at IS NULL AND d.kind = 'until-event'
                              AND d.created_at < now() - make_interval(days => %s)""",
                     (DEAD_WAKE_DAYS,))
        for d in dead:
            findings.append({
                "kind": "dead-wake-condition", "severity": "high",
                "subject": f"{d['source_type']} {d['source_id']}",
                "detail": (f"deferred until event {d['wake_event_type']!r} on "
                           f"{d['created_at']:%Y-%m-%d} and it has not fired in "
                           f"{DEAD_WAKE_DAYS}+ days. A wake condition that never fires is a "
                           f"graveyard with a wake condition on it.")})

        # SEVERITY DROPPED FROM MEDIUM TO LOW, and the sentence rewritten, because task 0158
        # changed what this means. It used to describe an item stranded outside the queue: the
        # read suppressed on `woke_at IS NULL` alone, so a passed wake time held the item out
        # forever. The read now honours `wake_at`, so the item is back on its tier and the
        # operator has already seen it. What is left is a bookkeeping gap -- an open row with no
        # `woke_by` -- and reporting that at the same severity as a genuine graveyard would train
        # the reader to skim past the check that still catches one.
        stale = _rows(s, """SELECT d.* FROM brain.queue_defer d
                             WHERE d.woke_at IS NULL AND d.kind = 'until-time'
                               AND d.wake_at < now()""")
        for d in stale:
            findings.append({"kind": "defer-past-its-wake-time", "severity": "low",
                             "subject": f"{d['source_type']} {d['source_id']}",
                             "detail": f"wake time {d['wake_at']:%Y-%m-%d %H:%M} has passed, so "
                                       f"the item is back on its tier and the queue is correct. "
                                       f"The defer row is still open with no `woke_by`: nothing "
                                       f"closed it, because nothing in this system runs `queue "
                                       f"wake` on a schedule. The audit trail is short one row, "
                                       f"the operator is not short an item."})

        # Flags inherit by OR and may be RAISED at any time, so a default that was legal when it
        # was written can become act-shaped on a task that never changed. The write-time trigger
        # cannot see this; nothing else would.
        # READ THE GATED VERDICT (task 0154). The rows this finding is about are gated BY
        # DEFINITION, and since migration 0011 the rule that governs a gated row is
        # `null_branch_gated`. Asking `null_branch` here would report a row as fine while the
        # fire path refuses it, which is the same class of mistake as the D9 measurement agreeing
        # with the bug: a check that cannot disagree with what it checks.
        now_illegal = _rows(s, """SELECT * FROM brain.queue_pending_default_residue
                                   WHERE gated AND NOT null_branch_gated""")
        for r in now_illegal:
            # Name the words (task 0145, extended by 0154). A critical finding that says
            # "act-shaped" reads as the gate being fussy; one that says "you wrote `send`" reads
            # as the gate being right, and it lets the operator check the finding against the text
            # instead of trusting it. Three reasons, most specific evidence first.
            acts = (f" It names: {', '.join(r['act_verbs'])}." if r["act_verbs"]
                    else f" It uses words this gate does not recognise: {', '.join(r['residue'])}."
                    if r["residue"]
                    else f" It opens a clause with: {', '.join(r['bare_imperatives'])}."
                    if r["bare_imperatives"]
                    else " It states no null branch at all, so it cannot be classified.")
            findings.append({
                "kind": "default-now-act-shaped", "severity": "critical",
                "subject": f"{r['question_id']} on {r['work_item_id']}",
                "detail": (f"a hard flag was raised after this default was written. Silence "
                           f"would ship {r['default_text']!r} on a flagged task.{acts} `queue "
                           f"default fire` refuses it, so nothing will ship, but it needs an "
                           f"answer.")})

        declared = _rows(s, """SELECT id, text, produced_by FROM brain.recommendation
                                WHERE requires_human = false""")
        for r in declared:
            findings.append({
                "kind": "requires-human-false-declared", "severity": "low",
                "subject": f"recommendation {r['id']}",
                "detail": ("a producer declared this needs no human. It changes nothing -- "
                           "`recommend accept` requires a human decider whatever the column "
                           "says -- and it is counted here so the claim is visible.")})

        orphan = _rows(s, """SELECT q.id, q.asked_by FROM brain.question q
                              LEFT JOIN brain.queue_default_event e ON e.question_id = q.id
                             WHERE q.answered_at IS NOT NULL
                               AND q.answer = q.default_if_unanswered
                               AND coalesce(btrim(q.default_if_unanswered), '') <> ''
                               AND e.question_id IS NULL""")
        for r in orphan:
            findings.append({
                "kind": "default-fired-without-a-record", "severity": "medium",
                "subject": r["id"],
                "detail": ("this question was answered with exactly its own default and has no "
                           "queue_default_event row. Either a human typed the default verbatim, "
                           "or a fire landed and its record did not. The override-after-default "
                           "rate is short one row either way.")})

        bumps = _bumps(s)
        dag = _dag(s)
    live_bumped = 0
    for key, rows in bumps.items():
        total, live = ranking.bump_contribution(rows, now=now)
        if live:
            live_bumped += 1
    if live_bumped > SECOND_QUEUE_BUMPS:
        findings.append({
            "kind": "bumps-have-become-a-second-queue", "severity": "medium",
            "subject": f"{live_bumped} items",
            "detail": (f"{live_bumped} items carry a live bump. Beyond {SECOND_QUEUE_BUMPS} "
                       f"simultaneous bumps this is a second queue the score no longer governs. "
                       f"Say so plainly rather than absorbing the pattern.")})

    for cyc in dag.cycles:
        findings.append({"kind": "dependency-cycle", "severity": "high",
                         "subject": " -> ".join(cyc),
                         "detail": ("depends_on contains a cycle. The unblock walk contributes "
                                    "zero for the back edge so the ranking still terminates, "
                                    "but the cycle is a data error and both items will read as "
                                    "blocked forever.")})
    return {"findings": findings, "checked_at": now,
            "checks": ["dead-wake-condition", "defer-past-its-wake-time",
                       "default-now-act-shaped", "requires-human-false-declared",
                       "default-fired-without-a-record", "bumps-have-become-a-second-queue",
                       "dependency-cycle"]}


def tuning_recommendations(days: int = 14, threshold: int = 3) -> list:
    """When the same class is bumped three times in a fortnight, the weights are wrong.

    Surfaced as a recommendation the operator decides on, rather than absorbed into the weights
    silently. A model that quietly retunes itself toward whatever the operator bumped is a model
    that has stopped being a second opinion.
    """
    since = _now() - timedelta(days=days)
    with store.read("runtime") as s:
        rows = _rows(s, """SELECT coalesce(item_class, '(unclassified)') AS item_class,
                                  count(*) AS n, avg(delta) AS avg_delta
                             FROM brain.queue_bump
                            WHERE created_at >= %s
                            GROUP BY 1 HAVING count(*) >= %s""", (since, threshold))
    return [{"item_class": r["item_class"], "bumps": int(r["n"]),
             "avg_delta": float(r["avg_delta"]),
             "text": (f"{r['item_class']} has been bumped {r['n']} times in {days} days "
                      f"(avg {float(r['avg_delta']):+.2f}). The ranking is systematically wrong "
                      f"for this class; tune the weights rather than bumping it again.")}
            for r in rows]
