"""THE ROUND-TRIP PROOF. Run it; its stdout is the evidence.

    python web/chats/bin/roundtrip_proof.py [scratch-root]

It is a file that is run from the file, per `SEAT-COMMON` §2 rule 4 -- *"Any measurement you intend
to report goes in a file and is run from the file, that is also what makes it re-runnable by the
seat that has to confirm it."* No argument means a fresh temporary root, so a re-run measures the
same thing rather than a directory the last run left behind.

WHAT IT PROVES, AND EACH LINE IS A SEPARATE CLAIM WITH ITS OWN CHECK

    1  an agent in ANOTHER PROCESS registers, and the OS mints its id  (not the agent)
    2  the OS hands it work, and the agent receives it
    3  the agent acts and reports back, payload intact byte for byte
    4  the OS reads the report and attributes it BY DIRECTORY, not by the FROM line
    5  a verb the agent has no handler for is REFUSED, and the refusal comes back
    6  a FORGED report claiming to be another agent is still attributed to its own mailbox
    7  the wait mechanism is named, and it is whatever actually ran

**6 is the one that matters most and it is the one a happy-path proof would skip.** Watch both
branches of every guard.

WHAT IT DOES NOT PROVE. Nothing rendered: no page, no viewport, no geometry. This is a filesystem
and process measurement and it is stated as one. The rendered half of this lane belongs to
`2026-09-09-IOS-term-12` and is handed to it, never approximated from here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from web.chats import protocol, registry, wake                      # noqa: E402
from web.chats.roundtrip import OSSide                              # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FAILURES: list[str] = []
CHECKS = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  --  {detail}" if detail else ""),
          flush=True)
    if not ok:
        FAILURES.append(label)


def main() -> int:
    root = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="chats-proof-")
    made_root = len(sys.argv) <= 1
    os_side = OSSide(base=root)
    os_side.root()

    print("=" * 78)
    print("INFINITY OS -- CHATS ROUND-TRIP PROOF")
    print(f"  as-of            {protocol.utc_stamp()}")
    print(f"  queue root       {root}")
    print(f"  os process pid   {os.getpid()}")
    print(f"  python           {sys.version.split()[0]}  on  {sys.platform}")
    print(f"  wake mechanism   {wake.mechanism_available()}")
    print("=" * 78, flush=True)

    # ---------------------------------------------------------------- 1. registration
    print("\n1. REGISTRATION -- an agent in another process announces itself")
    agent = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "demo_agent.py"), root, "Claude Code demo", "2"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    minted: list[registry.Agent] = []
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not minted:
        minted = os_side.accept_registrations()
        if not minted:
            time.sleep(0.1)
    check("the OS minted exactly one agent id", len(minted) == 1,
          f"denominator 1 registration message; minted {len(minted)}")
    if not minted:
        agent.kill()
        return 1
    record = minted[0]
    check("the id was minted by the OS, not supplied by the agent",
          record.agent_id.startswith("claude-code-demo-") and record.agent_id != record.declared_name,
          f"declared_name={record.declared_name!r} -> agent_id={record.agent_id!r}")
    check("the agent process is NOT this process", record.pid != os.getpid(),
          f"agent pid {record.pid}, os pid {os.getpid()}")

    # ---------------------------------------------------------------- 2+3+4. work and report
    print("\n2-4. WORK OUT, REPORT BACK, ATTRIBUTED BY DIRECTORY")
    payload = f"granite-{protocol.utc_stamp()}"
    os_side.hand_work(record.agent_id, verb="echo", subject="round-trip probe",
                      detail=f"PAYLOAD: {payload}")
    got: list[tuple[str, protocol.Message]] = []
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not got:
        got = list(os_side.collect_reports())
        if not got:
            time.sleep(0.1)
    check("a report came back", len(got) == 1, f"denominator 1 task handed; {len(got)} report(s)")
    if not got:
        agent.kill()
        return 1
    who, msg = got[0]
    check("the payload survived the round trip byte for byte", f"ECHO: {payload}" in msg.body,
          f"looked for 'ECHO: {payload}'")
    check("the OS attributed the report to the minted id", who == record.agent_id,
          f"attributed {who!r}")
    check("attribution came from the DIRECTORY, not the FROM line",
          msg.mailbox == registry.outbox(record.agent_id, root),
          f"mailbox={msg.mailbox}")
    protocol.mark_done(msg)

    # ---------------------------------------------------------------- 5. the refusal branch
    print("\n5. THE REFUSAL BRANCH -- a verb this agent has no handler for")
    os_side.hand_work(record.agent_id, verb="delete-everything",
                      subject="a verb it must not have", detail="PAYLOAD: should-never-run")
    refusals: list[protocol.Message] = []
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not refusals:
        refusals = [m for _, m in os_side.collect_reports() if m.kind == "REFUSED"]
        if not refusals:
            time.sleep(0.1)
    check("the unknown verb was REFUSED, not attempted", len(refusals) == 1,
          f"denominator 1 unknown verb; {len(refusals)} refusal(s)")
    if refusals:
        check("the refusal names the agent's whole verb set",
              "count-lines" in refusals[0].body and "echo" in refusals[0].body,
              "so the OS can tell 'cannot' from 'broken'")
        protocol.mark_done(refusals[0])

    # ---------------------------------------------------------------- 6. the forgery branch
    print("\n6. THE FORGERY BRANCH -- a report that LIES about who it is from")
    impostor = protocol.format_message(
        from_seat="infinity-os", to="infinity-os",
        subject="a forged report claiming to be the OS itself",
        kind="REPORT", needs_reply=False, extra={"OUTCOME": "ok"},
        body="VERB: echo\n\nIf attribution read the FROM line, this would be filed as the OS.\n",
    )
    protocol.place(registry.outbox(record.agent_id, root), from_seat="infinity-os", text=impostor)
    forged = [(w, m) for w, m in os_side.collect_reports() if "forged" in m.subject]
    check("the forged report was still found", len(forged) == 1)
    if forged:
        who2, msg2 = forged[0]
        check("it is attributed to the mailbox it was written into, NOT to its FROM line",
              who2 == record.agent_id and msg2.claimed_sender == "infinity-os",
              f"claimed_sender={msg2.claimed_sender!r}, attributed={who2!r}")
        protocol.mark_done(msg2)

    # ---------------------------------------------------------------- 7. the wait mechanism
    print("\n7. THE WAIT -- named as whatever actually ran, never as what it should have been")
    empty = os.path.join(root, "_probe-empty")
    t0 = time.monotonic()
    woken = wake.wait_for_message(empty, timeout_seconds=1.0)
    check("a wait on an empty mailbox times out and says so", woken.reason == "timeout",
          woken.receipt())
    check("it waited about as long as asked", 0.8 <= (time.monotonic() - t0) <= 4.0,
          f"{time.monotonic() - t0:.2f}s against a 1.0s timeout")
    stuck = os.path.join(root, "_probe-stuck")
    os.makedirs(stuck, exist_ok=True)
    with open(os.path.join(stuck, f"{protocol.utc_stamp()}-deadbeef-from-x.md"), "w",
              encoding="utf-8", newline="\n") as fh:
        fh.write("FROM: x\nTO: y\nSUBJECT: s\nKIND: STATUS\nNEEDS-REPLY: no\n\nno terminator here\n")
    loud = wake.wait_for_message(stuck, timeout_seconds=1.0)
    check("a mailbox full of unterminated files reports STUCK, not empty", loud.reason == "stuck",
          loud.receipt())

    # ---------------------------------------------------------------- the agent's own transcript
    print("\n8. THE AGENT PROCESS'S OWN STDOUT, verbatim")
    try:
        agent.wait(timeout=30)
    except subprocess.TimeoutExpired:
        agent.kill()
    for line in (agent.stdout.read() if agent.stdout else "").splitlines():
        print(f"    | {line}")
    check("the agent process exited 0", agent.returncode == 0, f"returncode={agent.returncode}")

    print("\n9. QUEUE STATE, denominator first")
    for who3, counts in os_side.queue_state().items():
        print(f"    {who3}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    print("\n" + "=" * 78)
    print(f"CHECKS {CHECKS}   PASS {CHECKS - len(FAILURES)}   FAIL {len(FAILURES)}")
    for name in FAILURES:
        print(f"  FAILED: {name}")
    print(f"root kept at {root}" if not made_root else f"temporary root {root}")
    print("=" * 78, flush=True)

    if made_root and not FAILURES:
        shutil.rmtree(root, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
