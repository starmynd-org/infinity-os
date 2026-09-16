"""The three verbs this lane owns: `entity resolve`, `receipt book`, `touch add`.

Per the narrow waist in `D00-shared-context.md`, every surface that needs lineage or a
promotion calls these. The MCP server's `resolve_entity` and `book_receipt` tools are thin
wrappers over this module, never a second implementation.

`lineage project` is a FOURTH SUBCOMMAND AND NOT A FOURTH VERB, and the distinction is task
0103's. The three verbs above write git. `lineage project` writes nothing: it reads a commit
git already holds and calls the one store transition of the same name (`store_projection.py`),
which is the loader migration 4's projection always implied. It is here because that transition
had no surface at all -- not this CLI, not the console, not MCP -- and the only way to run the
hop from a booked receipt into the store was to import the module in a Python REPL, which is
what D9 had to do to finish the acceptance run (task 0141, from 0118).

Exit codes are part of the contract, because a caller must be able to tell the outcomes apart
without parsing prose:

  0  resolved / booked / projected
  2  usage error
  3  unresolved entity      (no node matched)
  4  ambiguous entity       (several nodes matched; the caller must disambiguate)
  5  promotion refused      (a typed refusal from promotion.py; stderr names the code)
  6  store unreachable      (`lineage project` ONLY: it is the one subcommand needing Postgres)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config
from .brief import DEFAULT_BUDGET_TOKENS, BriefRenderer
from .index import STATUS_AMBIGUOUS, STATUS_UNRESOLVED, EntityIndex
from .promotion import Lineage, PromotionRefused
from .receipt import Receipt, Touch, book

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNRESOLVED = 3
EXIT_AMBIGUOUS = 4
EXIT_REFUSED = 5
EXIT_STORE = 6

#: The program name in warnings, matching `bin/brain-adapter`.
_PROG = "brain-adapter"


def _index(args) -> EntityIndex:
    return EntityIndex.build(
        config.brain_root(getattr(args, "brain", None)),
        config.cache_dir(),
        use_cache=not getattr(args, "no_cache", False),
    )


def _emit(obj) -> None:
    print(json.dumps(obj, indent=2))


def _target_repo(args):
    """The repo a subcommand writes to or reads from, and a warning when it is not the brain.

    `--repo` accepts ANY git repository and this does not change that (task 0256). It is used on
    purpose: this lane proves promotions on scratch worktrees, and the test suite books into
    throwaway `/tmp` fixtures. Refusing a foreign repo here would break both, and would also be
    the wrong layer -- the defect 0256 measured was never that foreign repos are reachable, it was
    that the STORE could not tell afterwards which repo a `git_ref` came from. Migration 19 and
    `store_join.repo_identity` fix that where it belongs: in the row.

    What was missing at THIS layer is that booking into a throwaway directory looked exactly like
    booking into the brain, so nobody running the command had any reason to think twice. Measured
    2026-08-16: 20 of 26 `git_ref`s in the live store resolve in no repository at all, and four
    of them were still readable in `/tmp/0149-cli-zf9k78n8` and
    `/tmp/0149-proof-6a81fb59-ifs6ld7b`. This line is what those runs did not print.

    A warning, not a refusal, and stderr, not stdout: the JSON contract on stdout is what callers
    parse and adding a field to it is a different change from telling a human what they just did.
    """
    brain = config.brain_root(getattr(args, "brain", None))
    if not getattr(args, "repo", None):
        return brain
    repo = Path(args.repo).resolve()
    # A LINKED WORKTREE OF THE BRAIN IS THE BRAIN, and comparing paths says otherwise. That
    # mattered the moment 0349 made `receipts/durable` the only bookable ref: a booking has to
    # happen in a worktree, since the brain's own checkout stays on `main`, so every legitimate
    # booking would print a warning saying its commit was as durable as the throwaway it is not.
    # Identity is the root commit -- the same key migration 19 uses -- so the shared object store
    # is recognised rather than guessed at from a path.
    if repo != brain and _same_repo(repo, brain):
        print(f"{_PROG}: --repo {repo} is a worktree of the brain (same object store, same "
              f"identity). A commit here lands on a ref in the brain's own ref store, so the "
              f"worktree being temporary costs nothing.", file=sys.stderr)
    elif repo != brain:
        print(f"{_PROG}: --repo {repo} is not the configured brain ({brain}). This is allowed -- "
              f"scratch worktrees and test fixtures are why --repo exists -- but a commit made "
              f"here is durable evidence only for as long as THIS repository is. If it is a "
              f"throwaway, do not project it into the store: since migration 19 the row records "
              f"which repository it came from, and before it, 20 of 26 stored git_refs pointed at "
              f"repositories that no longer exist.", file=sys.stderr)
    return repo


def _same_repo(a: Path, b: Path) -> bool:
    """Two paths, one repository? Answered by root commit, never by string comparison."""
    from .durability import repo_identity

    ia, ib = repo_identity(a), repo_identity(b)
    return bool(ia) and ia == ib


def _status_exit(status: str) -> int:
    return {STATUS_UNRESOLVED: EXIT_UNRESOLVED, STATUS_AMBIGUOUS: EXIT_AMBIGUOUS}.get(status, EXIT_OK)


# ---- entity resolve -------------------------------------------------------------

def cmd_entity_resolve(args) -> int:
    idx = _index(args)
    if args.audit:
        return _audit(idx, args)
    res = idx.reverse(args.ref) if args.reverse else idx.resolve(args.ref)
    out = res.to_dict()
    out["produced_by_value"] = res.produced_by()
    _emit(out)
    if not res.ok:
        print(
            f"entity resolve: {res.status} for {args.ref!r}. produced_by is NULL and the "
            f"raw reference is preserved. This is never a fabricated id.",
            file=sys.stderr,
        )
    return _status_exit(res.status)


def _audit(idx: EntityIndex, args) -> int:
    """Reconcile a list of stored ids against the brain. Reports, never rejects."""
    refs = [l.strip() for l in sys.stdin.read().splitlines() if l.strip()] if args.ref == "-" \
        else [args.ref]
    rows = [idx.reverse(r).to_dict() for r in refs]
    dangling = [r for r in rows if r["status"] != "resolved"]
    _emit({
        "checked": len(rows),
        "resolved": len(rows) - len(dangling),
        "dangling": len(dangling),
        "dangling_ids": [r["ref"] for r in dangling],
    })
    return EXIT_OK  # a dangling id is a metric, not a failure


# ---- brief render ---------------------------------------------------------------

def cmd_brief_render(args) -> int:
    idx = _index(args)
    if args.work_item:
        wi = json.loads(Path(args.work_item).read_text(encoding="utf-8"))
    else:
        wi = json.loads(sys.stdin.read())
    brief = BriefRenderer(idx).render(wi, namespace=args.namespace, budget_tokens=args.budget)
    if args.stats_only:
        _emit(brief.stats())
    else:
        text = brief.text()
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        print(json.dumps(brief.stats(), indent=2), file=sys.stderr)
    return EXIT_OK


# ---- receipt book ---------------------------------------------------------------

def _resolve_produced_by(idx: EntityIndex, ref: str | None, allow_unresolved: bool):
    """Shared by `receipt book` and `touch add`. Refuses by default, never fabricates."""
    if not ref:
        return Lineage(produced_by=None, produced_by_ref=None, resolution_status=STATUS_UNRESOLVED), None
    res = idx.resolve(ref)
    if not res.ok and not allow_unresolved:
        return None, res
    lin = Lineage(**{k: v for k, v in res.produced_by().items()
                     if k in ("produced_by", "produced_by_ref", "resolution_status")})
    return lin, res


def cmd_receipt_book(args) -> int:
    idx = _index(args)
    lineage, res = _resolve_produced_by(idx, args.produced_by, args.allow_unresolved)
    if lineage is None:
        _emit({"refused": "UNRESOLVABLE_PRODUCER", "resolution": res.to_dict()})
        print(
            f"receipt book: --produced-by {args.produced_by!r} is {res.status}. Refusing to "
            f"book. Pass --allow-unresolved to book with produced_by=null and the raw "
            f"reference recorded visibly in the receipt.",
            file=sys.stderr,
        )
        return _status_exit(res.status)

    lineage.caused_by_event_id = args.caused_by_event_id
    lineage.approval_ref = args.approval_ref
    lineage.actor_type = args.actor_type
    lineage.session_id = args.session_id
    lineage.work_item_id = args.work_item_id
    lineage.canonical_task = args.canonical_task

    touches = []
    for spec in args.touch or []:
        t, err = _parse_touch(idx, spec, args.allow_unresolved)
        if t is None:
            _emit({"refused": "UNRESOLVABLE_TOUCH", "spec": spec, "resolution": err.to_dict()})
            return _status_exit(err.status)
        touches.append(t)

    receipt = Receipt(
        department=args.department,
        date=args.date,
        slug=args.slug,
        title=args.title,
        moment=args.moment,
        summary=args.summary,
        what_happened=_read_maybe(args.body),
        artifacts=args.artifact or [],
        touches=touches,
        external=args.external,
        canon_touching=args.canon_touching,
        lineage=lineage,
        promotion_kind=args.promotion_kind,
    )
    repo = _target_repo(args)
    try:
        result, path = book(repo, receipt, expect_branch=args.branch)
    except PromotionRefused as e:
        _emit({"refused": e.code, "message": e.message})
        print(f"receipt book refused [{e.code}]: {e.message}", file=sys.stderr)
        return EXIT_REFUSED
    _emit({"booked": True, "receipt_id": receipt.entity_id(),
           "path": str(path.relative_to(repo)), **result.to_dict()})
    _report_durability(result)
    return EXIT_OK


def _report_durability(result) -> None:
    """Say which tier the booking actually reached, at the moment it happens.

    `promote()` refuses anything below `ledger`, so this never has to announce a failure. It
    announces the CEILING instead: `ledger` means the commit survives scratch-branch cleanup, a
    deleted worktree and /tmp, and still lives in exactly one working copy until a human merges
    it. Printing "booked" without that distinction is how 26 provisional receipts read as done.
    """
    if result.durability_tier == "ledger":
        print(f"{_PROG}: booked at durability tier 'ledger' ({result.durable_ref}). It survives "
              f"branch cleanup, a deleted worktree, /tmp and gc. It is NOT on main and NOT "
              f"pushed: a human merges the ledger, and until then this commit exists in one "
              f"working copy.", file=sys.stderr)


# ---- touch add ------------------------------------------------------------------

def _parse_touch(idx: EntityIndex, spec: str, allow_unresolved: bool):
    """`<ref>:<component_type>:<orient_role>[:<role>]`"""
    parts = spec.split(":")
    if len(parts) < 3:
        raise SystemExit(
            f"--touch expects <ref>:<component_type>:<orient_role>[:<role>], got {spec!r}")
    ref, ctype, orient = parts[0], parts[1], parts[2]
    role = parts[3] if len(parts) > 3 else "load-bearing"
    res = idx.resolve(ref)
    if not res.ok and not allow_unresolved:
        return None, res
    return Touch(
        entity_id=res.entity_id,
        component_type=ctype,
        orient_role=orient,
        role=role,
        entity_ref=ref,
        resolution_status=res.status,
    ), res


def cmd_touch_add(args) -> int:
    """Add touch edges to an already-booked receipt.

    Receipts are append-only (WAGER-13a), so this books a follow-on receipt carrying the
    new edges and naming the one it extends, rather than editing a committed file.
    """
    idx = _index(args)
    touches = []
    for spec in args.touch:
        t, err = _parse_touch(idx, spec, args.allow_unresolved)
        if t is None:
            _emit({"refused": "UNRESOLVABLE_TOUCH", "spec": spec, "resolution": err.to_dict()})
            return _status_exit(err.status)
        touches.append(t)

    lineage, res = _resolve_produced_by(idx, args.produced_by, args.allow_unresolved)
    if lineage is None:
        _emit({"refused": "UNRESOLVABLE_PRODUCER", "resolution": res.to_dict()})
        return _status_exit(res.status)
    lineage.approval_ref = args.approval_ref
    lineage.actor_type = args.actor_type
    lineage.work_item_id = args.work_item_id

    receipt = Receipt(
        department=args.department,
        date=args.date,
        slug=args.slug,
        title=f"Touch edges for {args.subject_id}",
        moment="result-produced",
        summary=f"Component-touch edges for {args.subject_type} {args.subject_id}.",
        what_happened=(
            f"Appends {len(touches)} component-touch edge(s) for "
            f"`{args.subject_type}` `{args.subject_id}`. Receipts are append-only "
            f"(WAGER-13a), so this is a new record rather than an edit to the subject."
        ),
        touches=touches,
        lineage=lineage,
        # The declared subject now reaches the machine-readable block, not only the prose.
        # Without these two lines every edge names this receipt as its own subject.
        subject_type=args.subject_type,
        subject_id=args.subject_id,
    )
    repo = _target_repo(args)
    try:
        result, path = book(repo, receipt, expect_branch=args.branch)
    except PromotionRefused as e:
        _emit({"refused": e.code, "message": e.message})
        print(f"touch add refused [{e.code}]: {e.message}", file=sys.stderr)
        return EXIT_REFUSED
    _emit({"booked": True, "touches": len(touches), "path": str(path.relative_to(repo)),
           **result.to_dict()})
    _report_durability(result)
    return EXIT_OK


# ---- lineage project ------------------------------------------------------------

def cmd_lineage_project(args) -> int:
    """Load one committed promotion into the store. git -> store, and never the reverse.

    The import is inside the function and that is load-bearing rather than tidy.
    `store_projection` imports `store`, which imports psycopg2; every other subcommand here is
    required to keep working with Postgres stopped, because "losing the store costs zero
    knowledge" is expressed in this lane as an import graph rather than as a promise
    (`store_projection.py`, and `store_join.py` imports no driver at all). A module-level import
    would make `entity resolve` need a database.
    """
    from .store_projection import project

    repo = _target_repo(args)
    try:
        res = project(repo, args.sha, actor=args.actor, work_item_id=args.work_item_id,
                      allow_undeclared_repo=args.allow_undeclared_repo)
    except PromotionRefused as e:
        _emit({"refused": e.code, "message": e.message})
        print(f"lineage project refused [{e.code}]: {e.message}", file=sys.stderr)
        return EXIT_REFUSED
    except Exception as e:                                            # noqa: BLE001
        # A store that is down is not a refusal and must not read as one: a refusal means the
        # commit was examined and rejected, and reporting "refused" for "nothing looked at it"
        # is the confidently-wrong shape this whole runtime exists to avoid. Everything typed
        # is caught above; this is the connection, the missing driver, and the schema.
        _emit({"projected": False, "store_error": e.__class__.__name__, "message": str(e)})
        print(f"lineage project: the store did not answer ({e.__class__.__name__}). NOTHING was "
              f"projected and the commit is untouched -- git holds the promotion either way, so "
              f"re-run this when Postgres is up. The projection is idempotent by construction.",
              file=sys.stderr)
        return EXIT_STORE

    receipt = res.get("receipt") or {}
    _emit({
        "projected": not res["already_projected"],
        "already_projected": res["already_projected"],
        # The RESOLVED sha off the row, never the string that was typed. `read_promotion` resolves
        # `HEAD` or a branch against git before the transition sees it -- which is correct, git
        # does the resolving -- so echoing the argument back would report `git_ref: HEAD` for a
        # row that immutably names a commit, and the one field a reader would use to reconcile
        # the store against git would be the one field that cannot be.
        "git_ref": receipt.get("git_ref"),
        "receipt_row_id": receipt.get("id"),
        "action": receipt.get("action"),
        "subject_type": receipt.get("subject_type"),
        "subject_id": receipt.get("subject_id"),
        "touches_inserted": res["touches_inserted"],
        "touches_already_present": res["touches_already_present"],
    })
    if res["already_projected"]:
        print(f"lineage project: {args.sha[:12]} was already projected; nothing was written. "
              f"Re-projecting is a no-op by design, so this is a success, not a collision.",
              file=sys.stderr)
    return EXIT_OK


def _read_maybe(v: str | None) -> str:
    if not v:
        return ""
    p = Path(v)
    if p.exists() and p.is_file():
        return p.read_text(encoding="utf-8")
    return v


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="brain-adapter", description=__doc__)
    ap.add_argument("--brain", help="brain repo root (default $BRAIN_ROOT)")
    ap.add_argument("--no-cache", action="store_true")
    sub = ap.add_subparsers(dest="group", required=True)

    ent = sub.add_parser("entity").add_subparsers(dest="verb", required=True)
    r = ent.add_parser("resolve", help="turn a reference into a stable id, or back")
    r.add_argument("ref", help="an id, path, [[wikilink]], alias or filename; '-' with --audit")
    r.add_argument("--reverse", action="store_true", help="id -> current path")
    r.add_argument("--audit", action="store_true", help="reconcile stored ids; reports, never rejects")
    r.set_defaults(func=cmd_entity_resolve)

    b = sub.add_parser("brief").add_subparsers(dest="verb", required=True)
    br = b.add_parser("render", help="assemble the brief a fresh session receives")
    br.add_argument("--work-item", help="path to a work-item JSON file (default stdin)")
    br.add_argument("--namespace", help="skip routing and use this namespace")
    br.add_argument("--budget", type=int, default=DEFAULT_BUDGET_TOKENS)
    br.add_argument("--out")
    br.add_argument("--stats-only", action="store_true")
    br.set_defaults(func=cmd_brief_render)

    rec = sub.add_parser("receipt").add_subparsers(dest="verb", required=True)
    bk = rec.add_parser("book", help="the promotion event: runtime state becomes a git commit")
    for p in (bk,):
        p.add_argument("--repo", help="target repo or worktree (default the brain)")
        p.add_argument("--branch", help="branch this must be on; refuses main/master always")
        p.add_argument("--department", required=True)
        p.add_argument("--date", required=True, help="YYYY-MM-DD")
        p.add_argument("--slug", required=True)
        p.add_argument("--title", required=True)
        p.add_argument("--summary", required=True)
        p.add_argument("--moment", required=True, help="a WAGER-14a booking moment")
        p.add_argument("--body", help="text, or a path to a file")
        p.add_argument("--produced-by", help="the producing entity reference")
        p.add_argument("--caused-by-event-id", default=None)
        p.add_argument("--approval-ref", default=None)
        p.add_argument("--actor-type", choices=["human", "ai", "hybrid"], default=None)
        p.add_argument("--session-id", default=None)
        p.add_argument("--work-item-id", default=None)
        p.add_argument("--canonical-task", default=None)
        p.add_argument("--artifact", action="append")
        p.add_argument("--touch", action="append",
                       help="<ref>:<component_type>:<orient_role>[:<role>]")
        p.add_argument("--external", action="store_true")
        p.add_argument("--canon-touching", action="store_true")
        p.add_argument("--promotion-kind", default="closeout-to-planning-truth")
        p.add_argument("--allow-unresolved", action="store_true",
                       help="book with produced_by=null and the raw ref recorded visibly")
    bk.set_defaults(func=cmd_receipt_book)

    t = sub.add_parser("touch").add_subparsers(dest="verb", required=True)
    ta = t.add_parser("add", help="append component-touch edges as a new append-only receipt")
    ta.add_argument("--repo")
    ta.add_argument("--branch")
    ta.add_argument("--department", required=True)
    ta.add_argument("--date", required=True)
    ta.add_argument("--slug", required=True)
    ta.add_argument("--subject-type", required=True, help="receipt | disposition | wager")
    ta.add_argument("--subject-id", required=True)
    ta.add_argument("--touch", action="append", required=True,
                    help="<ref>:<component_type>:<orient_role>[:<role>]")
    ta.add_argument("--produced-by")
    ta.add_argument("--approval-ref", default=None)
    ta.add_argument("--actor-type", choices=["human", "ai", "hybrid"], default=None)
    ta.add_argument("--work-item-id", default=None)
    ta.add_argument("--allow-unresolved", action="store_true")
    ta.set_defaults(func=cmd_touch_add)

    lin = sub.add_parser("lineage").add_subparsers(dest="verb", required=True)
    lp = lin.add_parser("project", help="load a committed receipt into the store. git -> store, "
                                        "never the reverse. The one subcommand needing Postgres")
    lp.add_argument("sha", help="the FULL commit sha the promotion is in. Not a branch, not a "
                                "tag, not HEAD: the transition refuses anything that moves")
    lp.add_argument("--repo", help="repo or worktree holding that commit (default the brain)")
    lp.add_argument("--actor", default="adapter",
                    help="who ran the projection; lands in the receipt's booked_by. It defaults "
                         "to `adapter` rather than to a person: the loader is what loaded it, "
                         "and a receipt that names a human who did not type this is a lie in "
                         "the one column that says who")
    lp.add_argument("--work-item-id", default=None,
                    help="task to note the projection on. Defaults to the one the receipt names")
    lp.add_argument("--allow-undeclared-repo", action="store_true",
                    help="project from a repository policy/durable-refs.json does not declare. "
                         "For fixtures proving this door. The row will outlive the commit it "
                         "names, which is what the 26 rows booked before 2026-08-17 all did")
    lp.set_defaults(func=cmd_lineage_project)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except PromotionRefused as e:
        print(f"refused [{e.code}]: {e.message}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
