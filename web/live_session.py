"""The live view of the operator's OWN Claude Code terminals. A reader, and only a reader.

WHY THIS EXISTS. Asked on 2026-08-28 what one thing would make him run his real day through this
product, Andrew answered the terminal: *"it's supposed to actually have like a terminal and a
beautiful UX wrapper on top of that terminal because a lot of times people are using like Claude
code and things like that in the terminal."* This module is the READ half of that: his own
sessions, live, rendered from the JSONL the harness is appending to while he watches.

It calls no verb, writes no row and touches no file. The room it renders in is `sessions`, whose
`ROOM_VERBS` entry is an empty frozenset, so `web/tests/test_allowlist.py` already proves every
registered verb in the runtime is refused here before `store.apply` is entered.

**IT IS NOT A TERMINAL EMULATOR AND MUST NOT PRETEND TO BE.** No PTY text exists in these files.
Claude Code writes one JSON record per line and this reads that. Drawing terminal chrome around it
would be a lie about what the operator is looking at, in a product whose whole claim is that its
surfaces do not lie about what they have.

================================================================================================
THE RENDERED UNIT IS THE EXCHANGE, AND THAT IS THE ONE REAL DESIGN DIFFERENCE FROM `runfeed.py`.

`web/runfeed.py` chose the BEAT: one assistant text block, the agent saying what it is doing or
found, with everything between two beats folded to a count. It chose that because a fleet run has
exactly one speaker. **An interactive session has two, and the second one is Andrew.**

So the spine here is what HE TYPED, and the beats hang off it. One `said` plus the beats that
followed it is an exchange, and everything else folds. Measured on a real session of his,
`c8691bac` of 2026-08-28, 309 lines:

    line   8   HUMAN   why doesn't buzz have notifications?
    line 166   BEAT    Two separate upstream gaps, and neither one is your relay...
    line 170   HUMAN   Is there anything fixing the transcripts which seemed much worse...
    line 243   BEAT    Short answer: no. Nothing open fixes English accuracy...
    line 252   HUMAN   yes
    line 255   BEAT    I'll do both. Starting with the build-readiness check...
    line 302   BEAT    Both done. ## #4534 build-readiness: it does not merge cleanly...

309 lines render as 7 rows, and those 7 rows are the whole record of what happened. That is the
test the unit has to pass, and it is why the unit is not the line, the tool call or the turn:
his own words are the only index into his own day that he already holds in his head.

**IDENTIFYING HIS WORDS IS A MEASUREMENT, NOT A GUESS, and getting it wrong would drown the page.**
`type: "user"` is not the human. Measured over this host's whole corpus, 1,489 transcript files and
100,303 `user` records: **3,339 are speech and 96,964 are tool results wearing the user role.** A
reader that took the type at face value would render ninety-seven thousand tool results as things
somebody said. The test is `origin`, and `_who` below holds the full mapping with its measurements.

A SUBAGENT SESSION HAS NO HUMAN and is still worth watching, so the same unit covers it: its spine
is the brief it was launched with. It renders as a `said` from the launcher, and **it does not get
the brand accent**, because brand is reserved for operator causality and a subagent's brief is not
the operator speaking. Neither is a coordinator's mid-run message, a peer session's, or a harness
notification, and all four arrive on a `user` record.

================================================================================================
FOUR TRAPS MEASURED ON THIS HOST'S OWN CORPUS ON 2026-08-28. All four shape the code below.

1. **THE SUPPRESSED KEY IS EVERYWHERE HERE, NOT IN ONE EVENT.** `runfeed.py` names the hazard:
   `system/init` carries `permissionMode` verbatim and putting `permission_mode` on the operator's
   screen is forbidden absolutely and in every state. In the fleet's stream-json that key rides ONE
   event type, which is folded anyway, so refusing the type is a complete defence.

   **In this format it is worse in two distinct ways.** First, there is an entire record type whose
   whole payload is the key: `{"type":"permission-mode","permissionMode":"auto",...}`, and this
   host's corpus holds 7,700 of them. Second, and this is the one that would have leaked,
   `permissionMode` is a TOP-LEVEL KEY ON THE HUMAN'S OWN PROMPT RECORD -- on exactly the record
   this module exists to render -- and 2,582 `user` records carry it. `runfeed`'s strategy of
   refusing one event type is NOT sufficient here.

   **Measured after the defence was built, over all 10,282 records in the corpus that carry a
   suppressed key: 0 produced a churn line, and all 3,339 rows rendered as speech carry text
   byte-identical to their own `message.content`.** The string does still appear on this page in
   one place, and that place is correct: five sessions in which Andrew and Claude were discussing
   this very rule. The prohibition is on the console reporting a session's permission mode as a
   fact, not on the letters, and censoring his own prose about his own system would be its own lie.

   So the defence is two paths and a rule, rather than a filter:
     * `_line()` -- the churn path -- refuses ANY record carrying a suppressed key at top level,
       before it is turned into text, and refuses the carrier types outright as well.
     * `_text_of()` -- the rendered path -- reads an explicit field allowlist and nothing else
       (`message.content` text, plus `timestamp` and a derived `who` on the entry). A record is
       never copied onto an entry, so there is no shape in which an unforeseen key that a future
       harness version adds rides out of this module.
   `_SUPPRESSED_KEYS` and `_REFUSED_TYPES` are named as data so the check is greppable, as
   `runfeed.py` names its own.

2. **THE `usage` BLOCK IS REPEATED ON EVERY CONTENT-BLOCK RECORD OF ONE ASSISTANT MESSAGE, and
   summing it naively overstates spend 2.22x.** Claude Code writes one record per content block,
   and every one of them carries the same completed `message.usage`. Measured on `c8691bac`: 101
   assistant records, 37 distinct `message.id`, `usage` identical across all three records of a
   message. Measured over the whole corpus: **173,236 assistant records carrying usage are 83,350
   distinct API messages, and the naive sum prices them at $27,631.51 against $12,448.93
   deduplicated.** `message.id` was present on 173,236 of 173,236, so the dedupe key never has to
   be guessed. This is the same class of defect the price card already names for the fleet ("LAST
   WINS WITHIN A SESSION", `budget/price_card.py:210`), arriving in a different file format.

   `budget/price_card.py` is then asked for the dollars, exactly as `runfeed.py` asks it: ONE
   implementation of cost in this runtime, not two. `derive_usd()` takes the four component keys
   and this format spells them identically, so nothing is converted and nothing is re-derived.
   `ingest/ingest/transcript.py` deliberately leaves `cost_usd` None because "deriving one needs a
   price table keyed by model and date, which this lane does not own." That table now exists.

3. **A TORN TAIL IS NORMAL, AND HERE IT CAN STAY TORN FOR SEVERAL POLLS.** The runfeed learned
   that a file being appended to while you read it routinely ends in half a JSON object, that this
   renders as NOTHING because it will be whole next poll, and that a file of nothing but torn lines
   is a different, amber state. All three hold. What is different is duration: the longest single
   line in this corpus is **1,356,155 characters**, one JSON record of 1.3MB, because a tool result
   is a whole file. A line that size is not written atomically, so the tail can be half-written
   across two or three consecutive three-second polls rather than one -- watched at 300KB, 900KB
   and complete, and it rendered nothing at all until it was whole. The count is therefore rendered
   as a dim fact on the vitals rather than being invisible, so a reader who meets it twice in a row
   learns what it is instead of wondering what he missed.

   **The torn tail also caused the one real defect this lane shipped and then caught**, and the fix
   is in `read_session` under the heading THE CURSOR IS NOT `pos`. It is worth reading before
   touching the poll: a cursor that counts a half-written line has silently skipped the record that
   line becomes.

4. **`ended_at` DOES NOT MEAN THE SESSION ENDED, SO LIVENESS COMES FROM THE FILE.** Measured on the
   live store: 1,735 sessions, **414 with `ended_at` set**. The hook writes it at `SessionEnd` and
   a terminal that is still open, was killed, or crashed never gets one. A picker that called every
   null-`ended_at` session live would call 1,321 sessions live. The only honest liveness signal is
   the transcript file's own mtime, and this module says so on the screen: the state word is
   derived from mtime and the fact ("last written 12s ago") is rendered beside it, never replaced
   by it.

   The corollary shapes the picker and is worth stating on its own: `web/sessions.py` trap 1
   records that the index has a FLOOR of live sessions, because the hook indexes at `SessionEnd`,
   so **every session open right now is a file on disk with no row in `brain.session`.** A picker
   built from the store alone would systematically omit the exact sessions this page exists to
   show. So the picker is DISK-FIRST and store-enriched: the disk decides what exists and how
   recently it moved, and the store supplies the goal, the role and the parentage where it has
   them. Measured cost of the walk: 12.9ms for 1,489 files, one stat each and no reads.

================================================================================================
WHAT IT COSTS TO READ ONE SESSION, because this is the surface meant to stay open all day.

The whole file is read and parsed on every poll, as `runfeed.read_run` does, because a counter that
only counted the new lines would tick down to nothing on a quiet session. Measured over the corpus
as it stands: 1,489 files, 1.62GB, median 645KB, 90th percentile 2.06MB, largest 18.7MB. One whole
`read_session` costs **62.9ms on the largest file and 3.8ms on the median**, so the poll a page
actually makes costs a few milliseconds and the worst case is still far inside the three-second
cadence. `MAX_READ_BYTES` is a ceiling above that worst case, and when it bites the page SAYS SO
rather than reading forever or silently showing a prefix: a truncation nobody mentions reads as
"that was all of it".

Only entries above the stream position the client already holds are returned, so the feed region is
append-only and a 17MB file is never sent down the wire.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import store

from . import runfeed

# ONE implementation of cost in this runtime, not two. See trap 2.
try:
    from budget import price_card
except Exception:                                                       # noqa: BLE001
    price_card = None            # the vitals say `cost unavailable`, and say why, rather than 0


# ------------------------------------------------------------------ the projects root, config
#
# NEVER A HARDCODED PATH, for `runfeed.runs_dir`'s reason. The resolution order below is the one
# `ingest/` already uses (`ingest/ingest/queries.py:179`, `backfill.py:206`) plus one console
# override above it, so a console pointed at another host's mounted projects has a way in and the
# two halves of the runtime cannot disagree about where transcripts live.

def projects_root() -> Path:
    """Where Claude Code writes transcripts on THIS host. Config, in three steps, no guesses."""
    env = os.environ.get("CONSOLE_PROJECTS_ROOT")
    if env:
        return Path(env).expanduser()
    return Path(os.environ.get("CLAUDE_PROJECTS_ROOT") or "~/.claude/projects").expanduser()


#: Above this, the reader refuses and the page says why. See the cost paragraph in the docstring:
#: the largest file in this corpus is 18.7MB and a whole read of it costs 62.9ms, so this is
#: roughly 2.5x headroom over the worst case measured rather than a number that bites on a normal
#: day. It is a bound and the page says when it bit.
MAX_READ_BYTES = 48 * 1024 * 1024

#: The picker's head-read budget for a session the index has never seen. See `_head_goal`.
HEAD_BYTES = 256 * 1024


# ------------------------------------------------------------------ the two suppression lists
#
# TRAP 1. Both are data so the check is greppable, exactly as `runfeed._SUPPRESSED_KEYS` is.

#: Keys that must not reach the operator's screen through any line this module returns. The rule is
#: the brief's, absolute and in every state: `permission_mode` is what makes an agent's self-report
#: trustworthy, and a surface that shows it invites the one change that would quietly make every
#: self-report worthless. In THIS format the key is stamped on the human's own prompt record, which
#: is why the rendered path is an allowlist and not a filter.
_SUPPRESSED_KEYS = ("permissionMode", "permission_mode", "bypassPermissions")

#: Record types refused outright, before any text is made from them.
#:
#:   permission-mode  the whole payload IS the forbidden key.
#:   mode             the same class of fact: what the operator switched the terminal into. It is
#:                    not a fact about the work, and refusing it is deliberate rather than an
#:                    oversight, so a reviewer who wants it back finds a decision here.
#:   attachment       hook output, verbatim, up to 15KB a record. On this host that is the
#:                    operator's global CLAUDE.md and his memory index. It is bookkeeping, not
#:                    conversation, and quoting it 400 characters at a time inside a fold would put
#:                    private context on a screen that exists to show a conversation.
_REFUSED_TYPES = ("permission-mode", "mode", "attachment")

#: Types that are harness bookkeeping and carry nothing a reader wants, but leak nothing either.
#: They are counted into folds and produce no line, which is how a fold's count stays honest about
#: how many records went by without inventing text for them.
_SILENT_TYPES = ("last-prompt", "atis-latch", "ai-title", "bridge-session",
                 "file-history-snapshot", "file-history-delta")


def _carries_suppressed(obj: dict) -> bool:
    """Does this record carry a forbidden key at top level? O(1), asked of the DICT not the text.

    Asked of the parsed record rather than of the rendered string on purpose. A value check would
    be fragile -- `auto`, `plan` and `default` are ordinary English -- and a string check on the
    output only catches the leak after the text has been built.
    """
    return any(k in obj for k in _SUPPRESSED_KEYS)


def _line(obj) -> str:
    """One churn line, or "" for a record that may not produce one.

    `runfeed.stream_event_line` is called rather than reimplemented: it is the display contract for
    what a churn line says, including its 300/400 truncation limits, and this format's `message`
    is the same Anthropic message shape it already reads. What is NOT reused is the decision about
    which records may speak, because that is a decision about THIS surface -- the same split
    `runfeed.churn_line` makes for its own.
    """
    if not isinstance(obj, dict):
        return ""
    kind = str(obj.get("type") or "")
    if kind in _REFUSED_TYPES or kind in _SILENT_TYPES:
        return ""
    if _carries_suppressed(obj):
        return ""
    line = runfeed.stream_event_line(obj)
    # Belt to the braces above: a key that arrived nested, in a future harness version, still does
    # not leave this module.
    return "" if any(k in line for k in _SUPPRESSED_KEYS) else line


# ------------------------------------------------------------------ reading one session file


def _blocks(obj) -> list:
    msg = obj.get("message")
    content = msg.get("content") if isinstance(msg, dict) else obj.get("content")
    return content if isinstance(content, list) else []


def _text_of(obj) -> str:
    """The human's words. `message.content` only, as a string or as text blocks, and nothing else.

    THE ALLOWLIST OF TRAP 1 IS THIS FUNCTION AND `_blocks` ABOVE. Nothing else on the record is
    ever read onto an entry, so `permissionMode`, `cwd`, `gitBranch` and every key a future harness
    version adds are absent by construction rather than by filtering.
    """
    msg = obj.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content.strip()
    parts = []
    for b in content if isinstance(content, list) else []:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(str(b.get("text") or "").strip())
    return "\n\n".join(p for p in parts if p)


#: `origin.kind` -> how the row is labelled, for everything that is speech but is not Andrew.
#: Named as data because the set is the harness's and will grow, and a missing name should render
#: as churn rather than as an unattributed sentence in the operator's own column.
_ORIGINS = {
    "task-notification": "harness · background task",
    "coordinator": "the session that launched this one",
    "peer": "another session",
}


def _who(obj, is_first_user: bool = False) -> tuple:
    """`(who, operator)` for a `user` record, or `(None, False)` if it is not speech at all.

    `type: "user"` is overwhelmingly a tool result wearing the user role. Measured over the whole
    corpus on this host, 855 main transcripts and 634 subagent transcripts:

        MAIN      50,189 list:tool_result          against   1,332 with origin.kind == "human"
        SUBAGENT  46,435 list:tool_result          against     633 launch briefs

    A reader that treated `user` as speech would render 96,624 tool results as things somebody
    said. That is the whole difficulty of this format and it is why the test is `origin`, not type.

    **THE SUBAGENT BRIEF CARRIES NO `origin` AND NO `promptSource`, and this function shipped
    missing all 633 of them before it was tested against a live one.** The historical corpus has
    `promptSource: "sdk"` on 614 launch briefs, all of them in MAIN transcripts from headless
    runs, so a rule written from history alone looked complete and rendered a live subagent with
    no spine at all. What the harness writes today for a sidechain's brief is a plain-string
    `user` record with `isSidechain: true` and nothing else, and it is always the FIRST user
    record in the file. All four conditions are required together: first, sidechain, string
    content, no origin. Requiring `isSidechain` is what keeps the 16 `<local-command-caveat>`
    records that open some MAIN transcripts out of the operator's column.

    `operator` is what earns the brand accent, and it is spent on exactly one thing: a prompt the
    human typed. The design guide reserves brand for operator causality, so a subagent's brief, a
    coordinator's mid-run message, a peer session and a harness notification all render as plain
    rows even though every one of them arrives on a `user` record.
    """
    origin = obj.get("origin")
    kind = str((origin or {}).get("kind") or "") if isinstance(origin, dict) else ""
    source = str(obj.get("promptSource") or "")
    if kind == "human":
        return "you", True
    if kind in _ORIGINS:
        return _ORIGINS[kind], False
    if source == "sdk":
        return "launched with this brief", False
    if (is_first_user and obj.get("isSidechain") is True and not kind
            and isinstance((obj.get("message") or {}).get("content"), str)):
        return "launched with this brief", False
    return None, False


class _Usage:
    """Deduplicated token basis. TRAP 2: one bag per `message.id`, never one per record."""

    def __init__(self):
        self.tokens = {"input_tokens": 0, "output_tokens": 0,
                       "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        self.seen: set = set()
        self.records = 0
        self.models: set = set()

    def add(self, obj) -> None:
        msg = obj.get("message")
        if not isinstance(msg, dict):
            return
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            return
        self.records += 1
        model = str(msg.get("model") or "")
        if model and model != "<synthetic>":
            self.models.add(model)
        # `message.id` was present on 173,236 of 173,236 records carrying usage across the whole
        # corpus. `requestId` is the fallback and agreed with it on every one of them.
        key = msg.get("id") or obj.get("requestId")
        if not key or key in self.seen:
            return
        self.seen.add(key)
        for c in self.tokens:
            v = usage.get(c)
            if isinstance(v, int):
                self.tokens[c] += v

    @property
    def messages(self) -> int:
        return len(self.seen)


def read_session(path, since: int = 0) -> dict:
    """One transcript file -> exchanges, beats, folds, and the counters the vitals need.

    `since` is the CURSOR the client already holds: the line number of the last WHOLE record the
    server had read, 1 based, which is not the same as the number of lines on disk. Only entries
    above it come back, which is what makes the feed region append-only. The counters are computed
    over the WHOLE file, because a count that only counted the new lines would tick down to nothing
    on a quiet session.

    Every dict returned is JSON-safe and carries `pos`, so the client can key on it.
    """
    p = Path(path)
    out = {
        "path": str(p), "exists": p.exists(), "readable": True, "error": "",
        "entries": [], "position": 0, "lines": 0, "said": 0, "beats": 0, "tools": 0,
        "events": 0, "torn": 0,
        "live_tool": None, "open_fold": None, "last_event_at": None, "first_event_at": None,
        "session_id": "", "now_line": "", "bytes": 0, "mtime": None,
        "tokens": None, "messages": 0, "usage_records": 0, "models": [],
    }
    if not p.exists():
        out["readable"] = False
        return out
    try:
        st = p.stat()
    except OSError as exc:
        out["readable"] = False
        out["error"] = f"{exc.__class__.__name__}: {exc}"
        return out
    out["bytes"], out["mtime"] = st.st_size, st.st_mtime

    if st.st_size > MAX_READ_BYTES:
        # NO SILENT PREFIX. Reading the first N bytes of a conversation and rendering it as the
        # conversation is the shape this system spends its refusals on.
        out["readable"] = False
        out["error"] = (f"{st.st_size:,} bytes is over the {MAX_READ_BYTES:,} byte read ceiling. "
                        f"Nothing is shown rather than a prefix shown as the whole file.")
        return out

    entries: list = []
    pos = 0
    last_ok = 0                # the cursor. See `position` below: it is NOT `pos`.
    pending: list = []
    live_tool = None
    last_at = ""
    usage = _Usage()
    n_said = n_beat = 0
    first_user = True

    def flush(at_pos):
        """A run of non-conversation records becomes ONE fold row.

        CLOSED FOLDS ONLY GO IN `entries`, for `runfeed.flush_churn`'s reason: a closed fold's
        `pos` never changes again, which is the whole basis of an append-only feed keyed by
        position. The churn after the last rendered row is still growing, so it rides `open_fold`
        and is replaced in place. Appending it would re-send the same fold with a higher `pos` on
        every poll and the operator would watch one row breed.
        """
        if not pending:
            return
        first, last = pending[0], pending[-1]
        entries.append({
            # KEYED BY KIND AND POSITION, NOT BY POSITION. A fold is closed BY the row that
            # follows it and is stamped with that row's position, so `fold` and `said` at line 8
            # of a real session share a number. The client appends by key and skips a key it has
            # already seen, so a bare position would silently drop one of the two.
            "kind": "fold", "key": f"fold-{at_pos}", "pos": at_pos, "n": len(pending),
            "tools": sum(1 for c in pending if c["is_tool"]),
            "from_pos": first["pos"], "to_pos": last["pos"],
            "seconds": runfeed._between(first["at"], last["at"]),
        })
        pending.clear()

    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        out["readable"] = False
        out["error"] = f"{exc.__class__.__name__}: {exc}"
        return out

    for line in raw.splitlines():
        pos += 1
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            # THE TORN TAIL. Counted, never rendered, never an error. Whole next poll -- or, on a
            # 1.3MB tool-result line, the poll after that. See trap 3.
            out["torn"] += 1
            continue
        if not isinstance(obj, dict):
            continue
        last_ok = pos
        out["events"] += 1
        out["session_id"] = obj.get("sessionId") or out["session_id"]
        at = obj.get("timestamp") or ""
        if at:
            out["last_event_at"] = at
            out["first_event_at"] = out["first_event_at"] or at
            last_at = at
        else:
            # Same reading as `runfeed`: the harness bookkeeping records carry no `timestamp`, and
            # a fold whose two ends are both blank would report a duration of zero, which reads as
            # "that took no time" rather than "nobody stamped it".
            at = last_at
        kind = str(obj.get("type") or "")

        if kind == "user":
            who, operator = _who(obj, is_first_user=first_user)
            first_user = False
            if who:
                text = _text_of(obj)
                if text:
                    flush(pos)
                    n_said += 1
                    entries.append({"kind": "said", "key": f"said-{pos}",
                                    "pos": pos, "at": at, "text": text,
                                    "who": who, "operator": operator, "n": n_said,
                                    "source": str(obj.get("promptSource") or "")})
                    live_tool = None
                    continue
            pending.append({"at": at, "pos": pos, "is_tool": False, "line": _line(obj)})
            continue

        if kind == "assistant":
            usage.add(obj)
            texts, tools = [], []
            for b in _blocks(obj):
                if not isinstance(b, dict):
                    continue
                bt = str(b.get("type") or "")
                if bt == "text" and str(b.get("text") or "").strip():
                    texts.append(str(b["text"]).strip())
                elif bt == "tool_use":
                    tools.append(b)
            for b in tools:
                out["tools"] += 1
                # The newest tool call is ONE live line replaced in place, not an appended row.
                live_tool = {"pos": pos, "name": str(b.get("name") or "?"),
                             "detail": runfeed.truncate(runfeed._tool_detail(b), 160), "at": at}
            if texts:
                flush(pos)
                n_beat += 1
                body = "\n\n".join(texts)
                out["now_line"] = runfeed.truncate(body.split("\n")[0], 200)
                entries.append({"kind": "beat", "key": f"beat-{pos}", "pos": pos, "at": at,
                                "text": body, "n": n_beat})
                live_tool = None       # the call folded into the count when this beat landed
                continue
            if tools:
                for b in tools:
                    pending.append({"at": at, "pos": pos, "is_tool": True, "line": _line(obj)})
                continue

        pending.append({"at": at, "pos": pos, "is_tool": False, "line": _line(obj)})

    # ================================================================== THE CURSOR IS NOT `pos`
    #
    # **A TORN TAIL MUST NOT ADVANCE THE CURSOR, AND SHIPPING IT SO WAS A REAL DEFECT.** Watched
    # failing in a real browser on 2026-08-28: a half-written line appended, the reader reported
    # `position` as the total line count including it, the client moved its cursor to that number,
    # and when the line completed a poll later its entry was at a position the client had already
    # passed. The row was never sent. The vitals moved -- `1 reply` became `2 replies` and the cost
    # went $0.03 to $0.06, because the counters are computed over the whole file -- while the reply
    # itself never appeared. **The most convincing possible failure: a screen that says something
    # arrived and does not show it.**
    #
    # So the cursor is the line of the last WHOLE record. A torn tail leaves it exactly where it
    # was and the completed record is above it next poll. `runfeed.read_run` has the same shape and
    # its window is small because a fleet stream's lines are small; here the longest line in the
    # corpus is 1.3MB, which makes the window wide enough to hit on an ordinary day.
    out["position"] = last_ok
    out["lines"] = pos                     # what is on disk, for the file-shrank check and display
    out["said"], out["beats"] = n_said, n_beat
    out["live_tool"] = live_tool
    out["tokens"] = dict(usage.tokens) if usage.messages else None
    out["messages"], out["usage_records"] = usage.messages, usage.records
    out["models"] = sorted(usage.models)
    if pending:
        out["open_fold"] = {
            "n": len(pending), "tools": sum(1 for c in pending if c["is_tool"]),
            "from_pos": pending[0]["pos"], "to_pos": pending[-1]["pos"],
            "seconds": runfeed._between(pending[0]["at"], pending[-1]["at"]),
        }
    out["entries"] = [e for e in entries if e["pos"] > since]
    return out


def fold_lines(path, from_pos: int, to_pos: int, cap: int = 200) -> dict:
    """The churn inside one fold, read on demand when the operator presses `show`.

    NOT shipped with the poll, for the reason `runfeed.fold_lines` measured: one fold here spans
    158 records on a small session and far more on a working one, and sending them twenty times a
    minute to render a line that says `12 tool calls` would be this page's most expensive read on
    its most frequent path.

    `cap` is a bound and the response SAYS when it bit.
    """
    p = Path(path)
    if not p.exists():
        return {"lines": [], "dropped": 0, "missing": True}
    lines: list = []
    dropped = 0
    pos = 0
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"lines": [], "dropped": 0, "missing": False,
                "error": f"{exc.__class__.__name__}: {exc}"}
    for row in raw.splitlines():
        pos += 1
        if pos < from_pos or pos > to_pos:
            continue
        row = row.strip()
        if not row:
            continue
        try:
            obj = json.loads(row)
        except ValueError:
            continue
        text = _line(obj)
        if not text:
            continue
        if len(lines) >= cap:
            dropped += 1
            continue
        lines.append({"pos": pos, "text": text})
    return {"lines": lines, "dropped": dropped, "missing": False}


def derived_cost(read: dict) -> tuple:
    """`(usd, why)`. Never 0.0 as a stand-in: a cost of zero and a cost nobody could compute are
    different facts and the vitals row says which one it has.

    The token bag is already deduplicated by `message.id` (trap 2) and is handed to the price card
    unchanged, because this format spells the four component keys exactly as the card does.
    """
    if price_card is None:
        return None, "budget.price_card did not import; no price table is loaded"
    tokens = read.get("tokens")
    if not tokens:
        return None, "no assistant message in this session carries a usage block yet"
    try:
        return float(price_card.derive_usd(tokens)), ""
    except Exception as exc:                                            # noqa: BLE001
        return None, f"the price card refused this token basis ({exc.__class__.__name__})"


# ------------------------------------------------------------------ liveness, from the FILE
#
# TRAP 4. `ended_at` is set at `SessionEnd` and 414 of 1,735 sessions have one, so it cannot say
# whether a terminal is open. The file's mtime can, and the state word never replaces the fact:
# both are rendered, always.

LIVE_UNDER = 120          # 2m. Claude Code writes a record per content block, so a working
#                           session touches its file every few seconds.
IDLE_UNDER = 3600         # 1h


def state_of(age) -> dict:
    """The state word and its colour, from the age of the last write. Three states, no red.

    RED IS DAMAGE AND NOTHING ELSE, shell wide. **A transcript that stopped growing is not damage.**
    A terminal he closed, a session he finished and a laptop that slept all produce exactly the
    same file, and spending the damage colour on any of them would leave no colour for the one
    thing here that IS damage: a pointer whose file the disk no longer has.

    IDLE is amber because amber in this shell means waiting on a human, and a Claude Code session
    that has written nothing for four minutes is overwhelmingly waiting for Andrew to type. It is
    not a verdict, which is why the fact is always beside the word.
    """
    if age is None:
        return {"word": "UNKNOWN", "colour": "rest",
                "detail": "the file has no modification time this console could read"}
    if age < LIVE_UNDER:
        return {"word": "LIVE", "colour": "run"}
    if age < IDLE_UNDER:
        return {"word": "IDLE", "colour": "wait"}
    return {"word": "RESTING", "colour": "rest"}


def written(age) -> dict:
    """The fact that sits beside the state word. Never a verdict, and never omitted."""
    if age is None:
        return {"colour": "ink-3", "text": "no write time"}
    return {"colour": "ink" if age < LIVE_UNDER else "ink-2",
            "text": f"file last written {runfeed._ago(age)} ago"}


# ------------------------------------------------------------------ the picker
#
# TRAP 4's corollary: DISK FIRST, STORE SECOND. The index has a floor of live sessions because the
# hook writes at `SessionEnd` (`web/sessions.py` trap 1), so a picker built from `brain.session`
# would omit precisely the sessions this page exists to show. The disk decides what exists; the
# store supplies the goal, the role and the parentage where it has them.

_POINTERS = """
    SELECT t.pointer, t.session_id, s.role, s.workdir, s.stated_goal, s.started_at, s.ended_at,
           s.parent_session_id
      FROM brain.transcript t
      LEFT JOIN brain.session s ON s.id = t.session_id
