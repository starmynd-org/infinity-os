#!/usr/bin/env python3
"""`swarm helper <id>` opens a terminal on a task, and it does NOT claim it.

Andrew's own answer, on 2026-08-28, to what is missing that would make him run his real day
through this product: *"One of the key things about this app is that it's supposed to actually
have like a terminal and a beautiful UX wrapper on top of that terminal ... We didn't get to test
to make sure that all terminal conversations are being logged properly. That was a key part of
the product that's not being tracked."* Three things sit under that. This suite is the third:
launching a helper terminal from a task, defaulting to Claude Code or Codex.

WHAT MAKES THIS WORTH A SUITE RATHER THAN A SMOKE TEST. The verb runs an interactive engine, so
the tempting shape is to test only `--print` and assert nothing about the launch, which would
leave every claim about the launch untested and reading exactly like a pass. Scenes 6 to 10
therefore run the verb ALL THE WAY THROUGH, against a fake engine planted on PATH under a real
pty, and read back what the verb actually handed the binary. Three of the four properties the
feature rests on are only observable there:

    it launched the engine with the session id it minted BEFORE the launch, and
    the SAME id is on the task's thread                     -> the row and the conversation match
    the engine's prompt carries the POSTED BRIEF            -> nothing had to be pasted by hand
    `claimed_by` is still NULL and `attempts` still 0       -> `helper` is not `claim`

The fourth, that a Claude Code session started with `--session-id X` is registered under X by the
installed session hooks, is a fact about the HARNESS and not about this repo, so it is not
asserted here. It was measured instead: session `78ff70e0-971b-412f-ad93-0c9e0386d6a3`, minted by
this verb on 2026-08-28, appears in `ingest.session` with `registration_source='hook'` and the
helper preamble as its stated goal. Asserting it would mean starting a real engine inside a test.

Run: python3 engine/tests/test_helper_verb.py    (a scratch database, never `brain`)
"""

from __future__ import annotations

import os
import pty
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("ENGINE_SCRATCH_DB", "brain_scratch"))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile              # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])

# The runner exports both of these into every terminal and both mean "an agent is at the
# keyboard" to the CLI. Popped so this suite tests the verb and not the runner's environment.
os.environ.pop("SWARM_PARENT_TASK", None)
os.environ.pop("SWARM_AGENT", None)

import store                                          # noqa: E402
from swarm_engine import accept, transitions          # noqa: E402,F401  registers the verbs

SWARM = str(ROOT / "engine/bin/swarm")
PASS, FAIL = 0, 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  ok    {msg}")


def bad(msg, detail=""):
    global FAIL
    FAIL += 1
    print(f"  FAIL  {msg}")
    if detail:
        print(f"        {detail}")


def check(cond, msg, detail=""):
    ok(msg) if cond else bad(msg, detail)


# ------------------------------------------------------------------ the harness

TMP = tempfile.mkdtemp(prefix="helper-verb-")
FAKEBIN = Path(TMP) / "bin"
FAKEBIN.mkdir(parents=True)
HOME = Path(TMP) / "home"
HOME.mkdir()
CFG = Path(TMP) / "config.json"
CFG.write_text('{"fleet":"helper-test","defaults":{"permission_mode":"auto"},'
               '"agents":[{"name":"T1","role":"terminal","lanes":["*"]}]}\n', encoding="utf-8")

# The fake engines. Each writes its own argv, one argument per line, and exits 0. A real engine
# is never started: what is under test is what this verb HANDS an engine, and a suite that
# started Claude Code to find out would be measuring the subscription.
for name in ("claude", "codex"):
    f = FAKEBIN / name
    f.write_text('#!/usr/bin/env bash\n'
                 'printf "%s\\n" "$@" > "$FAKE_ARGV_OUT"\n'
                 'printf "PWD=%s\\n" "$PWD" >> "$FAKE_ARGV_OUT"\n'
                 f'echo "fake {name} ran"\n'
                 'exit 0\n', encoding="utf-8")
    f.chmod(0o755)


def env(**extra):
    e = dict(os.environ)
    e["ENGINE_HOME"] = str(HOME)
    e["ENGINE_CONFIG"] = str(CFG)
    e.update(extra)
    return e


def run(args, **kw):
    p = subprocess.run([SWARM] + args, capture_output=True, text=True, env=env(**kw))
    return p.returncode, p.stdout, p.stderr


