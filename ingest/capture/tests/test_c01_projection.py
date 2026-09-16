"""The projection onto pinned C01, checked by C01's OWN validator rather than by mine.

This is the point of the file. A test asserting that my projection produces the fields I think C01
wants proves only that I read the schema the way I read the schema. So the instances this suite
produces are written into a copy of C01's fixture tree at the pinned commit and `tools/validate.py`
is run over them: their schemas, their invariants, their tool, my data.

The contracts are obtained with `git archive` of the NAMED COMMIT, never from the worktree, which
is the program rule and the one that caught a mid-draft tree earlier in this sprint.

IF THE PIN CANNOT BE RESOLVED THIS SUITE FAILS, IT DOES NOT SKIP. That is a change, and the reason
is `CAPTURE-CONTRACTS-PIN-HOST-BOUND-01`. The old text here said it "SKIPS with the reason rather
than passing: a projection nobody validated must not report green" -- the principle was right and
the mechanism did not deliver it, because a skip is EXIT 0. Two of the three resolution steps were
scratch infrastructure this sprint created, so on any host that never ran this sprint these seven
cases would have gone quiet and green forever, and nothing in the output would have told a reader
whether the projection was validated against C01 or never compared to it at all.

HOW TO RESOLVE THE PIN, in the order tried:

    C01_PIN_DIR=<dir>   a directory holding the pinned `contracts/` tree. THE SUPPORTED OVERRIDE,
                        documented here because it is what unblocks a second host today and until
                        now nobody outside this file knew it existed.
    <repos>/internal/infinite-brain-harness   a real clone carrying the commit. Found by walking up
                        from this file, never by a fixed parent count.
    C01_PIN_OPTIONAL=1  downgrade the refusal to a skip, on a host that deliberately carries
                        neither. An explicit, greppable decision rather than a silent default.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from capture.c01 import (
    C01_VERSION,
    ProjectionError,
    identity_key,
    to_capture_receipt,
    to_capture_record,
)
from capture.contracts import Provenance, RetentionPolicy, SourceRef
from capture.journal import CaptureJournal, DeliveryAttempt

#: The worktree root, so `attention` imports alongside `capture`. At module scope rather than
#: inside a helper: the T01 tests import `attention.packets.*` in their own bodies, which runs
#: before any helper does.
_WORKTREE = Path(__file__).resolve().parents[3]
if str(_WORKTREE) not in sys.path:
    sys.path.insert(0, str(_WORKTREE))

PINNED_COMMIT = "0f5909af55859403f8ee5e4cc3e36550c3d1c136"

#: CAPTURE-CONTRACTS-PIN-HOST-BOUND-01. This was
#: `HARNESS = Path(r"C:\Users\you\repos\_scratch-infinity\worktrees\H-cap01")`, and the fix is
#: the one THIS FILE ALREADY WROTE nineteen lines below for the pin directory: derive from
#: `__file__`, never write an absolute path. The reasoning was recorded and then not applied to the
#: line above it.
#:
#: TWO DEFECTS, not one, and the second is the one that reaches other hosts. The old constant was
#: (a) a `C:\...` path, which does not resolve under WSL where this suite runs, and (b) a path into
#: `_scratch-infinity`, which exists only because this sprint created it. `DEFAULT_PIN_DIR` below
#: is scratch too, so two of the three resolution steps disappeared on any machine that never ran
#: this sprint -- including the target environment being specified now.
#:
#: The candidates end at a REAL CLONE. Measured: `internal/infinite-brain-harness` carries this
#: commit, its `.git` is a DIRECTORY rather than a worktree pointer file, and WSL git can therefore
#: `git archive` from it -- 768 KB of `contracts/`, where the linked worktree returns nothing at
#: all. That last point is why this is a repair and not a relocation: the old path could not have
#: worked from here even if it had existed.
def _harness_candidates() -> list[Path]:
    """Real clones that may carry the pinned commit, derived from this file's location.

    Walks UP rather than counting a fixed number of parents, because a fixed count is exactly the
    defect `INGEST-COVERAGE-CONFTEST-DEPTH-01` was: correct for one nesting and silently wrong one
    level over. Each ancestor is asked whether it looks like the repos root.
    """
    seen: list[Path] = []
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / "internal" / "infinite-brain-harness"
        if (candidate / ".git").exists() and candidate not in seen:
            seen.append(candidate)
    return seen

#: A PRE-EXTRACTED copy of the pinned contracts, and the reason it exists is an environment
#: pincer rather than a preference. `git archive` can only read this Windows-created worktree from
#: Windows git, and pytest only exists in WSL, so the two halves of "verify against the named
#: commit" cannot run in the same process. Extracting once from Git Bash breaks the deadlock
#: WITHOUT weakening the rule: the tree still comes from `git archive` of the named commit, just
#: not from inside the test. Refresh it with the command in `RESUME-HERE.md` if the pin moves.
#:
#: The order below is deliberate. An explicit env var wins, then the conventional path, then a
#: live `git archive` from a real clone. If none of the three yields a validator the suite FAILS
#: with the reason and every candidate it tried, because a projection nobody validated must not
#: report green. This sentence used to end "SKIPS with the reason" and that was the defect
#: `CAPTURE-CONTRACTS-PIN-HOST-BOUND-01` names: the principle was already written here correctly,
#: and a skip is exit 0, so the mechanism did not deliver what the sentence promised.
#:
#: The path is DERIVED FROM THIS FILE'S LOCATION rather than written absolute, and that is the
#: whole fix. The first attempt hard-coded a `C:\...` path that exists and that this suite could
#: not see, because it runs under WSL where a `C:\` path does not resolve. Relative to `__file__`
#: is correct from either side of the extract-from-Windows / test-from-WSL boundary.
#: parents: 0 tests, 1 capture, 2 ingest, 3 R-cap0405, 4 worktrees, 5 _scratch-infinity.
PIN_DIR_ENV = "C01_PIN_DIR"
DEFAULT_PIN_DIR = Path(__file__).resolve().parents[5] / f"c01-pin-{PINNED_COMMIT[:8]}"

PROV = Provenance(connector="imap-synthetic", connector_version="1", run_id="r1",
                  cursor="cursor:inbox-a:0001")


def _pre_extracted() -> Path | None:
    """A tree already extracted from `git archive` of the pinned commit, if one is present."""
    import os

    for candidate in (os.environ.get(PIN_DIR_ENV), DEFAULT_PIN_DIR):
        if not candidate:
            continue
        root = Path(candidate)
        contracts = root / "contracts" if (root / "contracts").is_dir() else root
        if (contracts / "tools" / "validate.py").exists():
            return contracts
    return None


#: Set to any non-empty value to turn the refusal below back into a skip, on a host that
#: deliberately does not carry the contracts. It is an EXPLICIT, greppable decision by whoever set
#: it, which is the whole difference from the old default.
PIN_OPTIONAL_ENV = "C01_PIN_OPTIONAL"


def _unresolved(tried: list[str]) -> None:
    """FAIL, not skip, when the pin cannot be found. This is the second half of the finding.

    The old behaviour skipped on every unresolved path, and a skip is EXIT 0. Both remaining
    resolution steps were scratch infrastructure this sprint created, so on any other host these
    seven cases -- the ones binding the projection to its named contract -- would have gone quiet
    and green forever, and nothing in the output would distinguish "validated against C01" from
    "never looked at C01". `unittest`-style runners make this worse by reporting OK over an
    all-skipped run; this repository's own rule is that a verdict over an empty set is not a pass.
    Refusing makes the two states different at the exit code, which is where a reader looks.

    Kept deliberately: the reason. The old skip stated why it skipped, which was better than three
    other instruments in this program, and none of that detail is lost -- every candidate tried is
    named below.
    """
    import os

    detail = "; ".join(tried) if tried else "no harness clone carrying the pin was found"
    remedy = (
        f"Set {PIN_DIR_ENV} to a directory holding the pinned `contracts/` tree, or place a clone "
        f"at <repos>/internal/infinite-brain-harness carrying commit {PINNED_COMMIT[:8]}. "
        f"Set {PIN_OPTIONAL_ENV}=1 to downgrade this to a skip on a host that deliberately has "
        f"neither."
    )
    message = (
        f"C01 pin {PINNED_COMMIT[:8]} UNRESOLVED, so the projection was validated against nothing. "
        f"Tried: {PIN_DIR_ENV}={os.environ.get(PIN_DIR_ENV) or 'unset'}; "
        f"{DEFAULT_PIN_DIR} (absent); {detail}. {remedy}"
    )
    if os.environ.get(PIN_OPTIONAL_ENV):
        pytest.skip(message)
    pytest.fail(message, pytrace=False)


@pytest.fixture(scope="module")
def contracts_tree(tmp_path_factory):
    """The pinned contracts, from a pre-extracted archive or a live one. Skips rather than passes.

    The tree is COPIED before use, never validated in place, because the suite writes fixture files
    into it and a shared pre-extracted directory must not accumulate one run's artifacts for the
    next run to trip over.
    """
    source = _pre_extracted()
    work = tmp_path_factory.mktemp("c01-pin")
    tried: list[str] = []

    if source is None:
        for harness in _harness_candidates():
            if shutil.which("git") is None:
                tried.append("git is not on PATH")
                break
            proc = subprocess.run(
                ["git", "-C", str(harness), "archive", PINNED_COMMIT, "contracts"],
                capture_output=True,
            )
            if proc.returncode != 0:
                tried.append(f"{harness}: {proc.stderr.decode(errors='replace').strip()[:120]}")
                continue
            tar = subprocess.run(["tar", "-x", "-C", str(work)],
                                 input=proc.stdout, capture_output=True)
            if tar.returncode != 0 or not (work / "contracts" / "tools" / "validate.py").exists():
                tried.append(f"{harness}: archived, but no contracts/tools/validate.py in it")
                continue
            print(f"\nC01 pin: archived {PINNED_COMMIT[:8]} from {harness}")
            yield work / "contracts"
            return
        _unresolved(tried)
        return

    print(f"\nC01 pin: using pre-extracted contracts at {source}")
    dest = work / "contracts"
    shutil.copytree(source, dest)
    yield dest


def _validate(contracts: Path, instances: list[tuple[str, str, dict]]) -> str:
    """Write instances as positive fixtures and run C01's validator over the whole tree."""
    written = []
    for schema, fixture_id, instance in instances:
        p = contracts / "fixtures" / "positive" / f"{schema}.pos.zz-t03-{fixture_id}.json"
        p.write_text(json.dumps({
            "fixture_id": f"{schema}.pos.zz-t03-{fixture_id}",
            "schema": schema,
            "kind": "positive",
            "reason": "Terminal 03 projection output, validated by C01's own tool",
            "instance": instance,
        }, indent=2), encoding="utf-8")
        written.append(p)
    try:
        proc = subprocess.run([sys.executable, "tools/validate.py"], cwd=contracts,
                              capture_output=True, text=True)
        return proc.stdout + proc.stderr
    finally:
        for p in written:
            p.unlink(missing_ok=True)


