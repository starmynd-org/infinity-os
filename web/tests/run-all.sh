#!/usr/bin/env bash
# Every suite the WEB lane owns. Task 0373, and the reason it exists is that until 2026-08-27
# there was no such file: `web/tests/` held 14 test files, NONE of them registered in any runner,
# and `grep -rn "web/tests" engine/tests/run-all.sh queue/tests/run-all.sh` returned nothing.
# 14 of 14 unregistered. Twelve of those suites and 333 assertions were green and nothing in this
# repo executed them, so no certificate this program has ever issued covered the web layer.
#
# ------------------------------------------------------------------------------------------
# THE HAZARD THIS FILE REMOVES RATHER THAN WORKS AROUND
# ------------------------------------------------------------------------------------------
# Seven of the fourteen drive a BROWSER at a console named by `$BASE`, whose default is
# `http://127.0.0.1:3103`. On the operator's host that port has been served by a console attached
# to the LIVE store since at least 2026-08-23, re-measured 2026-08-27 and unchanged:
#
#     ss -ltnp             ->  127.0.0.1:3103   python3   pid 11458
#     /proc/11458/environ  ->  BRAIN_PG_DB=brain
#
# Those suites click real verbs. `BRAIN_PG_DB` set on the TEST does not reach the console, which
# is a separate long-lived process whose environment was fixed when it was exec'd -- so the
# suites' own live-store guards protect the database the test connects to and truncates, and
# leave the one the browser is actually writing into wide open.
#
# So this runner starts a SECOND console, on a database it builds, on a port nothing else uses,
# and VERIFIES the target by reading the serving process rather than by trusting the port
# (`web/tests/_console_guard.py`). The live console is never stopped, restarted or reconfigured.
# Every suite refuses independently as well: belt and braces, because a runner is a convenience
# and the refusal has to survive somebody running one suite by hand.
#
# ------------------------------------------------------------------------------------------
# NOT RUN IS A VERDICT, AND IT IS COUNTED
# ------------------------------------------------------------------------------------------
# `docs/SUITE-INPUT-RULE.md`. A suite whose input is live state declares NOT RUN by exiting 77
# and saying which input it lacked. This runner prints it, counts it on the banner, and does not
# go red for an INHERENT one -- a browser that is not installed on this host is a different thing
# from a browser that renders the wrong page. What it will not do is let a suite vanish: every
# NOT RUN is named on the banner beside the count that ran.
#
# The remediable half keeps `queue/tests/run-all.sh`'s shape: no chromium on this host means the
# seven browser suites are named with the download command and the run exits 1.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$(dirname "$HERE")")"

