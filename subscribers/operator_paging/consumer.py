"""`operator-paging`: the one consumer. An event that needs a human reaches him.

It is `Action class: effect`, and that is not a formality. `lane-2-subscriber-contract.md` D6 is
explicit that prepare is a closed set of four write targets and that an outbound message is not
one of them: *"Not an outbound message of any kind. No email, no Telegram, no Buzz post, no
webhook, no Slack ... a notification to a third-party service is a send."* A pager sends. So it is
effect-class, and `_system/subscriber-registry-rules.md` then makes **`Flagged-event posture:
gated` mandatory** for it.

That is the safety gate expressed as a mechanism rather than a sentence:

    An event carrying external: true or canon_touching: true may wake a listener to prepare,
    never to send, spend, deploy, publish, or touch canon.

This listener never sees a flagged event's contents at all. The blanking is a `CASE` in the
`SELECT` (see `fabric/listener.py:read_batch`), so the payload does not cross the process
boundary; what arrives is a tombstone carrying the sequence, the type, and the two flags. The
cursor still advances past it, which is why the gating is countable instead of invisible, and the
page it produces says a flagged thing is waiting and where to look. It cannot leak the contents
because it never had them.

**The `subscriber` role has no write path to `event`, and this process proves it at every start**
by trying, on a live connection with its own credentials, and refusing to run if the write lands.
"""

from __future__ import annotations

import os

from fabric import listener as _listener

from . import memory, policy, transport

SUBSCRIBER_ID = "operator-paging"


class OperatorPaging(_listener.Listener):
    """Reads what it declared, pages what policy allows, records everything either way.

    Since task 0162 it also takes a page back. `question.answered` is not machine volume and was
    never machine volume; it is the cancellation of a page, and while it sat on the never-page list
    the retraction promised by `fabric/producers/questions.py` and `fabric/types.py` existed only
    in those two docstrings. See `memory.PageMemory` for why the pager's memory of what it sent is
    the outbound log itself and not a new file.
    """

    def __init__(self, transports=None, page_memory=None, **kw):
        super().__init__(SUBSCRIBER_ID, **kw)
        self.transports = transports if transports is not None else transport.default_transports()
        self.memory = page_memory if page_memory is not None else memory.PageMemory()
        self.paged = 0
        self.held = 0
        self.never = 0
        self.retracted = 0

    def start(self) -> dict:
        """Prove the preconditions, then remember what the last run of this process sent.

        Rehydration is deliberately after `super().start()`: a listener that refused to run has
        nothing to retract, and reading the log before the gate check would be work done on
        authority that has not been established yet.
        """
        gate = super().start()
        self.log(event="memory_rehydrated", subscriber=self.subscriber, **self.memory.rehydrate())
        return gate

    def handle(self, row: dict) -> None:
        if row.get("type") in policy.RETRACTS:
            self._retract(row)
            return

        d = policy.decision(row)
        if not d["page"]:
            if d.get("held"):
                self.held += 1
            else:
                self.never += 1
            self.log(event="not_paged", event_seq=row["event_seq"], type=row["type"],
                     reason=d["reason"])
            return

        text = policy.render(row, d)
        results = [t.send(text) for t in self.transports]
        delivered = [r for r in results if r.get("delivered")]
        self.paged += 1
        self.log(event="paged", event_seq=row["event_seq"], type=row["type"],
                 tombstone=bool(row.get("tombstone")), urgent=d.get("urgent"),
                 reason=d["reason"],
                 via=",".join(r["transport"] for r in delivered) or "NONE",
                 failed=",".join(f"{r['transport']}:{r.get('error', '')}"
                                 for r in results
                                 if not r.get("delivered") or r.get("failed_targets")) or "-")
        if not delivered:
            # No transport landed. Raising is what eventually quarantines this subscriber, and
            # that is correct: a pager that cannot page has to stop and be seen to have stopped,
            # not keep acking events into a channel nobody is receiving.
            raise RuntimeError(
                f"no transport delivered event_seq {row['event_seq']}: "
                f"{[r.get('error') for r in results]}")
        # Only a page that actually went out is remembered, so a retraction can never cancel
        # something the operator was never told about. Questions only: nothing retracts a
        # quarantine notice, and remembering one would put it in the flagged-outstanding list.
        if row["type"].startswith("question."):
            self.memory.record_page(qid=policy.qid_of(row), event_seq=row["event_seq"])

    def _retract(self, row: dict) -> None:
        """Cancel the page this answer settles, if this pager sent one and still owes a correction.

        The lookup splits on the gate and nothing else. An unflagged answer carries its qid, so it
        cancels exactly one page. A flagged one does not -- `subject_id` is blanked in SQL before
        the row crosses this process boundary -- so all it can work with is the set of flagged
        pages still standing, and `policy.render_retraction` is explicit with the operator about
        the ambiguity rather than resolving it by guessing.
        """
        if row.get("tombstone"):
            prior = self.memory.outstanding_flagged()
        else:
            entry = self.memory.outstanding(policy.qid_of(row))
            prior = [entry] if entry else []

        d = policy.decision(row, prior=prior)
        if not d["page"]:
            self.never += 1
            self.log(event="not_paged", event_seq=row["event_seq"], type=row["type"],
                     reason=d["reason"])
            return

        text = policy.render_retraction(row, prior)
        results = [t.send(text) for t in self.transports]
        delivered = [r for r in results if r.get("delivered")]
        self.retracted += 1
        self.log(event="retracted", event_seq=row["event_seq"], type=row["type"],
                 tombstone=bool(row.get("tombstone")),
                 cancels=",".join(d.get("cancels") or []) or "NOTHING (ambiguous)",
                 reason=d["reason"],
                 via=",".join(r["transport"] for r in delivered) or "NONE",
                 failed=",".join(f"{r['transport']}:{r.get('error', '')}"
                                 for r in results
                                 if not r.get("delivered") or r.get("failed_targets")) or "-")
        if not delivered:
            raise RuntimeError(
                f"no transport delivered the retraction for event_seq {row['event_seq']}: "
                f"{[r.get('error') for r in results]}")
        # After delivery, never before. A retraction that failed to send must stay owed, so the
        # re-delivered row retries it; a retraction that landed must not be sent twice, and the
        # at-least-once drain WILL hand this row over again if the process dies before the ack.
        for key in d.get("cancels") or []:
            self.memory.mark_retracted(key)


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="the operator-paging subscriber")
    p.add_argument("--once", action="store_true",
                   help="drain and exit, instead of listening")
    p.add_argument("--dry-run", action="store_true",
                   help="render pages without sending any")
    p.add_argument("--tunnel", metavar="VPS",
                   help="open the ssh tunnel to the one authoritative Telegram driver first")
    a = p.parse_args()

    if a.tunnel:
        transport.open_tunnel(a.tunnel)
        import time
        for _ in range(20):
            if transport.relay_reachable():
                break
            time.sleep(0.5)

    ts = [transport.DryRunTransport()] if a.dry_run else None
    c = OperatorPaging(transports=ts)
    if a.once:
        c.start()
        try:
            n = c.drain()
        finally:
            c.stop()
        print(f"handled={n} paged={c.paged} retracted={c.retracted} held={c.held} "
              f"never_page={c.never} gated_tombstones={c.gated_total}")
        if a.dry_run:
            for t in c.transports:
                for msg in getattr(t, "sent", []):
                    print("---\n" + msg)
        return 0
    c.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
