# Update Infinity OS on your own server

This moves an install made with `INSTALL.md` to a newer release, **without reinstalling and without
touching your data**. Your data lives in `~/.brain-postgres`, outside the repository, and nothing
here writes to it except the schema migrations the new release brings.

Run every command **on the server, as the install user** (`infinity` if you followed `INSTALL.md`),
over a normal `ssh infinity@<your-server>` login. Not as `root`, and not through `su`.

**The rule for the whole file:** every step says what you should see. If you see something else,
**stop and send the command and its full output** to whoever supports your install. Do not try
`git stash`, `git reset`, `git checkout --`, `--force`, or deleting files to get past a refusal. Each
of those can lose something that a stopped update never loses.

It takes about ten minutes. The console is unavailable for a few seconds during step 6.

---

## Step 1. Write down where you are now

```bash
export REPO="$HOME/infinity-os"
cd "$REPO"
git rev-parse --short HEAD | tee "$HOME/infinity-os-update-from.txt"
curl -s http://127.0.0.1:3103/api/health | grep -o '"commit_at_start":"[^"]*"'
systemctl --user --failed --no-legend | tee "$HOME/infinity-os-failed-before.txt"
```

**Expect:** a short commit id, then `"commit_at_start":"` followed by the same id, then a list of
failed units (it may be empty). The id is the release you are updating **from**; it is saved in
`~/infinity-os-update-from.txt`, and the failed list in `~/infinity-os-failed-before.txt`, so later
steps can use them even if you reconnect.

If the two ids differ, the console is not running the code in your folder. That is worth knowing
before you change anything; send both lines and stop.

**Freshness is `commit_at_start`.** The health endpoint also prints a field called `python_stale`.
Do not use it for this question: it can read `no` on a console that is several releases old.

## Step 2. Take a backup

```bash
"$REPO/store/bin/brain-postgres-backup.sh" backup
"$REPO/store/bin/brain-postgres-backup.sh" list
```

**Expect:** `wrote /home/infinity/.brain-postgres/backups/brain-<date>.dump (<n> bytes)`, and that
file in the list. Migrations only move forward; this dump is the way back if one goes wrong.

## Step 3. Check nothing in the folder was changed locally

```bash
git status --porcelain
```

**Expect: no output at all.**

**If it prints any line, stop here.** A changed or untracked file inside the repository makes the
fast-forward in step 4 refuse, or carry your change silently. Send the output. Do not discard,
stash or delete anything to make it go away; one of those files may be the only copy of something.

## Step 4. Fetch the new release and fast-forward to it

```bash
git fetch origin
git log --oneline HEAD..@{upstream} | head -20
git log --oneline HEAD..@{upstream} | wc -l
git merge --ff-only @{upstream}
git rev-parse --short HEAD
```

**Expect:** the first `log` lists the commits you are about to receive and the count is above 0;
then `Fast-forward` and a file list; then the new commit id. Write it down: it is the release you
are updating **to**.

- **The count is `0`:** you are already on the newest release. Stop; there is nothing to update.
- **`fatal: Not possible to fast-forward, aborting.`:** your copy and the published one have gone
  different ways. Nothing was changed. **Stop and send the output.** Never reach for `--force`,
  `git reset --hard` or `git pull --rebase` here: those overwrite history in your folder, and the
  person helping you needs to see it as it is.
- **`git fetch` asks for a username or password:** your server cannot reach the repository it was
  cloned from. Stop and send the output.

`@{upstream}` means "the branch this folder was cloned from", so this file does not need to know
the branch name.

## Step 5. Apply any new schema migrations

First, work out what the new release expects. This reads the migration files and touches no
database:

```bash
"$REPO/engine/bin/scratch-db.sh" ledger | grep 'versions read'
"$REPO/engine/bin/scratch-db.sh" ledger \
  | awk '$1 ~ /^[0-9]+$/ && $2 ~ /\.sql$/ { print $1, $2 }' \
  > "$HOME/infinity-os-ledger.txt"
EXPECT_MAX="$(tail -1 "$HOME/infinity-os-ledger.txt" | awk '{print $1}')"
EXPECT_COUNT="$(wc -l < "$HOME/infinity-os-ledger.txt")"
echo "this release expects ${EXPECT_MAX}/${EXPECT_COUNT}"
```

