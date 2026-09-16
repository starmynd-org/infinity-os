"""The console's actions: each one a room, a verb, and an argument shape.

There is no logic here that changes state. Every function assembles arguments and calls
`rooms.dispatch`, which calls `store.apply`, which runs the one registered transition. If a
behaviour the operator sees is not visible in a transition, it does not exist -- that is the
narrow waist, and this file is where a console is most tempted to break it.

**The undo table, and why it is a table rather than a boolean.** The receipt stripe promises
`undo` and the brief says that where no un-verb exists it must read `no undo, external` rather
than pretend. So each action declares its own inverse by name:

| action            | verb              | inverse                | why                                        |
|-------------------|-------------------|------------------------|--------------------------------------------|
| answer            | `answer`          | `amend` (reanswer)     | corrects the answer; the question stays answered |
| answer, external  | `answer`          | none                   | the answer WAS the external effect          |
| accept work       | `accept work`     | `unaccept work`        | migration 43: a real inverse, reason required |
| send back         | `reopen`          | none                   | the row may have gone to the FLEET, not to you |
| mark my task done | `done`            | `reopen`               | a real inverse: back to your queue, unspent  |
| defer, question   | `post`            | none                   | the child task exists and an agent may hold it |
| decline           | `answer`/`reopen` | as the underlying verb  |                                            |

`amend` is labelled `amend` and not `undo`, because calling a correction an undo is the same
class of lie as a progress bar with no denominator.

TWO ROWS OF THAT TABLE CHANGED ON 2026-08-28 AND BOTH WERE WRONG IN THE SAME WAY: they stated a
GENERAL rule where the truth was a fact about one row or one release.

  `accept work` read `none / nothing clears accepted_at, by design`. True of the runtime until
  migration 43, and it is what row `0413` was filed against rather than a rule to keep. The verb
  exists now, it clears the two acceptance columns and touches neither `state` nor `result`, and
  the withdrawal is recorded on the thread rather than erased, so the stripe carries it exactly
  as `MUST-NOT-BUILD` item 10 requires wherever a real inverse exists.

  `send back` read `none / the item is already back in the queue`. That sentence names the wrong
  queue and was measured false on the operator's own click: `reopen` leaves `agent_claimable`
  alone, so on a fleet row the item goes to the FLEET and leaves the human queue entirely.
  `brain.queue_open` returned 1 row for it before the click and 0 after, and he read the
  disappearance as success. `_where_reopen_left_it` now measures the destination on the row just
  written instead of asserting it here.
"""

from __future__ import annotations

from swarm_engine import steering

from . import rooms, scoping

OPERATOR_DEFAULT = "operator"


class ActionRefused(ValueError):
    """A guard this console owns: an empty reason, a missing default, a wrong kind."""


MIN_REASON = 12


def _need(text: str, what: str) -> str:
    text = (text or "").strip()
    if len(text) < MIN_REASON:
        # P3-04: the sentence names the act it refuses (`what`) and no other. The old tail
        # spoke of a reopen whatever the act was, so a refused Mark done read as a reopen.
        raise ActionRefused(
            f"{what} needs a real reason a fresh session can act on ({MIN_REASON} characters "
            f"minimum, counted after surrounding whitespace is removed; you sent {len(text)}). "
            f"An act with no reason is how a lane repeats the same defect.")
    return text


def answer(room: str, *, qid: str, text: str, item: dict, operator: str,
           amend: bool = False) -> dict:
    """Answer a question. **`requeue=False` on purpose, and it is the incident of 2026-08-16.**

    The engine's `answer` refuses to requeue behind an agent still heartbeating `working` on the
    blocked task, but only when the caller passes `requeue=False`. The console is not the surface
    that knows whether that process is gone -- the runner is, and it fences the engine and then
    requeues itself. A console that passed `requeue=True` would reproduce exactly the failure
    that put two agents on one task against one repo: the claim was atomic and still correct, and
    the gap was that `ask` blocked the TASK and left the PROCESS alive.
    """
    if not (text or "").strip():
        raise ActionRefused("an answer with no text is a silence that pretends to be a decision.")
    res = rooms.dispatch(room, "answer", item=item, actor=operator,
                         qid=qid, text=text, amend=amend, requeue=False)
    external = "EXTERNAL" in (item.get("gates") or [])
    return {
        "verb": "answer",
        "receipt": f'{qid} answered: "{_clip(text)}"',
        "undo": None if external else {"action": "amend", "label": "amend", "qid": qid},
        "no_undo_reason": "external" if external else None,
        "note": _requeue_note(res),
        "result": res,
    }


def _requeue_note(res: dict) -> str | None:
    if not isinstance(res, dict):
        return None
    if res.get("requeue_refused"):
        return (f"Answer recorded. NOT requeued: {res['requeue_refused']} is still heartbeating "
                f"on {res.get('task')}. Requeuing behind a live engine is how one task gets two "
                f"agents. The runner fences the engine and requeues.")
    if res.get("requeued"):
        return f"{res.get('task')} is back in the queue."
    return None


def accept_default(room: str, *, item: dict, operator: str) -> dict:
    default = (item.get("default") or "").strip()
    if not default:
        raise ActionRefused(
            f"{item['id']} states no default, so there is nothing for silence to have decided. "
            f"Answer it.")
    out = answer(room, qid=item["id"], text=default, item=item, operator=operator)
    out["receipt"] = f'{item["id"]} accepted the default: "{_clip(default)}"'
    return out


def accept_work(room: str, *, item: dict, operator: str) -> dict:
    """`as_operator=True` since lane E, row 0384, and it is not decoration.

    This call ran as `brain_runtime` until migration 36, which was invisible because the gate it
    passed was a string test on `by=`: "is this name in `brain.agent`". Measured from that same
    login on 2026-08-27, 6 of 6 forged names were accepted. Migration 36 makes the database ask
    `brain.current_human()` instead, so the console has to open a human login exactly as it
    already does for `recommend accept` and every admin verb. WHICH human it opens is
    `store.human_slug()`, and `web/app.py` verifies at startup that the process actually holds
    that credential rather than discovering it one refused Accept at a time.
    """
    res = rooms.dispatch(room, "accept work", item=item, actor=operator,
                         id=item["id"], by=operator, as_operator=True)
    return {
        "verb": "accept work",
        "subject_type": "work_item", "subject_id": str(item["id"]),
        "receipt": f"{item['id']} ACCEPTED by {operator}",
        # ROW 0413. THIS WAS `None` UNTIL MIGRATION 43, AND THE `None` WAS HONEST AT THE TIME.
        #
        # The sentence that stood here said "nothing clears it", and that was true of the runtime
        # rather than a rule about the world: `MUST-NOT-BUILD` item 10 requires the stripe to
        # carry a real inverse WHEREVER ONE EXISTS and to say so plainly where none does, so the
        # honest `no undo` was the correct rendering of a missing verb, and row `0413` was filed
        # against the absence. `unaccept work` now exists and clears `accepted_at`/`accepted_by`
        # and nothing else, so the record survives as a withdrawal ON THE THREAD rather than as
        # an erasure: migration 43 permits the clear only where an `unaccept` thread row, signed
        # by `brain.current_human()`, was written first in the same transaction. A decision you
        # can take back and a decision with no record are still different things.
        #
        # The verb requires a reason and this spec carries none, which is deliberate: the stripe
        # lives seven seconds and `console.js::undo` posts `csrf`, `action` and `id` only, so
        # `unaccept_work` below supplies the one reason that is TRUE of every press of this
        # button and says which surface it came from. The typed reason belongs to the other door,
        # on `/task/<id>`, where the withdrawal is a changed mind rather than a misclick.
        "undo": {"action": "unaccept", "label": "Unaccept", "id": item["id"]},
        "no_undo_reason": None,
        "result": res,
    }


