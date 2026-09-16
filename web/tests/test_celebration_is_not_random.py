"""Row 0409: celebration fires on OTHER PEOPLE'S acts and never on your own, and both halves of
that are wrong at the surface.

The operator drove the eleven-step demo on 2026-08-28 and reported two things that read as
separate defects and are one mechanism:

    "kind of funny, when I opened the task, it then just launched confetti randomly"
    "nothing happened when I accepted work"

`console.js` polls `/api/celebrate?since=N` every three seconds, `model.celebrations()` returned
every `brain.thread` row with `kind IN ('done','accept')` newer than the cursor, and the cursor
was moved to the store's head by the CLIENT on every write it made. One cursor was answering two
questions -- "what have I seen" (a position) and "which of these are mine" (an identity) -- and
using a position for the second one produced all three of the behaviours this file pins:

  1. his own act was never celebrated                              INTENDED, and kept
  2. anybody else's `done` painted a full-viewport burst at him    measured at +2.12s, mid-read
  3. his own click DESTROYED other actors' un-celebrated events    measured, recorded nowhere

Nothing here is a claim about a selector existing. Every check below is about what is on a
person's screen: whether particles were painted, and whether the sentence that says what
happened was inside the viewport at the time.

THE DATABASE THIS FILE WRITES TO AND THE CONSOLE IT DRIVES ARE TWO DIFFERENT PROCESSES.
`_console_guard.refuse_unless_scratch` resolves the listener at `$BASE` to a pid and reads its
own `BRAIN_PG_DB`, exactly as `test_browser.py` does, because `BRAIN_PG_DB` set here reaches
this process and not that one.

    web/tests/run-all.sh                    (stands up its own console and store)
    BASE=http://127.0.0.1:3113 BRAIN_PG_DB=brain_console python3 -m web.tests.test_celebration_is_not_random
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import websockets

_R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_R, os.path.join(_R, "engine"), os.path.join(_R, "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _console_guard                                                   # noqa: E402
# THE HARNESS IS IMPORTED, NOT RETYPED. `Page`, `_launch` and `_ws_url` carry three measured
# lessons each (a fresh profile per launch, a refusal on a stale chrome holding the CDP port, and
# a process-GROUP kill), and a second copy of them here would be a second place for those to rot.
# Importing runs no test: `test_browser.py` guards its own entry point behind `__main__`.
from web.tests.test_browser import BASE, Page, _launch, _ws_url         # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []

# A LANE OF ITS OWN, so `claim` cannot take somebody else's row. `store.apply("claim", ...)` names
# no task -- it takes the top of the lane -- which is how `test_runfeed_browser` ended up asserting
# about a task it had never posted (run-all.sh says so at length). A lane nothing else uses makes
# the claim deterministic without pinning anything.
PROBE_LANE = "celebration-probe"

CANVAS = ("(function(){var c=document.getElementById('cf');"
          " if(!c) return null;"
          " return c.getContext('2d').getImageData(0,0,c.width,c.height).data"
          "         .some(function(x){return x!==0});})()")

LINE = ("(function(){var e=document.querySelector('#celebrate .receipt');"
        " if(!e) return null; var r=e.getBoundingClientRect();"
        " return {text:(e.textContent||'').replace(/\\s+/g,' ').trim(),"
        "         top:Math.round(r.top), inView:r.bottom>0&&r.top<innerHeight};})()")

SAY = ("(function(){var e=document.querySelector('#saybar .banner');"
       " if(!e) return null; var r=e.getBoundingClientRect();"
       " return {text:(e.textContent||'').replace(/\\s+/g,' ').trim(),"
       "         top:Math.round(r.top), inView:r.bottom>0&&r.top<innerHeight};})()")


def _other_actor_finishes(title: str) -> str:
    """A `done` from somebody who is not the operator, through the registered verbs."""
    import store
    from swarm_engine import transitions                                # noqa: F401
    wid = store.apply("post", title=title, lane=PROBE_LANE, posted_by="commander")["id"]
    store.apply("claim", agent="T9", lanes=[PROBE_LANE], role="worker")
    store.apply("done", id=wid, agent="T9", summary="finished in another terminal entirely")
    return wid


async def _wait_for(p: Page, expr, seconds: float = 10.0):
    """Poll the page for a value that is not None/False. Returns (elapsed, value) or None."""
    steps = int(seconds / 0.25)
    for i in range(steps):
        await asyncio.sleep(0.25)
        v = await p.js(expr)
        if v:
            return round((i + 1) * 0.25, 2), v
    return None


async def _click_first(p: Page, action: str) -> bool:
    """Press the primary control of the first card offering `action`. A real click on a real
    button, so the write goes through `post()` in `console.js` and not around it."""
    return bool(await p.js("""(function(){var f=null;
      document.querySelectorAll('form.act-form').forEach(function(x){
        if(!f && x.querySelector('[name=action][value=%s]')) f=x;});
      if(!f) return false; f.querySelector('button.act').click(); return true;})()""" % action))


async def another_actors_done_states_itself_and_paints_nothing(p: Page):
    """Symptom 1. He was reading. Somebody else's `done` landed. Confetti.

    The burst is gone and the SENTENCE is not: a celebration with no referent is noise, and the
    line used to read `0013 done by T9` -- an id, a verb and an agent name. It now names the item
    the way the operator names it, with its title.
    """
    await p.goto("/queue?tier=judge")
    assert await p.js(CANVAS) is False, "the canvas had pixels before anything happened"
    # He is reading: a panel open, no write of his own.
    await p.js("var t=document.querySelector('[data-toggle^=\"defer-\"]'); if(t) t.click(); true")
    title = "Another terminal finished this while you were reading"
    wid = _other_actor_finishes(title)
    hit = await _wait_for(p, LINE, 10.0)
    assert hit, f"{wid} finished and the console never said so within 10s"
    dt, line = hit
    assert wid in line["text"], f"the line does not name the item: {line['text']!r}"
    assert title[:24] in line["text"], f"the line does not name what it is about: {line['text']!r}"
    assert line["inView"], f"the line rendered at {line['top']}px, outside the viewport"
    # Two full poll cycles AFTER the line, because the burst used to fire on the same poll.
    await asyncio.sleep(6.5)
    assert await p.js(CANVAS) is False, (
        "somebody else's routine `done` painted particles over the operator's reading surface")
    print(f"      +{dt}s: {line['text']!r} at {line['top']}px, 0 canvas pixels")


async def your_own_act_is_never_celebrated(p: Page):
    """The rule that was right, kept -- and now kept by identity rather than by a moved cursor.

    Checked at the endpoint AND at the canvas: the endpoint proves the exclusion survives a
    reload (a cursor jump did not), and the canvas proves the surface agrees.
    """
    await p.goto("/queue?tier=judge")
    head = await p.js("fetch('/api/celebrate?since=0').then(function(r){return r.json();})"
                      ".then(function(j){return j.head;})")
    assert await _click_first(p, "accept_work"), "no card offered `Accept work`"
    await asyncio.sleep(1.0)
    after = await p.js(f"fetch('/api/celebrate?since={head}').then(function(r){{return r.json();}})")
    assert after["events"] == [], (
        f"the operator's own act came back as something to celebrate: {after['events']}")
    assert after["head"] > head, (
        "nothing was written, so this proves nothing: head did not move past the accept")
    await asyncio.sleep(6.5)
    assert await p.js(CANVAS) is False, "a click celebrated itself"
    print(f"      accept moved head {head} -> {after['head']} and returned 0 events, 0 pixels")


async def a_click_does_not_swallow_somebody_elses_event(p: Page):
    """The half nobody had recorded, and the reason a cursor was the wrong instrument.

    `celebrate_from` is `max(seq)` over the whole thread, not the seq of this write. Jumping to
    it stepped over every event anybody else had landed since the last poll. Measured before the
    fix: 12 seconds of silence for a `done` that fires reliably at ~2s with no click in the way.
    """
    await p.goto("/queue?tier=judge")
    wid = _other_actor_finishes("T9 finished this 200ms before the operator clicked")
    await asyncio.sleep(0.2)
    # ANY write of his own reproduces this: the cursor jump was on the response, not on the verb.
    # `accept_default` rather than a second `accept work` so the two checks that need a reviewable
    # card do not compete for the same two rows in the seeded store.
    assert await _click_first(p, "accept_default"), "no card offered `Accept default`"
    hit = await _wait_for(p, LINE, 12.0)
    assert hit, (f"{wid} was finished by T9 and the operator's own click one fifth of a second "
                 f"later deleted it: 12s of silence")
    dt, line = hit
    assert wid in line["text"], f"a different event surfaced: {line['text']!r}"
    print(f"      T9's `done` survived the operator's click, stated at +{dt}s")


async def the_receipt_for_your_own_act_is_in_the_viewport(p: Page):
    """"Nothing happened when I accepted work" was an accurate report of his screen.

    The receipt was never missing. It rendered at 470ms and 770ms, at the TOP of a document he
    had scrolled, while the card he clicked was at y 357 and the page shifted under him when it
    left. Accepting the LAST card of the tier is what puts the two hosts off screen, so that is
    what this drives; accepting the first one at scrollY 0 passes either way, which is why this
    defect survived a green run.
    """
    # A SHORTER WINDOW FOR THIS ONE CHECK, AND IT IS A DENOMINATOR REPAIR RATHER THAN A CHANGE
    # TO WHAT THIS CHECK ASSERTS. Row 0431 turned every queue item into a one-line grid row and
    # moved 177px of collapsed drawers below the list, so the seeded Judge tier that used to be
    # taller than the 1200px window `_launch` opens now fits inside it. This check's subject IS
    # the scroll: 0409 was geometry, the receipt rendered at -395px on a tier he had scrolled,
    # and accepting the FIRST card at scrollY 0 passes either way. With the page fitting, the
    # `scrolls` guard below fires and the check reports, correctly, that it cannot see the defect
    # it exists for. That is a verdict over an empty set, not a pass and not a defect.
    #
    # Measured on a freshly seeded scratch store, 1440 wide: `/queue?tier=judge` is 852px tall,
    # so it fits a 1200px window and scrolls a 700px one, and both `Accept work` cards are still
    # on it (2 of 2). 700 rather than 760 because 760 is a laptop height somebody may later make
    # this suite's default; a number chosen to be smaller than the CONTENT is the honest one.
    #
    # NOTHING THIS CHECK ASSERTS IS TOUCHED: not `inView`, not the 6s wait, not the requirement
    # that the receipt names the item, and no tolerance is widened. The override is cleared in
    # the `finally` so the checks after this one measure the window `_launch` actually opened.
    await p.send("Emulation.setDeviceMetricsOverride", width=1440, height=700,
                 deviceScaleFactor=1, mobile=False)
    try:
        await _the_receipt_is_in_the_viewport(p)
    finally:
        await p.send("Emulation.clearDeviceMetricsOverride")


async def _the_receipt_is_in_the_viewport(p: Page):
    await p.goto("/queue?tier=judge")
    # FIND, SCROLL AND CLICK IN ONE EVALUATION. Marking the form and coming back for it in a
    # second call is what the poller is built to break: a repaint replaces the list region's
    # innerHTML, the marked node and its attribute go with it, and the second call clicks null.
    # `scrollIntoView` is synchronous here, so `scrollY` below is the position the click happened
    # at rather than the position before it.
    where = await p.js("""(function(){var fs=[];
      document.querySelectorAll('form.act-form').forEach(function(x){
        if(x.querySelector('[name=action][value=accept_work]')) fs.push(x);});
      if(!fs.length) return null;
      var f=fs[fs.length-1];
      /* ROW 0431 PUT THE VERBS ONE CLICK INSIDE THE ROW, so this check has to reach the surface
         the way a person now does. Without opening the row first, `btn` is inside a
         `display:none` subtree: it has NO BOX, `getBoundingClientRect()` reads 0, and
         `scrollIntoView` scrolls nowhere -- measured here, three window heights, the last
         `Accept work` form reporting a document top of 0 at 700, 600 and 500px. The click still
         fired and the write still landed, so the check went green while reporting `the button at
         y 0 and scrollY 0`, which is a GEOMETRY claim over an element that had no geometry. The
         open is synchronous: `console.js`'s 0431 module takes the click, writes the hash with
         `replaceState` and paints `.is-open` in the same handler, so the box exists by the next
         line. This changes how this check REACHES the surface and nothing about what it asserts.
         Same pattern lane B1 applied to `test_browser.py` and
         `test_argument_is_beside_the_verb.py` on 2026-08-29. */
      var row=f.closest ? f.closest('.qrow') : null;
      if(row){var op=row.querySelector('a.qopen'); if(op) op.click();}
      var idf=f.querySelector('[name=id]'), btn=f.querySelector('button.act');
      if(!idf || !btn) return {broken:true};
      f.scrollIntoView({block:'center'});
      var out={id:idf.value, scrollY:Math.round(scrollY),
               btnTop:Math.round(btn.getBoundingClientRect().top),
               scrolls:document.documentElement.scrollHeight > innerHeight};
      btn.click();
      return out;})()""")
    assert where, "no card offered `Accept work`"
    assert not where.get("broken"), "the accept form carries no id input or no primary button"
    assert where["scrolls"], (
        "the queue fits in this viewport, so this check cannot see the defect it exists for. "
        "It needs a page taller than the window; make the window shorter or seed more cards.")
    hit = await _wait_for(p, SAY, 6.0)
    assert hit, f"accepting {where['id']} said nothing at all within 6s"
    dt, say = hit
    assert where["id"] in say["text"], f"the receipt does not name the item: {say['text']!r}"
    assert say["inView"], (
        f"the receipt for his own act rendered at {say['top']}px with scrollY {where['scrollY']}: "
        f"on time, above the fold, out of sight. That is the whole of \"nothing happened\".")
    print(f"      accepted {where['id']} with the button at y {where['btnTop']} and scrollY "
          f"{where['scrollY']}: {say['text']!r} at {say['top']}px, in view, at +{dt}s")


async def _run():
    async with websockets.connect(_ws_url(), max_size=40 * 1024 * 1024) as ws:
        p = Page(ws)
        await p.send("Page.enable")
        await p.send("Runtime.enable")
        # ORDER IS LOAD BEARING AND SO IS SAYING SO. Three of these four resolve a real card,
        # and the seeded Judge tier offers exactly two with `Accept work` on them, so the check
        # that needs the tier at full height runs first and the one that needs no write runs last.
        for name, fn in [
            # FIRST: it needs the tier at full height AND the last `Accept work` card on it, so
            # anything that resolves a card has to run after it.
            ("the receipt for your own act is IN THE VIEWPORT",
             the_receipt_for_your_own_act_is_in_the_viewport),
            ("your own act is never celebrated",
             your_own_act_is_never_celebrated),
            ("a click does not swallow somebody else's event",
             a_click_does_not_swallow_somebody_elses_event),
            ("somebody else's `done` states itself and paints nothing",
             another_actors_done_states_itself_and_paints_nothing),
        ]:
            try:
                await fn(p)
                PASS.append(name)
                print(f"ok    {name}")
            except AssertionError as exc:
                FAIL.append((name, str(exc)))
                print(f"FAIL  {name}\n      {exc}")
            except Exception as exc:                                    # noqa: BLE001
                FAIL.append((name, repr(exc)))
                print(f"ERROR {name}\n      {exc!r}")


def verdict() -> int:
    """The verdict, and beside it the count of checks that produced it. Task 0445.

    A module-level function rather than four lines at the foot of `main` on purpose. `main`
    cannot run without a console on a scratch store, a websocket and a browser, so a guard
    living inside it is one nobody can fire on demand; here the zero branch is reachable from a
    probe that imports this file and sets the counters, which is the difference between
    asserting the rule and demonstrating it. The empty set is not hypothetical here: `_run`
    catches every exception each scenario raises, so a console that never came up, or a
    `_launch` that died before the first scenario, produces an empty PASS and an empty FAIL and
    the old line printed `0 passed, 0 failed` and returned 0.
    """
    if len(PASS) + len(FAIL) == 0:                                      # DENOMINATOR
        print("0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


def main() -> int:
    served = _console_guard.refuse_unless_scratch(
        BASE, what="test_celebration_is_not_random.py")
    print(f"      console at {BASE} is pid {served['pid']} on database {served['db']!r}")
    proc = _launch()
    try:
        asyncio.run(_run())
    finally:
        proc.kill()
    return verdict()


if __name__ == "__main__":
    sys.exit(main())
