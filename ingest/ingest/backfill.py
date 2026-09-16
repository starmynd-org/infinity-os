"""Bulk read-and-index of the existing transcript corpus.

D00 allows this path to call the library directly rather than shelling one verb per file:
"Bulk backfill may use the library directly, since that is a read and index path rather than
a transition other surfaces must observe." It still calls the same `session_register` and
`transcript_index` functions the hook calls, in one transaction per file, so there is exactly
one implementation of each transition. What it skips is the subprocess, not the verb.

Backfilled sessions do not emit `session.started`. Replaying two months of session starts
into a fabric that has never seen them would tell every future subscriber that 1,100 sessions
began the moment the index was built. Backfill is an index, not a history replay.

## The only-missing selector (task 0329)

`already_indexed` is the one place that answers "does this pointer have a row yet", and both
of the paths added for 0329 share it:

  * `run(..., only_missing=True)` -- a whole-corpus pass that touches only files with no row.
  * `index_missing_subagents` -- the same question asked of one session's `subagents/`
    directory, which is what the hook calls at SessionEnd.

Insert-only-if-absent is not an optimisation here, it is the safety property. Both profiles'
transcript upserts set `verified_at = NULL` and `verified_ok`/`verify_result = NULL` ON
CONFLICT, so a pass that re-upserts an already-indexed pointer wipes the verification state of
every row it touches (task 0318 hit exactly this, which is why the unconditional backfill is
unsafe to re-run casually). Never touching an existing pointer is what keeps that hazard out.

The trade is stated rather than hidden: a file whose CONTENT changed after it was indexed is
skipped by `only_missing`, because its pointer already has a row. Catching drift is
`transcript verify`'s job -- it re-hashes -- and this selector is for files that have no row
at all. The two are different questions and the fast one must not pretend to answer the slow
one.
"""

from __future__ import annotations

import os
import sys
import time
from collections import Counter
from typing import Any

from . import config, profiles, store, transcript as tx, verbs
from .goal import adjudicate


def already_indexed(cur, paths: list[str]) -> set[str]:
    """Which of these absolute paths already carry a `transcript` row on THIS host.

    One statement whatever the length of `paths`, and scoped to `config.host_id()` for the
    same reason `queries.filesystem_report` is: a pointer stored against another host is not a
    row about a file this machine can see, and treating it as one would make a sweep here skip
    a file that has never been indexed locally.

    Requires a RealDictCursor, as every read in this lane does.
    """
    if not paths:
        return set()
    ptr_col, host_col = ("pointer", "pointer_host") if profiles.is_brain() \
        else ("path", "host_id")
    cur.execute(f"SELECT {ptr_col} AS p FROM transcript "
                f"WHERE {host_col} = %s AND {ptr_col} = ANY(%s)",
                (config.host_id(), [os.path.abspath(p) for p in paths]))
    return {r["p"] for r in cur.fetchall()}


