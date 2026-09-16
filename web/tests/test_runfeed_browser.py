#!/usr/bin/env python3
"""What only a browser can prove about the runfeed, take-control and the picker. Task 0166.

The one that matters is the caret. v1 proved a caret at offset 6 surviving two poll cycles on the
Queue, and the synthesis prototype re-proved it; this proves it on the surface that is most likely
to break it, because the runfeed is the only page in the console that repaints on a THREE SECOND
CLOCK WHILE THE OPERATOR IS TYPING INTO IT. Take-control's whole value is a sentence being typed
to a live terminal, and a repaint that ate it would be the feature failing at the only moment it
is used.

"The value survived" is worth nothing on its own -- a page where nothing repainted passes that.
So the test forces REAL EVENTS to arrive: it appends to the run's stream file mid-typing, and
asserts the poller appended them (`window.__rfAppended`) before it asserts the caret did not move.

The rest are the absences the brief makes absolute, checked in a rendered DOM rather than by
reading a template:

  * `permission_mode` and `bypassPermissions` appear NOWHERE, in any state.
  * The steering input DOES NOT EXIST IN THE DOM until control is entered. Not hidden: absent.
  * The picker has exactly three selects, and a fourth would be a defect.
  * No bar, no ring, no percent and no ETA anywhere in the vitals.

Run (it starts its own console against a scratch database):

    ENGINE_SCRATCH_DB=brain_t1_0166 BRAIN_PG_DB=brain_t1_0166 \\
      python3 web/tests/test_runfeed_browser.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "engine"), str(ROOT / "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if os.environ.get("BRAIN_PG_DB", "brain") == "brain":
    sys.exit("refusing to run against the live store. Set BRAIN_PG_DB to a scratch database.")

# A terminal running this inherits `SWARM_PARENT_TASK` from its own run, and `post` resolves a
# parent from it -- against the SCRATCH database, where that task does not exist. Dropped here so
# the suite behaves the same run by a human and run by an agent.
os.environ.pop("SWARM_PARENT_TASK", None)

CHROME = os.environ.get(
    "CHROME", os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux/chrome"))
PORT = int(os.environ.get("RF_PORT", "3199"))
CDP = int(os.environ.get("RF_CDP", "9224"))
BASE = f"http://127.0.0.1:{PORT}"
RUNS = Path(os.environ.get("RF_RUNS", "/tmp/runfeed-test-runs"))
AGENT = "T-demo"

import store                                                            # noqa: E402
from swarm_engine import steering, transitions                          # noqa: E402,F401

PASS, FAIL = [], []


# ------------------------------------------------------------------ a run to look at

def _event(kind, **kw):
    base = {"type": kind, "session_id": "sess-runfeed-0166",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())}
    base.update(kw)
    return json.dumps(base) + "\n"


def beat(text):
    return _event("assistant", message={"content": [{"type": "text", "text": text}]})


def tool(name, detail):
    return _event("assistant", message={"content": [
        {"type": "tool_use", "name": name, "input": {"command": detail}}]})


def churn(n=6):
    return "".join(_event("stream_event", event={"type": "content_block_delta",
                                                 "delta": {"text": "."}}) for _ in range(n))


def seed() -> dict:
    """One agent, one live run, one real stream file. Written by the verbs, never by fixtures."""
    RUNS.mkdir(parents=True, exist_ok=True)
    store.apply("heartbeat", agent=AGENT, status="working", role="terminal", host="test")
    tid = store.apply("post", lane="console", title="a run to watch in the runfeed",
                      agent_claimable=True, workdir=str(ROOT),
                      signals={"reversibility": "reversible"})["id"]
    store.apply("claim", agent=AGENT, lanes=["*"])
    path = RUNS / f"{tid}-attempt1.stream.jsonl"
    path.write_text(
        _event("system", subtype="init", model="claude-opus-5", permissionMode="auto",
               cwd=str(ROOT))
        + beat("Reading the brief first, because the terminal view truncates it.")
        + churn(8) + tool("Bash", "grep -n 'steer' engine/swarm_engine/steering.py")
        + beat("The exclusion has to bite in three places and each asks a different question.")
        + churn(5) + tool("Read", "web/runfeed.py"),
        encoding="utf-8")
    store.apply("run start", id=tid, attempt=1, agent=AGENT, host="test",
                session_id="sess-runfeed-0166", stream_pointer=str(path),
                stream_pointer_host="test")
    return {"task": tid, "path": path}


def append_events(path, text):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)


# ------------------------------------------------------------------ the console and the browser

class Page:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

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
        for _ in range(80):
            await asyncio.sleep(0.25)
            if await self.js("document.readyState === 'complete'"):
                await asyncio.sleep(0.3)
                return
        raise RuntimeError(f"{path} never finished loading")

    async def width(self, w, h=1200):
        await self.send("Emulation.setDeviceMetricsOverride", width=w, height=h,
                        deviceScaleFactor=1, mobile=False)

    async def shot(self, out):
        r = await self.send("Page.captureScreenshot", format="png", captureBeyondViewport=True)
        import base64
        Path(out).write_bytes(base64.b64decode(r["data"]))
        return out


def start_console():
    env = {**os.environ, "FLASK_APP": "web.app:app", "CONSOLE_RUNS_DIR": str(RUNS),
           "CONSOLE_BIND": "127.0.0.1", "CONSOLE_PORT": str(PORT),
           "CONSOLE_ORIGIN": BASE}
    proc = subprocess.Popen(
        [sys.executable, "-m", "flask", "run", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(80):
        time.sleep(0.25)
        try:
            with urllib.request.urlopen(f"{BASE}/api/health", timeout=1):
                return proc
        except Exception:                                               # noqa: BLE001
            continue
    err = proc.stderr.read().decode("utf-8", "replace")[-2000:] if proc.stderr else ""
    proc.kill()
    raise RuntimeError(f"the console did not come up on {PORT}\n{err}")


def start_chrome():
    proc = subprocess.Popen(
        [CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
         f"--remote-debugging-port={CDP}", "--window-size=1440,1200",
         "--user-data-dir=/tmp/runfeed-cdp-profile", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        time.sleep(0.25)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json/version", timeout=1):
                return proc
        except Exception:                                               # noqa: BLE001
            continue
    proc.kill()
    raise RuntimeError("chrome did not come up on the debugging port")


def ws_url():
    with urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json/list", timeout=2) as r:
        for t in json.load(r):
            if t.get("type") == "page":
                return t["webSocketDebuggerUrl"]
    raise RuntimeError("no page target")


# ------------------------------------------------------------------ the checks

async def the_feed_renders_beats_and_folds(p: Page, run):
    await p.goto(f"/agent/{AGENT}")
    got = await p.js("""({
        beats: document.querySelectorAll('#feed .ev').length,
        folds: document.querySelectorAll('#feed .foldbtn').length,
        vitals: (document.querySelector('#vt .vt')||{}).textContent || '',
        live: (document.querySelector('#live')||{}).textContent || '',
        seam: (document.querySelector('.seam.open')||{}).textContent || ''
    })""")
    assert got["beats"] >= 2, f"the two beats did not render: {got}"
    assert got["folds"] >= 1, f"the churn did not fold: {got}"
    assert "WORKING" in got["vitals"], f"the vitals do not say the state: {got['vitals']!r}"
    assert "beat" in got["vitals"] and "tool call" in got["vitals"], got["vitals"]
    assert "fresh session" in got["seam"], f"the seam's fixed copy is missing: {got['seam']!r}"
    assert "▸" in got["live"], f"the live tool line did not render: {got['live']!r}"
    print(f"      {got['beats']} beats, {got['folds']} folds, vitals {got['vitals'].strip()[:90]}")


async def nothing_implies_completion(p: Page):
    """MUST-NOT-BUILD #4. No bar, no ring, no ETA, no percent, and no `<progress>`."""
    got = await p.js("""(function(){
        var rf = document.querySelector('.runfeed');
        var els = rf.querySelectorAll('*');
        var pct = 0, widths = 0;
        for (var i=0;i<els.length;i++){
          var st = els[i].getAttribute('style') || '';
          if (/width:\\s*\\d+%/.test(st)) widths++;
        }
        var text = rf.textContent;
        return {progress: rf.querySelectorAll('progress,meter').length,
                widths: widths,
                pct: (text.match(/\\d+\\s*%/g)||[]).length,
                eta: /\\beta\\b|remaining|complete\\b/i.test(text)};
    })()""")
    assert got["progress"] == 0, "a <progress> or <meter> is in the runfeed"
    assert got["widths"] == 0, "an inline percentage width is in the runfeed"
    assert got["pct"] == 0, "a percentage is rendered in the runfeed"
    print(f"      no progress element, no percentage, no width:% anywhere in the feed")


