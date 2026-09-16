"""Proof that `chats.register(app)` resolves — the exact line R16 puts in `web/app.py`.

    python3 web/chats/bin/reexport_proof.py        (needs flask; run under WSL python)
    python  web/chats/bin/reexport_proof.py        (Windows python, no flask: the LAZINESS half)

**mutatesState: NO.** It imports and inspects. It opens no store, serves nothing and writes nothing.

WHY THIS FILE EXISTS. `2026-09-09-IOS-admiral` published my own docstring sentence — *"what
`web/app.py` would gain is `chats.register(app)` and nothing else"* — as ruling R16, **as literal
text for `term-2` to type into `create_app()`.** It was not true against my branch:
`chats.register` raised `AttributeError`. **A docstring describing an intended interface and a
ruling instructing a seat to type a line are different kinds of sentence, and the gap between them
was mine to close.**

**So the promise is now a CHECK rather than a sentence.** The thing R16 asks a seat to type is the
thing this file runs.

THE TWO HALVES, AND THEY PULL AGAINST EACH OTHER

    A  `chats.register` must RESOLVE          -- so web/app.py needs one line, not two
    B  `import web.chats` must not need flask -- so the three Windows-run proofs still import

**A plain `from .views import register` in `__init__.py` satisfies A and breaks B.** Measured:
`python -c "import flask"` on this box's Windows interpreter returns `ModuleNotFoundError`, and
`roundtrip_proof.py`, `harness_registration_proof.py` and `shim_proof.py` all run there and import
`web.chats.protocol` / `registry`, **which executes `__init__.py` first.**

**This file asserts BOTH, and which half it can assert depends on the interpreter running it** —
so it reports the interpreter and says which half it proved.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

CHECKS = 0
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  --  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def main() -> int:
    have_flask = True
    try:
        import flask  # noqa: F401
    except ImportError:
        have_flask = False

    print("=" * 78)
    print("RE-EXPORT PROOF -- the exact line R16 puts in web/app.py")
    print(f"  interpreter   {sys.executable}")
    print(f"  python        {sys.version.split()[0]} on {sys.platform}")
    print(f"  flask         {'present' if have_flask else 'ABSENT -- proving the laziness half'}")
    print("=" * 78)

    # ---- HALF B, and it is provable on EITHER interpreter but only MEANINGFUL without flask ----
    print("\nB. `import web.chats` costs no web framework")
    before = "flask" in sys.modules
    import web.chats as chats
    after = "flask" in sys.modules
    check("importing the package does not itself import flask",
          before == after,
          f"flask in sys.modules before={before} after={after}"
          + ("" if not have_flask else "  (flask was already imported by this proof; the"
                                       " Windows run is the one that proves it)"))
    check("`views` and `register` are advertised in __all__",
          "views" in chats.__all__ and "register" in chats.__all__,
          f"__all__ = {chats.__all__}")
    check("an unknown attribute still raises AttributeError rather than importing something",
          _raises_attribute_error(chats),
          "a lazy __getattr__ that swallows unknown names hides typos forever")

    # ---- HALF A ----
    print("\nA. `chats.register` resolves -- the line R16 asks term-2 to type")
    if not have_flask:
        print("  SKIPPED on this interpreter: resolving it imports views, which imports flask.")
        print("  **That is the point of half B, not a gap.** Run under WSL python to assert it.")
    else:
        check("`chats.register` resolves", callable(getattr(chats, "register", None)),
              "this is the attribute that raised AttributeError before the repair")
        from web.chats import views
        check("and it IS views.register, not a lookalike",
              getattr(chats, "register") is views.register,
              "a re-export that returns a different object is a second entry point")
        check("`chats.views` resolves to the module",
              getattr(chats, "views") is views)

        # The whole point: mount through the re-exported name, exactly as web/app.py would.
        from flask import Flask
        app = Flask(__name__, template_folder=os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "templates")))
        chats.register(app)
        routes = sorted(str(r) for r in app.url_map.iter_rules() if "chats" in str(r))
        check("mounting via `chats.register(app)` registers the Chats routes",
              len(routes) >= 2, f"routes: {routes}")

    print("\n" + "=" * 78)
    print(f"CHECKS {CHECKS}   PASS {CHECKS - len(FAILURES)}   FAIL {len(FAILURES)}")
    for name in FAILURES:
        print(f"  FAILED: {name}")
    print("=" * 78)
    return 1 if FAILURES else 0


def _raises_attribute_error(mod) -> bool:
    try:
        getattr(mod, "definitely_not_a_real_attribute")
    except AttributeError:
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
