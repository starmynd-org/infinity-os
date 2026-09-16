"""L01-SPINE-01: the spine composes, and it says which half of itself is real.

The run itself is the artifact; this suite is what stops it becoming a run that always says PASS.
Three groups: the database refusals, which are the only thing here that could damage anything if
they were wrong; the composition properties; and the two negative scenes the packet named.
"""

from __future__ import annotations

import pytest

from ports import REAL, STANDIN, SpineRefused
from preflight import SHARED_DEFAULT, UnsafeDatabase, census, guard_database_name
from run import INJECTED, run_spine
from standins import StandinRuntime


# -- the refusals ----------------------------------------------------------------------------------


@pytest.mark.parametrize("name,because", [
    (None, "no database was named"),
    ("", "no database was named"),
    ("   ", "no database was named"),
    ("brain", "refusing the live store"),
    ("brain_dev", "must contain"),
    ("postgres", "must contain"),
    (SHARED_DEFAULT, "shared"),
])
def test_a_database_this_spine_may_not_touch_is_refused(name, because):
    """The only code here that could do harm is the code that picks a database, so it is the code
    with the most tests. `brain_scratch` is refused by name: it is the store every lane's suite
    reads, and `scratch-db.sh` records the day an agent dropped it while finding out what its
    verbs were."""
    with pytest.raises(UnsafeDatabase, match=because):
        guard_database_name(name)


def test_a_private_scratch_name_is_accepted():
    """The positive control. A refusal that refuses everything protects nothing and proves less."""
    assert guard_database_name("brain_scratch_spine_t03") == "brain_scratch_spine_t03"
    assert guard_database_name("  brain_scratch_spine_t03  ") == "brain_scratch_spine_t03"


def test_a_census_that_could_not_connect_says_so_rather_than_reporting_zero():
    """"I could not look" and "I looked and found nothing" are different answers.

    Without credentials this environment cannot connect, and the census reports `connected=False`
    with the driver's reason. A census that returned an empty present-list either way would read
    as a measurement and be a failure to measure.
    """
    report = census("brain_scratch_spine_t03_does_not_exist")
    assert report.connected is False
    assert report.error
    assert report.can_run_real_runtime_stages is False
    assert "connected       no" in "\n".join(report.rows())


def test_the_census_names_every_relation_it_looked_for():
    """Print the denominator. An absence is a finding only when the thing that would have been
    there is named, so the report lists every relation with its status rather than summarising.

    THE NAMES ARE THE CORRECTED ONES. Four of the original five were wrong, taken from the packet's
    prose because the schema was what was missing; the merge showed `authority` is `authority_grant`
    and so on. This test is what pins them to the tree instead of to prose."""
    rows = "\n".join(census("brain_scratch_spine_t03_does_not_exist").rows())
    for relation in ("authority_grant", "authority_revocation", "approval",
                     "execution_lease", "effect_attempt", "receipt"):
        assert relation in rows


# -- the run ---------------------------------------------------------------------------------------


@pytest.fixture
def rows(tmp_path):
    """The STAND-IN path. These tests run with no database, which is the only path a suite can
    take unattended: a live run writes rows into a disposable store that has to exist first.
    `test_live_runtime_refuses_a_database_it_may_not_touch` covers the live path's own guard."""
    return run_spine(tmp_path)


def test_every_stage_passes_and_the_run_covers_all_six(rows):
    assert [e.ok for e in rows] == [True] * len(rows)
    stages = [e.stage for e in rows]
    for n in ("1 ", "2 ", "3a", "3b", "4 ", "5 ", "6 ", "N1", "N2"):
        assert any(s.startswith(n) for s in stages), n


def test_the_live_runtime_refuses_a_database_it_may_not_touch():
    """The live path writes rows through real transitions, so its guard is the one that matters
    most. It refuses before anything can connect.

    This docstring used to say the refusal had to precede the import because `store.session` reads
    the name at import time. That was an assumption I never drove, and Terminal 04 measured it
    false: the name is read per connection, so a guard after the import guards the value that will
    actually be used. The refusal still comes first, for the plainer reason that a refused database
    must never reach a connection at all."""
    from live import LiveRuntime, LiveUnavailable

    for name in ("brain", "postgres", "brain_dev"):
        with pytest.raises(LiveUnavailable, match="refusing"):
            LiveRuntime(name)


