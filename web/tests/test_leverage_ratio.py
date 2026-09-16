#!/usr/bin/env python3
"""THE LEVERAGE RATIO, AND THE COVERAGE IT IS NOT ALLOWED TO BE PRINTED WITHOUT. Task 0290.

The Brief's header said *Operator minutes are measured nowhere in migration 1, so the leverage
ratio has one real half.* That was true when D7 wrote it and migration 23 (`brain.time_entry`,
task 0279) made it false. A stated absence is a claim about the store, and this suite exists
because such a claim goes stale exactly like a number does -- on the one screen whose pitch is
double-entry accountability, printing a stale absence is the same class of lie as printing an
illustrative figure.

Turning the absence into a number is the easy half and it is the dangerous one. Fleet time is
TOTAL: every run is in `brain.run` and nothing asks an agent to opt in. Operator minutes are a
SUBSET: a timer is never required and an untimed item is normal, which migration 23 states as a
policy rather than a shortfall. A total over a subset is inflated by exactly however undisciplined
he was about pressing start, and this is the most quotable figure on the surface. So every scene
below is about one of the ways that goes wrong:

  1. THE STALE SENTENCE.   Gone from the model AND from the rendered page, with both halves
                           measured and the arithmetic reproducible from the two numbers shown.
  2. NEVER BARE.           Partial coverage renders AS partial: "1 of 3", 33%, in the same element
                           as the ratio. A ratio the reader can quote without the coverage is the
                           whole defect and not a formatting preference.
  3. NULL, NOT ZERO.       An untimed window has no denominator. Not zero minutes, not an infinite
                           ratio, not a silently omitted line: a named absence.
  4. ONE WINDOW.           The Brief states 12 hours in its own header, so the operator half is
                           measured over 12 hours too. An entry that stopped 19.5h ago is in the
                           ledger, is visible to a 24h read, and is NOT in this ratio.
  5. WHAT IT DID NOT SEE.  Abandoned, superseded and still-running entries are excluded from the
                           denominator -- every one of them inflating the ratio -- so each is named
                           on the surface rather than subtracted from it.
  6. THE COST.             The Brief is re-rendered on the three-second poll. `depth_and_clearance`
                           is the console's most expensive read and `web/app.py:_queue_ctx` keeps
                           it off that path on purpose; this ratio must not put it back.
  7. NO CHURN.             The Brief is ONE polled region covering the whole page. This scene
                           FAILED first: the running timer's elapsed minutes were interpolated into
                           the coverage sentence, so two renders seven seconds apart differed and
                           every form on the screen would have been swapped twenty times a minute.

Run:  QUEUE_SCRATCH_DB=brain_t5_0290 BRAIN_PG_DB=brain_t5_0290 \
      python3 web/tests/test_leverage_ratio.py
"""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
import sys
import time
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
from human_queue import reads as Q                             # noqa: E402
from human_queue import time_ledger as _time                    # noqa: E402,F401
from human_queue import transitions as qt                       # noqa: E402,F401
from swarm_engine import transitions as engine                  # noqa: E402,F401
from web import guard, model                                    # noqa: E402
from web.app import create_app                                  # noqa: E402

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

    Two shapes here need it and neither is a verdict. An entry that has been running for five
    hours cannot be produced by waiting, and `brain.run` rows of a known duration cannot be
    produced by `run start`/`run end`, which stamp `now()` at both ends and leave sub-second rows
    -- the exact reason a factor-of-two defect in `_because_of_you` rendered as `0 agent minutes`
    and went unseen. This manufactures the clock and never the label: every stop below goes
    through the verb, so the cap and the append-only trigger decide what the rows say.
    """
    r = subprocess.run([SCRATCH, "psql", "-tAq", "-c", sql], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"psql exited {r.returncode}")
    return r.stdout.strip()


def reset():
    su("TRUNCATE brain.time_entry, brain.queue_item, brain.queue_defer, brain.queue_bump, "
       "brain.queue_calibration, brain.queue_default_event, brain.recommendation, brain.run, "
       "brain.artifact, brain.thread, brain.question, brain.agent, brain.work_item CASCADE; "
       "SELECT setval('brain.item_id_seq', 1, false);")


def _question(title="the pricing change"):
    """A question the console renders in Decide, built the way a producer builds one."""
    t = store.apply("post", title=title, lane="client", signals={"reversibility": "high"})
    q = store.apply("ask", question="ship the 12 percent uplift?", agent="T2", task=t["id"],
                    default="yes, ship it")
    store.apply("queue classify", source_type="question", source_id=q["id"],
                template_id="playbook-price-change")
    return t["id"], q["id"]


AGENT_HOURS = 6
AGENT_MINUTES = AGENT_HOURS * 60


def _fleet_time(tid: str, hours: int = AGENT_HOURS):
    """One run of an exact known duration, INSIDE the Brief's 12-hour window."""
    su("INSERT INTO brain.run (work_item_id, attempt, agent, started_at, ended_at, outcome) "
       f"VALUES ('{tid}', 1, 'T-0290', now() - interval '{hours} hours', now(), 'done')")


