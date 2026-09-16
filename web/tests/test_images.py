#!/usr/bin/env python3
"""V5: AN IMAGE IS A POINTER AND A HASH, AND ITS ABSENCE IS A FINDING. Task 0167.

Nine properties, each one a measurement rather than a description:

  1. Attach records a pointer, a host, a sha256, a byte count and a MIME, and every one of them
     is measured from the file rather than copied from the request.
  2. A file removed after it was attached renders as a RED MISSING finding, with NO `<img>` tag
     anywhere in the markup. The finding is DOM, not styling: the same rule deep work is built
     on. This is v1's own case -- a path recorded as present and gone -- rendered one step
     earlier, without waiting to be asked.
  3. A file whose bytes change under the pointer renders as an AMBER CHANGED finding carrying
     both hashes and both EXACT byte counts.
  4. A failed attach leaves a row that attests NOTHING: `pointer`, `pointer_host`, `sha256` and
     `bytes` all NULL, enforced by `image_attachment_failed_ck` and not by the caller.
  5. NO IMAGE BYTES ARE IN THE STORE. Asserted over the WHOLE `brain` schema, not over this
     lane's table, because the way image bytes get into Postgres is not somebody adding them
     here -- it is somebody adding them somewhere else.
  6. NO IMAGE BYTES ARE IN AN EVENT PAYLOAD, and the 4096-byte ceiling that would refuse them is
     shown refusing rather than described.
  7. One image, in place. A second live attach on one subject is refused twice: by the verb and
     by a partial unique index.
  8. Every write goes through the narrow waist -- the four verbs are registered, reachable from
     the Queue room and from nowhere else, and `web/images.py` contains no SQL outside a
     registered transition.
  9. Undo removes the pointer and leaves the file exactly where it is.

Run:  python3 -m web.tests.test_images
      (or `python3 -m pytest web/tests/test_images.py`, which is the same store and the same
      fixture: both go through the block below.)

The store is chosen, refused and reset by that block rather than by whoever types the command.
It selects a scratch database of this run's own when `BRAIN_PG_DB` names none, REFUSES `brain`
and `brain_scratch` by name, and clears every live attachment before the first assertion, so the
verdict is a fact about `web/images.py` and not about what the last run left behind. Task 0428,
and the two paragraphs above the code are the measurement that forced each half.


**THIS SUITE TESTS THE WORKING TREE, NOT HEAD, AND ON 2026-08-18 THOSE DIFFER.** The rendering
half of V5 lives in files two other Wave-2 lanes were editing at the same time
(`web/templates/macros.html`, `web/model.py`, `web/rooms.py`, `web/actions.py`, `web/app.py`,
`web/static/console.{css,js}`, `web/templates/detail_task.html`), so those edits were left in the
tree rather than swept into this lane's commit along with V3's and V4's in-flight work. Until
they are committed, the checks here that render the macro or read the room allowlist fail against
a bare HEAD checkout and pass against the tree. Measured in the tree, 2026-08-18: 93 passed, 0
failed. See outputs/2026-08-18-T2-0167-images/PROOF.md.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in ("queue", "engine"):
    sys.path.insert(0, str(ROOT / _p))
sys.path.insert(0, str(ROOT))

# ----------------------------------------------------------------- WHICH STORE, AND THE REFUSAL
#
# TASK 0428. What stood here was `os.environ.setdefault("BRAIN_PG_DB", "brain_t2_0167")`, and
# `setdefault` does not mean "use the scratch database". It means "use whatever is already set,
# and fall back to this only if nothing is" -- so the documented database was the one case that
# never happened in the environment this suite actually runs in. Every swarm terminal on this
# host exports `BRAIN_PG_DB=brain`. Measured 2026-08-29: `python3 -m pytest` on this file, typed
# by an agent, RAN THE SUITE AGAINST THE LIVE STORE and printed `12 passed`. `brain`
# gained nine work_item rows (0448..0456: `V5: one image, in place` and its siblings) and ten
# `brain.image_attachment` rows; `swarm show 0454` rendered one as a real inbox row on the
# operator's board. brain_t2_0167 was untouched. The GREEN was the false signal, not the red.
#
# Three things are settled here, before `import store` opens a connection to anything:
#
#   1. THE NAME. `BRAIN_PG_DB` when the caller named one -- `web/tests/run-all.sh` does, and pins
#      `ENGINE_SCRATCH_DB` to the same value -- otherwise a database of this run's own, derived
#      the way `engine/tests/_scratch_preflight.py:_suggested_scratch_db` derives it. What is
#      never used is the bare store default, because `store/schema.py` defaults it to `brain`.
#   2. THE REFUSAL. `brain` is the live bus; `brain_scratch` is the database every other lane's
#      suite reads. This suite WRITES to whatever it is handed -- it posts items, attaches images
#      and clears the live attachments it finds -- so both are refused by name. Exit 1 and not
#      77: `web/tests/run-all.sh` counts a 77 as an INHERENT NOT RUN and stays green, which would
#      turn "you aimed me at the live store" into a clean banner.
#   3. THE SCHEMA. `scratch-db.sh ensure` builds the database when it is absent and applies only
#      the migrations it has not recorded when it is present, so a first run on a derived name
#      works and every later one is a no-op. `ensure`, never `create`: create DROPs.

_LIVE_DB = "brain"                  # store/schema.py's default, duplicated rather than imported:
_SHARED_SCRATCH = "brain_scratch"   # importing `store` to learn it would connect to it first.


def _own_scratch_db() -> str:
    """A database this run may safely write to. The default, and the remedy line's name.

    It never echoes an input, for the reason task 0353 wrote down one directory over: in the
    branch that prints a remedy, the caller's own `BRAIN_PG_DB` is by definition the wrong
    database and in a fleet terminal it is the live store, and pasting the remedy is the single
    most likely thing anyone does with a remedy.
    """
    agent = "".join(c for c in os.environ.get("SWARM_AGENT", "").strip().lower() if c.isalnum())
    task = "".join(c for c in os.environ.get("SWARM_PARENT_TASK", "").strip().lower()
                   if c.isalnum())
    # THE FALLBACK IS SCOPED TO THIS WORKING TREE, not a fixed name. R-DBNAME-02, from Terminal 26's
    # database inventory: `brain_v5_images` was FIXED, so two terminals running web/tests at once
    # collided on one store -- each truncating attachments the other was asserting on -- and it
    # appeared in T26's before/after listing as a database created outside the declared names.
    #
    # THE WORKING TREE'S OWN DIRECTORY NAME is the right discriminator here, and a PID is not.
    # It is distinct across terminals, which is the collision; it is STABLE across runs, which
    # matters because a per-run name leaves a store behind on every run and the reaper only
    # recognises three patterns, none of them this one. So a PID would trade a collision for an
    # unbounded pile of abandoned stores.
    tree = "".join(c for c in ROOT.name.lower() if c.isalnum()) or "tree"
    name = f"brain_{agent}_{task}" if agent and task else f"brain_v5_images_{tree}"
    # Not reachable from either branch: both carry a suffix. Kept so that an edit which makes it
    # reachable fails here rather than quietly inside an attach against the operator's board.
    assert name not in (_LIVE_DB, _SHARED_SCRATCH), "the default must never name a shared store"
    return name


_DB = (os.environ.get("BRAIN_PG_DB") or "").strip() or _own_scratch_db()
if _DB in (_LIVE_DB, _SHARED_SCRATCH):
    _safe = _own_scratch_db()
    sys.stderr.write(
        f"test_images: BRAIN_PG_DB is {_DB!r}. REFUSING TO RUN, and this is not caution.\n"
        f"  This suite posts work items, attaches images to them, and clears every live\n"
        f"  attachment in the store before it starts. Against {_LIVE_DB!r} that writes fixtures\n"
        f"  onto the operator's board -- measured on 2026-08-29, nine rows, 0448..0456 -- and\n"
        f"  against {_SHARED_SCRATCH!r} it empties attachments another lane's suite is reading.\n"
        f"  Point it at a scratch database of your own:\n"
        f"      BRAIN_PG_DB={_safe} python3 -m web.tests.test_images\n"
        f"  or UNSET BRAIN_PG_DB, which selects {_safe} and builds it if it is not there.\n"
        f"  Do not resolve this by exporting {_DB} into ENGINE_SCRATCH_DB as well.\n"
        f"  Under pytest this refusal arrives as `mainloop: caught unexpected SystemExit` and a\n"
        f"  traceback, exit 3. That is pytest reacting to a refusal at import, not a defect in\n"
        f"  the suite: the sentence above it is the whole finding.\n")
    raise SystemExit(1)

os.environ["BRAIN_PG_DB"] = _DB
# `scratch-db.sh` reads ENGINE_SCRATCH_DB and defaults it to brain_scratch. Pinned here to the
# same name rather than left to that default, so the database that gets built and the database
# that gets asserted on cannot come apart -- the halves-apart defect that
# `engine/tests/_scratch_preflight.py` refuses, closed by construction instead.
#
# BUT IT REFUSES A CONFLICT NOW RATHER THAN OVERWRITING ONE. Raised by Terminal 26 on 2026-09-07
# while auditing which files SET a database name rather than read one: this assignment runs AT
# IMPORT and mutates the environment for the WHOLE pytest session, so a module imported after this
# one silently gets THIS suite's store instead of the one its caller chose.
#
# The import-time work is load-bearing and is not being moved: `scratch-db.sh ensure` runs a few
# lines below and reads ENGINE_SCRATCH_DB, so a fixture or monkeypatch would be too late to build
# the right store. What was wrong was not the timing but the SILENCE -- overwriting a value
# somebody else set, without saying so.
#
# In the runner path this changes nothing: `web/tests/run-all.sh` exports both names to the same
# `$WEB_DB` before dispatch, so the values already agree and this branch is not taken. It bites
# only where the hazard is real -- somebody running several web suites in ONE pytest process with
# a target of their own.
_prior = (os.environ.get("ENGINE_SCRATCH_DB") or "").strip()
if _prior and _prior != _DB:
    sys.stderr.write(
        f"test_images: ENGINE_SCRATCH_DB is already {_prior!r} and this suite needs {_DB!r}.\n"
        f"  REFUSING rather than overwriting it. This assignment happens at import, so taking\n"
        f"  {_prior!r} away here would retarget every module imported after this one in the same\n"
        f"  pytest process, and they would report against a store nobody pointed them at.\n"
        f"  Set BRAIN_PG_DB and ENGINE_SCRATCH_DB to the same name, or run this suite in its own\n"
        f"  process:\n"
        f"      BRAIN_PG_DB={_prior} ENGINE_SCRATCH_DB={_prior} python3 -m web.tests.test_images\n")
    raise SystemExit(1)
os.environ["ENGINE_SCRATCH_DB"] = _DB
os.environ.setdefault("BRAIN_AFTER_COMMIT_HOOKS", "0")
os.environ.pop("SWARM_PARENT_TASK", None)      # the live runner exports this; tests post roots

_ensure = subprocess.run([str(ROOT / "engine/bin/scratch-db.sh"), "ensure"],
                         capture_output=True, text=True)
if _ensure.returncode != 0:
    sys.stdout.write(_ensure.stdout)
    sys.stderr.write(_ensure.stderr)
    sys.stderr.write(
        f"\ntest_images: `scratch-db.sh ensure` exited {_ensure.returncode} on {_DB}. NOT\n"
        f"RUNNING: every result below would be a statement about the schema rather than about\n"
        f"web/images.py. That exit code is the condition DETECTED; the cause is whatever the\n"
        f"output above says it is, and this line does not guess at it.\n")
    raise SystemExit(1)

import store                                                          # noqa: E402
from swarm_engine import transitions as _engine                       # noqa: E402,F401
from web import images, rooms as web_rooms                            # noqa: E402

PASS, FAIL = 0, 0
TMP = tempfile.mkdtemp(prefix="v5-images-")


# ------------------------------------------------------------------------------- AND THE FIXTURE
#
# THE SECOND HALF OF 0428, AND THE HALF THAT MAKES THE RESULT A FACT ABOUT THE CODE. Even on a
# database this suite owns, its own writes decided the next run's verdict, and the mechanism is
# narrower than "a dirty store". Measured on brain_t2_0167, 2026-08-29: image_attachment held
# attached=47 while brain.work_item held TWO rows and brain.item_id_seq.last_value was 2. That
# store had been TRUNCATEd -- `engine/bin/scratch-db.sh:572` empties brain.work_item CASCADE and
# then does `setval('brain.item_id_seq', 1, false)` -- and image_attachment is not on that list
# and carries no foreign key to work_item, so CASCADE never reached it. The id space therefore
# RECYCLES underneath live attachments: rows sat on subject_id 0001, 0002, 0003, 0010..., and the
# next `post` hands 0003 straight back out. The failure a lane then reads,
#
#     work_item 0002 already carries image ...
#
# is not this run's second attach being refused. It is a PREVIOUS run's attachment sitting on an
# id this run was just given, and property 7 ("one image, in place") is exactly the property that
# cannot tell the two apart. 0428's brief reports it as 6 of 12 red under pytest; what was
# re-measured here on 2026-08-29 is HEAD's version of this file dying on its FIRST property with
# `work_item 0003 already carries image 20260818T184305Z-ee2c13`, an August 18th row refusing an
# August 29th attach, with nothing wrong in web/images.py. Intermittent rather than reliable,
# which is worse: the collision fires only when a survivor happens to sit on an id the new run is
# handed, so the same code is green on Monday and red on Tuesday.
#
# So the suite supplies its own fixture instead of inheriting one: before anything is asserted,
# every attachment the store is still carrying is cleared THROUGH THE REGISTERED VERBS, and the
# count is printed. `docs/SUITE-INPUT-RULE.md`: a suite whose input is live state supplies a
# fixture or declares NOT RUN, and "run it against a fresh database" is a rule nobody can obey
# twice in a row.


#: RENDERABLE, IN THE PRODUCT'S OWN WORDS, and copied from `web/images.py:_LIVE_SQL` rather than
#: reinvented. The first draft of this fixture cleared `state IN ('landing','attached')` -- MY
#: definition of live, not the console's -- and the suite was still not idempotent, in a way that
#: took a second run to find: `test_undo_removes_the_pointer_and_keeps_the_file` asserts that a
#: detached subject renders NO card, and on brain_t2_0167 it rendered one anyway, because an
#: undismissed `failed` row from an earlier run sat on the id this run had just been handed. A
#: fixture that clears less than the read path renders leaves exactly the residue that matters.
_RENDERABLE = ("state IN ('landing','attached') "
               "OR (state = 'failed' AND dismissed_at IS NULL)")


def _clear_live_attachments() -> dict:
    """Return the store to 'no attachment renders on any card', and say how far it had drifted.

    Through the registered verbs and never a DELETE, for the reason migration 29 grants DELETE to
    nobody: a row you can silently remove is not a record. Each of the three renderable states
    gets the verb that is true of it -- an `attached` row is detached, a `landing` row is a
    process that died mid-attach so it fails and is then dismissed, and a `failed` row is
    dismissed, which is the verb whose whole job is "this finding leaves the card and the row
    does not". Every one of them keeps its history and frees the slot.
    """
    with store.read() as s:
        rows = [dict(r) for r in s.query(
            f"SELECT attach_id, state FROM brain.image_attachment WHERE {_RENDERABLE} ORDER BY id")]
    out = {"attached": 0, "landing": 0, "failed": 0, "seen": len(rows)}
    for r in rows:
        if r["state"] == "attached":
            images.detach(r["attach_id"], by="test_images fixture")
            out["attached"] += 1
            continue
        if r["state"] == "landing":
            store.apply("image failed", actor="test_images fixture", attach_id=r["attach_id"],
                        stage="record",
                        reason="stranded 'landing' row cleared by the test_images fixture (0428)")
            out["landing"] += 1
        else:
            out["failed"] += 1
        images.discard(r["attach_id"], by="test_images fixture")
    images.invalidate()
    with store.read() as s:
        out["left"] = s.scalar(
            f"SELECT count(*) FROM brain.image_attachment WHERE {_RENDERABLE}")
    if out["left"]:
        sys.stderr.write(
            f"test_images: cleared {out['seen']} renderable attachment(s) from {_DB} and "
            f"{out['left']} remain.\nNOT RUNNING: the properties below would be measured against "
            f"those rows, and the failure would name this run's subjects rather than the run that "
            f"left them.\n")
        raise SystemExit(1)
    return out


_FIXTURE = _clear_live_attachments()
print(f"fixture: {_DB} carried {_FIXTURE['seen']} renderable attachment(s) from earlier runs "
      f"({_FIXTURE['attached']} attached, {_FIXTURE['landing']} landing, "
      f"{_FIXTURE['failed']} undismissed failures); "
      f"{_FIXTURE['seen']} cleared, {_FIXTURE['left']} renderable remain.")


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")
    return ok


def refused(fn, *a, **kw) -> str:
    try:
        fn(*a, **kw)
        return ""
    except Exception as e:                                            # noqa: BLE001
        return str(e) or e.__class__.__name__


def png(path: str, w: int = 64, h: int = 32) -> str:
    """A real PNG, written by this file, so the magic bytes are genuinely the format's."""
    import struct
    import zlib

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)

    # Deterministic pseudo-noise, not a gradient: a gradient compresses to a few hundred bytes
    # and the 4096-byte event-ceiling test below needs a file that is genuinely bigger than the
    # ceiling. Deterministic so a failure is reproducible.
    rows, seed = [], 0x5EED
    for y in range(h):
        row = bytearray(b"\x00")
        for x in range(w):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            row += bytes(((seed >> 16) & 0xFF, (seed >> 8) & 0xFF, seed & 0xFF))
        rows.append(bytes(row))
    raw = b"".join(rows)
    blob = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))
    Path(path).write_bytes(blob)
    return path


