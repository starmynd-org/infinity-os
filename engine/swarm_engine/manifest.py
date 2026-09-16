"""The capability manifest: what this CLI can do, in a form an agent reads instead of infers.

## The gap this closes

An agent driving `swarm` today reads `--help` and infers. That is a parse of prose written for a
human, and it answers none of the three questions an agent actually has before it calls something:

  * does this verb CHANGE STATE, or only read?
  * which transition does it go through, so its effect can be found in the trail afterwards?
  * does `--json` actually do anything on it?

The third one is not hypothetical. Measured on 2026-08-27, before this file existed: `--json` is
registered on 48 of 48 verbs, because `build_parser`'s `add()` helper attaches it to every
subparser, and 26 of those 48 handlers never read it. An agent that infers "the flag is there, so
the output is machine-readable" is wrong on more than half the surface, silently, and the way it
finds out is by parsing a human-formatted board as JSON.

## Derived, never maintained

Every field here is read out of the parser and out of `store.transitions.registered()` at the
moment it is asked for. Nothing is a list somebody has to remember to extend.

That is the whole design and it is worth being explicit about why: a hand-written manifest is a
second description of the CLI, and a second description drifts. The first verb somebody adds
without touching it, the manifest is wrong; and a manifest that is wrong is worse than none,
because the agent that trusted it does not go and read `--help` to check.

The cost of deriving is a shallow source scan for `store.apply(` and for `args.json`, which sees
what the handler body itself does and not what a helper three frames down does. Where that is
uncertain the field says so rather than guessing: `writes` is `true`, `false`, or `"indirect"`.
"""

from __future__ import annotations

import datetime as _dt
import inspect
import re

import store

#: `store.apply("verb"` in a handler's own body, and the one thin wrapper over it that the admin
#: group uses. `_admin_apply` exists only to name an un-applied migration as itself rather than
#: letting `die_db` print it as a defect; it forwards to `store.apply` unchanged. Matching it here
#: is what keeps the manifest's `writes` and `transitions` fields TRUE rather than pessimistic:
#: without this the 7 admin write verbs fell back to "indirect", which is a correct-sounding
#: answer that tells a reader less than the real one. A wrapper that ever did more than forward
#: would have to come out of this pattern.
_APPLY = re.compile(r"""(?:store\.apply|_admin_apply)\(\s*["']([a-z][a-z0-9 _-]*)["']""")

#: A handler that reaches a transition through something this scan cannot resolve: another cmd_
#: handler, or a helper whose name says it applies. Reported as `indirect` rather than as a false
#: `false`, because "no" and "I cannot tell" are different answers and an agent deciding whether
#: to call a verb needs the difference.
#:
#: DELIBERATELY NOT a match on any module prefix. An earlier version matched `admin_mod.`, which
#: made three READ-ONLY admin verbs report `indirect` purely because they call `admin_mod.layer()`
#: and `admin_mod.history()`. A pessimistic answer is still a wrong one: it tells an agent that
#: `admin config get` might change state, and the whole reason this file exists is that inferring
#: from a surface is worse than being told.
#: `(?<!def )` is load-bearing and was found by watching this get it wrong: without it, every
#: handler matched its OWN definition line, `def cmd_ls(args):`, and the manifest reported
#: 0 read-only verbs out of 62. A heuristic that classifies everything is not a heuristic.
_INDIRECT = re.compile(r"\b(store\.apply|_admin_apply|(?<!def )cmd_[a-z_]+\(args)")

MANIFEST_VERSION = "1"


def _source(fn) -> str:
    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):
        return ""


def _arguments(parser) -> list:
    out = []
    for a in parser._actions:
        if a.dest in ("help", "fn"):
            continue
        opts = list(a.option_strings)
        out.append({
            "name": (opts[0] if opts else a.dest),
            "dest": a.dest,
            "positional": not opts,
            "options": opts,
            "required": bool(getattr(a, "required", False)) or not opts,
            "takes_value": a.nargs != 0 and not isinstance(a.const, bool)
                           and a.__class__.__name__ not in ("_StoreTrueAction",
                                                            "_StoreFalseAction"),
            "choices": list(a.choices) if a.choices else None,
            "default": a.default if not callable(a.default) else None,
            "help": (a.help or "").strip(),
        })
    return out


