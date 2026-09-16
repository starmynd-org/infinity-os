#!/usr/bin/env python3
"""On an install with no brain beside it, brain-paging is skipped by name and brain-health stays green.

W5-S7, 2026-09-16, on ALPHA-COMMANDER-3's request, carried item A5. W5-S6 MEASURED on a clean
install: `fabric/listener.py` defaulted `BRAIN_ROOT` to ONE LAPTOP'S PATH,
`/mnt/c/Users/you/repos/your-brain`, so `brain-paging.service` refused at
start on every other host ("no subscriber declaration at ...") and was left FAILED, and
`systemd/brain-health` then read paging as HELD ("no cursor row") and exited 2 on every sweep. An
update procedure that checks for failed units reads that as a broken update everywhere.

THE BEHAVIOUR CHOSEN, AND WHY IT IS NOT "START WITHOUT A DECLARATION". The listener's first
precondition is doctrine: declare, never discover, and an undeclared listener does not run. That
stays. What was wrong was treating "this host has no brain at all" as a fault. So:
  * the declaration is looked for where THIS install says (SUBSCRIBERS_DECLARATION, BRAIN_ROOT, else
    the brain checked out beside the install), never at a path baked in for one machine;
  * `python3 -m subscribers.operator_paging --declared` exits 1 with a named reason only when no
    declaration FILE exists, and the unit runs it as ExecCondition, which systemd reads as "skip",
    not "fail";
  * a declaration file that exists but is wrong still refuses at start, by name (scene 6);
  * `paging-health` reports exit 5, "not declared on this host", before touching the store, and
    `brain-health` notes it without raising severity.

WHERE THIS IS RED. At 2341335, from any tree that is not checked out beside
`your-brain` -- a fresh install, or a scratch clone. On the operator's laptop, where the
runtime sits beside the brain, the old absolute default and the new sibling default are the same
path, so scene 1 cannot tell them apart there, and that is the behaviour being preserved.

Needs no database: the one store read (paging-health past the declaration check) is never reached
by the fixed code, and at the parent it fails by exit code, not by hanging.
Run: python3 engine/tests/test_paging_on_an_install_without_a_brain.py
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / "systemd" / "brain-paging.service"
HEALTH = ROOT / "systemd" / "brain-health"
PAGING_HEALTH = ROOT / "subscribers" / "bin" / "paging-health"
ENTRY = """# Subscribers

## operator-paging

- Subscriber: `operator-paging`
- Owning department: `chief-of-staff`
- Action class: `effect`
- Interface:
  - Consumes: `question.`, `fabric.subscriber.quarantined`
- Flagged-event posture: `gated`
- Phase: `1`
- Status: `live`
"""


def env_without_a_brain(**extra):
    env = {k: v for k, v in os.environ.items() if k not in ("BRAIN_ROOT", "SUBSCRIBERS_DECLARATION")}
    env["BRAIN_PG_DB"] = "brain_w5s7_paging_no_such_db"
    env["PYTHONPATH"] = str(ROOT)
    env.update(extra)
    return env


def run(args, env):
    p = subprocess.run(args, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout + p.stderr


class PagingOnAnInstallWithoutABrain(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="w5s7-paging-")
        cls.absent = Path(cls.tmp.name) / "no-brain-here" / "departments" / "SUBSCRIBERS.md"
        cls.present = Path(cls.tmp.name) / "a-brain" / "departments" / "SUBSCRIBERS.md"
        cls.present.parent.mkdir(parents=True)
        cls.present.write_text(ENTRY, encoding="utf-8")
        cls.wrong = Path(cls.tmp.name) / "a-wrong-brain" / "departments" / "SUBSCRIBERS.md"
        cls.wrong.parent.mkdir(parents=True)
        cls.wrong.write_text("# Subscribers\n\n## somebody-else\n", encoding="utf-8")
        print("\n  DENOMINATORS  install root %s; absent %s exists=%s; present %s exists=%s"
              % (ROOT, cls.absent, cls.absent.exists(), cls.present, cls.present.exists()))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # 1 -------------------------------------------------------------- the default names this install
    def test_1_with_no_brain_named_the_declaration_is_looked_for_beside_this_install(self):
        rc, out = run([sys.executable, "-c",
                       "from fabric import listener; print('PATH=' + str(listener.DECLARATION_PATH))"],
                      env_without_a_brain())
        self.assertEqual(rc, 0, out)
        got = re.search(r"PATH=(.*)", out).group(1).strip()
        want = str(ROOT.parent / "your-brain" / "departments" / "SUBSCRIBERS.md")
        self.assertEqual(got, want, "the declaration is looked for at a path this install did not name")

    # 2 -------------------------------------------------------------- skipped, by name
    def test_2_the_unit_condition_says_not_started_and_exits_1_when_no_declaration_exists(self):
        rc, out = run([sys.executable, "-m", "subscribers.operator_paging", "--declared"],
                      env_without_a_brain(SUBSCRIBERS_DECLARATION=str(self.absent)))
        self.assertEqual(rc, 1, out)
        self.assertIn("operator-paging: not started. no subscriber declaration on this host", out)

    # 3 -------------------------------------------------------------- control: the condition can pass
    def test_3_control_the_condition_passes_where_a_declaration_file_exists(self):
        rc, out = run([sys.executable, "-m", "subscribers.operator_paging", "--declared"],
                      env_without_a_brain(SUBSCRIBERS_DECLARATION=str(self.present)))
        self.assertEqual(rc, 0, out)
        self.assertIn("operator-paging: declared on this host", out)

    # 4 -------------------------------------------------------------- the unit runs the condition
    def test_4_the_paging_unit_runs_the_condition_before_it_starts(self):
        text = UNIT.read_text(encoding="utf-8")
        cond = "ExecCondition=/usr/bin/python3 -m subscribers.operator_paging --declared"
        self.assertIn(cond, text)
        self.assertLess(text.index(cond), text.index("ExecStart=/usr/bin/python3 -m subscribers.operator_paging"))

    # 5 -------------------------------------------------------------- health: not declared is not HELD
    def test_5_paging_health_reads_not_declared_before_the_store(self):
        rc, out = run([sys.executable, str(PAGING_HEALTH)],
                      env_without_a_brain(SUBSCRIBERS_DECLARATION=str(self.absent)))
        self.assertEqual(rc, 5, out)
        self.assertIn('"verdict": "not declared on this host"', out)

    def test_5b_brain_health_notes_not_declared_without_raising_severity(self):
        text = HEALTH.read_text(encoding="utf-8")
        branch = re.search(r"^\s*5\)(.*?);;", text, re.S | re.M)
        self.assertIsNotNone(branch, "brain-health has no branch for paging-health exit 5")
        self.assertNotIn("bump", branch.group(1))
        self.assertIn("note paging N/A", branch.group(1))
        # control: the same parse finds the bump in the branch that must raise severity
        held = re.search(r"^\s*3\)(.*?);;", text, re.S | re.M)
        self.assertIn("bump 2", held.group(1))

    # 6 -------------------------------------------------------------- doctrine intact
    def test_6_a_declaration_file_without_the_entry_still_refuses_at_start_by_name(self):
        sys.path.insert(0, str(ROOT))
        from fabric import listener
        ok, _ = listener.declared_here(self.wrong)
        self.assertTrue(ok, "a file that exists is not forgiven by the condition")
        with self.assertRaises(listener.StartupRefused) as caught:
            listener.load_declaration("operator-paging", self.wrong)
        self.assertIn("has no entry", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
