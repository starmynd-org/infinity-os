#!/usr/bin/env python3
"""`durability_tier`: the word `mirrored`, earned by ASKING THE REMOTE and never by a push exit.

`policy/durable-refs.json` names three tiers and the census reported none of them. Axis 1 could
say DURABLE -- "this sha is an ancestor of a ref that still exists here" -- and that sentence is
true of a commit that has never left this laptop. This module adds the missing word.

THE ONE RULE. `mirrored` is granted only from evidence produced by `git ls-remote`, a question
asked OF THE REMOTE in this run. Three things that look like evidence and are not:

  a `git push` that exited 0      exit 0 says the transport did not error. Task 0357 measured a
                                  push that would have exited 0 having moved 0 of the 20 refs it
                                  claimed to move, because `--follow-tags` cannot carry a
                                  lightweight tag.
  a remote-TRACKING ref           `origin/main` is a file in .git on THIS disk. It proves a fetch
                                  succeeded at some past moment, not that the object is on the
                                  remote now, and it survives the remote being deleted. Note that
                                  `policy/durable-refs.json` defines `mirrored` as "an ancestor of
                                  a remote-tracking ref"; read literally that definition is
                                  satisfiable with the network unplugged, so this module does NOT
                                  implement it literally. Refs under `refs/remotes/` are ignored
                                  as evidence here, deliberately.
  the manifest saying so          a JSON file that declares 20 salvaged commits is a claim about
                                  the past, not a measurement of a remote.

WHAT IS ASSERTED, because a verdict without a denominator is how v1's restore verifier printed
RESTORE VERIFIED after comparing one table of twenty: every result carries
`remote_refs_advertised` (how many refs the remote named), `shas_compared` (how many we tested
against them) and per-tier counts. A caller that prints the verdict without the counts is
printing less than it measured.

EVIDENCE GRADES for `mirrored`, recorded per sha rather than flattened:

  exact-ref            the remote advertises a ref pointing AT this sha. The object is there.
  ancestor-of-remote   the remote advertises ref R at sha X, and locally sha is an ancestor of X.
                       Weaker: it assumes the remote's history behind X matches ours, which is
                       true of a connected repository and is still an assumption. Recorded under
                       its own name so it is never silently read as `exact-ref`.

READ-ONLY. `ls-remote`, `rev-parse`, `merge-base`, `for-each-ref`. Nothing here writes, pushes or
fetches.

    python3 -m store.durability_tier --repo REPO --remote NAME_OR_URL [--from-manifest FILE]
                                     [--sha SHA]... [--json]

Exit 0 when the measurement was produced, 3 when it could not be (the remote refused to answer).
It is a MEASUREMENT, not a gate.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

#: Worst to best. `canon` is above `mirrored` because it is `mirrored` plus being what a fresh
#: clone checks out; both require remote evidence here.
NONE, LEDGER, MIRRORED, CANON = "none", "ledger", "mirrored", "canon"
_RANK = {NONE: 0, LEDGER: 1, MIRRORED: 2, CANON: 3}

#: Ref prefixes on the remote that grant `canon` when a sha is reachable from one.
CANON_REMOTE_REFS = ("refs/heads/main", "refs/heads/master")


class RemoteUnreachable(RuntimeError):
    """`git ls-remote` did not answer. NOT a verdict of `none`: it is an absence of measurement."""


def git(repo, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False)


def ls_remote(repo: Path, remote: str, timeout: int = 60) -> dict[str, str]:
    """Every ref the remote advertises, asked once. refname -> sha.

    One question per run on purpose: twenty questions to a remote that answers the first and
    times out on the rest would report nineteen refs missing, which is a lie about the remote
    dressed as a durability finding.
    """
    p = subprocess.run(["git", "-C", str(repo), "ls-remote", remote],
                       capture_output=True, text=True, check=False, timeout=timeout)
    if p.returncode != 0:
        raise RemoteUnreachable(
            f"git ls-remote {remote} exited {p.returncode}: "
            f"{(p.stderr or '').strip().splitlines()[-1] if p.stderr.strip() else 'no stderr'}")
    out: dict[str, str] = {}
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and not parts[1].endswith("^{}"):
            out[parts[1]] = parts[0]
    return out


def local_ledger_refs(repo: Path, patterns: list[str]) -> list[str]:
    """Policy's ledger refs, expanded against disk, minus anything under refs/remotes/.

    A pattern ending in `*` is expanded here rather than by the shell, same as the census.
    """
    out: list[str] = []
    for pat in patterns:
        if pat.endswith("*"):
            out += [ln.strip() for ln in
                    git(repo, "for-each-ref", "--format=%(refname)", pat).stdout.splitlines()
                    if ln.strip()]
        elif git(repo, "rev-parse", "--verify", "--quiet", f"{pat}^{{commit}}").returncode == 0:
            out.append(pat)
    return [r for r in out if not r.startswith("refs/remotes/")]


def tier_of(repo: Path, sha: str, advertised: dict[str, str], ledger_refs: list[str]) -> dict:
    """One sha's tier, with the evidence that produced it named."""
    held: list[str] = []
    remote_ref = None
    evidence = None

    exact = sorted(r for r, s in advertised.items() if s == sha)
    if exact:
        held.append(MIRRORED)
        remote_ref, evidence = exact[0], "exact-ref"
    else:
        for ref, tip in sorted(advertised.items()):
            if git(repo, "cat-file", "-e", f"{tip}^{{commit}}").returncode != 0:
                continue  # the remote's tip is not on this disk, so ancestry is not computable
            if git(repo, "merge-base", "--is-ancestor", sha, tip).returncode == 0:
                held.append(MIRRORED)
                remote_ref, evidence = ref, "ancestor-of-remote"
                break

    if remote_ref is not None:
        tip = advertised[remote_ref]
        for canon in CANON_REMOTE_REFS:
            if canon in advertised and (
                advertised[canon] == sha
                or (git(repo, "cat-file", "-e", f"{advertised[canon]}^{{commit}}").returncode == 0
                    and git(repo, "merge-base", "--is-ancestor",
                            sha, advertised[canon]).returncode == 0)):
                held.append(CANON)
                break
        del tip

    for ref in ledger_refs:
        if git(repo, "merge-base", "--is-ancestor", sha, ref).returncode == 0:
            held.append(LEDGER)
            break

    best = max(held, key=lambda t: _RANK[t]) if held else NONE
    return {
        "git_ref": sha,
        "durability_tier": best,
        "tiers_held": sorted(set(held), key=lambda t: _RANK[t]),
        "remote_ref": remote_ref,
        "remote_evidence": evidence,
        "mirrored": MIRRORED in held,
    }


