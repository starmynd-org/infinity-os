"""Chats: how an agent reaches this OS, and how this OS reaches an agent.

THE INTERFACE WORD IS "CHATS" AND THE BACK END KEEPS ITS NAMES. `MUST-NOT-BUILD.md` item 11 at
`0180a51`, amended 2026-09-08: *"The nav label, headings and empty states read CHATS. The back end
keeps its names -- sessions, transcripts, the capture hook, the CLI, the data model, and the nav key
`terminals` are all unchanged."* And: *"The rename applies to the PLURAL SECTION only."* So this
package is `chats` because it is the plural section's interior; every session it registers is a
`session`, every transcript a `transcript`, and nothing here renames a single Terminal view.

WHAT THIS IS. A local file queue that lets an agent process on THIS machine announce itself to the
OS, receive work, act, report back, and wait on its queue without burning tokens on a polling loop.
It is the same shape `BUS-PROTOCOL-2026-09-09.md` proves on the bus this fleet runs on.

WHAT THIS IS NOT, and the list is the security half of item 11, re-armed and standing in full:

    no shell emulation          no command entry             no pane that executes
    no tunnel                   no reverse proxy             no Tailscale Funnel
    no public exposure          no cache layer               no service worker, no PWA
    no EventSource              no WebSocket

**THE ONE INVARIANT THAT MAKES MOST OF THAT UNREACHABLE RATHER THAN MERELY UNBUILT.** Nothing in
this package opens a socket. `grep -nE "socket|bind|listen|serve" web/chats/*.py` is the check,
and the reason it is worth more than a promise: *an invariant that makes a state unreachable beats a
test that visits the state.* A queue whose transport is `os.replace` on a local filesystem has no
port to expose, no origin to spoof and no tunnel to forget to close. **"Reachable from off this
host" is not a property this transport can have.**

**THE OTHER INVARIANT, AND IT IS BORROWED FROM `web/guard.py` DELIBERATELY.** `guard.py`'s opening
finding, verbatim: *"An allowlist keyed on an attacker-supplied string is an allowlist keyed on
nothing."* Its repair was to take the room from the ROUTE instead of from the payload. **Here the
route is the DIRECTORY a message was found in.** An agent's identity is which inbox held the file,
never the `FROM:` line inside it, because the `FROM:` line is a string the sender chose.
`BUS-PROTOCOL` §4 says the same thing from the other end: **a message is never authorization.**

WHAT IS DELIBERATELY NOT HERE. No write crosses a socket, so nothing here goes near `/<room>/act`,
which item 11 condition 2 reserves for every state write. The Postgres seam -- `session_register`,
`session_end`, `transcript_index` in `ingest/ingest/verbs.py` -- is an EXISTING door owned by other
seats, and this package does not call it and does not reimplement it. See `README.md` for why that
line is where it is.
"""

from __future__ import annotations

__all__ = ["protocol", "registry", "roundtrip", "wake", "views", "register"]


def __getattr__(name: str):
    """Resolve `chats.register` and `chats.views` ON ACCESS, not at import. PEP 562.

    **THE POINT OF THE LAZINESS, AND IT IS NOT STYLE.** `web/app.py` must be able to write
    `chats.register(app)` -- one line of contact, the shape `terminal.register(app)` already sets --
    **without this package's internal module layout leaking into a file three other lanes write
    in.** That is what `2026-09-09-IOS-admiral` repaired R16 to preserve rather than spend.

    **But the obvious spelling breaks three of this package's five proofs, and I measured it before
    choosing this one.** A plain `from .views import register` here would pull **flask** into the
    package import path, and `python -c "import flask"` on this box's WINDOWS interpreter returns
    `ModuleNotFoundError`. `roundtrip_proof.py`, `harness_registration_proof.py` and
    `shim_proof.py` all run under that interpreter and import `web.chats.protocol` / `registry`,
    **which executes this file first.** They would fail at import, having nothing to do with flask.

    So: `import web.chats` still costs nothing and touches no web framework, and
    `chats.register(app)` resolves the moment `web/app.py` calls it -- **in a process that has
    already imported flask to exist at all.**

    **`__all__` lists `views` and `register` because the ruling asks for it and because they are the
    package's public surface.** `dir()` and `from web.chats import *` see them; **the import cost is
    still not paid until one is touched.**
    """
    if name in ("views", "register"):
        # `importlib.import_module`, NOT `from . import views`. THE OBVIOUS SPELLING RECURSES
        # FOREVER: `from . import views` runs the import system's `_handle_fromlist`, which does a
        # `getattr(package, "views")` -- landing back in this function -- and the stack ends in
        # `RecursionError`. Measured, not reasoned about: my first version did exactly that.
        #
        # `import_module` binds the submodule in `sys.modules` and returns it without re-entering
        # here, and the import system then sets it as a real attribute, so this runs ONCE.
        import importlib
        _views = importlib.import_module(f"{__name__}.views")
        return _views if name == "views" else _views.register
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
