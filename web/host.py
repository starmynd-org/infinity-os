"""WHERE THIS CONSOLE IS SERVED. One file, four values, and one of them is the line V7b changes.

Task 0168 (V8b). Before this file the console's address was spelled in five places -- the bind in
`web/bin/console`, the port in `web/bin/phone-reach.sh`, the base URL in `web/bin/shots.sh` and
again in `web/tests/test_browser.py`, and, invisibly, the ONE ORIGIN the write door accepts, which
was not spelled anywhere because it was derived from whatever `Host` header arrived. Moving the
console to the tailnet meant finding all five. That is a port, not a config change, and the V8b
brief asked for the opposite.

WHAT THE MOVE ACTUALLY BREAKS, AND IT IS NOT THE BIND. Measured 2026-08-18, in process, four
cases, nothing written:

    loopback,  Host 127.0.0.1:3103        Origin http://127.0.0.1:3103        PASSES the gate
    shape A,   Host <peer>.ts.net         Origin https://<peer>.ts.net        403 cross-origin
    shape A,   Host 127.0.0.1:3103        Origin https://<peer>.ts.net        403 cross-origin
    shape B,   Host 100.64.0.10:3103   Origin http://100.64.0.10:3103   PASSES the gate

`guard.check_origin` compares the browser's `Origin` against `urlsplit(request.host_url)`, and
under `tailscale serve` the TLS ends at the proxy, so `request.host_url` is **http** while the
phone's `Origin` is **https**. The scheme differs even when the host does not, and it differs in
both proxy variants -- whether Serve preserves the inbound `Host` or rewrites it to the backend.
So under shape A, which is the shape V8a recommends and the ONLY one that gives the phone a
secure context (`getUserMedia`, `crypto.subtle`, service workers), the console renders perfectly
on the phone and **every single button returns 403**. A surface that looks finished and cannot
write is worse than one that is visibly not there.

THE ONE LINE. Set `CONSOLE_ORIGIN` to the origin the BROWSER shows in its address bar. Nothing
else in this repo needs to change for shape A:

    CONSOLE_ORIGIN=https://your-server.tailnet-name.ts.net

Under shape B (bind the tailnet address directly, no proxy, no TLS, no secure context) it is two
values instead of one, `CONSOLE_HOST` and `CONSOLE_ORIGIN`, because there the socket moves too.

WHY AN ALLOWED ORIGIN AND NOT `ProxyFix`. The werkzeug answer is to trust `X-Forwarded-Proto` and
rebuild `host_url` from it. That trusts a header the request carries, which is the exact mistake
`web/guard.py` was written to undo -- an allowlist keyed on an attacker-supplied string is an
allowlist keyed on nothing. This value comes from the environment the operator started the
process in and can never come from a request. It also stays honestly narrow: it adds ONE origin,
it does not disable the check, and every other defence is untouched (the room-scoped CSRF token,
the session binding, `SameSite=Strict`).

AND THERE IS A SECOND REASON, WHICH IS THE LOAD-BEARING ONE, AND UNTIL 2026-09-15 IT WAS WRITTEN
DOWN NOWHERE. `CONSOLE_ORIGIN` DOES TWO JOBS ON ONE VALUE:

    1. `guard.py:_accepted()` splices `extra_origins()` into the write door's allowlist, which is
       everything above.
    2. `terminal.py:config_reason()` filters `extra_origins()` for an origin whose HOST IS NOT
       LOOPBACK, and any hit SWITCHES THE UNAUTHENTICATED BROWSER PTY OFF.

Job 2 is not a side effect, it is the mechanism: a declared non-loopback origin is how this file
tells the rest of the console that a proxy or `tailscale serve` is in front of it, and a phone on
the tailnet reaching a DASHBOARD was lane D's answer while a phone on the tailnet reaching a SHELL
is a separate question that is open with the operator
(`outputs/2026-08-29-commander/lane-B3-phone-proposal.md`).

So `ProxyFix` does not merely trust the wrong string. It makes the write door pass WITH NO
`CONSOLE_ORIGIN` SET AT ALL -- and `terminal.py` then sees no declared remote origin and LEAVES THE
PTY ON, over the tailnet. The console registers an unauthenticated pty, so that is a shell for
anyone who reaches the port. It would arrive as the side effect of a correct-looking patch to the
403, with nobody deciding it and no moment at which it could have been argued.

MEASURED 2026-09-15, `guard.check_origin` driven in process, four cases, nothing written: shape A
with `CONSOLE_ORIGIN` unset REFUSES; declared, it PASSES; a foreign `Origin` under that same
config still REFUSES, which is what makes the second result a narrowing and not a hole; loopback
with nothing declared is unchanged. In the same run `terminal.config_reason()` is empty (pane ON)
with nothing declared and names the declared origin (pane OFF) with it set.

IF YOU EVER DO NEED A PROXY ANSWER THAT IS NOT THIS ONE, the thing to check is the COUPLING and
not the 403: whatever you build must still give `terminal.py` a way to know a proxy is in front,
or it must switch the pane off itself. `web/tests/test_terminal.py` (the `a proxy declared` case)
and `web/tests/test_phone_surface.py` pin both halves and are the suites that would tell you.

DEFAULT IS TODAY. `CONSOLE_ORIGIN` unset means `extra_origins()` is empty and `check_origin`
behaves exactly as it did before this file existed. Nothing about the local console changes.
"""

