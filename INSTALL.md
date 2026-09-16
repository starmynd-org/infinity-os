# Install Infinity OS on your own server

**This is Infinity OS.** Its source is the public GitHub repository
**`starmynd-org/infinity-os`**, branch **`main`**. If you are reading a different
repository, or a different branch, you are in the wrong place and nothing below will work.

Cloning it needs **no GitHub account and no credential**. There is no tarball and no installer
script hosted anywhere. If `git clone` asks you for a password, the address is wrong: stop and
compare it with step 1 rather than looking for another copy.

You are installing: a Postgres store in a Docker container, a Flask console on
`http://127.0.0.1:3103`, a dispatch CLI, and a set of `systemd --user` units that bring all of
it back after a reboot.

---

## How to read the claims in this file

Every statement here is labelled, and the labels are not decoration.

| Label | What it means |
|---|---|
| **MEASURED** | Something was run and the output is what is quoted. The date and the host are named. |
| **READ** | Taken out of the repository's own code or configuration at `main`, quoted or paraphrased. Not executed. |
| **INFERRED** | Assembled from READ facts into a step that nobody has executed in this order on a clean machine. |
| **UNVERIFIED** | Not determined. Named anyway, rather than guessed. |

**Ledger values re-measured 2026-09-16.** The numbers in steps 5a and 5c come from the repository's
own `engine/bin/scratch-db.sh ledger` (which touches no database) run in a clean clone at `e363020`;
its run at the older `e8a67bc` reproduced this file's previous numbers exactly, as the control. The
numbers in steps 5d and 10 were read from the live store of a server installed from this guide, at
`e363020`. No migration file changed between `e363020` and `cdedcdc`. Everything else in this
paragraph describes the original assembly and is unchanged.

**The whole install sequence below is INFERRED.** It was assembled on 2026-09-15 by reading
`master` at commit `1c692cf8afdbec46340b535c983b631c16c62123`. It has **not** been executed
end to end on a clean server by anyone. What *was* executed, on a Windows host with a checkout
of that commit on 2026-09-15:

* **MEASURED:** the private repository this public tree is curated from resolved its branch to
  `1c692cf8afdbec46340b535c983b631c16c62123` on that date.
* **MEASURED:** every shell script this file tells you to run parses under `bash -n`:
  `store/bin/apply-migration.sh`, `store/bin/brain-postgres-service.sh`,
  `store/bin/brain-store-ready.sh`, `store/bin/provision-operator.sh`,
  `store/bin/provision-subscriber.sh`, `store/bin/brain-postgres-backup.sh`,
  `engine/bin/scratch-db.sh`, `systemd/install.sh`, `web/bin/console`.
* **MEASURED:** `engine/bin/scratch-db.sh ledger` runs with no database at all and prints
  `74 versions read, 1..77, HOLES AT 54 55 60.` at the commit measured. That command is how step 5
  gets the list **and the expected values** for whatever release you cloned, rather than from
  numbers typed into this file.
* **MEASURED:** `store/bin/apply-migration.sh` reads a file's ledger version with the regex
  `VALUES *\(([0-9]+),`, and that reading agrees with the version the file actually records in
  `brain.schema_migration` on **all 54** files. It will not mis-report one as already applied.
* **MEASURED:** of those 54 files, exactly **one** — `migrations/0002_roles.sql` — needs psql
  variables passed in, which is why step 5 applies it with its own command.

Nobody has stood a clean VPS up from this file. Read step 5 and the "What is not covered"
section before you decide whether that matters to you.

---

## Prerequisites

| Thing | Requirement | Label |
|---|---|---|
| OS | Linux, with **systemd as pid 1**. `systemd/install.sh` refuses to run otherwise: it checks `ps -p 1 -o comm=` and exits naming the problem. | READ |
| Privileges | A **normal user account**, not root. Supervision here is `systemd --user` and there is deliberately no sudo path in any unit. You do need root (or an existing setup) *once*, to install Docker and Python. | READ |
| Docker | An engine this user can reach **without sudo** (`docker info` must succeed as that user). `store/bin/brain-docker-wait.sh` polls `docker info` and refuses to pretend the store started. | READ |
| Postgres | **None on the host.** Postgres runs as the container image **`postgres:16.10-alpine`**, named `brain-postgres`, bound to `127.0.0.1:5432`. There is no host `psql` and the repo assumes there is none: every SQL path in it runs `psql` *inside* the container. | READ |
| Python | `python3` with **Flask** and **psycopg2**. The only two third-party imports on any runtime path. | READ |
| Python version | The tree's compiled bytecode on the authoring host is `cpython-312`, so it is known to run under **CPython 3.12**. There is no `requirements.txt`, no `pyproject.toml` and no declared minimum anywhere in the repository. **Install 3.12 if you have the choice.** The true minimum is **UNVERIFIED**. | READ + UNVERIFIED |
| Where Python lives | Two `systemd` units hard-code **`/usr/bin/python3`** (`brain-paging.service`, `brain-routine-tick.service`). If you install Flask and psycopg2 into a virtualenv instead of the system interpreter, those two units will fail and the console will not. | READ |
| Filesystem | The Postgres data directory must be on a **native Linux filesystem**. `brain-postgres-service.sh` refuses a data path under `/mnt/c` or `/mnt/<letter>` outright, because drvfs does not guarantee atomic rename. On a normal server this is automatic. | READ |
| Disk | **UNVERIFIED.** The repo records no figure. What consumes space: the Postgres image, `~/.brain-postgres/data`, and `~/.brain-postgres/backups`, which `brain-postgres-backup.sh` prunes to **30 days** of compressed `pg_dump` files. Budget generously; nobody has measured it. |
| Memory | **UNVERIFIED.** The repo records no figure. A Postgres 16 container plus one Flask process plus one listener is modest, but that sentence is an expectation and not a measurement. |
| Network | **Outbound** to github.com and to your Docker registry. **No inbound ports are needed or wanted** — see step 9. |

