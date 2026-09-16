# `ingest/` — D3: session registration and the transcript index

Owns five verbs, the transcript reader, the goal adjudicator, and the fabric's first producer.

**The one rule this directory exists to keep: `transcript` stores a pointer and a hash, never
the blob.** There is no content column in either schema and there must never be one. Two homes
for transcript content is a split source of truth; one home plus an index is not.

## The verbs

Every surface enters through these. The hook calls them; it does not INSERT. Bulk backfill
calls the same functions in-process (D00 allows that for a read-and-index path) — it skips the
subprocess, not the verb.

```
ingest/bin/ingest session register    --key K [--goal … --workdir … --parent …]
ingest/bin/ingest session end         --key K [--reason R]
ingest/bin/ingest session link-parent --key K --parent P --source path|field|path+field|hook-arg
ingest/bin/ingest transcript index    PATH [PATH…]
ingest/bin/ingest transcript verify   [--limit N | --path P | --id N] [--details]
                                      # runs daily whole-corpus on a timer (0335), see below

ingest/bin/ingest backfill            # bulk read+index of ~/.claude/projects
ingest/bin/ingest backfill --only-missing  # index only what has no row; safe to schedule (0329)
ingest/bin/ingest coverage [--projects-root R]   # every count with its denominator, disk included
ingest/bin/ingest events drain        # replay parked events once D5's `event emit` exists
ingest/bin/ingest init-schema         # create the scratch db + schema, idempotent
```

## Where it writes

Two profiles, one set of transition functions, selected by `BRAIN_PROFILE`:

| Profile | Target | When |
|---|---|---|
| `brain` (**default** since task 0228) | `brain.brain` — **D1's migration 1, unmodified** | now that slot 1 has landed |
| `scratch` | `d3_scratch.ingest` — `ingest/schema/0001_ingest_scratch.sql` | wave 1, before slot 1; now tests only |

```bash
ingest/bin/ingest backfill                      # the brain database
BRAIN_PROFILE=scratch ingest/bin/ingest backfill  # D3's wave-1 database
```

The default was `scratch` and that is what made the installed session hook useless for six
hours: its entry in `~/.claude/settings.json` is a bare command with no `env` block, so every
live session inherited the process default and registered where nobody reads. A default is a
decision this lane makes in its own code, not a variable the operator's settings file has to
remember to carry.

D3's own tables (`event_outbox`, `transcript_orphan`, `transcript_absence`) live in the `ingest`
schema on **both** profiles: on the brain profile the search path is `brain, ingest, public`, so
`session` and `transcript` resolve to D1's migration 1 while D3's producer-side tables resolve
to D3's.

`ingest/ingest/profiles.py` holds the mapping and **enumerates every fact migration 1 cannot
carry** rather than absorbing the losses silently. The four that matter are in crosstalk
slot 4. D3 never edits D1's migrations.

## The five things that are easy to get wrong

**1. `session_id` is not unique across the corpus.** A subagent transcript carries its
**parent's** `sessionId`; its own id is `agentId`. The key is `session_key`: the uuid for a
main session, `'<parent_uuid>:<agent_id>'` for a subagent. Measured over 733/733 subagent
files, path- and field-derived parentage agree.

**2. `ended_at` is not `last_activity_at`.** A transcript's last timestamp is a lower bound on
the end, not an end — a crash and a clean exit look identical. Backfilled sessions get
`last_activity_at` and a NULL `ended_at`. On the `brain` profile, which has no
`last_activity_at`, the fact is **dropped rather than misfiled**.

**3. A null is not a zero.** `cost_usd` is NULL on all 1,130 sessions because no Claude Code
record carries a USD figure anywhere. Token usage is there for 99.5%; deriving cost needs a
price table this lane does not own.

**3a. The token counts carry the denominator they were summed over** (task 0441). The harness
writes one assistant **message** as several **records** and repeats the COMPLETED `message.usage`
block on every one of them, so a `+=` per record counts each message's tokens once per record.
Re-measured 2026-08-29 over the whole corpus root `~/.claude/projects` — population 1496 files,
scanned 1496, gap 0 — 174,060 assistant records carrying usage are **83,779 distinct messages**,
and priced through `budget/price_card.py` the naive sum reads **$27,749.37 against a deduped
$12,508.47, a 2.22x overstatement**. `scan()` now dedups on `message.id`, which was present on
174,060 of 174,060 of those records. `tokens_messages` and `tokens_usage_records` are stored
beside the four sums so a reader can see which denominator was used, and `tokens_source` is
`message-usage-deduped` on rows written since — **NULL on every row written before, which is what
makes the inflated ones identifiable by query rather than by memory**. Re-running `ingest
backfill` restores the rows whose transcripts are still on disk. Nothing here prices anything:
`cost_usd` stays NULL and `budget/price_card.py` remains the single pricing authority.

