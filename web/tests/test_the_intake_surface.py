#!/usr/bin/env python3
"""ROW 0438, HIS ASK 7: the intake surface, and the badge MUST-NOT-BUILD item 7's overrule bought.

Lane D1, 2026-08-30. Five claims, and none of them is a claim that a selector exists.

  1. A PERSON REACHES IT BY CLICKING. From the front page, one click on a room tab, and the wait
     is printed in milliseconds. `web/model.py` can be right about the store and the operator can
     still have no way to the page: lane C1 measured exactly that state, 10 of 10 console routes
     rendering zero mention of a row that had landed in his live store.

  2. THE BADGE IS AMBER AND IT IS NEVER RED, read off the PAINTED ELEMENT in both themes.
     MUST-NOT-BUILD item 7 -- unread counts, red badges, notification inboxes -- was overruled by
     the operator on 2026-08-28 for this surface only, on the stated condition that it ships
     amber, because the incident behind item 7 is a RED count describing things that were merely
     WAITING: *"Red means damage and nothing else, shell wide, or there is no colour left for a
     thing that is actually broken."* A class name is not a colour, so this compares the computed
     colour against the `--wait` and `--dmg` tokens as the browser resolved them.

  3. THE COUNT IS THE NARROW ONE. The second condition on that overrule is that the badge shows
     only what is genuinely waiting on him, never everything unprocessed. So this suite lands two
     objectives, ACCEPTS ONE, and asserts the badge reads 1 while the table holds 2. A badge that
     counted every row would pass every markup assertion ever written and fail this one.

  4. ZERO IS AN ABSENCE. With nothing waiting there is no badge element at all, and the page says
     so in words. Watched failing first: the zero state is asserted BEFORE anything is landed, so
     the badge assertions afterwards are a change and not a coincidence.

  5. THE PROPOSALS ARE READ AND NOT PRETENDED. His ask asks intake to show AI-proposed actions
     for approval. Nothing proposes anything against an intake item today, so the surface states
     that absence with its denominator -- and this suite proves the statement is a READ by
     raising a real recommendation through the registered verb and watching the absence turn into
     a rendered proposal carrying its argument against itself.

  6. THE ROOM CALLS ZERO VERBS, enforced server side, the way Study and Sessions are. Nothing was
     added to `ROOM_VERBS` to build this page, so `POST /intake/act` is refused before a row is
     looked up.

Run:  BASE=http://127.0.0.1:3191 python3 -m web.tests.test_the_intake_surface

The console at BASE must be on a scratch store: `_console_guard` reads the SERVING PROCESS and
not the port, and refuses `brain` and `brain_scratch`. THIS SUITE WRITES: it accepts whatever is
already in the inbox so that the zero state is reachable, then lands its own rows. There is no
verb that deletes an objective, which is exactly why it must never point at the live store.
"""

from __future__ import annotations

import os
import sys
import time

_R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_R, os.path.join(_R, "engine"), os.path.join(_R, "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _console_guard                                                   # noqa: E402
import store                                                            # noqa: E402
import swarm_engine.transitions                                         # noqa: E402,F401
import human_queue.transitions                                          # noqa: E402,F401
from playwright.sync_api import sync_playwright                         # noqa: E402

BASE = os.environ.get("BASE", "http://127.0.0.1:3191")
CHROME = os.environ.get(
    "CHROME", os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux/chrome"))
DESK = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}
TAG = f"d1-{os.getpid()}"

PASS: list[str] = []
FAIL: list[str] = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'' if ok else '   ' + detail}")
    return bool(ok)


def note(s):
    print(f"  ..    {s}")