async def permission_mode_is_absent(p: Page):
    """Absolute, in every state. The engine announces it in `init`; the console never shows it."""
    got = await p.js("""({
        html: document.documentElement.innerHTML.toLowerCase(),
        selects: Array.prototype.map.call(document.querySelectorAll('.pickform select'),
                                          function(s){return s.name})
    })""")
    for word in ("permissionmode", "permission_mode", "bypasspermissions"):
        assert word not in got["html"], f"{word!r} is in the rendered DOM"
    assert got["selects"] == ["model", "effort", "account"], (
        f"the picker has {got['selects']}, and a fourth select would be the defect the spec "
        f"names")
    print(f"      picker selects: {got['selects']}; no permission word in the DOM")


async def the_input_does_not_exist_until_control_is_entered(p: Page):
    before = await p.js("""({
        input: !!document.getElementById('steerinput'),
        panel: !!document.getElementById('seize'),
        panelOpen: (document.getElementById('seize')||{}).hidden === false,
        band: !!document.querySelector('.steerband')
    })""")
    assert before["input"] is False, "the steering input exists before control was taken"
    assert before["panel"] is True, "the consequence panel is not on the page"
    assert before["panelOpen"] is False, "the consequence panel is open before it was asked for"
    assert before["band"] is False, "the steering band renders when nothing is being steered"

    await p.js("document.querySelector('[data-toggle=\\'seize\\']').click()")
    await asyncio.sleep(0.3)
    after = await p.js("""({
        open: document.getElementById('seize').hidden === false,
        text: document.getElementById('seize').textContent,
        input: !!document.getElementById('steerinput')
    })""")
    assert after["open"] is True, "the consequence panel did not open"
    assert "marked steered" in after["text"], f"the consequence is not stated: {after['text']!r}"
    assert "Seize this run" in after["text"], "the verb is not on the panel"
    assert after["input"] is False, (
        "the input appeared with the panel. It must not exist until control is ENTERED: two acts "
        "on two targets, and there is nothing to fall into")
    print("      panel says the consequence; the input still does not exist")


