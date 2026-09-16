"""The human queue's state changes, registered once each on D1's narrow waist.

    recommend · recommend accept · recommend reject
    queue classify · queue defer · queue wake · queue bump · queue demote
    queue default fire

THE ONE THAT MATTERS:

    **`recommend accept` is the single place `requires_human` is enforced. One transition, one
    test, one thing to prove. A second acceptance path anywhere is the bug.**

There is no `recommend execute`, no `--auto`, no batch accept, and no flag on any verb here that
turns the decider off. The success criterion the whole program is judged on is that no code path
turns a recommendation into executed work without a human decider, and the cheapest way to hold
that is to have exactly one path and to make it loud.

COMPOSITION, stated once because it is the rule this file leans on twice. A transition may call
another lane's transition FUNCTION with its own `ctx`, which keeps one implementation and one
transaction. It may never re-implement one. `recommend accept` creates its work item by calling
`swarm_engine.transitions.post`, and `queue default fire` answers by calling
`swarm_engine.transitions.answer`; both run inside this verb's transaction, and `post`'s hard
flag inheritance therefore applies to the spawned work exactly as it would to a hand-posted
task. Copying either body here would have produced a second writer of `work_item` and a second
writer of `question.answer`, which is the failure D1's module exists to make impossible.

AND COMPOSING MEANS KEEPING WHAT THE COMPOSED VERB REPORTED, not only its write. `queue default
fire` called `answer` and threw its return away, so the guard that refuses to requeue behind a
live engine fired correctly and no caller could observe it: the sweep at 07:00 and 19:00 saw a
return that could not tell a default fired into a parked task from one fired into a task now back
in the inbox. A verb that composes another and discards its findings is a second, quieter writer
of the same fact -- the one that writes nothing.
"""

from __future__ import annotations

import os

import store
from swarm_engine import transitions as engine
from swarm_engine.signals import norm_task_id, signal_level

from . import tiers
from .defaults import DefaultRefused, check_default

SOURCE_TYPES = ("work_item", "question", "recommendation")


class QueueError(RuntimeError):
    """A queue verb refusing a call. Carries an exit code so the CLI does not invent one."""

    def __init__(self, msg, code: int = 1):
        super().__init__(msg)
        self.code = code


def _looks_like_an_agent_process() -> str:
    """The runner exports `SWARM_PARENT_TASK` into every terminal it starts.

    Defense in depth and explicitly NOT the gate: any process can clear its own environment, so
    this raises the bar rather than closing the hole. The part that is not bypassable is the
    trigger `recommendation_human_decider`, which since migration 32 refuses an acceptance from
    any connection `brain.current_human()` does not name, whatever the calling process claims.

    THIS DOCSTRING USED TO SAY THE HOLE WAS UNCLOSABLE -- "an agent that passes the string
    `operator` is not distinguishable from the operator by anything in v1 ... closing it needs
    real authentication, which this runtime does not have". That was wrong when it was written
    and it stayed wrong for two days: migration 20 had ALREADY built the authentication, for
    `brain.work_item.actor_type`, and nobody carried it one table across. The V9 acceptance run
    (task 0222) then measured the consequence -- `by='zzz-not-a-person'` dispatched real work
    booked `actor_type='human'`. Task 0290 closed it. The sentence is kept here rather than
    deleted because "this runtime does not have X" is the shape of claim worth re-checking
    before it is repeated: the thing it said was missing was one directory away.
    """
    return os.environ.get("SWARM_PARENT_TASK", "")


def _source(ctx, source_type: str, source_id: str) -> dict:
    if source_type not in SOURCE_TYPES:
        raise QueueError(f"unknown source type {source_type!r}. One of: {', '.join(SOURCE_TYPES)}")
    table = {"work_item": "brain.work_item", "question": "brain.question",
             "recommendation": "brain.recommendation"}[source_type]
    where = "id = %s::bigint" if source_type == "recommendation" else "id = %s"
    row = ctx.one(f"SELECT * FROM {table} WHERE {where}", (source_id,))
    if not row:
        raise QueueError(f"no such {source_type}: {source_id}")
    return row


def _overlay(ctx, source_type: str, source_id: str) -> dict:
    row = ctx.one("SELECT * FROM brain.queue_item WHERE source_type = %s AND source_id = %s",
                  (source_type, source_id))
    if row:
        return row
    return ctx.one(
        "INSERT INTO brain.queue_item (source_type, source_id) VALUES (%s, %s) RETURNING *",
        (source_type, source_id))


def _work_item_of(source_type: str, source_id: str, row: dict) -> str | None:
    if source_type == "work_item":
        return source_id
    if source_type == "question":
        return row.get("work_item_id")
    if row.get("subject_type") == "work_item":
        return row.get("subject_id")
    return None


def _spawned_workdir(ctx, workdir: str, parent: str) -> str:
    """Where the task these verbs SPAWN will run: what the caller typed, else the source task's own.

    Task 0100 put a gate on `engine.post`: a row the fleet may claim must carry an ABSOLUTE
    workdir, because an empty one is not a lenient boundary but no boundary -- the runner falls
    through to the agent profile's own directory, which is the parent of every repo on the box.
    Both spawning verbs in this file inherit `agent_claimable` from the source task, so from the
    moment that gate landed both refused outright whenever the source was fleet work and the
    operator typed no `--workdir`: `recommend accept` FAILED rather than accepting-without-
    spawning, which is the worse of the two ways to be wrong about an acceptance.

    INHERITED FROM THE SOURCE TASK, which is a source that can be named rather than a constant
    nobody chose. The task `queue defer --kind until-question` spawns exists to produce what the
    deferred row needs, and the one `recommend accept` spawns executes a recommendation ABOUT its
    subject; `post` is already given that same row as `parent` in both verbs, so the tree the
    spawned work runs in is that row's tree. A REPO-SHAPED CONSTANT WOULD BE THE DEFECT THE GATE
    EXISTS TO CLOSE: a client recommendation and an `engine` recommendation resolve to different
    trees and one fallback for both hides the mistake behind a value that looks deliberate.

    THE TYPED FLAG STILL WINS. `--workdir` is on both CLIs already (`queue accept`, `queue defer`)
    and a recommendation is quite often "do this over in the other repo", which is a fact only the
    decider holds. Inheritance is the floor under a caller who typed nothing, not a ceiling.

    A SOURCE THAT IS ITSELF CLAIMABLE AND CARRIES NO WORKDIR cannot be inherited from, and the
    refusal names this lane's flag AND the row that was empty. The engine's own message says
    `--workdir` too, but it cannot say which source was consulted and found empty, and that is the
    half that tells the operator whether to fix his command or fix the source row. Such a row can
    no longer be posted, but the ones that predate 0100 are still in the table.
    """
    typed = str(workdir or "").strip()
    if typed:
        # Absoluteness is judged in ONE place, `engine._check_fleet_workdir`, and it is judged on
        # the value that reaches the column. Re-checking it here would be a second opinion about
        # the same string that can drift from the first.
        return typed
    # `post` falls back to SWARM_PARENT_TASK when the caller names no parent, and claimability
    # comes from whatever parent it ends up using -- so the row read here is the row it will read.
    src = norm_task_id(parent or "") or norm_task_id(os.environ.get("SWARM_PARENT_TASK", ""))
    if not src:
        return ""
    row = ctx.one("SELECT workdir, agent_claimable FROM brain.work_item WHERE id = %s", (src,))
    if not row:
        # A parent that does not resolve is `post`'s refusal to make, in better words than these,
        # and making it here first would move a message the engine lane owns into this file.
        return ""
    inherited = str(row["workdir"] or "").strip()
    if inherited:
        return inherited
    if row["agent_claimable"]:
        raise QueueError(
            f"{src} is agent-claimable and carries no workdir, so there is nothing to inherit and "
            f"the task this would spawn is one the fleet may take with no directory to cd to. "
            f"Pass --workdir <absolute path that exists on the target host>, or give {src} one "
            f"(`swarm set {src} workdir <path>`) and every task spawned from it inherits it. "
            f"There is no default worth guessing: the spawned work runs where its subject lives, "
            f"and a constant here would run an engine task in a client's tree.", code=6)
    # A held source has no workdir to give and needs none: what it spawns is held too, and the
    # gate is only about rows a terminal can be handed.
    return ""


# ------------------------------------------------------------------ recommendations

