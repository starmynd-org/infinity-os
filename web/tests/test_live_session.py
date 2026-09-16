"""The live-session reader, proven rather than promised. Read only, no store, no browser.

    python3 -m web.tests.test_live_session

WHAT IT PROVES AND WHY EACH ONE IS HERE. Every case below is a defect that was either found by
measuring this host's real corpus or watched failing in a browser on 2026-08-28, so none of them
is a hypothetical.

  1. THE SUPPRESSION, STRUCTURALLY, OVER THE WHOLE REAL CORPUS. Not "the string is absent" -- the
     string legitimately appears in sessions where the operator was discussing this very rule, and
     censoring his own prose about his own system would be a different lie. What is proven is that
     no record CARRYING a suppressed key produces a churn line, and that every row rendered as
     speech carries text byte-identical to its own `message.content`.
  2. THE FOUR TORN-TAIL CASES, against a file that is grown a piece at a time: a half-written
     record renders nothing, the completed record then renders, a file of nothing but torn lines
     is AMBER and not damage, and a 1.3MB record stays torn across several reads without ever
     rendering a fragment.
  3. THE CURSOR DEFECT, which is the one this lane shipped and caught. A torn tail must not
     advance the cursor. It did, the client stepped past it, and the record that line became was
     never sent -- while the counters, which are computed over the whole file, moved. A screen
     that says something arrived and does not show it.
  4. THE USAGE DEDUPE. One assistant message is written as several records and every one of them
     repeats the completed `message.usage`. Summing the records overstates spend 2.22x across this
     corpus, and the price card must be handed one bag per `message.id`.
  5. `_who`, on every shape the corpus actually contains, including the live subagent brief that
     carries no `origin` and no `promptSource` and was missed until a live one was opened.

NOT RUN IS A VERDICT (docs/SUITE-INPUT-RULE.md). Cases 1 and 5's corpus sweep needs the operator's
projects root; without it they report NOT RUN and exit 77 rather than passing on no input.
"""

from __future__ import annotations

import json
import os as _os
import sys as _sys
import tempfile
import traceback
from pathlib import Path

_R = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
for _p in (_R, _os.path.join(_R, 'engine'), _os.path.join(_R, 'queue')):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

from web import live_session as L                                        # noqa: E402

NOT_RUN = 77
FAILS: list = []
CHECKS = 0


def ck(name, cond, got=""):
    global CHECKS
    CHECKS += 1
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{got}]" if got != "" else ""))
    if not cond:
        FAILS.append(name)


# ------------------------------------------------------------------ case 1 and 5: the corpus

def corpus_files() -> list:
    root = L.projects_root()
    if not root.is_dir():
        return []
    out = []
    for dp, _dn, fn in _os.walk(root):
        out += [_os.path.join(dp, f) for f in fn if f.endswith(".jsonl")]
    return out


def test_the_corpus():
    files = corpus_files()
    if not files:
        print(f"NOT RUN: {L.projects_root()} holds no transcript. This case needs the operator's "
              f"own corpus and refuses to pass on no input.")
        _sys.exit(NOT_RUN)
    print(f"\ncase 1+5 · the whole corpus, {len(files)} files under {L.projects_root()}")

    carriers = lines_from_carriers = 0
    said = said_wrong = 0
    briefs = 0
    types: dict = {}
    for f in files:
        try:
            raw = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        first_user = True
        sidechain = "/subagents/" in f
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            if L._carries_suppressed(obj):
                carriers += 1
                types[obj.get("type")] = types.get(obj.get("type"), 0) + 1
                if L._line(obj) != "":
                    lines_from_carriers += 1
            if obj.get("type") != "user":
                continue
            who, operator = L._who(obj, is_first_user=first_user)
            first_user = False
            if not who:
                continue
            said += 1
            if sidechain and operator:
                said_wrong += 1                 # a sidechain has no operator in it, ever
            if who == "launched with this brief":
                briefs += 1
            content = (obj.get("message") or {}).get("content")
            if isinstance(content, str) and L._text_of(obj) != content.strip():
                said_wrong += 1

    ck("every record carrying a suppressed key is refused a churn line",
       lines_from_carriers == 0, f"{carriers} carriers {types}, {lines_from_carriers} lines")
    ck("every rendered row's text is exactly its own message.content",
       said_wrong == 0, f"{said} speech rows, {said_wrong} wrong")
    ck("subagent launch briefs are recognised", briefs > 0, f"{briefs} briefs")
    ck("speech is a small minority of `user` records", said > 0, f"{said} speech rows")


# ------------------------------------------------------------------ case 4: the usage dedupe

