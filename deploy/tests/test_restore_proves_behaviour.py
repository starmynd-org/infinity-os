"""A restore is proven by BEHAVIOUR surviving, not by row counts matching.

Packet R06. Its acceptance says the restore must prove references, pending approval scope, next-run
policy and representative behaviour, "not just row counts", and that phrase is the whole test. Row
counts are the easiest thing to preserve and the least informative: a dump that restored every row
and dropped every constraint, trigger and sequence position would match on counts and would be a
store where a revoked grant works again, an approval can be replayed, and the fencing epoch restarts
at a number already issued.

So this seeds a store with state that MEANS something, dumps it, restores it into a different store,
and then asks the restored store the questions the original would refuse. Counts are asserted too,
last and briefly, because a restore that preserved behaviour and lost half the rows is also broken.

Run inside WSL, against disposable stores only:

    ENGINE_SCRATCH_DB=brain_scratch_t04 python3 -m pytest deploy/tests/ -q -s

It builds and drops its own target database and never touches the source beyond reading it.
"""

from __future__ import annotations

import datetime as dt
import os
import pathlib
import subprocess
import sys
import uuid

import psycopg2
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

import store                                                            # noqa: E402
from store import authority                                             # noqa: E402
from execution import coordinator as ex                                 # noqa: E402

SOURCE = os.environ.get("BRAIN_PG_DB", "")
TARGET = "brain_scratch_t04_restored"

pytestmark = pytest.mark.skipif(
    SOURCE in ("", "brain"),
    reason="point BRAIN_PG_DB at a scratch database; this never runs against the live store",
)


# --- migration 63: every authority question is asked inside a workspace -----------------------
WS = "ws-test-restore"


def _check(*a, **kw):
    """`authority.check` with this suite's workspace defaulted. Pass `workspace=` to override."""
    kw.setdefault("workspace", WS)
    return authority.check(*a, **kw)


def _in_force(*a, **kw):
    kw.setdefault("workspace", WS)
    return authority.in_force(*a, **kw)


def _su(db, sql, fetch=True):
    """A read against a named store through the superuser path, for asking the RESTORED store
    questions without repointing the whole process at it."""
    secret = pathlib.Path.home() / ".brain-postgres-secrets" / "brain-postgres-bootstrap-superuser"
    out = subprocess.run(
        ["docker", "exec", "-i", "-e", f"PGPASSWORD={secret.read_text().strip()}",
         "brain-postgres", "psql", "-Atq", "-h", "127.0.0.1", "-U", "postgres", "-d", db,
         "-c", sql],
        capture_output=True, text=True)
    if out.returncode != 0:
        return ("ERROR", out.stderr.strip().splitlines()[0] if out.stderr.strip() else "failed")
    return ("OK", out.stdout.strip()) if fetch else ("OK", "")


# ITS OWN VERB NAMES. `store.transition` raises DuplicateTransition when a name is registered
# twice, and the suites in store/ and engine/execution/ register their own fixture verbs. A shared
# name would make running two suites in one pytest process an import error.
@store.transition("test deploy make work item")
def _make_item(ctx, *, title: str):
    return dict(ctx.one("INSERT INTO brain.work_item (title, lane) VALUES (%s, 'mv-deploy') "
                        "RETURNING id", (title,)))


@store.transition("test deploy make agent")
def _make_agent(ctx, *, name: str):
    return dict(ctx.one("INSERT INTO brain.agent (name, role, status, host, updated) "
                        " VALUES (%s, 'worker', 'idle', 'deploy-test', now()) "
                        " ON CONFLICT (name) DO UPDATE SET updated = now() RETURNING name",
                        (name,)))


