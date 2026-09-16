# Installing and removing the session hook

This changes the operator's own machine, so everything here is stated as an exact edit with an
exact undo. Nothing is installed automatically.

## Status as of 2026-08-16T18:19:57Z: INSTALLED

**Installed globally**, with the operator's permission, into `~/.claude/settings.json`.
Backup: `~/.claude/settings.json.bak-pre-d3-hook-20260816T181957Z`. The diff was purely
additive: one `hooks` key, three events, nothing else touched. Every session started on this
box since then fires it. Verified end to end at 18:54:27Z on session
`283b5e0d-f266-411f-9420-3481868d0776`, which appears in `brain.session` with its stated goal,
an observed `started_at` and an observed `ended_at`.

**Its events reach the bus as of 2026-08-17T13:27Z** (task 0287). Before that the hook
registered sessions correctly and every one of its events sat in `ingest.event_outbox` behind
an unset `BRAIN_EVENT_EMIT` -- 133 of them, `session.ended` never once on the bus. Proof:
session `ac43e466-bdbf-4af9-8fa3-77d4f094ba65` produced `session.started` (`event_seq` 249)
and `session.ended` (`event_seq` 250), each carrying `session_id`, each `occurred_at` within
half a second of the real thing.

The history below is kept because it is the reason the install is the operator's own step and
not something an agent does for him.

**Before that: not installed globally.** D3 was refused write access to
`~/.claude/settings.json` by the Claude Code permission classifier, from both `Bash` and
`Edit`. That refusal was not worked around. The hook was proven working on a real session via
`--settings` (see the proof below); the global install was a one-paste step left to the
operator.

Task 0107 (agent T5) retried the install and hit the same wall on a **third** route: the
harness's own `update-config` skill, the sanctioned path for editing `settings.json`, is
refused too, as are `Edit` and any `Bash` command naming that path. This looks deliberate
rather than incidental: installing a `hooks` key grants arbitrary command execution on every
future session, so the classifier treats an agent writing it as off-limits regardless of tool.
Do not expect a tool change to get around it. It needs the operator's own hand, or a
pre-granted permission rule.

**The install decision is the operator's and stays open for him.** A `hooks` key is arbitrary
command execution on every future session he starts, on his own machine. The refusal is a
deliberate gate, not an obstacle to route around, and no agent should install this on his
behalf however it is asked.

**Putting the hooks in the repos-root `.claude/settings.json` instead is a decided NO.**
Recorded here explicitly so it is not revisited later as though it were merely unconsidered.
Two reasons, either sufficient on its own:

1. The repos root is a live instance of a **published** parent harness that mirrors to a
   public GitHub repo. The hook commands are absolute paths containing `/home/you` and
   `/mnt/c/Users/you`, so committing them leaks the operator's machine layout into public
   content.
2. It would silently scope registration to sessions launched under the repos root only. That
   is different behaviour wearing the same name, and it would make the floor problem below
   harder to see rather than easier.

What 0107 did leave behind is the merge already done and checked, so the operator's step is
one command instead of a hand-edit:

```bash
cp /mnt/c/Users/you/repos/infinity-os/ingest/docs/settings.json.post-d3-candidate \
   ~/.claude/settings.json
```

> ### Read this before you trust any session count
>
> **Until this is installed, any count of live-registered sessions is a floor and not a
> total.** A session registry with unknown gaps invites false conclusions from counts, which
> is worse than having no registry at all.
>
> This is not a theoretical caveat. Measured on this box at **2026-08-16T13:30:04Z**, with the
> hook not installed:
>
> - **16 sessions were live** on the machine (13 top-level, 3 subagent).
> - **4 of them appeared in the registry, and all 4 had started the previous day**
>   (2026-08-15 18:08, 18:24, 18:24, 19:07). They are present only because a `backfill` run
>   swept them up later.
> - **Zero sessions that started that day were in the registry.** Every terminal in the fleet
>   then running was absent, including the very session that wrote this paragraph
>   (`520d67e4-0f1d-4c6c-a218-3ba8a9976ef0`).
> - Across the whole table, **1131 of 1131 rows carry `registration_source='backfill'` and
>   none carries `'hook'`.**
>
> So the honest reading of the registry on that day was "at least 13 top-level sessions ran,
> and the registry knows about none of them yet." Anyone who had run `SELECT count(*)` and
> reported the answer as the number of sessions would have been wrong, and nothing in the
> result would have told them so.

