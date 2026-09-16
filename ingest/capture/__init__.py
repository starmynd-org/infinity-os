"""Durable revision-aware capture: the part of Infinite Intake that must never lose an event.

The invariant this package exists to hold is invariant 1 of `INFINITE-INTAKE-END-TO-END-FLOW.md`:

    Never mark a source item handled, clean it up, react to it, or acknowledge it until its
    durable capture receipt exists.

Everything here is arranged so that the only way to obtain a `CaptureReceipt` is for the raw bytes
to be in custody AND the journal row to be committed. The outbox -- the thing that lets a
connector mark an email read -- takes a receipt as its input and cannot be driven any other way.

WHAT IS DRAFT HERE. `contracts.py` is a LOCAL DRAFT of the C01 CaptureRecord/Revision/Receipt
wire schema, because C01 has not published (`infinite-brain-harness/contracts` does not exist at
the time of writing). Its digest is recorded in the CAP04 handoff. Every other module imports the
shapes from `contracts` and nowhere else, so swapping in the pinned C01 version is one file.

WHAT IS A PORT. `state_port.JournalStore` is the seam onto R01's state API, which is also
unpublished. `sqlite_store.SqliteJournalStore` is a reference implementation on a disposable file
so the failure model can be tested today. No Postgres is touched by anything in this package.
"""
