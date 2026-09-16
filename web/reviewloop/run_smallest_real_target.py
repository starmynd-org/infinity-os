"""Run one declared review pass over the smallest real target found for this lane."""

from pathlib import Path

from web.reviewloop.core import GitRepository, ReviewRule, run_pass, write_json


def main() -> int:
    repository_root = Path(__file__).resolve().parents[2]
    output = repository_root / "web" / "reviewloop" / "evidence" / "SMALLEST-REAL-PASS.json"
    rule = ReviewRule(
        target_path="web/README.md",
        needle=(
            "| Queue | `answer` · `recommend accept` · `recommend reject` · `accept work` · "
            "`reopen` · `done` · `post` |"
        ),
        replacement=(
            "| Queue | Read the live verb set from `GET /api/audit`; this table is not an "
            "authority list. |"
        ),
        title="Repair the stale Queue allowlist documentation",
        why="The checked-in room table names fewer Queue verbs than the live allowlist.",
        rationale="A stale authority table can direct a future editor to preserve the wrong boundary.",
        novelty_queries=("`web/README.md`'s room table is wrong for 2 of 5 rooms.",),
    )
    invocation = (
        f"PYTHONDONTWRITEBYTECODE=1 PYTHONPATH={repository_root.as_posix()} python "
        f"{(repository_root / 'web' / 'reviewloop' / 'run_smallest_real_target.py').as_posix()}"
    )
    report = run_pass(
        GitRepository(repository_root),
        base_ref="HEAD",
        workspace="ws-infinity-runtime",
        producer_actor="reviewloop:term-10",
        rules=(rule,),
        invocation=invocation,
    )
    write_json(output, report)
    counts = report["counts"]
    print(f"DENOMINATOR review targets: {report['measurement']['denominator']['review_targets']}")
    print(
        "RESULT reviewed {reviewed}, candidates {candidates}, proposed {proposed}, "
        "suppressed {suppressed_existing_answer}, refused {refused}".format(**counts)
    )
    print(
        "NON-AUTHOR CONFIRMATION: "
        f"{report['confirmation']['confirmed_by_non_author']}/"
        f"{report['confirmation']['proposal_denominator']} "
        f"({report['confirmation']['fraction']})"
    )
    print(
        "FALSE-POSITIVE RATE: declined "
        f"{report['false_positive_rate']['declined']}/"
        f"{report['false_positive_rate']['decided_proposal_denominator']} decided "
        f"({report['false_positive_rate']['rate']})"
    )
    print(f"STOP: {str(report['stopping_condition']['stop']).upper()}")
    print(f"EVIDENCE: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