**4. `transcript_orphans.open` is not the size of the gap** (task 0323). An orphan row
requires the indexing transaction to have *survived* and only its session key to have failed.
When the transaction rolls back instead — six sessions did on 2026-08-16, on
`transcript_session_key_fkey` — the file leaves no transcript row, no session row and no orphan
row, so it is absent from the numerator **and** the denominator of every SQL count here. On
2026-08-17T14:36Z `open` said 2 while 76 sessions on disk had no `session` row. The number that
can see it is `coverage`'s `filesystem` section, whose denominator is `tx.walk` rather than the
store:

```
filesystem.files_on_disk         1256      the walk, workflow journals included (they are
                                           indexed as keyless pointers, so they belong here)
filesystem.indexed               1238 of 1256
filesystem.on_disk_not_indexed     18      the rolled-back shape. NOT the same fact as an orphan
filesystem.indexed_not_on_disk     61      a pointer whose file is gone. `.recorded_absent`
                                           says how many of those `verify` has classified;
                                           `.not_yet_recorded` is the backlog (rule 5 below)
filesystem.pointers_on_other_hosts  0      not dead, just not visible from here
```

Measured 2026-08-17T14:47Z. The walk costs 6.6 ms over 1,256 files and `coverage` is not on the
hook's path, so there is no `--no-walk`: a way to turn the disk-side denominator off is a way to
go back to quoting `open` as the total.

**`on_disk_not_indexed` has a floor, and the floor is not a gap.** The hook indexes a transcript
at `SessionEnd`, so every session open right now is a file with no `transcript` row. Of the 18
above: 7 open sessions, 2 pre-flip files indexed into `d3_scratch` and never into `brain` (a
real gap), 9 subagent transcripts from the hook-install window (task 0325). The report does not
split them, because deciding which is which needs a session key derived from the path, and for a
main session that is a guess — `tx.scan` prefers the file's own `sessionId` and only falls back
to the filename. Read the sample and join it against `session` yourself.

**5. A missing file is not a failed verification** (task 0324). The harness deletes transcripts
on a retention horizon, so most gone pointers are files reaching their designed end of life.
Measured 2026-08-17: all 61 dead pointers belonged to sessions started 2026-07-15..07-17 and to
no later day, and the oldest file left on disk was dated 2026-07-19 — a hard cut 30 days back,
i.e. a sweep, and one that runs again tomorrow. So the count alone is not actionable and
`transcript verify` classifies it:

```
pointers_absent.open_by_classification
  aged-out      60   older than BRAIN_TRANSCRIPT_RETENTION_DAYS (30). Nobody acts on this
  unexplained    0   gone while still INSIDE the horizon. THE ONLY ONE THAT IS AN EVENT
  unknown-age    1   no timestamp survives to place it. Never defaulted to aged-out
pointers_absent.mib_open  45.0
```

Three rules this encodes. **The row is never deleted** — the pointer, the hash and the byte
count are the only surviving record that those transcripts existed, and they are copied onto
the `ingest.transcript_absence` row too so the loss is readable on its own. **`verified_ok` is
NULL, not false**, for a gone file on the brain profile: `verified_at IS NULL` is not yet
verified, `NULL` with a `verified_at` is checked-and-nothing-to-hash, `false` is a real
mismatch. That gives D1's nullable boolean three states with no change to D1's schema.
**`transcript verify` exits 0 on an aged-out sweep** and 1 on `unexplained`, `unknown-age`,
`mismatch` or `unreadable`; a scheduled verify that went red every day for a sweep running on
schedule is a verb whose exit code stops being read.

### A file that GREW is `appended`, not `mismatch` (task 0330)

A sweep can hash a transcript whose session is still writing to it, and that hash is stale the
moment it is taken. Re-measured on this host 2026-08-18, on both profiles: `d3_scratch` held
**eleven** rows whose file no longer reproduced the stored sha256, and **all eleven were pure
growth** — hashing the first recorded `bytes` of each file reproduced the stored sha256 exactly,
and in all eleven the byte at the cut was a newline. `brain` held none, its two having already
been re-indexed. Nine of the eleven were main sessions, so the subagent-only scan that first
found this undercounted by five and a half times; the count moves between measurements because
live sessions keep growing, which is the point.

Those eleven are the evidence the verdict works on real data. Verified before the remedy was
applied: `transcript verify --path` on each returned `appended: 1, mismatch: 0` and exit 0,
eleven times out of eleven. Before this task all eleven would have returned `mismatch` — on the
brain profile, `verified_ok = false`, the corruption bucket. All eleven were then re-indexed
(`ingest transcript index`) and re-verify returns `match`, leaving `stale = 0` on both profiles.
Nothing was lost by re-indexing: every one of the eleven had `verified_at` NULL beforehand.

