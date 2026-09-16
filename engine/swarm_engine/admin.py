"""The admin verb group: config with provenance, the roster, roles and secrets.

**Every write in this file goes through `store.apply`.** That is not a style choice and it is
the whole of the design. An admin CLI that changed a setting by editing `config.json` would be
the second writer `store/narrow_waist.md` exists to prevent: no transaction, no actor, no trail,
and a file three agents are already editing during a build.

## Where a value comes from, and why the file keeps its job

    resolution order:   code default  <  config file  <  store override

The file is not replaced and must not be. `config.py` argues it at length: `config.json` resolves
`{**defaults, **agent}` and PASSES UNKNOWN KEYS THROUGH, which is what let `config_dirs` ship
with no CLI change at all. Moving config into columns would either freeze the key set or
reimplement JSON in SQL. So this module adds a layer on top and folds the two, which is the same
shape `fleet_config.py` already uses for the console's per-agent model picker, one scope wider.

`swarm config` therefore keeps printing RESOLVED config and now genuinely resolves both layers.
`swarm config --layers` is the second question, which nothing could answer before: not what is in
force, but WHERE EACH VALUE CAME FROM. The difference between what is configured and what is in
force is where this class of tool usually lies.

## Attribution comes from the login, never from an argument

`brain.current_human()` reads `session_user`, fixed at authentication and unreachable from SQL.
Migration 40's triggers OVERWRITE `set_by` and `changed_by` with its answer rather than checking
a value the caller passed, which is the shape `queue/schema/0015_recommendation_human_login.sql`
already uses for `decided_by`. A record of who changed config that the changer can spell is not
a record.

The practical consequence: every transition here declares `as_operator=True`, which
`store/transitions.py:_login_for` reads to open the operator's login. Declaring it is not what
makes it true. A process without the operator credential gets `StoreConfigError` from `_connect`,
and a process that somehow connected as `brain_runtime` anyway is refused by the trigger.

## What is NOT in the store, stated rather than quietly split

`CREATE ROLE ... PASSWORD` is not here and cannot be. `store.apply` opens one of the five logins
in `store/session.py:ROLES`, every one of them `NOSUPERUSER NOCREATEDB NOCREATEROLE` by migration
2, and minting a login needs a privilege none of them has and none of them should have. Adding a
superuser connection to `store/` to make provisioning "go through the waist" would open a far
larger hole than the one this lane closes: it would put a route to `CREATE ROLE` inside the
module every surface imports.

So `admin human provision` is honest about being two acts rather than pretending to be one:

  1. `admin human plan`     one `store.apply` transaction. Validates, checks the ceiling, and
                            writes the INTENT to `brain.admin_change` with the human and the time.
  2. the privileged half    `store/bin/provision-human.sh`, run as the bootstrap superuser, which
                            mints the role and writes the credential into the 0600 backend. It
                            touches no table this module owns.
  3. `admin human confirm`  a second `store.apply`, recording the OUTCOME.

Two transactions, and the gap between them is real: an interrupted run leaves an intent with no
outcome. That is visible in `swarm admin history` as a plan with no confirm, which is a better
failure than a silent one and is the reason the intent is written first rather than last.
"""

from __future__ import annotations

import json
import re

import store

from .transitions import VerbError

#: `fleet` for a fleet-wide key, `agent:<name>` for one agent's, `roster` for an entry in the
#: agent roster (its key is the agent name). Anything else is refused: a scope nobody folds is a
#: setting that looks applied and reaches nothing.
FLEET_SCOPE = "fleet"
ROSTER_SCOPE = "roster"
_AGENT_SCOPE = re.compile(r"^agent:[A-Za-z0-9_.-]{1,64}$")

#: A dotted path into the config dict. Lowercase because every key in the file is, and a key that
#: differs from the file's only in case would override nothing while looking like it did.
_KEY = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z0-9_]+)*$")

