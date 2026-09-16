"""Nine tools, each a thin wrapper over a verb.

**If a tool contains logic that is not in a verb, the waist is broken** and the console and the
MCP server drift into two subtly different systems. So every handler below is the same shape:
validate the argument names, call `store.apply(verb, ...)` or a read, return what came back. No
tool computes a state transition, and no tool reaches SQL.

That is checkable rather than asserted, and `mcp/tests/test_tools.py` checks it: every write tool
declares the verb it wraps, that verb must be in `store.registered()`, and calling the tool with
`store.apply` monkeypatched proves the tool called that verb and no other.

**What is deliberately not here:**

  * **No generic run-a-command tool.** Fixed tools, fixed argument shapes. A `run_verb(name, kw)`
    tool would technically satisfy "a thin wrapper over a verb" and would hand an agent the whole
    surface, including `pause`, `reap`, `set` and `accept work`, from one call site nobody
    reviews.
  * **Nothing touching config, `permission_mode`, or `bypassPermissions`.** `permission_mode` is
    load-bearing: `acceptEdits` produces confident unverified work, and `auto` is what makes an
    agent's self-report trustworthy at all. A tool that could change it would let the agent whose
    trustworthiness depends on that setting change that setting.
  * **No `accept work`.** Acceptance is the human's act (D00 rule 4). An MCP tool for it would be
    an agent accepting an agent's work, which is self-approval one call removed.
  * **No `done` for someone else, no `reopen`, no `answer`.** `finish_work` reports on the item
    the CALLING agent holds; `answer` is the operator's verb and an agent answering its own
    question is the actor forgery the whole prohibition is about.

Two of the nine wrap verbs that are not registered in `store`, and they say so rather than
reimplementing them: `book_receipt` and `resolve_entity` are D2's, implemented in
`adapter/brain_adapter/`, and the receipt path writes to git rather than to Postgres.

**That adapter-to-store join is built.** It landed with migration 5, and `resolve_entity` --
a read, carrying no gate -- works through it. What still stops `book_receipt` from writing is
therefore not a missing seam: it is a decision the operator owns, because booking a receipt is
a git commit into the operator's brain. Its refusal below says so in those words.

Neither refusal names a task id any more. This file used to point a reader at "task 0103" for
the seam; that task closed while the pointer stayed, so the pointer outlived the fact and read
as "unbuilt" long after the thing was built. A refusal describes the condition it is refusing
on, which a reader can check, rather than an id, which rots.
"""

from __future__ import annotations

import os
import sys

import store
from store import reads as R

# REGISTRATION IS AN IMPORT SIDE EFFECT, so the server has to do these imports or its registry is
# empty and every write tool fails with UnknownTransition while every read tool works fine.
#
# This line exists because a real Claude Code session found its absence: `raise_question` came
# back with `no transition named 'ask'. Registered: (none registered)`. The unit tests did not
# catch it, because a test module that imports the lanes to enumerate them also registers them,
# and the test process is therefore never the process the server runs in. `test_the_server_
# process_can_write` runs the server as a subprocess for exactly that reason.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_REPO, os.path.join(_REPO, "engine"), os.path.join(_REPO, "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# D2's adapter, and it is APPENDED rather than inserted at 0 like the three above. The asymmetry
# is load-bearing, not untidy: `adapter/` holds sibling directories named `tools/` and `tests/`
# that carry no `__init__.py`, and Python 3 resolves such directories as namespace packages
# anyway. Prepend `adapter/` and a bare `import tools` inside THIS server process resolves to
# `adapter/tools`. Appended, it sits behind everything real, `brain_adapter` still imports
# because it is a genuine package, and no other name in the tree changes meaning.
#
# Its absence was a defect rather than a policy. Without it `book_receipt` and `resolve_entity`
# took their ImportError branch in every server process the server ever ran, so `resolve_entity`
# never resolved anything and `book_receipt` reported an unbuilt seam while the seam sat built
# and importable one directory away.
_ADAPTER = os.path.join(_REPO, "adapter")
if _ADAPTER not in sys.path:
    sys.path.append(_ADAPTER)

from swarm_engine import transitions as _engine_transitions             # noqa: F401,E402
from swarm_engine import accept as _engine_accept                       # noqa: F401,E402
from fabric import emit as _fabric_emit                                 # noqa: F401,E402
try:
    from human_queue import transitions as _queue_transitions           # noqa: F401,E402
except ImportError:                                                     # pragma: no cover
    _queue_transitions = None

