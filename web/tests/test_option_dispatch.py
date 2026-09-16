#!/usr/bin/env python3
"""THE NAMED TEST FOR V3: a drafted option cannot dispatch without a human choosing it.

    `test_a_drafted_option_cannot_dispatch_without_a_human`

An option that drafts itself is an agent proposing its own work, and the whole risk of this
component is that the drafting agent ends up executing its own proposal. The runtime already had
the answer and this lane routed through it rather than around it: **an option IS a
`brain.recommendation`**, and `recommend accept` is the single place `requires_human` is enforced
-- an empty decider, a decider registered in `brain.agent`, and a process running under the fleet
runner are each refused there, and `brain.recommendation_human_decider` refuses the first two
again at the table.

So this file is not a second enforcement point. It proves that the OPTION LAYER reaches executed
work through that verb and through nothing else, which is the claim
`queue/tests/test_no_self_execution.py` cannot make on its own because it predates the option
layer and knows nothing about it.

The other five tests are V3's definition of done, in order:

    two to four options render, each with its counterargument         (test 2, test 6)
    choosing one dispatches real work, end to end                     (test 3)
    the fast/deep choice selects a REAL model and effort              (test 4)
    an option is never also a queue card of its own                   (test 5)
    `done` is still refused on a row an agent will report on          (test 7)

Run:  QUEUE_SCRATCH_DB=brain_scratch_t4_0165 BRAIN_PG_DB=brain_scratch_t4_0165 \
      python3 web/tests/test_option_dispatch.py
      (against a scratch database, never `brain`)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in ("queue", "engine"):
    sys.path.insert(0, str(ROOT / _p))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("QUEUE_SCRATCH_DB", "brain_queue_scratch"))
# The live runner exports this into every terminal. Popped so the tests below can post roots and
# act as the console; test 1d puts it BACK, in a subprocess, and proves the refusal it causes.
os.environ.pop("SWARM_PARENT_TASK", None)

sys.path.insert(0, str(ROOT / "queue/tests"))
from _scratch_preflight import reconcile                              # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])

import store                                                          # noqa: E402
import human_queue                                                    # noqa: E402,F401
from human_queue import options as QO                                 # noqa: E402
from swarm_engine import transitions as engine                        # noqa: E402,F401
from web import actions as web_actions, model as web_model, rooms as web_rooms   # noqa: E402

SCRATCH = str(ROOT / "queue/bin/queue-scratch-db.sh")
PASS, FAIL = 0, 0
LANE_FAST, LANE_DEEP = "design", "engine"
WORKDIR = str(ROOT)


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")
    return ok


def refused(fn, *a, **kw) -> str:
    try:
        fn(*a, **kw)
        return ""
    except Exception as e:                                            # noqa: BLE001
        return str(e) or e.__class__.__name__


def su(sql: str) -> str:
    """Superuser psql, FOR SETUP AND FOR READING THE TABLE'S OWN REFUSALS. Never to prove a verb."""
    r = subprocess.run([SCRATCH, "psql", "-tAq", "-c", sql], capture_output=True, text=True,
                       env={**os.environ, "QUEUE_SCRATCH_DB": os.environ["BRAIN_PG_DB"]})
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"psql exited {r.returncode}")
    return r.stdout.strip()


def su_refused(sql: str) -> str:
    r = subprocess.run([SCRATCH, "psql", "-tAq", "-c", sql], capture_output=True, text=True,
                       env={**os.environ, "QUEUE_SCRATCH_DB": os.environ["BRAIN_PG_DB"]})
    return "" if r.returncode == 0 else r.stderr.strip()


def reset():
    su("TRUNCATE brain.queue_item_option, brain.queue_item, brain.queue_defer, brain.queue_bump, "
       "brain.queue_calibration, brain.queue_default_event, brain.recommendation, brain.thread, "
       "brain.question, brain.agent, brain.work_item CASCADE; "
       "SELECT setval('brain.item_id_seq', 1, false);")
    web_model.invalidate_options()


def rows(sql, params=None):
    with store.read("runtime") as s:
        return s.query(sql, params)


