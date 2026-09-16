"""The verb allowlist, proven rather than promised.

The load-bearing test is `test_study_calls_zero_verbs`. It does not check that the Study template
has no buttons: it enumerates EVERY verb registered anywhere in the runtime, posts each one at
the write door with `room=study`, and asserts two things -- the request was refused, and
`store.apply` was never entered. A surface that cannot act cannot bias a decision, and this is
what makes that sentence true rather than aspirational.

Run:  python3 -m web.tests.test_allowlist
"""

from __future__ import annotations

import pathlib
import sys
import traceback

import os as _os
import sys as _sys
_R = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
for _p in (_R, _os.path.join(_R, 'engine'), _os.path.join(_R, 'queue')):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import store
from web import rooms

# Importing the lanes registers their transitions. The enumeration below is only meaningful if
# every verb in the runtime is present, so this import list is the test's coverage.
from swarm_engine import transitions as _engine_t                       # noqa: F401
from swarm_engine import accept as _engine_a                            # noqa: F401
from fabric import emit as _fabric                                      # noqa: F401
from budget import transitions as _budget                               # noqa: F401

# `post` reads SWARM_PARENT_TASK as its default parent and the live runner exports it into
# every terminal, so an agent running this suite from inside a claimed task carries a parent
# id that exists on the file bus and not in the scratch store. Nothing here reaches `post`
# today -- the enumeration asserts every verb is REFUSED before store.apply -- but that is
# immunity by accident, and the next test in this file that actually posts would inherit the
# trap. Dropped for the same reason queue/tests/ and engine/tests/ drop it.
_os.environ.pop("SWARM_PARENT_TASK", None)

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"ok    {name}")
    except AssertionError as exc:
        FAIL.append((name, str(exc)))
        print(f"FAIL  {name}\n      {exc}")
    except Exception:                                                   # noqa: BLE001
        FAIL.append((name, traceback.format_exc()))
        print(f"ERROR {name}\n{traceback.format_exc()}")


class Spy:
    """Stands in for `store.apply` and records that it was NOT called."""

    def __init__(self):
        self.calls = []

    def __call__(self, verb, *a, **kw):
        self.calls.append((verb, kw))
        return {"id": kw.get("id", "spy"), "spy": True}


def test_study_calls_zero_verbs():
    verbs = sorted(store.registered())
    assert len(verbs) >= 30, f"only {len(verbs)} verbs registered; the enumeration is incomplete"
    spy = Spy()
    real = store.transitions.apply
    rooms.store.apply = spy
    try:
        for verb in verbs:
            try:
                rooms.dispatch("study", verb, item={"id": "x", "actor_type": "human"})
            except rooms.RoomRefusal:
                continue
            except Exception as exc:                                    # noqa: BLE001
                raise AssertionError(
                    f"{verb!r} from Study raised {exc.__class__.__name__} instead of "
                    f"RoomRefusal. It must be refused by the allowlist, first.") from None
            raise AssertionError(f"{verb!r} was NOT refused from the Study room")
    finally:
        rooms.store.apply = real
    assert not spy.calls, f"store.apply was entered from Study: {spy.calls}"
    assert len(rooms.ROOM_VERBS["study"]) == 0, "Study's allowlist is not empty"


def test_sessions_calls_zero_verbs():
    """The Sessions room's safety claim, proved the same way Study's is.

    Sessions is the proof surface for terminal logging (row `0424`). Its whole job is to be
    believed about coverage, which is exactly why it must not be able to change what it counts:
    a room that could run `ingest backfill` from beside the number that made you want to press it
    is a room whose numbers are an argument for its own buttons. The remedies stay on the CLI.

    This is a copy of `test_study_calls_zero_verbs` rather than a parametrisation of it on
    purpose. Both rooms carry the claim independently, and a shared loop would let one of them
    quietly stop being checked the day somebody narrows the other's fixture.
    """
    verbs = sorted(store.registered())
    assert len(verbs) >= 30, f"only {len(verbs)} verbs registered; the enumeration is incomplete"
    spy = Spy()
    real = store.transitions.apply
    rooms.store.apply = spy
    try:
        for verb in verbs:
            try:
                rooms.dispatch("sessions", verb, item={"id": "x", "actor_type": "human"})
            except rooms.RoomRefusal:
                continue
            except Exception as exc:                                    # noqa: BLE001
                raise AssertionError(
                    f"{verb!r} from Sessions raised {exc.__class__.__name__} instead of "
                    f"RoomRefusal. It must be refused by the allowlist, first.") from None
            raise AssertionError(f"{verb!r} was NOT refused from the Sessions room")
    finally:
        rooms.store.apply = real
    assert not spy.calls, f"store.apply was entered from Sessions: {spy.calls}"
    assert len(rooms.ROOM_VERBS["sessions"]) == 0, "Sessions' allowlist is not empty"