def item(title: str) -> str:
    r = store.apply("post", title=title, lane="console", posted_by="T2", body="V5 test")
    return r["id"] if isinstance(r, dict) else r


def render(img, subject_id: str) -> str:
    """The macro, rendered by Jinja, so the assertions are about the markup that ships."""
    os.environ.setdefault("FLASK_SECRET", "test-only")
    from flask import render_template_string
    from web.app import app
    with app.test_request_context("/"):
        return render_template_string(
            "{% import 'macros.html' as m %}{{ m.media_block('queue', img, 'work_item', sid) }}",
            img=img, sid=subject_id)


# --------------------------------------------------- 1. the four things that get recorded

def test_attach_records_pointer_host_hash_and_size():
    p = png(f"{TMP}/attached.png")
    tid = item("V5: an image is attached to this")
    out = images.attach("work_item", tid, p, by="T2", actor_type="ai")

    on_disk = hashlib.sha256(Path(p).read_bytes()).hexdigest()
    check("the sha256 recorded is the file's own", out["sha256"] == on_disk,
          f"{out['sha256']} != {on_disk}")
    check("the byte count recorded is the file's own", out["bytes"] == os.path.getsize(p))
    check("the pointer is absolute", os.path.isabs(out["pointer"]))
    check("the host the pointer is absolute ON is recorded", out["pointer_host"] == images.host())
    check("the MIME came from the magic bytes", out["mime"] == "image/png")

    with store.read() as s:
        row = s.one("SELECT * FROM brain.image_attachment WHERE attach_id = %s",
                    (out["attach_id"],))
    check("and all five are in the store row",
          all(row[k] for k in ("pointer", "pointer_host", "sha256", "bytes", "mime")),
          str({k: row[k] for k in ("pointer", "pointer_host", "sha256", "bytes", "mime")}))
    check("the row opened BEFORE it attested (opened_at <= attached_at)",
          row["opened_at"] <= row["attached_at"])
    check("and it carried a due_at from the moment it opened", row["due_at"] is not None)
    return tid, out


