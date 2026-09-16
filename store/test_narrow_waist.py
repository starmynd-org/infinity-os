"""The tests that make the narrow waist a property rather than a promise.

Run against the live local store:

    python3 -m store.test_narrow_waist

Every assertion here is about what the module and the database REFUSE. A test suite that only
checks the happy path would pass just as well against a library that lets four lanes each write
their own transitions, which is the exact failure this module exists to prevent.
"""

from __future__ import annotations

import sys

import psycopg2

import store
from store import reads
from store.session import ReadOnlyViolation, StoreConfigError, read
from store.transitions import DuplicateTransition, UnknownTransition, _scratch_registry

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
    except AssertionError as exc:
        FAIL.append(f"{name}: {exc}")
        print(f"FAIL  {name}\n      {exc}")
    except Exception as exc:  # noqa: BLE001 - an unexpected error is a failure, loudly
        FAIL.append(f"{name}: unexpected {exc.__class__.__name__}: {exc}")
        print(f"FAIL  {name}\n      unexpected {exc.__class__.__name__}: {exc}")
    else:
        PASS.append(name)
        print(f"ok    {name}")


# ---------------------------------------------------------------- the read path cannot write

def test_read_refuses_insert():
    with read() as s:
        try:
            s.query("INSERT INTO brain.work_item (title, lane) VALUES ('x','store')")
        except ReadOnlyViolation:
            return
        raise AssertionError("store.read() accepted an INSERT. The narrow waist is gone.")


def test_read_refuses_update():
    with read() as s:
        try:
            s.query("UPDATE brain.work_item SET title = 'x'")
        except ReadOnlyViolation:
            return
        raise AssertionError("store.read() accepted an UPDATE.")


def test_read_session_exposes_no_connection():
    with read() as s:
        leaked = [a for a in ("connection", "conn", "_conn", "copy_expert", "callproc")
                  if hasattr(s, a)]
        assert not leaked, f"the read session leaks {leaked}, which is a route back to a write"
        assert set(s.__slots__) == {"_cur"}, f"unexpected read-session state: {s.__slots__}"


def test_store_exports_no_raw_write_helper():
    banned = [n for n in ("execute", "insert", "update", "delete", "cursor", "connect", "commit")
              if hasattr(store, n)]
    assert not banned, (
        f"store exports {banned}. A raw write helper on the package is how four lanes each end up "
        f"writing their own transitions."
    )


# ---------------------------------------------------------------- one transition per state change

def test_duplicate_registration_is_refused():
    with _scratch_registry():
        @store.transition("test.dup")
        def _a(ctx):
            return 1

        try:
            @store.transition("test.dup")
            def _b(ctx):
                return 2
        except DuplicateTransition:
            return
        raise AssertionError("two functions registered the same verb. Two lanes can each define "
                             "`done` and neither finds out.")


def test_unknown_verb_is_refused():
    try:
        store.apply("test.no.such.verb")
    except UnknownTransition:
        return
    raise AssertionError("apply() accepted an unregistered verb")


# ---------------------------------------------------------------- one verb, one transaction

def test_transition_rolls_back_entirely_on_failure():
    """A ported `done` is a row update plus a thread event. Half of that must never survive."""
    with _scratch_registry():
        @store.transition("test.partial")
        def _partial(ctx):
            row = ctx.one(
                "INSERT INTO brain.work_item (title, lane) VALUES ('rollback probe','store') "
                "RETURNING id")
            ctx.thread(row["id"], "note", "this thread event must not survive either")
            raise RuntimeError("the second write failed")

        before = _count("brain.work_item")
        threads_before = _count("brain.thread")
        try:
            store.apply("test.partial", actor="test")
        except RuntimeError:
            pass
        else:
            raise AssertionError("the transition swallowed its own failure")
        assert _count("brain.work_item") == before, "the row survived a failed transition"
        assert _count("brain.thread") == threads_before, "the thread event survived"


def test_transition_commits_all_writes_together():
    with _scratch_registry():
        @store.transition("test.whole")
        def _whole(ctx, title):
            row = ctx.one(
                "INSERT INTO brain.work_item (title, lane) VALUES (%s,'store') RETURNING id",
                (title,))
            ctx.thread(row["id"], "post", title)
            ctx.execute(
                "INSERT INTO brain.artifact (work_item_id, path, kind, exists_at_record) "
                "VALUES (%s, '/tmp/probe', 'created', false)", (row["id"],))
            return row["id"]

        item = store.apply("test.whole", "atomicity probe", actor="test")
        assert reads.work_item(item), "the row did not land"
        assert len(reads.thread(item)) == 1, "the thread event did not land with it"
        assert len(reads.artifacts(item)) == 1, "the artifact did not land with it"
        _cleanup(item)


# ---------------------------------------------------------------- the grants are the second gate

def test_runtime_role_cannot_insert_an_event():
    """The registration and the grant have to agree, and neither trusts the other."""
    with _scratch_registry():
        @store.transition("test.sneaky_event", role="runtime")
        def _sneaky(ctx):
            ctx.execute("INSERT INTO brain.event (type, external, canon_touching) "
                        "VALUES ('test.sneak', false, false)")

        try:
            store.apply("test.sneaky_event")
        except psycopg2.errors.InsufficientPrivilege:
            return
        raise AssertionError(
            "a transition registered under `runtime` inserted an event. The producer role's "
            "INSERT-only grant is decorative.")


def test_subscriber_cannot_write_event_through_the_module():
    with read("subscriber") as s:
        try:
            s.query("INSERT INTO brain.event (type, external, canon_touching) "
                    "VALUES ('x', false, false)")
        except (ReadOnlyViolation, psycopg2.errors.InsufficientPrivilege):
            return
        raise AssertionError("the subscriber wrote to event")


def test_secret_failure_is_closed():
    import os
    saved = os.environ.get("BRAIN_SECRET_DIR")
    os.environ["BRAIN_SECRET_DIR"] = "/nonexistent/secret/backend"
    try:
        with read():
            pass
    except StoreConfigError:
        return
    finally:
        if saved is None:
            os.environ.pop("BRAIN_SECRET_DIR", None)
        else:
            os.environ["BRAIN_SECRET_DIR"] = saved
    raise AssertionError("an unresolvable secret did not fail closed")


# ---------------------------------------------------------------- helpers

def _count(table: str) -> int:
    with read() as s:
        return int(s.scalar(f"SELECT count(*) FROM {table}"))


def _cleanup(item_id: str):
    with _scratch_registry():
        @store.transition("test.cleanup", role="owner")
        def _c(ctx, iid):
            ctx.execute("DELETE FROM brain.work_item WHERE id = %s", (iid,))
        store.apply("test.cleanup", item_id)


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            check(name, fn)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
