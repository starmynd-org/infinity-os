"""Connection and host identity for the ingest lane.

Two things are resolved here and nowhere else, so that repointing to D1's schema when
crosstalk slot 1 lands is a one-file change:

  BRAIN_DSN     which database the verbs write to
  BRAIN_SCHEMA  which schema inside it

Slot 1 landed, so both now default to D1's `brain` database and `brain` schema, selected by
`profiles.profile()` and not by a second copy of the rule here. `BRAIN_PROFILE=scratch` still
points them back at `d3_scratch.ingest` (task 0228).

Reading the profile through `profiles.profile()` rather than re-testing the env var also ends
a divergence this file used to carry: `BRAIN_PROFILE=barin` raised in `profiles` and silently
meant "scratch" here, so a typo could send the verbs' SQL and their connection to two
different schemas.
"""

from __future__ import annotations

import os
import subprocess
import platform
from functools import lru_cache

from . import profiles

SCRATCH_DB = "d3_scratch"
SCRATCH_SCHEMA = "ingest"
BRAIN_DB = "brain"
BRAIN_SCHEMA = "brain"

# The container D1 provisioned. Named here only so the password can be resolved from the
# live container rather than committed to the repo.
PG_CONTAINER = os.environ.get("BRAIN_PG_CONTAINER", "brain-postgres")
PG_HOST = os.environ.get("BRAIN_PG_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("BRAIN_PG_PORT", "5432"))
PG_USER = os.environ.get("BRAIN_PG_USER", "postgres")


@lru_cache(maxsize=1)
def _password_from_container() -> str:
    """Read POSTGRES_PASSWORD off the running container.

    v1 is local and the store has no secret manager yet. Resolving at runtime keeps the
    password out of the repo; when D1 publishes per-role connection strings this whole
    function is replaced by reading them.
    """
    out = subprocess.run(
        ["docker", "inspect", PG_CONTAINER, "--format", "{{range .Config.Env}}{{println .}}{{end}}"],
        capture_output=True, text=True, timeout=20,
    )
    if out.returncode != 0:
        raise RuntimeError(f"cannot inspect container {PG_CONTAINER}: {out.stderr.strip()}")
    for line in out.stdout.splitlines():
        if line.startswith("POSTGRES_PASSWORD="):
            return line.split("=", 1)[1]
    raise RuntimeError(f"POSTGRES_PASSWORD not present in {PG_CONTAINER} env")


# A hook runs on the operator's session-start path. If the store is down, the hook must give
# up in seconds and let the session proceed, not sit in a TCP retry. Measured: without this,
# a connect to a closed port took over 100 seconds before returning.
CONNECT_TIMEOUT_S = int(os.environ.get("BRAIN_PG_CONNECT_TIMEOUT", "5"))


def transcript_retention_days() -> int:
    """How old a session must be before its transcript file going away is expected.

    This lane does not delete transcripts; the harness does, and this is D3's record of the
    horizon it observed the harness deleting on. It exists so that `transcript verify` can say
    whether a gone file aged out or vanished early, which is the whole difference between a
    line in a report and an incident.

    30 is MEASURED, not assumed (task 0324, 2026-08-17). All 61 dead pointers in the live store
    belonged to sessions started 2026-07-15..07-17 and none to any later day; the oldest file
    still on disk was dated 2026-07-19. A hard cut, 100% gone on one side and 0% on the other,
    sitting 30 days back from the day it was measured. `~/.claude/settings.json` carries no
    `cleanupPeriodDays`, so this is the harness default doing it and not an operator setting
    this lane could read.

    Override when the operator changes that setting. The horizon in force is copied onto every
    `ingest.transcript_absence` row at classification time, so raising it here never restates
    what an older row meant.
    """
    return int(os.environ.get("BRAIN_TRANSCRIPT_RETENTION_DAYS", "30"))


def dsn(database: str | None = None) -> str:
    explicit = os.environ.get("BRAIN_DSN")
    if explicit and database is None:
        return explicit
    default_db = BRAIN_DB if profiles.is_brain() else SCRATCH_DB
    db = database or os.environ.get("BRAIN_DB", default_db)
    pw = os.environ.get("BRAIN_PG_PASSWORD") or _password_from_container()
    return (f"host={PG_HOST} port={PG_PORT} user={PG_USER} password={pw} dbname={db} "
            f"connect_timeout={CONNECT_TIMEOUT_S}")


def schema() -> str:
    default = BRAIN_SCHEMA if profiles.is_brain() else SCRATCH_SCHEMA
    return os.environ.get("BRAIN_SCHEMA", default)


@lru_cache(maxsize=1)
def host_id() -> str:
    """Stable id for the machine a filesystem path is absolute *on*.

    D3 trap: a transcript pointer such as /mnt/c/Users/... is only meaningful on this WSL
    instance. Storing it without saying which host it is absolute on means a later VPS move
    breaks every pointer silently. Every `transcript` row carries this.
    """
    override = os.environ.get("BRAIN_HOST_ID")
    if override:
        return override
    node = platform.node() or "unknown"
    kind = "wsl" if "microsoft" in platform.release().lower() else "linux"
    try:
        with open("/etc/machine-id") as fh:
            mid = fh.read().strip()[:12]
    except OSError:
        mid = "nomachineid"
    return f"{kind}:{node}:{mid}"


def path_class(path: str) -> str:
    """Which filesystem class a path lives on, so a mover knows what breaks."""
    p = os.path.abspath(path)
    if p.startswith("/mnt/") and len(p) > 6 and p[6] == "/":
        return "wsl-drvfs"      # a Windows drive seen through WSL; not portable
    return "wsl-ext4"           # native Linux filesystem inside the distro


def windows_path(path: str) -> str | None:
    """The Windows-side spelling of a drvfs path, or None if it has none.

    Recorded alongside, never instead of, the WSL path.
    """
    p = os.path.abspath(path)
    if path_class(p) != "wsl-drvfs":
        return None
    drive = p[5]
    rest = p[7:].replace("/", "\\")
    return f"{drive.upper()}:\\{rest}"
