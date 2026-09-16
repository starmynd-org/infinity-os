"""The account / model / effort picker's state, and the one key it may never carry.

V4's second deliverable is "a UI over config", and the first question is where the UI's change
LANDS. `config.json` stays a file for the reason `config.py` gives at length -- it resolves
`{**defaults, **agent}` and passes unknown keys through, and that tolerance is what let
`config_dirs` ship without a code change. This module does not take that away.

What it adds is an OVERRIDE LAYER in the store, and the argument is `accept.py`'s, verbatim, one
subsystem over:

    "The flag is a `runtime_flag` row rather than a config key on purpose. A config key is edited
     by whoever is editing config, which during a build is an agent; a `runtime_flag` write is a
     verb call that lands on the thread and is visible in `swarm status`."

The same holds here and more sharply, because this picker sits on a surface the operator uses at
speed. A console that rewrote `config.json` would be a second writer of a file three agents are
already editing during a build, with no transaction, no actor and no record of who changed what.
A `runtime_flag` row has `set_by`, `set_at` and `note` for free, rolls back with its transaction,
and is one `SELECT` away from the pending-chip the UI has to render.

    resolution order, after this module:   file config  <  store override

**`permission_mode` IS REFUSED HERE, in the verb, not merely left out of the UI.** The brief's
rule is absolute: `auto` is what makes an agent's self-report worth reading, because under it a
terminal can run the commands that verify its own work while destructive calls are still refused.
A picker that could lower it would make every self-report on the surface worthless, and a control
that is only absent from a template is one template edit from existing. Anything touching
`bypassPermissions` is refused by the same list.

**Nothing here applies mid-run and the UI says so.** `swarm-run` resolves config for a dispatch
and hands the engine a model on the command line; a run already in flight was launched with the
value that was current then and cannot be re-pointed. The honest semantics, MEASURED on this
runner rather than assumed:

  * model, effort, engine args -> **the next claim**, because `swarm-run` re-resolves config at
    the top of each loop iteration (`refresh_config`, added by task 0166 for exactly this).
  * the preferred account -> **the next runner start**. The account ring and its cooldown state
    are built once at start (`swarm-run`, the `account ring` block) and a running runner keeps
    the ring it built. V00 already records this: a newly authenticated account needs
    `swarm-fleet restart`, not just `swarm start`.
"""

from __future__ import annotations

import json

import store

from .transitions import VerbError

#: One row per agent. The key is namespaced so `swarm status`'s flag list stays readable.
PREFIX = "agent_override:"

#: Every key this picker may set, and nothing else is accepted. A list rather than a blocklist:
#: a new key someone adds to config must be added HERE deliberately to become settable, which is
#: the direction that fails safe.
SETTABLE = ("model", "effort", "account")

#: Refused by name, with the reason attached, so a caller gets the argument and not a shrug.
REFUSED = {
    "permission_mode":
        "`permission_mode` is what makes an agent's self-report trustworthy: under `auto` a "
        "terminal can run the commands that verify its own work while destructive calls are "
        "still refused, and under `acceptEdits` it can write a file and cannot run the test "
        "that proves the file is right. It is carried in config and changed by a human editing "
        "that file, never from a console surface.",
    "permissionMode": "see `permission_mode`: same key, same refusal.",
    "bypassPermissions":
        "nothing on any console surface may touch `bypassPermissions`, in any state, disabled "
        "or otherwise.",
    "dangerously_skip_permissions": "see `bypassPermissions`.",
}


def key_for(agent: str) -> str:
    return PREFIX + str(agent or "").strip()


def override(agent: str) -> dict:
    """The pending/applied override for one agent. `{}` when there is none or the store is away.

    Fails closed and quietly: a config resolution that raised because the database was
    unreachable would stop a runner from starting, and the runner's own store check is the place
    that decides whether the store being away is fatal.
    """
    try:
        with store.read() as s:
            row = s.one("SELECT value, set_at, set_by, note FROM brain.runtime_flag "
                        " WHERE key = %s", (key_for(agent),))
    except Exception:                                                   # noqa: BLE001
        return {}
    if not row or not (row.get("value") or "").strip():
        return {}
    try:
        data = json.loads(row["value"])
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {"values": {k: v for k, v in data.items() if k in SETTABLE},
            "set_at": row.get("set_at"), "set_by": row.get("set_by") or "",
            "note": row.get("note") or ""}


