"""Stable refusal shape for estate imports."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImportRefusal(Exception):
    code: str
    member: str
    why: str
    instead: str

    def __str__(self) -> str:
        return f"{self.code}: {self.member}: {self.why} Instead: {self.instead}"

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "member": self.member,
            "why": self.why,
            "instead": self.instead,
        }
