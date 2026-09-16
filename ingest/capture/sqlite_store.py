"""Reference `JournalStore` on SQLite. Disposable by construction, never a production store.

WHY SQLITE AND NOT POSTGRES. R01 owns the schema ledger and has not allocated numbers; the repo's
own carve-out forbids applying migrations to the live store. A file-backed SQLite database in a
temp directory lets the crash and idempotency properties be *tested* now, and the port keeps the
Postgres implementation one class away.

WHY `synchronous=FULL` AND NOT THE DEFAULT. The tests in this packet assert durability across a
simulated crash. With `synchronous=NORMAL` in WAL mode SQLite may acknowledge a commit that a
power cut would lose, which would make the durability assertion a lie that passes.

THE FAULT HOOK IS PRODUCTION CODE, NOT TEST CODE. `fault` is called at named points inside the
commit path so a test can raise exactly where a crash hurts. It defaults to a no-op. Simulating a
crash from outside the transaction cannot reach the window between "raw is durable" and "row is
committed", which is the window this packet exists to make safe.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

from .state_port import HeadState, JournalRow, OutboxIntent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS capture_journal (
    journal_seq   INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id    TEXT NOT NULL UNIQUE,
    source_key    TEXT NOT NULL,
    kind          TEXT NOT NULL,
    revision      INTEGER NOT NULL,
    content_digest TEXT NOT NULL,
    raw_digest    TEXT,
    supersedes    TEXT,
    committed_at  TEXT NOT NULL,
    record_json   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS capture_journal_source ON capture_journal(source_key, journal_seq);

CREATE TABLE IF NOT EXISTS capture_outbox (
    intent_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    journal_seq INTEGER NOT NULL REFERENCES capture_journal(journal_seq),
    capture_id  TEXT NOT NULL,
    action      TEXT NOT NULL,
    target      TEXT NOT NULL,
    params_json TEXT,
    state       TEXT NOT NULL DEFAULT 'claimable',
    result      TEXT
);

CREATE TABLE IF NOT EXISTS capture_dead_letters (
    dead_letter_id TEXT PRIMARY KEY,
    source_key     TEXT,
    reason         TEXT NOT NULL,
    detail         TEXT NOT NULL,
    received_at    TEXT NOT NULL,
    raw_digest     TEXT,
    attempts       INTEGER NOT NULL DEFAULT 1
);

-- I02-RECONCILE-01. A reconciliation is an APPEND, not an UPDATE: the dead letter itself is never
-- edited, and a separate row records who resolved it, when and how. Readiness is regainable
-- without rewriting what yesterday said.
-- MANY FINDINGS PER LOSS, newest decides, every earlier one still readable. Terminal 04 drove the
-- defect the first shape had: with `dead_letter_id` as the primary key, a human who recorded
-- `still-unknown` could never record `recaptured` when the item actually arrived, so an honest
-- answer blocked readiness for ever. Migration 68 carries the same correction on the Postgres side.
CREATE TABLE IF NOT EXISTS capture_dead_letter_reconciliations (
    reconciliation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    dead_letter_id TEXT NOT NULL REFERENCES capture_dead_letters(dead_letter_id),
    resolution     TEXT NOT NULL,
    resolved_by    TEXT NOT NULL,
    resolved_at    TEXT NOT NULL,
    note           TEXT NOT NULL DEFAULT ''
);
"""


