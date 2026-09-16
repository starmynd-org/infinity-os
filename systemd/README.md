# Supervision: what survives a reboot

## If WSL has just restarted, run this first, before anything else touches the box

```bash
cd /mnt/c/Users/you/repos/infinity-os && ./systemd/reboot-test verify
```

That one line is the whole test. It runs gates 0 through 7 and prints PASS / FAIL / INCONCLUSIVE,
and it aborts at gate 0 when `boot_id` is unchanged, so it cannot be used to manufacture a green.

Three constraints, each of which destroys the answer rather than degrading it:

1. **Before any hand start.** The moment someone runs `systemctl --user start default.target`, or
   starts a single unit, the evidence about what the *boot* did is gone and only the journal is
   left. Verify first, then start whatever you were going to start.
2. **Not before ~200s.** `brain-health.timer` is `OnStartupSec=180`. Check earlier and a timer that
   has legitimately not fired yet reads as a failure.
3. **Promptly, though this one is no longer fatal.** The number this test exists to measure used to
   live only in the user journal, which retains ~3.75h on this host (measured 2026-08-17: oldest
   retained entry `18:20:43`, newest `22:05:43`; the 60s health sweep evicts `brain-store`'s single
   one-shot boot line), so an overnight restart read the next morning lost it silently. Since task
   0345 the gate also writes it to `systemd/.cold-start-log`, which nothing rotates, and gate 6
   reads that first. Verifying inside the window is still better, because only then does the
   journal survive as an independent second measurement. See *The cold-start number outlives the
   journal* below.

Report the measured `brain-docker-wait` seconds as a number either way. That number is the entire
reason the 180s gate exists and it has never been observed on a cold start.

## What this directory is

Three live services and, until 2026-08-17, not one of them restarted by itself. This directory is
the fix. Everything here is `systemd --user`, which needs no `sudo` — and `sudo` on this host needs
a password no agent may enter, so that is not a stylistic preference, it is the only mechanism
available to anything but the operator's own hands.

```
systemd/install.sh                 substitute @REPO@, install, enable-linger, enable
systemd/brain-store.service        Brain Postgres 5432
systemd/brain-console.service      console 3103
systemd/brain-paging.service       operator-paging listener (binds no port)
systemd/brain-health               the sweep; run it by hand any time
systemd/brain-health.service       oneshot wrapper for the sweep, --repair
systemd/brain-health.timer         every 60s
systemd/brain-transcript-verify         whole-corpus transcript re-hash; run it by hand any time
systemd/brain-transcript-verify.service oneshot wrapper, store-ready gated
systemd/brain-transcript-verify.timer   daily 03:20 local, Persistent=true
systemd/brain-routine-tick.service      oneshot `swarm routine tick --fire`, store-ready gated
systemd/brain-routine-tick.timer        every 5 minutes, OnStartupSec=240
systemd/reboot-test                the split before/after boot test; see the top of this file
systemd/.gitignore                 one line, and the reason the line is there
systemd/.reboot-test-before        NOT IN GIT, on purpose. See "The before-state is not committed"
```

The three timers do three different jobs and none substitutes for another. `brain-health` asks
whether the runtime is *up*, every 60s, and repairs it. `brain-transcript-verify` asks whether the
data the runtime already recorded is *still true*, once a day, and never repairs anything — a
mismatch is reported, never restated. See "Why a data sweep is not a health sweep" below.

`brain-routine-tick` is the third and it is a different KIND of unit, added 2026-08-27 by bus row
`0376`: the first one here that dispatches WORK rather than supervising a process. Everything else
in this directory restarts something or checks something. This one asks whether a slot the
operator himself scheduled has been crossed, and posts a work item if it has.

Two properties keep it from becoming a second control plane. It is a `oneshot`, so there is no
resident scheduler with its own opinion about what time it is: one pass, then the process exits
and systemd owns the next one. And it cannot fire a slot twice, because `brain.routine_run`
carries `UNIQUE (routine_id, scheduled_for)` and the occurrence row is written in the same
transaction as the post, so a second caller for one slot is refused by Postgres with 23505 and
the transaction that would have posted a duplicate cannot commit. That is measured rather than
asserted: `engine/tests/test_routines.py` runs eight concurrent processes at one slot with the
constraint dropped and then with it in place.

Turning one routine off is one act and needs no credential:

```bash
swarm routine disable <name> --reason "..."
```

