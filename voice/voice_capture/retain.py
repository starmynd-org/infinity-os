"""The upload: moving the audio out of a volatile place into a durable one, and proving it moved.

"Upload" is the brief's word and it is the right one even though nothing here leaves the machine
today. What the stage means is the blob crossing from somewhere that will be swept -- a temp
directory -- to somewhere the operator can still open it next month. Every property that makes a
remote upload fail applies to it: the destination can be absent, unwritable, full, or reachable
right up until the moment it is not.

WHY IT IS ITS OWN STAGE WITH ITS OWN FAILURE. On 2026-08-18 this host lost its network from
about 01:00Z to 07:00Z. Thirty swarm tasks stopped. Nothing raised, nothing paged, and it was
found by a human six hours later -- because work that has BEGUN and not FINISHED throws no
exception anywhere. That is the same silence as the 0-byte save, and it is why the failure here
is not "catch the exception": the exception is the easy half. The hard half is the capture that
never gets as far as raising one, and that is handled by `due_at` in the store rather than here.

WHAT MAKES THE TRANSFER PROVEN RATHER THAN ASSUMED: the destination is re-hashed after the move
and compared to the source's hash, and the byte counts are compared too. `shutil.copy2` returning
without raising is not evidence that the bytes arrived; a short write on a full disk is exactly
the case where it does.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import config


class RetainFailed(RuntimeError):
    """The audio is not durably where it says it is. Never swallowed, always counted."""


@dataclass
class Retained:
    path: Path
    bytes: int
    sha256: str
    host: str
    kind: str


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def retain(src: Path, capture_id: str, *, retain_dir: Path = None, day: str = None) -> Retained:
    """Copy the audio to the retain directory, verify it there, then drop the scratch copy.

    COPY-VERIFY-DELETE, never move. `shutil.move` across a filesystem boundary is a copy followed
    by an unlink it performs for you, and it performs the unlink whether or not you have looked
    at what arrived. Doing it in three steps means the scratch copy still exists at the moment
    the destination is checked, so a failed transfer costs nothing at all.
    """
    dest_dir = Path(retain_dir or config.RETAIN_DIR) / (day or capture_id[3:11])
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RetainFailed(
            f"the retain directory {dest_dir} could not be created ({exc.__class__.__name__}: "
            f"{exc}). The audio is still at {src} and has NOT been lost -- but it is in a temp "
            f"directory that gets swept, so this is a countdown, not a resting state.") from None

    if not os.access(dest_dir, os.W_OK):
        raise RetainFailed(
            f"the retain directory {dest_dir} exists and is not writable by this process. Same "
            f"countdown: the audio is at {src}, in a temp directory.")

    src_bytes = src.stat().st_size
    src_sha = sha256_of(src)
    dest = dest_dir / f"{capture_id}.wav"

    try:
        shutil.copy2(src, dest)
    except OSError as exc:
        raise RetainFailed(
            f"copying {src} to {dest} failed ({exc.__class__.__name__}: {exc}). "
            f"The scratch copy is intact.") from None

    # THE PART THAT IS NOT DECORATION. A copy that returned without raising can still have
    # written short -- a full filesystem is the ordinary way -- and the only way to find out is
    # to measure what arrived rather than to trust that something did.
    if not dest.exists():
        raise RetainFailed(
            f"the copy to {dest} returned without error and there is no file there. "
            f"That is the 2026-08-17 shape one filesystem later.")
    dest_bytes = dest.stat().st_size
    dest_sha = sha256_of(dest)
    if dest_bytes != src_bytes or dest_sha != src_sha:
        raise RetainFailed(
            f"the retained copy does not match the source. src {src_bytes} bytes "
            f"sha256 {src_sha[:16]}..., dest {dest_bytes} bytes sha256 {dest_sha[:16]}... . "
            f"Compared: 2 of 2 (byte count and full-file digest). The scratch copy is intact at "
            f"{src} and the bad destination is left in place for inspection rather than deleted.")

    # Only now. The scratch copy is the fallback and it is not dropped until the destination has
    # been read back and matched.
    try:
        src.unlink()
    except OSError:
        # Not a failure of the retention: the durable copy is verified. Left to the temp sweep.
        pass

    return Retained(path=dest, bytes=dest_bytes, sha256=dest_sha, host=config.host(),
                    kind=config.MEDIA_KIND)
