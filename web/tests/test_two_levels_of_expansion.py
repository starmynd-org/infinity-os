#!/usr/bin/env python3
"""THE SPREADSHEET QUEUE, AND ITS TWO LEVELS OF EXPANSION. Bus row `0431`.

The operator's own words from the 2026-08-28 demo are the specification, and they are two asks
that are one build (`0421` items 1 and 2):

    "a spreadsheet-like queue ... one row per item, hard edges, neutral colours", "like
    Airtable", with Notion and ROWY named beside it -- and then "first click expands the row
    inline with its actions, second opens a right-side panel", and NEVER navigate to a new page.

WHY EVERY CHECK BELOW IS TAKEN FROM A BROWSER AND NOT FROM THE HTML. Four of the five claims are
geometric or stateful and a DOM read cannot tell you whether any of them is true:

  * "one row per item" is a statement about HEIGHT. The markup was already one element per item
    before this row; what the operator was looking at was three items filling a 1000px screen.
  * "expands inline" and "opens a right-side panel" are statements about COMPUTED DISPLAY and
    about where the panel's box actually is, after a real click.
  * "never navigate to a new page" is a statement about `location.pathname` and about whether
    the document was replaced. A link that renders correctly and reloads the page passes every
    markup assertion in this repo.
  * THE REPAINT CLAIM CANNOT BE READ FROM MARKUP AT ALL, and it is the one that nearly sank the
    mechanism. `console.js` patches the `list` region by assigning `innerHTML` whenever the
    server's markup for it changes, which destroys and re-creates every row. Measured on
    chromium 1223 while building this: with `location.hash` unchanged at `#pan-0001`, emptying
    that region and assigning the identical markup back leaves BOTH the inline expansion and the
    panel at `display:none` -- the browser resolves the `:target` element once and does not
    re-resolve it when an element carrying that id is re-inserted.

    THAT WAS RECORDED HERE AS A KNOWN LIMIT ON 2026-08-28 AND CLOSED ON 2026-08-29, by the lane
    that owns `console.js` rather than only the stylesheet. It was never a cosmetic limit: on a
    board where anything moves -- an `agent waiting 12m` ticking to 13 is enough -- the row the
    operator had open shut itself every three seconds, at a moment he did not choose and for a
    reason he could not see. `console.js` re-reads the hash after every repaint and paints
    `.is-open` / `.is-panel`, which are the same rules in the same selector lists in
    `console.css`. Check 5 now asserts the row SURVIVES, and it was watched failing first: on
    this same store, with the grid in and the console.js module out, it read open false.

  * AND THE CLICK MUST NOT MOVE THE PAGE, which is check 5b and is the 0409 shape restated. A
    fragment navigation scrolls its target into view. Measured at 1440x760 on `/queue?tier=shape`
    before the fix, clicking the LAST row moved the document 122px and moved that row 122px up
    the screen, under the cursor that had just clicked it. `console.js` takes the click and
    writes the hash with `replaceState`, which does not scroll.

WHAT IS DELIBERATELY ASSERTED ABOUT WHAT IS *NOT* BUILT. `MUST-NOT-BUILD.md` item 2 -- no score
editing, no drag-to-reorder, pin and defer only -- is KEPT, and a grid is the one shape where
somebody adds a drag handle by reflex. Check 6 walks the rendered queue for `draggable`, for a
`grab` cursor, and for any control that submits `priority`, and requires zero of each. It is
here rather than only in `MUST-NOT-BUILD.md` because that file is prose and this is a gate.

Run:  BASE=http://127.0.0.1:3126 python3 -m web.tests.test_two_levels_of_expansion

The console at BASE must be on a scratch store: `_console_guard` reads the SERVING PROCESS, not
the port, and refuses `brain` and `brain_scratch`.
"""

from __future__ import annotations

import os
import sys

