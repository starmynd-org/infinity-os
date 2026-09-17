"""The attention queue as a screen may show it: ranked, explainable, and sortable without lying.

R04. Two rules live here and both are load-bearing.

**Sorting a column is not reprioritising.** `sorted_by()` returns a view whose `reordered` flag is
true and whose rows keep their original `rank`. The template renders the rank column and a banner
saying the working order is unchanged. Without this, a user sorts by "effect on you", sees the
biggest number at the top, and reasonably concludes the system will do that one first.

**A restricted item is metadata, never content.** `QueueItem.readable` false means the content was
never indexed for this reader, so `provenance` carries the boundary event and nothing else, and
`title` is the placeholder the producer supplied rather than the real subject line. The inspector
has no path to fetch it, which is the point: the authorisation check happens before indexing, not
at render time.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .honesty import Completeness, Freshness, Impact
from .signals import Signals

# The four tiers, in working order, with the sentence that explains each to a person.
TIERS: Dict[int, str] = {
    1: "needs a decision",
    2: "waiting on you",
    3: "finished, worth a look",
    4: "background",
}

# THE STORE'S OWN TIER WORDS, AND THE ONE PLACE THE DISPLAY INTEGER IS DERIVED FROM THEM.
#
# There are THREE tier vocabularies in this system and not two: the store's `decide/judge/shape`,
# this layer's 1-4 above, and the approved shell's `Decide/Review/Create`. The shell's axis maps
# onto the store by a clean rename; THE 1-4 AXIS MAPS ONTO NEITHER. Which surface survives is
# item 2 on the operator's escalation file and is not this seat's to decide.
#
# So the store's word is CARRIED VERBATIM on the item and the integer is derived here. The
# alternative, which term-4 proposed as a stopgap and I refused, was for the port to map
# `judge -> 2` and `shape -> 2` on its side: that puts two store tiers into one integer and the
# map stops being invertible, so nothing downstream -- template, filter, sort, receipt, or the
# next seat -- can recover which it was. Information destroyed at the one boundary where keeping
# it is free.
#
# THIS MAPPING IS PROVISIONAL AND IS PARKED ON THE OPERATOR'S RULING. It is deliberately one dict
# in one function so that the ruling costs one line here and nothing in anyone's binding. Nobody
# should read it as the axis question having been settled by this seat.
#
# `shape -> 4` rather than term-4's suggested 2, and the reasoning is recorded so it can be
# argued with: tier 2 reads "waiting on you", and a shape row is not waiting on the person -- it
# has no overlay, so no template, no option and no context. "Background" is the honest one of the
# four. Note that under ITEM/1.0 section 7 most shape rows are REFUSED before they reach here at
# all, because a row carrying no option is not decidable; where they land on the display axis
# matters less than that they are counted in `refused` with a denominator.
STORE_TIERS: Dict[str, int] = {
    "decide": 1,
    "judge": 2,
    "shape": 4,
}


def display_tier(store_tier: str) -> int:
    """The 1-4 display integer for a store tier word. THE ONLY PLACE THIS MAPPING EXISTS."""
    try:
        return STORE_TIERS[store_tier]
    except KeyError:
        raise ValueError(
            "unknown store tier %r; the store's words are %s"
            % (store_tier, ", ".join(sorted(STORE_TIERS)))
        )

# WHAT THE RANK EVIDENCE CANNOT SAY, SAID OUT LOUD RATHER THAN LEFT TO BE INFERRED.
#
# RELEASE 22. The chip links to `#evidence-rank` under the label "Rank evidence", which is the
# strongest claim this surface makes about position: it offers to explain a number. The declared
# signals are real evidence and they feed the ranker, but the number itself is assigned by
# enumerating the queue-wide tier-flattened ranking, and THIS VIEW HAS NO PER-ITEM TRACE OF THAT.
# Leaving that gap silent under an "evidence" heading invites the reader to supply the missing
# causal step themselves, which is the whole defect this release was opened for.
#
# THE FIRST DRAFT SAID "IS RECORDED ANYWHERE" AND THAT WAS A GLOBAL CLAIM ON LOCAL EVIDENCE.
# `term-13` at 202710Z: `queue/human_queue/reads.py::why` computes an additive per-item
# decomposition -- `unblock_detail`, `bump_detail`, declared-versus-computed -- and its own
# docstring says "This is what makes the ordering falsifiable", citing the rule that if the order
# cannot be explained from the signals and the rules, the order is wrong. So explanation machinery
# EXISTS; it is simply not on this view's port. I had verified the VIEW and asserted the SYSTEM,
# which is the same over-reach `term-4` corrected on producer purpose.
#
# The narrower claim is also the more useful one: "not available here" tells a reader where to
# look next, and "not recorded" tells them to stop looking. What `why()` does NOT establish is a
# PERSISTED trace -- it computes on demand -- so this note claims neither presence nor absence
# beyond this surface.
#
# So it is stated, and stated locally. A surface that names what it cannot show is honest; one
# that shows evidence next to a number and says nothing about the join is not.
RANK_POSITION_NOTE = (
    "The position comes from the queue-wide ranking. A per-item trace connecting these declared "
    "signals to that position is not available in this view."
)

RANK_EXPLANATION = (
    "Ranking uses, in order: a dated commitment to someone outside; something blocked on your "
    "decision; work that finished and needs checking; everything else. It is computed in one "
    "place, and sorting a column on this page does not change it."
)


# WHAT THE ROW IS NOW, IN A PERSON'S WORDS. THE ONE PLACE IN `web/views/**` THAT SPELLS A KIND OUT.
#
# THE KIND IS READ OFF THE STORE'S ARM AND IS NEVER INFERRED FROM THE TIER. `KIND_BY_ARM`
# (`web/model.py:83`) maps `(source_type, primary_verb)` to four words on this branch and to a
# fifth, `objective`, once Platform's hunks land with the arm (see the entry below) -- and
# `primary_verb`, not `item_class`, because `item_class` is overridable per item by
# `brain.queue_item`. `QueueItem.kind` carries that word verbatim; this dict only says it in
# English. Deriving a kind from `tier` here would be a second mapping in a second place, which is
# the defect `display_tier` above exists to prevent, stated for the other axis: a `human` row and
# a `review` row both land on tier 2 under some store words, so the tier cannot tell them apart
# and a sentence built from it would be a guess wearing the store's authority.
#
# WHY THE VERB IS IN THE SENTENCE for two of the four. The measured defect (T3, ATT-1g,
# `df13a55`, section 3 and section 4) is that a person who follows a stripe's "Open this row's
# inspector" link reads "Included because the store records an option for this item and a
# completed work result" and cannot tell what to do next: the words `Accept work` and `done`
# appear nowhere on the inspector. E6 as amended asks a tester to prove the act landed by reading
# the inspector's kind, so the kind has to be readable there in the queue's own vocabulary. The
# store's primary verb is named in parentheses where one exists on this room's rows.
#
# `question` and `recommendation` carry no primary verb in `KIND_BY_ARM` (both map from a `None`),
# so neither sentence invents one; the recommendation sentence names the room that holds `approve`
# and `reject` instead, which is the same sentence E10 puts on the row itself.
KIND_NOW: Dict[str, str] = {
    "review": "Finished work awaiting your acceptance (Accept work)",
    "human": "Your own task, not yet marked done (Mark my task done)",
    "question": "A question waiting for your answer",
    "recommendation": "A proposal awaiting your approval or rejection",
    # ARM 5, AND IT IS AHEAD OF `KIND_BY_ARM` ON THIS BRANCH ON PURPOSE. Andrew's D4 admits
    # objectives as a fifth arm of `brain.queue_open`; the two `KIND_BY_ARM` entries that produce
    # this word are Platform's hunks (`PROPOSED-web-model-hunks.patch`) and `web/model.py` is not
    # this seat's file, so on the tree as it stands NOTHING reaches this entry. That is the safe
    # order of the two and is stated so the extra key is not read as a mistake: a sentence waiting
    # for its arm renders nothing at all, and an arm arriving before its sentence renders the
    # store's raw word, which `kind_sentence` below already guarantees. The FAILURE this pair is
    # ordered against is the third case -- an objective falling through to `review` and being
    # described to a person as finished work -- and that is refused in `web/app.py`'s own branch
    # as well as closed by the second `KIND_BY_ARM` entry.
    #
    # THE VERB IS IN THE SENTENCE, like the first two and for the same reason: the store's own
    # `primary_verb` on arm 5 is the text `Accept objective`, and a person arriving on this
    # inspector from a stripe has to be able to read what to do next in the queue's vocabulary.
    "objective": "An intake item awaiting your triage (Accept objective)",
}


# F-01 / P3-03, 2026-09-13. THE ROW TITLE OF A RECOMMENDATION IS BOUNDED HERE AND NOWHERE ELSE.
# `brain.queue_open`'s recommendation arm carries `r.text`, the whole proposal, as the row title,
# and W-11 measured three of them at 1,099 to 1,230 characters ranking first on c6fc3cc: the first
# three rows were 479 to 554 px tall at 1440 and the first row alone 947 px at 375, with the first
# control at 1,218 px. A title is a handle, not the document. The bound is the first sentence when
# it fits, else the first whole words, never more than TITLE_BOUND characters including the mark;
# whitespace is collapsed first so a bound never depends on how the producer wrapped its lines.
# Storage is never cut: the full text rides `QueueItem.proposal`. Idempotent by construction (a
# bounded title is at most TITLE_BOUND characters and passes through), so a title bounded twice
# is the title bounded once.
#
# WHY 64. Measured at 375x812 with the dated intake observation on the page (the shape Andrew's
# first screen has): the chrome above the first card ends at about 501 px and the card's first
# control is 44 px tall, so the title, the disclosure line, the state line, the Inspect cell, the
# argument against, the no-inverse sentence and the Approve sentence share about 265 px. In the
# bold row title a line of Latin text broke at 43 and 45 characters on the measured proposals, so
# 100 characters cost three lines (52 px) and anything up to about 90 costs two; 64 keeps a Latin
# title to two lines with a line and a half of slack for long words, and with the no-inverse
# sentence above the first control the phone control's bottom edge measured 805 px against a
# fold at 812. At 100 it had measured 819. The same bound serves the argument against below the
# title. The number is a measurement of Latin text and is re-measured by
# `web/tests/check_attention_recommendations.py`, not carried; it counts code points, so a title
# of wide ideographs or emoji is wider than the arithmetic.
TITLE_BOUND = 64
# A first sentence shorter than this is an abbreviation, not a sentence ("Dr.", "Inc.", "No."),
# and the bound falls through to whole words instead.
SENTENCE_FLOOR = 24
ELLIPSIS = "…"
_SENTENCE_END = re.compile(r"^(.+?[.!?])(?=\s|$)")


def bounded_title(text: str) -> str:
    """The row-sized form of a proposal: first sentence if it fits, else first words, marked."""
    text = " ".join((text or "").split())
    if len(text) <= TITLE_BOUND:
        return text
    sentence = _SENTENCE_END.match(text)
    if sentence and SENTENCE_FLOOR <= len(sentence.group(1)) <= TITLE_BOUND:
        return sentence.group(1)
    cut = text[: TITLE_BOUND - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:") + ELLIPSIS


def kind_sentence(kind: str) -> str:
    """The person-readable sentence for a store kind. An unknown kind renders its own raw word.

    A KIND THIS FILE DOES NOT KNOW IS PRINTED, NOT GUESSED. `KIND_BY_ARM` gains a word whenever
    the store gains an arm, and this dict is downstream of that: a fifth arm arriving before this
    line is updated must show the reader the store's own word rather than the nearest of the four,
    because a wrong sentence here is indistinguishable from a right one at the point it is read.
    Returning the raw word is a legible gap; falling back to "review" would be a silent lie.
    """
    return KIND_NOW.get(kind, kind)


@dataclass(frozen=True)
class ProvenanceStep:
    at: datetime
    what: str
    by: str


@dataclass(frozen=True)
class Provenance:
    """Where a claim came from. Every visible status links to one of these."""

    source_label: str
    zone: str
    reference: Optional[str]
    steps: Tuple[ProvenanceStep, ...] = ()

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError(
                "provenance with no steps is a claim with no evidence; record at least the event "
                "that created the item"
            )


@dataclass(frozen=True)
class QueueItem:
    item_id: str
    title: str
    kind: str
    rank: int
    why: str
    impact: Impact
    freshness: Freshness
    provenance: Provenance
    tier: Optional[int] = None
    # The store's own word, carried verbatim: decide | judge | shape. When supplied, `tier` is
    # DERIVED from it and never trusted from the caller. See STORE_TIERS above for why.
    store_tier: Optional[str] = None
    readable: bool = True
    completeness: Optional[Completeness] = None
    signals: Optional[Signals] = None
    native_options: Tuple[object, ...] = ()
    counterargument: str = ""
    # F-01 / P3-03, 2026-09-13. A recommendation's `title` is the BOUNDED form of its proposal
    # (`bounded_title` below); the whole text travels here, uncut, for the row's disclosure and
    # the inspector's "Proposal, in full". Empty on every other kind.
    proposal: str = ""

    def __post_init__(self) -> None:
        # THE DERIVATION, AND THE GUARD ON IT. A caller may send the store's word, or the display
        # integer, or both -- and if it sends both they must agree. Sending both and disagreeing
        # is the shape of two mappings existing in two places, which is the whole defect this
        # field was added to prevent, so it is a refusal rather than a silent preference.
        if self.store_tier is not None:
            derived = display_tier(self.store_tier)
            if self.tier is not None and self.tier != derived:
                raise ValueError(
                    "store_tier %r derives display tier %d but tier=%r was also passed. The "
                    "display integer is derived here and in no other place; send the store's "
                    "word, or the integer, not two answers."
                    % (self.store_tier, derived, self.tier)
                )
            object.__setattr__(self, "tier", derived)
        if self.tier is None:
            raise ValueError("an item carries either store_tier or tier; this one carries neither")
        if self.tier not in TIERS:
            raise ValueError("tier is 1-4, got %r" % (self.tier,))
        if not self.why:
            raise ValueError("every item says why it is in the queue")
        if not self.readable and (self.impact.is_known or self.impact.is_estimate):
            raise ValueError(
                "an item whose content was never read cannot carry a measured or estimated "
                "impact: either figure could only have come from reading it"
            )

    def explain_rank(self) -> str:
        """WHAT THIS ITEM DECLARED that the ranker reads. Not a trace of why it landed here.

        Empty when the producer declared nothing, so the caller falls back to the queue-level
        explanation rather than printing a sentence assembled entirely out of defaults.

        RELEASE 22. The name stays `explain_rank` because it is called from the template and from
        `status_evidence`, and renaming it would be a churn no reviewer asked for -- but the
        docstring said "Why THIS item is where it is", and that was the claim being corrected one
        layer down. Declared signals feed the ranking; they do not determine the ordinal, and this
        view carries no trace of the join. Whether one exists elsewhere is not this docstring's to
        say -- `queue/human_queue/reads.py::why` computes a decomposition on demand. See
        `RANK_POSITION_NOTE`.
        """
        return self.signals.explain() if self.signals else ""

    @property
    def proposal_is_bounded(self) -> bool:
        """True when the row title is shorter than the proposal, so a disclosure has something to add."""
        return bool(self.proposal) and self.title != " ".join(self.proposal.split())

    @property
    def counterargument_summary(self) -> str:
        """The row-sized form of the argument against; the whole text is on the inspector."""
        return bounded_title(self.counterargument)

    @property
    def counterargument_is_bounded(self) -> bool:
        return self.counterargument_summary != " ".join((self.counterargument or "").split())

    @property
    def incomplete(self) -> bool:
        """True when this item finished without covering everything. The card must say so."""
        return self.completeness is not None and not self.completeness.whole

    @property
    def tier_label(self) -> str:
        return TIERS[self.tier]

    @property
    def status_evidence(self) -> Dict[str, Dict[str, object]]:
        """Permitted, claim-specific evidence used by both chips and inspector anchors."""
        latest = self.provenance.steps[-1]
        freshness_at = self.freshness.at or latest.at
        evidence = {
            "rank": {"at": latest.at, "label": "Rank evidence",
                     "detail": (self.explain_rank() or "No per-item signal was declared.")
                               + " " + RANK_POSITION_NOTE},
            "freshness": {"at": freshness_at, "label": "Freshness evidence", "detail": self.freshness.render()},
            "impact": {"at": latest.at, "label": "Impact evidence", "detail": self.impact.render()},
            "permission": {"at": latest.at, "label": "Permission evidence",
                           "detail": "you may read this item" if self.readable else "you may see only limited details"},
        }
        if self.completeness is not None:
            evidence["completeness"] = {"at": latest.at, "label": "Completeness evidence",
                                        "detail": self.completeness.render()}
        return evidence

    def inspect(self) -> Dict[str, object]:
        """What the read-only inspector may show. Restricted items return the refusal, not a stub.

        There is no branch here that fetches content. The inspector's whole surface is this dict.
        """
        if not self.readable:
            return {
                "readable": False,
                "item_id": self.item_id,
                "title": self.title,
                "workspace": self.provenance.source_label,
                "content": None,
                "message": (
                    "You are not allowed to read this one. It is here because its existence was "
                    "recorded, not because its content was read: nothing was indexed for your "
                    "account, so there is nothing to show, not even a preview."
                ),
                "impact": self.impact.render(),
                "steps": self.provenance.steps,
                "evidence": self.status_evidence,
            }
        return {
            "readable": True,
            "item_id": self.item_id,
            "title": self.title,
            "workspace": self.provenance.source_label,
            "zone": self.provenance.zone,
            "reference": self.provenance.reference,
            "freshness": self.freshness.render(),
            "impact": self.impact.render(),
            "completeness": self.completeness.render() if self.completeness else None,
            # WHAT IT IS NOW, above WHY IT IS HERE, and the raw word beside the sentence so a
            # reader who needs the store's vocabulary is not left translating back out of prose.
            # Readable branch only: the unreadable branch returns the refusal rather than a stub,
            # and a key nothing renders is surface without a reader.
            "kind": self.kind,
            "kind_now": kind_sentence(self.kind),
            "native_options": self.native_options,
            "counterargument": self.counterargument,
            "proposal": self.proposal,
            "why": self.why,
            "rank_reason": self.explain_rank(),
            "unstated_signals": self.signals.silence_note() if self.signals else "",
            "display": self.signals.display if self.signals else None,
            "steps": self.provenance.steps,
            "evidence": self.status_evidence,
        }


SORTS: Dict[str, Callable[[QueueItem], object]] = {
    "title": lambda i: i.title.lower(),
    "kind": lambda i: i.kind,
    "freshness": lambda i: {"fresh": 0, "stale": 1, "absent": 2}[i.freshness.state],
    "tier": lambda i: i.tier,
}

# Admission kind and ranking tier answer different questions. Never rerank a
# completed shape-tier row merely to make its pending acceptance discoverable.
FILTERS = {
    'queue': lambda item: True,
    'review': lambda item: item.kind == 'review' or item.tier in (1, 2),
    'restricted': lambda item: not item.readable,
    'stale': lambda item: item.freshness.state == 'stale',
    'unmeasured': lambda item: not item.impact.is_known and not item.impact.is_estimate,
    # Keyed `awaiting`, as its label reads; `done` still resolves (navigation.FILTER_ALIASES).
    'awaiting': lambda item: item.kind == 'review',
}
FILTER_LABELS = {'queue': 'Queue', 'review': 'Needs review', 'restricted': 'Restricted',
                 'stale': 'Stale', 'unmeasured': 'Unmeasured', 'awaiting': 'Awaiting acceptance'}


def empty_message(view, filter_key):
    if filter_key != 'queue' and not view.filtered_total:
        return 'No rows match ' + FILTER_LABELS[filter_key] + '. Other work may still need you; open Queue to see it.'
    if view.has_denominators and not view.filtered_total and view.total_open:
        return 'No rows are admitted to this view. The admission and blocked counts below explain what remains outside it.'
    return view.empty_note


@dataclass(frozen=True)
class AdmissionRefusal:
    """Read-only recovery metadata, deliberately not an actionable QueueItem."""
    item_id: str
    title: str
    reason: str
    source_url: Optional[str] = None
    owner: str = 'Attention'
    needs_preparation: bool = False


@dataclass(frozen=True)
class QueueView:
    """A list of items plus the truth about what order they are in and why."""

    items: Tuple[QueueItem, ...]
    reordered: bool = False
    sort_key: Optional[str] = None
    descending: bool = False
    checked_at: Optional[datetime] = None

    # THE DENOMINATORS. Supplied by the port; see VIEW-CONTRACT-2026-09-09.md section 4.
    #
    # `None` means THE PORT DID NOT SUPPLY THEM, and that is a third state which is neither zero
    # nor a number. It has to stay distinguishable, because `0 shown` over an empty store and
    # `0 shown` over ten thousand rows are the same four characters on a page, and defaulting
    # these to 0 would make "no denominator" indistinguishable from "the denominator is zero".
    # That is the exact confusion this whole contract exists to end. When they are None the page
    # says the counts are unavailable; it does not print a zero.
    refused_of: Optional[int] = None      # unsuppressed pre-gate candidates. The outer denominator.
    refused: Optional[int] = None         # dropped by ITEM/1.0 section 7. Counted, never returned.
    blocked: Optional[int] = None         # suppressed as blocked. Never folded into `deferred`.
    deferred: Optional[int] = None        # suppressed as deferred. Never folded into `blocked`.
    total_open: Optional[int] = None      # refused_of + blocked + deferred. SUPPLIED, so I5 can fail.
    admitted: Optional[int] = None        # refused_of - refused. SUPPLIED, so I1 can fail.
    filtered_total: Optional[int] = None  # admitted rows matching the ACTIVE filter, before slicing.
    total_by_tier: Optional[Dict[str, int]] = None   # same population, split by the STORE's word.
    window: int = 0                       # the RANKER's per-tier window. 0 = the ranker was unwindowed.
    limit: int = 0                        # rows this page was allowed. 0 = unbounded.
    offset: int = 0
    refusal_items: Tuple[AdmissionRefusal, ...] = ()

    @classmethod
    def ranked(cls, items: Sequence[QueueItem], checked_at: Optional[datetime] = None) -> "QueueView":
        return cls(items=tuple(sorted(items, key=lambda i: i.rank)), checked_at=checked_at)

    def __post_init__(self) -> None:
        """The invariants. A violation is a ValueError HERE rather than a wrong number on a page.

        Numbering matches VIEW-CONTRACT-2026-09-09.md section 5, so a red assertion names the
        clause a reader can go and check.

        I2 WAS A TAUTOLOGY IN THE FIRST TWO DRAFTS OF THAT CONTRACT and shipped in neither, because
        `term-13` found it by algebra and `term-14` found it independently by running the written
        expressions. It read `offset + shown + hidden == filtered_total` while `hidden` was DEFINED
        as `filtered_total - offset - shown`, so it reduced to `filtered_total == filtered_total`
        and could not fail on any input -- including on the page-slice plant I had named as its
        plant. It is a bound here, not an identity, and the bound is what has teeth.

        AND THE BOUND IS STILL NOT ENOUGH, which is the more important half. A port reporting
        `filtered_total = 500` against a true matching population of 1,240 satisfies EVERY
        assertion below, with every field coherent and nonnegative, and the page is confidently
        wrong. No invariant computed from this object's own fields can catch that, because this
        object has no access to the truth it is being checked against. That needs an external
        oracle -- T1 in the contract -- and these assertions are not a substitute for it.
        """
        supplied = [self.refused_of, self.refused, self.blocked, self.deferred,
                    self.total_open, self.admitted, self.filtered_total]
        if all(v is None for v in supplied):
            # The port supplies no denominators. Nothing to check, and the page will say the
            # counts are unavailable rather than print a zero.
            #
            # "TODAY'S PORT IS THIS ONE" STOOD HERE AND IS NOW FALSE. `term-13` caught it at
            # 181111Z. It was written when the only port returned `QueueView.ranked([])`
            # unconditionally; since MERGE-IOS-03 at 130fe86 the mounted port DOES supply
            # denominators, and the live page reads "7 of 100 shown". A present-tense claim about
            # which port exists is a claim that expires on a merge, and this one did.
            #
            # The BRANCH is still reachable and still correct -- a port may legitimately supply
            # none -- so the code is unchanged and only the sentence about the world is.
            return
        if any(v is None for v in supplied):
            missing = [n for n, v in zip(
                ("refused_of", "refused", "blocked", "deferred", "total_open", "admitted",
                 "filtered_total"), supplied) if v is None]
            raise ValueError(
                "the denominators are supplied together or not at all; missing %s. A refusal "
                "count without its denominator is not quotable." % ", ".join(missing))
        for name, value in (("refused_of", self.refused_of), ("refused", self.refused),
                            ("blocked", self.blocked), ("deferred", self.deferred),
                            ("total_open", self.total_open), ("admitted", self.admitted),
                            ("filtered_total", self.filtered_total),
                            ("window", self.window), ("limit", self.limit),
                            ("offset", self.offset)):
            if value < 0:
                raise ValueError("%s is negative (%r); a count cannot be" % (name, value))
        if self.refused + self.admitted != self.refused_of:
            raise ValueError(
                "I1: refused %d + admitted %d != refused_of %d"
                % (self.refused, self.admitted, self.refused_of))
        if self.refusal_items and len(self.refusal_items) != self.refused:
            raise ValueError('Refusal identities do not reconcile with the refusal count')
        if {i.item_id for i in self.items} & {i.item_id for i in self.refusal_items}:
            raise ValueError('An item cannot be both admitted and refused')
        # I2. A BOUND, NOT AN IDENTITY -- AND IT IS *ONE* BOUND, NOT TWO.
        #
        # The contract and my own bus messages said this was two safeguards: `offset + shown <=
        # filtered_total` AND `hidden >= 0`. `term-14` pointed out they are algebraically the same
        # statement, because `hidden` is DEFINED as `filtered_total - offset - shown`:
        #
        #     hidden >= 0  <=>  filtered_total - offset - shown >= 0  <=>  offset + shown <= filtered_total
        #
        # They cannot fire independently and cannot both be evidence. Writing one predicate twice
        # and counting two greens is a smaller version of the tautology this whole guard replaced,
        # so it is written once. THE SECOND TIME I HAVE ASSERTED INDEPENDENCE WITHOUT CHECKING IT
        # TODAY, and both times a reviewer did the algebra I had not.
        if self.offset + self.shown > self.filtered_total:
            raise ValueError(
                "I2: offset %d + shown %d overshoots filtered_total %d (hidden would be %d). A "
                "page cannot hold more rows than the population it is a page of; this is what a "
                "page-local filtered_total looks like from in here."
                % (self.offset, self.shown, self.filtered_total,
                   self.filtered_total - self.offset - self.shown))
        if not (self.filtered_total <= self.admitted <= self.refused_of <= self.total_open):
            raise ValueError(
                "I3: filtered_total %d <= admitted %d <= refused_of %d <= total_open %d does not "
                "hold" % (self.filtered_total, self.admitted, self.refused_of, self.total_open))
        if self.limit > 0 and self.shown > self.limit:
            raise ValueError("I4: shown %d exceeds limit %d" % (self.shown, self.limit))
        if self.total_open != self.refused_of + self.blocked + self.deferred:
            raise ValueError(
                "I5: total_open %d != refused_of %d + blocked %d + deferred %d"
                % (self.total_open, self.refused_of, self.blocked, self.deferred))
        if self.total_by_tier is not None:
            # NEGATIVE PER-TIER COUNTS WERE ACCEPTED, AND `term-14` FOUND IT (CAP14-REV-126).
            # The nonnegativity loop above walks the SCALARS and never descends into this dict, so
            # `{decide: -1, judge: 29, shape: 4}` summed to 32 and passed every check. A count of
            # minus one is not a plausible-but-wrong split needing an oracle -- it is not a count
            # at all, and it can be refused from in here with no external knowledge whatever.
            # `{decide: 0, ...}` stays valid: zero is a real answer and only negatives are refused.
            for word, value in self.total_by_tier.items():
                if value < 0:
                    raise ValueError(
                        "I6: total_by_tier[%r] is negative (%d); a count cannot be"
                        % (word, value))
            for word in self.total_by_tier:
                if word not in STORE_TIERS:
                    raise ValueError(
                        "I6: total_by_tier is keyed by the STORE's word (%s), got %r. A split "
                        "keyed by the display integer has already lost a tier."
                        % (", ".join(sorted(STORE_TIERS)), word))
            if sum(self.total_by_tier.values()) != self.filtered_total:
                raise ValueError(
                    "I6: total_by_tier sums to %d, filtered_total is %d"
                    % (sum(self.total_by_tier.values()), self.filtered_total))

    @property
    def has_denominators(self) -> bool:
        """Did the port supply the counts? Neither True-because-zero nor a guess."""
        return self.filtered_total is not None

    @property
    def shown(self) -> int:
        return len(self.items)

    @property
    def hidden(self) -> int:
        """Rows matching this filter that are not on this page. DERIVED, never passed in."""
        if self.filtered_total is None:
            raise ValueError("hidden has no meaning without filtered_total")
        return self.filtered_total - self.offset - self.shown

    @property
    def has_next(self) -> bool:
        """Is there a NEXT page, and would going to it actually move?

        `shown > 0` IS THE WHOLE POINT AND IT CLOSES A SELF-LINK. The template builds the Next
        href as `offset + shown`, so when `shown` is 0 that is THE CURRENT OFFSET: the link points
        at the page the reader is already on. In the fault state -- an empty page over a non-empty
        population -- the old condition was true (`0 + 0 < 100`), so the page offered a Next that
        could be clicked forever without moving, DIRECTLY BESIDE the sentence saying this page is
        failing to render its rows. That is the worst possible place to offer an escape that is not
        one.

        `term-14` found the state and left the contract question open rather than inventing a
        predicate for it: implementation matched the contract, and it suggested Retry or
        suppression. THIS IS SUPPRESSION, and the argument for it over the alternatives:

          - advancing by `limit` instead of `shown` would SKIP the rows that belong on this page.
            It papers over the fault by stepping past it, and the reader is never told they were
            skipped -- a silent hole in a list whose whole defect is already a silent hole.
          - a "Retry" control would imply forward progress that may not exist. WHETHER THE FAULT IS
            TRANSIENT IS UNKNOWN, and that is the honest reason rather than the one I first gave.
            I originally wrote that retry "cannot work because the port returns an empty view
            unconditionally" -- but that is the OLD port, and the cleared paired port does not
            behave that way. `term-13` refused the reasoning: injected empty-page fault states do
            not establish that all future faults are permanent. Suppression is chosen because it
            promises nothing, not because recovery is known to be impossible.

        Suppressing it leaves the reader with the denominator sentence and no false affordance,
        which is the honest state. AND IT IS STRUCTURAL: a self-link is now unreachable rather
        than something the template has to remember not to emit.

        The end page is unaffected -- at `offset == filtered_total` the first clause was already
        false -- and the normal case is unaffected, because a page that rendered rows has
        `shown > 0` by definition.
        """
        return (self.has_denominators and self.shown > 0
                and self.offset + self.shown < self.filtered_total)

    @property
    def has_prev(self) -> bool:
        return self.offset > 0

    @property
    def denominator_note(self) -> str:
        """The sentence UNDER the table. Never beside a row, and never in the nav.

        `web/MUST-NOT-BUILD.md` at `0180a51`, item 8. The CONDITION is the decision, so it is the
        condition quoted here rather than the item: the score is overruled ONLY where he cannot
        act on it, and NEVER BESIDE A ROW. What that condition governs is PLACEMENT. Under the
        table these numbers are a denominator; printed next to a tier's rows the same numbers are
        a score. The overrule also does not travel to the nav, and nothing here is rendered there.

        Two denominators are kept apart on purpose, because they answer different questions:
        `refused` is against `refused_of` (how much the contract turned away), and `hidden` is
        against `filtered_total` (how much this filter has that this page does not show). Folding
        them into one ratio would produce a number that is true of nothing.

        WHEN THE COUNTS ARE UNAVAILABLE THIS SAYS SO IN THE WORDS OF WHAT IS MISSING, which is
        acceptance clause 3. It does not print a zero, because a zero here is indistinguishable
        from a real zero and this page has spent all day being read as empty when it was untested.
        """
        if not self.has_denominators:
            return (
                "How many rows there are in total is not shown, because the store-backed port "
                "does not report it yet. This page cannot tell you whether it is showing all of "
                "them or some of them."
            )
        # "NOT ON THIS PAGE" WAS WRONG AND `term-13` CAUGHT IT. `hidden` is
        # `filtered_total - offset - shown`, so it EXCLUDES the rows on earlier pages -- which are
        # also not on this page. At offset 8 of a filtered 32 showing 8, the rows not on this page
        # number 24; `hidden` is 16. The sentence was true only on the first page and quietly wrong
        # on every other one, which is the worst available shape for a number a person is reading
        # to decide whether they have seen everything.
        parts = []
        if self.hidden:
            parts.append(
                "Showing %d of %d, starting at %d. %d remain after this page."
                % (self.shown, self.filtered_total, self.offset + 1, self.hidden))
        elif self.offset:
            parts.append(
                "Showing the last %d of %d. None remain after this page."
                % (self.shown, self.filtered_total))
        else:
            parts.append("Showing all %d." % self.filtered_total)
        if self.refused:
            parts.append(
                "%d of %d %s refused for not carrying a decision."
                % (self.refused, self.refused_of,
                   "were" if self.refused != 1 else "was"))
        if self.blocked or self.deferred:
            parts.append(
                "%d blocked and %d deferred are not counted above."
                % (self.blocked or 0, self.deferred or 0))
        return " ".join(parts)

    def sorted_by(self, key: str, descending: bool = False) -> "QueueView":
        if key not in SORTS:
            raise ValueError("unknown sort %r; the sortable columns are %s" % (key, ", ".join(sorted(SORTS))))
        ordered = sorted(self.items, key=SORTS[key], reverse=descending)
        return replace(self, items=tuple(ordered), reordered=True, sort_key=key, descending=descending)

    @property
    def empty(self) -> bool:
        return not self.items

    @property
    def order_note(self) -> str:
        if not self.reordered:
            return "Ranked order: the order Infinity would work through them."
        return (
            "Sorted view. You are looking at these by %s. That changes what you see, not what "
            "Infinity does first: the ranked order is unchanged." % self.sort_key
        )

    @property
    def empty_note(self) -> str:
        """An empty queue is not a failed read, and the difference has to survive onto the screen.

        AND AN EMPTY PAGE IS NOT AN EMPTY QUEUE, which is the case this branch got wrong all day.
        Measured 2026-09-09 at `143237Z` on `ios_mount_scratch` at ledger 68: 100 rows in
        `brain.queue_open`, 0 rendered, and this page said "Nothing needs you." The response was
        4,581 bytes with 100 rows in the store and 4,581 bytes with 0 -- byte-identical, so the
        page was provably not a function of the store at all.

        "Nothing needs you" over a hundred waiting rows is not a cosmetic problem. It is the page
        asserting something it does not know, and it is the sentence a person would act on.

        So the moment the port supplies denominators, an empty page with a non-empty population
        says what it actually knows.

        AND NO FURTHER THAN THAT, WHICH THE SUITE HAD TO TELL ME. My first attempt also replaced
        the no-denominator case with "this page cannot tell you whether anything needs you", and
        four tests went red -- `test_an_empty_queue_is_distinguished_from_a_failed_read` among
        them. They were right and the change was wrong: a port that genuinely read the queue and
        found it empty DOES know, and `checked_at` is how it says so. Blanketing every
        denominator-less port with an "I cannot tell" sentence destroys the empty-versus-failed-
        read distinction those tests exist to protect -- it would have been a regression wearing
        the costume of more honesty. The honesty about MISSING COUNTS belongs in
        `denominator_note`, which is a different sentence in a different place, and is rendered
        beside this one.
        """
        # AN EMPTY PAGE PAST THE START IS NOT THE SAME ACCUSATION, AND `term-4` CAUGHT ME MAKING
        # IT. Its binding clamps a stale `offset` to `filtered_total`, which produces a legitimately
        # empty end page carrying a Previous link -- the queue shrank under a bookmarked URL. My
        # first version called every empty page with a non-empty population "a fault in this page",
        # so a correct clamp would have been reported to the reader as a defect. Accusing the code
        # of a bug it does not have is the same species of error as reassuring the reader when it
        # does: both are the page saying something it does not know.
        # AND THE CONDITION IS `offset >= filtered_total`, NOT `offset` -- `term-14` caught the
        # first repair being as wrong as the thing it repaired. I wrote `and self.offset`, which is
        # true of ANY non-zero offset, so an empty MIDDLE page said "past the end of the list"
        # while 92 rows still remained ahead of it. An empty page at offset 8 of 100 is not a stale
        # bookmark; it is the false-empty, and it had just been handed the one message that excuses
        # it. Past the end means the offset has reached or passed the population, and nothing else.
        if (self.has_denominators and self.filtered_total
                and self.offset >= self.filtered_total):
            return (
                "This page is past the end of the list. There are %d rows that match, and this "
                "page starts after the last of them, which usually means the queue got shorter "
                "since this link was made. Use Previous to get back to them."
                % self.filtered_total
            )
        if self.has_denominators and self.filtered_total:
            return (
                "This page is showing none of the %d rows that match. That is a fault in this "
                "page, not an empty queue: the rows are there and it is not rendering them."
                % self.filtered_total
            )
        if self.checked_at is None:
            return "Nothing needs you. When this list was last built is not recorded."
        return (
            "Nothing needs you. Last checked %s. This is an empty queue, not a failed read: an "
            "unreachable source would appear here as an exception."
            % self.checked_at.strftime("%a %d %b %Y, %H:%M %Z")
        )

    @property
    def working_order(self) -> Tuple[str, ...]:
        """The order the system will actually work in, whatever this view is sorted by."""
        return tuple(item.item_id for item in sorted(self.items, key=lambda i: i.rank))