# The one reason the receipt-stripe door records, and it names its own surface. `unaccept work`
# refuses an empty reason for a good argument -- at the row, an acceptance withdrawn and an
# acceptance that never happened are the same absence, and only the reason can say whether this
# was a misclick or a changed mind -- so this door answers that question rather than dodging it.
# It is true of both callers by construction: the queue stripe and the stack ticker both live
# inside the receipt window and neither has a text field.
RECEIPT_WINDOW_REASON = (
    "withdrawn from the console's receipt stripe with no reason typed: a misclick inside the "
    "receipt window rather than a changed mind. A considered withdrawal is typed on the item's "
    "own page")


def unaccept_work(room: str, *, item_id: str, reason: str, operator: str) -> dict:
    """Withdraw an acceptance. Row `0413`, the UI half of a verb that already exists.

    His words on 2026-08-28: *"nothing happened when I accepted work. That would maybe be a
    moment for confetti and for it to change from accept work to like accepted. Maybe you have an
    option to like unaccept or like send back or undo the acceptance would be good."*

    TWO DOORS, AND THEY RECORD DIFFERENT THINGS, which is what the transition asks for in so many
    words: *"the reason is also the only place the record can say WHICH kind of withdrawal this
    was: a misclick on a card, or a human who read the report again and changed his mind. The
    first is a defect report about the console, the second is the disagreement signal."* The
    receipt stripe is the first and records `RECEIPT_WINDOW_REASON`; the control on `/task/<id>`
    is the second and requires a typed one. A single canned reason on both doors would have
    turned that dataset into noise, and a required text box on a seven-second stripe would have
    been a control nobody can use in time.

    NO `by=`. Attribution is `brain.current_human()`, read inside the transaction and unreachable
    from SQL, so the caller never types a name and cannot get one wrong. `as_operator=True` is
    what opens the human login the database then reads.

    THE ITEM IS NOT A QUEUE CARD AND CANNOT BE. `brain.queue_open` arm 1 is `state = 'done' AND
    accepted_at IS NULL`, so the moment the acceptance lands the row leaves the view this
    console's stale-card guard checks against. `web/app.py::_perform` therefore exempts this
    action from that guard exactly as it exempts the stopwatch and the image verbs, and for the
    same reason: the surface acts on a row that has legitimately left the queue. The id is still
    checked, by the verb, against `brain.work_item`.
    """
    reason = " ".join((reason or "").split())
    if reason and len(reason) < MIN_REASON:
        raise ActionRefused(
            f"withdrawing an acceptance needs a real reason a fresh session can act on "
            f"({MIN_REASON} characters minimum). At the row, an acceptance that was withdrawn and "
            f"one that never happened are the same absence, and they mean opposite things about "
            f"whether a human read the work.")
    res = rooms.dispatch(room, "unaccept work", item=None, actor=operator,
                         id=item_id, reason=reason or RECEIPT_WINDOW_REASON, as_operator=True)
    was = (res.get("was_accepted_by") or "").strip() if isinstance(res, dict) else ""
    return {
        "verb": "unaccept work",
        "subject_type": "work_item", "subject_id": str(item_id),
        "receipt": f"{item_id} is back in the queue: the acceptance"
                   + (f" by {was}" if was else "") + " is withdrawn and the report is untouched",
        # THE INVERSE OF THE INVERSE IS THE VERB THE OPERATOR ALREADY HAS, and it is on the card
        # that just came back. Offering `Accept work` from this stripe would put an acceptance one
        # unconsidered click from a withdrawal and back, which is the rubber stamp the Judge tier
        # exists against.
        "undo": None,
        "no_undo_reason": f"{item_id} is back in the queue with `Accept work` on it; accept it "
                          f"there when you have read it again",
        "result": res,
    }


def send_back(room: str, *, item: dict, reason: str, operator: str) -> dict:
    """`Send back` runs `reopen`, and THE RECEIPT SAYS WHERE THE ROW ACTUALLY WENT. Row `0414`.

    This function used to close with `no_undo_reason = "the item is already back in the queue,
    unspent"`, which is a sentence about the wrong queue and was measured false on the operator's
    own click of 2026-08-28. `reopen` clears `claimed_by` and leaves `agent_claimable` exactly as
    it found it, so on a fleet row the item goes back to the FLEET and leaves the human queue
    entirely: `brain.queue_open` returned 1 row for it before the click and 0 after. What he read
    on screen was a stripe telling him it was back in his queue, and what he said afterwards was
    *"It has now disappeared from the queue, so I guess that is good."* He scored a silent
    failure as a success, which is the worse half of this defect.

    SO THE SENTENCE IS MEASURED AFTER THE WRITE RATHER THAN WRITTEN INTO THE FILE. `reopen`
    returns `{id, state, was}` and knows nothing about who may claim the row next, so the two
    columns that decide the destination are read here, once, on the row just written. A receipt
    is a claim about what happened; a hardcoded one is a claim about what usually happens, and
    the difference is exactly what this row is.

    THERE IS NO INVERSE VERB AND THE STRIPE SAYS SO IN THE VERB'S OWN TERMS, per `MUST-NOT-BUILD`
    item 10. Bringing a row back from the fleet is `swarm set <id> agent_claimable false
    --as-operator`, and `set` is deliberately not in the Queue's allowlist (must-not-build 2), so
    the console names the command instead of growing a button that would need that grant. That
    remedy has existed in the product since row `0399` and was printed in exactly one place: the
    forgery refusal on `swarm done`, which is the one screen the operator will never see.
    """
    reason = _need(reason, "send back")
    res = rooms.dispatch(room, "reopen", item=item, actor=operator,
                         id=item["id"], reason=reason, agent=operator)
    where = _where_reopen_left_it(item["id"])
    return {
        "verb": "reopen",
        "receipt": f'{item["id"]} sent back: "{_clip(reason)}" · {where["receipt"]}',
        "undo": None,
        "no_undo_reason": where["no_undo"],
        "note": where.get("note"),
        "result": res,
    }


def _where_reopen_left_it(item_id: str) -> dict:
    """The two columns that decide where a reopened row goes, read on the row just written.

    `agent_claimable` says whether the fleet may take it; `brain.runtime_flag.fleet_paused` says
    whether anything is running to take it. Those are different facts and the receipt separates
    them: a row handed to a live fleet is a hand-off, and a row handed to a stopped one is work
    parked with nobody, which is what live looks like today.

    A read failure is reported as one. The write already happened, so a receipt that invented a
    destination here would be the same class of lie the hardcoded sentence was.
    """
    try:
        import store
        from store import reads as R
        with store.read() as s:
            row = s.one("SELECT agent_claimable, state FROM brain.work_item WHERE id = %s",
                        (item_id,))
        paused = R.fleet_paused()
    except Exception as exc:                                            # noqa: BLE001
        return {"receipt": "where it went next could not be read",
                "no_undo": f"the reopen is recorded; this console could not read the row back "
                           f"to say where it went ({exc}). `swarm show {item_id}` has it"}
    if row is None:
        return {"receipt": "and the row is no longer readable",
                "no_undo": f"`swarm show {item_id}` is where this row's state actually lives"}
    if not row["agent_claimable"]:
        return {"receipt": "it is on YOUR board now, as `Mark my task done`",
                "no_undo": "the item is on your own board, unspent, and nothing has left the "
                           "queue"}
    if paused:
        return {"receipt": "it went to the FLEET, WHICH IS PAUSED, and left your queue",
                "no_undo": f"it is the fleet's now and the fleet is stopped, so nothing will "
                           f"pick it up until you resume. `swarm set {item_id} agent_claimable "
                           f"false --as-operator` takes it back onto your board",
                "note": f"{item_id} is out of your queue and in front of a paused fleet. Resume "
                        f"the fleet, or take it back with `swarm set {item_id} agent_claimable "
                        f"false --as-operator`."}
    return {"receipt": "it went to the FLEET and left your queue",
            "no_undo": f"it is the fleet's now, not yours. `swarm set {item_id} "
                       f"agent_claimable false --as-operator` takes it back onto your board"}


