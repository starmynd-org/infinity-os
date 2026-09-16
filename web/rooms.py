"""Room separation, enforced server side.

This is the safety-bearing file in `web/`. Everything else in the console is rendering.

The brief states the rule in one sentence and it is worth repeating where the code is:

    Room separation is a server-side verb allowlist, not a UI promise.

A UI promise is a promise about buttons. Buttons are a rendering decision, and a rendering
decision is one template edit, one stale cache, or one crafted POST away from being false. So
the room a request came from is carried on the request, checked here against a frozen set, and
refused before `store.apply` is reached. Omitting a button is not enforcement; this is.

**Study calls zero verbs.** Not "Study has no buttons": `ROOM_VERBS["study"]` is an empty
frozenset, and `web/tests/test_allowlist.py` proves that every verb registered anywhere in the
runtime -- all of them, enumerated from `store.registered()` rather than from a list kept by
hand -- is refused from Study, and that `store.apply` is never entered. That is what makes it
safe for Study to be the enjoyable room: a surface that cannot act cannot bias a decision.

Two guards, not one. The allowlist answers "may this room run this verb at all". The per-item
guards below answer "may this room run this verb on THIS row", which is the question the
`done` prohibition actually asks.

AND THE SECOND ONE READS THE ROW. Until task 0427 it read `item['actor_type']`, and `item` is
the console CARD, whose `actor_type` the card stamped on itself from the arm the row arrived on
-- so the "second" guard was `brain.queue_open`'s arm 2 restated in Python and could refuse
nothing that arm admitted. `assert_allowed_on` now reads `brain.work_item` itself. Those reads
are here rather than in `web/model.py` on purpose: a guard that trusts the rendering layer for
the fact it turns on is one template edit away from being false, which is the same argument the
allowlist is built on.

AND IT READS THE ROW'S PAST, NOT ONLY ITS PRESENT (row `0399`, 2026-08-27). `agent_claimable` and
`claimed_by` are both statements about what happens NEXT to a row, and `reopen` clears the second
of them by design, so two clicks in the Judge tier -- `Send back`, then the verb that has silently
replaced `Accept work` on the same card -- put an agent's finished report inside the guard's own
definition of the operator's own work, and the store accepted a `done` that overwrote it.
Reproduced in a browser 2 of 2 at commit `6b5fa01`. The third term, `agent_work_on`, asks whether
an agent has ALREADY worked the row and reads `brain.run` and the append-only `brain.thread`.

AND THE DATABASE BACKS THE FIRST TWO ARMS OF THAT TERM, because this file is not on every path.
`swarm done <id> --agent operator` on the same reopened row forges identically with no `rooms.py`
in its path at all, measured on `brain_lane_j` 2026-08-27. Task 0377's lesson is that a Python
check the database does not back holds only for polite callers, so
`migrations/0042_done_is_never_filed_over_an_agents_report.sql` refuses the same write in a
trigger, for every caller, and this guard is what turns that refusal into a sentence naming the
verb the operator actually wanted.
"""

from __future__ import annotations

import store

# `attention` joins this tuple with its `ROOM_VERBS` entry under R18, and the pair is not
# optional. `audit()` builds its `rooms` map from `ROOM_VERBS.items()` and
# `web/tests/test_allowlist.py:584` asserts `set(audit()["rooms"]) == set(ROOMS)`, so a key added
# to one and not the other is a red test and an audit surface that disagrees with itself.
# `allowed()` also lists this tuple when it refuses an unknown room, so a room that can act while
# absent here would be missing from the sentence naming what exists.
#
# NAMED AS A THIRD EDIT, because R18's scope sentence is two hunks: the `attention` key in
# `ROOM_VERBS` and the attention branch of `_perform`. This line is neither. It is here because
# the granted hunk is incoherent without it, not because the grant reached this far -- and it is
# said out loud so it can be refused rather than discovered.
ROOMS = ("queue", "attention", "brief", "fleet", "scope", "study", "sessions", "board")

