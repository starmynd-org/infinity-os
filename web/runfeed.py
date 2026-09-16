"""V4's server-side stream reader (V10-C SPEC sections 2.2-2.4): beats, folds, vitals, seams.

SHELL-0 stub (task 0156): created empty so the module path exists and every V4 diff is an
append into a file V4 alone owns. No behaviour lands here until V4 does.

Trap, from task 0151: ``stream_event_line()``, which C:236-238 tells V4 to "reuse verbatim",
does not exist anywhere in this repo. It is a port from the admiral fleet's codebase, and
porting it is real V4 work. Do not go looking for it locally.

================================================================================ V4, task 0166

What this module is: **a reader, and only a reader.** It opens run stream files and turns them
into the shapes the runfeed renders. It calls no verb, writes no row, and touches no file. Every
state change take-control makes goes through `swarm_engine.steering` and `msg`, which is the
narrow waist as V00 states it: a terminal UI that writes state directly is a violation.

It is not a terminal emulator. **No PTY text ever renders**, because none exists: the runner
writes structured stream-json, one event per line (`engine/bin/swarm-run`, the STREAM_FORMATTER),
and this reads that. The rendered unit is the BEAT -- one assistant text block, the agent saying
what it is doing or found -- and everything between two beats folds to a count.

THREE TRAPS MEASURED ON THE LIVE STREAMS OF 2026-08-18, all of which shape the code below:

1. **`system/init` carries `permissionMode` and `model` verbatim.** A reader that rendered raw
   event lines would put `permission_mode` on the operator's screen, which the brief forbids
   absolutely and in every state. So `system` events are folded, never rendered, and
   `_SUPPRESSED_KEYS` names the keys that may not leave this module even inside a fold line.
2. **The tail line is routinely torn.** The formatter appends line-buffered while this reads, so
   the last line of a live file is often half a JSON object. That is expected and is rendered as
   NOTHING -- it will be whole next poll. A torn tail is never an error, and a file of nothing
   but torn lines is a different, amber, state.
3. **Cost exists only on the terminal `result` event.** `total_cost_usd` is not emitted mid-run.
   `budget/price_card.py` already derives it from the token basis and was reproduced against the
   engine's own billing to $0.00000000 on 107 of 108 priced sessions, so this module calls that
   and derives no second price. Measured 3.9ms on a 936KB live stream, which is why it can run
   on a three-second poll.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import store
from swarm_engine import steering

# The mid-run price. One implementation of cost in this runtime, not two.
try:
    from budget import price_card
except Exception:                                                       # noqa: BLE001
    price_card = None            # the vitals say `cost unavailable`, and say why, rather than 0

# ------------------------------------------------------------------ the runs directory, config
#
# NEVER A HARDCODED PATH, and this is the one V10 left explicitly to V4 (DS section 10). The V4
# and V10 briefs both say `~/.swarm/runs/`; that is the ADMIRAL fleet's home and it is not this
# host's. This runtime's runner resolves `STATE="${ENGINE_HOME:-$HOME/.brain-runtime}"` and writes
# `$STATE/runs` (`engine/bin/swarm-run:82,87`), and `swarm_engine/cli.py:117` reads the same
# variable. So the resolution order here is that same variable, one config key above it for a host
# that puts runs somewhere else, and no literal anywhere in the rendering path.

RUNS_DIR_KEY = "runs_dir"


def runs_dir() -> Path:
    """Where run streams live on THIS host. Config, in three steps, none of them a guess.

    1. `CONSOLE_RUNS_DIR`, for a console pointed at another host's mounted runs.
    2. `runs_dir` in the engine config file, for a host that moved them.
    3. `$ENGINE_HOME/runs`, which is what the runner actually writes.
    """
    env = os.environ.get("CONSOLE_RUNS_DIR")
    if env:
        return Path(env).expanduser()
    try:
        from swarm_engine import config as engine_config
        cfg = engine_config.config()
        if cfg.get(RUNS_DIR_KEY):
            return Path(str(cfg[RUNS_DIR_KEY])).expanduser()
    except Exception:                                                   # noqa: BLE001
        pass
    home = os.environ.get("ENGINE_HOME") or os.path.expanduser("~/.brain-runtime")
    return Path(home).expanduser() / "runs"


def stream_path(task_id: str, attempt: int) -> Path:
    """`<runs>/<task>-attempt<N>.stream.jsonl`, the name the runner writes (swarm-run:1183)."""
    return runs_dir() / f"{task_id}-attempt{attempt}.stream.jsonl"


# ------------------------------------------------------------------ the churn line, PORTED
#
# `stream_event_line()` and its one helper `truncate()` are lifted VERBATIM from
# `/mnt/c/Users/you/repos/internal/swarm-admiral/bin/swarm-board`, lines 1039-1113 and 246-250,
# per the port measurement in `outputs/2026-08-18-V10-stream-event-line-port/PORT-COST.md` (task
# 0160): 80 lines, stdlib only, zero globals, zero config, and the input shape verified identical
# to this runtime's own `.stream.jsonl`. It is the display contract for what a churn line says,
# including the 300/400 truncation limits, so it is not adapted and not rewritten.
#
# THE ONE THING WRAPPED AROUND IT, and it is not an adaptation of the function: `churn_line()`
# below refuses to return a line for a `system` event. The ported function is honest about every
# field it is given, and `system/init` is given `permissionMode`. The suppression is the caller's
# job because it is a decision about THIS surface, not about that function.


def truncate(text, limit):
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def stream_event_line(obj):
    """One readable line from one raw stream event.

    Only used when the human-readable log is missing, so it aims at legible rather
    than complete. Every field is optional because the shape of an event is the
    engine's business, not the board's.
    """
    if not isinstance(obj, dict):
        return truncate(str(obj), 300)
    label = str(obj.get("type") or "event")
    subtype = str(obj.get("subtype") or "")
    if subtype:
        label += "/" + subtype
    parts = []

    inner = obj.get("event")
    if isinstance(inner, dict):
        if inner.get("type"):
            label += "/" + str(inner.get("type"))
        delta = inner.get("delta")
        if isinstance(delta, dict):
            for key in ("text", "thinking", "partial_json"):
                value = delta.get(key)
                if isinstance(value, str) and value:
                    parts.append(value)

    message = obj.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if content is None:
        content = obj.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            btype = str(block.get("type") or "")
            if btype == "text":
                parts.append(str(block.get("text") or ""))
            elif btype == "thinking":
                parts.append(str(block.get("thinking") or ""))
            elif btype == "tool_use":
                detail = ""
                data = block.get("input")
                if isinstance(data, dict):
                    for key in ("command", "file_path", "path", "pattern",
                                "prompt", "description", "url", "query"):
                        if data.get(key):
                            detail = str(data[key])
                            break
                    if not detail:
                        try:
                            detail = json.dumps(data, default=str)
                        except (TypeError, ValueError):
                            detail = str(data)
                parts.append("tool %s %s" % (block.get("name") or "?", detail))
            elif btype == "tool_result":
                payload = block.get("content")
                if isinstance(payload, list):
                    payload = " ".join(
                        str(p.get("text", "")) if isinstance(p, dict) else str(p)
                        for p in payload)
                parts.append("result %s" % (payload if isinstance(payload, str)
                                            else str(payload or "")))
            elif btype:
                parts.append(btype)

    for key in ("result", "text", "error"):
        value = obj.get(key)
        if isinstance(value, str) and value:
            parts.append(value)

    body = truncate(" ".join(p for p in parts if p), 400)
    return (label + "  " + body) if body else label


#: Keys that must not reach the operator's screen through any line this module returns. The rule
#: is the brief's, absolute and in every state: `permission_mode` is what makes an agent's
#: self-report trustworthy, and a picker or a feed that shows it invites the one change that would
#: quietly make every self-report worthless. Named as data so the check is greppable.
_SUPPRESSED_KEYS = ("permissionMode", "permission_mode", "bypassPermissions")


def churn_line(obj) -> str:
    """The ported line, with the one event type that carries a forbidden key refused outright.

    A `system` event is engine bookkeeping -- `init`, `status`, `thinking_tokens`, the hook
    pair -- and folds anyway (C section 2.2). Refusing it HERE rather than at the fold means an
    unfolded churn list cannot leak it either.
    """
    if isinstance(obj, dict) and str(obj.get("type") or "") == "system":
        return ""
    line = stream_event_line(obj)
    return "" if any(k in line for k in _SUPPRESSED_KEYS) else line


# ------------------------------------------------------------------ reading one stream

#: What a fold counts as a tool call. `tool_use` blocks on assistant events; `tool_result`
#: blocks come back on `user` events and are the same call arriving, so they are NOT counted
#: twice -- an accumulating number that double-counts is a number that reads like progress.
def _blocks(obj) -> list:
    msg = obj.get("message")
    content = msg.get("content") if isinstance(msg, dict) else obj.get("content")
    return content if isinstance(content, list) else []


class Torn(Exception):
    """The tail line was half-written. Expected on a live file; never an error."""


def read_run(path, since: int = 0, limit: int = 400) -> dict:
    """One run file -> beats, folds, the live tool line, and the counters the vitals need.

    `since` is the STREAM POSITION the client already holds: the line number of the jsonl, 1
    based. Only entries produced at a position above it come back, which is what makes the feed
    region append-only (C section 2.5.1). The counters are always computed over the WHOLE file,
    because a count that only counted the new lines would tick down to nothing on a quiet run.

    Every dict this returns is JSON-safe and carries `pos`, so the client can key on it.
    """
    p = Path(path)
    out = {
        "path": str(p), "exists": p.exists(), "readable": True, "error": "",
        "entries": [], "position": 0, "beats": 0, "tools": 0, "events": 0, "torn": 0,
        "live_tool": None, "open_fold": None, "last_event_at": None, "first_event_at": None,
        "session_id": "", "result": None, "now_line": "",
    }
    if not p.exists():
        out["readable"] = False
        return out

    entries: list = []
    pos = 0
    pending_churn: list = []
    live_tool = None
    last_at = ""

    def flush_churn(at_pos):
        """A run of non-beat events becomes ONE fold row: `-- N tool calls . 3m -- show`.

        CLOSED FOLDS ONLY GO IN `entries`. A fold is closed by the beat that follows it, and a
        closed fold's `pos` never changes again -- which is the whole basis of an append-only
        feed keyed by stream position. The churn AFTER the last beat is still growing, so it is
        not an entry at all: it rides `live` below and is replaced in place, exactly as the
        newest tool call is. Appending it would re-send the same fold with a higher `pos` on
        every poll, and the operator would watch one fold row breed.
        """
        if not pending_churn:
            return
        first, last = pending_churn[0], pending_churn[-1]
        entries.append({
            "kind": "fold", "pos": at_pos, "n": len(pending_churn),
            "tools": sum(1 for c in pending_churn if c["is_tool"]),
            "from": first["at"], "to": last["at"],
            "from_pos": first["pos"], "to_pos": last["pos"],
            "seconds": _between(first["at"], last["at"]),
        })
        pending_churn.clear()

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
            # THE TORN TAIL. Counted, never rendered, never an error. It will be whole next poll.
            out["torn"] += 1
            continue
        if not isinstance(obj, dict):
            continue
        out["events"] += 1
        out["session_id"] = obj.get("session_id") or out["session_id"]
        at = obj.get("timestamp") or ""
        if at:
            out["last_event_at"] = at
            out["first_event_at"] = out["first_event_at"] or at
            last_at = at
        else:
            # MEASURED: only `assistant`, `user` and `result` events carry `timestamp`; the 785
            # `stream_event` deltas of a live run carry none. A fold whose two ends are both
            # blank reports a duration of zero, which reads as "that took no time" rather than
            # "nobody stamped it". Inheriting the last stamp seen is the honest reading: the
            # event happened at or after it.
            at = last_at
        kind = str(obj.get("type") or "")

        if kind == "result":
            out["result"] = {
                "subtype": obj.get("subtype") or "", "is_error": bool(obj.get("is_error")),
                "cost": obj.get("total_cost_usd"), "turns": obj.get("num_turns"),
                "duration_ms": obj.get("duration_ms") or obj.get("duration_api_ms"),
                "at": at,
            }
            continue

        if kind == "assistant":
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
                             "detail": truncate(_tool_detail(b), 160), "at": at}
            if texts:
                flush_churn(pos)
                out["beats"] += 1
                body = "\n\n".join(texts)
                out["now_line"] = truncate(body.split("\n")[0], 200)
                entries.append({"kind": "beat", "pos": pos, "at": at, "text": body,
                                "n": out["beats"]})
                live_tool = None          # the call folded into the count when this beat landed
                continue
            if tools:
                for b in tools:
                    pending_churn.append({"at": at, "pos": pos, "is_tool": True,
                                          "line": churn_line(obj)})
                continue

        # Everything else is churn: thinking, tool results, system bookkeeping, rate limits.
        pending_churn.append({"at": at, "pos": pos, "is_tool": False, "line": churn_line(obj)})

    out["position"] = pos
    out["live_tool"] = live_tool
    # The open fold: everything since the last beat, still growing. In place, never appended.
    if pending_churn:
        out["open_fold"] = {
            "n": len(pending_churn),
            "tools": sum(1 for c in pending_churn if c["is_tool"]),
            "from_pos": pending_churn[0]["pos"], "to_pos": pending_churn[-1]["pos"],
            "seconds": _between(pending_churn[0]["at"], pending_churn[-1]["at"]),
        }
    out["entries"] = [e for e in entries if e["pos"] > since]
    return out


def _between(a, b) -> int:
    """Seconds between two ISO stamps, 0 when either is absent. Never negative."""
    from datetime import datetime
    try:
        ta = datetime.fromisoformat(str(a).replace("Z", "+00:00")).timestamp()
        tb = datetime.fromisoformat(str(b).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0
    return max(0, int(tb - ta))


def fold_lines(path, from_pos: int, to_pos: int, cap: int = 200) -> dict:
    """The churn lines inside one fold, read on demand when the operator presses `show`.

    NOT shipped with the feed. A single fold on a live run held 1,199 events when this was
    measured, and putting those on a three-second poll would send a megabyte of engine
    bookkeeping twenty times a minute to render a line that says `12 tool calls`.

    `cap` is a bound and the response SAYS when it bit. A silent truncation reads as "that was
    all of it", which is the shape this system spends its refusals on.
    """
    p = Path(path)
    lines: list = []
    dropped = 0
    if not p.exists():
        return {"lines": [], "dropped": 0, "missing": True}
    pos = 0
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        pos += 1
        if pos < from_pos or pos > to_pos:
            continue
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        line = churn_line(obj)
        if not line:
            continue
        if len(lines) >= cap:
            dropped += 1
            continue
        lines.append({"pos": pos, "text": line})
    return {"lines": lines, "dropped": dropped, "missing": False}


def _tool_detail(block) -> str:
    data = block.get("input")
    if not isinstance(data, dict):
        return ""
    for key in ("command", "file_path", "path", "pattern", "description", "url", "query"):
        if data.get(key):
            return str(data[key])
    return ""


def derived_cost(path, session_id: str = "") -> tuple:
    """`(usd, why)`. Mid-run dollars from the price card, or None and the reason it is absent.

    Never 0.0 as a stand-in. A cost of zero and a cost nobody could compute are different facts
    and the vitals row says which one it has.
    """
    if price_card is None:
        return None, "budget.price_card did not import; no price table is loaded"
    try:
        per_session = price_card.derive_stream(str(path))
    except Exception as exc:                                            # noqa: BLE001
        return None, f"price card could not read the stream ({exc.__class__.__name__})"
    if not per_session:
        return None, "no priced tokens in this stream yet"
    if session_id and session_id in per_session:
        return float(per_session[session_id]), ""
    return float(sum(per_session.values())), ""


# ------------------------------------------------------------------ the vitals row
#
# "An agent does not know how far through it is, so nothing here implies completion." Facts that
# only accumulate, plus recency, and recency is the health signal. No bar, no ring, no ETA, no
# percent -- MUST-NOT-BUILD #4, whose sole exception is the stack's pips and this is not that.

#: Recency thresholds, C section 2.3. Amber is "waits on attention"; the question mark on
#: `stalled?` is deliberate, because staleness is evidence and not a verdict.
QUIET_AFTER = 120         # 2m
STALLED_AFTER = 600       # 10m
FRESH_UNDER = 30


def recency(seconds) -> dict:
    if seconds is None:
        return {"word": "", "colour": "ink-3", "text": "no event yet"}
    s = int(seconds)
    if s >= STALLED_AFTER:
        return {"word": "stalled?", "colour": "wait", "text": f"last event {_ago(s)} ago"}
    if s >= QUIET_AFTER:
        return {"word": "quiet", "colour": "wait", "text": f"last event {_ago(s)} ago"}
    return {"word": "", "colour": "ink-2" if s >= FRESH_UNDER else "ink",
            "text": f"last event {_ago(s)} ago"}


def _ago(s) -> str:
    s = int(s or 0)
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60:02d}"


def elapsed_words(seconds) -> str:
    return _ago(seconds)


# ------------------------------------------------------------------ following the AGENT
#
# The runfeed follows the agent, not the task -- the `swarm-fleet watch` behaviour -- so what it
# needs from the store is that agent's runs in order, and the two clocks (the run row and the
# heartbeat) that can disagree about whether it is alive.

_RUNS_FOR_AGENT = """
    SELECT r.id AS run_id, r.work_item_id, r.attempt, r.agent, r.started_at, r.ended_at,
           r.outcome, r.exit_code, r.session_id, r.stream_pointer, r.stream_pointer_host,
           r.stream_bytes, w.title, w.state, w.lane, w.claimed_by
      FROM brain.run r
      LEFT JOIN brain.work_item w ON w.id = r.work_item_id
     WHERE r.agent = %s
     ORDER BY r.started_at DESC
     LIMIT %s
