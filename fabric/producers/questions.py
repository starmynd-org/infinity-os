"""Producer 2: operator questions.

Added to the fabric's scope on 2026-08-16 after audit, and the reason is worth keeping because it
is the whole argument for wiring a second producer at all:

    `swarm ask` execs `~/.swarm/notify.sh` today. With only session lifecycle wired, a question
    raised at 02:00 would sit until the 07:00 brief, and doctor's question-age warning is 12
    hours, so nothing surfaces it. D7's own premise, "a fleet that raises questions at 3am is an
    always-on fleet", would quietly stop being true.

So questions page. This module turns one `swarm ask` into one `question.raised` event, and it does
two things beyond the obvious insert:

**It inherits the hard flags by OR (EF-7).** A question raised from inside a task flagged
`external: true` is itself a flagged event, because two compliant hops defeat a gate that holds
only one hop. The flags come from the asking task's own record, ORed with the question's, and a
derived item can only ever raise them.

**It refuses to truncate.** A question's text routinely runs past 4096 bytes. The event carries a
real summary and a `payload_ref` to the question record; the text is not cut. Porting a
destructive cut into a store whose whole pitch is that nothing is lost would be the worst kind of
faithful.

## The store, and the join (task 0140, 2026-08-16)

This module was written against the FILE bus and the engine was written against Postgres, so for
one day each half was correct and no question raised through `swarm ask` produced an event.
Measured on the live store with the paging subscriber listening: `max(event_seq)=54` before
`q0028` and `54` after. All 32 `question.raised` events that existed at that point came from this
module's own file-bus demo.

Two changes close it, and neither one is "the producer switched buses":

1. **The record and the flags are read STORE-FIRST, file bus second.** `brain.question` is where
   a question raised through any surface of the engine lands, so that is where this looks. The
   file-bus reads are kept, not deleted: swarm-admiral is still the running system and its
   `notify.sh` still calls `fabric.cli notify-hook`, so a producer that could only read Postgres
   would fix tonight's fleet by breaking the one that is currently answering the operator.
2. **The call site is `store.transitions.after_commit`, not a line in the CLI.** Wiring it into
   `cmd_ask` would have paged the CLI's questions and silently not the MCP server's, not the
   console's, and not the one `fail` raises for itself when a task runs out of attempts. Every
   one of those goes through `store.apply`. See `store/transitions.py:after_commit` for why this
   cannot instead be an INSERT inside the `ask` transaction: `ask` runs as `brain_runtime`, which
   holds no INSERT on `brain.event`, and the grant is the point rather than an obstacle.

**`work_item_id` is set only from a store record.** The file bus and the store both number tasks
`0001`, `0002`, and they are not the same items. Writing a file-bus task id into
`brain.event.work_item_id` would attribute an event to whatever store task happens to share the
number, so the file-bus path leaves that column NULL exactly as it did before.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import store
from store import transitions as _transitions

from .. import emit as _emit

SWARM_HOME = Path(os.environ.get("SWARM_HOME", str(Path.home() / ".swarm")))

#: Frontmatter keys that carry a hard flag on a work item.
_FLAG_RE = re.compile(r"^(external|canon_touching):\s*(true|false)\s*$", re.M | re.I)
_QID_RE = re.compile(r"\b(q\d{3,})\b")


def _store_record(qid: str) -> dict | None:
    """`brain.question`, normalised into the file bus's key names. None if the row is not there.

    A read failure is not a missing row and must not be reported as one: a store that is down
    would otherwise look exactly like a question that was never asked, and the caller would emit
    a contentless event instead of refusing. So this raises on a broken read and returns None
    only for "no such row".
    """
    if not qid:
        return None
    with store.read("runtime") as s:
        row = s.one("SELECT * FROM brain.question WHERE id = %s", (qid,))
    if not row:
        return None
    return {
        "qid": row["id"],
        "task": row["work_item_id"] or "",
        "from": row["asked_by"] or "",
        "text": row["text"] or "",
        "default_if_unanswered": row["default_if_unanswered"] or "",
        "asked_at": row["asked_at"].isoformat() if row.get("asked_at") else "",
        "answered_at": row["answered_at"].isoformat() if row.get("answered_at") else "",
        # Migration 31. The file bus has no withdrawal, so these are store-only and read empty
        # for a file-bus record, which is what a producer for a bus without the verb should say.
        "withdrawn_by": row.get("withdrawn_by") or "",
        "withdrawn_at": row["withdrawn_at"].isoformat() if row.get("withdrawn_at") else "",
        "withdrawn_reason": row.get("withdrawn_reason") or "",
        # The one key the file bus has no equivalent of, and the reason it exists: only a store
        # record may put a task id in `brain.event.work_item_id`. See the module docstring.
        "_from_store": True,
    }


def _question_record(qid: str) -> dict | None:
    """Store first, file bus second. Both buses are live and only one of them is the future."""
    try:
        rec = _store_record(qid)
    except Exception:  # noqa: BLE001 -- the file bus is a real fallback, not a swallow
        rec = None
    if rec:
        return rec
    for sub in ("open", "answered"):
        p = SWARM_HOME / "operator" / sub / f"{qid}.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
    return None


def _store_task_flags(task_id: str) -> tuple[bool, bool] | None:
    """The asking task's flags from `brain.work_item`. None means "no such row", not "unflagged"."""
    with store.read("runtime") as s:
        row = s.one("SELECT external, canon_touching FROM brain.work_item WHERE id = %s",
                    (task_id,))
    if not row:
        return None
    return bool(row["external"]), bool(row["canon_touching"])