def _capture(store, custody, *, deleted=False, body="first"):
    journal = CaptureJournal(store, custody)
    return journal.capture(DeliveryAttempt(
        source=SourceRef("synthetic-mail", "acct:inbox-a", "email-001"),
        payload={"body": body},
        occurred_at="2026-09-06T17:59:00Z",
        provenance=PROV,
        raw_blob=json.dumps({"body": body}).encode("utf-8"),
        raw_media_type="application/json",
        retention=RetentionPolicy(brains=("brain-company",)),
        deleted=deleted,
    ))


def test_a_projected_record_and_receipt_pass_c01s_own_validator(contracts_tree, store, custody):
    """Their schemas, their invariants, their tool, my data."""
    outcome = _capture(store, custody)
    row = store.get(outcome.receipt.capture_id)
    record_wire = to_capture_record(
        _record_from(row), workspace="ws:alpha", source_kind="email",
        custody_store="raw-store-scratch", custody_locator="ws-alpha/2026/09/email-001.json",
        attempt=1,
    )
    receipt_wire = to_capture_receipt(
        outcome.receipt, custody_store="raw-store-scratch",
        custody_locator="ws-alpha/2026/09/email-001.json",
        new_source_identity=True, still_live=True,
        source=SourceRef("synthetic-mail", "acct:inbox-a", "email-001"),
    )

    out = _validate(contracts_tree, [
        ("capture-record", "record", record_wire),
        ("capture-receipt", "receipt", receipt_wire),
    ])
    assert "RESULT: PASS" in out, out[-3000:]
    assert "zz-t03" not in out or "FAIL" not in out, out[-3000:]