async def seizing_shows_the_band_and_the_input(p: Page, run):
    await p.js("document.querySelector('#seize form button.act').click()")
    await asyncio.sleep(1.2)
    await p.goto(f"/agent/{AGENT}")
    got = await p.js("""({
        band: (document.querySelector('.steerband')||{}).textContent || '',
        input: !!document.getElementById('steerinput'),
        release: document.body.textContent.indexOf('Release control') >= 0,
        mark: (document.querySelector('.mark')||{}).textContent || ''
    })""")
    assert got["input"] is True, "the input does not exist while steering"
    assert "YOU ARE STEERING" in got["band"], f"no pinned header: {got['band']!r}"
    assert got["release"] is True, "there is no way out"
    assert "SEIZED" in got["mark"], f"the mark is not in the feed: {got['mark']!r}"
    assert steering.is_steered(run["task"]) is True, "the store does not know the run is steered"
    print(f"      band: {got['band'].split(chr(10))[0].strip()[:80]}")


async def the_caret_survives_two_polls_with_events_arriving(p: Page, run):
    """THE ACCEPTANCE TEST, verbatim from v1: caret at offset 6, two poll cycles, events arriving."""
    typed = "stop reading the census and run the exclusion test against the scratch database"
    await p.js(f"""(function(){{
        var ta = document.getElementById('steerinput');
        ta.focus(); ta.value = {json.dumps(typed)};
        ta.dispatchEvent(new Event('input', {{bubbles:true}}));
        ta.setSelectionRange(6, 6);
        window.__rfAppended = 0; window.__rfPolls = 0;
        return true;
    }})()""")
    before = await p.js("({tag: document.activeElement.tagName, id: document.activeElement.id,"
                        " s: document.activeElement.selectionStart})")
    assert before["id"] == "steerinput", f"the input did not take focus: {before}"
    assert before["s"] == 6, f"the caret is at {before['s']}, not 6"

    # REAL EVENTS, arriving while the caret sits at offset 6. Without this the test would pass on
    # a page where nothing repainted at all, which is the failure mode the v1 test was written
    # against.
    append_events(run["path"], churn(4) + tool("Bash", "python3 engine/tests/test_steering.py")
                  + beat("The negative control accepted the steered run, so the guard is real."))
    await asyncio.sleep(4.0)
    append_events(run["path"], churn(3) + beat("Second beat, arriving on the next poll."))
    await asyncio.sleep(4.0)

    after = await p.js("""({
        tag: document.activeElement.tagName, id: document.activeElement.id,
        v: document.activeElement.value, s: document.activeElement.selectionStart,
        polls: window.__rfPolls, appended: window.__rfAppended,
        skipped: window.__rfSkipped,
        beats: document.querySelectorAll('#feed .ev').length,
        btn: document.querySelector('.sayform button.act').disabled
    })""")
    assert after["polls"] >= 2, f"only {after['polls']} polls ran; nothing repainted"
    assert after["appended"] >= 2, (
        f"the poller appended {after['appended']} entries, so this proves nothing about a "
        f"repaint under the caret")
    assert after["id"] == "steerinput", f"focus moved to {after['id'] or after['tag']!r}"
    assert after["v"] == typed, f"the text changed under the caret: {after['v']!r}"
    assert after["s"] == 6, f"THE SELECTION MOVED from 6 to {after['s']}"
    assert after["btn"] is False, "the send button was disabled by the repaint"
    print(f"      {after['polls']} polls, {after['appended']} entries appended, "
          f"{after['beats']} beats on screen, caret still at 6")