#: An agent name, which becomes a roster key and a tmux window name.
_AGENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

#: Reserved inside a roster value: `{"enabled": false}` takes an agent OUT of the effective
#: roster without editing the file that put it there.
ENABLED = "enabled"


def _refused() -> dict:
    """The keys no surface may set, borrowed from `fleet_config` rather than restated.

    Composition, not re-implementation. `fleet_config.REFUSED` carries `permission_mode` and
    `bypassPermissions` with the ARGUMENT attached, and a second copy of that list is a second
    policy that drifts the first time one of them is edited.
    """
    from .fleet_config import REFUSED
    return REFUSED


def known_keys(cfg: dict | None = None) -> set:
    """Every key this fleet already has an opinion about, from the code and from the file.

    Derived, never listed. `CARRIED_KEYS` is the code's answer, the live file is the operator's,
    and the union is what `set` checks a new key against. A key outside it is very probably a
    typo, and a typo'd override is the worst outcome available here: it looks set, it is
    recorded, and it changes nothing.
    """
    from .config import CARRIED_KEYS, DEFAULT_CONFIG, config
    from .signals import DEFAULT_SIGNAL_WEIGHTS
    cfg = cfg if cfg is not None else config()
    out = set(CARRIED_KEYS) | set(DEFAULT_CONFIG) | {"agents", "defaults", "role", "host",
                                                     "config_dir", "workdir", "plans", "engine"}
    out |= {f"signals.{k}" for k in DEFAULT_SIGNAL_WEIGHTS}
    for k, v in (cfg or {}).items():
        out.add(k)
        if isinstance(v, dict):
            out |= {f"{k}.{sub}" for sub in v}
    for a in (cfg or {}).get("agents") or []:
        if isinstance(a, dict):
            out |= set(a)
    return {k for k in out if not k.startswith("_")}


# ------------------------------------------------------------------ reads


def layer() -> list:
    """Every active store override, newest scope-key order. `[]` when the store is away.

    Fails soft and quietly, the same way `fleet_config.override` does and for the same reason: a
    config resolution that raised because Postgres was unreachable would stop a runner from
    starting, and whether an absent store is fatal is the runner's decision to make, not this
    function's.
    """
    try:
        with store.read() as s:
            return s.query("SELECT scope, key, value, set_at, set_by, note "
                           "  FROM brain.config_in_force")
    except Exception:                                                   # noqa: BLE001
        return []


def history(scope: str = "", key: str = "", limit: int = 50) -> list:
    """The admin trail, newest first. Every admin verb that changed something wrote one row."""
    where, params = [], []
    if scope:
        where.append("scope = %s")
        params.append(scope)
    if key:
        where.append("key = %s")
        params.append(key)
    sql = ("SELECT change_seq, changed_at, changed_by, verb, scope, key, old_value, new_value, "
           "       note FROM brain.admin_change")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY change_seq DESC LIMIT %s"
    params.append(int(limit))
    with store.read() as s:
        return s.query(sql, tuple(params))


def humans() -> list:
    """Who this database recognises as a human. Migration 41's read door.

    `brain.human_role` is owner-only and the owner is not a human, so before migration 41 no
    single login could both read this list and pass the human gate. Measured on `brain`
    2026-08-27: brain_runtime denied, brain_operator denied, brain_owner reads it and
    `brain.current_human()` returns NULL for it.
    """
    with store.read() as s:
        return s.query("SELECT * FROM brain.human_roster()")


def ceiling() -> int:
    with store.read() as s:
        return int(s.scalar("SELECT brain.human_login_ceiling()"))


# ------------------------------------------------------------------ folding the two layers


def _set_path(d: dict, dotted: str, value):
    """Set one leaf of a nested dict, creating the intermediate dicts. Returns the old value."""
    parts = dotted.split(".")
    cur = d
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
        else:
            nxt = dict(nxt)
        cur[p] = nxt
        cur = nxt
    old = cur.get(parts[-1])
    cur[parts[-1]] = value
    return old