@store.transition("recommend")
def recommend(ctx, *, text, rationale="", subject_type="", subject_id="", template_id=None,
              proposed_action="", requires_human=True, cites_event_id=None,
              cites_session_id=None, produced_by=None, unblock_weight=None, by=""):
    """Raise a recommendation. It is a proposal and it executes nothing.

    `requires_human` is stored as declared, because measuring how often producers claim they do
    not need a human is worth more than refusing the claim -- but it changes nothing about what
    happens next. `recommend accept` requires a human decider whatever this column says, and
    `queue doctor` reports every row that declared otherwise. A column that quietly enabled an
    unattended path would be the second acceptance path this lane exists not to have.

    What a recommendation may cite, measured by D3 rather than assumed: `stated_goal` is a quote
    of the request at ~85% usefulness, not evidence anything was done, and `ended_at` is NULL on
    all 1,130 backfilled sessions, so completion must never be inferred from a cited session.
    """
    if not str(text or "").strip():
        raise QueueError("a recommendation with no text recommends nothing")
    if subject_type and subject_type not in ("work_item", "session", "event", "entity"):
        raise QueueError(f"unknown subject type {subject_type!r}")
    row = ctx.one(
        """INSERT INTO brain.recommendation
             (subject_type, subject_id, text, rationale, requires_human, state, template_id,
              proposed_action, unblock_weight, cites_event_id, cites_session_id, produced_by,
              actor_type)
           VALUES (%s, %s, %s, %s, %s, 'open', %s, %s, %s, %s, %s, %s, 'ai')
           RETURNING id, state, requires_human, template_id""",
        (subject_type or "", subject_id or "", text, rationale or "", bool(requires_human),
         template_id, proposed_action or "", unblock_weight, cites_event_id,
         cites_session_id or None, produced_by))
    rid = row["id"]
    if subject_type == "work_item" and subject_id:
        ctx.actor = by or produced_by or "queue"
        ctx.thread(norm_task_id(subject_id), "note",
                   f"recommendation r{rid} raised: {text[:200]}"
                   + ("" if requires_human else
                      " [requires_human=false as declared; acceptance still needs a human]"))
    return {"id": rid, "state": "open", "requires_human": bool(requires_human),
            "template_id": template_id, "spawned_work_item": None}


@store.transition("recommend accept")
def recommend_accept(ctx, *, id, by, lane="", workdir="", note="", execute=True,
                     as_operator=True):
    """Accept a recommendation. THE ONLY PATH FROM A PROPOSAL TO EXECUTED WORK.

    THE DECIDER IS A DATABASE LOGIN, NOT THE `by=` STRING. Task 0290, and it is the refusal every
    other one in this list turned out to rest on. Until migration 32 the tests below were the
    whole of the gate, and test 2 fails OPEN by construction: it refuses the names that ARE in
    `brain.agent`, so every name that is not one passed and the agent picks the name. MEASURED by
    the V9 acceptance run (task 0222) from a `brain_runtime` connection with SWARM_PARENT_TASK
    cleared: `by='Andrew'` dispatched, `by='zzz-not-a-person'` dispatched and booked
    `actor_type='human'`, and `by='T2'` dispatched too until a heartbeat put T2 in `brain.agent`.
    So this verb now asks `brain.current_human()` -- migration 20's answer from `session_user`,
    NULL for `brain_runtime`, which is the login every agent surface connects as -- and writes the
    DATABASE'S answer into `decided_by` rather than the caller's.

    Refusals, in order, and each one is a code path D9 will try:

    1. an empty decider;
    2. a decider that is a registered agent (`brain.agent`), which is D00 contract rule 4;
    3. a decider that looks like the process running under the fleet runner;
    4. a connection the database does not know as a human -- THE GATE;
    5. a `by=` that disagrees with the name the database gives that connection;
    6. a recommendation that is not open -- accepting twice would spawn the work twice. SINCE
       TASK 0377, DISPATCH IDEMPOTENCY, THIS IS ABOUT CONCURRENCY AND NOT ONLY SEQUENCE: the
       row is read `FOR UPDATE`,
       so simultaneous callers queue behind one another and every one after the first is refused
       here, instead of all of them passing a check that nothing was holding.

    1, 2, 4, 5 and the state rule are refused AGAIN and independently by trigger
    `recommendation_human_decider` (queue/schema/0015_recommendation_human_login.sql). Two gates,
    because the consequence of one of them being wrong is an agent executing its own proposal.

    `as_operator=True` IS A DEFAULT THAT DOES NOTHING ON ITS OWN, and every caller has to pass it.
    `store/transitions.py::_login_for` reads it out of the kwargs `store.apply` was CALLED with,
    not out of this signature, so it is here to document the verb rather than to satisfy it -- the
    same shape `engine/swarm_engine/steering.py::take` has, and the reason
    `engine/tests/test_steering_exclusion.py` passes it on every one of its calls. A caller that
    forgets connects as `brain_runtime`, gets NULL from `brain.current_human()`, and is refused at
    refusal 4 with the sentence that names what to pass. That is fail-closed: a forgotten keyword
    loses an acceptance the operator can retry, where the alternative loses the gate.

    The spawned work item is created by calling `swarm_engine.transitions.post` in this
    transaction, so the hard flags inherit by OR from the subject task exactly as they would for
    any other child. A recommendation about an external subject cannot produce an unflagged task.

    AND SO DOES THE WORKDIR, when `--workdir` is not typed: `_spawned_workdir` reads it off the
    subject task, because work that executes a recommendation about a row runs in that row's tree.
    Without it this verb FAILED OUTRIGHT on any fleet subject after task 0100 gated `post` on an
    absolute workdir -- the acceptance rolled back with the spawn, which is the loudest possible
    way to lose an operator's decision.
    """
    # `FOR UPDATE` IS THE CONCURRENCY GATE, AND IT IS LOAD-BEARING. Task 0377, dispatch idempotency.
    # Without it this read and the UPDATE below are a check-then-act with nothing holding the row
    # in between, so refusal 6 is true only of callers that arrive one at a time. MEASURED rather
    # than reasoned about: eight processes on eight connections, barrier-released at one open
    # recommendation, ALL EIGHT were allowed to dispatch and spawned eight work items. Seven were
    # orphans, because `spawned_work_item` holds one id and the last writer wins.
    # `engine/tests/test_dispatch_idempotency.py` scene 2 is that measurement, and it fails
    # without this clause.
    #
    # The database did not catch it either, and says so itself: 0015's header records that
    # "THE STATE COLUMN IS ONE STATEMENT DEEP ... nothing here guards `brain.recommendation.state`
    # transitions in general, only the transition INTO `accepted`", and the trigger body is
    # guarded by `OLD.state IS DISTINCT FROM 'accepted'`, so the second writer skips it whole.
    # Both existing gates are about WHO decides. Neither was ever about HOW MANY TIMES.
    #
    # A row lock rather than a unique index, because the correct behaviour on contention is to
    # WAIT and then re-read: the loser blocks here, wakes to find the row already decided, and is
    # refused three lines down by the message that was already right. `recommend reject` takes the
    # same lock on the same row, which is what stops an acceptance and a rejection interleaving.
    #
    # THE PHRASING OF THE LINE ABOVE IS LOAD-BEARING AND THIS NOTE IS WHY. `queue/tests/
    # test_no_self_execution.py` counts `re.finditer(r"state\s*=\s*'accepted'", src)` over this
    # whole module and requires exactly ONE hit, because one acceptance statement is the program
    # success criterion. It reads TEXT, so a COMMENT containing that literal fails it -- this one
    # did, on its first run. Do not write the literal here in prose. The scan is right to be that
    # blunt: a matcher clever enough to skip comments is a matcher that can be fooled by a second
    # UPDATE dressed as one.
    rec = ctx.one("SELECT * FROM brain.recommendation WHERE id = %s::bigint FOR UPDATE",
                  (str(id),))
    if not rec:
        raise QueueError(f"no such recommendation: {id}")
    if rec["state"] != "open":
        raise QueueError(
            f"recommendation {id} is already {rec['state']}"
            + (f" (it spawned {rec['spawned_work_item']})" if rec.get("spawned_work_item") else "")
            + ". Accepting twice would execute the same proposal twice.")

    decider = str(by or "").strip()
    if not decider:
        raise QueueError(
            "recommend accept needs a decider. Acceptance is a human act and this verb is the "
            "one place that is enforced.")
    if ctx.one("SELECT 1 FROM brain.agent WHERE name = %s", (decider,)):
        raise QueueError(
            f"{decider} is a registered agent and may not accept a recommendation. D00 contract "
            f"rule 4: the decider on every acceptance is a human. Say so on the thread instead.")
    running_as = _looks_like_an_agent_process()
    if running_as:
        raise QueueError(
            f"this process is a fleet terminal working task {running_as} (SWARM_PARENT_TASK is "
            f"set), so it is not the operator. A recommendation is accepted by a human at a "
            f"console, not by an agent inside a run.")

    # THE GATE. Asked of the DATABASE, never taken from the caller's string. Everything above is
    # a test on a name the caller chose; `session_user` is fixed at authentication and no SQL a
    # client can send changes it -- `SET ROLE` moves `current_user` and not this.
    the_human = ctx.scalar("SELECT brain.current_human()")
    if not the_human:
        raise QueueError(
            "refusing to accept a recommendation from a connection the database does not know as "
            "a human. Acceptance is the one act that turns a proposal into executed work, and "
            "until task 0290 the only identity test here was `is this name in brain.agent`, "
            "which fails open: every name that is not an agent passed, and the agent picks the "
            "name. Call this as the operator (`as_operator=True`, which opens the operator "
            "login); `brain.current_human()` returned NULL for this session, which is what it "
            "returns for brain_runtime -- the login every agent surface connects as.", code=6)
    if decider != the_human:
        raise QueueError(
            f"this call says the decider is {decider!r} and the database says the connection is "
            f"{the_human!r}. Refusing rather than recording either one: decided_by is the record "
            f"of who chose this work, and it is the input to the auto-accept measurement.",
            code=6)

    # The DATABASE'S answer, not `by`. The two are equal by the refusal above; writing
    # `the_human` is what makes that true by construction rather than by the check staying there.
    decider = the_human
    # `AND state = 'open'` PLUS `RETURNING` IS THE SECOND MECHANISM, and it is not redundant with
    # the lock: it is what keeps this verb correct if a later edit drops the `FOR UPDATE`, which
    # is exactly the kind of change that looks harmless in review. An UPDATE matching no row
    # returns nothing, and this refuses rather than going on to spawn a second task against a
    # recommendation somebody else already decided. Two mechanisms for one invariant, the same
    # shape task 0290 used for the decider: the Python test and the trigger.
    landed = ctx.execute(
        "UPDATE brain.recommendation SET state = 'accepted', decided_at = now(), "
        "decided_by = %s, actor_type = 'human' WHERE id = %s::bigint AND state = 'open' "
        "RETURNING id", (decider, str(id)))
    if not landed:
        raise QueueError(
            f"recommendation {id} stopped being open between this verb's read and its write, so "
            f"another caller decided it first. Refusing rather than spawning a second task for "
            f"one proposal. Nothing was written.")

    spawned = None
    if execute:
        parent = rec["subject_id"] if rec["subject_type"] == "work_item" else ""
        title = (rec["proposed_action"] or rec["text"])[:200]
        posted = engine.post(
            ctx,
            title=title,
            lane=lane or "queue",
            body=(f"Executing accepted recommendation r{rec['id']}.\n\n"
                  f"Recommended: {rec['text']}\n\nRationale: {rec['rationale']}\n\n"
                  f"Accepted by {decider}." + (f"\n\n{note}" if note else "")),
            workdir=_spawned_workdir(ctx, workdir, parent or ""),
            posted_by=decider,
            parent=parent or "",
            produced_by=rec.get("produced_by"),
            actor_type="hybrid")
        spawned = posted["id"]
        ctx.execute("UPDATE brain.recommendation SET spawned_work_item = %s WHERE id = %s::bigint",
                    (spawned, str(id)))
        ctx.actor = decider
        ctx.thread(spawned, "note",
                   f"spawned by `recommend accept` on r{rec['id']}, decided by {decider}. The "
                   f"recommendation itself executed nothing.")
    _mark_disposed(ctx, "recommendation", str(id), "accepted", decider)
    return {"id": rec["id"], "state": "accepted", "decided_by": decider,
            "spawned_work_item": spawned, "requires_human": rec["requires_human"]}


