"""The capture gate, and the proof that the door is unchanged when it is not configured.

WHY THIS SUITE NEEDS NO DATABASE, unlike `test_the_door.py`. `create_app` already takes `store`
and `transitions` as injection seams, and `host.require_token()` reads an environment variable. So
the whole route can be exercised against fakes, which is what makes the two claims below testable
at all on a host with no Postgres:

    with capture=None  -> the door behaves exactly as it did before this change
    with a gate        -> nothing is written and nothing is acknowledged unless capture succeeded

The journal under the gate is the REAL `ingest/capture` one on a temp SQLite file with real
AES-GCM custody, not a mock, because a gate tested against a mock journal proves only that the
gate calls a method.

WHAT THIS SUITE DOES NOT CLAIM. It does not run the existing DB-backed door suite; that needs
Postgres, which this host does not have. The regression argument here is structural -- with
`capture=None` the new code is one `if` that does not fire, and `test_the_door_is_untouched_...`
asserts the fake store sees byte-identical calls either way -- not a green run of `test_the_door.py`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

LANE = Path(__file__).resolve().parents[1]      # intake-service/
ROOT = LANE.parent                              # runtime repo root
for _p in (str(LANE), str(ROOT / "ingest")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The token must exist before `create_app` is called: the door refuses to boot without one, and
# that refusal is deliberate (see the module docstring in app.py).
os.environ.setdefault("INTAKE_TOKEN", "test-token-not-a-real-secret")

flask = pytest.importorskip("flask", reason="the door is a Flask app")

from capture.contracts import Provenance, SourceRef                     # noqa: E402
from capture.journal import CaptureJournal, DeliveryAttempt             # noqa: E402
from capture.raw_custody import AesGcmCipher, RawStore                  # noqa: E402
from capture.sqlite_store import SqliteJournalStore                     # noqa: E402
from intake_service import capture_gate                                 # noqa: E402
from intake_service.capture_gate import (                               # noqa: E402
    CaptureRefused,
    CaptureUnavailable,
    DurableCaptureGate,
    NullCaptureGate,
)

TOKEN = os.environ["INTAKE_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


# -- fakes for the parts that need Postgres ---------------------------------------------------


class FakeStore:
    """Records what the door asked it to write. `StoreConfigError` is the door's own except arm."""

    class StoreConfigError(RuntimeError):
        pass

    def __init__(self, fail: bool = False):
        self.calls: list[dict] = []
        self.fail = fail

    def apply(self, verb, **kwargs):
        if self.fail:
            raise self.StoreConfigError("scratch store is deliberately down for this test")
        self.calls.append({"verb": verb, **kwargs})
        return {"taken": True, "objective_id": f"obj-{len(self.calls)}",
                "objective_name": kwargs["name"]}

    def read(self):  # pragma: no cover - only the dedup branch uses it
        raise self.StoreConfigError("not needed in these tests")


class FakeTransitions:
    OBJECTIVE_ORIGINS = ("email", "slack", "meeting", "manual", "intake")

    class VerbError(RuntimeError):
        pass


@pytest.fixture
def journal(tmp_path):
    store = SqliteJournalStore(tmp_path / "capture.sqlite3")
    custody = RawStore(tmp_path / "custody", AesGcmCipher(AesGcmCipher.generate_key()))
    yield CaptureJournal(store, custody), store, custody
    store.close()


@pytest.fixture
def gate(journal):
    j, _, _ = journal
    return DurableCaptureGate(
        j,
        delivery_attempt_cls=DeliveryAttempt,
        source_ref_cls=SourceRef,
        provenance=Provenance(connector="intake-door", connector_version="1", run_id="test"),
    )


def make_app(store, capture=None):
    from intake_service.app import create_app
    app = create_app(store=store, transitions=FakeTransitions(), capture=capture)
    app.config["TESTING"] = True
    return app


BODY = {
    "source": "acct-mail-1",
    "origin": "email",
    "content": "The client asked for the revised timeline by Tuesday.",
    "title": "Revised timeline",
    "idempotency_key": "uid-00042",
    "author": "person@example.invalid",
    "timestamp": "2026-09-05T10:00:00Z",
}


# -- the door is unchanged when capture is not configured -------------------------------------


def test_the_door_is_untouched_when_no_gate_is_injected():
    """The regression claim, made structurally: same calls, same status, same body keys."""
    without = FakeStore()
    resp_a = make_app(without).test_client().post("/intake", headers=HEADERS, json=BODY)

    explicit_null = FakeStore()
    resp_b = make_app(explicit_null, capture=None).test_client().post(
        "/intake", headers=HEADERS, json=BODY
    )

    assert resp_a.status_code == resp_b.status_code == 201
    assert resp_a.get_json() == resp_b.get_json()
    assert without.calls == explicit_null.calls
    # And no capture key leaks into the contract's answer when capture is off.
    assert set(resp_a.get_json()) <= {"objective", "name", "deduped", "derived"}