def a_reviewable_fleet_task(title="Restate the ledger and check it against the export") -> str:
    """A finished fleet task waiting on acceptance: `brain.queue_open` arm 1, agent_claimable.

    This shape is chosen on purpose and it is the shape the DoD's "dispatches real work TO THE
    FLEET" turns on. `post` refuses an agent-claimable child under a held parent, deliberately --
    a child that let itself out of the hold would launder the operator's whole subtree into the
    fleet queue one hop at a time -- so the claimability of a dispatched option is INHERITED from
    the item the option is about, and is not a flag this console gets to assert. An option on
    fleet work reaches the fleet; an option on the operator's own held row stays held, and the
    card says which, out loud, rather than working around it.
    """
    tid = store.apply("post", title=title, lane=LANE_DEEP, posted_by="T9",
                      agent_claimable=True, workdir=WORKDIR)["id"]
    store.apply("claim", agent="T-opt-worker", lanes=["*"], host="test")
    store.apply("done", id=tid, agent="T-opt-worker", summary="restated, 122,653 rows")
    return tid


def four_options(fast_lane=LANE_FAST, deep_lane=LANE_DEEP) -> list:
    return [
        {"kind": "agent-does", "label": "One agent, fast model",
         "title": "Recheck the restatement against the export",
         "plan": "One terminal re-runs the comparison and pastes the twelve monthly figures.",
         "counterargument": "A fast model reads the first mismatch as the only mismatch.",
         "who": "agent", "thinking": "fast model", "size": "one task",
         "lane": fast_lane, "cost_est": 0.40, "time_est": "~6m"},
        {"kind": "agent-deep", "label": "One agent, deep model",
         "title": "Recheck the restatement and audit the loader that produced it",
         "plan": "One terminal re-runs the comparison and then reads the loader for the class "
                 "of error the mismatch would come from.",
         "counterargument": "Forty minutes on a discrepancy that may be one rounding rule.",
         "who": "agent", "thinking": "deep model, big prompt", "size": "one task",
         "lane": deep_lane, "cost_est": 3.0, "time_est": "~40m", "recommended": True},
        {"kind": "agent-helps", "label": "Work it with you",
         "title": "Sit with the operator on the restatement discrepancy",
         "plan": "A terminal opens the two figures side by side and you decide which is right.",
         "counterargument": "It spends your fifteen minutes, which is the scarcest input here.",
         "who": "agent + you", "thinking": "fast model", "size": "one task",
         "lane": deep_lane, "cost_est": 0.60, "time_est": "your 15m"},
        {"kind": "sprint", "label": "Scope as a sprint",
         "title": "Restate every sales table and reconcile the whole window",
         "plan": "Scope the whole reconciliation: every table, the whole window, one brief.",
         "counterargument": "A sprint on a discrepancy nobody has reproduced twice.",
         "who": "fleet", "thinking": "deep model, big prompt", "size": "scoped sprint",
         "lane": deep_lane, "cost_est": 12.0, "time_est": "overnight"},
    ]


def draft_on(tid, opts=None, by="T4-drafter"):
    return store.apply("queue draft options", actor=by, source_type="work_item", source_id=tid,
                       options=opts or four_options(), drafted_by=by, by=by)


def card(tid) -> dict:
    web_model.invalidate_options()
    return web_model.find_item(tid) or {}


# --------------------------------------------------- 1. THE NAMED TEST

