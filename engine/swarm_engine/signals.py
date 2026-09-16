"""The nine signals, ported unchanged from `bin/swarm`.

Nothing in this file is new. It is `bin/swarm:536-760` with the file bus removed, and the three
load-bearing properties are the reason it is ported literally rather than rewritten:

1. A MISSING SIGNAL IS CONSERVATIVE, NEVER THE MIDDLE. Unknown stakes are high, unknown
   reversibility is costly-or-irreversible. Defaulting to medium turns every unassessed task
   into an average one, which is how unassessed work quietly gets treated as safe.
2. THE TWO HARD FLAGS ARE GATES, NOT WEIGHTS. No combination of good signals outvotes them,
   because they are not in the sum.
3. HARD FLAGS INHERIT BY OR, UP THE WHOLE PARENT CHAIN, resolved on every read rather than
   stamped at post time. Holding for one hop is flag laundering (D00 rule 8, EF-7).

The folding vocabulary is duplicated in SQL as `brain.signal_level()` (migration 1, widened by
migration 6). That is deliberate and it is tested: `test_signal_parity` asserts this module and
the database agree on every value in both vocabularies, because two foldings that drift would
order the queue differently depending on which one asked.

Migration 6 is why the numeric branch below is now live. Migration 1 carried the word aliases and
neither numeric branch, so `dependency_unblocking=5` was refused by a CHECK and read `low`; D4
measured that and filed it to `D-CROSSTALK` slot 7 rather than folding `5` to `high` at the CLI,
because storing a level where the caller wrote a count discards the count.
"""

from __future__ import annotations

import math
import re

