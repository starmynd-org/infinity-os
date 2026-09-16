"""Condition 2 of the item 11 overrule, proved the way item 6's own test proves Study.

    python3 -m web.tests.test_terminal_carries_no_write

MUST-NOT-BUILD item 11 was overruled by the operator on 2026-08-28 for a typable pane, on two
conditions, and the second is the one this file exists for:

    "The socket carries the terminal pane and nothing else. No state write crosses it; every
     write stays on the `/<room>/act` door where the room allowlist and the room-scoped CSRF
     token live."

WHY THE PROOF IS SHAPED LIKE THIS AND NOT LIKE A GREP. `web/tests/test_terminal.py` already
asserts that `web/terminal.py` does not import `store`, `rooms` or `actions`, which is a claim
about today's source. The claim the operator was actually given is about the TRANSPORT: that
nothing a request can carry to a terminal endpoint reaches a verb. So this file borrows the shape
of the load-bearing test in the lane, `test_allowlist.py:test_study_calls_zero_verbs`: enumerate
EVERY verb registered anywhere in the runtime, put each one at the transport, and assert twice --
that it was refused, and that `store.apply` was never entered.

THE DEFECT IT IS SHAPED AGAINST IS ON THE RECORD AND IS NOT HYPOTHETICAL. Task 0147: the Study
allowlist was airtight and the room it keyed on was not, because `room` was read from
`request.form`, so a POST from Study saying `room=queue` ran `reopen` on a live task. **An
allowlist keyed on an attacker-supplied string is an allowlist keyed on nothing.** A second write
path that skipped the door would restate that defect in a new transport, so case 4 below sends
every verb name at the pane's own endpoints IN EVERY FIELD THEY COULD BE SMUGGLED IN, holding a
valid pane token and a real live pty, and watches the spy stay empty.

THIS SUITE WRITES NO STORE ROW. It reads the store through `create_app()` and it replaces
`store.apply` with a spy for the duration of every case that could conceivably reach it, so even
a regression that DID reach a verb would be recorded rather than executed.
"""

from __future__ import annotations

import os
import re
import sys
import time
import traceback