def test_every_room_refuses_what_it_does_not_list():
    spy = Spy()
    real = store.transitions.apply
    rooms.store.apply = spy
    try:
        for room, permitted in rooms.ROOM_VERBS.items():
            for verb in sorted(store.registered()):
                if verb in permitted:
                    continue
                try:
                    rooms.dispatch(room, verb, item={"id": "x", "actor_type": "human"})
                except rooms.RoomRefusal:
                    continue
                raise AssertionError(f"{room} did not refuse {verb!r}")
    finally:
        rooms.store.apply = real
    assert not spy.calls, f"a refused verb still reached store.apply: {spy.calls}"


def test_fleet_may_call_exactly_the_five_plus_the_operators_three():
    """The brief's five, plus the three the OPERATOR'S OVERRULE added, and nothing else.

    This test pinned five and the sentence it pinned them against was "and nothing else, ever".
    It now pins eight, and that is a widening rather than a loosened check, so it is written out
    rather than edited quietly:

      * `00-COMMANDER-HANDOFF.md` grants take-control in writing, bounded by four conditions.
        `steer take` and `steer release` are its verbs (task 0166).
      * `agent config set` is the account/model/effort picker, which is the same brief's second
        deliverable.

    What the check is FOR is unchanged: the set cannot drift by one verb without a test saying
    so. So it is still an exact equality, and the assertion below still fails on a ninth.
    """
    fleet = rooms.ROOM_VERBS["fleet"]
    assert fleet == frozenset({"answer", "reopen", "pause", "resume", "msg",
                               "steer take", "steer release", "agent config set"}), (
        "Fleet's list drifted from the brief plus the recorded overrule")
    # The guard that did NOT widen, asserted here because it is the one the original narrowness
    # was actually about: the console may now create work and may never fabricate a report.
    assert "done" not in fleet
    assert "claim" not in fleet and "cancel" not in fleet


class Rows:
    """Stands in for `rooms._row_behind`, the ONE read the write door makes, and records it.

    HAND-BUILDING THE ROW IS NOT THE DEFECT THIS REPLACES. The defect (task 0427) was
    hand-building the CARD: the guard's answer was a function of `item['actor_type']`, a key
    `web/model.py::_card` stamped on itself from the arm of `brain.queue_open` the row arrived
    on, so every test that supplied that key proved a property the console did not have and
    stayed green through the whole period the guard checked nothing.

    What is asserted below is the property that survives any card: the decision is a function of
    `brain.work_item`'s columns and of NOTHING the caller passes. That `_row_behind` really reads
    that table, for the card `model.find_item` really builds, is
    `web/tests/test_done_reads_the_row.py`'s claim -- it needs a store, and this suite is the
    DB-free one that every other room check lives in.
    """

    def __init__(self, **rows):
        self.rows, self.asked = rows, []

    def __call__(self, item_id):
        self.asked.append(item_id)
        return self.rows.get(item_id)


# Four shapes, as `brain.work_item` holds them. `HIS` is 0068's live shape: actor_type NULL,
# which is why the guard stopped asking about that column at all.
HIS = {"id": "0008", "actor_type": None, "agent_claimable": False, "state": "inbox",
       "claimed_by": ""}
