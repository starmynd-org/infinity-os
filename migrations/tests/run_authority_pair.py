"""2026-09-09-IOS-term-5: run the real suite with one reversible in-process writer plant."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
DB = os.environ.get("ENGINE_SCRATCH_DB", "")
if "scratch" not in DB or DB in ("brain", "brain_scratch"):
    raise SystemExit("scratch database required")
os.environ["BRAIN_PG_DB"] = DB
os.environ["BRAIN_AFTER_COMMIT_HOOKS"] = "0"
import pytest
import store.authority
from store import transitions

if "--plant-old-writer" in sys.argv:
    transitions._REGISTRY["approval decide"].role = "runtime"
    print("PLANTED OLD WRITER IN THIS PROCESS: approval decide role=runtime; no source edited", flush=True)
raise SystemExit(pytest.main([str(ROOT / "store/test_authority.py"), "-q", "-p", "no:cacheprovider"]))
