#!/usr/bin/env python3
"""`swarm post --help` and the write-path validator offer exactly the urgency words the store accepts.

W5-S7, 2026-09-16, on ALPHA-COMMANDER-3's request. W5-B2 MEASURED at 2341335 (E36): `swarm post --help`
said "--urgency URGENCY  low, medium or high or none, soon, deadline, decaying", and
`signals.validate_signal` accepted low, medium and high, while migration 0074 narrowed
`brain.signal_ok('urgency', ...)` to none, soon, deadline and decaying. An agent on a fresh install that
follows the help is accepted by the validator and refused by the store's CHECK on its first post.

THE STORE'S LIST IS READ FROM THE MIGRATION, NOT TYPED HERE. Scene 4 parses migration 0074's own
`field = 'urgency' AND lower(btrim(raw)) IN (...)` clause, so this suite follows the store if a later
migration's list is ever read instead, rather than pinning a copy of it.

Needs no database: `--help` exits before any store read, and the validator is pure.
Run: python3 engine/tests/test_urgency_words_match_the_store.py
"""

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BRAIN_PG_DB", "brain_w5s7_urgency_no_such_db")

from swarm_engine.signals import signal_level, validate_signal  # noqa: E402

MIGRATION = ROOT / "migrations" / "0074_urgency_has_four_words_and_high_is_not_one.sql"
CANDIDATES = ("low", "medium", "high", "none", "soon", "deadline", "decaying")


def store_words():
    text = MIGRATION.read_text(encoding="utf-8")
    m = re.search(r"field = 'urgency'\s+AND lower\(btrim\(raw\)\) IN \(([^)]*)\)", text)
    assert m, "migration 0074's urgency clause was not found, so the store's list is unknown"
    return tuple(w.strip().strip("'") for w in m.group(1).split(","))


class UrgencyWordsMatchTheStore(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.store = store_words()
        env = dict(os.environ, PYTHONPATH=str(ROOT / "engine"))
        p = subprocess.run([sys.executable, str(ROOT / "engine" / "bin" / "swarm"), "post", "--help"],
                           cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60)
        cls.help_rc, cls.help = p.returncode, p.stdout + p.stderr
        flat = re.sub(r"\s+", " ", cls.help)
        found = re.search(r"--urgency URGENCY (.*?)(?= --[a-z])", flat)
        cls.urgency_help = found.group(1) if found else ""
        print("\n  DENOMINATORS  store words %s (from %s); help rc %d; urgency help %r"
              % (cls.store, MIGRATION.name, cls.help_rc, cls.urgency_help))

    # 1 ------------------------------------------------------------------ the help
    def test_1_post_help_offers_no_urgency_word_the_store_refuses(self):
        self.assertEqual(self.help_rc, 0, self.help[-400:])
        self.assertTrue(self.urgency_help, "no --urgency help line was found, so nothing was measured")
        offered = set(re.findall(r"[a-z]+", self.urgency_help)) & set(CANDIDATES)
        self.assertEqual(sorted(offered - set(self.store)), [],
                         "the help offers words the store refuses: %r" % self.urgency_help)

    def test_2_post_help_offers_every_word_the_store_accepts(self):
        for w in self.store:
            self.assertIn(w, self.urgency_help)

    # 3 ------------------------------------------------------------------ the validator
    def test_3_the_validator_refuses_low_medium_and_high_for_urgency_by_name(self):
        for w in ("low", "medium", "high", "HIGH", " high "):
            with self.assertRaises(ValueError, msg=w) as caught:
                validate_signal("urgency", w)
            self.assertIn("urgency", str(caught.exception))

    def test_4_the_validator_accepts_exactly_the_store_set(self):
        accepted = []
        for w in CANDIDATES:
            try:
                validate_signal("urgency", w)
                accepted.append(w)
            except ValueError:
                pass
        self.assertEqual(sorted(accepted), sorted(self.store))

    # 5 ------------------------------------------------------------------ controls
    def test_5_control_never_assessed_is_still_accepted_and_other_signals_keep_three_levels(self):
        self.assertEqual(validate_signal("urgency", ""), "")
        self.assertEqual(validate_signal("stakes", "high"), "high")
        self.assertEqual(validate_signal("effort", "medium"), "medium")

    def test_6_control_the_read_path_still_folds_old_rows(self):
        self.assertEqual(signal_level("urgency", "high"), "high")
        self.assertEqual(signal_level("urgency", "deadline"), "high")


if __name__ == "__main__":
    unittest.main(verbosity=2)