def _timed(source_type: str, source_id: str, minutes: float, tier: str = "decide") -> None:
    """A measured entry: born running `minutes` ago, then STOPPED THROUGH THE VERB.

    A row inserted already stopped is refused by the trigger -- "an interval nobody actually timed
    is indistinguishable in the mean from one somebody did" -- so the start time is the only thing
    manufactured, and the cap still gets to decide the label.
    """
    su("INSERT INTO brain.time_entry (source_type, source_id, who, started_at, tier_at_start) "
       f"VALUES ('{source_type}', '{source_id}', '{model.OPERATOR}', "
       f"        now() - interval '{minutes} minutes', '{tier}')")
    store.apply("queue time stop", who=model.OPERATOR)


def _running(source_type: str, source_id: str, minutes: float, tier: str = "decide") -> None:
    su("INSERT INTO brain.time_entry (source_type, source_id, who, started_at, tier_at_start) "
       f"VALUES ('{source_type}', '{source_id}', '{model.OPERATOR}', "
       f"        now() - interval '{minutes} minutes', '{tier}')")


def _page() -> str:
    app = create_app()
    with app.test_request_context("/"):
        sid = guard.session_id()
    c = app.test_client()
    c.set_cookie(guard.SESSION_COOKIE, sid)
    return c.get("/brief").get_data(as_text=True)


STALE = "measured nowhere in migration 1"


# ------------------------------------------------- 1. the stale sentence, and the live arithmetic

def test_both_halves_are_measured_and_the_stale_absence_is_gone():
    reset()
    tid, qid = _question()
    _fleet_time(tid)
    _timed("question", qid, 30)
    # He cleared it, which is what he was timing. `answered_at` is what puts the item in the
    # coverage denominator, so this is not decoration.
    store.apply("answer", actor=model.OPERATOR, qid=qid, text="yes, ship it", requeue=False)

    b = model.brief()
    lev = b["leverage"]
    check("THE STALE SENTENCE IS GONE from the model", STALE not in b["ratio_note"],
          b["ratio_note"][:160])
    check("the operator half is a measured number and not null",
          b["operator_minutes"] is not None and 29.0 <= b["operator_minutes"] <= 31.5,
          str(b["operator_minutes"]))
    check("the fleet half is unchanged and still measured from run rows",
          b["agent_hours"] == float(AGENT_HOURS), str(b["agent_hours"]))
    # THE RATIO IS ARITHMETIC OVER THE TWO NUMBERS ON THE SCREEN, reproducible by a reader. If it
    # is not, the figure came from somewhere the surface did not show.
    expect = AGENT_MINUTES / b["operator_minutes"]
    check("the ratio is fleet minutes over HIS minutes, over the same window",
          abs(lev["ratio"] - expect) < 0.15, f"{lev['ratio']} vs {expect:.2f}")
    check("its window is the Brief's own 12 hours, on both halves",
          lev["window_hours"] == b["window_hours"] == 12, str(lev["window_hours"]))
    check("and it is his minutes: the same `who` the console writes under",
          lev["who"] == model.OPERATOR, lev["who"])

    html = _page()
    check("THE PAGE DOES NOT PRINT THE STALE SENTENCE EITHER", STALE not in html)
    check("the ratio is on the page", f"{lev['ratio']}x" in html, f"looking for {lev['ratio']}x")
    check("with the coverage of its denominator in the same element",
          "of what you cleared was timed" in html)
    check("and the note says which way the number is wrong, not merely that it might be",
          "inflated by however much of your time went unmeasured" in b["ratio_note"],
          b["ratio_note"][:200])