# ------------------------------------------------------------------------------ the declared list
#
# Column 2 is what the suite NEEDS, and it is the answer to task 0373's "say which of the 14 need
# a browser, which need the console serving, and which need neither".
#
#   none        a python process and a scratch database. No console, no browser.
#   console     HTTP against a serving console this runner started. No browser.
#   browser     a chromium over CDP, driving that console.
#   ownbrowser  a chromium, and NOTHING FROM THIS RUNNER: no console started for it and no
#              reseed before it. Two fulfilment styles sit under that one property, and the
#              branch below keys on the property and not on the style: a suite may start its
#              OWN console and seed its OWN store, or it may serve every request from Flask's
#              test client through a Playwright route and touch no store at all. Amended
#              2026-09-16, when four suites in the `none` column turned out to launch a
#              chromium: the wording described one style and the code has always keyed on the
#              property, so the wording moved rather than the code.
#   own         the suite builds its own everything and is invoked through its own wrapper.
#
# WHY `ownbrowser` EXISTS, AND IT IS A DEFECT I PUT IN AND TOOK OUT. It was `browser`, so this
# runner reseeded before it. `test_runfeed_browser.seed()` posts ONE task and then calls
# `store.apply("claim", agent=AGENT, lanes=["*"])` -- and after a reseed there are seven other
# claimable items, so the claim took somebody else's and the run row pointed at a task with no
# stream. Result: `the two beats did not render: {'beats': 0, ...}` and 9 of 10 red, in a suite
# that was 10 of 10 green minutes earlier. Nothing about the console had changed.
#
# The suite's `claim` being unpinned is a real fragility and is filed rather than fixed here (it
# is a test that has always assumed an empty store). What the RUNNER must not do is create the
# condition: a suite that builds its own fixture does not want this runner's.
#
SUITES=(
  # Attention's pure and synthetic modules were on disk but missing from this
  # dispatch array. `none` needs no console/browser and does not reseed the store.
  "test_act_refusals.py                   none     known inverse refusal HTTP routing with synthetic contexts"
  "test_attention_native_options.py       none     typed native admission and action guards with connections prohibited"
  "test_attention_ux.py                   none     finding regressions on synthetic Attention scenes"
  "test_attention_views.py                none     Attention view vocabulary and rendering contracts"
  "test_completeness.py                   none     complete partial and uncertain result vocabulary"
  "test_contract_reconciliation.py         none     view vocabulary against the separately pinned contracts repository"
  "test_routine_verbs_proposal.py          none     proposal data against the actual parsed room allowlist"
  "test_routines_and_attention_render.py  none     routine and Attention templates against stub read ports"
  "test_attention_panel.py                none     an Attention row opens its inspector in a read-only right-hand panel"
  "test_the_front_door.py                 none     / lands on the Attention inbox and the Queue keeps /queue"
  "test_attention_filter_words.py         none     the Awaiting acceptance tab is keyed awaiting, and old done links still resolve"
  "test_theme_button_in_header.py         none     the theme control sits in the header row and floats over nothing"
  "test_attention_chip_headlines.py       none     wide Attention rows show chip headlines, and headline plus reason is always the full sentence"
  "test_attention_help_note.py            none     Attention's standing note is one press away, word for word, instead of a box on every visit"
  "test_board_row_follows_its_link.py     none     a /queue/table row follows its own id link, and the board keeps no form and no new control"
  "test_served_commit_footer_removed.py   none     no served-commit footer under every room; the commit stays at /api/health and in a header"
  "test_five_tab_nav.py                   none     the five-tab nav with a row of rooms, the intake badge on Inbox, and the chats and terminal holds kept"
  "test_board_search_and_panel.py         none     /queue/table's GET search narrows the board, and its panel lifts only Accept work and Send back"
  "test_board_opens_as_a_table.py         none     the board's lede, coverage paragraph and callout are one press away, not between the search and the first row"
  "test_first_visit_is_light.py           none     a visitor who has chosen nothing gets light, per the locked guide; a stored choice is still honoured"
  "test_board_search_is_one_row_on_a_phone.py none  at 375 the board search is one row with its label kept, and the queue chips hold the 44px floor"
  "test_board_first_row_is_on_the_first_screen.py ownbrowser  at 375 the board first row is fully visible without scrolling, painted in chromium"
  "test_sprint_does_not_scroll_the_page.py ownbrowser  /sprint's table sits in a scroll box, so the PAGE never scrolls sideways, at zero rows or three"
  "test_no_suite_guesses_a_console.py     ownbrowser  a browser suite refuses to guess which console it drives; the control proves it refuses only when it should"
  "test_the_ground_is_slate.py            ownbrowser  the ten ground roles resolve to the locked Ground B Slate in both schemes, painted in chromium, with the seven state values pinned unmoved"
  # W4-INFINITY-FIX, IX-SG-07. `ownbrowser`: serves every request from Flask's test client through
  # a Playwright route, same fulfilment style as test_the_ground_is_slate.py above, and touches no
  # store. Forces `body.deep` by script, since P14 measured that no reachable route in this
  # fixture ever sets it, and reads the PAINTED background and ink off the shipped
  # `body.deep{background:var(--bg);...}` rule, not only the declared custom properties.
  "test_deep_work_is_manila.py            ownbrowser  deep work's page pair resolves to the locked P2 Manila palette in both schemes, painted through body.deep, with --deep-accent pinned unmoved"
  "test_queue_board_door_tap_floor.py     none     the Queue's door to the whole board meets the 44px tap floor at 390"
  "test_five_tabs_one_row_at_375.py       ownbrowser     at 375 and 390 the five nav tabs share one line, keep the 44px floor and their type, painted in chromium"
  # W5-S7. Same route-fulfilled harness as the suite above, no store. The room row squeezed the
  # nav at 1440 and hid three tabs behind its hidden scrollbar; a planted narrow nav is the control.
  "test_every_tab_is_visible_at_1440.py   ownbrowser     at 1440 on a page with a row of rooms every nav tab is inside the nav's box and the nav does not scroll, painted in chromium"
  "test_shared_receipts.py                none     receipt history current inverse and overlapping publication"
  "test_signals.py                        none     explanations from the item's declared signals"
  "test-runner-reaps-children.sh         none     a suite that dies before its teardown leaves no child, R-WEB-CHILDREN-01"
  "test_scoping.py                        none     what the console may and may not see"
  "test_defer_and_demote_at_the_surface.py none     a defer's wake condition and the tier a demote is FROM"
  "test_done_reads_the_row.py             none     done renders the row and not the brief"
  # ROW 0399, wave 3 lane J, registered by that lane on the commander's instruction rather than
  # appended to crosstalk.md, because the row it closes is the highest-severity finding of the
  # program and a fix whose regression test is not in a runner is a fix nobody will notice
  # breaking. `none`: it drives web/actions.py directly, which is what the write door calls, so
  # it needs no console and no browser. It TRUNCATEs and builds its own fixture, so this runner
  # must not reseed under it.
  "test_done_is_not_a_forgery.py          none     Send back then Mark my task done: refused, and his own task is not"
  "test_leverage_ratio.py                 none     the leverage arithmetic behind the number"
  # ROW 0438's QUALITY CONDITION, registered in the SAME CHANGE that built it, under this
  # runner's own rule that a fix whose regression test is not in a runner is a fix nobody will
  # notice breaking. `none`: it drives web/model.py directly against a scratch store, so it
  # needs no console and no browser. It TRUNCATEs brain.objective and builds its own fixture,
  # so this runner must not reseed under it.
  "test_intake_origin.py                  none     the badge counts what is waiting on HIM, not the runtime's own heartbeat"
  # ROW 0432, HIS ASK 3, and the THIRD of the three checks MUST-NOT-BUILD item 5's overrule
  # names. Registered in the same change that built it. `none`: it drives web/actions.py
  # directly and shells out to the voice CLI, so it needs no console and no browser. It
  # SPEAKS a known sentence through the host synthesiser when one is reachable and falls
  # back to generated silence when it is not, saying which it used.
  "test_the_voice_note.py                 none     the transcript reaches the thread through the note verb, and the capture closes with it"
  # PHASE 3 RULING 4, 2026-08-31. Registered in the same change that built it. `browser`:
  # the claim is about what a PERSON can click to, so it follows <a href> in a real
  # browser rather than parsing templates. It refuses to pass over an empty store: a
  # route whose data is absent is NOT MEASURED and counted, never folded into the pass.
  "test_every_route_is_reachable.py       browser  every human route is reachable by clicking, or declared with the reason nobody links to it"
  "test_option_dispatch.py                none     a drafted option dispatches through the one door"
  "test_because_of_you.py                 own      because-of-you's arithmetic, via test-because-of-you.sh"
  "test_runfeed_live_stream.py            ownbrowser      the runfeed against a stream the LIVE FLEET wrote"
  "test_allowlist.py                      console  every route the console exposes, and the ones it must not"
  "test_images.py                         console  the image pipeline end to end"
  "test_phone_surface.py                  browser  the phone surface's server-rendered half"
  "test_stopwatch_at_the_surface.py       console  the operator's stopwatch, at the surface"
  "test_runfeed_browser.py                ownbrowser  the runfeed in a browser: starts its OWN console AND seeds its own store, so this runner must not reseed under it"
  "test_browser.py                        browser  the five claims only a browser can prove"
  # ROW 0409. Registered here in the same change that fixed it, under this runner's own rule that
  # a fix whose regression test is not in a runner is a fix nobody will notice breaking. `browser`:
  # three of its four checks are about painted pixels and viewport geometry, which is the whole
  # reason the defect survived a green DOM run.
  "test_celebration_is_not_random.py      browser  whose acts celebrate, and whether the receipt for your own is on screen"
  "test_live_session.py                   none     the exchange as the unit, and what a torn tail must never advance"
  "test_phone.py                          browser  the 390px collapse, and deep work proven silent"
  # ROWS 0410, 0416, 0414 AND 0413, wave 2 of the 2026-08-28 operator drive, registered in the
  # same change that fixed them under this runner's own rule. Both are `browser` because both
  # claims are geometric or sequential rather than textual: the first measures that the argument
  # against a decision is IN THE VIEWPORT with the button that ends it (a wave working these rows
  # nearly shipped a fix that rendered at y=1054 on a 1057px viewport), and the second drives the
  # click and reads what the receipt said afterwards. Neither uses the CDP launcher on 9223: they
  # drive playwright directly and guard the console with `_console_guard`, so they do not contend
  # for that port with `test_browser.py`.
  "test_argument_is_beside_the_verb.py    browser  the counterargument and the report caveat, beside the verb that ends the decision"
  "test_the_card_says_where_the_work_went.py browser  Send back names the fleet, and an acceptance has an inverse"
  # ROW 0437, registered in the SAME CHANGE that built the pane, under this runner's own rule and
  # the lesson `swarm helper` learned three hours earlier: a suite whose registration is a second
  # commit is a suite that spends the interval unrun.
  #
  # `none`: it forks real ptys and drives `create_app()` through Flask's test client, so it needs
  # no console and no browser. It writes NO store row -- the surface it tests cannot -- so this
  # runner must not reseed for it and it needs no scratch fixture beyond the one already exported.
  "test_terminal.py                        none     the typable pane: the loopback gate, the closed write door, and a real shell typed into"
  # ROW 0437, AND THESE TWO WERE ON DISK AND DISPATCHED BY NOTHING. Found 2026-08-29 by diffing the
  # names this file declares against `web/tests/test_*.py` on a clean `git archive HEAD`: 22
  # declared, 24 present, 0 declared-but-missing, 2 present-and-undeclared. `test_suite_registration`
  # could not have caught it because it scans `engine/tests/` only, so an unregistered suite in THIS
  # directory is silent by construction.
  #
  # The second of them is not an ordinary omission. `test_terminal_carries_no_write.py` IS the proof
  # behind the one condition attached to MUST-NOT-BUILD item 11's overrule, that no state write
  # crosses the pane: 69 verbs enumerated, 2208 crafted POSTs, 0 entries into `store.apply`. A
  # condition whose proof nothing runs is a condition nobody is checking, which is this repo's own
  # 0465 shape (nothing executes `denominator-lint.py`, which is why five files landed past the
  # ratchet it exists to be).
  "test_terminal_carries_no_write.py       none     the overrule's condition, by enumeration: every registered verb crafted at every pane endpoint, and store.apply never entered"
  "test_terminal_human_path.py             browser  reaching the pane by URL (nav door held by ruling), and typing into it the way a person does"
  # ROW 0431, registered in the SAME CHANGE that landed the grid, for the reason the line above
  # gives: a suite whose registration is a second commit is a suite that spends the interval
  # unrun. It was written on 2026-08-28 and was NOT in this list, so it ran only when somebody
  # invoked it by hand, which is the state this comment exists to stop recurring.
  #
  # `browser`: it drives playwright directly against the console this runner starts and guards it
  # with `_console_guard`, exactly like the two suites above, so it does not contend for the CDP
  # port 9223 that `test_browser.py` holds. It resolves nothing and writes no store row, so the
  # runner needs no reseed for it.
  "test_two_levels_of_expansion.py        browser  one row per item, the inline expansion, the right-side panel, and that an open row survives the poll"
  # ROWS 0432, 0433 AND THE TWO THINGS HIS 7AM SURFACE WAS MISSING, registered in the SAME CHANGE
  # that landed them, under this runner's own rule three entries above: a suite whose
  # registration is a second commit is a suite that spends the interval unrun.
  #
  # `browser`: it drives playwright directly against the console this runner starts and guards it
  # with `_console_guard`, so it does not contend for the CDP port 9223 that `test_browser.py`
  # holds. IT DOES WRITE ONE ROW -- the Ctrl+Z check accepts a real card and then reverses the
  # acceptance with the key, which is the only honest way to prove an undo -- and it puts that
  # check LAST for the same reason `test_celebration_is_not_random.py` orders its own.
  #
  # ONE OF ITS CHECKS NEEDS A FIXTURE THIS RUNNER DOES NOT SEED, AND THE SUITE BUILDS ITS OWN:
  # the number keys need a row with a DRAFTED OPTIONS RAIL, and `web/bin/seed-demo.py` builds
  # none (measured 2026-08-29: 0 rails across all three tiers, and the first full run of this
  # entry went red for exactly that). The suite drafts one through the registered `queue draft
  # options` transition when the board has none, and is a no-op when it has one, so this runner
  # needs no reseed for it and nothing here has to remember the fixture.
  "test_the_keyboard_and_the_phone.py     browser  space, arrows, number keys, Ctrl+Z and V; deep work without the tier counters; 390px without a sideways scroll; and no page served as a stored copy"
  # ROW 0434, HIS ASK 4, registered in the SAME CHANGE that landed it, under this runner's own
  # rule three entries above: a suite whose registration is a second commit is a suite that spends
  # the interval unrun.
  #
  # `browser`: every claim in it is about what is on a screen -- which rows are rendered after a
  # dropdown moves, where the caret goes when a key is pressed, and whether a control exists at
  # all at 390px. It drives playwright directly against the console this runner starts and guards
  # it with `_console_guard`, so it does not contend for the CDP port 9223 that `test_browser.py`
  # holds. IT WRITES NO STORE ROW -- a filter is a read, no verb is dispatched anywhere in it --
  # so this runner needs no reseed for it and no fixture beyond the one already exported.
  #
  # ONE OF ITS BRANCHES IS NOT DRIVEN BY THIS RUNNER AND THE SUITE SAYS SO RATHER THAN PASSING.
  # `brain.work_item.project` arrives with migration 44, and this runner builds its store at the
  # full ledger, so the column-absent path -- which is the shape of live `brain`, at ledger 42 on
  # 2026-08-29 -- needs a SECOND console on a store below 44. Set `BASE42` to one and the suite
  # drives it; leave it unset and the suite prints NOT MEASURED and names what it lacked. The
  # sentence itself is still asserted against whichever store it is on, so the branch cannot rot
  # unnoticed on a store that is below 44.
  "test_the_order_and_the_filters.py      browser  what the queue is ordered by said on the screen, Notion-style filters over it that never sort, and item 2 kept while a spreadsheet grows a filter bar"
  # ROW 0438, HIS ASK 7, registered in the SAME CHANGE that landed the surface, under this
  # runner's own rule four entries above: a suite whose registration is a second commit is a
  # suite that spends the interval unrun. It is also the only way `DECLARED` and `ON_DISK` stay
  # equal, and an unregistered file in this directory reddens the banner by construction.
  #
  # `browser`: two of its claims are about PAINT. MUST-NOT-BUILD item 7's overrule ships on the
  # condition that the badge is amber and never red, and a class name is not a colour, so the
  # suite reads the computed colour off the painted element in both themes and compares it
  # against `--wait` and `--dmg` as the browser resolved them. The rest is reachability by
  # clicking, a 390px screen and a POST the room must refuse.
  #
  # IT WRITES, AND IT BUILDS ITS OWN FIXTURE, so this runner needs no reseed for it: the seed
  # makes no objectives at all (measured: 0), and the suite lands its own through the registered
  # `intake` transition, accepts one to prove the badge counts the NARROW number, and raises one
  # real recommendation to prove the "no proposals yet" sentence is a read rather than prose. It
  # accepts whatever is already in the inbox first, so the badge's ABSENCE can be watched before
  # its presence. THERE IS NO VERB THAT DELETES AN OBJECTIVE, which is why it refuses any console
  # not on a scratch store through `_console_guard`, exactly like the browser suites above.
  "test_the_intake_surface.py             browser  intake reached by clicking, the amber badge that is never red, the count that is the narrow one, and proposals rendered as a read rather than pretended"
  # S3, 2026-09-01, HIS RULING OF 2026-08-31 (the ranked window keeps its place AND gets a door to
  # the full sortable table). Registered in the SAME CHANGE that built the surface, under this
  # runner's own rule six entries above: a suite whose registration is a second commit is a suite
  # that spends the interval unrun.
  #
  # `browser`: every claim in it is about a rendered screen -- whether five queue tabs are
  # REACHABLE BY CLICKING, whether pressing a column header actually changes the row order,
  # whether a money column orders by magnitude rather than by the text it prints, and whether the
  # page scrolls sideways at 390px. None of that is visible to `curl`.
  #
  # IT WRITES NO STORE ROW. The whole surface is `GET` and carries no verb, which is the point:
  # `MUST-NOT-BUILD` item 2 is kept by construction and check 6 re-measures the RANKED window
  # afterwards to prove the sort did not leak onto it.
  #
  # IT NEEDS A FIXTURE THIS RUNNER DOES NOT SEED AND IT DECLARES NOT RUN RATHER THAN PASSING.
  # `web/bin/seed-demo.py` leaves four of the five operator queues EMPTY and every `impact` unset
  # (measured 2026-09-01: questions 4, review 2, work 2, dependencies 0, decisions 0, money 0), so
  # without `outputs/2026-09-01-sprints/S3/evidence/seed_the_five_queues.sql` the money ordering
  # and the decisions queue are verdicts over an empty set. The suite exits 77 and names what it
  # lacked rather than reporting a vacuous pass.
  "test_the_board_and_the_five_queues.py  browser  the five queues in the GUI, a money column that orders by magnitude, a sort that is pressed rather than merely present, and item 2 re-measured on the ranked window afterwards"
)

