# The console

One shell, five rooms, one write door.

```bash
BRAIN_PG_DB=brain_console ./web/bin/console        # http://127.0.0.1:3103
```

Loopback only. No public exposure, no tunnel, no SSE, no websocket, no cache layer, no PWA.

The store needs D6b's schema as well as `migrations/`: `engine/bin/scratch-db.sh` applies only the
files in `migrations/`, and `queue/schema/0007_queue.sql` (plus 0008 and 0009) live in the queue
lane's own directory. The live `brain` store has all four queue relations; a freshly built scratch
store has none. Without them the Queue room answers **HTTP 500** and logs
`the human queue cannot be read: brain.queue_open ... is NOT applied by engine/bin/scratch-db.sh`
(measured). It does not fall back to an order of its own, which is the point of having one.

```bash
ENGINE_SCRATCH_DB=brain_console ./engine/bin/scratch-db.sh psql -q -f - < queue/schema/0007_queue.sql
```

## The one thing to read first

`rooms.py` is the safety-bearing file. Everything else is rendering.

**Room separation is a server-side verb allowlist, not a UI promise.** A UI promise is a promise
about buttons, and buttons are one template edit or one crafted POST away from being false. The
room a request came from is carried on the request and checked against a frozen set before
`store.apply` is reached.

| Room | May call |
|---|---|
| Queue | `answer` · `recommend accept` · `recommend reject` · `accept work` · `reopen` · `done` · `post` |
| Brief | `answer` · `reopen` |
| Fleet | `answer` · `reopen` · `pause` · `resume` · `msg`, and nothing else, ever |
| Scope | `post` · `intake` · `accept` |
| **Study** | **nothing** |

`GET /api/audit` prints that table plus every registered verb no room can reach.

**Study calls zero verbs**, and that is proven rather than described:
`web/tests/test_allowlist.py:test_study_calls_zero_verbs` enumerates every verb registered
anywhere in the runtime, dispatches each one with `room=study`, and asserts both that it was
refused and that `store.apply` was never entered. `test_the_write_door_itself_refuses_study` does
the same through the real HTTP endpoint. A surface that cannot act cannot bias a decision.

Two guards, not one. The allowlist answers *may this room run this verb*. The per-item guard in
`assert_allowed_on` answers *may it run it on THIS row*, which is the question the `done`
prohibition actually asks: an operator `done` on an agent's item fabricates that agent's report,
and the two cases differ by the row, not by verb name.

**And the second guard reads the row.** Until task 0427 it read `item['actor_type']`, and `item`
is the console CARD, which stamped that key on itself from the arm of `brain.queue_open` the row
arrived on -- so the "second" guard was arm 2 restated in Python and could refuse nothing that
arm admitted. It now reads `brain.work_item` and permits `done` only where the row is **not
`agent_claimable` and no agent holds it**: not `actor_type`, because that column is nullable and
two of the operator's fifteen live rows on 2026-08-18 carried NULL, and migration 26's trigger
makes every `actor_type = 'human'` row non-claimable anyway. The `claimed_by` term is what closes
the residual `queue/schema/0013` named and could not reach from inside a view: a row an agent is
holding whose `agent_claimable` was lowered under it.

## Who ranks and who renders

**Neither the order nor the tier is computed here.** `human_queue.reads.queue()` is the one
classifier and the one ranking: the DAG unblock weighting, the decaying bump, the jump band, the
reversibility floor that keeps an irreversible item out of Decide. `web/model.py:queue_view`
adapts what it returns into cards and adds nothing to it.

This lane shipped with its own tier per row kind and its own `rank()`, because D6b had not landed
when it started. Both are deleted (task 0127). The half kept here is the CARD: which verb a kind
offers, what the button says, what the receipt reads.

Two properties come with D6b's queue and are visible on the screen:

* **The list is a window, the count is the total.** Seven items a tier by default. The tier chip
  shows the total and the list says how many are below the window; a card count rendered as a tier
  count would report a plan as if it were the whole obligation.
* **Nothing leaves the queue silently.** Blocked items, deferred items and anything under the
  window are counted and named under the list. `find_item` reads the whole queue rather than the
  window, so a write is never refused because a rendering limit hid its row.

An item reaches **Decide** only where a producer prepared the context and stated a recommended
option AND the item is reversible, which means the fast lane is empty until producers call
`queue classify`. That is the classifier working: a producer cannot declare its own item cheap.

## Where the state lives

Nowhere here. Every screen reads through `store.read()`, which Postgres has put in `SET
TRANSACTION READ ONLY`, and every write is `rooms.dispatch` → `store.apply` → the one registered
transition. The console holds exactly one piece of its own state: a seven-second in-memory list of
receipt stripes, which describe rows that are already committed.

