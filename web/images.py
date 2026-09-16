"""V5's image pipeline (V10-C SPEC sections 3.3-3.4): sha256 verification, MISSING/CHANGED
computation at render time, and the attach pipeline (read, then hash, then record).

Created as a SHELL-0 stub by task 0156 so the module path existed before this lane did. Filled
by task 0167 (V5), 2026-08-18.

THE POSTURE, IN ONE SENTENCE: an image is pointed at, hashed, and its absence is a finding.

Nothing in this file ever holds image bytes for longer than one 1 MiB chunk, and nothing in it
puts a byte anywhere but on a socket to the browser. The bytes stay on disk, the store holds a
pointer, a host, a sha256 and a byte count, and the console re-measures the last two AT RENDER
TIME rather than trusting what was written. That is the whole design, and the reason for it is
the one v1 already paid for: `brain.artifact.exists_at_record` records what was true when the
row was written, and v1 found a path recorded as present that was not on disk. It rendered that
as a finding. This module does the same thing one step earlier -- it does not wait to be asked.

WHY FOUR VERBS AND NOT ONE. The narrow waist is one transition function per state change, and an
attach has four state changes, not one:

    image attach     the attempt exists   (state 'landing', nothing attested, `due_at` set)
    image attached   the bytes are vouched for (pointer + host + sha256 + bytes + mime)
    image failed     the attempt died and says at which stage (nothing attested)
    image detached   the operator removed the pointer; the file on disk is untouched

`image attach` commits BEFORE the file is opened. That ordering is inherited from
`brain.voice_capture` (migration 25) and the incident behind it: on 2026-08-17 the operator's
monologue saved as 0 bytes and nobody found out for a day, because the only evidence the attempt
happened was the artefact it failed to produce. An attach that hangs reading a 20 MB file off
`/mnt/c` and is then killed leaves a `landing` row past its `due_at`, which
`brain.image_attachment_health` reports as `stuck`. Without the open-first row it would leave
nothing, and nothing is the one state no error handler catches.

WHAT V5 DECIDED, because DESIGN-SYSTEM.md section 10 left it here:

  * **MIME from the magic bytes, never the extension.** An extension is a claim a filename makes
    about itself. `ACCEPT` is closed at four formats -- png, jpeg, gif, webp -- because those are
    what a browser renders with no plugin and no conversion step, and a conversion step is a
    second copy of the image, which is the split source of truth this posture exists to refuse.
  * **25 MiB.** Not a storage limit (nothing is stored) but a render limit: the console re-hashes
    on a cadence and streams the file to the browser on every view, and both costs are linear in
    the file. A larger file is refused as a `failed` attach that says the number, never truncated
    and never silently downscaled.
  * **A refusal is a failed attach, not a dropped request.** Every exit from `attach()` leaves a
    row.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
import platform
import secrets
import sys
import time

import psycopg2.errors

import store

# --------------------------------------------------------------------------- acceptance

#: 25 MiB. See the module docstring for why this is a render limit rather than a storage one.
MAX_BYTES = int(os.environ.get("CONSOLE_IMAGE_MAX_BYTES", str(25 * 1024 * 1024)))

#: Magic-byte prefixes, longest first where two could collide. WEBP is the RIFF container, so it
#: needs the second four bytes at offset 8 as well and is handled below rather than by prefix.
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

#: The closed vocabulary, matching `image_attachment_mime_check` in migration 29. Two spellings
#: of one list is how they drift, so the test suite asserts this equals the column's CHECK.
ACCEPT = ("image/png", "image/jpeg", "image/gif", "image/webp")

#: How long a verified (path, size, mtime) verdict is reused before the file is hashed again.
#: The console polls every three seconds and re-verifies on every render; without this a single
#: 20 MB image would be hashed twenty times a minute for as long as its card is on screen.
#:
#: WHAT THIS CACHE CANNOT SEE, stated because a cache that hides a finding is worse than no cache:
#: a rewrite that preserves BOTH the byte count and the mtime to the nanosecond is invisible to
#: it for up to `VERIFY_TTL_S` seconds. It is not invisible after that -- the entry expires and
#: the file is hashed again -- so this bounds the latency of a CHANGED finding, it does not
#: suppress one. Set `CONSOLE_IMAGE_VERIFY_TTL=0` to hash on every single render.
VERIFY_TTL_S = float(os.environ.get("CONSOLE_IMAGE_VERIFY_TTL", "30"))

#: Read in chunks; the whole point is that a 25 MiB file never sits in memory at once.
_CHUNK = 1024 * 1024

SUBJECT_TYPES = ("work_item", "question", "recommendation", "voice_capture", "objective")
STAGES = ("read", "hash", "record")


class ImageError(RuntimeError):
    """A refusal a caller should print and exit non-zero on. Never a reason to retry blindly."""


class AttachFailed(RuntimeError):
    """The attach died and the failure is RECORDED. Carries the row so a caller can render it."""

    def __init__(self, message: str, *, attach_id: str, stage: str):
        super().__init__(message)
        self.attach_id, self.stage = attach_id, stage


# --------------------------------------------------------------------------- measurement


def host() -> str:
    """The host every pointer this process writes is absolute ON.

    `CONSOLE_IMAGE_HOST` overrides it for the VPS move, which is the whole reason the column
    exists: after that move, `platform.node()` returns the new host and every pointer written on
    the old one must keep saying the old one.
    """
    return os.environ.get("CONSOLE_IMAGE_HOST") or platform.node() or "unknown-host"


def sniff_mime(head: bytes) -> str | None:
    """The format, from the bytes. `None` means "not one of the four", never "probably fine"."""
    for magic, mime in _MAGIC:
        if head.startswith(magic):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def read_and_hash(path: str) -> dict:
    """Stat, sniff, hash. Raises `ImageError` naming the stage, and reads in chunks.

    The order is deliberate and it is the order the landing states are named after: the size is
    checked BEFORE the file is hashed, so an oversized file is refused in one stat rather than
    after 400 MB of I/O the answer was never going to depend on.
    """
    try:
        st = os.stat(path)
    except OSError as exc:
        raise ImageError(f"read: {path} could not be stat'd ({exc.__class__.__name__}: {exc})")
    if not os.path.isfile(path):
        raise ImageError(f"read: {path} is not a regular file")
    if st.st_size == 0:
        raise ImageError(
            f"read: {path} is 0 bytes. A 0-byte image is the 2026-08-17 voice save again: the "
            f"tool that produced it reported success and produced nothing. It is a failed "
            f"attach, not an attachment.")
    if st.st_size > MAX_BYTES:
        raise ImageError(
            f"read: {path} is {st.st_size} bytes and the limit is {MAX_BYTES} "
            f"({MAX_BYTES // (1024 * 1024)} MiB). Refused whole rather than downscaled: a "
            f"resized copy is a second image the hash would not cover.")
    digest = hashlib.sha256()
    first = b""
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(_CHUNK)
                if not chunk:
                    break
                if not first:
                    first = chunk[:16]
                digest.update(chunk)
    except OSError as exc:
        raise ImageError(f"hash: {path} could not be read ({exc.__class__.__name__}: {exc})")
    mime = sniff_mime(first)
    if mime not in ACCEPT:
        raise ImageError(
            f"read: {path} does not begin with the magic bytes of any accepted image format "
            f"({', '.join(ACCEPT)}). The extension was not consulted: an extension is a claim "
            f"the filename makes about itself.")
    return {"bytes": st.st_size, "sha256": digest.hexdigest(), "mime": mime,
            "mtime_ns": st.st_mtime_ns}


_VERIFY_CACHE: dict[str, tuple] = {}


def verify(pointer: str, sha256: str, *, now=None) -> dict:
    """Is the file still there, and is it still the file that was attested?

    Returns `{"verdict": "ok"|"missing"|"changed"|"unreadable", ...}`. Three of the four are
    findings, and none of them is a blank: the caller renders every one.

    `unreadable` is its own verdict and not folded into `missing`, for the reason the runfeed's
    degraded states give: a permission error and a deleted file are different facts about the
    world, and reporting the one you can prove is cheaper than guessing which it was.
    """
    now = now or time.monotonic()
    try:
        st = os.stat(pointer)
    except FileNotFoundError:
        _VERIFY_CACHE.pop(pointer, None)
        return {"verdict": "missing", "checked_at": _utc(), "on_disk_bytes": None,
                "on_disk_sha256": None}
    except OSError as exc:
        _VERIFY_CACHE.pop(pointer, None)
        return {"verdict": "unreadable", "checked_at": _utc(), "on_disk_bytes": None,
                "on_disk_sha256": None,
                "detail": f"{exc.__class__.__name__}: {exc}"}
    if not os.path.isfile(pointer):
        return {"verdict": "missing", "checked_at": _utc(), "on_disk_bytes": None,
                "on_disk_sha256": None}

    key = (st.st_size, st.st_mtime_ns)
    hit = _VERIFY_CACHE.get(pointer)
    if hit and hit[0] == key and (now - hit[1]) < VERIFY_TTL_S:
        current = hit[2]
    else:
        try:
            digest = hashlib.sha256()
            with open(pointer, "rb") as fh:
                while True:
                    chunk = fh.read(_CHUNK)
                    if not chunk:
                        break
                    digest.update(chunk)
            current = digest.hexdigest()
        except OSError as exc:
            # A file that stats and will not open is UNREADABLE, and it is not cached: the next
            # render asks again, because a permission that was fixed a second ago should show as
            # fixed rather than as a cached refusal.
            _VERIFY_CACHE.pop(pointer, None)
            return {"verdict": "unreadable", "checked_at": _utc(),
                    "on_disk_bytes": st.st_size, "on_disk_sha256": None,
                    "detail": f"{exc.__class__.__name__}: {exc}"}
        _VERIFY_CACHE[pointer] = (key, now, current)

    if current != sha256:
        return {"verdict": "changed", "checked_at": _utc(), "on_disk_bytes": st.st_size,
                "on_disk_sha256": current}
    return {"verdict": "ok", "checked_at": _utc(), "on_disk_bytes": st.st_size,
            "on_disk_sha256": current}


def _utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_attach_id() -> str:
    """A UTC stamp and six random hex, the same shape `voice open` uses, for the same reason:
    the log line, the store row and the console receipt carry one string a human can join by eye.
    """
    return (_dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-" + secrets.token_hex(3))


# --------------------------------------------------------------------------- the transitions


def _row(ctx, attach_id: str) -> dict:
    row = ctx.one("SELECT * FROM brain.image_attachment WHERE attach_id = %s", (attach_id,))
    if not row:
        raise ImageError(
            f"no attach {attach_id!r}. Every stage verb names an attempt `image attach` already "
            f"committed: a stage that could create its own row on the way past would reintroduce "
            f"the silence the open-first ordering removes.")
    return row


@store.transition("image attach")
def image_attach(ctx, *, attach_id, subject_type, subject_id, requested_path, requested_host,
                 due_at, by="", actor_type=None, produced_by=None, as_operator=False):
    """Open the attempt. BEFORE the file is opened, and that order is the design.

    Nothing is attested here and the table refuses an attempt that tries to be
    (`image_attachment_landing_ck`). What this row says is only: somebody asked for this file to
    go on this item at this instant, and a terminal state is now owed by `due_at`.
    """
    if subject_type not in SUBJECT_TYPES:
        raise ImageError(
            f"{subject_type!r} is not a subject an image may hang on. One of: "
            f"{', '.join(SUBJECT_TYPES)}. The vocabulary is closed in the column's CHECK too.")
    dupe = ctx.one("SELECT attach_id, state FROM brain.image_attachment "
                   " WHERE subject_type = %s AND subject_id = %s "
                   "   AND state IN ('landing','attached')", (subject_type, subject_id))
    if dupe:
        raise ImageError(
            f"{subject_type} {subject_id} already carries image {dupe['attach_id']} in state "
            f"{dupe['state']!r}. One image, in place -- the brief's no-gallery rule, which "
            f"`image_attachment_one_live_idx` also enforces at the table. Detach that one first.")
    ctx.execute(
        """INSERT INTO brain.image_attachment
             (attach_id, subject_type, subject_id, state, due_at, requested_path, requested_host,
              attached_by, actor_type, produced_by)
           VALUES (%s, %s, %s, 'landing', %s, %s, %s, %s, %s, %s)""",
        (attach_id, subject_type, subject_id, due_at, requested_path, requested_host,
         by, actor_type, produced_by))
    return {"attach_id": attach_id, "state": "landing", "stage": "read"}


@store.transition("image attached")
def image_attached(ctx, *, attach_id, pointer, pointer_host, sha256, bytes_on_disk, mime,
                   actor_type=None, as_operator=False):
    """The bytes were read and hashed, and this row now vouches for them.

    Every value here was MEASURED by `read_and_hash` from the file itself. Nothing is copied
    across from the request: `requested_path` is what was asked for and `pointer` is what was
    proven, and on the ordinary case they are the same string measured twice rather than once.
    """
    row = _row(ctx, attach_id)
    if row["state"] != "landing":
        raise ImageError(
            f"attach {attach_id} is in state {row['state']!r} and only a 'landing' attempt can "
            f"become 'attached'. Stages do not skip and a terminal row is not reopened: a "
            f"correction supersedes, it does not edit.")
    ctx.execute(
        """UPDATE brain.image_attachment
              SET state = 'attached', pointer = %s, pointer_host = %s, sha256 = %s, bytes = %s,
                  mime = %s, attached_at = now(), updated_at = now(),
                  actor_type = COALESCE(%s, actor_type)
            WHERE attach_id = %s""",
        (pointer, pointer_host, sha256, bytes_on_disk, mime, actor_type, attach_id))
    return {"attach_id": attach_id, "state": "attached", "sha256": sha256,
            "bytes": bytes_on_disk, "pointer": pointer, "pointer_host": pointer_host,
            "mime": mime}


@store.transition("image failed")
def image_failed(ctx, *, attach_id, stage, reason, as_operator=False):
    """The attempt died. NOTHING is attested, and the table enforces that.

    `image_attachment_failed_ck` requires `pointer`, `pointer_host`, `sha256` and `bytes` all
    NULL in this state, which is the design's own copy -- *nothing was recorded ... no pointer
    was written* -- expressed where a later lane cannot talk it out of being true. What survives
    is the attempt: which file, on which host, and the stage it died at. The console keeps that
    on screen until the operator acts on it, because a failed attach that fades is a swallow.
    """
    if stage not in STAGES:
        raise ImageError(f"{stage!r} is not an attach stage. One of: {', '.join(STAGES)}.")
    row = _row(ctx, attach_id)
    if row["state"] != "landing":
        raise ImageError(
            f"attach {attach_id} is in state {row['state']!r}; only a 'landing' attempt can "
            f"fail. A terminal row is not rewritten.")
    ctx.execute(
        """UPDATE brain.image_attachment
              SET state = 'failed', failed_stage = %s, failed_reason = %s, updated_at = now()
            WHERE attach_id = %s""",
        (stage, reason, attach_id))
    return {"attach_id": attach_id, "state": "failed", "stage": stage, "reason": reason}


@store.transition("image detached")
def image_detached(ctx, *, attach_id, by="", as_operator=False):
    """Remove the pointer. THE FILE ON DISK IS UNTOUCHED and this verb never opens it.

    The row is superseded, not deleted -- migration 29 grants DELETE to nobody but the owner, and
    V00's rule is that a record you can silently rewrite is not a record. What the operator gets
    back is the slot: the partial unique index only covers 'landing' and 'attached', so a
    detached row leaves the item free for another image while the history of this one stays.
    """
    row = _row(ctx, attach_id)
    if row["state"] != "attached":
        raise ImageError(
            f"attach {attach_id} is in state {row['state']!r}. Only an attached image has a "
            f"pointer to remove; a failed attempt attested nothing and there is nothing to undo.")
    ctx.execute(
        """UPDATE brain.image_attachment
              SET state = 'detached', detached_at = now(), detached_by = %s, updated_at = now()
            WHERE attach_id = %s""",
        (by, attach_id))
    return {"attach_id": attach_id, "state": "detached", "pointer": row["pointer"],
            "pointer_host": row["pointer_host"]}


@store.transition("image dismissed")
def image_dismissed(ctx, *, attach_id, by="", as_operator=False):
    """The operator acted on a failed attach. The finding leaves the card; the row does not.

    The design requires a failed attach to persist UNTIL ACTED ON, which makes acting on it a
    state change and not a client-side dismissal. The row stays `failed` and keeps its stage and
    its reason: what this verb changes is whether the console still draws it, and that is the
    only thing it is allowed to change. An evidence table a caller can empty is not evidence.
    """
    row = _row(ctx, attach_id)
    if row["state"] != "failed":
        raise ImageError(
            f"attach {attach_id} is in state {row['state']!r}. Only a failed attempt is "
            f"dismissed; an attached image is removed with `image detached`, which is a "
            f"different act and says a different thing on the receipt.")
    if row.get("dismissed_at"):
        raise ImageError(f"attach {attach_id} was already dismissed at {row['dismissed_at']}.")
    ctx.execute(
        """UPDATE brain.image_attachment
              SET dismissed_at = now(), dismissed_by = %s, updated_at = now()
            WHERE attach_id = %s""",
        (by, attach_id))
    return {"attach_id": attach_id, "state": "failed", "dismissed": True}


# --------------------------------------------------------------------------- the pipeline


def attach(subject_type: str, subject_id: str, path: str, *, by: str = "",
           actor_type: str | None = None, produced_by: str | None = None,
           attach_id: str | None = None, due_seconds: int = 120,
           as_operator: bool = False) -> dict:
    """Read, then hash, then record -- and leave a row whichever of the three fails.

    This is the ONLY function that attaches an image, and it is a caller of `store.apply` rather
    than a writer: the console, the CLI and anything V8 puts on a phone all reach the same four
    verbs through it. A surface that INSERTed its own attachment row would be the violation V00
    names, and there is no connection in this process that would accept one.
    """
    attach_id = attach_id or new_attach_id()
    abs_path = os.path.abspath(os.path.expanduser(path))
    this_host = host()
    due = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=due_seconds)
    started = time.monotonic()

    # The row exists from here on, whatever happens next.
    store.apply("image attach", actor=by, attach_id=attach_id, subject_type=subject_type,
                subject_id=subject_id, requested_path=abs_path, requested_host=this_host,
                due_at=due, by=by, actor_type=actor_type, produced_by=produced_by,
                as_operator=as_operator)
    try:
        measured = read_and_hash(abs_path)
    except ImageError as exc:
        stage = str(exc).split(":", 1)[0].strip()
        stage = stage if stage in STAGES else "read"
        store.apply("image failed", actor=by, attach_id=attach_id, stage=stage,
                    reason=str(exc), as_operator=as_operator)
        invalidate()
        raise AttachFailed(str(exc), attach_id=attach_id, stage=stage) from None
    try:
        out = store.apply("image attached", actor=by, attach_id=attach_id, pointer=abs_path,
                          pointer_host=this_host, sha256=measured["sha256"],
                          bytes_on_disk=measured["bytes"], mime=measured["mime"],
                          actor_type=actor_type, as_operator=as_operator)
    except Exception as exc:                                            # noqa: BLE001
        # The record stage is the third one the design names, and it fails like the other two:
        # loudly, into a row, saying where. Best effort -- if the store is what broke, this write
        # will not land either, and then the `landing` row past its `due_at` is the finding.
        try:
            store.apply("image failed", actor=by, attach_id=attach_id, stage="record",
                        reason=f"{exc.__class__.__name__}: {exc}", as_operator=as_operator)
        except Exception:                                               # noqa: BLE001
            pass
        raise AttachFailed(f"record: {exc}", attach_id=attach_id, stage="record") from None
    invalidate()
    out["elapsed_s"] = round(time.monotonic() - started, 3)
    out["subject_type"], out["subject_id"] = subject_type, subject_id
    out["name"] = os.path.basename(abs_path)
    return out


def discard(attach_id: str, *, by: str = "", as_operator: bool = False) -> dict:
    """Take a failed attach off the card. Nothing is deleted."""
    out = store.apply("image dismissed", actor=by, attach_id=attach_id, by=by,
                      as_operator=as_operator)
    invalidate()
    return out


def detach(attach_id: str, *, by: str = "", as_operator: bool = False) -> dict:
    """Undo an attach. The pointer goes; the file does not."""
    out = store.apply("image detached", actor=by, attach_id=attach_id, by=by,
                      as_operator=as_operator)
    invalidate()
    return out


# --------------------------------------------------------------------------- the read path


_LIVE_SQL = """
    SELECT * FROM brain.image_attachment
     WHERE subject_type = %s AND subject_id = %s
       AND (state IN ('landing','attached') OR (state = 'failed' AND dismissed_at IS NULL))
     ORDER BY id DESC LIMIT 1