# The human-actor verb set. Every entry is a verb some lane registered; nothing here is invented
# by the console and nothing here is a second implementation of anything.
#
# What is deliberately ABSENT from `queue`, and why:
#   `set`   score editing (must-not-build 2). The computed score stays the machine's.
#   `claim` a planner never claims and neither does the operator's console. Claiming is the
#           runner's act; a console claim would put a task in `active` with nobody running it.
#   `cancel` `release` `stop` `start` `reap`  fleet-shaped verbs. Fleet is where they would go,
#           and the brief's Fleet row says "and nothing else, ever", so they are nowhere.
ROOM_VERBS: dict[str, frozenset] = {
    # Queue: the human-actor verb set behind the five verb-named buttons, plus `post`, which is
    # the defer-with-question path -- the one deferral that makes an item cheaper rather than
    # older, and the only defer branch with a durable home today (see web/model.py:defer_options).
    "queue": frozenset({
        "answer",              # Accept default / Answer
        "recommend accept",    # Approve            (D6 registers this; see UNREGISTERED below)
        "recommend reject",    # Reject             (D6 registers this)
        "accept work",         # Accept work
        # ROW 0413, AND IT IS HERE BECAUSE THE ACT IT REVERSES IS HERE. The operator drove the
        # product end to end on 2026-08-28 and said: *"nothing happened when I accepted work.
        # That would maybe be a moment for confetti and for it to change from accept work to like
        # accepted. Maybe you have an option to like unaccept or like send back or undo the
        # acceptance would be good."* `MUST-NOT-BUILD` item 10 already required the receipt stripe
        # to carry a real inverse wherever one exists, and acceptance had none, so the stripe
        # correctly read `no undo` and the item was filed against the absence rather than the
        # sentence. Migration 43 and `transitions.py::unaccept_work` built the verb; this line is
        # what makes it reachable from the room the operator pressed `Accept work` in.
        #
        # IT IS NOT `reopen` AND MUST NOT BE CONFUSED WITH IT. `reopen` sends the WORK back: state
        # to `inbox`, claim cleared, attempts reset, an agent runs it again. This withdraws the
        # DECISION: `accepted_at` and `accepted_by` only, `state` and `result` untouched, so the
        # agent's report survives and the row returns to `brain.queue_open` arm 1 with `Accept
        # work` on it again. Granting one would not have granted the other, which is why this is
        # a second entry and not a widening of the first.
        #
        # NOT IN ANY OTHER ROOM. Study calls zero verbs, Fleet's list ends "and nothing else,
        # ever", Brief is answer and reopen. An acceptance is withdrawn where it was made.
        "unaccept work",       # Unaccept: the receipt stripe's undo, and the task page's
        "reopen",              # Send back, reason required
        "done",                # Mark my task done -- only on a row no agent will report on;
                               # the per-row guard below reads brain.work_item, not the card
        "post",                # defer-with-question: hesitation converted into agent work
        # D6b landed at 16:04Z with migration 0007 and these three. Before it landed there was
        # no home for a typed defer, a decaying bump or a calibration miss, and the console
        # rendered those controls disabled with the reason on them. They are live now.
        "queue defer",         # typed defer: five kinds, each with a wake condition
        "queue bump",          # the decaying additive bump, which is a labelled disagreement
        "queue demote",        # the `not fast` button, which logs a calibration miss
        # ------------------------------------------------------------------ the one-line overrule
        #
        # MUST-NOT-BUILD ITEM 5 IS OVERRULED FOR THIS ONE LINE, BY ANDREW, ON 2026-08-30, IN HIS
        # OWN WORDS: *"i want to override and say voice is needed to make it easy for users to get
        # through inbox"*. The amendment, with its condition, is at item 5 in
        # `web/MUST-NOT-BUILD.md`.
        #
        # ITEM 5'S INCIDENT IS PAID FOR RATHER THAN WAIVED. The prohibition is a freeform notes
        # FIELD, and its actual sentence is *"there is no box whose text goes nowhere"*: every text
        # box on a queue item is bound to one verb and one required argument. So this entry buys
        # exactly one thing and the condition on it is structural, not a promise:
        #
        #   * `note` is reachable ONLY from the voice affordance. There is no textarea, no typed
        #     note, no freeform box anywhere on a queue card. `test_queue_has_no_freeform_note_box`
        #     asserts the absence at the SURFACE, because this line makes the verb reachable and a
        #     rendering change is now the only thing between here and the box item 5 forbids.
        #   * The argument is a TRANSCRIPT, produced by a capture that is a counted row in
        #     `brain.voice_capture` before the microphone opens, and the note names the retained
        #     audio and its sha256. The text goes somewhere and comes from somewhere.
        #
        # AND NOTHING ELSE FROM THE VOICE LANE IS HERE. `voice open`, `voice recorded`,
        # `voice retained`, `voice transcribed` and `voice failed` are all registered verbs and
        # NONE of them is reachable from any room. The capture runs as the `voice` CLI in a
        # subprocess, doing its own writes as itself, exactly as it does when he runs it in a
        # terminal; the console posts the finished transcript through this one verb. That is why
        # the overrule is one line and not six: a rendering surface has no business being able to
        # open a microphone, and it still cannot.
        "note",                # the voice note: a transcript, onto the row he is looking at
        # THE OPERATOR'S STOPWATCH (task 0284). 0279 built `brain.time_entry` and registered these
        # two, and deliberately wired the CLI only: a room's worth of buttons over a ledger with
        # two rows in it would have shipped a surface before the thing it renders had been used
        # once. It has been used now, and until these two names were here `audit()` listed them
        # under `registered_and_unreachable` -- a verb the console could not reach.
        #
        # `queue time correct` IS DELIBERATELY NOT HERE, and its absence is a decision rather than
        # an oversight. A correction supersedes a stopped entry with two explicit timestamps and a
        # reason, which is a form, not a button; and every path to it starts with reading the
        # entry it is about. That is the CLI's shape (`queue time correct <id> --started ...`).
        # Adding it to this set to make `registered_and_unreachable` shorter would put a verb in a
        # room with no caller, which is the widening `note` was removed for in 0169.
        "queue time start",    # start the stopwatch on one item. Refuses a second one
        "queue time stop",     # stop the one running. The only UPDATE the ledger permits
        # V5's images (task 0167). FOUR verbs and not one, because an attach is four state
        # changes: the attempt exists, the bytes are vouched for, the attempt died, the pointer
        # was removed. `web/actions.py::attach_image` asserts all three of the attach verbs
        # BEFORE it opens the file, so the room gate is still passed before `store.apply` is
        # reached on any of them -- the pipeline reads the filesystem between two of the three
        # and therefore cannot be one `dispatch` call, and this is what that costs.
        #
        # NOT IN ANY OTHER ROOM. Study calls zero verbs and that is proven by test. Fleet's list
        # ends "and nothing else, ever". Brief is answer and reopen. An image is attached where
        # the operator is deciding, which is the Queue and the item page it opens.
        "image attach",        # the attempt: state 'landing', nothing attested
        "image attached",      # the bytes are vouched for: pointer + host + sha256 + bytes
        "image failed",        # the attempt died, and it says at which stage
        "image detached",      # the pointer is removed. The file on disk is untouched
        "image dismissed",     # the operator acted on a failed attach. The row survives it
    }),
    # ------------------------------------------------------------------ Attention, RULING R18
    #
    # SEVEN VERBS SINCE 2026-09-10 23:5xZ, and this line has read THREE, then four, five, and now
    # seven: R18 granted three, R36 added `done`, the receipt-expectations ruling
    # (`_system/attention/RECEIPT-EXPECTATIONS-2026-09-10.md`) added `reopen` as the inverse of
    # that `done`, and Andrew's D4 added `accept` and `recommend accept` WITH the objectives arm.
    # The count is written here because a reader who trusts a stale header does not
    # go and count the set -- and this header has been wrong before, which is why the block at the
    # bottom of the entry, not this sentence, is the authority. Placed beside Queue because it
    # ranks the SAME population -- `brain.queue_open`
    # through `web/model.py::_StoreAttentionPort.queue` -- so a reader comparing the two sets
    # should not have to go looking for the second one.
    #
    # GRANTED BY R18 (Admiral, 2026-09-09T19:58:59Z) and confirmed by Andrew directly. Before it,
    # `attention` was not a key in this dict at all, so `allowed()` took its `room not in
    # ROOM_VERBS` branch and refused with "no such room" -- NOT with Study's "calls zero verbs".
    # `web/views/blueprint.py` claimed the two refusals were the same and was wrong about it;
    # `outputs/2026-09-09-IOS-term-2/probe-attention-refusal.py` runs both and prints them.
    #
    # THE DIFFERENCE MATTERED: a room refused as UNKNOWN is indistinguishable, from outside, from
    # a room somebody forgot to list. Study's silence is a policy with a test behind it. This
    # room's silence was an absence, and no test can tell an absence from an oversight.
    #
    # THE SECOND HUNK EXISTS NOW, AND THE PARAGRAPH BELOW IS KEPT RATHER THAN CORRECTED IN PLACE,
    # because the reason it gave was right on the day and is the reason the branch it declined to
    # write is so small now. R18's five verbs all had room-generic branches in `_perform`; D4's two
    # do not, and they cannot: an objective's bare `source_id` is an integer that can equal a work
    # item id, so `model.find_item` -- which matches `i["id"] == item_id` over the whole open queue
    # -- can hand back the WRONG ROW for one (PROOF case 8 constructed exactly that collision). The
    # attention branch of `_perform` therefore resolves an objective by TYPED id through
    # `model.attention_port().item("objective:<id>")` and never by `_find`, and that is the whole of
    # what it is for. It is still not a place where a transition is reimplemented.
    #
    # ONE HUNK, THOUGH R18 GRANTED TWO. The ruling also granted the attention branch of
    # `web/app.py::_perform`, and there is nothing to write there: `_perform` dispatches on
    # ACTION, not on room, and `answer`, `accept_work` and `unaccept` already have room-generic
    # branches that pass `room` straight through to `web/actions.py`. The write door
    # `@app.post("/<room>/act")` is likewise generic and already matched `/attention/act`; the
    # allowlist is the only thing that was refusing it. A second hunk would have been a change
    # made because it was permitted rather than because it was needed.
    #
    #   `answer`         The seat brief names this verb and no other: *"Answering a row must
    #                    perform the act."* Until this line, answering a row on this surface
    #                    performed nothing, and every receipt it could show was a promise about a
    #                    page -- which is exactly what `MUST-NOT-BUILD` item 10 exists to forbid.
    #
    #   `accept work`    The completed-work admission arm. `term-4` traced that a row reaches this
    #                    surface on option-EXISTS or on a completed decision; this is the second
    #                    of those, and it is a large share of what Attention ranks.
    #
    #   `unaccept work`  NOT A SEPARATE CHOICE. It travels with `accept work` by construction:
    #                    `web/actions.py::accept_work` returns
    #                    `undo = {"action": "unaccept", ...}`, and `console.js` posts that undo
    #                    back to THE SAME ROOM. Granting the act without its inverse would ship a
    #                    receipt stripe whose undo button returns 403 -- an item 10 failure
    #                    wearing an item 10 affordance. The Queue's own entry says it plainly:
    #                    *"An acceptance is withdrawn where it was made."*
    #
    # WHAT IS DELIBERATELY ABSENT, each one a decision rather than an oversight:
    #
    #   `recommend accept`, `recommend reject`
    #                    NOT REGISTERED. They are in `UNREGISTERED_OWNER` below, owned by D6, and
    #                    a registry dump at this SHA lists 32 verbs with neither among them.
    #                    Naming them here would put two buttons on this surface that return 501.
    #                    SO THE RECOMMENDATION OPERATIONS -- approve and reject a recommendation --
    #                    are unavailable from every room, this one included, until D6 builds them.
    #
    #                    NARROWED BY `term-4` AT 203142Z, AND MY FIRST WORDING WAS BROADER THAN
    #                    THE EVIDENCE. It said "a row admitted BECAUSE it has an option cannot be
    #                    decided from any room". That treats option-backed as a DISJOINT arm
    #                    needing these two verbs, and it is not: the admission evidence terms
    #                    OVERLAP. A question can carry an option and still be answered; a
    #                    completed work item can carry an option and still be accepted -- and both
    #                    of those verbs are registered and are in this set. So the missing verbs
    #                    establish that the RECOMMENDATION OPERATIONS are absent, not that every
    #                    option-backed row is undecidable.
    #
    #                    Either way this grant does NOT close item 7, and it does not establish
    #                    that item 7 fails either. The option-to-operation mapping, and which door
    #                    each source and state can actually use, are separate questions owned
    #                    elsewhere. The room is OPENED, not done.
    #
    #   everything else  The Queue's other sixteen -- defer, bump, demote, note, the images, the
    #                    stopwatch -- are queue-management, not deciding. This room ranks and
    #                    decides; it does not schedule.
    #
    # A caveat on the evidence, because the oracle is process-dependent: `store.registered()`
    # returns what the IMPORTING process has imported, so a probe that imports less sees fewer
    # verbs. The first run of my own probe reported ALL of these unregistered from a registry of
    # size ZERO and printed a green line under it. The three above are registered by
    # `swarm_engine.transitions` and `swarm_engine.accept`, which are the modules the console's
    # own write path already goes through.
    #   `done`           WAS ABSENT AND IS NOW IN, BY RULING R36. Kept as a record of the reversal
    #                    rather than tidied away, because the reason I first gave was WRONG and a
    #                    later reader deserves to see which argument lost.
    #
    #                    I excluded it citing Fleet's *"never fabricate an agent's report."*
    #                    `term-13` showed that guards a DIFFERENT act: the per-row guard already
    #                    requires `not agent_claimable`, no holder, no agent prior work, and the
    #                    same acting-on/actor identity, in the STORE's terms rather than this
    #                    surface's. `KIND_BY_ARM` confirms the arm is the operator's own
    #                    non-claimable work, not an agent's. So the fear was real and it was
    #                    already answered one layer down.
    #
    #                    THE MEASUREMENT THAT DECIDED IT, on the retained first page at N100,
    #                    denominator 7: four rows are kind `human` and their only available act is
    #                    `done`. Excluding it left FOUR OF SEVEN ROWS IN AN INBOX WITH NO ACT
    #                    AVAILABLE. The Admiral's sentence is the right one: that is not a narrow
    #                    inbox, it is an inbox that lies about what it can do.
    #
    #                    KEEP THE GUARDS. Any request to weaken `not agent_claimable`, the holder
    #                    check or the prior-work check is a SEPARATE thing and is not this.
    #
    #   `dismiss`        STILL 0 OF 7 AND NOT GRANTED BY IMPLICATION. R36 says so explicitly and
    #                    there is no such verb in the registry. If it should exist, it needs its
    #                    own ask saying what it would MEAN -- the frozen c3 surface has a Dismiss
    #                    that is page-local, and a runtime verb of that name would not be it.
    #
    #   `reopen`         Sends the WORK back and requires a reason. Brief holds it and Attention
    #                    could argue for it, but a first grant should be the smallest set that
    #                    makes the named act true, so that a later widening is a deliberate act
    #                    with a reason attached rather than a line nobody remembers adding.
    #
    #                    THAT ARGUMENT LOST ON 2026-09-10 AND `reopen` IS NOW IN. The ruling is
    #                    `_system/attention/RECEIPT-EXPECTATIONS-2026-09-10.md`, section "Ruling
    #                    that rides with this file", published by ATTENTION-OS-admiral BEFORE any
    #                    control existed on this page. Its reason is item 10 of
    #                    `web/MUST-NOT-BUILD.md` read the other way round: `done` is now granted
    #                    here (R36), `web/actions.py::mark_my_task_done` returns
    #                    `undo = {"action": "undo_done"}`, and `undo_done` DISPATCHES THE VERB
    #                    `reopen` (`web/actions.py:366`). Without this line the receipt stripe on
    #                    a `done` either offers an undo button that returns 403, or says "no undo"
    #                    while the store holds the inverse -- and item 10 forbids exactly that
    #                    sentence: "Where a real inverse verb exists the stripe carries it."
    #                    The smallest set that makes the granted act TRUE includes the inverse of
    #                    that act, which is what the first grant's own `unaccept work` line says.
    #
    #                    THE CONSEQUENCE, STATED SO IT CAN BE REFUSED RATHER THAN DISCOVERED:
    #                    `send_back` also rides `reopen` (`web/actions.py:219`), so a POST naming
    #                    `action=send_back` at `/attention/act` now passes THIS gate. Three things
    #                    are true about that and none of them is "it is fine":
    #                      * the page renders NO send-back control, on any row, and a test asserts
    #                        the rendered form set;
    #                      * `assert_allowed_on` below still reads the row behind the card for
    #                        `done`, and `send_back`'s own transition still refuses what it
    #                        refuses -- a room gate is not the last gate;
    #                      * the act-door check
    #                        `outputs/2026-09-09-IOS-term-2/attention-act-door-check.py` pins this
    #                        set at EXACTLY these five, so a sixth verb is a red check rather than
    #                        a line nobody notices.
    #                    A reason-carrying send-back control is a separate ask with its own
    #                    argument; this line does not grant one and does not imply one.
    #
    #   `dismiss`        STILL NOT GRANTED, and this ruling does not touch it (R36 and the
    #                    ruling's own last line). There is no such verb in the registry, and the
    #                    page renders the word nowhere.
    #
    # ---------------------------------------------------------------- SEVEN SINCE 2026-09-10 23:5xZ
    #
    # `accept` AND `recommend accept` JOIN THE SET **WITH THE FIFTH ARM AND NEVER BEFORE IT**, by
    # Andrew's decision D4 (`knowledge/attention/OBJECTIVES-ARM-AND-OPTION-FIELDS-2026-09-10.md`,
    # header and section 3; recorded in `status/ATTENTION-OS-admiral.md` section 4). The arm is
    # `brain.objective WHERE state = 'inbox'` as a fifth `UNION ALL` on `brain.queue_open`, and the
    # EXISTING decidability gate -- not a new heuristic -- decides which of those rows the default
    # view shows. Written by `ATTENTION-OS-admiral/A10`, packet ATT-8.
    #
    # "WITH THE ARM" IS A REAL CONDITION AND THIS IS WHAT IT COSTS WHEN IT IS NOT MET, said out
    # loud so it can be refused rather than discovered: on a store whose migration has not been
    # applied, `brain.queue_open` has four arms, no objective row ever reaches this page, and these
    # two entries are two names nothing can reach -- which is the SAFE direction and is why the
    # order in the staging packet is hunks-then-migration and never the reverse. They are not
    # inert in the other direction: a POST naming `action=accept_objective` at `/attention/act`
    # passes THIS gate on any store, and is then refused by `_perform`'s own branch in words,
    # because the row it resolves through `model.attention_port().item()` is not there.
    #
    #   `accept`           THE OBJECTIVE TRIAGE VERB: inbox to accepted, BY NAME, at
    #                      `engine/swarm_engine/transitions.py:1813` (`UPDATE brain.objective ...
    #                      WHERE name = %s AND state = 'inbox'`). It is the same verb the Scope
    #                      room already holds, and this is the second room to hold it.
    #
    #                      NO INVERSE EXISTS, AND THE STRIPE SAYS SO IN THOSE WORDS.
    #                      `brain.objective.state` is CHECKed to `('inbox','accepted')`
    #                      (`migrations/0001:330`), there is no `unaccept` for an objective and no
    #                      `declined` state, so the receipt carries
    #                      `no_undo_reason = "an accepted objective has no inverse verb yet"` and
    #                      the page renders the server's sentence rather than an undo button that
    #                      would return 403 or, worse, one that appears to work. Adding a decline
    #                      state is a separate proposal and is NOT smuggled in here.
    #
    #                      AND NO AGENT ACCEPTS AN OBJECTIVE ON THE PERSON'S BEHALF. `accept` runs
    #                      as the console's own human login exactly as `accept work` does; the
    #                      fourth carve-out in this repo's `CLAUDE.md` is about acceptance being a
    #                      human act, and this entry moves WHERE a human may make it, never WHO.
    #
    #   `recommend accept` THE ACT ON **ONE OPTION** of an objective's interpretation, which is how
    #                      an objective row becomes decidable at all: the gate admits an objective
    #                      only on `has_option`, since an objective carries no completed result, no
    #                      answer and no recommendation decision. `web/actions.py::dispatch_option`
    #                      already rides this verb for every other source and the Queue has held it
    #                      since D7; nothing is reimplemented here.
    #
    #                      CREATING A TASK NEVER COUNTS AS COMPLETING IT, and that is structural
    #                      rather than a promise in a receipt: `recommend accept` posts the task the
    #                      option describes and writes NOTHING to `brain.objective`, so the
    #                      objective stays in `inbox` and stays on this page until the person
    #                      accepts it with the verb above. The receipt names the posted task's id.
    #
    #                      IT IS IN `UNREGISTERED_OWNER` BELOW AND THAT IS NOT A CONTRADICTION.
    #                      D6b registered it (commit `1f12f8c`); the map is the mechanism by which
    #                      `dispatch` names the lane that owes a verb in a process that has not
    #                      imported it, and a process which reaches this room without
    #                      `human_queue.transitions` gets a 501 naming D6 instead of a stack trace.
    #                      `web/app.py:56` imports that module, so the console's own process has it.
    #
    # `dismiss` STILL STAYS ABSENT (R36, and D4's own last line repeats it). There is no decline,
    # no dismiss and no page-local disappearance for an objective: the two verbs above are the
    # whole of what this room may do to one.
    #
    # THE ACT-DOOR CHECK MOVES IN THIS SAME COMMIT, to seven, with a plant that still goes red on
    # an EIGHTH (`outputs/2026-09-09-IOS-term-2/attention-act-door-check.py`). A guard whose
    # expectations lag the grant is a guard that goes green on the old world.
    # Andrew's 2026-09-12 census instruction connects standalone proposal decisions.
    "attention": frozenset({"answer", "accept work", "unaccept work", "done", "reopen",
                            "accept", "recommend accept", "recommend reject"}),
    # Brief: the brief says "answer, plus navigation". The MOCK also puts a `reopen` button on
    # the Defaulted rows, and where the brief and the mock disagree the mock wins. Posted to
    # crosstalk slot 7.
    "brief": frozenset({"answer", "reopen"}),
    # Fleet: the brief's list, verbatim, and the sentence after it is "and nothing else, ever".
    #
    # THIS SET WIDENED BY THREE ON 2026-08-18, task 0166, AND THE WIDENING IS THE OPERATOR'S OWN,
    # recorded here rather than merely done, because "and nothing else, ever" is a sentence a
    # later reader will find and be right to challenge.
    #
    # `00-COMMANDER-HANDOFF.md` grants the first of two deliberate overrules: v1 refused
    # interactive agent sessions by name ("do not build a chat with the admiral"), and the
    # operator has overruled it in writing, bounded -- entered deliberately, thread-recorded both
    # directions, ending with the run, and marking the run steered so it leaves the auto-accept
    # measurement. `steer take` and `steer release` are that overrule's verbs. They are refused
    # from every other room by the same allowlist that refuses everything else there, and the
    # runfeed is a Fleet surface (`/agent/<name>` renders with `room="fleet"`).
    #
    # `agent config set` is the picker: model, effort and the preferred account, per agent. It is
    # here for the same reason the runfeed is -- Fleet is the dispatch control surface -- and it
    # refuses `permission_mode` and anything touching `bypassPermissions` IN THE TRANSITION, by
    # name and with the reason (`swarm_engine/fleet_config.py`), rather than by leaving a select
    # out of a template.
    #
    # What did NOT arrive with them, and the absence is the point: `done` is not here and never
    # will be. The original narrowness was about actor forgery, and the console may now create
    # work and still may never fabricate an agent's report.
    "fleet": frozenset({"answer", "reopen", "pause", "resume", "msg",
                        "steer take", "steer release", "agent config set"}),
    # Scope: post via the scoping flow, and objective intake.
    #
    # THIS SET NARROWED FROM FOUR VERBS TO THREE ON 2026-08-16, task 0169, and the removal is
    # recorded rather than just done, because a permission that quietly reappears is how a
    # widening survives the reason for it.
    #
    # `note` was here. It was the narrowest widening in this table and it existed for exactly one
    # caller: the drafted definition of done travels in the posted body, and D7 measured that
    # `post` wrote the body into `work_item.result` while `done` overwrote that same column with
    # the agent's summary, so a definition posted through the body alone was destroyed by the work
    # finishing. `scope_post` therefore wrote a second copy onto the append-only thread, and
    # `note` was the only registered verb that reached it.
    #
    # Migration 14 gave the work order `work_item.brief`, write-once and overwritten by no verb,
    # so the body now outlives `done` on its own and the duplicate has nothing left to protect.
    # D7's own suite named this exit: `test_scoping.py` asserted the body loses its definition
    # after `done` with the message "If the store was fixed, this workaround should be removed
    # rather than left as dead weight." The store was fixed. Keeping the grant would leave a live
    # verb in a room with no caller, which is worse than the widening was -- a widening at least
    # had a reason attached. `scope_post` no longer dispatches it and
    # `test_scoping.py:test_the_scope_room_no_longer_needs_note` pins the set at three.
    "scope": frozenset({"post", "intake", "accept"}),
    # Study: ZERO. Proven by test, not by the absence of buttons.
    "study": frozenset(),
    # Sessions: the proof surface for terminal logging (row `0424`). EMPTY ON PURPOSE and for the
    # same reason Study is empty: a room whose job is to be believed about coverage must not also
    # be able to change what it is counting. It reads `ingest coverage` and renders it. The
    # remedies its numbers point at (`ingest transcript verify`, `ingest events requeue`,
    # `ingest backfill`) stay on the CLI, where they are deliberate acts with a reason attached
    # rather than a button next to the number that made you want to press it.
    "sessions": frozenset(),
    # Board: TWO VERBS, and the pair IS the four columns. Bus row 0436, under the one prohibition
    # the operator overruled (`web/MUST-NOT-BUILD.md` item 3, 2026-08-28, against his own
    # eight-terminals bill).
    #
    # His condition on that overrule is a requirement rather than a hedge: **a column is a project
    # STATE and moving between columns CALLS A REGISTERED TRANSITION whose argument is that
    # state.** So this set is not "the board's buttons": it is the whole board. `project rest`
    # carries `hold`, `ice` or `blocked` as an argument and `project resume` carries the fourth
    # word. There is no board table, no position column and no coordinate written anywhere, which
    # is why the prohibition's fear -- a second state machine nothing else in the runtime can see
    # -- is answered by construction here rather than waived. The claim path reads the SAME column
    # in SQL (`_PROJECT_BUDGET_CTE`, task 0430), so a drop and a throttle are one fact.
    #
    # WHAT IS DELIBERATELY ABSENT, and each absence is a decision:
    #   `project add`     a project is born rarely and `swarm project add` is the door. A create
    #                     form here would be a surface ahead of a need.
    #   `project rm`      destructive, and `brain_runtime` holds no DELETE on brain.project at
    #                     all. Removing a project is a considered act at a CLI, not a drag.
    #   `project attach`  moving WORK between projects. It is the one act that can lift a hold for
    #                     a single item -- migration 46 refuses it to every non-human connection
    #                     -- and putting it on a drag handle would make it the easiest gesture on
    #                     the page. Work items render on this board read-only.
    #   `project retitle` an edit, not a state change, and it belongs where the project is read.
    #   everything else   this room may not touch a work item at all. `done`, `accept work`,
    #                     `reopen` and the queue's whole set are absent, so a project board can
    #                     never become a second place to finish somebody's work.
    "board": frozenset({"project rest", "project resume"}),
}