def test_the_null_gate_is_a_written_down_decision_not_an_omission():
    assert NullCaptureGate().enabled is False
    assert NullCaptureGate().capture(None, {}) is None


# -- capture happens before the acknowledgement ------------------------------------------------


def test_evidence_is_durable_before_the_objective_row_is_written(gate, journal):
    """The invariant, asserted on ordering rather than on a comment."""
    j, store, custody = journal
    fake_store = FakeStore()
    resp = make_app(fake_store, capture=gate).test_client().post(
        "/intake", headers=HEADERS, json=BODY
    )

    assert resp.status_code == 201
    assert store.count_events() == 1, "the journal row exists"
    assert len(fake_store.calls) == 1, "and so does the objective row"

    answer = resp.get_json()
    assert answer["capture_id"].startswith("cap_")
    assert answer["capture_seq"] == 1
    # The contract's own three keys are untouched; capture keys are additive.
    assert {"objective", "name", "deduped"} <= set(answer)

    # The raw body is in custody, byte for byte, and readable back.
    row = store.get(answer["capture_id"])
    assert json.loads(custody.get(row.raw_digest)) == BODY


def test_a_capture_failure_writes_nothing_and_acknowledges_nothing(journal):
    """The whole point of the gate: a 503 that leaves the item with the connector."""
    j, store, _ = journal

    class BrokenJournal:
        def capture(self, attempt):
            raise RuntimeError("custody volume is unreachable")

    broken = DurableCaptureGate(
        BrokenJournal(),
        delivery_attempt_cls=DeliveryAttempt,
        source_ref_cls=SourceRef,
        provenance=Provenance(connector="intake-door", connector_version="1", run_id="test"),
    )
    fake_store = FakeStore()
    resp = make_app(fake_store, capture=broken).test_client().post(
        "/intake", headers=HEADERS, json=BODY
    )

    assert resp.status_code == 503
    assert resp.get_json()["retry"] is True
    assert fake_store.calls == [], "no objective row was written"
    assert store.count_events() == 0


def test_the_doors_own_payload_rule_still_fires_before_capture(gate, journal):
    """Defence in depth, and the order matters: the cheaper refusal comes first.

    A NUL byte is refused by BOTH the door's payload rules and the capture contract. The door's
    rule runs first, so the caller gets the specific field name it can act on (`content`) rather
    than a generic capture error, and custody is never touched by a body already known bad.
    """
    j, store, _ = journal
    fake_store = FakeStore()
    resp = make_app(fake_store, capture=gate).test_client().post(
        "/intake", headers=HEADERS, json={**BODY, "content": "a\x00b"}
    )

    assert resp.status_code == 400
    assert "retry" not in resp.get_json()
    assert resp.get_json()["field"] == "content", "the door names the field, not the stage"
    assert fake_store.calls == []
    assert store.count_events() == 0
    assert sum(1 for _ in store.dead_letters()) == 0, "capture was never reached"


def test_the_gate_refuses_a_payload_the_capture_contract_cannot_represent(gate, journal):
    """The gate's own permanent-refusal path, for what gets past the door's field rules.

    The door validates the fields it knows; the capture contract validates everything it is asked
    to hash. A NUL smuggled inside a nested `metadata` value is representable to the door and not
    to the journal, which is exactly the gap `CaptureRefused` exists for -- and it must be a
    permanent refusal with a dead letter, never a retry loop.
    """
    j, store, _ = journal

    class FakePayload:
        origin, source, author = "email", "acct-mail-1", "person@example.invalid"
        timestamp = "2026-09-05T10:00:00Z"

        def signature(self):
            return "uid-poison"

    with pytest.raises(CaptureRefused) as exc:
        gate.capture(FakePayload(), {"metadata": {"note": "a\x00b"}})

    assert "cannot be captured" in str(exc.value)
    assert store.count_events() == 0
    assert sum(1 for _ in store.dead_letters()) == 1, "it is dead-lettered, not lost"


