#!/usr/bin/env python3
"""A page that stops being true is taken back. Task 0162.

The defect this file exists to stop reopening, measured on the scratch store before the fix:

    paged      event_seq=18  type=question.raised    urgent=True
    not_paged  event_seq=19  type=question.answered  reason=... never-page list (machine volume)
    handled=2 paged=1 held=0 never_page=1

`question.answered` was emitted on every surface from task 0140 and two docstrings said it existed
"so a pager can retract rather than re-page". The one consumer had it on `NEVER_PAGE`. So the
retraction was a claim in prose and no code, in exactly the case the paging channel exists for: a
question pages the operator at 02:00, he answers it at 02:05, and his lock screen still says the
fleet is waiting on him.

Every test below asserts a PROPERTY of the channel rather than the shape of a function, because
the wrong version of this fix is easy to write and passes a wiring test: a pager that forwards
every `question.answered` retracts nothing and doubles the volume on the one channel whose stated
failure mode is being muted for carrying traffic.

  1. an answer cancels the page that was sent for it
  2. an answer with no page outstanding sends NOTHING (the volume bound)
  3. a page that was held is not a page, so it is not cancelled
  4. a retraction is sent once, even though the drain is at-least-once
  5. a retraction is not held by quiet hours, because it cannot wake anyone the page did not
  6. the memory survives a restart, because it IS the outbound log
  7. the gate still holds: a flagged answer names what it cannot say

Run: python3 subscribers/tests/test_paging_retraction.py

Tests 1-9 need no database. Test 10 is the end-to-end and needs the scratch store plus
`store/bin/provision-subscriber.sh --db brain_scratch --subscriber operator-paging`; it says so and
skips rather than failing if the store is not there, so the properties above stay checkable on a
box with no Postgres.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

# ISOLATION, AND IT IS FAIL-CLOSED ON PURPOSE. Task 0147.
#
# This was `os.environ.setdefault("BRAIN_PG_DB", ...)`, and setdefault is a NO-OP when the name is
# already set. Every agent terminal in this fleet exports `BRAIN_PG_DB=brain`, so the whole
# end-to-end test below ran against the LIVE store. Measured 2026-08-18: seven `work_item` rows
# titled "a task that asks" landed on the live bus in six minutes, each carrying a question the
# test then ANSWERED under the identity `operator` -- six fabricated operator answers (q0130,
# q0132, q0134, q0136, q0138, q0143) plus q0140 left outstanding, and the live pager sent all
# seven to the operator's real page log. Only an answered `swarm ask` is operator consent, so a
# test that manufactures them attacks the one signal the fleet uses to tell consent from noise.
#
# ASSIGNMENT, not setdefault: an ambient value is exactly what must not win here.
os.environ["BRAIN_PG_DB"] = os.environ.get("ENGINE_SCRATCH_DB") or "brain_scratch"

# BOTH names, because they are read by different things and disagreeing is its own bug:
# `store` connects to BRAIN_PG_DB (store/session.py), while `engine/bin/scratch-db.sh` keys off
# ENGINE_SCRATCH_DB (line 14). Unbound, the truncate below reset `brain_scratch` while
# `store.apply` wrote `brain` -- which is why this suite's four end-to-end assertions failed with
# "wanted [1], got [0]" and were read as a scratch store the truncate does not reset (task 0141).
# They were not the same database.
os.environ["ENGINE_SCRATCH_DB"] = os.environ["BRAIN_PG_DB"]

# The guard, because the two lines above are a default and a default is only ever one edit or one
# explicit override away from being gone. This suite TRUNCATEs and it writes questions and answers;
# there is no value in letting it point at the live store, so it refuses rather than reports.
# scratch-db.sh carries the same refusal for the same reason (line 31).
if os.environ["BRAIN_PG_DB"] == "brain":
    sys.exit("test_paging_retraction.py: refusing to run against the live store 'brain'. "
             "This suite posts work items, asks questions and answers them as the operator, and "
             "truncates between tests. Unset ENGINE_SCRATCH_DB or name a scratch database.")

# The declaration is the fixture beside this file unless SUBSCRIBERS_DECLARATION names one --
# never BRAIN_ROOT, and never wherever the operator's private brain happens to sit (W5-S7,
# 2026-09-16). This suite tests the consumer, not a brain.
if not os.environ.get("SUBSCRIBERS_DECLARATION"):
    os.environ["SUBSCRIBERS_DECLARATION"] = str(Path(__file__).resolve().parent / "fixtures" / "SUBSCRIBERS.md")

from subscribers.operator_paging import consumer, memory, policy, transport   # noqa: E402

PASS, FAIL = 0, 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  ok    {msg}")


def bad(msg, detail=""):
    global FAIL
    FAIL += 1
    print(f"  FAIL  {msg}")
    if detail:
        print(f"        {detail}")


def eq(msg, got, want):
    ok(msg) if got == want else bad(msg, f"wanted [{want}], got [{got}]")


def truth(msg, cond, detail=""):
    ok(msg) if cond else bad(msg, detail)


# ---------------------------------------------------------------- rows and a pager, no database

def raised(seq, qid="q0041", default="", task="0140", tombstone=False):
    return {"event_seq": seq, "type": "question.raised", "occurred_at": "2026-08-16T02:00:00Z",
            "subject_id": "" if tombstone else qid, "tombstone": tombstone,
            "external": tombstone, "canon_touching": False,
            "payload_summary": "" if tombstone else json.dumps(
                {"qid": qid, "task": task, "from": "T6",
                 "question": "cut from main or the release branch?",
                 "default_if_unanswered": default})}


def answered(seq, qid="q0041", tombstone=False):
    return {"event_seq": seq, "type": "question.answered", "occurred_at": "2026-08-16T02:05:00Z",
            "subject_id": "" if tombstone else qid, "tombstone": tombstone,
            "external": tombstone, "canon_touching": False,
            "payload_summary": "" if tombstone else json.dumps(
                {"qid": qid, "task": "0140", "answered_at": "2026-08-16T02:05:00Z"})}


class Pager:
    """A real `OperatorPaging` with a dry-run transport and a memory on a throwaway log.

    Constructed rather than mocked: `handle` is the unit under test and it is where the never-page
    list did its damage, so a test that reimplemented the dispatch would have passed before the fix.
    `Listener.__init__` reads the declaration from git and opens no connection, so this needs no
    database.
    """

    def __init__(self, log_path=None, transports=None):
        self.lines = []
        self.c = consumer.OperatorPaging(
            transports=transports if transports is not None else [transport.DryRunTransport()],
            page_memory=memory.PageMemory(log_path=log_path or Path(tempfile.mkdtemp()) / "p.log"),
            log=lambda **kw: self.lines.append(kw))

    def handle(self, row):
        self.c.handle(row)
        return self

    @property
    def sent(self):
        return [m for t in self.c.transports for m in getattr(t, "sent", [])]

    def last(self, event):
        got = [l for l in self.lines if l.get("event") == event]
        return got[-1] if got else None


# ================================================================ the property the task is about

def test_an_answer_cancels_the_page_that_was_sent_for_it():
    """The 02:00 page, the 02:05 answer, and the lock screen that used to keep lying."""
    p = Pager().handle(raised(88)).handle(answered(89))
    eq("the question paged", p.c.paged, 1)
    eq("and the answer retracted, instead of landing on the never-page list", p.c.retracted, 1)
    eq("two messages went out: the page and its cancellation", len(p.sent), 2)
    if len(p.sent) != 2:
        return
    msg = p.sent[1]
    truth("the retraction names the question, so it can be matched to the page on the lock screen",
          msg.startswith("[question.answered] q0041 is answered."), msg)
    truth("and says the page it cancels was sent, not merely that something changed",
          "cancels the page sent" in msg, msg)
    truth("the decision log says what it cancelled",
          (p.last("retracted") or {}).get("cancels") == "q0041", p.last("retracted"))


def test_an_answer_with_no_page_outstanding_sends_nothing():
    """The volume bound, and the whole reason this is safe to take off the never-page list.

    Most `question.answered` events will hit this path: the operator answers from the console
    inside working hours, having never been paged at all. If those spoke, the pager would have
    doubled its own volume on the one channel whose named failure is being muted for traffic.
    """
    p = Pager().handle(answered(89))
    eq("nothing was sent", len(p.sent), 0)
    eq("and it is counted as a non-page, not as a retraction", p.c.retracted, 0)
    reason = (p.last("not_paged") or {}).get("reason", "")
    truth("the log says why, in terms of this pager's own history and not of the event type",
          "no page outstanding" in reason, reason)


def test_a_page_that_was_held_is_not_cancelled():
    """Quiet hours drop a non-urgent page. A page that never went out has nothing to take back."""
    old = policy.QUIET_START_HOUR, policy.QUIET_END_HOUR
    policy.QUIET_START_HOUR, policy.QUIET_END_HOUR = 0, 24      # every hour is a quiet hour
    try:
        p = Pager().handle(raised(88, default="cut from main")).handle(answered(89))
        eq("the question was held, not paged", (p.c.held, p.c.paged), (1, 0))
        eq("so nothing was sent at all", len(p.sent), 0)
        eq("and the answer retracted nothing", p.c.retracted, 0)
    finally:
        policy.QUIET_START_HOUR, policy.QUIET_END_HOUR = old


def test_a_retraction_is_sent_once_under_an_at_least_once_drain():
    """`drain` acks per row and re-delivers one row after a crash. Two answers, one cancellation.

    Without this the operator gets "q0041 is answered, stand down" twice for one decision, which is
    the same noise the retraction was built to remove.
    """
    p = Pager().handle(raised(88)).handle(answered(89)).handle(answered(89))
    eq("still exactly one retraction", p.c.retracted, 1)
    eq("and exactly two messages in total", len(p.sent), 2)
    reason = (p.last("not_paged") or {}).get("reason", "")
    truth("the second one says the page was already taken back", "already taken it back" in reason,
          reason)


def test_a_retraction_is_not_held_by_quiet_hours():
    """It cannot wake anyone the page did not already wake, and holding it leaves the lie up."""
    old = policy.QUIET_START_HOUR, policy.QUIET_END_HOUR
    policy.QUIET_START_HOUR, policy.QUIET_END_HOUR = 0, 24
    try:
        # No stated default, so the question is urgent and pages THROUGH quiet hours. That is the
        # only way a page exists at 02:00 for a retraction to cancel at 02:05.
        p = Pager().handle(raised(88, default="")).handle(answered(89))
        eq("the urgent question paged through quiet hours", p.c.paged, 1)
        eq("and the retraction went out in the same quiet hours", p.c.retracted, 1)
        eq("nothing was held", p.c.held, 0)
    finally:
        policy.QUIET_START_HOUR, policy.QUIET_END_HOUR = old


def test_the_never_page_list_still_holds_for_machine_volume():
    """A regression guard on the rule this change edits. Sessions must still be silent."""
    p = Pager()
    for t in ("session.started", "session.ended"):
        d = policy.decision({"type": t, "event_seq": 1})
        eq(f"{t} still never pages", d["page"], False)
    truth("and question.answered is no longer on that list, because it is not machine volume",
          "question.answered" not in policy.NEVER_PAGE, sorted(policy.NEVER_PAGE))
    truth("it is a retraction type instead", "question.answered" in policy.RETRACTS)
    eq("nothing above sent anything", len(p.sent), 0)


# ================================================================ the memory is the outbound log

def test_the_memory_survives_a_restart_because_it_is_the_log():
    """The pager has no supervisor unit. A restart between the page and the answer is normal.

    This is also the test that pins the file format as a contract: `render` writes the first line
    and `memory` reads it, so a change to either without the other silently stops retraction.
    """
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "pages.log"
        drop = Path(d) / "drop.txt"
        old_log, old_drop = transport.LOCAL_LOG, transport.WINDOWS_DROP
        transport.LOCAL_LOG, transport.WINDOWS_DROP = log, drop
        try:
            first = Pager(log_path=log, transports=[transport.FileTransport()])
            first.handle(raised(88))
            eq("the page is on disk", log.exists(), True)

            second = Pager(log_path=log, transports=[transport.FileTransport()])
            evidence = second.c.memory.rehydrate()          # what start() does, without a database
            eq("a fresh process reads one page back out of the log", evidence["pages"], 1)
            eq("and it is outstanding", evidence["outstanding"], 1)

            second.handle(answered(89))
            eq("so the restarted process can still cancel it", second.c.retracted, 1)

            third = Pager(log_path=log, transports=[transport.FileTransport()])
            third.c.memory.rehydrate()
            third.handle(answered(89))
            eq("and a THIRD process does not re-send it, because the retraction is in the log too",
               third.c.retracted, 0)
        finally:
            transport.LOCAL_LOG, transport.WINDOWS_DROP = old_log, old_drop


def test_an_unreadable_log_costs_a_retraction_and_never_a_page():
    """Failing closed here would mean a pager that will not start. That is the worse failure."""
    p = Pager(log_path=Path("/nonexistent-dir-0162/pages.log"))
    evidence = p.c.memory.rehydrate()
    eq("no memory is recovered", evidence["pages"], 0)
    p.handle(raised(88)).handle(answered(89))
    eq("and the pager still pages", p.c.paged, 1)
    eq("and can still cancel what it sent in THIS process", p.c.retracted, 1)


# ================================================================ the gate, which withholds the id

def test_a_flagged_answer_cancels_the_one_flagged_page_outstanding():
    """`subject_id` is blanked in SQL for a flagged event, so the retraction is by sequence.

    Unambiguous by elimination rather than by lookup: a tombstone is always urgent, so a flagged
    question is never held, so one outstanding flagged page and one flagged answer is one pair.
    """
    p = Pager().handle(raised(88, tombstone=True)).handle(answered(89, tombstone=True))
    eq("the flagged question paged as a tombstone", p.c.paged, 1)
    eq("and the flagged answer retracted it", p.c.retracted, 1)
    if len(p.sent) != 2:
        return bad("two messages went out", f"got {len(p.sent)}")
    msg = p.sent[1]
    truth("the retraction names the page's event, which is all the gate leaves it",
          msg.startswith("[question.answered] event 88 is answered."), msg)
    truth("and says why it cannot name the question", "gated from its contents" in msg, msg)
    truth("it never prints the qid, which this process never received",
          "q0041" not in msg, msg)


def test_two_flagged_pages_outstanding_cancels_nothing_and_says_so():
    """The honest limit. A wrong 'this one is settled' is worse than the stale page it replaces."""
    p = (Pager().handle(raised(88, tombstone=True)).handle(raised(91, tombstone=True))
         .handle(answered(92, tombstone=True)))
    eq("both flagged questions paged", p.c.paged, 2)
    eq("the answer still speaks, because two stale pages are worse than one ambiguous one",
       p.c.retracted, 1)
    msg = p.sent[-1]
    truth("and it refuses to say which", "Cannot say which" in msg, msg)
    truth("naming both outstanding pages", "event 88" in msg and "event 91" in msg, msg)
    eq("nothing is marked cancelled, so no page is falsely claimed settled",
       (p.last("retracted") or {}).get("cancels"), "NOTHING (ambiguous)")
    eq("both are still outstanding afterwards", len(p.c.memory.outstanding_flagged()), 2)


# ================================================================ end to end, through the store

def test_end_to_end_against_the_scratch_store():
    """`store.apply('answer', ...)` -> event -> the pager -> a retraction line in pages.log.

    The unit tests above hand `handle` a row this file built. This one builds nothing: the row
    comes off `brain.event`, through the gated SELECT, into the real consumer, out through the real
    FileTransport. It is the only test here that would have caught the original defect end to end.
    """
    try:
        import store
        from swarm_engine import transitions                        # noqa: F401  registers verbs
        with store.read("runtime") as s:
            s.scalar("SELECT 1")
    except Exception as exc:                                        # noqa: BLE001
        print(f"  SKIP  no scratch store reachable ({type(exc).__name__}). "
              f"Bring it up with engine/bin/scratch-db.sh migrate to run this one.")
        return

    os.environ["BRAIN_AFTER_COMMIT_HOOKS"] = "1"
    os.environ.pop("SWARM_PARENT_TASK", None)
    subprocess.run([str(ROOT / "engine/bin/scratch-db.sh"), "truncate"],
                   check=True, capture_output=True)

    with tempfile.TemporaryDirectory() as d:
        log, drop = Path(d) / "pages.log", Path(d) / "drop.txt"
        old_log, old_drop = transport.LOCAL_LOG, transport.WINDOWS_DROP
        old_quiet = policy.QUIET_START_HOUR, policy.QUIET_END_HOUR
        transport.LOCAL_LOG, transport.WINDOWS_DROP = log, drop
        # Pinned so the result does not depend on the wall clock of the box running the suite.
        # The question below states no default, so it is urgent and pages either way; pinning
        # removes the doubt rather than the behaviour.
        policy.QUIET_START_HOUR, policy.QUIET_END_HOUR = 3, 4
        try:
            tid = store.apply("post", lane="fabric", title="a task that asks",
                              posted_by="commander")["id"]
            q = store.apply("ask", question="cut from main or from the release branch?",
                            agent="T2", task=tid, default="")
            c = consumer.OperatorPaging(transports=[transport.FileTransport()],
                                        page_memory=memory.PageMemory(log_path=log),
                                        log=lambda **kw: None)
            c.start()
            try:
                c.drain()
                eq("the raised question paged", c.paged, 1)
                store.apply("answer", qid=q["id"], text="cut from main")
                c.drain()
            finally:
                c.stop()
            eq("and the answer retracted it", c.retracted, 1)
            eq("nothing landed on the never-page list", c.never, 0)
            text = log.read_text(encoding="utf-8")
            truth(f"the page for {q['id']} is in pages.log",
                  f"[question.raised] {q['id']}" in text, text[-400:])
            truth("and so is its retraction, which is the record AND the memory",
                  f"[question.answered] {q['id']} is answered." in text, text[-400:])
        finally:
            transport.LOCAL_LOG, transport.WINDOWS_DROP = old_log, old_drop
            policy.QUIET_START_HOUR, policy.QUIET_END_HOUR = old_quiet


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print("test_paging_retraction.py  --  a page that stops being true is taken back (0162)\n")
    for t in tests:
        print(f"{t.__name__.replace('test_', '')}: "
              f"{t.__doc__.splitlines()[0] if t.__doc__ else ''}")
        try:
            t()
        except Exception:                                           # noqa: BLE001
            import traceback
            bad(f"{t.__name__} raised", traceback.format_exc().splitlines()[-1])
        print()
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