_R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_R, os.path.join(_R, "engine"), os.path.join(_R, "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _console_guard                                                   # noqa: E402
from playwright.sync_api import sync_playwright                         # noqa: E402

BASE = os.environ.get("BASE", "http://127.0.0.1:3126")
CHROME = os.environ.get(
    "CHROME", os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux/chrome"))

# The same viewport the 0410 suite uses, for the same reason: it is the one a near-miss was
# measured on, and a check that only ever ran at a round number would have passed that render.
VIEWPORT = {"width": 1440, "height": 1057}

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'' if ok else '   ' + detail}")
    return bool(ok)


# A COLLAPSED ROW IS A ROW. 56px is not a taste: `.qgrid` sets `min-height:44px`, which is the
# design guide's touch minimum and therefore the floor a row cannot go below, plus the 0.6rem of
# padding the grid carries. Anything materially above that is a card wearing a row's class.
ROW_MAX_PX = 72


def main() -> int:
    served = _console_guard.refuse_unless_scratch(
        BASE, what="test_two_levels_of_expansion.py")
    print(f"console {BASE} is pid {served['pid']} on {served['db']!r}\n")

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROME,
                               args=["--no-sandbox", "--hide-scrollbars"])
        page = b.new_context(viewport=VIEWPORT).new_page()
        try:
            rows_compared = run(page)
        finally:
            b.close()

    # DENOMINATOR. Every claim in this file is per-row, so a board with no rows makes all of them
    # vacuously true. A tier this suite could not find an item on is not a passing queue.
    if rows_compared == 0:                                              # DENOMINATOR
        print("\n0 comparisons made. A verdict over an empty set is not a pass.")
        print("The console at BASE rendered no queue rows on any tier: reseed the store.")
        return 2
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed  "
          f"({rows_compared} row(s) compared across the tiers that had any)")
    for f in FAIL:
        print(f"  FAIL {f}")
    return 1 if FAIL else 0


