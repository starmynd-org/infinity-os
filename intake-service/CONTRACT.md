# The intake door: the contract a connector author codes against

**This is the whole interface.** If you are writing a connector, in n8n, in Make, in Zapier, in a
cron script or by hand, everything you need is on this page. You do not need to read the runtime,
you do not need a database login, and you must not import anything from this repo.

**Registered port `3106`**, loopback, `your-brain/tools/port-registry.md`.
Decided 2026-09-01 by the operator: a separate service, not a POST route on the console, because a
second write door on the console would spend the one-write-door property a test currently asserts,
and because a separate service is the shape that survives moving to a VPS
(`d-intake-separate-service`).

---

## The one call

    POST http://127.0.0.1:3106/intake
    Content-Type: application/json
    Authorization: Bearer <token>

### Authentication, and why two headers are accepted

Send **either** of these. They are equivalent and the service checks both:

    Authorization: Bearer <token>
    X-Intake-Key: <token>

**This is not indecision, it is a scar.** In this estate a working credential was declared dead for
seven weeks because a probe sent `Authorization: Bearer` to an API that wanted `x-api-key`. The
secret was fine the whole time; the header was wrong, and the 401 said nothing useful. Accepting
both costs four lines and removes the entire failure mode. A rejected request says so explicitly:

    401  {"error": "unauthenticated",
          "accepted_headers": ["Authorization: Bearer <token>", "X-Intake-Key: <token>"],
          "hint": "the token is the value of INTAKE_TOKEN on the host running this service"}

**A 401 means your token is wrong. A 403 from something in front of this service means a proxy ate
your request and this service never saw it.** On this host that distinction has been load-bearing
more than once.

The service **fails closed**: if `INTAKE_TOKEN` is unset or empty at boot it refuses to start
rather than serving an open door. There is no anonymous mode and no `--insecure` flag.

---

## The canonical payload

Six fields are the vision's own list, and this door asks for nothing beyond them plus a declared
origin. `source`, `timestamp`, `author`, `content` are required; the rest are optional.

| field | type | required | what it is |
|---|---|---|---|
| `source` | string | **yes** | Where this came from, as a stable machine-readable id. Use `channel:account`, e.g. `gmail:service@example.com`, `slack:T0123/C0456`, `tldv:meetings`. It is what per-source cadence and per-source filtering key on, so keep it stable across runs. |
| `timestamp` | RFC 3339 string | **yes** | **When the thing happened, not when you pushed it.** A message written at 09:00 and swept at 14:00 carries `09:00`. Getting this wrong makes the inbox unsortable by time. |
| `author` | string | **yes** | Who produced the content. An email address, a Slack user id, a person's name, or the connector's own name when nothing produced it but the machine. |
| `content` | string | **yes** | The raw body. Markdown or plain text. **Do not summarise it and do not reformat it** (see *the door does not classify* below). |
| `attachments` | array of objects | no | `[{"name": ..., "mime": ..., "url"|"asset_ref": ...}]`. **Bytes do not go through this door.** Put the file where it belongs and pass a reference. **Every item must carry a non-blank `url` or `asset_ref`** and is refused with a `400` naming the index if it carries neither: a descriptor with no pointer is a reference to nothing, and the row would record a filename whose file is unreachable forever. Silent loss is worse than a refusal. |
| `metadata` | object | no | Anything else your connector knows: thread ids, labels, permalinks, participant lists. It is stored verbatim and never interpreted here. |
| `origin` | `"machine"` or `"human"` | no, but **declare it** | Whether a human or a machine produced this. |
| `title` | string | no | The objective's name. If you omit it one is derived; see *naming and dedup*. |
| `idempotency_key` | string | no, but **send it** | Your stable id for this item. See *naming and dedup*. |

### Declare the origin, every time

`origin` is optional in the schema and **you should always send it**, because an undeclared row
**reads as human** and therefore reaches the operator's attention badge. That default is the safe
direction on purpose: a human message wrongly hidden is worse than a machine message wrongly
shown. But it means a chatty machine connector that does not declare itself costs him a glance
every day, forever.