def _record_from(row):
    """Rebuild the internal record from the committed row, the way a reader would."""
    from capture.contracts import CaptureRecord, Provenance as P, RawCustodyRef, RetentionPolicy as R

    r = row.record
    return CaptureRecord(
        capture_id=r["capture_id"],
        source=SourceRef(r["source"]["provider"], r["source"]["account_id"],
                         r["source"]["source_id"]),
        kind=r["kind"], revision=r["revision"], content_digest=r["content_digest"],
        raw=RawCustodyRef(**r["raw"]) if r.get("raw") else None,
        attachments=(), occurred_at=r["occurred_at"], captured_at=r["captured_at"],
        participants=tuple(r["participants"]),
        retention=R(**{**r["retention"], "brains": tuple(r["retention"]["brains"])}),
        provenance=P(**r["provenance"]), supersedes=r.get("supersedes"),
    )


def test_a_tombstone_projects_as_tombstoned_and_not_live(contracts_tree, store, custody):
    _capture(store, custody)
    tomb = _capture(store, custody, deleted=True)
    row = store.get(tomb.receipt.capture_id)
    actor = {"actor_id": "svc:intake-door", "kind": "service_principal", "workspace": "ws:alpha"}
    wire = to_capture_record(
        _record_from(row), workspace="ws:alpha", source_kind="email",
        custody_store="s", custody_locator="l", tombstoned_by=actor,
    )
    assert wire["disposition"] == "tombstoned"
    assert wire["live"] is False
    assert wire["durable"] is False
    assert wire["tombstone"]["reason"] == "source_deleted", "an enum value, not prose"
    assert wire["tombstone"]["tombstoned_by"] == actor
    out = _validate(contracts_tree, [("capture-record", "tomb", wire)])
    assert "RESULT: PASS" in out, out[-2000:]


