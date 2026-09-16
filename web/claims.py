"""Claims, and how the console knows whether one has anything behind it.

This is the review view's engine, and it is the house rule rendered as code:

    a summary that claims a result without saying how it was measured is a finding,
    not a formatting nit.

Reviewing must not mean reading everything. It should mean finding the claims with nothing
behind them. That is only possible if "has evidence" is computed rather than eyeballed, so this
module defines exactly what counts as evidence and the review view counts what comes back. The
count in the closing line -- *"2 claims with nothing behind them"* -- is `len()` of the list
below and is never a constant.

**What counts as evidence, and the rule is deliberately narrow.** A claim line has evidence when
it carries at least one of:

  1. a path this task actually RECORDED as an artifact (`brain.artifact`), matched against the
     recorded path or its basename. A path the agent typed but never recorded is not evidence,
     because the artifact record is the thing another agent can check.
  2. an inline command or code span in backticks, which is a statement of how it was measured.
  3. a `file.ext:123` reference, which points at a line somebody can open.
  4. a bare path with a directory separator and a file extension, which is checkable by hand.

**What is deliberately NOT evidence:** the words "verified", "checked", "tested", "confirmed",
"passed", "all green". Those are the exact strings this program has watched be wrong. A claim
that says it was verified and does not say how is the naked case, not the clothed one.

The false-negative direction is chosen on purpose. A claim with real evidence phrased in a way
this parser misses gets pulled into the naked block and costs the reviewer thirty seconds. A
claim with no evidence that this parser waves through costs the reviewer the whole point of the
screen. Erring toward the naked block is the cheap failure.
"""

from __future__ import annotations

import os
import re

# Backticked spans, `file.py:14` references, and bare paths with an extension.
_CODE_SPAN = re.compile(r"`[^`\n]{2,}`")
_FILE_LINE = re.compile(r"\b[\w./\-]+\.\w{1,6}:\d+\b")
_BARE_PATH = re.compile(r"\b(?:[\w.\-]+/)+[\w.\-]+\.\w{1,6}\b")
_NUMBERS = re.compile(r"\b\d[\d,._]*\b")

# Verbs of assurance. Present without any of the four evidence forms above, they are the claim,
# not the evidence for it.
ASSURANCE = ("verified", "checked", "confirmed", "tested", "passed", "all green", "works",
             "validated", "successful", "no issues", "as expected")

_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
_HEADING = re.compile(r"^\s*#{1,6}\s+")


def split_claims(text: str) -> list[str]:
    """One claim per bullet, or per sentence when the report is prose.

    Bullets first, because an agent report that is bulleted has already done the splitting and
    re-splitting it on sentence boundaries would cut a bullet whose evidence sits in its second
    sentence away from the claim it belongs to.
    """
    text = (text or "").strip()
    if not text:
        return []
    lines = [ln.rstrip() for ln in text.splitlines()]
    bullets = [_BULLET.sub("", ln).strip() for ln in lines if _BULLET.match(ln)]
    if bullets:
        return [b for b in bullets if b]
    body = " ".join(ln for ln in lines if ln and not _HEADING.match(ln))
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", body)
    return [p.strip() for p in parts if len(p.strip()) > 12]


def evidence_for(claim: str, artifact_paths: list[str]) -> tuple[str | None, str]:
    """`(evidence, how)` or `(None, why-not)`. The whole naked/clothed decision lives here."""
    for path in artifact_paths:
        base = os.path.basename(path)
        if path and (path in claim or (len(base) > 4 and base in claim)):
            return path, "recorded artifact"
    m = _FILE_LINE.search(claim)
    if m:
        return m.group(0), "file and line"
    m = _CODE_SPAN.search(claim)
    if m:
        return m.group(0).strip("`"), "stated command"
    m = _BARE_PATH.search(claim)
    if m:
        return m.group(0), "path"
    low = claim.lower()
    if any(a in low for a in ASSURANCE):
        return None, "says it was verified, does not say how"
    if _NUMBERS.search(claim):
        return None, "a number with no stated measurement"
    return None, "no evidence given"