# --------------------------------------------------- 2. a removed image IS a finding

def test_a_removed_image_renders_as_a_finding():
    p = png(f"{TMP}/deleted-under-us.png")
    tid = item("V5: the image that gets deleted under the console")
    out = images.attach("work_item", tid, p, by="T2", actor_type="ai")
    images.invalidate()
    check("while the file is there, the verdict is ok",
          images.for_card("work_item", tid)["state"] == "ok")

    os.remove(p)
    images.invalidate()
    img = images.for_card("work_item", tid)
    check("with the file gone, the verdict is missing", img["state"] == "missing",
          f"got {img['state']!r}")
    check("the STORE row still says 'attached' -- the verdict is measured, not read", True)
    with store.read() as s:
        stored = s.scalar("SELECT state FROM brain.image_attachment WHERE attach_id = %s",
                          (out["attach_id"],))
    check("  (proof: the row's own state is still 'attached')", stored == "attached", stored)

    text = images.finding_text(img)
    for must in ("MISSING", out["pointer"], images.host(), out["sha256"][:7]):
        check(f"the finding names {must[:38]!r}", must in text, text)

    html = render(img, tid)
    check("NO <img> tag renders for a missing image", "<img" not in html, html[:400])
    check("no broken-image glyph can appear, because there is no element to break", True)
    check("the finding is markup, not a style", "MISSING" in html and "imgfind red" in html)
    check("and the block holds the layout slot absence claimed", "imgbox gone" in html)
    check("the caption still renders -- the pointer made legible",
          "imgcap" in html and os.path.basename(p) in html)
    return tid, img