# Verbs the console's buttons name that no lane has registered yet. Kept as data rather than as
# a comment so the console can say WHICH lane owes the verb instead of rendering a dead button
# or a stack trace. D6 owns recommendations (D00's verb-surface table).
# D6b registered `recommend accept`, `recommend reject`, `queue bump`, `queue defer` and
# `queue demote` at 2026-08-16T16:04Z (commit 1f12f8c). This map is kept rather than emptied:
# it is the mechanism by which the console names the lane that owes a verb instead of standing
# in for it, and the next unbuilt verb will need it again.
UNREGISTERED_OWNER = {
    "recommend accept": "D6",
    "recommend reject": "D6",
}


class RoomRefusal(PermissionError):
    """A verb a room may not run. Raised before the store is reached."""


class ItemRefusal(PermissionError):
    """A verb this room may run, on a row it may not run it on."""


class VerbNotBuiltYet(RuntimeError):
    """A verb the console names and no lane has registered. Named honestly, never faked."""


def allowed(room: str) -> frozenset:
    if room not in ROOM_VERBS:
        raise RoomRefusal(f"no such room: {room!r}. One of: {', '.join(ROOMS)}")
    return ROOM_VERBS[room]


def assert_room_can_act(room: str) -> None:
    """A room whose allowlist is empty is refused AT THE DOOR, before a row is even looked up.

    Study's refusal used to depend on the path through `_perform`: a POST naming a row that no
    longer exists was refused as a stale card, and one naming a row that did exist was refused by
    the allowlist further in. Same outcome, different reason, and the weaker of the two was the
    one the test happened to exercise. A room that calls zero verbs cannot want anything from this
    endpoint, so the endpoint stops before it asks what.
    """
    if not allowed(room):
        raise RoomRefusal(
            f"the {room} room calls zero verbs. Nothing posted here can be permitted, and it is "
            f"refused server side rather than by leaving the buttons out."
        )


