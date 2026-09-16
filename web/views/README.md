# The Attention room, as mounted

`web/views/**` and `web/templates/attention/**` are one seat's declared set (`2026-09-09-IOS-term-2`
on 2026-09-09) and this file is the room's own documentation. `web/README.md` is another file's
owner's and is not edited from here.

## What is here

| file | what |
|---|---|
| `blueprint.py` | The Flask blueprint, `url_prefix="/attention"`. Two GET routes: `/attention/` (the inbox, with `?filter=` and `?sort=`/`?dir=`) and `/attention/<item_id>` (the inspector). No POST. |
| `queue.py` | `QueueItem`, `QueueView`, `Provenance`, `ProvenanceStep`, `TIERS`. What a screen may say about a row, and the refusals a row meets at render. |
| `honesty.py` | `Impact`, `Freshness`, `Completeness`, and the display zone. An unmeasured figure has no value field; a stale value and an absent value are different shapes. |
| `signals.py` | The seven signals as closed vocabularies, refused rather than coerced; the explanation of a rank from declared signals only, with the silence named separately. |
| `../templates/attention/inbox.html`, `inspect.html` | The two pages. Both extend `base.html`, which is not this set's file. |

## How it is mounted

`web/app.py`, one import and one line after `terminal.register(app)`:

    from .views.blueprint import build as build_attention
    ...
    app.register_blueprint(build_attention(model.attention_port()))

`model.attention_port()` (`web/model.py`) is the store-backed `AttentionReadPort`. At the mount
(`3ed0ca4`) it returns an empty `QueueView` and `None` for every item, by construction; the rows are
`web/model.py`'s query path to supply, and `ITEM-CONTRACT-2026-09-09.md` in
`outputs/2026-09-09-IOS-term-2/` says what a row is.

## How it is run, today

A console on a scratch store, never the operator's 3103 to 3105 and never `brain`:

    BRAIN_PG_DB=ios_mount_scratch CONSOLE_HOST=127.0.0.1 CONSOLE_PORT=8300 FLASK_APP=web.app:app
    python3 -m flask run --host 127.0.0.1 --port 8300

from WSL, because Flask and psycopg2 exist only there on this machine. Build the store with
`ENGINE_SCRATCH_DB=ios_mount_scratch engine/bin/scratch-db.sh ensure`. Probe with `curl`; a
Windows port listing cannot see a WSL-forwarded port. Serve from a WSL-native clone if
`/api/health` must report the commit: `git` inside WSL cannot open a Windows-path worktree and
`serving.commit_now` reads "unknown".

## What it does and refuses

    GET  /attention            308 -> /attention/
    GET  /attention/           200, the inbox; ?filter= one of queue, review, restricted, stale, unmeasured, done
    GET  /attention/?sort=     200 for title, kind, freshness, tier; 400 for anything else
    GET  /attention/<id>       200 for a known item, 404 otherwise
    POST /attention/act        403 room-refusal: web/rooms.py ROOM_VERBS has no attention key

The room cannot act. That is structural: there is no POST in the blueprint and no verb in the
allowlist, so `rooms.assert_room_can_act("attention")` refuses before a row is looked up, the
same way it refuses Study. A decision path is four seams and is mapped in
`outputs/2026-09-09-IOS-term-2/DECISION-PATH-2026-09-09.md`.

## What it cannot say yet, in the words of what is missing

- A row from the store. The port is a stub until `web/model.py` binds it.
- Its own commit. `base.html` renders no SHA anywhere.
- An Attention entry in the nav. `base.html`'s ten-tab nav has none; `data-room="attention"` is
  set and every tab is `aria-selected="false"` on this page.
- A decidable row. Every row this surface can render is an announcement with provenance and no
  act, which item 7's surviving condition forbids. The contract refuses that at ingest; this
  surface will change shape once Andrew rules which Attention survives
  (`outputs/2026-09-09-IOS-term-2/TWO-ATTENTIONS-2026-09-09.md`).
- An honest empty state. "Nothing needs you" is `QueueView.empty_note` and is true only because
  the port is a stub. It is left as is until the ruling, because it belongs to a surface that
  may be retired.

## Tiers, and why the number on a row means less than it looks

`TIERS` here is 1 to 4. The store's tiers are `decide`, `judge`, `shape`
(`queue/human_queue/tiers.py`), and the operator's approved shell uses Decide, Review, Create
plus Done. The 1 to 4 axis maps onto neither; see the tier measurement in the two-Attentions
material. Treat the number as this view layer's private ordering key until the binding lands.

## Tests

`web/tests/test_routines_and_attention_render.py` renders both templates against stub ports with
no store. It is one of the test files no runner dispatches (`tools/check-at-head.sh` knows the
dispatched set); run it by name.