---

## Step 0. Make the user this install runs as

**Skip this only if you are already logged in as a normal, non-root user.** On a freshly created
cloud server you are almost certainly `root` and no other account exists — and everything below
runs as a normal user, because supervision here is `systemd --user` and no unit has a sudo path.

MEASURED 2026-09-15 on a stock Hetzner CPX22 running Ubuntu: the only account you get is `root`.
This step was missing from the first version of this file, which required a normal user and never
said how to get one.

**As root:**

```bash
adduser --disabled-password --gecos "" infinity
usermod -aG sudo infinity
# The account has no password, so sudo has nothing to authenticate against and would refuse
# with "a terminal is required to authenticate". This user authenticates by SSH key, which is
# the same pattern every cloud image's default user uses. MEASURED 2026-09-15: without this
# line, step 2's very first command fails.
printf '%s ALL=(ALL) NOPASSWD:ALL\n' infinity > /etc/sudoers.d/90-infinity
chmod 440 /etc/sudoers.d/90-infinity
visudo -c -f /etc/sudoers.d/90-infinity
install -d -m 700 -o infinity -g infinity /home/infinity/.ssh
cp /root/.ssh/authorized_keys /home/infinity/.ssh/authorized_keys
chown infinity:infinity /home/infinity/.ssh/authorized_keys
chmod 600 /home/infinity/.ssh/authorized_keys
loginctl enable-linger infinity
loginctl show-user infinity --property=Linger
```

**Expect:** the last line prints `Linger=yes`. If it prints `Linger=no`, stop and fix it — without
linger your user's service manager is torn down with your last SSH session, so **the whole install
dies when you log out** and comes back to nothing after a reboot. `systemd/install.sh` in step 8
tries to set this too, and says so if it fails; setting it here means you find out now rather than
seven steps later.

**Then log out and log back in as that user** — `ssh infinity@<your-server>` — and do everything
from step 1 onward as `infinity`. Do not `su` to it: `su` gives you the user without a login
session, and `systemctl --user` needs the session.

**Check you are who you think you are before continuing:**

```bash
whoami
echo "$HOME"
systemctl --user is-system-running 2>&1 | head -1
```

**Expect:** `infinity`, `/home/infinity`, and a word from the third command that is not
`Failed to connect to bus` — that error means you are in a session without a user manager, which
is what `su` produces and what step 8 will fail on.

## Step 1. Clone it

```bash
export REPO="$HOME/infinity-os"
git clone https://github.com/starmynd-org/infinity-os.git "$REPO"
cd "$REPO"
git checkout main
git rev-parse HEAD
git log -1 --format='%h %ad %s' --date=short
```

**Expect:** a clone that completes without prompting for a password, and `git rev-parse HEAD`
printing a 40-character SHA: the newest commit on `main`.

**If `git clone` prompts for a username and password**, the address is mistyped: this repository
is public and needs no credential. Compare it with step 1 character by character. Do not look for
the source anywhere else — anything that claims to be a copy is not this product.

The clone location is yours to choose. `$HOME/infinity-os` is used throughout this file.
`systemd/install.sh` bakes the path you clone into into the unit files at install time, so if
you move the clone later you must re-run that script.

## Step 2. Install what it needs

The exact package names are your distribution's. On Debian or Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y git curl python3 python3-pip docker.io
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

`apt-get` may end with `*** System restart required ***` and a list of services it deferred. You
can carry on without rebooting; a reboot at any later point is safe once step 10 passes (MEASURED
2026-09-16: after `systemctl reboot` the store and console came back on their own in under 90
seconds, same commit, terminal still off).

Then **log out and back in** so your shell picks up the `docker` group — **and restart your user
service manager as well, which logging out does not do.**

