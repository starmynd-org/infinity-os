# `intake-service/systemd/` — the intake cadence, staged, not installed

**Everything in this folder is written and proven. Nothing in it is enabled.** Installing a
persistent service is the operator's decision and this lane does not make it. What follows is the
whole of what he has to do, and the one choice he has to make first.

    brain-connector-sweep              the wrapper. Both units call it. Takes `email` or `meetings`
    brain-email-sweep.service/.timer   the owned mailboxes' capture folders, every 15 minutes
    brain-meeting-sweep.service/.timer tl;dv meetings and the named Slack channel, hourly
    brain-intake.service               the door itself, staged by the S1 lane 2026-09-01

**Why this folder and not `systemd/`.** This lane owns `intake-service/`, `ingest/connectors/` and
`automations/n8n/`. `systemd/` and its `install.sh` belong to another lane, and a file written into
a directory this lane does not own is a merge conflict at best and a silently reverted edit at
worst. That is the same reason `brain-intake.service` is here. `systemd/install.sh` performs the
`@REPO@` substitution for the units in ITS directory and does not see these, so the substitution
below is done by hand until somebody adopts them across.

---

## THE ONE DECISION, BEFORE ANYTHING IS ENABLED

> **STATUS 2026-09-02T17:41Z: the EMAIL sweep is SEEDED, ARMED and ENABLED.** The operator ruled
> it. The watermarks were seeded first (all 605 backlog messages declared handled, verified by a
> dry run offering 0), then `SWEEP_PUSH=1`, then `enable --now`. Its steady state is a pass that
> takes in nothing every fifteen minutes until he labels a message into a `to-brain` folder.
> **The MEETING sweep is still disarmed and disabled**; it was not part of that instruction.

**The meeting unit ships with `SWEEP_PUSH=0`. It reads every source and writes nothing.** That is
deliberate: a unit enabled by accident, or ahead of a decision, cannot land anything. Arming is one
line in each unit file.

For the meeting sweep, arming is safe and needs no preparation. Neither tl;dv nor Slack carries any
state; each pass offers the newest N items and the door deduplicates everything it has seen before,
so the ceiling is a handful of genuinely new items per pass and the steady state is `pushed 0`.

**For the email sweep, read this paragraph before setting `SWEEP_PUSH=1`** — it is already set,
and this is why it is safe, and what would make it unsafe again.

Measured 2026-09-02. The seven owned mailboxes hold **605 messages** in their `to-brain` capture
folders, none newer than 2026-06-26. **31 have been taken**: 25 from the pilot drain on
one mailbox, 5 from the one-firing gate proof (one from each of five other mailboxes), and
1 re-push of the message that had been refused. Both were authorised by the operator on 2026-09-02
and together they are what put mail on the board for the first time. **574 have never been taken,
and whether he wants them is still open.**

The watermark is the *only* bound on a capture folder. That is the whole of the 2026-09-02 fix:
`IMAP SEARCH SINCE` filters on `INTERNALDATE`, which for a Gmail label is the date the message
*arrived* and never the date the label was *applied*, so a date window over a capture folder hides
the operator's own capture gesture. Removing that bound is what makes the folder readable at all,
and it is also what makes an unseeded account drain. So:

**Arming the email sweep against unseeded accounts starts taking those 580, at
`SWEEP_EMAIL_LIMIT` per account per tick — 5 per account every 15 minutes, which is the entire
backlog inside a day.** That may be exactly what he wants. It must not happen because nobody read
this file.

**What was actually done, 2026-09-02: (a) below, then armed.** So the 574 are declared handled and
this cadence will never take them; they are still in the mailboxes and `--reset-watermark` brings
them back. **If you ever run that reset, or install this on another host, DISARM FIRST** — both put
the marks to zero, and an armed unit over unseeded accounts drains the backlog at 5 per account per
tick.

Two one-line answers, and both are reversible because nothing is ever deleted from a mailbox and
the door deduplicates:

    # (a) START CLEAN FROM TODAY. Marks the backlog as seen without taking any of it, so the
    #     cadence lands only what he labels from now on.
    python3 ingest/connectors/email_imap.py --accounts all --seed-watermark

    # (b) CHANGE YOUR MIND, EITHER WAY. Drops the marks; the backlog is a candidate again.
    python3 ingest/connectors/email_imap.py --accounts all --reset-watermark

    # (c) TAKE THE BACKLOG DELIBERATELY, at a pace you choose, without a timer:
    python3 ingest/connectors/email_imap.py --accounts all --limit 50 --oldest-first --push

**And one thing to know before you enable anything.** `systemctl --user enable --now <timer>` on
these timers **fires the service immediately**, it does not wait for the interval. `OnBootSec=5min`
is already in the past on a machine that has been up longer than five minutes, so systemd triggers
at once. Enabling *is* the first tick. That is how the gate proof below was run.

---

## Install

    REPO=$HOME/repos/infinity-os
    for u in brain-email-sweep.service brain-email-sweep.timer \
             brain-meeting-sweep.service brain-meeting-sweep.timer; do
      sed "s#@REPO@#$REPO#g" "$REPO/intake-service/systemd/$u" > ~/.config/systemd/user/"$u"
    done
    systemctl --user daemon-reload

**The four files above are already in `~/.config/systemd/user/` as of 2026-09-02**, put there to
prove they run. Re-running the loop is harmless and is how you pick up any edit to the staged
copies. Nothing is enabled: `systemctl --user is-enabled brain-email-sweep.timer` says `disabled`,
and so does the meeting one.

