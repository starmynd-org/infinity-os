"""When a page is allowed to wake a human, and what it is allowed to say.

Four rules, and each one exists because its absence has a named failure:

**The never-page list.** Machine-volume types never page, whatever else is true. Session start and
end run at machine volume with 14-day retention; a pager that forwarded them would be muted by
the operator inside a day and then the questions would not arrive either. The fastest way to lose
a paging channel is to use it for traffic.

**Quiet hours, with an escape that is derived rather than declared.** Between the configured
hours a page is held unless it is urgent, and urgency is not a field a producer sets. It is read
off the bus's own semantics: **a question with a stated default is not urgent, because silence is
a usable answer; a question with no stated default is urgent, because silence stalls it forever.**
That is why `swarm ask` makes `--default` mandatory, and it means the operator's own discipline
at ask-time is what decides whether he gets woken.

**Stated defaults travel in the message.** A page that says only "a question is waiting" forces
the operator out of bed to find out whether it could have waited. A page that carries the stated
default lets him decide from the lock screen. The default is in the payload for that reason and
the pager always prints it, including when it is empty, because "no default stated" is the single
most useful thing that message can say.

**A page is taken back when it stops being true (task 0162).** `question.answered` used to sit on
the never-page list, so the one event that can cancel a page was the one event the pager threw
away, and a question answered at 02:05 left a 02:00 page on the lock screen still demanding a
decision that was already made. It is now a *retraction*: it pages only to cancel a page this same
pager already sent, and it is silent otherwise. That keeps the reason the never-page list exists
intact rather than weakening it, and the argument is arithmetic rather than judgement -- **a
retraction is bounded by the pages already sent, at most one each, so it can never become
volume.** A question that was held, or that this pager never paged at all, produces nothing.

For the same reason a retraction ignores quiet hours: it exists only because the operator was
already woken, and holding the good news until 07:00 leaves the lie on the lock screen for the
whole night it matters.
"""

from __future__ import annotations

import datetime as _dt
import json
import os

#: The operator's wall clock. The WSL host runs UTC and the Windows clock on this box reads
#: UTC+3, which already cost one lane a three-hour timestamp confusion in crosstalk. Quiet hours
#: are a human's hours, so they are evaluated in the operator's zone and the offset is explicit
#: rather than inherited from whatever the process happens to think local time is.
OPERATOR_UTC_OFFSET_HOURS = float(os.environ.get("OPERATOR_UTC_OFFSET_HOURS", "3"))

QUIET_START_HOUR = int(os.environ.get("PAGING_QUIET_START", "22"))   # inclusive
QUIET_END_HOUR = int(os.environ.get("PAGING_QUIET_END", "7"))        # exclusive

#: Types that never page. Not "rarely". Never.
#:
#: `question.answered` was on this list until task 0162 and does not belong here: it is not machine
#: volume, it is the cancellation of a page, and it is handled by RETRACTS below. Putting it here
#: was what made two docstrings' promise of retraction untrue in code.
NEVER_PAGE = frozenset({"session.started", "session.ended"})

#: Types that CANCEL a page rather than raise one. A retraction is sent only when this pager can
#: point at a page it sent and has not already taken back, so its volume is bounded by the page
#: volume and it costs nothing when there is nothing outstanding.
#:
#: `question.withdrawn` joined it on 2026-08-18 (task 0159) and it is the SAME shape of fact from
#: the lock screen's point of view: the thing the page asked for is no longer being waited on. It
#: is not the same fact underneath -- nobody decided anything -- so `render_retraction` says which
#: of the two happened rather than calling both "answered".
RETRACTS = frozenset({"question.answered", "question.withdrawn"})

#: Types that page through quiet hours regardless of any other rule, because the condition they
#: report is that the fabric itself has stopped delivering.
ALWAYS_URGENT = frozenset({"fabric.subscriber.quarantined"})


def operator_now(utc_now: _dt.datetime = None) -> _dt.datetime:
    now = utc_now or _dt.datetime.now(_dt.timezone.utc)
    return now.astimezone(_dt.timezone(_dt.timedelta(hours=OPERATOR_UTC_OFFSET_HOURS)))


def in_quiet_hours(utc_now: _dt.datetime = None) -> bool:
    h = operator_now(utc_now).hour
    if QUIET_START_HOUR <= QUIET_END_HOUR:
        return QUIET_START_HOUR <= h < QUIET_END_HOUR
    return h >= QUIET_START_HOUR or h < QUIET_END_HOUR