from __future__ import annotations

import os

# --------------------------------------------------------------------------- the four values

#: The socket. Loopback is the sanctioned default and `web/bin/phone-reach.sh` calls anything
#: wildcard a defect: `0.0.0.0` publishes an unauthenticated console onto the operator's home
#: wifi, where the tailnet is not the boundary. Under shape A this does NOT change on the move --
#: `tailscale serve` proxies to loopback and the listening socket stays exactly here.
BIND_HOST = os.environ.get("CONSOLE_HOST", "127.0.0.1")

#: 3103 is the console's REGISTERED port, recorded in `your-brain`'s
#: `tools/port-registry.md` before anything bound it. Change the registry first.
BIND_PORT = int(os.environ.get("CONSOLE_PORT", "3103"))

#: THE LINE V7b CHANGES. The origin the phone's browser shows, when that is not the origin this
#: process is listening on -- i.e. whenever a proxy terminates TLS in front of the console. Empty
#: means "no proxy", which is the local console and is the default.
#:
#:     shape A   CONSOLE_ORIGIN=https://<peer>.tailnet-name.ts.net   (bind unchanged)
#:     shape B   CONSOLE_ORIGIN=http://100.x.y.z:3103  plus  CONSOLE_HOST=100.x.y.z
#:
#: Set it to what the ADDRESS BAR says, scheme included, no trailing slash and no path. A value
#: that disagrees with the address bar is a write door that 403s, which is the failure this
#: constant exists to prevent, so `extra_origins()` normalises rather than trusting the spelling.
CONSOLE_ORIGIN = os.environ.get("CONSOLE_ORIGIN", "")

# --------------------------------------------------------------------------- what the guard asks

def extra_origins() -> tuple[str, ...]:
    """The origins the write door accepts BESIDES the one it is listening on. Usually none.

    Returned as `scheme://netloc`, lowercased, no trailing slash, so a comparison against a
    browser's `Origin` header is a string equality and not an exercise in normalisation at the
    call site. `CONSOLE_ORIGIN` may name more than one, comma separated, for the case where the
    same console is reached both through Serve and over loopback on the host itself -- which is
    the operator's laptop the morning after the cutover, and forgetting it would lock him out of
    the console he is sitting in front of.
    """
    out = []
    for raw in CONSOLE_ORIGIN.split(","):
        raw = raw.strip().rstrip("/")
        if not raw:
            continue
        if "://" not in raw:
            # A bare host is the likeliest typo and the one with teeth: it would compare unequal
            # to every real `Origin` header and refuse every write, silently, forever. Refuse the
            # process instead, at import, where it is one line of output rather than a mystery.
            raise ValueError(
                f"CONSOLE_ORIGIN={raw!r} has no scheme. It must be exactly what the browser's "
                f"address bar shows, e.g. 'https://your-server.tailnet-name.ts.net'. A bare "
                f"host matches no Origin header and would refuse every write on the phone."
            )
        scheme, _, rest = raw.partition("://")
        out.append(f"{scheme.lower()}://{rest.lower().split('/')[0]}")
    return tuple(dict.fromkeys(out))


#: Where tools and browser checks should point. Derived, never spelled twice: a screenshot run and
#: a phone check that disagree about the address are two runs measuring two consoles. It is the
#: FIRST declared origin when there is one -- normalised through `extra_origins()` rather than
#: read raw, because `CONSOLE_ORIGIN` may legitimately name two and a comma is not a URL.
BASE_URL = os.environ.get("BASE") or (extra_origins() or (f"http://{BIND_HOST}:{BIND_PORT}",))[0]


def describe() -> str:
    """One line, for a boot log or a report. Says the bind and says whether a proxy is declared."""
    extra = extra_origins()
    return (f"console bind {BIND_HOST}:{BIND_PORT} · base {BASE_URL} · "
            f"accepted origins besides the bind: {', '.join(extra) if extra else 'none'}")


if __name__ == "__main__":                                              # pragma: no cover
    print(describe())
