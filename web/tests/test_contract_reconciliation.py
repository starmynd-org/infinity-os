"""R04 views against the pinned C01 contract.

Contract: C01-PIN-01, version 0.1.0-draft.5, commit `0f5909a`, digest
`sha256:fb42d422a7ae834c2faf5523aad57d97786e51dfbe67360d26b92802ddcb6a10`. That is the merge basis
for this program; later drafts are stewardship, not pins, and are not what a consumer is judged
against.

READ FROM THE COMMIT, NEVER FROM THE WORKTREE, and this file learned that twice. Terminal 07
nearly filed a false integrity failure because `digest.py --check` calls a digest stale whenever
the checkout is dirty with the next draft. I wrote that rule into this docstring and then kept
reading the checkout anyway, so when the steward moved the tree to draft.6 this suite re-pinned
itself to a draft nobody merges against. Schemas are now fetched with `git show`, so what is
asserted is what the program integrates.

The other defect this file carried, found by Terminal 14 and not by me: it located the contracts
by a literal Windows path, so off Windows all eleven tests skipped themselves. A guard that skips
is worse than one that is absent, because it reports a number that looks like coverage. The
location is derived from this file's own path now, with an `INFINITY_CONTRACTS` override.
"""

import json
import os
import subprocess
import unittest

from web.views import Impact

PIN_COMMIT = "0f5909a"
PIN_VERSION = "0.1.0-draft.5"
DIGEST = "sha256:fb42d422a7ae834c2faf5523aad57d97786e51dfbe67360d26b92802ddcb6a10"


def _find_repo():
    """Locate the contracts repository without naming one machine's drive letter."""
    override = os.environ.get("INFINITY_CONTRACTS")
    here = os.path.dirname(os.path.abspath(__file__))          # .../R-cap07/web/tests
    worktree = os.path.dirname(os.path.dirname(here))           # .../R-cap07
    siblings = os.path.dirname(worktree)                        # .../worktrees
    candidates = [
        override,
        os.path.join(siblings, "H-cap01"),
        os.path.join(os.path.dirname(siblings), "H-cap01"),
    ]
    for candidate in candidates:
        if candidate and os.path.isdir(os.path.join(candidate, "contracts")):
            return candidate, candidates
    return None, candidates


REPO, _TRIED = _find_repo()


def _at_pin(path):
    """Read one file from the pinned commit. None when it cannot be read, never a fallback."""
    if REPO is None:
        return None
    try:
        out = subprocess.run(["git", "-C", REPO, "show", "%s:contracts/%s" % (PIN_COMMIT, path)],
                             capture_output=True, check=True)
        return out.stdout.decode("utf-8")
    except (OSError, subprocess.CalledProcessError):
        return None


def schema(name):
    raw = _at_pin("schemas/" + name)
    if raw is None:
        raise unittest.SkipTest("cannot read %s at the pinned commit %s; tried repos: %s"
                                % (name, PIN_COMMIT, ", ".join(str(c) for c in _TRIED if c)))
    return json.loads(raw)


