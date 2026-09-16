#!/usr/bin/env python3
"""A page is delivered when it landed somewhere, not when the code reached the return. Task 0125.

The defect this file exists to stop reopening, measured on this box before the fix by calling the
shipped `FileTransport.send` with both of its targets made unwritable:

    {'transport': 'file', 'delivered': True, 'targets': [
        'FAILED /tmp/.../nodir/pages.log: [Errno 17] File exists',
        'FAILED /tmp/.../nodir/drop.txt: [Errno 17] File exists']}

`delivered` was the literal `True` on transport.py:84. It was never computed from what was
written, so a page that landed in neither file reported itself as sent. Three things then went
wrong at once, and they are the reason this is a channel defect and not a style point:

- `consumer.py`'s `if not delivered: raise` is the path that quarantines the pager. Its own
  comment says "a pager that cannot page has to stop and be seen to have stopped". A hardcoded
  `True` is exactly what stops that from ever firing.
- `memory.record_page` is documented "Called only after a transport reported it delivered", so a
  false `True` wrote a memory entry saying the operator was paged. That entry then suppresses
  re-paging and lets a later retraction cancel a page he never received.
- the `paged` log line put failures in `failed=` only for transports reporting `delivered: False`,
  so a file transport that lost one of its two targets was invisible there too.

Every test below asserts a PROPERTY of the channel rather than the shape of a function, because
the wrong version of this fix is easy to write and passes a wiring test. The wrong version is
`delivered = not anything_failed`: on the VPS the Windows drop is *expected* to be absent, so that
fix reports `delivered: False` on every normal page, the consumer raises, and the pager
quarantines itself permanently the first time it runs where it is meant to run. That converts a
silent drop into a total outage. The ext4 log is the record that must exist; the Windows drop is a
convenience. One target landing is a delivered page, zero targets landing is not, and tests 1-3
pin both edges of that contract so neither the old defect nor its naive fix can come back.

  1. both targets unwritable -> delivered is False, and nothing is claimed as a target
  2. only the Windows drop unwritable -> delivered is True AND the loss is visible to the caller
     and on the consumer's own log line (the VPS case, the one the naive fix breaks)
  3. both targets writable -> delivered is True, both paths in targets, no FAILED string anywhere
  4. a delivered: False page is never remembered, so no retraction can cancel a page nobody got

Run: python3 subscribers/tests/test_paging_delivery_truth.py

No database is needed by any test here. Unwritability is simulated by putting the target under a
parent that is a regular file, which makes `mkdir(parents=True)` raise `FileExistsError` (an
`OSError`) for root and non-root alike; a `chmod 0o500` would not stop a suite running as root and
would pass for the wrong reason.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("ENGINE_SCRATCH_DB", "brain_scratch"))

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


# ------------------------------------------------------------------ targets, writable or not

class Targets:
    """A temp dir holding a writable path and an unwritable one, plus the monkeypatch.

    `send` resolves `transport.LOCAL_LOG` and `transport.WINDOWS_DROP` at call time, which is the
    same seam `test_paging_retraction.py` uses. Pointing the transport at a directory it can never
    create is a real `OSError` from a real filesystem call: nothing here stubs `open`, so the test
    exercises the same except-branch the VPS will.
    """

    def __init__(self, local_ok=True, drop_ok=True):
        self.local_ok, self.drop_ok = local_ok, drop_ok

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        (d / "not-a-directory").write_text("a regular file where a parent dir would have to be\n")
        self.local = (d / "pages.log") if self.local_ok else (d / "not-a-directory" / "pages.log")
        self.drop = (d / "drop.txt") if self.drop_ok else (d / "not-a-directory" / "drop.txt")
        self._saved = transport.LOCAL_LOG, transport.WINDOWS_DROP
        transport.LOCAL_LOG, transport.WINDOWS_DROP = self.local, self.drop
        return self

    def __exit__(self, *exc):
        transport.LOCAL_LOG, transport.WINDOWS_DROP = self._saved
        self._tmp.cleanup()
        return False


def raised(seq, qid="q0125"):
    """A question with no stated default, so it is urgent and pages through any hour."""
    return {"event_seq": seq, "type": "question.raised", "occurred_at": "2026-08-18T02:00:00Z",
            "subject_id": qid, "tombstone": False, "external": False, "canon_touching": False,
            "payload_summary": json.dumps({"qid": qid, "task": "0125", "from": "T3",
                                           "question": "did the page land?",
                                           "default_if_unanswered": ""})}


def pager(log_path):
    lines = []
    c = consumer.OperatorPaging(transports=[transport.FileTransport()],
                                page_memory=memory.PageMemory(log_path=log_path),
                                log=lambda **kw: lines.append(kw))
    return c, lines


# ================================================================ the property the task is about

def test_a_page_that_landed_nowhere_is_not_a_delivered_page():
    """Both targets unwritable. This is the exact state the literal `True` reported as sent."""
    with Targets(local_ok=False, drop_ok=False) as t:
        r = transport.FileTransport().send("[question.raised] q0125 needs you")
    eq("delivered is False, because nothing was written", r["delivered"], False)
    eq("no file on this machine holds that page", (t.local.exists(), t.drop.exists()),
       (False, False))
    eq("and nothing is listed as a target that was not one", r["targets"], [])
    truth("no target entry is a failure string wearing a target's clothes",
          not any("FAILED" in x for x in r["targets"]), r["targets"])
    truth("the caller is told which targets were lost", len(r.get("failed_targets") or []) == 2,
          r.get("failed_targets"))
    err = r.get("error") or ""
    truth("and the error names both paths, so the log line is actionable",
          str(t.local) in err and str(t.drop) in err, err)


def test_the_windows_drop_is_allowed_to_be_absent_and_the_page_still_counts():
    """THE VPS CASE, and the one a naive `delivered = not any(failed)` breaks.

    There is no /mnt/c on a Linux server, so the Windows drop raises on every page there. If that
    made the page undelivered, `consumer.py` would raise on the pager's first normal page and
    quarantine it for behaving correctly. The ext4 log is the record; one target landing is a
    delivered page.
    """
    with Targets(local_ok=True, drop_ok=False) as t:
        r = transport.FileTransport().send("[question.raised] q0125 needs you")
        eq("delivered is True, because the record exists", r["delivered"], True)
        eq("the ext4 log is the target that landed", r["targets"], [str(t.local)])
        truth("and it really is on disk with the page in it",
              t.local.exists() and "q0125 needs you" in t.local.read_text(encoding="utf-8"))
        truth("the loss is NOT swallowed: the caller is told which target failed",
              any(str(t.drop) in x for x in (r.get("failed_targets") or [])),
              r.get("failed_targets"))
        truth("under a key of its own, never mixed into targets",
              not any("FAILED" in x or str(t.drop) in x for x in r["targets"]), r["targets"])

        # The property that matters more than the dict: the consumer neither quarantines nor
        # hides. A page that lost a lane must still page, and must still say it lost a lane.
        c, lines = pager(t.local)
        c.handle(raised(88))
        eq("the pager paged rather than raising", c.paged, 1)
        paged = [l for l in lines if l.get("event") == "paged"][-1]
        eq("its log line says the page went out via the file transport", paged["via"], "file")
        truth("and the same line names the lane it lost, which it could not do before",
              "file:" in paged["failed"] and str(t.drop) in paged["failed"], paged["failed"])


def test_both_targets_written_is_a_clean_delivery():
    """The control. A page that fully landed must report exactly that and carry no failure."""
    with Targets(local_ok=True, drop_ok=True) as t:
        r = transport.FileTransport().send("[question.raised] q0125 needs you")
        eq("delivered is True", r["delivered"], True)
        eq("both paths are targets", sorted(r["targets"]), sorted([str(t.local), str(t.drop)]))
        truth("both files exist and hold the page",
              all("q0125 needs you" in p.read_text(encoding="utf-8") for p in (t.local, t.drop)))
        truth("no FAILED string anywhere in the result", "FAILED" not in json.dumps(r), r)
        truth("and no failure keys at all on a clean send",
              "failed_targets" not in r and "error" not in r, sorted(r))

        c, lines = pager(t.local)
        c.handle(raised(88))
        paged = [l for l in lines if l.get("event") == "paged"][-1]
        eq("the consumer's log line reports nothing failed", paged["failed"], "-")


def test_a_page_that_did_not_land_is_never_remembered_as_sent():
    """`record_page` is "called only after a transport reported it delivered". Now that is true.

    This is the blast radius the dict shape only hints at: a remembered page suppresses re-paging
    and lets a later `question.answered` send "stand down, q0125 is answered" for a page the
    operator never received. The pager must instead stop and be seen to have stopped.
    """
    with Targets(local_ok=False, drop_ok=False) as t:
        c, lines = pager(t.local)
        try:
            c.handle(raised(88))
            bad("handle raises when no transport delivered", "it returned normally")
        except RuntimeError as exc:
            ok("handle raises, which is what eventually quarantines the pager")
            truth("and the raise carries the real reason, not None",
                  "targets failed" in str(exc), str(exc))

        eq("nothing is remembered as sent", len(c.memory._pages), 0)
        eq("so nothing is outstanding for a retraction to cancel",
           c.memory.outstanding("q0125"), None)
        paged = [l for l in lines if l.get("event") == "paged"][-1]
        eq("and the log line does not claim a route", paged["via"], "NONE")
        truth("it names the transport that failed", paged["failed"].startswith("file:"),
              paged["failed"])


def test_a_partly_lost_page_is_still_remembered_because_it_did_go_out():
    """The other half of the same rule: the VPS page IS remembered, so it can be retracted.

    Without this, "only remember what landed" could be satisfied by remembering nothing, and every
    page on the VPS would become unretractable: the 02:00 page stands on the lock screen forever
    while the 02:05 answer says there was nothing to take back.
    """
    with Targets(local_ok=True, drop_ok=False) as t:
        c, _ = pager(t.local)
        c.handle(raised(88))
        eq("the page is remembered", len(c.memory._pages), 1)
        truth("and it is outstanding, so an answer can cancel it",
              c.memory.outstanding("q0125") is not None)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print("test_paging_delivery_truth.py  --  delivered means it landed somewhere (0125)\n")
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
