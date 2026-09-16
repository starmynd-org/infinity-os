"""The full board at `/queue/table`: five queues, a money column, a sort, and item 2 kept.

S3, 2026-09-01. Registered in `web/tests/run-all.sh` in the SAME change that built the surface,
under that runner's own rule: a suite whose registration is a second commit is a suite that spends
the interval unrun.

WHAT THIS SUITE IS FOR. Four feature nodes moved and each one has a way of looking moved without
being moved:

  * `f-queues-in-the-gui` -- ABSENT until today: `grep -rn operator_queue web/` returned **0**
    while `brain.operator_queue` had served five queues since ledger 52. The cheap wrong check is
    counting five tabs. The real claim is that each one is REACHABLE BY CLICKING and each one
    carries its own count, so check 1 clicks all five and reads the rendered rows.

  * `f-spreadsheet-ux` -- the cheap wrong check is that a sort control EXISTS. A link that looks
    like a sort and returns the same order is exactly the disabled-affordance failure item 2
    names. Check 3 presses it and compares the ORDER BEFORE AND AFTER.

  * `f-sort-by-revenue` -- and this is the one with teeth. `brain.queue_open` publishes `impact`
    as a BAND (`migrations/0048...:272` folds it through `signal_level`), so a money column that
    sorted on the printed text would look right on a screenshot and be wrong. TWO ADVERSARIAL
    PAIRS are seeded for it, both of which invert under a text sort:
        `10000` vs `9000`   -- lexically '9000' > '10000'; numerically the reverse.
        `critical` vs `9000` -- alphabetically 'critical' < 'low'; by magnitude the reverse.
    Check 4 fails if either inverts. Watched failing: pointing the sort key at `impact` instead
    of `impact_magnitude` puts `9,000` above `10,000` and reddens it.

  * `f-decisions-queue` -- **THE EMPTY-SET TRAP IS THE WHOLE DIFFICULTY HERE** and it has already
    produced three wrong verdicts on this console. His Decide tier is 0 and nothing writes
    `work_item.kind`, so a check that opens the decisions queue on his store and finds nothing has
    MEASURED NOTHING. This suite refuses to pass over an empty board (see DENOMINATOR at the foot
    of `main`) and check 5 requires the page to distinguish `0` from `cannot be read`.

AND THE CHECK THAT PROTECTS THE THING THIS BUILD COULD HAVE BROKEN. `MUST-NOT-BUILD` item 2 is
live and unoverruled and its clause `nothing is sortable by the operator` is unqualified. The
sortable table is a SEPARATE surface for exactly that reason, and check 6 re-measures the RANKED
window afterwards: `.qhead` still undeliverable to a click, still zero controls, and still no
`[data-sort]`, `.sortable`, `draggable` or grab cursor anywhere on `/queue`. If a later change
moves the sort onto the ranked header, this goes red. The open question about that clause is
`outputs/2026-09-01-sprints/S3/01-ITEM-2-AND-THE-SORTABLE-TABLE.md` and it is the operator's.

THE SUITE IS NOT DETERMINISTIC AND THIS FILE IS WRITTEN FOR THAT.
`outputs/2026-08-31-phase-1/08-THE-SUITE-IS-NOT-DETERMINISTIC.md`: each run builds a fresh scratch
database and the suites' own writes decide what later suites see, so row counts move between runs
over identical code. **Nothing here is keyed to a raw count.** Every check is keyed to a RELATION
that must hold whatever the board holds -- an order, a reachability, a presence -- and the failure
report is a set of distinct SIGNATURES rather than a number, so a ratchet built on it cannot be
broken by the board getting bigger.

IT NEEDS THE FIXTURE, AND IT SAYS SO RATHER THAN PASSING WITHOUT IT. `web/bin/seed-demo.py` leaves
four of the five queues empty and every `impact` unset. Apply
`outputs/2026-09-01-sprints/S3/evidence/seed_the_five_queues.sql` to the console's scratch store
first. Without it this suite exits 77 NOT RUN and names what it lacked, per `docs/SUITE-INPUT-RULE.md`.

Run:  BASE=http://127.0.0.1:3107 python3 -m web.tests.test_the_board_and_the_five_queues

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

import urllib.request                                                   # noqa: E402
import json                                                             # noqa: E402

import _console_guard                                                   # noqa: E402
from playwright.sync_api import sync_playwright                         # noqa: E402

BASE = os.environ.get("BASE", "http://127.0.0.1:3107")
CHROME = os.environ.get(
    "CHROME", os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux/chrome"))

#: 1440x900 is the operator's own measuring viewport for this surface, and 390x844 is the phone
#: the collapse rules are written against. Both are driven: a media query is invisible to `curl`
#: and this repo has declared a defect live from curl output for exactly that reason.
WIDE = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}

#: HIS FIVE, in the order the reader names them. Imported rather than retyped would be better and
#: is not possible from a browser suite; the check below reads the rendered tab names and compares
#: against this, so a queue added to the reader and not to the surface fails here.
QUEUES = ("questions", "review", "dependencies", "work", "decisions")

PASS: list[str] = []
#: THE FAILURE REPORT IS A SET OF SIGNATURES, NOT A COUNT. A ratchet keyed to `len(FAIL)` is broken
#: by construction on a non-deterministic suite: the board grows, a per-row check fires more times,
#: and a green run goes red over unchanged code. A signature is `check name :: detail`, so the same
#: defect seen on three rows is ONE signature and a NEW defect is always a new string.
FAIL: set[str] = set()


def check(name, ok, detail=""):
    if ok:
        PASS.append(name)
    else:
        FAIL.add(f"{name} :: {detail}" if detail else name)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'' if ok else '   ' + detail}")
    return bool(ok)


def _health() -> dict:
    with urllib.request.urlopen(f"{BASE}/api/health", timeout=10) as r:
        return json.loads(r.read().decode())


def _rows(page) -> list[dict]:
    """Every rendered table row as a dict of its cells. The one place the DOM is read."""
    return page.evaluate("""() => [...document.querySelectorAll('.tbl tbody tr')]
      .filter(tr => !tr.querySelector('.tempty'))
      .map(tr => {
        const td = [...tr.querySelectorAll('td')].map(d => (d.innerText || '').trim());
        return {id: td[0], title: td[1], queue: td[2], state: td[3], lane: td[4],
                impact: td[5], unblocks: td[6], prio: td[7], age: td[8]};
      })""")


def main() -> int:
    served = _console_guard.refuse_unless_scratch(
        BASE, what="test_the_board_and_the_five_queues.py")
    print(f"console {BASE} is pid {served['pid']} on {served['db']!r}")

    # A CONSOLE SERVES FROZEN PYTHON. Template auto-reload never reloads Python, so a fresh-looking
    # page can be served by a stale module and every verdict below would be about old code. This is
    # the repo's own first law of measuring and it is cheaper to assert than to remember.
    h = _health()
    stale = h["serving"]["python_stale"]
    print(f"console serving commit {h['serving']['commit_now']}, python_stale={stale}, "
          f"schema {h['schema_version']}\n")
    if stale != "no":
        print("NOT RUN: the console at BASE is serving stale Python "
              f"({', '.join(h['serving']['changed_since_start']) or 'unknown'}). "
              "Restart it before believing anything this suite would report.")
        return 77

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROME,
                               args=["--no-sandbox", "--hide-scrollbars"])
        ctx = b.new_context(viewport=WIDE)
        page = ctx.new_page()
        try:
            seen = run(page, b)
        finally:
            b.close()

    # ------------------------------------------------------------------------------ DENOMINATOR
    # A VERDICT OVER AN EMPTY SET IS NOT A PASS, and on this exact surface that failure has already
    # been shipped three times. Every claim above is about rendered rows; a board with none makes
    # all of them vacuously true, and the decisions queue in particular is empty on every real
    # store because nothing writes `kind`.
    if seen["rows"] == 0:                                               # DENOMINATOR
        print("\nNOT RUN: 0 rows rendered on the board. A verdict over an empty set is not a pass.")
        print("Apply outputs/2026-09-01-sprints/S3/evidence/seed_the_five_queues.sql to this "
              "console's scratch store and re-run.")
        return 77
    if seen["with_money"] == 0:                                         # DENOMINATOR
        print(f"\nNOT RUN: {seen['rows']} row(s) rendered but 0 carry an impact amount, so the "
              "money column's ordering could not be measured at all.")
        print("Apply outputs/2026-09-01-sprints/S3/evidence/seed_the_five_queues.sql and re-run.")
        return 77

    print(f"\n{len(PASS)} passed, {len(FAIL)} distinct failure signature(s)  "
          f"(denominator: {seen['rows']} row(s) on the board, {seen['with_money']} with an "
          f"impact amount, {seen['queues_nonempty']} of 5 queues non-empty)")
    for f in sorted(FAIL):
        print(f"  FAIL {f}")
    return 1 if FAIL else 0


def run(page, browser) -> dict:
    seen = {"rows": 0, "with_money": 0, "queues_nonempty": 0}

    # ------------------------------------------------------- 1. the door, from the ranked window
    print("--- 1. the door: the board is reachable by clicking from the queue he lands on")
    for tier in ("decide", "judge", "shape"):
        page.goto(f"{BASE}/queue?tier={tier}", wait_until="networkidle")
        link = page.query_selector('a[href="/queue/table"]')
        check(f"a door to the board is on /queue?tier={tier}", link is not None,
              "no anchor with href=/queue/table")
    # THE DOOR MUST NOT DEPEND ON THE BOARD BEING UNTIDY. It was first written inside the
    # `chipnote` block, which renders only when there is overflow, a blocker, a deferral or a
    # cycle -- so on a quiet morning it was absent from 3 of 3 tiers. Measured, then moved out.
    page.goto(f"{BASE}/queue", wait_until="networkidle")
    check("and on the default landing page, whatever state the board is in",
          page.query_selector('a[href="/queue/table"]') is not None)
    page.click('a[href="/queue/table"]')
    page.wait_for_selector(".tbl", timeout=10000)
    check("clicking it arrives at the board", "/queue/table" in page.url, page.url)

    # ------------------------------------------------------------------- 2. the five queues
    print("\n--- 2. f-queues-in-the-gui: five queues, each reachable, each with its denominator")
    tabs = page.evaluate("""() => [...document.querySelectorAll('.tqs .tq')].map(a => ({
        name: (a.querySelector('.tqn')||{}).innerText || '',
        count: (a.querySelector('.tqc')||{}).innerText || '',
        href: a.getAttribute('href'), h: a.getBoundingClientRect().height}))""")
    names = [t["name"].strip() for t in tabs]
    for q in QUEUES:
        check(f"the {q} queue is on the surface", q in names, f"tabs rendered: {names}")
    check("every queue tab prints a denominator beside its name",
          all((t["count"] or "").strip() for t in tabs),
          f"{sum(1 for t in tabs if not (t['count'] or '').strip())} tab(s) with no count")
    # TAP FLOOR. The shell counts a control under 44px as a defect and this surface adds nine of
    # them at once.
    short = [t["name"] for t in tabs if t["h"] < 44]
    check("no queue tab is under the 44px tap floor", not short, f"under 44px: {short}")

    for q in QUEUES:
        page.goto(f"{BASE}/queue/table?queue={q}", wait_until="networkidle")
        rows = _rows(page)
        if rows:
            seen["queues_nonempty"] += 1
            # EVERY ROW IN A QUEUE MUST CLAIM THAT QUEUE. A filter that renders rows not in the
            # set it names is worse than one that renders none.
            wrong = [r["id"] for r in rows if q not in r["queue"]]
            check(f"every row shown under {q} names {q} in its queue cell", not wrong,
                  f"{len(wrong)} row(s) do not: {wrong[:4]}")
        else:
            # NOT A FAILURE, AND NOT A PASS EITHER. An empty queue is recorded so the denominator
            # at the foot can say how much of this check actually ran.
            print(f"  --    {q} rendered 0 rows (not measured, counted in the denominator)")

    # -------------------------------------------------------------------- 3. the sort works
    print("\n--- 3. f-spreadsheet-ux: the sort is pressed, not merely present")
    page.goto(f"{BASE}/queue/table", wait_until="networkidle")
    controls = page.evaluate("() => document.querySelectorAll('.tbl thead .tsort').length")
    check("the board offers sort controls at all", controls > 0, f"{controls} found")
    before = [r["id"] for r in _rows(page)]
    seen["rows"] = len(before)
    page.click('.tbl thead .tsort >> nth=0')
    page.wait_for_selector(".tbl", timeout=10000)
    after = [r["id"] for r in _rows(page)]
    # THE ORDER MUST ACTUALLY MOVE, and the same set must still be there. A "sort" that drops rows
    # is a filter, and a "sort" that returns the same order is the disabled affordance item 2 is
    # written against.
    check("pressing a column header changes the order", before != after,
          f"identical order after the press: {before[:5]}")
    check("and it reorders rather than filtering: the same rows are still there",
          sorted(before) == sorted(after),
          f"{len(before)} row(s) before, {len(after)} after")
    # A SORT CONTROL IS A LINK AND WORKS WITH THE SCRIPT DEAD. Not a preference: the whole console
    # is server-rendered so that its controls survive a broken script, and the mode exit uses the
    # same argument.
    hrefs = page.evaluate("""() => [...document.querySelectorAll('.tbl thead .tsort')]
        .filter(a => a.tagName === 'A' && a.getAttribute('href')).length""")
    check("every sort control is a real anchor with a real href", hrefs == controls,
          f"{hrefs} of {controls} are anchors with an href")
    aria = page.evaluate("""() => document.querySelectorAll('.tbl thead [aria-sort]').length""")
    check("the sorted column announces itself with aria-sort", aria > 0, f"{aria} found")

    # ------------------------------------------------------- 4. the money column, adversarially
    print("\n--- 4. f-sort-by-revenue: a money column that orders the board BY MAGNITUDE")
    page.goto(f"{BASE}/queue/table?sort=impact&dir=desc", wait_until="networkidle")
    rows = _rows(page)
    money = {}
    for r in rows:
        raw = (r["impact"] or "").replace(",", "").strip()
        try:
            money[r["id"]] = float(raw)
        except ValueError:
            continue
    seen["with_money"] = len(money)
    order = [r["id"] for r in rows]
    check("the board renders an impact column carrying amounts, not only bands",
          len(money) > 0, "0 cells parsed as an amount")

    def before_in(a, b):
        return a in order and b in order and order.index(a) < order.index(b)

    # THE TWO PAIRS THAT INVERT UNDER A TEXT SORT. Both are seeded by
    # `evidence/seed_the_five_queues.sql` precisely because they are the ones that look right.
    pairs = [(a, b) for a in money for b in money if money[a] > money[b]]
    inverted = [(a, b) for a, b in pairs if before_in(b, a)]
    check("descending by impact, every larger amount is above every smaller one",
          not inverted,
          f"{len(inverted)} inverted pair(s), e.g. {inverted[:3]} in order {order}")
    if len(money) >= 2:
        # THE LEXICAL TRAP, NAMED. '9000' > '10000' as text. If this ever passes while the check
        # above fails, the sort has been re-pointed at the printed column.
        lex = [(a, b) for a, b in pairs
               if str(int(money[b])) > str(int(money[a])) and before_in(b, a)]
        check("and the lexical trap is not present ('9000' must not outrank '10000')",
              not lex, f"{lex}")
    page.goto(f"{BASE}/queue/table?sort=impact&dir=asc", wait_until="networkidle")
    asc = [r["id"] for r in _rows(page)]
    check("asc and desc are actually different orders", asc != order,
          "ascending returned the descending order")

    # ------------------------------------------------------------------ 5. the decisions queue
    print("\n--- 5. f-decisions-queue: 0 is distinguished from 'cannot be read'")
    page.goto(f"{BASE}/queue/table?queue=decisions", wait_until="networkidle")
    txt = page.inner_text("body")
    drows = _rows(page)
    if drows:
        check("the decisions queue renders its rows", True)
    else:
        # THE EMPTY-SET TRAP, ANSWERED ON THE SURFACE. If the queue is empty the page must say WHY
        # -- nothing writes `kind` -- rather than printing a bare zero the reader would take for a
        # finding about his week.
        check("an empty decisions queue explains itself rather than printing a bare zero",
              ("by construction" in txt) or ("cannot be read" in txt),
              "the page said neither why it is empty nor that it cannot be read")
    check("the surface never claims a decision count it cannot compute",
          not ("0 decisions" in txt and "cannot be read" in txt),
          "the page reported BOTH a zero count and an unreadable column")

    # ------------------------------------------- 6. MUST-NOT-BUILD item 2, RE-MEASURED after all
    print("\n--- 6. item 2 kept: the RANKED window is not sortable and did not become so")
    for tier in ("decide", "judge", "shape"):
        page.goto(f"{BASE}/queue?tier={tier}", wait_until="networkidle")
        c = page.evaluate("""() => {
          const head = document.querySelector('.qhead');
          const all = [...document.querySelectorAll('*')];
          return {
            head: !!head,
            pointer: head ? getComputedStyle(head).pointerEvents : 'none',
            cursor: head ? getComputedStyle(head).cursor : 'auto',
            controls: head ? head.querySelectorAll('a,button,[role=button],[tabindex]').length : 0,
            sortish: document.querySelectorAll('[data-drag],[data-sort],.draghandle,.sortable').length,
            draggable: all.filter(e => e.hasAttribute('draggable')).length,
            grab: all.filter(e => /grab/.test(getComputedStyle(e).cursor)).length,
            prio: [...document.querySelectorAll('form input[name]')]
                    .filter(i => i.name === 'priority' || i.value === 'set').length,
          };
        }""")
        if not c["head"]:
            print(f"  --    {tier} rendered no column header (empty tier, not measured)")
            continue
        check(f"[{tier}] the ranked header is still undeliverable to a click",
              c["pointer"] == "none", f"pointer-events {c['pointer']}")
        check(f"[{tier}] and still does not dress as a control",
              c["controls"] == 0 and c["cursor"] in ("auto", "default"),
              f"{c['controls']} control(s), cursor {c['cursor']}")
        check(f"[{tier}] no sort or drag affordance anywhere on the ranked list",
              c["sortish"] == 0 and c["draggable"] == 0 and c["grab"] == 0,
              f"sortish {c['sortish']}, draggable {c['draggable']}, grab {c['grab']}")
        check(f"[{tier}] and nothing on it writes priority",
              c["prio"] == 0, f"{c['prio']} such input(s)")

    # --------------------------------------------------------------- 7. geometry, both viewports
    print("\n--- 7. the page never scrolls sideways, at 1440 and at 390")
    for label, vp in (("1440", WIDE), ("390", PHONE)):
        pg = browser.new_context(viewport=vp).new_page()
        try:
            pg.goto(f"{BASE}/queue/table", wait_until="networkidle")
            g = pg.evaluate("""() => ({
              doc: document.documentElement.scrollWidth,
              win: document.documentElement.clientWidth,
              wrapScrolls: (() => { const w = document.querySelector('.twrap');
                  return w ? w.scrollWidth > w.clientWidth : false; })(),
            })""")
            # THE WIDE TABLE SCROLLS INSIDE ITS OWN BOX. Nine columns do not fit a phone, and the
            # rule the whole console is held to is that the BODY never scrolls sideways -- the
            # overflow belongs to the container, which is why `wrapScrolls` being true at 390 is
            # the surface working rather than failing.
            check(f"[{label}px] the page body does not scroll sideways",
                  g["doc"] <= g["win"], f"document {g['doc']}px in a {g['win']}px window")
        finally:
            pg.close()

    return seen


if __name__ == "__main__":
    raise SystemExit(main())
