#!/usr/bin/env python3
"""An invented operator's working week, written into a SCRATCH store by the real verbs.

ALPHA-SPRINT-1, D-ALPHA-TESTDATA-1 (Andrew, 2026-09-14): test on a data store you set up yourself,
filled with good example data, never his real data. Built by A4 INFINITY-STREAMLINE so any seat
can rebuild the same week:

    BRAIN_PG_DB=brain_<something>_scratch_<...> BRAIN_PG_PORT=<not 5432> python3 seeds/alpha-example.py
    then, as the container superuser: seeds/alpha-example-dates.sql

WHAT IS IN IT, AND WHY EACH ONE. The Attention table, its right-hand panel and its empty states
only mean something when every kind and state is present, so the week carries:
  - reviews awaiting acceptance (one reopened and redone, one already accepted),
  - questions with and without a stated default, and one already answered,
  - an open recommendation and a rejected one,
  - intake objectives waiting, and one accepted,
  - the operator's own tasks, one of them snoozed,
  - a cancelled item and live agent work that is not the operator's to decide,
  - three projects, one resting.
Every name is invented and every address is @example.test. No string here is shaped like a key.

EVERY ROW COMES FROM A VERB (`store.apply`), for the reason web/bin/seed-demo.py gives: when the
store is wrong, what renders is wrong the same way. The operator's own rows need the operator's
login, so the store this runs against must have had `store/bin/provision-operator.sh` applied
INSIDE THE DISPOSABLE CONTAINER. There is no fallback to the runtime login, by design.
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_REPO, os.path.join(_REPO, "engine"), os.path.join(_REPO, "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DB = os.environ.get("BRAIN_PG_DB", "")
PORT = os.environ.get("BRAIN_PG_PORT", "5432")
if not DB or DB == "brain" or "scratch" not in DB:
    sys.exit("alpha-example: refusing. BRAIN_PG_DB must name a scratch database (it is %r)." % DB)
if PORT == "5432":
    sys.exit("alpha-example: refusing port 5432, which is the live brain-postgres on this host.")
os.environ.pop("SWARM_PARENT_TASK", None)

import store                                                            # noqa: E402
from swarm_engine import transitions as T                               # noqa: E402,F401
from swarm_engine import accept as A                                    # noqa: E402,F401
from swarm_engine import projects as P                                  # noqa: E402,F401
from human_queue import transitions as HQ                               # noqa: E402,F401

WORKDIR = _REPO
STEP = ["start"]


def step(name):
    STEP[0] = name


def apply(verb, **kw):
    return store.apply(verb, **kw)


def post(**kw):
    kw.setdefault("workdir", WORKDIR)
    return apply("post", **kw)["id"]


def claim(agent, lane, expect):
    got = (apply("claim", agent=agent, lanes=[lane], role="worker") or {}).get("id")
    if got != expect:
        raise RuntimeError("claim by %s on %s took %r, not %r" % (agent, lane, got, expect))


SIGNALS_HIGH = {"stakes": "high", "reversibility": "high", "urgency": "soon",
                "dependency_unblocking": "medium", "effort": "medium", "confidence": "high",
                "charter_alignment": "high"}
SIGNALS_LOW = {"stakes": "low", "reversibility": "high", "urgency": "decaying",
               "dependency_unblocking": "low", "effort": "low", "confidence": "medium",
               "charter_alignment": "medium"}


def main() -> int:
    step("projects")
    apply("project add", slug="northwind-renewal", title="Northwind renewal", by="operator")
    apply("project add", slug="pricing-refresh", title="Pricing page refresh", by="operator")
    apply("project add", slug="hiring-ops", title="Hiring operations", state="hold",
          reason="paused until the October budget review", by="operator")

    step("review awaiting acceptance: renewal terms")
    renewal = post(title="Summarise the Northwind renewal terms for Maren Okafor", lane="sales",
                   posted_by="operator", priority=4, agent_claimable=True, signals=SIGNALS_HIGH,
                   canonical_task="northwind-renewal#terms",
                   body="Maren Okafor (maren.okafor@example.test) wants a one-page summary of the "
                        "renewal: term length, price change, notice period.")
    claim("ada", "sales", renewal)
    apply("run start", id=renewal, attempt=1, agent="ada", host="scratch", session_id="ex-ada-1")
    apply("done", id=renewal, agent="ada",
          summary="- 24-month term proposed, 12-month available at 6% more\n"
                  "- price rises 4% from 1 November\n"
                  "- notice period stays at 60 days\n"
                  "- one clause (auto-renewal) needs a human read before Maren sees it\n")
    apply("run end", id=renewal, attempt=1, exit_code=0, outcome="done")

    step("review awaiting acceptance: pricing FAQ")
    faq = post(title="Draft the FAQ for the refreshed pricing page", lane="content",
               posted_by="operator", priority=3, agent_claimable=True, signals=SIGNALS_LOW,
               canonical_task="pricing-refresh#faq")
    claim("brook", "content", faq)
    apply("done", id=faq, agent="brook",
          summary="- 9 questions drafted from the last 40 support tickets\n"
                  "- two answers depend on the unannounced annual plan and are marked TBC\n")

    step("reviewed, reopened, redone")
    onboarding = post(title="Tidy the onboarding checklist for new clients", lane="ops",
                      posted_by="operator", priority=2, agent_claimable=True, signals=SIGNALS_LOW)
    claim("cyan", "ops", onboarding)
    apply("done", id=onboarding, agent="cyan", summary="Checklist tidied.")
    apply("reopen", id=onboarding, agent="operator",
          reason="The checklist still asks for a fax number. Remove it and say what else changed.")
    claim("cyan", "ops", onboarding)
    apply("done", id=onboarding, agent="cyan",
          summary="- removed the fax field\n- merged the two ID steps into one\n"
                  "- 14 steps down to 11\n")

    step("review accepted (history)")
    invoice = post(title="Reconcile the August invoices against the bank export", lane="finance",
                   posted_by="operator", priority=3, agent_claimable=True, signals=SIGNALS_HIGH)
    claim("ada", "finance", invoice)
    apply("done", id=invoice, agent="ada", summary="- 212 invoices matched\n- 3 duplicates flagged\n")
    # `as_operator=True` spelled out: `store.apply` picks the human login from the KEYWORD, not from
    # the verb's default, and a runtime login is refused (migration 36). Measured in slot 3.
    apply("accept work", id=invoice, by="operator", as_operator=True)

    step("questions")
    quote = post(title="Send Tobias Lindqvist the revised quote", lane="sales", posted_by="brook",
                 priority=4, signals=SIGNALS_HIGH, canonical_task="northwind-renewal#quote")
    apply("claim", agent="brook", lanes=["sales"], role="worker")
    quote_q = apply("ask", agent="brook", task=quote,
                    question="Send Tobias (tobias.lindqvist@example.test) the revised quote on Friday?",
                    default="hold until Friday, no action before then")
    workshop = post(title="Book the discovery workshop with Priya Raman", lane="ops",
                    posted_by="cyan", priority=3, signals=SIGNALS_LOW)
    apply("claim", agent="cyan", lanes=["ops"], role="worker")
    apply("ask", agent="cyan", task=workshop,
          question="Which week suits the workshop with Priya (priya.raman@example.test)?",
          default="")
    venue = post(title="Choose the venue for the client dinner", lane="ops", posted_by="brook",
                 priority=2, signals=SIGNALS_LOW)
    apply("claim", agent="brook", lanes=["ops"], role="worker")
    q = apply("ask", agent="brook", task=venue, question="The harbour room or the garden room?",
              default="the harbour room")
    apply("answer", qid=q["id"], text="the garden room, it seats twelve", requeue=False)

    step("recommendations")
    apply("recommend", subject_type="work_item", subject_id=renewal,
          text="Offer Northwind the 12-month term as the default",
          rationale="Against: the 24-month term locks in the price rise for longer and is what "
                    "their finance lead asked about first.",
          produced_by="ada", by="ada", requires_human=True)
    rejected = apply("recommend", subject_type="work_item", subject_id=faq,
                     text="Publish the FAQ before the annual plan is announced",
                     rationale="Against: two answers would say TBC on a public page.",
                     produced_by="brook", by="brook", requires_human=True)
    apply("recommend reject", id=rejected["id"], by="operator", as_operator=True,
          reason="Not with TBC answers on a public page.")

    step("intake objectives")
    apply("intake", name="Call note: Jonas Weber wants an October pilot",
          body="Jonas Weber (jonas.weber@example.test) asked for a four-week pilot starting in "
               "October, with two seats and a review call at the end.",
          source_name="call-notes/jonas-weber.md", source_signature="412:1757800000")
    apply("intake", name="Email: Aiko Tanaka disputes invoice 3107",
          body="Aiko Tanaka (aiko.tanaka@example.test) says invoice 3107 bills two seats that "
               "were cancelled in July.",
          source_name="mail/aiko-tanaka-3107.eml", source_signature="905:1757810000")
    apply("intake", name="Idea: a quarterly client newsletter",
          body="A short quarterly note to clients: what shipped, what is next.",
          source_name="notes/newsletter-idea.md", source_signature="120:1757700000")
    apply("accept", name="Idea: a quarterly client newsletter")

    step("the operator's own tasks")
    post(title="Call Aiko Tanaka about the invoice dispute", lane="finance",
         posted_by="operator", actor_type="human", priority=4, signals=SIGNALS_HIGH)
    snoozed = post(title="Renew the office insurance", lane="ops", posted_by="operator",
                   actor_type="human", priority=2, signals=SIGNALS_LOW)
    apply("queue defer", source_type="work_item", source_id=snoozed, kind="until-time",
          wake_at="2026-09-21T08:00:00+00:00", reason="broker is away until next week",
          label="next Monday", by="operator")

    step("cancelled and live agent work")
    old = post(title="Trial a weekly newsletter format", lane="content", posted_by="operator",
               priority=1, agent_claimable=True, signals=SIGNALS_LOW)
    apply("cancel", id=old, reason="replaced by the quarterly newsletter idea", agent="operator")
    live = post(title="Collect competitor pricing pages for the refresh", lane="research",
                posted_by="operator", priority=2, agent_claimable=True, signals=SIGNALS_LOW,
                canonical_task="pricing-refresh#research")
    claim("cyan", "research", live)
    apply("heartbeat", agent="cyan", status="working", task=live, host="scratch")
    apply("heartbeat", agent="ada", status="idle", host="scratch")
    apply("heartbeat", agent="brook", status="idle", host="scratch")

    step("membrane: prepared context so items reach Decide and Review")
    for wid in (renewal, faq, onboarding):
        apply("queue classify", source_type="work_item", source_id=wid, item_class="review",
              prepared_context_link="/task/%s" % wid, by="ada")
    # A DECIDE CARD OFFERING THE DEFAULT VERB, the way web/bin/seed-demo.py earns one: a stated
    # default, a prepared context and a counterargument on the question. Without it the keyboard
    # suite's default-verb check has no card to read (measured in slot 4: "0 of them").
    apply("queue classify", source_type="question", source_id=quote_q["id"], item_class="approval",
          prepared_context_link="/task/%s" % quote,
          counterargument="Tobias asked for the quote before his Thursday budget call.", by="brook")

    step("counts")
    with store.read() as s:
        for table in ("project", "work_item", "question", "recommendation", "objective", "thread",
                      "agent", "queue_open"):
            print("count %-15s %s" % (table, s.scalar("SELECT count(*) FROM brain.%s" % table)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:                                            # noqa: BLE001
        sys.exit("alpha-example: step %r failed: %s: %s" % (STEP[0], type(exc).__name__, exc))