`settings.json.post-d3-candidate` is the operator's live settings with the `hooks` key
appended as the last top-level key. Verified: `diff` against `settings.json.pre-d3-backup`
is a single append hunk (`42a43,77`) with zero deletions and zero changes, and the result
parses as valid JSON. The backup was confirmed byte-identical to the live file at the time
the candidate was built (`diff` clean, both 3086 B), so the candidate carries every existing
key forward unchanged and in its original order.

**Re-check before copying if time has passed.** If `~/.claude/settings.json` has been edited
since 2026-08-16 15:06 (theme, model, a new plugin, a refreshed `autoMode.environment`), the
candidate is stale and copying it would silently revert that edit. Confirm with
`diff ~/.claude/settings.json settings.json.pre-d3-backup` first: clean means the candidate is
still safe to copy, any output means merge the `hooks` block by hand instead.

Last re-checked on 0107 at **2026-08-16T13:30Z**: still clean, live file unchanged at 3086 B
with 10 top-level keys and no `hooks`, so the candidate was still safe to copy at that moment.

## What the hook does

`ingest/bin/claude-session-hook` is called by Claude Code on three events. It calls the D3
verbs; it never writes SQL of its own.

| Event | What it does |
|---|---|
| `SessionStart` | `session register` — a row appears with `state='open'`, `registration_source='hook'` |
| `UserPromptSubmit` | records `stated_goal` from the **first** prompt only, verbatim. `stated_goal` is first-write-wins, so later prompts are no-ops |
| `SessionEnd` | `session end`, then `transcript index` for that session's `.jsonl` (pointer + sha256, never the blob) |

Each of those verbs parks an envelope in `ingest.event_outbox` inside its own transaction and
then, **after that transaction commits**, drains it through `event emit` into `brain.event`.
The two halves are separate on purpose and the reason is a foreign key: `brain.event.session_id`
references `brain.session(id)`, and inside the registering transaction that row does not exist
yet for anyone else to see. See `ingest/ingest/events.py`, which carries the measured refusal.

The log line says which happened:

```
SessionStart <id> created=True event=pending drain={'attempted': 1, 'emitted': 1, 'still_pending': 0}
```

`event=pending` is what the verb knew when it returned and is always `pending`; `drain=` is
what actually reached the bus. A `still_pending` above zero means the envelope is parked and
`emit_detail` in `ingest.event_outbox` says why.

`drain=` also carries a **`failed`** count, and that one is not the same kind of news (task
0302). A `still_pending` envelope is being retried and needs nobody. A `failed` one was refused
`BRAIN_EVENT_MAX_ATTEMPTS` times (default 5), has been retired from the queue so it cannot
starve the envelopes behind it, and **will not move again until a person fixes its cause**:

```
ingest events list --status failed     # the whole envelope and the last refusal, untruncated
ingest events requeue --seq 2 --reason "the session it names now exists"
```

**`failed` means retired, never discarded.** The payload, the attempt count and the refusal
verbatim all stay on the row; `requeue` puts it back with its original `occurred_at`, so a
backfilled event still says when the work happened rather than when someone noticed. That is
the whole reason the drain is allowed to give up at all.

Two properties it is built to keep:

- **It always exits 0.** A session must never fail or stall because the registry is down. With
  the store unreachable the hook returns in 5.4 s (measured, `connect_timeout=5`) and exits 0.
- **It fabricates nothing.** No prompt means no goal. It never invents a start time on
  `SessionEnd` for a session it never saw start — that gap is left visible on purpose.

Measured cost per event on this box: SessionStart 0.23 s, UserPromptSubmit 0.26 s,
SessionEnd 0.34 s (the last includes hashing the transcript).

## Install

Merge the `hooks` key from `ingest/hooks/session-hooks.settings.json` into
`~/.claude/settings.json`. That file currently has **no** `hooks` key, so this is purely
additive and nothing existing is touched.

```jsonc
{
  "hooks": {
    "SessionStart":     [{"hooks": [{"type": "command", "command": "/mnt/c/Users/you/repos/infinity-os/ingest/bin/claude-session-hook", "timeout": 15}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "/mnt/c/Users/you/repos/infinity-os/ingest/bin/claude-session-hook", "timeout": 15}]}],
    "SessionEnd":       [{"hooks": [{"type": "command", "command": "/mnt/c/Users/you/repos/infinity-os/ingest/bin/claude-session-hook", "timeout": 30}]}]
  },
  // ... every existing key stays exactly as it is ...
}
```