class TheContractIsTheOneWeRead(unittest.TestCase):
    """If the draft moves, these tests should notice rather than silently pass against a new one.

    WHAT THIS IS NOT: an integrity check. It reads the version the contracts worktree DECLARES and
    compares it to the one these mappings were written against. It does not verify that the tree
    matches the digest, and it must not be quoted as if it did. Terminal 07 nearly reported a false
    integrity failure tonight for the neighbouring reason: `digest.py --check` inside that worktree
    calls the published digest stale whenever the tree is dirty with the next draft's edits, and the
    digest is correct for the commit it names. Verify a digest against the commit, never the
    worktree.
    """

    def test_the_pinned_commit_is_the_one_these_mappings_were_written_against(self):
        raw = _at_pin("DIGEST")
        if raw is None:
            self.skipTest("cannot read the pinned DIGEST; tried: %s"
                          % ", ".join(str(c) for c in _TRIED if c))
        self.assertIn(PIN_VERSION, raw, "the pin moved; re-read before trusting these mappings")
        self.assertIn(DIGEST, raw, "the pinned digest changed; re-read before trusting these mappings")

    def test_a_steward_draft_in_the_worktree_does_not_change_what_is_asserted(self):
        """The failure this closes: the checkout moved to draft.6 and this suite followed it.

        Whatever the working tree currently declares, the assertions above read the pinned commit.
        A later draft is tolerated in the tree and is not the basis of any claim here.
        """
        if REPO is None:
            self.skipTest("contracts repository not found; tried: %s"
                          % ", ".join(str(c) for c in _TRIED if c))
        checkout = os.path.join(REPO, "contracts", "DIGEST")
        if not os.path.exists(checkout):
            self.skipTest("no DIGEST in the checkout to compare against")
        with open(checkout, encoding="utf-8") as handle:
            in_tree = handle.read()
        pinned = _at_pin("DIGEST") or ""
        if PIN_VERSION not in in_tree:
            self.assertIn(PIN_VERSION, pinned,
                          "the tree has moved past the pin and the pinned commit no longer reads "
                          "as the pin either; stop and re-read before asserting anything")


class UnknownableMapsOntoImpact(unittest.TestCase):

    def setUp(self):
        if REPO is None:
            self.skipTest("contracts not found; tried: %s" % ", ".join(str(c) for c in _TRIED if c))
        self.statuses = schema("work-packet.schema.json")["$defs"]["Unknownable"]["properties"]["status"]["enum"]

    def test_every_contract_status_has_a_branch(self):
        """The failure this catches: a status the contract has and this surface silently drops."""
        self.assertEqual(set(self.statuses), {"unknown", "estimated", "measured"})
        for status in self.statuses:
            payload = {"status": status}
            if status != "unknown":
                payload.update({"value": 12, "unit": "min", "basis": "9 samples"})
            self.assertIsInstance(Impact.from_contract(payload), Impact)

    def test_an_estimate_is_not_rendered_as_a_measurement(self):
        estimate = Impact.from_contract({"status": "estimated", "value": 400, "unit": "EUR",
                                         "basis": "median of comparable renewals"})
        self.assertFalse(estimate.is_known)
        self.assertTrue(estimate.is_estimate)
        self.assertIn("estimated", estimate.render())
        self.assertIn("unconfirmed", estimate.render())

    def test_a_measurement_says_what_measured_it(self):
        measured = Impact.from_contract({"status": "measured", "value": 12, "unit": "min",
                                         "basis": "median over 9 samples"})
        self.assertTrue(measured.is_known)
        self.assertIn("12 min", measured.render())
        self.assertIn("9 samples", measured.render())

    def test_unknown_carrying_a_value_is_refused_at_the_boundary(self):
        """The contract forbids it. This surface is where a violation would reach a person."""
        with self.assertRaises(ValueError):
            Impact.from_contract({"status": "unknown", "value": 400, "unit": "EUR"})

    def test_unknown_with_no_basis_still_gets_a_reason_rather_than_a_shrug(self):
        impact = Impact.from_contract({"status": "unknown"})
        self.assertFalse(impact.is_known)
        self.assertIn("no basis", impact.render())

    def test_a_value_without_a_unit_is_refused(self):
        with self.assertRaises(ValueError):
            Impact.from_contract({"status": "measured", "value": 12, "basis": "x"})

    def test_a_whole_number_does_not_print_false_precision(self):
        impact = Impact.from_contract({"status": "measured", "value": 3.0, "unit": "h",
                                       "basis": "timed"})
        self.assertIn("3 h", impact.render())
        self.assertNotIn("3.0", impact.render())


