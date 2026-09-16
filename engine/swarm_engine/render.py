"""Rendering, which is the ONLY place anything is shortened.

D00 rule 11: store full text, truncate only renderings. Two write paths in the file bus
destroyed text before it ever reached disk -- `result` at 2000 chars and a thread line at 3000 --
and neither could be un-cut, because the full string was never anywhere. `docs/0037-result-spill.patch`
replaced destruction with spill-plus-pointer; this port goes further, because a `text` column has
no reason to cut anything at all.

So the rule here is one-directional: a function in this file may shorten what it prints and must
never be called on the way IN. `test_full_text_survives` posts a 4000-character result, reads it
back byte for byte, and asserts the rendering says so rather than pretending it is whole.
"""

from __future__ import annotations

import shutil

# What a terminal shows before it says "there is more". This is a display width, not a limit:
# the row still holds every byte and `--full` prints them.
LINE = 100
BLOCK = 2000
MORE = "  [+{n} more chars: swarm show {id} --full]"


def width(default=100) -> int:
    try:
        return max(60, min(shutil.get_terminal_size((default, 24)).columns, 200))
    except OSError:
        return default


def one_line(text, limit=LINE) -> str:
    """Flatten to a single line for a list view. Marks the cut; never silent."""
    flat = " ".join(str(text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[:limit - 1] + "…"


def block(text, task_id="", limit=BLOCK) -> str:
    """A multi-line body, with a pointer to the whole thing when it is longer than the view.

    The pointer is the difference between this and the defect it replaces. The file bus cut at
    2000 characters with no marker, so the text read as a finished sentence and, as a work order,
    read as a complete one.
    """
    raw = str(text or "")
    if len(raw) <= limit:
        return raw
    return raw[:limit] + MORE.format(n=len(raw) - limit, id=task_id or "<id>")


def cut_banner(task_id: str, n_hidden: int) -> str:
    """Loud on purpose. A marker that reads as prose is how the original defect stayed invisible."""
    bar = "!" * 76
    return "\n".join([
        "", bar,
        "!! This view is SHORTENED. The store holds the whole text; this terminal does not.",
        f"!!   {n_hidden} more characters are in the row and not on your screen.",
        "!! What you read above stops mid-sentence. Read as a work order it is a partial one.",
        f"!! Whole text:   swarm show {task_id} --full",
        bar])


def stamp(ts) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ") if ts is not None else ""


def short(ts) -> str:
    return ts.strftime("%m-%d %H:%M") if ts is not None else "     -    "


def dur(seconds) -> str:
    if seconds is None:
        return "-"
    s = int(seconds)
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60:02d}"
