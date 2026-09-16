# The Attention act door: receipt expectations, frozen before any control exists

Ruling by `ATTENTION-OS-admiral`, 2026-09-10 (UTC 18:4xZ), packet ATT-1b. This file is the
checklist a non-author executes against the act-door candidate. It is published before the
candidate is built, so the pass cannot be shaped to the build. Cite this file by path and SHA.

Law it applies: `web/MUST-NOT-BUILD.md` item 10 at `dc9a99d` (canonical, 862 lines): "Every
resolution leaves a receipt stripe ... Where a real inverse verb exists the stripe carries it ...
Where no inverse exists the stripe says so and says why, in the verb's own terms, rather than
offering an undo that would not undo." And R36 (term-2 handoff, section 3): where an act is
page-local or does not reach a durable store, the page says so in the words of what is missing.

## The facts the expectations are built on (all MEASURED on `174ba99` unless marked)

| Fact | Where |
|---|---|
| The one write door is `POST /<room>/act`; it calls `guard.guard_write`, `rooms.assert_room_can_act`, then `_perform`, then `_push_receipt`, and answers JSON `{ok, verb, receipt, note, celebrate_from, undo, no_undo_reason}` | `web/app.py:1149` to `1210` |
| The global receipt list is in-process memory and `_receipts()` prunes anything older than `RECEIPT_SECONDS = 7.0` as a side effect of reading | `web/app.py:86`, `:102` to `:106` |
| `console.js` binds every `form.act-form`, `preventDefault`s, fetches, and on success shows a nine-second toast then calls `patch()`; `/api/patch/attention` returns `{"regions": {}}`, so nothing on the Attention page is swapped | `web/static/console.js:202`, `:250`; `web/app.py` patch route `else` branch |
| `say()` is inside an IIFE and is not reachable from a page script | `console.js:11`, `:161`, `:441` |
| The act route hands `_perform` the bare `id` from the form; `_find` is `model.find_item`, which matches `i["id"] == item_id` over the whole open queue, not the window | `web/app.py:1994`, `web/model.py:684` |
| An Attention row's `item_id` is `<source_type>:<source_id>`; the queue's id is the bare `source_id` | `web/model.py` `_StoreAttentionPort.queue`, `item_id=key[0] + ":" + key[1]` |
| `KIND_BY_ARM`: `("work_item","Accept work")` is `review`, `("work_item","Mark my task done")` is `human`, `("question",None)` is `question`, `("recommendation",None)` is `recommendation` | `web/model.py:83` |
| `accept_work` returns `undo = {action: "unaccept", id}`; `mark_my_task_done` requires a non-empty summary via `_need` and returns `undo = {action: "undo_done", id}`; `undo_done` dispatches the verb `reopen`; `answer` returns `undo = amend` unless the question is external; `unaccept_work` supplies its own reason when the form has none | `web/actions.py:116`, `:300`, `:366`, `:67`, `:167` |
| The attention verb set at `14f2faa` is `answer, accept work, unaccept work, done`; `reopen` is not in it; `dismiss` exists nowhere | `14f2faa:web/rooms.py:277`; grep `dismiss` in `web/rooms.py` is 0 with positive control `reopen` at 3 rooms |
| After `done` on an operator's own row, the row leaves arm 2 (`NOT agent_claimable AND state IN ('inbox','active')`) and enters arm 1 (`state='done' AND accepted_at IS NULL`, primary verb `Accept work`) | `queue/schema/0017` arms 1 and 2 |
| After `accept work`, the row leaves `brain.queue_open` entirely | arm 1 predicate |
| CSRF tokens are minted per room; a token minted for another room is refused at the door | `web/guard.py:93`, `:109` to `:131` |
| The store credential for `accept work` is the process's human login (`as_operator=True`); a process without it is refused in words | `web/actions.py:116` docstring; `web/app.py` startup check |

