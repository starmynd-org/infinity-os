"""Put `ingest/` on the path so `capture` imports as a package, the way `connectors` does.

Nothing here reaches a database, a provider or a credential. Every store is a file in pytest's
own tmp_path and every identity is synthetic.
"""

from __future__ import annotations

import pathlib
import sys

# INGEST-COVERAGE-CONFTEST-DEPTH-01. This was `parents[2]`, COPIED from `capture/tests/conftest.py`
# where 2 is right, into a tree one level deeper where it is not. Count from this file:
# 0 tests, 1 coverage, 2 connectors, 3 ingest. The sibling `capture/tests` counts
# 0 tests, 1 capture, 2 ingest -- the same walk, a different answer, which is why the number has to
# be re-derived per file and never carried across a copy.
#
# WHAT THE WRONG NUMBER LOOKED LIKE, and it is why this was worth a repair and not a note. Run alone,
# this suite exited 4 with `ModuleNotFoundError: No module named 'capture'` and NO COUNT LINE, which
# reads as "nothing to run" rather than as a failure. Under a whole-tree run its 34 cases collected
# and passed -- but only because `capture` sorts before `connectors` and had already seeded sys.path.
# So the coverage-and-recovery axis of this subsystem had never once passed on its own, and a rename
# of either directory would have broken the only invocation under which it had ever run.
INGEST_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(INGEST_ROOT) not in sys.path:
    sys.path.insert(0, str(INGEST_ROOT))

import pytest  # noqa: E402

from capture.raw_custody import AesGcmCipher, RawStore  # noqa: E402
from capture.sqlite_store import SqliteJournalStore  # noqa: E402


@pytest.fixture
def custody(tmp_path):
    """Real AES-256-GCM custody. The key is generated per test and never leaves the process."""
    return RawStore(tmp_path / "custody", AesGcmCipher(AesGcmCipher.generate_key()))


@pytest.fixture
def store(tmp_path):
    s = SqliteJournalStore(tmp_path / "journal.sqlite3")
    yield s
    s.close()