# `--list` prints exactly what this script dispatches, one basename per line, and touches nothing.
# The same contract `engine/tests/run-all.sh` has, so a registration check can compare this
# output against the files on disk instead of parsing this file's prose.
# WHAT THIS RUNNER REACHES THROUGH ANOTHER FILE, DECLARED RATHER THAN BURIED IN A `case`.
#
# `test_because_of_you.py` is dispatched by running `bash ./web/tests/test-because-of-you.sh`,
# which is in the `case` at the foot of this file and was in NO list. So the `.sh` sat on disk,
# reachable only through a mapping nothing declared, and the registration check correctly reported
# it as undispatched the first time that check was pointed at this directory (2026-08-31).
#
# It is declared here rather than added to SUITES because adding it there would run it TWICE. The
# question the registration check asks is "does the runner reach this file", and this is the
# runner answering it out loud instead of a reader inferring it from a shell case.
WRAPPED=(
  "test-because-of-you.sh   run by test_because_of_you.py's entry, via the case at the foot"
)

# HOW MANY THIS RUNNER DECLARES, DERIVED ONCE AND CONSUMED BY BOTH READERS OF THAT FACT.
#
# It lives here rather than beside the gap block because THAT is where it went wrong: `--list`
# walked both arrays and the gap block counted only `SUITES`, so this file held two derivations of
# "what do I declare" and they disagreed by one. Only one of them was tested --
# `engine/tests/test_web_suite_registration.py` drives `--list` against the disk, which is why the
# correct half stayed correct while the untested half was wrong twice in opposite directions.
#
# Next to the arrays, a third array breaks BOTH readers at once, visibly, instead of only the one
# nobody checks. That is the whole repair; the arithmetic was a symptom.
DECLARED=$(( ${#SUITES[@]} + ${#WRAPPED[@]} ))

# ------------------------------------------------------- the category vocabulary, CHECKED
#
# AN UNMATCHED CATEGORY MUST BE AN ERROR AND NEVER A DEFAULT, and until 2026-09-16 it was a
# default. The dispatch loop selects behaviour by matching `$need` against `console`, `browser`
# and `ownbrowser`; anything else falls off the end and silently gets `none`'s treatment: no NOT
# RUN guard, no reseed. Two entries were living there. `test-runner-reaps-children.sh` parsed
# `(a`, because its second column was prose. `test_live_session.py` parsed `reader`, a word that
# is not in this file's vocabulary at all -- and `none` happened to be TRUE of it, so it had the
# right behaviour by falling through rather than by matching. That is a passing test with no
# assertion: it cannot start being wrong, it can only be discovered never to have been right.
#
# THE FAILURE MODE OF A TYPO HERE IS NOT A CRASH, WHICH IS WHY A CHECK EARNS ITS PLACE. `consol`
# for `console` is indistinguishable from `reader` to the loop below: that suite loses its NOT RUN
# guard and its reseed, and every suite AFTER it then runs against a store nobody reseeded. The
# damage lands on other people's suites and reads as their flakiness.
#
# THIS IS THE FORM CHECK AND NOT THE TRUTH CHECK. It catches a word that is not in the vocabulary.
# It cannot catch an honest declaration that is wrong about what its suite does -- four suites
# declared `none` and launched a chromium for months, every one of those words spelled correctly.
# Reading the behaviour rather than the declaration is a separate piece of work and is not here;
# do not read this check's silence as that question being answered.
#
# It refuses rather than warns, at startup, before anything runs. A warning on a table nobody
# re-reads is the defect this is written against, one level up.
BAD_NEED=""
for t in "${SUITES[@]}"; do
  read -r _f _need _ <<<"$t"
  case "$_need" in
    none|console|browser|ownbrowser|own) ;;
    *) BAD_NEED="$BAD_NEED
  $_f declares '$_need'" ;;
  esac
done
if [ -n "$BAD_NEED" ]; then
  printf 'REFUSING TO RUN: a suite declares a category this runner does not know.\n'
  printf 'Column 2 must be one of: none console browser ownbrowser own.\n'
  printf 'An unknown word matches no branch below, so the suite would silently get `none`:\n'
  printf 'no NOT RUN guard and no reseed, and every suite after it runs on a store nobody\n'
  printf 'reseeded.%s\n' "$BAD_NEED"
  exit 2
fi
unset BAD_NEED _f _need

if [ "${1:-}" = "--list" ]; then
  for t in "${SUITES[@]}"; do printf '%s\n' "${t%% *}"; done
  for t in "${WRAPPED[@]}"; do printf '%s\n' "${t%% *}"; done
  exit 0
fi

RC=0
INVOKED=0
NOTRUN=0
NOTRUN_NAMES=""
ASSERTIONS_LINE=""

# ------------------------------------------------------------------------------ the two databases
# One for the CONSOLE the browser drives, one for the suites that connect directly. They are the
# same database on purpose here -- the console must serve what the suite seeded -- and it is named
# for this runner so that it is never `brain` and never the shared `brain_scratch`.
WEB_DB="${WEB_SCRATCH_DB:-brain_web_runner_$$}"
case "$WEB_DB" in
  brain)         printf 'run-all: refusing to point the web suites at the live store.\n' >&2; exit 1 ;;
  brain_scratch) printf 'run-all: refusing the shared brain_scratch: other lanes truncate it.\n' >&2; exit 1 ;;