def assert_allowed(room: str, verb: str) -> None:
    """The gate. Called before anything else on every write path in the console."""
    permitted = allowed(room)
    if verb not in permitted:
        if not permitted:
            raise RoomRefusal(
                f"the {room} room calls zero verbs. {verb!r} is refused here, and it is refused "
                f"server side rather than by leaving the button out."
            )
        raise RoomRefusal(
            f"the {room} room may not call {verb!r}. It may call: {', '.join(sorted(permitted))}."
        )


# The four columns the `done` guard below decides on, read from `brain.work_item` and from no
# view. `brain.queue_open` publishes none of `agent_claimable`, `state` or `claimed_by`, and the
# whole point of this read is to reach the ROW rather than the arm the row arrived on, so a view
# would defeat it however convenient it looked.
_ROW_BEHIND = ("SELECT id, actor_type, agent_claimable, state, claimed_by "
               "FROM brain.work_item WHERE id = %s")


def _row_behind(item_id: str) -> dict | None:
    """The `brain.work_item` row a console write would act on."""
    with store.read() as s:
        return s.one(_ROW_BEHIND, (item_id,))


# =========================================================================== DID AN AGENT WORK IT
#
# ROW 0399, and the two columns above are not an answer to that question.
#
# `assert_allowed_on` permitted `done` where `NOT agent_claimable AND claimed_by IS EMPTY`, and
# both terms are about the row's FUTURE rather than its past:
#
#   `agent_claimable` says "may the fleet TAKE this", and `swarm set <id> agent_claimable false`
#   is a legitimate registered act meaning "I am taking it back from the fleet". It says nothing
#   about who has already worked it. `transitions.py::set_field` names the incident in its own
#   comment: on 2026-08-18 the admiral lowered the flag on 0103 sixty seconds after T5 claimed it.
#
#   `claimed_by` says "is an agent holding it NOW", and `reopen`, `fail`, `release` and `reap` all
#   clear it, deliberately and correctly. The forgery happens one statement after it is cleared:
#   `Send back` runs `reopen`, which empties `claimed_by`, and the verb on the same card then
#   passes both terms.
#
# MEASURED READ-ONLY ON LIVE `brain` 2026-08-27, ledger 33, 310 work items:
#   172 of 310 satisfied the old predicate. Of those 172:
#      7 carry a `brain.run` row              an agent's runner opened an attempt on them
#      7 carry a `claim` on the thread        the same seven
#     11 carry a `done`/`fail`/`block` on the thread filed by a name that is not `operator`
#     14 the union: the rows on which `Mark my task done` fabricates somebody else's report
#    158 the remainder: the operator's own work, where the verb is CORRECT and still reaches him
# and the arms that were rejected, measured the same way, on the same 172:
#    140 `posted_by` is not the operator      free text any caller sets; 41 of them say `admiral`
#                                             over rows no agent ever touched. Migration 26
#                                             rejected this column for the same reason.
#      4 the reporter is in `brain.agent`     that table holds fleet TERMINALS. The seven rows
#                                             this program's own lane subagents worked today
#                                             (0371-0375, 0379, 0382, reported by `lane-B`) are
#                                             not in it, so it under-reports by a factor of three
#      0 `actor_type`                         nullable, and migration 26 rejected it on
#                                             measurement: "unset is a real state"
#
# So the predicate is EVIDENCE THAT AN AGENT ALREADY WORKED THE ROW, and it has three arms:
#
#   1. a `brain.run` row exists            the runner opened an attempt. Written by `run start`,
#                                          deleted by nothing, and no console path reaches it.
#   2. a `claim` on the append-only thread `claim` is refused unless `agent_claimable` is true,
#                                          and migration 26's first trigger forces that false on
#                                          every `actor_type='human'` row, so the operator's own
#                                          work can never carry one. Not redundant with arm 1:
#                                          122 live rows carry a claim and 121 carry a run.
#   3. a `done`, `fail` or `block` on the  the identity-bearing arm, and the ONLY one that needs
#      thread filed by a name that is      to know who is asking. It is what catches a row worked
#      neither empty nor the human this    by a subagent that never went through the runner.
#      console is acting as
#
# ARM 3 IS DELIBERATELY NOT IN THE DATABASE TRIGGER that backs arms 1 and 2
# (`migrations/0042_done_is_never_filed_over_an_agents_report.sql`). An identity-free version of
# it cannot tell the operator's own second `done` after his own `reopen` from another party's
# report, and refusing that would delete the accept -> reopen -> done -> accept loop that
# migration 22 exists in writing to protect. 1 of the 172 live rows already carries a `done` from
# `operator`. Where the console knows the human it can afford arm 3; the trigger cannot.
#
# ONE IMPLEMENTATION, TWO CALLERS. `web/model.py` calls this to stop the CARD offering a verb the
# guard will refuse, because an affordance that cannot work is worse than one that is absent. It
# takes a list so the queue costs one query and not one per card.
_AGENT_WORK = """
SELECT w.id                                                                       AS id,
       (SELECT count(*) FROM brain.run r WHERE r.work_item_id = w.id)             AS runs,
       (SELECT coalesce(string_agg(DISTINCT r.agent, ', '), '') FROM brain.run r
         WHERE r.work_item_id = w.id AND coalesce(r.agent, '') <> '')             AS ran,
       (SELECT coalesce(string_agg(DISTINCT t.from_agent, ', '), '')
          FROM brain.thread t
         WHERE t.work_item_id = w.id AND t.kind = 'claim'
           AND coalesce(t.from_agent, '') <> '')                                  AS claimers,
       (SELECT count(*) FROM brain.thread t
         WHERE t.work_item_id = w.id AND t.kind = 'claim')                        AS claims,
       (SELECT coalesce(string_agg(DISTINCT t.from_agent, ', '), '')
          FROM brain.thread t
         WHERE t.work_item_id = w.id AND t.kind IN ('done', 'fail', 'block')
           AND coalesce(t.from_agent, '') NOT IN ('', %(me)s))                    AS reporters
  FROM brain.work_item w
 WHERE w.id = ANY(%(ids)s)
"""