Turning ALL of them off is `swarm pause`, which the tick honours. Stopping the timer itself
(`systemctl --user stop brain-routine-tick.timer`) is a third, coarser switch and the only one
that also stops the audit trail, so prefer either of the first two.

Install (idempotent, safe to re-run after editing a unit):

```bash
./systemd/install.sh
systemctl --user start brain-store.service brain-console.service brain-paging.service \
                      brain-health.timer brain-transcript-verify.timer
./systemd/brain-health              # store / console / paging, one line each
./systemd/brain-transcript-verify   # full transcript re-hash, before/after reading
```

`install.sh` enables the two **timers** and not the oneshot services behind them. A timer's `Unit=`
starts its service; enabling the service as well would additionally run it at every boot, on top of
the schedule.

## The three health checks, and why they are three different shapes

| service | check | what it would miss on its own |
|---|---|---|
| store | `pg_isready` inside the container | nothing about whether anything is *listening* |
| console | `GET /api/health` → 200 | nothing; the handler opens a read transaction, so 200 proves the store too |
| paging | lag **and** the cursor's clock | a *count* threshold alone misses a listener that died while the bus was quiet |

That third row is the one that cost something to learn. `fabric/lag.py` is right that lag is the
signal for a listener — a listener that is running, connected and stuck is green under every
process-level check, and listeners bind no port so the port registry's health-check rule does not
reach them at all. But measured on this box on 2026-08-17, with the `operator-paging` process not
running for 14.8 hours and five events waiting:

```
$ python3 -m fabric.cli health
"subscriber": "operator-paging", "lag": 5, "findings": [], "state": "ok"
"verdict": "keeping up"
```

`state: ok` over a corpse. `LAG_WARN` is 25, the lag was 5, and the check would have noticed around
event 126 — roughly an hour later, which is exactly the window in which a page is not a page. The
discriminator is not the count. It is the count **together with** the cursor's own clock:

- `lag == 0` → caught up. A quiet bus is not a sick one; a staleness alarm alone would page nightly.
- `lag > 0`, cursor advancing → draining. Normal.
- `lag > 0`, cursor frozen → **stuck**, and that single reading covers dead, hung, crash-looping
  and partitioned all at once.

`subscribers/bin/paging-health` implements it. Against the same live cursor it exited `2 STUCK`
where `fabric.cli health` printed `ok`.

## Why there is a timer as well as `Restart=`

`Restart=on-failure` covers the process that **died**. It is structurally blind to the process that
is alive and not working: a console answering TCP but not HTTP, a listener connected and not
draining. `brain-health.timer` runs the sweep every 60s with `--repair` and restarts exactly those
two. It never restarts the store (the container already carries `--restart unless-stopped`; if it
is down, docker chose not to restart it and that is a page, not a repair), and it never restarts a
**quarantined** listener — `fabric/listener.py` refuses to start while a quarantine stands, because
release is a human's act through the runtime role, and bouncing it produces the sawtooth quarantine
exists to prevent.

`on-failure`, never `always`, everywhere: a supervisor must not resurrect an intentional clean exit.

## Why a data sweep is not a health sweep

`brain-transcript-verify` was added by task 0335, and the reason it is a second timer rather than a
branch of `brain-health` is that the two answer questions with different shapes:

| | `brain-health` | `brain-transcript-verify` |
|---|---|---|
| question | is the runtime **up**? | is what the runtime **recorded** still true? |
| cadence | 60s — a dead listener is an outage now | daily — see below, the floor is set by retention |
| on failure | **repairs** (restarts console / listener) | **never** repairs; a mismatch is reported, not restated |
| cost | milliseconds | ~20-25s cold, 4.25s warm, whole corpus |

Folding the second into the first would have to pick one cadence for both, and both choices are
wrong: 60s means re-hashing the corpus 1,440 times a day, and daily means an outage goes unnoticed
for a day.

**The daily cadence is a floor, not a preference.** The harness deletes transcripts on a ~30-day
horizon (task 0324 measured a hard date cut: 100% of the corpus gone before 2026-07-19, 0% after),
so a fresh cohort ages out every day. Until a verify pass reaches them, the day's losses sit in
`ingest coverage` → `filesystem.indexed_not_on_disk.not_yet_recorded`: counted, but **unclassified**,
which is the state in which nobody can tell routine housekeeping from a real deletion. Less often
than daily and cohorts pile up there unattributed.