**Expect:** a line of the form `<count> versions read, 1..<max>, HOLES AT <numbers>.`, then
`this release expects <max>/<count>` with the same two numbers. **These numbers belong to the
release, so this file does not print them for you**; your server computes them from the files it
just received. The holes are expected and are not missing files.

Then apply. Every migration you already have answers `ALREADY APPLIED` and is skipped; only new
ones are applied, one file at a time, each checked against the ledger:

```bash
cd "$REPO"
while read -r v f <&3; do
  echo "=== ledger $v : $f"
  BRAIN_DB=brain "$REPO/store/bin/apply-migration.sh" "$f"
  rc=$?
  if [ "$rc" -eq 1 ] || [ "$rc" -gt 2 ]; then
    echo "STOPPED at ledger $v ($f), exit $rc. Nothing after this was applied."
    break
  fi
done 3< "$HOME/infinity-os-ledger.txt"
```

**Expect:** one block per ledger line. Old ones end `ALREADY APPLIED: ledger N is held by ...
Nothing to do.` New ones end `APPLIED. ledger N is now held by ...`. **No `STOPPED` line.**

- **Exit 2 is success here**: it means "already applied". That is why the loop only stops on 1.
- **The `3<` and `<&3` must stay as they are.** The migration tool uses `docker exec -i`, which
  swallows whatever is on normal input. With the list on normal input the loop applies one file and
  ends quietly, reporting nothing wrong.
- **If you see `STOPPED`:** each migration is one transaction, so the store is at the version the
  last `APPLIED` line named, and nothing is half-applied. Do not restart anything yet. Send the whole
  output.

Confirm the store now matches what the release expects:

```bash
docker exec -e PGPASSWORD="$(cat "$HOME/.brain-postgres-secrets/brain-postgres-bootstrap-superuser")" \
  brain-postgres psql -U postgres -d brain -tAc \
  "SELECT max(version) || '/' || count(*) FROM brain.schema_migration"
echo "expected ${EXPECT_MAX}/${EXPECT_COUNT}"
```

**Expect:** the two lines show the same pair.

## Step 6. Refresh the service definitions and restart

Only if the release changed the service files, re-install them. This prints the list of changed
service files, from the release you saved in step 1 to the one you now have:

```bash
export REPO="$HOME/infinity-os"
cd "$REPO"
git diff --name-only "$(cat "$HOME/infinity-os-update-from.txt")" HEAD -- systemd/
```

**If that printed nothing**, skip to "Restart" below.

**If it printed any file**, re-install the units. Then make sure the two `brain-n8n` units are off:
installs made from older releases have them, and older installers switched them back on every time
they ran; newer releases do not install them at all, so the loop only acts on a unit that exists:

```bash
"$REPO/systemd/install.sh"
for u in brain-n8n.service brain-n8n-failwatch.timer; do
  if systemctl --user cat "$u" > /dev/null 2>&1; then
    systemctl --user disable --now "$u"; echo "turned off $u"
  else
    echo "not installed: $u"
  fi
done
```

**Expect:** `installed ...` lines, `linger: Linger=yes`, the `enabled.` block, then for each of the
two units either `turned off` or `not installed`. Both are fine. On an install made from an older
release the installer may also print `brain-n8n.service is from an earlier install and was left as
it is`; that is expected, and the loop above is what turns it off.

**Restart:**

```bash
systemctl --user daemon-reload
systemctl --user reset-failed
systemctl --user restart brain-console.service brain-paging.service
sleep 10
systemctl --user is-active brain-store.service brain-console.service
```

`reset-failed` clears the "failed" mark on units that gave up before the update. Without it, a unit
that had already failed several times in a row can refuse the restart (`Start request repeated too
quickly`), and the new code never runs. It changes nothing else. Step 8 still compares against the
failed list you saved in step 1.

**Expect:** `active` twice. The paging listener is restarted too but is not part of this check,
because on a user's install it is normally skipped (`INSTALL.md` step 9); step 8 covers it. If the
restart line prints `Job for brain-paging.service failed`, that is also covered by step 8, not here.

## Step 7. Prove the browser terminal is still off

`INSTALL.md` step 9 switched off the console's browser terminal with a small override file. **Your
SSH tunnel arrives at the server as a local connection, so if the terminal were on, anyone using
the tunnel would get a shell on your server.** Check it survived the update, two ways:

```bash
systemctl --user show brain-console.service -p Environment
curl -s http://127.0.0.1:3103/terminal | grep -c 'the terminal pane is switched off'
```

