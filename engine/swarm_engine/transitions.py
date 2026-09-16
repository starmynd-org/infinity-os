"""Every state change in dispatch, registered once, called by every surface.

This is the narrow waist for this subsystem, and the pattern the other lanes add their verbs
against. Three rules hold it, and all three are structural rather than advisory:

  1. **One transition function per state change, exposed as a verb.** Registering a name twice
     raises `DuplicateTransition` at import time (D1's `store.transition`). The CLI, the runner,
     the console and an MCP wrapper all call `store.apply(verb, ...)` and get identical code.
     No surface reimplements a transition, so no surface can invent one.

  2. **One verb, one transaction.** A file rename was all-or-nothing per task. A ported `done`
     is a row update plus a thread event plus, often, an artifact record, and three writes are
     not atomic by default. `store.apply` wraps the whole body in one transaction: a killed
     `done` leaves no partial state, which `test_killed_done_leaves_no_partial_state` proves by
     killing one.

  3. **No write path outside a transition.** `store` exports no `execute`, `insert` or
     `connect`. The read path is a Postgres READ ONLY session. There is no connection in this
     process that would accept a write from anywhere but here.

Ported from `internal/swarm-admiral/bin/swarm`. The state machine is the asset; the filesystem
was the implementation detail. Atomic rename became `SELECT ... FOR UPDATE SKIP LOCKED` and
everything else ports unchanged.
"""

from __future__ import annotations

import os

import store
from store import schema

from .signals import (
    CONSERVATIVE,
    HARD_FLAGS,
    LEVELLED_SIGNALS,
    gate_reasons,
    norm_task_id,
    signal_weights,
    truthy,
    validate_signal,
)

STATES = ("inbox", "active", "done", "blocked", "cancelled")
ARTIFACT_KINDS = ("created", "modified", "deleted", "report", "finding", "external")


class VerbError(RuntimeError):
    """A verb refusing a call. Carries an exit code so the CLI does not invent one."""

    def __init__(self, msg, code: int = 1):
        super().__init__(msg)
        self.code = code


# The queue score, in SQL, because the ordering and the row lock have to be one statement.
#
# If claim ordered in Python and locked afterwards it would be reading a snapshot and acting on
# it, which is the shape of every claim bug this contract exists to prevent. The folding
# vocabulary is `brain.signal_level()` (migration 1, widened by 6); `test_signal_parity` asserts it agrees
# with signals.py on every value in both vocabularies, because two foldings that drift would
# order the queue differently depending on which one asked.
def _score_sql() -> str:
    def lv(field):
        return ("CASE brain.signal_level('{f}', w.{f}) WHEN 'high' THEN 2.0 "
                "WHEN 'medium' THEN 1.0 ELSE 0.0 END").format(f=field)
    return (
        f"( %(w_urgency)s * {lv('urgency')}"
        f" + %(w_dependency_unblocking)s * {lv('dependency_unblocking')}"
        f" + %(w_charter_alignment)s * {lv('charter_alignment')}"
        f" + %(w_stakes)s * {lv('stakes')}"
        f" - %(w_effort)s * {lv('effort')}"
        f" + %(w_age_per_day)s * GREATEST(0, EXTRACT(EPOCH FROM (now() - w.created)) / 86400.0) )"
    )


# A dependency is met only when its root is in `done`. Active is not done, which is the property
# `t_depends` asserts and the reason a dependent cannot start while its root is still running.
_DEPS_MET = """
  NOT EXISTS (
    SELECT 1 FROM regexp_split_to_table(w.depends_on, ',') AS d(raw)
     WHERE btrim(d.raw) <> ''
       AND NOT EXISTS (SELECT 1 FROM brain.work_item p
                        WHERE p.id = lpad(btrim(d.raw), 4, '0') AND p.state = 'done')
  )"""


# ------------------------------------------------------------------ the lane ceiling
#
# THE SPEND BRAKE, AT THE ONE POINT THAT KNOWS THE LANE.
#
# `bin/swarm-run` asks `budget check` before it claims, which is the right place for it: a claim
# you then release is a claim another agent raced for, and `release` is conditional on still
# holding it, so the loser of that race gets nothing while the queue looks like it moved. But the
# runner cannot pass a lane it does not know yet -- the claim is what decides the lane -- so it
# passes `--lane` only for an agent with exactly one configured lane. Every terminal in the
# shipped default config is `lanes: ["*"]`, so for the whole default fleet a lane ceiling was not
# gated at dispatch at all. It still braked, one run later, when `budget charge` pushed the meter
# over and the NEXT pre-claim check refused: a lag of one full run per agent, not a hole.
#
# This closes the lag by putting the lane ceiling in the only statement that knows which lane it
# is about to hand out, alongside the `FOR UPDATE SKIP LOCKED` that makes the claim atomic. One
# transaction, one race, and a stopped lane is never handed out in the first place -- so nothing
# is released and no attempt is charged.
#
# PARITY WITH `budget.enforcer.evaluate` IS THE WHOLE POINT, and it is the part that is easy to
# get half right. A scope is stopped when EITHER of two things is true, and they live in two
# places:
#
#   brain.budget_state.stopping   spend >= ceiling AND hard_stop_enabled. A ceiling crossed with
#                                 hard_stop_enabled=false is a measurement, not a brake, and the
#                                 view already folds that in -- so a soft ceiling must NOT and
#                                 does not exclude the lane here either.
#   brain.budget_open_stop        an uncleared `manual_stop`. `budget stop --scope lane` needs no
#                                 policy and has no spend, so it appears in NEITHER the meter nor
#                                 `budget_state`. Reading only the spend view is exactly the
#                                 mistake `reads.open_stops` documents.
#
# `budget_open_stop` also carries `hard_stop` rows, and `evaluate()` deliberately does not treat
# those as blocking on their own -- a hard stop is spend-derived and re-derives from `stopping`,
# so honouring it twice would keep a lane down after the operator raised its ceiling. The `kind`
# filter below is what keeps the two gates answering the same question.
#
# LANE SCOPE ONLY. Fleet, agent and work_item ceilings are the runner's gate, which already knows
# all three before it claims. Refusing a fleet stop here as well would be a second brake with a
# worse reason string ("no claimable work") than the one the operator already gets.
#
# A CTE RATHER THAN TWO CORRELATED SUBQUERIES, and here is the honest version of why.
#
# `brain.budget_state` is a view whose spend column is a LATERAL `sum()` over `budget_charge`, so
# what it costs to ask "is this lane stopped" is not obvious from the SQL. Written first as two
# correlated `NOT EXISTS` against the views -- `b.scope_id = w.lane` inside each -- one measured
# plan re-executed that aggregate once per candidate row: `EXPLAIN (ANALYZE)` on a 60-row inbox
# reported `loops=60` on the `Bitmap Heap Scan on budget_charge`. **That plan did not reproduce**
# on a second store with the same shape, where every form came back `loops=1`, so it is a plan the
# planner may choose and not a property of the correlated form. Both facts are recorded because
# the second one is what stops the next reader from trusting a number this comment could have
# asserted.
#
# The CTE removes the question. The stopped-lane set is computed once by construction, and
# `AS MATERIALIZED` says so to the planner rather than hoping for it: the cost of the budget half
# stops depending on how deep the inbox is, which is the only property worth guaranteeing here.
# The anti-join then runs against a tuplestore holding one row per stopped lane, which in a
# healthy fleet holds none. Cheap insurance, not a measured necessity -- do not "optimise" it back
# into the WHERE clause without an EXPLAIN ANALYZE on a real board.
_LANE_BUDGET_CTE = """WITH budget_stopped_lane AS MATERIALIZED (
       SELECT b.scope_id AS lane FROM brain.budget_state b
        WHERE b.scope_type = 'lane' AND b.stopping
        UNION
       SELECT s.scope_id AS lane FROM brain.budget_open_stop s
        WHERE s.scope_type = 'lane' AND s.kind = 'manual_stop'
     )
     """

# `NOT EXISTS`, not `NOT IN`. `budget_state.scope_id` and `budget_open_stop.scope_id` are view
# columns and read as nullable whatever their base tables say, and one NULL in the right-hand side
# makes `NOT IN` answer NULL for every row -- which would silently stop the whole fleet claiming
# anything at all. The anti-join form is NULL-safe and is the shape the planner wanted anyway.
_LANE_NOT_BUDGET_STOPPED = """
               AND NOT EXISTS (SELECT 1 FROM budget_stopped_lane bs WHERE bs.lane = w.lane)"""


# THE PROJECT HOLD. Task 0430, out of web/MUST-NOT-BUILD.md item 3, overruled by the operator on
# 2026-08-28 against an incident with a bill behind it, in his words:
#
#     "there's certain projects where I kind of just wanted the AI to take a break with it. And
#      then I realized later that it worked on it for like eight hours with eight terminals and I
#      ran out of API tokens very quickly."
#
# Everything above this line already did the hard part. `budget stop` latches, only `budget resume`
# lifts it, the incident is typed and recorded, and the lane CTE proves the anti-join in `claim` is
# the right place to enforce it. WHAT WAS MISSING WAS THE GRAIN. A LANE IS NOT A PROJECT: lanes are
# kinds of work, so three projects running in `exec` shared one switch and a project spanning two
# lanes had none. The control existed at every grain except his. This is his grain, on the proven
# path, and it is the same anti-join with two differences that are both deliberate.
#
# DIFFERENCE 1: THE SECOND ARM IS THE BOARD, NOT THE METER. The lane CTE unions two halves because
# a lane can be stopped two ways -- `budget_state.stopping` (spend over a HARD ceiling) and an
# uncleared `manual_stop`. A project also has two, and they are not the same two.
#
#   the operator's latch    `budget stop project <slug>` -- an uncleared manual_stop, exactly the
#                           lane's second half, and the only thing `budget resume` lifts.
#   the project's state     `brain.project.state <> 'in_progress'` -- migration 44's column,
#                           carrying the operator's own four words: in progress / hold / ice /
#                           blocked.
#
# WHY BOTH, rather than making the board write a latch. Two mechanisms that must agree is the
# thing to avoid, but this is one QUESTION -- "is this project at rest" -- asked of the two tables
# that can answer it, unioned into one set, which is precisely the shape the lane CTE already has.
# The alternative, a `project set state` verb that also files a budget incident, gives two records
# of one decision that drift the first time either is written without the other.
#
# WHY ALL THREE RESTING STATES AND NOT JUST `hold`. Migration 44 built
# `project_resting_idx ON brain.project (state, slug) WHERE state <> 'in_progress'` and its own
# comment says "the board reads this order and the throttle reads the resting set" -- the entity's
# author shaped the index for this predicate, having deliberately left the choice to this row
# ("WHICH resting states throttle is the verb row's decision"). It is also the fail-closed
# direction: a project the operator moved OFF `in_progress`, by any of his three words, is one he
# does not expect to be spending his money. To narrow it later -- `blocked` is the arguable one --
# change the one `<>` below and nothing else.
#
# WHAT IS NOT HERE: A SPEND ARM. `brain.budget_charge` carries agent, lane and work_item
# dimensions and NO PROJECT, so a project ceiling would meter 0.00 against the operator's limit in
# every window forever. Migration 45 refuses to store such a policy at all
# (`budget_policy_no_project_ceiling`), so `budget_state` can never hold a project row and a
# `stopping` arm here would be a branch that cannot fire -- exactly what migration 3's own enum
# comment warns about ("a typo in a text column becomes a branch that silently never fires, and
# the branch that never fires here is the one that stops spending"). The day `budget_charge` grows
# a project dimension it goes in, in the same change that drops that CHECK and never before it.
#
# DIFFERENCE 2: `scope_type::text`, NOT THE ENUM LITERAL, AND THIS ONE IS LOAD-BEARING. The
# 'project' label arrives in migration 45; the lane arm's `scope_type = 'lane'` works because
# 'lane' has been in `brain.budget_scope` since migration 3. Against a store with 3 and not 45,
# `s.scope_type = 'project'` does NOT return the empty set -- it RAISES `invalid input value for
# enum brain.budget_scope: "project"`, inside the statement that hands out every task on the
# fleet. A budget migration one version behind would stop the whole fleet claiming, which is the
# precise failure `_budget_schema_present` exists to prevent for migration 3. The cast makes both
# sides text and cannot raise. `_project_scope_present` is the belt to this suspenders, and both
# stay: the probe keeps `brain.work_item_project` from being named when it does not exist, and the
# cast keeps the enum from being coerced even if a future edit drops the probe.
#
# The MATERIALIZED keyword and the `NOT EXISTS` form are the lane CTE's, for the lane CTE's stated
# reasons; read those above rather than having them restated here. The NULL-safety argument is if
# anything stronger for this arm: `brain.work_item_project(w)` returns NULL for every row that is
# in no project, which today is every row on every database (0 of 345 on `brain`, measured
# 2026-08-28, task 0430). `NOT IN` would answer NULL for all of them and stop the fleet dead.
_PROJECT_BUDGET_CTE = """, budget_stopped_project AS MATERIALIZED (
       SELECT s.scope_id AS project FROM brain.budget_open_stop s
        WHERE s.scope_type::text = 'project' AND s.kind = 'manual_stop'
        UNION
       SELECT p.slug AS project FROM brain.project p
        WHERE p.state <> 'in_progress'"""

# THE ARCHIVE ARM. Migration 47, bus row 0443, decision D-05, and it is the load-bearing half of
# that decision rather than a tidy-up.
#
# The operator named four project states and none of them is terminal, so a project entered on the
# board stays on it forever. He was asked and chose an ARCHIVED STAMP over a fifth word, because
# `in progress / hold / ice / blocked` are his own four and naming his vocabulary is his.
#
# THE TRAP THAT MAKES THIS LINE NECESSARY. As first described the stamp was "not a board column,
# just an absence from the board" -- and an archived project keeps `state = 'in_progress'`, so the
# arm above lets it straight through and the claim path GOES ON HANDING OUT ITS WORK. He would go
# on paying for projects he archived, which is his eight-terminals incident restated in a new
# place. Migration 34 is the precedent and the tell is that its FIRING path reads the disable:
# `routine_armed_idx ... WHERE disabled_at IS NULL`. Without this arm the whole feature is
# decoration.
#
# IT IS A THIRD UNION MEMBER AND NOT AN `OR` ON THE SECOND, and the reason is the probe below.
# Migration 47 is a separate version from 44 and 45, so there are stores that carry the project
# table and the hold gate and NOT the column: on those, naming `p.archived_at` raises
# `UndefinedColumn` INSIDE THE STATEMENT THAT HANDS OUT EVERY TASK ON THE FLEET, which is exactly
# the failure `_project_scope_present` and the `scope_type::text` cast were both written to
# prevent, arriving two migrations later by the same door. Splicing it in only when
# `_project_archive_present` says the column is there costs one catalogue lookup per claim and
# cannot raise.
_PROJECT_ARCHIVED_ARM = """
        UNION
       SELECT p.slug AS project FROM brain.project p
        WHERE p.archived_at IS NOT NULL"""

_PROJECT_BUDGET_CTE_CLOSE = """
     )
     """

# `brain.work_item_project(w)` passes the WHOLE ROW, not `w.canonical_task`, and that is the seam
# with task 0429 rather than a flourish. The function is the single definition of "which project
# is this row in" (migration 45); when 0429's `brain.project` row lands, its body is re-pointed
# with one CREATE OR REPLACE and this line does not change. A `(text)` signature would have pinned
# the answer to one column and made every call site an edit on that day.
#
# NULL is the correct answer for a row in no project, and it needs no special case: NULL never
# equals a stopped scope_id, so the anti-join lets the row through, which is right.
_PROJECT_NOT_BUDGET_STOPPED = """
               AND NOT EXISTS (SELECT 1 FROM budget_stopped_project bp
                                WHERE bp.project = brain.work_item_project(w))"""


#: How long an agent row may go without a heartbeat before `claim` reads it as a CORPSE rather
#: than a holder. Deliberately the SAME number as `reap`'s `--stale-seconds` default, and the
#: equality is the whole un-wedge argument: the set of tasks this gate withholds and the set of
#: agents `reap` will pronounce dead are exact complements at every instant, so there is no window
#: in which a task is both unclaimable by this gate and untouchable by the recovery verb. Make
#: this LONGER than reap's default and you invent that window; make it shorter and a live agent
#: whose heartbeat merely stuttered gets its task taken, which is the bug.
#:
#: 900s against `bin/swarm-run`'s `SWARM_HEARTBEAT_SECONDS` default of 60 is a 15x margin, and the
#: heartbeat is a background `while sleep` loop that does not stop while the engine is thinking --
#: so 15 consecutive missed beats means the runner process itself is gone, not that a turn ran
#: long. `answer`'s own live-asker guard already uses 15 minutes; this is that number, shared.
CLAIM_LIVENESS_SECONDS = 900

# THE LIVENESS GATE, and it is three lines inside the statement that already holds the row lock.
#
# 2026-08-18: `swarm answer` requeued a task its asking agent still held, T4 claimed it, and two
# live agents ran one brief for five minutes. The requeue was one producer of that state; it is
# not the only one, and fixing producers one at a time is how the same incident arrives by a new
# door every fortnight. Measured on this tree the same day: `_hold` calls a task held only when
# `state = 'active'`, so a task `ask` moved to `blocked` is still `claimed_by = T5` and still has
# a live T5 on it while `reopen`, `fail` and `set state` all walk straight through -- and `reopen`
# on a blocked task is the commander's ORDINARY recovery move, used on tasks 0400 and 0407
# themselves at 08:13Z and 08:31Z that morning.
#
# So the check goes at the CONSUMER instead. `claim` is the single transition point through which
# every one of those producers must eventually pass to do harm: a task sitting in `inbox` under a
# live agent is untidy, a task HANDED OUT from under one is the incident. Whatever put the row
# there, it stops here.
#
# WHY IT IS A WHERE CLAUSE AND NOT A SEPARATE READ. The brief's constraint is that the claim path
# measured by `tests/test-claim-contention.py` -- 12 pre-connected claimers, barrier-released,
# every item claimed exactly once -- must not regress, and that atomicity must not be weakened to
# buy the check. An anti-join inside the existing `FOR UPDATE SKIP LOCKED` select adds no
# statement, no round trip and no second lock: the candidate is chosen and locked in the same
# breath it is tested for a live owner, so there is no interval in which the answer could go
# stale. Doing it as a read before the select would have exactly that interval, and it is the
# interval the incident happened in.
#
# `a.name <> %(agent)s` is not tidiness. A runner that died and came back under its own name has
# a stale row of its own pointing at the task; without the self-exclusion the agent would be
# fenced out of its own work by its own ghost, which is a wedge this gate would have created.
_NOT_HELD_BY_A_LIVE_AGENT = """
               AND NOT EXISTS (
                     SELECT 1 FROM brain.agent a
                      WHERE a.work_item_id = w.id
                        AND a.status = 'working'
                        AND a.name <> %(agent)s
                        AND a.updated > now() - make_interval(secs => %(liveness)s))"""