def test_no_stand_in_stage_is_ever_reported_as_real(rows):
    """The claim the whole artifact rests on: a stage says truthfully what backed it.

    On the stand-in path every runtime stage is a stand-in. On the live path (`--db`) stages 3 to 5
    become real and only B01 remains, because B01 has no table in this checkout or the merged one.
    A run that let a stand-in stage print `real` would be the exact dishonesty this file exists to
    prevent, so it is asserted rather than left to care."""
    for e in rows:
        if any(lane in e.stage for lane in ("R01", "R02", "B01")):
            assert e.backing == STANDIN, e.stage
    assert {e.backing for e in rows} == {REAL, STANDIN}


def test_the_packet_cites_the_capture_that_actually_landed(rows):
    """The spine is a spine only if the stages are joined. The packet's evidence names the capture
    id stage 1 produced, and the promotion row at the far end traces back to the same id."""
    capture_id = rows[0].detail["capture_id"]
    promotion = next(e for e in rows if e.stage.startswith("6 "))
    assert promotion.detail["traces_to"] == capture_id
    assert capture_id.startswith("cap_")


def test_execution_is_refused_before_the_decision_and_allowed_after(rows):
    """The gate is a gate. Stage 2 is a complete packet that cannot execute; stage 3b is the same
    packet with a decision recorded against that exact version."""
    proposed = next(e for e in rows if e.stage.startswith("2 "))
    bound = next(e for e in rows if e.stage.startswith("3b"))
    assert "no decision" in proposed.detail["refused"]
    assert bound.detail["allowed"] is True
    assert bound.detail["completeness"] == "complete"


def test_the_receipt_carries_both_an_outcome_and_a_completeness(rows):
    settled = next(e for e in rows if e.stage.startswith("5 "))
    assert settled.detail["outcome"] == "succeeded"
    assert settled.detail["completeness"] == "complete"


# -- the two negative scenes the packet named ------------------------------------------------------


def test_the_same_effect_cannot_be_reserved_twice(rows):
    """The packet's first negative scene. The reason text differs between the two runtimes and
    that difference is the finding: the stand-in refuses ("already spent"), the real R02 hands
    back the PRIOR ATTEMPT ("was already reserved ... as attempt N"), so a retrying caller learns
    what the first attempt knew instead of guessing."""
    scene = next(e for e in rows if e.stage.startswith("N1"))
    assert scene.ok
    assert "already" in scene.detail["reason"]


def test_an_evidence_sentence_cannot_reach_the_execution_decision(rows):
    """External text is data. A directive planted in the evidence changes what a human is shown
    and nothing about what the gate decides, so the verdict AND its reason are unchanged."""
    scene = next(e for e in rows if e.stage.startswith("N2"))
    assert scene.ok
    assert scene.detail["allowed"] is False
    assert scene.detail["same_reason_as_clean_packet"] is True
    assert scene.detail["excerpt_len"] == len(INJECTED)


# -- the stand-in's own refusals, which the scenes above depend on ----------------------------------


def test_a_second_holder_cannot_take_a_held_lease(tmp_path):
    runtime = StandinRuntime(tmp_path / "r.sqlite3")
    runtime.acquire_lease("act_1", holder="dispatcher-1")
    with pytest.raises(SpineRefused, match="already held"):
        runtime.acquire_lease("act_1", holder="dispatcher-2")
    runtime.close()


def test_an_effect_cannot_be_reserved_against_a_decision_nobody_made(tmp_path):
    runtime = StandinRuntime(tmp_path / "r.sqlite3")
    with pytest.raises(SpineRefused, match="no approval recorded"):
        runtime.reserve({"reservation_id": "r1", "version_hash": "wp_nothing",
                         "action_id": "act_1"})
    runtime.close()