def run_on_a_pty(args, argv_out, **kw):
    """Run the verb with a real terminal under it, and return what the fake engine was handed.

    The verb refuses to launch with no tty on stdin, on purpose: an interactive engine started
    by a script is a session nobody is watching. So a suite that wants to test the launch has to
    supply a terminal rather than route around the refusal.
    """
    e = env(PATH=f"{FAKEBIN}:{os.environ['PATH']}", FAKE_ARGV_OUT=str(argv_out), **kw)
    master, slave = pty.openpty()
    p = subprocess.Popen([SWARM] + args, stdin=slave, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, env=e, start_new_session=True)
    try:
        out = p.communicate(timeout=90)[0]
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        out = "TIMED OUT"
    os.close(slave)
    os.close(master)
    handed = Path(argv_out).read_text(encoding="utf-8").splitlines() if Path(argv_out).exists() \
        else []
    return p.returncode, out, handed


def post(title, body, workdir=None, extra=None):
    args = ["post", "--lane", "engine", "--title", title, "--body", body, "--for-agents"]
    if workdir:
        args += ["--workdir", workdir]
    args += (extra or [])
    rc, out, err = run(args)
    m = re.search(r"\b(\d{4})\b", out)
    if rc != 0 or not m:
        raise SystemExit(f"could not post a fixture task: rc={rc} out={out!r} err={err!r}")
    return m.group(1)


def row(tid):
    with store.read() as s:
        return s.one("SELECT * FROM brain.work_item WHERE id = %s", (tid,))


def thread(tid):
    with store.read() as s:
        return s.query("SELECT kind, from_agent, text FROM brain.thread "
                       "WHERE work_item_id = %s ORDER BY seq", (tid,))


print(f"test_helper_verb.py  --  the helper terminal, against {os.environ['BRAIN_PG_DB']}")
print()

WORKDIR = str(ROOT)
T_MAIN = post("helper: the row a terminal opens on", "THE POSTED BRIEF a helper must carry.",
              workdir=WORKDIR)
T_GONE = post("helper: a row whose tree is not there", "brief", workdir=str(Path(TMP) / "absent"))

# --------------------------------------------------------------------------- 1. print is inert
print("  scene 1: --print writes nothing and launches nothing")
before_state = row(T_MAIN)
before_thread = len(thread(T_MAIN))
rc, out, err = run(["helper", T_MAIN, "--print"])
check(rc == 0, "`helper --print` exits 0", f"rc={rc} err={err!r}")
check(f"{ROOT}/engine/bin/swarm helper {T_MAIN}" in out,
      "it prints one absolute, paste-ready line and no arguments to fill in", out)
after = row(T_MAIN)
check(len(thread(T_MAIN)) == before_thread,
      "it appended nothing to the thread", f"{before_thread} -> {len(thread(T_MAIN))}")
check(after["state"] == before_state["state"] and not after["claimed_by"]
      and after["attempts"] == before_state["attempts"],
      "and it moved no state: same state, no claimer, same attempt count",
      f"state={after['state']} claimed_by={after['claimed_by']!r} "
      f"attempts={after['attempts']}")
check(not (HOME / "helpers").exists() or not list((HOME / "helpers").glob("*")),
      "and it wrote no context file, because nothing was opened")

# ------------------------------------------------------------- 2. the codex caveat is on both
print("\n  scene 2: the two engines are NOT offered as equals")
rc, out_claude, _ = run(["helper", T_MAIN, "--print"])
rc2, out_codex, _ = run(["helper", T_MAIN, "--print", "--engine", "codex"])
check(f"--engine codex" in out_codex and "codex" in out_codex.lower(),
      "`--engine codex` prints the codex line")
check("NOT EQUIVALENT" in out_codex,
      "the CODEX line says its capture is NOT equivalent, before anything starts", out_codex)
check("NOT EQUIVALENT" not in out_claude and "session-id" in out_claude,
      "and the claude line does not, because its session IS registered and indexed", out_claude)
check("~/.codex/sessions" in out_codex,
      "the codex line names where the conversation actually goes instead", out_codex)

# ----------------------------------------------------------- 3. a missing workdir is refused
print("\n  scene 3: a workdir that is set and missing FAILS rather than relocating")
rc, out, err = run(["helper", T_GONE, "--print"])
check(rc != 0, "the verb refuses the row outright", f"rc={rc} out={out!r}")
check("Not relocating" in err or "not relocating" in err,
      "and says it is not relocating, which is `bin/swarm-run`'s rule for the fleet", err)

# ------------------------------------------------------------------ 4. and 5. the two refusals
print("\n  scene 4: the refusals that are not about a workdir")
rc, out, err = run(["helper", "9999", "--print"])
check(rc == 1 and "no such task" in err, "a task that does not exist exits 1", f"{rc} {err!r}")
p = subprocess.run([SWARM, "helper", T_MAIN], capture_output=True, text=True, env=env(),
                   stdin=subprocess.DEVNULL)
check(p.returncode == 1 and "--print" in p.stderr,
      "with no terminal on stdin it refuses and names --print, rather than silently switching "
      "mode", f"rc={p.returncode} err={p.stderr!r}")

