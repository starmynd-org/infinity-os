"""THE PHONE SURFACE, BUILT: the four jobs at 390px, and the write door the move breaks.

Task 0168 (V8b), the build half of V8a's architecture (task 0114). Run with a console serving:

    BASE=http://127.0.0.1:3103 BRAIN_PG_DB=brain python3 -m web.tests.test_phone_surface

`BASE` is not spelled here. `web/host.py` is the one configuration point for where this console
is served, and this file asks it, so a check and the console it checks cannot drift apart.

WHY A THIRD BROWSER FILE. `test_browser.py` reseeds and resolves real items, so its list is
ordered and nothing read-only belongs in it (V8a's reasoning, unchanged). `test_phone.py` is
V8a's four checks and they are its claims -- the 900px boundary, the collapsed strip, deep work
at the wire on the Queue, the mode pill on the landing tier. THIS file does not repeat any of
them. It asserts the six things V8b is responsible for:

  1. THE FOUR SURFACES, at 390px, in FOUR COMBOS -- both themes x both modes. The four jobs are
     V8a's and V8a wins where it disagrees with anything later: decide (`/queue`), dispatch
     (`/queue/stack`), watch a run (`/fleet`), read a brief (`/brief`). Not the whole console.
  2. THE TAP-TARGET SWEEP, on the phone side of the frozen 900px boundary. DESIGN-SYSTEM.md
     section 9 [V10-E] makes this a SWEEP and not a list, for the stated reason that a list is
     satisfiable by measuring only the targets someone remembered: it enumerates every control
     present and fails on any one under `--tap`.
  3. SINGLE COLUMN EVERYWHERE (DS section 9's first clause), measured as computed grid columns
     rather than believed from the stylesheet.
  4. DEEP WORK IS ABSOLUTE ON EVERY ROOM A PHONE POLLS, at the patch wire. V8a proved the Queue.
     A phone is a tab left open on a table and the operator lands wherever he left it, so this
     asserts the other three rooms too, and it asserts the shape of the failure: a count may
     appear anywhere, a card may not.
  5. ONE DOCUMENT FOR BOTH WIDTHS, byte for byte. This is the reason a phone-only path to the
     store cannot exist, and it is stronger than any inventory: the collapse is CSS, deep work
     is markup, and width is neither, so every proof `web/tools/thinness_audit.py` makes about
     the console is a proof about the phone with nothing re-derived.
  6. EVERY FORM A PHONE CAN REACH POSTS TO THE ONE WRITE DOOR, `/<room>/act`, with the room a URL
     segment. Beside it, PRINTED and not asserted, the extension V8b owes V8a's table: which of
     the table's actions are reachable at 390px, which are on screen versus one tap behind a
     fold, and which are reachable and not in the table at all.
  7. THE WRITE DOOR SURVIVES THE MOVE. Five origin cases, in process, no browser, nothing
     written. This is the check that would have caught the cutover: see `web/host.py`.

WHAT THIS FILE DOES NOT CLAIM. It measures headless Chromium on Linux at a 390px viewport, which
is a good instrument for layout, the cascade and the wire, and is NOT mobile Safari on a locked
phone. The one number that needs the operator's own device is named in the report and not
guessed at here.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import urllib.request

_R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_R, os.path.join(_R, "engine"), os.path.join(_R, "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import websockets                                                       # noqa: E402

from web import host                                                    # noqa: E402
from web.tests.test_browser import Page, _launch, _ws_url               # noqa: E402
from web.tools.thinness_audit import ACTIONS, PHONE_JOBS                # noqa: E402

# THIS FILE WILL NOT GUESS WHICH CONSOLE IT IS DRIVING (W3-UX-INFINITY, 2026-09-16).
#
# `host.BASE_URL` falls back to `http://127.0.0.1:{CONSOLE_PORT}` and `CONSOLE_PORT` defaults to
# 3103 -- one of the three ports this repo's own CLAUDE.md reserves in bold: "Ports 3103, 3104 and
# 3105 are the operator's. Do not restart, kill or write through them." So `python3 -m
# web.tests.test_phone_surface`, with nothing set, launches a chromium against the operator's live
# console. MEASURED 2026-09-16: it does, and something is serving there.
#
# THE DOCSTRING ABOVE IS NOT WRONG AND IS NOT BEING REVERSED. "`BASE` is not spelled here" is still
# true and still right: one configuration point, so a check and the console it checks cannot drift
# apart. What is added is that the operator must SAY which console, because the fallback's answer
# is the one address a seat must not be pointed at by accident. The documented invocation at the
# top of this file already sets `BASE` explicitly, so nothing that was correct before now refuses.
#
# A REFUSAL RATHER THAN A SAFER DEFAULT. Moving the default to a scratch port would relocate the
# trap: the next reader still cannot tell, from the command they typed, which console they drove.
# A refusal names the problem at the moment it matters and costs one environment variable.
if not os.environ.get("BASE"):
    raise SystemExit(
        "test_phone_surface: refusing to guess a console.\n"
        "  This suite drives a real browser against $BASE. With BASE unset it would resolve to\n"
        "  %s, and 3103/3104/3105 are the operator's own consoles.\n"
        "  Name the console you mean:\n"
        "      BASE=http://127.0.0.1:<your port> python3 -m web.tests.test_phone_surface\n"
        "  web/tests/run-all.sh already exports BASE, so this never fires under the runner."
        % host.BASE_URL)

BASE = host.BASE_URL
PASS: list[str] = []
FAIL: list[tuple[str, str]] = []

PHONE = 390
TAP = 44          # DESIGN-SYSTEM.md section 2.3's `--tap`, section 9's floor. Read back from the
                  # document below rather than trusted from here.

#: The four jobs, and the surface each one is done on. V8a section 7 names them; this is that list
#: and nothing else, because "a phone surface that tries to be everything is worse than one that
#: does four things well" is the whole architecture.
JOBS = [
    ("decide",       "/queue"),
    ("dispatch",     "/queue/stack"),
    ("watch a run",  "/fleet"),
    ("read a brief", "/brief"),
]

#: Both themes x both modes. "Mode" here is the BUILT mode mechanism -- the `io_deep` cookie,
#: server-read, absolute in the markup. DESIGN-SYSTEM.md section 1 settles a second mode
#: (Dispatch) whose shell is V3/V4/V-SHELL's and is not on this surface yet; nothing added by
#: this lane is mode-conditional, so it inherits that mode when it lands rather than needing a
#: port. Stated rather than implied, because "both modes" would otherwise silently mean
#: whichever two the reader had in mind.
COMBOS = [(theme, deep) for theme in ("dark", "light") for deep in (0, 1)]


async def _phone(p: Page, width: int = PHONE):
    await p.send("Emulation.setDeviceMetricsOverride", width=width, height=844,
                 deviceScaleFactor=1, mobile=True)


def _url(path: str, theme: str, deep: int) -> str:
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}theme={theme}&deep={deep}"


def _regions(room: str, query: str = "") -> dict[str, str]:
    """One poll, exactly as `console.js` makes it: same path, same header."""
    req = urllib.request.Request(f"{BASE}/api/patch/{room}?{query}",
                                 headers={"x-console-poll": "1"})
    with urllib.request.urlopen(req, timeout=8) as r:
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


# ---------------------------------------------------------------------------------------- 1+2+3

#: Every control the guide names, plus `summary` and `a`, because a `<details>` handle and a link
#: that acts are both things a thumb has to hit. Enumerated in the DOCUMENT, not listed here: the
#: selector is broad on purpose and the sweep fails on whatever it finds.
SWEEP = """(function(){
  var sel = 'button,select,textarea,input:not([type=hidden]),a[href],'
          + '[role=radio],[role=tab],summary,details>summary';
  var small = [], n = 0, cols = [];
  document.querySelectorAll(sel).forEach(function(el){
    var cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    var r = el.getBoundingClientRect();
    if (!r.width || !r.height) return;               /* laid out, per DS section 9 */
    if (el.type === 'radio' || el.type === 'checkbox') return;  /* the ROW carries the target */
    n++;
    if (r.height < %(tap)d - 0.5) small.push({
        tag: el.tagName, cls: (typeof el.className === 'string' ? el.className : ''),
        h: Math.round(r.height*10)/10, txt: (el.textContent||el.name||'').trim().slice(0,40)});
  });
  document.querySelectorAll('*').forEach(function(el){
    var cs = getComputedStyle(el);
    if (cs.display !== 'grid' && cs.display !== 'inline-grid') return;
    var t = cs.gridTemplateColumns;
    if (!t || t === 'none') return;
    if (t.trim().split(/\\s+/).length > 1) cols.push(
        (el.tagName + '.' + (typeof el.className === 'string' ? el.className : '')).slice(0,44)
        + ' -> ' + t);
  });
  /* THE ROOMS NAV. Three of the four phone jobs live in three different rooms, so a room the
     thumb cannot reach is a job the phone cannot do. `.rooms` carries `overflow-x:auto` with the
     scrollbar hidden, so a room pushed out is still in the document and still tab-reachable and
     announces itself to nobody -- which is why this measures the RECTANGLE against the viewport
     and not the DOM. */
  var offscreen = [], nrooms = 0;
  /* Since the five-tab nav (MUST-NOT-BUILD item 11 amended 2026-09-14) the group's rooms sit on a
     second row as `a.subroom`; a room off screen there is a job off the phone just the same. */
  document.querySelectorAll('a.room, a.subroom').forEach(function(a){
    nrooms++;
    var r = a.getBoundingClientRect();
    if (r.right > innerWidth + 0.5 || r.left < -0.5) offscreen.push(a.textContent.trim());
  });
  return {n: n, small: small, cols: cols, offscreen: offscreen, nrooms: nrooms,
          tap: getComputedStyle(document.documentElement).getPropertyValue('--tap').trim(),
          sw: document.documentElement.scrollWidth,
          cw: document.documentElement.clientWidth,
          h1: document.body.scrollHeight};})()"""


async def the_four_surfaces_hold_at_390(p: Page):
    """Sixteen renders: four jobs x two themes x two modes. No h-scroll, no second column, and
    every laid-out control at or above the `--tap` floor."""
    await _phone(p)
    tap_token = None
    for job, path in JOBS:
        for theme, deep in COMBOS:
            await p.goto(_url(path, theme, deep))
            r = await p.js(SWEEP % {"tap": TAP})
            tag = f"{job:12} {theme:5} deep={deep}"
            tap_token = tap_token or r["tap"]
            assert r["sw"] <= r["cw"] + 1, (
                f"{tag}: page scrolls horizontally, {r['sw']} > {r['cw']}. DS section 9: no "
                f"page-level horizontal scroll at 390.")
            assert not r["cols"], (
                f"{tag}: not single column at 390 -- {r['cols']}. DS section 9's first clause.")
            assert not r["offscreen"], (
                f"{tag}: the rooms {r['offscreen']} are outside the viewport. `.rooms` scrolls "
                f"sideways with the scrollbar hidden, so they are reachable by a swipe nobody "
                f"is told about -- and three of the four phone jobs are in three different "
                f"rooms, so a room off screen is a job off the phone.")
            assert not r["small"], (
                f"{tag}: {len(r['small'])} control(s) under {TAP}px: "
                + "; ".join(f"{s['tag']}.{s['cls']} {s['h']}px {s['txt']!r}"
                            for s in r["small"]))
            # The room count is PRINTED, not assumed. `/queue/stack` is full screen and has no
            # rooms nav at all, so an "all rooms reachable" line there would be a vacuous pass
            # dressed as a check -- the exact failure mode that got this program's earlier
            # verification file superseded.
            print(f"      {tag}  {r['n']:2} controls, all >= {TAP}px, 1 column, no h-scroll, "
                  f"{r['nrooms']} room tab(s), 0 off screen")
    assert tap_token == f"{TAP}px", (
        f"the document's `--tap` is {tap_token!r} and this file asserts {TAP}px. DS section 2.3 "
        f"puts the floor in ONE token so no component drifts off it; a check carrying its own "
        f"copy of the number is a second place for it to drift.")
    print(f"      --tap reads {tap_token} from the document, so the floor above is the token's")


# ------------------------------------------------------------------------------------------- 4

ROOMS_A_PHONE_POLLS = ["queue", "brief", "fleet"]

#: What a card looks like in a patch payload. The `.mini` class is the fast lane's card and
#: `.card` is the list's; either one arriving under deep work is the failure.
CARDish = re.compile(r'class="[^"]*\b(mini|card)\b', re.I)


async def deep_work_is_absolute_on_every_room_a_phone_polls(p: Page):
    """A phone is a tab left open on a table. Nothing arrives -- proven at the wire, per room.

    V8a proved the Queue's `fastpane` and `runbtn` go to zero characters on `tier=judge&deep=1`.
    The operator lands where he left the tab, so the other rooms are asserted here. The claim is
    the frozen rule and not a stronger one: A COUNT MAY APPEAR ANYWHERE, CARDS MAY NOT.
    """
    for room in ROOMS_A_PHONE_POLLS:
        off = _regions(room, "deep=0")
        on = _regions(room, "deep=1")
        assert on, f"/api/patch/{room}?deep=1 returned no regions at all"
        carded = {k: v for k, v in on.items() if CARDish.search(v or "")}
        # The Queue's own list region is the room the operator is IN when he is deciding; deep
        # work suppresses the OTHER tiers there, which is V8a's measured behaviour and its
        # discrepancy note. This check is about the three rooms and the fast lane.
        carded.pop("list", None)
        assert not carded, (
            f"deep work is on and /api/patch/{room} still shipped a card in region(s) "
            f"{sorted(carded)}: {[(k, _text(v)[:60]) for k, v in carded.items()]}")
        grew = {k for k in on if len(on.get(k) or "") > len(off.get(k) or "")}
        print(f"      {room:6} deep=1: {len(on)} regions, 0 carrying a card"
              f"{'; larger than deep=0 in ' + str(sorted(grew)) if grew else ''}")


async def the_poll_stops_when_the_phone_is_face_down(p: Page):
    """`console.js:70` returns early on `document.hidden`, and on a phone that is the whole story.

    Measured rather than read: `document.hidden` is overridden on the prototype, `patch()` is
    given four poll intervals to fire, and the server's own request count is the instrument.
    This is the honest phone consequence -- a locked phone stops asking, so the surface is up to
    one poll stale on unlock and never more, and it is not quietly holding a connection open.
    """
    await _phone(p)
    for _ in range(4):
        await p.goto(_url("/queue", "dark", 0))
        # `console.js` reads the room off `<body data-room>` and returns early without one, so a
        # 500 page would measure zero polls and report it as the poll being off. Same shared
        # Postgres, same reason as the fetch retry above.
        if await p.js("!!document.body.getAttribute('data-room')"):
            break
        await asyncio.sleep(1.0)
    assert await p.js("!!document.body.getAttribute('data-room')"), (
        "/queue did not serve a console document four times running; the poll cannot be measured "
        "against a page that is not there.")
    n0 = await p.js("(function(){window.__t3=0;var f=window.fetch;window.fetch=function(u){"
                    "if((''+u).indexOf('/api/patch/')>=0)window.__t3++;return f.apply(this,arguments)};"
                    "return 0})()")
    await asyncio.sleep(7.0)
    visible = await p.js("window.__t3")
    await p.js("Object.defineProperty(Document.prototype,'hidden',{get:function(){return true},"
               "configurable:true});document.dispatchEvent(new Event('visibilitychange'));"
               "window.__t3=0")
    await asyncio.sleep(7.0)
    hidden = await p.js("window.__t3")
    assert visible >= 1, (f"the visible tab polled {visible} times in 7s at a 3000ms interval; "
                          f"the instrument is not measuring the poll")
    assert hidden == 0, (
        f"the tab reported itself hidden and still made {hidden} poll(s). On a phone that is a "
        f"locked screen: `console.js` must stop asking, or the console spends the operator's "
        f"battery and his tailnet on a screen nobody is looking at.")
    print(f"      visible: {visible} polls in 7s · hidden: {hidden} polls in 7s "
          f"(so the surface is at most one poll stale on unlock, and never mid-connection)")


# ------------------------------------------------------------------------------------------- 5

async def the_phone_is_served_the_same_document_as_the_desktop(p: Page):
    """THE reason no phone-only path can exist, and it is a stronger claim than an inventory.

    An inventory of what the phone renders is a fact about today's templates: move a control and
    the inventory moves, and a check written against it fails for the wrong reason. What actually
    protects the waist is that THERE IS ONE DOCUMENT. The collapse is CSS; deep work is markup;
    width is neither. So this asserts the property directly -- the bytes served at 390px and at
    1440px are the same bytes -- and every proof `web/tools/thinness_audit.py` makes about the
    console is therefore a proof about the phone, with nothing re-derived.

    If this ever fails, someone has branched the surface on viewport at the SERVER, and the next
    thing to arrive is a mobile-only path to the store. That is the failure V00 names and it is
    the one this check exists to catch early, while it is still one template.
    """
    # THE BYTES AS SERVED, not the DOM as it ended up. Measured through `fetch` from inside the
    # emulated page so the request carries the same client hints the page did -- `sec-ch-ua-mobile`
    # is `?1` under mobile emulation and `?0` at 1440, and it is the one thing in an HTTP request
    # that tells a server it is talking to a phone, so a server that branched would branch on it.
    #
    # Reading `outerHTML` instead would compare the LIVE DOM and fail on `<canvas id="cf">`, whose
    # width and height attributes `console.js` sets from the viewport at runtime. Measured: at 390
    # and 1440 the two documents differed by exactly one byte, `width="390"` against `width="1440"`,
    # on that canvas and nowhere else, and two loads at the SAME width were byte-identical. That is
    # a client painting to the size of its own window, which is what a client is for. It is not the
    # server deciding what a phone may see, which is what this check is about.
    for job, path in JOBS:
        for theme, deep in COMBOS[:2]:                  # both modes, one theme: bytes, not colour
            url = _url(path, theme, deep)
            await _phone(p, PHONE)
            await p.goto(url)
            # THREE fetches, phone-wide-phone, with only the emulation changed between them and
            # no reload. `/fleet` carries live heartbeat ages that tick every second, so two
            # fetches a page-load apart differ for a reason that has nothing to do with width --
            # measured: 16538 bytes both times and not the same 16538. The first and third pin
            # that down: if they agree, nothing about the world changed during the window, and
            # any difference in the middle one is the width. If they disagree the window caught a
            # tick and the attempt is spent again rather than reported as a finding.
            # A 500 is not a measurement. The Postgres this reads through is shared with the rest
            # of the fleet and it dropped four connections during one run of this file
            # ("server closed the connection unexpectedly"), which arrives here as a six-line
            # error document. Comparing that against a real one would report an infrastructure
            # blip as a server branching on viewport, which is a false finding and the worst kind
            # -- it is the kind that gets a real check deleted. Retried, and the retry is bounded.
            async def fetch_doc(width: int) -> str:
                await _phone(p, width)
                for attempt in range(4):
                    body = await p.js(f"fetch({url!r}).then(function(r){{return r.text()}})")
                    if "500 Internal Server Error" not in body[:400]:
                        return body
                    await asyncio.sleep(1.0)
                raise AssertionError(
                    f"{job} deep={deep} at {width}px: the console returned 500 four times. "
                    f"Not a width finding -- the surface is not serving.")

            small = await fetch_doc(PHONE)
            wide = await fetch_doc(1440)
            again = await fetch_doc(PHONE)

            # THE NOISE FLOOR IS MEASURED, NOT ASSUMED, and it is measured in the same session
            # rather than pattern-matched. `/fleet` renders eleven agents' heartbeat ages in
            # SECONDS, so two fetches milliseconds apart differ on four lines for reasons that
            # have nothing to do with width. `small` and `again` bracket the middle fetch at the
            # SAME width, so the lines on which they disagree are exactly the lines this surface
            # cannot hold still. The width claim is then made on every OTHER line: a width branch
            # would land somewhere outside that set. Nothing is loosened -- a difference on a
            # line that held still between two 390px fetches is still a finding.
            a, b, c = small.split("\n"), wide.split("\n"), again.split("\n")
            assert len(a) == len(b) == len(c), (
                f"{job} deep={deep}: {len(a)}, {len(b)} and {len(c)} lines. The document changed "
                f"SHAPE between fetches, which no clock does.")
            ticking = {i for i in range(len(a)) if a[i] != c[i]}
            width_only = sorted(i for i in range(len(a)) if a[i] != b[i] and i not in ticking)
            assert not width_only, (
                f"{job} deep={deep}: the 1440px client was served different bytes on line(s) "
                f"{width_only}, which held still between two 390px fetches. One document serves "
                f"both widths; a server that branches on viewport is one template away from a "
                f"mobile-only path to the store.\n      390 : "
                + "\n      390 : ".join(a[i].strip()[:100] for i in width_only[:3])
                + "\n      1440: "
                + "\n      1440: ".join(b[i].strip()[:100] for i in width_only[:3]))
            note = (f", {len(ticking)} line(s) tick with the clock at either width"
                    if ticking else "")
            print(f"      {job:12} deep={deep}: {len(small)} bytes, identical at 390 and "
                  f"1440{note}")
    await _phone(p, PHONE)


async def every_action_the_phone_can_tap_names_a_verb(p: Page):
    """The thinness table, extended to the rendered surface. V8a's contract, measured at 390px.

    V8a's audit walks the AST and proves the console's actions reach their verbs through one
    `store.apply` call site. That is a fact about the code, and `thinness_audit` is its gate --
    not repeated here, because two gates on one property is one gate and one opinion.

    What this adds is the phone's own half. It ASSERTS the one thing that is the phone surface's
    responsibility -- every form a phone can reach posts to the ONE write door, `/<room>/act`,
    with the room a URL segment and never a payload field -- and it PRINTS the extension V8b owes
    V8a's table: which of the table's actions are reachable at 390px, tappable now versus one tap
    behind a fold, and which are not there at all.

    The reachable set is deliberately NOT asserted. Which controls a room renders is a product
    decision that moves; freezing it here would fail the next time the operator moved a button,
    and a check that cries about the wrong thing gets muted. Presence in the DOCUMENT is what
    counts as reachable, not presence on screen: the panels are toggled by `console.js` on the
    client, so markup shipped to the phone is one tap from a thumb.
    """
    await _phone(p)
    found: dict[str, set[str]] = {}
    onscreen: set[str] = set()
    doors: set[str] = set()
    for job, path in JOBS:
        for theme, deep in COMBOS:
            await p.goto(_url(path, theme, deep))
            r = await p.js("""(function(){
                var acts=[], doors=[];
                document.querySelectorAll('form').forEach(function(f){
                  /* The action rides a hidden input on most forms and a submit BUTTON on the
                     ones offering two verbs from one form. Reading only the input undercounts
                     the surface and would report a thinness this file had not measured. */
                  var els=f.querySelectorAll('[name=action]');
                  if(!els.length) return;
                  var vis=f.getClientRects().length>0 && getComputedStyle(f).display!=='none';
                  els.forEach(function(e){ if(e.value){ acts.push([e.value, vis?1:0]); } });
                  doors.push(f.getAttribute('action'));
                });
                return {acts: acts, doors: doors};})()""")
            for a, vis in r["acts"]:
                found.setdefault(a, set()).add(job)
                if vis:
                    onscreen.add(a)
            doors.update(r["doors"])
    assert found, "no action form was found on any of the four phone surfaces"
    bad_doors = sorted(d for d in doors if not re.fullmatch(r"/[a-z]+/act", d or ""))
    assert not bad_doors, (
        f"a form on the phone surface posts to {bad_doors}, which is not the one write door "
        f"`/<room>/act`. The room is a URL segment Flask matched, never a payload field.")
    job_of = {a: j for j, acts in PHONE_JOBS.items() for a in acts}
    print(f"      every form on the four phone surfaces posts to {sorted(doors)} -- one write "
          f"door, room in the path")
    print(f"      {len(found)} action(s) reachable at 390px, {len(onscreen)} of them on screen "
          f"without opening anything:")
    for a in sorted(found):
        where = "on screen" if a in onscreen else "one tap, behind a fold or a panel"
        table = job_of.get(a, "NOT IN THE TABLE")
        print(f"        {a:16} {table:16} {where:34} {', '.join(sorted(found[a]))}")
    unreachable = sorted(set(ACTIONS) - set(found))
    print(f"      of the thinness table's {len(ACTIONS)}, not reachable on these four surfaces: "
          f"{', '.join(unreachable)}")
    # PRINTED, NEVER ASSERTED, and the two cases below are printed for opposite reasons.
    #
    # `post_mine` is in the table and bucketed `desktop`, and the phone renders it anyway. That is
    # a product observation, not a defect: V8a's bucket says what the phone is FOR, not what the
    # template hides.
    #
    # An action absent from the table is a different animal -- it has not been driven through a
    # spy, so nothing has established which verb it names. `web/tools/thinness_audit.py` is the
    # gate for that and it EXITS 1 today over `web/images.py`'s six direct `store.apply` calls
    # (task 0181). Repeating its assertion here would give the property two gates and two
    # opinions; naming it here so the reader of a phone report is not the last to hear it.
    desktop_on_phone = sorted(a for a in found if job_of.get(a) == "desktop")
    if desktop_on_phone:
        print(f"      NOTE: {desktop_on_phone} is bucketed `desktop` in V8a's table and IS "
              f"rendered at 390px. Reachable, not recommended.")
    off_table = sorted(a for a in found if a not in ACTIONS)
    if off_table:
        print(f"      NOTE: {off_table} is reachable on the phone and is NOT in the thinness "
              f"table. `python3 -m web.tools.thinness_audit` is the gate for that; task 0181.")


# ------------------------------------------------------------------------------------------- 6

def the_write_door_survives_the_move() -> None:
    """The move to the tailnet, rehearsed in process. Nothing writes: every POST names a dead id.

    This is the check that decides whether V7b is a config change or a broken console. Under
    shape A (`tailscale serve` terminating TLS) the browser's `Origin` is https and
    `request.host_url` is http, so `check_origin` refuses every write while the surface renders
    perfectly. `web/host.py` closes it with one declared origin; this proves the closure is
    exactly as wide as it claims -- one origin, still scheme-sensitive, still refusing strangers.
    """
    import importlib

    peer = "your-server.tailnet-name.ts.net"

    def run(console_origin: str) -> dict[str, str]:
        os.environ["CONSOLE_ORIGIN"] = console_origin
        importlib.reload(host)
        import web.guard as guard
        importlib.reload(guard)
        import web.app as webapp
        importlib.reload(webapp)
        app = webapp.create_app()
        out = {}
        for label, http_host, origin in (
                ("loopback", "127.0.0.1:3103", "http://127.0.0.1:3103"),
                ("serveA-host-kept", peer, f"https://{peer}"),
                ("serveA-host-rewritten", "127.0.0.1:3103", f"https://{peer}"),
                ("a stranger", "127.0.0.1:3103", "http://evil.example"),
                ("same host, http not https", "127.0.0.1:3103", f"http://{peer}")):
            c = app.test_client()
            g = c.get("/queue", environ_overrides={"HTTP_HOST": http_host})
            tok = re.search(rb'name="csrf" value="([^"]+)"', g.data)
            r = c.post("/queue/act",
                       data={"csrf": tok.group(1).decode() if tok else "",
                             "action": "bump", "id": "T3-NO-SUCH-ITEM"},
                       headers={"Origin": origin},
                       environ_overrides={"HTTP_HOST": http_host})
            body = " ".join(r.get_data(as_text=True).split())
            out[label] = "refused" if "cross-origin" in body else "passed"
        return out

    before = os.environ.get("CONSOLE_ORIGIN")
    try:
        unset = run("")
        assert unset == {"loopback": "passed", "serveA-host-kept": "refused",
                         "serveA-host-rewritten": "refused", "a stranger": "refused",
                         "same host, http not https": "refused"}, \
            f"with CONSOLE_ORIGIN unset the door must behave exactly as it did before: {unset}"
        print(f"      CONSOLE_ORIGIN unset  -> {unset}")
        setv = run(f"https://{peer}")
        assert setv["serveA-host-kept"] == "passed" and \
            setv["serveA-host-rewritten"] == "passed", \
            f"the declared origin did not open the door for either Serve variant: {setv}"
        assert setv["a stranger"] == "refused", \
            f"declaring one origin opened the door to a stranger: {setv}"
        assert setv["same host, http not https"] == "refused", \
            (f"the declared origin is https and an http request to the same host was accepted, "
             f"so the scheme stopped being part of the comparison: {setv}")
        print(f"      CONSOLE_ORIGIN=https://{peer} -> {setv}")
        print("      so the VPS move is ONE value: web/host.py CONSOLE_ORIGIN. The bind does "
              "not move under shape A.")
    finally:
        if before is None:
            os.environ.pop("CONSOLE_ORIGIN", None)
        else:
            os.environ["CONSOLE_ORIGIN"] = before
        importlib.reload(host)


# ------------------------------------------------------------------------------------------------

async def _run():
    async with websockets.connect(_ws_url(), max_size=40 * 1024 * 1024) as ws:
        p = Page(ws)
        await p.send("Page.enable")
        await p.send("Runtime.enable")
        # THE COOKIE JAR CARRIES THE MODE. Deep work is an `io_deep` cookie the SERVER reads,
        # so a previous run that visited `?deep=1` made the next run render in deep work without
        # asking. Measured today: V8a's `test_phone.py` went 4/0, then 2/2 three times with
        # identical code against an identical server, then 4/0 again the moment the shared
        # profile was deleted. Nothing was wrong with the surface. `test_browser._launch` no
        # longer shares a profile between runs -- it makes a fresh temporary one per launch
        # (task 0208) -- so the jar arrives empty. This file never depended on that: every URL
        # it opens spells `theme=` and `deep=`, and it still empties the jar, because the
        # property being asserted is that the URL alone decides the mode.
        await p.send("Network.enable")
        await p.send("Network.clearBrowserCookies")
        for name, fn in [
                ("the four phone jobs hold at 390px, both themes, both modes",
                 the_four_surfaces_hold_at_390),
                ("deep work is absolute on every room a phone polls",
                 deep_work_is_absolute_on_every_room_a_phone_polls),
                ("the phone is served the same document as the desktop",
                 the_phone_is_served_the_same_document_as_the_desktop),
                ("a face-down phone stops asking", the_poll_stops_when_the_phone_is_face_down),
                ("every action the phone can tap names a verb",
                 every_action_the_phone_can_tap_names_a_verb)]:
            try:
                await fn(p)
                PASS.append(name)
                print(f"ok    {name}")
            except AssertionError as e:
                FAIL.append((name, str(e)))
                print(f"FAIL  {name}\n      {e}")


def main() -> int:
    print(f"      {host.describe()}")
    name = "the write door survives the move to the tailnet"
    try:
        the_write_door_survives_the_move()
        PASS.append(name)
        print(f"ok    {name}")
    except AssertionError as e:
        FAIL.append((name, str(e)))
        print(f"FAIL  {name}\n      {e}")
    proc = _launch()
    try:
        asyncio.run(_run())
    finally:
        proc.kill()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