def analyse(result_text: str, artifact_paths: list[str]) -> dict:
    """Every claim with its evidence, and the naked ones counted rather than asserted."""
    rows = []
    for c in split_claims(result_text):
        ev, how = evidence_for(c, artifact_paths)
        rows.append({"text": c, "evidence": ev, "how": how, "naked": ev is None})
    naked = [r for r in rows if r["naked"]]
    return {
        "claims": rows,
        "naked": naked,
        "naked_count": len(naked),
        "total": len(rows),
        # The closing line, computed. `2 claims with nothing behind them.`
        "line": _closing_line(len(naked), len(rows)),
    }


def _closing_line(naked: int, total: int) -> str:
    if total == 0:
        return ("This report makes no separable claims. There is nothing to check against "
                "evidence, which is itself the finding.")
    if naked == 0:
        return (f"{total} claims, every one with evidence attached. Open the evidence you do "
                f"not already trust.")
    one = naked == 1
    return (f"{naked} claim{'' if one else 's'} with nothing behind "
            f"{'it' if one else 'them'}. Check {'that' if one else 'those'} and you have "
            f"reviewed this.")


DOD_HEADING = re.compile(r"^\s*#{0,6}\s*definition of done\b", re.I)
_CHECKBOX = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s*(.+)$")
# `claim · check · threshold · verifier` from the Scope room, and the plain checkbox form the
# swarm briefs actually use today. Both parse; neither is invented.
_VERIFIER = re.compile(r"\b(verify-human|verify-ai|verify-deterministic|human|ai)\b", re.I)


def definition_of_done(brief_text: str) -> list[dict]:
    """DoD rows with their verifier, parsed from the posted brief.

    A `verify-human` row is rendered saying what it is -- *this review is that check* -- because
    a human verifier that nobody names reads as an unticked box rather than as the reviewer's
    own job.
    """
    lines = (brief_text or "").splitlines()
    start = None
    for i, ln in enumerate(lines):
        if DOD_HEADING.search(ln):
            start = i + 1
            break
    scan = lines[start:] if start is not None else lines
    rows = []
    for ln in scan:
        m = _CHECKBOX.match(ln)
        if not m:
            if start is not None and _HEADING.match(ln) and rows:
                break
            continue
        ticked, text = m.group(1).strip().lower() == "x", m.group(2).strip()
        v = _VERIFIER.search(text)
        verifier = (v.group(1).lower() if v else "")
        rows.append({
            # Emphasis markers are stripped rather than rendered. The template escapes its input,
            # correctly, so a literal `**claim**` reaches the screen with the asterisks showing.
            # Rendering the markdown instead would mean trusting agent-authored text as markup,
            # which is a larger door than a bold claim is worth.
            "text": text.replace("**", "").replace("__", ""),
            "ticked": ticked,
            "verifier": verifier or "unstated",
            "human": verifier in ("verify-human", "human"),
        })
    return rows