# ------------------------------------------------------- 6-9. the launch, all the way through
print("\n  scene 5: the launch itself, against a fake engine on a real pty")
argv_out = Path(TMP) / "argv-claude.txt"
before = row(T_MAIN)
rc, out, handed = run_on_a_pty(["helper", T_MAIN], argv_out)
check(rc == 0 and handed, "the verb ran the engine binary and returned",
      f"rc={rc} out={out[-400:]!r}")
sid = ""
if "--session-id" in handed:
    sid = handed[handed.index("--session-id") + 1]
check(re.fullmatch(r"[0-9a-f-]{36}", sid or ""),
      "it handed the engine a --session-id, minted before the launch", f"handed={handed[:6]}")
check(any(a == f"PWD={WORKDIR}" for a in handed),
      "the session started in the directory the ROW names, not in the caller's", handed[-1:])
# ONE ARGUMENT PER LINE means a multi-line prompt spans many lines, so the handoff is read
# whole. Slicing `handed[-2]` would test only the prompt's LAST line and pass on an empty one.
prompt = "\n".join(handed)
check("THE POSTED BRIEF a helper must carry." in prompt,
      "the prompt carries the POSTED BRIEF, so no brief was pasted by hand")
check("you must not claim it" in prompt and "swarm claim" in prompt,
      "and it tells the session it holds no claim and must not take one")

print("\n  scene 6: it is NOT `claim`")
after = row(T_MAIN)
check(not after["claimed_by"], "the row still has no claimer", repr(after["claimed_by"]))
check(after["attempts"] == before["attempts"],
      "no attempt was spent", f"{before['attempts']} -> {after['attempts']}")
check(after["state"] == before["state"], "and the state did not move", after["state"])

print("\n  scene 7: the row and the conversation name the same thing")
notes = [t for t in thread(T_MAIN) if t["kind"] == "note" and "helper terminal opened" in t["text"]]
check(len(notes) == 1, "exactly one helper note landed on the thread", f"{len(notes)} found")
check(bool(sid) and any(sid in n["text"] for n in notes),
      "and it names the SAME session id the engine was launched with", sid)
check(any("No claim was taken" in n["text"] for n in notes),
      "and it says on the record that no claim was taken")

print("\n  scene 8: the context file is the row, whole")
files = sorted((HOME / "helpers").glob(f"{T_MAIN}-*"))
check(len(files) == 1, "one context file was written", str(files))
if files:
    body = files[0].read_text(encoding="utf-8")
    shown = subprocess.run([SWARM, "show", T_MAIN, "--full"], capture_output=True, text=True,
                           env=env()).stdout
    check("THE POSTED BRIEF a helper must carry." in body,
          "it holds the posted brief whole")
    check(body.splitlines()[0] == shown.splitlines()[0],
          "and it is `swarm show --full`'s own output rather than a second renderer",
          f"{body.splitlines()[0]!r} vs {shown.splitlines()[0]!r}")

print("\n  scene 9: --no-record opens the terminal and leaves the row unlinked")
n_before = len([t for t in thread(T_MAIN) if t["kind"] == "note"])
rc, out, handed2 = run_on_a_pty(["helper", T_MAIN, "--no-record"], Path(TMP) / "argv-nr.txt")
n_after = len([t for t in thread(T_MAIN) if t["kind"] == "note"])
check(rc == 0 and handed2, "it still launches", f"rc={rc}")
check(n_after == n_before, "and writes no note, so nothing connects the row to the session",
      f"{n_before} -> {n_after}")

print("\n  scene 10: the codex launch, and what it is told before it starts")
rc, out, handed3 = run_on_a_pty(["helper", T_MAIN, "--engine", "codex"],
                                Path(TMP) / "argv-codex.txt")
check(rc == 0 and handed3, "the codex branch launches the codex binary", f"rc={rc}")
check("--cd" in handed3 and handed3[handed3.index("--cd") + 1] == WORKDIR,
      "it passes the row's directory as --cd, so the printed command stands on its own",
      str(handed3[:4]))
check("--session-id" not in handed3,
      "and it mints no session id, because codex has no flag to accept one")
check("NOT EQUIVALENT" in out,
      "the operator is told the capture is not equivalent BEFORE the session starts", out[:600])
check("NO codex rollout" in out,
      "and when no rollout appears it SAYS SO, rather than reporting a record it did not find",
      out[-400:])

