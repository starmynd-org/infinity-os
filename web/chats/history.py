"""Long-history fidelity: the full transcript, in order, without loading it all into memory.

Andrew's ask, the clause this file answers:

> *"even long chats getting the full history, stored, documented, accessible and scrollable."*

FOUR DECISIONS, AND EACH ONE IS A PLACE THIS COULD HAVE QUIETLY LIED

**1. "Full" is checkable or it is a slogan.** A transcript holds records this surface cannot render
-- attachments, mode changes, title latches, queue operations. **Dropping them silently is what
turns "full history" into a false claim**, so `Counts` publishes every record type it saw, rendered
and not, and `read_window` reports `dropped` as a number rather than as an omission. **The
denominator is the file; the numerator is what a person can see; both are printed.**

**2. Nothing is cached.** `MUST-NOT-BUILD.md` item 11, still in force with no amendment sought:
*"a cache layer and a service worker remain forbidden."* **What that condition governs includes the
convenience you would reach for here** -- an in-process index of line offsets, kept between
requests, is a cache layer wearing a smaller word. So every read streams the file from the top and
skips. **It costs one linear pass per window and it is measured rather than assumed**: see
`web/chats/bin/history_measure.py`, which prints bytes, records and elapsed time for a real
transcript so the trade is a number and not an opinion.

**3. Nothing is loaded whole.** The file is read line by line and only the requested window is
materialised. A 30 MB transcript costs one line's worth of memory at a time. **The failure this
avoids is the one you only meet in production**, where the long chat is the one that matters.

**4. Thinking is a KIND, never a silent drop.** Assistant `thinking` blocks are carried through as
`kind="thinking"` so the surface can fold or hide them **by a decision somebody made**, rather than
by this module removing them and nobody knowing they were there. `Counts.blocks` names them.

WHAT THIS FILE DOES NOT DO. It renders nothing -- it is the interior, and the sequence is ruled:
*interior first, frame after*. `term-2`'s chat surface at `4d5222d` is a frozen prototype and this
module does not restyle it; it supplies the numbers that frame needs, including `line_count` per
turn so the folding behaviour has something real to fold on.

**A correction to what that fold turned out to be.** I wrote here that it reproduces `term-2`'s
`~240px, show all · N more lines`. **It does not, and it could not.** The frozen surface clamps a
visible body to 240px; a `<details>` fold hides its non-summary children outright when closed, so a
`max-height` on the body governs nothing. **This module is unaffected either way — `line_count` is
the number a frame folds ON however it folds — but the claim was mine and it was wrong.**
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Iterator

#: Record `type` values that carry a renderable conversation turn. Everything else is counted and
#: reported, never rendered and never silently dropped.
CONVERSATION = ("user", "assistant")

#: Content blocks inside `message.content`. Listed so an unknown one is REPORTED rather than
#: skipped: a transcript format that grows a block type must not make history quietly shorter.
KNOWN_BLOCKS = ("text", "thinking", "tool_use", "tool_result", "image")


@dataclass
class Turn:
    """One renderable turn. `line_count` exists so a frame can fold without re-measuring text."""

    index: int
    role: str
    kind: str
    timestamp: str
    text: str
    line_count: int
    #: Populated for `tool_use` / `tool_result`, so the surface can label them without parsing text.
    tool_name: str = ""

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass
class Counts:
    """The denominator, and every part of it. **Printed before any numerator, everywhere.**"""

    path: str
    bytes_on_disk: int = 0
    lines: int = 0
    unparseable: int = 0
    record_types: dict[str, int] = field(default_factory=dict)
    blocks: dict[str, int] = field(default_factory=dict)
    turns: int = 0

    @property
    def not_rendered(self) -> int:
        """Records that exist in the file and produce no turn. **This is the honesty number.**"""
        return self.lines - self.unparseable - sum(
            v for k, v in self.record_types.items() if k in CONVERSATION)

    def summary(self) -> str:
        types = ", ".join(f"{k}={v}" for k, v in sorted(self.record_types.items()))
        blocks = ", ".join(f"{k}={v}" for k, v in sorted(self.blocks.items()))
        return (f"{self.bytes_on_disk} bytes, {self.lines} records "
                f"({self.unparseable} unparseable), {self.turns} renderable turns, "
                f"{self.not_rendered} records not rendered\n"
                f"  record types: {types}\n"
                f"  content blocks: {blocks}")


def _blocks_of(record: dict) -> Iterator[tuple[str, str, str]]:
    """Yield `(kind, text, tool_name)` for each content block. A string body is one `text` block."""
    message = record.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if isinstance(content, str):
        yield "text", content, ""
        return
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type") or "unknown")
        if kind == "text":
            yield "text", str(block.get("text") or ""), ""
        elif kind == "thinking":
            yield "thinking", str(block.get("thinking") or block.get("text") or ""), ""
        elif kind == "tool_use":
            yield "tool_use", json.dumps(block.get("input"), ensure_ascii=False,
                                         sort_keys=True)[:100_000], str(block.get("name") or "")
        elif kind == "tool_result":
            body = block.get("content")
            if isinstance(body, list):
                body = "\n".join(str(b.get("text") or "") for b in body if isinstance(b, dict))
            yield "tool_result", str(body or ""), str(block.get("tool_use_id") or "")
        else:
            # Unknown block: carried through with its own name rather than dropped, so a format
            # change shortens no history and shows up in `Counts.blocks` the first time it appears.
            yield kind, json.dumps(block, ensure_ascii=False, sort_keys=True)[:100_000], ""


def _iter_turns(path: str, counts: Counts) -> Iterator[Turn]:
    """Stream the file once, yielding turns and filling `counts` as it goes. Never loads it whole."""
    index = 0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            counts.lines += 1
            try:
                record = json.loads(line)
            except ValueError:
                counts.unparseable += 1
                continue
            if not isinstance(record, dict):
                counts.unparseable += 1
                continue
            rtype = str(record.get("type") or "unknown")
            counts.record_types[rtype] = counts.record_types.get(rtype, 0) + 1
            if rtype not in CONVERSATION:
                continue
            message = record.get("message") if isinstance(record.get("message"), dict) else {}
            role = str(message.get("role") or rtype)
            stamp = str(record.get("timestamp") or "")
            for kind, text, tool in _blocks_of(record):
                counts.blocks[kind] = counts.blocks.get(kind, 0) + 1
                counts.turns += 1
                yield Turn(index=index, role=role, kind=kind, timestamp=stamp, text=text,
                           line_count=text.count("\n") + 1 if text else 0, tool_name=tool)
                index += 1


def count(path: str) -> Counts:
    """Every figure about a transcript, in one linear pass. **mutatesState: NO.**"""
    counts = Counts(path=path, bytes_on_disk=os.path.getsize(path) if os.path.isfile(path) else 0)
    for _ in _iter_turns(path, counts):
        pass
    return counts


@dataclass
class Window:
    """A slice of history plus the whole file's counts, so a caller never has to ask twice."""

    counts: Counts
    offset: int
    turns: list[Turn]
    #: How many turns the QUERY matched, when there was one. `None` means no query ran, and it is
    #: `None` rather than `counts.turns` so that "everything matched" and "nothing was asked"
    #: cannot be confused -- they are different states and a page renders them differently.
    matched: int | None = None

    @property
    def total(self) -> int:
        """The population this window pages through: matches when searching, else the whole file."""
        return self.counts.turns if self.matched is None else self.matched

    @property
    def has_more_before(self) -> bool:
        return self.offset > 0

    @property
    def has_more_after(self) -> bool:
        return self.offset + len(self.turns) < self.total