def test_an_effect_cannot_be_reserved_for_an_action_the_approval_does_not_name(tmp_path):
    """AD-I2 in the direction that is easy to miss: the approval exists, it is approved, and it
    simply does not cover this action."""
    runtime = StandinRuntime(tmp_path / "r.sqlite3")
    runtime.record_approval({
        "version_hash": "wp_abc", "packet_id": "pkt", "decision": "approved",
        "decided_by": "andrew", "decided_at": "2026-09-07T09:00:00Z",
        "action_ids": ("act_covered",),
    })
    with pytest.raises(SpineRefused, match="does not name action"):
        runtime.reserve({"reservation_id": "r1", "version_hash": "wp_abc",
                         "action_id": "act_uncovered"})
    runtime.close()


def test_nothing_can_be_settled_that_was_never_reserved(tmp_path):
    runtime = StandinRuntime(tmp_path / "r.sqlite3")
    with pytest.raises(SpineRefused, match="no reservation"):
        runtime.settle("r_nothing", outcome="success", completeness="complete")
    runtime.close()


# -- L01-SPINE-02: the live path, run when a store is present and SKIPPED WITH A REASON otherwise --
#
# The Admiral's ruling. The gap I reported was that `pytest engine/spine/tests` exercised only the
# stand-in path, so the eight-real result was reproducible by hand and defended by nothing. The
# shape is `test_c01_projection.py`'s: run it when the environment has what it needs, and when it
# does not, SKIP WITH THE REASON PRINTED, so a run without a store cannot read as a pass.

SPINE_DB_ENV = "SPINE_SCRATCH_DB"


def _live_database() -> str | None:
    """The scratch store to run against, or None with the reason recorded by the caller."""
    import os

    name = (os.environ.get(SPINE_DB_ENV) or "").strip()
    return name or None


@pytest.fixture
def live_rows(tmp_path):
    """Drive the spine against a real store, or skip saying exactly why.

    THE SKIP IS THE POINT. A suite that silently passed without a store would report the same green
    as one that ran eight real stages, and the difference between those two is the whole artifact.
    """
    name = _live_database()
    if name is None:
        pytest.skip(
            f"{SPINE_DB_ENV} is unset, so the live path did NOT run. This suite has exercised the "
            f"stand-in path only. Set it to a disposable database carrying migrations 57-64 "
            f"(built with `ENGINE_SCRATCH_DB=<name> ./engine/bin/scratch-db.sh ensure`) to run it."
        )
    from live import LiveUnavailable

    try:
        return run_spine(tmp_path, name)
    except LiveUnavailable as exc:
        pytest.skip(f"{SPINE_DB_ENV}={name} is not usable: {exc}")


def test_the_live_path_leaves_only_b01_as_a_stand_in(live_rows):
    """The claim the live receipt makes, defended by a test rather than by a transcript.

    Counted as "everything but B01" rather than as a number: the count moved from eight to nine the
    day the vantage row was added, and a test pinned to the integer would have failed for a reason
    that had nothing to do with the property.
    """
    real = [e for e in live_rows if e.backing == REAL]
    standin = [e for e in live_rows if e.backing == STANDIN]

    assert [e.ok for e in live_rows] == [True] * len(live_rows)
    assert len(standin) == 1 and "B01" in standin[0].stage, "only B01 has no table anywhere"
    assert len(real) == len(live_rows) - 1


def test_the_live_path_refuses_the_second_reservation_through_the_real_port(live_rows):
    """The negative scene against R02 itself. The real port hands back the PRIOR ATTEMPT, which is
    a different contract from the stand-in's refusal, and the wording is what says so."""
    scene = next(e for e in live_rows if e.stage.startswith("N1"))
    assert scene.backing == REAL
    assert "already reserved" in scene.detail["reason"]
    assert "do not re-execute it" in scene.detail["reason"]


def test_an_unconnected_census_says_not_asked_and_never_absent():
    """SPINE-CENSUS-01 (CAP14-REV-046).

    A relation is ABSENT only if somebody looked and did not find it. With no connection nobody
    looked, and printing ABSENT states a measurement never taken. The model always kept `connected`
    separate; the renderer was reading it as a result.

    CAP14 named the relation list. The capability and stage loops had the identical defect and were
    not named, so they are asserted here too: fixing only what was reported is how a finding gets
    closed without being fixed.
    """
    rows = "\n".join(census("brain_scratch_spine_t03_does_not_exist").rows())

    assert "ABSENT" not in rows
    assert "WAITING ON" not in rows
    assert "NOT ASKED" in rows
    assert "connected       no" in rows


