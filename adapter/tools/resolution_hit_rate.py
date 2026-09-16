#!/usr/bin/env python3
"""Measure resolution against the real brain corpus, with the denominator stated.

"Hit rate" is meaningless without saying hit rate *of what*, so this measures three
different populations and reports each separately rather than blending them into one
flattering number:

  A. round trip over every id-bearing node. `resolve(id)` must return that node and
     `reverse(id)` must return its path. This is the measure `produced_by` depends on.
  B. real `[[wikilinks]]` as the corpus actually writes them. This is the realistic
     input a lane will hand to `entity resolve`, and it is the honest headline.
  C. bare filename lookups. The weakest reference form, measured so nobody assumes it.

Population B is capped by `--sample` because the corpus holds tens of thousands of
wikilinks; the cap is reported alongside the result, never left implicit.

Usage: python3 tools/resolution_hit_rate.py [--brain PATH] [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain_adapter import config  # noqa: E402
from brain_adapter.index import EntityIndex  # noqa: E402

WIKILINK = re.compile(r"\[\[([^\]\n]+?)\]\]")


def namespace_of(path: str) -> str:
    parts = path.split("/")
    if parts[0] == "knowledge" and len(parts) > 2:
        return f"knowledge/{parts[1]}"
    return parts[0]


def pop_a(idx: EntityIndex) -> dict:
    nodes = [n for n in idx.nodes if n.entity_id]
    hits = misses = wrong_path = 0
    by_ns_hits: Counter = Counter()
    by_ns_total: Counter = Counter()
    failures = []
    for n in nodes:
        ns = namespace_of(n.path)
        by_ns_total[ns] += 1
        fwd = idx.resolve(n.entity_id)
        rev = idx.reverse(n.entity_id)
        if fwd.ok and rev.ok and fwd.path == rev.path:
            hits += 1
            by_ns_hits[ns] += 1
            if rev.path != n.path:
                wrong_path += 1  # a duplicate id: resolved, but to the other home
        else:
            misses += 1
            if len(failures) < 20:
                failures.append({"id": n.entity_id, "path": n.path, "status": fwd.status})
    return {
        "denominator": len(nodes),
        "resolved": hits,
        "failed": misses,
        "hit_rate_pct": round(100.0 * hits / len(nodes), 2) if nodes else None,
        "resolved_to_a_different_home_duplicate_id": wrong_path,
        "namespaces_covered": len(by_ns_total),
        "worst_namespaces": sorted(
            ((ns, by_ns_hits[ns], by_ns_total[ns]) for ns in by_ns_total
             if by_ns_hits[ns] < by_ns_total[ns]),
            key=lambda t: (t[1] / t[2], -t[2]),
        )[:10],
        "sample_failures": failures,
    }


def pop_b(idx: EntityIndex, sample: int, seed: int) -> dict:
    """Every distinct wikilink target in the corpus, sampled."""
    targets: dict[str, str] = {}  # target -> a file that writes it
    for n in idx.nodes:
        try:
            text = (idx.brain_root / n.path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in WIKILINK.findall(text):
            t = m.split("|", 1)[0].split("#", 1)[0].strip()
            if t and t not in targets:
                targets[t] = n.path

    all_targets = sorted(targets)
    rng = random.Random(seed)
    chosen = all_targets if sample <= 0 or sample >= len(all_targets) else rng.sample(all_targets, sample)

    status = Counter()
    matched_by = Counter()
    by_ns_total: Counter = Counter()
    by_ns_hits: Counter = Counter()
    unresolved_examples, ambiguous_examples, no_id_targets = [], [], []
    for t in chosen:
        ns = namespace_of(targets[t])
        by_ns_total[ns] += 1
        res = idx.resolve(t)
        status[res.status] += 1
        if res.ok:
            by_ns_hits[ns] += 1
            matched_by[res.matched_by] += 1
        elif res.matched_by and res.matched_by.endswith("-no-id"):
            # A real file that carries no `id:` -- `_system/` rule files, `INDEX.md`,
            # `canon/agent-load-order.md` and friends are validator-exempt by design.
            # The link is not broken; the target simply is not an id-bearing entity.
            no_id_targets.append({"target": t, "path": res.path})
        elif res.status == "unresolved" and len(unresolved_examples) < 25:
            unresolved_examples.append({"target": t, "written_in": targets[t]})
        elif res.status == "ambiguous" and len(ambiguous_examples) < 15:
            ambiguous_examples.append({"target": t, "written_in": targets[t],
                                       "n_candidates": len(res.candidates)})
    n = len(chosen)
    return {
        "distinct_wikilink_targets_in_corpus": len(all_targets),
        "sampled": n,
        "sampling": "all" if n == len(all_targets) else f"random seed={seed}, cap={sample}",
        "denominator": n,
        "resolved": status["resolved"],
        "ambiguous": status["ambiguous"],
        "unresolved": status["unresolved"],
        "hit_rate_pct": round(100.0 * status["resolved"] / n, 2) if n else None,
        "namespaces_covered": len(by_ns_total),
        "matched_by": dict(matched_by),
        "target_is_a_real_file_carrying_no_id": len(no_id_targets),
        "genuinely_dangling": status["unresolved"] - len(no_id_targets),
        "hit_rate_pct_excluding_non_id_bearing_targets": (
            round(100.0 * status["resolved"] / (n - len(no_id_targets)), 2)
            if n - len(no_id_targets) else None),
        "sample_no_id_targets": no_id_targets[:15],
        "sample_unresolved": unresolved_examples,
        "sample_ambiguous": ambiguous_examples,
    }


def pop_c(idx: EntityIndex) -> dict:
    """Bare filename lookups: does `resolve(stem)` land on the node that owns it?"""
    nodes = [n for n in idx.nodes if n.entity_id]
    lands, elsewhere, ambiguous, unresolved = 0, 0, 0, 0
    for n in nodes:
        res = idx.resolve(n.stem)
        if res.ok and res.path == n.path:
            lands += 1
        elif res.ok:
            elsewhere += 1
        elif res.status == "ambiguous":
            ambiguous += 1
        else:
            unresolved += 1
    return {
        "denominator": len(nodes),
        "filename_lands_on_its_own_node": lands,
        "filename_resolves_to_a_DIFFERENT_node": elsewhere,
        "ambiguous": ambiguous,
        "unresolved": unresolved,
        "hit_rate_pct": round(100.0 * lands / len(nodes), 2) if nodes else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain")
    ap.add_argument("--json")
    ap.add_argument("--sample", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=20260816)
    args = ap.parse_args()

    brain = config.brain_root(args.brain)
    idx = EntityIndex.build(brain, config.cache_dir())

    report = {
        "brain": str(brain),
        "files_indexed": len(idx.nodes),
        "id_bearing_nodes": sum(1 for n in idx.nodes if n.entity_id),
        "duplicate_ids": len(idx.duplicate_ids),
        "population_a_id_round_trip": pop_a(idx),
        "population_b_real_wikilinks": pop_b(idx, args.sample, args.seed),
        "population_c_bare_filenames": pop_c(idx),
    }
    text = json.dumps(report, indent=2)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    print(json.dumps(
        {k: ({kk: vv for kk, vv in v.items() if not kk.startswith("sample")}
             if isinstance(v, dict) else v)
         for k, v in report.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