def run(projects_root: str, *, limit: int | None = None, progress_every: int = 100,
        only_missing: bool = False, out=sys.stderr) -> dict[str, Any]:
    """Index the corpus. With `only_missing`, index only what has no pointer row yet.

    `only_missing` skips a file WHOLE -- its session is not re-registered either. That is
    deliberate: the point of the flag is that an already-indexed file's rows are not written
    to at all, and re-running `session_register` for it would still move `last_activity_at`
    and re-adjudicate a goal that is already settled.
    """
    files = tx.walk(projects_root)
    if limit:
        files = files[:limit]

    counts: Counter = Counter()
    coverage: Counter = Counter()
    errors: list[tuple[str, str]] = []
    started = time.time()

    with store.transaction() as cur:
        if only_missing:
            have = already_indexed(cur, files)
            kept = [p for p in files if os.path.abspath(p) not in have]
            counts["skipped_already_indexed"] = len(files) - len(kept)
            counts["files_considered"] = len(files)
            files = kept
        for i, path in enumerate(files, 1):
            try:
                facts = tx.scan(path, projects_root)
            except Exception as exc:
                errors.append((path, f"scan: {exc}"))
                counts["scan_error"] += 1
                continue

            counts[f"file:{facts.kind}"] += 1
            counts["files"] += 1

            try:
                if facts.kind != tx.KIND_WORKFLOW_JOURNAL and facts.session_key:
                    goal, tier, gsource = adjudicate(facts)
                    verbs.session_register(
                        facts.session_key,
                        session_id=facts.session_id, agent_id=facts.agent_id,
                        kind=facts.kind, harness=facts.harness,
                        harness_version=facts.harness_version, entrypoint=facts.entrypoint,
                        model=facts.model, model_source=facts.model_source,
                        permission_mode=facts.permission_mode, git_branch=facts.git_branch,
                        is_sidechain=facts.is_sidechain,
                        project_root_dir=facts.project_root_dir, workdir=facts.workdir,
                        workdir_source=facts.workdir_source,
                        started_at=facts.started_at,
                        last_activity_at=facts.ended_at, state="unknown",
                        time_source=facts.time_source,
                        parent_session_key=facts.parent_session_id,
                        parent_source=facts.parent_source, workflow_id=facts.workflow_id,
                        turns_user=facts.turns_user, turns_assistant=facts.turns_assistant,
                        turns_source=facts.turns_source,
                        tokens={"input": facts.tokens_input, "output": facts.tokens_output,
                                "cache_read": facts.tokens_cache_read,
                                "cache_creation": facts.tokens_cache_creation,
                                "messages": facts.tokens_messages,
                                "usage_records": facts.tokens_usage_records,
                                "source": facts.tokens_source},
                        cost_usd=facts.cost_usd, cost_source=facts.cost_source,
                        stated_goal=goal, stated_goal_source=gsource, stated_goal_tier=tier,
                        actor_type="ai", registration_source="backfill",
                        emit_event=False, cur=cur,
                    )
                    counts["sessions_registered"] += 1
                    coverage[f"goal:{tier}"] += 1
                elif facts.kind == tx.KIND_WORKFLOW_JOURNAL:
                    counts["workflow_journals_not_registered"] += 1
                else:
                    counts["no_session_key"] += 1
                    errors.append((path, "no session_key derivable"))

                verbs.transcript_index(
                    path, projects_root=projects_root,
                    session_key=facts.session_key if facts.kind != tx.KIND_WORKFLOW_JOURNAL else None,
                    cur=cur,
                )
                counts["transcripts_indexed"] += 1
            except Exception as exc:
                errors.append((path, f"index: {exc}"))
                counts["index_error"] += 1
                raise

            # coverage, measured not assumed
            coverage["workdir:record-cwd" if facts.workdir_source == "record-cwd" else
                     "workdir:decoded-dirname" if facts.workdir_source == "decoded-dirname" else
                     "workdir:none"] += 1
            if facts.workdir_decode_matches_cwd is True:
                coverage["decode:matches-cwd"] += 1
            elif facts.workdir_decode_matches_cwd is False:
                coverage["decode:lossy"] += 1
            else:
                coverage["decode:untestable"] += 1
            coverage["model:present" if facts.model else "model:null"] += 1
            coverage["end:present" if facts.ended_at else "end:null"] += 1
            coverage["start:present" if facts.started_at else "start:null"] += 1
            coverage["cost:present" if facts.cost_usd is not None else "cost:null"] += 1
            coverage["tokens:present" if facts.tokens_output is not None else "tokens:null"] += 1
            # THE DENOMINATOR IS PART OF THE COVERAGE, not a detail below it. `repeated` counts
            # the sessions where the harness wrote one message as several records -- that is the
            # population the old per-record sum was wrong on, and printing it is how a backfill
            # run says how much of its corpus the dedup actually changed rather than asserting
            # that it changed something. `once` is a real session, not a failure: it means every
            # message appeared exactly once and dedup was a no-op there.
            if facts.tokens_messages is not None:
                coverage["tokens:repeated" if facts.tokens_usage_records > facts.tokens_messages
                         else "tokens:once"] += 1
            coverage["turns:nonzero" if (facts.turns_user + facts.turns_assistant) else "turns:zero"] += 1
            if facts.kind == tx.KIND_SUBAGENT:
                coverage[f"parent:{facts.parent_source or 'none'}"] += 1

            if progress_every and i % progress_every == 0:
                print(f"  ...{i}/{len(files)}", file=out, flush=True)

    return {
        "files_seen": len(files),
        "elapsed_s": round(time.time() - started, 1),
        "counts": dict(counts),
        "coverage": dict(coverage),
        "errors": errors[:50],
        "error_count": len(errors),
    }


# ---------------------------------------------------------------------------
# One session's subagents (task 0329)
# ---------------------------------------------------------------------------
#: Where the sweep stops when nobody gives it a budget. The hook passes what is left of its own
#: 8 s and this is only the library default, for a caller with no clock of its own.
SUBAGENT_SWEEP_BUDGET_S = 8.0


def session_subagent_dir(session_id: str, *, transcript_path: str | None = None,
                         projects_root: str | None = None) -> str | None:
    """Locate `<project>/<session_id>/subagents` for one session, or None if it has none.

    Claude Code writes a session's subagent transcripts beside its main file, so the
    transcript path the hook is handed already names the directory and no walk is needed:
    `<root>/<project_dir>/<uuid>.jsonl` puts them in `<root>/<project_dir>/<uuid>/subagents`.
    The fallback scans only the project directories directly under the root -- one `isdir`
    each, no recursion -- for the case where the caller has a session id and no path.
    """
    if transcript_path:
        cand = os.path.join(os.path.dirname(os.path.abspath(transcript_path)),
                            session_id, "subagents")
        if os.path.isdir(cand):
            return cand
    root = os.path.abspath(os.path.expanduser(
        projects_root or os.environ.get("CLAUDE_PROJECTS_ROOT") or "~/.claude/projects"))
    if not os.path.isdir(root):
        return None
    for name in sorted(os.listdir(root)):
        cand = os.path.join(root, name, session_id, "subagents")
        if os.path.isdir(cand):
            return cand
    return None


