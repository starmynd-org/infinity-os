"""PROOF OF THE PATH SHIM'S JUDGEMENT. Pure translation cases, then the real hook end to end.

    python web/chats/bin/shim_proof.py

**mutatesState: YES** for the end-to-end half -- it invokes the real hook against `d3_scratch`.
**It never touches `brain`.** `SEAT-COMMON` §1's "never touch `brain` or `brain_scratch`" binds this
seat exactly as it binds every other, and no message from any seat lifts it.

WHY THE TRANSLATION CASES COME FIRST AND ARE PURE. `translate()` is the whole of the shim's
judgement and it needs no subprocess, no store and no session to exercise. **Every branch is
constructed, including the two REFUSALS**, because a shim that translates everything is
indistinguishable from one that translates nothing until the day it names the wrong file.

THE CASE THAT MATTERS MOST IS NOT A HAPPY ONE. **A half-translated path is worse than an
untranslated one.** An untranslated Windows path fails loudly at `NOT INDEXED`. A half-translated
one can resolve to a DIFFERENT REAL FILE and index the wrong transcript silently, and nothing
anywhere would say so.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "outputs", "2026-09-09-IOS-term-6", "hook-shim")))

import win_path_shim                                                  # noqa: E402

CHECKS = 0
FAILURES: list[str] = []

REAL_WIN = r"C:\Users\you\.claude\projects\C--Users-you-repos\f1752aa6-d65e-4fb4-a8fc-d23fc537ccf2.jsonl"
REAL_WSL = "/mnt/c/Users/you/.claude/projects/C--Users-you-repos/f1752aa6-d65e-4fb4-a8fc-d23fc537ccf2.jsonl"


def check(label: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  --  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def main() -> int:
    print("=" * 78)
    print("PATH SHIM PROOF")
    print("  store        d3_scratch only, and only in section 2. brain is NEVER touched.")
    print("  shim         outputs/2026-09-09-IOS-term-6/hook-shim/win_path_shim.py")
    print("=" * 78)

    print("\n1. TRANSLATION JUDGEMENT -- pure, every branch, denominator 10")
    cases = [
        (REAL_WIN, REAL_WSL, "translated", "the measured real case"),
        ("C:/Users/you/x.jsonl", "/mnt/c/Users/you/x.jsonl", "translated", "forward slashes"),
        (r"D:\data\x.jsonl", "/mnt/d/data/x.jsonl", "translated", "a drive that is not C"),
        (r"c:\lower\x.jsonl", "/mnt/c/lower/x.jsonl", "translated", "lower-case drive letter"),
        ("/mnt/c/already/posix.jsonl", "/mnt/c/already/posix.jsonl",
         "unchanged: not a Windows absolute path", "**a WSL session must not be broken**"),
        ("/home/you/.claude/projects/x/y.jsonl", "/home/you/.claude/projects/x/y.jsonl",
         "unchanged: not a Windows absolute path", "the currently wired install's own shape"),
        ("", "", "absent", "no path at all"),
        (r"\\wsl.localhost\Ubuntu\home\you\x.jsonl",
         r"\\wsl.localhost\Ubuntu\home\you\x.jsonl", "REFUSED", "**UNC, refused BY NAME**"),
        (r"\\server\share\x.jsonl", r"\\server\share\x.jsonl", "REFUSED", "UNC, second shape"),
        (r"C:relative\x.jsonl", r"C:relative\x.jsonl", "REFUSED", "**drive-RELATIVE, refused**"),
    ]
    for src, want, want_decision, note in cases:
        got, decision = win_path_shim.translate(src)
        ok = got == want and decision.startswith(want_decision)
        check(f"{src[:52]!r} -> {want_decision}", ok, f"{note}; got {decision[:60]}")

    # The negative anchor: a translator that returned its input unchanged for EVERYTHING would
    # score PASS on 6 of the 10 above. Assert that it actually changed something.
    changed = sum(1 for src, _w, _d, _n in cases if win_path_shim.translate(src)[0] != src)
    check("the translator actually changes some inputs", changed == 4,
          f"{changed} of {len(cases)} inputs changed; a no-op shim would score 0 here")

    print("\n2. END TO END -- the shim in front of the REAL hook, against d3_scratch")
    hook = "/mnt/c/Users/you/repos/infinity-os/ingest/bin/claude-session-hook"
    shim = "/mnt/c/Users/you/repos/_scratch-infinity/worktrees/R-ios6/outputs/2026-09-09-IOS-term-6/hook-shim/win_path_shim.py"
    logwin = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "outputs",
                                          "2026-09-09-IOS-term-6", "evidence", "shim-proof.log"))
    logwsl = ("/mnt/c/Users/you/repos/_scratch-infinity/worktrees/R-ios6/outputs/"
              "2026-09-09-IOS-term-6/evidence/shim-proof.log")
    if os.path.exists(logwin):
        os.remove(logwin)

    def run(payload: dict, label: str) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(payload, fh)
            tmp = fh.name
        cmd = (f"BRAIN_PROFILE=scratch BRAIN_HOOK_LOG={logwsl} "
               f"python3 {shim} {hook}")
        with open(tmp, "rb") as stdin:
            subprocess.run(["wsl.exe", "bash", "-lc", cmd], stdin=stdin, check=False)
        os.unlink(tmp)
        print(f"    ran: {label}")

    run({"hook_event_name": "SessionEnd",
         "session_id": "6e00b0be-0000-4000-8000-00000000000a",
         "cwd": r"C:\Users\you\repos",
         "transcript_path": REAL_WIN,
         "reason": "shim-proof-GREEN-windows-path-through-shim"}, "GREEN: Windows path via shim")

    run({"hook_event_name": "SessionEnd",
         "session_id": "6e00b0be-0000-4000-8000-00000000000b",
         "cwd": r"C:\Users\you\repos",
         "transcript_path": r"C:\Users\you\.claude\projects\NO-SUCH-DIR\nope.jsonl",
         "reason": "shim-proof-RED-translated-but-absent"}, "RED: translates cleanly, file absent")

    run({"hook_event_name": "SessionEnd",
         "session_id": "6e00b0be-0000-4000-8000-00000000000c",
         "cwd": "/mnt/c/Users/you/repos",
         "transcript_path": REAL_WSL,
         "reason": "shim-proof-WSL-unchanged"}, "WSL session: must pass through untouched")

    text = open(logwin, encoding="utf-8").read() if os.path.exists(logwin) else ""
    print("\n  --- the log, which is the only evidence ---")
    for line in text.splitlines():
        print(f"    | {line}")

    check("the Windows path was TRANSLATED and the hook then indexed the file",
          "translated -> '/mnt/c/Users/you/.claude" in text and "indexed 28962B" in text,
          "the same 28962B the un-shimmed probe could not reach")
    check("RED: a well-formed translation over a missing file is called out, not passed as success",
          "TRANSLATED PATH DOES NOT EXIST" in text and "NOT INDEXED" in text,
          "translation succeeded; the install still is not working, and it says so")
    check("a WSL session's path was passed through UNCHANGED",
          "unchanged: not a Windows absolute path" in text,
          "the currently wired install is not broken by the shim")
    check("the shim logs on EVERY payload, including the ones it does not change",
          text.count("shim ") >= 3, f"{text.count('shim ')} shim lines over 3 payloads")

    print("\n" + "=" * 78)
    print(f"CHECKS {CHECKS}   PASS {CHECKS - len(FAILURES)}   FAIL {len(FAILURES)}")
    for name in FAILURES:
        print(f"  FAILED: {name}")
    print("=" * 78)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