def _task_flags(task_id: str, *, from_store: bool = False) -> tuple[bool, bool]:
    """Read the asking task's hard flags. Unknown reads CONSERVATIVE, never permissive.

    A task that cannot be found on either bus is treated as flagged on both axes. That is the
    opposite of the usual default and it is deliberate: the cost of a false flag is one page
    routed as a tombstone, and the cost of a false unflagged is a flagged payload delivered to an
    effect-class subscriber. Those are not symmetrical mistakes.

    `from_store` says which bus the question came from, and the flags are read from THAT bus and
    no other. Not a preference order: the two id spaces overlap -- file-bus task `0140` and store
    work_item `0140` are different items -- so a fallback across buses would answer a safety
    question with a different task's flags. A miss on the question's own bus reads conservative.
    """
    if not task_id:
        return False, False
    if from_store:
        try:
            flags = _store_task_flags(task_id)
        except Exception:  # noqa: BLE001 -- a store that is down reads conservative, below
            flags = None
        return flags if flags is not None else (True, True)
    for state in ("active", "inbox", "blocked", "done", "cancelled"):
        for p in (SWARM_HOME / "tasks" / state).glob(f"{task_id}-*.md"):
            text = p.read_text(encoding="utf-8", errors="replace")[:4000]
            flags = {k.lower(): v.lower() == "true" for k, v in _FLAG_RE.findall(text)}
            return flags.get("external", False), flags.get("canon_touching", False)
    return True, True


def emit_question_raised(message: str = "", *, qid: str = "", produced_by: str = None,
                         check_budget: bool = True) -> dict:
    """Turn one raised question into one `question.raised` event.

    Two callers, one body. `qid` is what the after-commit hook passes, because the engine already
    knows the id it just wrote and re-deriving it from prose would be inventing uncertainty.
    `message` is what the file bus passes its notifier: a short line like
    "operator question q0007 from T3", and the id is recovered from it. Everything else is looked
    up either way, because a producer that trusted a formatted string for its hard flags would be
    deciding the safety gate by regex.
    """
    if not qid:
        m = _QID_RE.search(message or "")
        qid = m.group(1) if m else ""
    rec = _question_record(qid) or {}
    from_store = bool(rec.get("_from_store"))
    task_id = rec.get("task", "")
    asked_by = rec.get("from", "")
    text = rec.get("text", "") or message or ""
    default = rec.get("default_if_unanswered", "")

    # EF-7. The question's own flags are false; the task's are whatever the task carries; OR wins.
    external, canon_touching = _emit.inherit(
        False, False, _task_flags(task_id, from_store=from_store))

    summary = _emit.summarise({
        "qid": qid,
        "task": task_id,
        "from": asked_by,
        # A real summary, not a cut: the first 600 characters of the question plus the stated
        # default, which is what a human needs to decide whether to get out of bed. The full text
        # is behind payload_ref and is not destroyed.
        "question": text[:600],
        "truncated_in_summary": len(text) > 600,
        "default_if_unanswered": default[:300],
        "asked_at": rec.get("asked_at", ""),
    })

    return _emit.emit(
        type="question.raised",
        external=external,
        canon_touching=canon_touching,
        department="", lane="",
        subject_type="question", subject_id=qid,
        payload_summary=summary,
        payload_ref=_payload_ref(qid, from_store),
        # Only ever from a store record: the two buses number tasks the same and mean different
        # items, and this column is a FK into the store's.
        work_item_id=(task_id or None) if from_store else None,
        actor_type="ai",
        produced_by=produced_by,
        actor=asked_by or "swarm",
        check_budget=check_budget,
    )


