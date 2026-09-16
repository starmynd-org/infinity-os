# The intake door

One HTTP door that lands **one unclassified objective** in `brain.objective` per message, so a
connector (n8n, Make, Zapier, cron, `curl`) can push into the brain without a database login and
without importing anything from this repo.

* **`CONTRACT.md` is the specification.** It is what a connector author reads and what this code
  implements. Where the two disagree, the contract is right and the code is the defect.
* **`intake_service/host.py` is the only configuration point.** The bind, the port (`3106`), the
  credential's environment variable and the content limit are asked for there and copied nowhere.
* **The door does not classify.** It lands `state='inbox'` and a human sorts it. A wrong
  classification is the same defect class as a fabricated transcription, because it will be
  believed later.

## Run it

    intake-service/bin/intake-service

**The credential lives in one file and both halves of the system read it from there.** Provision
it once; the door and every connector resolve it the same way, so there is no second copy to drift:

    mkdir -p ~/.intake-service-secrets && chmod 700 ~/.intake-service-secrets
    python3 -c 'import secrets;print(secrets.token_urlsafe(36))' \
      > ~/.intake-service-secrets/intake-token
    chmod 600 ~/.intake-service-secrets/intake-token

The file holds the **raw token and nothing else** -- no `KEY=`, no quoting -- which is the shape
`engine/bin/scratch-db.sh::secret()` already reads the store's own credentials in from
`~/.brain-postgres-secrets`. `INTAKE_TOKEN` in the environment still wins if it is set, so a
deliberate export always beats what is on disk. **With neither, the service refuses to boot.**
There is no anonymous mode and no `--insecure` flag.

Prefer the file to an export for anything scheduled: `INTAKE_TOKEN=... python3 connector.py` puts
the secret in a command line, and `ps` shows command lines to every process on the host.

The launcher prints `host.describe()` to stderr (where it bound, and whether a credential
resolved, never the credential) and then execs `flask run`. As a service:

    sed "s#@REPO@#$HOME/repos/infinity-os#g" \
      intake-service/systemd/brain-intake.service \
      > ~/.config/systemd/user/brain-intake.service
    systemctl --user daemon-reload && systemctl --user enable --now brain-intake

The unit carries **no `EnvironmentFile=`** on purpose: it would have meant one secret in two
places in two formats. See the comment in the unit itself for the argument.

The unit lives here rather than in the repo's `systemd/` because that directory belongs to
another lane. `systemd/install.sh` does the `@REPO@` substitution for the units in its own
directory and does not see this one; the two lines above are that substitution by hand.

    curl -sS http://127.0.0.1:3106/health

`/health` opens a real read transaction against Brain Postgres. **A down store is a 500, never a
cheerful degraded 200**, which is the console's own rule: an absent service is obviously absent,
and a 200 meaning "up but cannot write anything" is a lie a monitor believes.

## Test it

    intake-service/tests/run-all.sh              # or: python3 -m pytest intake-service/tests

**Against a scratch database, never `brain`.** `ENGINE_SCRATCH_DB` defaults to `brain_s1_door`,
this lane's own, and not the shared `brain_scratch` that every other suite in the estate defaults
to: `scratch-db.sh create` opens with a `DROP`, and a sibling commander is usually asserting
against the shared one. `conftest.py` refuses `brain` outright, because no verb deletes an
objective and a run there would leave rows on the operator's board.

    ENGINE_SCRATCH_DB=brain_s1_door engine/bin/scratch-db.sh create   # first time only

**Every check in this suite is a real `assert` that raises.** That is a deliberate choice against
the estate's other convention: 64 of the 81 Python suites in `engine/tests/`, `web/tests/` and
`queue/tests/` use a `check()` helper that counts and prints without raising, which is honest
under `python3 <file>` (how those runners invoke them) and silently reports failures as passes
under pytest. This lane picked the other half of that fork: asserts that raise, graded by pytest.
Do not mix the two styles in this directory.

The suite writes nothing outside its own rows: each run stamps `source_name` with a fresh id and
the session teardown deletes exactly those. Nothing here truncates anything.

## The mapping, which is settled

