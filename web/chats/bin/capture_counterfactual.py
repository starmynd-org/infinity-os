"""WHAT CAPTURE WOULD HAVE HELD. A counterfactual, labelled as one on every line.

    python web/chats/bin/capture_counterfactual.py <label>=<claude-home> [more...] [--save DIR]

**THIS IS NOT EVIDENCE THAT CAPTURE WORKS. IT IS THE OPPOSITE, AND THE DISTINCTION IS THE WHOLE
POINT OF THE FILE.**

`2026-09-09-IOS-admiral`, `20260909T154314Z`, instructing this seat directly:

> *"Do not repair it, do not propose a script that repairs it, and do not work around it by reading
> transcripts directly as though capture were wired. Measure and report what capture would have
> held."*

**What that condition governs: every sentence this script emits.** The trap it names is specific and
this package makes it easy: `web/chats/history.py` reads transcript files for a completely different
reason — rendering history — **and a reader who sees rich per-session detail here could conclude
that capture is working.** It is not. **Nothing here has been captured. Nothing here is in an
index. No store was opened. The hook did not run.** These are files on a disk, and the point of
reading them is to say what the index is MISSING.

WHAT IT DERIVES, AND WHERE EACH FIELD WOULD HAVE COME FROM

`ingest/bin/claude-session-hook` calls three verbs -- `session_register` on `SessionStart`, a goal
from the first `UserPromptSubmit`, and `session_end` + `transcript_index` on `SessionEnd`. So for
each session on an install this reports what those calls WOULD have been given:

    session_id        the transcript filename                     would be the registry key
    workdir           decoded from the `projects/` directory name  session_register(workdir=...)
    first / last seen the earliest and latest record timestamps    session open and end
    records           the transcript's own record count            nothing; context for a reader
    goal available    whether a first user turn exists at all      the stated_goal, verbatim or none
    pointer + bytes   the file itself                              transcript_index(pointer=...)

**IT PRINTS NO TRANSCRIPT CONTENT, INCLUDING NO GOAL TEXT.** It reports only whether a goal WOULD
have been available. The goals are the operator's own first prompts and a counterfactual does not
need to quote them to count them.

**THE HASH IS NOT COMPUTED BY DEFAULT.** `transcript_index` stores a content hash, and hashing this
corpus is 609 MB of I/O for a number nobody can compare against anything, because there is no index
to compare it to. `--hash` computes it anyway if a later confirmation wants it; the default says
`(not computed)` rather than leaving a reader to assume it was.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

#: Subagent transcripts. **This constant exists because the first run of this script reported "123
#: rows" over 332 files and I published the 123 without asking what the other 209 were.**
#:
#: They are `agent-<hex>.jsonl`, and they are not noise: `ingest/bin/claude-session-hook`'s own
#: docstring says `SessionEnd` *"index[es] any subagent transcript of that session that has no row
#: yet (task 0329)"*. **So the hook would have indexed them too, and a counterfactual that counts
#: only main sessions understates what capture would have held by 63% of the files.**
#:
#: *Read the match, not the count.* My regex matched 123 things and I reported 123 without asking
#: what it had not matched. The unmatched set is where the finding was.
_SUBAGENT = re.compile(r"^agent-([0-9a-f]+)\.jsonl$", re.I)

MAIN, SUBAGENT, UNCLASSIFIED = "main", "subagent", "unclassified"


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def decode_project_dir(name: str) -> str:
    """Best-effort reverse of Claude Code's directory encoding. **Lossy, and labelled lossy.**

    The encoding maps every non-alphanumeric character to one hyphen, so `-` in the original and
    `\\` in the original are the same character afterwards. **The original is NOT recoverable** and
    this returns a readable approximation for a person, never a path for a program. `session_register`
    would have received the real `cwd` from the hook payload; this is not that value and does not
    pretend to be.
    """
    return name


@dataclass
class WouldHave:
    """One row the index would have held. **Every field is a counterfactual.**"""

    session_id: str
    #: `main`, `subagent`, or `unclassified`. **Never inferred from content; taken from the
    #: filename shape, and an unrecognised shape is `unclassified` rather than quietly `main`.**
    kind: str
    project_dir: str
    workdir_hint: str
    pointer: str
    bytes_on_disk: int
    mtime_utc: str
    records: int = 0
    first_seen: str = ""
    last_seen: str = ""
    goal_available: bool = False
    sha256: str = ""
    read_error: str = ""


@dataclass
class InstallRows:
    label: str
    home: str
    rows: list[WouldHave] = field(default_factory=list)
    unreadable: int = 0
    bytes_total: int = 0


def _scan_transcript(path: str, row: WouldHave, want_hash: bool) -> None:
    """One pass. Timestamps, record count, and whether a first user turn exists. No content kept."""
    digest = hashlib.sha256() if want_hash else None
    try:
        with open(path, "rb") as raw:
            for chunk in iter(lambda: raw.read(1 << 20), b""):
                if digest:
                    digest.update(chunk)
    except OSError as exc:
        row.read_error = f"unreadable: {exc}"
        return
    if digest:
        row.sha256 = digest.hexdigest()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row.records += 1
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                stamp = str(record.get("timestamp") or "")
                if stamp:
                    row.first_seen = row.first_seen or stamp
                    row.last_seen = stamp
                if not row.goal_available and record.get("type") == "user":
                    message = record.get("message")
                    if isinstance(message, dict) and message.get("content"):
                        # Whether a goal EXISTS. The text is not read out and is not stored here.
                        row.goal_available = True
    except OSError as exc:
        row.read_error = f"unreadable partway: {exc}"


def survey(label: str, home: str, want_hash: bool) -> InstallRows:
    out = InstallRows(label=label, home=home)
    root = os.path.join(home, "projects")
    if not os.path.isdir(root):
        return out
    for project in sorted(os.listdir(root)):
        directory = os.path.join(root, project)
        if not os.path.isdir(directory):
            continue
        for dirpath, _dirs, files in os.walk(directory):
            for name in sorted(files):
                if not name.endswith(".jsonl"):
                    continue
                path = os.path.join(dirpath, name)
                found = _UUID.search(name)
                sub = _SUBAGENT.match(name)
                if found:
                    kind, ident = MAIN, found.group(0).lower()
                elif sub:
                    kind, ident = SUBAGENT, sub.group(1).lower()
                else:
                    kind, ident = UNCLASSIFIED, ""
                try:
                    stat = os.stat(path)
                except OSError:
                    out.unreadable += 1
                    continue
                row = WouldHave(
                    session_id=ident,
                    kind=kind,
                    project_dir=project,
                    workdir_hint=decode_project_dir(project),
                    pointer=path,
                    bytes_on_disk=stat.st_size,
                    mtime_utc=_dt.datetime.fromtimestamp(
                        stat.st_mtime, _dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                )
                _scan_transcript(path, row, want_hash)
                if row.read_error:
                    out.unreadable += 1
                out.rows.append(row)
                out.bytes_total += row.bytes_on_disk
    return out


def main() -> int:
    args = list(sys.argv[1:])
    want_hash = "--hash" in args
    if want_hash:
        args.remove("--hash")
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
    started = time.perf_counter()
    installs = []
    for arg in args:
        if "=" not in arg:
            print(f"error: {arg!r} is not <label>=<claude-home>", file=sys.stderr)
            return 1
        label, home = arg.split("=", 1)
        installs.append(survey(label, home, want_hash))
    elapsed = time.perf_counter() - started

    print("=" * 78)
    print("WHAT CAPTURE WOULD HAVE HELD -- A COUNTERFACTUAL. THIS RUN CAPTURED NOTHING.")
    print(f"  execution as-of   {as_of}")
    print("  store             NONE OPENED. No index was QUERIED or COMPARED against.")
    print("  hook              NOT INVOKED by this run. Nothing here ran it or asked it to run.")
    print(f"  content hash      {'computed' if want_hash else 'NOT computed (pass --hash)'}")
    print("  transcript text   NOT PRINTED, including goals. Only whether one would exist.")
    print("=" * 78)

    for inst in installs:
        rows = inst.rows
        main = [r for r in rows if r.kind == MAIN]
        subs = [r for r in rows if r.kind == SUBAGENT]
        other = [r for r in rows if r.kind == UNCLASSIFIED]
        with_id = main
        with_goal = [r for r in main if r.goal_available]
        empty = [r for r in rows if r.records == 0]
        print(f"\nINSTALL {inst.label}   {inst.home}")
        print(f"  transcript files found                      {len(rows)}   <- DENOMINATOR")
        print(f"  of those, unreadable                        {inst.unreadable}")
        print(f"    MAIN sessions (uuid filename)             {len(main)}")
        print(f"    SUBAGENT transcripts (agent-<hex>)        {len(subs)}   the hook indexes these too")
        print(f"    UNCLASSIFIED (neither shape)              {len(other)}")
        # NAME THEM. An unknown bucket that reports only a COUNT forces the next reader to do the
        # archaeology I did: save the JSON, grep it for the row, and read the pointer out by hand.
        # I published `unclassified: 1` and the words "I have not looked into what it is", and the
        # cost of finding out afterwards was far higher than printing the path would have been.
        for row in other[:10]:
            print(f"        {row.pointer}")
        if len(other) > 10:
            print(f"        ... and {len(other) - 10} more; use --save for all of them")
        # INCLUDES `unclassified`, and that changed once I read `transcript.walk`: it selects on
        # the `.jsonl` extension alone, so the hook's enumeration does not care what shape a
        # filename is. Excluding the bucket would have made this figure depend on MY classifier
        # rather than on the hook's, which is the wrong thing for a counterfactual about the hook.
        print(f"  rows the index WOULD have held, ANY shape   {len(main) + len(subs) + len(other)}")
        print(f"  of the MAIN sessions, a stated_goal WOULD exist {len(with_goal)} of {len(main)}")
        print(f"  transcripts of ANY kind with ZERO records   {len(empty)}")
        print(f"  bytes transcript_index WOULD have pointed at {inst.bytes_total}")
        if with_id:
            first = min((r.first_seen for r in with_id if r.first_seen), default="")
            last = max((r.last_seen for r in with_id if r.last_seen), default="")
            print(f"  earliest / latest record timestamp          {first or '(none)'} .. "
                  f"{last or '(none)'}")
            biggest = max(with_id, key=lambda r: r.bytes_on_disk)
            print(f"  largest single session                      {biggest.bytes_on_disk} bytes, "
                  f"{biggest.records} records")

    print(f"\nelapsed {elapsed:.1f}s")
    print("\n" + "-" * 78)
    print("THE ASSUMPTIONS THE 'WOULD HAVE HELD' FIGURE RESTS ON. Narrowed on term-13's return at")
    print("160002Z: a file count is not an execution of the branches that would index it.")
    print("  The row total is a MAXIMUM CANDIDATE POINTER POPULATION, not an observed count of")
    print("  missing index rows, and it holds only if ALL of these were true, none of which was")
    print("  inspected here:")
    print("   A. a SessionEnd event actually fired for each session (a killed process fires none)")
    print("   B. the hook received a transcript_path in that payload AND it resolved on its own")
    print("      filesystem -- measured false for a Windows path under WSL, at 155954Z")
    print("   C. no row already existed: subagent indexing is insert-only and skips a file that")
    print("      has a row, so an already-indexed file is not a would-have-held row")
    print("   D. every agent-<hex> file was eligible, i.e. was a subagent OF a session the hook saw")
    print("\n  ON D, AND IT IS THE ONE ASSUMPTION THAT HAS NOW BEEN CHECKED AGAINST THE SOURCE:")
    print("  `ingest/ingest/transcript.py::walk` selects on the `.jsonl` EXTENSION ALONE -- no name")
    print("  shape, no depth limit -- and `backfill.index_missing_subagents` calls `tx.walk(sdir)`")
    print("  deliberately, its own comment saying 'a workflow's subagents/workflows/<wf>/ files are")
    print("  found at whatever depth they sit'. **So an UNCLASSIFIED file is still a row the hook")
    print("  would write.** It is counted here for that reason and not by oversight: the bucket")
    print("  records that this script cannot name the file's KIND, never that the hook would skip it.")
    print("-" * 78)
    print("HOW TO READ THIS, AND HOW NOT TO")
    print("  * Every number above is what an index WOULD hold under A-D. No index was queried.")
    print("  * A session appearing here is NOT a captured session. It is a file on a disk. It is")
    print("    also not a 'lost' or 'uncaptured' session: this run inspected CURRENT configuration")
    print("    only, and establishes nothing about what was configured when each file was written.")
    print("  * `a stated_goal WOULD be available` means a first user turn exists in the file. It")
    print("    does NOT mean the hook would have succeeded: the hook reads its goal from a")
    print("    UserPromptSubmit PAYLOAD, not from the transcript, and it exits 0 on every failure.")
    print("  * The workdir column is a DIRECTORY NAME, not a path. The encoding maps every")
    print("    non-alphanumeric character to one hyphen, so the original is not recoverable.")
    print("    `session_register` would have received the real cwd from the hook payload instead.")
    print("  * These rows are not proposed for loading anywhere. Loading them would BE the repair,")
    print("    and the repair is BLOCKED-ON-OPERATOR because it is a settings.json write.")
    print("-" * 78)

    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        payload = {"as_of": as_of, "counterfactual": True, "captured": False,
                   "hash_computed": want_hash,
                   "installs": [{"label": i.label, "home": i.home, "unreadable": i.unreadable,
                                 "bytes_total": i.bytes_total,
                                 "rows": [asdict(r) for r in i.rows]} for i in installs]}
        blob = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        path = os.path.join(save_dir, f"CAPTURE-COUNTERFACTUAL-{as_of}.json")
        with open(path, "wb") as fh:
            fh.write(blob)
        print(f"\nraw result   {path}")
        print(f"raw sha256   {hashlib.sha256(blob).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
