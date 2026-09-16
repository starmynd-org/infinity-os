"""The state changes, one function each, registered on the narrow waist.

Six verbs, split by what actually changes in the world:

    budget set       the ceiling changes
    budget charge    the meter advances
    budget stop      a scope becomes stopped        (operator's act, or the meter's)
    budget resume    a scope becomes unstopped      (only ever an operator's act)
    budget note      something happened that is worth a record and stops nothing
    budget outcome   an act's observed result is attached to the incident that predicted it

Three of those (`set`, `stop`, plus `status`, which is a read) are the surface my brief names.
The other three are the mechanism underneath: without a metering verb the ceiling has nothing to
compare against, without a non-stopping incident verb a warning has to be written as a stop, and
without an outcome verb a row would claim a process died before anyone looked. Each is registered
once and every surface calls it; none of them is a private path.

The one ordering rule in this file, and it is the same rule as "the port registry row is written
BEFORE the first bind": **the incident is recorded before the signal is sent.** A crash between
the two leaves a stop that is recorded but not carried out, which the next dispatch still refuses
because the gate is derived from spend. The other order leaves money spent, a process dead, and no
record of why, which reads as a mystery task failure and charges the lane an attempt it did not
earn.
"""

from __future__ import annotations

from decimal import Decimal

import store

# Scope shapes, in the order the gate should report them: the widest brake first.
#
# `project` was added by task 0430 and sits between lane and work_item because that is where it
# belongs in a refusal message, not because it nests cleanly: a project SPANS lanes, which is the
# whole reason it had to exist. The operator's incident (web/MUST-NOT-BUILD.md item 3) is that he
# wanted one project to rest and had only a lane switch, so three projects in `exec` shared one
# brake and a project across two lanes had none.
SCOPES = ("fleet", "agent", "lane", "project", "work_item")

# The scopes a CEILING may be set on, which is NOT the same set and must not be allowed to drift
# back into being the same set. `project` is a hold-only scope: brain.budget_charge carries
# agent/lane/work_item dimensions and no project, so a project ceiling would meter 0.00 against
# any limit forever and read as a healthy project that never spends. The database refuses the row
# outright (`budget_policy_no_project_ceiling`, migration 45); this tuple is the same refusal one
# layer up, where the error message can say why.
CEILING_SCOPES = ("fleet", "agent", "lane", "work_item")

PERIODS = ("day", "rolling_24h", "total")


class BudgetError(RuntimeError):
    """A budget verb refused. Raised rather than returned so no caller can ignore it."""


def _check_scope(scope_type: str, scope_id: str) -> str:
    if scope_type not in SCOPES:
        raise BudgetError(f"unknown scope {scope_type!r}. One of: {', '.join(SCOPES)}")
    if scope_type == "fleet":
        if scope_id:
            raise BudgetError("a fleet policy takes no scope_id; a scoped fleet is a second fleet")
        return ""
    if not scope_id:
        raise BudgetError(
            f"scope {scope_type!r} needs a scope_id (which agent, lane, project or item)")
    return scope_id


def _check_ceiling_scope(scope_type: str) -> None:
    """A ceiling may not be set on a scope the meter cannot measure.

    Raised rather than silently accepted, and the reason is the whole of `CEILING_SCOPES` above:
    an accepted project ceiling is a row in `brain.budget_state` reporting spend 0.00 against the
    operator's limit, in every window, forever. `over_limit` false, `stopping` false, no warn and
    no breach -- a brake that cannot fire, presented as a brake that is not firing.

    Migration 45 makes the row unstorable at the database, which is the guarantee. This function
    exists so the operator gets a sentence instead of a CheckViolation.
    """
    if scope_type in SCOPES and scope_type not in CEILING_SCOPES:
        raise BudgetError(
            f"{scope_type!r} is a HOLD scope, not a ceiling scope. `budget stop {scope_type} "
            f"<id>` latches it and only `budget resume` lifts it, which is the control that "
            f"exists. A ceiling cannot be set on it: brain.budget_charge carries agent, lane and "
            f"work_item and no {scope_type}, so the spend would meter 0.00 against your limit in "
            f"every window and the brake would never fire. Refusing is fail-closed.")