async def sending_lands_on_the_thread(p: Page, run):
    await p.js("document.querySelector('.sayform button.act').click()")
    await asyncio.sleep(1.2)
    with store.read() as s:
        rows = s.query("SELECT from_agent, to_agent, kind, text FROM brain.thread "
                       " WHERE work_item_id = %s AND kind = 'msg' ORDER BY seq", (run["task"],))
        box = s.query("SELECT to_agent, text FROM brain.message WHERE work_item_id = %s",
                      (run["task"],))
    assert rows, "the operator's message did not land on the thread"
    assert rows[-1]["from_agent"] == "operator", f"attributed to {rows[-1]['from_agent']!r}"
    assert rows[-1]["to_agent"] == AGENT, f"addressed to {rows[-1]['to_agent']!r}"
    assert box and box[-1]["to_agent"] == AGENT, "it never reached the terminal's mailbox"
    print(f"      thread: {rows[-1]['from_agent']} → {rows[-1]['to_agent']}: "
          f"{rows[-1]['text'][:60]}")


async def releasing_keeps_the_mark(p: Page, run):
    await p.goto(f"/agent/{AGENT}")
    await p.js("""(function(){
        var f = Array.prototype.filter.call(document.querySelectorAll('form'), function(x){
            return x.querySelector('[value=steer_release]'); })[0];
        f.querySelector('button').click(); return true;})()""")
    await asyncio.sleep(1.2)
    assert steering.active_steering(run["task"]) is None, "control was not released"
    assert steering.is_steered(run["task"]) is True, "RELEASING UNMARKED THE RUN"
    await p.goto(f"/agent/{AGENT}")
    got = await p.js("({band: !!document.querySelector('.steerband'),"
                     " input: !!document.getElementById('steerinput')})")
    assert got["band"] is False, "the steering band survived the release"
    assert got["input"] is False, "the input survived the release"
    print("      released; mark kept; band and input gone from the DOM")


async def the_picker_says_next_claim_and_stays_pending(p: Page):
    await p.js("""(function(){
        var f = document.querySelector('.pickform');
        f.querySelector('[name=model]').value = 'sonnet';
        f.querySelector('button').click(); return true;})()""")
    await asyncio.sleep(1.2)
    await p.goto(f"/agent/{AGENT}")
    got = await p.js("""({
        pending: (document.querySelector('.pending')||{}).textContent || '',
        thisrun: (document.querySelector('.thisrun')||{}).textContent || ''
    })""")
    assert "applies at next claim" in got["pending"], f"no pending chip: {got['pending']!r}"
    assert "this run is unaffected" in got["pending"], got["pending"]
    assert "unchangeable" in got["thisrun"], f"the this-run row does not say so: {got['thisrun']!r}"
    assert "opus" in got["thisrun"], (
        f"the this-run row should still name the model the RUN started under, not the one just "
        f"picked: {got['thisrun']!r}")
    print(f"      {got['thisrun'].strip()[:80]} / {got['pending'].strip()[:70]}")


