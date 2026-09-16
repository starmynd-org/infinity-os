"""The typable pane, proven rather than promised. No console, no browser, real child processes.

    python3 -m web.tests.test_terminal

MUST-NOT-BUILD item 11 was overruled by the operator on 2026-08-28 for a typable pane and for
nothing else, on two conditions. This suite exists because a condition that is only written down
is a condition nobody is holding, so both are asserted here against the code rather than against
the prose that describes it:

  1. LOOPBACK ONLY. `test_the_gate_is_the_route_table` counts the terminal routes `create_app()`
     registers under four configurations. Off loopback the pty endpoints are ABSENT, not hidden,
     so the claim is checkable from the route table. `test_the_request_gate` then forges the
     headers a proxy would add and watches the refusal.
  2. THE PANE CARRIES THE PANE AND NOTHING ELSE. `test_terminal_is_not_a_room` puts a POST at
     `/terminal/act`, the one write door, and reads the refusal BY NAME out of `rooms`. A second
     check greps this lane's own module for the write machinery and requires it absent -- an
     import that is not there cannot be called by a handler somebody adds later.

AND THE THING THAT MAKES IT A TERMINAL: `test_a_real_shell` forks a real pty, types into it, and
waits for the characters to come back. Everything above it is a claim about a boundary; that one
is the claim that the feature exists at all, and a suite carrying only the boundaries would go
green over a pane that does nothing.

NO SCRATCH-DATABASE PREFLIGHT, deliberately, and the absence is the point rather than an
oversight: this suite writes no store row, because the surface it tests cannot. It reads the store
only through `create_app()`'s own boot banner.
"""

from __future__ import annotations

import os
import re
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from web import rooms, terminal                                        # noqa: E402
from web.terminal_screen import Screen                                 # noqa: E402

FAILS: list = []
CHECKS = 0


def ck(name, cond, got=""):
    global CHECKS
    CHECKS += 1
    print(("  ok   " if cond else "  FAIL ") + name + (f"   [{got}]" if got and not cond else ""))
    if not cond:
        FAILS.append(name)


def _text(screen: Screen) -> str:
    return "\n".join("".join(r[0] for r in line) for line in screen.render()["lines"])


# --------------------------------------------------------------------------- 1. the screen

def test_the_screen_draws():
    print("\ncase 1 · the emulator, with no process anywhere near it")
    s = Screen(5, 20)
    s.feed(b"hello \x1b[1;31mred\x1b[0m")
    line = s.render()["lines"][0]
    ck("one plain run and one bold red run, and nothing between them",
       len(line) == 2 and line[0][0] == "hello " and line[1][0] == "red", repr(line))
    ck("the colour is a THEME slot, not a hex", line[1][1] == "color:var(--t1)", line[1][1])
    ck("bold rides as a class", line[1][2] == "b", line[1][2])

    s = Screen(3, 10)
    s.feed(b"\x1b[7mX\x1b[0m")
    run = s.render()["lines"][0][0]
    ck("reverse on a default run swaps ink and paper server side",
       "color:var(--tbg)" in run[1] and "background:var(--tfg)" in run[1], run[1])

    s = Screen(3, 10)
    s.feed(b"\x1b[38;5;173mA")
    ck("a 256-colour cell is resolved to its own hex, which no theme may reinterpret",
       s.render()["lines"][0][0][1] == "color:#d7875f", s.render()["lines"][0][0][1])


def test_deferred_wrap():
    print("\ncase 2 · the last column, which is where a naive emulator loses a line")
    s = Screen(4, 5)
    s.feed(b"abcde")
    ck("writing INTO the last column leaves the cursor on that line", s.y == 0 and s.x == 4,
       f"y={s.y} x={s.x}")
    s.feed(b"f")
    ck("the next character is what wraps", s.y == 1 and _text(s).split("\n")[1] == "f",
       repr(_text(s).split("\n")[:2]))
    s2 = Screen(4, 5)
    s2.feed(b"abcde\x08X")
    ck("a backspace after the last column edits that column, it does not wrap first",
       _text(s2).split("\n")[0] == "abcdX", repr(_text(s2).split("\n")[0]))


