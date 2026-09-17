"""The Scope elicitation flow: the machine drafts, the operator demolishes.

The design guide's section 5 is the whole argument and it is worth restating, because every
decision in this file is downstream of one sentence in it: *a hurried human is a poor author and
an excellent critic*. So this room never shows the operator an empty definition-of-done form. It
shows him rows to kill.

A drafted row is `claim · check · threshold · verifier`, and it carries two more fields the guide
names explicitly:

  `could_pass_wrong`   the drafter's own statement of how this check could pass while the work is
                       wrong. A check that cannot say this has not been thought about.
  `prove_without_you`  "How will an agent prove this without you?" The one field the machine may
                       not skip.

What the drafter is, stated plainly rather than implied
-------------------------------------------------------
It is a **template drafter over the house rules**, not a language model. The console calls no
model and the surface declaration does not permit it to. That matters for honesty and it does not
weaken the flow, because the value the guide identifies is not authorship: it is giving the
critic something concrete to attack. A template row that is wrong in an obvious way is a better
prompt to the operator than a blank field, and it is a worse lie than a fabricated one because it
cannot pretend to knowledge it does not have.

Where it cannot honestly name a verifier it does not invent one. It marks the row
`verify-human` and says why, and that row then counts toward the line the guide asks for at post
time: *"2 unverifiable checks, expect this back in your Judge tier."* That is not a failure mode,
it is the mechanism. A lazy scope punishes itself one loop later, and the only job of the UI is
to say so before the operator pays for it rather than after.
"""

from __future__ import annotations

import re

# A verifier is a thing an agent can run without the operator in the room. Anything else is a
# person, and a check verified by a person is `verify-human` and says so.
VERIFY_AI = "verify-ai"
VERIFY_HUMAN = "verify-human"

_PATH = re.compile(r"(?:^|[\s(`'\"])((?:/|\./|~/)[\w./\-]{3,})")
_TABLE = re.compile(r"\b([a-z][a-z0-9_]{2,}\.[a-z][a-z0-9_]{2,})\b")
_NUMBER = re.compile(r"\b(\d[\d,]*(?:\.\d+)?)\s*(%|percent|rows?|items?|hours?|days?|k\b)?")
_COMMAND = re.compile(r"`([^`]{3,})`")
_DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")


def _row(claim, check, threshold, verifier, could_pass_wrong, prove_without_you="", why=""):
    return {
        "claim": claim,
        "check": check,
        "threshold": threshold,
        "verifier": verifier,
        "could_pass_wrong": could_pass_wrong,
        "prove_without_you": prove_without_you,
        "why_unverifiable": why,
        "kept": True,
    }


def draft(intent: str) -> list[dict]:
    """Read the intent for things that can be measured and propose a row for each.

    The rows are deliberately over-specific. An over-specific row is easy to correct and an
    over-general one is easy to nod at, and nodding is the failure this screen exists to prevent.
    """
    intent = (intent or "").strip()
    rows: list[dict] = []

    for path in dict.fromkeys(_PATH.findall(intent)):
        rows.append(_row(
            claim=f"{path} exists and carries the result",
            check=f"stat {path} and read it, in the session that reports it",
            threshold="present, non-empty, and written by this run rather than a previous one",
            verifier=VERIFY_AI,
            could_pass_wrong=("the file exists and is last week's copy, or it exists and is "
                              "empty. Existence is not freshness and it is not content."),
            prove_without_you=f"the agent pastes the first lines of {path} and its mtime"))

    for table in dict.fromkeys(_TABLE.findall(intent)):
        rows.append(_row(
            claim=f"{table} holds the restated data",
            check=f"count rows and take the min and max of the date column in {table}",
            threshold="the count and the window both stated as numbers, not as 'looks right'",
            verifier=VERIFY_AI,
            could_pass_wrong=("the count is right and every row in it is fixture data, or the "
                              "view emits GROUPING SETS and the count is a multiple of the "
                              "truth."),
            prove_without_you=f"the agent pastes the query it ran against {table} and its output"))

    for cmd in dict.fromkeys(_COMMAND.findall(intent)):
        rows.append(_row(
            claim=f"`{cmd}` succeeds",
            check=f"run `{cmd}` and read its output, not only its exit code",
            threshold="the output line that proves the work, quoted in the report",
            verifier=VERIFY_AI,
            could_pass_wrong=("a wrapper exits 0 while printing a failure. The exit code of a "
                              "wrapper is not the exit code of its child."),
            prove_without_you=f"the agent quotes the real stdout of `{cmd}`"))

    for num, unit in dict.fromkeys(_NUMBER.findall(intent)):
        if not unit:
            continue
        unit = unit.strip()
        figure = f"{num}{unit}" if unit == "%" else f"{num} {unit}"
        rows.append(_row(
            claim=f"the {unit} figure reaches {num}",
            check=f"measure it in this session and compare against {figure}",
            threshold=f"{figure}, stated with the measurement that produced it",
            verifier=VERIFY_AI,
            could_pass_wrong=("the threshold is met because the window was narrowed or a filter "
                              "was dropped. Tolerance-widening reads exactly like a pass."),
            prove_without_you="the agent states the query or command and its raw result"))

    for day in dict.fromkeys(_DATE.findall(intent)):
        rows.append(_row(
            claim=f"the work covers {day}",
            check=f"assert the boundary rows on {day} are present, not merely the range",
            threshold=f"a row on {day} shown, not a range that contains it",
            verifier=VERIFY_AI,
            could_pass_wrong="a range check passes with the boundary day missing inside it.",
            prove_without_you=f"the agent shows the row it found on {day}"))

    # Two rows the house rules demand of every task, whatever the intent said. They are not
    # padding: the first is the rule the review view already renders as UI, and the second is
    # the line the task detail page already prints when it is violated.
    rows.append(_row(
        claim="every claim in the report says how it was measured",
        check="read the report and count claims that state a result with no measurement",
        threshold="zero claims with nothing behind them",
        verifier=VERIFY_AI,
        could_pass_wrong=("a claim can name a command it never ran. Stating a method is not the "
                          "same as having used it, and only the pasted output separates them."),
        prove_without_you="the review view computes the evidence-less count from the report"))

    rows.append(_row(
        claim="the work leaves an artifact that can be opened",
        check="record every file produced with `swarm artifact`, absolute paths",
        threshold="at least one artifact, and each one present on disk when recorded",
        verifier=VERIFY_AI,
        could_pass_wrong=("a path can be recorded and then deleted, or recorded from a scratch "
                          "directory that is cleaned up after the run."),
        prove_without_you="the artifact record stamps whether the path was really on disk"))

    if not intent:
        return []

    # The row the drafter cannot honestly fill from the intent alone, kept rather than dropped
    # because its absence is the finding. It is what makes the unverifiable count non-zero on a
    # vague intent, which is the guide's self-punishing-scope mechanism doing its job.
    if len(rows) <= 2:
        rows.insert(0, _row(
            claim=f"the thing asked for is done: {_clip(intent)}",
            check="",
            threshold="",
            verifier=VERIFY_HUMAN,
            could_pass_wrong=("an agent will read this as satisfied by anything adjacent, "
                              "because nothing in it names a measurement."),
            prove_without_you="",
            why=("the intent names no path, no table, no command, no number and no date, so "
                 "there is nothing here an agent could check without you.")))
    return rows