@store.transition("recommend reject")
def recommend_reject(ctx, *, id, by, reason, as_operator=True):
    """Reject a recommendation, with a reason. A rejection with no reason teaches nobody.

    Rejections are the acted-on rate's other half and they are recorded as fully as acceptances:
    a template whose recommendations are consistently rejected is a dead playbook, and that is a
    prunable finding rather than an indictment of the whole layer.

    THE DECIDER IS A DATABASE LOGIN HERE TOO. Task 0313, the half task 0290 named and left. Until
    migration 33 this verb wrote `decided_by` straight from `by=`, defaulting to the literal
    string 'operator', and no trigger had anything to say about the `rejected` state -- so after
    0290 an agent could not accept a recommendation and could still record that the operator
    REJECTED one.

    WHY THAT MATTERS EVEN THOUGH THIS DISPATCHES NOTHING, and it is the reason the alternative
    was rejected. `brain.queue_acted_on` -- PLAN.md's falsifier for this whole layer -- counts
    `state = 'rejected'` and groups by `template_id`. It reads NEITHER `decided_by` NOR
    `actor_type`. So the obvious cheaper fix, keeping the verb open and booking `actor_type='ai'`
    when the connection is not a human, makes the row honest and leaves the falsifier movable by
    anything that can reach the table: the reader never looks at the column that was made honest.
    A measurement the measured can move is not a measurement.

    Refusals, in order, and each is a code path D9 will try:

    1. no reason -- it is the input to pruning templates;
    2. a recommendation that is not open;
    3. an empty decider;
    4. a decider that is a registered agent (`brain.agent`), which is D00 contract rule 4 read
       the way it is written: it is about who DECIDES, and rejecting is deciding;
    5. a connection the database does not know as a human -- THE GATE;
    6. a `by=` that disagrees with the name the database gives that connection.

    3, 4, 5, 6 and the actor_type rule are refused AGAIN and independently by trigger
    `recommendation_rejection_decider` (queue/schema/0016_recommendation_rejection_login.sql).

    `as_operator=True` IS A DEFAULT THAT DOES NOTHING ON ITS OWN, exactly as on `recommend
    accept`: `store/transitions.py::_login_for` reads it out of the kwargs `store.apply` was
    CALLED with, not out of this signature. A caller that forgets connects as `brain_runtime`,
    gets NULL from `brain.current_human()`, and is refused at refusal 5 with the sentence naming
    what to pass.

    THE COST, stated: on a host with no operator credential this verb is gone, because `_connect`
    raises before any SQL is sent. That is the same cost `recommend accept` already charges, in
    `queue/human_queue/cli.py`'s own words -- "there, nobody is the operator" -- and it is charged
    knowingly. An agent that believes a recommendation is wrong has the route it has always had
    for believing one is right: say so on the thread.
    """
    if not str(reason or "").strip():
        raise QueueError("recommend reject needs a reason. It is the input to pruning templates.")
    # `FOR UPDATE` for the same reason `recommend accept` takes it, and on the same row, which is
    # the point: the two verbs contend with EACH OTHER, not just with themselves. Task 0377,
    # dispatch idempotency. A
    # rejection dispatches nothing, so a doubled rejection is cheap; a rejection interleaved with
    # an acceptance is not. Unlocked, both read `state = 'open'`, both pass, and the last writer
    # owns `decided_at`, `decided_by` and `state` -- so an acceptance that really did spawn work
    # can end up recorded as `rejected`, with the spawned task still running and
    # `brain.queue_acted_on`, which counts by STATE, counting it as a pruned playbook.
    rec = ctx.one("SELECT * FROM brain.recommendation WHERE id = %s::bigint FOR UPDATE",
                  (str(id),))
    if not rec:
        raise QueueError(f"no such recommendation: {id}")
    if rec["state"] != "open":
        raise QueueError(f"recommendation {id} is already {rec['state']}")

    decider = str(by or "").strip()
    if not decider:
        raise QueueError(
            "recommend reject needs a decider. A rejection is the falsifier's other half: "
            "`brain.queue_acted_on` counts it and template pruning reads that count.")
    if ctx.one("SELECT 1 FROM brain.agent WHERE name = %s", (decider,)):
        raise QueueError(
            f"{decider} is a registered agent and may not reject a recommendation. D00 contract "
            f"rule 4 is about who DECIDES, and rejecting is deciding. Say so on the thread "
            f"instead.")

    # THE GATE. Asked of the DATABASE, never taken from the caller's string, and the same two
    # refusals in the same order `recommend accept` makes.
    the_human = ctx.scalar("SELECT brain.current_human()")
    if not the_human:
        raise QueueError(
            "refusing to reject a recommendation from a connection the database does not know as "
            "a human. A rejection dispatches nothing, which is why migration 32 left it open; it "
            "moves `brain.queue_acted_on` instead, and that view counts by STATE and groups by "
            "TEMPLATE, reading neither decided_by nor actor_type. So this verb prunes a playbook "
            "and until task 0313 `by=` defaulted to the string 'operator'. Call this as the "
            "operator (`as_operator=True`, which opens the operator login); "
            "`brain.current_human()` returned NULL for this session, which is what it returns "
            "for brain_runtime -- the login every agent surface connects as.", code=6)
    if decider != the_human:
        raise QueueError(
            f"this call says the decider is {decider!r} and the database says the connection is "
            f"{the_human!r}. Refusing rather than recording either one: decided_by is the record "
            f"of who rejected this proposal.", code=6)

    # The DATABASE'S answer, not `by`. The two are equal by the refusal above; writing
    # `the_human` is what makes that true by construction rather than by the check staying there.
    decider = the_human
    landed = ctx.execute(
        "UPDATE brain.recommendation SET state = 'rejected', decided_at = now(), "
        "decided_by = %s, actor_type = 'human' WHERE id = %s::bigint AND state = 'open' "
        "RETURNING id", (decider, str(id)))
    if not landed:
        raise QueueError(
            f"recommendation {id} stopped being open between this verb's read and its write, so "
            f"another caller decided it first. Refusing rather than overwriting their decision: "
            f"if that other caller ACCEPTED it, the work is already spawned and recording a "
            f"rejection over it would leave a running task under a pruned playbook.")
    if rec["subject_type"] == "work_item" and rec["subject_id"]:
        ctx.actor = decider
        ctx.thread(rec["subject_id"], "note", f"recommendation r{rec['id']} rejected: {reason}")
    _mark_disposed(ctx, "recommendation", str(id), f"rejected: {reason}", decider)
    return {"id": rec["id"], "state": "rejected", "decided_by": decider}


