#!/usr/bin/env python3
"""Tests for the three verbs. Stdlib unittest, no test runner to install.

The refusal tests matter more than the happy paths. Every one of them is a rule from
`D00-shared-context.md` or the brain's `_system/` that a convenient implementation would
quietly violate:

- a promotion never lands on the brain's trunk
- canon is never written without a human approval record
- an unresolvable entity produces a NULL and a loud status, never a plausible id
- a commit never sweeps a file the promotion did not write
- a generated file is raised, never patched
- a receipt is append-only

Run: BRAIN_ROOT=<a scratch worktree> python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain_adapter import config  # noqa: E402
from brain_adapter.durability import (  # noqa: E402
    ENV_POLICY, durability_of, policy_for, repo_identity,
)
from brain_adapter.index import STATUS_AMBIGUOUS, STATUS_UNRESOLVED, EntityIndex  # noqa: E402
from brain_adapter.promotion import (  # noqa: E402
    Lineage, Promotion, PromotionRefused, promote,
)
from brain_adapter.receipt import Receipt, Touch, book

BRAIN = config.brain_root()


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True).stdout


class TempRepo:
    """A throwaway git repo shaped enough like the brain to exercise the guards."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        _git(self.path, "init", "-q", "-b", "main")
        subprocess.run(["git", "-C", str(self.path), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(self.path), "config", "user.name", "t"], check=True)
        (self.path / "departments" / "sandbox" / "receipts").mkdir(parents=True)
        (self.path / "knowledge" / "x" / "canon").mkdir(parents=True)
        (self.path / "knowledge" / "example-department" / "metrics").mkdir(parents=True)
        for f in ("departments/sandbox/receipts/README.md",
                  "knowledge/x/canon/core-doctrine.md",
                  "knowledge/example-department/metrics/roas.md",
                  "other-lane-file.md"):
            p = self.path / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("seed\n", encoding="utf-8")
        _git(self.path, "add", "-A")
        _git(self.path, "commit", "-qm", "seed")
        self._declare()
        return self

    def _declare(self):
        """Name THIS fixture in its own policy file, keyed by the root commit it actually has.

        A fixture earns the right to book the same way the brain did: by being declared, in data.
        `default.book_refs` is empty in the shipped policy and empty here, so a repository the
        declaration has never heard of cannot book at all -- which is what stops a `mkdtemp` repo
        from naming its branch `receipts/durable` and being believed. The policy is written
        OUTSIDE the repo so it never shows up as an untracked file in a test that asserts a
        refused booking left the tree alone.
        """
        self._poldir = tempfile.TemporaryDirectory()
        self._policy = Path(self._poldir.name) / "durable-refs.json"
        self._policy.write_text(json.dumps({
            "default": {"canon_refs": ["main", "origin/main"],
                        "ledger_refs": ["receipts/durable"], "book_refs": []},
            "repos": {repo_identity(self.path): {
                "name": "TempRepo fixture",
                "canon_refs": ["main", "origin/main"],
                "ledger_refs": ["receipts/durable"],
                "book_refs": ["receipts/durable"]}},
        }), encoding="utf-8")
        self._envpatch = mock.patch.dict(os.environ, {ENV_POLICY: str(self._policy)})
        self._envpatch.start()

    def __exit__(self, *a):
        self._envpatch.stop()
        self._poldir.cleanup()
        self.tmp.cleanup()

    def branch(self, name):
        _git(self.path, "checkout", "-q", "-b", name)
        return self

    def ledger(self):
        """Check out the ref `policy/durable-refs.json` declares bookable, by asking the policy.

        The name is READ from the declaration rather than spelled here. A test that hard-codes
        `receipts/durable` still passes if someone renames the ledger in policy and forgets a
        call site, which is precisely the drift the single declaration exists to prevent.
        """
        return self.branch(policy_for(self.path).book_refs[0])


def _lineage(**kw):
    base = dict(produced_by="agent-x", produced_by_ref="agent-x", resolution_status="resolved")
    base.update(kw)
    return Lineage(**base)


def _receipt(**kw):
    base = dict(
        department="sandbox", date="2026-08-16", slug="proof", title="Proof",
        moment="result-produced", summary="A proof receipt.", lineage=_lineage(),
    )
    base.update(kw)
    return Receipt(**base)