def stated_default(row: dict) -> str | None:
    """The `--default` the asker was forced to supply. `None` means the payload did not carry one.

    A gated tombstone carries no payload at all, so this returns None for every flagged event.
    That is correct and it is also why a tombstone is always treated as urgent below: the pager
    cannot see whether silence is safe, so it does not get to assume it is.
    """
    raw = row.get("payload_summary") or ""
    if not raw:
        return None
    try:
        return (json.loads(raw) or {}).get("default_if_unanswered")
    except ValueError:
        return None


def is_urgent(row: dict) -> bool:
    if row.get("type") in ALWAYS_URGENT:
        return True
    if row.get("tombstone"):
        # Contents withheld, so urgency is unknowable. Unknown resolves to urgent, because the
        # cost of a needless 3am page is an annoyed operator and the cost of the other mistake is
        # a flagged question that nobody sees until the brief.
        return True
    d = stated_default(row)
    return not (d or "").strip()


def qid_of(row: dict) -> str:
    """The question this row is about, or "" when the gate withheld it.

    Payload first and `subject_id` second, the same precedence `render` uses. For a flagged event
    both are blanked in SQL, so this returns "" and every caller has to cope with not knowing.
    """
    try:
        payload = json.loads(row.get("payload_summary") or "{}") or {}
    except ValueError:
        payload = {}
    return payload.get("qid") or row.get("subject_id") or ""


def decision(row: dict, utc_now: _dt.datetime = None, prior: list = None) -> dict:
    """Should this page, and why. The `why` is returned so a held page is explainable later.

    `prior` is only read for a RETRACTS type: the pages this pager sent that this event could be
    cancelling, oldest first, from `memory.PageMemory`. Empty or None means there is nothing
    outstanding, and a retraction with nothing to retract is silence rather than a message.
    """
    t = row.get("type", "")
    if t in RETRACTS:
        return _retraction_decision(t, prior or [])
    if t in NEVER_PAGE:
        return {"page": False, "reason": f"{t} is on the never-page list (machine volume)"}
    quiet = in_quiet_hours(utc_now)
    urgent = is_urgent(row)
    if quiet and not urgent:
        return {"page": False, "held": True, "urgent": False,
                "reason": (f"quiet hours {QUIET_START_HOUR:02d}:00-{QUIET_END_HOUR:02d}:00 "
                           f"operator-local, and a stated default makes silence a usable answer")}
    return {"page": True, "urgent": urgent, "quiet": quiet,
            "reason": ("always-urgent type" if t in ALWAYS_URGENT
                       else "contents withheld, urgency unknowable" if row.get("tombstone")
                       else "no stated default: silence stalls this" if urgent
                       else "outside quiet hours")}


def _retraction_decision(t: str, prior: list) -> dict:
    """Three cases, and the third one is the honest limit of what a gated pager can say.

    Quiet hours are not consulted, on purpose: see the module docstring. A retraction only exists
    because a page went out, so it cannot wake anyone the page did not already wake.
    """
    if not prior:
        return {"page": False, "retract": True,
                "reason": (f"{t} with no page outstanding: this pager either never paged that "
                           f"question or has already taken it back, so there is nothing to cancel")}
    if len(prior) == 1:
        e = prior[0]
        what = e["qid"] or f"the flagged page on event {e['event_seq']}"
        return {"page": True, "retract": True, "urgent": False, "cancels": [e["key"]],
                "reason": f"cancels the page sent for {what} at {e['sent_at']}"}
    # Only reachable for a flagged answer, because an identified one looks up exactly one qid. The
    # gate blanked the id, so the pager knows a flagged question was answered and cannot know
    # which of the flagged pages it settles. It says so and cancels NOTHING rather than guessing:
    # a wrong "this one is settled" is worse than the stale page it would replace.
    return {"page": True, "retract": True, "urgent": False, "cancels": [],
            "reason": (f"a flagged question was settled ({t}) and {len(prior)} flagged pages are "
                       f"outstanding; the gate withheld the id, so no single page can be cancelled")}