Correction to the fact table, 2026-09-11 02:45Z, from the reviewer's findings F2, F3 and F8 on X4
(`artefacts/2026-09-10/ATTENTION-OS-admiral/review/REVIEW-act-door-eb4c495.md`): the `KIND_BY_ARM`
row above reads as if arms 3 and 4 emit a NULL `primary_verb`; they do not. Arm 3 emits
`'Accept default'` and arm 4 `'Approve'` (`queue/schema/0017` lines 109 and 123), and the kind
resolves through the two-step lookup at `web/model.py:787`, whose final default is `review`. And
"a recommendation can never render here" is a data invariant, not structure: nothing couples
`queue_item_option.source_id` to `recommendation_id`, so an option row addressed at one
recommendation and naming another would render the row into the E10 branch. The same mechanism
makes the `question` and `answer` path unreachable on every store measured (admission only through
an option). The original table text stands as what the passes were measured against.

## Ruling that rides with this file: `reopen` joins the attention verb set

`done` has a real inverse in the store, `reopen`, and `undo_done` already runs it. A stripe on a
`done` that says "no undo" while the store holds the inverse is the lie item 10 forbids, stated
the other way round. So the `attention` key of `ROOM_VERBS` gains `reopen`, for the inverse of the
person's own `done`. Consequence, stated so it can be refused rather than discovered: `send_back`
also rides `reopen` (`web/actions.py:228`), so a POST naming `action=send_back` at
`/attention/act` would pass the room gate. The page renders NO send-back control; the per-row
guard `rooms.assert_allowed_on` still reads the row behind the card; and the act-door check pins
the set at exactly five verbs so a sixth is red. `dismiss` stays absent (R36). The set after this
ruling: `answer, accept work, unaccept work, done, reopen`. term-2's guard
`outputs/2026-09-09-IOS-term-2/attention-act-door-check.py` pins `GRANTED` at four; it moves to
five in the same commit as the verb, with this file cited, exactly as term-2 moved it for R36.

## The expectations (E1 to E12). Each is a predicate a non-author can execute.

E1. Stripe before removal, always. After a successful act the page renders one receipt stripe,
carrying the JSON `receipt` text, in the slot the row occupied. The row's element leaves the DOM
only after the stripe element exists. Nothing vanishes before its receipt. (Order is item 10's
actual test.)

E2. No timer on this page's stripe. The stripe stays until the next act on the page or the next
page load, whichever comes first. It is not the nine-second toast and it is not the global seven
second list. The stripe says so in one sentence, in words a person can act on, such as: "This
receipt stays until you leave this page. The record is on the item's own thread." `RECEIPT_SECONDS`
is stated here as the behaviour of the GLOBAL list only: a fresh load of this page more than seven
seconds after an act shows no stripe from that list, and this page does not pretend otherwise.

E3. The inverse, where one exists. Where the JSON carries `undo` and the attention room holds the
inverse verb, the stripe carries a control that posts `undo.action` and `undo.id` to
`/attention/act` with the attention token, and its label is `undo.label`. Concretely: `accept work`
carries Unaccept; `done` carries undo (which runs `reopen`); `answer` carries amend unless the
question is external.

E4. No inverse, in the verb's own terms. Where the JSON carries no `undo`, the stripe reads
"no undo" followed by `no_undo_reason` verbatim. The page never invents a reason and never offers
a control that would not undo.

E5. Where the row went, read back, not assumed. For `done` on the person's own row the stripe
states the row's next state as the store now reports it (it reappears under Accept work as a
finished item awaiting the person's acceptance) and the page does not remove the row silently: it
either re-renders the row under its new kind on the next load or says on the stripe where it now
is. For `accept work` the stripe says the row has left this queue.

E6. Fresh load is the store. Reloading after an act shows the row absent (accept) or under its new
kind (done), and the bar, the denominator line and the filter counts reflect the store. No stripe
is required on a fresh load. If the page renders the global seven-second list on a fresh load it
must label it as expiring; it is not required to render it at all.

