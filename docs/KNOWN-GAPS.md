# What is missing, as of 2026-08-27

A doc that describes an aspiration is worse than no doc. This is the counterweight to the other
four: everything below is a real gap, one line each, with the bus row that carries it.

**Where the rows live.** The operator declared task bankruptcy on 2026-08-27 and cancelled the whole
board, so every row named here reads `cancelled` in `brain.work_item` today. The findings survive in
`outputs/2026-08-27-commander/BANKRUPTCY-EXPORT-2026-08-27.md`, which holds each row's full brief.
Re-entry into the queue is by hand, deliberately.

Where a line says **re-measured**, this file measured it again rather than copying the row.

## The loop has turned once, on a throwaway store

`brain.recommendation` on live `brain` holds **3 rows, all `state=open`, none decided, none with a
`spawned_work_item`**. The full arc from a disposition to an accepted recommendation to a spawned
work item to a booked receipt has run end to end exactly once, on `brain_demo`, on 2026-08-27. Two of
`PLAN.md`'s five success criteria sit behind that one fact. Row `0381`.

The eighth hop of the lineage walk, the receipt, **does not close and is refused by design**: three
guards refuse it in order, the last being that durability is a property of the repository, not of a
branch name. Before that gate existed, 20 of 26 stored git refs pointed at repositories that no
longer exist. Closing hop 8 in a demo would mean committing a fictional receipt into the real brain
repo.

## The console

| Gap | Row | State |
|---|---|---|
| The **Fleet room renders zero write controls**, and nothing anywhere links to `/agent/<name>` or `/rec/<rid>`. Eight verbs are granted to that room and none is reachable by clicking. **Re-measured:** `fleet.html` contains 0 `<form>` elements and 0 templates carry an `href` to either route | `0400` | open |
| **Nothing is announced to a screen reader**, and the keyboard loses its place after a write. **Re-measured** on `/queue?tier=judge`: 0 `<h1>`, 0 `<main>`, 0 `aria-live`, 0 skip link | `0401` | open |
| **One tier states three numbers on one screen**: the chip says `DECIDE 9`, the run-the-stack CTA says `7 decisions`, the foot of the list says `2 more below the window`. They reconcile and you have to do the arithmetic. The fix is one string | `0403` | **fixed 2026-08-28 under row `0415`**, which is where the operator met it again after this row was cancelled. The CTA states both numbers itself: `Run the stack · 7 of 8 decisions, one at a time`, measured at 1440px and 390px on `brain_demo`. The chip is now confirmed by the button rather than reconciled against it. The foot's `1 more in decide below the window` stays, because it is the list's own not-shown promise on every tier and is now a restatement rather than a third figure |
| **The 3s poll re-ships the whole room.** **Re-measured:** `GET /api/patch/queue?tier=judge` returns **63,880 bytes**, identical length 3 of 3 requests, content identical apart from a per-response CSRF token, with no conditional request. Measured with no cookie jar, so a browser holding a session may see genuinely byte-identical bodies, which is what the row reports | `0405` | open |
| **Six primary verb labels fail WCAG AA at 4.37:1** in the light theme | `0392` | open |
| **Deep work's count never reaches the patch wire**: the deep header is not a patch region, so the number the operator judges by is frozen at page load | `0398` | open |
| **Deep work is absolute on 2 tiers of 3.** **Re-measured:** `class="runbtn"` elements on `/queue`, `deep=0` against `deep=1`, are 2 to 0 on judge, 2 to 0 on shape, and **1 to 1 on decide** | `0404` | open |
| **An idle poll swaps something, and a resolved card can stay on screen.** Narrowed by a later measurement: the idle poll is inert, the trigger is a real store change with focus outside the panel, and only `Send back` leaves its card | `0390` `0391` `0402` | open |
| **Click-to-receipt is O(open items):** 875 ms at 5 open items, 1,910 ms at 40. The suspected cause is a correct property, so the fix is not to page the read | `0396` | open |

## Product