```bash
sudo loginctl terminate-user "$(id -un)"     # this disconnects you; reconnect afterwards
```

Reconnect, then check **both** places, because they can disagree:

```bash
docker info > /dev/null && echo "docker reachable in the shell as $(id -un)"
systemd-run --user --wait --pipe --quiet docker info > /dev/null \
  && echo "docker reachable inside the user manager"
```

**Expect both lines.** If the first prints and the second does not, do not continue — every unit
in step 9 will fail on docker while docker works perfectly every time you check it by hand.

**MEASURED 2026-09-15, and this is an interaction between two steps that are each correct
alone.** Step 0 enables lingering, which is right: without it your units die with your SSH
session. But lingering means the `systemd --user` manager **is never torn down**, so it keeps
the group list it was started with — and it was started when you first logged in, before
`usermod -aG docker`. Logging out and back in gives your *shell* the new group and leaves the
*manager* stale, permanently.

Measured on the box, with a control: `docker info` exited **0** in the shell and **1** inside the
user manager, while `/bin/true` exited 0 in both — so the manager was running and only docker was
refused. `brain-store.service` then failed with `docker engine did not answer within 180s.
Refusing to pretend the store started`, which is the script being honest about a condition that
looks impossible from the command line. After `loginctl terminate-user`, docker inside the
manager exited 0.

Install the two Python libraries into the interpreter at `/usr/bin/python3` — not a virtualenv,
because two systemd units name that path literally:

```bash
/usr/bin/python3 -m pip install --break-system-packages flask psycopg2-binary
/usr/bin/python3 -c "import flask, psycopg2, importlib.metadata as m; print('flask', m.version('flask'), '| psycopg2 ok')"
```

**Expect:** a line naming a Flask version and `psycopg2 ok`.

**MEASURED 2026-09-15**, stock Ubuntu 26.04 on a Hetzner CPX22: `python3` is **3.14.4**, and
`flask 3.1.3` and `psycopg2-binary` both installed and imported with no error. So the
prerequisites table's "install 3.12 if you have the choice" is cautious rather than required —
**3.14 works.** The table's UNVERIFIED minimum stays unverified; what is now measured is one
working upper end.

**This check used to read `flask.__version__`, and that was a trap with a date on it.** Python
emits `DeprecationWarning: The '__version__' attribute is deprecated and will be removed in
Flask 3.2` — so the guide's own verification step was written to break on the next minor release
of the thing it verifies. It now asks `importlib.metadata`, which does not.

**INFERRED, and worth knowing:** no Flask or psycopg2 version is pinned anywhere in the
repository, so "whatever pip installs today" is the only specification that exists. If the
console fails to import after this, a version skew is the first thing to suspect and there is
no lockfile to check it against.

## Step 3. Mint the store credentials

Infinity OS never keeps a password in a file it commits. Every credential is a **reference**
resolved at the point of use, out of a `0700` directory in your home with `0600` files inside
it. Five must exist before Postgres starts for the first time. Two more are minted later by
their own scripts.

```bash
umask 077
mkdir -p "$HOME/.brain-postgres-secrets"
chmod 700 "$HOME/.brain-postgres-secrets"
for ref in brain-postgres-bootstrap-superuser \
           brain-postgres-role-owner \
           brain-postgres-role-producer \
           brain-postgres-role-subscriber \
           brain-postgres-role-runtime; do
  if [ -s "$HOME/.brain-postgres-secrets/$ref" ]; then
    echo "kept   $ref"
  else
    LC_ALL=C tr -dc 'A-Za-z0-9_-' < /dev/urandom | head -c 48 \
      > "$HOME/.brain-postgres-secrets/$ref"
    chmod 600 "$HOME/.brain-postgres-secrets/$ref"
    echo "minted $ref"
  fi
done
ls -l "$HOME/.brain-postgres-secrets"
```

**Expect:** five lines reading `minted`, then a listing showing five files, each `-rw-------`
and each 48 bytes.

**READ:** the alphabet above is the URL-safe one the repo's own `provision-operator.sh` uses
(`secrets.token_urlsafe(36)`). The block is idempotent: a credential that already exists is
left alone, because rotating a password under a running console is a silent outage.

**READ, and this is the one that bit the project before:** `postgres` is the bootstrap
superuser and it is **separate from all four role logins**. The first standup made
`brain_owner` the initdb superuser, which would have made every grant in
`migrations/0002_roles.sql` decorative while still passing review. Do not collapse these five
into fewer.

## Step 4. Start the store

```bash
export REPO="$HOME/infinity-os"
"$REPO/store/bin/brain-postgres-service.sh" ensure-running
```

**Expect:** the container is created and then `127.0.0.1:5432 - accepting connections`, followed
by a line reading `schema=unreadable (migrations not applied, or the runtime secret is wrong)`.

