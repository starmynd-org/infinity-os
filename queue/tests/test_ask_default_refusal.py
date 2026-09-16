#!/usr/bin/env python3
"""A DEFAULT MAY NEVER CARRY A HARD FLAG'S ACTION. Enforced at `ask` time, not by convention.

The rule that carries the most weight in this lane, and the consequence that makes it worth the
machinery:

    **Silence can only ever ship the reversible branch. Bounded, not by construction:
    `no action; all of it` is measured as still accepted.**

That is what makes a bad week degrade safely rather than dangerously. It is also what lets the
pending-defaults surface be honestly calm instead of cosmetically softened: the operator can be
told "seven defaults fire at 19:00" without that sentence hiding an irreversible act.

The second sentence is not modesty, it is the finding. Task 0154 proposed the structural closure
this suite now tests as making the first sentence true BY CONSTRUCTION, then broke it with twenty
strings built only from the words it allows. Nineteen are closed; one is not. A claim that
measures cleaner than the property it describes is the exact defect the D9 pass found, so this
file states the bound everywhere it states the claim.

Enforcement is a trigger on `brain.question`, so it holds for `swarm ask`, for the console, for
an MCP wrapper and for a human typing INSERT at a psql prompt. Every one of those is tested
below, including the superuser, because a gate that only refuses the application role is not a
gate.

Run:  python3 queue/tests/test_ask_default_refusal.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "queue"))
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("QUEUE_SCRATCH_DB", "brain_queue_scratch"))
os.environ.pop("SWARM_PARENT_TASK", None)

# Build or reconcile the scratch database BEFORE this suite asserts anything. Task 0212. Running a
# suite by itself is what a brief asks for, and by itself this one used to die in reset() on a
# TRUNCATE against a database nobody had built.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile                 # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])

import psycopg2                                          # noqa: E402
import store                                             # noqa: E402
from human_queue import reads                            # noqa: E402
from human_queue import transitions as queue_transitions  # noqa: E402,F401
from human_queue.defaults import (SUGGESTIONS, act_verbs_in, bare_imperatives_in,  # noqa: E402
                                  check_default, is_null_branch, residue_in)
from swarm_engine import transitions as engine           # noqa: E402,F401

SCRATCH = str(ROOT / "queue/bin/queue-scratch-db.sh")
SWARM = str(ROOT / "engine/bin/swarm")
PASS, FAIL = 0, 0

ACT_SHAPED = "send Mick the corrected margin table"

# CHANGED IN TASK 0145, and the change is the whole point of migration 0009. This constant used to
# read "stage the reply in drafts, do not send", which the classifier now REFUSES: it names `send`.
# A null branch may not name the act even to forbid it, because a negation is bypassable and an
# absence is not. The replacement says exactly the same thing and names nothing.
NULL_BRANCH = "stage only; hold until I answer"

# The seven strings `brain.default_is_null_branch` classified as null branches on 2026-08-16,
# copied from outputs/2026-08-16-D9-adversarial/FINDINGS.md item 2. The first of them fired end to
# end on an EXTERNAL task and `brain.queue_default_event` recorded `external=t null_branch=t` for a
# default that says *email the client the quote*.
D9_BYPASSES = (
    "stage only. do not be shy, email the client the quote",
    "leave it alone, but do not be late, transfer the funds",
    "escalate to me, but do not block on it, deploy the fix",
    "pause; do not overthink it, publish the post",
    "nothing is sent without approval; publish the staged page",
    "defer. never mind, ship it",
    "hold. Do not wait for me, send the invoice to Mick.",
)

# D6b's own false accept, found while tuning and fixed by bounding the negator window. It was
# already refused before task 0145 and it must stay refused: this is the regression guard for the
# fix that 0145 replaced.
D6B_FALSE_ACCEPT = "do not send anything, then send the summary Monday"

# Invented against the 0145 fix itself, one per class the fix had to close. Every one of these was
# ACCEPTED by an earlier draft of migration 0009 and is refused by the version that shipped.
INVENTED_BYPASSES = (
    ("e-dropping inflection: wire + ing is wiring",
     "take no action beyond wiring the funds"),
    ("consonant doubling: ship + ing is shipping",
     "hold. the deck is shipping Monday"),
    ("letter-spacing splits the word",
     "stage only, then p u b l i s h the page"),
    ("a separator inside the word",
     "hold. e-mail the client the quote"),
    # Written as escapes, not as the characters. A homoglyph is invisible in a diff by definition
    # and a zero-width space is invisible everywhere, so spelling them out is the only way a
    # reader can see what the case actually tests.
    ("a Cyrillic homoglyph inside the word",
     "hold. \u0455end the quote to the client"),
    ("a zero-width space inside the word",
     "hold. se\u200bnd the quote to the client"),
    ("a synonym the vocabulary had not listed",
     "hold. mail the cheque to Mick"),
    ("an abbreviation that is an act: cc",
     "hold, and cc Mick on the thread"),
    ("the object sits inside the verb phrase",
     "hold, then let Mick have the file"),
    ("a phrasal act: loop someone in",
     "hold, then loop Mick in"),
    ("taking a thing live, said four different ways",
     "hold, then take it live"),
    ("an idiom for shipping",
     "stage only, then flip the switch"),
    ("the act as a gerund at the end of a null-branch sentence",
     "no action other than handing Mick the file"),
    # MOVED HERE FROM `KNOWN_RESIDUAL` BY TASK 0154, which is what the instruction on that
    # constant said to do when a residual closes: move it, never delete it. These six were the
    # measured residual of migration 0009 -- a plainly-stated outward act whose verb is simply not
    # in the vocabulary, so `act_verbs_in` finds NOTHING in any of them and they were accepted as
    # null branches on an external task. Migration 0011 refuses all six WITHOUT NAMING ONE OF
    # THEIR VERBS: what carries the act is the object and the recipient, and an object and a
    # recipient are residue. They are still refused for the reason 0145 could not use, so if a
    # later change re-opens one, it fails here.
    ("a synonym the act vocabulary does not name -> residue {cheque, mike}",
     "hold; get the cheque to Mick"),
    ("a synonym in the passive -> residue {client, monday}",
     "no action; the client hears from us Monday"),
    ("nominalised, no verb at all -> residue {delivery, mike, monday}",
     "no action; delivery to Mick Monday"),
    ("not English -> residue {envoyer, facture, la, mike}",
     "hold; envoyer la facture a Mick"),
    ("a light verb plus a recipient and a time -> residue {9, mike}",
     "no action; it goes to Mick at 9"),
    ("a shell command, no English verb -> residue {-rf, bucket, rm}",
     "hold. rm -rf the staging bucket"),
)

# THE CLASS THAT BROKE THE CLOSURE, and it broke it before it shipped rather than after. Task 0154
# attacked its own residue check with twenty strings built ONLY from words the closed lists allow;
# seventeen were accepted, every one of them a BARE IMPERATIVE WITH A PRONOUN OBJECT. They name no
# object and no recipient, so a residue check is blind to them BY CONSTRUCTION, and on a task whose
# brief already says what to do, `do it` ships exactly the act the gate exists to stop. The shipped
# 0009 classifier accepts them too, so this is a second class, not a side effect of the closure.
#
# Nineteen of the twenty are refused by migration 0011's clause-head rule. The twentieth is
# `KNOWN_RESIDUAL` below.
BARE_IMPERATIVES = (
    "no action; do it",
    "hold; make it so",
    "no action; proceed",
    "no action; continue",
    "hold; you know what to do",
    "no action; they take it",
    "no action; you get it",
    "hold; let them have it",
    "no action; it goes out",
    "hold. go",
    "no action; you know",
    "hold; it happens",
    "no action; you have it",
    "hold; give it to them",
    "no action; move it",
    "hold; you take it from here",
    "no action; touch nothing, then do it",
    "hold; the next one goes",
    "hold; you and I know what happens",
)

# What the fix does NOT close, measured after every hardening above, kept in the suite so the
# residual is a fact this repo states rather than a surprise the next attacker finds. This is
# ACCEPTED as a null branch on a flagged task and it should not be.
#
# `no action; all of it` is the last measured member of the bare-imperative class. Its residue is
# empty (`all`, `of` and `it` are function words) and its clause heads are `no` and `all`, neither
# of which is a verb, so NEITHER of migration 0011's two conditions can see it. The act is carried
# entirely by a quantifier pointing at the task's own brief. It is tracked as swarm task 0204
# rather than folded in here and called closed: one unsafe accept that is written down is a
# finding, and one that is described as none is the thing this program exists to prevent.
#
# If a later change closes this, this test FAILS and the entry should be MOVED to
# BARE_IMPERATIVES rather than deleted. A test that silently stops covering a case is how a
# residual becomes a surprise.
KNOWN_RESIDUAL = (
    "no action; all of it",
)


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def refuses(fn, *a, **kw) -> str:
    try:
        fn(*a, **kw)
        return ""
    except Exception as e:                                          # noqa: BLE001
        return str(e) or e.__class__.__name__


def su(sql, params=None):
    pw = (Path.home() / ".brain-postgres-secrets"
          / "brain-postgres-bootstrap-superuser").read_text().strip()
    conn = psycopg2.connect(host="127.0.0.1", port=5432, dbname=os.environ["BRAIN_PG_DB"],
                            user="postgres", password=pw)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
    finally:
        conn.close()


def reset():
    subprocess.run([SCRATCH, "psql", "-q", "-c",
                    "TRUNCATE brain.queue_item, brain.queue_defer, brain.queue_bump, "
                    "brain.queue_calibration, brain.queue_default_event, brain.recommendation, "
                    "brain.thread, brain.question, brain.agent, brain.work_item CASCADE; "
                    "SELECT setval('brain.item_id_seq', 1, false);"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_an_unflagged_task_may_state_any_default():
    reset()
    t = store.apply("post", title="rebuild the local index", lane="data")
    q = store.apply("ask", question="rebuild now or after the backfill?", agent="T1",
                    task=t["id"], default="rebuild it now")
    check("an unflagged task keeps every default it could state before", q["id"].startswith("q"))
    check("and the gate says so for the same reason", check_default(t["id"], "rebuild it now")["ok"])


def test_an_external_task_refuses_an_act_shaped_default():
    reset()
    t = store.apply("post", title="Acme margin correction", lane="client", external="true")
    msg = refuses(store.apply, "ask", question="do I send Mick the correction?", agent="T1",
                  task=t["id"], default=ACT_SHAPED)
    check("`ask` refuses an act-shaped default on an external task",
          "act-shaped" in msg and "hard flag" in msg, msg[:160] or "NOT REFUSED")
    with store.read("runtime") as s:
        n = s.scalar("SELECT count(*) FROM brain.question")
    check("and the question was NOT written: the whole verb rolled back", int(n) == 0, f"{n} rows")
    check("the same call with the null branch is accepted",
          store.apply("ask", question="do I send Mick the correction?", agent="T1",
                      task=t["id"], default=NULL_BRANCH)["id"].startswith("q"))


def test_a_canon_touching_task_refuses_it_too():
    reset()
    t = store.apply("post", title="rewrite _system/priority rules", lane="canon",
                    canon_touching="true")
    msg = refuses(store.apply, "ask", question="apply the rule edit?", agent="T1",
                  task=t["id"], default="apply the edit and commit it")
    check("`ask` refuses an act-shaped default on a canon-touching task",
          "act-shaped" in msg, msg[:160] or "NOT REFUSED")


def test_the_flag_is_inherited_by_or_up_the_whole_chain():
    """The gate reads the RECURSIVE view, so a laundered flag two hops down is still a flag."""
    reset()
    gp = store.apply("post", title="client engagement", lane="client", external="true")
    p = store.apply("post", title="the deck", lane="client", parent=gp["id"])
    child = store.apply("post", title="the cover slide", lane="client", parent=p["id"])
    msg = refuses(store.apply, "ask", question="send the cover slide?", agent="T1",
                  task=child["id"], default=ACT_SHAPED)
    check("a grandchild of an external task refuses an act-shaped default",
          "act-shaped" in msg and "external" in msg, msg[:160] or "NOT REFUSED")


def test_the_cli_refuses_it(qid_free_task=None):
    reset()
    t = store.apply("post", title="Initech invoice", lane="client", external="true")
    env = {**os.environ}
    r = subprocess.run([SWARM, "ask", "do I send the invoice?", "--from", "T1",
                        "--task", t["id"], "--default", "send the invoice"],
                       capture_output=True, text=True, env=env)
    both = r.stderr + r.stdout
    check("the CLI exits non-zero", r.returncode != 0, f"rc={r.returncode}")
    check("the CLI says why, in words", "act-shaped" in both, both[:200])
    # The hint has now changed with BOTH migrations and had to, twice. 0009's old text recommended
    # "prepare but do not send", which the act scan refuses; 0011's text dropped "take no action",
    # which the gated classifier refuses because `take` is a light verb. Guidance a system refuses
    # is worse than no guidance: it teaches the writer that the gate is arbitrary, and the next
    # thing they do is route around it. This assertion is the thing that stops either from
    # silently drifting back.
    check("and it states the null branch the writer should use instead",
          "hold, stage only, no action, ask again at the next checkpoint" in both, both[:300])
    check("and it names the act verbs it refused on, so the refusal is checkable against the text",
          "Refused for naming: invoice, send" in both, both[:400])
    # Was a FINDING here (task 0116, engine lane): the refusal was correct and the rendering was
    # not, because `swarm_engine.cli` caught `VerbError` only and a trigger's refusal reached the
    # operator as a Python traceback with the real sentence on the last three lines. `cli.main`
    # now catches `psycopg2.Error` and prints the message and its HINT. The assertion is kept,
    # inverted, rather than deleted: this is the surface where a regression would show, and a
    # refusal that reads like a crash in the CLI is a refusal that gets ignored.
    check("the refusal reads as a refusal, with no traceback",
          "Traceback" not in both, both[:300])
    check("and the whole refusal is the message and its hint, nothing else",
          len(both.strip().splitlines()) == 2, f"{len(both.strip().splitlines())} lines: {both[:300]}")
    print("      --- the refusal, as the CLI printed it ---")
    for line in both.strip().splitlines():
        print(f"      {line}")


def test_the_superuser_is_refused_as_well():
    reset()
    t = store.apply("post", title="Acme margin correction", lane="client", external="true")
    msg = refuses(su, "INSERT INTO brain.question (asked_by, work_item_id, text, "
                      "default_if_unanswered) VALUES ('postgres', %s, 'q', %s)",
                  (t["id"], ACT_SHAPED))
    check("a raw INSERT by the superuser is refused by the trigger",
          "act-shaped" in msg, msg[:160] or "NOT REFUSED")
    msg = refuses(su, "UPDATE brain.question SET default_if_unanswered = %s WHERE id = "
                      "(SELECT id FROM brain.question LIMIT 1)", (ACT_SHAPED,))
    check("and an UPDATE that swaps a good default for a bad one is refused too",
          "act-shaped" in msg or msg == "", msg[:160])


def test_one_classifier_so_the_preflight_cannot_drift():
    """`check_default` calls the database's classifier; it does not reimplement it.

    D4 kept `signal_level` in Python and SQL and had to write a parity test to hold the two
    together. This lane does not take that debt, and this test is what proves it rather than
    asserting it: the same battery through both doors, and any drift is a failure here.

    THERE ARE TWO CLASSIFIERS SINCE MIGRATION 0011 and the risk this test covers is now sharper:
    a preflight that asks the LAXER one on a flagged task would say yes where the write path says
    no, which is worse than no preflight because it is trusted. `check_default` derives the flag
    from the task, exactly as the trigger does, and this task is `external`.
    """
    reset()
    t = store.apply("post", title="external thing", lane="client", external="true")
    cases = [(ACT_SHAPED, False), (NULL_BRANCH, True),
             ("hold until I answer", True),
             # WAS `True` BEFORE TASK 0145. It names `send`, so it is refused now, and this line
             # is the cost of the fix written down where a reader trips over it rather than in a
             # report nobody opens. A null branch never needs to name the act it is not doing.
             ("prepare the deck but do not send it", False),
             ("publish the page", False),
             (D6B_FALSE_ACCEPT, False),
             ("no action; ask again at the next checkpoint", True),
             # WAS `True` BEFORE TASK 0154, on the ungated classifier, and it is the whole cost of
             # option (C) in one line: `take` is a light verb, so it is residue on a flagged task.
             # It was this system's OWN suggested text until this commit changed `SUGGESTIONS` in
             # the same breath as the migration.
             ("take no action", False),
             # The measured residual of migration 0009, closed by 0011 without naming its verb.
             ("hold; get the cheque to Mick", False),
             # The class that broke the closure before it shipped. Refused by the clause-head rule.
             ("no action; do it", False)]
    drift = []
    for text, expected_ok in cases:
        pre = check_default(t["id"], text)["ok"]
        wrote = refuses(store.apply, "ask", question="?", agent="T1", task=t["id"],
                        default=text) == ""
        if pre != wrote or pre != expected_ok:
            drift.append((text, expected_ok, pre, wrote))
        if wrote:
            subprocess.run([SCRATCH, "psql", "-q", "-c",
                            "UPDATE brain.work_item SET state='inbox', blocked_on='' "
                            "WHERE id='" + t["id"] + "'; DELETE FROM brain.question;"],
                           check=True, stdout=subprocess.DEVNULL)
    check("the preflight and the write path agree on every case, both matching intent",
          not drift, f"drift: {drift}")


def test_a_flag_raised_later_is_caught_by_doctor_and_the_fire_refuses():
    """The one case a write-time trigger cannot see, so it is covered by a check and a refusal.

    Hard flags inherit by OR and may be RAISED at any time. A default that was legal when it was
    written becomes act-shaped on a task that never changed. Nothing about the question row
    changes, so no trigger fires: this is the gap, and it is closed at the other end.
    """
    reset()
    parent = store.apply("post", title="a parent", lane="client")
    t = store.apply("post", title="a child", lane="client", parent=parent["id"])
    q = store.apply("ask", question="send it?", agent="T1", task=t["id"], default=ACT_SHAPED)
    check("the default was legal when it was written", q["id"].startswith("q"))
    store.apply("set", id=parent["id"], key="external", value="true")  # raised LATER
    findings = [f for f in reads.doctor()["findings"] if f["kind"] == "default-now-act-shaped"]
    check("`queue doctor` reports it as critical",
          len(findings) == 1 and findings[0]["severity"] == "critical",
          str(findings))
    msg = refuses(store.apply, "queue default fire", qid=q["id"], checkpoint="19:00")
    check("and `queue default fire` refuses to ship it",
          "act-shaped" in msg and "raised after" in msg, msg[:180] or "NOT REFUSED")
    with store.read("runtime") as s:
        answered = s.scalar("SELECT answer FROM brain.question WHERE id = %s", (q["id"],))
    check("so silence shipped nothing", answered is None, str(answered))


def test_the_d9_bypass_battery_is_refused():
    """The seven strings that defeated 0007's classifier, and D6b's own false accept beside them.

    This is the regression the task exists for. Each of the seven leads with a null-branch phrase
    and appends the act, so the negator sits in a clause it does not govern and 0007's 34-character
    pre-verb window excused the act verb it never governed. There is no window now.
    """
    reset()
    ext = store.apply("post", title="Acme margin correction", lane="client", external="true")
    got_through = []
    for text in D9_BYPASSES:
        verdict = check_default(ext["id"], text)
        if verdict["ok"]:
            got_through.append(text)
        print(f"      refused: {str(verdict['act_verbs']):24s} {text}")
    check("all seven FINDINGS.md bypasses are refused on an external task",
          not got_through, f"STILL ACCEPTED: {got_through}")
    check("D6b's original false accept is still refused, so the old fix did not regress",
          not check_default(ext["id"], D6B_FALSE_ACCEPT)["ok"],
          D6B_FALSE_ACCEPT)
    check("and every one of them is refused for naming an act verb, not for being unclassifiable",
          all(act_verbs_in(t) for t in D9_BYPASSES + (D6B_FALSE_ACCEPT,)),
          str([(t, act_verbs_in(t)) for t in D9_BYPASSES if not act_verbs_in(t)]))


def test_the_bypasses_this_lane_invented_against_its_own_fix():
    """Nineteen attacks written against the fix, not against the thing the fix replaced.

    Thirteen were ACCEPTED by an earlier draft of migration 0009 and named a class that draft had
    not closed: English inflection the hand-written suffix group could not generate, and
    obfuscation that writes the act verb differently.

    The last six were the MEASURED RESIDUAL of 0009 as it shipped, moved here by task 0154 when
    migration 0011 closed them. They are the interesting ones, because 0011 refuses them without
    naming one of their verbs: `act_verbs_in` still finds NOTHING in any of the six, and the
    refusal comes from the object and the recipient being words the gate does not recognise.
    """
    reset()
    ext = store.apply("post", title="client engagement", lane="client", external="true")
    got_through = []
    for why, text in INVENTED_BYPASSES:
        verdict = check_default(ext["id"], text)
        state = "ACCEPTED" if verdict["ok"] else "refused"
        if verdict["ok"]:
            got_through.append((why, text))
        print(f"      {state}  {why}")
        print(f"                {text!r} -> acts {verdict['act_verbs']} "
              f"residue {verdict.get('residue')}")
    check(f"all {len(INVENTED_BYPASSES)} invented bypasses are refused",
          not got_through, f"STILL ACCEPTED: {got_through}")


def test_the_bare_imperative_class_is_closed_by_the_clause_head_rule():
    """`no action; do it` contains no content word, so a residue check cannot see it.

    This is the class task 0154 broke its own proposed closure with, and it is a class the SHIPPED
    0009 classifier accepts too: seventeen of twenty strings built only from allowed words were
    accepted as null branches on a flagged task. On a task whose brief already says what to do,
    `do it` ships precisely the act the gate exists to stop.

    Migration 0011 closes these with a rule rather than a word list: a clause may not OPEN with a
    verb that is not a null-branch verb. `do not send` stays legal because `do` there is the
    auxiliary of a negator; `do it` does not, because `do` there is the main verb.
    """
    reset()
    ext = store.apply("post", title="client engagement", lane="client", external="true")
    got_through = [t for t in BARE_IMPERATIVES if check_default(ext["id"], t)["ok"]]
    check(f"all {len(BARE_IMPERATIVES)} bare imperatives are refused on a flagged task",
          not got_through, f"STILL ACCEPTED: {got_through}")
    # THREE OF THE NINETEEN WERE ALREADY REFUSED, and by the act scan rather than by anything this
    # migration added: `let them have`, `go out` and `give` are act phrases in 0009's vocabulary.
    # The number is asserted rather than the blanket claim it is tempting to write, because the
    # blanket claim is FALSE and this test failed on it once: writing "the ungated classifier
    # accepts them all" would have been a sentence that sounds stronger and is not true.
    CAUGHT_BY_THE_ACT_SCAN = ("hold; let them have it", "no action; it goes out",
                              "hold; give it to them")
    already = tuple(t for t in BARE_IMPERATIVES if not is_null_branch(t))
    check("exactly three of them were already refused, and by the ACT SCAN naming an act phrase, "
          "not by anything this migration added",
          already == CAUGHT_BY_THE_ACT_SCAN, f"{already}")
    new_ones = [t for t in BARE_IMPERATIVES if is_null_branch(t)]
    check(f"so the clause-head rule is what closes the other {len(new_ones)}, which the shipped "
          f"ungated classifier accepts to this day",
          len(new_ones) == len(BARE_IMPERATIVES) - 3 and all(not act_verbs_in(t)
                                                             for t in new_ones),
          str([(t, act_verbs_in(t)) for t in new_ones if act_verbs_in(t)]))
    print(f"      {len(BARE_IMPERATIVES)} refused on a flagged task. {len(new_ones)} of them are "
          f"still accepted by brain.default_is_null_branch and name no act verb at all,")
    print("      which is what an UNFLAGGED task is still allowed to say, and is what made this")
    print("      a second bypass class rather than a restatement of the act scan.")


def test_the_residual_is_measured_and_stated_rather_than_hidden():
    """WHAT THE FIX DOES NOT CLOSE. This test asserts a WEAKNESS, on purpose.

    Migration 0009 turned an unbounded bypass into a vocabulary-coverage bypass. Migration 0011
    turned that into a recognised-word bypass: an attacker can no longer reach for a synonym,
    because a synonym is residue whether or not anyone listed it. What is left is NOT nothing, and
    the reason this test exists is that the brief which proposed 0011 claimed it was.

    `no action; all of it` is accepted. Its residue is empty and neither of its clause heads is a
    verb, so both of 0011's conditions are blind to it by construction, and the act is carried by
    a quantifier pointing at the task's own brief. Tracked as swarm task 0204.

    If a later change closes this, this test fails and the entry should be MOVED to
    BARE_IMPERATIVES rather than deleted. A test that silently stops covering a case is how a
    residual becomes a surprise.
    """
    reset()
    ext = store.apply("post", title="client engagement", lane="client", external="true")
    still_open = []
    for text in KNOWN_RESIDUAL:
        if check_default(ext["id"], text)["ok"]:
            still_open.append(text)
            print(f"      OPEN, known: {text}")
    check("the residual is exactly what this lane measured it to be, no wider and no narrower",
          len(still_open) == len(KNOWN_RESIDUAL),
          f"{len(KNOWN_RESIDUAL) - len(still_open)} of the known residual is now closed: move it "
          f"to BARE_IMPERATIVES. Closed: {[t for t in KNOWN_RESIDUAL if t not in still_open]}")
    print("      ^ this fires on an external task. It names no act verb, leaves no residue and")
    print("        opens no clause with a verb, so nothing in this file can catch it and neither")
    print("        can queue_default_breach. Do not read a clean override_after_default() as")
    print("        proof, and do not write `by construction` anywhere near this gate.")


def test_the_system_never_recommends_a_phrasing_it_refuses():
    """`defaults.SUGGESTIONS` is what the refusal tells the writer to use instead.

    Guidance a system refuses is worse than no guidance: it teaches the writer that the gate is
    arbitrary, and the next thing they do is route around it. Two of the five entries named `send`
    before task 0145 and were removed; a third, `take no action`, was replaced by `no action` in
    task 0154 when migration 0011 made the light verbs residue. This test is what stops any of
    them going back in, and it is checked on an EXTERNAL task on purpose: the suggestions are what
    a writer is told to use at the exact moment the strictest classifier is the one refusing them.
    """
    reset()
    ext = store.apply("post", title="external thing", lane="client", external="true")
    bad = [(s, act_verbs_in(s), residue_in(s), bare_imperatives_in(s)) for s in SUGGESTIONS
           if act_verbs_in(s) or not check_default(ext["id"], s)["ok"]]
    check(f"all {len(SUGGESTIONS)} suggested null branches survive the GATED classifier and name "
          f"no act verb", not bad, str(bad))
    hint = refuses(store.apply, "ask", question="?", agent="T1", task=ext["id"],
                   default=ACT_SHAPED)
    # The trigger's HINT is a second copy of this guidance, written in SQL, and a copy is a thing
    # that drifts. Each phrase is checked through the same gate the writer will hit.
    hinted = ("hold", "stage only", "no action", "ask again at the next checkpoint")
    check("and the refusal's own hint recommends only phrasings the gated classifier accepts",
          all(not act_verbs_in(p) and check_default(ext["id"], p)["ok"] for p in hinted),
          str([(p, residue_in(p), bare_imperatives_in(p)) for p in hinted
               if not check_default(ext["id"], p)["ok"]]) + " | " + hint[:200])
    check("and the hint the trigger actually printed carries them, so the SQL copy has not drifted",
          "hold, stage only, no action, ask again at the next checkpoint" in hint, hint[:300])


def test_the_invariant_holds_over_every_case_in_this_file():
    """for every text T: default_is_null_branch(T) => act_verbs_in(T) = {}

    The accepted set is now checkable rather than arguable, and this is the check. A future edit
    that reintroduces an excuse mechanism -- a negator window, an article test, anything that lets
    an act verb through on the strength of a nearby word -- fails here rather than passing quietly.
    """
    reset()
    every = (list(D9_BYPASSES) + [D6B_FALSE_ACCEPT] + [t for _, t in INVENTED_BYPASSES]
             + list(KNOWN_RESIDUAL) + list(BARE_IMPERATIVES) + list(SUGGESTIONS)
             + [ACT_SHAPED, NULL_BRANCH, "hold until I answer", "stage only", "take no action",
                "no action; ask again at the next checkpoint",
                "the task stays blocked until you answer", "", "   "])
    violations = [(t, act_verbs_in(t)) for t in every
                  if is_null_branch(t) and act_verbs_in(t)]
    check(f"no accepted text names an act verb, over all {len(every)} cases in this file",
          not violations, str(violations))
    # THE SECOND INVARIANT, added by task 0154: the gated classifier is strictly stronger. It may
    # never accept something the ungated one refuses, on any text. That is what makes "flagged
    # tasks are held to a higher standard" a checkable property rather than a description of two
    # functions that happen to be written that way, and it is what would fail if someone later
    # added a permissive arm to the gated path.
    weaker = [t for t in every if is_null_branch(t, gated=True) and not is_null_branch(t)]
    check(f"the gated classifier never accepts what the ungated one refuses, over all "
          f"{len(every)} cases", not weaker, str(weaker))


def test_the_external_bypass_is_refused_through_every_door():
    """The exact FINDINGS.md string, end to end, on an `external` task, through all three doors.

    D6b proved the honest act-shaped default was refused through the verb, the CLI and a superuser
    INSERT. The bypass is the case that got PAST that, so it is proven through the same three doors
    rather than only against the classifier: a gate that refuses the application role and not the
    superuser is not a gate, and a classifier fixed without the write path re-tested is a fix
    nobody has seen work.
    """
    reset()
    bypass = D9_BYPASSES[0]
    t = store.apply("post", title="Acme quote", lane="client", external="true")

    msg = refuses(store.apply, "ask", question="do I send the quote?", agent="T1",
                  task=t["id"], default=bypass)
    check("door 1, the verb: `ask` refuses the bypass on an external task",
          "act-shaped" in msg and "external" in msg, msg[:200] or "NOT REFUSED")
    check("and it names the verb it refused on", "email" in msg, msg[:300])
    with store.read("runtime") as s:
        n = s.scalar("SELECT count(*) FROM brain.question")
    check("the question was NOT written: the whole verb rolled back", int(n) == 0, f"{n} rows")
    print(f"      --- the refusal, from the verb ---\n      {msg.strip().splitlines()[0][:150]}")

    r = subprocess.run([SWARM, "ask", "do I send the quote?", "--from", "T1",
                        "--task", t["id"], "--default", bypass],
                       capture_output=True, text=True, env={**os.environ})
    both = r.stderr + r.stdout
    check("door 2, the CLI: exits non-zero and refuses in words",
          r.returncode != 0 and "act-shaped" in both and "Traceback" not in both,
          f"rc={r.returncode} {both[:200]}")

    msg = refuses(su, "INSERT INTO brain.question (asked_by, work_item_id, text, "
                      "default_if_unanswered) VALUES ('postgres', %s, 'q', %s)",
                  (t["id"], bypass))
    check("door 3, the superuser: a raw INSERT is refused by the trigger",
          "act-shaped" in msg, msg[:200] or "NOT REFUSED")

    with store.read("runtime") as s:
        n = s.scalar("SELECT count(*) FROM brain.question")
        fired = s.scalar("SELECT count(*) FROM brain.queue_default_event")
    check("after all three doors, nothing was written and nothing fired",
          int(n) == 0 and int(fired) == 0, f"{n} questions, {fired} events")


def test_the_record_can_disagree_with_the_classifier():
    """`queue_default_event.null_branch` recorded `t` for a default that shipped an act.

    So the measurement that is supposed to catch this AGREED WITH THE BUG, and both the
    null-branch claim and the override-after-default rate read clean while an external act had
    shipped. A metric that cannot disagree with the thing it measures is not a metric.

    Three separate things are proven here: the record carries what was SHIPPED and not what the
    classifier believed; the caller cannot under-report it; and the self-certifying row is
    unrepresentable rather than merely unlikely.
    """
    reset()
    plain = store.apply("post", title="local index", lane="data")
    q = store.apply("ask", question="rebuild or email the summary?", agent="T1",
                    task=plain["id"], default="email the client the quote")
    out = store.apply("queue default fire", qid=q["id"], checkpoint="19:00")
    with store.read("runtime") as s:
        row = s.one("SELECT null_branch, shipped_act_verbs, default_text, external "
                    "FROM brain.queue_default_event WHERE question_id = %s", (q["id"],))
    check("an acting default on an UNFLAGGED item still fires: that is allowed and unchanged",
          out["default_text"] == "email the client the quote", str(out))
    check("and the row records WHAT SHIPPED, from an independent scan of the text",
          sorted(row["shipped_act_verbs"]) == ["email", "quote"], str(row["shipped_act_verbs"]))
    check("beside the classifier's separate verdict, which is free to disagree with it",
          row["null_branch"] is False, str(row["null_branch"]))
    print(f"      queue_default_event: null_branch={row['null_branch']} "
          f"shipped_act_verbs={row['shipped_act_verbs']} text={row['default_text']!r}")

    # Two more real questions, because `queue_default_event.question_id` carries a foreign key and
    # a made-up id is refused before the trigger or the CHECK is ever reached.
    p2 = store.apply("post", title="second local index", lane="data")
    q2 = store.apply("ask", question="rebuild?", agent="T1", task=p2["id"], default="rebuild now")
    p3 = store.apply("post", title="third local index", lane="data")
    q3 = store.apply("ask", question="rebuild?", agent="T1", task=p3["id"], default="rebuild now")

    # The caller cannot under-report. A superuser naming an empty array gets the scan anyway.
    su("INSERT INTO brain.queue_default_event (question_id, work_item_id, checkpoint, "
       "default_text, producer, external, canon_touching, null_branch, shipped_act_verbs, "
       "fired_at) VALUES (%s, %s, '19:00', %s, 'postgres', false, false, false, '{}', now())",
       (q2["id"], p2["id"], "wire the funds to Mick"))
    with store.read("runtime") as s:
        forced = s.scalar("SELECT shipped_act_verbs FROM brain.queue_default_event "
                          "WHERE question_id = %s", (q2["id"],))
    check("a caller that names an empty array gets the scan written over it by the trigger",
          list(forced) == ["wire"], str(forced))

    # The lie is unrepresentable: null_branch=t beside an act verb violates the CHECK. This is the
    # 2026-08-16 row exactly -- external=t, null_branch=t, text naming `email` -- and under 0007's
    # classifier `queue default fire` would have hit this constraint and rolled the whole
    # transition back, so the default would not have fired at all.
    msg = refuses(su, "INSERT INTO brain.queue_default_event (question_id, work_item_id, "
                      "checkpoint, default_text, producer, external, canon_touching, "
                      "null_branch, fired_at) VALUES (%s, %s, '19:00', %s, 'postgres', true, "
                      "false, true, now())",
                  (q3["id"], p3["id"], "stage only. do not be shy, email the client the quote"))
    check("a row certifying a null branch while carrying an act verb is REFUSED by the CHECK",
          "queue_default_event_null_branch_is_actless" in msg, msg[:200] or "NOT REFUSED")
    print(f"      --- the record refusing to certify a lie ---\n      {msg.strip().splitlines()[0][:150]}")

    with store.read("runtime") as s:
        breach = s.scalar("SELECT count(*) FROM brain.queue_default_breach")
        lying = s.scalar("SELECT count(*) FROM brain.queue_default_event WHERE null_branch "
                         "AND cardinality(shipped_act_verbs) > 0")
    check("nothing on an unflagged item is a breach, and no self-certifying row exists anywhere",
          int(breach) == 0 and int(lying) == 0, f"{breach} breach, {lying} self-certifying")


def test_what_silence_can_ship_is_only_ever_reversible():
    """The property, stated as a query over everything that fired. This is the auditable form."""
    reset()
    plain = store.apply("post", title="local index", lane="data")
    ext = store.apply("post", title="client mail", lane="client", external="true")
    q1 = store.apply("ask", question="rebuild?", agent="T1", task=plain["id"],
                     default="rebuild it now")
    q2 = store.apply("ask", question="send?", agent="T1", task=ext["id"], default=NULL_BRANCH)
    store.apply("queue default fire", qid=q1["id"], checkpoint="19:00")
    store.apply("queue default fire", qid=q2["id"], checkpoint="19:00")
    o = reads.override_after_default()
    check("two defaults fired", o["fired"] == 2, str(o["fired"]))
    check("no default that fired on a FLAGGED item was anything but a null branch",
          not o["non_null_branch_fired"], str(o["non_null_branch_fired"]))
    check("the acting default on the unflagged item fired normally, as it should",
          o["acting_defaults_on_unflagged_items"] == 1,
          str(o["acting_defaults_on_unflagged_items"]))
    check("the claim reads as the property", "named an act verb" in o["claim"], o["claim"])
    # The claim must state its own limit. A sentence that reads as proof when it is evidence is
    # how the 2026-08-16 measurement came back clean while an external act had shipped.
    #
    # THE WORDING IS ASSERTED, not just its presence, and the assertion is the operator's decision
    # on task 0154 written into a test: the claim says BOUNDED and names the bound in the same
    # breath. "By construction" is the exact defect the D9 pass found -- the structural closure
    # was proposed as making this true by construction and was then broken by twenty strings built
    # only from words it allows -- so this system says what it has measured and not what it
    # wishes were true, and a future edit that upgrades the wording fails here.
    check("and it states what it cannot see, so it is not quotable as proof",
          "bounded, not true by construction" in o["claim"], o["claim"])
    check("and it names the surviving accept rather than rounding the residual to zero",
          "no action; all of it" in o["claim"], o["claim"])
    print(f"      {o['claim']}")


def main():
    print("test_ask_default_refusal.py  --  a default may never carry a hard flag's action")
    print(f"  store: {os.environ['BRAIN_PG_DB']} (scratch)\n")
    for fn in (test_an_unflagged_task_may_state_any_default,
               test_an_external_task_refuses_an_act_shaped_default,
               test_a_canon_touching_task_refuses_it_too,
               test_the_flag_is_inherited_by_or_up_the_whole_chain,
               test_the_cli_refuses_it,
               test_the_superuser_is_refused_as_well,
               test_one_classifier_so_the_preflight_cannot_drift,
               test_a_flag_raised_later_is_caught_by_doctor_and_the_fire_refuses,
               test_the_d9_bypass_battery_is_refused,
               test_the_bypasses_this_lane_invented_against_its_own_fix,
               test_the_bare_imperative_class_is_closed_by_the_clause_head_rule,
               test_the_residual_is_measured_and_stated_rather_than_hidden,
               test_the_system_never_recommends_a_phrasing_it_refuses,
               test_the_invariant_holds_over_every_case_in_this_file,
               test_the_external_bypass_is_refused_through_every_door,
               test_the_record_can_disagree_with_the_classifier,
               test_what_silence_can_ship_is_only_ever_reversible):
        print(f"=== {fn.__name__} ===")
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
