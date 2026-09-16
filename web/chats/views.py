"""Chats history routes: no send action and no database write.

Two GET routes over the file queue and the transcript reader. The index GET also calls
registry.ensure_root(), which may create the local queue scaffold. These routes are not
filesystem-read-only. They do not register agents or consume queue messages. There is no POST here, no form, no
`action=`, and no verb table entry for this room** -- so `rooms.assert_room_can_act` refuses `chats`
before a row is looked up, exactly as it refuses Study and Sessions. **That is the mechanism, not a
promise: a future POST would have to add itself to `ROOM_VERBS`, which is a file with a different
owner and a reviewer.**

WHY THESE TEMPLATES EXTEND NOTHING, AND IT IS A RULE RATHER THAN A STYLE CHOICE

`base.html` is in NO seat's declared file set. `LAUNCH-HELD-SEATS` §2: *"`base.html` and `run-all.sh`
are in NO seat's set. If your work needs either, ask; do not take."* **So these templates extend
nothing**, which is the shape `stack.html` already uses deliberately, and **the cost is stated
rather than discovered**: the shell chrome never runs for this surface, so anything `base.html`
provides -- the nav, the theme script, the favicon link -- has to be repeated here or be absent.
**Absent is the honest default and what is absent is named on the page.**

THE PACKAGE AND THE APPLICATION MOUNT ARE ONE ADMISSION

Importing this package adds no application routes. Attention owns the paired import and
chats.register(app) call in web/app.py. R29 selects one combined admission. A rendered page
must not claim to be unmounted merely because the package was prepared separately.

WHAT IS DELIBERATELY NOT HERE

No `EventSource`, no `WebSocket`, no service worker, no manifest, no cache layer, no PWA. No shell
emulation, no command entry, no pane that executes. **No composer, no textarea, no
`contenteditable`.** `MUST-NOT-BUILD.md` item 11's security half at `0180a51`, re-armed and standing
in full. **And no polling script at all: these pages are static once served.**
"""

from __future__ import annotations

import os

from flask import Blueprint, abort, render_template, request

from . import history, registry, roundtrip

#: The nav label. `MUST-NOT-BUILD.md` item 11 as amended 2026-09-08: *"The nav label, headings and
#: empty states read CHATS."* **The back end keeps its names** -- sessions, transcripts, the capture
#: hook, the nav key `terminals` -- and **the rename applies to the PLURAL SECTION only**, so
#: nothing here renames a single Terminal view.
NAV_LABEL = "CHATS"

#: Turns per page. Modest on purpose: the whole file is streamed for the counts either way, so the
#: page size buys nothing on the read and costs a reader everything on the render. A 6,190-turn
#: transcript rendered whole is a page nobody can use.
PAGE = 50

#: The one sentence this surface must never lose, and it OPENS with the refusal.
#:
#: **A measured correction to my own brief, which said this sentence was "restored" from a deletion.
#: On the integration line it was never there.** `git log -S"You cannot send from here" 56838e5 --
#: web` returns NOTHING; the sentence exists once at `4d5222d` in `web/templates/live_session.html`,
#: on `mv/c2-terminals-session-sync-r1`, which `git merge-base --is-ancestor` shows is **NOT an
#: ancestor of `56838e5`**. It was added there by `8be520a` and never removed. **So this is written,
#: not restored, and the absence on this line is a never-merged state rather than a deletion.**
#:
#: The prior lane's own finding about it, which is why it leads rather than trails: *"it OPENS with
#: 'You cannot send from here' ... leading with the refusal is what makes a composer-shaped box
#: safe, because nobody hover-tests a cursor before forming an impression."* And: the sentence was
#: once set at 11.2px under 13.28px message text, and **small grey text is how an interface says
#: footnote, skippable.** It is not smaller than the content it governs here either.
CANNOT_SEND = (
    "You cannot send from here. This reads files that agents and Claude Code append to; it is a "
    "record of conversations, not a way into one. Interactive terminal release remains held "
    "pending the operator's security decision."
)

#: What this surface cannot do, in the words of what is missing. Acceptance clause 3.
UNBUILT = (
    ("This page has no shared shell navigation.", "The Chats templates are standalone pages. "
     "They do not include the shared navigation, theme toggle, or favicon."),
    ("Nothing here updates on its own.", "There is no poll, no socket and no push. Reload to see "
     "new messages. A live update path is not built and is not being built."),
    ("Agents on other machines cannot appear here.", "Registration is a local file queue. Whether "
     "an agent not on this machine may register is an operator decision that has not been made."),
)