esac
export BRAIN_PG_DB="$WEB_DB"
export ENGINE_SCRATCH_DB="$WEB_DB"
# FOUR OF THESE SUITES IMPORT queue/tests/_scratch_preflight.py, WHICH REFUSES A MISMATCH. Measured
# on the first run of this file: test_done_reads_the_row, test_leverage_ratio, test_option_dispatch
# and test_stopwatch_at_the_surface all stopped with "BRAIN_PG_DB is brain_web_runner_NNN but
# queue-scratch-db.sh would act on brain_queue_scratch". The preflight is right and the runner was
# wrong: exporting one name and not the other is exactly the trap task 0212 wrote it against, and
# it would have TRUNCATEd brain_queue_scratch while asserting on a different database.
export QUEUE_SCRATCH_DB="$WEB_DB"
unset SWARM_PARENT_TASK

CONSOLE_PORT="${WEB_CONSOLE_PORT:-3123}"
export BASE="http://127.0.0.1:${CONSOLE_PORT}"

CHROME_BIN="${CHROME:-$HOME/.cache/ms-playwright/chromium-1223/chrome-linux/chrome}"
HAVE_BROWSER=yes
[ -x "$CHROME_BIN" ] || HAVE_BROWSER=no

# EVERY PORT THESE SUITES NEED, NAMED BEFORE ANYTHING IS DISPATCHED. Task 0382.
#
# `test_runfeed_browser.py` starts its OWN console on 3199 and kills it on the way out. When that
# process is killed mid-run -- which is what happens when somebody stops a runner -- the console
# LEAKS, and the next run dies 20 minutes in with `Address already in use: Port 3199 is in use by
# another program`. Measured on this host: a leaked console on 3199 was still holding the port,
# on a database that had already been dropped.
#
# That is a red about the environment arriving as a crash, which is the thing this whole runner
# is written against. Named here, up front, with what is holding it, so a reader acts on the
# right object in the first ten seconds rather than the twentieth minute.
CDP_PORT="${CDP_PORT:-9223}"
for port in "$CONSOLE_PORT" 3199 "$CDP_PORT"; do
  holder="$(ss -ltnp 2>/dev/null | grep -E "127\.0\.0\.1:$port[[:space:]]" || true)"
  if [ -n "$holder" ]; then
    pid="$(printf '%s' "$holder" | grep -o 'pid=[0-9]*' | cut -d= -f2 | head -1)"
    db="$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | grep '^BRAIN_PG_DB=' | cut -d= -f2)"
    printf 'run-all: PORT %s IS ALREADY IN USE by pid %s (BRAIN_PG_DB=%s).\n' \
      "$port" "$pid" "${db:-unknown}"
    if [ "$port" = "$CONSOLE_PORT" ]; then
      printf 'run-all: that is the port this runner serves on. Refusing to dispatch: the suites\n'
      printf 'would drive somebody else process. Set WEB_CONSOLE_PORT, or stop pid %s.\n' "$pid"
      exit 1
    fi
    printf 'run-all: a suite that wants it will fail with "Address already in use". Usually a\n'
    printf 'leaked console from an interrupted run; stop pid %s and rerun.\n' "$pid"
  fi