def test_alt_screen_and_scrollback():
    print("\ncase 3 · the alternate screen, and what may become history")
    s = Screen(3, 10, scrollback=50)
    s.feed(b"one\r\ntwo\r\nthree\r\nfour\r\n")
    ck("a full-height primary scroll pushes the top line into history",
       len(s.scrollback) == 2 and "".join(c[0] for c in s.scrollback[0]).strip() == "one",
       f"{len(s.scrollback)} kept")

    before = _text(s)
    s.feed(b"\x1b[?1049h")
    s.feed(b"TUI PAINT")
    ck("the alternate screen is a different buffer", "TUI PAINT" in _text(s))
    kept = len(s.scrollback)
    s.feed(b"\r\n" * 10)
    ck("scrolling the ALTERNATE screen adds nothing to history", len(s.scrollback) == kept,
       f"{kept} -> {len(s.scrollback)}")
    s.feed(b"\x1b[?1049l")
    ck("leaving it restores the primary screen byte for byte", _text(s) == before)

    s3 = Screen(4, 10, scrollback=50)
    s3.feed(b"a\r\nb\r\nc\r\nd\r\n")
    n = len(s3.scrollback)
    s3.feed(b"\x1b[2;3r")                      # a scrolling region inside the screen
    s3.feed(b"\r\n" * 6)
    ck("a scroll inside a REGION is a repaint, not history", len(s3.scrollback) == n,
       f"{n} -> {len(s3.scrollback)}")


def test_erase_and_history_accounting():
    print("\ncase 4 · what clears the transcript, and the gap that must be printed")
    s = Screen(2, 10, scrollback=50)
    s.feed(b"a\r\nb\r\nc\r\nd\r\n")
    ck("history exists to clear", len(s.scrollback) > 0, str(len(s.scrollback)))
    s.feed(b"\x1b[2J")
    ck("ED 2 clears the SCREEN and keeps the transcript", len(s.scrollback) > 0,
       str(len(s.scrollback)))
    s.feed(b"\x1b[3J")
    ck("ED 3, which is what `clear` sends, takes the transcript too", len(s.scrollback) == 0)

    s = Screen(2, 10, scrollback=3)
    s.feed(b"".join(f"L{i}\r\n".encode() for i in range(10)))
    h = s.history(have=0)
    ck("a bounded deque reports what it DROPPED rather than hiding the gap",
       h["dropped"] == h["total"] - len(s.scrollback) and h["dropped"] > 0, str(h["dropped"]))
    ck("the first line offered is the first one still kept", h["from"] == h["dropped"],
       f"from={h['from']} dropped={h['dropped']}")
    h2 = s.history(have=h["total"])
    ck("a client that is caught up is offered nothing", h2["lines"] == [], str(len(h2["lines"])))


def test_partial_utf8_and_replies():
    print("\ncase 5 · a glyph split across two reads, and the reports a shell waits on")
    s = Screen(2, 10)
    glyph = "é".encode()
    s.feed(glyph[:1])
    ck("half a glyph renders nothing at all", _text(s).strip() == "", repr(_text(s)))
    s.feed(glyph[1:])
    ck("the other half completes it, once", _text(s).strip() == "é", repr(_text(s).strip()))

    s = Screen(5, 20)
    s.feed(b"\x1b[3;7H\x1b[6n")
    ck("a cursor position report is answered with the position",
       s.pending_response == b"\x1b[3;7R", repr(s.pending_response))
    s.pending_response = b""
    s.feed(b"\x1b[c")
    ck("a device attributes request is answered", s.pending_response == b"\x1b[?6c",
       repr(s.pending_response))


# --------------------------------------------------------------------------- 2. the gate

def _app_under(env: dict):
    """Build a console under `env`, with every module that reads the environment reloaded.

    `web/host.py` reads CONSOLE_HOST and CONSOLE_ORIGIN AT IMPORT, so a test that only sets
    os.environ measures the configuration the interpreter started with. That would have been a
    passing test of nothing.
    """
    import importlib
    saved = {k: os.environ.get(k) for k in ("CONSOLE_HOST", "CONSOLE_ORIGIN", "CONSOLE_TERMINAL")}
    try:
        for k in saved:
            os.environ.pop(k, None)
        os.environ.update({k: v for k, v in env.items() if v is not None})
        import web.host
        import web.terminal
        import web.app
        importlib.reload(web.host)
        importlib.reload(web.terminal)
        importlib.reload(web.app)
        # THE REASON IS READ HERE, INSIDE THE ENVIRONMENT IT IS ABOUT. Read after the `finally`
        # below restores the process, it is the reason for the RESTORED configuration -- which is
        # "the pane is on" every time, and the assertion would be measuring nothing.
        return web.app.create_app(), web.terminal.config_reason()
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        import web.host
        import web.terminal
        import web.app
        importlib.reload(web.host)
        importlib.reload(web.terminal)
        importlib.reload(web.app)


def _terminal_routes(app) -> list:
    return sorted(str(r) for r in app.url_map.iter_rules() if str(r).startswith("/terminal"))