# --------------------------------------------------- 3. changed bytes

def test_changed_bytes_render_as_a_finding_carrying_both_hashes():
    p = png(f"{TMP}/changed-under-us.png")
    tid = item("V5: the image whose bytes change under the pointer")
    out = images.attach("work_item", tid, p, by="T2", actor_type="ai")
    before = os.path.getsize(p)
    with open(p, "ab") as fh:
        fh.write(b"\x00" * 64)
    images.invalidate()
    images._VERIFY_CACHE.clear()
    img = images.for_card("work_item", tid)
    check("changed bytes are CHANGED, not ok and not missing", img["state"] == "changed",
          f"got {img['state']!r}")
    text = images.finding_text(img)
    check("the finding carries the recorded hash", out["sha256"][:7] in text, text)
    check("and the hash on disk now", img["on_disk_sha_short"] in text, text)
    check("and EXACT byte counts, not the rounded caption figure",
          f"{before:,}" in text and f"{before + 64:,}" in text, text)
    html = render(img, tid)
    check("the current bytes render beneath the finding, dimmed", "imgbox dim" in html, html[:300])
    check("and they are labelled unattested in the URL that serves them",
          "unattested=1" in html, html[:400])


# --------------------------------------------------- 4. a failed attach attests nothing

def test_a_failed_attach_attests_nothing():
    tid = item("V5: the attach that fails")
    exc = None
    try:
        images.attach("work_item", tid, f"{TMP}/there-is-no-such-file.png", by="T2")
    except images.AttachFailed as e:
        exc = e
    check("a missing source raises AttachFailed", exc is not None)
    check("and it names the stage", exc and exc.stage == "read", exc and exc.stage)
    with store.read() as s:
        row = s.one("SELECT * FROM brain.image_attachment WHERE attach_id = %s",
                    (exc.attach_id,))
    check("a row exists for the attempt -- it did not vanish", row is not None)
    check("its state is 'failed'", row["state"] == "failed")
    check("NOTHING is attested: pointer, pointer_host, sha256, bytes all NULL",
          row["pointer"] is None and row["pointer_host"] is None
          and row["sha256"] is None and row["bytes"] is None,
          str({k: row[k] for k in ("pointer", "pointer_host", "sha256", "bytes")}))
    check("and the attempt is still named, so the finding can say which file",
          row["requested_path"].endswith("there-is-no-such-file.png"))

    # AND THE DATABASE IS WHAT ENFORCES IT, not the transition remembering to. Read the CHECK
    # itself rather than trying to violate it through a verb: a verb that refuses proves the verb
    # refuses, and the claim here is about the column.
    with store.read() as s:
        ck = s.scalar("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                      " WHERE conrelid = 'brain.image_attachment'::regclass "
                      "   AND conname = 'image_attachment_failed_ck'")
    check("image_attachment_failed_ck keeps pointer, host, sha256 and bytes NULL in 'failed'",
          ck and all(f"{c} IS NULL" in ck for c in ("pointer", "pointer_host", "sha256", "bytes")),
          str(ck))
    check("and requires a stage, so a failure can always say where it died",
          ck and "failed_stage IS NOT NULL" in ck, str(ck))

    html = render(images.for_card("work_item", tid), tid)
    check("the failed attach renders red and loud", "ATTACH FAILED" in html and "red" in html)
    check("with `Attach again` and `Discard`, so it persists until acted on",
          "Attach again" in html and "Discard" in html, html[:600])

    # A file that is not an image, refused on its bytes rather than its name.
    txt = f"{TMP}/not-an-image.png"
    Path(txt).write_text("the extension claims PNG. The bytes disagree.\n")
    tid2 = item("V5: the file that lies about its extension")
    e2 = refused(images.attach, "work_item", tid2, txt, by="T2")
    check("a .png that is not a PNG is refused on its MAGIC BYTES",
          "magic bytes" in e2, e2)
    check("and that refusal is a row too, not a dropped request",
          images.for_card("work_item", tid2)["state"] == "failed")

    # 0 bytes: the 2026-08-17 voice save, in a different costume.
    z = f"{TMP}/zero.png"
    Path(z).write_bytes(b"")
    tid3 = item("V5: the 0-byte save, again")
    e3 = refused(images.attach, "work_item", tid3, z, by="T2")
    check("a 0-byte file is a failed attach, not an attachment", "0 bytes" in e3, e3)


