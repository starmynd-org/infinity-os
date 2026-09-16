"""Tests for the adapter/store seam. Task 0103.

Everything here runs without a database on purpose. `store_join` imports no driver, so the
mapping and the git-parsing halves are testable on a host with Postgres stopped -- which is the
same property that makes "losing the store costs zero knowledge" true rather than hoped for.
The write half is exercised live by `adapter/tools/lineage_join_proof.py`, which needs the
store and says so.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain_adapter.index import Resolution  # noqa: E402
from brain_adapter.promotion import Lineage, PromotionRefused  # noqa: E402
from brain_adapter.receipt import Receipt, Touch  # noqa: E402
from brain_adapter.store_join import (  # noqa: E402
    RESOLUTION_STATUSES,
    is_sha,
    lineage_columns,
    parse_receipt,
    producer_stamp,
    read_promotion,
    repo_identity,
)

SHA = "0" * 40


# ---------------------------------------------------------------- the four states

def test_a_resolved_reference_carries_its_id_and_its_raw_ref():
    res = Resolution(ref="surface-boundary", status="resolved",
                     entity_id="knowledge-ai-architecture-surface-boundary")
    assert lineage_columns(res) == {
        "produced_by": "knowledge-ai-architecture-surface-boundary",
        "produced_by_ref": "surface-boundary",
        "resolution_status": "resolved",
    }


def test_an_unresolved_reference_is_null_plus_the_raw_ref_and_never_an_id():
    cols = lineage_columns(Resolution(ref="[[nope]]", status="unresolved"))
    assert cols["produced_by"] is None
    assert cols["produced_by_ref"] == "[[nope]]"
    assert cols["resolution_status"] == "unresolved"


def test_ambiguous_lands_distinctly_from_unresolved():
    """The distinction D2 built on purpose, and the one a two-state column would destroy."""
    amb = lineage_columns(Resolution(ref="accountability-chart", status="ambiguous",
                                     candidates=[{"entity_id": "a"}, {"entity_id": "b"}]))
    unres = lineage_columns(Resolution(ref="accountability-chart", status="unresolved"))
    assert amb["resolution_status"] == "ambiguous"
    assert unres["resolution_status"] == "unresolved"
    assert amb != unres
    # Both are NULL producers. If the status collapsed, these rows would be indistinguishable
    # and an ambiguous reference -- which is FIXABLE, it has candidates -- would read as a dead
    # end for the rest of the system's life.
    assert amb["produced_by"] is unres["produced_by"] is None


def test_the_fourth_state_is_no_attempt_and_is_not_unresolved():
    stamp = producer_stamp("d3-ingest")
    assert stamp["resolution_status"] is None, "a producer name never made a claim about the brain"
    assert stamp["produced_by_ref"] is None
    assert None not in RESOLUTION_STATUSES
    # Migration 17 (task 0142). The producer name lands in its own column and `produced_by` is
    # NULL, so `produced_by IS NOT NULL` means "an entity id" with no exceptions. Before 17 this
    # asserted the opposite (`stamp["produced_by"] == "d3-ingest"`), which is why the assertion
    # is spelled out both ways here rather than simply edited: the same shape that this function
    # emits was also worn by nine rows carrying a real, resolvable entity id, so a lineage
    # walker reading the triple alone could not tell those two apart.
    assert stamp["produced_by"] is None
    assert stamp["produced_by_producer"] == "d3-ingest"


def test_a_resolution_that_carries_an_id_it_did_not_earn_still_emits_null():
    """Belt: `Resolution.produced_by()` branches on the STATUS, not on the id field."""
    forged = Resolution(ref="[[nope]]", status="unresolved", entity_id="knowledge-made-up")
    assert lineage_columns(forged)["produced_by"] is None


def test_a_hand_edited_receipt_claiming_an_id_it_did_not_resolve_is_refused():
    """Braces: a git receipt is a text file anyone can edit, so parsing it is not trusting it.

    This is the realistic route to a fabricated id -- not a bug in the resolver, an edit to the
    committed markdown -- and it is refused one layer before the CHECK constraint would refuse
    it, with the calling code in the traceback rather than a bare constraint name.
    """
    body = _receipt().render().replace("| `resolution_status` | `resolved` |",
                                       "| `resolution_status` | `unresolved` |")
    parsed = parse_receipt(body, git_ref=SHA)
    with pytest.raises(PromotionRefused) as e:
        parsed.lineage_columns()
    assert e.value.code == "FABRICATED_ID"


def test_a_receipt_claiming_resolved_while_naming_no_id_is_refused_too():
    body = _receipt().render().replace(
        "| `produced_by` | `knowledge-ai-architecture-surface-boundary` |",
        "| `produced_by` | `null` |")
    with pytest.raises(PromotionRefused) as e:
        parse_receipt(body, git_ref=SHA).lineage_columns()
    assert e.value.code == "LINEAGE_INCOHERENT"


# ---------------------------------------------------------------- the subject fix

def test_touch_edges_name_the_declared_subject_not_the_receipt_itself():
    """The defect the seam surfaced: edges used to point at the record of the edges.

    WAGER-2c's subject is `disposition_id` OR `wager_id`. Neither arm could be expressed while
    render() hardcoded ('receipt', own-id), and a projection of such a receipt is
    self-referential and cannot be scored.
    """
    r = _receipt(subject_type="work_item", subject_id="0103")
    body = r.render()
    assert 'subject_type: "work_item"' in body
    assert 'subject_id: "0103"' in body


def test_an_unset_subject_still_renders_exactly_as_before():
    """Backward compatibility, so every receipt already committed still parses the same."""
    r = _receipt()
    body = r.render()
    assert 'subject_type: "receipt"' in body
    assert f'subject_id: "{r.entity_id()}"' in body


# ---------------------------------------------------------------- reading git back

def test_a_committed_receipt_parses_into_the_columns_it_was_written_with():
    r = _receipt(subject_type="disposition", subject_id="d-42")
    parsed = parse_receipt(r.render(), git_ref=SHA, path="departments/x/receipts/y.md")
    assert parsed.entity_id == r.entity_id()
    assert parsed.moment == "result-produced"
    assert parsed.lineage_columns() == {
        "produced_by": "knowledge-ai-architecture-surface-boundary",
        "produced_by_ref": "surface-boundary",
        "resolution_status": "resolved",
    }
    assert [t["subject_type"] for t in parsed.touches] == ["disposition"]
    assert parsed.touches[0]["orient_role"] == "tradition"
    assert parsed.touches[0]["role"] == "load-bearing"


def test_an_unresolved_edge_keeps_its_raw_reference_and_is_projectable():
    """`touch add --allow-unresolved` commits a null id; migration 9 made the store hold it.

    Was `test_an_edge_git_can_hold_and_the_table_cannot_is_reported_not_dropped`, and the rename
    is the decision task 0114 took: `brain.touch.entity_id` is nullable, so this edge is no
    longer something the table cannot hold. What must survive is the raw reference, because
    without it a null id is a row that lost its claim rather than one that preserved it.
    """
    r = _receipt(touches=[Touch(entity_id=None, component_type="rule", orient_role="substrate",
                                entity_ref="retrieval-load-order-policy",
                                resolution_status="unresolved")])
    parsed = parse_receipt(r.render(), git_ref=SHA)
    assert len(parsed.unresolved_touches()) == 1
    assert parsed.unresolved_touches()[0]["entity_ref"] == "retrieval-load-order-policy"
    assert parsed.unresolved_touches()[0]["resolution_status"] == "unresolved"
    assert parsed.unprojectable_touches() == [], "a named reference IS a far end"


def test_an_edge_naming_no_far_end_at_all_is_still_unprojectable():
    """The narrow thing the old NOT NULL was really buying, kept at the correct width."""
    r = _receipt(touches=[Touch(entity_id="knowledge-ai-architecture-surface-boundary",
                                component_type="rule", orient_role="substrate")])
    parsed = parse_receipt(r.render(), git_ref=SHA)
    parsed.touches[0]["entity_id"] = None
    parsed.touches[0]["entity_ref"] = None
    assert len(parsed.unprojectable_touches()) == 1


def test_ambiguous_is_not_collapsed_into_unresolved_on_an_edge():
    """Both are a null id. They are FIXABLE differently, so the row must say which."""
    r = _receipt(touches=[Touch(entity_id=None, component_type="rule", orient_role="substrate",
                                entity_ref="accountability-chart",
                                resolution_status="ambiguous")])
    parsed = parse_receipt(r.render(), git_ref=SHA)
    assert parsed.unresolved_touches()[0]["resolution_status"] == "ambiguous"


def test_a_receipt_with_no_id_is_refused_rather_than_attributed_to_nothing():
    with pytest.raises(PromotionRefused) as e:
        parse_receipt("# not a receipt\n", git_ref=SHA)
    assert e.value.code == "RECEIPT_HAS_NO_ID"


# ---------------------------------------------------------------- the direction of truth

def test_only_a_full_sha_counts_as_a_source():
    assert is_sha(SHA)
    assert is_sha("a" * 64)
    assert not is_sha("scratch/d2-adapter-receipt-proof"), "a branch moves"
    assert not is_sha("HEAD")
    assert not is_sha("c0aceaa8"), "an abbreviation is not the commit's name"
    assert not is_sha(None)


def test_the_projection_reads_the_commit_and_not_the_working_tree(tmp_path):
    """Edit the file after committing it; the projection must still see the committed text."""
    repo = tmp_path / "r"
    (repo / "departments" / "d" / "receipts").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "scratch/test")
    _git(repo, "config", "user.email", "t@t"); _git(repo, "config", "user.name", "t")
    target = repo / "departments" / "d" / "receipts" / "2026-08-16-x.md"
    target.write_text(_receipt().render(), encoding="utf-8")
    _git(repo, "add", str(target.relative_to(repo)))
    _git(repo, "commit", "-qm", "receipt")
    sha = _git(repo, "rev-parse", "HEAD")

    target.write_text(target.read_text().replace("surface-boundary", "TAMPERED"), encoding="utf-8")
    parsed = read_promotion(repo, sha)
    assert parsed.lineage_columns()["produced_by_ref"] == "surface-boundary"
    assert "TAMPERED" not in str(parsed.lineage)


# ---------------------------------------------------------------- the work item a receipt names
#
# Task 0149. `brain.thread.work_item_id` has a foreign key to `brain.work_item`, and
# `project_parsed` used to fall back to the id the RECEIPT named when the caller passed none. The
# two ids are the same SHAPE and different REGISTRIES -- `brain.work_item.id` is
# `DEFAULT next_item_id()`, a store-local sequence; the receipt's is whatever queue booked it --
# so the key was as likely to be satisfied by the wrong task as to be violated.
#
# These stub `store.apply` rather than reaching a database, because what is under test is which
# argument the adapter hands over, and that is answerable with no store at all. The four
# behaviours that need one -- the note written, skipped, not requested, and not repeated -- are
# in `adapter/tools/work_item_note_proof.py`, which says so and needs the live constraint.

def _captured_apply(monkeypatch):
    """Swap `store.apply` for a recorder and return the list it records into."""
    from brain_adapter import store_projection

    calls = []

    def fake_apply(verb, **kwargs):
        calls.append((verb, kwargs))
        return {"receipt": {"id": 1}, "touches_inserted": 0, "touches_already_present": 0,
                "already_projected": False, "thread_note": "not-requested"}

    monkeypatch.setattr(store_projection.store, "apply", fake_apply)
    return store_projection, calls


def test_the_receipts_own_work_item_is_not_promoted_into_the_stores_foreign_key(monkeypatch):
    proj, calls = _captured_apply(monkeypatch)
    parsed = parse_receipt(_receipt().render(), git_ref=SHA, path="departments/x/receipts/y.md")
    assert parsed.lineage.get("work_item_id") == "0103", "the fixture must NAME one, or this " \
                                                         "test passes for the wrong reason"

    out = proj.project_parsed(parsed, actor="T2")           # caller asks for nothing

    assert calls[0][1]["work_item_id"] is None, (
        "the receipt's own work_item_id reached the foreign-key parameter. That is task 0149: "
        "it is an id from another registry and the key would be satisfied by whichever store "
        "task happened to share the number.")
    assert out["lineage_work_item_id"] == "0103", "reported, so the skip is visible not silent"


def test_a_work_item_id_the_caller_names_is_still_passed_through(monkeypatch):
    """The fix is about the FALLBACK. A caller that knows its registry still gets its note."""
    proj, calls = _captured_apply(monkeypatch)
    parsed = parse_receipt(_receipt().render(), git_ref=SHA, path="departments/x/receipts/y.md")

    proj.project_parsed(parsed, actor="T2", work_item_id="0041")

    assert calls[0][1]["work_item_id"] == "0041"


def test_store_join_needs_no_database_driver():
    """The import graph IS the invariant, so this measures the graph and not the source text.

    A fresh interpreter imports the git half and reports what came with it. If `store` or
    `psycopg2` appear, the adapter has acquired a database dependency and "losing the store
    costs zero knowledge" is no longer true of the code that reads the knowledge.
    """
    probe = (
        "import sys; sys.path.insert(0, %r);"
        "import brain_adapter.store_join;"
        "print(sorted(m for m in sys.modules if m in ('store', 'psycopg2')))"
        % str(Path(__file__).resolve().parents[1])
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", f"store_join pulled in {out.stdout.strip()}"


# ---------------------------------------------------------------- helpers

def _receipt(subject_type=None, subject_id=None, touches=None) -> Receipt:
    return Receipt(
        department="warner-sandbox", date="2026-08-16", slug="t5-seam-test",
        title="seam test", moment="result-produced", summary="seam test",
        lineage=Lineage(produced_by="knowledge-ai-architecture-surface-boundary",
                        produced_by_ref="surface-boundary", resolution_status="resolved",
                        actor_type="ai", work_item_id="0103"),
        touches=touches if touches is not None else [
            Touch(entity_id="knowledge-ai-architecture-surface-boundary",
                  component_type="knowledge", orient_role="tradition")],
        subject_type=subject_type, subject_id=subject_id,
    )


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


# ---------------------------------------------------------------- which repository (task 0256)
#
# `brain.receipt.git_ref` was a bare sha with no column naming a repository, and `receipt book
# --repo X` accepts any git repository. Measured on the live store 2026-08-16: 20 of 26 distinct
# git_refs resolved in NO repository on this machine, and four of them were still readable in
# `/tmp/0149-cli-zf9k78n8` and `/tmp/0149-proof-6a81fb59-ifs6ld7b` -- commits that were never the
# brain's, recorded as durable evidence. These tests are the seam half of the repair: a projection
# now carries the identity of the repository it read, so "the brain lost a commit" and "that commit
# was never the brain's" stop being the same row.


def _fixture_repo(root: Path, branch: str = "scratch/test") -> Path:
    """A throwaway repo shaped like the ones that produced the 20 LOST shas."""
    repo = root
    (repo / "departments" / "d" / "receipts").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", branch)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    # A base commit, so the root commit is NOT the receipt commit. Without it the two shas are the
    # same string and `git_repo == git_ref` passes for the wrong reason -- which is what this
    # fixture did on its first run, caught by the assertion below rather than by reading the code.
    #
    # The content is keyed to the directory NAME, and that is not decoration. Two repos with a
    # byte-identical first commit made in the same second get the same root commit sha, so a
    # constant here made two unrelated fixture repos into the same repository -- which is a real
    # property of this identity, pinned by
    # `test_two_repositories_born_in_the_same_second_collide` below rather than papered over here.
    (repo / "README.md").write_text(f"fixture {repo.name}\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "base")
    return repo


def _commit_receipt(repo: Path, name: str) -> str:
    target = repo / "departments" / "d" / "receipts" / name
    target.write_text(_receipt().render(), encoding="utf-8")
    _git(repo, "add", str(target.relative_to(repo)))
    _git(repo, "commit", "-qm", f"receipt {name}")
    return _git(repo, "rev-parse", "HEAD")


def test_a_projection_records_which_repository_its_commit_came_from(tmp_path):
    repo = _fixture_repo(tmp_path / "throwaway")
    sha = _commit_receipt(repo, "2026-08-17-a.md")
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")

    parsed = read_promotion(repo, sha)

    assert parsed.git_repo == root, (
        "the projection did not record the repository it read. Without this the row is the one "
        "task 0256 measured: a sha that could have come from anywhere.")
    assert parsed.git_repo != parsed.git_ref, "the identity is the ROOT commit, not this commit"


def test_two_repositories_are_never_the_same_repository(tmp_path):
    """The whole point: a /tmp fixture and the brain must not produce the same `git_repo`."""
    a = _fixture_repo(tmp_path / "a")
    b = _fixture_repo(tmp_path / "b")
    sha_a = _commit_receipt(a, "2026-08-17-a.md")
    sha_b = _commit_receipt(b, "2026-08-17-b.md")

    assert read_promotion(a, sha_a).git_repo != read_promotion(b, sha_b).git_repo


def test_repo_identity_is_the_same_on_every_branch(tmp_path):
    """A receipt booked on a scratch branch must attribute to the SAME repository as trunk.

    This lane proves promotions on scratch branches, so an identity that changed per branch would
    be useless exactly where it is needed. `--all` would do that -- it folds in the roots of every
    branch that happens to exist -- which is why `repo_identity` walks one commit's ancestry.
    """
    repo = _fixture_repo(tmp_path / "r", branch="scratch/one")
    first = _commit_receipt(repo, "2026-08-17-a.md")
    _git(repo, "checkout", "-q", "-b", "scratch/two")
    second = _commit_receipt(repo, "2026-08-17-b.md")

    assert repo_identity(repo, first)[0] == repo_identity(repo, second)[0]


def test_a_repository_with_no_remote_still_gets_a_readable_label(tmp_path):
    repo = _fixture_repo(tmp_path / "r")
    sha = _commit_receipt(repo, "2026-08-17-a.md")

    ident, label = repo_identity(repo, sha)

    assert ident and len(ident) == 40
    assert label == str(repo.resolve()), "no origin: the path is the honest label"


def test_a_directory_that_is_not_a_repository_yields_null_and_never_raises(tmp_path):
    """A projection is about the receipt. Failing it because the label could not be computed
    would trade a complete row for no row, and the NULL says exactly what happened."""
    assert repo_identity(tmp_path) == (None, None)


def test_parse_receipt_never_invents_a_repository():
    """`parse_receipt` is given a body and a sha and no repository. A guess here is the exact
    fabrication migration 19's NULL exists to avoid."""
    parsed = parse_receipt(_receipt().render(), git_ref=SHA, path="departments/x/receipts/y.md")

    assert parsed.git_repo is None and parsed.git_repo_ref is None