def test_the_gate_is_the_route_table():
    print("\ncase 6 · loopback only, enforced where it can be counted")
    cases = [
        ("loopback, nothing declared", {}, 6, ""),
        ("bound to the world", {"CONSOLE_HOST": "0.0.0.0"}, 1, "not loopback"),
        ("a proxy declared", {"CONSOLE_ORIGIN": "https://peer.tailnet-name.ts.net"}, 1,
         "CONSOLE_ORIGIN declares"),
        ("switched off", {"CONSOLE_TERMINAL": "off"}, 1, "switched off"),
        # THE ONE THAT NEARLY SWITCHED THE PANE OFF INSIDE ITS OWN TEST RUNNER.
        # `web/tests/run-all.sh` starts its console with CONSOLE_ORIGIN=http://127.0.0.1:<port>,
        # which is the console declaring the address it is already listening on and is not a proxy.
        ("a LOOPBACK origin declared, which is what run-all.sh does",
         {"CONSOLE_ORIGIN": "http://127.0.0.1:3123"}, 6, ""),
    ]
    for label, env, want, reason_frag in cases:
        app, reason = _app_under(env)
        got = _terminal_routes(app)
        ck(f"{label}: {want} terminal route(s)", len(got) == want, f"{len(got)}: {got}")
        if reason_frag:
            ck(f"{label}: the refusal names the condition", reason_frag in reason, reason[:70])
        else:
            ck(f"{label}: nothing to refuse", reason == "", reason[:70])
    print(f"  ---- {len(cases)} configurations compared")


def _csrf(client) -> str:
    body = client.get("/terminal").get_data(as_text=True)
    m = re.search(r'__TERMINAL_CSRF = "([^"]+)"', body)
    return m.group(1) if m else ""


def test_the_request_gate():
    print("\ncase 7 · the checks the configuration cannot make")
    from web.app import create_app
    app = create_app()
    c = app.test_client()
    token = _csrf(c)
    ck("the page mints a token when the pane is on", bool(token), repr(token[:20]))

    r = c.post("/terminal/open", data={"kind": "shell"},
               headers={"Origin": "http://localhost", "X-Forwarded-For": "203.0.113.9"})
    ck("a forwarding header refuses the pane", r.status_code == 403 and
       b"proxying" in r.data, f"{r.status_code} {r.data[:70]}")

    r = c.post("/terminal/open", data={"kind": "shell", "csrf": token},
               headers={"Origin": "http://evil.example"})
    ck("a cross-origin POST is refused", r.status_code == 403 and b"cross-origin" in r.data,
       f"{r.status_code} {r.data[:70]}")

    r = c.post("/terminal/open", data={"kind": "shell"}, headers={"Origin": "http://localhost"})
    ck("no CSRF token, no pty", r.status_code == 403 and b"CSRF" in r.data,
       f"{r.status_code} {r.data[:70]}")

    with app.test_request_context("/"):
        from web import guard
        other = guard.token_for("queue")
    r = c.post("/terminal/open", data={"kind": "shell", "csrf": other},
               headers={"Origin": "http://localhost"})
    ck("a token minted for the Queue is refused at the pane", r.status_code == 403,
       f"{r.status_code} {r.data[:70]}")

    r = c.post("/terminal/open", data={"kind": "rm -rf /", "csrf": token},
               headers={"Origin": "http://localhost"})
    ck("there is no command parameter: an unknown KIND is refused",
       r.status_code == 403 and b"unknown terminal kind" in r.data,
       f"{r.status_code} {r.data[:80]}")
    print("  ---- 6 refusals compared")


def test_terminal_is_not_a_room():
    print("\ncase 8 · condition 2: the pane carries the pane and nothing else")
    ck("`terminal` is not in rooms.ROOMS", "terminal" not in rooms.ROOMS, str(rooms.ROOMS))
    ck("`terminal` has no verb set at all", "terminal" not in rooms.ROOM_VERBS)
    try:
        rooms.assert_room_can_act("terminal")
        ck("the one write door refuses it BY NAME", False, "it did not raise")
    except rooms.RoomRefusal as exc:
        ck("the one write door refuses it BY NAME", "no such room" in str(exc), str(exc)[:60])

    from web.app import create_app
    app = create_app()
    c = app.test_client()
    token = _csrf(c)
    r = c.post("/terminal/act", data={"action": "done", "id": "0001", "csrf": token},
               headers={"Origin": "http://localhost"})
    ck("a POST at /terminal/act is a 403 and never a verb", r.status_code == 403,
       f"{r.status_code} {r.data[:70]}")

    # ON THE PARSED TREE, NEVER ON THE TEXT. The first version of this check grepped the source
    # and went red on this module's own DOCSTRING, which names `store.apply` in the sentence
    # explaining that it never calls it. A prose mention is not a call, and a check that cannot
    # tell them apart is a check that will be silenced rather than fixed.
    import ast
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "terminal.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
            imported.update(a.name for a in node.names)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    forbidden = ("store", "rooms", "actions", "model", "swarm_engine", "human_queue")
    for f in forbidden:
        ck(f"web/terminal.py neither imports nor names {f!r}",
           f not in imported and f not in names, f"imported={f in imported} named={f in names}")
    print(f"  ---- {len(forbidden)} write-side modules checked for absence, on the parsed tree")


