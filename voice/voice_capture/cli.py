"""`voice` -- record, transcribe, land. A thin wrapper over verbs and nothing else.

It owns no state transitions. Every state change here is one `store.apply(verb, ...)` call, the
same call the console, an MCP tool or a phone client would make, which is the narrow waist V00
freezes. The LANDING in particular adds no verb at all: `cmd_land` calls `intake`, the transition
that has always owned putting an objective in the admiral's inbox, with more keyword arguments.

EXIT CODES, because the operator's cron will branch on them and not on stdout:
    0  ok
    1  error
    2  nothing to do
    3  a capture failed -- loud, and there is a counted row saying which stage
    4  the health line is not clean

LOUD MEANS THREE THINGS AT ONCE and any one of them alone is the swallow this lane exists to
remove: a non-zero exit code, a sentence on stderr that names the capture and the stage, and a
row in `brain.voice_capture` that a later reader can count without having been there.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import store

from . import config, reads, recorder, retain, transcribe
from . import transitions as _v  # noqa: F401  (registers the capture verbs)
from .transitions import VoiceError

OK, ERR, EMPTY, CAPTURE_FAILED, UNHEALTHY = 0, 1, 2, 3, 4


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_capture_id(now: datetime = None) -> str:
    return "vc-{}-{}".format((now or _now()).strftime("%Y%m%dT%H%M%SZ"), secrets.token_hex(3))


def loud(capture_id: str, stage: str, detail: str) -> None:
    """Print the failure where a human and a log scraper both find it.

    stderr and not stdout: the happy path's JSON goes to stdout and a caller piping it into `jq`
    must not have a failure quietly become part of its input.
    """
    print(f"VOICE CAPTURE FAILED  capture={capture_id}  stage={stage}\n  {detail}",
          file=sys.stderr)


def fail_capture(capture_id: str, stage: str, detail: str) -> int:
    """Count it, then say it. In that order, and the order is the point.

    If the process dies between the two, the row is already in the store and `voice health` finds
    it. If it were the other way round, the loudest possible failure would still be one nobody
    can count tomorrow -- which is the 2026-08-17 incident's structure, not its content.
    """
    try:
        store.apply("voice failed", capture_id=capture_id, stage=stage, detail=detail)
    except Exception as exc:  # noqa: BLE001
        # The store is unreachable, and this is the one moment where being unable to count a
        # failure must not stop us reporting it.
        print(f"VOICE: could not record the failure of {capture_id} in the store "
              f"({exc.__class__.__name__}: {exc}). The failure itself follows and is REAL; what "
              f"is missing is the counted row.", file=sys.stderr)
    loud(capture_id, stage, detail)
    return CAPTURE_FAILED


# ------------------------------------------------------------------ record

def cmd_record(args) -> int:
    capture_id = args.capture_id or new_capture_id()
    seconds = args.seconds
    scratch = Path(args.scratch_dir or config.SCRATCH_DIR) / f"{capture_id}.wav"

    # THE ROW BEFORE THE MICROPHONE. Everything else in this lane follows from this line being
    # above the recording rather than below it.
    due = _now() + timedelta(seconds=seconds + config.STAGE_SLACK_SECONDS)
    store.apply("voice open", capture_id=capture_id, due_at=due, host=config.host(),
                source="mic", requested_seconds=seconds, scratch_pointer=str(scratch),
                actor_type="ai", produced_by=args.produced_by or "voice-cli")
    print(f"capture {capture_id} open, recording {seconds}s, due {due:%Y-%m-%dT%H:%M:%SZ}")

    try:
        rec = recorder.record(scratch, seconds)
    except recorder.SaveFailed as exc:
        return fail_capture(capture_id, "save", str(exc))
    except Exception as exc:  # noqa: BLE001 -- an unexpected error is still a failed save
        return fail_capture(capture_id, "save",
                            f"unexpected {exc.__class__.__name__} while recording: {exc}")

    store.apply("voice recorded", capture_id=capture_id, scratch_pointer=str(rec.path),
                media_duration_s=rec.duration_s,
                due_at=_now() + timedelta(seconds=config.STAGE_SLACK_SECONDS))
    print(f"recorded {rec.bytes} bytes, {rec.duration_s}s of audio "
          f"({rec.elapsed_s}s wall clock, {rec.wave_in_devices} input device(s))")

    return _retain_transcribe_land(args, capture_id, rec.path, rec.duration_s)


def cmd_ingest_file(args) -> int:
    """Take a wav that already exists. The same pipeline, the same verbs, the same checks.

    This is not a test hook. It is how an already-recorded file -- a phone memo, a Zoom export,
    the file the operator saved by hand this morning -- enters the system, and it goes through
    exactly the stages a live recording does so that there is one path to be right about.
    """
    src = Path(args.wav).expanduser().resolve()
    capture_id = args.capture_id or new_capture_id()
    due = _now() + timedelta(seconds=config.STAGE_SLACK_SECONDS)
    store.apply("voice open", capture_id=capture_id, due_at=due, host=config.host(),
                source="file", requested_seconds=None, scratch_pointer=str(src),
                actor_type="ai", produced_by=args.produced_by or "voice-cli")
    print(f"capture {capture_id} open, from file {src}")
    try:
        rec = recorder.save_check(src, args.expect_seconds or 1)
    except recorder.SaveFailed as exc:
        return fail_capture(capture_id, "save", str(exc))
    store.apply("voice recorded", capture_id=capture_id, scratch_pointer=str(rec.path),
                media_duration_s=rec.duration_s, due_at=due)
    print(f"accepted {rec.bytes} bytes, {rec.duration_s}s of audio")
    return _retain_transcribe_land(args, capture_id, rec.path, rec.duration_s,
                                   keep_source=not args.consume)


def _retain_transcribe_land(args, capture_id: str, wav: Path, duration_s: float,
                            keep_source: bool = False) -> int:
    # ---------------------------------------------------------- upload
    try:
        if keep_source:
            # An already-durable source is copied, not consumed. Deleting the operator's own file
            # because we made a second copy of it is not this lane's call to make.
            import shutil
            tmp = Path(config.SCRATCH_DIR) / f"{capture_id}.wav"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(wav, tmp)
            wav = tmp
        held = retain.retain(wav, capture_id, retain_dir=args.retain_dir)
    except retain.RetainFailed as exc:
        return fail_capture(capture_id, "upload", str(exc))
    except Exception as exc:  # noqa: BLE001
        return fail_capture(capture_id, "upload",
                            f"unexpected {exc.__class__.__name__} while retaining: {exc}")

    store.apply("voice retained", capture_id=capture_id, media_pointer=str(held.path),
                media_pointer_host=held.host, media_sha256=held.sha256,
                media_bytes=held.bytes, media_kind=held.kind,
                # THE DEADLINE IS THE RUNNING ENGINE'S, not a constant. whisper.cpp is ~13x
                # slower than windows-sapi on the same audio; a deadline sized for SAPI and then
                # spent on whisper reports a healthy capture as STUCK, which would make the one
                # signal that catches an outage nobody raised cry wolf about normal work.
                due_at=_now() + timedelta(
                    seconds=max(config.TRANSCRIBE_TIMEOUT_FLOOR_S,
                                int(duration_s * transcribe.timeout_ratio()) + 60)
                    + config.STAGE_SLACK_SECONDS))
    print(f"retained {held.path} ({held.bytes} bytes, sha256 {held.sha256})")

    # ---------------------------------------------------------- transcribe
    t = transcribe.transcribe(held.path, duration_s)
    store.apply("voice transcribed", capture_id=capture_id, transcription_engine=t.engine,
                transcription_status=t.status, transcript_chars=len(t.text),
                transcription_confidence=t.confidence,
                transcription_accuracy_class=t.accuracy_class,
                due_at=_now() + timedelta(seconds=config.STAGE_SLACK_SECONDS))

    if t.status in (transcribe.NULL, transcribe.UNAVAILABLE):
        # NOT a swallow and NOT the end of the capture. The audio is retained and pointed at, so
        # the note still lands -- with an EMPTY body, which is the honest thing to store and
        # which the table's CHECK enforces. It is loud here and counted as
        # `landed-without-words` on the health line.
        loud(capture_id, "transcribe",
             f"no transcript: status={t.status} engine={t.engine}. {t.detail} "
             f"The audio IS retained at {held.path} (sha256 {held.sha256}), so this is "
             f"recoverable rather than lost: nothing was guessed and nothing was written into "
             f"the body.")
    else:
        print(f"transcribed {len(t.text)} chars, status={t.status}, engine={t.engine}, "
              f"confidence={t.confidence}, segments={t.segments}")
        if t.detail:
            print(f"  note: {t.detail}")

    # ---------------------------------------------------------- land
    #
    # TWO LANDINGS AND NEITHER IS A NEW VERB (row 0432, his ask 3, migration 51).
    #
    #   no --onto   the transcript becomes one unclassified objective a human sorts, through
    #               `intake`. This is what the lane shipped and it is the right landing for a
    #               morning monologue: the machine classifies nothing.
    #   --onto ID   the transcript becomes a NOTE on that row, through `note`. This is what he
    #               asked for on 2026-08-30, and the argument is his: *"voice is needed to make it
    #               easy for users to get through inbox"*. A monologue that lands in the intake
    #               inbox is not a note on the item in front of him, it is a second thing to sort,
    #               which is MORE inbox rather than less.
    #
    # The capture closes in the same transaction either way, through the same function, because
    # the invariant is about the capture and not about which landing it took.
    if getattr(args, "no_land", False):
        return _print_for_a_caller_that_lands_it_itself(capture_id, held, t, duration_s)
    onto = getattr(args, "onto", None)
    if onto:
        return _land_as_note(args, capture_id, held, t, duration_s, onto)

    name = args.name or capture_id
    try:
        r = store.apply(
            "intake",
            name=name,
            body=t.text,
            source_name=held.path.name,
            source_signature=held.sha256,
            bytes_=len(t.text.encode("utf-8")),
            intake_format="voice",
            media_pointer=str(held.path),
            media_pointer_host=held.host,
            media_sha256=held.sha256,
            media_bytes=held.bytes,
            media_kind=held.kind,
            media_duration_s=duration_s,
            transcription_engine=t.engine,
            transcription_status=t.status,
            capture_id=capture_id,
        )
    except Exception as exc:  # noqa: BLE001
        return fail_capture(capture_id, "land",
                            f"{exc.__class__.__name__}: {exc}. The audio is retained at "
                            f"{held.path} and nothing about it is lost; what did not happen is "
                            f"the objective row.")

    print(json.dumps({"capture_id": capture_id, "landed": r,
                      "media_pointer": str(held.path), "media_sha256": held.sha256,
                      "transcription_status": t.status, "transcript_chars": len(t.text)},
                     indent=2))
    if t.status in (transcribe.NULL, transcribe.UNAVAILABLE):
        return CAPTURE_FAILED
    return OK


def note_text(capture_id: str, held, t, duration_s: float) -> str:
    """The note's body: the transcript in full, then one line saying where the audio is.

    ONE FORMATTER, TWO CALLERS. The CLI's `--onto` lands this itself; the console gets it out of
    `--no-land` and posts it through its own door. Two copies of this string would drift, and the
    half that drifts is the provenance line, which is the half that makes the note checkable.

    THE POINTER AND THE HASH ARE NOT DECORATION. A note that said "voice note attached" and pointed
    nowhere would be the 0-byte save with better manners, which is migration 25's own sentence
    about a pointer with no bytes behind it.

    AN EMPTY TRANSCRIPT STILL PRODUCES A NOTE. `null` and `unavailable` mean the engine produced
    nothing; the honest note says so and names the retained audio so a human can go and listen.
    Fabricating words is refused everywhere else in this lane and does not start here. What must
    not happen is SILENCE: a capture that produced no words and no note is the 2026-08-17 incident,
    in which the only record of the recording was the recording.
    """
    body = (t.text or "").strip()
    if body:
        return (f"{body}\n\n"
                f"[voice note {capture_id}, {duration_s:.0f}s, {t.engine}, {t.status}, "
                f"accuracy {t.accuracy_class}. Audio retained at {held.path} on {held.host}, "
                f"sha256 {held.sha256}]")
    return (f"[voice note {capture_id} landed WITHOUT WORDS: {duration_s:.0f}s of audio, "
            f"engine {t.engine} returned status {t.status}. Nothing was guessed. The audio IS "
            f"retained at {held.path} on {held.host}, sha256 {held.sha256}, so this is "
            f"recoverable: listen to it, or transcribe it again.]")


def _print_for_a_caller_that_lands_it_itself(capture_id, held, t, duration_s) -> int:
    """`--no-land`: stop at `transcribed` and hand the caller everything it needs to land.

    THIS IS WHAT THE CONSOLE USES AND IT IS WHY THE CONSOLE NEEDS NO NEW POWERS. Pressing `V` on a
    row runs this as a subprocess, which does the capture as ITSELF, exactly as it does when he
    runs it in a terminal. The console then posts the note through its own single write door with
    the one verb it is allowed. So the Queue room gains `note` and nothing else: it does not gain
    `voice open`, `voice recorded`, `voice retained` or `voice transcribed`, none of which a
    rendering surface has any business being able to call.

    THE CAPTURE IS LEFT NON-TERMINAL ON PURPOSE AND THAT IS SAFE, because `note` closes it. If the
    caller dies before posting, the row sits past its `due_at` and `voice health` reports it STUCK,
    which is exactly the outcome this lane wants: a capture that began and did not finish is
    visible rather than silent. A `--no-land` that marked the capture landed would be asserting an
    outcome it had not seen.
    """
    print(json.dumps({
        "capture_id": capture_id,
        "landed": False,
        "note_text": note_text(capture_id, held, t, duration_s),
        "transcript": t.text,
        "transcription_status": t.status,
        "transcription_engine": t.engine,
        "transcript_chars": len(t.text),
        "audio_seconds": round(duration_s, 2),
        "media_pointer": str(held.path),
        "media_sha256": held.sha256,
    }, indent=2))
    if t.status in (transcribe.NULL, transcribe.UNAVAILABLE):
        return CAPTURE_FAILED
    return OK


def _land_as_note(args, capture_id: str, held, t, duration_s: float, onto: str) -> int:
    """Land the transcript as a note on one named row. Row 0432, his ask 3.

    THE POINTER GOES ON THE THREAD AND THE AUDIO IS NOT SUMMARISED. `brain.thread` holds text, so
    the note carries the transcript in full plus one line naming where the audio is and what its
    hash is. A note that said "voice note attached" and pointed nowhere would be the 0-byte save
    with better manners, which is the sentence migration 25 already uses about a pointer with no
    bytes behind it.

    AN EMPTY TRANSCRIPT STILL POSTS. `null` and `unavailable` mean the engine produced nothing, and
    the honest note says so and names the retained audio, so a human can go and listen. Fabricating
    words is refused everywhere else in this lane and it is not going to start here. What must not
    happen is silence: a capture that produced no words and no note is the 2026-08-17 incident, in
    which the only record of the recording was the recording.
    """
    onto = str(onto).strip()
    text = note_text(capture_id, held, t, duration_s)
    started = time.monotonic()
    try:
        store.apply("note", id=onto, text=text,
                    agent=args.produced_by or "voice-cli", capture_id=capture_id)
    except Exception as exc:  # noqa: BLE001
        return fail_capture(capture_id, "land",
                            f"{exc.__class__.__name__}: {exc}. The audio is retained at "
                            f"{held.path} and nothing about it is lost; what did not happen is "
                            f"the note on {onto}.")
    # THE ROUND TRIP IS TIMED, because his definition of done says it is. A voice affordance whose
    # cost nobody measured is one he tries twice and stops using.
    print(json.dumps({"capture_id": capture_id, "noted_on": onto,
                      "media_pointer": str(held.path), "media_sha256": held.sha256,
                      "transcription_status": t.status, "transcript_chars": len(t.text),
                      "audio_seconds": round(duration_s, 2),
                      "land_ms": round((time.monotonic() - started) * 1000, 1)}, indent=2))
    if t.status in (transcribe.NULL, transcribe.UNAVAILABLE):
        return CAPTURE_FAILED
    return OK


# ------------------------------------------------------------------ health

def cmd_health(args) -> int:
    """The line that would have caught 2026-08-17, and the line that would have caught this
    morning's outage.

    Three questions, and the third is the one no error handler asks:
      1. did anything fail outright?
      2. did anything land without words?
      3. is anything STUCK -- opened, never terminal, past its deadline -- and, the same question
         from the other side, WAS THERE A CAPTURE AT ALL TODAY? A day with zero captures on a
         machine whose operator records daily is the silence itself, and it produces no failed
         row to count because nothing ever started.
    """
    rows = reads.counts(args.hours)
    tally = {r["verdict"]: int(r["n"]) for r in rows}
    total = sum(tally.values())
    today = reads.captures_today(args.utc_offset)

    print(f"voice health, last {args.hours}h on {config.host()}")
    if not rows:
        print("  no captures in the window")
    for verdict in ("ok", "in-flight", "landed-without-words", "stuck", "failed"):
        if verdict in tally:
            print(f"  {verdict:<22} {tally[verdict]}")
    print(f"  captures opened today  {today}  (operator day, UTC+{args.utc_offset:g})")
    print(f"  captures compared      {total}  (every row in the window, not a sample)")

    # stdout first, then stderr. Without the flush the two streams interleave by buffer size and
    # the failure block prints ABOVE the header it belongs under, which is how a reader skimming
    # a cron mail attributes a stuck capture to the wrong run.
    sys.stdout.flush()

    problems = []
    for row in reads.bad(args.hours):
        problems.append(row)
        print(f"\n  {row['verdict'].upper()}  {row['capture_id']}  state={row['state']}",
              file=sys.stderr)
        if row["failure_stage"]:
            print(f"    stage={row['failure_stage']}  {row['failure_detail']}", file=sys.stderr)
        if row["verdict"] == "stuck":
            print(f"    opened {row['opened_at']:%Y-%m-%dT%H:%M:%SZ}, due "
                  f"{row['due_at']:%Y-%m-%dT%H:%M:%SZ}, still {row['state']!r} after "
                  f"{row['open_for']}. Nothing raised: this capture began and did not finish, "
                  f"which is the shape a six-hour network outage has and the shape no exception "
                  f"handler catches.", file=sys.stderr)
        if row["verdict"] == "landed-without-words":
            print(f"    audio retained at {row['media_pointer']} on {row['media_pointer_host']} "
                  f"(sha256 {row['media_sha256']}), transcription_status="
                  f"{row['transcription_status']}, engine={row['transcription_engine']}. "
                  f"Recoverable: transcribe the same file again with a better engine.",
                  file=sys.stderr)

    missing_today = args.expect_daily and today == 0
    if missing_today:
        print("\n  NO CAPTURE TODAY. The operator records a monologue daily and there is no row "
              "for today at all -- not a failed one, not an in-flight one, none. On 2026-08-17 "
              "the monologue saved as 0 bytes and nobody noticed until a human checked by hand; "
              "this line is what checking by hand was standing in for.", file=sys.stderr)

    if problems or missing_today:
        return UNHEALTHY
    return OK


def cmd_show(args) -> int:
    row = reads.capture(args.capture_id)
    if not row:
        print(f"no capture {args.capture_id}", file=sys.stderr)
        return EMPTY
    print(json.dumps(row, indent=2, default=str))
    return OK


def cmd_ls(args) -> int:
    rows = reads.recent(args.limit)
    if not rows:
        print("no captures")
        return EMPTY
    for r in rows:
        print(f"{r['capture_id']}  {r['verdict']:<21} {r['state']:<11} "
              f"{r['transcription_status'] or '-':<12} {r['objective_name'] or '-'}")
    return OK


def cmd_objectives(args) -> int:
    rows = reads.voice_objectives(args.limit)
    if not rows:
        print("no voice objectives")
        return EMPTY
    print(json.dumps(rows, indent=2, default=str))
    return OK


def cmd_engines(args) -> int:
    """Which engines this host can actually use, asked rather than assumed."""
    any_ok = False
    for eng in transcribe.ENGINES:
        ok, why = eng.available()
        # WHICH ONE WOULD ACTUALLY ANSWER, not merely which ones exist. `transcribe()` takes the
        # first available engine, so on a two-engine host "both available" leaves the operator to
        # work out which one his monologue went through. It is stated.
        mark = "<- takes the next capture" if (ok and any_ok is False) else ""
        any_ok = any_ok or ok
        cls = getattr(eng, "accuracy_class", transcribe.ACCURACY_UNMEASURED)
        print(f"{eng.name:<16} {'available' if ok else 'UNAVAILABLE':<12} "
              f"{cls:<16} {why} {mark}".rstrip())
    print("\naccuracy class is MEASURED on this host, not claimed: `names-reliable` means proper "
          "nouns\nsurvive and you may resolve a client or product from the body; `shape-only` "
          "means the shape of\nthe day survives and the NAMES IN IT DO NOT. See "
          "voice/docs/ENGINES.md for the numbers.")
    if not any_ok:
        print("\nNo engine is available. A capture on this host will land with "
              "transcription_status='unavailable' and an EMPTY body -- the audio is still "
              "retained and hashed, so it is recoverable, and nothing is guessed.",
              file=sys.stderr)
        return UNHEALTHY
    return OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="voice", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, **kw):
        s = sub.add_parser(name, **kw)
        s.set_defaults(fn=fn, retain_dir=None)
        return s

    s = add("record", cmd_record, help="record from the microphone and land it")
    s.add_argument("--seconds", type=int, required=True)
    s.add_argument("--name", help="the objective name; defaults to the capture id")
    s.add_argument("--capture-id")
    s.add_argument("--scratch-dir")
    s.add_argument("--retain-dir")
    s.add_argument("--produced-by")
    s.add_argument("--onto", metavar="TASK_ID",
                   help="land the transcript as a NOTE on this row instead of as an "
                        "objective in the intake inbox. Row 0432, his ask 3: speaking "
                        "AT the item in front of you rather than into a second inbox. "
                        "Posts through the registered `note` verb; needs ledger 51")
    s.add_argument("--no-land", action="store_true",
                   help="stop at `transcribed` and print the transcript and the note text as "
                        "JSON, for a caller that lands it through its own door. THE CONSOLE "
                        "USES THIS: pressing V runs this as a subprocess and then posts the "
                        "note itself with the one verb its room allows, so no capture verb "
                        "has to become reachable from a rendering surface")

    s = add("ingest-file", cmd_ingest_file, help="take an existing wav through the same pipeline")
    s.add_argument("wav")
    s.add_argument("--name")
    s.add_argument("--capture-id")
    s.add_argument("--retain-dir")
    s.add_argument("--produced-by")
    s.add_argument("--expect-seconds", type=int, default=1)
    s.add_argument("--consume", action="store_true",
                   help="move the source rather than copying it")
    s.add_argument("--onto", metavar="TASK_ID",
                   help="land the transcript as a NOTE on this row instead of as an "
                        "objective in the intake inbox. Row 0432, his ask 3: speaking "
                        "AT the item in front of you rather than into a second inbox. "
                        "Posts through the registered `note` verb; needs ledger 51")
    s.add_argument("--no-land", action="store_true",
                   help="stop at `transcribed` and print the transcript and the note text as "
                        "JSON, for a caller that lands it through its own door. THE CONSOLE "
                        "USES THIS: pressing V runs this as a subprocess and then posts the "
                        "note itself with the one verb its room allows, so no capture verb "
                        "has to become reachable from a rendering surface")

    s = add("health", cmd_health, help="the counted surface; exits 4 when it is not clean")
    s.add_argument("--hours", type=int, default=24)
    s.add_argument("--utc-offset", type=float,
                   default=float(os.environ.get("OPERATOR_UTC_OFFSET_HOURS", "3")))
    s.add_argument("--expect-daily", action="store_true", default=config.EXPECT_DAILY)
    s.add_argument("--no-expect-daily", dest="expect_daily", action="store_false")

    s = add("show", cmd_show, help="one capture, everything about it")
    s.add_argument("capture_id")

    s = add("ls", cmd_ls, help="recent captures with their verdicts")
    s.add_argument("--limit", type=int, default=20)

    s = add("objectives", cmd_objectives, help="what landed, with the status of its transcript")
    s.add_argument("--limit", type=int, default=20)

    add("engines", cmd_engines, help="which transcription engines this host can use")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except VoiceError as exc:
        print(f"voice: {exc}", file=sys.stderr)
        return ERR
    except store.StoreConfigError as exc:
        print(f"voice: {exc}", file=sys.stderr)
        return ERR