def test_the_repository_reaches_the_store_transition(monkeypatch, tmp_path):
    """Recording it on the parsed receipt is worthless if it stops before the INSERT."""
    proj, calls = _captured_apply(monkeypatch)
    repo = _fixture_repo(tmp_path / "r")
    sha = _commit_receipt(repo, "2026-08-17-a.md")
    parsed = read_promotion(repo, sha)

    proj.project_parsed(parsed, actor="T6")

    assert calls[0][1]["git_repo"] == parsed.git_repo
    assert calls[0][1]["git_repo_ref"] == parsed.git_repo_ref


def test_an_unattributed_receipt_is_not_defaulted_to_the_brain(monkeypatch):
    """`project_parsed` also serves `parse_receipt`-built receipts, which have no repository.
    Filling in the configured brain there would write a claim nobody proved."""
    proj, calls = _captured_apply(monkeypatch)
    parsed = parse_receipt(_receipt().render(), git_ref=SHA, path="departments/x/receipts/y.md")

    proj.project_parsed(parsed, actor="T6")

    assert calls[0][1]["git_repo"] is None
    assert calls[0][1]["git_repo_ref"] is None


def test_two_repositories_born_in_the_same_second_collide(tmp_path):
    """The known limit of root-commit identity, pinned so it is a test and not a surprise.

    A commit sha covers the tree, the parents, the author, the committer, the message AND the
    timestamps. Two repositories whose first commit is byte-identical and lands in the same second
    therefore have the SAME root commit and are the same repository by this measure.

    This is not a defect to fix by changing the key. A remote URL is absent for a local-only repo
    and changes on an org rename; a filesystem path dies when the repo moves, and every one of the
    20 LOST shas came from a path that no longer exists. The root commit is still the only
    identity that survives rename, move and re-clone -- and for the collision that actually occurs
    in practice, a fork and its parent, sharing an identity is the CORRECT answer, because a
    commit reachable in one is history the other holds too.

    What it gets wrong is two unrelated repositories minted from the same template in the same
    second, which is this test. Asserted rather than asserted-against, so that a future change to
    the identity scheme has to come here and delete a test that says what it is giving up.
    """
    a, b = tmp_path / "a", tmp_path / "b"
    for repo in (a, b):
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "scratch/test")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "README.md").write_text("identical\n", encoding="utf-8")
        _git(repo, "add", "README.md")
        # A fixed timestamp, so the collision is deterministic instead of depending on how fast
        # the test host is. Without it this test passes or fails on a clock boundary.
        _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base",
             "--date=2026-08-17T00:00:00+00:00")

    assert repo_identity(a)[0] == repo_identity(b)[0], (
        "root-commit identity no longer collides on identical initial commits. That may be an "
        "improvement, but it changes what `git_repo` means and the change belongs in "
        "`repo_identity`'s docstring and in migration 19's column comment, not in a passing test.")
