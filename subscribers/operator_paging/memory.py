"""What this pager sent, so it can take it back. Task 0162.

`question.answered` was emitted on every surface and nothing consumed it. `policy.NEVER_PAGE`
carried it, so the one event that can cancel a page was the one event the pager threw away.
Measured on the scratch store on 2026-08-16, seeding one `ask` with no stated default and one
`answer`:

    paged      event_seq=18  type=question.raised    urgent=True
    not_paged  event_seq=19  type=question.answered  reason=... never-page list (machine volume)
    handled=2 paged=1 held=0 never_page=1

The gap that leaves is the case the paging channel exists for. A question pages the operator's
phone at 02:00, he answers it from the console at 02:05, and the page on his lock screen still
says the fleet is waiting on him, so he re-reads a decision he already made.

Retraction needs one thing the pager did not have: memory of what it sent for a given question.

## This module writes nothing, and that is the design

`departments/SUBSCRIBERS.md` enumerates what this subscriber writes -- one line per page to
`~/.swarm/operator/pages.log` and the Windows drop, one enqueue to the bridge, and exactly one
row, its own cursor. `_system/subscriber-registry-rules.md` makes that enumeration the contract
rather than a description of it. A new ledger file would be a write path the declaration does not
name, and a subscriber that writes where it did not declare is the same defect as a subscriber
that consumes what it did not declare.

So the memory **is** the record. `pages.log` is already a durable, append-only line per page, and
"what did I send" is literally what it holds. Two consequences, both load-bearing:

- The memory and the record cannot disagree, because there is only one file and one writer.
- A retraction is itself a page, so it lands in the same log and this module reads it back as the
  fact that the question is already retracted. Restart-safety and de-duplication come free rather
  than needing a second state file to stay in step with the first.

Inside a running process the dictionary below is authoritative and the file is not read again. The
scan exists for the restart, which is not hypothetical: this subscriber's declaration says its
supervisor unit is *(none yet)*.

## Where it stops, stated rather than discovered later

**A page whose log write failed is not remembered, and that is now enforced rather than hoped
for.** `transport.FileTransport.send` used to return `delivered: True` even when both of its
targets raised, so a page that landed nowhere was recorded here as sent and a later retraction
cancelled something the operator never received. Task 0125 made `delivered` mean "at least one
target was actually written", so zero targets now reports `delivered: False`, `consumer.py` never
reaches `record_page`, and the raise there quarantines the pager instead. One target written is
still a delivered page: the Windows drop is legitimately absent on a host with no Windows, and the
ext4 log this module reads back is the one that must exist.

**A page older than the horizon cannot be remembered.** The scan reads the tail of the log, not all
of it, so a pager restarting after tens of thousands of pages forgets the oldest. Forgetting reads
as "nothing outstanding to retract", which sends nothing -- the failure direction that costs a
missing retraction rather than a false one.

**A question's own text is inside the log the scan reads.** A question whose body contained a line
shaped like `<ISO8601>Z␠␠[question.raised] q0007` would be read back as a page that was never sent,
and the cost is one spurious "q0007 is answered, stand down" for a question the operator was not
paged about. It is bounded, it destroys nothing, and it is the reason the anchor is the transport's
own timestamp prefix and two spaces rather than the bracket alone. Nothing here parses a payload,
so nothing here can be made to page or to stay silent by the contents of a question.

**A flagged page cannot be identified.** For a flagged event the gate blanks `subject_id` in SQL
(`fabric/listener.py:read_batch`), so a gated pager never learns which question a flagged
`question.answered` settles. Those pages are keyed by their event sequence instead, and
`policy.render_retraction` is explicit with the operator about what it cannot say. Looking the qid
up in `brain.question` would close the gap and would also be exactly the "gated only by its own
good behaviour" failure that `fabric/listener.py` refuses to be, so it is not available.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from . import policy, transport

#: How much of the tail of `pages.log` a restart reads back. A page record is a few hundred bytes,
#: so this is thousands of pages, and a pager that sends thousands of pages has a louder problem
#: than a forgotten retraction.
HORIZON_BYTES = 512 * 1024

#: A page older than this is dropped from memory. It matches the machine-volume retention the
#: policy docstring cites, and it is what stops an unanswered flagged page from being listed as
#: outstanding forever.
MAX_AGE_DAYS = 14

#: The first line of every page record, which is the only line that carries the transport's
#: timestamp prefix. A `policy.RETRACTS` type (`question.answered`, `question.withdrawn`) is a
#: retraction that was sent; any other `question.` type is a page that was sent. Both directions
#: in one pattern, because they are written by one renderer and a second pattern is a second
#: thing to keep in step.
#:
#: Deliberately any `question.` type rather than `question.raised` alone: `consumer.handle` records
#: every question page it sends, so a type added later would be remembered in the running process
#: and then forgotten by a restart if this pattern named only the type that exists today. That
#: asymmetry is the kind that is found months later by a retraction that works until the pager is
#: restarted.
#:
#: Either an id (`q0041`) or a sequence (`event 88`) follows the type. The sequence form is what a
#: flagged page renders, since the gate withheld the id. It is also, harmlessly, what an unflagged
#: question with no id at all would render; such an event carries nothing to retract by id either,
#: so it lands in the same unidentified bucket and is treated the same way.
_PAGE_LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) {2}"
    r"\[(question\.[a-z]+)\]\s+(?:(q\d{3,})|event (\d+))\b"
)

_STAMP = "%Y-%m-%dT%H:%M:%SZ"


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def key_for(qid: str = "", event_seq=None) -> str:
    """One key space. An identified page is its qid; a gated one is the sequence it arrived on."""
    return qid if qid else f"event:{event_seq}"


class PageMemory:
    """Pages this subscriber sent and has not yet cancelled.

    Not a cache of the store. It answers exactly one question -- "did I page this, and have I
    already taken it back?" -- and it answers it from the pager's own outbound record, which is
    the only place that fact has ever existed.
    """

    def __init__(self, log_path=None, horizon_bytes: int = HORIZON_BYTES,
                 max_age_days: int = MAX_AGE_DAYS):
        # Resolved at construction rather than import: `transport.LOCAL_LOG` is read from
        # SWARM_HOME at import time, and a test that sets SWARM_HOME afterwards would otherwise be
        # measuring the operator's real log.
        self.log_path = Path(log_path) if log_path else transport.LOCAL_LOG
        self.horizon_bytes = horizon_bytes
        self.max_age_days = max_age_days
        self._pages: dict[str, dict] = {}

    # ------------------------------------------------------------------ reading it back

    def _tail(self) -> list[str]:
        """The last `horizon_bytes` of the log, whole lines only.

        A seek into the middle of the file lands mid-line and mid-UTF-8. The first line is dropped
        for the first reason, and errors are replaced for the second, because a pager that refused
        to start over one truncated multibyte character would be trading a missing retraction for
        no pages at all.
        """
        try:
            size = self.log_path.stat().st_size
            with self.log_path.open("rb") as fh:
                if size > self.horizon_bytes:
                    fh.seek(size - self.horizon_bytes)
                raw = fh.read()
        except OSError:
            return []
        lines = raw.decode("utf-8", "replace").splitlines()
        return lines[1:] if size > self.horizon_bytes and lines else lines

    def rehydrate(self) -> dict:
        """Rebuild from the log. Returns evidence, so a restart can say what it recovered.

        Replay order is the file's order, which is send order, so a retraction always lands after
        the page it cancels and the last word about a key wins.
        """
        self._pages.clear()
        matched = 0
        for line in self._tail():
            m = _PAGE_LINE.match(line)
            if not m:
                continue
            matched += 1
            stamp, type_, qid, seq = m.group(1), m.group(2), m.group(3), m.group(4)
            key = key_for(qid or "", seq)
            # `policy.RETRACTS` and not a literal, so the two halves cannot drift: a retraction
            # type added to the policy (`question.withdrawn`, task 0159) must be replayed as a
            # cancellation here too, or a restart resurrects a page the pager had already taken
            # back and the operator is asked a second time about something already settled.
            if type_ in policy.RETRACTS:
                entry = self._pages.get(key)
                if entry:
                    entry["retracted"] = True
            else:
                self._pages[key] = {"key": key, "qid": qid or "", "event_seq": seq,
                                    "sent_at": stamp, "retracted": False}
        self._prune()
        return {"log": str(self.log_path), "lines_matched": matched,
                "pages": len(self._pages), "outstanding": len(self.outstanding_all())}

    def _prune(self) -> None:
        cutoff = _now() - _dt.timedelta(days=self.max_age_days)
        for key, entry in list(self._pages.items()):
            try:
                sent = _dt.datetime.strptime(entry["sent_at"], _STAMP).replace(
                    tzinfo=_dt.timezone.utc)
            except (ValueError, TypeError):
                continue          # an unparseable stamp is kept: forgetting is the lossy direction
            if sent < cutoff:
                del self._pages[key]

    # ------------------------------------------------------------------ the running process

    def record_page(self, *, qid: str, event_seq, sent_at: str = "") -> dict:
        """A page just went out. Called only after a transport reported it delivered."""
        key = key_for(qid or "", event_seq)
        entry = {"key": key, "qid": qid or "", "event_seq": str(event_seq),
                 "sent_at": sent_at or _now().strftime(_STAMP), "retracted": False}
        self._pages[key] = entry
        return entry

    def mark_retracted(self, key: str) -> None:
        entry = self._pages.get(key)
        if entry:
            entry["retracted"] = True

    # ------------------------------------------------------------------ what is still standing

    def outstanding(self, qid: str) -> dict | None:
        """The page for this question, if one was sent and has not been cancelled."""
        entry = self._pages.get(key_for(qid or "", None))
        return entry if entry and not entry["retracted"] and entry["qid"] else None

    def outstanding_flagged(self) -> list[dict]:
        """Every gated page still standing, oldest first.

        Oldest first because it is the order the operator saw them in, and because a message that
        lists them has to list them in some order that is not arbitrary.
        """
        return sorted((e for e in self._pages.values()
                       if not e["retracted"] and not e["qid"]),
                      key=lambda e: (e["sent_at"], int(e["event_seq"] or 0)))

    def outstanding_all(self) -> list[dict]:
        return [e for e in self._pages.values() if not e["retracted"]]
