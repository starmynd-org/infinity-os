"""Measure both frozen views of Andrew's estate without reading either worktree."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from web.importer.brain import parse_brain_tree
    from web.importer.catalog import parse_catalog
    from web.importer.git_source import GitTree
else:
    from .brain import parse_brain_tree
    from .catalog import parse_catalog
    from .git_source import GitTree


CATALOG_FILES = (
    "docs/architecture/components.tsv",
    "docs/architecture/members.tsv",
    "docs/wiki/graphs.json",
)


def _loss_counts(outcomes: list[Any]) -> dict[str, int]:
    counts = Counter(loss.code for outcome in outcomes for loss in outcome.losses)
    return dict(sorted(counts.items()))


def _refusals(outcomes: list[Any]) -> list[dict[str, str]]:
    return [
        outcome.refusal.as_dict()
        for outcome in outcomes
        if outcome.refusal is not None
    ]


def build_report(
    *,
    catalog_repo: Path,
    catalog_revision: str,
    brain_repo: Path,
    brain_revision: str,
    invocation: str,
) -> dict[str, Any]:
    catalog_tree = GitTree(catalog_repo, catalog_revision)
    components, members, graphs = (catalog_tree.read(path) for path in CATALOG_FILES)
    catalog = parse_catalog(
        components_data=components,
        members_data=members,
        graphs_data=graphs,
        source_revision=catalog_tree.revision,
    )
    brain_tree = GitTree(brain_repo, brain_revision)
    brain = parse_brain_tree(brain_tree)
    return {
        "contract_version": "ESTATE/1.0",
        "measurement_rule": (
            "Each measurement prints its denominator before outcome counts. The two views overlap "
            "and are not added into a false unique-estate total."
        ),
        "measurements": [
            {
                "name": "architecture-catalog/1.0",
                "denominator": catalog.coverage.denominator,
                "counts": catalog.coverage.counts,
                "predicate": catalog.coverage.predicate,
                "denominator_member_predicate": catalog.member_predicate,
                "control_fired": catalog.coverage.control_fired,
                "runtime_component_types": catalog.runtime_component_types,
                "runtime_members": catalog.runtime_members,
                "brain_graphs": catalog.brain_graphs,
                "loss_codes": _loss_counts(catalog.outcomes),
                "refusals": _refusals(catalog.outcomes),
            },
            {
                "name": "brain-directory/1.0",
                "denominator": brain.coverage.denominator,
                "counts": brain.coverage.counts,
                "predicate": brain.coverage.predicate,
                "denominator_member_predicate": brain.member_predicate,
                "control_fired": brain.coverage.control_fired,
                "tracked_blobs_examined": brain.tracked_blobs_examined,
                "portable_entity_types": brain.entity_types,
                "loss_codes": _loss_counts(brain.outcomes),
                "refusals": _refusals(brain.outcomes),
            },
        ],
        "sources": {
            "catalog": {
                "repo": str(catalog_repo.resolve()),
                "revision": catalog_tree.revision,
                "file_sha256": catalog.source_digests,
            },
            "brain": {
                "repo": str(brain_repo.resolve()),
                "revision": brain_tree.revision,
                "inventory_sha256": brain.inventory_sha256,
            },
        },
        "invocation": invocation,
        "oauth_accounts_accessed": 0,
        "provider_stores_accessed": 0,
        "activation_attempted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-repo", type=Path, required=True)
    parser.add_argument("--catalog-revision", required=True)
    parser.add_argument("--brain-repo", type=Path, required=True)
    parser.add_argument("--brain-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    invocation = (
        "python web/importer/measure_andrew_estate.py "
        f"--catalog-repo {args.catalog_repo.as_posix()} --catalog-revision {args.catalog_revision} "
        f"--brain-repo {args.brain_repo.as_posix()} --brain-revision {args.brain_revision} "
        f"--output {args.output.as_posix()}"
    )
    report = build_report(
        catalog_repo=args.catalog_repo,
        catalog_revision=args.catalog_revision,
        brain_repo=args.brain_repo,
        brain_revision=args.brain_revision,
        invocation=invocation,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for measurement in report["measurements"]:
        print(f"{measurement['name']} denominator: {measurement['denominator']}")
        print(json.dumps(measurement["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