# --------------------------------------------------------------------------- the ceiling


@store.transition("budget set")
def budget_set(ctx, *, scope_type: str, scope_id: str = "", limit_usd, period: str = "day",
               warn_percent: int = 80, hard_stop_enabled: bool = True, set_by: str = "",
               note: str = "", produced_by: str | None = None,
               produced_by_ref: str | None = None,
               resolution_status: str | None = None) -> dict:
    """Set the ceiling for one scope and period. Supersedes rather than overwrites.

    The prior policy is retired, not updated in place, so `budget status --history` can still say
    what the ceiling was at the moment of a breach. A ceiling raised after the fact must never
    rewrite the record of the breach it was raised in response to.

    THE LINEAGE TRIPLE, AND WHY IT IS THREE KWARGS IN EVERY VERB IN THIS FILE.
    `produced_by` on its own has two readings and no way to tell them apart: "this resolved to
    that id" and "nobody looked". The other two columns split them, and the ambiguous case --
    a reference that resolved to several candidates -- has no representation at all without
    them, because it is a NULL producer that is NOT the same thing as an unattributed row.
    The triple is built once, in `brain_adapter.store_join.lineage_columns(resolution)`, and
    splatted in:

        store.apply("budget set", scope_type=..., **lineage_columns(res))

    No verb in this lane assembles it, validates it, or defaults any part of it. Passing none
    of the three still means "no resolution was attempted", which is what every existing caller
    keeps getting. `budget_policy_lineage_coherent` is the judge.
    """
    scope_id = _check_scope(scope_type, scope_id)
    # Before the period, so a project ceiling is refused for the reason it is actually refused
    # for rather than for a typo in `--period` that would never have mattered.
    _check_ceiling_scope(scope_type)
    if period not in PERIODS:
        raise BudgetError(f"unknown period {period!r}. One of: {', '.join(PERIODS)}")
    limit = Decimal(str(limit_usd))
    if limit < 0:
        raise BudgetError("a negative ceiling is not a ceiling")
    if not 0 < int(warn_percent) <= 100:
        raise BudgetError("warn_percent must be in 1..100")

    prior = ctx.one(
        "UPDATE brain.budget_policy SET state = 'retired', retired_at = now(), retired_by = %s "
        " WHERE state = 'active' AND scope_type = %s AND scope_id = %s AND period = %s "
        "RETURNING id, limit_usd, warn_percent, hard_stop_enabled",
        (set_by, scope_type, scope_id, period),
    )

    # A superseding policy keeps the original window start, so raising a ceiling mid-window does
    # not silently reset a 'total' meter to zero and hand back a budget already spent.
    effective_from = None
    if prior:
        effective_from = ctx.scalar(
            "SELECT effective_from FROM brain.budget_policy WHERE id = %s", (prior["id"],)
        )

    row = ctx.one(
        "INSERT INTO brain.budget_policy "
        "  (scope_type, scope_id, period, limit_usd, warn_percent, hard_stop_enabled, "
        "   set_by, note, produced_by, produced_by_ref, resolution_status, actor_type, "
        "   effective_from) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ai', COALESCE(%s, now())) "
        "RETURNING id, scope_type, scope_id, period, limit_usd, warn_percent, "
        "          hard_stop_enabled, effective_from, set_at",
        (scope_type, scope_id, period, limit, int(warn_percent), bool(hard_stop_enabled),
         set_by, note, produced_by, produced_by_ref, resolution_status, effective_from),
    )
    row["superseded_policy_id"] = prior["id"] if prior else None
    row["acknowledged_incidents"] = _acknowledge(ctx, scope_type, scope_id, set_by,
                                                 f"ceiling set to {limit} by {set_by or '?'}")
    return row


