#!/usr/bin/env python3
"""`Send back` AND `Accept work` REPORT WHAT THEY ACTUALLY DID. Rows `0414` and `0413`.

Both come out of the operator's first end-to-end drive of the product, 2026-08-28, and both are
the same failure seen from the two ends of one click: the card did not tell him what he needed
before he acted, or what happened after.

  `0414`  `Send back` runs `reopen`, which clears `claimed_by` and leaves `agent_claimable`
          EXACTLY as it found it. Measured either side of his click: before, `agent_claimable`
          true and `brain.queue_open` returned 1 row for the item; after, `agent_claimable` still
          true and it returned 0. The row went to the FLEET and left the human queue entirely,
          the receipt stripe said *"the item is already back in the queue, unspent"*, and his own
          recording reads *"It has now disappeared from the queue, so I guess that is good."* He
          scored a silent failure as a success, and on live the fleet is PAUSED, so the same
          click moves work to nobody.
  `0413`  *"nothing happened when I accepted work. That would maybe be a moment for confetti and
          for it to change from accept work to like accepted. Maybe you have an option to like
          unaccept or like send back or undo the acceptance would be good."* Measured on
          `/task/0039`, an accepted row: the words `Accept work` appeared 0 times, `Unaccept` 0
          times, and NO CONTROL AT ALL. The button did not become `accepted`; it vanished.

WHAT EACH CHECK PINS:

  1. THE DESTINATION IS ON     The `Send back` panel states, before the submit, whether this row
     THE PATH TO THE BUTTON.   goes to the fleet or back to the operator, and names the command
                               that takes it back. The panel is the only way to reach the verb,
                               so the sentence cannot be skipped.
  2. A PAUSED FLEET SAYS SO.   With `fleet_paused` true the same panel says the work will sit
                               with nobody. This is the live configuration.
  3. THE RECEIPT IS MEASURED,  After the click, the stripe names where the row actually went, and
     NOT ASSERTED.            `brain.queue_open` is read either side to prove the sentence.
  4. `Accept work` LEAVES AN   The stripe carries `Unaccept`, per MUST-NOT-BUILD item 10 now that
     INVERSE.                  a real inverse verb exists.
  5. UNACCEPT RETURNS THE ROW  `accepted_at` and `accepted_by` clear; `state` and `result` are
     AND KEEPS THE REPORT.     byte-identical, and the row is back in `brain.queue_open`.
  6. AN ACCEPTED ITEM SAYS SO  `/task/<id>` renders the accepted state with who and when, and the
     AND OFFERS THE WAY BACK.  inverse under it with a typed reason.

Run:  BASE=http://127.0.0.1:3108 python3 web/tests/test_the_card_says_where_the_work_went.py

It SEEDS ITS OWN TWO ROWS and spends only those, so the demo board is not consumed to prove a
defect. The console at BASE must be on a scratch store; `_console_guard` reads the serving
process and refuses `brain` and `brain_scratch`.
"""

from __future__ import annotations

import os
import sys