_R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_R, os.path.join(_R, "engine"), os.path.join(_R, "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import store                                                            # noqa: E402
from web import rooms, terminal                                         # noqa: E402

# Importing the lanes is what registers their transitions, so this import list IS the
# enumeration's coverage. It is the same list `test_allowlist.py` carries, on purpose: if the two
# enumerations ever differ in size, one of them is measuring a smaller runtime than it claims.
from swarm_engine import transitions as _engine_t                       # noqa: E402,F401
from swarm_engine import accept as _engine_a                            # noqa: E402,F401
from fabric import emit as _fabric                                      # noqa: E402,F401
from budget import transitions as _budget                               # noqa: E402,F401

# `post` reads SWARM_PARENT_TASK as its default parent and the runner exports it into every
# terminal. Nothing here reaches `post`, but that is immunity by accident and the next test
# written in this file would inherit the trap.
os.environ.pop("SWARM_PARENT_TASK", None)

# BUILT ONCE, HERE, AND THE ORDER IS THE POINT. `create_app()` is what registers the console's own
# verbs (the queue lane's, the image lane's, the recommendation lane's), so an enumeration taken
# before this line sees 47 of them and an enumeration taken after sees 69. Two cases in one file
# reporting two different denominators would be a suite arguing with itself, so the app is
# constructed at import and every case below counts the same runtime.
from web.app import create_app                                          # noqa: E402

APP = create_app()

FAILS: list = []
CHECKS = 0


def ck(name, cond, got=""):
    global CHECKS
    CHECKS += 1
    print(("  ok   " if cond else "  FAIL ") + name + (f"   [{got}]" if got and not cond else ""))
    if not cond:
        FAILS.append(name)


class Spy:
    """Stands in for `store.apply`, and its whole job is to stay empty."""

    def __init__(self):
        self.calls = []

    def __call__(self, verb, *a, **kw):
        self.calls.append((verb, kw))
        return {"id": kw.get("id", "spy"), "spy": True}


def _verbs() -> list:
    return sorted(store.registered())


# --------------------------------------------------------------------------- 1. the enumeration

def test_the_enumeration_is_not_vacuous():
    """A verdict over an empty set is not a pass, so the denominator is checked before it is used."""
    print("\ncase 1 · the denominator")
    verbs = _verbs()
    ck(f"the runtime registers {len(verbs)} verbs, and the enumeration reaches all of them",
       len(verbs) >= 30, got=f"only {len(verbs)} registered")
    print(f"  ---- {len(verbs)} verbs enumerated: {', '.join(verbs)}")
    return verbs


# --------------------------------------------------- 2. the room name, at the dispatcher

def test_every_verb_is_refused_from_the_terminal_room():
    """`rooms.dispatch('terminal', verb)` for all of them, and `store.apply` never entered.

    `terminal` is not in `rooms.ROOMS` at all, which is STRONGER than an empty allowlist: an empty
    allowlist is a set somebody can add one entry to, and a room that does not exist is refused by
    name before any set is consulted. Study's version of this test is the model.
    """
    print("\ncase 2 · every registered verb, dispatched at room 'terminal'")
    verbs = _verbs()
    spy = Spy()
    real = rooms.store.apply
    rooms.store.apply = spy
    refused = 0
    wrong = []
    try:
        for verb in verbs:
            try:
                rooms.dispatch("terminal", verb, item={"id": "x", "actor_type": "human"})
            except rooms.RoomRefusal:
                refused += 1
                continue
            except Exception as exc:                                    # noqa: BLE001
                wrong.append(f"{verb} raised {exc.__class__.__name__}, not RoomRefusal")
                continue
            wrong.append(f"{verb} was NOT refused")
    finally:
        rooms.store.apply = real
    ck(f"all {refused} of {len(verbs)} were refused by the allowlist", refused == len(verbs),
       got="; ".join(wrong[:4]))
    ck("store.apply was never entered", not spy.calls, got=str(spy.calls[:3]))
    ck("'terminal' is not a room at all, so the refusal is by name",
       "terminal" not in rooms.ROOMS and "terminal" not in rooms.ROOM_VERBS)


# --------------------------------------------------- 3. the write door, by name

def test_the_write_door_refuses_the_pane_by_name():
    print("\ncase 3 · the one write door, asked for the pane")
    app = APP
    client = app.test_client()
    spy = Spy()
    real = rooms.store.apply
    rooms.store.apply = spy
    try:
        seen = set()
        for verb in _verbs():
            r = client.post("/terminal/act", headers={"Origin": "http://localhost"},
                            data={"csrf": "anything", "action": verb, "id": "0001",
                                  "text": "a reason long enough to pass the gate"})
            seen.add(r.status_code)
        ck(f"every verb at /terminal/act was refused, statuses {sorted(seen)}",
           seen and not ({200, 201} & seen), got=str(sorted(seen)))
    finally:
        rooms.store.apply = real
    ck("store.apply was never entered from /terminal/act", not spy.calls, got=str(spy.calls[:3]))


# --------------------------------------------------- 4. the transport itself, holding real keys

def _live_pane(app):
    """A real pty, opened through the real endpoint with a real token. Returns (client, tok, key).

    The refusals below only mean something against a pane that WORKS. A test that opened nothing
    would be measuring a 404 and calling it a security property.
    """
    client = app.test_client()
    body = client.get("/terminal").get_data(as_text=True)
    m = re.search(r'__TERMINAL_CSRF = "([^"]+)"', body)
    tok = m.group(1) if m else ""
    r = client.post("/terminal/open", headers={"Origin": "http://localhost"},
                    data={"csrf": tok, "kind": "shell", "rows": 24, "cols": 80})
    return client, tok, (r.get_json() or {}).get("key")


def test_no_field_on_the_transport_reaches_a_verb():
    """EVERY verb, in EVERY field it could be smuggled in, at EVERY endpoint the pane exposes.

    This is task 0147's defect aimed at the new transport. The pane's endpoints take `data`,
    `rows`, `cols`, `kind`, `task` and `engine`; a request may of course also carry `room`,
    `action`, `verb` and `id`, because a POST body carries whatever its author typed. The question
    is whether any of them is READ. The spy answers it.
    """
    print("\ncase 4 · every verb, in every field, at every endpoint of the pane")
    app = APP
    client, tok, key = _live_pane(app)
    ck("a real pane opened, so the refusals below are measured against a working surface",
       bool(key), got="no pty opened")
    if not key:
        return
    verbs = _verbs()
    endpoints = [("/terminal/open", {"kind": "shell"}),
                 (f"/terminal/{key}/input", {"data": ""}),
                 (f"/terminal/{key}/resize", {"rows": 24, "cols": 80}),
                 (f"/terminal/{key}/close", {})]
    smuggle_fields = ("room", "action", "verb", "kind", "task", "engine", "id", "data")
    spy = Spy()
    real = rooms.store.apply
    rooms.store.apply = spy
    posts = 0
    ran = []
    try:
        for path, base in endpoints:
            for verb in verbs:
                for field in smuggle_fields:
                    payload = dict(base)
                    payload["csrf"] = tok
                    payload[field] = verb
                    payload.setdefault("room", "queue")
                    r = client.post(path, headers={"Origin": "http://localhost"}, data=payload)
                    posts += 1
                    j = r.get_json() or {}
                    # A terminal endpoint answers about a pane: ok/key/bytes/screen/rows. It must
                    # never answer about an ITEM, which is what every verb receipt in this
                    # runtime carries.
                    if isinstance(j, dict) and ("item" in j or "receipt" in j or "applied" in j):
                        ran.append((path, field, verb, sorted(j)[:6]))
    finally:
        rooms.store.apply = real
    ck(f"{posts} crafted POSTs across {len(endpoints)} endpoints, "
       f"{len(verbs)} verbs and {len(smuggle_fields)} fields", posts > 0)
    ck("not one of them returned a verb receipt", not ran, got=str(ran[:2]))
    ck("store.apply was never entered from the transport", not spy.calls, got=str(spy.calls[:3]))
    print(f"  ---- {posts} POSTs, {len(verbs)} verbs, {len(smuggle_fields)} fields, "
          f"{len(endpoints)} endpoints, 0 reached store.apply")


# --------------------------------------------------- 5. the route table

def test_the_only_state_door_is_still_the_one_door():
    """Walk the URL map. Every write route is either THE door or a pane route with no verb in it.

    `test_allowlist.py:test_no_get_route_writes` asserts `/<room>/act` is the ONLY rule accepting
    a write method, and the pane's four endpoints make that assertion false as written. That is a
    real collision and it is reported rather than papered over: the patch this lane is asking for
    is in `outputs/2026-08-29-commander/crosstalk-B3.md`. What this check does is establish the
    property that patch would rest on, so the assertion can be narrowed WITHOUT being weakened:
    every write rule outside the door belongs to `web/terminal.py`, and case 4 above has already
    put all 47 verbs at every one of them.
    """
    print("\ncase 5 · the route table, walked")
    app = APP
    writable = [(r.rule, r.endpoint) for r in app.url_map.iter_rules()
                if {"POST", "PUT", "PATCH", "DELETE"} & r.methods]
    doors = [rule for rule, _ in writable if not rule.startswith("/terminal/")]
    pane = [(rule, ep) for rule, ep in writable if rule.startswith("/terminal/")]
    ck("the only state door is still /<room>/act", doors == ["/<room>/act"], got=str(doors))
    ck(f"the {len(pane)} other write rules all belong to the pane's blueprint",
       all(ep.startswith("terminal.") for _, ep in pane), got=str(pane))
    ck("and every one of them was exercised with every verb in case 4",
       {rule for rule, _ in pane} == {"/terminal/open", "/terminal/<key>/input",
                                      "/terminal/<key>/resize", "/terminal/<key>/close"},
       got=str(sorted(rule for rule, _ in pane)))
    print(f"  ---- {len(writable)} write rules: 1 door, {len(pane)} pane")


# --------------------------------------------------- 6. the pane still does its own job

def test_the_pane_still_types_after_all_that():
    """After 1500 crafted POSTs at it, a keystroke still reaches the process.

    A surface that refused everything INCLUDING its own feature would pass every check above and
    be worthless. This is the denominator's other half.
    """
    print("\ncase 6 · and the pane still works")
    app = APP
    client, tok, key = _live_pane(app)
    if not key:
        ck("a pane opened", False)
        return
    stamp = str(int(time.time()))[-6:]
    client.post(f"/terminal/{key}/input", headers={"Origin": "http://localhost"},
                data={"csrf": tok, "data": f"echo typed-{stamp}\r"})
    seen = False
    deadline = time.time() + 15
    while time.time() < deadline:
        r = client.get(f"/terminal/{key}/screen")
        j = r.get_json() or {}
        s = j.get("screen") or {}
        text = "\n".join("".join(run[0] for run in line) for line in s.get("lines") or [])
        hist = "\n".join("".join(run[0] for run in line)
                         for line in (s.get("history") or {}).get("lines") or [])
        if (text + hist).count(f"typed-{stamp}") >= 2:
            seen = True
            break
        time.sleep(0.05)
    ck("a command typed at the transport ran and its output came back", seen)
    client.post(f"/terminal/{key}/close", headers={"Origin": "http://localhost"},
                data={"csrf": tok, "hard": "1"})


def main() -> int:
    print("test_terminal_carries_no_write.py  --  condition 2 of the item 11 overrule, "
          "proved the way item 6 proves Study")
    # ORDERED EXPLICITLY, not by `sorted(globals())`. The cases are numbered in their docstrings
    # and an alphabetical sweep prints case 4 before case 1, which reads as a suite that lost its
    # place. The list is also the coverage: a test added to this file and not to this list does
    # not run, and that is louder than a test that runs in the wrong order.
    for fn in (test_the_enumeration_is_not_vacuous,
               test_every_verb_is_refused_from_the_terminal_room,
               test_the_write_door_refuses_the_pane_by_name,
               test_no_field_on_the_transport_reaches_a_verb,
               test_the_only_state_door_is_still_the_one_door,
               test_the_pane_still_types_after_all_that):
        try:
            fn()
        except Exception:                                               # noqa: BLE001
            FAILS.append(fn.__name__)
            print(f"  ERROR {fn.__name__}\n{traceback.format_exc()}")
    # Close anything this suite forked, so it does not leave children behind.
    for s in list(terminal._SESSIONS.values()):
        s.close(hard=True)
    if CHECKS == 0:                                                     # DENOMINATOR
        print("0 comparisons made. A verdict over an empty set is not a pass, and this suite "
              "asserts an ABSENCE, which is exactly the shape that reads green over nothing.")
        return 2
    print(f"\n{CHECKS - len(FAILS)} passed, {len(FAILS)} failed, of {CHECKS} compared")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