"""


def _indexed() -> dict:
    """`absolute path -> the store's facts about it`, or {} with the reason if the store is out.

    One query. 1,733 rows on this host, which is small enough to index in memory and is the only
    way to answer "does the store know this file" for a disk walk without a query per file.
    """
    try:
        with store.read() as s:
            rows = s.query(_POINTERS, ())
    except Exception:                                                   # noqa: BLE001
        return {}
    return {str(r["pointer"]): r for r in rows}


def _key_from_path(path: Path, root: Path) -> tuple:
    """`(session_key, kind)` from the path shape, for a file the index has never seen.

    The corpus taxonomy is `ingest/ingest/transcript.py`'s and is quoted rather than re-derived:

        <root>/<uuid>.jsonl                                     main session
        <root>/<uuid>/subagents/agent-<id>.jsonl                subagent
        <root>/<uuid>/subagents/workflows/<wf>/agent-<id>.jsonl workflow subagent
        <root>/<uuid>/subagents/workflows/<wf>/journal.jsonl    workflow journal, not a session

    THIS IS THE ONE PLACE THE CONSOLE RE-DERIVES SOMETHING `ingest` OWNS, and the narrowness is
    deliberate. `web/sessions.py` refused to re-implement the disk walk because that page's whole
    job is to answer a coverage question honestly and a second answer would be the defect. This is
    not a coverage claim: it is a filename parse, used only to name a file the index has not
    reached yet, and every row built from it is labelled `not indexed yet` on the screen.
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        return "", ""
    parts = rel.parts
    if len(parts) == 2:
        return path.stem, "main"
    if len(parts) >= 4 and parts[1] == "subagents" and path.name.startswith("agent-"):
        return f"{parts[0]}:{path.stem[len('agent-'):]}", "subagent"
    return "", ""                     # a workflow journal is not a session and is not offered