done

# ---------------------------------------------------------------- R-WEB-CHILDREN-01
#
# THE RUNNER NAMED THREE PORTS IT NEEDS AND REAPED ONE. `cleanup()` killed `$CONSOLE_PID`, its own
# console, and nothing else. The other two long-lived things are started by the SUITES: a second
# console on 3199 (`test_runfeed_browser.py`) and a chromium on $CDP_PORT (`test_browser.py`). Both
# suites kill what they start IN THEIR OWN TEARDOWN, so nothing leaks on a clean pass -- and neither
# is reaped by anybody if the suite DIES BEFORE REACHING that teardown, which is what a browser or a
# console that cannot do what it expects produces.
#
# This runner already knew: the comment at the port census says a leaked 3199 console was measured
# on this host still holding the port "on a database that had already been dropped". It DETECTED the
# leak at startup and never PREVENTED it at exit -- reporting next run's symptom instead of not
# leaking. Under Terminal 26's seal the consequence is a runner that prints its banner, all 30
# suites invoked, and is then killed at 600s because an orphan outlived it.
#
# BY PROCESS GROUP, AND NEVER BY PORT. The obvious fix is for cleanup() to kill whatever holds 3199
# and $CDP_PORT. A test runner that kills by port is one typo from killing the operator's consoles
# on 3103, 3104 and 3105, and it would do it from a trap, on every exit, including the exits where
# it is already failing. `setsid` puts each suite in its own process group, so a suite's children
# are ITS group; killing the group reaps them by pid without this runner ever needing to know what
# a suite started or what port anything holds.
SUITE_PGIDS=""
ORPHANS=0
ORPHAN_NAMES=""
HAVE_SETSID=yes
command -v setsid >/dev/null 2>&1 || HAVE_SETSID=no

# `run_suite <label> <cmd...>`: run it in its own process group and reap what it leaves behind.
run_suite() {
  local label="$1"; shift
  if [ "$HAVE_SETSID" = no ]; then
    # NO setsid: run it the old way and SAY SO, rather than pretend the reaping happened. An
    # unreapable run is not a clean one, and silence here would be the same false green this
    # repository keeps finding.
    "$@"
    return $?
  fi
  # JOB CONTROL OFF FOR THIS BACKGROUND JOB, and it is load-bearing rather than tidiness.
  # R-WEB-CHILDREN-MONITOR-01, Terminal 25, REV-024, measured:
  #
  #     real run_suite, monitor OFF   rc=3   elapsed=2.02s
  #     real run_suite, monitor ON    rc=0   elapsed=0.00s
  #
  # Under `set -m` the wrapper does not merely lose the suite's exit status -- the elapsed time says
  # IT NEVER WAITS AT ALL, so the next suite would start while this one is still running against one
  # scratch database. Every red suite would read green. It does not happen today only because this
  # is a script and scripts default monitor-off, which is an UNDECLARED condition somebody could
  # change without knowing what it holds up.
  #
  # `setsid -w` IS THE OBVIOUS REPAIR AND IT IS A TRAP. It restores the status by making $pid
  # setsid's WAITING PARENT, whose process group is no longer the suite's -- so `kill -0 -- "-$pid"`
  # below tests the wrong group and the orphan detection this whole function exists to provide goes
  # quietly dead. T25 measured both: setsid -w restores the status and loses the orphan; `set +m`
  # restores the status and KEEPS the orphan visible.
  set +m
  setsid "$@" &
  local pid=$!
  SUITE_PGIDS="$SUITE_PGIDS $pid"
  # REAP THE GROUP BEFORE REAPING THE LEADER. R-WEB-CHILDREN-EXIT-01 priority 2, Terminal 08.
  #
  # This waited first and checked the group afterwards. `wait` REAPS the leader, which frees its PID
  # for reuse -- and the PID is also the PGID. If anything became a group leader with that number in
  # the interval, this runner would signal a group it did not start. T08 could not reproduce it and
  # neither can I; on Linux with a large pid_max it is very unlikely. IT IS NOT ZERO, and what is at
  # the other end is the operator's consoles on 3103/3104/3105.
  #
  # The intent is T08's and the reasoning is sound: A ZOMBIE'S PID CANNOT BE REUSED, so waiting for
  # the leader to become a zombie WITHOUT reaping it, checking and killing while it still holds that
  # number, and reaping last, closes the interval.
  #
  # BUT THAT STATE IS ALMOST NEVER OBSERVABLE, AND MY FIRST VERSION OF THIS COMMENT CLAIMED IT WAS.
  # It said "there is then no interval in which the group id can mean anything else". Terminal 25
  # measured the actual loop, 20 trials per case:
  #
  #     leader exits instantly   pinned as Z = 0/20     already gone = 20/20
  #     leader runs ~1s          pinned as Z = 1/20     already gone = 19/20
  #
  # Bash reaps background children asynchronously and remembers the status for a later `wait`, so
  # thirty-nine times in forty the loop takes the `already gone` exit and the check runs with the
  # PID FREE -- the very interval this block exists to remove, entered silently.
  #
  # SO THE WINDOW IS NARROWED BY TIMING, NOT CLOSED BY CONSTRUCTION. Nothing is broken: detection is
  # unaffected and the residual risk is the same very-unlikely PID reuse neither T08 nor T25 could
  # reproduce. Closing it for real would mean not using `&` plus `wait` at all, which costs more
  # than the window is worth. What was wrong was the SENTENCE, and a reader who believes a
  # guarantee that holds one time in forty is worse off than one who knows the shape of the risk.
  #
  # The leader itself is in the group, so membership alone proves nothing: `pgrep -g` is filtered to
  # exclude it, or every suite would look like it left an orphan.
  local st members
  local PID_WINDOW_OPEN=no
  if [ -r "/proc/$pid/stat" ] && command -v pgrep >/dev/null 2>&1; then
    while :; do
      # AFTER THE LAST ')', not field 3. /proc/<pid>/stat is `pid (comm) state ...` and comm is
      # parenthesised and may contain spaces or parens, so a positional read is wrong for any
      # command whose name has a space in it. Unreachable today, because this branch is almost
      # never taken -- which is exactly why it would be found late. Noted by T25.
      st="$(sed -n 's/.*) //p' "/proc/$pid/stat" 2>/dev/null | cut -d' ' -f1)"
      if [ -z "$st" ]; then
        # ALREADY REAPED BY BASH, so the PID is free from here on and the check below runs in the
        # window. This is the common path, not the exception. It announces the exposure in the same
        # words the no-/proc fallback uses, because two paths that leave the same window open must
        # not differ in whether they mention it -- that difference is how one of them gets trusted.
        PID_WINDOW_OPEN=yes
        break
      fi
      [ "$st" = "Z" ] && break                    # exited, unreaped: the PID is pinned
      sleep 0.05
    done
    members="$(pgrep -g "$pid" 2>/dev/null | grep -v "^${pid}\$" || true)"
    if [ "$PID_WINDOW_OPEN" = yes ] && [ -n "$members" ]; then
      # ON STDOUT, LIKE THE ORPHAN LINE IT ANNOTATES. R-WEB-DISCLOSURE-STREAM-SPLIT-01, CAP14.
      #
      # This went to stderr while the ORPHAN report three lines down goes to stdout, and the
      # dispatcher keeps only stdout -- so the caveat was dropped and the finding it qualifies was
      # kept. A disclosure that travels on a different stream from the claim it weakens is worse
      # than no disclosure: the reader gets the confident half and never learns there was another.
      printf '  NOTE: %s was already reaped when its group was checked, so the pid-reuse window\n' "$label"
      printf '        R-WEB-ZOMBIE-UNREACHABLE-01 describes was open for this reap.\n'
    fi
    if [ -n "$members" ]; then
      ORPHANS=$((ORPHANS + 1)); ORPHAN_NAMES="$ORPHAN_NAMES $label"
      printf '  ORPHAN: %s left a child running. Reaping its process group (%s).\n' "$label" "$pid"
      kill -- "-$pid" 2>/dev/null
      sleep 0.3
      kill -9 -- "-$pid" 2>/dev/null
    fi
    wait "$pid"; local rc=$?
    return $rc
  fi
  # NO /proc OR NO pgrep: fall back to the old order and SAY the window is open, rather than let a
  # weaker guarantee pass for the stronger one. The `already gone` branch above says the same thing
  # for the same reason.
  # Same stream as its ORPHAN line, for the same reason. R-WEB-DISCLOSURE-STREAM-SPLIT-01.
  printf '  NOTE: no /proc or no pgrep here, so the orphan check runs AFTER the leader is reaped\n'
  printf '        and the pid-reuse window R-WEB-CHILDREN-EXIT-01 describes is open.\n'
  # AND WHAT THE CENSUS BELOW CANNOT SEE AT ALL. R-WEB-ORPHAN-ESCAPES-GROUP-01, CAP14.
  #
  # Detection here is GROUP-SCOPED, not a session scan: `kill -0 -- -$pid` and `pgrep -g` both ask
  # "is anything still in THIS process group". A child that calls `setsid` itself becomes the leader
  # of a NEW group and leaves this one, so it survives the reap and is never counted or named. That
  # is a real blind spot and not a bug to fix here -- finding it would mean walking every process on
  # the host and deciding which are ours, which is a different and much less safe instrument.
  wait "$pid"; local rc=$?
  # ANYTHING STILL IN THE GROUP IS AN ORPHAN THE SUITE DID NOT REAP. Counted and named, because a
  # reaper that silently cleans up teaches nobody that a suite is leaking.
  if kill -0 -- "-$pid" 2>/dev/null; then
    ORPHANS=$((ORPHANS + 1)); ORPHAN_NAMES="$ORPHAN_NAMES $label"
    printf '  ORPHAN: %s left a child running. Reaping its process group (%s).\n' "$label" "$pid"
    kill -- "-$pid" 2>/dev/null
    sleep 0.3
    kill -9 -- "-$pid" 2>/dev/null
  fi
  return $rc
}