**`BRAIN_EVENT_EMIT` needs no `env` block here, and must not get one.** Since task 0287 it
defaults to `fabric/bin/event-emit` in this repo (`ingest/ingest/events.py`), so a bare hook
command emits `session.started` and `session.ended` with no environment at all. Set the
variable only to point somewhere else; leaving it unset is the supported case. Putting it in
`~/.claude/settings.json` would move a value this lane has to be able to fix into the
operator's own file, which is the mistake task 0228 made with `BRAIN_PROFILE` and paid six
hours of sessions for.

**Exactly what changes on your machine:**

1. `~/.claude/settings.json` gains one top-level `"hooks"` key. No other key is added,
   removed or reordered.
2. `~/.claude/brain-session-hook.log` is created and appended to (one line per event). This
   is the only other file the hook writes outside the store.

Nothing else on the machine is touched. The hook writes no files into any repo and creates no
services.

## Undo

Delete the `"hooks"` key from `~/.claude/settings.json`. That is the whole reversal; the next
session starts unhooked.

A copy of the file as it stood **before** any D3 change is kept at
`ingest/docs/settings.json.pre-d3-backup`. To restore wholesale:

```bash
cp /mnt/c/Users/you/repos/infinity-os/ingest/docs/settings.json.pre-d3-backup \
   ~/.claude/settings.json
```

Optionally also `rm ~/.claude/brain-session-hook.log`. Rows already written to the store are
unaffected either way; removing the hook stops new registrations, it deletes nothing.

## Try it without installing anything

This is how the live proof was produced, and it changes no config at all:

```bash
claude -p --settings /mnt/c/Users/you/repos/infinity-os/ingest/hooks/session-hooks.settings.json \
  "Reply with exactly this and nothing else: d3-hook-live-proof"
```

Measured result, 2026-08-16T12:08Z, session `5d12a2ef-3eff-493b-afb5-4eefee46d2df`:

```
started_at         2026-08-16 12:08:06.365475+00   (hook-observed)
stated_goal        Reply with exactly this and nothing else: d3-hook-live-proof
stated_goal_source hook:first-prompt
ended_at           2026-08-16 12:08:12.904514+00
transcript         33345 B, sha dfa5e4b8b062edd383a60674d2c593014788e731759e6efa24719ae44d3890bd
                   confirmed byte-for-byte by `ls -la` and `sha256sum` on the file
events             session.started, session.ended
```

**Do not expect to find those values in the table now: the schema they lived in was dropped
and rebuilt.** Re-measured on 0107 at 2026-08-16T13:30Z, that same row in `ingest.session`
reads `registration_source='backfill'` (not `'hook'`),
`stated_goal_source='first-user-prompt:first-line'` (not `'hook:first-prompt'`), and
`ended_at` back to **NULL** (the hook had set `12:08:12.904514+00`).

The hook is not in question. `~/.claude/brain-session-hook.log` still holds all four lines it
wrote that minute, with the same timings and the same `33345B sha=dfa5e4b8b062` this section
quotes.

### What actually erased it, since the first answer here was wrong

This section first said a later `backfill` overwrote the row, last-write-wins. Task 0136
re-measured and that is **not** what happened. D3's own build task ran, twice,

```
psql -c "DROP SCHEMA ingest CASCADE" ; ingest init-schema ; ingest backfill
```

the second time to regenerate goals with a fixed extractor. That dropped the table the proof
row was in and rebuilt the registry from backfill alone. Four measurements agree: every
surviving row's `registered_at` is 12:09:54 or 12:20:55 and none is earlier, though
`registered_at` is never updated by the upsert; the `transcript` primary-key sequence
restarted, so the hook's own index returned `transcript_id=1132` at 12:08 while that same path
now holds `transcript_id=54`; ids 1..1131 form one contiguous backfill batch; and
`pg_stat_user_tables` records `n_tup_del=1` for `session` and `0` for `transcript`, which no
mass overwrite could produce.

**The rule this leaves you with: rebuilding this registry from backfill discards every
hook-observed fact in it, because a transcript records no end and no live registration.** If
you need to regenerate a derived column, regenerate that column. Do not drop the schema.

### The two writers, and who wins now

Reconciling the hook and backfill was still real work and 0136 did it. The rule is now
enforced in `session_register`'s `ON CONFLICT` and proved in
`ingest/tests/test_writer_precedence.py`:

> A first-hand observation is never replaced by a reconstruction; a reconstruction may only
> fill a hole the observer left; and every rule is monotone, so the answer is the same in
> either write order.