# The canon's numeric signal literal, identical to the regex in `brain.signal_numeric()`
# (migration 6). Optional sign, digits with an optional fraction or a bare fraction, and an
# optional exponent. Kept as one shared shape in two languages on purpose: this is the accepted
# set for BOTH the write gate and the fold, so the two cannot drift the way migration 1's did.
NUMERIC_LITERAL = re.compile(r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?")

SIGNAL_LEVELS = ("low", "medium", "high")
LEVEL_VALUE = {"low": 0.0, "medium": 1.0, "high": 2.0}

LEVELLED_SIGNALS = ("stakes", "reversibility", "urgency", "dependency_unblocking",
                    "effort", "confidence", "charter_alignment", "impact")
HARD_FLAGS = ("external", "canon_touching")
SIGNAL_FIELDS = LEVELLED_SIGNALS + HARD_FLAGS

CONSERVATIVE = {
    "stakes": "high",
    "reversibility": "low",   # low reversibility is this vocabulary's costly-or-irreversible
    "urgency": "low",
    "dependency_unblocking": "low",
    "effort": "high",
    "confidence": "low",
    "charter_alignment": "low",
    "impact": "high",         # migration 48. The same conservative `stakes` has always taken.
}

#: What the store accepts for urgency on write (migration 74's `brain.signal_ok`). Read-side folding
#: below still knows low/medium/high for rows written earlier.
URGENCY_WORDS = ("none", "soon", "deadline", "decaying")

SIGNAL_ALIASES = {
    "stakes": {"none": "low", "critical": "high"},
    "impact": {"none": "low", "critical": "high"},
    "reversibility": {"reversible": "high", "costly": "medium", "irreversible": "low"},
    "urgency": {"none": "low", "soon": "medium", "deadline": "high", "decaying": "high"},
    "effort": {"small": "low", "large": "high"},
    "confidence": {},
    "charter_alignment": {},
    "dependency_unblocking": {},
}

# ---------------------------------------------------------------- impact, the continuous signal
#
# MIGRATION 48, ROW 0434, HIS ASK 4. `impact` is the only signal in this vocabulary that carries a
# MAGNITUDE and not just a level, and the reason it exists is that its predecessor could not.
# `stakes` accepts five words and folds them onto three levels, so `urgency x stakes` has at most
# nine cells and fewer distinct values than that. The operator asked for urgency times importance,
# got a Shape tier of three distinct scores, and the coarseness of the scale was the whole cause.
#
# It lives HERE, in the engine's signal module, and not in `human_queue.rank`, for the reason this
# file's own header gives about the SQL fold: two implementations of one fold drift, and then a
# task is explained one way and ordered another. `human_queue.rank` imports these rather than
# keeping a second copy, and `test_signal_parity_word_vocabulary` now walks `impact` for free
# because it iterates `LEVELLED_SIGNALS` and `SIGNAL_ALIASES`.
IMPACT_BAND = {"none": 0.25, "low": 0.5, "medium": 1.0, "high": 2.0, "critical": 3.0}
IMPACT_FLOOR, IMPACT_CEILING = 0.25, 4.0
IMPACT_MONEY_FLOOR = 100.0   # below this a bare number reads as both money and a rating
IMPACT_UNSET = 2.0           # conservative, and it is `stakes`'s own long-standing default


def impact_magnitude(raw) -> float:
    """One band or one amount of money onto a magnitude in [0.25, 4.0]. Never zero, never None.

    THE PYTHON HALF OF `brain.impact_magnitude`, held equal to it by
    `test_signal_parity_impact_magnitude`.

    ONE DECADE OF MONEY PER BAND STEP, anchored so 1000 reads the same as `medium`, 10000 as
    `high` and 100000 as `critical`. Logarithmic rather than linear because on a linear scale one
    row carrying 500k sets the top of the range and every other row rounds to an indistinguishable
    sliver near zero, which is the collapse this signal exists to end arriving through the other
    door.

    NEVER ZERO. The magnitude is a FACTOR in `urgency x impact`, and a zero factor annihilates: a
    row would score nothing on the spine however urgent it is. The floor is 0.25 and `none` sits
    on it.

    A BARE NUMBER IS MONEY, never a one-to-five rating. `validate_signal` refuses the range where
    those two readings differ, so nothing ambiguous is ever stored; a value that reaches here from
    under the floor anyway is read as the money it claims to be rather than silently promoted.
    """
    v = str(raw if raw is not None else "").strip().lower()
    if not v:
        return IMPACT_UNSET
    if v in IMPACT_BAND:
        return IMPACT_BAND[v]
    if NUMERIC_LITERAL.fullmatch(v):
        money = float(v)
        if money < IMPACT_MONEY_FLOOR:
            return IMPACT_FLOOR
        return min(IMPACT_CEILING, max(IMPACT_FLOOR, math.log10(money / 100.0)))
    return IMPACT_UNSET


def impact_band(raw) -> str:
    """The magnitude read back as a level, on the same thresholds `brain.signal_level` uses.

    Every existing surface that prints a signal prints a level. This is what lets `impact` join
    `LEVELLED_SIGNALS` without any of them learning a new shape.
    """
    m = impact_magnitude(raw)
    return "low" if m < 1.0 else ("medium" if m < 2.0 else "high")

# Code defaults, not config defaults. D1 measured this and it is worth restating here because
# D00 got it wrong: the weight block is NOT in ~/.swarm/config.json. It exists only as this
# dict. Porting "the weight block from config" would port a block that is not there. The
# operator can still override it by writing a `signals` key; nothing here requires him to.
DEFAULT_SIGNAL_WEIGHTS = {
    "w_urgency": 1.0, "w_dependency_unblocking": 1.0, "w_charter_alignment": 1.0,
    "w_stakes": 1.0, "w_effort": 1.0, "w_age_per_day": 0.25,
    "gate_low_confidence": True,
}


def truthy(v) -> bool:
    return str(v if v is not None else "").strip().lower() in ("true", "yes", "y", "1", "on")


def norm_task_id(raw) -> str:
    """`#7` and `7` and `0007` are one task. Question ids keep their `q` and are not padded."""
    s = str(raw if raw is not None else "").lstrip("#").strip()
    if not s:
        return ""
    if s.startswith("q"):
        return "q" + s[1:].zfill(4)
    return s.zfill(4)


def signal_level(field: str, raw):
    """Fold one declared value onto low, medium or high. None means "not declared".

    Anything unreadable returns None and is therefore treated as unset, which means
    conservative. A garbage value must never land in the middle of the range.
    """
    v = str(raw if raw is not None else "").strip().lower()
    if not v:
        return None
    if v in SIGNAL_LEVELS:
        return v
    if v in SIGNAL_ALIASES.get(field, {}):
        return SIGNAL_ALIASES[field][v]
    if field == "impact" and NUMERIC_LITERAL.fullmatch(v):
        # Migration 48. Money read back as a band, on the magnitude's own thresholds, so a surface
        # that only knows how to print a level prints one that agrees with the number.
        return impact_band(v)
    if field in ("dependency_unblocking", "confidence"):
        # The canon carries `dependency_unblocking` as a count of items this task unblocks and
        # `confidence` as 0.0 to 1.0. Migration 6 accepts and folds both; before it the store's
        # CHECK refused them outright, which is D-CROSSTALK slot 7.
        #
        # NUMERIC_LITERAL is `brain.signal_numeric()`'s regex, character for character, and it
        # is here rather than a bare `float()` so that the two gates cannot accept different
        # sets. `float()` alone accepts four things Postgres does not -- `nan`, `inf`,
        # underscore separators (`1_0`), and non-ASCII digits -- and every one of them would
        # pass validation at post time and then die on a bare CHECK violation from the driver.
        #
        # TWO EDGES OF `bin/swarm` ARE THEREFORE NOT PORTED, both measured by running it rather
        # than reading it: `float('nan') < 0.4` is False and so is `< 0.8`, so the source folds
        # `confidence=nan` to HIGH -- a garbage string at the TOP of the range, worse than the
        # middle this docstring forbids, and enough to LIFT a task up the queue. And
        # `int(float('inf'))` raises OverflowError, which the source does not catch, so the
        # source CLI tracebacks on `--dependency-unblocking inf`. Both read unreadable here,
        # which means conservative, and the store refuses both on write.
        if not NUMERIC_LITERAL.fullmatch(v):
            return None
        f = float(v)
        if field == "dependency_unblocking":
            n = int(f)  # int(float(x)) truncates toward zero, and so does SQL trunc()
            return "low" if n <= 0 else ("medium" if n <= 2 else "high")
        return "low" if f < 0.4 else ("medium" if f < 0.8 else "high")
    return None


def validate_signal(field: str, raw):
    """Strict on the write path, tolerant on the read path.

    An unreadable value is refused at post time rather than silently folded to conservative,
    because the writer is present to fix it and the reader never is.
    """
    v = str(raw if raw is not None else "").strip()
    if not v:
        return ""
    # THE ONE AMBIGUITY IN THE WHOLE VOCABULARY, REFUSED OUT LOUD (migration 48). `impact=3` reads
    # as three dollars and as three out of five, and the two order the row very differently: as
    # money it floors at the bottom of the scale, as a rating it sits in the middle. Only the
    # writer knows which was meant and only the writer is present to be asked. Refusing is the
    # same discipline this function's docstring already states, applied to the one case where a
    # value is READABLE and still not safe to read.
    if field == "impact" and NUMERIC_LITERAL.fullmatch(v.lower()):
        money = float(v)
        # THE ACCEPTED SET IS `= 0 OR >= 100` AND IT IS WRITTEN AS ONE CONDITION ON EACH SIDE.
        # `brain.signal_ok` says exactly that; this said `0 < money < 100`, which is the same for
        # positive numbers and NOT the same for negative ones. `test_signal_parity_numeric` caught
        # it on three values (-1, -0.5, -0.3): SQL refused them, Python accepted them, and a post
        # that passed validation would have died on a bare CHECK violation from the driver. That
        # is the exact failure mode migration 6's own comment describes and the reason the two
        # gates are held equal by test rather than by reading.
        if money != 0 and money < IMPACT_MONEY_FLOOR:
            negative = " A negative impact is refused on both sides: work that destroys value is a" \
                       " reversibility and stakes question, not a smaller number." if money < 0 else ""
            raise ValueError(
                f"impact: {v!r} is ambiguous and is refused rather than guessed. A bare number "
                f"on `impact` is MONEY, so {v!r} means {v} of currency, which is the very bottom "
                f"of the scale. If you meant a rating, say `low`, `medium`, `high` or `critical`. "
                f"If you really do mean {v} of money, the row is worth `none`. Amounts of "
                f"{int(IMPACT_MONEY_FLOOR)} and above are accepted as written, and 0 means "
                f"none.{negative}")
    # URGENCY IS FOUR WORDS ON THE WRITE PATH, AS THE STORE HAS SAID SINCE MIGRATION 74. That migration
    # narrowed `brain.signal_ok('urgency', ...)` to none, soon, deadline and decaying, and this
    # function kept accepting low, medium and high, so a post that passed here died on a bare CHECK
    # violation from the driver -- the failure this function exists to prevent (W5-B2 E36, fixed by
    # W5-S7 2026-09-16). The READ path (`signal_level`) still folds all seven words, because rows
    # written before migration 74 may still hold them.
    if field == "urgency" and v.lower() not in URGENCY_WORDS:
        raise ValueError(
            f"urgency: {v!r} is not one of {', '.join(URGENCY_WORDS)}. Since migration 74 the store "
            f"refuses low, medium and high for urgency: say none, soon, deadline or decaying.")
    if signal_level(field, v) is None:
        vocab = ", ".join(SIGNAL_LEVELS + tuple(SIGNAL_ALIASES.get(field, {})))
        extra = ""
        if field == "dependency_unblocking":
            extra = ", or a count of items this task unblocks"
        elif field == "confidence":
            extra = ", or 0.0 to 1.0"
        elif field == "impact":
            extra = (f", or an amount of money ({int(IMPACT_MONEY_FLOOR)} or more, or 0 for none)")
        raise ValueError(f"{field}: {v!r} is not one of {vocab}{extra}")
    return v


def signal_weights(cfg: dict | None = None) -> dict:
    """Weights from config, so ordering is tunable without a code change.

    A bad edit falls back to the default for that one key rather than wedging the queue: an
    operator mistyping a weight must not be able to stop the fleet claiming work.
    """
    w = dict(DEFAULT_SIGNAL_WEIGHTS)
    for k, v in ((cfg or {}).get("signals") or {}).items():
        if k not in w:
            continue
        if k == "gate_low_confidence":
            w[k] = v if isinstance(v, bool) else truthy(v)
            continue
        try:
            w[k] = float(v)
        except (TypeError, ValueError):
            pass
    return w


def score_terms(levels: dict, age_days: float, w: dict) -> dict:
    """score = w_u*urgency + w_d*unblocking + w_c*charter + w_s*stakes - w_e*effort + aging

    Reversibility and confidence are deliberately absent from the sum: in the operator's model
    they decide surfacing, not ordering. The aging term is anti-starvation and can never cross a
    priority band, because the band is a separate sort key applied first.
    """
    return {
        "urgency": w["w_urgency"] * LEVEL_VALUE[levels["urgency"]],
        "unblocking": w["w_dependency_unblocking"] * LEVEL_VALUE[levels["dependency_unblocking"]],
        "charter": w["w_charter_alignment"] * LEVEL_VALUE[levels["charter_alignment"]],
        "stakes": w["w_stakes"] * LEVEL_VALUE[levels["stakes"]],
        "effort": -w["w_effort"] * LEVEL_VALUE[levels["effort"]],
        "age": w["w_age_per_day"] * max(0.0, age_days),
    }


def gate_reasons(external: bool, canon: bool, declared_confidence, w: dict) -> list:
    """Why a task surfaces to the operator whatever its other signals say.

    `low_confidence` fires only on a DECLARED low confidence. Confidence defaults conservative,
    so gating on the default would gate every unassessed task at once and the gate would stop
    carrying information.
    """
    out = []
    if external:
        out.append("external")
    if canon:
        out.append("canon_touching")
    if w.get("gate_low_confidence") and signal_level("confidence", declared_confidence) == "low":
        out.append("low_confidence")
    return out


def lane_match(task_lane: str, agent_lanes) -> bool:
    """An empty lane list claims nothing, which is how a planner is kept off the queue.

    Treating empty as "everything" would let the admiral claim and then personally execute the
    work it just decomposed. This is D00 contract rule 5 and it is enforced twice: here, and by
    the runner skipping the claim call outright for a planner role. Two independent gates,
    because the consequence of one of them being wrong is an agent executing its own plan.
    """
    if not agent_lanes:
        return False
    if "*" in agent_lanes:
        return True
    return task_lane in agent_lanes


def host_match(task_host: str, agent_host: str) -> bool:
    """A task with no host runs anywhere. A pinned task runs only on that machine.

    v1 is single-host and this ports INERT rather than dropped: see engine/HOST-AFFINITY.md.
    Every task posted by this engine has `host = ''`, so this returns True for everything until
    an operator pins one by hand, and the column, the flag and this function are all still here
    the day a second host exists.
    """
    if not task_host:
        return True
    return task_host == agent_host