**Why the whole corpus and not `--limit N`.** 0335 was posted expecting a full pass to be costly and
permanently red. Measured on this host 2026-08-17: 900 pointers in 13.98s cold, and the entire
sweep including both coverage reads in 4.25s warm — cheaper per day than `brain-health` spends per
minute. The predicted red did not happen either: a full pass returned **0 mismatch** with 2
`appended`, because task 0330 re-hashes the first `stored_bytes` and asks whether the file *grew*
rather than whether it *changed*. An open, actively-growing session transcript verifies clean.

**This unit is red on this host today, and that is not a malfunction.** `transcript verify` exits 1
on what a human must act on: a hash that moved, a file present and unreadable, or a pointer that
vanished while still inside the retention horizon (`unexplained`) or with no derivable age at all
(`unknown-age`). There is exactly one `unknown-age` pointer — a workflow journal under
`subagents/workflows/`, indexed with `session_id` NULL, and task 0324's classifier derives age from
the pointer's **session**, so it can never acquire an age basis and re-reports every pass. It is
left visible rather than filtered out, because a check quietly widened until it passes has stopped
being a check. The sweep prints its actionable breakdown by name, so the red states its own cause:

```bash
systemctl --user --failed                                   # names the unit
journalctl --user -u brain-transcript-verify -n 30           # names the reason
```

## Two traps `systemd --user` sets, and where they are handled

1. **A user unit cannot order itself `After=` a *system* unit.** `docker.service` lives in the
   system manager and there is no ordering edge between managers, so `After=docker.service` in
   `brain-store.service` would be silently unsatisfiable rather than an error. The wait is inside
   the unit instead: `ExecStartPre=store/bin/brain-docker-wait.sh 180`.
2. **`Requires=` + `After=` gets you "the prior unit reached active", not "the server behind it
   answers".** `docker start` on an existing container returns before Postgres finishes crash
   recovery, which on a cold boot is precisely when a dependant that trusted the edge alone gets
   ECONNREFUSED and dies. `ExecStartPre=store/bin/brain-store-ready.sh 120` blocks on `pg_isready`.

## Linger

Without `loginctl enable-linger` the user manager is not started at boot at all and is torn down
when the last session closes, so every unit here would wait for the operator to open a shell — which
defeats the whole point on an always-on box. `install.sh` enables it; it needs no `sudo` for one's
own user. Confirm with `loginctl show-user "$USER" --property=Linger`.

**Measured 2026-08-17 22:04 EEST: configured, never exercised.** `Linger=yes`, and the marker file
`/var/lib/systemd/linger/you` exists (root-owned, 0 bytes, mtime Aug 17 16:20, written by
`install.sh`). So the setting is real. It has not yet started anything, and the numbers say so
rather than the calendar:

```
user@1000.service   ActiveEnterTimestampMonotonic = 12803405 us after boot
session 1 (you, Service=login, Type=tty, pts/1)  TimestampMonotonic = 12490310 us after boot
```

The session preceded the user manager by 313ms, so on this boot PAM started the manager, not
linger. Consistent with the dates: this boot began Sat 2026-08-15 22:05:38 EEST and linger was
enabled two days into it. Run `reboot-test verify` gate 1 against this boot and it would print
INCONCLUSIVE, which is the correct reading. Linger being set is a configuration fact; linger having
started the manager is an observation nobody has made yet.

## Reading the verifier's exit code

The command is at the top of this file. What it returns:

| exit | meaning |
|---|---|
| `0` | PASS — the boot link is proven, all three units came back with no human command |
| `1` | FAIL — a hard failure. Report the measured numbers **before** changing any timeout |
| `2` | ABORT — no reboot actually happened (`boot_id` unchanged); this is not the test |
| `3` | INCONCLUSIVE — a gate could not discriminate. Do **not** record this as a pass |

`2` and `3` are the two that matter, because both are states in which everything on screen looks
healthy. `2` means you are measuring the same boot the capture came from. `3` means the run could
not separate linger from a PAM login, or gate 6 could not recover the cold-start number. Neither is
a pass, and the script deliberately will not round either one up.

If the reboot is deliberate rather than incidental, re-capture the before-state first with
`./systemd/reboot-test capture`, which also prints the full shutdown protocol. The current capture
is in `systemd/.reboot-test-before` and was taken on boot `ced7f52c`.

### The before-state is not committed

