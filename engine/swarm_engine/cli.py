"""The swarm CLI over the store. Forty verbs, same names, same semantics, same exit codes.

This is a shipped deliverable of the engine lane, not a by-product of the console. It is what
lets the operator dogfood from Wave 2 instead of waiting for D7, and review time is the scarce
resource this whole program exists to buy back.

**It owns no state transitions.** Every verb that changes state calls `store.apply(verb, ...)`,
which is the same call the runner, the console and any future MCP wrapper make. That is the
narrow waist: `bin/swarm` was the only writer under the file bus, and the property that made
many surfaces safe at once was that none of them could invent a transition. If this file ever
grows an UPDATE, the property is gone -- so it cannot, because there is no writable connection
in this process outside a registered transition.

Exit codes are the ported contract and scripts depend on them:
    0  ok        1  error        2  empty / nothing to do
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg2

import store

from . import accept as accept_mod
from . import liveness as liveness_mod
from . import ratelimit, reads, render
from . import admin as admin_mod
from . import projects as projects_mod
from . import routines as routines_mod
from .config import agent_config, config_path, effective, is_planner, resolved
from .config import fingerprint as config_fingerprint
from .signals import (
    HARD_FLAGS,
    LEVELLED_SIGNALS,
    norm_task_id,
    signal_weights,
    truthy,
)
from . import transitions
from .transitions import ARTIFACT_KINDS, STATES, VerbError

OK, ERR, EMPTY = 0, 1, 2

CLOCK_SKEW_TOLERANCE = 120


def die(msg, code=ERR):
    print(f"swarm: {msg}", file=sys.stderr)
    sys.exit(code)


# PL/pgSQL `RAISE EXCEPTION` with no custom SQLSTATE. A guard refusing on purpose, which is a
# different event from a fault, even though both arrive here as the same Python exception.
# The SQLSTATEs a DELIBERATE refusal arrives under. `P0001` is what a bare plpgsql `RAISE
# EXCEPTION` sets. `42501` is what a guard that is about WHO IS ASKING sets on purpose --
# migration 20's `work_item_human_actor_is_a_login` and migration 26's
# `work_item_agent_claimable_raise_is_a_human` both do -- and printing "this one is a defect,
# not a rule" under those was wrong in the way that matters: it tells an agent its correct
# refusal is our bug, which is an invitation to route around it.
DB_REFUSALS = ("P0001", "42501")


def die_db(e, verb=""):
    """Render a database refusal as a refusal.

    The guards this system relies on are triggers, not Python: `question_default_null_branch`
    refuses an act-shaped default on a flagged task, and it is the first of them, not the last.
    Each one already carries a sentence written for the operator and a HINT saying what to write
    instead. Letting it out as a traceback buries that sentence under twenty frames of ours and
    makes a correct refusal read like a crash in the CLI, which is the one reading that gets it
    ignored. Catching the class here rather than per-verb is what makes every lane's future
    trigger land already rendered.

    A fault is not a refusal and comes through the same door. Anything that is not a deliberate
    `RAISE` is our defect, so its SQLSTATE and verb are printed: that is the part of the removed
    traceback still worth having, kept to the one line that needs it.
    """
    diag = e.diag
    primary = (diag.message_primary or "").strip() or str(e).strip()
    print(f"swarm: {primary}", file=sys.stderr)
    for extra in (diag.message_detail, diag.message_hint):
        if extra and extra.strip():
            print(f"       {extra.strip()}", file=sys.stderr)
    if e.pgcode and e.pgcode not in DB_REFUSALS:
        print(f"       [SQLSTATE {e.pgcode}"
              + (f" on `swarm {verb}`" if verb else "") + "] -- this one is a defect, not a rule",
              file=sys.stderr)
    sys.exit(ERR)


def out_json(obj):
    print(json.dumps(obj, default=str))


def shell_export(prefix: str, data: dict):
    for k, v in data.items():
        if isinstance(v, bool):
            v = "true" if v else "false"
        val = str(v if v is not None else "").replace("'", "'\\''")
        print(f"{prefix}{k.upper()}='{val}'")


def _tid(raw):
    return norm_task_id(raw)


# ------------------------------------------------------------------ init

def cmd_init(args):
    """Apply the migrations. `init` created directories; here it is the schema.

    The runtime plane is still files -- run streams and logs -- so those directories are made
    too. They hold pointers' targets, never state: the row is the authority.
    """
    from .signals import DEFAULT_SIGNAL_WEIGHTS  # noqa: F401  (documented in `config`)
    h = Path(os.environ.get("ENGINE_HOME", os.path.expanduser("~/.brain-runtime")))
    for d in ("runs", "logs"):
        (h / d).mkdir(parents=True, exist_ok=True)
    cfg = config_path()
    if not cfg.exists():
        from .config import DEFAULT_CONFIG
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {cfg}")
    try:
        h_state = store.health()
    except Exception as e:                                    # noqa: BLE001
        die(f"the store is not reachable: {e}\n"
            f"       Apply migrations/0001_initial.sql and 0002_roles.sql first.")
    print(f"runtime home ready at {h}")
    print(f"store: schema_migration {h_state['schema_version']}, head_seq {h_state['head_seq']}")
    return OK


# ------------------------------------------------------------------ post / claim

def cmd_post(args):
    body = args.body or ""
    if args.file:
        body = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    signals = {f: getattr(args, f, "") or "" for f in LEVELLED_SIGNALS}
    try:
        r = store.apply("post", title=args.title, lane=args.lane, host=args.host or "",
                        body=body, priority=args.priority, workdir=args.workdir or "",
                        depends_on=args.depends_on or "", posted_by=args.posted_by,
                        max_attempts=args.max_attempts, parent=args.parent or "",
                        external=args.external, canon_touching=args.canon_touching,
                        signals=signals, canonical_task=args.canonical_task,
                        project=args.project,
                        # `--mine` is the operator's own work, and it is NOT this flag that makes
                        # it his: `store.apply` reads `actor_type` and opens the OPERATOR's
                        # database login, and migration 20's trigger refuses the row to any login
                        # with no `brain.human_role` mapping. So an agent that passes `--mine` in
                        # a fleet terminal gets a refusal naming the credential it does not hold,
                        # rather than a row the console would let a human sign off. The flag is
                        # deliberately not `--actor-type <value>`: `ai` and `hybrid` are recorded
                        # by the surfaces that observe the work, not declared at post time.
                        actor_type="human" if args.mine else None,
                        # DEFAULT FALSE, and the flag is on this side of the fence on purpose.
                        # See migration 26 and `transitions.post`. `--mine` never needs it: a
                        # human-actor row is coerced to held by the database itself.
                        agent_claimable=True if args.for_agents else None)
    except (VerbError, ValueError) as e:
        die(str(e))
    for f in r["lowered"]:
        print(f"swarm: {r['id']} tried to clear the inherited hard flag {f}, which stays true. "
              f"A derived task inherits {f} by OR and can only raise it.", file=sys.stderr)
    if args.json:
        out_json(r)
    else:
        print(r["id"])
    # SAID AT POST TIME, because the alternative is a task that is never claimed and nobody
    # knows why until `doctor` runs. A held row is the correct default and a silent default is
    # not; this is the line that turns "forgot --for-agents" into a two-second fix.
    if not r.get("agent_claimable"):
        print(f"swarm: {r['id']} is HELD FROM THE FLEET (agent_claimable=false), so no agent "
              f"will claim it. That is the default: this table holds the operator's own queue "
              f"as well as the fleet's, and a row nobody classified is his. If this is fleet "
              f"work, post it with --for-agents or under --parent <a fleet task>.",
              file=sys.stderr)
    return OK


# Task 0177, out of 0122. WHY THE CLAIM HANDED OUT NOTHING, asked of the verb that knows.
#
# `claim` excludes a budget-stopped lane from its candidate set, in the same statement as its
# `FOR UPDATE SKIP LOCKED`, because that statement is the only thing that knows the lane it is
# about to hand out. Correct, and silent: an empty claim reads exactly like an empty queue, so a
# runner whose every lane is stopped heartbeats `idle` and looks healthy. The runner's own spend
# gate cannot fill the gap -- it is asked about the fleet and the agent, and at gate time the lane
# is not decided.
#
# It is a FLAG ON `claim` AND NOT A NEW VERB for two reasons that are both about not lying:
#
#   * the lane list and the host label used here are the ones the claim just used -- same
#     `agent_config` merge, same `--lane`/`--host` overrides -- so the explanation cannot be about
#     a different claim than the one that came back empty. A separate verb would recompute them
#     and could drift, which is the exact class of bug this task exists to close.
#   * `test-verbs.sh` asserts the CLI exposes EXACTLY sixteen verbs beyond the 40
#     (`VERB-PARITY.md`), so a seventeenth would be a contract change. A flag is not. (The count
#     was written as six here, was six when this decision was made, and has been stale four times
#     since: seven at task 0159, eight at 0280, corrected by 0317; then fourteen by 0442 and
#     sixteen when S2 added `observation` and `disposition` on 2026-09-01, and this line said
#     eight through both. Nothing checks this comment, which is why it drifts and why the count
#     is worth distrusting on sight. The ARGUMENT is unaffected by the number -- what makes a flag
#     cheaper than a verb is that no declaration has to be added anywhere, and that is true at any
#     count.)
#
# IT WRITES NOTHING. No `blocked_dispatch` incident, no thread row, no heartbeat. That is the
# whole reason 0122 left this undone: this path runs every `interval` seconds for every agent, and
# one stop that files an incident per poll becomes hundreds of rows, which is what the runner's
# edge-triggered spend gate exists to avoid. Two read-only queries on an empty claim, and only on
# an empty claim.
def _explain_empty_claim(args, lanes, host):
    """Print a reason for an empty claim, when there is one that is certainly true.

    Silence when the answer is "the queue is empty", because that is the runner's default reading
    already and saying it adds nothing. `EMPTY_REASON` is emitted under `--shell` on every path,
    including the empty one, so `eval` on a caller's side always defines it.
    """
    reason, off = "", []

    # The liveness gate first, because it is the LOUDER answer. A budget stop is a brake somebody
    # armed on purpose; a task withheld because two surfaces disagree about who owns it is an
    # anomaly, and `claim` skipping it in SQL is exactly the kind of correct-and-invisible that
    # let the 2026-08-18 incident run for five minutes before a human noticed.
    try:
        held = [r for r in reads.withheld_by_liveness(transitions.CLAIM_LIVENESS_SECONDS)
                if ("*" in lanes or r["lane"] in lanes) and (not r["host"] or r["host"] == host)]
    except Exception:                                            # noqa: BLE001
        held = []
    if held and not args.shell:
        for r in held:
            print(f"claim: {r['id']} is queued as {r['state']} with claimed_by="
                  f"{r['claimed_by'] or '(nobody)'}, but {r['live_agent']} heartbeat "
                  f"{render.dur(r['silent'])} ago saying it is WORKING it. Not handing it out: "
                  f"that is two agents on one task. Stop {r['live_agent']}, or wait "
                  f"{transitions.CLAIM_LIVENESS_SECONDS}s for its heartbeat to go stale.",
                  file=sys.stderr)

    # THE ACTOR GATE, second, because it is the QUIETEST answer of the three and the one an
    # agent is most likely to misread as an empty board. After the V1 cutover a terminal with
    # `lanes: ["*"]` polling live `brain` sees 24 rows in `inbox` and may have 15 of them: an
    # empty claim against a visibly full queue is exactly the state that gets "fixed" by
    # somebody widening a predicate. It is told, in words, that the rows it can see are not its.
    try:
        held = reads.held_from_the_fleet()
    except Exception:                                            # noqa: BLE001
        held = []
    held = [r for r in held if "*" in lanes or r["lane"] in lanes]
    if held and not args.shell:
        mine = sum(1 for r in held if r["operator_owned"])
        unclassified = [r["id"] for r in held if not r["operator_owned"]]
        print(f"claim: {len(held)} inbox row(s) in your lanes are HELD FROM THE FLEET "
              f"(agent_claimable=false) and were never candidates -- {mine} marked as the "
              f"operator's own work, {len(unclassified)} that nobody classified. This is a "
              f"guard, not an empty queue, and it is not something to route around: it is what "
              f"keeps the operator's own credential rotation out of a claim. It holds them "
              f"from `brain_runtime`, which is the login you are on -- not from every route a "
              f"process running as this Unix user has (store/SECRETS.md). "
              f"`swarm ls` marks them [OPERATOR].", file=sys.stderr)
        if unclassified:
            print(f"claim: unclassified and therefore held: {', '.join(unclassified[:12])}"
                  f"{' ...' if len(unclassified) > 12 else ''}. If any of those are FLEET work, "
                  f"whoever posted them omitted --for-agents and they will sit there forever.",
                  file=sys.stderr)

    stopped = reads.budget_stopped_lanes()
    if stopped:
        any_lane = "*" in lanes
        mine = [r for r in reads.queue_order(effective())
                if (any_lane or r["lane"] in lanes) and (not r["host"] or r["host"] == host)]
        with_work = {r["lane"] for r in mine}
        if any_lane:
            # An `*` agent claims from every lane there is, so the only measurable question is
            # whether every lane holding claimable work is stopped. An empty board is not a
            # budget answer and gets none.
            off = sorted(with_work & stopped)
            blocked = bool(with_work) and with_work <= stopped
        else:
            off = sorted(set(lanes) & stopped)
            blocked = all(l in stopped for l in lanes)
        if blocked:
            reason = "lane_budget"
    if args.shell:
        shell_export("CLAIM_", {"empty_reason": reason, "empty_lanes": ",".join(off)})
        return
    if not reason:
        return
    which = "every lane with claimable work" if "*" in lanes else "every lane you claim from"
    print(f"claim: nothing handed out, and {which} is budget-stopped ({', '.join(off)}). "
          f"This is a brake, not an empty queue. `budget check --lane {off[0]}` says why.",
          file=sys.stderr)


def cmd_claim(args):
    acfg = agent_config(args.agent)
    role = acfg.get("role", "terminal")
    # A PLANNER NEVER CLAIMS (D00 rule 5). Its lane list is empty, and an empty lane list matches
    # nothing. The runner also skips the claim call outright for a planner role. Two independent
    # gates, because the consequence of one being wrong is an admiral executing its own plan.
    if is_planner(role):
        lanes = []
    else:
        lanes = [l for l in (args.lane or "").split(",") if l] or acfg.get("lanes", ["*"])
    host = args.host or acfg.get("host", "")
    task = store.apply("claim", agent=args.agent, lanes=lanes, host=host,
                       weights=signal_weights(effective()), role=role)
    if not task:
        if getattr(args, "explain", False):
            _explain_empty_claim(args, lanes, host)
        return EMPTY
    info = {"id": task["id"], "title": task["title"], "lane": task["lane"],
            "host": task["host"], "workdir": task["workdir"], "attempts": task["attempts"],
            "max_attempts": task["max_attempts"], "external": task["external"],
            "canon_touching": task["canon_touching"], "gated": task["gated"]}
    if args.shell:
        shell_export("TASK_", info)
    elif args.json:
        out_json(info)
    else:
        print(task["id"])
    return OK


# ------------------------------------------------------------------ finishers

PRODUCED_WORDS = ("wrote", "created", "built", "generated", "exported", "committed", "produced",
                  "saved", "published", "deployed")


def cmd_done(args):
    try:
        r = store.apply("done", id=_tid(args.id), summary=args.summary, agent=args.agent,
                        force=args.force)
    except VerbError as e:
        die(str(e))
    summary = str(args.summary or "").lower()
    if not r["artifacts"] and (any(w in summary for w in PRODUCED_WORDS) or "/" in args.summary):
        # A nudge, never a block. An agent that cannot file its report is an agent whose work
        # is lost, so this goes to stderr and the verb still succeeds.
        print(f"swarm: {r['id']} finished with no artifacts recorded, and its summary says it "
              f"produced something. Record what it made: swarm artifact {r['id']} "
              f"/absolute/path --kind created --note \"...\"", file=sys.stderr)
    # `done` is the agent's report, not acceptance. Say so where it is read.
    print(f"{r['id']} done (reported, not accepted)")
    return OK


def cmd_block(args):
    try:
        r = store.apply("block", id=_tid(args.id), reason=args.reason, agent=args.agent,
                        unspent=args.unspent, force=args.force)
    except VerbError as e:
        die(str(e))
    print(f"{r['id']} blocked")
    # A REFUND THAT WENT UNSAID IS A REFUND NOBODY CAN AUDIT, and a refund that was REFUSED and
    # went unsaid is worse: the caller believes the ladder was reset and it was not. Both
    # branches print, and the refusal prints to stderr so `swarm-run`'s `recon` surfaces it.
    if getattr(args, "unspent", False):
        if r.get("refunded"):
            print(f"  attempt {r['was']} refunded -> {r['attempts']}/{r.get('max_attempts', '?')}"
                  f" (budget incident {r['incident']}). Still blocked: a human clears a "
                  f"budget stop, not the passage of time.")
        else:
            print(f"swarm: {r['id']} is blocked but its attempt was NOT refunded ({r.get('why')})."
                  f" attempts stays at {r.get('attempts')}. See the task thread for why.",
                  file=sys.stderr)
    return OK


def cmd_fail(args):
    try:
        r = store.apply("fail", id=_tid(args.id), reason=args.reason, agent=args.agent,
                        force=args.force)
    except VerbError as e:
        die(str(e))
    if r["requeued"]:
        print(f"{r['id']} requeued, attempt {r['attempts']}/{r['max_attempts']}")
    else:
        print(f"{r['id']} blocked after {r['attempts']} attempts, raised {r['question']}")
    return OK


def cmd_reopen(args):
    try:
        r = store.apply("reopen", id=_tid(args.id), reason=args.reason, agent=args.agent,
                        force=args.force)
    except VerbError as e:
        die(str(e))
    print(f"{r['id']} reopened from {r['was']}, attempts reset to 0")
    return OK


def cmd_cancel(args):
    try:
        r = store.apply("cancel", id=_tid(args.id), reason=args.reason, agent=args.agent,
                        force=args.force)
    except VerbError as e:
        die(str(e))
    print(f"{r['id']} cancelled")
    if r.get("withdrew"):
        # Said out loud, because it is a write on the OPERATOR'S queue made as a side effect of
        # someone else's verb. Silent would be the wrong kind of tidy (task 0159).
        print(f"{len(r['withdrew'])} open question(s) withdrawn with it, NOT answered: "
              f"{', '.join(r['withdrew'])}", file=sys.stderr)
    if r.get("left_standing"):
        # The cancel happened; the cascade could not. Louder than the success line, because the
        # thing left behind is an entry in the operator's own queue pointing at a task that no
        # longer exists, which is the whole defect this task was posted about.
        print(f"swarm: {len(r['left_standing'])} open question(s) are STILL IN THE OPERATOR'S "
              f"QUEUE: {', '.join(r['left_standing'])}. This store is below migration 31, so no "
              f"withdrawal can be recorded in it and the cancel could not take them with it. See "
              f"the thread note on {r['id']} and migrations/0031_question_withdrawal.sql.",
              file=sys.stderr)
    return OK


# ------------------------------------------------------------------ questions

def cmd_ask(args):
    try:
        r = store.apply("ask", question=args.question, agent=args.agent, task=args.task,
                        default=args.default)
    except VerbError as e:
        die(str(e))
    print(r["id"])
    if r["task"]:
        print(f"{r['task']} blocked on {r['id']}", file=sys.stderr)
    _page(r["id"], args.question, r["task"])
    return OK


def _page(qid, text, task):
    """The line for the agent that is standing right here. The human's page is not sent from here.

    DECIDED: one path, and it is not a second notification system. This prints ONE line on
    stderr, for the terminal that just ran the verb, and that is all it has ever done.

    WIRED 2026-08-16 (task 0140), and the wiring is deliberately not in this function. The
    transport is D5's `event emit`, and the call site is `store.transitions.after_commit`, at the
    narrow waist: `store.apply("ask", ...)` emits `question.raised` after it commits, so the MCP
    server, the console and the question `fail` raises for itself when a task runs out of
    attempts all page too. Putting the call here would have paged the CLI's questions and
    silently not the other three, which is the same seam one layer up.

    What it looked like before it was wired, since the reasoning still holds: a question raised
    at 02:00 sat until the 07:00 brief, because doctor's question-age warning is 12 hours and
    `swarm questions` needs a human to open it.
    """
    print(f"swarm: OPERATOR QUESTION {qid}"
          f"{' on ' + task if task else ''}: {render.one_line(text, 120)}", file=sys.stderr)


def cmd_answer(args):
    text = " ".join(args.text) if args.text else ""
    if args.file:
        text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    if not text.strip():
        die("an empty answer is not an answer. Pass the text, or -f - to read stdin.")
    planners = [a.get("name") for a in effective().get("agents", [])
                if is_planner(a.get("role", "")) and a.get("name")]
    try:
        r = store.apply("answer", qid=args.qid, text=text, amend=args.amend, planners=planners,
                        requeue=args.requeue)
    except VerbError as e:
        die(str(e))
    print(f"{r['id']} {'amended' if r['amended'] else 'answered'}"
          f"{', told ' + ', '.join(r['told']) if r['told'] else ''}")
    if r["requeue_refused"]:
        # The 2026-08-16 incident, refused rather than repeated.
        # Exit 0, loudly. THE ANSWER LANDED -- only the requeue was withheld. Returning an
        # error here would tell a script the answer failed and invite it to answer again, which
        # is a worse outcome than the one this guard exists to prevent.
        print(f"swarm: {r['task']} NOT requeued: {r['requeue_refused']} is still heartbeating on "
              f"it. Requeuing behind a live engine is how one task gets two agents. Stop that "
              f"engine, then: swarm answer-requeue {r['task']}", file=sys.stderr)
        return OK
    if r["requeued"]:
        print(f"{r['task']} requeued")
    return OK


def cmd_withdraw(args):
    """Take a question out of the operator's queue without answering it. NOT one of the 40.

    Declared in engine/VERB-PARITY.md as the seventh addition, and task 0159 is the measurement:
    an agent whose question stopped mattering had exactly one way to clear the row it left in
    front of a human, and that way was `answer`, which writes the identity `operator`. So the
    fleet's choices were to forge an answer or to leave the question standing. It left them
    standing, and on 2026-08-18 the operator spent a decision on q0140 -- a question raised by a
    test about a branch that does not exist, on a task cancelled four and a half hours earlier.

    The door check is `--from`, and it is required rather than defaulted for the reason
    `transitions.withdraw` gives: a withdrawal nobody signed is the unattributed write this verb
    was built to replace.
    """
    try:
        r = store.apply("withdraw", qid=args.qid, agent=args.agent, reason=args.reason)
    except VerbError as e:
        die(str(e))
    print(f"{r['id']} withdrawn by {r['withdrawn_by']}, NOT answered")
    if r["task"]:
        print(f"swarm: it was {r['asked_by'] or 'an agent'}'s question on {r['task']}. The task's "
              f"state is unchanged: a withdrawal does not requeue anything.", file=sys.stderr)
    return OK


def cmd_answer_requeue(args):
    """Requeue a task whose question is answered, after its engine has been stopped.

    Split out of `answer` on purpose. The requeue is the dangerous half and it needs a human or
    a runner to assert that the process is gone, which is a fact neither the bus nor a
    transaction can observe.
    """
    tid = _tid(args.id)
    t = reads.task(tid)
    if not t:
        die(f"no such task: {tid}")
    if t["state"] != "blocked":
        die(f"{tid} is {t['state']}, not blocked. Nothing to requeue.", EMPTY)
    r = store.apply("reopen", id=tid, reason=f"question answered, engine confirmed stopped by "
                                             f"{args.agent or 'operator'}", agent=args.agent)
    print(f"{r['id']} requeued")
    return OK


def cmd_release(args):
    """Put a task back ONLY if this agent still holds it.

    NOT one of the 40. Declared as a deviation in engine/VERB-PARITY.md, and it is here because
    the commander's 2026-08-16 incident requires it: the runner's shutdown trap used to call
    `fail` unconditionally, so killing one agent's window released a task the store had already
    handed to a different agent. `fail` cannot be made conditional without changing what `fail`
    means, so the conditional release is its own verb.
    """
    r = store.apply("release", id=_tid(args.id), agent=args.agent, reason=args.reason)
    if args.json:
        out_json(r)
    elif r["released"]:
        print(f"{r['id']} released")
    else:
        print(f"swarm: {r['id']} NOT released: the store says {r['owner'] or '(nobody)'} holds it, "
              f"not {args.agent}. A dying agent does not unclaim a live one's task.",
              file=sys.stderr)
    return OK if r["released"] else EMPTY


def cmd_run_start(args):
    """Open the run row for one attempt, and PRINT ITS ID. NOT one of the 40; see VERB-PARITY.md.

    The bare id on stdout is the whole point of this verb having output at all. A budget stop is
    matched back to its run on run_id OR session_id (`budget/halt.py`), and the session id does
    not exist until the engine announces it -- so this is the only key available at launch, and
    the only key at all on an engine that writes no stream-json. `swarm-run` captures it with
    command substitution and hands it to `budget guard --run` and `budget halt --run`.
    """
    r = store.apply("run start", id=_tid(args.id), attempt=args.attempt, agent=args.agent,
                    pid=args.pid, host=args.host or "", session_id=args.session or "",
                    stream_pointer=args.stream, stream_pointer_host=args.host or "")
    if args.json:
        out_json(r)
    elif r.get("run_id") is not None:
        print(r["run_id"])
    return OK


def cmd_run_end(args):
    """Close the run row, with the stream's size and hash. NOT one of the 40."""
    sha = size = None
    if args.stream and Path(args.stream).exists():
        import hashlib
        data = Path(args.stream).read_bytes()
        sha, size = hashlib.sha256(data).hexdigest(), len(data)
    store.apply("run end", id=_tid(args.id), attempt=args.attempt, exit_code=args.exit_code,
                outcome=args.outcome, stream_sha256=sha, stream_bytes=size)
    return OK


