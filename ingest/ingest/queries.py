"""Read paths. Every count here comes back with its denominator.

The lane rule: report coverage, never impressions. A query in this module that returns "142
sessions have a model" without also returning how many sessions were examined is a bug, not
a formatting preference, because the number is unreadable without the denominator and gets
quoted anyway.

Task 0323 found the hole that rule still had. Every SELECT below draws its denominator from
`session`, `transcript`, `transcript_orphan` or `event_outbox`, so the denominator of every
ratio was "rows we managed to write". A transcript whose indexing transaction rolled back
leaves no row in any of the four, so it was missing from the numerator AND the denominator of
everything here, and `transcript_orphans {open: N}` read like a total while counting only the
gaps that got far enough to be recorded. `filesystem_report` is the denominator that is not in
the database, and it is why `coverage_report` now does one I/O call.
"""

from __future__ import annotations

import os
from typing import Any

from . import config, profiles, transcript as tx


def _pair(cur, total_sql: str, hit_sql: str) -> dict[str, Any]:
    cur.execute(total_sql)
    total = cur.fetchone()["n"]
    cur.execute(hit_sql)
    hit = cur.fetchone()["n"]
    return {"n": hit, "of": total, "pct": round(100.0 * hit / total, 1) if total else None}


def _orphan_counts(cur) -> dict[str, Any]:
    """Transcripts that could not be keyed to a session. Both profiles, same table.

    This is the countable half of the fix in `verbs.transcript_index`. `open` is the number
    that matters: pointers whose claimed session key has never been honoured. If it is not
    zero, every count of sessions on this profile is a floor.
    """
    cur.execute("""SELECT count(*) AS total,
                          count(*) FILTER (WHERE resolved_at IS NULL) AS open,
                          count(DISTINCT session_key) FILTER (WHERE resolved_at IS NULL) AS keys,
                          max(last_seen_at) AS last_seen
                   FROM transcript_orphan""")
    r = cur.fetchone()
    cur.execute("""SELECT reason, count(*) AS n FROM transcript_orphan
                   WHERE resolved_at IS NULL GROUP BY 1 ORDER BY 2 DESC""")
    return {"open": r["open"], "resolved": r["total"] - r["open"], "ever": r["total"],
            "distinct_session_keys_open": r["keys"], "last_seen_at": r["last_seen"],
            "open_by_reason": {row["reason"]: row["n"] for row in cur.fetchall()}}


def _absence_counts(cur) -> dict[str, Any]:
    """Pointers whose FILE is gone, split by whether that was expected. Both profiles.

    `filesystem.indexed_not_on_disk` (task 0323) already counts these off the disk. This is the
    other half, and the reason the count alone was not enough: 61 dead pointers read the same
    whether a retention sweep aged them out on schedule or somebody deleted a transcript of a
    session that ended this morning. `open_by_classification` is where that difference lives,
    and `unexplained` is the only key in it anybody has to act on.

    The two numbers answer different questions and are both reported on purpose. The
    filesystem one is what the disk says RIGHT NOW; this one is what verify has actually
    walked up to and recorded. A gap between them means verify has not caught up, not that a
    file came back -- `bytes_lost` is only ever the bytes of pointers this lane has confirmed.
    """
    cur.execute("""SELECT count(*) AS total,
                          count(*) FILTER (WHERE returned_at IS NULL) AS open,
                          coalesce(sum(bytes) FILTER (WHERE returned_at IS NULL), 0) AS b,
                          min(first_observed_absent_at) AS first_seen,
                          max(last_observed_absent_at) AS last_seen
                   FROM transcript_absence""")
    r = cur.fetchone()
    cur.execute("""SELECT classification, count(*) AS n FROM transcript_absence
                   WHERE returned_at IS NULL GROUP BY 1 ORDER BY 2 DESC""")
    by_class = {row["classification"]: row["n"] for row in cur.fetchall()}
    return {"open": r["open"], "returned": r["total"] - r["open"], "ever": r["total"],
            # Zeroes included on purpose. A key that only exists when it is non-zero is a key
            # a reader learns not to look for, and `unexplained` is the one that must be
            # looked for every time.
            "open_by_classification": {k: by_class.get(k, 0) for k in
                                       ("aged-out", "unexplained", "unknown-age")},
            "bytes_open": int(r["b"]), "mib_open": round(int(r["b"]) / 1048576, 1),
            "first_observed_absent_at": r["first_seen"], "last_observed_absent_at": r["last_seen"]}