# ------------------------------------------------------------------ 2. never bare

def test_partial_coverage_renders_as_partial():
    """Three items cleared, one timed. The ratio is built from that one and must say so."""
    reset()
    tid, qid = _question("priced work, timed")
    t2, q2 = _question("priced work, untimed A")
    t3, q3 = _question("priced work, untimed B")
    _fleet_time(tid)
    _timed("question", qid, 20)
    for q in (qid, q2, q3):
        store.apply("answer", actor=model.OPERATOR, qid=q, text="yes, ship it", requeue=False)

    b = model.brief()
    cov = b["leverage"]["coverage"]
    check("coverage counts every item he cleared, not just the timed ones",
          (cov["measured"], cov["cleared"]) == (1, 3), str(cov))
    check("the rate is a number as well as a sentence", abs(cov["rate"] - 1 / 3) < 0.01, str(cov))
    check("THE COVERAGE TRAVELS INSIDE THE NOTE, so a template cannot render the figure and drop "
          "the caveat", "measured over 1 of 3 item(s) cleared in 12h" in b["ratio_note"],
          b["ratio_note"][:240])
    check("and it says coverage over items is not coverage over minutes",
          "not coverage over minutes" in b["ratio_note"], b["ratio_note"][-200:])

    html = _page()
    check("the page renders the percentage next to the ratio", "33% of what you cleared was timed"
          in html, [ln for ln in html.splitlines() if "cleared was timed" in ln][:2])
    check("the denominator is shown as the subset it is: minutes AND the items they came from",
          "1 timed item" in html, [ln for ln in html.splitlines() if "timed item" in ln][:2])


# ------------------------------------------------------- 3. an untimed window is null, not zero

def test_no_measurement_is_an_absence_and_never_a_zero_or_an_infinity():
    reset()
    tid, qid = _question()
    _fleet_time(tid)
    store.apply("answer", actor=model.OPERATOR, qid=qid, text="yes, ship it", requeue=False)

    b = model.brief()
    check("operator minutes are NULL and not 0.0", b["operator_minutes"] is None,
          str(b["operator_minutes"]))
    check("there is no ratio", b["leverage"]["ratio"] is None, str(b["leverage"]["ratio"]))
    check("the note names the absence and says the ledger now EXISTS -- which is the difference "
          "between this sentence and the one it replaced",
          "No operator minutes were measured in the last 12h" in b["ratio_note"]
          and "migration 23" in b["ratio_note"], b["ratio_note"][:220])
    check("it does not claim the store cannot hold them", STALE not in b["ratio_note"])

    html = _page()
    check("the page says not measured in this window", "not measured in this window" in html)
    # NO RATIO ELEMENT AT ALL, which is stronger than "no zero on the page": the figure and its
    # coverage are one element by design, so the absence of that element is the absence of both.
    check("NO RATIO ELEMENT AND NO ZERO RATIO reached the page",
          'class="lev"' not in html and "0.0x" not in html,
          [ln.strip() for ln in html.splitlines() if "lev" in ln or "0.0x" in ln][:3])


# ---------------------------------------------------------------------- 4. one window, stated