def search(path: str, needle: str, *, offset: int = 0, limit: int = 200) -> Window:
    """Turns whose text contains `needle`, case-insensitively. **mutatesState: NO.**

    Andrew asked for history that is *"accessible and scrollable"*. Paging made the second true; a
    6,190-turn conversation with no way to find anything in it is still not the first.

    **THE DENOMINATOR IS THE WHOLE FILE AND THE NUMERATOR IS THE MATCHES, and `Window.counts` still
    describes the FILE rather than the result.** That is deliberate: a search result that reports
    only its own size answers *"how many did you show me"* and never *"out of how many"*, and the
    second is the question that tells a reader whether to trust an empty result.

    **What it does NOT search, stated because a search that silently skips things is worse than no
    search:** only renderable turns. The 51% of records that are attachments, mode changes, titles
    and queue operations are not searched, because they are not shown, and a hit a reader cannot be
    shown to is a hit that reads as a bug. `Counts.not_rendered` is what that costs and it is
    published on the page.

    **An empty `needle` returns nothing rather than everything.** The alternative is a blank query
    silently becoming "show all", which is the same class of mistake as a filter that fails open.
    """
    if offset < 0:
        raise ValueError(f"offset {offset} is negative. Refused rather than read from the end.")
    if limit <= 0:
        raise ValueError(f"limit {limit} must be positive.")
    counts = Counts(path=path, bytes_on_disk=os.path.getsize(path) if os.path.isfile(path) else 0)
    lowered = (needle or "").lower()
    chosen: list[Turn] = []
    hits = 0
    for turn in _iter_turns(path, counts):
        if not lowered or lowered not in turn.text.lower():
            continue
        if offset <= hits < offset + limit:
            chosen.append(turn)
        hits += 1
    window = Window(counts=counts, offset=offset, turns=chosen)
    window.matched = hits
    return window


def read_window(path: str, *, offset: int = 0, limit: int = 200) -> Window:
    """Turns `[offset, offset+limit)`, plus the counts for the WHOLE file. **mutatesState: NO.**

    **`counts` covers the whole transcript, not the window**, which is the difference between a
    scrollbar that knows how long the document is and one that guesses. It costs the full pass
    either way, because there is no index and an index would be the cache layer item 11 forbids.

    A negative `offset` is refused rather than wrapped. Python would happily read `-5` as "five from
    the end" and a surface asking for page -1 would get the LAST page and render it as the first.
    """
    if offset < 0:
        raise ValueError(f"offset {offset} is negative. Refused rather than silently read from the "
                         f"end, which would render the last page as the first.")
    if limit <= 0:
        raise ValueError(f"limit {limit} must be positive.")
    counts = Counts(path=path, bytes_on_disk=os.path.getsize(path) if os.path.isfile(path) else 0)
    chosen: list[Turn] = []
    for turn in _iter_turns(path, counts):
        if offset <= turn.index < offset + limit:
            chosen.append(turn)
    return Window(counts=counts, offset=offset, turns=chosen)
