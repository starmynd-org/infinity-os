"""Telling a budget stop apart from a subscription refusal.

This file is the one property my lane must prove. Both halts look identical from a distance: a
run that stopped early, an engine that exited non-zero, a task still sitting `active`. They are
opposite conditions, and the cost of confusing them runs in both directions.

    A REFUSAL treated as a BREACH strands a healthy fleet. Measured on 2026-08-14: one five-hour
    ceiling became 41 charged attempts, 18 blocked tasks, 18 spurious operator questions and 11
    idle hours with every runner healthy. (`swarm-admiral/docs/rate-limits.md`.)

    A BREACH treated as a REFUSAL spends real money in a loop. The refusal path is `swarm reopen`,
    which clears the claim AND resets `attempts` to 0 by design -- the agent was never allowed to
    start, so it should not be charged. Apply that to a run killed for spending too much and the
    task returns to the queue with a fresh attempt ladder, is claimed by the next free terminal,
    and spends again. There is no counter left to stop it.

## How they are told apart, and why that order

    1. FIRST-PARTY. Did we stop this run ourselves? A budget stop writes its incident BEFORE the
       signal is sent, so the evidence is a row in `budget_incident` carrying this `run_id`. We
       know because we did it.
    2. INFERRED. Otherwise, does the engine log carry a refusal? That is text-scraping somebody
       else's message, and it is right to be second.
    3. Otherwise the bus decides: still `active` means the agent never reported, which is a
       task failure and charges an attempt.

First-party evidence beats text every time, and the order is not cosmetic. A long run killed for
budget may well have a rate-limit line somewhere in its log from an earlier turn that it retried
past. Check the text first and that run is misfiled as a refusal, reopened, and re-dispatched --
which is precisely the money-retry-loop failure above. Checking our own record first makes that
unreachable.

## Composing rather than fighting

The two mechanisms answer different questions and both answers are used:

    the refusal decides what happens to THIS TASK      -> reopen, unspent, wait for the window
    the budget decides whether a NEW DISPATCH may start -> preflight, refused until a human acts

So a refusal during an active budget stop yields: task reopened unspent (the refusal's verdict)
AND no new dispatch (the budget's verdict). Neither overrides the other, and the account rotation
in `swarm-run` keeps working untouched.

## The refusal detector is not reimplemented here

`limit_reset_epoch` is lifted out of `swarm-admiral/bin/swarm-run` at runtime, the same way
`tests/test-ratelimit.sh` does it, so this file cannot drift away from the detector the fleet
actually runs. A second copy of that regex would be a second definition of "refused", and the
first thing that happens to a second definition is that one of them is updated.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- the four halts

COMPLETED = "completed"
BUDGET_STOP = "budget_stop"
RATE_LIMIT = "rate_limit_refusal"
TASK_FAILURE = "task_failure"

# What the dispatcher should do, per halt. The whole distinction, in one table.
DISPOSITION = {
    # The operator's money. Not `fail` (the lane did nothing wrong) and NOT `reopen` (which
    # returns the task to the queue to be claimed and spent again). A human decides.
    BUDGET_STOP: "block",
    # The window reopens on its own. Clear the claim, reset attempts, do not charge the task.
    RATE_LIMIT: "reopen",
    TASK_FAILURE: "fail",
    COMPLETED: "none",
}

# Does the halt clear itself with the passage of time? This single boolean is the difference
# between "wait" and "stop", and it is why a budget stop must never take the refusal path.
SELF_CLEARING = {BUDGET_STOP: False, RATE_LIMIT: True, TASK_FAILURE: False, COMPLETED: False}

# Is the attempt charged to the task?
CHARGES_ATTEMPT = {BUDGET_STOP: False, RATE_LIMIT: False, TASK_FAILURE: True, COMPLETED: False}

DEFAULT_RUNNER = os.environ.get(
    "SWARM_RUN_PATH", "/mnt/c/Users/you/repos/internal/swarm-admiral/bin/swarm-run")


class RefusalDetectorUnavailable(RuntimeError):
    """The runner's LIMIT_PARSER could not be lifted.

    Raised rather than defaulting to "not a refusal". A detector that silently fails closed here
    would charge every rate-limit refusal as a task failure, which is the exact 2026-08-14
    incident. Better to stop and say so.
    """


@dataclass
class Halt:
    kind: str
    reason: str
    disposition: str
    self_clearing: bool
    charges_attempt: bool
    retry_after_epoch: int | None = None      # only ever set for a refusal
    incident_id: int | None = None            # only ever set for a budget stop
    evidence: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def is_budget_stop(self) -> bool:
        return self.kind == BUDGET_STOP

    @property
    def is_refusal(self) -> bool:
        return self.kind == RATE_LIMIT

    def line(self) -> str:
        after = f", retry after epoch {self.retry_after_epoch}" if self.retry_after_epoch else ""
        inc = f", incident {self.incident_id}" if self.incident_id else ""
        return (f"{self.kind}: {self.reason} -> {self.disposition} "
                f"(self-clearing={self.self_clearing}, charges attempt="
                f"{self.charges_attempt}{after}{inc})")


# --------------------------------------------------------------------------- the refusal probe

_PARSER_CACHE: dict[str, str] = {}


def _limit_parser_source(runner_path: str = DEFAULT_RUNNER) -> str:
    """Lift LIMIT_PARSER out of the runner rather than copying it. Never vendored."""
    if runner_path in _PARSER_CACHE:
        return _PARSER_CACHE[runner_path]
    try:
        src = open(runner_path, encoding="utf-8", errors="replace").read()
    except OSError as exc:
        raise RefusalDetectorUnavailable(
            f"could not read the runner at {runner_path} to lift LIMIT_PARSER ({exc}). "
            f"Set SWARM_RUN_PATH. Refusing to guess: a missing refusal detector charges every "
            f"rate limit to the task as a failure, which is the 2026-08-14 incident."
        ) from None
    marker = "LIMIT_PARSER=$(cat <<'PY'\n"
    if marker not in src:
        raise RefusalDetectorUnavailable(
            f"{runner_path} has no LIMIT_PARSER block. The runner's refusal detection moved or "
            f"was renamed; this module must be repointed rather than given its own copy.")
    body = src.split(marker, 1)[1].split("\nPY\n", 1)[0]
    _PARSER_CACHE[runner_path] = body
    return body


def limit_reset_epoch(log_path: str, runner_path: str = DEFAULT_RUNNER) -> int | None:
    """The runner's own answer: epoch to wait until, or None when the log has no refusal."""
    body = _limit_parser_source(runner_path)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(body)
        parser = fh.name
    try:
        proc = subprocess.run([sys.executable, parser, log_path],
                              capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(parser)
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    return int(out) if re.fullmatch(r"\d+", out) else None


# --------------------------------------------------------------------------- the classifier


def budget_stop_for_run(run_id: int | None, session_id: str = "") -> dict | None:
    """First-party evidence: an incident WE filed against this run, before we signalled it.

    THE RUN ID BRANCH IS TIME-FENCED, and that is not defensive coding. `brain.run` is keyed
    UNIQUE (work_item_id, attempt) and `run start` is an upsert, so a run row id is REUSED every
    time that pair recurs -- and it recurs routinely, because `reopen` and the answer-requeue path
    both reset `attempts` to 0, which puts the next dispatch back on attempt 1. Without the fence:

        attempt 1 is stopped for budget, incident filed against run row 42, task BLOCKED
        the operator raises the ceiling and reopens; attempts -> 0
        the task is re-claimed at attempt 1, and `run start` UPDATES row 42
        this run merely fails unreported -- an ordinary task failure
        `halt --run 42` finds the incident from the FIRST dispatch, blocks again, charges nothing

    which is a task that can never fail, never advance its ladder, and parks a human every round.
    `run start`'s conflict clause sets `started_at = now()`, so an incident belonging to the row's
    previous occupant is strictly older than this dispatch's start and this predicate excludes it.

    The session id needs no such fence: it is unique per engine session, so it can only ever name
    the run that produced it.
    """
    if run_id is None and not session_id:
        return None
    import store
    with store.read("runtime") as s:
        return s.one(
            "SELECT id, kind, action_taken, spend_usd, limit_usd, scope_type, scope_id, detail "
            "  FROM brain.budget_incident "
            " WHERE kind IN ('hard_stop','manual_stop') "
            "   AND action_taken IN ('run_stopped','agent_stopped','fleet_stopped') "
            "   AND ((%(run_id)s IS NOT NULL AND run_id = %(run_id)s "
            "         AND occurred_at >= (SELECT started_at FROM brain.run "
            "                              WHERE id = %(run_id)s)) "
            "        OR (%(sid)s <> '' AND session_id = %(sid)s)) "
            " ORDER BY id DESC LIMIT 1",
            {"run_id": run_id, "sid": session_id},
        )


def classify(*, log_path: str | None = None, task_state: str = "active",
             run_id: int | None = None, session_id: str = "",
             stopped_by_guard: dict | None = None,
             runner_path: str = DEFAULT_RUNNER) -> Halt:
    """The one classifier. Every surface calls this; nobody re-derives "was that a wall?".

    `stopped_by_guard` lets an in-process guard hand over what it already knows without a round
    trip; the database lookup is the same evidence and is used when the caller is a fresh process
    (which the runner always is).
    """
    # 1. FIRST-PARTY. We stopped it, so we know, and no log text can talk us out of it.
    incident = stopped_by_guard or budget_stop_for_run(run_id, session_id)
    if incident:
        return Halt(
            kind=BUDGET_STOP,
            reason=(f"budget {incident['kind']} on {incident['scope_type']}"
                    f"{':' + incident['scope_id'] if incident.get('scope_id') else ''}: "
                    f"spend {incident.get('spend_usd')} against ceiling "
                    f"{incident.get('limit_usd')}"),
            disposition=DISPOSITION[BUDGET_STOP],
            self_clearing=SELF_CLEARING[BUDGET_STOP],
            charges_attempt=CHARGES_ATTEMPT[BUDGET_STOP],
            retry_after_epoch=None,          # a stop does not have one. That IS the difference.
            incident_id=incident["id"],
            evidence=f"brain.budget_incident id={incident['id']} written before the signal",
            detail=dict(incident),
        )

    # 2. INFERRED, from somebody else's wording, using the runner's own parser.
    if log_path and os.path.exists(log_path):
        until = limit_reset_epoch(log_path, runner_path)
        if until is not None:
            return Halt(
                kind=RATE_LIMIT,
                reason="the engine refused on a subscription limit before doing any work",
                disposition=DISPOSITION[RATE_LIMIT],
                self_clearing=SELF_CLEARING[RATE_LIMIT],
                charges_attempt=CHARGES_ATTEMPT[RATE_LIMIT],
                retry_after_epoch=until,     # the window reopens by itself, at this time
                incident_id=None,            # and files NO breach record. It is not a breach.
                evidence=f"LIMIT_PARSER lifted from {runner_path} matched {log_path}",
            )

    # 3. The bus is the authority on the outcome; the log only ever explained it.
    if task_state == "active":
        return Halt(kind=TASK_FAILURE, reason="the agent did not report",
                    disposition=DISPOSITION[TASK_FAILURE],
                    self_clearing=SELF_CLEARING[TASK_FAILURE],
                    charges_attempt=CHARGES_ATTEMPT[TASK_FAILURE],
                    evidence="task still active after the engine exited")
    return Halt(kind=COMPLETED, reason=f"the agent reported: {task_state}",
                disposition=DISPOSITION[COMPLETED], self_clearing=False, charges_attempt=False,
                evidence=f"bus state {task_state}")