CONSOLE_PID=""
cleanup() {
  # EVERY SUITE GROUP FIRST, then this runner's own console. If the runner is interrupted mid-suite
  # the group is still live, and that interrupt is exactly the case the port census describes as
  # leaking a console onto 3199.
  local g
  for g in $SUITE_PGIDS; do
    kill -- "-$g" 2>/dev/null
  done
  if [ -n "$CONSOLE_PID" ] && kill -0 "$CONSOLE_PID" 2>/dev/null; then
    kill "$CONSOLE_PID" 2>/dev/null
    wait "$CONSOLE_PID" 2>/dev/null
  fi
  if [ -z "${WEB_SCRATCH_DB:-}" ]; then
    ENGINE_SCRATCH_DB="$WEB_DB" "$ROOT/engine/bin/scratch-db.sh" drop >/dev/null 2>&1
  fi
}
trap cleanup EXIT INT TERM

printf '\n=== web suites: %s files declared, database %s, console %s ===\n' \
  "${#SUITES[@]}" "$WEB_DB" "$BASE"

# THE BOOTSTRAP NAMES ITS OWN FAILURE, AND HERE IS THE ONE IT USED TO MISNAME. Lane D3.
#
# `git archive HEAD | tar -x` into a real filesystem -- the export `docs/SUITE-INPUT-RULE.md` row
# 0372 is written around, and the only honest way to measure what the operator actually has --
# produces a tree in which `engine/bin/scratch-db.sh` is mode 644. Executing it exits 126, and
# this block used to report that as `could not build <db> ... every result below would be about
# the schema`. THE SCHEMA WAS FINE. 0 of 26 suites were dispatched and the message named the
# wrong object, which is what SUITE-INPUT-RULE obligation 1 forbids in as many words: a wrong
# guess is worse than no guess, because someone will act on it.
#
# WHY THE REPOSITORY CANNOT SEE THIS. The working tree is on a 9p/DrvFs mount that reports every
# file as rwxrwxrwx, and `core.fileMode` is false, so a `chmod +x` here never reaches the index.
# Measured 2026-08-30: 282 committed files carry a `#!` and are mode 100644; FOUR files in the
# whole repository are 100755, all of them under systemd/. On this host every runner works and on
# any other filesystem none of them start.
#
# So: SAY IT, print the remedy, and dispatch anyway through `bash`, because 26 verdicts a reader
# can act on are worth more than a refusal -- but the run is NOT GREEN, exactly as a missing
# chromium is not green further down. The bit is REMEDIABLE and doctrine says a remediable
# absence exits non-zero.
BUILDER="$ROOT/engine/bin/scratch-db.sh"
EXECBIT_DEFECT=no
if [ ! -x "$BUILDER" ]; then
  EXECBIT_DEFECT=yes
  cat <<'EXECBIT'

THE BUILDER IS NOT EXECUTABLE. That is a defect in the COMMITTED TREE, not in this host.
  This is what a clean `git archive HEAD` export looks like: git holds this repo's shell scripts
  at 100644, and the working tree only looks executable because it sits on a 9p/DrvFs mount that
  reports every file as rwxrwxrwx. `core.fileMode` is false, so `chmod +x` never reaches git.
  remedy (writes the index directly, so core.fileMode does not matter):
      git ls-files -s | awk '$1=="100644"{print $4}' \
        | while read f; do head -c2 "$f" | grep -q '^#!' && git update-index --chmod=+x "$f"; done
  Dispatching through `bash` so this run still produces verdicts. IT IS NOT GREEN.
EXECBIT
  printf '\n'
fi
if ! bash "$BUILDER" create >/tmp/web-runner-build.$$ 2>&1; then
  printf 'run-all: the builder RAN and could not build %s. Not running the suites: every result\n' "$WEB_DB"
  printf 'below would be about the schema, not about the console.\n'
  sed -n '1,20p' /tmp/web-runner-build.$$
  rm -f /tmp/web-runner-build.$$
  exit 1
fi
rm -f /tmp/web-runner-build.$$
# The console renders a store. An EMPTY store makes a layout suite read as a layout defect, which
# is exactly the defect task 0375 filed against test_phone.py: "the strip is empty with deep work
# OFF; nothing to suppress" is a zero denominator wearing a failure's clothes.
if ! python3 "$ROOT/web/bin/seed-demo.py" >/tmp/web-runner-seed.$$ 2>&1; then
  printf 'run-all: seeding %s failed. Not running: an unseeded console makes every layout\n' "$WEB_DB"
  printf 'assertion below a statement about the store.\n'
  sed -n '1,20p' /tmp/web-runner-seed.$$
  rm -f /tmp/web-runner-seed.$$
  exit 1