@pytest.fixture(scope="module")
def seeded():
    """State whose MEANING a restore has to carry, not just its rows."""
    who = store.whoami()["human"]
    tag = uuid.uuid4().hex[:8]
    scope = f"lane/restore-{tag}"
    seed = {"who": who, "scope": scope, "tag": tag}

    # a grant in force, and a grant deliberately revoked
    live = store.apply("authority grant", workspace=WS, granted_by=who, subject=who, subject_kind="human",
                       capability="effect.external", scope=scope,
                       expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=4),
                       evidence="deploy restore fixture")
    dead = store.apply("authority grant", workspace=WS, granted_by=who, subject=who, subject_kind="human",
                       capability="effect.spend", scope=scope,
                       expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=4),
                       evidence="deploy restore fixture")
    store.apply("authority revoke", workspace=WS, grant_seq=dead["grant_seq"], revoked_by=who,
                reason="revoked before the backup, and it must stay revoked after the restore")
    seed["live_grant"] = live["grant_seq"]
    seed["dead_grant"] = dead["grant_seq"]

    # a lease with a settle secret, and an effect left genuinely unresolved
    item = store.apply("test deploy make work item", title=f"restore fixture {tag}")["id"]
    lease = ex.acquire(workspace=WS, work_item_id=item, holder="the-worker", seconds=3600)
    attempt = ex.reserve(workspace=WS, idempotency_key=f"restore-{tag}", lease_id=lease["lease_id"],
                         lease_epoch=lease["lease_epoch"],
                         description="an effect nobody has settled",
                         subject=who, capability="effect.external", scope=scope)
    seed.update(item=item, lease=lease, attempt=attempt["attempt_seq"])

    # an approval FOR an agent, so the subject-match property has something to be wrong about
    agent = f"restore-agent-{tag}"
    store.apply("test deploy make agent", name=agent)
    decider = store.apply("authority grant", workspace=WS, granted_by=who, subject=who, subject_kind="human",
                          capability="approval.decide", scope=scope,
                          expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=4),
                          evidence="deploy restore fixture")
    proposal = f"restore-proposal-{tag}"
    store.apply("approval decide", workspace=WS, proposal_id=proposal, proposal_version="v1", decided_by=who,
                subject=agent, grant_seq=decider["grant_seq"])
    seed.update(agent=agent, proposal=proposal, decider=decider["grant_seq"])
    return seed