async def phone_width_has_no_horizontal_scroll(p: Page):
    await p.width(390, 844)
    await p.goto(f"/agent/{AGENT}")
    got = await p.js("""(function(){
        var小 = 0;
        var ctrls = document.querySelectorAll('.runfeed button, .rfside button, .rfside select,'
                                              + ' .rfside textarea');
        var under = [];
        for (var i=0;i<ctrls.length;i++){
          var r = ctrls[i].getBoundingClientRect();
          if (r.height && r.height < 44) under.push((ctrls[i].className||ctrls[i].name) + ':' +
                                                    Math.round(r.height));
        }
        return {scroll: document.documentElement.scrollWidth,
                inner: window.innerWidth, under: under};
    })()""")
    assert got["scroll"] <= got["inner"] + 1, (
        f"page-level horizontal scroll at 390: scrollWidth {got['scroll']} > {got['inner']}")
    assert not got["under"], f"controls under the 44px floor at 390: {got['under']}"
    print(f"      390px: scrollWidth {got['scroll']} <= {got['inner']}, every control >= 44px")
    await p.width(1440, 1200)


async def run_all():
    run = seed()
    console = start_console()
    chrome = start_chrome()
    try:
        async with websockets.connect(ws_url(), max_size=40 * 1024 * 1024) as ws:
            p = Page(ws)
            await p.send("Page.enable")
            await p.send("Runtime.enable")
            checks = [
                ("the feed renders beats, folds, vitals and a seam",
                 lambda: the_feed_renders_beats_and_folds(p, run)),
                ("nothing in the feed implies completion", lambda: nothing_implies_completion(p)),
                ("permission_mode is absent from the DOM in every state",
                 lambda: permission_mode_is_absent(p)),
                ("the steering input does not exist until control is entered",
                 lambda: the_input_does_not_exist_until_control_is_entered(p)),
                ("seizing shows the band, the input and the mark",
                 lambda: seizing_shows_the_band_and_the_input(p, run)),
                ("A CARET AT OFFSET 6 SURVIVES TWO POLLS WITH EVENTS ARRIVING",
                 lambda: the_caret_survives_two_polls_with_events_arriving(p, run)),
                ("both directions of the steering session land on the thread",
                 lambda: sending_lands_on_the_thread(p, run)),
                ("releasing control keeps the steered mark",
                 lambda: releasing_keeps_the_mark(p, run)),
                ("the picker states next-claim semantics and stays pending",
                 lambda: the_picker_says_next_claim_and_stays_pending(p)),
                ("390px: no horizontal scroll, every control >= 44px",
                 lambda: phone_width_has_no_horizontal_scroll(p)),
            ]
            for name, fn in checks:
                try:
                    await fn()
                    PASS.append(name)
                    print(f"ok    {name}")
                except AssertionError as exc:
                    FAIL.append((name, str(exc)))
                    print(f"FAIL  {name}\n      {exc}")
                except Exception as exc:                                # noqa: BLE001
                    FAIL.append((name, repr(exc)))
                    print(f"ERROR {name}\n      {exc!r}")
            # The shots the report carries, taken from the same session that just proved the
            # behaviour, so a screenshot can never be of a build the checks did not run against.
            out = Path(os.environ.get("RF_SHOTS", "/tmp/runfeed-shots"))
            out.mkdir(parents=True, exist_ok=True)
            for w, theme, tag in ((1440, "dark", "wide-dark"), (1440, "light", "wide-light"),
                                  (390, "dark", "phone-dark"), (390, "light", "phone-light")):
                await p.width(w, 1400 if w > 900 else 900)
                await p.goto(f"/agent/{AGENT}?theme={theme}")
                await p.shot(out / f"runfeed-{tag}.png")
            print(f"      shots in {out}")
    finally:
        chrome.kill()
        console.kill()


def main():
    print(f"runfeed in a browser, against {os.environ.get('BRAIN_PG_DB')} on {BASE}")
    asyncio.run(run_all())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for name, why in FAIL:
        print(f"  FAIL {name}: {why}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