def test_a_tombstone_without_an_actor_is_refused_rather_than_authored(store, custody):
    """C01 requires an Actor on a tombstone and this lane records none.

    Inventing one would put a made-up author of a deletion into evidence, which is the single thing
    a tombstone must not carry: the whole value of the row is that it says who ended the identity.
    Found by C01's validator rejecting my first projection, which also wrote prose where the schema
    wanted an enum.
    """
    _capture(store, custody)
    tomb = _capture(store, custody, deleted=True)
    row = store.get(tomb.receipt.capture_id)
    with pytest.raises(ProjectionError, match="inventing an author"):
        to_capture_record(_record_from(row), workspace="ws:alpha", source_kind="email",
                          custody_store="s", custody_locator="l")


# -- the rulings, asserted directly ---------------------------------------------------------------


def test_identity_key_uses_only_provider_account_and_external_id():
    """CR-I1. Thread and account_ref are deliberately NOT identity-bearing: hashing the whole
    object is the E06/E23 gap where the same item on another thread got a second key."""
    a = identity_key(SourceRef("synthetic-mail", "acct:inbox-a", "email-001"))
    b = identity_key(SourceRef("synthetic-mail", "acct:inbox-a", "email-001"))
    assert a == b and a.startswith("sha256:") and len(a) == 71

    # A different external_id is a different identity; a different provider likewise.
    assert a != identity_key(SourceRef("synthetic-mail", "acct:inbox-a", "email-002"))
    assert a != identity_key(SourceRef("other-mail", "acct:inbox-a", "email-001"))


