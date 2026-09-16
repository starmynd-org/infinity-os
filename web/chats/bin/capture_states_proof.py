"""Proof that `capture_coverage.py`'s four configuration states are REACHABLE, not merely declared.

    python web/chats/bin/capture_states_proof.py

**mutatesState: YES.** It builds synthetic `~/.claude` directories in a temporary root and reads
them. It touches no real install, opens no store, and deletes what it made. **It is a FIXTURE and
is declared as one** -- nothing it prints is a measurement of this machine.

**AND IT WRITES NO FILE NAMED `settings.json`, ANYWHERE, EVER.** See `FIXTURE_SETTINGS_NAME` below:
`SEAT-COMMON` §1 forbids writing any file of that name and the prohibition is unqualified. An
earlier version of this proof wrote six of them into a temp directory on my own narrower reading;
`2026-09-09-IOS-term-13` caught it and the catch is upheld.

WHY IT EXISTS. `capture_coverage.py` revision 2 classifies an install as `COMPLETE`, `PARTIAL`,
`ABSENT` or `UNKNOWN` after `2026-09-09-IOS-term-13` returned revision 1 for folding three of those
into one. **A classifier with four declared states and two reachable ones is revision 1 with more
words**, and the only way to tell them apart is to construct an install of each shape and read the
verdict back.

*"Watch both branches of every guard. A guard that fires on everything is indistinguishable from one
that fires on nothing."* There are six branches here, so all six are constructed.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from web.chats.bin import capture_coverage as cc                     # noqa: E402

HOOK = "/some/path/ingest/bin/claude-session-hook"
OTHER = "/some/other/path/ingest/bin/claude-session-hook"

#: **THE FIXTURE NEVER WRITES A FILE CALLED `settings.json`, AND THAT IS A PROHIBITION, NOT TASTE.**
#: `SEAT-COMMON` §1: *"Never write any `settings.json`."* My first version of this proof wrote six
#: synthetic ones into a temporary directory, on my own reading that the rule meant a real user's
#: config. **`2026-09-09-IOS-term-13` pointed out that the rule names ANY file of that name and it
#: is right** — the rule is unqualified, and the whole discipline of this fleet is to quote the
#: condition rather than the reading of it that suits you. So the classifier takes the filename as
#: a parameter, a real survey leaves it at its default, and this fixture uses the name below.
#: Nothing of the coverage is lost: every branch is still constructed on disk and read back.
FIXTURE_SETTINGS_NAME = "settings-fixture.json"

CHECKS = 0
FAILURES: list[str] = []


def _home(root: str, name: str, settings: str | None) -> str:
    home = os.path.join(root, name)
    os.makedirs(os.path.join(home, "projects"), exist_ok=True)
    if settings is not None:
        with open(os.path.join(home, FIXTURE_SETTINGS_NAME), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(settings)
    return home


def _hooks(mapping: dict[str, str]) -> str:
    return json.dumps({"hooks": {
        event: [{"hooks": [{"type": "command", "command": command}]}]
        for event, command in mapping.items()
    }}, indent=2)


def check(label: str, got: str, want: str, reason: str) -> None:
    global CHECKS
    CHECKS += 1
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {got}, wanted {want}")
    print(f"          reason given: {reason}")
    if not ok:
        FAILURES.append(label)


def main() -> int:
    root = tempfile.mkdtemp(prefix="capture-states-")
    print("=" * 78)
    print("CAPTURE STATE CLASSIFIER -- all six branches constructed and read back")
    print(f"  fixture root      {root}")
    print("  mutatesState      YES. This is a fixture. It measures no real install.")
    print("=" * 78)
    try:
        cases = [
            ("all three, one command -> COMPLETE",
             _hooks({e: HOOK for e in cc.HOOK_EVENTS}), cc.COMPLETE),
            ("all three, TWO different commands -> PARTIAL",
             _hooks({**{e: HOOK for e in cc.HOOK_EVENTS}, "SessionEnd": OTHER}), cc.PARTIAL),
            ("only SessionStart -> PARTIAL",
             _hooks({"SessionStart": HOOK}), cc.PARTIAL),
            ("valid settings wiring nothing -> ABSENT",
             "{}", cc.ABSENT),
            ("settings that is not JSON -> UNKNOWN",
             "{ this is not json", cc.UNKNOWN),
            ("no settings file at all -> UNKNOWN",
             None, cc.UNKNOWN),
        ]
        for i, (label, settings, want) in enumerate(cases):
            home = _home(root, f"case{i}", settings)
            inst = cc.survey(f"case{i}", home, FIXTURE_SETTINGS_NAME)
            check(label, inst.state, want, inst.state_reason)

        # The negative anchor. A positive anchor only anchors if the thing under test produces it:
        # if every shape above returned COMPLETE the run would still print six PASS lines unless
        # the states actually differ, so assert that they do.
        global CHECKS
        CHECKS += 1
        states = {cc.survey(f"case{i}", os.path.join(root, f"case{i}"), FIXTURE_SETTINGS_NAME).state
                  for i in range(len(cases))}
        # I wrote `{COMPLETE, PARTIAL, ABSENT}` here first and this check went red on its first
        # run. THE CHECK WAS WRONG, NOT THE CLASSIFIER: two of the six fixtures are UNKNOWN and I
        # had left it out of my own expected set. Kept as a note because a negative anchor that
        # fires on its author's arithmetic is the anchor working, and quietly correcting it would
        # have removed the only evidence that this line is capable of failing at all.
        want = {cc.COMPLETE, cc.PARTIAL, cc.ABSENT, cc.UNKNOWN}
        ok = states == want
        print(f"  [{'PASS' if ok else 'FAIL'}] the six fixtures produce ALL FOUR states: "
              f"{sorted(states)}, wanted {sorted(want)}")
        if not ok:
            FAILURES.append("states are distinguishable")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n" + "=" * 78)
    print(f"CHECKS {CHECKS}   PASS {CHECKS - len(FAILURES)}   FAIL {len(FAILURES)}")
    for name in FAILURES:
        print(f"  FAILED: {name}")
    print("=" * 78)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