def measure(repo: Path, remote: str, shas: list[str], ledger_patterns: list[str],
            policy_provenance: dict | None = None) -> dict:
    """Tier every sha, and assert the counts rather than only the verdict."""
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    advertised = ls_remote(repo, remote)
    ledger_refs = local_ledger_refs(repo, ledger_patterns)
    rows = [tier_of(repo, s, advertised, ledger_refs) for s in shas]
    counts = {t: len([r for r in rows if r["durability_tier"] == t])
              for t in (CANON, MIRRORED, LEDGER, NONE)}
    # `by_tier` is the BEST tier per sha and the tiers are not exclusive: a sha that is both
    # mirrored and on the ledger appears once, under `mirrored`. Reporting only that hides the
    # ledger footing, so both are reported and neither is derivable from the other.
    held = {t: len([r for r in rows if t in r["tiers_held"]]) for t in (CANON, MIRRORED, LEDGER)}
    return {
        "measured_at": stamp,
        "repo": str(repo),
        "remote": remote,
        "remote_refs_advertised": len(advertised),
        "shas_compared": len(rows),
        "ledger_refs_used": ledger_refs,
        "policy": policy_provenance or {"source": "caller-supplied", "ledger_patterns": ledger_patterns},
        "by_tier": counts,
        "by_tier_held": held,
        "mirrored_of_compared": f"{counts[MIRRORED] + counts[CANON]} of {len(rows)}",
        "evidence_grades": {g: len([r for r in rows if r["remote_evidence"] == g])
                            for g in ("exact-ref", "ancestor-of-remote")},
        "rows": rows,
    }


def policy_ledger_refs(repo: Path, policy_path: Path) -> tuple[list[str], dict]:
    """The policy's ledger patterns for THIS repo, plus a record of how the entry was chosen.

    `policy/durable-refs.json` is keyed by repo identity -- the sorted root commits of HEAD -- and
    falls back to `default` for a repository it has never heard of. That fallback is not a
    detail: `default.ledger_refs` is `["receipts/durable"]` and does NOT carry
    `refs/tags/receipts/salvage/*`, so a repo whose identity fails to compute is measured against
    a strictly narrower rule and scores every salvage commit as not-on-the-ledger. It cost this
    module a wrong answer in its own test suite: a fixture repo with an UNBORN HEAD returns
    nothing from `rev-list --max-parents=0 HEAD`, the identity came back empty, and the fallback
    fired silently. So the branch taken is returned beside the answer, and a caller that prints
    the tier without it is printing an answer whose rule it did not check.
    """
    try:
        doc = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], {"source": str(policy_path), "readable": False,
                    "error": f"{exc.__class__.__name__}", "entry_matched": None,
                    "ledger_patterns": []}
    p = git(repo, "rev-list", "--max-parents=0", "HEAD")
    roots = sorted({ln.strip() for ln in p.stdout.splitlines() if ln.strip()})
    identity = ",".join(roots) or None
    named = (doc.get("repos") or {}).get(identity or "")
    entry = named if named is not None else (doc.get("default") or {})
    return list(entry.get("ledger_refs") or []), {
        "source": str(policy_path),
        "readable": True,
        "repo_identity": identity,
        "repo_identity_computable": bool(identity),
        "entry_matched": "repos[%s]" % identity if named is not None else "default",
        "fell_back_to_default": named is None,
        "entry_name": (named or {}).get("name") if named else None,
        "ledger_patterns": list(entry.get("ledger_refs") or []),
    }