FLEETS = {"id": "0001", "actor_type": None, "agent_claimable": True, "state": "inbox",
          "claimed_by": ""}
HELD = {"id": "0002", "actor_type": None, "agent_claimable": False, "state": "active",
        "claimed_by": "T7"}
HIS_MARKED = {"id": "0009", "actor_type": "human", "agent_claimable": False, "state": "inbox",
              "claimed_by": ""}


def test_done_is_refused_on_an_agent_item():
    """The prohibition was about ACTOR FORGERY, not buttons.

    An operator `done` on an agent's work fabricates that agent's report. The allowlist cannot
    tell the two cases apart because both are the verb `done`; the ROW can, and since task 0427
    the row is what is read. An agent item is one the fleet may claim, or one an agent is
    holding -- the second is the case a view could not close, because `queue_open` publishes
    neither column.
    """
    spy, lookup = Spy(), Rows(**{r["id"]: r for r in (HIS, FLEETS, HELD, HIS_MARKED)})
    real, real_row = store.transitions.apply, rooms._row_behind
    rooms.store.apply, rooms._row_behind = spy, lookup
    try:
        # THE CARD CANNOT BUY PERMISSION. Every one of these dicts asserts the exact key the old
        # guard turned on, over a row that is not his.
        for row, why in ((FLEETS, "the fleet may claim it"), (HELD, "an agent is holding it")):
            try:
                rooms.dispatch("queue", "done", item={"id": row["id"], "actor_type": "human"},
                               id=row["id"], summary="x")
            except rooms.ItemRefusal:
                continue
            raise AssertionError(f"`done` was allowed on a row where {why}, because the item "
                                 f"claimed actor_type=human")
        # AND THE CARD CANNOT LOSE IT EITHER. His own row, with no actor_type on the card at all
        # -- which is what `_card` now produces -- and NULL in the column, is permitted.
        rooms.dispatch("queue", "done", item={"id": HIS["id"], "kind": "human"},
                       id=HIS["id"], summary="x")
        assert spy.calls and spy.calls[-1][0] == "done", "the operator's own case did not reach "\
            "the verb"
        assert lookup.asked[-1] == HIS["id"], \
            f"the guard looked up {lookup.asked[-1]!r}, not the row the verb acts on"
        # The thirteen rows migration 26's trigger coerces: human, therefore not claimable.
        rooms.dispatch("queue", "done", item={"id": HIS_MARKED["id"]}, id=HIS_MARKED["id"],
                       summary="x")
        assert spy.calls[-1][0] == "done", "an actor_type=human row was refused"
    finally:
        rooms.store.apply, rooms._row_behind = real, real_row


def test_done_checks_the_row_the_verb_will_act_on():
    """A guard that reads one row while `store.apply` writes another is not a guard.

    `dispatch` is handed the card and the verb's kwargs separately, so the two ids can differ.
    They must not, and the missing row case is refused rather than waved through.
    """
    spy, lookup = Spy(), Rows(**{HIS["id"]: HIS})
    real, real_row = store.transitions.apply, rooms._row_behind
    rooms.store.apply, rooms._row_behind = spy, lookup
    try:
        for item, kwid, what in (
                ({"id": HIS["id"]}, "0001", "a card for one row and a verb aimed at another"),
                ({"id": "4242"}, "4242", "a row that does not exist"),
                ({}, "", "an item with no id at all"),
                (None, HIS["id"], "no item at all")):
            try:
                rooms.dispatch("queue", "done", item=item, id=kwid, summary="x")
            except rooms.ItemRefusal:
                continue
            raise AssertionError(f"`done` was allowed with {what}")
        assert not spy.calls, f"a refused `done` still reached store.apply: {spy.calls}"
    finally:
        rooms.store.apply, rooms._row_behind = real, real_row


