"""The typable pane, reached and typed into the way a person reaches and types into it.

    BASE=http://127.0.0.1:3107 python3 -m web.tests.test_terminal_human_path

WHY THIS EXISTS BESIDE `test_terminal.py`. That suite forks a real pty and proves the server end
works, and it is green on a machine where no human could get to the feature at all. The standing
finding in this repo is that a green driver run is true about the machine and quiet about the
experience, so everything below is measured through a real browser, with real mouse events and
real key events, and every wait is reported in milliseconds rather than asserted away.

FOUR THINGS ONLY A BROWSER CAN SAY:

  1. The nav door is HELD, and the pane still answers. Since 2026-09-13 the Terminal nav link is
     HELD BY RULING: Andrew's P14 direction F7 removed it (Platform a73398f), and MUST-NOT-BUILD
     item 11's typable-pane phone question is open. So case 1 asserts the nav carries NO link
     named Terminal and none pointing at /terminal under another name. It then reaches the pane
     by URL and checks it lands there, served rather than refused. When the hold is lifted,
     this case goes back to clicking, in the same change that restores the link and deletes
     `/terminal` from `HELD_BY_RULING` in `test_every_route_is_reachable.py`.
  2. A keystroke ECHOES, and how long it took. `terminal.js` sends every key over
     `POST /terminal/<key>/input` and paints the pty's own echo, so the number below is the whole
     loop -- browser to Flask to pty to screen model to JSON to DOM -- and not a server timing.
  3. A COMMAND runs and its output arrives, timed from the Enter keystroke.
  4. THE PANE SURVIVES A RELOAD, or it does not. A terminal the operator loses by pressing F5 is
     not a terminal he will leave a long Claude Code run in, and no server-side test can see this
     because the pty is perfectly alive on the other side of it.

THE TRANSPORT IS NOT A WEBSOCKET AND THIS SUITE IS WHERE THAT IS PAID FOR. MUST-NOT-BUILD item 11
permits one for this pane; the pane does not take it. Whether that was the right call is a latency
question, so the latency is measured here in milliseconds rather than argued.

NO STORE WRITE HAPPENS ANYWHERE IN THIS FILE. It opens a `shell` pane and types `echo`. It is safe
against a console on the live store for exactly that reason, and it does not reseed anything.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import random
import re
import shutil
import signal
import statistics
import string
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets

_R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _R not in sys.path:
    sys.path.insert(0, _R)

CHROME = os.environ.get(
    "CHROME", os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux/chrome"))
BASE = os.environ.get("BASE", "http://127.0.0.1:3107")
#: A port of this suite's own. `test_browser.py` holds 9223 and two chromes on one port is the
#: stale-browser defect that file documents at length.
PORT = int(os.environ.get("CDP_PORT", "9227"))

PASS: list = []
FAIL: list = []
NOTES: list = []


def ck(name, cond, got=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"   [{got}]" if got else ""))


def note(line):
    NOTES.append(line)
    print("  ..   " + line)


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

    async def url(self):
        return await self.js("location.pathname")

    # ---------------------------------------------------------------- real input, not .click()

    async def click_text(self, selector, text):
        """Click the element matching `selector` whose visible text contains `text`.

        A real mouse press and release at the element's own centre, because `HTMLElement.click()`
        fires a synthetic event that skips hit-testing entirely: it would happily click a control
        another element is sitting on top of, which is precisely the class of defect a human-path
        audit is for.
        """
        box = await self.js(f"""
          (() => {{
            const els = Array.from(document.querySelectorAll({json.dumps(selector)}));
            const el = els.find(e => (e.innerText || e.textContent || '').includes({json.dumps(text)}));
            if (!el) return null;
            el.scrollIntoView({{block: 'center'}});
            const r = el.getBoundingClientRect();
            return {{x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height}};
          }})()
        """)
        if not box or box["w"] <= 0 or box["h"] <= 0:
            raise RuntimeError(f"no visible {selector} containing {text!r}")
        for kind in ("mousePressed", "mouseReleased"):
            await self.send("Input.dispatchMouseEvent", type=kind, x=box["x"], y=box["y"],
                            button="left", clickCount=1)
        return box

    async def key(self, ch):
        """One real key, the way a keyboard sends it: keyDown, char, keyUp."""
        if ch == "\r":
            await self.send("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter",
                            windowsVirtualKeyCode=13, nativeVirtualKeyCode=13, text="\r")
            await self.send("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter",
                            windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
            return
        vk = ord(ch.upper()) if ch.isalnum() else 0
        await self.send("Input.dispatchKeyEvent", type="keyDown", key=ch, text=ch,
                        unmodifiedText=ch, windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk)
        await self.send("Input.dispatchKeyEvent", type="keyUp", key=ch,
                        windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk)

    async def wait_armed(self, seconds=15.0):
        """Wait until `terminal.js` has bound its handlers and enabled the openers.

        A click before that lands on a button whose `onclick` is still null and does nothing at
        all, silently. That is a real defect and it is fixed in the product (the template ships
        the openers `disabled`), so this waits on the ARMED state rather than on a sleep: a wait
        that is a sleep is a wait that passes on a slow machine by luck.
        """
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            armed = await self.js(
                "!!document.getElementById('tnew') && !document.getElementById('tnew').disabled "
                "&& typeof document.getElementById('thelp').onclick === 'function'")
            if armed:
                return (time.monotonic() - t0) * 1000.0
            await asyncio.sleep(0.02)
        return None

    async def screen_text(self):
        return await self.js(
            "(document.getElementById('thist') ? document.getElementById('thist').innerText : '')"
            " + '\\n' + document.getElementById('tscreen').innerText")

    async def wait_for(self, needle, seconds=20.0):
        """Poll the rendered screen until `needle` appears. Returns the wait in MILLISECONDS."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            if needle in (await self.screen_text() or ""):
                return (time.monotonic() - t0) * 1000.0
            await asyncio.sleep(0.02)
        return None