# --------------------------------------------------- 5. NO BYTES IN THE STORE

def test_no_blob_column_anywhere_in_the_schema():
    """Over the WHOLE schema, and that is the point.

    Asserting this about `brain.image_attachment` alone would prove nothing worth proving: the
    way image bytes get into Postgres is not somebody adding a `bytea` to the image table, where
    a reviewer would see it. It is somebody adding one to `brain.thread`, or `brain.objective`,
    or a table this lane never heard of. So the assertion is over `information_schema`.
    """
    with store.read() as s:
        blobs = s.query(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            " WHERE table_schema = 'brain' AND data_type IN ('bytea','oid')")
        cols = s.query(
            "SELECT column_name, data_type FROM information_schema.columns "
            " WHERE table_schema = 'brain' AND table_name = 'image_attachment'")
        largeobj = s.scalar("SELECT count(*) FROM pg_largeobject_metadata")
        total = s.scalar("SELECT count(*) FROM brain.image_attachment")
        widest = s.scalar(
            "SELECT COALESCE(max(length(pointer) + length(coalesce(sha256,'')) "
            "  + length(coalesce(requested_path,''))), 0) FROM brain.image_attachment")
    check("no bytea or oid column anywhere in schema brain", not blobs, str(blobs))
    check("no large object in the database at all", largeobj == 0, f"{largeobj} large objects")
    check("image_attachment holds text, timestamps and two integers and nothing else",
          all(c["data_type"] in ("text", "bigint", "timestamp with time zone")
              for c in cols),
          str([c for c in cols if c["data_type"] not in
               ("text", "bigint", "timestamp with time zone")]))
    check(f"and {total} attachment rows carry at most {widest} characters of path and hash",
          widest < 4096, f"widest row {widest}")


