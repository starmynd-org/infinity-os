# Decision packet: a second Unix user for the fleet

**For the operator. Nothing here is built, and nothing here should be built until you answer.**

Raised by task 0295 (V9 blocker 4) as option **B** of question q0305. You answered q0305 on
2026-08-19 with *"D PLUS A NOW. B IS RAISED TO THE OPERATOR AND NOT TAKEN BY ME. C IS DECLINED"*
and asked for B as a packet. This is that packet. D and A are done and are described at the end
so you can see what the residual actually is after them.

---

## 1. B is the only option that makes the sentence true

Every other thing anyone can build leaves this sentence false:

> An agent cannot promote a row you have held.

Today the true sentence is the narrower one: **a connection as `brain_runtime` cannot promote the
row.** That is a real database invariant, it is tested, and it is not what people have been
reading it as. The gap between the two sentences is not a bug in the schema and no schema change
closes it. It is this: the fleet's terminals and your own console run as **the same Unix user**,
your credentials are files that user can read, and a process that can read
`~/.brain-postgres-secrets/brain-postgres-role-operator` can simply *be* you as far as Postgres
is concerned. Measured from a live fleet terminal, twice, on 2026-08-19 (0295's thread).

Giving the fleet its own Unix account is the only proposal on the table that changes that,
because it makes the separation the operating system's rather than a flag's. `0700` on a
directory in your home is a real boundary between two UIDs and no boundary at all within one.

Said plainly and without softening: **if you want the strong sentence, this is the option. If you
pick anything else, the strong sentence stays false and the honest thing is to keep saying the
narrow one.**

## 2. B closes the credential hole and leaves the repos wide open

This is the part you must not read as more than it is.

`~/.brain-postgres-secrets` is on **ext4**, mode `0700`, owned by you. A second Unix user
genuinely cannot read it — including `brain-postgres-bootstrap-superuser`, which sits in the same
directory and opens a `rolsuper=true` role, and which any plan that moves only the operator
credential would leave behind. So B closes the credential hole properly.

The repos are a different story. `/mnt/c` is a **9p drvfs mount** with `uid=1000,gid=1000` baked
into the mount options and no `metadata` option. Every file under it reports mode `777` owned by
uid 1000, including this repo, including `engine/bin/swarm`. That is not a permission that can be
tightened from inside: it is what the mount presents to every local user.

So after B:

| | before B | after B |
|---|---|---|
| fleet can read your DB credentials | yes | **no** |
| fleet can connect to Postgres as `brain_operator` | yes | **no** |
| fleet can write every file in every repo | yes | **yes, unchanged** |
| fleet can edit `engine/bin/swarm` itself | yes | **yes, unchanged** |

**B means "the fleet cannot become you in the database". It does not mean "the fleet cannot touch
your work".** The repo half needs its own answer and it is a separate job: move the checkouts to
ext4, or remount `/mnt/c` with `metadata`. Neither is part of B and neither should be bundled into
it silently.

## 3. The full inventory of what B touches

The cost is in this list rather than in the idea. Read the list before you say yes.

1. **The runner and the terminals.** Everything that claims and executes work moves to the new
   account: its `$HOME`, its shell profile, its PATH, its Claude Code configuration and
   credentials directory.
2. **The systemd units, and this is subtler than it looks.** They are `systemd --user` units
   installed into `$HOME/.config/systemd/user` — seven of them: `brain-store`, `brain-console`,
   `brain-paging`, `brain-health` (+ timer), `brain-transcript-verify` (+ timer). There is no
   `User=` line to change, because a user manager *is* the identity. So the question is not "edit
   the units", it is **which of the seven move to the fleet account and which stay with you**.
   `brain-store` (Postgres) and `brain-console` are yours; anything supervising the fleet is the
   fleet's. A second user needs its own `systemd --user` instance, which needs
   `loginctl enable-linger` for that account — **and that one needs root**, so it is a hop you
   perform yourself. (Yours already has `Linger=yes`.) Note the trade this gives up: today's
   install script needs no sudo at all, and that was a deliberate property.
3. **`~/.brain-runtime`.** Today `755` under your home: the run streams, the claim state, the
   heartbeats. The fleet must own or share it, and "share" here means deciding whether you can
   still read a run log without becoming the fleet user.
4. **The git identity.** Commits currently land as your own name and business address because
   that is the ambient git config of your account. A second user has its own, and whether fleet
   commits should keep carrying your name is a decision, not a detail.
5. **Every surface of yours that writes `as_operator`.** `web/actions.py` passes
   `as_operator=True` on every console write — queue edits, steering take/release, fleet config
   override, image landing — and it runs unattended. If the console keeps running as you it is
   unaffected; if it ever moves under the fleet account it stops working. Decide which side of the
   line the console lives on **before** the move, not after.
6. **The scratch databases and the Postgres role grants.** `brain_operator` is granted to your
   login; the new account gets `brain_runtime` only, and `store/bin/provision-operator.sh` should
   refuse to run under it.
7. **File ownership on anything the fleet already created** under `~/.brain-runtime` and in
   output directories.

## 4. What is already done, so you can see the residual

- **D (landed, task 0295).** `swarm set --as-operator` now refuses when `SWARM_AGENT` or
  `SWARM_PARENT_TASK` is set. It is a **deterrent and not a boundary**, and the refusal text says
  so itself and names both ways past it (`env -u`, and the psycopg2 route that never loads the
  CLI), the way the pre-commit guard names `--no-verify`. It addresses the failure that actually
  happened on 2026-08-18 — a helpful pass promoting rows you had deliberately held — by making
  that particular accident impossible to have *by accident*.
- **A (landed, tasks 0295).** `store/SECRETS.md`, `engine/tests/test_actor_gate.py`,
  `queue/tests/test_human_actor_identity.py`, the `claim` and `doctor` banners and the console
  card now say *held from `brain_runtime`* rather than *held from agents*.
- **C (declined by you).** Locking the credential behind sudo or a passphrase: strictly weaker
  than B, and it breaks the unattended console. Not costed further and not carried here as a live
  option.

**And one correction that changes how urgent B is.** Measured 2026-08-19 after the packet's
options were first costed: a row carrying `actor_type = 'human'` cannot be promoted by *any*
login, including yours, because migration 26's `work_item_human_is_never_agent_claimable` coerces
the flag back to false and is not conditioned on `session_user`. All fourteen of your named held
rows — a personal errand, the client sends, a credential rotation, an API key to set — carry `actor_type =
'human'`. **They were never one command away.** Reaching them takes two raw SQL statements (clear
`actor_type`, then raise the flag) and `actor_type` cannot be set through the CLI at all.

That does not make the hole imaginary and it is not offered as a reason to skip B. It means the
one-command route reaches the *other* 79 held rows — the ones held because somebody forgot
`--for-agents` — and the rows you care about most are behind one more deliberate step than this
finding first claimed.

## 5. The question

Do you want B?

- **Yes** → it is half a day to a day, it is mostly `sudo` work only you can do, and it should be
  posted as its own task with this packet as the brief. Do not let it be bundled with the repo
  half.
- **No, not now** → then D plus A is the standing answer, and the sentence stays *"held from
  `brain_runtime`"*. That is defensible; it is simply weaker than people have been reading it as,
  which is the thing this whole row was about.
- **Not until the repos move** → a legitimate third answer. B without the repo half buys database
  separation and nothing about file writes, and you may reasonably want both or neither.

Written by T2 for task 0295, 2026-08-19. No part of B is implemented.