def test_an_entry_outside_the_twelve_hours_is_not_in_the_ratio():
    """THE WINDOW DECISION, PINNED. The reads take `days`; this ratio is stated over the Brief's
    own 12 hours, so a real measured entry from 19.5 hours ago belongs to a 24h read and not to
    this figure. A ratio whose halves cover different spans is not a ratio."""
    reset()
    tid, qid = _question()
    _fleet_time(tid)
    _timed("question", qid, 10)
    store.apply("answer", actor=model.OPERATOR, qid=qid, text="yes, ship it", requeue=False)
    entry = int(su("SELECT id FROM brain.time_entry ORDER BY id DESC LIMIT 1"))
    # `queue time correct` is the sanctioned way to record an interval retroactively: a new row
    # that supersedes the original, both surviving. It is how a 19.5-hour-old measurement gets
    # into this ledger without disabling the trigger that would refuse a bare stopped INSERT.
    now = _dt.datetime.now(_dt.timezone.utc)
    store.apply("queue time correct", entry_id=entry,
                started_at=now - _dt.timedelta(hours=20),
                stopped_at=now - _dt.timedelta(hours=19, minutes=30),
                reason="recording the interval actually worked, for task 0290's window test",
                who=model.OPERATOR)

    day = Q.operator_minutes(hours=24, who=model.OPERATOR)
    check("the entry IS in the ledger and a 24h read sees all 30 minutes of it",
          abs(day["measured_minutes"] - 30.0) < 0.5, str(day["measured_minutes"]))
    b = model.brief()
    check("AND THE 12h RATIO DOES NOT COUNT IT", b["leverage"]["ratio"] is None
          and b["operator_minutes"] is None, str(b["leverage"]))
    check("the note states the window it is absent over", "in the last 12h" in b["ratio_note"],
          b["ratio_note"][:160])
    check("the superseded original is named as excluded rather than silently dropped",
          any("superseded" in x for x in b["leverage"]["excluded"]),
          str(b["leverage"]["excluded"]))


# ------------------------------------------------------- 5. what the denominator did not see

def test_every_excluded_class_is_named_because_each_one_inflates_the_ratio():
    reset()
    tid, qid = _question()
    _fleet_time(tid)
    # A forgotten timer: five hours is past the 4h cap, so the VERB relabels it `abandoned`. Those
    # minutes are real time out of every mean, and leaving them unmentioned makes the ratio look
    # better than the evidence.
    _timed("work_item", tid, 300, tier="judge")
    _timed("question", qid, 40)
    store.apply("answer", actor=model.OPERATOR, qid=qid, text="yes, ship it", requeue=False)
    # And a timer running right now, which is not a measurement until it stops.
    _running("work_item", tid, 7, tier="judge")

    om = Q.operator_minutes(hours=12, who=model.OPERATOR)
    check("the abandoned entry is in the ledger", om["entries"]["abandoned"] == 1,
          str(om["entries"]))
    check("a timer is running", om["entries"]["running"] == 1, str(om["entries"]))
    check("THE DENOMINATOR IS THE MEASURED 40 MINUTES ONLY -- not the 300 abandoned, not the 7 "
          "still running", abs(om["measured_minutes"] - 40.0) < 1.0,
          str(om["measured_minutes"]))

    b = model.brief()
    check("the ratio uses that denominator",
          abs(b["leverage"]["ratio"] - AGENT_MINUTES / 40.0) < 0.4, str(b["leverage"]["ratio"]))
    note = b["ratio_note"]
    check("the abandoned entry is NAMED on the surface, with what it is",
          "abandoned entry" in note and "out of every mean" in note, note[:280])
    check("so is the running one", "a timer is running now" in note, note[:280])
    # ITS ELAPSED IS NOT IN THE SENTENCE, and scene 7 is why. The number is still in the read for
    # a surface that owns a ticking region; the Brief does not.
    check("but its elapsed minutes are NOT interpolated into the printed sentence",
          "elapsed" not in note, note[:280])
    check("while the read still carries the figure for a caller that can render it",
          om["running_minutes"] is not None and om["running_minutes"] > 0,
          str(om["running_minutes"]))
    check("and the cap is named from the database rather than from a Python copy of it",
          "4h cap" in note, note[:280])
    check("the page carries the same sentence", "out of every mean" in _page())