# --------------------------------------------------------------------------- the browser

def _launch():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1) as r:
            json.load(r)
        raise RuntimeError(
            f"something is already listening on CDP port {PORT}: a chrome from an earlier run, "
            f"with its own cookie jar. Kill it before trusting anything measured here.")
    except RuntimeError:
        raise
    except Exception:                                                   # noqa: BLE001
        pass
    profile = tempfile.mkdtemp(prefix="terminal-cdp-")
    proc = subprocess.Popen(
        [CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
         f"--remote-debugging-port={PORT}", "--window-size=1512,982",
         f"--user-data-dir={profile}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    pgid = os.getpgid(proc.pid)

    def _cleanup(pgid=pgid, path=profile):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except Exception:                                               # noqa: BLE001
            pass
        for _ in range(15):
            time.sleep(0.2)
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.isdir(path):
                return
        print(f"WARNING: could not remove {path}; a chrome in group {pgid} is still writing.",
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


# --------------------------------------------------------------------------- the checks

async def nav_door_held_pane_by_url(p: Page):
    """Case 1. The Terminal nav door is HELD BY RULING, so it is absent; the pane answers by URL.

    Absent by LABEL and by HREF, because a held door renamed is still the door. The pane checks
    below are unchanged: only the way in moved from a click to the URL.
    """
    print("\ncase 1 · the nav holds no Terminal door (held by ruling); the pane answers by URL")
    await p.goto("/queue")
    # `nav.subrooms` too since the five-tab nav: a held door must not slip into the row of rooms.
    labels = await p.js(
        "Array.from(document.querySelectorAll('nav.rooms a, nav.subrooms a')).map(a => a.innerText.trim())")
    ck("the front page carries a rooms nav", bool(labels), got=str(labels))
    ck("none of its links is named Terminal: the nav door is held by ruling (F7, a73398f)",
       "Terminal" not in (labels or []), got=f"nav reads {labels}")
    hrefs = await p.js(
        "Array.from(document.querySelectorAll('nav.rooms a, nav.subrooms a')).map(a => a.getAttribute('href'))")
    ck("and none of them points at /terminal under another name",
       not any((h or "").startswith("/terminal") for h in (hrefs or [])),
       got=f"nav hrefs {hrefs}")
    t0 = time.monotonic()
    await p.goto("/terminal")
    ms = (time.monotonic() - t0) * 1000.0
    ck("the pane still answers by URL at /terminal", await p.url() == "/terminal",
       got=await p.url())
    note(f"URL to the loaded pane (the nav door is held): {ms:.0f} ms")
    refused = await p.js("!!document.querySelector('.finding.wait')")
    ck("the pane is served here, not refused", not refused)
    ck("the screen element is on the page and focusable",
       await p.js("!!document.getElementById('tscreen') && "
                  "document.getElementById('tscreen').tabIndex >= 0"))
    # THE OPENERS ARE INERT UNTIL THEY ARE ARMED, AND THEY LOOK IT. Before 2026-08-29 they looked
    # live and were not: a click in the window before `terminal.js` bound its handlers produced no
    # request, no message and no visible change, and it cost the logging-proof run its session.
    ck("the openers ship disabled, so an early click cannot be silently swallowed",
       await p.js("document.getElementById('tnew').disabled === true || "
                  "typeof document.getElementById('tnew').onclick === 'function'"))
    armed = await p.wait_armed()
    ck("and the script arms them", armed is not None)
    if armed is not None:
        note(f"the openers became live {armed:.0f} ms after the page finished loading")


async def opens_a_shell(p: Page):
    """Case 2. One click opens a real process and its prompt arrives."""
    print("\ncase 2 · one click opens a shell")
    t0 = time.monotonic()
    await p.click_text("button.tbtn", "New shell")
    got = await p.wait_for("$", 20.0)
    ms = (time.monotonic() - t0) * 1000.0
    ck("a prompt appears in the pane", got is not None,
       got="" if got is not None else "no prompt within 20s")
    note(f"click to a live prompt: {ms:.0f} ms")
    ck("the header names the running process",
       "pid" in (await p.js("document.getElementById('tstatus').innerText") or ""),
       got=await p.js("document.getElementById('tstatus').innerText"))
    # THE PANE MUST OPEN BIG ENOUGH TO HOST THE PROGRAM IT WAS BUILT FOR, and until 2026-08-29 it
    # did not. `.tscroll` was `max-height: 72vh` with no height, so an EMPTY pane was as tall as
    # its placeholder and `fitSize()` asked the pty for the floor of its own calculation: SIX
    # ROWS, on a 1512x982 window. Measured with a real Claude Code helper in the pane: `136x6 -
    # alt screen`. Claude Code's chrome is four of those six rows. bash hid it completely, which
    # is why this assertion is on the GEOMETRY and not on whether a shell worked.
    size = await p.js("document.getElementById('tsize').innerText")
    rows = int((size or "0x0").split("\u00d7")[1].split()[0]) if "\u00d7" in (size or "") else 0
    ck(f"the pane opens tall enough to run a TUI in: {size}", rows >= 20,
       got=f"{rows} rows. Claude Code's own chrome is four of them.")
    note(f"the pane opened at {size} on a {await p.js('window.innerWidth')}"
         f"x{await p.js('window.innerHeight')} window")


async def a_keystroke_echoes(p: Page):
    """Case 3. THE NUMBER THAT DECIDES THE TRANSPORT: one key, seen back, in milliseconds.

    Ten keys, each timed on its own, and the median and the worst are both printed. A mean would
    hide the one slow key, and the one slow key is what a terminal feels like.
    """
    print("\ncase 3 · a keystroke, and how long it took to come back")
    await p.js("document.getElementById('tscreen').focus()")
    ck("the screen has focus", await p.js("document.activeElement.id") == "tscreen",
       got=await p.js("document.activeElement.id"))
    word = "abcdefghij"
    per_key = []
    for i, ch in enumerate(word):
        before = await p.screen_text()
        t0 = time.monotonic()
        await p.key(ch)
        # The needle is the whole prefix typed so far, so an echo of an EARLIER key cannot be
        # mistaken for this one's.
        needle = word[:i + 1]
        ok = False
        while time.monotonic() - t0 < 10.0:
            if needle in (await p.screen_text() or ""):
                ok = True
                break
            await asyncio.sleep(0.004)
        per_key.append((time.monotonic() - t0) * 1000.0 if ok else None)
        if not ok:
            break
        del before
    good = [x for x in per_key if x is not None]
    ck(f"every keystroke echoed: {len(good)} of {len(word)}", len(good) == len(word))
    if good:
        note(f"keystroke echo, {len(good)} samples: median {statistics.median(good):.0f} ms, "
             f"best {min(good):.0f} ms, worst {max(good):.0f} ms")
    return good


async def _fresh_prompt(p: Page, dirty):
    """Ctrl-C the half-typed line and WAIT FOR THE PROOF that it went, never for a fixed sleep.

    The first version of this slept 300 ms after a Ctrl-U and case 4 then failed once in three
    runs, because the leftovers were still on the line and the command it typed became
    `abcdefghijecho hello-...`, which bash refuses without ever echoing the argument. A wait for a
    duration is a wait that is right on the machine it was written on.
    """
    for _ in range(3):
        await p.send("Input.dispatchKeyEvent", type="keyDown", key="c", code="KeyC",
                     modifiers=2, windowsVirtualKeyCode=67, nativeVirtualKeyCode=67)
        await p.send("Input.dispatchKeyEvent", type="keyUp", key="c", code="KeyC",
                     modifiers=2, windowsVirtualKeyCode=67, nativeVirtualKeyCode=67)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 4.0:
            tail = ((await p.screen_text()) or "").rstrip().splitlines()
            if tail and dirty not in tail[-1]:
                return True
            await asyncio.sleep(0.05)
    return False


async def a_command_answers(p: Page):
    """Case 4. A real command, timed from the Enter key to the output on screen."""
    print("\ncase 4 · a command, timed from Enter to its answer")
    ck("the half-typed line was cleared before the command was typed",
       await _fresh_prompt(p, "abcdefghij"))
    stamp = str(int(time.time()))
    for ch in "echo hello-" + stamp:
        await p.key(ch)
    await asyncio.sleep(0.25)
    t0 = time.monotonic()
    await p.key("\r")
    ok = False
    while time.monotonic() - t0 < 15.0:
        text = await p.screen_text() or ""
        # Two occurrences: the echoed command line, and the output line under it.
        if text.count("hello-" + stamp) >= 2:
            ok = True
            break
        await asyncio.sleep(0.004)
    ms = (time.monotonic() - t0) * 1000.0
    ck("the command ran and its output came back", ok,
       got="" if ok else "output never appeared")
    if ok:
        note(f"Enter to the answer on screen: {ms:.0f} ms")
    return ms if ok else None


async def survives_a_reload(p: Page):
    """Case 5. F5. The pty is alive on the server; is it still reachable from the page?"""
    print("\ncase 5 · the operator presses reload")
    before = await p.js("window.__tkey || null")
    server_says = await p.js("""
      fetch('/terminal', {credentials:'same-origin'}).then(r => r.text())
        .then(t => (t.match(/tscreen/) ? 'served' : 'refused'))
    """)
    ck("the page is still served after the pane opened", server_says == "served", got=server_says)
    pid_before = await p.js("document.getElementById('tstatus').innerText")
    await p.goto("/terminal")
    await asyncio.sleep(1.0)
    tabs = await p.js("document.getElementById('ttabs').children.length")
    placeholder = await p.js("!!document.getElementById('tplaceholder')")
    ck("after a reload the running pane is still offered", tabs and tabs > 0,
       got=f"{tabs} tab(s) rendered, placeholder={'yes' if placeholder else 'no'}")
    pid_after = await p.js("document.getElementById('tstatus').innerText")
    ck("and it is the SAME process, not a new one", pid_after == pid_before,
       got=f"{pid_before!r} became {pid_after!r}")
    # A tab that paints is not a tab that types. The claim is that the operator can carry on.
    await p.js("document.getElementById('tscreen').focus()")
    stamp = str(int(time.time()))[-5:]
    for ch in "echo back-" + stamp:
        await p.key(ch)
    t0 = time.monotonic()
    await p.key("\r")
    ok = False
    while time.monotonic() - t0 < 15.0:
        if (await p.screen_text() or "").count("back-" + stamp) >= 2:
            ok = True
            break
        await asyncio.sleep(0.004)
    ck("and it still takes keystrokes after the reload", ok)
    if ok:
        note(f"after a reload, Enter to the answer: {(time.monotonic() - t0) * 1000.0:.0f} ms")
    del before
    return {"tabs": tabs, "placeholder": placeholder}


async def typing_fast_arrives_in_order(p: Page):
    """Case 7. THE DEFECT NO SERVER-SIDE TEST COULD SEE: does what you type arrive IN ORDER?

    FOUND IN PRODUCTION, NOT IN A FIXTURE. On 2026-08-29 a real Claude Code helper was opened from
    this pane and typed into from this browser, and `brain.session.stated_goal` recorded what the
    process actually received. Typed:
        "Do not read, write or edit any file, and run no command."
    Received, and stored:
        "ot read, write or edit any file,a nd run no command."
    Four characters lost at the head and two adjacent characters SWAPPED in the middle. Every
    individual `POST /terminal/<key>/input` was correct and the server handled each faithfully.
    `fetch` simply makes no ordering promise across concurrent requests, and the browser runs
    several connections to one origin.

    HOW THIS REPRODUCES IT, AND WHY THE EVENTS ARE SYNTHETIC HERE. The keystrokes are dispatched
    as `KeyboardEvent`s from inside the page, in ONE TICK, rather than through CDP one at a time.
    That is not a shortcut, it is the condition: CDP round-trips each real key, which spaces them
    out enough that the race only shows against a slow responder like a full TUI repainting. A
    burst in one tick is what a fast typist, a held key, or a paste-like run of input looks like
    from the client's side, and it is the maximum-concurrency case the old code allowed. Case 3
    above uses REAL keys for the latency numbers; this one uses a burst for the ordering claim.

    THE SHELL IS THE WITNESS. `read -r` puts exactly the bytes the pty received into a variable
    and `echo` prints them back, so the comparison is byte-for-byte about DELIVERY, not about
    rendering.
    """
    print("\ncase 7 · 200 characters dispatched in a single tick")
    await _fresh_prompt(p, "zzzz-never-present")
    word = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(200))
    for ch in "read -r L; echo QQ${L}ZZ":
        await p.key(ch)
    await p.key("\r")
    await asyncio.sleep(1.0)
    await p.js("document.getElementById('tscreen').focus()")
    t0 = time.monotonic()
    await p.js(f"""
      (() => {{
        const scr = document.getElementById('tscreen');
        const w = {json.dumps(word)};
        for (const ch of w) {{
          scr.dispatchEvent(new KeyboardEvent('keydown',
            {{key: ch, bubbles: true, cancelable: true}}));
        }}
        scr.dispatchEvent(new KeyboardEvent('keydown',
          {{key: 'Enter', bubbles: true, cancelable: true}}));
        return true;
      }})()
    """)
    # THE WITNESS LINE WRAPS AND MUST BE REASSEMBLED BEFORE IT IS COMPARED. The first version of
    # this read one DOM row and reported 134 of 200 delivered on code that had delivered all 200:
    # the screen was 136 columns wide and the echo simply continued on the next row. A test that
    # measures the terminal's width and calls it data loss is a test that would have sent a lane
    # hunting a defect that was not there.
    got = None
    while time.monotonic() - t0 < 25.0:
        flat = "".join(l.rstrip() for l in ((await p.screen_text()) or "").splitlines())
        m = re.search(r"QQ([a-z0-9]*)ZZ", flat)
        if m:
            got = m.group(1)
            break
        await asyncio.sleep(0.02)
    ms = (time.monotonic() - t0) * 1000.0
    ck("the shell echoed the line back", got is not None,
       got="" if got is not None else "no QQ...ZZ line within 25s")
    if got is None:
        return
    same = got == word
    ck(f"all {len(word)} characters arrived, in order, unaltered", same,
       got=(f"{len(got)} of {len(word)} characters, first difference at index "
            f"{next((i for i, (x, y) in enumerate(zip(word, got)) if x != y), min(len(word), len(got)))}"))
    note(f"200 keys dispatched in one tick: round trip {ms:.0f} ms, "
         f"{len(got)} of {len(word)} characters delivered, "
         f"{'in order' if same else 'OUT OF ORDER'}")


async def what_a_socket_could_save(p: Page):
    """Case 6. THE NUMBER THE TRANSPORT DECISION RESTS ON, and it is a bound rather than an opinion.

    The pane polls and POSTs over plain `fetch`. MUST-NOT-BUILD item 11's overrule permits a
    websocket instead, so the honest question is not "is fetch fast" but "how much of the measured
    keystroke latency is the HTTP transport a socket would replace". An empty POST to the same
    input endpoint is that transport with the pty taken out of it: same door, same guard, same
    JSON, no keystroke. Whatever a websocket saved would have to come out of THIS number, and it
    could not take all of it, because a socket still has to serialise and paint.
    """
    print("\ncase 6 · what a websocket could have saved")
    samples = await p.js("""
      (async () => {
        const key = window.__tkey;
        const out = [];
        for (let i = 0; i < 20; i++) {
          const body = new URLSearchParams();
          body.set('csrf', window.__TERMINAL_CSRF);
          body.set('data', '');
          const t0 = performance.now();
          await fetch('/terminal/' + key + '/input', {
            method: 'POST', credentials: 'same-origin',
            headers: {'Content-Type': 'application/x-www-form-urlencoded',
                      'X-CSRF': window.__TERMINAL_CSRF},
            body: body.toString()}).then(r => r.json());
          out.push(performance.now() - t0);
        }
        return out;
      })()
    """)
    ck("the empty round trip was measurable", bool(samples), got=str(samples)[:60])
    if samples:
        note(f"HTTP round trip with no keystroke in it, {len(samples)} samples: "
             f"median {statistics.median(samples):.1f} ms, worst {max(samples):.1f} ms")
    return samples


async def _run():
    proc = _launch()
    try:
        async with websockets.connect(_ws_url(), max_size=64 * 1024 * 1024) as ws:
            p = Page(ws)
            await p.send("Page.enable")
            await p.send("Runtime.enable")
            await nav_door_held_pane_by_url(p)
            if FAIL:
                return
            await opens_a_shell(p)
            await a_keystroke_echoes(p)
            await a_command_answers(p)
            await survives_a_reload(p)
            await typing_fast_arrives_in_order(p)
            await what_a_socket_could_save(p)
            # CLOSE WHAT THIS RUN FORKED. The cap is process wide (`terminal.MAX_SESSIONS`), so a
            # suite that leaves its pane running is a suite that makes the sixth run of the day
            # fail on a refusal it caused itself.
            try:
                await p.click_text("button.tbtn", "Close pane")
                await asyncio.sleep(0.5)
            except Exception:                                           # noqa: BLE001
                pass
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:                                               # noqa: BLE001
            pass


def main() -> int:
    print(f"test_terminal_human_path.py  --  the pane reached and typed into, against {BASE}")
    asyncio.run(_run())
    total = len(PASS) + len(FAIL)
    if total == 0:                                                      # DENOMINATOR
        print("0 comparisons made. The browser never reached the pane, so nothing here is a "
              "verdict about it. A verdict over an empty set is not a pass.")
        return 2
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed, of {total} compared")
    for line in NOTES:
        print(f"  timing: {line}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