ACTOR_ENV = "SWARM_AGENT"


def _actor(given: str | None = None) -> str:
    a = (given or os.environ.get(ACTOR_ENV) or "").strip()
    if not a:
        raise ValueError(
            "every tool call needs an agent name. Pass `agent`, or set SWARM_AGENT. An "
            "unattributed write is a row nobody can hold to account.")
    return a


# --------------------------------------------------------------------------- reads

def read_queue(*, lane: str | None = None, limit: int = 20, **_) -> dict:
    """What `claim` would take next, in order, without taking it. Read-only by construction."""
    rows = R.queue(lane=lane, limit=int(limit))
    return {"queue": rows, "count": len(rows),
            "note": "This is the preview. The claim itself is atomic and uses FOR UPDATE SKIP "
                    "LOCKED; what you see here may be taken by another agent before you ask."}


def read_recommendations(*, state: str = "pending", limit: int = 20, **_) -> dict:
    """Recommendations with their rationale. The rationale is the counterargument.

    A recommendation is never returned without the field that argues against it, for the same
    reason the console never renders one without it: a recommendation with nothing arguing
    against it has not been reviewed, it has been repeated.
    """
    with store.read() as s:
        rows = s.query(
            "SELECT id, subject_type, subject_id, text, rationale, requires_human, state, "
            "       created_at, decided_at, decided_by, produced_by "
            "  FROM brain.recommendation WHERE state = %s ORDER BY created_at DESC LIMIT %s",
            (state, int(limit)))
    for r in rows:
        if not (r.get("rationale") or "").strip():
            r["counterargument_missing"] = (
                "This recommendation states no counterargument. Treat that as a finding.")
    return {"recommendations": rows, "count": len(rows)}


# --------------------------------------------------------------------------- writes

def emit_event(*, type: str, external, canon_touching, agent: str | None = None,
               payload_summary: str = "", department: str = "", lane: str = "",
               subject_type: str = "", subject_id: str = "", work_item_id: str | None = None,
               session_id: str | None = None, payload_ref: str | None = None, **_) -> dict:
    """`event emit`. The flags are REQUIRED and there is no default for them.

    `fabric.emit` refuses an omitted flag rather than assuming false, because a producer that
    forgets is exactly the producer whose event should have been gated. This wrapper does not
    supply one either.
    """
    return _fabric_emit.emit(type=type, external=external, canon_touching=canon_touching,
                      actor=_actor(agent), payload_summary=payload_summary,
                      department=department, lane=lane, subject_type=subject_type,
                      subject_id=subject_id, work_item_id=work_item_id, session_id=session_id,
                      payload_ref=payload_ref)


def claim_work(*, lanes, agent: str | None = None, host: str = "", role: str = "", **_) -> dict:
    """`claim`. First claimer wins; everyone else gets nothing, which is not an error.

    A planner never claims: an empty lane list matches nothing, and that is the same gate the
    file bus had rather than a new one.
    """
    a = _actor(agent)
    if isinstance(lanes, str):
        lanes = [x.strip() for x in lanes.split(",") if x.strip()]
    got = store.apply("claim", actor=a, agent=a, lanes=list(lanes or []), host=host, role=role)
    if not got:
        return {"claimed": None,
                "note": "Nothing claimable in those lanes. Not an error: the first claimer won."}
    return {"claimed": got}


def report_progress(*, status: str = "working", agent: str | None = None,
                    task: str | None = None, note: str | None = None, host: str = "",
                    pid: int | None = None, role: str = "", **_) -> dict:
    """`heartbeat`, plus `note` when there is something to say.

    Two verbs because there are two state changes, not one merged one: the reaper reads the
    heartbeat and a human reads the note. Both are registered transitions; this tool invents
    neither.
    """
    a = _actor(agent)
    out = {"heartbeat": store.apply("heartbeat", actor=a, agent=a, status=status, task=task,
                                    host=host, pid=pid, role=role)}
    if note and task:
        out["note"] = store.apply("note", actor=a, id=task, text=note, agent=a)
    elif note:
        out["note_skipped"] = ("a note needs a task to hang on. Pass `task`, or send a message "
                               "with the messaging verb instead of losing the text.")
    return out