# ------------------------------------------------------------------ the membrane overlay

@store.transition("queue classify")
def queue_classify(ctx, *, source_type, source_id, item_class=None, template_id=None,
                   prepared_context_link=None, recommended_option=None, counterargument=None,
                   eta=None, by="operator", produced_by=None):
    """Set the membrane fields the tier is computed from. It never sets the tier.

    A producer stating its own tier is the fast lane's failure mode: the tier is derived, so a
    producer that wants an item in Decide has to actually prepare the context and state a
    recommended option. `queue demote` is the only writer of `tier_override`, and it writes a
    calibration miss beside it.

    The counterargument is stored next to the recommendation because showing a recommendation
    alone is how a review queue becomes a rubber stamp.
    """
    _source(ctx, source_type, str(source_id))
    _overlay(ctx, source_type, str(source_id))
    sets, params = [], []
    for col, val in (("item_class", item_class), ("template_id", template_id),
                     ("prepared_context_link", prepared_context_link),
                     ("recommended_option", recommended_option),
                     ("counterargument", counterargument), ("eta", eta),
                     ("produced_by", produced_by)):
        if val is not None:
            sets.append(f"{col} = %s")
            params.append(val)
    if not sets:
        raise QueueError("queue classify with nothing to set")
    params += [source_type, str(source_id)]
    row = ctx.one(f"UPDATE brain.queue_item SET {', '.join(sets)} "
                  f"WHERE source_type = %s AND source_id = %s RETURNING *", params)
    return dict(row)


KINDS = ("work", "decision")
UNSET = "unset"


@store.transition("queue type")
def queue_type(ctx, *, id, kind=None, impact=None, agent="", force=False, note=""):
    """Declare what a row IS and what it is WORTH, as one act. `queue classify` sets the membrane
    fields the TIER is computed from; this sets the two columns on the work item itself.

    THE ONE THAT MATTERS HERE: **this verb writes nothing.** It calls `swarm_engine.transitions
    .set_field` with its own `ctx`, once per column, inside this transaction. `kind` and `impact`
    already have exactly one writer each and it is not in this file. A fresh `UPDATE
    brain.work_item SET kind = ...` here would be the second writer of one fact, which is the
    failure this module's header names in its own COMPOSITION paragraph and the reason
    `recommend accept` calls `post` rather than copying it.

    So this verb exists for the three things a caller cannot get by typing `swarm set` twice, and
    for nothing else:

    **1. THE TWO FACTS LAND TOGETHER OR NOT AT ALL.** `set` writes one key per call, so two shell
    invocations are two transactions and anything that interrupts between them leaves a row typed
    `decision` with no impact -- a decision the money column cannot order, which is half of what
    the queue surface is blocked on. One transaction makes the half-state unreachable rather than
    unlikely.

    **2. A DECISION MUST CARRY AN IMPACT.** Refused, not warned, unless `force`. Migration 52 built
    the decisions queue on one argument -- *"The decisions queue is only worth opening if every row
    in it genuinely needs a decision"* -- and a decision with no impact sorts at the bottom of the
    one column that orders it, which is a silent deprioritisation wearing the clothes of a
    declaration. `force` is left open because a future caller may legitimately have only half the
    answer; it is loud and it lands on the thread.

    **3. IT REFUSES A HELD ROW.** `agent_claimable = false` is what this repo prints as *held for
    the operator*, and on 2026-09-02 that is exactly the operator's 28 held rows. The wave-1
    carve-out audit recorded those rows as not being in this store at all, having searched for a
    hold TABLE; they are 28 of the 48 rows in `state IN ('inbox','active')`, which is precisely the
    population the decisions queue reads. A carve-out that survives only because each commander
    remembers it is one commander's inattention from being gone, so the refusal lives here.
    `outputs/2026-09-02-wave2/W2A/03-THE-28-HELD-ROWS-ARE-REACHABLE.md` has the measurement.

    THERE IS NO `--impact unset`, AND THAT IS MEASURED RATHER THAN FORGOTTEN. Routing an empty
    impact through `set` writes the EMPTY STRING, which is not NULL: `COALESCE(impact, stakes)`
    then stops at it, `stakes` is never read, and the unreadable value folds onto the conservative
    default, which for this field is `high`. Measured on `brain_w2a`: a `stakes=low` row given an
    empty impact reads `high` at magnitude 2.0 instead of `low` at 0.5. Writing NULL here instead
    would make this file the second writer the paragraph above refuses. So an empty impact is
    refused at this door and the defect is reported to the lane that owns it, in
    `04-EMPTY-IMPACT-READS-AS-HIGH.md`. `kind` HAS an `unset`, because it is not a levelled signal,
    never reaches `validate_signal`, and `set` writes a real NULL for it -- verified, not assumed.

    WHAT THIS VERB DOES NOT DECIDE. Which rows are decisions, and what money a row is worth, are
    the operator's answers and migration 52 says so in its own text: *"NOTHING IS BACKFILLED BY
    THIS FILE. Which of the 48 are decisions is his answer, not a pattern match."* This verb is the
    door that answer walks through. It is not the answer, and it takes no guess when it is called
    with none.
    """
    tid = norm_task_id(str(id))
    if kind is None and impact is None:
        raise QueueError(
            "queue type with nothing to declare. Pass --kind (work, decision or unset), --impact "
            "(a band, or an amount of money), or both. A verb that accepted an empty call would "
            "file a thread note saying a row had been typed when nothing had been.")

    if kind is not None:
        kind = str(kind).strip().lower()
        if kind not in KINDS + (UNSET,):
            raise QueueError(
                f"kind: {kind!r} is not one of {', '.join(KINDS)}, or {UNSET!r} to undeclare. The "
                f"database refuses anything else through work_item_kind_check, so this refusal is "
                f"the same rule said earlier and with a repair attached.")
    if impact is not None:
        impact = str(impact).strip()
        if not impact:
            raise QueueError(
                "refusing an EMPTY impact. It does not clear the column: `set` writes the empty "
                "string, COALESCE(impact, stakes) stops there because '' is not NULL, and the "
                "unreadable value folds onto this field's conservative default, which is `high`. "
                "Measured: a stakes=low row given an empty impact reads high at magnitude 2.0 "
                "instead of low at 0.5. There is no --impact unset for that reason, and writing a "
                "NULL here would make this module a second writer of work_item.impact. To take an "
                "impact back, declare the one you mean.", code=5)

    row = ctx.one("SELECT id, kind, impact, stakes, state, agent_claimable, title "
                  "FROM brain.work_item WHERE id = %s", (tid,))
    if not row:
        raise QueueError(f"no such work item: {tid}")

    # THE OPERATOR'S OWN ROW. `agent_claimable = false` is the operator's, by `post`'s own
    # design: *"A row nobody classified is the operator's: brain.work_item holds his day as well
    # as the fleet's queue"*. Typing such a row moves something in HIS list, so it is a deliberate
    # act with an override on the thread rather than an ordinary edit.
    #
    # THIS IS NOT `_hold()`, which is the other sense of held in this repo -- `state='active'`
    # with a live claim -- and a grep for the word finds that one instead of this. Both senses are
    # real and they cover different rows.
    #
    # TODAY'S INSTANCE, which is why the refusal is here and not in a commander's memory: on
    # 2026-09-02 this predicate selects EXACTLY the operator's 28 held rows, and the wave-1
    # carve-out audit recorded those rows as not being in this store at all, having searched for a
    # hold TABLE. They are 28 of the 48 rows in `state IN ('inbox','active')`, which is precisely
    # the population the decisions queue reads. See
    # outputs/2026-09-02-wave2/W2A/03-THE-28-HELD-ROWS-ARE-REACHABLE.md.
    if not row["agent_claimable"] and not force:
        raise QueueError(
            f"refusing to type {tid}: it is the OPERATOR'S OWN ROW (agent_claimable = false), not "
            f"fleet work. Typing it as a decision, or giving it a value, moves it in his list and "
            f"not in yours. Pass --force if you mean it; the override is written to the row's "
            f"thread, which is append-only. On this store today that predicate is exactly his 28 "
            f"held rows, and every commander in this programme is carved out of them.", code=6)

    # A DECISION MUST BE ORDERABLE. The row's EXISTING impact counts: re-typing the kind of a row
    # that already carries money is not the half-state this guards against.
    becoming = kind if kind is not None and kind != UNSET else (row["kind"] or "")
    will_have_impact = impact if impact is not None else (row["impact"] or "")
    if becoming == "decision" and not will_have_impact and not force:
        raise QueueError(
            f"refusing to type {tid} as a decision with no impact. The decisions queue is ordered "
            f"by the money column, so a decision carrying nothing sorts to the bottom of the one "
            f"list that exists to be small -- a silent deprioritisation that looks like a "
            f"declaration. Pass --impact with it (a band, or an amount of money), or --force if "
            f"you genuinely have only half the answer and want it recorded that way.", code=5)

    # ------------------------------------------------------------------ the composed writes
    #
    # ONE WRITER PER COLUMN AND IT IS NOT THIS FILE. `set_field` is called as a FUNCTION with this
    # transaction's ctx, so its refusals, its coercion read-back and its thread line all apply
    # exactly as they do to `swarm set`, and a rollback here rolls back both columns together.
    wrote = {}
    if kind is not None:
        r = engine.set_field(ctx, id=tid, key="kind",
                             value=(None if kind == UNSET else kind), agent=agent, force=force)
        wrote["kind"] = r["kind"]
    if impact is not None:
        r = engine.set_field(ctx, id=tid, key="impact", value=impact, agent=agent, force=force)
        wrote["impact"] = r["impact"]

    after = ctx.one("SELECT kind, impact FROM brain.work_item WHERE id = %s", (tid,))
    ctx.actor = (agent or "").strip() or "operator"
    # ONE NOTE FOR THE ACT, not a second copy of the two `set` lines above it. What those two
    # cannot say is that they were ONE declaration and why, and that is the whole of what this
    # adds. A note repeating the values would be the same fact recorded twice, one commit apart
    # from the file that says not to do that.
    declared = " and ".join(f"{k} = {v!r}" for k, v in wrote.items())
    ctx.thread(tid, "note",
               f"TYPED: {declared}, declared as one act by "
               f"{(agent or '').strip() or 'the operator'}."
               + (f" {note}" if note else "")
               + (" FORCED past a refusal." if force else ""))

    return {"id": tid, "kind": after["kind"], "impact": after["impact"],
            "reads_as": ("decision" if (after["kind"] or "").lower() == "decision" else "work"),
            "declared": sorted(wrote), "forced": bool(force),
            "was": {"kind": row["kind"], "impact": row["impact"]},
            # REPORTED, because it is the fact that decides whether this row is now in his
            # decisions queue, and a caller that had to re-query for it would be a caller that
            # mostly would not.
            "in_decisions_queue": ((after["kind"] or "").lower() == "decision"
                                   and row["state"] in ("inbox", "active"))}


