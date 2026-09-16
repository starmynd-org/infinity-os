"""Connectors: the things that push into the intake door, and nothing else.

Each module here reads one source and calls `door.push`. None of them imports `store`, `engine`
or any transition: see `door.py`'s opening comment for why that is a rule rather than a style.
A connector in this package is deletable without touching the runtime, which is the property
that lets a user write their own.
"""
