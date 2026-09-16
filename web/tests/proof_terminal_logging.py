"""End to end: open Claude Code from the browser, talk to it, and find the conversation indexed.

    BASE=http://127.0.0.1:3107 TASK=0437 python3 -m web.tests.proof_terminal_logging

THIS IS NOT NAMED `test_*` AND THAT IS DELIBERATE. It launches a REAL Claude Code session, which
costs real money and writes a real thread note on a real row, so no runner may pick it up by
globbing. It is the proof the operator asked for by name and it is run on purpose, by a human or
by a lane that has decided to spend on it.

    "the terminal ... and PROOF that every terminal conversation is logged. That was a key part of
     the product that is not being tracked."   -- the operator, 2026-08-28, row 0421 item 10

WHAT IT PROVES, AND WHY EACH STEP IS THERE RATHER THAN A SHORTCUT:

  1. The session is launched THE WAY HE WOULD LAUNCH IT: a click on the pane's own Claude Code
     button, not `swarm helper` in a shell. A proof that ran the CLI would be proving the CLI.
  2. The conversation is TYPED IN THE BROWSER, and both halves are counted. The denominator is
     exchanges typed against exchanges found in the transcript on disk, because "it is logged" is
     a claim about content and not about a row existing.
  3. The session is ENDED, because the transcript is indexed at `SessionEnd` and a proof that
     stopped before that would be measuring a file the index has not seen yet. That floor is
     `ingest/ingest/queries.py`'s own, and `web/sessions.py` renders it as prose beside the number.
  4. The row, the session and the transcript are then read back and shown to name the SAME
     conversation: the thread note on the row carries the session id `swarm helper` minted before
     the launch, and that id is the key in `brain.session` and the link on `brain.transcript`.

SAFETY, BECAUSE THIS OPENS AN AGENT WITH EDIT RIGHTS IN A TREE FOUR OTHER AGENTS ARE WORKING IN.
The operator's `~/.claude/settings.json` sets `defaultMode: auto`, so the helper CAN edit. The
opening turn is interrupted with Escape from the moment the pane paints, the first thing typed
forbids edits, and the tree is diffed before and after and printed either way. A proof that
quietly changed the thing it was proving would be worse than no proof.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time

_R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_R, os.path.join(_R, "engine"), os.path.join(_R, "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_terminal_human_path import Page, _launch, _ws_url, BASE      # noqa: E402

import websockets                                                       # noqa: E402

TASK = os.environ.get("TASK", "0437")
STAMP = str(int(time.time()))[-6:]
WORD_ONE = f"ORANGERY-{STAMP}"
WORD_TWO = f"MARGATE-{STAMP}"

FIRST = (f"Do not read, write or edit any file, and run no command. Reply with exactly the one "
         f"word {WORD_ONE} and nothing else.")
SECOND = (f"Same again, no tools: reply with exactly the one word {WORD_TWO} and nothing else.")

OUT: list = []


def say(line):
    OUT.append(line)
    print(line, flush=True)


def _sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=_R).stdout


async def _type(p: Page, text):
    for ch in text:
        await p.key(ch)
    await asyncio.sleep(0.2)
    await p.key("\r")


async def _wait_answer(p: Page, needle, seconds):
    """Wait for the needle to appear TWICE: once in what was typed, once in what came back.

    The first version of this waited for one occurrence and reported "answered in 0.0 s" on every
    exchange, because the word it was waiting for was in the prompt it had just typed. It was
    measuring its own echo. A proof that measures its own input is not a proof.
    """
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        if (await p.screen_text() or "").count(needle) >= 2:
            return (time.monotonic() - t0) * 1000.0
        await asyncio.sleep(0.25)
    return None


async def _run():
    proc = _launch()
    key = None
    try:
        async with websockets.connect(_ws_url(), max_size=64 * 1024 * 1024) as ws:
            p = Page(ws)
            await p.send("Page.enable")
            await p.send("Runtime.enable")

            say("\nstep 1 · reach the pane by URL (its nav door is held by ruling), and open "
                "Claude Code on the row")
            # THE NAV DOOR IS HELD BY RULING since 2026-09-13: Andrew's P14 direction F7 removed it
            # (Platform a73398f), and MUST-NOT-BUILD item 11's typable-pane question is open. So
            # this proof reaches the pane by URL. `test_terminal_human_path.py` case 1 asserts
            # the door is absent; this proof only needs the pane, and clicking an absent door
            # raises before Claude Code opens.
            await p.goto("/terminal")
            armed = await p.wait_armed()
            say(f"  the openers armed after {armed:.0f} ms"
                if armed is not None else "  the openers NEVER armed")
            await p.js(f"document.getElementById('ttask').value = {json.dumps(TASK)}")
            t0 = time.monotonic()
            await p.click_text("button.tbtn", "Claude Code")
            for _ in range(200):
                await asyncio.sleep(0.1)
                key = await p.js("window.__tkey || null")
                if key:
                    break
            label = await p.js("document.getElementById('tstatus').innerText")
            say(f"  a pane opened in {(time.monotonic() - t0) * 1000:.0f} ms, session key {key}")
            say(f"  the pane header reads: {label!r}")
            await asyncio.sleep(3)
            geom = await p.js("document.getElementById('tsize').innerText")
            say(f"  the pane geometry is {geom}")

            say("\nstep 2 · interrupt the opening turn before it can act")
            # ESC from the moment the pane paints. `claude` boots its TUI first, so the window
            # this is racing is the launch itself and not a running tool call.
            for _ in range(40):
                await p.js("document.getElementById('tscreen').focus()")
                await p.send("Input.dispatchKeyEvent", type="keyDown", key="Escape",
                             code="Escape", windowsVirtualKeyCode=27, nativeVirtualKeyCode=27)
                await p.send("Input.dispatchKeyEvent", type="keyUp", key="Escape",
                             code="Escape", windowsVirtualKeyCode=27, nativeVirtualKeyCode=27)
                await asyncio.sleep(0.4)
            say("  16 seconds of Escape sent into the pane")

            say("\nstep 3 · two exchanges, typed in the browser")
            typed = 0
            answered = 0
            await p.js("document.getElementById('tscreen').focus()")
            await _type(p, FIRST)
            typed += 1
            ms = await _wait_answer(p, WORD_ONE, 150)
            if ms is not None:
                answered += 1
                say(f"  exchange 1: answered in {ms / 1000:.1f} s, the pane shows {WORD_ONE}")
            else:
                say(f"  exchange 1: NO second occurrence of {WORD_ONE} within 150 s")
            await asyncio.sleep(2)
            await p.js("document.getElementById('tscreen').focus()")
            await _type(p, SECOND)
            typed += 1
            ms = await _wait_answer(p, WORD_TWO, 150)
            if ms is not None:
                answered += 1
                say(f"  exchange 2: answered in {ms / 1000:.1f} s, the pane shows {WORD_TWO}")
            else:
                say(f"  exchange 2: NO second occurrence of {WORD_TWO} within 150 s")
            say(f"  typed {typed} exchanges, {answered} answered on screen")

            say("\nstep 4 · end the session, because the transcript is indexed at SessionEnd")
            await p.js("document.getElementById('tscreen').focus()")
            await _type(p, "/exit")
            await asyncio.sleep(6)
            alive = await p.js("(document.getElementById('tstatus').innerText || '')")
            if "exited" not in alive:
                # Ctrl-D, then the pane's own Close, which SIGHUPs the child.
                await p.send("Input.dispatchKeyEvent", type="keyDown", key="d", code="KeyD",
                             modifiers=2, windowsVirtualKeyCode=68, nativeVirtualKeyCode=68)
                await p.send("Input.dispatchKeyEvent", type="keyUp", key="d", code="KeyD",
                             modifiers=2, windowsVirtualKeyCode=68, nativeVirtualKeyCode=68)
                await asyncio.sleep(6)
                alive = await p.js("(document.getElementById('tstatus').innerText || '')")
            say(f"  the pane now reads: {alive!r}")
            if "exited" not in alive:
                await p.click_text("button.tbtn", "Close pane")
                await asyncio.sleep(4)
                closed = await p.js("document.getElementById('tstatus').innerText")
                say(f"  after Close pane: {closed!r}")
            return typed, answered
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), 9)
        except Exception:                                               # noqa: BLE001
            pass


def main() -> int:
    say(f"proof_terminal_logging.py  --  a real Claude Code session, opened from {BASE}, on {TASK}")
    before = _sh("git status --short")
    typed, answered = asyncio.run(_run())

    say("\nstep 5 · the row, the session and the transcript, read back")
    show = _sh(f"BRAIN_PG_DB=brain python3 engine/bin/swarm show {TASK} --full")
    sid = ""
    for line in show.splitlines():
        if "helper terminal opened by" in line and "session=" in line:
            sid = line.split("session=", 1)[1].split()[0]
    say(f"  the row's newest helper note names session {sid or '(none found)'}")

    # THE SCHEMA IS THE SCHEMA AND NOT WHAT A LANE REMEMBERS. `brain.session` keys on `id` and
    # `brain.transcript` links on `session_id` and holds `pointer`, not `path`. The first version
    # of this file guessed `session_key` from `ingest/README.md`, which describes D3's OWN scratch
    # tables, and died on `column "session_key" does not exist` AFTER spending a real session.
    import store                                                        # noqa: E402
    rows = tx = []
    if sid:
        with store.read() as s:
            rows = s.query("SELECT id, harness, agent, role, workdir, work_item_id, started_at, "
                           "ended_at, actor_type, stated_goal FROM brain.session "
                           "WHERE id = %(k)s", {"k": sid})
            tx = s.query("SELECT id, session_id, pointer, sha256, bytes, indexed_at, verified_at "
                         "FROM brain.transcript WHERE session_id = %(k)s", {"k": sid})
    say(f"  brain.session rows for that id: {len(rows)}")
    for r in rows:
        say(f"    {r['id']}  harness={r['harness']}  role={r['role']}  actor={r['actor_type']}")
        say(f"    started={r['started_at']}  ended={r['ended_at']}")
        say(f"    workdir={r['workdir']}")
        say(f"    stated_goal: {str(r['stated_goal'])[:160]}")
        if str(r["stated_goal"] or "").strip() == FIRST.strip():
            say("    the stored goal is BYTE-IDENTICAL to what was typed in the browser")
        else:
            say("    the stored goal DIFFERS from what was typed. Typed:")
            say(f"      {FIRST}")
    say(f"  brain.transcript rows for that id: {len(tx)}")
    found = 0
    for r in tx:
        say(f"    id={r['id']}  bytes={r['bytes']}  sha256={str(r['sha256'])[:16]}...  "
            f"indexed_at={r['indexed_at']}  verified_at={r['verified_at']}")
        say(f"    pointer: {r['pointer']}")
        try:
            blob = open(r["pointer"], encoding="utf-8", errors="replace").read()
        except OSError as exc:
            say(f"    could not read the transcript: {exc}")
            continue
        for word in (WORD_ONE, WORD_TWO):
            hit = blob.count(word)
            say(f"    {word}: {hit} occurrence(s) in the indexed file")
            if hit:
                found += 1
    if not tx:
        # A MISSING TRANSCRIPT ROW IS A FINDING, NOT AN ERROR TO SWALLOW. The index records why a
        # pointer is not there, and reporting "0 transcripts" without asking those tables would be
        # the same shape of silence this whole feature exists against.
        # THE CONSOLE'S ROLE CANNOT READ THE `ingest` SCHEMA (`permission denied for schema
        # ingest`, measured 2026-08-29), which is correct: the console is a reader of `brain` and
        # D3's producer tables are not its business. So the question is asked through the CLI that
        # owns them rather than around it.
        say("    NO transcript row for this session. Asking the index why:")
        out = _sh("timeout 120 ingest/bin/ingest coverage 2>&1 | tail -40")
        for line in out.splitlines()[-14:]:
            say(f"      {line}")
        import glob as _glob
        hits = [f for f in _glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))
                if sid and sid in f]
        say(f"    transcript files on disk named for this session: {len(hits)} {hits[:2]}")
    say(f"\n  DENOMINATOR: {typed} exchanges typed in the browser, {answered} answered on screen, "
        f"{found} of {typed} found in the transcript the index points at")

    say("\nstep 6 · did the helper change the tree")
    after = _sh("git status --short")
    if after == before:
        say("  git status is byte-identical before and after. The helper edited nothing.")
    else:
        say("  THE TREE MOVED. Lines that appeared:")
        for line in set(after.splitlines()) - set(before.splitlines()):
            say(f"    {line}")
        say("  (four other agents are live in this tree, so a moved line is not proof it was "
            "the helper. Check mtimes before concluding anything.)")
    return 0 if (sid and rows and tx and found == typed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