def test_the_projection_refuses_to_invent_a_workspace(store, custody):
    """C01 requires a workspace this lane does not carry. Guessing it would put an unverified
    tenant boundary into evidence, and workspace is what the whole isolation story rests on."""
    outcome = _capture(store, custody)
    row = store.get(outcome.receipt.capture_id)
    for missing in ("workspace", "source_kind", "custody_store", "custody_locator"):
        kwargs = dict(workspace="ws:alpha", source_kind="email",
                      custody_store="s", custody_locator="l")
        kwargs[missing] = ""
        with pytest.raises(ProjectionError, match="rather than this projection inventing one"):
            to_capture_record(_record_from(row), **kwargs)


def test_durability_is_a_journal_position_and_not_a_flag(store, custody):
    """CR-I3, which is this lane's CR-4. A receipt asserting durability with no sequence is
    refused by the projection, with the reason named, rather than left to the schema."""
    from capture.contracts import CaptureReceipt

    lying = CaptureReceipt(
        receipt_id="rcpt_x", capture_id="cap_x", source_key="sk_x", journal_seq=0,
        content_digest="sha256:" + "0" * 64, raw_digest=None, kind="create", revision=1,
        issued_at="2026-09-06T18:00:00Z", durable=True,
    )
    with pytest.raises(ProjectionError, match="not a flag"):
        to_capture_receipt(lying, custody_store="s", custody_locator="l",
                           new_source_identity=True, still_live=True)


def test_the_audience_travels_with_the_evidence(store, custody):
    """CR-3, this lane's own request: the Brains the evidence is cleared for are on the record."""
    outcome = _capture(store, custody)
    row = store.get(outcome.receipt.capture_id)
    wire = to_capture_record(_record_from(row), workspace="ws:alpha", source_kind="email",
                             custody_store="s", custody_locator="l")
    assert wire["audience"] == ["brain-company"]


def test_the_projection_writes_the_pinned_version():
    assert C01_VERSION == "0.1.0-draft.5"


# -- capture-revision ------------------------------------------------------------------------------


def test_a_projected_revision_chain_passes_c01s_own_validator(contracts_tree, store, custody):
    """Revision 1 and revision 2, with the chain link the schema's conditional requires."""
    from capture.c01 import to_capture_revision

    first = _capture(store, custody, body="first")
    second = _capture(store, custody, body="second")
    r1 = _record_from(store.get(first.receipt.capture_id))
    r2 = _record_from(store.get(second.receipt.capture_id))
    assert r1.revision == 1 and r2.revision == 2

    initial = to_capture_revision(r1)
    revised = to_capture_revision(r2, previous_revision_hash=r1.content_digest)

    assert initial["change_kind"] == "initial"
    assert initial["previous_revision_hash"] is None
    assert revised["change_kind"] == "content_revision"
    assert revised["previous_revision_hash"] == r1.content_digest
    # CV-I1 holds by construction: both derive the key from the same SourceRef.
    assert initial["identity_key"] == revised["identity_key"]

    out = _validate(contracts_tree, [
        ("capture-revision", "rev1", initial),
        ("capture-revision", "rev2", revised),
    ])
    assert "RESULT: PASS" in out, out[-2500:]


