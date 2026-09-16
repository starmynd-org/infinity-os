# Infinity OS (Claude Code orientation)

You are in a checkout of **Infinity OS**, most likely on the server of the person you are helping.
`README.md` says what it is. This file is about how to help without breaking their install.

## Read first, for the job you were given

| Job | Read |
|---|---|
| install it | `INSTALL.md`, top to bottom, running each step and checking its **Expect** line |
| update an install | `UPDATING.md`. Never improvise a `git pull` |
| reach the console | `MAC-ACCESS.md` |
| understand a behaviour | `docs/WHY-IT-IS-LIKE-THIS.md`, then `docs/KNOWN-GAPS.md` |
| change the code | `docs/CHANGING-IT.md` and `web/MUST-NOT-BUILD.md` |

## Rules that protect the person's install

1. **Stop and show the output when a step's result differs from its Expect line.** Do not reach for
   `git stash`, `git reset --hard`, `git checkout --`, `--force` or deleting files to get past a
   refusal. A stopped step loses nothing; those can.
2. **Never expose the console.** It has no login and no HTTPS. Do not set `CONSOLE_HOST=0.0.0.0`,
   open port 3103 in a firewall, or put a proxy in front of it. The SSH tunnel is the only door.
3. **Keep the browser terminal off** unless the person asks for it: `CONSOLE_TERMINAL=off`, read back
   with `systemctl --user show brain-console.service -p Environment`.
4. **Freshness is `commit_at_start`** from `GET /api/health`, compared with `git rev-parse HEAD`.
   Do not use `python_stale` to decide whether the console runs the code in the folder.
5. **Migrations go through `store/bin/apply-migration.sh`, one file at a time**, as `INSTALL.md`
   and `UPDATING.md` show, and only on the person's say-so. Take a backup first.
6. **Never print, paste or commit a credential.** Every credential is a file under
   `~/.brain-postgres-secrets`, read at the point of use.

## Measuring without being wrong

- A verdict over an empty set is not a pass: print the denominator.
- Never pipe a test runner through `head` or `tail`; the exit code becomes the pipe's.
- A command's exit code is not the result; read the output.

## Mirror rule

`AGENTS.md` is the same file for other coding agents. The two are edited together.
