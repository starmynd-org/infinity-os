"""Deterministic agent glyphs: a 5-to-7 node constellation hashed from the agent name.

No avatars, no photos, no emoji. The same name always produces the same constellation on every
screen and in every session, because an identity that shuffles between page loads is not an
identity. FNV-1a plus a small PRNG, matching the mock's function exactly so the console and the
mock draw the same shape for the same name.

Rendered server side rather than in JavaScript so a glyph is present in the HTML the poll
returns, which keeps it stable across a repaint instead of re-randomising on every patch.
"""

from __future__ import annotations

_MASK = 0xFFFFFFFF


def _seed(name: str):
    h = 2166136261
    for ch in name:
        h = ((h ^ ord(ch)) * 16777619) & _MASK

    state = {"h": h}

    def rnd() -> float:
        state["h"] = (state["h"] + 0x6D2B79F5) & _MASK
        t = state["h"]
        t = ((t ^ (t >> 15)) * (t | 1)) & _MASK
        t = (t ^ (t + ((t ^ (t >> 7)) * (t | 61) & _MASK))) & _MASK
        return ((t ^ (t >> 14)) & _MASK) / 4294967296.0

    return rnd


# The five semantic states, and brand orange is not among them: orange means operator causality
# and never rides on agent-originated content.
AGENT_COLOURS = ("var(--run)", "var(--fin)", "var(--wait)", "var(--rest)")


def colour_for(name: str) -> str:
    return AGENT_COLOURS[sum(ord(c) for c in name) % len(AGENT_COLOURS)]


def glyph(name: str, colour: str | None = None, size: int = 26) -> str:
    rnd = _seed(name)
    col = colour or colour_for(name)
    n = 5 + int(rnd() * 3)
    pts = [(4 + rnd() * (size - 8), 4 + rnd() * (size - 8)) for _ in range(n)]
    lines = []
    for i in range(1, n):
        j = int(rnd() * i)
        lines.append(
            f'<line x1="{pts[i][0]:.1f}" y1="{pts[i][1]:.1f}" x2="{pts[j][0]:.1f}" '
            f'y2="{pts[j][1]:.1f}" stroke="{col}" stroke-width="0.7" opacity="0.45"/>')
    dots = "".join(
        f'<circle cx="{p[0]:.1f}" cy="{p[1]:.1f}" r="{2.1 if i == 0 else 1.3}" fill="{col}"/>'
        for i, p in enumerate(pts))
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
            f'role="img" aria-label="{name}">{"".join(lines)}{dots}</svg>')