@store.transition("queue assign")
def queue_assign(ctx, *, id, to=None, note="", as_operator=True):
    """Hand a work item to one named human, or hand it back. Lane E, row 0384, migration 37.

    THE ONLY WRITER OF `brain.work_item.assigned_human`, which is the answer to the one of row
    0384's four subject questions that has no answer at all once a second human exists: whose
    queue is whose.

    `to=None` UNASSIGNS, which is not a deletion: it returns the row to
    `brain.default_assignee()`, the operator, which is the bus's own rule from queue schema 0013
    ("a row nobody classified is his"). So there is no state this verb can produce in which a row
    belongs to nobody.

    IT IS NOT A PERMISSION AND GRANTS NOTHING. Any human on this instance may still accept this
    row, answer its questions and see it in `brain.queue_open`, which is unfiltered and stays
    that way. See `outputs/2026-08-27-E-0384-multi-user/IDENTITY-POLICY.md` part 3 for why a
    permission matrix is deliberately not built here.

    Both refusals are the database's as well as this function's, because a gate that exists in
    one place is a gate one bug away from being absent: migration 37 refuses an assignment from a
    non-human login, and refuses a name `brain.human_roster()` does not know.
    """
    tid = norm_task_id(str(id))
    who = ctx.scalar("SELECT brain.current_human()")
    if not who:
        raise QueueError(
            "refusing to assign work from a connection the database does not know as a human. "
            "Assignment moves a row between humans, so an agent that could do it could move the "
            "operator's own work off his queue. Call this as a human (`as_operator=True`, which "
            "opens a human login); `brain.current_human()` returned NULL for this session, which "
            "is what it returns for brain_runtime.", code=6)
    to = (str(to).strip() or None) if to is not None else None
    if to is not None and not ctx.one("SELECT 1 FROM brain.human_roster() r WHERE r.human = %s",
                                      (to,)):
        known = [r["human"] for r in ctx.execute("SELECT human FROM brain.human_roster()")]
        raise QueueError(
            f"refusing to assign {tid} to {to!r}: this database does not know that human. A row "
            f"assigned to a name nobody holds leaves EVERY queue, which is the anti-graveyard "
            f"failure `queue doctor` exists to catch arriving through a new door. Known: "
            f"{', '.join(known) or '(none)'}. `swarm admin human provision <slug>` adds one.",
            code=6)
    row = ctx.one("UPDATE brain.work_item SET assigned_human = %s WHERE id = %s "
                  "RETURNING id, assigned_human", (to, tid))
    if not row:
        raise QueueError(f"no such work item: {tid}")
    ctx.actor = who
    # THE THREAD EVENT IS NOT DECORATION. `brain.thread` is append-only and no verb deletes from
    # it, so the assignment history survives migration 37's rollback, which drops the column.
    ctx.thread(tid, "note",
               (f"assigned to {to} by {who}" if to else
                f"unassigned by {who}; it returns to the default assignee")
               + (f". {note}" if note else ""))
    return {"id": tid, "assigned_human": row["assigned_human"], "assigned_by": who,
            "means": ("this row now wants " + to + "'s attention" if to else
                      "this row is unassigned and belongs to the default assignee"),
            "not_a_permission": "every human on this instance may still act on it"}