"""


def agent_runs(name: str, limit: int = 6) -> list:
    with store.read() as s:
        return s.query(_RUNS_FOR_AGENT, (name, limit))


def agent_row(name: str) -> dict | None:
    with store.read() as s:
        return s.one(
            "SELECT name, role, status, work_item_id, pid, host, updated, stopped_at "
            "  FROM brain.agent WHERE name = %s", (name,))


def _state_of(agent, run, last_event_age) -> dict:
    """The state word and its colour. Five semantics, and the word is always present.

    DEAD is red and it is the one damage case: the runner exited without filing a report, so the
    run row has an `ended_at` and the work item is still `active`. A quiet agent is not dead --
    that is what the amber recency words are for -- and calling it dead would spend the one
    colour reserved for things that are actually broken.
    """
    if agent and agent.get("stopped_at"):
        return {"word": "STOPPED", "colour": "rest"}
    if run and run.get("ended_at") and str(run.get("state") or "") == "active":
        return {"word": "DEAD", "colour": "dmg",
                "detail": "the runner exited and filed no report for this attempt"}
    if not run or run.get("ended_at"):
        return {"word": "RESTING", "colour": "rest"}
    if last_event_age is not None and last_event_age >= STALLED_AFTER:
        return {"word": "WORKING", "colour": "run"}
    return {"word": "WORKING", "colour": "run"}


# ------------------------------------------------------------------ the thread, in the feed
#
# The stream is what the ENGINE said. The thread is what the AGENT and the OPERATOR said, and
# under take-control it is the whole record of the conversation -- condition 2 of the overrule.
# Both are rendered in one column because they are one story; they are keyed separately (`s<pos>`
# for a stream position, `t<seq>` for a thread row) because they are two clocks and pretending
# otherwise is how an append-only feed starts re-sending rows.

_THREAD_SINCE = """
    SELECT seq, ts, from_agent, to_agent, kind, text
      FROM brain.thread
     WHERE work_item_id = %s AND seq > %s AND kind IN ('msg', 'note', 'ask', 'answer')
     ORDER BY seq