def get_path(d: dict, dotted: str):
    cur = d
    for p in dotted.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def fold(cfg: dict, rows: list | None = None) -> dict:
    """File config in, effective config out. The ONE place the two layers meet.

    Deliberately a deep set of one leaf per row rather than a dict update of a whole subtree:
    `signals.w_urgency` must move one weight and leave the other six alone. A whole-subtree
    override would silently reset every key the setter did not mention, which is the config bug
    that is hardest to see because the file still says what it always said.
    """
    rows = layer() if rows is None else rows
    if not rows:
        return cfg
    out = json.loads(json.dumps(cfg))          # a copy, so a caller's dict is never mutated
    agents = out.setdefault("agents", [])
    by_name = {a.get("name"): a for a in agents if isinstance(a, dict)}

    for r in rows:
        scope, key = r["scope"], r["key"]
        try:
            value = json.loads(r["value"])
        except (TypeError, ValueError):
            value = r["value"]
        if scope == FLEET_SCOPE:
            _set_path(out, key, value)
        elif scope == ROSTER_SCOPE:
            entry = by_name.get(key)
            if isinstance(value, dict) and value.get(ENABLED) is False:
                if entry is not None:
                    agents.remove(entry)
                    by_name.pop(key, None)
                continue
            if entry is None:
                entry = {"name": key}
                agents.append(entry)
                by_name[key] = entry
            if isinstance(value, dict):
                entry.update({k: v for k, v in value.items() if k != ENABLED})
        elif scope.startswith("agent:"):
            entry = by_name.get(scope.split(":", 1)[1])
            if entry is not None:
                _set_path(entry, key, value)
    return out


def layers_for(key: str, cfg_file: dict, cfg_effective: dict, rows: list) -> dict:
    """Where one key's value came from. What `swarm config --layers` prints per key."""
    store_row = next((r for r in rows if r["scope"] == FLEET_SCOPE and r["key"] == key), None)
    return {
        "key": key,
        "file": get_path(cfg_file, key),
        "store": (json.loads(store_row["value"]) if store_row else None),
        "in_force": get_path(cfg_effective, key),
        "source": "store" if store_row else ("file" if get_path(cfg_file, key) is not None
                                             else "code default"),
        "set_by": (store_row or {}).get("set_by", ""),
        "set_at": str((store_row or {}).get("set_at", "") or ""),
        "note": (store_row or {}).get("note", ""),
    }


def fleet_key_report(cfg_file: dict, rows: list | None = None) -> list:
    """Every fleet-scope key worth listing, with its layer. ONE producer, two call sites.

    `swarm config --layers` and `swarm admin config get` ask the same question and must not
    answer it twice: two renderings of one report is how the second one drifts.

    CONTAINER KEYS ARE LEFT OUT and that is a judgement worth stating. `signals` is a dict whose
    seven leaves are each listed on their own line, and `agents` is a list of six objects that
    `swarm admin agent list` renders properly. Printing them here as one JSON blob per row makes
    a table nobody can read and hides the seven lines that answer the question.
    """
    rows = layer() if rows is None else rows
    eff = fold(cfg_file, rows)
    keys = sorted({k for k in known_keys(cfg_file) if "." in k or k in cfg_file}
                  | {r["key"] for r in rows if r["scope"] == FLEET_SCOPE})
    out = []
    for k in keys:
        info = layers_for(k, cfg_file, eff, rows)
        if info["in_force"] is None and info["store"] is None:
            continue
        if isinstance(info["in_force"], (dict, list)) and info["store"] is None:
            continue
        out.append(info)
    return out


# ------------------------------------------------------------------ shared refusals