| Gap | Row |
|---|---|
| `web/model.py:828` computes the acted-on rate with the same zero-denominator defect the queue read had, on a product surface | `0387` |
| Which denominator `PLAN.md`'s 30 percent recommendation falsifier means, accepted over raised or accepted over decided. An operator question; a lane may not answer it | `0388` |
| `brain.recommendation.cites_event_id` and `cites_session_id` have one writer and **no reachable caller**: the one surface that can raise a recommendation can only raise one that cites nothing | `0389` |
| Dispatching an option from the operator's **own** card spawns work no agent can claim, and the receipt says only `posted` | `0294` |
| Paperclip's third retirement condition: the daily ritual has a stated end, and the nine personal errands need a home that is not the incumbent board | `0380` |

## Runtime and process

* **`test_terminal.py` is SIGKILLed intermittently inside a full `web/tests` sweep, and the cause
  is UNKNOWN.** Exit 137 on two of three sweeps, 0 on the third, 0 every time it runs alone.
  Measured 2026-09-16: alone it takes 11 to 12s against the sweep's 120s timeout, and a timeout
  exits 124 rather than 137, so the timeout is not it. Run immediately after three chromium suites
  it is exit 0, 57 of 57; chromium processes stay flat at 2 with no leak and 15,220 MB stay
  available, so browser load is not it either, and an earlier claim that load was the cause was
  **withdrawn**: one clean control run cannot isolate an effect that fires two times in three.
  The untested candidate is that `web/terminal.py` forks real ptys at two sites and a reaped child
  would explain the intermittency with no load story at all. **Not the product:** the suite is
  green whenever it completes. Anyone who meets a 137 here should not read it as a failure.
* **`brain-routine-tick.timer` is not installed on this host.** It is in the repo and in
  `systemd/install.sh`'s unit list; `systemctl --user is-enabled brain-routine-tick.timer` answers
  `not-found`. Live `brain` holds 0 routines and 0 routine runs, so nothing is currently missed.
  Remedy: re-run `systemd/install.sh`. Not filed as a row as of 2026-08-27.
* **`brain-transcript-verify.service` fails on every run, and nothing has been verified since
  2026-08-18.** **Re-measured 2026-08-28:** the 06:48:35 EEST run read the corpus and then
  raised `CheckViolation` on `transcript_absence_age_basis_check`, and the sweep exited 2. The
  whole pass is one transaction, so the crash rolls back every `verified_at` it had just
  written: `max(verified_at)` on live `brain` is 2026-08-18 00:20, and 281 pointers have never
  been verified at all. The cause is a migration, not a timer:
  `ingest/schema/0006_absence_parent_age_basis.sql` widens the `age_basis` CHECK from four
  values to seven and has never been applied to live `brain`, which still holds 0004's four, so
  the first pointer dated from its parent session aborts the sweep. Remedy: `psql -d brain -f
  ingest/schema/0006_absence_parent_age_basis.sql`. The tests cannot catch this; they build a
  fresh database from every file in `schema/`, so they only ever see the applied world. Row
  `0423`.
* **Beware the instrument that reads this service.** On 2026-08-28, hours after the crash
  above, `systemctl --user is-failed` answered `inactive` with `Result=success` and
  `ExecMainStatus=0`, because the WSL user manager restarted at 12:34 EEST and a restarted
  manager holds no record of a unit this boot never ran. `list-timers` still showed the 06:48
  run, because that comes from the persistent stamp file rather than from the service. Only
  `journalctl --user -u brain-transcript-verify` survived the restart, and it is the one to
  read. The line that stood here until 2026-08-28 said this service was in `failed` state and
  never repaired. It was written 2026-08-27, quoted in good faith as a live measurement by a
  model review on 2026-08-28, and used to conclude that the operator's top-named missing
  capability was broken. Row `0425`.
* **`brain.human_role` is not readable by the read role on live `brain`**: `permission denied for
  table human_role` through `store.read()`, measured 2026-08-27. `swarm whoami` and `swarm admin
  human list` read it through their own login and work.
* **Auto-accept ships disabled** and stays disabled until the one-week disagreement measurement has
  run. That measurement has not run.
* **43 of 68 registered transitions are reachable from no console room.** Most of that is correct
  (runner plumbing, budget verbs, fabric verbs, agent-only verbs) and some of it is a surface nobody
  has built. `GET /api/audit` prints the list under `registered_and_unreachable`.

## Documentation that is false against the code

Named here because the fix is a doc edit and because a stale contract is worse than a missing one.