# ------------------------------------------------------------------ the actor gate
#
# THE OPERATOR'S OWN QUEUE IS IN THIS TABLE, AND UNTIL MIGRATION 26 NOTHING HERE KNEW IT.
#
# `brain.work_item` is the fleet's dispatch queue and it is ALSO the operator's day. Measured on
# live `brain` on 2026-08-18, before this predicate existed: 24 rows in `inbox`, of which 15 were
# posted by `operator` -- 0058..0073 less 0064 -- including a credential rotation, a
# contract to send to a client and a QC result to send to a client. Every terminal in the shipped fleet config is
# `"lanes": ["*"]`, this statement had no actor predicate, and the V1 cutover repoints that fleet
# at this store. The first poll after it would have ranked his day alongside the engine backlog
# and handed one of those rows to whichever terminal asked first. Two of them send external mail;
# one rotates live credentials.
#
# WHY IT IS HERE AND NOT IN THE FLEET CONFIG. Narrowing every terminal's `lanes` to the
# engineering lanes reads like the cheap fix and is not a guard: it lives outside the transition,
# it fails open the moment the operator invents a lane, and it is the "surface reimplements a
# transition" shape the narrow waist exists to forbid. `claim` is the one point every dispatch
# passes through. Anywhere else is a second door.
#
# WHY IT IS A WHERE CLAUSE AND NOT A POST-CLAIM RELEASE. A claim that is then released has
# already incremented `attempts`, already written a `claim` event on the thread, and already
# raced every other claimer for the row. A refusal that costs the task an attempt is not a
# refusal, it is a claim with an apology attached; on `max_attempts = 2` two polite agents would
# exhaust the operator's task between them without either doing any work.
#
# WHY THE COLUMN AND NOT `actor_type`. See migration 26's header: two of those 15 rows carry
# `actor_type = NULL`, not `'human'`, and they were created inside the same 1.7 seconds as rows
# that carry it. The human mark had already been forgotten twice on the operator's real data.
_AGENT_CLAIMABLE = """
               AND w.agent_claimable"""


def _actor_gate_present(ctx) -> bool:
    """Is migration 26 applied to THIS database? Asked, never assumed -- and fail CLOSED.

    The shape is `_budget_schema_present`'s and the ANSWER TO ABSENCE IS THE OPPOSITE, on
    purpose. A store with no budget schema has no spend brake to arm and dispatching anyway is
    correct: the brake was never installed. A store with no actor gate cannot tell the operator's
    work from the fleet's AT ALL, and the failure mode is not a missed measurement, it is a
    contract mailed to a client. So `claim` refuses outright rather than handing out rows it
    cannot classify, and it says which migration is missing instead of raising `UndefinedColumn`
    from inside a locking statement.

    Asked per claim rather than cached, for `_budget_schema_present`'s reason: a cached "absent"
    would keep the fleet refusing for the life of the process after the migration landed.

    `pg_attribute` AND NOT `information_schema.columns`, and the difference is MEASURED rather
    than assumed. `information_schema.columns` is a view over five catalogs with a privilege test
    per row; asking it once per claim cost the contention probe (12 pre-connected claimers,
    barrier-released, 20 items) a median per-claim 80.6ms -> 125.9ms on 2026-08-18. The
    `pg_attribute` form is one index lookup and gave that back. A gate that is right and slows
    every dispatch by half is a gate somebody removes.

    `to_regclass` rather than a cast, for `_budget_schema_present`'s reason: the cast RAISES on a
    missing relation and would abort the transaction; `to_regclass` returns NULL, `attrelid =
    NULL` matches nothing, EXISTS is false, and the claim refuses. Fail closed on both halves.
    """
    return bool(ctx.scalar(
        "SELECT EXISTS (SELECT 1 FROM pg_attribute "
        "                WHERE attrelid = to_regclass('brain.work_item') "
        "                  AND attname = 'agent_claimable' AND NOT attisdropped)"))


def _budget_schema_present(ctx) -> bool:
    """Is migration 3 applied to THIS database? Asked, never assumed.

    `budget/schema/0003_budget.sql` is not in `migrations/`, so `bin/scratch-db.sh` does not apply
    it and every scratch database starts without it. `bin/swarm-run` supports that state on
    purpose -- it says THE SPEND BRAKE IS NOT ARMED and dispatches anyway -- so the claim has to
    support it too. Naming a missing relation in the candidate query would raise `UndefinedTable`,
    abort the transaction, and stop the entire fleet claiming: a spend brake that takes the fleet
    down when it is not installed is worse than the lag it was fixing.

    `to_regclass` returns NULL rather than raising for a missing relation or a missing schema, so
    this is one catalog lookup and never an error. It is asked per claim rather than cached: a
    cached "absent" would leave the brake off for the life of the process after a migration
    landed, which is the failure mode that costs money.
    """
    return bool(ctx.scalar("SELECT to_regclass('brain.budget_state') IS NOT NULL "
                           "   AND to_regclass('brain.budget_open_stop') IS NOT NULL"))


