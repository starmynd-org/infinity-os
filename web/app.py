"""The console: one shell, five rooms, and one write door.

Every state change on this surface goes through `POST /<room>/act`, which calls `rooms.dispatch`,
which calls `store.apply`. That is ONE route rule and ONE view function, not five: a genuinely
per-room endpoint is how the allowlist becomes advisory, because the fifth one written in a hurry
is the one that forgets to check.

**The room is a URL segment, never a form field.** It used to be `request.form["room"]`, and a
POST that said `room=queue` from a page at `/study` got the Queue's verbs and ran `reopen` on a
live task (measured 2026-08-16). The allowlist was not the hole; the room's identity was. What
makes the route-derived room mean something is `web/guard.py`: a CSRF token scoped to the room
that issued it, plus an Origin check, so the room a request acts in is the page its token came
from rather than a string it brought along.

**No SSE, no websockets, no cache layer.** The client polls `GET /api/patch/<room>` every three
seconds and swaps the regions whose HTML changed, skipping any region holding the focused input.
Poll-and-patch is on the must-build side of the same list that forbids SSE, and it is also the
only one of the two that cannot leave a half-open connection behind on a laptop that sleeps.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import traceback
from contextlib import nullcontext
from threading import RLock

from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_file, url_for

import store
from store import reads as R

from pathlib import Path

from . import (actions, guard, images, live_session, model, rooms, runfeed, scoping,
               sessions, terminal, chats)
from .glyph import colour_for, glyph
from .views.blueprint import build as build_attention

# `web/__init__.py` put `engine/` and `queue/` on the path, before this module or `model` ran.
# The spelling of those packages is load-bearing and the reason is stated there.
#
# The imports below are not unused: registration is an import side effect, and a process that
# skips them has an empty registry. The MCP server shipped without them once and every write tool
# failed while every read worked.
from swarm_engine import accept as _engine_accept                        # noqa: F401,E402
from swarm_engine import transitions as _engine_transitions              # noqa: F401,E402
# V4's two: `steer take`/`steer release` and `agent config set`. Registration is an import side
# effect, and the module comment above says what a process that skips one gets.
from swarm_engine import fleet_config                                    # noqa: E402
from swarm_engine import steering                                        # noqa: E402
from fabric import emit as _fabric_emit                                  # noqa: F401,E402
try:
    from human_queue import transitions as _queue_transitions            # noqa: F401,E402
except ImportError:                                                      # pragma: no cover
    _queue_transitions = None      # D6b's lane is absent; the controls fall back and say why

# WHICH HUMAN THIS CONSOLE IS. Lane E, row 0384.
#
# `CONSOLE_OPERATOR` was one env var read here and passed to every action as a NAME. Under one
# human that was harmless: there was one credential, one name, and they agreed by construction.
# Under row 0386 decision 2 there are up to twelve human logins and the name and the credential
# are two different facts, so the console has to resolve the name from the same place
# `store.apply` resolves the credential, or the two drift and only the database notices.
#
# `store.human_slug()` is that one place. `CONSOLE_OPERATOR` is still honoured and is now the
# console's spelling of the explicit rung; unset, it falls to `$BRAIN_HUMAN` and then to
# `operator`, so a single-human host is unchanged.
OPERATOR = store.human_slug(os.environ.get("CONSOLE_OPERATOR") or None)

# AND THE PROCESS IS THAT HUMAN, which is one line rather than a kwarg on twenty call sites.
#
# The identity policy's grain is per PROCESS, because a credential is per process. Without this
# line the console has TWO answers: `OPERATOR`, which every action passes as `by=`, and whatever
# `store.human_slug()` resolves inside `apply()`, which reads `$BRAIN_HUMAN`. They agree today
# only because both fall back to `operator`, and they stop agreeing the moment somebody sets
# `CONSOLE_OPERATOR` without `BRAIN_HUMAN`. The failure would be a decider mismatch on every
# write, which reads as a permissions problem and is a configuration one.
#
# Assignment rather than setdefault, deliberately: `CONSOLE_OPERATOR` is this surface's explicit
# rung and outranks the session's. When it is unset, `OPERATOR` already IS what `$BRAIN_HUMAN`
# said, so this is a no-op.
os.environ["BRAIN_HUMAN"] = OPERATOR
RECEIPT_SECONDS = 7.0

#: The deep header's `since` clock, and the whole of what this console will accept as one.
#: V-SHELL-2 (task 0176): `io_deep_since` is a cookie, a cookie is operator-writable, and this
#: value is rendered into the shell chrome of every room. Anything that is not exactly four
#: digits around a colon is discarded and the header says `since --` instead -- the same branch
#: a missing cookie takes, so a tampered clock and an absent one are one code path, not two.
_HHMM = re.compile(r"[0-2][0-9]:[0-5][0-9]")

# Ephemeral, in memory, seven seconds. This is the ONLY console state that is not read from the
# store, and it is a rendering artifact of the last action rather than a fact about the world:
# every receipt describes a row that is already committed. It lives here rather than in the store
# because a table whose rows are meaningful for seven seconds is not a table.
_RECEIPTS: list[dict] = []
_RECEIPT_LOCK = RLock()
_ACCEPTANCE_LOCK = RLock()


def _receipts() -> list[dict]:
    with _RECEIPT_LOCK:
        now = time.time()
        live = [r for r in _RECEIPTS if now - r["at"] < RECEIPT_SECONDS]
        _RECEIPTS[:] = live
        return [dict(r) for r in reversed(live)]


def _push_receipt(out: dict) -> None:
    with _RECEIPT_LOCK:
        verb = out.get("verb")
        subject_id = out.get("subject_id")
        if subject_id is None and verb == "accept work":
            subject_id = (out.get("undo") or {}).get("id")
        if (verb in ("accept work", "unaccept work") and subject_id is not None
                and out.get("subject_type", "work_item") == "work_item"):
            for receipt in _RECEIPTS:
                undo = receipt.get("undo") or {}
                if undo.get("action") == "unaccept" and str(undo.get("id")) == str(subject_id):
                    receipt["undo"] = None
                    receipt["no_undo_reason"] = (
                        "Acceptance withdrawn; this inverse has been used."
                        if verb == "unaccept work" else
                        "Superseded by the newer acceptance shown here.")
        _RECEIPTS.append({
            "at": time.time(), "text": out["receipt"], "undo": out.get("undo"),
            "no_undo_reason": out.get("no_undo_reason"), "note": out.get("note"),
        })


def _perform_with_receipt(room: str, action: str, item_id: str, text: str) -> dict:
    # This process owns this ephemeral stripe. Keep acceptance transitions and their
    # publication in order; an older withdrawal must not retire a newer acceptance.
    # Other verbs and receipt reads do not wait on the acceptance transaction.
    with _ACCEPTANCE_LOCK if action in ("accept_work", "unaccept") else nullcontext():
        out = _perform(room, action, item_id, text)
        _push_receipt(out)
        return out


# =============================================================================================
# V-SHELL-3 (task 0177): THE STACK SURFACE. DS section 5, which adopts V10-A SPEC sections 3-7.
#
# Everything below is ADDITIVE. It adds one module-level helper and one block of keyword
# arguments to `stack()`; it renames nothing, removes nothing and adds no route, and that last
# one is a design decision rather than a shortage of ideas.
#
# THE PRESS-TO-READABLE BUDGET IS 250ms AND THE STACK ADVANCES BY NAVIGATING. Those two
# sentences are in tension, and the resolution is that the client PREFETCHES the next document
# while the operator is still reading the current card, then swaps the card node in from the
# document it already holds. That needs no fragment route: the next document is exactly what a
# navigation would have produced, so the prefetch and the fallback navigation render the same
# server truth from the same route, and the fallback is what runs when the prefetch has not
# landed yet. A fragment endpoint would have been a SECOND renderer of the same card, and two
# renderers of one card is how the fast path and the slow path start disagreeing.
#
# The same fetch is what the interruption rule (DS 5.2) rides: the client re-reads this route
# every three seconds and patches the header counts, the pip row and the damage line ONLY,
# never `.stackcard`. Repaint safety is therefore structural rather than careful.
# =============================================================================================


def _stack_view(items: list[dict], n: int, d: int, t0: int) -> dict:
    """The pip vocabulary (DS 5.4), the header counts (DS 6.2) and the damage line (DS 5.2).

    `n` is the skip cursor and `d` the resolve cursor, both already on the URL. `t0` is the
    denominator the run was ENTERED at, and it is what makes `+N arrived` a fact rather than a
    guess: without it the server can see that the stack got bigger but not that it got bigger
    *during this run*. It rides the URL for the same reason `n` and `d` do -- the poll is off on
    this surface by design, so the cursor has nowhere else to live.

    THE ORDER OF THE ROW IS THE ORDER OF THE RUN, and skipped pips sit at the back because that
    is where the items themselves went. `skip` cycles to the back of the list (the route's own
    docstring says why it is not a defer), so a skipped pip drawn in place would be the one pip
    in the row that lies about where its item is.
    """
    live = len(items)
    total = max(live + n + d, 1)
    k = (n % live) if live else 0
    # The run order seen from the current card: current first, then what is still ahead, then
    # what has already been cycled past.
    order = (items[k:] + items[:k]) if live else []
    ahead = order[1:live - k] if live else []
    skipped = order[live - k:] if live else []
    arrived = max(0, total - t0) if t0 else 0

    def _upcoming(it: dict) -> str:
        # Colour is never the only carrier (DS 5.4): external is HOLLOW, burning is LARGER.
        # Both facts are also text on the item's own card when it arrives, and both are read off
        # fields the card already carries -- no schema change, V10-A section 11.
        if any(str(g).lower() == "external" for g in (it.get("gates") or [])):
            return "up ext"
        if it.get("live"):
            return "up burn"
        return "up"

    pips = [{"cls": "done"} for _ in range(d)]
    if live:
        pips.append({"cls": "cur"})
        pips += [{"cls": _upcoming(i)} for i in ahead]
    # `arriving` is the trailing edge of what is ahead: the pips the run did not start with.
    if arrived:
        for pip in pips[-arrived:] if arrived <= len(pips) else pips:
            if pip["cls"].startswith("up"):
                pip["cls"] += " arriving"
    pips += [{"cls": "skip"} for _ in skipped]
    waiting = len([i for i in ([order[0]] + ahead if live else []) if i.get("live")])
    return {"pips": pips, "arrived": arrived, "waiting": waiting,
            "ahead": ahead, "skipped": skipped, "total": total}


def _stack_damage() -> dict:
    """DS 5.2 class 3. One persistent line, word and count and link, never a card, never a modal.

    A dead agent is the damage this surface can actually observe: `model.fleet()` already types
    an agent that has been `working` for more than fifteen minutes as `dead`, and that is a
    fleet fact rather than a queue fact, which is why the line links to Fleet and not to a card.
    Zero dead agents renders NO line at all -- an empty damage bar is a bar that teaches the eye
    to skip the place damage will appear.
    """
    try:
        dead = [a for a in model.fleet()["agents"] if a["state"] == "dead"]
    except Exception:                                                   # noqa: BLE001
        return {}
    if not dead:
        return {}
    return {"n": len(dead), "word": "broken", "where": "fleet", "href": "/fleet"}


def _stack_external(item: dict | None) -> bool:
    """DS 5.6 / V10-A 6.4: the item whose resolution leaves this system and has no inverse."""
    if not item:
        return False
    return any(str(g).lower() == "external" for g in (item.get("gates") or []))


def _announce_who_this_console_is() -> dict:
    """Say WHO this console is, once, at startup, and say so loudly when it is nobody.

    Lane E, row 0384. The failure this closes is invisible with one human and certain with two:
    a console configured for a human whose credential the host does not hold starts cleanly,
    renders that name on every card, and then refuses every write for the rest of the evening,
    one refusal at a time, with a message about a decider mismatch. **A configuration error that
    presents as a permissions error is the worst failure available here**, because the reader's
    first move is to widen a permission.

    It PRINTS and does not raise. A console that will not start is worse than one that can read
    but not write: the reads are how the operator finds out what is going on, and `store.whoami`
    fails soft for the same reason.
    """
    who = store.whoami(OPERATOR)
    if who["agrees"]:
        print(f"console: writing as {who['human']!r} (login {who['login']}), which "
              f"brain.current_human() confirms", flush=True)
    else:
        print(f"console: THIS PROCESS CANNOT WRITE AS {OPERATOR!r}. {who['reason']}\n"
              f"console: reads work; every human-attributed write will be refused. Provision "
              f"the credential (`swarm admin human provision {OPERATOR}`, or "
              f"store/bin/provision-operator.sh for the operator) or set CONSOLE_OPERATOR to "
              f"the human this host actually holds.", flush=True)
    return who


# ---- WHAT THIS PROCESS IS SERVING. Task 0427.
#
# A `flask run` process serves the Python it imported at startup for the rest of its life.
# `jinja_env.auto_reload` below reloads TEMPLATES and has never reloaded Python, and the two go
# stale INDEPENDENTLY. Measured 2026-08-28 across four live consoles: the one on 3103 rendered a
# nav added to `base.html` after it started, while `GET /live` still returned 404 because that
# route arrived later. Two consoles up six hours served stale markup as well.
#
# **That produces a FALSE NEGATIVE, which is the expensive direction.** A lane that fixes
# something, curls a long-lived console, and sees the defect unchanged concludes its own fix
# failed, and may revert a correct change or go looking for a mechanism that is not there. Two
# agents were warned mid-flight for exactly this on 2026-08-28.
#
# Restarting the console is the workaround and not the fix, because it only helps someone who
# already suspects. This is the fix: **the process states what it is serving, so a stale one is
# visible rather than silent.** Identity rides every response as a header and costs nothing, being
# read once at startup. The per-file verdict is on `/api/health` and deliberately NOT on every
# response: the sweep stats 62 files and measured ~70 ms on this 9p mount, while the client polls
# every three seconds. That split is why this is not the cache layer item 11 forbids -- nothing
# rendered is stored or replayed, and the only thing held between requests is three constants.


def _serving_source_files() -> list[Path]:
    """Every repo file whose contents THIS PROCESS froze, plus the templates it renders.

    Derived from `sys.modules` rather than from a glob of `web/`, because the console imports
    `store`, `swarm_engine`, `human_queue` and `fabric`, and a lane fixing any of those is misled
    by exactly the same stale process. A hand-written glob would answer "not stale" while the
    module the lane just edited sat frozen in memory, which is the false negative this exists to
    close, restated one directory over.

    `web/static` is excluded on purpose and not by oversight: Flask reads those from disk on every
    request, so they are never stale.
    """
    root = Path(__file__).resolve().parent.parent
    found: set[Path] = set()
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if not path:
            continue
        try:
            resolved = Path(path).resolve()
            resolved.relative_to(root)
        except (ValueError, OSError):
            continue                      # site-packages and stdlib are not this repo's to watch
        found.add(resolved)
    found.update(p for p in (root / "web" / "templates").glob("*.html") if p.is_file())
    return sorted(found)


def _serving_mtimes() -> dict[str, int]:
    """Path -> mtime_ns. A file that cannot be stat-ed is OMITTED, so it reads as removed."""
    out: dict[str, int] = {}
    for path in _serving_source_files():
        try:
            out[str(path)] = path.stat().st_mtime_ns
        except OSError:
            continue
    return out


def _serving_commit() -> str | None:
    """The commit checked out right now, or None.

    NEVER raises. A console that refused to start because `git` was missing would be a worse
    failure than a console that does not know its commit, which is the same reason
    `_announce_who_this_console_is` prints instead of raising. No dirty flag is reported: this
    repo carries `outputs/` untracked at all times, so `git status` here says "dirty" every day
    of the week and a flag that is always on is a flag nobody reads.
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parent.parent), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None


