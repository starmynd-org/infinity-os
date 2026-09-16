# The store's secret references

**Do not read a count out of this file. Run `store/bin/secret-preflight.py --db <db>`.** This file
names the references and holds none of their values, which is the whole posture: the repo stores
references, policy and scope; trusted runtime binds values at the point of use; a service that
cannot resolve a required secret fails closed rather than falling back.

The count is a derived quantity and this file used to assert it. It said *"Five references plus one
per listener"* from the day it was written until 2026-08-18, and by then the fixed set had been six
for two days: migration 20 added `operator` to `store/session.py:ROLES` and nobody came back here.
The five got copied into the V7 VPS brief as the credentials the new host would need, and
provisioning exactly those five yields a host where the paging listener **cannot connect at all** —
`dsn()` fails closed for a subscriber with no credential of its own and refuses the shared
`brain_subscriber` login on purpose. Paging was the entire reason for that move. Demonstrated on a
scratch database in `outputs/2026-08-18-D3-0169-secret-count/PROOF.txt`, task 0169.

So the required set is now **derived on every run**, from three rules, by a script that carries no
number of its own:

1. one per entry in `store/session.py:ROLES` — **five** today: owner, producer, subscriber,
   runtime, operator. It was four until migration 20;
2. `brain-postgres-bootstrap-superuser`, found by reading the `secret <ref>` calls in
   `store/bin/*.sh`. **No Python caller opens it** — the container takes it as `POSTGRES_PASSWORD`
   and the backup job as `PGPASSWORD` — so an enumeration that reads only the Python misses it,
   which is one of the two ways the number in this file went wrong;
3. one per row of `brain.subscriber_role` **in the target database**, because that mapping is what
   decides which login is which listener;
4. one per row of `brain.human_role` **in the target database**, `brain_operator` excluded because
   rule 1 already carries it. Added 2026-08-27 with the admin verb group (task 0385), and the
   argument is rule 3's exactly, one identity kind later: the commander's decision of that day
   (row 0386, decision 2) is **one Postgres login per human, ceiling twelve**, so the mapping is
   what decides which login is which human, and a human with a mapping and no credential cannot
   authenticate at all. `store/session.py:dsn('operator', human=<slug>)` refuses to fall back to
   `brain_operator`, on purpose: one human writing as another is the self-service identity
   migration 10 removed. MEASURED before the rule existed: a freshly provisioned
   `brain_human_lanef_test`, mapped in `brain.human_role` on the target database, was reported in
   the "in the backend and NOT required" column, which is the same shape as the V7 undercount
   this file was written about.

Rules 1 and 2 are the fixed set and it is **six**. Rules 3 and 4 are one per live listener and one
per named human, and on `brain` today that is one and none. Seven, and the script re-derives it
rather than repeating this sentence.

A credential in the backend that no rule requires is reported separately and never counted as
satisfying anything: `brain-postgres-subscriber-n8n-bridge` is one today — a login role and a
secret file for a listener that exists nowhere in this tree and holds zero grants on `brain`. That
is why "eight files are in the directory" is also not the answer; on `brain`, seven references
resolve and the eighth file is an orphan.

**Why listeners stopped sharing one credential (migration 10, 2026-08-16).** The RLS policy on
`subscriber_cursor` keyed on `current_setting('brain.subscriber')`, a value the client sends, and
every listener logged in as the one `brain_subscriber` role. So a listener could call `set_config`
and become another listener: measured, one moved another's cursor from 10 to 4242, which silently
skips every event in between. Identity is now `session_user`, mapped through `brain.subscriber_role`
by a table no listener can read or write. **One login per listener is what makes that mapping mean
anything**, so each gets a reference of its own here.

The residual is honest and worth stating: on a `local-attended` host all of these files sit in one
`0700` directory, so an OS process that can read two of them can connect as either listener. The
database no longer takes a client's word about who it is; file permissions are what separate two
listeners running as the same user on one box.