def test_a_journal_that_returns_a_non_durable_receipt_is_refused(journal):
    """The gate's last guard, and it needs its own test because the real journal cannot trip it.

    `ingest/capture` only ever hands back a receipt after its own commit returned, so this branch
    is unreachable through it. It exists for the implementation that is coming: R01's
    Postgres-backed store behind the same port. A store that reports success without durability
    is the one failure that would let this door acknowledge an item it did not keep, so the gate
    refuses the receipt rather than trusting the caller.
    """
    from capture.contracts import CaptureReceipt
    from capture.journal import CaptureOutcome

    class OptimisticJournal:
        """Answers before it has committed. journal_seq 0 means nothing was written."""

        def capture(self, attempt):
            return CaptureOutcome(receipt=CaptureReceipt(
                receipt_id="rcpt_optimistic", capture_id="cap_x", source_key="sk_x",
                journal_seq=0, content_digest="sha256:0", raw_digest=None, kind="create",
                revision=1, issued_at="2026-09-05T10:00:00Z", durable=False,
            ))

    class EmptyJournal:
        """Neither a receipt nor a dead letter: a partial implementation."""

        def capture(self, attempt):
            return CaptureOutcome()

    provenance = Provenance(connector="intake-door", connector_version="1", run_id="test")
    for broken in (OptimisticJournal(), EmptyJournal()):
        g = DurableCaptureGate(broken, delivery_attempt_cls=DeliveryAttempt,
                               source_ref_cls=SourceRef, provenance=provenance)
        fake_store = FakeStore()
        resp = make_app(fake_store, capture=g).test_client().post(
            "/intake", headers=HEADERS, json=BODY
        )
        assert resp.status_code == 503, broken
        assert resp.get_json()["retry"] is True
        assert fake_store.calls == [], "nothing was taken in on an undurable receipt"


def test_a_store_failure_after_a_successful_capture_still_returns_503(gate, journal):
    """Evidence survives; the work item does not. The connector retries and dedup absorbs it."""
    j, store, _ = journal
    fake_store = FakeStore(fail=True)
    resp = make_app(fake_store, capture=gate).test_client().post(
        "/intake", headers=HEADERS, json=BODY
    )

    assert resp.status_code == 503
    assert store.count_events() == 1, "the evidence was already durable and is kept"

    # The retry captures nothing new and lands the row this time.
    working = FakeStore()
    retry = make_app(working, capture=gate).test_client().post(
        "/intake", headers=HEADERS, json=BODY
    )
    assert retry.status_code == 201
    assert store.count_events() == 1, "the redelivery deduped instead of making a second event"
    assert len(working.calls) == 1


def test_a_redelivery_reports_the_same_capture_id(gate, journal):
    j, store, _ = journal
    client = make_app(FakeStore(), capture=gate).test_client()
    first = client.post("/intake", headers=HEADERS, json=BODY).get_json()
    second = make_app(FakeStore(), capture=gate).test_client().post(
        "/intake", headers=HEADERS, json=BODY
    ).get_json()

    assert first["capture_id"] == second["capture_id"]
    assert first["capture_seq"] == second["capture_seq"]
    assert store.count_events() == 1


def test_identity_comes_from_the_connectors_signature_not_the_title(gate, journal):
    """Two items sharing a title are two events; one signature redelivered is one."""
    j, store, _ = journal
    client = make_app(FakeStore(), capture=gate).test_client()
    client.post("/intake", headers=HEADERS, json={**BODY, "idempotency_key": "uid-1"})
    client.post("/intake", headers=HEADERS, json={**BODY, "idempotency_key": "uid-2"})

    assert store.count_events() == 2
    assert len(store.source_keys()) == 2


def test_an_edited_body_under_the_same_signature_is_a_revision(gate, journal):
    j, store, _ = journal
    client = make_app(FakeStore(), capture=gate).test_client()
    client.post("/intake", headers=HEADERS, json=BODY)
    client.post("/intake", headers=HEADERS, json={**BODY, "content": "Actually, Thursday."})

    assert store.count_events() == 2
    head = store.head(SourceRef("email", "acct-mail-1", "uid-00042").source_key)
    assert head.revision == 2


# -- authentication is still checked first ------------------------------------------------------


def test_an_unauthenticated_request_never_reaches_capture(gate, journal):
    """A caller with no token must not be able to write into custody."""
    j, store, _ = journal
    fake_store = FakeStore()
    resp = make_app(fake_store, capture=gate).test_client().post("/intake", json=BODY)

    assert resp.status_code == 401
    assert store.count_events() == 0
    assert fake_store.calls == []


def test_an_undecodable_body_never_reaches_capture(gate, journal):
    j, store, _ = journal
    resp = make_app(FakeStore(), capture=gate).test_client().post(
        "/intake", headers=HEADERS, data="not json at all"
    )
    assert resp.status_code == 400
    assert store.count_events() == 0


# -- the composition root ----------------------------------------------------------------------


def test_capture_is_off_unless_all_three_variables_are_set(tmp_path):
    """Off is the default, and each variable is a deliberate act."""
    from intake_service import bootstrap

    key = "ab" * 32
    assert bootstrap.build_gate({}) is None
    assert bootstrap.build_gate({"INTAKE_CAPTURE_DIR": str(tmp_path), "INTAKE_CAPTURE_KEY": key}) is None
    assert bootstrap.enabled({"INTAKE_CAPTURE": "off"}) is False

    gate = bootstrap.build_gate({
        "INTAKE_CAPTURE": "on",
        "INTAKE_CAPTURE_DIR": str(tmp_path / "cap"),
        "INTAKE_CAPTURE_KEY": key,
    })
    assert gate is not None and gate.enabled is True


