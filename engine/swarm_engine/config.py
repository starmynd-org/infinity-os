"""Config, and the one key in it that decides whether an agent's self-report is worth reading.

`config.json` stays a file. It is not a table and D1's COVERAGE.md says why: putting it in the
store would either freeze the key set or reimplement JSON in columns, and the thing that makes
this file work is that it does neither. It resolves `{**defaults, **agent}` and **passes unknown
keys through**. That tolerance is what let `config_dirs` ship with no CLI change, and it is
preserved here on purpose rather than by accident.

`permission_mode` is load-bearing and appeared in no planning document. The live fleet runs
`auto`, which runs the permission classifier: a terminal can execute the commands it needs to
verify its own work, while destructive calls are still refused. Under `acceptEdits` a terminal
can write a file but cannot run the test that proves the file is right, which produces confident
unverified work. That is the whole reason an agent's self-report is trustworthy under `auto` and
is not under `acceptEdits`. So it is carried as a config key, never hard-coded, and never left to
the engine default.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

# Every key that must survive the port, from D00 plus D1's measured correction. `signals` is in
# this list because D00 named it, and it is documented as a code default: D1 measured that the
# live config overrides none of it (see signals.DEFAULT_SIGNAL_WEIGHTS).
CARRIED_KEYS = (
    "permission_mode", "config_dirs", "add_dirs", "lanes", "plans", "model", "engine_args",
    "allowed_tools", "task_timeout", "interval", "effort", "signals", "fleet",
)

DEFAULT_CONFIG = {
    "fleet": "swarm",
    "signals": {
        "w_urgency": 1.0,
        "w_dependency_unblocking": 1.0,
        "w_charter_alignment": 1.0,
        "w_stakes": 1.0,
        "w_effort": 1.0,
        "w_age_per_day": 0.25,
        "gate_low_confidence": True,
    },
    "agents": [
        # `workdir` HERE IS NOT A FALLBACK FOR A TASK THAT HAS NONE, AND SINCE TASK 0100 IT NEVER
        # BECOMES ONE. `swarm-run` used to `cd` here when `TASK_WORKDIR` was empty, which is how a
        # task nobody gave a directory ran in `$HOME` -- the one tree that is the parent of every
        # repo on the box. That branch is deleted: `post` now refuses to create an agent-claimable
        # row with no workdir, and the runner fails the ones that predate the guard. So these
        # three values are left exactly as they were on purpose. Changing them to a repo would
        # only move the same defect one layer down: one tree for every lane is still a tree
        # nobody chose for the task, and it would read deliberate. A terminal's cwd comes from
        # the TASK; a planner never claims one and never `cd`s at all (there is one `cd` in
        # swarm-run and it is on the terminal path), so for every role this value is now what
        # `swarm-run` prints in its `up.` line and nothing else.
        #
        # A planner claims nothing: `lanes: []`. It wakes on board change and plans.
        {"name": "admiral", "role": "admiral", "lanes": [], "plans": ["*"], "engine": "claude",
         "model": "opus", "permission_mode": "auto", "interval": 120,
         "config_dir": "~/.claude", "workdir": str(Path.home())},
        {"name": "T1", "role": "terminal", "lanes": ["*"], "engine": "claude",
         "model": "opus", "permission_mode": "auto", "interval": 30,
         "config_dir": "~/.claude", "workdir": str(Path.home())},
        {"name": "T2", "role": "terminal", "lanes": ["*"], "engine": "claude",
         "model": "opus", "permission_mode": "auto", "interval": 30,
         "config_dir": "~/.claude", "workdir": str(Path.home())},
    ],
}

# Roles that never claim. D00 contract rule 5. The list is here rather than inline so a new
# planner role is one edit and cannot be added to the fleet without passing this gate.
PLANNER_ROLES = ("admiral", "commander", "planner")


class ConfigError(RuntimeError):
    pass


def config_path() -> Path:
    """Where the engine's own config lives.

    NOT `~/.swarm/config.json`. That is the live bus, this lane does not touch it, and pointing
    at it would make a test run against the running fleet's file. `ENGINE_HOME` overrides.
    """
    return Path(os.environ.get("ENGINE_CONFIG",
                               os.path.expanduser("~/.brain-runtime/config.json")))


def fingerprint() -> dict:
    """What config this fleet is actually running, as one comparable string. Task 0273.

    READ ONLY, and that is the whole of what this function is. It answers "is the file the same
    one it was an hour ago" without anybody having to have recorded that it changed, which is the
    property the task's brief asks for and the property version control does not have: a `git
    diff` is blind to an edit made in place on an untracked file, and `~/.brain-runtime/` is not
    a git repository at all (checked: `git rev-parse` there says "not a git repository").

    THE MEASUREMENT THAT MADE THIS NECESSARY. On 2026-08-18 `~/.brain-runtime/config.json`
    changed three times in one day -- model, lanes, agent set -- with no record of who, when or
    why. A lane opened an investigation to find out and the answer came back "the commander"
    (task 0215, q0217). Nothing writes this file: no verb in this repo has a write path to it,
    so every change is a human editing it by hand.

    The hash is over the file's BYTES, not over the parsed dict, on purpose. A comment or a key
    order that changed is still an edit somebody made, and a reader comparing two fingerprints
    is asking whether the file is the same file, not whether it happens to parse the same.

    `mtime` and `bytes` ride along because a fingerprint that differs tells you THAT it changed
    and these two narrow down WHEN, from the filesystem, with nothing having had to be recorded.
    """
    import hashlib
    path = config_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {"path": str(path), "present": False, "sha256": "", "bytes": 0, "mtime": None}
    st = path.stat()
    return {"path": str(path), "present": True,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "mtime": _dt.datetime.fromtimestamp(st.st_mtime, _dt.timezone.utc).isoformat()}


def config() -> dict:
    path = config_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DEFAULT_CONFIG
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path} is not valid JSON: {e}") from None


def effective(cfg: dict | None = None) -> dict:
    """The file, with the store's override layer folded on top. WHAT IS ACTUALLY IN FORCE.

    `config()` is what the file says. This is what the fleet runs, and the two are different the
    moment anyone calls `swarm admin config set`. Every caller that is deciding BEHAVIOUR -- the
    weights `claim` orders by, the roster `swarm-fleet up` starts, the model `swarm-run` hands
    the engine -- reads this one. `config()` is still the right call for a caller that wants the
    file itself, which is `swarm init` and the fingerprint.

    Imported lazily and failing soft for the reason `resolved()` already states: this module is
    read by tests that have no database, and a config resolution that raised because Postgres was
    unreachable would stop a runner from starting.
    """
    cfg = cfg if cfg is not None else config()
    try:
        from . import admin
        return admin.fold(cfg)
    except Exception:                                                   # noqa: BLE001
        return cfg


def agent_config(name: str, cfg: dict | None = None) -> dict:
    """`{**defaults, **agent}`, with unknown keys passed through.

    Both halves matter. The merge is what lets an operator set `add_dirs` once for the whole
    fleet instead of on seven agents. The pass-through is what lets a key the CLI has never
    heard of reach the runner, which is how `config_dirs` shipped without a code change: the
    runner read it, the CLI relayed it, and nothing in between had to know it existed.
    """
    # `effective()` and not `config()`: a caller with no cfg of its own is asking what this
    # agent RUNS, and since the admin verb group exists the file is only half of that answer.
    # Every caller that passes a cfg has already resolved which half it means.
    cfg = cfg if cfg is not None else effective()
    merged = dict(cfg.get("defaults") or {})
    for a in cfg.get("agents", []):
        if a.get("name") == name:
            merged.update(a)          # the agent's own keys win over the fleet defaults
            return merged
    return merged if not name else {}


def is_planner(role: str) -> bool:
    return str(role or "").strip().lower() in PLANNER_ROLES


def resolved(name: str, cfg: dict | None = None) -> dict:
    """What `swarm config --agent X` prints. The fleet-level keys plus the agent's own.

    AND, SINCE TASK 0166, the store override the console's picker writes, folded on top. The
    order is file first, store second, so a value the operator picked from the console wins over
    the file for exactly as long as the override row exists; `fleet_config.py` states why the
    picker writes a row rather than editing this file. `permission_mode` is not settable that
    way -- the verb refuses it by name -- so the key below is still the file's, always.
    """
    cfg = cfg if cfg is not None else effective()
    out = {"fleet": cfg.get("fleet", "swarm")}
    if name:
        out.update(agent_config(name, cfg))
        out.setdefault("permission_mode", "auto")
        # Imported here rather than at module scope: `fleet_config` imports `store`, and this
        # module is read by tests that have no database. A store that is away returns no
        # override and the file's values stand.
        try:
            from . import fleet_config
            out = fleet_config.apply_override(out, name)
        except Exception:                                               # noqa: BLE001
            pass
        out["planner"] = is_planner(out.get("role", ""))
        # Stated rather than implied: a planner's empty lane list is what keeps it off the
        # queue, and printing it is how an operator can see the gate rather than trust it.
        if out["planner"]:
            out.setdefault("lanes", [])
    else:
        out["agents"] = [a.get("name") for a in cfg.get("agents", [])]
        out["signals"] = cfg.get("signals", DEFAULT_CONFIG["signals"])
    return out