def test_the_revision_chain_refuses_to_disagree_with_its_own_number(store, custody):
    """The two halves of the schema's conditional, refused here with a reason rather than there."""
    from capture.c01 import to_capture_revision

    first = _capture(store, custody, body="first")
    second = _capture(store, custody, body="second")
    r1 = _record_from(store.get(first.receipt.capture_id))
    r2 = _record_from(store.get(second.receipt.capture_id))

    with pytest.raises(ProjectionError, match="revision 1 cannot name a previous"):
        to_capture_revision(r1, previous_revision_hash=r1.content_digest)
    with pytest.raises(ProjectionError, match="does not carry"):
        to_capture_revision(r2)


def test_a_tombstone_is_not_a_revision(store, custody):
    """C01 models deletion as a record disposition, not as a revision."""
    from capture.c01 import to_capture_revision

    _capture(store, custody)
    tomb = _capture(store, custody, deleted=True)
    with pytest.raises(ProjectionError, match="not a capture-revision"):
        to_capture_revision(_record_from(store.get(tomb.receipt.capture_id)))


# -- T01: work-packet and approval-decision --------------------------------------------------------


def _demo_packet():
    from attention.packets.model import (
        Authority, Claim, EvidenceRef, ExternalAction, Risk, WorkPacket,
    )

    return WorkPacket(
        packet_id="pkt_demo", objective="Send the revised timeline",
        rationale=(Claim("Client asked.", cites=("cap_a",)),),
        evidence=(EvidenceRef("cap_a", "sk_1", 1, "Client asked for it.", ("brain-company",)),),
        deliverables=("A revised timeline.",), destination="client",
        acceptance_criteria=("Client confirms receipt.",), proposed_owner="andrew",
        authority_required=Authority.EXTERNAL, risk=Risk.MEDIUM, expected_cost="0", effort="1h",
        urgency="this week", rollback="Send a correction.", audience=("brain-company",),
        external_actions=(ExternalAction("send", "client", "Email it", capability="mail.send",
                                         reversibility="irreversible"),),
    )


PRODUCER = {"actor_id": "svc:attention", "kind": "service_principal", "workspace": "ws:alpha"}
DECIDER = {"actor_id": "human:andrew", "kind": "human", "workspace": "ws:alpha"}


def test_a_projected_work_packet_and_decision_pass_c01s_own_validator(contracts_tree):
    """Their schemas, their invariants, their tool, my data, for the T01 pair."""
    from capture.c01 import to_approval_decision, to_work_packet
    from attention.packets.approval import Approval, Decision, utcnow

    p = _demo_packet()
    wp = to_work_packet(p, workspace="ws:alpha", producer_actor=PRODUCER,
                        created_at="2026-09-07T00:00:00Z")
    approval = Approval(p.packet_id, p.version_hash, Decision.APPROVED, "andrew", utcnow(),
                        p.authority_required, action_ids=p.action_ids())
    ad = to_approval_decision(approval, workspace="ws:alpha", actor=DECIDER,
                              auth_evidence={"kind": "session", "ref": "sess:abc"},
                              version_hash=wp["version_hash"], decision_id="ad:1",
                              expires_at="2026-09-08T00:00:00Z", authority_check_ref="chk:1")

    out = _validate(contracts_tree, [("work-packet", "wp", wp), ("approval-decision", "ad", ad)])
    assert "RESULT: PASS" in out, out[-3000:]


def test_the_wire_version_hash_excludes_presentation(contracts_tree):
    """WP-I1, ruled this lane's way: a re-render must not void a decision already made."""
    from capture.c01 import to_work_packet, work_packet_version_hash

    p = _demo_packet()
    wp = to_work_packet(p, workspace="ws:alpha", producer_actor=PRODUCER,
                        created_at="2026-09-07T00:00:00Z")
    retitled = dict(wp, title="A completely different title")
    assert work_packet_version_hash(retitled) == wp["version_hash"]

    # A substantive change does move it.
    rescoped = dict(wp, intent="Do something else entirely")
    assert work_packet_version_hash(rescoped) != wp["version_hash"]


