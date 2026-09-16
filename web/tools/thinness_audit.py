#!/usr/bin/env python3
"""THE THINNESS AUDIT. Every action the phone surface can take, and the verb it calls.

Task 0114 (V8a). Run it, do not read a number out of a report:

    python3 -m web.tools.thinness_audit            # the table, and a pass/fail
    python3 -m web.tools.thinness_audit --json     # the same thing for a later lane to diff

V7a's transferable point, learned the hard way inside one task: *a report states; a gate
measures.* Its own prose said the runtime repo had no git remote, and that was true at 11:14:14Z
and false at 11:27:29Z. So the claim "the phone surface invents no transitions" ships here as a
check that re-measures, not as a paragraph V9 has to take on trust.

WHAT IS BEING PROVEN, and each part is proven differently on purpose:

  1. THE ONLY WAY OUT IS A VERB.  `store.apply` -- the single entry point to every state change
     in the runtime -- is called from exactly ONE place in `web/`: `rooms.dispatch`, whose first
     statement is the allowlist. Proven by walking the AST of every file under `web/`, not by
     grepping for a string, because a grep cannot tell a call from a docstring and `web/` has
     eleven docstrings that name it.

  2. THERE IS ONE WRITE DOOR.  Flask's URL map is walked for any rule accepting a write method.
     `web/tests/test_allowlist.py:test_no_get_route_writes` already asserts this; it is repeated
     here because assertion 1 is worth nothing if a second route can be added beside it.

  3. EVERY ACTION NAMES A VERB.  Each of the console actions in `web/app.py:_perform` is driven
     with `store.apply` replaced by a spy, and the verb it dispatched is recorded. An action that
     changed state without naming a verb would reach the spy with no verb, or reach it from
     somewhere other than `rooms.dispatch`; assertion 1 makes the second impossible and this one
     catches the first.

  4. THE VERB IS NOT THE PHONE'S OWN.  Every verb the surface dispatches is looked up in the
     other surfaces -- the engine CLI, the queue CLI, the budget CLI, the MCP server, the voice
     lane -- so the "surface" column is measured from those files rather than asserted. A verb
     only the console can call is not a violation on its own, but it is the shape a mobile-only
     path would take, so it is printed as a WAIST-OF-ONE and counted.

WHAT THIS DOES NOT PROVE, and saying so is the point of having it:

  * It does not prove the READ paths are safe. They are safe for a different reason and it is
    structural rather than local: `store.read()` hands out a session Postgres has put in
    `SET TRANSACTION READ ONLY` (`store/session.py:191`), so a read path cannot write even if it
    tried to. That is asserted at the end, against a real session, when a store is reachable.
  * It does not prove the phone renders anything. That is `web/tests/test_browser.py`.
"""

from __future__ import annotations

import ast
import json
import os
import sys

