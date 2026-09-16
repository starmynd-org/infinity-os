"""Auto-acceptance: implemented, SHIPPED DISABLED, and measuring what it would have decided.

D00 contract rule 3. `done` means the agent reported it finished; acceptance is a separate act
and `reopen` is the rejection verb. The narrowing says acceptance is automatic for
non-`external`, non-`canon_touching`, **reversible**, DoD-passed items and human otherwise. It
narrows WHO accepts, never WHETHER it was recorded.

**It ships off.** Turning it on before the measurement converts unreliable self-reported
completion into fabricated completion at fleet scale: the agent says done, nothing checks, the
row says accepted, and the operator's queue looks finished. One week of measurement first, and
the measurement is the disagreement rate between what this module would have decided and what
the operator actually decided.

The flag is a `runtime_flag` row rather than a config key on purpose. A config key is edited by
whoever is editing config, which during a build is an agent; a `runtime_flag` write is a verb
call that lands on the thread and is visible in `swarm status`.
"""

from __future__ import annotations

import os

import store

from . import steering
from .transitions import VerbError

FLAG = "auto_accept_enabled"

# ------------------------------------------------------------------ property 4 of the overrule
#
# "A steered run is MARKED steered, and excluded from auto-accept eligibility and from the
# disagreement measurement. A run the operator steered is no longer an independent self-report --
# it is co-authored. Without this, the two-week measurement quietly measures his own steering back
# to him."  (V4 brief, which calls this the property easiest to forget.)
#
# It bites in three places and each is a different question:
#
#   would_accept()           would the rule accept this?      -> no, and it says why
#   measure_in_transaction() what does the trail record?      -> an EXCLUDED row, not a verdict
#   measurements()           what does the week score?        -> nothing on a steered task
#
# The third is not redundant with the second. The second stops a steered run's own `done` from
# creating a data point; the third stops an EARLIER, unsteered verdict on the same task from
# being paired with a human decision that is really about the steered work -- `measurements()`
# pairs a verdict with the first accept/reopen AFTER it, and that decision does not know which
# attempt it is about. Dropping the whole task is the conservative direction: it loses a data
# point rather than counting a co-authored one, and D00's named failure is the confidently-wrong
# number, never the missing one.
STEERED_REASON = "steered by the operator (co-authored, not an independent self-report)"

#: What lands on the thread instead of a verdict. It deliberately does NOT start with
#: `MEASURE_TAG`, so `measurements()` cannot read it as a verdict; it starts with the steering
#: tag, so a reader of the trail finds it beside the SEIZED row that caused it.
EXCLUDED_NOTE = (steering.EXCLUDED + "from the auto-accept measurement: this run was steered by "
                 "the operator, so its self-report is co-authored and is not an independent data "
                 "point. Auto-accept is not eligible on it either, whatever the flag says.")

# ------------------------------------------------------------------ the recorded verdict
#
# ONE definition of the measurement's wording, used by the writer AND by the reader, because a
# report that greps for a string the writer no longer emits is a report that silently scores
# nothing. Task 0139: the previous reader did not read this record at all -- it re-derived the
# verdict from `brain.auto_accept_candidate`, whose `accepted_at IS NULL` predicate the human's
# own acceptance mutates, so every acceptance scored as a `false_reject`.
#
# The DISABLED wording is byte-for-byte what shipped, so the measurements already on the thread
# (0027 and 0033 on the live store at the time of the fix) still score rather than being lost to
# a reformat.
MEASURE_TAG = "[auto-accept measurement, rule "
DISABLED_PREFIX = MEASURE_TAG + "DISABLED] "
ENABLED_PREFIX = MEASURE_TAG + "ENABLED] "
VERDICT_YES = "auto-accept WOULD have accepted this"
VERDICT_NO = "auto-accept would NOT have accepted this: "
VERDICT_ACTED = "auto-accept accepted this automatically"

