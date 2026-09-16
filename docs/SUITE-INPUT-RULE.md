# A suite never reports the environment as a failure

Read this before you write a test, and before you read a red one.

## The rule

**A suite whose input is live state must either supply its own fixture or declare NOT RUN with
the reason. It may never report the environment as a code defect.**

A suite's verdict is a claim about the code. The moment its verdict also depends on state it
does not control -- what the fleet last wrote, what the clock has crossed, whether this tree has
a `.git`, whether a console is serving -- the red it prints is ambiguous, and the reader cannot
tell which of the two things it means. There is no way to read such a red correctly, so it stops
being read at all. That is the whole cost, and it is paid by the one red that was real.

## What made this a rule rather than four patches

Four suites were found this way in one week, all filed as separate rows, all with the same shape:

| Row | Suite | The live state it took as input |
|---|---|---|
| `0372` | `engine/tests/test-infra-failure.sh` | a `.git` directory, which the mandated `git archive` export cannot have |
| `0373` | `web/tests/test_browser.py` and six others | a console process, whose default was the operator's LIVE one |
| `0374` | `web/tests/test_runfeed_live_stream.py` | whichever run stream the fleet most recently wrote |
| `0379` | `engine/tests/test_utilization_gauge.py` | believed to be the live meter; it was actually a hard-coded date that expired |

`0379` is the one to remember. It was filed as an environment dependency and it was not one: the
scene wrote its own fixture into a tempdir and then stamped it with a rate-limit window pinned to
a literal, `2026-08-23T18:00:00Z`, while timestamping the surrounding lines with the real clock.
On 2026-08-23 at 18:00Z the fixture began describing a reading in a window that had already
reset, so the gauge correctly answered UNKNOWN and four assertions went red. **A fixture with a
date in it is live state wearing a fixture's clothes.** Before you classify a red as an
environment dependency, check whether the suite is actually reading the environment at all.

## The three verdicts a suite may reach

    GREEN     the comparisons ran and passed.        Print the denominator.
    RED       the comparisons ran and failed.        This is a claim about the CODE.
    NOT RUN   the comparisons could not run.         Name the missing input and the reason.

NOT RUN is not a fourth colour to hide behind. It carries obligations:

1. **Name the input that was missing and the condition you detected**, not a guess at the cause.
   `test-infra-failure.sh` used to say `Is this a shallow clone?` on a tree that was neither
   shallow nor a clone; it was not a git work tree at all. A wrong guess is worse than no guess,
   because someone will act on it.
2. **State the remedy if there is one.** `queue/tests/run-all.sh` names
   `store/bin/provision-operator.sh --db <name>` for its six credential-gated suites. A reader
   who cannot act on a NOT RUN will learn to skip it.
3. **Count it in the denominator.** A suite that vanished from the run is a suite whose absence
   nobody can see. `N invoked of M on disk, K NOT RUN` on the banner, every run.
4. **Never let NOT RUN be the whole suite.** Any suite that can declare NOT RUN also carries at
   least one hermetic scene that runs in every context. Otherwise NOT RUN is deletion with a
   nicer name, and the check would go years without executing while reading green.

## Which NOT RUN turns the runner red, and which does not

This is the one place the idiom needed extending, and the distinction is about whether anyone can
do anything about it.

**NOT RUN (REMEDIABLE)** -- the input is absent because this host is not set up. Somebody can fix
it, so the runner says so and **exits non-zero**. This is `queue/tests/run-all.sh`'s existing
behaviour for a missing operator credential and it does not change.

**NOT RUN (INHERENT)** -- the input cannot exist in this verification context by construction.
Nobody can fix it, and there is nothing to act on. The runner prints it, counts it, and **does
not go red**. `test-infra-failure.sh` under a `git archive` export is the case: the export rule
and the suite's `git show` are both correct and permanently incompatible, and a gate that fires
on every honest run for a reason unrelated to the thing it gates stops being read.

An INHERENT NOT RUN must name the specific structural condition it detected, and the suite must
still run its other scenes. `test-infra-failure.sh` runs 58 of its 62 assertions under an export;
only scene 0 stands down.

## The exit code

A suite declares NOT RUN by **exiting 77**, the long-standing automake convention for "skipped".
It is unmistakable against this repo's `0 = ok, 1 = failed, 2 = zero denominator`.

    77   NOT RUN (INHERENT).  The runner prints it, counts it, and stays green.

A REMEDIABLE NOT RUN is the runner's own decision -- the runner is what knows whether the host
has the credential -- and keeps its existing shape: print the reason and the remedy, set RC=1.

The suite prints its own NOT RUN line before exiting 77. The runner does not invent one, because
only the suite knows which input was missing.

## What this rule is not

It is not permission to widen a tolerance. Lowering `beats >= 3` to `beats >= 1` so that a
stalled run passes would delete the check; picking a stream that actually has three beats, and
saying how many were scanned and rejected, keeps it. Lowering a WCAG bar from 4.5 to 4.3 is the
worst version of the same move. If the honest answer is that the input is not there, the answer
is NOT RUN, never a smaller question.

## Provenance

Written 2026-08-27 by lane B of the Infinity OS completion program, task `0382`, closing rows
`0372`, `0373`, `0374` and `0379` as one class rather than four patches. The idiom copied rather
than invented: `queue/tests/run-all.sh` lines 21 to 51 had it for six credential-gated suites
since task 0312.