def mark_my_task_done(room: str, *, item: dict, summary: str, operator: str) -> dict:
    """`done` on the operator's OWN work. The guard is in rooms.assert_allowed_on, not here."""
    summary = _need(summary, "marking your own task done")
    res = rooms.dispatch(room, "done", item=item, actor=operator,
                         id=item["id"], summary=summary, agent=operator)
    return {
        "verb": "done",
        "receipt": f'{item["id"]} marked done: "{_clip(summary)}"',
        "undo": {"action": "undo_done", "label": "undo", "id": item["id"]},
        "no_undo_reason": None,
        "result": res,
    }


MIN_TITLE = 6


def post_mine(room: str, *, title: str, lane: str, operator: str) -> dict:
    """THE OPERATOR'S OWN WORK, INTO HIS OWN QUEUE. The door that did not exist until task 0267.

    `mark_my_task_done` above has been here since D7 shipped and could never fire, because
    nothing could create the row it acts on: `actor_type` has no default, no CLI flag set it, and
    `brain.queue_open`'s second arm reads `WHERE w.actor_type = 'human'`. So the console offered
    an operator a verb for work he had no way to put there.

    This is the same `post` verb the Scope room calls and the defer-with-question path calls --
    no second implementation, and no new entry in the room's allowlist, because `post` was
    already in it. The single difference is `actor_type='human'`, and that word is not a flag
    this file gets to assert: `store.apply` reads it and opens the OPERATOR's database login,
    and migration 20's trigger refuses the row to any login with no `brain.human_role` mapping.
    A console running on a host with no operator credential is refused here in words rather than
    posting an agent item that looks like the operator's.

    Deliberately NOT the Scope flow. Scope elicits a definition of done and asks per row "how
    will an agent prove this without you?", which is the right question for work an agent will
    do and the wrong one for "renew the insurance". His own day has to be enterable in one line
    or it will be entered somewhere else, and a second list is the thing this system is against.
    """
    title = " ".join((title or "").split())
    if len(title) < MIN_TITLE:
        raise ActionRefused(
            f"your own task needs a title of at least {MIN_TITLE} characters. This row is what "
            f"you will read at 7am with no other context.")
    try:
        res = rooms.dispatch(room, "post", item=None, actor=operator,
                             title=_clip(title, 90), lane=lane or "ops",
                             posted_by=operator, actor_type="human", body=title)
    except Exception as exc:                                            # noqa: BLE001
        # StoreConfigError is the expected one: this process holds no operator credential, which
        # is a sentence the operator can act on and a 500 is not. Re-raised as a refusal rather
        # than swallowed, and NEVER retried without actor_type -- posting it as an agent item
        # would be the console quietly doing the forgery it refuses.
        if exc.__class__.__name__ == "StoreConfigError":
            raise ActionRefused(str(exc)) from None
        raise
    tid = res.get("id") if isinstance(res, dict) else res
    return {
        "verb": "post",
        "receipt": f"{tid} is yours: it is in your queue and only you can mark it done",
        "undo": None,
        "no_undo_reason": "the item exists; mark it done or send it back from its own card",
        "result": res,
        "task": tid,
    }


# P3-01, 2026-09-13. A reopen has no inverse of its own, and until now this receipt sent no reason,
# so the Attention stripe printed "no undo — the server sent no reason with this receipt", which
# Screening read as something having gone wrong. The reason is the store's truth: `reopen`
# (`engine/swarm_engine/transitions.py`) resets state, claim and attempts and leaves `result` as
# `done` wrote it, so the note typed at done stays as the task's stored result text.
REOPEN_KEEPS_NOTE = ("a reopen has no inverse of its own: the task is unfinished again, and the "
                     "note typed at done stays as its stored result text")


def undo_done(room: str, *, item: dict, operator: str) -> dict:
    res = rooms.dispatch(room, "reopen", item=item, actor=operator, id=item["id"],
                         reason="undone from the console within the receipt window",
                         agent=operator)
    return {"verb": "reopen", "receipt": f"{item['id']} back in your queue", "undo": None,
            "no_undo_reason": REOPEN_KEEPS_NOTE, "result": res}


def defer_with_question(room: str, *, item: dict, question: str, lane: str,
                        operator: str) -> dict:
    """Hesitation converted into agent work.

    "I cannot decide until I see X" posts an agent task to produce X. `--parent` is not optional
    here for the same reason it is not optional on the bus: it carries the `external` and
    `canon_touching` gates down to the child, and a flagged item that produces an unflagged child
    is how a gate that looks intact stops protecting anything.
    """
    question = _need(question, "a defer-with-question")
    parent = item.get("task") or ""
    res = rooms.dispatch(room, "post", item=item, actor=operator,
                         title=question, lane=lane or "research",
                         posted_by=operator, parent=parent,
                         body=f"Produced for {item['id']}, which the operator cannot decide "
                              f"without this. Item: {item['title']}")
    child = res.get("id") if isinstance(res, dict) else res
    return {
        "verb": "post",
        "receipt": f"{item['id']} deferred: {child} posted for the missing input"
                   + (f", gates inherited from {parent}" if parent else ""),
        "undo": None,
        "no_undo_reason": "the child task exists and an agent may already hold it",
        "result": res,
    }


def decline(room: str, *, item: dict, reason: str, operator: str) -> dict:
    """Returned to the producer. Re-raisable once, and only with a new event."""
    reason = _need(reason, "a decline")
    if item["kind"] == "question":
        out = answer(room, qid=item["id"], text=f"declined: {reason}", item=item,
                     operator=operator)
        out["receipt"] = f'{item["id"]} declined, returned to {item.get("src") or "the producer"}'
        return out
    return send_back(room, item=item, reason=f"declined: {reason}", operator=operator)


def recommend(room: str, *, item: dict, accept: bool, operator: str, reason: str = "") -> dict:
    """Approve or reject a recommendation card.

    `as_operator=True` ON BOTH HALVES, and it is not decoration: it is what makes `store.apply`
    open the operator login, which is the only session `brain.current_human()` answers for and the
    only one migrations 32 and 33 permit a decision from.

    IT WAS THE ACCEPT HALF ONLY UNTIL TASK 0313, on the reasoning that a rejection dispatches
    nothing and so is not invariant 1. True, and beside the point: what a rejection moves is
    `brain.queue_acted_on`, PLAN.md's falsifier for this layer, which counts `state = 'rejected'`
    and groups by `template_id` while reading neither `decided_by` nor `actor_type`. A rejection
    prunes a playbook, and this verb used to write whatever `by=` said with no gate at all.
    """
    verb = "recommend accept" if accept else "recommend reject"
    # Native Attention supplies a reason field. Other rooms retain their existing
    # single-button rejection contract until their controls are changed together.
    kwargs = ({"reason": _need(reason, "rejecting a recommendation")}
              if not accept and room == "attention" else {})
    res = rooms.dispatch(room, verb, item=item, actor=operator, id=item["id"], by=operator,
                         as_operator=True, **kwargs)
    return {"verb": verb, "receipt": f"{item['id']} {'approved' if accept else 'rejected'}",
            "subject_type": "recommendation",
            "undo": None, "no_undo_reason": "recommendations record a decision", "result": res}


