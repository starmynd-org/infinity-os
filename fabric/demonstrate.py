"""The demonstration, run rather than asserted. `python3 -m fabric.demonstrate`

Every claim this lane makes is checked here against the live store, in one pass, with its output
printed. A grant nobody tested is a grant nobody has, and a lag metric nobody watched climb is a
number on a dashboard.

Seven things, in order:

1. Every verb registered, and the role each runs as.
2. The `subscriber` role's missing write path to `event`, proved from inside a running listener.
3. The 4096-byte ceiling REJECTING rather than truncating (EF-1).
4. Hard flags inheriting by OR, and a `gated` subscriber receiving a tombstone rather than the
   flagged payload (EF-7 plus the subscriber registry's mandatory posture for effect class).
5. **Lag climbing while the listener is stopped, and recovering when it restarts.**
6. Quarantine: what causes it, what it does to lag, and that only a human clears it.
7. The two-record path and the disposition that answers an event (EF-2, EF-3).
"""

from __future__ import annotations

import json
import sys

import store

from . import emit as _emit
from . import lag as _lag
from . import listener as _listener
from .producers import sessions as _sessions

SUB = "operator-paging"


def h(title: str):
    print(f"\n=== {title} " + "=" * max(0, 72 - len(title)))


def q(sql, params=None, role="runtime"):
    with store.read(role) as s:
        return s.query(sql, params)