@store.transition("queue demote")
def queue_demote(ctx, *, source_type, source_id, by="operator", producer="", note=""):
    """The `not fast` button: one tap, and it logs a calibration miss against the producer.

    The demote and the miss are ONE transaction on purpose. A demote that recorded no miss would
    let the operator absorb a bad estimate silently, and the producer would keep making it. The
    stopwatch decides what is cheap for the human, never the producing agent.

    IT DEMOTES FROM THE TIER THE OPERATOR ACTUALLY SAW (task 0158). This used to compute the
    current tier from `brain.queue_item` alone, which carries only what the membrane wrote; the
    console's tier is computed by `reads.queue()` from the arm's row AND the overlay AND the
    question's stated default. So the two disagreed on every item whose tier turned on something
    the membrane had not written, which was three of the four arms. Both now call
    `tiers.tier_inputs` on the same two rows, and `brain.queue_open` is read here for the same
    reason `reads.queue()` reads it: it is the row that carries `default_item_class` and the
    inherited reversibility signal.
    """
    row = _source(ctx, source_type, str(source_id))
    ov = _overlay(ctx, source_type, str(source_id))
    qo = ctx.one("SELECT * FROM brain.queue_open WHERE source_type = %s AND source_id = %s",
                 (source_type, str(source_id)))
    if not qo:
        raise QueueError(
            f"{source_id} is not an open queue item, so it is not in a tier and there is nothing "
            f"to demote it from. `not fast` corrects where a LIVE item sits.")
    pending = _pending_default(ctx, source_type, str(source_id), row)
    inputs = tiers.tier_inputs(dict(qo), dict(ov), pending["default_text"] if pending else None)
    rev = signal_level("reversibility", qo.get("reversibility"))
    current = tiers.tier_of(inputs, rev)["tier"]
    target = tiers.demote_target(current)
    if target == current:
        # THE MISS ROW MUST BE ABLE TO EXPRESS A MISS. Shape is the bottom, so a demote from it
        # moves nothing and would write `declared == observed`: a row in the calibration dataset
        # that records a miscalibration of zero, indistinguishable from a producer who got it
        # right. Measured on 2026-08-16 as `shape -> shape` rows against real producers.
        raise QueueError(
            f"{source_id} is already in {current.title()} and there is no tier below "
            f"it. `not fast` moves an item down one and logs the gap as a producer's miss; from "
            f"the bottom it would log a miss of zero. What you want is `queue defer` with the "
            f"question that would make it decidable, or a decline with a reason.", code=5)
    ctx.execute("UPDATE brain.queue_item SET tier_override = %s, tier_override_reason = %s "
                "WHERE source_type = %s AND source_id = %s",
                (target, f"demoted from {current} by {by}: not fast",
                 source_type, str(source_id)))
    producer = producer or row.get("claimed_by") or row.get("asked_by") or row.get("posted_by") \
        or row.get("produced_by") or qo.get("producer") or ""
    ctx.execute(
        """INSERT INTO brain.queue_calibration
             (source_type, source_id, producer, kind, declared, observed, note, created_by)
           VALUES (%s, %s, %s, 'not-fast', %s, %s, %s, %s)""",
        (source_type, str(source_id), producer, current, target,
         note or "the operator opened it and it was not a two-minute item", by))
    if source_type == "work_item":
        ctx.actor = by
        ctx.thread(str(source_id), "note",
                   f"not fast: demoted {current} -> {target}. Calibration miss logged against "
                   f"{producer or 'an unnamed producer'}.")
    return {"source_type": source_type, "source_id": str(source_id), "was": current,
            "tier": target, "producer": producer}


@store.transition("queue bump")
def queue_bump(ctx, *, source_type, source_id, delta, reason, by="operator",
               half_life_hours=24.0, model_score=None, model_rank=None, model_tier=None,
               item_class=None):
    """Record a labelled disagreement with the model. It decays; it does not pin.

    The reason is required by this verb AND by a CHECK on the column, because the log is the
    weight-tuning dataset and an unlabelled row in a tuning dataset is worse than no row: it
    reports a disagreement whose cause nobody can recover.
    """
    if not str(reason or "").strip():
        raise QueueError(
            "a bump needs a reason. Every bump is a labelled disagreement between you and the "
            "model, and that log is what tunes the weights so you have to bump this class less.")
    try:
        delta = float(delta)
    except (TypeError, ValueError):
        raise QueueError(f"bump delta {delta!r} is not a number") from None
    if delta == 0:
        raise QueueError("a bump of zero is not a disagreement")
    _source(ctx, source_type, str(source_id))
    _overlay(ctx, source_type, str(source_id))
    row = ctx.one(
        """INSERT INTO brain.queue_bump
             (source_type, source_id, delta, half_life_hours, reason, model_score, model_rank,
              model_tier, item_class, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at""",
        (source_type, str(source_id), delta, half_life_hours, reason, model_score, model_rank,
         model_tier, item_class, by))
    if source_type == "work_item":
        ctx.actor = by
        ctx.thread(str(source_id), "note",
                   f"bumped {delta:+g} (half life {half_life_hours:g}h): {reason}")
    return {"id": row["id"], "delta": delta, "half_life_hours": half_life_hours,
            "created_at": row["created_at"]}


# ------------------------------------------------------------------ defer

DEFER_KINDS = ("until-time", "until-event", "until-question", "accept-default", "decline")


@store.transition("queue defer")
def queue_defer(ctx, *, source_type, source_id, kind, wake_at=None, wake_event_type=None,
                wake_subject_type=None, wake_subject_id=None, question=None, lane="",
                workdir="", reason="", label="", by="operator", acknowledge_default=False):
    """Every path out of the queue carries a wake condition. There is no untyped defer.

    Five kinds and each one is checked here and again by a CHECK constraint:

    - `until-time`      a time, from the chips
    - `until-event`     wakes when a named dependency resolves
    - `until-question`  DEFER WITH QUESTION: posts an agent task to produce what is missing and
                        wakes when it lands. This is the one no ordinary task manager can have,
                        because its producers are agents: deferral that makes the item CHEAPER
                        rather than older.
    - `accept-default`  recorded as a decision, never as a snooze
    - `decline`         returned to the producer with a reason

    THE THIRD DEFER REFUSES TO BE A DEFER. After two, the time chips are gone and the options
    are decline or move to Shape, because three deferrals means mis-scoped, not mis-timed.

    DEFERRING PAST A SILENCE WINDOW IS AN INTERSTITIAL, NEVER A SIDE EFFECT. If a default is
    pending on this item, this verb refuses until the caller has acknowledged what silence will
    ship. The DEFAULTED discipline only works if silence is always informed.

    THE `until-question` BRANCH SPAWNS A TASK, and with no `--workdir` it takes the deferred row's
    own -- see `_spawned_workdir`. The question is about THIS item, so the work that answers it
    belongs in the same tree, and after task 0100's gate a fleet-claimable source with no typed
    workdir refused the whole defer rather than spawning anything.
    """
    if kind not in DEFER_KINDS:
        raise QueueError(f"unknown defer kind {kind!r}. One of: {', '.join(DEFER_KINDS)}. "
                         f"There is no untyped defer: every path out carries a wake condition.")
    row = _source(ctx, source_type, str(source_id))
    _overlay(ctx, source_type, str(source_id))
    prior = ctx.scalar(
        "SELECT count(*) FROM brain.queue_defer WHERE source_type = %s AND source_id = %s "
        "AND kind NOT IN ('decline', 'accept-default')", (source_type, str(source_id))) or 0
    n = int(prior) + 1

    if n >= 3 and kind in ("until-time", "until-event", "until-question"):
        raise QueueError(
            f"{source_id} has been deferred {prior} times already. The third defer refuses to be "
            f"a defer: three deferrals means mis-scoped, not mis-timed. Decline it with a "
            f"reason, or move it to Shape with `queue demote`.", code=3)

    pending = _pending_default(ctx, source_type, str(source_id), row)
    if pending and kind not in ("accept-default", "decline") and not acknowledge_default:
        raise QueueError(
            f"deferring {source_id} runs past its silence window. Silence will ship: "
            f"\"{pending['default_text']}\" (null branch: {pending['null_branch']}). "
            f"Defer anyway with acknowledge_default, answer it now, or extend the window "
            f"explicitly -- extending is your right and it is a distinct act.", code=4)

    wake_task = None
    if kind == "until-question":
        if not str(question or "").strip():
            raise QueueError("defer-with-question needs the question: what would make this "
                             "decidable? That question becomes the agent task.")
        wakes_for = _work_item_of(source_type, str(source_id), row) or ""
        posted = engine.post(
            ctx, title=f"Produce what {source_id} needs: {str(question)[:160]}",
            lane=lane or "queue",
            body=(f"The operator deferred {source_type} {source_id} until this lands.\n\n"
                  f"What is missing: {question}\n\n"
                  f"Answering this makes the queue item cheaper, not older."),
            workdir=_spawned_workdir(ctx, workdir, wakes_for), posted_by=by,
            parent=wakes_for)
        wake_task = posted["id"]

    if kind == "decline" and not str(reason or "").strip():
        raise QueueError("a decline is returned to the producer, so it needs a reason.")

    rec = ctx.one(
        """INSERT INTO brain.queue_defer
             (source_type, source_id, n, kind, label, wake_at, wake_event_type,
              wake_subject_type, wake_subject_id, wake_task, reason, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id, n, kind, wake_at, wake_task""",
        (source_type, str(source_id), n, kind, label or "", wake_at, wake_event_type,
         wake_subject_type, wake_subject_id, wake_task, reason or "", by))

    wid = _work_item_of(source_type, str(source_id), row)
    if wid:
        ctx.actor = by
        ctx.thread(wid, "note",
                   f"queue defer {n} ({kind})"
                   + (f" until {wake_at}" if wake_at else "")
                   + (f", waiting on {wake_task}" if wake_task else "")
                   + (f": {reason}" if reason else ""))
    out = {**dict(rec), "third_defer_next": n >= 2}
    if n >= 2 and kind in ("until-time", "until-event", "until-question"):
        out["warning"] = ("this was defer 2. The next one will be refused: time chips are "
                          "removed and the options become decline or move to Shape.")
    return out