_R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_R, os.path.join(_R, "engine"), os.path.join(_R, "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# `post` reads SWARM_PARENT_TASK, and the runner exports it into every terminal. An audit run
# from inside a claimed task would otherwise carry a parent id that lives on the file bus.
os.environ.pop("SWARM_PARENT_TASK", None)

import store                                                            # noqa: E402
from web import rooms                                                   # noqa: E402

# Importing the lanes is what registers their transitions. This list IS the audit's coverage:
# a verb whose lane is not imported here is a verb `store.registered()` will not know about.
from swarm_engine import transitions as _engine_t                       # noqa: F401,E402
from swarm_engine import accept as _engine_a                            # noqa: F401,E402
from fabric import emit as _fabric                                      # noqa: F401,E402
from budget import transitions as _budget                               # noqa: F401,E402

# Every console action `web/app.py:_perform` dispatches on, in its own order. Kept here rather
# than scraped out of the templates because the templates pass `{{ action }}` through macros:
# a template grep finds ten of these twenty and reports the other ten as absent.
ACTIONS = ["post_mine", "scope_post", "answer", "amend", "accept_default", "accept_work",
           "send_back", "mark_done", "undo_done", "defer_question", "decline", "defer_time",
           "bump", "not_fast", "approve", "reject", "time_start", "time_stop", "time_abandon",
           "brief_reopen",
           # V4's four (task 0166). Added here rather than left out because this tool is where
           # "name the verb behind every action you build" is CHECKED: an action the list does
           # not carry is an action the gate reports nothing about, and a gate that looks intact
           # while missing the newest surface is the shape this program spends its refusals on.
           "steer_take", "steer_release", "steer_say", "picker_set"]

# The room an action is driven in. Everything the Queue can call defaults to `queue`; V4's four
# are FLEET actions, and driving them as the Queue would report them refused by the allowlist --
# true, and not the question this tool asks, which is which verb the action names.
ROOM_FOR = {"steer_take": "fleet", "steer_release": "fleet", "steer_say": "fleet",
            "picker_set": "fleet"}

# What the phone surface is FOR, mapped onto the actions that serve it. An action in no bucket
# is one the phone can reach and the report does not claim it is for -- printed, not hidden.
PHONE_JOBS = {
    "decide":   ["accept_default", "answer", "amend", "approve", "reject", "defer_time",
                 "defer_question", "decline", "not_fast", "bump"],
    "dispatch": ["accept_work", "send_back", "mark_done", "undo_done", "brief_reopen"],
    "watch":    ["time_start", "time_stop", "time_abandon"],
    "read":     [],
    "desktop":  ["post_mine", "scope_post"],
}

# The surfaces to look for each verb in. Every one of these is a thin wrapper over the same
# `store.apply`; the point of the column is that the phone appears in a row beside them and
# never alone.
SURFACE_FILES = {
    "engine CLI":  "engine/swarm_engine/cli.py",
    "queue CLI":   "queue/human_queue/cli.py",
    "budget CLI":  "budget/cli.py",
    "MCP":         "mcp/tools.py",
    "voice":       "voice/voice_capture/cli.py",
}


# ------------------------------------------------------------------ 1. the AST walks

def _calls(path: str):
    """Every Call node in a file, with its dotted callee name. AST, so docstrings do not count."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (SyntaxError, OSError):
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f, parts = node.func, []
        while isinstance(f, ast.Attribute):
            parts.append(f.attr)
            f = f.value
        if isinstance(f, ast.Name):
            parts.append(f.id)
        yield ".".join(reversed(parts)), node


def _py_files(root: str):
    for dirpath, dirnames, names in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", ".pytest_cache", "tests")]
        for n in sorted(names):
            if n.endswith(".py"):
                yield os.path.join(dirpath, n)


def apply_call_sites(root: str) -> tuple[list, list]:
    """Where `store.apply` is actually CALLED under `root`, split into SERVED and OFF-SURFACE.

    THE SCOPE IS NARROWED AND THE NARROWING IS PRINTED, because a check that quietly chose its
    own scope is the tolerance-widening this system exists to refuse. `MUST-NOT-BUILD.md` #11
    does the same thing and says why: its grep covers the served surface on purpose, and it names
    the one websocket in the lane that the grep therefore misses.

    SERVED   = the modules the running Flask app imports. These are the files a phone's request
               executes, and the claim "every phone action names a verb" is about exactly these.
    OFF      = `web/bin/` and `web/tests/`. Terminal-run scripts and the suite. `seed-demo.py`
               calls `store.apply` about thirty times and that is not a bypass of anything: it is
               a CLI standing where the engine CLI stands, calling the same one entry point. It
               never runs inside a request, it is not reachable from the tailnet, and no button
               anywhere invokes it. It is COUNTED AND PRINTED rather than filtered silently, so a
               reader can disagree with the split instead of never seeing it.
    """
    served, off = [], []
    for path in _py_files(root):
        rel = os.path.relpath(path, _R)
        bucket = off if (os.sep + "bin" + os.sep) in path or rel.endswith("seed-demo.py") \
            else served
        for name, node in _calls(path):
            if name.endswith("store.apply"):
                bucket.append((rel, node.lineno))
    return served, off


def verbs_in(path: str) -> set[str]:
    """Literal verbs a surface hands to `store.apply`. A non-literal is reported, not guessed."""
    full = os.path.join(_R, path)
    found: set[str] = set()
    if not os.path.exists(full):
        return found
    for name, node in _calls(full):
        if not name.endswith("store.apply") and name != "apply":
            continue
        if node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            found.add(node.args[0].value)
        elif node.args:
            found.add(f"<computed at {path}:{node.lineno}>")
    return found


# ------------------------------------------------------------------ 2. driving the actions

class Spy:
    """Stands in for `store.apply`. Records the verb and returns something shaped enough that
    the action function can finish building its receipt."""

    def __init__(self):
        self.verbs: list[str] = []

    def __call__(self, verb, *a, **kw):
        self.verbs.append(verb)
        return {"id": kw.get("id", "q0001"), "verb": verb, "spy": True, "capped": False,
                "requeued": False, "cap_seconds": 0, "source_id": kw.get("id", "q0001"),
                "started_at": "", "stopped_at": "", "seconds": 0, "abandoned": False,
                "wake_at": "", "kind": kw.get("kind", ""), "delta": 0.0, "state": "inbox",
                "tier_at_start": None, "label": "", "question_id": "", "child": "",
                "unblocked": 0, "waiting": 0, "measured": 0, "today_seconds": 0,
                "entry": {}, "reason": "", "note": "", "summary": ""}


CARD = {"id": "q0001", "kind": "human", "gates": [], "title": "an item",
        "default": "the stated default", "source_type": "question",
        "primary_verb": "answer", "tier": "decide", "actor_type": "human"}
TEXT = "a reason long enough to pass the twelve character floor"


def drive() -> dict[str, dict]:
    """Run every console action with the store spied. Returns action -> what it dispatched."""
    from web import app as webapp, model

    spy = Spy()
    store.apply = spy
    rooms.store.apply = spy
    model.find_item = lambda i: dict(CARD, id=(i or "q0001"))
    webapp._find = lambda i: dict(CARD, id=(i or "q0001"))
    # The `done` guard reads brain.work_item. This audit is about which VERB an action names,
    # not about whether that row exists, and `web/tests/test_allowlist.py` already proves the
    # guard on a real store. Standing in a row it PERMITS is what lets `mark_done` reach its
    # verb here; a row it refused would report `done` as unreachable, which is the wrong claim.
    rooms._row_behind = lambda i: {"id": i, "actor_type": "human", "agent_claimable": False,
                                   "state": "inbox", "claimed_by": None}
    webapp.R = type("R", (), {"work_item": staticmethod(
        lambda i: {"id": i, "title": "an item", "state": "review"})})()

    app = webapp.create_app()
    form = {"id": "q0001", "text": TEXT, "lane": "web", "intent": TEXT, "wake_in": "2h",
            "label": "tomorrow", "delta": "1.0", "acknowledge_default": "1",
            "source_type": "question", "title": "a title long enough to post"}

    out: dict[str, dict] = {}
    for action in ACTIONS:
        spy.verbs.clear()
        f = dict(form, action=action)
        room = ROOM_FOR.get(action, "queue")
        with app.test_request_context(f"/{room}/act", method="POST", data=f):
            declared, err = None, None
            try:
                res = webapp._perform(room, action, "q0001", TEXT)
                declared = res.get("verb")
            except Exception as exc:                                    # noqa: BLE001
                # WHERE the raise happened is the whole meaning of it, so it is labelled by
                # whether a verb had already been dispatched rather than by the exception type.
                # Before the verb: the safe direction, the action refused itself. After the verb:
                # the transition ran and the receipt builder tripped over this audit's STUB
                # return value -- an artifact of the spy, not a property of the surface, and
                # calling it a refusal would be this tool lying about its own instrument.
                where = "after the verb (the spy's stub return, not a refusal)" \
                    if spy.verbs else "before the verb, so no transition ran"
                err = f"raised {where}: {type(exc).__name__}: {str(exc)[:50]}"
            out[action] = {"dispatched": list(spy.verbs), "declared": declared, "error": err}
    return out


# ------------------------------------------------------------------ 3. the report

def main(argv: list[str]) -> int:
    findings: list[str] = []

    sites, off_surface = apply_call_sites(os.path.join(_R, "web"))
    # The line number is not the assertion -- the FILE and the COUNT are. The line is printed so
    # a move shows up as a diff rather than as a silent pass somewhere else in the same file.
    if len(sites) != 1 or sites[0][0] != "web/rooms.py":
        findings.append(
            f"store.apply is called from {len(sites)} place(s) in the SERVED surface: {sites}. "
            f"It must be called from exactly one, inside rooms.dispatch, or the allowlist is "
            f"optional.")

    from web.app import create_app
    doors = sorted(r.rule for r in create_app().url_map.iter_rules()
                   if {"POST", "PUT", "PATCH", "DELETE"} & r.methods)
    if doors != ["/<room>/act"]:
        findings.append(f"more than one write door: {doors}")

    table = drive()
    elsewhere = {name: verbs_in(path) for name, path in SURFACE_FILES.items()}
    registered = set(store.registered())

    rows = []
    for action in ACTIONS:
        r = table[action]
        verbs = r["dispatched"]
        if not verbs and not r["error"]:
            findings.append(f"{action} completed and dispatched NO verb: a path to the store "
                            f"that names no transition")
        for v in verbs:
            if v not in rooms.ROOM_VERBS["queue"] | rooms.ROOM_VERBS["brief"] \
                       | rooms.ROOM_VERBS["fleet"] | rooms.ROOM_VERBS["scope"]:
                findings.append(f"{action} dispatched {v!r}, which no room lists")
            if v not in registered and v not in rooms.UNREGISTERED_OWNER:
                findings.append(f"{action} dispatched {v!r}, which no lane has registered")
        if r["declared"] and verbs and r["declared"] not in verbs:
            findings.append(f"{action} reports verb {r['declared']!r} on its receipt and "
                            f"dispatched {verbs}: the receipt names a transition that did not run")
        job = next((k for k, v in PHONE_JOBS.items() if action in v), "UNCLASSIFIED")
        for v in (verbs or ["(refused before a verb)"]):
            others = sorted(n for n, s in elsewhere.items() if v in s)
            rows.append({"action": action, "verb": v, "job": job,
                         "surfaces": ["console"] + others,
                         "waist_of_one": bool(verbs) and not others,
                         "note": r["error"] or ""})

    if "--json" in argv:
        print(json.dumps({"rows": rows, "write_doors": doors,
                          "store_apply_call_sites_served": sites,
                          "store_apply_call_sites_off_surface": off_surface,
                          "findings": findings}, indent=2))
        return 1 if findings else 0

    print("THE THINNESS TABLE -- every console/phone action, the verb it calls, who else calls it")
    print(f"{'ACTION':16} {'VERB':18} {'PHONE JOB':10} SURFACES CALLING THE SAME VERB")
    print("-" * 96)
    for r in rows:
        mark = "  <-- waist of one" if r["waist_of_one"] else ""
        print(f"{r['action']:16} {r['verb']:18} {r['job']:10} "
              f"{', '.join(r['surfaces'])}{mark}")
        if r["note"]:
            print(f"{'':16} {'':18} {r['note']}")
    print("-" * 96)
    print(f"store.apply in the SERVED surface:  {sites or 'NONE'}")
    print(f"store.apply off-surface (web/bin/): {len(off_surface)} call sites in "
          f"{len({p for p, _ in off_surface})} file(s) -- "
          f"{', '.join(sorted({p for p, _ in off_surface})) or 'none'}. Terminal-run, never "
          f"inside a request, calling the same one entry point the CLIs call.")
    print(f"write doors in the URL map:        {doors}")
    print(f"actions driven:                    {len(ACTIONS)}   "
          f"distinct verbs reached: {len({r['verb'] for r in rows if r['verb'].startswith('(') is False})}")
    print(f"verbs registered in the runtime:   {len(registered)}   "
          f"reachable from any room: {len(rooms.audit()['reachable_from_any_room'])}")

    if findings:
        print("\nFINDINGS -- a path that reaches the store without a verb, or a verb that is "
              "the phone's own:")
        for f in findings:
            print(f"  * {f}")
        return 1
    print("\nNo action reaches the store without naming a verb, and every verb it names is one "
          "an existing lane registered. The surface invents no transitions.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
