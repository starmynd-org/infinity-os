"""Put `ingest/` on the path so `capture` imports as a package, the way `connectors` does.

Nothing here reaches a database, a provider or a credential. Every store is a file in pytest's
own tmp_path and every identity is synthetic.
"""

from __future__ import annotations

import pathlib
import sys

INGEST_ROOT = pathlib.Path(__file__).resolve().parents[2]
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
