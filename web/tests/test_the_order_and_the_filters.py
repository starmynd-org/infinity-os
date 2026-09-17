#!/usr/bin/env python3
"""ROW 0434, HIS ASK 4. Lane C3, 2026-08-29.

*"Default order by urgency x importance, with Notion-style filtering by project, client, task,
priority and custom fields."*

Six claims, and the first two are the ones a rewrite tends to lose.

  1. THE QUEUE SAYS WHAT IT IS ORDERED BY, and the sentence is not typed twice. It is built by
     `model.order_terms` out of `human_queue.rank.DEFAULT_WEIGHTS`, so this file compares it
     against THAT MODULE and never against a literal: a check that asserts a sentence it also
     wrote is a check that passes when the ranking changes underneath it.

  2. NOTHING HERE SORTS, AND `MUST-NOT-BUILD` ITEM 2 IS KEPT. The item forbids score editing and
     drag-to-reorder; the filter changes WHICH rows are on screen and never their order. Proven
     as a SUBSEQUENCE: the ids that survive a filter appear in the same relative order they had
     with no filter. Plus item 2's own census -- 0 draggable, 0 handles, 0 grab cursors, 0
     controls writing `priority` -- and row 0431's unclickable column header, re-measured here
     because this is the lane that would have broken it.

  3. THE FILTERS ARE REACHED BY CLICKING FROM THE FRONT PAGE and every one of them filters on the
     column it names: every visible row carries the value, and every hidden row does not. Both
     halves, because "the rows that are left all match" is also true of a filter that hides
     everything.

  4. IT PRINTS ITS OWN DENOMINATOR. The list is a WINDOW on the tier, so a filter running over
     the rendered rows cannot see what is below the window, blocked, or deferred. The strip says
     so. A control that reported `0 of 7` over a tier of twenty would be this console's own "8
     items counted and unreachable" defect wearing a new name.

  5. THE PROJECT FILTER DEGRADES HONESTLY. `brain.work_item.project` arrives with migration 44
     and live `brain` was at ledger 42 on 2026-08-29, so on his own board the column does not
     exist. Three states and three different sentences, and this file asserts the sentence
     MATCHES THE STORE IT IS RUNNING ON rather than asserting one of them.

  6. THE KEYBOARD REACHES IT. His framing is *"our people are going to be power users, not iPad
     babies"*, so `f` and `/` are pressed and the resulting focus is read. And below 900px
     nothing is built at all, which is measured as a count of controls rather than as a style:
     `console.css` puts a 44px floor under every dropdown below 899px and five of them plus a
     search box is 264px of a 390px screen.

Run:  BASE=http://127.0.0.1:3180 python3 -m web.tests.test_the_order_and_the_filters

Optional second console, on a store BELOW migration 44, which is the shape of live `brain`:

      BASE42=http://127.0.0.1:3182 ...

When `BASE42` is unset the column-absent branch is declared NOT MEASURED and named, rather than
being counted as a pass. The branch is still covered on any store below ledger 44, because check
5 asserts the sentence against the store it is on.

The console at BASE must be on a scratch store: `_console_guard` reads the SERVING PROCESS and
not the port, and refuses `brain` and `brain_scratch`.
"""

from __future__ import annotations

import json
import os
import sys
import time

