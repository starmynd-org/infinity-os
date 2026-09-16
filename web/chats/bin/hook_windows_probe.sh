#!/usr/bin/env bash
# WINDOWS HOOK CANDIDATE PROBE. Run from Git Bash on Windows. The measurement is this file.
#
#   bash web/chats/bin/hook_windows_probe.sh
#
# WHAT IT TESTS, and both branches of every guard are watched because the thing under test
# EXITS 0 WHETHER IT WORKS OR NOT. `ingest/bin/claude-session-hook`'s own header: "It exits 0 no
# matter what." A hook that exits 0 on success and 0 on failure cannot be tested by its exit code,
# so every verdict below comes from a LOG LINE the hook wrote, never from `$?`.
#
#   RED-1  invoked with NO stdin            -> must log "no session_id in payload"
#   GREEN  invoked with a payload on stdin  -> must log "SessionStart <id> created=..."
#   RED-2  SessionEnd with a WINDOWS transcript path -> what happens to the pointer
#   GREEN-2 SessionEnd with the /mnt/c translation of the same file
#
# IT NEVER TOUCHES `brain`. `BRAIN_PROFILE=scratch` points the verbs at `d3_scratch.ingest`,
# which is what SEAT-COMMON section 1 permits: the forbidden names are `brain` and `brain_scratch`.
# It writes its log to this lane's own evidence directory, not to the operator's
# `~/.claude/brain-session-hook.log`, so the real log is neither polluted nor confused with this.
set -u

HOOKLOG=/mnt/c/Users/you/repos/_scratch-infinity/worktrees/R-ios6/outputs/2026-09-09-IOS-term-6/evidence/hook-probe.log
WINLOG="C:/Users/you/repos/_scratch-infinity/worktrees/R-ios6/outputs/2026-09-09-IOS-term-6/evidence/hook-probe.log"
PAYLOADS="C:/Users/you/repos/_scratch-infinity/worktrees/R-ios6/outputs/2026-09-09-IOS-term-6/evidence"

echo "=============================================================================="
echo "WINDOWS HOOK CANDIDATE PROBE"
echo "  as-of        $(date -u +%Y%m%dT%H%M%SZ)"
echo "  store        d3_scratch.ingest via BRAIN_PROFILE=scratch. NEVER brain."
echo "  hook log     ${WINLOG}"
echo "  verdicts     come from LOG LINES. The hook exits 0 either way and \$? proves nothing."
echo "=============================================================================="

marker() { echo "===PROBE-$1-$(date -u +%H%M%S)===" >> "${WINLOG}"; }

echo
echo "RED-1 -- invoked through wsl.exe with NO stdin at all"
marker RED1
wsl.exe bash -lc "BRAIN_PROFILE=scratch BRAIN_HOOK_LOG=${HOOKLOG} /mnt/c/Users/you/repos/infinity-os/ingest/bin/claude-session-hook < /dev/null"
echo "  exit=$?  (meaningless by design; read the log)"

echo
echo "GREEN-1 -- SessionStart payload piped from WINDOWS into wsl.exe"
marker GREEN1
wsl.exe bash -lc "BRAIN_PROFILE=scratch BRAIN_HOOK_LOG=${HOOKLOG} /mnt/c/Users/you/repos/infinity-os/ingest/bin/claude-session-hook" < "${PAYLOADS}/probe-sessionstart.json"
echo "  exit=$?  (meaningless by design; read the log)"

echo
echo "RED-2 -- SessionEnd whose transcript_path is a WINDOWS path (C:/...)"
marker RED2
wsl.exe bash -lc "BRAIN_PROFILE=scratch BRAIN_HOOK_LOG=${HOOKLOG} /mnt/c/Users/you/repos/infinity-os/ingest/bin/claude-session-hook" < "${PAYLOADS}/probe-sessionend-winpath.json"
echo "  exit=$?"

echo
echo "GREEN-2 -- the same SessionEnd with the /mnt/c translation of the same file"
marker GREEN2
wsl.exe bash -lc "BRAIN_PROFILE=scratch BRAIN_HOOK_LOG=${HOOKLOG} /mnt/c/Users/you/repos/infinity-os/ingest/bin/claude-session-hook" < "${PAYLOADS}/probe-sessionend-wslpath.json"
echo "  exit=$?"

echo
echo "=============================================================================="
echo "THE LOG, WHICH IS THE ONLY EVIDENCE HERE"
echo "=============================================================================="
cat "${WINLOG}"