def finish_work(*, task: str, outcome: str, summary: str, agent: str | None = None,
                blocked_on: str = "", **_) -> dict:
    """`done`, `block` or `fail`, chosen by `outcome`, and never anything else.

    `done` means the agent reported it finished. **Acceptance is a separate act and no tool here
    performs it**, because the decider on every acceptance is a human (D00 rule 4).

    `fail` requeues while attempts remain, so the honest report of a bad attempt costs less than
    a `done` that has to be reopened. Say what the next attempt must do differently.

    IT REPORTS ON THE ITEM THE CALLING AGENT HOLDS, and that sentence is now enforced rather than
    documented. It was documented only: the adversarial pass called
    `finish_work(task=<T1's task>, outcome='failed', agent='T3')` on this shipped surface and got
    `{'requeued': True}` while T1 held the task. Nothing here changed to fix it and nothing here
    should have -- this is a thin wrapper, and a check added at the wrapper would be a second
    implementation of a rule the verb has to carry anyway for the CLI and the runner. `_hold` in
    `swarm_engine.transitions` refuses it now, for every surface at once, and the `agent` these
    calls already pass is what it tests against.

    No `force` is exposed here, deliberately. The override exists for an operator at a terminal
    who can see both agents; an agent that could grant itself the override has no predicate.
    """
    a = _actor(agent)
    verb = {"done": "done", "blocked": "block", "block": "block", "failed": "fail",
            "fail": "fail"}.get(outcome)
    if not verb:
        raise ValueError("outcome must be one of: done, blocked, failed. A partly done task is "
                         "`failed`, not `done` with a summary that says mostly.")
    if verb == "done":
        return {"verb": verb, "result": store.apply("done", actor=a, id=task, summary=summary,
                                                    agent=a)}
    if verb == "block":
        return {"verb": verb, "result": store.apply("block", actor=a, id=task, reason=summary,
                                                    agent=a, blocked_on=blocked_on)}
    return {"verb": verb, "result": store.apply("fail", actor=a, id=task, reason=summary,
                                                agent=a)}


def raise_question(*, question: str, default: str, agent: str | None = None,
                   task: str | None = None, **_) -> dict:
    """`ask`. `default` is mandatory and this wrapper does not default it.

    Without a default the operator's silence stalls the task forever. With one, silence is a
    usable answer. That is why the engine's signature has no default for it and why adding one
    here would quietly undo the rule.
    """
    if not (default or "").strip():
        raise ValueError(
            "`default` is required: say what happens if the operator never answers. Without it "
            "silence stalls the task forever instead of deciding it.")
    a = _actor(agent)
    return store.apply("ask", actor=a, question=question, agent=a, task=task, default=default)


def book_receipt(*, action: str, subject_type: str, subject_id: str, department: str,
                 title: str, detail: str = "", agent: str | None = None,
                 caused_by_event_id: str | None = None, approval_ref: str | None = None,
                 external: bool = False, canon_touching: bool = False, **_) -> dict:
    """D2's `receipt book`.

    **This one refuses, and the refusal states which of two conditions it is refusing on.** That
    distinction is the whole point of the function: an earlier version reported one cause (an
    unbuilt seam) for what was in fact the other (this server could not import the adapter), and
    a refusal that mislabels itself sends every reader to the wrong repair.

    `receipt book` is not a registered `store.transition`. D2 implements it in
    `adapter/brain_adapter/receipt.py`, where booking a receipt is a git commit through the
    promotion door, not a Postgres row. This tool wraps that function or refuses; it never grows
    a second implementation, because a second implementation of a booking verb is how the ledger
    stops being one ledger.
    """
    would = {"action": action, "subject_type": subject_type, "subject_id": subject_id,
             "department": department, "title": title, "external": external,
             "canon_touching": canon_touching}
    try:
        from brain_adapter import receipt as _r                      # noqa: F401
    except ImportError:
        return {
            "ok": False,
            "unavailable": "receipt book",
            "cause": "adapter-not-importable",
            "reason": (
                "D2's `brain_adapter` cannot be imported from this process, so this tool cannot "
                "reach the one implementation of `receipt book`. This is an installation fault "
                "in THIS server and not an unbuilt seam: the verb exists, and its CLI door "
                "(`brain_adapter.cli receipt book`) works. Repair the server's sys.path -- see "
                "the bootstrap at the top of this module, which appends `adapter/` -- rather "
                "than reimplementing the verb here."),
            "would_have_booked": would,
        }
    return {
        "ok": False,
        "unavailable": "receipt book",
        "cause": "operator-decision-pending",
        "reason": (
            "the adapter IS importable here, so nothing about the seam is what stops this. Two "
            "things do. (1) THE OPERATOR'S DECISION, which is why this is still a refusal: "
            "booking a receipt is a git commit into the operator's brain through the promotion "
            "door. `receipt book` is not `accept work`, so it is not self-approval and D00 rule "
            "4 does not forbid it, but whether an agent may commit into the operator's brain is "
            "the operator's call and has not been made. (2) THE ARGUMENT MAPPING, which is not a "
            "one-line wiring job: `brain_adapter.receipt.Receipt` requires `date`, `slug`, "
            "`moment` and `summary` plus a resolved `Lineage`, and this tool's schema supplies "
            "none of the five. `action` here is not a Receipt field at all -- the store's "
            "`action` column is derived from `moment`, which is enum-checked against WAGER-14a's "
            "four booking moments, while this argument is free text. A tool that guessed the "
            "five would be inventing the provenance the receipt exists to record. Until both are "
            "settled, book through D2's CLI door, where a human supplies them."),
        "would_have_booked": would,
    }