**That `schema=unreadable` line is correct at this point and is not an error.** The database
exists and is empty. There is no schema in it yet; step 5 is what puts one there.

```bash
export REPO="$HOME/infinity-os"
"$REPO/store/bin/brain-postgres-service.sh" status
docker exec -e PGPASSWORD="$(cat "$HOME/.brain-postgres-secrets/brain-postgres-bootstrap-superuser")" \
  brain-postgres psql -U postgres -tAc "SELECT version()" -d brain
```

**Expect:** a running container line, then `PostgreSQL 16.10 ...`.

**MEASURED 2026-09-15 — this command used to omit the password and could not pass.** Written as
`docker exec brain-postgres psql -U postgres …` it prints `Password for user postgres:` and then
`fe_sendauth: no password supplied`, because the container is built with `scram-sha-256` auth and
`postgres` holds the bootstrap-superuser credential minted in step 3. In an interactive shell it
does not even fail cleanly — it **sits there prompting**. The repo's own scripts had it right all
along (`store/bin/` uses `docker exec -i -e PGPASSWORD="$SU"` in 53 `psql` invocations); this file
simply had not copied them.

**Note the absence of `-i` here, and keep it absent.** The repo's helpers use `docker exec -i`
because they pipe SQL in. A `-i` on a command inside a loop that reads from the same stdin will
consume the loop's input — which is the trap called out in step 5.

**READ:** the container is created with `--restart unless-stopped`, data at
`~/.brain-postgres/data`, checksums on, `scram-sha-256` auth, and the database is named
**`brain`** because `BRAIN_PG_DB` defaults to `brain`. Leave that name alone —
`migrations/0002_roles.sql` names the database `brain` literally in two `GRANT`/`REVOKE` lines,
so a store called anything else needs those two lines edited.

## Step 5. Apply the schema

This is the longest step and the one most likely to go wrong. Read all of it before you paste
any of it.

**READ, and it is deliberate:** `store/bin/apply-migration.sh` applies **one file** and has
**no `--all`**, because a loop that dies on the third file leaves a store nobody can describe.
On a live store that rule stands. The loop in 5c below exists for the **one** case the rule was
not written for — a brand new, empty database on a fresh server — and it stops at the first
failure and prints every verdict, which is what the rule protects. After this install, go back
to one file at a time.

**READ:** `engine/bin/scratch-db.sh` cannot help you here. It builds every other database in
this system, but it refuses the name `brain` by name at its line 71 — it is a scratch tool and
`brain` is the live store. That refusal is why this step is hand-assembled.

### 5a. Get the list, and the first file

```bash
export REPO="$HOME/infinity-os"
cd "$REPO"
"$REPO/engine/bin/scratch-db.sh" ledger \
  | awk '$1 ~ /^[0-9]+$/ && $2 ~ /\.sql$/ { print $1, $2 }' \
  > "$HOME/infinity-os-ledger.txt"
"$REPO/engine/bin/scratch-db.sh" ledger | grep 'versions read'
EXPECT_MAX="$(tail -1 "$HOME/infinity-os-ledger.txt" | awk '{print $1}')"
EXPECT_COUNT="$(wc -l < "$HOME/infinity-os-ledger.txt")"
echo "this release expects ${EXPECT_MAX}/${EXPECT_COUNT}"
head -3 "$HOME/infinity-os-ledger.txt"
tail -1 "$HOME/infinity-os-ledger.txt"
```

**Expect:** a summary line of the form `<count> versions read, 1..<max>, HOLES AT <numbers>.`, then
`this release expects <max>/<count>` carrying **the same two numbers**, then the first three lines
`1 migrations/0001_initial.sql`, `2 migrations/0002_roles.sql`,
`3 budget/schema/0003_budget.sql`, and a last line whose first number is `<max>`.

**These two numbers are the expected values for the rest of this file** (steps 5c, 5d, 9 and 10).
They are computed from the release you cloned, not typed here, because a typed number goes stale
the day a migration is added and then tells a correct install that it failed. **Keep this shell
open** so `EXPECT_MAX` and `EXPECT_COUNT` stay set; step 10 shows how to recompute them if you
reconnect.

**If the count in the summary line and `EXPECT_COUNT` differ, stop and look at the file.** The
`$2 ~ /\.sql$/` half of the filter is there for a reason: `ledger` also prints a summary line that
begins with a number and would otherwise be captured as one extra "migration" called `versions`.
**MEASURED 2026-09-15:** without that half the filter yields one line too many.

**READ:** that command touches no database. The list is read out of each file's own recorded
version, never off its filename — filename numbers are a per-directory counter and they lie.
The holes are expected and are not missing files.

Now the first migration. **It does not go through `apply-migration.sh`, and that is not a
shortcut — it is the only way it can work.**