def run(page) -> int:
    compared = 0

    # ---------------------------------------------------------------- 1. one row per item
    print("--- 1. a spreadsheet, not blocks of text")
    for tier in ("decide", "judge", "shape"):
        page.goto(f"{BASE}/queue?tier={tier}", wait_until="networkidle")
        r = page.evaluate("""() => {
          const rows = [...document.querySelectorAll('.qrow')];
          return {
            n: rows.length,
            heights: rows.map(e => Math.round(e.getBoundingClientRect().height)),
            expanded: rows.filter(e => getComputedStyle(
                e.querySelector('.qexp')).display !== 'none').length,
            panels: rows.filter(e => getComputedStyle(
                e.querySelector('.qpanel')).display !== 'none').length,
            docW: document.documentElement.scrollWidth,
            winW: window.innerWidth,
          };
        }""")
        if not r["n"]:
            print(f"      {tier}: no rows on this tier, nothing to compare")
            continue
        compared += r["n"]
        tallest = max(r["heights"])
        check(f"{tier}: {r['n']} rows, the tallest {tallest}px at rest",
              tallest <= ROW_MAX_PX,
              f"heights {r['heights']}, ceiling {ROW_MAX_PX}px")
        # AT REST IS AT REST. A row that ships expanded is the surface he already had.
        check(f"{tier}: 0 of {r['n']} rows are expanded at rest",
              r["expanded"] == 0, f"{r['expanded']} already open")
        check(f"{tier}: 0 of {r['n']} panels are open at rest",
              r["panels"] == 0, f"{r['panels']} already open")
        # The grid must not be a new source of the page-level horizontal scroll this console
        # already has at 375px: a `1fr` title column whose min-content is a store string is
        # exactly how a table widens its container.
        check(f"{tier}: the grid adds no horizontal scroll at 1440 "
              f"(doc {r['docW']} vs window {r['winW']})",
              r["docW"] <= r["winW"], f"{r['docW']} > {r['winW']}")

    # --------------------------------------------- 2 and 3. the two levels, on a real click
    print("--- 2. first click expands the row INLINE, with its actions")
    page.goto(f"{BASE}/queue?tier=judge", wait_until="networkidle")
    ids = page.eval_on_selector_all(".qrow", "e => e.map(x => x.id)")
    if not check("there is a row on Judge to open", bool(ids), "no .qrow rendered"):
        return compared
    rid = ids[0]

    before_doc = page.evaluate("() => { window.__same = 1; return location.href; }")
    page.click(f"#{rid} .qopen")
    page.wait_for_timeout(120)
    lvl1 = page.evaluate("""(rid) => {
      const row = document.getElementById(rid);
      const box = s => { const e = row.querySelector(s); if (!e) return null;
        const b = e.getBoundingClientRect();
        return {d: getComputedStyle(e).display, top: b.top, bottom: b.bottom,
                width: b.width, text: (e.innerText || '').trim()}; };
      return {href: location.href, same: window.__same === 1,
              exp: box('.qexp'), btns: box('.btns'), before: box('.beforeact'),
              panel: box('.qpanel'),
              verbs: [...row.querySelectorAll('.qexp .btns button.act, .qexp .btns a.act')]
                       .map(e => (e.innerText || '').trim())};
    }""", rid)
    check("the first click did not load a new document",
          lvl1["same"] is True, "the page was replaced")
    check(f"the first click expanded {rid} inline",
          lvl1["exp"] and lvl1["exp"]["d"] != "none", "the expansion is still display:none")
    check("its ACTIONS are what it expanded with",
          bool(lvl1["btns"]) and lvl1["btns"]["d"] != "none" and bool(lvl1["verbs"]),
          f"verbs found: {lvl1['verbs']}")
    check("the first click did NOT open the panel",
          lvl1["panel"] and lvl1["panel"]["d"] == "none",
          "level two arrived on click one")
    # ROWS 0410 AND 0416 DO NOT REGRESS: the argument is above the verb and touching it, at the
    # moment the verb becomes reachable. This is the same 0..24px gap the 0410 suite measures.
    if lvl1["before"]:
        gap = lvl1["btns"]["top"] - lvl1["before"]["bottom"]
        check(f"the argument is directly above the verbs (gap {gap:.0f}px)", 0 <= gap <= 24,
              f"gap {gap:.1f}px between .beforeact and .btns")

    print("--- 3. second click opens a RIGHT-SIDE panel, and still no new page")
    page.click(f"#{rid} .qmore")
    page.wait_for_timeout(120)
    lvl2 = page.evaluate("""(rid) => {
      const row = document.getElementById(rid);
      const p = row.querySelector('.qpanel');
      const b = p.getBoundingClientRect();
      const cs = getComputedStyle(p);
      return {same: window.__same === 1, path: location.pathname + location.search,
              d: cs.display, pos: cs.position,
              left: b.left, right: b.right, width: b.width,
              winW: window.innerWidth,
              text: (p.innerText || '').trim().length,
              expStillOpen: getComputedStyle(row.querySelector('.qexp')).display !== 'none'};
    }""", rid)
    check("the second click did not load a new document either",
          lvl2["same"] is True, f"the page was replaced; now at {lvl2['path']}")
    check("the panel is open", lvl2["d"] != "none", "still display:none")
    # RIGHT-SIDE, MEASURED. "A panel" that renders under the list is not what he asked for, and
    # `display:block` alone cannot tell the two apart.
    check(f"it is on the RIGHT of the viewport (right edge {lvl2['right']:.0f} "
          f"of {lvl2['winW']}, left edge {lvl2['left']:.0f})",
          lvl2["pos"] == "fixed" and abs(lvl2["right"] - lvl2["winW"]) <= 2
          and lvl2["left"] > lvl2["winW"] / 2,
          f"position {lvl2['pos']}, box {lvl2['left']:.0f}..{lvl2['right']:.0f}")
    check("the panel carries the full context rather than a heading",
          lvl2["text"] > 60, f"only {lvl2['text']} characters in it")
    # THE SECOND CLICK MUST NOT UNDO THE FIRST. With the fragment on the panel the row itself no
    # longer matches `:target`, so this is the `:has()` rule doing its job.
    check("the inline expansion is STILL open underneath the panel",
          lvl2["expStillOpen"] is True, "opening level two closed level one")

    print("--- 4. and it closes again, without a round trip")
    page.click(f"#{rid} .qpclose")
    page.wait_for_timeout(120)
    shut = page.evaluate("""(rid) => {
      const row = document.getElementById(rid);
      return {same: window.__same === 1,
              exp: getComputedStyle(row.querySelector('.qexp')).display,
              panel: getComputedStyle(row.querySelector('.qpanel')).display};
    }""", rid)
    check("closing the panel closed the row too, with no new document",
          shut["same"] is True and shut["exp"] == "none" and shut["panel"] == "none",
          f"exp {shut['exp']}, panel {shut['panel']}, same document {shut['same']}")

    # ---------------------------------- 5. the poll, which is the check the feature lives on
    print("--- 5. the open row under a real list repaint, which is what the 3s poll does")
    page.click(f"#{rid} .qopen")
    page.wait_for_timeout(120)
    limit = page.evaluate("""async (rid) => {
      const open0 = getComputedStyle(
          document.getElementById(rid).querySelector('.qexp')).display !== 'none';
      const host = document.querySelector('[data-region="list"]');
      const html = host.innerHTML;
      host.innerHTML = '';                  /* exactly what console.js patch() does */
      await new Promise(r => setTimeout(r, 40));
      host.innerHTML = html;
      await new Promise(r => setTimeout(r, 80));
      const row = document.getElementById(rid);
      return {open0: open0, hash: location.hash, rowBack: !!row,
              open1: row ? getComputedStyle(row.querySelector('.qexp')).display !== 'none'
                         : null};
    }""", rid)
    check("the row was open before the repaint", limit["open0"] is True)
    check("the row's markup comes back after the repaint", limit["rowBack"] is True)
    check("the URL fragment survives the repaint",
          limit["hash"].endswith(rid), f"hash is {limit['hash']!r}")
    # THE CHECK THE FEATURE LIVES ON. `:target` alone reads False here and did until 2026-08-29;
    # `.is-open`, re-applied from the hash by `console.js` after the repaint, is what makes it
    # True. If this goes red the poll is closing rows under the operator again, which is the
    # defect he would meet within one minute of using this surface on a live board.
    check("THE OPEN ROW SURVIVES A LIST REPAINT (console.js re-applies the fragment)",
          limit["open1"] is True,
          "the repaint closed the row. Either the MutationObserver in console.js is gone, or "
          "the .is-open / .is-panel rules left console.css, or patch() stopped assigning "
          "innerHTML to the list region and this check is measuring nothing.")

    # ------------------------------------ 5b. AND OPENING A ROW DOES NOT MOVE THE PAGE
    # A fragment navigation scrolls its target into view, and a list the operator has scrolled is
    # exactly where that is felt. Measured at a viewport short enough for the list to scroll: the
    # 0409 incident's own geometry was a 757px-high viewport, and a check that only ever ran at
    # 1440x1057 on a seven-row board would never have seen this.
    print("--- 5b. and the click does not move the page under him")
    page.set_viewport_size({"width": 1440, "height": 760})
    page.goto(f"{BASE}/queue?tier=shape", wait_until="networkidle")
    page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(80)
    sids = page.eval_on_selector_all(".qrow", "e => e.map(x => x.id)")
    scrollable = page.evaluate(
        "() => document.documentElement.scrollHeight > window.innerHeight")
    if sids and scrollable:
        last = sids[-1]
        before = page.evaluate("""(r) => ({sy: Math.round(window.scrollY),
            top: Math.round(document.getElementById(r).getBoundingClientRect().top)})""", last)
        page.click(f"#{last} .qopen")
        page.wait_for_timeout(120)
        after = page.evaluate("""(r) => ({sy: Math.round(window.scrollY),
            top: Math.round(document.getElementById(r).getBoundingClientRect().top)})""", last)
        moved = abs(after["sy"] - before["sy"])
        check(f"opening the LAST row of a scrolled list moved the page {moved}px "
              f"(scrollY {before['sy']} -> {after['sy']}, "
              f"row top {before['top']} -> {after['top']})",
              moved == 0, "the fragment navigation scrolled its target into view")
    else:
        # DENOMINATOR, NOT A PASS. A board too short to scroll cannot answer this question, and
        # saying so is the honest verdict.
        print(f"      the shape tier is not scrollable at 1440x760 ({len(sids)} rows): "
              f"this check had nothing to measure and is NOT counted as a pass")
    page.set_viewport_size(VIEWPORT)

    # ------- 7. AND THE REASON BOX INSIDE THE OPEN ROW, WITH WHAT HE HAD TYPED IN IT
    # The row surviving the poll is half of it. `Defer`, `Bump`, `not fast`, `Answer`, `Send back`
    # and `Mark my task done` each open a panel by flipping a `hidden` ATTRIBUTE, which the repaint
    # puts back the way the server renders it, which is closed. A textarea's typed value is worse:
    # it is a property, not an attribute, so it is not in the markup at all.
    #
    # THE PAYLOAD HERE IS THE REAL `/api/patch` RESPONSE AND NOT THIS PAGE'S OWN innerHTML, and
    # that difference is the whole check. Reading innerHTML back out after opening the panel
    # captures the OPEN state, so a version of this check written the easy way passes on a console
    # where the defect is fully present. Measured that way it read `openAfter: true`; measured
    # against the server's markup, on the same page in the same second, `openAfter: false`.
    print("--- 7. the reason box in the open row, and the sentence typed into it")
    page.goto(f"{BASE}/queue?tier=judge", wait_until="networkidle")
    rids = page.eval_on_selector_all(".qrow", "e => e.map(x => x.id)")
    if rids:
        page.click(f"#{rids[0]} .qopen")
        page.wait_for_timeout(120)
        typed = "this ranks too low because the client is waiting on it"
        box = page.evaluate("""async (a) => {
          const row = document.getElementById(a.rid);
          const btn = row.querySelector('[data-toggle^="bump-"]');
          if (!btn) return {none: true};
          const pid = btn.dataset.toggle;
          btn.click();
          await new Promise(r => setTimeout(r, 60));
          const panel = document.getElementById(pid);
          const ta = panel.querySelector('textarea');
          if (ta) { ta.value = a.typed;
                    ta.dispatchEvent(new Event('input', {bubbles: true})); ta.blur(); }
          const before = {open: !panel.hidden, val: ta ? ta.value : null,
                          btn: ta ? !!panel.querySelector('button.act').disabled : null};
          const res = await fetch('/api/patch/queue' + location.search,
                                  {headers: {'x-console-poll': '1'}});
          const j = await res.json();
          document.querySelector('[data-region="list"]').innerHTML = j.regions.list;
          await new Promise(r => setTimeout(r, 200));
          const p2 = document.getElementById(pid);
          const t2 = p2 ? p2.querySelector('textarea') : null;
          /* THE GATE, AND WHY THIS WAITS FOR A REAL POLL RATHER THAN READING STRAIGHT AWAY.
             `wire()` binds the length gate to `input` AND evaluates it once at bind time, so the
             restored value reaches the button by one of two routes depending on which ran first.
             In the console, `patch()` calls `wire()` synchronously and the restore is the
             microtask after it, so the dispatched `input` is what enables the button. HERE the
             region was assigned by hand and `wire()` has not run at all, so nothing is listening
             and the button is still the server's disabled. Waiting for the next real poll runs
             `wire()`, which re-evaluates the gate against the value now in the field. Either
             order has to end with a button he can press, which is what this asserts. */
          const patches0 = window.__patches;
          for (let i = 0; i < 60 && window.__patches <= patches0; i++) {
            await new Promise(r => setTimeout(r, 200));
          }
          const p3 = document.getElementById(pid);
          const t3 = p3 ? p3.querySelector('textarea') : null;
          return {pid: pid, before: before,
                  open: p3 ? !p3.hidden : null, val: t3 ? t3.value : null,
                  btn: p3 ? !!p3.querySelector('button.act').disabled : null,
                  polled: window.__patches > patches0,
                  openImmediately: p2 ? !p2.hidden : null,
                  valImmediately: t2 ? t2.value : null};
        }""", {"rid": rids[0], "typed": typed})
        if box.get("none"):
            print("      no bump control on this row: NOT COUNTED, there was nothing to open")
        else:
            check(f"{box['pid']} was open with a reason typed into it before the repaint",
                  box["before"]["open"] is True and box["before"]["val"] == typed)
            check("the reason box is STILL open after the repaint",
                  box["openImmediately"] is True and box["open"] is True,
                  "the poll closed the panel he was reading")
            check("and the sentence he typed is still in it",
                  box["valImmediately"] == typed and box["val"] == typed,
                  f"the field came back holding {box['val']!r}")
            check("a real poll ran over the restored panel and did not undo it",
                  box["polled"] is True,
                  "no poll arrived inside 12s, so the wire()-order half is unproven")
            # A restored value with a still-disabled button is worse than a lost one: the text is
            # there and unsendable. The gate reads `input`, so the restore has to fire one.
            check("and the gated submit is enabled again, not text he cannot send",
                  box["btn"] is False, "the button came back disabled under a restored reason")

    # ------- 7b. AND A REASON THAT HAS ALREADY BEEN SENT IS NEVER PUT BACK IN FRONT OF HIM
    # The other half of check 7, and the half that could do harm rather than merely annoy.
    # `Bump` does not resolve its item, so the card stays. `post()` submits by fetch, resets the
    # form so the length gate returns to disabled-because-empty, and leaves the panel open with an
    # empty field; the next repaint closes it because that is how the server renders it. A restore
    # that remembered the panel would re-open it and REFILL it with the sentence he already sent,
    # which reads as a bump that did not go through and invites a second one. A bump is a decaying
    # additive term against the ranking, so two are not one.
    #
    # `form.reset()` fires `reset` and not `input`, so the memory cannot learn this from the
    # field; the submit is the signal. WATCHED FAILING FIRST, on this store, with that one
    # listener disabled: panel back open TRUE, field refilled with all 54 characters. With it:
    # panel closed, field empty, and the say bar still carrying the receipt for the bump that did
    # land.
    print("--- 7b. a reason already sent is not resurrected by the next repaint")
    page.goto(f"{BASE}/queue?tier=judge", wait_until="networkidle")
    rids = page.eval_on_selector_all(".qrow", "e => e.map(x => x.id)")
    if rids:
        page.click(f"#{rids[0]} .qopen")
        page.wait_for_timeout(120)
        sent = page.evaluate("""async (a) => {
          const row = document.getElementById(a.rid);
          const btn = row.querySelector('[data-toggle^="bump-"]');
          if (!btn) return {none: true};
          const pid = btn.dataset.toggle;
          btn.click();
          await new Promise(r => setTimeout(r, 60));
          const panel = document.getElementById(pid);
          const ta = panel.querySelector('textarea');
          ta.value = a.typed;
          ta.dispatchEvent(new Event('input', {bubbles: true}));
          panel.querySelector('button.act').click();
          await new Promise(r => setTimeout(r, 1800));
          const res = await fetch('/api/patch/queue' + location.search,
                                  {headers: {'x-console-poll': '1'}});
          const j = await res.json();
          document.querySelector('[data-region="list"]').innerHTML = j.regions.list;
          await new Promise(r => setTimeout(r, 250));
          const p2 = document.getElementById(pid);
          const t2 = p2 ? p2.querySelector('textarea') : null;
          return {pid: pid,
                  say: (document.getElementById('saybar') || {innerText: ''}).innerText.trim(),
                  open: p2 ? !p2.hidden : null, val: t2 ? t2.value : null};
        }""", {"rid": rids[0], "typed": "this ranks too low because the client is waiting on it"})
        if sent.get("none"):
            print("      no bump control on this row: NOT COUNTED, there was nothing to send")
        else:
            check("the bump landed and said so", bool(sent["say"]),
                  "the say bar said nothing about a write that should have succeeded")
            check("the panel does NOT come back open after the send",
                  sent["open"] is False, "a sent reason is being offered again")
            check("and the field is not refilled with the sentence he already sent",
                  not sent["val"], f"the field came back holding {sent['val']!r}")

    # ------------------------------------------ 6. MUST-NOT-BUILD item 2, on the grid itself
    print("--- 6. MUST-NOT-BUILD item 2 on a surface that looks like Airtable")
    page.goto(f"{BASE}/queue?tier=judge", wait_until="networkidle")
    prohibited = page.evaluate("""() => {
      const all = [...document.querySelectorAll('*')];
      return {
        /* THE ATTRIBUTE, NOT THE IDL PROPERTY. `HTMLElement.draggable` is TRUE BY DEFAULT on
           every `<a href>` and every `<img>`, so `e.draggable === true` counted 45 elements on
           a queue with no drag affordance of any kind and reported the console for a
           prohibition it keeps. The claim is "somebody made this draggable", and the only
           evidence of that is the author having written the attribute. */
        draggable: all.filter(e => e.hasAttribute('draggable')).length,
        grab: all.filter(e => /grab/.test(getComputedStyle(e).cursor)).length,
        sortHandles: document.querySelectorAll(
            '[data-drag],[data-sort],.draghandle,.sortable').length,
        priorityWrites: [...document.querySelectorAll('form input[name]')]
            .filter(i => i.name === 'priority' || i.value === 'set').length,
        rows: document.querySelectorAll('.qrow').length,
      };
    }""")
    check(f"no drag handle on any of the {prohibited['rows']} rows",
          prohibited["draggable"] == 0 and prohibited["sortHandles"] == 0
          and prohibited["grab"] == 0,
          f"draggable {prohibited['draggable']}, handles {prohibited['sortHandles']}, "
          f"grab cursors {prohibited['grab']}")
    check("no control on the grid writes priority or dispatches `set`",
          prohibited["priorityWrites"] == 0,
          f"{prohibited['priorityWrites']} such input(s)")

    return compared


if __name__ == "__main__":
    raise SystemExit(main())