"""


def live_rows(pairs: list[tuple]) -> dict:
    """Every renderable attachment for a batch of subjects, in ONE read.

    The queue renders up to twenty-one cards and the poller re-renders them every three seconds.
    One query per card would be twenty-one round trips per poll; this is one.
    """
    if not pairs:
        return {}
    with store.read() as s:
        rows = s.query(
            """SELECT DISTINCT ON (subject_type, subject_id) *
                 FROM brain.image_attachment
                WHERE (state IN ('landing','attached')
                       OR (state = 'failed' AND dismissed_at IS NULL))
                  AND (subject_type, subject_id) IN %s
                ORDER BY subject_type, subject_id, id DESC""",
            (tuple((str(a), str(b)) for a, b in pairs),))
    return {(r["subject_type"], r["subject_id"]): r for r in rows}


#: How long the whole-table snapshot below is reused. ONE second, not thirty: this is a cache
#: against N round trips inside ONE render, not a cache of state across renders. The queue draws
#: up to twenty-one cards and `store.read()` opens a connection per call, so a per-card read would
#: be twenty-one connections every three seconds. A render completes in far under a second, so one
#: read serves every card on the page and the next poll reads again.
INDEX_TTL_S = float(os.environ.get("CONSOLE_IMAGE_INDEX_TTL", "1.0"))

_INDEX: list = [0.0, {}]


def invalidate() -> None:
    """Drop the snapshot. Called by this module's own writers so an attach the operator just
    made is on the very next render rather than up to `INDEX_TTL_S` later."""
    _INDEX[0], _INDEX[1] = 0.0, {}


def index(now=None) -> dict:
    """Every renderable attachment in the store, keyed by (subject_type, subject_id).

    Renderable means `landing`, `attached` or `failed`: the three states that put something on a
    card. `detached` is deliberately absent -- the operator removed that pointer and the row is
    kept as history, not as a thing to draw.
    """
    now = now if now is not None else time.monotonic()
    if _INDEX[1] and (now - _INDEX[0]) < INDEX_TTL_S:
        return _INDEX[1]
    try:
        with store.read() as s:
            rows = s.query(
                """SELECT DISTINCT ON (subject_type, subject_id) *
                     FROM brain.image_attachment
                    WHERE state IN ('landing','attached')
                       OR (state = 'failed' AND dismissed_at IS NULL)
                    ORDER BY subject_type, subject_id, id DESC""")
    except psycopg2.errors.UndefinedTable:
        # A STORE BELOW MIGRATION 29, and the console still renders. This is deliberately NOT
        # the Queue room's posture, which raises rather than falling back, and the difference is
        # whether the fallback can lie. The Queue cannot be ordered without D6b's tables, so an
        # order invented in its absence would be a second classifier. Here, "no image on this
        # item" is not a guess: `brain.image_attachment` is the ONLY place an attachment lives,
        # so if the table is absent then no image has ever been attached and rendering nothing is
        # the true answer rather than a degraded one.
        #
        # The attach VERB is a different question and it fails loudly, because attaching against
        # a store with no table must not look like it worked.
        _warn_no_table()
        _INDEX[0], _INDEX[1] = now, {}
        return _INDEX[1]
    _INDEX[0] = now
    _INDEX[1] = {(r["subject_type"], str(r["subject_id"])): r for r in rows}
    return _INDEX[1]


_WARNED = []


def _warn_no_table() -> None:
    """Once per process, on stderr. Once, because this runs on a three-second poll and a warning
    that repeats twenty times a minute is one an operator filters out."""
    if _WARNED:
        return
    _WARNED.append(True)
    print("web/images: brain.image_attachment does not exist in "
          f"{os.environ.get('BRAIN_PG_DB', 'brain')}, so no image renders anywhere. That table "
          "is migrations/0029_image_attachment.sql and it is NOT applied by "
          "engine/bin/scratch-db.sh on a store built before it. Apply it: see "
          "web/docs/APPLY-IMAGES.md. Nothing else on this console is affected.", file=sys.stderr)


def for_card(subject_type: str, subject_id) -> dict | None:
    """The card hook's read. One snapshot for the page, one verification per image."""
    row = index().get((str(subject_type), str(subject_id)))
    return render_fields(row) if row else None