def resolve_entity(*, ref: str, brain_root: str | None = None, **_) -> dict:
    """D2's `entity resolve`. A read, and it fails loudly rather than fabricating an id.

    On failure `produced_by` is a real NULL with the raw reference preserved beside it, which is
    D2's rule: a fabricated id in a lineage column is worse than an empty one, because the empty
    one is visible.

    **The index is built with `EntityIndex.build`, the same constructor D2's CLI door uses, and
    the bare `EntityIndex(root)` here was a defect rather than a shortcut.** `__init__` takes
    `nodes=None` and gives back an index over nothing; `build` is what scans the brain. So the
    bare form resolved every reference in the world to `unresolved` -- not an error, an answer,
    and a confident wrong one. That is the worst shape this particular tool can fail in, because
    D2 gives `unresolved` a meaning downstream: `produced_by` NULL with the raw ref beside it. A
    resolver that always returns it fills lineage with NULLs that look like findings.
    """
    root = brain_root or os.environ.get("BRAIN_ROOT")
    if not root:
        return {"ok": False, "status": "unresolved",
                "reason": "no brain root configured. Set BRAIN_ROOT or pass brain_root. "
                          "Guessing a repo path is how a resolver reads the wrong brain."}
    try:
        from brain_adapter import config as _cfg
        from brain_adapter.index import EntityIndex
    except ImportError:
        return {"ok": False, "status": "unavailable",
                "reason": "D2's brain_adapter is not importable from here. `entity resolve` is "
                          "D2's verb and this tool will not reimplement a resolver."}
    res = EntityIndex.build(_cfg.brain_root(root), _cfg.cache_dir()).resolve(ref)
    return {"ok": res.ok, "status": res.status, "entity_id": res.entity_id, "path": res.path,
            "matched_by": res.matched_by, "candidates": res.candidates}


# --------------------------------------------------------------------------- the registry