def _source(item: dict) -> tuple:
    """D6b addresses a queue item as `(source_type, source_id)`, one of work_item, question or
    recommendation. The console's `kind` maps onto it exactly, which is the point: both lanes
    are describing the same three rows."""
    return ({"question": "question", "recommendation": "recommendation"}.get(item["kind"],
                                                                            "work_item"),
            item["id"])


def bump(room: str, *, item: dict, reason: str, delta: float, operator: str) -> dict:
    """D6b's `queue bump`. A decaying additive nudge, never a pin and never a score edit.

    The reason is required by the verb AND by a CHECK on the column, because every bump is a
    labelled disagreement between the operator and the model and that log IS the weight-tuning
    dataset. An unlabelled row in a tuning dataset is worse than no row.
    """
    reason = _need(reason, "a bump")
    st, sid = _source(item)
    res = rooms.dispatch(room, "queue bump", item=item, actor=operator,
                         source_type=st, source_id=sid, delta=float(delta), reason=reason,
                         by=operator)
    return {"verb": "queue bump", "receipt": f"{item['id']} bumped {delta:+g}: \"{_clip(reason)}\"",
            "undo": None, "no_undo_reason": "it decays on a 24h half life rather than pinning",
            "result": res}


#: How long `V` records for when nobody says. Short on purpose: this is a note on the row in front
#: of him, not a monologue, and the monologue already has a landing (`voice record` with no
#: `--onto`, into the intake inbox). A default nobody can sit through is a default nobody uses.
VOICE_SECONDS_DEFAULT = 20
VOICE_SECONDS_MAX = 300

#: The transcript may not exceed this. `brain.thread.text` takes the full text and the CLI's own
#: note is bounded by the audio, so this is not a size limit dressed as a safety one: it is the
#: refusal that stops a caller crafting a POST with an arbitrary body against `note`, which is the
#: exact shape MUST-NOT-BUILD item 5 forbids and which the allowlist line now makes reachable.
VOICE_TRANSCRIPT_MAX = 20_000


def voice_note(room: str, *, item: dict, seconds, operator: str, wav: str = "") -> dict:
    """His ask 3: press `V` on a row, speak, and the transcript lands as a note ON THAT ROW.

    MUST-NOT-BUILD item 5 is overruled for exactly this, by him, on 2026-08-30, and the condition
    is in `web/MUST-NOT-BUILD.md` at item 5. Read it before widening anything here.

    THE CONSOLE DOES NOT OPEN THE MICROPHONE AND MUST NOT LEARN HOW. The capture runs as the
    `voice` CLI in a SUBPROCESS, doing its own writes as itself, exactly as it does when he runs it
    in a terminal: `voice open` before the microphone, the save check, the retain-and-rehash, the
    transcription verdict. All five of those verbs stay unreachable from every room. What comes
    back over the pipe is a transcript and a capture id, and this function posts them through the
    one verb this room is allowed.

    That split is the whole reason the overrule is one allowlist line rather than six. A rendering
    surface that could call `voice open` could open a microphone from a crafted POST.

    THE CAPTURE IS STILL CLOSED IN ONE TRANSACTION WITH THE NOTE. `--no-land` deliberately leaves
    the capture at `transcribed`, and `note` moves it to `landed` while writing the thread row. So
    either both exist or neither does, and a caller that dies in between leaves a capture that
    `voice health` reports as STUCK rather than a note nothing accounts for.

    THERE IS NO TYPED PATH TO THIS FUNCTION. `text` is not a parameter. The only body it will ever
    post is one a transcription engine produced, which is what keeps item 5's own sentence true:
    there is no box whose text goes nowhere, because there is no box.
    """
    import json
    import os
    import subprocess
    import time

    try:
        seconds = int(seconds or VOICE_SECONDS_DEFAULT)
    except (TypeError, ValueError):
        raise ActionRefused(f"{seconds!r} is not a number of seconds") from None
    if not 1 <= seconds <= VOICE_SECONDS_MAX:
        raise ActionRefused(
            f"a voice note is between 1 and {VOICE_SECONDS_MAX} seconds; {seconds} is not. A "
            f"longer recording is a monologue, and that has its own landing: `voice record` with "
            f"no --onto puts it in the intake inbox as one row to sort.")

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd = [os.sys.executable, os.path.join(root, "voice/bin/voice")]
    # `ingest-file` is the SAME pipeline as `record` through the same verbs and the same checks;
    # the voice lane's own README says so and says why. It is here so a caller with audio already
    # on disk, and a test with a fixture wav, reach this function rather than a parallel one.
    cmd += (["ingest-file", wav, "--expect-seconds", "1"] if wav
            else ["record", "--seconds", str(seconds)])
    cmd += ["--no-land", "--produced-by", operator]

    started = time.monotonic()
    # THE TIMEOUT IS THE RECORDING PLUS THE TRANSCRIPTION, not a constant. whisper.cpp runs about
    # 13x slower than windows-sapi on the same audio, and a timeout sized for the fast engine
    # would kill a healthy capture mid-transcription and leave it STUCK, which is the one signal
    # this lane has against an outage nobody raised.
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds * 6 + 180)
    except subprocess.TimeoutExpired:
        raise ActionRefused(
            f"the capture did not finish within {seconds * 6 + 180}s. Nothing was posted. The "
            f"capture row exists and `voice health` will report it, so this is visible rather "
            f"than lost.") from None
    # EXIT 3 IS `landed-without-words` AND IT IS NOT A FAILURE TO POST THROUGH.
    #
    # The voice CLI exits 3 when the engine produced no text. The lane's own rule is that this is
    # *"NOT a swallow and NOT the end of the capture"*: the audio is retained and hashed, so the
    # note still lands with an honest body saying the engine heard nothing and naming the
    # recording. Treating 3 as a refusal here made the console SILENT on exactly the case the
    # whole lane was built around, and it made `V` disagree with `voice ingest-file --onto`, which
    # posts the honest note. Two paths, one landing: they agree, or one of them is lying about
    # what happened.
    #
    # Measured in `test_the_voice_note.py` before this branch existed: a capture of real silence
    # took the refusal arm and nothing reached the thread.
    landed_without_words = (p.returncode == 3)
    if (p.returncode != 0 and not landed_without_words) or not (p.stdout or "").strip():
        raise ActionRefused(
            f"the capture failed (exit {p.returncode}). Nothing was posted, and the failure is a "
            f"counted row: run `voice health`. "
            f"{(p.stderr or '').strip().splitlines()[-1] if (p.stderr or '').strip() else ''}")
    try:
        out = json.loads((p.stdout or "").strip().splitlines()[-1]
                         if (p.stdout or "").strip().startswith("{")
                         else (p.stdout or "")[(p.stdout or "").index("{"):])
    except (ValueError, IndexError):
        raise ActionRefused(
            "the capture returned something this console could not read as JSON. Nothing was "
            "posted rather than posting a guess at what it meant.") from None

    text = (out.get("note_text") or "").strip()
    if not text:
        raise ActionRefused(
            "the capture produced no note text at all, which is different from producing no "
            "WORDS. A capture that heard nothing still returns a note saying so and naming the "
            "retained audio, so an empty one here means the pipe, not the microphone.")
    if len(text) > VOICE_TRANSCRIPT_MAX:
        raise ActionRefused(
            f"the transcript is {len(text)} characters, over the {VOICE_TRANSCRIPT_MAX} this door "
            f"accepts. The audio is retained and nothing is lost; land it with "
            f"`voice ingest-file <wav> --onto {item['id']}` from a terminal.")

    res = rooms.dispatch(room, "note", item=item, actor=operator,
                         id=item["id"], text=text, agent=operator,
                         capture_id=out.get("capture_id"))
    heard = int(out.get("transcript_chars") or 0)
    # THE ROUND TRIP IS TIMED AND THE TIME IS ON THE RECEIPT, because his definition of done says
    # it is. A voice affordance whose cost nobody measured is one he tries twice and stops using.
    ms = round((time.monotonic() - started) * 1000)
    said = (f"{heard} characters" if heard else "NO WORDS: the audio is retained, nothing guessed")
    return {"verb": "note",
            "receipt": f"{item['id']} voice note, {said}, {ms}ms",
            # `note` appends to an append-only thread. There is no un-note and saying so is the
            # honest thing MUST-NOT-BUILD item 10 requires wherever no real inverse exists.
            "undo": None,
            "no_undo_reason": "the thread is append-only; the audio is retained and hashed",
            "result": {**(res if isinstance(res, dict) else {}),
                       "capture_id": out.get("capture_id"),
                       "transcript_chars": heard,
                       "transcription_status": out.get("transcription_status"),
                       "media_sha256": out.get("media_sha256"),
                       "round_trip_ms": ms}}


