"""THE CANONICAL PAYLOAD: what a connector sends, and what the door turns it into.

Pure functions. No Flask, no `store`, no database, no clock. That is deliberate and it is not
tidiness: every rule in this file is a rule about a MESSAGE, and a rule about a message that can
only be exercised by standing up a web server and a Postgres is a rule that will be exercised
once. `tests/test_payload.py` runs this whole file with neither.

The one thing it will not do is decide what an item MEANS. `CONTRACT.md` states that as a rule
rather than a shortcut, so nothing here classifies, summarises, retitles or drops.

------------------------------------------------------------------------------------------------
THE FOUR FIELDS WITH NO COLUMN, AND WHY THEY GO IN THE BODY
------------------------------------------------------------------------------------------------
`brain.objective` has 24 columns, measured rather than assumed, and there is no `author`, no
`metadata`, no JSON column, no external id and no occurred-at. So `timestamp`, `author`,
`attachments` and `metadata` have nowhere of their own to live.

They are written as a YAML front matter block at the top of `body`. This is not an invention and
not a fudge: it is the mechanism THIS DOOR ALREADY USES. `swarm_engine.cli._declared_origin`
parses `origin:` out of exactly such a block on the filesystem intake path, and the n8n heartbeat
file on disk carries one. `tests/test_payload.py` round-trips what this module writes back
through that function, so the two cannot drift apart silently.

Deterministic key order, `origin` first, and only keys that were supplied. Two consequences worth
stating:

  * `origin` is the FIRST key inside the fence, so `_declared_origin`'s eleven-line window never
    has to reach past the block to find it.
  * Every value except `origin` is JSON-encoded, which is legal YAML and cannot contain a raw
    newline, so no attacker-supplied `author` or `source` can forge a second `origin:` line.
    `origin` itself is one of two literal words and is written bare so that `_declared_origin`
    reads back the same string this module was given.

------------------------------------------------------------------------------------------------
WHY AN UNKNOWN FIELD IS REFUSED RATHER THAN IGNORED
------------------------------------------------------------------------------------------------
`idempotency-key` with a hyphen, `Origin` with a capital, `ts` instead of `timestamp`: each one
is a payload that looks accepted and silently loses the field that was doing the work. The dedup
key is the pointed case, because the consequence is not a missing field, it is a flooded inbox
five minutes later, and `CONTRACT.md` already records that exact defect on the filesystem path.
A typo is cheap to fix and expensive to find, so this refuses with a 400 that names the key.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

#: The fields `CONTRACT.md` requires. Absent, empty, or not a string is a 400 naming the field.
REQUIRED_FIELDS = ("source", "timestamp", "author", "content")

#: Everything else the contract allows. Anything outside these two tuples is refused; see above.
OPTIONAL_FIELDS = ("attachments", "metadata", "origin", "title", "idempotency_key")

KNOWN_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS

#: The fence and the key order of the front matter block. `origin` first is load-bearing: see the
#: module docstring.
FENCE = "---"
FRONT_MATTER_ORDER = ("origin", "source", "timestamp", "author", "attachments", "metadata")

#: Characters kept verbatim in the readable half of a derived name. Everything else becomes `-`.
#: The name's UNIQUENESS never rests on this: it rests on the hash appended after it.
_NAME_SAFE = re.compile(r"[^A-Za-z0-9._:@/-]+")

#: How much of the readable half of a derived name survives. A name is a board label as well as a
#: key, and a 400-character source would make an unreadable one.
_NAME_PREFIX_MAX = 64

#: Hex characters of the derived-name hash. 16 hex is 64 bits: at any inbox size this estate will
#: ever hold, a collision is not the failure mode worth engineering against; a name derived from
#: CONTENT ALONE is, and that is what this avoids by seeding on source plus key.
_NAME_HASH_LEN = 16


class PayloadError(ValueError):
    """One refusal, carrying the field it is about and the status it deserves.

    `field` is not decoration. `CONTRACT.md` promises that a 400 names the exact field and what
    was wrong with it, and a connector author reading a wall of prose to find out which key to
    fix is the reason that promise is in the contract.
    """

    def __init__(self, field: str, message: str, status: int = 400, **extra):
        super().__init__(message)
        self.field = field
        self.message = message
        self.status = status
        self.extra = extra

    def body(self) -> dict:
        out = {"error": self.message, "field": self.field}
        out.update(self.extra)
        return out


@dataclass(frozen=True)
class Payload:
    """One accepted message, normalised. Every derivation below is a pure function of this."""

    source: str
    timestamp: str
    author: str
    content: str
    attachments: list | None = None
    metadata: dict | None = None
    origin: str | None = None
    title: str | None = None
    idempotency_key: str | None = None

    # ---------------------------------------------------------------- what the row is made of

    def canonical(self) -> str:
        """The stable text this payload hashes as. Sorted keys, no whitespace, no clock.

        THE CLOCK IS THE WHOLE POINT. `CONTRACT.md` records the filesystem path's defect: a
        signature derived from `size:mtime` over a file rewritten every five minutes changes on
        every tick, so the dedup that was believed to be redundant was doing nothing. A signature
        derived from here changes only when the MESSAGE changes.

        `timestamp` is included because it is the producer's statement of WHEN THE THING
        HAPPENED, which is stable for a given item. A connector that puts `now()` there is
        putting a generated-at stamp inside the value it dedups on, which is that same defect
        arriving through this door. `idempotency_key` exists so that no connector has to get
        this right, and the contract says to send it.
        """
        return json.dumps(
            {
                "source": self.source,
                "timestamp": self.timestamp,
                "author": self.author,
                "content": self.content,
                "origin": self.origin,
                "title": self.title,
                "attachments": self.attachments,
                "metadata": self.metadata,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def signature(self) -> str:
        """`source_signature` for the transition. The producer's key when there is one.

        The transition dedups on the PAIR `(source_name, source_signature)` and requires both, so
        there is no "no key" path: a payload without an `idempotency_key` gets a content hash,
        which makes a byte-identical repeat dedup exactly as a keyed one would. Different text
        under the same key is a different row by design, and that is why the contract asks for a
        key: it is the only value a connector controls when the text of an item can change.
        """
        if self.idempotency_key:
            return self.idempotency_key
        return "sha256:" + hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()

    def signature_was_derived(self) -> bool:
        return not self.idempotency_key

    def name(self) -> str:
        """`name` for the transition. GLOBALLY unique, because the column is.

        `brain.objective.name` is `text NOT NULL UNIQUE` across the whole table, and the
        transition's second dedup check answers `name already present` on a collision. So a name
        derived from CONTENT ALONE makes two different items from two different producers
        silently become one: the second message does not error, it vanishes into a 200. The seed
        is therefore the source plus the producer's key (or the content hash standing in for it),
        and the readable prefix carries the source so a human on the board can see where a row
        came from.
        """
        if self.title:
            return self.title
        seed = f"{self.source}\x00{self.idempotency_key or self.canonical()}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:_NAME_HASH_LEN]
        return f"{_slug(self.source)}-{digest}"

    def name_was_derived(self) -> bool:
        return not self.title

    def front_matter(self) -> str:
        """The YAML block that carries the four fields with no column. See the module docstring."""
        values = {
            "origin": self.origin,
            "source": self.source,
            "timestamp": self.timestamp,
            "author": self.author,
            "attachments": self.attachments,
            "metadata": self.metadata,
        }
        lines = [FENCE]
        for key in FRONT_MATTER_ORDER:
            value = values[key]
            if value is None:
                continue                      # not supplied is not written; see `origin` below
            if key == "origin":
                # BARE, and the only bare value in the block. `_declared_origin` returns the text
                # after the colon verbatim, so a JSON-quoted `"human"` would read back as
                # `'"human"'` and match neither value the database accepts. The vocabulary is two
                # literal words, so there is nothing here to quote against.
                lines.append(f"{key}: {value}")
            else:
                lines.append(f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
        lines.append(FENCE)
        return "\n".join(lines)

    def body(self) -> str:
        """`body` for the transition: the front matter, a blank line, then the content VERBATIM.

        The content is never reformatted, wrapped, trimmed of its own trailing newline or
        summarised. `CONTRACT.md`: send what arrived, say what it is, never say what it means.
        """
        return f"{self.front_matter()}\n\n{self.content}"

    def body_bytes(self) -> int:
        return len(self.body().encode("utf-8"))


def _slug(text: str) -> str:
    """The readable half of a derived name. Never the unique half."""
    out = _NAME_SAFE.sub("-", text).strip("-")
    out = out[:_NAME_PREFIX_MAX].strip("-")
    return out or "intake"


def _find_nul(value, path: str = "(body)"):
    """The dotted path of the first string holding a NUL, or `None`. Depth-first, keys included.

    Returns a PATH and not a bool because `CONTRACT.md` promises a 400 names the exact field, and
    "something in your payload has a NUL in it" is the shape of error message that sends a
    connector author reading their own JSON by eye.
    """
    if isinstance(value, str):
        return path if "\x00" in value else None
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and "\x00" in key:
                return f"{path}.(a key)"
            found = _find_nul(item, f"{path}.{key}" if path != "(body)" else str(key))
            if found is not None:
                return found
        return None
    if isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_nul(item, f"{path}[{index}]")
            if found is not None:
                return found
        return None
    return None


def parse(raw, *, origins, max_content_bytes: int) -> Payload:
    """Validate one decoded JSON payload, or raise `PayloadError`.

    `origins` and `max_content_bytes` are ARGUMENTS AND NOT CONSTANTS IN THIS FILE, which is the
    point of the whole module boundary. The authority on which origins exist is
    `swarm_engine.transitions.OBJECTIVE_ORIGINS` and the authority on the size limit is
    `intake_service.host.MAX_CONTENT_BYTES`; a second copy here is a second answer, and the one
    that is wrong is the one nobody edited. `app.py` passes both and a test asserts it passes
    the engine's own tuple rather than a lookalike.
    """
    if not isinstance(raw, dict):
        raise PayloadError(
            "(body)",
            "the request body must be a JSON object with the fields named in CONTRACT.md, "
            f"got {type(raw).__name__}.",
        )

    unknown = sorted(k for k in raw if k not in KNOWN_FIELDS)
    if unknown:
        raise PayloadError(
            unknown[0],
            f"unknown field(s): {', '.join(unknown)}. This door refuses what it cannot store "
            f"rather than dropping it silently, because a mistyped `idempotency_key` is not a "
            f"missing field, it is a flooded inbox on your next run. Accepted fields: "
            f"{', '.join(KNOWN_FIELDS)}.",
            unknown_fields=unknown,
        )

    values = {}
    for field_name in REQUIRED_FIELDS:
        if field_name not in raw:
            raise PayloadError(field_name, f"`{field_name}` is required and was not sent.")
        value = raw[field_name]
        if not isinstance(value, str):
            raise PayloadError(
                field_name,
                f"`{field_name}` must be a string, got {type(value).__name__}.",
            )
        if not value.strip():
            raise PayloadError(field_name, f"`{field_name}` is required and was empty.")
        values[field_name] = value

    # THE SIZE IS MEASURED IN BYTES, NOT CHARACTERS. A body of emoji is four times its length in
    # the column, and a limit that counted characters would be a limit that moved with the
    # alphabet. 413 and not 400: the contract gives the remedy, which is to truncate and pass a
    # reference in `attachments`.
    content_bytes = len(values["content"].encode("utf-8"))
    if content_bytes > max_content_bytes:
        raise PayloadError(
            "content",
            f"`content` is {content_bytes} bytes and the limit is {max_content_bytes}. "
            f"Truncate it and pass a reference in `attachments`: bytes do not go through this "
            f"door.",
            status=413,
            content_bytes=content_bytes,
            limit_bytes=max_content_bytes,
        )

    # NO NUL ANYWHERE, AND IT IS A 400 BECAUSE IT IS A FACT ABOUT THE PAYLOAD.
    #
    # Measured live 2026-09-02: a DMARC aggregate report carrying a NUL byte in its body reached
    # this door and it answered **500**. The traceback bottomed out in psycopg2 with `ValueError:
    # A string literal cannot contain NUL (0x00) characters`, raised while ADAPTING THE PARAMETER
    # -- before any SQL was sent, and so it is not a `psycopg2.Error` and never reached the 503
    # arm below. It escaped to Flask as an unhandled exception.
    #
    # A 500 is this door claiming IT broke. It had not: a Postgres `text` value cannot contain a
    # NUL, so the payload was unstorable by construction and no amount of retrying would change
    # that. The distinction is not pedantic, it decides what the caller does. A 500 or a 503 says
    # "retry" and the connector obliges forever, reddening its unit on every pass over one
    # message. A 400 says "this payload, as sent, cannot be stored", which is true, actionable,
    # and terminal.
    #
    # This is checked over the WHOLE raw object rather than over `content` alone. The NUL that was
    # actually seen was in a body, but headers reach columns too, and the front matter carries
    # metadata verbatim into the same `text` column. One check at the door beats five.
    nul_path = _find_nul(raw)
    if nul_path is not None:
        raise PayloadError(
            nul_path,
            f"`{nul_path}` contains a NUL (0x00) character. A Postgres text value cannot hold "
            f"one, so this payload cannot be stored as sent and retrying it will not help. "
            f"Strip NUL bytes before sending; they carry no information in a text body.",
        )

    for field_name in ("title", "idempotency_key"):
        value = raw.get(field_name)
        if value is None:
            continue
        if not isinstance(value, str):
            raise PayloadError(
                field_name, f"`{field_name}` must be a string, got {type(value).__name__}."
            )
        if not value.strip():
            raise PayloadError(
                field_name,
                f"`{field_name}` was sent empty. Omit it and one is derived, or send a stable "
                f"value; an empty one is neither.",
            )
        values[field_name] = value

    origin = raw.get("origin")
    if origin is not None:
        if not isinstance(origin, str):
            raise PayloadError(
                "origin", f"`origin` must be a string, got {type(origin).__name__}."
            )
        normalised = origin.strip().lower()
        if normalised and normalised not in tuple(origins):
            # REFUSED AND NOT FOLDED, which is the transition's own rule mirrored one layer up so
            # the refusal names the field. The fold would be toward `human`, and a machine that
            # meant to be quiet would start showing up on the operator's badge instead.
            raise PayloadError(
                "origin",
                f"`origin` must be one of {', '.join(origins)}, got {origin!r}. An unrecognised "
                f"origin is refused rather than folded: the fold is toward `human`, and a "
                f"machine that meant to be quiet would silently start reaching the operator's "
                f"badge instead.",
                accepted=list(origins),
            )
        # Empty string means the same thing as absent: nobody said. It is not written at all, so
        # the column stays NULL, which is the difference between "nobody said" and "somebody said
        # a person put this here".
        values["origin"] = normalised or None

    attachments = raw.get("attachments")
    if attachments is not None:
        if not isinstance(attachments, list):
            raise PayloadError(
                "attachments",
                f"`attachments` must be an array of objects, got {type(attachments).__name__}.",
            )
        for i, item in enumerate(attachments):
            if not isinstance(item, dict):
                raise PayloadError(
                    "attachments",
                    f"`attachments[{i}]` must be an object like "
                    f'{{"name": ..., "mime": ..., "url": ...}}, got {type(item).__name__}.',
                )
            # EVERY ATTACHMENT MUST POINT AT SOMETHING, and this is the one place it can be
            # caught. Bytes do not go through this door, so an attachment carrying neither `url`
            # nor `asset_ref` is a reference to nothing: the connector believes it handed over a
            # file, the row records a filename, and the file is unreachable forever. That is
            # silent loss, which is worse than a refusal, and it is refused here rather than
            # discovered by whoever eventually clicks the attachment.
            if not str(item.get("url") or "").strip() and not str(item.get("asset_ref") or "").strip():
                raise PayloadError(
                    "attachments",
                    f"`attachments[{i}]` carries neither `url` nor `asset_ref`, so it points at "
                    f"nothing. Bytes do not go through this door: put the file where it belongs "
                    f"and pass a reference. Got keys: {sorted(item)}.",
                )
        values["attachments"] = attachments

    metadata = raw.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict):
            raise PayloadError(
                "metadata",
                f"`metadata` must be an object, got {type(metadata).__name__}. It is stored "
                f"verbatim and never interpreted here.",
            )
        values["metadata"] = metadata

    return Payload(**values)