#: The badge, the tab and the two colour tokens in one read, so a check compares one snapshot.
#: MOVED 2026-09-14 WITH THE FIVE-TAB NAV (D-INFINITY-UX-RULINGS-1, MUST-NOT-BUILD item 11 amended):
#: Intake is the Inbox group's second room, so its link lives in `nav.subrooms` as `a.subroom`, and
#: the badge rides it there as well as on the Inbox tab. Same four readings, new address.
BADGE = """() => {
  const a = Array.from(document.querySelectorAll('nav.subrooms a.subroom'))
                 .find(x => x.getAttribute('href') === '/intake');
  if (!a) return {tab: false};
  const b = a.querySelector('.gate');
  const r = a.getBoundingClientRect();
  const probe = document.createElement('span');
  probe.style.color = 'var(--wait)'; document.body.appendChild(probe);
  const wait = getComputedStyle(probe).color;
  probe.style.color = 'var(--dmg)';
  const dmg = getComputedStyle(probe).color;
  probe.remove();
  return {
    tab: true, tabText: a.innerText.trim(),
    index: Array.from(document.querySelectorAll('nav.subrooms a.subroom')).indexOf(a),
    inViewport: r.x >= 0 && r.right <= window.innerWidth,
    badge: !!b,
    badgeText: b ? b.textContent.trim() : null,
    color: b ? getComputedStyle(b).color : null,
    border: b ? getComputedStyle(b).borderTopColor : null,
    wait, dmg,
    theme: document.documentElement.getAttribute('data-theme'),
  };
}"""

DMG_SWEEP = """() => {
  const probe = document.createElement('span');
  probe.style.color = 'var(--dmg)'; document.body.appendChild(probe);
  const dmg = getComputedStyle(probe).color; probe.remove();
  const hits = [];
  for (const e of document.querySelectorAll('header *, .shell *')) {
    const c = getComputedStyle(e);
    if (c.color === dmg || c.borderTopColor === dmg || c.borderLeftColor === dmg
        || c.backgroundColor === dmg) hits.push(e.className || e.tagName);
  }
  return {dmg, n: document.querySelectorAll('header *, .shell *').length, hits};
}"""


def counts():
    with store.read() as s:
        return {
            "inbox": int(s.scalar("SELECT count(*) FROM brain.objective WHERE state='inbox'")),
            "total": int(s.scalar("SELECT count(*) FROM brain.objective")),
        }


def page_number(page):
    """The number the PAGE says, read out of its own text rather than out of a template."""
    return page.evaluate(
        "() => { const m = (document.body.innerText.match(/(\\d+) waiting/) || []); "
        "return m[1] || null; }")


def badge_number(state):
    return "".join(c for c in (state.get("badgeText") or "") if c.isdigit())


def to_intake_by_clicking(page, theme="dark", base=None):
    """From the front page, by CLICKING. The reachability claim, and it is made every time.

    Retried once and the retry is PRINTED, never swallowed: this suite runs beside other lanes'
    browser suites on a WSL2 host where a click on a room tab has hung for its whole timeout while
    a curl of the same URL answered in under a second.
    """
    for attempt in (1, 2):
        try:
            page.goto(f"{base or BASE}/?theme={theme}", wait_until="load")
            t0 = time.monotonic()
            # The front page is the Attention inbox (ab6917e), so the Inbox row with Intake in it is
            # on screen: still ONE click from the front page, now on the row rather than the strip.
            page.click("nav.subrooms a.subroom[href='/intake']", timeout=8000)
            page.wait_for_selector("h4", timeout=15000)
            return (time.monotonic() - t0) * 1000.0
        except Exception as exc:                                        # noqa: BLE001
            if attempt == 2:
                raise
            print(f"      RETRYING the click on the Intake tab: {type(exc).__name__}")
            page.wait_for_timeout(1500)
    return 0.0


def land(name, body):
    return store.apply("intake", name=name, body=body, source_name=f"{name}.md",
                       source_signature=f"{len(body)}:{int(time.time())}", bytes_=len(body))