"""


def thread_entries(task_id: str, agent: str, since_seq: int = 0) -> list:
    """Thread rows for one task as feed entries. Steering marks render as marks, not as speech."""
    if not task_id:
        return []
    with store.read() as s:
        rows = s.query(_THREAD_SINCE, (task_id, since_seq))
    out = []
    for r in rows:
        text = r["text"] or ""
        from_agent = r["from_agent"] or ""
        if text.startswith("[steering] "):
            out.append({"kind": "mark", "key": f"t{r['seq']}", "seq": r["seq"], "at": r["ts"],
                        "who": from_agent, "text": text})
            continue
        mine = from_agent != agent
        out.append({
            "kind": "said", "key": f"t{r['seq']}", "seq": r["seq"], "at": r["ts"],
            "who": from_agent, "to": r["to_agent"] or "", "verb": r["kind"],
            "operator": mine,          # brand left edge and a `you -> T3` head, per C section 2.6
            "text": text,
        })
    return out


def feed(name: str, since: int = 0, since_seq: int = 0, runs_back: int = 3) -> dict:
    """Everything one agent's runfeed needs, degraded states included, verbs called: zero.

    `since` keys the CURRENT run only. Older runs come back whole on a full render and are never
    re-sent by a poll, which is what keeps the feed append-only.
    """
    agent = agent_row(name)
    runs = agent_runs(name, runs_back)
    now = time.time()

    out = {
        "agent": name, "row": agent, "runs": [], "findings": [], "vitals": None,
        "steering": None, "steered": False, "position": since, "seq": since_seq,
        "now": int(now),
    }
    if not runs:
        out["vitals"] = {
            "state": {"word": "RESTING", "colour": "rest"},
            "task": "", "attempt": 0, "elapsed": "", "beats": 0, "tools": 0,
            "cost": None, "cost_why": "no run has ever been recorded for this agent",
            "recency": recency(None), "now_line": "",
        }
        return out

    for i, r in enumerate(runs):
        path = r.get("stream_pointer") or str(stream_path(r["work_item_id"], r["attempt"]))
        current = i == 0
        read = read_run(path, since=since if current else 0)
        block = {
            "run": r, "current": current, "path": path,
            "host": r.get("stream_pointer_host") or "",
            "entries": read["entries"], "position": read["position"],
            "beats": read["beats"], "tools": read["tools"], "torn": read["torn"],
            "live_tool": read["live_tool"] if current else None,
            "open_fold": read.get("open_fold") if current else None,
            "result": read["result"], "now_line": read["now_line"],
            "readable": read["readable"], "error": read["error"],
            "seam": _seam(r),
        }
        # --------------------------------------------------- the four degraded states
        if not read["exists"]:
            block["degraded"] = {
                "level": "dmg", "word": "MISSING",
                "text": (f"run {r['work_item_id']}-attempt{r['attempt']} is recorded and its "
                         f"stream is not on disk"),
                "path": path, "host": r.get("stream_pointer_host") or "",
            }
        elif not read["readable"]:
            block["degraded"] = {
                "level": "wait", "word": "unreadable",
                "text": f"stream unreadable at {path} · showing nothing rather than guessing",
                "path": path, "host": r.get("stream_pointer_host") or "", "raw": read["error"],
            }
        elif read["events"] == 0 and read["torn"]:
            block["degraded"] = {
                "level": "wait", "word": "unreadable",
                "text": (f"every line of {path} is torn · showing nothing rather than guessing"),
                "path": path, "host": r.get("stream_pointer_host") or "",
                "raw": f"{read['torn']} torn line(s), 0 whole events",
            }
        else:
            block["degraded"] = None

        if current:
            age = _age(read["last_event_at"], now)
            state = _state_of(agent, r, age)
            cost, why = derived_cost(path, read["session_id"]) if read["exists"] else (None, "")
            started = r.get("started_at")
            out["vitals"] = {
                "state": state,
                "task": r["work_item_id"], "attempt": r["attempt"],
                "title": r.get("title") or "",
                "elapsed": elapsed_words(_age(started, now)),
                "beats": read["beats"], "tools": read["tools"],
                "cost": cost, "cost_why": why,
                "recency": recency(age), "now_line": read["now_line"],
                "ended": bool(r.get("ended_at")), "outcome": r.get("outcome") or "",
            }
            out["position"] = read["position"]
            # ------------------------------------ two clocks that can disagree
            hb = _age(agent.get("updated") if agent else None, now)
            if (hb is not None and age is not None and hb >= QUIET_AFTER
                    and age < FRESH_UNDER and not r.get("ended_at")):
                out["findings"].append({
                    "level": "wait",
                    "text": (f"heartbeat {_ago(hb)} old while events arrive "
                             f"({_ago(age)} ago) — two clocks disagree"),
                    "detail": ("Both facts are rendered rather than one being picked: the "
                               "heartbeat is written by the runner and the stream by the "
                               "engine, so a stale one means the runner is wedged, not that "
                               "the work stopped."),
                })
        if current:
            block["said"] = thread_entries(r["work_item_id"], name, since_seq)
            out["seq"] = max([e["seq"] for e in block["said"]] + [since_seq])
            live = steering.active_steering(r["work_item_id"])
            # CONDITION 3, BOTH HALVES. The thread says a session is open; the run row says
            # whether there is still a run for it to be open over. A pinned YOU ARE STEERING
            # header above a terminal that exited would be the console asserting control it
            # does not have.
            out["steering"] = (dict(live, agent=name, task=r["work_item_id"])
                               if live and not r.get("ended_at") else None)
            out["steered"] = steering.is_steered(r["work_item_id"])
        block["steered"] = steering.is_steered(r["work_item_id"])
        out["runs"].append(block)

    # RESTING carries the last run's tail rather than a blank screen (C section 2.8).
    if out["vitals"] and out["vitals"]["state"]["word"] in ("RESTING", "STOPPED"):
        last = runs[0]
        out["vitals"]["resting_note"] = (
            f"no task held · last run {last['work_item_id']} "
            f"{last.get('outcome') or 'ended'} "
            f"{last['ended_at'].strftime('%H:%MZ') if last.get('ended_at') else ''}")
    return out


def _age(when, now) -> int | None:
    """Seconds since a timestamp that may be a datetime, an ISO string, or absent."""
    if when is None or when == "":
        return None
    if hasattr(when, "timestamp"):
        return max(0, int(now - when.timestamp()))
    try:
        text = str(when).replace("Z", "+00:00")
        from datetime import datetime
        return max(0, int(now - datetime.fromisoformat(text).timestamp()))
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------------ the seam
#
# Two full-width mono rules between one run and the next, in receipt-stripe grammar. The fixed
# sentence is the point of the whole component: it teaches the statelessness contract at exactly
# the moment the operator could believe context flowed from the last task into this one.

FRESH_SESSION = "fresh session — nothing carries over but the bus"


def _seam(run) -> dict:
    """The closing line for a finished run and the opening line for the one that follows.

    `steered by you` rides the CLOSING line and it is permanent: it is read from the thread, so
    it survives everything except deleting an append-only row.
    """
    outcome = str(run.get("outcome") or "")
    colour = {"done": "fin", "fail": "dmg", "block": "wait", "cancel": "rest",
              "timeout": "dmg", "killed": "dmg"}.get(outcome, "rest")
    return {
        "outcome": outcome, "colour": colour,
        "opened": (f"claimed {run['work_item_id']} · attempt {run['attempt']} · "
                   f"{run['started_at'].strftime('%H:%MZ') if run.get('started_at') else ''} · "
                   f"{FRESH_SESSION}"),
        "closed": (f"{run['work_item_id']} {outcome or 'ended'} "
                   f"({'reported, not accepted' if outcome == 'done' else outcome or 'no report'})"
                   f" · {run['ended_at'].strftime('%H:%MZ') if run.get('ended_at') else ''}"
                   if run.get("ended_at") else ""),
    }


def run_model(path) -> str:
    """The model THIS run announced, from its own `system/init` event. Nothing else from it.

    The picker's "this run" line has to state a fact about the run rather than what config says
    now, or a change made a minute ago reads as though it had been in force all along -- the
    "looks instant" the brief forbids. The engine announces the model it started under in the
    first event of the stream, so that is where this reads it.

    THAT EVENT ALSO CARRIES `permissionMode`. This function returns one key and never that one;
    `_SUPPRESSED_KEYS` names the ones that may not leave this module, and `churn_line` refuses
    the whole event type so an unfolded churn list cannot leak it either.
    """
    p = Path(path)
    if not p.exists():
        return ""
    try:
        with p.open(encoding="utf-8", errors="replace") as fh:
            for _ in range(12):            # `init` is the first event; a dozen lines is generous
                line = fh.readline()
                if not line:
                    break
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict) and obj.get("subtype") == "init":
                    return str(obj.get("model") or "")
    except OSError:
        return ""
    return ""
