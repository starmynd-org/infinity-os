"""Producer 3: voice capture failures.

WHY THIS EXISTS RATHER THAN A LOG LINE. `voice health` is a command somebody has to run, and the
2026-08-17 incident is exactly the case where nobody ran it: the operator believed his monologue
had saved and there was no reason to go looking. A check that depends on suspicion catches the
failures you already suspect. An event does not.

WHY THE CALL SITE IS `after_commit` AND NOT A LINE IN `voice/voice_capture/cli.py`. Wiring it
into the CLI would page the CLI's failures and silently not a cron's, not the console's, not a
phone client's. Every surface goes through `store.apply`, so the join lives where `apply` can see
it and a surface never has to remember it. This is the same argument `fabric/producers/
questions.py` makes about `swarm ask`, and the same defect it was written to close.

WHAT IS HONESTLY NOT TRUE YET, and it must not be papered over: V00 records that paging reaches
nothing. The listener is supervised and caught up; the Telegram relay on 3180 is unreachable, so
nothing leaves this machine. This module makes a capture failure REACH THE FABRIC, which is the
half this lane owns. Whether the fabric then reaches a human is V00 item 2 and somebody else's
lane. An event that lands in `brain.event` and pages nobody is still strictly better than a log
line nobody greps -- it is countable, it survives, and it turns on the day the relay does.
"""

from __future__ import annotations

import store
from store import transitions as _transitions

from .. import emit as _emit


def emit_capture_failed(*, capture_id: str, stage: str, detail: str,
                        produced_by: str = None, check_budget: bool = True) -> dict:
    """One failed capture, one `voice.capture.failed` event.

    The flags are FALSE and stated rather than defaulted: a voice capture is the operator's own
    machine recording the operator's own voice into the operator's own store, so it touches
    nothing outside and no canon. `emit` refuses an event whose flags nobody decided, which is
    why they are arguments here rather than an omission.
    """
    row = _capture(capture_id) or {}
    summary = _emit.summarise({
        "capture_id": capture_id,
        "stage": stage,
        # A real summary and not a cut: the detail is what tells the operator whether to
        # re-record, free a disk or install an engine, and the full text is on the capture row.
        "detail": (detail or "")[:800],
        "truncated_in_summary": len(detail or "") > 800,
        "state": row.get("state", ""),
        "host": row.get("host", ""),
        # THE POINTER TRAVELS WITH THE FAILURE. A page that says "your recording failed" and
        # cannot say whether the audio survived sends the operator to look; one that carries the
        # path and the hash lets him answer it from the lock screen.
        "media_pointer": row.get("media_pointer") or "",
        "media_sha256": row.get("media_sha256") or "",
        "audio_retained": bool(row.get("media_pointer")),
        "opened_at": str(row.get("opened_at", "")),
    })
    return _emit.emit(
        type="voice.capture.failed",
        external=False,
        canon_touching=False,
        department="", lane="voice",
        subject_type="voice_capture", subject_id=capture_id,
        payload_summary=summary,
        payload_ref=f"brain.voice_capture:{capture_id}",
        actor_type="ai",
        produced_by=produced_by,
        actor="voice",
        check_budget=check_budget,
    )


def _capture(capture_id: str) -> dict | None:
    try:
        with store.read() as s:
            return s.one(
                "SELECT capture_id, state, host, media_pointer, media_sha256, opened_at "
                "  FROM brain.voice_capture WHERE capture_id = %s", (capture_id,))
    except Exception:  # noqa: BLE001 -- a hook that cannot read must not fail the verb
        return None


def _hook_capture_failed(verb: str, result, kwargs: dict) -> None:
    """Runs AFTER `voice failed` has committed, on its own connection, as `brain_producer`.

    It cannot fail the verb: `store.transitions._run_after_commit` catches and prints. That is
    deliberate and it is the one place a swallow is correct -- a pager that is down must not turn
    a recorded failure into an unrecorded one. The counted row is already in the store by the
    time this runs, so the worst case is a failure that is countable and not paged, never a
    failure that is neither.
    """
    emit_capture_failed(
        capture_id=kwargs.get("capture_id", "") or (result or {}).get("capture_id", ""),
        stage=kwargs.get("stage", ""),
        detail=kwargs.get("detail", ""),
    )


_transitions.after_commit("voice failed", _hook_capture_failed)