@pytest.mark.parametrize(
    "env,fragment",
    [
        ({"INTAKE_CAPTURE": "on"}, "somewhere durable"),
        ({"INTAKE_CAPTURE": "on", "INTAKE_CAPTURE_DIR": "d"}, "will not fall back to plaintext"),
        ({"INTAKE_CAPTURE": "on", "INTAKE_CAPTURE_DIR": "d", "INTAKE_CAPTURE_KEY": "zz"}, "not hex"),
        ({"INTAKE_CAPTURE": "on", "INTAKE_CAPTURE_DIR": "d", "INTAKE_CAPTURE_KEY": "ab" * 8},
         "needs exactly 32"),
    ],
)
def test_a_misconfigured_capture_refuses_to_boot_rather_than_degrading(env, fragment):
    """The same argument host.require_token() makes: a door that boots wrong looks healthy."""
    from intake_service.bootstrap import CaptureConfigError, build_gate

    with pytest.raises(CaptureConfigError, match=fragment):
        build_gate(env)


def test_a_bootstrapped_gate_captures_through_the_real_journal(tmp_path):
    """End to end through the composition root: env in, durable evidence out."""
    from intake_service import bootstrap

    root = tmp_path / "cap"
    gate = bootstrap.build_gate({
        "INTAKE_CAPTURE": "on",
        "INTAKE_CAPTURE_DIR": str(root),
        "INTAKE_CAPTURE_KEY": "cd" * 32,
    })
    fake_store = FakeStore()
    resp = make_app(fake_store, capture=gate).test_client().post(
        "/intake", headers=HEADERS, json=BODY
    )

    assert resp.status_code == 201
    assert resp.get_json()["capture_seq"] == 1
    assert (root / "journal.sqlite3").exists()
    on_disk = b"".join(p.read_bytes() for p in (root / "custody").rglob("*") if p.is_file())
    assert b"revised timeline" not in on_disk, "custody is encrypted at rest"


def test_describe_tells_an_operator_what_is_on_without_printing_the_key():
    from intake_service.bootstrap import describe

    d = describe({"INTAKE_CAPTURE": "on", "INTAKE_CAPTURE_DIR": "/var/capture",
                  "INTAKE_CAPTURE_KEY": "ef" * 32})
    assert d["capture_enabled"] is True
    assert d["key_present"] is True and d["key_length_bytes"] == 32
    assert "ef" * 32 not in repr(d)
    assert describe({})["capture_enabled"] is False


# -- the contract and the code must name the same keys -------------------------------------------


#: Exactly what `CONTRACT.md` promises under "Three capture keys". If this list and that table
#: disagree, one of them is a defect, and the contract is the specification.
CONTRACT_CAPTURE_KEYS = {"capture_id", "capture_receipt", "capture_seq"}
CONTRACT_BASE_KEYS = {"objective", "name", "deduped"}


def test_the_capture_keys_are_exactly_the_three_the_contract_documents(gate):
    """A response carrying a key its own specification does not mention is the defect this
    session has been chasing all day, one level up: the claim and the code disagreeing while
    each looks fine alone. So the key set is asserted rather than described."""
    resp = make_app(FakeStore(), capture=gate).test_client().post(
        "/intake", headers=HEADERS, json=BODY
    )
    assert resp.status_code == 201
    keys = set(resp.get_json())
    assert CONTRACT_CAPTURE_KEYS <= keys, "the contract promises these three"
    extra = keys - CONTRACT_BASE_KEYS - CONTRACT_CAPTURE_KEYS - {"derived", "reason"}
    assert extra == set(), f"undocumented keys in the response: {sorted(extra)}"


def test_the_contract_file_and_the_code_name_the_same_capture_keys():
    """Read the specification and check it mentions each key the code emits.

    Crude on purpose: a full parse would be a second implementation of the table. This catches the
    case that actually happens, which is a key renamed in code and left alone in the document.
    """
    contract = (LANE / "CONTRACT.md").read_text(encoding="utf-8")
    for key in sorted(CONTRACT_CAPTURE_KEYS):
        assert f"`{key}`" in contract, f"{key} is emitted by the door but absent from CONTRACT.md"
    assert "when, and only\nwhen, the host has durable capture configured" in contract


def test_the_contract_claim_that_capture_off_is_unchanged_has_a_test_behind_it():
    """The contract says this is asserted by a test rather than promised. Name that test here so
    deleting it breaks something visible."""
    assert callable(test_the_door_is_untouched_when_no_gate_is_injected)