def _head_goal(path: Path) -> str:
    """The first thing the human typed, read from the head of a file the index has not reached.

    Bounded at `HEAD_BYTES` and honest when the bound bites: a session whose first human prompt is
    past 256KB gets no goal rather than a wrong one. For a main session the first prompt is within
    the first few records, so this is a cheap read for the live sessions that need it and is never
    paid at all for the 1,733 the store already describes.
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            raw = fh.read(HEAD_BYTES)
    except OSError:
        return ""
    first = True
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "user":
            continue
        who, _ = _who(obj, is_first_user=first)
        first = False
        if who:
            return runfeed.truncate(_text_of(obj), 400)
    return ""


#: The picker's row cap. Stated on the screen when it bites, never silently applied.
PICKER_CAP = 60


def index(hours: int = 24, kind: str = "main", q: str = "", cap: int = PICKER_CAP) -> dict:
    """The sessions worth opening, newest write first. A list that is useful at 7am.

    THE DESIGN PROBLEM, WHICH IS NOT THE QUERY. There are 1,735 sessions and 854 transcript files
    on this host and 799 of the sessions are subagents. A flat list of all of them is a directory
    listing, not a picker, and at 7am the operator is not browsing: he is resuming. So the default
    is narrow on purpose and every narrowing is stated on the screen with the control to widen it:

      * ordered by LAST WRITE, not by start. What moved most recently is what he was doing.
      * `main` sessions only by default. A subagent is a thing a session did, not a thing he did;
        the count of a main session's subagents rides its row, and `kind=all` opens them.
      * a 24 hour window by default, which is "today and last night".
      * grouped by workdir in the template, because a repo is how he thinks about his day.

    Every row carries where it came from -- `indexed` true or false -- so the ones the store has
    never seen are legible as the live floor rather than as an anomaly.
    """
    root = projects_root().resolve()
    out = {"root": str(root), "rows": [], "refused": "", "hours": hours, "kind": kind, "q": q,
           "walked": 0, "in_window": 0, "capped": 0, "head_reads": 0, "store_known": 0}
    if not root.is_dir():
        out["refused"] = (f"{root} is not a directory on this host, so there is nothing to walk. "
                          f"No list is shown rather than an empty one that reads as `no sessions`.")
        return out

    ptr = _indexed()
    out["store_known"] = len(ptr)
    now = time.time()
    floor = now - hours * 3600
    found = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".jsonl"):
                continue
            fp = Path(dirpath) / name
            out["walked"] += 1
            try:
                st = fp.stat()
            except OSError:
                continue
            if st.st_mtime < floor:
                continue
            found.append((st.st_mtime, st.st_size, fp))
    found.sort(reverse=True)
    out["in_window"] = len(found)

    children: dict = {}
    rows = []
    for mtime, size, fp in found:
        known = ptr.get(str(fp))
        if known:
            key = known["session_id"] or ""
            role = str(known["role"] or "")
            goal = known["stated_goal"] or ""
            workdir = known["workdir"] or ""
            parent = known["parent_session_id"] or ""
        else:
            key, role = _key_from_path(fp, root)
            if not key:
                continue
            parent = key.split(":")[0] if role == "subagent" else ""
            goal, workdir = "", ""
        if not key:
            continue
        if role == "subagent":
            children[parent] = children.get(parent, 0) + 1
        rows.append({
            "key": key, "role": role or "?", "path": str(fp), "bytes": size,
            "mtime": mtime, "age": int(max(0, now - mtime)),
            "goal": goal, "workdir": workdir, "parent": parent,
            "indexed": bool(known), "workdir_guess": False,
            "started_at": known["started_at"] if known else None,
            "ended_at": known["ended_at"] if known else None,
        })

    keep = []
    for r in rows:
        if kind == "main" and r["role"] != "main":
            continue
        r["subagents"] = children.get(r["key"], 0)
        if not r["goal"] and not r["indexed"] and out["head_reads"] < 40:
            # Only for the live floor, and only up to a stated budget. See `_head_goal`.
            r["goal"] = _head_goal(Path(r["path"]))
            out["head_reads"] += 1
        if not r["workdir"]:
            # The store's workdir is the `cwd` recorded inside the file. This one is decoded from
            # the directory name and the transform is not injective, so it is labelled a guess on
            # the screen rather than presented as the same fact.
            r["workdir"] = _workdir_of(Path(r["path"]), root)
            r["workdir_guess"] = True
        if q:
            hay = f"{r['goal']} {r['workdir']} {r['key']}".lower()
            if q.lower() not in hay:
                continue
        r["state"] = state_of(r["age"])
        r["written"] = written(r["age"])
        r["short"] = runfeed.truncate(r["goal"], 160) or "(no prompt found in this file)"
        keep.append(r)

    out["capped"] = max(0, len(keep) - cap)
    out["rows"] = keep[:cap]
    return out


def _workdir_of(path: Path, root: Path) -> str:
    """The project directory this file sits under, decoded, and it IS A GUESS. Labelled as one.

    `ingest/ingest/transcript.py:decode_project_dir` states why: Claude Code replaces every
    non-alphanumeric run with `-`, so a path containing a literal `-`, `.`, `_` or ` ` encodes
    identically to one containing `/` and the transform is not injective. The authoritative
    workdir is the `cwd` inside the file, which is what the store carries; this is the fallback
    for a file the store has never seen, and the template renders it as the guess it is.
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        return ""
    return "/" + rel.parts[0].lstrip("-").replace("-", "/") if rel.parts else ""