# The thread kinds that carry a human verdict on finished work. `accept` is yes; `reopen` is the
# rejection verb (D00 rule 3). `cancel` is neither: the item was withdrawn, and scoring a
# withdrawal as a rejection would put dead items in the disagreement rate.
DECIDING_KINDS = ("accept", "reopen", "cancel")


def enabled() -> bool:
    """False unless the operator turned it on, and false if anything at all goes wrong.

    Fails closed twice over: the env override only ever turns it ON for a test, and an
    unreachable store reads as disabled rather than raising into a `done`.
    """
    if os.environ.get("ENGINE_AUTO_ACCEPT") == "1":
        return True
    try:
        with store.read("runtime") as s:
            return str(s.scalar("SELECT value FROM brain.runtime_flag WHERE key = %s",
                                (FLAG,)) or "").lower() == "true"
    except Exception:                                             # noqa: BLE001
        return False


def would_accept(task_id: str) -> dict:
    """What the rule WOULD decide, from `brain.auto_accept_candidate`.

    The view is D1's and it queries `reversibility`, which is the column D00 warns about by
    name: without it the eligibility test reads NULL, defaults conservative, nothing is ever
    eligible, and a week of measurement reports a perfect 0% disagreement rate from a test that
    evaluated nothing.

    STEERED RUNS ARE INELIGIBLE, and the term is applied HERE rather than added to the view.
    Two reasons, and the first is the smaller one: the view is DDL against the live database and
    an agent's write there is refused, so a term that lived only in the view would not exist
    until the operator ran it by hand. The second is that this is the same shape the module
    already uses for `enabled()` -- fail closed in Python, on a fact read from an append-only
    table -- and the exclusion has to hold in the transition that measures as well as in the
    reader that reports, which is two call sites and one view.
    """
    if steering.is_steered(task_id):
        return {"id": task_id, "eligible": False, "reasons": [STEERED_REASON]}
    with store.read("runtime") as s:
        row = s.one("SELECT * FROM brain.auto_accept_candidate WHERE id = %s", (task_id,))
        if row:
            return {"id": task_id, "eligible": True, "reasons": []}
        sig = s.one("SELECT external, canon_touching, reversibility FROM brain.work_item_signals "
                    "WHERE id = %s", (task_id,))
        state = s.one("SELECT state, accepted_at FROM brain.work_item WHERE id = %s", (task_id,))
    reasons = []
    if not sig or not state:
        return {"id": task_id, "eligible": False, "reasons": ["no such task"]}
    if sig["external"]:
        reasons.append("external")
    if sig["canon_touching"]:
        reasons.append("canon_touching")
    if sig["reversibility"] != "high":
        reasons.append(f"reversibility {sig['reversibility']} (needs reversible)")
    if state["state"] != "done":
        reasons.append(f"state {state['state']}")
    if state["accepted_at"] is not None:
        reasons.append("already accepted")
    return {"id": task_id, "eligible": False, "reasons": reasons}