def not_fast(room: str, *, item: dict, operator: str, note: str = "") -> dict:
    """D6b's `queue demote`: the `not fast` button, and the calibration miss is the same
    transaction. A demote that recorded no miss would let the operator absorb a bad estimate
    silently and the producer would keep making it.

    THE RECEIPT NAMES THE TWO TIERS THE VERB ACTUALLY WROTE (task 0158). It used to read
    "demoted out of Decide" for every item whatever tier it was in, which was a sentence about
    the button rather than about the write, and it was wrong for the Judge items the button
    mostly gets pressed on. The verb returns `was` and `tier`; those are what the operator is
    told, and they are the same two values that went into the calibration row.
    """
    st, sid = _source(item)
    res = rooms.dispatch(room, "queue demote", item=item, actor=operator,
                         source_type=st, source_id=sid, by=operator,
                         producer=(item.get("src") or "").split(" · ")[0], note=note)
    moved = res if isinstance(res, dict) else {}
    was, now_in = (moved.get("was") or "").title(), (moved.get("tier") or "").title()
    return {"verb": "queue demote",
            "receipt": (f"{item['id']} moved {was} -> {now_in}, calibration miss logged against "
                        f"{moved.get('producer') or 'its producer'}"
                        if was and now_in else
                        f"{item['id']} demoted, calibration miss logged against "
                        f"{(item.get('src') or 'its producer').split(' · ')[0]}"),
            "undo": None, "no_undo_reason": "the miss is the record; removing it is the failure "
                                            "it exists to prevent",
            "result": res}


def defer(room: str, *, item: dict, kind: str, operator: str, wake_at: str = "",
          question: str = "", reason: str = "", label: str = "",
          acknowledge_default: bool = False) -> dict:
    """D6b's `queue defer`. Five kinds, each carrying a wake condition. No untyped defer.

    `acknowledge_default` is not a convenience flag: the verb REFUSES a defer past a pending
    silence window until the caller has acknowledged what silence will ship, because the
    DEFAULTED discipline only works if silence is always informed. The console shows that as an
    interstitial and passes the acknowledgement through; it never sets it on the operator's
    behalf.
    """
    st, sid = _source(item)
    kw = {"source_type": st, "source_id": sid, "kind": kind, "by": operator,
          "acknowledge_default": bool(acknowledge_default)}
    if kind == "until-time":
        kw["wake_at"] = wake_at
        kw["label"] = label or wake_at
    elif kind == "until-question":
        kw["question"] = _need(question, "a defer with a question")
        kw["lane"] = item.get("lane") or "research"
        kw["workdir"] = item.get("workdir") or ""
    elif kind == "decline":
        kw["reason"] = _need(reason, "a decline")
    res = rooms.dispatch(room, "queue defer", item=item, actor=operator, **kw)
    return {"verb": "queue defer",
            "receipt": f"{item['id']} deferred, {kind}"
                       + (f" · {label or wake_at}" if kind == "until-time" else ""),
            "undo": None,
            "no_undo_reason": _wake_promise(kind, res, label or wake_at),
            "result": res}


def _wake_promise(kind: str, res, when: str) -> str:
    """What will actually bring this item back, per kind. Never one sentence for all five.

    THIS LINE USED TO READ "it wakes when the condition is met" FOR EVERY KIND, and for every
    kind it was false (task 0158): the read filtered on `woke_at IS NULL` alone, nothing in the
    system calls `queue wake` on a schedule, and a `+2h` defer was therefore a permanent
    disappearance wearing a timer. The read now honours the two conditions the store can
    evaluate, so the promise is true for those two and it is worth saying which two, because the
    third one is still a promise only a human keeps.
    """
    task = (res or {}).get("wake_task") if isinstance(res, dict) else None
    return {
        "until-time": f"it returns to its tier on its own at {when}" if when else
                      "it returns to its tier on its own when the wake time passes",
        "until-question": (f"it returns to its tier on its own when {task} lands, and it comes "
                           f"back cheaper than it left" if task else
                           "it returns to its tier when the task it is waiting on lands"),
        "until-event": ("nothing in this system can see that event fire, so this one waits for "
                        "an explicit `queue wake`. `doctor` reports it if it has not fired in "
                        "14 days rather than letting it sit."),
        "accept-default": "recorded as a decision, not a snooze: it does not come back",
        "decline": "returned to the producer, re-raisable once and only with a new event",
    }.get(kind, "the wake condition is recorded")


# ------------------------------------------------------------------ the operator's stopwatch
#
# WHOSE MINUTES THESE ARE IS THE ONE THING THIS PAIR MUST NOT GET WRONG. `brain.time_entry` is
# keyed on `who`, every figure that reads it defaults to `who='operator'`, and a console that
# passed a session id, a login name or a browser fingerprint would write rows that are in the
# ledger and in NONE of his numbers -- a stopwatch that runs and measures nobody. So `who` is
# `web.app.OPERATOR` (CONSOLE_OPERATOR, default `operator`), the same name this console already
# acts under, and it is the same name `web.model` reads back with. The console's write and the
# console's read cannot disagree; the console and the CLI can, and that is stated in
# `web/model.py:stopwatch`.

SOURCE_TYPES = ("work_item", "question", "recommendation")