* **`web/tests/run-all.sh:49` defines `none` as "a python process and a scratch database. No
  console, no browser", and four suites declared `none` launch a chromium.**
  `test_five_tabs_one_row_at_375.py` has been in that category since it landed and says "painted
  in chromium" in its own description; `test_board_first_row_is_on_the_first_screen.py`,
  `test_sprint_does_not_scroll_the_page.py` and the nav suite make four. **Fixed 2026-09-16** by
  moving them to `ownbrowser` and amending that category's definition. Recorded here because the
  declaration was wrong for months and a reader of the category list had no way to know.
* **The message on commit `74da961` says the exit 137 below is "established". It is not.** The
  claim was made on one control run and withdrawn the same evening on better evidence; the commit
  is pushed and a commit message cannot be edited. Read the row under "Runtime and process"
  instead. Recorded here because this file is where this repo sends a reader who wants to know
  which claims not to trust, and the one that needs distrusting is in a commit.

* **`web/tests/test_contract_reconciliation.py` is BLIND under WSL against a Windows worktree, and
  it reports that blindness as a FAILURE OF YOUR CHANGE.** Measured 2026-09-16, both ways on the
  same tree: `python3 -m unittest` under `wsl.exe` gives `Ran 12, FAILED (failures=1, skipped=11)`,
  and Windows `python -m unittest` on the identical files gives `Ran 12, OK`. The suite reads a
  pinned contract through `git show`; a cut worktree's `.git` is a FILE naming a `C:/` path, WSL
  git cannot resolve it, and the read comes back as the empty string. The assertion then says
  "the tree has moved past the pin", which reads exactly like a real regression. **Eleven of its
  twelve cases silently skip in the same run**, so the suite is one measured case pretending to be
  twelve. Two things a reader needs: this red is environmental, and the way to tell is to re-run
  it with Windows python before believing it. **Not fixed.** The fix is a module-level refusal
  when the git read is empty -- the shape `web/tests/test_phone_surface.py` already uses for an
  unset `BASE` -- not a skip.
* **`web/tests/test_browser.py`'s AA debt register will go red on the Ground B Slate change
  (`a79eb39`) and the failures are the SAME failures.** Its eight known-failing signatures are
  spelled as literal hex pairs (`"#8a8e93 on #f6f6f4 @ span"`), and every ground and ink hex in
  them moves with the palette. Its own rule is that the set going up is a regression and going
  DOWN without lowering the number is also red, so it cannot be re-harvested silently. Measured
  across the same revert on the shell chrome the no-store harness renders: **1 failing text node
  of 126 in both schemes at the fix and at the revert**, same element, 1.99 -> 1.98 light and
  2.68 -> 2.67 dark. That population is NOT this suite's, so it bounds the change rather than
  clearing it. Whoever re-harvests must state whether the count rose.
* **`web/README.md`'s room table is wrong for 2 of 5 rooms.** **Re-measured** against `/api/audit`:
  it lists 7 verbs for the Queue where the allowlist holds 17, and 5 for Fleet, followed by "and
  nothing else, ever", where the allowlist holds 8. Row `0404`.
* **`swarm --help` says "Forty verbs".** The parser carries **51 subcommands**, two of which
  (`admin`, `routine`) are verb groups with subcommands of their own. `engine/VERB-PARITY.md`
  documents the original 40 and is correct about them; the count in the CLI epilog is not.
  Re-measured 2026-08-27. Not filed as a row.
* **`swarm accept-work --help` says `--by` "defaults to $USER".** Measured 2026-08-27: with `--by`
  omitted the acceptance is recorded as `operator`, the human the database says the connection is,
  not `you`, which is `$USER` on this host. The behaviour is right and the help text is stale.
  Not filed as a row.
* Do not trust an "applied clean on live" sentence in a migration header. At least one of them was
  never true. Re-read `brain.schema_migration`.

## Not a gap, and worth saying so

* The **eleven must-not-build prohibitions** are not a backlog. All eleven were reviewed on
  2026-08-27 and kept with zero amendments. Two had their *checks* corrected and one real colour leak
  was repaired.
* Two of the checks in that file are now measurements with instruments attached rather than
  sentences, which is why the numbers in them will move. Read the instrument, not the number.

---

Also here: `README.md` for what this is, `docs/WHY-IT-IS-LIKE-THIS.md` for why it behaves this way,
`docs/OPERATING.md` for running it, `docs/CHANGING-IT.md` for editing it.
