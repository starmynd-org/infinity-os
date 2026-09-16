"""The typable pane: one pty per session, a screen the server draws, and NO write door.

MUST-NOT-BUILD item 11 was overruled by the operator on 2026-08-28 **for a typable terminal pane
only**, and the amendment re-arms the rest of that item rather than leaving it alone. Both of its
conditions are enforced HERE, in code, and neither is a comment:

1. **Loopback only, no tunnel, no reverse proxy, no Funnel.** `availability()` below is the gate
   and it is CLOSED by default off loopback: a non-loopback `CONSOLE_HOST`, a declared
   `CONSOLE_ORIGIN` (which is how `web/host.py` says a proxy is in front), a forwarding header on
   the request, or a peer address that is not loopback each refuse this surface with the reason
   printed. There is deliberately NO environment variable that opens it anyway. The phone
   question is the operator's and is open: `outputs/2026-08-29-commander/lane-B3-phone-proposal.md`
   is the proposal, four options with their costs, and until he rules the code's answer is no.
   (The path this docstring shipped with, `outputs/2026-08-28-T2-0437-typable-terminal/
   PHONE-PROPOSAL.md`, was never written. A refusal that cites a document nobody can open is a
   refusal that reads as a bug, so it was repointed on 2026-08-29 rather than left.)

2. **The socket carries the terminal pane and nothing else.** `room='terminal'` is NOT in
   `rooms.ROOMS`, so `POST /terminal/act` is refused BY NAME at the one write door -- the same
   posture `/live` took, and stronger than an empty allowlist because it does not depend on an
   allowlist staying empty. Nothing in this module imports `rooms`, `actions` or `store.apply`,
   nothing here calls a verb, and `web/tests/test_terminal.py` asserts both by inspecting this
   module's own symbols rather than by reading this paragraph.

WHAT IS NOT HERE, AND IT IS THE HEADLINE. **There is no websocket.** The overrule permits one; it
does not require one, and nothing on this host can serve one -- measured 2026-08-28: `flask_sock`,
`simple_websocket`, `wsproto`, `gevent`, `eventlet`, `ptyprocess` and `pyte` are all absent, and
the repo carries no `requirements.txt` or `pyproject.toml` in which a new dependency could be
declared. So the pane polls over plain `fetch` exactly as every other room does, the browser
prohibition probe (`pw_prohibitions.py`) still measures `EventSource` and `WebSocket` NEVER
CONSTRUCTED, and the overrule is banked unspent. `web/terminal_screen.py` says why a screen beats
a byte stream even when a socket is available.

THE COST, STATED WHERE IT IS INCURRED. Before this file, exposing the console leaked a dashboard.
After it, exposing the console leaks a SHELL. That sentence is the reason `availability()` refuses
rather than warns, and the reason its refusals are printed on the page instead of logged.
"""

from __future__ import annotations

import errno
import fcntl
import os
import pty
import re
import signal
import struct
import termios
import threading
import time
import uuid

from flask import Blueprint, jsonify, render_template, request

from . import guard, host
from .terminal_screen import Screen

bp = Blueprint("terminal", __name__)

#: The room name. It is NOT in `rooms.ROOMS` and must never be added: see condition 2 above.
ROOM = "terminal"

#: Process-wide cap. A pty is a real child process; an endpoint that can be POSTed in a loop and
#: forks every time is a fork bomb with a CSRF token on it.
MAX_SESSIONS = 6

#: How long a poll may block waiting for the screen to change, when the server is threaded.
#: Bounded on purpose: a laptop that sleeps mid-poll leaves a connection that CLOSES ON ITS OWN,
#: which is the property `web/app.py`'s header claims for poll-and-patch and this must not lose.
MAX_WAIT_SECONDS = 20.0

#: A pane the browser stopped polling for this long is reaped WITH ITS CHILD. The operator closing
#: a tab must not leave `claude` running against a session nobody is reading.
IDLE_REAP_SECONDS = 30 * 60

_LOOPBACK = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"})

