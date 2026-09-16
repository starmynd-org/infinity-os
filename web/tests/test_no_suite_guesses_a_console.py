"""A browser suite refuses to guess which console it is driving.

W3-UX-INFINITY, 2026-09-16. `web/host.py`'s `BASE_URL` falls back to
`http://127.0.0.1:{CONSOLE_PORT}` with `CONSOLE_PORT` defaulting to **3103** -- one of the three
ports this repo's own `CLAUDE.md` reserves in bold: "Ports 3103, 3104 and 3105 are the operator's.
Do not restart, kill or write through them."

So `python3 -m web.tests.test_phone_surface`, typed with nothing set, launched a chromium against
the operator's live console. MEASURED 2026-09-16: it did, and something was serving there. The run
read and rendered only -- every write assertion in that suite builds its own in-process app with
`create_app()` -- so nothing was written, restarted or killed. But the default is a trap laid for
every seat that will ever type that command, and a careful one walked into it within an hour of
reading the warning.

WHY A REFUSAL AND NOT A SAFER DEFAULT. Pointing the fallback at a scratch port relocates the trap:
the next reader still cannot tell, from the command they typed, which console they drove. A
refusal names the problem at the moment it matters. Removing the condition beats trusting the
guard.

WHY THIS SUITE AND NOT ALL TEN. MEASURED: exactly one suite inherits the fallback --
`test_phone_surface.py`, via `host.BASE_URL`. Nine others read `BASE` from the environment
themselves and have no fallback to inherit, and `run-all.sh:373` exports `BASE` unconditionally,
so the runner never reaches any of this. One file, and it cannot affect the runner.

THE PAIR IS THE POINT. Asserting only that it refuses would pass if the suite refused
unconditionally, which would be a worse defect than the one being fixed. So the second case runs
it WITH `BASE` set and requires it NOT to refuse for this reason -- it fails later, for a
connection, which is the suite working.

THIS SUITE LAUNCHES A CHROMIUM, AND IT IS DECLARED `ownbrowser` FOR THAT REASON. MEASURED under
an exec tracer before it was declared: 30 execs, 5 of them chromium. They come from the CONTROL
case, which sets `BASE` and therefore gets past the guard, launches a browser, and fails on the
dead port. Declaring it `none` would have been the sixth mis-declaration found tonight and the
only one I authored. The cost of that control is one browser launch, about eighteen seconds, and
it buys the difference between "it refuses" and "it refuses only when it should".

MEASURED in the same run: this suite never reaches 3103. The no-`BASE` case refuses at module
level, before any browser code, and the control case points at port 1. Zero mentions of 3103 in
its output. The suite that tests a trap does not spring it.

Run:  python3 -m unittest web.tests.test_no_suite_guesses_a_console
"""

import os
import subprocess
import sys
import unittest

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(WEB_DIR)
GUARDED = "web.tests.test_phone_surface"
MARKER = "refusing to guess a console"
# A port in the reserved range that nothing will ever serve, so the WITH-BASE case fails on a
# connection rather than on anything this suite is asserting about.
DEAD = "http://127.0.0.1:1"


def run_module(env_extra):
    env = dict(os.environ)
    env.pop("BASE", None)
    env.update(env_extra)
    # Never inherit a live store into a browser suite from whatever ran this.
    env["BRAIN_PG_DB"] = env.get("BRAIN_PG_DB", "brain_w3ux_guard_no_such_db")
    return subprocess.run([sys.executable, "-m", GUARDED], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=120)


class NoSuiteGuessesAConsole(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.without = run_module({})
        cls.withbase = run_module({"BASE": DEAD})
        print("\n  MEASURED  BASE unset -> exit %s;  BASE=%s -> exit %s"
              % (cls.without.returncode, DEAD, cls.withbase.returncode))

    def test_with_no_BASE_it_refuses_and_says_so(self):
        out = self.without.stdout + self.without.stderr
        self.assertNotEqual(self.without.returncode, 0,
                            "it ran with no BASE: %s" % out[:300])
        self.assertIn(MARKER, out, out[:400])

    def test_the_refusal_names_the_address_it_would_have_used(self):
        """A refusal that does not say what it was about to do teaches nothing."""
        out = self.without.stdout + self.without.stderr
        self.assertIn("3103", out, "the refusal does not name the port it would have driven")
        self.assertIn("BASE=", out, "the refusal does not show how to satisfy it")

    def test_control_with_BASE_set_it_does_NOT_refuse_for_this_reason(self):
        """The nonzero beside the zero.

        Without this, a suite that refused unconditionally would pass the case above while being
        a worse defect than the one this guard fixes. With BASE named it gets past the guard and
        fails on the dead port instead, which is the suite working.
        """
        out = self.withbase.stdout + self.withbase.stderr
        self.assertNotIn(MARKER, out,
                         "it refused even though BASE was given: %s" % out[:300])

    def test_control_the_runner_still_exports_BASE_so_this_never_fires_there(self):
        """If the runner ever stops exporting BASE, every console suite in it starts refusing."""
        runall = open(os.path.join(WEB_DIR, "tests", "run-all.sh"), encoding="utf-8").read()
        self.assertIn('export BASE="http://127.0.0.1:${CONSOLE_PORT}"', runall)

    def test_control_only_one_suite_inherits_the_fallback(self):
        """The blast radius, kept true. If another suite starts taking host.BASE_URL it needs the
        same guard, and this reddens rather than letting it inherit the trap silently."""
        tests = os.path.join(WEB_DIR, "tests")
        inherit = []
        for name in sorted(os.listdir(tests)):
            if not name.endswith(".py") or name == os.path.basename(__file__):
                continue
            with open(os.path.join(tests, name), encoding="utf-8") as fh:
                if "host.BASE_URL" in fh.read():
                    inherit.append(name)
        self.assertEqual(inherit, ["test_phone_surface.py"], inherit)


if __name__ == "__main__":
    unittest.main()