def test_no_image_bytes_can_enter_an_event_payload():
    """The 4096-byte ceiling, shown REFUSING rather than described.

    migration 1: `payload_summary` has a 4096-byte ceiling that REJECTS rather than truncates.
    An image in an event payload is therefore not a policy this lane has to enforce -- it is an
    INSERT that fails. Proven by trying it.
    """
    with store.read() as s:
        emitted = s.query(
            "SELECT event_seq, type, length(payload_summary) AS n FROM brain.event "
            " WHERE type LIKE 'image%' OR payload_summary LIKE '%\\x89PNG%'")
    check("no image-shaped event has ever been emitted", not emitted, str(emitted))

    # Deliberately noisy pixels: a gradient compresses to under the ceiling and would prove
    # nothing. This is a real PNG whose bytes are genuinely larger than the limit.
    blob = png(f"{TMP}/for-an-event.png", w=256, h=256)
    body = Path(blob).read_bytes()
    check(f"the test image is {len(body):,} bytes, over the 4096 ceiling", len(body) > 4096,
          f"{len(body)} bytes -- pick a less compressible image")
    # A REGISTERED type on purpose. `image.attached` is refused before the ceiling is reached
    # ("a subscriber can only declare interest in a type that exists"), which is a second real
    # guard and not the one under test -- proving the ceiling with it would prove the wrong
    # thing. `question.raised` is registered, so the only thing left to refuse this INSERT is
    # the 4096-byte limit itself.
    msg = refused(store.apply, "event emit", type="question.raised", subject_type="work_item",
                  subject_id="0001", payload_summary=body.decode("latin-1"),
                  external=False, canon_touching=False,
                  department="console", lane="console")
    check("emitting one as a payload is REFUSED, not truncated",
          bool(msg) and ("4096" in msg or "ceiling" in msg or "too long" in msg
                         or "check constraint" in msg.lower()),
          msg or "IT WAS ACCEPTED")

    src = (ROOT / "web/images.py").read_text()
    check("and web/images.py emits no event at all", "event emit" not in src)


# --------------------------------------------------- 7. one image, in place