#: WHY EVERY CALLER PASSES `as_operator=True` EXPLICITLY EVEN THOUGH THE SIGNATURES DEFAULT IT.
#: `store/transitions.py:_login_for` reads the KWARGS DICT that was handed to `apply()`, which is
#: the only thing it can see: a Python default lives on the function and is bound after the login
#: has already been chosen. A caller that relies on the default connects as `brain_runtime`,
#: gets refused by migration 40's trigger, and reads that refusal as the guard being broken. The
#: defaults stay because they document the intent of each verb; the explicit kwarg is what makes
#: it true.


def _check_human(ctx) -> str:
    """The identity gate, asked once, in the one place every admin transition passes through.

    The database refuses this too (migration 40's trigger). Asking here as well buys the caller a
    sentence naming the verb instead of a SQLSTATE, and costs one round trip inside a transaction
    that was going to make several.
    """
    who = ctx.scalar("SELECT brain.current_human()")
    if not who:
        raise VerbError(
            "refusing an admin write from a connection the database does not know as a human. "
            "An admin verb decides what the fleet runs and who may write to this store, so it "
            "is established by WHICH LOGIN makes it, exactly as migration 20 establishes "
            "actor_type=human. Call it as the operator (the CLI passes as_operator=True, which "
            "opens that login); a host with no operator credential has nobody to attribute this "
            "to and refuses, which is the fail-closed direction.", code=6)
    return who


def _check_key(scope: str, key: str, *, allow_new: bool, cfg: dict | None = None) -> None:
    refused = _refused()
    leaf = key.split(".")[-1]
    for candidate in (key, leaf):
        if candidate in refused:
            raise VerbError(
                f"refusing to set {candidate!r} from any admin surface. {refused[candidate]}",
                code=6)
    if not _KEY.match(key):
        raise VerbError(
            f"{key!r} is not a config key. A key is a dotted lowercase path into the config "
            f"document, for example `fleet`, `signals.w_urgency` or `task_timeout`. This is "
            f"refused rather than quoted, for the same reason a subscriber slug is.", code=6)
    if allow_new:
        return
    known = known_keys(cfg)
    if key not in known and leaf not in known:
        near = sorted(k for k in known if leaf[:4] and k.endswith(leaf[:4]))[:5]
        raise VerbError(
            f"{key!r} is in neither the code's carried keys nor the live config file, so an "
            f"override on it would be recorded, would look applied, and would reach nothing. "
            f"That is the worst outcome available here. If the key is genuinely new, say so with "
            f"--new-key and it will be set and recorded as new."
            + (f" Did you mean: {', '.join(near)}?" if near else ""), code=6)


def _encode(value) -> str:
    """JSON text, from whatever a CLI or a caller handed over.

    A bare string that does not parse as JSON is stored as a JSON string, so `set fleet swarm`
    works without anybody quoting it twice, and `set signals.w_urgency 2.0` stores a number
    rather than the text "2.0". The file it overrides is JSON, so the layer above it has to be.
    """
    if isinstance(value, str):
        try:
            return json.dumps(json.loads(value))
        except ValueError:
            return json.dumps(value)
    return json.dumps(value)