Run one by hand, which pushes nothing while `SWEEP_PUSH=0`:

    systemctl --user start brain-email-sweep.service
    journalctl --user -u brain-email-sweep.service -n 60 --no-pager

Arm it, when the decision above is made — edit the `Environment=SWEEP_PUSH=` line in the staged
`.service` file, re-run the install loop, `daemon-reload`. Then, and only then:

    systemctl --user enable --now brain-email-sweep.timer      # DONE 2026-09-02T17:41Z
    systemctl --user enable --now brain-meeting-sweep.timer    # not done: still disarmed
    systemctl --user list-timers --all

To stop: `systemctl --user disable --now brain-email-sweep.timer`.

**A note on leaving them disarmed and enabled.** It works, and it is a reasonable way to watch the
wiring for a day, but a dry run prints the full JSON payload of every message it would have pushed
and a push run prints one line each. Disarmed and enabled at 15 minutes is a lot of journal. It is
a staging posture, not a resting one.

---

## What has been proven, and how

Run under systemd on 2026-09-02, not imported, not simulated:

| what | evidence |
|---|---|
| `brain-email-sweep.service` runs green | `Result=success ExecMainStatus=0`. 7 accounts, 2 client mailboxes excluded by the connector itself, door probe `READY schema=52`, offered 580, pushed 0 |
| `brain-meeting-sweep.service` runs green | `Result=success ExecMainStatus=0`. tl;dv offered 69, Slack offered 5 |
| a dead door **reddens** the unit | `ActiveState=failed`, `ExecMainStatus=1`, listed by `systemctl --user --failed`, and **no connector ran** |
| a door that answers 200 with an unreadable store **also reddens** | stand-in door returning `{"store":{"read":false}}` → `the door answered but is not usable: STORE-UNREADABLE`. 200 alone is not health |
| both units parse | `systemd-analyze --user verify` clean on both timers and the services they pull in |
| **the cadence lands mail unattended** | the timer fired the service on its own and 5 objectives landed from **5 distinct mailboxes** (five of the operator's own mailboxes), denominator printed: matched 605, excluded 25, candidates 580, fetched 6, pushed 5, refused 1 |

### Three things that were nearly wrong, kept here because they will be met again

**gcloud is not on a systemd unit's PATH.** On this host there is no Linux gcloud: the only one is
the Windows SDK reached over WSL interop. An interactive shell finds it because WSL appends the
Windows PATH; `systemctl --user show-environment` does not contain it. Every connector resolves its
credential through `gcloud secrets versions access`, so a unit written the obvious way dies on
every pass with `gcloud is not on PATH` — a failure that never appears while testing by hand. The
wrapper pins it, and `GCLOUD_BIN_DIR` in the unit is the override if the SDK moves.

**systemd's 90-second default would have killed the email sweep.** `DefaultTimeoutStartSec` is 90s
and applies to `Type=oneshot`. The pass took **125 seconds** under systemd against 57.9s measured
interactively — seven TLS handshakes and seven SEARCHes over a real network, and a push pass is
slower still. Both units set `TimeoutStartSec=600`. Without it the unit dies as a *timeout*, which
reads like a hung connector rather than like an interval that was always too short.

**A NUL byte was a poison pill, and the gate run is what found it.** The one refusal above was the
door answering **500** on a DMARC aggregate report carrying fifty NUL bytes: psycopg2 raises
`ValueError: A string literal cannot contain NUL (0x00) characters` while adapting the parameter,
before any SQL, so it is not a `psycopg2.Error` and it missed the door's 503 arm. Because the
connector correctly refuses to mark a refused message as handled, that message stayed a candidate
and would have 500'd on every pass forever, holding the unit red over one message the operator
cannot see and did not write. Fixed on both sides: the connector strips NULs and records the count
in `metadata.nul_bytes_stripped`, and the door now refuses one with a **400** naming the field,
because a text column cannot hold a NUL and telling the caller to retry is telling it to do the one
thing that cannot work. The exact message now returns 201.

> **The door's 400 is not live yet.** The door is a hand-started `flask run` with no reloader, so
> pid 285624 is still running the old `payload.py`. This is **not** blocking: with the connector
> fix no NUL reaches the door at all, and the re-push was proven against that unmodified process.
> The 400 is defence in depth for every other producer, and it takes effect whenever the door is
> next restarted — which is the operator's call, not this lane's.

**Under WSL2 an unbound localhost port hangs rather than refusing.** The dead-door control returned
`curl rc=28` (timed out after 10s), not `rc=7` (connection refused). So `curl -m 10` in the probe is
load-bearing: without it the probe would hang until systemd killed the unit, and the journal would
say `timeout` where the truth is `the door is not there`.

---

## The door

`brain-intake.service` is staged here and **is not installed**. The door is currently a
hand-started process (pid 285624 since 2026-09-01, still serving 127.0.0.1:3106), which is why
neither sweep unit declares `Requires=` or `Wants=` on it: a dependency on an uninstalled unit
would make the sweep fail to start for a reason that has nothing to do with whether the door
answers. The wrapper's `GET /health` probe is the real dependency check.

**When `brain-intake.service` is finally installed, add `Wants=brain-intake.service` and
`After=brain-intake.service` to both sweep services — and keep the probe.** Ordering says the door
was *started*. Only the probe says it *answered*.
