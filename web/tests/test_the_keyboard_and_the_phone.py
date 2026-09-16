#!/usr/bin/env python3
"""ROW 0432 AND ROW 0433, PLUS THE TWO THINGS HIS 7AM SURFACE WAS MISSING. Lane B4, 2026-08-29.

Four claims, and none of them is a claim that a selector exists.

  1. THE KEYBOARD (`0432`, his ask 3). *"our users really don't want a shiny interface ... our
     people are going to be power users, not iPad babies."* Space opens and closes, arrows move
     between items and between options, number keys pick an option, Ctrl+Z reverses what has an
     inverse AND SAYS SO WHERE NOTHING DOES, V states what is missing rather than doing nothing.
     Every key below is PRESSED and the resulting screen is read. A key that is bound and does
     nothing passes every markup assertion ever written.

  2. DEEP WORK (`0433`, his ask 9). *"it doesn't really give off the vibe of going super, super
     deep."* The tier counters are gone from the MARKUP, the focused item is in the right panel,
     and the other rows are dimmer. MUST-NOT-BUILD item 9 is KEPT and is absolute in the markup
     rather than in CSS, so the counts here are of DOM NODES and BYTES: a card that is
     `display:none` is still read by a screen reader and still shipped down the wire.

  3. THE PHONE DOES NOT SCROLL SIDEWAYS. Measured 2026-08-29 before this landed: 390 wide,
     `document.scrollWidth` 483 on the front page and on every tier, `nav.rooms` 470px with its
     last two tabs past the right edge. *"The morning pass happens on a phone, in a chair, before
     the laptop is open."*

  4. A PAGE IS NEVER A STORED COPY. The console sent no `Cache-Control` and no `Expires` at all,
     so a browser was free to serve a document from before a decision. That is his 2026-08-28
     complaint restated: *"nothing happened when I accepted work."*

  5. THE 0420 COPY RULING (D-06), because it is copy and copy rots silently: the button reads
     `Go with default` and the default line reads `If you say nothing:`, and neither of the two
     phrases he rejected is on any surface.

Run:  BASE=http://127.0.0.1:3130 python3 -m web.tests.test_the_keyboard_and_the_phone

The console at BASE must be on a scratch store: `_console_guard` reads the SERVING PROCESS and
not the port, and refuses `brain` and `brain_scratch`.
"""

from __future__ import annotations

import os
import sys
import urllib.request