def by_attach_id(attach_id: str) -> dict | None:
    """One attachment by its own id, read fresh -- the bytes route and the undo path both name
    the attach rather than the subject, and neither may be served out of a snapshot."""
    with store.read() as s:
        row = s.one("SELECT * FROM brain.image_attachment WHERE attach_id = %s", (attach_id,))
    return row


def for_subject(subject_type: str, subject_id: str) -> dict | None:
    """One subject's renderable attachment, verified. `None` means no image, which is not a
    finding: an item that never had an image is not an item whose image is gone."""
    with store.read() as s:
        row = s.one(_LIVE_SQL, (subject_type, str(subject_id)))
    return render_fields(row) if row else None


def _kb(n) -> str:
    if n is None:
        return "unknown size"
    n = int(n)
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def render_fields(row: dict) -> dict:
    """One store row plus one verification, turned into what the template renders.

    THE STATE IS COMPUTED HERE AND NOT READ OFF THE ROW, and that is the point of the whole lane.
    `state = 'attached'` says the bytes were vouched for when they were vouched for.
    `img.state` says what is true now. v1's finding was a path recorded as present that was not on
    disk; a console that rendered the stored state would have shown that as a healthy image.
    """
    out = {
        "attach_id": row["attach_id"], "row_state": row["state"],
        "subject_type": row["subject_type"], "subject_id": row["subject_id"],
        "pointer": row.get("pointer"), "pointer_host": row.get("pointer_host"),
        "sha256": row.get("sha256"), "bytes": row.get("bytes"), "mime": row.get("mime"),
        "requested_path": row.get("requested_path"),
        "requested_host": row.get("requested_host"),
        "attached_by": row.get("attached_by") or "", "attached_at": row.get("attached_at"),
        "failed_stage": row.get("failed_stage"), "failed_reason": row.get("failed_reason"),
        "name": os.path.basename(row.get("pointer") or row.get("requested_path") or ""),
        "size": _kb(row.get("bytes")),
        "sha_short": (row.get("sha256") or "")[:7],
        "on_this_host": (row.get("pointer_host") or row.get("requested_host")) == host(),
    }
    if row["state"] == "failed":
        out["state"] = "failed"
        return out
    if row["state"] == "landing":
        # `stuck` is not a fifth rendering state: the design gives landing state words and no bar,
        # and a landing past its due_at is a landing that is going to be reported as a failure by
        # `brain.image_attachment_health`. What the card says is what is true -- it is still open.
        out["state"] = "landing"
        out["stage_words"] = ("reading", "hashing", "recording pointer")
        out["due_at"] = row.get("due_at")
        return out

    v = verify(row["pointer"], row["sha256"])
    out["checked_at"] = v["checked_at"]
    out["on_disk_bytes"] = v.get("on_disk_bytes")
    out["on_disk_sha256"] = v.get("on_disk_sha256")
    out["on_disk_sha_short"] = (v.get("on_disk_sha256") or "")[:7]
    out["on_disk_size"] = _kb(v.get("on_disk_bytes"))
    out["detail"] = v.get("detail")
    out["state"] = {"ok": "ok", "missing": "missing", "changed": "changed",
                    "unreadable": "unreadable"}[v["verdict"]]
    # THE POINTER IS ONLY HALF THE CLAIM. A pointer that is absolute on another host is not a
    # missing file, it is a file this process cannot speak about, and reporting it as MISSING
    # would be the confidently-wrong number V00 warns about. D3's inherited warning, and it is
    # the reason `pointer_host` is a column at all.
    if not out["on_this_host"] and out["state"] != "ok":
        out["state"] = "elsewhere"
    return out


