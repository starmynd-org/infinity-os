"""A minimal external agent, run as its own PROCESS, to prove the round trip is a round trip.

Run:  python web/chats/bin/demo_agent.py <CHATS_ROOT> <declared-name> <tasks-to-serve>

It is deliberately dumb. Its whole authority is two verbs registered in code below, and it exists
so that `roundtrip_proof.py` can prove the loop across a process boundary rather than inside one
interpreter, where a shared module object would do half the work and nobody would notice.

**It refuses a third verb on purpose.** A proof in which nothing is refused has watched one branch
of a guard, and *a guard that fires on everything is indistinguishable from one that fires on
nothing.*
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from web.chats.roundtrip import AgentClient   # noqa: E402


def _echo(body: str) -> str:
    """Return what was asked, so the proof can assert the payload survived the trip byte for byte."""
    payload = ""
    for line in body.splitlines():
        if line.startswith("PAYLOAD:"):
            payload = line.split(":", 1)[1].strip()
    return f"ECHO: {payload}\nPID: {os.getpid()}\n"


def _count_lines(body: str) -> str:
    return f"LINES: {len(body.splitlines())}\nPID: {os.getpid()}\n"


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: demo_agent.py <chats-root> <declared-name> <tasks-to-serve>",
              file=sys.stderr)
        return 1
    root, name, count = sys.argv[1], sys.argv[2], int(sys.argv[3])

    client = AgentClient(declared_name=name, harness="demo-agent", base=root)
    client.handle("echo", _echo).handle("count-lines", _count_lines)

    agent_id = client.register(timeout_seconds=30.0)
    print(f"[agent pid={os.getpid()}] registered as {agent_id}", flush=True)
    print(f"[agent pid={os.getpid()}] my whole verb set: {', '.join(client.verbs)}", flush=True)

    served = 0
    while served < count:
        woken = client.serve_one(timeout_seconds=60.0)
        print(f"[agent pid={os.getpid()}] {woken.receipt()}", flush=True)
        if not woken.messages:
            print(f"[agent pid={os.getpid()}] woke with nothing; stopping rather than looping",
                  file=sys.stderr, flush=True)
            return 2
        served += len(woken.messages)
    print(f"[agent pid={os.getpid()}] served {served} message(s); exiting", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