_R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_R, os.path.join(_R, "engine"), os.path.join(_R, "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _console_guard                                                   # noqa: E402
from playwright.sync_api import sync_playwright                         # noqa: E402

BASE = os.environ.get("BASE", "http://127.0.0.1:3130")
CHROME = os.environ.get(
    "CHROME", os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux/chrome"))

DESK = {"width": 1440, "height": 760}
PHONE = {"width": 390, "height": 844}

PASS: list[str] = []
FAIL: list[str] = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'' if ok else '   ' + detail}")
    return bool(ok)


SCREEN = """() => {
  const vis = e => { if (!e) return false; const r = e.getBoundingClientRect();
                     return r.width > 0 && r.height > 0; };
  const cur = document.querySelector('.qrow.is-cursor');
  const open = document.querySelector('.qrow.is-open');
  const pan = document.querySelector('.qrow.is-panel > .qpanel');
  const bar = document.querySelector('#saybar .banner');
  return {
    cursor: cur ? cur.id : null,
    cursorMark: cur ? getComputedStyle(cur.querySelector('.qgrid')).boxShadow : null,
    open: open && vis(open.querySelector('.qexp')) ? open.id : null,
    panel: pan && vis(pan) ? pan.parentElement.id : null,
    say: bar ? bar.textContent.trim() : null,
    picked: (() => { const r = document.querySelector('input[data-opt]:checked');
                     return r ? r.value : null; })(),
    path: location.pathname + location.search,
    rows: document.querySelectorAll('.qrow').length,
  };
}"""


def to_the_queue(page, tier):
    """Reached by CLICKING from the front page. No id is ever resolved in this file."""
    page.goto(BASE + "/", wait_until="load")
    # The Queue is the WORK tab's room since the five-tab nav (MUST-NOT-BUILD item 11 amended
    # 2026-09-14); the tab opens /queue, so this is still one click from the front page.
    page.click("nav.rooms a.room:has-text('Work')")
    page.wait_for_selector('[data-region="list"]', timeout=15000)
    page.click(f'[data-region="tiers"] a.tier:has-text("{tier}"), '
               f'[data-region="tiers"] .tier:has-text("{tier}")')
    page.wait_for_selector('[data-region="list"]', timeout=15000)
    page.mouse.click(4, 400)                      # focus the document, nothing inside it


# --------------------------------------------------------------------------------- 1. the keys

def the_keyboard_moves_opens_and_closes(page):
    to_the_queue(page, "Shape")
    s = page.evaluate(SCREEN)
    if s["rows"] < 2:                                                   # DENOMINATOR
        check("the tier has rows to move between", False,
              f"{s['rows']} row(s): every key check below would be a verdict over an empty set")
        return
    check("the tier has rows to move between", True)
    page.keyboard.press("ArrowDown")
    a = page.evaluate(SCREEN)
    check("Down puts a cursor on a row and opens nothing",
          a["cursor"] is not None and a["open"] is None, str(a))
    check("the cursor is marked by something other than colour",
          bool(a["cursorMark"]) and a["cursorMark"] != "none", str(a["cursorMark"]))
    if a["cursor"] is None:                                             # DENOMINATOR
        # WITHOUT A CURSOR EVERY COMPARISON BELOW IS `None == None`. Watched, 2026-08-29, with
        # the keyboard module disabled on purpose: four of the checks after this line reported
        # `ok` over a screen on which no key had done anything, because "the row that opened is
        # the row the cursor was on" is true when neither exists. A verdict over an empty set is
        # not a pass, so the rest of this check does not run.
        print("      NOT MEASURED: no cursor was placed, so the open/close checks below would "
              "compare None against None.")
        return
    page.keyboard.press("ArrowDown")
    b = page.evaluate(SCREEN)
    check("Down moves to the next row", b["cursor"] not in (None, a["cursor"]))
    page.keyboard.press("ArrowUp")
    c = page.evaluate(SCREEN)
    check("Up moves back", c["cursor"] == a["cursor"], f"{b['cursor']} -> {c['cursor']}")
    page.keyboard.press(" ")
    d = page.evaluate(SCREEN)
    check("Space opens the row the cursor is on", d["open"] == a["cursor"], str(d))
    check("Space did not navigate", d["path"] == s["path"], d["path"])
    page.keyboard.press(" ")
    e = page.evaluate(SCREEN)
    check("Space closes it again and keeps the cursor",
          e["open"] is None and e["cursor"] == a["cursor"], str(e))
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    f = page.evaluate(SCREEN)
    check("Right twice reaches the right-side panel with the row still open",
          f["panel"] == a["cursor"] and f["open"] == a["cursor"], str(f))
    check("and it still did not navigate", f["path"] == s["path"], f["path"])
    page.keyboard.press("ArrowLeft")
    g = page.evaluate(SCREEN)
    check("Left closes the panel and leaves the row open",
          g["panel"] is None and g["open"] == a["cursor"], str(g))
    page.keyboard.press("Escape")
    h = page.evaluate(SCREEN)
    check("Escape closes the row", h["open"] is None and h["cursor"] == a["cursor"], str(h))


#: The four kinds `queue draft options` accepts. Not this file's vocabulary: it is the queue's,
#: and the transition refuses anything else by name.
FIXTURE_OPTIONS = [
    {"kind": "agent-does", "label": "Answer it from what is already here",
     "title": "Answer from the tables that already exist",
     "plan": "Read the three tables that already exist and write the answer.",
     "counterargument": "It may be answering a question nobody asked twice.",
     "who": "fleet", "thinking": "fast model, small prompt", "size": "one pass",
     "lane": "research", "cost_est": 0.20, "time_est": "your 5m"},
    {"kind": "agent-deep", "label": "Reproduce it first",
     "title": "Reproduce the discrepancy before scoping anything",
     "plan": "Reproduce it twice on two days before anybody scopes a fix.",
     "counterargument": "Two reproductions cost a day the decision may not have.",
     "who": "fleet", "thinking": "deep model", "size": "half a day",
     "lane": "research", "cost_est": 0.60, "time_est": "your 15m"},
    {"kind": "sprint", "label": "Scope it as a sprint",
     "title": "Scope the whole reconciliation",
     "plan": "Every table, the whole window, one brief.",
     "counterargument": "A sprint on a discrepancy nobody has reproduced twice.",
     "who": "fleet", "thinking": "deep model, big prompt", "size": "scoped sprint",
     "lane": "research", "cost_est": 12.0, "time_est": "overnight"},
    {"kind": "agent-helps", "label": "Ask the producer what they meant",
     "title": "Put it back to the producer",
     "plan": "One question to whoever filed it, and wait.",
     "counterargument": "A round trip costs a day and may return the same sentence.",
     "who": "human", "thinking": "no model", "size": "one message",
     "lane": "research", "cost_est": 0.0, "time_est": "your 2m"},
]


def seed_one_rail_if_the_board_has_none(page) -> bool:
    """The number keys need a row with a DRAFTED OPTIONS RAIL, and the demo seed builds none.

    Measured 2026-08-29 against `web/bin/seed-demo.py`: 0 rails across all three tiers, so the
    first version of this check reported NOT MEASURED and went red in the sanctioned runner for a
    FIXTURE reason rather than a product one. A suite that needs a fixture the runner does not
    build has two honest options and only one of them is useful: say NOT MEASURED forever, or
    build it. This builds it, through the registered `queue draft options` transition -- the same
    door `test_option_dispatch.py` uses -- so nothing here is a second way to create an option.

    It writes to the database this process is pointed at, which the runner guarantees is the one
    the console at BASE is serving (`_console_guard` has already refused anything else). It is a
    no-op when a rail already exists, so running this suite twice does not draft twice.
    """
    if page.evaluate("() => document.querySelectorAll('.rail').length") > 0:
        return True
    import store                                                        # noqa: PLC0415
    import human_queue                                                  # noqa: PLC0415,F401
    with store.read() as s:
        rows = s.query("SELECT id FROM brain.work_item WHERE state IN ('inbox','active') "
                       "AND NOT agent_claimable ORDER BY id LIMIT 1")
        if not rows:
            rows = s.query("SELECT id FROM brain.work_item WHERE state IN ('inbox','active') "
                           "ORDER BY id LIMIT 1")
    if not rows:                                                        # DENOMINATOR
        print("      NOT MEASURED: 0 work items on this store, so there is nothing to draft "
              "options on.")
        return False
    store.apply("queue draft options", actor="B4-fixture", source_type="work_item",
                source_id=rows[0]["id"], options=FIXTURE_OPTIONS, drafted_by="B4-fixture",
                by="B4-fixture")
    print(f"      drafted {len(FIXTURE_OPTIONS)} options on {rows[0]['id']}, because this board "
          f"carried none")
    page.reload(wait_until="load")
    return True


def a_tier_carrying_a_rail(page):
    """Which tier the drafted options landed on, found by CLICKING each chip.

    The seeder takes the first item the operator owns and does not get to choose its tier, and the
    demo seed's ids move every time the store is reseeded. Looking on one tier and reporting NOT
    MEASURED was this check failing to walk far enough rather than the board carrying nothing.
    """
    for tier in ("Shape", "Judge", "Decide"):
        to_the_queue(page, tier)
        if page.evaluate("() => document.querySelectorAll('.rail').length") > 0:
            return tier
    return None


def a_number_key_picks_an_option_or_says_why_not(page):
    to_the_queue(page, "Shape")
    if not seed_one_rail_if_the_board_has_none(page):
        check("a row with drafted options is on the board", False, "0 work items to draft on")
        return
    tier = a_tier_carrying_a_rail(page)
    if tier is None:                                                    # DENOMINATOR
        print("      NOT MEASURED: no tier carries a drafted options rail even after seeding "
              "one, so the number keys have nothing to pick.")
        check("a row with drafted options is on the board", False, "0 rails on any tier")
        return
    print(f"      the drafted options are on the {tier} tier")
    page.mouse.click(4, 400)
    target = None
    for _ in range(12):
        page.keyboard.press("ArrowDown")
        s = page.evaluate(SCREEN)
        if not s["cursor"]:
            break
        if page.evaluate("id => !!document.getElementById(id).querySelector('.rail')", s["cursor"]):
            target = s["cursor"]
            break
    if target is None:                                                  # DENOMINATOR
        print("      NOT MEASURED: no row on this board carries a drafted options rail, so the "
              "number keys have nothing to pick. Seed one with "
              "`outputs/2026-08-29-commander/B4/seed_one_option_set.py`.")
        check("a row with drafted options is reachable by arrowing", False,
              "0 rails on this board")
        return
    check("a row with drafted options is reachable by arrowing", True)
    page.keyboard.press("ArrowRight")
    page.keyboard.press("2")
    s = page.evaluate(SCREEN)
    check("a number key picks that option", s["picked"] == "2", f"picked {s['picked']!r}")
    page.keyboard.press("ArrowDown")
    s = page.evaluate(SCREEN)
    check("Down moves between OPTIONS while a row with options is open, not between rows",
          s["picked"] == "3" and s["cursor"] == target, str(s))
    page.keyboard.press("Escape")
    page.keyboard.press("ArrowDown")
    s = page.evaluate(SCREEN)
    check("closing the row puts the arrows back on the queue", s["cursor"] != target, str(s))
    if s["cursor"] and not page.evaluate(
            "id => !!document.getElementById(id).querySelector('.rail')", s["cursor"]):
        page.keyboard.press("8")
        s = page.evaluate(SCREEN)
        check("a number on a row with no options says so rather than doing nothing",
              "no drafted options" in (s["say"] or ""), repr(s["say"]))


def ctrl_z_is_never_silent(page):
    """MUST-NOT-BUILD item 10 is KEPT, and this is the check that keeps it.

    The item forbids a LIE about undo, not undo. So the key runs the inverse the receipt stripe
    already carries, and where the stripe says `no undo, <reason>` it reads that reason back.
    """
    to_the_queue(page, "Judge")
    page.keyboard.press("Control+z")
    s = page.evaluate(SCREEN)
    check("Ctrl+Z with nothing to undo says so", "Nothing to undo" in (s["say"] or ""),
          repr(s["say"]))
    page.locator("a.qopen").first.click()
    page.wait_for_timeout(150)
    acc = page.locator(".qrow.is-open button.act:visible:has-text('Accept work')").first
    if not acc.count():                                                 # DENOMINATOR
        print("      NOT MEASURED: no `Accept work` on this board, so there is no act with a "
              "real inverse to reverse.")
        check("an act with a real inverse is on the board", False, "0 acceptances available")
        return
    check("an act with a real inverse is on the board", True)
    item = page.evaluate("() => document.querySelector('.qrow.is-open').id")
    acc.click()
    page.wait_for_selector('[data-region="receipts"] .receipt', timeout=20000)
    page.wait_for_timeout(250)
    stripe = page.evaluate("""() => { const r =
        document.querySelector('[data-region="receipts"] .receipt');
        return {undo: !!r.querySelector('form.act-form button'),
                why: (r.querySelector('.why') || {}).textContent || ''}; }""")
    page.mouse.click(4, 400)
    page.keyboard.press("Control+z")
    if stripe["undo"]:
        page.wait_for_function("id => !!document.getElementById(id)", arg=item, timeout=20000)
        check("Ctrl+Z ran the real inverse and the item came back", True)
    else:
        page.wait_for_timeout(400)
        s = page.evaluate(SCREEN)
        check("Ctrl+Z on an act with no inverse says so in that verb's own terms",
              "cannot be undone" in (s["say"] or ""), repr(s["say"]))


def v_says_what_is_missing(page):
    """V is BUILT now, so what is missing changed and this check follows it. 2026-08-31.

    THE DOCTRINE IS UNCHANGED AND THAT IS WHY THIS IS NOT TOLERANCE-WIDENING. The rule this
    check enforces is "a bound key either acts or says what is missing, and never silently does
    nothing" -- item 10's defect. When it was written, what was missing was the FEATURE, and it
    asserted the words `not built`. The operator then overruled MUST-NOT-BUILD item 5 in his own
    words -- *"i want to override and say voice is needed to make it easy for users to get
    through inbox"* -- and `web/actions.py:voice_note` shipped.

    So `not built` is now a FALSE sentence, and a check demanding the console keep saying it
    would be pinning a lie. What is missing on the Shape tier with no row focused is not the
    feature; it is the ROW, because a voice note lands ON an item. That is what V now says, and
    it is what this asserts.

    WHAT IS DELIBERATELY STILL ASSERTED: the key must still explain rather than no-op, and it
    must still name the path that does exist. Both survive verbatim below against the new
    sentence. A faked transcription remains the worse failure and `transitions.py` still refuses
    a body under a status that says the engine heard nothing.
    """
    to_the_queue(page, "Shape")
    page.keyboard.press("v")
    s = page.evaluate(SCREEN)
    say = s["say"] or ""
    check("V does not silently do nothing", bool(say.strip()), repr(say))
    check("V states what it needs, which is a row", "row" in say.lower(), repr(say))
    check("V names the path that does exist", "arrows" in say.lower() or "?" in say, repr(say))
    check("V no longer claims the feature is unbuilt", "not built" not in say.lower(), repr(say))
    page.keyboard.press("?")
    s = page.evaluate(SCREEN)
    check("? states the keys", "space open and close" in (s["say"] or ""), repr(s["say"]))
    low = (s["say"] or "").lower()
    check("MUST-NOT-BUILD item 8: the legend keeps no score",
          not any(w in low for w in ("points", "streak", "leaderboard", "%")), repr(s["say"]))


# ---------------------------------------------------------------------------- 2. deep work

def deep_work_removes_the_counters_and_keeps_item_9(page):
    to_the_queue(page, "Judge")
    off = page.evaluate(DEEPCOUNT)
    page.click(".deepbar a.deepbtn")
    page.wait_for_load_state("load")
    page.wait_for_timeout(150)
    on = page.evaluate(DEEPCOUNT)
    print(f"      deep off: first row y={off['firstRowTop']}, {off['tierChips']} tier chip(s), "
          f"fast pane {off['fastpaneBytes']} bytes, run strip {off['runstrip']}, "
          f"canvas {off['canvas']}")
    print(f"      deep on : first row y={on['firstRowTop']}, {on['tierChips']} tier chip(s), "
          f"fast pane {on['fastpaneBytes']} bytes, run strip {on['runstrip']}, "
          f"canvas {on['canvas']}")
    check("item 9: no fast-lane card, no fast-pane text, no run strip, no canvas, in the MARKUP",
          on["cards"] == 0 and on["fastpaneBytes"] == 0 and on["runstrip"] == 0
          and on["canvas"] == 0, str(on))
    check("his ask 9: the tier counters are absent from the markup, not hidden",
          on["tierChips"] == 0 and on["tiersBytes"] == 0,
          f"{on['tierChips']} chips, {on['tiersBytes']} bytes")
    check("the region host stays, so the poll contract is unchanged", on["tiersRegion"] == 1,
          f"{on['tiersRegion']}")
    check("the count survives where item 9 puts it, in the deep header", bool(on["deepCount"]),
          repr(on["deepCount"]))
    check("removing the counters reclaimed pixels above row one",
          (off["firstRowTop"] or 0) > (on["firstRowTop"] or 0),
          f"{off['firstRowTop']} -> {on['firstRowTop']}")
    if on["rows"] < 2:                                                  # DENOMINATOR
        check("deep work has more than one row, so greying has a subject", False,
              f"{on['rows']} row(s)")
        return
    check("deep work has more than one row, so greying has a subject", True)
    check("deep work arrives with one item already in the right panel",
          on["panel"] is not None and on["panel"] == on["cursor"],
          f"cursor {on['cursor']} panel {on['panel']}")
    focused = [t for t in on["titleInks"] if t["cursor"]]
    others = {t["color"] for t in on["titleInks"] if not t["cursor"]}
    check("the other rows are rendered in a different ink from the focused one",
          len(focused) == 1 and focused[0]["color"] not in others,
          f"{[f['color'] for f in focused]} vs {others}")
    # LEAVE DEEP WORK THROUGH THE ONLY DOOR THERE IS, and leave it for the checks after this one.
    # Deep work is a COOKIE the server reads, so it outlives the navigation: the first version of
    # this file left it on and every later check landed on a surface with no rooms nav at all,
    # which read as "the Queue tab does not exist" rather than "you are still in deep work".
    # DS 3.4: `leave` in the deep header is the only exit, so that is what is clicked.
    # `console.js` upgrades this click into a 420ms choreography before the navigation is
    # issued, so waiting on `load` alone reads the OLD document and reports an exit that worked
    # as an exit that did not. Wait for the thing whose return IS the exit: the rooms nav.
    page.click("#deephd a.dhleave, a.dhleave")
    left = True
    try:
        page.wait_for_selector("nav.rooms a.room", timeout=15000)
    except Exception:                                                   # noqa: BLE001
        left = False
    check("`leave` is the only exit and it works", left,
          "the rooms nav did not come back within 15s of clicking leave")


DEEPCOUNT = """() => {
  const vis = e => { if (!e) return false; const r = e.getBoundingClientRect();
                     return r.width > 0 && r.height > 0; };
  const cur = document.querySelector('.qrow.is-cursor');
  const pan = document.querySelector('.qrow.is-panel > .qpanel');
  const t = document.querySelector('[data-region="tiers"]');
  const fp = document.querySelector('[data-region="fastpane"]');
  const dc = document.querySelector('[data-region="deepcount"]');
  const r0 = document.querySelector('.qrow');
  return {
    cards: document.querySelectorAll('.card.mini, [data-region="fastpane"] .card').length,
    fastpaneBytes: fp ? fp.textContent.trim().length : 0,
    runstrip: document.querySelectorAll('.runbtn').length,
    canvas: document.querySelectorAll('canvas#cf').length,
    tiersRegion: document.querySelectorAll('[data-region="tiers"]').length,
    tierChips: document.querySelectorAll('.tier').length,
    tiersBytes: t ? t.textContent.trim().length : 0,
    deepCount: dc ? dc.textContent.trim() : null,
    rows: document.querySelectorAll('.qrow').length,
    cursor: cur ? cur.id : null,
    panel: pan && vis(pan) ? pan.parentElement.id : null,
    firstRowTop: r0 ? Math.round(r0.getBoundingClientRect().top + scrollY) : null,
    titleInks: [...document.querySelectorAll('.qrow')].map(r => ({
      id: r.id, cursor: r.classList.contains('is-cursor'),
      color: getComputedStyle(r.querySelector('.qctitle')).color })),
  };
}"""


# ------------------------------------------------------------------------------- 3. the phone

PHONE_URLS = ["/", "/queue?tier=decide", "/queue?tier=judge", "/queue?tier=shape",
              "/queue?tier=decide&deep=1", "/brief", "/fleet"]


def the_phone_does_not_scroll_sideways(browser):
    """A fresh context per screen, nothing clicked, exactly as lane B6 measured it."""
    n = 0
    for url in PHONE_URLS:
        ctx = browser.new_context(viewport=PHONE)
        p = ctx.new_page()
        p.goto(BASE + url, wait_until="load")
        d = p.evaluate("""() => ({sw: document.documentElement.scrollWidth,
                                  cw: document.documentElement.clientWidth,
                                  off: [...document.querySelectorAll('a.room, a.subroom')]
                                    .filter(a => { const r = a.getBoundingClientRect();
                                                   return r.right > innerWidth + 0.5 ||
                                                          r.left < -0.5; })
                                    .map(a => a.textContent.trim())})""")
        n += 1
        check(f"no page-level horizontal scroll at 390 on {url}", d["sw"] <= d["cw"] + 1,
              f"scrollWidth {d['sw']} against clientWidth {d['cw']}")
        check(f"every room tab is inside the viewport at 390 on {url}", not d["off"],
              f"outside: {d['off']}")
        ctx.close()
    if n == 0:                                                          # DENOMINATOR
        check("a phone screen was measured", False, "0 of 0")
    print(f"      {n} of {len(PHONE_URLS)} screens measured at 390x844")


# ----------------------------------------------------------------- 4. and 5. headers and copy

def a_page_is_never_a_stored_copy():
    """No browser needed: this is a header, and a header is what a cache reads.

    THE VALUE CHANGED ON 2026-08-31 AND THE CHECK CHANGED WITH IT, on his ruling. It asserted
    `no-store`, which shipped on the reasoning that `no-cache` "is enough for the HTTP cache and
    NOT enough for the back-forward cache". That was never measured. A/B/C'd on a real console,
    adding a row to the store while the page was away and pressing back:

        no-store                  back ~590ms   stale at load in 4 of 5 runs
        no-cache, must-revalidate back  ~85ms   fresh at load in 2 of 2 runs
        no header at all          back  ~74ms   fresh at load in 1 of 1 run

    Eight times slower AND less fresh than the option that was skipped, and when it was stale what
    fixed it was the three-second poll, not the header. So the assertion moved to the value that
    actually delivers what the item is about: the page you come back to is the page the store has.

    `must-revalidate` is asserted as part of the value rather than left optional. `no-cache` alone
    still permits a cache to serve a stale copy while it is unreachable, which would put yesterday's
    queue on the screen on a flaky connection with nothing saying so, and that is the same class of
    lie the whole item exists against.
    """
    seen = 0
    WANT = "no-cache, must-revalidate"
    for path, want in ((f"/queue?tier=judge", WANT), ("/", WANT),
                       ("/api/patch/queue?tier=judge", None), ("/api/health", None)):
        req = urllib.request.Request(BASE + path, method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as r:
            cc = r.headers.get("Cache-Control")
            ctype = (r.headers.get("Content-Type") or "").split(";")[0]
        seen += 1
        if want:
            check(f"{path} is served `Cache-Control: {want}`", cc == want,
                  f"got {cc!r} on {ctype}")
        else:
            # THE JSON SURFACES ARE LEFT ALONE, which is the other half of the claim: the hook
            # is scoped by the response's own content type, so the three-second patch endpoint and
            # the health probe never see it whatever the HTML value becomes.
            check(f"{path} ({ctype}) is left alone", cc != WANT, f"got {cc!r}")
    if seen == 0:                                                       # DENOMINATOR
        check("a response was read", False, "0 of 0")


def the_copy_ruling_holds(page):
    """D-06 of 2026-08-29, taken from his own recording. Copy rots silently, so it is a check."""
    seen, bad = 0, []
    for url in ("/queue?tier=decide", "/queue?tier=judge", "/queue?tier=shape", "/queue/stack"):
        page.goto(BASE + url, wait_until="load")
        html = page.content()
        seen += 1
        for phrase in ("Accept default", "Silence ships"):
            if phrase in html:
                bad.append(f"{url}: {phrase!r}")
    if seen == 0:                                                       # DENOMINATOR
        check("a surface was read", False, "0 of 0")
        return
    check(f"neither phrase he rejected is on any of the {seen} surfaces read", not bad,
          "; ".join(bad))
    page.goto(BASE + "/queue?tier=decide", wait_until="load")
    html = page.content()
    has_default = "accept_default" in html
    if not has_default:                                                 # DENOMINATOR
        print("      NOT MEASURED: no card on this board offers the default verb, so the two "
              "replacement strings have nothing to appear on.")
        check("a card offering the default verb is on the board", False, "0 of them")
        return
    check("a card offering the default verb is on the board", True)
    check("the button reads `Go with default`", "Go with default" in html)
    check("the default line reads `If you say nothing:`", "If you say nothing:" in html)


def main() -> int:
    served = _console_guard.refuse_unless_scratch(BASE, what="test_the_keyboard_and_the_phone")
    print(f"      console at {BASE} is pid {served.get('pid')} on database "
          f"{served.get('db')!r}")
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        page = b.new_context(viewport=DESK).new_page()
        errs: list[str] = []
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        try:
            for name, fn in (
                    ("the keyboard moves, opens and closes", the_keyboard_moves_opens_and_closes),
                    ("a number key picks an option or says why not",
                     a_number_key_picks_an_option_or_says_why_not),
                    ("V says what is missing rather than doing nothing", v_says_what_is_missing),
                    ("deep work removes the counters and keeps item 9",
                     deep_work_removes_the_counters_and_keeps_item_9),
                    ("the 0420 copy ruling holds", the_copy_ruling_holds),
                    # LAST, because it is the only one here that writes to the store.
                    ("Ctrl+Z is never silent", ctrl_z_is_never_silent)):
                print(f"--- {name}")
                fn(page)
            print("--- the phone does not scroll sideways")
            the_phone_does_not_scroll_sideways(b)
            print("--- a page is never a stored copy")
            a_page_is_never_a_stored_copy()
            check("no javascript error on any surface driven above", not errs,
                  "; ".join(errs[:3]))
        finally:
            b.close()
    n = len(PASS) + len(FAIL)
    if n == 0:                                                          # DENOMINATOR
        print("\n0 checks made. A verdict over an empty set is not a pass.")
        return 2
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed  (of {n} checks; desktop "
          f"{DESK['width']}x{DESK['height']}, phone {PHONE['width']}x{PHONE['height']})")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
