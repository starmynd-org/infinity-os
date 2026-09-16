"""Producer 4: fleet lifecycle. Task 0273, 2026-08-19.

WHY THIS PRODUCER EXISTS. `swarm stop` wrote `brain.agent.stopped_at` and emitted nothing. On the
board that made a commander's deliberate stop and an agent that had silently died the same two
facts: a STOPPED flag and no explanation. At 00:55Z on 2026-08-19 the commander stopped T4 and T5
on a rate-limit decision; at 00:56Z an admiral pass, seeing two terminals stopped with no trail of
any kind, recorded "something else stopped two terminals tonight with no trail, and that is
worse", and at 00:57Z it restarted both -- correctly, on the information it had. Fleet lifecycle
was the only part of this system with no audit trail.

WHAT THIS MODULE IS NOT: it is not the fix. The fix is in the transition, where `stop` files the
record on `brain.message` INSIDE the same transaction as the column write, because
`brain.feed` -- what `swarm feed` reads, and the surface a human or an admiral pass actually
looks at -- is a UNION of `thread`, `message` and `question` and does NOT include `brain.event`.
An audit trail that lived only in `brain.event` would be a trail in the one place the incident
proves nobody was looking.

So this module is the SECOND copy, for the fabric's subscribers, and it is deliberately the one
that is allowed to fail. `after_commit` cannot fail the verb (`store/transitions.py:after_commit`
rule 2), and that property is wrong for the load-bearing record and exactly right for this one:
a producer role that is unreachable must not turn a landed stop into an error the operator
retries. If this hook never fires, `swarm status` and `swarm feed` still carry the whole trail.

WHY `after_commit` AND NOT A LINE IN `cmd_stop`. Every surface goes through `store.apply` -- the
CLI, the MCP server, the console's action allowlist. Wiring the emit into the CLI would have
published the CLI's stops and silently not the console's, which is the same seam one layer up.

THE 4096-BYTE CEILING is enforced by REFUSAL, not truncation, so a summary that would not fit
raises rather than quietly losing the tail. A stop reason is operator prose with no bound.
`_summary` bounds the reason inside a structured payload and says in the payload itself that it
did; `payload_ref` points at `brain.message:<seq>`, where the reason is stored whole. Nothing in
this lane cuts a record.
"""

from __future__ import annotations

import sys as _sys

import store
from store import transitions as _transitions

from .. import emit as _emit

#: How much of a reason rides in the summary. Far under the 4096-byte ceiling on purpose: the
#: ceiling is the hard floor of correctness and this is the readable size. The untruncated text
#: is behind `payload_ref` either way, so this number can only cost legibility, never a record.
_REASON_IN_SUMMARY = 600


#: Set once, the first time a store turns out to be unable to carry a fabric event at all.
_ANNOUNCED_ABSENT = False


def _can_emit() -> bool:
    """Can THIS store carry a fabric event? Checked, not assumed, and checked on the store.

    `docs/SCHEMA-TOLERANCE.md` rule 1: code that reads a schema element tolerates that element
    being absent unless every store it can open is proven to have it. `fabric/emit.py` writes
    `brain.event.produced_by_producer`, which migration **17** added, and this host carries 130
    databases of which most are below the tip. Measured on `brain_t1_0273_low6`, a real store
    built at ledger 6: every one of the four hooks below raised
    `UndefinedColumn: column "produced_by_producer" of relation "event" does not exist`, and
    `_run_after_commit` printed four lines of SQL error per verb.

    That noise is the thing rule 5 forbids: `swarm stop` already served on such a store, quietly,
    and a change of this task's may not make a path that served start shouting. So the
    precondition is READ instead of discovered by exception, and a store that cannot carry the
    event is announced ONCE per process and then skipped in silence.

    Skipping costs nothing that matters. The load-bearing trail is the `brain.message` record
    `stop` writes inside its own transaction, it is complete at ledger 1, and `swarm status` and
    `swarm feed` both read it. This module is the redundant copy, by design and by its docstring.

    NOT `except Exception: pass`. This asks the catalog a specific question and answers it with a
    fact; anything else the store raises is still a real failure and still reaches the hook
    runner's report.
    """
    global _ANNOUNCED_ABSENT
    with store.read("runtime") as s:
        ok = bool(s.one("SELECT 1 FROM information_schema.columns "
                        " WHERE table_schema = 'brain' AND table_name = 'event' "
                        "   AND column_name = 'produced_by_producer'"))
    if not ok and not _ANNOUNCED_ABSENT:
        _ANNOUNCED_ABSENT = True
        print("fabric: this store's brain.event predates migration 17 (no produced_by_producer), "
              "so no fleet.* event can be written to it. The stop/start trail itself is "
              "unaffected -- it is on brain.message and `swarm status` and `swarm feed` read it.",
              file=_sys.stderr)
    return ok


