# Brief quality checklist

Run a brief against this before you post it. A brief is read once, by a stranger, who cannot
ask you what you meant. If any answer below is no, fix the brief instead of posting it.

Every brief carries all four sections: `## Objective`, `## Context`, `## Definition of done`,
`## Report back`.

## 1. Done is verifiable

- [ ] Done is a check someone can run, not a feeling. "Row count matches the export for all
      12 months" passes. "The data is correct" does not.
- [ ] Each box under `## Definition of done` names the command, query, or file to inspect.
- [ ] The brief says what failure looks like, so the terminal reports a failure instead of
      widening the tolerance until it passes.
- [ ] Done is reachable in one session. If it is not, this is two tasks.
- [ ] **Every check under `## Definition of done` names its denominator.** Not "the restore
      is verified" but "all 20 tables compared, 20 of 20 match"; not "no duplicate claims" but
      "12 claims read, 12 distinct"; not "the CSRF gate holds" but "12 of 12 probes got PAST
      the gate, and then were refused". A verdict with no count is a verdict about whatever
      set survived, and the set is the thing that silently goes to zero.
- [ ] **The brief says what a zero denominator means, and it is never a pass.** A check that
      compared nothing exits nonzero and the terminal reports it as INCONCLUSIVE, not as HELD.
      This is the fleet's most repeated defect: V9's acceptance run tabulated twelve
      instruments across ten lanes, and three of the twelve were written that night by the
      agent counting them (rows counted by task 0292; the run's own prose says eleven). `roles/terminal.md`'s "Print the denominator" is the terminal-facing half of this
      box; `engine/bin/denominator-lint.py` is the machine that checks it.
- [ ] **A check the brief orders cannot be satisfied by an upstream refusal.** If every probe
      can 403 at a gate, or the tool can be absent from the PATH, or a subprocess can eat the
      loop's stdin, the brief says so under `## Context` and names what the terminal must print
      to prove the probes arrived.

## 2. Paths are real

- [ ] `--workdir` is set and is the repo the work actually happens in. The terminal may not
      leave it, so a wrong `workdir` returns as a question, not as work.
- [ ] Every path is absolute and exists. Copy them from a listing, never from memory.
- [ ] Repo, table, and file names are exact: `example-project.gold.sales_ledger`,
      not "the ledger table".
- [ ] The brief says where to start: the one file or command to open first.
- [ ] Any path you are unsure of is labelled a guess. "The README names
      `raw_cin7.stock_on_hand`, which was empty last check. The live table may be
      `inventory_balance_daily`."

## 3. Traps are stated up front

- [ ] Every known failure mode in this area is in `## Context`, before the work, not as a
      footnote. A trap discovered by the terminal costs an hour. The same trap in the brief
      costs one line.
- [ ] Environment traps are included when relevant: a tool that hangs under WSL, a CA bundle
      that must be exported, hours in which a job must not run.
- [ ] The brief says what not to touch: other lanes' files, deliberate holds, staged work
      belonging to someone else.

## 4. Prior wrong answers are named

- [ ] Any number previously reported wrong appears with the wrong value, the true value, and
      the cause. "Channel net revenue is not -$12,345, that is a 3x GROUPING SETS fan-out. Truth
      is -$15,904.29. Pin the grain."
- [ ] Any prior attempt is named by task id, with what it tried and how far it got.
- [ ] Every claim you inherited rather than measured is labelled as inherited. The terminal is
      entitled to treat everything else as checked.

## 5. Report back is explicit

- [ ] `## Report back` lists what the summary must contain: which numbers, which paths, which
      command output.
- [ ] It names the fork: which decisions the terminal makes alone, and which it must raise
      with `swarm ask` rather than guess. Every ask carries `--default`.
- [ ] If a finding needs to reach a specific agent immediately, the brief says to send it with
      `swarm msg` rather than leave it in the summary.

## 6. Commits are named paths, never a count

- [ ] No box under `## Definition of done` makes a whole-tree count the deliverable.
      "Commit the 84 modified + 84 untracked paths" is not an instruction, it is a headcount
      of everybody else's work. A count is not a set of named paths, and it goes stale
      between the measuring and the committing.
- [ ] The brief says **"commit YOUR work, named paths"** and never **"commit the tree"**.
      Those two sentences are one word apart in spirit and worlds apart in blast radius, and
      both phrasings are printed here side by side because the next person will write the
      shorter one by accident.
- [ ] If the brief asks for a commit, the brief lists the paths, by name. If you cannot name
      them while posting, the terminal cannot name them while committing, and what it
      substitutes is the tree.
- [ ] The boundary, in one sentence, so nobody re-derives it: asking a terminal to
      **measure** the tree (`git status --porcelain | wc -l`) is fine and is often required;
      making that measurement the thing it **commits** is the defect.
- [ ] Paths that are neither yours nor this task's are handed off with
      `swarm post --parent`, never appended as "and commit everything else while you are in
      there".
- [ ] This has now happened twice. Two occurrences is the argument for this section; one
      would have read as an accident.

The two occurrences, cited precisely, because a rule about honest briefs must not itself
carry an unverified claim:

- **2026-08-18, `45f1a3f`**, fully documented. 301 files and 51,433 insertions, sweeping the
  in-flight work of 15 tasks across all 8 agents into one commit whose message was about a
  VPS clone. The work order was task 0108 item 3, *"Commit the 84 modified + 84 untracked
  paths first"*. That count was measured 46 minutes before the commit and had already moved
  to 94 modified and 207 untracked. `roles/terminal.md:142` did not stop it: the lane named
  all 301 paths explicitly and proved the staged set equal, 301 == 301. Naming the whole
  tree by pathspec is still `git add -A`. Measured in full at
  `outputs/2026-08-18-commit-hygiene/CONTAMINATION.md`; the terminal-facing half of the fix
  is `docs/COMMIT-HYGIENE.md`.
- **2026-08-14, `75fafb5`** (named alongside `7633603`), the earlier one, cited with its
  limits stated. Archived task **0040** fixed the cause of that `git add -A` sweep by
  creating a repo-root `CLAUDE.md`, committed as `cb4d171`. Three things about 0040 are not
  provable and are therefore not claimed: it is not on this bus (`swarm show 0040` returns
  `no such task`), its title is recorded nowhere on disk, and none of those three shas exist
  in this repo, whose first commit is `8f9dd6c` on 2026-08-16, two days after that sweep.
  The surviving record is archived task 0062 at
  `outputs/2026-08-17-backlog-archive.md:747-756`. It is a prior-program precedent, not this
  repo's own task.

## Instant rejects

- "Fix the issues in X." Which issues? Name them, or post a task to find them.
- "Make sure everything works." Not verifiable, not a task.
- "Investigate and improve." Two tasks, and the second one is not written yet.
- A brief containing no number, no path, and no command.
- A brief that forces the terminal to decide something the operator owns, without telling it
  to ask.
- "Commit the N modified and N untracked paths." A count is not a path list. Name the paths,
  or scope the commit to the ones this task produced.
- A definition-of-done box whose check has no denominator. "Verify the restore" and "confirm
  no duplicates" are both satisfied by comparing nothing at all.

## Last check

Could you execute this brief yourself, in a fresh session, with no memory of writing it, and
know when to stop? If not, it is not a brief yet. Post the investigation that would let you
write it.