# `verb` is the transition each tool wraps, and it is data so the test can enumerate it. A tool
# with `verb: None` is a read or a wrapper over another lane's module.
TOOLS = {
    "read_queue": {
        "fn": read_queue, "verb": None, "write": False,
        "description": "What `claim` would take next, in order, without taking it.",
        "schema": {"type": "object", "properties": {
            "lane": {"type": "string", "description": "restrict to one lane"},
            "limit": {"type": "integer", "default": 20}}},
    },
    "read_recommendations": {
        "fn": read_recommendations, "verb": None, "write": False,
        "description": "Open recommendations, each with the rationale that argues against it.",
        "schema": {"type": "object", "properties": {
            "state": {"type": "string", "default": "pending",
                      "enum": ["pending", "accepted", "rejected"]},
            "limit": {"type": "integer", "default": 20}}},
    },
    "emit_event": {
        "fn": emit_event, "verb": "event emit", "write": True,
        "description": "Emit one event. `external` and `canon_touching` are required, not "
                       "defaulted: a producer that forgets is the one whose event needed a gate.",
        "schema": {"type": "object",
                   "required": ["type", "external", "canon_touching"],
                   "properties": {
                       "type": {"type": "string",
                                "description": "a REGISTERED event type. Inventing one is "
                                               "refused at emit time."},
                       "external": {"type": "boolean"},
                       "canon_touching": {"type": "boolean"},
                       "payload_summary": {"type": "string",
                                           "description": "4096 bytes max; REJECTED above, never "
                                                          "truncated. Oversize goes behind "
                                                          "payload_ref."},
                       "payload_ref": {"type": "string"},
                       "department": {"type": "string"}, "lane": {"type": "string"},
                       "subject_type": {"type": "string"}, "subject_id": {"type": "string"},
                       "work_item_id": {"type": "string"}, "session_id": {"type": "string"},
                       "agent": {"type": "string"}}},
    },
    "claim_work": {
        "fn": claim_work, "verb": "claim", "write": True,
        "description": "Claim the next task in your lanes. First claimer wins; getting nothing "
                       "is not an error.",
        "schema": {"type": "object", "required": ["lanes"], "properties": {
            "lanes": {"type": "array", "items": {"type": "string"}},
            "agent": {"type": "string"}, "host": {"type": "string"},
            "role": {"type": "string"}}},
    },
    "report_progress": {
        "fn": report_progress, "verb": "heartbeat", "write": True,
        "description": "Heartbeat so the reaper does not take your task back, and optionally "
                       "leave a note on the task thread.",
        "schema": {"type": "object", "properties": {
            "status": {"type": "string", "default": "working",
                       "enum": ["working", "waiting", "idle"]},
            "task": {"type": "string"}, "note": {"type": "string"},
            "agent": {"type": "string"}, "host": {"type": "string"},
            "pid": {"type": "integer"}, "role": {"type": "string"}}},
    },
    "finish_work": {
        "fn": finish_work, "verb": "done", "write": True,
        "description": "Report your task finished, blocked or failed. `done` is the report, not "
                       "the acceptance: a human accepts.",
        "schema": {"type": "object", "required": ["task", "outcome", "summary"], "properties": {
            "task": {"type": "string"},
            "outcome": {"type": "string", "enum": ["done", "blocked", "failed"]},
            "summary": {"type": "string",
                        "description": "what changed, where, how you verified it, and what the "
                                       "next agent needs to know. Stored in full, never cut."},
            "blocked_on": {"type": "string"}, "agent": {"type": "string"}}},
    },
    "raise_question": {
        "fn": raise_question, "verb": "ask", "write": True,
        "description": "Ask the operator one specific question. `default` is mandatory.",
        "schema": {"type": "object", "required": ["question", "default"], "properties": {
            "question": {"type": "string"},
            "default": {"type": "string",
                        "description": "what happens if the operator never answers"},
            "task": {"type": "string"}, "agent": {"type": "string"}}},
    },
    "book_receipt": {
        "fn": book_receipt, "verb": "receipt book", "write": True, "owner": "D2",
        "description": "Book a receipt through D2's one door. Not a store transition: a receipt "
                       "is a git commit, and this tool refuses to write one any other way.",
        "schema": {"type": "object",
                   "required": ["action", "subject_type", "subject_id", "department", "title"],
                   "properties": {
                       "action": {"type": "string"}, "subject_type": {"type": "string"},
                       "subject_id": {"type": "string"}, "department": {"type": "string"},
                       "title": {"type": "string"}, "detail": {"type": "string"},
                       "caused_by_event_id": {"type": "string"},
                       "approval_ref": {"type": "string"},
                       "external": {"type": "boolean"}, "canon_touching": {"type": "boolean"},
                       "agent": {"type": "string"}}},
    },
    "resolve_entity": {
        "fn": resolve_entity, "verb": "entity resolve", "write": False, "owner": "D2",
        "description": "Resolve a reference to a brain entity id. Fails loudly rather than "
                       "fabricating an id.",
        "schema": {"type": "object", "required": ["ref"], "properties": {
            "ref": {"type": "string"}, "brain_root": {"type": "string"}}},
    },
}

# Anything matching these must never appear in a tool name, a schema property, or an argument
# this server will forward. Asserted by test rather than trusted to review.
FORBIDDEN = ("permission_mode", "bypassPermissions", "config", "allowed_tools", "engine_args",
             "add_dirs", "config_dirs", "run_command", "exec", "shell", "eval")


def call(name: str, arguments: dict) -> dict:
    if name not in TOOLS:
        raise KeyError(f"no such tool: {name}. The nine are: {', '.join(sorted(TOOLS))}. There "
                       f"is no generic run-a-verb tool and there will not be one.")
    bad = sorted(k for k in arguments if any(f in k.lower() for f in FORBIDDEN))
    if bad:
        raise ValueError(
            f"refused: {', '.join(bad)}. Nothing touching config, permission_mode or "
            f"bypassPermissions is reachable from this server. `permission_mode` is what makes "
            f"an agent's self-report trustworthy; an agent that could change it could change "
            f"the thing its own trustworthiness rests on.")
    return TOOLS[name]["fn"](**arguments)