#: Headers that only exist when something is in front of this process. Used to REFUSE, never to
#: permit, which is the safe direction for a header a request can forge: an attacker who sets one
#: has locked themselves out, and `web/host.py` explains at length why the other direction
#: (trusting `X-Forwarded-Proto` via `ProxyFix`) is the mistake this console was written against.
_PROXY_HEADERS = ("X-Forwarded-For", "X-Forwarded-Proto", "X-Forwarded-Host", "X-Real-IP",
                  "Forwarded")

_TASK_ID = re.compile(r"^[0-9]{1,8}$")
_ENGINES = frozenset({"claude", "codex"})


class TerminalRefused(Exception):
    """This pane may not open, or may not be reached from here. Never a state refusal."""


# --------------------------------------------------------------------------- is it even allowed

def _is_loopback(addr: str) -> bool:
    return (addr or "") in _LOOPBACK


def _host_of(origin: str) -> str:
    """`http://127.0.0.1:3123` -> `127.0.0.1`. Port and brackets off, nothing else touched."""
    rest = origin.split("://", 1)[-1]
    if rest.startswith("["):
        return rest[1:rest.index("]")] if "]" in rest else rest
    return rest.rsplit(":", 1)[0] if ":" in rest else rest


def config_reason() -> str:
    """Why the pane is off, from CONFIGURATION alone. Empty string means on. No request needed.

    Split from the per-request check so `web/bin/console` and a test can both ask the question
    without a request context, and so the page can state the reason rather than 404.
    """
    if (os.environ.get("CONSOLE_TERMINAL") or "").strip().lower() in ("0", "off", "no", "false"):
        return ("the terminal pane is switched off: CONSOLE_TERMINAL is set to off in this "
                "console's environment.")
    if not hasattr(os, "forkpty"):
        return "this platform has no pty. A terminal pane needs one and will not fake it."
    if not _is_loopback(host.BIND_HOST):
        return (f"this console is bound to {host.BIND_HOST}, not loopback. MUST-NOT-BUILD item 11 "
                f"was overruled for a typable pane ON LOOPBACK ONLY: before the pane, exposing "
                f"the console leaked a dashboard, and after it, it leaks a shell. Set "
                f"CONSOLE_HOST=127.0.0.1 to have the pane back.")
    # A DECLARED ORIGIN THAT IS ITSELF LOOPBACK IS NOT A PROXY, and the distinction is not
    # academic: `web/tests/run-all.sh` starts its console with `CONSOLE_ORIGIN=http://127.0.0.1:
    # <port>`, which is the console declaring the address it is already listening on. Refusing on
    # the mere PRESENCE of the value would have switched this pane off inside its own test runner
    # and every suite below would have measured the refusal instead of the pane. What names a
    # proxy is a declared origin whose HOST is not loopback -- shape A's `https://<peer>.ts.net`.
    remote = [o for o in host.extra_origins() if not _is_loopback(_host_of(o))]
    if remote:
        return (f"CONSOLE_ORIGIN declares {', '.join(remote)}, which is how web/host.py says a "
                f"proxy or `tailscale serve` is in front of this console. The pane is refused "
                f"there: a phone on the tailnet reaching a DASHBOARD was lane D's answer, and a "
                f"phone on the tailnet reaching a SHELL is a different question that is open with "
                f"the operator (outputs/2026-08-29-commander/lane-B3-phone-proposal.md).")
    return ""


def request_reason() -> str:
    """Why this REQUEST may not have the pane. Empty string means it may.

    Two checks the configuration cannot make. The peer address is the only one of the four that
    is measured at the wire rather than read out of the environment, so it is the one that still
    holds when somebody starts the console in a way nothing here anticipated.
    """
    cfg = config_reason()
    if cfg:
        return cfg
    for h in _PROXY_HEADERS:
        if request.headers.get(h):
            return (f"this request arrived with {h}, so something is proxying it. The pane is "
                    f"loopback-direct only.")
    if not _is_loopback(request.remote_addr or ""):
        return (f"this request came from {request.remote_addr!r}, which is not loopback. The pane "
                f"is served to 127.0.0.1 and nothing else.")
    return ""


def is_available() -> bool:
    return not config_reason()