def cmd_questions(args):
    rows = reads.questions(answered=args.answered, since_hours=args.since,
                           withdrawn=args.withdrawn)
    if args.json:
        out_json(rows)
        return OK if rows else EMPTY
    if not rows:
        print("no answered questions in the window" if args.answered
              else "no withdrawn questions in the window" if args.withdrawn
              else "no open questions")
        return EMPTY
    for q in rows:
        head = f"{q['id']}  {q['asked_by']}"
        if q["work_item_id"]:
            head += f"  on {q['work_item_id']}"
        print(head)
        print(f"  Q: {render.one_line(q['text'], 140)}")
        if q["default_if_unanswered"]:
            print(f"  default if unanswered: {render.one_line(q['default_if_unanswered'], 120)}")
        if q["answer"]:
            print(f"  A: {render.one_line(q['answer'], 140)}")
        if q.get("withdrawn_at"):
            # Never rendered as an answer, whatever else changes here. The whole verb exists so
            # that "nobody decided this" and "the operator decided this" stay two different
            # sentences on the same screen.
            print(f"  WITHDRAWN by {q['withdrawn_by']}: "
                  f"{render.one_line(q['withdrawn_reason'] or 'no reason recorded', 120)}")
        if q["amended_from"]:
            print(f"  (amended, was: {render.one_line(q['amended_from'], 100)})")
    return OK


# ------------------------------------------------------------------ objectives

def cmd_objectives(args):
    rows = reads.objectives(state="inbox")
    if args.json:
        out_json(rows)
        return OK if rows else EMPTY
    if not rows:
        print("no objectives waiting")
        return EMPTY
    for o in rows:
        print(f"{o['name']}  ({o['bytes'] or 0} bytes, taken in {render.short(o['taken_in_at'])})")
    return OK


def cmd_accept(args):
    """Accept an OBJECTIVE. Finished WORK is `swarm accept-work` (task 0141)."""
    try:
        r = store.apply("accept", name=args.name)
    except VerbError as e:
        # The wrong-door case, and it used to read like a missing task. D9 (task 0118) ran
        # `swarm accept 0027` against a real, finished, unaccepted item and got "no objective in
        # inbox named: 0027" -- true, and the wrong answer to the question being asked, because
        # this is the objectives verb and `accept work` had no CLI door at all. The door exists
        # now, so the refusal names it rather than leaving the operator to conclude the store
        # lost his task.
        tid = norm_task_id(args.name)
        if tid and reads.task(tid):
            die(f"{args.name} is a work item, not an objective. `accept` moves an OBJECTIVE out "
                f"of the admiral's inbox. Accepting finished work is the separate human act D00 "
                f"rule 3 keeps apart from the agent's `done`: swarm accept-work {tid}")
        die(str(e))
    print(f"{r['name']} accepted")
    return OK


#: A dropped file may declare who wrote it, in a YAML front matter block at the very top:
#:
#:     ---
#:     origin: machine
#:     ---
#:
#: ONE DROP FOLDER HOLDS BOTH KINDS, WHICH IS WHY A FLAG ALONE IS NOT ENOUGH. `var/intake` is
#: swept every five minutes by `brain-intake-sweep.timer`, and on 2026-08-31 it held two files the
#: n8n heartbeat wrote and one a human put there. A `--origin` on the sweep would have to call all
#: three the same thing. So the per-file declaration exists and it OVERRIDES the sweep's default.
#:
#: IT IS A DECLARATION AND NOT A NAME MATCH. The difference is who is speaking: the writer of the
#: file says what it is, in a machine-readable block it had to put there on purpose. A lane
#: correctly refused to infer the same fact from `source_name LIKE 'intake-heartbeat-%'` in the
#: render layer, and this is not that: nothing here reads the name.
_FRONT_MATTER_KEY = "origin:"