Deep work is a cookie rather than `localStorage`, because the fast pane is rendered server side: a
deep work the server could not see would still ship the interrupting HTML down the wire and hide
it with CSS. Deep work is absolute, so the cards are **not rendered**, not hidden, and
`test_browser.py` checks the markup is empty rather than checking a style.

## Poll and patch

Every three seconds, `GET /api/patch/<room>` returns each region's HTML, rendered by the same
template that rendered the page. The client swaps the regions that changed and **skips the region
holding the focused input**, so a repaint never takes the keyboard out from under a sentence being
typed. Proven in a real browser, with a real data change forced mid-typing so the test cannot pass
on a page where nothing repainted.

## Images: a pointer, a hash and a host, never the blob

V5 (task 0167). An image attached to a work item, a queue item or a voice note is a row in
`brain.image_attachment` holding the pointer, the host that pointer is absolute ON, the sha256,
the byte count and the MIME. **The bytes stay on disk.** Nothing is copied, nothing is uploaded
and nothing is resized, which is why `undo` can honestly say *pointer removed, file kept*.

**The verdict is measured at render time, not read off the row.** `web/images.py::verify`
re-stats and re-hashes the file on every render, so a file that is gone is **MISSING** (red,
holding the layout slot, and **no `<img>` element at all** — a broken-image glyph is the browser
reporting a fetch, not the console reporting a finding), a file whose bytes moved is **CHANGED**
(amber, both hashes, both exact byte counts, current bytes beneath at 60%), a file that stats and
will not open is **UNREADABLE**, and a pointer absolute on another host is **ANOTHER HOST** —
which is not a missing file but a file this process cannot speak about, and is the whole reason
`pointer_host` is a column. v1 found a path recorded as present that was not on disk; a console
that rendered the stored state would have drawn it as healthy.

Five verbs, because an attach is four state changes and dismissing a failure is a fifth:
`image attach` (the attempt, written BEFORE the file is opened, with a `due_at`, so a killed
attach leaves a `landing` row that `brain.image_attachment_health` calls `stuck` rather than
leaving nothing), `image attached`, `image failed`, `image detached`, `image dismissed`. Queue
room only. A failed attach attests **nothing** — `image_attachment_failed_ck` keeps `pointer`,
`pointer_host`, `sha256` and `bytes` NULL — and stays on the card until it is dismissed, which is
itself a recorded act.

```bash
web/bin/image attach work_item 0059 /mnt/c/Users/you/screens/acme-proof.png --human
web/bin/image verify --all       # re-hashes every attached image; exits non-zero on any finding
```

**The attach control takes a PATH, not an upload, and the drop target is deliberately absent.**
A browser hands JavaScript a `File` and never a path, so a drop could only be honoured by
uploading the bytes and writing them somewhere — a second copy, in a location `DESIGN-SYSTEM.md`
§10 routes to V6. The console is loopback-only on the operator's own machine, so a path is the
honest input. Accepted formats come from the **magic bytes** and not the extension
(png/jpeg/gif/webp); the ceiling is 25 MiB (`CONSOLE_IMAGE_MAX_BYTES`), checked with one `stat`
before anything is hashed.

Needs `migrations/0029_image_attachment.sql`, which is **not applied to live `brain`** — see
`web/docs/APPLY-IMAGES.md`. On a store below it the console renders normally and no image appears
anywhere, with one line on stderr naming the migration; the attach verb fails loudly rather than
looking like it worked.

## What is deliberately not built

The must-not-build list, item by item, is in `MUST-NOT-BUILD.md`. The four most likely to creep
back: progress bars anywhere except the run-the-stack pips, a freeform notes field on a queue item,
unread counts and red badges, and anything that interrupts deep work.

## One figure the runtime does not measure

The leverage ratio: agent hours are real, operator minutes are measured nowhere in migration 1, so
the ratio has one real half and is not rendered as a ratio. It renders as a stated absence rather
than a plausible number. Crosstalk slot 7 has the argument.

The other two on this list are no longer absences. The decaying bump and typed defer had no column
and no verb when this lane started and rendered as disabled controls carrying that reason; D6b
landed migration 0007 at 2026-08-16T16:04Z and both are live, through `queue bump` and
`queue defer`. The disabled rendering is kept as the fallback for a store without the queue tables.

## Tests

```bash
python3 -m web.tests.test_allowlist                             # 12, no browser, no store
QUEUE_SCRATCH_DB=<db> BRAIN_PG_DB=<db> \
  python3 web/tests/test_done_reads_the_row.py                  # 31, needs the queue schema
BRAIN_PG_DB=brain_console python3 -m web.tests.test_browser     # 6, drives chromium over CDP
./web/bin/shots.sh                                              # 23 screenshots, both themes
```

`test_browser.py` reseeds the scratch store first, because it resolves real items and a second run
against the leftovers of the first would fail for the wrong reason. It refuses to run against
`brain`.