# --------------------------------------------------------------------------- one pty session

class PtySession:
    """One child process on one pty, one screen, one reader thread."""

    def __init__(self, key: str, owner: str, argv: list, cwd: str, label: str,
                 rows: int, cols: int):
        self.key = key
        self.owner = owner
        self.argv = list(argv)
        self.cwd = cwd
        self.label = label
        self.opened_at = time.time()
        self.last_seen = time.time()
        self.exit_code = None
        self.bytes_in = 0
        self.bytes_out = 0
        self.screen = Screen(rows=rows, cols=cols)
        self.rev = 0
        self._lock = threading.Lock()
        self._changed = threading.Event()
        self._closing = False

        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env["LINES"] = str(rows)
        env["COLUMNS"] = str(cols)
        # A pane inside the console is still a console surface, and the engine reads this to know
        # which human it is acting as. Left exactly as the console process has it.
        env.pop("SWARM_PARENT_TASK", None)
        # AND THE ONE VARIABLE THAT SILENTLY BREAKS THE HALF OF THIS FEATURE THE OPERATOR ASKED
        # FOR BY NAME. Measured 2026-08-29, A/B, two otherwise identical pty sessions:
        #
        #     CLAUDE_CODE_CHILD_SESSION set     ->  answered fine, 0 transcript files written
        #     ONLY that variable removed        ->  answered fine, 1 transcript file written
        #
        # A `claude` that believes it is a CHILD of another Claude Code session writes no
        # transcript of its own, so `SessionEnd` has nothing to index, so the conversation never
        # reaches `brain.transcript` and never appears in the Sessions room. The session row is
        # still written, which is what makes it so quiet: the record says the conversation
        # happened and cannot say what was said.
        #
        # THIS IS NOT AN EXOTIC CONDITION. It is set in every terminal a Claude Code session
        # spawned, which is how the fleet, the commander and any agent-started console are run,
        # and it is inherited straight through `dict(os.environ)` into the pty. Five real helper
        # sessions were lost to it this morning before it was found.
        #
        # NAMED, NOT PREFIX-STRIPPED, on purpose: `CLAUDE_CODE_*` also carries real configuration
        # the operator may have set, and a wildcard here would silently disable it.
        env.pop("CLAUDE_CODE_CHILD_SESSION", None)

        pid, fd = pty.fork()
        if pid == 0:                                             # pragma: no cover - the child
            # BETWEEN fork AND exec, DO NOTHING ELSE. The parent is threaded, so anything that
            # takes a lock here can deadlock a child that will never be able to release it. The
            # environment was built above, before the fork, precisely so this branch is one call.
            try:
                os.chdir(cwd)
                os.execvpe(self.argv[0], self.argv, env)
            except BaseException:
                os._exit(127)
        self.pid = pid
        self.fd = fd
        self.set_size(rows, cols)
        self._reader = threading.Thread(target=self._read_loop, name=f"pty-{key}", daemon=True)
        self._reader.start()

    # ------------------------------------------------------------------ the reader

    def _read_loop(self) -> None:
        """Drain the pty forever. THE DRAIN IS NOT OPTIONAL and does not depend on a client.

        A pty whose master is never read fills its buffer and the child BLOCKS on write. If this
        loop only ran while a browser was polling, closing the laptop lid would freeze `claude`
        mid-answer and it would look like the model had hung.
        """
        while True:
            try:
                data = os.read(self.fd, 65536)
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                data = b""                                       # EIO: the child let go of the pty
            if not data:
                break
            with self._lock:
                self.bytes_out += len(data)
                self.screen.feed(data)
                reply = self.screen.pending_response
                self.screen.pending_response = b""
                self.rev += 1
            if reply:
                # The program asked where the cursor is, or what we are. An unanswered DSR is a
                # shell that never draws its prompt, so this is a reply and not an input.
                try:
                    os.write(self.fd, reply)
                except OSError:
                    pass
            self._changed.set()
            self._changed.clear()
        self._finish()

    def _finish(self) -> None:
        try:
            _, status = os.waitpid(self.pid, 0)
            self.exit_code = (os.WEXITSTATUS(status) if os.WIFEXITED(status)
                              else -os.WTERMSIG(status) if os.WIFSIGNALED(status) else -1)
        except (ChildProcessError, OSError):
            self.exit_code = self.exit_code if self.exit_code is not None else -1
        with self._lock:
            self.rev += 1
        self._changed.set()
        self._changed.clear()

    # ------------------------------------------------------------------ the operator's end

    def write(self, text: str) -> int:
        if self.exit_code is not None:
            raise TerminalRefused("this session has exited. Open a new one.")
        data = text.encode("utf-8", "replace")
        n = 0
        while n < len(data):
            try:
                n += os.write(self.fd, data[n:])
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                raise TerminalRefused(f"the pty would not take input: {exc}") from None
        self.bytes_in += len(data)
        return len(data)

    def set_size(self, rows: int, cols: int) -> None:
        rows = max(1, min(200, int(rows)))
        cols = max(20, min(400, int(cols)))
        with self._lock:
            self.screen.resize(rows, cols)
            self.rev += 1
        try:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass
        self._changed.set()
        self._changed.clear()

    def wait_for_change(self, since_rev: int, seconds: float) -> None:
        """Block until the screen moves past `since_rev`, or the deadline. Never longer."""
        deadline = time.time() + max(0.0, seconds)
        while self.rev <= since_rev and self.exit_code is None:
            left = deadline - time.time()
            if left <= 0:
                return
            self._changed.wait(min(left, 0.5))

    def snapshot(self, have_history: int) -> dict:
        with self._lock:
            out = self.screen.render()
            out["history"] = self.screen.history(max(0, have_history))
            out["rev"] = self.rev
        out.update({
            "key": self.key,
            "label": self.label,
            "cwd": self.cwd,
            "argv": self.argv,
            "pid": self.pid,
            "alive": self.exit_code is None,
            "exit_code": self.exit_code,
            "opened_at": self.opened_at,
            "bytes_in": self.bytes_in,
            "bytes_out": self.bytes_out,
        })
        return out

    def close(self, hard: bool = False) -> None:
        self._closing = True
        try:
            os.killpg(os.getpgid(self.pid), signal.SIGKILL if hard else signal.SIGHUP)
        except (OSError, ProcessLookupError):
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass


# --------------------------------------------------------------------------- the registry

_SESSIONS: dict[str, PtySession] = {}
_REGISTRY_LOCK = threading.Lock()


def _reap() -> None:
    """Drop finished sessions the browser has already seen, and kill panes nobody is reading."""
    now = time.time()
    with _REGISTRY_LOCK:
        for key, s in list(_SESSIONS.items()):
            idle = now - s.last_seen
            if s.exit_code is not None and idle > 120:
                _SESSIONS.pop(key, None)
            elif idle > IDLE_REAP_SECONDS:
                s.close(hard=True)
                _SESSIONS.pop(key, None)


def live_count() -> int:
    return sum(1 for s in _SESSIONS.values() if s.exit_code is None)


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _argv_for(kind: str, task: str, engine: str) -> tuple[list, str, str]:
    """The command, the cwd and the label. THE CLIENT NEVER SENDS A COMMAND STRING.

    It sends a KIND from a fixed set, and this function builds the argv. Once a shell is open that
    distinction buys nothing against the operator, and everything against a request that reached
    this endpoint some way nobody predicted: a `cmd=` parameter is a remote-execution API whether
    or not the guard in front of it currently holds.
    """
    root = _repo_root()
    if kind == "shell":
        shell = os.environ.get("SHELL") or "/bin/bash"
        return [shell, "-l"], root, os.path.basename(shell)
    if kind == "helper":
        if not _TASK_ID.match(task or ""):
            raise TerminalRefused(f"{task!r} is not a task id. `helper` opens a session ON a row.")
        if engine not in _ENGINES:
            raise TerminalRefused(f"engine {engine!r} is not one of: {', '.join(sorted(_ENGINES))}")
        swarm = os.path.join(root, "engine", "bin", "swarm")
        if not os.path.exists(swarm):
            raise TerminalRefused(f"no swarm CLI at {swarm}.")
        # `swarm helper` resolves the row's workdir itself and REFUSES one that is set and
        # missing, so this does not second-guess it: it starts at the repo root and lets the verb
        # move. It also refuses a non-tty stdin, which a pty satisfies -- that is the whole reason
        # this pane can run it and the old paste-block could not be automated.
        return [swarm, "helper", task, "--engine", engine], root, f"helper {task} · {engine}"
    raise TerminalRefused(f"unknown terminal kind {kind!r}: one of shell, helper.")