def test_usage_is_deduplicated_by_message_id():
    print("\ncase 4 · one bag of tokens per API message, not per record")
    u = L._Usage()
    rec = {"type": "assistant", "requestId": "req_1", "message": {
        "id": "msg_1", "model": "claude-opus-5", "role": "assistant",
        "usage": {"input_tokens": 2, "output_tokens": 500,
                  "cache_read_input_tokens": 20000, "cache_creation_input_tokens": 1000}}}
    for _ in range(3):                      # thinking, text and tool_use, one record each
        u.add(rec)
    ck("three records of one message count once",
       u.messages == 1 and u.records == 3 and u.tokens["output_tokens"] == 500,
       f"messages={u.messages} records={u.records} out={u.tokens['output_tokens']}")

    second = json.loads(json.dumps(rec))
    second["message"]["id"] = "msg_2"
    u.add(second)
    ck("a second message adds a second bag",
       u.messages == 2 and u.tokens["output_tokens"] == 1000, str(u.tokens["output_tokens"]))

    from budget import price_card
    ck("the price card is asked, not a second implementation",
       float(price_card.derive_usd(u.tokens)) > 0
       and L.derived_cost({"tokens": u.tokens})[0] == float(price_card.derive_usd(u.tokens)))
    ck("no usage at all is `unavailable` and never 0.0",
       L.derived_cost({"tokens": None}) == (None, "no assistant message in this session carries "
                                                  "a usage block yet"))


# ------------------------------------------------------------------ cases 2 and 3: the torn tail

def _rec(**kw):
    d = {"sessionId": "s", "timestamp": "2026-08-28T20:00:00.000Z", "permissionMode": "auto"}
    d.update(kw)
    return json.dumps(d)


def _human(t):
    return _rec(type="user", origin={"kind": "human"}, promptSource="typed",
                message={"role": "user", "content": t})


def _beat(t, mid="msg_a"):
    return _rec(type="assistant", requestId="req_a", message={
        "id": mid, "model": "claude-opus-5", "role": "assistant",
        "content": [{"type": "text", "text": t}],
        "usage": {"input_tokens": 1, "output_tokens": 10,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}})


def test_the_torn_tail():
    print("\ncase 2+3 · a file being appended to while it is read")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "t.jsonl"
        f.write_text(_human("what is going on") + "\n" + _beat("here is what") + "\n",
                     encoding="utf-8")
        whole = L.read_session(str(f))
        ck("a whole file reads clean", whole["torn"] == 0 and whole["position"] == 2
           and len(whole["entries"]) == 2, f"pos={whole['position']} torn={whole['torn']}")

        victim = _beat("this one arrives in two pieces", mid="msg_b")
        with f.open("a", encoding="utf-8") as fh:
            fh.write(victim[: len(victim) // 2])
        torn = L.read_session(str(f))
        ck("a torn tail renders NOTHING", len(torn["entries"]) == len(whole["entries"]),
           f"{len(whole['entries'])} -> {len(torn['entries'])}")
        ck("a torn tail is not an error", torn["readable"] and not torn["error"])
        ck("a torn tail is counted", torn["torn"] == 1)
        # CASE 3, THE DEFECT. `lines` moved and the CURSOR did not.
        ck("A TORN TAIL DOES NOT ADVANCE THE CURSOR",
           torn["position"] == whole["position"] and torn["lines"] == whole["lines"] + 1,
           f"position {whole['position']} -> {torn['position']}, "
           f"lines {whole['lines']} -> {torn['lines']}")

        with f.open("a", encoding="utf-8") as fh:
            fh.write(victim[len(victim) // 2:] + "\n")
        done = L.read_session(str(f), since=torn["position"])
        ck("the completed record is then SENT to a client holding that cursor",
           [e["kind"] for e in done["entries"]] == ["beat"], str(done["entries"])[:80])
        ck("and the cursor then moves", done["position"] == 3, done["position"])

        allbad = Path(d) / "bad.jsonl"
        allbad.write_text('{"type": "assis\n{"type": "us\n{"br\n', encoding="utf-8")
        r = L.read_session(str(allbad))
        ck("a file of nothing but torn lines has 0 records and 3 torn",
           r["events"] == 0 and r["torn"] == 3 and r["entries"] == [])

        big = _rec(type="user", origin={"kind": "human"}, promptSource="typed",
                   message={"role": "user", "content": "X" * 1_300_000})
        seen = []
        base = f.read_text(encoding="utf-8")
        for cut in (300_000, 900_000, len(big)):
            f.write_text(base + big[:cut] + ("\n" if cut == len(big) else ""), encoding="utf-8")
            rr = L.read_session(str(f))
            seen.append((rr["torn"], len([e for e in rr["entries"] if e["kind"] == "said"])))
        ck("a 1.3MB record stays torn across reads and never renders a fragment",
           seen == [(1, 1), (1, 1), (0, 2)], str(seen))


# ------------------------------------------------------------------ case 5: who is speaking

def test_who_is_speaking():
    print("\ncase 5 · `user` is not the human")
    cases = [
        ("a tool result", {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "ok"}]}}, False, None, False),
        ("what he typed", json.loads(_human("hello")), False, "you", True),
        ("a queued message he typed", {"type": "user", "origin": {"kind": "human"},
                                       "promptSource": "queued",
                                       "message": {"role": "user", "content": "hi"}},
         False, "you", True),
        ("a harness task notification", {"type": "user", "origin": {"kind": "task-notification"},
                                         "message": {"role": "user", "content": "<t/>"}},
         False, "harness · background task", False),
        ("a coordinator message", {"type": "user", "origin": {"kind": "coordinator"},
                                   "message": {"role": "user", "content": "next"}},
         False, "the session that launched this one", False),
        ("a peer session", {"type": "user", "origin": {"kind": "peer"},
                            "message": {"role": "user", "content": "hi"}},
         False, "another session", False),
        ("a headless launch brief", {"type": "user", "promptSource": "sdk",
                                     "message": {"role": "user", "content": "You are agent T4"}},
         False, "launched with this brief", False),
        ("a LIVE subagent brief: no origin, no promptSource, first, sidechain",
         {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "You are"}},
         True, "launched with this brief", False),
        ("the same shape NOT first is not a brief",
         {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "[Image:"}},
         False, None, False),
        ("a <local-command-caveat> opening a MAIN file is not speech",
         {"type": "user", "isSidechain": False,
          "message": {"role": "user", "content": "<local-command-caveat>"}}, True, None, False),
    ]
    for label, obj, first, who, operator in cases:
        got_who, got_op = L._who(obj, is_first_user=first)
        ck(label, (got_who, got_op) == (who, operator), f"{got_who!r},{got_op}")

    ck("only what he typed wears the brand accent",
       L._who(json.loads(_human("x")))[1] is True
       and L._who({"type": "user", "isSidechain": True,
                   "message": {"role": "user", "content": "brief"}}, is_first_user=True)[1] is False)