def agent_work_on(item_ids, *, acting_as: str = "operator") -> dict:
    """`{id: evidence}` for every id given, where evidence is a dict with `worked` and `why`.

    `why` is a SENTENCE NAMING THE EVIDENCE, not a boolean restated, because the operator reading
    the refusal has to be able to go and check it: the run, the claimer, the reporter.
    """
    ids = [str(i).strip() for i in item_ids if str(i or "").strip()]
    if not ids:
        return {}
    with store.read() as s:
        rows = s.query(_AGENT_WORK, {"ids": ids, "me": str(acting_as or "")})
    out = {}
    for r in rows:
        why = []
        if r["runs"]:
            why.append(f"{r['runs']} run(s) on brain.run"
                       + (f" by {r['ran']}" if r["ran"] else ""))
        if r["claims"]:
            why.append(f"claimed on the thread by {r['claimers'] or 'an unnamed agent'}")
        if r["reporters"]:
            why.append(f"already reported on by {r['reporters']}")
        out[str(r["id"])] = {"id": str(r["id"]), "runs": int(r["runs"]),
                            "claims": int(r["claims"]), "ran": r["ran"],
                            "claimers": r["claimers"], "reporters": r["reporters"],
                            "worked": bool(why), "why": "; ".join(why)}
    return out