def _seq(agent: str, kind: str):
    """The `brain.message` row this event points at, so `payload_ref` names a real record.

    Read back rather than returned by the transition, because the transition's contract is the
    verb's result and adding a row sequence to it would make an internal key part of an API the
    CLI prints from. A read failure raises here and the hook swallows it, which is the correct
    direction: no event is better than an event whose ref points nowhere.
    """
    with store.read("runtime") as s:
        return s.scalar("SELECT seq FROM brain.message WHERE to_agent = %s AND kind = %s "
                        "ORDER BY seq DESC LIMIT 1", (agent, kind))


def _summary(**fields) -> str:
    reason = fields.get("reason") or ""
    if len(reason) > _REASON_IN_SUMMARY:
        fields["reason"] = reason[:_REASON_IN_SUMMARY]
        fields["reason_truncated_in_summary"] = True
        fields["reason_bytes"] = len(reason.encode("utf-8"))
    return _emit.summarise(fields)


def emit_agent_stopped(*, agent: str, by: str, reason: str, held=None) -> dict:
    seq = _seq(agent, "stop")
    return _emit.emit(
        type="fleet.agent.stopped",
        external=False, canon_touching=False, actor=by,
        subject_type="agent", subject_id=agent,
        work_item_id=held or None,
        payload_summary=_summary(agent=agent, by=by, reason=reason, held=held or ""),
        payload_ref=f"brain.message:{seq}" if seq else None,
    )


def emit_agent_started(*, agent: str, by: str, reason: str, held=None) -> dict:
    seq = _seq(agent, "start")
    return _emit.emit(
        type="fleet.agent.started",
        external=False, canon_touching=False, actor=by,
        subject_type="agent", subject_id=agent,
        work_item_id=held or None,
        payload_summary=_summary(agent=agent, by=by, reason=reason, held=held or ""),
        payload_ref=f"brain.message:{seq}" if seq else None,
    )


def emit_fleet_paused(*, by: str, note: str) -> dict:
    return _emit.emit(
        type="fleet.paused", external=False, canon_touching=False, actor=by,
        subject_type="fleet", subject_id="fleet_paused",
        payload_summary=_summary(by=by, reason=note),
        payload_ref="brain.runtime_flag:fleet_paused",
    )


def emit_fleet_resumed(*, by: str, note: str) -> dict:
    return _emit.emit(
        type="fleet.resumed", external=False, canon_touching=False, actor=by,
        subject_type="fleet", subject_id="fleet_paused",
        payload_summary=_summary(by=by, reason=note),
        payload_ref="brain.runtime_flag:fleet_paused",
    )


# ------------------------------------------------------------------ the hooks

def _hook_stopped(verb: str, result, kwargs: dict) -> None:
    r = result or {}
    if r.get("stopped") and _can_emit():
        emit_agent_stopped(agent=r.get("agent") or "", by=r.get("by") or "",
                           reason=r.get("reason") or "", held=r.get("held"))


def _hook_started(verb: str, result, kwargs: dict) -> None:
    """Only when a stop was actually LIFTED.

    `start` on an agent that was already running returns `released: False` and exits 2, and an
    event for that would say a terminal was started when nothing happened -- the same false
    record this whole task is about, filed in the trail meant to prevent it.
    """
    r = result or {}
    if r.get("released") and _can_emit():
        emit_agent_started(agent=r.get("agent") or "", by=r.get("by") or "",
                           reason=r.get("reason") or "", held=None)


def _hook_paused(verb: str, result, kwargs: dict) -> None:
    r = result or {}
    if not _can_emit():
        return
    emit_fleet_paused(by=r.get("by") or "", note=r.get("note") or "")


def _hook_resumed(verb: str, result, kwargs: dict) -> None:
    r = result or {}
    if not _can_emit():
        return
    emit_fleet_resumed(by=r.get("by") or "", note=r.get("note") or "")


_transitions.after_commit("stop", _hook_stopped)
_transitions.after_commit("start", _hook_started)
_transitions.after_commit("pause", _hook_paused)
_transitions.after_commit("resume", _hook_resumed)
