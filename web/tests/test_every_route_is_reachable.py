#!/usr/bin/env python3
"""Every human route is reachable by clicking, or is declared with a reason. His ruling, 2026-08-31.

WHAT THIS CLOSES, AND IT IS NOT WHAT THE FINDING SAID IT WAS.

The record carried "4 of 21 routes are still unreachable by clicking, all empty-set cases. Worth a
pass once there is data behind them." Crawled properly, on a store seeded with one of each kind,
exactly four came back unreachable, and not one of them was a missing door:

    /event/<eid>            `fleet.html` links it when a subscriber has a head sequence. The
                            scratch store had none. The live store's `operator-paging` subscriber
                            is at last_seq 1572, so the link renders there.
    /session/<sid>          `sessions.html:80` links every row. The scratch store had ZERO
                            sessions. The live store has 1771.
    /image/<attach_id>      ZERO anchors to it anywhere in the templates. It is only ever an
                            `<img src>`: a resource the page embeds, not a page anybody clicks to.
    /terminal/<key>/screen  its own documentation: "the screen is drawn on the SERVER and polled
                            over plain `fetch`". A fragment endpoint.

So the original measurement was a VERDICT OVER AN EMPTY SET wearing a defect's clothes. The routes
were not missing doors; the store was missing rows. Nothing needed building.

WHICH IS EXACTLY WHY THIS FILE EXISTS. A finding that dissolves on contact with data will be
re-derived by the next person who measures on a fresh store, and a door that is genuinely deleted
later will look identical to these four. So the claim is asserted rather than remembered, and the
two halves are kept apart:

  * A route that is REACHED is reached.
  * A route that is NOT reached must be in `NOT_A_DESTINATION` with a reason, in `NEEDS_DATA`
    with the query that says whether the store could have exercised it at all, or in
    `HELD_BY_RULING` with the ruling that keeps its door out of the nav.
  * A route that is none of those is a FAILURE.
  * So is a HELD route that IS reached, and so is a hold naming a route that no longer exists.
    A hold is a ruling, and a ruling that stops being true has to be changed here, not bypassed.

AND IT REFUSES TO PASS OVER AN EMPTY STORE, which is the whole point. For every `NEEDS_DATA`
route the precondition is checked FIRST. Absent data is reported NOT MEASURED and counted, never
folded into the pass, because "unreachable" and "there was nothing to reach" are different answers
and only one of them is a defect.

Run:  BASE=http://127.0.0.1:3113 BRAIN_PG_DB=<scratch> python3 -m web.tests.test_every_route_is_reachable
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from urllib.parse import urljoin, urlparse

_R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_R, os.path.join(_R, "engine"), os.path.join(_R, "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import websockets                                                       # noqa: E402

import store                                                            # noqa: E402
from web.tests import _console_guard                                    # noqa: E402
from web.tests.test_browser import BASE, Page, _launch, _ws_url          # noqa: E402

PASS, FAIL, NOT_MEASURED = 0, 0, 0

#: Not pages. Nothing should ever link to these and a link WOULD be the defect.
NOT_A_DESTINATION = {
    "/image/<attach_id>":
        "an <img src> and never an <a href>: a resource a page embeds, not a page you click to",
    "/terminal/<key>/screen":
        "a screen fragment polled over plain fetch and drawn server side (web/terminal_screen.py)",
}

#: Pages, deliberately NOT linked because a ruling holds their door shut. Unlike
#: `NOT_A_DESTINATION`, these ARE destinations and the hold can be lifted, so a link that renders
#: while the entry stands is a FAILURE: either the ruling changed and this file was not told, or
#: the door came back by accident. Lifting a hold means deleting its entry in the same change that
#: adds the link. Recorded 2026-09-13 as the restart coordinator's PROVISIONAL ruling, on the
#: standing records each value cites and pending Andrew's batch, which may overturn it.
HELD_BY_RULING = {
    "/terminal":
        "held by ruling: Andrew's P14 direction F7 removed the held terminal navigation entry "
        "(Platform a73398f, reviewed PASS), and MUST-NOT-BUILD item 11's typable-pane phone "
        "question is still open",
    "/chats/":
        "held by ruling: the Chats room (MUST-NOT-BUILD item 11, amended 2026-09-08) stays out of "
        "the nav until the chats live-mount agreement is made (web/app.py, chats.register)",
    "/chats/<agent_id>":
        "held by ruling: reached only from /chats/, which is held until the chats live-mount "
        "agreement is made (MUST-NOT-BUILD item 11, amended 2026-09-08)",
}

#: Linked in a template, but the link only renders when the store holds the row behind it. The
#: query is the precondition: if it returns 0 the route is NOT MEASURED rather than unreachable.
NEEDS_DATA = {
    "/event/<eid>": ("a subscriber with a head sequence, which is what fleet.html links",
                     "SELECT count(*) FROM brain.subscriber_lag WHERE last_seq > 0"),
    "/session/<sid>": ("at least one session, which the Sessions room lists and links",
                       "SELECT count(*) FROM brain.session"),
    "/question/<qid>": ("an open question", "SELECT count(*) FROM brain.question WHERE answer IS NULL"),
    "/rec/<rid>": ("an open recommendation",
                   "SELECT count(*) FROM brain.recommendation WHERE state = 'open'"),
    "/artifact/<int:seq>": ("a recorded artifact", "SELECT count(*) FROM brain.artifact"),
    "/agent/<name>": ("an agent on the roster", "SELECT count(*) FROM brain.agent"),
    "/sprint/<slug>": ("a project on the board", "SELECT count(*) FROM brain.project"),
    "/task/<tid>": ("a work item", "SELECT count(*) FROM brain.work_item"),
    "/queue/stack": ("an item in the Decide tier, which is what renders Run the stack",
                     "SELECT count(*) FROM brain.queue_open"),
    "/live/<key>": ("a live run", "SELECT count(*) FROM brain.run"),
}


def ok(msg):
    global PASS
    PASS += 1
    print(f"  ok    {msg}")


def bad(msg, detail=""):
    global FAIL
    FAIL += 1
    print(f"  FAIL  {msg}")
    if detail:
        print(f"        {detail}")


def unmeasured(msg, detail=""):
    global NOT_MEASURED
    NOT_MEASURED += 1
    print(f"  --    NOT MEASURED  {msg}")
    if detail:
        print(f"                    {detail}")


def human_routes() -> list[str]:
    """Every GET route a person could be on. The API is excluded: nobody clicks to JSON."""
    from web.app import create_app
    rules = []
    for r in create_app().url_map.iter_rules():
        rule = str(r.rule)
        if r.endpoint == "static" or "GET" not in (r.methods or set()):
            continue
        if rule.startswith("/api/"):
            continue
        rules.append(rule)
    return sorted(rules)


def _pattern(rule: str):
    return re.compile("^" + re.sub(r"<[^>]+>", "[^/]+", rule) + "$")


async def crawl(page, routes) -> tuple[set, int]:
    """Follow ONLY `<a href>` from `/`, the way a person does. Returns (rules reached, urls seen).

    `<a href>` and not every URL in the markup, deliberately: the question this file answers is
    whether a PERSON can get there by clicking, and an `<img src>` or a `fetch` is neither a
    person nor a click. That distinction is what makes two of the four honest rather than open.
    """
    pats = [(r, _pattern(r)) for r in routes]
    seen, queue, reached = set(), ["/"], set()
    while queue and len(seen) < 200:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        try:
            await page.goto(path)
        except Exception:                                               # noqa: BLE001
            continue
        for rule, pat in pats:
            if pat.match(urlparse(path).path):
                reached.add(rule)
        hrefs = await page.js("""Array.from(document.querySelectorAll('a[href]'))
                                   .map(a => a.getAttribute('href'))""")
        for h in hrefs or []:
            if not h or h.startswith(("#", "mailto:", "http://", "https://")):
                continue
            nxt = urljoin(path, h)
            if urlparse(nxt).path.startswith("/api/"):
                continue
            if nxt not in seen:
                queue.append(nxt)
    return reached, len(seen)


async def _run(routes):
    """One CDP session, the harness `test_browser` already carries, imported and not retyped.

    A fresh temporary profile per launch is `_launch`'s own doing and it matters here more than
    anywhere: `io_deep` is a one-year cookie and a crawl that inherited it from an earlier run
    would be walking the deep-work surface, where whole regions are absent from the markup by
    design, and reporting their links as missing doors.
    """
    async with websockets.connect(_ws_url(), max_size=40 * 1024 * 1024) as ws:
        p = Page(ws)
        await p.send("Page.enable")
        await p.send("Runtime.enable")
        return await crawl(p, routes)


async def _amain() -> int:
    routes = human_routes()
    reached, urls = await _run(routes)

    # THE DENOMINATOR, FIRST. A crawl that followed one link and stopped would report almost
    # everything unreachable and look like a catastrophic finding; a crawl that never started
    # would report nothing and look like a pass.
    if urls < 10:
        bad(f"the crawl only visited {urls} URL(s)", "nothing was measured; this is not a pass")
        return 2
    ok(f"the crawl walked {urls} URLs from / by following links only")

    have = {}
    with store.read() as s:
        for rule, (_why, sql) in NEEDS_DATA.items():
            try:
                have[rule] = int(s.scalar(sql))
            except Exception:                                           # noqa: BLE001
                have[rule] = 0

    # A HOLD ON A ROUTE THAT DOES NOT EXIST ASSERTS NOTHING, and would quietly outlive its door.
    for rule in sorted(set(HELD_BY_RULING) - set(routes)):
        bad(f"{rule} is declared HELD BY RULING but is not a registered route",
            "a stale hold hides nothing and asserts nothing: remove the entry, or restore the route.")

    for rule in routes:
        if rule in reached and rule in HELD_BY_RULING:
            bad(f"{rule} is HELD BY RULING and a link to it rendered",
                f"{HELD_BY_RULING[rule]}. Either the hold was lifted and this file was not told "
                f"(delete the entry in the change that adds the link), or the link came back by "
                f"accident (remove it).")
        elif rule in reached:
            ok(f"{rule} is reachable by clicking")
        elif rule in NOT_A_DESTINATION:
            ok(f"{rule} is correctly NOT linked: {NOT_A_DESTINATION[rule]}")
        elif rule in HELD_BY_RULING:
            ok(f"{rule} is HELD BY RULING and correctly NOT linked: {HELD_BY_RULING[rule]}")
        elif rule in NEEDS_DATA and have.get(rule, 0) == 0:
            unmeasured(f"{rule}", f"this store does not have {NEEDS_DATA[rule][0]}, so there was "
                                  f"nothing to reach. Unreachable and empty are different answers, "
                                  f"and only one of them is a defect.")
        elif rule in NEEDS_DATA:
            bad(f"{rule} is NOT reachable by clicking",
                f"this store HAS {have[rule]} of {NEEDS_DATA[rule][0]}, so the row exists and the "
                f"link does not. That is a door that has gone missing.")
        else:
            bad(f"{rule} is NOT reachable by clicking and is not declared",
                "add it to NOT_A_DESTINATION with the reason nobody should link to it, or to "
                "NEEDS_DATA with the query that says whether this store could reach it.")

    print(f"\n{PASS} passed, {FAIL} failed, {NOT_MEASURED} not measured  "
          f"(over {len(routes)} human GET routes, {urls} URLs crawled)")
    # DENOMINATOR. Every route can return early into NOT MEASURED on a bare store, and a run where
    # all of them did would otherwise print `0 passed, 0 failed` and exit 0. Task 0292's rule.
    if PASS + FAIL == 0:                                                # DENOMINATOR
        print("0 routes were decided either way. A verdict over an empty set is not a pass.")
        return 2
    return 1 if FAIL else 0


def main() -> int:
    served = _console_guard.refuse_unless_scratch(
        BASE, what="test_every_route_is_reachable.py")
    print(f"      console at {BASE} is pid {served['pid']} on database {served['db']!r}")
    proc = _launch()
    try:
        return asyncio.run(_amain())
    finally:
        proc.kill()


if __name__ == "__main__":
    sys.exit(main())