def test_a_drafted_option_cannot_dispatch_without_a_human():
    """Every route from a DRAFTED OPTION to executed work, tried, and each one refused.

    Seven routes. Six of them are refusals; the seventh is the shape of the console itself.
    """
    reset()
    tid = a_reviewable_fleet_task()
    before = int(su("SELECT count(*) FROM brain.work_item"))
    res = draft_on(tid)
    after = int(su("SELECT count(*) FROM brain.work_item"))

    # (a) drafting spawns nothing. The verb writes proposals and has no path to executed work.
    check("a. drafting four options spawns zero work items",
          after == before and len(res["options"]) == 4,
          f"work_item count {before} -> {after}, {len(res['options'])} options")
    check("a. and every option is a recommendation with requires_human",
          int(su("SELECT count(*) FROM brain.recommendation r JOIN brain.queue_item_option o "
                 "ON o.recommendation_id = r.id WHERE r.requires_human AND r.state = 'open'")) == 4)

    rid = res["options"][0]["recommendation"]

    # (b) an agent as the decider. The name is in `brain.agent` because it claimed a task.
    msg = refused(store.apply, "recommend accept", id=rid, by="T-opt-worker", execute=True)
    check("b. a registered agent may not accept its own drafted option",
          "registered agent" in msg, msg or "NOT REFUSED")

    # (c) no decider at all.
    msg = refused(store.apply, "recommend accept", id=rid, by="", execute=True)
    check("c. an empty decider is refused", "needs a decider" in msg, msg or "NOT REFUSED")

    # (d) the fleet runner's own environment. A subprocess, because this one popped the variable.
    code = (
        "import os,sys;"
        "os.environ['SWARM_PARENT_TASK']='0165';"
        f"sys.path[:0]=[{str(ROOT)!r},{str(ROOT / 'queue')!r},{str(ROOT / 'engine')!r}];"
        "import store, human_queue;"
        f"print(store.apply('recommend accept', id={rid}, by='operator', execute=True))")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env={**os.environ, "SWARM_PARENT_TASK": "0165"})
    check("d. a process running under the fleet runner is refused",
          r.returncode != 0 and "not the operator" in (r.stderr + r.stdout),
          (r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout) else "NO ERROR")

    # (e) around the verb entirely, straight at the table, as the superuser.
    err = su_refused(f"UPDATE brain.recommendation SET state='accepted', decided_at=now(), "
                     f"decided_by='T-opt-worker', actor_type='human' WHERE id={rid}")
    check("e. the TABLE refuses an agent decider even to a superuser",
          "registered agent" in err, err or "NOT REFUSED")
    err = su_refused(f"UPDATE brain.recommendation SET state='accepted', decided_at=now() "
                     f"WHERE id={rid}")
    check("e. and refuses an acceptance with no decider recorded",
          "no decider" in err, err or "NOT REFUSED")

    # (f) the console's own path holds no second implementation. `dispatch_option` assembles
    #     arguments and calls `rooms.dispatch`; it never reaches `store.apply` itself.
    # BOUNDED TO THE FUNCTION, not to the end of the file: `web/actions.py` is appended to by
    # other V10 lanes and a slice to EOF measures their code as if it were this one's.
    src = (ROOT / "web/actions.py").read_text()
    start = src.index("def dispatch_option(")
    nxt = src.find("\ndef ", start + 1)
    block = src[start: nxt if nxt > 0 else len(src)]
    check("f. web/actions.py::dispatch_option calls no transition directly",
          "store.apply" not in block and 'rooms.dispatch(room, "recommend accept"' in block,
          "the console has a second path to executed work")

    # (g) the drafting verb is on NO room's allowlist, so no surface can call it at all.
    reachable = set().union(*web_rooms.ROOM_VERBS.values())
    check("g. `queue draft options` is reachable from no room",
          "queue draft options" not in reachable)
    check("g. and the only option verb any room holds is `recommend accept`",
          "recommend accept" in web_rooms.ROOM_VERBS["queue"])

    # And after all seven, the proposals are still open and nothing was executed.
    check("nothing was executed by any of the seven routes",
          int(su("SELECT count(*) FROM brain.work_item")) == after
          and su(f"SELECT state FROM brain.recommendation WHERE id={rid}") == "open")


# --------------------------------------------------- 2. the options render