def manifest_vs_namespace(repo: Path, manifest: dict, pattern: str) -> dict:
    """What the manifest declares versus what the tag namespace actually holds.

    Neither side is authoritative and this function does not pick one. It reports both
    denominators, because "20 of 20 mirrored" and "20 of 23 mirrored" are both true sentences
    about the same push and only one of them is the sentence a reader wants.
    """
    declared = {s["pinned_by_tag"]: s["git_ref"] for s in (manifest.get("salvaged") or [])}
    on_disk = {ln.strip(): git(repo, "rev-parse", f"{ln.strip()}^{{commit}}").stdout.strip()
               for ln in git(repo, "for-each-ref", "--format=%(refname)", pattern).stdout.splitlines()
               if ln.strip()}
    missing = sorted(set(declared) - set(on_disk))
    undeclared = sorted(set(on_disk) - set(declared))
    drifted = sorted(r for r in set(declared) & set(on_disk) if declared[r] != on_disk[r])
    return {
        "manifest_count_field": manifest.get("count"),
        "manifest_entries": len(declared),
        "namespace_pattern": pattern,
        "namespace_refs": len(on_disk),
        "declared_but_absent_from_namespace": missing,
        "in_namespace_but_undeclared": undeclared,
        "declared_sha_differs_from_tag": drifted,
        "agree": not (missing or undeclared or drifted)
                 and manifest.get("count") == len(declared) == len(on_disk),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--remote", required=True, help="remote name or URL, asked with ls-remote")
    ap.add_argument("--sha", action="append", default=[], help="a sha to tier (repeatable)")
    ap.add_argument("--from-manifest", metavar="FILE",
                    help="tier every git_ref in a salvage-manifest.json")
    ap.add_argument("--ledger-ref", action="append", default=None,
                    help="local ledger ref or pattern (repeatable); default from the policy file")
    ap.add_argument("--policy", default=None, help="path to policy/durable-refs.json")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    shas = list(args.sha)
    manifest = None
    if args.from_manifest:
        manifest = json.loads(Path(args.from_manifest).read_text(encoding="utf-8"))
        shas += [s["git_ref"] for s in (manifest.get("salvaged") or [])]
    if not shas:
        print("durability-tier: nothing to measure (--sha or --from-manifest)", file=sys.stderr)
        return 3

    patterns = args.ledger_ref
    prov: dict = {"source": "--ledger-ref on the command line", "ledger_patterns": patterns}
    if patterns is None:
        pol = Path(args.policy) if args.policy else \
            Path(__file__).resolve().parents[1] / "policy" / "durable-refs.json"
        patterns, prov = policy_ledger_refs(repo, pol)

    try:
        result = measure(repo, args.remote, sorted(dict.fromkeys(shas)), patterns, prov)
    except RemoteUnreachable as exc:
        print(f"durability-tier: {exc}", file=sys.stderr)
        print("durability-tier: NOT measured. No sha is downgraded to 'none' on this path -- "
              "an unanswered remote is an absent measurement, not a verdict.", file=sys.stderr)
        return 3

    if manifest is not None:
        result["manifest_vs_namespace"] = manifest_vs_namespace(
            repo, manifest, "refs/tags/receipts/salvage/*")

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"durability tier  {result['measured_at']}")
    print(f"  repo                    {result['repo']}")
    print(f"  remote                  {result['remote']}")
    pol = result.get("policy") or {}
    print(f"  policy entry            {pol.get('entry_matched')}"
          f"{'   <-- FELL BACK TO default' if pol.get('fell_back_to_default') else ''}")
    print(f"  ledger refs used        {len(result['ledger_refs_used'])}")
    print(f"  refs the remote named   {result['remote_refs_advertised']}")
    print(f"  shas compared to them   {result['shas_compared']}")
    for t in (CANON, MIRRORED, LEDGER, NONE):
        held = result["by_tier_held"].get(t)
        print(f"    {t:<10} {result['by_tier'][t]:>3}"
              + (f"   (held by {held} counting overlap)" if held is not None else ""))
    print(f"  evidence                {result['evidence_grades']}")
    mvn = result.get("manifest_vs_namespace")
    if mvn:
        print(f"  manifest {mvn['manifest_entries']} entries (count field "
              f"{mvn['manifest_count_field']}) vs namespace {mvn['namespace_refs']} refs: "
              f"{'AGREE' if mvn['agree'] else 'DISAGREE'}")
        for k in ("declared_but_absent_from_namespace", "in_namespace_but_undeclared",
                  "declared_sha_differs_from_tag"):
            for r in mvn[k]:
                print(f"    {k}: {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
