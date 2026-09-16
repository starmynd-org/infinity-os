"""THE PHONE SURFACE AT 390px: the collapse, and deep work proven silent. Task 0114 (V8a).

Run with the console already serving, exactly like `test_browser.py`:

    BRAIN_PG_DB=brain_console python3 -m web.tests.test_phone

WHY THIS IS A SECOND FILE AND NOT MORE TESTS IN `test_browser.py`. That file's `main()` reseeds
the store and its list is ORDERED on purpose -- its comment says the last three "resolve real
items, so anything needing a reviewable item has to run before them". Everything here is a GET.
Adding read-only checks into a suite that reseeds would mean reseeding the operator's console
store to answer a question about CSS, and it would couple a phone check to a write ordering it
has nothing to do with. The Page harness, the chrome launcher and the CDP plumbing are IMPORTED
from `test_browser` rather than written again: one browser harness in the lane, two entry points.

WHAT THESE FIVE CHECKS ADD, given what `test_browser.py` already proves at 375 and 390:

  Already proven there:  no page-level horizontal scroll, and every interactive element at the
  guide's 44px, at 375 and 390, over six paths. Not repeated here.

  Not proven anywhere before this file:
    1. THE COLLAPSE BOUNDARY IS WHERE THE STYLESHEET SAYS IT IS. The two-panel layout is a single
       `@media(min-width:900px)` rule. A rule can be true at 1440 and 390 and still have moved,
       so this pins BOTH SIDES: 899 collapsed, 900 not. A boundary checked only from far away is
       a boundary nobody would notice sliding.
    2. THE COLLAPSED STRIP IS THE ONE THE MOCK SPECIFIES. `Decide N ... run them >`, one row,
       carrying the count -- not a second stack of full cards, which is what "collapse" degrades
       into when nobody pins it.
    3. DEEP WORK IS SILENT AT THE WIRE, NOT JUST IN THE DOM. This is the phone-specific half and
       it is the reason the file exists. `test_browser.py:deep_work_absolute` reads the DOM of a
       page that was loaded with deep work already on. A phone is left on a table with the tab
       open, and what arrives arrives through `GET /api/patch/queue` every three seconds. So the
       PATCH PAYLOAD is fetched and its regions are measured. A card that never renders because
       the server never sent it is a stronger claim than a card that is not in the DOM.
    4. THE MODE IS VISIBLE ON THE TIER A PHONE LANDS ON. `/` redirects to `/queue`, which is
       `tier=decide`, and `queue.html` wraps the whole deep-work bar in `{% if tier != 'decide' %}`.
       The shell pill in `base.html` is therefore the ONLY thing saying deep work is on there.
       Asserted, because if that pill ever moves the phone's landing page silently loses the
       only indication that a mode is suppressing content.

WHAT IS DELIBERATELY NOT ASSERTED, and this is a measurement rather than a concession:
`MUST-NOT-BUILD.md` #9 says "the run-the-stack strip is not rendered" under deep work, without a
tier qualifier. MEASURED 2026-08-18 at the patch wire: on `tier=judge&deep=1` the `runbtn` region
is 0 characters, and on `tier=decide&deep=1` it is 49 -- `queue.html:60` reads
`{% if tiers.decide and not (deep and tier != 'decide') %}`, so the strip survives on the Decide
tier alone. That is coherent with what deep work is FOR -- nothing from ELSEWHERE arrives -- and
it is not what the list says. Check 3 asserts the property that actually protects the operator:
under deep work the strip never carries another tier's burning item. The discrepancy between the
list's sentence and the template's condition is reported to the operator, not quietly asserted
into either shape by this lane.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request

_R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_R, os.path.join(_R, "engine"), os.path.join(_R, "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import websockets                                                       # noqa: E402

from web.tests.test_browser import BASE, Page, _launch, _ws_url         # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _console_guard                                                   # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []

PHONE = 390


async def _phone(p: Page, width: int = PHONE):
    await p.send("Emulation.setDeviceMetricsOverride", width=width, height=844,
                 deviceScaleFactor=1, mobile=(width <= 430))


def _regions(query: str) -> dict[str, str]:
    """One poll, as the client makes it. `x-console-poll` is the header `console.js` sends."""
    req = urllib.request.Request(f"{BASE}/api/patch/queue?{query}",
                                 headers={"x-console-poll": "1"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r).get("regions", {})


def _text(html: str) -> str:
    out, depth = [], 0
    for ch in html:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return " ".join("".join(out).split())


# ------------------------------------------------------------------------------- 1 + 2

async def the_collapse_boundary_is_at_900(p: Page):
    """899 collapsed, 900 not. Both sides of the one `@media(min-width:900px)` rule."""
    seen = {}
    for w in (375, PHONE, 899, 900, 1440):
        await _phone(p, w)
        await p.goto("/queue?tier=judge")
        seen[w] = await p.js("""(function(){
            var fp=document.querySelector('.pane-fast');
            var sp=document.querySelector('.split.two');
            return {pane: fp?getComputedStyle(fp).display:'(absent)',
                    cols: sp?getComputedStyle(sp).gridTemplateColumns:'(no .split.two)',
                    sw: document.documentElement.scrollWidth,
                    cw: document.documentElement.clientWidth};})()""")
    for w in (375, PHONE, 899):
        assert seen[w]["pane"] == "none", (
            f"at {w}px the fast pane is {seen[w]['pane']!r}: the two-panel layout did not "
            f"collapse, so a 20rem sidebar is competing with the queue on a phone")
        assert seen[w]["sw"] <= seen[w]["cw"] + 1, (
            f"at {w}px the page scrolls horizontally: {seen[w]['sw']} > {seen[w]['cw']}")
    for w in (900, 1440):
        assert seen[w]["pane"] != "none", (
            f"at {w}px the fast pane is still collapsed: the 900px rule has moved, and every "
            f"desktop width now renders the phone layout")
        assert "px" in seen[w]["cols"], (
            f"at {w}px .split.two is not a grid: {seen[w]['cols']!r}")
    print(f"      899 -> pane {seen[899]['pane']}, 900 -> pane {seen[900]['pane']}, "
          f"columns {seen[900]['cols']}; no h-scroll at 375/390/899")


async def the_collapsed_strip_is_one_row_carrying_the_count(p: Page):
    """Below 900 the fast lane is `Decide N ... run them >`, not a second column of cards."""
    await _phone(p)
    await p.goto("/queue?tier=judge")
    r = await p.js("""(function(){
        var b=document.querySelector('.runbtn');
        var all=[].slice.call(document.querySelectorAll('.mini'));
        var vis=all.filter(function(e){var x=e.getBoundingClientRect();
                                       return x.width>0 && x.height>0;});
        return {cls: b?b.className:'(no .runbtn)',
                txt: b?b.innerText.replace(/\\s+/g,' ').trim():'',
                h: b?Math.round(b.getBoundingClientRect().height):0,
                minis_in_markup: all.length,
                minis_visible: vis.length,
                pane: (function(){var f=document.querySelector('.pane-fast');
                                  return f?getComputedStyle(f).display:'(absent)';})(),
                wire: document.documentElement.outerHTML.length,
                total: document.querySelector('.runbtn b')?
                       document.querySelector('.runbtn b').textContent.trim():''};})()""")
    assert "collapsed" in r["cls"], (
        f"at {PHONE}px the fast lane did not render its collapsed strip: class={r['cls']!r}")
    assert "Decide" in r["txt"] and "run them" in r["txt"], (
        f"the collapsed strip does not name the tier and the act: {r['txt']!r}")
    assert r["total"].isdigit(), (
        f"the collapsed strip carries no count, so it is an affordance with no obligation "
        f"attached: {r['txt']!r}")
    # WHAT COLLAPSE MEANS HERE, MEASURED RATHER THAN ASSUMED, because the two mechanisms in
    # this shell are different on purpose and the difference is easy to assert backwards.
    # DEEP WORK removes cards from the MARKUP -- `MUST-NOT-BUILD.md` #9 argues that at length,
    # because a `display:none` card is still read by a screen reader and still shipped.
    # THE RESPONSIVE COLLAPSE does not: `.pane-fast` is `display:none` under
    # `@media(min-width:900px)` not applying, and the fast-lane cards stay in the HTML.
    # That is defensible -- `display:none` DOES remove a node from the accessibility tree, so
    # the screen-reader half of #9's argument does not apply, and the same document has to serve
    # both widths without a second render -- but it is a real cost on a phone and it is printed
    # rather than glossed. So the assertion is the property the collapse actually promises:
    # nothing from the fast lane is VISIBLE, and the strip is what the operator sees.
    assert r["pane"] == "none", f"the fast pane is {r['pane']!r} at {PHONE}px, not collapsed"
    assert r["minis_visible"] == 0, (
        f"{r['minis_visible']} fast-lane cards are VISIBLE at {PHONE}px. Collapsed means a count "
        f"in a strip; cards below the queue are a second queue on a phone")
    assert r["h"] >= 43.5, f"the strip is {r['h']}px, under the guide's 44px tap target"
    print(f"      {r['txt']!r} -- one strip, {r['h']}px, count {r['total']}; "
          f"{r['minis_visible']} fast-lane cards visible, {r['minis_in_markup']} still in the "
          f"markup behind display:none ({r['wire']} bytes of document either way)")


# ------------------------------------------------------------------------------- 3

async def the_fast_lane_appears_exactly_once(p: Page):
    """Row `0415`. The Decide count is on screen ONCE at every width, or it is a defect.

    WHAT THIS PINS THAT NOTHING ELSE DID, and it is the reason the row could have come back
    silently. `the_collapse_boundary_is_at_900` measures `.pane-fast` on both sides of the rule,
    and `the_collapsed_strip_is_one_row_carrying_the_count` measures the strip at 390. Neither
    ever asks what the STRIP's computed display is above 900px. Delete
    `@media(min-width:900px){.lanebar .runbtn.collapsed:not(.urgent){display:none}}` from
    `console.css` and both of those stay green while the operator gets his complaint back:

        "if I click on judge, I do see other things, and then I see decide off to the right.
         And then if I click on shape, I see shape, and then I see decide to the right."

    That is this repo's own seven-instance pattern -- a check that is green and never ran against
    the thing that ships -- so the invariant is asserted directly rather than implied by two
    checks that each measure one half of it.

    THE INVARIANT IS A COUNT, NOT A VISIBILITY. `strip xor pane` would pass if BOTH vanished, and
    a fast lane nobody can reach is a different defect wearing this one's clothes. So both are
    measured and the sum must be exactly 1.

    THE BOUNDARY IS WALKED FROM ONE PIXEL AWAY. 899 and 900 are the two values a media query can
    get wrong while 390 and 1440 stay right, which is why the sibling check walks them too.

    `decide` IS EXCLUDED and that is not a gap. On its own tier the fast lane is not a fast lane:
    the strip is absent from the markup entirely and the full `Run the stack` button stands in its
    place, which the `{% if tier == 'decide' %}` arm in `queue.html` does deliberately.
    """
    widths = (375, PHONE, 880, 899, 900, 901, 1440, 1920)
    bad, seen = [], {}
    for tier in ("judge", "shape"):
        for w in widths:
            await _phone(p, w)
            await p.goto(f"/queue?tier={tier}")
            r = await p.js("""(function(){
                var vis=function(e){return !!e && getComputedStyle(e).display!=='none';};
                var s=document.querySelector('.lanebar .runbtn.collapsed');
                return {strip: vis(s),
                        urgent: !!s && s.classList.contains('urgent'),
                        pane: vis(document.querySelector('.pane-fast'))};})()""")
            n = int(r["strip"]) + int(r["pane"])
            seen[(tier, w)] = n
            if n != 1:
                bad.append(f"{tier} at {w}px: strip={r['strip']} pane={r['pane']} "
                           f"urgent={r['urgent']} -> {n} copies")
    assert not bad, (
        "the Decide fast lane is not on screen exactly once:\n      " + "\n      ".join(bad) +
        "\n      Two copies is row 0415, the tier number following the operator around. "
        "Zero copies is worse: the fast lane became unreachable and no other check would say so.")
    print(f"      one copy at every width, both tiers: "
          f"{', '.join(str(w) for w in widths)} px x judge/shape")


async def deep_work_is_silent_at_the_patch_wire(p: Page):
    """What ARRIVES at a phone every three seconds under deep work. Measured at the wire.

    The DOM check in `test_browser.py` proves the page that loaded carries nothing. This proves
    the server never sends anything either, which is the claim that matters for a tab left open
    on a table: `console.js` polls `/api/patch/queue` every 3000ms and swaps whatever comes back.
    """
    off = _regions("tier=judge&deep=0")
    on = _regions("tier=judge&deep=1")
    dec = _regions("tier=decide&deep=1")

    assert _text(off["fastpane"]), "the fast pane is empty with deep work OFF; nothing to suppress"
    assert _text(off["runbtn"]), "the strip is empty with deep work OFF; nothing to suppress"

    assert _text(on["fastpane"]) == "", (
        f"the poll SENDS fast-lane content under deep work: {_text(on['fastpane'])[:120]!r}")
    assert _text(on["runbtn"]) == "", (
        f"the poll SENDS the run-the-stack strip under deep work: {_text(on['runbtn'])[:120]!r}")
    assert 'class="mini"' not in on["fastpane"] + on["list"], \
        "a fast-lane card arrived in a patch payload under deep work"

    # THE COUNT MOVED ADDRESS AND THIS LINE DID NOT FOLLOW IT, which is why it was red. Row
    # `0390`'s lane, 2026-08-27, and the correction is the one `test_browser.py:deep_work_absolute`
    # already made in the DOM: it read `.deepnote` -- the count inside the Queue's `deepbar` --
    # until V-SHELL-2 (task 0176) built DS section 6.3's deep header, and it says at length that
    # "WHAT CHANGED IS THE SELECTOR AND NOTHING ELSE". This file's wire half was never re-pointed,
    # so it kept asking the `deepbar` for a count that `queue.html:70`'s `{% if tier != 'decide'
    # and not deep %}` has correctly withheld since that task: measured here, the region is 0
    # characters under deep work and is SUPPOSED to be, because DS 3.4 makes `leave` the only exit
    # and that bar carried a second one.
    #
    # THE OTHER HALF WAS A REAL DEFECT AND IT IS FIXED IN THE SHELL, not here. The count lived in
    # `base.html`'s deep header, which was not a `data-region` at all, so no count of any kind
    # reached this wire: a phone left on a table under deep work held the number the page loaded
    # with, for the life of the tab. `base.html` now marks the count span as its own region and
    # `outputs/2026-08-27-G-console-defects/fix-deepcount-at-the-wire.txt` watches it go
    # `1 waiting` -> `0 waiting` across a real Decide resolution, which is the half a frozen
    # number would otherwise still satisfy.
    #
    # BOTH HALVES ARE ASSERTED, so this is narrower than the line it replaces rather than wider:
    # the count must ARRIVE, and the deepbar must stay SILENT.
    assert _text(on["deepbar"]) == "", (
        f"the deep-work bar is not silent under deep work: {_text(on['deepbar'])!r}. DS 3.4 makes "
        f"`leave` the only exit and that bar is where the second one used to be")
    assert "waiting" in _text(on.get("deepcount", "")), (
        f"the count did not survive in the deep header, at the WIRE: "
        f"{_text(on.get('deepcount', '<no deepcount region in the payload>'))!r}. A count may "
        f"appear anywhere; it is the one thing deep work keeps, and a count that never arrives "
        f"is a count that is frozen at page load")
    assert "deepcount" not in off, (
        "the deep header's count region is in the payload with deep work OFF, where there is no "
        "deep header to swap it into")

    # The Decide tier. `queue.html:60` keeps the strip here because the strip is about the tier
    # being read. What deep work has to guarantee is that it never carries ANOTHER tier's item,
    # which is the only part of it that is an interruption.
    strip = _text(dec["runbtn"])
    for intrusion in ("agent idle", "agent waiting", "run them"):
        assert intrusion not in strip, (
            f"under deep work the Decide strip carries {intrusion!r} -- that is the cross-tier "
            f"collapsed strip, which is another tier's burning item arriving: {strip!r}")
    assert 'class="mini"' not in dec["fastpane"] + dec["list"], \
        "a fast-lane card arrived on the Decide tier under deep work"
    print(f"      judge+deep: fastpane 0 chars, runbtn 0 chars, deepbar 0 chars, count kept at "
          f"the wire in `deepcount` ({_text(on['deepcount'])!r}) and absent from the deep=0 "
          f"payload; decide+deep strip is {strip!r}, no other tier in it")


# ------------------------------------------------------------------------------- 4

async def the_mode_is_visible_on_the_tier_a_phone_lands_on(p: Page):
    """`/` -> `/queue` -> tier=decide, where `queue.html` renders no deep-work bar at all."""
    await _phone(p)
    await p.goto("/queue?deep=1")
    r = await p.js("""(function(){
        // `.deeppill` OR `.deepoff`: the question is whether the mode is NAMEABLE on the
        // tier a phone lands on, not which of its two honest states it is in. On Decide
        // his ruling means it is never `.deeppill`, so selecting only that asserted the
        // mode must LIE about applying here in order to be visible at all.
        var pill=document.querySelector('.deeppill') || document.querySelector('.deepoff');
        var bar=document.querySelector('[data-region=deepbar]');
        var btn=document.querySelector('.deepbtn');
        return {pill: pill?pill.innerText.replace(/\\s+/g,' ').trim():'',
                bar: bar?bar.textContent.trim().length:-1,
                toggle: !!btn,
                pill_is_a_link: !!(pill && pill.closest('a'))};})()""")
    assert r["pill"], (
        "on the tier a phone lands on, NOTHING says deep work is on: `queue.html` wraps the "
        "deep-work bar in `{% if tier != 'decide' %}` and the shell pill in `base.html` is the "
        "only remaining indication. It is gone.")
    assert "deep work" in r["pill"].lower(), f"the shell pill does not name the mode: {r['pill']!r}"
    print(f"      landing tier shows {r['pill']!r}; the deepbar region is {r['bar']} chars and "
          f"the toggle is {'present' if r['toggle'] else 'ABSENT -- no off switch on this tier'}; "
          f"the pill is {'a link' if r['pill_is_a_link'] else 'not a link'}")


async def _run():
    async with websockets.connect(_ws_url(), max_size=40 * 1024 * 1024) as ws:
        p = Page(ws)
        await p.send("Page.enable")
        await p.send("Runtime.enable")
        for name, fn in [
            ("the collapse boundary is at 900, from both sides",
             the_collapse_boundary_is_at_900),
            ("the collapsed strip is one row carrying the count",
             the_collapsed_strip_is_one_row_carrying_the_count),
            ("the fast lane appears exactly once at every width (row 0415)",
             the_fast_lane_appears_exactly_once),
            ("deep work is silent at the patch wire, not just in the DOM",
             deep_work_is_silent_at_the_patch_wire),
            ("the deep-work mode is visible on the tier a phone lands on",
             the_mode_is_visible_on_the_tier_a_phone_lands_on),
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
        await p.send("Emulation.clearDeviceMetricsOverride")


NOT_RUN = 77                            # docs/SUITE-INPUT-RULE.md
SKIPPED: list[str] = []


def precondition() -> dict:
    """IS THERE A FAST LANE TO COLLAPSE AT ALL? Task 0375, confirming the theory 0373 recorded.

    THE FINDING, NOT A GUESS. On 2026-08-23 this suite reported two failures against an unseeded
    console:

        FAIL  the collapsed strip is one row carrying the count
              at 390px the fast lane did not render its collapsed strip: class='(no .runbtn)'
        FAIL  deep work is silent at the patch wire, not just in the DOM
              the strip is empty with deep work OFF; nothing to suppress

    Read the second message. It is this suite honestly reporting a ZERO DENOMINATOR and then
    calling it a failure. `test_browser.py` guards the identical condition explicitly at its
    `assert off["minis"] > 0, "the fast pane is empty with deep work OFF; nothing to hide"` and
    seeds itself through `_reseed()`. This file did neither, so an empty store read as a layout
    defect and the first failure read as a broken stylesheet.

    So the check is not deleted and the store is not silently seeded underneath it. The condition
    is measured FIRST, by name, and an unmet precondition is NOT RUN with the remedy, which is
    what `web/bin/seed-demo.py` exists for.
    """
    n = 0
    err = ""
    try:
        regions = _regions("tier=judge&deep=0")
        n = len([1 for cls in ('class="mini"',) if cls in regions.get("fastpane", "")]) \
            + regions.get("fastpane", "").count('class="mini"')
        strip = _text(regions.get("runbtn", ""))
    except Exception as exc:                                            # noqa: BLE001
        err, strip = f"{type(exc).__name__}: {exc}", ""
    return {"minis": n, "strip": strip, "error": err}


def main() -> int:
    # NO RESEED. Every check here is a GET, so this runs against whatever the console is already
    # serving. What it may NOT do is run against the operator's live console, because a GET suite
    # aimed at the live store still reports the operator's real queue as if it were a fixture --
    # and `_launch` below drives a browser at it. Task 0373.
    served = _console_guard.refuse_unless_scratch(BASE, what="test_phone.py")
    print(f"      console at {BASE} is pid {served['pid']} on database {served['db']!r}")

    pre = precondition()
    if pre["error"] or (pre["minis"] == 0 and not pre["strip"]):
        print(f"\nNOT RUN  the phone collapse and deep-work scenes")
        print(f"         condition: the console at {BASE} serves {pre['minis']} fast-lane "
              f"card(s) and a {len(pre['strip'])}-character collapsed strip with deep work OFF. "
              f"There is nothing to collapse and nothing to suppress, so 'the strip did not "
              f"render' would be a statement about the STORE, not about the layout."
              + (f" ({pre['error']})" if pre["error"] else ""))
        print(f"         remedy:    seed the store the console is on, then rerun:")
        print(f"                        ENGINE_SCRATCH_DB=<db> python3 web/bin/seed-demo.py")
        print(f"                    web/tests/run-all.sh does this before it dispatches.")
        print(f"\n0 passed, 0 failed, 4 scene(s) NOT RUN  "
              f"(precondition: {pre['minis']} fast-lane cards, needs at least 1)")
        return NOT_RUN

    print(f"      precondition: {pre['minis']} fast-lane card(s) and a collapsed strip of "
          f"{len(pre['strip'])} chars with deep work OFF, so there IS something to collapse")
    proc = _launch()
    try:
        asyncio.run(_run())
    finally:
        proc.kill()
    if len(PASS) + len(FAIL) == 0:                                      # DENOMINATOR
        print("\n0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed  "
          f"(precondition met: {pre['minis']} fast-lane card(s) on the console at {BASE})")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