```bash
export REPO="$HOME/infinity-os"
S="$HOME/.brain-postgres-secrets"
docker exec -i -e PGPASSWORD="$(cat "$S/brain-postgres-bootstrap-superuser")" \
  brain-postgres psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -U postgres -d brain \
    -f - < "$REPO/migrations/0001_initial.sql"
```

Then confirm the ledger now exists, because psql exiting 0 is not evidence:

```bash
docker exec -e PGPASSWORD="$(cat "$S/brain-postgres-bootstrap-superuser")" \
  brain-postgres psql -U postgres -d brain -tAc \
  "SELECT max(version) FROM brain.schema_migration"
```

**Expect:** `1`.

**MEASURED 2026-09-15 — this step used to call `apply-migration.sh` and could never have
worked.** That script reads the ledger *before* it applies anything, and dies if it cannot:

```
BEFORE="$(psql_owner -tAc 'SELECT COALESCE(max(version), 0) FROM brain.schema_migration' …)"
[ -n "$BEFORE" ] || die "could not read the ledger on '$DB'. Nothing was applied."
```

— and `brain.schema_migration` is created by `0001_initial.sql` itself, at line 707, in the
schema created at line 26. **So the tool refuses to apply the file that makes the tool usable.**
On an empty database step 5 stopped at its first command with `could not read the ledger on
'brain'. Nothing was applied.`

That refusal is correct and must not be relaxed: a migration applied with no ledger row is one
nothing can later tell was applied. What was wrong was pointing a reader at it for the bootstrap
case. **The repository already knew this** — `engine/bin/scratch-db.sh` applies "0001 and 0002
literally" rather than through the tool, which is exactly what the two blocks above now do. Every
file from ledger 3 onward goes through `apply-migration.sh` as normal, because by then the ledger
exists.

### 5b. The one file that needs its passwords passed in

`migrations/0002_roles.sql` creates the four database roles and sets their passwords from psql
variables, so that the committed file carries references and never values.
`apply-migration.sh` does not pass those variables, so this file gets its own command:

```bash
export REPO="$HOME/infinity-os"
S="$HOME/.brain-postgres-secrets"
docker exec -i -e PGPASSWORD="$(cat "$S/brain-postgres-bootstrap-superuser")" \
  brain-postgres psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -U postgres -d brain \
    -v owner_pw="$(cat "$S/brain-postgres-role-owner")" \
    -v producer_pw="$(cat "$S/brain-postgres-role-producer")" \
    -v subscriber_pw="$(cat "$S/brain-postgres-role-subscriber")" \
    -v runtime_pw="$(cat "$S/brain-postgres-role-runtime")" \
    -f - < "$REPO/migrations/0002_roles.sql"
```

Then confirm the ledger actually moved, because psql exiting 0 is not evidence it applied
anything:

```bash
docker exec -e PGPASSWORD="$(cat "$S/brain-postgres-bootstrap-superuser")" \
  brain-postgres psql -U postgres -d brain -tAc \
  "SELECT max(version) FROM brain.schema_migration"
docker exec -e PGPASSWORD="$(cat "$S/brain-postgres-bootstrap-superuser")" \
  brain-postgres psql -U postgres -d brain -tAc \
  "SELECT rolname, rolsuper, rolcanlogin FROM pg_roles WHERE rolname LIKE 'brain_%' ORDER BY 1"
```

**Expect:** `2`, then four rows — `brain_owner`, `brain_producer`, `brain_runtime`,
`brain_subscriber` — each with `rolsuper` **`f`** and `rolcanlogin` **`t`**.

**If any of those says `rolsuper = t`, stop.** A superuser role bypasses every grant in that
file and the whole least-privilege split is decorative.

### 5c. Every file after the first two

```bash
export REPO="$HOME/infinity-os"
cd "$REPO"
while read -r v f <&3; do
  [ "$v" -le 2 ] && continue
  echo "=== ledger $v : $f"
  BRAIN_DB=brain "$REPO/store/bin/apply-migration.sh" "$f"
  rc=$?
  if [ "$rc" -eq 1 ] || [ "$rc" -gt 2 ]; then
    echo "STOPPED at ledger $v ($f), exit $rc. Nothing after this was applied."
    break
  fi
done 3< "$HOME/infinity-os-ledger.txt"
```

**The `3<` and the `<&3` are load-bearing; do not simplify them to `< file`.**
`apply-migration.sh` calls `docker exec -i`, and `docker exec -i` **consumes the stdin it
inherits**. With the list on plain stdin, the first migration's ledger read would swallow the
rest of the list and the loop would silently apply one file and stop, reporting nothing wrong.
Reading the list on fd 3 keeps it out of reach.

**Expect:** `EXPECT_COUNT` minus 2 blocks, each ending `APPLIED. ledger N is now held by ...`, and **no**
`STOPPED` line. Each migration is one transaction, so a file that fails rolls its own DDL back
and leaves the store at the version before it.