def test_two_to_four_options_render_each_with_its_counterargument():
    reset()
    tid = a_reviewable_fleet_task()
    draft_on(tid)
    c = card(tid)
    check("the card carries four options", c.get("options_n") == 4, str(c.get("options_n")))
    check("no option is pre-selected: the card states no selection at all",
          not any(o.get("selected") for o in c["options"]))
    check("exactly one is marked recommended",
          len([o for o in c["options"] if o["recommended"]]) == 1)
    for o in c["options"]:
        check(f"option {o['n']} ({o['kind']}) states a plan and a counterargument",
              bool(o["plan"]) and bool(o["counter"]) and not o["counter_missing"])
        check(f"option {o['n']} states cost and time on the rail",
              bool(o["cost"]) and bool(o["time"]), f"{o['cost']} · {o['time']}")
    check("the floor is two: a set of one is refused",
          "not a choice" in refused(draft_on, a_reviewable_fleet_task("second"),
                                    [four_options()[0]]))
    check("and the ceiling is four: a set of five is refused",
          "is a form" in refused(draft_on, a_reviewable_fleet_task("third"),
                                 four_options() + [four_options()[0]]))
    html = _render_card(tid)
    for o in c["options"]:
        check(f"option {o['n']}'s counterargument is IN the markup",
              o["counter"][:40] in html, "rendered without its counterargument")
    check("the rail is a radiogroup and no radio is checked in the served markup",
          'role="radiogroup"' in html and "checked" not in html)
    check("the disabled primary says why", "Pick an option first" in html)


def _render_card(tid) -> str:
    from web.app import create_app
    app = create_app()
    with app.test_request_context("/queue"):
        from flask import render_template_string
        from web import guard
        c = card(tid)
        return render_template_string(
            "{% import 'macros.html' as m %}{{ m.options_slot('queue', item) }}",
            item=c, csrf_for=lambda room: guard.token_for(room))


# --------------------------------------------------- 3. one real dispatch, end to end

def test_choosing_one_dispatches_real_work():
    """The operator chooses option 2 and a task exists that a terminal then actually claims."""
    reset()
    tid = a_reviewable_fleet_task()
    draft_on(tid)
    c = card(tid)
    out = web_actions.dispatch_option("queue", item=c, n=2, operator="operator")
    task = out["task"]
    check("a work item was posted", bool(task), str(out))
    w = rows("SELECT * FROM brain.work_item WHERE id = %s", (task,))[0]
    check("it is the option's lane", w["lane"] == LANE_DEEP, w["lane"])
    check("it carries the subject's workdir, inherited", w["workdir"] == WORKDIR, w["workdir"])
    check("the fleet may claim it, inherited from the item the option is about",
          w["agent_claimable"] is True, str(w["agent_claimable"]))
    check("its parent is the item the option was drafted on", w["parent"] == tid, str(w["parent"]))
    check("the acceptance names a human decider",
          su(f"SELECT decided_by || '/' || actor_type FROM brain.recommendation "
             f"WHERE spawned_work_item = '{task}'") == "operator/human")
    check("the receipt names the task and the engine that will run it",
          task in out["receipt"] and "effort" in out["receipt"], out["receipt"])
    check("and it offers no undo, with the reason",
          out["undo"] is None and "cancel" in out["no_undo_reason"], str(out["no_undo_reason"]))

    # END TO END: a terminal claims it. This is the half that makes it "to the fleet".
    claimed = store.apply("claim", agent="T-opt-claimer", lanes=[LANE_DEEP], host="test")
    check("a terminal claims the dispatched task",
          (claimed or {}).get("id") == task, str(claimed))

    # And the same option cannot be dispatched twice.
    c2 = card(tid)
    check("the card now states what was chosen rather than offering the choice again",
          (c2.get("option_dispatched") or {}).get("task") == task, str(c2.get("option_dispatched")))
    check("dispatching it a second time is refused",
          "already dispatched" in refused(web_actions.dispatch_option, "queue", item=c2, n=2,
                                          operator="operator"))


# --------------------------------------------------- 4. fast versus deep is a real model