_R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_R, os.path.join(_R, "engine"), os.path.join(_R, "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _console_guard                                                   # noqa: E402
from playwright.sync_api import sync_playwright                         # noqa: E402

BASE = os.environ.get("BASE", "http://127.0.0.1:3108")
CHROME = os.environ.get(
    "CHROME", os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux/chrome"))
# A LANE PER RUN, because `claim` names no task. An abandoned row from an earlier run sits in
# the lane claimable forever, and the next run's claim takes THAT one: measured, `claim took
# '0060', not '0062'`. A unique lane makes the only claimable row in it the one just posted, so
# the check below can be an assertion rather than a hope.
LANE = "wave2probe-" + os.urandom(3).hex()

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'' if ok else '   ' + detail}")
    return ok


def seed(store, report: str) -> str:
    """One finished agent row, claimed and reported by an agent, `agent_claimable` still true.

    The claim goes through `claim`, which names no task and hands out the next claimable one, so
    the id it took is CHECKED against the id just posted. The demo seeder learned that the hard
    way: eight days in which every claim in it silently matched zero rows.
    """
    r = store.apply("post", title="wave 2 probe", lane=LANE, workdir="/tmp",
                    agent_claimable=True, posted_by="wave2")
    tid = r["id"] if isinstance(r, dict) else r
    got = store.apply("claim", agent="T-w2", lanes=[LANE], role="worker") or {}
    if got.get("id") != tid:
        sys.exit(f"claim took {got.get('id')!r}, not {tid!r}. Refusing to seed blind.")
    store.apply("done", id=tid, summary=report, agent="T-w2")
    return tid


def row(store, tid: str) -> dict:
    with store.read() as s:
        w = s.one("SELECT state, agent_claimable, coalesce(result,'') AS result, accepted_at, "
                  "coalesce(accepted_by,'') AS accepted_by FROM brain.work_item WHERE id = %s",
                  (tid,))
        w["in_queue"] = s.scalar("SELECT count(*) FROM brain.queue_open WHERE source_id = %s",
                                 (tid,))
    return w


def send_back(page, tid, store, *, paused: bool):
    page.goto(f"{BASE}/task/{tid}", wait_until="networkidle")
    page.click(f"button[data-toggle='sb-{tid}']")
    hint = page.inner_text(f"#sb-{tid} .hint")

    # 1 and 2. The panel is the ONLY way to the verb, so what it says is on the path.
    check("the send-back panel names the fleet as the destination",
          "goes back to the FLEET and leaves your queue" in hint, hint[:200])
    check("the send-back panel names the command that takes it back",
          f"swarm set {tid} agent_claimable false --as-operator" in hint, hint[:200])
    check("a paused fleet is stated before the click" if paused
          else "a running fleet is stated before the click",
          ("FLEET IS PAUSED RIGHT NOW" in hint) is paused, hint[:200])

    before = row(store, tid)
    page.fill(f"#sb-{tid} textarea", "the July figure has to be in before this can be accepted")
    page.click(f"#go-sb-{tid}")
    page.wait_for_timeout(2500)
    after = row(store, tid)

    # 3. The measurement the receipt is a claim about.
    check("reopen left agent_claimable true and took the row out of the human queue",
          before["in_queue"] == 1 and after["in_queue"] == 0 and after["agent_claimable"],
          f"before {before['in_queue']} rows, after {after['in_queue']} rows, "
          f"agent_claimable {after['agent_claimable']}")
    page.goto(f"{BASE}/queue?tier=judge", wait_until="networkidle")
    stripes = page.eval_on_selector_all(".receipt", "els => els.map(e => e.innerText)")
    text = "\n".join(stripes)
    check("the receipt says the row went to the fleet and out of his queue",
          "went to the FLEET" in text and "left your queue" in text, text[:300])
    check("the receipt no longer claims the item is back in his queue",
          "already back in the queue" not in text, text[:300])
    if paused:
        check("the receipt says the fleet it went to is stopped",
              "WHICH IS PAUSED" in text, text[:300])
        check("the receipt names the command that takes it back",
              f"swarm set {tid} agent_claimable false" in text, text[:300])


def accept_then_unaccept(page, tid, store):
    page.goto(f"{BASE}/task/{tid}", wait_until="networkidle")
    was = row(store, tid)
    page.click("form.act-form:has(input[value='accept_work']) button.act")
    page.wait_for_timeout(2500)
    acc = row(store, tid)
    check("accept work recorded the acceptance",
          acc["accepted_at"] is not None and acc["accepted_by"] != "",
          f"accepted_at {acc['accepted_at']}, by {acc['accepted_by']!r}")

    # 6. The state is a state, and the way back is under it.
    page.goto(f"{BASE}/task/{tid}", wait_until="networkidle")
    body = page.inner_text("body")
    check("the accepted item says it is accepted, with who and when",
          f"{tid} is accepted" in body and acc["accepted_by"] in body, body[:200])
    check("the accepted item offers Unaccept",
          page.locator(f"button[data-toggle='un-{tid}']").count() == 1)

    # 4. And so does the stripe, which is where he was standing when he complained. The stripe
    #    is seven seconds long, so it is read from a fresh accept rather than from the one above.
    page.click(f"button[data-toggle='un-{tid}']")
    page.fill(f"#un-{tid} textarea", "read the report again: the rebuild was never tested")
    page.click(f"#go-un-{tid}")
    page.wait_for_timeout(2500)
    un = row(store, tid)

    # 5. The whole argument for a second verb: the report survives.
    check("unaccept cleared the acceptance",
          un["accepted_at"] is None and un["accepted_by"] == "",
          f"accepted_at {un['accepted_at']}, by {un['accepted_by']!r}")
    check("unaccept left state and result untouched",
          un["state"] == was["state"] == "done" and un["result"] == was["result"],
          f"state {un['state']!r}, result identical: {un['result'] == was['result']}")
    check("the row is back in the queue with Accept work on it", un["in_queue"] == 1,
          f"{un['in_queue']} rows in brain.queue_open")
    page.goto(f"{BASE}/task/{tid}", wait_until="networkidle")
    check("the item page offers Accept work again",
          page.locator("input[value='accept_work']").count() == 1)


def stripe_carries_the_inverse(page, tid, store):
    """MUST-NOT-BUILD item 10: where a real inverse verb exists the stripe carries it."""
    page.goto(f"{BASE}/task/{tid}", wait_until="networkidle")
    page.click("form.act-form:has(input[value='accept_work']) button.act")
    page.wait_for_timeout(2500)
    page.goto(f"{BASE}/queue?tier=judge", wait_until="networkidle")
    labels = page.eval_on_selector_all(".receipt button",
                                       "els => els.map(e => e.innerText.trim())")
    check("the accept receipt stripe carries Unaccept rather than `no undo`",
          "Unaccept" in labels, f"stripe buttons: {labels}")
    stripes = page.eval_on_selector_all(".receipt", "els => els.map(e => e.innerText)")
    check("the accept receipt no longer says nothing clears an acceptance",
          "nothing clears it" not in "\n".join(stripes), "\n".join(stripes)[:200])
    page.click(".receipt button:has-text('Unaccept')")
    page.wait_for_timeout(2500)
    back = row(store, tid)
    check("the stripe's Unaccept actually withdrew the acceptance",
          back["accepted_at"] is None and back["in_queue"] == 1,
          f"accepted_at {back['accepted_at']}, {back['in_queue']} queue rows")
    with store.read() as s:
        why = s.query("SELECT text FROM brain.thread WHERE work_item_id = %s AND kind = "
                      "'unaccept' ORDER BY seq DESC LIMIT 1", (tid,))
    check("the stripe's withdrawal names its own surface on the thread",
          bool(why) and "receipt stripe" in why[0]["text"],
          why[0]["text"][:200] if why else "no unaccept row on the thread")


def verdict() -> int:
    """The verdict, and beside it the count of checks that produced it. Task 0445.

    A module-level function rather than four lines at the foot of `main` on purpose. `main`
    cannot run without a console on a scratch store and a browser, so a guard living inside it
    is one nobody can fire on demand; here the zero branch is reachable from a probe that
    imports this file and sets the counters, which is the difference between asserting the rule
    and demonstrating it. This suite is the one with the most to lose from an empty set: it
    SEEDS its own four rows, so a seed that silently produced nothing, or four scenarios that
    all raised past their `check` calls, leaves PASS and FAIL both empty and exits 0.
    """
    if len(PASS) + len(FAIL) == 0:                                      # DENOMINATOR
        print("0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


def main():
    served = _console_guard.refuse_unless_scratch(
        BASE, what="test_the_card_says_where_the_work_went.py")
    os.environ["BRAIN_PG_DB"] = served["db"]
    import store                                                        # noqa: E402
    from swarm_engine import transitions as _T                          # noqa: E402,F401
    from swarm_engine import accept as _A                               # noqa: E402,F401
    from human_queue import transitions as _HQ                          # noqa: E402,F401
    print(f"console {BASE} is pid {served['pid']} on {served['db']!r}\n")

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROME,
                               args=["--no-sandbox", "--hide-scrollbars"])
        page = b.new_context(viewport={"width": 1440, "height": 1057}).new_page()

        # ALL FOUR ROWS ARE SEEDED BEFORE THE FIRST PAUSE, and that is not tidiness. `claim`
        # refuses outright while `fleet_paused` is true (`transitions.claim`, which reads the
        # flag as the string `'true'` rather than for truthiness), so a row seeded inside the
        # paused block would take nothing and this suite would prove a defect against a task
        # that does not exist. Measured: `claim took None, not '0061'`.
        rows = [seed(store, "Reconciled. The July figure is still missing."),
                seed(store, "Reconciled. The July figure is still missing."),
                seed(store, "Playbook step added. Never tested on a rebuild."),
                seed(store, "Cover redrawn. The wordmark is still the 2024 lockup.")]

        print("--- 0414: send back, fleet running")
        send_back(page, rows[0], store, paused=False)

        print("--- 0414: send back, fleet PAUSED (the live configuration)")
        store.apply("pause", by="operator", note="row 0414 regression check")
        try:
            send_back(page, rows[1], store, paused=True)
        finally:
            store.apply("resume", by="operator")

        print("--- 0413: accept, then unaccept from the item page")
        accept_then_unaccept(page, rows[2], store)

        print("--- 0413: accept, then unaccept from the receipt stripe")
        stripe_carries_the_inverse(page, rows[3], store)
        b.close()

    sys.exit(verdict())


if __name__ == "__main__":
    main()