def render_retraction(row: dict, prior: list) -> str:
    """Take back a page, in the words the operator needs on a lock screen at 02:05.

    The first line is the whole message for anyone reading a notification preview, and it is also
    the line `memory.PageMemory` reads back out of `pages.log` after a restart, which is why its
    shape (`[type] <qid or 'event N'> ...`) matches the page it cancels.

    IT SAYS WHICH KIND OF STAND-DOWN THIS IS. An answered question was decided; a withdrawn one
    was not, and telling the operator it was "answered" would be the lie task 0159 removed from
    the store reappearing in the transport. `settled` below is the verb-shaped half of that
    sentence and the type name in the bracket is the machine-readable half.
    """
    t = row.get("type", "question.answered")
    settled = "is answered" if t == "question.answered" else "was withdrawn, NOT answered"
    was = "answered" if t == "question.answered" else "withdrawn without an answer"
    if len(prior) == 1:
        e = prior[0]
        if e["qid"]:
            return (f"[{t}] {e['qid']} {settled}. Stand down.\n"
                    f"This cancels the page sent {e['sent_at']}. Nothing is waiting on you for it.\n"
                    f"What is still open: swarm questions")
        return (f"[{t}] event {e['event_seq']} {settled}. Stand down.\n"
                f"This cancels the flagged page sent {e['sent_at']}. That event is flagged and this "
                f"pager is gated from its contents, so it can name the page it is cancelling and "
                f"not the question.\n"
                f"What is still open: swarm questions")
    seqs = ", ".join(f"event {e['event_seq']}" for e in prior)
    return (f"[{t}] a flagged question was {was}. Cannot say which.\n"
            f"{len(prior)} flagged pages are still outstanding ({seqs}) and this pager is gated "
            f"from flagged contents, so it cannot tell which one this settles. None of them is "
            f"cancelled here.\n"
            f"What is still open: swarm questions")


def render(row: dict, decision_: dict) -> str:
    """The message text. A tombstone says less on purpose, and says that it is saying less.

    An event carrying `external: true` or `canon_touching: true` may wake a listener to prepare,
    never to send, spend, deploy, publish or touch canon. This subscriber is effect-class, so it
    never receives a flagged event's contents at all: the row it gets is blanked in SQL before it
    crosses the process boundary. What it can still say is that something flagged is waiting and
    where to look, which is the whole job.
    """
    seq = row.get("event_seq")
    t = row.get("type", "?")
    when = row.get("occurred_at")
    if row.get("tombstone"):
        return (f"[{t}] event {seq} needs you.\n"
                f"Contents WITHHELD: this event is flagged "
                f"(external={row.get('external')}, canon_touching={row.get('canon_touching')}) "
                f"and this pager is effect-class, so it is gated from the payload by design.\n"
                f"Occurred {when}. Read it: swarm questions | python3 -m fabric.cli show {seq}")

    payload = {}
    try:
        payload = json.loads(row.get("payload_summary") or "{}") or {}
    except ValueError:
        payload = {"summary": (row.get("payload_summary") or "")[:600]}

    if t == "fabric.subscriber.quarantined":
        # The fabric reporting on itself. Say what stopped and what clears it, because the person
        # reading this at 3am should not have to remember the release verb.
        return (f"[{t}] {payload.get('subscriber', '?')} is {payload.get('condition', '?')}.\n"
                f"lag={payload.get('lag')} last_seq={payload.get('last_seq')} "
                f"head_seq={payload.get('head_seq')}; its cursor last moved "
                f"{payload.get('cursor_last_moved')}.\n"
                f"Events are accumulating behind it and nothing is consuming them.\n"
                f"Clear it: python3 -m fabric.cli release "
                f"--subscriber {payload.get('subscriber', '?')} --by <you>")

    qid = payload.get("qid") or row.get("subject_id") or ""
    who = payload.get("from", "")
    question = (payload.get("question") or "").strip()
    default = payload.get("default_if_unanswered")
    default_line = (f"If you say nothing: {default}" if (default or "").strip()
                    else "NO DEFAULT STATED: silence stalls this task.")

    head = f"[{t}] {qid} from {who}".rstrip() if qid else f"[{t}] event {seq}"
    body = question if question else (row.get("payload_summary") or "")[:600]
    more = "\n(summary only; full text is on the bus)" if payload.get(
        "truncated_in_summary") else ""
    lines = [head, body + more]
    if t.startswith("question."):
        lines.append(default_line)
        if qid:
            lines.append(f'Answer: swarm answer {qid} "..."')
    return "\n".join(x for x in lines if x)