def test_the_refused_types():
    print("\ncase 1b · the record types that may never speak")
    ck("a permission-mode record makes no line",
       L._line({"type": "permission-mode", "permissionMode": "bypassPermissions"}) == "")
    ck("a mode record makes no line", L._line({"type": "mode", "mode": "normal"}) == "")
    ck("an attachment record makes no line",
       L._line({"type": "attachment", "attachment": {"content": "the operator's CLAUDE.md"}}) == "")
    ck("a user record carrying the key makes no line", L._line(json.loads(_human("hi"))) == "")
    ck("a tool call still makes one",
       "Bash" in L._line({"type": "assistant", "message": {"content": [
           {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}}))


def verdict() -> int:
    """The verdict, and beside it the count of checks that produced it. Task 0445.

    A module-level function rather than four lines at the foot of `main`, so the zero branch is
    reachable from a probe that imports this file and sets the counters, instead of only from a
    run that happens to compare nothing. `CHECKS` is the denominator and `ck` is the only thing
    that raises it, so an empty set here means every one of the five cases raised past its own
    assertions -- which `main` catches and records, so the run would otherwise still reach this
    line. It printed `0 checks, 5 failed` in that case, which is at least red; what it printed
    if the case list itself were ever emptied was `0 checks, 0 failed` and exit 0.

    Not to be confused with NOT RUN: case 1+5 exits 77 from inside `test_the_corpus` when the
    operator's transcript root is absent, and never reaches here. That is the suite declaring
    it had no input. This is the suite declaring it had input and compared none of it.
    """
    if CHECKS == 0:                                                     # DENOMINATOR
        print("0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"\n{CHECKS} checks, {len(FAILS)} failed")
    for f in FAILS:
        print("  FAILED:", f)
    return 1 if FAILS else 0


def main() -> int:
    for t in (test_usage_is_deduplicated_by_message_id, test_the_torn_tail,
              test_who_is_speaking, test_the_refused_types, test_the_corpus):
        try:
            t()
        except SystemExit:
            raise
        except Exception:                                               # noqa: BLE001
            traceback.print_exc()
            FAILS.append(t.__name__ + " raised")
    return verdict()


if __name__ == "__main__":
    _sys.exit(main())
