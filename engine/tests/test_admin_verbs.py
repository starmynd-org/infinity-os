#!/usr/bin/env python3
"""The admin verb group's refusals, each one watched FAILING and then PASSING.

Task 0385, lane F. The verbs are in `engine/swarm_engine/admin.py` and their schema is
`migrations/0040_admin_config_provenance.sql` and `migrations/0041_human_roster_and_ceiling.sql`.

WHY EVERY TEST HERE IS ABOUT A REFUSAL. The happy path of "set a config key" is one INSERT and it
would pass just as well against a version of this that edited `config.json` behind the store's
back, which is the second writer the whole architecture exists to prevent. What separates the two
is what the system says NO to, and to whom.

  test_a_config_write_from_the_runtime_login_is_refused    migration 40's trigger, with the
      Python check bypassed entirely, so what is measured is the DATABASE and not this repo's
      opinion of it. The Python guard is tested separately, because a guard tested only through
      the layer above it is a guard nobody has watched fail.
  test_set_by_is_the_login_not_the_argument                the caller passes 'i-said-so' and the
      row reads 'operator'. Attribution that the changer can spell is not attribution.
  test_the_trail_is_append_only_for_every_login            including brain_owner, which the GRANT
      cannot refuse because owner holds ALL. Two mechanisms, and the test names which one fired.
  test_the_human_ceiling_refuses_a_raw_superuser_insert    the ceiling is a decision boundary
      (row 0386 decision 2) and the route a hurry takes is a hand-written INSERT, not the verb.
  test_a_store_override_is_actually_in_force               the difference between recorded and in
      force is where this class of tool lies. `signal_weights(effective())` moves; the file does
      not.
  test_swarm_config_still_prints_resolved_config           the property the lane brief says this
      tool already gets right, pinned so a later change to `--layers` cannot regress it.
  test_refused_keys_are_refused_by_name                    permission_mode and bypassPermissions,
      borrowed from fleet_config rather than restated, so one edit changes one policy.
  test_an_unknown_key_is_refused_without_new_key           an override on a typo is recorded,
      looks applied and reaches nothing, which is the worst outcome available here.
  test_a_roster_entry_that_would_never_claim_is_refused    an empty lane list on a terminal role.
  test_no_admin_verb_prints_a_secret_value                 structural: the group's source is
      searched for a call that could return one.
  test_every_admin_verb_honours_json                       uniform --json is a rule in this group
      and 26 of the 48 older verbs accept the flag and ignore it.

Run: python3 engine/tests/test_admin_verbs.py     (scratch database, never `brain`)

NOT RUN rather than green, on two stores this cannot speak for: one below ledger 41, and one on a
host with no operator credential. A suite that reports a pass on a database it could not exercise
is the failure mode `store/SECRETS.md` calls the V7 undercount, one layer up.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("ENGINE_SCRATCH_DB", "brain_scratch"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile              # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])
os.environ.pop("SWARM_PARENT_TASK", None)
os.environ.pop("SWARM_AGENT", None)
os.environ.pop("BRAIN_HUMAN", None)

import psycopg2                                                 # noqa: E402
import store                                                    # noqa: E402
from store.session import dsn                                   # noqa: E402
from swarm_engine import admin                                  # noqa: E402
from swarm_engine.config import config, effective               # noqa: E402
from swarm_engine.signals import signal_weights                 # noqa: E402
from swarm_engine.transitions import VerbError                  # noqa: E402

DB = os.environ["BRAIN_PG_DB"]
PASS, FAIL, SKIP = 0, 0, 0

#: Every key this suite writes. Cleaned before and after, by the owner, because `config unset`
#: leaves the row on purpose and a second run would then read the first run's history.
TEST_KEYS = ("signals.w_urgency", "signals.w_stakes", "signals.w_effort", "lanef_probe")
TEST_AGENTS = ("LANEF1", "LANEF2")


def ok(msg):
    global PASS
    PASS += 1
    print(f"  ok    {msg}")


def bad(msg, detail=""):
    global FAIL
    FAIL += 1
    print(f"  FAIL  {msg}")
    if detail:
        print(f"        {detail}")


def skip(msg):
    global SKIP
    SKIP += 1
    print(f"  NOT RUN  {msg}")


def eq(msg, got, want):
    ok(msg) if got == want else bad(msg, f"wanted [{want}], got [{got}]")


def truth(msg, cond, detail=""):
    ok(msg) if cond else bad(msg, detail)


def _owner(sql, params=None):
    c = psycopg2.connect(**dsn("owner"))
    c.set_session(readonly=False, autocommit=True)
    try:
        with c.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []
    finally:
        c.close()


def _clean():
    _owner("DELETE FROM brain.config_setting WHERE key = ANY(%s) OR key = ANY(%s)",
           (list(TEST_KEYS), list(TEST_AGENTS)))
    _owner("DELETE FROM brain.human_role WHERE human LIKE 'lanef%%'")


def _write(**kw):
    kw.setdefault("as_operator", True)
    return store.apply("admin config set", **kw)


# ------------------------------------------------------------------ preconditions


def _ledger_ok() -> bool:
    try:
        with store.read() as s:
            s.scalar("SELECT count(*) FROM brain.config_setting")
            s.scalar("SELECT brain.human_login_ceiling()")
        return True
    except Exception:                                            # noqa: BLE001
        return False


def _operator_credential() -> bool:
    try:
        dsn("operator")
        return True
    except Exception:                                            # noqa: BLE001
        return False


# ------------------------------------------------------------------ the tests


def test_a_config_write_from_the_runtime_login_is_refused():
    """The DATABASE refuses brain_runtime, with the Python guard bypassed entirely."""
    c = psycopg2.connect(**dsn("runtime"))
    c.set_session(readonly=False, autocommit=False)
    try:
        with c.cursor() as cur:
            cur.execute("INSERT INTO brain.config_setting (scope, key, value) "
                        "VALUES ('fleet','signals.w_stakes','9.0')")
        c.commit()
        bad("brain_runtime is refused a raw config write", "the INSERT landed")
    except psycopg2.errors.InsufficientPrivilege as e:
        c.rollback()
        truth("brain_runtime is refused a raw config write by the server",
              "not a human login" in str(e), f"raised: {str(e).splitlines()[0]}")
    except Exception as e:                                       # noqa: BLE001
        c.rollback()
        bad("brain_runtime is refused a raw config write", f"wrong exception: {e!r}")
    finally:
        c.close()

    # And the same call through the verb, which is the Python half, separately.
    try:
        store.apply("admin config set", key="signals.w_stakes", value="9.0")
        bad("the verb refuses a call that did not open the operator login", "it landed")
    except VerbError as e:
        truth("the verb refuses a call that did not open the operator login",
              "not know as a human" in str(e), f"raised: {e}")


def test_set_by_is_the_login_not_the_argument():
    """A caller cannot spell its own attribution. The trigger overwrites it."""
    c = psycopg2.connect(**dsn("operator"))
    c.set_session(readonly=False, autocommit=False)
    try:
        with c.cursor() as cur:
            cur.execute("INSERT INTO brain.config_setting (scope, key, value, set_by) "
                        "VALUES ('fleet','signals.w_stakes','3.0','i-said-so') "
                        "ON CONFLICT (scope,key) DO UPDATE SET value = EXCLUDED.value, "
                        "set_by = EXCLUDED.set_by")
        c.commit()
        with c.cursor() as cur:
            cur.execute("SELECT set_by FROM brain.config_setting "
                        " WHERE scope='fleet' AND key='signals.w_stakes'")
            got = cur.fetchone()[0]
        eq("set_by is the login's human, not the caller's string", got, "operator")
    finally:
        c.close()

    _write(key="signals.w_stakes", value="3.5", note="from the verb")
    with store.read() as s:
        row = s.one("SELECT changed_by FROM brain.admin_change ORDER BY change_seq DESC LIMIT 1")
    eq("changed_by on the trail row is the login's human too", row["changed_by"], "operator")


def test_the_trail_is_append_only_for_every_login():
    """Two mechanisms, and the owner is the one only a trigger can refuse."""
    _write(key="lanef_probe", value="1", new_key=True, note="append-only probe")
    seq = _owner("SELECT min(change_seq) FROM brain.admin_change")[0][0]

    for role, expect in (("operator", "grant"), ("owner", "trigger")):
        for op in ("UPDATE brain.admin_change SET note = 'rewritten' WHERE change_seq = %s",
                   "DELETE FROM brain.admin_change WHERE change_seq = %s"):
            c = psycopg2.connect(**dsn(role))
            c.set_session(readonly=False, autocommit=False)
            word = op.split()[0]
            try:
                with c.cursor() as cur:
                    cur.execute(op, (seq,))
                c.commit()
                bad(f"{word} on the trail is refused to brain_{role}", "it landed")
            except psycopg2.errors.InsufficientPrivilege as e:
                c.rollback()
                msg = str(e).splitlines()[0]
                fired = "trigger" if "append-only" in msg else "grant"
                truth(f"{word} on the trail is refused to brain_{role} by the {fired}",
                      fired == expect,
                      f"expected the {expect} to fire, {fired} did: {msg}")
            except Exception as e:                               # noqa: BLE001
                c.rollback()
                bad(f"{word} on the trail is refused to brain_{role}", f"wrong exception: {e!r}")
            finally:
                c.close()


def test_the_human_ceiling_refuses_a_raw_superuser_insert():
    """The route a hurry takes is a hand-written INSERT, not the verb, so the gate is a trigger."""
    cap = _owner("SELECT brain.human_login_ceiling()")[0][0]
    have = _owner("SELECT count(*) FROM brain.human_role")[0][0]
    eq("the ceiling is the stated twelve", cap, 12)
    room = cap - have
    for i in range(room):
        _owner("INSERT INTO brain.human_role (role_name, human) VALUES (%s, %s)",
               (f"brain_human_lanef{i}", f"lanef{i}"))
    at_cap = _owner("SELECT count(*) FROM brain.human_role")[0][0]
    eq(f"the roster is filled to the ceiling ({at_cap} of {cap})", at_cap, cap)
    try:
        _owner("INSERT INTO brain.human_role (role_name, human) "
               "VALUES ('brain_human_lanef_over', 'lanef-over')")
        bad("a thirteenth mapping is refused to the bootstrap superuser", "it landed")
    except psycopg2.errors.InsufficientPrivilege as e:
        truth("a thirteenth mapping is refused to the bootstrap superuser",
              "ceiling" in str(e), f"raised: {str(e).splitlines()[0]}")
    # and the verb refuses before anything is minted
    try:
        store.apply("admin human plan", slug="lanef-over", as_operator=True)
        bad("the verb refuses to plan a thirteenth login", "it planned one")
    except VerbError as e:
        truth("the verb refuses to plan a thirteenth login", "ceiling" in str(e), f"raised: {e}")
    _owner("DELETE FROM brain.human_role WHERE human LIKE 'lanef%%'")


def test_a_store_override_is_actually_in_force():
    """Recorded is not the same as in force, and this is the difference."""
    file_w = signal_weights(config())["w_urgency"]
    _write(key="signals.w_urgency", value="7.25", note="in-force probe")
    eff_w = signal_weights(effective())["w_urgency"]
    eq("the file's weight is unchanged", signal_weights(config())["w_urgency"], file_w)
    eq("the effective weight is the store's", eff_w, 7.25)
    truth("the two layers disagree, which is the whole point", eff_w != file_w,
          f"both read {eff_w}; the fixture cannot show a difference")

    # one leaf moves and the other six do not: a whole-subtree override is the config bug that is
    # hardest to see, because the file still says what it always said
    eff = signal_weights(effective())
    fil = signal_weights(config())
    moved = [k for k in eff if eff[k] != fil[k]]
    eq("exactly one of the seven signal weights moved", moved, ["w_urgency"])

    store.apply("admin config unset", key="signals.w_urgency", note="probe done", as_operator=True)
    eq("after unset the file's value stands again",
       signal_weights(effective())["w_urgency"], file_w)
    with store.read() as s:
        n = s.scalar("SELECT count(*) FROM brain.admin_change WHERE key = 'signals.w_urgency'")
    truth(f"the unset is on the trail too ({n} row(s) for that key)", n >= 2,
          "an unset that vanished from the trail is the one change nobody could audit")


def test_swarm_config_still_prints_resolved_config():
    """The property the lane brief says this tool already gets right. Pinned, not assumed."""
    out = subprocess.run([sys.executable, str(ROOT / "engine/bin/swarm"), "config", "--json"],
                         capture_output=True, text=True, env={**os.environ})
    eq("`swarm config --json` exits 0", out.returncode, 0)
    try:
        doc = json.loads(out.stdout)
    except ValueError:
        bad("`swarm config --json` prints one JSON document", out.stdout[:200])
        return
    truth("it is still RESOLVED config: fleet, agents and signals in one document",
          {"fleet", "agents", "signals"} <= set(doc), f"keys: {sorted(doc)}")
    truth("and not a provenance report: no per-key `source` at the top level",
          "source" not in doc and "keys" not in doc,
          "the default output grew provenance, which breaks `swarm config --shell`")


def test_refused_keys_are_refused_by_name():
    """Borrowed from fleet_config, not restated, so one edit changes one policy."""
    from swarm_engine.fleet_config import REFUSED
    truth(f"the refusal list is fleet_config's own ({len(REFUSED)} keys)",
          admin._refused() is REFUSED or admin._refused() == REFUSED,
          "admin.py has its own copy, which will drift")
    for key in ("permission_mode", "bypassPermissions"):
        try:
            _write(key=key, value="x", new_key=True)
            bad(f"{key} is refused", "it landed")
        except VerbError as e:
            truth(f"{key} is refused, with the argument attached",
                  "refusing to set" in str(e) and len(str(e)) > 120,
                  f"raised: {e}")


def test_an_unknown_key_is_refused_without_new_key():
    """An override on a typo is recorded, looks applied and reaches nothing."""
    try:
        _write(key="signals.w_urgencyy", value="3.0")
        bad("a key in neither the carried keys nor the file is refused", "it landed")
    except VerbError as e:
        truth("a key in neither the carried keys nor the file is refused",
              "--new-key" in str(e), f"raised: {e}")
    r = _write(key="lanef_probe", value="2", new_key=True, note="deliberately new")
    truth("--new-key lets the same call through, recorded as new", r["new_key"] is True)


def test_a_roster_entry_that_would_never_claim_is_refused():
    """D00 contract rule 5, asserted where the roster is WRITTEN and not only where it is read."""
    for name, values, why in (
        ("LANEF1", {"role": "terminal", "lanes": []}, "empty lane list"),
        ("LANEF2", {"role": "admiral", "lanes": ["engine"]}, "planner with lanes"),
    ):
        try:
            store.apply("admin agent add", name=name, values=values, as_operator=True)
            bad(f"a roster entry with a {why} is refused", "it landed")
        except VerbError as e:
            truth(f"a roster entry with a {why} is refused", "refusing" in str(e), f"raised: {e}")
    r = store.apply("admin agent add", name="LANEF1",
                    values={"role": "terminal", "lanes": ["engine"]}, as_operator=True)
    eq("a well formed entry lands", r["agent"], "LANEF1")
    names = [a.get("name") for a in effective().get("agents", [])]
    truth("and the new agent is in the roster `swarm-fleet up` reads", "LANEF1" in names,
          f"roster: {names}")
    rem = store.apply("admin agent remove", name="LANEF1", as_operator=True)
    names = [a.get("name") for a in effective().get("agents", [])]
    truth("and removing it takes it back out", "LANEF1" not in names, f"roster: {names}")
    eq("the removal is attributed", rem["set_by"], "operator")


def test_no_admin_verb_prints_a_secret_value():
    """Structural. `resolve_secret` returns a value, so the group must never call it."""
    src = (ROOT / "engine/swarm_engine/admin.py").read_text(encoding="utf-8")
    cli_src = (ROOT / "engine/swarm_engine/cli.py").read_text(encoding="utf-8")
    admin_cli = cli_src.split("# ------------------------------------------------------------------ admin")[-1]
    admin_cli = admin_cli.split("# ------------------------------------------------------------------ parser")[0]
    for name, text in (("admin.py", src), ("the admin CLI block", admin_cli)):
        truth(f"{name} never calls resolve_secret", "resolve_secret" not in text,
              "a verb that resolves a secret is one print away from leaking it")
        truth(f"{name} never reads the secret backend directly", "_secret_backend" not in text)
    # and the one verb that reports on secrets composes the preflight rather than re-reading files
    truth("`admin secret list` composes store/bin/secret-preflight.py",
          "secret-preflight.py" in admin_cli and "audit(" in admin_cli)


def test_every_admin_verb_honours_json():
    """Uniform --json is a rule in this group. 26 of the 48 older verbs accept it and ignore it."""
    from swarm_engine import cli, manifest as manifest_mod
    m = manifest_mod.build(cli.build_parser())
    admin_verbs = [v for v in m["verbs"] if v["verb"].startswith("admin ")]
    ignored = [v["verb"] for v in admin_verbs if not v["json_honoured"]]
    truth(f"{len(admin_verbs) - len(ignored)}/{len(admin_verbs)} admin verbs honour --json",
          not ignored, f"these accept it and ignore it: {ignored}")
    truth(f"the manifest counts every verb ({m['counts']['verbs']}) and is derived, not listed",
          m["counts"]["verbs"] >= 60 and m["counts"]["registered_transitions"] > 0,
          json.dumps(m["counts"]))
    truth("and it reports the older verbs' gap rather than hiding it",
          len(m["json_flag_ignored_by"]) > 0,
          "if this is empty the measurement stopped working, not the gap")


def main():
    print("test_admin_verbs.py  --  the admin verb group's refusals, watched failing and passing")
    print(f"  store: {DB} (scratch)\n")
    if DB == "brain":
        print("REFUSING to run against the live store `brain`. Set ENGINE_SCRATCH_DB.")
        return 1
    if not _ledger_ok():
        skip(f"{DB} is below ledger 41: brain.config_setting or brain.human_login_ceiling() is "
             f"absent. Apply migrations/0040 and 0041. Nothing here is asserted, and this is NOT "
             f"a pass.")
        print("\n0 passed, 0 failed, 1 not run: 0 of 11 test functions were exercised")
        return 0
    if not _operator_credential():
        skip("this host holds no operator credential, so nothing can write as a human. That is a "
             "correct state (on this host nobody is the operator) and it is NOT a pass.")
        print("\n0 passed, 0 failed, 1 not run: 0 of 11 test functions were exercised")
        return 0

    _clean()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"{t.__name__.replace('test_', '')}: "
              f"{t.__doc__.splitlines()[0] if t.__doc__ else ''}")
        try:
            t()
        except Exception:                                        # noqa: BLE001
            import traceback
            bad(f"{t.__name__} raised",
                traceback.format_exc().strip().replace("\n", "\n        "))
        print()
    _clean()
    if PASS + FAIL == 0:                                     # DENOMINATOR
        print("0 comparisons made over 11 test functions. A verdict over an empty set is not a "
              "pass: every test raised before its first assertion, or the collector found none.")
        return 2
    print(f"{PASS} passed, {FAIL} failed, {SKIP} not run   "
          f"({PASS + FAIL} assertions over {len(tests)} test functions on {DB})")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