So verify re-tests the stored hash as a **prefix** of the file on disk. If it holds, every byte
this lane recorded is still there unchanged and the file only got longer: verdict `appended`.
If the prefix fails, or the file shrank, recorded bytes moved and it is `mismatch` as before —
a file that grew *and* had a recorded byte rewritten is still a `mismatch`, because the verdict
turns on the prefix, not on the growth. Both digests come out of one read.

`appended` does not exit 1 and books no absence; the file is there and its history verified. It
is not `match` either — the stored hash no longer covers the file, and `transcript index` is
what makes it cover the file again. On the scratch profile `verify_result` holds the word
verbatim; on the brain profile it takes the same NULL `verified_ok` a gone file takes, and the
two are told apart in `verify_state` by the open `transcript_absence` row that only a gone file
books. D1's schema is untouched; `schema/0005_verify_appended.sql` widens D3's own CHECK.

### Something schedules `verify` now (task 0335)

`systemd/brain-transcript-verify.timer` runs a **whole-corpus** pass daily at 03:20 local, with
`Persistent=true` so a laptop that was off at 03:20 sweeps shortly after its next start instead of
skipping the day. Measured 2026-08-17 at 23:42, immediately before the timer was installed:
`not-yet-verified` was **1,265 of 1,346** and rising — every newly indexed pointer arrives
unverified, so the backlog had a positive arrival rate (~120-155 new pointers a day) and would never
have converged on hand-runs. It is **0** now, and one pass costs 4.25s warm or ~20-25s cold. Daily
is a floor rather than a preference: the harness ages a fresh cohort out every day, and until a pass
reaches them the day's losses sit unclassified in `filesystem.indexed_not_on_disk.not_yet_recorded`,
which is the number that still says how far behind the sweep is.

The full pass also settles the fear that made the schedule look expensive: 1,346 pointers returned
**0 `mismatch`** with 2 `appended`, including an actively-growing session transcript. `--limit` is
not needed to protect anything, and a bounded window that reaches each pointer only once would be
indexing twice rather than verifying.

**The scheduled unit is red on this host, and not for a reason above.** Exactly one pointer
classifies `unknown-age` forever: a workflow journal under `subagents/workflows/`, indexed with
`session_id` NULL, and the classifier derives a file's age from its **session**, so it can never
acquire an age basis. That single row exits the verb 1 on every pass. It was left visible rather
than filtered — `systemd/README.md` has the reasoning and the journal command that names it.

## Layout

```
bin/ingest                     the verb surface
bin/claude-session-hook        SessionStart / UserPromptSubmit / SessionEnd
                               SessionEnd also sweeps this session's subagents/ (task 0329)
ingest/config.py               DSN, schema, host_id, path_class      <- repoint here
ingest/profiles.py             scratch vs D1's migration 1
ingest/store.py                connection + one-verb-one-transaction
ingest/verbs.py                THE five transition functions
ingest/transcript.py           pure reader: file -> facts, no writes
ingest/goal.py                 goal adjudication that refuses to fabricate
ingest/events.py               producer: parks an envelope, drains it through `event emit`
                               AFTER the transaction commits (task 0287; the reason is a FK)
ingest/backfill.py             bulk read+index, and the insert-only selector both the
                               `--only-missing` pass and the hook's subagent sweep share
ingest/queries.py              reads; every count returns its denominator
schema/0001_ingest_scratch.sql the scratch schema
schema/*.sql                   D3's own tables, applied to BOTH databases by `init-schema`
hooks/                         the exact settings block to merge
docs/HOOK-INSTALL.md           what changes on the operator's machine, and the undo
docs/EVENT-CONTRACT.md         the shape D5 consumes
reports/                       the measured coverage and stated_goal reports
```

Each suite builds and drops a database of its own and never touches `d3_scratch` or `brain`:

```bash
python3 ingest/tests/test_writer_precedence.py   # the hook and backfill disagree; order must not decide
python3 ingest/tests/test_transcript_orphan.py   # an unresolvable transcript is counted, not lost
python3 ingest/tests/test_event_emit_wiring.py   # parked in the transaction, emitted after it commits
python3 ingest/tests/test_coverage_filesystem.py # a rolled-back index is countable from the disk
python3 ingest/tests/test_transcript_absence.py  # a gone file is classified, not called a failure
python3 ingest/tests/test_subagent_indexing.py   # a subagent reaches the store with nobody running anything
python3 ingest/tests/test_token_denominator.py  # usage is summed once per message, never once per record
```

## Host safety

Every pointer is stored with the `host_id` it is absolute **on**, and `workdir_path_class`
records whether a workdir is `wsl-ext4` or `wsl-drvfs`. 95% of workdirs are under `/mnt/c` and
are meaningful only on this machine. v1 is local; a VPS move can then find every broken
pointer with a query instead of one silent failure at a time.

## Live registration

Not installed globally — writing `~/.claude/settings.json` is refused by the permission
classifier. `docs/HOOK-INSTALL.md` has the exact block, the undo, a pre-change backup of the
operator's settings, and the measured proof it fires on a real session.
