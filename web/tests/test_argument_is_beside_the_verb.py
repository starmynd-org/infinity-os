#!/usr/bin/env python3
"""THE ARGUMENT AGAINST A DECISION IS ON THE SAME SCREEN AS THE BUTTON THAT ENDS IT.

Rows `0410` and `0416`, from the operator's own end-to-end drive of 2026-08-28. They were filed
as two defects on two verbs and they are one sentence: *what should change his mind was one
interaction away from the control that ends the decision.*

  `0410`  `/queue?tier=shape` rendered `Approve` and `Reject` live on a recommendation whose
          counterargument is stored in full, and the word `Against` appeared 0 times in the HTML.
          The block was inside `{% if expanded %}` and the tier list renders collapsed. Asked
          whether he would have known an argument existed that he had not read: *"approved it,
          no i wouldn't have known"*.
  `0416`  `/queue?tier=judge` rendered 8 `Accept work` buttons and 0 occurrences of any of the
          four self-stated caveats its own cards' reports carry. *"I only see it in the report in
          full and not in something that would like tie in to where I should accept work or
          not."*

WHY THIS SUITE MEASURES GEOMETRY AND NOT `grep`. A wave working these rows nearly shipped a fix
that rendered at y=1054 on a 1057px viewport, below the fold on the screen it was fixing, and
called it the same defect wearing a scrollbar. Presence in the HTML is not the claim. The claim
is ADJACENCY: there is no scroll position at which the operator can see the verb and not the
argument. So every check below is taken from a real browser, from `getBoundingClientRect`, on
every card the board actually rendered:

  1. EVERY POSITION CARD    A card carrying `Approve` or `Accept work` carries the block. Not
     CARRIES THE BLOCK.     most of them, and not the expanded one.
  2. IT IS ABOVE THE VERB   Directly above `.btns`, in the same card, so it cannot be scrolled
     AND TOUCHING IT.       away from the thing it argues with.
  3. NO EMPTY SLOT.         Every block says which of the three states it is in. Absent and
                            merely unrendered looking alike is the whole of `0410`.
  4. THE ARGUMENT ITSELF    A recommendation with a rationale prints that rationale, not a
     IS ON SCREEN.          placeholder. `0410` failed on exactly this: the data reached the
                            card and the template did not print it.
  5. THE VERB IS VISIBLE    Scroll each card's primary button into view and assert the block is
     WITH IT.               inside the viewport at the same instant. This is the y=1054 check.

Run:  BASE=http://127.0.0.1:3107 python3 web/tests/test_argument_is_beside_the_verb.py

The console at BASE must be on a scratch store; `_console_guard` reads the SERVING PROCESS and
refuses `brain` and `brain_scratch`, because this suite drives a real browser at real cards.
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

BASE = os.environ.get("BASE", "http://127.0.0.1:3107")
CHROME = os.environ.get(
    "CHROME", os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux/chrome"))

# 1057 is not a round number and that is why it is here: it is the viewport the y=1054 near-miss
# was measured on. A check that only ever ran at 1200 would have passed that render too.
VIEWPORT = {"width": 1440, "height": 1057}

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'' if ok else '   ' + detail}")
    return ok


# The two verbs of position. `Defer`, `Bump` and `Open` are not positions on the work and carry
# no argument, which is the same distinction `macros.secondary` draws in its own comment.
POSITION_VERBS = ("Approve", "Accept work")

RECT = """
(sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return {top: r.top, bottom: r.bottom, height: r.height,
          text: (el.innerText || '').trim()};
}
"""


def run(page, tier):
    page.goto(f"{BASE}/queue?tier={tier}", wait_until="networkidle")
    cards = page.eval_on_selector_all(
        ".card", "els => els.map(e => e.id).filter(Boolean)")
    seen = 0
    for cid in cards:
        # THE ROW IS OPENED FIRST, BECAUSE SINCE ROW 0431 A CARD AT REST IS A SPREADSHEET ROW AND
        # ITS VERBS ARE ONE CLICK AWAY. This is a change to how the suite REACHES the surface and
        # not to what it asserts: every check below is byte for byte the check it was, and the
        # 0..24px tolerance in particular is untouched.
        #
        # It is also the check's own subject. `.beforeact` and `.btns` now live inside the same
        # `.qexp`, so they arrive together or not at all, and what this suite measures is exactly
        # the moment the verb becomes reachable. Before this line the three geometry reads ran
        # against a `display:none` subtree and every rectangle was 0..0, which reported "the verb
        # is visible and the argument is not" about a card on which NEITHER was visible. That is
        # a false red, and a false red on this suite is worse than most: it is the regression
        # test for the defect the operator filed with the words "approved it, no i wouldn't have
        # known".
        opener = page.query_selector(f"#{cid} .qopen")
        if opener:
            opener.click()
            page.wait_for_timeout(60)
        primary = page.eval_on_selector_all(
            f"#{cid} .btns button.act, #{cid} .btns a.act",
            "els => els.map(e => (e.innerText || '').trim())")
        label = next((p for p in primary if p in POSITION_VERBS), None)
        if not label:
            continue
        seen += 1
        blk = page.evaluate(RECT, f"#{cid} .beforeact")
        btns = page.evaluate(RECT, f"#{cid} .btns")
        if not check(f"{tier}/{cid} ({label}) carries the argument block", blk is not None):
            continue

        # 2. above the verbs, in the same card, and touching them. `.exp` sets a .6rem margin
        #    and a .6rem padding above its own rule, so the gap below it is the card's own
        #    spacing and nothing else fits between.
        gap = btns["top"] - blk["bottom"]
        check(f"{tier}/{cid} block is directly above the verbs (gap {gap:.0f}px)",
              0 <= gap <= 24, f"gap {gap:.1f}px between the block and .btns")

        # 3. no empty slot: the block always says which of the three states it is.
        check(f"{tier}/{cid} the block says something",
              len(blk["text"]) > 40, f"only {len(blk['text'])} chars: {blk['text']!r}")

        # 5. the y=1054 check. Put the button where the operator would have it and assert the
        #    argument is in the viewport at the same instant.
        page.eval_on_selector(f"#{cid} .btns", "e => e.scrollIntoView({block: 'center'})")
        page.wait_for_timeout(60)
        blk2 = page.evaluate(RECT, f"#{cid} .beforeact")
        btn2 = page.evaluate(RECT, f"#{cid} .btns")
        h = VIEWPORT["height"]
        both = (0 <= btn2["top"] < h) and (blk2["bottom"] > 0) and (blk2["top"] < h)
        check(f"{tier}/{cid} argument is on screen with the verb "
              f"(block {blk2['top']:.0f}..{blk2['bottom']:.0f}, verbs at {btn2['top']:.0f}, "
              f"viewport {h})", both,
              "the verb is visible and the argument is not")
    return seen


def a_recommendation_with_no_counterargument(page, served) -> int:
    """SECTION 4b's INPUT, WHICH THE RUNNER'S OWN SEED GUARANTEES IS ABSENT. Row 0466.

    The check below it -- *a recommendation with none renders the finding rather than an empty
    block* -- has been red on every run anybody has made of this suite, and it is not a defect in
    the product. It is an `any()` over an empty set.

    THE MEASUREMENT, not a guess. `web/bin/seed-demo.py` raises exactly ONE recommendation, and it
    raises it WITH a rationale, on purpose. Its own comment says why:

        with a rationale, because the card renders a recommendation with no counterargument as
        the finding it is and a seed with none would exercise only the finding.

    So on the board this runner builds, `SELECT count(*) FROM brain.recommendation WHERE state =
    'open' AND coalesce(rationale, '') = ''` is 0, and a check asserting that the
    missing-counterargument finding is on screen could never have been anything but red. It was
    reporting a FIXTURE GAP in the words of a product defect, which is the shape
    `docs/SUITE-INPUT-RULE.md` exists against, and the reason it survived is that the check
    printed no denominator: `any(...)` over nothing looks exactly like `any(...)` over something.

    The template was never wrong. `macros.html:573` and `detail_rec.html:15` both carry the
    sentence, `web/model.py:566` sets `card["counter"]` from `brain.recommendation.rationale`, and
    `macros.before_you_act` prints the finding whenever that is falsy. There was nothing to fix
    on the product side and nothing to soften on the test side.

    A suite that needs a fixture the runner does not build has two honest options, and
    `test_the_keyboard_and_the_phone.py:seed_one_rail_if_the_board_has_none` chose between them
    yesterday for the identical reason: say NOT MEASURED forever, or build it. This builds it,
    through the registered `recommend` transition -- the same door `queue recommend` calls -- so
    nothing here is a second way to create a recommendation. It is a no-op when the board already
    carries one, so running this suite twice does not raise two.

    IT REFUSES TO WRITE INTO A STORE IT IS NOT SURE OF. `_console_guard` has already established
    that the console at BASE is on a scratch database, but this process writes through its OWN
    connection, and the runner's own history is that exporting one database name and not the
    other TRUNCATEs one store while asserting on another. So the name this process would write to
    has to equal the name the serving console reads from, or nothing is written and the scene is
    NOT MEASURED with the mismatch printed.

    Returns the number of counterargument-free recommendations the board carries afterwards. 0
    means the scene could not be measured, and the caller says so rather than asserting.
    """
    import store                                                        # noqa: PLC0415
    import human_queue.transitions                                      # noqa: PLC0415,F401

    mine, theirs = os.environ.get("BRAIN_PG_DB") or "", served.get("db") or ""
    if mine != theirs:
        print(f"      NOT MEASURED: this process would write to {mine!r} and the console at "
              f"{BASE} serves {theirs!r}. A fixture written into the wrong store is worse than "
              f"an unmeasured scene, so nothing was written.")
        return 0

    with store.read() as s:
        n = s.scalar("SELECT count(*) FROM brain.recommendation "
                     "WHERE state = 'open' AND coalesce(rationale, '') = ''")
    print(f"      the store carries {n} open recommendation(s) with no counterargument")
    if n:
        return int(n)

    with store.read() as s:
        rows = s.query("SELECT id FROM brain.work_item ORDER BY id LIMIT 1")
    if not rows:                                                        # DENOMINATOR
        print("      NOT MEASURED: 0 work items on this store, so there is nothing to raise a "
              "recommendation against.")
        return 0
    store.apply("recommend", subject_type="work_item", subject_id=rows[0]["id"],
                text="Retire the nightly reseed and let each suite build what it needs.",
                rationale="", produced_by="D3-fixture", by="D3-fixture", requires_human=True)
    print(f"      ... so this suite raised one against {rows[0]['id']} through the registered "
          f"`recommend` transition, because the demo seed builds none")
    page.reload(wait_until="networkidle")
    return 1


def verdict(cards: int) -> int:
    """The verdict, and beside it the two counts that produced it. Task 0445.

    A module-level function rather than four lines at the foot of `main` on purpose. `main`
    cannot run without a console on a scratch store and a browser, so a guard living inside it
    is one nobody can fire on demand; here the zero branch is reachable from a probe that
    imports this file and sets the counters, which is the difference between asserting the rule
    and demonstrating it.

    TWO denominators, because this suite has two. `cards` is the number of cards that actually
    carried a verb of position, and it is the one that can silently go to zero: a board that
    rendered no `Approve` and no `Accept work` -- an empty queue, a tier filter that matched
    nothing, a selector this template stopped emitting -- makes `run` compare zero geometries
    while section 4 still files its own two checks. PASS+FAIL would be 2 and the suite would
    print a green over the claim it exists to make. Both have to be nonzero.
    """
    print(f"\n{cards} cards with a verb of position checked")
    if cards == 0 or len(PASS) + len(FAIL) == 0:                        # DENOMINATOR
        print("0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


def main():
    served = _console_guard.refuse_unless_scratch(
        BASE, what="test_argument_is_beside_the_verb.py")
    print(f"console {BASE} is pid {served['pid']} on {served['db']!r}\n")
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROME,
                               args=["--no-sandbox", "--hide-scrollbars"])
        ctx = b.new_context(viewport=VIEWPORT)
        page = ctx.new_page()
        total = 0
        for tier in ("decide", "judge", "shape"):
            print(f"--- {tier}")
            total += run(page, tier)

        # 4. THE ARGUMENT ITSELF, not a placeholder. `0410`'s data reached the card and the
        #    template did not print it, so a suite that only counted blocks would have passed
        #    the defect. This asserts the stored rationale is the text on screen.
        print("--- the argument itself")
        page.goto(f"{BASE}/queue?tier=shape", wait_until="networkidle")
        # SECTION 4b HAS AN INPUT NOW. See the helper's docstring: the check below it asserted
        # over a population the runner's own seed guarantees is empty, and was red for that
        # reason on every run this suite has ever had.
        no_counter = a_recommendation_with_no_counterargument(page, served)
        blocks = page.eval_on_selector_all(
            ".beforeact", "els => els.map(e => (e.innerText || '').trim())")
        # `.exp h5` is `text-transform:uppercase`, and `innerText` returns RENDERED text, so
        # the heading arrives here as `AGAINST IT`. Matched case-insensitively rather than
        # against the source string, because what this suite is about is what the operator sees.
        with_counter = [t for t in blocks if "states no counterargument" not in t
                        and "against it" in t.lower()]
        without_counter = [t for t in blocks if "states no counterargument" in t]
        # BOTH DENOMINATORS, ON THE SCREEN, EVERY RUN. Without this line an `any()` over an
        # empty set and an `any()` over a real population print the same word, which is how the
        # check below stayed red for a fixture reason without anybody being able to see it.
        print(f"      {len(blocks)} argument block(s) rendered: {len(with_counter)} carry a "
              f"counterargument, {len(without_counter)} carry the missing-counterargument "
              f"finding")
        check("a recommendation with a rationale prints the rationale on the collapsed card",
              any(len(t) > 120 for t in with_counter),
              f"{len(with_counter)} counterargument blocks, longest "
              f"{max((len(t) for t in with_counter), default=0)} chars")
        if no_counter == 0:                                             # DENOMINATOR
            print("      NOT MEASURED: the board carries no recommendation without a "
                  "counterargument and this suite could not raise one, so there is nothing "
                  "the finding could be rendered for. Not counted as a pass.")
        else:
            check("a recommendation with none renders the finding rather than an empty block",
                  any("states no counterargument" in t for t in blocks),
                  f"{no_counter} counterargument-free recommendation(s) in the store and "
                  f"{len(blocks)} block(s) on screen, none carrying the finding")
        b.close()
    sys.exit(verdict(total))


if __name__ == "__main__":
    main()
