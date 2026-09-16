"""The capture lifecycle, one transition per state change, registered in the one registry.

WHY THESE ARE NEW VERBS AND THE LANDING IS NOT.

V00 freezes: one transition function per state change, exposed as a verb, called by every
surface. What that forbids is a SECOND path into an EXISTING state change -- a recorder that
INSERTs its own objective row, an integration with a private door. It does not forbid new state
changes, and a capture attempt's lifecycle is a genuinely new one: nothing in this system
previously had a state called `recording`.

So the split is: the five verbs here own the ATTEMPT, and the LANDING -- the state change that
puts an objective in the admiral's inbox -- adds no verb at all. It calls `intake`, the function
that has always owned it, with more keyword arguments. There is deliberately no `voice land`
verb and no way to write `state='landed'` from this file; that write lives inside `intake`'s
transaction (`engine/swarm_engine/transitions.py::_voice_capture_landed`) so the capture row and
the objective move together or not at all.

WHY THE ROW EXISTS BEFORE THE AUDIO DOES.

`voice open` commits a row before the microphone is touched. That ordering is the entire fix for
the 2026-08-17 incident, in which the operator's monologue saved as 0 bytes and nobody noticed
until a human checked by hand. When the artefact is the only evidence the attempt happened, an
attempt that produces nothing produces no evidence, and there is nothing left to alarm on. Every
`open` is a promise that a terminal state is owed, and `due_at` is when the promise comes due.
"""

from __future__ import annotations

import store


class VoiceError(RuntimeError):
    """A refusal a caller should print and exit non-zero on, not one to retry."""


FAILURE_STAGES = ("save", "upload", "transcribe", "land")
NON_TERMINAL = ("recording", "recorded", "retained", "transcribed")


def _capture(ctx, capture_id: str) -> dict:
    row = ctx.one("SELECT * FROM brain.voice_capture WHERE capture_id = %s", (capture_id,))
    if not row:
        raise VoiceError(
            f"no capture {capture_id!r}. Every stage verb names a capture that `voice open` "
            f"already committed: a stage that could create its own row on the way past would "
            f"reintroduce the silence this table exists to remove, because a capture nobody "
            f"opened is a capture nobody is owed a terminal state for.")
    return row


def _expect(row: dict, want: tuple) -> None:
    if row["state"] not in want:
        raise VoiceError(
            f"capture {row['capture_id']} is in state {row['state']!r} and this stage requires "
            f"{' or '.join(repr(w) for w in want)}. Stages do not skip: a capture that reached "
            f"'retained' without passing the save check would be asserting the audio is durable "
            f"on the strength of a check nobody ran.")


@store.transition("voice open")
def voice_open(ctx, *, capture_id, due_at, host, source="mic", requested_seconds=None,
               scratch_pointer=None, actor_type=None, produced_by=None):
    """Open a capture. FIRST, before the microphone, and that order is the whole design.

    `due_at` is supplied by the caller because the caller is the only party that knows how long
    it is about to ask the microphone for. It is NOT NULL in the table, so there is no way to
    open a capture that is owed a terminal state at no particular time.
    """
    dupe = ctx.one("SELECT state FROM brain.voice_capture WHERE capture_id = %s", (capture_id,))
    if dupe:
        raise VoiceError(
            f"capture {capture_id!r} already exists in state {dupe['state']!r}. Capture ids carry "
            f"a UTC timestamp and six random hex characters; a collision means the caller reused "
            f"one, and reusing one would overwrite the evidence of an earlier attempt.")
    ctx.execute(
        """INSERT INTO brain.voice_capture
             (capture_id, state, due_at, host, source, requested_seconds, scratch_pointer,
              actor_type, produced_by)
           VALUES (%s, 'recording', %s, %s, %s, %s, %s, %s, %s)""",
        (capture_id, due_at, host, source, requested_seconds, scratch_pointer,
         actor_type, produced_by))
    return {"capture_id": capture_id, "state": "recording", "due_at": str(due_at)}


@store.transition("voice recorded")
def voice_recorded(ctx, *, capture_id, scratch_pointer, media_duration_s=None, due_at=None):
    """The recorder returned AND the file passed the save check.

    Not "the recorder exited 0". `recorder.save_check` stats the file, reads its RIFF header and
    compares the data chunk against what was asked for, and only a file that survives all three
    reaches this verb. A wrapper's exit code is not the child's -- the 2026-08-17 save reported
    success too.
    """
    row = _capture(ctx, capture_id)
    _expect(row, ("recording",))
    ctx.execute(
        """UPDATE brain.voice_capture
              SET state = 'recorded', scratch_pointer = %s, media_duration_s = %s,
                  due_at = COALESCE(%s, due_at), updated_at = now()
            WHERE capture_id = %s""",
        (scratch_pointer, media_duration_s, due_at, capture_id))
    return {"capture_id": capture_id, "state": "recorded"}