#: How many paths each direction of `filesystem_report` names before it stops listing. A count
#: with no example is unactionable and a full list of 1,299 is unreadable, so both are reported:
#: the sample is capped and `sample_of` says what the cap is hiding.
SAMPLE_LIMIT = 10


def filesystem_report(cur, projects_root: str | None = None) -> dict[str, Any]:
    """The one count in this module whose denominator is the disk rather than the store.

    Why it has to exist (task 0323, handed on from 0318): `verbs.transcript_index` writes the
    session row, the transcript row and the orphan row in ONE transaction. When that
    transaction rolls back -- which is exactly what the installed hook did for six sessions on
    2026-08-16, raising ForeignKeyViolation on `transcript_session_key_fkey` -- the file leaves
    behind no `transcript` row, no `session` row and no `transcript_orphan` row. So it was
    invisible to every other query here, in the numerator and the denominator both. Measured on
    2026-08-17T14:36Z, before 0318's fix: `transcript_orphans {open}` said 2 and the real
    number of sessions on disk with no `session` row was 76.

    Do not read `on_disk_not_indexed` as the same fact as `transcript_orphans.open`. An orphan
    row requires the indexing transaction to have SURVIVED and only its session key to have
    failed. This number requires nothing to have survived, which is the whole point.

    IT HAS A FLOOR, AND THE FLOOR IS NOT A GAP. The hook indexes a transcript at `SessionEnd`,
    so every session that is open right now is a file on disk with no `transcript` row, and
    `on_disk_not_indexed` counts it. Measured 2026-08-17T14:47Z, when it stood at 18: seven
    open sessions (registered, `ended_at` NULL, file touched within the hour), two pre-flip
    files indexed into `d3_scratch` and never into `brain` (a real gap), and nine subagent
    transcripts from the hook-install window (task 0325). This report does not try to split
    them: telling a live session from a stalled one needs the session row AND a session key
    derived from the path, and a path-derived key for a main session is a guess -- `tx.scan`
    prefers the file's own `sessionId` field and only falls back to the filename. A count that
    guesses would be the confidently-wrong number this lane keeps refusing to print. Read the
    sample, join it against `session` yourself, and expect roughly one file per open terminal.

    THE FLOOR IS NOT ONLY OPEN SESSIONS. Measured again 2026-08-28T13:04Z, task 0422, when it
    stood at 96 (43 main, 53 subagent). The split was 11 live, 84 abandoned-open, 1 real gap,
    and "roughly one file per open terminal" would have accounted for 11 of the 96:

      live               11 files, 6 sessions. Registered, `ended_at` NULL, file written since
                         the current WSL boot began. Correct, expected, and not a defect.
      abandoned-open     84 files, 36 sessions. Registered, `ended_at` NULL, file last written
                         in an EARLIER boot, so no process is writing them and no `SessionEnd`
                         is ever coming. 22 of them carry an mtime inside the four seconds
                         before the 2026-08-28 12:28 EEST shutdown flush.
      real gap            1 file. c8b01a6c-...-e2e8a6b3332e, 790,376 bytes, session ended
                         2026-08-18T23:59:07Z with no transcript row behind it.

    The instrument that splits live from abandoned is NOT in the store and is not printed here:
    it is the start of the current boot, from `journalctl --list-boots`. A file whose mtime
    predates this boot cannot be one a running session is appending to, whatever its session row
    says, and that test needs nothing path-derived and guesses nothing. It stays out of this
    report because a query in this module may not depend on a fact that is only true of the host
    it happens to run on.

    WHY 84 SESSIONS NEVER ENDED, since the hook is installed and working. `SessionEnd` is the
    only path that indexes a transcript, this lane's own code lives on `/mnt/c`, and `/mnt/c`
    goes away BEFORE the hooks that fire at WSL shutdown can finish. `~/.claude/brain-session-
    hook.log` carries the same refusal on both of this month's shutdowns, 2026-08-20T17:19:50Z
    and 2026-08-28T09:28:27Z:

        OSError: [Errno 5] Input/output error:
            '/mnt/c/Users/you/repos/infinity-os/ingest/bin/..'

    at the import, before any verb runs. So the count rises by one per session every time the
    host is restarted with terminals open, and stays there: nothing retries. `ingest backfill
    --only-missing` is the drain, and it is the ONLY thing that closes this bucket.

    The one real gap has its own cause and it is not the mount: the hook logged
    `SessionEnd ... ended`, then `transcript_index` raised `OperationalError: server closed the
    connection unexpectedly`. One `SessionEnd` is two transactions, the end committed, the store
    died, and the index never happened. That is the shape to look for whenever this bucket holds
    a session with an `ended_at`: not a missing hook, a store that went away between two of its
    statements.

    Both directions are reported because both were uncounted. `indexed_not_on_disk` is a
    pointer whose file is gone: 61 of them on 2026-08-17 (task 0324).

    Three things this deliberately does NOT do:

    - It does not resolve symlinks. `tx.scan` stores `os.path.abspath(path)`, so this compares
      `abspath` to `abspath`. A `realpath` here would look tidier and would mismatch every row
      the moment any component of the root became a link.
    - It does not treat another host's pointers as dead. A row stored against a different
      `host_id` is not missing just because this machine cannot see the file, so those rows are
      counted on their own and kept out of both differences. Zero on this host today, and the
      first VPS pointer would otherwise arrive as 1,299 fresh "dead" pointers.
    - It does not filter workflow journals out of `files_on_disk`. They are not sessions, but
      `backfill.py` and `transcript_index` DO index them as keyless pointers, so they belong in
      this denominator. `files_on_disk_by_kind` says how many there are, so nobody tries to
      "fix" them into sessions.
    """
    root = os.path.abspath(os.path.expanduser(
        projects_root or os.environ.get("CLAUDE_PROJECTS_ROOT") or "~/.claude/projects"))
    ptr_col, host_col = ("pointer", "pointer_host") if profiles.is_brain() \
        else ("path", "host_id")
    host = config.host_id()

    cur.execute(f"SELECT {ptr_col} AS p FROM transcript WHERE {host_col} = %s", (host,))
    indexed = {r["p"] for r in cur.fetchall()}
    # `IS DISTINCT FROM`, not `<>`: both schemas declare the host column NOT NULL today, and a
    # `<>` here would quietly drop a NULL-host row out of BOTH buckets the day one does not.
    # A row that is in the table and in neither number is the exact bug this section is for.
    cur.execute(f"SELECT count(*) AS n FROM transcript "
                f"WHERE {host_col} IS DISTINCT FROM %s", (host,))
    other_hosts = cur.fetchone()["n"]
    cur.execute("SELECT count(*) AS n FROM transcript")
    pointers_total = cur.fetchone()["n"]

    out: dict[str, Any] = {"projects_root": root, "host_id": host,
                           "root_exists": os.path.isdir(root),
                           "pointers_total": pointers_total,
                           "pointers_indexed_here": len(indexed),
                           "pointers_on_other_hosts": other_hosts}

    if not out["root_exists"]:
        # An unreadable root makes every pointer look dead. Reporting `indexed_not_on_disk:
        # 1299` because a path is wrong is worse than reporting nothing, so the counts are
        # withheld and the reason is named. Refusing to answer is an answer; guessing is not.
        out["refused"] = (f"{root} is not a directory on {host}, so the disk cannot be "
                          f"compared against the store from here. Counts withheld rather than "
                          f"reported as gaps. Pass --projects-root, or set "
                          f"CLAUDE_PROJECTS_ROOT.")
        return out

    on_disk = {os.path.abspath(p): tx.classify(p, root)[0] for p in tx.walk(root)}
    missing = sorted(set(on_disk) - indexed)
    dead = sorted(indexed - set(on_disk))

    by_kind: dict[str, int] = {}
    for kind in on_disk.values():
        by_kind[kind] = by_kind.get(kind, 0) + 1
    missing_by_kind: dict[str, int] = {}
    for p in missing:
        missing_by_kind[on_disk[p]] = missing_by_kind.get(on_disk[p], 0) + 1

    total = len(on_disk)
    hit = total - len(missing)
    out.update({
        "files_on_disk": total,
        "files_on_disk_by_kind": by_kind,
        "indexed": {"n": hit, "of": total,
                    "pct": round(100.0 * hit / total, 1) if total else None},
        "on_disk_not_indexed": {
            "n": len(missing), "of": total,
            "by_kind": missing_by_kind,
            "sample": missing[:SAMPLE_LIMIT], "sample_of": len(missing)},
        "indexed_not_on_disk": {
            "n": len(dead), "of": len(indexed),
            # Task 0324. Without this, `n` and `pointers_absent.open` are two numbers a reader
            # has to reconcile by hand, and the obvious reading of a difference -- "files came
            # back" -- is the wrong one. `not_yet_recorded` is the honest name: these are
            # pointers the disk says are gone that `transcript verify` has not walked up to
            # yet, so they are counted but not classified and nobody knows if they matter.
            "recorded_absent": 0, "not_yet_recorded": len(dead),
            "sample": dead[:SAMPLE_LIMIT], "sample_of": len(dead)},
    })
    if dead:
        cur.execute("SELECT count(*) AS n FROM transcript_absence "
                    "WHERE returned_at IS NULL AND pointer_host = %s AND pointer = ANY(%s)",
                    (host, dead))
        recorded = cur.fetchone()["n"]
        out["indexed_not_on_disk"].update({"recorded_absent": recorded,
                                           "not_yet_recorded": len(dead) - recorded})
    return out