# ================================================================= WHAT THE REPORT SAYS AGAINST
# ITSELF (row 0416)
#
# The operator drove the product end to end on 2026-08-28 and stopped on a Judge card whose
# report volunteered a gap in its own work -- *"The churn paragraph is still missing the July
# number."* -- and said:
#
#     "I'm trying to find that ... I only see it in the report in full and not in something
#      that would like tie in to where I should accept work or not."
#
# Measured on the demo store before this existed: `/queue?tier=judge` rendered 8 `Accept work`
# buttons and 0 occurrences of any of the four self-stated caveats its own cards' reports carry
# (0004 "the client has not approved the revenue figure yet", 0006 "the wordmark is still the
# 2024 lockup", 0009 "no screenshots yet" and "needs the ops sign off", 0011 "assume a feature
# that is behind a flag"). Each one is the single most decision-relevant sentence on its card
# and each was one interaction away from the button that ends the decision.
#
# THIS IS NOT A SUMMARISER AND IT INVENTS NOTHING. It selects whole claim lines, verbatim, out
# of the text the agent wrote, using the vocabulary a report uses when it says what it did NOT
# do. Nothing is rewritten, nothing is scored, and the count of what was scanned is returned
# beside the hits so a card can say `scanned, none found` rather than showing an empty slot --
# because the whole finding on the recommendation half of this pair (row 0410) was a screen
# where ABSENT and MERELY UNRENDERED looked identical.
#
# THE VOCABULARY IS DELIBERATELY SELF-REFERENTIAL and that is what keeps it narrow. It matches a
# report describing a shortfall in its OWN work, not a report describing a defect in the world:
# `9 tags firing on pages that no longer exist` is a finding about the subject and stays out,
# `no screenshots yet` is a hole in the work and comes in. That distinction is why there is no
# bare `no` and no bare `not` in the list, and why `still` is only ever matched with a following
# word.
#
# THE FALSE-POSITIVE DIRECTION IS CHOSEN, exactly as `evidence_for` above chooses it. A benign
# sentence pulled onto the card costs the operator one line of reading. A real caveat left off
# the card costs him the decision, which is the defect this was filed against. Measured on the
# thirteen reports in the demo store: 6 lines flagged over 5 of them, 6 of 6 are genuine self-stated
# gaps, 0 false positives, and 1 known miss (`0008`: "22 broken, 18 fixed, 4 point at a vendor
# doc that is gone" states a shortfall arithmetically and carries none of this vocabulary). The
# miss is left rather than closed with a numeric rule, because a rule that read every count as a
# caveat would flag `three cuts delivered at 0:58, 1:04 and 1:11` too, and a slot that cries on
# every card is a slot the operator learns to skip.
CAVEAT_PHRASES = (
    # a hole in the work, named
    "still missing", "is missing", "are missing", "still needs", "still no", "still the",
    "not yet", "yet to be", "left open", "open question", "known gap", "caveat", "limitation",
    "out of scope", "not covered", "todo", "fixme",
    # something the report could not or did not do
    "has not", "have not", "hasn't", "haven't", "had not",
    "did not", "didn't", "does not", "doesn't", "do not", "don't",
    "could not", "couldn't", "cannot", "can't", "was not able", "unable to",
    "not verified", "unverified", "not tested", "untested", "not checked", "unchecked",
    "not reviewed", "not approved", "not signed off",
    # something the report is standing on rather than having established
    "assume", "assumes", "assumed", "assuming", "assumption",
    "needs the", "needs a", "needs sign", "awaiting", "pending", "blocked on",
)

# `no screenshots yet`, `no answer from them yet`: the negation and the `yet` are separated by
# the thing that is missing, so the phrase list above cannot hold this shape.
_NO_YET = re.compile(r"\bno\b[^.;]{0,48}\byet\b", re.I)

# Three, and the fourth is a count rather than a truncation. A slot that reprints the report is
# the report, and the operator already has a control that opens it.
CAVEAT_SHOWN = 3


def caveats(result_text: str) -> dict:
    """What a report says against itself: `{lines, extra, scanned}`, verbatim and unrewritten.

    `scanned` is the number of claim lines this looked at, and it is returned even when nothing
    matched so the card can state that it looked. A caveat block that renders nothing where a
    report has none and renders nothing where a report was never read are the same silence, and
    telling them apart is the entire point of the pair of rows this serves.
    """
    lines = []
    scanned = split_claims(result_text)
    for claim in scanned:
        low = claim.lower()
        if any(p in low for p in CAVEAT_PHRASES) or _NO_YET.search(claim):
            lines.append(claim)
    return {"lines": lines[:CAVEAT_SHOWN],
            "extra": max(0, len(lines) - CAVEAT_SHOWN),
            "found": len(lines),
            "scanned": len(scanned)}