def _clip(text: str, n: int = 70) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1] + "…"


def unverifiable(rows: list[dict]) -> list[dict]:
    """A row is unverifiable when no agent could settle it alone.

    Two ways in, and they are different failures: the row is explicitly `verify-human`, or it is
    marked `verify-ai` while leaving "how will an agent prove this without you" empty. The second
    is the worse one, because it claims a machine verifier and does not name one.
    """
    out = []
    for r in rows:
        if not r.get("kept"):
            continue
        if r.get("verifier") == VERIFY_HUMAN or not (r.get("prove_without_you") or "").strip():
            out.append(r)
    return out


def price(rows: list[dict]) -> str:
    """What the UI says at post time, before the operator pays for it rather than after."""
    kept = [r for r in rows if r.get("kept")]
    if not kept:
        return ("Nothing says what done looks like. This posts as raw intent, and the surfacing policy will "
                "route whatever comes back into your own Judge tier.")
    bad = unverifiable(kept)
    if not bad:
        return (f"{len(kept)} checks, all of them machine-verifiable. Nothing here needs you in "
                f"the room to settle it.")
    return (f"{len(bad)} unverifiable {'check' if len(bad) == 1 else 'checks'} of {len(kept)}, "
            f"expect this back in your Judge tier. A vague definition yields low confidence and "
            f"the surfacing policy routes low-confidence output back to you.")


def render(rows: list[dict]) -> str:
    """The definition of done as it lands in the posted body.

    A table rather than prose because the review view reads rows, and because a `verify-human`
    row has to be able to say what it is: *this review is that check*.
    """
    kept = [r for r in rows if r.get("kept")]
    if not kept:
        return ""
    out = ["## Definition of done",
           "",
           "Drafted by the console's template drafter and demolished by the operator. "
           "**Agent-drafted, so this work is disqualified from auto-accept**: an agent "
           "validating against a definition it wrote itself is self-grading moved up one level.",
           ""]
    for i, r in enumerate(kept, 1):
        mark = "x" if r["verifier"] == VERIFY_AI and r.get("prove_without_you") else " "
        out.append(f"- [{mark}] **{r['claim']}** ({r['verifier']})")
        if r.get("check"):
            out.append(f"      - check: {r['check']}")
        if r.get("threshold"):
            out.append(f"      - threshold: {r['threshold']}")
        if r.get("could_pass_wrong"):
            out.append(f"      - could pass while the work is wrong: {r['could_pass_wrong']}")
        if r.get("prove_without_you"):
            out.append(f"      - an agent proves this without the operator by: "
                       f"{r['prove_without_you']}")
        else:
            out.append(f"      - **no agent can prove this without the operator.** "
                       f"{r.get('why_unverifiable') or 'This review is that check.'}")
    out += ["", price(kept)]
    return "\n".join(out)


def from_form(form) -> list[dict]:
    """Rebuild the rows from the demolition form. Killed rows come back with kept unset."""
    rows = []
    for i in range(int(form.get("n") or 0)):
        claim = (form.get(f"claim-{i}") or "").strip()
        if not claim:
            continue
        rows.append({
            "claim": claim,
            "check": (form.get(f"check-{i}") or "").strip(),
            "threshold": (form.get(f"threshold-{i}") or "").strip(),
            "verifier": VERIFY_HUMAN if form.get(f"verifier-{i}") == VERIFY_HUMAN else VERIFY_AI,
            "could_pass_wrong": (form.get(f"wrong-{i}") or "").strip(),
            "prove_without_you": (form.get(f"prove-{i}") or "").strip(),
            "why_unverifiable": (form.get(f"why-{i}") or "").strip(),
            "kept": form.get(f"keep-{i}") == "1",
        })
    return rows