def test_a_canon_touching_packet_is_forced_to_human_surfacing():
    """AR-I3, derived rather than accepted from the caller so the two cannot disagree."""
    from capture.c01 import to_work_packet

    p = _demo_packet()
    wp = to_work_packet(p, workspace="ws:alpha", producer_actor=PRODUCER,
                        created_at="2026-09-07T00:00:00Z", canon_touching=True, surfacing="rule")
    assert wp["canon_touching"] is True
    assert wp["surfacing"] == "human", "a caller asking for rule surfacing is overruled, not obeyed"


def test_the_packet_projection_refuses_the_three_facts_this_lane_does_not_carry():
    from capture.c01 import ProjectionError, to_work_packet

    p = _demo_packet()
    base = dict(workspace="ws:alpha", producer_actor=PRODUCER, created_at="2026-09-07T00:00:00Z")
    for missing in ("workspace", "created_at"):
        kwargs = dict(base); kwargs[missing] = ""
        with pytest.raises(ProjectionError, match="not carried by this lane"):
            to_work_packet(p, **kwargs)
    kwargs = dict(base); kwargs["producer_actor"] = {}
    with pytest.raises(ProjectionError, match="records no actor on a packet"):
        to_work_packet(p, **kwargs)


def _approval(**kw):
    from attention.packets.approval import Approval, Decision, utcnow
    from attention.packets.model import Authority

    p = _demo_packet()
    base = dict(packet_id=p.packet_id, version_hash=p.version_hash, decision=Decision.APPROVED,
                decided_by="andrew", decided_at="2026-09-07T10:00:00Z",
                authority_granted=Authority.EXTERNAL, action_ids=p.action_ids())
    base.update(kw)
    return Approval(**base), p


def _project(approval, packet, **kw):
    from capture.c01 import to_approval_decision
    return to_approval_decision(
        approval, workspace="ws:alpha", actor=DECIDER,
        auth_evidence={"kind": "session", "ref": "sess:abc"},
        version_hash="sha256:" + "a" * 64, decision_id="ad:1",
        **{"expires_at": "2026-09-08T10:00:00Z", "authority_check_ref": "chk:1", **kw})


def test_a_spent_approval_does_not_project_as_a_fresh_one(contracts_tree):
    """STATE-API-PORT v3.1, consumer side. The store stopped a decision being SPENT twice at
    migration 0066; this asserts the projection cannot describe a spent one as unconsumed.

    It used to. `consumed` and `revocation` were hardcoded to the fresh state, so a decision
    already spent projected as available. That is the same hole one layer out: 0057's unique
    constraint stopped a decision being RECORDED twice and never stopped one being SPENT twice,
    and `single_use: true` has been in the contract throughout.
    """
    approval, packet = _approval()
    fresh = _project(approval, packet)
    assert fresh["single_use"] is True
    assert fresh["consumed"] == {"state": "unconsumed"}

    spent = _project(approval, packet, consumed={
        "state": "consumed",
        "consumed_at": "2026-09-07T11:00:00Z",
        "receipt_ref": "rcpt:1",
    })
    assert spent["consumed"]["state"] == "consumed"
    assert spent["consumed"]["receipt_ref"] == "rcpt:1"
    assert spent["single_use"] is True, "single_use is const true and never conditional"

    out = _validate(contracts_tree, [
        ("approval-decision", "fresh", fresh), ("approval-decision", "spent", spent),
    ])
    assert "RESULT: PASS" in out, out[-2500:]