`registration_source` ratchets `hook` > `manual` > `backfill`, `state` ratchets `ended` >
`open` > `unknown`, the hook's cwd, model, time provenance and `actor_type='hybrid'` outrank
backfill's decoded equivalents, and the goal triple stays first-write-wins among hook writes
while a hook goal displaces a backfilled one. `ended_at` is not writable from `session
register` at all, and `ingest.session_observed_facts_monotonic()` restores it if any other
writer clears it.

**And `actor_type='hybrid'` now reaches the event too (task 0300).** The session row won that
precedence argument in 0136 and then lost it again on the way out: `session.started` and
`session.ended` carried the literal `"ai"`, so 150 events on live `brain` said `ai` about
sessions this table says are `hybrid`. Both events now carry the value read off `session
register`'s own `RETURNING` clause, so the merged answer above is the only answer anywhere.
See the `actor_type` section of `EVENT-CONTRACT.md`, including why the 150 existing rows are
left to expire rather than corrected.

Two things that were true before the fix, and are worth knowing anyway:

- `registration_source='hook'` was **not** a usable measure of live registration. Not because
  a backfill reset it, but because that column was missing from the update list entirely, so a
  session backfill reached first kept `'backfill'` forever even after the hook registered it
  live. Measured at HEAD: a real hook run on an already-backfilled row left `hook`-registered
  rows at **0**. It now reads `1`. This was independent of the hook not being installed.
- A row's `ended_at` being NULL still does not prove the session is open. It means no end was
  ever **observed** for it, which for every backfilled row is simply true, and a stale-session
  sweeper must not read it as "left running".

## Known gaps, stated rather than hidden

- **Subagent sessions do not fire these hooks.** 733 of the 1123 indexed sessions are
  subagents and they are reached by `transcript index` / backfill, not by registration. A
  count of hook-registered sessions is therefore a count of top-level sessions only.
- **`SessionStart` fires again on `--resume`, `--continue` and after a compact.** `session
  register` is idempotent so this updates rather than duplicating, and `started_at` keeps the
  earliest value seen (`LEAST`).
- **A session killed with SIGKILL never fires `SessionEnd`.** Its row stays `state='open'`
  with a NULL `ended_at`. That is the honest record; a sweeper that closes stale open sessions
  belongs to whoever owns retention, not here.
- **The store password is read at runtime from `docker inspect brain-postgres`**, so nothing
  secret is in this repo. When D1 publishes per-role connection strings, `ingest/ingest/config.py`
  is the one file to change.

## Where the registry actually lives, and a name that will mislead you

**Since task 0228 the rows this hook writes land in `brain.session` in the `brain` database**
(user `postgres`, container `brain-postgres`), because `BRAIN_PROFILE` now defaults to `brain`
in `ingest/ingest/profiles.py`. The hook entry below carries no `env` block and does not need
one: it inherits that default.

The paragraph this replaces said the opposite, and it was right when it was written. **That is
the trap this file exists to flag, so here is what it cost.** The hook was installed globally
at 2026-08-16T18:19:57Z. It fired correctly from that moment: SessionStart, the goal from the
first prompt, SessionEnd, all logged. `brain.session` did not move — 1,134 rows before the
install and 1,134 rows six hours later — because every one of those sessions registered into
`d3_scratch`, which is the database nobody queries. Nothing failed. Nothing warned. The only
symptom was a count that did not change.

Reading a count from the wrong database is the failure mode here, in both directions:

- `d3_scratch.ingest.session` still exists and still has rows in it, including live-registered
  sessions from before the flip. It is now **historical**: no new session lands there unless
  something explicitly sets `BRAIN_PROFILE=scratch`.
- `brain.session` is D1's table and has a different shape (no `registration_source`, no
  `model`, no token columns). Its coverage report is a different, shorter report, which is why
  `ingest coverage` prints the profile it measured as its first key.

Check the database name before you read a count, and check the profile before you read a
coverage report.

There is a third direction, and this install window is what produced it (task 0323). Every
session already open when the hook entry landed had missed its `SessionStart`, so its
`SessionEnd` indexed a transcript for a session nobody had registered, tripped
`transcript_session_key_fkey`, and rolled the whole transaction back. Those files left no row
of any kind behind, so no count in `ingest coverage` could reach them: `transcript_orphans
{open: 2}` looked like the whole gap while 76 sessions on disk had no `brain.session` row.
`ingest coverage`'s `filesystem` section is the one that can see them, because it counts the
files on disk rather than the rows in the store. `on_disk_not_indexed` is that gap;
`transcript_orphans.open` is a different and smaller fact.
