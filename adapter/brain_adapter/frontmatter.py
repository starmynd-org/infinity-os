"""Frontmatter reading for brain nodes.

The brain writes YAML frontmatter between two `---` fences at the top of a markdown
file. Only a handful of keys matter to the adapter, and the corpus is ~11,500 files, so
this parses the small set directly instead of handing every file to PyYAML. Values that
do not fit the simple forms fall back to PyYAML for that one file.

Nothing here writes. Reading canon is free; writing goes through `promotion.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

FENCE = "---"

# Keys the adapter reads. Everything else in the frontmatter is left alone.
WANTED = (
    "id",
    "aliases",
    "type",
    "namespace",
    "title",
    "summary",
    "lifecycle_state",
    "retrieval_class",
    "export_class",
    "confidence",
    "verified_at",
    "updated",
)

_SCALAR = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):[ \t]*(.*)$")
_LIST_ITEM = re.compile(r"^[ \t]*-[ \t]+(.*)$")


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    return raw


def _inline_list(raw: str) -> list[str]:
    """Parse `["a", "b"]` without invoking a YAML parser."""
    inner = raw.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    out = []
    for part in inner.split(","):
        part = _unquote(part)
        if part:
            out.append(part)
    return out


@dataclass
class Frontmatter:
    path: Path
    present: bool = False
    fields: dict = field(default_factory=dict)

    @property
    def id(self) -> str | None:
        v = self.fields.get("id")
        return v if isinstance(v, str) and v else None

    @property
    def aliases(self) -> list[str]:
        v = self.fields.get("aliases")
        if isinstance(v, list):
            return [a for a in v if isinstance(a, str) and a]
        if isinstance(v, str) and v:
            return [v]
        return []

    def get(self, key: str, default=None):
        return self.fields.get(key, default)


def read(path: Path, text: str | None = None) -> Frontmatter:
    """Read the frontmatter block of one file. Never raises on a malformed file."""
    fm = Frontmatter(path=path)
    if text is None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return fm
    if not text.startswith(FENCE):
        return fm

    lines = text.split("\n")
    end = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == FENCE:
            end = i
            break
    if end is None:
        return fm
    fm.present = True

    block = lines[1:end]
    i = 0
    while i < len(block):
        line = block[i]
        m = _SCALAR.match(line)
        if not m:
            i += 1
            continue
        key, rest = m.group(1), m.group(2).strip()
        if key not in WANTED:
            i += 1
            continue
        if rest.startswith("["):
            # Inline list, possibly wrapped over several lines.
            buf = rest
            while buf.count("[") > buf.count("]") and i + 1 < len(block):
                i += 1
                buf += " " + block[i].strip()
            fm.fields[key] = _inline_list(buf)
        elif rest == "":
            # Block list, or a nested mapping we do not care about.
            items = []
            j = i + 1
            while j < len(block):
                lm = _LIST_ITEM.match(block[j])
                if not lm:
                    break
                items.append(_unquote(lm.group(1)))
                j += 1
            if items:
                fm.fields[key] = items
                i = j - 1
            else:
                fm.fields[key] = ""
        else:
            fm.fields[key] = _unquote(rest)
        i += 1
    return fm