def index_missing_subagents(session_id: str, *, transcript_path: str | None = None,
                            projects_root: str | None = None,
                            budget_s: float | None = None,
                            registration_source: str = "hook",
                            cur=None) -> dict[str, Any]:
    """Index every subagent transcript of one session that has no pointer row yet.

    THE HOLE THIS FILLS (task 0329, measured 2026-08-17). `claude-session-hook` indexes exactly
    one file at SessionEnd -- `payload['transcript_path']`, the MAIN session -- and Claude Code
    fires no per-subagent event this lane is wired to. So until this function existed, the only
    code path that had ever written a subagent transcript row was a human running `ingest
    backfill`. The evidence: 689 of the 698 subagent files on disk carried the identical
    `indexed_at` of 2026-08-16T12:20:40Z, one sweep, and the 9 born after it had no row at all
    until a task went and made them by hand.

    Why the hook and not a `SubagentStop` entry. `SubagentStop` does exist in this harness
    (claude 2.1.233) and its payload carries the parent's `session_id` and `transcript_path`,
    NOT the subagent's own file, so a hook wired to it would still have to enumerate this
    directory -- it would buy the timing, not the lookup. What it would cost is an edit to
    `~/.claude/settings.json`, which `ingest/docs/HOOK-INSTALL.md` records as the operator's
    own step, refused to agents by the permission classifier on three separate routes and
    deliberately so. This function needs no such edit: SessionEnd is already wired.

    Three properties it must not lose:

    1. **It only ever inserts.** A file that already has a pointer row is skipped, never
       re-upserted, because both profiles' upserts null `verified_at` on conflict.
    2. **It is bounded.** It runs inside the hook's 8 s budget and hashes and parses every file
       it indexes. When the budget runs out it STOPS and reports `deferred`, and those files
       are picked up by `ingest backfill --only-missing`. A hook that overruns its budget to
       finish an index is a hook that makes the operator's session hang.
    3. **One bad file does not lose the others.** Each index runs inside its own SAVEPOINT, so
       a scan or constraint failure rolls back that file alone rather than poisoning the
       transaction the rest of the sweep is riding in.

    `register_missing_session=True` matches what `backfill.run` does for these same files: a
    subagent session is only ever evidenced by its transcript, so refusing to register it would
    index every one of them as a keyless orphan.

    `registration_source` is `hook` and not a finer `hook:subagent-sweep`, because the scratch
    schema constrains the column to `('hook','backfill','manual')` and the brain profile has no
    such column at all. Measured, not assumed: the finer value was tried first and every
    subagent register raised `CheckViolation` on `session_registration_source_check`, which the
    per-file SAVEPOINT then rolled back one file at a time exactly as designed. Widening a
    vocabulary to carry which BRANCH of the hook wrote a row is a schema change this task did
    not need; the branch is already legible in the hook log line.
    """
    started = time.time()
    budget = SUBAGENT_SWEEP_BUDGET_S if budget_s is None else budget_s
    root = os.path.abspath(os.path.expanduser(
        projects_root or os.environ.get("CLAUDE_PROJECTS_ROOT") or "~/.claude/projects"))
    out: dict[str, Any] = {"session_id": session_id, "subagent_dir": None, "on_disk": 0,
                           "already_indexed": 0, "indexed": 0, "deferred": 0, "failed": 0,
                           "errors": [], "budget_s": round(budget, 2), "elapsed_s": 0.0}

    sdir = session_subagent_dir(session_id, transcript_path=transcript_path,
                                projects_root=root)
    if sdir is None:
        out["elapsed_s"] = round(time.time() - started, 2)
        return out
    out["subagent_dir"] = sdir

    # `tx.walk` and not a glob: it is the same enumeration `backfill.run` indexes from, so a
    # workflow's `subagents/workflows/<wf>/` files are found at whatever depth they sit.
    files = tx.walk(sdir)
    out["on_disk"] = len(files)
    if not files:
        out["elapsed_s"] = round(time.time() - started, 2)
        return out

    def _sweep(c) -> None:
        have = already_indexed(c, files)
        todo = [p for p in files if os.path.abspath(p) not in have]
        out["already_indexed"] = len(files) - len(todo)
        for i, path in enumerate(todo):
            if time.time() - started > budget:
                out["deferred"] = len(todo) - i
                return
            c.execute("SAVEPOINT brain_subagent_sweep")
            try:
                verbs.transcript_index(path, projects_root=root,
                                       register_missing_session=True,
                                       registration_source=registration_source, cur=c)
            except Exception as exc:
                c.execute("ROLLBACK TO SAVEPOINT brain_subagent_sweep")
                out["failed"] += 1
                if len(out["errors"]) < 5:
                    out["errors"].append((path, f"{type(exc).__name__}: {exc}"))
            else:
                c.execute("RELEASE SAVEPOINT brain_subagent_sweep")
                out["indexed"] += 1

    if cur is not None:
        _sweep(cur)
    else:
        with store.transaction() as c:
            _sweep(c)
    out["elapsed_s"] = round(time.time() - started, 2)
    return out