| `secret_ref.id` | Role | What it opens | Exposure |
|---|---|---|---|
| `brain-postgres-role-owner` | `brain_owner` | DDL, the retention sweep, the rollup | migrations and the sweep job only |
| `brain-postgres-role-producer` | `brain_producer` | INSERT on `event`, nothing else | any event producer |
| `brain-postgres-role-subscriber` | `brain_subscriber` | SELECT on `event` and LISTEN, and **no cursor row at all** since migration 10 | the group login; no listener uses it |
| `brain-postgres-subscriber-<slug>` | `brain_sub_<slug>` | SELECT on `event`, LISTEN, **its own** cursor row | one per listener, minted by `store/bin/provision-subscriber.sh` |
| `brain-postgres-role-runtime` | `brain_runtime` | DML on the work, session and queue tables | the app |
| `brain-postgres-role-operator` | `brain_operator` | what `brain_runtime` can do, by membership, **plus** `work_item.actor_type = 'human'` | the operator's own console on the operator's own host; absent by design everywhere else |
| `brain-postgres-human-<slug>` | `brain_human_<slug>` | what `brain_operator` can do, for a **named** human: `actor_type='human'`, `brain.config_setting` and the admin trail | one per named human, minted by `store/bin/provision-human.sh`, capped at twelve mappings by `brain.human_role_ceiling` |
| `brain-postgres-bootstrap-superuser` | `postgres` | everything | applying migrations, and the backup job |

`brain-postgres-role-operator` arrived with migration 20 (2026-08-17) and **is not a second app
role**: `provision-operator.sh` grants it `brain_runtime` by membership with `INHERIT`, so it can do
exactly what the app can do and one thing more, which is establish that a work item is a human's.
A host that has not run `store/bin/provision-operator.sh` resolves nothing here and fails closed —
correct, because on that host nobody is the operator. It was missing from the table above for a day
and that omission is the direct parent of the V7 undercount.

## What `--as-operator` gates on today, measured rather than inferred

`swarm set <id> agent_claimable true --as-operator` is the one CLI route to the operator's login
(`engine/swarm_engine/cli.py`, verb `set`; `store/transitions.py:_login_for` is where the redirect
is decided). **The flag opens the operator's connection; it does not check who is typing it.** What
it gates on is a file read: `resolve_secret("brain-postgres-role-operator")` from the backend below.

So on `local-attended` the sentence migration 26 supports is narrower than the one a reader infers:

> An agent **connecting as `brain_runtime`** cannot promote a held row.

That is true, load-bearing, and tested (`engine/tests/test_actor_gate.py`,
`test_brain_runtime_cannot_promote_the_row`, which is named for the login precisely because the
login is the whole of what it measures). The wider sentence, *an agent cannot promote a held
row*, **is not true on this host**, and the residual is the same one the listeners have: the
backend is a `0700` directory in the operator's home and every fleet terminal runs as that same
Unix user. Measured from a live fleet terminal on 2026-08-19 (`SWARM_AGENT` and
`SWARM_PARENT_TASK` exported, `uid=1000(you)`), against scratch stores
`brain_t2_0222_path` (task 0222) and `brain_t3_0295` (task 0295):

- every file in `~/.brain-postgres-secrets/` came back readable, **including
  `brain-postgres-bootstrap-superuser`**, whose role is `rolsuper=true rolcanlogin=true` on live
  `brain`. Locking the operator credential away from agent processes and leaving that one in place
  would buy nothing;
- `dsn("operator")` from that terminal returned `user=brain_operator` on `127.0.0.1:5432` and
  connected: `session_user = brain_operator`, member of `brain_operator, brain_runtime`;
- four commands (`set workdir`, `set agent_claimable true --as-operator`, then `claim` from a
  second agent) moved one of the operator's own rows into a terminal;
- **and removing the flag does not close it.** The same promotion was done with no `swarm` CLI at
  all: six lines of `psycopg2` on `dsn("operator")` and one `UPDATE brain.work_item SET
  agent_claimable = true`. The flag is a convenience over a credential the process already holds.

### Which rows that actually reaches, measured 2026-08-19 and narrower than first reported

The paragraph above was posted as though it reached every `[OPERATOR]` row. It does not, and the
reason is a **second** migration-26 trigger that the first three passes over this finding did not
mention: `work_item_human_is_never_agent_claimable` **coerces** `agent_claimable` to false on any
row carrying `actor_type = 'human'`, and it is not conditioned on `session_user`, so **no login is
exempt from it** — not `brain_operator`, not raw SQL, not the console. Measured as
`session_user = brain_operator` with no transition in the call path: a single
`UPDATE ... SET agent_claimable = true` on a human-actor row returns `false`; the same statement
on an unclassified held row returns `true`.

Read on live `brain` the same day: **93 held-and-open rows, 14 of them `actor_type = 'human'` and
79 `NULL`.** All fourteen of the rows usually named when this finding is discussed — 0058 a personal
errand, 0059/0060/0069 the client sends, 0061 a credential rotation, 0066 the contract, 0073 an API
key, 0183 the operator decision — are in the fourteen. **None of them was ever one
`--as-operator` away.** What the one-command route reaches is the other 79, whose hold is usually
a forgotten `--for-agents` rather than a decision.

