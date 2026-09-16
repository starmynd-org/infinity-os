"""The message on the wire, where the wire is a directory.

Four properties, and each one is here because its absence has already cost this operation
something on the bus this fleet is running on right now:

1. **A message is placed ATOMICALLY.** Written to `<name>.part` in the same directory, then
   `os.replace`d into its final name. Same volume, so the rename is atomic on both NT and POSIX.
   A reader listing the directory sees the file only once it is complete.

2. **A message carries a TERMINATOR**, `---END---` on its own line, and a reader that does not find
   one treats the file as mid-write, skips it, and re-reads next cycle. `BUS-PROTOCOL` §2:
   *"Without it you will eventually read half a message and act on it, and the failure is silent
   because a half-message is still valid text."* Atomic placement makes this a second belt rather
   than the only one. **Both, not either** -- the atomic write protects against a half-written file
   and the terminator protects against a sender that crashed mid-compose and wrote a whole one.

3. **The filename stamp is UTC, read at placement time.** This machine's local clock is UTC+3 and
   `git log` and `ps` both render local time. A stamp copied out of another tool's output does not
   mislabel a message, **it reverses it** -- two such files on the fleet bus are their sender's
   oldest and sort below its newest. So the stamp is read here, in the function that does the
   rename, and never passed in.

4. **The sender's identity is NOT read from the message.** `from_seat` in the body is a courtesy
   label for a human reading the file. Every caller that needs to know who sent something gets it
   from `Delivered.mailbox` -- the directory the file was found in. See `roundtrip.py`.

A note on the format. It is the fleet bus's format on purpose, line for line, so that a person who
can read one can read the other and so that `bus-wait.sh`'s terminator check works unmodified
against these directories. It is not JSON because the primary reader is a person diagnosing a
stuck agent at 7am, and this operation has a standing preference for a record a later seat can
reconstruct from.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Iterator

#: On its own line, and the last non-empty line of a complete message.
TERMINATOR = "---END---"

#: Suffix a message wears while it is being written. Never listed as a message: the scan globs
#: `*.md`, and this is not one, which is a second reason a half-written file is invisible.
PART_SUFFIX = ".part"

#: Every field the header must carry. Missing one is a malformed message, and malformed is a
#: REFUSAL rather than a best-effort parse -- a header this small has no ambiguity worth guessing
#: at, and a guess here becomes an action taken on a message nobody sent.
REQUIRED = ("FROM", "TO", "SUBJECT", "KIND", "NEEDS-REPLY")

#: The kinds this package's own round trip uses. `roundtrip.py` refuses an unknown kind rather than
#: passing it through, for the reason `rooms.py` refuses an unregistered verb: an allowlist that
#: forwards what it does not recognise is not an allowlist.
KINDS = ("REGISTER", "REGISTERED", "TASK", "REPORT", "STATUS", "REFUSED", "BLOCKED", "QUEUE-EMPTY")

_HEADER_LINE = re.compile(r"^([A-Z][A-Z0-9-]*):[ \t]*(.*)$")

#: Filenames this package writes and reads. Anchored at both ends: a pattern that only anchors at
#: the start would accept `<stamp>-from-x.md.bak` and hand a backup file to a dispatcher.
_NAME = re.compile(r"^(\d{8}T\d{6}Z)-([0-9a-f]{8})-from-(.+)\.md$")


def utc_stamp() -> str:
    """`yyyymmddTHHMMSSZ`, from the clock, in UTC, at the moment of the call.

    Read here and nowhere else. No caller may pass a stamp in, because every stamping failure this
    operation has had was a correct timestamp read from the wrong clock and carried.
    """
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class Message:
    """A parsed message. `headers` keeps every header, `body` is everything after the blank line."""

    headers: dict[str, str]
    body: str
    #: Set by `scan()` to the directory the file was found in. **This, not `headers['FROM']`, is
    #: the sender's identity.** `None` on a message that was composed rather than read.
    mailbox: str | None = None
    #: Set by `scan()` to the file's own path, so a caller can move it to `_done/` without
    #: rebuilding the name and getting it subtly wrong.
    path: str | None = None

    @property
    def kind(self) -> str:
        return self.headers.get("KIND", "")

    @property
    def subject(self) -> str:
        return self.headers.get("SUBJECT", "")

    @property
    def claimed_sender(self) -> str:
        """What the message SAYS it is from. Named `claimed_` so no call site forgets which it has.

        Compare `Message.mailbox`, which is where it actually came from. The naming is the control:
        a field called `sender` would be trusted by someone in a hurry, and this one cannot be read
        without noticing what it is.
        """
        return self.headers.get("FROM", "")


class Malformed(ValueError):
    """A message that cannot be parsed. Carries the reason in the words of what is missing."""


def format_message(*, from_seat: str, to: str, subject: str, kind: str,
                   needs_reply: bool, body: str, extra: dict[str, str] | None = None) -> str:
    """Render a complete message, terminator included. Pure: touches no clock and no filesystem."""
    if kind not in KINDS:
        raise Malformed(
            f"kind {kind!r} is not one of {', '.join(KINDS)}. This is refused rather than passed "
            f"through: an allowlist that forwards what it does not recognise is not an allowlist."
        )
    if "\n" in subject:
        raise Malformed("SUBJECT is one line. A newline in it would be read as the next header.")
    lines = [
        f"FROM: {from_seat}",
        f"TO: {to}",
        f"SUBJECT: {subject}",
        f"KIND: {kind}",
        f"NEEDS-REPLY: {'yes' if needs_reply else 'no'}",
    ]
    for key, value in (extra or {}).items():
        if not _HEADER_LINE.match(f"{key}: {value}"):
            raise Malformed(f"header name {key!r} must be UPPER-CASE with hyphens, e.g. RE-TASK.")
        lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n\n" + body.rstrip("\n") + "\n\n" + TERMINATOR + "\n"


def parse(text: str) -> Message:
    """Parse a complete message. **Refuses one without the terminator**, which is the whole point.

    The terminator check is first, before any header is read, because a half-written message's
    headers parse perfectly -- they are the part that was written first.
    """
    stripped = [ln.rstrip("\r") for ln in text.splitlines()]
    tail = [ln for ln in stripped if ln.strip()]
    if not tail or tail[-1] != TERMINATOR:
        raise Malformed(
            f"no {TERMINATOR!r} on its own line at the end. This message is still being written, "
            f"or its sender omitted the terminator. It is NOT an empty message and it is NOT a "
            f"malformed one until a second read says so -- skip it and re-read next cycle."
        )
    headers: dict[str, str] = {}
    idx = 0
    for idx, line in enumerate(stripped):
        if not line.strip():
            break
        match = _HEADER_LINE.match(line)
        if not match:
            raise Malformed(f"line {idx + 1} is not a header and the blank line has not come yet: "
                            f"{line!r}")
        headers[match.group(1)] = match.group(2).strip()
    else:
        raise Malformed("no blank line after the headers, so this message has no body.")
    missing = [k for k in REQUIRED if k not in headers]
    if missing:
        raise Malformed(f"missing required header(s): {', '.join(missing)}")
    body_lines = stripped[idx + 1:]
    while body_lines and body_lines[-1].strip() in ("", TERMINATOR):
        body_lines.pop()
    return Message(headers=headers, body="\n".join(body_lines))


def place(directory: str, *, from_seat: str, text: str) -> str:
    """Write `text` into `directory` atomically, under a UTC-stamped name. Returns the full path.

    The stamp is read INSIDE this function, in the same call that performs the rename, which is the
    fleet-wide fix for the reversed-ordering failure. The `uuid4` slice is not decoration: two
    messages placed in the same second would otherwise collide on the name and the second would
    silently replace the first.
    """
    if TERMINATOR not in text:
        raise Malformed(f"refusing to place a message with no {TERMINATOR!r}. A reader would skip "
                        f"it forever and the sender would never learn why.")
    os.makedirs(directory, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", from_seat) or "unknown"
    name = f"{utc_stamp()}-{uuid.uuid4().hex[:8]}-from-{safe}.md"
    final = os.path.join(directory, name)
    part = final + PART_SUFFIX
    with open(part, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(part, final)
    return final


@dataclass
class Scan:
    """What one pass over a mailbox found. **`skipped_incomplete` is not zero-by-assumption.**

    It is reported rather than swallowed because a mailbox that is never empty and never delivers
    is the exact shape of a sender that has stopped writing terminators, and a scanner that reports
    only what it delivered makes that indistinguishable from an idle queue.
    """

    delivered: list[Message] = field(default_factory=list)
    skipped_incomplete: list[str] = field(default_factory=list)
    malformed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def loud_empty(self) -> str | None:
        """The sentence `bus-wait.sh` prints, and the reason it exists. `None` when truly empty.

        *"A timer that reports 'empty' over a full inbox is the exact instrument failure this fleet
        keeps paying for, and it would be invisible."*
        """
        if self.delivered:
            return None
        stuck = len(self.skipped_incomplete) + len(self.malformed)
        if not stuck:
            return None
        return (f"{stuck} file(s) present, none deliverable. This is NOT an empty mailbox. "
                f"Either a sender is mid-write, or a sender is omitting {TERMINATOR!r}.")


def scan(directory: str) -> Scan:
    """One pass over a mailbox, oldest name first. Reads; moves nothing; changes nothing.

    Declared first, because `SEAT-COMMON` §7 asks for it as the first field of anything that
    mutates: **mutatesPage / mutatesState: NO.** Draining is `mark_done()`, and it is separate on
    purpose so that a caller that only wants to look cannot accidentally consume.
    """
    out = Scan()
    if not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".md") or not _NAME.match(name):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            out.malformed.append((path, f"unreadable: {exc}"))
            continue
        try:
            msg = parse(text)
        except Malformed as exc:
            if TERMINATOR not in text:
                out.skipped_incomplete.append(path)
            else:
                out.malformed.append((path, str(exc)))
            continue
        msg.mailbox = directory
        msg.path = path
        out.delivered.append(msg)
    return out


def iter_complete(directory: str) -> Iterator[Message]:
    """`scan().delivered` as an iterator, for callers that want only the happy path."""
    yield from scan(directory).delivered


def mark_done(message: Message) -> str:
    """MOVE a processed message into `<mailbox>/_done/`. **Never deletes. Returns the new path.**

    `BUS-PROTOCOL` §3 step 4: *"Never delete a message; the record is how a later seat reconstructs
    what happened."* And the failure mode that rule exists for, seen on this fleet's own bus today:
    a bulk `find -exec mv` swept a message that arrived between the read and the drain into `_done`
    UNREAD, which destroys the one distinction `_done` exists to preserve. **So this takes one
    message that a caller has already read, never a directory.**
    """
    if not message.path:
        raise ValueError("this message was composed, not read, so there is no file to move.")
    done = os.path.join(os.path.dirname(message.path), "_done")
    os.makedirs(done, exist_ok=True)
    target = os.path.join(done, os.path.basename(message.path))
    os.replace(message.path, target)
    message.path = target
    return target