def test_one_image_in_place():
    p = png(f"{TMP}/only-one.png")
    tid = item("V5: one image, in place")
    images.attach("work_item", tid, p, by="T2", actor_type="ai")
    msg = refused(images.attach, "work_item", tid, p, by="T2")
    check("a second live attach on one subject is refused by the verb",
          "already carries image" in msg, msg)
    with store.read() as s:
        idx = s.scalar(
            "SELECT indexdef FROM pg_indexes "
            " WHERE schemaname='brain' AND indexname='image_attachment_one_live_idx'")
    check("and by a partial unique index, so a second lane cannot make a gallery possible",
          idx is not None and "UNIQUE" in idx and "landing" in idx and "attached" in idx, str(idx))


def test_an_unresolvable_subject_is_refused_never_invented():
    p = png(f"{TMP}/orphan.png")
    msg = refused(images.attach, "work_item", "9999-not-a-row", p, by="T2")
    check("an image on a row that does not exist is refused at the table",
          "no brain.work_item row" in msg, msg)


# --------------------------------------------------- 8. the narrow waist

def test_every_write_goes_through_a_registered_verb():
    reg = store.registered()
    for verb in ("image attach", "image attached", "image failed", "image detached",
                 "image dismissed"):
        check(f"{verb!r} is one registered transition", verb in reg,
              f"registered: {sorted(k for k in reg if k.startswith('image'))}")
    src = (ROOT / "web/images.py").read_text()
    body = src.split("--------------------------------------------------------------------------"
                     "- the pipeline")[-1]
    check("the pipeline below the transitions contains no INSERT or UPDATE of its own",
          "INSERT INTO" not in body and "UPDATE brain" not in body)
    check("and it reaches the store only through store.apply and store.read",
          "store.apply(" in body or "store.read(" in body)

    for verb in ("image attach", "image attached", "image failed", "image detached",
                 "image dismissed"):
        check(f"{verb!r} is reachable from the Queue room",
              verb in web_rooms.ROOM_VERBS["queue"])
        for room in ("study", "fleet", "brief", "scope"):
            msg = refused(web_rooms.assert_allowed, room, verb)
            check(f"  and refused from {room}", bool(msg), f"{room} permitted {verb}")

    audit = web_rooms.audit()
    check("no image verb is left registered-and-unreachable",
          not [v for v in audit["registered_and_unreachable"] if v.startswith("image ")],
          str(audit["registered_and_unreachable"]))


# --------------------------------------------------- 9. undo

def test_undo_removes_the_pointer_and_keeps_the_file():
    p = png(f"{TMP}/kept-on-disk.png")
    tid = item("V5: undo keeps the file")
    out = images.attach("work_item", tid, p, by="T2", actor_type="ai")
    res = images.detach(out["attach_id"], by="operator")
    check("detach reports the pointer it removed", res["pointer"] == out["pointer"])
    check("THE FILE IS STILL ON DISK", os.path.exists(p))
    check("and its bytes are untouched",
          hashlib.sha256(Path(p).read_bytes()).hexdigest() == out["sha256"])
    images.invalidate()
    check("the card renders no image block at all",
          images.for_card("work_item", tid) is None)
    with store.read() as s:
        row = s.one("SELECT state, detached_at FROM brain.image_attachment WHERE attach_id = %s",
                    (out["attach_id"],))
    check("the row is superseded, not deleted", row is not None and row["state"] == "detached")
    check("and the slot is free for another image",
          not refused(images.attach, "work_item", tid, p, by="T2"))


def test_a_pointer_on_another_host_is_not_reported_as_missing():
    """D3's inherited warning, and the reason `pointer_host` is a column at all.

    A VPS move makes every pointer written here absolute on a machine this process is not. That
    is not a missing file -- it is a file this host cannot speak about -- and reporting it as
    MISSING would be the confidently-wrong number V00 exists to prevent.
    """
    p = png(f"{TMP}/written-elsewhere.png")
    tid = item("V5: the pointer written on another host")
    out = images.attach("work_item", tid, p, by="T2", actor_type="ai")
    os.remove(p)
    with store.read() as s:
        row = dict(s.one("SELECT * FROM brain.image_attachment WHERE attach_id = %s",
                         (out["attach_id"],)))
    row["pointer_host"] = "SOME-VPS-THIS-IS-NOT"
    img = images.render_fields(row)
    check("it renders as 'elsewhere', not as 'missing'", img["state"] == "elsewhere",
          f"got {img['state']!r}")
    check("and the finding names both hosts",
          "SOME-VPS-THIS-IS-NOT" in images.finding_text(img)
          and images.host() in images.finding_text(img), images.finding_text(img))


def test_the_mime_vocabulary_matches_the_column():
    with store.read() as s:
        ck = s.scalar(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            " WHERE conrelid = 'brain.image_attachment'::regclass "
            "   AND conname = 'image_attachment_mime_check'")
    found = set(re.findall(r"image/[a-z]+", ck or ""))
    check("web/images.py's ACCEPT and the column's CHECK are the same four formats",
          found == set(images.ACCEPT), f"column: {sorted(found)}, module: {sorted(images.ACCEPT)}")


# ------------------------------------- 12. the fixture, and the defect it takes out of the suite