def _declared_origin(text: str):
    """Read `origin:` out of a leading `---` front matter block. Anything else is undeclared.

    DELIBERATELY THE SMALLEST POSSIBLE PARSER, and no YAML library. The whole vocabulary is two
    words on one key, the block has to be the first thing in the file, and a parser that accepted
    more would be a parser that could be surprised. Anything it cannot read returns None, which
    means undeclared, which reads as human, which surfaces. There is no input to this function
    that can make a row quiet by accident.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:12]:                       # a front matter block, not a document
        if line.strip() == "---":
            return None
        if line.strip().lower().startswith(_FRONT_MATTER_KEY):
            return line.split(":", 1)[1].strip().lower() or None
    return None


def cmd_intake(args):
    src = Path(args.source).expanduser()
    if not src.is_dir():
        die(f"no such source folder: {src}")
    taken, machine = [], 0
    for f in sorted(src.glob("*.md")):
        st = f.stat()
        sig = f"{st.st_size}:{int(st.st_mtime)}"
        body = f.read_text(encoding="utf-8", errors="replace")
        # THE FILE'S OWN DECLARATION WINS OVER THE SWEEP'S DEFAULT, because the file is closer to
        # the writer. `--origin` is for a folder only one producer writes into; the front matter
        # is for the folder that already exists, which several do.
        origin = _declared_origin(body) or (args.origin or None)
        r = store.apply("intake", name=f.stem, body=body,
                        source_name=f.name, source_signature=sig, bytes_=st.st_size,
                        origin=origin)
        if r["taken"]:
            taken.append(r["name"])
            if r.get("origin") == "machine":
                machine += 1
    if not args.quiet:
        # THE DENOMINATOR IS PRINTED. "took in 3" says nothing about how many of them anybody is
        # waiting on, and this sweep's whole reason for declaring an origin is that those are
        # different numbers.
        note = f" ({machine} machine, {len(taken) - machine} waiting on a human)" if taken else ""
        print(f"took in {len(taken)}{note}" + (f": {', '.join(taken)}" if taken else ""))
    return OK if taken else EMPTY


# ------------------------------------------------------------------ threads and mail

def _held_while_active_banner(tid):
    """Tell a WORKING agent, on its own next bus call, that its task is now held. Task 0119.

    The only channel that reaches a model mid-run. A terminal's prompt is built once at dispatch
    and `bin/swarm-run` reads `swarm inbox` on a PLANNER wake only, so a `msg` to a running
    terminal is deliverable and not delivered. The stdout of the verbs the agent calls ITSELF is
    read, as a tool result -- `note` and `artifact` are the two it calls repeatedly while working.

    Opportunistic, and said plainly rather than sold as a recall: it arrives on the agent's next
    bus call, which through a long silent stretch of work is not soon enough. `cancel --force`
    is what stops a run now.
    """
    try:
        t = reads.task(tid)
    except Exception:                                            # noqa: BLE001
        return                                   # a banner is never worth failing the verb over
    if not reads.held_and_active(t):
        return
    print(f"!! {tid} IS NOW HELD FOR THE OPERATOR while you are executing it "
          f"(agent_claimable=false, claimed_by {t['claimed_by']}).")
    print(f"!! Nothing recalled your claim and nothing stopped your run. Treat it as HELD: stage "
          f"your work, apply")
    print(f"!! nothing irreversible or client-visible, and report what you have. If you have "
          f"ALREADY applied something,")
    print(f"!! say so on this task's thread now: `swarm note {tid} \"...\" --from <you>`.")


def cmd_note(args):
    tid = _tid(args.id)
    try:
        store.apply("note", id=tid, text=args.text, agent=args.agent)
    except VerbError as e:
        die(str(e))
    print("noted")
    _held_while_active_banner(tid)
    return OK


def cmd_msg(args):
    try:
        store.apply("msg", text=args.text, agent=args.agent, to=args.to, task=args.task)
    except VerbError as e:
        die(str(e))
    print(f"sent to {args.to}")
    return OK


def cmd_inbox(args):
    rows, read_seq = reads.messages(args.agent)
    unread = [m for m in rows if m["seq"] > read_seq]
    if args.json:
        out_json({"messages": rows, "read_seq": read_seq, "unread": len(unread)})
    else:
        if not rows:
            print(f"no messages for {args.agent}")
        for m in rows:
            mark = "*" if m["seq"] > read_seq else " "
            tag = f" [{m['work_item_id']}]" if m["work_item_id"] else ""
            print(f"{mark} {render.short(m['ts'])}  {m['from_agent']}{tag}: "
                  f"{render.one_line(m['text'], 120)}")
    if args.mark_read:
        store.apply("inbox mark-read", agent=args.agent)
    return OK if rows else EMPTY


# ------------------------------------------------------------------ artifacts

def cmd_artifact(args):
    raw = args.path
    kind = args.kind
    if kind not in ARTIFACT_KINDS:
        die(f"--kind must be one of {', '.join(ARTIFACT_KINDS)}")
    # `external` names something that is not a file (a deployed page, a sent message), so it is
    # not resolved or stat'ed. Everything else is made absolute against the caller's cwd.
    if kind == "external":
        path, exists = raw, True
    else:
        p = Path(raw).expanduser()
        path = str(p if p.is_absolute() else (Path.cwd() / p).resolve())
        exists = Path(path).exists()
    try:
        r = store.apply("artifact", id=_tid(args.id), path=path, kind=kind, note=args.note,
                        agent=args.agent, exists=exists)
    except VerbError as e:
        die(str(e))
    print(f"recorded {kind}: {path}" + ("" if exists else "   [MISSING at record time]"))
    _held_while_active_banner(r["id"] if isinstance(r, dict) and r.get("id") else _tid(args.id))
    return OK


def cmd_artifacts(args):
    kinds = tuple(k for k in (args.kind or "").split(",") if k)
    rows = reads.artifacts(task_id=_tid(args.task) if args.task else None,
                           since_hours=args.since, agent=args.agent, kinds=kinds)
    if args.json:
        out_json(rows)
        return OK if rows else EMPTY
    if not rows:
        print("no artifacts recorded")
        return EMPTY
    cur = None
    missing = 0
    for a in rows:
        if a["work_item_id"] != cur:
            cur = a["work_item_id"]
            print(f"\n{cur}")
        gone = not Path(a["path"]).exists() if a["kind"] != "external" else False
        missing += 1 if gone else 0
        flag = "  MISSING" if gone else ""
        note = f"  -- {render.one_line(a['note'], 60)}" if a["note"] else ""
        print(f"  {a['kind']:<9} {a['path']}{note}{flag}")
    if missing:
        # Re-stat at read time is the cross-check: a path claimed and never written is a
        # finding, not a formatting problem.
        print(f"\n{missing} recorded path(s) are not on disk now.", file=sys.stderr)
    return OK


# ------------------------------------------------------------- orient and decide
#
# Orient and Decide are two of OODA's four stages and until 2026-09-01 neither had a surface.
# `observation open`, `observation close` and `disposition record` were registered transitions
# reachable ONLY by importing `fabric.emit` and calling `store.apply` from Python, which is why
# `brain.observation` held five rows, all `kind='demo'`, all written on 2026-08-16 by a lane
# doing exactly that import, and nothing since. A verb no primary user can type is not a verb.
#
# THE RULING WAS A CLI AND NOT A CONSOLE ROOM, and the reason is worth keeping: the console
# needs the verbs a HUMAN would use, while the CLI is what agents use, and agents are the
# primary users here. `web/rooms.py:55` already comments its allowlist as the human-actor verb
# set and names the agent verbs it excludes on purpose. So these three land here and the
# console's one-write-door property is not touched.
#
# This file still owns no state transition. Each verb below is one `store.apply` call, the same
# call `fabric/cli.py`, the runner and any future MCP wrapper make. The transitions live in
# `fabric/emit.py` and register lazily through `store.transitions._load_hooks()`, which is why
# nothing here imports fabric: by the time `apply` runs, the verb is in the registry.


def _require_event(seq):
    """Refuse a dangling reference with a sentence instead of a foreign-key traceback.

    The FK would catch this anyway, and on `disposition` it is ON DELETE RESTRICT so it catches
    it hard. The pre-check is not the guard; it is the error message. A walker that mistypes an
    event sequence should be told the event does not exist, not shown a constraint name.
    """
    with store.read("runtime") as rs:
        ev = rs.one("SELECT event_seq, type, occurred_at FROM brain.event WHERE event_seq = %s",
                    (seq,))
    if ev is None:
        die(f"no event with sequence {seq}. `fabric show <seq>` reads one; an observation "
            f"has to be an observation OF something.")
    return ev


def _producer_stamp(name):
    """Migration 17's shape, and the reason it is not `produced_by`.

    A CLI caller is a producer NAME, not an entity id. Since migration 17 `produced_by IS NOT
    NULL` means "an entity id" unconditionally, because that is the only rule a lineage walker
    can follow that is right on both the nine resolvable rows and the 2,269 that resolve to
    nothing. So the name lands in `produced_by_producer`; `produced_by`, `produced_by_ref` and
    `resolution_status` stay NULL, which is migration 5's fourth state and is the truth here:
    nobody attempted a resolution, and there was no reference to preserve.
    """
    return {"produced_by": None, "produced_by_producer": (name or None),
            "produced_by_ref": None, "resolution_status": None}


def cmd_observation_open(args):
    ev = _require_event(args.event_seq)
    if not str(args.text or "").strip():
        die("an observation with no text observes nothing. Pass --text.")
    oid = store.apply("observation open", event_id=args.event_seq,
                      subject_type=args.subject_type or "",
                      subject_id=args.subject_id or "",
                      kind=args.kind or "", text=args.text,
                      actor_type=args.actor_type,
                      actor=args.producer or "",
                      **_producer_stamp(args.producer))
    if args.json:
        out_json({"observation_id": oid, "event_seq": args.event_seq, "status": "open",
                  "kind": args.kind or "", "produced_by_producer": args.producer or None})
        return OK
    print(f"observation {oid} opened against event {args.event_seq} "
          f"({ev['type']}), status open")
    return OK


def cmd_observation_close(args):
    with store.read("runtime") as rs:
        row = rs.one("SELECT id, status FROM brain.observation WHERE id = %s", (args.id,))
    if row is None:
        die(f"no observation {args.id}")
    if row["status"] == "closed":
        # Not an error and not a silent success. Closing a closed observation changes nothing,
        # and exit 2 is this repo's "nothing to do" code.
        print(f"observation {args.id} is already closed", file=sys.stderr)
        return EMPTY
    r = store.apply("observation close", observation_id=args.id, actor=args.producer or "")
    if args.json:
        out_json(r)
        return OK
    print(f"observation {r['id']} closed at {r['closed_at']}")
    return OK


def cmd_disposition_record(args):
    ev = _require_event(args.event_seq)
    if args.observation_id is not None:
        with store.read("runtime") as rs:
            o = rs.one("SELECT id, event_id FROM brain.observation WHERE id = %s",
                       (args.observation_id,))
        if o is None:
            die(f"no observation {args.observation_id}")
        # EF-3 lets a disposition answer an occurrence with no standing observation, but one
        # that names an observation belonging to a DIFFERENT event is almost always a typo, and
        # it would put a kink in the lineage chain that reads as real. Refuse it here; the
        # schema cannot, because both FKs are satisfied.
        if o["event_id"] is not None and o["event_id"] != args.event_seq:
            die(f"observation {args.observation_id} was opened against event {o['event_id']}, "
                f"not {args.event_seq}. A disposition that cites both has to agree with itself.")
    did = store.apply("disposition record", event_id=args.event_seq, verdict=args.verdict,
                      rationale=args.rationale or "",
                      observation_id=args.observation_id,
                      decided_by=args.by or "",
                      actor_type=args.actor_type,
                      actor=args.producer or args.by or "",
                      **_producer_stamp(args.producer))
    if args.json:
        out_json({"disposition_id": did, "event_seq": args.event_seq,
                  "observation_id": args.observation_id, "verdict": args.verdict,
                  "decided_by": args.by or ""})
        return OK
    print(f"disposition {did} recorded on event {args.event_seq} ({ev['type']}): "
          f"{args.verdict}"
          + (f", answering observation {args.observation_id}" if args.observation_id else
             ", answering no standing observation (EF-3 allows it)"))
    return OK


# ------------------------------------------------------------------ agents

def cmd_heartbeat(args):
    acfg = agent_config(args.agent)
    store.apply("heartbeat", agent=args.agent, status=args.status, task=args.task,
                pid=args.pid or os.getppid(), host=args.host or acfg.get("host", ""),
                role=acfg.get("role", ""))
    return OK


def cmd_reap(args):
    r = store.apply("reap", stale_seconds=args.stale_seconds, act=args.yes)
    if args.json:
        out_json(r)
        return OK if r["found"] else EMPTY
    if not r["found"]:
        print("nothing to reap")
        return EMPTY
    for f in r["found"]:
        print(f"{f['id']}  held by {f['claimed_by']}, silent {render.dur(f['silent'])}"
              + ("  -> requeued" if f["id"] in r["requeued"] else ""))
    if not args.yes:
        print("\nnothing was changed. Re-run with --yes to requeue.")
    return OK


def cmd_paused(args):
    """May this agent work? 0 stop, 2 carry on. The exit code IS the answer."""
    if reads.fleet_paused():
        print("fleet paused")
        return OK
    if args.agent and reads.agent_stopped(args.agent):
        print(f"{args.agent} stopped")
        return OK
    return EMPTY


def _who(args=None):
    """Who is running this verb. What the caller said, then the agent's own name, then the human.

    `SWARM_AGENT` is exported into every terminal by the runner, so an agent that stops a peer
    signs its own name rather than the login the whole fleet shares. Without this every stop on
    this host would be signed `you` and the trail would answer "which machine" instead of
    "who decided".

    THE `$USER` TAIL IS GONE FROM HERE TOO. Bus row `0419`, and it is the second half of lane E's
    row 0384 rather than a new finding. That row removed `$USER` from `accept work`, wrote the
    rule at `cmd_accept_work` below in the words "IT IS GONE", and left this function ending
    `or os.environ.get("USER") or "operator"`, which signs `start`, `stop`, `pause` and `resume`.
    So the forgery generator was gone from the verb that records decisions and live on the four
    that record fleet control. MEASURED read-only on 2026-08-28, which is what the row is filed
    on rather than a reading of the code:

        brain        brain.message  kind stop   from_agent `you`   3 rows
        brain_demo   brain.message  kind start  from_agent `you`   1 row
                     brain.message  kind stop   from_agent `operator`   1 row

    Two names for the same person inside one session on the demo store, and `you` is nobody:
    not a human in `brain.human_role`, not an agent in `brain.agent`. The tail is replaced by
    `store.human_slug()`, the one producer `accept work` resolves through, which reads
    `$BRAIN_HUMAN` and defaults to `operator`.

    WHAT THIS IS AND IS NOT, because the difference from `accept work` is real and stating it is
    the point of the row. There, `human_slug()` selects a CREDENTIAL and the database then refuses
    the write if the process does not hold it, so the name is established. These four verbs run as
    `brain_runtime` and write `brain.agent` and `brain.runtime_flag`, where no such gate exists, so
    what this returns is a REQUEST and nothing here can make it a fact. That is tolerable because
    these are operational acts and not decisions of record, and the row says so in as many words.
    What is fixed is that the request can no longer be a login name that belongs to no one; what
    is NOT fixed is that `--by` is still a string any caller may choose, which is unchanged and
    deliberate: an agent stopping a peer has to be able to sign its own name.
    """
    return (getattr(args, "by", "") or os.environ.get("SWARM_AGENT")
            or store.human_slug())


def cmd_stop(args):
    """Stop one agent, on the record. A stop with no reason is refused, not defaulted.

    Task 0273. Before this, `stop` wrote `brain.agent.stopped_at` and emitted nothing at all, so
    a commander's deliberate stop and an agent that had silently died were the same two facts on
    the board: a STOPPED flag and no explanation. At 00:55Z on 2026-08-19 the commander stopped
    T4 and T5 on a rate-limit decision; at 00:56Z an admiral pass could not tell that from a
    silent failure and restarted both; at 01:0xZ it read the commander's answer and stopped them
    again. Nothing in that sequence was a mistake except the verb that left no trail.

    `--reason` is `required=True` in the parser and re-checked in the transition, so neither the
    CLI nor another surface can file a reasonless stop.
    """
    try:
        r = store.apply("stop", agent=args.agent, by=_who(args), reason=args.reason)
    except VerbError as e:
        die(str(e))
    print(f"{args.agent} stopped by {r['by']}: {r['reason']}"
          + (f" (was holding {r['held']})" if r["held"] else ""))
    if not r["known"]:
        # The same phantom `start` was fixed for by task 0253, reported rather than fixed here:
        # changing it is a lifecycle change and this task's constraint forbids one. Task 0282.
        print(f"swarm: {args.agent} had no agent row before this. `stop` CREATED one, so a "
              f"mistyped name now shows in `swarm status` as a stopped terminal that never "
              f"existed. Check the name.", file=sys.stderr)
    if r["was_already_stopped"]:
        print(f"swarm: {args.agent} was already stopped. The stamp and the reason are now "
              f"this one's; the earlier record is still in `swarm feed`.", file=sys.stderr)
    return OK


def cmd_start(args):
    """Release one stopped agent. Says which of the three things happened, and exits it.

    0 it lifted a stop, 2 the agent was already running so there was nothing to lift, 1 there is
    no agent by that name. Before task 0253 all three printed `X started` and exited 0, which is
    the failure this verb is least able to afford: the operator runs `start` precisely when he
    believes an agent is stuck, reads the line, and stops looking. On 2026-08-18 that cost two
    terminals about six minutes, because a `start` that had not released anything was
    indistinguishable from one that had.
    """
    r = store.apply("start", agent=args.agent, by=_who(args),
                    reason=getattr(args, "reason", "") or "")
    if r["released"]:
        # Task 0273 added the signature. The line still begins `{agent} started`, because that
        # prefix is what an operator and `test_start_is_not_silent` both read it by.
        print(f"{args.agent} started by {r['by']}"
              + (f": {r['reason']}" if r["reason"] else ""))
        return OK
    if r["known"]:
        print(f"{args.agent} was not stopped. Nothing changed.")
        return EMPTY
    print(f"no agent named {args.agent}. Nothing changed; check the name against `swarm status`.")
    return ERR


def cmd_pause(args):
    """Pause the whole fleet, signed. `--reason` is optional here and required on `stop`.

    The asymmetry is deliberate and it is about who can tell. A fleet pause is visible to every
    agent at once and to the operator on the first line of `swarm status`; an agent stop is one
    flag on one row that looks exactly like that agent having died. The ambiguity `stop`'s
    required reason exists to close does not exist here.

    `brain.runtime_flag` has carried `set_by`, `set_at` and `note` since migration 1 and `pause`
    has always written the first. Task 0273 is what makes anything READ them: `swarm status` now
    prints who paused the fleet and why. A provenance column no surface displays is the same
    absence as no column at all.
    """
    r = store.apply("pause", by=_who(args), note=getattr(args, "reason", "") or "")
    print(f"fleet paused by {r['by']}" + (f": {r['note']}" if r["note"] else ""))
    return OK


def cmd_resume(args):
    """Resume the fleet, signed.

    `by` used to be left at the transition's `operator` default no matter who ran it, so the one
    provenance column this pair already had recorded the actor on pause and lost it on resume.
    """
    r = store.apply("resume", by=_who(args), note=getattr(args, "reason", "") or "")
    print(f"fleet resumed by {r['by']}" + (f": {r['note']}" if r["note"] else ""))
    return OK


def cmd_accept_work(args):
    """Acceptance, the human's act, on the operator's own CLI. Task 0141.

    Before this the verb D00 rule 3 makes load-bearing -- acceptance is separate from the agent's
    `done`, and `reopen` is the rejection -- had exactly ONE door: a Flask development server on
    loopback (`web/actions.py:98`). The operator's own CLI could not reach it, and `swarm accept`
    is the objectives verb, so the obvious command answered a task id with "no objective in inbox
    named: 0027". A load-bearing act with one door is one crashed dev server away from having
    none.

    It is a separate subcommand rather than a smarter `accept` on purpose. `web/MUST-NOT-BUILD.md`
    item 1 states the rule the console follows and it is the right rule here: the label names the
    verb that will run. A command that infers which of two state machines you meant from the shape
    of its argument is a command that guesses wrong the day an objective is named `0027`.

    **The gate here, and what it does not do, stated plainly.** This refuses to run at all inside
    a fleet terminal, read off the same two variables `transitions._caller` reads, because on the
    CLI those variables mean the hands on the keyboard belong to an agent. It lives at the door
    rather than in the verb: a long-running console started FROM a terminal shell inherits
    `SWARM_PARENT_TASK` and would be refused forever by an env check inside the transition, and
    D9 started the console from exactly such a shell. The transition's own guard is the one that
    cannot be shed -- it refuses any acceptor registered in `brain.agent` -- so an agent that
    unsets the variables is still caught by the name it works under. An agent that unsets them
    AND passes `--by operator` is not caught by either, and nothing in one process can catch it;
    what both gates remove is every convenient path, which is the same claim `store/narrow_waist.md`
    makes for the waist itself.
    """
    fleet = os.environ.get("SWARM_AGENT", "").strip()
    parent = os.environ.get("SWARM_PARENT_TASK", "").strip()
    if fleet or parent:
        die(f"refusing to accept from a fleet terminal "
            f"(SWARM_AGENT={fleet or '(unset)'} SWARM_PARENT_TASK={parent or '(unset)'}). "
            f"`done` is the agent's report that it finished. Acceptance is the separate act that "
            f"says a human read it, and the decider on every acceptance is a human (D00 rules 3 "
            f"and 4). Run this from your own shell, or accept it in the console.", code=6)
    # `$USER` WAS A FORGERY GENERATOR AND IT IS GONE. Lane E, row 0384. On this host `$USER` is
    # `you`, which is nobody: not a human in `brain.human_role`, not an agent in
    # `brain.agent`, so it passed every test this verb had and landed as `accepted_by='you'`
    # on the operator's own work. The default is now the human this PROCESS is, resolved by
    # `store.human_slug()` from `$BRAIN_HUMAN` and defaulting to `operator`; the database then
    # refuses it if the process does not hold that credential. A name that resolves from the
    # shell's login name is the same class of answer as `--by` was before task 0141.
    #
    # AND IT WAS NOT GONE FROM THIS FILE when that paragraph was written, which is bus row `0419`:
    # `_who` above signed `start`, `stop`, `pause` and `resume` off the same variable for another
    # day, and `cmd_auto_accept` below signed the fleet-wide auto-accept flag off it. Both are
    # repaired now and each says so where it stands. The sentence above is true of this verb and
    # was never true of the file; a rule stated in one function is not enforced in the next one.
    by = args.by.strip() or store.human_slug()
    r = store.apply("accept work", id=_tid(args.id), by=by, as_operator=True)
    if args.json:
        out_json(r)
        return OK
    print(f"{r['id']} accepted by {r['accepted_by']}")
    return OK


def cmd_unaccept_work(args):
    """Withdraw an acceptance, on the operator's own CLI. Bus row 0413.

    It is a separate subcommand and not `accept-work --undo`, for the reason
    `web/MUST-NOT-BUILD.md` item 1 gives and `cmd_accept_work` restates: the label names the verb
    that will run. A flag that flips a command into its own inverse is a command whose name says
    `accept` while it unaccepts, which is precisely the class of guess item 1 exists against.

    The fleet-terminal refusal above it is the SAME gate `accept work` carries and it is here for
    the same reason: on the CLI, `SWARM_AGENT` or `SWARM_PARENT_TASK` means the hands on the
    keyboard belong to an agent, and withdrawing a human's decision is no more an agent's act than
    making one. It lives at the door rather than in the verb because a long-running console
    started FROM a fleet shell inherits those variables and an env check inside the transition
    would refuse it forever. The transition's own gate, `brain.current_human()`, is the one that
    cannot be shed.
    """
    fleet = os.environ.get("SWARM_AGENT", "").strip()
    parent = os.environ.get("SWARM_PARENT_TASK", "").strip()
    if fleet or parent:
        die(f"refusing to withdraw an acceptance from a fleet terminal "
            f"(SWARM_AGENT={fleet or '(unset)'} SWARM_PARENT_TASK={parent or '(unset)'}). "
            f"Acceptance is the act that says a human read the report, and withdrawing one is a "
            f"decision of record too. Run this from your own shell, or use the console.", code=6)
    r = store.apply("unaccept work", id=_tid(args.id), reason=args.reason,
                    by=args.by.strip(), as_operator=True)
    if args.json:
        out_json(r)
        return OK
    # It says what the row IS now, not just what happened, because "nothing happened when I
    # accepted work" is the complaint this whole row was filed on. `Accept work` is the control
    # the item carries again, named here in the same words the console uses for it.
    print(f"{r['id']} unaccepted by {r['unaccepted_by']}: {r['reason']}")
    print(f"  it was accepted by {r['was_accepted_by']} at {render.short(r['was_accepted_at'])}. "
          f"The work is untouched and still {r['state']}; the item is back in the queue with "
          f"`Accept work` on it. Both acts are on the thread: `swarm show {r['id']}`.")
    return OK


def cmd_auto_accept(args):
    """Read the week's measurement; with --on/--off, write the flag.

    Read is the default action ON PURPOSE. Before task 0139 the measurement D00 rule 3 gates the
    switch behind had no surface at all -- nothing anywhere read `disagreement_report()` -- and the
    flag had no writer, so the only way to flip it was raw SQL. Both halves live on one verb so
    that the number and the switch are never more than one command apart.

    `--by` NO LONGER FALLS BACK TO `$USER`. Bus row `0419` names `start`, `stop`, `pause` and
    `resume`; this was the fifth site of the same fallback and it is the one with the largest
    subject. `brain.runtime_flag.set_by` here records who turned AUTOMATIC ACCEPTANCE on or off
    for the whole fleet, which is the switch `set_flag` itself refuses to enable without a reason,
    and it was being signed with a login name that is nobody. It resolves through
    `store.human_slug()` now, the same producer `accept work` uses. The flag write still runs as
    `brain_runtime` and no trigger checks this column, so the name is a request rather than an
    established fact, exactly as `_who` says of the four fleet verbs.
    """
    if args.on or args.off:
        r = store.apply("auto accept flag", on=bool(args.on),
                        by=args.by.strip() or store.human_slug(), note=args.note or "")
        if args.json:
            out_json(r)
            return OK
        print(f"auto-accept {'ENABLED' if r['enabled'] else 'DISABLED'} by {r['set_by']}"
              + (f": {r['note']}" if r["note"] else ""))
        return OK

    rep = accept_mod.disagreement_report()
    if args.json:
        out_json({"report": rep, "measurements": accept_mod.measurements()} if args.full
                 else {"report": rep})
        return OK
    print(f"auto-accept {'ENABLED' if rep['enabled'] else 'DISABLED'}")
    rate = rep["disagreement_rate"]
    print(f"  {rep['n']} recorded verdict(s), {rep['scored']} decided by a human, "
          f"{rep['agree']} agreed")
    print("  disagreement rate " + (f"{rate:.1%}" if rate is not None else
                                    "-- nothing decided yet, so there is no rate"))
    for k, label in (("false_accept", "false ACCEPT (rule yes, human said no)"),
                     ("false_reject", "false reject (rule no, human said yes)"),
                     ("pending", "not decided yet, NOT scored"),
                     ("withdrawn", "cancelled, no verdict on the work"),
                     ("rule_acted", "the rule accepted these itself, not scored")):
        if rep[k]:
            print(f"  {label}: {' '.join(rep[k])}")
    if args.full:
        for m in accept_mod.measurements():
            print(f"  {m['id']} seq {m['seq']}  {render.short(m['measured_at'])}  "
                  f"rule {'yes' if m['rule_would_accept'] else 'no ':<3}  human {m['human']:<9} "
                  f"{m['decided_by']}")
    return OK


# ------------------------------------------------------------------ reads

def cmd_ls(args):
    rows = reads.tasks(state=args.state, lane=args.lane)
    if args.json:
        out_json(rows)
        return OK if rows else EMPTY
    if not rows:
        print("no tasks")
        return EMPTY
    for t in rows:
        who = t["claimed_by"] or t["posted_by"]
        gate = ""
        if t["external"] or t["canon_touching"]:
            gate = " [GATE]"
        # THE MARKER THE BRIEF FOR 0414 ASKED FOR: which rows dispatch may hand out and which it
        # may not, on the surface the operator actually reads. `[GATE]` is about what surfaces to
        # him AFTER an agent works it; this is about whether an agent may work it at all, and
        # before migration 26 the first was visible and the second did not exist.
        if not t.get("agent_claimable") and t["state"] in ("inbox", "blocked"):
            gate += " [OPERATOR]"
        # Task 0119. `[OPERATOR]` was scoped to inbox/blocked, so the one state where the hold is
        # a claim ABOUT A LIVE RUN was the one state this surface said nothing at all about.
        if reads.held_and_active(t):
            gate += " [HELD+RUNNING]"
        print(f"{t['id']}  {t['state']:<9} p{t['priority']} {t['lane']:<12} "
              f"{render.one_line(t['title'], 56):<56} {who}{gate}")
    return OK


def cmd_state(args):
    t = reads.task(_tid(args.id))
    if not t:
        die(f"no such task: {args.id}")
    print(t["state"])
    return OK


def cmd_show(args):
    tid = _tid(args.id)
    t = reads.task(tid)
    if not t:
        die(f"no such task: {tid}")
    sig = reads.signals_of(tid, effective())
    if args.json:
        out_json({"task": t, "signals": sig, "thread": reads.thread(tid),
                  "artifacts": reads.artifacts(task_id=tid), "runs": reads.runs(tid)})
        return OK

    print(f"{t['id']}  {t['title']}")
    print(f"  lane {t['lane']}  state {t['state']}  priority {t['priority']}  "
          f"attempts {t['attempts']}/{t['max_attempts']}")
    if t["claimed_by"]:
        print(f"  claimed by {t['claimed_by']} at {render.stamp(t['claimed_at'])}")
    if t["workdir"]:
        print(f"  workdir {t['workdir']}")
    if t["depends_on"]:
        print(f"  depends on {t['depends_on']}")
    if t["parent"]:
        print(f"  parent {t['parent']}")
    if t["blocked_on"]:
        q = reads.question(t["blocked_on"])
        print(f"  blocked on {t['blocked_on']}" + (f": {render.one_line(q['text'], 80)}" if q else ""))
    # THE LINE THAT WAS READ AS A GUARANTEE AND WAS A DESCRIPTION OF A COLUMN. Task 0119.
    #
    # `swarm show 0103` printed `claimable by an agent: NO -- held for the operator` while the row
    # was `state active`, `claimed_by T5`, and a runner was executing it on a client's executive
    # dashboard. Every word of it was true about the column and the sentence it formed was false
    # about the world. THIS FUNCTION IS ALSO THE AGENT PROMPT -- `bin/swarm-run` builds a
    # terminal's whole work order by shelling out to it -- so the agent inside the task read the
    # same false line about itself and could not have learned otherwise.
    #
    # A hold that did not stop a run is not a hold, and this now says which of the two it is.
    holder = reads.held_and_active(t)
    if holder:
        print(f"  claimable by an agent: NO -- held for the operator, BUT {holder} IS EXECUTING "
              f"IT NOW")
        print(f"      The hold reaches FUTURE claims only. It did not recall {holder}'s claim, "
              f"stop the runner, or")
        print(f"      release the task, and nothing about this run is gated by it. If you are "
              f"{holder}: treat this as")
        print(f"      HELD -- stage your work, apply nothing irreversible, and report. To "
              f"actually stop the run:")
        print(f"      `swarm cancel {t['id']} --agent <you> --force`.")
    else:
        print(f"  claimable by an agent: "
              f"{'yes' if t.get('agent_claimable') else 'NO -- held for the operator'}")
    if sig and sig["gated"]:
        print(f"  GATE {' + '.join(sig['gate_reasons'])}"
              + (f" (inherited from {', '.join(sig['inherited_from'])})"
                 if any(sig["inherited"].values()) else ""))

    hidden = 0

    # THE BRIEF FIRST, AND LABELLED, because THIS FUNCTION IS THE AGENT PROMPT. `bin/swarm-run`
    # builds a terminal's whole prompt by shelling out to `swarm show "$TASK_ID"`, so whatever
    # this prints between the banners IS the work order the attempt runs against.
    #
    # Before migration 14 the work order and the report shared the `result` column, so this block
    # printed `result: <the posted body>` on a fresh task and `result: <the agent's summary>` on a
    # reopened one -- same label, and after `done` the body was gone from the database entirely.
    # Attempt 2 of a rejected task was therefore dispatched against attempt 1's summary. Measured
    # for task 0138; the probe is in `migrations/0014_work_item_brief.sql`.
    brief = t.get("brief") or ""
    if brief:
        shown = brief if args.full else render.block(brief, tid)
        hidden += max(0, len(brief) - len(shown))
        print(f"\n  brief (the posted work order):\n{shown}")

    # `result` says what most recently HAPPENED, and prints only when it says something the brief
    # does not. The `!=` half of this guard is now belt and braces rather than the main event:
    # migration 14 landed with `post` writing the body into BOTH columns, so the two were
    # byte-identical on any task no finisher had touched and printing both put the same
    # instructions in the prompt twice, reading as two work orders rather than one. Task 0169
    # ended that -- `post` writes `brief` only and a fresh task's `result` is '' -- so the
    # emptiness check is what carries it today. The `!=` stays for the rows posted during the
    # transition, which still hold the duplicate, and it costs nothing.
    if t["result"] and t["result"] != brief:
        shown = t["result"] if args.full else render.block(t["result"], tid)
        hidden += max(0, len(t["result"]) - len(shown))
        label = "result" if not brief else "result (the latest report, NOT the work order)"
        print(f"\n  {label}: {shown}")

    th = reads.thread(tid)
    if th:
        print("\n  thread")
        for e in th:
            body = e["text"] if args.full else render.one_line(e["text"], render.width() - 30)
            hidden += max(0, len(e["text"]) - len(body))
            to = f" -> {e['to_agent']}" if e["to_agent"] else ""
            print(f"    {render.short(e['ts'])} {e['from_agent']}{to} [{e['kind']}] {body}")

    arts = reads.artifacts(task_id=tid)
    if arts:
        print("\n  artifacts")
        for a in arts:
            gone = "" if a["kind"] == "external" or Path(a["path"]).exists() else "  MISSING"
            print(f"    {a['kind']:<9} {a['path']}{gone}")

    rs = reads.runs(tid)
    if rs:
        print("\n  runs")
        for r in rs:
            print(f"    attempt {r['attempt']}  {r['agent']}  {r['outcome'] or 'running'}  "
                  f"exit {r['exit_code']}  {r['stream_pointer'] or ''}")

    if args.full:
        # Forensic recovery. Under the file bus this rebuilt text the WRITE PATH had destroyed;
        # here nothing is destroyed, so --full recovers the different loss that still happens:
        # an agent killed before its `done` landed leaves the report only in the run stream.
        rec = _recover_from_streams(tid, rs)
        if rec:
            print("\n  recovered from the run stream (never reached the bus)")
            for verb, text, src in rec:
                print(f"    swarm {verb}: {render.one_line(text, 200)}\n      from {src}")
    elif hidden:
        print(render.cut_banner(tid, hidden))
    return OK


def _recover_from_streams(tid, runs):
    """shlex-parse `swarm done/fail/block/note` back out of a run's tool_use records.

    Ported from `show --full` (swarm-admiral, landed 2026-08-15). Its original job was to
    recover text the write path had cut; that job is gone because nothing cuts. The job that
    remains is the one the incident of an orphaned engine creates: the agent typed the command,
    the process died before it ran, and the only copy is the transcript.
    """
    import shlex
    found = []
    for r in runs:
        ptr = r.get("stream_pointer")
        if not ptr or not Path(ptr).exists():
            continue
        try:
            lines = Path(ptr).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if "swarm" not in line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            for blk in _tool_uses(ev):
                cmd = str(blk.get("command") or "")
                if not cmd or "swarm" not in cmd:
                    continue
                try:
                    parts = shlex.split(cmd)
                except ValueError:
                    continue
                for i, tok in enumerate(parts):
                    if tok in ("done", "fail", "block", "note") and i and "swarm" in parts[i - 1]:
                        text = ""
                        for j, p in enumerate(parts[i:]):
                            if p in ("--summary", "--reason") and i + j + 1 < len(parts):
                                text = parts[i + j + 1]
                        if not text and len(parts) > i + 2:
                            text = parts[-1]
                        if text:
                            found.append((tok, text, f"{ptr} attempt {r['attempt']}"))
    return found


def _tool_uses(ev):
    msg = ev.get("message") or {}
    content = msg.get("content") if isinstance(msg, dict) else None
    for blk in (content or []):
        if isinstance(blk, dict) and blk.get("type") == "tool_use":
            yield blk.get("input") or {}


def cmd_signals(args):
    sig = reads.signals_of(_tid(args.id), effective())
    if not sig:
        die(f"no such task: {args.id}")
    if args.json:
        out_json(sig)
        return OK
    print(f"{sig['id']}  score {sig['score']}  priority {sig['priority']}")
    for f in LEVELLED_SIGNALS:
        d = sig["declared"][f] or "(not assessed)"
        print(f"  {f:<24} {sig['levels'][f]:<7} {d}")
    for f in HARD_FLAGS:
        src = ""
        if sig["inherited"][f] and sig["inherited_from"]:
            src = f"  (inherited from {', '.join(sig['inherited_from'])})"
        print(f"  {f:<24} {str(sig[f]).lower()}{src}")
    if sig["gated"]:
        print(f"  GATED: {' + '.join(sig['gate_reasons'])} -- surfaces to the operator whatever "
              f"its other signals say")
    return OK


def cmd_why(args):
    tid = _tid(args.id)
    sig = reads.signals_of(tid, effective())
    if not sig:
        die(f"no such task: {args.id}")
    q = reads.queue_order(effective())
    pos = next((i + 1 for i, r in enumerate(q) if r["id"] == tid), None)
    terms = ", ".join(f"{k} {v:+g}" for k, v in sig["terms"].items() if v)
    print(f"{tid} is {'#' + str(pos) if pos else 'not'} in the claimable queue "
          f"({len(q)} claimable).")
    print(f"  priority band {sig['priority']} first, then score {sig['score']} = {terms or '0'}")
    if sig["gated"]:
        print(f"  GATE {' + '.join(sig['gate_reasons'])}: surfaces to the operator regardless")
    if sig["state"] != "inbox":
        print(f"  it is {sig['state']}, so it is not claimable at all")
    # Task 0177. THE PREVIEW AND THE CLAIM DISAGREE, AND THIS SAYS SO OUT LOUD.
    #
    # `reads.queue_order` is a preview: `state = 'inbox'`, `_DEPS_MET`, and the claim's ordering.
    # It applies neither predicate that depends on who is claiming, and task 0122 added one of
    # them -- the lane ceiling. Without this line `why` reports a task as "#1 in the claimable
    # queue" that `claim` will never hand to anybody, which is a worse answer than no answer:
    # the operator reads it as "the fleet is about to take this".
    #
    # A line rather than a fixed queue. Making the preview exact means it can no longer answer
    # "the queue" at all -- host affinity and the lane ceiling are both per-agent, so an exact
    # queue is a queue FOR AN AGENT, and the board and console ask for the fleet's. That is a
    # bigger decision than this task; the divergence that is left is written down at
    # `reads.queue_order`.
    if sig["state"] == "inbox":
        t = reads.task(tid)
        lane = (t or {}).get("lane", "")
        stopped = reads.budget_stopped_lanes()          # None = brake not installed, so silent
        if stopped and lane in stopped:
            print(f"  LANE {lane} IS BUDGET-STOPPED, so `claim` excludes it from the candidate "
                  f"set and no agent will be handed this however high it sorts. The queue above "
                  f"is a preview and does not apply the lane ceiling.")
            print(f"  `budget check --lane {lane}` says why. Raising that ceiling or "
                  f"`budget resume` is the way out.")
    return OK


def cmd_feed(args):
    kinds = tuple(k for k in (args.kind or "").split(",") if k)
    since = args.since or (60 if args.follow else None)
    rows = reads.feed(since_minutes=since, agent=args.agent,
                      task_filter=_tid(args.task) if args.task else None,
                      kinds=kinds, limit=args.limit)
    if args.json:
        out_json(rows)
        return OK if rows else EMPTY
    w = render.width()
    for e in rows:
        tag = f"[{e['work_item_id']}]" if e["work_item_id"] else "      "
        to = f" -> {e['to_who']}" if e["to_who"] else ""
        print(f"{render.short(e['ts'])} {tag} {e['from_who']}{to} {e['kind']}: "
              f"{render.one_line(e['text'], max(20, w - 46))}")
    if args.follow:
        import time
        seen = {(str(e["ts"]), e["kind"], e["text"]) for e in rows}
        try:
            while True:
                time.sleep(args.interval)
                for e in reads.feed(since_minutes=5, agent=args.agent,
                                    task_filter=_tid(args.task) if args.task else None,
                                    kinds=kinds, limit=50):
                    key = (str(e["ts"]), e["kind"], e["text"])
                    if key in seen:
                        continue
                    seen.add(key)
                    tag = f"[{e['work_item_id']}]" if e["work_item_id"] else "      "
                    print(f"{render.short(e['ts'])} {tag} {e['from_who']} {e['kind']}: "
                          f"{render.one_line(e['text'], max(20, w - 46))}", flush=True)
        except KeyboardInterrupt:
            pass
    return OK if rows else EMPTY


def stopped_note(a):
    """The STOPPED flag with its provenance, or the flag plus the fact that there is none.

    THE SENTENCE THIS FUNCTION EXISTS FOR is the third branch. A STOPPED flag with no record
    behind it is not the same thing as a stop somebody signed, and printing them identically is
    what let an admiral pass at 00:56Z on 2026-08-19 read a commander's deliberate stop as two
    terminals dying silently. So the flag alone now says out loud that it is unexplained instead
    of standing there looking like a decision.

    `.get()` and not `[...]`: `reads.agents()` grew these keys in task 0273 and a caller holding
    an older row (a `--json` payload replayed, a test fixture) must degrade to "no record"
    rather than raise a KeyError, which is rule 2 of `docs/SCHEMA-TOLERANCE.md`.
    """
    if not a.get("stopped_at"):
        return ""
    if a.get("stop_kind") != "stop":
        return "  STOPPED, no record of who or why"
    who = a.get("stop_by") or "?"
    # THE REASON IS PARSED BACK OUT OF THE RECORD'S TEXT, and the format is fixed by
    # `transitions._lifecycle`, which is the only thing that writes these rows: "STOP <agent> by
    # <who>[, holding <task>]: <reason>". `maxsplit=1` so a reason containing ": " -- and the
    # incident's own reason, "rate limit: holding T4 and T5", does -- survives whole.
    #
    # A `stop_reason` column on `brain.agent` would not need parsing, and it would also be dead
    # on the 119 stores below the tip until the operator applied the migration. Parsing a string
    # this repo writes itself is the cheaper coupling. If you change the format in `_lifecycle`,
    # change it here; nothing else reads it.
    why = (a.get("stop_record") or "").split(": ", 1)
    why = why[1] if len(why) > 1 else ""
    return (f"  STOPPED by {who} {render.dur(_age(a.get('stop_at')))} ago"
            + (f": {render.one_line(why, 60)}" if why else ""))


def _age(ts):
    """Seconds since `ts`, or 0 when there is no timestamp. Never a guess about the future."""
    if not ts:
        return 0
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.timezone.utc)
    return max(0, int((now - ts).total_seconds()))


def paused_note():
    """`PAUSED` with the actor and reason `brain.runtime_flag` has always held and nobody read."""
    r = reads.fleet_pause_record()
    if not r or str(r.get("value") or "").lower() != "true":
        return ""
    return ("   PAUSED by " + (r.get("set_by") or "?")
            + f" {render.dur(_age(r.get('set_at')))} ago"
            + (f": {render.one_line(r.get('note') or '', 60)}" if r.get("note") else ""))


def cmd_status(args):
    c = reads.counts()
    ags = reads.agents()
    open_q = len(reads.questions())
    paused = reads.fleet_paused()
    if args.json:
        out_json({"counts": c,
                  "agents": [dict(a, liveness=liveness_mod.classify(a)) for a in ags],
                  "open_questions": open_q, "paused": paused,
                  "pause_record": reads.fleet_pause_record(),
                  "liveness_window_seconds": liveness_mod.CLAIM_LIVENESS_SECONDS,
                  "liveness_host": liveness_mod.this_host()})
        return OK
    print(f"fleet {effective().get('fleet', 'swarm')}" + (paused_note() if paused else ""))
    print("  " + "  ".join(f"{k} {v}" for k, v in c.items()))
    print(f"  open questions {open_q}")
    for a in ags:
        on = f" on {a['work_item_id']}" if a["work_item_id"] else ""
        print(f"  {a['name']:<10} {a['role']:<10} {a['status']:<10}{on}  "
              f"silent {render.dur(a['silent'])}{stopped_note(a)}{liveness_mod.note(a)}")
    # THE LIVENESS VERDICT, ALONGSIDE THE HEARTBEAT AND NEVER INSTEAD OF IT. Task 0371.
    #
    # `status` printed the word `heartbeat` last wrote and the age of that write, and left the
    # reader to decide which of those meant "alive". On 2026-08-27 this board read
    # `D9R terminal working on 0052 silent 248h37` off a row whose pid is 4097838, and
    # /proc/4097838 does not exist. `reads.agents()` was already selecting that pid and nothing
    # anywhere read it. `swarm_engine/liveness.py` holds what each verdict rests on and what it
    # refuses to guess; the trailing line here is its denominator, because a fleet whose rows are
    # all on other hosts is a fleet this process cannot call dead, and that belongs on the screen.
    _live = [liveness_mod.classify(a) for a in ags]
    _tally = {}
    for _v in _live:
        _tally[_v["state"]] = _tally.get(_v["state"], 0) + 1
    print(f"  liveness: {sum(1 for _v in _live if _v['checkable'])} of {len(ags)} agent(s) "
          f"process-checked on {liveness_mod.this_host() or '(unknown host)'}, window "
          f"{liveness_mod.CLAIM_LIVENESS_SECONDS}s"
          + (f"  [{', '.join(f'{k} {n}' for k, n in sorted(_tally.items()))}]" if ags
             else "  [0 agents on the roster: nothing was compared]"))
    return OK


def cmd_board(args):
    c = reads.counts()
    q = reads.queue_order(effective())
    if args.json:
        out_json({"counts": c, "queue": q, "agents": reads.agents(),
                  "questions": reads.questions(), "blocked": reads.blocked_waiting()})
        return OK
    print(f"=== {effective().get('fleet', 'swarm')} ==="
          + (paused_note() if reads.fleet_paused() else ""))
    print("  " + "  ".join(f"{k} {v}" for k, v in c.items()))
    print("\n-- next up --")
    for r in q[:12]:
        print(f"  {r['id']}  p{r['priority']} score {round(r['score'], 2):<6} {r['lane']:<12} "
              f"{render.one_line(r['title'], 60)}")
    if not q:
        print("  (queue empty)")
    print("\n-- active --")
    for t in reads.tasks(state="active"):
        print(f"  {t['id']}  {t['claimed_by']:<8} {render.one_line(t['title'], 60)}")
    b = reads.blocked_waiting()
    if b:
        print("\n-- blocked --")
        for r in b:
            print(f"  {r['id']}  on {r['blocked_on'] or '-'}  "
                  f"{render.one_line(r['question'] or r['title'], 60)}")
    oq = reads.questions()
    if oq:
        print("\n-- questions waiting on you --")
        for r in oq:
            print(f"  {r['id']}  {r['asked_by']}: {render.one_line(r['text'], 70)}")
    print("\n-- agents --")
    # Same classifier as `status`, so the two surfaces cannot disagree about what "live" means.
    # Task 0371 item 4. (The `for a in reads.agents(): pass` that stood here did nothing but pay
    # for a query; removed with it.)
    _bag = reads.agents()
    for a in _bag:
        print(f"  {a['name']:<10} {a['status']:<10} silent {render.dur(a['silent'])}"
              + stopped_note(a) + liveness_mod.note(a))
    print(f"  liveness: "
          f"{sum(1 for a in _bag if liveness_mod.classify(a)['checkable'])} of {len(_bag)} "
          f"agent(s) process-checked on {liveness_mod.this_host() or '(unknown host)'}, window "
          f"{liveness_mod.CLAIM_LIVENESS_SECONDS}s")
    return OK


def cmd_brief(args):
    w = reads.brief_window(args.since)
    if args.json:
        out_json(w)
        return OK
    print(f"=== the last {args.since}h ===\n")
    done = [t for t in w["finished"] if t["state"] == "done"]
    print(f"finished {len(done)}")
    for t in done:
        print(f"  {t['id']}  {t['claimed_by']:<8} {render.one_line(t['title'], 60)}")
        if t["result"]:
            print(f"      {render.one_line(t['result'], 160)}")
    blocked = [t for t in w["finished"] if t["state"] == "blocked"]
    if blocked:
        print(f"\nblocked {len(blocked)}")
        for t in blocked:
            print(f"  {t['id']}  {render.one_line(t['result'], 100)}")
    if w["questions"]:
        print(f"\nquestions {len(w['questions'])}")
        for q in w["questions"]:
            mark = ("answered" if q["answer"]
                    else f"withdrawn by {q['withdrawn_by']}" if q.get("withdrawn_at")
                    else "OPEN")
            print(f"  {q['id']} [{mark}] {render.one_line(q['text'], 100)}")
    # The MISSING cross-check. A record whose path is not on disk now is a finding.
    missing = [a for a in w["artifacts"]
               if a["kind"] != "external" and not Path(a["path"]).exists()]
    print(f"\nartifacts {len(w['artifacts'])}" + (f", {len(missing)} MISSING" if missing else ""))
    for a in w["artifacts"]:
        gone = "  MISSING" if a in missing else ""
        print(f"  {a['work_item_id']}  {a['kind']:<9} {a['path']}{gone}")
    return OK


def cmd_config(args):
    # `--fingerprint` is a READ and nothing else, added by task 0273 as the measurable half of
    # the config-provenance proposal in `docs/CONFIG-PROVENANCE.md`. It answers "what config is
    # this fleet running" in one comparable string, so a lane investigating a fleet that behaves
    # differently than it did an hour ago has something to compare instead of a file with no
    # history. It records nothing; recording it at runner start is the proposal, not this.
    if getattr(args, "fingerprint", False):
        fp = config_fingerprint()
        if args.json:
            out_json(fp)
        elif not fp["present"]:
            print(f"{fp['path']}  ABSENT -- the fleet is running on DEFAULT_CONFIG")
            return EMPTY
        else:
            print(f"{fp['sha256']}  {fp['bytes']} bytes  mtime {fp['mtime']}  {fp['path']}")
        return OK
    # `--layers` answers WHERE EACH VALUE CAME FROM. It is a separate flag and not a change to
    # the default output on purpose: `swarm config` printing resolved config is the property the
    # lane brief says this tool already gets right, and the difference between what is configured
    # and what is in force is where this class of tool usually lies. Adding provenance to the
    # default document would break every caller that parses it, `swarm-run`'s `--shell` included.
    if getattr(args, "layers", False):
        from .config import config as file_config
        file_cfg = file_config()
        rows = admin_mod.layer()
        infos = admin_mod.fleet_key_report(file_cfg, rows)
        from_store = [i for i in infos if i["source"] == "store"]
        if args.json:
            out_json({"keys": infos, "counted": len(infos), "from_store": len(from_store),
                      "file": str(config_path()), "fingerprint": config_fingerprint()})
        else:
            for i in infos:
                print(f"{i['key']:<32} {json.dumps(i['in_force'], default=str):<28} "
                      f"[{i['source']}]" + (f"  {i['set_by']}" if i["set_by"] else ""))
            print(f"\n{len(from_store)}/{len(infos)} key(s) in force come from the store layer, "
                  f"the rest from {config_path()} or the code default.")
        return OK if infos else EMPTY
    r = resolved(args.agent or "", effective())
    if args.shell:
        flat = {k: (",".join(map(str, v)) if isinstance(v, list) else v)
                for k, v in r.items() if not isinstance(v, dict)}
        shell_export("CFG_", flat)
    elif args.json:
        out_json(r)
    else:
        print(json.dumps(r, indent=2, default=str))
    return OK


# --------------------------------------------------------------------------------- task 0295 D
# A DETERRENT, AND THE DOCSTRING SAYS SO BEFORE IT SAYS ANYTHING ELSE. This is option D of
# question q0305, chosen by the operator on 2026-08-19 alongside "finish A"; option B, the only
# one that would make the strong sentence true, was raised to him and NOT built (the packet is
# `docs/DECISION-fleet-unix-user.md`).
def _refuse_as_operator_from_a_fleet_terminal():
    """Refuse `swarm set --as-operator` inside a fleet terminal. IT IS NOT A SECURITY BOUNDARY.

    WHAT IT DOES NOT DO, first, because a deterrent that reads as a control is worse than none:
    `env -u SWARM_AGENT -u SWARM_PARENT_TASK swarm set ...` walks straight past it, and the
    promotion does not need this CLI at all -- six lines of psycopg2 on `dsn("operator")` and a
    single statement raising `agent_claimable` on the row do the same thing without ever loading
    this file. Both were measured from a live fleet terminal on 2026-08-19 (task 0295's thread,
    scratch stores `brain_t2_0222_path` and `brain_t3_0295`). The credential is a `0600` file in
    the operator's home and every fleet process on this host runs as that same Unix user, so
    nothing in this process can stop a process that decides to read it.

    WHAT IT DOES DO is the failure that actually happened rather than the one easiest to imagine.
    On 2026-08-18 an admiral pass promoted rows the operator had deliberately held, WHILE BEING
    HELPFUL, because nothing distinguished a deliberate hold from a forgotten `--for-agents`.
    This makes that particular accident impossible to have by accident: a fleet terminal must
    unset its own identity to type the flag, which is a deliberate act, and it leaves a legible
    refusal on the way. Same shape and same limit as the pre-commit guard naming `--no-verify`.

    It lives at the CLI door and not in the transition on purpose, the same reasoning as
    `cmd_accept`: the console (`web/actions.py`) passes `as_operator=True` as a Python kwarg on
    every operator write and can be started from a shell that inherited `SWARM_PARENT_TASK`, so
    an env check inside the transition would refuse the operator's own unattended surface. The
    flag exists on this one verb only.
    """
    fleet = os.environ.get("SWARM_AGENT", "").strip()
    parent = os.environ.get("SWARM_PARENT_TASK", "").strip()
    if not (fleet or parent):
        return
    die(f"refusing `--as-operator` from a fleet terminal "
        f"(SWARM_AGENT={fleet or '(unset)'} SWARM_PARENT_TASK={parent or '(unset)'}). "
        f"The flag opens the OPERATOR'S database login, and `agent_claimable = false` is how he "
        f"holds his own row -- a credential rotation, the client sends, a personal errand. Promoting one "
        f"is his decision. If you believe a held row is fleet work, say so on its thread or post "
        f"a new one; do not promote his.\n"
        f"swarm: THIS IS A DETERRENT, NOT A BOUNDARY, and you should know exactly how thin it "
        f"is. `env -u SWARM_AGENT -u SWARM_PARENT_TASK` defeats it, and the promotion never "
        f"needed this CLI: six lines of psycopg2 on the operator's DSN do it without loading "
        f"this file. Both were measured (task 0295). What stops you is that you are not supposed "
        f"to, not that you cannot -- see `store/SECRETS.md`, `What --as-operator gates on "
        f"today`. What the database DOES enforce is narrower and it is real: a connection as "
        f"`brain_runtime` cannot promote the row at all.", code=6)


def cmd_set(args):
    # `--agent` defaults to EMPTY, not to "operator". The transition resolves it: what was typed,
    # else `SWARM_AGENT` from the terminal, else the operator -- and inside a fleet terminal that
    # named nobody, it refuses rather than filing the write against the operator. Defaulting to
    # "operator" here is exactly how `set external = False`, run by an agent, was recorded as the
    # operator's act on 2026-08-16.
    if getattr(args, "as_operator", False):
        _refuse_as_operator_from_a_fleet_terminal()
    try:
        r = store.apply("set", id=_tid(args.id), key=args.key, value=args.value,
                        agent=args.agent, force=args.force,
                        as_operator=bool(getattr(args, "as_operator", False)))
    except (VerbError, ValueError) as e:
        die(str(e))
    except psycopg2.errors.RaiseException as e:
        die_db(e, "set")
    # The STORED value. A trigger may coerce one (migration 26 holds a human-actor row back from
    # the fleet however the flag was written), and printing the request instead would report an
    # edit that did not happen.
    print(f"{_tid(args.id)} {args.key} = {r[args.key]}")
    # A FORCED HOLD OVER A LIVE RUN TELLS THE PERSON WHO TYPED IT, not only the thread. Task 0119.
    # The admiral read `agent_claimable = False` on 0103, believed the row was held, and it was
    # not: T5's runner carried on. The bare echo of the stored value is TRUE and it is the whole
    # misreading, so the caveat goes where the decision was made.
    if r.get("held_while_active"):
        who = r["held_while_active"]
        print(f"swarm: {_tid(args.id)} is held for the operator AND {who} is still executing it. "
              f"This did NOT stop the run, recall the claim, or release the task -- it holds the "
              f"row's future only.", file=sys.stderr)
        print(f"swarm: {who} has been messaged and the override is on the thread. `swarm doctor` "
              f"reports this row as CRITICAL until one of the two is made true: `swarm cancel "
              f"{_tid(args.id)} --agent <you> --force` stops the run, `swarm set "
              f"{_tid(args.id)} agent_claimable true --as-operator` clears the hold.",
              file=sys.stderr)
    if r.get("coerced"):
        print(f"swarm: you asked for {args.key} = {r['asked']} and the store kept "
              f"{r[args.key]}. That is a guard, not a failure -- `swarm show {_tid(args.id)}` "
              f"says which one.", file=sys.stderr)
    return OK


def cmd_tick(args):
    """Does a planner have anything to think about? 0 yes, 2 no.

    The fingerprint is the board's shape. A planner that already planned this exact board does
    not plan it again, which is what stops an admiral burning a subscription on an unchanged
    queue.
    """
    import hashlib
    if reads.fleet_paused() or reads.agent_stopped(args.agent):
        return EMPTY
    rows = reads.tasks()
    shape = "|".join(f"{t['id']}:{t['state']}:{t['claimed_by']}" for t in rows)
    shape += "||" + "|".join(o["name"] for o in reads.objectives(state="inbox"))
    shape += "||" + "|".join(q["id"] for q in reads.questions())
    fp = hashlib.sha256(shape.encode()).hexdigest()[:16]
    ags = {a["name"]: a for a in reads.agents()}
    a = ags.get(args.agent) or {}
    same = a.get("tick_fingerprint") == fp
    recent = (a.get("tick_at") is not None
              and (a["tick_at"].timestamp() + args.min_seconds) > __import__("time").time())
    if args.commit:
        store.apply("tick commit", agent=args.agent, fingerprint=fp)
        print(fp)
        return OK
    if same and recent:
        return EMPTY
    if args.json:
        out_json({"fingerprint": fp, "changed": not same, "recent": recent})
    else:
        print(fp)
    return OK


def cmd_utilization(args):
    # READ ONLY, deliberately. This prints where the seven-day meter stands and stops there: it
    # does not pause, stop, throttle or rotate anything. Where the ceiling sits is question q0276
    # and it is the operator's. The gauge is the half that can be built without his answer, and
    # it is the half that was missing -- before this, no surface in the engine could read the
    # meter at all, so the standing commitment to stop the fleet at a ceiling was unenforceable
    # in the plainest sense: nobody could tell whether it had been crossed.
    #
    # NOT `budget`, which is a DIFFERENT meter behind a different binary (budget/bin/budget,
    # not a swarm verb). That one counts DOLLARS, off the terminal `result` event's
    # total_cost_usd.
    # This one is the Anthropic seven-day rate limit, and the consequence differs: dollars are
    # a cost, but this reaching 1.0 takes the OPERATOR's own Claude access away until the window
    # resets, because both config dirs are one account.
    #
    # It reads the run streams and nothing else, so it answers with Postgres down.
    r = ratelimit.read(directory=args.runs, max_age_minutes=args.max_age_minutes)
    if args.json:
        out_json(r)
    else:
        print(ratelimit.render(r), file=sys.stdout if r["ok"] else sys.stderr)
    # EMPTY, not ERR: nothing to read is the ported meaning of 2, and the caller that matters
    # here is a shell test asking "did I get a number", which any non-zero answers.
    return OK if r["ok"] else EMPTY


def cmd_doctor(args):
    """Absence detection. Exit 1 on a critical finding, so cron can page on it."""
    import time as _t
    crit, warn = [], []
    ags = reads.agents()
    for a in ags:
        if a["stopped_at"]:
            continue
        if a["silent"] is not None and a["silent"] > args.stale_seconds:
            crit.append(f"{a['name']} last heartbeat {render.dur(a['silent'])} ago")
        if a["silent"] is not None and a["silent"] < -CLOCK_SKEW_TOLERANCE:
            warn.append(f"{a['name']} heartbeat is {render.dur(-a['silent'])} in the FUTURE "
                        f"-- a broken clock, not skew")
    for t in reads.tasks(state="active"):
        held = (_t.time() - t["claimed_at"].timestamp()) if t["claimed_at"] else 0
        if held > args.stuck_seconds:
            crit.append(f"{t['id']} active for {render.dur(held)}, held by "
                        f"{t['claimed_by'] or '(nobody)'}")
        # The one the file bus could not see: two agents on one task. The row cannot hold two
        # claimers, so the tell is a second agent heartbeating against the same task id.
        others = [a["name"] for a in ags
                  if a["work_item_id"] == t["id"] and a["name"] != t["claimed_by"]
                  and a["status"] == "working"]
        if others:
            crit.append(f"{t['id']} is claimed by {t['claimed_by']} and ALSO being worked by "
                        f"{', '.join(others)}. Two agents on one task: stop one now.")
    # The other face of the same defect, and doctor was blind to it. The loop above only walks
    # tasks in `active`, so it catches "two agents on one task" and MISSES "no agent owns this
    # task and one is working it anyway" -- which is what the commander's recovery traded the
    # double claim for on 2026-08-18: killing a runner fired its shutdown requeue, the task went
    # to `inbox` with `claimed_by` empty, and T4 kept working it. Critical, not a warning: the
    # work in flight is being done against a row that says nobody is doing it, so every other
    # surface reports the fleet healthy.
    for r in reads.withheld_by_liveness(transitions.CLAIM_LIVENESS_SECONDS):
        crit.append(f"{r['id']} is {r['state']} with claimed_by="
                    f"{r['claimed_by'] or '(nobody)'}, but {r['live_agent']} heartbeat "
                    f"{render.dur(r['silent'])} ago working it. Nobody owns work that is being "
                    f"done. `claim` is withholding it; stop {r['live_agent']} or release it.")

    # HELD AND RUNNING AT THE SAME TIME. Task 0119, and the same class as the double claim above:
    # the board says one thing and reality is another. CRITICAL because the board's version is the
    # SAFER one -- a reader of `swarm show` or `swarm ls` sees a row the fleet may not touch, and
    # an agent is touching it -- so this is the shape that looks healthiest from every surface
    # while being the one that publishes to a client's dashboard.
    try:
        for r in reads.held_while_active():
            gate = " [GATE: external/canon]" if (r["external"] or r["canon_touching"]) else ""
            crit.append(f"{r['id']} is ACTIVE and being executed by {r['claimed_by']}, and the "
                        f"board says HELD FOR THE OPERATOR (agent_claimable=false){gate}. "
                        f"Lowering the flag does NOT recall an in-flight claim -- it reaches "
                        f"future claims only. This is 2026-08-18 task 0103, on a client-visible "
                        f"view. Decide which is true: `swarm cancel {r['id']} --agent <you> "
                        f"--force` stops the run, or `swarm set {r['id']} agent_claimable true "
                        f"--as-operator` clears a hold nothing is enforcing.")
    except Exception as e:                                       # noqa: BLE001
        crit.append(f"could not check for held-and-active rows ({e.__class__.__name__}). Whether "
                    f"a row the board calls held is being executed right now is UNVERIFIED, not "
                    f"verified good.")

    # POSTED FOR NOBODY. Migration 26 made `agent_claimable` default false, which is the right
    # default and a silent one: a fleet surface that posts without `--for-agents` produces a row
    # no agent will ever claim and no error anywhere. The trade is deliberate -- forgetting the
    # flag now stalls a task instead of mailing a client -- but a stall nobody is told about is
    # how 2026-08-14 happened. So the stall is reported, and the operator's OWN held rows are not
    # reported, because those are the guard working.
    try:
        orphaned = [r for r in reads.held_from_the_fleet() if not r["operator_owned"]]
        if orphaned:
            ids = ", ".join(r["id"] for r in orphaned[:10])
            more = "" if len(orphaned) <= 10 else f", +{len(orphaned) - 10} more"
            warn.append(f"{len(orphaned)} inbox row(s) are held from the fleet and are NOT marked "
                        f"as the operator's own ({ids}{more}). Either he posted them without "
                        f"--mine, or a fleet surface posted them without --for-agents -- in "
                        f"which case no agent will ever claim them. `swarm show <id>` says "
                        f"which; `swarm set <id> agent_claimable true --as-operator` releases "
                        f"one.")
    except Exception as e:                                       # noqa: BLE001
        crit.append(f"could not read the actor gate ({e.__class__.__name__}). Whether the "
                    f"operator's own queue is exposed to the fleet is UNVERIFIED, not verified "
                    f"good. Apply migrations/0026_work_item_agent_claimable.sql.")

    for q in reads.questions():
        age = _t.time() - q["asked_at"].timestamp()
        if age > args.question_seconds:
            warn.append(f"{q['id']} unanswered for {render.dur(age)}")
    # Task 0140. A question that produced no event reached no human and will not until somebody
    # opens the console, so this is CRITICAL rather than a warning: the always-on claim is either
    # true or it is not, and "the fleet is asking and nobody is being told" is the state that
    # looks healthiest from every other surface. Absence of the join, measured, not asserted.
    try:
        for q in reads.unpaged_questions():
            crit.append(f"{q['id']} (asked by {q['asked_by'] or '?'} "
                        f"{render.dur(_t.time() - q['asked_at'].timestamp())} ago) produced no "
                        f"question.raised event, so it paged NOBODY. Check the after-commit hook "
                        f"in store/transitions.py and the fabric's daily event budget.")
    except Exception as e:                                       # noqa: BLE001
        # A store without the fabric's tables is a deployment, not a defect. Say which it is
        # rather than either crashing doctor or reporting a clean bill of health.
        warn.append(f"could not check whether questions are reaching anyone "
                    f"({e.__class__.__name__}). Paging is UNVERIFIED, not verified good.")
    # Task 0177. THE FLEET LOOKS FINE AND IS NOT WORKING, which is what doctor is for. `claim`
    # excludes a budget-stopped lane from its candidate set (task 0122) and says nothing, and the
    # runner's spend gate cannot say it either -- that gate is asked about the fleet and the agent,
    # and at gate time the lane is not decided. So a multi-lane runner whose lanes are all stopped
    # calls `claim`, gets nothing, heartbeats, and reads as a healthy idle agent from every other
    # surface. This is the sentence that state was missing.
    #
    # CRITICAL only when work is actually stranded. A ceiling is something the operator set, so an
    # agent braked over an empty board is a warning: nothing is being lost this minute. Claimable
    # work sitting in a lane no agent can be handed is the 2026-08-14 shape -- healthy runners,
    # eleven idle hours, nothing saying why -- and doctor exits 1 on it so cron pages.
    try:
        for d in reads.dispatch_stalled(effective()):
            which = ("every lane with claimable work is budget-stopped" if d["any_lane"]
                     else "every lane it claims from is budget-stopped")
            where = (f"lanes {','.join(d['lanes'])}" if not d["any_lane"] else "lanes *")
            line = (f"{d['agent']} ({where}) can claim nothing: {which} "
                    f"({', '.join(d['stopped_lanes'])})")
            if d["waiting"]:
                ids = ", ".join(d["waiting_ids"])
                more = "" if d["waiting"] <= len(d["waiting_ids"]) else ", ..."
                crit.append(f"{line}. {d['waiting']} claimable task(s) are stranded there "
                            f"({ids}{more}). It will keep heartbeating and look healthy. "
                            f"`budget check --lane {d['stopped_lanes'][0]}` says why; raising the "
                            f"ceiling or `budget resume` is the way out.")
            else:
                warn.append(f"{line}. No claimable work is waiting in them, so nothing is "
                            f"stranded yet -- but that agent is braked, not idle.")
    except Exception as e:                                       # noqa: BLE001
        # Same rule as the paging check above: a store without the budget tables is a deployment
        # and not a defect, and `reads.budget_stopped_lanes` already answers None for it. Anything
        # else reaching here means the brake's state is UNREAD, which is worth a sentence rather
        # than a traceback that takes the other findings down with it.
        warn.append(f"could not check whether any lane is budget-stopped "
                    f"({e.__class__.__name__}). An idle agent may be braked, not idle.")
    nblocked = reads.counts()["blocked"]
    if nblocked >= args.blocked_threshold:
        crit.append(f"{nblocked} tasks blocked. If they arrived together, look for a "
                    f"subscription refusal charged as a failure before you look at the work.")
    for t in reads.tasks(state="inbox"):
        if t["host"] and t["host"] not in {a["host"] for a in ags}:
            warn.append(f"{t['id']} is pinned to host {t['host']}, which no agent reports")
    if args.json:
        out_json({"critical": crit, "warning": warn})
    else:
        for c in crit:
            print(f"CRITICAL  {c}")
        for x in warn:
            print(f"warning   {x}")
        if not crit and not warn:
            print("ok")
    return ERR if crit else OK


# ------------------------------------------------------------------ projects
#
# THE DOOR ONTO MIGRATION 44'S ROW. Bus row 0442. Before this, the only way a project came into
# existence was a hand-written INSERT at a psql prompt: no subcommand, no console form, and
# `web/bin/seed-demo.py` creates none. Every rule these verbs state is already enforced by the
# table; what this surface adds is that an operator meets English instead of SQLSTATE 23514.
#
# `engine/swarm_engine/projects.py` carries the reasoning, including the four measurements behind
# there being no `rename` action at all.


def _project_line(p, wired=True):
    """One project. COLOUR NEVER RIDES ALONE and there is none here: the state word is the signal.

    `open_items` is on the same line as the state on purpose. "This project is on hold" is a
    state; "and 14 items are still open under it" is the measurement that says whether the hold
    did anything, and it is the number the operator asked this feature to give him.
    """
    # THE ARCHIVE IS PRINTED WHATEVER THE STATE, and it is printed FIRST, because migration 47
    # makes it orthogonal to the operator's four words: a project archived while `blocked` is
    # still blocked, so a line that showed only the state would report a project that left the
    # board as one that is still on it. Measured 2026-08-29 by lane B2: without this an archived
    # project printed as plain `in progress` and nothing on the line said otherwise.
    arch = ""
    if p.get("archived_at"):
        arch = (f"\n               ARCHIVED {p['archived_at']:%Y-%m-%d %H:%M}Z by "
                f"{p['archived_by']}: {p['archived_reason']}"
                f"\n               off the board, and its work is withheld from the claim path. "
                f"Not cancelled: `swarm project unarchive {p['slug']}` returns it.")
    if p["state"] == "in_progress":
        head = (f"  in progress  {p['slug']:<24} {p['open_items']:>3} open "
                f"({p['active_items']} active) of {p['all_items']}")
        return f"{head}\n               {p['title']}{arch}"
    warn = "" if wired else "   [NOT ENFORCED ON THIS STORE]"
    return (f"  {p['state'].upper():<12} {p['slug']:<24} {p['open_items']:>3} open "
            f"({p['active_items']} active) of {p['all_items']}{warn}\n"
            f"               {p['title']}\n"
            f"               since {p['state_changed_at']:%Y-%m-%d %H:%M}Z by "
            f"{p['state_changed_by']}: {p['state_reason']}{arch}")


def _throttle_note(wired):
    """Say whether a hold on THIS store stops anything, rather than letting the verb imply it.

    A `hold` printed on a store without task 0430's gate records an intention and throttles
    nothing, and the incident behind this whole feature is an operator believing work was resting
    while it ran eight terminals wide. So the two halves are named separately: the claim path is
    code in this tree, the function is a row in this database's catalogue, and one does not imply
    the other.
    """
    if wired["wired"]:
        return None
    missing = []
    if not wired["claim_path"]:
        missing.append("engine/swarm_engine/transitions.py has no _PROJECT_BUDGET_CTE (task 0430)")
    if not wired["schema"]:
        missing.append("this database has no brain.work_item_project() "
                       "(budget/schema/0045_project_is_a_hold_scope.sql)")
    return ("A PROJECT STATE RECORDS AN INTENTION HERE AND THROTTLES NOTHING: "
            + "; ".join(missing) + ".\nSetting a project to hold on this store will NOT stop an "
            "agent claiming its work.")


def cmd_project(args):
    action = args.action
    wired = projects_mod.throttle_is_wired()

    if action == "list":
        rows = projects_mod.listing()
        if args.json:
            out_json({"projects": rows, "throttle": wired})
            return OK if rows else EMPTY
        if not rows:
            # "no projects" and "this store cannot hold one" are different facts.
            # `projects_mod.listing()` has already said the second on stderr, once, if it applies.
            print("no projects, over 0 rows in brain.project. "
                  "`swarm project add --help` says what one needs.")
            return EMPTY
        for p in rows:
            print(_project_line(p, wired["wired"]))
        resting = sum(1 for p in rows if p["state"] != "in_progress")
        held_open = sum(p["open_items"] for p in rows if p["state"] != "in_progress")
        print(f"\n{len(rows) - resting} in progress, {resting} resting over {len(rows)} projects; "
              f"{held_open} open item(s) under a resting project")
        note = _throttle_note(wired)
        if note and resting:
            print(f"\n{note}", file=sys.stderr)
        return OK

    if action == "show":
        p = projects_mod.one(args.slug)
        if not p:
            die(f"no project named {args.slug!r}. `swarm project list` prints every one.", EMPTY)
        rows = projects_mod.items(args.slug)
        if args.json:
            out_json({"project": p, "items": rows, "throttle": wired})
            return OK
        print(_project_line(p, wired["wired"]))
        for w in rows:
            print(f"    {w['id']:<6} {w['state']:<8} {w['lane']:<8} "
                  f"{(w['claimed_by'] or ''):<6} {w['title']}")
        print(f"\n{len(rows)} of {p['all_items']} work item(s) shown, "
              f"{p['open_items']} open, {p['active_items']} active")
        note = _throttle_note(wired)
        if note:
            print(f"\n{note}", file=sys.stderr)
        return OK

    if action == "add":
        res = store.apply("project add", slug=args.slug, title=args.title or "",
                          state=args.state or "in_progress", reason=args.reason or "",
                          by=args.by or "")
        if args.json:
            out_json(res)
            return OK
        print(f"project {res['slug']} created, {res['state'].replace('_', ' ')}, "
              f"by {res['created_by']}")
        if res["state"] != "in_progress":
            print(f"  resting from birth: {res['state_reason']}")
            print(f"  `swarm project resume {res['slug']} --reason '...'` starts it, "
                  f"and that needs the operator login")
        else:
            print(f"  `swarm project hold {res['slug']} --reason '...'` stops it, "
                  f"and that needs nothing")
        return OK

    if action in projects_mod.REST_STATES:
        res = store.apply("project rest", slug=args.slug, state=action,
                          reason=args.reason or "", by=args.by or "")
        if args.json:
            out_json({**res, "throttle": wired})
            return OK
        if res["already"]:
            print(f"{res['slug']} was ALREADY {res['state']} since "
                  f"{res['state_changed_at']:%Y-%m-%d %H:%M}Z by {res['state_changed_by']}: "
                  f"{res['state_reason']}")
        else:
            print(f"{res['slug']}: {res['was'].replace('_', ' ')} -> {res['state'].upper()}, "
                  f"recorded against {res['state_changed_by']}")
        # THE DENOMINATOR OF THE HOLD, on the same line as the verdict. A hold over zero open
        # items stopped nothing, and the operator should read that here rather than discover it.
        print(f"  {res['open_items']} open work item(s) are under it")
        note = _throttle_note(wired)
        if note:
            print(f"\n{note}", file=sys.stderr)
        else:
            print("  the claim path excludes them by NOT EXISTS: "
                  "swarm_engine/transitions.py _PROJECT_BUDGET_CTE (task 0430)")
        return OK

    if action == "resume":
        res = store.apply("project resume", slug=args.slug, reason=args.reason or "",
                          by=args.by or "", as_operator=True)
        if args.json:
            out_json(res)
            return OK
        if res["already"]:
            print(f"{res['slug']} was already in progress.")
        else:
            print(f"{res['slug']}: {res['was'].upper()} -> in progress, by "
                  f"{res['state_changed_by']}: {res['state_reason']}")
        print(f"  {res['open_items']} open work item(s) are claimable again")
        return OK

    if action in ("archive", "unarchive"):
        # MIGRATION 47, bus row 0443, decision D-05. The operator named four states and none of
        # them is terminal, so without this a project entered on the board stays on it forever.
        # The stamp is orthogonal to his four words: a project archived while `blocked` is still
        # blocked, and that is why a delegate could take this answer and could not have taken a
        # fifth word.
        if action == "archive":
            res = store.apply("project archive", slug=args.slug, reason=args.reason or "",
                              by=args.by or "")
        else:
            res = store.apply("project unarchive", slug=args.slug, by=args.by or "",
                              as_operator=True)
        if args.json:
            out_json({**res, "throttle": wired})
            return OK
        if action == "archive":
            if res["already"]:
                print(f"{res['slug']} was ALREADY archived "
                      f"{res['archived_at']:%Y-%m-%d %H:%M}Z by {res['archived_by']}: "
                      f"{res['archived_reason']}")
            else:
                print(f"{res['slug']} ARCHIVED by {res['archived_by']}: "
                      f"{res['archived_reason']}")
            # THE DENOMINATOR AGAIN, and here it is the whole point. An archive that left work
            # claimable would be the eight-terminals incident in a new place, so the number of
            # items it just took out of the claim path is printed beside the verdict.
            print(f"  {res['open_items']} open work item(s) leave the claim path with it")
            print(f"  its state is untouched: still {res['state'].replace('_', ' ')}. Archiving "
                  f"is not cancelling and nothing was removed.")
            print(f"  `swarm project unarchive {res['slug']}` puts it back, and that needs the "
                  f"operator login")
        elif res["already"]:
            print(f"{res['slug']} was not archived.")
        else:
            print(f"{res['slug']} is back on the board, {res['state'].replace('_', ' ')}, "
                  f"by {res['by']}")
            print(f"  it was archived {res['was_archived_at']:%Y-%m-%d %H:%M}Z by "
                  f"{res['was_archived_by']}: {res['was_archived_reason']}")
            print(f"  {res['open_items']} open work item(s) are claimable again")
        return OK

    if action == "retitle":
        res = store.apply("project retitle", slug=args.slug, title=args.title or "")
        if args.json:
            out_json(res)
            return OK
        print(f"{res['slug']}: {res['was']!r} -> {res['title']!r}")
        print(f"  the state stamp is untouched: still {res['state']} as of "
              f"{res['state_changed_at']:%Y-%m-%d %H:%M}Z")
        return OK

    if action == "rm":
        res = store.apply("project rm", slug=args.slug, by=args.by or "", as_operator=True)
        if args.json:
            out_json(res)
            return OK
        print(f"project {res['slug']} ({res['title']!r}) DELETED by {res['deleted_by']}")
        return OK

    if action == "attach":
        if not args.task:
            die("`swarm project attach <slug> --task <id>` needs the task to file.")
        res = store.apply("project attach", id=_tid(args.task), slug=args.slug,
                          agent=args.by or "")
        if args.json:
            out_json(res)
            return OK
        if res["already"]:
            print(f"{res['id']} is already filed under {res['project']}.")
            return OK
        print(f"{res['id']} filed under {res['project']} ({res['state'].replace('_', ' ')})"
              + (f", moved off {res['was']}" if res["was"] else ""))
        if res["left_resting"]:
            # STILL REPORTED, AND NOW ONLY REACHABLE BY A HUMAN. Migration 44 left this hatch open
            # and migration 46 (bus row 0444) closed it: an agent moving work off a RESTING
            # project is refused by the verb and by a trigger, and neither trusts the other. The
            # operator is exempt, because it is his board -- so if this line prints at all, he did
            # it himself, and what the surface owes him is that the consequence is visible.
            print(f"  it was under {res['left_resting']['slug']}, which is "
                  f"{res['left_resting']['state']}. Moving work off a resting project takes THAT "
                  f"ITEM out from under the project's throttle, so an agent may claim it again "
                  f"while the project still reads {res['left_resting']['state']} on the board. "
                  f"Only your own login can do this: migration 46 refuses every other "
                  f"connection.", file=sys.stderr)
        return OK

    die(f"unknown project action {action!r}")


# ------------------------------------------------------------------ routines
#
# The verbs are thin, per the narrow waist: every one that changes state is `store.apply(...)`
# and this file owns none of them. `tick` is the whole scheduler and it is a single pass with no
# loop in it, because the clock is `systemd/brain-routine-tick.timer` and a resident scheduler
# would be a second thing with an opinion about what time it is.


def _routine_line(r):
    when = f"every {r['period_minutes']}m from {r['anchor_at']:%Y-%m-%d %H:%M}Z"
    if r.get("disabled_at"):
        # COLOUR NEVER RIDES ALONE, and here there is no colour at all: the word DISABLED is the
        # whole signal, followed by who and why, because "why is this dark" is the only question
        # anybody asks about a routine that stopped.
        return (f"  DISABLED  {r['name']:<24} {when}\n"
                f"            off since {r['disabled_at']:%Y-%m-%d %H:%M}Z by "
                f"{r['disabled_by']}: {r['disabled_reason']}")
    tail = f"  last {r['last_fired_at']:%m-%d %H:%M}Z" if r.get("last_fired_at") else "  never run"
    return (f"  armed     {r['name']:<24} {when}  slot "
            f"{r['current_slot']:%m-%d %H:%M}Z  runs {r.get('runs', 0)}{tail}")


def cmd_routine(args):
    action = args.action

    if action == "list":
        rows = routines_mod.listing()
        if args.json:
            out_json(rows)
            return OK if rows else EMPTY
        if not rows:
            # BELOW LEDGER 34 `routines_mod.listing()` has already said so on stderr, once.
            # "no routines" and "this store cannot hold one" are different facts and a reader
            # who sees only this line must not read the second as the first.
            print("no routines, over 0 rows in brain.routine. "
                  "`swarm routine add --help` says what one needs.")
            return EMPTY
        for r in rows:
            print(_routine_line(r))
        live = sum(1 for r in rows if not r["disabled_at"])
        print(f"\n{live} armed, {len(rows) - live} disabled, over {len(rows)} routines")
        return OK

    if action == "runs":
        rows = routines_mod.runs(args.name, limit=args.limit)
        if args.json:
            out_json(rows)
            return OK if rows else EMPTY
        if not rows:
            print(f"{args.name} has decided no slots yet, over 0 occurrences")
            return EMPTY
        for r in rows:
            print(f"  {r['scheduled_for']:%Y-%m-%d %H:%M}Z  {r['outcome']:<8} "
                  f"{r['work_item_id'] or '':<6} {r['note']}")
        print(f"\n{len(rows)} occurrences, newest first")
        return OK

    if action == "due":
        rows = routines_mod.due()
        if args.json:
            out_json(rows)
            return OK if rows else EMPTY
        armed = routines_mod.armed()
        if not rows:
            print(f"nothing due, over {len(armed)} armed routines")
            return EMPTY
        for r in rows:
            print(f"  {r['name']:<24} slot {r['current_slot']:%Y-%m-%d %H:%M}Z"
                  + ("  PAST GRACE, would be recorded as missed" if r["past_grace"] else ""))
        print(f"\n{len(rows)} due, over {len(armed)} armed routines")
        return OK

    if action == "tick":
        res = routines_mod.tick(dry_run=not args.fire)
        if args.json:
            out_json(res)
            return ERR if res["refused"] else OK
        armed = len(routines_mod.armed())
        if res["paused"]:
            print(f"FLEET PAUSED: dispatched nothing, over {res['eligible']} due of {armed} "
                  f"armed routines. `swarm resume` lifts it.")
            return OK
        for f in res["fired"]:
            if f.get("dry_run"):
                print(f"  would {f['would']:<7} {f['routine']:<24} "
                      f"slot {f['scheduled_for']:%Y-%m-%d %H:%M}Z")
            else:
                print(f"  {f['outcome']:<8} {f['routine']:<24} "
                      f"slot {f['scheduled_for']:%Y-%m-%d %H:%M}Z "
                      f"{f['work_item_id'] or ''} {f['note']}")
        for x in res["refused"]:
            print(f"  REFUSED  {x['routine']}: {x['reason']}")
        word = "would fire" if res["dry_run"] else "decided"
        print(f"\n{word} {len(res['fired'])}, refused {len(res['refused'])}, over "
              f"{res['eligible']} due of {armed} armed routines"
              + ("   (dry run: pass --fire to dispatch)" if res["dry_run"] else ""))
        return ERR if res["refused"] else OK

    if action == "add":
        body = args.body or ""
        if args.file:
            body = sys.stdin.read() if args.file == "-" else Path(args.file).read_text()
        res = store.apply(
            "routine add", name=args.name, title=args.title, lane=args.lane, body=body,
            workdir=args.workdir or "", parent=_tid(args.parent) or "",
            priority=args.priority, max_attempts=args.max_attempts,
            agent_claimable=bool(args.for_agents),
            external=truthy(args.external), canon_touching=truthy(args.canon_touching),
            period_minutes=routines_mod.parse_period(args.every),
            anchor_at=routines_mod.parse_anchor(args.at),
            misfire_grace_minutes=args.grace, skip_if_open=not args.allow_overlap,
            by=args.by or "", as_operator=True)
        if args.json:
            out_json(res)
            return OK
        print(f"routine {res['name']} armed: every {res['period_minutes']} minutes, "
              f"next slot {res['next_slot']:%Y-%m-%d %H:%M}Z")
        print(f"disable it with: swarm routine disable {res['name']} --reason '...'")
        return OK

    if action == "disable":
        res = store.apply("routine disable", name=args.name, by=args.by or "operator",
                          reason=args.reason or "")
        if args.json:
            out_json(res)
            return OK
        if res.get("already"):
            print(f"{res['name']} was ALREADY disabled at {res['disabled_at']:%Y-%m-%d %H:%M}Z "
                  f"by {res['disabled_by']}: {res['disabled_reason']}")
        else:
            print(f"{res['name']} DISABLED. It will not fire again until somebody re-arms it, "
                  f"which needs the operator login.")
        return OK

    if action == "enable":
        res = store.apply("routine enable", name=args.name, by=args.by or "", as_operator=True)
        if args.json:
            out_json(res)
            return OK
        if res.get("already"):
            print(f"{res['name']} was already armed.")
        else:
            print(f"{res['name']} ARMED again (it was off for: {res['was_disabled_for']}). "
                  f"Next slot {res['next_slot']:%Y-%m-%d %H:%M}Z")
        return OK

    if action == "fire":
        res = store.apply("routine fire", name=args.name, by=args.by or "operator")
        if args.json:
            out_json(res)
            return OK
        print(f"{res['routine']} slot {res['scheduled_for']:%Y-%m-%d %H:%M}Z: {res['outcome']} "
              f"{res['work_item_id'] or ''} {res['note']}")
        return OK

    die(f"unknown routine action {action!r}")


# ------------------------------------------------------------------ admin
#
# THE ADMIN VERB GROUP. Every write below goes through `store.apply`, which is the whole design:
# an admin CLI that changed a setting by editing `config.json` would be the second writer
# `store/narrow_waist.md` exists to prevent. `engine/swarm_engine/admin.py` holds the transitions
# and the argument; this file holds only the door.
#
# UNIFORM `--json` IS A RULE HERE AND NOT A HABIT. Measured on 2026-08-27 across the 48 verbs that
# predate this group: 48/48 accept `--json` because `add()` attaches it to every subparser, and
# 26/48 handlers never read it. Every handler below reads it, and `swarm admin manifest` reports
# the ratio rather than asserting it, so a regression shows up as a number.


def _refuse_admin_from_a_fleet_terminal(verb: str):
    """Refuse an admin WRITE inside a fleet terminal. IT IS NOT A SECURITY BOUNDARY.

    Same shape, same limit and the same honesty as `_refuse_as_operator_from_a_fleet_terminal`,
    which the docstring above this one spells out at length: `env -u SWARM_AGENT -u
    SWARM_PARENT_TASK` walks past it, and none of these acts needs this CLI at all, because on a
    `local-attended` host every fleet process runs as the operator's own Unix user and can read
    the operator credential out of a 0600 file.

    What it does stop is the accident that actually happens. On 2026-08-18 an admiral pass
    promoted rows the operator had deliberately held, while being helpful. An admin verb group is
    a strictly larger version of that hazard: a terminal that decided the fleet would run better
    with different signal weights, or with one more agent, would be helpful in exactly the same
    way and would re-point the queue for everybody. Making it a deliberate act is the whole of
    what this buys, and it leaves a legible refusal on the way.

    READS ARE NOT REFUSED. `swarm admin config get`, `agent list`, `role list`, `human list`,
    `secret list`, `history` and `manifest` are how a terminal finds out what it is running, and
    a terminal that cannot ask is a terminal that guesses.
    """
    fleet = os.environ.get("SWARM_AGENT", "").strip()
    parent = os.environ.get("SWARM_PARENT_TASK", "").strip()
    if not (fleet or parent):
        return
    die(f"refusing `swarm admin {verb}` from a fleet terminal "
        f"(SWARM_AGENT={fleet or '(unset)'} SWARM_PARENT_TASK={parent or '(unset)'}). "
        f"This verb changes what the FLEET runs, or who may write to this store, for everybody. "
        f"Deciding that is the operator's, and a terminal that believes a setting is wrong should "
        f"say so on its task thread or `swarm ask` it.\n"
        f"swarm: THIS IS A DETERRENT, NOT A BOUNDARY, and you should know how thin it is. "
        f"`env -u SWARM_AGENT -u SWARM_PARENT_TASK` defeats it, and on this host the operator "
        f"credential is a 0600 file every fleet process can read. What stops you is that you are "
        f"not supposed to. What the DATABASE enforces is real and narrower: a connection as "
        f"`brain_runtime` cannot write brain.config_setting at all, because migration 40's "
        f"trigger refuses any session brain.current_human() does not recognise. "
        f"See store/SECRETS.md, `What --as-operator gates on today`.", code=6)


def _admin_die(e):
    die(str(e), getattr(e, "code", ERR))


def _admin_apply(verb, **kw):
    """`store.apply` for the admin group, with the un-applied-migration case named as itself.

    Every admin write goes through here, so the "this store is below migration 40" sentence is
    written once. A raw 42P01 out of `die_db` reads as "this one is a defect, not a rule", and
    telling an operator that his own un-applied migration is our bug is the one rendering that
    gets a correct refusal ignored.
    """
    kw.setdefault("as_operator", True)
    try:
        return store.apply(verb, **kw)
    except psycopg2.Error as e:
        if _below_40(e):
            _die_below_40(e)
        raise


def cmd_admin_manifest(args):
    from . import manifest as manifest_mod
    m = manifest_mod.build(build_parser())
    if args.json:
        out_json(m)
    else:
        for line in manifest_mod.render(m):
            print(line)
    return OK


def cmd_admin_config_get(args):
    """Resolved config, per key, WITH ITS SOURCE. The question `swarm config` cannot answer."""
    from .config import config as file_config
    file_cfg = file_config()
    rows = admin_mod.layer()
    eff = admin_mod.fold(file_cfg, rows)
    if args.key:
        info = admin_mod.layers_for(args.key, file_cfg, eff, rows)
        if args.json:
            out_json(info)
        else:
            print(f"{info['key']} = {json.dumps(info['in_force'], default=str)}   "
                  f"[{info['source']}]")
            print(f"  file    {json.dumps(info['file'], default=str)}")
            print(f"  store   {json.dumps(info['store'], default=str)}"
                  + (f"   set by {info['set_by']} at {info['set_at']}"
                     if info["set_by"] else ""))
            if info["note"]:
                print(f"  note    {info['note']}")
        return OK if info["in_force"] is not None else EMPTY
    infos = admin_mod.fleet_key_report(file_cfg, rows)
    from_store = [i for i in infos if i["source"] == "store"]
    if args.json:
        out_json({"keys": infos, "counted": len(infos), "from_store": len(from_store)})
    else:
        for i in infos:
            print(f"{i['key']:<32} {json.dumps(i['in_force'], default=str):<28} [{i['source']}]"
                  + (f"  {i['set_by']}" if i["set_by"] else ""))
        print(f"\n{len(from_store)}/{len(infos)} fleet keys in force come from the store layer; "
              f"the rest come from {config_path()} or from the code default.")
    return OK if infos else EMPTY


def cmd_admin_config_set(args):
    _refuse_admin_from_a_fleet_terminal("config set")
    try:
        r = _admin_apply("admin config set", scope=args.scope, key=args.key, value=args.value,
                        note=args.note, new_key=args.new_key, as_operator=True)
    except (VerbError, ValueError) as e:
        _admin_die(e)
    if args.json:
        out_json(r)
    else:
        print(f"{r['scope']}/{r['key']} = {json.dumps(r['value'], default=str)}"
              f"   (was {json.dumps(r['was'], default=str)}), set by {r['set_by']}")
        print("Recorded in brain.admin_change. `swarm admin history --key "
              f"{r['key']}` shows the trail; `swarm config` now resolves it.")
    return OK


def cmd_admin_config_unset(args):
    _refuse_admin_from_a_fleet_terminal("config unset")
    try:
        r = _admin_apply("admin config unset", scope=args.scope, key=args.key, note=args.note,
                        as_operator=True)
    except (VerbError, ValueError) as e:
        _admin_die(e)
    if args.json:
        out_json(r)
    else:
        print(f"{r['scope']}/{r['key']} override withdrawn (was "
              f"{json.dumps(r['was'], default=str)}), by {r['set_by']}. "
              f"The file's value stands again.")
    return OK


def _below_40(e) -> bool:
    """Is this a store that has not had migration 40 applied, rather than a defect?

    `docs/SCHEMA-TOLERANCE.md`: the ledger of the store your code will open is not the tip of
    `migrations/`, in either direction. A surface that did not exist before a migration refuses
    in a SENTENCE (doctrine rule 5), and `die_db` cannot tell the two apart: an UndefinedTable
    arrives with SQLSTATE 42P01 and gets printed as "this one is a defect, not a rule", which
    tells the operator his own un-applied migration is our bug.
    """
    return getattr(e, "pgcode", "") in ("42P01", "42883") and (
        "admin_change" in str(e) or "config_setting" in str(e)
        or "config_in_force" in str(e) or "human_roster" in str(e)
        or "human_login_ceiling" in str(e))


def _die_below_40(e):
    die(f"this store ({os.environ.get('BRAIN_PG_DB', 'brain')}) is below migration 40: "
        f"{str(e).strip().splitlines()[0]}. The admin verb group's tables are "
        f"migrations/0040_admin_config_provenance.sql and "
        f"migrations/0041_human_roster_and_ceiling.sql, and no admin change can have been "
        f"recorded on a store that has neither. Apply them with:\n"
        f"       psql -d {os.environ.get('BRAIN_PG_DB', 'brain')} "
        f"-f migrations/0040_admin_config_provenance.sql\n"
        f"       psql -d {os.environ.get('BRAIN_PG_DB', 'brain')} "
        f"-f migrations/0041_human_roster_and_ceiling.sql", code=2)


def cmd_admin_history(args):
    try:
        rows = admin_mod.history(scope=args.scope or "", key=args.key or "", limit=args.limit)
    except psycopg2.Error as e:
        if _below_40(e):
            _die_below_40(e)
        raise
    if args.json:
        out_json({"changes": rows, "counted": len(rows), "limit": args.limit})
        return OK if rows else EMPTY
    if not rows:
        print(f"no admin changes recorded (0 rows matched"
              + (f" scope={args.scope}" if args.scope else "")
              + (f" key={args.key}" if args.key else "") + ")")
        return EMPTY
    for r in rows:
        when = str(r["changed_at"])[:19]
        old = "" if r["old_value"] is None else r["old_value"]
        new = "(unset)" if r["new_value"] is None else r["new_value"]
        print(f"{when}  {r['changed_by']:<12} {r['verb']:<22} {r['scope']}/{r['key']}  "
              f"{old or '(unset)'} -> {new}")
        if r["note"]:
            print(f"{'':<21}  note: {r['note']}")
    print(f"\n{len(rows)} change(s) shown, limit {args.limit}.")
    return OK


def cmd_admin_agent_list(args):
    from .config import config as file_config, is_planner
    file_cfg = file_config()
    rows = admin_mod.layer()
    eff = admin_mod.fold(file_cfg, rows)
    in_file = {a.get("name") for a in (file_cfg.get("agents") or []) if isinstance(a, dict)}
    in_force = [a for a in (eff.get("agents") or []) if isinstance(a, dict)]
    store_rows = {r["key"]: r for r in rows if r["scope"] == admin_mod.ROSTER_SCOPE}
    listing = []
    for a in in_force:
        n = a.get("name")
        listing.append({"name": n, "role": a.get("role", ""), "lanes": a.get("lanes", []),
                        "model": a.get("model", ""), "planner": is_planner(a.get("role", "")),
                        "in_file": n in in_file, "in_store": n in store_rows,
                        "source": ("store" if n in store_rows and n not in in_file else
                                   "file+store" if n in store_rows else "file")})
    removed = [k for k, r in store_rows.items()
               if json.loads(r["value"]).get(admin_mod.ENABLED) is False]
    if args.json:
        out_json({"agents": listing, "counted": len(listing), "in_file": len(in_file),
                  "removed_by_store": removed})
        return OK if listing else EMPTY
    for a in listing:
        print(f"{a['name']:<12} {a['role']:<10} {a['model'] or '-':<8} "
              f"lanes {len(a['lanes']) if a['lanes'] != ['*'] else 'all':<4} [{a['source']}]"
              + ("  planner, never claims" if a["planner"] else ""))
    print(f"\n{len(listing)} agent(s) in force; {len(in_file)} in {config_path()}, "
          f"{len(store_rows)} store row(s), {len(removed)} removed by the store layer"
          + (f": {', '.join(removed)}" if removed else "."))
    return OK if listing else EMPTY


def _kv_pairs(pairs):
    out = {}
    for p in pairs or []:
        if "=" not in p:
            die(f"--set takes key=value, got {p!r}", code=6)
        k, v = p.split("=", 1)
        try:
            out[k.strip()] = json.loads(v)
        except ValueError:
            out[k.strip()] = v
    return out


def cmd_admin_agent_add(args):
    _refuse_admin_from_a_fleet_terminal("agent add")
    values = _kv_pairs(args.set)
    if args.values:
        try:
            values.update(json.loads(args.values))
        except ValueError:
            die("--values must be a JSON object", code=6)
    try:
        r = _admin_apply("admin agent add", name=args.name, values=values, note=args.note,
                        as_operator=True)
    except (VerbError, ValueError) as e:
        _admin_die(e)
    if args.json:
        out_json(r)
    else:
        print(f"{r['agent']} in the roster with {len(r['values'])} store-set key(s), "
              f"by {r['set_by']}"
              + ("  (amending the entry the config file already has)" if r["in_file"] else ""))
        print(f"applies {r['applies']}")
    return OK


def cmd_admin_agent_remove(args):
    _refuse_admin_from_a_fleet_terminal("agent remove")
    try:
        r = _admin_apply("admin agent remove", name=args.name, note=args.note, as_operator=True)
    except (VerbError, ValueError) as e:
        _admin_die(e)
    if args.json:
        out_json(r)
    else:
        print(f"{r['agent']} removed from the effective roster by {r['set_by']}"
              + ("  (it is still in the config file; the store layer overrides it)"
                 if r["in_file"] else ""))
        print(f"applies {r['applies']}")
    return OK


def cmd_admin_role_list(args):
    """Every database role this store uses, and which of them the database calls a human.

    THE READ LANE E WAS MISSING. Measured on live `brain` 2026-08-27: `brain.human_role` is
    permission denied to brain_runtime AND to brain_operator, and readable only by brain_owner,
    for which `brain.current_human()` returns NULL. So no single login could both read the roster
    and pass the human gate. Migration 41's `brain.human_roster()` is the door and this is the
    verb over it.
    """
    with store.read() as s:
        roles = s.query(
            """SELECT r.rolname, r.rolcanlogin, r.rolsuper,
                      ARRAY(SELECT b.rolname FROM pg_auth_members m
                              JOIN pg_roles b ON b.oid = m.roleid
                             WHERE m.member = r.oid ORDER BY 1) AS member_of
                 FROM pg_roles r
                WHERE r.rolname LIKE 'brain\\_%' ORDER BY r.rolname""")
        try:
            humans = {h["role_name"]: h["human"] for h in s.query(
                "SELECT * FROM brain.human_roster()")}
            door = "brain.human_roster()"
        except Exception:                                               # noqa: BLE001
            humans, door = {}, "UNAVAILABLE (this store is below migration 41)"
    for r in roles:
        r["human"] = humans.get(r["rolname"], "")
    if args.json:
        out_json({"roles": roles, "counted": len(roles), "humans": len(humans),
                  "roster_door": door})
        return OK if roles else EMPTY
    for r in roles:
        print(f"{r['rolname']:<28} {'login' if r['rolcanlogin'] else 'nologin':<8} "
              f"{('human:' + r['human']) if r['human'] else 'agent':<20} "
              f"member of {', '.join(r['member_of']) or '-'}")
    print(f"\n{len(roles)} brain_* role(s) in this cluster, {len(humans)}/{len(roles)} mapped as "
          f"humans in this database. Roster read through {door}.")
    return OK


def cmd_admin_human_list(args):
    with store.read() as s:
        try:
            rows = s.query("SELECT * FROM brain.human_roster()")
            cap = int(s.scalar("SELECT brain.human_login_ceiling()"))
        except Exception as e:                                          # noqa: BLE001
            die(f"this store cannot answer: {e.__class__.__name__}. "
                f"brain.human_roster() and brain.human_login_ceiling() are migration 41 "
                f"(migrations/0041_human_roster_and_ceiling.sql). Apply it with: "
                f"psql -d {os.environ.get('BRAIN_PG_DB', 'brain')} "
                f"-f migrations/0041_human_roster_and_ceiling.sql")
    if args.json:
        out_json({"humans": rows, "counted": len(rows), "ceiling": cap,
                  "remaining": max(0, cap - len(rows))})
        return OK if rows else EMPTY
    for r in rows:
        print(f"{r['human']:<16} {r['role_name']:<24} granted {str(r['granted_at'])[:19]} "
              f"by {r['granted_by']}")
    print(f"\n{len(rows)}/{cap} human login(s), {max(0, cap - len(rows))} below the stated "
          f"ceiling. A thirteenth reopens the decision (row 0386, decision 2); it is never a "
          f"quiet migration.")
    return OK if rows else EMPTY


def _one_line(text: str, rc: int) -> str:
    """The executor's answer in one line, for a trail other rows have to share a page with.

    psql result tables, rule lines and `(N rows)` are dropped: they are formatting, and a note
    that is mostly formatting hides the sentence a reader came for.
    """
    skip = ("-+-", "(1 row)", "(0 rows)")
    lines = [ln.strip() for ln in (text or "").splitlines()
             if ln.strip() and not ln.strip().startswith("-")
             and not any(s in ln for s in skip)
             and not ln.strip().endswith("rows)") and "|" not in ln]
    return f"rc={rc}: " + (lines[-1] if lines else "the executor printed nothing")


def cmd_admin_human_provision(args):
    """Two acts, and this verb is honest about being two rather than pretending to be one.

    `store.apply` opens one of the five non-superuser logins in `store/session.py:ROLES`, and
    `CREATE ROLE ... PASSWORD` needs a privilege none of them has and none of them should have.
    So the intent and the outcome are transactions, and the minting is a privileged executor
    between them. An interrupted run leaves a plan with no confirm, which `swarm admin history`
    shows and which is a better failure than a silent one.
    """
    _refuse_admin_from_a_fleet_terminal("human provision")
    try:
        plan = _admin_apply("admin human plan", slug=args.slug, note=args.note, as_operator=True)
    except (VerbError, ValueError) as e:
        _admin_die(e)
    if plan["already"]:
        msg = (f"{plan['slug']} is already mapped as {plan['role_name']} "
               f"({plan['humans']}/{plan['ceiling']} human logins). Nothing minted. Pass "
               f"--rotate to mint a new password for an existing human.")
        if not args.rotate:
            if args.json:
                out_json({**plan, "outcome": "already_provisioned"})
            else:
                print(msg)
            return OK

    db = os.environ.get("BRAIN_PG_DB", "brain")
    script = str(Path(__file__).resolve().parents[2] / "store" / "bin" / "provision-human.sh")
    cmd = ["bash", script, "--db", db, "--human", plan["slug"]]
    if args.rotate:
        cmd.append("--rotate")
    import subprocess
    proc = subprocess.run(cmd, capture_output=True, text=True)
    detail = (proc.stdout + proc.stderr).strip()
    outcome = "provisioned" if proc.returncode == 0 else f"failed rc={proc.returncode}"
    try:
        # ONE LINE ON THE TRAIL, not the executor's whole stdout. The executor prints a psql
        # result table, and pasting it into `note` makes `swarm admin history` unreadable for
        # every OTHER row on the page. The full output goes to the terminal and to --json, where
        # it is one call's answer rather than a permanent fixture in a shared trail.
        conf = _admin_apply("admin human confirm", slug=plan["slug"],
                           role_name=plan["role_name"], outcome=outcome,
                           detail=_one_line(
                               # A success speaks on stdout and a refusal speaks on stderr, so
                               # the trail takes whichever stream actually carries the answer.
                               # Reading the concatenation picked up a psql NOTICE about a grant
                               # that was already held, which is true and is not the sentence.
                               proc.stdout if proc.returncode == 0 else (proc.stderr or
                                                                         proc.stdout),
                               proc.returncode),
                           as_operator=True)
    except (VerbError, ValueError) as e:
        # The privileged half may well have landed. Say so rather than reporting a clean failure.
        die(f"the privileged half exited {proc.returncode} and the outcome could NOT be "
            f"recorded: {e}. brain.admin_change holds the PLAN and no confirm, which is exactly "
            f"what that gap is for. Executor output:\n{detail}", code=ERR)
    payload = {**plan, **conf, "executor_rc": proc.returncode,
               "secret_ref": plan.get("secret_ref", ""),
               "executor_output": detail}
    if args.json:
        out_json(payload)
    else:
        print(detail)
        print(f"\n{conf['outcome']}: {plan['slug']} as {plan['role_name']}, confirmed by "
              f"{conf['confirmed_by']}, mapping visible to brain.human_roster(): "
              f"{'yes' if conf['mapped'] else 'NO'}")
        print(f"credential reference {plan.get('secret_ref', '')} in the 0600 backend. "
              f"THE VALUE IS NOT PRINTED HERE AND IS IN NO COLUMN.")
        print(f"That login writes through the store with BRAIN_HUMAN={plan['slug']} in its "
              f"environment, or as_human={plan['slug']!r} on an apply().")
    return OK if proc.returncode == 0 else ERR


def cmd_admin_human_revoke(args):
    _refuse_admin_from_a_fleet_terminal("human revoke")
    from store.session import human_role_name
    role_name = args.role or human_role_name(args.slug)
    try:
        r = _admin_apply("admin human revoke", role_name=role_name, note=args.note, as_operator=True)
    except (VerbError, ValueError) as e:
        _admin_die(e)
    if args.json:
        out_json(r)
    else:
        print(f"{r['human']} ({r['role_name']}) is no longer a human in this database, "
              f"revoked by {r['revoked_by']}")
        print(f"STILL TRUE: {r['still_true']}")
    return OK


def cmd_admin_secret_list(args):
    """Names, presence and modes. NEVER a value, and there is no flag that prints one.

    Composition rather than re-implementation: `store/bin/secret-preflight.py` derives the
    required set from three rules and carries no count of its own, which is the whole point of
    that file. This verb calls its `audit()` and adds nothing.
    """
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "store" / "bin" / "secret-preflight.py"
    spec = importlib.util.spec_from_file_location("_secret_preflight", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    db = args.db or os.environ.get("BRAIN_PG_DB", "brain")
    r = mod.audit(db, os.environ.get("BRAIN_PG_CONTAINER", "brain-postgres"))
    if args.json:
        out_json(r)
    else:
        print(f"secret backend {r['backend']} (mode {r['backend_mode']}), database "
              f"{r['database']}")
        for x in r["required"]:
            print(f"  [{'ok     ' if x['present'] else 'MISSING'}] {x['ref']}"
                  + (f" 0{x['mode']}" if x["mode"] else ""))
            print(f"            resolved by: {x['resolved_by']}")
        if r["unrequired_in_backend"]:
            print("  in the backend and not required by this database: "
                  + ", ".join(r["unrequired_in_backend"]))
        print(f"\n{r['present_count']}/{r['required_count']} required reference(s) resolve; "
              f"{r['in_backend_count']} file(s) in the backend. "
              f"VALUES ARE NEVER READ, PRINTED OR RETURNED by this verb.")
        if r["subscriber_enumeration_failed"]:
            print(f"WARNING: {r['subscriber_enumeration_failed']}")
    return ERR if r["missing"] else OK


# ------------------------------------------------------------------ parser

def build_parser():
    p = argparse.ArgumentParser(prog="swarm", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, **kw):
        s = sub.add_parser(name, **kw)
        s.add_argument("--json", action="store_true", help="machine-readable output")
        s.set_defaults(fn=fn)
        return s

    add("init", cmd_init, help="apply the schema and write a config")

    s = add("post", cmd_post, help="create a task")
    s.add_argument("--lane", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--host", help="pin this task to one machine, by the agent host label")
    s.add_argument("--body")
    s.add_argument("-f", "--file", help="read the brief from a file, or - for stdin")
    s.add_argument("--priority", type=int, default=3)
    s.add_argument("--workdir")
    s.add_argument("--depends-on")
    s.add_argument("--posted-by", default="operator")
    s.add_argument("--max-attempts", type=int, default=2)
    s.add_argument("--parent", help="the task this one was derived from. Its two hard flags are "
                                    "inherited by OR and can be raised here, never lowered")
    s.add_argument("--canonical-task", help="<project-slug>#<task-id> on the git planning ladder")
    # NOT the same fact as --canonical-task, which is a foreign task id on the git planning
    # ladder. This is the foreign key into brain.project (migration 44), and it is what
    # `budget stop project <slug>` and the claim-path project gate actually read. A row may carry
    # either, or neither; migration 44's work_item_project_matches_canonical stops them
    # disagreeing when it carries both.
    s.add_argument("--project", help="project slug this work belongs to (brain.project.slug). "
                                     "What a project HOLD throttles; canonical_task is not read "
                                     "by the hold")
    for flag, canon in (("stakes", "or critical"),
                        ("reversibility", "or reversible, costly, irreversible"),
                        ("urgency", "or none, soon, deadline, decaying"),
                        ("dependency-unblocking", "or a count"),
                        ("effort", "or small, large"), ("confidence", "or 0.0 to 1.0"),
                        ("charter-alignment", ""),
                        # Migration 48, row 0434. The one signal that takes a MAGNITUDE. A bare
                        # number here is MONEY and never a one-to-five rating; anything from 1 to
                        # 99 is refused out loud because it reads as both.
                        ("impact", "or critical, or an amount of money like 25000")):
        s.add_argument(f"--{flag}", default="", help=f"low, medium or high {canon}".strip())
    s.add_argument("--external", nargs="?", const="true", default="",
                   help="hard flag: sends, deploys, spends or publishes. Always surfaces")
    s.add_argument("--canon-touching", nargs="?", const="true", default="",
                   help="hard flag: proposes a change to canon. Always surfaces")
    s.add_argument("--for-agents", action="store_true",
                   help="the FLEET should do this: agent_claimable=true, so `claim` may hand it "
                        "out. Default is held, because this table is also the operator's own "
                        "queue and a row nobody classified is his. A task posted with --parent "
                        "inherits its parent's answer and needs no flag")
    s.add_argument("--mine", action="store_true",
                   help="the operator's OWN work: actor_type=human, so it surfaces in his queue "
                        "and he alone can mark it done. Refused unless this process holds the "
                        "operator credential (store/bin/provision-operator.sh), so a connection "
                        "as `brain_runtime` cannot post one")

    s = add("claim", cmd_claim, help="atomically take the next task")
    s.add_argument("--agent", required=True)
    s.add_argument("--lane", help="comma separated, overrides config")
    s.add_argument("--host", help="this agent's host label, overrides config")
    s.add_argument("--shell", action="store_true", help="emit eval-able shell assignments")
    s.add_argument("--explain", action="store_true",
                   help="on an EMPTY claim, say why when the reason is a budget-stopped lane "
                        "rather than an empty queue. Reads only; writes no incident")

    s = add("done", cmd_done, help="report a task finished. NOT acceptance")
    s.add_argument("id")
    s.add_argument("--summary", required=True)
    s.add_argument("--agent", default="")
    s.add_argument("--force", action="store_true",
                   help="act on a task ANOTHER agent still holds. The override is written to that task's thread, and it needs --agent so it is signed")

    s = add("block", cmd_block, help="park a task")
    s.add_argument("id")
    s.add_argument("--reason", required=True)
    s.add_argument("--agent", default="")
    s.add_argument("--unspent", action="store_true",
                   help="give back the attempt the claim charged. For a run stopped for SPEND "
                        "rather than judged: the row stays BLOCKED (a human clears it), but it "
                        "keeps its full retry ladder. Refused unless brain.budget_incident holds "
                        "a hard_stop or manual_stop naming this task since it was claimed")
    s.add_argument("--force", action="store_true",
                   help="act on a task ANOTHER agent still holds. The override is written to that task's thread, and it needs --agent so it is signed")

    s = add("fail", cmd_fail, help="requeue or block after a failure")
    s.add_argument("id")
    s.add_argument("--reason", required=True)
    s.add_argument("--agent", default="")
    s.add_argument("--force", action="store_true",
                   help="act on a task ANOTHER agent still holds. The override is written to that task's thread, and it needs --agent so it is signed")

    s = add("ask", cmd_ask, help="raise an operator question")
    s.add_argument("question")
    s.add_argument("--from", dest="agent", required=True)
    s.add_argument("--task")
    s.add_argument("--default", required=True,
                   help="what you will do if the operator never answers. Required: it is what "
                        "turns operator silence into a usable answer instead of a stalled lane")

    s = add("reopen", cmd_reopen, help="send a finished task back to the queue, unspent")
    s.add_argument("id")
    s.add_argument("--reason", required=True)
    s.add_argument("--from", dest="agent", default="")
    s.add_argument("--force", action="store_true",
                   help="act on a task ANOTHER agent still holds. The override is written to that task's thread, and it needs --from so it is signed")

    add("objectives", cmd_objectives, help="list objectives waiting for the admiral")

    s = add("accept", cmd_accept, help="move an objective to accepted")
    s.add_argument("name")

    s = add("intake", cmd_intake, help="copy new objectives from a drop folder")
    s.add_argument("--source", required=True)
    s.add_argument("--quiet", action="store_true")
    s.add_argument("--origin", choices=("human", "machine"),
                   help="who is dropping these, when the whole folder has one producer. A file "
                        "may override it with an `origin:` line in a leading --- front matter "
                        "block, which is what a shared folder needs. UNDECLARED READS AS HUMAN "
                        "and therefore surfaces on his intake badge: a machine that wants to be "
                        "quiet has to say so, and a human drop that says nothing still reaches "
                        "him (migration 50)")

    s = add("config", cmd_config, help="print resolved config")
    s.add_argument("--agent")
    s.add_argument("--shell", action="store_true", help="emit eval-able shell assignments")
    s.add_argument("--fingerprint", action="store_true",
                   help="sha256 of the config FILE, so two moments can be compared (task 0273)")
    s.add_argument("--layers", action="store_true",
                   help="for each key, the FILE value, the STORE override and which one is in "
                        "force. The default output stays what it has always been: resolved "
                        "config, one document, no provenance. This is the second question")



    # ---- orient and decide. Nested for the same reason `admin` is: `observation open` and
    # `observation close` are one noun's two state changes, and the transitions they call are
    # named exactly this. A flat `observe`/`close` would put the CLI's vocabulary and the
    # store's registry out of step, and `store.transitions.registered()` is what `admin
    # manifest` reports, so the two are read side by side.
    s_obs = sub.add_parser(
        "observation",
        help="Orient: open and close standing observations against events",
        description="An observation is EF-2's second record: a standing reading of what an "
                    "occurrence MEANS, separate from the occurrence itself because the two are "
                    "written by two different Postgres roles.\n\n"
                    "Until 2026-09-01 these verbs had no surface at all and were reachable only "
                    "by importing fabric.emit from Python, which is why brain.observation held "
                    "five demo rows from 2026-08-16 and nothing after.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    obs_sub = s_obs.add_subparsers(dest="observation_cmd", required=True)

    def addobs(group, name, fn, **kw):
        sp = group.add_parser(name, **kw)
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.set_defaults(fn=fn)
        return sp

    s = addobs(obs_sub, "open", cmd_observation_open,
               help="open a standing observation against one event")
    s.add_argument("--event-seq", type=int, required=True, dest="event_seq",
                   help="the event this observes, by brain.event.event_seq. REQUIRED: an "
                        "observation of nothing is a note, and notes have their own verb")
    s.add_argument("--text", required=True,
                   help="what was observed. Never truncated by the store")
    s.add_argument("--kind", default="",
                   help="a producer's own classifier. `demo` is the fixture kind: five rows "
                        "wear it and none of them are evidence of anything")
    s.add_argument("--subject-type", default="", dest="subject_type")
    s.add_argument("--subject-id", default="", dest="subject_id")
    s.add_argument("--from", dest="producer", default="",
                   help="the producer NAME (an agent slug, or a person). Lands in "
                        "produced_by_producer, never in produced_by, which since migration 17 "
                        "means an entity id and nothing else")
    s.add_argument("--actor-type", dest="actor_type", default=None,
                   choices=("human", "ai", "hybrid"),
                   help="who produced this reading. NULL when not declared")

    s = addobs(obs_sub, "close", cmd_observation_close,
               help="close a standing observation. Exit 2 if it is already closed")
    s.add_argument("id", type=int)
    s.add_argument("--from", dest="producer", default="")

    s_disp = sub.add_parser(
        "disposition",
        help="Decide: record a verdict on an event, optionally answering an observation",
        description="EF-3: event_id is REQUIRED and observation_id is NULLABLE. A disposition "
                    "always answers an occurrence; it does not always answer a standing "
                    "observation.\n\n"
                    "NOTE what this verb is not. `decided_by` here is a free text column and no "
                    "trigger checks it, unlike brain.recommendation.decided_by, which migration "
                    "32 binds to brain.current_human(). A disposition is an analytic verdict "
                    "and not an acceptance: it dispatches nothing and spawns nothing. The only "
                    "path from a proposal to executed work is still `queue accept`.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    disp_sub = s_disp.add_subparsers(dest="disposition_cmd", required=True)

    s = addobs(disp_sub, "record", cmd_disposition_record,
               help="record a verdict on one event")
    s.add_argument("--event-seq", type=int, required=True, dest="event_seq",
                   help="the event being disposed of. REQUIRED by EF-3")
    s.add_argument("--verdict", required=True,
                   help="the reading's conclusion. Open vocabulary on purpose: the store does "
                        "not own what a verdict may say")
    s.add_argument("--rationale", default="", help="why. Never truncated by the store")
    s.add_argument("--observation-id", type=int, default=None, dest="observation_id",
                   help="the standing observation this answers, when there is one. Refused if "
                        "it was opened against a different event")
    s.add_argument("--by", default="",
                   help="the decider, recorded as typed. NOT a login gate: see this group's "
                        "description")
    s.add_argument("--from", dest="producer", default="")
    s.add_argument("--actor-type", dest="actor_type", default=None,
                   choices=("human", "ai", "hybrid"))

    # ---- the admin verb group. Nested, because `admin config set` and `config` are different
    # questions and collapsing them into one verb would make the read-only one look writable.
    s_admin = sub.add_parser(
        "admin",
        help="configuration, roster, roles and secrets. Every write goes through store.apply",
        description="The admin verb group. Every WRITE here is a state change through "
                    "store.apply(verb): transactional, attributed to the database LOGIN rather "
                    "than to an argument, and recorded in brain.admin_change. Every verb honours "
                    "--json.\n\n"
                    "Reads are open to any surface. Writes refuse a connection "
                    "brain.current_human() does not recognise, which is migration 40's trigger "
                    "and not this CLI's opinion.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    admin_sub = s_admin.add_subparsers(dest="admin_group", required=True)

    def addadmin(group, name, fn, **kw):
        sp = group.add_parser(name, **kw)
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.set_defaults(fn=fn)
        return sp

    addadmin(admin_sub, "manifest", cmd_admin_manifest,
             help="what this CLI can do, machine-readable, derived from the parser itself")

    s_hist = addadmin(admin_sub, "history", cmd_admin_history,
                      help="the admin trail: who changed what, when, from what to what")
    s_hist.add_argument("--scope", default="")
    s_hist.add_argument("--key", default="")
    s_hist.add_argument("--limit", type=int, default=50)

    s_cfg = admin_sub.add_parser("config", help="read and write config, with provenance")
    cfg_sub = s_cfg.add_subparsers(dest="admin_config_cmd", required=True)

    s = addadmin(cfg_sub, "get", cmd_admin_config_get,
                 help="one key or all of them, each with the layer it came from")
    s.add_argument("key", nargs="?", default="")

    s = addadmin(cfg_sub, "set", cmd_admin_config_set,
                 help="set one key in the store layer. Recorded and attributed")
    s.add_argument("key")
    s.add_argument("value")
    s.add_argument("--scope", default="fleet",
                   help="`fleet` (default) or `agent:<name>`. The roster is `admin agent add`")
    s.add_argument("--note", default="", help="why. Kept on the row and in the trail")
    s.add_argument("--new-key", action="store_true",
                   help="this key is genuinely new. Without it a key that is in neither the "
                        "carried keys nor the live file is REFUSED, because an override on a "
                        "typo is recorded, looks applied and reaches nothing")

    s = addadmin(cfg_sub, "unset", cmd_admin_config_unset,
                 help="withdraw a store override so the file's value stands again")
    s.add_argument("key")
    s.add_argument("--scope", default="fleet")
    s.add_argument("--note", default="")

    s_ag = admin_sub.add_parser("agent", help="the agent roster")
    ag_sub = s_ag.add_subparsers(dest="admin_agent_cmd", required=True)

    addadmin(ag_sub, "list", cmd_admin_agent_list,
             help="the roster in force, and which layer each entry came from")

    s = addadmin(ag_sub, "add", cmd_admin_agent_add,
                 help="put an agent in the roster, or amend the file's entry for it")
    s.add_argument("name")
    s.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="repeatable. The value is parsed as JSON when it parses, else kept as a "
                        "string, so --set lanes='[\"engine\"]' and --set model=opus both work")
    s.add_argument("--values", default="", help="the whole entry as one JSON object")
    s.add_argument("--note", default="")

    s = addadmin(ag_sub, "remove", cmd_admin_agent_remove,
                 help="take an agent out of the effective roster, including one from the file")
    s.add_argument("name")
    s.add_argument("--note", default="")

    s_role = admin_sub.add_parser("role", help="the database roles the grants rest on")
    role_sub = s_role.add_subparsers(dest="admin_role_cmd", required=True)
    addadmin(role_sub, "list", cmd_admin_role_list,
             help="every brain_* database role, its memberships, and which are humans")

    s_hum = admin_sub.add_parser("human", help="the human logins the acceptance gate rests on")
    hum_sub = s_hum.add_subparsers(dest="admin_human_cmd", required=True)

    addadmin(hum_sub, "list", cmd_admin_human_list,
             help="who this database recognises as a human, against the stated ceiling")

    s = addadmin(hum_sub, "provision", cmd_admin_human_provision,
                 help="mint a named human login: intent, privileged half, outcome, all recorded")
    s.add_argument("slug")
    s.add_argument("--note", default="")
    s.add_argument("--rotate", action="store_true",
                   help="mint a NEW password for a human who already has one. Not the default: "
                        "rotating under a live session is a silent outage")

    s = addadmin(hum_sub, "revoke", cmd_admin_human_revoke,
                 help="remove a human mapping. A DEMOTION, not a lockout, and it says so")
    s.add_argument("slug", nargs="?", default="")
    s.add_argument("--role", default="", help="the role name, when it is not brain_human_<slug>")
    s.add_argument("--note", default="")

    s_sec = admin_sub.add_parser("secret", help="secret REFERENCES. Never a value")
    sec_sub = s_sec.add_subparsers(dest="admin_secret_cmd", required=True)
    s = addadmin(sec_sub, "list", cmd_admin_secret_list,
                 help="every reference this runtime resolves, its presence and its mode")
    s.add_argument("--db", default="")

    s = add("state", cmd_state, help="print just the state of one task")
    s.add_argument("id")

    s = add("tick", cmd_tick, help="does a planner have anything to think about?")
    s.add_argument("--agent", required=True)
    s.add_argument("--min-seconds", type=int, default=300)
    s.add_argument("--commit", action="store_true", help="record that a pass just ran")

    s = add("doctor", cmd_doctor, help="absence detection, exit 1 on a critical finding")
    s.add_argument("--stale-seconds", type=int, default=900)
    s.add_argument("--stuck-seconds", type=int, default=7200)
    s.add_argument("--question-seconds", type=int, default=43200)
    s.add_argument("--blocked-threshold", type=int, default=5)

    s = add("set", cmd_set, help="set one field on a task")
    s.add_argument("id")
    s.add_argument("key")
    s.add_argument("value")
    s.add_argument("--agent", default="",
                   help="who is editing. Recorded on the thread, and it is what `set state` is "
                        "checked against when another agent holds the task. Defaults to "
                        "SWARM_AGENT, then to the operator -- but inside a fleet terminal "
                        "(SWARM_PARENT_TASK set) with neither, `set` refuses rather than filing "
                        "an agent's edit against the operator")
    s.add_argument("--as-operator", action="store_true",
                   help="open the OPERATOR'S database login for this edit. Needed for "
                        "`agent_claimable true`, which hands one of his rows to the fleet and "
                        "which migration 26's trigger refuses to any other login. It opens that "
                        "login, it does not grant it -- but on a local-attended host the fleet "
                        "runs as the operator's own Unix user and CAN read the credential, so "
                        "this is refused from a fleet terminal (SWARM_AGENT or SWARM_PARENT_TASK "
                        "set) as a deterrent, not as a boundary. store/SECRETS.md says what it "
                        "does and does not stop")
    s.add_argument("--force", action="store_true",
                   help="`set state`, or `set agent_claimable false`, on a task ANOTHER agent "
                        "still holds. Written to that task's thread, and for a hold the holder "
                        "is messaged. It holds the row's FUTURE and does NOT stop the run: "
                        "`swarm cancel <id> --agent <you> --force` is what stops a run")

    for name, amend, blurb in (("answer", False, "answer an operator question"),
                               ("reanswer", True, "correct an answer already given")):
        s = add(name, cmd_answer, help=blurb)
        s.add_argument("qid")
        s.add_argument("text", nargs="*", default=[])
        s.add_argument("-f", "--file", help="read the answer from a file, or - for stdin")
        s.add_argument("--requeue", action="store_true",
                       help="requeue the blocked task even if its asker is still heartbeating. "
                            "Only pass this once you know the engine is stopped")
        s.set_defaults(amend=amend)

    s = add("answer-requeue", cmd_answer_requeue,
            help="requeue a blocked task after its engine has been confirmed stopped")
    s.add_argument("id")
    s.add_argument("--from", dest="agent", default="")

    s = add("withdraw", cmd_withdraw,
            help="retire a question WITHOUT answering it, signed by whoever withdraws it")
    s.add_argument("qid")
    s.add_argument("--from", dest="agent", default="",
                   help="who is withdrawing it, by name. Inside a fleet terminal this is "
                        "required: an unsigned withdrawal would be filed as `operator`, who did "
                        "not do it")
    s.add_argument("--reason", default="",
                   help="why it stopped needing an answer. Kept on the question and on the "
                        "task's thread")

    s = add("questions", cmd_questions, help="list open operator questions")
    s.add_argument("--answered", action="store_true")
    s.add_argument("--withdrawn", action="store_true",
                   help="questions retired without an answer, newest first, each with the hand "
                        "that withdrew it")
    s.add_argument("--since", type=int, default=12,
                   help="hours, with --answered or --withdrawn")

    s = add("msg", cmd_msg, help="message another agent")
    s.add_argument("text")
    s.add_argument("--from", dest="agent", required=True)
    s.add_argument("--to", required=True)
    s.add_argument("--task")

    s = add("inbox", cmd_inbox, help="read messages addressed to an agent")
    s.add_argument("--agent", required=True)
    s.add_argument("--mark-read", action="store_true")

    s = add("note", cmd_note, help="append a working note to a task thread")
    s.add_argument("id")
    s.add_argument("text")
    s.add_argument("--from", dest="agent", default="")

    s = add("heartbeat", cmd_heartbeat, help="record agent liveness")
    s.add_argument("--agent", required=True)
    s.add_argument("--status", required=True)
    s.add_argument("--task")
    s.add_argument("--pid", type=int)
    s.add_argument("--host")

    s = add("reap", cmd_reap, help="requeue work owned by a dead agent. Acts with --yes")
    s.add_argument("--stale-seconds", type=int, default=900)
    s.add_argument("--yes", action="store_true")

    s = add("ls", cmd_ls, help="list tasks")
    s.add_argument("--state", choices=STATES)
    s.add_argument("--lane")

    s = add("show", cmd_show, help="show one task and its thread")
    s.add_argument("id")
    s.add_argument("--full", action="store_true",
                   help="print stored text whole, and recover any finish that never reached "
                        "the bus from the run stream")

    s = add("artifact", cmd_artifact, help="record one thing a task produced or changed")
    s.add_argument("id")
    s.add_argument("path")
    s.add_argument("--kind", default="created", help=f"one of {', '.join(ARTIFACT_KINDS)}")
    s.add_argument("--note", default="")
    s.add_argument("--from", dest="agent", default="")

    s = add("artifacts", cmd_artifacts, help="what the fleet produced, grouped by task")
    s.add_argument("--task")
    s.add_argument("--since", type=float, help="only the last N hours")
    s.add_argument("--agent")
    s.add_argument("--kind", help=f"comma separated: {', '.join(ARTIFACT_KINDS)}")

    s = add("signals", cmd_signals, help="one task's nine signals, and whether it is gated")
    s.add_argument("id")

    s = add("why", cmd_why, help="one line explaining a task's position in the queue")
    s.add_argument("id")

    s = add("feed", cmd_feed, help="everything happening on the bus, in time order")
    s.add_argument("--since", type=int, help="only the last N minutes")
    s.add_argument("--agent")
    s.add_argument("--task")
    s.add_argument("--kind")
    s.add_argument("--limit", type=int, default=200)
    s.add_argument("--follow", action="store_true")
    s.add_argument("--interval", type=float, default=2.0)

    add("status", cmd_status, help="fleet state")
    add("board", cmd_board, help="text dashboard")

    s = add("brief", cmd_brief, help="what happened overnight, run this first")
    s.add_argument("--since", type=int, default=12)

    s = add("paused", cmd_paused, help="may this agent work? 0 stop, 2 carry on")
    s.add_argument("--agent")

    s = add("stop", cmd_stop, help="stop one agent, not the fleet")
    s.add_argument("--agent", required=True)
    # REQUIRED, task 0273. A stop with no reason is indistinguishable on the board from an agent
    # that died, and on 2026-08-19 that ambiguity reversed a commander's decision in three
    # minutes. Making it optional-with-a-default would put the word "unspecified" in the trail
    # and change nothing about what a reader can conclude.
    s.add_argument("--reason", required=True,
                   help="why. REQUIRED: a stop nobody signed reads as a crash")
    # The four `--by` defaults below are ONE sentence, and row 0419 is why it is spelled out on
    # each of them rather than left as "(default $SWARM_AGENT)". That shorter form was true and
    # incomplete: it named the first source and said nothing about the tail, which was `$USER` and
    # signed three live stops on `brain` as `you`. See `_who`.
    s.add_argument("--by", default="",
                   help="who is stopping it: $SWARM_AGENT in a fleet terminal, else the human "
                        "this process is ($BRAIN_HUMAN, else `operator`)")

    s = add("start", cmd_start, help="release one stopped agent")
    s.add_argument("--agent", required=True)
    s.add_argument("--reason", default="", help="why it is being released")
    s.add_argument("--by", default="",
                   help="who is releasing it: $SWARM_AGENT in a fleet terminal, else the human "
                        "this process is")

    s = add("pause", cmd_pause, help="stop the fleet")
    s.add_argument("--reason", default="", help="why the fleet is paused")
    s.add_argument("--by", default="",
                   help="who paused it: $SWARM_AGENT in a fleet terminal, else the human this "
                        "process is")

    s = add("resume", cmd_resume, help="restart the fleet")
    s.add_argument("--reason", default="", help="why the fleet is resuming")
    s.add_argument("--by", default="",
                   help="who resumed it: $SWARM_AGENT in a fleet terminal, else the human this "
                        "process is")

    s = add("cancel", cmd_cancel, help="cancel a task")
    s.add_argument("id")
    s.add_argument("--reason", default="cancelled by operator")
    s.add_argument("--agent", default="operator")
    s.add_argument("--force", action="store_true",
                   help="act on a task ANOTHER agent still holds. The override is written to that task's thread, and it needs --agent so it is signed")

    # ---- NOT part of the ported 40. Three runner-plumbing verbs, declared in VERB-PARITY.md.
    s = add("release", cmd_release,
            help="put a task back, ONLY if this agent still holds it (runner plumbing)")
    s.add_argument("id")
    s.add_argument("--agent", required=True)
    s.add_argument("--reason", default="released")

    s = add("run-start", cmd_run_start,
            help="open the run row for one attempt and print its id (runner plumbing)")
    s.add_argument("id")
    s.add_argument("--attempt", type=int, required=True)
    s.add_argument("--agent", default="")
    s.add_argument("--pid", type=int)
    s.add_argument("--host", default="")
    s.add_argument("--session", default="")
    s.add_argument("--stream", help="absolute path to the run's stream.jsonl")
    # No --json here: `add()` gives every subparser one already, and re-registering it raises
    # argparse.ArgumentError at parser-BUILD time, which kills all 40 verbs, not just this one.

    s = add("run-end", cmd_run_end, help="close the run row (runner plumbing)")
    s.add_argument("id")
    s.add_argument("--attempt", type=int, required=True)
    s.add_argument("--exit-code", type=int)
    s.add_argument("--outcome", default="")
    s.add_argument("--stream")

    s = add("accept-work", cmd_accept_work,
            help="accept finished WORK: the human act `done` is not. `accept` is objectives")
    s.add_argument("id")
    # NOT "$USER". Row 0384 removed that fallback and row 0419 found this help string still
    # advertising it, which is the same defect as the code one surface out: a reader who trusts
    # it sets no $BRAIN_HUMAN and expects his login name in `accepted_by`.
    s.add_argument("--by", default="",
                   help="who accepted; defaults to the human this process is ($BRAIN_HUMAN, "
                        "else `operator`). Never an agent, and never $USER")

    s = add("unaccept-work", cmd_unaccept_work,
            help="withdraw an acceptance. The inverse of accept-work, recorded, with a reason. "
                 "`reopen` is what sends the WORK back")
    s.add_argument("id")
    # REQUIRED, row 0413, on `stop`'s argument. At the row, an acceptance that was withdrawn and
    # one that never happened are the same absence, and they mean opposite things. The reason is
    # also the only place the record says whether this was a misclick or a changed mind.
    s.add_argument("--reason", required=True,
                   help="why. REQUIRED: a withdrawn acceptance with no reason is indistinguishable "
                        "from one that never happened")
    s.add_argument("--by", default="",
                   help="who is withdrawing it. Optional: the database answers this from the "
                        "connection and refuses a name that disagrees")

    s = add("auto-accept", cmd_auto_accept,
            help="the auto-accept measurement, and the only verb that writes its flag")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--on", action="store_true", help="enable auto-accept (requires --note)")
    g.add_argument("--off", action="store_true", help="disable auto-accept")
    s.add_argument("--by", default="",
                   help="who decided; defaults to the human this process is, never $USER")
    s.add_argument("--note", default="", help="why. Required to enable")
    s.add_argument("--full", action="store_true", help="every recorded verdict, one per line")

    s = add("utilization", cmd_utilization,
            help="where the seven-day rate-limit meter stands NOW. Reads only, brakes nothing",
            description="Print the fleet's current seven-day rate-limit utilization: the value, "
                        "the timestamp of the reading it came from, how many readings and how "
                        "many run streams it rests on, and when the window resets.\n\n"
                        "A GAUGE, NOT A GOVERNOR. It stops nothing and throttles nothing. It is "
                        "what makes a ceiling checkable; where the ceiling sits is the "
                        "operator's call.\n\n"
                        "Both Claude config dirs are ONE account (measured on task 0261), so "
                        "there is one meter and all agents wall together. At 1.0 the OPERATOR "
                        "loses his own Claude access until the window resets. This is NOT the "
                        "`budget` CLI (budget/bin/budget): that meters dollars off each run's "
                        "total_cost_usd and knows nothing about this meter.\n\n"
                        "Prints UNKNOWN with a reason, and exits 2, whenever it cannot produce a "
                        "reading. It has no fallback value: it will not print a stale number, a "
                        "remembered one, or 0.0.\n\n"
                        "Reads the run streams only. No store, so it answers with Postgres down.",
            formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("--runs", help="stream directory to read. Default $ENGINE_HOME/runs, the "
                                  "same path swarm-run writes")
    s.add_argument("--max-age-minutes", type=int,
                   default=ratelimit.DEFAULT_MAX_AGE_MINUTES,
                   help="past this, the reading is reported as a FLOOR rather than as current, "
                        "because the meter only rises inside a window (default: %(default)s)")
    # THE DOOR ONTO A PROJECT. Bus row 0442, over migration 44's entity and task 0430's gate.
    #
    # `cmd_project` was written on 2026-08-29 and NEVER REGISTERED HERE, so for the whole of that
    # day `swarm project` answered "invalid choice" and the only writer for brain.project was
    # still a hand-typed INSERT at a psql prompt. A subcommand nobody can reach is the same
    # nothing as a subcommand nobody wrote. Lane B2 registered it; the reasoning for every verb,
    # including the four measurements behind there being no `rename`, is in
    # engine/swarm_engine/projects.py.
    s = add("project", cmd_project,
            help="projects: the board's rows. add, list, show, hold/ice/blocked, resume, "
                 "archive, retitle, attach, rm",
            description="A PROJECT IS A ROW (migration 44) AND ITS STATE IS A SPENDING CONTROL.\n"
                        "The operator's own words are what this exists for: 'there's certain "
                        "projects where I kind of just wanted the AI to take a break with it. And "
                        "then I realized later that it worked on it for like eight hours with "
                        "eight terminals and I ran out of API tokens very quickly.'\n\n"
                        "SO `swarm project hold <slug>` IS NOT A LABEL. Since task 0430 the claim "
                        "path excludes every task whose project is not in progress, in SQL, by "
                        "NOT EXISTS, in the same statement that locks the row. `swarm project "
                        "list` says whether that gate is present on THIS store rather than "
                        "assuming it, because a hold that records an intention and throttles "
                        "nothing is the incident above wearing a green tick.\n\n"
                        "THE ASYMMETRY IS MIGRATION 35'S AND IT IS DELIBERATE. Bringing a project "
                        "to rest (hold / ice / blocked) needs NOTHING, because a kill switch that "
                        "can be refused is not a kill switch. Returning it to in progress needs "
                        "the OPERATOR LOGIN and a reason, because that is the statement that puts "
                        "agents back on the work. Deleting a resting one needs a human too.\n\n"
                        "THERE IS NO `rename`, AND THAT IS MEASURED RATHER THAN OMITTED. The slug "
                        "is the primary key; a plain rename is refused by the foreign key for any "
                        "project that has work, the copy-and-repoint route destroys 'when did "
                        "this go on hold', and ON UPDATE CASCADE is refused by "
                        "work_item_project_matches_canonical on exactly the rows that name a "
                        "project twice. A subcommand that always refused would be MUST-NOT-BUILD "
                        "item 2's disabled affordance, so there is none. `retitle` IS offered and "
                        "leaves the state stamp alone.\n\n"
                        "actions: list, show, add, hold, ice, blocked, resume, archive, "
                        "unarchive, retitle, attach, rm",
            formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("action",
                   choices=("list", "show", "add", "hold", "ice", "blocked", "resume",
                            "archive", "unarchive", "retitle", "attach", "rm"))
    s.add_argument("slug", nargs="?",
                   help="the project. Lower case letters, digits and hyphens: "
                        "^[a-z0-9][a-z0-9-]{0,62}$, the same shape as a routine name")
    s.add_argument("--title", help="add: the display title, NOT NULL and non-blank. retitle: the "
                                   "new one, which does not move the state stamp")
    s.add_argument("--state", help="add: born resting instead of in progress. One of hold, ice, "
                                   "blocked, and it needs --reason. The honest shape for 'set "
                                   "this up, do not start on it yet'")
    s.add_argument("--reason", help="hold/ice/blocked, resume and archive: why. Required in "
                                    "EVERY direction, because each is a decision somebody has to "
                                    "be able to read six weeks later")
    s.add_argument("--task", help="attach: the work item id to file into this project")
    s.add_argument("--by", default="", help="who is acting. A courtesy for the output line: the "
                                            "row carries what the DATABASE says the connection "
                                            "is, never this string")

    s = add("routine", cmd_routine,
            help="routines: a stored call to `post`, on a clock. add, list, due, tick, disable",
            description="A ROUTINE IS A STORED CALL TO `post`, ON A CLOCK. It is the half of "
                        "PLAN.md's board-retirement condition that budgets did not cover, and it "
                        "is an object in the store so that it can be listed, disabled and shown "
                        "to have fired.\n\n"
                        "THE CLOCK IS NOT HERE. `swarm routine tick --fire` is one pass and then "
                        "the process exits; systemd/brain-routine-tick.timer owns the next one. "
                        "There is no daemon, no loop and no second notion of what time it is.\n\n"
                        "A DOUBLE FIRE IS IMPOSSIBLE IN THE SCHEMA, not unlikely in this CLI: "
                        "brain.routine_run carries UNIQUE (routine_id, scheduled_for), the "
                        "occurrence row is written in the same transaction as the post, and a "
                        "second caller for one slot is refused by Postgres with 23505.\n\n"
                        "THE KILL SWITCH IS ONE ACT: `swarm routine disable <name> --reason ...`, "
                        "which needs no credential, because a kill switch that can be refused is "
                        "not a kill switch. ARMING one does need the operator login, because a "
                        "routine is a standing scheduled dispatch. `swarm pause` stops all of "
                        "them at once and this honours it.\n\n"
                        "actions: list, due, tick, runs, add, disable, enable, fire",
            formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("action",
                   choices=("list", "due", "tick", "runs", "add", "disable", "enable", "fire"))
    s.add_argument("name", nargs="?", help="the routine, for runs/disable/enable/fire")
    s.add_argument("--fire", action="store_true",
                   help="tick: actually dispatch. Without it `tick` is a DRY RUN, because a "
                        "scheduler that fires by default is one typo away from posting during a "
                        "test run")
    s.add_argument("--reason", help="disable: why. Required, and stored, because it is the only "
                                    "question anybody asks about a dark routine later")
    s.add_argument("--by", default="", help="who is acting")
    s.add_argument("--limit", type=int, default=20, help="runs: how many occurrences")
    s.add_argument("--title", help="add: the title every occurrence is posted under")
    s.add_argument("--lane", help="add: the lane every occurrence is posted into")
    s.add_argument("--body", help="add: the work order, reposted verbatim each period")
    s.add_argument("--every", help="add: daily, weekly, hourly, twice-daily, or a number of "
                                   "minutes. Monthly is refused and says why")
    s.add_argument("--at", help="add: the anchor. HH:MM (UTC, today) or a full ISO timestamp. "
                                "Slot k is anchor + k*period, so 07:00 with --every twice-daily "
                                "is 07:00 and 19:00")
    s.add_argument("--grace", type=int,
                   help="add: minutes. A slot found later than this is recorded as `missed` "
                        "rather than fired, because backfilling posts work for a period that has "
                        "gone. Default is the period, so the current slot always fires")
    s.add_argument("--allow-overlap", action="store_true",
                   help="add: post even when the previous occurrence is still open. Default is "
                        "to record the slot as `skipped`, because a daily routine whose task is "
                        "never closed is thirty open rows in a month")
    s.add_argument("--parent", help="add: the task every occurrence is posted under. Its hard "
                                    "flags are inherited by OR and can be raised, never lowered")
    s.add_argument("--for-agents", action="store_true",
                   help="add: the FLEET should do this. Refused without an absolute --workdir, "
                        "by a CHECK on the table, so it fails at a console rather than at 03:20")
    s.add_argument("--workdir", help="add: the absolute directory every occurrence runs in. "
                                     "Required by the table when --for-agents is set")
    s.add_argument("-f", "--file", help="add: read the work order from a file, or - for stdin")
    s.add_argument("--priority", type=int, default=3, help="add: posted priority")
    s.add_argument("--max-attempts", type=int, default=2, help="add: posted max_attempts")
    s.add_argument("--external", nargs="?", const="true", default="",
                   help="add: hard flag on every occurrence. Sends, deploys, spends or publishes")
    s.add_argument("--canon-touching", nargs="?", const="true", default="",
                   help="add: hard flag on every occurrence. Proposes a change to canon")

    add("whoami", cmd_whoami,
        help="which human this process IS, as the database names it. Exit 1 on a mismatch",
        description="Prints what this process ASKED to be (store.human_slug: an explicit slug, "
                    "else $BRAIN_HUMAN, else `operator`) beside what the DATABASE says it IS "
                    "(brain.current_human(), from session_user), and exits 1 when they "
                    "disagree. Under row 0386 decision 2 this instance carries up to twelve "
                    "human logins, so a console or a service unit configured for one human on a "
                    "host holding another's credential is a real state, and it used to present "
                    "as a permissions error one refused write at a time.")

    # ---- the thirteenth verb beyond the 40. Declared in engine/VERB-PARITY.md in the same
    # change that added it, because engine/tests/test-verbs.sh goes red until the row is there
    # and leaving a red suite for the next lane is the toll this check exists to charge.
    s = add("helper", cmd_helper,
            help="open a HELPER terminal on one task. Claims nothing, reports nothing",
            description="Opens an interactive engine session against one work item, with the "
                        "row's own record already loaded, in the directory the row names.\n\n"
                        "IT IS NOT `claim`. `claim` takes the row, spends an attempt and puts an "
                        "agent on the hook for a report. This takes nothing and reports nothing: "
                        "it is a human opening a terminal about a row. The one mark it leaves is "
                        "a note on the thread naming the session, which is what makes the "
                        "conversation findable afterwards.\n\n"
                        "CAPTURE IS NOT EQUAL ACROSS THE TWO ENGINES and the difference is "
                        "printed before the session starts. A Claude Code helper is registered "
                        "by the session hooks and its transcript is indexed with a sha256. A "
                        "Codex helper writes a rollout file nothing in this runtime reads.",
            formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("id")
    s.add_argument("--engine", default="claude", choices=list(HELPER_ENGINES),
                   help="which terminal to open. Default claude")
    s.add_argument("--model", default="",
                   help="passed through to the engine. Default is the engine's own")
    s.add_argument("--print", dest="print_only", action="store_true",
                   help="print the paste-ready launch line and exit. Writes nothing, launches "
                        "nothing, and is what the console's task page renders")
    s.add_argument("--no-record", action="store_true",
                   help="do not write the thread note. The terminal still opens, and nothing "
                        "then connects this row to the conversation")

    return p


# ---------------------------------------------------------------- lane E, row 0384: who am I


def cmd_whoami(args):
    """WHICH human this process is, as the DATABASE names it. Not as the caller hopes.

    Lane E, row 0384. Under row 0386 decision 2 there are up to twelve human logins on this
    instance, so "am I the operator" stops being rhetorical and becomes a question with a wrong
    answer available. Two facts, deliberately printed side by side because the whole point is
    that they can differ:

        requested   which human this PROCESS ASKED to be, from store.human_slug():
                    an explicit slug, else $BRAIN_HUMAN, else `operator`
        human       which human the DATABASE says this connection IS,
                    from brain.current_human(), which reads session_user

    A disagreement is a CONFIGURATION error, and printing it here is what stops it presenting
    later as a permissions error. Exit 1 when they disagree, so a service unit's ExecStartPre
    can refuse to start rather than run all evening writing rows the database refuses.
    """
    who = store.whoami()
    roster, ceiling = [], None
    try:
        from . import admin as _admin
        roster, ceiling = _admin.humans(), _admin.ceiling()
    except Exception as exc:                                            # noqa: BLE001
        who["roster_error"] = f"{exc.__class__.__name__}: {exc}"
    payload = {**who, "roster": [r["human"] for r in roster], "ceiling": ceiling}
    if args.json:
        out_json(payload)
        return OK if who["agrees"] else ERR
    print(f"requested  {who['requested']}")
    print(f"login      {who['login'] or '(no connection)'}")
    print(f"human      {who['human'] or '(this login is not a human)'}")
    if roster or ceiling is not None:
        print(f"roster     {len(roster)} of {ceiling if ceiling is not None else '?'} human "
              f"login(s): {', '.join(r['human'] for r in roster) or '(none)'}")
    elif who.get("roster_error"):
        print(f"roster     NOT READ: {who['roster_error']}")
    if who["agrees"]:
        print(f"\nthis process writes human-attributed rows as {who['human']!r}.")
        return OK
    print(f"\nTHIS PROCESS CANNOT WRITE AS {who['requested']!r}. {who['reason']}")
    print("Human-attributed writes (accept work, recommend accept, admin config set, routine "
          "add) will be refused by the database. `swarm admin human list` shows who exists; "
          "`swarm admin human provision <slug>` mints a login and its credential.")
    return ERR


# ------------------------------------------------------------ the helper terminal, 2026-08-28
# Source: outputs/2026-08-27-DEMO/OPERATOR-FEEDBACK.md, Part Two, programme question 2.
#
# THE OPERATOR'S OWN ANSWER TO "what is missing", 2026-08-28, driving the eleven-step demo:
#
#     "One of the key things about this app is that it's supposed to actually have like a
#      terminal and a beautiful UX wrapper on top of that terminal because a lot of times people
#      are using like Claude code and things like that in the terminal ... We didn't get to test
#      to make sure that all terminal conversations are being logged properly."
#
# Three things sit under that. Every terminal conversation is logged (shipped, separately). A
# wrapper (the console's, separately). And THIS one: a helper terminal launched FROM A TASK,
# defaulting to Claude Code or Codex.
#
# WHY IT IS NOT `claim`, and this is the whole design. `claim` is the runner's act: it takes the
# row, writes `claimed_by`, spends an attempt, and puts the fleet on the hook for a report.
# A human opening a terminal about a row is none of those things. So `helper` takes nothing,
# spends nothing, and reports nothing. It loads the row's own record into a fresh session and
# gets out of the way. The one mark it leaves is a `note` on the thread saying a helper was
# opened and NAMING THE SESSION, which is what makes the conversation findable afterwards. That
# note is a record of a launch, not a decision about the work.

HELPER_ENGINES = ("claude", "codex")

# WHAT EACH ENGINE ACTUALLY LEAVES BEHIND, kept as data because three surfaces have to say the
# same thing and the codex row is the one it would be convenient to leave vague.
#
# THE CODEX GAP IS REAL AND IT IS STATED HERE RATHER THAN DISCOVERED LATER. `engine/bin/swarm-run`
# runs the fleet's claude sessions through STREAM_FORMATTER, which writes the raw stream-json, a
# human transcript and the terminal result event as they arrive; its codex branch (swarm-run:687)
# appends bare stdout to one log and produces no structured stream at all. On the HELPER path the
# asymmetry is different but no smaller: an interactive Claude Code session is registered by the
# SessionStart / SessionEnd hooks in ~/.claude/settings.json and lands in the session index with a
# transcript pointer and a sha256, and an interactive Codex session writes a rollout file under
# ~/.codex/sessions that NOTHING in this runtime reads. The point of this whole feature is that
# the operator can trust the record, so he does not get to choose codex believing the logging is
# equivalent. It is not, and the sentence below is printed before the session starts.
HELPER_CAPTURE = {
    "claude": (
        "Claude Code, started with --session-id {session}. The SessionStart and SessionEnd hooks "
        "register this session and index its transcript with a sha256 when it ends, so the "
        "conversation is on the record under that id."),
    "codex": (
        "Codex, and ITS CAPTURE IS NOT EQUIVALENT. Codex writes a rollout transcript under "
        "{codex_home}, and nothing in this runtime indexes it: no session row, no transcript "
        "pointer, no hash. A Claude Code helper on this same row is logged and this one is not. "
        "The rollout file this session leaves is named on the task thread when it exits."),
}

HELPER_CODEX_SESSIONS = "~/.codex/sessions"


def _helper_context(tid):
    """The exact bytes of `swarm show <id> --full`, captured rather than re-rendered.

    Calling the function is the point. A second renderer that formats a task for a prompt is a
    second definition of what a task IS, and this repo has already paid for that once: before
    migration 14 the work order and the report shared a column and the runner dispatched attempt
    2 against attempt 1's summary. `cmd_show` is also what `bin/swarm-run` shells out to for the
    fleet's prompt, so a helper terminal and a runner terminal read the row in the same words.
    """
    import contextlib
    import io

    shim = argparse.Namespace(id=tid, json=False, full=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_show(shim)
    return buf.getvalue()


def _helper_prompt(tid, ctx, workdir, workdir_source, capture, context_file, stamp):
    return f"""You are a HELPER TERMINAL, opened by a human on work item {tid}.