def _record(ctx, *, verb, scope, key, old, new, note) -> None:
    ctx.execute(
        "INSERT INTO brain.admin_change (verb, scope, key, old_value, new_value, note) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (verb, scope, key, old, new, note or ""))


def _current(ctx, scope: str, key: str):
    return ctx.one("SELECT value, active FROM brain.config_setting WHERE scope = %s AND key = %s",
                   (scope, key))


# ------------------------------------------------------------------ transitions


@store.transition("admin config set")
def config_set(ctx, *, scope=FLEET_SCOPE, key, value, note="", new_key=False,
               as_operator=True):
    """Set one config key in the store layer, with the change recorded and attributed."""
    scope = str(scope or FLEET_SCOPE).strip()
    key = str(key or "").strip()
    if scope != FLEET_SCOPE and not _AGENT_SCOPE.match(scope):
        raise VerbError(
            f"{scope!r} is not a config scope. Use `fleet` for a fleet-wide key or "
            f"`agent:<name>` for one agent's. The roster is `admin agent add` and `admin agent "
            f"remove`, which validate an agent entry rather than taking a loose key.", code=6)
    who = _check_human(ctx)
    _check_key(scope, key, allow_new=bool(new_key))

    if scope.startswith("agent:"):
        name = scope.split(":", 1)[1]
        from .config import config
        if not any((a or {}).get("name") == name for a in (config().get("agents") or [])) \
           and not _current(ctx, ROSTER_SCOPE, name):
            raise VerbError(
                f"no agent named {name!r} is in the roster, in the file or in the store. An "
                f"override scoped to an agent that does not exist would be recorded and would "
                f"reach nothing. `swarm admin agent list` prints the roster; `swarm admin agent "
                f"add` puts a name in it.", code=6)

    encoded = _encode(value)
    prior = _current(ctx, scope, key)
    old = prior["value"] if prior and prior["active"] else None

    ctx.execute(
        """INSERT INTO brain.config_setting (scope, key, value, active, note)
                VALUES (%s, %s, %s, true, %s)
           ON CONFLICT (scope, key) DO UPDATE
             SET value = EXCLUDED.value, active = true, note = EXCLUDED.note""",
        (scope, key, encoded, note or ""))
    _record(ctx, verb="admin config set", scope=scope, key=key, old=old, new=encoded, note=note)
    return {"scope": scope, "key": key, "value": json.loads(encoded), "was": (
        json.loads(old) if old else None), "set_by": who, "new_key": bool(new_key)}


@store.transition("admin config unset")
def config_unset(ctx, *, scope=FLEET_SCOPE, key, note="", as_operator=True):
    """Withdraw a store override so the file's value stands again. An UPDATE, never a DELETE.

    `brain_runtime` holds DELETE on nothing, by design, and the trail would lose the row that
    says the override ever existed. Clearing `active` keeps both properties.
    """
    scope, key = str(scope or FLEET_SCOPE).strip(), str(key or "").strip()
    who = _check_human(ctx)
    prior = _current(ctx, scope, key)
    if not prior or not prior["active"]:
        raise VerbError(
            f"there is no active store override on {scope}/{key}, so there is nothing to "
            f"withdraw. `swarm config --layers` shows which keys the store is overriding; every "
            f"other value in force comes from the file or from the code default and is not "
            f"unsettable from here.", code=2)
    ctx.execute("UPDATE brain.config_setting SET active = false, note = %s "
                " WHERE scope = %s AND key = %s", (note or "", scope, key))
    _record(ctx, verb="admin config unset", scope=scope, key=key, old=prior["value"], new=None,
            note=note)
    return {"scope": scope, "key": key, "was": json.loads(prior["value"]), "set_by": who}


@store.transition("admin agent add")
def agent_add(ctx, *, name, values=None, note="", as_operator=True):
    """Put an agent in the roster, or amend the entry the file already has for it."""
    name = str(name or "").strip()
    if not _AGENT_NAME.match(name):
        raise VerbError(
            f"{name!r} is not an agent name. It becomes a roster key, a tmux window name and the "
            f"`agent` column on every row this terminal claims, so it is letters, digits, dot, "
            f"dash and underscore, and it is refused rather than quoted.", code=6)
    who = _check_human(ctx)
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except ValueError:
            raise VerbError("`values` must be a JSON object of the agent's config keys.") from None
    values = dict(values or {})
    values.pop("name", None)
    for k in values:
        if k in _refused():
            raise VerbError(f"refusing to set {k!r} on a roster entry. {_refused()[k]}", code=6)

    from .config import config, is_planner
    in_file = next((a for a in (config().get("agents") or []) if (a or {}).get("name") == name),
                   None)
    merged = {**(in_file or {}), **values}
    # D00 contract rule 5, asserted where the roster is written rather than only where it is
    # read. A planner that claims is an admiral executing the work it just decomposed.
    if is_planner(merged.get("role", "")) and merged.get("lanes"):
        raise VerbError(
            f"refusing to give {name!r} the planner role {merged.get('role')!r} AND a lane list. "
            f"A planner never claims (D00 contract rule 5) and an empty lane list is the gate "
            f"that holds it: `lanes: []`. Set one or the other.", code=6)
    if not is_planner(merged.get("role", "")) and merged.get("lanes") == []:
        raise VerbError(
            f"refusing to put {name!r} in the roster with an empty lane list and a non-planner "
            f"role. An empty lane list matches nothing, so this agent would start, heartbeat and "
            f"never claim, which reads on the board as a queue with no work in it.", code=6)

    prior = _current(ctx, ROSTER_SCOPE, name)
    old = prior["value"] if prior and prior["active"] else None
    encoded = json.dumps(values, sort_keys=True)
    ctx.execute(
        """INSERT INTO brain.config_setting (scope, key, value, active, note)
                VALUES (%s, %s, %s, true, %s)
           ON CONFLICT (scope, key) DO UPDATE
             SET value = EXCLUDED.value, active = true, note = EXCLUDED.note""",
        (ROSTER_SCOPE, name, encoded, note or ""))
    _record(ctx, verb="admin agent add", scope=ROSTER_SCOPE, key=name, old=old, new=encoded,
            note=note)
    return {"agent": name, "values": values, "in_file": in_file is not None, "set_by": who,
            "applies": "at the next `swarm-fleet up`; a running window keeps the config it "
                       "started with, and per-agent model and effort are re-resolved every claim"}


@store.transition("admin agent remove")
def agent_remove(ctx, *, name, note="", as_operator=True):
    """Take an agent out of the effective roster, including one the file put there.

    A positive assertion (`{"enabled": false}`) rather than an absence, because the file is the
    other layer and an absence in this one means "no opinion", which is what lets the file's
    entry stand.
    """
    name = str(name or "").strip()
    who = _check_human(ctx)
    from .config import config
    in_file = any((a or {}).get("name") == name for a in (config().get("agents") or []))
    prior = _current(ctx, ROSTER_SCOPE, name)
    if not in_file and not (prior and prior["active"]):
        raise VerbError(
            f"no agent named {name!r} is in the roster, in the file or in the store. Removing a "
            f"name nothing has would be recorded as a change and would change nothing.", code=2)
    old = prior["value"] if prior and prior["active"] else None
    encoded = json.dumps({ENABLED: False})
    ctx.execute(
        """INSERT INTO brain.config_setting (scope, key, value, active, note)
                VALUES (%s, %s, %s, true, %s)
           ON CONFLICT (scope, key) DO UPDATE
             SET value = EXCLUDED.value, active = true, note = EXCLUDED.note""",
        (ROSTER_SCOPE, name, encoded, note or ""))
    _record(ctx, verb="admin agent remove", scope=ROSTER_SCOPE, key=name, old=old, new=encoded,
            note=note)
    return {"agent": name, "in_file": in_file, "set_by": who,
            "applies": "at the next `swarm-fleet up`. It does NOT stop a running window: "
                       "`swarm stop --agent " + name + " --reason ...` is what does that"}


@store.transition("admin human plan")
def human_plan(ctx, *, slug, role_name="", note="", as_operator=True):
    """Record the INTENT to provision a human login, and check the ceiling before anything mints.

    The first of the two acts `admin human provision` is honest about being. Nothing here creates
    a role: this transaction validates, refuses a thirteenth login, and writes the row that says
    who asked for this and when. The privileged half runs afterwards, outside the store.
    """
    slug = str(slug or "").strip()
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,32}$", slug):
        raise VerbError(
            f"{slug!r} is not a human slug. It becomes a Postgres role name "
            f"(`brain_human_<slug>`) and a secret reference id, so it is lowercase letters, "
            f"digits and hyphens, and it is refused rather than quoted.", code=6)
    if slug == "operator":
        raise VerbError(
            "the `operator` human is provisioned by store/bin/provision-operator.sh and not from "
            "here. That script owns one role, brain_operator, which every operator surface "
            "already writes through and which store/session.py resolves by name; a second "
            "provisioner for the same role is how two provisioners drift.", code=6)
    who = _check_human(ctx)
    role_name = str(role_name or f"brain_human_{slug.replace('-', '_')}").strip()

    cap = int(ctx.scalar("SELECT brain.human_login_ceiling()"))
    have = [r["role_name"] for r in ctx.execute("SELECT * FROM brain.human_roster()")]
    if role_name in have:
        return {"slug": slug, "role_name": role_name, "already": True, "planned_by": who,
                "humans": len(have), "ceiling": cap}
    if len(have) >= cap:
        raise VerbError(
            f"refusing to plan a {len(have) + 1}th human login against a stated ceiling of {cap} "
            f"({len(have)} exist). This is a decision boundary, not a capacity limit: the "
            f"operator ruled on 2026-08-27 (row 0386, decision 2) that a thirteenth login "
            f"REOPENS the decision and is never a quiet migration. Raise it by replacing "
            f"brain.human_login_ceiling() in a migration, or revoke a human who has left: "
            f"`swarm admin human revoke <slug>`.", code=6)

    _record(ctx, verb="admin human plan", scope="human", key=role_name, old=None,
            new=json.dumps({"slug": slug, "role_name": role_name}), note=note)
    return {"slug": slug, "role_name": role_name, "already": False, "planned_by": who,
            "humans": len(have), "ceiling": cap,
            "secret_ref": f"brain-postgres-human-{slug}"}


