"""Raw custody: the original bytes, content-addressed, encrypted, and durable before anything else.

ORDER OF OPERATIONS IS THE WHOLE DESIGN. Raw bytes land and fsync BEFORE the journal row is
written, and the journal row commits BEFORE a receipt exists. That ordering makes the only
possible crash residue an *orphan blob* -- bytes on disk that no journal row references -- which
costs disk and loses nothing. The opposite ordering makes the residue a journal row pointing at
bytes that were never written, which is unrecoverable evidence loss.

WHY CONTENT ADDRESSING. The path IS the digest, so a redelivery of identical bytes writes the
same path and the store is idempotent for free. It also means a blob cannot be silently swapped:
`get` re-hashes on read and raises rather than hand back bytes that no longer match their name.

ENCRYPTION IS A PORT, AND THE DEFAULT REFUSES. `NullCipher` exists for tests that are explicitly
about something else, and `RawStore` will not accept it unless `allow_plaintext=True` is passed at
construction. That is deliberate: "we forgot to configure encryption" should be a loud constructor
error in a build, not a quiet plaintext mailbox archive on someone's laptop.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .contracts import RawCustodyRef, digest_of_bytes

#: Custody refuses a blob larger than this. A connector that needs more must stream to a
#: provider-side reference and capture the reference, not inline the bytes.
DEFAULT_MAX_BLOB_BYTES = 64 * 1024 * 1024

#: Media types custody will store inline. Everything else is refused with a reason so the
#: connector dead-letters rather than guessing. This is a capture-side control, and it is not a
#: substitute for the fetch-side guard in `fetch_guard.py`.
DEFAULT_ALLOWED_MEDIA = frozenset(
    {
        "text/plain",
        "text/html",
        "text/markdown",
        "text/vtt",
        "application/json",
        "application/pdf",
        "message/rfc822",
        "image/png",
        "image/jpeg",
        "audio/mpeg",
        "audio/mp4",
        "audio/wav",
        "video/mp4",
        "application/octet-stream",
    }
)


class CustodyError(RuntimeError):
    """Custody refused or failed. The caller must not proceed to a journal write."""


class CustodyIntegrityError(CustodyError):
    """Stored bytes no longer hash to their own name. Never return them to a caller."""


class Cipher(Protocol):
    """The encryption seam. Real deployments supply an operator-controlled key manager."""

    name: str

    def encrypt(self, plaintext: bytes, aad: bytes) -> bytes: ...

    def decrypt(self, ciphertext: bytes, aad: bytes) -> bytes: ...


class NullCipher:
    """No encryption. Only usable when `RawStore(allow_plaintext=True)` says so out loud."""

    name = "null"

    def encrypt(self, plaintext: bytes, aad: bytes) -> bytes:
        return plaintext

    def decrypt(self, ciphertext: bytes, aad: bytes) -> bytes:
        return ciphertext


class AesGcmCipher:
    """AES-256-GCM with the blob digest as additional authenticated data.

    Binding the AAD to the digest means a ciphertext cannot be moved to a different blob name and
    still decrypt: relocation is detected as a tag failure, not as silently wrong plaintext.
    """

    name = "aes-256-gcm"

    def __init__(self, key: bytes):
        if len(key) != 32:
            raise CustodyError("AES-256-GCM requires a 32-byte key")
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise CustodyError(
                "cryptography is required for encrypted custody; refusing to fall back to plaintext"
            ) from exc
        self._aesgcm = AESGCM(key)

    @staticmethod
    def generate_key() -> bytes:
        return secrets.token_bytes(32)

    def encrypt(self, plaintext: bytes, aad: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + self._aesgcm.encrypt(nonce, plaintext, aad)

    def decrypt(self, ciphertext: bytes, aad: bytes) -> bytes:
        if len(ciphertext) < 13:
            raise CustodyIntegrityError("ciphertext too short to contain a nonce and tag")
        nonce, body = ciphertext[:12], ciphertext[12:]
        try:
            return self._aesgcm.decrypt(nonce, body, aad)
        except Exception as exc:
            raise CustodyIntegrityError(f"custody blob failed authentication: {exc}") from exc


@dataclass(frozen=True)
class CustodyPolicy:
    max_blob_bytes: int = DEFAULT_MAX_BLOB_BYTES
    allowed_media: frozenset[str] = DEFAULT_ALLOWED_MEDIA


def _fsync_dir(path: Path) -> None:
    """A rename is not durable until the DIRECTORY entry is fsynced. Omitting this is the classic
    way a content-addressed store loses a blob to a power cut while passing every unit test."""
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError:
        # Some filesystems (and Windows via WSL interop on /mnt) refuse directory fsync. The
        # write itself was already fsynced; record the weaker guarantee rather than pretend.
        pass
    finally:
        os.close(fd)


class RawStore:
    """Content-addressed, encrypted-at-rest custody on a local filesystem."""

    def __init__(
        self,
        root: str | Path,
        cipher: Cipher,
        *,
        policy: CustodyPolicy | None = None,
        allow_plaintext: bool = False,
    ):
        if isinstance(cipher, NullCipher) and not allow_plaintext:
            raise CustodyError(
                "refusing NullCipher without allow_plaintext=True: raw custody holds private "
                "mail, transcripts and attachments"
            )
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.cipher = cipher
        self.policy = policy or CustodyPolicy()

    def _path_for(self, raw_digest: str) -> Path:
        hexpart = raw_digest.split(":", 1)[-1]
        return self.root / hexpart[:2] / hexpart[2:4] / hexpart

    def put(self, blob: bytes, media_type: str) -> RawCustodyRef:
        """Durably store bytes and return the reference. fsynced before it returns."""
        if not isinstance(blob, (bytes, bytearray)):
            raise CustodyError("custody stores bytes; encode before calling put")
        if len(blob) > self.policy.max_blob_bytes:
            raise CustodyError(
                f"blob of {len(blob)} bytes exceeds custody limit {self.policy.max_blob_bytes}"
            )
        if media_type not in self.policy.allowed_media:
            raise CustodyError(f"media type {media_type!r} is not accepted by custody policy")

        raw_digest = digest_of_bytes(bytes(blob))
        target = self._path_for(raw_digest)
        if target.exists():
            # Idempotent: identical bytes, identical name. Nothing to do and nothing to corrupt.
            return RawCustodyRef(
                raw_digest=raw_digest,
                byte_len=len(blob),
                media_type=media_type,
                encrypted=not isinstance(self.cipher, NullCipher),
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self.cipher.encrypt(bytes(blob), raw_digest.encode("ascii"))
        tmp = target.parent / f".{target.name}.{secrets.token_hex(8)}.part"
        try:
            with open(tmp, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
            _fsync_dir(target.parent)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise CustodyError(f"custody write failed: {exc}") from exc

        return RawCustodyRef(
            raw_digest=raw_digest,
            byte_len=len(blob),
            media_type=media_type,
            encrypted=not isinstance(self.cipher, NullCipher),
        )

    def get(self, raw_digest: str) -> bytes:
        """Return the original bytes, re-verifying the digest. Raises rather than return doubt."""
        target = self._path_for(raw_digest)
        if not target.exists():
            raise CustodyError(f"custody has no blob {raw_digest}")
        stored = target.read_bytes()
        plaintext = self.cipher.decrypt(stored, raw_digest.encode("ascii"))
        actual = digest_of_bytes(plaintext)
        if actual != raw_digest:
            raise CustodyIntegrityError(
                f"custody blob {raw_digest} now hashes to {actual}; refusing to return it"
            )
        return plaintext

    def has(self, raw_digest: str) -> bool:
        return self._path_for(raw_digest).exists()

    def iter_digests(self):
        """Every blob currently held. Used by reconciliation to find orphans after a crash."""
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and not path.name.startswith("."):
                yield "sha256:" + path.name

    def forget(self, raw_digest: str) -> bool:
        """Erase bytes for a retention or deletion request. The JOURNAL ROW SURVIVES.

        Forgetting content is a retention action; forgetting that an event happened is evidence
        destruction. Only the first is available here.
        """
        target = self._path_for(raw_digest)
        if not target.exists():
            return False
        target.unlink()
        return True

    def is_plaintext(self) -> bool:
        return isinstance(self.cipher, NullCipher)
