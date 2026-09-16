"""The counted half. Every one of these goes through `store.read()`, which Postgres has put in
READ ONLY mode, so exposing them costs nothing.

The verdicts are computed in `brain.voice_capture_health` and not here, on purpose. Two readers
that each decide for themselves what "stuck" means will eventually disagree, and the day they
disagree is the day the health line is wrong in the reassuring direction.
"""

from __future__ import annotations

import store

#: Verdicts that are a problem. `in-flight` is not one and neither is `ok`.
BAD_VERDICTS = ("failed", "stuck", "landed-without-words")


def capture(capture_id: str) -> dict | None:
    with store.read() as s:
        return s.one("SELECT * FROM brain.voice_capture_health WHERE capture_id = %s",
                     (capture_id,))


def recent(limit: int = 20) -> list:
    with store.read() as s:
        return s.query("SELECT * FROM brain.voice_capture_health "
                       "ORDER BY opened_at DESC LIMIT %s", (limit,))


def counts(since_hours: int = 24) -> list:
    """One row per verdict, with the count. The whole point of the table."""
    with store.read() as s:
        return s.query(
            """SELECT verdict, count(*) AS n
                 FROM brain.voice_capture_health
                WHERE opened_at > now() - (%s * interval '1 hour')
                GROUP BY verdict ORDER BY verdict""", (since_hours,))


def bad(since_hours: int = 24) -> list:
    with store.read() as s:
        return s.query(
            """SELECT * FROM brain.voice_capture_health
                WHERE verdict = ANY(%s) AND opened_at > now() - (%s * interval '1 hour')
                ORDER BY opened_at DESC""", (list(BAD_VERDICTS), since_hours))


def captures_today(utc_offset_hours: float = 3.0) -> int:
    """How many captures were OPENED on the operator's today.

    His day, not UTC's: the WSL host runs UTC and his wall clock reads UTC+3, and a daily
    expectation evaluated in the wrong zone is wrong for three hours out of every twenty-four --
    which is the window a morning monologue actually lands in.
    """
    with store.read() as s:
        return s.scalar(
            """SELECT count(*) FROM brain.voice_capture
                WHERE (opened_at + (%s * interval '1 hour'))::date
                    = (now() + (%s * interval '1 hour'))::date""",
            (utc_offset_hours, utc_offset_hours))


def voice_objectives(limit: int = 20) -> list:
    """What actually landed. Reads `transcription_status` alongside `body` every time.

    Never select a voice objective's body without its status. `null` and `unavailable` mean the
    body is empty because the engine produced nothing, and a reader that sees only an empty body
    cannot tell that from a recording of silence or from a bug in this package.
    """
    with store.read() as s:
        return s.query(
            """SELECT id, name, state, intake_format, transcription_status, transcription_engine,
                      media_pointer, media_pointer_host, media_sha256, media_bytes,
                      media_duration_s, bytes, length(body) AS body_chars, taken_in_at
                 FROM brain.objective
                WHERE intake_format = 'voice'
                ORDER BY taken_in_at DESC LIMIT %s""", (limit,))