def open_session(owner: str, kind: str, task: str = "", engine: str = "claude",
                 rows: int = 30, cols: int = 100) -> PtySession:
    _reap()
    if live_count() >= MAX_SESSIONS:
        raise TerminalRefused(
            f"{live_count()} terminals are already open and the cap is {MAX_SESSIONS}. Close one. "
            f"The cap exists because every one of these is a real child process.")
    argv, cwd, label = _argv_for(kind, task, engine)
    key = uuid.uuid4().hex[:16]
    s = PtySession(key, owner, argv, cwd, label, rows, cols)
    with _REGISTRY_LOCK:
        _SESSIONS[key] = s
    return s


def get_session(owner: str, key: str) -> PtySession:
    """A session is BOUND TO THE BROWSER THAT OPENED IT, and a stranger gets `no such session`.

    Not 403: the refusal must not tell a caller that a key it guessed happens to exist. The owner
    is `guard.session_id()`, the same cookie a CSRF token is bound to, so a second browser on the
    same loopback host cannot read the first one's shell.
    """
    s = _SESSIONS.get(key)
    if s is None or s.owner != owner:
        raise TerminalRefused(f"no such session: {key!r}")
    s.last_seen = time.time()
    return s


def sessions_for(owner: str) -> list:
    _reap()
    return [{"key": s.key, "label": s.label, "alive": s.exit_code is None,
             "exit_code": s.exit_code, "opened_at": s.opened_at, "cwd": s.cwd,
             "title": s.screen.title}
            for s in sorted(_SESSIONS.values(), key=lambda x: x.opened_at)
            if s.owner == owner]


# --------------------------------------------------------------------------- the routes
#
# SIX ROUTES ON A BLUEPRINT, AND `web/app.py` GAINS ONE LINE. Not because a blueprint is tidier
# but because this lane does not own that file: a surface that needs six entries in somebody
# else's module is a surface that will collide with whoever else is editing it. `register` below
# is the entire contact.

def _refuse(msg: str, code: int = 403):
    return jsonify({"ok": False, "kind": "terminal-refusal", "error": msg}), code


def _guard_post() -> str:
    """Everything true before a keystroke is taken. Returns the owner id."""
    reason = request_reason()
    if reason:
        raise TerminalRefused(reason)
    guard.check_origin()
    guard.check_token(ROOM, (request.form.get("csrf") or
                             request.headers.get("X-CSRF") or "").strip())
    return guard.session_id()


bp_page_only = Blueprint("terminal_off", __name__)


@bp.get("/terminal")
@bp_page_only.get("/terminal")
def terminal_page():
    """The pane. It renders its refusal rather than 404-ing, because the reason is the point."""
    reason = request_reason()
    return render_template(
        "terminal.html", room=ROOM, refused=reason,
        csrf=("" if reason else guard.token_for(ROOM)),
        sessions=([] if reason else sessions_for(guard.session_id())),
        cap=MAX_SESSIONS, repo=_repo_root(),
        task=(request.args.get("task") or "").strip()[:8],
    )


@bp.post("/terminal/open")
def terminal_open():
    try:
        owner = _guard_post()
        s = open_session(
            owner,
            kind=(request.form.get("kind") or "shell").strip(),
            task=(request.form.get("task") or "").strip(),
            engine=(request.form.get("engine") or "claude").strip(),
            rows=int(request.form.get("rows") or 30),
            cols=int(request.form.get("cols") or 100),
        )
    except guard.WriteRefused as exc:
        return _refuse(str(exc))
    except TerminalRefused as exc:
        return _refuse(str(exc))
    except (ValueError, OSError) as exc:
        return _refuse(f"could not open a terminal: {exc}", 500)
    return jsonify({"ok": True, "key": s.key, "label": s.label, "cwd": s.cwd, "argv": s.argv})