# ------------------------------------------------------------------ one session, for the page


def resolve(key: str) -> dict:
    """`session_key` -> the file it names, checked against the projects root.

    THE PATH IS NEVER TAKEN FROM THE CLIENT. `runfeed_fold` learned the general form of this: a
    reader endpoint that opens any path a query string names is a file-read primitive, whatever it
    was written for. Here the client sends a KEY, and the path is either the store's own pointer
    for that key or a name built from the taxonomy -- and either way it is resolved and required
    to live under the projects root before it is opened.
    """
    root = projects_root().resolve()
    out = {"key": key, "path": "", "row": None, "error": ""}
    if not key or "/" in key or "\\" in key or ".." in key:
        out["error"] = f"{key!r} is not a session key"
        return out
    try:
        with store.read() as s:
            row = s.one(
                "SELECT s.id, s.role, s.workdir, s.stated_goal, s.started_at, s.ended_at, "
                "       s.parent_session_id, s.harness, s.agent, t.pointer, t.pointer_host, "
                "       t.bytes AS indexed_bytes, t.indexed_at "
                "  FROM brain.session s "
                "  LEFT JOIN brain.transcript t ON t.session_id = s.id "
                " WHERE s.id = %s", (key,))
    except Exception:                                                   # noqa: BLE001
        row = None
    candidate = None
    if row and row.get("pointer"):
        candidate = Path(str(row["pointer"]))
    else:
        # Not indexed yet, which for a session open right now is the NORMAL case, not an error.
        if ":" in key:
            parent, agent = key.split(":", 1)
            candidate = root / _project_dir_for(parent, root) / parent / "subagents" / f"agent-{agent}.jsonl"
        else:
            d = _project_dir_for(key, root)
            candidate = (root / d / f"{key}.jsonl") if d else None
    if candidate is None:
        out["error"] = (f"no transcript file on this host is named by {key!r}, and the store has "
                        f"no pointer for it either")
        return out
    try:
        target = candidate.resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        out["error"] = f"{candidate} is not inside {root}"
        return out
    out["path"], out["row"] = str(target), row
    return out


