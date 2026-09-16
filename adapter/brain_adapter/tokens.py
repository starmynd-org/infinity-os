"""Token accounting for a rendered brief.

There is no local Claude tokenizer on this host and no API key to call
`messages.count_tokens` with, so the count here is an estimate and is labelled as one
everywhere it is reported. The method is stated rather than hidden behind a single
number, because a brief budget that silently under-counts is how a brief blows a context
window.

Calibration: markdown prose in this corpus runs about 3.6 characters per Claude token.
`estimate()` returns that, plus a conservative upper bound at 3.2, so a budget decision
can be made against the pessimistic figure.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

CHARS_PER_TOKEN = 3.6
CHARS_PER_TOKEN_CONSERVATIVE = 3.2

_WORD = re.compile(r"\w+|[^\w\s]")


@dataclass
class TokenCount:
    chars: int
    words: int
    tokens_estimate: int
    tokens_upper_bound: int
    method: str
    exact: bool

    def to_dict(self) -> dict:
        return {
            "chars": self.chars,
            "words": self.words,
            "tokens_estimate": self.tokens_estimate,
            "tokens_upper_bound": self.tokens_upper_bound,
            "method": self.method,
            "exact": self.exact,
        }


def count(text: str) -> TokenCount:
    """Exact when an API key is available, estimated otherwise. Says which."""
    chars = len(text)
    words = len(_WORD.findall(text))

    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic

            client = anthropic.Anthropic()
            res = client.messages.count_tokens(
                model=os.environ.get("BRIEF_TOKEN_MODEL", "claude-sonnet-5"),
                messages=[{"role": "user", "content": text}],
            )
            n = int(res.input_tokens)
            return TokenCount(chars, words, n, n, "anthropic.messages.count_tokens", True)
        except Exception:  # noqa: BLE001 - an estimate beats a crash in a rendering path
            pass

    return TokenCount(
        chars=chars,
        words=words,
        tokens_estimate=int(chars / CHARS_PER_TOKEN),
        tokens_upper_bound=int(chars / CHARS_PER_TOKEN_CONSERVATIVE),
        method=f"chars/{CHARS_PER_TOKEN} (upper bound chars/{CHARS_PER_TOKEN_CONSERVATIVE})",
        exact=False,
    )


def estimate(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)