def _acknowledge(ctx, scope_type: str, scope_id: str, by: str, ref: str) -> list:
    """Close open `hard_stop` records for a scope whose ceiling the operator has just revisited.

    A hard stop is derived from the meter, so the operator's answer to one is to change the
    ceiling (or to leave it and let the window roll). Revisiting the ceiling is that answer, and
    it closes the record. A `manual_stop` is deliberately NOT closed here: "I stopped the fleet"
    and "I changed the ceiling" are two decisions, and collapsing them means one of them happens
    by accident. `budget resume` is the only thing that lifts a manual stop.
    """
    rows = ctx.execute(
        "UPDATE brain.budget_incident SET cleared_at = now(), cleared_by = %s, clear_ref = %s "
        " WHERE cleared_at IS NULL AND kind = 'hard_stop' "
        "   AND scope_type = %s AND scope_id = %s RETURNING id",
        (by, ref, scope_type, scope_id),
    )
    return [r["id"] for r in rows]


@store.transition("budget unset")
def budget_unset(ctx, *, scope_type: str, scope_id: str = "", period: str | None = None,
                 by: str = "", reason: str = "") -> dict:
    """Remove a ceiling. Retires, never deletes.

    A ceiling that can only be lowered and never removed is a landmine: the way to "turn it off"
    becomes setting it to a very large number, and a very large number is indistinguishable in
    the data from a deliberate ceiling. The retired row stays so a past breach keeps saying what
    the ceiling was when it happened.
    """
    scope_id = _check_scope(scope_type, scope_id)
    rows = ctx.execute(
        "UPDATE brain.budget_policy SET state = 'retired', retired_at = now(), retired_by = %s "
        " WHERE state = 'active' AND scope_type = %s AND scope_id = %s "
        "   AND (%s IS NULL OR period = %s::brain.budget_period) "
        "RETURNING id, period, limit_usd",
        (by, scope_type, scope_id, period, period),
    )
    return {"retired": [r["id"] for r in rows], "count": len(rows), "detail": rows,
            "acknowledged_incidents": _acknowledge(
                ctx, scope_type, scope_id, by, f"ceiling removed by {by or '?'}: {reason}")}


# --------------------------------------------------------------------------- the meter


