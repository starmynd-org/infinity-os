"""What makes the room's identity real, rather than a string the request brought with it.

`rooms.py` is a correct allowlist and it swept clean on its own terms: every registered verb was
refused from `room='study'`, with `store.apply` never entered. It was keyed on a value read out of
`request.form`. So a POST that said `room=queue`, sent with `Referer: /study` and
`Origin: http://evil.example`, got the Queue's whole verb set and ran `reopen` on a live task
(measured 2026-08-16, task 0119 item 1). **An allowlist keyed on an attacker-supplied string is an
allowlist keyed on nothing.**

Three things replace that one string, and none of them is a field the request can fill in:

1. **The room comes from the ROUTE.** `POST /<room>/act`. Still one write door -- one rule, one
   view function, one gate -- but the room is a URL segment Flask parsed, not a form value the
   handler read. This alone is not enough: a request can be aimed at any URL. It is what makes the
   other two possible, because now the token and the room can be checked against each other.

2. **The CSRF token is SCOPED TO A ROOM.** A room page renders a token minted for that room and
   bound to this browser's session cookie. The Study page's token is a Study token. Presenting it
   at `/queue/act` is refused, so "a request originating from Study" is a fact about the token it
   carries rather than a fact about a header anyone can set.

3. **Origin.** Checked when it is there, with `Referer` as the fallback, and a POST carrying
   neither is refused. A browser sends one of the two on every form post and every `fetch`; a
   thing that sends neither is not the console.

The secret is a process secret by default. Restarting the console invalidates every outstanding
token, which costs a page reload and is the correct direction to fail: a console whose tokens
outlive a restart needs somewhere durable to keep a signing key, and this surface holds no state.
Set `CONSOLE_SECRET` when that trade stops being the right one.
"""

from __future__ import annotations

import hmac
import os
import secrets
import time
from hashlib import sha256
from urllib.parse import urlsplit

from flask import g, request

from . import host

#: Tokens age out. Long enough that a console left open over lunch still works, short enough that
#: one scraped out of a screenshot is not a standing key.
TOKEN_TTL_SECONDS = 12 * 3600

#: `issued` is rounded DOWN to this, which makes a token a pure function of (room, session,
#: bucket) instead of a fresh string on every render.
#:
#: This is not a nicety. The console polls `/api/patch/<room>` every three seconds and swaps the
#: regions whose markup changed; the queue's `list` region carries nine of these tokens, so a
#: token minted per render made that region's payload different on every poll of a page where
#: nothing had happened. Measured 2026-08-16 (task 0157): after every other cause was fixed, two
#: patch payloads four seconds apart were byte-identical in all nine regions once the token values
#: were normalised out, and differed in `list` when they were not. A repaint destroys any panel
#: the operator has open, so a nonce that changes for no reason was costing him the screen.
#:
#: It cannot weaken anything. The room, the session binding, the secret and the HMAC are
#: untouched, and `check_token` reads age as `now - issued`: rounding `issued` down can only make
#: a token look OLDER than it is, so it expires EARLIER. The effective life is 11-12h rather than
#: exactly 12h, which is the direction to err in.
TOKEN_BUCKET_SECONDS = 3600

SESSION_COOKIE = "vd_sid"

_SECRET = (os.environ.get("CONSOLE_SECRET") or "").encode() or os.urandom(32)


class WriteRefused(PermissionError):
    """The request may not write here. Raised before the room, the verb or the row is considered.

    Separate from `rooms.RoomRefusal` on purpose: that one means "this room may not call that
    verb", and this one means "I do not accept that you are this room". Collapsing them would
    report an identity failure as a policy decision.
    """


def session_id() -> str:
    """This browser's id, minted on first sight and carried in an HttpOnly cookie.

    It identifies nobody and authorises nothing. Its only job is to be the thing a token is bound
    to, so a token minted for one browser cannot be replayed from another.
    """
    if not hasattr(g, "_vd_sid"):
        sid = request.cookies.get(SESSION_COOKIE) or ""
        g._vd_sid_new = not sid
        g._vd_sid = sid or secrets.token_urlsafe(24)
    return g._vd_sid


def token_for(room: str) -> str:
    """Mint a token for one room. Rendered into that room's forms and nowhere else.

    Stable within `TOKEN_BUCKET_SECONDS`, for the reason stated on that constant: the same page
    rendered twice by two polls must produce the same bytes, or the poll repaints a screen on
    which nothing happened.
    """
    issued = int(time.time()) // TOKEN_BUCKET_SECONDS * TOKEN_BUCKET_SECONDS
    return f"{room}.{issued}.{_sign(room, issued, session_id())}"


def _sign(room: str, issued: int, sid: str) -> str:
    msg = f"{room}\n{issued}\n{sid}".encode()
    return hmac.new(_SECRET, msg, sha256).hexdigest()