`systemd/.reboot-test-before` is in `systemd/.gitignore`. It was never lost and it is not missing:
it is deliberately the one file in this directory that git does not carry, and the reason is that
committing it would break the test rather than protect it.

The verifier reads exactly one field out of that file: `captured_at_boot_id`, at `reboot-test:133`,
plus the file's existence at `:136`. Nothing else in it is ever compared. The `--- units ---`,
`--- enabled ---`, `--- docker ---` and `--- health ---` blocks are there for a human reading the
capture; gates 1 through 7 re-measure all of it live against the running system. So the file's
entire machine-readable content is one UUID.

That is what makes committing it unsafe. Gate 0 passes when the recorded boot id differs from the
current one. A committed before-state hands every fresh clone, on any machine, a boot id that
cannot match — an automatic gate-0 pass certifying a restart that never happened on that host. The
guarantee at the top of this file ("it aborts at gate 0 when `boot_id` is unchanged, so it cannot be
used to manufacture a green") is exactly the thing a committed capture would switch off.

The failure modes are not symmetric, which is why this is the safer side to err on:

- **File lost, then verify:** it says so, by name, and refuses — `no before-state at ... Run
  './systemd/reboot-test capture' before rebooting`, exit `2`. Loud, and recoverable: the boot the
  current capture belongs to is `ced7f52c-b195-49f4-badd-b22dd1b53592`, recorded here in full for
  exactly that reason. `printf 'captured_at_boot_id=%s\n' ced7f52c-b195-49f4-badd-b22dd1b53592 >
  systemd/.reboot-test-before` restores everything the verifier actually consumes.
- **File committed, then verify on a fresh clone:** a green PASS on gate 0, silently, with no way
  to tell it from the real thing.

One practical note: being ignored is also stricter than being merely untracked, which is what it was
before 2026-08-18. An untracked, unignored file is deleted by a plain `git clean -fd`; an ignored one
survives that and takes `git clean -fdx`.

## The cold-start number outlives the journal

The whole reboot test exists to produce one number: how many seconds
`store/bin/brain-docker-wait.sh` waited for dockerd on a cold start. Until 2026-08-18 that number
was emitted exactly once, by a `Type=oneshot` unit, into the user journal, and nowhere else — and
the user journal on this host retains ~3.75h. A restart at 03:00 read at 09:00 showed three green
units with the measurement already destroyed: a green from a test that had quietly stopped
measuring. Gate 6 was taught to *detect* that (task 0299), which is a warning, not a fix; it does
not help when nobody is at the keyboard inside the window, which on an unattended overnight
restart is the normal case.

The fix (task 0345) is that the script which measures the number now also persists it. Every
invocation appends one line to `systemd/.cold-start-log`:

```
wall=2026-08-18T03:00:11+03:00 boot=<boot_id> uptime_us=41000000 ppid_comm=systemd \
  invocation=<systemd InvocationID> outcome=reachable waited_s=7 budget_s=180 pid=1234
```

Both outcomes are recorded, `reachable` and `timeout`, because a boot where the gate blew is the
case most worth keeping. Nothing rotates the file, and a runaway guard trims it to the last 400
lines past 500.

Four things about it are load-bearing:

- **The write can never fail the gate.** It is called `|| true`, every command inside is guarded,
  and the wait behaviour is byte-identical whether the file is writable or not. Verified by
  pointing `BRAIN_COLD_START_LOG` at an unwritable path: exit `0`, stdout unchanged. A supervision
  gate must not acquire a new way to fail in order to become observable.
- **stdout is untouched.** The journal line is still emitted, in the same words, because when it
  survives it is a genuinely independent second source. Gate 6 reads both and says so loudly if
  they disagree for the same invocation.
- **Gate 6 accepts a record only when it carries this boot's `boot_id` *and*
  `brain-store.service`'s live `InvocationID`.** `boot=` alone would accept a hand run from hour
  six of the same boot; `invocation=` alone would accept a record from an earlier boot. Together
  they say: written by the process the live `brain-store` invocation started, on the boot being
  verified. Without that, a file would have been a way to manufacture a green rather than a way to
  keep one honest.
- **`INVOCATION_ID` being set proves nothing on its own — it is inherited.** Measured 2026-08-17:
  a hand run from an agent shell arrived with a perfectly real `INVOCATION_ID`, belonging to the
  `claude-<n>.scope` the shell ran in. "It has an invocation id" is not "systemd started it"; only
  the exact match above is. `ppid_comm` is recorded alongside because it is *not* inherited
  (`systemd` under `ExecStartPre`, a shell name for a hand run).

What can still destroy the number is a hand `systemctl --user restart brain-store` before
verifying: that mints a new invocation against a dockerd that has been warm for hours. Gate 6
catches that too, via `uptime_us` — the boot's own activation is necessarily early (the wait gate
itself only allows 180s), so a record written more than 900s after boot is reported as a warm hand
restart and scored inconclusive, never printed as a passing number.