def apply_override(resolved: dict, agent: str) -> dict:
    """Fold the store override onto a resolved config dict. The ONE place the two layers meet.

    `account` is not a config key: it is the FIRST entry of `config_dirs`, which is a ring the
    runner rotates when an account walls. Choosing an account therefore REORDERS the ring rather
    than replacing it -- dropping the fallbacks would turn a five-hour wall on one account into a
    parked agent, which is the thing the ring exists to prevent.
    """
    ov = override(agent)
    if not ov:
        return resolved
    vals = ov["values"]
    out = dict(resolved)
    if vals.get("model"):
        out["model"] = vals["model"]
    if "effort" in vals:
        out["effort"] = vals["effort"]
    if vals.get("account"):
        ring = list(out.get("config_dirs") or ([out["config_dir"]] if out.get("config_dir") else []))
        want = vals["account"]
        out["config_dirs"] = [want] + [d for d in ring if d != want]
    out["_override"] = {"values": vals, "set_at": str(ov["set_at"] or ""),
                        "set_by": ov["set_by"], "note": ov["note"]}
    return out


@store.transition("agent config set")
def set_override(ctx, *, agent, values, by="operator", note="", as_operator=True):
    """Set what one agent uses at its next claim. The picker's only writer.

    `values` is a dict of `SETTABLE` keys. Everything else is refused, and the two keys the brief
    forbids are refused by name with the reason, because a refusal that says only "unknown key"
    teaches nobody why the key is not there.
    """
    agent = str(agent or "").strip()
    if not agent:
        raise VerbError("refusing to set config for no agent.")
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except ValueError:
            raise VerbError("`values` must be a JSON object of the settable keys.") from None
    if not isinstance(values, dict) or not values:
        raise VerbError("refusing to set an empty override: it would record a change of nothing.")

    for k in values:
        if k in REFUSED:
            raise VerbError(f"refusing to set {k!r} from any console surface. {REFUSED[k]}",
                            code=6)
        if k not in SETTABLE:
            raise VerbError(
                f"{k!r} is not settable from the picker. Settable: {', '.join(SETTABLE)}. "
                f"Config keys that are not in that list are edited in the config file by a human, "
                f"deliberately.", code=6)

    # THE SAME IDENTITY GUARD THE STEERING MARK USES, and for a smaller but real version of the
    # same reason: this row decides what model and which account a terminal runs its NEXT CLAIM
    # under, so an agent able to write it could re-point the fleet. `brain.current_human()`
    # answers from `session_user` and returns NULL for `brain_runtime` (migration 20).
    human = ctx.scalar("SELECT brain.current_human()")
    if not human:
        raise VerbError(
            "refusing to set an agent's config from a connection the database does not know as a "
            "human. This row decides what a terminal runs its next claim under. Call it as the "
            "operator (`as_operator=True`, which opens the operator login).", code=6)
    by = human
    if not ctx.one("SELECT 1 FROM brain.agent WHERE name = %s", (agent,)):
        raise VerbError(
            f"no agent named {agent!r} has ever heartbeated. Setting an override for a name the "
            f"fleet does not have would look like a change and reach nothing.", code=6)

    clean = {k: ("" if values[k] is None else str(values[k]).strip()) for k in values}
    ctx.actor = by
    ctx.execute(
        """INSERT INTO brain.runtime_flag (key, value, set_by, note) VALUES (%s, %s, %s, %s)
           ON CONFLICT (key) DO UPDATE
             SET value = EXCLUDED.value, set_at = now(), set_by = EXCLUDED.set_by,
                 note = EXCLUDED.note""",
        (key_for(agent), json.dumps(clean), by, note or "set from the console picker"))
    return {"agent": agent, "values": clean, "by": by}


def consumed_by(agent: str, set_at) -> dict | None:
    """Has a run started under this override yet? That is what turns the chip into a receipt.

    "Applies at next claim" is a claim about the future, and the only honest way to close it is
    to point at the run that started AFTER the change. A run row is that evidence: `run start` is
    written by the runner at dispatch, so a row with `started_at > set_at` was launched with the
    config this override was already in.
    """
    if not set_at:
        return None
    try:
        with store.read() as s:
            return s.one(
                "SELECT work_item_id, attempt, started_at FROM brain.run "
                " WHERE agent = %s AND started_at > %s ORDER BY started_at LIMIT 1",
                (agent, set_at))
    except Exception:                                                   # noqa: BLE001
        return None