**Expect:** the first line contains **both** `CONSOLE_TERMINAL=off` **and** `BRAIN_PG_DB=brain`
(the second one proves you are reading the right service's settings); the second command prints
`1`.

**If `CONSOLE_TERMINAL=off` is missing, or the count is `0`: close your tunnel now**, then run:

```bash
mkdir -p ~/.config/systemd/user/brain-console.service.d
printf '[Service]\nEnvironment=CONSOLE_TERMINAL=off\n' \
  > ~/.config/systemd/user/brain-console.service.d/no-terminal.conf
systemctl --user daemon-reload
systemctl --user restart brain-console.service
```

and repeat this step until both checks pass.

## Step 8. Prove the update worked: three checks

The first two lines recompute the expected pair from step 5's file, so this still works if you
reconnected since then.

```bash
export REPO="$HOME/infinity-os"; cd "$REPO"
EXPECT_MAX="$(tail -1 "$HOME/infinity-os-ledger.txt" | awk '{print $1}')"; EXPECT_COUNT="$(wc -l < "$HOME/infinity-os-ledger.txt")"
echo "--- commit (updated from $(cat "$HOME/infinity-os-update-from.txt"))"
git rev-parse --short HEAD
curl -s http://127.0.0.1:3103/api/health | grep -o '"commit_at_start":"[^"]*"'

echo "--- V1 schema ledger (expected ${EXPECT_MAX}/${EXPECT_COUNT})"
docker exec -e PGPASSWORD="$(cat "$HOME/.brain-postgres-secrets/brain-postgres-bootstrap-superuser")" \
  brain-postgres psql -U postgres -d brain -tAc \
  "SELECT max(version) || '/' || count(*) FROM brain.schema_migration"

echo "--- V2 console health (expected schema_version ${EXPECT_MAX})"
curl -s http://127.0.0.1:3103/api/health

echo "--- V3 the CLI can read the store as you"
python3 engine/bin/swarm whoami
python3 engine/bin/swarm status
```

The update **passed** when all of these hold:

| Check | Passes when |
|---|---|
| **commit** | `commit_at_start` equals the new id from `git rev-parse --short HEAD`, **and differs from the "updated from" id** |
| **V1** | prints exactly the expected pair |
| **V2** | the JSON contains `"server":"up"` and `"schema_version":<the expected max>` |
| **V3** | `whoami` names `operator`, and `status` prints a table, not a traceback |

**If `commit_at_start` still shows the old id**, the console did not restart onto the new code, even
if `systemctl` said it did. Run `systemctl --user restart brain-console.service`, wait ten seconds,
and check again. A restart command finishing is not proof that the running process changed.

Then check nothing new has failed:

```bash
systemctl --user --failed --no-legend > "$HOME/infinity-os-failed-after.txt"
comm -13 <(sort "$HOME/infinity-os-failed-before.txt") <(sort "$HOME/infinity-os-failed-after.txt") \
  > "$HOME/infinity-os-failed-new.txt"
if [ -s "$HOME/infinity-os-failed-new.txt" ]; then cat "$HOME/infinity-os-failed-new.txt"; else echo "no newly failed units"; fi
```

**Expect:** `no newly failed units`. A unit that was failing before and is fine now is not listed;
that is an update fixing something. Any unit that **is** listed failed after the update; send it,
**except** `brain-health.service` or `brain-transcript-backfill.service`: those two fail or
clear depending on when their timers last ran (see `INSTALL.md` step 9), so a line for either of them
alone is not a sign the update went wrong.

---

## If something went wrong

- **Stopped before step 4 finished:** nothing changed. Send the output.
- **Stopped in step 5:** the new code is in the folder and the store is at a known version. The
  console is still the old process until step 6, so it keeps working. Send the output; do not
  restart.
- **A check failed in step 8:** leave it running and send the output of step 8 and of
  `journalctl --user -u brain-console --since "15 min ago" --no-pager | tail -50`.
- **Going back to the old release is not a self-service step.** Code can be moved back, but
  migrations only go forward, and older code on a newer schema has not been tested. Your step 2
  backup is what makes a supported rollback possible.

## What this file does not cover

- Updating the operating system, Docker or Python.
- Anything you edited inside the repository folder. Step 3 stops the update when it finds such a
  change.
- A published history that was rewritten. The update is a fast-forward and refuses rather than
  guessing.