def test_the_fast_and_deep_choice_selects_a_real_model_and_effort():
    """The option carries a LANE; the model and the effort are READ BACK from the fleet config.

    Not a stored model name. `model`, `effort` and `engine` are per-agent config, `swarm claim`
    filters candidates on `lane`, and `engine/bin/swarm-run` reads `CFG_MODEL` and `CFG_EFFORT`
    off the claiming profile. One copy of the value, so the card cannot state a model no run used.

    TWO HALVES, AND THE SECOND ONE IS THE MEASUREMENT THAT MATTERS TONIGHT. The first proves the
    resolution: against a config whose lanes carry different profiles, two options resolve to two
    different models and efforts. The second reads THIS HOST'S LIVE FLEET CONFIG and finds that
    every profile carries `lanes: ["*"]`, so every lane resolves to the same claude/opus run --
    and the card is required to SAY so rather than to imply a choice the runner will not make.
    """
    import json
    import tempfile
    from swarm_engine import config as cfg

    fixture = {"fleet": "swarm", "agents": [
        {"name": "P", "role": "admiral", "lanes": [], "engine": "claude", "model": "opus"},
        {"name": "F1", "role": "terminal", "lanes": ["fast"], "engine": "claude",
         "model": "haiku", "effort": ""},
        {"name": "S1", "role": "terminal", "lanes": ["slow"], "engine": "claude",
         "model": "opus", "effort": "high"}]}
    fast = web_model.lane_engine("fast", fixture)
    deep = web_model.lane_engine("slow", fixture)
    check("the fast lane resolves to its own profile, model and effort",
          fast == {"agents": ["F1"], "engine": "claude", "model": "haiku",
                   "effort": "engine default", "why": ""}, str(fast))
    check("the deep lane resolves to a DIFFERENT model and effort",
          deep["model"] == "opus" and deep["effort"] == "high"
          and deep["model"] != fast["model"], str(deep))
    check("a planner is never a claimant: it claims nothing, by contract rule 5",
          "P" not in fast["agents"] + deep["agents"])
    check("a lane no profile claims is an honest absence, not an invented model",
          web_model.lane_engine("nobody-claims-this", fixture)["model"] is None
          and "no agent" in web_model.lane_engine("nobody-claims-this", fixture)["why"])
    check("and an option with no lane says which profile is unknown rather than guessing",
          web_model.lane_engine("", fixture)["model"] is None)

    # THE LIVE FLEET, re-measured now rather than taken from any brief.
    live = cfg.config()
    lanes = {a.get("name"): a.get("lanes") for a in live.get("agents", [])
             if not cfg.is_planner(a.get("role", ""))}
    here_fast = web_model.lane_engine(LANE_FAST)
    here_deep = web_model.lane_engine(LANE_DEEP)
    same = (here_fast["engine"], here_fast["model"], here_fast["effort"]) == \
           (here_deep["engine"], here_deep["model"], here_deep["effort"])
    print(f"    live fleet config {cfg.config_path()}: {lanes}")
    print(f"    lane {LANE_FAST!r} -> {here_fast['engine']} {here_fast['model']} "
          f"effort {here_fast['effort']} ({', '.join(here_fast['agents'])})")
    print(f"    lane {LANE_DEEP!r} -> {here_deep['engine']} {here_deep['model']} "
          f"effort {here_deep['effort']} ({', '.join(here_deep['agents'])})")
    check("every live profile's model and effort are read from config, never invented",
          all(web_model.lane_engine(l or "x", live)["model"] is not None
              for l in ("engine", "design", "queue")))

    reset()
    tid = a_reviewable_fleet_task()
    draft_on(tid)
    c = card(tid)
    o = [x for x in c["options"] if x["kind"] == "agent-does"][0]
    check("the commitment line names the model, the effort and the profiles that would run it",
          o["engine"]["model"] in o["commitment"] and "effort" in o["commitment"]
          and o["engine"]["agents"][0] in o["commitment"], o["commitment"])
    if same:
        check("THE FLEET COLLAPSES FAST AND DEEP TONIGHT, and the card states it rather than "
              "implying a choice the runner will not make",
              bool(c.get("options_same_engine")) and "same run" in _render_card(tid),
              "the card offered a fast/deep choice this fleet will not honour")
        print(f"    finding on the card: {c['options_same_engine']}")
    else:
        check("the fleet distinguishes the two lanes, so no same-run finding is raised",
              not c.get("options_same_engine"), str(c.get("options_same_engine")))


# --------------------------------------------------- 5. an option is not also a card