@store.transition("accept work")
def accept_work(ctx, *, id, by, auto=False, as_operator=True):
    """Record acceptance. The ONLY writer of `accepted_at`, whoever decides.

    D00 rule 4: no agent self-approves canon. A canon-touching item is not eligible for the
    automatic path at all, and the `auto` caller is refused here as well as filtered by the
    view, because a gate that exists in one place is a gate one bug away from being absent.

    Task 0141 added the `by` guards. Until it, this verb had exactly one door -- the console,
    which hardcodes `operator` (`web/app.py:59`) -- so trusting `by` cost nothing. The moment it
    grew a second door (`swarm accept-work`), `by` became a string the caller chooses, and rule
    4 was a comment rather than a check. It is checked here, in the transition, rather than at
    the new door, because a guard on one surface is a guard the next surface does not have.

    ----------------------------------------------------------------------------------------
    THE DECIDER IS A DATABASE LOGIN. Lane E, row 0384, migration 36. This is the half task
    0141's guards could not reach, and migration 22 named it and left it.
    ----------------------------------------------------------------------------------------
    The test below it -- "is `by` a row in `brain.agent`" -- FAILS OPEN, and how far open was
    measured rather than reasoned about. From `brain_runtime`, the credential every agent
    surface in this fleet holds, on scratch `brain_lane_e` on 2026-08-27:

        6 of 6 forged names accepted, including 'zzz-not-a-person' and 'operator'
        the same 6 names against brain.recommendation.decided_by: 0 of 6

    Both columns record "a human read this and decided". One asked the database and one asked
    the caller. Migration 22's own header says so and files the repair as task 0275, which was
    cancelled with the queue reset, leaving its paragraph as the only pointer.

    So this verb now asks `brain.current_human()` -- migration 20's answer from `session_user`,
    fixed at authentication and unreachable from SQL -- and refuses a `by=` that disagrees with
    it rather than recording either one. `as_operator=True` is what makes `store.apply` open a
    human login; a process without one gets `StoreConfigError` from `_connect`, and one that
    somehow connected as `brain_runtime` anyway is refused by migration 36's trigger. Two
    mechanisms, the database's load-bearing, exactly as `recommend accept` has done since 0290.

    The `by=` check is `!=`, not "is a human at all". Under row 0386 decision 2 there are up to
    twelve human logins, and a human who could record a COLLEAGUE's acceptance is the same
    forgery one seat over.

    The refusals are `VerbError`, which is a `RuntimeError` subclass, so a caller catching the
    old exception type still catches these; the CLI gets the exit code instead of a traceback.
    """
    by = (by or "").strip()
    if not by:
        raise VerbError(
            f"refusing to accept {id} with no acceptor. `accepted_by` is the whole record of who "
            f"decided, and D00 rule 3 makes acceptance the act that is separate from the agent's "
            f"`done`. An acceptance attributed to nobody is a `done` with a second timestamp.",
            code=6)
    sig = ctx.one("SELECT external, canon_touching, reversibility FROM brain.work_item_signals "
                  "WHERE id = %s", (id,))
    if not sig:
        raise VerbError(f"no such task: {id}")
    # `claim` writes a row here (transitions.py:548) and so does `heartbeat`, so every name the
    # fleet has ever taken work under is in `brain.agent` -- and the operator, who claims
    # nothing, is not. That makes the membership test a real question about the acceptor rather
    # than a name blocklist: an agent accepting ANY agent's work is the self-approval D00 rule 4
    # prohibits, one call removed, which is the same argument `mcp/tools.py` gives for having no
    # accept tool at all.
    if ctx.one("SELECT 1 FROM brain.agent WHERE name = %s", (by,)):
        raise VerbError(
            f"refusing to record {by} as the acceptor of {id}: {by} is a fleet agent, and the "
            f"decider on every acceptance is a human (D00 rule 4). `done` is the agent's report "
            f"that it finished; acceptance is the separate act that says a human read it. Accept "
            f"from the console, or run `swarm accept-work {id}` from your own shell.",
            code=6)
    if auto and (sig["external"] or sig["canon_touching"] or sig["reversibility"] != "high"):
        raise VerbError(
            f"{id} is not eligible for automatic acceptance "
            f"(external={sig['external']} canon_touching={sig['canon_touching']} "
            f"reversibility={sig['reversibility']}). A human decides this one.")

    # THE GATE. Asked of the DATABASE, never taken from the caller's string. Everything above is
    # a test on a name the caller chose. See the docstring for what that cost, measured.
    the_human = ctx.scalar("SELECT brain.current_human()")
    if not the_human:
        raise VerbError(
            f"refusing to accept {id} from a connection the database does not know as a human. "
            f"Acceptance is the act that turns an agent's self-report into recorded completion, "
            f"and until migration 36 the only identity test here was `is this name in "
            f"brain.agent`, which fails open: every name that is not an agent passed, and 6 of 6 "
            f"forged names were accepted from brain_runtime when it was measured. Call this as a "
            f"human (`as_operator=True`, which opens a human login); `brain.current_human()` "
            f"returned NULL for this session, which is what it returns for brain_runtime, the "
            f"login every agent surface connects as.", code=6)
    if by != the_human:
        raise VerbError(
            f"this call says the acceptor is {by!r} and the database says the connection is "
            f"{the_human!r}. Refusing rather than recording either one: accepted_by is the "
            f"record of who decided, and it is the input to the auto-accept measurement. There "
            f"are up to twelve human logins on this instance, so recording a colleague's "
            f"acceptance is the same forgery one seat over.", code=6)
    # The DATABASE'S answer, not `by`. The two are equal by the refusal above; writing
    # `the_human` is what makes that true by construction rather than by the check staying there.
    by = the_human
    ctx.execute("UPDATE brain.work_item SET accepted_at = now(), accepted_by = %s WHERE id = %s",
                (by, id))
    ctx.actor = by
    ctx.thread(id, "accept", f"accepted by {by}" + (" (automatic)" if auto else ""))
    return {"id": id, "accepted_by": by, "auto": auto}