def main() -> int:
    from subscribers.operator_paging.consumer import OperatorPaging
    from subscribers.operator_paging import memory, transport

    def listener(**kw):
        # A demonstration starts from no memory of prior pages. The default `PageMemory` reads
        # `~/.swarm/operator/pages.log`, which is the operator's real outbound record: reading it
        # here would let a real outstanding page decide what this run prints, and a demonstration
        # whose output depends on last night's questions demonstrates nothing repeatable.
        return OperatorPaging(transports=[transport.DryRunTransport()],
                              page_memory=memory.PageMemory(
                                  log_path="/nonexistent/demonstrate-has-no-page-history"),
                              **kw)

    failures = []

    # ---------------------------------------------------------------- 1
    h("1. every verb, and the role it runs as")
    print(json.dumps(store.transitions.registered(), indent=2))

    # ---------------------------------------------------------------- 2
    h("2. the subscriber CANNOT write to event, proved from inside a running listener")
    c = listener()
    gate = c.start()
    for k, v in gate.items():
        print(f"  {k:<7} {v}")
    if not all("42501" in v for v in gate.values()):
        failures.append("gate: not every write was refused by the GRANT (42501)")
    c.stop()

    # ---------------------------------------------------------------- 3
    h("3. EF-1: the 4096-byte ceiling REJECTS, it does not truncate")
    try:
        _emit.emit(type="question.raised", external=False, canon_touching=False,
                   payload_summary="x" * 4097, actor="demo")
        failures.append("ceiling: a 4097-byte payload was accepted")
        print("  FAIL: accepted")
    except _emit.PayloadTooLarge as exc:
        print(f"  refused: {exc}")
    try:
        _emit.emit(type="question.raised", external=None, canon_touching=False, actor="demo")
        failures.append("flags: an event with an unstated external flag was accepted")
        print("  FAIL: unflagged event accepted")
    except _emit.FlagOmitted as exc:
        print(f"  refused: {str(exc)[:120]}...")

    # ---------------------------------------------------------------- 4
    h("4. EF-7 flags inherit by OR, and a gated subscriber gets a TOMBSTONE")
    print(f"  inherit(False, False, (True, False)) -> {_emit.inherit(False, False, (True, False))}")
    print(f"  inherit(True, True, (False, False))  -> {_emit.inherit(True, True, (False, False))}"
          "   <- a derived item never LOWERS a flag")
    flagged = _emit.emit(
        type="question.raised", external=True, canon_touching=False,
        subject_type="question", subject_id="q-flagged",
        payload_summary=json.dumps({"qid": "q-flagged", "from": "T6",
                                    "question": "SECRET-PAYLOAD-MUST-NOT-REACH-THE-PAGER",
                                    "default_if_unanswered": ""}),
        actor="demo")
    print(f"  emitted flagged event {flagged['event_id']}")
    c = listener()
    c.start()
    batch = c.read_batch()
    tomb = [r for r in batch.rows if r["subject_id"] == "" and r["tombstone"]]
    raw = q("SELECT payload_summary FROM brain.event WHERE event_id = %s", (flagged["event_id"],))
    leaked = any("SECRET-PAYLOAD" in (r.get("payload_summary") or "") for r in batch.rows)
    print(f"  the row IS in the store with its payload: "
          f"{'SECRET-PAYLOAD' in raw[0]['payload_summary']}")
    print(f"  the listener received {len(tomb)} tombstone(s) and leaked payload: {leaked}")
    if leaked or not tomb:
        failures.append("gated posture: the flagged payload reached the effect-class subscriber")
    c.drain()
    print(f"  gated tombstones handled: {c.gated_total}, paged: {c.paged}")
    for t in c.transports:
        for msg in getattr(t, "sent", []):
            if "WITHHELD" in msg:
                print("  --- the page it produced ---")
                print("  " + msg.replace("\n", "\n  "))
    c.stop()

    # ---------------------------------------------------------------- 5
    h("5. THE LAG METRIC: stop the listener, watch lag climb, restart it, watch it recover")
    before = _lag.subscribers()
    print(f"  listener running, drained:      {json.dumps(before, default=str)}")
    print("  --- listener is now STOPPED (it is not running; nothing is consuming) ---")
    for i in range(6):
        # `actor_type` used to be the literal "ai" inside the producer; task 0300 moved the
        # decision to the caller, so this demo states it. A synthetic session with no operator
        # in it really is `ai`, unlike the hook-registered ones that are `hybrid`.
        _sessions.emit_session_started(f"demo-session-{i}", harness="demo", agent="T6",
                                      actor_type="ai", link_session=False)
    for i in range(6):
        _emit.emit(type="question.raised", external=False, canon_touching=False,
                   subject_type="question", subject_id=f"q-lag-{i}",
                   payload_summary=json.dumps({"qid": f"q-lag-{i}", "from": "T6",
                                               "question": f"lag demo {i}",
                                               "default_if_unanswered": "proceed"}),
                   actor="demo")
    climbing = _lag.subscribers()
    print(f"  12 events emitted, listener down: {json.dumps(climbing, default=str)}")
    hl = _lag.health()
    print(f"  health verdict while stuck:      {hl['verdict']}  (server is still 'up')")
    if climbing[0]["lag"] <= before[0]["lag"]:
        failures.append("lag: it did not climb while the listener was stopped")

    print("  --- listener RESTARTED ---")
    c = listener()
    c.start()
    n = c.drain()
    c.stop()
    after = _lag.subscribers()
    print(f"  drained {n} events, lag now:      {json.dumps(after, default=str)}")
    print(f"  health verdict after recovery:   {_lag.health()['verdict']}")
    if after[0]["lag"] != 0:
        failures.append(f"lag: did not recover to 0, it is {after[0]['lag']}")

    # ---------------------------------------------------------------- 6
    h("6. QUARANTINE: what causes it, what it does, and who may clear it")
    print("  DEFINITION")
    print("    quarantines:   3 consecutive handler failures on the SAME event_seq (a poison"
          " event), or an explicit `fabric quarantine`. The cursor STOPS advancing, so lag"
          " climbs and keeps climbing.")
    print("    un-quarantines: `fabric release --subscriber X --by <human>` ONLY. It runs as"
          " `runtime`; the RLS policy gives the subscriber role no write outside its own row"
          " and no path to clear this at all.")

    class Poisoned(OperatorPaging):
        def handle(self, row):
            raise RuntimeError("simulated poison event: the handler cannot process this row")

    _emit.emit(type="question.raised", external=False, canon_touching=False,
               subject_type="question", subject_id="q-poison",
               payload_summary=json.dumps({"qid": "q-poison", "from": "T6",
                                           "question": "poison", "default_if_unanswered": "x"}),
               actor="demo")
    p = Poisoned(transports=[transport.DryRunTransport()],
                 page_memory=memory.PageMemory(
                     log_path="/nonexistent/demonstrate-has-no-page-history"))
    p.start()
    for _ in range(3):
        try:
            p.drain()
        except _listener.StartupRefused as exc:
            print(f"  quarantined: {exc}")
            break
    p.stop()
    qrow = _lag.subscribers()
    print(f"  cursor after quarantine:         {json.dumps(qrow, default=str)}")
    if not qrow[0]["quarantined"]:
        failures.append("quarantine: the subscriber did not quarantine itself")

    print("  a quarantined cursor REFUSES to advance, even on a good ack:")
    r = _emit.ack(SUB, qrow[0]["head_seq"])
    print(f"    event ack -> {json.dumps(r, default=str)}")

    print("  and the listener REFUSES to start:")
    try:
        listener().start()
        failures.append("quarantine: a quarantined listener started anyway")
    except _listener.StartupRefused as exc:
        print(f"    {str(exc)[:200]}")

    print("  the WATCHER notices, and it is a producer, not a listener:")
    from . import watch as _watch
    found = _watch.check_once()
    print(f"    fabric.watch -> {json.dumps(found, default=str)}")
    if not found:
        failures.append("watch: the quarantine condition was not noticed")

    print("  release is a human's act, through the runtime role:")
    print(f"    {json.dumps(_emit.release(SUB, 'operator'), default=str)}")
    c = listener()
    c.start()
    c.drain()
    c.stop()
    print(f"  after release and drain:         {json.dumps(_lag.subscribers(), default=str)}")
    for t in c.transports:
        for msg in getattr(t, "sent", []):
            if "quarantined" in msg:
                print("  --- and the page the watcher's event produced ---")
                print("  " + msg.replace("\n", "\n  "))

    # ---------------------------------------------------------------- 7
    h("7. EF-2 two records, EF-3 disposition requires an event and not an observation")
    ev = q("SELECT event_seq FROM brain.event ORDER BY event_seq DESC LIMIT 1")[0]["event_seq"]
    oid = _emit.observe(ev, kind="demo", text="the standing observation this occurrence opened",
                        subject_type="question", subject_id="q-demo")
    print(f"  observation {oid} opened against event {ev}")
    did = _emit.dispose(ev, "prepared", rationale="disposed, never acted",
                        observation_id=oid, decided_by="T6")
    print(f"  disposition {did} with observation_id={oid}")
    did2 = _emit.dispose(ev, "prepared", rationale="no standing observation", decided_by="T6")
    print(f"  disposition {did2} with observation_id=None  <- EF-3: nullable")
    try:
        _emit.dispose(None, "prepared")
        failures.append("EF-3: a disposition with no event_id was accepted")
    except ValueError as exc:
        print(f"  refused a disposition with no event: {str(exc)[:90]}")

    h("EF-4: consequential events with no receipt booked yet")
    print(json.dumps(_lag.receipts_due(5), indent=2, default=str))

    h("RESULT")
    if failures:
        for f in failures:
            print(f"  FAILED: {f}")
        return 1
    print("  every check above passed against the live store.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