@store.transition("admin human confirm")
def human_confirm(ctx, *, slug, role_name, outcome, detail="", as_operator=True):
    """Record the OUTCOME of the privileged half. The second act, and the one that closes it.

    Called whether the executor succeeded or failed, on purpose: an intent with no outcome is a
    run that was interrupted, and that is a state a reader should be able to see rather than
    infer from an absence.
    """
    who = _check_human(ctx)
    ok = str(outcome or "").strip() == "provisioned"
    mapped = None
    if ok:
        mapped = ctx.one("SELECT role_name, human FROM brain.human_roster() WHERE role_name = %s",
                         (role_name,))
        if not mapped:
            raise VerbError(
                f"refusing to record {role_name!r} as provisioned: brain.human_roster() does not "
                f"have it, so the privileged half did not land the mapping. Recording a success "
                f"the database cannot see is exactly the trail defect this table exists to "
                f"prevent.", code=1)
    _record(ctx, verb="admin human confirm", scope="human", key=role_name, old=None,
            new=json.dumps({"slug": slug, "outcome": outcome}), note=detail)
    return {"slug": slug, "role_name": role_name, "outcome": outcome, "confirmed_by": who,
            "mapped": bool(mapped)}


@store.transition("admin human revoke")
def human_revoke(ctx, *, role_name, note="", as_operator=True):
    """Remove a human mapping. A DEMOTION, not a lockout, and the return value says so."""
    role_name = str(role_name or "").strip()
    who = _check_human(ctx)
    gone = ctx.scalar("SELECT brain.revoke_human_role(%s)", (role_name,))
    _record(ctx, verb="admin human revoke", scope="human", key=role_name,
            old=json.dumps({"human": gone}), new=None, note=note)
    return {"role_name": role_name, "human": gone, "revoked_by": who,
            "still_true": "the login is not disabled and is still a member of brain_runtime, so "
                          "it can do what any agent can do. A lockout is ALTER ROLE ... NOLOGIN "
                          "plus removing the credential file, which needs the bootstrap "
                          "superuser: store/bin/provision-human.sh --revoke."}
