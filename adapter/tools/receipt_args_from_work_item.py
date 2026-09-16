#!/usr/bin/env python3
"""What an accepted work item can and cannot hand `receipt book`. Task 0291, from 0222.

    python3 adapter/tools/receipt_args_from_work_item.py <work-item-id> [--json]
                                                          [--allow-unaccepted]

**THIS TOOL NEVER BOOKS ANYTHING.** It reads one row and prints the argv a human would run.
That is deliberate and it is the whole shape of the task: booking a receipt is a git commit
into the operator's brain through the promotion door, and whether an agent may make that
commit is the OPERATOR'S CALL and has not been made. A tool that answered the mapping
question by booking would have answered a different question. So this reads, reports, and
stops one step short of the door.

WHAT V9 MEASURED, AND WHY THIS FILE EXISTS
------------------------------------------
The V9 acceptance run (task 0222, section 1, hop 13) walked the whole path -- voice note,
objective, queue item, drafted options, dispatch from the card, claim, run, steer, done,
accept from the console -- and hop 13, *"a receipt books to a durable and mirrored ref"*,
did not happen. Two facts sit behind that:

1. `accept work` books no receipt. `grep -i receipt engine/swarm_engine/accept.py` matches
   nothing, and it still matched nothing when this file was written.
2. The only agent-reachable door refuses: `mcp/tools.py:238 book_receipt` returns
   `cause='operator-decision-pending'` and names two blockers. The first is the operator
   decision above. The second is that the argument mapping *is not a wiring job* -- and
   that second one is what this tool answers, because it is real work either way the first
   is decided.

THE SHAPE OF THE ANSWER
-----------------------
`Receipt` (adapter/brain_adapter/receipt.py) has seven constructor arguments with no
default: `department`, `date`, `slug`, `title`, `moment`, `summary`, `lineage`. The MCP
tool's schema supplies `department` and `title` and none of the other five, which is where
that refusal's "none of the five" comes from.

Asked of an accepted `brain.work_item` row instead, the seven split three ways, and the
split is the finding rather than a preamble to it:

  SUPPLIED     a column holds it, and it is populated in practice
  EMPTY        the column exists and is NULL/'' on the rows that exist, so the value is
               absent rather than unmappable -- a different repair from the one below
  NO COLUMN    nothing on the row answers it and no rule in this repository derives it

`moment` and `department` are NO COLUMN, and they are not the same kind of gap:

  * `moment` is enum-checked against WAGER-14a's four booking moments
    (`disposition-created`, `wager-registered`, `result-produced`,
    `consequential-event-emitted`). **Acceptance is not one of the four.** The row's
    `external` and `canon_touching` flags do force `consequential-event-emitted` when
    either is true -- `Receipt.validate` refuses that moment when neither is (receipt.py,
    NOT_CONSEQUENTIAL) -- but nothing says which moment an *acceptance* is. That is a
    doctrine question answered in the brain's `_system/wager-ledger-rules.md`, which is
    outside this task's workdir and was not read. This tool states the constraint it can
    see from here and refuses to pick.
  * `department` has no derivation at all. The row carries `lane`; there is no
    lane-to-department map anywhere in this repository. `Receipt.validate` refuses
    NO_RECEIPT_HOME unless `departments/<department>/receipts/` already exists in the
    brain, so the value has to name a real directory a department declared -- which a lane
    name is not, and guessing one produces a typed refusal at best and a misfiled receipt
    at worst.

THE ONE THING ACCEPTANCE ADDS THAT NOTHING EARLIER HAS
------------------------------------------------------
`approval_ref`. `accept work` is the only writer of `accepted_by`/`accepted_at`
(engine/swarm_engine/accept.py), and it refuses a fleet agent as the acceptor, so the pair
is a record that a *human* decided. `promote()` refuses `CANON_WITHOUT_APPROVAL` and
`CANON_TOUCHING_WITHOUT_APPROVAL` when a promotion carries no `approval_ref`. So the reason
the receipt hop belongs at acceptance rather than at `done` is not sequencing taste: `done`
is a self-report and cannot produce the one lineage field a canon-touching promotion cannot
be made without. Acceptance is exactly the event that can.

The exact STRING that field should hold is a contract question -- the shape of record lives
in the brain's `entities/rules/result-and-escalation-contract.md`, also outside this workdir
-- so this tool proposes a form and labels it a proposal.

EXIT CODES
----------
  0  every one of the seven is supplied; the printed argv is complete
  3  at least one is EMPTY or NO COLUMN; the argv is printed with those left as
     <<REFUSED: ...>> so it cannot be pasted and run by accident
  4  no such work item, or it is not accepted (see `--allow-unaccepted`)
  6  the store did not answer
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_ROOT = next(p for p in _HERE.parents if (p / "store" / "__init__.py").exists())
sys.path[:0] = [str(_ROOT), str(_ROOT / "adapter"), str(_ROOT / "engine")]

import store  # noqa: E402
from brain_adapter.receipt import BOOKING_MOMENTS  # noqa: E402

EXIT_OK = 0
EXIT_INCOMPLETE = 3
EXIT_NO_ITEM = 4
EXIT_STORE = 6

SUPPLIED = "supplied"
EMPTY = "empty-column"
NO_COLUMN = "no-column"

#: `Receipt`'s own slug rule, restated so this tool can say "that id is not a slug" instead
#: of finding out from a refusal three steps later.
_SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _field(name, arg, value, verdict, source, why=""):
    return {"field": name, "cli_arg": arg, "value": value, "verdict": verdict,
            "source": source, "why": why}


def map_row(row: dict, artifacts: list[str]) -> list[dict]:
    """The mapping. One dict per field, and every verdict carries the reason for it.

    Nothing here guesses. A field with no honest source gets NO_COLUMN and a `value` of
    None, and the caller renders that as a refusal token rather than as an omission -- an
    omitted `--moment` is a usage error a reader blames on the command line, and the point
    is that it is not one.
    """
    out = []

    # ---- date. The one real choice, and it is between two timestamps, not a guess.
    #
    # `finished_at` is when the agent said done; `accepted_at` is when a human agreed. A
    # receipt booked AT acceptance is dated by the acceptance, so that is what this uses --
    # and it says so, because the other reading (date the work, not the verdict) is
    # defensible and the difference shows up in a filename that is append-only afterwards.
    #
    # UTC, stated rather than assumed: the column is timestamptz and the store answers in
    # UTC, so a receipt accepted at 23:40 New York lands on the following day's filename.
    accepted_at = row.get("accepted_at")
    out.append(_field(
        "date", "--date",
        accepted_at.date().isoformat() if accepted_at else None,
        SUPPLIED if accepted_at else EMPTY,
        "brain.work_item.accepted_at",
        "the acceptance timestamp, rendered in UTC. `accept work` is its only writer, so on "
        "an accepted row it is never null. Dating by `finished_at` instead is a defensible "
        "reading -- date the work, not the verdict -- and the two differ across midnight."))

    # ---- slug. Mechanical, and the collision is the part worth saying out loud.
    #
    # Work item ids are zero-padded decimals, so they already satisfy the slug rule. What
    # they do not do is stay unique across receipts: WAGER-13a makes receipts append-only,
    # a status change books a NEW one, and `Receipt.validate` refuses RECEIPT_EXISTS on a
    # colliding path. Two receipts for one item on one day is the normal case, not an edge
    # one, so the slug carries the moment -- and since the moment is refused below, so is
    # the slug's suffix. They fail together on purpose.
    item_id = row["id"]
    slug_ok = bool(_SLUG_OK.match(item_id))
    out.append(_field(
        "slug", "--slug",
        f"{item_id}-<<REFUSED: moment>>" if slug_ok else None,
        NO_COLUMN if slug_ok else EMPTY,
        "brain.work_item.id + the booking moment",
        f"{item_id!r} is already a legal slug, so the id half is free. The moment half is "
        f"not decoration: the path is departments/<dept>/receipts/<date>-<slug>.md, "
        f"receipts are append-only (WAGER-13a) and a second receipt for this item on this "
        f"date refuses RECEIPT_EXISTS without it."))

    # ---- title. NOT NULL, and it is the work item's own words.
    out.append(_field(
        "title", "--title", row["title"], SUPPLIED if row["title"] else EMPTY,
        "brain.work_item.title", "NOT NULL on the table."))

    # ---- summary. Present, and it is a SELF-REPORT, which is a caveat and not a defect.
    #
    # `done --summary` lands in `result` (engine/swarm_engine/transitions.py, `_finish`).
    # That is the agent's account of its own work. D00 rule 3 makes `done` the self-report
    # and acceptance the separate human act, so a receipt whose `summary` is `result`
    # verbatim records what the AGENT claimed, over a lineage that records that a human
    # agreed. That is coherent -- it is what `approval_ref` is for -- but it is worth
    # knowing which of the two the sentence in the receipt came from.
    result = (row.get("result") or "").strip()
    out.append(_field(
        "summary", "--summary", result.splitlines()[0] if result else None,
        SUPPLIED if result else EMPTY,
        "brain.work_item.result",
        "the agent's `done --summary`, verbatim. It is a self-report; the human's half of "
        "the record is `approval_ref` below. First line only: `summary` is a YAML scalar "
        "and `_yaml_scalar` flattens newlines anyway."))

    # ---- moment. The refusal that matters.
    flagged = bool(row.get("external") or row.get("canon_touching"))
    out.append(_field(
        "moment", "--moment", None, NO_COLUMN,
        "nothing on brain.work_item",
        f"WAGER-14a's four moments are {sorted(BOOKING_MOMENTS)} and ACCEPTANCE IS NOT ONE "
        f"OF THEM. This row is external={row.get('external')} "
        f"canon_touching={row.get('canon_touching')}: "
        + ("either flag being true makes `consequential-event-emitted` the only moment "
           "`Receipt.validate` will accept for a consequential booking, so the row narrows "
           "it to one -- but the row still does not say that an acceptance IS that moment."
           if flagged else
           "neither flag is set, so `consequential-event-emitted` is REFUSED outright "
           "(NOT_CONSEQUENTIAL) and the remaining three are a doctrine choice this row "
           "cannot make.")))

    # ---- department. No derivation exists.
    out.append(_field(
        "department", "--department", None, NO_COLUMN,
        f"nothing on brain.work_item (it has lane={row.get('lane')!r})",
        "there is no lane-to-department map in this repository, and `Receipt.validate` "
        "refuses NO_RECEIPT_HOME unless departments/<department>/receipts/ already exists "
        "in the brain. A department declares its own receipt home; the runtime does not "
        "create one, so this must name a real directory rather than a lane."))

    # ---- lineage, field by field. Nine of them, and they do not share a verdict.
    lin = [
        ("produced_by", "--produced-by", row.get("produced_by"),
         "brain.work_item.produced_by",
         "when this is null and `resolution_status` says 'resolved', `promote()` refuses "
         "LINEAGE_INCOHERENT. Booking anyway means `--allow-unresolved`, which books "
         "produced_by=null with the raw reference recorded visibly -- honest, and it is "
         "what every booking off a work item does today."),
        ("produced_by_ref", "(via --produced-by)", row.get("produced_by_ref"),
         "brain.work_item.produced_by_ref", "the raw reference kept beside a null id."),
        ("resolution_status", "(via --produced-by)", row.get("resolution_status"),
         "brain.work_item.resolution_status", "'resolved' or the reason it is not."),
        ("caused_by_event_id", "--caused-by-event-id", None,
         "no column on brain.work_item",
         "legal as an explicit null: Lineage's contract requires the KEY to be present so "
         "that 'flagged event, no approval' is detectable by join. A work item is not a "
         "fabric event and has no event id to name."),
        ("approval_ref", "--approval-ref",
         (f"work-item:{item_id} accepted_by={row.get('accepted_by')} "
          f"accepted_at={accepted_at.isoformat() if accepted_at else None}")
         if row.get("accepted_by") else None,
         "brain.work_item.accepted_by + accepted_at",
         "THE FIELD ACCEPTANCE ADDS. `accept work` is the only writer of this pair and it "
         "refuses a fleet agent as acceptor, so it records that a HUMAN decided -- which "
         "is the field `promote()` refuses a canon-touching promotion without. The string "
         "form here is a PROPOSAL: the shape of record is the brain's "
         "`entities/rules/result-and-escalation-contract.md`, not this file."),
        ("actor_type", "--actor-type", row.get("actor_type"),
         "brain.work_item.actor_type", "human | ai | hybrid."),
        ("session_id", "--session-id", row.get("session_id") or None,
         "brain.work_item.session_id", "NOT NULL with default '', so empty is the norm."),
        ("work_item_id", "--work-item-id", item_id,
         "brain.work_item.id", "always available; it is the row's own key."),
        ("canonical_task", "--canonical-task", row.get("canonical_task"),
         "brain.work_item.canonical_task", "nullable."),
    ]
    for name, arg, value, source, why in lin:
        verdict = SUPPLIED if value else (NO_COLUMN if "no column" in source else EMPTY)
        out.append(_field(f"lineage.{name}", arg, value, verdict, source, why))

    # ---- the flags and the artifacts. These are the easy half and they are complete.
    out.append(_field("external", "--external", bool(row.get("external")), SUPPLIED,
                      "brain.work_item.external", "NOT NULL boolean."))
    out.append(_field("canon_touching", "--canon-touching", bool(row.get("canon_touching")),
                      SUPPLIED, "brain.work_item.canon_touching", "NOT NULL boolean."))
    out.append(_field("artifacts", "--artifact", artifacts,
                      SUPPLIED if artifacts else EMPTY,
                      "brain.artifact rows for this work item",
                      "repeatable. The fleet records these as it goes, so this is populated "
                      "exactly when the agent did that."))
    return out


def render_argv(fields: list[dict]) -> list[str]:
    """The command a human would run, with every gap left as a token that cannot be pasted.

    `<<REFUSED: ...>>` rather than an omitted flag, because an omitted required flag is an
    argparse usage error and a reader blames the command line for it. The refusal has to
    survive into the text, or the finding is lost at exactly the moment somebody acts on it.
    """
    argv = ["python3", "adapter/bin/brain-adapter", "receipt", "book"]
    by_field = {f["field"]: f for f in fields}

    def token(name):
        f = by_field[name]
        if f["verdict"] == SUPPLIED:
            return str(f["value"])
        if f["value"] is not None:            # partially derivable, e.g. the slug
            return str(f["value"])
        return f"<<REFUSED: {name} is {f['verdict']}>>"

    for name in ("department", "date", "slug", "title", "summary", "moment"):
        argv += [by_field[name]["cli_arg"], token(name)]

    for name in ("lineage.produced_by", "lineage.approval_ref", "lineage.actor_type",
                 "lineage.session_id", "lineage.work_item_id", "lineage.canonical_task"):
        f = by_field[name]
        if f["verdict"] == SUPPLIED:
            argv += [f["cli_arg"], str(f["value"])]
    if by_field["lineage.produced_by"]["verdict"] != SUPPLIED:
        argv += ["--allow-unresolved"]
    if by_field["external"]["value"]:
        argv += ["--external"]
    if by_field["canon_touching"]["value"]:
        argv += ["--canon-touching"]
    for path in by_field["artifacts"]["value"] or []:
        argv += ["--artifact", path]
    return argv


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="receipt_args_from_work_item",
        description="what an accepted work item can hand `receipt book`. Books nothing.")
    ap.add_argument("work_item", help="the work item id, e.g. 0291")
    ap.add_argument("--json", action="store_true", help="machine-readable, for a caller")
    ap.add_argument("--allow-unaccepted", action="store_true",
                    help="map a work item that has not been accepted. The point of the "
                         "acceptance is that it is what supplies approval_ref, so this "
                         "will report that field EMPTY; it exists for measuring the gap")
    args = ap.parse_args(argv)

    try:
        with store.read() as s:
            row = s.one("SELECT * FROM brain.work_item WHERE id = %s", (args.work_item,))
            arts = ([r["path"] for r in s.query(
                "SELECT DISTINCT path FROM brain.artifact WHERE work_item_id = %s ORDER BY path",
                (args.work_item,))] if row else [])
    except Exception as e:                                            # noqa: BLE001
        print(f"the store did not answer ({e.__class__.__name__}): {e}", file=sys.stderr)
        return EXIT_STORE

    if not row:
        print(f"no such work item: {args.work_item}", file=sys.stderr)
        return EXIT_NO_ITEM
    if row.get("accepted_at") is None and not args.allow_unaccepted:
        print(f"{args.work_item} is not accepted (state={row['state']!r}, accepted_at is "
              f"NULL). Acceptance is what supplies `approval_ref`, which is the one lineage "
              f"field a canon-touching promotion cannot be made without. Pass "
              f"--allow-unaccepted to map it anyway and see that field come back empty.",
              file=sys.stderr)
        return EXIT_NO_ITEM

    fields = map_row(dict(row), arts)
    command = render_argv(fields)
    gaps = [f["field"] for f in fields if f["verdict"] != SUPPLIED]
    payload = {"work_item": args.work_item, "accepted_by": row.get("accepted_by"),
               "fields": fields, "argv": command, "gaps": gaps,
               "complete": not gaps, "booked": False}

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(f"work item {args.work_item}  accepted_by={row.get('accepted_by')!r}  "
              f"accepted_at={row.get('accepted_at')}")
        print()
        width = max(len(f["field"]) for f in fields)
        for f in fields:
            print(f"  {f['field']:<{width}}  {f['verdict']:<12}  {f['source']}")
            if f["verdict"] != SUPPLIED:
                print(f"  {'':<{width}}  ^^ {f['why']}")
        print()
        print("the command, with every gap left as a token that cannot be pasted:")
        print()
        print("  " + " \\\n    ".join(_pairs(command)))
        print()
        print(f"{len(gaps)} of {len(fields)} fields are not supplied: "
              f"{', '.join(gaps) if gaps else 'none'}")
        print("NOTHING WAS BOOKED. Booking is a git commit into the operator's brain "
              "through the promotion door, and whether an agent may make it is his call.")
    return EXIT_OK if not gaps else EXIT_INCOMPLETE


def _pairs(argv: list[str]) -> list[str]:
    """Fold the argv into `--flag value` lines so a human can read the gaps in it."""
    lines, i = [], 0
    while i < len(argv):
        if argv[i].startswith("--") and i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            lines.append(f"{argv[i]} {argv[i + 1]!r}")
            i += 2
        else:
            lines.append(argv[i])
            i += 1
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