def _already_woken_or_absent(ctx, defer_id) -> str:
    """Tell "somebody else already woke this" apart from "there is no such defer". Task 0382.

    Both states used to arrive at the reader as `no open defer 12`. A time-based routine cannot
    act on that: one of them means its own watch is wrong and the other means it lost a harmless
    race with the operator. See `queue_wake` for the full reasoning.
    """
    r = ctx.one("SELECT woke_at, woke_by FROM brain.queue_defer WHERE id = %s", (defer_id,))
    if not r:
        return (f"no such defer {defer_id}. Nothing was woken and nothing was written; check "
                f"the id you are watching.")
    return (f"defer {defer_id} was already woken at {r['woke_at']} by {r['woke_by'] or '?'}, so "
            f"this wake was not recorded. That is not a failure: the wake condition fired and "
            f"the defer is closed. One wake, one thread note, one woke_by.")


@store.transition("queue wake")
def queue_wake(ctx, *, defer_id, by="system", note=""):
    """Close a defer because its wake condition fired. A defer nobody wakes is the graveyard.

    ------------------------------------------------------------------------------------------
    THE CHECK-THEN-ACT RESIDUAL TASK 0377 LEFT OPEN, ANSWERED FOR THIS VERB SPECIFICALLY.
    ------------------------------------------------------------------------------------------
    0377 fixed `recommend accept`, `recommend reject` and `answer`, and named this verb as having
    the same shape and NOT being fixed with them: a good predicate on the SELECT
    (`AND woke_at IS NULL`) and none at all on the UPDATE (`WHERE id = %s`). Its instruction was
    explicit that the shape does not imply the remedy, and that each site needs its own answer to
    "is concurrency reachable here, and what is correct on contention". So, both questions.

    IS CONCURRENCY REACHABLE HERE? Yes, and by MORE routes than for the three verbs 0377 fixed.
    A defer can be woken by the operator at a surface, by an event-driven waker, and -- the case
    0377 called out -- by a TIME-BASED ROUTINE, which calls this on every fire. A clock is the
    one caller that never gets bored, and it is racing a human who is looking at the same item.

    WHAT WAS ACTUALLY BROKEN, which is smaller than a double dispatch and not nothing. Two wakers
    both read `woke_at IS NULL`, both pass, both run an unpredicated UPDATE. Nothing is spawned
    and no decision is duplicated -- but `woke_by` records whichever writer landed last, and BOTH
    write `queue defer N woke (...)` to the work item's thread. In a system whose record IS the
    thread, one event appearing twice with two different actors is a corrupted record, and the
    operator has no way to tell it from the item having genuinely woken twice.

    AND WHAT IS CORRECT ON CONTENTION HERE IS NOT WHAT WAS CORRECT THERE. For the three deciding
    verbs the answer was "wait, re-read, refuse", full stop, because the loser was attempting a
    SECOND DECISION and needed to be stopped. A second waker is not a rival decision: it is the
    same fact arriving twice, and the wake condition really did fire. So the write is still
    serialised and the loser is still refused -- one wake, one note, one `woke_by` -- but the
    REFUSAL HAD TO CHANGE, because the loser here is usually a clock.

    `no open defer 12` is what this verb said for both of the two states that reach it, and they
    are not the same state:

        (a) there is no such defer, or the caller is holding a stale id from days ago
        (b) the defer was open when this caller read it and another waker closed it in between

    A routine that gets (a) has a bug in what it is watching. A routine that gets (b) has nothing
    wrong with it at all and must not treat the refusal as a failure to retry or page on. So (b)
    now names itself, with who woke it and when. That is the whole of the difference from 0377's
    three, and it is the reason this was left to be answered rather than swept in with them.

    THE TWO MECHANISMS, same belt-and-braces shape 0377 used:
      1. `FOR UPDATE` on the read. Under READ COMMITTED the loser blocks, and when the winner
         commits, Postgres re-evaluates the predicate before handing over the lock -- so the
         loser's SELECT returns nothing and falls into the already-woken branch by itself.
      2. `AND woke_at IS NULL ... RETURNING` on the UPDATE. This is what keeps the verb correct
         if some later edit drops the lock, which is exactly the kind of change that looks
         harmless in review.
    """
    row = ctx.one("SELECT * FROM brain.queue_defer WHERE id = %s AND woke_at IS NULL "
                  "FOR UPDATE", (defer_id,))
    if not row:
        raise QueueError(_already_woken_or_absent(ctx, defer_id))
    updated = ctx.one("UPDATE brain.queue_defer SET woke_at = now(), woke_by = %s "
                      "WHERE id = %s AND woke_at IS NULL RETURNING id", (by, defer_id))
    if not updated:
        raise QueueError(_already_woken_or_absent(ctx, defer_id))
    src = _source(ctx, row["source_type"], row["source_id"])
    wid = _work_item_of(row["source_type"], row["source_id"], src)
    if wid:
        ctx.actor = by
        ctx.thread(wid, "note", f"queue defer {defer_id} woke ({row['kind']}) {note}".strip())
    return {"id": defer_id, "kind": row["kind"], "source_id": row["source_id"]}


# ------------------------------------------------------------------ defaults

def _pending_default(ctx, source_type, source_id, row) -> dict | None:
    if source_type != "question":
        return None
    if row.get("answer") is not None:
        return None
    text = (row.get("default_if_unanswered") or "").strip()
    if not text:
        return None
    # ASK THE CLASSIFIER THAT GOVERNS THIS ITEM, not the laxer one (migration 0011). This sentence
    # is what an operator reads when a defer runs past a silence window, and printing
    # `null branch: True` for a text that `queue default fire` would refuse is exactly the shape
    # of calm surface this lane exists not to have.
    sig = ctx.one("SELECT external, canon_touching FROM brain.work_item_signals WHERE id = %s",
                  (row.get("work_item_id"),)) if row.get("work_item_id") else None
    gated = bool(sig and (sig["external"] or sig["canon_touching"]))
    fn = "brain.default_is_null_branch_gated" if gated else "brain.default_is_null_branch"
    return {"default_text": text, "gated": gated,
            "null_branch": bool(ctx.scalar(f"SELECT {fn}(%s)", (text,)))}


