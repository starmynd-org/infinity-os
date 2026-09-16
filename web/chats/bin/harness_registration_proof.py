"""CLAUDE CODE AND CODEX REGISTER, AGAINST REAL TRANSCRIPTS ON THIS MACHINE.

    python web/chats/bin/harness_registration_proof.py <claude-home> <codex-sessions-root>

Andrew named both harnesses, so both are exercised, **and against files that actually exist rather
than against fixtures.** A fixture proves the code runs; a real rollout file proves the naming rule
this module reproduces is the naming rule the other program uses.

**mutatesState: YES.** It writes into a temporary queue root and deletes it. It reads the real
transcript directories and **writes nothing to them, opens no store, and prints no transcript
content** -- paths, sizes and stamps only.

WHAT IT PROVES, AND THE LAST TWO ARE THE ONES A HAPPY-PATH PROOF WOULD SKIP

    1  a Claude Code session binds to a real transcript on disk, verified
    2  a Codex session binds to a real rollout file, located by walking sessions/YYYY/MM/DD
    3  the OS RE-STATS the pointer instead of believing the message's own TRANSCRIPT-VERIFIED
    4  a LIED-ABOUT pointer registers with verified=False -- the lie is recorded, not believed
    5  a session with no id at all registers, unverified, with the reason in the record
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from web.chats import harness, protocol, registry                    # noqa: E402
from web.chats.roundtrip import OSSide                                # noqa: E402

CHECKS = 0
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  --  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def _newest(root: str, suffix: str = ".jsonl") -> str:
    best, best_m = "", -1.0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(suffix):
                continue
            path = os.path.join(dirpath, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime > best_m:
                best, best_m = path, mtime
    return best


def _register(os_side: OSSide, root: str, name: str, sess: harness.HarnessSession):
    registry.send_registration(declared_name=name, harness=sess.harness,
                               workdir=sess.workdir, base=root, provenance=sess.as_body())
    minted = os_side.accept_registrations()
    return minted[0] if minted else None


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        print("error: give <claude-home> and <codex-sessions-root>", file=sys.stderr)
        return 1
    claude_home, codex_home = sys.argv[1], sys.argv[2]
    root = tempfile.mkdtemp(prefix="chats-harness-")
    os_side = OSSide(base=root)
    os_side.root()

    print("=" * 78)
    print("INFINITY OS -- CLAUDE CODE AND CODEX REGISTER, against real transcripts")
    print(f"  as-of            {protocol.utc_stamp()}")
    print(f"  claude home      {claude_home}")
    print(f"  codex home       {codex_home}")
    print(f"  queue root       {root}   (temporary; deleted on a clean run)")
    print("  mutatesState     YES on the queue root. The transcript directories are READ ONLY.")
    print("  store            NONE OPENED.   content printed: NONE")
    print("=" * 78)

    try:
        # -------------------------------------------------------- 1. Claude Code, a real transcript
        print("\n1. CLAUDE CODE -- bind to a real transcript this machine actually holds")
        real = _newest(os.path.join(claude_home, "projects"))
        check("found a real Claude Code transcript to bind to", bool(real),
              os.path.basename(real) if real else "none under projects/")
        if not real:
            return 1
        cc = harness.claude_code_session(claude_home=claude_home, transcript_path=real)
        check("it verified against disk", cc.verified,
              f"{cc.transcript_bytes} bytes, mtime {cc.transcript_mtime_utc}")
        check("the session id was derived from the filename, not invented",
              bool(cc.session_id) and cc.session_id in os.path.basename(real).lower(),
              f"session_id={cc.session_id}")
        rec = _register(os_side, root, "claude-code session", cc)
        check("the OS registered it and RE-STAT'd the pointer itself",
              rec is not None and rec.transcript_verified,
              f"agent_id={rec.agent_id if rec else None}")

        # -------------------------------------------------------- 2. Codex, a real rollout
        print("\n2. CODEX -- locate a real rollout by session id, walking sessions/YYYY/MM/DD")
        rollout = _newest(os.path.join(codex_home, "sessions"))
        check("found a real Codex rollout to bind to", bool(rollout),
              os.path.basename(rollout) if rollout else "none under sessions/")
        if rollout:
            by_path = harness.codex_session(codex_home=codex_home, transcript_path=rollout)
            check("the rollout filename parsed and yielded a session id",
                  bool(by_path.session_id), f"session_id={by_path.session_id}")
            # The real test of the naming rule: throw the path away, keep only the id, find it again.
            by_id = harness.codex_session(codex_home=codex_home, session_id=by_path.session_id)
            check("the SAME file was found from the session id ALONE",
                  by_id.verified and os.path.normcase(by_id.transcript_path)
                  == os.path.normcase(os.path.abspath(rollout)),
                  f"{by_id.transcript_bytes} bytes; located by walk")
            rec2 = _register(os_side, root, "codex session", by_id)
            check("the OS registered the Codex session with a verified pointer",
                  rec2 is not None and rec2.transcript_verified,
                  f"agent_id={rec2.agent_id if rec2 else None}")

        # -------------------------------------------------------- 3. the lie
        print("\n3. THE LIE -- a registration whose message SWEARS its pointer is verified")
        liar = harness.HarnessSession(
            harness="claude-code", session_id="00000000-0000-0000-0000-000000000000",
            transcript_path=os.path.join(claude_home, "projects", "nope", "nope.jsonl"),
            verified=True, transcript_bytes=999999, transcript_mtime_utc="20260909T000000Z",
            workdir=os.getcwd(), detected_from="a message that is lying",
            notes=["this record asserts verified=True over a path that does not exist"])
        body = liar.as_body()
        check("the message really does claim TRANSCRIPT-VERIFIED: yes",
              "TRANSCRIPT-VERIFIED: yes" in body, "so the check below is not vacuous")
        rec3 = _register(os_side, root, "a lying client", liar)
        check("the OS recorded verified=False ANYWAY, having stat'd it itself",
              rec3 is not None and not rec3.transcript_verified,
              "a message is never authorization -- not for a verb, and not for a fact")
        check("and it kept the claimed pointer rather than discarding it",
              rec3 is not None and rec3.transcript_path.endswith("nope.jsonl"),
              "an unverified pointer is a lead; deleting it would delete the evidence")

        # ---------------------------------------------- 4-pre. the encoder, against real directories
        # The encoder reproduces ANOTHER program's naming rule, so the only honest test of it is
        # the directories that program actually created. A fixture here would test my regex
        # against my own understanding of my regex, which is how the first version passed review
        # in my head and failed on disk by exactly one hyphen.
        print("\n4-pre. THE PROJECT-DIRECTORY ENCODER, checked against directories on this disk")
        projects_root = os.path.join(claude_home, "projects")
        real_dirs = sorted(d for d in os.listdir(projects_root)
                           if os.path.isdir(os.path.join(projects_root, d)))
        check("there are real project directories to check against", len(real_dirs) >= 2,
              f"denominator {len(real_dirs)} directories")
        # `C--Users-you-repos` must be produced from `C:\Users\you\repos`, character for
        # character. This is the one mapping whose input is known independently.
        produced = harness.encode_project_dir(r"C:\Users\you\repos")
        check("encode(C:\\Users\\you\\repos) reproduces a directory that exists",
              produced in real_dirs, f"produced {produced!r}")

        # ---------------------------------------------- 4a. SELF-identification from the environment
        # THIS IS ANDREW'S ACTUAL QUESTION -- "can we get Claude Code to register?" -- so it is
        # exercised with NO session id and NO path supplied: only the environment and a workdir.
        print("\n4a. SELF-IDENTIFICATION -- a Claude Code process registering itself, unaided")
        workdir = os.environ.get("PROOF_WORKDIR", r"C:\Users\you\repos")
        alone = harness.claude_code_session(claude_home=claude_home, workdir=workdir)
        check("it found its own session id in the environment", bool(alone.session_id),
              f"detected_from={alone.detected_from}, session_id={alone.session_id or '(none)'}")
        check("and bound it to a transcript that exists, with nothing supplied", alone.verified,
              f"{alone.transcript_path} ({alone.transcript_bytes} bytes)")
        rec4 = _register(os_side, root, "a self-identifying claude code session", alone)
        check("the OS registered it with a verified pointer",
              rec4 is not None and rec4.transcript_verified,
              f"agent_id={rec4.agent_id if rec4 else None}")

        # ---------------------------------------------- 4b. the same call where it CANNOT work
        print("\n4b. THE SAME CALL, WHERE IT CANNOT WORK -- a workdir with no project directory")
        blind = harness.claude_code_session(claude_home=claude_home,
                                            workdir=r"C:\no\such\place\at\all")
        check("it did NOT verify", not blind.verified, "; ".join(blind.notes)[:170])
        check("and it did not invent a file, only a derivation it labelled as one",
              bool(blind.transcript_path) and not os.path.exists(blind.transcript_path),
              "the derived path is reported so a reader can see WHAT was looked for")
        rec5 = _register(os_side, root, "an unfindable client", blind)
        check("it registered anyway, unverified", rec5 is not None and not rec5.transcript_verified,
              f"agent_id={rec5.agent_id if rec5 else None}")

        print("\n5. THE REGISTRY, as the Chats surface would read it")
        for record in registry.registered(root):
            flag = "VERIFIED" if record.transcript_verified else "unverified"
            print(f"    {record.agent_id:<28} {record.harness:<12} {flag:<10} "
                  f"{os.path.basename(record.transcript_path) or '(no pointer)'}")
    finally:
        pass

    print("\n" + "=" * 78)
    print(f"CHECKS {CHECKS}   PASS {CHECKS - len(FAILURES)}   FAIL {len(FAILURES)}")
    for name in FAILURES:
        print(f"  FAILED: {name}")
    print("=" * 78)
    if not FAILURES:
        shutil.rmtree(root, ignore_errors=True)
    else:
        print(f"queue root KEPT for inspection: {root}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