def test_a_connected_census_still_distinguishes_present_from_absent(tmp_path):
    """The positive control. A renderer that said NOT ASKED unconditionally would pass the test
    above and report nothing at all, which is the failure mode of a fix aimed at a string."""
    from preflight import CORROBORATING_RELATIONS, Census

    looked = Census("brain_scratch_probe", True, ledger=64,
                    recorded=frozenset(range(1, 65)),
                    present=(CORROBORATING_RELATIONS[0][1],),
                    absent=tuple(r for _, r in CORROBORATING_RELATIONS[1:]))
    rows = "\n".join(looked.rows())

    assert "present" in rows and "ABSENT" in rows
    assert "NOT ASKED" not in rows
    assert "WAITING ON 67" in rows, "a ledger at 64 has not got 67 and must say so"


def test_the_live_path_states_its_vantage_and_is_not_a_superuser(live_rows):
    """Terminal 04's finding, generalised by CAP14 and applied to my own evidence.

    Every negative scene on the live path is a refusal offered as proof. A refusal watched from a
    privileged role proves the layers that ignore privilege and NOTHING about the layer that does
    not, so the run has to say who was looking. Measured, not assumed: this path connects as
    `brain_runtime`.

    The assertion is the one that matters on the day someone changes the connection: if the spine
    ever runs as `owner` or a superuser, every refusal below stops meaning what it says, and this
    fails rather than staying green.
    """
    vantage = next(e for e in live_rows if e.stage.startswith("0 "))

    assert vantage.ok
    assert vantage.detail["superuser"] is False, (
        "refusals watched from a superuser prove nothing about grants; T04 found a whole defence "
        "layer untested behind exactly this"
    )
    assert vantage.detail["role"] == "brain_runtime"


def test_the_docstring_documents_the_invocation_that_produces_the_figure():
    """SPINE-DOC-01 (CAP14-REV-048).

    `--db` is the invocation the G3 figure comes from and it was documented nowhere in this file.
    The prose then asserted the live path did not exist, so running the file the way it documented
    itself CONFIRMED the wrong conclusion. A stale comment misleads once; a self-confirming one
    survives the reader checking.

    The second assertion is the one with teeth: no standing measurement. A number written into a
    docstring is true of one store on one day and then speaks for every other, which is how "It is
    five absent, today" outlived the day it was measured.
    """
    import run as run_module

    doc = run_module.__doc__ or ""

    assert "--db" in doc, "the invocation that produces the figure must be documented"
    assert "--census" in doc

    for stale in ("It is five absent", "five required relations", "by name"):
        assert stale not in doc, f"standing measurement or superseded mechanism in the docstring: {stale}"


@pytest.mark.parametrize("name", ["brain", "brain_scratch", "postgres", "brain_dev", ""])
def test_the_writing_path_refuses_every_database_the_reading_path_refuses(name):
    """The live path WRITES, so it must be at least as strict as the census that only reads.

    It was not. `bind()` carried its own weaker copy of the rule -- "contains scratch and is not
    brain" -- so `--db brain_scratch` would have driven real transitions into the store every
    lane's suite reads, while the read-only census beside it refused that name by name. The writing
    path more permissive than the reading path is backwards, and no test failed to say so: it was
    found by auditing my own runners for Terminal 26.

    Both now call `guard_database_name`, so the two cannot diverge again. This test is the pin.
    """
    from live import LiveRuntime, LiveUnavailable

    with pytest.raises(LiveUnavailable):
        LiveRuntime(name)


def test_the_database_name_is_bound_at_connect_time_not_at_import_time():
    """The claim I shipped as a fact and never measured, now pinned by a test.

    I asserted in a comment that `store.session` reads `BRAIN_PG_DB` at import time, reasoned it
    from reading the code rather than driving it, and that assertion travelled into a fleet-wide
    warning. Terminal 04 measured the opposite and asked where mine came from; there was no answer,
    because there was no measurement.

    So the correction gets a test rather than a corrected comment. A comment can go back to being
    wrong the next time somebody reasons about it; this fails if the binding ever moves to import
    time, which would make the ordering rule real and everything written about it true again.
    """
    import os

    import store  # noqa: F401 - imported first ON PURPOSE, which is the point
    from store.session import dsn

    before = os.environ.get("BRAIN_PG_DB")
    try:
        os.environ["BRAIN_PG_DB"] = "brain_scratch_set_after_the_import"
        assert dsn("runtime")["dbname"] == "brain_scratch_set_after_the_import"
    finally:
        if before is None:
            os.environ.pop("BRAIN_PG_DB", None)
        else:
            os.environ["BRAIN_PG_DB"] = before