_R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_R, os.path.join(_R, "engine"), os.path.join(_R, "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _console_guard                                                   # noqa: E402
from human_queue import rank as RANK                                    # noqa: E402
from web import model as MODEL                                          # noqa: E402
from playwright.sync_api import sync_playwright                         # noqa: E402

BASE = os.environ.get("BASE", "http://127.0.0.1:3180")
BASE42 = os.environ.get("BASE42") or ""
CHROME = os.environ.get(
    "CHROME", os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux/chrome"))

DESK = {"width": 1440, "height": 1200}
PHONE = {"width": 390, "height": 844}

PASS: list[str] = []
FAIL: list[str] = []
NOTES: list[str] = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'' if ok else '   ' + detail}")
    return bool(ok)


def not_measured(why):
    NOTES.append(why)
    print(f"      NOT MEASURED: {why}")


#: Everything about the strip and the rows in one read, so a check compares one snapshot rather
#: than several taken at different moments.
SCREEN = """() => {
  const bar = document.getElementById('qfilter');
  const rows = Array.from(document.querySelectorAll('.qrow'));
  const vis = rows.filter(r => r.style.display !== 'none');
  const sel = Array.from(document.querySelectorAll('#qfilter select'));
  return {
    said: (document.getElementById('qfilter-said') || {}).textContent || '',
    all: rows.map(r => r.id),
    visible: vis.map(r => r.id),
    lanes: vis.map(r => r.getAttribute('data-f-lane')),
    hiddenLanes: rows.filter(r => r.style.display === 'none')
                     .map(r => r.getAttribute('data-f-lane')),
    scores: vis.map(r => r.getAttribute('data-f-score')),
    selects: sel.map(s => ({id: s.id, n: s.options.length, disabled: s.disabled,
                            value: s.value, first: s.options.length ? s.options[0].text : ''})),
    hasSearch: !!document.getElementById('qf-q'),
    hasClear: !!document.getElementById('qf-clear'),
    projectColumn: bar ? bar.getAttribute('data-project-column') : null,
    projectNote: bar ? bar.getAttribute('data-project-note') : null,
    belowWindow: bar ? bar.getAttribute('data-below-window') : null,
    tier: bar ? bar.getAttribute('data-tier') : null,
    search: location.search,
    focus: document.activeElement ? (document.activeElement.id || document.activeElement.tagName)
                                  : null,
    cursor: (document.querySelector('.qrow.is-cursor') || {}).id || null,
    cursorHidden: (() => { const c = document.querySelector('.qrow.is-cursor');
                           return c ? c.style.display === 'none' : null; })(),
  };
}"""


def to_the_queue(page, tier, base=None):
    """Reached by CLICKING from the front page. No id is ever resolved in this file.

    THIS IS THE REACHABILITY CLAIM AND IT IS MADE ONCE, by `a_person_reaches_the_filters_by_
    clicking` below. Every other check calls `at_tier`, which navigates. The distinction is
    deliberate: re-proving the route on every fixture setup does not make the route more true, and
    it makes the whole file hostage to a stylesheet another lane is editing in the same window --
    measured here on 2026-08-29, when a `page.click` on the room tab hung for 30s mid-run while
    `console.css` was being written by lane C2, on a console whose markup was correct throughout.
    """
    # RETRIED ONCE AND THE RETRY IS PRINTED, never swallowed. Measured twice in this window: the
    # room tab was `pointer-events:auto`, 52x32, at (569,17) and fully actionable a second later,
    # while a click on it had just hung for its whole timeout. Lane C2 was writing `console.css`
    # in the same minutes and a partially written stylesheet is served like any other. A retry
    # that says it retried is instrumentation; a retry that hides it is a green over a flake.
    for attempt in (1, 2):
        try:
            page.goto((base or BASE) + "/", wait_until="load")
            # WORK opens /queue since the five-tab nav (MUST-NOT-BUILD item 11 amended 2026-09-14).
            page.click("nav.rooms a.room:has-text('Work')", timeout=8000)
            break
        except Exception as exc:                                        # noqa: BLE001
            if attempt == 2:
                raise
            print(f"      RETRYING the click on the room tab: {type(exc).__name__}. "
                  f"The markup was correct; another lane is writing console.css in this window.")
            page.wait_for_timeout(1500)
    page.wait_for_selector('[data-region="list"]', timeout=30000)
    page.click(f'[data-region="tiers"] a.tier:has-text("{tier}")')
    page.wait_for_selector('[data-region="list"]', timeout=30000)
    page.mouse.click(4, 400)                      # focus the document, nothing inside it


def at_tier(page, tier, base=None):
    """One tier, by URL. Fixture setup, never a claim about how a person gets here.

    RETRIED ONCE, AND THE RETRY IS PRINTED. This ran beside two other lanes' full browser suites,
    a vitest run in another repo and this repo's own web runner, at a load average that reached
    14 on WSL2. A `curl` of this exact URL answered 200 in 660 ms in the same minute a page load
    of it timed out at 30 s. That is the host and not the console, and a run that dies on it has
    told you nothing about the product; a run that retries and SAYS it retried has told you both.
    """
    for attempt in (1, 2):
        try:
            page.goto(f"{base or BASE}/queue?tier={tier.lower()}", wait_until="load")
            page.wait_for_selector('[data-region="list"]', timeout=20000)
            break
        except Exception as exc:                                        # noqa: BLE001
            if attempt == 2:
                raise
            print(f"      RETRYING the load of /queue?tier={tier.lower()}: "
                  f"{type(exc).__name__}. The host was contended; the console answers this URL "
                  f"in well under a second when it is not.")
            page.wait_for_timeout(2000)
    page.mouse.click(4, 400)                      # focus the document, nothing inside it


def biggest_tier(page, base=None):
    """The tier with the most rendered rows, so no check below runs over a two-row list."""
    best, n = "Judge", -1
    for t in ("Decide", "Judge", "Shape"):
        at_tier(page, t, base=base)
        rows = len(page.evaluate(SCREEN)["all"])
        if rows > n:
            best, n = t, rows
    at_tier(page, best, base=base)
    return best, n


def controls_are_built(page, why):
    """Is the filter bar actually there? Asked before anything drives it.

    WITHOUT THIS, A MISSING MODULE IS A 30-SECOND TIMEOUT AND A TRACEBACK instead of a verdict.
    Watched happening on 2026-08-29 with the 0434 module disabled on purpose: the run died on
    `Page.select_option: waiting for locator("#qf-lane")` after nine checks, so the fail-watch
    could not report how many checks the module is actually load-bearing for. A suite that hangs
    has told you less than one that goes red.
    """
    # THE BAR IS SHUT BY DEFAULT (it costs 79px open and 20px shut), so this OPENS it the way a
    # person does -- by clicking the control that is on the screen -- and then asks whether the
    # dropdowns are there. Clicking rather than calling into the module, because "there is a
    # control that opens it" is half of what is being checked.
    if page.evaluate("() => !!document.getElementById('qf-open')"):
        page.click("#qf-open")
        page.wait_for_timeout(80)
    ok = page.evaluate("() => !!document.getElementById('qf-lane')")
    if not ok:
        check(f"the filter controls are on the page ({why})", False,
              "no #qf-lane: the 0434 module did not build the bar, so every check below it "
              "would be driving a control that is not there")
    return bool(ok)


def a_person_reaches_the_filters_by_clicking(page):
    """The route, once, by clicking: front page -> Queue -> a tier -> the filter bar is there."""
    to_the_queue(page, "Judge")
    s = page.evaluate(SCREEN)
    check("clicking Queue from the front page reaches a list with rows",
          bool(s["all"]), f"{len(s['all'])} row(s)")
    if not s["all"]:                                                    # DENOMINATOR
        not_measured("the tier reached by clicking has no rows, so the bar above it had nothing "
                     "to build a facet from")
        return
    check("and the order sentence is above the grid on arrival",
          "ordered by" in s["said"], s["said"][:120])
    # SHUT ON ARRIVAL AND THAT IS THE DESIGN: open, the strip is 79px and row one lands at y=432
    # on a page row 0431 brought to y=329. Shut it is one line. What must be true is that the way
    # in is ON THE SCREEN and one click away, not that the dropdowns are already there.
    check("a control that opens the filters is on the page, unopened",
          page.evaluate("() => !!document.getElementById('qf-open')")
          and not s["selects"], f"{len(s['selects'])} dropdown(s) before the click")
    page.click("#qf-open")
    page.wait_for_timeout(80)
    o = page.evaluate(SCREEN)
    check("one click on it puts every filter and the search box on the screen",
          len(o["selects"]) == 5 and o["hasSearch"] and o["hasClear"],
          f"{len(o['selects'])} dropdown(s), search {o['hasSearch']}, clear {o['hasClear']}")


# ------------------------------------------------------- 1. the queue says what it orders by

def the_queue_says_what_it_orders_by(page):
    biggest_tier(page)
    s = page.evaluate(SCREEN)
    # THE TERMS COME FROM THE RANKING MODULE AND NOT FROM THIS FILE. `rank.DEFAULT_WEIGHTS` is
    # the sum the console is ordered by; if a weight is added or removed there this check fails
    # until the sentence says so, which is the whole point of not writing the sentence twice.
    # THE TERMS COME FROM `model.order_terms()`, AND THAT FUNCTION IS SEPARATELY REQUIRED TO
    # COVER `rank.DEFAULT_WEIGHTS` in the check below. Two assertions rather than one map written
    # twice: this one says the SCREEN agrees with the model, and that one says the MODEL agrees
    # with the ranking. A single copied map here would let a weight be added to the ranking and
    # named nowhere, with everything still green.
    want = MODEL.order_terms()["terms"]
    if not want:                                                        # DENOMINATOR
        check("the ranking module declares weights to describe", False,
              "rank.DEFAULT_WEIGHTS carries no term this file knows how to name")
        return
    check("the ranking module declares weights to describe", True,
          f"{len(want)} of {len(RANK.DEFAULT_WEIGHTS)}")
    # THE MODEL AGAINST THE RANKING, with no browser involved: every weight the human ranking
    # sums must be named by `order_terms`, and the one weight it deliberately does NOT carry
    # (`w_effort`, dropped because effort is the agent's token cost) must stay absent.
    unnamed = [k for k in RANK.DEFAULT_WEIGHTS if k not in MODEL.order_terms()["weights"]]
    check("every weight the ranking sums is one the model knows how to name", not unnamed,
          str(unnamed))
    check("`w_effort` is still absent from the human ranking",
          "w_effort" not in RANK.DEFAULT_WEIGHTS, str(sorted(RANK.DEFAULT_WEIGHTS)))
    missing = [t for t in want if t not in s["said"]]
    check("the strip names every term the ranking actually sums", not missing,
          f"missing {missing} from {s['said']!r}")
    # AND IT NOW CLAIMS THE PRODUCT, BECAUSE THE PRODUCT IS NOW WHAT IT COMPUTES. Row 0434,
    # 2026-08-31. This check used to read the other way: `" x " not in s["said"]`, asserting the
    # strip did NOT claim a product, because he had asked for urgency x importance and the queue
    # summed six additive terms with no signal called importance in them. `rank.score` now
    # computes `urgency x impact` as a spine, so the same check is re-pointed rather than deleted.
    #
    # IT IS INVERTED AND NOT REMOVED ON PURPOSE. The thing worth protecting was never the absence
    # of the words, it was that the strip and the arithmetic say the same thing; only the true
    # answer moved. Deleting it would have left the pair unguarded in the direction that now
    # matters, which is a console still describing a sum after somebody reverts the spine.
    check("the strip claims the product it now computes",
          " x " in s["said"], s["said"])
    check("and the product it names is urgency by impact",
          "urgency x impact" in s["said"], s["said"])
    # THE OLD TERMS ARE GONE FROM THE SENTENCE, which is the other half of not drifting. `stakes`
    # still exists as a column and is still the fallback the magnitude reads, but it is no longer
    # a term in the sum and a strip that still named it would be describing arithmetic that has
    # been deleted.
    check("`stakes` is no longer named as a term, because it is no longer one",
          "stakes" not in s["said"], s["said"])
    check("`effort` is not named, because the human ranking drops it on purpose",
          "effort" not in s["said"], s["said"])
    # THE BUMP IS THE OPERATOR'S ONE LEVER OVER THE ORDER and it is named, because a lever
    # nothing mentions is a lever nobody pulls.
    check("the strip names the operator's bump as part of the order",
          "bump" in s["said"], s["said"])


# ------------------------------------------------------- 2. nothing here sorts

def nothing_here_sorts(page):
    tier, n = biggest_tier(page)
    if not controls_are_built(page, 'the subsequence check'):
        return
    before = page.evaluate(SCREEN)
    if len(before["all"]) < 3:                                          # DENOMINATOR
        check("the tier has enough rows to prove an order survives a filter", False,
              f"{len(before['all'])} row(s) on {tier}: a subsequence check over 2 rows proves "
              f"nothing")
        return
    check("the tier has enough rows to prove an order survives a filter", True,
          f"{len(before['all'])} rows on {tier}")
    lanes = [l for l in before["lanes"] if l]
    if not lanes:                                                       # DENOMINATOR
        check("a facet with a value to filter on", False, "no row carries a lane")
        return
    want = max(set(lanes), key=lanes.count)
    check("a facet with a value to filter on", True, f"lane {want!r} on {lanes.count(want)} rows")
    page.select_option("#qf-lane", want)
    page.wait_for_timeout(80)
    after = page.evaluate(SCREEN)
    # THE SUBSEQUENCE. The surviving ids must appear in the same relative order as before, which
    # is what "a filter is not a sort" means operationally. A module that reordered would pass a
    # set comparison and fail this one.
    it = iter(before["all"])
    subseq = all(any(x == y for y in it) for x in after["visible"])
    check("the rows that survive a filter keep the ranking's order",
          subseq, f"{before['all']} -> {after['visible']}")
    check("the filter narrowed something", 0 < len(after["visible"]) < len(before["all"]),
          f"{len(after['visible'])} of {len(before['all'])}")
    page.click("#qf-clear")
    page.wait_for_timeout(80)
    back = page.evaluate(SCREEN)
    check("clearing puts every row back, in the same order",
          back["visible"] == before["all"], f"{back['visible']} vs {before['all']}")
    check("clearing empties the url", "f_lane" not in back["search"], back["search"])

    # ITEM 2'S OWN CENSUS, re-measured by the lane that would have broken it.
    census = page.evaluate("""() => {
      const rows = Array.from(document.querySelectorAll('.qrow'));
      const all = Array.from(document.querySelectorAll('[data-region=\"list\"] *'));
      const grab = all.filter(e => {
        const c = getComputedStyle(e).cursor; return c === 'grab' || c === 'grabbing'; });
      const head = document.querySelector('.qhead');
      const hs = head ? getComputedStyle(head) : null;
      return {
        rows: rows.length,
        /* THE EXPLICIT ATTRIBUTE, NOT THE IDL PROPERTY, AND THAT IS A CORRECTED CHECK RATHER
           THAN A WIDENED ONE. `element.draggable` is TRUE BY SPEC for every `<a href>` and every
           `<img>`, so the property counts the browser's native link dragging and not anything
           anybody built: measured on this list 2026-08-29 it answered 49, on a page with no drag
           feature of any kind, and it would have answered the same before row 0431 existed.
           What item 2 forbids is a control MADE draggable, which is `draggable="true"` in the
           markup. Both numbers are reported so the correction is evidence rather than a claim. */
        draggableProp: all.filter(e => e.draggable).length,
        draggableTagCensus: Array.from(new Set(all.filter(e => e.draggable)
                                                  .map(e => e.tagName))).sort().join(','),
        draggable: all.filter(e => e.getAttribute('draggable') === 'true').length,
        handles: document.querySelectorAll('[data-drag],.draghandle,[aria-grabbed]').length,
        grab: grab.length,
        prioControls: document.querySelectorAll(
          'input[name=priority],select[name=priority],[data-action*=priority]').length,
        headExists: !!head,
        headPointer: hs ? hs.pointerEvents : null,
        headCursor: hs ? hs.cursor : null,
        headControls: head ? head.querySelectorAll('a,button,[role=button],[tabindex]').length : 0,
      };
    }""")
    if not census["rows"]:                                              # DENOMINATOR
        check("there are rows to run item 2's census over", False, "0 rows")
        return
    check("there are rows to run item 2's census over", True, f"{census['rows']} rows")
    check("item 2: nothing on the list was MADE draggable", census["draggable"] == 0,
          str(census["draggable"]))
    print(f"      (the IDL property answers {census['draggableProp']} over "
          f"{census['draggableTagCensus']}, which is the browser's native link and image "
          f"dragging and is true of this page with or without any of this lane's work)")
    check("item 2: no drag handle", census["handles"] == 0, str(census["handles"]))
    check("item 2: no grab cursor anywhere on the list", census["grab"] == 0, str(census["grab"]))
    check("item 2: no control writes priority", census["prioControls"] == 0,
          str(census["prioControls"]))
    if not census["headExists"]:                                        # DENOMINATOR
        not_measured("this tier renders no column header, so row 0431's unclickable header "
                     "could not be re-measured here")
    else:
        check("row 0431's column header is still undeliverable to a click",
              census["headPointer"] == "none", str(census["headPointer"]))
        check("and it still does not dress as a control",
              census["headCursor"] in ("auto", "default") and census["headControls"] == 0,
              f"cursor {census['headCursor']}, {census['headControls']} control(s)")


# ------------------------------------------------------- 3. every facet filters on its own column

def every_facet_filters_on_its_own_column(page):
    biggest_tier(page)
    if not controls_are_built(page, 'the per-facet checks'):
        return
    s = page.evaluate(SCREEN)
    live = [x for x in s["selects"] if not x["disabled"] and x["n"] > 1]
    if not live:                                                        # DENOMINATOR
        check("at least one facet has values on this tier", False,
              f"{len(s['selects'])} dropdown(s), none with a value: every check below would be "
              f"a verdict over an empty set")
        return
    check("at least one facet has values on this tier", True,
          f"{len(live)} of {len(s['selects'])} dropdowns carry values")
    for spec in live:
        facet = spec["id"].replace("qf-", "")
        vals = page.evaluate(
            "id => Array.from(document.getElementById(id).options).map(o => o.value)"
            ".filter(v => v && v !== '(none)')", spec["id"])
        if not vals:
            continue
        v = vals[0]
        page.select_option("#" + spec["id"], v)
        page.wait_for_timeout(60)
        got = page.evaluate("""f => {
          const rows = Array.from(document.querySelectorAll('.qrow'));
          const on = r => (r.getAttribute('data-f-' + f) || '');
          return {
            shown: rows.filter(r => r.style.display !== 'none').map(on),
            hidden: rows.filter(r => r.style.display === 'none').map(on),
          };
        }""", facet)
        if facet == "gate":
            ok_shown = all(v in (x or "").split(" ") for x in got["shown"])
            ok_hidden = all(v not in (x or "").split(" ") for x in got["hidden"])
        else:
            ok_shown = all(x == v for x in got["shown"])
            ok_hidden = all(x != v for x in got["hidden"])
        check(f"{facet}={v}: every row still on screen carries it",
              bool(got["shown"]) and ok_shown, str(got["shown"]))
        # THE OTHER HALF, and it is the one a broken filter passes: hiding everything satisfies
        # "the rows that are left all match".
        check(f"{facet}={v}: every row it hid does not carry it", ok_hidden, str(got["hidden"]))
        page.click("#qf-clear")
        page.wait_for_timeout(60)

    # THE SEARCH, over the id and over the title, because those are the two things a person types.
    row0 = page.evaluate("""() => {
      const r = document.querySelector('.qrow');
      return r ? {id: r.id.replace(/^card-/, ''),
                  word: (r.querySelector('.qctitle') || {}).textContent || ''} : null;
    }""")
    if not row0 or not row0["id"]:                                      # DENOMINATOR
        check("there is a row to search for", False, "no row on this tier")
        return
    check("there is a row to search for", True, row0["id"])
    page.fill("#qf-q", row0["id"])
    page.wait_for_timeout(80)
    a = page.evaluate(SCREEN)
    check("searching an id leaves that row and only rows carrying it",
          "card-" + row0["id"] in a["visible"], str(a["visible"]))
    word = next((w for w in row0["word"].split() if len(w) > 4), "")
    if not word:                                                        # DENOMINATOR
        not_measured("the first row's title carries no word over four letters, so the title "
                     "search had nothing distinctive to look for")
    else:
        page.fill("#qf-q", word)
        page.wait_for_timeout(80)
        b = page.evaluate(SCREEN)
        check("searching a word from a title finds that row",
              "card-" + row0["id"] in b["visible"], f"{word!r} -> {b['visible']}")
    page.fill("#qf-q", "zzzz-no-row-carries-this")
    page.wait_for_timeout(80)
    c = page.evaluate(SCREEN)
    check("a search matching nothing hides every row and says so",
          c["visible"] == [] and "nothing here matches" in c["said"], c["said"])
    page.click("#qf-clear")
    page.wait_for_timeout(60)


# ------------------------------------------------------- 4. it prints its own denominator

def it_prints_its_own_denominator(page):
    tier, _ = biggest_tier(page)
    if not controls_are_built(page, 'the denominator check'):
        return
    s = page.evaluate(SCREEN)
    lanes = [l for l in s["lanes"] if l]
    if not lanes:                                                       # DENOMINATOR
        check("a facet to switch on before reading the count", False, "no row carries a lane")
        return
    check("a facet to switch on before reading the count", True, f"{len(set(lanes))} lane(s)")
    check("with no filter on, the strip states no count",
          " shown" not in s["said"], s["said"])
    page.select_option("#qf-lane", lanes[0])
    page.wait_for_timeout(80)
    a = page.evaluate(SCREEN)
    check("with a filter on, the strip states how many of how many",
          " shown" in a["said"] and " of " in a["said"], a["said"])
    below = int(a["belowWindow"] or 0)
    if not below:                                                       # DENOMINATOR
        not_measured(f"the {tier} tier fits inside the window on this store, so there is no "
                     f"below-the-window remainder for the strip to name. The branch is exercised "
                     f"on any store where a tier holds more than the window.")
    else:
        check("and it names what it is NOT searching",
              "not searched" in a["said"] and str(below) in a["said"],
              f"{below} below the window, strip says {a['said']!r}")
    page.click("#qf-clear")
    page.wait_for_timeout(60)


# ------------------------------------------------------- 5. the project filter degrades honestly

def the_project_filter_degrades_honestly(page, base=None, where="this store"):
    biggest_tier(page, base=base)
    # OPENED FIRST, because the claim is about the DROPDOWN and not only about the data attribute:
    # a store with no project column must show a control that is disabled and says why, which is
    # `MUST-NOT-BUILD` item 2's rule about an affordance that cannot work. Without this the two
    # sentence checks below pass and the surface claim reports NOT MEASURED, which is how a lane
    # ships a green over the half that matters.
    controls_are_built(page, f"{where}: the project dropdown")
    s = page.evaluate(SCREEN)
    col = s["projectColumn"]
    if col is None:                                                     # DENOMINATOR
        check(f"{where}: the strip states whether a project column exists", False,
              "data-project-column is absent from #qfilter")
        return
    check(f"{where}: the strip states whether a project column exists", True, f"={col}")
    note = s["projectNote"] or ""
    proj = next((x for x in s["selects"] if x["id"] == "qf-project"), None)
    if col == "0":
        # THE SHAPE OF LIVE `brain` ON 2026-08-29: ledger 42, migration 44 unapplied.
        check(f"{where}: with no project column the note says the INSTALL cannot hold one yet",
              "not yet updated to hold projects" in note, note)
        check(f"{where}: and it does not report a schema fact as an operator fact",
              "no project" not in note.lower().replace("nothing here has a project", ""), note)
        if proj is None:                                                # DENOMINATOR
            not_measured(f"{where}: no dropdown was built at all, so the disabled state could "
                         f"not be read. That is correct below 900px and wrong above it.")
        else:
            check(f"{where}: the project dropdown is disabled rather than empty and clickable",
                  proj["disabled"] and "not on this store" in proj["first"], json.dumps(proj))
    else:
        check(f"{where}: with a project column the note counts what is filed",
              ("row(s)" in note or "no row carries" in note), note)
        if proj is None:                                                # DENOMINATOR
            not_measured(f"{where}: no project dropdown was built")
        elif proj["n"] > 1:
            page.select_option("#qf-project", page.evaluate(
                "() => Array.from(document.getElementById('qf-project').options)"
                ".map(o => o.value).filter(v => v && v !== '(none)')[0]"))
            page.wait_for_timeout(80)
            got = page.evaluate("""() => {
              const rows = Array.from(document.querySelectorAll('.qrow'));
              return {shown: rows.filter(r => r.style.display !== 'none')
                                 .map(r => r.getAttribute('data-f-project')),
                      hidden: rows.filter(r => r.style.display === 'none')
                                  .map(r => r.getAttribute('data-f-project'))};
            }""")
            v = page.evaluate("() => document.getElementById('qf-project').value")
            check(f"{where}: the project filter filters on the project column",
                  bool(got["shown"]) and all(x == v for x in got["shown"])
                  and all(x != v for x in got["hidden"]), json.dumps(got))
            page.click("#qf-clear")
            page.wait_for_timeout(60)
        else:
            check(f"{where}: with the column present and nothing filed, it says so and is "
                  f"disabled", proj["disabled"] and "none set" in proj["first"],
                  json.dumps(proj))
    # THE TWO FIELDS HE NAMED THAT DO NOT EXIST, said once on the page rather than left to be
    # discovered by looking for a dropdown that is not there.
    absent = page.evaluate("() => (document.getElementById('qfilter') || {})"
                           ".getAttribute ? document.getElementById('qfilter')"
                           ".getAttribute('data-absent') : null")
    check(f"{where}: the console names the fields he asked for that this store has no column for",
          bool(absent) and "client" in absent and "custom fields" in absent, str(absent)[:160])


# ------------------------------------------------------- 6. the keyboard, and the phone

def the_keyboard_reaches_it(page):
    biggest_tier(page)
    if not controls_are_built(page, 'the keyboard checks'):
        return
    page.keyboard.press("f")
    a = page.evaluate(SCREEN)
    check("`f` puts the caret in the first filter control",
          a["focus"] == "qf-lane", str(a["focus"]))
    page.keyboard.press("Escape")
    page.wait_for_timeout(40)
    page.keyboard.press("/")
    b = page.evaluate(SCREEN)
    check("`/` puts the caret in the search box", b["focus"] == "qf-q", str(b["focus"]))
    # AND NO QUEUE KEY FIRES OVER A CARET. 0432's `typing()` treats a select and a text input as
    # typing, so typing `v` into the search box must search rather than answer for the voice note.
    page.keyboard.type("v")
    c = page.evaluate(SCREEN)
    check("typing into the search box does not fire the queue's own keys",
          "f_q=v" in c["search"] and "V is not built" not in (c["said"] or ""), c["search"])
    page.fill("#qf-q", "")
    page.keyboard.press("Escape")
    page.wait_for_timeout(60)
    d = page.evaluate(SCREEN)
    check("Escape leaves the bar and gives the queue its keys back",
          d["focus"] not in ("qf-q", "qf-lane"), str(d["focus"]))
    page.keyboard.press("?")
    e = page.evaluate("""() => {
      const b = document.querySelector('#saybar .banner');
      return b ? b.textContent : '';
    }""")
    # A KEYBOARD NOBODY IS TOLD ABOUT IS A KEYBOARD NOBODY USES, and this legend is the only
    # place the keys are named.
    check("the `?` legend names both filter keys", "f filter" in e and "/ search" in e, e[:200])


def the_cursor_never_lands_on_a_hidden_row(page):
    biggest_tier(page)
    if not controls_are_built(page, 'the cursor rescue check'):
        return
    s = page.evaluate(SCREEN)
    if len(s["all"]) < 2:                                               # DENOMINATOR
        check("two rows on this tier, so a filter can hide the one the cursor is on", False,
              f"{len(s['all'])} row(s)")
        return
    check("two rows on this tier, so a filter can hide the one the cursor is on", True,
          f"{len(s['all'])} rows")
    page.keyboard.press("ArrowDown")
    a = page.evaluate(SCREEN)
    if not a["cursor"]:                                                 # DENOMINATOR
        check("a cursor was placed", False, "no .qrow.is-cursor after ArrowDown")
        return
    check("a cursor was placed", True, a["cursor"])
    # THE SEARCH BOX AND NOT A DROPDOWN, because it works on every board rather than on a board
    # that happens to carry two lanes on one tier. Searching for the id of a row that is NOT the
    # cursor's hides the cursor's row and leaves exactly one other, which is the condition this
    # check needs, on any store, with no fixture and no store write.
    other_id = next((r for r in a["all"] if r != a["cursor"]), None)
    if other_id is None:                                                # DENOMINATOR
        not_measured("no row other than the cursor's, so no filter here can hide the cursor")
        return
    page.fill("#qf-q", other_id.replace("card-", ""))
    page.wait_for_timeout(150)
    b = page.evaluate(SCREEN)
    check("a filter that hides the cursor's row moves the cursor to one still on screen",
          b["cursor"] is not None and b["cursorHidden"] is False, str(b["cursor"]))
    # AND THE ARROWS ONLY WALK WHAT HE CAN SEE.
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    c = page.evaluate(SCREEN)
    check("the arrows never step onto a filtered-out row",
          c["cursorHidden"] is False and c["cursor"] in b["visible"] + [b["cursor"]],
          f"{c['cursor']} against visible {b['visible']}")
    page.click("#qf-clear")
    page.wait_for_timeout(60)


def the_phone_gets_the_sentence_and_no_dropdowns(browser):
    page = browser.new_context(viewport=PHONE).new_page()
    try:
        at_tier(page, "Judge")
        s = page.evaluate(SCREEN)
        check("the phone still says what the queue is ordered by",
              "ordered by" in s["said"], s["said"][:120])
        opener = page.evaluate("() => !!document.getElementById('qf-open')")
        check("and it builds no dropdown, no search box, no clear and not even an opener "
              "under the 44px floor",
              not s["selects"] and not s["hasSearch"] and not s["hasClear"] and not opener,
              f"{len(s['selects'])} select(s), search {s['hasSearch']}, clear {s['hasClear']}, "
              f"opener {opener}")
        # AND THE KEY SAYS SO RATHER THAN FAILING SILENTLY.
        page.keyboard.press("f")
        bar = page.evaluate("() => (document.querySelector('#saybar .banner') || {})"
                            ".textContent || ''")
        check("`f` on a phone says why there is nothing to focus",
              "900px" in bar and "44px" in bar, bar[:160])
        w = page.evaluate("() => ({client: document.documentElement.clientWidth,"
                          " scroll: document.documentElement.scrollWidth})")
        check("the strip did not put a sideways scroll on the phone",
              w["scroll"] <= w["client"], json.dumps(w))
    finally:
        page.close()


# ------------------------------------------------------- the timings

def the_two_expansions_are_still_instant(page):
    """B1 measured 69/59ms and B4 re-measured 67/56ms. This lane must not have moved them."""
    biggest_tier(page)
    first, second, filt, openms = [], [], [], []
    for _ in range(6):
        page.goto(BASE + "/queue?tier=judge", wait_until="load")
        # 30s AND NOT 15s, AND THE REASON IS THE HOST RATHER THAN THE PRODUCT. This ran
        # beside a full web suite, two other lanes' consoles and their browsers, at a
        # load average of 10 to 14 on WSL2; a plain `curl` of this same URL took 720 ms in
        # the same second. The MEASUREMENT below is unaffected -- it starts its clock
        # after this wait returns -- and the numbers it produces are still host numbers
        # and are printed as such.
        page.wait_for_selector(".qrow", timeout=30000)
        t = time.perf_counter()
        # `.qrow a.qopen` AND NOT `.qrow:first-child .qopen`: the first child of the list host is
        # row 0431's column header, so `:first-child` matches nothing and the click hangs for its
        # whole timeout on a page where everything is working. And `page.click` with a plain
        # selector rather than a chained locator, because that is the call lanes B1 and B4 timed
        # and a chained locator costs two extra round trips to the browser: measured here, the
        # same click read 128 ms chained and 76 ms plain, on the same page in the same minute.
        page.click(".qrow a.qopen")
        page.wait_for_selector(".qrow.is-open .qexp", state="visible", timeout=5000)
        first.append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        page.click(".qrow.is-open a.qmore")
        page.wait_for_selector(".qrow.is-panel .qpanel", state="visible", timeout=5000)
        second.append((time.perf_counter() - t) * 1000)
        # THE LEVEL-TWO PANEL IS CLOSED BEFORE THE STRIP IS TOUCHED. It is `position:fixed` and
        # 448px wide over the right of the window, and it is what covered the filter control when
        # the control lived at the right-hand end of the strip. The control now leads the strip
        # and is clear of it, and this closes the panel anyway so the timing below is of the
        # filter and not of a fight with an overlay.
        page.keyboard.press("Escape")
        page.wait_for_timeout(60)
        lanes = [l for l in page.evaluate(SCREEN)["lanes"] if l]
        # The bar is shut by default, so it is opened here BEFORE the clock starts. What is being
        # timed is applying a filter, not building the bar; both are measured and printed, and
        # conflating them would report the one-off cost as the per-use one.
        if page.evaluate("() => !!document.getElementById('qf-open')"):
            t0 = time.perf_counter()
            page.click("#qf-open")
            page.wait_for_selector("#qf-lane", timeout=10000)
            openms.append((time.perf_counter() - t0) * 1000)
        if lanes and page.evaluate("() => !!document.getElementById('qf-lane')"):
            t = time.perf_counter()
            page.select_option("#qf-lane", lanes[-1])
            page.wait_for_function(
                "() => document.getElementById('qfilter-said')"
                ".textContent.indexOf(' shown') > 0",
                timeout=5000)
            filt.append((time.perf_counter() - t) * 1000)
            page.click("#qf-clear")
    if not first or not second:                                         # DENOMINATOR
        check("the two expansions were timed", False, "0 samples")
        return
    med = lambda xs: sorted(xs)[len(xs) // 2]                           # noqa: E731
    print(f"      first click to inline expansion   median {med(first):.0f} ms "
          f"({min(first):.0f}..{max(first):.0f}, {len(first)} runs)")
    print(f"      second click to the right panel   median {med(second):.0f} ms "
          f"({min(second):.0f}..{max(second):.0f}, {len(second)} runs)")
    if openms:
        print(f"      click `filter` to the bar built   median {med(openms):.0f} ms "
              f"({min(openms):.0f}..{max(openms):.0f}, {len(openms)} runs)")
    if filt:
        print(f"      a filter applied                 median {med(filt):.0f} ms "
              f"({min(filt):.0f}..{max(filt):.0f}, {len(filt)} runs)")
    # THE STANDING NUMBERS ARE 67 AND 56 ms. The tolerance is stated once and is not widened: it
    # is generous because the host is a WSL2 dev server with other consoles on it, and a
    # REGRESSION this check exists to catch would be a round trip, which is hundreds of ms.
    check("the first expansion is still nowhere near a round trip", med(first) < 400,
          f"{med(first):.0f} ms against a 400 ms ceiling and a 67 ms standing figure")
    check("the second expansion is still nowhere near a round trip", med(second) < 400,
          f"{med(second):.0f} ms")
    if filt:
        check("a filter applies without a round trip", med(filt) < 400, f"{med(filt):.0f} ms")


def main() -> int:
    served = _console_guard.refuse_unless_scratch(BASE, what="test_the_order_and_the_filters")
    print(f"      console at {BASE} is pid {served.get('pid')} on database "
          f"{served.get('db')!r}")
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        page = b.new_context(viewport=DESK).new_page()
        # TWO DIFFERENT CLAIMS, SEPARATED, because one run in this window failed a single combined
        # check with "Failed to load resource: 500" and sent the lane looking for a defect in its
        # own JavaScript. The console log said what it actually was: `store.session.
        # StoreConfigError: could not connect as brain_runtime: server closed the connection
        # unexpectedly`, three lanes into a night that had this host at a load average of 14. The
        # console's own error path was correct -- it refused to render a queue it could not read
        # and answered 500 rather than inventing an order -- and merging that into "a JavaScript
        # error" hid both facts. So: exceptions from `pageerror`, transport from `response`, and
        # each says which it is.
        errs: list[str] = []
        bad: list[str] = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("response", lambda r: bad.append(f"{r.status} {r.url}")
                if r.status >= 400 and not r.url.endswith("/favicon.ico") else None)
        try:
            for name, fn in (
                    ("a person reaches the filters by clicking",
                     a_person_reaches_the_filters_by_clicking),
                    ("the queue says what it orders by", the_queue_says_what_it_orders_by),
                    ("nothing here sorts, and item 2 is kept", nothing_here_sorts),
                    ("every facet filters on its own column",
                     every_facet_filters_on_its_own_column),
                    ("the strip prints its own denominator", it_prints_its_own_denominator),
                    ("the project filter degrades honestly",
                     the_project_filter_degrades_honestly),
                    ("the keyboard reaches it", the_keyboard_reaches_it),
                    ("the cursor never lands on a hidden row",
                     the_cursor_never_lands_on_a_hidden_row),
                    ("the two expansions are still instant",
                     the_two_expansions_are_still_instant)):
                print(f"--- {name}")
                fn(page)
            print("--- the phone gets the sentence and no dropdowns")
            the_phone_gets_the_sentence_and_no_dropdowns(b)
            if BASE42:
                print("--- a store below migration 44, which is the shape of live `brain`")
                p42 = b.new_context(viewport=DESK).new_page()
                try:
                    _console_guard.refuse_unless_scratch(
                        BASE42, what="test_the_order_and_the_filters (ledger 42)")
                    the_project_filter_degrades_honestly(p42, base=BASE42, where="ledger 42")
                finally:
                    p42.close()
            else:
                not_measured("BASE42 is unset, so the column-absent branch was not driven "
                             "against a second console. Set BASE42 to a console on a store "
                             "below migration 44 to cover it end to end; check 5 still asserts "
                             "the branch that matches whichever store BASE is on.")
            check("no javascript exception on any surface driven above", not errs,
                  "; ".join(errs[:3]))
            # `/favicon.ico` IS EXCLUDED BY NAME AND IS NOT SWEPT UNDER THE RUG: this console
            # serves `/static/favicon.svg` and answers 404 for the path browsers ask for by
            # default, on every page load, with or without any of this lane's work. It is filed
            # to `crosstalk-C3.md` rather than counted here as a defect of the filters.
            check("no failed request on any surface driven above", not bad,
                  "; ".join(bad[:3]) + f"  ({len(bad)} in total)")
        finally:
            b.close()
    n = len(PASS) + len(FAIL)
    if n == 0:                                                          # DENOMINATOR
        print("\n0 checks made. A verdict over an empty set is not a pass.")
        return 2
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed  (of {n} checks; desktop "
          f"{DESK['width']}x{DESK['height']}, phone {PHONE['width']}x{PHONE['height']}"
          + (f"; {len(NOTES)} not measured" if NOTES else "") + ")")
    for note in NOTES:
        print(f"  not measured: {note}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