def build(base: str | None = None) -> Blueprint:
    """The blueprint. `base` is the queue root; `None` means `registry.root()`."""
    bp = Blueprint("chats", __name__, url_prefix="/chats",
                   template_folder=os.path.join(os.path.dirname(__file__), "..", "templates"))
    side = roundtrip.OSSide(base=base)

    @bp.get("/")
    def index():
        agents = registry.registered(base)
        counts = side.queue_state()
        return render_template(
            "chats/index.html", nav_label=NAV_LABEL, cannot_send=CANNOT_SEND, unbuilt=UNBUILT,
            agents=agents, counts=counts, root=registry.ensure_root(base),
        )

    @bp.get("/<agent_id>")
    def agent(agent_id: str):
        try:
            record = registry.load(agent_id, base)
        except ValueError:
            # A URL outside the minted ID shape does not identify a registration.
            abort(404)
        if record is None:
            # 404 rather than an empty page. An empty page for a name that does not exist is a
            # page that says the agent has no messages, which is a different and false claim.
            abort(404)
        # PAGING, AND IT IS PLAIN LINKS. Andrew's word was "scrollable", and a page that showed 50
        # of 6,190 turns and stopped was not that. There is no script here and there is no infinite
        # scroll: infinite scroll needs a fetch on a scroll event, and this surface has neither.
        #
        # An unparseable `offset` becomes 0 rather than a 500. A malformed query string is a person
        # editing a URL, and the useful answer to that is the first page, not a stack trace.
        try:
            offset = max(0, int(request.args.get("from", "0")))
        except (TypeError, ValueError):
            offset = 0
        # SEARCH IS A QUERY PARAMETER AND THERE IS NO SEARCH BOX, AND THAT IS A DECISION I DID NOT
        # TAKE ALONE. An `<input>` would be the first typable element on a surface whose entire
        # safety story is that it has none -- `surface_proof.py` asserts zero `<input>`, `<form>`,
        # `<textarea>` and `<button>`, which is what makes "this cannot be typed into" CHECKABLE
        # rather than argued. A search box is a read filter and not command entry, so item 11 does
        # not forbid it; **but trading a structural invariant for a convenience on THIS surface is
        # an operator question and it is flagged rather than answered.** `?q=` works today for
        # anyone with a URL, and the page says so.
        query = (request.args.get("q") or "").strip()
        window = None
        if record.transcript_verified and record.transcript_path:
            if query:
                window = history.search(record.transcript_path, query,
                                        offset=offset, limit=PAGE)
            else:
                window = history.read_window(record.transcript_path, offset=offset, limit=PAGE)
        return render_template(
            "chats/agent.html", nav_label=NAV_LABEL, cannot_send=CANNOT_SEND,
            agent=record, window=window, counts=side.queue_state().get(agent_id, {}),
            page=PAGE, offset=offset, query=query,
        )

    return bp


def register(app, base: str | None = None) -> None:
    """Mount Chats on `app`. **ONE LINE OF CONTACT, mirroring `terminal.register(app)`.**

    `web/app.py` already carries this exact shape for the typable pane -- `terminal.register(app)`,
    **at line 1555 as of `130fe86`, and it was 1554 at `56838e5`** -- and its comment is the reason:
    *"ONE LINE, AND IT IS THE WHOLE CONTACT THIS FILE HAS WITH THAT LANE … two entries here would be
    two edits in a module three other lanes are also working in."*

    **THE LINE NUMBER MOVED UNDER ME BETWEEN ASKING AND BEING ANSWERED, WHICH IS WHY BOTH ARE
    WRITTEN HERE.** I sent a ruling request naming `web/app.py:1554`; the first runtime merge landed
    at `130fe86`, `web/app.py` was one of its seven runtime paths, and the anchor is now 1555.
    **A line number is a measurement with a date on it exactly as a SHA is**, and this one decayed
    inside an hour. **Cite the anchor `terminal.register(app)`, not the integer.**

    **This function exists so that the grant I am asking for is as small as it can be.** Every
    decision about routes, prefixes and templates stays in this file, which is mine; what
    `web/app.py` would gain is `chats.register(app)` and nothing else.

    **WHY THERE IS NO LOOPBACK GATE HERE, unlike `terminal.register`.** That one registers a
    different route set off loopback because it carries a pane that EXECUTES, and R10b requires
    that pane to refuse a non-loopback origin server-side. **This surface executes nothing and
    has no database write or send action. The index may create a local scaffold.** R10a permits the dashboard across the tailnet, so
    gating these routes by origin would be inventing a prohibition the ruling does not make —
    *"which is the failure this file names as having been paid for three times."*

    **What is NOT loopback-optional and is not here at all: registration.** That is a file queue
    with no socket, and whether it may be reached from another host is `BLOCKED-ON-OPERATOR`.
    """
    app.register_blueprint(build(base))
