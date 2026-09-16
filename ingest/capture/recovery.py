"""Replay, export, restore, orphan reconciliation and retention erasure.

THE ROLLBACK RULE FROM THE PACKET: "compatible reader and replay frontier; never discard already
acknowledged captures." Everything here is written to that rule.

* `replay` is defined by a frontier (a sequence number), not by a timestamp. Clocks go backwards;
  sequence numbers do not, and a consumer that crashed mid-batch resumes exactly where it was.
* `export_journal` writes rows in sequence order with their contract version, so an older reader
  can skip fields it does not know instead of refusing the file.
* `restore_journal` REFUSES to write into a store that already holds rows. Overwriting newer
  state with an older backup is named in the ownership policy as a thing not to do, so it is not
  reachable by accident here.
* `forget_content` erases bytes and keeps rows. Retention deletion removes content; it must not
  remove the fact that an event occurred, or the coverage audit silently develops a hole.

ORPHAN BLOBS ARE THE EXPECTED CRASH RESIDUE, not a defect. `find_orphan_blobs` names them so a
sweep can reclaim disk. It never deletes on its own: a blob that looks orphaned during a
concurrent capture is a blob mid-flight, and reclaiming it would delete evidence in flight.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from .contracts import CONTRACT_DRAFT_VERSION
from .raw_custody import CustodyError, RawStore
from .state_port import JournalRow

EXPORT_FORMAT = "infinite-capture-journal-export/1"


@dataclass(frozen=True)
class ReplayResult:
    delivered: int
    frontier: int


def replay(
    store: Any,
    handler: Callable[[JournalRow], None],
    *,
    frontier: int = 0,
    limit: int | None = None,
) -> ReplayResult:
    """Deliver every row after `frontier` in order. The new frontier is returned, never assumed.

    The handler is called BEFORE the frontier advances for that row, so a handler that raises
    leaves the frontier at the last successfully handled row and the batch is resumable.
    """
    delivered = 0
    current = frontier
    for row in store.rows_since(frontier, limit):
        handler(row)
        current = row.journal_seq
        delivered += 1
    return ReplayResult(delivered=delivered, frontier=current)


def export_journal(store: Any, path: str | Path) -> int:
    """Write the journal as newline-delimited JSON. Returns the row count written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "format": EXPORT_FORMAT,
                    "contract_version": CONTRACT_DRAFT_VERSION,
                    "max_seq": store.max_seq(),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        for row in store.rows_since(0):
            fh.write(
                json.dumps(
                    {
                        "journal_seq": row.journal_seq,
                        "capture_id": row.capture_id,
                        "source_key": row.source_key,
                        "kind": row.kind,
                        "revision": row.revision,
                        "content_digest": row.content_digest,
                        "raw_digest": row.raw_digest,
                        "supersedes": row.supersedes,
                        "committed_at": row.committed_at,
                        "record": row.record,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
            count += 1
    return count


def restore_journal(store: Any, path: str | Path) -> int:
    """Load an export into an EMPTY store. Refuses a non-empty one."""
    if store.count_events() != 0:
        raise RuntimeError(
            "refusing to restore into a store that already holds rows: an old backup must never "
            "overwrite newer state"
        )
    path = Path(path)
    restored = 0
    with open(path, "r", encoding="utf-8") as fh:
        header = json.loads(fh.readline())
        if header.get("format") != EXPORT_FORMAT:
            raise RuntimeError(f"unknown journal export format {header.get('format')!r}")
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            store.append(
                {
                    "capture_id": row["capture_id"],
                    "source_key": row["source_key"],
                    "kind": row["kind"],
                    "revision": row["revision"],
                    "content_digest": row["content_digest"],
                    "raw_digest": row.get("raw_digest"),
                    "supersedes": row.get("supersedes"),
                    "committed_at": row["committed_at"],
                    "record": row["record"],
                }
            )
            restored += 1
    return restored


def find_orphan_blobs(store: Any, raw: RawStore) -> set[str]:
    """Blobs in custody that no committed row references: the residue of a crash mid-capture."""
    return set(raw.iter_digests()) - store.referenced_digests()


def find_dangling_references(store: Any, raw: RawStore) -> set[str]:
    """Rows pointing at bytes that are GONE. Presence only, and that is a real limitation.

    Anything here is either a retention erasure (expected, and the row says so) or real evidence
    loss. It is cheap: one stat per digest, no decryption.

    IT DOES NOT PROVE THE BYTES ARE RIGHT, and the docstring used to imply otherwise by quoting the
    acceptance line "all acknowledged raw digests resolve" against a check that only asks whether a
    file exists. A blob that was swapped, truncated or corrupted in place passes this and fails
    `find_unreadable_references`. Terminal 08 named the gap while offering to write a fixture for
    it; the claim was stronger than the check behind it, which is the same defect class as an
    unasserted value sitting in a manifest.
    """
    return {d for d in store.referenced_digests() if not raw.has(d)}


def find_unreadable_references(store: Any, raw: RawStore) -> dict[str, str]:
    """Rows whose bytes cannot be RETURNED. The honest form of "all acknowledged digests resolve".

    Reads every referenced blob back through custody, which re-verifies the digest and, under an
    authenticated cipher, the tag. So this catches three failures the cheap check cannot tell apart
    from health: bytes gone, bytes swapped for different bytes that hash to something else, and
    bytes whose ciphertext no longer authenticates.

    It is DELIBERATELY not folded into `coverage_report`: it decrypts everything, so it belongs in
    a periodic audit or an acceptance run, not in a per-request path. Returning a mapping of digest
    to the failure's own sentence rather than a bare set, because "gone" and "does not match its own
    name" send an operator to different places.
    """
    out: dict[str, str] = {}
    for digest in sorted(store.referenced_digests()):
        try:
            raw.get(digest)
        except CustodyError as exc:
            out[digest] = f"{exc.__class__.__name__}: {exc}"
    return out


def forget_content(store: Any, raw: RawStore, source_key: str) -> int:
    """Retention erasure for one identity. Bytes go; rows stay."""
    erased = 0
    for row in store.rows_since(0):
        if row.source_key != source_key:
            continue
        for digest in filter(None, [row.raw_digest, *(
            a["raw_digest"] for a in (row.record.get("attachments") or [])
        )]):
            if raw.forget(digest):
                erased += 1
    return erased


def coverage_report(store: Any) -> dict[str, Any]:
    """The numbers the acceptance evidence is stated in. Derived from rows, never accumulated."""
    kinds: dict[str, int] = {}
    for row in store.rows_since(0):
        kinds[row.kind] = kinds.get(row.kind, 0) + 1
    return {
        "capture_events": store.count_events(),
        "source_identities": len(store.source_keys()),
        "live_identities": len(store.live_source_keys()),
        "by_kind": kinds,
        "dead_letters": sum(1 for _ in store.dead_letters()),
        "frontier": store.max_seq(),
    }