def _refusal(exc: Exception) -> None:
    """A queue verb's own refusal, re-raised as the console's.

    `QueueError` is D6b's "this verb is refusing this call": two timers at once, no timer running,
    an agent name where a human one belongs. Left alone it reaches `web/app.py`'s last `except`
    and is rendered as a 500 with a stack trace behind it, so the operator is told the console
    broke when in fact the store answered him. Matched on the class NAME rather than imported,
    which is the shape `post_mine` already uses for `StoreConfigError`: `web/app.py` imports
    `human_queue` inside a `try`, because a runtime without D6b's lane must still boot.
    """
    if exc.__class__.__name__ == "QueueError":
        raise ActionRefused(str(exc)) from None


def time_start(room: str, *, item: dict | None, item_id: str, source_type: str,
               operator: str, note: str = "") -> dict:
    """Start the stopwatch on one item. The refusal path is the interesting one.

    `item` is None when the id is not a live queue item, and that is NOT an error here. The ledger
    is explicit that timing something outside a live tier is legitimate -- he may have just
    accepted or answered it -- and `time_ledger._tier_at` records `None` for the tier rather than
    inventing one, so the entry counts toward the overall figure and toward no tier's. A console
    that required a card would have a dead button on most of `/task/<id>`, which is the surface
    the operator reads a finished report on and the surface he most wants a stopwatch on.

    What is NOT taken on trust is which table the id is in: `source_type` rides the form because
    the card is gone, so it is checked against the three kinds the queue addresses, and the verb
    then refuses an id that names no row.
    """
    if item is not None:
        st, sid = _source(item)
    else:
        st, sid = (source_type or "work_item").strip(), item_id
        if st not in SOURCE_TYPES:
            raise ActionRefused(
                f"{st!r} is not a kind this queue addresses. One of: {', '.join(SOURCE_TYPES)}.")
    try:
        res = rooms.dispatch(room, "queue time start", item=item, actor=operator,
                             source_type=st, source_id=sid, who=operator, note=note or "")
    except Exception as exc:                                            # noqa: BLE001
        _refusal(exc)
        raise
    tier = res.get("tier_at_start")
    return {
        "verb": "queue time start",
        "receipt": (f"timer {res['id']} running on {sid}"
                    + (f", started in {tier.title()}" if tier else
                       ", which is not in a live tier: it counts in the overall figure and in no "
                       "tier's")
                    + f". Past {res['cap_seconds'] / 3600:.0f}h it is recorded as abandoned."),
        "undo": {"action": "time_abandon", "label": "not work: abandon it", "id": sid},
        # There IS an inverse and it is not `undo`, for the same reason `amend` is not called one:
        # a started entry that was a mistake is stopped as ABANDONED, which leaves the row in the
        # ledger and out of every mean. Deleting it is refused by the table.
        "no_undo_reason": None,
        "result": res,
    }


def time_stop(room: str, *, operator: str, abandon: bool = False, note: str = "") -> dict:
    """Stop the running timer, and SAY WHAT THE DATABASE RECORDED rather than what was asked for.

    The cap is the trigger's, not this file's and not the verb's Python half: past four hours the
    stop is forced to `abandoned`. `queue time stop` re-reads the row afterwards and returns
    `capped`, so the receipt can state the relabelling. A console that printed "stopped, 41.2
    minutes" over a row the database recorded as abandoned would be reporting its own request.
    """
    try:
        res = rooms.dispatch(room, "queue time stop", item=None, actor=operator,
                             who=operator, abandon=bool(abandon), note=note or "")
    except Exception as exc:                                            # noqa: BLE001
        _refusal(exc)
        raise
    where = res["source_id"]
    if res["capped"]:
        tail = (f". PAST THE CAP ({res['cap_seconds'] / 3600:.0f}h), so the database recorded it "
                f"as ABANDONED and it counts in no mean. `queue time correct {res['id']}` if you "
                f"know what the interval really was")
    elif not res["measured"]:
        tail = ". Recorded as abandoned at your request: in the ledger, out of every mean"
    else:
        tail = ((f" in {res['tier_at_start'].title()}" if res["tier_at_start"] else "")
                + ", measured")
    return {
        "verb": "queue time stop",
        "receipt": f"timer {res['id']} {res['ended_how']} on {where}: "
                   f"{res['minutes']:.1f} minutes{tail}",
        "undo": None,
        # `time_correct` is the real inverse and it is not a button: it needs two timestamps and a
        # reason. Naming it here is the console pointing at the verb rather than standing in for
        # it -- the same rule `rooms.UNREGISTERED_OWNER` exists for.
        "no_undo_reason": "a stopped entry is never edited. `queue time correct` supersedes it "
                          "with a reason, and both rows survive",
        "result": res,
    }


def scope_post(room: str, *, intent: str, lane: str, rows: list, operator: str) -> dict:
    """The end of the elicitation flow: post the task carrying the definition the operator kept.

    ONE write now, and that is the change worth recording. This used to be two: `post` put the
    definition in the body, where a reader of the task expects it, and then a second dispatch
    copied the same rows onto the append-only thread, because D7 measured `done` overwriting
    `work_item.result` -- the same column `post` wrote the body into -- so a definition that lived
    only in the body was destroyed by the work finishing. The screen said out loud that this was a
    workaround for a data loss in the store rather than a fix for it.

    Migration 14 is the fix: the body lands in `work_item.brief`, write-once, overwritten by no
    verb. The duplicate copy had one job and no longer has it, so it is gone along with `note`
    from the Scope room's verb allowlist in `rooms.py`. Task 0169.

    The receipt states the unverifiable count rather than a success. A post whose definition
    cannot be checked without the operator has told him something, and burying it under "posted"
    is how a lazy scope stops punishing itself.
    """
    intent = _need(intent, "a scope post")
    kept = [r for r in rows if r.get("kept")]
    body = scoping.render(rows)
    # `confidence` rides in `signals`, not as a keyword: the signal vocabulary is the store's,
    # and a scope with an unverifiable check is a low-confidence scope BY the surfacing policy's
    # own definition, which is what routes the output back to the operator's Judge tier. The UI
    # is not predicting that outcome, it is writing the input that causes it.
    res = rooms.dispatch(room, "post", item=None, actor=operator,
                         title=_clip(intent, 90), lane=lane or "research",
                         posted_by=operator, body=body or intent,
                         signals={"confidence": "low" if scoping.unverifiable(kept) else "medium"})
    tid = res.get("id") if isinstance(res, dict) else res
    bad = scoping.unverifiable(kept)
    return {
        "verb": "post",
        "receipt": (f"{tid} posted, {len(kept)} "
                    f"{'check' if len(kept) == 1 else 'checks'}"
                    + (f", {len(bad)} of them unverifiable, so expect it back in Judge"
                       if bad else ", all machine-verifiable")),
        "undo": None,
        "no_undo_reason": "the task exists and an agent may already hold it",
        "result": res,
        "task": tid,
    }


# --------------------------------------------------------------------------- V5: images
#
# THE ONE PLACE IN THIS FILE THAT DOES NOT CALL `rooms.dispatch` ONCE, and the reason is
# structural rather than a shortcut. An attach is read, then hash, then record: three registered
# transitions with a filesystem read between the first and the second, so it cannot be one
# `dispatch` call without `rooms` learning how to read files. What `dispatch` buys is that the
# room gate is passed BEFORE `store.apply` is reached, and `attach_image` buys exactly that by
# asserting all three verbs up front, before the path is even resolved. A room that may not run
# one of the three is refused before the file is opened, which is stricter than being refused
# after the bytes have been read.
#
# The pipeline itself is `web/images.py::attach`, and it is a CALLER of `store.apply` rather than
# a writer -- the CLI at `web/bin/image` reaches the same four verbs through the same function.
# No surface reimplements a transition.