`systemd/.cold-start-log` is gitignored, for a weaker reason than
`.reboot-test-before` above: it *cannot* forge a pass, since neither the boot id nor the invocation
id survives a clone. It is ignored because it is measurement output that changes on every boot, and
on a host with a daily auto-commit, committing it would put runtime churn in the history of a
directory whose job is configuration. Its durability comes from the filesystem, not from git.

## What has NOT been proven

**No WSL restart has been performed.** The operator is working on this machine and a WSL shutdown
would kill his session and the running swarm fleet with it. What that leaves untested is the one
thing only a real boot can test: that the user manager comes up under linger and reaches
`default.target` on a cold start. Everything downstream of that moment was tested, by stopping the
units and running `systemctl --user start default.target` — the same activation the boot performs.
Until someone reboots, this is a supervision story nobody has restarted, and that is the same shape
as a rollback nobody has tried. Say so rather than rounding it up.

### Evidence, and configuration that is not evidence

Re-measured 2026-08-17 22:04 to 22:07 EEST, boot `ced7f52c`, on the same boot the units were
installed into. Read the last column as the honest state of each service.

| service | evidence I have | configuration only |
|---|---|---|
| `brain-store.service` | `active (exited)`, `NRestarts=0`, `ExecMainStartTimestamp=Mon 2026-08-17 16:20:32`. Container `brain-postgres  Up 31 hours`. The 22:06:48 sweep printed `store OK 127.0.0.1:5432 - accepting connections`. | Starts at boot. `WantedBy=default.target`, symlink present in `~/.config/systemd/user/default.target.wants/`. Never once started by a boot. |
| `brain-console.service` | `active (running)`, `NRestarts=0`, since `16:24:39`. My own `curl` returned `200` from `http://127.0.0.1:3103/api/health` at 22:05. | Same. Enabled and wanted by `default.target`, never started by one. |
| `brain-paging.service` | `active (running)`, `NRestarts=0`, since `16:24:39`. The 22:06:48 sweep printed `paging OK keeping up`. | Same. Enabled and wanted by `default.target`, never started by one. |
| `brain-health.timer` | `active (waiting)`, firing on time: last `22:06:47`, next `22:07:47`. | `OnStartupSec=180` has never run at a startup. |

Every one of those start timestamps is a hand start performed after `install.sh` on 2026-08-17.
This boot began 2026-08-15 22:05:38, two days before the units existed, so no unit here has ever
been activated by a boot and the `WantedBy=` line is a promise rather than a record. The
`systemctl --user start default.target` rehearsal described above is the closest thing to a proof
and it is not this one: it exercises the activation, not the link from boot to activation.

One trap for whoever reads gate 6. A missing `docker engine reachable after Ns` line does **not**
prove the unit never ran. Measured right now: `brain-store.service` is `active (exited)` from
16:20:32 and `journalctl --user -u brain-store.service` prints `-- No entries --`, with or without
`-b`. It ran; the journal rotated. The discriminator is
`journalctl --user -o short-full | head -1`: if the earliest retained entry is newer than the
unit's `ExecMainStartTimestamp`, the absence is rotation and says nothing either way.

That trap is now survivable rather than merely detectable, but not retroactively. This boot
(`ced7f52c`) ran the pre-0345 `brain-docker-wait.sh`, so `systemd/.cold-start-log` does not exist
yet and gate 6 says so in those words: *"does not exist either, so this boot ran a
brain-docker-wait.sh from before task 0345 ... the durable copy starts at the NEXT boot."* The
installed unit calls the repo copy of the script by absolute path
(`ExecStartPre=/mnt/c/.../store/bin/brain-docker-wait.sh 180`, `@REPO@` already substituted), so
the change is live for the next boot with no `install.sh` re-run and no `daemon-reload`. The first
line will be written by the first restart after 2026-08-18.