class TestPromotionGuards(unittest.TestCase):
    def test_refuses_the_trunk(self):
        with TempRepo() as r:
            with self.assertRaises(PromotionRefused) as cm:
                book(r.path, _receipt())
            self.assertEqual(cm.exception.code, "PROTECTED_BRANCH")

    def test_refuses_a_kind_that_does_not_cross_the_plane(self):
        with TempRepo() as r:
            r.ledger()
            with self.assertRaises(PromotionRefused) as cm:
                book(r.path, _receipt(promotion_kind="runtime-state-write"))
            self.assertEqual(cm.exception.code, "NOT_A_PROMOTION")

    def test_refuses_an_unknown_kind(self):
        with TempRepo() as r:
            r.ledger()
            with self.assertRaises(PromotionRefused) as cm:
                book(r.path, _receipt(promotion_kind="just-write-it"))
            self.assertEqual(cm.exception.code, "UNKNOWN_PROMOTION_KIND")

    def test_refuses_canon_without_an_approval_ref(self):
        with TempRepo() as r:
            r.ledger()
            (r.path / "knowledge/x/canon/core-doctrine.md").write_text("edited\n", encoding="utf-8")
            with self.assertRaises(PromotionRefused) as cm:
                promote(r.path, Promotion(
                    kind="draft-to-canon-candidate-to-canon",
                    paths=[r.path / "knowledge/x/canon/core-doctrine.md"],
                    message="m", lineage=_lineage()))
            self.assertEqual(cm.exception.code, "CANON_WITHOUT_APPROVAL")

    def test_allows_canon_with_an_approval_ref(self):
        with TempRepo() as r:
            r.ledger()
            (r.path / "knowledge/x/canon/core-doctrine.md").write_text("edited\n", encoding="utf-8")
            res = promote(r.path, Promotion(
                kind="draft-to-canon-candidate-to-canon",
                paths=[r.path / "knowledge/x/canon/core-doctrine.md"],
                message="m", lineage=_lineage(approval_ref="operator:2026-08-16")))
            self.assertEqual(res.files_in_commit, ["knowledge/x/canon/core-doctrine.md"])

    def test_refuses_a_generated_file(self):
        with TempRepo() as r:
            r.ledger()
            p = r.path / "knowledge/example-department/metrics/roas.md"
            p.write_text("hand edit\n", encoding="utf-8")
            with self.assertRaises(PromotionRefused) as cm:
                promote(r.path, Promotion(kind="run-to-memory", paths=[p], message="m",
                                          lineage=_lineage()))
            self.assertEqual(cm.exception.code, "GENERATED_FILE")

    def test_refuses_incoherent_lineage(self):
        with TempRepo() as r:
            r.ledger()
            with self.assertRaises(PromotionRefused) as cm:
                book(r.path, _receipt(lineage=Lineage(
                    produced_by=None, produced_by_ref="x", resolution_status="resolved")))
            self.assertEqual(cm.exception.code, "LINEAGE_INCOHERENT")

    def test_refuses_canon_touching_without_approval(self):
        with TempRepo() as r:
            r.ledger()
            with self.assertRaises(PromotionRefused) as cm:
                book(r.path, _receipt(canon_touching=True,
                                      moment="consequential-event-emitted"))
            self.assertEqual(cm.exception.code, "CANON_TOUCHING_WITHOUT_APPROVAL")

    def test_commit_does_not_sweep_another_lanes_dirty_file(self):
        """The brain tree carries other sessions' work. A promotion must not take it."""
        with TempRepo() as r:
            r.ledger()
            (r.path / "other-lane-file.md").write_text("someone else's edit\n", encoding="utf-8")
            res, _ = book(r.path, _receipt())
            self.assertEqual(res.files_in_commit,
                             ["departments/sandbox/receipts/2026-08-16-proof.md"])
            self.assertIn("other-lane-file.md", _git(r.path, "status", "--porcelain"))

    def test_a_refused_promotion_leaves_no_file_behind(self):
        with TempRepo() as r:
            with self.assertRaises(PromotionRefused):
                book(r.path, _receipt())  # on main: refused
            self.assertFalse((r.path / "departments/sandbox/receipts/2026-08-16-proof.md").exists())


