#!/usr/bin/env python3
"""THE OPERATOR'S STOPWATCH, AT THE SURFACE HE ACTUALLY PRESSES. Task 0284, over 0279's ledger.

`queue/tests/test_time_ledger.py` proves the ledger: the cap, the overlap refusal, the
append-only rule, the coverage figure. None of that is what this suite is about. 0279 shipped
those verbs wired to the CLI ONLY, and the gap task 0284 exists to close is a console that
enumerates its own permissions from `store.registered()` and listed both verbs under
`registered_and_unreachable` -- registered, and reachable from no room.

So every check below is a sentence about the SURFACE, and each one pins a way a console can wire
a measurement and quietly break it:

  1. THE ALLOWLIST.        Reachable now, and `queue time correct` deliberately still is not.
  2. WHOSE MINUTES.        The row lands under `operator` -- the name every figure defaults to --
                           and not a session id, which is the trap the brief named. A row written
                           under any other name is in the ledger and in none of his numbers.
  3. A REFUSAL IS NOT A    Two timers at once is the verb refusing, not the console breaking.
     CRASH.                It must reach the operator as a stated refusal.
  4. THE STOP SURVIVES     The ordinary case is that the item LEFT the queue between start and
     THE CARD.             stop, because clearing it is what he was timing.
  5. THE WARNING COMES     The read writes it while the entry is open. A console that rendered it
     BEFORE THE STOP.      only afterwards would deliver it after the only moment it is usable.
  6. COVERAGE SURVIVES     Depth renders "measured over N of M" and the per-tier nulls WITH their
     RENDERING.            reasons. Hours without coverage is the totality implication the read
                           was built to refuse.
  7. THE CARDS DO NOT      A ticking number in the `list` region would rewrite every card every
     TICK.                 three seconds and destroy any panel the operator had open.

Run:  QUEUE_SCRATCH_DB=brain_t4_0284_queue BRAIN_PG_DB=brain_t4_0284_queue \
      python3 web/tests/test_stopwatch_at_the_surface.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in ("queue", "engine", "web"):
    sys.path.insert(0, str(ROOT / p))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("QUEUE_SCRATCH_DB", "brain_queue_scratch"))
os.environ.pop("SWARM_PARENT_TASK", None)

sys.path.insert(0, str(ROOT / "queue/tests"))
from _scratch_preflight import reconcile                       # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])

import store                                                   # noqa: E402
from human_queue import time_ledger as _time                   # noqa: E402,F401
from human_queue import transitions as qt                      # noqa: E402,F401
from swarm_engine import transitions as engine                 # noqa: E402,F401
from web import guard, model, rooms                            # noqa: E402
from web.app import create_app                                 # noqa: E402

SCRATCH = str(ROOT / "queue/bin/queue-scratch-db.sh")
PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")
    return ok


def su(sql: str) -> str:
    """Superuser psql, FOR SETUP ONLY -- never to prove a rule.

    It exists for one shape: an entry that has been running for five hours cannot be produced by
    waiting. Inserting one born running with a past `started_at` is what the trigger permits by
    design, so this manufactures the clock and never the verdict. Same argument, same wording, as
    `queue/tests/test_time_ledger.py`, which is where this helper comes from.
    """
    r = subprocess.run([SCRATCH, "psql", "-tAq", "-c", sql], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"psql exited {r.returncode}")
    return r.stdout.strip()


def reset():
    su("TRUNCATE brain.time_entry, brain.queue_item, brain.queue_defer, brain.queue_bump, "
       "brain.queue_calibration, brain.queue_default_event, brain.recommendation, "
       "brain.thread, brain.question, brain.agent, brain.work_item CASCADE; "
       "SELECT setval('brain.item_id_seq', 1, false);")


def _decidable_question():
    """A question the console renders in Decide, built the way a producer builds one."""
    t = store.apply("post", title="the pricing change", lane="client",
                    signals={"reversibility": "high"})
    q = store.apply("ask", question="ship the 12 percent uplift?", agent="T2", task=t["id"],
                    default="yes, ship it")
    store.apply("queue classify", source_type="question", source_id=q["id"],
                template_id="playbook-price-change")
    return t["id"], q["id"]


def _client(room="queue"):
    """A browser holding a real session and a token minted for `room`. See test_allowlist."""
    app = create_app()
    with app.test_request_context("/"):
        token, sid = guard.token_for(room), guard.session_id()
    c = app.test_client()
    c.set_cookie(guard.SESSION_COOKIE, sid)
    return app, c, token, {"Origin": "http://localhost"}


def _post(c, tok, hdr, **data):
    r = c.post("/queue/act", headers=hdr, data={"csrf": tok, **data})
    return r.status_code, r.get_json()


def _entries():
    with store.read("runtime") as s:
        return s.query("SELECT id, who, source_type, source_id, tier_at_start, ended_how, "
                       "       stopped_at, seconds FROM brain.time_entry ORDER BY id")


# ------------------------------------------------------------------ 1. the allowlist

def test_the_two_verbs_are_reachable_and_correct_is_deliberately_not():
    a = rooms.audit()
    check("`queue time start` is reachable from a room now",
          "queue time start" in a["reachable_from_any_room"], str(a["reachable_from_any_room"]))
    check("`queue time stop` is reachable from a room now",
          "queue time stop" in a["reachable_from_any_room"])
    check("NEITHER IS IN registered_and_unreachable ANY MORE, which is the gap 0284 was",
          not ({"queue time start", "queue time stop"} & set(a["registered_and_unreachable"])),
          str(a["registered_and_unreachable"]))
    check("`queue time correct` is still unreachable, ON PURPOSE: two timestamps and a reason is "
          "a CLI shape, and a verb in a room with no caller is the widening 0169 removed",
          "queue time correct" in a["registered_and_unreachable"])
    check("and Study still calls zero verbs",
          a["study_verb_count"] == 0 and not rooms.ROOM_VERBS["study"])
    for room in ("brief", "fleet", "scope", "study"):
        check(f"{room} cannot start a timer: this is the Queue's control",
              "queue time start" not in rooms.ROOM_VERBS[room])


# ------------------------------------------------------------------ 2, 3, 4. the write door

def test_the_console_starts_and_stops_a_timer_under_the_operators_own_name():
    reset()
    tid, qid = _decidable_question()
    app, c, tok, hdr = _client()

    st, j = _post(c, tok, hdr, action="time_start", id=qid, source_type="question")
    check("the start is accepted at the write door", st == 200 and j["ok"], f"{st} {j}")
    check("and the receipt names the verb that ran", j.get("verb") == "queue time start", str(j))

    rows = _entries()
    check("exactly one entry landed", len(rows) == 1, str(rows))
    # THE TRAP THE BRIEF NAMED, pinned. `who` is the name every figure defaults to, not the
    # session id the console happens to be holding. A row under any other name is in the ledger
    # and in none of his numbers, and nothing else on the surface would ever say so.
    check("IT IS UNDER `operator` AND NOT A SESSION ID",
          rows[0]["who"] == "operator", str(rows[0]))
    check("addressed as the queue addresses it: the question, not the work item",
          (rows[0]["source_type"], rows[0]["source_id"]) == ("question", str(qid)), str(rows[0]))
    check("stamped with the tier the card was in, which is what makes a ceiling measurable",
          rows[0]["tier_at_start"] == "decide", str(rows[0]))

    # 3. THE SECOND TIMER. The verb refuses; the console must say so rather than break.
    st2, j2 = _post(c, tok, hdr, action="time_start", id=tid, source_type="work_item")
    check("a second timer is REFUSED", st2 == 400 and not j2["ok"], f"{st2} {j2}")
    check("as a refusal, not as a 500 with a stack trace behind it",
          j2.get("kind") == "refused", str(j2))
    check("and the refusal names what is already running",
          str(qid) in j2["error"] and "Two timers at once are refused" in j2["error"],
          j2["error"])
    check("no second row was written", len(_entries()) == 1)

    # 4. THE ITEM LEAVES THE QUEUE, WHICH IS THE ORDINARY CASE: clearing it is what he timed.
    store.apply("answer", actor="operator", qid=qid, text="yes, ship it", requeue=False)
    check("the card is gone from the queue", model.find_item(str(qid)) is None)
    st3, j3 = _post(c, tok, hdr, action="time_stop", id=qid)
    check("THE STOP STILL LANDS: a stale card must not strand a running entry",
          st3 == 200 and j3["ok"], f"{st3} {j3}")
    rows = _entries()
    check("the entry is stopped and measured", rows[0]["ended_how"] == "stopped", str(rows[0]))
    check("the receipt says `stopped` and states the minutes",
          "stopped" in j3["receipt"] and "minutes" in j3["receipt"], j3["receipt"])

    st4, j4 = _post(c, tok, hdr, action="time_stop", id=qid)
    check("a second stop is refused in words, not as an error",
          st4 == 400 and j4.get("kind") == "refused" and "no timer running" in j4["error"],
          f"{st4} {j4}")


def test_abandon_says_what_the_row_says():
    reset()
    tid, qid = _decidable_question()
    app, c, tok, hdr = _client()
    _post(c, tok, hdr, action="time_start", id=qid, source_type="question")
    st, j = _post(c, tok, hdr, action="time_abandon", id=qid)
    check("the abandon button lands", st == 200 and j["ok"], f"{st} {j}")
    check("THE ROW SAYS ABANDONED", _entries()[0]["ended_how"] == "abandoned", str(_entries()))
    check("and so does the receipt, with what it means for the means",
          "abandoned" in j["receipt"] and "out of every mean" in j["receipt"], j["receipt"])
    check("no undo is offered, and the reason names the verb that supersedes instead",
          j.get("undo") is None, str(j))


# ------------------------------------------------------------------ 5. the warning, before

def test_the_over_cap_warning_renders_while_the_timer_is_still_running():
    """The whole argument for the strip. An operator told at STOP time that his entry was
    relabelled has already lost the chance to correct it into something true."""
    reset()
    tid, qid = _decidable_question()
    # Manufactured clock, not a manufactured verdict: born running five hours ago is a shape the
    # trigger permits, and the cap is four.
    su("INSERT INTO brain.time_entry (source_type, source_id, who, started_at, tier_at_start) "
       f"VALUES ('question', '{qid}', 'operator', now() - interval '5 hours', 'decide')")
    sw = model.stopwatch()
    check("the read reports it running", bool(sw.get("running")), str(sw)[:200])
    check("and over the cap", sw["running"]["over_cap"] is True, str(sw["running"]))
    check("with a warning written BEFORE any stop",
          "WILL be recorded as abandoned" in (sw["running"]["warning"] or ""),
          str(sw["running"]["warning"]))

    app, c, tok, hdr = _client()
    html = c.get("/queue").get_data(as_text=True)
    check("THE QUEUE PAGE RENDERS THAT WARNING, not a bare elapsed figure",
          "WILL be recorded as abandoned" in html)
    check("it names the cap in hours, so the operator can tell why",
          "OVER THE 4-HOUR CAP" in html, html[:0])
    check("the strip is marked over-cap rather than merely running",
          'class="strip timing overcap"' in html)
    check("and it offers the stop and the abandon, because both are still his to press",
          'value="time_stop"' in html and 'value="time_abandon"' in html)
    check("THE ENTRY IS STILL RUNNING: rendering a warning is not a state change",
          _entries()[0]["stopped_at"] is None, str(_entries()))

    detail = c.get(f"/task/{tid}").get_data(as_text=True)
    check("the task page carries the same warning, because that is where the minutes go",
          "WILL be recorded as abandoned" in detail)
    check("and it admits its own figure is frozen, since that page does not poll",
          "this page does not" in detail and "refresh" in detail)


def test_an_idle_strip_says_nothing_is_running_and_does_not_nag():
    reset()
    app, c, tok, hdr = _client()
    html = c.get("/queue").get_data(as_text=True)
    check("it says no timer is running", "no timer running" in html)
    check("A TIMER IS NEVER REQUIRED, and the strip says so rather than nagging",
          "an untimed item is normal" in html)
    check("nothing measured today is stated as nothing, not as a zero median",
          "nothing measured today" in html and "median None" not in html)


# ------------------------------------------------------------------ 6. coverage survives

def test_the_depth_block_renders_coverage_and_every_null_with_its_reason():
    reset()
    tid, qid = _decidable_question()
    app, c, tok, hdr = _client()
    # One measured item in Decide, and nothing in Judge or Shape. That asymmetry IS the test: the
    # tiers with no measurement must render as named gaps, never as zero and never as Decide's
    # number wearing their label.
    #
    # BORN RUNNING, THEN STOPPED THROUGH THE VERB. A row inserted already stopped is refused by
    # the trigger -- "an interval nobody actually timed is indistinguishable in the mean from one
    # somebody did" -- so the only thing manufactured here is the start time.
    su("INSERT INTO brain.time_entry (source_type, source_id, who, started_at, tier_at_start) "
       f"VALUES ('question', '{qid}', 'operator', now() - interval '10 minutes', 'decide')")
    store.apply("queue time stop", who="operator")
    d = model.depth()
    check("the read has a measurement to render", d["stopwatch"]["overall"]["n"] == 1, str(d)[:200])
    html = c.get("/queue").get_data(as_text=True)
    check("COVERAGE IS ON THE SCREEN, not just in the JSON",
          "coverage: measured over" in html or "nothing was cleared" in html)
    check("the hours figure is labelled as covering only the tiers that have a measurement",
          "hours of your time" in html and "tier(s) that have a measurement" in html)
    check("and the unmeasured tiers are NAMED with the depth they take out of that figure",
          html.count("UNMEASURED") >= 2, str(html.count("UNMEASURED")))
    check("with the reason attached to each",
          "no measured time entry started in this tier" in html)
    check("NO NULL IS RENDERED AS A NUMBER, and none is rendered as the word None",
          not re.search(r">\s*None\s*(hours|min)", html))
    check("the basis sentence travels with the numbers",
          "Median and not mean" in html or "NO OPERATOR TIME HAS BEEN MEASURED" in html)


# ------------------------------------------------------------------ 7. the cards do not tick

def test_a_running_timer_does_not_make_the_card_region_churn():
    """The defect this pins is the one console.js already measured once: a region that changes on
    the clock is swapped on every poll, and any panel the operator had open dies inside a second.
    An elapsed figure on a card would put that back, on the busiest region of the screen."""
    reset()
    tid, qid = _decidable_question()
    su("INSERT INTO brain.time_entry (source_type, source_id, who, started_at, tier_at_start) "
       f"VALUES ('question', '{qid}', 'operator', now() - interval '90 seconds', 'decide')")
    app, c, tok, hdr = _client()
    with app.test_request_context("/queue"):
        from web.app import _queue_ctx, _render_regions
        a = _render_regions("queue.html", _queue_ctx())
        b = _render_regions("queue.html", _queue_ctx())
    check("the card list is byte-identical across two renders with a timer running",
          a["list"] == b["list"], "the list region churns")
    check("the card DOES carry a stop control, so this is not passing by having no control",
          'value="time_stop"' in a["list"], a["list"][:200])
    check("and the strip -- the one region allowed to change on the clock -- carries the minutes",
          "min" in a["stopwatch"] and "timing" in a["stopwatch"])
    check("the depth block is NOT in the patch payload: it is the console's most expensive read "
          "and it must not run twenty times a minute",
          "hours of your time" not in "".join(a.values()))


def main():
    for fn in (test_the_two_verbs_are_reachable_and_correct_is_deliberately_not,
               test_the_console_starts_and_stops_a_timer_under_the_operators_own_name,
               test_abandon_says_what_the_row_says,
               test_the_over_cap_warning_renders_while_the_timer_is_still_running,
               test_an_idle_strip_says_nothing_is_running_and_does_not_nag,
               test_the_depth_block_renders_coverage_and_every_null_with_its_reason,
               test_a_running_timer_does_not_make_the_card_region_churn):
        print(f"=== {fn.__name__} ===")
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