fi
printf 'seeded: %s\n' "$(tail -1 /tmp/web-runner-seed.$$)"
rm -f /tmp/web-runner-seed.$$

# ------------------------------------------------------------------------------ the second console
FLASK_APP=web.app:app CONSOLE_BIND=127.0.0.1 CONSOLE_PORT="$CONSOLE_PORT" CONSOLE_ORIGIN="$BASE" \
  python3 -m flask run --host 127.0.0.1 --port "$CONSOLE_PORT" >/tmp/web-runner-console.$$ 2>&1 &
CONSOLE_PID=$!
CONSOLE_UP=no
for _ in $(seq 1 60); do
  sleep 0.25
  if curl -sf "$BASE/api/health" >/dev/null 2>&1; then CONSOLE_UP=yes; break; fi
done

# VERIFY THE TARGET BY READING THE SERVING PROCESS, NOT BY TRUSTING THE PORT. This is the same
# check `_console_guard` makes from inside each suite, made once here so the runner refuses before
# it dispatches anything rather than fourteen times afterwards.
if [ "$CONSOLE_UP" = yes ]; then
  SERVED_DB="$(python3 -c "
import sys; sys.path.insert(0, '$HERE')
import _console_guard as g
w = g.who_serves('$BASE')
print(f\"{w['pid']}\t{w['db']}\")" 2>/dev/null)"
  SERVED_PID="${SERVED_DB%%	*}"
  SERVED_DB="${SERVED_DB##*	}"
  printf 'console: pid %s serving %s (read from /proc/%s/environ)\n' \
    "$SERVED_PID" "$SERVED_DB" "$SERVED_PID"
  if [ "$SERVED_DB" = "brain" ] || [ "$SERVED_DB" = "brain_scratch" ]; then
    printf 'run-all: the console on %s is attached to %s. REFUSING to dispatch a suite that\n' \
      "$BASE" "$SERVED_DB"
    printf 'clicks real verbs at it.\n'
    exit 1
  fi
fi

if [ "$CONSOLE_UP" = no ]; then
  printf 'NO CONSOLE on %s. The seven suites that need one are NOT RUN below.\n' "$BASE"
  sed -n '1,15p' /tmp/web-runner-console.$$ 2>/dev/null
fi
rm -f /tmp/web-runner-console.$$

if [ "$HAVE_BROWSER" = no ]; then
  printf 'NO CHROMIUM at %s. The three browser suites are NOT RUN below.\n' "$CHROME_BIN"
  printf 'Install it:  python3 -m playwright install chromium   (or set $CHROME)\n'
fi

# ------------------------------------------------------------------------------ dispatch
ASSERT_TOTAL=0
for t in "${SUITES[@]}"; do
  # PARSE WITH `read`, NOT WITH PARAMETER EXPANSION, AND HERE IS WHY THE FIRST VERSION WAS WRONG.
  # `rest="${t#* }"` strips ONE space and `${rest## }` strips ONE more, so a column separated by a
  # RUN of spaces left `need` EMPTY -- and an empty `need` matches neither `console` nor `browser`,
  # so the two NOT RUN guards below were dead code on every run and the reseed never fired. It was
  # invisible because both resources happened to be present: a guard nobody has watched fail is
  # not a guard, and this one had never been watched. Caught by `test_phone.py` declaring NOT RUN
  # over a store the runner believed it had reseeded. `read` splits on whitespace RUNS.
  read -r f need _ <<<"$t"
  printf '\n============================================================\n%s\n============================================================\n' "$t"

  if { [ "$need" = browser ] || [ "$need" = ownbrowser ]; } && [ "$HAVE_BROWSER" = no ]; then
    printf '  NOT RUN: this suite drives a chromium over CDP and there is none at\n'
    printf '           %s\n' "$CHROME_BIN"
    printf '           remedy: python3 -m playwright install chromium, or set $CHROME\n'
    NOTRUN=$((NOTRUN + 1)); NOTRUN_NAMES="$NOTRUN_NAMES $f"
    RC=1                       # REMEDIABLE: somebody can install it, so this run is not green
    continue
  fi
  if { [ "$need" = console ] || [ "$need" = browser ]; } && [ "$CONSOLE_UP" = no ]; then
    printf '  NOT RUN: this suite needs a console serving on %s and none came up.\n' "$BASE"
    printf '           remedy: read the flask output above; the port may be taken.\n'
    NOTRUN=$((NOTRUN + 1)); NOTRUN_NAMES="$NOTRUN_NAMES $f"
    RC=1
    continue
  fi

  # RESEED BEFORE EVERY SUITE THAT READS THE CONSOLE, AND HERE IS THE MEASUREMENT THAT FORCED IT.
  #
  # On this runner's first run, `test_phone.py` declared NOT RUN with "0 fast-lane card(s)" over a
  # store that had been seeded at the top of this file. The store was seeded; `test_browser.py`
  # had eaten it. Its own docstring says so: "the last three resolve real items, so anything
  # needing a reviewable item has to run before them". It RESOLVES the Judge tier and then
  # test_phone finds nothing to collapse.
  #
  # The fix is not to reorder the list. Suite order that is load-bearing is a fact nobody can see
  # from the list, and the next person to add a suite will not know. Reseeding costs a couple of
  # seconds and makes every suite independent of what ran before it -- which is also what makes a
  # single suite reproducible by hand, with the same fixture the runner gave it.
  if [ "$need" = console ] || [ "$need" = browser ]; then
    ENGINE_SCRATCH_DB="$WEB_DB" "$ROOT/engine/bin/scratch-db.sh" truncate >/dev/null 2>&1
    # `brain.time_entry` JOINED THIS LIST ON 2026-08-27, row `0390`. `scratch-db.sh truncate` is
    # the engine lane's list and predates `queue/schema/0012`, so a RUNNING operator stopwatch
    # survives it -- and `test_stopwatch_at_the_surface.py`, three suites above `test_browser.py`,
    # ends by design with one running (its last scene inserts an entry born running 90s ago to
    # prove the CARD region does not churn while it ticks). The strip that does tick prints
    # `%.1f min`, a new string every six seconds, so `test_browser.py:idle_poll_swaps_nothing`
    # then watched a page that was not idle and reported the console for it. Measured at the wire:
    # 1 of 9 regions differs across two fetches 13s apart with a timer running, 0 of 9 with none.
    ENGINE_SCRATCH_DB="$WEB_DB" "$ROOT/engine/bin/scratch-db.sh" psql -q -c \
      "TRUNCATE brain.queue_item, brain.queue_bump, brain.queue_defer, brain.queue_calibration, brain.queue_default_event, brain.time_entry CASCADE" \
      >/dev/null 2>&1
    if python3 "$ROOT/web/bin/seed-demo.py" >/tmp/web-runner-reseed.$$ 2>&1; then
      printf '  reseeded: %s\n' "$(tail -1 /tmp/web-runner-reseed.$$)"
    else
      printf '  RESEED FAILED before this suite; its result is about the store, not the console:\n'
      sed -n '1,10p' /tmp/web-runner-reseed.$$
      RC=1
    fi
    rm -f /tmp/web-runner-reseed.$$
  fi
  INVOKED=$((INVOKED + 1))
  case "$f" in
    # `bash`, not `./`: the same 100644 export defect the bootstrap above names would make this
    # one suite exit 126 while the other 25 ran.
    test_because_of_you.py)   run_suite "$f" bash -c 'cd "$1" && bash ./web/tests/test-because-of-you.sh' _ "$ROOT" ;;
    # DISPATCH ON THE EXTENSION, BECAUSE THE DEFAULT ARM ASSUMED EVERY SUITE WAS PYTHON.
    #
    # It ran `python3 -m "web.tests.${f%.py}"`, and `%.py` is a NO-OP on a name ending `.sh`, so a
    # shell suite was handed to Python as a module called `web.tests.test-runner-reaps-children.sh`
    # and died with ModuleNotFoundError. Terminal 26 found it in a sealed run.
    #
    # THE SUITE THAT COULD NOT RUN WAS `test-runner-reaps-children.sh` -- THE ONE R-WEB-CHILDREN-01
    # ADDED TO PROVE THE REAPING WORKS. It was declared, counted as invoked, and had never executed
    # once. `test-because-of-you.sh` escaped only because it has its own arm above.
    #
    # I registered a shell suite in a runner whose default arm was python-only, and then read a
    # banner that said 31 invoked. The proof of the fix was itself unrun, which is the thing this
    # whole file is written against, in the file that is written against it.
    *.sh)                     run_suite "$f" bash -c 'cd "$1" && exec bash "./web/tests/$2"' _ "$ROOT" "$f" ;;
    *)                        run_suite "$f" bash -c 'cd "$1" && exec python3 -m "web.tests.${2%.py}"' _ "$ROOT" "$f" ;;
  esac
  case "$?" in
    0)  ;;
    77) NOTRUN=$((NOTRUN + 1)); NOTRUN_NAMES="$NOTRUN_NAMES $f"
        printf '  ^^ NOT RUN (INHERENT): the suite said which input it lacked. Counted, not red.\n'
        printf '     docs/SUITE-INPUT-RULE.md\n' ;;
    *)  RC=1 ;;
  esac