The hole is real and it is **two statements, not one**: nothing refuses `UPDATE ... SET
actor_type = NULL`, and after that the flag rises. `actor_type` is not in `set`'s settable list,
so that route cannot be typed through the CLI at all — it needs SQL. Both halves are pinned by
`engine/tests/test_actor_gate.py::test_a_human_row_resists_every_login_including_raw_sql`.

Anyone quoting a held count should re-pull it rather than forward one: this finding has carried
16, then 29, then 93 within twelve hours, all correct when measured.

The database is doing its job here and no schema change fixes this. What separates the operator's
queue from the fleet on this host is **filesystem permissions**, exactly as it is for two listeners,
and today those permissions separate nothing, because there is one Unix user. Closing it means
either a second Unix user for the fleet or a credential the process cannot read unattended, both of
which also take the credential away from the operator's own unattended surfaces (`web/actions.py`
passes `as_operator=True` on every console write). That trade is the operator's to make: it is
question **q0305** on the bus (task 0295, 2026-08-19). He answered it the same night:
**option D plus A**, with option B (a second Unix user for the fleet) raised to him and
deliberately not taken by the fleet — the packet is `docs/DECISION-fleet-unix-user.md`. So
`agent_claimable` is to be read everywhere as *held from `brain_runtime`*, never as *held from
agents*, and `swarm set --as-operator` now refuses from a fleet terminal as a **deterrent that
says so in its own refusal text**, not as a boundary (`engine/swarm_engine/cli.py`,
`_refuse_as_operator_from_a_fleet_terminal`).

The bootstrap superuser exists because of a mistake worth recording rather than hiding. The first
standup used `POSTGRES_USER=brain_owner`, which made `owner` the initdb **superuser** — and a superuser
bypasses every grant in `0002_roles.sql`, so the entire least-privilege split would have been
decorative while looking correct in review and passing a naive test. The bootstrap superuser is
now `postgres`, separate from all four roles, and `brain_owner` is `NOSUPERUSER`. Verified:

```
$ psql -U brain_owner -tAc "SELECT rolsuper FROM pg_roles WHERE rolname='brain_owner'"
brain_owner rolsuper=false
```

## Backend, for `local-attended`

v1's location class is `local-attended`, so the backend is a `0700` directory in the operator's
home, files `0600`:

```
~/.brain-postgres-secrets/<secret-ref-id>
```

Three properties, each deliberate:

- **Outside every repo.** No clone carries a value, and `git ls-files` cannot list one.
- **Outside the Postgres data directory.** `~/.brain-postgres/data` is what the backup job dumps
  and what a restore reads. A data backup that carried the credentials which open it would turn
  one lost file into a full compromise.
- **Resolved by reference, at the point of use.** `store/session.py:resolve_secret()` takes a
  reference id and returns a value. Callers pass ids. Nothing else in the package sees a value,
  and a failure raises `StoreConfigError` without logging or returning the value.

Moving to `vps-detached-service` later swaps `_secret_backend()` for Google Secret Manager,
per `_system/secret-registry-rules.md`'s house default, and changes no caller: they only ever
held a reference id.

## Where these are NOT registered, and whose job that is

`_system/secret-registry-rules.md` puts durable references at `secrets/<secret-id>.md` in the
**brain repo**, one file per reference. Writing them is a `_system/`-governed edit to a repo this
lane does not own beyond the port registry row, so it is handed off rather than done quietly.

**The coverage there is partial and this file deliberately does not restate its count**, because
restating a number measured in another repo is the failure this whole section is about. Task 0109
carries the live figure and the three names it found unregistered — one of them
`brain-postgres-subscriber-operator-paging`, the same credential the V7 brief dropped. Read that
task, or `git ls-files secrets/` in the brain, rather than this paragraph.

## How "no raw value in the repo" was verified

Not by reading the diff. By searching the repo for the actual generated values:

```bash
# EVERY file in the backend, not a typed list. A typed list is how the operator credential and
# both listener credentials went unchecked for a day: the loop that named five ran clean and
# proved nothing about the other three.
for f in ~/.brain-postgres-secrets/*; do
  ref=$(basename "$f")
  v=$(cat "$f")
  git grep -qF -- "$v" && echo "LEAK: $ref" || echo "clean: $ref"
done
```

Every value came back clean. `0002_roles.sql` takes its four passwords as psql variables
(`:'owner_pw'`), bound at apply time from the backend; the committed file contains the variable
names. The wrapper scripts call `secret()`, which reads the backend and fails closed.