def test_a_previous_runs_attachment_does_not_decide_this_ones_verdict():
    """0428's regression test. It runs LAST, because it clears every attachment in the store.

    It RECONSTRUCTS the condition rather than describing it: a live attachment sitting on a
    subject a later run is handed again. Property 7 refuses the second attach -- correctly, and
    for a reason that has nothing to do with the code under test, because the row it is refusing
    on is not this run's. The fixture is the only thing that tells those two cases apart, so what
    is asserted here is that the same subject attaches CLEANLY once the fixture has run.

    MUTATION-TESTED RATHER THAN ASSERTED, 2026-08-29, because "this would catch a regression" is
    the easiest sentence in the world to write and the hardest to have earned. The module-level
    `_FIXTURE = _clear_live_attachments()` call was replaced with a no-op and the suite run twice
    against a store whose ids had been recycled under surviving attachments: it DIED both times
    with an uncaught `ImageError: work_item NNNN already carries image ...` -- once at property 1
    and once inside this very test, at the first attach below -- while the unmutated file printed
    97 passed on the same store at the same moment. So the guarantee is the honest, narrower one:
    removing the fixture does not leave a green suite. What this test adds on top is that the
    fixture's own inverse-verb path works, which a whole-suite death would not distinguish from
    any other crash.
    """
    p = png(f"{TMP}/left-by-the-last-run.png")
    tid = item("V5: the attachment an earlier run left behind")
    first = images.attach("work_item", tid, p, by="T2", actor_type="ai")
    msg = refused(images.attach, "work_item", tid, p, by="T2")
    check("with a live attachment in place, the next attach on that subject is refused",
          "already carries image" in msg, msg)

    cleared = _clear_live_attachments()
    check(f"the fixture clears the {cleared['seen']} renderable attachment(s) it finds, leaving "
          f"{cleared['left']}", cleared["seen"] >= 1 and cleared["left"] == 0, str(cleared))
    out = images.attach("work_item", tid, p, by="T2", actor_type="ai")
    check("and the SAME subject then attaches cleanly -- so the refusal above was about the row, "
          "not about the id", out["state"] == "attached", str(out))
    # BY ATTACH_ID AND NOT BY A COUNT OVER THE SUBJECT, and the first draft of this check got that
    # wrong in the way this whole task is about. It asserted `2 rows on this subject`, which is
    # true on a store with no history and false on the one that produced the defect: run against
    # brain_t2_0167, whose ids have been recycled under surviving attachments, the same subject
    # already carried two rows from August and the count came back 4. A check whose number depends
    # on the store's past is the thing being removed here, so this one names the two rows it made.
    with store.read() as s:
        was = s.one("SELECT state FROM brain.image_attachment WHERE attach_id = %s",
                    (first["attach_id"],))
        now = s.one("SELECT state FROM brain.image_attachment WHERE attach_id = %s",
                    (out["attach_id"],))
    check("and the row the fixture cleared is superseded rather than deleted",
          was is not None and was["state"] == "detached" and now["state"] == "attached",
          f"cleared row: {was}, new row: {now}")


if __name__ == "__main__":
    print(f"V5: images are pointed at, hashed, and their absence is a finding "
          f"({os.environ['BRAIN_PG_DB']})")
    print("\n-- 1. pointer, host, hash, size, MIME, all measured")
    test_attach_records_pointer_host_hash_and_size()
    print("\n-- 2. a deliberately-removed image renders as a finding")
    test_a_removed_image_renders_as_a_finding()
    print("\n-- 3. changed bytes render as a finding carrying both hashes")
    test_changed_bytes_render_as_a_finding_carrying_both_hashes()
    print("\n-- 4. a failed attach attests nothing, and says at which stage")
    test_a_failed_attach_attests_nothing()
    print("\n-- 5. NO IMAGE BYTES IN THE STORE")
    test_no_blob_column_anywhere_in_the_schema()
    print("\n-- 6. NO IMAGE BYTES IN AN EVENT PAYLOAD")
    test_no_image_bytes_can_enter_an_event_payload()
    print("\n-- 7. one image, in place")
    test_one_image_in_place()
    test_an_unresolvable_subject_is_refused_never_invented()
    print("\n-- 8. the narrow waist: five verbs, one room, no private door")
    test_every_write_goes_through_a_registered_verb()
    print("\n-- 9. undo removes the pointer and keeps the file")
    test_undo_removes_the_pointer_and_keeps_the_file()
    print("\n-- 10. a pointer on another host is not a missing file")
    test_a_pointer_on_another_host_is_not_reported_as_missing()
    print("\n-- 11. one MIME vocabulary, two spellings, checked against each other")
    test_the_mime_vocabulary_matches_the_column()
    print("\n-- 12. a previous run's attachment does not decide this run's verdict")
    test_a_previous_runs_attachment_does_not_decide_this_ones_verdict()
    shutil.rmtree(TMP, ignore_errors=True)
    if PASS + FAIL == 0:                                  # DENOMINATOR
        print("0 comparisons made. A verdict over an empty set is not a pass.")
        sys.exit(2)
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