**If you see `STOPPED`:** the store is at a known, consistent version — the one the last
`APPLIED` line named. Fix the cause, then re-run this same block. Files already applied exit 2
and are skipped, which is why the loop treats 2 as success.

### 5d. Confirm the schema landed

```bash
docker exec -e PGPASSWORD="$(cat "$HOME/.brain-postgres-secrets/brain-postgres-bootstrap-superuser")" \
  brain-postgres psql -U postgres -d brain -tAc \
  "SELECT max(version) || ' / ' || count(*) FROM brain.schema_migration"
echo "expected ${EXPECT_MAX} / ${EXPECT_COUNT}"
```

**Expect:** the two lines show exactly the same pair.

The two numbers differ because of the holes, and both must be right: the right maximum over too
few rows means files are missing in the middle, which is a store that reads finished and is not.

## Step 6. Provision the operator login

The console writes as **you**, not as the application. That takes a Postgres login of your own,
mapped to a human, and it does not exist until you make it.

```bash
export REPO="$HOME/infinity-os"
"$REPO/store/bin/provision-operator.sh" --db brain
```

**Expect:** `provisioned operator as brain_operator on brain (minted a new password, secret ref
brain-postgres-role-operator)`, then a table showing `brain_operator | operator`, then a
credential report from `secret-preflight.py`.

**READ:** this script is the half of migration 20 that cannot be committed — it does the
`CREATE ROLE ... PASSWORD` and writes the value into your secret directory. It is idempotent
and will not rotate an existing password unless you pass `--rotate`. Without it, the console
renders and **every write returns an error**, because `web/actions.py` opens the operator
connection on every write.

Then the listener's own login:

```bash
export REPO="$HOME/infinity-os"
"$REPO/store/bin/provision-subscriber.sh" --db brain --subscriber operator-paging
```

**Expect:** a line naming `brain_sub_operator_paging` and the secret reference
`brain-postgres-subscriber-operator-paging`.

**READ, and this is the exact mistake a previous host migration made:** provisioning only the
five step-3 credentials leaves this one absent, the paging listener cannot authenticate at all,
and nothing else looks wrong. Do not skip it.

## Step 7. Check every credential resolves

```bash
export REPO="$HOME/infinity-os"
"$REPO/store/bin/secret-preflight.py" --db brain
echo "preflight exit: $?"
```

**Expect:** `preflight exit: 0`, and no reference listed as missing.

**READ:** this script carries no hard-coded count. It derives the required set three ways — from
the code's role list, from the shell scripts that resolve a reference, and from the login
mappings in the database itself — precisely because a number written in one place and read in
three is what gets trusted without being counted. **A non-zero exit here names what is missing.
Fix it before step 8**; a missing credential fails closed at the point of use, which is later
and harder to read.

## Step 8. Install the supervision

```bash
export REPO="$HOME/infinity-os"
"$REPO/systemd/install.sh"
```

**Expect:** thirteen `installed /home/<you>/.config/systemd/user/brain-*.service|timer` lines, a
`linger: Linger=yes` line, and then an `enabled.` block printing the start command.

**If it says `pid 1 is not systemd`**, this host cannot run Infinity OS's supervision as shipped.

**If `enable-linger` fails**, the script says so and continues — but the units will **not**
survive a reboot, because without linger the user manager is torn down with your last SSH
session. On a server that defeats the point. Fix it before going further.

**READ: the n8n units are not installed.** `brain-n8n.service` starts n8n through a launcher
that lives in a **different repository** you do not have, and `brain-n8n-failwatch` watches that
n8n. `install.sh` installs and enables them only when run with `INSTALL_N8N=1`, which you should
not set. Nothing else in this install depends on them. (Before this change `install.sh` enabled
them unconditionally, and this step told you to disable them by hand. If you installed from an
earlier commit, `systemctl --user disable --now brain-n8n.service brain-n8n-failwatch.timer`
still applies to you.)

## Step 9. Start it

**Do this first, before you start anything.** With a loopback bind and no `CONSOLE_ORIGIN` set,
the console registers a **browser terminal** — a pty over websockets. **Anything that can reach
port 3103 on that server gets a shell as your user**, and the window in which that is true opens
the moment `brain-console.service` starts. Turning it off afterwards is a window you did not have
to open. `CONSOLE_TERMINAL=off` stops those endpoints being registered at all, rather than merely
hiding them.

```bash
mkdir -p ~/.config/systemd/user/brain-console.service.d
printf '[Service]\nEnvironment=CONSOLE_TERMINAL=off\n' \
  > ~/.config/systemd/user/brain-console.service.d/no-terminal.conf
systemctl --user daemon-reload
systemctl --user show brain-console.service -p Environment
```

