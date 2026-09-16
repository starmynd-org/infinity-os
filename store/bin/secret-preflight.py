#!/usr/bin/env python3
"""Every secret reference this runtime resolves, derived from the code and the database.

**This file carries no count.** It carries the three rules that produce one, because the number
is what went wrong: `store/SECRETS.md` said "five references plus one per listener" while
`store/session.py` had held six fixed references since migration 20, and a staged VPS cutover
inherited the five. Provisioning five leaves `brain-postgres-subscriber-operator-paging` absent,
`dsn()` fails closed for an unprovisioned subscriber by design, and the paging listener cannot
authenticate at all -- on the host the move existed to fix. A number written down in one place and
read in three is exactly the thing that gets trusted without being counted.

So the required set is DERIVED, every run:

1. One per entry in `store.session.ROLES`, as `store.session.SECRET_REFS` already builds it. Add
   a role there and it appears here with no edit to this file.
2. `brain-postgres-bootstrap-superuser`, found by reading the wrapper scripts that resolve it
   (`store/bin/*.sh`) rather than by being named here. It is not in `ROLES` because no Python
   caller opens it: the container takes it as `POSTGRES_PASSWORD` and the backup job as
   `PGPASSWORD`, so a code-only enumeration misses it, which is how a set of six becomes a set of
   five in a document.
3. One per listener, from `brain.subscriber_role` in the target database -- the mapping that
   decides which login is which subscriber. A listener with a mapping and no credential is the
   failure this exists to name. The credential backend is also read, so a credential with no
   mapping (a listener provisioned into another database, or one retired) is reported too, in its
   own column, never silently counted as satisfied.
4. One per NAMED human, from `brain.human_role` in the target database, `brain_operator`
   excluded because rule 1 already carries it. Added 2026-08-27 with the admin verb group, and
   the reason is rule 3's exactly: the commander's decision of that day is one Postgres login per
   human with a stated ceiling of twelve, so a mapping is what decides which login is which
   human, and a human with a mapping and no credential cannot authenticate at all. MEASURED
   before this rule existed: a freshly provisioned `brain_human_lanef_test`, mapped in
   `brain.human_role` on the target database, was reported in the "in the backend and NOT
   required" column -- the same shape as the V7 undercount this file was written about, one
   identity kind later.

Exit 0 when every required reference resolves. Exit 1 naming the missing ones. Values are never
read, printed, or written; only names, presence and modes.

    store/bin/secret-preflight.py --db brain
    store/bin/secret-preflight.py --db brain --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from store.session import (ROLES, SECRET_REFS, _secret_backend,  # noqa: E402
                           human_role_name, human_secret_ref, subscriber_role_name)

#: Refs the shell wrappers resolve that no Python caller does. Discovered by reading them, not
#: listed here: `secret brain-postgres-<something>` is the one spelling all of them use.
_SHELL_SECRET = re.compile(r"secret\s+(brain-postgres-[a-z0-9-]+)")


def shell_resolved_refs() -> dict:
    """Reference id -> the wrapper scripts that resolve it. Read from the scripts themselves."""
    found: dict[str, list[str]] = {}
    for script in sorted((REPO / "store" / "bin").glob("*.sh")):
        text = script.read_text(encoding="utf-8", errors="replace")
        for ref in _SHELL_SECRET.findall(text):
            # `brain-postgres-subscriber-$SUB` and friends never match: the regex wants a literal
            # id, and a script that builds one at runtime is covered by rule 3 instead.
            found.setdefault(ref, [])
            rel = str(script.relative_to(REPO))
            if rel not in found[ref]:
                found[ref].append(rel)
    return found


def _one_column(db: str, container: str, secrets: Path, sql: str, what: str) -> tuple[list, str]:
    """One column out of the target database over the bootstrap connection. ([], reason) on any
    failure, never a partial answer, because a rule that half-ran is an undercount wearing a
    green tick."""
    su = secrets / "brain-postgres-bootstrap-superuser"
    if not su.is_file():
        return [], (f"cannot read {what}: brain-postgres-bootstrap-superuser is absent, so this "
                    f"run cannot enumerate from the database")
    try:
        out = subprocess.run(
            ["docker", "exec", "-i", "-e", "PGPASSWORD=" + su.read_text(encoding="utf-8").strip(),
             container, "psql", "-tA", "-h", "127.0.0.1", "-U", "postgres", "-d", db, "-c", sql],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"cannot read {what}: {exc.__class__.__name__}"
    if out.returncode != 0:
        return [], (f"cannot read {what}: "
                    + (out.stderr.strip().splitlines() or ["psql failed"])[-1])
    return [s for s in (line.strip() for line in out.stdout.splitlines()) if s], ""


def mapped_subscribers(db: str, container: str, secrets: Path) -> tuple[list, str]:
    """Subscriber slugs with a login mapping in `db`. Returns ([], reason) when unreadable."""
    return _one_column(db, container, secrets,
                       "SELECT subscriber FROM brain.subscriber_role ORDER BY subscriber",
                       "brain.subscriber_role")


def mapped_humans(db: str, container: str, secrets: Path) -> tuple[list, str]:
    """Human slugs with a login mapping in `db`, brain_operator excluded (rule 1 carries it).

    A store below migration 20 has no `brain.human_role` at all, which is not a failure to
    report: there are no named humans on it, so the required set is correctly the shorter one.
    """
    rows, why = _one_column(
        db, container, secrets,
        "SELECT human FROM brain.human_role WHERE role_name <> 'brain_operator' ORDER BY human",
        "brain.human_role")
    if why and "does not exist" in why:
        return [], ""
    return rows, why


def audit(db: str, container: str) -> dict:
    secrets = _secret_backend()
    present = set()
    if secrets.is_dir():
        present = {p.name for p in secrets.iterdir() if p.is_file() and p.stat().st_size > 0}

    required = []
    for role in ROLES:
        required.append({"ref": SECRET_REFS[role], "resolved_by": "store/session.py:dsn(%r)" % role,
                         "rule": "1 role", "breaks": _role_breakage(role)})

    for ref, scripts in sorted(shell_resolved_refs().items()):
        if any(r["ref"] == ref for r in required):
            continue
        required.append({"ref": ref, "resolved_by": ", ".join(scripts), "rule": "2 shell",
                         "breaks": "the wrapper that resolves it dies before it acts; for "
                                   "brain-postgres-bootstrap-superuser that is the container "
                                   "standup, every migration and the backup job"})

    subs, why = mapped_subscribers(db, container, secrets)
    for slug in subs:
        required.append({
            "ref": f"brain-postgres-subscriber-{slug}",
            "resolved_by": f"store/session.py:dsn('subscriber', {slug!r}) -> "
                           f"{subscriber_role_name(slug)}",
            "rule": "3 listener",
            "breaks": f"the {slug} listener raises StoreConfigError at start and connects to "
                      f"nothing; dsn() refuses the shared brain_subscriber login on purpose",
        })

    hums, why_h = mapped_humans(db, container, secrets)
    for slug in hums:
        required.append({
            "ref": human_secret_ref(slug),
            "resolved_by": f"store/session.py:dsn('operator', human={slug!r}) -> "
                           f"{human_role_name(slug)}",
            "rule": "4 human",
            "breaks": f"the {slug} login raises StoreConfigError and connects to nothing; dsn() "
                      f"refuses to fall back to brain_operator on purpose, because one human "
                      f"writing as another is the self-service identity migration 10 removed",
        })

    for r in required:
        r["present"] = r["ref"] in present
        p = secrets / r["ref"]
        r["mode"] = oct(p.stat().st_mode & 0o777)[2:] if p.is_file() else None

    unmapped = sorted(present - {r["ref"] for r in required})
    return {
        "backend": str(secrets),
        "backend_mode": oct(secrets.stat().st_mode & 0o777)[2:] if secrets.is_dir() else None,
        "database": db,
        "required": required,
        "required_count": len(required),
        "present_count": sum(1 for r in required if r["present"]),
        "missing": [r["ref"] for r in required if not r["present"]],
        "in_backend_count": len(present),
        "unrequired_in_backend": unmapped,
        "subscriber_enumeration_failed": why,
        "human_enumeration_failed": why_h,
    }


def _role_breakage(role: str) -> str:
    return {
        "owner": "migrations, the retention sweep and the rollup cannot run",
        "producer": "no event can be emitted; every fabric producer fails closed",
        "subscriber": "the group login is unopenable (no listener uses it since migration 10)",
        "runtime": "the console, the engine and every verb fail closed: nothing works",
        "operator": "the human write door shuts; work_item.actor_type='human' cannot be written "
                    "and store/session.py refuses to fall back to brain_runtime",
    }.get(role, "the role cannot authenticate")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("BRAIN_PG_DB", "brain"))
    ap.add_argument("--container", default=os.environ.get("BRAIN_PG_CONTAINER", "brain-postgres"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    r = audit(a.db, a.container)
    if a.json:
        print(json.dumps(r, indent=2, sort_keys=True))
    else:
        print(f"secret backend: {r['backend']} (mode {r['backend_mode']}), "
              f"database {r['database']}")
        print(f"REQUIRED {r['required_count']}   present {r['present_count']}   "
              f"missing {len(r['missing'])}   files in backend {r['in_backend_count']}")
        for label, key in (("Listener", "subscriber_enumeration_failed"),
                           ("Human", "human_enumeration_failed")):
            if r[key]:
                print(f"  WARNING: {r[key]}")
                print(f"  {label} references are therefore UNDER-counted, not absent. Do not "
                      f"read this run's total as the required set.")
        print("")
        for x in r["required"]:
            mark = "ok     " if x["present"] else "MISSING"
            mode = f" 0{x['mode']}" if x["mode"] else ""
            print(f"  [{mark}] {x['ref']}{mode}")
            print(f"            resolved by: {x['resolved_by']}")
            if not x["present"]:
                print(f"            absent means: {x['breaks']}")
        if r["unrequired_in_backend"]:
            print("")
            print("  in the backend and NOT required by this database (a listener mapped "
                  "elsewhere, or a retired one):")
            for n in r["unrequired_in_backend"]:
                print(f"    {n}")
    sys.stdout.flush()
    if r["missing"]:
        print("", file=sys.stderr)
        print("FAIL: %d required secret reference(s) do not resolve: %s"
              % (len(r["missing"]), ", ".join(r["missing"])), file=sys.stderr)
        return 1
    if r["subscriber_enumeration_failed"] or r["human_enumeration_failed"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