def test_a_drafted_option_is_not_also_a_queue_card_of_its_own():
    reset()
    tid = a_reviewable_fleet_task()
    before = int(su("SELECT count(*) FROM brain.queue_open"))
    draft_on(tid)
    after = int(su("SELECT count(*) FROM brain.queue_open"))
    check("four drafted options add zero cards to the queue", after == before,
          f"{before} -> {after}: the queue flooded itself with the component built to keep it small")
    check("an ordinary open recommendation still gets its card",
          int(su("SELECT count(*) FROM brain.queue_open")) + 1 == _with_a_plain_recommendation())


def _with_a_plain_recommendation() -> int:
    store.apply("recommend", text="Rotate both npm tokens", rationale="They are in a transcript",
                subject_type="", subject_id="", produced_by="T9")
    return int(su("SELECT count(*) FROM brain.queue_open"))


# --------------------------------------------------- 6. never an option without its counterargument

def test_an_option_with_no_counterargument_renders_the_finding_and_stays_dispatchable():
    """C section 1.6c, and the posture is the console's shipped one.

    The verb stays enabled deliberately. Blocking dispatch on a missing counterargument teaches
    drafting agents to write filler to unblock, and filler in that slot is worse than a finding
    because it reads like an argument.
    """
    reset()
    tid = a_reviewable_fleet_task()
    opts = four_options()[:2]
    opts[0]["counterargument"] = ""
    res = draft_on(tid, opts)
    check("the draft is NOT refused for the missing counterargument",
          res["without_counterargument"] == [1], str(res["without_counterargument"]))
    c = card(tid)
    o = c["options"][0]
    check("the card marks the option", o["counter_missing"] and o["counter"] is None)
    html = _render_card(tid)
    check("the rail row carries the word", "⚠ no counterargument" in html)
    check("and the finding sits in the counterargument's exact slot",
          "has not been argued, it has been advertised" in html)
    check("the verb is still there", "Dispatch it" in html)
    out = web_actions.dispatch_option("queue", item=c, n=1, operator="operator")
    check("and it dispatches", bool(out["task"]), str(out))


# --------------------------------------------------- 7. the prohibition that survives

def test_done_is_still_refused_on_a_row_an_agent_will_report_on():
    """The overrule is bounded. The console may now CREATE work; it still may not fabricate a
    REPORT, and `done` is where that was always enforced."""
    reset()
    tid = a_reviewable_fleet_task()
    draft_on(tid)
    c = card(tid)
    out = web_actions.dispatch_option("queue", item=c, n=1, operator="operator")
    task = out["task"]
    msg = refused(web_rooms.assert_allowed_on, "queue", "done",
                  {"id": task, "kind": "review", "gates": []}, acting_on=task)
    check("`done` is refused on the task the dispatch just created",
          "agent_claimable=True" in msg and "fabricate that agent's report" in msg,
          msg or "NOT REFUSED")
    print("\n    THE REFUSAL, VERBATIM:\n    " + "\n    ".join(msg.split(". ")))
    check("`accept work` is what the refusal names instead", "accept work" in msg)


if __name__ == "__main__":
    print(f"V3: the action layer  ({os.environ['BRAIN_PG_DB']})")
    print("\n-- THE NAMED TEST: a drafted option cannot dispatch without a human choosing it")
    test_a_drafted_option_cannot_dispatch_without_a_human()
    print("\n-- two to four options, each with its counterargument")
    test_two_to_four_options_render_each_with_its_counterargument()
    print("\n-- choosing one dispatches real work, end to end")
    test_choosing_one_dispatches_real_work()
    print("\n-- fast versus deep selects a real model and a real effort")
    test_the_fast_and_deep_choice_selects_a_real_model_and_effort()
    print("\n-- an option is rendered on its item's card and is not a card of its own")
    test_a_drafted_option_is_not_also_a_queue_card_of_its_own()
    print("\n-- never an option without its counterargument")
    test_an_option_with_no_counterargument_renders_the_finding_and_stays_dispatchable()
    print("\n-- and `done` is still refused on a row an agent will report on")
    test_done_is_still_refused_on_a_row_an_agent_will_report_on()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