class TestDurability(unittest.TestCase):
    """Task 0349. A receipt is not booked until its ref is durable, and this is the proof.

    `receipt book` used to write a commit to whatever branch it was pointed at, record the sha and
    return success. Nothing ever required that sha to become reachable from anything that outlives
    a proof: 0 of 26 stored git_refs were ancestors of a durable ref when 0297 measured it.
    """

    def test_refuses_a_scratch_branch(self):
        with TempRepo() as r:
            r.branch("scratch/x")
            with self.assertRaises(PromotionRefused) as cm:
                book(r.path, _receipt())
            self.assertEqual(cm.exception.code, "NOT_DURABLE")

    def test_refuses_a_detached_head(self):
        """The nastiest shape: a worktree at a sha, where the branch name is not a branch."""
        with TempRepo() as r:
            _git(r.path, "checkout", "-q", "--detach")
            with self.assertRaises(PromotionRefused) as cm:
                book(r.path, _receipt())
            self.assertEqual(cm.exception.code, "NOT_DURABLE")

    def test_a_refused_booking_leaves_no_commit_and_no_file(self):
        """Refused at write time, so there is nothing to find later and nothing to clean up."""
        with TempRepo() as r:
            r.branch("scratch/x")
            before = _git(r.path, "rev-parse", "HEAD").strip()
            with self.assertRaises(PromotionRefused):
                book(r.path, _receipt())
            self.assertEqual(_git(r.path, "rev-parse", "HEAD").strip(), before)
            self.assertFalse((r.path / "departments/sandbox/receipts/2026-08-16-proof.md").exists())

    def test_books_onto_the_declared_ledger_and_says_which_tier(self):
        with TempRepo() as r:
            r.ledger()
            res, _ = book(r.path, _receipt())
            self.assertEqual(res.durability_tier, "ledger")
            self.assertEqual(res.durable_ref, policy_for(r.path).book_refs[0])
            tier, ref = durability_of(r.path, res.sha)
            self.assertEqual((tier, ref), (res.durability_tier, res.durable_ref))

    def test_a_policy_that_names_a_protected_branch_is_refused(self):
        """A config file does not get to unlock main. The gate above it still refuses."""
        with TempRepo() as r:
            pol = r.path / "policy.json"
            pol.write_text(json.dumps({
                "default": {"canon_refs": ["main"], "ledger_refs": ["main"],
                            "book_refs": ["main"]}}), encoding="utf-8")
            r.branch("receipts/durable")
            with mock.patch.dict(os.environ, {"BRAIN_DURABLE_POLICY": str(pol)}):
                with self.assertRaises(PromotionRefused) as cm:
                    book(r.path, _receipt())
            self.assertEqual(cm.exception.code, "POLICY_REFUSED")

    def test_a_missing_policy_refuses_rather_than_defaulting(self):
        """"I could not read the declaration" must never become "everything is durable"."""
        with TempRepo() as r:
            r.branch("receipts/durable")
            with mock.patch.dict(os.environ,
                                 {"BRAIN_DURABLE_POLICY": str(r.path / "nope.json")}):
                with self.assertRaises(PromotionRefused) as cm:
                    book(r.path, _receipt())
            self.assertEqual(cm.exception.code, "NO_DURABILITY_POLICY")

    def test_a_throwaway_repo_cannot_book_by_naming_its_branch_like_the_ledger(self):
        """The defect this gate shipped with, kept reproduced.

        Found by hand against the real adapter on 2026-08-17: `mktemp -d`, `git init -b
        receipts/durable`, create the department's receipt home so the earlier NO_RECEIPT_HOME
        gate does not fire, and the booking returned exit 0 at durability_tier 'ledger' for a
        commit in /tmp. The branch name matched `book_refs`; nothing ever asked whether the
        REPOSITORY was one the declaration knew. Identity is the root commit, and this fixture
        has its own.
        """
        with TempRepo() as r:
            # Not declared: the shipped `default` block, whose book_refs are empty by rule.
            pol = r.path.parent / "undeclared-policy.json"
            pol.write_text(json.dumps({
                "default": {"canon_refs": ["main"], "ledger_refs": ["receipts/durable"],
                            "book_refs": []},
                "repos": {},
            }), encoding="utf-8")
            r.branch("receipts/durable")          # spelled exactly like the real ledger
            with mock.patch.dict(os.environ, {ENV_POLICY: str(pol)}):
                with self.assertRaises(PromotionRefused) as cm:
                    book(r.path, _receipt())
            self.assertEqual(cm.exception.code, "UNDECLARED_REPO")
            self.assertFalse((r.path / "departments/sandbox/receipts/2026-08-16-proof.md").exists())

    def test_the_shipped_default_grants_no_bookable_ref_to_an_unknown_repo(self):
        """Read the real file. The code gate and this emptiness are two locks, not one."""
        from brain_adapter.durability import load_policy
        self.assertEqual(load_policy()["default"].get("book_refs") or [], [],
                         "default.book_refs must stay empty: it applies to every repository the "
                         "declaration has never heard of, including any mkdtemp one")

    def test_the_shipped_policy_never_makes_a_protected_branch_bookable(self):
        """Read the real file, not a fixture: this is the one rule a bad edit could remove."""
        from brain_adapter.durability import load_policy
        from brain_adapter.promotion import PROTECTED_BRANCHES
        doc = load_policy()
        entries = [doc["default"], *(doc.get("repos") or {}).values()]
        for e in entries:
            self.assertFalse(set(e.get("book_refs") or []) & PROTECTED_BRANCHES,
                             f"{e.get('name', 'default')} names a protected branch as bookable")


