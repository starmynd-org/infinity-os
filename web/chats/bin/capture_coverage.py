"""CAPTURE CONFIGURATION SURVEY. Is the session hook WIRED? Not: did it fire.

    python web/chats/bin/capture_coverage.py <install-label>=<claude-home> [more...] [--save DIR]

A file, run from the file, per `SEAT-COMMON` §2 rule 4, so the seat that has to confirm it can
re-run it rather than take it.

---------------------------------------------------------------------------------------------
REVISION 2, `20260909T154500Z`. **RETURNED BY `2026-09-09-IOS-term-13` AND THE RETURN WAS RIGHT.**
Revision 1 (`b7953f3`) is superseded and its output is preserved rather than restamped.

**What revision 1 got wrong, in its words and mine:**

- **It was titled "does the hook FIRE" and it observes CONFIGURATION.** `term-13`: *"The script does
  not observe hook firing. Its CLAIM 1 header and 'cannot fire on 123 sessions' therefore exceed its
  measurement."* **A settings file is not an execution and reading one is not watching one.** Every
  claim in this revision is about wiring, and the word "fire" appears only where it is being denied.
- **Any non-empty `wired` dict counted as covered**, so an install wiring **1 of 3** events scored
  identically to one wiring 3 of 3. **A partial configuration read as a complete one.**
- **Unreadable or malformed settings folded into UNWIRED**, which reports a fact the instrument does
  not have. **Absent, malformed and unwired are three different states and revision 1 had one.**
- **Current wiring says nothing about HISTORICAL capture.** A settings file that is empty today may
  have been wired last week; a session from last week is not evidence either way. **Held as a
  separate unknown rather than folded into a coverage figure.**

**The four configuration states this revision distinguishes, and the predicate for each:**

    COMPLETE     all 3 of SessionStart, UserPromptSubmit, SessionEnd are wired to a command whose
                 path contains `claude-session-hook`, AND all three name the SAME command
    PARTIAL      1 or 2 of the 3 are wired. **Reported as its own state and never as covered.**
    ABSENT       the settings file is readable and valid and wires 0 of 3
    UNKNOWN      the settings file is missing, unreadable, or not valid JSON. **Not "unwired".**

**THE "SAME COMMAND" CLAUSE IS THIS INSTRUMENT'S OWN DECLARED POLICY, NOT AN EXTERNAL
REQUIREMENT** — flagged by `2026-09-09-IOS-term-13` and the flag is upheld. **Three hooks pointing
at three different wrappers could be perfectly functional**, and nothing in the install contract
says the command text must be identical. This survey **cannot decide functional equivalence**, so it
declines to call that shape COMPLETE and reports it as PARTIAL **with the reason spelled out** —
which is a conservative reading a reader can disagree with, and not a defect it has found.
**Anyone quoting a PARTIAL must read its `reason` to know which of the two shapes it is.**

**Only COMPLETE counts an install's sessions as sitting under a wired hook.** `PARTIAL` and
`UNKNOWN` are printed in their own columns and are added to no coverage number.
---------------------------------------------------------------------------------------------

WHAT CLAIM THIS RELATES TO. `MUST-NOT-BUILD.md` item 11 at `0180a51`:

> *"A session launched from a task and typed into from the console is captured by the same `ingest`
> session hook as any other Claude Code session, so it lands in the index the Sessions room reports
> on. The feature and its proof arrive together."*

**That sentence contains at least three claims and they fail separately:**

    A  the hook is WIRED on the install the session runs on   <- THIS SCRIPT, and only this
    B  the hook FIRED for that session and succeeded          <- NOT measured. Nothing here watches it.
    C  the session LANDED in the index                        <- NOT measured. No store is opened.

**Claim A is a precondition for B and B for C**, which is the only reason measuring A alone is
worth anything: **an install wiring 0 of 3 events cannot satisfy B for sessions run while that was
true, and that is a statement about configuration, not about any particular session.**

**Why no store: `ingest/ingest/config.py` sets `BRAIN_DB = "brain"` as the default DSN and
`SEAT-COMMON` §1 forbids touching `brain`.** So this opens no database, rather than substituting a
filesystem reading for a store one.

WHY THE DENOMINATOR IS THE WHOLE MEASUREMENT. There is more than one Claude Code installation on
this machine and they do not share a `~/.claude`. **A coverage figure over one of them is a true
number about a fraction of reality.** So the installs are ARGUMENTS and this script guesses none.

IT PUBLISHES NO TRANSCRIPT CONTENT. Counts, types, sizes, paths and configuration only.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict

HOOK_MARK = "claude-session-hook"
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "SessionEnd")

COMPLETE, PARTIAL, ABSENT, UNKNOWN = "COMPLETE", "PARTIAL", "ABSENT", "UNKNOWN"

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def utc_now() -> str:
    """The execution as-of, read from the clock at run time. **Never estimated, never carried.**"""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class Install:
    label: str
    home: str
    present: bool = False
    settings_path: str = ""
    settings_bytes: int = -1
    settings_sha256: str = ""
    read_error: str = ""
    wired: dict[str, str] = field(default_factory=dict)
    state: str = UNKNOWN
    state_reason: str = ""
    project_dirs: int = 0
    transcripts: int = 0
    session_ids: list[str] = field(default_factory=list)
    hook_log_path: str = ""
    hook_log_bytes: int = -1
    hook_log_lines: int = 0
    hook_log_mtime_utc: str = ""
    hook_log_ids: int = 0

    @property
    def sessions(self) -> int:
        return len(self.session_ids)


def _classify(inst: Install) -> None:
    """Decide the configuration state. **Four outcomes, and each one names its own reason.**"""
    if inst.read_error:
        inst.state, inst.state_reason = UNKNOWN, inst.read_error
        return
    count = len(inst.wired)
    if count == len(HOOK_EVENTS):
        commands = set(inst.wired.values())
        if len(commands) == 1:
            inst.state = COMPLETE
            inst.state_reason = f"all {count} of {len(HOOK_EVENTS)} events wired to one command"
        else:
            # DECLARED INSTRUMENT POLICY, not a defect finding. `term-13` is right that different
            # wrappers could be functionally equivalent and that this survey cannot decide that,
            # so the reason says exactly what was seen and leaves the judgement to the reader.
            inst.state = PARTIAL
            inst.state_reason = (
                f"all {count} events wired, but to {len(commands)} DIFFERENT commands. This "
                f"instrument declines to call that COMPLETE as a matter of its own declared "
                f"policy; it cannot decide whether the wrappers are functionally equivalent, and "
                f"no install contract read here requires identical command text")
    elif count:
        inst.state = PARTIAL
        inst.state_reason = (f"{count} of {len(HOOK_EVENTS)} events wired; missing "
                             f"{', '.join(e for e in HOOK_EVENTS if e not in inst.wired)}")
    else:
        inst.state = ABSENT
        inst.state_reason = (f"settings read and valid; 0 of {len(HOOK_EVENTS)} events name a "
                             f"command containing {HOOK_MARK!r}")


#: The file a real install keeps its configuration in. **A parameter, and the reason is a
#: prohibition rather than flexibility.** `SEAT-COMMON` §1: *"Never write any `settings.json`."*
#: `2026-09-09-IOS-term-13` pointed out that the rule names ANY file of that name, not only a real
#: user's config, and it is right: my first classifier proof wrote six synthetic ones in a temp
#: directory. **Reading one is not writing one, so the survey's default is unchanged**; the proof
#: passes a different name so that no fixture in this package ever creates a `settings.json` again.
SETTINGS_NAME = "settings.json"


def _read_settings(inst: Install, settings_name: str = SETTINGS_NAME) -> None:
    inst.settings_path = os.path.join(inst.home, settings_name)
    try:
        raw = open(inst.settings_path, "rb").read()
    except OSError as exc:
        inst.read_error = f"{settings_name} unreadable: {exc}"
        return
    inst.settings_bytes = len(raw)
    inst.settings_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        inst.read_error = f"{settings_name} is not valid JSON: {exc}"
        return
    if not isinstance(data, dict):
        inst.read_error = f"{settings_name} top level is {type(data).__name__}, not an object"
        return
    hooks = data.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        inst.read_error = f"{settings_name} hooks key is {type(hooks).__name__}, not an object"
        return
    for event in HOOK_EVENTS:
        for entry in (hooks or {}).get(event) or []:
            if not isinstance(entry, dict):
                continue
            for hook in entry.get("hooks") or []:
                if isinstance(hook, dict) and HOOK_MARK in str(hook.get("command") or ""):
                    inst.wired[event] = str(hook.get("command"))


def _walk_projects(inst: Install) -> None:
    root = os.path.join(inst.home, "projects")
    if not os.path.isdir(root):
        return
    seen: set[str] = set()
    for name in sorted(os.listdir(root)):
        directory = os.path.join(root, name)
        if not os.path.isdir(directory):
            continue
        inst.project_dirs += 1
        for dirpath, _dirs, files in os.walk(directory):
            for fname in files:
                if not fname.endswith(".jsonl"):
                    continue
                inst.transcripts += 1
                found = _UUID.search(fname)
                if found:
                    seen.add(found.group(0).lower())
    inst.session_ids = sorted(seen)


def _read_hook_log(inst: Install) -> None:
    path = os.path.join(inst.home, "brain-session-hook.log")
    inst.hook_log_path = path
    if not os.path.isfile(path):
        return
    stat = os.stat(path)
    inst.hook_log_bytes = stat.st_size
    inst.hook_log_mtime_utc = _dt.datetime.fromtimestamp(
        stat.st_mtime, _dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ids: set[str] = set()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                inst.hook_log_lines += 1
                for found in _UUID.finditer(line):
                    ids.add(found.group(0).lower())
    except OSError as exc:
        inst.read_error += f" (hook log unreadable: {exc})"
    inst.hook_log_ids = len(ids)


def survey(label: str, home: str, settings_name: str = SETTINGS_NAME) -> Install:
    """Survey one install. `settings_name` exists for the fixture, never for a real measurement.

    **A real run always leaves it at the default.** It is a parameter because `SEAT-COMMON` §1
    forbids WRITING any `settings.json`, and the classifier's branches can only be exercised by
    constructing files of each shape. Constructing them under a different name keeps the fixture
    inside the rule instead of inside an interpretation of it.
    """
    inst = Install(label=label, home=home)
    inst.present = os.path.isdir(home)
    if not inst.present:
        inst.state, inst.state_reason = UNKNOWN, "the given claude home is not a directory"
        return inst
    _read_settings(inst, settings_name)
    _walk_projects(inst)
    _read_hook_log(inst)
    _classify(inst)
    return inst


def report(installs: list[Install], as_of: str, saved: str, digest: str) -> int:
    print("=" * 78)
    print("INFINITY OS -- CAPTURE CONFIGURATION SURVEY, revision 2")
    print(f"  execution as-of   {as_of}   (read from the clock in THIS run)")
    print("  measures          whether the hook is WIRED. NOT whether it fired. NOT the index.")
    print("  store             NONE OPENED.")
    print(f"  installs          {len(installs)}, all given on the command line; none guessed")
    if saved:
        print(f"  raw result        {saved}")
        print(f"  raw sha256        {digest}")
    print("=" * 78)

    by_state: dict[str, int] = {}
    total: set[str] = set()
    per_state_sessions: dict[str, set[str]] = {}

    for inst in installs:
        print(f"\nINSTALL {inst.label}   ->   {inst.state}")
        print(f"  reason               {inst.state_reason}")
        print(f"  home                 {inst.home}")
        if not inst.present:
            continue
        print(f"  settings.json        {inst.settings_bytes} bytes"
              f"  sha256 {inst.settings_sha256[:16] or '(none)'}")
        for event in HOOK_EVENTS:
            print(f"      {event:<18} {inst.wired.get(event, 'NOT WIRED')}")
        print(f"  project directories  {inst.project_dirs}")
        print(f"  transcript files     {inst.transcripts}")
        print(f"  distinct session ids {inst.sessions}   <- this install's denominator")
        if inst.hook_log_bytes >= 0:
            print(f"  hook log             {inst.hook_log_bytes} bytes, {inst.hook_log_lines} lines,"
                  f" last written {inst.hook_log_mtime_utc}")
            print(f"  ids named in the log {inst.hook_log_ids}   (a FAILURE log; not a numerator)")
        else:
            print("  hook log             ABSENT")
        by_state[inst.state] = by_state.get(inst.state, 0) + 1
        total |= set(inst.session_ids)
        per_state_sessions.setdefault(inst.state, set()).update(inst.session_ids)

    print("\n" + "-" * 78)
    print("COMBINED, DENOMINATOR FIRST")
    print(f"  distinct sessions across every install given          {len(total)}")
    for state in (COMPLETE, PARTIAL, ABSENT, UNKNOWN):
        ids = per_state_sessions.get(state, set())
        share = (100.0 * len(ids) / len(total)) if total else 0.0
        print(f"  sessions on an install whose config is {state:<9} {len(ids):>6}   {share:5.1f}%")
    print(f"  installs by state    " + ", ".join(f"{k}={v}" for k, v in sorted(by_state.items())))
    print("-" * 78)

    print("\nWHAT THIS DOES **NOT** ESTABLISH. Four separate unknowns, held separate on")
    print("`2026-09-09-IOS-term-13`'s return, and none of them folded into a number above:")
    print("  1. WHETHER THE HOOK EVER FIRED, anywhere. This reads settings files. It watches no")
    print("     process. An install classified COMPLETE may still have missed every session:")
    print("     the hook exits 0 on every failure BY DESIGN -- its own docstring says 'It exits 0")
    print("     no matter what' -- so a miss leaves no trace anywhere this script can see.")
    print("  2. WHETHER HISTORICAL SESSIONS WERE CAPTURED. Configuration is read as it is NOW.")
    print("     A file that wires nothing today may have wired three events last week, and a")
    print("     transcript from last week is evidence for neither. The session population and the")
    print("     configuration state are measured at the same instant and are otherwise unrelated.")
    print("  3. WHETHER THE EFFECTIVE CONFIGURATION IS THIS FILE. Only <home>/settings.json is")
    print("     read. Project-level settings, local overrides, managed policy and command-line")
    print("     flags are NOT read. This is one file's contents, not a resolved configuration.")
    print("  4. WHETHER ANY SESSION IS THE KIND ITEM 11 NAMES -- 'launched from a task and typed")
    print("     into from the console'. Nothing here distinguishes a console-launched session")
    print("     from any other. A session on an ABSENT install is not thereby a counter-example")
    print("     to item 11's sentence; it is a session on an install wiring 0 of 3 events.")
    print("\nAnd two properties of the figures themselves:")
    print("  * The hook log is a FAILURE log. 'ids named in the log' is a floor on trouble and is")
    print("    combined with nothing.")
    print("  * Transcript files are counted by walking `projects/`. A pruned transcript is invisible,")
    print("    so the denominator UNDERSTATES the population. The direction is stated, not corrected.")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:]]
    save_dir = ""
    if "--save" in args:
        idx = args.index("--save")
        try:
            save_dir = args[idx + 1]
        except IndexError:
            print("error: --save needs a directory", file=sys.stderr)
            return 1
        del args[idx:idx + 2]
    if not args:
        print(__doc__)
        print("error: give at least one <label>=<claude-home>", file=sys.stderr)
        return 1

    as_of = utc_now()
    installs = []
    for arg in args:
        if "=" not in arg:
            print(f"error: {arg!r} is not <label>=<claude-home>", file=sys.stderr)
            return 1
        label, home = arg.split("=", 1)
        installs.append(survey(label, home))

    saved = digest = ""
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        payload = {
            "as_of": as_of,
            "revision": 2,
            "measures": "hook wiring in <home>/settings.json only; not firing, not the index",
            "installs": [asdict(i) for i in installs],
        }
        blob = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(blob).hexdigest()
        saved = os.path.join(save_dir, f"CAPTURE-CONFIG-{as_of}.json")
        with open(saved, "wb") as fh:
            fh.write(blob)

    return report(installs, as_of, saved, digest)


if __name__ == "__main__":
    raise SystemExit(main())
