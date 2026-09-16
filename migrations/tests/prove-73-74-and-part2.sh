#!/usr/bin/env bash
# 2026-09-09-IOS-term-5: WSL convenience entrypoint. The Python proof accepts
# --receipt /absolute/log.txt and requires ENGINE_SCRATCH_DB and T5_SOURCE_SHA.
# For an archive export run the exported Python file directly at its absolute path.
exec /usr/bin/python3 /mnt/c/Users/you/repos/_scratch-infinity/worktrees/R-ios5/migrations/tests/prove_73_74_and_part2.py "$@"