@store.transition("auto accept flag")
def set_flag(ctx, *, on: bool, by="operator", note=""):
    """Write `auto_accept_enabled`. The narrow waist's only path to the switch.

    Task 0139's second finding: the flag had NO writer. `enabled()` read an absent row and failed
    closed, which is the right behaviour, but "ships DISABLED" was true by the absence of a record
    rather than by one, and turning it on meant raw SQL against `runtime_flag` -- a fleet-wide
    behaviour change with no verb, no actor and no thread. The module docstring above says the flag
    is a `runtime_flag` row precisely so the write is a verb call; until this function there was
    no such call.

    An UPDATE, not a DELETE, for `resume`'s reason: `brain_runtime` holds DELETE on nothing, and
    `set_at`/`set_by` are a better record of who turned it off than a missing row ever was.

    It refuses to turn ON with no reason. Every other guard here is about not accepting work
    without a human; this one is about not switching that off without a human saying why.
    """
    if on and not note.strip():
        raise RuntimeError(
            "refusing to enable auto-accept with no reason. This converts self-reported "
            "completion into recorded completion for the whole fleet; pass --note saying what "
            "measurement justifies it. `swarm auto-accept` prints the report.")
    ctx.execute(
        """INSERT INTO brain.runtime_flag (key, value, set_by, note) VALUES (%s, %s, %s, %s)
           ON CONFLICT (key) DO UPDATE
             SET value = EXCLUDED.value, set_at = now(), set_by = EXCLUDED.set_by,
                 note = EXCLUDED.note""",
        (FLAG, "true" if on else "false", by, note))
    return {"flag": FLAG, "enabled": on, "set_by": by, "note": note}