def test_queue_cannot_set():
    """`set` is score editing (must-not-build 2), and the rest are fleet-shaped verbs. All are
    registered, so their absence has to be checked rather than assumed.

    `note` LEFT THIS LIST ON 2026-08-31 AND THE REASON IS RECORDED RATHER THAN THE LINE DELETED.
    MUST-NOT-BUILD item 5 was overruled by the operator on 2026-08-30, for the voice note only, in
    his own words. What that item actually forbids is a box whose text goes nowhere, and the
    condition attached to the overrule keeps that true: there is no box, and the only body the verb
    can be reached with is a transcript. So the assertion did not weaken, it MOVED, to
    `test_queue_has_no_freeform_note_box` below, which checks the thing item 5 is about instead of
    the proxy that used to stand for it."""
    for verb in ("set", "claim", "cancel", "release", "stop", "start", "reap"):
        assert verb not in rooms.ROOM_VERBS["queue"], f"{verb} is reachable from the Queue room"


def test_the_voice_capture_verbs_are_reachable_from_no_room():
    """THE HALF OF THE OVERRULE THAT KEEPS IT ONE LINE WIDE.

    A voice note is six state changes: open, recorded, retained, transcribed, the landing, and on
    a bad day failed. Only the LANDING is reachable from a room. The five capture verbs run in the
    `voice` CLI as a subprocess, writing as itself, exactly as when the operator runs it in a
    terminal.

    This is not tidiness. A rendering surface that could call `voice open` could open a microphone
    from a crafted POST, and `web/rooms.py`'s opening argument is that a UI promise is one template
    edit, one stale cache, or one crafted POST away from being false."""
    reachable = set().union(*rooms.ROOM_VERBS.values())
    for verb in ("voice open", "voice recorded", "voice retained", "voice transcribed",
                 "voice failed"):
        assert verb not in reachable, \
            f"{verb} is reachable from a room, which would let a surface open a microphone"


def test_queue_has_no_freeform_note_box():
    """MUST-NOT-BUILD ITEM 5'S ACTUAL SENTENCE, CHECKED WHERE IT IS NOW POSSIBLE TO BREAK IT.

    *"There is no box whose text goes nowhere."* Until the overrule, that was guaranteed by `note`
    being off the allowlist: no verb, no box worth rendering. The verb is reachable now, so the
    guarantee has to be asserted directly, and this is the check that would fail the day somebody
    adds a textarea beside the Voice button because it seems helpful.

    TWO THINGS ARE ASSERTED AND THE SECOND IS THE LOAD-BEARING ONE:

      1. the voice control renders no text input of any kind;
      2. the ACTION does not accept typed text at all. `web/app.py`'s `voice_note` branch passes
         `seconds` and nothing else, and `actions.voice_note` has no `text` parameter. A template
         is one edit from growing a box; a function signature that cannot take the text is what
         makes the box pointless if somebody does.
    """
    import inspect
    from web import actions

    sig = inspect.signature(actions.voice_note)
    assert "text" not in sig.parameters, \
        "actions.voice_note grew a `text` parameter, which is the freeform note item 5 forbids"

    src = pathlib.Path(_R, "web/app.py").read_text(encoding="utf-8")
    branch = src[src.index('if action == "voice_note":'):]
    branch = branch[:branch.index("if action ==", 10)]
    assert "text=text" not in branch, \
        "the voice_note branch passes `text` through, which routes typed input at the `note` verb"

    macros = pathlib.Path(_R, "web/templates/macros.html").read_text(encoding="utf-8")
    form = macros[macros.index('class="act-form qvoice"'):]
    form = form[:form.index("</form>")]
    for forbidden in ("<textarea", "<input type=\"text\"", "<input type=text"):
        assert forbidden not in form, f"the voice control renders {forbidden}, which is a box"


def test_no_room_can_reach_the_dangerous_verbs():
    reachable = set().union(*rooms.ROOM_VERBS.values())
    for verb in ("reap", "set", "cancel", "budget stop", "budget set", "event ack",
                 "subscriber quarantine", "inbox mark-read", "run start", "run end",
                 "tick commit", "intake"):
        if verb in store.registered():
            assert verb not in reachable or verb == "intake", \
                f"{verb} is reachable from a room and should not be"