def test_a_revoked_approval_projects_as_revoked(contracts_tree):
    approval, packet = _approval()
    revoked = _project(approval, packet, revocation={
        "revoked": True,
        "revoked_at": "2026-09-07T10:30:00Z",
        "revoked_by": DECIDER,
        "reason": "the target changed",
    })
    assert revoked["revocation"]["revoked"] is True
    out = _validate(contracts_tree, [("approval-decision", "revoked", revoked)])
    assert "RESULT: PASS" in out, out[-2500:]


def test_ad_i3_an_approval_cannot_expire_before_it_was_made():
    """Refused here with both timestamps named, rather than at the schema with a bare error."""
    from capture.c01 import ProjectionError

    approval, packet = _approval()
    with pytest.raises(ProjectionError, match="AD-I3"):
        _project(approval, packet, expires_at="2026-09-07T09:00:00Z")
    with pytest.raises(ProjectionError, match="AD-I3"):
        _project(approval, packet, expires_at=approval.decided_at)
    # Strictly after is fine.
    ok = _project(approval, packet, expires_at="2026-09-07T10:00:01Z")
    assert ok["expires_at"] == "2026-09-07T10:00:01Z"


def test_ad_i4_an_approval_revoked_before_it_was_spent_cannot_be_spent():
    """The store enforces this from 0066; a projection emitting the pair anyway would describe a
    state the store now refuses, which is worse than being refused by the store."""
    from capture.c01 import ProjectionError

    approval, packet = _approval()
    with pytest.raises(ProjectionError, match="AD-I4"):
        _project(
            approval, packet,
            consumed={"state": "consumed", "consumed_at": "2026-09-07T12:00:00Z",
                      "receipt_ref": "rcpt:1"},
            revocation={"revoked": True, "revoked_at": "2026-09-07T11:00:00Z",
                        "revoked_by": DECIDER},
        )
    # Spent BEFORE revocation is a legitimate history and must still project.
    ok = _project(
        approval, packet,
        consumed={"state": "consumed", "consumed_at": "2026-09-07T11:00:00Z",
                  "receipt_ref": "rcpt:1"},
        revocation={"revoked": True, "revoked_at": "2026-09-07T12:00:00Z",
                    "revoked_by": DECIDER},
    )
    assert ok["consumed"]["state"] == "consumed" and ok["revocation"]["revoked"] is True


def test_a_decision_without_auth_evidence_is_refused():
    """C01 is stronger than what this lane built, and the refusal says so.

    `decided_by` was a bare string defended by a blacklist of five literals. That is a heuristic;
    `auth_evidence` is structural, and a self-asserted name is not evidence of who decided.
    """
    from capture.c01 import ProjectionError, to_approval_decision
    from attention.packets.approval import Approval, Decision, utcnow

    p = _demo_packet()
    approval = Approval(p.packet_id, p.version_hash, Decision.APPROVED, "andrew", utcnow(),
                        p.authority_required)
    with pytest.raises(ProjectionError, match="not evidence of who decided"):
        to_approval_decision(approval, workspace="ws:alpha", actor=DECIDER, auth_evidence={},
                             version_hash="wp_x", decision_id="ad:1")


def test_an_approved_decision_must_expire_and_name_its_authority_check():
    """C01 requires both on an approved decision, and the pair is the point rather than the fields.

    An approval with no expiry is a standing permission nobody reviews again; one naming no
    authority check cannot be tied to the boundary that let it be made. Found by C01's validator
    refusing my first two single-use fixtures, which omitted both.
    """
    from capture.c01 import ProjectionError, to_approval_decision

    approval, packet = _approval()
    for omit in ("expires_at", "authority_check_ref"):
        kwargs = {"expires_at": "2026-09-08T10:00:00Z", "authority_check_ref": "chk:1"}
        kwargs.pop(omit)
        with pytest.raises(ProjectionError, match=omit):
            to_approval_decision(
                approval, workspace="ws:alpha", actor=DECIDER,
                auth_evidence={"kind": "session", "ref": "sess:abc"},
                version_hash="sha256:" + "a" * 64, decision_id="ad:1", **kwargs)
