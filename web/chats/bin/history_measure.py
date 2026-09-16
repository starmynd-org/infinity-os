"""LONG-HISTORY FIDELITY, MEASURED AT REAL TRANSCRIPTS. A file, run from the file.

    python web/chats/bin/history_measure.py <transcript.jsonl> [more...]

It prints, per transcript: bytes, records, renderable turns, records NOT rendered, the block-type
census, and the elapsed cost of a full pass and of one window.

**WHY THE ELAPSED FIGURE IS HERE AND NOT IN A COMMENT.** `web/chats/history.py` refuses to keep an
index between requests, because an index kept between requests is the cache layer `MUST-NOT-BUILD`
item 11 forbids with no amendment sought. **That refusal costs a linear pass per window, and a cost
you have not measured is a cost you are guessing at.** So it is measured, on the longest transcripts
actually on this machine, and the number is published whether or not it is flattering.

**IT PRINTS NO TRANSCRIPT CONTENT.** Counts, types, sizes and timings only. These are the operator's
own conversations and a measurement does not need to quote them to be a measurement.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from web.chats import history                                        # noqa: E402


def main() -> int:
    paths = sys.argv[1:]
    if not paths:
        print(__doc__)
        print("error: give at least one transcript path", file=sys.stderr)
        return 1

    print("=" * 78)
    print("INFINITY OS -- LONG-HISTORY FIDELITY. Denominator before every number.")
    print(f"  transcripts given   {len(paths)}")
    print("  content printed     NONE. Counts, types, sizes and timings only.")
    print("=" * 78)

    worst_full = 0.0
    for path in paths:
        print(f"\n{os.path.basename(path)}")
        if not os.path.isfile(path):
            print("  NOT A FILE. Counted in nothing.")
            continue
        t0 = time.perf_counter()
        counts = history.count(path)
        full = time.perf_counter() - t0
        worst_full = max(worst_full, full)
        print(f"  {counts.summary()}")
        print(f"  full pass           {full * 1000:.0f} ms")

        t1 = time.perf_counter()
        win = history.read_window(path, offset=0, limit=50)
        first = time.perf_counter() - t1
        t2 = time.perf_counter()
        last_offset = max(0, counts.turns - 50)
        tail = history.read_window(path, offset=last_offset, limit=50)
        deep = time.perf_counter() - t2
        print(f"  window 0..50        {len(win.turns)} turns in {first * 1000:.0f} ms   "
              f"more_before={win.has_more_before} more_after={win.has_more_after}")
        print(f"  window {last_offset}..+50  {len(tail.turns)} turns in {deep * 1000:.0f} ms   "
              f"more_before={tail.has_more_before} more_after={tail.has_more_after}")

        # The claim a scrollbar depends on, checked rather than assumed: the LAST window must
        # report nothing after it, and the first must report nothing before it.
        #
        # THE NON-EMPTINESS CLAUSE, AND WHAT IT DOES AND DOES NOT BUY -- stated precisely, because
        # I first wrote a confident version of this comment and then checked it and it was wrong.
        #
        # It does NOT close a hole reachable by the offsets THIS script uses. `tail_offset` is
        # `counts.turns - 50`, which is strictly less than `counts.turns`, so an empty tail already
        # makes `has_more_after` TRUE and the check already fails. Planted and confirmed: replacing
        # the window predicate so that a non-zero offset returns nothing scores
        # `[FAIL] ... (50 and 0, expected 50)`.
        #
        # What it DOES close is the case where `read_window` clamps a large offset up to the end,
        # or where a later edit to this script asks for `offset == counts.turns`. There an empty
        # window has nothing after it TRIVIALLY and the check would score PASS over zero rows.
        # `SEAT-COMMON` §7: *a positive anchor only anchors if the thing under test produces it*,
        # and *a banner over zero cases is a zero*. Cheap insurance, honestly labelled.
        expected_tail = min(50, counts.turns)
        ok_first = not win.has_more_before and len(win.turns) == min(50, counts.turns)
        ok_last = (not tail.has_more_after) and len(tail.turns) == expected_tail
        print(f"  [{'PASS' if ok_first and ok_last else 'FAIL'}] first window has nothing before it "
              f"and the last has nothing after it, and BOTH returned turns "
              f"({len(win.turns)} and {len(tail.turns)}, expected {expected_tail})")

        folded = sum(1 for t in win.turns if t.line_count > 12)
        print(f"  foldable in page 1  {folded} of {len(win.turns)} turns exceed 12 lines "
              f"(the frame folds at ~240px; this is the number it folds ON)")

    print("\n" + "=" * 78)
    print(f"WORST FULL PASS OBSERVED: {worst_full * 1000:.0f} ms")
    print("That is the price of refusing an index. `MUST-NOT-BUILD` item 11: a cache layer remains")
    print("forbidden with no amendment sought, and an index kept between requests is one.")
    print("If this number ever stops being acceptable, the answer is a RULING, not a quiet cache.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