Declare `origin: "machine"` for anything a machine produced about itself: heartbeats, health
reports, scheduled digests. Declare `origin: "human"` for anything a person wrote. If a machine is
carrying a human's words (an email connector, a Slack connector, a transcript), the origin is
**`human`**: the human wrote it and the machine only moved it.

### The door does not classify, and that is a rule not a shortcut

The door lands **one unclassified objective**. It does not decide what the item means, how urgent
it is, which project it belongs to, or what should happen next. **A human sorts it.**

This is `q-who-classifies`, the deepest open question in the product, and the runtime has taken the
conservative side of it deliberately: **a wrong classification is the same defect class as a
fabricated transcription, because it will be believed later.** So a connector that "helpfully"
rewrites a subject line into an action, or drops what it judges to be noise, is not saving anyone
work. It is manufacturing a fact.

**Send what arrived. Say what it is. Never say what it means.**

---

## Naming and dedup, which is where connectors actually go wrong

A connector on a five-minute schedule will push the same item many times. Two independent
mechanisms stop that becoming a flooded inbox, and **you should rely on the first**.

**1. `idempotency_key` — the one you control.** Send a stable id for the item: a Gmail message id,
a Slack `channel+ts`, a tl;dv meeting id. The door remembers keys it has already accepted and
answers a repeat with `200` and `"deduped": true` instead of landing a second objective. Same key,
same answer, forever. **If you send nothing else optional, send this.**

**The key is remembered per `source`, not globally.** The door dedups on the **pair**
`(source, idempotency_key)`, because that is the pair the store's own intake dedup is built on. Two
consequences, and the second one bites: two different connectors may safely use the same key space,
**and a connector that renames its own `source` re-lands every item it has ever pushed.** So treat
`source` as an identifier you have to keep stable, not a label you can tidy up later. If you must
rename one, expect a full re-land and say so before you do it.

**2. The objective name.** Objectives are also unique by name, so two pushes that resolve to the
same `title` collapse to one. This is the safety net, not the design: it means a connector with no
idempotency key still cannot flood the board, but it also means two genuinely different items that
happen to share a title will silently become one.

**The trap that has already cost this repo a day.** The filesystem intake path dedups on the file's
`size:mtime` signature *and* on the objective name, and the heartbeat workflow rewrites its file
every five minutes with a fresh timestamp inside it. So the signature changes on every tick and
**only the name check is actually holding**, while the code comment claims both are. A dedup rule
that is load-bearing and believed to be redundant is the shape of an outage. Do not build the same
thing: make your key stable, and do not put a "generated at" stamp inside the value you dedup on.

---

## What comes back

| status | meaning |
|---|---|
| `201 Created` | Accepted and landed. Body: `{"objective": "<id>", "name": "<name>", "deduped": false, "reason": ...}` |
| `200 OK` | Already seen. Body: `{"objective": "<id>", "name": "<name>", "deduped": true, "reason": ...}`. **Not an error.** Your retry worked; there is one objective, as intended. |
| `400 Bad Request` | The payload is wrong. The body names the exact field and what was wrong with it. **An unknown top-level field is also a 400**, naming the key: a mistyped `idempotency-key` would otherwise be read as "no key sent", and a connector on a five-minute schedule with no key is a flooded inbox rather than a missing field. |
| `401 Unauthorized` | See authentication above. |
| `413 Payload Too Large` | `content` exceeded the limit, **which is counted in BYTES and not in characters**. A four-byte character costs four. Truncate and pass a reference in `attachments`. |
| `503 Service Unavailable` | The store refused or is unreachable. **Also returned on a lost race for a `name`**: if two pushes resolve to the same objective name at the same instant, the loser gets `503` and its retry reads the row back and answers `200`. Retrying is correct there and it is why the race is a `503` rather than a `409`. |