@store.transition("queue default fire")
def queue_default_fire(ctx, *, qid, checkpoint, by="default", planners=()):
    """Silence decided. Book it as a decision, not as a snooze, and record what it shipped.

    Two writes in one transaction: the answer (through `swarm_engine.transitions.answer`, so
    there is still exactly one implementation of answering) and the accountability record. They
    are one transaction because the failure mode of two is a default that fired with no record,
    which makes the override-after-default rate quietly wrong in the producer's favour.

    `null_branch` is recorded AT FIRE TIME rather than recomputed later. The claim "silence only
    ever shipped the reversible branch" then survives a change to the classifier, which a
    recomputed column would not: it would report today's rule about yesterday's decision.
    """
    q = ctx.one("SELECT * FROM brain.question WHERE id = %s", (qid,))
    if not q:
        raise QueueError(f"no such question: {qid}")
    if q["answer"] is not None:
        raise QueueError(f"{qid} is already answered. A default fires into silence, not over an "
                         f"answer.")
    # A WITHDRAWN QUESTION HAS NO SILENCE LEFT TO INTERPRET (task 0159, migration 31). Firing here
    # would write an operator-signed answer on behalf of a question nobody is waiting on -- almost
    # always because the task that raised it was cancelled -- which is the act this whole
    # withdrawal path exists to make impossible. `brain.queue_pending_default` already excludes
    # it, so this is the second copy, and it is the one that holds for a caller naming the qid.
    if q.get("withdrawn_at") is not None:
        raise QueueError(f"{qid} was withdrawn by {q['withdrawn_by']} and is not waiting on "
                         f"anyone. Silence decides nothing here; there is no default to fire.")
    text = (q["default_if_unanswered"] or "").strip()
    if not text:
        raise QueueError(f"{qid} has no stated default, so silence decides nothing. It stays "
                         f"open until you answer it.")
    if checkpoint not in ("07:00", "19:00", "manual"):
        raise QueueError("defaults fire at two fixed checkpoints (07:00, 19:00) or manually. A "
                         "drip of deadlines is a slot machine; two checkpoints are a rhythm.")

    sig = ctx.one("SELECT external, canon_touching FROM brain.work_item_signals WHERE id = %s",
                  (q["work_item_id"],)) if q["work_item_id"] else None
    # `null_branch` is the UNGATED verdict and stays that way: it is what the column has always
    # meant, so a row written before migration 0011 keeps meaning what it meant. The REFUSAL below
    # asks the gated classifier, because that is the rule that governs a flagged task now.
    null_branch = bool(ctx.scalar("SELECT brain.default_is_null_branch(%s)", (text,)))
    ext = bool(sig["external"]) if sig else False
    canon = bool(sig["canon_touching"]) if sig else False
    gated_ok = (not (ext or canon)
                or bool(ctx.scalar("SELECT brain.default_is_null_branch_gated(%s)", (text,))))
    if not gated_ok:
        # Unreachable through `ask`, which the trigger guards. Reachable if a flag was RAISED on
        # a parent after the default was written, which is the one path the write-time trigger
        # cannot see. Refusing here is the difference between a gate and a gate-shaped comment.
        # NAME THE EVIDENCE (migration 0011): a default can fail here for three different reasons
        # and "act-shaped" describes only one of them. A writer told `cheque, mick` can check the
        # refusal against their own sentence; a writer told "act-shaped" can only take its word.
        why = ctx.one("SELECT brain.act_verbs_in(%s) AS acts, "
                      "brain.bare_imperative_in(%s) AS imper, "
                      "brain.null_branch_residue(%s) AS residue", (text, text, text))
        # Most substantive first: an act verb, then the words the gate does not recognise, then
        # the clause head. Empty residue plus an imperative is the bare-imperative class, which
        # has nothing else to report.
        evidence = (f" It names: {', '.join(why['acts'])}." if why["acts"]
                    else f" It uses words this gate does not recognise: "
                         f"{', '.join(why['residue'])}." if why["residue"]
                    else f" It opens a clause with: {', '.join(why['imper'])}." if why["imper"]
                    else " It states no null branch at all, so it cannot be classified.")
        raise QueueError(
            f"{qid}'s default is act-shaped and its task is now "
            f"{'external' if ext else ''}{' + ' if ext and canon else ''}"
            f"{'canon_touching' if canon else ''}. A flag was raised after the default was "
            f"written.{evidence} Silence may not ship this: answer it, or rewrite the default to "
            f"the null branch.", code=5)

    # KEEP WHAT THE ANSWER REPORTED. Composing a verb and discarding its findings is the shape
    # this file's composition rule exists to prevent, and here it cost the one observation the
    # 2026-08-16 fix was for: `answer` refuses to requeue behind a live engine and says so in
    # `requeue_refused`, but a caller of `queue default fire` saw a return that could not tell a
    # default fired into a parked task from one fired into a task now back in the inbox. That is
    # exactly the fact `checkpoints.fire_due` reports unattended, at 07:00 and 19:00, with no
    # human in the loop. Declared red on purpose by task 0226 and closed here.
    answered = engine.answer(ctx, qid=qid, text=text, planners=tuple(planners), requeue=False)
    ctx.execute(
        """INSERT INTO brain.queue_default_event
             (question_id, work_item_id, checkpoint, default_text, producer, external,
              canon_touching, null_branch)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (qid, q["work_item_id"], checkpoint, text, q["asked_by"] or "", ext, canon, null_branch))
    requeued = bool(answered.get("requeued"))
    refused_behind = answered.get("requeue_refused")
    # ONE NOTE, BOTH FACTS. What silence shipped and what happened to the task it was blocking are
    # the same event, and a reader who has to join two thread notes to learn the second one will
    # read the first alone. Say it in the DEFAULTED sentence.
    if refused_behind:
        outcome = (f" The task was NOT requeued: {refused_behind} is still heartbeating on it, and "
                   f"requeuing behind a live engine is how one task gets two agents. Fence that "
                   f"engine, then requeue.")
    elif requeued:
        outcome = " The task was requeued and is claimable again."
    else:
        outcome = " No requeue: the task was not blocked on this question."
    if q["work_item_id"]:
        ctx.actor = by
        ctx.thread(q["work_item_id"], "note",
                   f"DEFAULTED at {checkpoint}: silence shipped \"{text}\". This is recorded as a "
                   f"decision, not as a snooze." + outcome)
    return {"question_id": qid, "checkpoint": checkpoint, "default_text": text,
            "null_branch": null_branch, "external": ext, "canon_touching": canon,
            # From `engine.answer`, not recomputed here: one implementation reports it, one
            # caller relays it. `requeue_overrode` is not relayed because this verb hardcodes
            # `requeue=False` and can never produce it -- a key that is structurally always None
            # reads as a fact measured, not as a branch that cannot happen.
            "requeued": requeued, "requeue_refused": refused_behind}


@store.transition("queue extend window")
def queue_extend_window(ctx, *, qid, until, by="operator", reason=""):
    """Extending a silence window is the operator's right and a DISTINCT EXPLICIT ACT.

    It is not a side effect of deferring, which is why it is its own verb: the DEFAULTED
    discipline only works if silence is always informed, and a window that slid because
    something else happened is silence nobody was informed about.
    """
    q = ctx.one("SELECT * FROM brain.question WHERE id = %s", (qid,))
    if not q:
        raise QueueError(f"no such question: {qid}")
    if q["answer"] is not None:
        raise QueueError(f"{qid} is answered; there is no window left to extend.")
    if q.get("withdrawn_at") is not None:
        raise QueueError(f"{qid} was withdrawn by {q['withdrawn_by']}; there is no window left "
                         f"to extend, because nothing is waiting on silence.")
    ctx.execute(
        """INSERT INTO brain.queue_default_event
             (question_id, work_item_id, checkpoint, default_text, producer, null_branch,
              fired_at, window_extended_to)
           VALUES (%s, %s, 'manual', %s, %s, brain.default_is_null_branch(%s), NULL, %s)
           ON CONFLICT (question_id) DO UPDATE SET window_extended_to = EXCLUDED.window_extended_to""",
        (qid, q["work_item_id"], q["default_if_unanswered"] or "", q["asked_by"] or "",
         q["default_if_unanswered"] or "", until))
    if q["work_item_id"]:
        ctx.actor = by
        ctx.thread(q["work_item_id"], "note",
                   f"silence window on {qid} extended to {until}"
                   + (f": {reason}" if reason else "") + ". The default still fires.")
    return {"question_id": qid, "window_extended_to": until}


# ------------------------------------------------------------------ disposition

def _mark_disposed(ctx, source_type, source_id, disposition, by):
    ctx.execute(
        """INSERT INTO brain.queue_item (source_type, source_id, disposed_at, disposition, decision)
           VALUES (%s, %s, now(), %s, %s)
           ON CONFLICT (source_type, source_id)
           DO UPDATE SET disposed_at = now(), disposition = EXCLUDED.disposition,
                         decision = EXCLUDED.decision""",
        (source_type, str(source_id), disposition, f"{disposition} by {by}"))


@store.transition("queue opened")
def queue_opened(ctx, *, source_type, source_id):
    """Stamp the first open. `added`, `opened_at` and `disposed_at` are the clearance measurement.

    Recorded from day one because WIP is the thing that decides whether this layer survives:
    agents generate at machine speed and the operator clears maybe 30 to 60 minutes a day. A
    depth number with no measured clearance rate cannot produce an honest ETA.
    """
    _source(ctx, source_type, str(source_id))
    _overlay(ctx, source_type, str(source_id))
    ctx.execute("UPDATE brain.queue_item SET opened_at = coalesce(opened_at, now()) "
                "WHERE source_type = %s AND source_id = %s", (source_type, str(source_id)))
    return {"source_type": source_type, "source_id": str(source_id)}


__all__ = ["QueueError", "DefaultRefused", "check_default", "DEFER_KINDS"]
