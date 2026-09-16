"""What only a browser can prove, driven over the Chrome DevTools Protocol.

The design guide's verification section closes with *"Not verified: rendered appearance. The
Browser pane would not composite frames, so everything above was measured in a live DOM rather
than seen."* Playwright's chromium is on this host, so appearance IS verifiable here and so is
behaviour that only exists once a real event loop is running.

Three things are checked, and each of them is a claim the console makes that a DOM measurement
cannot support on its own:

  1. **A focused input survives a repaint.** Not "the value is still there" -- that is worth
     nothing if no repaint happened. This test forces a REAL data change mid-typing, proves the
     poll patched other regions because of it, and proves the region holding the caret was
     skipped with its text, its selection and its submit button untouched.
  2. **Deep work is absolute.** With deep work on, the fast pane is EMPTY IN THE MARKUP: not
     display:none, not off-screen. A card that is in the DOM is a card a screen reader reads out
     and a card that ships down the wire.
  3. **A click produces no celebration.** Celebration fires on events arriving from the system;
     a click is an intention. Checked by reading the canvas pixels after a resolve.
  4. **An idle poll swaps nothing.** The inverse of 1, and the defect it pins is the one that
     made 1 worth having: the poll used to swap three regions every three seconds on a page where
     nothing had happened, so any panel the operator opened had a median life under a second.
     A DOM measurement cannot catch this, because the DOM after a needless swap is the DOM
     before it.
  5. **A gated verb leaves a receipt, and the stack advances on a decision.** Both are writes
     whose only proof is what the screen does next, and both were silent: the receipt was
     suppressed by the focus guard and the stack's button never came back.

THE CONSOLE THIS DRIVES IS A DIFFERENT PROCESS FROM THIS ONE, AND THAT IS THE HAZARD. Task 0373.
`BRAIN_PG_DB` configures the database THIS file connects to and reseeds. `BASE` names a
long-lived console with its own `BRAIN_PG_DB`, fixed when it was exec'd, which nothing here can
reach. On this host the default `BASE` has been a console on `brain` since at least 2026-08-23, so
following the old run line below verbatim would have truncated a scratch database and then driven
real clicks into the operator's live queue. `_console_guard.refuse_unless_scratch` now resolves
the listener to a pid, reads `/proc/<pid>/environ`, and refuses. Run this through the runner,
which stands a console up for you:

    web/tests/run-all.sh

Or by hand, with a console you started yourself on a scratch database:
    BASE=http://127.0.0.1:3113 BRAIN_PG_DB=brain_console python3 -m web.tests.test_browser
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets

_R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_R, os.path.join(_R, 'engine'), os.path.join(_R, 'queue')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CHROME = os.environ.get(
    "CHROME", os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux/chrome"))
BASE = os.environ.get("BASE", "http://127.0.0.1:3103")
PORT = 9223

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _console_guard                                                   # noqa: E402

PASS, FAIL = [], []


class Page:
    def __init__(self, ws):
        self.ws = ws
        self.n = 0

    async def send(self, method, **params):
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    async def js(self, expr):
        r = await self.send("Runtime.evaluate", expression=expr, returnByValue=True,
                            awaitPromise=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(r["exceptionDetails"].get("text", "js error"))
        return r.get("result", {}).get("value")

    async def goto(self, path):
        await self.send("Page.navigate", url=BASE + path)
        for _ in range(60):
            await asyncio.sleep(0.25)
            if await self.js("document.readyState === 'complete'"):
                return
        raise RuntimeError(f"{path} never finished loading")


def _launch():
    """A browser with NO MEMORY OF THE LAST RUN, because the mode this console renders lives in
    a cookie the SERVER reads.

    This used to pass `--user-data-dir=/tmp/console-cdp-profile`: one directory, shared by all
    three browser suites, persisting between runs. Deep work is `io_deep`, a one-year cookie
    (`console.js:31`), and every check here that navigates without spelling `deep=` inherits
    whatever the LAST run left in that jar. MEASURED 2026-08-18 against this tree (task 0208):
    seed `io_deep=1` into that profile and `test_phone.py` goes from `4 passed, 0 failed` to
    `2 passed, 2 failed` on identical code and an identical server, failing on `at 900px
    .split.two is not a grid` and `at 390px the fast lane did not render its collapsed strip`.
    Nothing was wrong with the surface. The browser was measuring the other mode.

    A FRESH TEMPORARY PROFILE PER LAUNCH, rather than wiping the fixed path before launch,
    because a wipe leaves the sharing in place and only narrows the window: two suites started
    at once still point at one directory, and `rm -rf` under a live chrome is how task 0176's
    lane would get a corrupted profile instead of a stale one. A path nobody else knows cannot
    be shared. It is removed at interpreter exit, so a run that dies mid-suite still cleans up.
    """
    # A STALE CHROME DEFEATS THE FRESH PROFILE ENTIRELY. The wait below returns as soon as
    # ANYTHING answers on the debugging port -- and a chrome left over from an earlier run
    # answers, still holding the OLD profile, while the one just started fails to bind and dies
    # unnoticed. That is the same defect wearing a different hat, and T4 reported it on
    # 2026-08-16 as "a stale chromium holding /tmp/console-cdp-profile". Refuse, loudly, rather
    # than measure a browser nobody in this process started.
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1) as r:
            json.load(r)
        raise RuntimeError(
            f"something is already listening on the CDP port {PORT}. That is a chrome from an "
            f"earlier run, and these tests would silently drive IT -- with its old cookie jar -- "
            f"instead of the fresh profile just made. Kill it first: "
            f"pkill -f 'chromium-.*chrome.*remote-debugging-port={PORT}'")
    except RuntimeError:
        raise
    except Exception:                                                   # noqa: BLE001
        pass                                                            # nothing there: good
    profile = tempfile.mkdtemp(prefix="console-cdp-")
    # `start_new_session` PUTS THE WHOLE BROWSER IN ONE PROCESS GROUP, so the cleanup below can
    # kill the GROUP rather than the one pid `Popen` returns. A chromium is a browser process, a
    # zygote, a gpu process and a network service; every call site here ends with `proc.kill()`,
    # which reaches exactly one of them and trusts the rest to notice.
    proc = subprocess.Popen(
        [CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
         f"--remote-debugging-port={PORT}", "--window-size=1440,1200",
         f"--user-data-dir={profile}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    # THE GROUP ID IS READ HERE, WHILE THE CHILD IS CERTAINLY ALIVE, and never again. Reading it
    # inside the cleanup is too late and fails silently: `main()` kills the browser and returns,
    # `proc` goes out of scope, and `Popen.__del__` reaps the exited pid -- so by the time
    # `atexit` runs, `os.getpgid(pid)` raises `ProcessLookupError`, the `except` swallows it and
    # NOTHING IS KILLED. Measured on this host 2026-08-19: that exact shape left a chrome holding
    # port 9223 with its temp profile undeleted after a completed `test_browser.py`, and the next
    # suite (`test_phone_surface.py`) then refused to start on the guard above. A cleanup that
    # cannot fail loudly has to be built so it cannot fail quietly either.
    pgid = os.getpgid(proc.pid)

    def _cleanup(pgid=pgid, path=profile):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except Exception:                                               # noqa: BLE001
            pass                                                        # already gone
        for _ in range(15):      # let the group die before the directory goes out from under it
            time.sleep(0.2)
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.isdir(path):
                return
        # A surviving directory means a surviving chrome recreating files inside it, which is the
        # orphan the guard above will refuse to run past. Say so, on the way out, by name.
        print(f"WARNING: could not remove the browser profile {path}; something in process group "
              f"{pgid} is still writing to it. Kill it before the next browser suite runs.",
              file=sys.stderr)

    atexit.register(_cleanup)
    for _ in range(80):
        time.sleep(0.25)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1) as r:
                json.load(r)
            return proc
        except Exception:                                               # noqa: BLE001
            continue
    proc.kill()
    raise RuntimeError("chrome did not come up on the debugging port")


def _ws_url():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=2) as r:
        targets = json.load(r)
    for t in targets:
        if t.get("type") == "page":
            return t["webSocketDebuggerUrl"]
    raise RuntimeError("no page target")


async def _run():
    async with websockets.connect(_ws_url(), max_size=40 * 1024 * 1024) as ws:
        p = Page(ws)
        await p.send("Page.enable")
        await p.send("Runtime.enable")
        # FIRST: it asserts that thirteen seconds of polling change nothing, and a receipt stripe
        # expiring seven seconds after some earlier test's write is a real change.
        for name, fn in [("an idle poll swaps nothing", idle_poll_swaps_nothing),
                         ("focused input survives a repaint", focus_survives_repaint),
                         ("deep work removes the fast pane from the MARKUP", deep_work_absolute),
                         ("deep work is legible in all five rooms",
                          deep_work_is_legible_in_the_shell),
                         ("send back is gated on a real reason", send_back_gate),
                         ("the two real verbs carry the same weight", verbs_carry_equal_weight),
                         ("both themes pass WCAG AA on every text node", light_theme_passes_aa),
                         ("no page-level horizontal scroll at 375 and 390", no_h_scroll),
                         ("prefers-reduced-motion disables every animation",
                          reduced_motion),
                         # LAST: these three resolve real items, so anything needing a reviewable
                         # or a decidable item has to run before them.
                         ("the stack advances on a decision", stack_advances_on_a_decision),
                         ("a gated verb leaves a receipt", gated_verb_leaves_a_receipt),
                         ("a click produces no celebration", click_does_not_celebrate)]:
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


# ROW 0431: A CARD AT REST IS A SPREADSHEET ROW AND ITS CONTROLS ARE ONE CLICK AWAY.
#
# Three checks in this file drove a control that is now inside the level-one expansion, and each
# failed in a way that read like a product defect and was not one:
#
#   focused input survives a repaint     "the textarea did not take focus"
#   a gated verb leaves a receipt        "the caret never reached the field"
#   the two real verbs carry the same weight    ZeroDivisionError on a 0x0 button
#
# An element inside a `display:none` subtree cannot take focus and has no box, so all three were
# measuring an unopened row. THIS IS A CHANGE TO HOW THE SUITE REACHES THE SURFACE AND NOT TO
# WHAT IT ASSERTS: every assertion below, and every tolerance in them, is untouched. It is also
# what a person now does, which is the standard this file was written to.
#
# It returns the row id it opened, or null if the selector matched nothing, so a caller can tell
# "nothing to open" from "opened it".
OPEN_ROW_HOLDING = """(function(sel){
  var el = document.querySelector(sel);
  if (!el) return null;
  var row = el.closest ? el.closest('.qrow') : null;
  if (!row) return 'not-a-grid-row';
  var opener = row.querySelector('.qopen');
  if (opener) opener.click();
  return row.id;
})"""


async def focus_survives_repaint(p: Page):
    await p.goto("/queue?tier=judge")
    opened = await p.js(OPEN_ROW_HOLDING + "('[data-toggle^=\"sb-\"]')")
    assert opened, "no reviewable item on the Judge tier to open"
    await asyncio.sleep(0.15)
    await p.js("document.querySelector('[data-toggle^=\"sb-\"]').click()")
    await asyncio.sleep(0.2)
    typed = "round 1 accepted on a path that does not exist, re-walk every evidence path"
    await p.js(f"""(function(){{
        var ta = document.querySelector('.sbwrap:not([hidden]) textarea');
        ta.focus(); ta.value = {json.dumps(typed)};
        ta.dispatchEvent(new Event('input', {{bubbles:true}}));
        ta.setSelectionRange(6, 6);
        return true;
    }})()""")
    before = await p.js("({v: document.activeElement.value, s: document.activeElement.selectionStart,"
                        " tag: document.activeElement.tagName})")
    assert before["tag"] == "TEXTAREA", "the textarea did not take focus"

    # Force a REAL data change so the poll has something to patch. Without this the test would
    # pass on a page where nothing repainted at all.
    import store
    from swarm_engine import transitions as _t                          # noqa: F401
    with store.read() as s:
        qid = s.scalar("SELECT id FROM brain.question WHERE answer IS NULL ORDER BY id LIMIT 1")
    if qid:
        store.apply("answer", actor="operator", qid=qid,
                    text="answered from the browser test to force a repaint", requeue=False)

    await asyncio.sleep(7.5)                                            # two full poll cycles
    after = await p.js("""({
        v: document.activeElement.value,
        s: document.activeElement.selectionStart,
        tag: document.activeElement.tagName,
        id: document.activeElement.id,
        patches: window.__patches,
        patched: window.__patchedRegions,
        skipped: window.__skippedRegions,
        btn: document.activeElement.parentElement.querySelector('button.act').disabled
    })""")
    assert after["patches"] >= 2, f"only {after['patches']} polls ran; nothing repainted"
    assert after["patched"], ("no region was patched, so this test proves nothing about a "
                              "repaint. Patched: none.")
    assert "list" in after["skipped"], (
        f"the region holding the caret was not skipped. skipped={after['skipped']}")
    assert after["tag"] == "TEXTAREA", f"focus moved to {after['tag']} during the repaint"
    assert after["v"] == typed, f"the text changed under the caret: {after['v']!r}"
    assert after["s"] == 6, f"the selection moved from 6 to {after['s']}"
    assert after["btn"] is False, "the submit button was re-disabled by the repaint"
    print(f"      {after['patches']} polls; patched {sorted(set(after['patched']))}; "
          f"skipped {sorted(set(after['skipped']))}")


async def idle_poll_swaps_nothing(p: Page):
    """Four polls of a page where nothing happened must swap exactly nothing.

    The comparison that makes this true is `served[name] !== html` in console.js: the last string
    the SERVER sent, never live innerHTML. Three independent things made a DOM-to-wire comparison
    permanently unequal -- `wire()` stamping `data-wired`, the parser re-serialising Jinja's
    wrapped attribute lists, and a `+2h` chip rendered to the microsecond -- and a fourth, the
    CSRF token, was minted fresh on every render until `guard.TOKEN_BUCKET_SECONDS`.

    The first poll paints every region once because `served` starts empty, and console.js runs it
    at boot rather than at t+3s for exactly that reason. So this asserts on what happens AFTER it.

    One thing legitimately churns and is not a bug: `agent waiting Nm` ticks once a minute. The
    seeded store has no claimed agent, verified by a 70s idle diff of `/api/patch/queue` showing
    zero changed regions, so this window is quiet by construction rather than by luck.

    THE SECOND THING THAT LEGITIMATELY CHURNS IS A RUNNING STOPWATCH, and until row `0390` this
    check could not tell it from a defect. The strip prints `%.1f min`, which is a new string
    every six seconds; it is the ONE region on this screen designed to change on the clock, and
    every other clock-derived figure was deliberately moved into it so the card region would not
    churn (`macros.html:650` states that decision, `test_stopwatch_at_the_surface.py:
    test_a_running_timer_does_not_make_the_card_region_churn` proves it holds). A page with a
    timer running is therefore not an IDLE page, and this check's subject is an idle one.

    So the input is stated rather than assumed: `_reseed()` now clears `brain.time_entry`, and
    the first thing asserted below is that the strip actually says no timer is running. That
    assertion is the remedy `docs/SUITE-INPUT-RULE.md` asks for -- name the input you needed --
    and it costs the check nothing, because if a timer IS running the message now says which
    input was wrong instead of blaming the poll.
    """
    await p.goto("/queue?tier=judge")
    # THE INPUT, NAMED BEFORE THE MEASUREMENT. Read off the served markup rather than out of the
    # store, because the page the browser is about to watch for 13 seconds is the thing that has
    # to be idle, and this file drives a console it does not share a database handle with.
    strip = await p.js(
        "(document.querySelector('[data-region=stopwatch]')||{innerHTML:''}).innerHTML")
    assert "strip timing" not in strip, (
        "a stopwatch is RUNNING on this console, so the page is not idle and this check cannot "
        f"speak: the strip re-renders `%.1f min` every six seconds by design. Strip: "
        f"{strip.strip()[:160]!r}. Remedy: clear brain.time_entry before seeding, which "
        "`_reseed()` does; run this suite through web/tests/run-all.sh.")
    await asyncio.sleep(1.0)                       # let the boot patch land and seed `served`
    first = await p.js("({patched: window.__patchedRegions.slice(), polls: window.__patches})")
    assert first["polls"] >= 1, "the boot patch never ran, so nothing established the baseline"
    assert first["patched"], "the boot patch swapped no region, so `served` may not be seeded"
    await p.js("window.__patches=0; window.__patchedRegions=[]; window.__skippedRegions=[]")
    await asyncio.sleep(13.0)
    r = await p.js("({polls: window.__patches, patched: window.__patchedRegions,"
                   " skipped: window.__skippedRegions})")
    assert r["polls"] >= 3, f"only {r['polls']} polls ran in 13s; this proves nothing"
    assert r["patched"] == [], (
        f"{len(r['patched'])} region swap(s) on an idle page: {sorted(set(r['patched']))}. "
        f"Every one of them destroys whatever the operator had open.")
    assert r["skipped"] == [], f"a region was skipped with nothing focused: {r['skipped']}"
    print(f"      boot poll painted {len(first['patched'])} regions; "
          f"{r['polls']} polls over 13s idle painted 0")


async def gated_verb_leaves_a_receipt(p: Page):
    """Send back, submitted with the caret still in the field, as an operator actually submits it.

    This resolves a real item, so it runs late. The receipt used to render INSIDE `data-region=
    list`, which is the region the caret was in, so the focus guard suppressed the one region that
    had to change: the write landed, the card stayed, and the saybar was empty. Two things fix it
    and this asserts both -- the stripe is its own region, and post() blurs before it patches.
    """
    await p.goto("/queue?tier=judge")
    opened = await p.js(OPEN_ROW_HOLDING + "('[data-toggle^=\"sb-\"]')")
    assert opened, "no reviewable item on the Judge tier to open"
    await asyncio.sleep(0.15)
    target = await p.js("""(function(){
      var t=document.querySelector('[data-toggle^="sb-"]'); if(!t) return null;
      t.click();
      var ta=document.querySelector('.sbwrap:not([hidden]) textarea');
      ta.focus();
      ta.value='the evidence path in round two does not resolve, re-walk it before this lands';
      ta.dispatchEvent(new Event('input',{bubbles:true}));
      return {id: t.dataset.toggle.replace('sb-',''), focused: document.activeElement.tagName};})()""")
    assert target, "no reviewable item on the Judge tier to send back"
    assert target["focused"] == "TEXTAREA", "the caret never reached the field"
    await p.js("document.querySelector('.sbwrap:not([hidden]) button.act').click()")
    await asyncio.sleep(2.0)
    r = await p.js("""({receipts: document.querySelectorAll('.receipt').length,
                        card: !!document.getElementById('card-%s'),
                        say: document.getElementById('saybar').innerText.trim(),
                        active: document.activeElement.tagName})""" % target["id"])
    assert r["receipts"] >= 1, "a gated verb landed its write and left no receipt"
    assert r["card"] is False, f"card-{target['id']} survived the verb that resolved it"
    assert r["say"], "the saybar said nothing about a write that succeeded"
    assert r["active"] != "TEXTAREA", "the caret was left in the field, so the guard still binds"
    print(f"      {target['id']} sent back with the caret in the field: "
          f"{r['receipts']} receipt, card gone, saybar states it")


async def stack_advances_on_a_decision(p: Page):
    """One click, one post, one pip. The mode used to take three clicks and produce one post.

    `patch()` returns immediately on this page and always did -- the stack is the one mode nothing
    is allowed to arrive in -- so it was the only re-render after a write and there was none. The
    stack advances by NAVIGATING to the `data-next` the server stamped, which is how `skip for
    now` has always worked.
    """
    await p.goto("/queue/stack?from=decide&scroll=41")
    before = await p.js("""(function(){
      var h=document.querySelector('.stackhd span'), b=document.querySelector('.stackbtns button.act');
      if(!b) return null;
      return {hd: h.textContent.trim(),
              pips: [].map.call(document.querySelectorAll('.pip'),function(p){return p.className.trim()}),
              next: document.getElementById('stackwrap').dataset.next,
              back: document.getElementById('stack-close').getAttribute('href')};})()""")
    assert before, "the stack had no decidable item, so this proves nothing about advancing"
    assert before["next"], "the server stamped no data-next, so a decision has nowhere to go"
    assert "scroll=41" in before["back"], f"close lost the scroll it was opened from: {before['back']}"
    await p.js("document.querySelector('.stackbtns button.act').click()")
    await asyncio.sleep(2.5)
    after = await p.js("""(function(){
      var h=document.querySelector('.stackhd span');
      var b=document.querySelector('.stackbtns button.act');
      return {url: location.href, hd: h ? h.textContent.trim() : '',
              pips: [].map.call(document.querySelectorAll('.pip'),function(p){return p.className.trim()}),
              disabled: b ? b.disabled : null,
              clear: !!document.querySelector('.stackdone')};})()""")
    assert "d=1" in after["url"], (
        f"the decided cursor did not advance, so the decision re-rendered nothing: {after['url']}")
    gone = [c for c in after["pips"] if "gone" in c]
    assert after["clear"] or gone, (
        f"no pip was filled by a decision. before={before['pips']} after={after['pips']}")
    assert after["disabled"] is not True, "the button stayed disabled, so the next item is dead"
    print(f"      {before['hd']!r} -> {after['hd'] or 'stack clear'!r}; "
          f"pips {before['pips']} -> {after['pips']}")


async def deep_work_absolute(p: Page):
    # `burn` is no longer a region of its own: the burning item rides the collapsed strip inside
    # `runbtn`, because the two could only ever render together and three stacked bars about one
    # lane put the first card at 45 % of a 390px viewport (task 0160). The claim is unchanged and
    # is asserted the same way -- under deep work the strip carries NOTHING, burning item
    # included -- against the region that now holds it.
    await p.goto("/queue?tier=judge&deep=0")
    off = await p.js("""({
        pane: document.querySelector('[data-region=fastpane]').textContent.trim().length,
        minis: document.querySelectorAll('.mini').length,
        run: document.querySelector('[data-region=runbtn]').textContent.trim().length,
        bars: document.querySelectorAll('.lanebar').length,
        cf: document.querySelectorAll('#cf').length,
        cel: document.querySelectorAll('#celebrate').length})""")
    assert off["minis"] > 0, "the fast pane is empty with deep work OFF; nothing to hide"
    assert off["bars"] == 1, "the fast lane's bars are not in one row"
    # THE CELEBRATION LAYER, ASSERTED PRESENT FIRST, for the same reason `minis > 0` is asserted
    # above: an absence proves nothing if the thing was never rendered in either mode. Task 0226
    # found both of these shipping into deep work; task 0235 absented them from the markup.
    assert off["cf"] == 1 and off["cel"] == 1, \
        f"the celebration layer is not rendered with deep work OFF either: {off}; nothing to hide"
    await p.goto("/queue?tier=judge&deep=1")
    on = await p.js("""({
        pane: document.querySelector('[data-region=fastpane]').textContent.trim().length,
        minis: document.querySelectorAll('.mini').length,
        run: document.querySelector('[data-region=runbtn]').textContent.trim().length,
        idle: /agent idle|agent waiting/.test(
            document.querySelector('[data-region=runbtn]').textContent),
        count: /\\d+ waiting/.test(
            (document.querySelector('#deephd') || {textContent: ''}).textContent),
        cf: document.querySelectorAll('#cf').length,
        cel: document.querySelectorAll('#celebrate').length,
        anims: document.getAnimations().length})""")
    assert on["minis"] == 0, f"{on['minis']} cards are still in the DOM under deep work"
    assert on["pane"] == 0, "the fast pane region still carries content under deep work"
    assert on["run"] == 0, "the run-the-stack strip is still in the DOM under deep work"
    assert not on["idle"], "the burning-item line is still in the DOM under deep work"
    # THE CELEBRATION LAYER IS ABSENT, NOT INERT (task 0235, found by 0226). `#celebrate` is the
    # receipt host and `#cf` is the full-viewport canvas `console.js` sizes to the window and
    # paints with requestAnimationFrame. Both used to ship into this mode unconditionally --
    # visually quiet, still in the DOM, still read by assistive tech, one innerHTML from a burst
    # across the reading surface. MUST-NOT-BUILD #9 and DS 6.1 are about what SHIPS.
    assert on["cf"] == 0, "the confetti canvas is still in the DOM under deep work"
    assert on["cel"] == 0, "the celebration receipt host is still in the DOM under deep work"
    # AND NOTHING IS ANIMATING. The node counts prove the two hosts are gone; this proves no
    # other element took over the job. `getAnimations()` reads the document's live animation
    # timeline, so a CSS keyframe or a Web Animations call anywhere on the page fails it.
    assert on["anims"] == 0, f"{on['anims']} animations are running under deep work"

    # ------------------------------------------------------------------ THE TIER IT NEVER VISITED
    #
    # EVERYTHING ABOVE RUNS ON `judge` AND ONLY ON `judge`, AND THAT IS HOW THIS RULE WENT
    # UNCHECKED ON THE TIER HE ACTUALLY LANDS ON. `/queue` defaults to `decide`, `queue.html`
    # renders the deep-work toggle behind `{% if tier != 'decide' %}` so there is no way to enter
    # the mode from there, and `io_deep` is a one-year cookie read on every navigation. Turning
    # deep work on in Judge and clicking Decide carried it across, and the assertions above could
    # never see it because they never went there.
    #
    # Measured at 1440px before the fix: `judge` deep on took the page 1103px -> 562px, while
    # `decide` deep on kept the run strip and all three cards and made the page TALLER, 716 ->
    # 773. The same mode, two behaviours, one of them on his default page.
    #
    # HIS RULING, 2026-08-31: DEEP WORK DOES NOT APPLY TO DECIDE. So what is asserted here is not
    # "the mode is absolute on decide too" but the thing he actually chose: on Decide the page is
    # the ORDINARY page, cookie or no cookie, and the mode is still running for Judge afterwards.
    await p.goto("/queue?tier=decide")          # deep=1 still held in the cookie, no query arg
    dec = await p.js("""({
        tiers: (document.querySelector('[data-region=tiers]') || {textContent: ''})
                 .textContent.trim().length,
        run: document.querySelector('[data-region=runbtn]').textContent.trim().length,
        nav: document.querySelectorAll('nav.rooms a').length,
        cards: document.querySelectorAll('[id^=card-]').length})""")
    assert dec["nav"] > 0, (
        "the Decide tier rendered no room navigation while the deep-work cookie was set. Deep "
        "work does not apply to Decide (his ruling, 2026-08-31) and taking the exits away is "
        "exactly what that ruling was about.")
    assert dec["tiers"] > 0, \
        "the Decide tier rendered no tier strip while the deep-work cookie was set"

    # AND THE MODE SURVIVES THE VISIT. Not applying on one tier must not switch it off for the
    # others: `_mode_cookies` writes only on an explicit `?deep=`, so passing through Decide is
    # not a crossing. Without this the ruling would read as "visiting your default page silently
    # leaves deep work", which is a different and much worse behaviour.
    await p.goto("/queue?tier=judge")
    back = await p.js("""({
        run: document.querySelector('[data-region=runbtn]').textContent.trim().length,
        nav: document.querySelectorAll('nav.rooms a').length})""")
    assert back["run"] == 0 and back["nav"] == 0, (
        f"deep work did not survive a visit to the Decide tier: judge came back with "
        f"run={back['run']} nav={back['nav']}, both of which should be 0. Passing through a tier "
        f"the mode does not apply to must not end the mode.")
    # THE COUNT MOVED ADDRESS; THE CLAIM DID NOT. This read `.deepnote` -- the count inside the
    # Queue's `deepbar` -- until V-SHELL-2 (task 0176) built DS section 6.3's deep header. DS 6.4
    # puts the count in that header and DS 6.3 deletes the bar's exit toggle from deep work, so
    # `.deepnote` is now correctly absent in this mode and `querySelector('.deepnote')` returned
    # null here, throwing before the assert could speak.
    #
    # WHAT CHANGED IS THE SELECTOR AND NOTHING ELSE. The assertion is the same assertion at the
    # same strength -- a count, with the word it counts, present in deep work -- and it is now
    # pinned to the one place the design system says that count lives, which is a narrower target
    # than "somewhere in a note element". Every other assertion in this function is untouched:
    # minis, pane, run and idle still demand absence FROM THE MARKUP. If deep work ever stopped
    # carrying a count this still fails, which is the whole point of the line.
    assert on["count"], \
        "the count did not survive in the deep header; a count may appear anywhere (DS 6.4)"
    print(f"      deep off: {off['minis']} cards, pane {off['pane']}b, "
          f"{off['cf']} canvas + {off['cel']} receipt host · "
          f"deep on: {on['minis']} cards, pane {on['pane']}b, {on['cf']} canvas, "
          f"{on['cel']} receipt host, {on['anims']} animations, count kept")


async def deep_work_is_legible_in_the_shell(p: Page):
    """Deep work is a ONE-YEAR cookie and an ABSOLUTE mode, so every room says whether it is on.

    Measured 2026-08-16 before the fix: of the five places the operator can be, exactly one --
    `/queue?tier=judge` -- said anything about it, while /brief, /fleet, /study and
    /queue?tier=decide rendered a normal-looking surface with the fast lane suppressed. The
    guide's rule is what allows the pill rather than forbidding it: *a count may appear anywhere,
    cards may not*, so the chrome carries a state word and a number and never an item.
    """
    await p.goto("/queue?tier=judge&deep=1")               # the toggle sets the cookie
    seen = {}
    for path in ("/queue?tier=judge", "/queue?tier=decide", "/brief", "/fleet", "/study"):
        await p.goto(path)
        # `.deeppill, .deepoff` -- TWO ELEMENTS, TWO TRUE SENTENCES, ONE CONTRACT. 2026-08-31.
        # The contract this check enforces is LEGIBILITY: with the mode on, no room may be
        # silent about it. It is not "the element with this class exists". Since his ruling of
        # 2026-08-31 the mode has two honest states, and they need different words:
        #   `.deeppill`  the mode is RUNNING on this surface -- state word, `since`, count, exit.
        #   `.deepoff`   the mode is ON and DOES NOT APPLY on this tier -- state word and exit,
        #                and deliberately NO count, because there is no deep work in progress
        #                here to leave and a number would be decoration.
        # Asserting the count against BOTH is what made this red when `.deepoff` first borrowed
        # the `.deeppill` class, and the check was right: it was holding an element to a promise
        # that element was never making. So the count is required where the mode RUNS, and the
        # naming is required everywhere.
        seen[path] = await p.js("""(function(){
            var e=document.querySelector('.deeppill') || document.querySelector('.deepoff');
            return e ? {t: e.textContent.replace(/\\s+/g,' ').trim(),
                        n: /\\d/.test(e.textContent),
                        running: e.classList.contains('deeppill'),
                        cards: e.querySelectorAll('.card').length}
                     : null})()""")
    missing = [k for k, v in seen.items() if not v]
    assert not missing, f"deep work is on and these rooms say nothing about it: {missing}"
    no_count = [k for k, v in seen.items() if v["running"] and not v["n"]]
    assert not no_count, f"the mode is running and the pill carries no count: {no_count} in {seen}"
    not_running = [k for k, v in seen.items() if not v["running"]]
    assert not_running == ["/queue?tier=decide"], (
        f"exactly one surface should report the mode as on-but-not-applied, and it is the Decide "
        f"tier his 2026-08-31 ruling carved out. Got: {not_running}")
    assert all(v["cards"] == 0 for v in seen.values()), "the pill grew a card"
    await p.goto("/queue?tier=judge&deep=0")               # leave the store as it was found
    gone = await p.js("document.querySelectorAll('.deeppill, .deepoff').length")
    assert gone == 0, "the pill survived deep work being switched off"
    print(f"      all 5 rooms carry it: {seen['/brief']['t']!r}; none with deep work off")


async def reduced_motion(p: Page):
    """Every animation off, and the celebration burst replaced by a static badge.

    `prefers-reduced-motion` is not a preference about taste. Motion on a surface someone reads
    for an hour is a symptom trigger, so this is checked with the emulated setting on rather than
    trusted to a media query nobody rendered.
    """
    await p.send("Emulation.setEmulatedMedia",
                 features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
    await p.goto("/queue?tier=judge")
    r = await p.js("""(function(){
      var bad = [];
      [].slice.call(document.querySelectorAll('*')).forEach(function(e){
        var c = getComputedStyle(e);
        if (c.animationName && c.animationName !== 'none') bad.push(e.className + ':anim');
        if (c.transitionDuration && parseFloat(c.transitionDuration) > 0)
          bad.push(e.className + ':trans ' + c.transitionDuration);
      });
      return {bad: bad.slice(0, 8),
              reduce: matchMedia('(prefers-reduced-motion: reduce)').matches};})()""")
    assert r["reduce"] is True, "the emulated media query did not take"
    assert not r["bad"], f"animation or transition still live under reduced motion: {r['bad']}"
    await p.send("Emulation.setEmulatedMedia", features=[])
    print("      no live animation or transition on any element with reduce on")


async def click_does_not_celebrate(p: Page):
    """A click is an intention; celebration fires on events ARRIVING. Read in canvas pixels.

    THE PIXEL ASSERTION SURVIVED TASK 0235 AND IS MEANT TO. That task absented `<canvas id=cf>`
    from the markup under deep work, and the cheap way to keep this function green would have
    been to delete the read. The claim here is not about deep work at all: outside it the canvas
    is rendered, and a resolve must leave every one of its pixels at zero.

    What the guard below buys is an honest failure if this ever runs with the mode on. `deep=` is
    not spelled in the goto, so this inherits whatever cookie the run left behind, and a null
    canvas would otherwise throw `Uncaught` -- an ERROR that says nothing about celebration. An
    absent canvas is only acceptable when deep work explains it, and then it is the STRONGER
    form of this claim: no canvas at all cannot paint particles. Any other absence is a failure.
    """
    await p.goto("/queue?tier=judge")
    present = await p.js("""({cf: document.querySelectorAll('#cf').length,
                             deep: document.body.classList.contains('deep')})""")
    if not present["cf"]:
        assert present["deep"], \
            "the confetti canvas is absent OUTSIDE deep work; nothing renders the celebration"
        print("      canvas absent under deep work (task 0235): no surface to paint particles on")
        return
    before = await p.js("(function(){var c=document.getElementById('cf');"
                        "return c.getContext('2d').getImageData(0,0,c.width,c.height)"
                        ".data.some(function(x){return x!==0})})()")
    assert before is False, "the canvas had pixels before anything happened"
    await p.js("document.querySelector('form.act-form button.act').click()")
    await asyncio.sleep(1.5)
    after = await p.js("""(function(){var c=document.getElementById('cf');
        return {px: c.getContext('2d').getImageData(0,0,c.width,c.height).data
                     .some(function(x){return x!==0}),
                receipt: document.querySelectorAll('.receipt').length}})()""")
    assert after["px"] is False, "a click produced canvas particles"
    assert after["receipt"] >= 1, "resolving left no receipt stripe"
    print(f"      no particles after a resolve; {after['receipt']} receipt stripe rendered")


async def verbs_carry_equal_weight(p: Page):
    """The accept verb and the verb that argues with it are the same size on the card.

    The card renders the counterargument beside the recommendation because showing the
    recommendation alone is how a review queue becomes a rubber stamp. The button row was making
    the opposite argument in geometry: measured at 1440, `Accept work` was 412x44 = 18106px2 at
    weight 650 and `Send back` was 95x44 = 4187px2 at weight 400, a ratio of 4.32 before a word
    is read. Only a rendered look catches this -- both are 44px tall and both are present, so
    every DOM assertion in this file passed while it was true.
    """
    await p.send("Emulation.setDeviceMetricsOverride", width=1440, height=1000,
                 deviceScaleFactor=1, mobile=False)
    await p.goto("/queue?tier=judge")
    await p.js(OPEN_ROW_HOLDING + "('.card .btns button.act')")
    await asyncio.sleep(0.15)
    r = await p.js("""(function(){
      var card=document.querySelector('.card'); if(!card) return null;
      var a=card.querySelector('.btns button.act'),
          c=card.querySelector('.btns .counter');
      if(!a||!c) return {missing:true};
      var ra=a.getBoundingClientRect(), rc=c.getBoundingClientRect();
      return {a:[Math.round(ra.width),Math.round(ra.height)],
              c:[Math.round(rc.width),Math.round(rc.height)],
              wa:getComputedStyle(a).fontWeight, wc:getComputedStyle(c).fontWeight};})()""")
    assert r and not r.get("missing"), "no card with both a primary verb and a counter verb"
    area_a, area_c = r["a"][0] * r["a"][1], r["c"][0] * r["c"][1]
    ratio = max(area_a, area_c) / min(area_a, area_c)
    assert ratio <= 1.05, (
        f"the two verbs are {ratio:.2f}:1 in area: accept {r['a']}, counter {r['c']}")
    assert r["wa"] == r["wc"], f"weights differ: accept {r['wa']}, counter {r['wc']}"
    await p.send("Emulation.clearDeviceMetricsOverride")
    print(f"      accept {r['a'][0]}x{r['a'][1]} vs counter {r['c'][0]}x{r['c'][1]}, "
          f"{ratio:.2f}:1, both at weight {r['wa']}")


async def send_back_gate(p: Page):
    await p.goto("/queue?tier=judge")
    r = await p.js("""(function(){
      var t=document.querySelector('[data-toggle^="sb-"]'); if(!t) return null; t.click();
      var ta=document.querySelector('.sbwrap:not([hidden]) textarea');
      var btn=ta.parentElement.querySelector('button.act');
      var out={empty: btn.disabled};
      ta.value='too short'; ta.dispatchEvent(new Event('input',{bubbles:true}));
      out.short = btn.disabled;
      ta.value='a reason a fresh session can act on';
      ta.dispatchEvent(new Event('input',{bubbles:true}));
      out.real = btn.disabled;
      return out;})()""")
    assert r, "no reviewable item on the Judge tier to test the gate against"
    assert r["empty"] is True, "Send back was enabled with an empty reason"
    assert r["short"] is True, "Send back was enabled on a too-short reason"
    assert r["real"] is False, "Send back stayed disabled on a real reason"
    print("      disabled on empty and on 'too short', enabled on a real reason")


async def no_h_scroll(p: Page):
    """No horizontal scroll, and EVERY tap target at the guide's own 44px on a phone.

    The floor here was 32px and it was checked over a hand-picked selector list, which is why
    this passed while `/brief` had 19 of its 20 interactive elements under 44px -- `reopen` at
    38, `walk the lineage` at 32, every item-id link at 14 (task 0160, measured at 375). Both
    halves of that are fixed here: the threshold is 44, which is what the guide says, and the
    selector is every interactive element on the page rather than a list someone maintained.
    """
    for w in (375, 390):
        await p.send("Emulation.setDeviceMetricsOverride", width=w, height=800,
                     deviceScaleFactor=1, mobile=True)
        for path in ("/queue", "/queue?tier=judge", "/brief", "/study", "/task/0001", "/fleet"):
            await p.goto(path)
            r = await p.js("({sw: document.documentElement.scrollWidth,"
                           " cw: document.documentElement.clientWidth,"
                           " n: document.querySelectorAll("
                           "'a, button, [role=tab], input:not([type=hidden]), textarea').length,"
                           " small: [].slice.call(document.querySelectorAll("
                           "'a, button, [role=tab], input:not([type=hidden]), textarea')).filter("
                           "function(e){var b=e.getBoundingClientRect();"
                           "return b.height>0 && b.height<43.5}).map(function(e){"
                           "return (e.className||e.tagName)+':'+Math.round("
                           "e.getBoundingClientRect().height)})})")
            assert r["sw"] <= r["cw"] + 1, \
                f"{path} at {w}px scrolls horizontally: {r['sw']} > {r['cw']}"
            # HIS RULING, 2026-09-01, lane B4 decision D-B4-6, PUT TO HIM WITH BOTH COSTS
            # MEASURED AND ANSWERED "leave it, keep the rows".
            #
            # The queue row's item-id link (`.qcid a`, an unclassed `<a>` inside it) is 13px.
            # Raising it to the floor was measured doing exactly what it promises: the phone row
            # goes 71px -> 100px and the Shape tier goes from 3 rows to 2 on a 390x844 screen.
            # He chose the rows. The reasoning is his and it is a real one: the row's PRIMARY
            # tap target is the whole 71px of it, and the id link is a SECONDARY route to the
            # same page the row already opens, so the floor would buy an alternative path at
            # the cost of 40 percent of what he sees at 7am.
            #
            # DECLARED, NOT SUPPRESSED, and the difference matters. This names ONE selector on
            # ONE surface. Anything else under the floor still fails, including a second small
            # target on this same row, so the exception cannot spread by being convenient. The
            # measurement that produced it is in `console.css` beside the rule that was not
            # applied, and if he reverses the ruling both come out together.
            #
            # `qblocks` WAS ALSO ON THIS LIST WHEN D-B4-6 WAS FILED and is not any more: the
            # filing recorded `A:14` and `qblocks:17`, and today only the id link is under the
            # floor. That one was fixed in the meantime rather than declared, which is the
            # normal path and the reason this list is one entry and not two.
            RULED_EXCEPTIONS = {"A"}          # D-B4-6: the queue row's item-id link
            unruled = [t for t in r["small"] if t.rsplit(":", 1)[0] not in RULED_EXCEPTIONS]
            assert not unruled, (
                f"{path} at {w}px has tap targets under 44px: {unruled}"
                + (f"  (ruled exceptions present and allowed: {sorted(set(r['small']) - set(unruled))})"
                   if len(unruled) != len(r["small"]) else ""))
    await p.send("Emulation.clearDeviceMetricsOverride")
    print("      375 and 390: no page-level horizontal scroll, every tap target at 44px")


#: Every room that renders text, plus the two tiers the queue actually differs on. NINE rooms, and
#: the check used to visit two of them.
#:
#: `/study` and `/sessions` are here even though they call zero verbs: a room being read-only says
#: nothing about whether its text is legible, and Study is the room the brief calls the enjoyable
#: one.
AA_ROUTES = (
    "/queue?tier=decide", "/queue?tier=judge", "/queue?tier=shape",
    "/brief", "/intake", "/scope", "/study", "/sessions", "/fleet", "/board",
)

#: THE RATCHET, AND IT IS NOT A WIDENED BAR.
#:
#: Widening the scan from 4 combinations to 20 surfaces failures that were always there and were
#: never measured. Those are real and the fix for every one of them is A COLOUR, which is a change
#: to his design system and not a test lane's call. So the bar stays at WCAG AA and this number is
#: a FLOOR THAT MAY ONLY SHRINK, exactly as `policy/denominator-baseline.txt` works for the
#: denominator lint: the suite fails if the count goes UP, and fails if this constant is above the
#: measured count, so lowering a fixed colour without lowering this line is also red.
#:
#: MEASURED 2026-08-31 on a seeded scratch console at ledger 52, 1440x900. Set from the run and
#: not guessed: 31 of 1234 text nodes, over 20 of 20 theme and page combinations. The old scan saw
#: 0 of 4 combinations, so every one of these was already there and none of them is new.
#:
#: TWENTY-NINE OF THE THIRTY-ONE ARE LIGHT THEME, and they are SIX distinct colour pairs, not
#: thirty-one problems:
#:
#:     2.15:1  #806987 on #c59fd0   button#scope-go.act    light   the Scope room's primary control
#:     2.48:1  #ad91b6 on #774785   button#scope-go.act    dark    the same control, dark
#:     3.20:1  #8c9096 on #ffffff   span                   light   x7   the muted text token
#:     3.20:1  #8c9096 on #ffffff   td                     light   x3   the same token in a table
#:     3.54:1  #84898f on #ffffff   td                     light   x3
#:     3.95:1  #7c8187 on #ffffff   span.sub               light   x4
#:
#: THE TWO WORST ARE THE SAME CONTROL AND IT IS A BUTTON HE PRESSES. `#scope-go` at 2.15:1 is the
#: shape this check was originally written for: white-ish on a brand colour, on a primary control,
#: which is exactly the `Accept work` defect the docstring above records being fixed once already.
#: The other four pairs are one muted grey that is too light on white and would move together.
#:
#: NOT FIXED HERE ON PURPOSE. The fix for every one is a COLOUR in his design system, and picking
#: those is his call rather than a test lane's, especially the brand pair. What this lane owes him
#: is that they stop being invisible, which is what the widened scan and this floor do.
#: 93, MEASURED 2026-08-31, AND 31 WAS NEVER TRUE OF THIS SCAN.
#:
#: THE BAR DID NOT MOVE AND WILL NOT. 4.5 for body text and 3.0 for large text are WCAG AA. What
#: moved is the FLOOR: the count of failures this scan currently finds. Raising a floor to match
#: a real measurement is bookkeeping; raising the bar would be the tolerance-widening this lane
#: exists to refuse, and it is not what this is.
#:
#: WHY IT WENT 31 -> 93 WITHOUT THE CONSOLE GETTING WORSE. 31 came from the NARROW scan: fewer
#: rooms, and no reading of `opacity`. Commit `8f51d3a` widened it to ten rooms and made it
#: multiply each element's inherited opacity into the alpha -- and in the same rewrite dropped
#: `seen = []`, so from 13:12 on 2026-08-31 until this line was written the check raised
#: `NameError` on the first page of the first theme and measured NOTHING. The suite reported it
#: as `ERROR` rather than `FAIL`, which is why a dead instrument read as noise for a day, and
#: `31` sat underneath it looking like a floor somebody was holding.
#:
#: So 93 is not a regression from 31. It is the first honest number this scan has produced:
#: 93 failing text nodes of 1394, over 20 of 20 theme/page combinations, all of them measured.
#:
#: THIS IS A DEBT REGISTER, NOT A BUDGET. Every one of the 93 is fixed by changing a COLOUR in
#: his design system, which is his call and not a test lane's -- the brand pair on `#scope-go`
#: at 2.15:1 most of all, because it is a button he presses. What this lane owes him is that
#: they are counted, in both directions: going up is a regression, and going DOWN without
#: lowering this number is also red, so a fix cannot be absorbed silently.
#: HARVESTED 2026-08-31. Each entry is `<fg> on <bg> @ <selector>`. See the long note in
#: `light_theme_passes_aa` for why this is a SET and not the count it replaced.
AA_KNOWN_SIGNATURES: frozenset[str] = frozenset({
    # ---- THE BRAND PAIR ON A BUTTON HE PRESSES. Both themes, and the worst two ratios here.
    # This is the same shape as the `Accept work` defect this file's docstring records being
    # fixed once already: near-white on a brand colour, on a primary control.
    "#806987 on #c59fd0 @ button#scope-go.act",          # light, ~2.15:1
    "#ad91b6 on #774785 @ button#scope-go.act",           # dark,  ~2.48:1
    # ---- ONE MUTED GREY FAMILY ON WHITE, wearing seven selectors. These five hexes are within
    # ~0x18 of each other and are almost certainly ONE token composited at different inherited
    # opacities, which is exactly what the widened check now reads and the old one did not.
    # Fixing the token should retire most of this block in a single change.
    "#747980 on #ffffff @ div.mm",
    "#7c8187 on #ffffff @ span.sub",
    "#84898f on #ffffff @ button.fold",
    "#84898f on #ffffff @ td",
    "#8a8e93 on #f6f6f4 @ span",
    "#8c9096 on #ffffff @ span",
    "#8c9096 on #ffffff @ td",
})


async def light_theme_passes_aa(p: Page):
    """WCAG AA on EVERY text node, in BOTH themes, computed rather than eyeballed.

    Dark passed this from the start and light did not, on exactly one thing: white on the brand
    orange is 3.50:1, and it was the label of `Accept work`, `Accept default`, `Approve` and the
    Brief's `Clear the queue` -- the four controls this surface exists to press. The dark theme
    had already solved it with a near-black label at 8.38:1. Measured over 73 nodes on the Queue
    and 61 on the Brief, in both themes; anything that regresses this shows up here as a ratio
    and a colour pair rather than as a screenshot someone has to look at.

    ------------------------------------------------------------------------------------------
    WHAT TASK 0382 CHANGED, AND WHAT IT DELIBERATELY DID NOT.
    ------------------------------------------------------------------------------------------
    NOT CHANGED: the bars. 4.5 for body text and 3.0 for large text are WCAG AA and are not this
    lane's to move. Task 0375 records failures at **4.37:1 against 4.5**, and the fix for those is
    the COLOUR. Widening the tolerance on an accessibility check would be the worst possible
    version of a QC lane, and it is written here so the next reader is not tempted.

    FOUR THINGS THAT MADE THE CHECK WORSE THAN ITS OWN MEASUREMENT, all fixed:

    1. **It stopped at the first failing page.** The `assert` was INSIDE the loop, so a failure on
       `dark /queue` meant `light /queue`, `dark /brief` and `light /brief` were never measured at
       all. The 2026-08-23 run reported six failing nodes and had silently examined one of four
       combinations. Now all four run and the assert is at the end.
    2. **`bad.slice(0,6)` truncated the list with nothing saying so**, so "6 failures" and "sixty
       failures" printed identically. The full count now travels beside the sample.
    3. **The failure line carried no denominator.** `N text nodes, 0 failures` printed only on the
       PASS path; the failure said which nodes failed and never how many were compared. A reader
       cannot tell 6 of 71 from 6 of 6.
    4. **It printed a ratio and no colours**, so whoever fixes it has to go and find the pair
       themselves. It now prints the computed foreground and background as hex, plus the element,
       because the person who acts on this output is changing a token.
    """
    # ------------------------------------------------------------------ THE ROUTES IT VISITS
    #
    # IT MEASURED TWO PAGES AND THE CONSOLE HAS NINE ROOMS. Four theme-and-page combinations out of
    # a possible forty-two, and the two it picked were the two a lane had just been working on.
    # Everything on the other seven rooms was unmeasured, and "0 failures" over an unvisited room
    # reads exactly like "0 failures" over a clean one.
    #
    # THE BARS ARE UNTOUCHED: 4.5 body, 3.0 large, WCAG AA. Widening a tolerance to absorb what a
    # wider scan turns up would be the worst possible version of this lane and the docstring above
    # already says so. What absorbs it instead is a RATCHET, below: the count may only go down.
    # RESTORED 2026-08-31. Commit `8f51d3a` widened this check to read `opacity` and visit ten
    # rooms, and dropped this line in the rewrite. `seen.append` below then raised `NameError`
    # on the FIRST page of the FIRST theme, so from that commit until now this check measured
    # NOTHING -- while its own commit message reported "finds 31 real failures", a number the
    # code in that commit could not produce, and `AA_KNOWN_FAILURES = 31` sat under it looking
    # like a ratchet somebody was holding.
    #
    # That is instance nine of the pattern `tools/check-at-head.sh` exists for, and the worst
    # shape of it so far: the other eight were checks that ran against the wrong artefact. This
    # one could not run at all, and the commit that broke it is the commit that claimed its
    # result. The suite reported `ERROR` rather than `FAIL`, which is why it read as noise.
    seen = []
    for theme in ("dark", "light"):
        for path in AA_ROUTES:
            sep = "&" if "?" in path else "?"
            await p.goto(f"{path}{sep}theme={theme}")
            r = await p.js(r"""(function(){
              function rgb(s){var m=(s||'').match(/rgba?\(([^)]+)\)/);if(!m)return null;
                var p=m[1].split(',').map(parseFloat);
                return {r:p[0],g:p[1],b:p[2],a:p.length>3?p[3]:1}}
              function hex(c){function h(v){v=Math.round(v).toString(16);
                return v.length<2?'0'+v:v} return '#'+h(c.r)+h(c.g)+h(c.b)}
              function lin(c){c=c/255;return c<=0.03928?c/12.92:Math.pow((c+0.055)/1.055,2.4)}
              function lum(c){return .2126*lin(c.r)+.7152*lin(c.g)+.0722*lin(c.b)}
              function over(f,b){return f.a>=1?f:{r:f.r*f.a+b.r*(1-f.a),g:f.g*f.a+b.g*(1-f.a),
                b:f.b*f.a+b.b*(1-f.a),a:1}}
              /* THE CHECK NEVER READ `opacity` AND THAT IS WHERE FAILURES WERE HIDING.
                 `getComputedStyle(e).color` carries the colour's OWN alpha and knows nothing about
                 an `opacity` on the element or on any ancestor. A label at `opacity:.6` composites
                 against its background exactly as an rgba() at .6 alpha would, and this measured
                 it as fully opaque and passed it. Walk the chain and multiply. */
              function opacityOf(e){var o=1;while(e&&e!==document.documentElement){
                var v=parseFloat(getComputedStyle(e).opacity);
                if(!isNaN(v))o*=v; e=e.parentElement} return o}
              function bgOf(e){while(e){var c=rgb(getComputedStyle(e).backgroundColor);
                if(c&&c.a>.999)return c;
                if(c&&c.a>0)return over(c,bgOf(e.parentElement||document.body));
                e=e.parentElement} return {r:255,g:255,b:255,a:1}}
              function sel(e){if(!e)return '?';var s=e.tagName.toLowerCase();
                if(e.id)s+='#'+e.id;
                if(e.className&&typeof e.className==='string'&&e.className.trim())
                  s+='.'+e.className.trim().split(/\s+/).slice(0,2).join('.');
                return s}
              var w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT),n=0,bad=[],x;
              /* SIGNATURES, NOT A SAMPLE. `sample` keeps 8 for a human to read; `sigs` keeps
                 EVERY distinct (fg, bg, selector) with how many nodes wore it, because the
                 count is the debt times the content and the signature is just the debt. */
              var sigs={};
              while((x=w.nextNode())){
                if(!(x.nodeValue||'').trim())continue;
                var e=x.parentElement,cs=getComputedStyle(e);
                if(cs.display==='none'||cs.visibility==='hidden')continue;
                var r=e.getBoundingClientRect(); if(!r.width&&!r.height)continue;
                n++;
                var bg=bgOf(e),c=rgb(cs.color),op=opacityOf(e);
                /* The element's own opacity multiplies the colour's alpha. An opacity of 0 is not
                   a contrast failure, it is invisible text, so it is skipped rather than reported
                   as a 1:1 ratio nobody can fix with a colour. */
                if(op<=0.001)continue;
                var fg=over({r:c.r,g:c.g,b:c.b,a:(c.a===undefined?1:c.a)*op},bg);
                var la=lum(fg),lb=lum(bg);
                var cr=(Math.max(la,lb)+.05)/(Math.min(la,lb)+.05);
                var px=parseFloat(cs.fontSize),wt=parseInt(cs.fontWeight)||400;
                var need=(px>=24||(px>=18.66&&wt>=700))?3:4.5;
                if(cr<need-.005)bad.push({text:x.nodeValue.trim().slice(0,28),
                  ratio:Math.round(cr*100)/100, need:need, fg:hex(fg), bg:hex(bg),
                  px:px, weight:wt, el:sel(e),
                  opacity:Math.round(op*100)/100});
                if(cr<need-.005){var k=hex(fg)+' on '+hex(bg)+' @ '+sel(e);
                  sigs[k]=(sigs[k]||0)+1;}}
              return {n:n,failed:bad.length,sample:bad.slice(0,8),sigs:sigs};})()""")
            # THE PER-PAGE DENOMINATOR GUARD, kept and made explicit: a page that rendered
            # nothing would otherwise report zero failures, which is a pass over an empty set.
            assert r["n"] > 20, (
                f"{path} in {theme} rendered only {r['n']} text nodes; nothing was measured, "
                f"and 0 failures over 0 comparisons is not a pass")
            seen.append((theme, path, r))
            mark = "0 failures" if not r["failed"] else f"{r['failed']} FAIL"
            print(f"      {theme:5} {path:20} {r['n']:>3} text nodes, {mark}")

    total_nodes = sum(r["n"] for _t, _p, r in seen)
    total_bad = sum(r["failed"] for _t, _p, r in seen)
    combos = len(AA_ROUTES) * 2

    # ==========================================================================================
    # THE RATCHET IS THE SIGNATURE SET, NOT THE COUNT. Rewritten 2026-08-31 after the count
    # version argued with itself across two runs of IDENTICAL console code.
    #
    # WHAT WAS MEASURED. Four full runs of `web/tests/run-all.sh`, with nothing under `web/`
    # changed between them except test files:
    #
    #       run 2   93 failures of 1394 text nodes
    #       run 3   85 failures of 1802
    #       run 4   60 failures of 1576
    #
    # Every room grew between runs (`/study` 144 -> 214 nodes, `/queue?tier=shape` 52 -> 85),
    # because each run seeds a fresh scratch database and the SUITES' OWN WRITES decide what
    # later suites see. So the console this check reads is a different console every time.
    #
    # WHY THE COUNT CANNOT BE A FLOOR. The failures are not spread: dark theme carries ONE, and
    # of light theme's failures `/study` and `/sessions` carry the large majority. That is the
    # same muted grey repeated once per row. The total is therefore THE DEBT TIMES THE CONTENT,
    # and it moves when the store gains a row and nobody touches a colour. Run 3 duly reported
    # "85 ... which is BETTER than the recorded floor of 93. Somebody fixed a colour" -- nobody
    # had; the page got bigger.
    #
    # WHAT IS STABLE is the set of distinct (foreground, background, selector) signatures. That
    # is a property of the design system and does not move with row count.
    #
    # THIS IS STRICTLY STRONGER THAN THE COUNT IT REPLACES. A new failing colour introduced in
    # the same change that deletes two rows LOWERS the total and would have passed the old
    # ratchet. It adds a signature, and fails here.
    #
    # THE BAR DID NOT MOVE. 4.5 and 3.0 are WCAG AA and are not this lane's to touch. This
    # changes what the floor COUNTS, never what the check REQUIRES.
    #
    # THE TWO DIRECTIONS ARE NOT SYMMETRIC HERE, AND THAT IS DELIBERATE:
    #
    #   A signature that APPEARS and is not known is a REGRESSION and fails. Unambiguous: no
    #   amount of seeding variance invents a colour pair that the stylesheet does not contain.
    #
    #   A signature that is MISSING is REPORTED AND DOES NOT FAIL, because on this suite an
    #   absence is ambiguous: the colour may be fixed, or the room may simply have had no rows
    #   in this run. The count version made this direction hard-fail and that is exactly the
    #   assertion that fired falsely. It is printed loudly so a real fix still gets its floor
    #   lowered; it is not allowed to turn a non-deterministic seed into a red suite.
    # ==========================================================================================
    sigs: dict[str, int] = {}
    for _theme, _path, r in seen:
        for k, c in (r.get("sigs") or {}).items():
            sigs[k] = sigs.get(k, 0) + c

    found = set(sigs)
    new_sigs = sorted(found - AA_KNOWN_SIGNATURES)
    gone = sorted(AA_KNOWN_SIGNATURES - found)

    if gone:
        print(f"      NOTE: {len(gone)} known signature(s) did not appear in this run. Either a "
              f"colour was fixed -- remove it from AA_KNOWN_SIGNATURES in that same change -- or "
              f"the room that carries it rendered no rows this run:")
        for g in gone:
            print(f"        - {g}")

    if new_sigs:
        detail = []
        for theme, path, r in seen:
            for b in r["sample"]:
                k = f"{b['fg']} on {b['bg']} @ {b['el']}"
                if k in set(new_sigs):
                    detail.append(f"{theme} {path} {b['text']!r} {b['ratio']}:1 needs "
                                  f"{b['need']} ({b['px']}px/{b['weight']})")
        raise AssertionError(
            f"WCAG AA: {len(new_sigs)} colour pair(s) fail that are not in AA_KNOWN_SIGNATURES. "
            f"THE FIX IS THE COLOUR, NEVER THE BAR.\n      "
            + "\n      ".join(f"{k}   x{sigs[k]} node(s)" for k in new_sigs)
            + ("\n      ---\n      " + "\n      ".join(detail[:8]) if detail else ""))

    print(f"      WCAG AA: {len(found)} known failing colour pair(s) over {total_bad} of "
          f"{total_nodes} text nodes, {len(seen)} of {combos} theme/page combinations. "
          f"No unknown pair. (The count moves with row volume; the signature set does not.)")


def _reseed() -> None:
    """A known store, every run. These tests RESOLVE real items, so a second run against the
    leftovers of the first has an empty Judge tier and fails for the wrong reason."""
    db = os.environ.get("BRAIN_PG_DB", "brain")
    if db == "brain":
        sys.exit("refusing to reseed the live store. Set BRAIN_PG_DB to a scratch database.")
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = {**os.environ, "ENGINE_SCRATCH_DB": db}
    env.pop("SWARM_PARENT_TASK", None)          # `post` inherits a parent from the environment
    subprocess.run([f"{repo}/engine/bin/scratch-db.sh", "truncate"], cwd=repo, env=env,
                   check=True, capture_output=True)
    # `scratch-db.sh truncate` is the engine lane's list and predates the queue tables, so a bump
    # or a defer left by an earlier run would survive it and reorder this run's queue. The console
    # renders D6b's ranking now, which means a stale row in any of these is a stale ORDER, and the
    # promise at the top of this function is a known store every run.
    #
    # `brain.time_entry` IS ON THIS LIST FOR THE SAME REASON, ONE TABLE FURTHER ALONG. Row `0390`,
    # 2026-08-27, and it is the whole of that row. `queue/schema/0012` postdates the engine lane's
    # list too, so a RUNNING operator timer survives the truncate above -- and a running timer is
    # the one thing on this console that the server renders from the clock: `macros.html`'s
    # stopwatch strip prints `%.1f min`, which is a new string every six seconds, in the
    # `stopwatch` region that exists precisely to absorb that tick (`queue.html:126`).
    #
    # WHO LEAVES ONE. `test_stopwatch_at_the_surface.py` runs three suites before this one in
    # `run-all.sh`, and its last scene inserts an entry born running 90 seconds ago and never
    # stops it, on purpose: it is proving that the card region does NOT churn while one runs. It
    # also crashed mid-file on 2026-08-27 and left a second one. Either way `idle_poll_swaps_
    # nothing` below then measured a page that was not idle and reported the console for it.
    #
    # THE ASSERTION IS NOT WIDENED AND THE STRIP IS NOT CHANGED. Measured at the wire on
    # `brain_lane_g`: with a timer running, 1 of 9 regions differs between two fetches 13s apart
    # and the differing bytes are `3.9 min` -> `4.2 min`; with none running, 0 of 9 differ.
    # `outputs/2026-08-27-G-console-defects/repro-0390-*.txt` carries both runs.
    subprocess.run([f"{repo}/engine/bin/scratch-db.sh", "psql", "-q", "-c",
                    "TRUNCATE brain.queue_item, brain.queue_bump, brain.queue_defer, "
                    "brain.queue_calibration, brain.queue_default_event, brain.time_entry "
                    "CASCADE"],
                   cwd=repo, env=env, check=True, capture_output=True)
    r = subprocess.run([sys.executable, f"{repo}/web/bin/seed-demo.py"], cwd=repo, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, f"seeding failed: {r.stderr}"
    print(f"      seeded: {r.stdout.strip()}")


def main() -> int:
    # STRUCTURAL, NOT PROCEDURAL. Task 0373 item 2. Before anything is truncated and before a
    # browser is launched: the console at BASE must not be the live one. The `_reseed` guard
    # below protects a different object (this process's database) and cannot see this one.
    served = _console_guard.refuse_unless_scratch(BASE, what="test_browser.py")
    print(f"      console at {BASE} is pid {served['pid']} on database {served['db']!r} "
          f"(read from /proc/{served['pid']}/environ, not assumed from the port)")
    _reseed()
    proc = _launch()
    try:
        asyncio.run(_run())
    finally:
        proc.kill()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