def assert_allowed_on(room: str, verb: str, item: dict | None, *, acting_on: str = "",
                      actor: str = "operator") -> None:
    """The second guard: this verb, on THIS ROW -- read from the table, not taken from the card.

    `done` is the one the prohibition in `sprint-swarm-app.md` was actually about. That
    prohibition was never about buttons; it was about actor forgery. An operator `done` on an
    agent's work item fabricates *the agent's* report, and no allowlist entry can tell those
    apart because both are the verb `done`. Only the row can.

    ================================================================ WHAT THIS GUARD USED TO BE

    Until task 0427 it read `item['actor_type']` and permitted `done` where that was `'human'`.
    `item` IS NOT A `work_item` ROW. It is the console CARD built by `web/model.py::_card`, and
    the card's `actor_type` was put there by the card: `_card` looks `kind` up in `KIND_BY_ARM`
    on `(source_type, primary_verb)`, and `_VERBS['human']` then stamped `actor_type = 'human'`
    onto every card of that kind. The value this guard tested was therefore decided by WHICH ARM
    OF `brain.queue_open` THE ROW ARRIVED ON, and `brain.work_item.actor_type` was never read on
    this path at all. Since `queue/schema/0013` that arm is `NOT w.agent_claimable`, so the guard
    was that predicate restated in Python: it could refuse nothing the arm admitted and admit
    nothing the arm refused. Two guards on paper, one predicate in fact.

    MEASURED 2026-08-18 on `brain_scratch_t4_0427`, which is why this is a repair and not a
    tidy-up. Post a fleet row (`agent_claimable = true`), let an agent claim it (`state = active`,
    `claimed_by = T-probe-0427`), then lower `agent_claimable` to false -- which migration 26
    permits to everybody, deliberately, as the safe direction. The row lands on arm 2 with an
    agent still parked on it, `model.find_item` returns a card with `kind = 'human'` and
    `actor_type = 'human'`, and the old guard PERMITTED `done`.

    Nothing was forged on the day, and the reason is worth writing down because it is not this
    file: `engine/swarm_engine/transitions.py::_hold` refused the call, since `web/actions.py`
    passes `agent=<operator>` and the operator is not the holder of an `active` row. So the
    defect the measurement actually found is that the CONSOLE permitted a write the STORE then
    rejected -- the exact inversion of this module's rule that a room's writes are refused before
    `store.apply` is reached. The operator got a `VerbError` out of a transition where he should
    have got a stated refusal from the room, and the room's own second guard was doing no work.

    ================================================================ WHAT IT IS NOW

        permit `done` when   NOT agent_claimable   AND   no agent holds the row
                             AND NO AGENT HAS EVER WORKED THE ROW

    The first two terms are read off `brain.work_item` by `_row_behind` below. The third is row
    `0399` and it is `agent_work_on` above: two clicks in the Judge tier -- `Send back`, which
    runs `reopen` and CLEARS `claimed_by`, then the verb that has silently replaced `Accept work`
    on the same card -- satisfied both of the first two terms on an agent's finished report, and
    the store accepted it. Reproduced 2 of 2 in a real browser at commit `6b5fa01`, on the repo's
    own seeded rows, in `outputs/2026-08-27-J-0399/`.

    The first two terms are KEPT and are checked FIRST, cheapest first: they refuse a row nobody
    has worked YET but the fleet is about to (`agent_claimable`), and a row an agent is holding
    right now (`claimed_by`). The third refuses a row an agent has ALREADY worked. Three different
    tenses of the same question and none of them subsumes the others.

      * `NOT agent_claimable` and NOT `actor_type = 'human'`, for the reason migration 26 gives
        at length: `actor_type` is nullable and "unset is a real state", and two of the
        operator's fifteen live rows on 2026-08-18 (0068, 0071) carry NULL and are his. Migration
        26's `work_item_human_is_never_agent_claimable` trigger coerces every `actor_type =
        'human'` row to `agent_claimable = false`, so this term is a superset of the old one by
        construction and no row the old guard permitted is lost.
      * `coalesce(claimed_by, '') = ''` is the term that makes this a check rather than a
        restatement, and it is the residual `queue/schema/0013` named and could not close from
        inside a view. A row an agent holds is a row that agent will file a report for, and that
        is the report an operator `done` would fabricate. `release`, `fail` and `reap` all clear
        `claimed_by` when they return a row to `inbox` (`transitions.py`), so this term does not
        strand a requeued row on a stale claimer.

    `actor_type` and `state` are read too, and they appear in the refusal rather than in the
    predicate: a refusal that prints the row's measured columns is one the operator can act on,
    and a third term restating arm 2's `state IN ('inbox','active')` would put this guard back
    where it started.

    `acting_on` is the id `store.apply` will be handed. A guard that checks one row while the
    verb acts on another is not a guard, so when both are present they must agree.

    `actor` is the human this console is acting as, and it is a term in the predicate rather than
    decoration: arm 3 of `agent_work_on` refuses a row somebody ELSE has already reported on, and
    the operator's own second `done` after his own `reopen` is the case that must survive it. It
    defaults to `operator` so that a caller which does not know defaults to the single-human host
    this instance is, and a second human's report is then treated as another party's -- which is
    the fail-closed direction and is also correct: fabricating another HUMAN's report is the same
    act as fabricating an agent's.
    """
    assert_allowed(room, verb)
    if verb != "done":
        return
    if not item:
        raise ItemRefusal("`done` needs a work item, and this request named none.")
    card_id = str(item.get("id") or "").strip()
    target = str(acting_on or "").strip() or card_id
    if card_id and target != card_id:
        raise ItemRefusal(
            f"this request shows the card for {card_id} and would run `done` on {target}. The "
            f"guard and the verb must be about the same row; refusing rather than checking one "
            f"and acting on the other."
        )
    if not target:
        raise ItemRefusal("`done` needs a work item id, and this request carried none.")

    row = _row_behind(target)
    if row is None:
        raise ItemRefusal(
            f"there is no brain.work_item {target!r}. `done` from the console is a statement "
            f"about a row, and this request names none."
        )
    held = (row.get("claimed_by") or "").strip()
    if row.get("agent_claimable") or held:
        why = ("the fleet may claim it" if row.get("agent_claimable")
               else f"{held} is holding it")
        raise ItemRefusal(
            f"{target} is agent_claimable={row.get('agent_claimable')!r}, "
            f"claimed_by={held or 'nobody'!r}, state={row.get('state')!r}, "
            f"actor_type={row.get('actor_type') or 'unset'!r}: {why}. `done` from the console is "
            f"legitimate only on work no agent will report on -- not agent-claimable and held by "
            f"nobody -- because on an agent's item it would fabricate that agent's report, which "
            f"is what the prohibition was about. The verb for an agent's finished work is "
            f"`accept work`. These columns are read from brain.work_item, not from the card: the "
            f"card's `actor_type` was stamped by the arm the row arrived on (task 0427)."
        )

    # ------------------------------------------------------------------ row 0399: the third term
    #
    # Neither column above can see the past. `reopen` empties `claimed_by` and `agent_claimable`
    # was already false, so an agent's finished work reads here as the operator's own the moment
    # he presses `Send back`. This asks the question those two cannot: has an agent ALREADY worked
    # it. The evidence, and why each arm is there, is above `agent_work_on`.
    worked = agent_work_on([target], acting_as=actor).get(target)
    if worked and worked["worked"]:
        raise ItemRefusal(
            f"{target} has been worked by an agent: {worked['why']}. `done` from the console "
            f"writes brain.work_item.result, which is the column every later surface reads as "
            f"WHAT THE AGENT REPORTED, so on this row it would file that agent's report in the "
            f"operator's words and take the row off the queue as finished. It is refused however "
            f"the row's own columns read right now: agent_claimable="
            f"{row.get('agent_claimable')!r}, claimed_by={held or 'nobody'!r}, "
            f"state={row.get('state')!r}. `Send back` runs `reopen`, which CLEARS `claimed_by` by "
            f"design, so those two columns say only that no agent is holding it at this instant. "
            f"If the work is finished and good, the verb is `accept work`. If it is not, it has "
            f"already been sent back and it is waiting to be worked again -- and if nothing will "
            f"pick it up, `swarm set {target} agent_claimable true --as-operator` is what hands "
            f"it back to the fleet. Read from brain.run and the append-only brain.thread, never "
            f"from the card. Row 0399."
        )