print("\n  scene 11: the codex rollout recovery, which is the only pointer that engine leaves")
# Called directly with a root of its own. The verb reads ~/.codex/sessions, and a suite that
# planted files under the operator's real home to test this would be writing into his engine's
# state to prove a point about reading it.
from swarm_engine.cli import _codex_rollouts_since                          # noqa: E402
fake_codex = Path(TMP) / "codex-sessions" / "2026" / "08" / "28"
fake_codex.mkdir(parents=True)
cut = time.time()
old_roll = fake_codex / "rollout-2026-08-27T10-00-00-aaaa.jsonl"
old_roll.write_text("{}\n", encoding="utf-8")
os.utime(old_roll, (cut - 3600, cut - 3600))
new_roll = fake_codex / "rollout-2026-08-28T10-00-00-bbbb.jsonl"
new_roll.write_text("{}\n", encoding="utf-8")
os.utime(new_roll, (cut + 5, cut + 5))
found = _codex_rollouts_since(cut, root=str(Path(TMP) / "codex-sessions"))
check(len(found) == 1 and found[0] == new_roll,
      "it returns the rollout written DURING the session and not the one from yesterday",
      str(found))
check(_codex_rollouts_since(cut, root=str(Path(TMP) / "not-there")) == [],
      "and a sessions directory that is not there returns nothing rather than raising")

print("\n  scene 12: the logging claim is READ off the operator's settings, not asserted")
# The claude branch tells the operator his conversation is on the record. That is a claim about
# `~/.claude/settings.json`, a file no agent is allowed to write (three refusals, recorded in
# ingest/docs/HOOK-INSTALL.md), so it can be absent on any host. A verb that promised the record
# anyway would be the exact failure this feature exists to prevent. Both answers are exercised.
wired_dir = Path(TMP) / "cfg-wired"
wired_dir.mkdir()
(wired_dir / "settings.json").write_text(
    '{"hooks":{"SessionStart":[{"hooks":[{"type":"command",'
    '"command":"/x/ingest/bin/claude-session-hook"}]}],'
    '"SessionEnd":[{"hooks":[{"type":"command",'
    '"command":"/x/ingest/bin/claude-session-hook"}]}]}}\n', encoding="utf-8")
bare_dir = Path(TMP) / "cfg-bare"
bare_dir.mkdir()

rc, out, _ = run_on_a_pty(["helper", T_MAIN, "--no-record"], Path(TMP) / "argv-hooks-on.txt",
                          CLAUDE_CONFIG_DIR=str(wired_dir))
check("hooks ARE wired" in out, "with the hooks wired it says so and names the file it read",
      out[:800])
rc, out, _ = run_on_a_pty(["helper", T_MAIN, "--no-record"], Path(TMP) / "argv-hooks-off.txt",
                          CLAUDE_CONFIG_DIR=str(bare_dir))
check("NOT INSTALLED" in out and "HOOK-INSTALL.md" in out,
      "and with them absent it refuses to promise a record, and names the operator's own step",
      out[:800])
check("was NOT registered" in out,
      "including afterwards, where the session id is on the thread and the conversation is not",
      out[-500:])

print("\n  scene 13: the console prints the SAME line this verb answers with")
# TWO AUTHORS OF ONE STRING is the drift this checks for. The task page cannot shell out to the
# CLI per render, so `web/templates/detail_task.html` composes the launch line itself, and a
# rename of the verb or a change to its flags would leave the console handing out a command that
# no longer exists while `--print` quietly stayed right. There is no route this template's lane
# could call, so the two are reconciled HERE instead, which is the cheapest place that can see
# both. Substituting the template's own two variables is the whole comparison.
TEMPLATE = ROOT / "web/templates/detail_task.html"
tpl = TEMPLATE.read_text(encoding="utf-8") if TEMPLATE.exists() else ""
lines = re.findall(r"\{\{ repo \}\}/engine/bin/swarm helper \{\{ w\.id \}\}[^<\n]*", tpl)
check(len(lines) == 2,
      "the task page carries exactly two launch lines, one per engine", f"{len(lines)}: {lines}")
for tl in lines:
    concrete = tl.replace("{{ repo }}", str(ROOT)).replace("{{ w.id }}", T_MAIN).strip()
    want_codex = "--engine codex" in concrete
    printed = run(["helper", T_MAIN, "--print"] + (["--engine", "codex"] if want_codex else []))[1]
    check(concrete in printed,
          f"and the console's {'codex' if want_codex else 'claude'} line is byte for byte what "
          f"`--print` answers with", f"{concrete!r} not in {printed!r}")

print()
# THIS SUITE'S OWN DENOMINATOR. Task 0292: `0 passed, 0 failed` exits 0 and run-all.sh prints
# ALL SUITES GREEN over it, so a run whose assertions never executed reads like a clean one.
if PASS + FAIL == 0:                                                        # DENOMINATOR
    print("DENOMINATOR: 0 assertions ran. A verdict over an empty set is not a pass.")
    sys.exit(2)
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
