"""Turning a live run's token stream into dollars, and checking that the answer was right.

    python3 -m budget.price_card --stream RUN.stream.jsonl [--json]

Two jobs, and they are deliberately in one file because they must never disagree:

    derive_stream()   what this run has cost SO FAR, per engine session, mid-flight. This is
                      what `RunGuard.watch` charges against a ceiling while the engine runs.
    check()           at the end, compare what we derived against what the engine says it was
                      billed, and say WHICH KIND of disagreement it is. A brake that silently
                      under-counts is not a brake, and the only thing that can catch that is a
                      truth-up against first-party billing.

WHY THIS EXISTS. `budget/enforcer.py` used to state, as settled fact, that a live run's own
spend is unobservable: Claude Code carries `total_cost_usd` on the terminal `result` event and
nowhere else, and turning mid-run token counts into dollars "needs a price table keyed by model
and date, which no lane here owns". The first half is true and re-verified. The second half was
an assumption, and it cost the fleet the only brake that could stop a run on its own spend: over
the 2026-08-16/17 corpus, 31 of 146 completed runs individually cost more than $10 and the
largest single run was $34.55, all of it unobservable to the guard until it had already been paid.

THE CARD, AND WHY IT IS NOT A GUESS.

The four constants below are the PUBLISHED Claude Opus 5 list card, not a fit that happens to
look round, and every one is derived rather than eyeballed:

    input           $5.00/MTok    published Opus 5 list input
    output         $25.00/MTok    published Opus 5 list output
    cache_read      $0.50/MTok    0.1x base input, the standard cache-read multiplier
    cache_creation $10.00/MTok    2x base input, the standard ONE-HOUR cache-write multiplier
                                  (the 5-minute multiplier is 1.25x = $6.25; see the tier note)

Verified 2026-08-17 by T5 against 153 `*.stream.jsonl` in `~/.swarm/runs` (232,855 events, 0
unparseable): applied to the `message_delta` basis it reproduces Claude Code's own billed
`total_cost_usd` to $0.00000000 on 107 of the 108 priced sessions whose mid-run basis is
complete, and the single exception is a haiku line this file reports as a named residual rather
than folding into the card.

Read that paragraph as the load-bearing one. A COPIED CARD AND A FITTED CARD MUST BE TELLABLE
APART BY THE NEXT READER, and this one is both: published rates, independently reproduced against
first-party billing on a real corpus. Two traps for whoever refits it:

  1. A plain least-squares fit over that corpus lands on 5.007 / 25.005 / 0.49998 / 9.9996, and
     rounding THAT to the cent gives $5.01 for input and a 0.20% error. `input_tokens` runs about
     4 tokens against millions of `cache_read` tokens in the same regression, so it is barely
     identified and the fit's third decimal is noise. Recognise the card; do not ship the fit.
  2. The earlier decision package (0166) reported that these are NOT the published rates, citing
     $15/$75/$1.50/$18.75 and a 172.5% median error. That comparison used OPUS 4.x-era list
     pricing. Against Opus 5's own list card the numbers agree exactly. Corrected here 2026-08-17.

THE CACHE-CREATION TIER, which is the one number a future account could move under us.
`cache_creation` is billed at a different rate for 5-minute and 1-hour cache writes, and $10.00 is
the 1-hour rate. That is what this account uses and it was measured, not assumed: 17,536,198
cache-creation tokens across 168 stream files, 100.0% `ephemeral_1h`, zero `ephemeral_5m`. The
split IS visible mid-run -- `message_delta`'s `usage.iterations[].cache_creation` carries it, and
those sub-values sum to exactly the flat `cache_creation_input_tokens` -- so `read_stream` counts
the tiers separately and `check()` names a 5-minute mix as a specific, expected cause of drift
rather than leaving it to look like card rot. The published 5m rate is $6.25/MTok; this corpus
never priced it, so nothing here charges it.

ALSO MEASURED AND CARRIED FORWARD: all 111 priced sessions report `contextWindow` 1000000, so
this is the 1M-context card for this account. A 200k-context run is unmeasured here.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from decimal import Decimal

# --------------------------------------------------------------------------- the card

MTOK = Decimal(1_000_000)

# USD per MILLION tokens. See the module docstring for provenance: published Claude Opus 5 list
# rates, verified 2026-08-17 against 153 stream files in ~/.swarm/runs (232,855 events) to
# $0.00000000 on 107 of 108 priced sessions with a complete basis.
CARD_USD_PER_MTOK = {
    "input_tokens": Decimal("5.00"),
    "output_tokens": Decimal("25.00"),
    "cache_read_input_tokens": Decimal("0.50"),
    "cache_creation_input_tokens": Decimal("10.00"),   # the 1h write rate; 5m is $6.25, unpriced here
}

COMPONENTS = tuple(CARD_USD_PER_MTOK)

# `modelUsage` names the same four quantities in camelCase. Kept as an explicit map rather than a
# string transform because the mapping is a fact about the engine's output, not a naming rule.
MODEL_USAGE_KEYS = {
    "input_tokens": "inputTokens",
    "output_tokens": "outputTokens",
    "cache_read_input_tokens": "cacheReadInputTokens",
    "cache_creation_input_tokens": "cacheCreationInputTokens",
}

# What the self-check will forgive, and NOTHING ELSE. `total_cost_usd` and `costUSD` arrive as
# JSON numbers and come back through float, so a comparison against exact Decimal arithmetic can
# differ in the last representable place. One millionth of a dollar covers that and covers
# nothing real: the smallest thing the card can charge is a single output token at $0.000025.
#
# DO NOT WIDEN THIS. Every real discrepancy measured on this corpus has its own named channel
# below -- an un-streamed model is `absent_model_residual`, an under-reporting stream is
# `basis_incomplete`, a moved rate is `card_drift`. Widening the tolerance until the check stops
# firing converts all three into silence, which is the exact failure the check exists to prevent.
TOLERANCE_USD = Decimal("0.000001")


def derive_usd(tokens: dict) -> Decimal:
    """Dollars for one bag of token counts. Exact decimal arithmetic, no float anywhere."""
    return sum((Decimal(int(tokens.get(c) or 0)) * CARD_USD_PER_MTOK[c] / MTOK
                for c in COMPONENTS), Decimal(0))


# --------------------------------------------------------------------------- reading the stream


@dataclass
class SessionBasis:
    """One engine session's mid-run token basis, plus whatever end-of-run billing it has yet.

    Keyed by session id and not by file, because a file can hold more than one session: two
    terminals ran one (task, attempt) twice on 2026-08-16 and shared a stream (task 0244). Every
    event carries `session_id` at top level, so the split is free.
    """
    session_id: str
    tokens: dict = field(default_factory=lambda: dict.fromkeys(COMPONENTS, 0))
    cache_1h: int = 0            # from usage.iterations[].cache_creation, when the engine sends it
    cache_5m: int = 0
    stream_models: set = field(default_factory=set)   # models seen on message_start
    deltas: int = 0
    # End-of-run, present only once the session's result event lands.
    billed_usd: Decimal | None = None
    model_usage: dict = field(default_factory=dict)
    results: int = 0

    @property
    def derived_usd(self) -> Decimal:
        return derive_usd(self.tokens)


def _add_usage(basis: SessionBasis, usage: dict) -> None:
    for c in COMPONENTS:
        v = usage.get(c)
        if isinstance(v, int):
            basis.tokens[c] += v
    # The tier split lives one level down, per iteration. It is read for the self-check only:
    # nothing here charges 5m and 1h differently, because this corpus has never seen a 5m token
    # and a rate nobody measured is not a rate this lane ships.
    for it in usage.get("iterations") or []:
        cc = (it or {}).get("cache_creation") or {}
        basis.cache_1h += int(cc.get("ephemeral_1h_input_tokens") or 0)
        basis.cache_5m += int(cc.get("ephemeral_5m_input_tokens") or 0)


def read_stream(path: str) -> dict:
    """`session_id -> SessionBasis` for every session in a stream-json file.

    THE BASIS IS `stream_event` -> `event.type == "message_delta"` -> `usage`, SUMMED. Not the
    `assistant` event: that one snapshots `output_tokens` from `message_start` and carries about
    0.8% of what the run is billed for, and reading it is how a previous pass concluded the data
    was not there at all.

    A torn line is skipped rather than fatal. The file is being appended to by a live engine while
    this reads it, so the last line is routinely half-written; the next poll gets it whole.
    """
    out: dict = {}

    def basis(sid: str) -> SessionBasis:
        if sid not in out:
            out[sid] = SessionBasis(session_id=sid)
        return out[sid]

    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return out
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            sid = ev.get("session_id") or ""
            if not sid:
                continue
            kind = ev.get("type")
            if kind == "stream_event":
                inner = ev.get("event") or {}
                if inner.get("type") == "message_delta":
                    b = basis(sid)
                    b.deltas += 1
                    _add_usage(b, inner.get("usage") or {})
                elif inner.get("type") == "message_start":
                    model = ((inner.get("message") or {}).get("model") or "")
                    if model:
                        basis(sid).stream_models.add(model)
            elif kind == "result":
                b = basis(sid)
                b.results += 1
                usd = ev.get("total_cost_usd")
                if usd is not None:
                    # LAST WINS WITHIN A SESSION. `total_cost_usd` is cumulative: 0012-attempt1
                    # carries 19 result events on one session id climbing $5.7808 -> $27.2951, so
                    # summing them overstates that run 10.8x. Across DISTINCT sessions it is a sum.
                    try:
                        b.billed_usd = Decimal(str(usd))
                    except (TypeError, ValueError):
                        pass
                mu = ev.get("modelUsage")
                if isinstance(mu, dict) and mu:
                    b.model_usage = mu
    return out


def derive_stream(path: str) -> dict:
    """`session_id -> USD derived so far`. The mid-run reader, and all the guard needs."""
    return {sid: b.derived_usd for sid, b in read_stream(path).items()}


# --------------------------------------------------------------------------- the self-check


@dataclass
class SelfCheck:
    """What the derived number and the billed number said, and what the difference MEANS.

    The verdict is the whole point. The operator's condition on approving mid-run derivation was
    that a non-zero delta is a finding with its own incident row, not a log line -- but a check
    that fires on day one for a reason that is not card rot gets muted, and a muted check is worse
    than none. So a difference is only ever `card_drift` after the two innocent explanations have
    been measured and named.
    """
    session_id: str
    verdict: str                    # see classify() for the five values and what each one means
    derived_usd: Decimal
    billed_usd: Decimal | None
    streamed_billed_usd: Decimal    # modelUsage cost of the models the stream basis can see
    residual_usd: Decimal           # billed by models absent from the basis (haiku, today)
    delta_usd: Decimal              # derived - streamed_billed: the number that must be zero
    streamed_models: list = field(default_factory=list)
    absent_models: list = field(default_factory=list)
    token_gaps: dict = field(default_factory=dict)   # component -> (basis, modelUsage)
    detail: str = ""

    @property
    def is_finding(self) -> bool:
        """Does this need an incident row? Only the two verdicts a human must act on."""
        return self.verdict in ("card_drift", "basis_incomplete")

    def line(self) -> str:
        return (f"{self.session_id[:8] or '<none>'} {self.verdict}: derived ${self.derived_usd:.6f} "
                f"vs ${self.streamed_billed_usd:.6f} streamed-model billing "
                f"(delta ${self.delta_usd:.8f}), residual ${self.residual_usd:.6f}. {self.detail}")


def _model_matches(name: str, key: str, entry: dict) -> bool:
    """Does a `message_start` model name refer to this `modelUsage` entry?

    Three ways, because the engine is not consistent: the key is the model string, the entry's
    `canonicalModel` is, or one is a prefix of the other (`claude-haiku-4-5` vs
    `claude-haiku-4-5-20251001`). Prefix matching is deliberately last and deliberately anchored:
    a loose `in` would let `claude-opus-5` claim a hypothetical `claude-opus-5-mini` line.
    """
    if not name:
        return False
    canonical = (entry or {}).get("canonicalModel") or ""
    return name in (key, canonical) or key.startswith(name + "-") or name.startswith(key + "-")


def check(basis: SessionBasis) -> SelfCheck:
    """Compare the card's answer against the engine's own billing, and classify the difference.

    Five verdicts:

        in_flight              no result event yet, so there is nothing to check against.
        clean                  derived == billing for every model the stream carried, and no
                               other model was billed. The card is right and the basis is whole.
        absent_model_residual  derived == billing for the models the stream carried, and some
                               OTHER model was billed too. Measured cause: haiku. 2 of 148
                               sessions carry `claude-haiku-4-5-20251001` in `modelUsage` and
                               ZERO sessions in the whole corpus carry a haiku `message_start` or
                               `message_delta` -- it is billed and structurally invisible mid-run.
                               `0131-attempt1` is the whole story to the cent: derived $3.486704,
                               billed $3.493753, difference $0.007049, which is that run's haiku
                               line exactly, and its OPUS line is $3.486704 -- a zero-dollar match.
                               Corpus haiku spend is $0.483911 in total. NOT a finding: it is
                               reported by name and trued up at end of run.
        basis_incomplete       the token basis itself disagrees with what `modelUsage` says was
                               billed for the same models. The stream under-reported; the card is
                               not implicated. Measured on `0012-attempt1`, whose stream is NOT
                               truncated (its 93 message_deltas sum exactly to its 19 result
                               events' own `usage`) yet whose `modelUsage` reports 327,782 output
                               tokens against `usage`'s 55,701. Task 0250 owns the cause.
        card_drift             the tokens agree and the dollars do not. THIS is card rot, and it
                               is the only verdict that means "refit the card".

    basis_incomplete and card_drift have OPPOSITE REMEDIES -- fix the stream, or fix the card --
    which is why they are separated by a measurement (do the token counts match?) rather than by a
    dollar threshold. A tolerance can only ever tell you that something is wrong, never which.
    """
    derived = basis.derived_usd
    billed = basis.billed_usd

    if billed is None:
        return SelfCheck(basis.session_id, "in_flight", derived, None, Decimal(0), Decimal(0),
                         Decimal(0), detail="no result event yet; nothing to check against")

    mu = basis.model_usage or {}
    streamed_keys, absent_keys = [], []
    for key, entry in mu.items():
        if any(_model_matches(m, key, entry) for m in basis.stream_models):
            streamed_keys.append(key)
        else:
            absent_keys.append(key)

    def cost_of(keys) -> Decimal:
        return sum((Decimal(str((mu.get(k) or {}).get("costUSD") or 0)) for k in keys), Decimal(0))

    if mu:
        streamed_billed = cost_of(streamed_keys)
        residual = cost_of(absent_keys)
    else:
        # No modelUsage at all (an older engine, or a truncated result). Fall back to the session
        # total and say so: with nothing to attribute per model, an absent model is indistinguishable
        # from a stale card, and claiming otherwise would be the false confidence this check exists
        # to refuse.
        streamed_billed, residual = billed, Decimal(0)

    delta = derived - streamed_billed

    # The token comparison, which is the discriminator. modelUsage carries its own counts for the
    # same four components, so "did the stream see what was billed" is a measurement, not a guess.
    gaps = {}
    if streamed_keys:
        for c in COMPONENTS:
            billed_tokens = sum(int((mu.get(k) or {}).get(MODEL_USAGE_KEYS[c]) or 0)
                                for k in streamed_keys)
            if billed_tokens != basis.tokens.get(c, 0):
                gaps[c] = (basis.tokens.get(c, 0), billed_tokens)

    def sc(verdict: str, detail: str) -> SelfCheck:
        return SelfCheck(basis.session_id, verdict, derived, billed, streamed_billed, residual,
                         delta, sorted(basis.stream_models), sorted(absent_keys), gaps, detail)

    if abs(delta) <= TOLERANCE_USD:
        if abs(residual) <= TOLERANCE_USD:
            return sc("clean", "the card reproduces this session's billing exactly")
        return sc("absent_model_residual",
                  f"the card is exact for the streamed model(s) {sorted(basis.stream_models)}; "
                  f"${residual:.6f} was billed by {sorted(absent_keys)}, which never appears in "
                  f"the stream and cannot be seen mid-run. Named, charged at end of run, not a "
                  f"card problem")

    if gaps or not basis.stream_models or not mu:
        why = ("the stream carries no message_start, so the basis cannot be attributed to a model"
               if not basis.stream_models else
               "the result event carries no modelUsage, so nothing can be attributed per model"
               if not mu else
               "the mid-run token basis disagrees with the tokens modelUsage says were billed: "
               + "; ".join(f"{c} basis {a:,} vs billed {b:,}" for c, (a, b) in sorted(gaps.items())))
        return sc("basis_incomplete",
                  why + ". The remedy is upstream of the card: the engine did not stream what it "
                        "billed. Do NOT refit the card against this")

    tier = ""
    if basis.cache_5m:
        tier = (f" NOTE: this session carries {basis.cache_5m:,} ephemeral_5m cache-creation "
                f"tokens against {basis.cache_1h:,} 1h. The $10.00 constant is the 1-HOUR write "
                f"rate; the published 5-minute rate is $6.25 and this account has never billed "
                f"one. A 5m mix is the most likely cause of this drift and needs a tier-aware "
                f"card, not a nudged constant.")
    return sc("card_drift",
              f"the token basis matches modelUsage exactly and the dollars do not, off by "
              f"${delta:.8f} on ${streamed_billed:.6f}. That is the card, not the basis: refit it "
              f"against a fresh corpus and re-record the provenance in budget/price_card.py.{tier}")


def check_stream(path: str) -> list:
    """Every session in a file, checked. Ordered by session id so output is stable across runs."""
    return [check(b) for _, b in sorted(read_stream(path).items())]


# --------------------------------------------------------------------------- cli


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(prog="budget.price_card",
                                 description="derive a live run's spend from its token stream, "
                                             "and check the card against the engine's own billing")
    ap.add_argument("--stream", required=True, help="the run's stream.jsonl")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    checks = check_stream(a.stream)
    if a.json:
        print(json.dumps([{
            "session_id": c.session_id, "verdict": c.verdict, "finding": c.is_finding,
            "derived_usd": str(c.derived_usd),
            "billed_usd": None if c.billed_usd is None else str(c.billed_usd),
            "streamed_billed_usd": str(c.streamed_billed_usd),
            "residual_usd": str(c.residual_usd), "delta_usd": str(c.delta_usd),
            "streamed_models": c.streamed_models, "absent_models": c.absent_models,
            "token_gaps": {k: list(v) for k, v in c.token_gaps.items()},
            "detail": c.detail,
        } for c in checks]))
    else:
        for c in checks:
            print(c.line())
    return 5 if any(c.is_finding for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