done

# ------------------------------------------------------------------------------ both denominators
#
# TWO, and they answer different questions. ON_DISK vs declared catches a file dropped into
# web/tests/ that nobody registered -- the defect this whole runner exists because of. INVOKED vs
# declared catches a suite that was declared and did not run on THIS host. A banner carrying only
# one of them was how 14 files sat unrun: `engine/tests/run-all.sh` said ALL SUITES GREEN over 25
# of 30 and the line did not say 25, or 30, or that they differed.
# BOTH EXTENSIONS, BECAUSE THIS RUNNER DISPATCHES BOTH. Counted `test_*.py` only, while SUITES
# carries `test-because-of-you.sh` and, since R-WEB-CHILDREN-01, `test-runner-reaps-children.sh`.
# So DECLARED exceeded ON_DISK and the gap line printed a NEGATIVE count of "files not in the
# list", which cannot be true in its own terms. Terminal 26 read it in a sealed run:
# "31 declared of 30 test_*.py on disk ... REGISTRATION GAP: -1 file(s)".
#
# THE SECOND SUITE IS MINE, ADDED THIS SESSION, so I broke this arithmetic and then read past the
# banner that said so. The count and the thing counted have to be the same set, which is the same
# error as measuring a proxy: `test_*.py` was standing in for "a suite" and stopped being one.
#
# AND THEN I MADE THE SAME MISTAKE AGAIN, IN THE OTHER DIRECTION, IN THIS BLOCK.
# R-WEB-GAP-DENOMINATOR-01, CAP14 REV-033.
#
# The paragraph above says the count and the thing counted have to be the same set. I widened the
# THING COUNTED to both extensions and left the COUNT at `${#SUITES[@]}` -- and this runner has two
# declaration arrays. `WRAPPED` holds `test-because-of-you.sh`, kept out of `SUITES` deliberately
# because a name in both would run it TWICE. So DECLARED was 31 against ON_DISK 32, the gap block
# fired, and THE RUNNER EXITED 1 AT ITS OWN TIP over a file that IS declared and IS dispatched.
#
# THE FIRST VERSION OF THIS DEFECT PRINTED -1 AND THE SECOND PRINTS +1, WHICH IS WORSE. A negative
# count of "files not in the list" is impossible in its own terms, so it reads as broken arithmetic
# and gets investigated. A positive one looks actionable, names no file, and sends the reader
# hunting for an unregistered suite that does not exist. `--list` had the right answer the whole
# time -- it prints both arrays -- so the two halves of this file disagreed and only one was read.
ON_DISK=$(ls "$HERE"/test_*.py "$HERE"/test-*.sh 2>/dev/null | wc -l)
# DECLARED is set once, beside the arrays and above `--list`. Deliberately NOT recomputed here.
if [ "$INVOKED" -eq 0 ] || [ "$ON_DISK" -eq 0 ]; then    # DENOMINATOR
  printf '\nDENOMINATOR: %s suite(s) invoked of %s declared, %s suite files on disk. 0 comparisons made.\n' \
    "$INVOKED" "$DECLARED" "$ON_DISK"
  printf 'A verdict over an empty set is not a pass.\n'
  exit 2
fi
if [ "$HAVE_SETSID" = no ]; then
  printf '\nNOTE: setsid is absent, so suites did NOT run in their own process groups and a child\n'
  printf '      one of them left behind was NOT reaped by this runner. R-WEB-CHILDREN-01.\n'
elif [ "$ORPHANS" -gt 0 ]; then
  printf '\nORPHANS REAPED: %s suite(s) left a child running:%s\n' "$ORPHANS" "$ORPHAN_NAMES"
  printf '  Each is a suite that did not reach its own teardown. The runner cleaned up after it,\n'
  printf '  which is not the same as the suite being correct.\n'
fi
printf '\n%s  (%s of %s declared suites invoked; %s declared of %s suite files on disk; %s NOT RUN)\n' \
  "$([ $RC -eq 0 ] && echo 'ALL WEB SUITES GREEN' || echo 'SOMETHING FAILED')" \
  "$INVOKED" "$DECLARED" "$DECLARED" "$ON_DISK" "$NOTRUN"
if [ "$DECLARED" -ne "$ON_DISK" ]; then
  # BOTH DIRECTIONS, NAMED, NEVER A SIGNED NUMBER. A single subtraction cannot say WHICH side is
  # larger without a minus sign, and a minus sign in "files not in the list" is not a quantity
  # anybody can act on -- it is the arithmetic admitting it assumed one direction. The two cases
  # are different defects: a file nobody registered, versus a name registered for a file that is
  # gone. `compare` in test_suite_registration.py reports the same pair for the same reason.
  if [ "$ON_DISK" -gt "$DECLARED" ]; then
    printf 'REGISTRATION GAP: %s suite file(s) in web/tests/ are NOT in this runner s list.\n' \
      "$((ON_DISK - DECLARED))"
  else
    printf 'REGISTRATION GAP: this runner declares %s name(s) with no suite file on disk.\n' \
      "$((DECLARED - ON_DISK))"
  fi
  printf 'Named by engine/tests/test_web_suite_registration.py, registered in engine/tests/run-all.sh.\n'
  RC=1
fi
if [ "$NOTRUN" -gt 0 ]; then
  printf 'NOT RUN, with the input each one lacked printed above:%s\n' "$NOTRUN_NAMES"
fi
if [ "$EXECBIT_DEFECT" = yes ]; then
  printf 'EXEC BIT: the committed tree is not executable (named at the top of this run, with the\n'
  printf 'remedy). REMEDIABLE, so this run is not green whatever the suites above said.\n'
  RC=1
fi
exit $RC