def measure_in_transaction(ctx, task_id: str) -> dict:
    """The measurement, run INSIDE the `done` transaction that triggered it.

    It lives here rather than in the CLI on purpose. A measurement bolted onto one surface is a
    measurement the next surface silently skips, and a week of data with an unknown number of
    missing rows is worse than no data: the disagreement rate would be computed over whatever
    happened to go through the CLI. In the transition, every surface gets it, and it lands or
    rolls back with the `done` it describes rather than becoming a note about a task that never
    finished.

    Reads the same view `would_accept` reads, through the transaction's own cursor, so it sees
    the row this transaction has just marked done.

    Every one of the four cases records a verdict on the thread, and the label states the flag's
    ACTUAL value. Two reasons, both learned from task 0139:

      1. The recorded verdict is what `disagreement_report()` scores. A case that records nothing
         is a case the week's report cannot see, and the acting case (flag on, item eligible)
         recorded nothing at all before this.
      2. The label used to read `rule DISABLED` whenever the module declined, including when the
         flag was ON and the item was merely ineligible. A trail that misreports the state of the
         switch is worse than no trail: it is the one record anybody would use to reconstruct
         whether the fleet was accepting on its own.
    """
    # Property 4, INSIDE the `done` transaction, before eligibility is asked. A steered run
    # records the exclusion and returns: no verdict row, so the week's report has nothing to
    # score, and no acting branch, so the rule cannot accept it even with the flag on.
    if steering.is_steered_in(ctx, task_id):
        ctx.thread(task_id, "note", EXCLUDED_NOTE)
        return {"id": task_id, "eligible": False, "acted": False, "steered": True,
                "reasons": [STEERED_REASON]}

    row = ctx.one("SELECT id FROM brain.auto_accept_candidate WHERE id = %s", (task_id,))
    eligible = row is not None
    on = str(ctx.scalar("SELECT value FROM brain.runtime_flag WHERE key = %s", (FLAG,))
             or "").lower() == "true" or os.environ.get("ENGINE_AUTO_ACCEPT") == "1"

    if not eligible:
        sig = ctx.one("SELECT external, canon_touching, reversibility "
                      "FROM brain.work_item_signals WHERE id = %s", (task_id,))
        reasons = []
        if sig:
            if sig["external"]:
                reasons.append("external")
            if sig["canon_touching"]:
                reasons.append("canon_touching")
            if sig["reversibility"] != "high":
                reasons.append(f"reversibility {sig['reversibility']} (needs reversible)")
    else:
        reasons = []

    prefix = ENABLED_PREFIX if on else DISABLED_PREFIX
    if on and eligible:
        # The rule acted. There is no human decision to compare this against, so the record says
        # `acted` rather than `WOULD have`, and the report keeps it out of the agreement rate
        # instead of scoring the rule's own act as agreement with a human.
        # `brain.auto_acceptor()` rather than the literal, and it is not a flourish. Migration
        # 36 exempts exactly one value from the human-login gate, because the rule acting inside
        # a `done` transaction on a fleet host has no human anywhere near it and claims none.
        # Two spellings of a sentinel is how that narrow exemption silently widens, so the
        # trigger and this writer call one producer. Same argument
        # `store/session.py::subscriber_role_name` makes about a role name.
        ctx.execute("UPDATE brain.work_item SET accepted_at = now(), "
                    "accepted_by = brain.auto_acceptor() WHERE id = %s", (task_id,))
        ctx.thread(task_id, "note", prefix + VERDICT_ACTED)
        ctx.thread(task_id, "accept", "accepted automatically (rule ENABLED)")
        return {"id": task_id, "eligible": True, "acted": True}

    verdict = VERDICT_YES if eligible else VERDICT_NO + ", ".join(reasons or ["ineligible"])
    ctx.thread(task_id, "note", prefix + verdict)
    return {"id": task_id, "eligible": eligible, "acted": False, "reasons": reasons}