_ATTACH_VERBS = ("image attach", "image attached", "image failed")


def attach_image(room: str, *, subject_type: str, subject_id: str, path: str,
                 operator: str) -> dict:
    """Point at a file, hash it, and record the pointer on this item.

    Every exit leaves a row. A refused MIME, an oversized file, a path that is not there: each
    one lands as `state='failed'` naming the stage, because the alternative is a request that
    disappears, and a request that disappears is the failure mode this whole lane is shaped by.
    """
    from . import images
    for verb in _ATTACH_VERBS:
        rooms.assert_allowed(room, verb)
    path = (path or "").strip()
    if not path:
        raise ActionRefused(
            "an attach needs an absolute path to a file on this host. The console records a "
            "pointer and a hash and never the bytes, so it needs the file where it is rather "
            "than a copy of it -- which is also why this is a path and not an upload.")
    try:
        res = images.attach(subject_type, subject_id, path, by=operator, actor_type="human",
                            as_operator=True)
    except images.AttachFailed as exc:
        # NOT re-raised as a refusal that vanishes. The row is already `failed` and the card now
        # renders the red block until the operator acts on it; this receipt is the immediate
        # half of the same fact.
        return {
            "verb": "image failed",
            "receipt": f"attach FAILED at {exc.stage} · nothing was recorded · {_clip(str(exc), 90)}",
            "undo": None,
            "no_undo_reason": "nothing was attached, so there is nothing to undo. The failed "
                              "attempt stays on the card until you discard it",
            "result": {"attach_id": exc.attach_id, "state": "failed", "stage": exc.stage},
        }
    except images.ImageError as exc:
        raise ActionRefused(str(exc)) from None
    return {
        "verb": "image attached",
        "receipt": (f"image attached · {res['name']} · {res['bytes']} bytes · "
                    f"sha {res['sha256'][:7]} · on {res['pointer_host']}"),
        "undo": "detach_image",
        "undo_label": "undo",
        "result": res,
    }


def detach_image(room: str, *, attach_id: str, operator: str, discard: bool = False) -> dict:
    """Remove the pointer. THE FILE ON DISK IS UNTOUCHED and the receipt says so.

    `discard` is the same verb reached from a FAILED attach, and it is a different sentence: a
    failed attempt attested nothing, so discarding it removes a finding rather than a pointer.
    """
    from . import images
    if discard:
        rooms.assert_allowed(room, "image dismissed")
        res = images.discard(attach_id, by=operator, as_operator=True)
        return {
            "verb": "image dismissed",
            "receipt": f"failed attach discarded · {attach_id} · nothing was ever recorded",
            "undo": None,
            "no_undo_reason": "the attempt attested nothing; discarding it removes the finding "
                              "and leaves the record of the attempt",
            "result": res,
        }
    res = rooms.dispatch(room, "image detached", item=None, actor=operator,
                         attach_id=attach_id, by=operator, as_operator=True)
    return {
        "verb": "image detached",
        "receipt": (f"pointer removed, file kept · {res['pointer']} stays on "
                    f"{res['pointer_host']}"),
        "undo": None,
        # An honest `no undo`, per this file's own table. Re-attaching is a NEW attach that
        # re-reads and re-hashes the file, which is a different act with a different id -- and
        # calling that an undo would be the same class of lie as a progress bar with no
        # denominator.
        "no_undo_reason": "the file was never moved; attach it again to point at it once more, "
                          "which re-reads and re-hashes it under a new attach id",
        "result": res,
    }


def _clip(text: str, n: int = 60) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


# ------------------------------------------------------------------ V3: dispatching an option
#
# THE ONE THING THIS BLOCK DOES NOT DO IS DISPATCH. It assembles arguments for `recommend accept`
# and hands them to `rooms.dispatch`, exactly like every other function in this file, and
# `recommend accept` is the single enforcement point for `requires_human`.
#
# WHAT THAT ENFORCEMENT IS CHANGED AT TASK 0290, AND THIS COMMENT WAS THE CLAIM V9 FALSIFIED. It
# used to end "a drafted option therefore cannot become executed work without a human choosing
# it", resting on three tests: an empty decider, a decider registered in `brain.agent`, and a
# caller running under the fleet runner. The middle one is a DENYLIST read as a gate -- it
# refuses the names that ARE agents, so every name that is not one passed, and the agent picks
# the name. The V9 acceptance run (task 0222) dispatched real work from an agent connection under
# `by='Andrew'` and under `by='zzz-not-a-person'`, both booked `actor_type='human'`.
#
# The gate is now the LOGIN: `brain.current_human()` (migration 20, extended to this table by
# migration 32) answers from `session_user`, and it is NULL for `brain_runtime` -- what every
# agent surface connects as. That is why the call below passes `as_operator=True`, and why the
# claim in this comment is now true: it rests on which connection wrote the row rather than on a
# string this file passed.
#
# The console's overrule is real and it is bounded: the console may now CREATE work. It still may
# never fabricate an agent's REPORT, and `done` stays refused on any row whose `actor_type` is not
# human -- `web/rooms.py::assert_allowed_on`, unchanged by this lane.
#
# NO NEW VERB, and no new entry in the Queue's allowlist: `recommend accept` has been in it since
# D7. What is new is `execute=True`, which is that verb's own documented half -- it posts the work
# item in the same transaction as the acceptance, so an acceptance that cannot spawn its work
# rolls back rather than recording a decision whose consequence never happened.