# ---------------------------------------------------------------------------- 6. the cost

def test_the_brief_does_not_reach_for_the_consoles_most_expensive_read():
    """`web/app.py:_queue_ctx` keeps `depth_and_clearance` off the three-second poll deliberately:
    it reads every open queue item plus four windows of history. The Brief IS re-rendered on that
    poll, so a leverage ratio built on that read would put the console's heaviest query on its
    most frequent path -- twenty times a minute, for one figure."""
    reset()
    tid, qid = _question()
    _fleet_time(tid)
    _timed("question", qid, 15)
    store.apply("answer", actor=model.OPERATOR, qid=qid, text="yes, ship it", requeue=False)

    calls = {"depth": 0, "queue": 0}
    real_depth, real_queue = Q.depth_and_clearance, Q.queue

    def boom_depth(*a, **k):
        calls["depth"] += 1
        raise AssertionError("brief() called depth_and_clearance")

    def boom_queue(*a, **k):
        calls["queue"] += 1
        raise AssertionError("brief() read the whole queue")

    Q.depth_and_clearance, Q.queue = boom_depth, boom_queue
    try:
        b = model.brief()
        ok = b["leverage"]["ratio"] is not None
    finally:
        Q.depth_and_clearance, Q.queue = real_depth, real_queue
    check("the ratio is produced without depth_and_clearance and without a full queue read",
          ok and calls == {"depth": 0, "queue": 0}, str(calls))


# ------------------------------------------------------- 7. the page does not churn on the clock

def test_the_brief_region_is_byte_identical_across_two_polls_with_a_timer_running():
    """MEASURED WHILE WRITING THIS FILE, and it failed first. The Brief is ONE region covering the
    whole page, so anything in it that moves on the clock rather than on an event swaps every form
    on the screen twenty times a minute -- including the Defaulted reopen buttons. With the running
    timer's elapsed minutes interpolated into the coverage sentence, two renders seven seconds
    apart differed by exactly that figure. `console.js` measured the same defect once on the Defer
    panel, and the Queue room's ticking strip is a separate region for the same reason."""
    reset()
    tid, qid = _question()
    _fleet_time(tid)
    _timed("question", qid, 25)
    store.apply("answer", actor=model.OPERATOR, qid=qid, text="yes, ship it", requeue=False)
    _running("work_item", tid, 3, tier="judge")

    app = create_app()
    with app.test_request_context("/brief"):
        from web.app import _render_regions
        ctx = {"room": "brief", "receipts": [], "q": model.queue_items()}
        a = _render_regions("brief.html", dict(ctx, brief=model.brief()))
        time.sleep(7)
        b = _render_regions("brief.html", dict(ctx, brief=model.brief()))
    check("the ratio IS rendered, so this is not passing by rendering nothing",
          'class="lev"' in a["brief"], a["brief"][:0])
    check("THE WHOLE BRIEF REGION IS BYTE-IDENTICAL 7 SECONDS APART with a timer running",
          a["brief"] == b["brief"],
          next((f"{x[:150]!r}" for x, y in zip(a["brief"].splitlines(), b["brief"].splitlines())
                if x != y), "lengths differ"))


def main():
    for fn in (test_both_halves_are_measured_and_the_stale_absence_is_gone,
               test_partial_coverage_renders_as_partial,
               test_no_measurement_is_an_absence_and_never_a_zero_or_an_infinity,
               test_an_entry_outside_the_twelve_hours_is_not_in_the_ratio,
               test_every_excluded_class_is_named_because_each_one_inflates_the_ratio,
               test_the_brief_does_not_reach_for_the_consoles_most_expensive_read,
               test_the_brief_region_is_byte_identical_across_two_polls_with_a_timer_running):
        print(f"=== {fn.__name__} ===")
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