def measurements() -> list:
    """Every recorded verdict, each paired with the human decision that followed it.

    One row per `done`, not per task: an item that is done, rejected and done again was evaluated
    twice and decided twice, and both are real data points. The pairing rule is the whole of it --
    for a verdict recorded at thread seq N, the human's decision is the FIRST `accept` or `reopen`
    after N. Everything else about this function follows from three facts about the trail:

      * `brain.thread` is append-only, and `accept` and `reopen` both land on it. So the pairing
        is over immutable rows, unlike the old reader, which asked a view whose membership the
        acceptance itself changed.
      * `reopen` is also what the runner calls on a subscription refusal (D00 rule 6), so it is
        NOT unambiguously a rejection. It is disambiguated structurally rather than by pattern:
        the refusal path only fires on an ACTIVE task, so it cannot be the first deciding event
        after a `done`. Taking only the first one keeps a later cycle's wall out of this cycle.
      * The rule's own `accept` (flag on, item eligible) is marked `acted` by the writer, so it is
        never mistaken for a human agreeing with the rule.

    A verdict with no decision yet is `pending`, and pending is NOT a rejection. That distinction
    is the second half of the 0139 defect: `false_accept` used to be every eligible item the
    operator had not got to, which counted his backlog and not his disagreement.
    """
    with store.read("runtime") as s:
        rows = s.query(
            """SELECT work_item_id AS id, seq, ts, kind, from_agent, text
                 FROM brain.thread
                WHERE (kind = 'note' AND text LIKE %s) OR kind = ANY(%s)
                ORDER BY work_item_id, seq""",
            (MEASURE_TAG + "%", list(DECIDING_KINDS)))

    # Property 4's third bite. Subtracted here rather than filtered in SQL so the count is a
    # number this function can report: a measurement that silently drops rows is the shape of
    # every confidently-wrong number in this system.
    steered = steering.steered_tasks()

    by_item: dict = {}
    for r in rows:
        if r["id"] in steered:
            continue
        by_item.setdefault(r["id"], []).append(r)

    out = []
    for item_id, events in by_item.items():
        for i, e in enumerate(events):
            if e["kind"] != "note" or not e["text"].startswith(MEASURE_TAG):
                continue
            body = e["text"][len(ENABLED_PREFIX):] if e["text"].startswith(ENABLED_PREFIX) \
                else e["text"][len(DISABLED_PREFIX):]
            decision = next((d for d in events[i + 1:] if d["kind"] in DECIDING_KINDS), None)
            out.append({
                "id": item_id,
                "seq": e["seq"],
                "measured_at": e["ts"],
                "rule_enabled": e["text"].startswith(ENABLED_PREFIX),
                "rule_acted": body.startswith(VERDICT_ACTED),
                "rule_would_accept": body.startswith((VERDICT_YES, VERDICT_ACTED)),
                # accept -> the human said yes. reopen -> the human said no. cancel -> withdrawn,
                # which is no verdict on the work at all. None -> not decided yet.
                "human": {"accept": "accepted", "reopen": "rejected",
                          "cancel": "withdrawn"}.get(decision["kind"]) if decision else "pending",
                "decided_by": decision["from_agent"] if decision else "",
                "decided_at": decision["ts"] if decision else None,
            })
    return sorted(out, key=lambda m: m["seq"])


def disagreement_report() -> dict:
    """The week's measurement: where the rule and the operator disagreed.

    Two directions, and the second is the one that matters. The rule saying yes where a human
    said no is a false accept, which is the failure mode that turns self-report into fabrication.

    Scored over the verdicts the module RECORDED at `done` time (see `measurements`), and only
    over those a human has since decided. Task 0139 replaced the old derivation, which recomputed
    the verdict from `brain.auto_accept_candidate` at read time and so measured whether the
    operator had clicked yet rather than whether he and the rule agreed: on the single data point
    that existed, a perfect agreement scored `disagreement_rate: 1.0`.

    `disagreement_rate` is None, never 0.0, when nothing has been decided. D00's named failure is
    a rate that reads clean from a test that evaluated nothing, and 0.0 is indistinguishable from
    a real result. `scored`, `pending` and `withdrawn` are all reported so the denominator is
    visible rather than implied.
    """
    ms = measurements()
    scored = [m for m in ms if m["human"] in ("accepted", "rejected") and not m["rule_acted"]]
    agree = [m for m in scored
             if m["rule_would_accept"] == (m["human"] == "accepted")]
    false_accept = [m for m in scored if m["rule_would_accept"] and m["human"] == "rejected"]
    false_reject = [m for m in scored if not m["rule_would_accept"] and m["human"] == "accepted"]
    # THE DENOMINATOR IS VISIBLE, AND SO IS WHAT WAS TAKEN OUT OF IT. `steered_excluded` is the
    # list of tasks this report is deliberately blind to, printed rather than implied, because
    # "excluded from the measurement" is only checkable if the report says which ones.
    steered = sorted(steering.steered_tasks())
    return {"n": len(ms), "scored": len(scored), "agree": len(agree),
            "false_accept": [m["id"] for m in false_accept],
            "false_reject": [m["id"] for m in false_reject],
            "pending": [m["id"] for m in ms if m["human"] == "pending"],
            "withdrawn": [m["id"] for m in ms if m["human"] == "withdrawn"],
            "rule_acted": [m["id"] for m in ms if m["rule_acted"]],
            "steered_excluded": steered,
            "disagreement_rate": round(1 - len(agree) / len(scored), 4) if scored else None,
            "enabled": enabled()}