def _project_scope_present(ctx) -> bool:
    """Are migrations 44 AND 45 applied to THIS database? Asked, never assumed. Task 0430.

    `_budget_schema_present`'s answer is not this one. That probe tests migration 3, which creates
    the type `brain.budget_scope`; the label 'project' and the function `brain.work_item_project`
    arrive twelve migrations later in `budget/schema/0045_project_is_a_hold_scope.sql`. Gating the
    project arm on migration 3 would name a function that does not exist on every store between
    the two, and `UndefinedFunction` inside the claim statement is the entire fleet unable to take
    work -- the same failure `_budget_schema_present` was written to prevent, arriving one
    migration later by the same door.
    """
    # It returns False when the budget schema is absent, so a caller cannot arm the project arm
    # without the lane arm. That is not tidiness: `_PROJECT_BUDGET_CTE` begins with a comma and is
    # a CONTINUATION of the lane CTE's `WITH`, so on its own it is a syntax error. Migration 45
    # cannot be applied without migration 3 (it ALTERs a type 3 creates), so the two can only
    # disagree on a store somebody has taken apart by hand -- and the safe answer there is to
    # enforce nothing rather than to refuse everything.
    if not _budget_schema_present(ctx):
        return False
    # pg_proc rather than `to_regprocedure('brain.work_item_project(brain.work_item)')`: that form
    # has to resolve the composite type named in the signature and raises if it cannot, which is
    # the thing being guarded against. This lookup cannot raise.
    #
    # BOTH OBJECTS THE PROJECT ARM NAMES, not just the function. The arm reads `brain.project`
    # (migration 44) as well as calling `brain.work_item_project` (migration 45), and 45 RAISEs
    # rather than applies if 44 is absent -- so on any store built in order, one implies the
    # other. This asks for both anyway: the cost is one catalog lookup and the alternative is that
    # a hand-assembled store turns `UndefinedTable` into the fleet's inability to claim, which is
    # the failure this whole family of probes exists to prevent.
    return bool(ctx.scalar(
        "SELECT to_regclass('brain.project') IS NOT NULL"
        "   AND EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "                WHERE n.nspname = 'brain' AND p.proname = 'work_item_project')"))


def _project_archive_present(ctx) -> bool:
    """Is migration 47 applied to THIS database? Asked, never assumed. Bus row 0443, D-05.

    A THIRD PROBE AND NOT A WIDENING OF THE SECOND, for the reason `_project_scope_present` gives
    about the first: the archive column arrives two migrations after the project table and three
    after the budget schema, so there are real stores that carry `brain.project` and the hold gate
    and not `archived_at`. Naming that column on one of them raises `UndefinedColumn` inside the
    statement that hands out every task on the fleet, and a fleet that cannot claim is a worse
    outcome than an archive that is not yet enforced.

    `pg_attribute` rather than `information_schema.columns`, for `_actor_gate_present`'s MEASURED
    reason: the information_schema form is a view over five catalogs with a privilege test per
    row and cost the contention probe a median 80.6ms -> 125.9ms per claim. `to_regclass` returns
    NULL rather than raising for a missing relation, so `attrelid = NULL` matches nothing and this
    answers False instead of aborting the transaction. Fail closed on both halves.

    It does NOT re-ask `_project_scope_present`'s question: the caller splices this arm into a
    CTE that arm already opened, so the two are asked together at the one call site and an archive
    arm can never be armed without the project arm around it.
    """
    return bool(ctx.scalar(
        "SELECT EXISTS (SELECT 1 FROM pg_attribute "
        "                WHERE attrelid = to_regclass('brain.project') "
        "                  AND attname = 'archived_at' AND NOT attisdropped)"))


def _flags(ctx, task_id: str) -> dict:
    """The two hard flags, ORed up the WHOLE parent chain. D00 rule 8, EF-7.

    Resolved on every read rather than stamped at post time, so raising a flag on a parent
    raises it on every descendant retroactively. Stamping alone holds for exactly one hop, and
    a two-hop chain in which each hop looks compliant on its own is flag laundering.

    Since migration 12 the view also answers `lineage_cycle`: TRUE means the walk could not
    resolve this chain (a loop, or deeper than `brain.lineage_depth_cap()`) and both flags are
    reading TRUE because an unreadable ancestry fails closed, not because an ancestor set them.
    """
    row = ctx.one("SELECT external, canon_touching, lineage_cycle "
                  "FROM brain.work_item_signals WHERE id = %s", (task_id,))
    return row or {"external": False, "canon_touching": False, "lineage_cycle": False}


def _refuse_cycle(ctx, task_id: str, new_parent: str) -> None:
    """Refuse the one edge that makes the flag walk non-terminating. THE 2026-08-16 OUTAGE.

    A cycle in `parent` is not a bad row, it is an outage: `brain.work_item_signals` walked the
    chain with no cycle guard, so ONE loop made the view non-terminating for EVERY row, `claim`
    hung inside libpq holding its `FOR UPDATE` lock, and the fleet stopped claiming until a human
    found the backends by hand.

    Migration 12 bounds the read so that arriving cycle degrades instead of hanging. This is the
    other half, and it is the cheaper one: refusing the edge means the degraded state is never
    entered by a verb at all. The database refuses it a second time in
    `brain.work_item_refuse_parent_cycle`, for the routes no verb owns.
    """
    path = ctx.scalar("SELECT brain.parent_cycle_path(%s, %s)", (task_id, new_parent))
    if path:
        raise VerbError(
            f"refusing to make {new_parent} the parent of {task_id}: that closes the parent "
            f"cycle {' -> '.join(path)}. Hard flags are resolved by walking this chain on every "
            f"read, so a loop in it does not break one row -- on 2026-08-16 it made "
            f"brain.work_item_signals non-terminating for EVERY row and `claim` hung holding its "
            f"row lock.", code=7)


def _refuse_poisoned_chain(ctx, task_id: str, what: str) -> None:
    """Refuse to build on an ancestry the database cannot read, and say how to repair it.

    A cycle that arrived by a route no verb owns -- a restore runs with triggers disabled -- makes
    every flag under it fail closed. Inheriting that silently would gate a descendant forever with
    no explanation on its thread, so this refuses in words instead. It is bounded and instant,
    which is the whole difference from what this used to do, which was not return.
    """
    if not _flags(ctx, task_id).get("lineage_cycle"):
        return
    parent = ctx.scalar("SELECT parent FROM brain.work_item WHERE id = %s", (task_id,))
    path = ctx.scalar("SELECT brain.parent_cycle_path(%s, %s)", (task_id, parent)) if parent else None
    named = f" ({' -> '.join(path)})" if path else ""
    raise VerbError(
        f"{what}: {task_id}'s parent chain does not resolve{named}. Its hard flags are reading "
        f"true because an unreadable ancestry fails closed, not because an ancestor set them. "
        f"Repair the chain first -- `swarm set {task_id} parent <a-real-ancestor>`, or clear it "
        f"-- then retry. Read `brain.work_item_signals.lineage_cycle` to find every affected row.",
        code=7)


def _caller(agent: str = "") -> str:
    """Who is actually running this, or "" when the answer would be a guess.

    `swarm set` took no `--agent` at all until this afternoon and the transition defaulted to
    `agent="operator"`, so the thread on 0044 records `operator [set] external = False` for an act
    T2 performed. The one record of who cleared a hard flag named the person who did not do it.

    Three sources, in order: what the caller said, what the terminal exports (`SWARM_AGENT`, the
    same variable the MCP server reads), and -- only outside a fleet terminal -- the operator. A
    process running a task (`SWARM_PARENT_TASK` is set) that names nobody gets "" and its verb
    refuses, because an unattributed write on a security-relevant field is worse than no write.
    """
    named = (agent or "").strip() or os.environ.get("SWARM_AGENT", "").strip()
    if named:
        return named
    return "" if os.environ.get("SWARM_PARENT_TASK", "").strip() else "operator"


def _require(ctx, task_id: str) -> dict:
    row = ctx.one("SELECT * FROM brain.work_item WHERE id = %s", (task_id,))
    if not row:
        raise VerbError(f"no such task: {task_id}")
    return row


# ------------------------------------------------------------------ the claimer predicate
#
# THE 2026-08-16 INCIDENT, GENERALISED. D4 put `WHERE claimed_by = %(agent)s` on `release` and on
# nothing else, so `fail`, `reopen`, `block`, `done` and `cancel` all still ended or moved work a
# DIFFERENT agent was holding -- from the runner's reconciliation block, and from the shipped MCP
# `finish_work` tool. The adversarial pass measured every one of them
# (`outputs/2026-08-16-D9-adversarial/FINDINGS.md`, "the release fix was applied to `release` and
# to nothing else").
#
# So the predicate lives HERE, in one function, and `_hold` below is the ONE door every such verb
# loads its task through. A verb added later that wants to end a task gets the check by using the
# door; `test_no_finisher_forgets_the_claimer_predicate` reads this module's source and fails if a
# new one writes `state` on `brain.work_item` without going through it.
#
# Three things it deliberately is NOT:
#
#   * It is not a lock. Two agents on one task is still detected by `doctor` and still cleaned up
#     by `reap`, and neither is weakened here: `reap` carries its own `claimed_by = ` predicate
#     against the agent it just found dead, which is the holder by construction.
#   * It is not authentication. Any caller can pass any `--agent`. It stops the CONFUSED caller --
#     a dying runner acting on what it thinks it holds -- which is what the incident was.
#   * It is not silent. A non-holder gets a refusal, never a no-op, and the deliberate override is
#     `force=True`, which must be signed and lands on the thread.

# THE REPAIR FOR TASK 0119, written once and appended to whichever refusal `_hold` raises.
#
# WHAT A TERMINAL MID-RUN ACTUALLY NOTICES, measured rather than hoped: nothing, by itself. A
# terminal's whole prompt is built ONCE, at dispatch, by `bin/swarm-run` shelling out to
# `swarm show "$TASK_ID"`, and the only place that script reads `swarm inbox` is a PLANNER wake
# (bin/swarm-run:851). No terminal polls its inbox, and the heartbeat loop's stdout goes to a log
# no model reads. So a `msg` to a running agent is deliverable, not delivered.
#
# What DOES reach a mid-run model is the stdout of the verbs it calls itself, which it reads as a
# tool result. `swarm note` and `swarm artifact` are the two a working agent calls repeatedly, and
# `cli._held_while_active_banner` prints on both. That is the honest answer: the signal is
# opportunistic and arrives on the agent's next bus call, which for a long silent stretch of work
# is not soon enough. If the run must stop NOW, `cancel --force` stops it and this does not.
_LOWERING_A_HELD_ROW = (
    "THIS IS NOT A WAY TO STOP A RUNNING AGENT, and lowering the flag here would not have been "
    "one (task 0119, the 0103 incident): `claim` only reads rows in `inbox`, so on an ACTIVE row "
    "the flag changes nothing today and only holds the row's future. Say which you mean:\n"
    "  * to STOP the run          `swarm cancel <id> --agent <you> --force`\n"
    "  * to take it BACK to the queue, unheld and reclaimable  ask the holder to `swarm release`, "
    "or `swarm fail <id> --agent <you> --force`\n"
    "  * to hold the row's FUTURE and let this run finish      add --force here. It is signed, it "
    "lands on the thread, the holder is messaged, and it does NOT stop the run."
)


def _hold(ctx, task_id: str, agent: str, *, force: bool = False, advice: str = "") -> dict:
    """Load a task for a verb that ends or moves it, refusing a caller that does not hold it.

    Held means `state = 'active'` with a non-empty `claimed_by`. A task nobody holds (inbox,
    done, blocked, cancelled, or active with the claim already cleared) has no live work to take,
    so those calls pass through untouched: `reopen` on finished work and `block` on an unclaimed
    task are ordinary operator acts and stay ordinary.

    AN ANONYMOUS CALLER IS REFUSED TOO, and that is a deliberate reading rather than an accident
    of the comparison. `swarm done ID --summary ...` with no `--agent` reaches here as `''`, and
    the store cannot tell the holder who did not type their name from a stranger who did not
    want to. Letting `''` through would make the whole predicate opt-in -- omit one flag and it
    is gone -- which is the exact backwardness this task exists to invert. It is also already
    settled elsewhere in this tree: `mcp/tools.py:_actor` refuses an unattributed call outright,
    because "an unattributed write is a row nobody can hold to account", and `_finish` files the
    thread actor as `claimed_by` when the caller is anonymous, so an anonymous `done` on held
    work is recorded as the HOLDER'S report whoever typed it.

    The blast radius is small and it was measured, not guessed: `bin/swarm-run` passes `--agent`
    on every reconciliation call, the MCP tools cannot be anonymous, and a task nobody holds is
    not gated at all. What is refused is exactly one thing -- an unnamed caller ending work an
    agent is actively holding -- and it gets its own message below rather than the mismatch one,
    because "you did not say who you are" and "you are not the holder" are different repairs.

    `advice` is one verb-specific sentence appended to whichever refusal fires. The predicate is
    the same for every caller and stays in this one function; what differs is the REPAIR, and
    `set agent_claimable false` has a different one from `done` (task 0119: the repair is
    `release`/`cancel`, or a signed hold that is honest about not stopping the run). A second
    copy of the predicate written next to a better message is how a guard grows a door beside it.
    """
    task = _require(ctx, task_id)
    holder = (task.get("claimed_by") or "").strip()
    caller = (agent or "").strip()
    if task.get("state") != "active" or not holder or caller == holder:
        return task

    # A refusal whose whole job is to say what to type must name the flag THIS verb actually
    # takes. `swarm reopen` spells it `--from` (argparse `dest="agent"`), every other gated verb
    # spells it `--agent`, and telling a runner to pass a flag that does not exist on the verb it
    # just called is how a good error message becomes a wrong one.
    flag = "--from" if ctx.verb == "reopen" else "--agent"

    if not force and not caller:
        raise VerbError(
            f"{task_id} is held by {holder} and `{ctx.verb}` was called by nobody: no {flag} "
            f"was given. Say who you are. If you are {holder}, pass {flag} {holder}; if you are "
            f"the operator ending {holder}'s live run, pass {flag} <you> --force, which is "
            f"written to the task's thread. An anonymous report on a held task is filed against "
            f"{holder} whoever typed it, so this refuses rather than forges."
            + (f"\n{advice}" if advice else ""),
            code=6)
    if not force:
        raise VerbError(
            f"{task_id} is held by {holder} and `{ctx.verb}` was called by "
            f"{caller}. A verb may only act on a task the caller still "
            f"holds. This is the 2026-08-16 incident: a runner acting on what it THOUGHT it held "
            f"ended a live agent's work. Pass {flag} {holder} if you are {holder}, or --force "
            f"to override this deliberately (the override is written to the thread)."
            + (f"\n{advice}" if advice else ""),
            code=6)
    if not caller:
        # An override nobody signed is not a logged override, it is an anonymous one. `_finish`
        # falls back to `claimed_by` for the thread actor, so an unsigned force would file the
        # displaced agent's name against an act it did not perform: forgery, in the record.
        raise VerbError(
            f"--force on {task_id} needs --agent: the override is recorded on the thread and an "
            f"override nobody signed would be filed against {holder}, who did not do it.",
            code=6)
    ctx.actor = caller
    ctx.thread(task_id, "note",
               f"OVERRIDE: `{ctx.verb}` forced by {caller} on {task_id}, which {holder} holds. "
               f"The claimer predicate was overridden deliberately, and {holder} was not asked.")
    return task


# ------------------------------------------------------------------ the workdir gate


def _check_fleet_workdir(claimable: bool, workdir: str) -> None:
    """A row the fleet may claim has to say WHERE, and say it absolutely. Task 0100.

    The workdir is the boundary a terminal is forbidden to leave (D00 rule 7). An EMPTY one is
    not a lenient boundary, it is no boundary: `engine/bin/swarm-run` used to fall through to the
    agent PROFILE's own directory, which is `str(Path.home())` in the shipped DEFAULT_CONFIG and
    `/mnt/c/Users/you/repos` in the live fleet config as of 2026-08-18. Both are trees nobody
    chose for the task, and both are PARENTS of every repo on the box, so a brief's relative
    paths resolve somewhere real and wrong rather than failing. Measured cost: task 0051's agent
    was handed workdir `/home/you`, found no `outputs/` tree there, and had to infer the
    right repo out of the brief -- and it created an orphan `/home/you/outputs` while
    probing.

    REFUSED, not defaulted. There is no correct default: a client task and an `engine` task
    live in different repos, so any single fallback trades one wrong tree for another and hides
    the mistake behind a value that looks deliberate. The poster is the only party that knows.

    ONLY WHEN `agent_claimable`. `brain.work_item` is the operator's own queue as well as the
    fleet's, and 15 of the 19 live rows carrying an empty workdir on 2026-08-18 were his
    (a video to record, a personal document to renew). Those are never dispatched to a
    terminal and never cd anywhere, so requiring a directory of them would break his queue to
    guard a door they do not use. The other four were agent-claimable and were the whole defect.

    ABSOLUTE, because the runner does a bare `cd "$RUN_DIR"` and a relative path resolves against
    whatever directory `swarm-run` happens to be sitting in -- the same silent-wrong-tree failure
    with one more step. `~` is allowed: the runner expands it, and a caller who writes it means it.
    """
    if not claimable:
        return
    wd = str(workdir or "").strip()
    if wd and (wd.startswith("/") or wd.startswith("~")):
        return
    if not wd:
        raise VerbError(
            "refusing an agent-claimable task with no workdir. The workdir is the boundary a "
            "terminal may not leave, and an empty one is not a boundary: the runner has nothing "
            "to cd to but the agent profile's own directory, which is the parent of every repo "
            "on the box, so every relative path in the brief resolves in a tree nobody chose. "
            "Pass --workdir <absolute path that exists on the target host>. There is no default "
            "worth guessing -- a task in another repo would silently run in this one. A task "
            "with no workdir is legitimate ONLY while it is held for the operator, who reads it "
            "himself and is never dispatched to a terminal.", code=6)
    raise VerbError(
        f"refusing an agent-claimable task whose workdir is relative: {wd!r}. The runner does a "
        f"bare `cd`, so a relative path resolves against wherever swarm-run happens to be and "
        f"lands the terminal in a tree nobody chose. Give an absolute path, or one starting "
        f"with `~`.", code=6)


# ------------------------------------------------------------------ post

@store.transition("post")
def post(ctx, *, title, lane, host="", body="", priority=3, workdir="", depends_on="",
         posted_by="operator", max_attempts=2, parent="", external="", canon_touching="",
         signals=None, canonical_task=None, project=None, produced_by=None, produced_by_ref=None,
         resolution_status=None, actor_type=None, agent_claimable=None):
    """Create a task. Hard flags inherit by OR from the parent and can be raised, never lowered.

    `agent_claimable` DEFAULTS TO FALSE, and that is the guard, not an oversight. A row nobody
    classified is the operator's: `brain.work_item` holds his day as well as the fleet's queue,
    and on 2026-08-18 fifteen of the twenty-four claimable rows on live `brain` were his. It
    inherits from `parent` when one is given -- a child of fleet work is fleet work, which is how
    a terminal handing off adjacent work with `--parent` keeps the handoff claimable without
    anyone passing a flag -- and a caller that means "the fleet should do this" says so once, at
    the root, with `swarm post --for-agents`.

    UNLIKE THE TWO HARD FLAGS, THIS ONE INHERITS BY COPY AND NOT BY OR. `external` and
    `canon_touching` may be raised and never lowered because raising them is the cautious
    direction. Here the cautious direction is the OTHER one: `true` is the permissive value, so a
    child under a held parent must not be able to raise itself out of the hold. An explicit
    `agent_claimable=False` on a child of fleet work is honoured; an explicit `True` under a held
    parent is refused, out loud, for the same reason `post` refuses to lower a hard flag.

    Forgetting it on FLEET work is a task that sits in `inbox`, printed `[OPERATOR]` by
    `swarm ls`, reported by `swarm doctor` as posted-for-nobody and named by
    `swarm claim --explain`. Forgetting it on the OPERATOR'S work -- which is what actually
    happened twice on 2026-08-17, see migration 26 -- used to be an agent rotating a credential.
    Only one of those two failures announces itself, and the flag now lives on that side.

    THE LINEAGE TRIPLE. `produced_by` is one third of a fact, and on its own it cannot say the
    thing this system most needs said: that a reference WAS resolved and came back ambiguous.
    A NULL producer with no status reads as "nobody asked", so an ambiguous resolution written
    through `produced_by` alone is indistinguishable from an unattributed row -- it silently
    collapses into `unresolved`, which is the distinction D2 built on purpose.

    All three arrive together from `brain_adapter.store_join.lineage_columns(resolution)` and
    are never assembled here:

        store.apply("post", title=..., lane=..., **lineage_columns(res))

    One producer of the triple across engine, budget, fabric and ingest, so the failure case
    cannot drift between four lanes. Passing none of the three still means "no resolution was
    attempted", which is what every existing caller keeps getting, and it is why this change
    breaks nothing. `work_item_lineage_coherent` is the judge, and it is the only one.
    """
    parent = norm_task_id(parent) or norm_task_id(os.environ.get("SWARM_PARENT_TASK", ""))
    p_ext = p_canon = p_claimable = False
    if parent:
        if not ctx.one("SELECT 1 FROM brain.work_item WHERE id = %s", (parent,)):
            # A parent that does not resolve would silently drop the inheritance, which is how
            # a hard flag gets laundered by accident rather than by intent. Refuse.
            raise VerbError(f"no such parent task: {parent}")
        # A new row cannot close a loop by itself -- nothing points at it yet -- but a parent
        # whose OWN chain is already cyclic is the case that used to hang this verb for 30s and
        # counting. Bounded now, and refused in words rather than inherited silently.
        _refuse_poisoned_chain(ctx, parent, f"cannot post under {parent}")
        pf = _flags(ctx, parent)
        p_ext, p_canon = bool(pf["external"]), bool(pf["canon_touching"])
        p_claimable = bool(ctx.scalar(
            "SELECT agent_claimable FROM brain.work_item WHERE id = %s", (parent,)))

    # GAP CLOSED BY MIGRATION 6. D4 filed to D-CROSSTALK slot 7 that migration 1's CHECK carried
    # the word aliases and neither numeric branch, so `--dependency-unblocking 5` was refused here
    # in words rather than as a bare Postgres CHECK violation. D4 deliberately did NOT fold `5` to
    # `high` behind the caller's back: that would have discarded the count and hidden the gap.
    # Migration 6 widens `brain.signal_ok` instead, so the raw string is now STORED as the caller
    # wrote it and folded only on read. The refusal is deleted, not replaced by a coercion, and
    # `validate_signal` is the single gate again -- it accepts exactly what `signal_ok` accepts.
    declared = {f: validate_signal(f, (signals or {}).get(f, "")) for f in LEVELLED_SIGNALS}
    own_ext, own_canon = truthy(external), truthy(canon_touching)
    ext, canon = own_ext or p_ext, own_canon or p_canon

    # Explicit wins, then the parent, then held. `agent_claimable is None` is "nobody said", and
    # nobody-said is the operator's -- see the docstring. A child may always be held back from
    # the fleet; it may not promote itself out of a held parent, which would launder the
    # operator's subtree into the queue one hop at a time exactly as a cleared hard flag used to.
    if agent_claimable is None:
        claimable = p_claimable
    else:
        claimable = bool(agent_claimable)
        if claimable and parent and not p_claimable:
            raise VerbError(
                f"refusing to post an agent-claimable child under {parent}, which is not "
                f"agent-claimable. A held parent is the operator's work or work nobody "
                f"classified, and a child that let itself out of that hold would launder the "
                f"whole subtree into the fleet queue one hop at a time. Post it without "
                f"`--for-agents` and it inherits the hold, or raise {parent} first -- which "
                f"only the operator can do.")

    _check_fleet_workdir(claimable, workdir)

    # A producer may raise a flag on a child because it learned something worse. It may never
    # lower one. An explicit attempt to clear an inherited flag is kept, refused out loud, and
    # written onto both threads, because a silent refusal teaches nobody.
    lowered = [f for f, raw, inherited, own in
               (("external", external, p_ext, own_ext),
                ("canon_touching", canon_touching, p_canon, own_canon))
               if inherited and not own and str(raw or "").strip() != ""]

    row = ctx.one(
        """INSERT INTO brain.work_item
             (title, lane, host, state, priority, posted_by, workdir, depends_on, parent,
              external, canon_touching, max_attempts, stakes, reversibility, urgency,
              dependency_unblocking, effort, confidence, charter_alignment,
              canonical_task, produced_by, produced_by_ref, resolution_status, actor_type,
              agent_claimable)
           VALUES (%(title)s, %(lane)s, %(host)s, 'inbox', %(priority)s, %(posted_by)s,
                   %(workdir)s, %(depends_on)s, %(parent)s, %(external)s, %(canon_touching)s,
                   %(max_attempts)s, %(stakes)s, %(reversibility)s, %(urgency)s,
                   %(dependency_unblocking)s, %(effort)s, %(confidence)s,
                   %(charter_alignment)s, %(canonical_task)s, %(produced_by)s,
                   %(produced_by_ref)s, %(resolution_status)s, %(actor_type)s,
                   %(agent_claimable)s)
           RETURNING id""",
        {"title": title, "lane": lane, "host": host or "", "priority": int(priority),
         "posted_by": posted_by, "workdir": workdir or "", "depends_on": depends_on or "",
         "parent": parent or None, "external": ext, "canon_touching": canon,
         "max_attempts": int(max_attempts),
         "canonical_task": canonical_task, "produced_by": produced_by,
         "produced_by_ref": produced_by_ref, "resolution_status": resolution_status,
         "actor_type": actor_type, "agent_claimable": claimable,
         **{f: (declared[f] or None) for f in LEVELLED_SIGNALS}})
    tid = row["id"]

    # THE POSTED BODY, INTO A COLUMN THAT NOTHING ELSE MAY WRITE. Migration 14, task 0138.
    #
    # `result` used to be the table's only free-text column, and it held two different facts by
    # turns: this verb wrote the work order there and `done`, `fail`, `block` and `cancel` each
    # replaced it with the report. The second write did not amend the first, it ERASED it, and no
    # other row held a copy -- the thread's `post` event stores the title, never the body. Since
    # `bin/swarm-run` builds a terminal's whole prompt from `swarm show "$TASK_ID"`, `reopen` then
    # dispatched attempt 2 against attempt 1's SUMMARY: the task was re-worked from the text it
    # had been rejected FOR, and the instruction it was rejected for missing was gone. `fail` did
    # the same with no operator in the loop, and the runner reconciles EVERY unreported run to
    # `fail`. Measured on 2026-08-16; the probe is in `migrations/0014_work_item_brief.sql`.
    #
    # THE TRANSITION IS OVER, task 0169. Migration 14 landed with `post` writing the body into
    # BOTH columns, because the surfaces that read the work order out of `result` had not moved
    # yet and dropping the copy that day would have blanked their brief panels. They have moved:
    # `web/model.py`'s `review()`, `web/templates/detail_task.html` and `swarm show` all read
    # `brief` now, and `web/actions.py`'s scope flow no longer duplicates the definition of done
    # onto the thread to outlive `done`. So the `result` column is gone from the INSERT above and
    # a fresh task's `result` is '' until something actually HAPPENS to it. Two facts, two
    # addresses, one writer each: `brief` is the work order and never changes, `result` is the
    # latest report and starts empty. A verb that writes the body back into `result` is
    # reintroducing the defect, not restoring a convenience.
    #
    # WHY A SECOND STATEMENT AND NOT A COLUMN IN THE INSERT ABOVE, which is what this started as:
    # the INSERT's column list and its placeholder list are kept in step by hand, so adding one
    # column means editing two lines that must agree. Three terminals were editing this file
    # concurrently on 2026-08-16 and a partial overwrite landed exactly that way -- the column
    # list gained `brief` while the placeholder list lost the edit, giving "INSERT has more target
    # columns than expressions" and failing EVERY post fleet-wide until it was spotted. Same
    # transaction, so atomicity is unchanged; but the worst case of losing this hunk is one empty
    # brief, not a store that cannot accept work. Prefer the failure mode that degrades.
    #
    # The write-once trigger permits '' -> text, which is what makes this legal: the row is one
    # microsecond old and its brief is still the column default.
    ctx.execute("UPDATE brain.work_item SET brief = %s WHERE id = %s", (body or "", tid))

    # THE PROJECT, and it is a second statement for the reason spelled out immediately above
    # rather than a fifth column on an INSERT whose two hand-kept lists once cost every post on
    # the fleet. Task 0430, over migration 44's `brain.work_item.project` foreign key.
    #
    # ONLY WHEN ASKED FOR. `project` is nullable and 0 of 345 live rows carry one, so the common
    # post must not pay a statement for it -- and, more to the point, must not be able to fail on
    # one. A bad slug raises ForeignKeyViolation and rolls the whole post back, which is the right
    # answer when the caller named a project (posting work into a project that does not exist is
    # how a hold silently covers nothing) and would be an absurd one for every task that named no
    # project at all.
    if project:
        ctx.execute("UPDATE brain.work_item SET project = %s WHERE id = %s", (project, tid))

    ctx.actor = posted_by
    ctx.thread(tid, "post", title)

    # THE IMPACT, and it is a second statement for the same two reasons `brief` and `project` are.
    # Migration 48, row 0434, his ask 4.
    #
    # ONLY WHEN ASKED FOR, and ASKED FOR BEFORE IT IS WRITTEN. `docs/SCHEMA-TOLERANCE.md` rule 5,
    # and `store/schema.py`'s probe rather than a `try` for the reason that module opens with:
    # Postgres aborts the whole transaction on a failed statement, so a `post` that discovered the
    # missing column by hitting it could not then go on to post anything at all. A store below
    # ledger 48 keeps posting exactly as it did.
    #
    # A DROPPED SIGNAL SAYS SO ON THE THREAD. The row still lands, because refusing the whole post
    # over a tuning signal would be worse than the gap it reports. But it lands with a note naming
    # what was typed and what has to happen for it to take effect: a signal that vanished without
    # a word is how a queue comes to be ordered by something nobody set, which is the same shape
    # as the weights that never reached `rank` and went a fortnight unnoticed.
    if declared.get("impact"):
        if schema.has_column("work_item", "impact", ctx=ctx):
            ctx.execute("UPDATE brain.work_item SET impact = %s WHERE id = %s",
                        (declared["impact"], tid))
        else:
            ctx.thread(tid, "note",
                       f"impact={declared['impact']!r} was posted and NOT stored: this store has "
                       f"no brain.work_item.impact column, so it is below ledger 48. The row is "
                       f"ranked on its stakes until migrations/0048_impact_is_continuous.sql is "
                       f"applied, after which `swarm set {tid} impact {declared['impact']}` "
                       f"records it.")

    for flag in lowered:
        msg = (f"{tid} tried to clear the inherited hard flag {flag}, which stays true. "
               f"A derived task inherits {flag} from {parent} by OR and can only raise it.")
        ctx.thread(tid, "note", msg)
        ctx.thread(parent, "note", msg)

    gates = [f for f, v in (("external", ext), ("canon_touching", canon)) if v]
    if gates:
        src = f", inherited from {parent}" if parent and (p_ext or p_canon) else ""
        ctx.thread(tid, "note",
                   f"GATE {' + '.join(gates)}{src}: this task surfaces to the operator "
                   f"whatever its other signals say")

    # Read back rather than echoed: migration 26's trigger coerces a human-actor row to held,
    # so the value this returns is the one the database kept and not the one the caller asked
    # for. A surface that printed the request would tell the operator his row was claimable.
    return {"id": tid, "parent": parent, "external": ext, "canon_touching": canon,
            "gated": bool(gates), "lowered": lowered,
            "agent_claimable": bool(ctx.scalar(
                "SELECT agent_claimable FROM brain.work_item WHERE id = %s", (tid,)))}


# ------------------------------------------------------------------ claim
#
# THE LOAD-BEARING TRANSITION. D00 contract rule 2: claiming is atomic and the first claimer
# wins. Under the file bus that was `os.rename`, which the kernel makes atomic. Here it is
# `SELECT ... FOR UPDATE SKIP LOCKED`, and the two properties that make it equivalent are:
#
#   FOR UPDATE   a claimer holds a row lock for the rest of its transaction, so no second
#                claimer can take the same row.
#   SKIP LOCKED  a claimer that meets a locked row moves to the next candidate instead of
#                waiting, so twelve claimers do twelve claims rather than serialising into one
#                queue. Without it the property still holds and the throughput does not.
#
# The ordering and the lock are ONE statement on purpose. Ordering in Python and locking after
# would be acting on a snapshot, which is the shape of the bug this whole contract exists to
# prevent. Under READ COMMITTED, a row that changed between the snapshot and the lock is
# re-checked against the WHERE clause and skipped if it no longer qualifies, so a row another
# claimer just took can never be handed out twice.

@store.transition("claim")
def claim(ctx, *, agent, lanes, host="", weights=None, role=""):
    """Take the next claimable task, or return None. First claimer wins; the rest see None.

    A planner never claims. `lanes` is empty for a planner role and an empty lane list matches
    nothing, which is the same gate the file bus had. It is asserted here as well as in the
    runner because the consequence of one of the two being wrong is an admiral executing the
    work it just decomposed.
    """
    # `== 'true'`, not truthiness. The flag is stored as text, `resume` writes 'false' rather
    # than deleting the row (brain_runtime holds DELETE on nothing, by design), and every
    # non-empty string is truthy in Python -- so a truthiness test here would read a RESUMED
    # fleet as paused and the whole fleet would quietly stop claiming.
    if str(ctx.scalar("SELECT value FROM brain.runtime_flag WHERE key = 'fleet_paused'")
           or "").lower() == "true":
        return None
    if ctx.scalar("SELECT stopped_at FROM brain.agent WHERE name = %s", (agent,)):
        return None
    if not lanes:
        # A planner, or an agent configured to claim nothing. Not an error: it is the gate.
        return None

    w = weights or signal_weights()
    params = {"agent": agent, "host": host or "", "lanes": list(lanes),
              "any_lane": "*" in lanes, "liveness": CLAIM_LIVENESS_SECONDS,
              **{k: v for k, v in w.items() if k != "gate_low_confidence"}}

    # The lane ceiling and the project hold, in the same statement as the row lock. See
    # `_LANE_BUDGET_CTE` and `_PROJECT_BUDGET_CTE`. Every half is empty on a store that predates
    # the migration that arms it, which is a supported state and not an error.
    #
    # TWO PROBES, NOT ONE, and they are asked in this order because the second contains the first:
    # the lane arm needs migration 3 and the project arm needs migration 45. `budget_cte` is one
    # `WITH` with one or two members -- `_PROJECT_BUDGET_CTE` opens with a comma and is a
    # continuation, never a statement on its own, which `_project_scope_present` guarantees by
    # returning False whenever the lane half is absent.
    # THREE PROBES, NOT TWO, since migration 47. The third is `_project_archive_present`, and it is
    # asked only when the project arm is armed at all: `_PROJECT_ARCHIVED_ARM` is a UNION member
    # inside the CTE `_PROJECT_BUDGET_CTE` opens and `_PROJECT_BUDGET_CTE_CLOSE` closes, so on its
    # own it is a syntax error and cannot be spliced without them. An archived project is withheld
    # from the claim path exactly as a resting one is, which is decision D-05's load-bearing half:
    # without it an archived project keeps `state = 'in_progress'`, agents keep claiming its work,
    # and the operator keeps paying for projects he took off his board.
    armed = _budget_schema_present(ctx)
    project_armed = _project_scope_present(ctx)
    archive_armed = project_armed and _project_archive_present(ctx)
    budget_cte = (_LANE_BUDGET_CTE if armed else "") + (
        (_PROJECT_BUDGET_CTE + (_PROJECT_ARCHIVED_ARM if archive_armed else "")
         + _PROJECT_BUDGET_CTE_CLOSE) if project_armed else "")
    budget_gate = (_LANE_NOT_BUDGET_STOPPED if armed else "") + (
        _PROJECT_NOT_BUDGET_STOPPED if project_armed else "")

    # THE ACTOR GATE, and unlike the lane ceiling its absence is a refusal rather than a shrug.
    if not _actor_gate_present(ctx):
        raise VerbError(
            "refusing to hand out any task: this store has no brain.work_item.agent_claimable "
            "column, so `claim` cannot tell the operator's own work from the fleet's. That "
            "column is migration 26 and it exists because this table holds both queues -- on "
            "2026-08-18 fifteen of the twenty-four claimable rows on live `brain` were the "
            "operator's, among them a credential rotation and two client sends. Apply "
            "migrations/0026_work_item_agent_claimable.sql, then claim again. Refusing is the "
            "fail-closed direction and it is deliberate: an unclassified queue is not an empty "
            "one.", code=7)

    row = ctx.one(
        f"""{budget_cte}SELECT w.id
              FROM brain.work_item w
             WHERE w.state = 'inbox'
               AND (%(any_lane)s OR w.lane = ANY(%(lanes)s))
               AND (w.host = '' OR w.host = %(host)s)
               AND {_DEPS_MET}{budget_gate}{_NOT_HELD_BY_A_LIVE_AGENT}{_AGENT_CLAIMABLE}
             ORDER BY w.priority ASC, {_score_sql()} DESC, w.id ASC
             FOR UPDATE SKIP LOCKED
             LIMIT 1""", params)
    if not row:
        return None

    task = ctx.one(
        """UPDATE brain.work_item
              SET state = 'active', claimed_by = %s, claimed_at = now(), attempts = attempts + 1
            WHERE id = %s AND state = 'inbox'
        RETURNING *""", (agent, row["id"]))
    if not task:
        # Belt and braces. The row lock above makes this unreachable; if it ever fires, the
        # claim protocol is broken and returning None is the safe answer.
        return None

    ctx.actor = agent
    ctx.thread(task["id"], "claim", f"attempt {task['attempts']}")
    ctx.execute(
        """INSERT INTO brain.agent (name, role, status, work_item_id, host, updated)
           VALUES (%s, %s, 'working', %s, %s, now())
           ON CONFLICT (name) DO UPDATE
             SET status = 'working', work_item_id = EXCLUDED.work_item_id, updated = now()""",
        (agent, role or "terminal", task["id"], host or ""))

    # Bury the corpse we just stepped over. The gate above only let this claim through because
    # every other agent pointing at this task had been silent past `CLAIM_LIVENESS_SECONDS`, so
    # by the time this line runs those rows are dead BY MEASUREMENT rather than by assumption --
    # it is the same predicate `reap` acts on, applied to the rows the claim has already tested.
    #
    # It exists because nothing else would ever clear them. `reap` only looks at tasks in
    # `active`, and the state this fixes is a task that spent time in `inbox` with a stale agent
    # row still on it, which reap cannot see. Left alone the row survives the handover and
    # `doctor` reports two agents on one task forever, so the one surface that would have caught
    # a REAL double claim cries wolf until nobody reads it.
    #
    # `name <> %s` and the staleness bound are both load-bearing: without them a claim would
    # cancel a live agent's row, which is the incident inverted.
    ctx.execute(
        """UPDATE brain.agent SET status = 'dead', work_item_id = NULL
            WHERE work_item_id = %s AND name <> %s AND status = 'working'
              AND updated <= now() - make_interval(secs => %s)""",
        (task["id"], agent, CLAIM_LIVENESS_SECONDS))

    flags = _flags(ctx, task["id"])
    gates = gate_reasons(bool(flags["external"]), bool(flags["canon_touching"]),
                         task.get("confidence"), w)
    if gates:
        # The gate does not stop the work, it guarantees the operator sees it. Putting it on the
        # thread means the trail says so even if nobody was watching the feed.
        ctx.thread(task["id"], "note",
                   f"GATE {' + '.join(gates)}: claimed work that surfaces to the operator "
                   f"whatever its other signals say")

    task["external"] = bool(flags["external"])
    task["canon_touching"] = bool(flags["canon_touching"])
    task["gated"] = bool(gates)
    task["gate_reasons"] = gates
    return task


# ------------------------------------------------------------------ the finishers

def _finish(ctx, task_id, state, text, who, kind, force=False):
    task = _hold(ctx, task_id, who, force=force)
    # THE FULL TEXT, UNCUT. D00 rule 11 and 0037's semantics: `result` was cut at 2000 chars by
    # the file bus with no marker, so it read as a finished sentence and the original was gone.
    # A `text` column has no reason to cut anything. Renderings truncate; storage does not.
    ctx.execute(
        "UPDATE brain.work_item SET result = %s, finished_at = now(), state = %s WHERE id = %s",
        (text, state, task_id))
    ctx.actor = who or task.get("claimed_by") or "?"
    ctx.thread(task_id, kind, text)
    ctx.execute("UPDATE brain.run SET ended_at = now(), outcome = %s "
                "WHERE work_item_id = %s AND attempt = %s AND ended_at IS NULL",
                (kind, task_id, task["attempts"]))
    return task


@store.transition("done")
def done(ctx, *, id, summary, agent="", force=False):
    """The agent reported it finished. Acceptance is a SEPARATE act (D00 rule 3).

    `done` never sets `accepted_at`. `reopen` is the rejection verb, and the auto-accept
    narrowing in accept.py ships disabled and only records what it would have decided.

    Only the holder may report. `done` by a non-holder does not merely end another agent's work,
    it FILES THAT AGENT'S REPORT: `_finish` stamps the thread with `claimed_by` when the caller is
    anonymous, so the forged summary reads as the holder's own. Measured in the adversarial pass:
    T3 closed T1's 0022 and the thread said `actor='T3'` over T1's task.
    """
    _finish(ctx, id, "done", summary, agent, "done", force=force)
    # The auto-accept measurement, in this transaction rather than in a caller. Every surface
    # that reports a task done contributes to the week of data; none can skip it by accident.
    from .accept import measure_in_transaction
    verdict = measure_in_transaction(ctx, id)
    n = ctx.scalar("SELECT count(*) FROM brain.artifact WHERE work_item_id = %s", (id,))
    return {"id": id, "state": "done", "artifacts": n, "auto_accept": verdict}


# --------------------------------------------------------------------- the unspent refund
#
# `block --unspent` refunds the attempt the CLAIM charged, and the whole design turns on where
# that charge happens. `claim` does `attempts = attempts + 1` (see the UPDATE above) at the
# moment a row is handed out, BEFORE the run has done anything. So a run stopped for spend does
# not get charged an attempt by `block` -- `_finish` never touches the column. It is charged by
# the claim, and `block` simply LEAVES IT CHARGED. The bug is an omission, not an act.
#
# WHAT THAT COST, measured on live `brain` 2026-08-29 (task 0461). Nine rows sat `blocked` at
# attempt 1 of 2 at once; eight of them carried a `hard_stop` incident naming the row itself
# (0408 0430 0431 0437 0440 0442 0443 0444, incidents 229-245), and the ninth (0407) was a
# genuine operator-question block. `max_attempts` defaults to 2, so two ceiling crossings
# exhaust a healthy row's entire allowance having never once genuinely tried it. The operator
# requeued all nine BY HAND that same hour, which is the cost stated as an action.
#
# THE SYSTEM ALREADY KNEW. `budget/halt.py:84` declares
# `CHARGES_ATTEMPT = {BUDGET_STOP: False, ...}` and `Halt.line()` prints it into the reason this
# verb stores. 0431's stored `result` on live `brain` read, verbatim:
#
#     ... -> block (self-clearing=False, charges attempt=False, incident 237)
#
# while `brain.work_item.attempts` for 0431 read 1. The field was declared, formatted into the
# operator's own record, and consumed by nothing. This is the code that consumes it.
#
# WHY NOT THE OTHER TWO VERBS, which is the question this file has answered twice before and
# must keep answering the same way:
#
#   * NOT `fail`. That is the ladder, and a lane that did nothing wrong must not climb it.
#   * NOT `reopen`. That resets attempts to 0 AND returns the row to `inbox`, so the next free
#     terminal claims it and spends again with no counter left to stop the loop. The money
#     retry loop is the failure `test-budget-wiring.sh` exists to catch, and it stays caught.
#
# The refund is safe precisely BECAUSE the row stays `blocked`. Parked is what makes a refund
# not a loop: a blocked row cannot be re-claimed until a human raises the ceiling or types
# `budget resume`, so refunds can never outnumber claims, and `GREATEST(attempts - 1, 0)` means
# the column cannot go negative even if a caller asks twice.
#
# THE REFUND IS GATED ON FIRST-PARTY EVIDENCE, not on the caller's word. Without that gate any
# agent could type `swarm block <own task> --unspent` and erase its own ladder, which converts
# the retry limit into an opt-out. The evidence is the same record `budget halt` requires before
# it will call a stop at all: a `hard_stop` or `manual_stop` row in `brain.budget_incident`
# naming THIS work item, filed at or after this claim. No evidence, no refund -- and the block
# still happens, loudly annotated, because leaving a stopped run `active` would be worse than
# leaving an attempt charged.
_UNSPENT_EVIDENCE = """
    SELECT bi.id FROM brain.budget_incident bi, brain.work_item w
     WHERE w.id = %s AND bi.work_item_id = w.id
       AND bi.kind IN ('hard_stop', 'manual_stop')
       AND (w.claimed_at IS NULL OR bi.occurred_at >= w.claimed_at)
     ORDER BY bi.id DESC LIMIT 1"""


def _refund_attempt(ctx, task_id: str, before: int) -> dict:
    """Give back the attempt the claim charged, if a budget stop is on the record. Never silent.

    Returns what happened so the caller can print it. Every branch writes the thread, because an
    attempt that moved -- or pointedly did not -- with no trail is how this column stopped
    meaning what it says in the first place.
    """
    # The brake is not installed on this store, so no budget stop can have produced this call.
    # Refusing is the honest answer: a refund handed out where a ceiling cannot exist is a
    # refund handed out on nothing at all.
    #
    # `to_regclass` ON THE TABLE THIS FUNCTION ACTUALLY READS, not `_budget_schema_present`.
    # That probe tests `budget_state` and `budget_open_stop`; the evidence query below names
    # `budget_incident`. All three arrive in migration 3 today, so the two probes agree today --
    # and a probe that agrees by coincidence is one migration away from naming a relation it
    # never tested. `to_regclass` returns NULL rather than raising, so this is one catalog
    # lookup and never an aborted transaction.
    if not ctx.scalar("SELECT to_regclass('brain.budget_incident') IS NOT NULL"):
        ctx.thread(task_id, "note",
                   "REFUND REFUSED: this block asked for `--unspent` but the store has no "
                   "brain.budget_incident (budget/schema/0003_budget.sql is not applied), so no "
                   "budget stop can be on the record here. The attempt stays charged.")
        return {"refunded": False, "why": "no budget schema", "attempts": before}

    row = ctx.one(_UNSPENT_EVIDENCE, (task_id,))
    if not row:
        ctx.thread(task_id, "note",
                   f"REFUND REFUSED: this block asked for `--unspent` and there is no hard_stop "
                   f"or manual_stop incident naming {task_id} since it was claimed. The attempt "
                   f"stays charged at {before}. A refund is evidence-gated on purpose: without "
                   f"the gate, `--unspent` is an opt-out from the retry limit.")
        return {"refunded": False, "why": "no first-party budget incident", "attempts": before}

    after = ctx.scalar(
        """UPDATE brain.work_item SET attempts = GREATEST(attempts - 1, 0)
            WHERE id = %s RETURNING attempts""", (task_id,))
    ctx.thread(task_id, "note",
               f"attempt REFUNDED {before} -> {after} of this row's allowance: budget incident "
               f"{row['id']} says this run was stopped for spend, not judged. The row stays "
               f"BLOCKED and is not requeued -- a human raises the ceiling or types `budget "
               f"resume` -- so the refund cannot become a retry loop.")
    return {"refunded": True, "incident": row["id"], "attempts": after, "was": before}


@store.transition("block")
def block(ctx, *, id, reason, agent="", blocked_on="", unspent=False, force=False):
    """Park a task. Only the holder may park live work.

    The runner's budget-stop branch reaches this verb, and it reached it guarded only by "the
    task state is active" -- so a runner whose task had been reaped and re-claimed underneath it
    parked the NEW holder's run for a ceiling the new holder never crossed.

    `unspent=True` additionally refunds the attempt the claim charged. Read `_refund_attempt`
    above for why that is a separate flag, why it is gated on first-party evidence, and why
    giving the attempt back is safe here and would not be safe from `reopen`.
    """
    task = _finish(ctx, id, "blocked", reason, agent, "block", force=force)
    if blocked_on:
        ctx.execute("UPDATE brain.work_item SET blocked_on = %s WHERE id = %s", (blocked_on, id))
    out = {"id": id, "state": "blocked", "attempts": task["attempts"],
           "max_attempts": task["max_attempts"]}
    if unspent:
        # AFTER `_finish`, and the order is load-bearing. `_finish` closes the run row with
        # `WHERE attempt = %s` keyed on the attempt number the claim assigned; refund first and
        # that UPDATE matches nothing, so the run is left open forever and the budget stop
        # disappears from `brain.run`.
        out.update(_refund_attempt(ctx, id, task["attempts"]))
    return out


@store.transition("fail")
def fail(ctx, *, id, reason, agent="", force=False):
    """Requeue while attempts remain, block and raise an operator question when they do not.

    Only the holder may fail a task. `bin/swarm-run`'s reconciliation calls this on every
    unreported run, and it called it guarded only by `state = active`: T3's runner requeued
    T1's live 0019 in the adversarial measurement, which is the incident by a second door.
    """
    task = _hold(ctx, id, agent, force=force)
    attempts, cap = task["attempts"], task["max_attempts"]
    ctx.actor = agent or task.get("claimed_by") or "?"
    if attempts < cap:
        ctx.execute(
            """UPDATE brain.work_item
                  SET state = 'inbox', claimed_by = '', claimed_at = NULL, result = %s
                WHERE id = %s""", (reason, id))
        ctx.thread(id, "fail", f"attempt {attempts}/{cap}: {reason}")
        ctx.execute("UPDATE brain.run SET ended_at = now(), outcome = 'fail' "
                    "WHERE work_item_id = %s AND attempt = %s AND ended_at IS NULL",
                    (id, attempts))
        return {"id": id, "state": "inbox", "requeued": True,
                "attempts": attempts, "max_attempts": cap}

    qid = _raise_question(
        ctx, frm=ctx.actor, task_id=id,
        text=(f"{id} failed {attempts} times and is blocked. Last reason: {reason}"),
        default="the task stays blocked until you answer")
    ctx.execute(
        "UPDATE brain.work_item SET state = 'blocked', result = %s, finished_at = now(), "
        "blocked_on = %s WHERE id = %s", (reason, qid, id))
    ctx.thread(id, "fail", f"attempt {attempts}/{cap}, out of attempts: {reason}")
    ctx.execute("UPDATE brain.run SET ended_at = now(), outcome = 'fail' "
                "WHERE work_item_id = %s AND attempt = %s AND ended_at IS NULL", (id, attempts))
    return {"id": id, "state": "blocked", "requeued": False, "question": qid,
            "attempts": attempts, "max_attempts": cap}


@store.transition("reopen")
def reopen(ctx, *, id, reason, agent="", force=False):
    """Send a finished task back to the queue, unspent.

    This is the rejection verb for `done` (D00 rule 3) AND the verb the runner calls on a
    subscription refusal (D00 rule 6). It resets `attempts` to 0 on purpose: a wall costs
    wall-clock, never work, and a refused task must not carry a spent attempt into its next run.

    That reset is also why the refusal PATTERN must not be loose enough to match an ordinary
    success. A false positive here requeues a genuinely failed task forever, because there is no
    attempt ladder left to stop it. `test_ratelimit` tests the negative cases explicitly.

    ON A LIVE TASK, ONLY THE HOLDER. `reopen` is the most dangerous of the finishers to get wrong,
    because it clears the claim AND resets `attempts` to 0: a non-holder reopening live work both
    interrupts it and erases the ladder that would have stopped it looping. Measured: T3 reopened
    T1's 0020 and its attempts went 1 -> 0. On a task nobody holds -- the ordinary case, rejecting
    finished work -- `_hold` passes it straight through and this stays the operator's verb.
    """
    task = _hold(ctx, id, agent, force=force)
    ctx.execute(
        """UPDATE brain.work_item
              SET state = 'inbox', claimed_by = '', claimed_at = NULL, finished_at = NULL,
                  attempts = 0, blocked_on = ''
            WHERE id = %s""", (id,))
    ctx.actor = agent or "operator"
    ctx.thread(id, "reopen", reason)
    return {"id": id, "state": "inbox", "was": task["state"]}


@store.transition("unaccept work")
def unaccept_work(ctx, *, id, reason, by="", as_operator=True):
    """Withdraw an acceptance. The inverse of `accept work`, and a record rather than a revert.

    Bus row `0413`, the operator's own words on 2026-08-28 after driving the product end to end
    for the first time: *"Maybe you have an option to like unaccept or like send back or undo the
    acceptance would be good."* `web/MUST-NOT-BUILD.md` item 10 already required an inverse verb
    wherever a real one exists, and acceptance had none. His approval to overrule item 10 was
    recorded UNSPENT, because item 10 does not forbid undo, it forbids a lie about undo; the
    ruling is `2026-08-28-row-0421-two-prohibitions-overruled.md` section B and it says to
    deliver the ask by building this verb.

    ----------------------------------------------------------------------------------------
    IT IS NOT `reopen`, AND THE DIFFERENCE IS THE WHOLE REASON IT IS A SECOND VERB.
    ----------------------------------------------------------------------------------------
    `reopen` sends the WORK back: state to `inbox`, claim cleared, `attempts` reset to 0, and an
    agent picks it up again. This withdraws the DECISION and touches nothing else. The row stays
    `done`, `result` still holds the agent's own report, and the item reappears in
    `brain.queue_open` arm 1 exactly where it sat before the acceptance, with `Accept work` on it
    again. That is what an operator who clicked the wrong card needs and what `reopen` cannot do
    without costing an agent a second run of finished work.

    ----------------------------------------------------------------------------------------
    THE ATTRIBUTION IS THE DATABASE'S, NOT THE CALLER'S. Same gate as `accept work`.
    ----------------------------------------------------------------------------------------
    `brain.current_human()` reads `session_user`, is fixed at authentication and is unreachable
    from SQL. `as_operator=True` is what makes `store.apply` open a human login; a process
    without one gets `StoreConfigError` from `_connect`, and one that somehow connected as
    `brain_runtime` anyway reads NULL here and is refused. Withdrawing a decision of record is
    itself a decision of record, so it gets the acceptance's identity rule and not the weaker one
    the fleet verbs use.

    `by` is OPTIONAL here where `accept work` requires it, and the difference is deliberate
    rather than sloppy. `accept work` refuses an empty `by` because it predates the login gate
    and `accepted_by` was once the only record of who decided. The trail entry here is written
    from `brain.current_human()` whatever the caller passes, so requiring the caller to guess
    that name first would add a chance to be wrong and no chance to be right. A `by` that IS
    passed and disagrees is refused rather than silently overwritten, for the reason `accept
    work` gives: under row 0386 decision 2 there are up to twelve human logins, so recording a
    colleague's act is the same forgery one seat over.

    A COLLEAGUE'S ACCEPTANCE MAY BE WITHDRAWN and the thread text says whose. It is a real act a
    second seat may need to take, it is signed by the withdrawer's own name, and refusing it
    would make an acceptance unwithdrawable the day its acceptor's login is retired. What is
    refused is doing it under the acceptor's name.

    ----------------------------------------------------------------------------------------
    THE TRAIL IS WRITTEN BEFORE THE ERASURE, AND THAT ORDER IS LOAD BEARING.
    ----------------------------------------------------------------------------------------
    Migration 22 refuses to clear these two columns at all. Migration 43 gives that refusal one
    exemption: a thread row of kind `unaccept`, dated after the acceptance, signed by
    `brain.current_human()`. The trigger is BEFORE UPDATE and a transaction sees its own writes,
    so the record has to exist before the UPDATE that depends on it. Written the other way round
    this verb refuses itself, which is a failure mode worth knowing about rather than a bug to
    discover.

    ----------------------------------------------------------------------------------------
    A REASON IS REQUIRED, on the same argument `stop` and `queue bump` make.
    ----------------------------------------------------------------------------------------
    `reopen` carries one because a rejection with no reason teaches nobody. `stop` refuses one
    outright because a stop nobody signed reads on the board as an agent that died. Here the
    ambiguity is sharper: an acceptance withdrawn with no reason is indistinguishable, at the
    row, from an acceptance that never happened, and the two mean opposite things about whether
    a human read the work. The reason is also the only place the record can say WHICH kind of
    withdrawal this was: a misclick on a card, or a human who read the report again and changed
    his mind. The first is a defect report about the console, the second is the disagreement
    signal `accept.disagreement_report()` exists to measure. A verb that cannot tell them apart
    turns that dataset into noise, and D00's named failure is the confidently wrong number.

    WHAT THIS VERB DOES NOT REACH, said rather than left to be found. The auto-accept
    measurement in `engine/swarm_engine/accept.py` pairs each recorded verdict with the FIRST
    `accept`, `reopen` or `cancel` after it (`DECIDING_KINDS`). `unaccept` is not in that tuple,
    so an acceptance that was later withdrawn still scores as `accepted` in this week's
    disagreement rate, and a human withdrawing the RULE's own acceptance is invisible to it. That
    is a real gap in the measurement, it is one word plus a pairing rule in that module, and that
    module is outside this task's file scope. It is filed here so the next reader of either file
    finds it.
    """
    reason = " ".join((reason or "").split())
    if not reason:
        raise VerbError(
            f"refusing to withdraw the acceptance of {id} with no reason. At the row, an "
            f"acceptance that was withdrawn and one that never happened are the same absence, "
            f"and they mean opposite things about whether a human read the work. The reason is "
            f"also what separates a misclick from a changed mind, which are a console defect and "
            f"a disagreement signal respectively. Pass --reason.", code=7)
    row = ctx.one("SELECT state, accepted_at, accepted_by FROM brain.work_item WHERE id = %s",
                  (id,))
    if not row:
        raise VerbError(f"no such task: {id}")
    was_by = (row["accepted_by"] or "").strip()
    if row["accepted_at"] is None and not was_by:
        # NOT a silent no-op. `undo` on a card whose acceptance is already gone must say so, or
        # the console has a control that reports success for having changed nothing, which is the
        # N4 defect this row was filed against wearing the opposite mask.
        raise VerbError(
            f"{id} is not accepted, so there is no acceptance to withdraw. `swarm show {id}` "
            f"prints the thread if you are looking for who accepted it and when; if the work "
            f"itself should go back to the queue, that is `reopen`.", code=6)

    the_human = ctx.scalar("SELECT brain.current_human()")
    if not the_human:
        raise VerbError(
            f"refusing to withdraw the acceptance of {id} from a connection the database does "
            f"not know as a human. Withdrawing a decision of record is a decision of record, so "
            f"it is gated exactly as `accept work` is: `brain.current_human()` returned NULL for "
            f"this session, which is what it returns for brain_runtime, the login every agent "
            f"surface connects as. Call this as a human (`as_operator=True`, which opens a human "
            f"login), or from the operator's own shell or console.", code=6)
    named = (by or "").strip()
    if named and named != the_human:
        raise VerbError(
            f"this call says the withdrawal is by {named!r} and the database says the connection "
            f"is {the_human!r}. Refusing rather than recording either one. A withdrawal may be "
            f"filed against a COLLEAGUE's acceptance, which is why the acceptor is not checked "
            f"here, but it is always signed by the human who filed it.", code=6)

    ctx.actor = the_human
    whose = "their own acceptance" if was_by == the_human else f"the acceptance by {was_by}"
    # THE RECORD FIRST, THE ERASURE SECOND. Migration 43's exemption reads this row; see the
    # docstring. `ctx.thread` signs it with `ctx.actor`, which is the database's answer above and
    # never the caller's string, and the trigger re-checks that signature rather than trusting it.
    ctx.thread(id, "unaccept",
               f"{the_human} withdrew {whose} (recorded {row['accepted_at']}): {reason}")
    ctx.execute("UPDATE brain.work_item SET accepted_at = NULL, accepted_by = NULL WHERE id = %s",
                (id,))
    return {"id": id, "state": row["state"], "unaccepted_by": the_human,
            "was_accepted_by": was_by, "was_accepted_at": row["accepted_at"], "reason": reason}


@store.transition("cancel")
def cancel(ctx, *, id, reason="cancelled by operator", agent="operator", force=False):
    """Close a task without it being done. Cancelling live work is `--force`, on purpose.

    The operator may of course cancel a running task, and that is not what the predicate refuses:
    it refuses cancelling it SILENTLY. `--force` costs one word and puts the displaced agent's
    name on the thread, which is the difference between a decision and a task that vanished
    under a working agent.

    IT ALSO CLOSES THE QUESTIONS THE TASK LEFT IN FRONT OF A HUMAN (task 0159). Cancelling used
    to end the task and leave its open questions standing in `swarm questions` and on the
    operator's card, and the cost was measured rather than predicted: task 0139 was cancelled at
    12:10Z on 2026-08-18 and at 16:47Z the operator answered q0140, its question, about a release
    branch that does not exist, on a task that no longer existed. Nothing could have cleared it
    but him, because `answer` was the only verb that closed a question and it writes `operator`.

    The cascade is `withdraw`, not `answer`: the questions are retired, signed by whoever
    cancelled, and `answer` stays NULL on every one of them. An ALREADY-ANSWERED question is left
    exactly as it is -- a decision the operator made is not something a cancel gets to erase.
    """
    _finish(ctx, id, "cancelled", reason, agent, "cancel", force=force)
    who = _caller(agent) or agent or "operator"
    questions, cascaded = _withdraw_open_questions(
        ctx, id, by=who, reason=f"{id} was cancelled: {reason}")
    if questions and cascaded:
        ctx.actor = who
        ctx.thread(id, "note",
                   f"{len(questions)} open question(s) withdrawn with the cancel: "
                   f"{', '.join(questions)}. They were NOT answered -- nobody decided them, and "
                   f"they are out of the operator's queue because the task they belong to is "
                   f"gone. `swarm questions --withdrawn` shows them.")
    elif questions:
        # THE CANCEL STILL HAPPENED AND THE QUESTIONS ARE STILL STANDING. Said on the thread
        # rather than swallowed, because this is precisely the state task 0159 exists to end and
        # a silent skip would leave it looking closed. The store is below ledger 31, so nothing in
        # it can record a withdrawal and only the operator's own hand can clear these.
        ctx.actor = who
        ctx.thread(id, "note",
                   f"{len(questions)} open question(s) are STILL IN THE OPERATOR'S QUEUE after "
                   f"this cancel: {', '.join(questions)}. This store is below migration 31 "
                   f"(migrations/0031_question_withdrawal.sql) and cannot record a withdrawal, so "
                   f"the cascade could not run. Until it is applied they can only be cleared by "
                   f"the operator answering them, which is the cost this task measured.")
    return {"id": id, "state": "cancelled",
            "withdrew": questions if cascaded else [],
            "left_standing": [] if cascaded else questions}


@store.transition("release")
def release(ctx, *, id, agent, reason="released"):
    """Put a task back ONLY if this agent still holds it.

    The commander's 2026-08-16 incident, second finding: killing a runner fired its trap, which
    released a task the bus had already handed to a different agent. The trap acted on what the
    runner thought it held rather than on what the bus says it holds, so a dying agent could
    unclaim a live one's work.

    `WHERE claimed_by = %(agent)s` is the whole fix, and it belongs in the transition rather
    than in the runner because every surface that releases needs it, not just the one that had
    the bug.
    """
    row = ctx.one(
        """UPDATE brain.work_item
              SET state = 'inbox', claimed_by = '', claimed_at = NULL
            WHERE id = %s AND state = 'active' AND claimed_by = %s
        RETURNING id""", (id, agent))
    if not row:
        owner = ctx.scalar("SELECT claimed_by FROM brain.work_item WHERE id = %s", (id,))
        return {"id": id, "released": False, "owner": owner}
    ctx.actor = agent
    ctx.thread(id, "note", f"released by {agent}: {reason}")
    return {"id": id, "released": True}


# ------------------------------------------------------------------ questions

def _raise_question(ctx, *, frm, task_id, text, default="") -> str:
    row = ctx.one(
        """INSERT INTO brain.question (asked_by, work_item_id, text, default_if_unanswered)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (frm, task_id or None, text, default or ""))
    return row["id"]


@store.transition("ask")
def ask(ctx, *, question, agent, task=None, default):
    """Raise an operator question. Blocks the task, and `--default` is mandatory.

    Without a default the operator's silence stalls the task forever. With one, silence is a
    usable answer.
    """
    tid = norm_task_id(task) if task else ""
    if tid:
        _require(ctx, tid)
    qid = _raise_question(ctx, frm=agent, task_id=tid, text=question, default=default)
    if tid:
        ctx.execute(
            "UPDATE brain.work_item SET state = 'blocked', blocked_on = %s WHERE id = %s",
            (qid, tid))
        ctx.actor = agent
        ctx.thread(tid, "ask", f"{qid}: {question}")
    return {"id": qid, "task": tid, "default": default}


@store.transition("answer")
def answer(ctx, *, qid, text, amend=False, planners=(), requeue=False):
    """Answer, tell the planners, and requeue the blocked task.

    REQUEUE IS CONDITIONAL, and that condition is the commander's 2026-08-16 incident. The file
    bus requeued unconditionally: T3 asked a question, T3's engine kept running, the answer
    requeued the task, T1 legitimately claimed it, and two agents then executed the same brief
    against the same repo concurrently. The claim was atomic and still correct; the gap was that
    `ask` blocked the TASK and left the PROCESS alive.

    A transaction boundary cannot fix a process-lifecycle invariant, so this verb reports what
    it found and refuses to requeue behind a live claimer: if the asking agent is still
    heartbeating `working` on this task, the answer lands, the planners are told, and the task
    stays blocked with `requeue_refused` set. The runner fences the engine and then calls
    `answer --requeue` itself, which is the only surface that knows the process is gone.

    `requeue` DEFAULTS TO FALSE, and the default is the whole point. D4 shipped this guard as
    `if live and not requeue` over a signature that read `requeue=True`, so the guard existed
    only for callers who passed the keyword. The CLI passed it (`--requeue` is `store_true`) and
    the console passed it (`web/actions.py`); `queue default fire` passed `requeue=True`
    explicitly and every other library caller got the dangerous value by saying nothing. The
    adversarial pass reproduced it in one line: `store.apply("answer", qid=..., text=...)`
    requeued a task behind a live engine and two agents heartbeat `working` on it.

    Safe by omission, dangerous by name. `requeue=True` now means exactly one thing -- "I have
    fenced that process and I am asserting it is gone" -- and only a surface that can know that
    may say it. Saying it over a live claimer is still permitted, because the runner's fence is
    real, but it is no longer silent: it lands on the thread.
    """
    # `FOR UPDATE` BECAUSE THE OTHER CALLER IS A CLOCK. Task 0377, dispatch idempotency. This
    # read and the UPDATE below
    # are a check-then-act, and the refusal three lines down -- "already answered" -- was true only
    # of callers arriving one at a time. The caller that does not arrive politely is
    # `queue default fire`, which runs UNATTENDED at 07:00 and 19:00 and whose own refusal says
    # "a default fires into silence, not over an answer". Unlocked, the operator answering at a
    # checkpoint and the checkpoint firing both read `answer IS NULL`, both pass, and the last
    # writer wins -- so a machine-written default can land ON TOP of a human decision and the
    # record then says silence shipped it. That is the one thing the whole silence-window doctrine
    # promises cannot happen.
    q = ctx.one("SELECT * FROM brain.question WHERE id = %s FOR UPDATE", (qid,))
    if not q:
        raise VerbError(f"no such question: {qid}")
    if amend and q["answer"] is None:
        raise VerbError(f"{qid} has no answer to amend. Use `answer`, not `reanswer`.")
    if not amend and q["answer"] is not None:
        raise VerbError(f"{qid} is already answered. Use `reanswer` to correct it.")
    # A WITHDRAWN QUESTION IS NOT WAITING FOR THIS, and the refusal is the whole point of task
    # 0159. On 2026-08-18 at 16:47Z the operator answered q0140 -- a question raised by a test,
    # about a release branch that does not exist, on task 0139, which had been cancelled four and
    # a half hours earlier -- because it was still in his queue and answering was the only act
    # this store offered. Once `withdraw` has taken one out of the queue, an answer arriving from
    # a stale console tab, a scheduled default or a script must not put it back.
    #
    # `.get`, NOT `[...]`, and the four characters are the whole of doctrine rule 2. `q` came from
    # `SELECT *`, so against a store below ledger 31 the query SUCCEEDS and the dict simply has no
    # such key: bracket access raised `KeyError: 'withdrawn_at'` here on 2026-08-18 and took
    # `answer` -- the operator's one verb -- down for him and for every agent while eleven
    # questions sat in front of him. Absent column means no question there was ever withdrawn, so
    # `None` is the true value and the refusal below correctly does not fire.
    if q.get("withdrawn_at") is not None:
        raise VerbError(
            f"{qid} was withdrawn by {q['withdrawn_by']} "
            f"({q['withdrawn_reason'] or 'no reason recorded'}). Nothing is waiting on this "
            f"answer, so it is not recorded. `swarm questions --withdrawn` shows it with the "
            f"hand that withdrew it; reopen the task if the decision is live again.")

    if amend:
        ctx.execute(
            "UPDATE brain.question SET amended_from = answer, amended_at = now(), "
            "answer = %s, answered_at = now() WHERE id = %s", (text, qid))
    else:
        # `AND answer IS NULL` PLUS `RETURNING` is the second mechanism, and it survives a later
        # edit that drops the lock above. An amend is a DELIBERATE overwrite and keeps its
        # unconditional write; a first answer is not, and must never overwrite one.
        landed = ctx.execute(
            "UPDATE brain.question SET answer = %s, answered_at = now() "
            "WHERE id = %s AND answer IS NULL RETURNING id", (text, qid))
        if not landed:
            raise VerbError(
                f"{qid} was answered by someone else between this verb's read and its write, so "
                f"this answer was NOT recorded. Refusing rather than overwriting: if the other "
                f"writer was `queue default fire`, silence has already shipped a default here, "
                f"and if it was a person, this would have replaced their decision. Read the "
                f"answer and use `reanswer` if it should be corrected.")

    ctx.actor = "operator"
    kind = "reanswer" if amend else "answer"
    for p in planners:
        ctx.execute(
            "INSERT INTO brain.message (from_agent, to_agent, kind, work_item_id, text) "
            "VALUES ('operator', %s, %s, %s, %s)",
            (p, kind, q["work_item_id"], f"{qid} {kind}: {text}"))

    out = {"id": qid, "task": q["work_item_id"], "amended": amend, "requeued": False,
           "requeue_refused": None, "requeue_overrode": None, "told": list(planners)}
    tid = q["work_item_id"]
    if tid:
        ctx.thread(tid, "answer", f"{qid}: {text}")
        task = ctx.one("SELECT * FROM brain.work_item WHERE id = %s", (tid,))
        if task and task["state"] == "blocked":
            live = ctx.one(
                """SELECT name, updated FROM brain.agent
                    WHERE work_item_id = %s AND status = 'working'
                      AND updated > now() - interval '15 minutes'""", (tid,))
            if live and not requeue:
                out["requeue_refused"] = live["name"]
                ctx.thread(tid, "note",
                           f"{qid} answered, and {live['name']} is still heartbeating on this "
                           f"task. NOT requeued: requeuing behind a live engine is how one task "
                           f"gets two agents. Fence the engine, then requeue.")
            else:
                if live:
                    # requeue=True over a live claimer. Permitted -- the runner fences the engine
                    # and it is the only surface that can know -- but never silent again, because
                    # the incident's whole shape was a requeue nobody could see afterwards.
                    out["requeue_overrode"] = live["name"]
                    ctx.thread(tid, "note",
                               f"{qid} answered and REQUEUED although {live['name']} was still "
                               f"heartbeating on this task. The caller asserted that engine is "
                               f"fenced by passing requeue. If it was not, this task now has two "
                               f"agents.")
                ctx.execute(
                    "UPDATE brain.work_item SET state = 'inbox', claimed_by = '', "
                    "claimed_at = NULL, attempts = 0, blocked_on = '' WHERE id = %s", (tid,))
                out["requeued"] = True
    return out


# ------------------------------------------------------------------ withdrawal (task 0159)
#
# THE THIRD THING THAT CAN HAPPEN TO A QUESTION, and until 2026-08-18 there were only two.
#
# `ask` put a row in the operator's queue and `answer` was the only verb that could take it out
# again. So an agent whose question had stopped mattering -- because its task was cancelled,
# because it answered itself, because the branch it asked about was fabricated -- had exactly one
# way to clear the row it left in front of a human: manufacture an operator-signed answer.
#
# That is the act task 0147 was posted to stop. A tool writing `operator` on the live bus is the
# same forgery whether the motive is tidiness or not, and the record afterwards says a human
# decided something no human ever read.
#
# So withdrawal is its own verb, it is SIGNED by whoever performs it, and it never touches
# `answer`. What the operator sees is that the question leaves his queue. What the record keeps is
# the question, its text, its stated default, its `question.raised` event, and a name.

def _withdraw_open_questions(ctx, task_id: str, *, by: str, reason: str) -> tuple[list, bool]:
    """Withdraw every still-open question on one task. Returns the ids, so a caller can say so.

    Open means neither answered nor already withdrawn: an answered question is a decision the
    operator made and this must not overwrite it, which is also what `question_not_both_ck`
    enforces one layer down (migration 31).

    IT ASKS THE STORE BEFORE IT WRITES, and that is not defensive habit, it is the only order
    that works. `cancel` served long before this cascade existed, and against a store below ledger
    31 the UPDATE below raises `UndefinedColumn` -- which aborts the whole transaction, so the
    task does not get cancelled either. A feature added to a verb may not take the verb away
    (`docs/SCHEMA-TOLERANCE.md`, rule 5). A `try` cannot fix that: after the failed statement
    Postgres refuses every later one in the same transaction. So the probe comes first.

    Returns `(ids, cascaded)`. `cascaded=False` says the questions are still standing and this
    store cannot record otherwise, which is a fact the caller must say out loud rather than let
    `[]` be read as "there were none".
    """
    if not schema.question_withdrawal(ctx=ctx):
        open_ids = [r["id"] for r in ctx.execute(
            "SELECT id FROM brain.question WHERE work_item_id = %s AND answer IS NULL",
            (task_id,))]
        return open_ids, False
    rows = ctx.execute(
        """UPDATE brain.question
              SET withdrawn_at = now(), withdrawn_by = %s, withdrawn_reason = %s
            WHERE work_item_id = %s AND answer IS NULL AND withdrawn_at IS NULL
        RETURNING id""",
        (by, reason, task_id))
    return [r["id"] for r in rows], True


@store.transition("withdraw")
def withdraw(ctx, *, qid, agent="", reason=""):
    """Take a question out of the operator's queue WITHOUT answering it, and sign the act.

    The verb an agent needs when its own question stopped mattering. It does not decide the
    question, it retires it: `answer` stays NULL forever, so nothing downstream can ever read a
    withdrawal as a decision, and `questions --withdrawn` shows it with the hand that acted.

    ANONYMOUS IS REFUSED, through the same door `set` uses. A withdrawal whose record says
    `operator` when a script did it is the defect this verb exists to remove, so a fleet terminal
    that names nobody gets a refusal rather than a default identity. Migration 31 carries the
    same rule as a CHECK, for the surface written after this one.

    IT DOES NOT MOVE THE TASK. A blocked task whose question is withdrawn stays blocked, on
    purpose: requeuing is the dangerous half (`answer`'s docstring, the 2026-08-16 incident), and
    a verb about the operator's queue does not get to restart an engine as a side effect. The
    thread note says so where the next reader will be standing.
    """
    who = _caller(agent)
    if not who:
        raise VerbError(
            f"`withdraw` needs an agent here. This process is a fleet terminal "
            f"(SWARM_PARENT_TASK={os.environ.get('SWARM_PARENT_TASK', '').strip()}) and an "
            f"unnamed withdrawal would be filed as `operator`, who did not do it. A withdrawal "
            f"nobody signed is the anonymous write this verb was built to replace. Pass "
            f"--from <your name>, or export SWARM_AGENT.", code=6)
    q = ctx.one("SELECT * FROM brain.question WHERE id = %s", (qid,))
    if not q:
        raise VerbError(f"no such question: {qid}")
    if q["answer"] is not None:
        raise VerbError(
            f"{qid} is already answered. A withdrawal cannot take back a decision the operator "
            f"made -- `reanswer` corrects an answer, and only he should.")
    # THIS ONE REFUSES RATHER THAN FALLS BACK, and the asymmetry is doctrine rule 5. Every read
    # site below ledger 31 keeps serving because it served before migration 31 existed; `withdraw`
    # did not exist before it, so nothing regresses when it stops here, and the alternative is
    # worse than an error: an agent told its question was withdrawn when the store recorded
    # nothing would walk away from a question still sitting in front of the operator. A sentence,
    # not a psycopg2 traceback, because the reader is an agent or the operator and neither can act
    # on `UndefinedColumn`.
    if not schema.question_withdrawal(ctx=ctx):
        raise VerbError(schema.below_31(), code=6)
    if q.get("withdrawn_at") is not None:
        raise VerbError(f"{qid} was already withdrawn by {q['withdrawn_by']}"
                        f"{' at ' + q['withdrawn_at'].isoformat() if q.get('withdrawn_at') else ''}.")

    ctx.execute(
        "UPDATE brain.question SET withdrawn_at = now(), withdrawn_by = %s, "
        "withdrawn_reason = %s WHERE id = %s", (who, reason, qid))
    tid = q["work_item_id"] or ""
    if tid:
        task = ctx.one("SELECT state FROM brain.work_item WHERE id = %s", (tid,))
        ctx.actor = who
        note = f"{qid} withdrawn by {who}: {reason or 'no reason given'}. It is out of the " \
               f"operator's queue and was never answered."
        if task and task["state"] == "blocked":
            note += (f" {tid} is still BLOCKED: a withdrawal does not requeue anything, because "
                     f"requeuing behind a live engine is how one task gets two agents.")
        ctx.thread(tid, "note", note)
    return {"id": qid, "task": tid, "withdrawn_by": who, "reason": reason,
            "asked_by": q["asked_by"] or ""}


# ------------------------------------------------------------------ objectives

@store.transition("accept")
def accept(ctx, *, name):
    row = ctx.one("UPDATE brain.objective SET state = 'accepted', accepted_at = now() "
                  "WHERE name = %s AND state = 'inbox' RETURNING id, name", (name,))
    if not row:
        raise VerbError(f"no objective in inbox named: {name}")
    return {"name": row["name"], "state": "accepted"}


#: The four things a transcription may honestly be. `null` and `unavailable` both mean NO TEXT,
#: and they are kept apart because they call for different acts: `null` is "re-record, the room
#: was too loud", `unavailable` is "install an engine". Mirrored from migration 24's CHECK, and
#: the database is the load-bearing copy -- this one exists so the refusal names the rule instead
#: of the constraint.
TRANSCRIPTION_STATUSES = ("ok", "partial", "null", "unavailable")

#: Statuses that assert the engine produced NOTHING. A body under one of these is a fabrication.
TRANSCRIPTION_NO_TEXT = ("null", "unavailable")

INTAKE_FORMATS = ("text", "voice")

# Migration 50, row 0438. WHO put an intake item there, declared by the writer at the door and
# never inferred from what the row is called. The two values are not symmetric and the asymmetry
# is the design: an undeclared row reads as `human` everywhere, so a machine that wants to stay
# off his badge has to say so, and a human drop that says nothing still reaches him.
OBJECTIVE_ORIGINS = ("human", "machine")


@store.transition("intake")
def intake(ctx, *, name, body, source_name, source_signature, bytes_=None,
           intake_format="text", media_pointer=None, media_pointer_host=None, media_sha256=None,
           media_bytes=None, media_kind=None, media_duration_s=None, transcription_engine=None,
           transcription_status=None, capture_id=None, origin=None,
           author=None, occurred_at=None):
    """Copy one objective in, deduped by `<size>:<mtime>`, which is the file bus's key.

    THE VOICE DOOR IS THIS DOOR (task 0377, lane V2). A voice note is an input FORMAT, not a
    subsystem, so it does not earn a transition of its own: it arrives here with more keyword
    arguments and lands as one `state='inbox'` objective that a human sorts. That is the whole
    landing decision, and the argument for it is that the operator's monologue contains durable
    objectives, his own tasks and things only he can decide all at once -- so a path that landed
    a typed row would have to CLASSIFY spoken English, and a wrong classification is the same
    defect class as a fabricated transcription. It will be believed later. The machine lands one
    row; the fan-out to `accept`, `post` and `ask` is a human act through verbs that exist.

    Three refusals below mirror migration 24's CHECKs. The database is the load-bearing copy and
    these do not replace it; they exist so a caller is told which RULE it broke rather than which
    constraint name it hit, and so the rule is stated in the transition every surface calls.

    `capture_id` links the `brain.voice_capture` attempt row to the objective it produced, IN THIS
    TRANSACTION, which is what makes the two unable to disagree about whether a note landed.
    """
    if intake_format not in INTAKE_FORMATS:
        raise VerbError(f"--intake-format must be one of {', '.join(INTAKE_FORMATS)}, "
                        f"got {intake_format!r}")
    if transcription_status is not None and transcription_status not in TRANSCRIPTION_STATUSES:
        raise VerbError(f"transcription_status must be one of "
                        f"{', '.join(TRANSCRIPTION_STATUSES)}, got {transcription_status!r}")

    # NEVER FABRICATE A TRANSCRIPTION. D3 measured this on `stated_goal`: a fabricated goal is
    # worse than a null one because it will be believed later. A status that says the engine
    # heard nothing, carrying words anyway, is refused here and again by the table.
    if transcription_status in TRANSCRIPTION_NO_TEXT and (body or "").strip():
        raise VerbError(
            f"transcription_status={transcription_status!r} says the engine produced no text, "
            f"and a body of {len((body or '').strip())} characters came with it. Refusing: "
            f"unintelligible audio is a NULL, not a guess. Land it with an empty body -- the "
            f"audio is retained and the pointer is on the row, so it can be transcribed again.")
    # The mirror half, and it is the 0-byte save arriving through the other door: a status that
    # asserts there IS text, with nothing behind it.
    if transcription_status in ("ok", "partial") and not (body or "").strip():
        raise VerbError(
            f"transcription_status={transcription_status!r} asserts the engine returned text and "
            f"the body is empty. That is the 2026-08-17 incident's shape: a save that reported "
            f"success and wrote nothing. Use 'null' if the engine heard nothing.")

    if intake_format == "voice" and not (media_pointer and media_pointer_host and media_sha256
                                         and transcription_status):
        raise VerbError(
            "a voice objective must carry media_pointer, media_pointer_host, media_sha256 and "
            "transcription_status. The audio is kept and pointed at, never copied into the "
            "store, and a voice row that lost its pointer has lost the recording.")

    seen = ctx.one("SELECT id, name FROM brain.objective "
                   "WHERE source_name = %s AND source_signature = %s",
                   (source_name, source_signature))
    if seen:
        # Idempotent, and the capture still reaches a terminal state. A capture left in
        # `transcribed` because its content had already landed would read as STUCK on the health
        # line, which is a false alarm and false alarms are how a real one gets ignored.
        _voice_capture_landed(ctx, capture_id, seen["id"], seen["name"])
        return {"name": name, "taken": False, "reason": "already seen",
                "objective_name": seen["name"]}
    exists = ctx.one("SELECT id FROM brain.objective WHERE name = %s", (name,))
    if exists:
        return {"name": name, "taken": False, "reason": "name already present"}

    if intake_format == "text" and capture_id is None:
        # BYTE-IDENTICAL to the statement this verb has always run. Deliberate: migration 24 has
        # not been applied to the live `brain` database yet, and a text intake that started
        # naming columns which do not exist there would break the drop-folder path that works
        # today in order to serve a format that is not in use yet.
        row = ctx.one(
            """INSERT INTO brain.objective (name, state, body, bytes, source_name, source_signature)
               VALUES (%s, 'inbox', %s, %s, %s, %s) RETURNING id""",
            (name, body, bytes_, source_name, source_signature))
    else:
        row = ctx.one(
            """INSERT INTO brain.objective
                 (name, state, body, bytes, source_name, source_signature,
                  intake_format, media_pointer, media_pointer_host, media_sha256, media_bytes,
                  media_kind, media_duration_s, transcription_engine, transcription_status)
               VALUES (%s, 'inbox', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (name, body, bytes_, source_name, source_signature,
             intake_format, media_pointer, media_pointer_host, media_sha256, media_bytes,
             media_kind, media_duration_s, transcription_engine, transcription_status))

    # THE ORIGIN, AND IT IS A SECOND STATEMENT ON PURPOSE (migration 50, row 0438).
    #
    # Both INSERTs above are left byte-identical. The text arm carries a comment explaining that
    # it is deliberately unchanged so a store without migration 24 keeps working, and adding a
    # column to it would spend exactly the property that comment is protecting. The same argument
    # applies one migration later, so the origin is written after the fact, only when it was
    # declared, and only when the store has somewhere to put it.
    #
    # UNDECLARED IS NOT WRITTEN AT ALL, rather than written as 'human'. NULL and 'human' READ the
    # same through `brain.objective_origin`, and they mean different things: NULL is "nobody said"
    # and 'human' is "somebody said a person put this here". Collapsing them would throw away the
    # only evidence that a producer has not been taught to declare itself yet.
    origin_declared = str(origin or "").strip().lower()
    if origin_declared:
        if origin_declared not in OBJECTIVE_ORIGINS:
            raise VerbError(
                f"--origin must be one of {', '.join(OBJECTIVE_ORIGINS)}, got {origin!r}. An "
                f"unrecognised origin is refused rather than folded, because the fold is toward "
                f"`human` and a machine that meant to be quiet would silently start showing up "
                f"on his badge instead.")
        if schema.has_column("objective", "origin", ctx=ctx):
            ctx.execute("UPDATE brain.objective SET origin = %s WHERE id = %s",
                        (origin_declared, row["id"]))

    # WHO WROTE IT AND WHEN IT HAPPENED (migration 53, S1 wave 1). A THIRD STATEMENT, for exactly
    # the reason the origin block above is a second one: both INSERTs stay byte-identical, so a
    # store that has not applied 53 keeps landing objectives rather than dying on a column that is
    # not there. `schema.has_column` is the same gate the origin write uses.
    #
    # NEITHER IS INFERRED AND NEITHER IS DEFAULTED. An absent author is NULL, never the connector's
    # own name, because "the machine that moved it" is not the author and a row that claimed
    # otherwise would answer "who is waiting on me" with a lie. An absent or unparseable
    # occurred_at is NULL, never `now()`, because now() is when the SWEEP ran: writing it would
    # make every late-swept message look fresh, which is the sort defect this column exists to fix.
    #
    # The table refuses a blank author and refuses a pre-2000 occurred_at, so a connector whose
    # date parse fell to the unix epoch is told at the door instead of poisoning the sort order
    # with a 1970 row that merely looks old.
    if author is not None and str(author).strip():
        if schema.has_column("objective", "author", ctx=ctx):
            ctx.execute("UPDATE brain.objective SET author = %s WHERE id = %s",
                        (str(author).strip(), row["id"]))
    if occurred_at is not None:
        if schema.has_column("objective", "occurred_at", ctx=ctx):
            ctx.execute("UPDATE brain.objective SET occurred_at = %s WHERE id = %s",
                        (occurred_at, row["id"]))

    _voice_capture_landed(ctx, capture_id, row["id"], name)
    return {"name": name, "taken": True, "objective_id": row["id"],
            "objective_name": name, "origin": origin_declared or None}


def _voice_capture_landed(ctx, capture_id, objective_id, objective_name, work_item_id=None):
    """Close the capture attempt in the SAME transaction as the thing it produced.

    Not a second door: `brain.voice_capture` has no transition of its own that can set `landed`,
    precisely because landing is this state change and this is the function that owns it. A
    caller that tried to mark a capture landed without writing something would find no verb
    that does it.

    TWO LANDINGS, ONE FUNCTION (migration 51). `intake` lands an objective and `note` lands onto a
    work item. They pass different arguments to the same UPDATE rather than each writing their
    own, because the invariant that matters is about the capture and not about either landing:
    exactly one target, set in the same transaction as the target itself. Two copies of this
    statement is how one of them would eventually forget to move `state`.

    THE DATABASE BACKS IT, and this function does not have to be trusted.
    `voice_capture_landed_points_at_one_ck` refuses a landed row pointing at neither or at both,
    for every caller, however it is written.

    ON A STORE BELOW LEDGER 51 the work-item arm is refused by the OLD constraint, which requires
    an objective on any landed row. That refusal is correct and is left to happen: a note landing
    on a store that cannot record where it landed would leave a capture claiming a landing nothing
    can point at, which is the disagreement this whole table exists to prevent. The message says
    which migration is owed.
    """
    if not capture_id:
        return
    # THE COLUMN IS ASKED FOR, NOT ASSUMED, AND THE OBJECTIVE PATH MUST NOT PAY FOR THE NOTE PATH.
    # An UPDATE that named `work_item_id` unconditionally would break `intake` on every store below
    # ledger 51, which is a landing that has worked since migration 24 and has nothing to do with
    # this change. So the column joins the statement only when the store has it.
    has_wid = schema.has_column("voice_capture", "work_item_id", ctx=ctx)
    if work_item_id and not has_wid:
        raise VerbError(
            f"capture {capture_id!r} cannot land as a note on {work_item_id}: this store has no "
            f"brain.voice_capture.work_item_id, so it is below ledger 51 and has nowhere to record "
            f"where the note went. Apply migrations/0051_a_voice_note_can_land_on_a_row.sql. "
            f"Nothing was written: the note and the capture move together or not at all.")
    sets = "state = 'landed', objective_id = %s, objective_name = %s, updated_at = now()"
    params = [objective_id, objective_name]
    if has_wid:
        sets += ", work_item_id = %s"
        params.append(work_item_id)
    params.append(capture_id)
    row = ctx.one(
        f"UPDATE brain.voice_capture SET {sets} "
        " WHERE capture_id = %s AND state = 'transcribed' RETURNING capture_id",
        tuple(params))
    if not row:
        # The objective INSERT above is in this transaction and rolls back with the raise, so a
        # capture in the wrong state cannot leave a half-landed pair behind.
        found = ctx.one("SELECT state FROM brain.voice_capture WHERE capture_id = %s",
                        (capture_id,))
        raise VerbError(
            f"capture {capture_id!r} is {found['state'] if found else 'not in the store'}, and "
            f"only a capture in state 'transcribed' can land. Nothing was written: the objective "
            f"and the capture row move together or not at all.")


# ------------------------------------------------------------------ threads, mail, artifacts

@store.transition("note")
def note(ctx, *, id, text, agent="", capture_id=None):
    """Append to a task's thread. Since migration 51 this is also where a VOICE note lands.

    `capture_id` IS NOT A NEW DOOR AND THAT IS THE POINT (row 0432, his ask 3). The voice lane's
    own rule is that a voice note is an input FORMAT and not a subsystem, so it earns no landing
    verb: `intake` takes extra keyword arguments and lands an objective, and this takes one extra
    keyword argument and lands a note on a row he is already looking at. The transcript posts
    through the verb every agent in this system already uses to say something about a task.

    THE CAPTURE CLOSES IN THIS TRANSACTION, through the SAME function `intake` calls. That is what
    makes `brain.voice_capture` unable to disagree with the thing it landed into: either the note
    and the terminal capture row both exist, or neither does. A capture left non-terminal past its
    `due_at` reads as STUCK on the health line, which is the one signal that catches an outage
    nothing raised, so a landing path that forgot to close it would be teaching that signal to cry
    wolf about ordinary work.
    """
    _require(ctx, id)
    ctx.actor = agent
    ctx.thread(id, "note", text)     # full text. The 3000-byte cut does not port.
    _voice_capture_landed(ctx, capture_id, None, None, work_item_id=id)
    return {"id": id, "capture_id": capture_id}


@store.transition("msg")
def msg(ctx, *, text, agent, to, task=None):
    tid = norm_task_id(task) if task else None
    if tid:
        _require(ctx, tid)
    ctx.execute(
        "INSERT INTO brain.message (from_agent, to_agent, kind, work_item_id, text) "
        "VALUES (%s, %s, 'msg', %s, %s)", (agent, to, tid, text))
    if tid:
        ctx.actor = agent
        ctx.thread(tid, "msg", text, to_agent=to)
    return {"to": to, "task": tid}


@store.transition("inbox mark-read")
def inbox_mark_read(ctx, *, agent):
    head = ctx.scalar("SELECT COALESCE(max(seq), 0) FROM brain.message WHERE to_agent = %s",
                      (agent,))
    ctx.execute(
        """INSERT INTO brain.agent (name, inbox_read_seq) VALUES (%s, %s)
           ON CONFLICT (name) DO UPDATE SET inbox_read_seq = EXCLUDED.inbox_read_seq""",
        (agent, head))
    return {"agent": agent, "read_seq": head}


@store.transition("artifact")
def artifact(ctx, *, id, path, kind="created", note="", agent="", exists=None, actor_type=None,
             produced_by=None, produced_by_ref=None, resolution_status=None, path_host=""):
    """Record one thing a task produced. Safe to call as many times as you like.

    `exists_at_record` is stamped by the caller, which has the filesystem. Re-statting at read
    time is what turns a path that was claimed and never written into a finding rather than a
    formatting problem.

    The lineage triple, same contract as `post` above: built by
    `brain_adapter.store_join.lineage_columns`, never here, and never partially.
    """
    if kind not in ARTIFACT_KINDS:
        raise VerbError(f"--kind must be one of {', '.join(ARTIFACT_KINDS)}, got {kind!r}")
    task = _require(ctx, id)
    who = agent or task.get("claimed_by") or ""
    ctx.execute(
        """INSERT INTO brain.artifact
             (work_item_id, agent, path, path_host, kind, note, exists_at_record,
              actor_type, produced_by, produced_by_ref, resolution_status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (id, who, path, path_host or "", kind, note, bool(exists), actor_type, produced_by,
         produced_by_ref, resolution_status))
    # first_seen is the MINIMUM stamp in the (path, kind) group, never the first record's: a
    # host clock that steps backwards between two writes is a hazard this operation has met.
    ctx.execute(
        """UPDATE brain.artifact a SET first_seen = m.min_ts
             FROM (SELECT path, kind, min(ts) AS min_ts FROM brain.artifact
                    WHERE work_item_id = %s GROUP BY path, kind) m
            WHERE a.work_item_id = %s AND a.path = m.path AND a.kind = m.kind""", (id, id))
    summary = ctx.scalar(
        """SELECT string_agg(DISTINCT path, ', ' ORDER BY path)
             FROM brain.artifact WHERE work_item_id = %s""", (id,))
    ctx.execute("UPDATE brain.work_item SET artifacts = %s WHERE id = %s", (summary or "", id))
    if not exists and kind != "external":
        ctx.actor = who
        ctx.thread(id, "artifact",
                   f"MISSING at record time: {path} ({kind}). Recorded anyway; the record is "
                   f"the finding.")
    return {"id": id, "path": path, "kind": kind, "exists": bool(exists)}


# ------------------------------------------------------------------ agents and the fleet

@store.transition("heartbeat")
def heartbeat(ctx, *, agent, status, task=None, pid=None, host="", role=""):
    """Liveness. The row is the authority on state, never a convenience copy.

    `host` comes from the caller or from config, never from `uname()`: the CLI relays, so on a
    relaying host `uname()` is the BUS host and doctor's mismatch warning becomes a permanent
    false positive.
    """
    tid = norm_task_id(task) if task else None
    ctx.execute(
        """INSERT INTO brain.agent (name, role, status, work_item_id, pid, host, updated)
           VALUES (%s, %s, %s, %s, %s, %s, now())
           ON CONFLICT (name) DO UPDATE
             SET role = CASE WHEN EXCLUDED.role <> '' THEN EXCLUDED.role ELSE brain.agent.role END,
                 status = EXCLUDED.status,
                 work_item_id = EXCLUDED.work_item_id,
                 pid = COALESCE(EXCLUDED.pid, brain.agent.pid),
                 host = CASE WHEN EXCLUDED.host <> '' THEN EXCLUDED.host ELSE brain.agent.host END,
                 updated = now()""",
        (agent, role or "terminal", status, tid, pid, host or ""))
    return {"agent": agent, "status": status, "task": tid}


@store.transition("reap")
def reap(ctx, *, stale_seconds=900, act=False):
    """Requeue work owned by an agent that stopped heartbeating. Shows first, acts with --yes.

    The row is the authority. An agent with no row at all still holds nothing, so a task whose
    claimer never heartbeated is reaped on the task's own claim age rather than being invisible.
    """
    rows = ctx.execute(
        """SELECT w.id, w.claimed_by, w.claimed_at, a.updated,
                  EXTRACT(EPOCH FROM (now() - COALESCE(a.updated, w.claimed_at)))::int AS silent
             FROM brain.work_item w
             LEFT JOIN brain.agent a ON a.name = w.claimed_by
            WHERE w.state = 'active'
              AND COALESCE(a.updated, w.claimed_at) < now() - make_interval(secs => %s)
            ORDER BY w.id""", (stale_seconds,))
    if not act:
        return {"found": rows, "requeued": []}

    out = []
    for r in rows:
        # Conditional on still being the claimer, same reason as `release`: the agent that was
        # holding it is the only one whose claim this may clear.
        got = ctx.one(
            """UPDATE brain.work_item SET state = 'inbox', claimed_by = '', claimed_at = NULL
                WHERE id = %s AND state = 'active' AND claimed_by = %s RETURNING id""",
            (r["id"], r["claimed_by"]))
        if got:
            ctx.actor = "reap"
            ctx.thread(r["id"], "reap",
                       f"requeued: {r['claimed_by']} silent for {r['silent']}s")
            ctx.execute("UPDATE brain.agent SET status = 'dead', work_item_id = NULL "
                        "WHERE name = %s", (r["claimed_by"],))
            out.append(r["id"])
    return {"found": rows, "requeued": out}


@store.transition("set")
def set_field(ctx, *, id, key, value, agent="", force=False, as_operator=False):
    """One frontmatter field, and only from the list a hand edit is allowed to touch.

    `state` IS THE SIXTH DOOR. Everything else on the settable list is metadata -- retitling or
    reprioritising a running task is an ordinary edit and stays ungated -- but `set state inbox`
    on a task another agent is executing makes it claimable again with no verb named in the
    trail, which is the incident with a different spelling. So `key == 'state'` goes through the
    same predicate as the finishers and every other key does not.

    THE TWO HARD FLAGS AND `parent` ARE THE SEVENTH. `post` refuses to lower an inherited flag,
    out loud, and D1 tested that across three hops. `set` used to run `truthy(value)` and write
    whatever it was handed, so the identical act was permitted one verb over, and the adversarial
    pass walked out through it in two individually-compliant hops (`set external false`, then
    `set parent <clean>`), with every descendant born clean afterwards. EF-7 says the flags
    inherit by OR and may be RAISED, NEVER LOWERED, so:

      * lowering either flag is refused here exactly as it is in `post`;
      * a re-parent FREEZES what this task resolves today into its own columns before it moves,
        so moving to a clean parent re-ORs and can never reset;
      * the cyclic edge is refused before it is written, because the read that resolves these
        flags used to walk the chain forever when one existed.
    """
    # `kind` joins the settable set with migration 52 (his ask 5). It is a plain column with a
    # CHECK, not a signal, so it needs no entry in LEVELLED_SIGNALS and no fold: the database
    # refuses anything but the two words, and `brain.work_item_kind` decides what NULL means.
    settable = {"priority", "lane", "host", "workdir", "depends_on", "max_attempts", "title",
                "parent", "canonical_task", "state", "created", "agent_claimable", "kind",
                *LEVELLED_SIGNALS, *HARD_FLAGS}
    if key not in settable:
        raise VerbError(f"{key} is not settable. One of: {', '.join(sorted(settable))}")
    who = _caller(agent)
    if not who:
        raise VerbError(
            f"`set` needs an agent here. This process is a fleet terminal "
            f"(SWARM_PARENT_TASK={os.environ.get('SWARM_PARENT_TASK', '').strip()}) and an "
            f"unnamed `set` is filed on {id}'s thread as `operator`, who did not do it -- which "
            f"is how the one record of who cleared a hard flag named the wrong person on "
            f"2026-08-16. Pass --agent <your name>, or export SWARM_AGENT.", code=6)
    # LOWERING `agent_claimable` IS THE EIGHTH DOOR, and task 0119 is the measurement.
    #
    # On 2026-08-18 the admiral lowered the flag on 0103 SIXTY SECONDS after T5 claimed it, on a
    # task that changes a view on a client's executive dashboard. The write succeeded, `swarm
    # show 0103` began printing `claimable by an agent: NO -- held for the operator`, and T5's
    # runner carried on executing it. Nothing bad happened because T5 staged everything as
    # `*.PROPOSED.sql` and touched no live view -- that is a good agent, not a guarantee.
    #
    # `_AGENT_CLAIMABLE` is a CLAIM-TIME predicate and it is correct: task 0414 put it inside the
    # `FOR UPDATE SKIP LOCKED` statement on purpose and nothing here touches it. But `claim` only
    # ever reads rows in `inbox`, so lowering the flag on an ACTIVE row changes nothing about
    # today: its entire effect is on the row's FUTURE, after a fail or a release requeues it.
    # What the caller believed they had bought -- "stop working this" -- was never on offer,
    # because no verb means that.
    #
    # So the write is refused through the SAME door every finisher loads its task through, and
    # the refusal names the three things the caller might actually have meant. Not "accept and
    # signal", which leaves the write silent by default and preserves exactly today's lie with a
    # message attached; not "accept and release", which would make a metadata key a finisher that
    # throws away live work with no --force and no thread entry, beside `release` and `cancel`
    # which the 2026-08-16 fix deliberately gated.
    #
    # `--force` is still open, because holding the FUTURE of a running row is a legitimate act and
    # the admiral's real intent. It is signed, it lands on the thread, and it messages the holder.
    # Raising the flag is untouched: the safe direction stays ungated.
    held_holder = ""
    if key == "state":
        _hold(ctx, id, who, force=force)
    elif key == "agent_claimable" and not truthy(value):
        row = _hold(ctx, id, who, force=force, advice=_LOWERING_A_HELD_ROW)
        if row.get("state") == "active" and (row.get("claimed_by") or "").strip():
            held_holder = (row["claimed_by"] or "").strip()
    else:
        _require(ctx, id)
    ctx.actor = who

    if key in LEVELLED_SIGNALS:
        value = validate_signal(key, value)
    elif key in HARD_FLAGS:
        value = truthy(value)
        if not value:
            _refuse_lowering(ctx, id, key)
    elif key == "agent_claimable":
        # `as_operator` selects the OPERATOR'S LOGIN in store.transitions._login_for; it is not
        # a permission this function grants. Raising false -> true is refused by migration 26's
        # trigger for any session `brain.current_human()` does not recognise, so an agent that
        # sets the flag here gets the database's refusal and not a row. Lowering is open to
        # everybody: taking work back from the fleet is the safe direction.
        value = truthy(value)
    elif key in ("priority", "max_attempts"):
        value = int(value)
    elif key == "state" and value not in STATES:
        raise VerbError(f"state must be one of {', '.join(STATES)}")
    elif key == "parent":
        value = _reparent(ctx, id, value) or None

    ctx.execute(f"UPDATE brain.work_item SET {key} = %s WHERE id = %s", (value, id))

    # READ BACK WHAT THE DATABASE KEPT, not what the caller asked for. Migration 26's first
    # trigger COERCES rather than refuses: `agent_claimable true` on a row that is
    # `actor_type = 'human'` lands false, correctly and silently. A verb that echoed the request
    # would tell the operator he had just handed his own row to the fleet when he had not, and
    # the thread -- the only durable record of the edit -- would say the same thing. So the
    # stored value is what is returned and what is written to the thread.
    stored = ctx.scalar(f"SELECT {key} FROM brain.work_item WHERE id = %s", (id,))

    # THE WORKDIR GATE, ON THE SECOND DOOR. Task 0100. `post` refuses an agent-claimable row with
    # no workdir; `set` reaches both halves of that pair one field at a time -- raise
    # `agent_claimable` on a row that has no workdir, or blank the workdir of a row the fleet may
    # already claim -- and either edit rebuilds the refused state without the refusal. This
    # codebase has already been walked out through exactly that gap once: `set external false`
    # was permitted while `post` refused it, and two individually-compliant hops laundered the
    # flag. A guard that lives at one verb is a guard with a door beside it.
    #
    # Judged on WHAT THE DATABASE KEPT and not on what was asked, because migration 26 COERCES
    # `agent_claimable true` to false on a human-actor row. That row never becomes claimable, so
    # refusing it for a workdir it will never use would be a wrong refusal wearing this one's
    # words. Raising here rolls the whole transaction back (store.transitions.apply rollbacks on
    # any exception), so the UPDATE above does not survive the refusal.
    if key in ("agent_claimable", "workdir"):
        now = ctx.one("SELECT agent_claimable, workdir FROM brain.work_item WHERE id = %s", (id,))
        _check_fleet_workdir(bool(now["agent_claimable"]), now["workdir"])

    ctx.thread(id, "set", f"{key} = {stored}"
               + ("" if stored == value else f" (asked for {value}; the store coerced it)"))

    # A FORCED HOLD OVER A LIVE RUN SAYS SO, in the two places somebody might read it. `_hold`
    # has already written the OVERRIDE line naming who forced it; this says what the override
    # DID NOT do, because that is the half the admiral got wrong on 0103. The message is
    # addressed to the holder rather than filed only on the thread, so it is at least deliverable
    # -- see `_LOWERING_A_HELD_ROW` for what a terminal mid-run will and will not notice.
    if held_holder and not stored:
        ctx.execute(
            "INSERT INTO brain.message (from_agent, to_agent, kind, work_item_id, text) "
            "VALUES (%s, %s, 'msg', %s, %s)",
            (who or "operator", held_holder, id,
             f"{id} IS NOW HELD FOR THE OPERATOR while you are executing it. {who or 'somebody'} "
             f"lowered agent_claimable on the row you hold. Nothing stopped your run and nothing "
             f"released your claim -- the hold reaches future claims only. Do not apply anything "
             f"irreversible on this task: stage it and report. If you have already applied "
             f"something, say so on the thread now."))
        # "forced" only when it WAS forced. The holder lowering the flag on its own live task
        # passes `_hold` with no override, and a thread line calling that an override would put a
        # word in the record that describes a different act. Same defect this task is about, one
        # size down: a sentence that is nearly true about a row somebody will read as a guarantee.
        how = ("forced" if force and who != held_holder else
               "set" if who != held_holder else "lowered the flag on its own live task,")
        ctx.thread(id, "note",
                   f"HELD FOR THE OPERATOR WHILE {held_holder} IS EXECUTING IT. "
                   f"{who or 'somebody'} {how} agent_claimable=false on an active row. This did "
                   f"NOT recall the claim, stop the runner, or release the task; it takes effect "
                   f"the next time this row would be handed out. {held_holder} was messaged. To "
                   f"actually stop the run: `swarm cancel {id} --agent <you> --force`.")

    return {"id": id, key: stored, "asked": value, "coerced": stored != value,
            "held_while_active": held_holder or None}


def _refuse_lowering(ctx, task_id: str, flag: str) -> None:
    """EF-7's one direction, enforced where `post` already enforces it."""
    resolved = _flags(ctx, task_id)
    if not resolved[flag]:
        return                      # already false: writing false again changes nothing
    own = ctx.one("SELECT external, canon_touching, parent FROM brain.work_item WHERE id = %s",
                  (task_id,))
    if resolved.get("lineage_cycle"):
        source = ("its parent chain does not resolve, so both flags fail closed until the chain "
                  "is repaired")
    elif own[flag]:
        source = "it is set on this task itself"
    else:
        source = f"it is inherited by OR from {own['parent'] or 'an ancestor'}"
    raise VerbError(
        f"refusing to clear {flag} on {task_id}: {source}. A hard flag may be RAISED and never "
        f"LOWERED (D00 rule 8, EF-7) -- `post` has always refused this and `set` refusing it is "
        f"the same rule, not a new one. If the flag is wrong, it is wrong on the task that "
        f"carries it and clearing it there is an operator decision with a reason on the thread; "
        f"if this task is genuinely not that work, it is a different task, not a re-parented one.",
        code=7)


def _reparent(ctx, task_id: str, new_parent) -> str:
    """Move a task, refusing the loop and freezing the flags it resolves today into its own row.

    THE TWO-HOP LAUNDER, CLOSED AT THE SECOND HOP. `set external false` is refused above, which
    already breaks the measured path. This is the independent half: even with the flag never
    touched, moving a task out from under an external parent used to drop the inherited value on
    the floor -- resolved external went true -> false, and every descendant posted afterwards was
    born clean. Freezing first means the move re-ORs. It can raise this task's own flags and it
    can never lower what it already resolved.
    """
    new_parent = norm_task_id(new_parent)
    if new_parent:
        if new_parent == task_id:
            raise VerbError(f"refusing to make {task_id} its own parent: the flag walk over its "
                            f"chain would never terminate, for this row or any other.", code=7)
        if not ctx.one("SELECT 1 FROM brain.work_item WHERE id = %s", (new_parent,)):
            # Used to reach the table and come back as a bare foreign key violation, which reads
            # as a crash rather than as a rule.
            raise VerbError(f"no such parent task: {new_parent}", code=7)
        _refuse_cycle(ctx, task_id, new_parent)
        _refuse_poisoned_chain(ctx, new_parent, f"cannot re-parent {task_id} under {new_parent}")

    lin = ctx.one("SELECT has_external, has_canon, is_truncated "
                  "FROM brain.work_item_lineage(%s)", (task_id,))
    # The raw OR over every ancestor actually read. A cycle stops the walk only after visiting
    # every reachable ancestor, so the OR is complete; the depth cap does not, so that one is
    # conservative.
    keep = {"external": bool(lin["has_external"]) or bool(lin["is_truncated"]),
            "canon_touching": bool(lin["has_canon"]) or bool(lin["is_truncated"])}
    own = ctx.one("SELECT external, canon_touching, parent FROM brain.work_item WHERE id = %s",
                  (task_id,))
    frozen = [f for f in HARD_FLAGS if keep[f] and not own[f]]
    if frozen:
        ctx.execute(
            "UPDATE brain.work_item SET external = external OR %s, canon_touching = "
            "canon_touching OR %s WHERE id = %s",
            (keep["external"], keep["canon_touching"], task_id))
        ctx.thread(task_id, "note",
                   f"{' + '.join(frozen)} was inherited from {own['parent'] or 'an ancestor'} and "
                   f"is now carried by {task_id} itself, because it is moving to "
                   f"{new_parent or 'no parent'}. A hard flag inherits by OR and a re-parent "
                   f"re-ORs: it may raise this task's flags and may never reset them (EF-7).")
    return new_parent


@store.transition("tick commit")
def tick_commit(ctx, *, agent, fingerprint):
    ctx.execute(
        """INSERT INTO brain.agent (name, tick_fingerprint, tick_at) VALUES (%s, %s, now())
           ON CONFLICT (name) DO UPDATE
             SET tick_fingerprint = EXCLUDED.tick_fingerprint, tick_at = now()""",
        (agent, fingerprint))
    return {"agent": agent, "fingerprint": fingerprint}


#: The kinds `stop` and `start` file on `brain.message`. Read back by `reads.agent_lifecycle`,
#: which is what puts the reason next to STOPPED in `swarm status`.
LIFECYCLE_KINDS = ("stop", "start")


def _lifecycle(ctx, *, agent, kind, by, reason, held):
    """File one fleet-lifecycle record where a human will actually meet it.

    THE RECORD GOES ON `brain.message` AND IT GOES INSIDE THIS TRANSACTION. Both halves are the
    finding of task 0273 and neither is a preference.

    *Where.* `brain.feed` -- what `swarm feed` reads and what an admiral pass reads through it --
    is a UNION of `thread`, `message` and `question` and NOTHING else (`migrations/0001_initial.sql`
    at the view). `brain.event` is not in it. So an audit trail that lives only in `brain.event`
    is a trail in the one place the 00:57Z incident proves nobody was looking. `thread` cannot
    carry this: its `work_item_id` is NOT NULL and a fleet stop has no task. `message` can: its
    `work_item_id` is nullable, its `kind` is free text with no CHECK, and `answer` already files
    'answer' and 'reanswer' there, so a kind that is not 'msg' is established rather than novel.

    *Inside the transaction.* The alternative was an `after_commit` hook, and a hook cannot fail
    the verb by design (`store/transitions.py:after_commit` rule 2). That is exactly wrong here:
    a stop whose trail silently did not get written is the bug, not a degraded version of it. In
    one transaction, `stopped_at` and the record that explains it land together or neither lands.
    The fabric event is still emitted, from a hook, and it is the redundant copy rather than the
    load-bearing one.

    *No migration.* Every column touched here exists in migration 1, so this path serves against
    all 130 databases on this host whatever their ledger says, per `docs/SCHEMA-TOLERANCE.md`.
    A `stopped_by` / `stop_reason` column on `brain.agent` would have read more nicely and would
    have been dead on the 119 stores below the tip until the operator applied it.

    `to_agent` is the agent itself, so the notice is addressed to whoever was stopped and turns
    up in its `swarm inbox` when it comes back -- and so the read-back in `status` is an exact
    match on a name rather than a parse of prose.

    THE TEXT FORMAT IS PART OF THE CONTRACT: "STOP <agent> by <who>[, holding <task>]: <reason>".
    `cli.stopped_note` splits it on the first ": " to put the reason next to STOPPED in `swarm
    status`. Change the format here and change it there; nothing else parses it.
    """
    ctx.execute(
        "INSERT INTO brain.message (from_agent, to_agent, kind, work_item_id, text) "
        "VALUES (%s, %s, %s, %s, %s)",
        (by, agent, kind, held,
         f"{kind.upper()} {agent} by {by}"
         + (f", holding {held}" if held else "")
         + f": {reason}"))


@store.transition("stop")
def stop(ctx, *, agent, by="", reason=""):
    """Stop one agent, and say who did it and why. A stop with no reason is refused.

    THE INCIDENT, 2026-08-19 00:55Z to 01:0xZ. The commander stopped T4 and T5 on a rate-limit
    decision. The verb wrote one column and emitted nothing, so on the board that stop was
    byte-identical to two terminals dying silently. An admiral pass a minute later read exactly
    that -- "something else stopped two terminals tonight with no trail, and that is worse" --
    and restarted both, correctly, on the information it had. It reversed a commander's decision
    three minutes after it was taken, and no part of the system was wrong except this verb.

    `reason` is REQUIRED and that is the whole point rather than a nicety. An optional reason
    left empty produces a record that says a stop happened and nothing about whether it was
    meant, which is the state the fleet was already in. The refusal is what makes the trail
    non-optional; a caller that cannot say why it is stopping an agent has not decided to.

    `stopped_at` is written exactly as before and means exactly what it meant. The runner reads
    it unchanged. This is the verb learning to leave a record, not the lifecycle changing.
    """
    reason = " ".join((reason or "").split())
    if not reason:
        raise VerbError(
            "refusing to stop an agent with no reason. A stop that carries no reason is "
            "indistinguishable on the board from an agent that died, which is the 2026-08-19 "
            "00:57Z incident: an admiral could not tell the two apart and reversed a commander's "
            "decision three minutes after it was taken. Pass --reason.", code=7)
    by = (by or "").strip() or "operator"
    ctx.actor = by
    # Read BEFORE the write: `stopped_at` is about to be overwritten, and whether this was
    # already stopped is the difference between a stop and a re-stamp of one.
    was = ctx.one("SELECT stopped_at, work_item_id FROM brain.agent WHERE name = %s", (agent,))
    ctx.execute(
        """INSERT INTO brain.agent (name, stopped_at) VALUES (%s, now())
           ON CONFLICT (name) DO UPDATE SET stopped_at = now()""", (agent,))
    held = was["work_item_id"] if was else None
    _lifecycle(ctx, agent=agent, kind="stop", by=by, reason=reason, held=held)
    return {"agent": agent, "stopped": True, "by": by, "reason": reason, "held": held,
            "was_already_stopped": bool(was and was["stopped_at"]),
            "known": bool(was)}


@store.transition("start")
def start(ctx, *, agent, by="", reason=""):
    """Release one stopped agent -- and report, rather than pretend, when there is nothing to
    release.

    The ported version was `INSERT ... ON CONFLICT DO UPDATE SET stopped_at = NULL`, symmetric
    with `stop` and wrong in two ways that only show up on the name you did not mean to type.
    It reported the same success whether it had lifted a stop or not, so `start` on an agent
    that was already running looked exactly like `start` on one it had just released; and the
    INSERT half INVENTED the row, so `swarm start --agent T$` put a phantom terminal in
    `status` that nothing had ever heartbeated as. Task 0253.

    An UPDATE with the predicate in the WHERE, so the row itself decides. `RETURNING` makes the
    answer the same transaction as the act -- a read-then-write would race the reaper, which
    also writes this column. Nothing is inserted here at all: `heartbeat` is what registers an
    agent (see its ON CONFLICT above), and it runs on every runner boot, so refusing to create
    the row costs a real agent nothing and costs a typo its phantom.
    """
    by = (by or "").strip() or "operator"
    reason = " ".join((reason or "").split())
    ctx.actor = by
    released = ctx.execute(
        """UPDATE brain.agent SET stopped_at = NULL
            WHERE name = %s AND stopped_at IS NOT NULL
        RETURNING name, work_item_id""", (agent,))
    if released:
        # Task 0273. The record is filed only on the branch that CHANGED something, which is the
        # same distinction the exit codes above draw: a `start` that released nothing must not
        # leave a line in the feed saying an agent was started. That would put the exact class of
        # false record in the trail that this trail exists to prevent.
        _lifecycle(ctx, agent=agent, kind="start", by=by,
                   reason=reason or "no reason given", held=released[0]["work_item_id"])
        return {"agent": agent, "stopped": False, "released": True, "known": True,
                "by": by, "reason": reason}
    known = bool(ctx.execute("SELECT 1 FROM brain.agent WHERE name = %s", (agent,)))
    return {"agent": agent, "stopped": False, "released": False, "known": known,
            "by": by, "reason": reason}


@store.transition("pause")
def pause(ctx, *, by="operator", note=""):
    ctx.execute(
        """INSERT INTO brain.runtime_flag (key, value, set_by, note)
           VALUES ('fleet_paused', 'true', %s, %s)
           ON CONFLICT (key) DO UPDATE
             SET value = 'true', set_at = now(), set_by = EXCLUDED.set_by, note = EXCLUDED.note""",
        (by or "operator", note or ""))
    return {"paused": True, "by": by or "operator", "note": note or ""}


@store.transition("resume")
def resume(ctx, *, by="operator", note=""):
    """Clear the fleet pause.

    An UPDATE, not a DELETE. `brain_runtime` is granted DELETE on nothing at all -- migrations
    and the retention sweep are the only paths that remove a row -- so the ported "remove the
    PAUSE file" would fail with `permission denied for table runtime_flag`. Writing 'false' is
    also the better record: `set_at` and `set_by` then say who resumed and when, which the
    file's absence never could.
    """
    ctx.execute(
        """INSERT INTO brain.runtime_flag (key, value, set_by, note)
           VALUES ('fleet_paused', 'false', %s, %s)
           ON CONFLICT (key) DO UPDATE
             SET value = 'false', set_at = now(), set_by = EXCLUDED.set_by,
                 note = EXCLUDED.note""", (by or "operator", note or ""))
    return {"paused": False, "by": by or "operator", "note": note or ""}


# ------------------------------------------------------------------ runs

@store.transition("run start")
def run_start(ctx, *, id, attempt, agent, pid=None, host="", session_id="", stream_pointer=None,
              stream_pointer_host=""):
    """Open the run row, and RETURN ITS ID so the caller can key a budget stop to this run.

    `run_id` is the second key in the returned dict and the reason this statement RETURNS at all.
    `halt.budget_stop_for_run` matches first-party evidence on run_id OR session_id and nothing
    else. Until this id came back, the session id was the only key a runner could hold -- and the
    runner learns that one by reading the engine's own stream-json `init` event, which meant two
    things at once: a codex run (no stream-json) could not be guarded at all, because a stop with
    no key is reconciled as an ordinary task failure and charges the lane an attempt it did not
    earn; and a claude run stopped in its first second had no session id yet either. One RETURNING
    clause closes both.

    `id` still means the WORK ITEM, unchanged, because callers read it that way.
    """
    row = ctx.one(
        """INSERT INTO brain.run (work_item_id, attempt, agent, host, pid, session_id,
                                  stream_pointer, stream_pointer_host)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (work_item_id, attempt) DO UPDATE
             SET agent = EXCLUDED.agent, pid = EXCLUDED.pid, session_id = EXCLUDED.session_id,
                 stream_pointer = EXCLUDED.stream_pointer, started_at = now(),
                 ended_at = NULL, outcome = NULL
           RETURNING id""",
        (id, attempt, agent, host or "", pid, session_id or "", stream_pointer,
         stream_pointer_host or ""))
    if session_id:
        ctx.execute("UPDATE brain.work_item SET session_id = %s WHERE id = %s", (session_id, id))
    # `started_at = now()` on the conflict path is not cosmetic either: the row is keyed
    # (work_item_id, attempt) and `reopen` resets attempts to 0, so attempt 1 recurs on the same
    # task and lands on this same row id. Refreshing the stamp is what lets `budget_stop_for_run`
    # tell a stop filed against THIS dispatch from one filed against the previous occupant of the
    # same row. Do not drop it.
    return {"id": id, "attempt": attempt, "run_id": row["id"] if row else None}


@store.transition("run end")
def run_end(ctx, *, id, attempt, exit_code=None, outcome=None, stream_sha256=None,
            stream_bytes=None):
    ctx.execute(
        """UPDATE brain.run SET ended_at = now(), exit_code = %s, outcome = %s,
                                stream_sha256 = %s, stream_bytes = %s
            WHERE work_item_id = %s AND attempt = %s""",
        (exit_code, outcome, stream_sha256, stream_bytes, id, attempt))
    return {"id": id, "attempt": attempt}