**Expect:** one line containing **both** `CONSOLE_TERMINAL=off` **and** `BRAIN_PG_DB=brain`. The
second is written by the unit file itself, so seeing it proves you are reading the console's own
environment and not an empty answer. If `BRAIN_PG_DB=brain` is there and `CONSOLE_TERMINAL=off` is
not, stop — the drop-in did not take, and starting the console now would expose a shell. **If you deliberately
want the browser terminal**, skip this block; it is a real feature, and the point here is that you
choose it rather than receive it.

Now start:

```bash
systemctl --user start brain-store.service brain-console.service brain-paging.service \
                       brain-health.timer brain-transcript-verify.timer \
                       brain-transcript-backfill.timer brain-routine-tick.timer \
                       brain-intake-sweep.timer
sleep 10
systemctl --user list-units 'brain-*'
curl -s http://127.0.0.1:3103/api/health
curl -s http://127.0.0.1:3103/terminal | grep -c 'the terminal pane is switched off'
```

**Expect:** `brain-store.service` and `brain-console.service` both `active`, a JSON body
containing `"server":"up"` and `"schema_version":<EXPECT_MAX>`, and a final `1`, which is the
running console itself saying its terminal is off — see step 10 for the check that either
passes or fails by name.

**Three units will show as failed or restarting in that list, and on a fresh install that is
expected.** MEASURED 2026-09-16 on a clean Hetzner CPX22, Ubuntu 24.04, by the rehearsal of this
file:

| Unit | What its journal says | Why | Affects the console? |
|---|---|---|---|
| `brain-paging.service` (`activating auto-restart`, then `failed`) | `StartupRefused: no subscriber declaration at /mnt/c/Users/.../departments/SUBSCRIBERS.md` | The paging listener looks for its declaration in the operator's own brain checkout, which this install does not have | No. Nothing pages you; the console works |
| `brain-health.service` (`failed`) | `paging HELD no cursor row` | It reports the paging listener above as not running | No |
| `brain-transcript-backfill.service` (`failed`) | `backfill-sweep UNAVAILABLE: ingest coverage failed` | Step 5 does not install the `ingest` schema (see "What is not covered") | No |

Any **other** unit failing is not expected: `journalctl --user -u <unit> --no-pager -n 30` shows why.

**The console binds `127.0.0.1:3103` and only that.** There is no authentication, no TLS and no
login page. **Do not** set `CONSOLE_HOST=0.0.0.0` to "make it reachable" — that publishes an
unauthenticated console, and the repo's own phone-reach check calls a wildcard bind a defect.

To open it from your laptop, tunnel to it. **`MAC-ACCESS.md` walks this step by step for a Mac**,
including making an SSH key and a shortcut. In short, on **your laptop**, not the server:

```bash
ssh -N -L 13103:127.0.0.1:3103 <you>@<your-server>
```

Leave that running, then open **<http://127.0.0.1:13103/queue?tier=decide>** in your browser.

**The browser terminal, and why step 9 turned it off before starting anything.** With a loopback
bind and no `CONSOLE_ORIGIN` set, the console registers a pty over websockets: anything that can
reach port 3103 on that server gets a shell as your user. The drop-in at the top of step 9 stops
those endpoints being registered at all, rather than merely hiding them. **This paragraph used to
sit here, after the start command.** That ordering meant a reader following the file top to bottom
ran an unauthenticated shell on their own server for as long as it took them to read this far —
the fix existed and arrived too late to be one. If you want the terminal, remove the drop-in and
restart: `rm ~/.config/systemd/user/brain-console.service.d/no-terminal.conf && systemctl --user
daemon-reload && systemctl --user restart brain-console.service`.

## Step 10. Verification — three named checks

Run all three. Each one passes or fails by name; none of them is "it looks fine".

```bash
export REPO="$HOME/infinity-os"
cd "$REPO"
EXPECT_MAX="$(tail -1 "$HOME/infinity-os-ledger.txt" | awk '{print $1}')"
EXPECT_COUNT="$(wc -l < "$HOME/infinity-os-ledger.txt")"
echo "--- expected ${EXPECT_MAX}/${EXPECT_COUNT}, commit $(git rev-parse --short HEAD)"

echo "--- V1 schema ledger"
docker exec -e PGPASSWORD="$(cat "$HOME/.brain-postgres-secrets/brain-postgres-bootstrap-superuser")" \
  brain-postgres psql -U postgres -d brain -tAc \
  "SELECT max(version) || '/' || count(*) FROM brain.schema_migration"

echo "--- V2 console health"
curl -s http://127.0.0.1:3103/api/health

echo "--- V3 the CLI can read the store as you"
python3 engine/bin/swarm whoami
python3 engine/bin/swarm status
```

**V1 PASSES** when it prints exactly the expected pair from the first line. Any other pair is a
partial schema; go back to step 5c. **This is the check that catches a store which reads finished
and is not.**