class SqliteJournalStore:
    """Append-only capture journal. No UPDATE and no DELETE statement exists in this file."""

    def __init__(self, path: str | Path, fault: Callable[[str], None] | None = None):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._fault = fault or (lambda _point: None)
        self._conn = sqlite3.connect(self.path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # -- writes -------------------------------------------------------------------------------

    def append(
        self, row: Mapping[str, Any], intents: Iterable[OutboxIntent] = ()
    ) -> tuple[int, bool]:
        existing = self.get(row["capture_id"])
        if existing is not None:
            # The idempotent path. Do NOT rewrite intents: the first commit already staged them,
            # and staging them twice is how a source item gets two "mark read" calls.
            return existing.journal_seq, False

        cur = self._conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            self._fault("before_insert")
            cur.execute(
                "INSERT INTO capture_journal "
                "(capture_id, source_key, kind, revision, content_digest, raw_digest, "
                " supersedes, committed_at, record_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    row["capture_id"],
                    row["source_key"],
                    row["kind"],
                    int(row["revision"]),
                    row["content_digest"],
                    row.get("raw_digest"),
                    row.get("supersedes"),
                    row["committed_at"],
                    json.dumps(row["record"], ensure_ascii=False, sort_keys=True),
                ),
            )
            seq = int(cur.lastrowid)
            for intent in intents:
                cur.execute(
                    "INSERT INTO capture_outbox "
                    "(journal_seq, capture_id, action, target, params_json) VALUES (?,?,?,?,?)",
                    (
                        seq,
                        row["capture_id"],
                        intent.action,
                        intent.target,
                        json.dumps(intent.params or {}, ensure_ascii=False, sort_keys=True),
                    ),
                )
            self._fault("before_commit")
            cur.execute("COMMIT")
        except BaseException:
            # A crash here leaves the raw blob orphaned and NO journal row. That is the residue
            # this whole ordering was chosen to produce.
            try:
                cur.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        self._fault("after_commit")
        return seq, True

    def dead_letter(self, entry: Mapping[str, Any]) -> str:
        self._conn.execute(
            "INSERT OR IGNORE INTO capture_dead_letters "
            "(dead_letter_id, source_key, reason, detail, received_at, raw_digest, attempts) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                entry["dead_letter_id"],
                entry.get("source_key"),
                entry["reason"],
                entry["detail"],
                entry["received_at"],
                entry.get("raw_digest"),
                int(entry.get("attempts", 1)),
            ),
        )
        return entry["dead_letter_id"]

    def reconcile_dead_letter(self, entry: Mapping[str, Any]) -> bool:
        """Append one reconciliation. False if this dead letter was already reconciled.

        Refuses an id that names no dead letter. A reconciliation of nothing would be a row saying
        a loss was handled when no loss was ever recorded, which is the one thing a reconciliation
        must never be able to say.
        """
        exists = self._conn.execute(
            "SELECT 1 FROM capture_dead_letters WHERE dead_letter_id=?",
            (entry["dead_letter_id"],),
        ).fetchone()
        if exists is None:
            raise KeyError(
                "no dead letter " + repr(entry["dead_letter_id"]) + " to reconcile; refusing to "
                "record a resolution for a loss that was never recorded"
            )
        cur = self._conn.execute(
            "INSERT INTO capture_dead_letter_reconciliations "
            "(dead_letter_id, resolution, resolved_by, resolved_at, note) VALUES (?,?,?,?,?)",
            (
                entry["dead_letter_id"],
                entry["resolution"],
                entry["resolved_by"],
                entry["resolved_at"],
                entry.get("note", ""),
            ),
        )
        return cur.rowcount == 1

    def dead_letter_reconciliations(self, dead_letter_id: str):
        """Every finding about one loss, oldest first. The newest decides; the rest are the trail.

        A reader that could only see the current answer could not tell a loss that was always
        closed from one a human reopened, and the second is the interesting case.
        """
        return [
            dict(r) for r in self._conn.execute(
                "SELECT * FROM capture_dead_letter_reconciliations WHERE dead_letter_id=? "
                "ORDER BY reconciliation_seq",
                (dead_letter_id,),
            )
        ]

    def unreconciled_dead_letters(self, source_key: str | None = None) -> int:
        # THE LATEST FINDING DECIDES, and `still-unknown` is not a closing one. A loss with no
        # finding at all and a loss whose newest finding is `still-unknown` are both unreconciled,
        # which is what stops readiness being regained by writing down that you looked.
        sql = (
            "SELECT COUNT(*) FROM capture_dead_letters d "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM capture_dead_letter_reconciliations r "
            "   WHERE r.dead_letter_id = d.dead_letter_id "
            "     AND r.resolution <> 'still-unknown' "
            "     AND r.reconciliation_seq = ("
            "         SELECT MAX(r2.reconciliation_seq) FROM capture_dead_letter_reconciliations r2 "
            "          WHERE r2.dead_letter_id = d.dead_letter_id))"
        )
        params: tuple = ()
        if source_key is not None:
            sql += " AND d.source_key=?"
            params = (source_key,)
        return int(self._conn.execute(sql, params).fetchone()[0])

    def mark_intent_done(self, intent_id: int, result: str) -> None:
        self._conn.execute(
            "UPDATE capture_outbox SET state='done', result=? WHERE intent_id=? AND state='claimable'",
            (result, intent_id),
        )

    # -- reads --------------------------------------------------------------------------------

    @staticmethod
    def _to_row(r: sqlite3.Row) -> JournalRow:
        return JournalRow(
            journal_seq=r["journal_seq"],
            capture_id=r["capture_id"],
            source_key=r["source_key"],
            kind=r["kind"],
            revision=r["revision"],
            content_digest=r["content_digest"],
            raw_digest=r["raw_digest"],
            supersedes=r["supersedes"],
            committed_at=r["committed_at"],
            record=json.loads(r["record_json"]),
        )

    def get(self, capture_id: str) -> JournalRow | None:
        r = self._conn.execute(
            "SELECT * FROM capture_journal WHERE capture_id=?", (capture_id,)
        ).fetchone()
        return self._to_row(r) if r else None

    def head(self, source_key: str) -> HeadState | None:
        r = self._conn.execute(
            "SELECT * FROM capture_journal WHERE source_key=? ORDER BY journal_seq DESC LIMIT 1",
            (source_key,),
        ).fetchone()
        if r is None:
            return None
        return HeadState(
            source_key=source_key,
            revision=r["revision"],
            content_digest=r["content_digest"],
            capture_id=r["capture_id"],
            live=r["kind"] != "tombstone",
            journal_seq=r["journal_seq"],
        )

    def rows_since(self, frontier: int = 0, limit: int | None = None) -> Iterator[JournalRow]:
        sql = "SELECT * FROM capture_journal WHERE journal_seq > ? ORDER BY journal_seq"
        args: tuple[Any, ...] = (frontier,)
        if limit is not None:
            sql += " LIMIT ?"
            args = (frontier, limit)
        for r in self._conn.execute(sql, args):
            yield self._to_row(r)

    def live_source_keys(self) -> set[str]:
        """Identities whose most recent row is not a tombstone."""
        rows = self._conn.execute(
            "SELECT source_key, kind FROM capture_journal j WHERE journal_seq = "
            "(SELECT MAX(journal_seq) FROM capture_journal WHERE source_key = j.source_key)"
        )
        return {r["source_key"] for r in rows if r["kind"] != "tombstone"}

    def count_events(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) c FROM capture_journal").fetchone()["c"])

    def max_seq(self) -> int:
        r = self._conn.execute("SELECT MAX(journal_seq) m FROM capture_journal").fetchone()
        return int(r["m"] or 0)

    def source_keys(self) -> set[str]:
        return {
            r["source_key"]
            for r in self._conn.execute("SELECT DISTINCT source_key FROM capture_journal")
        }

    def referenced_digests(self) -> set[str]:
        out: set[str] = set()
        for r in self._conn.execute(
            "SELECT raw_digest, record_json FROM capture_journal WHERE raw_digest IS NOT NULL"
        ):
            out.add(r["raw_digest"])
            for att in json.loads(r["record_json"]).get("attachments") or []:
                out.add(att["raw_digest"])
        return out

    def dead_letters(self) -> Iterator[Mapping[str, Any]]:
        for r in self._conn.execute("SELECT * FROM capture_dead_letters ORDER BY dead_letter_id"):
            yield dict(r)

    def claimable_intents(self) -> Iterator[Mapping[str, Any]]:
        for r in self._conn.execute(
            "SELECT * FROM capture_outbox WHERE state='claimable' ORDER BY intent_id"
        ):
            yield dict(r)

    def all_intents(self) -> Iterator[Mapping[str, Any]]:
        for r in self._conn.execute("SELECT * FROM capture_outbox ORDER BY intent_id"):
            yield dict(r)