**`reason` says WHICH rule answered**, and it is worth reading rather than skipping. On a `200` it
distinguishes `"already seen"` — your `(source, idempotency_key)` pair was recognised, which is the
dedup you asked for — from `"name already present"`, which means the **title** collided and your key
never came into it. The second is usually a bug in how you derive titles, and it hides a broken key
perfectly: two pushes of different items sharing a title collapse into one and still answer `200`.
**A dedup check that does not assert which rule held is worthless**, and that exact mask was found
in this service's own test suite and fixed before it shipped.

**A refusal is a refusal and nothing is queued here.** If the store is down you get `503` and the
door keeps nothing. That is deliberate: a silent local buffer is how an intake system loses work it
has already told a producer it took. **Retry on `503`.** Your connector is the queue.

### Three capture keys, present only when durable capture is switched on

`capture_id`, `capture_receipt` and `capture_seq` appear on a `201` and a `200` **when, and only
when, the host has durable capture configured**. They are additive: the three keys this contract
already promises are unchanged, and a connector that ignores these three is correct.

| key | meaning |
|---|---|
| `capture_id` | Identity of the captured evidence, derived from the source identity and the content. Stable across retries of the same delivery. |
| `capture_receipt` | The receipt issued for this capture. Proof the evidence was durable **before** this response was sent. |
| `capture_seq` | The journal sequence the row committed at. Present only for a committed row, so it is the durability witness rather than a self-assertion. |

**Their absence is not a failure.** A door with capture off behaves exactly as it did before capture
existed, byte for byte, and that is asserted by a test rather than promised. Do not branch on these
keys being present unless you configured capture yourself.

**What changes when capture IS on, and it is one thing:** a `201` then means the original payload is
in durable custody *as well as* landed as an objective. If capture cannot complete you get `503`
with `retry: true` and **nothing is written at all** — no evidence, no objective, no
acknowledgement — for the same reason the store-down branch keeps nothing: your connector is the
queue. A payload the capture contract cannot represent gets a `400` naming `(capture)`, and
retrying it unchanged will fail identically for ever.

Turning capture on is an operational decision with its own authorisation. It changes what a `201`
means to every connector already pointed at this door, so it is not a code change and no default
enables it.

---

## Health

    GET http://127.0.0.1:3106/health

`200` with a body reporting the service, whether the store answered a read, and whether the
credential resolved. **Like the console's health handler, this one opens a real read transaction
against Brain Postgres**, so a `200` also proves the store is reachable and a down store gives
`500` rather than a cheerful degraded `200`. It reports credential resolution too, because a door
that is up and unauthenticated is worse than a door that is down.

`/health` needs no token. Nothing else is unauthenticated.

---

## A connector, end to end, in one command

    curl -sS -X POST http://127.0.0.1:3106/intake \
      -H 'Content-Type: application/json' \
      -H "Authorization: Bearer $INTAKE_TOKEN" \
      -d '{
        "source": "gmail:service@example.com",
        "timestamp": "2026-09-01T09:14:22Z",
        "author": "someone@example.com",
        "content": "Can we move Thursday to 3pm?",
        "origin": "human",
        "title": "Email: can we move Thursday to 3pm",
        "idempotency_key": "gmail:19a2f0c4b8e1",
        "metadata": {"thread_id": "19a2f0c4b8e1", "labels": ["INBOX"]}
      }'

**Then go and look.** The item is on the board as one unclassified objective waiting for a human.

---

## The last rule, and it is the one this sprint exists to enforce

**Run your connector once, against the real runtime, before you believe it works.**

On 2026-09-01 the intake heartbeat workflow was found to have been committed carrying a JavaScript
syntax error: `today\'s` written as `today\\'s`, where the escaped backslash ended the string. It
could never have run. Nobody knew, because importing it was somebody else's step, and a green
import is not an execution. The forensics are exact: the workflow itself had been succeeding on
schedule since 2026-08-29, but on a *different copy* than the one in git, so the artefact in the
repository had never once been executed while production ran something else.

**The evidence that a connector works is an execution id and its status.** Not a green import, not
a passing unit test, not a commit message.
