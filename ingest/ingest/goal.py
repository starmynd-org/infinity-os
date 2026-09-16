"""Can `stated_goal` be recovered from a transcript, or must it be captured at session start?

The rule this module is built around: **a fabricated goal is worse than a null one**, because
a null is visibly missing and a fabrication gets believed. So every value returned here is
either a verbatim quote from the transcript or a string the harness itself wrote. Nothing is
paraphrased, summarised or guessed, and a session with no recoverable goal returns None.

Four tiers, in the order they are tried:

  A  explicit-brief     the first user turn opens with an assignment. Recovered by quoting
                        its first meaningful line. Deterministic, no model involved.
  B  harness-title      the file carries an `ai-title` record. Claude Code wrote it, not
                        this lane. Honest but retrospective: it describes what the session
                        turned out to be about, which is not always what was asked.
  C  away-summary       a `system/away_summary` record. Same caveat as B, more detail.
  D  none               the first user turn is a greeting, a bare slash command, or too
                        short to carry an object. Returns None. This is the correct answer,
                        not a failure of the extractor.
"""

from __future__ import annotations

import re

TIER_BRIEF = "A:explicit-brief"
TIER_TITLE = "B:harness-title"
TIER_AWAY = "C:away-summary"
TIER_NONE = "D:none"

# Wrappers the harness injects into the first user message. None of them is the operator
# speaking, so none of them can be the goal.
_STRIP_BLOCKS = [
    re.compile(r"<system-reminder>.*?</system-reminder>", re.S | re.I),
    re.compile(r"<command-message>.*?</command-message>", re.S | re.I),
    re.compile(r"<command-name>.*?</command-name>", re.S | re.I),
    re.compile(r"<command-args>.*?</command-args>", re.S | re.I),
    re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.S | re.I),
    re.compile(r"<user-prompt-submit-hook>.*?</user-prompt-submit-hook>", re.S | re.I),
    re.compile(r"<local-command-caveat>.*?</local-command-caveat>", re.S | re.I),
    re.compile(r"Caveat:.*?</command-message>", re.S),
]

# Lines that are structure, not content. Every entry here was added because a hand-read of
# a real sample found it standing in for a goal, and each is listed with what it displaced.
_SKIP_LINE = re.compile(
    r"^\s*(?:$"
    r"|[-=_*#>|`~]{3,}\s*$"
    r"|Read these,? in this order"
    r"|Working directory:"
    r"|The swarm CLI is:"
    r"|Your bus is:"
    # Buzz relay envelope: the human's actual question sits below these headers.
    r"|\[Context\]"
    r"|Scope:"
    r"|Channel:"
    r"|Event ID:"
    r"|\[Conversation Context"
    r"|\[Buzz event:"
    # a bare path on its own line
    r"|(?:\d+\.\s*)?[/~]?[A-Za-z]:?[\\/][^\s]*$"
    r")", re.I)

# Shell/TUI prompt glyphs pasted in with the text. NBSP included: the operator's terminal
# pastes "❯ " as one unit.
_PROMPT_GLYPHS = re.compile(r"^[\s ]*[❯>❯▎▸›»$#]+[\s ]*")

# A line ending in a colon introduces the real content rather than being it.
_LEAD_IN = re.compile(r":\s*$")

# Greetings and content-free openers: a real tier-D negative.
_CONTENTLESS = re.compile(
    r"^\s*(?:hi|hey|hello|yo|ok|okay|thanks|ty|continue|go|go ahead|proceed|resume|"
    r"test|ping|\?+|/\w[\w:-]*)\s*[.!?]?\s*$", re.I)

_MIN_GOAL_CHARS = 12


def _clean(text: str) -> str:
    for pat in _STRIP_BLOCKS:
        text = pat.sub(" ", text)
    return text.strip()


def _first_meaningful_line(text: str, max_chars: int = 300) -> str | None:
    """The first line that is neither wrapper nor scaffolding. A quote, never a summary.

    Joining is the one liberty taken: a line ending in ':' introduces the next line rather
    than standing alone ("Can you review my qc system as dicussed here:"), so the two are
    concatenated. Both halves are still verbatim; nothing is reworded.
    """
    kept: list[str] = []
    for raw in text.splitlines():
        line = _PROMPT_GLYPHS.sub("", raw.strip()).lstrip("#*>-• ").strip()
        if not line or _SKIP_LINE.match(line):
            continue
        if len(line) < _MIN_GOAL_CHARS and not kept:
            continue
        kept.append(line)
        if not _LEAD_IN.search(line):
            break
        if len(kept) >= 3:      # a lead-in chain this long is a list, not a sentence
            break
    if not kept:
        return None
    joined = " ".join(kept).strip()
    return joined[:max_chars] if len(joined) >= _MIN_GOAL_CHARS else None


def adjudicate(facts) -> tuple[str | None, str, str]:
    """Return (stated_goal, tier, source).

    `facts` is a TranscriptFacts from transcript.scan.
    """
    text = _clean(facts.first_user_text or "")

    if text and not _CONTENTLESS.match(text):
        # A swarm brief states its task in the title line of the task block; quote that in
        # preference to the "You are agent T5" boilerplate that precedes it.
        m = re.search(r"^title:\s*(.+)$", text, re.M)
        if m and len(m.group(1).strip()) >= _MIN_GOAL_CHARS:
            return m.group(1).strip()[:300], TIER_BRIEF, "first-user-prompt:task-title"
        line = _first_meaningful_line(text)
        if line:
            return line, TIER_BRIEF, "first-user-prompt:first-line"

    if facts.ai_title:
        return facts.ai_title.strip()[:300], TIER_TITLE, "ai-title-record"

    if facts.away_summary:
        return facts.away_summary.strip()[:300], TIER_AWAY, "away-summary-record"

    return None, TIER_NONE, "none"
