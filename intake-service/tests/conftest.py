"""What every suite in this lane needs before it asserts anything.

THREE THINGS, AND THE FIRST ONE IS THE ONE THAT MATTERS.

1. **A SCRATCH DATABASE, NEVER `brain`.** There is no verb that deletes an objective, so a suite
   pointed at the live store leaves rows on the operator's board that only he can clear. The
   refusal below is unconditional and there is no flag that turns it off. The one real end to end
   run into `brain` is the operator's, at gate time, with real content.

2. **The scratch database reconciled to `migrations/` first**, through `engine/tests/`'s own
   `_scratch_preflight`. Borrowed rather than reimplemented: task 0153 measured what a suite run
   against a stale scratch reports, and it was 9 red assertions that were all one missing column
   and none of them about the code under test. A database BEHIND the tree also passes code the
   live store would reject, which is the same skew running quietly the other way.

3. **A store that is down is NOT RUN, not red.** `docs/SUITE-INPUT-RULE.md`: a suite's verdict is
   a claim about the code, and a red that might mean "Postgres is not running" is a red nobody
   can read. The DB-backed suites skip with the reason; the pure ones (`test_payload.py`) need
   none of this and run regardless, which is most of what this door's rules are made of.

NOTHING HERE TRUNCATES ANYTHING. Every row this lane writes carries a per-run marker in its
`source_name`, so the teardown deletes exactly what this run created and cannot empty a sibling
lane's scratch mid-run. `engine/tests`'s `reset()` idiom truncates the shared `brain_scratch`,
which is correct for a suite that owns its store and is hostile in a tree several lanes are
working in at once.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

LANE = Path(__file__).resolve().parents[1]          # intake-service/
ROOT = LANE.parent                                  # the runtime repo root
SCRATCH_SH = ROOT / "engine/bin/scratch-db.sh"

for _p in (str(LANE), str(ROOT), str(ROOT / "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ------------------------------------------------------------------ the database, decided once
#
# The engine lane's idiom for the PAIRING, and NOT for the name. BRAIN_PG_DB follows
# ENGINE_SCRATCH_DB, because setting only one of the two is the defect `_scratch_preflight`
# refuses over: it would reconcile one database and assert against another.
#
# THE NAME IS THIS LANE'S OWN AND NOT THE SHARED `brain_scratch`. Every suite in the estate
# defaults to that one, so it is the database several commanders are asserting against at any
# moment, and `scratch-db.sh create` OPENS WITH A DROP. A per-lane name is the difference
# between a build that costs this lane thirty seconds and one that takes a sibling's suite down
# mid-run. `_suggested_scratch_db` in `engine/tests/_scratch_preflight.py` names the same idiom.
os.environ.setdefault("ENGINE_SCRATCH_DB", "brain_s1_door")
os.environ.setdefault("BRAIN_PG_DB", os.environ["ENGINE_SCRATCH_DB"])

if "brain" in (os.environ["BRAIN_PG_DB"], os.environ["ENGINE_SCRATCH_DB"]):
    raise SystemExit(
        "intake-service/tests: refusing to run against the live store 'brain'. This suite lands "
        "objectives and no verb deletes one, so a run here would leave rows on the operator's "
        "board. Point ENGINE_SCRATCH_DB and BRAIN_PG_DB at a scratch database."
    )

# The suite's own credential, set before `intake_service.app` is imported anywhere. It is not a
# secret and is never the value on the host: `host.require_token()` reads the environment, so a
# suite that inherited a real INTAKE_TOKEN would be asserting against the operator's own token.
os.environ["INTAKE_TOKEN"] = "s1-suite-token-not-a-real-credential"

#: Marks every row this run writes, so the teardown can delete exactly those and nothing else.
RUN_ID = uuid.uuid4().hex[:12]
SOURCE_PREFIX = f"suite:s1-intake/{RUN_ID}"


@pytest.fixture(scope="session")
def token() -> str:
    return os.environ["INTAKE_TOKEN"]


@pytest.fixture(scope="session")
def run_source() -> str:
    """A `source` no other run and no other lane uses. Also the teardown's delete predicate."""
    return SOURCE_PREFIX


@pytest.fixture(scope="session")
def scratch_store():
    """Reconcile the scratch database, or declare NOT RUN. Rule 3 in the module docstring."""
    sys.path.insert(0, str(ROOT / "engine/tests"))
    try:
        from _scratch_preflight import reconcile           # noqa: PLC0415
        reconcile(os.environ["BRAIN_PG_DB"])
    except SystemExit as exc:                              # the preflight's own refusal
        pytest.skip(f"NOT RUN: the scratch database could not be reconciled ({exc}). "
                    f"Start Postgres (`systemctl --user start brain-store`) and re-run.")
    except Exception as exc:                               # noqa: BLE001
        pytest.skip(f"NOT RUN: {exc.__class__.__name__}: {exc}")

    import store                                            # noqa: PLC0415
    try:
        with store.read() as session:
            session.scalar("SELECT 1")
    except Exception as exc:                                # noqa: BLE001
        pytest.skip(f"NOT RUN: this host cannot open a read against "
                    f"{os.environ['BRAIN_PG_DB']} ({exc.__class__.__name__}: {exc}).")

    yield store

    # Exactly this run's rows. `LIKE` on the marker and nothing else: no TRUNCATE, no state
    # filter, no date window that could widen to a sibling's work.
    subprocess.run(
        [str(SCRATCH_SH), "psql", "-q", "-c",
         f"DELETE FROM brain.objective WHERE source_name LIKE '{SOURCE_PREFIX}%'"],
        env={**os.environ, "ENGINE_SCRATCH_DB": os.environ["ENGINE_SCRATCH_DB"]},
        capture_output=True, check=False)


@pytest.fixture(scope="session")
def door(scratch_store):
    """The real app on the real (scratch) store. One per session; Flask apps are stateless here."""
    from intake_service.app import create_app              # noqa: PLC0415
    return create_app()


@pytest.fixture()
def client(door):
    return door.test_client()


@pytest.fixture()
def post(client, token, run_source):
    """POST one payload with a valid credential and this run's source. The suite's normal path."""
    def _post(overrides=None, headers=None, source=None):
        payload = {
            "source": source or f"{run_source}/default",
            "timestamp": "2026-09-01T09:14:22Z",
            "author": "someone@example.com",
            "content": "Can we move Thursday to 3pm?",
        }
        for k, v in (overrides or {}).items():
            if v is None:
                payload.pop(k, None)
            else:
                payload[k] = v
        head = {"Authorization": f"Bearer {token}"}
        head.update(headers or {})
        return client.post("/intake", json=payload, headers=head)
    return _post


@pytest.fixture()
def row(scratch_store):
    """Read one landed objective back, through the public read surface and nothing else."""
    def _row(name):
        with scratch_store.read() as session:
            return session.one(
                "SELECT id, name, state, body, bytes, source_name, source_signature, origin, "
                "       intake_format "
                "  FROM brain.objective WHERE name = %s", (name,))
    return _row
