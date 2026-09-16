"""The verb surface. `python3 -m fabric.cli <verb> ...`

Per the narrow waist in `D00`, this is a thin wrapper over the transitions and nothing else. It
holds no SQL, no state machine and no second implementation of anything: a CLI, an MCP tool, an
n8n webhook and the console all call `store.apply` with the same verb name and get the same code.
No integration earns a private path, and that includes this one.
"""

from __future__ import annotations

import argparse
import json
import sys

import store

from . import emit as _emit
from . import lag as _lag
from . import types as _types
from .producers import questions as _questions


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="fabric", description="the event fabric's verbs")
    sub = p.add_subparsers(dest="verb", required=True)

    e = sub.add_parser("emit", help="event emit: the ONLY way an event is written")
    e.add_argument("--type", required=True)
    e.add_argument("--external", choices=("true", "false"), required=True,
                   help="required: an event whose flag nobody decided is not an unflagged event")
    e.add_argument("--canon-touching", choices=("true", "false"), required=True)
    e.add_argument("--department", default="")
    e.add_argument("--lane", default="")
    e.add_argument("--subject-type", default="")
    e.add_argument("--subject-id", default="")
    e.add_argument("--summary", default="")
    e.add_argument("--payload-ref", default=None)
    e.add_argument("--produced-by", default=None)
    # The other two thirds of the lineage triple. Without them the CLI can emit only "resolved"
    # and "nobody asked", and `ambiguous` is unsayable from this surface.
    e.add_argument("--produced-by-ref", default=None)
    e.add_argument("--resolution-status", default=None,
                   choices=("resolved", "ambiguous", "unresolved"))
    e.add_argument("--actor", default="")

    a = sub.add_parser("ack", help="event ack: advance one subscriber's cursor")
    a.add_argument("--subscriber", required=True)
    a.add_argument("--seq", type=int, required=True)

    s = sub.add_parser("show", help="read one event by sequence")
    s.add_argument("seq", type=int)

    sub.add_parser("types", help="the registered event types")
    sub.add_parser("health", help="liveness AND lag, because they are different questions")
    sub.add_parser("lag", help="per-subscriber lag: max(event_seq) - last_seq")
    sub.add_parser("receipts-due", help="EF-4: consequential events with no receipt booked")
    sub.add_parser("verbs", help="every registered transition and the role it runs as")

    q = sub.add_parser("quarantine", help="a subscriber stops itself")
    q.add_argument("--subscriber", required=True)
    q.add_argument("--reason", required=True)

    r = sub.add_parser("release", help="un-quarantine. A human's act, through the runtime role")
    r.add_argument("--subscriber", required=True)
    r.add_argument("--by", required=True)

    o = sub.add_parser("observe", help="observation open: EF-2's second record")
    o.add_argument("--event-seq", type=int, required=True)
    o.add_argument("--kind", default="")
    o.add_argument("--text", default="")
    o.add_argument("--subject-type", default="")
    o.add_argument("--subject-id", default="")

    d = sub.add_parser("dispose", help="disposition record: EF-3, event required, observation not")
    d.add_argument("--event-seq", type=int, required=True)
    d.add_argument("--verdict", required=True)
    d.add_argument("--rationale", default="")
    d.add_argument("--observation-id", type=int, default=None)
    d.add_argument("--by", default="")

    n = sub.add_parser("notify-hook",
                       help="what ~/.swarm/notify.sh calls: one swarm ask -> one event")
    n.add_argument("message")

    args = p.parse_args(argv)

    if args.verb == "emit":
        _print(_emit.emit(
            type=args.type,
            external=args.external == "true",
            canon_touching=args.canon_touching == "true",
            department=args.department, lane=args.lane,
            subject_type=args.subject_type, subject_id=args.subject_id,
            payload_summary=args.summary, payload_ref=args.payload_ref,
            produced_by=args.produced_by, produced_by_ref=args.produced_by_ref,
            resolution_status=args.resolution_status, actor=args.actor))
    elif args.verb == "ack":
        _print(_emit.ack(args.subscriber, args.seq))
    elif args.verb == "show":
        with store.read("runtime") as rs:
            _print(rs.one("SELECT * FROM brain.event WHERE event_seq = %s", (args.seq,)))
    elif args.verb == "types":
        _print({k: {"retention_class": v.retention_class, "books_receipt": v.books_receipt,
                    "description": v.description} for k, v in _types.all_types().items()})
    elif args.verb == "health":
        _print(_lag.health())
    elif args.verb == "lag":
        _print(_lag.subscribers())
    elif args.verb == "receipts-due":
        _print(_lag.receipts_due())
    elif args.verb == "verbs":
        _print(store.transitions.registered())
    elif args.verb == "quarantine":
        _print(_emit.quarantine(args.subscriber, args.reason))
    elif args.verb == "release":
        _print(_emit.release(args.subscriber, args.by))
    elif args.verb == "observe":
        _print({"observation_id": _emit.observe(
            args.event_seq, kind=args.kind, text=args.text,
            subject_type=args.subject_type, subject_id=args.subject_id)})
    elif args.verb == "dispose":
        _print({"disposition_id": _emit.dispose(
            args.event_seq, args.verdict, rationale=args.rationale,
            observation_id=args.observation_id, decided_by=args.by)})
    elif args.verb == "notify-hook":
        _print(_questions.emit_question_raised(args.message))
    return 0


if __name__ == "__main__":
    sys.exit(main())