def _project_dir_for(session_uuid: str, root: Path) -> str:
    """Which encoded project directory holds this session. Found by looking, not by guessing.

    The project directory is an encoding of the cwd and cannot be derived from the session id, so
    this scans the root's immediate children for the file. `os.walk` over the whole corpus costs
    12.9ms measured, and this is a cheaper subset of that: one `exists()` per project directory.
    """
    for child in sorted(root.iterdir()) if root.is_dir() else []:
        if not child.is_dir():
            continue
        if (child / f"{session_uuid}.jsonl").exists() or (child / session_uuid).is_dir():
            return child.name
    return ""


def feed(key: str, since: int = 0) -> dict:
    """Everything one live-session page needs, degraded states included, verbs called: zero."""
    r = resolve(key)
    now = time.time()
    out = {"key": key, "path": r["path"], "row": r["row"], "vitals": None, "entries": [],
           "position": since, "degraded": None, "now": int(now), "readable": True,
           "live_tool": None, "open_fold": None, "findings": []}

    if r["error"] or not r["path"]:
        out["readable"] = False
        out["degraded"] = {"level": "wait", "word": "not found",
                           "text": r["error"] or f"no file resolved for {key!r}",
                           "path": "", "raw": ""}
        return out

    read = read_session(r["path"], since=since)
    out["entries"] = read["entries"]
    out["position"] = read["position"]
    out["live_tool"] = read["live_tool"]
    out["open_fold"] = read["open_fold"]
    out["read"] = read

    # ---------------------------------------------------------------- the degraded states
    #
    # MISSING is the one red on this page and it is the one damage case: the store recorded a
    # pointer and the disk does not have the file. Everything else here is amber, because a
    # transcript that cannot be read right now is a reading problem, not a broken session.
    if not read["exists"]:
        out["readable"] = False
        out["degraded"] = {
            "level": "dmg", "word": "MISSING",
            "text": ("this session is recorded in the store and its transcript is not on disk"
                     if r["row"] else "no transcript file at this path"),
            "path": r["path"], "raw": ""}
    elif not read["readable"]:
        out["readable"] = False
        out["degraded"] = {"level": "wait", "word": "unreadable",
                           "text": f"showing nothing rather than guessing · {read['error']}",
                           "path": r["path"], "raw": ""}
    elif read["events"] == 0 and read["torn"]:
        out["readable"] = False
        out["degraded"] = {
            "level": "wait", "word": "unreadable",
            "text": "every line of this file is torn · showing nothing rather than guessing",
            "path": r["path"],
            "raw": f"{read['torn']} torn line(s), 0 whole records"}

    age = int(max(0, now - read["mtime"])) if read["mtime"] else None
    cost, why = derived_cost(read)
    row = r["row"] or {}
    out["vitals"] = {
        "state": state_of(age),
        "written": written(age),
        "said": read["said"], "beats": read["beats"], "tools": read["tools"],
        "cost": cost, "cost_why": why,
        "torn": read["torn"], "position": read["position"], "lines": read["lines"],
        "bytes": read["bytes"],
        "models": read["models"], "messages": read["messages"],
        "usage_records": read["usage_records"],
        "now_line": read["now_line"],
        "role": str(row.get("role") or ("subagent" if ":" in key else "main")),
        "workdir": str(row.get("workdir") or _workdir_of(Path(r["path"]),
                                                         projects_root().resolve())),
        "goal": runfeed.truncate(str(row.get("stated_goal") or ""), 400),
        "indexed": bool(row.get("pointer")),
        "parent": str(row.get("parent_session_id") or ""),
        "elapsed": runfeed.elapsed_words(runfeed._age(row.get("started_at"), now))
                   if row.get("started_at") else "",
    }

    # TRAP 2, ON THE SCREEN RATHER THAN ONLY IN THE CODE. The count that makes the cost honest is
    # rendered, because a reader who knows 101 records became 37 messages can tell this page's
    # number apart from a number that summed the records.
    if read["messages"] and read["usage_records"] > read["messages"]:
        out["findings"].append({
            "level": "rest",
            "text": (f"{read['usage_records']} assistant records carry a usage block and they are "
                     f"{read['messages']} distinct API messages"),
            "detail": ("Claude Code writes one record per content block and repeats the completed "
                       "message's usage on every one of them. The cost above counts each message "
                       "once, keyed on message.id. Summing the records instead overstates spend "
                       "2.22x, measured over all 1,489 transcripts on this host."),
        })
    return out
