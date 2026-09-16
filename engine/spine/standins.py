"""Reference implementations for the three stages whose owning lanes are not in this checkout.

EVERY CLASS HERE IS A STAND-IN AND SAYS SO. `preflight.py` measures the absence these exist to work
around; nothing in this file claims to be R01, R02 or B01, and no evidence row a stand-in produces
is marked `real`.

WHAT A STAND-IN IS FOR, AND WHAT IT IS NOT. It is for proving the COMPOSITION: that the stages fit
together, that an approval can name action ids the packet actually carries, that a reservation can
be refused because an approval is already spent, that a receipt carries an outcome and a
completeness. It is NOT evidence about durability, concurrency, or any behaviour of the real
Postgres-backed ports. Two leases racing is a question this file cannot answer and does not
pretend to.

They are backed by SQLite rather than dicts on purpose: the rows survive the process, so the
evidence a run prints can be read back out of a file afterwards by someone who did not run it.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from ports import SpineRefused

SCHEMA = """
CREATE TABLE IF NOT EXISTS standin_approval (
    version_hash TEXT PRIMARY KEY,
    packet_id    TEXT NOT NULL,
    decision     TEXT NOT NULL,
    decided_by   TEXT NOT NULL,
    decided_at   TEXT NOT NULL,
    action_ids   TEXT NOT NULL,
    -- C01 requires both of these on an APPROVED approval-decision. This lane's in-memory
    -- `Approval` carries neither, so they live on the durable row rather than being invented onto
    -- the type to make a projection validate.
    expires_at          TEXT,
    authority_check_ref TEXT
);

-- One row per action a lease is held on. The UNIQUE constraint IS the lease: a second holder
-- cannot insert, which is the only lease property a single-process stand-in can honestly show.
CREATE TABLE IF NOT EXISTS standin_lease (
    action_id TEXT PRIMARY KEY,
    holder    TEXT NOT NULL,
    taken_at  TEXT NOT NULL
);

-- THE SPEND LEDGER. `version_hash` is UNIQUE, so an approval can back exactly one reservation.
-- The second attempt fails on the constraint rather than on a check somebody remembered to write.
CREATE TABLE IF NOT EXISTS standin_reservation (
    reservation_id TEXT PRIMARY KEY,
    version_hash   TEXT NOT NULL UNIQUE,
    action_id      TEXT NOT NULL,
    reserved_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS standin_receipt (
    receipt_id     TEXT PRIMARY KEY,
    reservation_id TEXT NOT NULL REFERENCES standin_reservation(reservation_id),
    outcome        TEXT NOT NULL,
    completeness   TEXT NOT NULL,
    settled_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS standin_promotion (
    promotion_id TEXT PRIMARY KEY,
    result_json  TEXT NOT NULL,
    received_at  TEXT NOT NULL
);
"""


class StandinRuntime:
    """R01, R02 and B01's shapes over one SQLite file. A stand-in for three lanes, not one."""

    is_standin = True

    def __init__(self, path: str | Path):
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- R01 -----------------------------------------------------------------------------------

    def record_approval(self, entry: Mapping[str, Any]) -> str:
        self._conn.execute(
            "INSERT OR REPLACE INTO standin_approval "
            "(version_hash, packet_id, decision, decided_by, decided_at, action_ids, "
            "expires_at, authority_check_ref) VALUES (?,?,?,?,?,?,?,?)",
            (
                entry["version_hash"], entry["packet_id"], entry["decision"],
                entry["decided_by"], entry["decided_at"],
                json.dumps(list(entry.get("action_ids", ()))),
                entry.get("expires_at"), entry.get("authority_check_ref"),
            ),
        )
        self._conn.commit()
        return entry["version_hash"]

    def approval_for(self, version_hash: str) -> Mapping[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM standin_approval WHERE version_hash=?", (version_hash,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["action_ids"] = tuple(json.loads(d["action_ids"]))
        return d

    # -- R02 -----------------------------------------------------------------------------------

    def acquire_lease(self, action_id: str, *, holder: str) -> str:
        try:
            self._conn.execute(
                "INSERT INTO standin_lease (action_id, holder, taken_at) VALUES (?,?,?)",
                (action_id, holder, "2026-09-07T10:00:00Z"),
            )
        except sqlite3.IntegrityError as exc:
            held = self._conn.execute(
                "SELECT holder FROM standin_lease WHERE action_id=?", (action_id,)
            ).fetchone()
            raise SpineRefused(
                "lease on " + repr(action_id) + " is already held by "
                + repr(held["holder"] if held else "someone")
            ) from exc
        self._conn.commit()
        return action_id

    def reserve(self, entry: Mapping[str, Any]) -> str:
        approval = self.approval_for(entry["version_hash"])
        if approval is None:
            raise SpineRefused(
                "no approval recorded for version " + repr(entry["version_hash"])
                + "; an effect cannot be reserved against a decision nobody made"
            )
        if entry["action_id"] not in approval["action_ids"]:
            raise SpineRefused(
                "the approval does not name action " + repr(entry["action_id"])
                + "; AD-I2 binds an approval to specific actions in both directions"
            )
        try:
            self._conn.execute(
                "INSERT INTO standin_reservation "
                "(reservation_id, version_hash, action_id, reserved_at) VALUES (?,?,?,?)",
                (entry["reservation_id"], entry["version_hash"], entry["action_id"],
                 entry.get("reserved_at", "2026-09-07T10:00:00Z")),
            )
        except sqlite3.IntegrityError as exc:
            prior = self._conn.execute(
                "SELECT reservation_id FROM standin_reservation WHERE version_hash=?",
                (entry["version_hash"],),
            ).fetchone()
            raise SpineRefused(
                "approval " + repr(entry["version_hash"]) + " is already spent on reservation "
                + repr(prior["reservation_id"] if prior else "unknown")
                + "; one decision authorises one effect"
            ) from exc
        self._conn.commit()
        return entry["reservation_id"]

    def settle(self, reservation_id: str, *, outcome: str, completeness: str) -> Mapping[str, Any]:
        if self._conn.execute(
            "SELECT 1 FROM standin_reservation WHERE reservation_id=?", (reservation_id,)
        ).fetchone() is None:
            raise SpineRefused("no reservation " + repr(reservation_id) + " to settle")
        receipt = {
            "receipt_id": "rcpt_" + reservation_id,
            "reservation_id": reservation_id,
            "outcome": outcome,
            "completeness": completeness,
            "settled_at": "2026-09-07T10:00:05Z",
        }
        self._conn.execute(
            "INSERT OR REPLACE INTO standin_receipt "
            "(receipt_id, reservation_id, outcome, completeness, settled_at) VALUES (?,?,?,?,?)",
            (receipt["receipt_id"], reservation_id, outcome, completeness, receipt["settled_at"]),
        )
        self._conn.commit()
        return receipt

    # -- B01 -----------------------------------------------------------------------------------

    def receive(self, result: Mapping[str, Any]) -> str:
        promotion_id = "promo_" + str(result.get("receipt_id", "unknown"))
        self._conn.execute(
            "INSERT OR REPLACE INTO standin_promotion (promotion_id, result_json, received_at) "
            "VALUES (?,?,?)",
            (promotion_id, json.dumps(dict(result), sort_keys=True), "2026-09-07T10:00:06Z"),
        )
        self._conn.commit()
        return promotion_id

    # -- reading the evidence back ---------------------------------------------------------------

    def count(self, table: str) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0])
