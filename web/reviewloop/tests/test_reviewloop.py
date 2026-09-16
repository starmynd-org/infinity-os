from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from web.reviewloop.core import (
    ALLOWED_GIT_COMMANDS,
    AcceptedDecision,
    GitRepository,
    Proposal,
    ReviewRule,
    ReviewLoopError,
    classify_target,
    materialize_accepted,
    run_pass,
    summarize_decisions,
)


def git(root: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Fixture Author",
        "GIT_AUTHOR_EMAIL": "fixture@local",
        "GIT_COMMITTER_NAME": "Fixture Author",
        "GIT_COMMITTER_EMAIL": "fixture@local",
    }
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def fixture_repo(files: dict[str, str]) -> tuple[tempfile.TemporaryDirectory, Path]:
    holder = tempfile.TemporaryDirectory(prefix="reviewloop-test-")
    root = Path(holder.name) / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
        git(root, "add", "--", relative)
    git(root, "commit", "-m", "fixture base")
    return holder, root


def proposal_from(report: dict, index: int = 0) -> Proposal:
    return Proposal(**report["proposals"][index])


class ReviewLoopTests(unittest.TestCase):
    def test_structured_forbidden_payloads_refuse_with_controls(self) -> None:
        class MemoryRepository:
            root = Path("C:/synthetic-reviewloop-fixture-not-created")

            def __init__(self, path: str):
                self.path = path

            def resolve(self, _ref: str) -> str:
                return "1" * 40

            def tracked_files(self, _ref: str) -> tuple[str, ...]:
                return (self.path,)

            def read_text(self, _ref: str, path: str) -> str:
                if path != self.path:
                    raise AssertionError(f"unexpected target: {path}")
                return "PLACEHOLDER\n"

            def grep(self, _ref: str, _query: str) -> tuple:
                return ()

        operational = {
            "queue row": "data/queue/row.json",
            "receipt": "data/receipts/receipt.json",
            "event": "data/events/event.json",
            "session": "data/sessions/session.json",
            "transcript": "data/transcripts/transcript.json",
            "run log": "data/run-logs/run.json",
        }
        cases = [
            ("drafts/NOTE-DRAFT.md", "Use a bounded review rule.\n", None),
            ("drafts/CONFIG-DRAFT.md", "secret_ref: synthetic/backend/reference\n", None),
            ("migrations/0001_existing.sql", "SELECT 2;\n", "MIGRATION-REFUSES"),
            ("AGENTS.md", "Use a bounded review rule.\n", "NAMED-HUMAN-REQUIRED"),
        ]
        cases.extend(
            (
                path,
                json.dumps(
                    {
                        "record_kind": kind,
                        "synthetic": True,
                        "record_id": "fixture-only",
                        "occurred_at": "2026-09-09T00:00:00Z",
                        "body": "synthetic operational record for a refusal test",
                    }
                )
                + "\n",
                "OPERATIONAL-STATE-REFUSES",
            )
            for kind, path in operational.items()
        )
        cases.extend(
            (
                ("drafts/CONFIG-DRAFT.md", "api_key: synthetic-test-value-not-a-real-key\n", "SENSITIVE-PAYLOAD-REFUSES"),
                (
                    "drafts/CONFIG-DRAFT.md",
                    "entitlement: administrator\nsubject: synthetic-fixture-actor\n",
                    "SENSITIVE-PAYLOAD-REFUSES",
                ),
            )
        )

        self.assertEqual(len(cases), 12)
        for index, (path, replacement, refusal_code) in enumerate(cases):
            with self.subTest(index=index, path=path):
                report = run_pass(
                    MemoryRepository(path),
                    base_ref="fixture-main",
                    workspace="synthetic-fixture",
                    producer_actor="worker:fixture",
                    rules=(
                        ReviewRule(
                            target_path=path,
                            needle="PLACEHOLDER\n",
                            replacement=replacement,
                            title="Synthetic boundary test",
                            why="Measure declared proposal refusal",
                            rationale="Synthetic fixture; no action authorized",
                            novelty_queries=("reviewloop-unique-boundary-fixture",),
                        ),
                    ),
                    invocation="pure in-memory protocol fixture",
                    run_at="2026-09-09T18:20:00Z",
                )
                self.assertEqual(report["measurement"]["denominator"]["review_targets"], 1)
                self.assertEqual(report["measurement"]["denominator"]["tracked_files_per_novelty_query"], 1)
                if refusal_code is None:
                    self.assertEqual(report["counts"]["proposed"], 1)
                else:
                    self.assertEqual(report["counts"]["proposed"], 0)
                    self.assertEqual(report["refusals"][0]["code"], refusal_code)
                    self.assertNotIn("synthetic-test-value-not-a-real-key", json.dumps(report["refusals"]))

        adjacent = (
            (
                "drafts/CONFIG-DRAFT.md",
                "api_key: PLACEHOLDER\n",
                "PLACEHOLDER",
                "synthetic-test-value-not-a-real-key",
                "SENSITIVE-PAYLOAD-REFUSES",
            ),
            (
                "drafts/CONFIG-DRAFT.md",
                "secret_ref: PLACEHOLDER\n",
                "PLACEHOLDER",
                "synthetic/backend/reference",
                None,
            ),
            (
                "drafts/NOTE-DRAFT.md",
                "PLACEHOLDER\n",
                "PLACEHOLDER\n",
                '{"record_kind":"session","body":"synthetic record"}\n',
                "OPERATIONAL-STATE-REFUSES",
            ),
            (
                "drafts/CONFIG-DRAFT.md",
                "PLACEHOLDER\n",
                "PLACEHOLDER\n",
                '{"configuration":{"api_key":"synthetic-test-value-not-a-real-key"}}\n',
                "SENSITIVE-PAYLOAD-REFUSES",
            ),
            (
                "drafts/CONFIG-DRAFT.md",
                "PLACEHOLDER\n",
                "PLACEHOLDER\n",
                '{"configuration":{"api_key":"synthetic-test-value-not-a-real-key"},"configuration":{}}\n',
                "DUPLICATE-JSON-MEMBER-REFUSES",
            ),
            (
                "drafts/CONFIG-DRAFT.md",
                "PLACEHOLDER\n",
                "PLACEHOLDER\n",
                '{"configuration":{}}\n',
                None,
            ),
            (
                "drafts/NOTE-DRAFT.md",
                "PLACEHOLDER\n",
                "PLACEHOLDER\n",
                '{"record_kind":"session","record_kind":"authored-example"}\n',
                "DUPLICATE-JSON-MEMBER-REFUSES",
            ),
        )
        self.assertEqual(len(adjacent), 7)
        for index, (path, content, needle, replacement, refusal_code) in enumerate(adjacent):
            with self.subTest(adjacent=index, path=path):
                repository = MemoryRepository(path)
                repository.read_text = lambda _ref, _path, value=content: value
                report = run_pass(
                    repository,
                    base_ref="fixture-main",
                    workspace="synthetic-fixture",
                    producer_actor="worker:fixture",
                    rules=(
                        ReviewRule(
                            target_path=path,
                            needle=needle,
                            replacement=replacement,
                            title="Synthetic full-candidate boundary test",
                            why="Measure the complete proposed file",
                            rationale="Synthetic fixture; no action authorized",
                            novelty_queries=("reviewloop-unique-full-candidate-fixture",),
                        ),
                    ),
                    invocation="pure in-memory full-candidate protocol fixture",
                    run_at="2026-09-09T18:20:00Z",
                )
                if refusal_code is None:
                    self.assertEqual(report["counts"]["proposed"], 1)
                    self.assertEqual(
                        report["proposals"][0]["replacement_content"],
                        content.replace(needle, replacement, 1),
                    )
                else:
                    self.assertEqual(report["counts"]["proposed"], 0)
                    self.assertEqual(report["refusals"][0]["code"], refusal_code)
                    self.assertNotIn("synthetic-test-value-not-a-real-key", json.dumps(report["refusals"]))

    def test_draft_row_is_decidable_and_acceptance_stops_on_a_local_branch(self) -> None:
        holder, root = fixture_repo({"drafts/NOTE-DRAFT.md": "alpha\n"})
        self.addCleanup(holder.cleanup)
        report = run_pass(
            GitRepository(root),
            base_ref="main",
            workspace="ws-test",
            producer_actor="reviewloop:worker",
            rules=(
                ReviewRule(
                    target_path="drafts/NOTE-DRAFT.md",
                    needle="alpha",
                    replacement="beta",
                    title="Replace the draft marker",
                    why="The draft still carries its temporary marker.",
                    rationale="The reviewed replacement makes the draft explicit.",
                    novelty_queries=("replace the unique draft marker with beta",),
                ),
            ),
            invocation="python -m unittest draft local branch scene",
        )
        self.assertEqual(report["counts"]["proposed"], 1)
        proposal = proposal_from(report)
        item = proposal.attention_item
        self.assertEqual(item["kind"], "proposal")
        self.assertEqual(len(item["options"]), 3)
        self.assertTrue(all(option.get("inverse") for option in item["options"]))
        self.assertFalse(any(field in item for field in ("rank", "score", "points", "tier", "badge")))
        self.assertEqual(proposal.required_reviewer_class, "workspace-reviewer")
        self.assertIsNone(proposal.named_reviewer)

        self_accept_destination = Path(holder.name) / "self-accept-must-not-exist"
        with self.assertRaises(ReviewLoopError) as refusal:
            materialize_accepted(
                GitRepository(root),
                proposal,
                AcceptedDecision(
                    proposal_id=proposal.proposal_id,
                    decision="accept-change",
                    decided_by="reviewloop:worker",
                    decided_at="2026-09-09T14:59:00Z",
                    actor_kind="human",
                ),
                worktree_path=self_accept_destination,
            )
        self.assertEqual(refusal.exception.refusal.code, "HUMAN-ACCEPTANCE-REQUIRED")
        self.assertFalse(self_accept_destination.exists())

        destination = Path(holder.name) / "prepared"
        result = materialize_accepted(
            GitRepository(root),
            proposal,
            AcceptedDecision(
                proposal_id=proposal.proposal_id,
                decision="accept-change",
                decided_by="human:reviewer",
                decided_at="2026-09-09T15:00:00Z",
            ),
            worktree_path=destination,
        )
        self.assertEqual(result.status, "prepared-local-branch")
        self.assertEqual(git(destination, "branch", "--show-current"), result.branch)
        self.assertEqual((destination / "drafts" / "NOTE-DRAFT.md").read_text(encoding="utf-8"), "beta\n")
        self.assertIn("Publication: not performed", git(destination, "log", "-1", "--format=%B"))
        self.assertEqual(git(destination, "remote"), "")
        self.assertIn("human must publish", result.missing.lower())
        self.assertNotIn("push", ALLOWED_GIT_COMMANDS)
        self.assertNotIn("merge", ALLOWED_GIT_COMMANDS)

    def test_governed_target_needs_named_human_and_a_collision_becomes_an_item(self) -> None:
        self.assertEqual(classify_target("web/MUST-NOT-BUILD-successor.md"), "governed")
        holder, root = fixture_repo({"web/MUST-NOT-BUILD.md": "rule alpha\n"})
        self.addCleanup(holder.cleanup)
        without_name = run_pass(
            GitRepository(root),
            base_ref="main",
            workspace="ws-test",
            producer_actor="reviewloop:worker",
            rules=(
                ReviewRule(
                    target_path="web/MUST-NOT-BUILD.md",
                    needle="alpha",
                    replacement="beta",
                    title="Change a governed rule",
                    why="A governed rule is under explicit review.",
                    rationale="Fixture rationale.",
                    novelty_queries=("unique governed fixture topic",),
                ),
            ),
            invocation="python -m unittest governed reviewer refusal scene",
        )
        self.assertEqual(without_name["counts"]["proposed"], 0)
        self.assertEqual(without_name["refusals"][0]["code"], "NAMED-HUMAN-REQUIRED")

        with_name = run_pass(
            GitRepository(root),
            base_ref="main",
            workspace="ws-test",
            producer_actor="reviewloop:worker",
            rules=(
                ReviewRule(
                    target_path="web/MUST-NOT-BUILD.md",
                    needle="alpha",
                    replacement="beta",
                    title="Change a governed rule",
                    why="A governed rule is under explicit review.",
                    rationale="Fixture rationale.",
                    novelty_queries=("unique governed fixture topic",),
                    named_reviewer="human:andrew",
                ),
            ),
            invocation="python -m unittest governed conflict scene",
        )
        proposal = proposal_from(with_name)
        self.assertEqual(proposal.required_reviewer_class, "named-human")
        self.assertEqual(proposal.attention_item["canon_touching"], True)
        self.assertEqual(proposal.attention_item["surfacing"], "human")

        (root / "web" / "MUST-NOT-BUILD.md").write_text("rule concurrent\n", encoding="utf-8", newline="")
        git(root, "add", "--", "web/MUST-NOT-BUILD.md")
        git(root, "commit", "-m", "concurrent governed edit")
        destination = Path(holder.name) / "must-not-exist"
        result = materialize_accepted(
            GitRepository(root),
            proposal,
            AcceptedDecision(
                proposal_id=proposal.proposal_id,
                decision="accept-change",
                decided_by="human:andrew",
                decided_at="2026-09-09T15:01:00Z",
            ),
            worktree_path=destination,
        )
        self.assertEqual(result.status, "conflict-attention-item")
        self.assertFalse(destination.exists())
        conflict = result.conflict_item
        self.assertEqual(conflict["kind"], "exception")
        self.assertEqual(conflict["canon_touching"], True)
        self.assertEqual(conflict["surfacing"], "human")
        self.assertEqual(len(conflict["options"]), 3)

    def test_existing_migration_refuses_instead_of_resolving(self) -> None:
        holder, root = fixture_repo({"migrations/0001.sql": "SELECT 1;\n"})
        self.addCleanup(holder.cleanup)
        report = run_pass(
            GitRepository(root),
            base_ref="main",
            workspace="ws-test",
            producer_actor="reviewloop:worker",
            rules=(
                ReviewRule(
                    target_path="migrations/0001.sql",
                    needle="SELECT 1",
                    replacement="SELECT 2",
                    title="Edit an applied migration",
                    why="Fixture attempts an unsafe migration edit.",
                    rationale="This must refuse.",
                    novelty_queries=("unique migration collision fixture",),
                ),
            ),
            invocation="python -m unittest migration refusal scene",
        )
        self.assertEqual(report["counts"]["proposed"], 0)
        self.assertEqual(report["refusals"][0]["code"], "MIGRATION-REFUSES")

    def test_existing_answer_suppresses_a_duplicate_proposal_and_stops(self) -> None:
        query = "the room table is already documented as stale"
        holder, root = fixture_repo(
            {
                "web/README.md": "Queue has seven verbs.\n",
                "docs/KNOWN-GAPS.md": query + "\n",
            }
        )
        self.addCleanup(holder.cleanup)
        report = run_pass(
            GitRepository(root),
            base_ref="main",
            workspace="ws-test",
            producer_actor="reviewloop:worker",
            rules=(
                ReviewRule(
                    target_path="web/README.md",
                    needle="seven",
                    replacement="seventeen",
                    title="Repair the verb count",
                    why="The table is stale.",
                    rationale="The checked-in table disagrees with the source.",
                    novelty_queries=(query,),
                ),
            ),
            invocation="python -m unittest existing answer scene",
        )
        self.assertEqual(report["measurement"]["denominator"]["review_targets"], 1)
        self.assertEqual(report["counts"]["candidates"], 1)
        self.assertEqual(report["counts"]["proposed"], 0)
        self.assertEqual(report["counts"]["suppressed_existing_answer"], 1)
        self.assertEqual(report["suppressions"][0]["code"], "EXISTING-ANSWER-FOUND")
        self.assertTrue(report["stopping_condition"]["stop"])

    def test_false_positive_rate_uses_decided_proposals_as_denominator(self) -> None:
        holder, root = fixture_repo(
            {
                "drafts/A-DRAFT.md": "one\n",
                "drafts/B-DRAFT.md": "two\n",
            }
        )
        self.addCleanup(holder.cleanup)
        report = run_pass(
            GitRepository(root),
            base_ref="main",
            workspace="ws-test",
            producer_actor="reviewloop:worker",
            rules=(
                ReviewRule(
                    target_path="drafts/A-DRAFT.md",
                    needle="one",
                    replacement="first",
                    title="First draft change",
                    why="Fixture A.",
                    rationale="Fixture A.",
                    novelty_queries=("unique metric fixture A",),
                ),
                ReviewRule(
                    target_path="drafts/B-DRAFT.md",
                    needle="two",
                    replacement="second",
                    title="Second draft change",
                    why="Fixture B.",
                    rationale="Fixture B.",
                    novelty_queries=("unique metric fixture B",),
                ),
            ),
            invocation="python -m unittest metric denominator scene",
        )
        proposals = tuple(proposal_from(report, index) for index in range(2))
        acceptance = AcceptedDecision(
            proposals[0].proposal_id, "accept-change", "human:one", "2026-09-09T15:02:00Z"
        )
        rejection = AcceptedDecision(
            proposals[1].proposal_id, "reject-change", "human:two", "2026-09-09T15:03:00Z"
        )

        empty = summarize_decisions(proposals[:1], ())
        self.assertEqual(empty["proposal_denominator"], 1)
        self.assertEqual(empty["decided_proposal_denominator"], 0)
        self.assertEqual(empty["false_positive_rate"], "not-measurable")

        one = summarize_decisions(proposals[:1], (acceptance,))
        self.assertEqual(one["confirmed_fraction"], "1/1")
        self.assertEqual(one["decided_proposal_denominator"], 1)

        with self.assertRaisesRegex(ValueError, "more than one decision names proposal"):
            summarize_decisions(proposals[:1], (acceptance, acceptance))
        conflicting = AcceptedDecision(
            proposals[0].proposal_id, "reject-change", "human:two", "2026-09-09T15:03:00Z"
        )
        with self.assertRaisesRegex(ValueError, "more than one decision names proposal"):
            summarize_decisions(proposals[:1], (acceptance, conflicting))
        with self.assertRaisesRegex(ValueError, "proposal denominator contains the same proposal"):
            summarize_decisions((proposals[0], proposals[0]), (acceptance,))

        decisions = (acceptance, rejection)
        metrics = summarize_decisions(proposals, decisions)
        self.assertEqual(metrics["proposal_denominator"], 2)
        self.assertEqual(metrics["decided_proposal_denominator"], 2)
        self.assertEqual(metrics["confirmed_by_non_author"], 1)
        self.assertEqual(metrics["confirmed_fraction"], "1/2")
        self.assertEqual(metrics["declined"], 1)
        self.assertEqual(metrics["false_positive_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