def check_token(room: str, token: str) -> None:
    """Refuse anything that is not a live token minted for THIS room and THIS browser."""
    if not token:
        raise WriteRefused(
            f"this POST carries no CSRF token. The {room} room's write door only accepts a token "
            f"the {room} page issued, which is what stops another page relabelling itself as this "
            f"one."
        )
    parts = token.split(".")
    if len(parts) != 3:
        raise WriteRefused("malformed CSRF token.")
    tok_room, issued, mac = parts
    if tok_room != room:
        raise WriteRefused(
            f"this token was issued to the {tok_room!r} room and was presented at the {room!r} "
            f"room's write door. The room a request acts in is not a label it chooses: it is the "
            f"page the token came from. Refused."
        )
    try:
        issued_at = int(issued)
    except ValueError:
        raise WriteRefused("malformed CSRF token.") from None
    if not hmac.compare_digest(mac, _sign(tok_room, issued_at, session_id())):
        raise WriteRefused(
            "this CSRF token was not issued to this browser, or was not issued by this console. "
            "Reload the page."
        )
    age = int(time.time()) - issued_at
    if age > TOKEN_TTL_SECONDS or age < -60:
        raise WriteRefused(f"this CSRF token is {age}s old and they last {TOKEN_TTL_SECONDS}s. "
                           f"Reload the page.")


def _accepted() -> tuple[str, ...]:
    """The origins this door opens for: the one it is listening on, plus any DECLARED in config.

    Task 0168 (V8b), and the second entry is not a nicety. `request.host_url` is built from the
    `Host` header, so its scheme is whatever THIS process is speaking -- `http`. Put
    `tailscale serve` in front to give the phone a trustworthy origin and the browser now sends
    `Origin: https://<peer>.ts.net` while `host_url` still says `http://…`. The SCHEME differs
    even when the host does not, in both proxy variants, so every write from the phone is refused
    403 with the surface rendering perfectly. Measured, four cases, `web/host.py` carries the
    table and the reasoning.

    The declared origin comes from the environment the process was started in and can never come
    from a request, which is the distinction this whole module exists to hold. `ProxyFix` and
    trusting `X-Forwarded-Proto` would put the answer back in the attacker's hands.
    """
    here = urlsplit(request.host_url)
    return (f"{here.scheme}://{here.netloc}", *host.extra_origins())


def check_origin() -> None:
    """Same-origin or nothing. `Origin` first, `Referer` as the fallback, neither is a refusal."""
    accepted = _accepted()
    origin = request.headers.get("Origin")
    if origin:
        there = urlsplit(origin)
        if f"{there.scheme}://{there.netloc}" not in accepted:
            raise WriteRefused(
                f"cross-origin POST from {origin!r}. This console's write door serves "
                f"{' and '.join(accepted)}, and it does not take writes from another. If the "
                f"console is behind a proxy, the origin the BROWSER shows goes in "
                f"CONSOLE_ORIGIN (web/host.py)."
            )
        return
    referer = request.headers.get("Referer")
    if referer:
        there = urlsplit(referer)
        if f"{there.scheme}://{there.netloc}" not in accepted:
            raise WriteRefused(f"cross-origin POST, Referer {referer!r}.")
        return
    raise WriteRefused(
        "this POST carries neither Origin nor Referer. A browser sends one of the two on every "
        "form post and every fetch, so a request with neither did not come from the console, and "
        "the one write door does not guess."
    )


def guard_write(room: str, form) -> None:
    """Everything that must be true before the allowlist is even consulted.

    Order matters and it is the order below: origin, then token, then the payload's own opinion of
    which room it is in -- which is now only ever a stale page to be caught, never an input.
    """
    check_origin()
    check_token(room, (form.get("csrf") or "").strip())
    claimed = (form.get("room") or "").strip()
    if claimed and claimed != room:
        raise WriteRefused(
            f"this POST is at the {room!r} room's door and its payload says {claimed!r}. The "
            f"payload does not decide, and a page still sending a room field is a stale page: "
            f"reload it."
        )


def attach(app) -> None:
    """Wire the session cookie and give templates the one function they need."""
    app.jinja_env.globals["csrf_for"] = token_for

    @app.after_request
    def _set_session_cookie(response):
        if getattr(g, "_vd_sid_new", False) and hasattr(g, "_vd_sid"):
            # SameSite=Strict is a second, independent brake: it means a browser will not attach
            # this cookie to a cross-site POST at all, so the token check never even runs for one.
            response.set_cookie(SESSION_COOKIE, g._vd_sid, httponly=True, samesite="Strict",
                                max_age=TOKEN_TTL_SECONDS, path="/")
            g._vd_sid_new = False
        return response