# -- SPINE-G3-RENAME-01: the caller survives migration 65's rename ---------------------------------


class _Reserve64:
    """R02 as it is at ledger 64: the guard is called `fencing_token`."""

    @staticmethod
    def reserve(**kwargs):  # pragma: no cover - signature is the subject, not the body
        ...

    @staticmethod
    def _reserve(ctx, *, idempotency_key, lease_id, fencing_token, description, workspace,
                 subject, capability, scope, proposal=None):  # pragma: no cover
        ...


class _Reserve65:
    """R02 after migration 65: the same guard is called `lease_epoch`."""

    @staticmethod
    def reserve(**kwargs):  # pragma: no cover
        ...

    @staticmethod
    def _reserve(ctx, *, idempotency_key, lease_id, lease_epoch, description, workspace,
                 subject, capability, scope, proposal=None):  # pragma: no cover
        ...


def test_the_caller_passes_the_lease_guard_by_whichever_name_the_store_uses():
    """CAP14-REV-048's SPINE-G3-RENAME-01, and the half its verdict did not cover.

    CAP14 raised this as an EVIDENCE problem and said it was not a code defect, because this branch
    never modified `coordinator.py` so the merge takes T04's renamed version cleanly. That is right
    about `coordinator.py` and wrong about this file: the CALLER is mine and it named
    `fencing_token` twice, once reading the lease row and once as the keyword. On a store carrying
    65 the read is a KeyError and the keyword a TypeError, so the merge would have taken a correct
    callee and left a broken caller.

    Both halves are discovered rather than assumed, so this passes at ledger 64 and at 65+ with no
    edit at merge time. That is `docs/SCHEMA-TOLERANCE.md` rule 5 applied to a rename.
    """
    from live import _epoch_kwarg

    assert _epoch_kwarg(_Reserve64, {"fencing_token": 7}) == {"fencing_token": 7}
    assert _epoch_kwarg(_Reserve65, {"lease_epoch": 7}) == {"lease_epoch": 7}
    # A store carrying both answers with the current name, not the older one.
    assert _epoch_kwarg(_Reserve65, {"lease_epoch": 9, "fencing_token": 7}) == {"lease_epoch": 9}


def test_a_lease_carrying_neither_name_is_a_named_refusal_not_a_keyerror():
    """If R02 renames the guard a third time, this must say so rather than fail obscurely."""
    from live import LiveUnavailable, _epoch_kwarg

    with pytest.raises(LiveUnavailable, match="renamed again"):
        _epoch_kwarg(_Reserve64, {"lease_id": 1, "holder": "x"})


def test_the_live_run_records_the_ledger_its_figure_was_measured_against(live_rows):
    """SPINE-G3-RENAME-01's evidence half. The live figure was produced on a ledger-64 store, and
    on a store carrying 65-67 the same code does not produce a different number -- it does not run.
    So a figure without its ledger describes a store configuration rather than a system, and both
    CAP14 and I published one. The version now travels with the number."""
    vantage = next(e for e in live_rows if e.stage.startswith("0 "))

    assert isinstance(vantage.detail["ledger"], int)
    assert "holes" in vantage.detail, "holes are reported, not smoothed over: membership, not magnitude"