class TestReceiptShape(unittest.TestCase):
    def test_refuses_a_moment_outside_wager_14a(self):
        with TempRepo() as r:
            r.ledger()
            with self.assertRaises(PromotionRefused) as cm:
                book(r.path, _receipt(moment="felt-like-it"))
            self.assertEqual(cm.exception.code, "BAD_BOOKING_MOMENT")

    def test_refuses_consequential_moment_without_a_hard_flag(self):
        with TempRepo() as r:
            r.ledger()
            with self.assertRaises(PromotionRefused) as cm:
                book(r.path, _receipt(moment="consequential-event-emitted"))
            self.assertEqual(cm.exception.code, "NOT_CONSEQUENTIAL")

    def test_receipts_are_append_only(self):
        with TempRepo() as r:
            r.ledger()
            book(r.path, _receipt())
            with self.assertRaises(PromotionRefused) as cm:
                book(r.path, _receipt())
            self.assertEqual(cm.exception.code, "RECEIPT_EXISTS")

    def test_refuses_a_missing_receipt_home(self):
        with TempRepo() as r:
            r.ledger()
            with self.assertRaises(PromotionRefused) as cm:
                book(r.path, _receipt(department="no-such-department"))
            self.assertEqual(cm.exception.code, "NO_RECEIPT_HOME")

    def test_unresolved_producer_is_visible_in_the_receipt_body(self):
        """The unresolvable-entity path: null, loud, and never a fabricated id."""
        with TempRepo() as r:
            r.ledger()
            _, path = book(r.path, _receipt(lineage=Lineage(
                produced_by=None, produced_by_ref="agent-that-does-not-exist",
                resolution_status="unresolved")))
            body = path.read_text(encoding="utf-8")
            self.assertIn("| `produced_by` | `null` |", body)
            self.assertIn("agent-that-does-not-exist", body)
            self.assertIn("did not resolve", body)
            msg = _git(r.path, "log", "-1", "--format=%B")
            self.assertIn("produced-by: null", msg)
            self.assertIn("produced-by-unresolved-ref: agent-that-does-not-exist", msg)

    def test_lineage_fields_are_present_even_when_null(self):
        """EF-5: null is a value, a missing key is a hidden breach."""
        with TempRepo() as r:
            r.ledger()
            _, path = book(r.path, _receipt())
            body = path.read_text(encoding="utf-8")
            self.assertIn("| `caused_by_event_id` | `null` |", body)
            self.assertIn("| `approval_ref` | `null` |", body)

    def test_flagged_event_without_approval_is_stated_in_the_receipt(self):
        with TempRepo() as r:
            r.ledger()
            _, path = book(r.path, _receipt(
                external=True, moment="consequential-event-emitted",
                lineage=_lineage(caused_by_event_id="evt-1", approval_ref="op:2026-08-16")))
            self.assertIn("evt-1", path.read_text(encoding="utf-8"))