@store.transition("budget charge")
def budget_charge(ctx, *, usd, source_ref: str, source: str = "run_json", agent: str = "",
                  lane: str = "", work_item_id: str | None = None, run_id: int | None = None,
                  session_id: str = "", note: str = "", truth_up: bool = False,
                  produced_by: str | None = None, produced_by_ref: str | None = None,
                  resolution_status: str | None = None) -> dict:
    """Record spend. Idempotent on `(source, source_ref)`.

    Idempotency is not a nicety here. Reconciliation re-reads a run's json, and a long run is
    charged incrementally while it is still running. A brake that double-counts stops a healthy
    fleet exactly as expensively as one that under-counts lets it burn, so the second charge of
    the same reference is a no-op that says so rather than an error or a silent duplicate.

    A ZERO charge is recorded, not skipped. A refusal costs nothing, and the way to prove that
    is a row saying 0.00, not the absence of a row.

    TRUTH-UP, which is what makes an incremental charge and a final one compose instead of
    double-counting. Since 0251 the guard derives a live run's spend from its own token stream and
    charges it in increments while the run is still going (`budget/price_card.py`). The runner
    then charges the engine's authoritative `total_cost_usd` at the end. Those are the same money.
    `truth_up=True` says so: `usd` is the AUTHORITATIVE TOTAL for this session, and what lands is
    the total minus everything already charged against that session id.

    Three properties worth stating, because each one is a way this could have gone wrong:

      * It is keyed on `session_id`, which is globally unique per engine session -- not on
        (task, attempt), which `reopen` makes repeat.
      * It still writes a row when the remainder is zero. That row is the evidence that the
        mid-run derivation landed exactly, and it is what a later audit reads instead of guessing.
      * If the prior charges EXCEED the authoritative total, the remainder clamps at zero and the
        overshoot is reported back rather than swallowed. `usd >= 0` is a CHECK on this table, so
        a refund is not expressible; over-derivation is a finding, and hiding it inside a negative
        that the database would reject anyway is how it would stop being one.

    With no session id there is nothing to key on -- charges cannot be attributed to a session
    that has no identity -- so the full amount is charged and the note says why. That is the codex
    path, which writes no stream-json and therefore has nothing to true up against.
    """
    amount = Decimal(str(usd))
    if amount < 0:
        raise BudgetError("a negative charge is a refund, which this ledger does not model")
    if not source_ref:
        raise BudgetError("every charge needs a source_ref; it is the idempotency key")

    authoritative, already, over = amount, Decimal(0), Decimal(0)
    if truth_up and session_id:
        # Excludes this very (source, source_ref) so that re-running the same truth-up is a
        # no-op through the ON CONFLICT below rather than subtracting its own earlier self.
        row = ctx.one(
            "SELECT COALESCE(sum(usd), 0)::numeric(12,6) AS usd FROM brain.budget_charge "
            " WHERE session_id = %s AND NOT (source = %s AND source_ref = %s)",
            (session_id, source, source_ref))
        already = Decimal(str((row or {}).get("usd") or 0))
        if already > amount:
            over = already - amount
            amount = Decimal(0)
        else:
            amount = amount - already
        note = (f"{note + ' ' if note else ''}truth-up: ${authoritative} billed for session "
                f"{session_id}, ${already} already metered, ${amount} remains"
                + (f". OVER-CHARGED by ${over}" if over else ""))
    elif truth_up:
        note = (f"{note + ' ' if note else ''}truth-up requested with no session id, so no prior "
                f"charge can be attributed to this run: charged in full")

    row = ctx.one(
        "INSERT INTO brain.budget_charge "
        "  (usd, agent, lane, work_item_id, run_id, session_id, source, source_ref, note, "
        "   produced_by, produced_by_ref, resolution_status, actor_type) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ai') "
        "ON CONFLICT (source, source_ref) DO NOTHING "
        "RETURNING id, usd, charged_at",
        (amount, agent, lane, work_item_id, run_id, session_id, source, source_ref, note,
         produced_by, produced_by_ref, resolution_status),
    )
    if row is None:
        prior = ctx.one(
            "SELECT id, usd, charged_at FROM brain.budget_charge "
            " WHERE source = %s AND source_ref = %s", (source, source_ref))
        return {"charged": False, "duplicate_of": prior["id"], "usd": prior["usd"],
                "charged_at": prior["charged_at"], "truth_up": truth_up,
                "authoritative_usd": authoritative, "already_charged_usd": already,
                "over_charged_usd": over}
    return {"charged": True, "charge_id": row["id"], "usd": row["usd"],
            "charged_at": row["charged_at"], "truth_up": truth_up,
            "authoritative_usd": authoritative, "already_charged_usd": already,
            "over_charged_usd": over}


# --------------------------------------------------------------------------- stopping