def _describe(name: str, parser, prefix: str = "") -> dict:
    fn = parser.get_default("fn")
    src = _source(fn) if fn else ""
    applies = sorted(set(_APPLY.findall(src)))
    if applies:
        writes = True
    elif _INDIRECT.search(src):
        writes = "indirect"
    else:
        writes = False
    has_json_flag = any("--json" in a.option_strings for a in parser._actions)
    honours_json = bool(re.search(r"args\.json|getattr\(args,\s*[\"']json[\"']", src))
    return {
        "verb": (prefix + " " + name).strip(),
        "help": (parser.description or "").strip().splitlines()[0] if parser.description
                else (getattr(parser, "_swarm_help", "") or ""),
        "handler": f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__name__', '?')}",
        "writes": writes,
        "transitions": applies,
        "json_flag": has_json_flag,
        "json_honoured": honours_json,
        "arguments": _arguments(parser),
    }


def _subparsers(parser):
    for action in parser._actions:
        if hasattr(action, "choices") and isinstance(getattr(action, "choices", None), dict):
            return action
    return None


def build(parser, *, include_transitions: bool = True) -> dict:
    """The manifest for one argparse tree. Recurses into a nested verb group like `admin`."""
    top = _subparsers(parser)
    verbs = []

    def walk(sub_action, prefix):
        for name, sp in sub_action.choices.items():
            nested = _subparsers(sp)
            if nested is not None:
                walk(nested, (prefix + " " + name).strip())
            else:
                verbs.append(_describe(name, sp, prefix))

    if top is not None:
        walk(top, "")

    writes = [v for v in verbs if v["writes"] is True]
    indirect = [v for v in verbs if v["writes"] == "indirect"]
    honoured = [v for v in verbs if v["json_flag"] and v["json_honoured"]]
    ignored = [v for v in verbs if v["json_flag"] and not v["json_honoured"]]

    out = {
        "manifest_version": MANIFEST_VERSION,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "cli": parser.prog,
        "derived_from": "the argparse tree and store.transitions.registered(), read at the "
                        "moment you asked. Nothing here is a maintained list, because a second "
                        "description of a CLI drifts and a manifest that is wrong is worse than "
                        "no manifest at all.",
        "exit_codes": {
            "0": "ok",
            "1": "error",
            "2": "empty, or nothing to do. NOT an error: `swarm artifacts` on an empty ledger "
                 "exits 2, which is this repo's zero-denominator convention",
            "6": "a verb refusing on purpose. A guard said no; the call was well formed",
        },
        "write_path": "Every state change goes through store.apply(verb) in one transaction. "
                      "Reads go through store.read(), which Postgres itself marks SET "
                      "TRANSACTION READ ONLY, so a caller that attempts a write is refused by "
                      "the server. See store/narrow_waist.md.",
        "counts": {
            "verbs": len(verbs),
            "writing_verbs": len(writes),
            "indirect_writers": len(indirect),
            "read_only_verbs": len(verbs) - len(writes) - len(indirect),
            "json_flag_present": len(honoured) + len(ignored),
            "json_honoured": len(honoured),
            "json_flag_ignored": len(ignored),
        },
        "json_flag_ignored_by": sorted(v["verb"] for v in ignored),
        "verbs": verbs,
    }
    if include_transitions:
        try:
            out["transitions"] = store.transitions.registered()
        except Exception:                                               # noqa: BLE001
            out["transitions"] = {}
        out["counts"]["registered_transitions"] = len(out["transitions"])
    return out


def render(m: dict) -> list:
    """The human rendering. Same content, and it prints the denominator on every count line."""
    c = m["counts"]
    lines = [
        f"{m['cli']} capability manifest v{m['manifest_version']}, generated {m['generated_at']}",
        "",
        f"verbs                 {c['verbs']}",
        f"  write state         {c['writing_verbs']}/{c['verbs']} directly, "
        f"{c['indirect_writers']}/{c['verbs']} indirectly, "
        f"{c['read_only_verbs']}/{c['verbs']} read only",
        f"  --json accepted     {c['json_flag_present']}/{c['verbs']}",
        f"  --json honoured     {c['json_honoured']}/{c['json_flag_present']} of those; "
        f"{c['json_flag_ignored']}/{c['json_flag_present']} accept it and IGNORE it",
        f"registered transitions {c.get('registered_transitions', 0)}",
        "",
    ]
    if m["json_flag_ignored_by"]:
        lines.append("accept --json and ignore it (parse their text output, not JSON):")
        lines.append("  " + ", ".join(m["json_flag_ignored_by"]))
        lines.append("")
    lines.append(f"{'verb':<26} {'writes':<9} {'json':<5} transitions")
    for v in m["verbs"]:
        w = {True: "yes", False: "no", "indirect": "indirect"}[v["writes"]]
        j = "yes" if (v["json_flag"] and v["json_honoured"]) else "no"
        lines.append(f"{v['verb']:<26} {w:<9} {j:<5} {', '.join(v['transitions']) or '-'}")
    return lines