| payload | column |
|---|---|
| `source` | `source_name` |
| `idempotency_key` | `source_signature` (the pair IS the transition's dedup key) |
| `content` | `body`, below the front matter block |
| `title` | `name` (UNIQUE in the table, and the second dedup key) |
| `origin` | `origin`, written only when declared |
| | `intake_format` is always `text` |

`timestamp`, `author`, `attachments` and `metadata` **have no column**: `brain.objective` has 24
of them and none is an author, a metadata blob, a JSON column, an external id or an occurred-at.
They are written as a YAML front matter block at the top of `body`, which is the mechanism this
door already uses: `swarm_engine.cli._declared_origin` parses `origin:` out of exactly such a
block on the filesystem path, and `tests/test_payload.py` round-trips this module's output back
through that function so the two cannot drift apart.

## Migration 53 is written, proven and unapplied, and this door does not wait for it

`migrations/0053_an_intake_item_carries_who_and_when.sql` adds `author` and `occurred_at` to
`brain.objective`. It is **this lane's own** migration, on the ledger number the wave-1 admiral
allocated, proven on a scratch database with both of its constraints watched refusing and its
proof block watched failing under a planted defect. **It is not applied to the live store**
(`brain` is at ledger 52 with 24 columns, measured 2026-09-01), and applying it is carve-out 3:
the operator's, not a commander's.

Two consequences worth knowing before reading a green from this suite:

* `scratch-db.sh create` applies every file in `migrations/`, so a freshly built scratch database
  has 26 columns and is AHEAD of live. A suite green there does not by itself speak for the store
  the operator's gate run will hit. So the suite was also run against a scratch database with
  those two columns dropped and ledger row 53 left in place, which is the live store's shape:
  the suite passed identically on both shapes. The door writes only through `store.apply("intake", ...)`,
  which names the pre-53 columns, so it lands on either.
* **The door already passes `author` and `occurred_at` to the transition**, which writes each one
  only when its column exists (`store/schema.py::has_column`). So on the live store today both are
  silently skipped and nothing changes; the day 53 is applied they start landing in columns with
  no edit here and no redeploy. That is deliberate: a migration whose columns nothing writes is
  dead weight, and 53's own comments say so.
* They are passed **even though the front matter already carries them**, and that is not
  redundancy for its own sake. Front matter is the wire record, faithful and unindexed; a column
  is the queryable projection. *Who is waiting on me* and *newest first* are the two questions
  anyone actually asks of an inbox, and neither can be answered by grepping a body.

## What the door derives, and why it says so

* **No `idempotency_key`**: `source_signature` becomes `sha256:<hex>` over the canonical payload,
  so a byte-identical repeat still dedups. **Never the clock.** The filesystem path derives
  `size:mtime` over a file the heartbeat rewrites every five minutes, so its signature changes on
  every tick and only the name check is actually holding while a comment claims both do.
* **No `title`**: `name` becomes `<source>-<16 hex of sha256(source + key)>`. Globally unique,
  because the column is unique across the whole table and a collision does not error, it makes a
  message vanish into a 200. **Never derived from the content alone.**

Both are reported back in a `derived` object on the response, because a connector author who
never sees that line will not learn that their retries are deduping on content rather than on a
key they control.

## Two places this is stricter than `CONTRACT.md`

Both are refusals the contract implies and does not spell. Raise them if either is wrong.

1. **An unknown top-level field is a 400** naming the key, rather than being ignored.
   `idempotency-key` with a hyphen is not a missing field, it is a flooded inbox on the next run.
2. **A 200 and a 201 carry a `reason`** (`already seen` or `name already present`) beside the
   three keys the contract promises. It is the difference between "your key worked" and "another
   item happened to share this title", which the contract itself calls a safety net rather than
   the design.

## The last rule

**Run your connector once, against the real runtime, before you believe it works.** The evidence
is an execution id and its status, not a green import, not a passing unit test, not a commit
message. This service was booted on 3106 against a scratch store and driven with `curl` before
this file was written: health 200, a bad token 401, a new item 201 with a derived name, the same
key again 200 `deduped`.