@store.transition("budget stop")
def budget_stop(ctx, *, scope_type: str, scope_id: str = "", kind: str = "manual_stop",
                by: str = "", reason: str = "", action_taken: str | None = None,
                detected_by: str = "manual", policy_id: int | None = None,
                spend_usd=0, limit_usd=None, percent_used=None, period: str | None = None,
                window_start=None, window_end=None, agent: str = "", lane: str = "",
                work_item_id: str | None = None, run_id: int | None = None,
                session_id: str = "", engine_pid: int | None = None,
                produced_by: str | None = None, produced_by_ref: str | None = None,
                resolution_status: str | None = None) -> dict:
    """A scope becomes stopped. Files the typed breach record.

    One transition for both causes, because "this scope has stopped spending" is one state change
    whether the operator typed it or the meter reached the ceiling. `kind` records which, and
    `detected_by` records how, so "we caught it before spending" stays measurable.

    Called BEFORE any signal is sent to a live process. See the module docstring.
    """
    scope_id = _check_scope(scope_type, scope_id)
    if kind not in ("manual_stop", "hard_stop"):
        raise BudgetError(f"{kind!r} does not stop anything; file it with `budget note`")
    if action_taken is None:
        action_taken = {"fleet": "fleet_stopped", "agent": "agent_stopped"}.get(
            scope_type, "dispatch_refused")

    row = ctx.one(
        "INSERT INTO brain.budget_incident "
        "  (kind, policy_id, scope_type, scope_id, period, window_start, window_end, "
        "   spend_usd, limit_usd, percent_used, action_taken, detected_by, "
        "   prescribed_disposition, agent, lane, work_item_id, run_id, session_id, engine_pid, "
        "   detail, produced_by, produced_by_ref, resolution_status, actor_type) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ai') "
        "RETURNING id, kind, occurred_at, action_taken, prescribed_disposition, cause",
        (kind, policy_id, scope_type, scope_id, period, window_start, window_end,
         Decimal(str(spend_usd)), limit_usd, percent_used, action_taken, detected_by,
         # NOT `fail` (the lane did nothing wrong) and NOT `reopen` (which returns the task to
         # the queue to be claimed and spent again -- the money retry loop this lane exists to
         # prevent). `block` needs a human, which is right: it is the human's money.
         "block" if work_item_id else "hold_dispatch",
         agent, lane, work_item_id, run_id, session_id, engine_pid, reason, produced_by,
         produced_by_ref, resolution_status),
    )
    if work_item_id:
        # `budget`, and MIGRATION 13 IS WHAT MAKES THAT LEGAL. Read that file before you touch
        # this line: `budget` is not in migration 1's `brain.thread.kind` CHECK, so against a
        # store older than 13 this raises CheckViolation and ROLLS THE WHOLE TRANSACTION BACK --
        # no incident row, no stop recorded, nothing for `budget halt` to find. That was live
        # for the whole of 2026-08-16 until D4 wired the brake (0110) and ran a real stop
        # against a real task; every test above this passed throughout, because none of them
        # passed a work_item_id that exists, so none of them reached this branch. D4 unblocked
        # it with `note`; task 0120 gave the store the word and this line went back to it.
        #
        # ONE kind for the whole family, not one per incident kind. `brain.budget_incident.kind`
        # already types hard_stop / manual_stop / blocked_dispatch under CHECKs, and a second
        # copy of that distinction here would be a second place for it to be got wrong. The
        # text leads with the incident kind, so the line still reads without a join, and
        # `kind = 'budget'` is the one filter that answers "did money touch this task".
        # Reasoning in full at migrations/0013_thread_budget_kind.sql.
        ctx.thread(work_item_id, "budget",
                   f"budget {kind} on {scope_type}"
                   f"{':' + scope_id if scope_id else ''}: {reason}", to_agent=agent)
    return row


@store.transition("budget resume")
def budget_resume(ctx, *, scope_type: str, scope_id: str = "", by: str = "",
                  reason: str = "") -> dict:
    """Lift open manual stops on a scope. The only way a manual stop ever ends.

    A rate-limit refusal clears itself when the window resets. A budget stop does not, and this
    verb is the whole of why: something has to be typed. `budget set` raising a ceiling clears a
    spend-derived stop (the meter is simply no longer over the line) but deliberately does NOT
    clear a manual one, because "I stopped the fleet" and "I raised the ceiling" are different
    decisions and collapsing them means one of them happens by accident.
    """
    scope_id = _check_scope(scope_type, scope_id)
    lifted = ctx.execute(
        "UPDATE brain.budget_incident SET cleared_at = now(), cleared_by = %s, clear_ref = %s "
        " WHERE cleared_at IS NULL AND kind = 'manual_stop' "
        "   AND scope_type = %s AND scope_id = %s "
        "RETURNING id, occurred_at",
        (by, reason, scope_type, scope_id),
    )
    if lifted:
        # DELIBERATELY NO LINEAGE TRIPLE, unlike every other INSERT in this file, and it is not
        # an omission. This row is not produced from a reference: it is the record of an
        # operator typing `budget resume`, so there is no ref to preserve and no resolution was
        # attempted. All three columns stay SQL NULL, which is the fourth state -- the same
        # thing `store_join.producer_stamp()` says for a row that never made a claim about the
        # brain. Adding kwargs here would offer a caller a way to attribute a human's act to a
        # brain node, which is the fabrication the whole triple exists to make impossible.
        ctx.execute(
            "INSERT INTO brain.budget_incident "
            "  (kind, scope_type, scope_id, action_taken, detected_by, detail, produced_by, "
            "   produced_by_ref, resolution_status, actor_type, cleared_at, cleared_by) "
            "VALUES ('resumed',%s,%s,'resumed','manual',%s,NULL,NULL,NULL,'human',now(),%s)",
            (scope_type, scope_id, reason or f"resumed by {by}", by),
        )
    return {"lifted": [r["id"] for r in lifted], "count": len(lifted)}