**V2 PASSES** when the JSON contains `"server":"up"`, `"schema_version":<EXPECT_MAX>`, and
`"commit_at_start":"<the commit on the first line>"`. The commit proves the running console is the
code you cloned; use `commit_at_start` for that, never `python_stale`. **The same JSON also
carries `"stale":"yes"` on a console that started seconds ago; that field does not decide V2.**
MEASURED 2026-09-16 on a fresh install, 9 seconds after start. It is served
through the `brain_runtime` login, so a pass here proves the app's own credential resolves and
connects — not just that Flask is listening. **V2 FAILS** if curl returns nothing (the console
is not up: `journalctl --user -u brain-console` is the place to look) or if the body is an
error (the runtime credential or the schema is wrong).

**V3 PASSES** when `whoami` names `operator` as the human this connection is, and `status` prints
a fleet table rather than a traceback. **V3 FAILS** with a credential error if step 6 was
skipped — that is the specific failure step 6 exists to prevent.

If all three pass, Infinity OS is installed and running, it will come back after a reboot, and
`http://127.0.0.1:3103/queue?tier=decide` through your tunnel is the surface you use.

**Next:** `MAC-ACCESS.md` to reach the console from your laptop, and `UPDATING.md` when a new
release is published.

---

## What is not covered

This section is the counterweight to everything above. A guide that describes an aspiration is
worse than no guide.

**Nobody has run this file on a clean server.** Every step is assembled from code read at
`master` on 2026-09-15. The commands that were actually executed are listed at the top of this
file, and they are static checks — parses, ledger reads, a remote resolve — not an install.
Expect to debug. If a step fails, the failure is probably in this document and not in you.

**No TLS, no authentication, no users.** The console has no login page and no password. Its
only boundary is that it binds loopback. Everything in step 9 about tunnelling is the security
model, not a convenience.

**No reverse proxy, no public exposure, no domain.** Putting a proxy in front of this needs
`CONSOLE_ORIGIN` set to the exact origin the browser's address bar shows, scheme included,
because the write door compares against it and a mismatch 403s every write while the page still
renders perfectly. That configuration is documented in `web/host.py` and is **not covered here**.

**No firewall configuration, no OS hardening, no unattended upgrades.** None of it. Port 5432 is
published to `127.0.0.1` only by the container, and port 3103 likewise, but nothing here checks
your firewall.

**Backups exist but are not scheduled by this install.** `store/bin/brain-postgres-backup.sh`
gives you `backup`, `verify`, `list` and `restore`, prunes to 30 days, and restores only into a
**named scratch database, never over live**. No timer runs it. Scheduling it is yours.

**Updates follow `UPDATING.md`, not `git pull`.** It fetches, fast-forwards only, applies new
migrations through `apply-migration.sh` one verdict at a time, restarts, re-checks the browser
terminal is off, and proves the new commit with `commit_at_start`. Do not improvise a
`git pull`: it skips the migrations and the checks.

**Shipped units that are not installed, or are known to fail, independent of anything you do:**

* `brain-n8n.service` and `brain-n8n-failwatch.timer` point at a **different repository** you do
  not have. Step 8 does not install them. READ.
* `brain-transcript-verify.service` is recorded in `docs/KNOWN-GAPS.md` as failing on every run
  on the authoring host, because `ingest/schema/0006_absence_parent_age_basis.sql` had never been
  applied there. **On your install it may behave differently and this file does not know:** the
  `ingest/` schema files record **no ledger version at all** (MEASURED), so
  `apply-migration.sh` refuses them by design and step 5 does **not** apply them. Your store
  therefore has no `ingest` schema. What that breaks, and whether transcript verification is
  something you need, is **UNVERIFIED** here. Read `docs/KNOWN-GAPS.md`.

**The `ingest`, `voice`, `fabric`, `mcp` and `adapter` subsystems are not
installed or configured by this file.** They are in the tree. Nothing above turns them on and
nothing above has checked what they need.

**Everything in `docs/KNOWN-GAPS.md` applies to your install too.** It is a real list of real
defects with real measurements — the Fleet room renders zero write controls, nothing is
announced to a screen reader, six verb labels fail WCAG AA contrast. It is current as of
2026-08-27 and it is honest. Read it before you decide this is finished software.

**Secrets live in one `0700` directory owned by one Unix user.** Any process running as that
user can read all of them, the bootstrap superuser credential included. On a single-user server
that is the whole separation there is. `store/SECRETS.md` states this residual plainly and at
length; it is a known property, not an oversight, and it is not fixed by this install.

---

Also here: `README.md` for what this is, `docs/OPERATING.md` for running it day to day,
`docs/WHY-IT-IS-LIKE-THIS.md` for why it behaves this way, `docs/CHANGING-IT.md` for editing it,
`docs/KNOWN-GAPS.md` for what is missing, `MAC-ACCESS.md` to reach the console from a Mac, and
`UPDATING.md` to move to a newer release.