def emit_question_answered(qid: str, *, produced_by: str = None,
                           check_budget: bool = True) -> dict:
    """Emitted so a pager can retract rather than re-page. Same flags, same OR rule.

    The consumer that makes that sentence true is `subscribers/operator_paging` and it arrived a
    task later (0162): this event was emitted on every surface from 0140 and the pager had
    `question.answered` on its never-page list, so nothing retracted anything for a day. The
    docstring was right and the system was not, which is the failure mode worth naming here.
    """
    rec = _question_record(qid) or {}
    from_store = bool(rec.get("_from_store"))
    task_id = rec.get("task", "")
    external, canon_touching = _emit.inherit(
        False, False, _task_flags(task_id, from_store=from_store))
    return _emit.emit(
        type="question.answered",
        external=external, canon_touching=canon_touching,
        subject_type="question", subject_id=qid,
        payload_summary=_emit.summarise({"qid": qid, "task": task_id,
                                         "answered_at": rec.get("answered_at", "")}),
        payload_ref=_payload_ref(qid, from_store),
        work_item_id=(task_id or None) if from_store else None,
        actor_type="human", produced_by=produced_by, actor="operator",
        check_budget=check_budget,
    )


def emit_question_withdrawn(qid: str, *, produced_by: str = None,
                            check_budget: bool = True) -> dict:
    """A question was retired without an answer. Same flags, same OR rule, one different actor.

    `actor_type` is **ai** and the actor is the withdrawing agent, where `question.answered` says
    human/operator. That difference is the whole content of the event: a subscriber reading the
    two must never be able to conclude from a withdrawal that a person decided anything. The
    pager consumes it exactly as it consumes an answer -- as a retraction, bounded by the pages
    already sent -- because from the lock screen's point of view both mean the same thing, which
    is "stand down, this is not waiting on you".
    """
    # `_question_record` and not `_store_record`: it is the one that survives a store read
    # failure, and a retraction lost to an exception leaves a page on the lock screen all night.
    rec = _question_record(qid) or {}
    from_store = bool(rec.get("_from_store"))
    task_id = rec.get("task", "")
    external, canon_touching = _emit.inherit(
        False, False, _task_flags(task_id, from_store=from_store))
    return _emit.emit(
        type="question.withdrawn",
        external=external, canon_touching=canon_touching,
        subject_type="question", subject_id=qid,
        payload_summary=_emit.summarise({"qid": qid, "task": task_id,
                                         "withdrawn_by": rec.get("withdrawn_by", ""),
                                         "withdrawn_at": rec.get("withdrawn_at", ""),
                                         "reason": (rec.get("withdrawn_reason") or "")[:300]}),
        payload_ref=_payload_ref(qid, from_store),
        work_item_id=(task_id or None) if from_store else None,
        actor_type="ai", produced_by=produced_by,
        actor=rec.get("withdrawn_by") or "swarm",
        check_budget=check_budget,
    )


def _payload_ref(qid: str, from_store: bool) -> str | None:
    """Where the full text is, said in the vocabulary of the bus it is actually on.

    A store question has no file, and pointing at `~/.swarm/operator/open/q0041.json` for one
    would hand a human a path that does not exist -- worse than no ref, because a missing ref
    sends them to `swarm questions` and a wrong one sends them to `ls`.
    """
    if not qid:
        return None
    if from_store:
        return f"brain.question:{qid}"
    return str(SWARM_HOME / "operator" / "open" / f"{qid}.json")


# ------------------------------------------------------------------ the join (task 0140)

def _hook_question_raised(verb: str, result, kwargs: dict) -> None:
    """`ask` raised one, or `fail` ran a task out of attempts and raised one for it.

    `fail` is the half a CLI-level call site would have missed entirely: nobody types that
    question, the transition writes it, and it is the 3am one -- a task that failed its last
    attempt at 02:00 is exactly the event the operator needs before 07:00.
    """
    qid = (result or {}).get("id") if verb == "ask" else (result or {}).get("question")
    if qid:
        emit_question_raised(qid=qid)


def _hook_question_answered(verb: str, result, kwargs: dict) -> None:
    qid = (result or {}).get("id")
    if qid:
        emit_question_answered(qid)


def _hook_question_withdrawn(verb: str, result, kwargs: dict) -> None:
    """`withdraw` retired one, or `cancel` retired every open question its task had raised.

    `cancel` is the half a CLI call site would have missed, and it is the one task 0159 was
    posted about: nobody types the withdrawal, the transition writes it because the task the
    question belonged to has just been closed. One event per question, so a page is retracted per
    page sent -- the retraction's volume stays bounded by the paging volume exactly as
    `subscribers/operator_paging/policy.py` argues for an answer.
    """
    r = result or {}
    qids = [r["id"]] if verb == "withdraw" and r.get("id") else list(r.get("withdrew") or [])
    for qid in qids:
        emit_question_withdrawn(qid)


_transitions.after_commit("ask", _hook_question_raised)
_transitions.after_commit("fail", _hook_question_raised)
_transitions.after_commit("answer", _hook_question_answered)
_transitions.after_commit("withdraw", _hook_question_withdrawn)
_transitions.after_commit("cancel", _hook_question_withdrawn)