def dispatch(room: str, verb: str, *, item: dict | None = None, actor: str = "operator", **kw):
    """The console's ONLY write path. Both guards, then the store's one implementation.

    No surface reimplements a transition, so this function contains no state change of its own:
    it decides whether the call is permitted and then hands it to `store.apply`, which is the
    same call the CLI, the runner and the MCP server make.
    """
    assert_allowed_on(room, verb, item, acting_on=str(kw.get("id") or ""), actor=actor)
    if verb in UNREGISTERED_OWNER and verb not in store.registered():
        raise VerbNotBuiltYet(
            f"{verb!r} is not registered yet. {UNREGISTERED_OWNER[verb]} owns it. The console "
            f"will not stand in for a lane's transition: a second implementation here is exactly "
            f"the drift the narrow waist exists to prevent."
        )
    return store.apply(verb, actor=actor, **kw)


def audit() -> dict:
    """Every room, its verbs, and every registered verb no room may call.

    This is the surface an auditor reads instead of grepping templates for buttons.
    """
    registry = store.registered()
    reachable = set().union(*ROOM_VERBS.values())
    return {
        "rooms": {r: sorted(v) for r, v in ROOM_VERBS.items()},
        "registered": sorted(registry),
        "reachable_from_any_room": sorted(reachable),
        "registered_and_unreachable": sorted(set(registry) - reachable),
        "named_but_unregistered": sorted(reachable - set(registry)),
        "study_verb_count": len(ROOM_VERBS["study"]),
    }
