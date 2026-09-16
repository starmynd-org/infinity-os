#!/usr/bin/env python3
"""The runfeed against a REAL engine stream, growing while the browser watches. Task 0166.

The browser suite beside this one drives the whole feature and writes its own stream file, which
proves the behaviour and not the reading: a file this lane wrote is a file shaped the way this
lane expects. So this one points a run row at a stream the LIVE FLEET is writing right now --
`~/.brain-runtime/runs/<task>-attempt<N>.stream.jsonl`, produced by `engine/bin/swarm-run`'s
formatter from a real Claude Code session -- and renders that.

READ ONLY, AND ON PURPOSE. The stream file is opened and never written. The rows that make it
renderable (an agent, a work item, a run) are created in a SCRATCH database, and the steering
session runs there too. Nothing here touches the live store, the live fleet's tasks, or another
lane's work: a steering mark on a live task would exclude somebody else's run from the auto-accept
measurement to make a screenshot, which is the measurement corruption this lane exists to prevent.

WHAT IT THEREFORE DOES AND DOES NOT PROVE. It proves the reader, the folds, the vitals, the cost
and the live tool line against real engine output at real size, and it proves a steering session
end to end with both directions on the thread. It does not prove that the operator seized a live
terminal -- only the operator can do that, because the verb refuses any connection the database
does not know as a human, and an agent doing it in his name would be the forgery this system
refuses everywhere else.

    ENGINE_SCRATCH_DB=brain_t1_0166 BRAIN_PG_DB=brain_t1_0166 \\
      python3 web/tests/test_runfeed_live_stream.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "engine"), str(ROOT / "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if os.environ.get("BRAIN_PG_DB", "brain") == "brain":
    sys.exit("refusing to run against the live store. Set BRAIN_PG_DB to a scratch database.")
os.environ.pop("SWARM_PARENT_TASK", None)

import store                                                            # noqa: E402
from swarm_engine import steering, transitions                          # noqa: E402,F401

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_runfeed_browser as B                                        # noqa: E402

AGENT = "T-live"
OUT = Path(os.environ.get("RF_SHOTS", "/tmp/runfeed-shots"))


#: The floor this suite's rendering assertion needs. NOT a tolerance: the suite still requires a
#: run with at least this many beats, it just declines to call a shorter one a rendering defect.
REQUIRED_BEATS = 3
NOT_RUN = 77                        # docs/SUITE-INPUT-RULE.md


def newest_adequate_stream():
    """The newest run stream the real fleet wrote THAT HAS ENOUGH BEATS TO ASSERT ON. Task 0374.

    WHAT THIS USED TO BE, AND WHY THE VIRTUE IS KEPT. It used to return `runs[0]`, the newest
    file by mtime, full stop. The intent behind that is right and rare: the browser suite beside
    this one writes its own stream, and a file this lane wrote is a file shaped the way this lane
    expects, so this one deliberately reads input it did not shape. That is kept. What is added
    is a floor.

    THE DEFECT THE FLOOR CLOSES, measured 2026-08-23 and reproduced 2026-08-27. The newest stream
    on this host is `0354-attempt1.stream.jsonl`: a session that stalled after ONE beat, last
    event 106 hours earlier, because the fleet has been stopped since 2026-08-20. The reader
    rendered 1 beat from a file containing 1 beat, which is arguably CORRECT, and the suite failed
    with "a real run rendered fewer than three beats" -- a sentence that reads as a rendering bug
    and was actually four days of stopped fleet. The suite's verdict was a function of what the
    fleet happened to leave on disk, which is not a fixture, it is a variable.

    Re-measured 2026-08-27 over the same directory, newest first:

        0354-attempt1   1 beat    <- the newest, and the one the old code took
        0355-attempt1  23 beats
        0342-attempt1  16 beats
        0352-attempt1   7 beats

    So an adequate candidate was sitting one row down the whole time.

    NOT `runs[0] if runs else exit`, AND NOT `beats >= 1`. Lowering the bar to 1 would delete the
    check. Scanning for the newest ADEQUATE file keeps both the bar and the unshaped input.

    Returns (path, population), where `population` is what a reader needs to judge the choice:
    how many candidates existed, how many were read, why each rejected one was rejected. Returns
    (None, population) when nothing on this host qualifies -- the caller declares NOT RUN.
    """
    from web import runfeed
    d = runfeed.runs_dir()
    runs = sorted(d.glob("*-attempt*.stream.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    pop = {"dir": str(d), "candidates": len(runs), "scanned": 0, "rejected": [], "chosen": None}
    for path in runs:
        pop["scanned"] += 1
        try:
            beats = runfeed.read_run(path)["beats"]
        except Exception as exc:                                        # noqa: BLE001
            pop["rejected"].append(f"{path.name}: unreadable ({type(exc).__name__})")
            continue
        if beats >= REQUIRED_BEATS:
            pop["chosen"] = {"file": path.name, "beats": beats,
                             "mtime": time.strftime("%Y-%m-%dT%H:%MZ",
                                                    time.gmtime(path.stat().st_mtime)),
                             "bytes": path.stat().st_size}
            return path, pop
        pop["rejected"].append(f"{path.name}: {beats} beat(s), needs {REQUIRED_BEATS}")
    return None, pop


def print_population(pop):
    """The denominator, on the same lines as the choice. Task 0374 item 3."""
    print(f"stream population: {pop['candidates']} candidate(s) in {pop['dir']}, "
          f"{pop['scanned']} read, {len(pop['rejected'])} rejected, "
          f"{'1 chosen' if pop['chosen'] else '0 chosen'}")
    for r in pop["rejected"][:6]:
        print(f"  rejected  {r}")
    if len(pop["rejected"]) > 6:
        print(f"  rejected  ... and {len(pop['rejected']) - 6} more")
    if pop["chosen"]:
        c = pop["chosen"]
        print(f"  chose     {c['file']}  {c['beats']} beats, mtime {c['mtime']}, "
              f"{c['bytes']:,} bytes -- written by the live fleet, not by this lane")


#: The lane this suite posts its own task into, and claims on. NOT `["*"]`, and that is row
#: `0390`'s lane fixing what `web/tests/run-all.sh` already named and filed: *"The suite's `claim`
#: being unpinned is a real fragility ... it is a test that has always assumed an empty store."*
SEED_LANE = "console"


def seed(stream: Path) -> dict:
    """One agent, one task, one run, and THE CLAIM HAS TO LAND ON THE TASK JUST POSTED.

    `store.apply("claim", ...)` names no task: it hands out the next claimable row in the lanes it
    is given. With `lanes=["*"]` that is any claimable row in the store, and inside
    `web/tests/run-all.sh` this suite is `own`, so the runner does NOT reseed before it and the
    store still holds whatever the six suites above it left.

    MEASURED 2026-08-27, replaying the runner's first six suites onto a freshly seeded
    `brain_lane_g` and then running this file:

        claimable inbox rows when this suite started:
          0002  inbox  design  Recheck the restatement against the export   <- left by an earlier suite
        after seed():
          0002  active design  claimed_by T-live     <- the claim took SOMEBODY ELSE'S row
          0003  inbox  console reading the live stream 0355-attempt1.stream.jsonl  <- ours, unclaimed

    `steering.take` then refuses our own task, correctly and in words -- *"state='inbox'
    claimed_by=nobody, and take-control is control of a RUN"* -- the seize POST is refused, the
    steering input never renders, and the next `document.getElementById('steerinput').focus()`
    throws `RuntimeError: Uncaught` twenty lines from the cause. That is what the runner has been
    printing: a suite reporting somebody else's leftovers as an unreadable crash.

    TWO CHANGES, AND NEITHER IS A LOOSENED ASSERTION. The claim is pinned to this suite's own
    lane, which excludes the row above by construction; and the row it took is then CHECKED
    against the row just posted, so if a leftover ever lands in this lane too, the suite says
    which id it got and which it wanted instead of failing in the browser. The live scene it
    guards is unchanged and still runs.
    """
    store.apply("heartbeat", agent=AGENT, status="working", role="terminal", host="live-read")
    tid = store.apply("post", lane=SEED_LANE, title=f"reading the live stream {stream.name}",
                      agent_claimable=True, workdir=str(ROOT),
                      signals={"reversibility": "reversible"})["id"]
    got = store.apply("claim", agent=AGENT, lanes=[SEED_LANE])
    got_id = (got or {}).get("id")
    if got_id != tid:
        raise SystemExit(
            f"test_runfeed_live_stream: the claim took {got_id!r}, not the {tid!r} this suite "
            f"just posted. `claim` hands out the next claimable row in lane {SEED_LANE!r} and "
            f"this suite is `own` in web/tests/run-all.sh, so the store still holds what the "
            f"suites above it left. Nothing below this line would be a statement about the "
            f"runfeed. Remedy: reseed the scratch database (`engine/bin/scratch-db.sh truncate` "
            f"then `web/bin/seed-demo.py`) and run this file again.")
    store.apply("run start", id=tid, attempt=1, agent=AGENT, host="live-read",
                session_id="", stream_pointer=str(stream), stream_pointer_host="this host")
    return {"task": tid, "path": stream}


def stalled_run_renders_sanely() -> int:
    """A ONE-BEAT STALLED RUN IS A THING THE CONSOLE MUST RENDER SANELY. Task 0374 item 4.

    Hermetic, and that is the point: it is the half of this file that runs in EVERY context, so
    the live-stream scene above can honestly declare NOT RUN without the suite vanishing
    (`docs/SUITE-INPUT-RULE.md`). It needs no console, no browser and no database -- it calls the
    reader directly on a stream it writes into a tmpdir.

    AND IT IS THE CASE TODAY'S RED WAS EVIDENCE FOR. `0354-attempt1` is a real session that
    stalled after one beat with its last event 106 hours old, and the suite's response was to
    call the RENDERER broken. Nobody had ever decided what the console should do with such a run.
    This scene decides: render the one beat it has, count it honestly, and do not invent a
    second. `read_run` is the same function `web/runfeed.feed()` calls, so this is the reader the
    console uses and not a copy of it.
    """
    import json as _json
    import tempfile
    from web import runfeed

    passed = failed = 0

    def ck(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  ok    {name}")
        else:
            failed += 1
            print(f"  FAIL  {name}")
            if detail:
                print(f"        {detail}")

    print("\nhermetic scene: a one-beat stalled run renders sanely")
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "9999-attempt1.stream.jsonl"
        stamp = "2026-08-19T05:00:00.000Z"
        lines = [
            _json.dumps({"type": "system", "subtype": "init", "session_id": "stall",
                         "timestamp": stamp}),
            _json.dumps({"type": "assistant", "session_id": "stall", "timestamp": stamp,
                         "message": {"content": [{"type": "text",
                                                  "text": "the one beat this run ever wrote"}]}}),
        ]
        # 40 tool calls with no further beat: the shape a stalled session leaves behind.
        for i in range(40):
            lines.append(_json.dumps({
                "type": "assistant", "session_id": "stall", "timestamp": stamp,
                "message": {"content": [{"type": "tool_use", "id": f"t{i}", "name": "Bash",
                                         "input": {"command": "true"}}]}}))
        path.write_text("\n".join(lines) + "\n")

        r = runfeed.read_run(path)
        ck(f"the reader returns rather than raising on a stalled run "
           f"({r['events']} events read from {path.stat().st_size:,} bytes)", True)
        ck(f"it counts the beats it has and does not invent one: {r['beats']} beat(s)",
           r["beats"] == 1, r)
        ck("it counts the tool calls that followed the last beat",
           r["tools"] >= 40, f"tools={r['tools']}")
        ck("it folds them rather than rendering 40 rows",
           len(r["entries"]) < 40, f"{len(r['entries'])} entries for {r['events']} events")
        ck("and it reports zero torn lines on a whole file", r["torn"] == 0, r["torn"])

        # THE NEGATIVE CONTROL, so "1 beat" is not simply what this reader always says.
        adequate = Path(d) / "9998-attempt1.stream.jsonl"
        many = [lines[0]]
        for i in range(5):
            many.append(_json.dumps({"type": "assistant", "session_id": "stall",
                                     "timestamp": stamp,
                                     "message": {"content": [{"type": "text",
                                                              "text": f"beat {i}"}]}}))
        adequate.write_text("\n".join(many) + "\n")
        r2 = runfeed.read_run(adequate)
        ck(f"CONTROL: the same reader counts {r2['beats']} beats on a five-beat file, so the 1 "
           f"above is a reading and not a ceiling", r2["beats"] == 5, r2)

    print(f"\n  hermetic scene: {passed} passed, {failed} failed")
    if passed + failed == 0:                                            # DENOMINATOR
        print("  0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    return 1 if failed else 0


async def main_async():
    stream, pop = newest_adequate_stream()
    print_population(pop)
    if stream is None:
        # NOT RUN, not FAIL. docs/SUITE-INPUT-RULE.md, task 0374. The input is live fleet state
        # this suite does not control, and reporting its absence as a rendering defect is
        # reporting the environment as a code defect.
        print(f"\nNOT RUN  the live-stream scene")
        print(f"         condition: no run stream in {pop['dir']} has {REQUIRED_BEATS} or more "
              f"beats. {pop['candidates']} candidate(s) existed and all {pop['scanned']} were "
              f"read and rejected; the reasons are listed above.")
        print(f"         remedy:    none from here. This scene renders a stream the LIVE FLEET "
              f"wrote, and the fleet has written nothing long enough. It is not a bar that can "
              f"be lowered: {REQUIRED_BEATS} beats is what the folds and the vitals need to be "
              f"observable at all.")
        print(f"         hermetic:  the stalled-run scene below still ran, so this is not a "
              f"suite that vanished.")
        stalled_scene_result = stalled_run_renders_sanely()
        return NOT_RUN if stalled_scene_result == 0 else 1
    size0 = stream.stat().st_size
    run = seed(stream)
    print(f"reading {stream} ({size0:,} bytes, written by the live fleet)")

    console = B.start_console()
    chrome = B.start_chrome()
    trail = []
    try:
        async with websockets.connect(B.ws_url(), max_size=40 * 1024 * 1024) as ws:
            p = B.Page(ws)
            await p.send("Page.enable")
            await p.send("Runtime.enable")
            await p.width(1440, 1400)
            await p.goto(f"/agent/{AGENT}")
            got = await p.js("""({
                vitals: (document.querySelector('#vt .vt')||{}).textContent||'',
                beats: document.querySelectorAll('#feed .ev').length,
                folds: document.querySelectorAll('#feed .foldbtn').length,
                now: (document.querySelector('.nowline')||{}).textContent||'',
                live: (document.querySelector('#live')||{}).textContent||''
            })""")
            print("vitals :", " ".join(got["vitals"].split()))
            print("now    :", got["now"][:110])
            print("live   :", " ".join(got["live"].split())[:110])
            print(f"beats  : {got['beats']}   folds: {got['folds']}   "
                  f"(fixture {stream.name}, {pop['chosen']['beats']} beats on disk, chosen from "
                  f"{pop['candidates']} candidates)")
            assert got["beats"] >= REQUIRED_BEATS, (
                f"the console rendered {got['beats']} beat(s) from {stream.name}, which holds "
                f"{pop['chosen']['beats']} on disk. This IS a rendering defect: the file was "
                f"checked for adequacy before it was chosen, so a short render is the reader's "
                f"doing and not the fleet's")
            assert "~$" in got["vitals"], (
                "the cost did not derive from a real stream, which is the one thing a synthetic "
                "stream cannot check")

            # A steering session, end to end, on the scratch rows.
            await p.js("document.querySelector('[data-toggle=\\'seize\\']').click()")
            await asyncio.sleep(0.3)
            await p.js("document.querySelector('#seize form button.act').click()")
            await asyncio.sleep(1.2)
            await p.goto(f"/agent/{AGENT}")
            said = ("re-measure the row count before you report it, and quote what the query "
                    "printed")
            await p.js(f"""(function(){{
                var ta = document.getElementById('steerinput');
                ta.focus(); ta.value = {json.dumps(said)};
                ta.dispatchEvent(new Event('input', {{bubbles:true}}));
                ta.setSelectionRange(6, 6);
                return true; }})()""")
            # Two poll cycles against a file the fleet may be appending to as this runs.
            await asyncio.sleep(7.0)
            caret = await p.js("({v:document.activeElement.value,"
                               " s:document.activeElement.selectionStart,"
                               " polls:window.__rfPolls, appended:window.__rfAppended})")
            print(f"caret  : offset {caret['s']} after {caret['polls']} polls "
                  f"({caret['appended']} entries appended)")
            assert caret["s"] == 6 and caret["v"] == said, "the caret moved on a live stream"
            await p.js("document.querySelector('.sayform button.act').click()")
            await asyncio.sleep(1.2)
            await p.goto(f"/agent/{AGENT}?theme=light")
            await p.shot(OUT / "runfeed-live-steering-light.png")
            await p.width(390, 900)
            await p.goto(f"/agent/{AGENT}")
            await p.shot(OUT / "runfeed-live-steering-phone.png")
            await p.width(1440, 1400)
            await p.goto(f"/agent/{AGENT}")
            await p.shot(OUT / "runfeed-live-steering-dark.png")
    finally:
        chrome.kill()
        console.kill()

    with store.read() as s:
        trail = s.query("SELECT seq, ts, from_agent, to_agent, kind, text FROM brain.thread "
                        " WHERE work_item_id = %s ORDER BY seq", (run["task"],))
    print("\n--- the thread trail a steered run leaves, which is what `show --full` reads ---")
    for r in trail:
        who = r["from_agent"] + (f" → {r['to_agent']}" if r["to_agent"] else "")
        print(f"  {r['seq']:>4} {r['ts'].strftime('%H:%M:%SZ')} {who:<22} {r['kind']:<8} "
              f"{' '.join(r['text'].split())[:120]}")
    print(f"\nsteered: {steering.is_steered(run['task'])}   "
          f"still holding control: {steering.active_steering(run['task']) is not None}")
    print(f"stream grew by {stream.stat().st_size - size0:,} bytes while this ran")
    # The hermetic half runs in BOTH branches, always, so the suite's coverage does not depend on
    # which one it took. docs/SUITE-INPUT-RULE.md.
    return stalled_run_renders_sanely()


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