def test_unregistered_verbs_are_named_not_faked():
    """`recommend accept` is D6's. The console names the owner instead of standing in for it."""
    for verb in ("recommend accept", "recommend reject"):
        assert verb in rooms.ROOM_VERBS["queue"], f"{verb} should be on the Queue's list"
        if verb not in store.registered():
            try:
                rooms.dispatch("queue", verb, item={"id": "1"}, id="1", by="operator")
            except rooms.VerbNotBuiltYet as exc:
                assert "D6" in str(exc), "the refusal must name the lane that owes the verb"
                continue
            raise AssertionError(f"{verb} was dispatched despite being unregistered")


def _rendered_token(client, path):
    """The token a browser would actually have, scraped out of that page's HTML."""
    import re
    m = re.search(r'name="csrf" value="([^"]+)"',
                  client.get(path).get_data(as_text=True))
    return m.group(1) if m else None


def _minted(app, room):
    """A VALID token for `room`, a browser holding the cookie it is bound to, and its headers.

    Study renders no token because Study renders no write form, so the only way to test "a
    legitimate Study session at a door" is to mint one. It matters that these tests drive the door
    holding real credentials: a refusal that happens because the request looked malformed proves
    the CSRF layer works and proves nothing about the room separation underneath it.

    The client is FRESH on purpose. A client that has already browsed carries its own session
    cookie, and two session cookies on one request is a test measuring whichever one Werkzeug
    happened to prefer.
    """
    from web import guard
    with app.test_request_context("/"):
        token, sid = guard.token_for(room), guard.session_id()
    client = app.test_client()
    # `set_cookie`, not a Cookie header: the test client manages its own jar and a hand-written
    # header loses to it, which reads as a failing token check rather than as a test wiring bug.
    client.set_cookie(guard.SESSION_COOKIE, sid)
    return token, client, {"Origin": "http://localhost"}


def test_the_write_door_itself_refuses_study():
    """Through the real HTTP endpoint, not just the function under it.

    `rooms.dispatch` being correct is worth nothing if a route reaches `store.apply` around it,
    so this drives the one write door the way a crafted POST would -- and now also the way a
    legitimate Study page would, holding a real Study token, because a refusal that only happens
    because the request looked malformed is not a demonstration that Study calls zero verbs.
    """
    from web.app import create_app
    app = create_app()
    client = app.test_client()
    # Study renders no write form and therefore no token. That is the surface being honest; the
    # enforcement is below, driven with a token minted for Study anyway.
    assert _rendered_token(client, "/study") is None, \
        "the Study page rendered a CSRF token, which means it rendered a write form"
    tok, client, hdr = _minted(app, "study")
    spy = Spy()
    real = store.transitions.apply
    rooms.store.apply = spy
    try:
        actions = ["answer", "accept_default", "accept_work", "send_back", "mark_done",
                   "undo_done", "defer_question", "decline", "approve", "reject",
                   "brief_reopen", "amend"]
        for action in actions:
            r = client.post("/study/act", headers=hdr,
                            data={"csrf": tok, "action": action, "id": "0001",
                                  "text": "a reason long enough to pass the gate"})
            assert r.status_code in (400, 403), \
                f"study/{action} returned {r.status_code}, expected a refusal"
            assert r.get_json()["kind"] == "room-refusal", \
                f"study/{action} was refused as {r.get_json()['kind']}, not by the allowlist"
        # And every registered verb name, in case a future action maps straight through.
        for verb in sorted(store.registered()):
            r = client.post("/study/act", headers=hdr,
                            data={"csrf": tok, "action": verb, "id": "0001",
                                  "text": "a reason long enough to pass the gate"})
            assert r.status_code in (400, 403, 501), f"study/{verb} returned {r.status_code}"
    finally:
        rooms.store.apply = real
    assert not spy.calls, f"a POST at the Study door reached store.apply: {spy.calls}"


