#!/usr/bin/env bash
# THE LIVE PROBE. One throwaway Windows Claude Code session, run with a candidate hook file.
#
#   bash web/chats/bin/hook_live_probe.sh
#
# This closes the one thing `hook_windows_probe.sh` CANNOT: that probe piped a payload into
# `wsl.exe` from Git Bash, which is a proxy. **This runs the artefact the operator would run.**
# The runtime repo's own CLAUDE.md: *"Measure the artefact he uses, not the cheapest proxy."*
#
# IT INSTALLS NOTHING. `--settings` is per-invocation and is the route `HOOK-INSTALL.md` documents
# under "Try it without installing anything" -- *"This is how the live proof was produced, and it
# changes no config at all."* No `settings.json` is written, read for content, or modified.
#
# THE TRAP THIS FILE EXISTS TO AVOID, AND IT IS DOCUMENTED IN THE HOOK'S OWN SOURCE.
# `claude-session-hook`, task 0437 comment: *"A `claude` that inherits CLAUDE_CODE_CHILD_SESSION
# writes no transcript file at all, so this branch is reached with a path that never existed."*
# **`CLAUDE_CODE_CHILD_SESSION=1` is set inside an agent session**, measured in this one. Launching
# the probe without unsetting it would produce a transcript-less session and the hook would report
# NOT INDEXED -- **which is exactly the failure signature this probe is trying to detect**, from an
# entirely unrelated cause. That is a false positive that would have looked like a real finding.
set -u

LOGWIN="C:/Users/you/repos/_scratch-infinity/worktrees/R-ios6/outputs/2026-09-09-IOS-term-6/evidence/hook-live.log"
CANDIDATE="C:/Users/you/repos/_scratch-infinity/worktrees/R-ios6/outputs/2026-09-09-IOS-term-6/evidence/hook-candidate-windows-PROBE.json"

echo "=============================================================================="
echo "LIVE WINDOWS HOOK PROBE -- one throwaway session"
echo "  as-of                $(date -u +%Y%m%dT%H%M%SZ)"
echo "  candidate            ${CANDIDATE}"
echo "  hook log             ${LOGWIN}"
echo "  store                d3_scratch.ingest (BRAIN_PROFILE=scratch in the candidate). NEVER brain."
echo "  installs             NOTHING. --settings is per-invocation."
echo "  CLAUDE_CODE_CHILD_SESSION in THIS shell: [${CLAUDE_CODE_CHILD_SESSION:-unset}]"
echo "=============================================================================="

echo
echo "-- launching. Unsetting the child-session marker first; see this file's header --"
env -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_HOST_SESSION_ID \
    -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT \
    claude -p --settings "${CANDIDATE}" \
    "Reply with exactly this and nothing else: ios-term-6-hook-proof"
# CAPTURED ON THE VERY NEXT LINE. My first version put a bare `echo` between the command and this
# read, so `$?` reported the ECHO's status and printed `claude exit=0` over a segmentation fault.
# A status read one line too late is not a weaker measurement, it is a measurement of something
# else, and it read as a clean run.
CLAUDE_STATUS=$?
echo
echo "  claude exit=${CLAUDE_STATUS}"

echo
echo "=============================================================================="
echo "THE HOOK LOG FROM THE LIVE SESSION -- the only evidence here"
echo "=============================================================================="
if [ -f "${LOGWIN}" ]; then
  cat "${LOGWIN}"
else
  echo "NO LOG FILE AT ALL at ${LOGWIN}."
  echo "That is the loudest possible result: the hook did not run once. If Claude Code on Windows"
  echo "could not invoke the command, nothing would have written this file."
fi