# --------------------------------------------------------------------------- 3. a real shell

def _poll_until(session, needle, seconds=12.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        text = "\n".join("".join(r[0] for r in line) for line in session.screen.render()["lines"])
        hist = "\n".join("".join(c[0] for c in line) for line in list(session.screen.scrollback))
        if needle in text or needle in hist:
            return True
        session.wait_for_change(session.rev, 0.4)
    return False


def test_a_real_shell():
    print("\ncase 9 · a pty, typed into, answering")
    if not os.path.exists("/bin/sh"):
        print("  NOT RUN: no /bin/sh on this host, so there is no shell to open.")
        sys.exit(77)
    os.environ["SHELL"] = "/bin/sh"
    opened = []
    try:
        s = terminal.open_session("owner-a", "shell", rows=12, cols=60)
        opened.append(s)
        ck("the child is alive", s.exit_code is None and s.pid > 0, str(s.pid))
        s.write("echo TERMINAL-IS-TYPABLE\r")
        ck("what was typed comes back on the screen",
           _poll_until(s, "TERMINAL-IS-TYPABLE"), _text(s.screen)[-120:])

        s.write("stty size\r")
        ck("the child sees the size the pane asked for",
           _poll_until(s, "12 60"), _text(s.screen)[-120:])

        s.set_size(20, 90)
        s.write("stty size\r")
        ck("a resize reaches the child", _poll_until(s, "20 90"), _text(s.screen)[-120:])

        try:
            terminal.get_session("owner-b", s.key)
            ck("a stranger cannot attach to it", False, "it did not raise")
        except terminal.TerminalRefused as exc:
            ck("a stranger cannot attach to it", "no such session" in str(exc), str(exc))

        s.write("exit\r")
        for _ in range(40):
            if s.exit_code is not None:
                break
            s.wait_for_change(s.rev, 0.25)
        ck("the exit is observed and the child is reaped", s.exit_code is not None,
           repr(s.exit_code))
    finally:
        for s in opened:
            try:
                s.close(hard=True)
            except Exception:                                          # noqa: BLE001
                pass


def test_the_cap():
    print("\ncase 10 · the cap, because this endpoint forks")
    os.environ["SHELL"] = "/bin/sh"
    made = []
    try:
        while len(made) < terminal.MAX_SESSIONS:
            made.append(terminal.open_session("cap-owner", "shell", rows=5, cols=20))
        ck(f"{terminal.MAX_SESSIONS} open", terminal.live_count() >= terminal.MAX_SESSIONS,
           str(terminal.live_count()))
        try:
            made.append(terminal.open_session("cap-owner", "shell", rows=5, cols=20))
            ck("the next one is refused", False, "it opened")
        except terminal.TerminalRefused as exc:
            ck("the next one is refused", "cap is" in str(exc), str(exc)[:70])
    finally:
        for s in made:
            try:
                s.close(hard=True)
            except Exception:                                          # noqa: BLE001
                pass
        terminal._SESSIONS.clear()


def main() -> int:
    tests = (test_the_screen_draws, test_deferred_wrap, test_alt_screen_and_scrollback,
             test_erase_and_history_accounting, test_partial_utf8_and_replies,
             test_the_gate_is_the_route_table, test_the_request_gate,
             test_terminal_is_not_a_room, test_a_real_shell, test_the_cap)
    for t in tests:
        try:
            t()
        except SystemExit:
            raise
        except Exception:                                              # noqa: BLE001
            traceback.print_exc()
            FAILS.append(t.__name__ + " raised")
    passed = CHECKS - len(FAILS)
    if CHECKS == 0:                                                    # DENOMINATOR
        print("\n0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"\n{passed} passed, {len(FAILS)} failed, of {CHECKS} compared "
          f"across {len(tests)} cases")
    for f in FAILS:
        print("  FAILED:", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