def dispatch_option(room: str, *, item: dict, n, operator: str) -> dict:
    """Choose one drafted option and dispatch it. Two acts, and this is the second.

    The first act is the operator selecting a row, which happens in the browser and writes
    nothing. This is the second, on a different target, and it names the option by ordinal so the
    row is re-read from the store rather than taken from the markup that posted it.
    """
    from . import model                                  # local: web.model imports no actions
    opt = model.option_for(item["source_type"], item["id"], n)
    if opt is None:
        raise ActionRefused(
            f"{item['id']} has no drafted option {n!r}. The set may have been redrafted since "
            f"this card was rendered; reload and choose again rather than dispatching an option "
            f"that is not there.")
    if opt["kind"] == "sprint":
        # C section 1.5: a sprint is never spawned from this card. Scoping is its own surface
        # with its own demolition step, and a sprint posted as a one-line task is the thing the
        # Scope room exists to prevent.
        raise ActionRefused(
            "a sprint is not dispatched from a card. `Open sprint scoping` takes this option's "
            "own words into the Scope room, where the drafter proposes a definition of done and "
            "you demolish it; what gets posted then is a scoped brief rather than one line.")
    if opt.get("spawned_work_item"):
        raise ActionRefused(
            f"option {opt['n']} already dispatched {opt['spawned_work_item']}"
            + (f", decided by {opt['decided_by']}" if opt.get("decided_by") else "")
            + ". Accepting it twice would execute the same proposal twice.")
    if opt.get("state") != "open":
        raise ActionRefused(
            f"option {opt['n']} is {opt.get('state')}, not open, so there is nothing left to "
            f"decide on it.")

    res = rooms.dispatch(room, "recommend accept", item=item, actor=operator,
                         id=opt["recommendation_id"], by=operator,
                         lane=opt["lane"] or item.get("lane") or "queue",
                         # `workdir` is deliberately not passed: `queue defer`'s and
                         # `recommend accept`'s shared `_spawned_workdir` inherits it from the
                         # SUBJECT task, because work executing a proposal about a row runs in
                         # that row's tree. A constant here would run an engine task in a client
                         # tree, which is the defect task 0100's workdir gate exists to close.
                         # THE LOGIN, not a flag. The store opens the OPERATOR connection for
                         # this verb only when the call says so, and migration 32 refuses an
                         # acceptance from any session `brain.current_human()` returns NULL for.
                         # Without this keyword the console is refused by its own database.
                         # (Spelling the store's write function here would trip route (f) of
                         # web/tests/test_option_dispatch.py, which reads this block as text to
                         # prove the console holds no second path to executed work.)
                         as_operator=True,
                         note=f"Dispatched from the option rail: {opt['label']} "
                              f"({opt['kind']}, {opt['who']}, {opt['thinking']}, {opt['size']}). "
                              f"Estimate {opt['cost_est'] if opt['cost_est'] is not None else 'none stated'} "
                              f"/ {opt['time_est'] or 'no time stated'}, drafted by "
                              f"{opt['drafted_by'] or 'an unnamed producer'}.",
                         execute=True)
    model.invalidate_options()
    task = (res or {}).get("spawned_work_item")
    eng = model.lane_engine(opt["lane"] or "")
    where = (f"lane {opt['lane']} · {eng['engine']} {eng['model']} effort {eng['effort']}"
             if eng.get("model") else f"lane {opt['lane'] or 'unset'} · {eng['why']}")
    return {
        "verb": "recommend accept",
        "receipt": f"{item['id']} dispatched option {opt['n']}, {opt['label']}: "
                   f"{task or 'no task'} posted, {where}",
        "undo": None,
        # RECALL-BEFORE-CLAIM IS NOT OFFERED, AND THIS IS THE HONEST HALF OF THE CHOICE THE
        # DESIGN LEFT OPEN (DESIGN-SYSTEM.md section 10: "recall-before-claim vs `no undo --
        # claimed`, whichever the verb layer proves"). What the verb layer proves is that the
        # console cannot recall: `cancel` is a fleet-shaped verb and `web/rooms.py` puts none of
        # them in any room, so a `recall` button here would either widen that allowlist for one
        # affordance or reimplement `cancel` in the console -- the second implementation the
        # narrow waist exists to prevent. The stripe says what is true instead.
        "no_undo_reason": (f"{task} exists and the fleet may already have claimed it. Nothing in "
                           f"this room recalls a posted task: `cancel` is a fleet verb and no "
                           f"room holds it. Send it back or cancel it from the fleet"),
        "result": res,
        "task": task,
    }


# ============================================================ V4: take-control and the picker
#
# Task 0166. Four actions, three verbs, and no state change that is not one of them. The rule
# this section is most at risk of breaking is the one V00 names as most at risk in v2: a terminal
# UI that writes state directly is a violation, and take-control is a new surface with a text box
# on it. So the text box posts `msg` -- the verb that already exists, that already writes
# `brain.message` AND the thread row -- and the mode itself is `steer take` / `steer release`.
#
# The undo table above gains three rows, and all three are `no undo` with a reason:
#
# | action        | verb                | inverse | why                                          |
# |---------------|---------------------|---------|----------------------------------------------|
# | take control  | `steer take`        | none    | the mark is permanent; `Release control` ends |
# |               |                     |         | the session and does not unmark the run       |
# | send to agent | `msg`               | none    | the words are on an append-only thread and in |
# |               |                     |         | the terminal's mailbox; both are said things  |
# | set for next  | `agent config set`  | none    | setting it again is the correction, and the   |
# |   claim       |                     |         | row carries who set it and when               |


def take_control(room: str, *, agent: str, item: dict, attempt: int, operator: str) -> dict:
    """Seize one live run. The consequence panel is the only path here, and it says all of this.

    `as_operator=True` is not decoration: it is what makes `store.apply` open the operator login,
    which is the only session `brain.current_human()` answers for, which is what stops any agent
    process writing a mark that says `operator`. See `swarm_engine/steering.py`.
    """
    res = rooms.dispatch(room, "steer take", item=item, actor=operator,
                         id=item["id"], agent=agent, by=operator, attempt=attempt,
                         as_operator=True)
    already = bool(res.get("already"))
    return {
        "verb": "steer take",
        "receipt": (f"already steering {agent} on {item['id']}" if already else
                    f"you are steering {agent} on {item['id']} · run marked steered · "
                    f"excluded from measurement"),
        "undo": None,
        "no_undo_reason": ("the mark is permanent: the run is co-authored from here on, and a "
                           "record you can silently rewrite is not a record. `Release control` "
                           "ends the session and leaves the mark"),
        "note": None if already else steering.CONSEQUENCE,
        "result": res,
    }


def release_control(room: str, *, agent: str, item: dict, operator: str) -> dict:
    res = rooms.dispatch(room, "steer release", item=item, actor=operator,
                         id=item["id"], agent=agent, by=operator, as_operator=True)
    return {
        "verb": "steer release",
        "receipt": f"steering ended · run {item['id']} marked steered · excluded from measurement",
        "undo": None,
        "no_undo_reason": "the mark stays. It is a fact about how this run was produced",
        "result": res,
    }


def steer_say(room: str, *, agent: str, item: dict, text: str, operator: str) -> dict:
    """The operator's half of the conversation. `msg`, unchanged, with the task attached.

    The task is what puts it on the THREAD as well as in the mailbox, and the thread is the whole
    concession that makes the overrule safe: `show --full` has to be able to reconstruct what
    happened without this console.
    """
    text = (text or "").strip()
    if not text:
        raise ActionRefused("an empty message would land on the thread saying nothing.")
    res = rooms.dispatch(room, "msg", item=item, actor=operator,
                         to=agent, task=item["id"], text=text, agent=operator)
    return {
        "verb": "msg",
        "receipt": f"you → {agent}: {_clip(text)}",
        "undo": None,
        "no_undo_reason": "it is on the thread and in the terminal's mailbox; both are said",
        # NEVER LOOKS INSTANT, BECAUSE IT IS NOT. A terminal reads its mailbox when it next
        # checks; nothing here interrupts a running engine.
        "note": f"on the thread and in {agent}'s mailbox. A terminal reads its mailbox when it "
                f"checks, which is not the instant you pressed send",
        "result": res,
    }


def set_for_next_claim(room: str, *, agent: str, values: dict, operator: str) -> dict:
    """The picker. Applies at the next claim, and the receipt says so rather than looking instant.

    `permission_mode` is not filtered out here. It is REFUSED IN THE TRANSITION, by name, with
    the argument attached, because a control that is only absent from a template is one template
    edit away from existing.
    """
    values = {k: v for k, v in (values or {}).items() if v is not None}
    if not values:
        raise ActionRefused("nothing was picked, so there is nothing to set.")
    res = rooms.dispatch(room, "agent config set", item=None, actor=operator,
                         agent=agent, values=values, by=operator, as_operator=True,
                         note=f"set from the console picker by {operator}")
    said = " · ".join(f"{k} {v or 'unset'}" for k, v in sorted(values.items()))
    return {
        "verb": "agent config set",
        "receipt": f"{agent}: {said} — applies at the next claim, this run is unaffected",
        "undo": None,
        "no_undo_reason": "setting it again is the correction; the row records who set it and when",
        "note": ("the preferred account applies at the next runner start rather than the next "
                 "claim: the account ring is built once when the runner starts"
                 if "account" in values else None),
        "result": res,
    }
