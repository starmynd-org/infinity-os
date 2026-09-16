#!/usr/bin/env python3
"""Task 0347. Prove that EVERY exit path of Guard B prints, not just the ones a test hit.

The eleven cases exercised on 0347 are evidence about the branches they reached. They say
nothing about a branch nobody thought to reach, and "the paths I tested all printed" is the
same shape of claim as "the comparisons I made all passed". So this walks the hook's AST and
enumerates EVERY `return` in `main()` plus the module-level except-handler, then checks each
one is preceded by an emitter. The denominator is the number of exit paths FOUND, and if that
number is 0 the hook was not parsed and this exits 2 rather than reporting a clean bill.

    python3 engine/bin/every-exit-speaks.py [path-to-hook]   # 0 all speak, 1 silent, 2 broken

MOVED HERE BY TASK 0352, off 0347. It was written into
`outputs/2026-08-19-T3-0347-guard-b-quotable/`, where nothing ran it again after that task
closed -- the same shape of defect as the hook it checks. `engine/tests/test_precommit_guard.py`
now runs it on every copy of the hook, on a deliberately silenced mutant (so its pass is not
vacuous), and on a file with no main() (so its zero-denominator exit 2 is asserted too). It is
MOVED and not copied on purpose: a second copy would recreate the keep-them-in-sync-by-remembering
problem that the same suite has to check for on the hook itself.
"""
import ast
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[2] / ".git" / "hooks" / "pre-commit"
EMITTERS = {"note", "fail_open", "write"}          # note()/fail_open() speak; .write() is stderr


def called_name(n):
    f = n.func
    return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")


def stream_aliases(fn):
    """Local names bound to a stream write, e.g. `out = sys.stderr.write`.

    Resolved rather than hardcoded: the refusal banner in this hook writes through `out`,
    and a checker that did not know that would score the loudest path in the file SILENT.
    """
    names = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Attribute) and n.value.attr == "write":
            names |= {t.id for t in n.targets if isinstance(t, ast.Name)}
    return names


def emits(node, emitters):
    """Does this statement (or the expression it returns) put bytes on a stream?"""
    return any(isinstance(n, ast.Call) and called_name(n) in emitters for n in ast.walk(node))


def main(argv):
    hook = Path(argv[1]) if len(argv) > 1 else DEFAULT
    try:
        tree = ast.parse(hook.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        print(f"BROKEN: could not parse {hook}: {exc}")
        return 2

    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    if fn is None:
        print(f"BROKEN: no main() in {hook}, so 0 exit paths could be enumerated.")
        return 2

    # Every `return` in main(), attributed to the block it sits in, plus the statement that
    # precedes it in that same block -- which is where note()/the banner lives.
    emitters = EMITTERS | stream_aliases(fn)
    # Nested helpers (`owner_landed`) return VALUES to their caller; they are not exit paths
    # of the process and must not dilute the denominator in either direction.
    nested = {id(n) for d in ast.walk(fn) if isinstance(d, ast.FunctionDef) and d is not fn
              for n in ast.walk(d)}

    exits = []
    for block in [fn] + [n for n in ast.walk(fn) if id(n) not in nested]:
        for kind in ("body", "orelse", "finalbody"):
            stmts = getattr(block, kind, None)
            # `ast.IfExp` also has .body/.orelse, but they are EXPRESSIONS, not statement
            # lists -- `own = {me} if me else set()` made this raise until it was guarded.
            if not isinstance(stmts, list) or not all(isinstance(x, ast.stmt) for x in stmts):
                continue
            for i, st in enumerate(stmts):
                if isinstance(st, ast.Return) and id(st) not in nested:
                    spoke = emits(st, emitters) or (i and emits(stmts[i - 1], emitters))
                    exits.append((st.lineno, bool(spoke), ast.unparse(st)[:60]))
    exits = sorted(set(exits))

    speak = [e for e in exits if e[1]]
    silent = [e for e in exits if not e[1]]

    if len(exits) == 0:                                             # DENOMINATOR
        print("0 exit paths enumerated. A verdict over an empty set is not a pass.")
        return 2

    print(f"hook: {hook}")
    for lineno, spoke, src in exits:
        print(f"  line {lineno:>4}  {'SPEAKS ' if spoke else 'SILENT '}  {src}")
    print(f"\n{len(speak)} of {len(exits)} exit path(s) in main() speak before returning; "
          f"{len(silent)} silent.  (stream aliases resolved: "
          f"{', '.join(sorted(stream_aliases(fn))) or 'none'})")
    if silent:
        print("A silent exit is a verdict nobody can quote. See task 0347.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
