"""`queue` -- the human queue's CLI. A thin wrapper over the verbs, per the narrow waist.

The console and an MCP wrapper will be equally thin over the same verbs, and none of them can
invent a transition. Where this file and `transitions.py` disagree, the transition wins, because
the transition is what actually runs.

NO BUTTON HERE IS LABELLED "DONE", and that is not a UI note that stopped at the UI: every
command is named for the state change it makes. A label that names its transition cannot quietly
generalise across rooms.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import store

from . import checkpoints, reads, tiers
from .defaults import check_default
from .transitions import QueueError

OK, ERR, EMPTY = 0, 1, 2      # EMPTY is the repo-wide zero-denominator exit; see engine/swarm_engine/cli.py:44


def die(msg, code=ERR):
    print(f"queue: {msg}", file=sys.stderr)
    return code


def out_json(obj):
    print(json.dumps(obj, indent=2, default=str))
    return OK


def parse_ref(raw: str) -> tuple:
    """`0088` is a work item, `q0044` a question, `r12` a recommendation. One rule, stated once."""
    s = str(raw).strip().lstrip("#")
    if s.startswith("q"):
        return "question", "q" + s[1:].zfill(4)
    if s.startswith("r"):
        return "recommendation", s[1:]
    return "work_item", s.zfill(4)


def fmt_ref(source_type, source_id) -> str:
    return f"r{source_id}" if source_type == "recommendation" else str(source_id)


# ------------------------------------------------------------------ reading

def cmd_list(args):
    q = reads.queue(tier=args.tier, window=args.window)
    if args.json:
        return out_json(q)
    t = q["totals"]
    print(f"HUMAN QUEUE  {q['now']:%Y-%m-%dT%H:%M:%SZ}   "
          f"decide {t['decide']} · judge {t['judge']} · shape {t['shape']}   "
          f"({t['blocked']} blocked, {t['deferred']} deferred, not shown)")
    for name, block in q["tiers"].items():
        print(f"\n{name.upper()}  {block['total']}  ({block['act']})")
        if not block["items"]:
            # Queue zero does not celebrate. Calm, and a statement of what the fleet is doing.
            print("  nothing needs you here.")
            continue
        for i in block["items"]:
            gates = (" " + " ".join(i["gates"])) if i["gates"] else ""
            idle = f"  ⏸ {i['idle_agent']} waiting" if i.get("idle_agent") else ""
            mark = " *oldest, reserved slot" if i.get("reserved_oldest") else ""
            print(f"  {i['rank']:>2}. {fmt_ref(i['source_type'], i['source_id']):<8} "
                  f"{str(i['title'])[:64]:<64} {i['score']:>6}{gates}{idle}{mark}")
            if i.get("default_text"):
                print(f"      silence ships: {i['default_text']!r} "
                      f"(null branch: {i['default_null_branch']})")
        if block["hidden"]:
            print(f"  ... {block['hidden']} more, not shown. This is a window, not a backlog.")
    if q["cycles"]:
        print(f"\n  {len(q['cycles'])} dependency cycle(s) found. Run `queue doctor`.")
    return OK


def cmd_why(args):
    st, sid = parse_ref(args.id)
    w = reads.why(st, sid)
    if args.json:
        return out_json(w)
    if not w["found"]:
        return die(f"{args.id} is not in the human queue")
    terms = ", ".join(f"{k} {v:+g}" for k, v in w["terms"].items() if v)
    where = f"#{w['rank']} in {w['tier']}" if w.get("rank") else f"in {w.get('tier', '?')}"
    print(f"{fmt_ref(st, sid)} is {where}"
          + (f" of {w['of']}" if w.get("of") else "") + ".")
    print(f"  tier {w['tier']}: {w['tier_reason']}")
    print(f"  score {w['score']} = {terms or '0'}"
          + ("   [no effort term: effort is the agent's token cost, not yours]"))
    if w["band"] < 2:
        print(f"  HARD RULE: jumps its tier because {w['band_reason']}")
    if w["unblock_detail"]:
        print(f"  U = {w['unblock_weight']}, from {len(w['unblock_detail'])} item(s) it blocks:")
        for d in w["unblock_detail"][:6]:
            print(f"     {d['id']}: band {d['band_value']} / {d['blockers']} blocker(s)"
                  f" + 0.5*{d['downstream_u']} downstream = {d['contribution']:+g}")
    else:
        print(f"  U = {w['unblock_weight']}: it blocks nothing that is still open")
    for b in w["bump_detail"]:
        print(f"  bump {b['delta']:+g} by {b['by']} {b['age_hours']}h ago, now worth "
              f"{b['now_worth']:+g} (half life {b['half_life_hours']:g}h): {b['reason']}")
    if w.get("declared_vs_computed"):
        print(f"  calibration: {w['declared_vs_computed']}")
    if w.get("gates"):
        print(f"  GATE {' + '.join(w['gates'])}: surfaces to you whatever its other signals say")
    return OK


def cmd_depth(args):
    d = reads.depth_and_clearance(days=args.days, who=args.who)
    if args.json:
        return out_json(d)
    print(f"depth: {d['depth']['open']} open "
          f"(decide {d['depth']['decide']}, judge {d['depth']['judge']}, "
          f"shape {d['depth']['shape']}), {d['depth']['blocked']} blocked, "
          f"{d['depth']['deferred']} deferred")
    if not d["measured"]:
        print(f"clearance: nothing cleared in {d['days_measured']}d, so the ETA is UNDEFINED, "
              f"not zero. An ETA from an assumed rate would be a confident number from no "
              f"measurement.")
    else:
        print(f"clearance: {d['cleared']} in {d['days_measured']}d = {d['per_day']}/day "
              f"(item throughput, MEASURED)")
        for k, v in d["eta_days"].items():
            print(f"  eta {k}: {v} days")
        print(f"  basis: {d['eta_days_basis']}")
        if d["triage_mode"]:
            print("  TRIAGE MODE: depth is past 7x daily clearance. Batch-decline candidates "
                  "should be presented as one reviewable list.")

    # THE SECOND CURRENCY, PRINTED UNDER ITS OWN HEADING AND NEVER MIXED INTO THE FIRST. Items
    # per day and hours of the operator's life are different units answering different questions,
    # and a surface that interleaved them would invite exactly the arithmetic nobody should do.
    sw = d["stopwatch"]
    e = sw["entries"]
    print(f"\noperator time ({sw['who']}): {e['measured']} measured entr(ies), "
          f"{e['abandoned']} abandoned, {e['superseded']} superseded, {e['running']} running")
    print(f"  coverage: {sw['coverage']['text']}"
          + (f"  ({sw['coverage']['rate']:.0%})" if sw["coverage"]["rate"] is not None else ""))
    if not sw["overall"]["n"]:
        print(f"  {sw['basis']}")
        return OK
    print(f"  measured: {sw['overall']['n']} timed item(s), median "
          f"{sw['overall']['median_minutes']}min, mean {sw['overall']['mean_minutes']}min, "
          f"{sw['overall']['total_minutes']}min total")
    for t in tiers.TIERS:
        st = sw["per_item"][t]
        if st["n"]:
            print(f"  eta {t}: {sw['eta_hours'][t]} hours  "
                  f"(depth {d['depth'][t]} x median {st['median_minutes']}min over {st['n']} "
                  f"measured item(s))")
    for u in sw["unmeasured_tiers"]:
        # NOT a zero and NOT another tier's mean. The gap is the finding.
        print(f"  eta {u['tier']}: UNMEASURED -- {u['why']}, and its {u['depth']} item(s) are "
              f"therefore NOT in the hours figure below.")
    if sw["eta_hours_measured_tiers_only"] is not None:
        print(f"  = {sw['eta_hours_measured_tiers_only']} hours of your time, over the "
              f"{len(tiers.TIERS) - len(sw['unmeasured_tiers'])} tier(s) that have a measurement.")
    if sw["untiered_items"]:
        print(f"  {sw['untiered_items']} timed item(s) were not in a live tier when the "
              f"stopwatch started; they count in the overall figure and in no tier's.")
    if sw["mixed_tier_items"]:
        print(f"  {sw['mixed_tier_items']} timed item(s) span two tiers (demoted mid-item); each "
              f"is counted once, in the tier it started in.")
    print(f"  basis: {sw['basis']}")
    return OK


def cmd_ceilings(args):
    c = reads.tier_ceilings(days=args.days, who=args.who)
    if args.json:
        return out_json(c)
    print(f"tier ceilings, measured over {c['days_measured']}d from {c['items_measured']} timed "
          f"item(s) ({c['who']})")
    for t in tiers.TIERS:
        b = c["tiers"][t]
        claimed = f"{b['claimed_seconds'] / 60:.0f}min" if b["claimed_seconds"] else "unclaimed"
        print(f"  {t:<7} claimed {claimed:<10} {b['verdict']}")
    return OK


# ------------------------------------------------------------------ the operator's stopwatch

def cmd_time_start(args):
    st, sid = parse_ref(args.id)
    r = store.apply("queue time start", source_type=st, source_id=sid, who=args.who,
                    note=args.note or "")
    print(f"timer {r['id']} running on {args.id} "
          f"({r['tier_at_start'] or 'not in a live tier'}), started "
          f"{r['started_at']:%H:%M:%S}. Past {r['cap_seconds'] / 3600:.0f}h it is recorded as "
          f"abandoned and left out of every mean.")
    return OK


def cmd_time_stop(args):
    r = store.apply("queue time stop", who=args.who, abandon=args.abandon, note=args.note or "")
    print(f"timer {r['id']} {r['ended_how']} on {fmt_ref(r['source_type'], r['source_id'])}: "
          f"{r['minutes']:.2f} minutes"
          + (f" in {r['tier_at_start']}" if r["tier_at_start"] else ""))
    if r["capped"]:
        # SAID, NOT SWALLOWED. The database relabelled this row and a surface that printed
        # `stopped` over it would be reporting the write it attempted rather than the one that
        # landed.
        print(f"  PAST THE CAP ({r['cap_seconds'] / 3600:.0f}h), so it is recorded as ABANDONED "
              f"and counts in no mean. A forgotten timer must not be able to poison the "
              f"clearance figure, and clamping it to the cap would invent a measurement rather "
              f"than lose one. If you know what the interval really was, "
              f"`queue time correct {r['id']}`.")
    elif not r["measured"]:
        print("  recorded as abandoned at your request: in the ledger, out of every mean.")
    return OK


def cmd_time_status(args):
    s = reads.time_status(who=args.who, days=args.days)
    if args.json:
        return out_json(s)
    if s["running"]:
        run = s["running"]
        print(f"RUNNING  timer {run['id']} on "
              f"{fmt_ref(run['source_type'], run['source_id'])} "
              f"({run['tier_at_start'] or 'not in a live tier'}) -- "
              f"{run['elapsed_minutes']:.1f} minutes since {run['started_at']:%H:%M:%S}")
        if run["warning"]:
            print(f"  OVER CAP: {run['warning']}")
    else:
        print(f"no timer running for {s['who']}. `queue time start <id>` starts one; a timer is "
              f"never required and an untimed item is normal.")
    t = s["today"]
    # `median None min` is what an unguarded f-string prints over an empty set, and a surface
    # that renders a null as a word the operator reads as a number is the small version of the
    # defect this whole ledger is about. No measurement says so.
    print(f"last {s['days']}d: {t['n']} measured entr(ies)"
          + (f", {t['total_minutes']}min total, median {t['median_minutes']}min" if t["n"]
             else ", so there is nothing measured to report a median over")
          + (f", {s['abandoned']} abandoned" if s["abandoned"] else "")
          + (f", {s['superseded']} superseded by a correction" if s["superseded"] else ""))
    for r in s["recent"][:12]:
        mark = ("  [SUPERSEDED]" if r["superseded"] else
                "  [ABANDONED, in no mean]" if r["ended_how"] == "abandoned" else "")
        print(f"  {r['id']:>4} {fmt_ref(r['source_type'], r['source_id']):<8} "
              f"{(r['tier_at_start'] or '-'):<7} {float(r['seconds']) / 60.0:>7.2f}min  "
              f"{r['started_at']:%m-%d %H:%M}{mark}")
    return OK


def cmd_time_correct(args):
    started = _iso(args.started)
    stopped = _iso(args.stopped)
    r = store.apply("queue time correct", entry_id=args.entry_id, started_at=started,
                    stopped_at=stopped, reason=args.reason, who=args.who or "",
                    note=args.note or "")
    print(f"entry {args.entry_id} superseded by {r['id']}: "
          f"{r['was_seconds'] / 60.0:.2f} -> {r['minutes']:.2f} minutes. Both rows survive; the "
          f"measurement reads the correction and the ledger still shows what was recorded first.")
    if r["capped"]:
        print(f"  the corrected interval is past the cap, so it too is recorded as ABANDONED and "
              f"counts in no mean. Record a genuinely long session as several entries.")
    return OK


def _iso(raw: str):
    d = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def cmd_acted_on(args):
    """The falsifier, with what it compared on the same line as the verdict. Task 0382.

    This used to print `BELOW THE FALSIFIER: cut this layer back` over 3 raised and 0 decided,
    which is a verdict derived from zero comparisons. See `reads.acted_on` for the full account.
    Nothing about the 30 percent bar changed and no line was suppressed; what was added is the
    denominator, printed, and a refusal to render a verdict over an empty one.
    """
    a = reads.acted_on()
    if args.json:
        return out_json(a)
    print(f"recommendations raised {a['raised']}, accepted {a['accepted']}, "
          f"decided {a['decided']}, still open {a['still_open']}")
    if not a["measurable"]:
        # THE ZERO IS PRINTED AND THE VERDICT IS NOT. Rule 2: zero compared is a hard failure,
        # never a pass, and here "a pass" would have meant a confident instruction to delete a
        # layer of this system.
        print(f"acted-on rate: NOT MEASURABLE   "
              f"(compared {a['decided']} decision(s) out of {a['raised']} raised; "
              f"falsifier bar unchanged at {a['falsifier_threshold']:.0%})")
        print(f"  {a['verdict']}")
        for r in a["per_template"]:
            print(f"  {r['template_id']:<32} raised {r['raised']:>3}  accepted {r['accepted']:>3}  "
                  f"decided {int(r['accepted'] or 0) + int(r['rejected'] or 0):>3}  "
                  f"rate NOT MEASURABLE")
        return EMPTY
    print(f"acted-on rate: {a['acted_on_rate']}  "
          f"(compared {a['accepted']} accepted of {a['raised']} raised; falsifier: below "
          f"{a['falsifier_threshold']:.0%} this layer is cut back to events plus paging)")
    # The second reading, printed beside the first rather than instead of it. They diverge as
    # soon as the queue is deep, and which one the falsifier means is an open question on the
    # bus, not something this line decides. See `reads.acted_on`.
    print(f"  over DECIDED only: {a['decided_rate']}  "
          f"({a['accepted']} accepted of {a['decided']} decided, {a['still_open']} still open "
          f"and not counted either way)")
    print(f"  {a['verdict']}")
    for r in a["per_template"]:
        print(f"  {r['template_id']:<32} raised {r['raised']:>3}  accepted {r['accepted']:>3}  "
              f"rate {r['acted_on_rate']}")
    return OK


def cmd_overrides(args):
    o = reads.override_after_default()
    if args.json:
        return out_json(o)
    print(f"defaults fired: {o['fired']}")
    print(f"  {o['claim']}")
    for r in o["per_producer"]:
        print(f"  {r['producer']:<20} fired {r['defaults_fired']:>3}  "
              f"overridden {r['overridden']:>3}  rate {r['override_rate']}")
    return OK


def cmd_defaults(args):
    with store.read("runtime") as s:
        # `_residue` rather than the base view: same rows, plus the act verbs each default names,
        # the words the gate does not recognise, and the verdict of the classifier that actually
        # governs a flagged row. Each migration adds its own view over the last rather than
        # reshaping it, because `CREATE OR REPLACE VIEW` cannot drop a column and the second
        # apply of the schema is where that fails.
        rows = s.query("SELECT * FROM brain.queue_pending_default_residue ORDER BY asked_at")
    if args.json:
        return out_json(rows)
    b = checkpoints.checkpoint_bounds()
    gated = [r for r in rows if r["gated"]]
    print(f"{len(rows)} pending default(s). Next checkpoint fires at 07:00 or 19:00 UTC "
          f"(last mark {b['current']:%H:%M}).")
    for r in rows:
        flag = " ".join(n for n, v in (("EXTERNAL", r["external"]),
                                       ("CANON", r["canon_touching"])) if v)
        print(f"  {r['question_id']:<8} {flag:<16} {str(r['default_text'])[:70]!r}")
    if gated:
        # `null_branch_gated`, not `null_branch`: these rows are gated by definition and since
        # migration 0011 the gated classifier is the rule that governs them.
        bad = [r for r in gated if not r["null_branch_gated"]]
        print(f"\n  {len(gated)} on flagged tasks. Null-branch: "
              f"{len(gated) - len(bad)}/{len(gated)}.")
        if bad:
            print("  NOT NULL BRANCH -- a flag was raised after the default was written:")
            for r in bad:
                # Name the words. `act_verbs` comes from the independent scan added by migration
                # 0009 and `residue` from the recognised-word check added by 0011, so the operator
                # can check the refusal against the text rather than take the classifier's word
                # for it.
                acts = (", ".join(r["act_verbs"]) if r["act_verbs"]
                        else "unrecognised: " + ", ".join(r["residue"]) if r["residue"]
                        else "opens with " + ", ".join(r["bare_imperatives"])
                        if r["bare_imperatives"]
                        else "no null branch stated")
                print(f"    {r['question_id']}: {r['default_text']!r}  [{acts}]")
        else:
            # WAS "Silence can only ever ship the reversible branch." -- an unqualified claim on
            # the surface an operator reads at a checkpoint. Task 0145 measured six phrasings that
            # the act scan cannot see ("hold; get the cheque to Mick"); migration 0011 closed
            # those by refusing every word it does not recognise, and one measured phrasing
            # survives even that. So the line says what it checked and names the bound in the same
            # breath. The unqualified version is what read clean on 2026-08-16 while an external
            # act had shipped, and "by construction" would repeat that defect in a new place.
            print("  No pending default on a flagged task names an act verb or a word this gate "
                  "does not recognise.")
            print("  Bounded, not proven: `no action; all of it` would still pass (swarm 0204).")
    return OK


def cmd_points(args):
    p = reads.points(ledger=args.ledger)
    if args.json:
        return out_json(p)
    if not p["exists"]:
        return die(p["note"])
    print(f"effectiveness-points (polarity neutral, explicitly not a value target)")
    print(f"  {p['points']} points over {p['events']} events, last {p['last_earned_at'][:10]}")
    for actor, v in sorted(p["by_actor_type"].items()):
        print(f"    {actor:<10} {v['points']:>4} points  {v['events']:>3} events")
    print(f"  {p['history_warning']}")
    return OK


def cmd_doctor(args):
    d = reads.doctor()
    tune = reads.tuning_recommendations()
    if args.json:
        return out_json({**d, "tuning": tune})
    if not d["findings"] and not tune:
        print(f"queue doctor: {len(d['checks'])} checks, no findings.")
        return OK
    for f in d["findings"]:
        print(f"  [{f['severity'].upper():<8}] {f['kind']}: {f['subject']}")
        print(f"             {f['detail']}")
    for t in tune:
        print(f"  [TUNING  ] {t['text']}")
    return ERR if any(f["severity"] in ("critical", "high") for f in d["findings"]) else OK


def cmd_check_default(args):
    v = check_default(args.task, args.text)
    if args.json:
        return out_json(v)
    if v["ok"]:
        print(f"ok: {'null branch' if v['null_branch'] else 'not gated'}"
              + (", gated" if v["gated"] else ""))
        return OK
    return die(v["reason"])


# ------------------------------------------------------------------ writing

def cmd_recommend(args):
    r = store.apply("recommend", text=args.text, rationale=args.rationale or "",
                    subject_type=args.subject_type or "", subject_id=args.subject_id or "",
                    template_id=args.template, proposed_action=args.proposed_action or "",
                    requires_human=not args.no_human, produced_by=args.produced_by,
                    by=args.by)
    print(f"r{r['id']} raised, state open, requires_human={r['requires_human']}. "
          f"It executes nothing until a human accepts it.")
    return OK


def cmd_accept(args):
    _, rid = parse_ref(args.id) if str(args.id).startswith("r") else ("recommendation", args.id)
    # `as_operator=True` is what makes `store.apply` open the OPERATOR login, which is the only
    # session `brain.current_human()` answers for and therefore the only one migration 32 lets
    # accept anything. It is passed here and not left to the verb's signature default because
    # `store/transitions.py::_login_for` reads the kwargs `apply` was called with. On a host with
    # no operator credential this fails closed in `_connect`, which is correct: there, nobody is
    # the operator.
    r = store.apply("recommend accept", id=rid, by=args.by, lane=args.lane or "",
                    workdir=args.workdir or "", note=args.note or "",
                    execute=not args.no_execute, as_operator=True)
    print(f"r{r['id']} accepted by {r['decided_by']}"
          + (f", spawned {r['spawned_work_item']}" if r["spawned_work_item"] else
             ", no work spawned (--no-execute)"))
    return OK


def cmd_reject(args):
    _, rid = parse_ref(args.id) if str(args.id).startswith("r") else ("recommendation", args.id)
    # `as_operator=True` for the same reason `cmd_accept` passes it, and since task 0313 for a
    # reason of its own: a rejection dispatches nothing but it moves `brain.queue_acted_on`, which
    # counts by STATE and groups by TEMPLATE, so it prunes a playbook. Migration 33 refuses the
    # write from any connection `brain.current_human()` does not name. On a host with no operator
    # credential this fails closed in `_connect`, which is correct: there, nobody is the operator.
    r = store.apply("recommend reject", id=rid, by=args.by, reason=args.reason, as_operator=True)
    print(f"r{r['id']} rejected by {r['decided_by']}")
    return OK


def cmd_assign(args):
    """Hand a work item to a named human, or hand it back with --to ''.

    `as_operator=True` for the same reason `cmd_accept` passes it: migration 37 refuses this
    write from any connection `brain.current_human()` does not name, and on a host with no human
    credential it fails closed in `_connect`, which is correct. There, nobody is that human.
    """
    to = None if args.to is None else (args.to.strip() or None)
    r = store.apply("queue assign", id=args.id, to=to, note=args.note or "", as_operator=True)
    if r["assigned_human"]:
        print(f"{r['id']} assigned to {r['assigned_human']} by {r['assigned_by']}. "
              f"This is not a permission: {r['not_a_permission']}.")
    else:
        print(f"{r['id']} unassigned by {r['assigned_by']}; it returns to the default assignee.")
    return OK


def cmd_mine(args):
    """The queue narrowed to one human, with BOTH counts on the line.

    The denominator is the point. `brain.queue_open_for` filters `brain.queue_open`, and an
    unassigned row belongs to the default assignee, so on a store with no assignments the two
    counts are equal for the operator and the narrowed count is 0 for everybody else. Printing
    only the narrowed number would make an empty personal queue and an empty QUEUE look the same.
    """
    who = args.who or store.human_slug()
    rows, total = reads.mine(who)
    print(f"{who}: {len(rows)} of {total} open queue item(s)"
          + ("" if total else "  (nothing is open, so this is not a statement about " + who + ")"))
    for r in rows:
        print(f"  {r['source_type']:14} {r['source_id']:8} {str(r['title'])[:70]}")
    return OK


def cmd_classify(args):
    st, sid = parse_ref(args.id)
    store.apply("queue classify", source_type=st, source_id=sid, item_class=args.item_class,
                template_id=args.template, prepared_context_link=args.context,
                recommended_option=args.option, counterargument=args.counterargument,
                by=args.by)
    w = reads.why(st, sid)
    print(f"{args.id}: tier {w.get('tier', '?')} -- {w.get('tier_reason', '')}")
    return OK


def cmd_type(args):
    """Declare what a row IS and what it is WORTH, in one call.

    A work item only. `parse_ref` is not used: `kind` and `impact` are columns on
    `brain.work_item`, and a question or a recommendation has neither, so accepting `q0044` here
    would take a reference this verb cannot act on and fail one layer down with a worse message.
    """
    r = store.apply("queue type", id=args.id, kind=args.kind, impact=args.impact,
                    agent=args.agent, force=args.force, note=args.note or "")
    if args.json:
        return out_json(r)
    was = r["was"]
    print(f"{r['id']}: kind {was['kind']!r} -> {r['kind']!r}, "
          f"impact {was['impact']!r} -> {r['impact']!r}")
    # WHERE THE ROW NOW IS, because that is the only reason to type it and a caller who has to run
    # a second command to find out is a caller who mostly will not.
    if r["in_decisions_queue"]:
        print("  it is now in the DECISIONS queue. `queue queues` lists it.")
    else:
        print(f"  it reads as {r['reads_as']!r}"
              + (" (unclassified reads as work)" if r["kind"] is None else "") + ".")
    if r["forced"]:
        print("  FORCED past a refusal. The override is on the row's thread.")
    return OK


def cmd_demote(args):
    st, sid = parse_ref(args.id)
    r = store.apply("queue demote", source_type=st, source_id=sid, by=args.by,
                    note=args.note or "")
    print(f"{args.id}: {r['was']} -> {r['tier']}. Calibration miss logged against "
          f"{r['producer'] or 'an unnamed producer'}.")
    return OK


def cmd_bump(args):
    st, sid = parse_ref(args.id)
    w = reads.why(st, sid)
    r = store.apply("queue bump", source_type=st, source_id=sid, delta=args.delta,
                    reason=args.reason, by=args.by, half_life_hours=args.half_life,
                    model_score=w.get("score"), model_rank=w.get("rank"),
                    model_tier=w.get("tier"), item_class=w.get("item_class"))
    print(f"{args.id} bumped {r['delta']:+g}, half life {r['half_life_hours']:g}h. Logged as a "
          f"disagreement with the model at score {w.get('score')}, rank {w.get('rank')}.")
    return OK


def cmd_defer(args):
    st, sid = parse_ref(args.id)
    wake_at = None
    if args.until:
        wake_at = datetime.fromisoformat(args.until.replace("Z", "+00:00"))
        if wake_at.tzinfo is None:
            wake_at = wake_at.replace(tzinfo=timezone.utc)
    r = store.apply("queue defer", source_type=st, source_id=sid, kind=args.kind,
                    wake_at=wake_at, wake_event_type=args.event, question=args.question,
                    lane=args.lane or "", workdir=args.workdir or "", reason=args.reason or "",
                    label=args.label or "", by=args.by,
                    acknowledge_default=args.acknowledge_default)
    print(f"{args.id} deferred ({r['kind']}), defer {r['n']}"
          + (f", agent task {r['wake_task']} posted to make it decidable" if r["wake_task"] else "")
          + (f"\n  {r['warning']}" if r.get("warning") else ""))
    return OK


def cmd_wake(args):
    r = store.apply("queue wake", defer_id=args.defer_id, by=args.by, note=args.note or "")
    print(f"defer {r['id']} woke ({r['kind']}) on {r['source_id']}")
    return OK


def cmd_extend(args):
    until = datetime.fromisoformat(args.until.replace("Z", "+00:00"))
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    store.apply("queue extend window", qid=parse_ref(args.id)[1], until=until, by=args.by,
                reason=args.reason or "")
    print(f"{args.id}: silence window extended to {until:%Y-%m-%dT%H:%MZ}. The default still fires.")
    return OK


def cmd_checkpoint(args):
    r = checkpoints.fire_due(dry_run=not args.fire)
    if args.json:
        return out_json(r)
    print(f"checkpoint {r['checkpoint']}: {r['eligible']} default(s) eligible"
          + ("  [DRY RUN -- pass --fire to decide them]" if r["dry_run"] else ""))
    for f in r["fired"]:
        print(f"  {f.get('question_id')}: {str(f.get('default_text'))[:70]!r}")
    for f in r["refused"]:
        print(f"  REFUSED {f['question_id']}: {f['reason']}")
    return OK


def cmd_queues(args):
    """HIS ASK 5: five queues, not one. `queue list` is the WORK queue ranked into three tiers;
    this is the five KINDS of thing waiting on him, which is a different question.

    THE COUNTS DO NOT SUM TO THE BOARD AND THE HEADER SAYS SO. A row that is done-and-unaccepted
    and also blocks something is in `review` and in `dependencies`: one row, two questions. A total
    would invite him to read it as an obligation count, which it is not, so the word is `across`.
    """
    q = reads.queues(limit=args.limit)
    if args.json:
        return out_json(q)
    print(f"HIS FIVE QUEUES  {q['now']:%Y-%m-%dT%H:%M:%SZ}   "
          f"{q['across']} across five (a row can be in two, so this is not a board count)")
    for name in reads.QUEUES:
        block = q["queues"][name]
        if not block["available"]:
            # UNAVAILABLE IS NOT EMPTY. Zero would say "nothing needs deciding" about a column that
            # does not exist, which is the fabricated number this whole lane refuses.
            print(f"\n{name.upper():<13} UNAVAILABLE")
            print(f"  {block['why']}")
            continue
        print(f"\n{name.upper():<13} {block['n']}")
        if not block["items"]:
            # AN EMPTY LIST AND AN EMPTY QUEUE ARE DIFFERENT ANSWERS, and printing one over the
            # other was a real defect in the first version of this command: on a store below ledger
            # 52 the counts are read but the rows are not, so `WORK 48` printed `nothing needs you
            # here` underneath it. That is a fabricated reassurance, which is the one thing this
            # lane refuses everywhere else.
            if block["n"]:
                print(f"  {block['n']} waiting. This store is below ledger 52, so the counts are "
                      f"read and the rows are not. Apply migration 52 to list them.")
            else:
                print("  nothing needs you here.")
            continue
        for i in block["items"][:args.limit]:
            when = f"{i['since']:%m-%d %H:%MZ}" if i.get("since") else "".ljust(11)
            print(f"  {fmt_ref(i['source_type'], i['source_id']):<8} {when:<12} "
                  f"{str(i['title'])[:60]}")
        if block["n"] > len(block["items"]):
            print(f"  ... {block['n'] - len(block['items'])} more. This is a window, not a backlog.")
    return OK


def build_parser():
    p = argparse.ArgumentParser(prog="queue", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, **kw):
        s = sub.add_parser(name, **kw)
        s.set_defaults(fn=fn)
        s.add_argument("--json", action="store_true")
        return s

    s = add("list", cmd_list, help="the ranked queue, a window per tier")
    s.add_argument("--tier", choices=tiers.TIERS)
    s.add_argument("--window", type=int, default=reads.DEFAULT_WINDOW)

    s = add("queues", cmd_queues,
            help="his FIVE queues: questions, review, dependencies, work, decisions (ask 5)")
    s.add_argument("--limit", type=int, default=10)

    s = add("why", cmd_why, help="the additive decomposition for one item")
    s.add_argument("id")

    s = add("depth", cmd_depth, help="depth, measured clearance, and the ETA that divides them")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--who", default="operator", help="whose stopwatch the hours half reads")

    s = add("ceilings", cmd_ceilings,
            help="is Decide really a two-minute lane? measured, with its coverage")
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--who", default="operator")

    # `queue time ...` -- the operator's stopwatch. A nested group rather than four top-level
    # commands, because `start` and `stop` on their own would read as verbs about the QUEUE
    # ("start what?"), and this CLI's rule is that every command is named for the state change it
    # makes.
    tp = sub.add_parser("time", help="the operator's stopwatch: start, stop, status, correct")
    tsub = tp.add_subparsers(dest="time_cmd", required=True)

    def addt(name, fn, **kw):
        t = tsub.add_parser(name, **kw)
        t.set_defaults(fn=fn)
        t.add_argument("--json", action="store_true")
        t.add_argument("--who", default="operator")
        return t

    t = addt("start", cmd_time_start, help="start the stopwatch on one item. Refuses a second")
    t.add_argument("id")
    t.add_argument("--note", default="")

    t = addt("stop", cmd_time_stop, help="stop the running timer. The one update this ledger takes")
    t.add_argument("--abandon", action="store_true",
                   help="say out loud that this was not time spent on the item. Same label the "
                        "cap forces, same exclusion from every mean.")
    t.add_argument("--note", default="")

    t = addt("status", cmd_time_status, help="what is running, and what was measured recently")
    t.add_argument("--days", type=int, default=1)

    t = addt("correct", cmd_time_correct,
             help="supersede a stopped entry with a new one. It is never edited")
    t.add_argument("entry_id", type=int)
    t.add_argument("--started", required=True, help="ISO time the interval really began")
    t.add_argument("--stopped", required=True, help="ISO time it really ended")
    t.add_argument("--reason", required=True)
    t.add_argument("--note", default="")
    # Empty, not `operator`: a correction inherits WHOSE minutes these were from the row it
    # corrects. Defaulting to `operator` here would silently reassign a second person's entry to
    # him -- and `who` is the key the one-running-timer rule and every figure are computed per.
    t.set_defaults(who="")

    add("acted-on", cmd_acted_on, help="the acted-on rate per template: this layer's falsifier")
    add("overrides", cmd_overrides, help="override-after-default rate, per producer")
    add("defaults", cmd_defaults, help="what silence will ship, and whether it is a null branch")
    add("doctor", cmd_doctor, help="dead wake conditions, cycles, and defaults gone act-shaped")

    s = add("points", cmd_points, help="effectiveness-points, read only, sliced by actor")
    s.add_argument("--ledger")

    s = add("check-default", cmd_check_default, help="would this default be refused?")
    s.add_argument("task")
    s.add_argument("text")

    s = add("recommend", cmd_recommend, help="raise a recommendation (it executes nothing)")
    s.add_argument("text")
    s.add_argument("--rationale")
    s.add_argument("--subject-type", default="")
    s.add_argument("--subject-id", default="")
    s.add_argument("--template")
    s.add_argument("--proposed-action")
    s.add_argument("--produced-by")
    s.add_argument("--by", default="")
    s.add_argument("--no-human", action="store_true",
                   help="declare requires_human=false. It changes nothing: acceptance still "
                        "needs a human. The claim is recorded and reported by `queue doctor`.")

    s = add("accept", cmd_accept, help="accept a recommendation. The ONLY path to executed work")
    s.add_argument("id")
    s.add_argument("--by", required=True, help="the human who decided. An agent name is refused.")
    s.add_argument("--lane", default="")
    s.add_argument("--workdir", default="",
                   help="where the spawned work runs. Defaults to the SUBJECT TASK'S workdir: "
                        "work executing a recommendation about a row belongs in that row's tree. "
                        "Type one when the work is somewhere else.")
    s.add_argument("--note", default="")
    s.add_argument("--no-execute", action="store_true",
                   help="record the acceptance without spawning the work item")

    s = add("reject", cmd_reject, help="reject a recommendation, with a reason")
    s.add_argument("id")
    # REQUIRED, and it used to default to the literal string `operator`. That default WAS the
    # forgery task 0313 closed: any process that could run this line recorded the operator as
    # having rejected a recommendation he had never seen. Since migration 33 the name has to be
    # the one `brain.current_human()` gives this connection, so a default is either redundant or
    # wrong, and `queue recommend accept` has required it since it was written.
    s.add_argument("--by", required=True,
                   help="the human who decided. An agent name is refused, and so is a name that "
                        "is not the login you are connected as.")
    s.add_argument("--reason", required=True)

    s = add("assign", cmd_assign, help="hand a work item to a named human, or hand it back")
    s.add_argument("id")
    s.add_argument("--to", default=None,
                   help="the human slug, from `swarm admin human list`. Pass an empty string to "
                        "unassign, which returns the row to the default assignee")
    s.add_argument("--note", default="")

    s = add("mine", cmd_mine, help="the open queue narrowed to one human, with both counts")
    s.add_argument("--who", default="",
                   help="a human slug. Defaults to the human THIS PROCESS is, from $BRAIN_HUMAN")

    s = add("classify", cmd_classify, help="set the membrane fields the tier is computed from")
    s.add_argument("id")
    s.add_argument("--item-class")
    s.add_argument("--template")
    s.add_argument("--context", help="prepared_context_link")
    s.add_argument("--option", help="recommended_option")
    s.add_argument("--counterargument")
    s.add_argument("--by", default="operator")

    s = add("type", cmd_type,
            help="declare what a row IS (work or decision) and what it is WORTH, as one act")
    s.add_argument("id")
    s.add_argument("--kind", choices=["work", "decision", "unset"],
                   help="what the row IS. `unset` undeclares it, and an undeclared row READS AS "
                        "work everywhere, so it stays in the work queue either way")
    # NO `unset` HERE AND THE HELP SAYS WHY. An empty impact does not clear the column: `set`
    # writes the empty string, COALESCE(impact, stakes) stops there, and the unreadable value
    # folds onto this field's conservative default, which is `high`. Measured on a scratch store,
    # written up in outputs/2026-09-02-wave2/W2A/04-EMPTY-IMPACT-READS-AS-HIGH.md.
    s.add_argument("--impact",
                   help="what it is WORTH: low, medium, high, none, critical, or an amount of "
                        "money (100 or more, or 0 for none). There is no `unset`: an empty impact "
                        "reads as `high`, so to take one back, declare the one you mean")
    s.add_argument("--agent", default="",
                   help="who is declaring this. Defaults to the operator outside a fleet "
                        "terminal, and is REQUIRED inside one")
    s.add_argument("--note", default="", help="one sentence, filed on the row's thread")
    s.add_argument("--force", action="store_true",
                   help="override a refusal: a row HELD FOR THE OPERATOR, or a decision with no "
                        "impact. The override is written to the thread")

    s = add("demote", cmd_demote, help="`not fast`: demote a tier and log a calibration miss")
    s.add_argument("id")
    s.add_argument("--by", default="operator")
    s.add_argument("--note", default="")

    s = add("bump", cmd_bump, help="a decaying additive bump. Never a pin")
    s.add_argument("id")
    s.add_argument("--delta", type=float, default=2.0)
    s.add_argument("--reason", required=True)
    s.add_argument("--half-life", type=float, default=24.0)
    s.add_argument("--by", default="operator")

    s = add("defer", cmd_defer, help="a typed defer. Every path out carries a wake condition")
    s.add_argument("id")
    s.add_argument("--kind", required=True,
                   choices=("until-time", "until-event", "until-question", "accept-default",
                            "decline"))
    s.add_argument("--until", help="ISO time, for until-time")
    s.add_argument("--event", help="event type, for until-event")
    s.add_argument("--question", help="what would make this decidable? for until-question")
    s.add_argument("--lane", default="")
    s.add_argument("--workdir", default="",
                   help="where the until-question task runs. Defaults to the deferred row's own "
                        "workdir: the question is about this item, so answering it is work in "
                        "this item's tree.")
    s.add_argument("--reason", default="")
    s.add_argument("--label", default="")
    s.add_argument("--by", default="operator")
    s.add_argument("--acknowledge-default", action="store_true",
                   help="you have read what silence will ship and are deferring past it anyway")

    s = add("wake", cmd_wake, help="close a defer because its wake condition fired")
    s.add_argument("defer_id", type=int)
    s.add_argument("--by", default="system")
    s.add_argument("--note", default="")

    s = add("extend", cmd_extend, help="extend a silence window. A distinct, explicit act")
    s.add_argument("id")
    s.add_argument("--until", required=True)
    s.add_argument("--by", default="operator")
    s.add_argument("--reason", default="")

    s = add("checkpoint", cmd_checkpoint, help="fire the eligible defaults. Dry run by default")
    s.add_argument("--fire", action="store_true")

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except QueueError as e:
        return die(str(e), getattr(e, "code", ERR))
    except Exception as e:                                          # noqa: BLE001
        return die(f"{e.__class__.__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())