def coverage_report(cur, projects_root: str | None = None) -> dict[str, Any]:
    """Coverage for whichever profile is live. The two schemas do not carry the same facts,
    so the two reports do not either; see profiles.py for the enumerated difference.

    `filesystem` is the same section on both profiles and is not optional. There is no
    `--no-walk`: a caller who could turn the disk-side denominator off would eventually quote
    `transcript_orphans.open` as the size of the gap again, which is the reading task 0323
    exists to make impossible. The walk costs 6.6 ms over 1,256 files, and this function has
    exactly one caller (`bin/ingest` `cmd_coverage`); the session hook does not import this
    module, so nothing here is on the operator's session-start path.
    """
    out = brain_coverage_report(cur) if profiles.is_brain() else scratch_coverage_report(cur)
    out["filesystem"] = filesystem_report(cur, projects_root)
    return out


def brain_coverage_report(cur) -> dict[str, Any]:
    """D1's migration 1. Fewer columns than the scratch schema, so fewer rows here.

    What is absent is absent on purpose and named in profiles.py rather than approximated:
    there is no `model`, no token or cost column and no `last_activity_at`, so no per-model
    rollup, no derivable cost, and no "has an end time" number that is really a last-record
    timestamp wearing an end's name.
    """
    out: dict[str, Any] = {"profile": "brain"}

    cur.execute("SELECT count(*) AS n FROM session")
    total = cur.fetchone()["n"]
    out["sessions_total"] = total

    cur.execute("""SELECT coalesce(nullif(role,''),'(blank)') AS role, count(*) AS n
                   FROM session GROUP BY 1 ORDER BY 1""")
    out["sessions_by_role"] = {r["role"]: r["n"] for r in cur.fetchall()}

    def cov(label: str, where: str, denom: str = "TRUE") -> None:
        cur.execute(f"SELECT count(*) AS n FROM session WHERE {denom}")
        d = cur.fetchone()["n"]
        cur.execute(f"SELECT count(*) AS n FROM session WHERE ({denom}) AND ({where})")
        h = cur.fetchone()["n"]
        out.setdefault("coverage", {})[label] = {
            "n": h, "of": d, "pct": round(100.0 * h / d, 1) if d else None}

    cov("stated_goal_present", "stated_goal IS NOT NULL")
    cov("workdir_resolved", "workdir <> ''")
    cov("actor_type_known", "actor_type IS NOT NULL")
    cov("agent_known", "agent <> ''")
    # `ended_at` here means an end was actually observed. This schema has no last-activity
    # column to confuse it with, which is the one place it is simpler than the scratch one.
    cov("ended_at_observed", "ended_at IS NOT NULL")
    cov("parent_known_subagents_only", "parent_session_id IS NOT NULL", "role = 'subagent'")
    cov("parent_row_present_subagents_only",
        "parent_session_id IN (SELECT id FROM session)", "role = 'subagent'")
    cov("work_item_linked", "work_item_id IS NOT NULL")

    cur.execute("""SELECT to_char(date_trunc('month', started_at), 'YYYY-MM') AS m, count(*) AS n
                   FROM session GROUP BY 1 ORDER BY 1""")
    out["sessions_by_month"] = {r["m"]: r["n"] for r in cur.fetchall()}

    cur.execute("""SELECT coalesce(nullif(harness,''),'(blank)') AS h, count(*) AS n
                   FROM session GROUP BY 1 ORDER BY 2 DESC""")
    out["by_harness"] = {r["h"]: r["n"] for r in cur.fetchall()}

    cur.execute("""SELECT coalesce(produced_by_producer,'NULL (not stamped)') AS p, count(*) AS n
                   FROM session GROUP BY 1 ORDER BY 2 DESC""")
    out["sessions_by_producer"] = {r["p"]: r["n"] for r in cur.fetchall()}

    cur.execute("SELECT count(*) AS n, coalesce(sum(bytes),0) AS b FROM transcript")
    r = cur.fetchone()
    out["transcripts_total"] = {"files": r["n"], "bytes": int(r["b"]),
                                "mib": round(int(r["b"]) / 1048576, 1)}

    cur.execute("""SELECT pointer_host, count(*) AS n, coalesce(sum(bytes),0) AS b
                   FROM transcript GROUP BY 1 ORDER BY 2 DESC""")
    out["pointers_by_host"] = [{"pointer_host": r["pointer_host"], "files": r["n"],
                                "mib": round(int(r["b"]) / 1048576, 1)} for r in cur.fetchall()]

    cur.execute("""SELECT count(*) AS total,
                          count(*) FILTER (WHERE session_id IS NOT NULL) AS linked
                   FROM transcript""")
    r = cur.fetchone()
    out["transcripts_linked_to_a_session"] = {
        "n": r["linked"], "of": r["total"],
        "pct": round(100.0 * r["linked"] / r["total"], 1) if r["total"] else None}

    # verified_ok is a nullable boolean, and task 0324 made all three of its states mean
    # something. NULL with a verified_at is not "unknown": it is `transcript verify` saying it
    # looked and the answer was neither a pass nor a fail. That is why those rows are their own
    # lines here instead of sitting inside a failure bucket next to a hash mismatch, which is a
    # genuine corruption signal. `mismatch` and `unreadable` still share `false`; D1's column
    # cannot hold them apart and both are failures, so the collapse costs nothing a reader acts
    # on.
    #
    # Task 0330 gave NULL a SECOND cause -- `appended`, a file that only grew -- and the two
    # must not merge into one line, or a report that exists to separate benign from actionable
    # would start hiding one benign thing inside another. They are split by the fact that
    # differs on disk rather than by anything new in the column: `missing` books an open
    # `ingest.transcript_absence` row and `appended` never does. The ELSE is exact and not a
    # fallback -- those two are the only writers of a NULL alongside a non-NULL `verified_at`,
    # since the index upsert clears BOTH columns together and never one of them.
    cur.execute("""SELECT CASE WHEN t.verified_at IS NULL THEN 'not-yet-verified'
                               WHEN t.verified_ok THEN 'ok'
                               WHEN t.verified_ok IS FALSE THEN 'not-ok (mismatch|unreadable)'
                               WHEN a.pointer IS NOT NULL
                                    THEN 'checked; file gone (see pointers_absent)'
                               ELSE 'checked; file grew since indexing (re-index to re-cover)'
                          END AS v,
                          count(*) AS n
                   FROM transcript t
                   LEFT JOIN ingest.transcript_absence a
                          ON a.pointer = t.pointer AND a.pointer_host = t.pointer_host
                         AND a.returned_at IS NULL
                   GROUP BY 1 ORDER BY 2 DESC""")
    out["verify_state"] = {r["v"]: r["n"] for r in cur.fetchall()}

    out["transcript_orphans"] = _orphan_counts(cur)
    out["pointers_absent"] = _absence_counts(cur)

    cur.execute("""SELECT emit_status, count(*) AS n FROM event_outbox GROUP BY 1""")
    out["event_outbox"] = {r["emit_status"]: r["n"] for r in cur.fetchall()}

    return out