def _serving_report(commit: str | None, started_at: float, at_start: dict[str, int]) -> dict:
    """Does what this process holds in memory still match what is on disk?

    **The denominator is printed and never assumed.** `files_compared` is the number of files this
    sweep actually stat-ed, and zero makes the verdict `unknown` rather than `no`: a sweep that
    found none of the files it started with has measured nothing, and "not stale" over an empty
    set is not a pass. That is the failure shape this whole surface exists against, so it would be
    a poor thing to rebuild inside the instrument.
    """
    now = _serving_mtimes()
    compared = sorted(set(now) & set(at_start))
    changed = [p for p in compared if now[p] != at_start[p]]
    added = sorted(set(now) - set(at_start))
    removed = sorted(set(at_start) - set(now))
    commit_now = _serving_commit() or "unknown"
    short = [str(Path(p).name) for p in changed]
    report = {
        "pid": os.getpid(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
        "uptime_seconds": int(time.time() - started_at),
        "commit_at_start": commit or "unknown",
        "commit_now": commit_now,
        "files_compared": len(compared),
        "changed_since_start": short,
        "added_since_start": [Path(p).name for p in added],
        "removed_since_start": [Path(p).name for p in removed],
    }
    if not compared:                                                            # DENOMINATOR
        report["stale"] = "unknown"
        report["reason"] = (
            "0 files compared. This sweep found none of the files it started with, so it "
            "measured nothing; a verdict over an empty set is not a pass.")
        return report
    moved_commit = bool(commit) and commit_now not in ("unknown", commit)
    report["stale"] = "yes" if (changed or added or removed or moved_commit) else "no"
    # Named separately because it is the half `auto_reload` CANNOT repair. Templates can be
    # perfectly fresh in a process whose routes and helpers are hours old, which is precisely
    # what 3103 was doing when this was measured.
    # A MODULE IMPORTED LATE IS NOT A STALE MODULE, AND COUNTING IT AS ONE MADE THIS INSTRUMENT
    # CRY WOLF. Measured 2026-09-01 on a console started minutes earlier with no source edited:
    # `added_since_start: ["admin.py", "config.py"]` and `python_stale: yes`. Both are lazily
    # imported on a route this console had not served at startup, so `sys.modules` grew and the
    # sweep read growth as decay. THEY WERE READ FROM DISK AFTER THE PROCESS BOOTED, which makes
    # them the FRESHEST code in the process, not the stalest.
    #
    # WHY THIS MATTERS MORE THAN A WRONG FIELD. `python_stale` is this repo's first law of
    # measuring -- CLAUDE.md tells every lane to read it before believing a page, and a suite that
    # gates on it (S3's own board suite does) refuses to run when it says yes. An instrument that
    # fires on ordinary lazy imports teaches its readers to skip it, and the one thing it exists
    # to catch is a defect that only shows up when nobody is checking.
    #
    # THE REAL SIGNAL IS NARROWER AND IS KEPT WHOLE: a `.py` whose bytes changed UNDER a process
    # that had already imported it. That is `changed`, and it is untouched below.
    #
    # THE ONE GAP THIS LEAVES, NAMED RATHER THAN LEFT TO BE FOUND: a module imported after boot
    # and THEN edited is in `added` forever and never in `changed`, because `changed` is computed
    # against the startup snapshot and that module was not in it. So an added `.py` is compared
    # against the process start TIME instead: if it was last written before this process booted,
    # it cannot have changed since it was imported and it is fresh. If it was written after, it is
    # treated as stale, which is the safe direction and the one this file already chooses for a
    # sweep that finds nothing.
    late_writes = [p for p in added
                   if p.endswith(".py") and now.get(p, 0) > int(started_at * 1_000_000_000)]
    report["python_stale"] = (
        "yes" if any(p.endswith(".py") for p in changed) or late_writes else "no")
    report["imported_since_start"] = [Path(p).name for p in added if p.endswith(".py")]
    report["remedy"] = None if report["stale"] == "no" else (
        "Restart this console before verifying a fix against it. Template auto-reload never "
        "reloads Python, so a fresh-looking page can still be served by a stale module.")
    return report


def _deep_cookie_on() -> bool:
    """Is the deep-work cookie set for this request? ONE reading, and it is this one.

    Three copies of this line used to exist, spelled identically in `_deep_state`, `_deep_now` and
    `_queue_ctx`, and `_deep_now`'s own docstring recorded that consolidating them was the better
    shape and was being left alone because two of the three were cited by line number in other
    lanes' acceptance records. Those citations are already stale, and the ruling of 2026-08-31
    needed one predicate rather than three, so they are consolidated here.
    """
    return (request.args.get("deep") or request.cookies.get("io_deep") or "0") == "1"


def _deep_applies() -> bool:
    """Whether deep work applies to the surface being rendered RIGHT NOW.

    HIS RULING, 2026-08-31: DEEP WORK DOES NOT APPLY TO THE DECIDE TIER.

    THE DEFECT IT CLOSES IS A MODE LEAKING INTO A TIER THAT NEVER OFFERED IT. `queue.html`
    renders the deep-work toggle behind `{% if tier != 'decide' %}`, so there are ZERO ways to
    turn deep work on from the Decide tier. But `io_deep` is a one-year cookie read on every
    navigation, so turning it on in Judge and then clicking Decide carried it in. Reproduced
    in a browser: toggle on in judge, click to decide, and the page came back with the run
    strip and three cards while the shell said the mode was running.

    WHAT THAT STATE ACTUALLY DID, measured at 1440px rather than reasoned about:

        judge   deep on   tier chips 90->0   run strip 19->0   page 1103px -> 562px
        decide  deep on   tier chips 90->0   run strip 19->19  page  716px -> 773px

    On Judge the mode halves the page. On Decide it removed the navigation and the tier chips,
    left the cards and the run strip exactly where they were, and made the page TALLER. That
    is not a quieter surface, it is the same surface with the exits taken away, and it is the
    page he lands on by default.

    MUST-NOT-BUILD ITEM 9'S CHECK COULD NEVER HAVE CAUGHT IT. `deep_work_absolute` runs on
    `/queue?tier=judge`, where the carve-out does not apply, so the one assertion protecting
    this rule was measuring the arm that was already correct. That is instance eight of the
    pattern `tools/check-at-head.sh` is written about: a check that was green and had never
    run against the case that mattered.

    THE COOKIE IS NOT TOUCHED AND THAT IS DELIBERATE. This decides whether the mode APPLIES to
    this render, not whether it is ON. Visiting Decide must not silently switch deep work off
    for Judge and Shape, so `_mode_cookies` keeps its own reading: it writes only when
    `?deep=` is present, which is the operator actually crossing a threshold.
    """
    if not _deep_cookie_on():
        return False
    # The Decide tier, whether it was named or defaulted to. `/queue` with no tier IS decide.
    if (request.path or "").rstrip("/") in ("/queue", "") and \
       (request.args.get("tier") or "decide") == "decide":
        return False
    return True


def create_app() -> Flask:
    app = Flask(__name__)
    whoami = _announce_who_this_console_is()
    # `image_host` is a CALLABLE and not a value, so the attach form's "absolute path, on X"
    # label is answered by the process that will resolve the path rather than by whatever the
    # host was called when this module was imported. V5 (task 0167).
    app.jinja_env.globals.update(glyph=glyph, colour_for=colour_for, OPERATOR=OPERATOR,
                                 WHOAMI=whoami, image_host=images.host)
    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True
    # Flask caches templates unless debug is on, and debug is off here on purpose: the debugger's
    # console is remote code execution behind a PIN, which is not a thing to run beside a store.
    #
    # THIS SETTING IS NOT SUFFICIENT, which is the point of the block above
    # `_serving_source_files`: even when it works it covers only templates, and the three
    # constants below are how a stale PYTHON process becomes visible instead.
    #
    # **Setting the env attribute alone did not work, and task 0447 found out why.** Assigning
    # `jinja_env.auto_reload` here is undone a moment later by the Flask CLI, which does
    # `app.debug = get_debug_flag()` (`flask/cli.py:369`) AFTER importing this module, and that
    # setter repopulates dependent values (`flask/sansio/app.py:562`):
    #
    #     if self.config["TEMPLATES_AUTO_RELOAD"] is None:
    #         self.jinja_env.auto_reload = value        # value is False; debug is off here
    #
    # So under `flask run` -- and ONLY under `flask run` -- auto-reload was False for the life of
    # the process, and no console had ever reloaded a template at any age. It never reproduced
    # under a probe because importing the app skips `cli.py:369` entirely; that is what made 0427
    # read the failure as a six-hour uptime effect, when a three-minute-old console does it too.
    #
    # The config key is what makes it stick: a non-None value makes the setter's guard skip. Both
    # lines are needed, because that guard only DECLINES to overwrite, it never restores a True.
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True
    serving_started_at = time.time()
    serving_commit = _serving_commit()
    # No served-commit template global: the page footer that read it is removed by Andrew's
    # D-INFINITY-UX-RULINGS-1 (2026-09-14). The commit stays readable where a person checking a
    # build looks: `GET /api/health` (`serving.commit_now`) and the `X-Console-Commit` header.
    serving_mtimes = _serving_mtimes()
    print(f"console: pid {os.getpid()} serving commit {serving_commit or 'unknown'}. This "
          f"process freezes its Python at startup and auto-reload never refreshes it, so before "
          f"you verify a fix against this console read `serving.stale` from GET /api/health.",
          flush=True)

    @app.after_request
    def _say_what_is_being_served(response):
        """Three headers, read from constants, so a page and a 404 both say who served them.

        No filesystem work happens here. The per-file verdict costs a 62-file stat sweep and
        lives on `/api/health`, because this hook runs on the three-second poll as well.
        """
        response.headers["X-Console-Commit"] = serving_commit or "unknown"
        response.headers["X-Console-Started"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(serving_started_at))
        response.headers["X-Console-Pid"] = str(os.getpid())
        return response
    # The session cookie and `csrf_for(room)`. Attached before any route is declared so there is no
    # window in which a write door exists without the thing that decides whose door it is.
    guard.attach(app)

    @app.context_processor
    def _theme():
        """Light by default, per the locked Infinity style guide. `?theme=dark` and `?theme=light`
        are how a screenshot run picks one without a profile.

        THIS FUNCTION DOES NOT CHOOSE THE DEFAULT and never did; it returns None when nobody has
        asked for a theme, and the templates' pre-paint script resolves that None. The default
        lives in one expression, twice: `base.html` and `stack.html`. Said here because this
        docstring read "Dark by default" for a month and was the most quotable statement of a
        default that is actually set somewhere else (W3-UX-INFINITY, 2026-09-15, R21).
        """
        return {"theme": request.args.get("theme") or request.cookies.get("vd_theme") or None}

    @app.context_processor
    def _deep_state():
        """Deep work, for the SHELL rather than for the one room that can toggle it.

        `io_deep` is a one-year cookie and the mode is absolute: it removes the fast pane, the
        burning line and every card outside the tier from the markup. Measured 2026-08-16: its
        state was legible on exactly one of the five places the operator can be, so /brief,
        /fleet, /study and even /queue?tier=decide said nothing while an absolute mode was
        suppressing work. The guide's rule is what permits this rather than forbidding it -- *"A
        count may appear anywhere. Cards may not."* -- so what goes in the chrome is a state word
        and a count, and never an item.

        The count is a CALLABLE and it is cached on `g`, because a context processor runs on
        every render including the three-second poll. `_queue_ctx` has already read the queue on
        the Queue room, so the pill costs that room nothing; on the other four it costs one read,
        and only while deep work is on.
        """
        # `_deep_applies` and not the raw cookie: on the Decide tier the mode does not apply
        # (his ruling, 2026-08-31), and a header that said "deep work on" over a page
        # rendering its nav, its tier chips and its run strip would be two surfaces
        # disagreeing about one mode. The cookie is untouched, so Judge and Shape are
        # still in it.
        on = _deep_applies()

        def deep_count() -> int:
            if not hasattr(g, "_decide_total"):
                g._decide_total = model.queue_view()["totals"]["decide"]
            return g._decide_total

        # ---- V-SHELL-2 (task 0176) EXTENDS THIS PROCESSOR RATHER THAN ADDING A SECOND ONE.
        # `deep_since` is the deep header's clock (DS section 6.3, B section 3.4). It is a FACT
        # AND NOT A SCORE: no target, no streak, no praise, and `since --` when it is absent
        # rather than a guess or a zero.
        #
        # The client writes `io_deep_since` at the moment it writes `io_deep`, in the operator's
        # OWN local time, which is the only clock "since 14:02" can honestly mean on a header he
        # is reading. Deriving it here from a server timestamp would render the VPS's timezone
        # (`web/host.py` documents that this console is reached over Tailscale from elsewhere)
        # and be wrong by hours while looking exact.
        #
        # SANITISED, NOT TRUSTED: a cookie is operator-writable and this value goes into the
        # markup. Anything that is not literally HH:MM becomes None, which renders `since --`
        # through the same branch a missing cookie takes.
        raw_since = request.cookies.get("io_deep_since") or ""
        since = raw_since if _HHMM.fullmatch(raw_since) else None
        # `deep_here` IS THE HALF HIS RULING LEFT UNSAID, and leaving it unsaid was a defect.
        #
        # `deep_on` above is `_deep_applies()`, so on the Decide tier it is False and the shell
        # renders nothing: no `body.deep`, no header, no exit. That is right about the SURFACE
        # and wrong about the STATE. `io_deep` is a ONE-YEAR cookie. Turn deep work on in Judge,
        # click to Decide -- the tier this console lands on by default -- and the mode is running
        # with no indication anywhere and no way to leave it from where he is standing. The next
        # visit to Judge then hides the nav and the cards, and nothing on the surface he came
        # from ever said why.
        #
        # Caught by `test_phone.py:the_mode_is_visible_on_the_tier_a_phone_lands_on` on
        # 2026-08-31, which asserts the mode is nameable on the tier a phone lands on. The check
        # was right and the ruling's implementation was incomplete.
        #
        # THE ARGUMENT AGAINST A HEADER HERE STILL STANDS AND THIS DOES NOT BREAK IT: a header
        # saying "deep work on" over a page rendering its nav, its chips and its run strip would
        # be two surfaces disagreeing about one mode. So this does not claim the mode is running
        # HERE. It states the true sentence -- on, and not applied on this tier -- which is one
        # surface telling the whole truth rather than two telling halves of it.
        return {"deep_on": on, "deep_count": deep_count, "deep_since": since,
                "deep_here": _deep_cookie_on() and not on}

    # ------------------------------------------------- the mode contract (DS sections 3, 4.1)
    #
    # V-SHELL-1, task 0175. Dispatch is the OTHER mode, and it is built here rather than in the
    # one room that can enter it for the same reason deep work is: the strip renders server side
    # in EVERY room (DS:261-263), and a client-only read would leave four of five rooms silent
    # about a mode that is running. `_deep_state` above measured that failure on 2026-08-16 and
    # its docstring carries the reasoning; this block does not re-derive it.
    #
    # THE TWO MODES ARE EXCLUSIVE AND DEEP WINS EVERY CONFLICT (DS:264-266). That is enforced in
    # two places on purpose, because they answer two different questions: `_dispatch_state` makes
    # deep work win the MARKUP (no pill, no strip, nothing to click), and `_mode_cookies` makes it
    # win the STATE, by clearing `io_dispatch` on the response. Markup alone would leave a mode
    # switched on and invisible, which is the shape of a bug that outlives the session it started.

    def _deep_now() -> bool:
        """The deep read, spelled exactly as `_deep_state` (:117) and `_queue_ctx` (:493) spell it.

        A third copy of one line is not free, and consolidating all three into this helper is the
        better shape -- but those two lines are cited by task id and line number in 0171's
        acceptance record and in three lanes' briefs, so moving them is a change to other people's
        citations rather than to this lane's code. Left as a named follow-up, not taken here.
        """
        return _deep_cookie_on()

    @app.context_processor
    def _dispatch_state():
        """Dispatch, for the SHELL: the mode pill in every room and the strip under the header.

        `io_dispatch` is a one-year cookie, values `0|1`, read on the server (DS:261-262). The
        `?dispatch=` query arg overrides it exactly as `?deep=` overrides `io_deep`, so a harness
        can drive the mode without a cookie jar.

        THE COUNT IS A CALLABLE AND IT IS CACHED ON `g`, for `_deep_state`'s reason: a context
        processor runs on every render including the three-second poll. Two callables and not one,
        because they cost differently. `dispatch_count()` reuses the read `_queue_ctx` already
        stashed, so it is free on the Queue; `dispatch_extra()` is a second read that only the
        strip asks for, so it is paid only while the mode is ON and something remains to resume.
        The strip is also gated on `not patch_only` in `base.html`: the header is not a patched
        region, so the poll would be paying for markup it never swaps.
        """
        deep = _deep_now()
        raw = (request.args.get("dispatch") or request.cookies.get("io_dispatch") or "0") == "1"

        def dispatch_count() -> int:
            """Items left to dispatch. The Decide TOTAL, not the window's card count.

            Same value and same cache slot as `deep_count()`, deliberately: two numbers in one
            shell chrome that disagree about how much is waiting is worse than either number.
            """
            if not hasattr(g, "_decide_total"):
                g._decide_total = model.queue_view()["totals"]["decide"]
            return g._decide_total

        def dispatch_extra() -> dict:
            """Burning items and agents waiting -- the strip's other two counts, read once.

            `R.agents()` rather than `model.fleet()`: fleet() reads the whole queue again for its
            `queue_count`, and the strip needs the roster and nothing else.
            """
            if not hasattr(g, "_dispatch_extra"):
                view = model.queue_view()
                g._decide_total = view["totals"]["decide"]
                g._dispatch_extra = {
                    "burning": len([i for i in view["tiers"]["decide"] if i["live"]]),
                    "waiting": len([a for a in R.agents()
                                    if not a["stopped"] and a["status"] == "waiting"]),
                }
            return g._dispatch_extra

        def mode_href(**changes) -> str:
            """This URL with mode args changed, everything else kept.

            The pill is rendered in every room, and `/scope?intent=...` is a room whose query
            string is the screen. Rebuilding the href from `request.path` alone would throw the
            operator's half-typed intent away to turn a mode on.
            """
            from urllib.parse import urlencode
            args = request.args.to_dict()
            for k, v in changes.items():
                args[k] = str(v)
            qs = urlencode(args)
            return request.path + ("?" + qs if qs else "")

        def dispatch_href() -> str:
            """Where the pill's `Dispatch` segment goes (DS:284-286).

            On: it ENDS the mode, in place. Off: it opens the stack, or lands on Decide's empty
            state when there is no stack to open. That branch is the one thing on the pill that
            costs a read, which is why it is behind the `g` cache and behind `not patch_only`.
            """
            if raw and not deep:
                return mode_href(dispatch=0)
            if dispatch_count():
                return "/queue/stack?from=decide&dispatch=1"
            return "/queue?tier=decide&dispatch=1"

        # `raw and not deep` is the exclusivity rule in the markup: in deep work the Dispatch
        # control is not rendered at all (DS:265), so `dispatch_on` is false there and both the
        # pill and the strip fall out of `base.html` by the same `{% if %}` that governs them
        # everywhere else. Absence, never `display:none` (operator ruling q0146, 2026-08-18).
        return {"dispatch_on": raw and not deep, "dispatch_count": dispatch_count,
                "dispatch_extra": dispatch_extra, "dispatch_href": dispatch_href,
                "mode_href": mode_href}

    @app.after_request
    def _mode_cookies(resp):
        """The one place either mode cookie is WRITTEN by the server.

        `io_deep` is ALSO written by `console.js`, at the moment the operator clicks, and the two
        writes are not a duplication -- they answer different questions and V-SHELL-2 (task 0176)
        added the server half here rather than in a second `after_request`:

          * the CLIENT write is what makes the cookie land AT INITIATION (DS:301, B section 4.5).
            It happens before the door animates and before the navigation is issued, so a request
            made during the 560ms already reads the new state. The animation is theater over a
            state change that has already happened.
          * the SERVER write is what makes the door work WITH THE SCRIPT DEAD (B section 4.6:
            "with JS unavailable the pill still works as a plain form and the swap is instant and
            complete"). Without it, `leave` with no JS would end deep work for exactly one
            response and the next navigation would walk back into it off the untouched jar --
            an exit that does not exit, which is the one thing DS 3.4 cannot tolerate given it
            makes `leave` the ONLY exit.

        Dispatch has four entry points
        (DS:281-283) and three of them are plain links to `/queue/stack` in `queue.html` -- a file
        this lane does not own -- carrying no mode arg at all. So the ARRIVAL is what turns the
        mode on: entering the stack by any door is entering Dispatch, which is DS:283's "the first
        three turn the mode on AND open the stack" implemented once instead of three times.

        Order is the contract, not a preference: the clear is checked FIRST and returns, so no
        later branch can switch a mode back on inside a response that has just refused it.
        """
        deep = _deep_now()
        # ---- V-SHELL-2 (task 0176): `io_deep`, written before the `if deep` branch below can
        # return. Only `?deep=` acts, because that arg is the only thing on this surface that
        # MEANS a crossing: the two controls that carry it are the pill's `Deep work` segment and
        # the deep header's `leave`, and DS 3.4 says those are the only door in and the only door
        # out. A bare `io_deep=1` cookie on an ordinary navigation is the mode already running
        # and must not be rewritten.
        deep_arg = request.args.get("deep")
        held = request.cookies.get("io_deep")
        if deep_arg == "1" and held != "1":
            resp.set_cookie("io_deep", "1", max_age=31536000, path="/", samesite="Lax")
        elif deep_arg == "0" and held == "1":
            # `delete_cookie` and not `io_deep=0`, for the reason the io_dispatch clear below
            # states: a mode that is off and a mode that was never entered are the same state.
            # `io_deep_since` goes with it -- a clock for a session that has ended is not a fact
            # about anything, and leaving it behind would put a stale `since` on the next entry.
            resp.delete_cookie("io_deep", path="/")
            resp.delete_cookie("io_deep_since", path="/")
        # WRITTEN ONLY WHEN IT CHANGES, exactly as `io_dispatch` is below: the three-second poll
        # re-renders this header, and a Set-Cookie on every poll is a header the browser rewrites
        # twenty times a minute to say what it already said.
        arg = request.args.get("dispatch")
        if deep:
            # DEEP WINS, AND THE CLEAR IS A REAL WRITE (DS:265-266). Entering deep work while
            # Dispatch is on is allowed and ends Dispatch with the abandoned-run posture: no
            # summary, no ceremony, nothing asked. `delete_cookie` and not `io_dispatch=0`,
            # because a mode that is off and a mode that was never entered are the same state and
            # the jar should not be carrying a word for it.
            if request.cookies.get("io_dispatch") == "1" or arg == "1":
                resp.delete_cookie("io_dispatch", path="/")
            return resp
        want = arg if arg in ("0", "1") else ("1" if request.endpoint == "stack" else None)
        # Written only when it CHANGES: a Set-Cookie on every three-second poll is a header the
        # operator's browser rewrites twenty times a minute to say what it already said.
        if want is not None and request.cookies.get("io_dispatch") != want:
            resp.set_cookie("io_dispatch", want, max_age=31536000, path="/", samesite="Lax")
        return resp

    # ------------------------------------------------------------------ rooms

    @app.get("/")
    def front_door():
        """The front door is the Attention inbox (A4 INFINITY-STREAMLINE, D-ALPHA-UX-1, 2026-09-14).

        `/` used to render the Queue's card wall, and the surface that answers "what needs me" was
        a second click away. A redirect and nothing else: it reads no store and renders nothing,
        and `/queue` below is unchanged and one tab away. Reverting is putting `@app.get("/")` back
        on `queue`. Pinned by `web/tests/test_the_front_door.py`.

        AND IT CARRIES THE QUERY STRING, added 2026-09-16 (W3-UX-INFINITY, reported by
        W3-P14-INSTRUMENT). This redirect used to drop everything after the `?`. MEASURED:
        `GET /?theme=light` answered `302 Location: /attention/`, with the parameter gone.

        THE DEFECT IS NOT ABOUT THEME. A 302 that discards its query discards EVERY parameter,
        present and future. `theme` is only the one visible today, because something downstream
        reads it; a deep link, a filter or an invite token would vanish the same way and the next
        person would debug it from nothing. So this preserves the query rather than special-casing
        the parameter that exposed it.

        IT WIDENS NOTHING. `?theme=` already works on `/attention/` when asked directly, so this
        makes `/` agree with its own destination rather than granting anything new. The target is
        a fixed internal path from `url_for`, so appending the caller's query cannot redirect
        anywhere else.

        AND THE THEME FIX MADE THIS HARDER TO SEE, WHICH IS WHY THE PINNED CASE IS `?theme=dark`.
        The destination received no parameter and fell to the pre-paint default. Before `e96bbd6`
        that default was dark, so `/?theme=light` gave dark and was visibly wrong. After it the
        default is light, so `/?theme=light` gives light FOR THE WRONG REASON and looks correct.
        The live symptom flipped to `/?theme=dark` giving light, which nobody asks for. A fix that
        corrects the symptom a defect presents through can leave the defect and remove its only
        visible sign, so `web/tests/test_the_front_door.py` pins the direction that can still fail.
        """
        target = url_for("attention.inbox")
        query = request.query_string.decode("utf-8", "replace")
        return redirect(target + ("?" + query if query else ""))

    @app.get("/queue")
    def queue():
        return render_template("queue.html", **_queue_ctx())

    @app.get("/queue/table")
    def queue_table():
        """The full board as one sortable table. The door behind the ranked window.

        HIS RULING, 2026-08-31: the ranked window stays the default view AND gets a door to the
        full sortable table behind it. Both, not either. The two were never in conflict because
        they answer different questions -- `/queue` is a PLAN and is deliberately seven rows,
        this is an INVENTORY -- and the starvation guarantee only ever needed protecting on the
        surface that claims to be a plan.

        A SEPARATE ROUTE RATHER THAN A MODE ON `/queue`, and that is not a filing preference.
        `web/MUST-NOT-BUILD.md` item 2 is live and unoverruled, its clause `nothing is sortable
        by the operator` is unqualified, and `web/static/console.css:1893` reads it as forbidding
        anything hand-sortable. Putting the sort here leaves the ranked window's header exactly
        as it is -- `pointer-events:none`, zero controls, `cursor:auto` -- so the three committed
        censuses that exist to catch a sortable header keep measuring an unchanged surface and
        keep being able to fail. The open question about that clause is
        `outputs/2026-09-01-sprints/S3/01-ITEM-2-AND-THE-SORTABLE-TABLE.md` and it is his.

        NOTHING HERE WRITES. `sort`, `dir` and `queue` are read from the URL, checked against
        `model.SORTABLE` rather than used as keys, and reorder a rendered list. No verb is
        dispatched from this route, `priority` is never touched, and `set` stays off
        `ROOM_VERBS['queue']`. `GET` only: this is not on any write door.
        """
        args = request.args
        view = model.table_view(
            queue=(args.get("queue") or None),
            sort=(args.get("sort") or None),
            direction=(args.get("dir") or "desc"))
        # THE SEARCH (D-INFINITY-UX-RULINGS-1, 2026-09-14, amending MUST-NOT-BUILD item 2 condition
        # 3). A GET narrowing of the RENDERED rows by id or title, case-insensitive, applied after the
        # queue tab and the sort, so `total` stays the whole board and `shown`/`enriched` describe
        # what is on screen. It is a read of the view this route already built and writes nothing.
        search = (args.get("q") or "").strip()[:200]
        if search:
            needle = search.casefold()
            view["rows"] = [r for r in view["rows"]
                            if needle in str(r.get("id") or "").casefold()
                            or needle in str(r.get("title") or "").casefold()]
            view["shown"] = len(view["rows"])
            view["enriched"] = sum(1 for r in view["rows"] if r.get("enriched"))
        return render_template("table.html", room="queue", search=search, **view)

    @app.get("/queue/stack")
    def stack():
        """Run the stack: full screen, one Decide item at a time, resolve and advance.

        Closing returns to the originating tier AND the scroll position it was opened from,
        which is why `from` and `scroll` ride the URL rather than living in the browser only.
        `skip for now` advances `n`, which cycles to the back of the stack rather than deferring:
        skipping inside a stack is not a wake-condition decision and typing it as one would be a
        lie about what happened.

        `d` is the count of items RESOLVED in this run, and it is a second cursor because a
        resolved item leaves `queue_items()` while a skipped one does not. Without it the mode had
        no way to advance on a decision at all: the poll does not run here by design, so the only
        re-render after a write was a patch() that returns immediately on this page. Three clicks
        produced one post, the pips never moved and the button never came back. With `d` the
        total stays the size the stack was entered at, and a decision fills a pip exactly as a
        skip does.
        """
        items = [i for i in model.queue_items() if i["tier"] == "decide"]
        # V-SHELL-3 (0177). `x` IS THE OPTIMISTIC LOOP'S ONE HONEST LIE, AND IT IS THE CLIENT'S
        # LIE RATHER THAN THE SERVER'S. The stack advances by navigating and the resolve POST is
        # optimistic, so the client asks for the next document BEFORE the write it just started
        # has committed. Without this the next document renders the SAME card -- measured, 0177,
        # press-to-readable 436ms and a spurious `+1 arrived`, because `d` moves the denominator
        # and nothing moves the cursor into the list.
        #
        # So the client names the id it has already committed to on its own screen and this
        # route renders the list without it. THE ROW IS NOT TOUCHED: nothing is written, nothing
        # is hidden from any other surface, and the exclusion lasts exactly one render. If the
        # POST then fails, the put-back returns to the pre-press URL, which carries no `x` at
        # all, and the item is at the front of the list again because it never left it.
        #
        # `x` IS A LIST, AND IT IS A LIST BECAUSE ONE ID WAS MEASURABLY NOT ENOUGH. It read one
        # id until 2026-08-19, which assumed the write for card A had landed before the client
        # asked for the document after B. Nothing enforced that: `warm()` fires ~250ms after the
        # press and a resolve POST answers in ~1s on this host, so the next document was rendered
        # while the previous write was still in flight, A was still an open row, and A came back
        # at the front. MEASURED over eight presses: q0008, q0010, q0008 AGAIN, q0008, q0012,
        # q0014, q0012 AGAIN, q0012 -- every card decided two or three times, the second write
        # refused and put back. Loud rather than silent, and still the loop showing the operator
        # a decision he had already taken.
        #
        # So the client names every id it has committed THIS RUN and this route renders the list
        # without all of them. The exclusion is still the client's statement about its own
        # screen, still writes nothing, and still lasts exactly one render; what changed is that
        # it no longer expires after one advance. `d` counts the same ids, so the denominator
        # below is unaffected: len(items) drops by exactly what d added.
        drop = [x for x in (request.args.get("x") or "").split(",") if x.strip()]
        if drop:
            items = [i for i in items if i["id"] not in drop]
        from_tier = request.args.get("from") or "decide"
        scroll = request.args.get("scroll") or "0"
        n = int(request.args.get("n") or 0)
        d = int(request.args.get("d") or 0)
        # NO `drop` TERM HERE, DELIBERATELY, and it was wrong for one edit: `d` already counts
        # the item `x` removed. `len(items) + n + d` is the denominator as it will be one moment
        # from now, which is exactly the denominator the optimistic screen is already showing.
        total = len(items) + n + d
        # V-SHELL-3 (0177): `t0` is the denominator this run was ENTERED at. Absent on the three
        # entry doors, it is stamped here from the live total and then rides every advance, which
        # is what makes DS 5.2's `+N arrived` a measured fact instead of a difference between two
        # numbers nobody wrote down.
        t0 = int(request.args.get("t0") or 0) or max(total, 1)
        # `n` and not `n + d`: `n` is the position of the cursor in the CURRENT list, and the item
        # a decision removed is the one the next item slides into.
        item = items[n % len(items)] if items else None
        back = f"/queue?tier={from_tier}&scroll={scroll}"
        nxt = (f"/queue/stack?from={from_tier}&scroll={scroll}&n={n}&d={d + 1}&t0={t0}"
               + (f"&x={item['id']}" if item else ""))
        # V-SHELL-3 (0177). `skip for now` and `not fast` are two different exits and they move
        # two different cursors: a skip cycles the item to the back (n+1, denominator unchanged,
        # DS 5.4's hollow pip at the back), while `not fast` REMOVES the item from the run
        # entirely (DS 5.5: the pip is removed, the denominator shrinks, the operator is never
        # punished with a longer-looking run for using the button). `t0` therefore drops by one
        # on a demote and stays put on a skip -- the same two facts the pip row is drawing.
        skip_url = f"/queue/stack?from={from_tier}&scroll={scroll}&n={n + 1}&d={d}&t0={t0}"
        judge_url = (f"/queue/stack?from={from_tier}&scroll={scroll}&n={n}&d={d}"
                     f"&t0={max(t0 - 1, 1)}")
        # The celebration cursor. It was hardcoded `data-since="0"`, which was harmless only by
        # accident: `stack.html` renders neither `#celebrate` nor the canvas, so the events came
        # back and were dropped. What it did do was ask for the whole 'done' history every three
        # seconds. It matters more now, because the stack ADVANCES BY NAVIGATING, so every
        # decision renders a fresh document and re-reads this cursor; the first surface to add a
        # receipt to this page with a zero cursor would celebrate the operator's own click, which
        # is the one thing celebration is defined never to do.
        with store.read() as s:
            since = s.scalar("SELECT COALESCE(max(seq), 0) FROM brain.thread")
        # `room="queue"` is what the stack's forms post to and mint a token for: the stack is a
        # Queue surface running full screen, not a sixth room. `data-room="stack"` in the template
        # is a different thing -- it tells the poller not to patch this page -- and the two are
        # deliberately not the same value.
        # V-SHELL-3 (0177): DS section 5's surface needs the shape of the WHOLE run, not just
        # the card in hand -- the pip row is a map of what is left (DS 5.4) and the header line
        # is its textual equivalent (DS 6.2). Every value below is derived from items already
        # read above; nothing new is queried and no route was added.
        view = _stack_view(items, n, d, t0)
        agents = model.fleet()["agents"]
        return render_template("stack.html", item=item, done=n + d, total=max(total, 1),
                               n=n, d=d, decided=d, next_url=nxt, scroll=scroll, since=since,
                               room="queue", back=back, from_tier=from_tier,
                               t0=t0, skip_url=skip_url, judge_url=judge_url,
                               pips=view["pips"], arrived=view["arrived"],
                               waiting=view["waiting"], damage=_stack_damage(),
                               external=_stack_external(item),
                               # DS 5.3 state 5, ARRIVED-THIS-RUN. The test is exact rather than
                               # heuristic: `n + d` is how many of the run's own items are behind
                               # the operator, and `t0` is how many it committed to. Once the
                               # first has caught the second, everything the run started with has
                               # been resolved or cycled past, so the card in hand was not in it.
                               # The operator should know a card was not in the stack he
                               # committed to.
                               item_new=bool(item) and (n + d) >= t0,
                               # DS 7.3's first variant: the run cannot complete by resolution
                               # alone because every item still in it has already been cycled
                               # past. `n >= len(items)` is that sentence in arithmetic -- the
                               # skip cursor has been round the whole remaining list once.
                               skipped_only=bool(items) and n >= len(items),
                               queue_open=len(model.queue_items()),
                               working=len([a for a in agents if a["state"] == "working"]),
                               red=len([a for a in agents if a["state"] == "dead"]))

    @app.get("/brief")
    def brief():
        return render_template("brief.html", room="brief", brief=model.brief(),
                               receipts=_receipts(), q=model.queue_items())

    @app.get("/fleet")
    def fleet():
        return render_template("fleet.html", room="fleet", fleet=model.fleet(),
                               receipts=_receipts())

    @app.get("/scope")
    def scope():
        """One route, two states: say it badly, then demolish what the drafter made of it.

        The draft is recomputed from the intent on the query string rather than held in a
        session. The console holds no state of its own beyond the receipt stripes, and a
        half-finished definition of done is exactly the kind of state that turns into a second
        queue if it is allowed to persist somewhere the operator cannot see.
        """
        intent = (request.args.get("intent") or "").strip()
        rows = scoping.draft(intent) if intent else []
        return render_template("scope.html", room="scope", receipts=_receipts(),
                               lanes=_lanes(), intent=intent, rows=rows,
                               lane=request.args.get("lane") or "",
                               price=scoping.price(rows) if rows else "")

    @app.get("/sessions")
    def sessions_room():
        """THE PROOF SURFACE. Read-side only, zero verbs, denominators from the disk.

        Row `0424`: Study reports transcript logging by counting rows in `brain.transcript`, so a
        file the index never saw cannot appear on it at any value. This room reads
        `ingest coverage`, whose denominator is the disk walk, and renders every number against
        what it is out of. See `web/sessions.py` for the three traps that shape it.
        """
        return render_template("sessions.html", room="sessions", s=sessions.feed())

    # ------------------------------------------------------------------ the live terminal wrapper
    #
    # THE READ SIDE OF WHAT ANDREW ASKED FOR ON 2026-08-28: his own Claude Code sessions, live,
    # rendered from the JSONL the harness appends to. `web/live_session.py` carries the design and
    # the four traps it was measured against; these two routes are the whole server surface.
    #
    # THE ROOM IS `live` AND IT IS DELIBERATELY NOT IN `rooms.ROOMS`. This surface calls zero
    # verbs, and an unknown room is refused BY NAME at the write door -- `rooms.allowed` raises
    # `RoomRefusal: no such room` before a row is looked up -- which is a stronger posture than an
    # empty allowlist because it does not depend on the allowlist staying empty. Nothing on either
    # page renders a form, a CSRF token or an action.

    @app.get("/live")
    def live_index():
        """The picker: which of your terminals to watch. Disk first, store second.

        The index writes a session's row at `SessionEnd`, so a terminal open right now has no row
        at all (`web/sessions.py` trap 1). A picker built from `brain.session` would omit exactly
        the sessions this page exists to show, which is why the walk decides what exists.
        """
        try:
            hours = max(1, min(720, int(request.args.get("hours") or 24)))
        except ValueError:
            hours = 24
        kind = "all" if request.args.get("kind") == "all" else "main"
        ix = live_session.index(hours=hours, kind=kind,
                                q=(request.args.get("q") or "").strip()[:80])
        return render_template("live_session.html", room="live", key="", ix=ix, sess=None)

    @app.get("/live/<key>")
    def live_one(key):
        """One session, live. Read only, and the page says so in the sidebar rather than implying it.

        A key that resolves to no file on this host is a rendered amber state and not a 404: the
        honest answer is "the store names this and the disk does not have it", which a 404 cannot
        say. A key that is not a session key at all does 404, because that is a bad URL.
        """
        sess = live_session.feed(key)
        if not sess["path"] and not sess["row"] and "not a session key" in (
                (sess.get("degraded") or {}).get("text") or ""):
            abort(404)
        # The opening line of the feed, in the seam grammar the runfeed uses. It states what the
        # reader is looking at at exactly the moment he could believe it is a terminal.
        # A FIXED SENTENCE, no number in it. It is the seam grammar's job: it states what the
        # reader is looking at at exactly the moment he could believe it is a terminal. A count
        # here would be frozen at page load, which is the defect the header comment describes.
        opened = f"reading {key} from disk · a transcript, not a terminal"
        others = [r for r in live_session.index(hours=1, kind="all", cap=8)["rows"]
                  if r["key"] != key][:6]
        return render_template("live_session.html", room="live", key=key, sess=sess,
                               opened=opened, others=others, ix=None)

    @app.get("/study")
    def study():
        return render_template("study.html", room="study", study=model.study(),
                               crosstalk=model.crosstalk())

    # ------------------------------------------------------------------ the spine

    @app.get("/task/<tid>")
    def task(tid):
        from web.views.navigation import inspector_url
        rv = model.review(tid)
        if not rv:
            abort(404)
        return render_template("detail_task.html", room="queue", rv=rv,
                               attention_return=inspector_url('work_item:' + tid, request.args)
                               if request.args.get('from_attention') == '1' else None,
                               crosstalk=model.crosstalk(tid), receipts=_receipts(),
                               stopwatch=model.stopwatch())

    @app.get("/agent/<name>")
    def agent(name):
        """THE LIVE TERMINAL VIEW. One agent, followed across tasks, read-side only.

        This route replaced a 29-line detail page (task 0166). What it renders is
        `web/runfeed.py`, which opens the run stream files and calls not one verb; the only
        writes reachable from this page are take-control's, and they go through the same
        `/<room>/act` door as every other write in the console. The room is `fleet`, so the
        allowlist that governs this page is the Fleet room's -- checked server side, as ever.
        """
        with store.read() as s:
            a = s.one("SELECT * FROM brain.agent WHERE name = %s", (name,))
            th = s.query("SELECT 1 FROM brain.thread WHERE from_agent = %s LIMIT 1", (name,))
            items = s.query("SELECT id, title, state, finished_at FROM brain.work_item "
                            "WHERE claimed_by = %s ORDER BY created DESC LIMIT 20", (name,))
        if not a and not th and not items:
            abort(404)
        f = runfeed.feed(name)
        return render_template("runfeed.html", room="fleet", name=name, agent=a,
                               items=items, receipts=_receipts(), feed=f,
                               steering=f.get("steering"), picker=_picker(name, f),
                               consequence=steering.CONSEQUENCE)

    @app.get("/question/<qid>")
    def question(qid):
        with store.read() as s:
            q = s.one("SELECT * FROM brain.question WHERE id = %s", (qid,))
        if not q:
            abort(404)
        return render_template("detail_question.html", room="queue", q=q,
                               receipts=_receipts())

    @app.get("/session/<sid>")
    def session(sid):
        with store.read() as s:
            sess = s.one("SELECT * FROM brain.session WHERE id = %s", (sid,))
            tr = s.query("SELECT * FROM brain.transcript WHERE session_id = %s", (sid,))
            kids = s.query("SELECT id, agent, started_at FROM brain.session "
                           "WHERE parent_session_id = %s ORDER BY started_at", (sid,))
        if not sess:
            abort(404)
        return render_template("detail_session.html", room="study", s=sess, transcripts=tr,
                               kids=kids)

    @app.get("/event/<eid>")
    def event(eid):
        with store.read() as s:
            where = "event_id::text = %s" if "-" in eid else "event_seq = %s::bigint"
            e = s.one(f"SELECT * FROM brain.event WHERE {where}", (eid,))
            obs = s.query("SELECT * FROM brain.observation WHERE event_id = "
                          "(SELECT event_seq FROM brain.event WHERE "
                          + where + ")", (eid,)) if e else []
        if not e:
            abort(404)
        return render_template("detail_event.html", room="study", e=e, obs=obs)

    @app.get("/rec/<rid>")
    def rec(rid):
        with store.read() as s:
            r = s.one("SELECT * FROM brain.recommendation WHERE id = %s::bigint", (rid,))
        if not r:
            abort(404)
        return render_template("detail_rec.html", room="queue", r=r, receipts=_receipts())

    @app.get("/artifact/<int:seq>")
    def artifact(seq):
        with store.read() as s:
            a = s.one("SELECT * FROM brain.artifact WHERE seq = %s", (seq,))
        if not a:
            abort(404)
        a["exists_now"] = os.path.exists(a["path"]) if a.get("path") else False
        return render_template("detail_artifact.html", room="queue", a=a)

    @app.get("/image/<attach_id>")
    def image(attach_id):
        """The bytes, streamed from disk. THE ONLY PLACE IMAGE BYTES EXIST IN THIS SYSTEM.

        There is no upload endpoint and no copy anywhere: this reads the file the pointer names
        and streams it. That is what "a pointer and a hash, never the blob" costs on the render
        side, and it is a cost worth naming -- the alternative is two homes for one image, which
        v1's contract already refused for transcripts.

        THE PATH COMES FROM THE ROW, NEVER FROM THE REQUEST. `attach_id` selects a row; the row
        supplies the path. There is no path component of this URL to traverse with, which is why
        the route is keyed on the attach id rather than on the pointer.

        A file that vanished between the render and this fetch 404s, and `console.js` turns that
        404 into the MISSING finding rather than letting the browser draw its broken glyph. The
        hash is re-checked here too: serving bytes that no longer match the attestation under the
        plain URL would let a card show changed bytes as the attested ones, which is the exact
        lie the CHANGED state exists to refuse. `?unattested=1` is how the CHANGED block asks for
        them anyway, and that block renders them dimmed and says what they are.
        """
        row = images.by_attach_id(attach_id)
        if not row or row["state"] != "attached" or not row.get("pointer"):
            abort(404)
        if (row.get("pointer_host") or "") != images.host():
            # Another host's pointer is not this host's file. Serving whatever happens to sit at
            # that path here would be the silent VPS failure `pointer_host` exists to prevent.
            abort(404)
        v = images.verify(row["pointer"], row["sha256"])
        if v["verdict"] != "ok" and request.args.get("unattested") != "1":
            abort(404)
        try:
            return send_file(row["pointer"], mimetype=row["mime"], conditional=True,
                             download_name=os.path.basename(row["pointer"]))
        except OSError:
            abort(404)

    @app.get("/sprint/<slug>")
    def sprint(slug):
        """A sprint is a `canonical_task` prefix: `<project-slug>#<task-id>` resolves the ladder."""
        with store.read() as s:
            items = s.query(
                "SELECT id, title, state, canonical_task, accepted_at FROM brain.work_item "
                " WHERE canonical_task LIKE %s ORDER BY canonical_task, id", (f"{slug}#%",))
        return render_template("detail_sprint.html", room="queue", slug=slug, items=items)

    # ------------------------------------------------------------------ the project board
    #
    # APPENDED HERE, at lane B2's assigned anchor, and every line above is untouched. Bus row
    # 0436, under `web/MUST-NOT-BUILD.md` item 3, which the operator overruled on 2026-08-28 for
    # a project board only, against an incident with a bill attached.
    #
    # READ ONLY. This route renders four columns; the drop that moves a card between them is a
    # POST to the one write door, `/<room>/act`, with `room="board"`, and it runs `project rest`
    # or `project resume` through `rooms.dispatch`. There is no second write path and this
    # function performs no state change of any kind.
    #
    # `web/board.py` carries the reasoning, including why the drag is permitted where item 2
    # forbids drag-to-reorder: this one writes a STATE and never an order.

    @app.get("/board")
    def board_room():
        from . import board as board_mod
        b = board_mod.board()
        return render_template("board.html", room="board", board=b,
                               columns=b["columns"], throttle=b["throttle"],
                               token=guard.token_for("board"))

    # ------------------------------------------------------------------ the one write door

    @app.post("/<room>/act")
    def act(room):
        # `room` is what Flask matched in the path. Nothing below reads it from the payload, and
        # `guard.guard_write` refuses a payload that still carries a contradicting one rather than
        # ignoring it, because a page still sending that field is a stale page and saying so is
        # cheaper than letting it fail somewhere further in.
        action = (request.form.get("action") or "").strip()
        item_id = (request.form.get("id") or "").strip()
        text = request.form.get("text") or ""
        try:
            guard.guard_write(room, request.form)
            # Unknown room, and a room that calls zero verbs: both refused before a row is looked
            # up, so the refusal never depends on which id the request happened to name.
            rooms.assert_room_can_act(room)
            out = _perform_with_receipt(room, action, item_id, text)
        except guard.WriteRefused as exc:
            return jsonify({"ok": False, "kind": "write-refusal", "error": str(exc)}), 403
        except rooms.RoomRefusal as exc:
            return jsonify({"ok": False, "kind": "room-refusal", "error": str(exc)}), 403
        except rooms.ItemRefusal as exc:
            return jsonify({"ok": False, "kind": "item-refusal", "error": str(exc)}), 403
        except rooms.VerbNotBuiltYet as exc:
            return jsonify({"ok": False, "kind": "not-built", "error": str(exc)}), 501
        # Engine domain refusals (including missing/already withdrawn acceptance)
        # share the existing client-refusal contract: HTTP 400, kind "refused".
        except (actions.ActionRefused, _engine_transitions.VerbError) as exc:
            return jsonify({"ok": False, "kind": "refused", "error": str(exc)}), 400
        except store.UnknownTransition as exc:
            return jsonify({"ok": False, "kind": "unknown-verb", "error": str(exc)}), 501
        except Exception as exc:                                        # noqa: BLE001
            app.logger.error("act failed: %s", traceback.format_exc())
            return jsonify({"ok": False, "kind": "error", "error": str(exc)}), 500
        with store.read() as s:
            head = s.scalar("SELECT COALESCE(max(seq), 0) FROM brain.thread")
        # The client advances its celebration cursor past its own write, which is how a click can
        # never celebrate itself. Celebration fires on events ARRIVING, not on inputs going in.
        # V-SHELL-3 (0177), ADDITIVE: two keys, nothing renamed and nothing removed.
        #
        # `undo` and `no_undo_reason` were already computed by every action and already handed to
        # `_push_receipt` for the list surface's stripe -- they were simply never sent to the
        # client. DS 4.2 and DS 5.7 require the stack ticker to carry `undo` where a real inverse
        # verb exists and `no undo — <reason>` IN THE VERB'S OWN TERMS where not, and a client
        # that cannot read the reason can only invent one. Measured before this line existed, the
        # ticker read `no undo — this verb has no inverse`, which is the client's sentence about
        # every verb; with it, the same press reads the verb's own.
        #
        # Every existing reader is unaffected: the core's `post()` reads ok/error/receipt/note/
        # celebrate_from and nothing else.
        return jsonify({"ok": True, "verb": out["verb"], "receipt": out["receipt"],
                        "note": out.get("note"), "celebrate_from": head,
                        "undo": out.get("undo"), "no_undo_reason": out.get("no_undo_reason"),
                        "subject_type": out.get("subject_type")})

    # ------------------------------------------------------------------ poll and patch

    @app.get("/api/patch/<room>")
    def patch(room):
        if room == "queue":
            html = _render_regions("queue.html", _queue_ctx())
        elif room == "brief":
            html = _render_regions("brief.html", {"room": "brief", "brief": model.brief(),
                                                  "receipts": _receipts(),
                                                  "q": model.queue_items()})
        elif room == "fleet":
            html = _render_regions("fleet.html", {"room": "fleet", "fleet": model.fleet(),
                                                  "receipts": _receipts()})
        elif room == "study":
            html = _render_regions("study.html", {"room": "study", "study": model.study(),
                                                  "crosstalk": model.crosstalk()})
        elif room == "board":
            # ROW 0436, ADDED 2026-08-29 AFTER THE HUMAN-PATH AUDIT, and the board was the one room
            # that fell through to the `else` below. It answered 200 with `{"regions": {}}` forever,
            # so the client polled every three seconds and had nothing to swap. Watched twice: the
            # drop POSTs 200 in 254ms, a correct banner shows for about nine seconds, and the card
            # then sits in its OLD column through nine poll cycles until the page is reloaded by
            # hand.
            #
            # BOTH HALVES WERE MISSING AND EITHER ALONE STILL REPAINTS NOTHING: `board.html`
            # declared no `data-region`, alone among the rooms, and this chain had no `board` arm.
            # A silent `else` is why it looked like a rendering bug rather than an absence.
            #
            # This is the room where a stale screen costs the most. The board is the guardrail the
            # operator asked for against his own eight-terminals bill, so a board that still shows a
            # project as in progress after he moved it to hold is telling him the precise thing that
            # incident already cost him.
            from . import board as board_mod
            b = board_mod.board()
            html = _render_regions("board.html", {"room": "board", "board": b,
                                                  "columns": b["columns"],
                                                  "throttle": b["throttle"],
                                                  "token": guard.token_for("board")})
        else:
            html = {}
        return jsonify({"regions": html})

    # ------------------------------------------------------------------------- intake (row 0438)
    #
    # HIS ASK 7, AND THIS IS ITS LAST LINK. Lane D1, 2026-08-30, appended after the patch route
    # under the rule two other lanes were editing this file under tonight: NOTHING ABOVE THIS LINE
    # IS TOUCHED. Two additions, both reads.
    #
    # WHY A ROOM OF ITS OWN AND NOT A BLOCK ON `/scope`. `web/rooms.py` files `intake` and
    # `accept` under the Scope room's verbs, so Scope was the obvious host, and it is the wrong
    # one: the badge has to ride the tab a person clicks, and a badge on `Scope` counts intake
    # items on a room whose other half is the definition-of-done drafter. `0438`'s own words for
    # the surface are *"things flow to the like intake attention inbox and just leave that open"*,
    # which is a room. Nothing was added to `ROOM_VERBS` for it, which is what makes the room
    # read only by construction: `rooms.assert_room_can_act("intake")` refuses an unknown room
    # before a row is looked up, exactly as it does for a typo.
    #
    # IT IS NOT IN THE PATCH CHAIN ABOVE, AND THAT IS A DECISION RATHER THAN AN OMISSION. Adding
    # an `intake` arm means editing a line above this one, which this lane may not do tonight; and
    # the surface loses nothing, because MUST-NOT-BUILD item 7's overrule keeps the anti-capture
    # posture whole -- *"no notification, no push, no sound"*, and the 7am moment is **open the
    # page**. A room that repaints itself every three seconds is the beginning of a page that asks
    # for him. The badge and the list are computed on a real navigation and nowhere else.

    @app.context_processor
    def _intake_badge():
        """The amber intake badge, for the SHELL, on the same construction as the two above.

        MUST-NOT-BUILD ITEM 7 IS OVERRULED FOR EXACTLY THIS AND THE OVERRULE'S CONDITIONS ARE
        REQUIREMENTS. AMBER, never red: the incident item 7 was written against is a RED count
        that described things merely waiting, and its sentence is *"Red means damage and nothing
        else, shell wide, or there is no colour left for a thing that is actually broken."* An
        unprocessed intake item is waiting on a human, which is what `--wait` means in this
        shell. This badge spends none of the damage colour. It counts `state = 'inbox'` and
        nothing else, which is the store's own word for "not yet sorted by a human" and the
        narrowest number this table can produce.

        A CALLABLE AND CACHED ON `g`, for `_deep_state`'s reason: a context processor runs on
        every render. `base.html` calls it behind `not patch_only`, so the three-second poll pays
        nothing for a header it throws away, and the deep-work branch does not render the nav at
        all -- so deep work carries no badge, which is item 9 and is the correct answer anyway.

        The `/intake` route below seeds this cache with the number it is about to RENDER, so the
        badge on that page cannot disagree with the list under it.
        """

        def intake_waiting():
            if not hasattr(g, "_intake_waiting"):
                g._intake_waiting = model.intake_waiting_count()
            return g._intake_waiting

        return {"intake_waiting": intake_waiting}

    @app.get("/intake")
    def intake_room():
        """What arrived through the door and has not been sorted. Zero verbs.

        The count is stashed on `g` BEFORE the render so the badge in the header and the list in
        the body are the same read. Two counts of one number that can disagree is the defect
        `_dispatch_state` names about `deep_count` and `dispatch_count` sharing a cache slot.
        """
        data = model.intake()
        if data["read"]:
            g._intake_waiting = data["waiting_n"]
        return render_template("intake.html", room="intake", intake=data)

    # ------------------------------------------------------ the document is never a stored copy
    #
    # A THIRD `after_request`, APPENDED RATHER THAN FOLDED INTO EITHER OF THE TWO ABOVE. Row
    # 0432, lane B4, 2026-08-29. Flask runs every registered hook, so this is purely additive and
    # two other lanes were appending to this file in the same window. Neither existing hook is
    # touched: `_say_what_is_being_served` writes three identity headers and `_mode_cookies` is
    # the one place the mode cookies are written, and folding a cache rule into either would put
    # two unrelated reasons behind one `return resp`.
    #
    # WHAT IT FIXES, AND IT IS HIS OWN COMPLAINT WEARING A DIFFERENT HAT. Measured on this
    # console 2026-08-29 before this hook existed: `curl -sI` on a queue URL returned 200 with
    # `Content-Type`, `Set-Cookie`, `X-Console-Commit`, `X-Console-Started` and `X-Console-Pid`,
    # and NO `Cache-Control` and NO `Expires`. With no freshness stated, a browser is free to
    # serve the copy it already holds. Measured on a phone: after a decision, a same-url
    # re-navigation rendered the OLD document -- the tier chip still read `8` and the card just
    # decided was still listed -- while a fresh fetch of the identical URL in the same second
    # read `7` with the card gone. That is 2026-08-28's *"nothing happened when I accepted work"*
    # restated: the act landed, the store agreed, and the surface he came back to did not, with
    # nothing on the screen saying which of the two was true.
    #
    # ------------------------------------------------------------------ CORRECTED 2026-08-31
    #
    # THIS BLOCK USED TO SAY `no-store` AND NOT `no-cache`, on the ground that "`no-cache` permits
    # storing and requires revalidation, which is enough for the HTTP cache and NOT enough for the
    # back-forward cache". THAT WAS NEVER MEASURED AND IT DOES NOT HOLD.
    #
    # A/B/C'd on a real console at 1440px, adding a row to the store while the page was away and
    # pressing back, which is the gesture the incident is about:
    #
    #     no-store                 back ~590ms   stale at load in 4 of 5 runs
    #     no-cache, must-revalidate back  ~85ms   fresh at load in 2 of 2 runs
    #     no header at all         back  ~74ms   fresh at load in 1 of 1 run
    #
    # `no-store` was roughly EIGHT TIMES SLOWER and LESS FRESH than the option that was skipped.
    # When it was stale, what corrected it was the three-second poll arriving about four seconds
    # later, not the header. So the header was buying the slowness and the poll was doing the work.
    #
    # HIS RULING, 2026-08-31: `no-cache, must-revalidate`. The browser may hold the page, and it
    # must ask the server before showing it again, so the back button is current by construction
    # rather than by a poll catching up. The original bug cannot return, because the server is
    # asked every time.
    #
    # WHAT IS GIVEN UP, STATED RATHER THAN GLOSSED. `no-store` was the only one of the three that
    # kept a decided queue off disk, which on a shared or borrowed phone is worth something on its
    # own, and it was a real reason at the time. It is traded for the back button being usable.
    # If the phone is ever genuinely shared, this is the line to revisit and the trade is recorded
    # here so that revisit does not start from scratch.
    #
    # SCOPED TO `text/html` BY THE RESPONSE'S OWN CONTENT TYPE, so nothing else changes:
    #
    #   * `/api/patch/<room>` is `application/json` and is untouched. It is a fetch on a
    #     three-second interval that already sends its own request every time; the region
    #     patching was measured working correctly (receipt stripe at 1.35s with no navigation)
    #     and this hook must not be the thing that changes it.
    #   * `console.css` and `console.js` are `text/css` and `text/javascript`. Putting `no-store`
    #     on them would refetch the stylesheet on every navigation to fix a staleness that is not
    #     theirs, which would make the phone slower to fix a bug it does not have.
    #
    # It is a header and not a meta tag for the reason MUST-NOT-BUILD item 9 gives about deep
    # work: a rule that only exists once the document is parsed is a rule the transport never
    # saw. A 304, a proxy and the bfcache all read the header.
    @app.after_request
    def _a_page_is_never_a_stored_copy(resp):
        """`Cache-Control: no-cache, must-revalidate` on HTML, and on nothing else.

        `no-cache` does NOT mean "do not cache". It means "you may keep it, and you must ask me
        before you show it again", which is exactly the contract the back button needs.
        `must-revalidate` closes the one gap `no-cache` leaves on its own: it forbids a cache from
        serving the stored copy while it is unreachable, so a flaky connection cannot put yesterday's
        queue on the screen with nothing saying so.
        """
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype == "text/html":
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp

    # ---------------------------------------------------- what the queue orders by, and its filters
    #
    # A FOURTH CONTEXT PROCESSOR, APPENDED RATHER THAN FOLDED INTO ANY OF THE THREE ABOVE. Row
    # 0434, lane C3, 2026-08-29, and this file was assigned to this lane ADDITIVE ONLY: no line
    # above or below this block is modified, reflowed or reordered, and `python3 -c "import
    # web.app"` was run after every edit. It is the same shape lane B4 used for the
    # `after_request` directly above it, and for the same reason: Flask runs every registered
    # hook, so appending one is purely additive while folding a second reason into an existing
    # one is not.
    #
    # WHY A CONTEXT PROCESSOR RATHER THAN A KEY IN `_queue_ctx`. `_queue_ctx` builds its dict by
    # naming every key, so a new fact reaches `queue.html` only by editing that function, and
    # editing it is exactly what this lane was told not to do while two other lanes were in this
    # file in the same window. A processor adds a name to the template namespace and changes no
    # existing line.
    #
    # THE COST, AND THE HALF OF IT THAT IS PAID ONCE. `queue_order` is FREE: it reads
    # `human_queue.rank.DEFAULT_WEIGHTS`, which is a module constant, and touches no store.
    # `queue_filter_facts` is a CALLABLE for `_deep_state`'s reason -- a context processor runs on
    # every render including the three-second poll -- and `queue.html` calls it only under
    # `not patch_only`, which is the same guard `depth()` already carries in that template. It is
    # cached on `g` as well, so a page that somehow asked twice pays one catalogue read.
    @app.context_processor
    def _queue_order_and_filters():
        """`queue_order` (a value) and `queue_filter_facts` (a callable). Row 0434."""

        def queue_filter_facts() -> dict:
            if not hasattr(g, "_qfilter_facts"):
                g._qfilter_facts = model.queue_filter_facts()
            return g._qfilter_facts

        # `_queue_weights` is seeded by `_queue_ctx` on the room that actually renders the
        # sentence. Everywhere else it is absent and `order_terms` falls back to the defaults,
        # which costs no round trip and is the right answer on a page with no queue under it.
        return {"queue_order": model.order_terms(getattr(g, "_queue_weights", None)),
                "queue_filter_facts": queue_filter_facts}

    @app.get("/api/runfeed/<name>")
    def runfeed_poll(name):
        """The runfeed's own poller. Poll and patch, three seconds, plain fetch, no SSE.

        It is a SIBLING of `/api/patch/<room>` rather than a branch inside it, because the two
        have different keying and merging them would give one of them the other's bugs: a room
        patch swaps whole regions by name, and this returns only entries ABOVE a stream position
        the client already holds. The feed region is append-only; the vitals and the live tool
        line are single nodes replaced by id; and nothing here can reach the steering input or
        the picker, which are siblings of the feed and not children of it.
        """
        since = int(request.args.get("since") or 0)
        since_seq = int(request.args.get("seq") or 0)
        f = runfeed.feed(name, since=since, since_seq=since_seq)
        parts = app.jinja_env.get_template("runfeed_parts.html").module
        cur = next((b for b in f["runs"] if b["current"]), None)
        entries = []
        if cur:
            for e in cur["entries"]:
                entries.append({"key": f"s{e['pos']}",
                                "html": str(parts.entry(e, cur["path"], name))})
            for e in cur.get("said") or []:
                entries.append({"key": e["key"], "html": _said_html(e, name)})
        return jsonify({
            "vitals": str(parts.vitals(f["vitals"], name)) if f["vitals"] else "",
            "live": str(parts.live(cur)) if cur else "",
            "entries": entries,
            "position": f["position"], "seq": f["seq"],
            # WHICH RUN THE CURSOR BELONGS TO. Stream positions are per FILE, so a cursor from
            # the previous run is a number with no meaning against the next one -- and the
            # runfeed follows the AGENT across tasks, so that is not an edge case, it is the
            # normal event. When this changes the client reloads and gets the seam with it.
            "run": (f"{cur['run']['work_item_id']}-{cur['run']['attempt']}" if cur else ""),
            "steering": bool(f.get("steering")), "steered": bool(f.get("steered")),
            "findings": [str(parts.finding(x)) for x in f["findings"]],
        })

    @app.get("/api/runfeed/<name>/fold")
    def runfeed_fold(name):
        """The churn inside one fold, read only when the operator presses `show`.

        Not shipped with the poll: one fold on a live run held 1,199 events when this was
        measured, and sending those twenty times a minute to render `12 tool calls` would be the
        console's most expensive read on its most frequent path.
        """
        path = request.args.get("path") or ""
        # The path came from the page, so it is checked against the runs directory rather than
        # trusted: a reader endpoint that opens any path a query string names is a file-read
        # primitive, whatever it was written for.
        base = runfeed.runs_dir().resolve()
        try:
            target = Path(path).resolve()
            target.relative_to(base)
        except (ValueError, OSError):
            return jsonify({"ok": False, "error": f"{path!r} is not inside {base}"}), 400
        return jsonify(runfeed.fold_lines(str(target),
                                          int(request.args.get("from") or 0),
                                          int(request.args.get("to") or 0)))

    @app.get("/api/live/<key>")
    def live_poll(key):
        """The live view's own poller. Poll and patch, three seconds, plain fetch, no SSE.

        A SIBLING of `/api/runfeed/<name>` rather than a branch inside it, for the reason that one
        gives for not being a branch of `/api/patch/<room>`: the two have different producers and
        different keying, and merging them would give one of them the other's bugs.

        Entries are keyed `fold-<pos>` / `said-<pos>` / `beat-<pos>` and NOT by position alone. A
        fold is closed by the row that follows it and carries that row's position, so a bare
        position collides and the client's append-only skip would silently drop one of the pair.
        """
        since = int(request.args.get("since") or 0)
        f = live_session.feed(key, since=since)
        parts = app.jinja_env.get_template("live_parts.html").module
        return jsonify({
            "vitals": str(parts.vitals(f["vitals"])) if f["vitals"] else "",
            "live": str(parts.live(f)),
            "entries": [{"key": e["key"], "html": str(parts.entry(e))} for e in f["entries"]],
            "position": f["position"],
        })

    @app.get("/api/live/<key>/fold")
    def live_fold(key):
        """The records inside one fold, read only when the operator presses `show`.

        THE PATH IS NOT ON THE QUERY STRING, and that is the difference from `/api/runfeed/<name>/fold`
        rather than an inconsistency with it. That endpoint takes a path and checks it against the
        runs directory, because a reader that opens any path a query string names is a file-read
        primitive whatever it was written for. Here the client sends only the session KEY it is
        already on, and `live_session.resolve` turns it into a path and requires that path to live
        under the projects root, so no caller-supplied path exists to check.
        """
        r = live_session.resolve(key)
        if r["error"] or not r["path"]:
            return jsonify({"lines": [], "dropped": 0, "missing": True,
                            "error": r["error"]}), 400
        return jsonify(live_session.fold_lines(r["path"],
                                               int(request.args.get("from") or 0),
                                               int(request.args.get("to") or 0)))

    @app.get("/api/celebrate")
    def celebrate():
        # `mine=OPERATOR` is row 0409. The rule "a click never celebrates itself" used to be
        # enforced by the CLIENT jumping its cursor to the store's head after every write it
        # made, which silenced the operator's own act (intended) and also silenced every OTHER
        # actor's act that happened to land in the same window (not intended, and measured: a
        # `done` 200ms before an Accept was never shown at all). The exclusion is an identity,
        # so it is asked of the identity column. `OPERATOR` is `store.human_slug()` and is the
        # same string `accept work` writes into `brain.thread.from_agent`.
        since = int(request.args.get("since") or 0)
        return jsonify(model.celebrations(since, mine=OPERATOR))

    @app.get("/api/audit")
    def audit():
        """The per-room verb allowlist, machine readable. What an auditor reads."""
        return jsonify(rooms.audit())

    @app.get("/api/health")
    def health():
        # ADDITIVE. `store.health()`'s own keys are untouched: `brain-store-ready.sh` and
        # `brain-health.timer` read this endpoint and a renamed key would break a repair path.
        out = dict(store.health())
        out["serving"] = _serving_report(serving_commit, serving_started_at, serving_mtimes)
        return jsonify(out)

    # ------------------------------------------------------------------ the typable pane
    #
    # ONE LINE, AND IT IS THE WHOLE CONTACT THIS FILE HAS WITH THAT LANE. `web/terminal.py` owns
    # six routes and registers them itself; six entries here would be six edits in a module three
    # other lanes are also working in.
    #
    # IT REGISTERS A DIFFERENT ROUTE SET OFF LOOPBACK, and that is the enforcement of the first
    # condition MUST-NOT-BUILD item 11's overrule was granted under rather than a description of
    # it: with a non-loopback CONSOLE_HOST, a declared CONSOLE_ORIGIN, or CONSOLE_TERMINAL=off,
    # the pty endpoints ARE NOT REGISTERED AT ALL and only the page that states the reason is.
    # Before the pane, exposing this console leaked a dashboard; after it, it leaks a shell.
    terminal.register(app)

    # ------------------------------------------------------------------------- chats, read only
    #
    # ONE LINE, the same shape as the terminal's above. R29 binds this call and `web/chats` into
    # ONE admission: the package is not on the integration line, so this line landing alone makes
    # `create_app()` raise ImportError and THE CONSOLE DOES NOT START. Proposal and the two hunks:
    # `_scratch-infinity/artefacts/2026-09-10/INFINITY-PLATFORM-admiral/chats/
    #  PAIRED-MOUNT-PROPOSAL.md`. `chats.register` is a LAZY PEP 562 re-export and must stay one;
    # `from .views import register` pulls flask into the package import path and breaks the proofs
    # that run under this box's Windows interpreter. Routes: GET /chats/ and GET /chats/<agent_id>.
    # Root is `CHATS_ROOT` or `~/.infinity-os/chats`; the owning pair agrees it before a live mount.
    chats.register(app)

    # ------------------------------------------------------------------ attention, read only
    #
    # ONE LINE, the same shape as the terminal's above, and the whole contact this file has with
    # `web/views/**`. The blueprint is three GET routes over a read port and has no POST; it was
    # built, templated and tested at 0180a51 and registered nowhere, so `/attention` 404'd on a
    # feature that existed. `model.attention_port()` is the store-backed port, and today it
    # reports an absence or an empty queue rather than rows; the rows are `web/model.py`'s to
    # supply. Mounted 2026-09-09 by 2026-09-09-IOS-term-2.
    app.register_blueprint(build_attention(model.attention_port()))

    return app


def _lanes() -> list[str]:
    with store.read() as s:
        rows = s.query("SELECT DISTINCT lane FROM brain.work_item ORDER BY lane")
    return [r["lane"] for r in rows] or ["research"]


def _queue_ctx() -> dict:
    # THE WINDOW IS THE SCREEN'S, AND IT IS STILL SEVEN UNLESS HE ASKS FOR THE WHOLE TIER.
    #
    # HIS RULING, 2026-08-31, put to him with the three options and their costs: build the door,
    # opt-in per visit. The queue counted 23 items it would not show him and offered no control
    # anywhere on the page matching more, all, show, next or page, on 3 of 3 tiers. A page that
    # names an obligation and gives no way to reach it reads as an oversight every time it is seen.
    #
    # OPT-IN PER VISIT IS THE WHOLE OF WHAT HE RULED FOR, and each half of that matters:
    #   * `?window=all` is the ONLY value that widens it. Anything else, including a number, gets
    #     the default, so there is no page-size parameter to tune and no second default to drift.
    #   * IT IS NOT REMEMBERED. No cookie, no session key, no `g`. A fresh navigation is seven
    #     again, so the plan-for-the-next-hour stays the thing he lands on and the inventory is
    #     something he asks for on purpose.
    #   * NOTHING LINKS TO IT except the count at the foot of the list, which is the sentence that
    #     was already promising those rows.
    #
    # THE STACK IS NOT TOUCHED AND MUST NOT BE. `/queue/stack` reads `model.queue_items()`, which
    # is its own call at the default window, so run-the-stack keeps running the SEVEN. A stack that
    # ran thirty because he had widened the list would be a different act wearing the same button.
    # `model.queue_view()` with no argument IS the default window, so the narrow arm needs no
    # constant here and there is no second copy of 7 to drift from the one in `human_queue.reads`.
    wide = request.args.get("window") == "all"
    view = model.queue_view(window=10_000) if wide else model.queue_view()
    items, tiers = view["items"], view["tiers"]
    tier = request.args.get("tier") or "decide"
    if tier not in tiers:
        tier = "decide"
    # Deep work is PERSISTED, and it is persisted in a cookie the server reads rather than in
    # localStorage the server cannot see. The fast pane is rendered server side, so a deep work
    # that lived only in the browser would still ship the interrupting HTML down the wire and
    # hide it with CSS. Deep work is absolute: the cards are not rendered, not hidden.
    # HIS RULING, 2026-08-31: deep work does not apply to the Decide tier. `_deep_applies`
    # carries the reasoning and the measurements; this is the one place the queue reads it.
    deep = _deep_applies()
    open_id = request.args.get("open") or None
    burning = [i for i in tiers["decide"] if i["live"]]
    # The shell's deep-work pill asks for this count through `deep_count()`. Handing it the read
    # this room already did is what stops the pill costing the Queue a second `queue_view()` on
    # every three-second poll.
    g._decide_total = view["totals"]["decide"]
    # AND THE WEIGHTS THAT ORDERED THESE ROWS, for the strip above the column header. Stashed on
    # `g` for the same reason `_decide_total` is: the shell's context processor renders that
    # sentence and would otherwise resolve the config a second time, once per render including
    # every three-second patch poll. Context processors run DURING `render_template`, which is
    # after this function has returned, so the value is here before anything reads it.
    #
    # It is also the only way the sentence and the list can be guaranteed to agree. Row 0438's
    # intake badge learned this the same way: two reads of one number can disagree, and the moment
    # they would is exactly the moment the operator has just tuned something and is looking to see
    # whether it took.
    g._queue_weights = view.get("weights")
    open_review = None
    open_item = next((i for i in items if i["id"] == open_id), None)
    if open_item and open_item.get("task"):
        open_review = model.review(open_item["task"])
    with store.read() as s:
        since = s.scalar("SELECT COALESCE(max(seq), 0) FROM brain.thread")
    return {
        "room": "queue", "items": items, "tiers": tiers, "TIERS": model.TIERS,
        # The TIER COUNT IS THE TOTAL AND THE CARDS ARE A WINDOW, so the two are passed
        # separately and the chip renders the total. `tiers[k]|length` was the count while this
        # lane rendered every open item; under D6b's window it would report a plan as if it were
        # the whole obligation, which is the one thing a queue must never round down.
        "totals": view["totals"], "hidden": view["hidden"],
        # WHETHER HE ASKED FOR THE WHOLE TIER. The template needs it because `hidden[tier]` is 0
        # in BOTH states, and the sentence must not vanish when the window is wide: it becomes
        # "showing all 30 of 30" rather than nothing, or the screen stops saying what it shows.
        "window_all": wide,
        "blocked": view["blocked"], "deferred_items": view["deferred_items"],
        "cycles": view["cycles"],
        "tier": tier, "deep": deep, "open_id": open_id, "open_review": open_review,
        "burning": len(burning), "burn_item": burning[0] if burning else None,
        "receipts": _receipts(),
        "zero": model.queue_zero_note() if not view["totals"]["open"] else None,
        "defaults": [i for i in items if i.get("default")],
        "defer": model.defer_options, "since": since,
        # THE STOPWATCH, IN TWO SHAPES WITH TWO COSTS.
        #
        # `stopwatch` is a value, because it is the one thing on this screen that changes on the
        # clock rather than on an event: a running timer's elapsed minutes are stale the second
        # they are rendered, so the strip is a patched region and pays three small reads on every
        # three-second poll.
        #
        # `depth` is a CALLABLE and the template calls it only on a full render, because
        # `reads.depth_and_clearance` reads every open item plus four windows of history. Twenty
        # times a minute for a block the operator opens when he plans his morning would be the
        # console's most expensive read on its most frequent path.
        "stopwatch": model.stopwatch(), "depth": model.depth,
        # The operator's own door (task 0267). Read here rather than in the template, and the
        # same list the Scope room offers, so his work and an agent's are filed under one
        # vocabulary of lanes instead of two.
        "lanes": _lanes(),
    }


def _render_regions(template: str, ctx: dict) -> dict:
    """Every region of a room, rendered by the SAME template that rendered the page.

    Rendering the poll response from a second template is how a console grows two versions of
    one screen; the region ids come out of the one template through a marker block.
    """
    ctx = dict(ctx, patch_only=True)
    html = render_template(template, **ctx)
    out = {}
    marker = "<!--region:"
    idx = 0
    while True:
        start = html.find(marker, idx)
        if start < 0:
            break
        name_end = html.find("-->", start)
        name = html[start + len(marker):name_end]
        end = html.find(f"<!--/region:{name}-->", name_end)
        if end < 0:
            break
        out[name] = html[name_end + 3:end]
        idx = end
    return out


def _perform_attention_native(action: str, item_id: str, text: str) -> dict:
    from .attention_native_door import perform
    return perform(action, item_id, text, source_type=request.form.get('source_type'),
                   port=model.attention_port(), operator=OPERATOR)


def _perform(room: str, action: str, item_id: str, text: str) -> dict:
    if room == 'attention' and action in ('mark_done', 'approve', 'reject'):
        return _perform_attention_native(action, item_id, text)
    # V4'S FOUR ARE ANSWERED BEFORE THE QUEUE IS READ, and the order is the point rather than a
    # micro-optimisation. They act on a RUN: an active task an agent is holding is not in the
    # operator's queue at all -- that is what `agent_claimable` means -- so `_find` returns None
    # for every one of them and the stale-card refusal below would refuse all four. Reading the
    # queue first would also make a steering message depend on every read `queue_view` makes,
    # which is a page's worth of work to send one sentence, and it made this path fail on an
    # unrelated lane's half-applied migration while proving exactly that.
    if action in ("steer_take", "steer_release", "steer_say", "picker_set"):
        return _perform_v4(room, action, item_id, text)
    # THE BOARD'S FOUR DROPS ARE ANSWERED BEFORE THE QUEUE IS READ, for exactly the reason the
    # `steer_*` branch above gives about a run: a PROJECT is not a work item, so `_find` returns
    # None for every slug and the stale-card refusal below would refuse every drop. Bus row 0436.
    # Inserted, not substituted: no line above or below this block was changed.
    if action in ("project_hold", "project_ice", "project_blocked", "project_resume"):
        from . import board as board_mod
        return board_mod.perform(room, action, item_id, text)
    # ------------------------------------------------------- D4: an OBJECTIVE, from the Attention
    #
    # ANSWERED BEFORE THE QUEUE IS READ, for a reason the two branches above do not have and which
    # is the whole justification for this branch existing at all: `_find` is `model.find_item`,
    # which matches `i["id"] == item_id` over the open queue, and an objective's id is a BARE
    # INTEGER AS TEXT -- `brain.objective.id`, the intake arm's own key, which counts from 1 just
    # as every other arm's key does. A bare number therefore names no arm at all. The SQL proof
    # constructed the collision that IS constructible on this store
    # (`outputs/2026-09-10-ATTENTION-OS-admiral/schema-proposal/PROOF.md` case 8: an objective at
    # id 1 beside recommendation 1, two colliding ordered pairs over one view), and keyed lookups
    # on the PAIR returned one row each and the right one. THE SAME CASE MEASURED THE OTHER HALF
    # AND IT IS THE OPPOSITE WAY ROUND: a collision with a WORK ITEM is NOT constructible here,
    # because work item ids are zero padded (`0007`) and no integer's text carries a leading zero.
    # So an objective is resolved by TYPED id through the attention port and never by `_find`,
    # because `_find` on `"1"` would cheerfully hand back somebody else's row and this door would
    # then act on it.
    #
    # SCOPED TO `room == "attention"` AND TO ITS TWO ACTIONS, AND THE SCOPE IS NOT A FORM FIELD.
    # This branch used to take `dispatch_option` only when the form declared
    # `source_type=objective`, which made the typed guarantee conditional on a value the client
    # chose to send: a POST that omitted the field fell through to `_find` on the bare id -- the
    # one lookup the paragraph above exists to forbid -- and so did one naming another arm (R2's
    # F1 on X7, four cells: `source_type` absent and `source_type=work_item` both reached `_find`,
    # with `source_type=objective` and `accept_objective` as the controls that reached the typed
    # read). The branch now takes EVERY `dispatch_option` on this room, and `_perform_objective`
    # refuses a form that declares no `source_type`, or one naming a source this door holds no
    # verb for, IN WORDS and BEFORE ANY LOOKUP. Fail-closed instead of fail-open, and nothing
    # legitimate is lost: `dispatch_option` keeps its room-generic branch further down for every
    # other room -- the queue card's option rail posts to `/<room>/act` (`web/templates/macros.html`)
    # and never here -- and no control on the Attention surface posts `dispatch_option` at all.
    # Nothing else in this function is touched, and no line above or below this block moved.
    if room == "attention" and action in ("accept_objective", "dispatch_option"):
        return _perform_objective(room, action, item_id)
    item = _find(item_id)
    # `scope_post` and `post_mine` create a row rather than acting on one, so there is nothing
    # to go stale.
    #
    # THE STOPWATCH IS THE THIRD SHAPE: neither of its two verbs is an act on a live card.
    # `time_stop` names no item at all -- it stops whatever is running, and the ordinary case is
    # that the item LEFT the queue between the start and the stop, because clearing it is what he
    # was timing. Refusing the stop as a stale card would strand the entry running until the cap
    # relabelled it abandoned, which is the one outcome the cap exists to make rare.
    # `time_start` accepts an id that is not in the queue for the reason the ledger states: an
    # item outside a live tier is timed with `tier_at_start = NULL`, counted in the overall
    # figure and in no tier's, rather than refused.
    # V5's three image actions join the stopwatch's exemption and for the same reason: an image
    # is attached where the item is READ, and `/task/<id>` renders items that left the queue an
    # hour ago. `detach_image` and `discard_image` name an ATTACH id rather than a card at all.
    # Refusing them as stale cards would put the one surface where a report is actually read
    # outside the lane. The subject still has to resolve -- migration 29's trigger refuses an
    # attachment on a row that does not exist -- so this exemption widens the surface, not the
    # guarantee.
    # ROW 0413 ADDS `unaccept` TO THAT EXEMPTION, and it is the cleanest case of it. `Accept
    # work` is precisely the act that takes the row OUT of `brain.queue_open` (arm 1 is
    # `state = 'done' AND accepted_at IS NULL`), so the stale-card guard would refuse the undo of
    # every acceptance, always, on the ground that the acceptance worked. The id is still checked
    # against `brain.work_item` by the verb itself, which refuses a row that is not accepted with
    # a sentence saying so rather than reporting success for having changed nothing.
    if item is None and action not in ("brief_reopen", "scope_post", "post_mine",
                                       "time_start", "time_stop", "time_abandon",
                                       "attach_image", "detach_image", "discard_image",
                                       "unaccept"):
        raise actions.ActionRefused(
            f"{item_id!r} is not in the queue any more. Somebody or something else resolved it, "
            f"and acting on a stale card is how two surfaces disagree.")
    if action == "post_mine":
        return actions.post_mine(room, title=text, lane=request.form.get("lane") or "",
                                 operator=OPERATOR)
    if action == "scope_post":
        return actions.scope_post(room, intent=request.form.get("intent") or "",
                                  lane=request.form.get("lane") or "",
                                  rows=scoping.from_form(request.form), operator=OPERATOR)
    if action == "answer":
        return actions.answer(room, qid=item_id, text=text, item=item, operator=OPERATOR)
    if action == "amend":
        return actions.answer(room, qid=item_id, text=text, item=item, operator=OPERATOR,
                              amend=True)
    if action == "accept_default":
        return actions.accept_default(room, item=item, operator=OPERATOR)
    if action == "accept_work":
        return actions.accept_work(room, item=item, operator=OPERATOR)
    if action == "send_back":
        return actions.send_back(room, item=item, reason=text, operator=OPERATOR)
    if action == "mark_done":
        return actions.mark_my_task_done(room, item=item, summary=text, operator=OPERATOR)
    if action == "undo_done":
        return actions.undo_done(room, item=item, operator=OPERATOR)
    if action == "unaccept":
        # `text` is empty from the receipt stripe and the stack ticker, both of which live inside
        # the receipt window and neither of which has a field; `actions.unaccept_work` supplies
        # the reason that is true of that door and names it. `/task/<id>` posts a typed one.
        return actions.unaccept_work(room, item_id=item_id, reason=text, operator=OPERATOR)
    if action == "defer_question":
        # D6b's `queue defer --kind until-question` posts the child task AND records the wake
        # condition in one transaction. Before it landed the console did the `post` half only,
        # which left a child task and no link back. `defer_with_question` is kept for the case
        # where the queue tables are not present.
        if "queue defer" in store.registered():
            return actions.defer(room, item=item, kind="until-question", question=text,
                                 operator=OPERATOR,
                                 acknowledge_default=_ack(request.form))
        return actions.defer_with_question(room, item=item, question=text,
                                           lane=request.form.get("lane") or "", operator=OPERATOR)
    if action == "decline":
        if "queue defer" in store.registered():
            return actions.defer(room, item=item, kind="decline", reason=text, operator=OPERATOR,
                                 acknowledge_default=_ack(request.form))
        return actions.decline(room, item=item, reason=text, operator=OPERATOR)
    if action == "defer_time":
        # The chip posts a SPEC and the wake time is computed HERE, from the moment the operator
        # ticked it. `wake_at` is still honoured for a caller that states an absolute time, which
        # is what the queue CLI does.
        spec = request.form.get("wake_in") or ""
        return actions.defer(room, item=item, kind="until-time", operator=OPERATOR,
                             wake_at=(model.wake_at_from(spec) if spec
                                      else request.form.get("wake_at") or ""),
                             label=request.form.get("label") or "",
                             acknowledge_default=_ack(request.form))
    if action == "bump":
        return actions.bump(room, item=item, reason=text, operator=OPERATOR,
                            delta=float(request.form.get("delta") or 1.0))
    if action == "voice_note":
        # ROW 0432, HIS ASK 3. `text` is deliberately NOT passed and there is no branch here that
        # would accept one: the only body this action will ever post is a transcript a
        # transcription engine produced. That is the structural half of MUST-NOT-BUILD item 5's
        # overrule condition, and it lives here as well as in `actions.voice_note` because this is
        # the line a future edit would most easily route the typed field through.
        #
        # `wav` IS NOT READ FROM THE REQUEST AND MUST NEVER BE. `actions.voice_note` takes a `wav`
        # path so a test can drive the real function with a fixture recording instead of a
        # microphone. Wiring that parameter to `request.form` would turn this door into an
        # arbitrary local file reader: a crafted POST naming any path on the host would have it
        # copied into the retain directory, run through a transcription engine, and its contents
        # written onto a task thread. The console is loopback-only, which is a reason to be
        # careful here rather than a reason not to be.
        return actions.voice_note(room, item=item, operator=OPERATOR,
                                  seconds=request.form.get("seconds"))
    if action == "not_fast":
        return actions.not_fast(room, item=item, operator=OPERATOR, note=text)
    if action == "approve":
        return actions.recommend(room, item=item, accept=True, operator=OPERATOR)
    if action == "reject":
        return actions.recommend(room, item=item, accept=False, operator=OPERATOR, reason=text)
    if action == "time_start":
        # `who` is NOT read from the request and never will be: it is OPERATOR, the one name this
        # console acts under. See `web/actions.py`'s stopwatch header.
        return actions.time_start(room, item=item, item_id=item_id,
                                  source_type=request.form.get("source_type") or "",
                                  operator=OPERATOR, note=text)
    if action == "time_stop":
        return actions.time_stop(room, operator=OPERATOR, note=text)
    if action == "time_abandon":
        # The same verb with `abandon=True`. A separate console action rather than a checkbox
        # because it answers a different question -- "that was not work on this item" -- and the
        # receipt it produces has to say so; both land the same `abandoned` label the cap does.
        return actions.time_stop(room, operator=OPERATOR, abandon=True, note=text)
    if action == "attach_image":
        # `subject_type` rides the form because the same control renders on three kinds of card
        # and the store's vocabulary is the queue's, not the console's word for it. It is
        # validated by `image attach` against a closed list AND by migration 29's CHECK.
        return actions.attach_image(room, subject_type=request.form.get("source_type") or
                                    (item or {}).get("source_type") or "work_item",
                                    subject_id=item_id,
                                    path=request.form.get("path") or "", operator=OPERATOR)
    if action == "detach_image":
        return actions.detach_image(room, attach_id=request.form.get("attach_id") or "",
                                    operator=OPERATOR)
    if action == "discard_image":
        return actions.detach_image(room, attach_id=request.form.get("attach_id") or "",
                                    operator=OPERATOR, discard=True)
    # ---------------------------------------------------------------- V3: the option rail
    # ONE BRANCH AND NO NEW ROUTE. `dispatch_option` calls `recommend accept`, which the Queue's
    # allowlist has held since D7; the ordinal rides the form and the option row is re-read from
    # the store by `web/model.py::option_for`, never taken off the card that posted it.
    if action == "dispatch_option":
        return actions.dispatch_option(room, item=item, n=request.form.get("option"),
                                       operator=OPERATOR)
    if action == "brief_reopen":
        w = R.work_item(item_id)
        if not w:
            raise actions.ActionRefused(f"no work item {item_id}")
        return actions.send_back(room, item={"id": item_id, "kind": "review",
                                             "gates": [], "title": w["title"]},
                                 reason=text or "reopened from the brief: the default that "
                                                "shipped needs a second look",
                                 operator=OPERATOR)
    raise actions.ActionRefused(f"no such console action: {action!r}")


def _perform_objective(room: str, action: str, item_id: str) -> dict:
    """The attention branch of `_perform`, and the whole of D4's act half in this file.

    A SIBLING FUNCTION FOR THE SAME REASON `_perform_v4` AND `board.perform` ARE ONE: the branch
    above is one `if`, and the argument for why an objective is not resolved like every other row
    is four paragraphs long. It is called from that branch and from nowhere else, and it
    reimplements no transition: both verbs already exist and both go out through
    `rooms.dispatch` -> `store.apply` like every other write this console makes.

    ========================================================= WHY NOT `_find`, WHICH IS THE POINT

    `_find` is `model.find_item`, which matches `i["id"] == item_id` over the whole open queue.
    An objective's `source_id` is `brain.objective.id` as text -- a bare integer -- and the other
    arms' ids are not disjoint from it: `brain.recommendation.id` is an integer too. PROOF case 8
    constructed the collision on a disposable store (an objective at id 1 beside recommendation 1)
    and watched the KEYED lookup return one row each and the right one, because the port keys on
    the PAIR `(source_type, source_id)`. An untyped match would have had two candidates and no way
    to choose, and this door would then have accepted an objective named by a recommendation's id.

    So the row is read through `model.attention_port().item("objective:<id>")`, which is the same
    read the inspector makes, and the refusals below are what a person sees when it comes back
    empty. THE PORT'S OWN GATE IS PART OF THAT READ and is not bypassed here: an objective with no
    option is not decidable, is not on this page, and is refused by this door in the same words as
    one that never existed. A control is never offered for a row this function would refuse.

    ============================================================== WHOSE LOGIN THIS WRITE OPENS,
    stated precisely rather than in the reassuring direction, because the difference is the fourth
    carve-out in this repo's `CLAUDE.md`.

    `accept` (`engine/swarm_engine/transitions.py:1813`) takes `name` and nothing else, so this
    call cannot pass `as_operator=True`: `store/transitions.py::apply` does NOT pop that keyword
    (only `as_human` is popped), and a transition that has not declared it raises TypeError. The
    verb therefore opens the runtime login here exactly as it does for its only other callers --
    `swarm accept <name>` in a terminal and the Scope room -- and this console neither weakens nor
    strengthens that. What IS true of this door: the request arrives on the person's own console
    session, carrying the room's own token, from a surface no agent posts to, and there is no
    unattended path to it. Making `accept` demand `brain.current_human()` the way `accept work`
    does is a change to an engine transition and a migration; it is worth proposing and it is NOT
    smuggled in here, and this paragraph is where a reviewer should start if they want it.
    """
    # ============================================ THE DECLARED SOURCE, CHECKED BEFORE ANY LOOKUP
    #
    # R2's F1 on X7. The branch above used to read this field to decide its OWN scope, so a form
    # that left it out routed itself to `_find`. It is still read, and it is now read as a
    # DECLARATION TO CHECK rather than as a route: it is never authority -- the row is re-read by
    # typed id below whatever the form says -- but a post that declares nothing, or declares a
    # source this door holds no verb for, is refused BY NAME here rather than looked up on a bare
    # integer somewhere else in the queue. Both refusals happen before `port.item`, so a refused
    # form costs no read.
    declared = (request.form.get("source_type") or "").strip()
    if not declared:
        raise actions.ActionRefused(
            "this form carries no `source_type`, and the Attention door will not look a row up "
            "without one. Every control this page renders declares it, so a post that does not "
            "is a stale page or a hand-made request; the id it carries is a bare number, and a "
            "bare number names no arm on its own. Reload the page and press the control again.")
    if declared != "objective":
        raise actions.ActionRefused(
            f"this door holds no verb for a row of source {declared!r}. `accept_objective` and "
            f"`dispatch_option` on the Attention room are arm 5's verbs and resolve the row as "
            f"`objective:<id>` through the attention port; a row from another arm is acted on in "
            f"the room that owns it, not here. Refused rather than looked up on the bare id.")
    typed = "objective:" + item_id
    row = model.attention_port().item(typed)
    if row is None:
        raise actions.ActionRefused(
            f"there is no objective {item_id!r} on this queue. Either it is not an intake item "
            f"waiting on you any more -- an accepted objective leaves the intake arm, and a "
            f"second accept of the same row lands exactly here -- or it carries no option, in "
            f"which case the queue gate never admitted it and this page never offered a control "
            f"for it. This door reads the row by its typed id ({typed!r}) rather than by the bare "
            f"one, because the bare id is only the intake arm's own key -- `brain.objective.id` "
            f"counts from 1 exactly as every other arm's key does -- so a bare number names no "
            f"arm at all, and this console refuses rather than guesses which one you meant.")
    if row.kind != "objective":
        raise actions.ActionRefused(
            f"{typed!r} came back as kind {row.kind!r}, not 'objective'. That is the coupled half "
            f"of this admission missing rather than a bad request: `web/model.py::KIND_BY_ARM` "
            f"gains the two objective entries in Platform's hunks, and without them an objective "
            f"row falls through the `(source_type, None)` lookup to 'review' and would be acted "
            f"on as finished work. Refusing is the fail-closed direction; the migration must not "
            f"land without those hunks (staging packet section 4).")
    # The CARD the guards are handed. `assert_allowed_on` reads `brain.work_item` for `done` and
    # for no other verb, so neither of these two reaches it -- but a dict that lied about its own
    # source_type would still be wrong, and `actions.dispatch_option` reads both of these keys to
    # re-read the option row from the store.
    item = {"id": item_id, "source_type": "objective", "kind": row.kind, "title": row.title}
    if action == "dispatch_option":
        # RIDES THE EXISTING IMPLEMENTATION AND ADDS NOTHING. `actions.dispatch_option` re-reads
        # the option by ordinal through `model.option_for`, refuses a sprint, refuses an already
        # dispatched option and refuses one that is not open, then dispatches `recommend accept`.
        # None of that is duplicated here and none of it needed a change to take an objective:
        # `option_for` keys on `(source_type, source_id)` and `queue/human_queue/options.py::_SETS`
        # filters by neither. The objective is NOT touched by this act, which is D4's structural
        # sentence: creating a task never counts as completing it.
        return actions.dispatch_option(room, item=item, n=request.form.get("option"),
                                       operator=OPERATOR)
    res = rooms.dispatch(room, "accept", item=item, actor=OPERATOR, name=row.title)
    return {
        "verb": "accept",
        # The store's fact, in the person's words, and it says where the proposals went precisely
        # because they did NOT come with it: `brain.recommendation` rows about this objective are
        # untouched by the transition and are read on the objective's own page.
        "receipt": f"{row.title} accepted into the store; its proposals stay on its own page",
        # E3/E4. `brain.objective.state` is CHECKed to ('inbox','accepted') (migrations/0001:330):
        # there is no `unaccept` for an objective and no `declined` state, so this stripe says "no
        # undo" and then the server's own reason, VERBATIM, rather than offering a control that
        # would 403 or, worse, one that looks like it worked.
        "undo": None,
        "no_undo_reason": "an accepted objective has no inverse verb yet",
        "result": res,
    }


def _perform_v4(room: str, action: str, item_id: str, text: str) -> dict:
    """Take-control and the picker. The item is the RUN's work item, read from the table.

    `picker_set` names an AGENT rather than a work item, which is why it is the one branch here
    that builds no item at all: it changes what a terminal will use at its next claim, and that
    is about no row.
    """
    agent = (request.form.get("agent") or "").strip()
    if action == "picker_set":
        values = {}
        for key in ("model", "effort", "account"):
            if key in request.form:
                values[key] = request.form.get(key) or ""
        return actions.set_for_next_claim(room, agent=item_id or agent, values=values,
                                          operator=OPERATOR)
    w = R.work_item(item_id)
    if not w:
        raise actions.ActionRefused(
            f"there is no work item {item_id!r}. Take-control is control of a RUN, and this "
            f"request names no row for a run to be on.")
    item = {"id": item_id, "title": w.get("title") or "", "kind": "run", "gates": []}
    if action == "steer_take":
        return actions.take_control(room, agent=agent, item=item, operator=OPERATOR,
                                    attempt=int(request.form.get("attempt") or 0) or None)
    if action == "steer_release":
        return actions.release_control(room, agent=agent, item=item, operator=OPERATOR)
    return actions.steer_say(room, agent=agent, item=item, text=text, operator=OPERATOR)


def _said_html(entry: dict, name: str) -> str:
    """One thread row rendered as a beat. The operator's carry a brand edge and a `you ->` head.

    Escaped, every field, because a thread row is agent-written text and the console already
    learned that once: `say()` in console.js uses textContent for the same reason.
    """
    from markupsafe import escape
    at = entry["at"].strftime("%H:%M:%SZ") if entry.get("at") else ""
    if entry["kind"] == "mark":
        return f'<div class="mark" data-key="{entry["key"]}">{escape(entry["text"])}</div>'
    head = f"you \u2192 {escape(name)}" if entry.get("operator") else escape(entry["who"])
    return (f'<div class="ev{" mine" if entry.get("operator") else ""}" '
            f'data-key="{entry["key"]}">'
            f'<div class="evh">{head} \u00b7 {escape(entry["verb"])} \u00b7 {at}</div>'
            f'<div class="evb">{escape(entry["text"])}</div></div>')


# --------------------------------------------------------------------------- the picker's shape
#
# EVERY OPTION CARRIES ITS COUNTERARGUMENT, which is a shell-wide rule and not a card-only one:
# "Never show an option without its counterargument". A model select whose options are three bare
# words is three choices with no stated cost, and an operator picking one of them is guessing.

_MODELS = [
    {"value": "opus", "against": "the fleet's default; dearest per run"},
    {"value": "sonnet", "against": "cheaper and faster; weaker on long verification chains"},
    {"value": "haiku", "against": "cheapest; not for work that has to verify itself"},
]
_EFFORTS = [
    {"value": "", "label": "unset", "against": "the engine's own default"},
    {"value": "low", "label": "low", "against": "quickest; skips the second look"},
    {"value": "medium", "label": "medium", "against": "the middle; no stated failure mode"},
    {"value": "high", "label": "high", "against": "slowest and dearest; what a planner runs on"},
]


def _picker(name: str, f: dict) -> dict:
    """What this run IS using, and what the next claim WILL use. The split is the meaning.

    `this run` reads the model from the run's own `system/init` event -- the engine's announcement
    of what it started under, which is a fact about THIS run. Reading it from config would report
    a change made five minutes ago as though it had been in force all along, which is exactly the
    "looks instant" the brief forbids.

    ONLY `model` is taken from that event. It also carries `permissionMode`, and nothing in this
    console reads that key from anywhere: see `web/runfeed.py`'s `_SUPPRESSED_KEYS`.
    """
    from swarm_engine import config as engine_config
    resolved = engine_config.resolved(name)
    ring = list(resolved.get("config_dirs") or
                ([resolved["config_dir"]] if resolved.get("config_dir") else []))
    ov = resolved.get("_override") or {}
    cur = next((b for b in f.get("runs") or [] if b["current"]), None)
    run_model = runfeed.run_model(cur["path"]) if cur else ""
    if cur and not cur["run"].get("ended_at"):
        this_run = (f"{run_model or resolved.get('model') or 'model unstated'} \u00b7 "
                    f"{(ring[0] if ring else 'default account')} \u00b7 "
                    f"effort {resolved.get('effort') or 'unset'} \u2014 this run, unchangeable")
        why = ("the model is the engine's own `init` announcement for this run; the account and "
               "effort are what config resolves now, and a change made since the run started did "
               "not reach it")
    else:
        this_run = "no run in flight"
        why = "nothing is executing, so there is nothing this picker cannot change"

    pending = applied = ""
    if ov:
        consumed = fleet_config.consumed_by(name, ov.get("set_at"))
        said = " \u00b7 ".join(f"{k} {v or 'unset'}" for k, v in sorted(ov["values"].items()))
        if consumed:
            applied = (f"applied: {said} \u2014 consumed by {consumed['work_item_id']} attempt "
                       f"{consumed['attempt']}")
        else:
            pending = f"{said} \u2014 set by {ov.get('set_by') or 'somebody'}, no claim has taken it yet"
    return {
        "models": _MODELS, "efforts": _EFFORTS,
        "accounts": [{"value": d, "present": os.path.isdir(os.path.expanduser(d))} for d in ring],
        "model": resolved.get("model") or "", "effort": resolved.get("effort") or "",
        "account": ring[0] if ring else "",
        "this_run": this_run, "this_run_why": why,
        "pending": pending, "applied": applied,
    }


def _ack(form) -> bool:
    """Deferring past a silence window is an INTERSTITIAL, never a side effect.

    D6b's `queue defer` refuses until the caller has acknowledged what silence will ship. The
    console renders that acknowledgement as a checkbox on the defer form and passes through what
    the operator actually ticked. It never sets it on his behalf: an acknowledgement the operator
    did not give is exactly the silence-uninformed case the DEFAULTED discipline forbids.
    """
    return (form.get("acknowledge_default") or "") in ("1", "on", "true", "yes")


def _find(item_id: str) -> dict | None:
    """The whole queue, not the window. `model.find_item` states why."""
    return model.find_item(item_id)


app = create_app()