class WhereTheContractAndThisSurfaceStillDisagree(unittest.TestCase):
    """Recorded as tests so the change requests cannot be forgotten between here and the pin."""

    def setUp(self):
        if REPO is None:
            self.skipTest("contracts not found; tried: %s" % ", ".join(str(c) for c in _TRIED if c))

    def test_the_contract_cannot_express_no_effect_claimed(self):
        """CHANGE REQUEST 1 to C01.

        "Nobody measured this" and "this item has no business effect to measure" are different
        facts, and the surface says them differently: 'not measured (why)' versus 'no effect
        claimed'. `Unknownable` has one bucket for both, so a producer with nothing to claim must
        send `unknown` and the screen then implies a measurement is missing when none was ever
        owed. Requested: a fourth status `not_applicable`, or a boolean beside it.
        """
        self.assertEqual(set(schema("work-packet.schema.json")["$defs"]["Unknownable"]
                             ["properties"]["status"]["enum"]),
                         {"unknown", "estimated", "measured"})
        self.assertEqual(Impact.none_claimed().render(), "no effect claimed")

    def test_the_receipt_completeness_vocabulary_now_has_a_view(self):
        """CHANGE REQUEST 2, CLOSED by this lane rather than left as a note.

        `Receipt.completeness` is complete / partial / uncertain, and R04 rendered no component for
        it, so a routine that finished with a source missing said "finished" and put the caveat in
        prose a reader skims. `web/views/honesty.Completeness` now carries the three states, and
        `web/tests/test_completeness.py` holds its rules: partial refuses to be built without
        naming what is missing, uncertain refuses to be built without a reason, and an unrecognised
        value is refused rather than defaulted to complete.
        """
        completeness = schema("receipt.schema.json")["properties"]["completeness"]["enum"]
        self.assertEqual(set(completeness), {"complete", "partial", "uncertain"})
        from web.views import Completeness
        for value in completeness:
            built = Completeness.from_contract(value, missing=["a source"], why="a reason")
            self.assertEqual(built.state, value)

    def test_the_seven_signals_now_drive_the_explanation(self):
        """CHANGE REQUEST 3, CLOSED by this lane rather than left as a note.

        The ranked queue explained its order with one constant sentence printed under every item.
        That is not an explanation of any particular item, and it stays plausible after the ranker
        changes, which is the worst property a status sentence can have. It now reads from the
        item's own declared signals.

        The rule that made it worth building is in `web/views/signals.py`: the ranker may treat an
        unstated stake as high, and the screen may not report that back as something the producer
        said. Every closed value set is asserted against the contract here, so a value added there
        fails this test rather than being silently dropped on a screen.
        """
        from web.views import Signals
        import web.views.signals as sg
        props = schema("work-packet.schema.json")["$defs"]["Signals"]["properties"]
        ranking = {"stakes", "reversibility", "urgency", "dependency_unblocking", "effort",
                   "confidence", "charter_alignment"}
        # `display` is the eighth member and is NOT a ranking signal: it is an object the producer
        # supplies for the inspector. Terminal 14 found that the old assertion here treated the
        # seven as the whole vocabulary, so a packet carrying `display` was refused outright. The
        # assertion now names both sets, and fails if either changes.
        self.assertEqual(set(props), ranking | {"display"})
        self.assertEqual(props["display"]["type"], "object")
        for field, allowed in (("stakes", sg.STAKES), ("reversibility", sg.REVERSIBILITY),
                               ("urgency", sg.URGENCY), ("effort", sg.EFFORT),
                               ("charter_alignment", sg.ALIGNMENT)):
            self.assertEqual(set(props[field]["enum"]), set(allowed),
                             "%s drifted from the contract" % field)
        every = {f: (props[f]["enum"][0] if "enum" in props[f] else 1) for f in ranking}
        every["confidence"] = 0.5
        self.assertEqual(Signals.from_contract(every).undeclared, ())
        # And a packet carrying the eighth member is accepted rather than refused, which is the
        # defect Terminal 14 reported: it reaches the inspector and never the explanation.
        with_display = dict(every, display={"label": "Source status", "note": "fixture source quiet"})
        built = Signals.from_contract(with_display)
        self.assertEqual(built.display, ("Source status", "fixture source quiet"))
        self.assertNotIn("Source status", built.explain())


if __name__ == "__main__":
    unittest.main()