def scratch_coverage_report(cur) -> dict[str, Any]:
    out: dict[str, Any] = {"profile": "scratch"}

    cur.execute("SELECT kind, count(*) AS n FROM session GROUP BY kind ORDER BY kind")
    out["sessions_by_kind"] = {r["kind"]: r["n"] for r in cur.fetchall()}
    cur.execute("SELECT count(*) AS n FROM session")
    total = cur.fetchone()["n"]
    out["sessions_total"] = total

    cur.execute("SELECT kind, count(*) AS n, sum(bytes) AS b FROM transcript GROUP BY kind ORDER BY kind")
    out["transcripts_by_kind"] = {r["kind"]: {"files": r["n"], "bytes": int(r["b"] or 0)}
                                  for r in cur.fetchall()}
    cur.execute("SELECT count(*) AS n, coalesce(sum(bytes),0) AS b FROM transcript")
    r = cur.fetchone()
    out["transcripts_total"] = {"files": r["n"], "bytes": int(r["b"]),
                                "mib": round(int(r["b"]) / 1048576, 1)}

    def cov(label: str, where: str, denom: str = "TRUE") -> None:
        cur.execute(f"SELECT count(*) AS n FROM session WHERE {denom}")
        d = cur.fetchone()["n"]
        cur.execute(f"SELECT count(*) AS n FROM session WHERE ({denom}) AND ({where})")
        h = cur.fetchone()["n"]
        out.setdefault("coverage", {})[label] = {
            "n": h, "of": d, "pct": round(100.0 * h / d, 1) if d else None}

    cov("workdir_resolved", "workdir IS NOT NULL")
    cov("workdir_from_record_cwd", "workdir_source = 'record-cwd'")
    cov("workdir_from_decoded_dirname", "workdir_source = 'decoded-dirname'")
    cov("model_known", "model IS NOT NULL")
    cov("started_at_known", "started_at IS NOT NULL")
    # Read these two together. last_activity_at is the last record in the file, a LOWER BOUND
    # on when the session stopped. ended_at means an end was actually observed. A backfilled
    # corpus has 100% of the first and 0% of the second, and reporting only the first as "has
    # an end time" would be the confidently-wrong answer.
    cov("last_activity_known", "last_activity_at IS NOT NULL")
    cov("ended_at_observed", "ended_at IS NOT NULL")
    cov("workdir_on_drvfs_not_portable", "workdir_path_class = 'wsl-drvfs'")
    cov("cost_usd_known", "cost_usd IS NOT NULL")
    cov("token_usage_known", "tokens_output IS NOT NULL")
    cov("turns_counted", "coalesce(turns_user,0) + coalesce(turns_assistant,0) > 0")
    cov("harness_version_known", "harness_version IS NOT NULL")
    cov("git_branch_known", "git_branch IS NOT NULL")
    cov("parent_known_subagents_only", "parent_session_key IS NOT NULL", "kind = 'subagent'")
    cov("parent_row_present_subagents_only",
        "parent_session_key IN (SELECT session_key FROM session)", "kind = 'subagent'")
    cov("stated_goal_present", "stated_goal IS NOT NULL")

    cur.execute("""SELECT stated_goal_tier AS tier, count(*) AS n FROM session
                   GROUP BY 1 ORDER BY 1""")
    out["stated_goal_by_tier"] = {r["tier"]: {"n": r["n"], "of": total,
                                              "pct": round(100.0 * r["n"] / total, 1) if total else None}
                                  for r in cur.fetchall()}

    cur.execute("""SELECT to_char(date_trunc('month', started_at), 'YYYY-MM') AS m,
                          count(*) AS n
                   FROM session WHERE started_at IS NOT NULL GROUP BY 1 ORDER BY 1""")
    out["sessions_by_month"] = {r["m"]: r["n"] for r in cur.fetchall()}

    cur.execute("""SELECT harness, count(*) AS n FROM session GROUP BY 1 ORDER BY 2 DESC""")
    out["by_harness"] = {r["harness"]: r["n"] for r in cur.fetchall()}

    cur.execute("""SELECT model, count(*) AS n FROM session GROUP BY 1 ORDER BY 2 DESC NULLS LAST""")
    out["by_model"] = {(r["model"] or "NULL (not recorded)"): r["n"] for r in cur.fetchall()}

    cur.execute("""SELECT path_class, host_id, count(*) AS n, coalesce(sum(bytes),0) AS b
                   FROM transcript GROUP BY 1,2 ORDER BY 3 DESC""")
    out["pointers_by_host_and_class"] = [
        {"host_id": r["host_id"], "path_class": r["path_class"], "files": r["n"],
         "mib": round(int(r["b"]) / 1048576, 1)} for r in cur.fetchall()]

    cur.execute("""SELECT coalesce(verify_result,'not-yet-verified') AS v, count(*) AS n
                   FROM transcript GROUP BY 1 ORDER BY 2 DESC""")
    out["verify_state"] = {r["v"]: r["n"] for r in cur.fetchall()}

    out["transcript_orphans"] = _orphan_counts(cur)
    out["pointers_absent"] = _absence_counts(cur)

    cur.execute("""SELECT emit_status, count(*) AS n FROM event_outbox GROUP BY 1""")
    out["event_outbox"] = {r["emit_status"]: r["n"] for r in cur.fetchall()}

    return out