@bp.post("/terminal/<key>/input")
def terminal_input(key):
    try:
        owner = _guard_post()
        s = get_session(owner, key)
        n = s.write(request.form.get("data") or "")
    except guard.WriteRefused as exc:
        return _refuse(str(exc))
    except TerminalRefused as exc:
        return _refuse(str(exc), 404 if "no such session" in str(exc) else 403)
    # Settle before answering. The child needs a moment to echo, and a reply that raced it would
    # make every keystroke feel one keystroke behind even though the poll would fix it 80ms later.
    s.wait_for_change(s.rev - 1, 0.12)
    return jsonify({"ok": True, "bytes": n, "screen": s.snapshot(int(request.form.get("have") or 0))})


@bp.post("/terminal/<key>/resize")
def terminal_resize(key):
    try:
        owner = _guard_post()
        s = get_session(owner, key)
        s.set_size(int(request.form.get("rows") or 30), int(request.form.get("cols") or 100))
    except guard.WriteRefused as exc:
        return _refuse(str(exc))
    except TerminalRefused as exc:
        return _refuse(str(exc), 404 if "no such session" in str(exc) else 403)
    except ValueError as exc:
        return _refuse(f"bad size: {exc}", 400)
    return jsonify({"ok": True, "rows": s.screen.rows, "cols": s.screen.cols})


@bp.post("/terminal/<key>/close")
def terminal_close(key):
    try:
        owner = _guard_post()
        s = get_session(owner, key)
    except guard.WriteRefused as exc:
        return _refuse(str(exc))
    except TerminalRefused as exc:
        return _refuse(str(exc), 404 if "no such session" in str(exc) else 403)
    s.close(hard=(request.form.get("hard") == "1"))
    return jsonify({"ok": True})


@bp.get("/terminal/<key>/screen")
def terminal_screen(key):
    """The whole transport. Plain `fetch`, one JSON body, and a BOUNDED wait.

    `wait` is honoured only when this server is threaded (`wsgi.multithread`). Under
    `flask run --without-threads` a blocking poll would stall the entire console for the duration,
    so it returns immediately instead and the client falls back to a fast poll. That is measured
    off the WSGI environment rather than assumed from the launcher.
    """
    reason = request_reason()
    if reason:
        return _refuse(reason)
    try:
        s = get_session(guard.session_id(), key)
    except TerminalRefused as exc:
        return _refuse(str(exc), 404)
    try:
        rev = int(request.args.get("rev") or -1)
        have = int(request.args.get("have") or 0)
        wait = float(request.args.get("wait") or 0)
    except ValueError:
        rev, have, wait = -1, 0, 0.0
    if wait > 0 and request.environ.get("wsgi.multithread"):
        s.wait_for_change(rev, min(wait, MAX_WAIT_SECONDS))
    if rev >= 0 and s.rev == rev:
        return jsonify({"ok": True, "changed": False, "rev": rev,
                        "alive": s.exit_code is None, "exit_code": s.exit_code})
    return jsonify({"ok": True, "changed": True, "screen": s.snapshot(have)})


def register(app) -> None:
    """The one line `web/app.py` carries, and WHAT IT REGISTERS DEPENDS ON THE BIND.

    Off loopback the pty routes ARE NOT THERE -- not hidden, not disabled, absent -- so
    `test_allowlist.py`'s question, what does this console expose, has a smaller answer in that
    configuration. That is the honest shape of "loopback only": a guard that can be reasoned about
    from the route table rather than from reading every handler.

    The PAGE is registered either way, and it renders the reason. A 404 there would leave the
    operator with a dead nav entry and no sentence explaining which of the four conditions he
    tripped, which is how a security posture turns into a bug report.
    """
    if config_reason():
        app.register_blueprint(bp_page_only)
        return
    app.register_blueprint(bp)