# --------------------------------------------------------------------------- records


@store.transition("budget note")
def budget_note(ctx, *, kind: str, scope_type: str, scope_id: str = "", policy_id: int | None = None,
                spend_usd=0, limit_usd=None, percent_used=None, period: str | None = None,
                window_start=None, window_end=None, action_taken: str = "none",
                detected_by: str = "charge", agent: str = "", lane: str = "",
                work_item_id: str | None = None, run_id: int | None = None,
                session_id: str = "", detail: str = "",
                produced_by: str | None = None, produced_by_ref: str | None = None,
                resolution_status: str | None = None) -> dict:
    """A typed incident that stops nothing: `warn`, `soft_breach`, `blocked_dispatch`,
    `meter_mismatch`.

    Separate from `budget stop` on purpose. A warning filed as a stop reads, a week later, as a
    fleet that halted; a stop filed as a warning reads as one that did not. The `kind` CHECKs in
    the migration refuse a row that claims a warning stopped a run.

    `meter_mismatch` (migration 21, task 0251) is the odd one out and deliberately so: it is the
    only kind here that is not about a ceiling. It says the two independent measurements of what a
    run cost -- the price card applied to the run's own token stream, and the engine's billed
    `total_cost_usd` -- disagree. No limit was crossed and nothing was stopped; what needs a human
    is deciding whether the card has gone stale or the stream under-reported, because those have
    opposite remedies. `budget selfcheck` is the only thing that files it.
    """
    if kind not in ("warn", "soft_breach", "blocked_dispatch", "meter_mismatch"):
        raise BudgetError(f"{kind!r} is a stop; file it with `budget stop`")
    scope_id = _check_scope(scope_type, scope_id)
    return ctx.one(
        "INSERT INTO brain.budget_incident "
        "  (kind, policy_id, scope_type, scope_id, period, window_start, window_end, spend_usd, "
        "   limit_usd, percent_used, action_taken, detected_by, prescribed_disposition, agent, "
        "   lane, work_item_id, run_id, session_id, detail, produced_by, produced_by_ref, "
        "   resolution_status, actor_type) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ai') "
        "RETURNING id, kind, occurred_at, action_taken, cause",
        (kind, policy_id, scope_type, scope_id, period, window_start, window_end,
         Decimal(str(spend_usd)), limit_usd, percent_used, action_taken, detected_by,
         "hold_dispatch" if kind == "blocked_dispatch" else "none",
         agent, lane, work_item_id, run_id, session_id, detail, produced_by, produced_by_ref,
         resolution_status),
    )


@store.transition("budget outcome")
def budget_outcome(ctx, *, incident_id: int, signal_sent: str = "", detail: str = "",
                   engine_pid: int | None = None) -> dict:
    """Attach what was observed to the incident that predicted it.

    The incident is written before the signal is sent, so until this lands the row states an
    intention. This is the verb that turns it into a report: which signal, whether the process
    died, and what it exited with.
    """
    row = ctx.one(
        "UPDATE brain.budget_incident "
        "   SET signal_sent = %s, "
        "       detail = CASE WHEN %s = '' THEN detail ELSE detail || E'\\n' || %s END, "
        "       engine_pid = COALESCE(%s, engine_pid) "
        " WHERE id = %s "
        "RETURNING id, kind, signal_sent, action_taken, detail",
        (signal_sent, detail, detail, engine_pid, incident_id),
    )
    if row is None:
        raise BudgetError(f"no incident {incident_id}")
    return row