WHAT YOU ARE NOT. You have not claimed this task and you must not claim it. `claim` is the fleet
runner's act: it takes the row, spends an attempt and puts an agent on the hook for a report.
This session holds none of that. Do not run `swarm claim`, `swarm done`, `swarm fail` or
`swarm block` on {tid}, and do not write as though you were the agent that holds it. `done` is
the agent's report and `accept-work` is the operator's decision. Neither one is yours. If
something here needs filing, say so and let the human file it.

WHAT YOU ARE. A second pair of hands on one row, with that row's own record already loaded, so
nobody had to paste a brief by hand. The human is at this keyboard. Read what he points at,
answer him, and do the work he asks for.

WHERE YOU ARE. {workdir}
  ({workdir_source})

WHAT THIS CONVERSATION LEAVES BEHIND. {capture}

A copy of the record below is at {context_file}. It is a SNAPSHOT taken at {stamp}: the row can
move under you while you work, and `swarm show {tid} --full` re-reads it.

----- the row, as the store had it at {stamp} -----
{ctx}----- end of the row -----
"""


def _codex_rollouts_since(since_epoch, root=None):
    """Rollout files Codex wrote during this session, newest first. Best effort, never raises.

    Codex has no --session-id, so unlike the claude branch there is no key that can be minted
    before the launch and asserted afterwards. What there IS is a directory it writes into, so
    the pointer is recovered by mtime and reported as what it is: files that appeared while the
    session ran, not a session id the engine confirmed.
    """
    base = Path(os.path.expanduser(root or HELPER_CODEX_SESSIONS))
    try:
        found = [p for p in base.rglob("rollout-*.jsonl") if p.stat().st_mtime >= since_epoch]
    except OSError:
        return []
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def _session_hook_installed():
    """Are the session hooks actually wired into the operator's Claude Code settings?

    Returns (True | False | None, what was read). None means the file could not be read at all,
    which is a different answer from "not wired" and is reported as such.

    THE CLAUDE BRANCH'S WHOLE CAPTURE CLAIM RESTS ON THIS FILE, so it is read rather than
    assumed. `~/.claude/settings.json` is the OPERATOR'S file and no agent may write it:
    `ingest/docs/HOOK-INSTALL.md` records the classifier refusing three separate routes, on
    purpose, because a `hooks` key is arbitrary command execution on every future session. It
    follows that the hooks can be absent on any host, and a verb that promised "this conversation
    is on the record" while they were absent would be doing the exact thing this feature exists
    to stop. `CLAUDE_CONFIG_DIR` is honoured because it is the real variable the account ring in
    `bin/swarm-run` already rotates, so a helper opened under a second account reads that
    account's settings and not the default one's.
    """
    root = Path(os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")))
    path = root / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, f"{path} does not exist"
    except (OSError, ValueError) as exc:
        return None, f"{path} could not be read: {exc.__class__.__name__}"
    wired = set()
    for ev in ("SessionStart", "SessionEnd"):
        for group in (data.get("hooks") or {}).get(ev) or []:
            for h in group.get("hooks") or []:
                if "claude-session-hook" in str(h.get("command") or ""):
                    wired.add(ev)
    return (wired == {"SessionStart", "SessionEnd"},
            f"{path} wires {', '.join(sorted(wired)) or 'neither event'}")


def cmd_helper(args):
    """Open a helper terminal on one task. It claims nothing and reports nothing.

    See the block comment above for why this is not `claim`. The mechanics:

      * the row's workdir is where the session starts, and a workdir that is set and MISSING is
        a hard failure rather than a relocation. That rule is `bin/swarm-run`'s, carried here on
        purpose: a session that quietly starts in the wrong tree writes to the wrong tree.
      * the context is the bytes of `swarm show <id> --full`, so the engine and the operator are
        reading the same record.
      * claude gets a session id minted HERE, before the launch, so the thread note and the
        session index name the same conversation. Codex has no such flag, which is half of why
        its capture is not equivalent.
      * `--print` writes nothing, launches nothing, and prints the paste-ready line. That is what
        the console's task page renders.
    """
    import datetime as _dt
    import subprocess
    import time
    import uuid

    tid = _tid(args.id)
    t = reads.task(tid)
    if not t:
        die(f"no such task: {tid}")
    engine = args.engine
    if engine not in HELPER_ENGINES:
        die(f"unknown engine {engine!r}. One of: {', '.join(HELPER_ENGINES)}")

    # A MISSING WORKDIR FAILS RATHER THAN RELOCATING. `bin/swarm-run` carries this rule for the
    # fleet and the reason is the same for a human: the alternative is a session that starts in
    # whatever directory the console happened to be launched from and edits the wrong tree.
    if t.get("workdir"):
        workdir = os.path.expanduser(t["workdir"])
        workdir_source = f"the row's own workdir, {t['workdir']}"
        if not os.path.isdir(workdir):
            die(f"{tid} names a workdir that is not there: {workdir}\n"
                f"       Not relocating. A helper that starts in some other tree edits some "
                f"other tree.\n"
                f"       Fix the row (`swarm set {tid} workdir <path> --as-operator`) or make "
                f"the directory.")
    else:
        workdir = os.getcwd()
        workdir_source = ("this row names no workdir, so the session starts where you ran this "
                          "from")

    swarm_bin = str(Path(__file__).resolve().parents[1] / "bin" / "swarm")
    repo = str(Path(__file__).resolve().parents[2])
    state = Path(os.environ.get("ENGINE_HOME", os.path.expanduser("~/.brain-runtime")))
    launch_line = f"{swarm_bin} helper {tid}"
    if engine != "claude":
        launch_line += f" --engine {engine}"

    # MINTED HERE, BEFORE THE LAUNCH, and that is the point of it: it is the key the thread note
    # and the session index are both written under, so the row and the conversation name the same
    # thing. Under --print nothing is launched, so the id would be a promise about a session that
    # never starts and the sentence says so instead.
    session_id = str(uuid.uuid4())
    capture = HELPER_CAPTURE[engine].format(
        session="<minted when the session starts>" if args.print_only else session_id,
        codex_home=HELPER_CODEX_SESSIONS)

    if args.print_only:
        payload = {"task": tid, "title": t["title"], "engine": engine, "workdir": workdir,
                   "workdir_source": workdir_source, "launch": launch_line, "capture": capture,
                   "lines": {e: f"{swarm_bin} helper {tid}"
                                + ("" if e == "claude" else f" --engine {e}")
                             for e in HELPER_ENGINES}}
        if args.json:
            out_json(payload)
            return OK
        print(f"{tid}  {t['title']}")
        print(f"  engine   {engine}")
        print(f"  workdir  {workdir}")
        print(f"           {workdir_source}")
        print(f"  capture  {capture}")
        print(f"\n{launch_line}")
        return OK

    # An interactive engine with no terminal under it hangs or dies, and either way the operator
    # gets a mystery. Refused with the flag that answers the other question, rather than silently
    # switching mode: a verb that does something different depending on how it was called is how
    # a script starts an Opus session nobody is watching.
    if not sys.stdin.isatty():
        die(f"no terminal on stdin, and `helper` opens an INTERACTIVE session.\n"
            f"       For the line to paste into a terminal: swarm helper {tid} --print")

    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ctx = _helper_context(tid)
    helpers = state / "helpers"
    try:
        helpers.mkdir(parents=True, exist_ok=True)
        context_file = helpers / f"{tid}-{stamp.replace(':', '')}-{engine}.md"
        context_file.write_text(ctx, encoding="utf-8")
    except OSError as exc:
        die(f"cannot write the context file under {helpers}: {exc}")

    prompt = _helper_prompt(tid, ctx, workdir, workdir_source, capture, context_file, stamp)

    if engine == "claude":
        argv = ["claude", "--session-id", session_id,
                "--add-dir", str(state), "--add-dir", repo]
        if args.model:
            argv += ["--model", args.model]
        argv.append(prompt)
    else:
        argv = ["codex", "--cd", workdir]
        if args.model:
            argv += ["-m", args.model]
        argv.append(prompt)

    # THE NOTE GOES ON BEFORE THE LAUNCH, not after. It is the only edge between this row and the
    # conversation, and a note written after the session would be missing for every session that
    # crashed, was killed, or is still running -- which is exactly when somebody goes looking for
    # it. It is signed by whichever human the DATABASE says this connection is, falling back to
    # the slug this process asked to be, because `store.whoami` is the verb that exists to stop a
    # request being read as an answer.
    # CHECKED, NOT ASSUMED. The claude capture sentence above is a claim about the operator's
    # own settings file, and this is the one line that turns it into a reading.
    hooks_ok, hooks_why = (_session_hook_installed() if engine == "claude" else (None, ""))

    who = store.whoami()
    signer = who.get("human") or who.get("requested") or "operator"
    if not args.no_record:
        text = (f"helper terminal opened by {signer}: engine={engine} "
                + (f"session={session_id} " if engine == "claude" else "")
                + f"workdir={workdir} context={context_file}. "
                f"No claim was taken and nothing was reported: this is a human opening a "
                f"terminal about this row. Capture: {capture}"
                + (f" Session hooks: {'installed' if hooks_ok else 'NOT INSTALLED'} "
                   f"({hooks_why})." if engine == "claude" else ""))
        try:
            store.apply("note", id=tid, text=text, agent=signer)
        except Exception as exc:                                        # noqa: BLE001
            # A store that cannot take the note must not stop the operator getting a terminal.
            # It is said out loud instead, because a silent miss here is the missing edge.
            print(f"swarm: the helper note did NOT land on {tid} ({exc.__class__.__name__}: "
                  f"{exc}). The session below is not linked to the row.", file=sys.stderr)

    print(f"{tid}  {t['title']}")
    print(f"  engine   {engine}" + (f"  model {args.model}" if args.model else ""))
    print(f"  workdir  {workdir}")
    print(f"  context  {context_file}")
    print(f"  capture  {capture}")
    if engine == "claude":
        if hooks_ok:
            print(f"  logging  the SessionStart and SessionEnd hooks ARE wired: {hooks_why}")
        elif hooks_ok is None:
            print(f"  logging  UNKNOWN: {hooks_why}. Whether this conversation is registered "
                  f"cannot be read from here.")
        else:
            print(f"  logging  NOT INSTALLED, so this conversation will NOT be registered and "
                  f"its transcript will NOT be indexed. {hooks_why}.")
            print(f"           The install is the operator's own step and no agent may do it: "
                  f"ingest/docs/HOOK-INSTALL.md.")
    print("  claim    NOT taken. This is your terminal, not an agent's run.\n")

    # AND THE ONE VARIABLE THAT SILENTLY THROWS AWAY THE CONVERSATION THIS COMMAND EXISTS TO KEEP.
    # A `claude` that believes it is a CHILD of another Claude Code session writes NO transcript
    # file, so `SessionEnd` has nothing to index, the conversation never reaches
    # `brain.transcript`, and it never appears in the Sessions room. Measured A/B on 2026-08-29,
    # two otherwise identical pty sessions, both answering correctly and both exiting 0:
    #
    #     CLAUDE_CODE_CHILD_SESSION set     ->  answered fine, 0 transcript files written
    #     ONLY that variable removed        ->  answered fine, 1 transcript file written
    #
    # The `brain.session` row IS written either way, and the note above has already landed on the
    # thread, which is exactly what makes the loss quiet: the row and the note both say a
    # conversation happened and neither can say what was said. Five real helper sessions were lost
    # to this on 2026-08-29 before it was found. The `logging` lines printed above would have said
    # the hooks ARE wired every one of those five times, because they were.
    #
    # NAMED, NOT PREFIX-STRIPPED, on purpose: `CLAUDE_CODE_*` also carries real configuration the
    # operator may have set, and a wildcard here would silently disable it.
    child_env = dict(os.environ)
    child_env.pop("CLAUDE_CODE_CHILD_SESSION", None)

    started = time.time()
    try:
        rc = subprocess.call(argv, cwd=workdir, env=child_env)
    except FileNotFoundError:
        die(f"{engine!r} is not on PATH, so there is nothing to open.\n"
            f"       claude:  https://claude.com/claude-code\n"
            f"       codex:   npm i -g @openai/codex  (or the snap)")
    except KeyboardInterrupt:
        rc = 130

    print(f"\nhelper session on {tid} exited rc={rc}.")
    if engine == "claude":
        if hooks_ok:
            print(f"  session {session_id}, registered by the hooks read above. "
                  f"`swarm show {tid}` carries the same id.")
        else:
            print(f"  session {session_id} was NOT registered: the hooks are not wired "
                  f"({hooks_why}). The id is on the thread and the conversation is not.")
    else:
        rolls = _codex_rollouts_since(started)
        if rolls:
            print("  codex rollout(s) written while this session ran:")
            for p in rolls[:4]:
                print(f"    {p}")
            if not args.no_record:
                try:
                    store.apply("note", id=tid,
                                text=(f"helper terminal on {tid} closed: codex rollout "
                                      f"{rolls[0]}. Recovered by mtime, not confirmed by the "
                                      f"engine, and NOT in the transcript index."),
                                agent=signer)
                except Exception:                                       # noqa: BLE001
                    pass
        else:
            print(f"  NO codex rollout appeared under {HELPER_CODEX_SESSIONS} while this ran. "
                  f"This conversation may have left no transcript at all.")
    return OK



def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args) or OK
    except VerbError as e:
        die(str(e), e.code)
    except store.StoreConfigError as e:
        die(str(e))
    except psycopg2.Error as e:
        # After StoreConfigError, which is what a connection failure is already wrapped as. What
        # is left is the server talking: a trigger refusing, a constraint refusing, or a defect.
        die_db(e, getattr(args, "cmd", ""))
    except BrokenPipeError:
        # `swarm feed | head` must not traceback.
        try:
            sys.stdout.close()
        finally:
            return OK