@store.transition("voice retained")
def voice_retained(ctx, *, capture_id, media_pointer, media_pointer_host, media_sha256,
                   media_bytes, media_kind, due_at=None):
    """The audio is in its durable place and was re-hashed THERE.

    The hash and the byte count are the destination's, measured after the move, not the source's
    carried forward. A transfer that reported success and wrote a short file is the same defect
    as the 0-byte save one filesystem later, and the only way to catch it is to measure the thing
    that arrived.
    """
    row = _capture(ctx, capture_id)
    _expect(row, ("recorded",))
    if not media_bytes or int(media_bytes) <= 0:
        raise VoiceError(
            f"retaining {capture_id} with media_bytes={media_bytes!r}. A retained file of zero "
            f"bytes is the 2026-08-17 incident exactly. Refusing, and the table refuses it too.")
    ctx.execute(
        """UPDATE brain.voice_capture
              SET state = 'retained', media_pointer = %s, media_pointer_host = %s,
                  media_sha256 = %s, media_bytes = %s, media_kind = %s,
                  due_at = COALESCE(%s, due_at), updated_at = now()
            WHERE capture_id = %s""",
        (media_pointer, media_pointer_host, media_sha256, media_bytes, media_kind, due_at,
         capture_id))
    return {"capture_id": capture_id, "state": "retained", "media_sha256": media_sha256}


@store.transition("voice transcribed")
def voice_transcribed(ctx, *, capture_id, transcription_engine, transcription_status,
                      transcript_chars=0, transcription_confidence=None, due_at=None,
                      transcription_accuracy_class=None):
    """An engine ran and stated an outcome, INCLUDING the two outcomes that mean no words.

    `null` (the engine ran and heard nothing) and `unavailable` (no engine ran at all) are
    outcomes, not errors, and they advance the capture rather than killing it: the audio is
    already retained, so the note can still land with an empty body and be transcribed again
    later by a better engine. They are counted as failures on the health line -- see
    `brain.voice_capture_health`, verdict `landed-without-words` -- and they are refused a
    character count, here and by the table, because a status that says there are no words while
    carrying some is a fabrication however it got there.
    """
    row = _capture(ctx, capture_id)
    _expect(row, ("retained",))
    if transcription_status in ("null", "unavailable") and int(transcript_chars or 0) > 0:
        raise VoiceError(
            f"transcription_status={transcription_status!r} says no text and "
            f"transcript_chars={transcript_chars}. NEVER FABRICATE A TRANSCRIPTION: "
            f"unintelligible audio is a null, not a guess, because a guess will be believed "
            f"later. D3 measured this rule on stated_goal.")
    if transcription_status in ("ok", "partial") and int(transcript_chars or 0) <= 0:
        raise VoiceError(
            f"transcription_status={transcription_status!r} asserts text and transcript_chars is "
            f"{transcript_chars}. That is a success report over an empty result, which is the "
            f"2026-08-17 shape. Use 'null' when the engine heard nothing.")
    # A CLASS WITHOUT WORDS IS A CLAIM ABOUT NOTHING (migration 27). Under `null`/`unavailable`
    # the body is empty, so "the text you may resolve names from" describes text that is not
    # there. Dropped rather than raised: the caller passing its engine's class alongside a null
    # is reasonable code, and the table's CHECK would otherwise turn a legitimate null into a
    # transaction failure that loses the capture.
    if transcription_status in ("null", "unavailable"):
        transcription_accuracy_class = None
    if transcription_accuracy_class not in (None, "names-reliable", "shape-only", "unmeasured"):
        raise VoiceError(
            f"transcription_accuracy_class={transcription_accuracy_class!r} is not one of "
            f"names-reliable, shape-only, unmeasured. It is a MEASUREMENT on this host, not a "
            f"free-text opinion about an engine -- see voice/docs/ENGINES.md for what each value "
            f"was measured at and on which sample.")
    ctx.execute(
        """UPDATE brain.voice_capture
              SET state = 'transcribed', transcription_engine = %s, transcription_status = %s,
                  transcript_chars = %s, transcription_confidence = %s,
                  transcription_accuracy_class = %s,
                  due_at = COALESCE(%s, due_at), updated_at = now()
            WHERE capture_id = %s""",
        (transcription_engine, transcription_status, int(transcript_chars or 0),
         transcription_confidence, transcription_accuracy_class, due_at, capture_id))
    return {"capture_id": capture_id, "state": "transcribed", "status": transcription_status,
            "accuracy_class": transcription_accuracy_class}


@store.transition("voice failed")
def voice_failed(ctx, *, capture_id, stage, detail):
    """Terminal and bad, and it MUST say which stage and why.

    The table refuses a `failed` row with no stage or an empty detail, so "handling" an error by
    parking the capture in a terminal state with nothing written down is not available. That
    swallow is the defect this lane exists to close: the ingest hook is right to exit 0 and eat
    its errors, because a hook that fails a session is worse than a hook that loses a log line.
    A capture is the opposite -- losing it silently is the entire loss.
    """
    if stage not in FAILURE_STAGES:
        raise VoiceError(f"stage must be one of {', '.join(FAILURE_STAGES)}, got {stage!r}")
    if not (detail or "").strip():
        raise VoiceError(
            "a failure with no detail is refused. Six months from now the row is all there is, "
            "and 'failed' with no sentence attached costs a re-run of whatever produced it.")
    row = _capture(ctx, capture_id)
    if row["state"] in ("landed", "failed"):
        raise VoiceError(
            f"capture {capture_id} is already terminal ({row['state']!r}). A record you can "
            f"silently rewrite is not a record: corrections supersede rather than edit, so open "
            f"a new capture rather than restating this one.")
    ctx.execute(
        """UPDATE brain.voice_capture
              SET state = 'failed', failure_stage = %s, failure_detail = %s,
                  failed_at = now(), updated_at = now()
            WHERE capture_id = %s""",
        (stage, detail.strip(), capture_id))
    return {"capture_id": capture_id, "state": "failed", "stage": stage}