def test_the_room_comes_from_the_route_not_the_payload():
    """The 0119 reproduction: a request originating from Study, claiming to be the Queue.

    It POSTed `room=queue` with `Referer: /study` and got 200 and a `reopen` on a live task. The
    room is now a URL segment and the token is scoped to the page that issued it, so a Study
    session cannot present itself at the Queue's door whatever it puts in the body.
    """
    from web import guard
    from web.app import create_app
    app = create_app()
    study_tok, study_client, study_hdr = _minted(app, "study")
    queue_tok, client, queue_hdr = _minted(app, "queue")
    spy = Spy()
    real = store.transitions.apply
    rooms.store.apply = spy
    try:
        stranger = app.test_client()          # a browser holding no session of this console's
        cases = [
            ("a Study session's token at the Queue's door",
             study_client, "/queue/act", {"csrf": study_tok}, study_hdr),
            ("that same Study token, presented by the Queue's browser",
             client, "/queue/act", {"csrf": study_tok}, queue_hdr),
            ("no token at all",
             client, "/queue/act", {}, queue_hdr),
            ("a valid Queue token replayed from another browser",
             stranger, "/queue/act", {"csrf": queue_tok}, {"Origin": "http://localhost"}),
            ("a Queue token from another origin",
             client, "/queue/act", {"csrf": queue_tok},
             dict(queue_hdr, **{"Origin": "http://evil.example",
                                "Referer": "http://localhost/study"})),
            ("neither Origin nor Referer",
             client, "/queue/act", {"csrf": queue_tok},
             {k: v for k, v in queue_hdr.items() if k != "Origin"}),
            ("a room that does not exist",
             client, "/kitchen/act", {"csrf": queue_tok}, queue_hdr),
        ]
        for name, who, url, extra, hdr in cases:
            r = who.post(url, headers=hdr,
                         data={"action": "send_back", "id": "0001",
                               "text": "a reason long enough to pass the gate", **extra})
            assert r.status_code == 403, f"{name}: got {r.status_code}, expected 403"
            assert r.get_json()["kind"] in ("write-refusal", "room-refusal"), \
                f"{name}: refused as {r.get_json()['kind']}"
        # The old shape, verbatim: the room in the payload, posted at the old URL.
        r = client.post("/act", data={"room": "queue", "action": "send_back", "id": "0001",
                                      "text": "sent back from a request that came from Study"},
                        headers={"Referer": "http://localhost/study",
                                 "Origin": "http://evil.example"})
        assert r.status_code == 404, \
            f"the payload-room write door still exists and answered {r.status_code}"
        # And a stale page that still sends the field is caught rather than silently obeyed.
        r = client.post("/queue/act", headers=queue_hdr,
                        data={"room": "study", "csrf": queue_tok, "action": "send_back",
                              "id": "0001", "text": "a reason long enough to pass the gate"})
        assert r.status_code == 403 and r.get_json()["kind"] == "write-refusal", \
            "a contradicting room field in the payload was not refused"
    finally:
        rooms.store.apply = real
    assert not spy.calls, f"a cross-room POST reached store.apply: {spy.calls}"
    assert guard.WriteRefused is not None


def test_no_get_route_writes():
    """Every room and detail page is a GET, and a GET never changes state.

    Checked by walking the URL map: any rule that accepts POST outside the one write door is a
    second write door, and the fifth one written in a hurry is the one that forgets the allowlist.
    `/<room>/act` is ONE rule with one view function -- the room is a segment Flask parsed rather
    than a field the payload supplied -- and this assertion is what keeps it one.

    NARROWED 2026-08-29 BY THE COMMANDER, NOT BY THE LANE THAT MADE IT RED, and narrowed rather
    than widened. Row 0437's typable pane registers four POST routes, so `writable ==
    ["/<room>/act"]` became false the moment the pane existed. The tempting repair is to edit the
    expected list to name five doors, and that is indistinguishable at review time from widening a
    tolerance to turn a red green, which is why the lane that owned the terminal declined to touch
    this file and raised it instead.

    The property this test was written to protect is ONE WRITE DOOR FOR STATE. "No other route
    accepts a write METHOD" was a proxy for that, and it was true until the pane. So the claim is
    now stated directly: the STATE door is still exactly one, the exception is exactly the pane's
    four rules, and every one of them belongs to the pane's own blueprint.

    THE EXCEPTION IS NAMED RATHER THAN EXCUSED. MUST-NOT-BUILD item 11 was overruled on 2026-08-28
    for a pane that types into a process, on the stated condition that NO STATE WRITE CROSSES IT.
    That condition is not asserted here, because a URL map cannot see it. It is proved by
    enumeration in `web/tests/test_terminal_carries_no_write.py`: 69 registered verbs against 4
    endpoints in 8 fields, 2208 crafted POSTs holding a valid pane token against a live pty, 0 verb
    receipts and 0 entries into `store.apply`. This test asserts the half a URL map CAN see.

    Item 6's own incident is why the shape matters: the allowlist was airtight and the room it
    keyed on was read from `request.form`, so a POST from the Study page saying `room=queue` got
    the Queue's verb set and ran `reopen` on a live task (task 0147). An allowlist keyed on an
    attacker-supplied string is an allowlist keyed on nothing.

    A guard nobody has watched fail is not a guard, so this one is watched failing in
    `test_the_narrowed_write_door_still_fires`, immediately below, on both of its arms.
    """
    _assert_one_state_door()