def main():                                                             # noqa: C901
    who = _console_guard.refuse_unless_scratch(BASE, what="the intake surface suite")
    print(f"{__file__.rsplit('/', 1)[-1]}  --  the intake surface, against {BASE}")
    print(f"      console pid {who['pid']} on database {who['db']!r} "
          f"(read from /proc/{who['pid']}/environ)")

    # ---------------------------------------------------------------- the fixture, and its
    # denominator. Whatever is in the inbox is accepted first so the ZERO STATE is reachable:
    # this is the only way to watch the badge's absence before watching its presence, and a badge
    # that has never been seen absent has not been shown to depend on anything.
    before = counts()
    with store.read() as s:
        stale = [r["name"] for r in
                 s.query("SELECT name FROM brain.objective WHERE state='inbox'")]
    for n in stale:
        store.apply("accept", name=n)
    cleared = counts()
    print(f"\nfixture · {before['inbox']} waiting of {before['total']} row(s) on arrival; "
          f"{len(stale)} accepted to reach the zero state; now {cleared['inbox']} waiting of "
          f"{cleared['total']}")
    if cleared["inbox"] != 0:
        print("  NOT RUN: the inbox could not be emptied, so the zero state cannot be watched.")
        return 77

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROME)
        ctx = b.new_context(viewport=DESK)
        page = ctx.new_page()
        try:
            # ---------------------------------------------------- 4. ZERO IS AN ABSENCE
            print("\ncase 1 · nothing waiting: no badge, and the page says so")
            ms = to_intake_by_clicking(page)
            note(f"front page to the intake surface, ONE CLICK: {ms:.0f} ms")
            check("clicking the room tab lands on /intake", page.url.endswith("/intake"),
                  page.url)
            z = page.evaluate(BADGE)
            check("the Intake room is in the Inbox group's row of rooms", z.get("tab"), str(z))
            check("with nothing waiting there is NO badge element", not z.get("badge"),
                  f"badge reads {z.get('badgeText')!r}")
            # CASE-INSENSITIVE, and the reason is worth one line: `.blk h4` is
            # `text-transform:uppercase`, so `innerText` returns what is PAINTED and a
            # case-sensitive match here fails on a heading that is on the screen and correct.
            check("and the page says it in words",
                  "nothing is waiting" in page.inner_text("body").lower(),
                  page.inner_text("body")[:200])
            check("no item block is rendered",
                  page.evaluate("() => document.querySelectorAll('[data-intake]').length") == 0)

            # ---------------------------------------------------- 1+3. LANDED, THEN NARROWED
            print("\ncase 2 · two items land through the intake door")
            a = f"{TAG}-a-client-call"
            c = f"{TAG}-b-planning-note"
            land(a, f"# The {TAG} client call\n\nThe first line of a dropped file, unclassified.\n")
            land(c, f"# {TAG} planning\n\nA second file so the badge has something above one.\n")
            landed = counts()
            note(f"denominator: 2 landed through `intake`; store now {landed['inbox']} waiting "
                 f"of {landed['total']} row(s)")
            ms = to_intake_by_clicking(page)
            note(f"front page to the intake surface, ONE CLICK: {ms:.0f} ms")
            items = page.evaluate("() => Array.from(document.querySelectorAll('[data-intake]'))"
                                  ".map(e => e.getAttribute('data-intake'))")
            check("both items are rendered", set(items) == {a, c}, f"rendered {items}")
            check("the newest is first", items and items[0] == c, f"rendered {items}")
            check("the item's own first line is on the page, not a summary of it",
                  f"The {TAG} client call" in page.inner_text("body"))
            st = page.evaluate(BADGE)
            check("the badge appeared", st.get("badge"), str(st))
            check("it counts 2", badge_number(st) == "2", f"badge reads {st.get('badgeText')!r}")
            check("the badge and the page agree", badge_number(st) == page_number(page),
                  f"badge {badge_number(st)} page {page_number(page)}")

            # ------------------------------- THE CONTENT ON THIS PAGE IS WRITTEN BY OUTSIDERS
            #
            # This is the ONLY room whose body text comes from outside the operation: an email, a
            # meeting-notes workflow, a file somebody sent him. Everything else on this console is
            # written by his own agents. So the escaping is a property of the surface and not a
            # framework detail to assume, and it is asserted the only way that means anything:
            # by landing markup through the real door and asking the BROWSER what it built.
            print("\ncase 2b · a dropped file is text, never markup")
            x = f"{TAG}-c-hostile"
            land(x, "# <script>window.__d1_ran = 1</script>\n\n"
                    "<img src=x onerror=\"window.__d1_img = 1\">\n")
            to_intake_by_clicking(page)
            ran = page.evaluate("() => !!(window.__d1_ran || window.__d1_img)")
            check("script in a dropped file does not execute", not ran,
                  "a dropped file ran javascript on his console")
            check("and there is no injected element either",
                  page.evaluate("() => document.querySelectorAll('img[src=\"x\"]').length") == 0)
            check("the markup is shown as the text it is",
                  "<script>" in page.inner_text("body"),
                  "the escaped text is not on the page, so this proved nothing")
            store.apply("accept", name=x)

            print("\ncase 3 · one is accepted: the count is the NARROW one")
            store.apply("accept", name=a)
            after = counts()
            note(f"denominator: {after['inbox']} waiting of {after['total']} row(s) in the table")
            to_intake_by_clicking(page)
            st = page.evaluate(BADGE)
            check("the badge follows the store down to 1", badge_number(st) == "1",
                  f"badge reads {st.get('badgeText')!r} over {after} ")
            check("IT IS NOT COUNTING EVERY ROW, which is the overrule's second condition",
                  badge_number(st) != str(after["total"]),
                  f"badge {badge_number(st)} equals the table's {after['total']}")
            check("the accepted item has left the list",
                  page.evaluate("() => Array.from(document.querySelectorAll('[data-intake]'))"
                                ".map(e => e.getAttribute('data-intake'))") == [c])

            # ---------------------------------------------------- 2. THE COLOUR, BOTH THEMES
            print("\ncase 4 · the badge is amber and never red, in both themes")
            # A CONTEXT PER THEME, WITH THE THEME IN localStorage, and the first version of this
            # case was WRONG in a way worth recording: it put `?theme=light` on the front page and
            # then CLICKED to `/intake`, which carries no query string, so the light assertions
            # ran against a dark page and passed. `base.html`'s boot script reads
            # `localStorage['vd-theme']` when the server states no theme, which is how the
            # operator's own browser holds it, so setting that is both the honest fixture and the
            # real mechanism. Measured before the fix: `--wait` read the same rgb in both
            # "themes", which is the tell.
            for theme in ("dark", "light"):
                tctx = b.new_context(viewport=DESK)
                tctx.add_init_script(
                    f"try{{localStorage.setItem('vd-theme', {theme!r})}}catch(e){{}}")
                tpage = tctx.new_page()
                to_intake_by_clicking(tpage, theme=theme)
                st = tpage.evaluate(BADGE)
                check(f"[{theme}] the page is actually in that theme", st.get("theme") == theme,
                      f"data-theme is {st.get('theme')!r}")
                check(f"[{theme}] the badge is painted --wait", st.get("color") == st.get("wait"),
                      f"{st.get('color')} vs --wait {st.get('wait')}")
                check(f"[{theme}] the badge is NOT painted --dmg",
                      st.get("color") != st.get("dmg"),
                      f"{st.get('color')} vs --dmg {st.get('dmg')}")
                check(f"[{theme}] its border is amber too", st.get("border") == st.get("wait"),
                      f"{st.get('border')} vs --wait {st.get('wait')}")
                check(f"[{theme}] the count carries a WORD and is never a bare number",
                      any(ch.isalpha() for ch in (st.get("badgeText") or "")),
                      f"badge reads {st.get('badgeText')!r}")
                sweep = tpage.evaluate(DMG_SWEEP)
                check(f"[{theme}] nothing on the intake surface spends the damage colour",
                      len(sweep["hits"]) == 0,
                      f"{len(sweep['hits'])} of {sweep['n']} element(s): {sweep['hits'][:4]}")
                note(f"[{theme}] --wait {st['wait']} · --dmg {st['dmg']} · badge {st['color']} · "
                     f"{len(sweep['hits'])} of {sweep['n']} elements in --dmg")
                tctx.close()

            # ---------------------------------------------------- 5. THE PROPOSALS ARE A READ
            print("\ncase 5 · AI-proposed actions: the absence is a read, not a sentence")
            to_intake_by_clicking(page)
            body = page.inner_text("body")
            check("with none raised the surface says so rather than showing an approve button",
                  "not generated yet" in body, body[-400:])
            check("and it prints the denominator it measured that against",
                  "open recommendation" in body)
            check("no proposal card is rendered",
                  page.evaluate("() => document.querySelectorAll('.finding.wait').length") == 0)
            rid = store.apply(
                "recommend",
                text=f"Add {TAG} to the knowledge graph and raise one task from it",
                rationale="AGAINST: it rests on one dropped file and nothing corroborates it.",
                subject_type="entity", subject_id=c)["id"]
            note(f"raised recommendation r{rid} through the registered verb, subject_type entity")
            to_intake_by_clicking(page)
            body = page.inner_text("body")
            check("the proposal is now rendered", page.evaluate(
                "() => document.querySelectorAll('.finding.wait').length") == 1)
            check("it carries its argument against itself", "AGAINST:" in body)
            check("and the absence sentence is gone", "not generated yet" not in body)
            # THE REFUSAL THAT MAKES THE ABOVE NECESSARY, watched rather than read off the source.
            refused = ""
            try:
                store.apply("recommend", text="x", subject_type="objective", subject_id=c)
            except Exception as exc:                                    # noqa: BLE001
                refused = str(exc)
            check("and `recommend` still refuses an objective subject, which is why the loop "
                  "`0438` describes cannot be raised through the verb today",
                  "unknown subject type" in refused, f"it answered {refused!r}")
            note(f"`recommend --subject-type objective` -> {refused!r}")

            # ---------------------------------------------------- 6. ZERO VERBS
            print("\ncase 6 · the room calls zero verbs, enforced server side")
            r = page.request.post(f"{BASE}/intake/act", form={"action": "accept", "id": c})
            check("POST /intake/act is refused", r.status in (403, 405), f"status {r.status}")
            note(f"POST /intake/act -> {r.status}: nothing was added to ROOM_VERBS for this room")

            # ------------------------------------- 7. A FAILED READ DOES NOT TAKE THE SHELL DOWN
            #
            # THE BADGE IS IN THE CHROME OF EVERY ROOM, so an exception raised counting it would
            # 500 nine screens over a table none of them uses. It is not a hypothetical: the
            # store dropped `brain_runtime` connections twice on this host on 2026-08-29 (lane
            # C3, cause below the database at Docker's port forwarding or WSL2's) and TWICE MORE
            # while this suite was being written -- one of them a 500 on `/api/health` from the
            # very console this run drove, which recovered on the next request.
            #
            # AND ZERO IS NOT THE ANSWER EITHER. A caught read that returns 0 paints "nothing is
            # waiting on you" over an unknown, which is the fabricated number `web/model.py`
            # opens by forbidding. `None` is the third answer and the badge says it in a word.
            print("\ncase 8 · the store drops the connection: unknown, not zero, and not a 500")
            from unittest.mock import patch                             # noqa: PLC0415
            from web import model as MODEL                              # noqa: PLC0415
            from web.app import create_app                              # noqa: PLC0415
            with patch.object(MODEL.store, "read",
                              side_effect=RuntimeError("server closed the connection")):
                got = MODEL.intake_waiting_count()
            check("a failed count answers None and never 0", got is None, f"it answered {got!r}")
            app = create_app()
            with patch.object(MODEL, "intake_waiting_count", return_value=None):
                cl = app.test_client()
                q = cl.get("/queue")
                body = q.get_data(as_text=True)
                check("the other rooms still render", q.status_code == 200,
                      f"/queue answered {q.status_code}")
                check("and the badge says it does not know, in a word",
                      "? waiting" in body, "the badge did not render the unknown state")
                check("it does not say zero", ">0 waiting<" not in body)
            note("with the count unreadable: /queue 200, badge reads '? waiting'")

            # ---------------------------------------------------- the phone
            print("\ncase 7 · the phone, 390x844")
            ctx2 = b.new_context(viewport=PHONE)
            p2 = ctx2.new_page()
            try:
                ms = to_intake_by_clicking(p2)
                note(f"front page to the intake surface on a phone, ONE CLICK: {ms:.0f} ms")
                st = p2.evaluate(BADGE)
                check("the tab and its badge are reachable without scrolling sideways",
                      st.get("inViewport"), str(st))
                geo = p2.evaluate("""() => {
                  const f = document.querySelector('[data-intake]');
                  return {sw: document.documentElement.scrollWidth, iw: window.innerWidth,
                          vh: window.innerHeight,
                          top: f ? Math.round(f.getBoundingClientRect().top) : -1};
                }""")
                check("the page does not scroll sideways", geo["sw"] <= geo["iw"],
                      f"scrollWidth {geo['sw']} > {geo['iw']}")
                check("the first item is on the first screen",
                      0 < geo["top"] < geo["vh"], str(geo))
                note(f"first item at {geo['top']}px of {geo['vh']}px = "
                     f"{100.0*geo['top']/geo['vh']:.0f} percent chrome")
                p2.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                p2.wait_for_timeout(200)
                check("still no sideways scroll at the bottom of the page",
                      p2.evaluate("() => document.documentElement.scrollWidth") <= geo["iw"])
            finally:
                ctx2.close()
        finally:
            ctx.close()
            b.close()

    total = len(PASS) + len(FAIL)
    if total == 0:                                                      # DENOMINATOR
        print("0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed, of {total} compared")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