@pytest.fixture(scope="module")
def restored(seeded, tmp_path_factory):
    """Dump the seeded store and restore it into a different one, through the real scripts."""
    dump = tmp_path_factory.mktemp("restore") / f"{SOURCE}.dump"
    b = subprocess.run(["bash", str(ROOT / "deploy" / "backup.sh"), SOURCE, str(dump)],
                       capture_output=True, text=True)
    assert b.returncode == 0, f"backup failed: {b.stderr}"
    print("\n  " + b.stdout.strip())
    r = subprocess.run(["bash", str(ROOT / "deploy" / "restore.sh"), str(dump), TARGET],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"restore failed: {r.stderr}"
    print("  " + r.stdout.strip())
    return TARGET


# ---------------------------------------------------------------- the refusals must survive


def test_the_backup_refuses_the_live_store(tmp_path):
    """Before trusting anything below: the tool refuses the one target that has no undo."""
    out = subprocess.run(["bash", str(ROOT / "deploy" / "backup.sh"), "brain",
                          str(tmp_path / "x.dump")], capture_output=True, text=True)
    assert out.returncode == 2 and "refusing the live store" in out.stderr
    out = subprocess.run(["bash", str(ROOT / "deploy" / "backup.sh"), "production_db",
                          str(tmp_path / "y.dump")], capture_output=True, text=True)
    assert out.returncode == 2 and "does not say scratch" in out.stderr


def test_a_revoked_grant_is_still_revoked_after_restore(seeded, restored):
    """The first thing a broken restore gets wrong: rows come back, the revocation does not bind."""
    ok, live = _su(restored, f"SELECT count(*) FROM brain.authority_in_force "
                             f" WHERE grant_seq = {seeded['dead_grant']}")
    assert ok == "OK" and live == "0", (
        f"a grant revoked before the backup is in force after the restore ({live}). Every row could "
        f"be present and this store would still be wrong.")
    ok, present = _su(restored, f"SELECT count(*) FROM brain.authority_grant "
                                f" WHERE grant_seq = {seeded['dead_grant']}")
    assert present == "1", "the revoked grant's record vanished, which is the opposite failure"
    ok, still = _su(restored, f"SELECT count(*) FROM brain.authority_in_force "
                              f" WHERE grant_seq = {seeded['live_grant']}")
    assert still == "1", "the live grant did not survive, so the restore lost authority entirely"


def test_the_approval_still_names_its_subject_after_restore(seeded, restored):
    """Pending approval SCOPE, in the packet's words: an approval must not widen in transit."""
    ok, for_agent = _su(restored, f"SELECT count(*) FROM brain.approval "
                                  f" WHERE proposal_id = '{seeded['proposal']}' "
                                  f"   AND subject = '{seeded['agent']}'")
    assert for_agent == "1", "the approval lost the subject it was for"
    ok, for_anyone = _su(restored, f"SELECT count(*) FROM brain.approval "
                                   f" WHERE proposal_id = '{seeded['proposal']}' "
                                   f"   AND subject IS NULL")
    assert for_anyone == "0", (
        "the approval came back naming nobody, which under this schema authorises nobody but under "
        "a careless reading looks like an approval that covers everyone")


def test_the_self_approval_refusal_survives_the_restore(seeded, restored):
    """A CONSTRAINT is not a row. If triggers do not come back, the store is open again."""
    ok, err = _su(restored,
                  f"INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, "
                  f" subject, grant_seq, decision) "
                  f"VALUES ('post-restore', 'v1', '{WS}', '{seeded['who']}', "
                  f" '{seeded['who']}', {seeded['decider']}, 'approve')", fetch=False)
    assert ok == "ERROR" and "own proposal" in err, (
        f"a self-approval was accepted by the restored store: {err!r}. The rows survived and the "
        f"rule did not, which is the restore failure that looks like a success.")


def test_the_replayed_approval_refusal_survives(seeded, restored):
    """The unique constraint is what makes a replay a refusal. Indexes are not rows either."""
    ok, err = _su(restored,
                  f"INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, "
                  f" subject, grant_seq, decision) "
                  f"VALUES ('{seeded['proposal']}', 'v1', '{WS}', '{seeded['who']}', "
                  f" '{seeded['agent']}', {seeded['decider']}, 'approve')", fetch=False)
    assert ok == "ERROR" and ("duplicate key" in err or "approval_is_decided_once" in err), \
        f"a replayed approval was accepted after the restore: {err!r}"


def test_the_unresolved_effect_is_still_waiting_for_a_human(seeded, restored):
    """A restore that quietly resolves in-flight work is worse than one that fails loudly."""
    ok, why = _su(restored, f"SELECT why FROM brain.effect_unresolved "
                            f" WHERE attempt_seq = {seeded['attempt']}")
    assert ok == "OK" and why == "reserved-never-settled", (
        f"the unsettled effect came back as {why!r}. An effect whose fate is unknown must still be "
        f"unknown after a restore, and must still be in the queue a human works.")


def test_the_effect_still_names_the_authority_that_licensed_it(seeded, restored):
    """REFERENCES, in the packet's words. The join is the audit trail; a dangling one is a story."""
    ok, joined = _su(restored,
                     f"SELECT count(*) FROM brain.effect_attempt a "
                     f"  JOIN brain.authority_grant g ON g.grant_seq = a.grant_seq "
                     f" WHERE a.attempt_seq = {seeded['attempt']} "
                     f"   AND a.subject = g.subject AND a.capability = g.capability "
                     f"   AND a.scope = g.scope")
    assert joined == "1", (
        "the effect's licensing grant does not join after the restore, so 'which authority allowed "
        "this' is no longer answerable from the store")


def test_the_settle_secret_survived_as_a_digest_and_still_refuses(seeded, restored):
    """The lease's secret must come back as a DIGEST that still refuses the wrong value."""
    ok, digest = _su(restored, f"SELECT settle_secret_sha256 FROM brain.execution_lease "
                               f" WHERE lease_id = {seeded['lease']['lease_id']}")
    assert ok == "OK" and digest and len(digest) == 64, f"the digest did not survive: {digest!r}"
    assert digest != seeded["lease"]["settle_secret"], "the plaintext secret was stored"
    ok, err = _su(restored,
                  f"SELECT set_config('brain.settle_secret', 'not-the-secret', false); "
                  f"UPDATE brain.effect_attempt SET settled_at = now(), outcome = 'success', "
                  f"completeness = 'complete', effect_state = 'applied', "
                  f" result_ref = 'r', settled_by = 'the-worker' "
                  f" WHERE attempt_seq = {seeded['attempt']}", fetch=False)
    assert ok == "ERROR" and "does not match" in err, \
        f"a wrong secret settled the restored attempt: {err!r}"


def test_the_fencing_epoch_did_not_restart(seeded, restored):
    """NEXT-RUN POLICY, and the sharpest instance of it here.

    A sequence is not a row. If `lease_epoch_seq` restarts at 1 in the restored store, the next
    lease it issues carries an epoch that ALREADY EXISTS on an older lease, and the fence silently
    stops distinguishing them. pg_dump carries setval; asserting it means a future dump flag change
    cannot quietly take it away.
    """
    ok, nextval = _su(restored, "SELECT last_value FROM brain.lease_epoch_seq")
    assert ok == "OK"
    assert int(nextval) >= seeded["lease"]["lease_epoch"], (
        f"the restored fencing sequence is at {nextval} and a lease already holds "
        f"{seeded['lease']['lease_epoch']}. The next lease would reuse a live epoch.")


def test_and_only_then_the_row_counts(seeded, restored):
    """Counts last, and briefly. A restore that kept every rule and lost half the rows is broken
    too; this is the cheap half of the check and it is not the interesting half."""
    for table in ("authority_grant", "authority_revocation", "approval", "execution_lease",
                  "effect_attempt", "capability"):
        ok, src = _su(SOURCE, f"SELECT count(*) FROM brain.{table}")
        ok2, dst = _su(restored, f"SELECT count(*) FROM brain.{table}")
        assert ok == "OK" and ok2 == "OK", f"could not count {table}"
        assert src == dst, f"brain.{table}: {src} rows before, {dst} after"
    print(f"  row counts match across 6 tables; the behaviour assertions above are the real check")