def _assert_one_state_door(extra_rules=()):
    """The body of the check, factored out ONLY so the positive control can plant a route and
    watch it fail. `extra_rules` is a test seam and nothing in the console ever passes it."""
    from web.app import create_app
    app = create_app()
    for rule, methods in extra_rules:
        app.add_url_rule(rule, endpoint=f"planted.{rule}", view_func=lambda: "",
                         methods=list(methods))
    writable = [(r.rule, r.endpoint) for r in app.url_map.iter_rules()
                if {"POST", "PUT", "PATCH", "DELETE"} & r.methods]
    doors = [rule for rule, _ in writable if not rule.startswith("/terminal/")]
    assert doors == ["/<room>/act"], f"more than one STATE write door exists: {doors}"
    pane = [(rule, ep) for rule, ep in writable if rule.startswith("/terminal/")]
    assert {rule for rule, _ in pane} == {
        "/terminal/open", "/terminal/<key>/input",
        "/terminal/<key>/resize", "/terminal/<key>/close"}, \
        f"the pane's write rules are not the four that were enumerated against: {pane}"
    assert all(ep.startswith("terminal.") for _, ep in pane), \
        f"a /terminal/ write rule is not the pane's: {pane}"


def test_the_narrowed_write_door_still_fires():
    """THE POSITIVE CONTROL on the narrowing above, required by the commander's ruling D-11.

    A narrowed assertion that cannot fail is a widened one wearing a disguise. So this plants a
    write route the narrowed check does not know about and asserts it goes RED, on BOTH arms:
    a non-terminal write door, and a fifth `/terminal/` route that the pane's enumeration never
    covered. Two of two must raise; then the unplanted check must still pass, or the control has
    only proved that everything fails.
    """
    fired = 0
    for planted in [("/sneaky/act", ("POST",)), ("/terminal/<key>/exec", ("POST",))]:
        try:
            _assert_one_state_door(extra_rules=[planted])
        except AssertionError:
            fired += 1
        else:
            raise AssertionError(
                f"the narrowed write-door check did NOT fire on a planted route {planted[0]}. "
                "It is not a guard.")
    assert fired == 2, f"positive control: {fired} of 2 planted routes were caught"
    _assert_one_state_door()
    print(f"    positive control: {fired} of 2 planted write routes caught, "
          "and the unplanted surface still passes")


def test_audit_reports_the_whole_surface():
    a = rooms.audit()
    assert a["study_verb_count"] == 0
    assert set(a["rooms"]) == set(rooms.ROOMS)
    assert a["registered"], "no verbs registered; the audit would be vacuous"
    # Most of the runtime's verbs must be unreachable from the console, or "room separation"
    # would be a description of a surface that reaches everything.
    assert len(a["registered_and_unreachable"]) > len(a["reachable_from_any_room"]), \
        "more verbs are reachable from a room than are not; that is not separation"


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            check(name, fn)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