E6, amended 2026-09-10 19:10Z by `ATTENTION-OS-admiral` AFTER the X1 pass (T2, `b3e1304`, evidence
`artefacts/2026-09-10/ATTENTION-OS-admiral/evidence/ATT-1e-measurement-b3e1304.md`), which found E6
NOT MET on the `done` half: the store put the row under Accept work (read-only: `state=done`,
`accepted_at` NULL) but the default view is a RANKED WINDOW (7 of 99 on the fixture) and the row
ranked outside it, so the stripe's "reload to see it there" was falsified by the reload. The
original E6 text above stands as what X1 was measured against and its verdict is not rewritten.
From X2 on, E6 reads: reloading after an act shows the row absent (accept) or, for `done`, in the
store under its new kind; the page never promises the row will be visible in a window it does not
control. The stripe for `done` therefore carries a direct link to the row's own inspector
(`/attention/<source_type>:<source_id>`), which exists regardless of the window, and its sentence
says the row is now finished work awaiting acceptance and names that link; it does not say
"reload to see it". A tester proves E6 by following the link after a reload and reading the
inspector's kind, and by confirming the bar's denominator moved as the store did.

E7. One door, zero acts outside the waist. Every control on the page posts to `/attention/act` and
nowhere else. There is no other route, no direct store call in `web/views/**`, no write in a
template. A test asserts the set of form actions on the rendered page is a subset of
`{/attention/act}` and that `web/views/**` imports nothing from `store` for writing.

E8. The room's token, and only the room's. Every form carries `csrf_for("attention")`. A token
minted for `queue` presented at `/attention/act` is refused (403 write-refusal) and the page shows
that refusal text in place, not a spinner.

E9. Every refusal is shown in place. A 400, 403, 501 or 500 answer renders its `error` text next
to the control that posted, and the control is re-enabled. A structural refusal (no operator
credential in the process, verb not built, room refusal) is shown in the words the server sent,
and the page adds nothing that claims the act reached the store.

E10. What the page says it cannot do, per row. A row whose kind has no verb in the attention room
(today: `recommendation`, whose verbs `approve` and `reject` are not held here) renders no control
and one sentence naming what is missing and where the act lives (the Queue room). A row the
per-row guard refuses (for example `done` on a row an agent holds) shows the guard's own sentence
after the press. `dismiss` appears nowhere, as a word or a control.

E11. The person's `done` needs a summary. The `done` control carries a text field for the summary;
the button is disabled until the field meets `web/actions.py` `MIN_REASON`; the refusal for a short
summary is the server's sentence, shown in place (E9), and the client gate exists only so the
common case never round-trips.

E12. The page stays inside 375 pixels with the controls on it. Measured on the LOADED mount
(`ios_mount_scratch` at N100) with `document.documentElement.scrollWidth <= 375` and zero
`a.att-chip` elements whose right edge exceeds the viewport, against the empty-store control
(which reads 0 today and proves nothing on its own). The chip overflow at `130fe86` is this file
set's defect and is fixed in the same candidate; a control that reintroduces sideways scroll is red.

## The measurement block a tester reports (denominators first, SHA and UTC on every line)

    rows rendered                       n of N (bar text verbatim, denominator line verbatim)
    rows with a control                 n of N, by kind
    rows with a "cannot act here" note  n of N, by kind, sentence quoted
    acts attempted                      per verb: request, HTTP status, JSON kind, receipt text
    stripe present before row removal   yes/no per act, with the DOM observation that proves order
    stripe survives 10 seconds          yes/no (E2), and the sentence it carries
    inverse offered                     per act: label, action posted, result
    fresh load after each act           row absent / moved / unchanged; counts before and after
    refusal shown in place              per refusal: the server's text, quoted
    scrollWidth at 375, loaded          n (E12), control on the empty store: n
    forms posting elsewhere             0 of n forms (E7), positive control: n forms found

A tester executes this against the served SHA on 8300 (`BRAIN_PG_DB=ios_mount_scratch`, never
`brain`) in a real browser pane, not from served bytes alone. Nothing here is a clearance: the
reviewer under `INFINITY-CLOSURE-admiral` clears.

## What this file does not decide

Whether objectives reach Attention (D4, Andrew's). The keyboard pass (C2, Andrew's session via
Closure). The `base.html` commit element (R44, until Platform writes it into a file). Any change
to `console.js`, `base.html`, `web/app.py`'s act route or the global receipt list: none is granted
to this lane and none is needed to meet E1 to E12.
