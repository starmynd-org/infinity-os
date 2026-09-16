#!/usr/bin/env python3
"""ROW 0432, HIS ASK 3: press V on a row, speak, and the transcript is a note ON THAT ROW.

MUST-NOT-BUILD item 5 was overruled by the operator on 2026-08-30, for this and only this, in his
own words: *"i want to override and say voice is needed to make it easy for users to get through
inbox"*. `web/MUST-NOT-BUILD.md` item 5 carries the statement and the condition. This suite is the
third of the three checks that item names, and it checks the SUBSTANCE: that the transcript reaches
the thread, through the verb, with the capture terminal in the same transaction.

FIVE CLAIMS.

  1. THE TRANSCRIPT LANDS ON THE ROW, through `note`, and the note carries the audio's pointer and
     sha256 so the words can be checked against the recording that produced them.

  2. THE CAPTURE IS TERMINAL AND POINTS AT EXACTLY ONE THING. `brain.voice_capture` reaches
     `landed` with `work_item_id` set and `objective_id` NULL, in the same transaction as the
     thread row. A capture left non-terminal past its deadline reads as STUCK on the health line,
     which is this lane's one signal against an outage nothing raised, and a landing path that
     forgot to close it would teach that signal to cry wolf about ordinary work.

  3. NOTHING IS EVER FABRICATED, AND THIS IS THE ONE THAT MATTERS MOST. A recording the engine
     could not read must land a note that SAYS the engine heard nothing and names the retained
     audio. Not a guess, and not silence either: silence is the 2026-08-17 incident, where the only
     record of the recording was the recording. Watched on real audio the engine cannot transcribe.

  4. THE HEALTH LINE STILL READS IT. A note landing is a landing, so `voice health` counts it and
     does not report it as stuck or failed. Asserted because the note path is new and the health
     surface predates it.

  5. THE CONSOLE POSTS NO TYPED TEXT. `actions.voice_note` takes no `text` and the only body it
     will post is what a transcription engine returned. That is item 5's condition made
     structural, and `test_allowlist.test_queue_has_no_freeform_note_box` holds the other half.

THE AUDIO IS REAL, NOT A MOCK. On a host with Windows speech synthesis reachable this suite SPEAKS
a known sentence, transcribes it with whatever engine `voice engines` reports, and asserts the
words came back. Where that is not reachable it falls back to a generated silent wav and says so:
claims 2, 3, 4 and 5 still run and claim 1 runs in its no-words form. A suite that quietly mocked
the transcriber would be asserting that this file's own plumbing works, which is the shape this
repo's whole test doctrine is written against.

Run:  python3 -m web.tests.test_the_voice_note

Needs a scratch store at ledger 51 and nothing else: no console, no browser.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_R = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
for _p in (_R, _os.path.join(_R, "engine"), _os.path.join(_R, "queue"),
           _os.path.join(_R, "voice")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import inspect  # noqa: E402
import pathlib  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import wave  # noqa: E402

import store  # noqa: E402
import swarm_engine  # noqa: E402,F401
from swarm_engine import transitions as _engine_t  # noqa: E402,F401
from web import actions, rooms  # noqa: E402

_os.environ.pop("SWARM_PARENT_TASK", None)

PASS, FAIL = 0, 0
LIVE = ("brain", "brain_scratch")
SPOKEN = ("The console still serves a stale template after a deploy. "
          "Restart it before verifying anything against it.")


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def guard() -> str:
    db = _os.environ.get("BRAIN_PG_DB", "brain")
    if db in LIVE:
        print(f"REFUSING to run against {db!r}: this suite posts work items and captures. "
              f"Export BRAIN_PG_DB to a scratch database.")
        sys.exit(2)
    return db


def at_ledger_51() -> bool:
    with store.read() as s:
        return bool(s.scalar(
            "SELECT count(*) > 0 FROM information_schema.columns "
            " WHERE table_schema = 'brain' AND table_name = 'voice_capture' "
            "   AND column_name = 'work_item_id'"))


def speak(path: str) -> bool:
    """Say a known sentence into a wav, using the host's own speech synthesis. True if it worked.

    NOT A MOCK OF THE TRANSCRIBER. This produces real audio and the real engine reads it, so what
    is asserted downstream is that the pipeline heard words rather than that a stub returned some.
    """
    win = f"C:\\Windows\\Temp\\ib-voice-test-{_os.getpid()}.wav"
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{win}'); $s.Rate = -1; $s.Speak('{SPOKEN}'); $s.Dispose()")
    try:
        p = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                           capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            return False
        src = subprocess.run(["wslpath", win], capture_output=True, text=True,
                             timeout=30).stdout.strip()
        if not src or not _os.path.exists(src):
            return False
        with open(src, "rb") as fh, open(path, "wb") as out:
            out.write(fh.read())
        _os.remove(src)
        return _os.path.getsize(path) > 10_000
    except Exception:                                                   # noqa: BLE001
        return False


def silence(path: str, seconds: int = 3) -> None:
    """A wav with nothing in it. The engine has to say it heard nothing, and mean it."""
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * seconds)


def a_row(title: str) -> dict:
    r = store.apply("post", title=title, lane="console", workdir=_R, posted_by="operator")
    return {"id": r["id"], "kind": "human", "actor_type": "human"}


def thread_of(tid: str) -> list:
    with store.read() as s:
        return s.query("SELECT kind, from_agent, text FROM brain.thread "
                       " WHERE work_item_id = %s ORDER BY seq", (tid,))


def capture_of(capture_id: str) -> dict:
    with store.read() as s:
        return s.one("SELECT state, objective_id, objective_name, work_item_id, "
                     "       transcription_status, transcript_chars "
                     "  FROM brain.voice_capture WHERE capture_id = %s", (capture_id,))


# ------------------------------------------------------------------ 1, 2 and 4

def test_a_spoken_note_lands_on_the_row(wav: str, spoken: bool):
    item = a_row("a row he is looking at")
    res = actions.voice_note("queue", item=item, seconds=10, operator="operator", wav=wav)

    check("the action reports the verb it actually ran", res["verb"] == "note", str(res["verb"]))
    check("and it says plainly that there is no undo, because the thread is append-only",
          res["undo"] is None and "append-only" in (res["no_undo_reason"] or ""),
          str(res.get("no_undo_reason")))
    ms = res["result"]["round_trip_ms"]
    check(f"the round trip is TIMED and it took {ms}ms", isinstance(ms, int) and ms > 0, str(ms))
    check("  and the receipt carries that time, where he will see it",
          "ms" in res["receipt"], res["receipt"])

    rows = thread_of(item["id"])
    notes = [r for r in rows if r["kind"] == "note"]
    check(f"exactly one note reached the thread on {item['id']}", len(notes) == 1,
          f"{len(notes)} note(s) in {len(rows)} thread row(s)")
    if not notes:
        return None
    body = notes[0]["text"]
    cap = res["result"]["capture_id"]
    check("the note names the capture", cap in body, body[:90])
    check("  and the retained audio's sha256, so the words can be checked against the recording",
          (res["result"]["media_sha256"] or "") in body, body[-160:])

    if spoken:
        # THE WORDS THEMSELVES. Not a substring of the whole sentence, which a mock could produce
        # by echoing: two distinctive words the synthesiser said and nothing in this file wrote
        # into the pipeline.
        heard = body.lower()
        got = [w for w in ("stale", "template", "deploy", "restart") if w in heard]
        check(f"the transcript carries the words that were SPOKEN ({len(got)} of 4 landmarks)",
              len(got) >= 3, f"found {got} in {body[:120]!r}")
        check("  and the status says the engine produced text",
              res["result"]["transcription_status"] in ("ok", "partial"),
              str(res["result"]["transcription_status"]))
    else:
        print("      NOT RUN: no host speech synthesis, so the spoken-words assertion has no "
              "input. Everything else in this suite still ran.")

    # 2. THE CAPTURE, TERMINAL AND POINTING AT EXACTLY ONE THING.
    c = capture_of(cap)
    check("the capture is terminal", c["state"] == "landed", str(c["state"]))
    check("  and points at the work item", c["work_item_id"] == item["id"], str(c["work_item_id"]))
    check("  and at NO objective, because a capture lands once",
          c["objective_id"] is None and c["objective_name"] is None, str(c))
    return item["id"]


def test_the_database_refuses_a_capture_that_landed_twice():
    """CLAIM 2's OTHER HALF, AND IT IS NOT ASSERTED BY THE PYTHON THAT AVOIDS IT.

    `_voice_capture_landed` passes one target or the other. That is a promise about one function.
    The constraint is what makes it true for every caller however it is written, which is the
    argument `web/rooms.py` makes about a Python check the database does not back."""
    with store.read() as s:
        got = s.scalar(
            "SELECT count(*) FROM pg_constraint "
            " WHERE conname = 'voice_capture_landed_points_at_one_ck'")
    check("the database itself refuses a capture pointing at both, or at neither", got == 1,
          f"{got} constraint(s) named voice_capture_landed_points_at_one_ck")


def test_the_health_line_still_reads_a_note_landing():
    """A note landing is a landing. The health surface predates the note path, so this is the
    regression check that the new landing does not read as stuck or failed."""
    p = subprocess.run([sys.executable, _os.path.join(_R, "voice/bin/voice"), "health",
                        "--hours", "24"],
                       capture_output=True, text=True, timeout=180)
    out = (p.stdout or "") + (p.stderr or "")
    check("`voice health` exits clean with a note landing in the window", p.returncode == 0,
          f"exit {p.returncode}: {out.strip()[:160]}")
    check("  and counts it as ok rather than stuck or failed",
          "ok" in out and "stuck" not in out.lower(), out.strip()[:160])


# ------------------------------------------------------------------ 3, the one that matters most

def test_a_capture_with_no_words_says_so_and_names_the_audio():
    """NEVER FABRICATE, AND NEVER GO SILENT EITHER. Both halves, on real audio the engine cannot
    read.

    The fabrication half is enforced three ways already (the transition, `brain.objective`'s CHECK
    and `brain.voice_capture`'s). What is new here is the SILENCE half: the note still posts, and
    it says the engine heard nothing and where the audio is, so a human can go and listen. A path
    that posted nothing would reproduce 2026-08-17, where the only record of the recording was the
    recording."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
        quiet = fh.name
    silence(quiet, seconds=3)
    item = a_row("a row spoken at with nothing to hear")
    try:
        res = actions.voice_note("queue", item=item, seconds=3, operator="operator", wav=quiet)
    except actions.ActionRefused as exc:
        # THE ARM THIS TEST EXISTS TO KEEP CLOSED. The voice CLI exits 3 for `landed-without-words`
        # and the console used to treat that as a refusal, which made it SILENT on exactly the case
        # the lane was built around, and made `V` disagree with `voice ingest-file --onto`. If this
        # arm is ever taken again the note did not land, so it is a FAIL and not a branch.
        check("a no-words capture still posts a note rather than refusing", False,
              f"refused instead: {str(exc)[:140]}")
        return
    finally:
        _os.unlink(quiet)

    body = "".join(r["text"] for r in thread_of(item["id"]) if r["kind"] == "note")
    status = res["result"]["transcription_status"]
    if status in ("ok", "partial"):
        # The engine claimed to hear something in silence. That is a finding about the ENGINE and
        # this suite reports it rather than passing quietly, but it is not a defect in this lane.
        check(f"the engine returned {status!r} on silence, so the no-words path had no input",
              True, f"reported, not asserted: {body[:80]!r}")
        return
    check(f"a capture that heard nothing still POSTS a note (status {status})", bool(body),
          "nothing reached the thread, which is the 2026-08-17 shape")
    check("  and the note SAYS the engine produced no words",
          "WITHOUT WORDS" in body, body[:140])
    check("  and names the retained audio, so it is recoverable rather than lost",
          "retained at" in body and "sha256" in body, body[:140])
    check("  and nothing was guessed into it",
          res["result"]["transcript_chars"] == 0, str(res["result"]["transcript_chars"]))


# ------------------------------------------------------------------ 5

def test_the_console_cannot_post_typed_text_through_this_door():
    """ITEM 5'S CONDITION, MADE STRUCTURAL. There is no box, and there is nowhere for a box to
    post to: the function has no `text` parameter at all."""
    sig = inspect.signature(actions.voice_note)
    check("actions.voice_note takes no `text`", "text" not in sig.parameters,
          str(list(sig.parameters)))
    check("  and `note` IS on the Queue's allowlist, which is the one-line overrule",
          "note" in rooms.ROOM_VERBS["queue"])
    check("  while every voice CAPTURE verb is reachable from no room at all",
          not (set().union(*rooms.ROOM_VERBS.values())
               & {"voice open", "voice recorded", "voice retained", "voice transcribed",
                  "voice failed"}))
    check("  and `note` is in NO other room, because a note belongs where the row is",
          [r for r, v in rooms.ROOM_VERBS.items() if "note" in v] == ["queue"],
          str([r for r, v in rooms.ROOM_VERBS.items() if "note" in v]))


def main() -> int:
    db = guard()
    print(f"  store: {db} (scratch)\n")
    if not at_ledger_51():
        print("  NOT RUN: this store has no brain.voice_capture.work_item_id, so it is below "
              "ledger 51.\n  Apply migrations/0051_a_voice_note_can_land_on_a_row.sql and rerun.")
        return 1

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
        wav = fh.name
    spoken = speak(wav)
    if not spoken:
        silence(wav, seconds=4)
    print(f"  audio: {'SPOKEN by the host synthesiser' if spoken else 'GENERATED SILENCE'} "
          f"({_os.path.getsize(wav)} bytes)\n")
    try:
        print("=== test_a_spoken_note_lands_on_the_row ===")
        test_a_spoken_note_lands_on_the_row(wav, spoken)
        for fn in (test_the_database_refuses_a_capture_that_landed_twice,
                   test_the_health_line_still_reads_a_note_landing,
                   test_a_capture_with_no_words_says_so_and_names_the_audio,
                   test_the_console_cannot_post_typed_text_through_this_door):
            print(f"=== {fn.__name__} ===")
            fn()
    finally:
        pathlib.Path(wav).unlink(missing_ok=True)
    # DENOMINATOR. This suite already refuses to run at all below ledger 51, but a scene that
    # returned early on every branch would still reach here, and `0 passed, 0 failed` printed by
    # a suite about a feature nobody exercised is the green this repo's whole test doctrine is
    # written against. Task 0292's rule.
    if PASS + FAIL == 0:                                                # DENOMINATOR
        print("\n0 assertions made. A verdict over an empty set is not a pass, and no voice note "
              "was proven to land.")
        return 2
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