class TestTouch(unittest.TestCase):
    def test_rejects_an_orient_role_outside_wager_2c(self):
        with self.assertRaises(PromotionRefused):
            Touch(entity_id="a", component_type="skill", orient_role="vibes").validate()

    def test_rejects_a_role_outside_the_enum(self):
        with self.assertRaises(PromotionRefused):
            Touch(entity_id="a", component_type="skill",
                  orient_role="tradition", role="sort-of").validate()

    def test_accepts_the_wager_2c_shape(self):
        t = Touch(entity_id="skill-x", component_type="skill",
                  orient_role="analysis-synthesis", role="load-bearing")
        t.validate()
        row = t.to_yaml_row("receipt", "receipt-1")
        self.assertIn('orient_role: "analysis-synthesis"', row)
        self.assertIn('role: "load-bearing"', row)


@unittest.skipUnless(BRAIN.exists(), "no brain at BRAIN_ROOT")
class TestResolutionAgainstTheRealBrain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.idx = EntityIndex.build(BRAIN, config.cache_dir())

    def test_resolves_a_known_id(self):
        r = self.idx.resolve("knowledge-ai-architecture-surface-boundary")
        self.assertTrue(r.ok)
        self.assertEqual(r.path, "knowledge/ai-architecture/concepts/surface-boundary.md")

    def test_resolves_a_wikilink(self):
        self.assertTrue(self.idx.resolve("[[surface-boundary]]").ok)

    def test_resolves_a_wikilink_with_a_display_name(self):
        self.assertTrue(self.idx.resolve("[[surface-boundary|the boundary]]").ok)

    def test_reverse_round_trips(self):
        eid = "knowledge-ai-architecture-surface-boundary"
        self.assertEqual(self.idx.reverse(eid).path, self.idx.resolve(eid).path)

    def test_unresolvable_gives_null_never_a_guess(self):
        r = self.idx.resolve("no-such-entity-anywhere-12345")
        self.assertEqual(r.status, STATUS_UNRESOLVED)
        self.assertIsNone(r.entity_id)
        pb = r.produced_by()
        self.assertIsNone(pb["produced_by"])
        self.assertEqual(pb["produced_by_ref"], "no-such-entity-anywhere-12345")
        self.assertEqual(pb["resolution_status"], STATUS_UNRESOLVED)

    def test_ambiguous_is_its_own_status_not_a_silent_pick(self):
        r = self.idx.resolve("core-doctrine")
        self.assertEqual(r.status, STATUS_AMBIGUOUS)
        self.assertIsNone(r.entity_id)
        self.assertGreater(len(r.candidates), 1)

    def test_a_department_directory_is_not_id_bearing(self):
        """D00's caution, tested: the directory has no id of its own."""
        self.assertNotIn("department-devops-platform", self.idx.by_id)
        r = self.idx.resolve("departments/devops-platform")
        self.assertTrue(r.ok)
        self.assertEqual(r.entity_id, "department-devops-platform-index")
        self.assertEqual(r.matched_by, "path")

    def test_same_id_in_several_homes_collapses_to_the_canonical_one(self):
        r = self.idx.resolve("agent-canon-editor")
        self.assertTrue(r.ok)
        self.assertTrue(r.path.startswith("entities/"), r.path)

    def test_a_file_with_no_id_returns_a_path_but_no_id(self):
        r = self.idx.resolve("stable-id-and-alias-rules")
        self.assertEqual(r.status, STATUS_UNRESOLVED)
        self.assertEqual(r.path, "_system/stable-id-and-alias-rules.md")
        self.assertTrue(r.matched_by.endswith("-no-id"))


class TestCliExitCodes(unittest.TestCase):
    """Exit codes are the contract for a caller that cannot parse prose."""

    def _run(self, *args):
        env = dict(os.environ, BRAIN_ROOT=str(BRAIN))
        return subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "bin" / "brain-adapter"), *args],
            capture_output=True, text=True, env=env)

    @unittest.skipUnless(BRAIN.exists(), "no brain at BRAIN_ROOT")
    def test_zero_on_resolved(self):
        self.assertEqual(self._run("entity", "resolve", "surface-boundary").returncode, 0)

    @unittest.skipUnless(BRAIN.exists(), "no brain at BRAIN_ROOT")
    def test_three_on_unresolved(self):
        self.assertEqual(self._run("entity", "resolve", "nope-not-here-9999").returncode, 3)

    @unittest.skipUnless(BRAIN.exists(), "no brain at BRAIN_ROOT")
    def test_four_on_ambiguous(self):
        self.assertEqual(self._run("entity", "resolve", "core-doctrine").returncode, 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