def finding_text(img: dict) -> str:
    """The finding, as one line of text, for a caller that has no HTML -- the CLI, a test, a
    report pasted into a task summary. The template renders the same facts; this is the only
    other place they are worded, and both read this function's vocabulary."""
    if img["state"] == "missing":
        return (f"MISSING · recorded {_stamp(img['attached_at'])}, not on disk at "
                f"{img['checked_at']} · {img['pointer']} · host {img['pointer_host']} · "
                f"sha {img['sha_short']} · {img['size']} when recorded")
    if img["state"] == "changed":
        # EXACT BYTES HERE, not the rounded caption figure. Measured 2026-08-18: 95,301 bytes
        # rewritten to 95,365 both render as "93 KB", so the finding read "93 KB recorded, 93 KB
        # on disk" and looked like it was contradicting itself. A finding whose own numbers
        # appear to agree is a finding the reader stops believing.
        return (f"CHANGED · bytes on disk no longer match the recorded hash "
                f"({img['sha_short']} → {img['on_disk_sha_short']}) · {img['pointer']} · "
                f"host {img['pointer_host']} · {img['bytes']:,} bytes recorded, "
                f"{(img['on_disk_bytes'] or 0):,} on disk at {img['checked_at']}")
    if img["state"] == "unreadable":
        return (f"UNREADABLE · {img['pointer']} exists and this process cannot read it "
                f"({img.get('detail') or 'no detail'}) at {img['checked_at']} · showing nothing "
                f"rather than guessing which")
    if img["state"] == "elsewhere":
        return (f"ANOTHER HOST · {img['pointer']} is absolute on {img['pointer_host']} and this "
                f"is {host()}. Not a missing file: a file this host cannot speak about.")
    if img["state"] == "failed":
        return (f"ATTACH FAILED at {img['failed_stage']} · nothing was recorded · "
                f"{img['requested_path']} on {img['requested_host']} · {img['failed_reason']}")
    return ""


def _stamp(ts) -> str:
    if not ts:
        return "at an unrecorded time"
    return ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