def test_no_docstring_in_the_spine_carries_a_superseded_claim():
    """CAP14-REV-065's minor, closed structurally rather than by correcting the sentence.

    My SPINE-DOC-01 guard read `run.py`'s MODULE docstring, and the next instance of the same class
    landed in `live.py`'s `settle` METHOD docstring, ten lines above a comment that said the
    opposite. A guard that covers one docstring is a guard that names the place the next one will
    not be.

    So this walks EVERY docstring in both modules -- module, class and function -- and refuses the
    claims that migration 67 superseded. T06's framing is the reason it is worth doing at all: a
    description adjacent to code is a second implementation that nothing typechecks, so the only
    fix that holds is one that typechecks it.
    """
    import ast
    import pathlib

    superseded = (
        "there is no column for it",
        "travels in `detail`",
        "COMPLETENESS IS NOT R02",
    )
    spine = pathlib.Path(__file__).resolve().parents[1]
    checked = 0
    for name in ("live.py", "run.py", "ports.py", "preflight.py", "standins.py"):
        tree = ast.parse((spine / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                continue
            doc = ast.get_docstring(node)
            if not doc:
                continue
            checked += 1
            for claim in superseded:
                assert claim not in doc, (
                    f"{name}:{getattr(node, 'name', '<module>')} still claims {claim!r}, which "
                    "migration 67 superseded"
                )
    # DENOMINATOR. "No superseded claim found" is also what this prints over zero docstrings.
    assert checked > 20, f"only {checked} docstrings walked; the scan found almost nothing"


def test_the_receipt_reports_the_stored_row_and_not_the_arguments_it_was_given():
    """CAP14-REV-065's precision, turned from a code-reading into a check.

    CAP14 queried the settled row from the store and found it agreed with my receipt. It then said
    what that does and does not prove: agreement is also what an ECHO produces, because the
    caller's arguments are what was written. It knew the echo was gone only by reading the code and
    seeing that `outcome` and `effect_state` take no fallback.

    So the control and the code-read together were the evidence, and the control alone would not
    have been. This makes the property checkable instead: a coordinator that stores something
    DIFFERENT from what it was handed, so a receipt built from the arguments and a receipt built
    from the row can no longer agree. Only reading the row passes.
    """
    from types import SimpleNamespace

    from live import LiveRuntime

    stored = {
        "outcome": "ambiguous",          # the store's answer, NOT the caller's
        "completeness": "uncertain",
        "effect_state": "unknown",
        "settled_at": "2026-09-07T09:00:00Z",
    }

    class _Coordinator:
        @staticmethod
        def _settle(ctx, *, attempt_seq, outcome, completeness, effect_state, settled_by,
                    settle_secret=None, result_ref=None, detail=None):  # pragma: no cover
            ...

        @staticmethod
        def settle(**kwargs):
            return dict(stored)

    # `ledger_versions` carries 67, because `settle` now decides the shape from the LEDGER rather
    # than from the callee's signature: T04's tolerance gave `_settle` one signature that tolerates
    # internally, so a signature probe answered "post-67" on every store and sent `noop` to a
    # ledger-64 store that has no spelling for it. Found by running, not by reading.
    fake = SimpleNamespace(coordinator=_Coordinator,
                           ledger_versions=frozenset({67}),
                           _leases={"act_1": {"lease_id": 1, "settle_secret": "s"}})

    receipt = LiveRuntime.settle(fake, "7", outcome="success", completeness="complete")

    assert receipt["outcome"] == "ambiguous", "the receipt echoed the caller instead of the row"
    assert receipt["effect_state"] == "unknown"
    assert receipt["completeness"] == "uncertain"


@pytest.mark.parametrize("recorded,applicable", [
    (frozenset(range(1, 69)), True),                       # a full tree
    (frozenset(range(1, 65)) | {68}, False),               # 68 above an unapplied 65-67: THIS tree
    (frozenset(range(1, 65)), False),                      # plain ledger 64
    (frozenset({66}), True),                               # membership, nothing else
])
def test_the_spend_scene_asks_whether_66_is_recorded_not_whether_the_ledger_is_high(
        recorded, applicable):
    """N3's gate, driven on both answers without needing a pre-66 store.

    The second case is this lane's own tree for several hours tonight: migration 68 recorded above
    an unapplied 65-67, where `max >= 66` reads TRUE and the mechanism does not exist. A gate
    written on magnitude would have run the scene there and asserted a refusal that cannot happen.

    The NOT ASKED row itself has still never been rendered against a real store -- every store here
    is at 67 or 68 -- so this pins the DECISION and not the row. That limit is recorded at the gate.
    """
    from run import _spend_scene_applicable

    assert _spend_scene_applicable(recorded) is applicable
